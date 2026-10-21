"""gitcsv sync command: three-way merge between local CSV, remote Sheets, and BASE."""

from __future__ import annotations

import sys
from typing import Annotated, Optional

import typer
from rich.prompt import Prompt

from gitcsv.cli.commands.common import get_active_config, get_sheets_gateway
from gitcsv.cli.ui import console, err_console
from gitcsv.core.merger import decide_sync
from gitcsv.services.sync_engine import apply_table, scan_tables


def sync_command(
    tables: Annotated[
        Optional[list[str]],
        typer.Argument(help="Specific table name(s) to 3-way sync."),
    ] = None,
    all_tables: Annotated[
        bool,
        typer.Option("--all", "-a", help="Sync all configured tables."),
    ] = False,
    ours: Annotated[
        bool,
        typer.Option("--ours", help="In case of cell conflicts, prefer local CSV values."),
    ] = False,
    theirs: Annotated[
        bool,
        typer.Option("--theirs", help="In case of cell conflicts, prefer remote Sheet values."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Preview 3-way merge without writing."),
    ] = False,
) -> None:
    """Perform an intelligent 3-way merge between local CSV, remote Sheets, and BASE."""
    config = get_active_config()

    if not all_tables and not tables:
        err_console.print("[red]Error: Specify table name(s) or use -a / --all.[/red]")
        raise typer.Exit(code=1)

    if ours and theirs:
        err_console.print("[red]Error: Cannot specify both --ours and --theirs.[/red]")
        raise typer.Exit(code=1)

    strategy = "ours" if ours else "theirs" if theirs else "manual"
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
    with console.status("[bold green]Planning 3-way sync...[/bold green]"):
        plans = scan_tables(config, gateway, selected_mappings)

    # 3. Compute 3-way decisions
    decisions = [decide_sync(plan, strategy=strategy) for plan in plans]

    # Check for blocking errors / conflicts
    blocked = [d for d in decisions if not d.allowed]

    # Interactive conflict resolution prompt
    if blocked and strategy == "manual" and sys.stdin.isatty():
        console.print(f"\n[bold yellow]⚡ {len(blocked)} table(s) have cell conflicts during 3-way merge:[/bold yellow]")
        for d in blocked:
            console.print(f"  • [bold]{d.table}[/bold]: {d.errors[0]}")

        choice = Prompt.ask(
            "\n[bold green]? Choose conflict resolution strategy[/bold green]",
            choices=["ours", "theirs", "abort"],
            default="abort",
        )
        if choice in ("ours", "theirs"):
            strategy = choice
            decisions = [decide_sync(plan, strategy=strategy) for plan in plans]
            blocked = [d for d in decisions if not d.allowed]

    if blocked:
        err_console.print(f"\n[bold red]Sync Blocked ({len(blocked)} table(s)):[/bold red]")
        for d in blocked:
            err_console.print(f"  • [bold]{d.table}[/bold]:")
            for err in d.errors:
                err_console.print(f"    - {err}")
        err_console.print("\n[dim]Use 'gitcsv sync --ours' or 'gitcsv sync --theirs' to resolve cell conflicts.[/dim]")
        raise typer.Exit(code=1)

    # 4. Execute writes
    console.print(f"Syncing [bold]{len(plans)}[/bold] table(s){' [DRY-RUN]' if dry_run else ''}...")

    applied_count = 0
    for mapping, plan, decision in zip(selected_mappings, plans, decisions, strict=True):
        if not plan.has_diff:
            console.print(f"  [green]=[/green] {plan.name}: already in sync")
            continue

        with console.status(f"[bold cyan]Writing {plan.name}...[/bold cyan]"):
            result = apply_table(config, gateway, mapping, decision, plan, dry_run=dry_run)

        if dry_run:
            console.print(f"  [yellow]~[/yellow] {plan.name}: preview ok")
        else:
            console.print(f"  [bold green]✔[/bold green] {plan.name}: 3-way merge completed (op: {result.operation_id[:8]})")
            applied_count += 1

    if not dry_run:
        console.print(f"\n[bold green]Done![/bold green] Successfully synced {applied_count} table(s).")
