"""Convert Sheet value arrays into immutable snapshots for the planner."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from fccsv3.domain.model import TableSnapshot


def snapshot_from_sheet_values(
    name: str,
    values: Sequence[Sequence[str]],
    *,
    column_map: Mapping[str, str] | None = None,
) -> TableSnapshot:
    if not values:
        return TableSnapshot(name, (), (), source="sheet")
    renames = column_map or {}
    headers = tuple(renames.get(str(value), str(value)) for value in values[0])
    rows: list[tuple[str, ...]] = []
    widths: list[int] = []
    for record in values[1:]:
        row = [str(value) for value in record]
        widths.append(len(row))
        rows.append(tuple((row + [""] * len(headers))[: len(headers)]))
    return TableSnapshot(
        name=name,
        headers=headers,
        rows=tuple(rows),
        source="sheet",
        raw_widths=tuple(widths),
    )


def values_for_sheet(
    snapshot: TableSnapshot, *, column_map: Mapping[str, str] | None = None
) -> list[list[str]]:
    """Reverse the configured Sheet-to-CSV header mapping before a write."""

    renames = column_map or {}
    inverse = {local: sheet for sheet, local in renames.items()}
    if len(inverse) != len(renames):
        raise ValueError("column_map cannot map two Sheet columns to one CSV column")
    headers = [inverse.get(header, header) for header in snapshot.headers]
    if len(set(headers)) != len(headers):
        raise ValueError("column_map creates duplicate Sheet headers")
    return [headers, *[list(row) for row in snapshot.rows]]
