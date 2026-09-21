"""Shared CSV layout. Row 0 = header, row 1 = types, data from row 2."""

HEADER_ROW_INDEX = 0
TYPES_ROW_INDEX = 1
FIRST_DATA_ROW_INDEX = 2

DEFAULT_CSV_DIRNAME = "Assets/CSV"
DEFAULT_CONFIG_FILENAME = ".fccsv.json"
DEFAULT_STATE_FILENAME = ".fccsv-state.json"
DEFAULT_BASE_DIRNAME = ".fccsv-base"
DEFAULT_BACKUP_DIRNAME = "backups"
DEFAULT_CREDENTIALS_FILENAME = "credentials.json"
DEFAULT_TOKEN_FILENAME = "token.json"
DEFAULT_JOURNAL_FILENAME = ".fccsv-journal.jsonl"
METADATA_TAB = "_fccsv"

MAX_ROWS_PER_TAB = 20000
MAX_COLS_PER_TAB = 100

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
]
