"""Sheets gateway: Protocol + InMemory (tests/offline) + Google (lazy imports).

Sheet holds header + data rows only; the types row stays local and is
re-attached on pull. Tab names are escaped for A1 ranges (v1 did not).
"""

from __future__ import annotations

import time
from typing import Any, Protocol

from fccsv.domain import keys as keys_mod
from fccsv.domain.errors import AuthError, QuotaExceededError, TabMissingError
from fccsv.domain.snapshot import TableSnapshot

_READ_CHUNK = 50


class SheetsGateway(Protocol):
    def list_tabs(self) -> list[str]: ...
    def read_tab(self, tab: str) -> TableSnapshot: ...
    def read_tabs(self, tabs: list[str]) -> dict[str, TableSnapshot]: ...
    def write_tab(self, snapshot: TableSnapshot) -> None: ...
    def write_tabs(self, snapshots: list[TableSnapshot]) -> None: ...
    def ensure_tab(self, tab: str) -> None: ...
    def drive_modified_time(self) -> str | None: ...
    def create_spreadsheet(self, title: str) -> str: ...


def spreadsheet_id_from_url(url_or_id: str) -> str:
    """Accept a full docs URL or a bare id."""
    value = url_or_id.strip()
    marker = "/spreadsheets/d/"
    if marker in value:
        return value.split(marker, 1)[1].split("/")[0].split("?")[0]
    return value


class InMemorySheetsGateway:
    """Test/offline double. Deep-copies on the way in and out."""

    def __init__(self, tabs: dict[str, TableSnapshot] | None = None) -> None:
        self._tabs: dict[str, TableSnapshot] = {}
        for name, snap in (tabs or {}).items():
            self._tabs[name] = TableSnapshot(
                name=name,
                headers=list(snap.headers),
                types=list(snap.types),
                rows=[dict(r) for r in snap.rows],
                key_column=snap.key_column,
            )

    def list_tabs(self) -> list[str]:
        return sorted(self._tabs)

    def read_tab(self, tab: str) -> TableSnapshot:
        if tab not in self._tabs:
            raise TabMissingError(tab)
        snap = self._tabs[tab]
        return TableSnapshot(
            name=tab,
            headers=list(snap.headers),
            types=list(snap.types),
            rows=[dict(r) for r in snap.rows],
            key_column=snap.key_column or keys_mod.infer_key_column(snap.headers, snap.rows),
        )

    def read_tabs(self, tabs: list[str]) -> dict[str, TableSnapshot]:
        return {t: self.read_tab(t) for t in tabs if t in self._tabs}

    def write_tab(self, snapshot: TableSnapshot) -> None:
        self._tabs[snapshot.name] = TableSnapshot(
            name=snapshot.name,
            headers=list(snapshot.headers),
            types=[],
            rows=[dict(r) for r in snapshot.rows],
            key_column=snapshot.key_column,
        )

    def write_tabs(self, snapshots: list[TableSnapshot]) -> None:
        for snap in snapshots:
            self.write_tab(snap)

    def ensure_tab(self, tab: str) -> None:
        if tab not in self._tabs:
            self._tabs[tab] = TableSnapshot(name=tab)

    def drive_modified_time(self) -> str | None:
        return None

    def create_spreadsheet(self, title: str) -> str:
        return "in-memory"


class GoogleSheetsGateway:
    """Live gateway. Google libs imported lazily so `import fccsv` never
    requires them and `--offline`/tests stay dependency-free."""

    def __init__(self, spreadsheet_id: str, credentials: Any) -> None:
        try:
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise AuthError(
                "Google client libraries are not installed. "
                "Run: pip install fccsv[sheets] (or pip install ./tools/fccsv-v2[sheets])"
            ) from exc

        self.spreadsheet_id = spreadsheet_id
        self.service = build("sheets", "v4", credentials=credentials)
        self._drive = None
        self._tabs_cache: list[str] | None = None
        try:
            self._drive = build("drive", "v3", credentials=credentials)
        except Exception:
            self._drive = None

    def _execute(self, request: Any) -> Any:
        from googleapiclient.errors import HttpError

        delay = 1.0
        for attempt in range(4):
            try:
                return request.execute()
            except HttpError as exc:
                status = exc.resp.status if exc.resp is not None else 0
                body = str(exc.content) if exc.content else ""
                if status == 429 or "RATE_LIMIT_EXCEEDED" in body:
                    raise QuotaExceededError from exc
                if status in (500, 502, 503) and attempt < 3:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise

    def list_tabs(self) -> list[str]:
        if self._tabs_cache is not None:
            return list(self._tabs_cache)
        meta = self._execute(self.service.spreadsheets().get(spreadsheetId=self.spreadsheet_id))
        tabs = [s["properties"]["title"] for s in meta.get("sheets", [])]
        self._tabs_cache = tabs
        return list(tabs)

    def read_tab(self, tab: str) -> TableSnapshot:
        from googleapiclient.errors import HttpError

        try:
            result = self._execute(
                self.service.spreadsheets()
                .values()
                .get(spreadsheetId=self.spreadsheet_id, range=keys_mod.escape_tab(tab))
            )
        except HttpError as exc:
            if exc.resp is not None and exc.resp.status == 400:
                raise TabMissingError(tab) from exc
            raise
        values = result.get("values", [])
        if not values:
            return TableSnapshot(name=tab)
        headers = [str(c) for c in values[0]]
        rows = [
            {h: (rec[j] if j < len(rec) else "") for j, h in enumerate(headers)}
            for rec in values[1:]
        ]
        return TableSnapshot(
            name=tab,
            headers=headers,
            rows=rows,
            key_column=keys_mod.infer_key_column(headers, rows),
        )

    def read_tabs(self, tabs: list[str]) -> dict[str, TableSnapshot]:
        """Batched read, chunked. Unknown tabs are skipped (caller checks)."""
        out: dict[str, TableSnapshot] = {}
        existing = set(self.list_tabs())
        for i in range(0, len(tabs), _READ_CHUNK):
            chunk = [t for t in tabs[i : i + _READ_CHUNK] if t in existing]
            if not chunk:
                continue
            result = self._execute(
                self.service.spreadsheets()
                .values()
                .batchGet(
                    spreadsheetId=self.spreadsheet_id,
                    ranges=[keys_mod.escape_tab(t) for t in chunk],
                )
            )
            for tab, vr in zip(chunk, result.get("valueRanges", [])):
                values = vr.get("values", [])
                if not values:
                    out[tab] = TableSnapshot(name=tab)
                    continue
                headers = [str(c) for c in values[0]]
                rows = [
                    {h: (rec[j] if j < len(rec) else "") for j, h in enumerate(headers)}
                    for rec in values[1:]
                ]
                out[tab] = TableSnapshot(
                    name=tab,
                    headers=headers,
                    rows=rows,
                    key_column=keys_mod.infer_key_column(headers, rows),
                )
        return out

    def ensure_tab(self, tab: str) -> None:
        if tab in self.list_tabs():
            return
        self._execute(
            self.service.spreadsheets().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={"requests": [{"addSheet": {"properties": {"title": tab}}}]},
            )
        )
        self._tabs_cache = None

    def write_tabs(self, snapshots: list[TableSnapshot]) -> None:
        for snap in snapshots:
            self.ensure_tab(snap.name)
        body = [
            {
                "range": keys_mod.escape_tab(snap.name),
                "values": [snap.headers]
                + [[r.get(h, "") for h in snap.headers] for r in snap.rows],
            }
            for snap in snapshots
        ]
        self._execute(
            self.service.spreadsheets()
            .values()
            .batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={"valueInputOption": "RAW", "data": body},
            )
        )

    def write_tab(self, snapshot: TableSnapshot) -> None:
        self.write_tabs([snapshot])

    def drive_modified_time(self) -> str | None:
        if self._drive is None:
            return None
        try:
            meta = self._execute(
                self._drive.files().get(fileId=self.spreadsheet_id, fields="modifiedTime")
            )
            return str(meta.get("modifiedTime", ""))
        except Exception:
            return None

    def create_spreadsheet(self, title: str) -> str:
        result = self._execute(
            self.service.spreadsheets().create(body={"properties": {"title": title}})
        )
        return str(result["spreadsheetId"])
