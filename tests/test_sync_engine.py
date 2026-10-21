"""Tests for synchronization engine and undo recovery with FakeSheetsGateway."""

from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

from gitcsv.config.schema import GitCsvConfig, TableMapping
from gitcsv.core.models import TableSnapshot
from gitcsv.core.planner import decide_operation
from gitcsv.services.recovery import undo_table
from gitcsv.services.sync_engine import apply_table, scan_tables
from gitcsv.storage.csv_store import read_csv_table, write_csv_table
from gitcsv.storage.sheets_api import FakeSheetsGateway


class SyncEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.csv_dir = self.root / "Assets" / "CSV"
        self.csv_dir.mkdir(parents=True)

        self.mapping = TableMapping(tab="Items", csv_file="Items.csv", preferred_key=("Id",))
        self.config = GitCsvConfig(
            spreadsheet_id="test_sheet",
            csv_dir=self.csv_dir,
            tables=(self.mapping,),
            project_root=self.root,
        )

        # Write initial local CSV
        self.csv_path = self.csv_dir / "Items.csv"
        initial_csv = TableSnapshot(
            name="Items",
            headers=("Id", "Cost"),
            types=("int", "int"),
            rows=(("1", "100"),),
        )
        write_csv_table(self.csv_path, initial_csv)

        # Initialize FakeSheetsGateway
        self.gateway = FakeSheetsGateway({
            "Items": [["Id", "Cost"], ["1", "150"]],
        })

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_scan_and_force_push_updates_sheet_and_sets_base(self) -> None:
        # 1. Scan tables -> should detect divergence
        plans = scan_tables(self.config, self.gateway, [self.mapping])
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertEqual(plan.status, "unbased_divergence")

        # 2. Decide push with force
        decision = decide_operation(plan, "push", force=True)
        self.assertTrue(decision.allowed)

        # 3. Apply push
        result = apply_table(self.config, self.gateway, self.mapping, decision, plan)
        self.assertTrue(result.verified)

        # 4. Verify fake gateway was updated with local values (Cost="100")
        sheet_values = self.gateway.read_values(("Items",))["Items"]
        self.assertEqual(sheet_values, [["Id", "Cost"], ["1", "100"]])

        # 5. Scan again -> should now be in_sync because BASE was committed!
        plans_after = scan_tables(self.config, self.gateway, [self.mapping])
        self.assertEqual(plans_after[0].status, "in_sync")

    def test_undo_reverts_latest_committed_operation(self) -> None:
        # First execute a push to have an operation in the journal
        plans = scan_tables(self.config, self.gateway, [self.mapping])
        decision = decide_operation(plans[0], "push", force=True)
        apply_table(self.config, self.gateway, self.mapping, decision, plans[0])

        # Verify sheet has "100"
        self.assertEqual(self.gateway.tables["Items"][1][1], "100")

        # Now undo
        undo_table(self.config, "Items", self.gateway)

        # Sheet should be reverted back to "150"
        self.assertEqual(self.gateway.tables["Items"][1][1], "150")

    def test_multiline_csv_cell_reading(self) -> None:
        multiline_file = self.csv_dir / "Multiline.csv"
        multiline_file.write_text('Id,Lore\nint,string\n1,"Line 1\nLine 2"\n', encoding="utf-8-sig")

        snap = read_csv_table(multiline_file)
        self.assertEqual(len(snap.rows), 1)
        self.assertIn("Line 1\nLine 2", snap.rows[0][1])

    def test_sparse_sheet_rows_padded_with_empty_strings(self) -> None:
        # Sheet returns a row with only 1 column, even though header has 3
        sparse_gateway = FakeSheetsGateway({
            "Sparse": [
                ["Id", "Name", "Desc"],
                ["1"],  # Missing Name and Desc
            ]
        })
        sparse_mapping = TableMapping(tab="Sparse", csv_file="Sparse.csv")
        sparse_file = self.csv_dir / "Sparse.csv"
        sparse_file.write_text("Id,Name,Desc\nint,string,string\n1,,\n", encoding="utf-8-sig")

        plans = scan_tables(self.config, sparse_gateway, [sparse_mapping])
        self.assertEqual(len(plans), 1)
        self.assertIsNotNone(plans[0].remote)
        # Should be padded to 3 elements: ('1', '', '')
        self.assertEqual(plans[0].remote.rows[0], ("1", "", ""))

    def test_push_new_local_table_creates_remote(self) -> None:
        # Table exists locally as CSV, but not on fake sheets
        new_file = self.csv_dir / "NewTable.csv"
        new_file.write_text("Id,Val\nint,string\n10,NewVal\n", encoding="utf-8-sig")

        new_mapping = TableMapping(tab="NewTable", csv_file="NewTable.csv")
        plans = scan_tables(self.config, self.gateway, [new_mapping])
        self.assertEqual(plans[0].status, "missing_remote")

        # Pushing should be allowed
        decision = decide_operation(plans[0], "push", force=False)
        self.assertTrue(decision.allowed)

        # Apply push
        apply_table(self.config, self.gateway, new_mapping, decision, plans[0])
        # Verify fake sheets gateway received the new table
        self.assertIn("NewTable", self.gateway.tables)
        self.assertEqual(self.gateway.tables["NewTable"][1][1], "NewVal")

    def test_bulk_push_creates_missing_remote_tables(self) -> None:
        from gitcsv.services.sync_engine import apply_many_tables

        new_file = self.csv_dir / "BulkNew.csv"
        new_file.write_text("Id,Val\nint,string\n20,BulkVal\n", encoding="utf-8-sig")

        new_mapping = TableMapping(tab="BulkNew", csv_file="BulkNew.csv")
        plans = scan_tables(self.config, self.gateway, [new_mapping])
        self.assertEqual(plans[0].status, "missing_remote")

        decision = decide_operation(plans[0], "push", force=False)
        self.assertTrue(decision.allowed)

        results = apply_many_tables(self.config, self.gateway, [(new_mapping, decision, plans[0])])
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].verified)
        self.assertIn("BulkNew", self.gateway.tables)
        self.assertEqual(self.gateway.tables["BulkNew"][1][1], "BulkVal")


if __name__ == "__main__":
    unittest.main()
