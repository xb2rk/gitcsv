"""BASE snapshots: last-sync content per tab for 3-way merge.

Stored as `<base_dir>/<tab>.json`. Missing file means no BASE (fresh v1
state or first sync) and callers fall back to hash comparison.
"""

from __future__ import annotations

import json
from pathlib import Path

from fccsv.domain.snapshot import TableSnapshot


def _safe_filename(tab: str) -> str:
    return "".join(c if c.isalnum() or c in ("-", "_", ".", " ") else "_" for c in tab)


def base_path(base_dir: Path, tab: str) -> Path:
    return base_dir / (_safe_filename(tab) + ".json")


def load_base(base_dir: Path, tab: str) -> TableSnapshot | None:
    path = base_path(base_dir, tab)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or "headers" not in data:
        return None
    rows = data.get("rows", [])
    return TableSnapshot(
        name=tab,
        headers=list(data.get("headers", [])),
        types=list(data.get("types", [])),
        rows=[dict(r) for r in rows] if isinstance(rows, list) else [],
        key_column=str(data.get("key_column", "")),
    )


def save_base(base_dir: Path, snapshot: TableSnapshot) -> None:
    base_dir.mkdir(parents=True, exist_ok=True)
    path = base_path(base_dir, snapshot.name)
    payload = {
        "headers": snapshot.headers,
        "types": snapshot.types,
        "rows": snapshot.rows,
        "key_column": snapshot.key_column,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def clear_base(base_dir: Path, tab: str) -> None:
    try:
        base_path(base_dir, tab).unlink()
    except FileNotFoundError:
        pass


def clear_all_base(base_dir: Path) -> None:
    if not base_dir.is_dir():
        return
    for path in base_dir.glob("*.json"):
        try:
            path.unlink()
        except OSError:
            pass
