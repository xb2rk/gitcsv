"""Apply one reviewed v3 operation and verify both sides before BASE advances."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from fccsv3.adapters.csv_file import read_csv_table, write_csv_table
from fccsv3.adapters.project_lock import ProjectWriteLock
from fccsv3.adapters.sheet_values import snapshot_from_sheet_values, values_for_sheet
from fccsv3.adapters.state_db import StateConflict, StateDB, read_bases, snapshot_fingerprint
from fccsv3.application.scan import SheetValuesReader, TableMapping, scan_tables
from fccsv3.domain.model import TablePlan, TableSnapshot
from fccsv3.domain.operation import OperationDecision, OperationKind, decide_operation
from fccsv3.domain.validate import validate_snapshot


class SheetValuesGateway(SheetValuesReader, Protocol):
    def write_values(self, tab: str, values: list[list[str]]) -> None: ...


class OperationInterrupted(RuntimeError):
    """The recovery journal is pending after a failed or uncertain write."""

    def __init__(self, operation_id: str, cause: Exception) -> None:
        super().__init__(
            f"operation {operation_id} is pending after {cause}; "
            f"inspect it with fccsv3 recover {operation_id}"
        )
        self.operation_id = operation_id


@dataclass(frozen=True, slots=True)
class ApplyResult:
    plan: TablePlan
    decision: OperationDecision
    operation_id: str | None = None
    verified: bool = False


def _csv_path(csv_dir: Path, mapping: TableMapping) -> Path:
    root = csv_dir.resolve()
    path = (root / mapping.csv_file).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"CSV path escapes configured directory: {mapping.csv_file}")
    return path


def _same_csv(left: TableSnapshot, right: TableSnapshot) -> bool:
    return (
        left.headers == right.headers
        and left.types == right.types
        and left.rows == right.rows
    )


def _same_sheet(left: TableSnapshot, right: TableSnapshot) -> bool:
    return left.headers == right.headers and left.rows == right.rows


def _verify(
    csv_path: Path,
    mapping: TableMapping,
    gateway: SheetValuesReader,
    decision: OperationDecision,
) -> None:
    assert decision.local_after is not None and decision.remote_after is not None
    actual_local = read_csv_table(csv_path, name=mapping.tab)
    values = gateway.read_values((mapping.tab,)).get(mapping.tab)
    if values is None:
        raise RuntimeError(f"Sheet tab disappeared during verification: {mapping.tab}")
    actual_remote = snapshot_from_sheet_values(
        mapping.tab, values, column_map=dict(mapping.column_map)
    )
    for snapshot in (actual_local, actual_remote):
        errors = tuple(
            item.message for item in validate_snapshot(snapshot)
            if item.severity == "error"
        )
        if errors:
            raise RuntimeError(
                f"{snapshot.source} verification found invalid data: {', '.join(errors)}"
            )
    if not _same_csv(actual_local, decision.local_after):
        raise RuntimeError("CSV values differ from the planned result")
    if not _same_sheet(actual_remote, decision.remote_after):
        raise RuntimeError("Sheet values differ from the planned result")


def _assert_inputs_unchanged(
    csv_path: Path,
    mapping: TableMapping,
    gateway: SheetValuesReader,
    local: TableSnapshot,
    remote: TableSnapshot,
) -> None:
    """Catch edits made after planning and before the first external write."""

    actual_local = read_csv_table(csv_path, name=mapping.tab)
    values = gateway.read_values((mapping.tab,)).get(mapping.tab)
    if values is None:
        raise StateConflict("Sheet tab disappeared after planning; scan again")
    actual_remote = snapshot_from_sheet_values(
        mapping.tab, values, column_map=dict(mapping.column_map)
    )
    if not _same_csv(actual_local, local) or not _same_sheet(actual_remote, remote):
        raise StateConflict("CSV or Sheet changed after planning; scan again")
    if any(
        item.severity == "error"
        for snapshot in (actual_local, actual_remote)
        for item in validate_snapshot(snapshot)
    ):
        raise StateConflict("CSV or Sheet became invalid after planning; scan again")


def plan_write(
    root: Path,
    csv_dir: Path,
    mapping: TableMapping,
    reader: SheetValuesReader,
    kind: OperationKind,
    *,
    force: bool = False,
    accept_schema: bool = False,
    new_types: Mapping[str, str] | None = None,
) -> ApplyResult:
    """Use only v3 BASE for writes; legacy BASE remains read-only shadow context."""

    return plan_many_writes(
        root, csv_dir, (mapping,), reader, kind,
        force=force, accept_schema=accept_schema, new_types=new_types,
    )[0]


def plan_many_writes(
    root: Path,
    csv_dir: Path,
    mappings: Sequence[TableMapping],
    reader: SheetValuesReader,
    kind: OperationKind,
    *,
    force: bool = False,
    accept_schema: bool = False,
    new_types: Mapping[str, str] | None = None,
) -> tuple[ApplyResult, ...]:
    """Batch-read remote tables and preflight every decision before writes."""

    for mapping in mappings:
        _csv_path(csv_dir, mapping)
    plans = scan_tables(csv_dir, tuple(mappings), reader, bases=read_bases(root))
    decisions = [
        decide_operation(
            plan, kind, force=force, accept_schema=accept_schema,
            new_types=new_types,
        )
        for plan in plans
    ]
    remote_tabs = tuple(
        plan.name for plan, decision in zip(plans, decisions, strict=True)
        if decision.allowed and decision.write_remote
    )
    blockers: dict[str, str] = {}
    if remote_tabs:
        batch_check = getattr(reader, "formula_blockers", None)
        if batch_check is not None:
            blockers = batch_check(remote_tabs)
        else:
            check = getattr(reader, "formula_blocker", None)
            if check is not None:
                blockers = {
                    tab: reason for tab in remote_tabs if (reason := check(tab))
                }
    return tuple(
        ApplyResult(
            plan,
            OperationDecision(kind, plan.name, None, None, errors=(blockers[plan.name],))
            if plan.name in blockers else decision,
        )
        for plan, decision in zip(plans, decisions, strict=True)
    )


def apply_table(
    root: Path,
    csv_dir: Path,
    mapping: TableMapping,
    gateway: SheetValuesGateway,
    kind: OperationKind,
    *,
    force: bool = False,
    accept_schema: bool = False,
    new_types: Mapping[str, str] | None = None,
    dry_run: bool = False,
) -> ApplyResult:
    """A pending journal record precedes every external write."""

    result = plan_write(
        root, csv_dir, mapping, gateway, kind,
        force=force, accept_schema=accept_schema, new_types=new_types,
    )
    decision, plan = result.decision, result.plan
    if dry_run or not decision.allowed:
        return result
    assert plan.local is not None and plan.remote is not None
    assert decision.local_after is not None and decision.remote_after is not None
    csv_path = _csv_path(csv_dir, mapping)
    expected_base = snapshot_fingerprint(plan.base) if plan.base else None
    with ProjectWriteLock(root):
        with StateDB(root) as state:
            operation_id = state.reserve(
                decision, plan.local, plan.remote, expected_base=expected_base
            )
            try:
                _assert_inputs_unchanged(
                    csv_path, mapping, gateway, plan.local, plan.remote
                )
            except Exception as error:
                state.cancel(operation_id, str(error))
                raise
            try:
                if decision.write_remote:
                    gateway.write_values(
                        mapping.tab,
                        values_for_sheet(
                            decision.remote_after, column_map=dict(mapping.column_map)
                        ),
                    )
                if decision.write_local:
                    write_csv_table(csv_path, decision.local_after)
                _verify(csv_path, mapping, gateway, decision)
                state.commit(operation_id, decision.local_after)
            except Exception as error:
                state.annotate_failure(operation_id, str(error))
                raise OperationInterrupted(operation_id, error) from error
        return ApplyResult(plan, decision, operation_id, verified=True)
