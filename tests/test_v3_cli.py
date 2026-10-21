"""CLI checks use disposable projects and never contact Google Sheets."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

V3_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(V3_SRC))

from fccsv3.interfaces.cli import build_parser, main  # noqa: E402


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "Assets" / "CSV").mkdir(parents=True)
        (self.root / ".fccsv.json").write_text(
            json.dumps(
                {
                    "csv_dir": "Assets/CSV",
                    "sources": [{
                        "spreadsheet_id": "unused",
                        "tables": [{"tab": "Items", "csv": "Items.csv", "key_column": "Id"}],
                    }],
                }
            ),
            encoding="utf-8",
        )
        self.csv_path = self.root / "Assets" / "CSV" / "Items.csv"
        self.csv_path.write_text("Id,Name\nnumber,text\n1,Apple\n", encoding="utf-8")

    def invoke(self, *args: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = main(args)
        return result, stdout.getvalue(), stderr.getvalue()

    def test_validate_accepts_project_before_or_after_command(self) -> None:
        before = self.csv_path.read_bytes()
        for args in (
            ("--project", str(self.root), "validate", "Items"),
            ("validate", "Items", "--project", str(self.root)),
        ):
            with self.subTest(args=args):
                code, output, error = self.invoke(*args)
                self.assertEqual(code, 0)
                self.assertIn("[Items] ok (1 rows, 2 columns)", output)
                self.assertEqual(error, "")
        self.assertEqual(self.csv_path.read_bytes(), before)
        self.assertFalse((self.root / ".fccsv-state.json").exists())

    def test_json_diagnostics_and_exit_code_for_invalid_row(self) -> None:
        self.csv_path.write_text("Id,Name\nnumber,text\n1\n", encoding="utf-8")
        code, output, error = self.invoke(
            "validate", "Items", "--project", str(self.root), "--format", "json"
        )
        payload = json.loads(output)
        self.assertEqual(code, 1)
        self.assertEqual(error, "")
        self.assertEqual(payload["command"], "validate")
        self.assertEqual(payload["tables"][0]["diagnostics"][0]["code"], "ROW_WIDTH")
        self.assertEqual(payload["tables"][0]["diagnostics"][0]["line"], 3)

    def test_missing_csv_is_reported_without_traceback(self) -> None:
        self.csv_path.unlink()
        code, output, error = self.invoke("validate", "--project", str(self.root))
        self.assertEqual(code, 1)
        self.assertIn("LOCAL_MISSING", output)
        self.assertEqual(error, "")

    def test_unknown_table_is_usage_error(self) -> None:
        code, output, error = self.invoke("validate", "Missing", "--project", str(self.root))
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertIn("no configured CSV matches 'Missing'", error)

    def test_distribution_exposes_plain_fccsv_command(self) -> None:
        metadata = tomllib.loads(
            (Path(__file__).resolve().parents[1] / "pyproject.toml")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(build_parser().prog, "fccsv")
        self.assertEqual(
            metadata["project"]["scripts"]["fccsv"],
            "fccsv3.interfaces.cli:main",
        )
        self.assertNotIn("fccsv3", metadata["project"]["scripts"])

    def test_help_has_complete_copyable_examples(self) -> None:
        help_text = build_parser().format_help()
        self.assertIn("fccsv validate BossLootData", help_text)
        self.assertNotIn("--force push", help_text)


if __name__ == "__main__":
    unittest.main()
