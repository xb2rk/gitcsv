"""Common CLI helpers and Sheets gateway initialization."""

from __future__ import annotations

import typer

from gitcsv.cli.ui import err_console
from gitcsv.config.loader import ConfigError, load_config
from gitcsv.config.schema import GitCsvConfig
from gitcsv.storage.oauth import AuthError, get_credentials
from gitcsv.storage.sheets_api import GoogleSheetsClient, SheetsGateway


def get_active_config(*, require_spreadsheet_id: bool = True) -> GitCsvConfig:
    """Load configuration for current project or exit cleanly with guidance."""
    try:
        return load_config(require_spreadsheet_id=require_spreadsheet_id)
    except ConfigError as err:
        err_console.print(f"[bold red]Configuration Error:[/bold red] {err}")
        raise typer.Exit(code=1) from err


def get_sheets_gateway(config: GitCsvConfig) -> SheetsGateway:
    """Obtain authenticated Google Sheets API gateway."""
    try:
        creds = get_credentials(config.auth, config.project_root)
        return GoogleSheetsClient(config.spreadsheet_id, creds)
    except AuthError as err:
        err_console.print(f"[bold red]Authentication Error:[/bold red] {err}")
        raise typer.Exit(code=2) from err
    except Exception as err:
        err_console.print(f"[bold red]Connection Error:[/bold red] Failed to connect to Google Sheets: {err}")
        raise typer.Exit(code=2) from err
