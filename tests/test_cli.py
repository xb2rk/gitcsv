"""Tests for gitcsv Typer CLI interface using CliRunner."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
import unittest

from typer.testing import CliRunner

from gitcsv import __version__
from gitcsv.cli.main import app


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()
        self.original_cwd = Path.cwd()

    def tearDown(self) -> None:
        import os
        os.chdir(self.original_cwd)

    def test_version_flag(self) -> None:
        result = self.runner.invoke(app, ["--version"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn(__version__, result.output)

    def test_init_command_creates_config_and_gitignore(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            csv_dir = root / "Assets" / "CSV"
            csv_dir.mkdir(parents=True)
            (csv_dir / "Plants.csv").write_text("Id,Name\nstring,string\n1,Peashooter\n", encoding="utf-8-sig")

            # Run init pointing to this temp dir
            result = self.runner.invoke(
                app,
                [
                    "-C", str(root),
                    "init",
                    "https://docs.google.com/spreadsheets/d/test_pvz_spreadsheet_999/edit",
                    "--csv-dir", "Assets/CSV",
                ],
            )
            self.assertEqual(result.exit_code, 0)

            # Check that .gitcsv/config.json was created
            config_file = root / ".gitcsv" / "config.json"
            self.assertTrue(config_file.is_file())
            content = json.loads(config_file.read_text(encoding="utf-8"))
            self.assertEqual(content["spreadsheet_id"], "test_pvz_spreadsheet_999")
            self.assertEqual(content["tables"][0]["tab"], "Plants")
            self.assertEqual(content["tables"][0]["csv"], "Plants.csv")

            # Check that .gitignore was configured
            gitignore_file = root / ".gitignore"
            self.assertTrue(gitignore_file.is_file())
            self.assertIn(".gitcsv/", gitignore_file.read_text(encoding="utf-8"))

            import os
            os.chdir(self.original_cwd)


    def test_help_command_displays_grouped_panels(self) -> None:
        result = self.runner.invoke(app, ["--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("start a working area", result.output)
        self.assertIn("inspect status and differences", result.output)
        self.assertIn("transfer and synchronize data", result.output)
        self.assertIn("undo and recovery", result.output)
        self.assertIn("system and diagnosis", result.output)

    def test_doctor_command_on_initialized_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            csv_dir = root / "Assets" / "CSV"
            csv_dir.mkdir(parents=True)
            (csv_dir / "Plants.csv").write_text("Id,Name\nstring,string\n1,Peashooter\n", encoding="utf-8-sig")

            gitcsv_dir = root / ".gitcsv"
            gitcsv_dir.mkdir()
            config_payload = {
                "spreadsheet_id": "dummy_sheet",
                "csv_dir": "Assets/CSV",
                "tables": [{"tab": "Plants", "csv": "Plants.csv"}],
            }
            (gitcsv_dir / "config.json").write_text(json.dumps(config_payload), encoding="utf-8")

            result = self.runner.invoke(app, ["-C", str(root), "doctor"])
            self.assertEqual(result.exit_code, 0)
            self.assertIn("Project configuration loaded", result.output)
            self.assertIn("All local CSV files passed shape and type validation", result.output)

            import os
            os.chdir(self.original_cwd)


if __name__ == "__main__":
    unittest.main()
