"""Name normalization + auto-mapping. Pure functions, easy to test."""

from __future__ import annotations

import re


def normalize_name(value: str) -> str:
    """Lowercase, drop spaces/underscores/dashes. 'Plant Data' == 'plantdata'."""
    return re.sub(r"[\s_\-]+", "", value.strip().lower())


def infer_key_column(headers: list[str], rows: list[dict[str, str]] = ()) -> str:
    """Best unique key, else '' (positional matching by row index).

    Tries single id-ish columns, then id-ish x other-column pairs
    (sequence-like partners like SortOrder/SlotIndex preferred), each
    uniqueness-tested against `rows`. Single-column keys fail on real
    tables (ShopSlotData repeats ShopId; MissionReward repeats Key), and
    some tables have no unique key at all (ShopSlotData is a weighted
    pool with repeat entries) — '' is the honest answer there.
    Without rows it falls back to the first id-ish column for display use.
    """
    ids = [h for h in headers if _is_id_like(h)]
    pairs: list[str] = []
    for a in ids:
        secondaries = sorted(
            (h for h in headers if h != a), key=lambda h: (_secondary_rank(h), headers.index(h))
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


def _secondary_rank(header: str) -> int:
    """Sequence-like columns make stabler key partners than free text."""
    if _is_id_like(header):
        return 0
    n = normalize_name(header)
    for token in ("index", "order", "required", "number"):
        if token in n:
            return 1
    return 2


def _is_id_like(header: str) -> bool:
    n = normalize_name(header)
    return n == "id" or n == "key" or n.endswith(("id", "index"))


def _is_unique(headers: list[str], rows: list[dict], parts: list[str]) -> bool:
    if any(p not in headers for p in parts):
        return False
    seen = {"\x1f".join(row.get(p, "") for p in parts) for row in rows}
    return len(seen) == len(rows)


def auto_map_tabs_to_csvs(
    sheet_tabs: list[str],
    csv_names: list[str],
) -> tuple[list[tuple[str, str]], list[str], list[str]]:
    """Match tabs to CSV basenames (without .csv) by normalized name.

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
    """Translate sheet headers to csv headers. Unmapped headers pass through."""
    if not column_map:
        return list(headers)
    return [column_map.get(h, h) for h in headers]
