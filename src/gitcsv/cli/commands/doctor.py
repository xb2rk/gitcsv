"""gitcsv doctor command: audit project integrity and diagnose issues."""

from __future__ import annotations

import typer

from gitcsv.cli.commands.common import get_active_config, get_sheets_gateway
from gitcsv.cli.ui import console, err_console
from gitcsv.core.validator import validate_snapshot
from gitcsv.storage.csv_store import read_csv_table
from gitcsv.storage.state_db import StateDB


def doctor_command() -> None:
    """Run an automated diagnostic check on configuration, auth, CSV files, and Sheets."""
    console.print("\n[bold cyan]🩺 Running gitcsv Doctor Diagnostic...[/bold cyan]\n")
    issues_found = 0

    # 1. Config Check
    try:
        config = get_active_config()
        console.print(f"[bold green]✔[/bold green] Project configuration loaded ({len(config.tables)} tables mapped)")
    except Exception as err:
        err_console.print(f"[bold red]✖[/bold red] Configuration check failed: {err}")
        raise typer.Exit(code=1) from err

    # 2. Local CSV Integrity Check
    csv_errors = 0
    for mapping in config.tables:
        csv_path = config.csv_dir / mapping.csv_file
        if not csv_path.is_file():
            console.print(f"[yellow]⚠[/yellow] CSV missing on disk: [dim]{mapping.csv_file}[/dim]")
            issues_found += 1
            continue

        snapshot = read_csv_table(csv_path, name=mapping.tab)
        diagnostics = validate_snapshot(snapshot)
        errors = [d for d in diagnostics if d.severity == "error"]
        if errors:
            console.print(f"[bold red]✖[/bold red] CSV '{mapping.csv_file}' has {len(errors)} formatting errors:")
            for err in errors:
                console.print(f"    • Line {err.line or '-'}: {err.message}")
            csv_errors += len(errors)
            issues_found += len(errors)

    if csv_errors == 0:
        console.print("[bold green]✔[/bold green] All local CSV files passed shape and type validation")

    # 3. Pending Operations Check
    with StateDB(config.project_root, read_only=True) as state_db:
        pending = state_db.get_pending_operations()

    if pending:
        console.print(f"[yellow]⚠[/yellow] Found {len(pending)} pending operation(s) in WAL requiring recovery:")
        for op in pending:
            console.print(f"    • Operation {op.id} ({op.table}): {op.error or 'Interrupted'}")
        issues_found += len(pending)
    else:
        console.print("[bold green]✔[/bold green] Transaction journal is clean (no pending operations)")

    # 4. Remote Sheet & Formula Check
    try:
        gateway = get_sheets_gateway(config)
        tabs = tuple(m.tab for m in config.tables)
        formula_blockers = gateway.formula_blockers(tabs)

        if formula_blockers:
            console.print(f"[bold red]✖[/bold red] Found formulas in {len(formula_blockers)} Google Sheet tab(s):")
            for tab, reason in formula_blockers.items():
                console.print(f"    • {tab}: {reason}")
            issues_found += len(formula_blockers)
        else:
            console.print("[bold green]✔[/bold green] Remote Google Sheets checked (no conflicting formulas)")
    except Exception as err:
        console.print(f"[yellow]⚠[/yellow] Skipped remote Google Sheets check: {err}")
        issues_found += 1

    # 5. Summary
    if issues_found == 0:
        console.print("\n[bold green]🎉 Everything looks healthy and ready to sync![/bold green]\n")
    else:
        console.print(f"\n[yellow]Doctor found {issues_found} warning(s) or issue(s).[/yellow]\n")
