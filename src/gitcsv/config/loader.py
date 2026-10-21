"""Cascade configuration loader for gitcsv.

Loads settings with the following priority:
1. Explicit CLI arguments or runtime overrides.
2. Environment variables (GITCSV_*).
3. Project .env.local or .env files.
4. Project config (.gitcsv/config.json or legacy .fccsv.json).
5. User global config (~/.gitcsv/config.json).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from gitcsv.config.schema import AuthConfig, GitCsvConfig, TableMapping

SPREADSHEET_URL_PATTERN = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")


class ConfigError(Exception):
    """Raised when gitcsv configuration is missing or invalid."""


def extract_spreadsheet_id(input_value: str) -> str:
    """Extract a Google Spreadsheet ID from either a full URL or a raw ID string."""
    clean = input_value.strip()

    # 1. Match full Google Sheets URL
    url_match = SPREADSHEET_URL_PATTERN.search(clean)
    if url_match:
        return url_match.group(1)

    # 2. Return raw ID if alphanumeric
    if re.fullmatch(r"[a-zA-Z0-9-_]+", clean):
        return clean

    raise ConfigError(f"Invalid Google Spreadsheet ID or URL: {input_value}")


def find_project_root(start_dir: Path | None = None) -> Path:
    """Find the root directory containing gitcsv or fccsv configuration."""
    current = (start_dir or Path.cwd()).resolve()

    for candidate in (current, *current.parents):
        if (candidate / ".gitcsv" / "config.json").is_file():
            return candidate
        if (candidate / "gitcsv.json").is_file():
            return candidate
        if (candidate / ".fccsv.json").is_file():
            return candidate

    return current


def load_config(
    project_root: Path | None = None,
    *,
    spreadsheet_id_override: str | None = None,
    token_override: Path | None = None,
    credentials_override: Path | None = None,
    require_spreadsheet_id: bool = True,
) -> GitCsvConfig:
    """Load gitcsv configuration for the project."""
    root = (project_root or find_project_root()).resolve()

    # 1. Load environment variables from .env.local or .env
    env_vars: dict[str, str] = {}
    for env_name in (".env.local", ".env"):
        env_path = root / env_name
        if env_path.is_file():
            for key, val in dotenv_values(env_path).items():
                if val is not None:
                    env_vars[key] = str(val)

    # 2. Look for config files
    raw_config: dict[str, Any] = {}
    config_file: Path | None = None

    if (root / ".gitcsv" / "config.json").is_file():
        config_file = root / ".gitcsv" / "config.json"
    elif (root / "gitcsv.json").is_file():
        config_file = root / "gitcsv.json"
    elif (root / ".fccsv.json").is_file():
        config_file = root / ".fccsv.json"

    if config_file is not None:
        try:
            raw_config = json.loads(config_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as err:
            raise ConfigError(f"Malformed JSON in configuration file {config_file}: {err}") from err

    # 3. Handle legacy .fccsv.json format if applicable
    if "sources" in raw_config or "spreadsheet_id" in raw_config and "tables" not in raw_config:
        sources = raw_config.get("sources", [raw_config])
        source = sources[0] if sources else {}
        tables_raw = source.get("tables", [])
        sheet_id_raw = source.get("spreadsheet_id", "")
    else:
        tables_raw = raw_config.get("tables", [])
        sheet_id_raw = raw_config.get("spreadsheet_id", "")

    # 4. Resolve Spreadsheet ID
    final_sheet_id = (
        spreadsheet_id_override
        or os.environ.get("GITCSV_SPREADSHEET_ID")
        or env_vars.get("GITCSV_SPREADSHEET_ID")
        or sheet_id_raw
    )
    if not final_sheet_id:
        if require_spreadsheet_id:
            raise ConfigError(
                f"No Google Spreadsheet ID configured in {root}.\n"
                "Run 'gitcsv init' to set up this project."
            )
        spreadsheet_id = ""
    else:
        spreadsheet_id = extract_spreadsheet_id(final_sheet_id)

    # 5. Resolve CSV Directory
    csv_dir_raw = raw_config.get("csv_dir", "Assets/CSV")
    csv_dir = (root / csv_dir_raw).resolve()

    # 6. Resolve Table Mappings
    mappings: list[TableMapping] = []
    for entry in tables_raw:
        if not isinstance(entry, dict):
            continue

        tab = entry.get("tab")
        csv_file = entry.get("csv")
        if not tab or not csv_file:
            continue

        preferred_key = tuple(
            k.strip() for k in entry.get("key_column", "").split(",") if k.strip()
        )
        col_map = tuple(entry.get("column_map", {}).items())

        mappings.append(
            TableMapping(
                tab=tab,
                csv_file=csv_file,
                preferred_key=preferred_key,
                column_map=col_map,
            )
        )

    # 7. Resolve Authentication Settings
    raw_auth = raw_config.get("auth", {})
    auth_mode = os.environ.get("GITCSV_AUTH_MODE") or raw_auth.get("mode", "oauth")

    cred_path = (
        str(credentials_override)
        if credentials_override
        else os.environ.get("GITCSV_CREDENTIALS_FILE")
        or env_vars.get("GITCSV_CREDENTIALS_FILE")
        or raw_auth.get("credentials_file", ".gitcsv/credentials.json")
    )

    tok_path = (
        str(token_override)
        if token_override
        else os.environ.get("GITCSV_TOKEN_FILE")
        or env_vars.get("GITCSV_TOKEN_FILE")
        or raw_auth.get("token_file", ".gitcsv/token.json")
    )

    cred_json = (
        os.environ.get("GITCSV_CREDENTIALS_JSON")
        or env_vars.get("GITCSV_CREDENTIALS_JSON")
        or raw_auth.get("credentials_json")
    )

    auth = AuthConfig(
        mode=auth_mode,
        credentials_file=cred_path,
        token_file=tok_path,
        credentials_json=cred_json,
    )

    return GitCsvConfig(
        spreadsheet_id=spreadsheet_id,
        csv_dir=csv_dir,
        tables=tuple(mappings),
        auth=auth,
        project_root=root,
    )
