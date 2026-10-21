"""Configuration data structures for gitcsv."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class TableMapping:
    """Mapping between a Google Sheet tab and a local CSV file."""

    tab: str
    csv_file: str
    preferred_key: tuple[str, ...] = ()
    column_map: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class AuthConfig:
    """Authentication configuration for Google Sheets API."""

    mode: str = "oauth"  # "oauth" or "service_account"
    credentials_file: str = ".gitcsv/credentials.json"
    token_file: str = ".gitcsv/token.json"
    credentials_json: str | None = None  # Raw pasted JSON string


@dataclass(frozen=True, slots=True)
class GitCsvConfig:
    """Full gitcsv project configuration."""

    spreadsheet_id: str
    csv_dir: Path
    tables: tuple[TableMapping, ...] = ()
    auth: AuthConfig = field(default_factory=AuthConfig)
    project_root: Path = field(default_factory=Path.cwd)

    def find_mapping(self, name_or_path: str) -> TableMapping | None:
        """Find a table mapping by sheet tab name or relative CSV file path."""
        # 1. Check exact tab name match
        for mapping in self.tables:
            if mapping.tab == name_or_path:
                return mapping

        # 2. Check CSV file path match
        normalized = name_or_path.replace("\\", "/")
        for mapping in self.tables:
            if mapping.csv_file.replace("\\", "/") == normalized:
                return mapping

        return None
