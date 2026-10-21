"""Rich terminal formatting, tables, diffs, and prompts for gitcsv."""

from __future__ import annotations

from typing import Sequence

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from gitcsv.core.models import TablePlan

console = Console()
err_console = Console(stderr=True)


def print_status_table(plans: Sequence[TablePlan]) -> None:
    """Print a rich formatted overview table of all table plans."""
    table = Table(
        title="gitcsv Table Status",
        show_header=True,
        header_style="bold cyan",
        border_style="dim",
    )

    table.add_column("Table", style="bold")
    table.add_column("Local (CSV)", justify="right")
    table.add_column("Remote (Sheet)", justify="right")
    table.add_column("Status", justify="center")
    table.add_column("Details", style="dim")

    for plan in plans:
        # Determine status badge style
        if plan.status == "in_sync":
            status_text = Text("In Sync", style="bold green")
        elif plan.status in ("local_changed", "remote_changed"):
            status_text = Text("Modified", style="bold yellow")
        elif plan.status in ("both_changed", "unbased_divergence"):
            status_text = Text("Diverged", style="bold red")
        elif plan.status == "missing_remote":
            status_text = Text("CSV Only", style="bold cyan")
        elif plan.status == "missing_local":
            status_text = Text("Sheet Only", style="bold magenta")
        else:
            status_text = Text("Invalid", style="bold red reverse")

        # Format details
        local_rows = str(len(plan.local.rows)) if plan.local else "-"
        remote_rows = str(len(plan.remote.rows)) if plan.remote else "-"

        details_parts: list[str] = []
        if plan.schema.has_change:
            details_parts.append("schema changed")
        if plan.rows.cells:
            details_parts.append(f"{len(plan.rows.cells)} changed cells")
        if plan.rows.only_local:
            details_parts.append(f"{len(plan.rows.only_local)} local-only rows")
        if plan.rows.only_remote:
            details_parts.append(f"{len(plan.rows.only_remote)} remote-only rows")
        if plan.rows.order_changed:
            details_parts.append("row order changed")

        details_str = ", ".join(details_parts) if details_parts else "Up to date"

        table.add_row(plan.name, local_rows, remote_rows, status_text, details_str)

    console.print(table)


def print_short_status(plans: Sequence[TablePlan]) -> None:
    """Print a concise status output matching git status -s."""
    for plan in plans:
        if plan.status == "in_sync":
            prefix = Text(" = ", style="green")
            desc = "in sync"
        elif plan.status == "local_changed":
            prefix = Text(" M ", style="yellow")
            desc = f"{len(plan.rows.cells)} cells modified"
        elif plan.status == "remote_changed":
            prefix = Text(" R ", style="yellow")
            desc = f"{len(plan.rows.cells)} remote cells changed"
        elif plan.status in ("both_changed", "unbased_divergence"):
            prefix = Text("UU ", style="bold red")
            desc = "DIVERGED / CONFLICT"
        elif plan.status == "missing_remote":
            prefix = Text("?? ", style="cyan")
            desc = "CSV only (new table)"
        elif plan.status == "missing_local":
            prefix = Text(" D ", style="magenta")
            desc = "Sheet only (missing CSV)"
        else:
            prefix = Text("!! ", style="bold red")
            desc = "Validation error"

        console.print(prefix + Text(f"{plan.name:<25} ", style="bold") + Text(desc, style="dim"))


def print_diff(plan: TablePlan, limit: int = 50) -> None:
    """Print colored cell and row diffs between local and remote snapshots."""
    header_text = Text()
    header_text.append(f"diff gitcsv://{plan.name}\n", style="bold")
    header_text.append(f"status: {plan.status} | identity: {plan.identity.mode if plan.identity else 'none'}", style="dim")
    console.print(Panel(header_text, expand=False))

    # 1. Schema diffs
    if plan.schema.only_local:
        console.print(f"[green]+ Local-only columns: {', '.join(plan.schema.only_local)}[/green]")
    if plan.schema.only_remote:
        console.print(f"[red]- Remote-only columns: {', '.join(plan.schema.only_remote)}[/red]")
    if plan.schema.reordered:
        console.print("[yellow]~ Columns reordered between local and remote[/yellow]")

    # 2. Row additions/deletions
    for key in plan.rows.only_local[:limit]:
        console.print(f"[green]+ CSV only row: {key}[/green]")
    for key in plan.rows.only_remote[:limit]:
        console.print(f"[red]- Sheet only row: {key}[/red]")

    # 3. Cell changes
    for delta in plan.rows.cells[:limit]:
        console.print(
            f"[yellow]~ Row {delta.row_key} | Col '{delta.column}':[/yellow] "
            f"[green]CSV={delta.local!r}[/green] vs [red]Sheet={delta.remote!r}[/red]"
        )

    total_changes = len(plan.rows.only_local) + len(plan.rows.only_remote) + len(plan.rows.cells)
    if total_changes > limit:
        console.print(f"[dim]... {total_changes - limit} more change(s) omitted (use --limit to expand)[/dim]")
