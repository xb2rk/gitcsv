"""gitcsv undo command: revert the latest committed operation for a table."""

from __future__ import annotations

from typing import Annotated

import typer

from gitcsv.cli.commands.common import get_active_config, get_sheets_gateway
from gitcsv.cli.ui import console, err_console
from gitcsv.services.recovery import RecoveryError, undo_table


def undo_command(
    table: Annotated[str, typer.Argument(help="Table name to revert.")],
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Preview undo without applying changes."),
    ] = False,
) -> None:
    """Revert the latest committed push, pull, or sync operation for a table."""
    config = get_active_config()
    gateway = get_sheets_gateway(config)

    try:
        latest = undo_table(config, table, gateway, dry_run=dry_run)
        if dry_run:
            console.print(f"[yellow]~[/yellow] Preview undo operation [bold]{latest.id[:8]}[/bold] for table [bold]{table}[/bold].")
        else:
            console.print(f"[bold green]✔[/bold green] Successfully reverted operation [bold]{latest.id[:8]}[/bold] for table [bold]{table}[/bold].")
    except RecoveryError as err:
        err_console.print(f"[red]Undo Failed:[/red] {err}")
        raise typer.Exit(code=1) from err
