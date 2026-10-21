# gitcsv

> **Git-like version control and synchronization between CSV files and Google Sheets.**

`gitcsv` brings the intuitive, reliable mental model of Git (`init`, `status`, `diff`, `push`, `pull`, `sync`, `restore`, `undo`) to game design tables and Google Sheets.

---

## Highlights

* **Git Mental Model**: Familiar commands like `status -s`, `diff`, `push`, `pull`, `sync` (3-way merge), `restore`, and `undo`.
* **Zero-.env Friction**: Automatically initializes and manages `.gitcsv/config.json`. No manual `.env` file required.
* **Flexible Google Auth**: Supports both **OAuth 2.0** (browser sign-in) and **Service Accounts** (key file or directly pasted JSON).
* **Safe Transactions**: Write-ahead logging (WAL) in SQLite (`.gitcsv/state.db`) + Project FileLock + post-write verification before advancing BASE.
* **Auto-Discovery**: `gitcsv init` scans Google Sheet tabs and local CSV files to generate 1-to-1 table mappings automatically.
* **Rich Terminal UI**: Beautiful ANSI tables, colored cell diffs, progress spinners, and short status mode.

---

## Quick Start

### 1. Installation

Install `gitcsv` in editable mode:

```bash
pip install -e tools/gitcsv
```

All dependencies (`typer`, `rich`, `google-api-python-client`, `google-auth`, `python-dotenv`) are included out-of-the-box.

### 2. Initialize a Project

Run the interactive wizard:

```bash
gitcsv init
```

Or pass your Google Spreadsheet URL directly:

```bash
gitcsv init "https://docs.google.com/spreadsheets/d/YOUR_SPREADSHEET_ID/edit" --csv-dir Assets/CSV
```

This automatically:
1. Extracts your Spreadsheet ID.
2. Discovers matching `.csv` files and Sheet tabs.
3. Creates `.gitcsv/config.json`.
4. Adds `.gitcsv/`, `.env.local`, and credential files to `.gitignore`.

---

## Command Reference

### Start a working area
* `gitcsv init [URL]` - Initialize gitcsv configuration and discover table mappings.

### Inspect status and differences
* `gitcsv status` - Show full status table of local CSVs vs Google Sheets.
* `gitcsv status -s` - Concise 1-line status output (like `git status -s`):
  * ` = ` In sync
  * ` M ` Modified cells
  * `?? ` CSV only (new local table)
  * ` D ` Sheet only (missing local CSV)
  * `UU ` Diverged / Conflict
* `gitcsv diff [TABLE]` - Show colored cell-by-cell and schema differences.
* `gitcsv log [TABLE]` - View history of sync operations and journal records.

### Transfer and synchronize data
* `gitcsv push [TABLE | -a]` - Publish local CSV changes to Google Sheets.
  * Options: `-f` (force), `-n` (dry-run), `-a` (all tables), `--accept-schema`.
* `gitcsv pull [TABLE | -a]` - Integrate remote Google Sheet changes into local CSV files.
  * Options: `-f` (force), `-n` (dry-run), `-a` (all tables), `--accept-schema`.
* `gitcsv sync [TABLE | -a]` - Perform an intelligent 3-way merge between local CSV, Sheets, and BASE.
  * Conflict resolution: `--ours` (prefer CSV), `--theirs` (prefer Sheet).

### Undo and recovery
* `gitcsv restore TABLE` - Discard uncommitted local CSV edits and restore from the last synced BASE state.
* `gitcsv undo TABLE` - Safely revert the latest committed push, pull, or sync operation.

### System and diagnosis
* `gitcsv auth status` - Check Google authentication status.
* `gitcsv auth login` - Sign in via Google OAuth 2.0 browser authorization flow.
* `gitcsv doctor` - Diagnose configuration, CSV format issues, formulas on Sheets, and pending WAL operations.

---

## Global Options
* `-C <path>` / `--work-dir <path>`: Run as if gitcsv was started in `<path>` (e.g. `gitcsv -C ../OtherProject status`).
* `-v` / `--version`: Display gitcsv version.
* `gitcsv help <command>`: View detailed help for any subcommand (e.g. `gitcsv help push`).

---

## License

Internal Tool - Craftland Studio / PVZ UGC Project.
