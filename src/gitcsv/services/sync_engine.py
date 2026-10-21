"""Transactional synchronization engine orchestrating planning, writes, and verification."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from gitcsv.config.schema import GitCsvConfig, TableMapping
from gitcsv.core.models import (
    OperationDecision,
    OperationKind,
    TablePlan,
    TableSnapshot,
)
from gitcsv.core.planner import decide_operation, plan_table
from gitcsv.core.validator import validate_snapshot
from gitcsv.storage.csv_store import read_csv_table, write_csv_table
from gitcsv.storage.lock import ProjectLock
from gitcsv.storage.sheets_api import SheetsGateway
from gitcsv.storage.state_db import StateDB


class SyncError(Exception):
    """Raised when a preflight check or synchronization step fails."""


def _normalize_sheet_rows(raw_matrix: list[list[str]], col_count: int) -> tuple[tuple[str, ...], ...]:
    """Pad trailing missing cells in raw Sheet matrix rows to match col_count."""
    normalized: list[tuple[str, ...]] = []
    for raw_row in raw_matrix:
        padded = list(raw_row[:col_count]) + [""] * max(0, col_count - len(raw_row))
        normalized.append(tuple(padded))
    return tuple(normalized)


class OperationInterruptedError(Exception):
    """Raised when an external write failed or response was lost; needs recovery."""

    def __init__(self, operation_id: str, cause: Exception) -> None:
        super().__init__(
            f"Operation {operation_id} was interrupted: {cause}\n"
            f"Inspect and resolve with: gitcsv recover {operation_id}"
        )
        self.operation_id = operation_id


@dataclass(frozen=True, slots=True)
class ApplyResult:
    """The outcome of executing an operation on a table."""

    plan: TablePlan
    decision: OperationDecision
    operation_id: str | None = None
    verified: bool = False


def scan_tables(
    config: GitCsvConfig,
    gateway: SheetsGateway,
    mappings: Sequence[TableMapping],
) -> list[TablePlan]:
    """Batch-read local CSVs, remote Sheets, and BASE snapshots to generate plans."""
    # 1. Read all local CSV snapshots
    local_snapshots: dict[str, TableSnapshot | None] = {}
    for mapping in mappings:
        csv_path = config.csv_dir / mapping.csv_file
        if csv_path.is_file():
            local_snapshots[mapping.tab] = read_csv_table(csv_path, name=mapping.tab)
        else:
            local_snapshots[mapping.tab] = None

    # 2. Batch-read all remote Sheet values
    tabs = tuple(m.tab for m in mappings)
    remote_values_map = gateway.read_values(tabs)

    remote_snapshots: dict[str, TableSnapshot | None] = {}
    for mapping in mappings:
        raw_rows = remote_values_map.get(mapping.tab)
        if raw_rows and len(raw_rows) > 0:
            headers = tuple(h.strip() for h in raw_rows[0])
            col_count = len(headers)
            normalized_rows = _normalize_sheet_rows(raw_rows[1:], col_count)

            remote_snapshots[mapping.tab] = TableSnapshot(
                name=mapping.tab,
                headers=headers,
                types=(),
                rows=normalized_rows,
                source="sheet",
            )
        else:
            remote_snapshots[mapping.tab] = None

    # 3. Read recorded BASE snapshots from SQLite
    with StateDB(config.project_root, read_only=True) as state_db:
        bases = state_db.get_all_bases()

    # 4. Generate pure comparison plans
    plans: list[TablePlan] = []
    for mapping in mappings:
        local_snap = local_snapshots.get(mapping.tab)
        remote_snap = remote_snapshots.get(mapping.tab)
        base_snap = bases.get(mapping.tab)

        plan = plan_table(
            local_snap,
            remote_snap,
            name=mapping.tab,
            base=base_snap,
            preferred_key=mapping.preferred_key,
        )
        plans.append(plan)

    return plans


def apply_table(
    config: GitCsvConfig,
    gateway: SheetsGateway,
    mapping: TableMapping,
    decision: OperationDecision,
    plan: TablePlan,
    *,
    dry_run: bool = False,
) -> ApplyResult:
    """Execute a reviewed operation with full transactional journaling and verification."""
    if dry_run or not decision.allowed:
        return ApplyResult(plan=plan, decision=decision, operation_id=None, verified=False)

    csv_path = config.csv_dir / mapping.csv_file
    root = config.project_root

    # 1. Acquire project file lock to ensure single writer
    with ProjectLock(root):
        with StateDB(root) as state_db:
            # 2. Reserve operation in journal WAL
            op_id = state_db.reserve_operation(
                kind=decision.kind,
                table_name=mapping.tab,
                before_local=plan.local,
                before_remote=plan.remote,
                after_local=decision.local_after,
                after_remote=decision.remote_after,
            )

            # 3. Check for formula blockers if writing to Sheets
            if decision.write_remote:
                blockers = gateway.formula_blockers((mapping.tab,))
                if mapping.tab in blockers:
                    state_db.cancel_operation(op_id, blockers[mapping.tab])
                    raise SyncError(blockers[mapping.tab])

            # 4. Execute external writes
            try:
                # Write to Google Sheets
                if decision.write_remote and decision.remote_after:
                    sheet_matrix = [list(decision.remote_after.headers)]
                    for row in decision.remote_after.rows:
                        sheet_matrix.append(list(row))
                    gateway.write_values(mapping.tab, sheet_matrix)

                # Write to local CSV
                if decision.write_local and decision.local_after:
                    write_csv_table(csv_path, decision.local_after)

                # 5. Verify post-write state by reading back both sides
                if decision.write_local and decision.local_after:
                    verify_local = read_csv_table(csv_path, name=mapping.tab)
                    if verify_local.rows != decision.local_after.rows:
                        raise RuntimeError("Verification failed: written CSV rows do not match planned result")

                if decision.write_remote and decision.remote_after:
                    verify_remote_data = gateway.read_values((mapping.tab,)).get(mapping.tab, [])
                    col_count = len(decision.remote_after.headers)
                    verify_rows = _normalize_sheet_rows(verify_remote_data[1:], col_count) if verify_remote_data else ()
                    if verify_rows != decision.remote_after.rows:
                        raise RuntimeError("Verification failed: written Sheet values do not match planned result")

                # 6. Commit operation and advance BASE
                committed_base = decision.local_after or plan.local
                assert committed_base is not None
                state_db.commit_operation(op_id, committed_base)

                return ApplyResult(plan=plan, decision=decision, operation_id=op_id, verified=True)

            except Exception as err:
                state_db.fail_operation(op_id, str(err))
                raise OperationInterruptedError(op_id, err) from err


def apply_many_tables(
    config: GitCsvConfig,
    gateway: SheetsGateway,
    items: Sequence[tuple[TableMapping, OperationDecision, TablePlan]],
    *,
    dry_run: bool = False,
) -> list[ApplyResult]:
    """Execute operations across multiple tables using high-performance bulk batch API requests."""
    results: list[ApplyResult] = []
    if dry_run:
        return [
            ApplyResult(plan=plan, decision=decision, operation_id=None, verified=False)
            for _, decision, plan in items
        ]

    # Filter only allowed operations that actually have changes
    active_items = [
        (mapping, decision, plan)
        for mapping, decision, plan in items
        if decision.allowed and (decision.write_local or decision.write_remote)
    ]

    if not active_items:
        return results

    root = config.project_root

    with ProjectLock(root):
        with StateDB(root) as state_db:
            # 1. Batch formula check for all remote writes in 1 single API call
            remote_tabs = tuple(
                plan.name for _, decision, plan in active_items if decision.write_remote
            )
            if remote_tabs:
                blockers = gateway.formula_blockers(remote_tabs)
                if blockers:
                    first_tab = next(iter(blockers))
                    raise SyncError(blockers[first_tab])

            # 2. Reserve all operations in SQLite WAL
            reserved_ops: list[tuple[str, TableMapping, OperationDecision, TablePlan]] = []
            for mapping, decision, plan in active_items:
                op_id = state_db.reserve_operation(
                    kind=decision.kind,
                    table_name=mapping.tab,
                    before_local=plan.local,
                    before_remote=plan.remote,
                    after_local=decision.local_after,
                    after_remote=decision.remote_after,
                )
                reserved_ops.append((op_id, mapping, decision, plan))

            try:
                # 3. Bulk batch write to Google Sheets in 2 API calls (1 batchClear + 1 batchUpdate)
                remote_data: dict[str, list[list[str]]] = {}
                for _, mapping, decision, _ in reserved_ops:
                    if decision.write_remote and decision.remote_after:
                        matrix = [list(decision.remote_after.headers)]
                        for row in decision.remote_after.rows:
                            matrix.append(list(row))
                        remote_data[mapping.tab] = matrix

                if remote_data:
                    gateway.batch_write_values(remote_data)

                # 4. Write local CSV files to disk
                for _, mapping, decision, _ in reserved_ops:
                    if decision.write_local and decision.local_after:
                        csv_path = config.csv_dir / mapping.csv_file
                        write_csv_table(csv_path, decision.local_after)

                # 5. Batch verify remote values in 1 single batchGet call
                if remote_data:
                    verified_remote = gateway.read_values(tuple(remote_data.keys()))
                    for _, mapping, decision, _ in reserved_ops:
                        if decision.write_remote and decision.remote_after:
                            raw_rows = verified_remote.get(mapping.tab, [])
                            col_count = len(decision.remote_after.headers)
                            actual_rows = _normalize_sheet_rows(raw_rows[1:], col_count) if raw_rows else ()
                            if actual_rows != decision.remote_after.rows:
                                raise RuntimeError(f"Batch verification failed for table {mapping.tab}")

                # 6. Commit all operations and advance BASE in SQLite
                for op_id, _, decision, plan in reserved_ops:
                    committed_base = decision.local_after or plan.local
                    assert committed_base is not None
                    state_db.commit_operation(op_id, committed_base)
                    results.append(ApplyResult(plan=plan, decision=decision, operation_id=op_id, verified=True))

            except Exception as err:
                for op_id, _, _, _ in reserved_ops:
                    state_db.fail_operation(op_id, str(err))
                raise SyncError(f"Batch operation failed: {err}") from err

    return results
