"""Revert the latest committed table operation using its saved before images."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fccsv3.adapters.csv_file import read_csv_table, write_csv_table
from fccsv3.adapters.project_lock import ProjectWriteLock
from fccsv3.adapters.sheet_values import snapshot_from_sheet_values, values_for_sheet
from fccsv3.adapters.state_db import (
    OperationRecord,
    StateDB,
)
from fccsv3.application.apply import (
    OperationInterrupted,
    SheetValuesGateway,
    _assert_inputs_unchanged,
    _csv_path,
    _same_csv,
    _same_sheet,
    _verify,
)
from fccsv3.application.scan import TableMapping
from fccsv3.domain.model import TableSnapshot
from fccsv3.domain.operation import OperationDecision
from fccsv3.domain.validate import validate_snapshot


@dataclass(frozen=True, slots=True)
class UndoPlan:
    source: OperationRecord | None
    local: TableSnapshot | None
    remote: TableSnapshot | None
    errors: tuple[str, ...] = ()
    operation_id: str | None = None

    @property
    def allowed(self) -> bool:
        return self.source is not None and not self.errors


def plan_undo(
    root: Path,
    csv_dir: Path,
    mapping: TableMapping,
    gateway: SheetValuesGateway,
    *,
    source_operation_id: str | None = None,
) -> UndoPlan:
    """Only the latest committed operation may be undone without losing history."""

    if not (root / ".fccsv3" / "state.sqlite3").is_file():
        return UndoPlan(None, None, None, ("no v3 operation history for this project",))
    with StateDB(root, read_only=True) as state:
        if state.pending():
            return UndoPlan(None, None, None, ("recover the pending operation first",))
        latest = state.latest_committed(mapping.tab)
        source = (
            state.get(source_operation_id)
            if source_operation_id is not None else latest
        )
    if source is None or source.table != mapping.tab or source.status != "committed":
        return UndoPlan(None, None, None, ("committed operation not found for table",))
    if latest is None or latest.id != source.id:
        return UndoPlan(
            source, None, None,
            ("only the latest committed operation for a table can be undone",),
        )
    path = _csv_path(csv_dir, mapping)
    local = read_csv_table(path, name=mapping.tab)
    values = gateway.read_values((mapping.tab,)).get(mapping.tab)
    if values is None:
        return UndoPlan(source, local, None, ("Sheet tab is missing",))
    remote = snapshot_from_sheet_values(
        mapping.tab, values, column_map=dict(mapping.column_map)
    )
    if any(
        issue.severity == "error"
        for snapshot in (local, remote)
        for issue in validate_snapshot(snapshot)
    ):
        return UndoPlan(source, local, remote, ("CSV or Sheet is invalid; inspect first",))
    if not _same_csv(local, source.after_local) or not _same_sheet(
        remote, source.after_remote
    ):
        return UndoPlan(
            source, local, remote,
            ("CSV or Sheet changed after this operation; inspect diff first",),
        )
    return UndoPlan(source, local, remote)


def undo_operation(
    root: Path,
    csv_dir: Path,
    mapping: TableMapping,
    gateway: SheetValuesGateway,
    *,
    source_operation_id: str | None = None,
    dry_run: bool = False,
) -> UndoPlan:
    preview = plan_undo(
        root, csv_dir, mapping, gateway,
        source_operation_id=source_operation_id,
    )
    if dry_run or not preview.allowed:
        return preview
    assert preview.source is not None
    assert preview.local is not None and preview.remote is not None
    source = preview.source
    decision = OperationDecision(
        "undo", mapping.tab, source.before_local, source.before_remote,
        write_local=not _same_csv(preview.local, source.before_local),
        write_remote=not _same_sheet(preview.remote, source.before_remote),
    )
    path = _csv_path(csv_dir, mapping)
    with ProjectWriteLock(root):
        with StateDB(root) as state:
            operation_id = state.reserve(
                decision, preview.local, preview.remote,
                expected_base=state.base_fingerprint(mapping.tab),
                source_operation_id=source.id,
            )
            try:
                _assert_inputs_unchanged(
                    path, mapping, gateway, preview.local, preview.remote
                )
            except Exception as error:
                state.cancel(operation_id, str(error))
                raise
            try:
                if decision.write_remote:
                    gateway.write_values(
                        mapping.tab,
                        values_for_sheet(
                            source.before_remote,
                            column_map=dict(mapping.column_map),
                        ),
                    )
                if decision.write_local:
                    write_csv_table(path, source.before_local)
                _verify(path, mapping, gateway, decision)
                if (
                    source.before_local.headers == source.before_remote.headers
                    and source.before_local.rows == source.before_remote.rows
                ):
                    state.commit(operation_id, source.before_local)
                else:
                    state.commit_unbased(operation_id)
            except Exception as error:
                state.annotate_failure(operation_id, str(error))
                raise OperationInterrupted(operation_id, error) from error
    return UndoPlan(source, preview.local, preview.remote, operation_id=operation_id)
