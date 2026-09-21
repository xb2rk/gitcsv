"""TUI panels: table list (lazygit files pane), diff view, status bar.
Presentation only; all content comes from `Backend`."""

from __future__ import annotations

from textual.widgets import DataTable, RichLog, Static

from fccsv.interfaces.tui.backend import DashboardRow


def detail_of(row: DashboardRow) -> str:
    plan = row.plan
    counts = (
        f"+{len(plan.row_diff.added_keys)} "
        f"-{len(plan.row_diff.removed_keys)} "
        f"~{len(plan.row_diff.changed_keys)}"
    )
    flags = ""
    if plan.schema_diff.has_change:
        flags += " [schema]"
    if plan.local is not None and plan.remote is not None:
        from fccsv.app import sync as sync_mod

        if sync_mod.plan_conflicted(plan):
            flags += " [conflict]"
    if plan.validation_issues:
        flags += f" [{len(plan.validation_issues)} validation issue(s)]"
    if row.remote_changed and row.direction in ("<", "X"):
        flags += " [sheet moved]"
    return f"{counts}{flags}"


class TablesPanel(DataTable):  # type: ignore[type-arg]
    """Left pane: one row per table with its git-style direction marker."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.cursor_type = "row"

    def on_mount(self) -> None:
        self.add_column(" ", key="dir")
        self.add_column("Table", key="tab")
        self.add_column("Detail", key="detail")

    def set_rows(self, rows: list[DashboardRow]) -> None:
        self.clear()
        for row in rows:
            self.add_row(row.direction, row.plan.tab, detail_of(row), key=row.plan.tab)

    def tab_at(self, index: int) -> str | None:
        try:
            cells = self.get_row_at(index)
        except Exception:
            return None
        return str(cells[1]) if len(cells) > 1 else None


class DiffPanel(RichLog):
    """Right pane: cell-level diff for the selected table."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(highlight=False, markup=False, wrap=True, **kwargs)  # type: ignore[arg-type]

    def show_lines(self, lines: list[str]) -> None:
        self.clear()
        for line in lines:
            self.write(line)


class StatusBar(Static):
    """Bottom bar: identity, sheet, fetch age, active filters."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__("", **kwargs)  # type: ignore[arg-type]
        self.text = ""

    def set_text(self, text: str) -> None:
        self.text = text
        self.update(text)
