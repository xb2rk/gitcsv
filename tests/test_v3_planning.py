"""V3 acceptance tests for read-only scanning and lossless planning."""

from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

V3_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(V3_SRC))

from fccsv3.adapters.csv_file import read_csv_table  # noqa: E402
from fccsv3.adapters.sheet_values import snapshot_from_sheet_values  # noqa: E402
from fccsv3.application.legacy_config import load_legacy_project  # noqa: E402
from fccsv3.application.scan import TableMapping, scan_tables  # noqa: E402
from fccsv3.domain.model import TableSnapshot  # noqa: E402
from fccsv3.domain.plan import plan_table  # noqa: E402
from fccsv3.domain.validate import validate_snapshot  # noqa: E402


def table(
    name: str,
    headers: tuple[str, ...],
    rows: tuple[tuple[str, ...], ...],
    *,
    source: str = "csv",
    types: tuple[str, ...] | None = None,
) -> TableSnapshot:
    return TableSnapshot(
        name,
        headers,
        rows,
        types=(
            types if types is not None else (() if source == "sheet" else ("text",) * len(headers))
        ),
        source=source,  # type: ignore[arg-type]
    )


class PlanningTests(unittest.TestCase):
    def test_boss_loot_uses_composite_key_after_stale_item_id_hint(self) -> None:
        headers = ("ItemId", "AccessKey", "Pool", "Weight")
        rows = (("00001", "", "Common", "10"), ("00001", "Hard", "Common", "20"))
        plan = plan_table(
            table("BossLootData", headers, rows),
            table("BossLootData", headers, rows, source="sheet"),
            preferred_key=("ItemId",),
        )
        self.assertEqual(plan.status, "in_sync")
        self.assertEqual(plan.identity.columns, ("ItemId", "AccessKey"))
        self.assertIn("STALE_KEY_HINT", [item.code for item in plan.diagnostics])

    def test_shop_offer_identity_uses_shop_and_item(self) -> None:
        headers = ("ShopId", "ItemId", "Weight")
        rows = (
            ("00001", "41005", "10"),
            ("00001", "41006", "10"),
            ("00002", "41005", "20"),
        )
        plan = plan_table(
            table("ShopSlotData", headers, rows),
            table("ShopSlotData", headers, rows, source="sheet"),
            preferred_key=("ItemId",),
        )
        self.assertEqual(plan.identity.columns, ("ShopId", "ItemId"))
        self.assertFalse(plan.has_diff)

    def test_tooltip_reorder_is_visible_even_with_a_valid_key(self) -> None:
        headers = ("ItemId", "SortOrder", "TitleKey")
        rows = (("42001", "20", "Reward.A"), ("42001", "20", "Reward.B"))
        plan = plan_table(
            table("ItemTooltipCatalog", headers, rows),
            table("ItemTooltipCatalog", headers, tuple(reversed(rows)), source="sheet"),
            preferred_key=("ItemId", "SortOrder"),
        )
        self.assertEqual(plan.identity.mode, "columns")
        self.assertTrue(plan.rows.order_changed)
        self.assertTrue(plan.has_diff)
        self.assertEqual(plan.status, "unbased_divergence")

    def test_keyless_table_uses_position_and_reports_changes(self) -> None:
        headers = ("Label", "Value")
        plan = plan_table(
            table("Labels", headers, (("A", "1"), ("B", "2"))),
            table("Labels", headers, (("B", "2"), ("A", "1")), source="sheet"),
        )
        self.assertEqual(plan.identity.mode, "position")
        self.assertEqual(len(plan.rows.cells), 4)
        self.assertTrue(plan.has_diff)

    def test_duplicate_headers_are_rejected_before_any_row_diff(self) -> None:
        local = table("Broken", ("Id", "Id"), (("1", "2"),))
        remote = table("Broken", ("Id", "Id"), (("1", "2"),), source="sheet")
        plan = plan_table(local, remote)
        self.assertEqual(plan.status, "invalid")
        self.assertIsNone(plan.identity)
        self.assertEqual(plan.rows.cells, ())
        self.assertIn("DUPLICATE_HEADER", [item.code for item in plan.diagnostics])

    def test_remote_duplicate_key_falls_back_without_losing_a_row(self) -> None:
        headers = ("Id", "Value")
        local = table("Items", headers, (("1", "A"), ("2", "B")))
        remote = table("Items", headers, (("1", "A"), ("1", "C")), source="sheet")
        plan = plan_table(local, remote, preferred_key=("Id",))
        self.assertEqual(plan.identity.mode, "position")
        self.assertEqual({cell.column for cell in plan.rows.cells}, {"Id", "Value"})
        self.assertIn("STALE_KEY_HINT", [item.code for item in plan.diagnostics])

    def test_header_reorder_is_visible_without_false_cell_changes(self) -> None:
        local = table("Items", ("Id", "Name"), (("1", "Apple"),))
        remote = table("Items", ("Name", "Id"), (("Apple", "1"),), source="sheet")
        plan = plan_table(local, remote)
        self.assertTrue(plan.schema.reordered)
        self.assertEqual(plan.rows.cells, ())
        self.assertTrue(plan.has_diff)

    def test_missing_side_is_explicit_and_not_a_validation_failure(self) -> None:
        local = table("Items", ("Id",), (("1",),))
        plan = plan_table(local, None)
        self.assertEqual(plan.status, "missing_remote")
        self.assertTrue(plan.has_diff)
        self.assertFalse(plan.has_errors)

    def test_snapshot_is_detached_from_mutable_input(self) -> None:
        headers = ["Id", "Name"]
        rows = [["1", "Apple"]]
        snapshot = TableSnapshot("Items", headers, rows, types=["number", "text"])
        headers[0] = "Changed"
        rows[0][1] = "Pear"
        self.assertEqual(snapshot.headers, ("Id", "Name"))
        self.assertEqual(snapshot.rows, (("1", "Apple"),))

    def test_remote_new_column_is_kept_in_plan(self) -> None:
        local = table("Items", ("Id", "Name"), (("1", "Apple"),))
        remote = table(
            "Items", ("Id", "Name", "NewColumn"), (("1", "Apple", "fresh"),), source="sheet"
        )
        plan = plan_table(local, remote)
        self.assertEqual(plan.schema.only_remote, ("NewColumn",))
        self.assertEqual(plan.remote.rows[0][2], "fresh")
        self.assertTrue(plan.has_diff)

    def test_type_only_change_is_local_change_since_base(self) -> None:
        headers = ("Id", "Name")
        rows = (("1", "Apple"),)
        base = table("Items", headers, rows, source="base", types=("number", "text"))
        local = table("Items", headers, rows, types=("text", "text"))
        remote = table("Items", headers, rows, source="sheet")
        plan = plan_table(local, remote, base=base)
        self.assertEqual(plan.status, "local_changed")
        self.assertTrue(plan.schema.local_types_changed_since_base)
        self.assertTrue(plan.has_diff)

    def test_independent_edits_are_classified_on_both_sides(self) -> None:
        headers = ("Id", "Name", "Price")
        base = table("Items", headers, (("1", "Apple", "100"),), source="base")
        local = table("Items", headers, (("1", "Apricot", "100"),))
        remote = table("Items", headers, (("1", "Apple", "120"),), source="sheet")
        plan = plan_table(local, remote, base=base)
        self.assertEqual(plan.status, "both_changed")
        self.assertEqual({cell.column for cell in plan.rows.cells}, {"Name", "Price"})

    def test_csv_reader_retains_ragged_line_number_for_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Items.csv"
            path.write_text("Id,Name\nnumber,text\n1\n", encoding="utf-8")
            snapshot = read_csv_table(path)
        issues = validate_snapshot(snapshot)
        self.assertEqual(snapshot.rows, (("1", ""),))
        self.assertEqual([(item.code, item.line) for item in issues], [("ROW_WIDTH", 3)])

    def test_internal_blank_csv_row_is_reported_instead_of_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Items.csv"
            path.write_text(
                "Id,Name\nnumber,text\n1,Apple\n\n2,Pear\n", encoding="utf-8"
            )
            snapshot = read_csv_table(path)
        self.assertEqual(snapshot.rows, (("1", "Apple"), ("", ""), ("2", "Pear")))
        self.assertEqual(
            [(item.code, item.line) for item in validate_snapshot(snapshot)],
            [("ROW_WIDTH", 4)],
        )

    def test_sheet_reader_maps_headers_without_dropping_values(self) -> None:
        snapshot = snapshot_from_sheet_values(
            "Items", [["Id", "DisplayName"], ["1", "Apple"]],
            column_map={"DisplayName": "Name"},
        )
        self.assertEqual(snapshot.headers, ("Id", "Name"))
        self.assertEqual(snapshot.rows, (("1", "Apple"),))

    def test_scan_reads_remote_tabs_once_and_never_writes_csv(self) -> None:
        class Reader:
            calls: list[tuple[str, ...]] = []

            def read_values(self, tabs: tuple[str, ...]) -> dict[str, list[list[str]]]:
                self.calls.append(tabs)
                return {"Items": [["Id", "Name"], ["1", "Apple"]]}

        with tempfile.TemporaryDirectory() as directory:
            csv_dir = Path(directory)
            csv_path = csv_dir / "Items.csv"
            with csv_path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerows([["Id", "Name"], ["number", "text"], ["1", "Apple"]])
            before = csv_path.read_bytes()
            reader = Reader()
            plans = scan_tables(csv_dir, [TableMapping("Items", "Items.csv")], reader)
            self.assertEqual(csv_path.read_bytes(), before)
        self.assertEqual(reader.calls, [("Items",)])
        self.assertEqual(plans[0].status, "in_sync")

    def test_legacy_config_import_preserves_mapping_as_hint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".fccsv.json").write_text(
                json.dumps(
                    {
                        "sources": [{"spreadsheet_id": "sheet-id", "tables": [
                            {"tab": "BossLootData", "csv": "BossLootData.csv",
                             "key_column": "ItemId, AccessKey", "column_map": {"SheetPool": "Pool"}}
                        ]}],
                        "csv_dir": "Assets/CSV",
                    }
                ),
                encoding="utf-8",
            )
            config = load_legacy_project(root)
        self.assertEqual(config.spreadsheet_id, "sheet-id")
        self.assertEqual(config.mappings[0].preferred_key, ("ItemId", "AccessKey"))
        self.assertEqual(config.mappings[0].column_map, (("SheetPool", "Pool"),))


if __name__ == "__main__":
    unittest.main()
