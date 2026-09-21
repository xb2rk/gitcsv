"""Local data validation. Pure checks over TableSnapshot; no I/O.

Catches corruption before it reaches the sheet or the game: ragged rows,
duplicate headers, duplicate keys, and blank keys. FC loaders silently skip
malformed rows, so a bad CSV looks like missing content in-game.
"""

from __future__ import annotations

from collections import Counter

from fccsv.domain.exceptions import DataValidationError
from fccsv.domain.models import TableSnapshot


def _display_key(key: str) -> str:
    return key.replace("\x1f", "|")


def find_duplicate_keys(snapshot: TableSnapshot) -> list[str]:
    """Keys appearing more than once, in first-seen order."""
    if not snapshot.key_column:
        return []
    counts = Counter(snapshot.key_of(row) for row in snapshot.rows)
    seen: list[str] = []
    for row in snapshot.rows:
        key = snapshot.key_of(row)
        if counts[key] > 1 and key not in seen:
            seen.append(key)
    return [_display_key(k) for k in seen]


def validate_snapshot(snapshot: TableSnapshot) -> list[str]:
    """Return human-readable issues; empty means valid."""
    issues: list[str] = []
    if not snapshot.headers:
        return ["no header row"]
    if len(set(snapshot.headers)) != len(snapshot.headers):
        dupes = sorted({h for h in snapshot.headers if snapshot.headers.count(h) > 1})
        issues.append(f"duplicate headers: {dupe_str(dupes)}")
    width = len(snapshot.headers)
    ragged = [i + 3 for i, row in enumerate(snapshot.rows) if len(row) != width]
    if ragged:
        issues.append(f"ragged rows (CSV lines): {ragged[:10]}")
    if snapshot.key_column:
        dup_keys = find_duplicate_keys(snapshot)
        if dup_keys:
            issues.append(f"duplicate keys in '{snapshot.key_column}': {dupe_str(dup_keys[:10])}")
        blank = sum(1 for row in snapshot.rows if not snapshot.key_of(row))
        if blank:
            issues.append(f"{blank} row(s) with blank key '{snapshot.key_column}'")
    return issues


def dupe_str(values: list[str]) -> str:
    return ", ".join(repr(v) for v in values)


def ensure_valid(snapshot: TableSnapshot) -> None:
    issues = validate_snapshot(snapshot)
    if issues:
        raise DataValidationError(f"[{snapshot.name}] " + "; ".join(issues))
