"""Diff + merge. Operates on TableSnapshot only; no file or network I/O."""

from __future__ import annotations

from fccsv.domain import mapping as mapping_mod
from fccsv.domain.models import RowDiff, SchemaDiff, TableSnapshot


def diff_schema(local: TableSnapshot, remote: TableSnapshot) -> SchemaDiff:
    local_headers = local.headers
    remote_headers = remote.headers
    added = [h for h in remote_headers if h not in local_headers]
    removed = [h for h in local_headers if h not in remote_headers]
    common_local = [h for h in local_headers if h in remote_headers]
    common_remote = [h for h in remote_headers if h in local_headers]
    reordered = common_local != common_remote
    return SchemaDiff(added=added, removed=removed, reordered=reordered)


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
    """Compare data rows by key column. Falls back to positional compare w/o keys."""
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
    """Rename snapshot headers through sheet->csv column_map."""
    if not column_map:
        if key_column:
            snapshot.key_column = key_column
        return snapshot
    renamed_headers = mapping_mod.apply_column_map(snapshot.headers, column_map)
    rename = {src: mapping_mod.apply_column_map([src], column_map)[0] for src in snapshot.headers}
    rows = [{rename.get(k, k): v for k, v in row.items()} for row in snapshot.rows]
    return TableSnapshot(
        name=snapshot.name,
        headers=renamed_headers,
        types=list(snapshot.types),
        rows=rows,
        key_column=key_column or snapshot.key_column,
    )
