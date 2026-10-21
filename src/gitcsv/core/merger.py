"""Three-way merge engine for CSV and Google Sheets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from gitcsv.core.models import (
    Identity,
    OperationDecision,
    TablePlan,
    TableSnapshot,
)


@dataclass(frozen=True, slots=True)
class CellConflict:
    """A conflicting edit where both local and remote changed the same cell differently."""

    row_key: tuple[str, ...] | int
    column: str
    base_val: str
    local_val: str
    remote_val: str


@dataclass(frozen=True, slots=True)
class MergeResult:
    """The result of a three-way merge operation."""

    table: str
    merged_local: TableSnapshot | None
    merged_remote: TableSnapshot | None
    conflicts: tuple[CellConflict, ...] = ()

    @property
    def has_conflicts(self) -> bool:
        return bool(self.conflicts)


def three_way_merge(
    plan: TablePlan,
    *,
    strategy: Literal["ours", "theirs", "manual"] = "manual",
) -> MergeResult:
    """Merge independent cell changes between local CSV and remote Sheet using BASE."""
    local, remote, base = plan.local, plan.remote, plan.base
    if local is None or remote is None or base is None:
        return MergeResult(
            table=plan.name,
            merged_local=None,
            merged_remote=None,
            conflicts=(),
        )

    # 1. Ensure schemas match for 3-way cell merge
    if local.headers != remote.headers or local.headers != base.headers:
        return MergeResult(
            table=plan.name,
            merged_local=None,
            merged_remote=None,
            conflicts=(
                CellConflict(
                    0,
                    "<schema>",
                    f"base cols: {len(base.headers)}",
                    f"local cols: {len(local.headers)}",
                    f"remote cols: {len(remote.headers)}",
                ),
            ),
        )

    headers = local.headers
    identity = plan.identity or Identity(mode="position")
    conflicts: list[CellConflict] = []

    # 2. Keyed indexing helper
    def to_row_map(snap: TableSnapshot) -> dict[tuple[str, ...] | int, tuple[str, ...]]:
        if identity.mode == "columns" and identity.columns:
            indices = tuple(snap.headers.index(c) for c in identity.columns)
            return {tuple(row[idx] for idx in indices): row for row in snap.rows}
        return {idx + 1: row for idx, row in enumerate(snap.rows)}

    local_map = to_row_map(local)
    remote_map = to_row_map(remote)
    base_map = to_row_map(base)

    all_keys = list(dict.fromkeys((*local_map, *remote_map, *base_map)))
    merged_rows: list[tuple[str, ...]] = []

    # 3. Merge row by row, cell by cell
    for key in all_keys:
        in_local = key in local_map
        in_remote = key in remote_map
        in_base = key in base_map

        # Case: Row deleted in both
        if not in_local and not in_remote:
            continue

        # Case: Row deleted on one side, unchanged on other
        if in_base and not in_local:
            if remote_map.get(key) == base_map.get(key):
                # Remote didn't touch it, local deleted it -> delete
                continue
            # Remote modified it, local deleted it -> conflict
            conflicts.append(CellConflict(key, "<row>", "present", "deleted", "modified"))
            continue

        if in_base and not in_remote:
            if local_map.get(key) == base_map.get(key):
                # Local didn't touch it, remote deleted it -> delete
                continue
            # Local modified it, remote deleted it -> conflict
            conflicts.append(CellConflict(key, "<row>", "present", "modified", "deleted"))
            continue

        # Case: Row added on one side only
        if not in_base and in_local and not in_remote:
            merged_rows.append(local_map[key])
            continue
        if not in_base and not in_local and in_remote:
            merged_rows.append(remote_map[key])
            continue

        # Case: Both have the row -> compare cell values
        l_row = local_map[key]
        r_row = remote_map[key]
        b_row = base_map.get(key, tuple("" for _ in headers))

        merged_cells: list[str] = []
        for col_idx, col_name in enumerate(headers):
            l_val = l_row[col_idx]
            r_val = r_row[col_idx]
            b_val = b_row[col_idx] if col_idx < len(b_row) else ""

            if l_val == r_val:
                merged_cells.append(l_val)
            elif l_val == b_val:
                # Local unchanged, remote changed -> take remote
                merged_cells.append(r_val)
            elif r_val == b_val:
                # Remote unchanged, local changed -> take local
                merged_cells.append(l_val)
            else:
                # Both changed to different values -> CONFLICT
                if strategy == "ours":
                    merged_cells.append(l_val)
                elif strategy == "theirs":
                    merged_cells.append(r_val)
                else:
                    conflicts.append(CellConflict(key, col_name, b_val, l_val, r_val))
                    merged_cells.append(l_val)  # placeholder

        merged_rows.append(tuple(merged_cells))

    if conflicts:
        return MergeResult(
            table=plan.name,
            merged_local=None,
            merged_remote=None,
            conflicts=tuple(conflicts),
        )

    # 4. Construct unified merged snapshots
    target_local = TableSnapshot(
        name=plan.name,
        headers=headers,
        types=local.types,
        rows=tuple(merged_rows),
        source="csv",
    )
    target_remote = TableSnapshot(
        name=plan.name,
        headers=headers,
        types=(),
        rows=tuple(merged_rows),
        source="sheet",
    )

    return MergeResult(
        table=plan.name,
        merged_local=target_local,
        merged_remote=target_remote,
        conflicts=(),
    )


def decide_sync(
    plan: TablePlan,
    *,
    strategy: Literal["ours", "theirs", "manual"] = "manual",
) -> OperationDecision:
    """Evaluate three-way sync and create an OperationDecision."""
    if plan.base is None:
        return OperationDecision(
            kind="sync",
            table=plan.name,
            local_after=None,
            remote_after=None,
            errors=(
                f"Cannot 3-way sync '{plan.name}': no common BASE exists. "
                f"Run 'gitcsv push {plan.name} --force' or 'gitcsv pull {plan.name} --force' first.",
            ),
        )

    merge_result = three_way_merge(plan, strategy=strategy)
    if merge_result.has_conflicts:
        error_msgs = [
            f"Conflict in row {c.row_key}, column '{c.column}': "
            f"Local='{c.local_val}', Remote='{c.remote_val}' (Base='{c.base_val}')"
            for c in merge_result.conflicts[:5]
        ]
        if len(merge_result.conflicts) > 5:
            error_msgs.append(f"... and {len(merge_result.conflicts) - 5} more conflicts")

        return OperationDecision(
            kind="sync",
            table=plan.name,
            local_after=None,
            remote_after=None,
            errors=(
                f"{len(merge_result.conflicts)} conflict(s) detected in '{plan.name}'. "
                "Resolve conflicts or re-run with --ours / --theirs",
                *error_msgs,
            ),
        )

    return OperationDecision(
        kind="sync",
        table=plan.name,
        local_after=merge_result.merged_local,
        remote_after=merge_result.merged_remote,
        errors=(),
    )
