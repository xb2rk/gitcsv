"""Resolve an interrupted write using its durable before/after images."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from fccsv3.adapters.csv_file import read_csv_table, write_csv_table
from fccsv3.adapters.project_lock import ProjectWriteLock
from fccsv3.adapters.sheet_values import snapshot_from_sheet_values, values_for_sheet
from fccsv3.adapters.state_db import OperationRecord, StateDB
from fccsv3.application.apply import (
    OperationInterrupted,
    SheetValuesGateway,
    _csv_path,
    _same_csv,
    _same_sheet,
    _verify,
)
from fccsv3.application.scan import TableMapping
from fccsv3.domain.model import TableSnapshot
from fccsv3.domain.operation import OperationDecision

RecoveryChoice = Literal["continue", "restore"]


@dataclass(frozen=True, slots=True)
class RecoveryInspection:
    record: OperationRecord
    local: str
    remote: str


def _side_state(
    actual: TableSnapshot,
    before: TableSnapshot,
    after: TableSnapshot,
    equal: Callable[[TableSnapshot, TableSnapshot], bool],
) -> str:
    if equal(actual, after):
        return "after"
    if equal(actual, before):
        return "before"
    return "other"


def inspect_recovery(
    root: Path,
    csv_dir: Path,
    mapping: TableMapping,
    gateway: SheetValuesGateway,
    operation_id: str,
) -> RecoveryInspection:
    if not (root / ".fccsv3" / "state.sqlite3").is_file():
        raise ValueError("no v3 state database; there is no operation to recover")
    with StateDB(root, read_only=True) as state:
        record = state.get(operation_id)
    if record is None or record.status != "pending":
        raise ValueError(f"pending operation not found: {operation_id}")
    if record.table != mapping.tab:
        raise ValueError(f"operation {operation_id} belongs to {record.table}")
    path = _csv_path(csv_dir, mapping)
    local = read_csv_table(path, name=mapping.tab)
    values = gateway.read_values((mapping.tab,)).get(mapping.tab)
    if values is None:
        raise RuntimeError(f"Sheet tab is missing: {mapping.tab}")
    remote = snapshot_from_sheet_values(
        mapping.tab, values, column_map=dict(mapping.column_map)
    )
    return RecoveryInspection(
        record,
        _side_state(local, record.before_local, record.after_local, _same_csv),
        _side_state(remote, record.before_remote, record.after_remote, _same_sheet),
    )


def recover_operation(
    root: Path,
    csv_dir: Path,
    mapping: TableMapping,
    gateway: SheetValuesGateway,
    operation_id: str,
    choice: RecoveryChoice,
    *,
    force: bool = False,
) -> RecoveryInspection:
    """Require an explicit choice; unknown current values need explicit force."""

    with ProjectWriteLock(root):
        inspection = inspect_recovery(root, csv_dir, mapping, gateway, operation_id)
        record = inspection.record
        if "other" in (inspection.local, inspection.remote) and not force:
            raise ValueError(
                "a side differs from both saved versions; inspect current values "
                "before recovery, then repeat with --force if intentional"
            )
        local_target = (
            record.after_local if choice == "continue" else record.before_local
        )
        remote_target = (
            record.after_remote if choice == "continue" else record.before_remote
        )
        target_state = "after" if choice == "continue" else "before"
        decision = OperationDecision(
            record.kind, record.table, local_target, remote_target
        )
        with StateDB(root) as state:
            try:
                if inspection.remote != target_state:
                    gateway.write_values(
                        mapping.tab,
                        values_for_sheet(
                            remote_target, column_map=dict(mapping.column_map)
                        ),
                    )
                if inspection.local != target_state:
                    write_csv_table(_csv_path(csv_dir, mapping), local_target)
                _verify(_csv_path(csv_dir, mapping), mapping, gateway, decision)
                if choice == "continue":
                    if (
                        record.kind == "undo"
                        and (local_target.headers != remote_target.headers
                             or local_target.rows != remote_target.rows)
                    ):
                        state.commit_unbased(operation_id)
                    else:
                        state.commit(operation_id, local_target)
                else:
                    state.mark_restored(operation_id)
            except Exception as error:
                state.annotate_failure(operation_id, str(error))
                raise OperationInterrupted(operation_id, error) from error
        return inspection
