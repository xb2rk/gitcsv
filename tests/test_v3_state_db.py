"""Pending operation durability and state isolation use only temporary files."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fccsv3.adapters.state_db import (  # noqa: E402
    StateConflict,
    StateDB,
    read_bases,
    snapshot_fingerprint,
)
from fccsv3.domain.model import TableSnapshot  # noqa: E402
from fccsv3.domain.operation import decide_operation  # noqa: E402
from fccsv3.domain.plan import plan_table  # noqa: E402


def table(value: str, source: str) -> TableSnapshot:
    return TableSnapshot(
        "Items", ("Id", "Value"), (("1", value),),
        types=() if source == "sheet" else ("number", "text"),
        source=source,  # type: ignore[arg-type]
    )


class StateDBTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.local = table("CSV", "csv")
        self.remote = table("Sheet", "sheet")
        self.decision = decide_operation(
            plan_table(self.local, self.remote), "push", force=True
        )

    def test_read_bases_does_not_create_state(self) -> None:
        self.assertEqual(read_bases(self.root), {})
        self.assertFalse((self.root / ".fccsv3").exists())

    def test_pending_survives_reopen_then_commit_updates_base_atomically(self) -> None:
        with StateDB(self.root) as db:
            operation_id = db.reserve(
                self.decision, self.local, self.remote, expected_base=None
            )
            self.assertEqual(read_bases(self.root), {})
        with StateDB(self.root) as db:
            self.assertEqual(db.pending()[0].id, operation_id)
            self.assertEqual(db.get(operation_id).before_remote.rows, (("1", "Sheet"),))
            db.commit(operation_id, self.local)
            self.assertEqual(db.pending(), ())
            self.assertEqual(db.get(operation_id).status, "committed")
        self.assertEqual(read_bases(self.root)["Items"].rows, (("1", "CSV"),))

    def test_pending_blocks_another_operation(self) -> None:
        with StateDB(self.root) as db:
            operation_id = db.reserve(
                self.decision, self.local, self.remote, expected_base=None
            )
            with self.assertRaisesRegex(StateConflict, operation_id):
                db.reserve(self.decision, self.local, self.remote, expected_base=None)

    def test_changed_base_blocks_stale_plan(self) -> None:
        with StateDB(self.root) as db:
            operation_id = db.reserve(
                self.decision, self.local, self.remote, expected_base=None
            )
            db.commit(operation_id, self.local)
            with self.assertRaisesRegex(StateConflict, "BASE changed"):
                db.reserve(self.decision, self.local, self.remote, expected_base=None)
            next_id = db.reserve(
                self.decision, self.local, self.remote,
                expected_base=snapshot_fingerprint(self.local),
            )
            self.assertNotEqual(next_id, operation_id)

    def test_failure_message_keeps_backup_pending(self) -> None:
        with StateDB(self.root) as db:
            operation_id = db.reserve(
                self.decision, self.local, self.remote, expected_base=None
            )
            db.annotate_failure(operation_id, "verification failed")
            record = db.get(operation_id)
            self.assertEqual(record.status, "pending")
            self.assertEqual(record.error, "verification failed")

    def test_unknown_schema_version_is_rejected(self) -> None:
        with StateDB(self.root):
            pass
        path = self.root / ".fccsv3" / "state.sqlite3"
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("PRAGMA user_version=99")
        with self.assertRaisesRegex(ValueError, "version 99"):
            read_bases(self.root)
        with self.assertRaisesRegex(ValueError, "version 99"):
            StateDB(self.root)

    def test_version_one_state_migrates_without_losing_bases(self) -> None:
        with StateDB(self.root) as db:
            operation_id = db.reserve(
                self.decision, self.local, self.remote, expected_base=None
            )
            db.commit(operation_id, self.local)
        path = self.root / ".fccsv3" / "state.sqlite3"
        with closing(sqlite3.connect(path)) as connection:
            connection.execute(
                "ALTER TABLE operations DROP COLUMN source_operation_id"
            )
            connection.execute("PRAGMA user_version=1")
            connection.commit()
        self.assertEqual(read_bases(self.root)["Items"].rows, (("1", "CSV"),))
        with StateDB(self.root, read_only=True) as db:
            self.assertEqual(db.get(operation_id).status, "committed")
            self.assertIsNone(db.get(operation_id).source_operation_id)
            self.assertEqual(db.history("Items")[0].id, operation_id)
        with closing(sqlite3.connect(path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
        with StateDB(self.root) as db:
            self.assertEqual(db.get(operation_id).status, "committed")
            self.assertIsNone(db.get(operation_id).source_operation_id)
        with closing(sqlite3.connect(path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
