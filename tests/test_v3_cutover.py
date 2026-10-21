"""Cutover checks use disposable files and an in-memory Sheet reader."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fccsv3.adapters.legacy_base import legacy_base_path  # noqa: E402
from fccsv3.adapters.state_db import StateDB  # noqa: E402
from fccsv3.application.cutover import (  # noqa: E402
    audit_cutover,
    create_cutover_backup,
)
from fccsv3.application.legacy_config import load_legacy_project  # noqa: E402
from fccsv3.interfaces.cli import main  # noqa: E402


# Produced by v2 snapshot_hash for the rows below. V2 omits CSV types.
V2_HASH = "dd9bd4ef8308bf0cbfb02a6e4494d5cc13bbf9dae6fb41920507cd292993d8b9"


class Reader:
    def __init__(self, values: dict[str, list[list[str]]]) -> None:
        self.values = values
        self.calls: list[tuple[str, ...]] = []

    def read_values(self, tabs: tuple[str, ...]) -> dict[str, list[list[str]]]:
        self.calls.append(tabs)
        return {
            tab: [row[:] for row in self.values[tab]]
            for tab in tabs if tab in self.values
        }


class CutoverTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.csv_dir = self.root / "Assets" / "CSV"
        self.csv_dir.mkdir(parents=True)
        self.csv_path = self.csv_dir / "BossLootData.csv"
        self.csv_path.write_bytes(
            b"ItemId,AccessKey,Amount\nint,string,int\n"
            b"00001,basic,1\n00001,tier2,2\n00002,basic,3\n"
        )
        self.rows = [
            ["00001", "basic", "1"],
            ["00001", "tier2", "2"],
            ["00002", "basic", "3"],
        ]
        self.configure("BossLootData")

    def configure(self, tab: str) -> None:
        (self.root / ".fccsv.json").write_text(
            json.dumps({
                "csv_dir": "Assets/CSV",
                "sources": [{
                    "spreadsheet_id": "disposable-sheet",
                    "tables": [{
                        "tab": tab,
                        "csv": "BossLootData.csv",
                        "key_column": "ItemId",
                    }],
                }],
            }),
            encoding="utf-8",
        )
        (self.root / ".fccsv-state.json").write_text(
            json.dumps({"tables": {tab: {"local": V2_HASH, "remote": V2_HASH}}}),
            encoding="utf-8",
        )
        base_path = legacy_base_path(self.root, tab)
        base_path.parent.mkdir(parents=True, exist_ok=True)
        base_path.write_text(
            json.dumps({
                "headers": ["ItemId", "AccessKey", "Amount"],
                "types": ["int", "string", "int"],
                "rows": [
                    dict(zip(("ItemId", "AccessKey", "Amount"), row, strict=True))
                    for row in self.rows
                ],
            }),
            encoding="utf-8",
        )
        self.reader = Reader({
            tab: [["ItemId", "AccessKey", "Amount"], *self.rows],
            "_fccsv": [["tab", "hash"], [tab, V2_HASH]],
        })

    def test_audit_reports_stale_single_key_and_valid_composite_identity(self) -> None:
        project = load_legacy_project(self.root)
        report = audit_cutover(self.root, project, self.reader)
        self.assertTrue(report.ready_for_review)
        self.assertEqual(report.blockers, ())
        self.assertEqual(self.reader.calls, [("BossLootData", "_fccsv")])
        table = report.tables[0]
        self.assertEqual(table.identity, "ItemId,AccessKey")
        self.assertEqual((table.csv_rows, table.sheet_rows), (3, 3))
        self.assertTrue(table.v2_base_schema_matches)
        self.assertTrue(table.v2_local_hash_matches)
        self.assertTrue(table.v2_sheet_hash_matches)
        self.assertTrue(table.metadata_hash_matches_v2)
        self.assertTrue(any("configured key ItemId" in item for item in report.warnings))
        self.assertFalse((self.root / ".fccsv3").exists())

    def test_audit_sees_type_change_even_when_v2_hash_still_matches(self) -> None:
        self.csv_path.write_text(
            self.csv_path.read_text(encoding="utf-8").replace(
                "int,string,int", "string,string,int"
            ),
            encoding="utf-8",
        )
        report = audit_cutover(self.root, load_legacy_project(self.root), self.reader)
        self.assertTrue(report.tables[0].v2_local_hash_matches)
        self.assertFalse(report.tables[0].v2_base_schema_matches)
        self.assertTrue(any("type row differs" in item for item in report.warnings))

    def test_audit_reports_sheet_value_changed_since_v2_state(self) -> None:
        self.reader.values["BossLootData"][1][2] = "9"
        report = audit_cutover(self.root, load_legacy_project(self.root), self.reader)
        self.assertFalse(report.tables[0].v2_sheet_hash_matches)
        self.assertTrue(any("Sheet differs" in item for item in report.warnings))

    def test_backup_contains_exact_csv_and_sanitized_legacy_base(self) -> None:
        tab = "Boss/Loot"
        self.configure(tab)
        project = load_legacy_project(self.root)
        with StateDB(self.root):
            pass
        output = self.root / "archive" / "cutover.zip"
        result = create_cutover_backup(self.root, project, self.reader, output)
        self.assertEqual(result, output.resolve())
        with zipfile.ZipFile(output) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(archive.read("csv/000.csv"), self.csv_path.read_bytes())
            self.assertEqual(
                json.loads(archive.read("sheet/values.json"))[tab],
                [["ItemId", "AccessKey", "Amount"], *self.rows],
            )
            self.assertEqual(
                json.loads(archive.read("legacy-base/000.json"))["headers"],
                ["ItemId", "AccessKey", "Amount"],
            )
            self.assertEqual(manifest["spreadsheet_id"], "disposable-sheet")
            self.assertEqual(manifest["legacy_state_path"], str(project.legacy_state_file))
            self.assertTrue(archive.read("v3/state.sqlite3").startswith(b"SQLite format 3\0"))
            for name, digest in manifest["entries"].items():
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), digest)
        with self.assertRaisesRegex(ValueError, "already exists"):
            create_cutover_backup(self.root, project, self.reader, output)
        del self.reader.values[tab]
        missing_output = self.root / "archive" / "missing.zip"
        with self.assertRaisesRegex(ValueError, "Sheet tab"):
            create_cutover_backup(self.root, project, self.reader, missing_output)
        self.assertFalse(missing_output.exists())

    def test_audit_reports_pending_without_migrating_v3_database(self) -> None:
        directory = self.root / ".fccsv3"
        directory.mkdir()
        database = directory / "state.sqlite3"
        with contextlib.closing(sqlite3.connect(database)) as connection:
            connection.execute("CREATE TABLE bases(tab TEXT, snapshot TEXT)")
            connection.execute(
                "CREATE TABLE operations(id TEXT, tab TEXT, status TEXT, started_at TEXT)"
            )
            connection.execute(
                "INSERT INTO operations VALUES "
                "('unfinished', 'BossLootData', 'pending', '2026-01-01')"
            )
            connection.execute("PRAGMA user_version=1")
            connection.commit()
        report = audit_cutover(self.root, load_legacy_project(self.root), self.reader)
        self.assertFalse(report.ready_for_review)
        self.assertEqual(report.pending_operations, ("unfinished",))
        with contextlib.closing(sqlite3.connect(database)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_backup_rejects_a_sheet_change_during_capture(self) -> None:
        class ChangingReader(Reader):
            def read_values(self, tabs: tuple[str, ...]) -> dict[str, list[list[str]]]:
                result = super().read_values(tabs)
                if len(self.calls) == 2:
                    result["BossLootData"][1][2] = "changed"
                return result

        reader = ChangingReader(self.reader.values)
        output = self.root / "unstable.zip"
        with self.assertRaisesRegex(RuntimeError, "Sheet values changed"):
            create_cutover_backup(
                self.root, load_legacy_project(self.root), reader, output
            )
        self.assertEqual(len(reader.calls), 2)
        self.assertFalse(output.exists())

    def test_backup_rejects_a_local_change_during_capture(self) -> None:
        outer = self

        class ChangingReader(Reader):
            def read_values(self, tabs: tuple[str, ...]) -> dict[str, list[list[str]]]:
                if len(self.calls) == 1:
                    outer.csv_path.write_bytes(outer.csv_path.read_bytes() + b"\n")
                return super().read_values(tabs)

        output = self.root / "unstable.zip"
        with self.assertRaisesRegex(RuntimeError, "local file changed"):
            create_cutover_backup(
                self.root, load_legacy_project(self.root),
                ChangingReader(self.reader.values), output,
            )
        self.assertFalse(output.exists())

    def test_cli_doctor_and_backup_have_machine_readable_results(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(
                ["doctor", "--project", str(self.root), "--format", "json"],
                reader=self.reader,
            )
        self.assertEqual((code, err.getvalue()), (0, ""))
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["command"], "doctor")
        self.assertEqual(payload["tables"][0]["identity"], "ItemId,AccessKey")
        self.assertTrue(payload["ready_for_review"])

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(
                main(["doctor", "--project", str(self.root)], reader=self.reader), 0
            )
        self.assertIn("[BossLootData]", out.getvalue())
        self.assertIn("v2 BASE: found", out.getvalue())

        output = self.root / "cutover.zip"
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(
                ["backup", "--project", str(self.root), "--output", str(output),
                 "--format", "json"],
                reader=self.reader,
            )
        self.assertEqual((code, err.getvalue()), (0, ""))
        self.assertEqual(json.loads(out.getvalue())["command"], "backup")
        self.assertTrue(output.is_file())

    def test_config_rejects_two_tabs_writing_the_same_csv(self) -> None:
        config_path = self.root / ".fccsv.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["sources"][0]["tables"].append({
            "tab": "AnotherLootTab", "csv": "BossLootData.csv"
        })
        config_path.write_text(json.dumps(config), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "both map to"):
            load_legacy_project(self.root)

    def test_config_rejects_csv_path_outside_csv_directory(self) -> None:
        config_path = self.root / ".fccsv.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["sources"][0]["tables"][0]["csv"] = "../outside.csv"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "escapes csv_dir"):
            load_legacy_project(self.root)


if __name__ == "__main__":
    unittest.main()
