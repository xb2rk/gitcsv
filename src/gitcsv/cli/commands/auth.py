"""gitcsv auth command: manage Google OAuth and Service Account credentials."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer

from gitcsv.cli.commands.common import get_active_config
from gitcsv.cli.ui import console, err_console
from gitcsv.storage.oauth import (
    AuthError,
    get_credentials,
    interactive_oauth_login,
    resolve_credentials_file,
)

auth_app = typer.Typer(help="Manage Google authentication credentials and tokens.")


@auth_app.command("status")
def auth_status() -> None:
    """Check whether Google API authentication is configured and active."""
    config = get_active_config(require_spreadsheet_id=False)

    try:
        creds = get_credentials(config.auth, config.project_root)
        if creds.valid:
            console.print("[bold green]✔ Authentication is active and valid.[/bold green]")
            console.print(f"  Mode: [bold]{config.auth.mode}[/bold]")
            if config.auth.mode == "oauth":
                console.print(f"  Token file: [cyan]{config.auth.token_file}[/cyan]")
            else:
                console.print(f"  Key file: [cyan]{config.auth.credentials_file}[/cyan]")
        else:
            console.print("[yellow]⚠ Credentials found but expired. Run 'gitcsv auth login'.[/yellow]")
    except AuthError as err:
        err_console.print(f"[red]✖ Not authenticated:[/red] {err}")
        raise typer.Exit(code=1) from err


@auth_app.command("login")
def auth_login(
    credentials: Annotated[
        Optional[Path],
        typer.Option("--credentials", "-c", help="Path to OAuth client secret JSON."),
    ] = None,
    json_content: Annotated[
        Optional[str],
        typer.Option("--json", help="Directly paste client secret JSON content."),
    ] = None,
    global_auth: Annotated[
        bool,
        typer.Option("--global", "-g", help="Store token globally in ~/.gitcsv/token.json for all projects."),
    ] = False,
) -> None:
    """Sign in to Google via OAuth 2.0 browser authorization flow."""
    config = get_active_config(require_spreadsheet_id=False)

    resolved_client = resolve_credentials_file(
        config.project_root, credentials or config.auth.credentials_file
    )
    if not json_content and (not resolved_client or not resolved_client.is_file()):
        err_console.print(
            "[red]Error: OAuth client secret file not found.[/red]\n"
            "Drop your downloaded 'client_secret_*.json' or 'credentials.json' into the project folder,\n"
            "or specify its path with: gitcsv auth login --credentials <path>"
        )
        raise typer.Exit(code=1)

    client_path = resolved_client or (config.project_root / config.auth.credentials_file)
    if global_auth or not (config.project_root / ".gitcsv").is_dir():
        token_path = Path.home() / ".gitcsv" / "token.json"
    else:
        token_path = config.project_root / config.auth.token_file

    console.print(f"[bold cyan]Using credentials: [dim]{client_path.name}[/dim][/bold cyan]")
    console.print("[bold cyan]Starting Google OAuth Sign-in...[/bold cyan]")
    try:
        interactive_oauth_login(client_path, token_path, client_json=json_content)
        console.print(f"[bold green]✔ Successfully signed in![/bold green] Token saved to [cyan]{token_path}[/cyan]")
    except Exception as err:
        err_console.print(f"[red]OAuth sign-in failed:[/red] {err}")
        raise typer.Exit(code=1) from err
