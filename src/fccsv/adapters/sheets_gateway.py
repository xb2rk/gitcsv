"""Sheets access behind a small protocol. Google imports are lazy.

`SheetsGateway` is the seam: production uses `GoogleSheetsGateway`
(extra `pip install fccsv[sheets]`), tests and `--offline` use
`InMemorySheetsGateway`. Sheet tabs hold header row + data rows only;
the FC type row stays local.
"""

from __future__ import annotations

import time
from typing import Protocol

from fccsv.core.logging import get_logger
from fccsv.domain import mapping as mapping_mod
from fccsv.domain.exceptions import QuotaExceededError, TabMissingError
from fccsv.domain.models import TableSnapshot

log = get_logger("fccsv.sheets")


def _http_status(exc: Exception) -> int | None:
    """API status without importing googleapiclient (stays an optional extra)."""
    resp = getattr(exc, "resp", None)
    status = getattr(resp, "status", None)
    return status if isinstance(status, int) else None


def _http_reason(exc: Exception) -> str:
    """Machine-readable error reason, e.g. RATE_LIMIT_EXCEEDED, else ''."""
    import json as _json

    try:
        payload = _json.loads(bytes(getattr(exc, "content", b"") or b"").decode())
        error = payload.get("error", {})
        details = error.get("errors", []) + error.get("details", [])
        return next((str(detail["reason"]) for detail in details if "reason" in detail), "")
    except (AttributeError, TypeError, ValueError):
        return ""


def _snapshot_from_values(tab: str, values: list[list[str]]) -> TableSnapshot:
    """Header row + data rows. Blank rows skipped, short rows padded."""
    if not values:
        return TableSnapshot(name=tab)
    headers = [c.strip() for c in values[0]]
    rows = []
    for raw in values[1:]:
        if all(c == "" for c in raw):
            continue
        padded = list(raw) + [""] * (len(headers) - len(raw))
        rows.append({h: padded[i] for i, h in enumerate(headers)})
    return TableSnapshot(
        name=tab,
        headers=headers,
        types=[],
        rows=rows,
        key_column=mapping_mod.infer_key_column(headers, rows),
    )


def _snapshot_to_values(snapshot: TableSnapshot) -> list[list[str]]:
    return [snapshot.headers] + [
        [row.get(h, "") for h in snapshot.headers] for row in snapshot.rows
    ]


class SheetsGateway(Protocol):
    def list_tabs(self) -> list[str]: ...

    def read_tab(self, tab: str) -> TableSnapshot: ...

    def read_tabs(self, tabs: list[str]) -> dict[str, TableSnapshot]: ...

    def write_tab(self, snapshot: TableSnapshot) -> None: ...

    def write_tabs(self, snapshots: list[TableSnapshot]) -> None: ...

    def ensure_tab(self, tab: str) -> None:
        """Create the tab if missing. No-op when it already exists."""
        ...

    def drive_modified_time(self) -> str | None:
        """Spreadsheet modifiedTime, or None when unavailable (tier skipped)."""
        ...

    def create_spreadsheet(self, title: str) -> str: ...


class InMemorySheetsGateway:
    """Test double. Tabs map to TableSnapshot with header + data rows."""

    def __init__(self, tabs: dict[str, TableSnapshot] | None = None) -> None:
        self.tabs: dict[str, TableSnapshot] = dict(tabs or {})

    def list_tabs(self) -> list[str]:
        return sorted(self.tabs)

    def read_tab(self, tab: str) -> TableSnapshot:
        snap = self.tabs[tab]
        return TableSnapshot(
            name=tab,
            headers=list(snap.headers),
            types=list(snap.types),
            rows=[dict(r) for r in snap.rows],
            key_column=snap.key_column or mapping_mod.infer_key_column(snap.headers, snap.rows),
        )

    def write_tab(self, snapshot: TableSnapshot) -> None:
        self.tabs[snapshot.name] = TableSnapshot(
            name=snapshot.name,
            headers=list(snapshot.headers),
            types=[],
            rows=[dict(r) for r in snapshot.rows],
            key_column=snapshot.key_column,
        )

    def ensure_tab(self, tab: str) -> None:
        self.tabs.setdefault(tab, TableSnapshot(name=tab))

    def read_tabs(self, tabs: list[str]) -> dict[str, TableSnapshot]:
        return {tab: self.read_tab(tab) for tab in tabs}

    def write_tabs(self, snapshots: list[TableSnapshot]) -> None:
        for snap in snapshots:
            self.write_tab(snap)

    def drive_modified_time(self) -> str | None:
        return None  # No Drive API offline; tier skipped.

    def create_spreadsheet(self, title: str) -> str:
        return "in-memory"


def spreadsheet_id_from_url(url_or_id: str) -> str:
    """Accept a full docs.google.com URL or a bare spreadsheet id."""
    text = url_or_id.strip()
    if "/spreadsheets/d/" in text:
        part = text.split("/spreadsheets/d/", 1)[1]
        return part.split("/")[0].split("?")[0]
    return text


class GoogleSheetsGateway:
    """Production gateway. Requires the `sheets` extra."""

    _MAX_ATTEMPTS = 4

    def __init__(self, spreadsheet_id: str, credentials=None) -> None:
        try:
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise ImportError("Missing Sheets extra: pip install 'fccsv[sheets]'") from exc
        if credentials is None:
            raise ValueError("credentials are required")
        self._service = build("sheets", "v4", credentials=credentials)
        self._drive = None  # Built lazily; Drive tier is best-effort.
        self._credentials = credentials
        self._spreadsheet_id = spreadsheet_id
        self._tabs_cache: list[str] | None = None

    def _execute(self, request, *, what: str):
        """Retry transient 5xx with backoff. Quota 429s fail fast: progress
        is saved per table, so re-running after ~60s resumes cleanly."""
        last: Exception | None = None
        for attempt in range(1, self._MAX_ATTEMPTS + 1):
            try:
                return request.execute()
            except Exception as exc:  # googleapiclient HttpError subclasses Exception
                if _http_status(exc) == 429 or _http_reason(exc) == "RATE_LIMIT_EXCEEDED":
                    raise QuotaExceededError from exc
                if not self._retryable(exc) or attempt == self._MAX_ATTEMPTS:
                    raise
                last = exc
                delay = 2 ** (attempt - 1)
                log.warning("%s: transient error, retrying in %ss", what, delay)
                time.sleep(delay)
        raise last  # pragma: no cover - loop always returns or raises

    @staticmethod
    def _retryable(exc: Exception) -> bool:
        return _http_status(exc) in (500, 502, 503)

    def invalidate_tabs_cache(self) -> None:
        self._tabs_cache = None

    def list_tabs(self) -> list[str]:
        if self._tabs_cache is None:
            meta = self._execute(
                self._service.spreadsheets().get(spreadsheetId=self._spreadsheet_id),
                what="list_tabs",
            )
            self._tabs_cache = [s["properties"]["title"] for s in meta.get("sheets", [])]
        return list(self._tabs_cache)

    def read_tab(self, tab: str) -> TableSnapshot:
        try:
            result = self._execute(
                self._service.spreadsheets()
                .values()
                .get(spreadsheetId=self._spreadsheet_id, range=f"'{tab}'"),
                what=f"read_tab {tab}",
            )
        except Exception as exc:
            if _http_status(exc) == 400:
                # Nonexistent tab: the API 400s on unparseable ranges.
                raise TabMissingError(tab) from exc
            raise
        return _snapshot_from_values(tab, result.get("values", []))

    def read_tabs(self, tabs: list[str]) -> dict[str, TableSnapshot]:
        """One batchGet for existing tabs only; invalid ranges would 400 it."""
        existing = set(self.list_tabs())
        readable = [tab for tab in tabs if tab in existing]
        if not readable:
            return {}
        result = self._execute(
            self._service.spreadsheets()
            .values()
            .batchGet(
                spreadsheetId=self._spreadsheet_id,
                ranges=[f"'{tab}'" for tab in readable],
            ),
            what=f"read_tabs {len(readable)}",
        )
        out: dict[str, TableSnapshot] = {}
        for tab, value_range in zip(readable, result.get("valueRanges", [])):
            out[tab] = _snapshot_from_values(tab, value_range.get("values", []))
        return out

    def write_tabs(self, snapshots: list[TableSnapshot]) -> None:
        """One batchUpdate for all tabs. Creates missing tabs first."""
        if not snapshots:
            return
        existing = set(self.list_tabs())
        self._add_tabs([snap.name for snap in snapshots if snap.name not in existing])
        self._execute(
            self._service.spreadsheets()
            .values()
            .batchUpdate(
                spreadsheetId=self._spreadsheet_id,
                body={
                    "valueInputOption": "RAW",
                    "data": [
                        {"range": f"'{snap.name}'!A1", "values": _snapshot_to_values(snap)}
                        for snap in snapshots
                    ],
                },
            ),
            what=f"write_tabs {len(snapshots)}",
        )

    def write_tab(self, snapshot: TableSnapshot) -> None:
        self.write_tabs([snapshot])

    def ensure_tab(self, tab: str) -> None:
        if tab in self.list_tabs():
            return
        self._add_tabs([tab])

    def _add_tabs(self, tabs: list[str]) -> None:
        """Create tabs in one structural update, then invalidate the cache."""
        names = list(dict.fromkeys(tabs))
        if not names:
            return
        self._execute(
            self._service.spreadsheets().batchUpdate(
                spreadsheetId=self._spreadsheet_id,
                body={"requests": [{"addSheet": {"properties": {"title": tab}}} for tab in names]},
            ),
            what=f"ensure_tabs {len(names)}",
        )
        self.invalidate_tabs_cache()

    def drive_modified_time(self) -> str | None:
        """Spreadsheet modifiedTime via Drive. None when unavailable (no
        scope, no token consent yet, API disabled): tiers fall back."""
        try:
            if self._drive is None:
                from googleapiclient.discovery import build

                self._drive = build("drive", "v3", credentials=self._credentials)
            meta = (
                self._drive.files()
                .get(fileId=self._spreadsheet_id, fields="modifiedTime")
                .execute()
            )
            stamp = meta.get("modifiedTime")
            return str(stamp) if stamp else None
        except Exception as exc:  # noqa: BLE001 - Drive metadata is best-effort.
            log.debug("drive tier skipped: %s", exc)
            return None

    def create_spreadsheet(self, title: str) -> str:
        created = self._execute(
            self._service.spreadsheets().create(body={"properties": {"title": title}}),
            what="create_spreadsheet",
        )
        return str(created["spreadsheetId"])
