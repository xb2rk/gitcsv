"""Pure three-way merge for tables with a trusted common BASE."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fccsv3.domain.model import Identity, TablePlan, TableSnapshot

ConflictKind = Literal["base", "schema", "identity", "structure", "row", "cell"]


@dataclass(frozen=True, slots=True)
class MergeConflict:
    kind: ConflictKind
    message: str
    row: tuple[str, ...] | int | None = None
    column: str | None = None


@dataclass(frozen=True, slots=True)
class MergeResult:
    snapshot: TableSnapshot | None
    conflicts: tuple[MergeConflict, ...] = ()

    @property
    def clean(self) -> bool:
        return self.snapshot is not None and not self.conflicts


def _same_values(left: TableSnapshot, right: TableSnapshot) -> bool:
    return left.headers == right.headers and left.rows == right.rows


def _with_local_types(values: TableSnapshot, local: TableSnapshot) -> TableSnapshot:
    return TableSnapshot(
        local.name,
        values.headers,
        values.rows,
        types=local.types,
        source="csv",
    )


def _indexed_rows(
    snapshot: TableSnapshot, identity: Identity
) -> tuple[tuple[tuple[str, ...], ...], dict[tuple[str, ...], tuple[str, ...]]]:
    positions = tuple(snapshot.headers.index(column) for column in identity.columns)
    sequence = tuple(tuple(row[index] for index in positions) for row in snapshot.rows)
    return sequence, dict(zip(sequence, snapshot.rows, strict=True))


def _merge_row(
    key: tuple[str, ...],
    base: tuple[str, ...] | None,
    local: tuple[str, ...] | None,
    remote: tuple[str, ...] | None,
    headers: tuple[str, ...],
) -> tuple[tuple[str, ...] | None, tuple[MergeConflict, ...]]:
    if base is None:
        if local is None or remote is None or local == remote:
            return local if local is not None else remote, ()
        return None, (MergeConflict("row", "same new row differs on both sides", key),)
    if local is None and remote is None:
        return None, ()
    if local is None:
        if remote == base:
            return None, ()
        return None, (MergeConflict("row", "CSV deleted a row changed on Sheet", key),)
    if remote is None:
        if local == base:
            return None, ()
        return None, (MergeConflict("row", "Sheet deleted a row changed in CSV", key),)

    merged: list[str] = []
    conflicts: list[MergeConflict] = []
    for index, column in enumerate(headers):
        old, csv_value, sheet_value = base[index], local[index], remote[index]
        if csv_value == sheet_value:
            merged.append(csv_value)
        elif csv_value == old:
            merged.append(sheet_value)
        elif sheet_value == old:
            merged.append(csv_value)
        else:
            conflicts.append(MergeConflict("cell", "both sides changed this cell", key, column))
    return (tuple(merged) if not conflicts else None), tuple(conflicts)


def merge3(plan: TablePlan) -> MergeResult:
    """Combine independent edits, preserving one-sided row structure and CSV types.

    Concurrent structural changes are deliberately blocked: row order can be
    meaningful to the game, so guessing an insertion order would lose intent.
    """

    local, remote, base, identity = plan.local, plan.remote, plan.base, plan.identity
    if plan.has_errors or local is None or remote is None or identity is None:
        return MergeResult(None, (MergeConflict("identity", "both valid sides are required"),))
    if base is None:
        return MergeResult(None, (MergeConflict("base", "no trusted common BASE"),))
    if local.headers != remote.headers or local.headers != base.headers:
        return MergeResult(None, (MergeConflict("schema", "headers differ from common BASE"),))

    # Positional matching cannot prove that a shifted row is the same entity.
    if identity.mode == "position":
        local_changed = not _same_values(local, base)
        remote_changed = not _same_values(remote, base)
        if local_changed and remote_changed and not _same_values(local, remote):
            return MergeResult(
                None, (MergeConflict("identity", "both sides changed without row identity"),)
            )
        winner = local if local_changed or not remote_changed else remote
        return MergeResult(_with_local_types(winner, local))

    if any(column not in base.headers for column in identity.columns):
        return MergeResult(
            None, (MergeConflict("identity", "row identity is absent from common BASE"),)
        )
    base_sequence, base_rows = _indexed_rows(base, identity)
    local_sequence, local_rows = _indexed_rows(local, identity)
    remote_sequence, remote_rows = _indexed_rows(remote, identity)
    if len(base_rows) != len(base_sequence):
        return MergeResult(None, (MergeConflict("identity", "duplicate row identity in BASE"),))

    local_structure = local_sequence != base_sequence
    remote_structure = remote_sequence != base_sequence
    if local_structure and remote_structure and local_sequence != remote_sequence:
        return MergeResult(
            None, (MergeConflict("structure", "both sides changed row membership or order"),)
        )
    order = (
        local_sequence
        if local_structure
        else remote_sequence if remote_structure else base_sequence
    )
    all_keys = set(base_rows) | set(local_rows) | set(remote_rows)
    merged_rows: dict[tuple[str, ...], tuple[str, ...]] = {}
    conflicts: list[MergeConflict] = []
    for key in all_keys:
        row, issues = _merge_row(
            key, base_rows.get(key), local_rows.get(key), remote_rows.get(key), local.headers
        )
        conflicts.extend(issues)
        if row is not None:
            merged_rows[key] = row
    if conflicts:
        return MergeResult(None, tuple(sorted(conflicts, key=lambda item: (item.row or (), item.column or ""))))
    if set(order) != set(merged_rows):
        return MergeResult(
            None, (MergeConflict("structure", "merged row order cannot represent all rows"),)
        )
    return MergeResult(
        TableSnapshot(
            local.name,
            local.headers,
            tuple(merged_rows[key] for key in order),
            types=local.types,
            source="csv",
        )
    )
