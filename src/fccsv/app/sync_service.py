"""Push/pull/status orchestration. Coordinates adapters + domain, owns no parsing.

Safety model (FC loaders skip malformed rows and read columns positionally):
data edits sync freely, schema edits block without --allow-schema-change,
validation failures block always, and both-sides-changed conflicts block
without --force. Pulls back up the local CSV first.

Quota model (Sheets allows ~60 reads/min): status/push/pull minimize remote
reads in tiers — Drive modifiedTime (0 Sheets reads) → `_fccsv` metadata tab
(1 read) → batched reads of changed tabs only. Local CSV reads are free.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fccsv.adapters import backup_store, state_store
from fccsv.adapters.local_store import read_csv_table, write_csv_table
from fccsv.core.config import FccsvConfig
from fccsv.core.logging import get_logger
from fccsv.domain import mapping as mapping_mod
from fccsv.domain.diff_merge import diff_rows, diff_schema, translate_snapshot
from fccsv.domain.exceptions import ConflictError, FccsvError, TabMissingError
from fccsv.domain.models import TableSnapshot, TableStatus
from fccsv.domain.schema_guard import ensure_schema_compatible
from fccsv.domain.validate import ensure_valid, validate_snapshot

log = get_logger("fccsv.sync")

# Reserved tab holding {tab: content-hash} rows so runs can skip unchanged
# tables with a single read. Protected by convention; designers must not edit.
METADATA_TAB = "_fccsv"


@dataclass
class SyncPaths:
    project_root: Path
    csv_dir: Path
    state_path: Path
    backup_dir: Path


def _paths(config: FccsvConfig, project_root: Path) -> SyncPaths:
    csv_dir = config.csv_dir_path(project_root)
    state_path = project_root / config.state_file
    return SyncPaths(
        project_root=project_root,
        csv_dir=csv_dir,
        state_path=state_path,
        backup_dir=project_root / "tools" / "fccsv" / "backups",
    )


def _local_snapshot(paths: SyncPaths, csv_file: str) -> TableSnapshot | None:
    csv_path = paths.csv_dir / csv_file
    if not csv_path.exists():
        return None
    return read_csv_table(csv_path)


def _resolve_key(table, local: TableSnapshot | None) -> str:
    """Mapping key wins; else uniqueness-tested inference from local rows."""
    if table.key_column:
        return table.key_column
    if local is not None:
        return mapping_mod.infer_key_column(local.headers, local.rows)
    return ""


def _remote_snapshot(gateway, tab: str, column_map: dict, key_column: str) -> TableSnapshot | None:
    try:
        raw = gateway.read_tab(tab)
    except (KeyError, ValueError, TabMissingError):
        return None
    snap = translate_snapshot(raw, column_map, key_column)
    if not snap.key_column:
        snap.key_column = key_column or mapping_mod.infer_key_column(snap.headers)
    return snap


def _read_metadata(gateway) -> dict[str, str]:
    """Per-tab remote hashes from the `_fccsv` tab. {} when absent/unreadable."""
    try:
        raw = gateway.read_tab(METADATA_TAB)
    except (KeyError, ValueError, TabMissingError):
        return {}
    return _metadata_hashes(raw)


def _metadata_hashes(raw: TableSnapshot | None) -> dict[str, str]:
    """Parse a metadata snapshot; malformed or missing metadata is ignored."""
    if raw is None:
        return {}
    if raw.headers[:2] != ["tab", "hash"]:
        return {}
    return {r.get("tab", ""): r.get("hash", "") for r in raw.rows if r.get("tab")}


def _seed_metadata(recorded_tables: dict) -> dict[str, str]:
    """Preserve state-backed hashes when `_fccsv` is absent or incomplete."""
    return {
        tab: entry["remote"]
        for tab, entry in recorded_tables.items()
        if isinstance(entry, dict) and isinstance(entry.get("remote"), str)
    }


def _metadata_snapshot(hashes: dict[str, str]) -> TableSnapshot:
    rows = [{"tab": tab, "hash": h} for tab, h in sorted(hashes.items())]
    return TableSnapshot(
        name=METADATA_TAB,
        headers=["tab", "hash"],
        types=["string", "string"],
        rows=rows,
        key_column="tab",
    )


def _drive_stamp(gateway) -> str | None:
    try:
        return gateway.drive_modified_time()
    except Exception as exc:  # noqa: BLE001 - this optimization must never block sync.
        log.debug("Drive timestamp tier skipped: %s", exc)
        return None


def _last_hashes(paths: SyncPaths, tab: str) -> tuple[str | None, str | None]:
    entry = state_store.load_state(paths.state_path).get("tables", {}).get(tab, {})
    return entry.get("local"), entry.get("remote")


def _status_from_state(
    tab: str, local: TableSnapshot | None, recorded: dict | None
) -> TableStatus | None:
    """Fast-path status when remote provably equals the recorded sync."""
    if local is None:
        return TableStatus(name=tab, remote_only=True) if recorded else None
    if recorded is None:
        return TableStatus(name=tab, local_only=True)
    if state_store.snapshot_hash(local.headers, local.rows) != recorded.get("local"):
        return None  # Local moved: needs a real diff.
    return TableStatus(name=tab, validation_issues=validate_snapshot(local))


def _is_conflict(
    local: TableSnapshot,
    remote: TableSnapshot,
    last_local: str | None,
    last_remote: str | None,
) -> bool:
    """Both sides moved since the last recorded sync."""
    if last_local is None or last_remote is None:
        return False
    local_hash = state_store.snapshot_hash(local.headers, local.rows)
    remote_hash = state_store.snapshot_hash(remote.headers, remote.rows)
    return local_hash != last_local and remote_hash != last_remote


def collect_status(config: FccsvConfig, project_root: Path, gateway) -> list[TableStatus]:
    """Drive fast path, otherwise one batched sheet read. Read-only.

    `_fccsv` is maintained by fccsv, not by direct edits in Google Sheets, so
    it cannot safely prove a tab unchanged when Drive metadata is unavailable.
    """
    paths = _paths(config, project_root)
    locals_: dict[str, TableSnapshot | None] = {}
    keys: dict[str, str] = {}
    for table in config.tables:
        local = _local_snapshot(paths, table.csv)
        key = _resolve_key(table, local)
        if local is not None:
            local.key_column = key
        locals_[table.tab] = local
        keys[table.tab] = key
    state = state_store.load_state(paths.state_path)
    recorded_tables = state.get("tables", {})

    stamp = _drive_stamp(gateway)
    if stamp and state.get("modified_time") == stamp:
        log.debug("status fast path: spreadsheet unchanged since last sync")
        fast = [
            _status_from_state(table.tab, locals_[table.tab], recorded_tables.get(table.tab))
            for table in config.tables
        ]
        if all(st is not None for st in fast):
            return [st for st in fast if st is not None]
        # A local move or unrecorded table voids the fast path: diff for real.

    existing = set(gateway.list_tabs())
    pending: list[str] = []
    by_tab: dict[str, TableStatus] = {}
    for table in config.tables:
        local = locals_[table.tab]
        if local is None:
            # A mapped local file was removed. It must not enter the batch-read
            # path, which compares a remote snapshot to a local one.
            by_tab[table.tab] = TableStatus(name=table.tab, remote_only=True)
            continue
        if table.tab not in existing:
            by_tab[table.tab] = TableStatus(name=table.tab, local_only=True)
            continue
        pending.append(table.tab)

    remotes: dict[str, TableSnapshot] = gateway.read_tabs(pending) if pending else {}
    for tab in pending:
        table = config.table_by_tab(tab)
        assert table is not None
        local = locals_[tab]
        assert local is not None
        remote = translate_snapshot(remotes[tab], table.column_map, keys[tab])
        if not remote.key_column:
            remote.key_column = keys[tab] or mapping_mod.infer_key_column(
                remote.headers, remote.rows
            )
        last = recorded_tables.get(tab) or {}
        by_tab[tab] = TableStatus(
            name=tab,
            schema_diff=diff_schema(local, remote),
            row_diff=diff_rows(local, remote),
            conflict=_is_conflict(local, remote, last.get("local"), last.get("remote")),
            validation_issues=validate_snapshot(local),
        )
    return [by_tab[table.tab] for table in config.tables]


def _to_sheet_snapshot(local: TableSnapshot, tab: str) -> TableSnapshot:
    # Sheets keep header + data only; the FC type row stays local.
    return TableSnapshot(
        name=tab,
        headers=list(local.headers),
        types=[],
        rows=[dict(r) for r in local.rows],
        key_column=local.key_column,
    )


def _to_local_snapshot(
    remote: TableSnapshot, local_types: list[str], csv_stem: str
) -> TableSnapshot:
    # A new local file inherits the sheet header as its type row placeholder;
    # designers fill real types before the next push validates them.
    return TableSnapshot(
        name=csv_stem,
        headers=list(remote.headers),
        types=list(local_types) if local_types else list(remote.headers),
        rows=[dict(r) for r in remote.rows],
        key_column=remote.key_column,
    )


def _check_conflict(paths: SyncPaths, tab: str, local, remote, *, force: str | None) -> None:
    if force in ("push", "pull"):
        return
    last_local, last_remote = _last_hashes(paths, tab)
    if (
        local is not None
        and remote is not None
        and _is_conflict(local, remote, last_local, last_remote)
    ):
        raise ConflictError(tab)


def push_table(
    config: FccsvConfig,
    project_root: Path,
    gateway,
    tab: str,
    *,
    allow_schema_change: bool = False,
    dry_run: bool = False,
    force: str | None = None,
) -> str:
    paths = _paths(config, project_root)
    table = config.table_by_tab(tab)
    if table is None:
        raise ValueError(f"No mapping for tab '{tab}'. Run `fccsv init` first.")
    local = _local_snapshot(paths, table.csv)
    if local is None:
        raise ValueError(f"Local CSV missing: {table.csv}")
    local.key_column = _resolve_key(table, local)
    ensure_valid(local)
    remote = _remote_snapshot(gateway, table.tab, table.column_map, local.key_column)
    if remote is not None:
        _check_conflict(paths, table.tab, local, remote, force=force)
        ensure_schema_compatible(local, remote, allow_schema_change=allow_schema_change)
    if dry_run:
        return f"dry-run: would push {table.csv} -> {table.tab} ({len(local.rows)} rows)"
    state = state_store.load_state(paths.state_path)
    metadata = _seed_metadata(state.get("tables", {}))
    metadata.update(_read_metadata(gateway))
    digest = state_store.snapshot_hash(local.headers, local.rows)
    metadata[table.tab] = digest
    gateway.write_tabs([_to_sheet_snapshot(local, table.tab), _metadata_snapshot(metadata)])
    _record_sync(paths, gateway, state, table.tab, local)
    log.debug("pushed %s -> %s (%d rows)", table.csv, table.tab, len(local.rows))
    return f"pushed {table.csv} -> {table.tab} ({len(local.rows)} rows)"


def pull_table(
    config: FccsvConfig,
    project_root: Path,
    gateway,
    tab: str,
    *,
    allow_schema_change: bool = False,
    dry_run: bool = False,
    force: str | None = None,
) -> str:
    paths = _paths(config, project_root)
    table = config.table_by_tab(tab)
    if table is None:
        raise ValueError(f"No mapping for tab '{tab}'. Run `fccsv init` first.")
    local = _local_snapshot(paths, table.csv)
    key = _resolve_key(table, local)
    if local is not None:
        local.key_column = key
    remote = _remote_snapshot(gateway, table.tab, table.column_map, key)
    if remote is None:
        raise ValueError(f"Sheet tab missing: {table.tab}")
    if local is not None:
        _check_conflict(paths, table.tab, local, remote, force=force)
        ensure_schema_compatible(local, remote, allow_schema_change=allow_schema_change)
        local_types = local.types
    else:
        local_types = []
    if dry_run:
        return f"dry-run: would pull {table.tab} -> {table.csv} ({len(remote.rows)} rows)"
    csv_path = paths.csv_dir / table.csv
    backup = backup_store.backup_file(csv_path, paths.backup_dir)
    if backup is not None:
        log.info("backed up %s -> %s", csv_path.name, backup.name)
    stem = table.csv.removesuffix(".csv")
    incoming = _to_local_snapshot(remote, local_types, stem)
    ensure_valid(incoming)
    write_csv_table(csv_path, incoming)
    state = state_store.load_state(paths.state_path)
    metadata = _seed_metadata(state.get("tables", {}))
    metadata.update(_read_metadata(gateway))
    metadata[table.tab] = state_store.snapshot_hash(remote.headers, remote.rows)
    gateway.write_tabs([_metadata_snapshot(metadata)])
    _record_sync(paths, gateway, state, table.tab, remote)
    log.debug("pulled %s -> %s (%d rows)", table.tab, table.csv, len(remote.rows))
    return f"pulled {table.tab} -> {table.csv} ({len(remote.rows)} rows)"


def _record_sync(paths: SyncPaths, gateway, state: dict, tab: str, snap: TableSnapshot) -> dict:
    """Record one table's hash and refresh the Drive stamp. Saves state."""
    digest = state_store.snapshot_hash(snap.headers, snap.rows)
    state_store.record_table(state, tab, digest, digest)
    stamp = _drive_stamp(gateway)
    if stamp:
        state["modified_time"] = stamp
    state_store.save_state(paths.state_path, state)
    return state


def push_all(
    config: FccsvConfig,
    project_root: Path,
    gateway,
    tabs: list[str],
    *,
    allow_schema_change: bool = False,
    dry_run: bool = False,
    force: str | None = None,
) -> tuple[list[str], list[str]]:
    """Push changed tables with one batched data read and write.

    The metadata read identifies clean tables before any data range is read.
    Returns (ok_lines, err_lines); a per-table validation or compatibility
    failure never aborts other tables.
    """
    paths = _paths(config, project_root)
    wanted = [t for t in (config.table_by_tab(tab) for tab in tabs) if t is not None]
    locals_: dict[str, TableSnapshot] = {}
    oks: list[str] = []
    errs: list[str] = []
    for table in wanted:
        local = _local_snapshot(paths, table.csv)
        if local is None:
            errs.append(f"error: [{table.tab}] local CSV missing: {table.csv}")
            continue
        local.key_column = _resolve_key(table, local)
        try:
            ensure_valid(local)
        except FccsvError as exc:
            errs.append(f"error: [{table.tab}] {exc}")
            continue
        locals_[table.tab] = local

    state = state_store.load_state(paths.state_path)
    recorded = state.get("tables", {})
    stamp = _drive_stamp(gateway)
    remote_unchanged = bool(stamp and stamp == state.get("modified_time"))
    existing = set(gateway.list_tabs())
    pending: list[str] = []
    for table in wanted:
        local = locals_.get(table.tab)
        if local is None:
            continue
        last = recorded.get(table.tab)
        digest = state_store.snapshot_hash(local.headers, local.rows)
        if remote_unchanged and last is not None and digest == last.get("local"):
            oks.append(f"in-sync {table.csv} -> {table.tab}")
            continue
        pending.append(table.tab)

    ranges = [tab for tab in pending if tab in existing]
    if pending and METADATA_TAB in existing:
        ranges.append(METADATA_TAB)
    batch = gateway.read_tabs(ranges) if ranges else {}
    metadata = _seed_metadata(recorded)
    metadata.update(_metadata_hashes(batch.pop(METADATA_TAB, None)))
    remotes = batch
    writes: list[TableSnapshot] = []
    for table in wanted:
        local = locals_.get(table.tab)
        if local is None or table.tab not in pending:
            continue
        raw = remotes.get(table.tab)
        if raw is not None:
            remote = translate_snapshot(raw, table.column_map, local.key_column)
            if not remote.key_column:
                remote.key_column = local.key_column
            last = recorded.get(table.tab) or {}
            try:
                if force not in ("push", "pull") and _is_conflict(
                    local, remote, last.get("local"), last.get("remote")
                ):
                    raise ConflictError(table.tab)
                ensure_schema_compatible(local, remote, allow_schema_change=allow_schema_change)
            except FccsvError as exc:
                errs.append(f"error: [{table.tab}] {exc}")
                continue
        if dry_run:
            oks.append(f"dry-run: would push {table.csv} -> {table.tab} ({len(local.rows)} rows)")
            continue
        writes.append(_to_sheet_snapshot(local, table.tab))
        digest = state_store.snapshot_hash(local.headers, local.rows)
        state_store.record_table(state, table.tab, digest, digest)
        metadata[table.tab] = digest
        oks.append(f"pushed {table.csv} -> {table.tab} ({len(local.rows)} rows)")
    if writes and not dry_run:
        writes.append(_metadata_snapshot(metadata))
        gateway.write_tabs(writes)
        stamp = _drive_stamp(gateway)
        if stamp:
            state["modified_time"] = stamp
        state_store.save_state(paths.state_path, state)
    for line in oks:
        log.debug("%s", line)
    return oks, errs


def pull_all(
    config: FccsvConfig,
    project_root: Path,
    gateway,
    tabs: list[str],
    *,
    allow_schema_change: bool = False,
    dry_run: bool = False,
    force: str | None = None,
) -> tuple[list[str], list[str]]:
    """Pull changed tables with one batched data read and metadata update."""
    paths = _paths(config, project_root)
    wanted = [t for t in (config.table_by_tab(tab) for tab in tabs) if t is not None]
    state = state_store.load_state(paths.state_path)
    recorded = state.get("tables", {})
    stamp = _drive_stamp(gateway)
    remote_unchanged = bool(stamp and stamp == state.get("modified_time"))
    existing = set(gateway.list_tabs())
    oks: list[str] = []
    errs: list[str] = []
    locals_: dict[str, TableSnapshot | None] = {}
    pending: list[str] = []
    missing: list[str] = []
    for table in wanted:
        local = _local_snapshot(paths, table.csv)
        if local is not None:
            local.key_column = _resolve_key(table, local)
        locals_[table.tab] = local
        last = recorded.get(table.tab)
        if (
            remote_unchanged
            and local is not None
            and last is not None
            and state_store.snapshot_hash(local.headers, local.rows) == last.get("local")
        ):
            oks.append(f"in-sync {table.tab} -> {table.csv}")
        elif table.tab in existing:
            pending.append(table.tab)
        else:
            missing.append(table.tab)

    ranges = list(pending)
    if pending and METADATA_TAB in existing:
        ranges.append(METADATA_TAB)
    batch = gateway.read_tabs(ranges) if ranges else {}
    sheet_metadata = _metadata_hashes(batch.pop(METADATA_TAB, None))
    metadata = _seed_metadata(recorded)
    metadata.update(sheet_metadata)
    remotes = batch
    for tab in missing:
        errs.append(f"error: [{tab}] sheet tab missing: {tab}")
    metadata_changed = metadata != sheet_metadata
    state_changed = False
    for table in wanted:
        if table.tab not in remotes:
            continue
        local = locals_[table.tab]
        key = _resolve_key(table, local)
        remote = translate_snapshot(remotes[table.tab], table.column_map, key)
        if not remote.key_column:
            remote.key_column = key
        try:
            if local is not None:
                last = recorded.get(table.tab) or {}
                if force not in ("push", "pull") and _is_conflict(
                    local, remote, last.get("local"), last.get("remote")
                ):
                    raise ConflictError(table.tab)
                ensure_schema_compatible(local, remote, allow_schema_change=allow_schema_change)
                local_types = local.types
            else:
                local_types = []
            if dry_run:
                oks.append(
                    f"dry-run: would pull {table.tab} -> {table.csv} ({len(remote.rows)} rows)"
                )
                continue
            csv_path = paths.csv_dir / table.csv
            backup_store.backup_file(csv_path, paths.backup_dir)
            incoming = _to_local_snapshot(remote, local_types, table.csv.removesuffix(".csv"))
            ensure_valid(incoming)
            write_csv_table(csv_path, incoming)
            digest = state_store.snapshot_hash(remote.headers, remote.rows)
            state_store.record_table(state, table.tab, digest, digest)
            if metadata.get(table.tab) != digest:
                metadata[table.tab] = digest
                metadata_changed = True
            state_changed = True
            oks.append(f"pulled {table.tab} -> {table.csv} ({len(remote.rows)} rows)")
        except FccsvError as exc:
            errs.append(f"error: [{table.tab}] {exc}")
    if metadata_changed and not dry_run:
        gateway.write_tabs([_metadata_snapshot(metadata)])
    if state_changed and not dry_run:
        stamp = _drive_stamp(gateway)
        if stamp:
            state["modified_time"] = stamp
        state_store.save_state(paths.state_path, state)
    for line in oks:
        log.debug("%s", line)
    return oks, errs
