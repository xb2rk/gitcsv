"""Read-only `fetch`: has the sheet moved since last sync?

Tiered for quota: Drive `modifiedTime` (0 reads) -> metadata-proven change
(0 extra reads) -> ONE batched `read_tabs` + hash compare. The last tier is
mandatory: designer edits never update the `_fccsv` tab, so a metadata
match proves nothing about the data. Never touches `Assets/CSV`. Powers
`status --fetch` and the `behind/ahead/diverged` report.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fccsv.adapters import state as state_mod
from fccsv.adapters.sheets import SheetsGateway
from fccsv.core.config import TableMapping
from fccsv.core.constants import METADATA_TAB


@dataclass
class TableFetch:
    tab: str
    remote_hash: str = ""
    changed: bool = False
    missing: bool = False


@dataclass
class FetchResult:
    drive_stamp: str | None = None
    stamp_match: bool = False
    tables: list[TableFetch] = field(default_factory=list)

    @property
    def any_changed(self) -> bool:
        return any(t.changed for t in self.tables)


def read_metadata_hashes(gateway: SheetsGateway) -> dict[str, str]:
    """Parse the `_fccsv` tab ({tab, hash}); corrupt/foreign shape -> {}."""
    try:
        snap = gateway.read_tab(METADATA_TAB)
    except Exception:
        return {}
    if [h.strip().lower() for h in snap.headers[:2]] != ["tab", "hash"]:
        return {}
    return {row.get("tab", ""): row.get("hash", "") for row in snap.rows if row.get("tab", "")}


def metadata_snapshot(hashes: dict[str, str]) -> object:
    """Build the `_fccsv` value payload (duck-typed to avoid an import)."""
    from fccsv.domain.snapshot import TableSnapshot

    rows = [{"tab": tab, "hash": h} for tab, h in sorted(hashes.items())]
    return TableSnapshot(name=METADATA_TAB, headers=["tab", "hash"], rows=rows)


def fetch(
    mappings: list[TableMapping],
    gateway: SheetsGateway,
    state: state_mod.SyncState,
) -> FetchResult:
    stamp = gateway.drive_modified_time()
    if stamp and state.modified_time and stamp == state.modified_time:
        return FetchResult(drive_stamp=stamp, stamp_match=True)
    meta = read_metadata_hashes(gateway)
    proven = {m.tab for m in mappings if meta.get(m.tab, "") not in ("", state.last(m.tab).remote)}
    try:
        snapshots = gateway.read_tabs([m.tab for m in mappings])
    except Exception:
        snapshots = {}
    tables = []
    for m in mappings:
        if m.tab in proven:
            tables.append(TableFetch(tab=m.tab, remote_hash=meta[m.tab], changed=True))
            continue
        snap = snapshots.get(m.tab)
        if snap is None:
            tables.append(TableFetch(tab=m.tab, missing=True, changed=True))
            continue
        h = state_mod.snapshot_hash(snap)
        tables.append(TableFetch(tab=m.tab, remote_hash=h, changed=h != state.last(m.tab).remote))
    return FetchResult(drive_stamp=stamp, tables=tables)
