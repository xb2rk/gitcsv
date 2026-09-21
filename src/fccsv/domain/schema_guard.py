"""Schema guard: column add/remove/reorder needs explicit approval.

Rationale: FC loaders use generated `XxxCSVIndex` positional enums plus a
`Length` guard and skip malformed rows. A silent schema change drops rows
in-game, so data sync must block on any header change by default.
"""

from __future__ import annotations

from fccsv.domain.diff_merge import diff_schema
from fccsv.domain.exceptions import SchemaChangeBlocked
from fccsv.domain.models import TableSnapshot


def ensure_schema_compatible(
    local: TableSnapshot,
    remote: TableSnapshot,
    *,
    allow_schema_change: bool = False,
) -> None:
    diff = diff_schema(local, remote)
    if not diff.has_change:
        return
    if allow_schema_change:
        return
    parts: list[str] = []
    if diff.added:
        parts.append(f"added={diff.added}")
    if diff.removed:
        parts.append(f"removed={diff.removed}")
    if diff.reordered:
        parts.append("reordered")
    raise SchemaChangeBlocked(local.name, ", ".join(parts) + ". Re-run with --allow-schema-change.")


def validate_row_width(snapshot: TableSnapshot) -> list[int]:
    """Return 0-based data-row indexes whose width differs from the header."""
    bad: list[int] = []
    width = len(snapshot.headers)
    for i, row in enumerate(snapshot.rows):
        if len(row) != width:
            bad.append(i)
    return bad
