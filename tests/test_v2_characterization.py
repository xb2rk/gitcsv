"""Disposable reproductions of v2 behavior that v3 must change.

These tests intentionally pass when v2 exhibits the documented defects. V3
acceptance tests must invert the assertions; this suite is evidence, not a
correctness gate for v3.
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

V2_SRC = Path(__file__).resolve().parents[2] / "fccsv-v2" / "src"
sys.path.insert(0, str(V2_SRC))

from fccsv.adapters.sheets import GoogleSheetsGateway, InMemorySheetsGateway  # noqa: E402
from fccsv.domain.diff import diff_rows  # noqa: E402
from fccsv.domain.errors import ConflictError  # noqa: E402
from fccsv.domain.snapshot import TableSnapshot  # noqa: E402
from fccsv.interfaces.cli import build_parser, main  # noqa: E402


HEADERS = ["Id", "Name", "Price"]
TYPES = ["number", "text", "number"]
ROWS = [["1", "Apple", "100"], ["2", "Banana", "50"]]


class _Request:
    def __init__(self, callback: Any) -> None:
        self.callback = callback

    def execute(self) -> dict[str, Any]:
        return self.callback()


class _FakeGoogleValues:
    """Model values.batchUpdate: cells outside the written range remain."""

    def __init__(self, values: list[list[str]]) -> None:
        self.grid = [list(row) for row in values]

    def spreadsheets(self) -> _FakeGoogleValues:
        return self

    def values(self) -> _FakeGoogleValues:
        return self

    def batchUpdate(self, *, spreadsheetId: str, body: dict[str, Any]) -> _Request:
        _ = spreadsheetId

        def apply() -> dict[str, Any]:
            for item in body["data"]:
                for row_index, new_row in enumerate(item["values"]):
                    while row_index >= len(self.grid):
                        self.grid.append([])
                    row = self.grid[row_index]
                    for column_index, value in enumerate(new_row):
                        while column_index >= len(row):
                            row.append("")
                        row[column_index] = value
            return {"responses": []}

        return _Request(apply)


class V2Characterization(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="fccsv-v3-phase-a-")
        self.root = Path(self.temporary.name)
        (self.root / "Assets" / "CSV").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _snapshot(
        self,
        name: str,
        rows: list[list[str]],
        headers: list[str] | None = None,
        *,
        key_column: str = "Id",
    ) -> TableSnapshot:
        columns = headers or HEADERS
        return TableSnapshot(
            name=name,
            headers=list(columns),
            rows=[dict(zip(columns, row, strict=True)) for row in rows],
            key_column=key_column,
        )

    def _write_csv(
        self,
        name: str,
        rows: list[list[str]],
        headers: list[str] | None = None,
        types: list[str] | None = None,
    ) -> None:
        path = self.root / "Assets" / "CSV" / f"{name}.csv"
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(headers or HEADERS)
            writer.writerow(types or TYPES)
            writer.writerows(rows)

    def _write_config(self, *names: str, key_column: str = "Id") -> None:
        config = {
            "sources": [
                {
                    "spreadsheet_id": "test-id",
                    "tables": [
                        {
                            "tab": name,
                            "csv": f"{name}.csv",
                            "key_column": key_column,
                            "column_map": {},
                            "skip_type_row_in_sheet": True,
                        }
                        for name in names
                    ],
                }
            ],
            "csv_dir": "Assets/CSV",
            "credentials_file": "credentials.json",
            "token_file": "token.json",
            "state_file": ".fccsv-state.json",
        }
        (self.root / ".fccsv.json").write_text(json.dumps(config), encoding="utf-8")

    def _one_table(
        self,
        *,
        local_rows: list[list[str]] | None = None,
        remote_rows: list[list[str]] | None = None,
    ) -> InMemorySheetsGateway:
        self._write_config("Items")
        self._write_csv("Items", local_rows if local_rows is not None else ROWS)
        return InMemorySheetsGateway(
            {"Items": self._snapshot("Items", remote_rows if remote_rows is not None else ROWS)}
        )

    def _run(
        self, *arguments: str, gateway: InMemorySheetsGateway | None = None
    ) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["--root", str(self.root), *arguments], gateway=gateway)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_a01_force_hint_has_wrong_argument_order(self) -> None:
        hint = str(ConflictError("Items"))
        self.assertIn("--force push", hint)
        self.assertIn("--force pull", hint)
        self.assertTrue(build_parser().parse_args(["push", "--force"]).force)

    def test_a02_offline_push_records_a_real_sync(self) -> None:
        self._one_table()
        code, output, error = self._run("--offline", "push")
        self.assertEqual((code, error), (0, ""))
        self.assertIn("pushed", output)
        state = json.loads((self.root / ".fccsv-state.json").read_text(encoding="utf-8"))
        self.assertIn("Items", state["tables"])

    def test_a03_shorter_google_write_leaves_old_cells(self) -> None:
        fake = _FakeGoogleValues(
            [["Id", "Name", "Extra"], ["1", "Old", "stale"], ["2", "Gone", "stale"]]
        )
        gateway = GoogleSheetsGateway.__new__(GoogleSheetsGateway)
        gateway.service = fake
        gateway.spreadsheet_id = "test-id"
        gateway._tabs_cache = ["Items"]
        gateway._execute = lambda request: request.execute()
        gateway.write_tab(
            self._snapshot("Items", [["1", "New"]], headers=["Id", "Name"])
        )
        self.assertEqual(fake.grid[0], ["Id", "Name", "Extra"])
        self.assertEqual(fake.grid[1], ["1", "New", "stale"])
        self.assertEqual(fake.grid[2], ["2", "Gone", "stale"])

    def test_a04_schema_change_pull_drops_new_sheet_column(self) -> None:
        gateway = self._one_table()
        self.assertEqual(self._run("push", gateway=gateway)[0], 0)
        gateway.write_tab(
            self._snapshot(
                "Items",
                [["1", "Apple", "100", "fresh"], ["2", "Banana", "50", "fresh"]],
                headers=[*HEADERS, "NewColumn"],
            )
        )
        code, _, error = self._run("pull", "--allow-schema-change", gateway=gateway)
        self.assertEqual((code, error), (0, ""))
        with (self.root / "Assets" / "CSV" / "Items.csv").open(
            encoding="utf-8", newline=""
        ) as stream:
            local = list(csv.reader(stream))
        self.assertEqual(local[0], HEADERS)
        self.assertNotIn("NewColumn", local[0])

    def test_a05_keyed_diff_collapses_duplicate_rows(self) -> None:
        local = self._snapshot("Items", [["1", "Lost", "1"], ["1", "Kept", "2"]])
        remote = self._snapshot("Items", [["1", "Kept", "2"]])
        self.assertFalse(diff_rows(local, remote).has_change)
        self.assertNotEqual(local.rows, remote.rows)

    def test_a06_order_only_change_passes_ci_gate(self) -> None:
        gateway = self._one_table()
        self.assertEqual(self._run("push", gateway=gateway)[0], 0)
        self._write_csv("Items", list(reversed(ROWS)))
        code, _, error = self._run("status", "--fail-on-diff", gateway=gateway)
        self.assertEqual((code, error), (0, ""))

    def test_a07_type_only_change_passes_ci_gate(self) -> None:
        gateway = self._one_table()
        self.assertEqual(self._run("push", gateway=gateway)[0], 0)
        self._write_csv("Items", ROWS, types=["text", "text", "number"])
        code, _, error = self._run("status", "--fail-on-diff", gateway=gateway)
        self.assertEqual((code, error), (0, ""))

    def test_a08_single_tab_sync_discards_other_metadata(self) -> None:
        self._write_config("Items", "Other")
        self._write_csv("Items", ROWS)
        self._write_csv("Other", ROWS)
        gateway = InMemorySheetsGateway(
            {name: self._snapshot(name, ROWS) for name in ("Items", "Other")}
        )
        self.assertEqual(self._run("push", gateway=gateway)[0], 0)
        self.assertEqual(len(gateway.read_tab("_fccsv").rows), 2)
        self._write_csv("Items", [["1", "Apple", "101"], ROWS[1]])
        self.assertEqual(self._run("push", "--tab", "Items", gateway=gateway)[0], 0)
        self.assertEqual([row["tab"] for row in gateway.read_tab("_fccsv").rows], ["Items"])

    def test_a09_reinit_drops_table_overrides(self) -> None:
        gateway = self._one_table()
        path = self.root / ".fccsv.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        mapping = config["sources"][0]["tables"][0]
        mapping["key_column"] = "Id,Name"
        mapping["column_map"] = {"Name": "DisplayName"}
        path.write_text(json.dumps(config), encoding="utf-8")
        self.assertEqual(self._run("init", "test-id", gateway=gateway)[0], 0)
        updated = json.loads(path.read_text(encoding="utf-8"))
        new_mapping = updated["sources"][0]["tables"][0]
        self.assertNotEqual(new_mapping["key_column"], "Id,Name")
        self.assertEqual(new_mapping["column_map"], {})

    @unittest.skipUnless(importlib.util.find_spec("textual"), "v2 TUI extra unavailable")
    def test_a10_failed_sync_and_onboarding_use_normal_notifications(self) -> None:
        from fccsv.interfaces.tui.app import FccsvApp

        gateway = self._one_table()
        app = FccsvApp(root=self.root, gateway=gateway)
        notices: list[tuple[str, dict[str, Any]]] = []

        async def run() -> None:
            with (
                patch.object(app, "_render_status"),
                patch.object(app, "_refresh", new_callable=AsyncMock),
                patch.object(app._backend, "sync", return_value=(1, "simulated failure")),
                patch.object(app._backend, "onboard_init", return_value=(1, "")),
                patch.object(app, "notify", side_effect=lambda message, **kwargs: notices.append(
                    (message, kwargs)
                )),
            ):
                await app._do_sync("push", ["Items"], force=False)
                await app._do_onboard("test-id")

        asyncio.run(run())
        self.assertIn(("simulated failure", {}), notices)
        self.assertIn(("sheet connected", {}), notices)


if __name__ == "__main__":
    unittest.main()
