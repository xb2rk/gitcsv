"""Recovery and Undo service for interrupted or accidental operations."""

from __future__ import annotations

from pathlib import Path

from gitcsv.config.schema import GitCsvConfig
from gitcsv.storage.csv_store import write_csv_table
from gitcsv.storage.lock import ProjectLock
from gitcsv.storage.sheets_api import SheetsGateway
from gitcsv.storage.state_db import OperationRecord, StateDB


class RecoveryError(Exception):
    """Raised when an undo or recovery operation cannot be performed safely."""


def undo_table(
    config: GitCsvConfig,
    table_name: str,
    gateway: SheetsGateway,
    *,
    dry_run: bool = False,
) -> OperationRecord:
    """Revert the latest committed operation for a table using its recorded before-images."""
    root = config.project_root

    with StateDB(root, read_only=True) as state_db:
        latest = state_db.get_latest_operation(table_name)

    if latest is None:
        raise RecoveryError(f"No committed operation found to undo for table '{table_name}'.")

    if latest.before_local is None and latest.before_remote is None:
        raise RecoveryError(f"Operation {latest.id} has no before-images recorded; cannot undo.")

    mapping = config.find_mapping(table_name)
    if mapping is None:
        raise RecoveryError(f"No configured mapping found for table '{table_name}'.")

    if dry_run:
        return latest

    csv_path = config.csv_dir / mapping.csv_file

    with ProjectLock(root):
        # 1. Restore local CSV if before_local existed
        if latest.before_local is not None:
            write_csv_table(csv_path, latest.before_local)

        # 2. Restore remote Sheet if before_remote existed
        if latest.before_remote is not None:
            sheet_matrix = [list(latest.before_remote.headers)]
            for row in latest.before_remote.rows:
                sheet_matrix.append(list(row))
            gateway.write_values(table_name, sheet_matrix)

        # 3. Update BASE in StateDB
        with StateDB(root) as state_db:
            if latest.before_local is not None:
                state_db.save_base(table_name, latest.before_local)
            else:
                state_db.delete_base(table_name)

    return latest


def recover_operation(
    config: GitCsvConfig,
    op_id: str,
    action: str,  # "continue" or "restore"
    gateway: SheetsGateway,
) -> None:
    """Recover an interrupted operation by either finishing it or rolling back."""
    root = config.project_root

    with StateDB(root) as state_db:
        pending_list = state_db.get_pending_operations()
        target = next((op for op in pending_list if op.id == op_id), None)

    if target is None:
        raise RecoveryError(f"No pending operation found with ID: {op_id}")

    mapping = config.find_mapping(target.table)
    if mapping is None:
        raise RecoveryError(f"Table '{target.table}' is no longer in configuration.")

    csv_path = config.csv_dir / mapping.csv_file

    with ProjectLock(root):
        if action == "continue":
            # Finish applying the after images
            if target.after_local is not None:
                write_csv_table(csv_path, target.after_local)
            if target.after_remote is not None:
                sheet_matrix = [list(target.after_remote.headers)]
                for row in target.after_remote.rows:
                    sheet_matrix.append(list(row))
                gateway.write_values(target.table, sheet_matrix)

            with StateDB(root) as state_db:
                committed_base = target.after_local or target.after_remote
                assert committed_base is not None
                state_db.commit_operation(target.id, committed_base)

        elif action == "restore":
            # Revert back to before images
            if target.before_local is not None:
                write_csv_table(csv_path, target.before_local)
            if target.before_remote is not None:
                sheet_matrix = [list(target.before_remote.headers)]
                for row in target.before_remote.rows:
                    sheet_matrix.append(list(row))
                gateway.write_values(target.table, sheet_matrix)

            with StateDB(root) as state_db:
                state_db.cancel_operation(target.id, "Restored by operator")
