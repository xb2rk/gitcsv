"""Diff over snapshots. Pure, never mutates inputs."""

from __future__ import annotations

from fccsv.domain import keys as keys_mod
from fccsv.domain.snapshot import RowDiff, SchemaDiff, TableSnapshot


def diff_schema(local: TableSnapshot, remote: TableSnapshot) -> SchemaDiff:
    added = [h for h in remote.headers if h not in local.headers]
    removed = [h for h in local.headers if h not in remote.headers]
    common_local = [h for h in local.headers if h in remote.headers]
    common_remote = [h for h in remote.headers if h in local.headers]
    return SchemaDiff(added=added, removed=removed, reordered=common_local != common_remote)


def _rows_by_key(table: TableSnapshot) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for row in table.rows:
        out[table.key_of(row)] = row
    return out


def diff_rows(
    local: TableSnapshot,
    remote: TableSnapshot,
    compare_headers: list[str] | None = None,
) -> RowDiff:
    """Compare data rows by key. Positional fallback when either side is key-less."""
    if not local.key_column or not remote.key_column:
        changed = [str(i) for i, (a, b) in enumerate(zip(local.rows, remote.rows)) if a != b]
        added = [f"+{i}" for i in range(len(local.rows), len(remote.rows))]
        removed = [f"-{i}" for i in range(len(remote.rows), len(local.rows))]
        return RowDiff(added_keys=added, removed_keys=removed, changed_keys=changed)
    headers = compare_headers or [h for h in local.headers if h in remote.headers]
    local_by_key = _rows_by_key(local)
    remote_by_key = _rows_by_key(remote)
    added = [k for k in remote_by_key if k not in local_by_key]
    removed = [k for k in local_by_key if k not in remote_by_key]
    changed = []
    for key in local_by_key:
        if key not in remote_by_key:
            continue
        a, b = local_by_key[key], remote_by_key[key]
        if any(a.get(h, "") != b.get(h, "") for h in headers):
            changed.append(key)
    return RowDiff(added_keys=added, removed_keys=removed, changed_keys=changed)


def translate_snapshot(
    snapshot: TableSnapshot,
    column_map: dict[str, str],
    key_column: str = "",
) -> TableSnapshot:
    """Rename headers through sheet->csv column_map. Pure: never mutates input."""
    if not column_map:
        if key_column and key_column != snapshot.key_column:
            return TableSnapshot(
                name=snapshot.name,
                headers=list(snapshot.headers),
                types=list(snapshot.types),
                rows=[dict(r) for r in snapshot.rows],
                key_column=key_column,
                raw_widths=list(snapshot.raw_widths),
                line_no=list(snapshot.line_no),
            )
        return snapshot
    renamed_headers = keys_mod.apply_column_map(snapshot.headers, column_map)
    rename = {src: keys_mod.apply_column_map([src], column_map)[0] for src in snapshot.headers}
    rows = [{rename.get(k, k): v for k, v in row.items()} for row in snapshot.rows]
    return TableSnapshot(
        name=snapshot.name,
        headers=renamed_headers,
        types=list(snapshot.types),
        rows=rows,
        key_column=key_column or snapshot.key_column,
        raw_widths=list(snapshot.raw_widths),
        line_no=list(snapshot.line_no),
    )


def filter_snapshot(
    snapshot: TableSnapshot,
    *,
    only_keys: set[str] | None = None,
    exclude_columns: set[str] | None = None,
) -> TableSnapshot:
    """Partial view for `--only K1,K2 --exclude Col`. Pure."""
    rows = snapshot.rows
    if only_keys is not None:
        rows = [r for r in rows if snapshot.key_of(r) in only_keys]
    headers = list(snapshot.headers)
    if exclude_columns:
        headers = [h for h in headers if h not in exclude_columns]
        rows = [{h: r.get(h, "") for h in headers} for r in rows]
    else:
        rows = [dict(r) for r in rows]
    return TableSnapshot(
        name=snapshot.name,
        headers=headers,
        types=[t for h, t in zip(snapshot.headers, snapshot.types) if h in headers],
        rows=rows,
        key_column=snapshot.key_column,
        raw_widths=list(snapshot.raw_widths)[: len(rows)],
        line_no=list(snapshot.line_no)[: len(rows)],
    )
