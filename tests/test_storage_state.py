"""Tests for SQLite StateDB, transaction WAL, and ProjectLock."""

from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from gitcsv.core.models import TableSnapshot
from gitcsv.storage.lock import LockAcquisitionError, ProjectLock
from gitcsv.storage.state_db import StateDB


class StorageStateTests(unittest.TestCase):
    def test_state_db_save_and_retrieve_base(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            snapshot = TableSnapshot(
                name="Items",
                headers=("Id", "Cost"),
                types=("int", "int"),
                rows=(("1", "100"), ("2", "200")),
                source="base",
            )

            with StateDB(root) as state_db:
                state_db.save_base("Items", snapshot)
                retrieved = state_db.get_base("Items")

            self.assertIsNotNone(retrieved)
            self.assertEqual(retrieved.name, "Items")
            self.assertEqual(retrieved.headers, ("Id", "Cost"))
            self.assertEqual(retrieved.types, ("int", "int"))
            self.assertEqual(retrieved.rows, (("1", "100"), ("2", "200")))

    def test_state_db_transaction_journal_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            before = TableSnapshot(name="Items", headers=("Id",), types=(), rows=(("1",),))
            after = TableSnapshot(name="Items", headers=("Id",), types=(), rows=(("1",), ("2",),))

            with StateDB(root) as state_db:
                # 1. Reserve pending
                op_id = state_db.reserve_operation("push", "Items", before, before, None, after)
                pending = state_db.get_pending_operations()
                self.assertEqual(len(pending), 1)
                self.assertEqual(pending[0].id, op_id)
                self.assertEqual(pending[0].status, "pending")

                # 2. Commit
                state_db.commit_operation(op_id, after)
                pending_after = state_db.get_pending_operations()
                self.assertEqual(len(pending_after), 0)

                # 3. Check history
                history = state_db.get_history(limit=5)
                self.assertEqual(len(history), 1)
                self.assertEqual(history[0].status, "committed")

    def test_project_lock_prevents_concurrent_acquisition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)

            with ProjectLock(root) as lock1:
                # Trying to acquire a second lock on same root should time out
                lock2 = ProjectLock(root, timeout_seconds=0.2)
                with self.assertRaises(LockAcquisitionError):
                    lock2.acquire()

            # Once lock1 exits, acquiring lock2 should succeed
            with ProjectLock(root, timeout_seconds=0.2) as lock3:
                self.assertTrue(lock3.lock_file.is_file())


if __name__ == "__main__":
    unittest.main()
