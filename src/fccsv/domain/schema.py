"""Schema guard: column add/remove/reorder needs explicit approval.

FC loaders use generated `XxxCSVIndex` positional enums plus a `Length`
guard and skip malformed rows. A silent schema change drops rows in-game,
so data sync blocks on any header change by default.
"""

from __future__ import annotations

from fccsv.domain.diff import diff_schema
from fccsv.domain.errors import SchemaChangeBlocked
from fccsv.domain.snapshot import SchemaDiff, TableSnapshot


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
    raise SchemaChangeBlocked(local.name, schema_diff_text(local.name, diff))


def schema_diff_text(name: str, diff: SchemaDiff) -> str:
    parts: list[str] = []
    if diff.added:
        parts.append(f"added={diff.added}")
    if diff.removed:
        parts.append(f"removed={diff.removed}")
    if diff.reordered:
        parts.append("reordered")
    return (
        ", ".join(parts)
        + f" on [{name}]. Update .fcc + fccompile first, then re-run with --allow-schema-change."
    )
