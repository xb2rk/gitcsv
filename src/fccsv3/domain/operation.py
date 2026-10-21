"""Choose a safe write outcome from a validated, read-only table plan."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from fccsv3.domain.merge import merge3
from fccsv3.domain.model import TablePlan, TableSnapshot

OperationKind = Literal["push", "pull", "sync", "undo"]


@dataclass(frozen=True, slots=True)
class OperationDecision:
    kind: OperationKind
    table: str
    local_after: TableSnapshot | None
    remote_after: TableSnapshot | None
    write_local: bool = False
    write_remote: bool = False
    errors: tuple[str, ...] = ()

    @property
    def allowed(self) -> bool:
        return not self.errors and self.local_after is not None and self.remote_after is not None


def _sheet_copy(snapshot: TableSnapshot) -> TableSnapshot:
    return TableSnapshot(snapshot.name, snapshot.headers, snapshot.rows, source="sheet")


def _same_values(left: TableSnapshot, right: TableSnapshot) -> bool:
    return left.headers == right.headers and left.rows == right.rows


def _blocked(kind: OperationKind, plan: TablePlan, *errors: str) -> OperationDecision:
    return OperationDecision(kind, plan.name, None, None, errors=tuple(errors))


def decide_operation(
    plan: TablePlan,
    kind: OperationKind,
    *,
    force: bool = False,
    accept_schema: bool = False,
    new_types: Mapping[str, str] | None = None,
) -> OperationDecision:
    """Force chooses a direction; it never bypasses shape or schema review."""

    local, remote, base = plan.local, plan.remote, plan.base
    if plan.has_errors:
        return _blocked(kind, plan, "validate both sides before writing")
    if local is None or remote is None:
        return _blocked(kind, plan, "both CSV and Sheet tab must exist before writing")
    if kind == "sync":
        if force:
            return _blocked(kind, plan, "sync has no force direction; use push or pull")
        if base is None and _same_values(local, remote):
            return OperationDecision(kind, plan.name, local, remote)
        result = merge3(plan)
        if not result.clean:
            return _blocked(kind, plan, *(issue.message for issue in result.conflicts))
        assert result.snapshot is not None
        if result.snapshot.headers != local.headers and not accept_schema:
            return _blocked(kind, plan, "schema differs; review and pass --accept-schema")
        return OperationDecision(
            kind,
            plan.name,
            result.snapshot,
            _sheet_copy(result.snapshot),
            write_local=(
                not _same_values(local, result.snapshot) or local.types != result.snapshot.types
            ),
            write_remote=not _same_values(remote, result.snapshot),
        )

    if local.headers != remote.headers and not accept_schema:
        return _blocked(kind, plan, "schema differs; review and pass --accept-schema")
    if kind == "push":
        if not _same_values(local, remote):
            if base is None and not force:
                return _blocked(
                    kind, plan,
                    "no trusted common BASE; review diff, then use push TABLE --force",
                )
            if base is not None and not _same_values(remote, base) and not force:
                return _blocked(
                    kind, plan,
                    "Sheet changed since BASE; use sync TABLE or push TABLE --force",
                )
            if any(header not in local.headers for header in remote.headers) and not force:
                return _blocked(
                    kind, plan,
                    "push removes a Sheet column; review and pass --force",
                )
        return OperationDecision(
            kind, plan.name, local, _sheet_copy(local),
            write_remote=not _same_values(local, remote),
        )

    if kind == "pull":
        if not _same_values(local, remote):
            if base is None and not force:
                return _blocked(
                    kind, plan,
                    "no trusted common BASE; review diff, then use pull TABLE --force",
                )
            if base is not None and not _same_values(local, base) and not force:
                return _blocked(
                    kind, plan,
                    "CSV changed since BASE; use sync TABLE or pull TABLE --force",
                )
            if any(header not in remote.headers for header in local.headers) and not force:
                return _blocked(
                    kind, plan,
                    "pull removes a CSV column; review and pass --force",
                )
        old_types = dict(zip(local.headers, local.types, strict=True))
        supplied = new_types or {}
        additions = tuple(header for header in remote.headers if header not in old_types)
        missing_types = tuple(header for header in additions if not supplied.get(header, "").strip())
        if missing_types:
            return _blocked(
                kind, plan,
                "new Sheet column(s) need CSV types: " + ", ".join(missing_types),
            )
        types = tuple(old_types.get(header, supplied.get(header, "")) for header in remote.headers)
        result = TableSnapshot(
            plan.name, remote.headers, remote.rows, types=types, source="csv"
        )
        return OperationDecision(
            kind, plan.name, result, remote,
            write_local=(not _same_values(local, result) or local.types != result.types),
        )
    raise ValueError(f"unknown operation: {kind}")
