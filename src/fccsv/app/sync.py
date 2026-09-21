"""Sync engine: one `plan -> apply` pipeline for single AND batch paths.

`push_table` is `plan_one + apply_push`; `push_all` is `plan_many +
apply_push` per table with one batched sheet read and incremental state
saves (a quota abort keeps already-synced tables, unlike v1).
Partial `--only/--exclude` merges selected keys onto the other side
instead of full-table overwrite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from fccsv.adapters import backup as backup_mod
from fccsv.adapters import base as base_mod
from fccsv.adapters import local as local_mod
from fccsv.adapters import state as state_mod
from fccsv.adapters.sheets import SheetsGateway
from fccsv.core.config import TableMapping
from fccsv.domain import keys as keys_mod
from fccsv.domain.diff import diff_rows, diff_schema, translate_snapshot
from fccsv.domain.errors import (
    ConflictError,
    DataValidationError,
    MappingError,
    TabMissingError,
)
from fccsv.domain.merge3 import both_changed_since_base
from fccsv.domain.schema import ensure_schema_compatible
from fccsv.domain.snapshot import RowDiff, SchemaDiff, TableSnapshot
from fccsv.domain.validate import ensure_valid, validate_snapshot


@dataclass
class TablePlan:
    """Everything `apply_*` needs. Read-only; built by `plan_one/many`."""

    name: str
    tab: str
    csv_file: str
    local: TableSnapshot | None
    remote: TableSnapshot | None
    base: TableSnapshot | None
    local_hash: str = ""
    remote_hash: str = ""
    last_local: str = ""
    last_remote: str = ""
    schema_diff: SchemaDiff = field(default_factory=SchemaDiff)
    row_diff: RowDiff = field(default_factory=RowDiff)
    validation_issues: list[str] = field(default_factory=list)
    rename_hint: str = ""


@dataclass
class ApplyOutcome:
    table: str
    applied: bool
    message: str


def _load_local(csv_dir: Path, mapping: TableMapping) -> TableSnapshot | None:
    path = csv_dir / mapping.csv
    if not path.exists():
        return None
    return local_mod.read_csv_table(path, key_column=mapping.key_column)


def _load_remote(
    gateway: SheetsGateway, mapping: TableMapping, preloaded: dict[str, TableSnapshot]
) -> TableSnapshot | None:
    try:
        snap = preloaded[mapping.tab] if mapping.tab in preloaded else gateway.read_tab(mapping.tab)
    except (TabMissingError, KeyError, ValueError):
        return None
    translated = translate_snapshot(snap, mapping.column_map, key_column=mapping.key_column)
    if not translated.key_column:
        key = mapping.key_column or keys_mod.infer_key_column(translated.headers, translated.rows)
        if key and key != translated.key_column:
            translated = TableSnapshot(
                name=translated.name,
                headers=list(translated.headers),
                types=list(translated.types),
                rows=[dict(r) for r in translated.rows],
                key_column=key,
            )
    return translated


def build_plan(
    mapping: TableMapping,
    *,
    local: TableSnapshot | None,
    remote: TableSnapshot | None,
    base: TableSnapshot | None,
    last_local: str,
    last_remote: str,
    rename_hint: str = "",
) -> TablePlan:
    local_hash = state_mod.snapshot_hash(local) if local is not None else ""
    remote_hash = state_mod.snapshot_hash(remote) if remote is not None else ""
    schema_diff = (
        diff_schema(local, remote) if local is not None and remote is not None else SchemaDiff()
    )
    row_diff = diff_rows(local, remote) if local is not None and remote is not None else RowDiff()
    issues = validate_snapshot(local) if local is not None else []
    return TablePlan(
        name=mapping.tab,
        tab=mapping.tab,
        csv_file=mapping.csv,
        local=local,
        remote=remote,
        base=base,
        local_hash=local_hash,
        remote_hash=remote_hash,
        last_local=last_local,
        last_remote=last_remote,
        schema_diff=schema_diff,
        row_diff=row_diff,
        validation_issues=issues,
        rename_hint=rename_hint,
    )


def plan_one(
    mapping: TableMapping,
    *,
    csv_dir: Path,
    gateway: SheetsGateway,
    state: state_mod.SyncState,
    base_dir: Path,
) -> TablePlan:
    local = _load_local(csv_dir, mapping)
    remote = _load_remote(gateway, mapping, {})
    base = base_mod.load_base(base_dir, mapping.tab)
    last = state.last(mapping.tab)
    return build_plan(
        mapping,
        local=local,
        remote=remote,
        base=base,
        last_local=last.local,
        last_remote=last.remote,
    )


def plan_many(
    mappings: list[TableMapping],
    *,
    csv_dir: Path,
    gateway: SheetsGateway,
    state: state_mod.SyncState,
    base_dir: Path,
    remote_snapshots: dict[str, TableSnapshot] | None = None,
) -> list[TablePlan]:
    """Batched planning. Pass `remote_snapshots` from one `read_tabs` call
    (including the `_fccsv` tab) to spend a single read round-trip."""
    if remote_snapshots is None:
        try:
            remote_snapshots = gateway.read_tabs([m.tab for m in mappings])
        except Exception:
            remote_snapshots = {}
    plans: list[TablePlan] = []
    for mapping in mappings:
        local = _load_local(csv_dir, mapping)
        remote = _load_remote(gateway, mapping, remote_snapshots)
        base = base_mod.load_base(base_dir, mapping.tab)
        last = state.last(mapping.tab)
        hint = ""
        if local is None and remote is None and last.local == "" and last.remote == "":
            hint = ""
        plans.append(
            build_plan(
                mapping,
                local=local,
                remote=remote,
                base=base,
                last_local=last.local,
                last_remote=last.remote,
                rename_hint=hint,
            )
        )
    return plans


def plan_in_sync(plan: TablePlan) -> bool:
    if plan.local is None or plan.remote is None:
        return False
    return (
        not plan.schema_diff.has_change
        and not plan.row_diff.has_change
        and plan.local_hash == plan.remote_hash
    )


def plan_conflicted(plan: TablePlan) -> bool:
    if plan.local is None or plan.remote is None or plan.base is None:
        if plan.base is not None:
            return False
        return both_changed_since_base(
            None,
            plan.local or TableSnapshot(name=plan.name),
            plan.remote or TableSnapshot(name=plan.name),
            plan.last_local or None,
            plan.last_remote or None,
            local_hash=plan.local_hash,
            remote_hash=plan.remote_hash,
        )
    assert plan.local is not None and plan.remote is not None
    return both_changed_since_base(
        plan.base,
        plan.local,
        plan.remote,
        plan.last_local or None,
        plan.last_remote or None,
        local_hash=plan.local_hash,
        remote_hash=plan.remote_hash,
    )


def _select_keys(snapshot: TableSnapshot, only_keys: set[str] | None) -> set[str]:
    if only_keys is None:
        return {snapshot.key_of(r) for r in snapshot.rows}
    return set(only_keys)


def _overlay_onto(
    source: TableSnapshot, overlay: TableSnapshot, *, exclude: set[str]
) -> TableSnapshot:
    """Partial write: overlay's rows win per selected key; excluded columns
    keep the source value. Returns a new snapshot on source's key column."""
    key_col = source.key_column or overlay.key_column
    by_key = {source.key_of(r): dict(r) for r in source.rows}
    order = [source.key_of(r) for r in source.rows]
    for row in overlay.rows:
        key = overlay.key_of(row)
        if key not in by_key:
            by_key[key] = {h: "" for h in source.headers}
            order.append(key)
        for header in source.headers:
            if header in exclude:
                continue
            if header in row:
                by_key[key][header] = row[header]
    return TableSnapshot(
        name=source.name,
        headers=list(source.headers),
        types=list(source.types),
        rows=[by_key[k] for k in order],
        key_column=key_col,
    )


def apply_push(
    plan: TablePlan,
    gateway: SheetsGateway,
    *,
    force: bool = False,
    allow_schema_change: bool = False,
    dry_run: bool = False,
    only_keys: set[str] | None = None,
    exclude_columns: set[str] | None = None,
) -> tuple[ApplyOutcome, TableSnapshot | None]:
    """Push local -> sheet. Returns (outcome, new_remote_snapshot)."""
    if plan.local is None:
        raise MappingError(f"[{plan.tab}] local CSV missing: {plan.csv_file}")
    ensure_valid(plan.local)
    if plan.remote is not None:
        ensure_schema_compatible(plan.local, plan.remote, allow_schema_change=allow_schema_change)
        if not force and plan_conflicted(plan):
            raise ConflictError(plan.tab)
    exclude = exclude_columns or set()
    if plan.remote is None:
        new_remote = plan.local
    elif only_keys is None and not exclude:
        new_remote = plan.local
    else:
        selected = [
            r
            for r in plan.local.rows
            if plan.local.key_of(r) in _select_keys(plan.local, only_keys)
        ]
        overlay = TableSnapshot(
            name=plan.local.name,
            headers=list(plan.local.headers),
            types=list(plan.local.types),
            rows=selected,
            key_column=plan.local.key_column,
        )
        new_remote = _overlay_onto(plan.remote, overlay, exclude=exclude)
    if dry_run:
        scope = " (partial)" if (only_keys is not None or exclude) else ""
        return ApplyOutcome(plan.tab, False, f"[{plan.tab}] would push{scope}"), None
    if plan.remote is not None and state_mod.snapshot_hash(new_remote) == plan.remote_hash:
        return ApplyOutcome(plan.tab, False, f"[{plan.tab}] already in sync"), None
    sheet_view = TableSnapshot(
        name=plan.tab,
        headers=list(new_remote.headers),
        rows=[dict(r) for r in new_remote.rows],
        key_column=new_remote.key_column,
    )
    gateway.write_tab(sheet_view)
    scope = " (partial)" if (only_keys is not None or exclude) else ""
    return ApplyOutcome(plan.tab, True, f"[{plan.tab}] pushed{scope}"), new_remote


def apply_pull(
    plan: TablePlan,
    gateway: SheetsGateway,
    *,
    csv_dir: Path,
    backup_root: Path,
    force: bool = False,
    allow_schema_change: bool = False,
    dry_run: bool = False,
    only_keys: set[str] | None = None,
    exclude_columns: set[str] | None = None,
) -> tuple[ApplyOutcome, TableSnapshot | None]:
    """Pull sheet -> local. Backs up the CSV first (unless dry-run)."""
    _ = gateway  # remote already loaded in plan; kept for signature symmetry.
    if plan.remote is None:
        raise TabMissingError(plan.tab)
    if plan.local is None:
        raise MappingError(f"[{plan.tab}] local CSV missing: {plan.csv_file}")
    remote_issues = validate_snapshot(plan.remote)
    if remote_issues:
        raise DataValidationError(
            f"[{plan.tab}] sheet failed validation: " + "; ".join(remote_issues)
        )
    ensure_schema_compatible(plan.local, plan.remote, allow_schema_change=allow_schema_change)
    if not force and plan_conflicted(plan):
        raise ConflictError(plan.tab)
    exclude = exclude_columns or set()
    if only_keys is None and not exclude:
        merged = TableSnapshot(
            name=plan.local.name,
            headers=list(plan.local.headers),
            types=list(plan.local.types),
            rows=[{h: r.get(h, "") for h in plan.local.headers} for r in plan.remote.rows],
            key_column=plan.local.key_column or plan.remote.key_column,
        )
    else:
        selected = [
            r
            for r in plan.remote.rows
            if plan.remote.key_of(r) in _select_keys(plan.remote, only_keys)
        ]
        overlay = TableSnapshot(
            name=plan.remote.name,
            headers=list(plan.remote.headers),
            types=list(plan.local.types),
            rows=selected,
            key_column=plan.remote.key_column,
        )
        local_headers = TableSnapshot(
            name=plan.local.name,
            headers=list(plan.local.headers),
            types=list(plan.local.types),
            rows=[{h: r.get(h, "") for h in plan.local.headers} for r in plan.local.rows],
            key_column=plan.local.key_column,
        )
        remote_as_local = TableSnapshot(
            name=overlay.name,
            headers=list(overlay.headers),
            types=list(overlay.types),
            rows=[{h: r.get(h, "") for h in overlay.headers} for r in overlay.rows],
            key_column=overlay.key_column,
        )
        _ = remote_as_local
        merged = _overlay_onto(local_headers, overlay, exclude=exclude)
    if dry_run:
        scope = " (partial)" if (only_keys is not None or exclude) else ""
        return ApplyOutcome(plan.tab, False, f"[{plan.tab}] would pull{scope}"), None
    if state_mod.snapshot_hash(merged) == plan.local_hash:
        return ApplyOutcome(plan.tab, False, f"[{plan.tab}] already in sync"), None
    backup_mod.backup_file(csv_dir / plan.csv_file, backup_root)
    local_mod.write_csv_table(csv_dir / plan.csv_file, merged)
    scope = " (partial)" if (only_keys is not None or exclude) else ""
    return ApplyOutcome(plan.tab, True, f"[{plan.tab}] pulled{scope}"), merged
