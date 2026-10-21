"""Select row identity only after both snapshots pass shape validation."""

from __future__ import annotations

from itertools import combinations

from fccsv3.domain.model import Diagnostic, Identity, TableSnapshot

MAX_IDENTITY_COLUMNS = 12


def _identity_rank(header: str) -> int | None:
    if header.casefold() == "id":
        return 0
    if header.endswith(("Id", "ID", "_id")):
        return 1
    if header.endswith(("Key", "KEY", "_key")):
        return 2
    if header.endswith(("Index", "INDEX", "_index")):
        return 3
    return None


def row_key(snapshot: TableSnapshot, row_index: int, columns: tuple[str, ...]) -> tuple[str, ...]:
    positions = tuple(snapshot.headers.index(column) for column in columns)
    row = snapshot.rows[row_index]
    return tuple(row[position] for position in positions)


def _unique_keys(
    snapshot: TableSnapshot, columns: tuple[str, ...]
) -> set[tuple[str, ...]] | None:
    if any(column not in snapshot.headers for column in columns):
        return None
    positions = tuple(snapshot.headers.index(column) for column in columns)
    keys: set[tuple[str, ...]] = set()
    for row in snapshot.rows:
        key = tuple(row[position] for position in positions)
        if not any(part.strip() for part in key) or key in keys:
            return None
        keys.add(key)
    return keys


def _candidate_keys(
    local: TableSnapshot,
    remote: TableSnapshot,
    base: TableSnapshot | None,
    columns: tuple[str, ...],
) -> tuple[set[tuple[str, ...]], set[tuple[str, ...]]] | None:
    local_keys = _unique_keys(local, columns)
    remote_keys = _unique_keys(remote, columns)
    if local_keys is None or remote_keys is None:
        return None
    if base is not None and all(column in base.headers for column in columns):
        if _unique_keys(base, columns) is None:
            return None
    return local_keys, remote_keys


def choose_identity(
    local: TableSnapshot,
    remote: TableSnapshot,
    *,
    base: TableSnapshot | None = None,
    preferred: tuple[str, ...] = (),
) -> tuple[Identity, tuple[Diagnostic, ...]]:
    """Prefer a valid hint, then short ID-like keys, then safe positional diff."""

    diagnostics: list[Diagnostic] = []
    if preferred:
        if _candidate_keys(local, remote, base, preferred) is not None:
            return Identity("columns", preferred, "verified configured key"), ()
        diagnostics.append(
            Diagnostic(
                "STALE_KEY_HINT",
                f"configured key {', '.join(preferred)} is not unique on both sides",
                "warning",
            )
        )

    common = [header for header in local.headers if header in remote.headers]
    ranked = sorted(
        ((rank, index, header) for index, header in enumerate(common)
         if (rank := _identity_rank(header)) is not None)
    )
    if len(ranked) > MAX_IDENTITY_COLUMNS:
        diagnostics.append(
            Diagnostic(
                "IDENTITY_SEARCH_LIMIT",
                f"checking the top {MAX_IDENTITY_COLUMNS} of {len(ranked)} ID-like columns",
                "info",
            )
        )
    candidates = [header for _, _, header in ranked[:MAX_IDENTITY_COLUMNS]]
    rank_of = {header: rank for rank, _, header in ranked}
    order_of = {header: index for index, header in enumerate(local.headers)}

    for width in range(1, min(3, len(candidates)) + 1):
        viable: list[tuple[tuple[int, int, tuple[int, ...]], tuple[str, ...]]] = []
        for combination in combinations(candidates, width):
            columns = tuple(sorted(combination, key=order_of.__getitem__))
            keys = _candidate_keys(local, remote, base, columns)
            if keys is None:
                continue
            overlap = len(keys[0] & keys[1])
            score = (
                -overlap,
                sum(rank_of[column] for column in columns),
                tuple(order_of[column] for column in columns),
            )
            viable.append((score, columns))
        if viable:
            _, columns = min(viable)
            return (
                Identity("columns", columns, "unique ID-like columns on both sides"),
                tuple(diagnostics),
            )

    diagnostics.append(
        Diagnostic(
            "POSITIONAL_IDENTITY",
            "no stable unique ID-like columns; compare complete rows by position",
            "info",
        )
    )
    return Identity("position", reason="no verified column identity"), tuple(diagnostics)
