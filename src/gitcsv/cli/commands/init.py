"""gitcsv init command: interactive wizard and CLI setup."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.prompt import Confirm, Prompt

from gitcsv.cli.ui import console, err_console
from gitcsv.services.init_wizard import initialize_project


def init_command(
    spreadsheet: Annotated[
        Optional[str],
        typer.Argument(
            help="Google Spreadsheet full URL or raw ID.",
            show_default=False,
        ),
    ] = None,
    csv_dir: Annotated[
        str,
        typer.Option("--csv-dir", "-d", help="Directory where CSV files are located."),
    ] = "Assets/CSV",
    mode: Annotated[
        str,
        typer.Option("--mode", "-m", help="Auth mode: 'oauth' or 'service_account'."),
    ] = "oauth",
    credentials: Annotated[
        Optional[Path],
        typer.Option("--credentials", "-c", help="Path to credentials JSON file."),
    ] = None,
    paste_json: Annotated[
        Optional[str],
        typer.Option("--json", help="Directly paste credentials JSON content."),
    ] = None,
) -> None:
    """Initialize gitcsv configuration and discover table mappings."""
    root = Path.cwd()

    console.print("\n[bold cyan]🚀 gitcsv Project Initialization[/bold cyan]\n")

    # 1. Interactive prompt if spreadsheet is not provided
    if not spreadsheet:
        spreadsheet = Prompt.ask(
            "[bold green]? Enter Google Spreadsheet URL or ID[/bold green]"
        )

    if not spreadsheet:
        err_console.print("[red]Error: Google Spreadsheet URL or ID is required.[/red]")
        raise typer.Exit(code=1)

    # 2. Interactive prompt for CSV directory if default doesn't exist
    target_csv_dir = root / csv_dir
    if not target_csv_dir.is_dir():
        console.print(f"[dim]Directory '{csv_dir}' not found.[/dim]")
        csv_dir = Prompt.ask(
            "[bold green]? Where are your CSV files stored?[/bold green]",
            default="Assets/CSV",
        )

    # 3. Interactive prompt for credentials if not supplied
    cred_file_str = str(credentials) if credentials else ".gitcsv/credentials.json"
    if not credentials and not paste_json:
        if mode == "service_account":
            cred_input = Prompt.ask(
                "[bold green]? Path to Service Account JSON key (or press Enter to paste later)[/bold green]",
                default="",
            )
            if cred_input.strip():
                cred_file_str = cred_input.strip()

    # 4. Run initialization service
    with console.status("[bold green]Discovering tables and setting up project...[/bold green]"):
        result = initialize_project(
            project_root=root,
            spreadsheet_url_or_id=spreadsheet,
            csv_dir_rel=csv_dir,
            auth_mode=mode,
            credentials_file=cred_file_str,
            credentials_json=paste_json,
        )

    console.print(f"[bold green]✔[/bold green] Spreadsheet ID: [bold]{result['spreadsheet_id']}[/bold]")
    console.print(f"[bold green]✔[/bold green] CSV Directory: [bold]{result['csv_dir']}[/bold]")
    console.print(f"[bold green]✔[/bold green] Discovered [bold]{result['tables_count']}[/bold] tables mapped.")
    console.print(f"[bold green]✔[/bold green] Created configuration: [cyan]{result['config_file']}[/cyan]")

    if result["ignored_patterns"]:
        console.print(f"[bold green]✔[/bold green] Configured .gitignore to protect secrets.")

    console.print("\n[bold]🎉 Setup complete![/bold] You can now run:")
    console.print("  • [cyan]gitcsv status[/cyan]     - Inspect CSV vs Sheet synchronization")
    console.print("  • [cyan]gitcsv push --all[/cyan] - Push local CSV changes to Google Sheets\n")
