"""Google Sheets API client with batch reads, formula checks, and retry backoff."""

from __future__ import annotations

import random
import socket
import time
from typing import Any, Iterable, Protocol

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


def _a1_tab(name: str) -> str:
    """Escape and wrap sheet tab name in single quotes for Google Sheets A1 notation."""
    return "'" + name.replace("'", "''") + "'"


class SheetsGateway(Protocol):
    """Protocol for reading and writing Google Sheet tabular values."""

    def list_tabs(self) -> list[str]: ...

    def read_values(self, tabs: tuple[str, ...]) -> dict[str, list[list[str]]]: ...

    def write_values(self, tab: str, values: list[list[str]]) -> None: ...

    def batch_write_values(self, data: dict[str, list[list[str]]]) -> None: ...

    def formula_blockers(self, tabs: tuple[str, ...]) -> dict[str, str]: ...

    def ensure_tab_exists(self, tab: str) -> None: ...


class GoogleSheetsClient:
    """Production Google Sheets API client with batch operations and retry logic."""

    def __init__(self, spreadsheet_id: str, credentials: Any) -> None:
        self.spreadsheet_id = spreadsheet_id
        self.service = build("sheets", "v4", credentials=credentials, cache_discovery=False)

    def _execute_with_retry(self, request_fn: Any, max_retries: int = 4) -> Any:
        """Execute Google API request with exponential backoff and jitter on rate limits (429) and network timeouts."""
        delay = 1.5
        for attempt in range(max_retries):
            try:
                return request_fn().execute()
            except HttpError as err:
                status_code = err.resp.status if hasattr(err, "resp") else None
                if status_code in (429, 500, 502, 503) and attempt < max_retries - 1:
                    jitter = random.uniform(0.5, 1.5)
                    time.sleep(delay * jitter)
                    delay *= 2.0
                    continue
                raise
            except (TimeoutError, socket.timeout, OSError) as err:
                if attempt < max_retries - 1:
                    jitter = random.uniform(0.5, 1.5)
                    time.sleep(delay * jitter)
                    delay *= 2.0
                    continue
                raise

    def list_tabs(self) -> list[str]:
        """Fetch all visible sheet tab names in the spreadsheet."""
        metadata = self._execute_with_retry(
            lambda: self.service.spreadsheets().get(
                spreadsheetId=self.spreadsheet_id,
                fields="sheets.properties.title",
            )
        )
        tabs: list[str] = []
        for sheet in metadata.get("sheets", []):
            title = sheet.get("properties", {}).get("title")
            if title:
                tabs.append(title)
        return tabs

    def read_values(self, tabs: tuple[str, ...]) -> dict[str, list[list[str]]]:
        """Batch-read display values from multiple sheet tabs.

        Only queries tabs that actually exist on the remote spreadsheet to avoid
        HttpError 400 'Unable to parse range'.
        """
        if not tabs:
            return {}

        available = set(self.list_tabs())
        existing = [t for t in tabs if t in available]
        if not existing:
            return {}

        results: dict[str, list[list[str]]] = {}
        chunk_size = 50

        for i in range(0, len(existing), chunk_size):
            chunk = tuple(existing[i : i + chunk_size])
            ranges = [_a1_tab(tab) for tab in chunk]

            response = self._execute_with_retry(
                lambda: self.service.spreadsheets().values().batchGet(
                    spreadsheetId=self.spreadsheet_id,
                    ranges=ranges,
                    valueRenderOption="FORMATTED_VALUE",
                )
            )

            value_ranges = response.get("valueRanges", [])
            for tab, value_range in zip(chunk, value_ranges, strict=True):
                raw_matrix = value_range.get("values", [])
                results[tab] = [[str(cell) for cell in row] for row in raw_matrix]

        return results

    def formula_blockers(self, tabs: tuple[str, ...]) -> dict[str, str]:
        """Scan specified tabs and return any tabs containing formulas that would be overwritten."""
        if not tabs:
            return {}

        available = set(self.list_tabs())
        existing = [t for t in tabs if t in available]
        if not existing:
            return {}

        blockers: dict[str, str] = {}
        chunk_size = 50

        for i in range(0, len(existing), chunk_size):
            chunk = tuple(existing[i : i + chunk_size])
            ranges = [_a1_tab(tab) for tab in chunk]

            response = self._execute_with_retry(
                lambda: self.service.spreadsheets().values().batchGet(
                    spreadsheetId=self.spreadsheet_id,
                    ranges=ranges,
                    valueRenderOption="FORMULA",
                )
            )

            value_ranges = response.get("valueRanges", [])
            for tab, value_range in zip(chunk, value_ranges, strict=True):
                for row in value_range.get("values", []):
                    for cell in row:
                        if isinstance(cell, str) and cell.startswith("="):
                            blockers[tab] = (
                                f"Sheet tab '{tab}' contains formulas (e.g. {cell[:20]!r}); "
                                "gitcsv will not overwrite formula tabs."
                            )
                            break
                    if tab in blockers:
                        break

        return blockers

    def ensure_tabs_exist(self, tabs: Iterable[str]) -> None:
        """Create tabs in spreadsheet if they do not exist, in a single batch API call."""
        target_tabs = list(dict.fromkeys(tabs))
        if not target_tabs:
            return

        existing = set(self.list_tabs())
        missing = [t for t in target_tabs if t not in existing]
        if not missing:
            return

        requests = [{"addSheet": {"properties": {"title": t}}} for t in missing]
        self._execute_with_retry(
            lambda: self.service.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={"requests": requests},
            )
        )

    def ensure_tab_exists(self, tab: str) -> None:
        """Create tab in spreadsheet if it does not exist."""
        self.ensure_tabs_exist((tab,))

    def write_values(self, tab: str, values: list[list[str]]) -> None:
        """Clear existing values and write new values to the sheet tab."""
        self.ensure_tab_exists(tab)

        quoted_tab = _a1_tab(tab)

        # 1. Clear old values in the tab
        self._execute_with_retry(
            lambda: self.service.spreadsheets().values().clear(
                spreadsheetId=self.spreadsheet_id,
                range=quoted_tab,
                body={},
            )
        )

        if not values:
            return

        # 2. Update with new matrix
        body = {"values": values}
        self._execute_with_retry(
            lambda: self.service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=f"{quoted_tab}!A1",
                valueInputOption="RAW",
                body=body,
            )
        )

    def batch_write_values(self, data: dict[str, list[list[str]]]) -> None:
        """Clear and update multiple sheet tabs in 2 bulk requests to conserve quota."""
        if not data:
            return

        # 1. Ensure all target tabs exist beforehand in 1 batch call
        self.ensure_tabs_exist(list(data.keys()))

        # 2. Batch clear all specified tabs in a single API call
        clear_ranges = [_a1_tab(tab) for tab in data]
        self._execute_with_retry(
            lambda: self.service.spreadsheets().values().batchClear(
                spreadsheetId=self.spreadsheet_id,
                body={"ranges": clear_ranges},
            )
        )

        # 3. Batch update all specified tabs in a single API call
        update_data = [
            {"range": f"{_a1_tab(tab)}!A1", "values": values}
            for tab, values in data.items()
            if values
        ]
        if update_data:
            self._execute_with_retry(
                lambda: self.service.spreadsheets().values().batchUpdate(
                    spreadsheetId=self.spreadsheet_id,
                    body={"valueInputOption": "RAW", "data": update_data},
                )
            )


class FakeSheetsGateway:
    """In-memory mock Google Sheets gateway for unit testing."""

    def __init__(self, initial_tables: dict[str, list[list[str]]] | None = None) -> None:
        self.tables: dict[str, list[list[str]]] = {
            tab: [row[:] for row in rows]
            for tab, rows in (initial_tables or {}).items()
        }
        self.formula_tabs: set[str] = set()
        self.write_calls: list[str] = []

    def list_tabs(self) -> list[str]:
        return list(self.tables.keys())

    def read_values(self, tabs: tuple[str, ...]) -> dict[str, list[list[str]]]:
        return {
            tab: [row[:] for row in self.tables.get(tab, [])]
            for tab in tabs
            if tab in self.tables
        }

    def formula_blockers(self, tabs: tuple[str, ...]) -> dict[str, str]:
        return {
            tab: f"Sheet tab '{tab}' contains formulas"
            for tab in tabs
            if tab in self.formula_tabs and tab in self.tables
        }

    def ensure_tab_exists(self, tab: str) -> None:
        if tab not in self.tables:
            self.tables[tab] = []

    def ensure_tabs_exist(self, tabs: Iterable[str]) -> None:
        for tab in tabs:
            self.ensure_tab_exists(tab)

    def write_values(self, tab: str, values: list[list[str]]) -> None:
        self.ensure_tab_exists(tab)
        self.write_calls.append(tab)
        self.tables[tab] = [row[:] for row in values]

    def batch_write_values(self, data: dict[str, list[list[str]]]) -> None:
        self.ensure_tabs_exist(list(data.keys()))
        for tab, values in data.items():
            self.write_values(tab, values)
