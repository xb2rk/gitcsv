"""Determine stable row identity across local, remote, and base snapshots."""

from __future__ import annotations

from gitcsv.core.models import Diagnostic, Identity, TableSnapshot


def _unique_columns(snapshot: TableSnapshot, columns: tuple[str, ...]) -> bool:
    """Return True if the specified column tuple uniquely identifies all rows in snapshot."""
    indices = tuple(snapshot.headers.index(c) for c in columns)
    seen: set[tuple[str, ...]] = set()

    for row in snapshot.rows:
        key = tuple(row[idx] for idx in indices)
        if key in seen:
            return False
        seen.add(key)

    return True


def choose_identity(
    local: TableSnapshot,
    remote: TableSnapshot,
    *,
    base: TableSnapshot | None = None,
    preferred: tuple[str, ...] = (),
) -> tuple[Identity, tuple[Diagnostic, ...]]:
    """Determine a unique identity column (or fallback to position) verified across snapshots."""
    diagnostics: list[Diagnostic] = []

    # 1. Check common columns present in all available snapshots
    snapshots = [local, remote] if base is None else [local, remote, base]
    common_headers = set(local.headers).intersection(remote.headers)
    if base is not None:
        common_headers.intersection_update(base.headers)

    # 2. Check user's preferred key if defined
    if preferred and set(preferred).issubset(common_headers):
        if all(_unique_columns(s, preferred) for s in snapshots):
            return Identity(mode="columns", columns=preferred), ()
        diagnostics.append(
            Diagnostic(
                "PREFERRED_KEY_NON_UNIQUE",
                f"Configured preferred key {preferred} is not unique in all snapshots",
                "warning",
            )
        )

    # 3. Look for single unique key column (e.g. Id, Name, Code, etc.)
    priority_names = ("id", "key", "code", "guid", "name")
    sorted_common = sorted(
        common_headers,
        key=lambda name: (
            0 if name.lower() in priority_names else 1,
            local.headers.index(name),
        ),
    )

    for col in sorted_common:
        candidate = (col,)
        if all(_unique_columns(s, candidate) for s in snapshots):
            return Identity(mode="columns", columns=candidate), tuple(diagnostics)

    # 4. Look for composite pair key (first 20 common columns to avoid combinatorial explosion)
    limited_candidates = sorted_common[:20]
    for i, col1 in enumerate(limited_candidates):
        for col2 in limited_candidates[i + 1 :]:
            pair = (col1, col2)
            if all(_unique_columns(s, pair) for s in snapshots):
                return Identity(mode="columns", columns=pair), tuple(diagnostics)

    # 5. Fallback to row position matching
    diagnostics.append(
        Diagnostic(
            "POSITIONAL_IDENTITY",
            "No unique row identity found; comparing rows by line position",
            "warning",
        )
    )
    return Identity(mode="position"), tuple(diagnostics)
