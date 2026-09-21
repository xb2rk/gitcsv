"""Typed errors so CLI can map them to exit codes and hints."""


class FccsvError(Exception):
    pass


class ConfigError(FccsvError):
    pass


class AuthError(FccsvError):
    pass


class MappingError(FccsvError):
    pass


class SchemaChangeBlocked(FccsvError):
    """Raised when headers/types/order differ and --allow-schema-change is off."""

    def __init__(self, table: str, detail: str) -> None:
        super().__init__(f"[{table}] schema change blocked: {detail}")
        self.table = table
        self.detail = detail


class ConflictError(FccsvError):
    """Raised when local and remote both changed since the last sync."""

    def __init__(self, table: str) -> None:
        super().__init__(
            f"[{table}] local and sheet both changed since last sync. "
            "Re-run with --force push (keep local) or --force pull (keep sheet)."
        )
        self.table = table


class QuotaExceededError(FccsvError):
    """Raised on Sheets 429 rate-limit. Progress is saved; re-run after ~60s."""

    def __init__(self) -> None:
        super().__init__(
            "Sheets quota exhausted (60 reads/min). Progress is saved in "
            ".fccsv-state.json — wait ~60s and re-run; synced tables are skipped."
        )


class TabMissingError(FccsvError):
    """Raised when a sheet tab does not exist. Treated as remote-missing."""

    def __init__(self, tab: str) -> None:
        super().__init__(f"Sheet tab missing: '{tab}'")
        self.tab = tab


class DataValidationError(FccsvError):
    pass
