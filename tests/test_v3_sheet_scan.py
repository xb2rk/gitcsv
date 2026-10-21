"""Read-only CLI and Google gateway contract tests use fake Sheet responses."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import types
import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

V3_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(V3_SRC))

from fccsv3.adapters.google_sheets import (  # noqa: E402
    GoogleSheetsGateway,
    GoogleSheetsValuesReader,
    _execute,
)
from fccsv3.application.apply import apply_table  # noqa: E402
from fccsv3.application.scan import TableMapping  # noqa: E402
from fccsv3.interfaces.cli import main  # noqa: E402


class Reader:
    def __init__(self, values: dict[str, list[list[str]]]) -> None:
        self.values = values
        self.calls: list[tuple[str, ...]] = []

    def read_values(self, tabs: tuple[str, ...]) -> dict[str, list[list[str]]]:
        self.calls.append(tabs)
        return {tab: self.values[tab] for tab in tabs if tab in self.values}


class ReadOnlyCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        csv_dir = self.root / "Assets" / "CSV"
        csv_dir.mkdir(parents=True)
        self.csv_path = csv_dir / "Items.csv"
        self.csv_path.write_text("Id,Name\nnumber,text\n1,Apple\n2,Pear\n", encoding="utf-8")
        (self.root / ".fccsv.json").write_text(
            json.dumps({
                "csv_dir": "Assets/CSV",
                "sources": [{"spreadsheet_id": "test", "tables": [
                    {"tab": "Items", "csv": "Items.csv", "key_column": "Id"}
                ]}],
            }),
            encoding="utf-8",
        )

    def invoke(self, reader: Reader | None, *args: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(args, reader=reader)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_status_flags_work_before_and_after_command(self) -> None:
        reader = Reader({"Items": [["Id", "Name"], ["2", "Pear"], ["1", "Apple"]]})
        before = self.csv_path.read_bytes()
        for args in (
            ("--project", str(self.root), "status", "Items", "--fail-on-diff"),
            ("status", "Items", "--project", str(self.root), "--fail-on-diff"),
        ):
            with self.subTest(args=args):
                code, output, error = self.invoke(reader, *args)
                self.assertEqual(code, 1)
                self.assertIn("[Items] unbased_divergence", output)
                self.assertEqual(error, "")
        self.assertEqual(reader.calls, [("Items",), ("Items",)])
        self.assertEqual(self.csv_path.read_bytes(), before)
        self.assertFalse((self.root / ".fccsv-state.json").exists())

    def test_diff_json_keeps_remote_added_column(self) -> None:
        reader = Reader({
            "Items": [["Id", "Name", "Extra"], ["1", "Apple", "a"], ["2", "Pear", "b"]]
        })
        code, output, error = self.invoke(
            reader, "diff", "Items", "--project", str(self.root), "--format", "json",
            "--exit-code"
        )
        payload = json.loads(output)
        self.assertEqual(code, 1)
        self.assertEqual(error, "")
        self.assertEqual(payload["tables"][0]["schema"]["only_remote"], ["Extra"])
        self.assertEqual(payload["tables"][0]["identity"]["columns"], ["Id"])

    def test_legacy_base_is_advisory_not_trusted_diff_context(self) -> None:
        base_dir = self.root / ".fccsv-base"
        base_dir.mkdir()
        (base_dir / "Items.json").write_text(
            json.dumps({
                "headers": ["Id", "Name"], "types": ["number", "text"],
                "rows": [{"Id": "1", "Name": "Apple"}, {"Id": "2", "Name": "Pear"}],
            }),
            encoding="utf-8",
        )
        self.csv_path.write_text("Id,Name\ntext,text\n1,Apple\n2,Pear\n", encoding="utf-8")
        reader = Reader({"Items": [["Id", "Name"], ["1", "Apple"], ["2", "Pear"]]})
        code, output, error = self.invoke(
            reader, "diff", "Items", "--project", str(self.root), "--format", "json",
            "--exit-code"
        )
        self.assertEqual(code, 0)
        self.assertEqual(error, "")
        self.assertFalse(json.loads(output)["tables"][0]["schema"]["local_types_changed_since_base"])

    def test_type_only_change_is_visible_using_v3_base(self) -> None:
        reader = Reader({"Items": [["Id", "Name"], ["1", "Apple"], ["2", "Pear"]]})
        result = apply_table(
            self.root, self.root / "Assets" / "CSV",
            TableMapping("Items", "Items.csv", ("Id",)), reader, "sync",
        )
        self.assertTrue(result.decision.allowed)
        self.csv_path.write_text("Id,Name\ntext,text\n1,Apple\n2,Pear\n", encoding="utf-8")
        code, output, error = self.invoke(
            reader, "diff", "Items", "--project", str(self.root), "--format", "json",
            "--exit-code",
        )
        self.assertEqual(code, 1)
        self.assertEqual(error, "")
        self.assertTrue(json.loads(output)["tables"][0]["schema"]["local_types_changed_since_base"])

    def test_missing_token_is_actionable(self) -> None:
        with patch.object(
            GoogleSheetsValuesReader,
            "from_token",
            side_effect=RuntimeError("Google token not found"),
        ):
            code, output, error = self.invoke(None, "status", "--project", str(self.root))
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertIn("Google token not found", error)


class _Request:
    def __init__(self, result: dict[str, object]) -> None:
        self.result = result

    def execute(self) -> dict[str, object]:
        return self.result


class _Service:
    def __init__(self, names: tuple[str, ...]) -> None:
        self.names = names
        self.batch_ranges: list[list[str]] = []
        self.metadata_reads = 0

    def spreadsheets(self) -> _Service:
        return self

    def get(self, **kwargs: object) -> _Request:
        self.metadata_reads += 1
        return _Request({"sheets": [{"properties": {"title": name}} for name in self.names]})

    def values(self) -> _Service:
        return self

    def batchGet(self, **kwargs: object) -> _Request:
        ranges = kwargs["ranges"]
        assert isinstance(ranges, list)
        self.batch_ranges.append(ranges)
        return _Request({"valueRanges": [{"values": [["Id"], ["1"]]} for _ in ranges]})


class GoogleReaderTests(unittest.TestCase):
    def test_batches_existing_tabs_and_quotes_a1_names(self) -> None:
        names = tuple(f"Tab{i}" for i in range(53)) + ("Boss's Loot",)
        service = _Service(names)
        reader = GoogleSheetsValuesReader(service, "spreadsheet-id")
        result = reader.read_values(names + ("Missing",))
        self.assertEqual(len(result), 54)
        self.assertEqual(len(service.batch_ranges), 2)
        self.assertEqual(service.batch_ranges[1][-1], "'Boss''s Loot'")
        self.assertNotIn("Missing", result)

    def test_repeated_reads_reuse_metadata(self) -> None:
        service = _Service(("Items",))
        reader = GoogleSheetsValuesReader(service, "spreadsheet-id")
        self.assertIn("Items", reader.read_values(("Items",)))
        self.assertIn("Items", reader.read_values(("Items",)))
        self.assertEqual(service.metadata_reads, 1)
        self.assertEqual(len(service.batch_ranges), 2)

    def test_quota_retries_with_visible_wait_then_reports_short_error(self) -> None:
        class RateLimit(Exception):
            def __init__(self) -> None:
                super().__init__("long Google error with request URL")
                self.resp = types.SimpleNamespace(status=429)

        class FailingRequest:
            def __init__(self) -> None:
                self.calls = 0

            def execute(self) -> dict[str, object]:
                self.calls += 1
                raise RateLimit()

        errors = types.ModuleType("googleapiclient.errors")
        errors.HttpError = RateLimit
        request = FailingRequest()
        output = io.StringIO()
        with patch.dict(sys.modules, {
            "googleapiclient": types.ModuleType("googleapiclient"),
            "googleapiclient.errors": errors,
        }), patch("fccsv3.adapters.google_sheets.time.sleep") as sleep, \
                contextlib.redirect_stderr(output):
            with self.assertRaisesRegex(RuntimeError, r"quota exceeded \(HTTP 429\)") as raised:
                _execute(request)
        self.assertEqual(request.calls, 4)
        self.assertEqual(sleep.call_args_list, [call(60), call(60), call(60)])
        self.assertEqual(output.getvalue().count("retrying in 60 seconds"), 3)
        self.assertNotIn("request URL", str(raised.exception))

    def test_quota_retry_succeeds_without_exposing_google_error(self) -> None:
        class RateLimit(Exception):
            def __init__(self) -> None:
                super().__init__("sensitive Google request URL")
                self.resp = types.SimpleNamespace(status=429)

        class OnceLimitedRequest:
            def __init__(self) -> None:
                self.calls = 0

            def execute(self) -> dict[str, object]:
                self.calls += 1
                if self.calls == 1:
                    raise RateLimit()
                return {"valueRanges": []}

        errors = types.ModuleType("googleapiclient.errors")
        errors.HttpError = RateLimit
        request = OnceLimitedRequest()
        output = io.StringIO()
        with patch.dict(sys.modules, {
            "googleapiclient": types.ModuleType("googleapiclient"),
            "googleapiclient.errors": errors,
        }), patch("fccsv3.adapters.google_sheets.time.sleep") as sleep, \
                contextlib.redirect_stderr(output):
            result = _execute(request)
        self.assertEqual(result, {"valueRanges": []})
        self.assertEqual(request.calls, 2)
        sleep.assert_called_once_with(60)
        self.assertIn("retrying in 60 seconds", output.getvalue())
        self.assertNotIn("request URL", output.getvalue())

    def test_formula_preflight_batches_tabs_and_reports_affected_tab(self) -> None:
        class FormulaService(_Service):
            def batchGet(self, **kwargs: object) -> _Request:
                ranges = kwargs["ranges"]
                assert isinstance(ranges, list)
                assert kwargs["valueRenderOption"] == "FORMULA"
                self.batch_ranges.append(ranges)
                return _Request({"valueRanges": [
                    {"values": [["=1+2" if tab == "'Tab52'" else "1"]]}
                    for tab in ranges
                ]})

        names = tuple(f"Tab{index}" for index in range(53))
        service = FormulaService(names)
        gateway = GoogleSheetsGateway(service, "spreadsheet-id")
        blockers = gateway.formula_blockers(names)
        self.assertEqual(tuple(blockers), ("Tab52",))
        self.assertEqual(len(service.batch_ranges), 2)
        self.assertEqual(len(service.batch_ranges[0]), 50)
        self.assertEqual(len(service.batch_ranges[1]), 3)


class _ActionRequest:
    def __init__(self, action: object) -> None:
        self.action = action

    def execute(self) -> dict[str, object]:
        self.action()  # type: ignore[operator]
        return {}


class _WriteService(_Service):
    def __init__(self) -> None:
        super().__init__(("Items",))
        self.grid = [["Id", "Name", "Extra"], ["1", "A", "x"], ["2", "B", "y"]]
        self.calls: list[str] = []

    def get(self, **kwargs: object) -> _Request:
        if kwargs.get("valueRenderOption") == "FORMULA":
            self.calls.append(f"formulas:{kwargs['range']}")
            return _Request({"values": self.grid})
        return super().get(**kwargs)

    def clear(self, **kwargs: object) -> _ActionRequest:
        self.calls.append(f"clear:{kwargs['range']}")
        return _ActionRequest(lambda: self.grid.clear())

    def update(self, **kwargs: object) -> _ActionRequest:
        self.calls.append(f"update:{kwargs['range']}")
        body = kwargs["body"]
        assert isinstance(body, dict)
        values = body["values"]
        assert isinstance(values, list)
        return _ActionRequest(lambda: self.grid.extend(values))


class GoogleWriterTests(unittest.TestCase):
    def test_shorter_write_clears_old_rows_and_columns(self) -> None:
        service = _WriteService()
        gateway = GoogleSheetsGateway(service, "spreadsheet-id")
        gateway.write_values("Items", [["Id", "Name"], ["1", "New"]])
        self.assertEqual(service.grid, [["Id", "Name"], ["1", "New"]])
        self.assertEqual(service.calls, ["formulas:'Items'", "clear:'Items'", "update:'Items'!A1"])

    def test_formula_blocks_before_clearing_tab(self) -> None:
        service = _WriteService()
        service.grid[1][1] = "=1+2"
        before = [row.copy() for row in service.grid]
        gateway = GoogleSheetsGateway(service, "spreadsheet-id")
        with self.assertRaisesRegex(RuntimeError, "Sheet tab contains formulas"):
            gateway.write_values("Items", [["Id", "Name"], ["1", "New"]])
        self.assertEqual(service.grid, before)
        self.assertEqual(service.calls, ["formulas:'Items'"])


if __name__ == "__main__":
    unittest.main()
