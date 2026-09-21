"""TUI backend: owns `Context` + gateway, delegates every transfer to the
CLI orchestration (`_run_sync`, `cmd_fetch/log/doctor/validate`) with
stdout captured. The TUI therefore cannot drift from CLI semantics:
same planning, same lineage commits, same journal, same exit codes."""

from __future__ import annotations

import argparse
import contextlib
import io
from dataclasses import dataclass
from pathlib import Path

from fccsv.adapters.sheets import InMemorySheetsGateway, SheetsGateway
from fccsv.app import auth as auth_mod
from fccsv.app import fetch as fetch_mod
from fccsv.app import sync as sync_mod
from fccsv.core.config import TableMapping
from fccsv.interfaces import cli


@dataclass
class DashboardRow:
    plan: sync_mod.TablePlan
    direction: str
    remote_changed: bool


@dataclass
class StartupState:
    """First-run checklist. The TUI gates on these in order: client
    identity -> login token -> connected sheet. All local reads, safe
    to compute on the UI thread."""

    client_origin: str
    has_client: bool
    has_token: bool
    has_sheet: bool
    root: Path
    token_file: Path


def _capture(func: object, *args: object, **kwargs: object) -> tuple[int, str]:
    """Run a `cmd_*`/`_run_sync` function, return (rc, stripped output)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        rc = func(*args, **kwargs)  # type: ignore[operator]
    return int(rc), buf.getvalue().strip()


class Backend:
    def __init__(self, ctx: cli.Context, gateway: SheetsGateway) -> None:
        self.ctx = ctx
        self.gateway = gateway

    @property
    def is_offline(self) -> bool:
        return isinstance(self.gateway, InMemorySheetsGateway)

    @property
    def spreadsheet_id(self) -> str:
        return self.ctx.config.active.spreadsheet_id

    def client_origin(self) -> str:
        client = auth_mod.resolve_client_file(
            None, project_root=self.ctx.root, tool_dir=cli.TOOL_DIR, cwd=self.ctx.root
        )
        return auth_mod.describe_client_origin(client)

    def mappings(self) -> list[TableMapping]:
        return cli.select_mappings(self.ctx, None)

    def startup_state(self) -> StartupState:
        client = auth_mod.resolve_client_file(
            None, project_root=self.ctx.root, tool_dir=cli.TOOL_DIR, cwd=self.ctx.root
        )
        token = auth_mod.resolve_token_file(None, project_root=self.ctx.root)
        return StartupState(
            client_origin=auth_mod.describe_client_origin(client),
            has_client=client is not None or auth_mod.inline_client_config() is not None,
            has_token=token.exists(),
            has_sheet=bool(self.ctx.config.active.spreadsheet_id),
            root=self.ctx.root,
            token_file=token,
        )

    def login(self) -> tuple[int, str]:
        """Browser OAuth; writes the per-user token file. Blocks — callers
        must run this off the UI thread."""
        args = argparse.Namespace(credentials=None, token=None)
        return _capture(cli.cmd_auth, self.ctx, self.gateway, args)

    def onboard_init(self, url_or_id: str) -> tuple[int, str]:
        """Connect a sheet: build a live gateway, write config from its
        tabs, then first-pull to seed CSVs + base lineage. Switching to a
        different sheet resets lineage (same as `remote set`), so the old
        sheet's hashes can never surface as false conflicts."""
        from fccsv.adapters import sheets as sheets_mod
        from fccsv.adapters import base as base_mod
        from fccsv.domain.errors import ConfigError

        value = url_or_id.strip()
        if not value:
            raise ConfigError("paste a Google Sheet URL or spreadsheet id first")
        token = auth_mod.resolve_token_file(None, project_root=self.ctx.root)
        if not token.exists():
            raise ConfigError("login first (no token yet) — rerun the auth step")
        client = auth_mod.resolve_client_file(
            None, project_root=self.ctx.root, tool_dir=cli.TOOL_DIR, cwd=self.ctx.root
        )
        creds = auth_mod.load_credentials(client, token)
        sid = sheets_mod.spreadsheet_id_from_url(value)
        old_id = self.ctx.config.active.spreadsheet_id
        self.gateway = sheets_mod.GoogleSheetsGateway(sid, creds)
        rc, init_text = _capture(
            cli.cmd_init, self.ctx, self.gateway, argparse.Namespace(url_or_id=value)
        )
        if rc != 0:
            return rc, init_text
        if old_id and old_id != self.ctx.config.active.spreadsheet_id:
            state_file = self.ctx.state_file
            if state_file.exists():
                state_file.replace(state_file.with_name(state_file.name + ".pre-init"))
            base_mod.clear_all_base(self.ctx.base_dir)
            init_text += "\nswitched sheet: old lineage reset"
        pull_rc, pull_text = self.sync("pull", None)
        combined = init_text + (f"\n{pull_text}" if pull_text else "")
        return pull_rc, combined

    def refresh(self, fetch_first: bool = False) -> tuple[list[DashboardRow], str]:
        """Re-plan every table. Optionally run the tiered fetch first so the
        `<` / `X` markers account for sheet moves (same as `status --fetch`)."""
        mappings = self.mappings()
        if not mappings:
            return [], "no tables mapped (run init)"
        state = cli.load_state(self.ctx)
        note = ""
        fetch_flags: dict[str, bool] = {}
        if fetch_first:
            result = fetch_mod.fetch(mappings, self.gateway, state)
            fetch_flags = {t.tab: (t.changed or t.missing) for t in result.tables}
            if result.stamp_match:
                note = f"up to date (drive stamp {result.drive_stamp})"
            else:
                moved = sorted(t.tab for t in result.tables if t.changed or t.missing)
                note = "sheet moved: " + (", ".join(moved) if moved else "(stamp only)")
        plans = sync_mod.plan_many(
            mappings,
            csv_dir=self.ctx.csv_dir,
            gateway=self.gateway,
            state=state,
            base_dir=self.ctx.base_dir,
        )
        rows = []
        for plan in plans:
            remote_changed = fetch_flags.get(plan.tab, plan.remote_hash != plan.last_remote)
            rows.append(
                DashboardRow(
                    plan=plan,
                    direction=cli.direction_of(plan, remote_changed=remote_changed),
                    remote_changed=remote_changed,
                )
            )
        return rows, note

    def sync(
        self,
        direction: str,
        tabs: list[str] | None,
        *,
        force: bool = False,
        only: set[str] | None = None,
        exclude: set[str] | None = None,
        allow_schema_change: bool = False,
    ) -> tuple[int, str]:
        args = argparse.Namespace(
            tab=tabs,
            only=",".join(sorted(only)) if only else None,
            exclude=",".join(sorted(exclude)) if exclude else None,
            force=force,
            allow_schema_change=allow_schema_change,
            dry_run=False,
        )
        return _capture(cli._run_sync, self.ctx, self.gateway, args, direction=direction)

    def fetch_text(self, tabs: list[str] | None = None) -> tuple[object, str]:
        args = argparse.Namespace(tab=tabs, json=False)
        state = cli.load_state(self.ctx)
        result = fetch_mod.fetch(self.mappings(), self.gateway, state)
        _, text = _capture(cli.cmd_fetch, self.ctx, self.gateway, args)
        return result, text

    def log_text(self, tab: str | None = None, limit: int = 50) -> str:
        args = argparse.Namespace(tab=tab, number=limit, oneline=False)
        _, text = _capture(cli.cmd_log, self.ctx, self.gateway, args)
        return text

    def doctor_text(self) -> tuple[int, str]:
        args = argparse.Namespace(credentials=None, token=None, offline=self.is_offline)
        return _capture(cli.cmd_doctor, self.ctx, self.gateway, args)

    def validate_text(self, tabs: list[str] | None = None) -> tuple[int, str]:
        args = argparse.Namespace(tab=tabs)
        return _capture(cli.cmd_validate, self.ctx, self.gateway, args)

    def diff_lines(self, row: DashboardRow) -> list[str]:
        """Human diff for one table, same wording as `cmd_diff`."""
        plan = row.plan
        lines = [cli._stat_line(plan, row.direction)]
        if plan.validation_issues:
            lines.append("validation: " + "; ".join(plan.validation_issues))
        if plan.local is None and plan.remote is None:
            lines.append("no data either side")
            return lines
        if plan.local is None:
            lines.append(f"D {plan.tab} — sheet only, pull creates the CSV")
            return lines
        if plan.remote is None:
            lines.append(f"A {plan.tab} — local only, push creates the tab")
            return lines
        for col in plan.schema_diff.added:
            lines.append(f"! column added: {col}")
        for col in plan.schema_diff.removed:
            lines.append(f"! column removed: {col}")
        if plan.schema_diff.reordered:
            lines.append("! column order changed")
        for key in plan.row_diff.removed_keys:
            lines.append(f"- {cli._format_key(plan, key)}")
        for key in plan.row_diff.added_keys:
            lines.append(f"+ {cli._format_key(plan, key)}")
        for key, cols in cli._cell_changes(plan).items():
            for col, (old, new) in cols.items():
                lines.append(f"~ {cli._format_key(plan, key)}.{col}: {old!r} -> {new!r}")
        if len(lines) == 1:
            lines.append("no differences")
        return lines
