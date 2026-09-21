"""First-time setup: auto-map sheet tabs to CSV files and build config."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fccsv.adapters import local as local_mod
from fccsv.adapters.sheets import SheetsGateway, spreadsheet_id_from_url
from fccsv.core.config import FccsvConfig, SourceConfig, TableMapping
from fccsv.domain.keys import auto_map_tabs_to_csvs


def build_config_from_tabs(
    sheet_tabs: list[str],
    csv_dir: Path,
    *,
    spreadsheet_id: str,
    skip_type_row_in_sheet: bool = True,
) -> FccsvConfig:
    csv_names = local_mod.list_csv_files(csv_dir)
    matched, _unmatched_tabs, unmatched_csvs = auto_map_tabs_to_csvs(sheet_tabs, csv_names)
    tables: list[TableMapping] = []
    for tab, csv_file in matched:
        key = _infer_local_key(csv_dir / csv_file)
        tables.append(
            TableMapping(
                tab=tab,
                csv=csv_file,
                key_column=key,
                skip_type_row_in_sheet=skip_type_row_in_sheet,
            )
        )
    for csv_file in unmatched_csvs:
        # Unmapped CSVs get a same-name tab so `push` can create it.
        key = _infer_local_key(csv_dir / csv_file)
        tables.append(
            TableMapping(
                tab=Path(csv_file).stem,
                csv=csv_file,
                key_column=key,
                skip_type_row_in_sheet=skip_type_row_in_sheet,
            )
        )
    tables.sort(key=lambda t: t.tab)
    return FccsvConfig(sources=[SourceConfig(spreadsheet_id=spreadsheet_id, tables=tables)])


def _infer_local_key(csv_path: Path) -> str:
    if not csv_path.exists():
        return ""
    try:
        return local_mod.read_csv_table(csv_path).key_column
    except Exception:
        return ""


def init_from_url_or_id(
    url_or_id: str,
    gateway_factory: SheetsGateway | None,
    *,
    csv_dir: Path,
    list_tabs_fn: Callable[[], list[str]] | None = None,
) -> FccsvConfig:
    """Build config from a sheet URL/ID. `list_tabs_fn` injects the tab
    listing for tests; otherwise uses the gateway."""
    spreadsheet_id = spreadsheet_id_from_url(url_or_id)
    if list_tabs_fn is not None:
        tabs = list_tabs_fn()
    elif gateway_factory is not None:
        tabs = gateway_factory.list_tabs()
    else:
        tabs = []
    return build_config_from_tabs(tabs, csv_dir, spreadsheet_id=spreadsheet_id)
