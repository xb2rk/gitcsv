"""gitcsv restore command: discard local changes and restore from BASE."""

from __future__ import annotations

from typing import Annotated

import typer

from gitcsv.cli.commands.common import get_active_config
from gitcsv.cli.ui import console, err_console
from gitcsv.storage.csv_store import write_csv_table
from gitcsv.storage.state_db import StateDB


def restore_command(
    table: Annotated[str, typer.Argument(help="Table name to restore from BASE.")],
) -> None:
    """Discard local modifications in a CSV file and restore from the last synced BASE state."""
    config = get_active_config()
    mapping = config.find_mapping(table)

    if mapping is None:
        err_console.print(f"[red]Error: Unknown table '{table}'.[/red]")
        raise typer.Exit(code=1)

    with StateDB(config.project_root, read_only=True) as state_db:
        base = state_db.get_base(mapping.tab)

    if base is None:
        err_console.print(f"[red]Error: No BASE snapshot exists for '{table}'. Cannot restore.[/red]")
        raise typer.Exit(code=1)

    csv_path = config.csv_dir / mapping.csv_file
    write_csv_table(csv_path, base)

    console.print(f"[bold green]✔[/bold green] Restored [bold]{table}[/bold] to last synchronized BASE ({len(base.rows)} rows).")
