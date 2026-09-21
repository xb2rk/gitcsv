"""Cell-level 3-way merge: BASE (last sync), LOCAL, REMOTE.

Rule per cell: same value on both sides -> keep. Only one side changed
vs BASE -> take the changed side. Both changed differently -> conflict,
keep local value in `merged` and record the cell.
Row add/remove follows the same per-key logic; key-less tables merge
positionally. Pure, no I/O.
"""

from __future__ import annotations

from fccsv.domain.snapshot import CellChange, MergeResult, TableSnapshot


def _cell_base(
    base_by_key: dict[str, dict[str, str]],
    local_by_key: dict[str, dict[str, str]],
    remote_by_key: dict[str, dict[str, str]],
    key: str,
    header: str,
) -> str:
    if key in base_by_key:
        return base_by_key[key].get(header, "")
    # New row: base is empty unless one side already has it.
    return ""


def merge3(
    base: TableSnapshot | None,
    local: TableSnapshot,
    remote: TableSnapshot,
) -> MergeResult:
    headers = list(local.headers)
    if base is None:
        base = TableSnapshot(name=local.name, headers=list(headers))
    base_by_key = {base.key_of(r): r for r in base.rows} if base.key_column else {}
    local_by_key = {local.key_of(r): r for r in local.rows} if local.key_column else {}
    remote_by_key = {remote.key_of(r): r for r in remote.rows} if remote.key_column else {}

    if not local.key_column or not remote.key_column:
        return _merge_positional(base, local, remote)

    conflicts: list[CellChange] = []
    merged_rows: list[dict[str, str]] = []
    for key in list(local_by_key) + [k for k in remote_by_key if k not in local_by_key]:
        if key in local_by_key and key not in remote_by_key:
            if key in base_by_key:
                # Removed remotely, kept locally: both-changed only if local also edited.
                if local_by_key[key] != base_by_key.get(key, {}):
                    conflicts.append(CellChange(key, "*", "", _row_text(local_by_key[key]), ""))
                merged_rows.append(dict(local_by_key[key]))
            else:
                merged_rows.append(dict(local_by_key[key]))
        elif key not in local_by_key and key in remote_by_key:
            if key in base_by_key:
                conflicts.append(CellChange(key, "*", "", "", _row_text(remote_by_key[key])))
            merged_rows.append(dict(remote_by_key[key]))
        else:
            row: dict[str, str] = {}
            for h in headers:
                b = _cell_base(base_by_key, local_by_key, remote_by_key, key, h)
                lv = local_by_key[key].get(h, "")
                rv = remote_by_key[key].get(h, "")
                if lv == rv:
                    row[h] = lv
                elif lv == b:
                    row[h] = rv
                elif rv == b:
                    row[h] = lv
                else:
                    row[h] = lv
                    conflicts.append(CellChange(key, h, b, lv, rv))
            merged_rows.append(row)

    merged = TableSnapshot(
        name=local.name,
        headers=headers,
        types=list(local.types),
        rows=merged_rows,
        key_column=local.key_column,
    )
    return MergeResult(merged=merged, conflicts=conflicts)


def _row_text(row: dict[str, str]) -> str:
    return ",".join(f"{k}={v}" for k, v in sorted(row.items()))


def _merge_positional(
    base: TableSnapshot,
    local: TableSnapshot,
    remote: TableSnapshot,
) -> MergeResult:
    """Key-less fallback: per-index 3-way on shared headers."""
    headers = list(local.headers)
    conflicts: list[CellChange] = []
    merged_rows: list[dict[str, str]] = []
    width = max(len(base.rows), len(local.rows), len(remote.rows))
    for i in range(width):
        b = base.rows[i] if i < len(base.rows) else {}
        lv_row = local.rows[i] if i < len(local.rows) else {}
        rv_row = remote.rows[i] if i < len(remote.rows) else {}
        if i >= len(local.rows):
            merged_rows.append(dict(rv_row))
            continue
        if i >= len(remote.rows):
            merged_rows.append(dict(lv_row))
            continue
        row: dict[str, str] = {}
        for h in headers:
            bval, lval, rval = b.get(h, ""), lv_row.get(h, ""), rv_row.get(h, "")
            if lval == rval:
                row[h] = lval
            elif lval == bval:
                row[h] = rval
            elif rval == bval:
                row[h] = lval
            else:
                row[h] = lval
                conflicts.append(CellChange(str(i), h, bval, lval, rval))
        merged_rows.append(row)
    merged = TableSnapshot(
        name=local.name,
        headers=headers,
        types=list(local.types),
        rows=merged_rows,
        key_column="",
    )
    return MergeResult(merged=merged, conflicts=conflicts)


def both_changed_since_base(
    base: TableSnapshot | None,
    local: TableSnapshot,
    remote: TableSnapshot,
    last_local_hash: str | None,
    last_remote_hash: str | None,
    *,
    local_hash: str,
    remote_hash: str,
) -> bool:
    """Table-level conflict gate used when BASE content is unavailable.

    Prefers real BASE diff when present; falls back to v1 hash comparison
    so a fresh v1 state file still blocks correctly.
    """
    if base is not None and base.rows:
        local_moved = any(
            local.key_of(r) != base.key_of(br) or r != br for r, br in zip(local.rows, base.rows)
        ) or len(local.rows) != len(base.rows)
        remote_moved = any(
            remote.key_of(r) != base.key_of(br) or r != br for r, br in zip(remote.rows, base.rows)
        ) or len(remote.rows) != len(base.rows)
        return local_moved and remote_moved
    if last_local_hash is None or last_remote_hash is None:
        return False
    return local_hash != last_local_hash and remote_hash != last_remote_hash
