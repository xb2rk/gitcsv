"""Tests for configuration parsing, URL extraction, and cascade loading."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
import unittest

from gitcsv.config.loader import extract_spreadsheet_id, load_config


class ConfigTests(unittest.TestCase):
    def test_extract_spreadsheet_id_from_urls_and_ids(self) -> None:
        raw_id = "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms"

        # Raw ID
        self.assertEqual(extract_spreadsheet_id(raw_id), raw_id)

        # Full URL with edit#gid=0
        url_with_gid = f"https://docs.google.com/spreadsheets/d/{raw_id}/edit#gid=0"
        self.assertEqual(extract_spreadsheet_id(url_with_gid), raw_id)

        # Full URL ending with /edit
        url_edit = f"https://docs.google.com/spreadsheets/d/{raw_id}/edit"
        self.assertEqual(extract_spreadsheet_id(url_edit), raw_id)

        # Full URL without trailing slash
        url_bare = f"https://docs.google.com/spreadsheets/d/{raw_id}"
        self.assertEqual(extract_spreadsheet_id(url_bare), raw_id)

    def test_load_config_from_gitcsv_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            gitcsv_dir = root / ".gitcsv"
            gitcsv_dir.mkdir()

            config_data = {
                "spreadsheet_id": "test_sheet_123",
                "csv_dir": "Assets/CSV",
                "tables": [
                    {"tab": "Items", "csv": "Items.csv", "key_column": "Id"},
                    {"tab": "Monsters", "csv": "Monsters.csv"},
                ],
            }
            (gitcsv_dir / "config.json").write_text(json.dumps(config_data), encoding="utf-8")

            config = load_config(root)
            self.assertEqual(config.spreadsheet_id, "test_sheet_123")
            self.assertEqual(config.csv_dir.resolve(), (root / "Assets" / "CSV").resolve())
            self.assertEqual(len(config.tables), 2)
            self.assertEqual(config.tables[0].tab, "Items")
            self.assertEqual(config.tables[0].preferred_key, ("Id",))
            self.assertEqual(config.tables[1].tab, "Monsters")
            self.assertEqual(config.tables[1].preferred_key, ())

    def test_fallback_to_legacy_fccsv_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            legacy_data = {
                "csv_dir": "Assets/CSV",
                "sources": [
                    {
                        "spreadsheet_id": "legacy_sheet_456",
                        "tables": [
                            {"tab": "BossLootData", "csv": "BossLootData.csv"}
                        ],
                    }
                ],
            }
            (root / ".fccsv.json").write_text(json.dumps(legacy_data), encoding="utf-8")

            config = load_config(root)
            self.assertEqual(config.spreadsheet_id, "legacy_sheet_456")
            self.assertEqual(len(config.tables), 1)
            self.assertEqual(config.tables[0].tab, "BossLootData")


if __name__ == "__main__":
    unittest.main()
