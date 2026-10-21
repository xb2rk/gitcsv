"""Guided terminal interface over the same plans and operations as the CLI."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fccsv3.adapters.state_db import read_bases, read_pending_summaries
from fccsv3.application.apply import (
    OperationInterrupted,
    SheetValuesGateway,
    apply_table,
    plan_write,
)
from fccsv3.application.legacy_config import load_legacy_project
from fccsv3.application.scan import TableMapping, scan_tables
from fccsv3.application.undo import plan_undo, undo_operation
from fccsv3.domain.model import TablePlan
from fccsv3.interfaces.command_text import table_command

Input = Callable[[str], str]
Output = Callable[[str], None]


def _command(kind: str, name: str, *, force: bool = False) -> str:
    return table_command(kind, name, force=force)


def _scan(root: Path, gateway: SheetValuesGateway) -> tuple[TablePlan, ...]:
    project = load_legacy_project(root)
    return scan_tables(
        project.csv_dir, project.mappings, gateway, bases=read_bases(root)
    )


def _show_diff(plan: TablePlan, output: Output) -> None:
    output(f"[{plan.name}] {plan.status}")
    for issue in plan.diagnostics:
        output(f"  {issue.severity.upper()}: {issue.message}")
    if plan.schema.has_change:
        output(f"  Schema: CSV {plan.schema.local_headers}; Sheet {plan.schema.remote_headers}")
    if plan.rows.only_local:
        output(f"  CSV-only rows: {len(plan.rows.only_local)}")
    if plan.rows.only_remote:
        output(f"  Sheet-only rows: {len(plan.rows.only_remote)}")
    if plan.rows.order_changed:
        output("  Row order differs")
    for cell in plan.rows.cells[:10]:
        output(f"  {cell.row} {cell.column}: CSV={cell.local!r}; Sheet={cell.remote!r}")
    if len(plan.rows.cells) > 10:
        output(f"  ... {len(plan.rows.cells) - 10} more changed cells")
    if not plan.has_diff and not plan.has_errors:
        output("  CSV and Sheet match")
    output(f"  CLI: {_command('diff', plan.name)}")


def _write(
    root: Path,
    csv_dir: Path,
    mapping: TableMapping,
    gateway: SheetValuesGateway,
    kind: str,
    input_fn: Input,
    output: Output,
) -> None:
    force = False
    preview = plan_write(root, csv_dir, mapping, gateway, kind)
    if not preview.decision.allowed:
        for error in preview.decision.errors:
            output(f"  BLOCKED: {error}")
        if kind in ("push", "pull") and any(
            "--force" in error for error in preview.decision.errors
        ):
            output(f"  CLI: {_command(kind, mapping.tab, force=True)}")
            if input_fn("Type FORCE to choose this direction: ").strip() == "FORCE":
                force = True
                preview = plan_write(root, csv_dir, mapping, gateway, kind, force=True)
        if not preview.decision.allowed:
            for error in preview.decision.errors:
                output(f"  BLOCKED: {error}")
            return
    decision = preview.decision
    output(
        f"  Preview: CSV write {'yes' if decision.write_local else 'no'}, "
        f"Sheet write {'yes' if decision.write_remote else 'no'}"
    )
    output(f"  CLI: {_command(kind, mapping.tab, force=force)}")
    if input_fn("Type APPLY to run this operation: ").strip() != "APPLY":
        output("  Cancelled; nothing written")
        return
    result = apply_table(root, csv_dir, mapping, gateway, kind, force=force)
    if not result.decision.allowed:
        output("  BLOCKED: inputs changed; review the plan again")
        return
    output(f"  Verified; operation {result.operation_id}")


def _undo(
    root: Path,
    csv_dir: Path,
    mapping: TableMapping,
    gateway: SheetValuesGateway,
    input_fn: Input,
    output: Output,
) -> None:
    preview = plan_undo(root, csv_dir, mapping, gateway)
    if not preview.allowed:
        for error in preview.errors:
            output(f"  BLOCKED: {error}")
        return
    assert preview.source is not None
    output(f"  Undo latest operation {preview.source.id}")
    output(f"  CLI: {_command('undo', mapping.tab)} --operation {preview.source.id}")
    if input_fn("Type UNDO to restore its before images: ").strip() != "UNDO":
        output("  Cancelled; nothing written")
        return
    result = undo_operation(root, csv_dir, mapping, gateway)
    if not result.allowed:
        for error in result.errors:
            output(f"  BLOCKED: {error}")
        return
    output(f"  Verified; undo operation {result.operation_id}")


def run_tui(
    root: Path,
    gateway: SheetValuesGateway,
    *,
    input_fn: Input = input,
    output: Output = print,
) -> int:
    """Guide one table at a time without capturing CLI output or exit codes."""

    root = root.resolve()
    project = load_legacy_project(root)
    mappings = {mapping.tab: mapping for mapping in project.mappings}
    output("fccsv3 | CSV and Google Sheets")
    output("Review a plan before each write. Type quit at any menu to leave.")
    try:
        while True:
            plans = _scan(root, gateway)
            output("\nTables:")
            for index, plan in enumerate(plans, start=1):
                output(f"  {index:2}. {plan.name}: {plan.status}")
            if (root / ".fccsv3" / "state.sqlite3").is_file():
                for operation_id, table in read_pending_summaries(root):
                    output(
                        f"  ERROR: pending {operation_id} for {table}; "
                        f"run fccsv3 recover {operation_id}"
                    )
            choice = input_fn("Table number/name, refresh, or quit: ").strip()
            if choice.lower() in ("quit", "q", "exit"):
                return 0
            if choice.lower() in ("refresh", "r", ""):
                continue
            if choice.isdigit() and 1 <= int(choice) <= len(plans):
                selected = plans[int(choice) - 1].name
            elif choice in mappings:
                selected = choice
            else:
                output(f"  ERROR: unknown table {choice!r}")
                continue
            mapping = mappings[selected]
            while True:
                action = input_fn(
                    f"{selected}> diff, plan push|pull|sync, "
                    "push|pull|sync, undo, back, quit: "
                ).strip().lower()
                if action in ("quit", "q", "exit"):
                    return 0
                if action in ("back", "b", ""):
                    break
                try:
                    if action == "diff":
                        current = next(plan for plan in _scan(root, gateway) if plan.name == selected)
                        _show_diff(current, output)
                    elif action.startswith("plan ") and action[5:] in (
                        "push", "pull", "sync"
                    ):
                        kind = action[5:]
                        preview = plan_write(root, project.csv_dir, mapping, gateway, kind)
                        output(f"  CLI: {_command('plan ' + kind, selected)}")
                        for error in preview.decision.errors:
                            output(f"  BLOCKED: {error}")
                        if preview.decision.allowed:
                            output(
                                f"  CSV write {preview.decision.write_local}; "
                                f"Sheet write {preview.decision.write_remote}"
                            )
                    elif action in ("push", "pull", "sync"):
                        _write(
                            root, project.csv_dir, mapping, gateway,
                            action, input_fn, output,
                        )
                    elif action == "undo":
                        _undo(root, project.csv_dir, mapping, gateway, input_fn, output)
                    else:
                        output(f"  ERROR: unknown action {action!r}")
                except OperationInterrupted as error:
                    output(f"  ERROR: {error}")
                except (OSError, ValueError, RuntimeError) as error:
                    output(f"  ERROR: {error}")
    except EOFError:
        output("\nSession ended")
        return 0
