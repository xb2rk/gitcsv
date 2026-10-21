"""gitcsv log command: show operation history and journal."""

from __future__ import annotations

from typing import Annotated, Optional

import typer
from rich.table import Table

from gitcsv.cli.commands.common import get_active_config
from gitcsv.cli.ui import console
from gitcsv.storage.state_db import StateDB


def log_command(
    table: Annotated[
        Optional[str],
        typer.Argument(help="Optional table name to filter history."),
    ] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", "-l", help="Number of history records to show."),
    ] = 20,
) -> None:
    """Show history of committed and pending sync operations."""
    config = get_active_config()

    with StateDB(config.project_root, read_only=True) as state_db:
        records = state_db.get_history(limit=limit)

    if table:
        records = [r for r in records if r.table == table]

    if not records:
        console.print("[dim]No operation history found in .gitcsv/state.db.[/dim]")
        return

    table_view = Table(
        title="gitcsv Operation Log",
        show_header=True,
        header_style="bold cyan",
        border_style="dim",
    )
    table_view.add_column("Operation ID", style="bold")
    table_view.add_column("Kind", justify="center")
    table_view.add_column("Table", style="cyan")
    table_view.add_column("Status", justify="center")
    table_view.add_column("Timestamp", style="dim")

    for record in records:
        status_style = "bold green" if record.status == "committed" else "bold yellow"
        table_view.add_row(
            record.id[:12],
            record.kind.upper(),
            record.table,
            f"[{status_style}]{record.status}[/{status_style}]",
            record.started_at[:19].replace("T", " "),
        )

    console.print(table_view)
