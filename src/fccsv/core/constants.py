"""Shared constants. No I/O here."""

from pathlib import Path

# FC convention: row 1 = header, row 2 = types, data from row 3 (0-based index 2).
HEADER_ROW_INDEX = 0
TYPES_ROW_INDEX = 1
FIRST_DATA_ROW_INDEX = 2

DEFAULT_CSV_DIRNAME = "Assets/CSV"
DEFAULT_CONFIG_FILENAME = ".fccsv.json"
DEFAULT_STATE_FILENAME = ".fccsv-state.json"
DEFAULT_TOKEN_FILENAME = "token.json"
DEFAULT_CREDENTIALS_FILENAME = "credentials.json"

# Google OAuth scopes: spreadsheets read/write plus Drive metadata (readonly)
# for the zero-read fast path (modifiedTime check). Old tokens without the
# Drive scope keep working; that tier just skips until re-auth.
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
]

# Hard cap to avoid accidental giant sheet writes.
MAX_ROWS_PER_TAB = 20000
MAX_COLS_PER_TAB = 100


def find_project_root(start: Path) -> Path:
    """Walk up until a directory containing Assets/CSV is found."""
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / DEFAULT_CSV_DIRNAME).is_dir():
            return candidate
    return current
