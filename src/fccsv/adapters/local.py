"""Local CSV I/O. Header row 0, types row 1, data from row 2.

Records `raw_widths` + `line_no` per data row so validation can flag
ragged rows even though short rows are padded for merge safety.
Encoding: UTF-8-SIG read, UTF-8 LF write.
"""

from __future__ import annotations

import csv
from pathlib import Path

from fccsv.core.constants import FIRST_DATA_ROW_INDEX
from fccsv.domain import keys as keys_mod
from fccsv.domain.snapshot import TableSnapshot


def list_csv_files(csv_dir: Path) -> list[str]:
    if not csv_dir.is_dir():
        return []
    return sorted(p.name for p in csv_dir.glob("*.csv") if p.is_file())


def read_csv_table(csv_path: Path, *, key_column: str = "") -> TableSnapshot:
    """Read header+types+rows. Drops trailing all-empty rows, skips inner
    all-empty rows, pads short rows, truncates long rows (raw width kept
    for ragged detection)."""
    with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
        raw = list(csv.reader(fh))
    if not raw:
        return TableSnapshot(
            name=csv_path.stem,
            headers=[],
            types=[],
            rows=[],
            key_column=key_column,
            raw_widths=[],
            line_no=[],
        )
    headers = [h.strip() for h in raw[0]]
    types = [t.strip() for t in raw[1]] if len(raw) > 1 else []
    # Drop trailing all-empty rows (spreadsheet export padding).
    data = raw[FIRST_DATA_ROW_INDEX:]
    while data and all(not (cell or "").strip() for cell in data[-1]):
        data.pop()
    rows: list[dict[str, str]] = []
    widths: list[int] = []
    lines: list[int] = []
    for i, record in enumerate(data):
        if all(not (cell or "").strip() for cell in record):
            continue
        widths.append(len(record))
        lines.append(FIRST_DATA_ROW_INDEX + i + 1)  # 1-based CSV line
        padded = list(record) + [""] * (len(headers) - len(record))
        rows.append({h: (padded[j] if j < len(padded) else "") for j, h in enumerate(headers)})
    key = key_column or keys_mod.infer_key_column(headers, rows)
    return TableSnapshot(
        name=csv_path.stem,
        headers=headers,
        types=types,
        rows=rows,
        key_column=key,
        raw_widths=widths,
        line_no=lines,
    )


def write_csv_table(csv_path: Path, snapshot: TableSnapshot) -> None:
    """Write header + types (or blank placeholders) + rows. Atomic."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    types = list(snapshot.types)
    if len(types) < len(snapshot.headers):
        types = types + [""] * (len(snapshot.headers) - len(types))
    tmp = csv_path.with_suffix(csv_path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
        writer.writerow(snapshot.headers)
        writer.writerow(types[: len(snapshot.headers)])
        for row in snapshot.rows:
            writer.writerow([row.get(h, "") for h in snapshot.headers])
    tmp.replace(csv_path)
