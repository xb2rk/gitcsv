"""Typed project config. One spreadsheet today; `sources` list keeps multi-source open."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class TableMapping:
    tab: str
    csv: str
    key_column: str = ""
    column_map: dict[str, str] = field(default_factory=dict)
    skip_type_row_in_sheet: bool = True


@dataclass
class SourceConfig:
    spreadsheet_id: str = ""
    tables: list[TableMapping] = field(default_factory=list)


@dataclass
class FccsvConfig:
    """Root config. `sources[0]` is active; more entries reserved for later."""

    sources: list[SourceConfig] = field(default_factory=list)
    csv_dir: str = "Assets/CSV"
    credentials_file: str = "credentials.json"
    token_file: str = "token.json"
    state_file: str = ".fccsv-state.json"

    @property
    def active(self) -> SourceConfig:
        if not self.sources:
            self.sources.append(SourceConfig())
        return self.sources[0]

    def table_by_tab(self, tab: str) -> TableMapping | None:
        for table in self.active.tables:
            if table.tab == tab:
                return table
        return None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FccsvConfig:
        sources: list[SourceConfig] = []
        raw_sources = data.get("sources")
        if raw_sources:
            for entry in raw_sources:
                tables = [TableMapping(**t) for t in entry.get("tables", [])]
                sources.append(
                    SourceConfig(spreadsheet_id=entry.get("spreadsheet_id", ""), tables=tables)
                )
        else:
            # v1 shape: top-level spreadsheet_id + tables.
            tables = [TableMapping(**t) for t in data.get("tables", [])]
            sources.append(
                SourceConfig(spreadsheet_id=data.get("spreadsheet_id", ""), tables=tables)
            )
        return cls(
            sources=sources,
            csv_dir=data.get("csv_dir", "Assets/CSV"),
            credentials_file=data.get("credentials_file", "credentials.json"),
            token_file=data.get("token_file", "token.json"),
            state_file=data.get("state_file", ".fccsv-state.json"),
        )

    def validate(self) -> list[str]:
        issues: list[str] = []
        tabs = [t.tab for t in self.active.tables]
        csvs = [t.csv for t in self.active.tables]
        if len(set(tabs)) != len(tabs):
            dupes = sorted({t for t in tabs if tabs.count(t) > 1})
            issues.append(f"duplicate tabs: {dupes}")
        if len(set(csvs)) != len(csvs):
            dupes = sorted({c for c in csvs if csvs.count(c) > 1})
            issues.append(f"duplicate csvs: {dupes}")
        for table in self.active.tables:
            if not table.tab.strip():
                issues.append("blank tab name")
            if not table.csv.strip():
                issues.append(f"[{table.tab}] blank csv")
        return issues
