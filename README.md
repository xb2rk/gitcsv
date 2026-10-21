# fccsv v3 (development preview)

V3 keeps the existing CSV header/type/data layout and imports the current
`.fccsv.json` mapping. It detects row identity for comparison, so everyday
CSV editing does not require users to manage a key column. V3 state and
recovery history live in one project-local `.fccsv3/state.sqlite3` file.
V2 state and BASE files are never rewritten by v3.
CLI and TUI `status`, `diff`, and `plan` use only v3 BASE; `doctor` examines v2
BASE as advisory cutover evidence.
Each mapped tab owns one CSV file under `csv_dir`. Duplicate CSV targets and
paths escaping that directory are rejected before scanning or writing.

**Live Google writes are not yet validated.** Write and recovery tests use
temporary CSVs and fake Sheets. Do not run v3 write commands against the
production spreadsheet until the live adapter and cutover gates are complete.
The [2026-10-07 read-only audit](docs/READ_ONLY_AUDIT.md) records the live
26-table check and the four tables blocked from automatic sync.
The Google writer owns the whole table tab: it clears old values before writing
new values, including stale rows and columns, while preserving formatting.
Before clearing, it checks for formulas and refuses to write a tab containing
them. Use dedicated data-only tabs; v3 does not preserve formulas through a
table replacement. The check cannot prevent a concurrent Sheet edit between
the read and write.

## Commands

Install Python 3.11+ in a separate virtual environment so the v2 and new
fccsv commands cannot collide. Each user signs in
with `fccsv auth login` and keeps their own OAuth token. Copy `.env.example` to
the **project root** as `.env.local` and set `FCCSV3_TOKEN_FILE` and
`FCCSV3_CREDENTIALS_FILE` to local file paths. Do not put token JSON in this file
or commit token or OAuth client files. CLI `--token`/`--credentials` paths override
process environment, then `.env.local`, then paths from `.fccsv.json`.

```powershell
pip install -e '.[sheets]'
fccsv auth status --project <project-directory>
fccsv auth login --project <project-directory>
fccsv validate --project <project-directory>
fccsv doctor --project <project-directory>
fccsv backup --output cutover.zip --project <project-directory>
fccsv status BossLootData --project <project-directory>
fccsv diff BossLootData --project <project-directory>
fccsv plan push BossLootData --force --project <project-directory>
fccsv plan push . --force --project <project-directory>
```

`doctor` audits v2 state, v2 BASE, `_fccsv`, current CSV/Sheet values, and any
pending v3 write without changing them. `backup` writes a verified ZIP of CSV
bytes, Sheet values, configuration, and available v2/v3 state. Its `--output`
path must not already exist. It compares CSV files and Sheet values again
before publishing the ZIP, and fails if they changed during capture. The ZIP
contains display values returned by Sheets, not formulas or formatting.
The other commands above read both sides or make a dry plan. A write takes one
name, several names, or a mapped file/directory path. `.` selects mapped
CSVs under the current directory, like Git; `--all` selects every configured
table regardless of the current directory.
The current mapping covers game-data CSVs under Assets/CSV. Localization is
not included in the period selection yet; see the
[localization sync proposal](docs/LOCALIZATION_SYNC_DESIGN.md).

`--project` and `--token` work before or after the command.

```powershell
fccsv push BossLootData --force --project <project-directory>
fccsv push ActiveSkillData ProgressionAccessData --force --project <project-directory>
fccsv pull BossLootData --force --project <project-directory>
fccsv sync BossLootData --project <project-directory>
fccsv push --all --force --project <project-directory>
# From the project root: fccsv push . --force
fccsv push Assets/CSV/BossLootData.csv --force --project <project-directory>
fccsv log --project <project-directory>
fccsv recover --project <project-directory>
fccsv undo BossLootData --dry-run --project <project-directory>
fccsv tui --project <project-directory>
```

`push` chooses CSV values; `pull` chooses Sheet values. If both sides differ
without a trusted v3 BASE, the first directional write needs `--force` after
reviewing `diff`. Once BASE exists, a directional command refuses to discard
changes made only on its target side unless `--force` is provided. `sync`
three-way merges independent cell edits against BASE and reports conflicts;
it does not accept an ambiguous force direction. Schema changes require
`--accept-schema`. If a pull adds a Sheet column, declare its CSV type:

```powershell
fccsv pull Items --force --accept-schema --type Extra=text
```

Every write stores both before images before changing anything, then reads
both sides back before advancing BASE. If an interrupted write leaves a pending
operation, inspect it and explicitly finish or restore it:

```powershell
fccsv recover OPERATION_ID --project <project-directory>
fccsv recover OPERATION_ID --continue --project <project-directory>
fccsv recover OPERATION_ID --restore --project <project-directory>
```

Recovery refuses current values that match neither saved version unless the
operator inspects them and repeats with `--force`. No offline write mode exists.

`undo TABLE` reverts the latest committed operation for that table using both
saved before images. It refuses later CSV or Sheet edits. `undo TABLE --dry-run`
previews without writing. `--operation ID` checks that the selected ID is still
the latest for the table. Undo itself is journaled and recoverable. If the
original CSV and Sheet differed, undo removes that table's trusted BASE rather
than inventing a common version.

A named group and `--all` batch-read their selected tables and block the
entire command if any initial plan is unsafe. Consecutive unchanged tables are
rechecked with one batch read. Changed tables are still written and verified
individually so each write has its own recoverable journal entry. Google
tab metadata is cached briefly, and HTTP 429 causes a visible one-minute retry
(up to three retries). Text output streams one line per table; `--verbose`
shows expanded details. A final line shows completed count and elapsed time.
Repeated no-op runs do not create new history entries.
Google Sheets cannot commit all tabs atomically; if a later table fails, earlier
tables remain committed and the output lists completed tables plus any pending
recovery operation. `--type` requires exactly one pull table; pull a
table with new Sheet columns individually.

`status --fail-on-diff` and `diff --exit-code` return 1 for changes. `doctor`
returns 1 for cutover blockers. Write
commands return 1 for a blocked plan, 2 for configuration or access errors,
and 3 when a pending operation needs recovery. `--format json` is available
for doctor, backup, status, diff, plan, write, recover, and log commands.

## Project status

Read-only commands, verified cutover backup, and the fake-gateway
write/recovery flow are implemented.
The guided terminal UI shows status, diff, plans, and copyable CLI commands;
it requires explicit confirmation for writes. Advanced schema options remain
in the CLI. Remaining gates include live Google write and sign-in validation
and production cutover. One writer per project is required during cutover. See the [distribution guide](docs/DISTRIBUTION.md),
[design review](docs/DESIGN_REVIEW.md),
[storage decision](docs/STORAGE_DECISION.md), and
[Vietnamese user guide](docs/USER_GUIDE.vi.md).

Run tests without optional dependencies:

```powershell
python -m unittest discover -s tests -p 'test_*.py' -v
```
