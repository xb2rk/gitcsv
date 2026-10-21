"""Domain models for gitcsv.

All models are immutable value objects representing tabular snapshots,
identity keys, schema/cell diffs, and synchronization decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True, slots=True)
class TableSnapshot:
    """An immutable in-memory snapshot of a table (from CSV or Google Sheets)."""

    name: str
    headers: tuple[str, ...]
    types: tuple[str, ...]  # Empty tuple if source has no type row (e.g. Google Sheets)
    rows: tuple[tuple[str, ...], ...]
    source: Literal["csv", "sheet", "base"] = "csv"


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """A shape, type, or integrity issue detected in a table."""

    code: str
    message: str
    severity: Literal["error", "warning"] = "error"
    line: int | None = None


@dataclass(frozen=True, slots=True)
class Identity:
    """Strategy for uniquely identifying and matching rows between local and remote."""

    mode: Literal["columns", "position"]
    columns: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CellDelta:
    """A single cell difference between two snapshots."""

    row_key: tuple[str, ...] | int  # Key values if identified by columns, else row index (1-based)
    column: str
    local: str
    remote: str


@dataclass(frozen=True, slots=True)
class RowDiff:
    """Row-level comparison between local and remote snapshots."""

    only_local: tuple[tuple[str, ...] | int, ...] = ()
    only_remote: tuple[tuple[str, ...] | int, ...] = ()
    cells: tuple[CellDelta, ...] = ()
    order_changed: bool = False

    @property
    def has_change(self) -> bool:
        """Return True if any row or cell changes exist."""
        return bool(self.only_local or self.only_remote or self.cells or self.order_changed)


@dataclass(frozen=True, slots=True)
class SchemaDiff:
    """Column and type differences between local and remote snapshots."""

    only_local: tuple[str, ...] = ()
    only_remote: tuple[str, ...] = ()
    reordered: bool = False
    local_types_changed: bool = False

    @property
    def has_change(self) -> bool:
        """Return True if any schema differences exist."""
        return bool(self.only_local or self.only_remote or self.reordered or self.local_types_changed)


TableStatus = Literal[
    "in_sync",
    "local_changed",
    "remote_changed",
    "both_changed",
    "unbased_divergence",
    "missing_local",
    "missing_remote",
    "invalid",
]


@dataclass(frozen=True, slots=True)
class TablePlan:
    """Pure comparison plan between local CSV, remote Sheet, and BASE state."""

    name: str
    status: TableStatus
    local: TableSnapshot | None
    remote: TableSnapshot | None
    base: TableSnapshot | None
    identity: Identity | None
    schema: SchemaDiff = field(default_factory=SchemaDiff)
    rows: RowDiff = field(default_factory=RowDiff)
    diagnostics: tuple[Diagnostic, ...] = ()

    @property
    def has_diff(self) -> bool:
        """Return True if schema, row diffs, or table presence differences exist."""
        if self.status in ("missing_local", "missing_remote"):
            return True
        return self.schema.has_change or self.rows.has_change

    @property
    def has_errors(self) -> bool:
        """Return True if any error-level diagnostics are present."""
        return any(item.severity == "error" for item in self.diagnostics)


OperationKind = Literal["push", "pull", "sync"]


@dataclass(frozen=True, slots=True)
class OperationDecision:
    """The decision of what to write when executing a push, pull, or sync."""

    kind: OperationKind
    table: str
    local_after: TableSnapshot | None
    remote_after: TableSnapshot | None
    errors: tuple[str, ...] = ()

    @property
    def allowed(self) -> bool:
        """Return True if the operation is permitted without blocking errors."""
        return not self.errors

    @property
    def write_local(self) -> bool:
        """Return True if local CSV needs to be updated."""
        return self.local_after is not None

    @property
    def write_remote(self) -> bool:
        """Return True if remote Google Sheet needs to be updated."""
        return self.remote_after is not None
