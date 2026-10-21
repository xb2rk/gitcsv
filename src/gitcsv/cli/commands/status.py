"""gitcsv status command: inspect synchronization state."""

from __future__ import annotations

from typing import Annotated, Optional

import typer

from gitcsv.cli.commands.common import get_active_config, get_sheets_gateway
from gitcsv.cli.ui import console, err_console, print_short_status, print_status_table
from gitcsv.services.sync_engine import scan_tables


def status_command(
    tables: Annotated[
        Optional[list[str]],
        typer.Argument(help="Optional table names to inspect. If omitted, checks all tables."),
    ] = None,
    short: Annotated[
        bool,
        typer.Option("--short", "-s", help="Give output in short-format like git status -s."),
    ] = False,
) -> None:
    """Show the synchronization status of local CSVs against remote Sheets."""
    config = get_active_config()
    gateway = get_sheets_gateway(config)

    # 1. Resolve selected table mappings
    if tables:
        selected_mappings = []
        for name in tables:
            mapping = config.find_mapping(name)
            if mapping is None:
                err_console.print(f"[red]Error: Unknown table or path '{name}'.[/red]")
                raise typer.Exit(code=1)
            selected_mappings.append(mapping)
    else:
        selected_mappings = list(config.tables)

    if not selected_mappings:
        console.print("[dim]No table mappings configured. Run 'gitcsv init' to add tables.[/dim]")
        return

    # 2. Batch scan tables
    with console.status("[bold green]Scanning local CSVs and Google Sheets...[/bold green]"):
        plans = scan_tables(config, gateway, selected_mappings)

    # 3. Print output
    if short:
        print_short_status(plans)
    else:
        print_status_table(plans)
