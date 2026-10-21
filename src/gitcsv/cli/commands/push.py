"""gitcsv push command: publish local CSV changes to Google Sheets."""

from __future__ import annotations

from typing import Annotated, Optional

import typer

from gitcsv.cli.commands.common import get_active_config, get_sheets_gateway
from gitcsv.cli.ui import console, err_console
from gitcsv.core.planner import decide_operation
from gitcsv.services.sync_engine import apply_many_tables, apply_table, scan_tables


def push_command(
    tables: Annotated[
        Optional[list[str]],
        typer.Argument(help="Specific table name(s) to push."),
    ] = None,
    all_tables: Annotated[
        bool,
        typer.Option("--all", "-a", help="Push all configured tables."),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Force overwrite remote changes."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Preview changes without writing."),
    ] = False,
    accept_schema: Annotated[
        bool,
        typer.Option("--accept-schema", help="Accept column additions/removals/reordering."),
    ] = False,
) -> None:
    """Push local CSV changes to remote Google Sheets."""
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
    with console.status("[bold green]Planning push operation...[/bold green]"):
        plans = scan_tables(config, gateway, selected_mappings)

    # 3. Evaluate decisions
    decisions = [
        decide_operation(plan, "push", force=force, accept_schema=accept_schema)
        for plan in plans
    ]

    # Check for blocking errors
    blocked = [d for d in decisions if not d.allowed]
    if blocked:
        err_console.print(f"\n[bold red]Push Blocked ({len(blocked)} table(s)):[/bold red]")
        for d in blocked:
            err_console.print(f"  • [bold]{d.table}[/bold]:")
            for err in d.errors:
                err_console.print(f"    - {err}")
        err_console.print("\n[dim]Fix blockers, inspect with 'gitcsv diff', or re-run with --force / --accept-schema[/dim]")
        raise typer.Exit(code=1)

    # 4. Execute writes
    console.print(f"Pushing [bold]{len(plans)}[/bold] table(s) to Google Sheets{' [DRY-RUN]' if dry_run else ''}...")

    if len(selected_mappings) > 1 and not dry_run:
        triplets = list(zip(selected_mappings, decisions, plans, strict=True))
        with console.status(f"[bold cyan]Bulk pushing {len(plans)} tables via batch API...[/bold cyan]"):
            results = apply_many_tables(config, gateway, triplets, dry_run=dry_run)
        for r in results:
            console.print(f"  [bold green]✔[/bold green] {r.plan.name}: updated and verified (op: {r.operation_id[:8]})")
        console.print(f"\n[bold green]Done![/bold green] Successfully bulk pushed {len(results)} table(s).")
        return

    applied_count = 0
    for mapping, plan, decision in zip(selected_mappings, plans, decisions, strict=True):
        if not plan.has_diff and not force:
            console.print(f"  [green]=[/green] {plan.name}: already up to date")
            continue

        with console.status(f"[bold cyan]Writing {plan.name}...[/bold cyan]"):
            result = apply_table(config, gateway, mapping, decision, plan, dry_run=dry_run)

        if dry_run:
            console.print(f"  [yellow]~[/yellow] {plan.name}: preview ok (writes: Sheet yes)")
        else:
            console.print(f"  [bold green]✔[/bold green] {plan.name}: updated and verified (op: {result.operation_id[:8]})")
            applied_count += 1

    if not dry_run:
        console.print(f"\n[bold green]Done![/bold green] Successfully pushed {applied_count} table(s).")
