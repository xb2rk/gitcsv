"""Commit bookkeeping after a successful apply: record hashes, save BASE.

Also owns the `_fccsv` metadata tab payload so the sheet carries the same
hashes (v1-compat: other clients keep working).
"""

from __future__ import annotations

from pathlib import Path

from fccsv.adapters import base as base_mod
from fccsv.adapters import state as state_mod
from fccsv.adapters.journal import JournalEntry, append_journal
from fccsv.adapters.sheets import SheetsGateway
from fccsv.app.fetch import metadata_snapshot
from fccsv.core.constants import METADATA_TAB
from fccsv.domain.snapshot import TableSnapshot


def commit_push(
    state: state_mod.SyncState,
    base_dir: Path,
    *,
    name: str,
    local_snapshot: TableSnapshot,
    new_remote: TableSnapshot,
    journal: Path | None = None,
    spreadsheet_id: str = "",
) -> None:
    local_hash = state_mod.snapshot_hash(local_snapshot)
    remote_hash = state_mod.snapshot_hash(new_remote)
    state_mod.record_table(state, name, local=local_hash, remote=remote_hash)
    base_for_save = TableSnapshot(
        name=name,
        headers=list(new_remote.headers),
        types=list(local_snapshot.types),
        rows=[dict(r) for r in new_remote.rows],
        key_column=new_remote.key_column,
    )
    base_mod.save_base(base_dir, base_for_save)
    if journal is not None:
        append_journal(
            journal,
            JournalEntry(
                action="push",
                table=name,
                spreadsheet_id=spreadsheet_id,
                rows=len(new_remote.rows),
                local=local_hash,
                remote=remote_hash,
            ),
        )


def commit_pull(
    state: state_mod.SyncState,
    base_dir: Path,
    *,
    name: str,
    merged: TableSnapshot,
    remote_snapshot: TableSnapshot,
    journal: Path | None = None,
    spreadsheet_id: str = "",
) -> None:
    local_hash = state_mod.snapshot_hash(merged)
    remote_hash = state_mod.snapshot_hash(remote_snapshot)
    state_mod.record_table(state, name, local=local_hash, remote=remote_hash)
    base_mod.save_base(base_dir, merged)
    if journal is not None:
        append_journal(
            journal,
            JournalEntry(
                action="pull",
                table=name,
                spreadsheet_id=spreadsheet_id,
                rows=len(merged.rows),
                local=local_hash,
                remote=remote_hash,
            ),
        )


def write_metadata_tab(
    gateway: SheetsGateway, state: state_mod.SyncState, names: list[str]
) -> None:
    hashes = {n: state.last(n).remote for n in names if state.last(n).remote}
    snapshot = metadata_snapshot(hashes)
    assert isinstance(snapshot, TableSnapshot)
    gateway.write_tab(snapshot)


def metadata_tab_name() -> str:
    return METADATA_TAB
