"""CSV storage adapter supporting header, type row, and data rows with UTF-8 BOM."""

from __future__ import annotations

import csv
from pathlib import Path

from gitcsv.core.models import TableSnapshot


def read_csv_table(path: Path, *, name: str | None = None) -> TableSnapshot:
    """Read a game CSV file into an immutable TableSnapshot.

    The first row is headers. The second row is data types (if recognized).
    Subsequent rows are data values.
    """
    table_name = name or path.stem

    # 1. Read file with UTF-8-SIG to strip BOM, handling multiline quoted cells
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        raw_rows = [
            tuple(cell.replace("\r\n", "\n").strip() for cell in row)
            for row in reader
            if any(c.strip() for c in row)
        ]

    if not raw_rows:
        return TableSnapshot(
            name=table_name,
            headers=(),
            types=(),
            rows=(),
            source="csv",
        )

    headers = raw_rows[0]

    # 2. Check if second row represents column data types
    known_types = {"int", "float", "string", "text", "bool", "json", "list", "tuple", "vector3"}
    has_type_row = False
    types: tuple[str, ...] = ()

    if len(raw_rows) > 1:
        candidate_types = raw_rows[1]
        matches = sum(1 for t in candidate_types if t.lower() in known_types)
        # If at least half of the columns match known types, treat as type row
        if len(candidate_types) == len(headers) and matches >= max(1, len(headers) // 2):
            has_type_row = True
            types = tuple(t.lower() for t in candidate_types)

    data_rows = raw_rows[2:] if has_type_row else raw_rows[1:]

    return TableSnapshot(
        name=table_name,
        headers=headers,
        types=types,
        rows=tuple(data_rows),
        source="csv",
    )


def write_csv_table(path: Path, snapshot: TableSnapshot) -> None:
    """Write an immutable TableSnapshot to a CSV file on disk using UTF-8-SIG."""
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")

        # 1. Write header row
        writer.writerow(snapshot.headers)

        # 2. Write type row if present
        if snapshot.types:
            writer.writerow(snapshot.types)

        # 3. Write data rows
        for row in snapshot.rows:
            writer.writerow(row)
