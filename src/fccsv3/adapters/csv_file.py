"""Read the existing Craftland CSV wire layout without changing files."""

from __future__ import annotations

import csv
import os
import tempfile
from pathlib import Path

from fccsv3.domain.model import TableSnapshot


def read_csv_table(path: Path, *, name: str | None = None) -> TableSnapshot:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        raw = list(csv.reader(stream))
    if not raw:
        return TableSnapshot(name or path.stem, (), (), source="csv")
    headers = tuple(raw[0])
    types = tuple(raw[1]) if len(raw) > 1 else ()
    data = list(enumerate(raw[2:], start=3))
    while data and not any(cell.strip() for cell in data[-1][1]):
        data.pop()
    rows: list[tuple[str, ...]] = []
    widths: list[int] = []
    lines: list[int] = []
    for line, record in data:
        widths.append(len(record))
        lines.append(line)
        rows.append(tuple((record + [""] * len(headers))[: len(headers)]))
    return TableSnapshot(
        name=name or path.stem,
        headers=headers,
        types=types,
        rows=tuple(rows),
        source="csv",
        raw_widths=tuple(widths),
        line_numbers=tuple(lines),
    )


def write_csv_table(path: Path, snapshot: TableSnapshot) -> None:
    """Replace a complete CSV only after its new content is safely written."""

    if len(snapshot.types) != len(snapshot.headers):
        raise ValueError("CSV type row must match the header width")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", suffix=".tmp",
            prefix=f".{path.name}.", dir=path.parent, delete=False,
        ) as stream:
            temporary = Path(stream.name)
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow(snapshot.headers)
            writer.writerow(snapshot.types)
            writer.writerows(snapshot.rows)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
