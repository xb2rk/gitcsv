"""Read v2 BASE snapshots as shadow-scan context without modifying them."""

from __future__ import annotations

import json
from pathlib import Path

from fccsv3.domain.model import TableSnapshot


def legacy_base_path(root: Path, tab: str) -> Path:
    safe_name = "".join(char if char.isalnum() or char in "-_. " else "_" for char in tab)
    return root / ".fccsv-base" / f"{safe_name}.json"


def load_legacy_base(root: Path, tab: str) -> TableSnapshot | None:
    path = legacy_base_path(root, tab)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        headers = tuple(data["headers"])
        types = tuple(data.get("types", ()))
        rows = tuple(tuple(str(row.get(header, "")) for header in headers) for row in data["rows"])
        return TableSnapshot(tab, headers, rows, types=types, source="base")
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None
