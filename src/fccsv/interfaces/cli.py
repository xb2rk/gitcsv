"""CLI: thin dispatch over `app/`. No merge logic lives here.

Every sync command spends one batched `plan_many` (single read
round-trip); state saves are incremental so a quota abort keeps
already-synced tables. Exit codes: 0 ok, 1 operational/sync/validation
(`--fail-on-diff` diffs land here too), 2 usage/config. `--offline` (or
no spreadsheet id) uses an in-memory sheet.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fccsv import __version__
from fccsv.adapters import base as base_mod
from fccsv.adapters import local as local_mod
from fccsv.adapters import state as state_mod
from fccsv.adapters.journal import read_journal
from fccsv.adapters.sheets import InMemorySheetsGateway, SheetsGateway
from fccsv.app import auth as auth_mod
from fccsv.app import doctor as doctor_mod
from fccsv.app import fetch as fetch_mod
from fccsv.app import init as init_mod
from fccsv.app import remote as remote_mod
from fccsv.app import sync as sync_mod
from fccsv.app.finalize import commit_pull, commit_push, write_metadata_tab
from fccsv.core import logging as logging_mod
from fccsv.core.config import FccsvConfig, TableMapping
from fccsv.core.constants import (
    DEFAULT_BASE_DIRNAME,
    DEFAULT_BACKUP_DIRNAME,
    DEFAULT_CONFIG_FILENAME,
    DEFAULT_JOURNAL_FILENAME,
)
from fccsv.domain.errors import ConfigError, DataValidationError, FccsvError
from fccsv.domain.snapshot import TableSnapshot
from fccsv.domain.validate import validate_snapshot

TOOL_DIR = Path(__file__).resolve().parents[3]

_DIRECTION_HELP = {
    "=": "in sync",
    ">": "local changed — run push",
    "<": "sheet changed — run pull",
    "X": "diverged on both sides — resolve, then --force push / --force pull",
    "A": "local only — push will create the tab",
    "D": "sheet only — pull will create the CSV",
    "?": "no data either side",
    "M": "differs",
}


@dataclass
class Context:
    root: Path
    config: FccsvConfig
    csv_dir: Path
    state_file: Path
    base_dir: Path
    backup_root: Path
    journal_file: Path


def resolve_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    cwd = Path.cwd().resolve()
    node: Path | None = cwd
    while node is not None:
        if (node / "Assets" / "CSV").is_dir() or (node / DEFAULT_CONFIG_FILENAME).exists():
            return node
        node = node.parent if node.parent != node else None
    return cwd


def load_config(root: Path) -> FccsvConfig:
    path = root / DEFAULT_CONFIG_FILENAME
    if not path.exists():
        return FccsvConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"cannot read config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"config {path} is not a JSON object")
    try:
        return FccsvConfig.from_dict(data)
    except TypeError as exc:
        raise ConfigError(f"config {path} has bad shape: {exc}") from exc


def build_context(root: Path, config: FccsvConfig) -> Context:
    return Context(
        root=root,
        config=config,
        csv_dir=(root / config.csv_dir).resolve(),
        state_file=root / config.state_file,
        base_dir=root / DEFAULT_BASE_DIRNAME,
        backup_root=root / DEFAULT_BACKUP_DIRNAME,
        journal_file=root / DEFAULT_JOURNAL_FILENAME,
    )


def save_config(ctx: Context) -> None:
    path = ctx.root / DEFAULT_CONFIG_FILENAME
    path.write_text(
        json.dumps(ctx.config.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def make_gateway(ctx: Context, args: argparse.Namespace) -> SheetsGateway:
    if args.offline:
        return InMemorySheetsGateway()
    spreadsheet_id = ctx.config.active.spreadsheet_id
    if not spreadsheet_id:
        return InMemorySheetsGateway()
    client = auth_mod.resolve_client_file(
        args.credentials,
        project_root=ctx.root,
        tool_dir=TOOL_DIR,
        cwd=Path.cwd(),
    )
    token = auth_mod.resolve_token_file(args.token, project_root=ctx.root)
    creds = auth_mod.load_credentials(client, token)
    from fccsv.adapters.sheets import GoogleSheetsGateway

    return GoogleSheetsGateway(spreadsheet_id, creds)


def select_mappings(ctx: Context, tabs: list[str] | None) -> list[TableMapping]:
    tables = ctx.config.active.tables
    if not tabs:
        return list(tables)
    wanted = set(tabs)
    found = [m for m in tables if m.tab in wanted]
    missing = sorted(wanted - {m.tab for m in found})
    if missing:
        known = ", ".join(m.tab for m in tables) or "(none)"
        raise ConfigError(f"unknown tab(s): {missing}. Known tabs: {known}")
    return found


def parse_set(value: str | None) -> set[str] | None:
    if value is None:
        return None
    return {part.strip() for part in value.split(",") if part.strip()}


def load_state(ctx: Context) -> state_mod.SyncState:
    return state_mod.load_state(ctx.state_file)


def direction_of(plan: sync_mod.TablePlan, *, remote_changed: bool) -> str:
    if plan.local is None and plan.remote is None:
        return "?"
    if plan.local is None:
        return "D"
    if plan.remote is None:
        return "A"
    if sync_mod.plan_in_sync(plan):
        return "="
    local_changed = plan.local_hash != plan.last_local
    if local_changed and remote_changed:
        return "X"
    if local_changed:
        return ">"
    if remote_changed:
        return "<"
    return "M"


def plan_has_diff(plan: sync_mod.TablePlan) -> bool:
    return (
        plan.local is None
        or plan.remote is None
        or plan.schema_diff.has_change
        or plan.row_diff.has_change
    )


def _short_hash(value: str) -> str:
    return value[:8] if value else "(none)"


# ---------------------------------------------------------------- commands


def cmd_auth(ctx: Context, gateway: SheetsGateway, args: argparse.Namespace) -> int:
    _ = gateway
    client = auth_mod.resolve_client_file(
        args.credentials, project_root=ctx.root, tool_dir=TOOL_DIR, cwd=Path.cwd()
    )
    token = auth_mod.resolve_token_file(args.token, project_root=ctx.root)
    auth_mod.load_credentials(client, token)
    print(f"authenticated; token saved: {token}")
    return 0


def cmd_init(ctx: Context, gateway: SheetsGateway, args: argparse.Namespace) -> int:
    config = init_mod.init_from_url_or_id(args.url_or_id, gateway, csv_dir=ctx.csv_dir)
    # Keep local file-layout choices from any existing config.
    config.csv_dir = ctx.config.csv_dir
    config.credentials_file = ctx.config.credentials_file
    config.token_file = ctx.config.token_file
    config.state_file = ctx.config.state_file
    ctx.config = config
    save_config(ctx)
    for table in config.active.tables:
        print(f"  {table.tab} -> {table.csv}")
    print(f"wrote {DEFAULT_CONFIG_FILENAME} ({len(config.active.tables)} tables)")
    return 0


def cmd_status(ctx: Context, gateway: SheetsGateway, args: argparse.Namespace) -> int:
    mappings = select_mappings(ctx, args.tab)
    if not mappings:
        print("no tables mapped (run init)")
        return 0
    state = load_state(ctx)
    fetch_flags: dict[str, bool] = {}
    stamp = ""
    if args.fetch:
        result = fetch_mod.fetch(mappings, gateway, state)
        stamp = result.drive_stamp or ""
        fetch_flags = {t.tab: (t.changed or t.missing) for t in result.tables}
    plans = sync_mod.plan_many(
        mappings, csv_dir=ctx.csv_dir, gateway=gateway, state=state, base_dir=ctx.base_dir
    )
    if args.fetch and stamp:
        print(f"drive stamp: {stamp}")
    rows: list[tuple[sync_mod.TablePlan, str, bool]] = []
    for plan in plans:
        remote_changed = fetch_flags.get(plan.tab, plan.remote_hash != plan.last_remote)
        rows.append((plan, direction_of(plan, remote_changed=remote_changed), remote_changed))
    if args.json:
        print(json.dumps([_status_json(plan, d) for plan, d, _ in rows], indent=2))
    elif args.short:
        for plan, d, _ in rows:
            suffix = " (!)" if plan.validation_issues else ""
            print(f"{d} {plan.tab}{suffix}")
    elif args.stat:
        for plan, d, _ in rows:
            print(_stat_line(plan, d))
    else:
        for plan, d, _ in rows:
            print(f"[{plan.tab}] {_DIRECTION_HELP[d]}")
            if plan.validation_issues:
                print(f"  validation: {'; '.join(plan.validation_issues)}")
    if args.fail_on_diff and any(plan_has_diff(plan) for plan, _, _ in rows):
        return 1
    return 0


def _status_json(plan: sync_mod.TablePlan, direction: str) -> dict[str, object]:
    return {
        "tab": plan.tab,
        "csv": plan.csv_file,
        "direction": direction,
        "in_sync": direction == "=",
        "conflict": sync_mod.plan_conflicted(plan),
        "local_changed": plan.local_hash != plan.last_local,
        "remote_changed": plan.remote_hash != plan.last_remote,
        "added": list(plan.row_diff.added_keys),
        "removed": list(plan.row_diff.removed_keys),
        "changed": list(plan.row_diff.changed_keys),
        "schema": {
            "added": list(plan.schema_diff.added),
            "removed": list(plan.schema_diff.removed),
            "reordered": plan.schema_diff.reordered,
        },
        "validation": list(plan.validation_issues),
    }


def _stat_line(plan: sync_mod.TablePlan, direction: str) -> str:
    counts = (
        f"+{len(plan.row_diff.added_keys)} "
        f"-{len(plan.row_diff.removed_keys)} "
        f"~{len(plan.row_diff.changed_keys)}"
    )
    flags = ""
    if plan.schema_diff.has_change:
        flags += " [schema]"
    if sync_mod.plan_conflicted(plan):
        flags += " [conflict]"
    if plan.validation_issues:
        flags += f" [{len(plan.validation_issues)} validation issue(s)]"
    return f"{direction} {plan.tab}: {counts}{flags}"


def _format_key(plan: sync_mod.TablePlan, key: str) -> str:
    key_col = ""
    if plan.local is not None and plan.local.key_column:
        key_col = plan.local.key_column
    elif plan.remote is not None:
        key_col = plan.remote.key_column
    if key_col:
        return f"{key_col}={key}"
    try:
        if key.startswith(("+", "-")):
            return f"row #{int(key[1:]) + 1}"
        return f"row #{int(key) + 1}"
    except ValueError:
        return key


def _cell_changes(plan: sync_mod.TablePlan) -> dict[str, dict[str, tuple[str, str]]]:
    if plan.local is None or plan.remote is None:
        return {}
    common = [h for h in plan.local.headers if h in plan.remote.headers]
    local_by_key = {plan.local.key_of(r): r for r in plan.local.rows}
    remote_by_key = {plan.remote.key_of(r): r for r in plan.remote.rows}
    out: dict[str, dict[str, tuple[str, str]]] = {}
    for key in plan.row_diff.changed_keys:
        left = local_by_key.get(key, {})
        right = remote_by_key.get(key, {})
        # Convention: sheet value -> CSV value, so with direction ">" the
        # arrow shows what push would write to the sheet.
        cols = {
            col: (right.get(col, ""), left.get(col, ""))
            for col in common
            if left.get(col, "") != right.get(col, "")
        }
        if cols:
            out[key] = cols
    return out


def cmd_diff(ctx: Context, gateway: SheetsGateway, args: argparse.Namespace) -> int:
    mappings = select_mappings(ctx, args.tab)
    if not mappings:
        print("no tables mapped (run init)")
        return 0
    state = load_state(ctx)
    plans = sync_mod.plan_many(
        mappings, csv_dir=ctx.csv_dir, gateway=gateway, state=state, base_dir=ctx.base_dir
    )
    changed = [p for p in plans if plan_has_diff(p)]
    if args.name_only:
        for plan in changed:
            print(plan.tab)
        return 0
    if args.json:
        payload = []
        for plan in changed:
            payload.append(
                {
                    "tab": plan.tab,
                    "added": list(plan.row_diff.added_keys),
                    "removed": list(plan.row_diff.removed_keys),
                    "changed": _cell_changes(plan),
                    "schema": {
                        "added": list(plan.schema_diff.added),
                        "removed": list(plan.schema_diff.removed),
                        "reordered": plan.schema_diff.reordered,
                    },
                }
            )
        print(json.dumps(payload, indent=2))
        return 0
    if args.stat:
        for plan in changed:
            print(_stat_line(plan, direction_of(plan, remote_changed=True)))
        if not changed:
            print("no differences")
        return 0
    if not changed:
        print("no differences")
        return 0
    for plan in changed:
        for col in plan.schema_diff.added:
            print(f"! [{plan.tab}] column added: {col}")
        for col in plan.schema_diff.removed:
            print(f"! [{plan.tab}] column removed: {col}")
        if plan.schema_diff.reordered:
            print(f"! [{plan.tab}] column order changed")
        for key in plan.row_diff.removed_keys:
            print(f"- [{plan.tab}] {_format_key(plan, key)}")
        for key in plan.row_diff.added_keys:
            print(f"+ [{plan.tab}] {_format_key(plan, key)}")
        for key, cols in _cell_changes(plan).items():
            for col, (old, new) in cols.items():
                print(f"~ [{plan.tab}] {_format_key(plan, key)}.{col}: {old!r} -> {new!r}")
    return 0


def _seed_from_remote(ctx: Context, plan: sync_mod.TablePlan) -> TableSnapshot:
    """First-pull seeding: create a missing CSV from the sheet content."""
    remote = plan.remote
    assert remote is not None
    issues = validate_snapshot(remote)
    if issues:
        raise DataValidationError(f"[{plan.tab}] sheet failed validation: " + "; ".join(issues))
    merged = TableSnapshot(
        name=Path(plan.csv_file).stem,
        headers=list(remote.headers),
        types=[],
        rows=[{h: r.get(h, "") for h in remote.headers} for r in remote.rows],
        key_column=remote.key_column,
    )
    local_mod.write_csv_table(ctx.csv_dir / plan.csv_file, merged)
    return merged


def _run_sync(
    ctx: Context, gateway: SheetsGateway, args: argparse.Namespace, *, direction: str
) -> int:
    mappings = select_mappings(ctx, args.tab)
    if not mappings:
        print("no tables mapped (run init)")
        return 0
    only = parse_set(args.only)
    exclude = parse_set(args.exclude)
    spreadsheet_id = ctx.config.active.spreadsheet_id
    state = load_state(ctx)
    plans = sync_mod.plan_many(
        mappings, csv_dir=ctx.csv_dir, gateway=gateway, state=state, base_dir=ctx.base_dir
    )
    errors = 0
    applied: list[str] = []
    for plan in plans:
        try:
            if direction == "pull" and plan.local is None and plan.remote is not None:
                if args.dry_run:
                    print(f"[{plan.tab}] would pull (new file)")
                    continue
                seeded = _seed_from_remote(ctx, plan)
                commit_pull(
                    state,
                    ctx.base_dir,
                    name=plan.tab,
                    merged=seeded,
                    remote_snapshot=plan.remote,
                    journal=ctx.journal_file,
                    spreadsheet_id=spreadsheet_id,
                )
                print(f"[{plan.tab}] pulled (new file)")
            elif direction == "push":
                outcome, new_remote = sync_mod.apply_push(
                    plan,
                    gateway,
                    force=args.force,
                    allow_schema_change=args.allow_schema_change,
                    dry_run=args.dry_run,
                    only_keys=only,
                    exclude_columns=exclude,
                )
                print(outcome.message)
                # Record lineage even for an in-sync no-op: without a BASE
                # snapshot the next both-changed edit would be undetectable.
                # No journal line for no-ops — `log` shows transfers only.
                if plan.local is not None and not args.dry_run:
                    synced = new_remote if new_remote is not None else plan.remote
                    if synced is not None:
                        commit_push(
                            state,
                            ctx.base_dir,
                            name=plan.tab,
                            local_snapshot=plan.local,
                            new_remote=synced,
                            journal=ctx.journal_file if outcome.applied else None,
                            spreadsheet_id=spreadsheet_id,
                        )
            else:
                outcome, merged = sync_mod.apply_pull(
                    plan,
                    gateway,
                    csv_dir=ctx.csv_dir,
                    backup_root=ctx.backup_root,
                    force=args.force,
                    allow_schema_change=args.allow_schema_change,
                    dry_run=args.dry_run,
                    only_keys=only,
                    exclude_columns=exclude,
                )
                print(outcome.message)
                # Same lineage rule as push: no-ops still save state + BASE.
                if not args.dry_run:
                    merged_snap = merged if merged is not None else plan.local
                    if merged_snap is not None and plan.remote is not None:
                        commit_pull(
                            state,
                            ctx.base_dir,
                            name=plan.tab,
                            merged=merged_snap,
                            remote_snapshot=plan.remote,
                            journal=ctx.journal_file if outcome.applied else None,
                            spreadsheet_id=spreadsheet_id,
                        )
            if not args.dry_run:
                applied.append(plan.tab)
                state_mod.save_state(ctx.state_file, state)
        except FccsvError as exc:
            print(f"error: {exc}", file=sys.stderr)
            errors += 1
    if args.dry_run:
        return 1 if errors else 0
    try:
        stamp = gateway.drive_modified_time()
    except Exception:
        stamp = None
    if stamp:
        state.modified_time = stamp
    state_mod.save_state(ctx.state_file, state)
    if applied:
        try:
            write_metadata_tab(gateway, state, applied)
        except Exception as exc:
            print(f"warning: metadata tab not updated ({exc})", file=sys.stderr)
    return 1 if errors else 0


def cmd_push(ctx: Context, gateway: SheetsGateway, args: argparse.Namespace) -> int:
    return _run_sync(ctx, gateway, args, direction="push")


def cmd_pull(ctx: Context, gateway: SheetsGateway, args: argparse.Namespace) -> int:
    return _run_sync(ctx, gateway, args, direction="pull")


def cmd_fetch(ctx: Context, gateway: SheetsGateway, args: argparse.Namespace) -> int:
    mappings = select_mappings(ctx, args.tab)
    if not mappings:
        print("no tables mapped (run init)")
        return 0
    state = load_state(ctx)
    result = fetch_mod.fetch(mappings, gateway, state)
    if args.json:
        print(
            json.dumps(
                {
                    "drive_stamp": result.drive_stamp,
                    "stamp_match": result.stamp_match,
                    "tables": [
                        {"tab": t.tab, "changed": t.changed, "missing": t.missing}
                        for t in result.tables
                    ],
                },
                indent=2,
            )
        )
        return 0
    if result.stamp_match:
        print(f"up to date (drive stamp {result.drive_stamp})")
        return 0
    if result.drive_stamp:
        print(f"drive stamp: {result.drive_stamp}")
    for table in result.tables:
        if table.missing:
            print(f"[{table.tab}] missing on sheet")
        elif table.changed:
            print(f"[{table.tab}] changed on sheet — run pull")
        else:
            print(f"[{table.tab}] unchanged")
    return 0


def cmd_log(ctx: Context, gateway: SheetsGateway, args: argparse.Namespace) -> int:
    _ = gateway
    spreadsheet_id = ctx.config.active.spreadsheet_id
    entries = read_journal(
        ctx.journal_file,
        table=args.tab,
        spreadsheet_id=spreadsheet_id,
        limit=args.number,
    )
    if not entries:
        print("no sync history yet")
        return 0
    for entry in reversed(entries):  # newest first
        if args.oneline:
            print(f"{entry.ts} {entry.action} {entry.table}")
        else:
            print(
                f"{entry.ts} {entry.action} {entry.table} "
                f"{entry.rows} rows "
                f"local={_short_hash(entry.local)} remote={_short_hash(entry.remote)}"
            )
    return 0


def cmd_remote_show(ctx: Context, gateway: SheetsGateway, args: argparse.Namespace) -> int:
    _ = (gateway, args)
    print(remote_mod.show_remote(ctx.config))
    return 0


def cmd_remote_set(ctx: Context, gateway: SheetsGateway, args: argparse.Namespace) -> int:
    _ = gateway
    message = remote_mod.set_remote(
        ctx.config,
        args.url_or_id,
        state_file=ctx.state_file,
        base_dir=ctx.base_dir,
        mode=args.mode,
    )
    save_config(ctx)
    print(message)
    return 0


def cmd_rename(ctx: Context, gateway: SheetsGateway, args: argparse.Namespace) -> int:
    _ = gateway
    try:
        message = remote_mod.rename_table(ctx.config, old=args.old, new=args.new)
    except LookupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    # Drop the old lineage so the next sync seeds fresh (the sheet tab
    # itself is renamed by the user in the browser, then this follows).
    state = load_state(ctx)
    state.tables.pop(args.old, None)
    state_mod.save_state(ctx.state_file, state)
    base_mod.clear_base(ctx.base_dir, args.old)
    save_config(ctx)
    print(message)
    return 0


def cmd_validate(ctx: Context, gateway: SheetsGateway, args: argparse.Namespace) -> int:
    _ = gateway
    mappings = select_mappings(ctx, args.tab)
    if not mappings:
        print("no tables mapped (run init)")
        return 0
    bad = 0
    for mapping in mappings:
        path = ctx.csv_dir / mapping.csv
        if not path.exists():
            print(f"[{mapping.tab}] not pulled yet: {mapping.csv}")
            continue
        try:
            snapshot = local_mod.read_csv_table(path, key_column=mapping.key_column)
        except (OSError, UnicodeDecodeError) as exc:
            print(f"[{mapping.tab}] unreadable: {exc}")
            bad += 1
            continue
        issues = validate_snapshot(snapshot)
        if issues:
            print(f"[{mapping.tab}] invalid: {'; '.join(issues)}")
            bad += 1
        else:
            print(f"[{mapping.tab}] ok ({len(snapshot.rows)} rows)")
    return 1 if bad else 0


def cmd_doctor(ctx: Context, gateway: SheetsGateway, args: argparse.Namespace) -> int:
    problems = doctor_mod.check_config(ctx.config)
    problems += doctor_mod.check_csv_dir(ctx.csv_dir)
    client = auth_mod.resolve_client_file(
        args.credentials, project_root=ctx.root, tool_dir=TOOL_DIR, cwd=Path.cwd()
    )
    token = auth_mod.resolve_token_file(args.token, project_root=ctx.root)
    problems += doctor_mod.check_auth_files(client, token)
    if not args.offline and ctx.config.active.spreadsheet_id:
        problems += doctor_mod.check_gateway(gateway)
    else:
        print("note: sheet reachability skipped (offline or no spreadsheet id)")
    print(auth_mod.describe_client_origin(client))
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}")
        return 1
    print("ok: config, CSVs, auth files, and sheet all look healthy")
    return 0


def _load_tui_app() -> Any | None:
    """Lazy import: `fccsv` without the `tui` extra keeps working."""
    try:
        from fccsv.interfaces.tui.app import FccsvApp
    except ImportError:
        return None
    return FccsvApp


def cmd_tui(ctx: Context, gateway: SheetsGateway, args: argparse.Namespace) -> int:
    _ = args
    app_cls = _load_tui_app()
    if app_cls is None:
        print("error: TUI needs the 'tui' extra: pip install fccsv[tui]", file=sys.stderr)
        return 2
    app_cls(root=ctx.root, gateway=gateway).run()
    return 0


def cmd_version(ctx: Context, gateway: SheetsGateway, args: argparse.Namespace) -> int:
    _ = (ctx, gateway, args)
    print(f"fccsv {__version__}")
    return 0


# ---------------------------------------------------------------- parser


def _add_tab_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--tab",
        action="append",
        default=None,
        help="limit to this tab (repeatable; default: all tables)",
    )


def _add_sync_args(parser: argparse.ArgumentParser) -> None:
    _add_tab_arg(parser)
    parser.add_argument("--only", default=None, help="comma-separated keys for partial sync")
    parser.add_argument("--exclude", default=None, help="comma-separated columns to skip")
    parser.add_argument("--force", action="store_true", help="push keeps local, pull keeps sheet")
    parser.add_argument(
        "--allow-schema-change", action="store_true", help="dev-only: allow header changes"
    )
    parser.add_argument("--dry-run", action="store_true", help="plan only, write nothing")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="fccsv v2: git-style CSV <-> Google Sheets sync")
    parser.add_argument("--root", default=None, help="project root (default: auto-detect)")
    parser.add_argument("--offline", action="store_true", help="no network; in-memory sheet")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--credentials", default=None, help="OAuth client JSON path")
    parser.add_argument("--token", default=None, help="user token JSON path")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("auth", help="run OAuth and save the user token")
    p.set_defaults(handler=cmd_auth)

    p = sub.add_parser("init", help="map sheet tabs to CSVs and write .fccsv.json")
    p.add_argument("url_or_id", help="spreadsheet URL or id")
    p.set_defaults(handler=cmd_init)

    p = sub.add_parser("status", help="compare local CSVs with the sheet")
    _add_tab_arg(p)
    p.add_argument("--fetch", action="store_true", help="use the tiered remote check first")
    p.add_argument("--short", action="store_true", help="git-style short lines")
    p.add_argument("--stat", action="store_true", help="added/removed/changed counts")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--fail-on-diff", action="store_true", help="exit 1 when any table differs")
    p.set_defaults(handler=cmd_status)

    p = sub.add_parser("diff", help="show cell-level differences")
    _add_tab_arg(p)
    p.add_argument("--name-only", action="store_true")
    p.add_argument("--stat", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(handler=cmd_diff)

    p = sub.add_parser("push", help="local CSVs -> sheet")
    _add_sync_args(p)
    p.set_defaults(handler=cmd_push)

    p = sub.add_parser("pull", help="sheet -> local CSVs (backs up first)")
    _add_sync_args(p)
    p.set_defaults(handler=cmd_pull)

    p = sub.add_parser("fetch", help="read-only check: did the sheet move?")
    _add_tab_arg(p)
    p.add_argument("--json", action="store_true")
    p.set_defaults(handler=cmd_fetch)

    p = sub.add_parser("log", help="local sync history")
    p.add_argument("--tab", default=None, help="only this tab")
    p.add_argument("-n", "--number", type=int, default=20, help="show last N entries")
    p.add_argument("--oneline", action="store_true")
    p.set_defaults(handler=cmd_log)

    p = sub.add_parser("remote", help="show or relocate the spreadsheet")
    remote_sub = p.add_subparsers(dest="remote_command", required=True)
    q = remote_sub.add_parser("show", help="print current source + mappings")
    q.set_defaults(handler=cmd_remote_show)
    q = remote_sub.add_parser("set", help="point at a new spreadsheet (resets lineage)")
    q.add_argument("url_or_id", help="spreadsheet URL or id")
    q.add_argument("--mode", choices=("keep-local", "keep-remote"), default="keep-local")
    q.set_defaults(handler=cmd_remote_set)

    p = sub.add_parser("rename", help="follow a sheet-tab rename in the mapping")
    p.add_argument("--old", required=True)
    p.add_argument("--new", required=True)
    p.set_defaults(handler=cmd_rename)

    p = sub.add_parser("validate", help="validate local CSVs (offline)")
    _add_tab_arg(p)
    p.set_defaults(handler=cmd_validate)

    p = sub.add_parser("doctor", help="health check: config, CSVs, auth, sheet")
    p.set_defaults(handler=cmd_doctor)

    p = sub.add_parser("version", help="print version")
    p.set_defaults(handler=cmd_version)

    p = sub.add_parser("tui", help="lazygit-style dashboard (needs the 'tui' extra)")
    p.set_defaults(handler=cmd_tui)

    return parser


def main(argv: list[str] | None = None, *, gateway: SheetsGateway | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging_mod.configure(verbose=args.verbose, quiet=args.quiet)
    root = resolve_root(args.root)
    try:
        config = load_config(root)
    except FccsvError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    ctx = build_context(root, config)
    active_gateway = gateway if gateway is not None else make_gateway(ctx, args)
    try:
        return int(args.handler(ctx, active_gateway, args))
    except FccsvError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
