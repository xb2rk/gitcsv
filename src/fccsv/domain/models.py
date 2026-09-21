"""Shared value objects. Pure data, no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TableSnapshot:
    """In-memory table. Rows are ordered dicts keyed by csv header."""

    name: str
    headers: list[str] = field(default_factory=list)
    types: list[str] = field(default_factory=list)
    rows: list[dict[str, str]] = field(default_factory=list)
    key_column: str = ""

    def key_parts(self) -> list[str]:
        """Key columns. Comma-separated composites like 'ShopId,SlotIndex'."""
        return [p.strip() for p in self.key_column.split(",") if p.strip()]

    def key_of(self, row: dict[str, str]) -> str:
        parts = self.key_parts()
        if not parts or any(p not in row for p in parts):
            return ""
        return "\x1f".join(row[p] for p in parts)


@dataclass
class SchemaDiff:
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    reordered: bool = False

    @property
    def has_change(self) -> bool:
        return bool(self.added or self.removed or self.reordered)


@dataclass
class RowDiff:
    added_keys: list[str] = field(default_factory=list)
    removed_keys: list[str] = field(default_factory=list)
    changed_keys: list[str] = field(default_factory=list)

    @property
    def has_change(self) -> bool:
        return bool(self.added_keys or self.removed_keys or self.changed_keys)


@dataclass
class TableStatus:
    name: str
    schema_diff: SchemaDiff = field(default_factory=SchemaDiff)
    row_diff: RowDiff = field(default_factory=RowDiff)
    local_only: bool = False
    remote_only: bool = False
    conflict: bool = False
    validation_issues: list[str] = field(default_factory=list)

    @property
    def in_sync(self) -> bool:
        return (
            not self.local_only
            and not self.remote_only
            and not self.schema_diff.has_change
            and not self.row_diff.has_change
        )
