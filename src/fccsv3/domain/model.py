"""Immutable values shared by the v3 planning stages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Severity = Literal["error", "warning", "info"]
PlanStatus = Literal[
    "invalid",
    "missing_local",
    "missing_remote",
    "in_sync",
    "unbased_divergence",
    "local_changed",
    "remote_changed",
    "both_changed",
]


@dataclass(frozen=True, slots=True)
class Diagnostic:
    code: str
    message: str
    severity: Severity = "error"
    line: int | None = None


@dataclass(frozen=True, slots=True)
class TableSnapshot:
    """Ordered cell values; malformed CSV rows remain representable for diagnostics."""

    name: str
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    types: tuple[str, ...] = ()
    source: Literal["csv", "sheet", "base"] = "csv"
    raw_widths: tuple[int, ...] = ()
    line_numbers: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        # Copy at the boundary so callers cannot mutate a planned snapshot.
        object.__setattr__(self, "headers", tuple(self.headers))
        object.__setattr__(self, "rows", tuple(tuple(row) for row in self.rows))
        object.__setattr__(self, "types", tuple(self.types))
        object.__setattr__(self, "raw_widths", tuple(self.raw_widths))
        object.__setattr__(self, "line_numbers", tuple(self.line_numbers))

    def row_as_dict(self, index: int) -> dict[str, str]:
        return dict(zip(self.headers, self.rows[index], strict=True))


@dataclass(frozen=True, slots=True)
class Identity:
    mode: Literal["columns", "position"]
    columns: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True, slots=True)
class SchemaDiff:
    only_local: tuple[str, ...] = ()
    only_remote: tuple[str, ...] = ()
    reordered: bool = False
    local_types_changed_since_base: bool = False

    @property
    def has_change(self) -> bool:
        return bool(
            self.only_local
            or self.only_remote
            or self.reordered
            or self.local_types_changed_since_base
        )


@dataclass(frozen=True, slots=True)
class CellDelta:
    row: tuple[str, ...] | int
    column: str
    local: str
    remote: str


@dataclass(frozen=True, slots=True)
class RowDiff:
    only_local: tuple[tuple[str, ...] | int, ...] = ()
    only_remote: tuple[tuple[str, ...] | int, ...] = ()
    cells: tuple[CellDelta, ...] = ()
    order_changed: bool = False

    @property
    def has_change(self) -> bool:
        return bool(self.only_local or self.only_remote or self.cells or self.order_changed)


@dataclass(frozen=True, slots=True)
class TablePlan:
    name: str
    status: PlanStatus
    local: TableSnapshot | None
    remote: TableSnapshot | None
    base: TableSnapshot | None
    identity: Identity | None
    schema: SchemaDiff
    rows: RowDiff
    diagnostics: tuple[Diagnostic, ...] = ()

    @property
    def has_diff(self) -> bool:
        return (
            self.local is None
            or self.remote is None
            or self.schema.has_change
            or self.rows.has_change
        )

    @property
    def has_errors(self) -> bool:
        return any(item.severity == "error" for item in self.diagnostics)
