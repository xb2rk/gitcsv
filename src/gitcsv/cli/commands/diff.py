"""gitcsv diff command: inspect cell-by-cell and schema differences."""

from __future__ import annotations

from typing import Annotated, Optional

import typer

from gitcsv.cli.commands.common import get_active_config, get_sheets_gateway
from gitcsv.cli.ui import console, err_console, print_diff
from gitcsv.services.sync_engine import scan_tables


def diff_command(
    tables: Annotated[
        Optional[list[str]],
        typer.Argument(help="Specific table name(s) to diff. Default: all tables with diffs."),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", "-l", help="Maximum change details per table."),
    ] = 50,
) -> None:
    """Show differences between local CSV files and remote Google Sheets."""
    config = get_active_config()
    gateway = get_sheets_gateway(config)

    # 1. Resolve selected table mappings
    if tables:
        selected_mappings = []
        for name in tables:
            mapping = config.find_mapping(name)
            if mapping is None:
                err_console.print(f"[red]Error: Unknown table '{name}'.[/red]")
                raise typer.Exit(code=1)
            selected_mappings.append(mapping)
    else:
        selected_mappings = list(config.tables)

    # 2. Batch scan tables
    with console.status("[bold green]Calculating diffs...[/bold green]"):
        plans = scan_tables(config, gateway, selected_mappings)

    # 3. Print diffs
    diff_count = 0
    for plan in plans:
        if plan.has_diff or plan.has_errors:
            print_diff(plan, limit=limit)
            diff_count += 1

    if diff_count == 0:
        console.print("[bold green]✔ Local CSV files and Google Sheets are completely in sync.[/bold green]")
