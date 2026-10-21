"""Project initialization wizard and table auto-discovery service."""

from __future__ import annotations

import json
from pathlib import Path

from gitcsv.config.loader import extract_spreadsheet_id
from gitcsv.storage.sheets_api import SheetsGateway


def setup_gitignore(project_root: Path) -> list[str]:
    """Ensure sensitive gitcsv files and directories are ignored in .gitignore."""
    gitignore_path = project_root / ".gitignore"
    patterns_to_ignore = [
        ".gitcsv/",
        ".env.local",
        "*.token.json",
        "credentials.json",
        "service_account.json",
    ]

    existing_lines: set[str] = set()
    if gitignore_path.is_file():
        existing_lines = {
            line.strip() for line in gitignore_path.read_text(encoding="utf-8").splitlines()
        }

    added: list[str] = []
    lines_to_append: list[str] = []

    for pattern in patterns_to_ignore:
        if pattern not in existing_lines:
            lines_to_append.append(pattern)
            added.append(pattern)

    if lines_to_append:
        with gitignore_path.open("a", encoding="utf-8") as f:
            if existing_lines and not gitignore_path.read_text(encoding="utf-8").endswith("\n"):
                f.write("\n")
            f.write("# gitcsv local files\n")
            for item in lines_to_append:
                f.write(f"{item}\n")

    return added


def auto_discover_tables(
    csv_dir: Path,
    remote_tabs: list[str] | None = None,
) -> list[dict[str, str]]:
    """Match CSV files with remote sheet tabs automatically."""
    csv_dir.mkdir(parents=True, exist_ok=True)
    csv_files = {f.stem.lower(): f for f in csv_dir.glob("*.csv")}

    mappings: list[dict[str, str]] = []

    # 1. If remote tabs are known, match by tab name
    if remote_tabs:
        for tab in remote_tabs:
            tab_lower = tab.lower()
            if tab_lower in csv_files:
                matched_file = csv_files[tab_lower].name
                mappings.append({"tab": tab, "csv": matched_file})
            else:
                # Tab exists in Sheet but not on disk -> suggest TabName.csv
                mappings.append({"tab": tab, "csv": f"{tab}.csv"})
        return mappings

    # 2. If remote tabs not available, infer from local CSV files
    for file in sorted(csv_dir.glob("*.csv")):
        mappings.append({"tab": file.stem, "csv": file.name})

    return mappings


def initialize_project(
    project_root: Path,
    spreadsheet_url_or_id: str,
    *,
    csv_dir_rel: str = "Assets/CSV",
    auth_mode: str = "oauth",
    credentials_file: str = ".gitcsv/credentials.json",
    token_file: str = ".gitcsv/token.json",
    credentials_json: str | None = None,
    gateway: SheetsGateway | None = None,
) -> dict[str, object]:
    """Create .gitcsv directory, write config.json, and configure .gitignore."""
    gitcsv_dir = project_root / ".gitcsv"
    gitcsv_dir.mkdir(parents=True, exist_ok=True)

    sheet_id = extract_spreadsheet_id(spreadsheet_url_or_id)
    csv_dir = project_root / csv_dir_rel
    csv_dir.mkdir(parents=True, exist_ok=True)

    # 1. Discover tables
    remote_tabs: list[str] | None = None
    if gateway is not None:
        try:
            remote_tabs = gateway.list_tabs()
        except Exception:
            remote_tabs = None

    table_mappings = auto_discover_tables(csv_dir, remote_tabs)

    # 2. Build config payload
    config_payload = {
        "spreadsheet_id": sheet_id,
        "csv_dir": csv_dir_rel.replace("\\", "/"),
        "auth": {
            "mode": auth_mode,
            "credentials_file": credentials_file,
            "token_file": token_file,
        },
        "tables": table_mappings,
    }

    # 3. If raw JSON credentials were provided, save them
    if credentials_json:
        cred_dest = project_root / credentials_file
        cred_dest.parent.mkdir(parents=True, exist_ok=True)
        cred_dest.write_text(credentials_json.strip(), encoding="utf-8")

    # 4. Save .gitcsv/config.json
    config_file = gitcsv_dir / "config.json"
    config_file.write_text(json.dumps(config_payload, indent=2, ensure_ascii=False), encoding="utf-8")

    # 5. Configure .gitignore
    ignored = setup_gitignore(project_root)

    return {
        "spreadsheet_id": sheet_id,
        "csv_dir": csv_dir_rel,
        "tables_count": len(table_mappings),
        "config_file": str(config_file),
        "ignored_patterns": ignored,
    }
