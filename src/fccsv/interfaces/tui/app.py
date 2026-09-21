"""lazygit-style dashboard: tables left, cell diff right, status bar bottom.

All transfers run in threads via `Backend` (same orchestration as the
CLI); the UI never blocks on the network. Quota-heavy auto-fetch is
opt-in (`w`)."""

from __future__ import annotations

import asyncio
import time
from functools import partial
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header

from fccsv.adapters.sheets import SheetsGateway
from fccsv.app import auth as auth_mod
from fccsv.app import sync as sync_mod
from fccsv.domain.errors import FccsvError
from fccsv.interfaces import cli
from fccsv.interfaces.tui.backend import Backend, DashboardRow
from fccsv.interfaces.tui.modals import (
    HELP_LINES,
    AuthGate,
    ConflictModal,
    InitGate,
    KeysModal,
    ReportScreen,
)
from fccsv.interfaces.tui.panels import DiffPanel, StatusBar, TablesPanel

AUTO_FETCH_SECONDS = 60.0


class FccsvApp(App[None]):
    CSS = """
    #panels { height: 1fr; }
    #tables { width: 44; }
    #diff { width: 1fr; }
    #status { height: 1; }
    """

    BINDINGS = [
        Binding("P", "push", "Push"),
        Binding("p", "pull", "Pull"),
        Binding("f", "fetch", "Fetch"),
        Binding("r", "refresh", "Refresh"),
        Binding("o", "only_keys", "Partial"),
        Binding("A", "toggle_schema", "Schema"),
        Binding("i", "setup", "Setup"),
        Binding("l", "show_log", "Log"),
        Binding("D", "show_doctor", "Doctor"),
        Binding("v", "validate", "Validate"),
        Binding("w", "toggle_watch", "Watch"),
        Binding("F1", "show_help", "Help"),
        Binding("?", "show_help", "Help"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, root: Path | None = None, gateway: SheetsGateway | None = None) -> None:
        super().__init__()
        resolved = cli.resolve_root(str(root) if root is not None else None)
        config = cli.load_config(resolved)
        ctx = cli.build_context(resolved, config)
        if gateway is None:
            # Never surprise-launch the browser from the constructor: only
            # attempt a live gateway when a token already exists. Otherwise
            # start offline and let the startup gate drive login explicitly.
            token = auth_mod.resolve_token_file(None, project_root=resolved)
            if ctx.config.active.spreadsheet_id and token.exists():
                import argparse as _argparse

                args = _argparse.Namespace(credentials=None, token=None, offline=False)
                try:
                    gateway = cli.make_gateway(ctx, args)
                except FccsvError as exc:
                    from fccsv.adapters.sheets import InMemorySheetsGateway

                    gateway = InMemorySheetsGateway()
                    self._startup_warning = str(exc)
                else:
                    self._startup_warning = ""
            else:
                from fccsv.adapters.sheets import InMemorySheetsGateway

                gateway = InMemorySheetsGateway()
                self._startup_warning = ""
        else:
            self._startup_warning = ""
        self._backend = Backend(ctx, gateway)
        self._rows: list[DashboardRow] = []
        self._current_tab: str | None = None
        self._only: set[str] | None = None
        self._allow_schema = False
        self._busy = False
        self._last_fetch: float | None = None
        self._last_note = ""
        self._gate_gen = 0
        self._watch_timer: object = None
        self._client_origin = ""
        try:
            self._client_origin = self._backend.client_origin()
        except Exception:
            self._client_origin = "client: unknown"

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            with Horizontal(id="panels"):
                yield TablesPanel(id="tables")
                yield DiffPanel(id="diff")
            yield StatusBar(id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "fccsv"
        self.sub_title = self._backend.spreadsheet_id or "offline"
        if self._startup_warning:
            self.notify(f"offline mode: {self._startup_warning}", severity="warning")
        tables = self.query_one("#tables", TablesPanel)
        tables.focus()
        self.run_worker(self._startup())

    # ---------------------------------------------------------------- startup

    async def _startup(self) -> None:
        """First-run onboarding: auth gate -> init gate -> dashboard.
        Returning users (token + sheet) land straight on the dashboard."""
        state = await asyncio.to_thread(self._backend.startup_state)
        self._client_origin = state.client_origin
        self._render_status()
        # Generation guard: a late answer from a superseded gate is dropped,
        # so duplicate pushes can never fork a second login/onboard flow.
        self._gate_gen += 1
        gen = self._gate_gen
        if not state.has_client or not state.has_token:
            self.push_screen(
                AuthGate(
                    origin=state.client_origin,
                    can_login=state.has_client,
                    root=str(state.root),
                    token=str(state.token_file),
                ),
                partial(self._on_auth_gate, gen=gen),
            )
        elif not state.has_sheet:
            self.push_screen(InitGate(), partial(self._on_init_gate, gen=gen))
        else:
            await self._refresh(fetch_first=False)

    def _on_auth_gate(self, result: str | None, gen: int) -> None:
        if gen != self._gate_gen:
            return  # stale gate
        if result == "login":
            self.run_worker(self._do_login())
        elif result == "quit" or result is None:
            self.exit()
        else:  # "offline" / Esc
            self._last_note = "offline: login skipped"
            self.run_worker(self._refresh(fetch_first=False))

    async def _do_login(self) -> None:
        if self._busy:
            return
        self._busy = True
        self._render_status()
        try:
            _, text = await asyncio.to_thread(self._backend.login)
        except Exception as exc:
            self._busy = False
            self.notify(f"login failed: {exc}", severity="error")
            await self._startup()
            return
        self._busy = False
        self.notify(text or "logged in")
        await self._startup()

    def _on_init_gate(self, result: str | None, gen: int) -> None:
        if gen != self._gate_gen:
            return  # stale gate
        if result == "quit" or result is None:
            self.exit()
        elif result == "offline":
            self._last_note = "offline: no sheet connected"
            self.run_worker(self._refresh(fetch_first=False))
        else:
            self.run_worker(self._do_onboard(result))

    async def _do_onboard(self, url_or_id: str) -> None:
        if self._busy:
            return
        self._busy = True
        self._render_status()
        try:
            _, text = await asyncio.to_thread(self._backend.onboard_init, url_or_id)
        except Exception as exc:
            self._busy = False
            self.notify(f"setup failed: {exc}", severity="error")
            await self._startup()
            return
        self._busy = False
        self.sub_title = self._backend.spreadsheet_id or "offline"
        self.notify(text or "sheet connected")
        await self._refresh(fetch_first=False)

    # ---------------------------------------------------------------- state

    @property
    def current_tab(self) -> str | None:
        return self._current_tab

    def select_tab(self, tab: str | None) -> None:
        """Point actions + diff at one table (UI events and tests share this)."""
        tabs = [r.plan.tab for r in self._rows]
        self._current_tab = tab if tab in tabs else (tabs[0] if tabs else None)
        self._show_current_diff()
        self._render_status()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self.select_tab(str(event.row_key.value))

    def _row_by_tab(self, tab: str) -> DashboardRow | None:
        for row in self._rows:
            if row.plan.tab == tab:
                return row
        return None

    def _show_current_diff(self) -> None:
        diff = self.query_one("#diff", DiffPanel)
        if self._current_tab is None:
            diff.show_lines(["no tables mapped (run init)"])
            return
        row = self._row_by_tab(self._current_tab)
        if row is None:
            diff.show_lines(["(table gone — refreshing)"])
            return
        diff.show_lines(self._backend.diff_lines(row))

    def _render_status(self) -> None:
        parts = [self._client_origin]
        parts.append(
            f"sheet: {self._backend.spreadsheet_id}"
            if self._backend.spreadsheet_id
            else "sheet: offline"
        )
        if self._last_fetch is None:
            parts.append("last fetch: never")
        else:
            parts.append(f"last fetch: {int(time.monotonic() - self._last_fetch)}s ago")
        if self._only:
            parts.append(f"only: {','.join(sorted(self._only))}")
        if self._allow_schema:
            parts.append("schema: ALLOW (dev-only)")
        if self._watch_timer is not None:
            parts.append("watch: on")
        if self._busy:
            parts.append("working…")
        if self._last_note:
            parts.append(self._last_note)
        self.query_one("#status", StatusBar).set_text(" | ".join(parts))

    # ---------------------------------------------------------------- IO

    async def _refresh(self, fetch_first: bool) -> None:
        if self._busy:
            return
        self._busy = True
        self._render_status()
        try:
            rows, note = await asyncio.to_thread(self._backend.refresh, fetch_first)
        except FccsvError as exc:
            self.notify(f"refresh failed: {exc}", severity="error")
            self._busy = False
            self._render_status()
            return
        self._rows = rows
        if fetch_first:
            self._last_fetch = time.monotonic()
            self._last_note = note
        panel = self.query_one("#tables", TablesPanel)
        panel.set_rows(rows)
        if self._current_tab not in [r.plan.tab for r in rows]:
            self._current_tab = rows[0].plan.tab if rows else None
        self._show_current_diff()
        self._busy = False
        self._render_status()

    async def _sync_flow(self, direction: str, *, force: bool = False) -> None:
        if self._busy:
            self.notify("busy — wait for the current op", severity="warning")
            return
        tabs = [self._current_tab] if self._current_tab else None
        targets = tabs or [r.plan.tab for r in self._rows]
        if not targets:
            self.notify("nothing mapped", severity="warning")
            return
        by_tab = {r.plan.tab: r.plan for r in self._rows}
        conflicted = [t for t in targets if t in by_tab and sync_mod.plan_conflicted(by_tab[t])]
        if conflicted and not force:
            lines = []
            for tab in conflicted:
                row = self._row_by_tab(tab)
                if row is not None:
                    lines.extend(self._backend.diff_lines(row))
            self.push_screen(
                ConflictModal(direction, conflicted, lines),
                lambda result: self._on_conflict(result, direction, targets),
            )
            return
        await self._do_sync(direction, targets, force=force)

    def _on_conflict(self, result: bool | None, direction: str, targets: list[str]) -> None:
        if result:
            self.run_worker(self._do_sync(direction, targets, force=True))

    async def _do_sync(self, direction: str, targets: list[str], *, force: bool) -> None:
        self._busy = True
        self._render_status()
        try:
            rc, text = await asyncio.to_thread(
                self._backend.sync,
                direction,
                targets,
                force=force,
                only=self._only,
                exclude=None,
                allow_schema_change=self._allow_schema,
            )
        except FccsvError as exc:
            self.notify(f"{direction} failed: {exc}", severity="error")
            self._busy = False
            self._render_status()
            return
        self.notify(text or f"{direction} done (rc={rc})")
        self._busy = False
        await self._refresh(fetch_first=False)

    # ---------------------------------------------------------------- actions

    async def action_push(self) -> None:
        await self._sync_flow("push")

    async def action_pull(self) -> None:
        await self._sync_flow("pull")

    async def action_fetch(self) -> None:
        await self._refresh(fetch_first=True)
        self.notify(self._last_note or "fetch done")

    async def action_refresh(self) -> None:
        await self._refresh(fetch_first=False)

    async def action_only_keys(self) -> None:
        current = ",".join(sorted(self._only)) if self._only else ""
        self.push_screen(KeysModal(current), self._on_only_keys)

    def _on_only_keys(self, result: str | None) -> None:
        if result is None:
            return
        picked = {p.strip() for p in result.split(",") if p.strip()}
        self._only = picked or None
        self.notify(f"partial scope: {','.join(sorted(picked))}" if picked else "full sync")
        self._render_status()

    async def action_toggle_schema(self) -> None:
        self._allow_schema = not self._allow_schema
        self.notify("schema changes ALLOWED (dev-only)" if self._allow_schema else "schema strict")
        self._render_status()

    async def action_setup(self) -> None:
        """Reconnect on demand: switch sheet (init + first pull) anytime."""
        if self._busy:
            self.notify("busy — wait for the current op", severity="warning")
            return
        self._gate_gen += 1
        self.push_screen(
            InitGate(self._backend.spreadsheet_id),
            partial(self._on_init_gate, gen=self._gate_gen),
        )

    async def action_show_log(self) -> None:
        text = await asyncio.to_thread(self._backend.log_text, self._current_tab, 50)
        lines = text.splitlines() or ["no sync history yet"]
        self.push_screen(ReportScreen("Sync log (local journal)", lines))

    async def action_show_doctor(self) -> None:
        rc, text = await asyncio.to_thread(self._backend.doctor_text)
        lines = text.splitlines() or ["(no output)"]
        _ = rc
        self.push_screen(ReportScreen("Doctor", lines))

    async def action_validate(self) -> None:
        tabs = [self._current_tab] if self._current_tab else None
        rc, text = await asyncio.to_thread(self._backend.validate_text, tabs)
        lines = text.splitlines() or ["(no output)"]
        title = "Validate" + ("" if rc == 0 else " — issues found")
        self.push_screen(ReportScreen(title, lines))

    async def action_toggle_watch(self) -> None:
        if self._watch_timer is not None:
            timer = self._watch_timer
            self._watch_timer = None
            stop = getattr(timer, "stop", None)
            if callable(stop):
                stop()
            self.notify("auto-fetch off")
        else:
            self._watch_timer = self.set_interval(AUTO_FETCH_SECONDS, self._auto_fetch)
            self.notify(f"auto-fetch every {int(AUTO_FETCH_SECONDS)}s (spends quota)")
        self._render_status()

    def _auto_fetch(self) -> None:
        self.run_worker(self._refresh(fetch_first=True))

    async def action_show_help(self) -> None:
        self.push_screen(ReportScreen("fccsv TUI keys", list(HELP_LINES)))
