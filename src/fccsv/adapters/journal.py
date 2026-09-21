"""Append-only sync journal: local history for `log`.

JSONL at the project root. Entries carry the spreadsheet id so history
stays attributable after `remote set` instead of mixing lineages.
Corrupt lines are skipped, never fatal.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class JournalEntry:
    ts: str = ""
    action: str = ""  # push | pull
    table: str = ""
    spreadsheet_id: str = ""
    rows: int = 0
    local: str = ""
    remote: str = ""


def utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def append_journal(path: Path, entry: JournalEntry) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not entry.ts:
        entry.ts = utcnow()
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")


def read_journal(
    path: Path,
    *,
    table: str | None = None,
    spreadsheet_id: str = "",
    limit: int | None = None,
) -> list[JournalEntry]:
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    entries: list[JournalEntry] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        entry = JournalEntry(
            ts=str(data.get("ts", "")),
            action=str(data.get("action", "")),
            table=str(data.get("table", "")),
            spreadsheet_id=str(data.get("spreadsheet_id", "")),
            rows=int(data.get("rows", 0) or 0),
            local=str(data.get("local", "")),
            remote=str(data.get("remote", "")),
        )
        if table is not None and entry.table != table:
            continue
        if spreadsheet_id and entry.spreadsheet_id != spreadsheet_id:
            continue
        entries.append(entry)
    if limit is not None:
        entries = entries[max(0, len(entries) - limit) :]
    return entries
