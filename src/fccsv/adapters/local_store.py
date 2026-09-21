"""Local CSV files <-> TableSnapshot. CSV dialect fixed: UTF-8, LF, comma."""

from __future__ import annotations

import csv
from pathlib import Path

from fccsv.core.constants import FIRST_DATA_ROW_INDEX
from fccsv.domain import mapping as mapping_mod
from fccsv.domain.models import TableSnapshot


def list_csv_files(csv_dir: Path) -> list[str]:
    if not csv_dir.is_dir():
        return []
    return sorted(p.name for p in csv_dir.glob("*.csv") if p.is_file())


def read_csv_table(csv_path: Path) -> TableSnapshot:
    """Row 1 = header, row 2 = types, data from row 3. All values kept as str."""
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        rows = list(reader)
    # Drop fully-empty trailing rows but keep positional shape otherwise.
    while rows and all(cell == "" for cell in rows[-1]):
        rows.pop()
    if len(rows) < 2:
        raise ValueError(f"{csv_path.name}: expected header + types rows, got {len(rows)}")
    headers = [c.strip() for c in rows[0]]
    types = [c.strip() for c in rows[1]]
    data_rows: list[dict[str, str]] = []
    for raw in rows[FIRST_DATA_ROW_INDEX:]:
        if all(cell == "" for cell in raw):
            continue
        padded = list(raw) + [""] * (len(headers) - len(raw))
        data_rows.append({h: padded[i] if i < len(padded) else "" for i, h in enumerate(headers)})
    key_column = mapping_mod.infer_key_column(headers, data_rows)
    return TableSnapshot(
        name=csv_path.stem,
        headers=headers,
        types=types,
        rows=data_rows,
        key_column=key_column,
    )


def write_csv_table(csv_path: Path, snapshot: TableSnapshot) -> None:
    """Write header + types + data rows. LF endings, quoted minimally."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
        writer.writerow(snapshot.headers)
        writer.writerow(snapshot.types if snapshot.types else [""] * len(snapshot.headers))
        for row in snapshot.rows:
            writer.writerow([row.get(h, "") for h in snapshot.headers])
