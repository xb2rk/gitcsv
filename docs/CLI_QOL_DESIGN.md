# CLI usability and local token paths

Date: 2026-10-15

## Current precedent

V2 batch-reads selected Sheet tabs but its ordinary sync loop writes one tab at
a time. V3 batch-reads preflight and verifies unchanged tabs in groups, while
changed tabs retain one durable operation and before/after verification each.
V3 state is project-local SQLite. The existing .fccsv.json mapping remains
the shared source of table names and default OAuth file paths.

## Options and decision

- Keep separate single-table and all-table CLI flows: least code change, but
  output and request behavior continue to drift.
- Accept one or more named tables or all tables, and route them through one
  preflight/apply flow: chosen. One name is a group of one. Text output is one
  line per table with optional verbose details. Existing single-table JSON
  retains its shape; named groups use the batch JSON shape with all=false.

Path selectors now follow Git-style scope: . selects mapped CSV files under the
current directory, a file or directory path selects mapped files there, and
--all selects every configured table regardless of cwd. Table names remain
valid selectors. Read commands still default to all; bare writes remain errors.

The chosen flow batches reads and unchanged verification. Changed writes remain
per-table because the current journal permits one pending operation at a time.
A multi-tab external write needs a durable batch journal and recovery contract;
combining clears and updates without that would make interrupted runs harder
to restore. No SQLite schema or project CSV format changes are made here.

## Per-user OAuth paths

- .fccsv.json keeps shared default file paths and spreadsheet mappings.
- A command-line token or credentials path wins over process environment, then
  project .env.local, then .fccsv.json.
- Only FCCSV3_TOKEN_FILE and FCCSV3_CREDENTIALS_FILE are read from the
  environment file. Their values are file paths, never token JSON or secrets.
- Project .env.local is optional and ignored by Git. Relative paths are
  resolved against the project root; ~ expands to the user's home.
- Each collaborator signs in with fccsv auth login and retains a distinct
  token. The project root token and OAuth client files are ignored by Git.

A process environment variable alone is simpler but less convenient for a
shared checkout. A shared token in .env is rejected because refresh tokens
grant account access and cannot safely be committed or distributed.

## Cost and verification

For a selection of N tabs, preflight is a batched Sheet read. Unchanged tabs
are batch-rechecked. Changed tabs reuse that preflight plan, then recheck their
inputs and verify output individually; this removes one redundant read per
changed tab without weakening pre-write or post-write checks. Google tab-title
metadata is cached for up to one minute within a command. HTTP 429 retries
announce their 60-second wait, up to three times; exhausted retries preserve
the existing recovery path. This helps quota spikes but cannot make a multi-tab
write atomic.

Verify selection order, duplicates, blocked preflight, JSON compatibility,
one-line text output, no-op BASE, request counts, secret-file precedence,
and local Git ignores with fake gateways and temporary projects. No live Sheet
write is required to test this CLI change.

## Git-style staging proposal

Path selectors are in place: `.` means mapped CSVs under the current
directory; a file or directory path narrows the selection; `--all` is
project-wide. This is selection, not a Git index. Today `fccsv push .`
applies a sync operation after preflight.

A future full Git workflow would use `fccsv add <path>` to store an exact
local CSV snapshot in a separate index, `fccsv diff --staged` to inspect
it, `fccsv commit -m <message>` to save a local revision, and a bare
`fccsv push` to send committed revisions to Google Sheets. `fccsv pull`
would fetch and merge remote changes. `status --short`, `log --oneline`,
and `restore --staged` would complete the familiar review flow.

This requires separate index and local-commit state in SQLite. The existing
BASE represents the last verified CSV/Sheet synchronization and cannot act
as a staging area. Push must apply the committed snapshot even if the working
CSV changes later, while retaining current remote rechecks and pending
recovery. Pull must preserve uncommitted local work and report conflicts.
Do not add an `add` alias until those behaviors are implemented.
