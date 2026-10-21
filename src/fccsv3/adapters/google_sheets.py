"""Read Sheet values in batches and replace complete table tabs on write."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fccsv3.adapters.oauth import load_token


def _a1_tab(name: str) -> str:
    return "'" + name.replace("'", "''") + "'"


def _execute(request: Any) -> dict[str, Any]:
    for attempt in range(4):
        try:
            result: dict[str, Any] = request.execute()
            return result
        except Exception as error:
            try:
                from googleapiclient.errors import HttpError
            except ImportError:
                raise RuntimeError(f"Google Sheets request failed: {error}") from error
            if not isinstance(error, HttpError):
                raise RuntimeError(f"Google Sheets request failed: {error}") from error
            code = error.resp.status if error.resp is not None else 0
            if code not in (429, 500, 502, 503) or attempt == 3:
                raise RuntimeError(f"Google Sheets request failed (HTTP {code}): {error}") from error
            time.sleep(2**attempt)
    raise AssertionError("retry loop did not return or raise")


class GoogleSheetsValuesReader:
    """One metadata read plus chunked values reads for requested existing tabs."""

    def __init__(self, service: Any, spreadsheet_id: str) -> None:
        self.service = service
        self.spreadsheet_id = spreadsheet_id

    @classmethod
    def from_token(cls, spreadsheet_id: str, token_path: Path) -> GoogleSheetsValuesReader:
        try:
            from googleapiclient.discovery import build
        except ImportError as error:
            raise RuntimeError("Install Google support: pip install 'fccsv3[sheets]'.") from error
        credentials = load_token(token_path)
        return cls(build("sheets", "v4", credentials=credentials), spreadsheet_id)

    def read_values(self, tabs: tuple[str, ...]) -> dict[str, list[list[str]]]:
        if not tabs:
            return {}
        sheets = self.service.spreadsheets()
        metadata = _execute(
            sheets.get(spreadsheetId=self.spreadsheet_id, fields="sheets.properties.title")
        )
        available = {
            str(sheet["properties"]["title"])
            for sheet in metadata.get("sheets", [])
            if "properties" in sheet and "title" in sheet["properties"]
        }
        result: dict[str, list[list[str]]] = {}
        for offset in range(0, len(tabs), 50):
            chunk = tuple(tab for tab in tabs[offset : offset + 50] if tab in available)
            if not chunk:
                continue
            response = _execute(
                sheets.values().batchGet(
                    spreadsheetId=self.spreadsheet_id,
                    ranges=[_a1_tab(tab) for tab in chunk],
                )
            )
            ranges = response.get("valueRanges", [])
            if len(ranges) != len(chunk):
                raise RuntimeError("Google Sheets returned fewer ranges than requested.")
            for tab, value_range in zip(chunk, ranges, strict=True):
                result[tab] = [list(map(str, row)) for row in value_range.get("values", [])]
        return result


class GoogleSheetsGateway(GoogleSheetsValuesReader):
    """Replace all values in a table tab, including stale trailing cells."""

    def formula_blockers(self, tabs: tuple[str, ...]) -> dict[str, str]:
        """Check planned remote writes in batches before any table is written."""

        blockers: dict[str, str] = {}
        for offset in range(0, len(tabs), 50):
            chunk = tabs[offset : offset + 50]
            response = _execute(
                self.service.spreadsheets().values().batchGet(
                    spreadsheetId=self.spreadsheet_id,
                    ranges=[_a1_tab(tab) for tab in chunk],
                    valueRenderOption="FORMULA",
                )
            )
            ranges = response.get("valueRanges", [])
            if len(ranges) != len(chunk):
                raise RuntimeError("Google Sheets returned fewer formula ranges than requested.")
            for tab, value_range in zip(chunk, ranges, strict=True):
                if any(
                    isinstance(cell, str) and cell.startswith("=")
                    for row in value_range.get("values", []) for cell in row
                ):
                    blockers[tab] = (
                        f"Sheet tab contains formulas: {tab}; "
                        "v3 cannot safely replace the whole tab"
                    )
        return blockers

    def formula_blocker(self, tab: str) -> str | None:
        """A values.clear call would erase formulas anywhere in this tab."""

        formula_values = _execute(
            self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=_a1_tab(tab),
                valueRenderOption="FORMULA",
            )
        ).get("values", [])
        if any(
            isinstance(cell, str) and cell.startswith("=")
            for row in formula_values for cell in row
        ):
            return f"Sheet tab contains formulas: {tab}; v3 cannot safely replace the whole tab"
        return None

    def write_values(self, tab: str, values: list[list[str]]) -> None:
        sheets = self.service.spreadsheets()
        metadata = _execute(
            sheets.get(spreadsheetId=self.spreadsheet_id, fields="sheets.properties.title")
        )
        available = {
            str(sheet["properties"]["title"])
            for sheet in metadata.get("sheets", [])
            if "properties" in sheet and "title" in sheet["properties"]
        }
        if tab not in available:
            raise RuntimeError(f"Sheet tab does not exist: {tab}")
        blocker = self.formula_blocker(tab)
        if blocker:
            raise RuntimeError(blocker)
        # A table tab is owned by fccsv. Clearing values preserves formatting.
        _execute(
            sheets.values().clear(
                spreadsheetId=self.spreadsheet_id, range=_a1_tab(tab), body={}
            )
        )
        if values:
            _execute(
                sheets.values().update(
                    spreadsheetId=self.spreadsheet_id,
                    range=f"{_a1_tab(tab)}!A1",
                    valueInputOption="RAW",
                    body={"values": values},
                )
            )
