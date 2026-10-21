"""Batch-load local and remote values, then make pure per-table plans."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from fccsv3.adapters.csv_file import read_csv_table
from fccsv3.adapters.sheet_values import snapshot_from_sheet_values
from fccsv3.domain.model import TablePlan, TableSnapshot
from fccsv3.domain.plan import plan_table


@dataclass(frozen=True, slots=True)
class TableMapping:
    tab: str
    csv_file: str
    preferred_key: tuple[str, ...] = ()
    column_map: tuple[tuple[str, str], ...] = ()


class SheetValuesReader(Protocol):
    def read_values(
        self, tabs: tuple[str, ...]
    ) -> Mapping[str, Sequence[Sequence[str]]]: ...


def scan_tables(
    csv_dir: Path,
    mappings: Sequence[TableMapping],
    reader: SheetValuesReader,
    *,
    bases: Mapping[str, TableSnapshot] | None = None,
) -> tuple[TablePlan, ...]:
    """Read the requested tabs once; never mutate CSV, Sheet, or state."""

    remote_values = reader.read_values(tuple(mapping.tab for mapping in mappings))
    plans: list[TablePlan] = []
    for mapping in mappings:
        csv_path = csv_dir / mapping.csv_file
        local = read_csv_table(csv_path, name=mapping.tab) if csv_path.is_file() else None
        values = remote_values.get(mapping.tab)
        remote = (
            snapshot_from_sheet_values(
                mapping.tab, values, column_map=dict(mapping.column_map)
            )
            if values is not None
            else None
        )
        plans.append(
            plan_table(
                local,
                remote,
                name=mapping.tab,
                base=(bases or {}).get(mapping.tab),
                preferred_key=mapping.preferred_key,
            )
        )
    return tuple(plans)
