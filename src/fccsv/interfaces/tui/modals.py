"""TUI modals: first-run auth/init gates, conflict confirm, `--only` key input,
text reports, help."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, RichLog

HELP_LINES = [
    "P push: local CSVs -> sheet (selected or all)",
    "p pull: sheet -> local CSVs, backs up first",
    "f fetch: read-only check whether the sheet moved",
    "r refresh: re-plan from disk + sheet",
    "o partial sync: set --only keys (empty clears)",
    "A toggle dev-only --allow-schema-change (default strict)",
    "i setup: connect/switch sheet (init + first pull)",
    "l sync log (local journal)",
    "D doctor report",
    "v validate local CSVs",
    "w toggle auto-fetch every 60s (spends quota)",
    "F1 this help",
    "q quit",
    "",
    "Conflict safety: push/pull on a diverged (X) table asks first.",
    "Force keeps your side: push keeps local, pull keeps sheet.",
]


class AuthGate(ModalScreen[str]):
    """First-run auth: log in, continue offline, or quit. Esc never traps
    the user — it means offline. Dismisses "login" | "offline" | "quit"."""

    BINDINGS = [
        Binding("escape", "offline", "Offline"),
        Binding("o", "offline", "Offline"),
        Binding("q", "quit_gate", "Quit"),
    ]

    def __init__(self, *, origin: str, can_login: bool, root: str, token: str) -> None:
        super().__init__()
        self._origin = origin
        self._can_login = can_login
        self._root = root
        self._token = token

    def compose(self) -> ComposeResult:
        lines = ["Welcome to fccsv — connect Google Sheets first.", self._origin]
        if not self._can_login:
            lines += [
                "No OAuth client found. Provide one, then login:",
                f"  1. put credentials.json (Desktop app) in {self._root}",
                "  2. or set $FCCSV_CLIENT_JSON / $FCCSV_CLIENT_ID+$FCCSV_CLIENT_SECRET",
                "  3. release builds bundle one — replace the file next to fccsv.exe to rotate",
            ]
        else:
            lines.append(f"No login yet — token will be saved to {self._token}.")
        yield Vertical(
            Label("\n".join(lines)),
            Button("Login with Google (l)", id="login", variant="primary")
            if self._can_login
            else Label("Login unavailable until a client is provided."),
            Button("Continue offline (o)", id="offline"),
            Button("Quit (q)", id="quit"),
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        pressed = event.button.id or "offline"
        self.dismiss(pressed if pressed in ("login", "offline", "quit") else "offline")

    async def action_login(self) -> None:
        if self._can_login:
            self.dismiss("login")

    def action_offline(self) -> None:
        self.dismiss("offline")

    def action_quit_gate(self) -> None:
        self.dismiss("quit")


class InitGate(ModalScreen[str]):
    """First-run (or re-run via `i`) sheet connect: paste a URL, init the
    config, and first-pull to seed the base. Dismisses the URL on init,
    "offline" to skip, "quit" to exit. Esc means offline."""

    BINDINGS = [
        Binding("escape", "offline", "Offline"),
        Binding("o", "offline", "Offline"),
        Binding("q", "quit_gate", "Quit"),
    ]

    def __init__(self, current: str = "") -> None:
        super().__init__()
        self._current = current

    def compose(self) -> ComposeResult:
        hint = f" (current: {self._current})" if self._current else ""
        yield Vertical(
            Label(f"Connect a Google Sheet{hint} — paste its URL or id:"),
            Input(value=self._current, id="url"),
            Button("Init + first pull", id="init", variant="primary"),
            Button("Continue offline (o)", id="offline"),
            Button("Quit (q)", id="quit"),
        )

    def _submit(self) -> None:
        value = self.query_one("#url", Input).value.strip()
        if not value:
            self.app.notify("paste a sheet URL first (or continue offline)", severity="warning")
            return
        self.dismiss(value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        _ = event
        self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        pressed = event.button.id or "offline"
        if pressed == "init":
            self._submit()
        else:
            self.dismiss(pressed if pressed in ("offline", "quit") else "offline")

    def action_offline(self) -> None:
        self.dismiss("offline")

    def action_quit_gate(self) -> None:
        self.dismiss("quit")


class ConflictModal(ModalScreen[bool]):
    """Confirm a transfer on diverged tables. F forces, Esc cancels."""

    BINDINGS = [
        Binding("f", "force", "Force"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, direction: str, tabs: list[str], lines: list[str]) -> None:
        super().__init__()
        self._direction = direction
        self._tabs = tabs
        self._lines = lines

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(f"Diverged: {', '.join(self._tabs)} — {self._direction} anyway?"),
            Label("\n".join(self._lines[:12])),
            Label("F forces (keeps your side) — Esc cancels."),
            Button(f"Force {self._direction} (f)", id="force", variant="error"),
            Button("Cancel (esc)", id="cancel"),
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "force")

    def action_force(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class KeysModal(ModalScreen[str | None]):
    """Input for `--only` keys. Empty applies to all; Esc cancels."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, current: str) -> None:
        super().__init__()
        self._current = current

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("Partial sync — comma-separated keys (empty = all rows):"),
            Input(value=self._current, id="keys"),
            Button("Apply", id="apply", variant="primary"),
            Button("Cancel (esc)", id="cancel"),
        )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        _ = event
        widget = self.query_one("#keys", Input)
        self.dismiss(widget.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "apply":
            self.dismiss(self.query_one("#keys", Input).value)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ReportScreen(ModalScreen[None]):
    """Scrollable read-only text (log / doctor / validate / help)."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close"),
    ]

    def __init__(self, title: str, lines: list[str]) -> None:
        super().__init__()
        self._title = title
        self._lines = lines

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(self._title),
            RichLog(highlight=False, markup=False, wrap=True, id="report"),
            Button("Close (q)", id="close"),
        )

    def on_mount(self) -> None:
        log = self.query_one("#report", RichLog)
        for line in self._lines:
            log.write(line)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        _ = event
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
