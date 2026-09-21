"""`doctor`: health check for auth, sheet reachability, and local CSVs.

Read-only. Each check returns a line; nonzero exit when any check fails.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from fccsv.adapters import local as local_mod
from fccsv.core.config import FccsvConfig


class TabLister(Protocol):
    """Narrow surface `doctor` needs: list tabs, nothing else."""

    def list_tabs(self) -> list[str]: ...


def check_config(config: FccsvConfig) -> list[str]:
    issues = config.validate()
    if not config.active.spreadsheet_id:
        issues.append("no spreadsheet_id (run init or remote set)")
    return issues


def check_csv_dir(csv_dir: Path) -> list[str]:
    if not csv_dir.is_dir():
        return [f"csv dir missing: {csv_dir}"]
    files = local_mod.list_csv_files(csv_dir)
    if not files:
        return [f"no CSV files in {csv_dir}"]
    issues: list[str] = []
    for name in files:
        try:
            snap = local_mod.read_csv_table(csv_dir / name)
        except Exception as exc:
            issues.append(f"{name}: unreadable ({exc})")
            continue
        if not snap.headers:
            issues.append(f"{name}: no header row")
    return issues


def check_auth_files(client_file: Path | None, token_file: Path) -> list[str]:
    issues: list[str] = []
    if client_file is None:
        issues.append("no OAuth client (credentials.json / env) found")
    elif not client_file.exists():
        issues.append(f"client file missing: {client_file}")
    if not token_file.exists():
        issues.append(f"no token yet: {token_file} (run auth)")
    return issues


def check_gateway(gateway: TabLister) -> list[str]:
    try:
        tabs = gateway.list_tabs()
    except Exception as exc:
        return [f"sheet unreachable: {exc}"]
    if not tabs:
        return ["sheet has no tabs"]
    return []
