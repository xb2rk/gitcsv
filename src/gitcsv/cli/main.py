"""Main Typer CLI entrypoint for gitcsv with Git-style grouped help panels."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Annotated, Optional

import typer

from gitcsv import __version__
from gitcsv.cli.commands.auth import auth_app
from gitcsv.cli.commands.diff import diff_command
from gitcsv.cli.commands.doctor import doctor_command
from gitcsv.cli.commands.init import init_command
from gitcsv.cli.commands.log import log_command
from gitcsv.cli.commands.pull import pull_command
from gitcsv.cli.commands.push import push_command
from gitcsv.cli.commands.restore import restore_command
from gitcsv.cli.commands.status import status_command
from gitcsv.cli.commands.sync import sync_command
from gitcsv.cli.commands.undo import undo_command
from gitcsv.cli.ui import console

app = typer.Typer(
    name="gitcsv",
    help="Git-like version control and synchronization between CSV files and Google Sheets.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

# --- Register Commands with Categorized Rich Help Panels ---

app.command(
    "init",
    rich_help_panel="start a working area",
    help="Initialize gitcsv configuration and discover table mappings.",
)(init_command)

app.command(
    "status",
    rich_help_panel="inspect status and differences",
    help="Show the synchronization status of local CSVs and Google Sheets.",
)(status_command)

app.command(
    "diff",
    rich_help_panel="inspect status and differences",
    help="Show cell-by-cell and schema differences between CSV and Sheets.",
)(diff_command)

app.command(
    "log",
    rich_help_panel="inspect status and differences",
    help="Show history of committed and pending sync operations.",
)(log_command)

app.command(
    "push",
    rich_help_panel="transfer and synchronize data",
    help="Publish local CSV changes to remote Google Sheets.",
)(push_command)

app.command(
    "pull",
    rich_help_panel="transfer and synchronize data",
    help="Integrate remote Google Sheet changes into local CSV files.",
)(pull_command)

app.command(
    "sync",
    rich_help_panel="transfer and synchronize data",
    help="Perform an intelligent 3-way merge between local CSV, Sheets, and BASE.",
)(sync_command)

app.command(
    "restore",
    rich_help_panel="undo and recovery",
    help="Discard local CSV edits and restore from the last synced BASE state.",
)(restore_command)

app.command(
    "undo",
    rich_help_panel="undo and recovery",
    help="Revert the latest committed push, pull, or sync operation for a table.",
)(undo_command)

app.add_typer(
    auth_app,
    name="auth",
    rich_help_panel="system and diagnosis",
)

app.command(
    "doctor",
    rich_help_panel="system and diagnosis",
    help="Run an automated diagnostic audit on configuration, auth, CSVs, and formulas.",
)(doctor_command)


@app.callback(invoke_without_command=True)
def main_callback(
    version: Annotated[
        Optional[bool],
        typer.Option("--version", "-v", help="Show gitcsv version and exit.", is_eager=True),
    ] = None,
    work_dir: Annotated[
        Optional[Path],
        typer.Option("-C", "--work-dir", help="Run as if gitcsv was started in <path>.", is_eager=True),
    ] = None,
) -> None:
    """Git-like version control and synchronization between CSV files and Google Sheets."""
    if version:
        console.print(f"[bold cyan]gitcsv[/bold cyan] version [bold]{__version__}[/bold]")
        raise typer.Exit()

    if work_dir is not None:
        if not work_dir.is_dir():
            console.print(f"[red]Error: Working directory '{work_dir}' does not exist.[/red]")
            raise typer.Exit(code=1)
        os.chdir(work_dir)


def run() -> None:
    """Command-line entrypoint with Git-style 'gitcsv help <subcommand>' rewriting."""
    args = list(sys.argv[1:])

    # Rewrite: gitcsv help <cmd> -> gitcsv <cmd> --help
    if len(args) >= 1 and args[0] == "help":
        if len(args) == 1:
            args = ["--help"]
        else:
            args = [args[1], "--help"]

    app(args)


if __name__ == "__main__":
    run()
