"""V3 writes are exercised only against disposable CSVs and fake Sheets."""

from __future__ import annotations

from contextlib import closing
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fccsv3.adapters.csv_file import read_csv_table, write_csv_table  # noqa: E402
from fccsv3.adapters.project_lock import ProjectWriteLock  # noqa: E402
from fccsv3.adapters.state_db import StateConflict, StateDB, read_bases  # noqa: E402
from fccsv3.application.apply import OperationInterrupted, apply_table  # noqa: E402
from fccsv3.application.recover import (  # noqa: E402
    inspect_recovery,
    recover_operation,
)
from fccsv3.application.scan import TableMapping  # noqa: E402
from fccsv3.application.undo import plan_undo, undo_operation  # noqa: E402
from fccsv3.domain.model import TableSnapshot  # noqa: E402


class FakeGateway:
    def __init__(self, values: list[list[str]]) -> None:
        self.values = [row[:] for row in values]
        self.writes = 0
        self.fail_after_write = False
        self.formula_blocker_message: str | None = None

    def formula_blocker(self, tab: str) -> str | None:
        return self.formula_blocker_message

    def read_values(self, tabs: tuple[str, ...]) -> dict[str, list[list[str]]]:
        return {tabs[0]: [row[:] for row in self.values]}

    def write_values(self, tab: str, values: list[list[str]]) -> None:
        self.writes += 1
        self.values = [row[:] for row in values]
        if self.fail_after_write:
            raise RuntimeError("simulated lost response")


class ApplyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.csv_dir = self.root / "CSV"
        self.path = self.csv_dir / "Items.csv"
        self.mapping = TableMapping("Items", "Items.csv", ("Id",))
        self.write_csv((("1", "A"),))

    def write_csv(
        self, rows: tuple[tuple[str, ...], ...],
        headers: tuple[str, ...] = ("Id", "Value"),
        types: tuple[str, ...] = ("number", "text"),
    ) -> None:
        write_csv_table(
            self.path, TableSnapshot("Items", headers, rows, types=types, source="csv")
        )

    def test_dry_run_does_not_create_state_or_write(self) -> None:
        gateway = FakeGateway([["Id", "Value"], ["1", "B"]])
        result = apply_table(
            self.root, self.csv_dir, self.mapping, gateway, "push",
            force=True, dry_run=True,
        )
        self.assertTrue(result.decision.allowed)
        self.assertEqual(result.operation_id, None)
        self.assertEqual(gateway.writes, 0)
        self.assertFalse((self.root / ".fccsv3").exists())

    def test_push_requires_explicit_direction_without_base(self) -> None:
        gateway = FakeGateway([["Id", "Value"], ["1", "B"]])
        blocked = apply_table(self.root, self.csv_dir, self.mapping, gateway, "push")
        self.assertFalse(blocked.decision.allowed)
        self.assertEqual(gateway.writes, 0)
        self.assertFalse((self.root / ".fccsv3").exists())
        result = apply_table(
            self.root, self.csv_dir, self.mapping, gateway, "push", force=True
        )
        self.assertTrue(result.verified)
        self.assertEqual(gateway.values, [["Id", "Value"], ["1", "A"]])
        self.assertEqual(read_bases(self.root)["Items"].rows, (("1", "A"),))
        with StateDB(self.root) as state:
            self.assertEqual(state.get(result.operation_id).status, "committed")

    def test_formula_blocker_prevents_journal_and_write(self) -> None:
        gateway = FakeGateway([["Id", "Value"], ["1", "B"]])
        gateway.formula_blocker_message = "Sheet tab contains formulas: Items"
        result = apply_table(
            self.root, self.csv_dir, self.mapping, gateway, "push", force=True,
        )
        self.assertFalse(result.decision.allowed)
        self.assertIn("contains formulas", result.decision.errors[0])
        self.assertEqual(gateway.writes, 0)
        self.assertFalse((self.root / ".fccsv3").exists())

    def test_pull_keeps_new_sheet_column_and_declared_type(self) -> None:
        gateway = FakeGateway([["Id", "Value", "Extra"], ["1", "B", "new"]])
        result = apply_table(
            self.root, self.csv_dir, self.mapping, gateway, "pull",
            force=True, accept_schema=True, new_types={"Extra": "text"},
        )
        self.assertTrue(result.verified)
        csv = read_csv_table(self.path)
        self.assertEqual(csv.headers, ("Id", "Value", "Extra"))
        self.assertEqual(csv.types, ("number", "text", "text"))
        self.assertEqual(csv.rows, (("1", "B", "new"),))

    def test_sync_merges_independent_cells_after_adoption(self) -> None:
        self.write_csv((("1", "A", "base"),),
                       headers=("Id", "Value", "Other"),
                       types=("number", "text", "text"))
        gateway = FakeGateway([["Id", "Value", "Other"], ["1", "A", "base"]])
        adopted = apply_table(self.root, self.csv_dir, self.mapping, gateway, "sync")
        self.assertTrue(adopted.verified)
        self.assertEqual(gateway.writes, 0)
        self.write_csv((("1", "local", "base"),),
                       headers=("Id", "Value", "Other"),
                       types=("number", "text", "text"))
        gateway.values[1][2] = "remote"
        result = apply_table(self.root, self.csv_dir, self.mapping, gateway, "sync")
        self.assertTrue(result.verified)
        self.assertEqual(
            read_csv_table(self.path).rows, (("1", "local", "remote"),)
        )
        self.assertEqual(gateway.values[1], ["1", "local", "remote"])

    def test_uncertain_remote_write_leaves_recoverable_pending_record(self) -> None:
        gateway = FakeGateway([["Id", "Value"], ["1", "B"]])
        gateway.fail_after_write = True
        with self.assertRaises(OperationInterrupted) as caught:
            apply_table(
                self.root, self.csv_dir, self.mapping, gateway, "push", force=True
            )
        with StateDB(self.root) as state:
            record = state.get(caught.exception.operation_id)
            self.assertEqual(record.status, "pending")
            self.assertEqual(record.before_remote.rows, (("1", "B"),))
            self.assertEqual(record.after_remote.rows, (("1", "A"),))
            self.assertIn("simulated lost response", record.error)
        self.assertEqual(read_bases(self.root), {})
        with self.assertRaisesRegex(Exception, "pending"):
            apply_table(
                self.root, self.csv_dir, self.mapping, gateway, "push", force=True
            )

    def test_recover_continue_finishes_pending_write(self) -> None:
        gateway = FakeGateway([["Id", "Value"], ["1", "B"]])
        gateway.fail_after_write = True
        with self.assertRaises(OperationInterrupted) as caught:
            apply_table(
                self.root, self.csv_dir, self.mapping, gateway, "push", force=True
            )
        gateway.fail_after_write = False
        operation_id = caught.exception.operation_id
        inspection = inspect_recovery(
            self.root, self.csv_dir, self.mapping, gateway, operation_id
        )
        self.assertEqual((inspection.local, inspection.remote), ("after", "after"))
        recover_operation(
            self.root, self.csv_dir, self.mapping, gateway,
            operation_id, "continue",
        )
        self.assertEqual(read_bases(self.root)["Items"].rows, (("1", "A"),))
        with StateDB(self.root) as state:
            self.assertEqual(state.get(operation_id).status, "committed")

    def test_recovery_inspection_does_not_migrate_v1_state(self) -> None:
        gateway = FakeGateway([["Id", "Value"], ["1", "B"]])
        gateway.fail_after_write = True
        with self.assertRaises(OperationInterrupted) as caught:
            apply_table(
                self.root, self.csv_dir, self.mapping, gateway, "push", force=True
            )
        database = self.root / ".fccsv3" / "state.sqlite3"
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("ALTER TABLE operations DROP COLUMN source_operation_id")
            connection.execute("PRAGMA user_version=1")
            connection.commit()
        inspection = inspect_recovery(
            self.root, self.csv_dir, self.mapping, gateway,
            caught.exception.operation_id,
        )
        self.assertEqual((inspection.local, inspection.remote), ("after", "after"))
        with closing(sqlite3.connect(database)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_recover_restore_reverts_sheet_and_keeps_old_base(self) -> None:
        gateway = FakeGateway([["Id", "Value"], ["1", "B"]])
        gateway.fail_after_write = True
        with self.assertRaises(OperationInterrupted) as caught:
            apply_table(
                self.root, self.csv_dir, self.mapping, gateway, "push", force=True
            )
        gateway.fail_after_write = False
        recover_operation(
            self.root, self.csv_dir, self.mapping, gateway,
            caught.exception.operation_id, "restore",
        )
        self.assertEqual(gateway.values[1], ["1", "B"])
        self.assertEqual(read_bases(self.root), {})
        with StateDB(self.root) as state:
            self.assertEqual(state.get(caught.exception.operation_id).status, "restored")

    def test_ambiguous_recovery_requires_force(self) -> None:
        gateway = FakeGateway([["Id", "Value"], ["1", "B"]])
        gateway.fail_after_write = True
        with self.assertRaises(OperationInterrupted) as caught:
            apply_table(
                self.root, self.csv_dir, self.mapping, gateway, "push", force=True
            )
        gateway.fail_after_write = False
        gateway.values[1][1] = "unexpected"
        operation_id = caught.exception.operation_id
        with self.assertRaisesRegex(ValueError, "differs from both"):
            recover_operation(
                self.root, self.csv_dir, self.mapping, gateway,
                operation_id, "restore",
            )
        recover_operation(
            self.root, self.csv_dir, self.mapping, gateway,
            operation_id, "restore", force=True,
        )
        self.assertEqual(gateway.values[1], ["1", "B"])

    def test_verification_rejects_stale_hidden_sheet_column(self) -> None:
        class StaleColumnGateway(FakeGateway):
            def write_values(self, tab: str, values: list[list[str]]) -> None:
                super().write_values(tab, values)
                self.values[1].append("stale")

        gateway = StaleColumnGateway([["Id", "Value"], ["1", "B"]])
        with self.assertRaisesRegex(OperationInterrupted, "invalid data"):
            apply_table(
                self.root, self.csv_dir, self.mapping, gateway, "push", force=True
            )
        with StateDB(self.root) as state:
            self.assertEqual(len(state.pending()), 1)
        self.assertEqual(read_bases(self.root), {})

    def test_changed_sheet_after_plan_cancels_without_a_write(self) -> None:
        class RacingGateway(FakeGateway):
            reads = 0

            def read_values(self, tabs: tuple[str, ...]) -> dict[str, list[list[str]]]:
                self.reads += 1
                if self.reads == 2:
                    self.values[1][1] = "Changed after plan"
                return super().read_values(tabs)

        gateway = RacingGateway([["Id", "Value"], ["1", "B"]])
        with self.assertRaisesRegex(StateConflict, "changed after planning"):
            apply_table(
                self.root, self.csv_dir, self.mapping, gateway, "push", force=True
            )
        self.assertEqual(gateway.writes, 0)
        self.assertEqual(read_csv_table(self.path).rows, (("1", "A"),))
        with StateDB(self.root) as state:
            self.assertEqual(state.pending(), ())
            self.assertEqual(state.history()[0].status, "cancelled")

    def test_active_project_lock_blocks_write(self) -> None:
        gateway = FakeGateway([["Id", "Value"], ["1", "B"]])
        with ProjectWriteLock(self.root):
            with self.assertRaisesRegex(StateConflict, "another fccsv write"):
                apply_table(
                    self.root, self.csv_dir, self.mapping, gateway,
                    "push", force=True,
                )
        self.assertEqual(gateway.writes, 0)
        self.assertFalse((self.root / ".fccsv3" / "state.sqlite3").exists())

    def test_active_project_lock_blocks_recovery(self) -> None:
        gateway = FakeGateway([["Id", "Value"], ["1", "B"]])
        gateway.fail_after_write = True
        with self.assertRaises(OperationInterrupted) as caught:
            apply_table(
                self.root, self.csv_dir, self.mapping, gateway, "push", force=True
            )
        gateway.fail_after_write = False
        with ProjectWriteLock(self.root):
            with self.assertRaisesRegex(StateConflict, "another fccsv write"):
                recover_operation(
                    self.root, self.csv_dir, self.mapping, gateway,
                    caught.exception.operation_id, "restore",
                )
        with StateDB(self.root) as state:
            self.assertEqual(len(state.pending()), 1)

    def test_undo_restores_both_before_images_and_clears_divergent_base(self) -> None:
        gateway = FakeGateway([["Id", "Value"], ["1", "B"]])
        original = apply_table(
            self.root, self.csv_dir, self.mapping, gateway, "push", force=True
        )
        preview = plan_undo(self.root, self.csv_dir, self.mapping, gateway)
        self.assertTrue(preview.allowed)
        self.assertEqual(preview.source.id, original.operation_id)
        undone = undo_operation(self.root, self.csv_dir, self.mapping, gateway)
        self.assertIsNotNone(undone.operation_id)
        self.assertEqual(gateway.values[1], ["1", "B"])
        self.assertEqual(read_csv_table(self.path).rows, (("1", "A"),))
        self.assertEqual(read_bases(self.root), {})
        with StateDB(self.root) as state:
            record = state.get(undone.operation_id)
            self.assertEqual(record.status, "committed")
            self.assertEqual(record.kind, "undo")
            self.assertEqual(record.source_operation_id, original.operation_id)

    def test_undo_preview_is_read_only_and_drift_blocks(self) -> None:
        gateway = FakeGateway([["Id", "Value"], ["1", "B"]])
        apply_table(self.root, self.csv_dir, self.mapping, gateway, "push", force=True)
        writes = gateway.writes
        preview = undo_operation(
            self.root, self.csv_dir, self.mapping, gateway, dry_run=True
        )
        self.assertTrue(preview.allowed)
        self.assertEqual(gateway.writes, writes)
        with StateDB(self.root) as state:
            self.assertEqual(len(state.history()), 1)
        gateway.values[1][1] = "later edit"
        blocked = undo_operation(self.root, self.csv_dir, self.mapping, gateway)
        self.assertFalse(blocked.allowed)
        self.assertIn("changed after", blocked.errors[0])
        self.assertEqual(gateway.values[1], ["1", "later edit"])

    def test_undo_requires_latest_committed_operation(self) -> None:
        gateway = FakeGateway([["Id", "Value"], ["1", "B"]])
        first = apply_table(
            self.root, self.csv_dir, self.mapping, gateway, "push", force=True
        )
        self.write_csv((("1", "C"),))
        apply_table(self.root, self.csv_dir, self.mapping, gateway, "push")
        blocked = plan_undo(
            self.root, self.csv_dir, self.mapping, gateway,
            source_operation_id=first.operation_id,
        )
        self.assertFalse(blocked.allowed)
        self.assertIn("latest committed", blocked.errors[0])

    def test_recover_interrupted_undo_keeps_divergent_result_unbased(self) -> None:
        gateway = FakeGateway([["Id", "Value"], ["1", "B"]])
        apply_table(self.root, self.csv_dir, self.mapping, gateway, "push", force=True)
        gateway.fail_after_write = True
        with self.assertRaises(OperationInterrupted) as caught:
            undo_operation(self.root, self.csv_dir, self.mapping, gateway)
        gateway.fail_after_write = False
        recover_operation(
            self.root, self.csv_dir, self.mapping, gateway,
            caught.exception.operation_id, "continue",
        )
        self.assertEqual(gateway.values[1], ["1", "B"])
        self.assertEqual(read_csv_table(self.path).rows, (("1", "A"),))
        self.assertEqual(read_bases(self.root), {})


if __name__ == "__main__":
    unittest.main()
