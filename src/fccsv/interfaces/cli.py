"""Argparse CLI. Thin by design: parse args, resolve config, call app services.

Exit codes: 0 ok, 1 sync/validation error, 2 usage/config error.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fccsv import __version__
from fccsv.adapters.sheets_gateway import InMemorySheetsGateway, spreadsheet_id_from_url
from fccsv.app import init_service, sync_service
from fccsv.app.auth_service import load_credentials, resolve_auth_paths
from fccsv.core import config as config_mod
from fccsv.core.constants import SCOPES
from fccsv.core.logging import configure, get_logger
from fccsv.domain.exceptions import FccsvError

log = get_logger("fccsv")


def _gateway_for(config, project_root: Path, *, offline: bool, args=None):
    if offline or not config.spreadsheet_id:
        return InMemorySheetsGateway()
    cred_file, token_file = resolve_auth_paths(
        project_root,
        config.credentials_file,
        config.token_file,
        credentials=getattr(args, "credentials", None),
        token=getattr(args, "token", None),
    )
    try:
        creds = load_credentials(token_file, cred_file, SCOPES)
    except ImportError as exc:
        raise FccsvError(str(exc)) from exc
    from fccsv.adapters.sheets_gateway import GoogleSheetsGateway

    return GoogleSheetsGateway(config.spreadsheet_id, creds)


def _common() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=None, help="Project root (auto-detected by default)")
    common.add_argument("--offline", action="store_true", help="Skip Google auth; use empty sheet")
    common.add_argument("--verbose", action="store_true", help="Debug logging on stderr")
    common.add_argument("--quiet", action="store_true", help="Errors only on stderr")
    common.add_argument(
        "--credentials", default=None, help="credentials.json path (or $FCCSV_CREDENTIALS)"
    )
    common.add_argument("--token", default=None, help="token.json path (or $FCCSV_TOKEN)")
    return common


def _add_sync_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tab", default=None, help="Single tab, or all mapped when omitted")
    parser.add_argument(
        "--allow-schema-change",
        action="store_true",
        help="Permit column add/remove/reorder (needs .fcc + compile after)",
    )
    parser.add_argument(
        "--force",
        choices=("push", "pull"),
        default=None,
        help="Resolve both-sides-changed conflicts: keep local (push) or sheet (pull)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")


def build_parser() -> argparse.ArgumentParser:
    common = _common()
    p = argparse.ArgumentParser(
        prog="fccsv", description="FC CSV <-> Google Sheets sync", parents=[common]
    )
    sub = p.add_subparsers(dest="command", required=True)

    a = sub.add_parser("auth", help="Run OAuth flow and save token.json", parents=[common])
    a.set_defaults(func=_cmd_auth)

    i = sub.add_parser("init", help="Link a sheet and auto-map tabs to CSVs", parents=[common])
    i.add_argument("--sheet", required=True, help="Spreadsheet URL or id")
    i.set_defaults(func=_cmd_init)

    s = sub.add_parser("status", help="Show per-table sync state", parents=[common])
    s.set_defaults(func=_cmd_status)

    pu = sub.add_parser("push", help="Push local CSVs -> sheet tabs", parents=[common])
    _add_sync_flags(pu)
    pu.set_defaults(func=_cmd_push)

    pl = sub.add_parser("pull", help="Pull sheet tabs -> local CSVs", parents=[common])
    _add_sync_flags(pl)
    pl.set_defaults(func=_cmd_pull)

    v = sub.add_parser(
        "validate",
        help="Check local CSVs (width, keys, headers) without any sheet",
        parents=[common],
    )
    v.add_argument("--tab", default=None, help="Single tab, or all mapped when omitted")
    v.set_defaults(func=_cmd_validate)

    d = sub.add_parser("diff", help="Alias for status", parents=[common])
    d.set_defaults(func=_cmd_status)

    ver = sub.add_parser("version", help="Print version", parents=[common])
    ver.set_defaults(func=_cmd_version)
    return p


def _cmd_auth(args, cfg, root: Path) -> int:
    cred_file, token_file = resolve_auth_paths(
        root,
        cfg.credentials_file,
        cfg.token_file,
        credentials=args.credentials,
        token=args.token,
    )
    load_credentials(token_file, cred_file, SCOPES)
    print(f"saved {token_file}")
    return 0


def _cmd_init(args, cfg, root: Path) -> int:
    next_cfg = config_mod.FccsvConfig.load(root)
    next_cfg.spreadsheet_id = spreadsheet_id_from_url(args.sheet)
    gateway = _gateway_for(next_cfg, root, offline=args.offline, args=args)
    tabs: list[str] = []
    if not args.offline:
        try:
            tabs = gateway.list_tabs()
        except FccsvError as exc:
            log.warning("could not list tabs (%s); mapping from local CSVs only", exc)

    fresh = init_service.init_from_url_or_id(args.sheet, tabs, next_cfg.csv_dir_path(root))
    fresh.credentials_file = next_cfg.credentials_file
    fresh.token_file = next_cfg.token_file
    fresh.state_file = next_cfg.state_file
    fresh.csv_dir = next_cfg.csv_dir
    path = fresh.save(root)
    print(f"wrote {path} with {len(fresh.tables)} tables, id={fresh.spreadsheet_id}")
    return 0


def _print_status(statuses) -> None:
    width = max([len(st.name) for st in statuses] + [4])
    print(f"{'TABLE':<{width}}  STATE")
    for st in statuses:
        if st.local_only:
            print(f"{st.name:<{width}}  local-only (push will create the tab)")
        elif st.remote_only:
            print(f"{st.name:<{width}}  remote-only (pull will create the CSV)")
        elif st.conflict:
            print(f"{st.name:<{width}}  CONFLICT: both sides changed (--force push|pull)")
        elif st.validation_issues:
            print(f"{st.name:<{width}}  invalid local: {'; '.join(st.validation_issues)}")
        elif st.in_sync:
            print(f"{st.name:<{width}}  in-sync")
        else:
            bits = []
            if st.schema_diff.has_change:
                bits.append(
                    f"schema added={st.schema_diff.added} "
                    f"removed={st.schema_diff.removed} "
                    f"reordered={st.schema_diff.reordered}"
                )
            if st.row_diff.has_change:
                bits.append(
                    f"rows +{len(st.row_diff.added_keys)} "
                    f"-{len(st.row_diff.removed_keys)} "
                    f"~{len(st.row_diff.changed_keys)}"
                )
            print(f"{st.name:<{width}}  " + "; ".join(bits))


def _require_sheet(cfg) -> int | None:
    if not cfg.spreadsheet_id:
        print("no spreadsheet linked; run `fccsv init --sheet <url>` first")
        return 2
    return None


def _cmd_status(args, cfg, root: Path) -> int:
    if (code := _require_sheet(cfg)) is not None:
        return code
    gateway = _gateway_for(cfg, root, offline=args.offline, args=args)
    _print_status(sync_service.collect_status(cfg, root, gateway))
    return 0


def _each_tab(cfg, tab: str | None) -> list[str]:
    if tab:
        if cfg.table_by_tab(tab) is None:
            raise FccsvError(f"Unknown tab '{tab}'. Check `fccsv status` for mapped names.")
        return [tab]
    return [t.tab for t in cfg.tables]


def _run_many(args, cfg, root: Path, *, direction: str) -> int:
    """Single tab -> per-table call; many tabs -> one batched call."""
    if (code := _require_sheet(cfg)) is not None:
        return code
    gateway = _gateway_for(cfg, root, offline=args.offline, args=args)
    tabs = _each_tab(cfg, args.tab)
    if args.tab is not None:
        func = sync_service.push_table if direction == "push" else sync_service.pull_table
        try:
            print(
                func(
                    cfg,
                    root,
                    gateway,
                    tabs[0],
                    allow_schema_change=args.allow_schema_change,
                    dry_run=args.dry_run,
                    force=args.force,
                )
            )
        except (FccsvError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        return 0
    func_all = sync_service.push_all if direction == "push" else sync_service.pull_all
    try:
        oks, errs = func_all(
            cfg,
            root,
            gateway,
            tabs,
            allow_schema_change=args.allow_schema_change,
            dry_run=args.dry_run,
            force=args.force,
        )
    except FccsvError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for line in oks:
        print(line)
    for line in errs:
        print(line, file=sys.stderr)
    return 1 if errs else 0


def _cmd_push(args, cfg, root: Path) -> int:
    return _run_many(args, cfg, root, direction="push")


def _cmd_pull(args, cfg, root: Path) -> int:
    return _run_many(args, cfg, root, direction="pull")


def _cmd_validate(args, cfg, root: Path) -> int:
    from fccsv.adapters.local_store import read_csv_table
    from fccsv.domain.validate import validate_snapshot

    csv_dir = cfg.csv_dir_path(root)
    failures = 0
    for tab in _each_tab(cfg, args.tab):
        table = cfg.table_by_tab(tab)
        assert table is not None  # narrowed by _each_tab
        csv_path = csv_dir / table.csv
        if not csv_path.exists():
            print(f"{tab}: missing {table.csv}")
            failures += 1
            continue
        issues = validate_snapshot(read_csv_table(csv_path))
        if issues:
            print(f"{tab}: " + "; ".join(issues))
            failures += 1
        else:
            print(f"{tab}: ok")
    return 1 if failures else 0


def _cmd_version(args, cfg, root: Path) -> int:
    print(f"fccsv {__version__}")
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure(verbose=args.verbose, quiet=args.quiet)
    root = config_mod.resolve_project_root(args.root)
    cfg = config_mod.FccsvConfig.load(root)
    try:
        code = args.func(args, cfg, root)
    except FccsvError as exc:
        print(f"error: {exc}", file=sys.stderr)
        code = 1
    raise SystemExit(code)
