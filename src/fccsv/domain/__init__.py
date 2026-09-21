"""Domain public surface. Pure functions only; no I/O here."""

from fccsv.domain.diff import diff_rows, diff_schema, filter_snapshot, translate_snapshot
from fccsv.domain.errors import (
    AuthError,
    ConfigError,
    ConflictError,
    DataValidationError,
    FccsvError,
    MappingError,
    QuotaExceededError,
    SchemaChangeBlocked,
    TabMissingError,
)
from fccsv.domain.keys import (
    apply_column_map,
    auto_map_tabs_to_csvs,
    escape_tab,
    infer_key_column,
    normalize_name,
    suggest_rename,
)
from fccsv.domain.merge3 import both_changed_since_base, merge3
from fccsv.domain.schema import ensure_schema_compatible
from fccsv.domain.snapshot import (
    CellChange,
    MergeResult,
    RowDiff,
    SchemaDiff,
    TableSnapshot,
    TableStatus,
)
from fccsv.domain.validate import ensure_valid, find_duplicate_keys, validate_snapshot

__all__ = [
    "AuthError",
    "CellChange",
    "ConfigError",
    "ConflictError",
    "DataValidationError",
    "FccsvError",
    "MappingError",
    "MergeResult",
    "QuotaExceededError",
    "RowDiff",
    "SchemaChangeBlocked",
    "SchemaDiff",
    "TableSnapshot",
    "TableStatus",
    "TabMissingError",
    "apply_column_map",
    "auto_map_tabs_to_csvs",
    "both_changed_since_base",
    "diff_rows",
    "diff_schema",
    "ensure_schema_compatible",
    "ensure_valid",
    "escape_tab",
    "filter_snapshot",
    "find_duplicate_keys",
    "infer_key_column",
    "merge3",
    "normalize_name",
    "suggest_rename",
    "translate_snapshot",
    "validate_snapshot",
]
