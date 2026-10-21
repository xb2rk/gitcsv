"""All-table preflight must avoid partial writes caused by known blockers."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fccsv3.adapters.state_db import read_bases, StateDB  # noqa: E402
from fccsv3.interfaces.cli import main  # noqa: E402


class Gateway:
    def __init__(self) -> None:
        self.tables = {
            "Items": [["Id", "Value"], ["1", "old"]],
            "Offers": [["Id", "Value"], ["2", "old"]],
        }
        self.read_calls: list[tuple[str, ...]] = []
        self.writes: list[str] = []
        self.fail_on: str | None = None
        self.formula_tabs: set[str] = set()
        self.formula_scans: list[tuple[str, ...]] = []

    def formula_blockers(self, tabs: tuple[str, ...]) -> dict[str, str]:
        self.formula_scans.append(tabs)
        return {
            tab: f"Sheet tab contains formulas: {tab}"
            for tab in tabs if tab in self.formula_tabs
        }

    def formula_blocker(self, tab: str) -> str | None:
        return f"Sheet tab contains formulas: {tab}" if tab in self.formula_tabs else None

    def read_values(self, tabs: tuple[str, ...]) -> dict[str, list[list[str]]]:
        self.read_calls.append(tabs)
        return {tab: [row[:] for row in self.tables[tab]] for tab in tabs}

    def write_values(self, tab: str, values: list[list[str]]) -> None:
        self.writes.append(tab)
        self.tables[tab] = [row[:] for row in values]
        if tab == self.fail_on:
            raise RuntimeError("simulated lost response")


class BatchCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.csv_dir = self.root / "Assets" / "CSV"
        self.csv_dir.mkdir(parents=True)
        for name, identifier in (("Items", "1"), ("Offers", "2")):
            (self.csv_dir / f"{name}.csv").write_text(
                f"Id,Value\nnumber,text\n{identifier},new\n", encoding="utf-8"
            )
        (self.root / ".fccsv.json").write_text(
            json.dumps({
                "csv_dir": "Assets/CSV",
                "sources": [{
                    "spreadsheet_id": "test",
                    "tables": [
                        {"tab": name, "csv": f"{name}.csv", "key_column": "Id"}
                        for name in ("Items", "Offers")
                    ],
                }],
            }), encoding="utf-8",
        )
        self.gateway = Gateway()

    def invoke(
        self, *args: str, cwd: Path | None = None
    ) -> tuple[int, str, str]:
        output, error = io.StringIO(), io.StringIO()
        with (
            contextlib.chdir(cwd or self.root),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(error),
        ):
            code = main(args, gateway=self.gateway)
        return code, output.getvalue(), error.getvalue()

    def test_plan_all_batches_one_sheet_read_without_state(self) -> None:
        code, output, error = self.invoke(
            "plan", "push", "--all", "--force", "--format", "json",
            "--project", str(self.root),
        )
        payload = json.loads(output)
        self.assertEqual(code, 0)
        self.assertTrue(payload["preflight_ok"])
        self.assertEqual(len(payload["tables"]), 2)
        self.assertEqual(self.gateway.read_calls, [("Items", "Offers")])
        self.assertEqual(self.gateway.writes, [])
        self.assertEqual(self.gateway.formula_scans, [("Items", "Offers")])
        self.assertFalse((self.root / ".fccsv3").exists())
        self.assertEqual(error, "")

    def test_unchanged_tables_use_one_batch_recheck_and_record_bases(self) -> None:
        for name, identifier in (("Items", "1"), ("Offers", "2")):
            (self.csv_dir / f"{name}.csv").write_text(
                f"Id,Value\nnumber,text\n{identifier},old\n", encoding="utf-8"
            )
        code, output, error = self.invoke(
            "push", "--all", "--force", "--project", str(self.root)
        )
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(self.gateway.read_calls, [
            ("Items", "Offers"), ("Items", "Offers"),
        ])
        self.assertEqual(self.gateway.writes, [])
        self.assertEqual(set(read_bases(self.root)), {"Items", "Offers"})
        self.assertEqual(output.count("[Items]"), 1)
        self.assertEqual(output.count("[Offers]"), 1)
        self.assertIn("writes=none", output)
        self.assertIn("Completed 2 of 2", output)

    def test_repeated_noop_batch_does_not_add_journal_entries(self) -> None:
        for name, identifier in (("Items", "1"), ("Offers", "2")):
            (self.csv_dir / f"{name}.csv").write_text(
                f"Id,Value\nnumber,text\n{identifier},old\n", encoding="utf-8"
            )
        command = ("push", "--all", "--force", "--project", str(self.root))
        self.assertEqual(self.invoke(*command)[0], 0)
        with StateDB(self.root, read_only=True) as state:
            first_count = len(state.history(limit=10))
        code, output, error = self.invoke(*command)
        self.assertEqual((code, error), (0, ""))
        with StateDB(self.root, read_only=True) as state:
            self.assertEqual(len(state.history(limit=10)), first_count)
        self.assertEqual(output.count("verified=no-op"), 2)
        self.assertEqual(self.gateway.writes, [])

    def test_mixed_batch_rechecks_noop_without_per_table_reads(self) -> None:
        (self.csv_dir / "Offers.csv").write_text(
            "Id,Value\nnumber,text\n2,old\n", encoding="utf-8"
        )
        code, output, error = self.invoke(
            "push", "--all", "--force", "--project", str(self.root)
        )
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(self.gateway.read_calls, [
            ("Items", "Offers"), ("Items",), ("Items",), ("Offers",),
        ])
        self.assertEqual(self.gateway.writes, ["Items"])
        self.assertEqual(set(read_bases(self.root)), {"Items", "Offers"})
        self.assertEqual(output.count("[Items]"), 1)
        self.assertEqual(output.count("[Offers]"), 1)

    def test_noop_batch_recheck_rejects_remote_drift_before_base(self) -> None:
        for name, identifier in (("Items", "1"), ("Offers", "2")):
            (self.csv_dir / f"{name}.csv").write_text(
                f"Id,Value\nnumber,text\n{identifier},old\n", encoding="utf-8"
            )
        original_read = self.gateway.read_values

        def read_with_drift(tabs: tuple[str, ...]) -> dict[str, list[list[str]]]:
            values = original_read(tabs)
            if len(self.gateway.read_calls) == 2:
                values["Offers"][1][1] = "external"
            return values

        self.gateway.read_values = read_with_drift
        code, output, error = self.invoke(
            "push", "--all", "--force", "--project", str(self.root)
        )
        self.assertEqual(code, 2)
        self.assertIn("changed since preflight", error)
        self.assertIn("Completed 0 of 2", output)
        self.assertEqual(self.gateway.writes, [])
        self.assertEqual(read_bases(self.root), {})

    def test_pending_blocks_all_before_google_reads(self) -> None:
        self.gateway.fail_on = "Items"
        code, _output, _error = self.invoke(
            "push", "Items", "--force", "--project", str(self.root)
        )
        self.assertEqual(code, 3)
        self.gateway.read_calls.clear()
        code, output, error = self.invoke(
            "push", "--all", "--force", "--project", str(self.root)
        )
        self.assertEqual(code, 2)
        self.assertIn("Completed 0 of 2", output)
        self.assertIn("is pending", error)
        self.assertEqual(self.gateway.read_calls, [])

    def test_blocked_table_prevents_every_write(self) -> None:
        (self.csv_dir / "Offers.csv").write_text(
            "Id,Id\nnumber,text\n2,new\n", encoding="utf-8"
        )
        code, output, error = self.invoke(
            "push", "--all", "--force", "--project", str(self.root)
        )
        self.assertEqual(code, 1)
        self.assertIn("No tables written", output)
        self.assertEqual(self.gateway.writes, [])
        self.assertFalse((self.root / ".fccsv3").exists())
        self.assertEqual(error, "")

    def test_formula_on_later_table_blocks_all_writes(self) -> None:
        self.gateway.formula_tabs.add("Offers")
        code, output, error = self.invoke(
            "push", "--all", "--force", "--format", "json", "--project", str(self.root)
        )
        payload = json.loads(output)
        self.assertEqual(code, 1)
        self.assertFalse(payload["preflight_ok"])
        self.assertIn("contains formulas", output)
        self.assertEqual(self.gateway.writes, [])
        self.assertEqual(self.gateway.formula_scans, [("Items", "Offers")])
        self.assertFalse((self.root / ".fccsv3").exists())
        self.assertEqual(error, "")

    def test_all_push_commits_both_table_bases(self) -> None:
        code, output, error = self.invoke(
            "push", "--all", "--force", "--project", str(self.root)
        )
        self.assertEqual(code, 0)
        self.assertIn("Completed 2 of 2", output)
        self.assertEqual(self.gateway.writes, ["Items", "Offers"])
        self.assertEqual(set(read_bases(self.root)), {"Items", "Offers"})
        self.assertEqual(error, "")

    def test_lost_response_reports_committed_and_pending_tables(self) -> None:
        self.gateway.fail_on = "Offers"
        code, output, error = self.invoke(
            "push", "--all", "--force", "--format", "json",
            "--project", str(self.root),
        )
        payload = json.loads(output)
        self.assertEqual(code, 3)
        self.assertEqual([item["table"] for item in payload["completed"]], ["Items"])
        self.assertIsNotNone(payload["pending_operation_id"])
        self.assertEqual(set(read_bases(self.root)), {"Items"})
        with StateDB(self.root) as state:
            self.assertEqual([item.table for item in state.pending()], ["Offers"])
        self.assertEqual(error, "")

    def test_write_requires_selection_and_rejects_all_mixed(self) -> None:
        cases = (
            (("push",), "provide TABLE, PATH, ., or --all"),
            (("push", "Items", "--all"), "--all cannot be combined"),
        )
        for args, message in cases:
            with self.subTest(args=args):
                code, output, error = self.invoke(
                    *args, "--project", str(self.root)
                )
                self.assertEqual(code, 2)
                self.assertEqual(output, "")
                self.assertIn(message, error)

    def test_dot_selects_every_table_for_plan_and_push(self) -> None:
        code, output, error = self.invoke(
            "plan", "push", ".", "--force", "--format", "json",
            "--project", str(self.root),
        )
        self.assertEqual((code, error), (0, ""))
        payload = json.loads(output)
        self.assertFalse(payload["all"])
        self.assertEqual([item["table"] for item in payload["tables"]], ["Items", "Offers"])
        self.assertEqual(self.gateway.read_calls, [("Items", "Offers")])
        self.assertEqual(self.gateway.writes, [])

        code, output, error = self.invoke(
            "push", ".", "--force", "--project", str(self.root),
        )
        self.assertEqual((code, error), (0, ""))
        self.assertIn("Completed 2 of 2", output)
        self.assertEqual(self.gateway.writes, ["Items", "Offers"])

    def test_path_and_table_selectors_follow_git_style(self) -> None:
        code, output, error = self.invoke(
            "push", ".", "Assets/CSV/Items.csv", "--force",
            "--project", str(self.root),
        )
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(self.gateway.writes, ["Items", "Offers"])
        self.assertIn("Completed 2 of 2", output)

    def test_csv_path_selects_only_its_mapped_table(self) -> None:
        code, output, error = self.invoke(
            "push", "Assets/CSV/Items.csv", "--force",
            "--project", str(self.root),
        )
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(self.gateway.read_calls[0], ("Items",))
        self.assertEqual(self.gateway.writes, ["Items"])

    def test_directory_path_selects_mapped_tables(self) -> None:
        code, output, error = self.invoke(
            "plan", "push", "Assets/CSV", "--force",
            "--project", str(self.root), "--format", "json",
        )
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(
            [item["table"] for item in json.loads(output)["tables"]],
            ["Items", "Offers"],
        )
        self.assertEqual(self.gateway.writes, [])

    def test_dot_works_for_read_only_commands(self) -> None:
        code, output, error = self.invoke(
            "status", ".", "--project", str(self.root),
        )
        self.assertEqual((code, error), (0, ""))
        self.assertIn("[Items]", output)
        self.assertIn("[Offers]", output)
        self.assertEqual(self.gateway.read_calls, [("Items", "Offers")])

        code, output, error = self.invoke(
            "validate", ".", "--project", str(self.root),
        )
        self.assertEqual((code, error), (0, ""))
        self.assertIn("2 table(s) checked", output)

    def test_dot_scopes_to_current_directory(self) -> None:
        nested = self.csv_dir / "nested"
        nested.mkdir()
        (nested / "Extra.csv").write_text(
            "Id,Value\nnumber,text\n3,new\n", encoding="utf-8"
        )
        config_path = self.root / ".fccsv.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["sources"][0]["tables"].append(
            {"tab": "Extra", "csv": "nested/Extra.csv", "key_column": "Id"}
        )
        config_path.write_text(json.dumps(config), encoding="utf-8")

        code, output, error = self.invoke(
            "validate", ".", "--project", str(self.root), cwd=nested,
        )
        self.assertEqual((code, error), (0, ""))
        self.assertIn("[Extra]", output)
        self.assertNotIn("[Items]", output)
        self.assertNotIn("[Offers]", output)
        self.assertIn("1 table(s) checked", output)

        code, output, error = self.invoke(
            "validate", ".", "--project", str(self.root), cwd=self.root,
        )
        self.assertEqual((code, error), (0, ""))
        self.assertIn("3 table(s) checked", output)

    def test_dot_outside_project_points_to_all(self) -> None:
        code, output, error = self.invoke(
            "push", ".", "--project", str(self.root), cwd=self.root.parent,
        )
        self.assertEqual((code, output), (2, ""))
        self.assertIn("current directory is outside the project", error)
        self.assertIn("use --all", error)
        self.assertEqual(self.gateway.read_calls, [])

    def test_named_group_uses_selected_order_and_batch_preflight(self) -> None:
        code, output, error = self.invoke(
            "push", "Offers", "Items", "--force", "--project", str(self.root)
        )
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(self.gateway.read_calls[0], ("Offers", "Items"))
        self.assertEqual(self.gateway.writes, ["Offers", "Items"])
        self.assertIn("1/2 [Offers]", output)
        self.assertIn("2/2 [Items]", output)
        self.assertEqual(set(read_bases(self.root)), {"Offers", "Items"})

    def test_named_group_json_has_batch_shape_and_all_false(self) -> None:
        code, output, error = self.invoke(
            "plan", "push", "Offers", "Items", "--force",
            "--format", "json", "--project", str(self.root),
        )
        payload = json.loads(output)
        self.assertEqual((code, error), (0, ""))
        self.assertFalse(payload["all"])
        self.assertEqual([item["table"] for item in payload["tables"]], ["Offers", "Items"])
        self.assertEqual(self.gateway.read_calls, [("Offers", "Items")])

    def test_named_group_preflight_blocks_every_selected_write(self) -> None:
        self.gateway.formula_tabs.add("Items")
        code, output, error = self.invoke(
            "push", "Offers", "Items", "--force", "--project", str(self.root)
        )
        self.assertEqual((code, error), (1, ""))
        self.assertIn("No tables written", output)
        self.assertEqual(self.gateway.writes, [])
        self.assertEqual(self.gateway.formula_scans, [("Offers", "Items")])

    def test_selection_errors_precede_google_reads(self) -> None:
        for selection, message in (
            (("Missing",), "no configured CSV matches"),
            ((".", "--all"), "--all cannot be combined"),
        ):
            with self.subTest(selection=selection):
                code, output, error = self.invoke(
                    "push", *selection, "--force", "--project", str(self.root)
                )
                self.assertEqual((code, output), (2, ""))
                self.assertIn(message, error)
        self.assertEqual(self.gateway.read_calls, [])

    def test_type_option_requires_one_pull_table(self) -> None:
        code, output, error = self.invoke(
            "pull", "Items", "Offers", "--type", "Extra=text",
            "--project", str(self.root),
        )
        self.assertEqual((code, output), (2, ""))
        self.assertIn("--type requires exactly one selected table", error)
        self.assertEqual(self.gateway.read_calls, [])

    def test_undo_command_restores_before_images_and_links_history(self) -> None:
        code, output, error = self.invoke(
            "push", "Items", "--force", "--project", str(self.root)
        )
        self.assertEqual(code, 0)
        self.assertEqual(error, "")
        with StateDB(self.root) as state:
            source_id = state.latest_committed("Items").id
        code, output, error = self.invoke(
            "undo", "Items", "--dry-run", "--project", str(self.root)
        )
        self.assertEqual(code, 0)
        self.assertIn("would undo", output)
        self.assertEqual(self.gateway.tables["Items"][1], ["1", "new"])
        code, output, error = self.invoke(
            "undo", "Items", "--operation", source_id,
            "--format", "json", "--project", str(self.root),
        )
        payload = json.loads(output)
        self.assertEqual(code, 0)
        self.assertTrue(payload["allowed"])
        self.assertEqual(payload["source_operation_id"], source_id)
        self.assertEqual(self.gateway.tables["Items"][1], ["1", "old"])
        with StateDB(self.root) as state:
            record = state.get(payload["undo_operation_id"])
            self.assertEqual(record.source_operation_id, source_id)


if __name__ == "__main__":
    unittest.main()
