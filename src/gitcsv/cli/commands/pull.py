"""gitcsv pull command: integrate remote Google Sheet changes into local CSV files."""

from __future__ import annotations

from typing import Annotated, Optional

import typer

from gitcsv.cli.commands.common import get_active_config, get_sheets_gateway
from gitcsv.cli.ui import console, err_console
from gitcsv.core.planner import decide_operation
from gitcsv.services.sync_engine import apply_table, scan_tables


def pull_command(
    tables: Annotated[
        Optional[list[str]],
        typer.Argument(help="Specific table name(s) to pull."),
    ] = None,
    all_tables: Annotated[
        bool,
        typer.Option("--all", "-a", help="Pull all configured tables."),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Force overwrite local CSV changes."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Preview changes without writing."),
    ] = False,
    accept_schema: Annotated[
        bool,
        typer.Option("--accept-schema", help="Accept remote column additions/removals."),
    ] = False,
) -> None:
    """Pull changes from Google Sheets into local CSV files."""
    config = get_active_config()

    if not all_tables and not tables:
        err_console.print("[red]Error: Specify table name(s) or use -a / --all.[/red]")
        raise typer.Exit(code=1)

    gateway = get_sheets_gateway(config)

    # 1. Resolve selected table mappings
    if all_tables:
        selected_mappings = list(config.tables)
    else:
        selected_mappings = []
        for name in (tables or []):
            mapping = config.find_mapping(name)
            if mapping is None:
                err_console.print(f"[red]Error: Unknown table '{name}'.[/red]")
                raise typer.Exit(code=1)
            selected_mappings.append(mapping)

    # 2. Preflight scan & plan
    with console.status("[bold green]Planning pull operation...[/bold green]"):
        plans = scan_tables(config, gateway, selected_mappings)

    # 3. Evaluate decisions
    decisions = [
        decide_operation(plan, "pull", force=force, accept_schema=accept_schema)
        for plan in plans
    ]

    # Check for blocking errors
    blocked = [d for d in decisions if not d.allowed]
    if blocked:
        err_console.print(f"\n[bold red]Pull Blocked ({len(blocked)} table(s)):[/bold red]")
        for d in blocked:
            err_console.print(f"  • [bold]{d.table}[/bold]:")
            for err in d.errors:
                err_console.print(f"    - {err}")
        err_console.print("\n[dim]Fix blockers, inspect with 'gitcsv diff', or re-run with --force / --accept-schema[/dim]")
        raise typer.Exit(code=1)

    # 4. Execute writes
    console.print(f"Pulling [bold]{len(plans)}[/bold] table(s) from Google Sheets{' [DRY-RUN]' if dry_run else ''}...")

    applied_count = 0
    for mapping, plan, decision in zip(selected_mappings, plans, decisions, strict=True):
        if not plan.has_diff and not force:
            console.print(f"  [green]=[/green] {plan.name}: already up to date")
            continue

        with console.status(f"[bold cyan]Writing {plan.name}...[/bold cyan]"):
            result = apply_table(config, gateway, mapping, decision, plan, dry_run=dry_run)

        if dry_run:
            console.print(f"  [yellow]~[/yellow] {plan.name}: preview ok (writes: CSV yes)")
        else:
            console.print(f"  [bold green]✔[/bold green] {plan.name}: updated local CSV (op: {result.operation_id[:8]})")
            applied_count += 1

    if not dry_run:
        console.print(f"\n[bold green]Done![/bold green] Successfully pulled {applied_count} table(s).")
