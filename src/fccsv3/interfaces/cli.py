"""Git-style v3 commands backed by one planning and recovery workflow."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from fccsv3 import __version__
from fccsv3.adapters.csv_file import read_csv_table
from fccsv3.adapters.google_sheets import GoogleSheetsGateway, GoogleSheetsValuesReader
from fccsv3.adapters.oauth import login as oauth_login
from fccsv3.adapters.state_db import StateDB, read_bases
from fccsv3.application.apply import (
    ApplyResult,
    OperationInterrupted,
    SheetValuesGateway,
    apply_table,
    plan_many_writes,
)
from fccsv3.application.cutover import audit_cutover, create_cutover_backup
from fccsv3.application.legacy_config import LegacyProject, load_legacy_project
from fccsv3.application.recover import inspect_recovery, recover_operation
from fccsv3.application.scan import SheetValuesReader, TableMapping, scan_tables
from fccsv3.application.undo import undo_operation
from fccsv3.domain.model import Diagnostic, TablePlan
from fccsv3.domain.validate import validate_snapshot
from fccsv3.interfaces.command_text import table_command


def _default_project(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / ".fccsv.json").is_file():
            return candidate
    raise ValueError("No .fccsv.json found. Pass --project <directory>.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fccsv3",
        description="Review and synchronize Craftland CSV tables with Google Sheets",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  fccsv3 validate BossLootData\n"
            "  fccsv3 status BossLootData\n"
            "  fccsv3 diff BossLootData --format json\n"
            "  fccsv3 push BossLootData --dry-run\n"
            "  fccsv3 push BossLootData --force\n"
            "  fccsv3 doctor\n"
            "  fccsv3 backup --output cutover.zip\n"
            "  fccsv3 auth login\n"
            "  fccsv3 recover"
        ),
    )
    parser.add_argument("--project", type=Path, default=None, help="project root")
    parser.add_argument("--token", type=Path, default=None, help="existing Google token file")
    parser.add_argument("--version", action="version", version=f"fccsv3 {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    auth = commands.add_parser("auth", help="manage the Google OAuth token")
    auth_actions = auth.add_subparsers(dest="auth_action", required=True)
    for action in ("status", "login"):
        auth_sub = auth_actions.add_parser(action)
        auth_sub.add_argument("--project", type=Path, default=argparse.SUPPRESS)
        auth_sub.add_argument("--token", type=Path, default=argparse.SUPPRESS)
        auth_sub.add_argument("--format", choices=("text", "json"), default="text")
        if action == "login":
            auth_sub.add_argument("--credentials", type=Path)
            auth_sub.add_argument("--reauth", action="store_true")
    doctor = commands.add_parser(
        "doctor", help="audit v2 and v3 state before switching writers"
    )
    doctor.add_argument("--project", type=Path, default=argparse.SUPPRESS)
    doctor.add_argument("--token", type=Path, default=argparse.SUPPRESS)
    doctor.add_argument("--format", choices=("text", "json"), default="text")
    backup = commands.add_parser(
        "backup", help="save CSV, Sheet values, configuration, and state to a ZIP"
    )
    backup.add_argument("--output", type=Path, required=True, metavar="ZIP")
    backup.add_argument("--project", type=Path, default=argparse.SUPPRESS)
    backup.add_argument("--token", type=Path, default=argparse.SUPPRESS)
    backup.add_argument("--format", choices=("text", "json"), default="text")
    validate = commands.add_parser(
        "validate",
        help="check local CSV shape without accessing Google Sheets",
        description="Validate local CSV header, type row, widths, and size limits.",
    )
    validate.add_argument("tables", nargs="*", metavar="TABLE", help="table names; default: all")
    validate.add_argument("--project", type=Path, default=argparse.SUPPRESS)
    validate.add_argument("--format", choices=("text", "json"), default="text")
    for command in ("status", "diff"):
        sub = commands.add_parser(command, help=f"read-only local/Sheet {command}")
        sub.add_argument("tables", nargs="*", metavar="TABLE", help="table names; default: all")
        sub.add_argument("--project", type=Path, default=argparse.SUPPRESS)
        sub.add_argument("--token", type=Path, default=argparse.SUPPRESS)
        sub.add_argument("--format", choices=("text", "json"), default="text")
        if command == "status":
            sub.add_argument("--fail-on-diff", action="store_true")
        else:
            sub.add_argument("--exit-code", action="store_true")
            sub.add_argument("--limit", type=int, default=50, help="maximum details per table")
    for command in ("push", "pull", "sync", "plan"):
        sub = commands.add_parser(command, help=f"review and apply a {command} operation")
        if command == "plan":
            sub.add_argument("direction", choices=("push", "pull", "sync"))
        sub.add_argument("table", nargs="?", metavar="TABLE", help="one table name")
        sub.add_argument("--all", action="store_true", help="all configured tables")
        sub.add_argument("--project", type=Path, default=argparse.SUPPRESS)
        sub.add_argument("--token", type=Path, default=argparse.SUPPRESS)
        sub.add_argument("--format", choices=("text", "json"), default="text")
        sub.add_argument("--accept-schema", action="store_true")
        sub.add_argument("--type", action="append", default=[], metavar="COLUMN=TYPE")
        if command != "sync":
            sub.add_argument("--force", action="store_true")
        if command != "plan":
            sub.add_argument("--dry-run", action="store_true")
    recover = commands.add_parser("recover", help="inspect or resolve an interrupted write")
    recover.add_argument("operation_id", nargs="?", metavar="OPERATION_ID")
    recover.add_argument("--project", type=Path, default=argparse.SUPPRESS)
    recover.add_argument("--token", type=Path, default=argparse.SUPPRESS)
    recover.add_argument("--format", choices=("text", "json"), default="text")
    choices = recover.add_mutually_exclusive_group()
    choices.add_argument("--continue", dest="continue_", action="store_true")
    choices.add_argument("--restore", action="store_true")
    recover.add_argument("--force", action="store_true")
    undo = commands.add_parser(
        "undo", help="revert the latest committed operation for one table"
    )
    undo.add_argument("table", metavar="TABLE")
    undo.add_argument("--operation", metavar="ID", help="verify a specific latest ID")
    undo.add_argument("--project", type=Path, default=argparse.SUPPRESS)
    undo.add_argument("--token", type=Path, default=argparse.SUPPRESS)
    undo.add_argument("--dry-run", action="store_true")
    undo.add_argument("--format", choices=("text", "json"), default="text")
    log = commands.add_parser("log", help="show local v3 operation history")
    log.add_argument("table", nargs="?", metavar="TABLE")
    log.add_argument("--project", type=Path, default=argparse.SUPPRESS)
    log.add_argument("--limit", type=int, default=20)
    log.add_argument("--format", choices=("text", "json"), default="text")
    tui = commands.add_parser("tui", help="guided terminal interface")
    tui.add_argument("--project", type=Path, default=argparse.SUPPRESS)
    tui.add_argument("--token", type=Path, default=argparse.SUPPRESS)
    return parser


def _diagnostic_payload(issue: Diagnostic) -> dict[str, object]:
    return asdict(issue)


def _validate(args: argparse.Namespace) -> int:
    root = (args.project or _default_project(Path.cwd())).resolve()
    project = load_legacy_project(root)
    by_name = {mapping.tab: mapping for mapping in project.mappings}
    if len(by_name) != len(project.mappings):
        raise ValueError("duplicate table names in .fccsv.json")
    selected = tuple(args.tables) if args.tables else tuple(by_name)
    unknown = tuple(name for name in selected if name not in by_name)
    if unknown:
        raise ValueError(f"unknown table(s): {', '.join(unknown)}")
    results: list[dict[str, object]] = []
    errors = 0
    for name in selected:
        path = project.csv_dir / by_name[name].csv_file
        if path.is_file():
            snapshot = read_csv_table(path, name=name)
            diagnostics = validate_snapshot(snapshot)
            rows, columns = len(snapshot.rows), len(snapshot.headers)
        else:
            diagnostics = (Diagnostic("LOCAL_MISSING", f"CSV file not found: {path}"),)
            rows, columns = 0, 0
        errors += sum(item.severity == "error" for item in diagnostics)
        results.append(
            {
                "table": name,
                "rows": rows,
                "columns": columns,
                "diagnostics": [_diagnostic_payload(item) for item in diagnostics],
            }
        )
    if args.format == "json":
        print(json.dumps({"version": 1, "command": "validate", "tables": results}))
    else:
        for result in results:
            issues = result["diagnostics"]
            state = "invalid" if any(issue["severity"] == "error" for issue in issues) else "ok"
            print(
                f"[{result['table']}] {state} "
                f"({result['rows']} rows, {result['columns']} columns)"
            )
            for issue in issues:
                location = f" line {issue['line']}:" if issue["line"] is not None else ":"
                print(f"  {issue['code']}{location} {issue['message']}")
        print(f"{len(results)} table(s) checked; {errors} error(s)")
    return 1 if errors else 0


def _selected_mappings(
    args: argparse.Namespace, project: LegacyProject
) -> tuple[TableMapping, ...]:
    mappings = project.mappings
    by_name = {mapping.tab: mapping for mapping in mappings}
    if len(by_name) != len(mappings):
        raise ValueError("duplicate table names in .fccsv.json")
    selected = tuple(args.tables) if args.tables else tuple(by_name)
    unknown = tuple(name for name in selected if name not in by_name)
    if unknown:
        raise ValueError(f"unknown table(s): {', '.join(unknown)}")
    return tuple(by_name[name] for name in dict.fromkeys(selected))


def _plans(args: argparse.Namespace, reader: SheetValuesReader | None) -> tuple[TablePlan, ...]:
    root = (args.project or _default_project(Path.cwd())).resolve()
    project = load_legacy_project(root)
    mappings = _selected_mappings(args, project)
    if reader is None:
        reader = _google_reader(args, root, project)
    bases = read_bases(root)
    return scan_tables(project.csv_dir, mappings, reader, bases=bases)


def _plan_payload(plan: TablePlan, *, details: bool) -> dict[str, object]:
    payload: dict[str, object] = {
        "table": plan.name,
        "status": plan.status,
        "local_rows": len(plan.local.rows) if plan.local else None,
        "sheet_rows": len(plan.remote.rows) if plan.remote else None,
        "has_diff": plan.has_diff,
        "identity": asdict(plan.identity) if plan.identity else None,
        "diagnostics": [_diagnostic_payload(issue) for issue in plan.diagnostics],
    }
    if details:
        payload["schema"] = asdict(plan.schema)
        payload["rows"] = asdict(plan.rows)
    return payload


def _show_plan_text(plan: TablePlan, *, details: bool, limit: int) -> None:
    identity = (
        ",".join(plan.identity.columns)
        if plan.identity and plan.identity.mode == "columns"
        else "row position"
    )
    print(f"[{plan.name}] {plan.status} | identity: {identity}")
    for issue in plan.diagnostics:
        print(f"  {issue.severity} {issue.code}: {issue.message}")
    if not details:
        print(
            f"  CSV {len(plan.local.rows) if plan.local else '-'} rows; "
            f"Sheet {len(plan.remote.rows) if plan.remote else '-'} rows"
        )
        if plan.has_diff and not plan.has_errors:
            parts = []
            if plan.schema.has_change:
                parts.append("schema")
            if plan.rows.only_local:
                parts.append(f"{len(plan.rows.only_local)} CSV-only rows")
            if plan.rows.only_remote:
                parts.append(f"{len(plan.rows.only_remote)} Sheet-only rows")
            if plan.rows.cells:
                parts.append(f"{len(plan.rows.cells)} changed cells")
            if plan.rows.order_changed:
                parts.append("row order")
            if parts:
                print(f"  changes: {', '.join(parts)}")
            print(f"  inspect: {table_command('diff', plan.name)}")
        return
    if plan.schema.has_change:
        print(f"  schema: {json.dumps(asdict(plan.schema), ensure_ascii=False)}")
    if plan.rows.order_changed:
        print("  row order changed")
    changes = (
        [f"  CSV only: {key}" for key in plan.rows.only_local]
        + [f"  Sheet only: {key}" for key in plan.rows.only_remote]
        + [
            f"  {change.row} / {change.column}: CSV {change.local!r}; Sheet {change.remote!r}"
            for change in plan.rows.cells
        ]
    )
    for line in changes[:limit]:
        print(line)
    if len(changes) > limit:
        print(f"  ... {len(changes) - limit} more change(s); use --limit")


def _inspect(args: argparse.Namespace, reader: SheetValuesReader | None) -> int:
    if args.command == "diff" and args.limit < 0:
        raise ValueError("--limit must be non-negative")
    plans = _plans(args, reader)
    details = args.command == "diff"
    if args.format == "json":
        print(
            json.dumps(
                {
                    "version": 1,
                    "command": args.command,
                    "tables": [_plan_payload(plan, details=details) for plan in plans],
                },
                ensure_ascii=False,
            )
        )
    else:
        for plan in plans:
            _show_plan_text(plan, details=details, limit=getattr(args, "limit", 0))
    has_change = any(plan.has_diff or plan.has_errors for plan in plans)
    requested_exit = getattr(args, "fail_on_diff", False) or getattr(args, "exit_code", False)
    return 1 if requested_exit and has_change else 0


def _project(args: argparse.Namespace) -> tuple[Path, LegacyProject]:
    root = (args.project or _default_project(Path.cwd())).resolve()
    return root, load_legacy_project(root)


def _google_reader(
    args: argparse.Namespace, root: Path, project: LegacyProject
) -> SheetValuesReader:
    if not project.spreadsheet_id:
        raise ValueError("no spreadsheet_id in .fccsv.json")
    token = args.token or project.token_file
    if not token.is_absolute():
        token = root / token
    return GoogleSheetsValuesReader.from_token(project.spreadsheet_id, token)


def _doctor(args: argparse.Namespace, reader: SheetValuesReader | None) -> int:
    root, project = _project(args)
    report = audit_cutover(root, project, reader or _google_reader(args, root, project))
    if args.format == "json":
        print(json.dumps({
            "version": 1, "command": "doctor", **asdict(report),
            "ready_for_review": report.ready_for_review,
        }, ensure_ascii=False))
    else:
        print(
            f"Cutover audit: {len(report.tables)} table(s), "
            f"{len(report.blockers)} blocker(s), {len(report.warnings)} warning(s)"
        )
        for table in report.tables:
            print(
                f"[{table.name}] {table.status} | CSV {table.csv_rows} rows, "
                f"Sheet {table.sheet_rows} rows | identity: {table.identity or '-'}"
            )
            print(f"  v2 BASE: {'found' if table.v2_base_present else 'missing'}")
            for error in table.errors:
                print(f"  error: {error}")
        for item in report.warnings:
            print(f"  warning: {item}")
        for item in report.blockers:
            print(f"  blocked: {item}")
        if report.pending_operations:
            print("  Inspect: fccsv3 recover")
    return 0 if report.ready_for_review else 1


def _backup(args: argparse.Namespace, reader: SheetValuesReader | None) -> int:
    root, project = _project(args)
    output = create_cutover_backup(
        root, project, reader or _google_reader(args, root, project), args.output
    )
    if args.format == "json":
        print(json.dumps({
            "version": 1, "command": "backup", "output": str(output),
            "tables": len(project.mappings),
        }, ensure_ascii=False))
    else:
        print(f"Cutover backup verified: {output}")
        print(f"  {len(project.mappings)} configured table(s) included")
    return 0


def _auth(args: argparse.Namespace) -> int:
    root, project = _project(args)
    token = args.token or project.token_file
    if not token.is_absolute():
        token = root / token
    if args.auth_action == "status":
        exists = token.is_file()
        if args.format == "json":
            print(json.dumps({"version": 1, "token_file": str(token), "exists": exists}))
        else:
            state = "found (not verified online)" if exists else "missing"
            print(f"Google token {state}: {token}")
            if not exists:
                print("  Sign in: fccsv3 auth login")
        return 0 if exists else 1
    client = args.credentials or project.credentials_file
    if not client.is_absolute():
        client = root / client
    oauth_login(client, token, reauth=args.reauth)
    if args.format == "json":
        print(json.dumps({"version": 1, "token_file": str(token), "ready": True}))
    else:
        print(f"Google token ready: {token}")
    return 0


def _google_gateway(
    args: argparse.Namespace,
    root: Path,
    project: LegacyProject,
) -> GoogleSheetsGateway:
    if not project.spreadsheet_id:
        raise ValueError("no spreadsheet_id in .fccsv.json")
    token = args.token or project.token_file
    if not token.is_absolute():
        token = root / token
    return GoogleSheetsGateway.from_token(project.spreadsheet_id, token)


def _mapping(project: LegacyProject, name: str) -> TableMapping:
    matches = tuple(mapping for mapping in project.mappings if mapping.tab == name)
    if len(matches) != 1:
        raise ValueError(f"expected exactly one mapping for table {name!r}")
    return matches[0]


def _types(values: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in values:
        column, separator, kind = raw.partition("=")
        if not separator or not column.strip() or not kind.strip():
            raise ValueError(f"expected --type COLUMN=TYPE, got {raw!r}")
        if column in result:
            raise ValueError(f"duplicate --type for {column}")
        result[column] = kind
    return result


def _instruction(message: str, table: str) -> str:
    return message.replace("push TABLE", table_command("push", table)).replace(
        "pull TABLE", table_command("pull", table)
    ).replace("sync TABLE", table_command("sync", table))


def _sync_item(result: ApplyResult, *, dry_run: bool) -> dict[str, object]:
    decision = result.decision
    return {
        "table": result.plan.name,
        "allowed": decision.allowed,
        "dry_run": dry_run,
        "write_csv": decision.write_local,
        "write_sheet": decision.write_remote,
        "operation_id": result.operation_id,
        "verified": result.verified,
        "errors": [
            _instruction(message, result.plan.name) for message in decision.errors
        ],
        "plan": _plan_payload(result.plan, details=True),
    }


def _show_sync_item(result: ApplyResult, kind: str, *, dry_run: bool) -> None:
    decision = result.decision
    action = "preview" if dry_run else "applied"
    print(f"[{result.plan.name}] {kind} {action} | {result.plan.status}")
    _show_plan_text(result.plan, details=False, limit=0)
    for error in decision.errors:
        print(f"  blocked: {_instruction(error, result.plan.name)}")
    if decision.allowed:
        print(
            f"  writes: CSV {'yes' if decision.write_local else 'no'}, "
            f"Sheet {'yes' if decision.write_remote else 'no'}"
        )
        if result.operation_id:
            print(f"  verified; operation {result.operation_id}")


def _sync_all(
    args: argparse.Namespace,
    root: Path,
    project: LegacyProject,
    gateway: SheetValuesGateway,
    kind: str,
    *,
    dry_run: bool,
) -> int:
    if args.type:
        raise ValueError("--type with --all is ambiguous; pull changed tables separately")
    mappings = project.mappings
    names = tuple(mapping.tab for mapping in mappings)
    if not names or len(set(names)) != len(names):
        raise ValueError("--all needs a nonempty set of unique table mappings")
    previews = plan_many_writes(
        root, project.csv_dir, mappings, gateway, kind,
        force=getattr(args, "force", False), accept_schema=args.accept_schema,
    )
    blocked = any(not item.decision.allowed for item in previews)
    if dry_run or blocked:
        if args.format == "json":
            print(json.dumps({
                "version": 1, "command": args.command, "direction": kind,
                "all": True, "preflight_ok": not blocked,
                "tables": [_sync_item(item, dry_run=True) for item in previews],
            }, ensure_ascii=False))
        else:
            for item in previews:
                _show_sync_item(item, kind, dry_run=True)
            if blocked:
                print("No tables written: at least one table is blocked.")
        return 1 if blocked else 0

    completed: list[ApplyResult] = []
    failure: str | None = None
    pending_id: str | None = None
    exit_code = 0
    for mapping in mappings:
        try:
            result = apply_table(
                root, project.csv_dir, mapping, gateway, kind,
                force=getattr(args, "force", False),
                accept_schema=args.accept_schema,
            )
            if not result.decision.allowed:
                failure = f"{mapping.tab} changed since preflight; run plan again"
                exit_code = 1
                break
            completed.append(result)
        except OperationInterrupted as error:
            pending_id = error.operation_id
            failure = str(error)
            exit_code = 3
            break
        except (OSError, ValueError, RuntimeError) as error:
            failure = str(error)
            exit_code = 2
            break
    if args.format == "json":
        print(json.dumps({
            "version": 1, "command": args.command, "direction": kind,
            "all": True, "preflight_ok": True,
            "completed": [_sync_item(item, dry_run=False) for item in completed],
            "failure": failure, "pending_operation_id": pending_id,
        }, ensure_ascii=False))
    else:
        for item in completed:
            _show_sync_item(item, kind, dry_run=False)
        print(f"Completed {len(completed)} of {len(mappings)} table(s).")
        if failure:
            print(f"Stopped: {failure}", file=sys.stderr)
    return exit_code


def _sync(
    args: argparse.Namespace, gateway: SheetValuesGateway | None
) -> int:
    root, project = _project(args)
    kind = args.direction if args.command == "plan" else args.command
    if args.all == (args.table is not None):
        raise ValueError("provide exactly one TABLE or --all")
    types = _types(args.type)
    if types and kind != "pull":
        raise ValueError("--type applies to pull; Sheet columns need CSV type declarations")
    if gateway is None:
        gateway = _google_gateway(args, root, project)
    dry_run = args.command == "plan" or getattr(args, "dry_run", False)
    if args.all:
        return _sync_all(args, root, project, gateway, kind, dry_run=dry_run)
    mapping = _mapping(project, args.table)
    result = apply_table(
        root, project.csv_dir, mapping, gateway, kind,
        force=getattr(args, "force", False),
        accept_schema=args.accept_schema,
        new_types=types,
        dry_run=dry_run,
    )
    if args.format == "json":
        print(json.dumps({
            "version": 1,
            "command": args.command,
            "direction": kind,
            **_sync_item(result, dry_run=dry_run),
        }, ensure_ascii=False))
    else:
        _show_sync_item(result, kind, dry_run=dry_run)
    return 0 if result.decision.allowed else 1


def _recover(
    args: argparse.Namespace, gateway: SheetValuesGateway | None
) -> int:
    root, project = _project(args)
    state_path = root / ".fccsv3" / "state.sqlite3"
    choice = "continue" if args.continue_ else "restore" if args.restore else None
    if args.force and choice is None:
        raise ValueError("--force requires --continue or --restore")
    if not state_path.is_file():
        if args.operation_id:
            raise ValueError("no v3 state database; there is no operation to recover")
        print("No pending v3 operations.")
        return 0
    with StateDB(root, read_only=True) as state:
        pending = state.pending()
        record = state.get(args.operation_id) if args.operation_id else None
    if args.operation_id is None:
        if choice:
            raise ValueError("provide an OPERATION_ID with --continue or --restore")
        if args.format == "json":
            print(json.dumps({"version": 1, "pending": [
                {"id": item.id, "table": item.table, "kind": item.kind,
                 "started_at": item.started_at, "error": item.error}
                for item in pending
            ]}))
        else:
            if not pending:
                print("No pending v3 operations.")
            for item in pending:
                print(f"{item.id} {item.table} {item.kind} pending: {item.error or '-'}")
        return 0
    if record is None or record.status != "pending":
        raise ValueError(f"pending operation not found: {args.operation_id}")
    mapping = _mapping(project, record.table)
    if gateway is None:
        gateway = _google_gateway(args, root, project)
    inspection = inspect_recovery(
        root, project.csv_dir, mapping, gateway, record.id
    )
    if choice is not None:
        recover_operation(
            root, project.csv_dir, mapping, gateway, record.id, choice,
            force=args.force,
        )
    if args.format == "json":
        print(json.dumps({
            "version": 1, "id": record.id, "table": record.table,
            "csv": inspection.local, "sheet": inspection.remote,
            "action": choice or "inspect",
        }))
    else:
        print(
            f"{record.id} {record.table}: CSV={inspection.local}, "
            f"Sheet={inspection.remote}"
        )
        if choice is None:
            print(
                f"  Choose: fccsv3 recover {record.id} --continue"
                f"  or  fccsv3 recover {record.id} --restore"
            )
        else:
            print(f"  {choice} verified")
    return 0


def _log(args: argparse.Namespace) -> int:
    root, _ = _project(args)
    if args.limit < 1:
        raise ValueError("--limit must be positive")
    if (root / ".fccsv3" / "state.sqlite3").is_file():
        with StateDB(root, read_only=True) as state:
            records = state.history(args.table, args.limit)
    else:
        records = ()
    if args.format == "json":
        print(json.dumps({"version": 1, "operations": [
            {"id": item.id, "table": item.table, "kind": item.kind,
             "status": item.status, "started_at": item.started_at,
             "finished_at": item.finished_at, "error": item.error,
             "source_operation_id": item.source_operation_id}
            for item in records
        ]}))
    else:
        for item in records:
            print(f"{item.id} {item.started_at} {item.table} {item.kind} {item.status}")
        if not records:
            print("No v3 operations yet.")
    return 0


def _undo(
    args: argparse.Namespace, gateway: SheetValuesGateway | None
) -> int:
    root, project = _project(args)
    mapping = _mapping(project, args.table)
    if gateway is None:
        gateway = _google_gateway(args, root, project)
    result = undo_operation(
        root, project.csv_dir, mapping, gateway,
        source_operation_id=args.operation, dry_run=args.dry_run,
    )
    if args.format == "json":
        print(json.dumps({
            "version": 1, "table": mapping.tab, "allowed": result.allowed,
            "dry_run": args.dry_run,
            "source_operation_id": result.source.id if result.source else None,
            "undo_operation_id": result.operation_id,
            "errors": result.errors,
            "restore_csv_rows": (
                len(result.source.before_local.rows) if result.source else None
            ),
            "restore_sheet_rows": (
                len(result.source.before_remote.rows) if result.source else None
            ),
        }, ensure_ascii=False))
    else:
        if result.source:
            action = "would undo" if args.dry_run else "undid"
            print(f"[{mapping.tab}] {action} {result.source.id}")
            print(
                f"  restore CSV {len(result.source.before_local.rows)} rows; "
                f"Sheet {len(result.source.before_remote.rows)} rows"
            )
        for error in result.errors:
            print(f"  blocked: {error}")
        if result.operation_id:
            print(f"  verified; undo operation {result.operation_id}")
    return 0 if result.allowed else 1


def main(
    argv: Sequence[str] | None = None,
    *,
    reader: SheetValuesReader | None = None,
    gateway: SheetValuesGateway | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "auth":
            return _auth(args)
        if args.command == "validate":
            return _validate(args)
        if args.command == "doctor":
            return _doctor(args, reader or gateway)
        if args.command == "backup":
            return _backup(args, reader or gateway)
        if args.command in ("status", "diff"):
            return _inspect(args, reader or gateway)
        if args.command in ("push", "pull", "sync", "plan"):
            return _sync(args, gateway)
        if args.command == "recover":
            return _recover(args, gateway)
        if args.command == "undo":
            return _undo(args, gateway)
        if args.command == "log":
            return _log(args)
        if args.command == "tui":
            from fccsv3.interfaces.tui import run_tui

            root, project = _project(args)
            return run_tui(root, gateway or _google_gateway(args, root, project))
    except OperationInterrupted as error:
        print(f"fccsv3: {error}", file=sys.stderr)
        return 3
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f"fccsv3: {error}", file=sys.stderr)
        return 2
    return 2
