"""The guided TUI uses structured plans and reports failed writes as errors."""

from __future__ import annotations

import contextlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fccsv3.adapters.state_db import StateDB  # noqa: E402
from fccsv3.interfaces.cli import main  # noqa: E402
from fccsv3.interfaces.command_text import table_command  # noqa: E402
from fccsv3.interfaces.tui import run_tui  # noqa: E402


class Gateway:
    def __init__(self) -> None:
        self.values = [["Id", "Value"], ["1", "old"]]
        self.writes = 0
        self.fail_after_write = False

    def read_values(self, tabs: tuple[str, ...]) -> dict[str, list[list[str]]]:
        return {tabs[0]: [row[:] for row in self.values]}

    def write_values(self, tab: str, values: list[list[str]]) -> None:
        self.writes += 1
        self.values = [row[:] for row in values]
        if self.fail_after_write:
            raise RuntimeError("simulated lost response")


class GuidedTuiTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        csv_dir = self.root / "CSV"
        csv_dir.mkdir()
        (csv_dir / "Items.csv").write_text(
            "Id,Value\nnumber,text\n1,new\n", encoding="utf-8"
        )
        (self.root / ".fccsv.json").write_text(
            json.dumps({
                "csv_dir": "CSV",
                "sources": [{
                    "spreadsheet_id": "fake",
                    "tables": [{"tab": "Items", "csv": "Items.csv", "key_column": "Id"}],
                }],
            }), encoding="utf-8",
        )
        self.gateway = Gateway()

    def run_script(self, *answers: str) -> tuple[int, str]:
        scripted = iter(answers)
        lines: list[str] = []
        code = run_tui(
            self.root, self.gateway,
            input_fn=lambda prompt: next(scripted), output=lines.append,
        )
        return code, "\n".join(lines)

    def test_diff_is_read_only_and_shows_cli_equivalent(self) -> None:
        code, output = self.run_script("1", "diff", "quit")
        self.assertEqual(code, 0)
        self.assertIn("CSV='new'; Sheet='old'", output)
        self.assertIn("fccsv diff Items", output)
        self.assertEqual(self.gateway.writes, 0)
        self.assertFalse((self.root / ".fccsv3").exists())

    def test_legacy_base_does_not_change_tui_status(self) -> None:
        base_dir = self.root / ".fccsv-base"
        base_dir.mkdir()
        (base_dir / "Items.json").write_text(
            json.dumps({
                "headers": ["Id", "Value"], "types": ["number", "text"],
                "rows": [{"Id": "1", "Value": "old"}],
            }), encoding="utf-8",
        )
        code, output = self.run_script("quit")
        self.assertEqual(code, 0)
        self.assertIn("Items: unbased_divergence", output)
        self.assertFalse((self.root / ".fccsv3").exists())

    def test_force_requires_two_explicit_confirmations(self) -> None:
        code, output = self.run_script("1", "push", "FORCE", "APPLY", "quit")
        self.assertEqual(code, 0)
        self.assertIn("fccsv push Items --force", output)
        self.assertIn("Verified; operation", output)
        self.assertEqual(self.gateway.values[1], ["1", "new"])
        with StateDB(self.root) as state:
            self.assertEqual(len(state.history("Items", 10)), 1)

    def test_failed_write_has_error_severity_and_pending_recovery(self) -> None:
        self.gateway.fail_after_write = True
        code, output = self.run_script("1", "push", "FORCE", "APPLY", "quit")
        self.assertEqual(code, 0)
        self.assertIn("ERROR: operation", output)
        self.assertIn("fccsv recover", output)
        self.assertNotIn("Verified; operation", output)
        with StateDB(self.root) as state:
            self.assertEqual(len(state.pending()), 1)

    def test_status_does_not_migrate_v1_state(self) -> None:
        directory = self.root / ".fccsv3"
        directory.mkdir()
        database = directory / "state.sqlite3"
        with contextlib.closing(sqlite3.connect(database)) as connection:
            connection.execute("CREATE TABLE bases(tab TEXT, snapshot TEXT)")
            connection.execute(
                "CREATE TABLE operations(id TEXT, tab TEXT, status TEXT, started_at TEXT)"
            )
            connection.execute(
                "INSERT INTO operations VALUES ('unfinished', 'Items', 'pending', '2026-01-01')"
            )
            connection.execute("PRAGMA user_version=1")
            connection.commit()
        code, output = self.run_script("quit")
        self.assertEqual(code, 0)
        self.assertIn("pending unfinished for Items", output)
        with contextlib.closing(sqlite3.connect(database)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_schema_change_blocks_without_claiming_success(self) -> None:
        self.gateway.values = [["Id", "Value", "New"], ["1", "old", "x"]]
        code, output = self.run_script("1", "pull", "quit")
        self.assertEqual(code, 0)
        self.assertIn("BLOCKED: schema differs", output)
        self.assertNotIn("Verified", output)
        self.assertEqual(self.gateway.writes, 0)

    def test_cli_dispatches_tui_with_project_and_gateway(self) -> None:
        with patch("fccsv3.interfaces.tui.run_tui", return_value=0) as run:
            code = main(["tui", "--project", str(self.root)], gateway=self.gateway)
        self.assertEqual(code, 0)
        run.assert_called_once_with(self.root.resolve(), self.gateway)

    def test_copyable_powershell_command_quotes_table_name(self) -> None:
        self.assertEqual(
            table_command("push", "Boss's Loot", force=True),
            "fccsv push 'Boss''s Loot' --force",
        )
