"""Sync state: per-table last-sync hashes + drive stamp. JSON, atomic writes.

v1-compat shape: {"tables": {name: {"local": h, "remote": h}},
"modified_time": stamp}. v2 adds "version": 2.
Corrupt state never blocks: it is quarantined to `.corrupt-<ts>` and
fresh state starts (v1 silently dropped it).
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fccsv.domain.snapshot import TableSnapshot


def snapshot_hash(snapshot: TableSnapshot) -> str:
    payload = json.dumps(
        {"h": snapshot.headers, "r": snapshot.rows}, sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class TableHashes:
    local: str = ""
    remote: str = ""


@dataclass
class SyncState:
    tables: dict[str, TableHashes] = field(default_factory=dict)
    modified_time: str = ""
    version: int = 2

    def last(self, name: str) -> TableHashes:
        return self.tables.get(name, TableHashes())


def load_state(state_file: Path) -> SyncState:
    if not state_file.exists():
        return SyncState()
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        quarantine = state_file.with_name(
            state_file.name + f".corrupt-{time.strftime('%Y%m%dT%H%M%S')}"
        )
        try:
            state_file.rename(quarantine)
        except OSError:
            pass
        return SyncState()
    if not isinstance(data, dict):
        return SyncState()
    tables: dict[str, TableHashes] = {}
    raw_tables = data.get("tables", {})
    if isinstance(raw_tables, dict):
        for name, entry in raw_tables.items():
            if isinstance(entry, dict):
                tables[str(name)] = TableHashes(
                    local=str(entry.get("local", "")),
                    remote=str(entry.get("remote", "")),
                )
    modified = data.get("modified_time", "")
    return SyncState(
        tables=tables,
        modified_time=modified if isinstance(modified, str) else "",
    )


def save_state(state_file: Path, state: SyncState) -> None:
    """Atomic write (tmp + rename) so quota-abort or Ctrl-C never corrupts."""
    state_file.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "version": 2,
        "modified_time": state.modified_time,
        "tables": {n: {"local": h.local, "remote": h.remote} for n, h in state.tables.items()},
    }
    tmp = state_file.with_suffix(state_file.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(state_file)


def record_table(state: SyncState, name: str, *, local: str, remote: str) -> None:
    state.tables[name] = TableHashes(local=local, remote=remote)
