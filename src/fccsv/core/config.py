"""Project config: spreadsheet link + table mappings. No Sheets I/O here."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from fccsv.core.constants import (
    DEFAULT_CONFIG_FILENAME,
    DEFAULT_CREDENTIALS_FILENAME,
    DEFAULT_CSV_DIRNAME,
    DEFAULT_STATE_FILENAME,
    DEFAULT_TOKEN_FILENAME,
    find_project_root,
)


@dataclass
class TableMapping:
    """One tab <-> one CSV file. Column maps are sheet_header -> csv_header."""

    tab: str
    csv: str
    key_column: str = ""
    column_map: dict[str, str] = field(default_factory=dict)
    skip_type_row_in_sheet: bool = True


@dataclass
class FccsvConfig:
    spreadsheet_id: str = ""
    csv_dir: str = DEFAULT_CSV_DIRNAME
    tables: list[TableMapping] = field(default_factory=list)
    credentials_file: str = DEFAULT_CREDENTIALS_FILENAME
    token_file: str = DEFAULT_TOKEN_FILENAME
    state_file: str = DEFAULT_STATE_FILENAME

    @classmethod
    def config_path(cls, project_root: Path) -> Path:
        return project_root / DEFAULT_CONFIG_FILENAME

    @classmethod
    def load(cls, project_root: Path) -> FccsvConfig:
        path = cls.config_path(project_root)
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        tables = [TableMapping(**t) for t in data.get("tables", [])]
        return cls(
            spreadsheet_id=data.get("spreadsheet_id", ""),
            csv_dir=data.get("csv_dir", DEFAULT_CSV_DIRNAME),
            tables=tables,
            credentials_file=data.get("credentials_file", DEFAULT_CREDENTIALS_FILENAME),
            token_file=data.get("token_file", DEFAULT_TOKEN_FILENAME),
            state_file=data.get("state_file", DEFAULT_STATE_FILENAME),
        )

    def save(self, project_root: Path) -> Path:
        path = self.config_path(project_root)
        payload = asdict(self)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return path

    def csv_dir_path(self, project_root: Path) -> Path:
        p = Path(self.csv_dir)
        return p if p.is_absolute() else project_root / p

    def table_by_tab(self, tab: str) -> TableMapping | None:
        for t in self.tables:
            if t.tab == tab:
                return t
        return None

    def table_by_csv(self, csv: str) -> TableMapping | None:
        for t in self.tables:
            if t.csv == csv:
                return t
        return None


def resolve_project_root(explicit: str | None = None) -> Path:
    if explicit:
        return find_project_root(Path(explicit))
    return find_project_root(Path.cwd())
