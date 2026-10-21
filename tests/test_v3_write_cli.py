"""Git-style commands exercise the same application flow as direct callers."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fccsv3.adapters.csv_file import read_csv_table  # noqa: E402
from fccsv3.interfaces.cli import main  # noqa: E402


class FakeGateway:
    def __init__(self) -> None:
        self.values = [["Id", "Name"], ["1", "Remote"]]
        self.metadata = [["tab", "hash"], ["Items", "old-hash"], ["Other", "other-hash"]]
        self.writes = 0
        self.fail_after_write = False

    def read_values(self, tabs: tuple[str, ...]) -> dict[str, list[list[str]]]:
        sources = {"Items": self.values, "_fccsv": self.metadata}
        return {
            tab: [row[:] for row in sources[tab]]
            for tab in tabs if tab in sources
        }

    def write_values(self, tab: str, values: list[list[str]]) -> None:
        if tab != "Items":
            raise AssertionError(f"unexpected tab write: {tab}")
        self.writes += 1
        self.values = [row[:] for row in values]
        if self.fail_after_write:
            raise RuntimeError("simulated lost response")


class WriteCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        csv_dir = self.root / "CSV"
        csv_dir.mkdir()
        self.csv_path = csv_dir / "Items.csv"
        self.csv_path.write_text("Id,Name\nnumber,text\n1,Local\n", encoding="utf-8")
        (self.root / ".fccsv.json").write_text(json.dumps({
            "csv_dir": "CSV",
            "sources": [{"spreadsheet_id": "fake", "tables": [
                {"tab": "Items", "csv": "Items.csv", "key_column": "Id"}
            ]}],
        }), encoding="utf-8")
        self.gateway = FakeGateway()

    def invoke(self, *args: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(args, gateway=self.gateway)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_plan_and_blocked_push_explain_complete_command(self) -> None:
        code, output, error = self.invoke(
            "--project", str(self.root), "plan", "push", "Items", "--force"
        )
        self.assertEqual(code, 0)
        self.assertIn("push preview", output)
        self.assertEqual(error, "")
        self.assertEqual(self.gateway.writes, 0)
        self.assertFalse((self.root / ".fccsv3").exists())
        code, output, _ = self.invoke("push", "Items", "--project", str(self.root))
        self.assertEqual(code, 1)
        self.assertIn("fccsv push Items --force", output)
        self.assertEqual(self.gateway.writes, 0)

    def test_force_push_then_status_and_log_use_v3_base(self) -> None:
        legacy = self.root / ".fccsv-base"
        legacy.mkdir()
        (legacy / "Items.json").write_text(json.dumps({
            "headers": ["Id", "Name"], "types": ["number", "text"],
            "rows": [{"Id": "1", "Name": "Older"}],
        }), encoding="utf-8")
        code, output, error = self.invoke(
            "push", "Items", "--project", str(self.root), "--force"
        )
        self.assertEqual((code, error), (0, ""))
        self.assertIn("verified=", output)
        self.assertEqual(self.gateway.values[1], ["1", "Local"])
        code, output, _ = self.invoke(
            "status", "Items", "--project", str(self.root), "--fail-on-diff"
        )
        self.assertEqual(code, 0)
        self.assertIn("in_sync", output)
        code, output, _ = self.invoke("log", "--project", str(self.root))
        self.assertEqual(code, 0)
        self.assertIn("Items push committed", output)

    def test_push_preserves_legacy_metadata_and_mapping_overrides(self) -> None:
        config_path = self.root / ".fccsv.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        mapping = config["sources"][0]["tables"][0]
        mapping["key_column"] = "Id,Name"
        mapping["column_map"] = {"DisplayName": "Name"}
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        config_before = config_path.read_bytes()
        metadata_before = [row[:] for row in self.gateway.metadata]
        self.gateway.values = [["Id", "DisplayName"], ["1", "Remote"]]

        code, output, error = self.invoke(
            "push", "Items", "--project", str(self.root), "--force"
        )
        self.assertEqual((code, error), (0, ""))
        self.assertIn("verified=", output)
        self.assertEqual(self.gateway.values, [["Id", "DisplayName"], ["1", "Local"]])
        self.assertEqual(self.gateway.metadata, metadata_before)
        self.assertEqual(config_path.read_bytes(), config_before)

        code, output, error = self.invoke(
            "status", "Items", "--project", str(self.root), "--fail-on-diff"
        )
        self.assertEqual((code, error), (0, ""))
        self.assertIn("in_sync", output)
        self.assertEqual(config_path.read_bytes(), config_before)

    def test_pull_requires_type_for_new_sheet_column(self) -> None:
        self.gateway.values = [["Id", "Name", "Extra"], ["1", "Remote", "new"]]
        code, output, _ = self.invoke(
            "pull", "Items", "--project", str(self.root),
            "--force", "--accept-schema",
        )
        self.assertEqual(code, 1)
        self.assertIn("new Sheet column(s) need CSV types: Extra", output)
        code, output, error = self.invoke(
            "pull", "Items", "--project", str(self.root),
            "--force", "--accept-schema", "--type", "Extra=text",
        )
        self.assertEqual((code, error), (0, ""))
        self.assertTrue("verified" in output)
        self.assertEqual(read_csv_table(self.csv_path).headers, ("Id", "Name", "Extra"))

    def test_single_table_compact_and_verbose_text(self) -> None:
        code, output, error = self.invoke(
            "plan", "push", "Items", "--force", "--project", str(self.root)
        )
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(len([line for line in output.splitlines() if "[Items]" in line]), 1)
        self.assertIn("1/1 [Items]", output)
        code, output, error = self.invoke(
            "plan", "push", "Items", "--force", "--verbose",
            "--project", str(self.root),
        )
        self.assertEqual((code, error), (0, ""))
        self.assertIn("  CSV 1 rows; Sheet 1 rows", output)

    def test_single_table_json_retains_flat_shape(self) -> None:
        code, output, error = self.invoke(
            "plan", "push", "Items", "--force", "--format", "json",
            "--project", str(self.root),
        )
        payload = json.loads(output)
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(payload["table"], "Items")
        self.assertNotIn("tables", payload)
        self.assertNotIn("all", payload)

    def test_recover_cli_lists_inspects_and_restores(self) -> None:
        self.gateway.fail_after_write = True
        code, _, error = self.invoke(
            "push", "Items", "--project", str(self.root), "--force"
        )
        self.assertEqual(code, 3)
        self.assertIn("is pending", error)
        self.gateway.fail_after_write = False
        code, output, _ = self.invoke("recover", "--project", str(self.root))
        self.assertEqual(code, 0)
        operation_id = output.split()[0]
        code, output, _ = self.invoke(
            "recover", operation_id, "--project", str(self.root)
        )
        self.assertEqual(code, 0)
        self.assertIn("--restore", output)
        code, output, _ = self.invoke(
            "recover", operation_id, "--project", str(self.root), "--restore"
        )
        self.assertEqual(code, 0)
        self.assertIn("restore verified", output)
        self.assertEqual(self.gateway.values[1], ["1", "Remote"])


if __name__ == "__main__":
    unittest.main()
