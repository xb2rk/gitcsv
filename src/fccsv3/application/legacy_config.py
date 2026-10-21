"""Read v2 project mapping as an import source; never rewrite it."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from fccsv3.application.scan import TableMapping


@dataclass(frozen=True, slots=True)
class LegacyProject:
    csv_dir: Path
    spreadsheet_id: str
    mappings: tuple[TableMapping, ...]
    token_file: Path
    credentials_file: Path
    legacy_state_file: Path


def load_legacy_project(root: Path) -> LegacyProject:
    path = root / ".fccsv.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a JSON object")
    sources = raw.get("sources")
    if sources is None:
        source = raw
    elif isinstance(sources, list) and sources and isinstance(sources[0], dict):
        source = sources[0]
    else:
        raise ValueError(f"{path}: no active source")
    tables = source.get("tables", [])
    if not isinstance(tables, list):
        raise ValueError(f"{path}: active source tables must be a list")
    mappings: list[TableMapping] = []
    for index, entry in enumerate(tables):
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: table {index} must be an object")
        tab, csv_file = entry.get("tab"), entry.get("csv")
        if not isinstance(tab, str) or not isinstance(csv_file, str):
            raise ValueError(f"{path}: table {index} needs tab and csv strings")
        key_text = entry.get("key_column", "")
        column_map = entry.get("column_map", {})
        if not isinstance(key_text, str) or not isinstance(column_map, dict):
            raise ValueError(f"{path}: invalid mapping for {tab}")
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in column_map.items()):
            raise ValueError(f"{path}: invalid column map for {tab}")
        mappings.append(
            TableMapping(
                tab=tab,
                csv_file=csv_file,
                preferred_key=tuple(part.strip() for part in key_text.split(",") if part.strip()),
                column_map=tuple(column_map.items()),
            )
        )
    csv_dir = raw.get("csv_dir", "Assets/CSV")
    spreadsheet_id = source.get("spreadsheet_id", "")
    if not isinstance(csv_dir, str) or not isinstance(spreadsheet_id, str):
        raise ValueError(f"{path}: invalid csv_dir or spreadsheet_id")
    token_file = raw.get("token_file", "token.json")
    credentials_file = raw.get("credentials_file", "credentials.json")
    state_file = raw.get("state_file", ".fccsv-state.json")
    if not all(isinstance(value, str) for value in (token_file, credentials_file, state_file)):
        raise ValueError(f"{path}: invalid token_file, credentials_file, or state_file")
    csv_root = (root / csv_dir).resolve()
    seen_tabs: set[str] = set()
    seen_files: dict[Path, str] = {}
    for mapping in mappings:
        if mapping.tab in seen_tabs:
            raise ValueError(f"{path}: duplicate tab mapping {mapping.tab!r}")
        seen_tabs.add(mapping.tab)
        csv_path = (csv_root / mapping.csv_file).resolve()
        if not csv_path.is_relative_to(csv_root):
            raise ValueError(f"{path}: CSV path escapes csv_dir: {mapping.csv_file!r}")
        if csv_path in seen_files:
            raise ValueError(
                f"{path}: {seen_files[csv_path]!r} and {mapping.tab!r} "
                f"both map to {mapping.csv_file!r}"
            )
        seen_files[csv_path] = mapping.tab
    return LegacyProject(
        root / csv_dir, spreadsheet_id, tuple(mappings),
        root / token_file, root / credentials_file, root / state_file,
    )
