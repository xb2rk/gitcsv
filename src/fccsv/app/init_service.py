"""Init wizard: link existing sheet or record a new id, then auto-map."""

from __future__ import annotations

from pathlib import Path

from fccsv.adapters.local_store import list_csv_files
from fccsv.adapters.sheets_gateway import spreadsheet_id_from_url
from fccsv.core.config import FccsvConfig, TableMapping
from fccsv.domain import mapping as mapping_mod


def build_config_from_tabs(
    sheet_tabs: list[str],
    csv_files: list[str],
    spreadsheet_id: str,
    csv_dir: Path | None = None,
) -> FccsvConfig:
    matched, _, _ = mapping_mod.auto_map_tabs_to_csvs(sheet_tabs, csv_files)
    tables = [
        TableMapping(tab=tab, csv=csv, key_column=_infer_local_key(csv_dir, csv))
        for tab, csv in matched
    ]
    # CSVs with no tab yet still get a mapping so `push` can create the tab.
    have_csv = {csv for _, csv in matched}
    for csv in csv_files:
        if csv not in have_csv:
            tab = csv.removesuffix(".csv")
            tables.append(TableMapping(tab=tab, csv=csv, key_column=_infer_local_key(csv_dir, csv)))
    return FccsvConfig(spreadsheet_id=spreadsheet_id, tables=tables)


def _infer_local_key(csv_dir: Path | None, csv_file: str) -> str:
    """Uniqueness-tested key from the local file; '' when unavailable."""
    if csv_dir is None:
        return ""
    path = csv_dir / csv_file
    if not path.is_file():
        return ""
    try:
        from fccsv.adapters.local_store import read_csv_table

        return read_csv_table(path).key_column
    except (ValueError, OSError):
        return ""


def init_from_url_or_id(
    url_or_id: str,
    sheet_tabs: list[str],
    csv_dir: Path,
) -> FccsvConfig:
    spreadsheet_id = spreadsheet_id_from_url(url_or_id)
    csv_files = list_csv_files(csv_dir)
    return build_config_from_tabs(sheet_tabs, csv_files, spreadsheet_id, csv_dir)
