"""Name normalization, key inference, tab mapping. Pure functions.

Ported from v1 `mapping.py`, plus `escape_tab` for safe A1 ranges
(v1 built `'{tab}'` without escaping `'` in tab names).
"""

from __future__ import annotations

import re
from collections.abc import Sequence


def normalize_name(value: str) -> str:
    """Lowercase, drop spaces/underscores/dashes. 'Plant Data' == 'plantdata'."""
    return re.sub(r"[\s_\-]+", "", value.strip().lower())


def escape_tab(tab: str) -> str:
    """Quote a tab for A1 notation. Doubles embedded single quotes."""
    return "'" + tab.replace("'", "''") + "'"


def _is_id_like(header: str) -> bool:
    n = normalize_name(header)
    return n == "id" or n == "key" or n.endswith(("id", "index"))


def _secondary_rank(header: str) -> int:
    if _is_id_like(header):
        return 0
    n = normalize_name(header)
    for token in ("index", "order", "required", "number"):
        if token in n:
            return 1
    return 2


def _is_unique(headers: list[str], rows: Sequence[dict[str, str]], parts: list[str]) -> bool:
    if any(p not in headers for p in parts):
        return False
    seen = {"\x1f".join(row.get(p, "") for p in parts) for row in rows}
    return len(seen) == len(rows)


def infer_key_column(headers: list[str], rows: Sequence[dict[str, str]] = ()) -> str:
    """Best unique key, else '' (positional matching by row index).

    Tries single id-ish columns, then id-ish x other-column pairs,
    each uniqueness-tested against `rows`. '' is the honest answer
    for key-less pool tables. Without rows, first id-ish column.
    """
    ids = [h for h in headers if _is_id_like(h)]
    pairs: list[str] = []
    for a in ids:
        secondaries = sorted(
            (h for h in headers if h != a),
            key=lambda h: (_secondary_rank(h), headers.index(h)),
        )
        for b in secondaries:
            cand = f"{a},{b}"
            if cand not in pairs:
                pairs.append(cand)
    candidates = ids + pairs
    if not rows:
        return candidates[0] if candidates else (headers[0] if headers else "")
    for cand in candidates:
        if _is_unique(headers, rows, cand.split(",")):
            return cand
    return ""


def auto_map_tabs_to_csvs(
    sheet_tabs: list[str],
    csv_names: list[str],
) -> tuple[list[tuple[str, str]], list[str], list[str]]:
    """Match tabs to CSV basenames by normalized name.

    Returns (matched[(tab, csv_file)], unmatched_tabs, unmatched_csvs).
    """
    csv_by_norm = {normalize_name(c.removesuffix(".csv")): c for c in csv_names}
    matched: list[tuple[str, str]] = []
    unmatched_tabs: list[str] = []
    used_csv: set[str] = set()
    for tab in sheet_tabs:
        csv_file = csv_by_norm.get(normalize_name(tab))
        if csv_file is not None and csv_file not in used_csv:
            matched.append((tab, csv_file))
            used_csv.add(csv_file)
        else:
            unmatched_tabs.append(tab)
    unmatched_csvs = [c for c in csv_names if c not in used_csv]
    return matched, unmatched_tabs, unmatched_csvs


def apply_column_map(headers: list[str], column_map: dict[str, str]) -> list[str]:
    """Translate sheet headers to csv headers. Unmapped pass through."""
    if not column_map:
        return list(headers)
    return [column_map.get(h, h) for h in headers]


def suggest_rename(
    unmatched_tabs: list[str],
    unmatched_csvs: list[str],
) -> list[tuple[str, str]]:
    """Hint `did you mean?` pairs for the rename guard (normalized match across lists)."""
    csv_by_norm = {normalize_name(c.removesuffix(".csv")): c for c in unmatched_csvs}
    out: list[tuple[str, str]] = []
    for tab in unmatched_tabs:
        csv_file = csv_by_norm.get(normalize_name(tab))
        if csv_file is not None:
            out.append((tab, csv_file))
    return out
