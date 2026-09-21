"""Typed errors. CLI maps each to exit code + hint. No I/O."""

from __future__ import annotations


class FccsvError(Exception):
    exit_code: int = 1


class ConfigError(FccsvError):
    exit_code = 2


class AuthError(FccsvError):
    pass


class MappingError(FccsvError):
    exit_code = 2


class SchemaChangeBlocked(FccsvError):
    """Headers/types/order differ and --allow-schema-change is off."""

    def __init__(self, table: str, detail: str) -> None:
        super().__init__(f"[{table}] schema change blocked: {detail}")
        self.table = table
        self.detail = detail


class ConflictError(FccsvError):
    """Same cell changed on both sides since BASE; needs --force."""

    def __init__(self, table: str, cells: int = 0) -> None:
        hint = (
            f"[{table}] local and sheet both changed since last sync"
            + (f" ({cells} cell(s))" if cells else "")
            + ". Re-run with --force push (keep local) or --force pull (keep sheet)."
        )
        super().__init__(hint)
        self.table = table


class QuotaExceededError(FccsvError):
    def __init__(self) -> None:
        super().__init__(
            "Sheets quota exhausted (60 reads/min). Progress is saved — "
            "wait ~60s and re-run; synced tables are skipped."
        )


class TabMissingError(FccsvError):
    def __init__(self, tab: str) -> None:
        super().__init__(f"Sheet tab missing: '{tab}'")
        self.tab = tab


class DataValidationError(FccsvError):
    pass
