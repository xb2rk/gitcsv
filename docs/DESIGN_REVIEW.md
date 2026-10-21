# fccsv v3 design review (Phase A)

Status: implementation in progress. Phase A recorded decisions and v2
behavior; current tests use temporary CSVs and fake Sheets. No v3 write has
been run against the project CSVs or a live spreadsheet.

## Scope and precedent

The closest implementation is `tools/fccsv-v2`. Its pure `domain/` functions,
`TableSnapshot`, `SheetsGateway` protocol, CSV layout, and CLI command names are
useful precedents. The current CLI orchestrates sync and the TUI captures CLI
output. `merge3` exists but is not called by production sync. The project has
26 mapped tables and 2,048 CSV data rows. Local planning has been benchmarked
below; Google API latency has not.

The project architecture owns game CSV data under `Assets/`. This tool reads
and writes CSV values only after an explicit sync plan. It does not modify FC
source, generated symbols, or editor-owned assets.

## Decisions and alternatives

**Version.** Patching v2 in place would share its live state and CLI behavior.
Build `tools/fccsv-v3` as a sibling instead. Keep v2 usable during development;
the v3 shadow run reads the real project without writing it.

**Row identity.** A configured key requires user maintenance. A generated CSV
ID changes the game's input. Infer an identity verified across local, Sheet,
and BASE, with positional comparison when no stable identity exists.

**Conflict.** A table-level force can discard independent edits. Merge
independent cells using BASE only after identity and schema validation. Show
same-cell, order, and schema conflicts for review.

**Local state.** One JSON is simple and inspectable. One SQLite database better
supports transactions for BASE, history, Undo, and concurrent invocation.
The Phase C prototype favored SQLite; see [storage decision](STORAGE_DECISION.md).
Keep user config separate. No v2 state migration occurs in Phase A.

**CLI.** Use familiar Git verbs: `status`, `diff`, `push`, `pull`, `sync`,
and `log`, with `doctor` and `backup` for cutover. Suggestions include complete commands and
precise `--force` semantics.

## Ownership and data flow

- CSV is the local authored source with header, type, and ordered data rows.
- A Sheet tab is the remote value source with header and data rows; it has no
  CSV type row.
- The v3 state store owns BASE, last-sync evidence, operations, and backups.
- Domain planning is pure: Scan -> Validate -> Match -> Diff -> Plan performs
  no writes. Apply -> Verify -> Commit is the only write path.
- CLI and TUI consume the same structured plan and result. They do not call
  each other's presentation functions.
- A force push keeps CSV values and discards conflicting Sheet edits; a force
  pull keeps Sheet values and discards conflicting CSV edits. Force never skips
  data validation or silently approves a schema change.

Google Sheets and local files do not share an atomic transaction. The apply
pipeline must retain a pre-write snapshot, verify the result, and record a
recoverable operation state after interruption. A re-read immediately before
writing narrows the race window but is not a server-side compare-and-swap.
For a planned Sheet replacement, preflight batch-reads formula-rendered values
and blocks a tab containing formulas. The writer repeats the check immediately
before clearing values. A concurrent Sheet edit can still occur after that
check.

## Current CLI contract

The examples below are the intended CLI shape. Most are executable in the
current development build, but live Google writes remain unvalidated:

```text
fccsv status
fccsv diff BossLootData
fccsv plan push BossLootData
fccsv doctor
fccsv backup --output cutover.zip
fccsv push BossLootData
fccsv push BossLootData --force
fccsv pull BossLootData --force
fccsv log BossLootData
fccsv recover OPERATION_ID --restore
```

Every write takes a table or explicit `--all`; omitting both is a usage error.
Human output gives full
commands, table scope, overwrite direction, and the changes that can be lost.
Machine output has a versioned JSON shape. `--force` requires an explicit table
or `--all` and still requires a current plan.

## Performance and migration gates

Batch Sheet reads, reuse fetched snapshots, and keep key selection linear in
row count with a fixed candidate limit. On this Windows host with bundled
Python 3.12, three runs of a local CSV read plus pure plan across the current
26 tables (2,048 data rows) took 19.7, 23.8, and 20.0 ms. A pure plan of a
synthetic 20,000-row table where neither `ItemId` nor `AccessKey` is unique
alone, but their pair is unique, took 92.7, 90.3, and 94.1 ms. Both runs used
in-memory Sheet snapshots and exclude Google API latency and writes; they are
environment-specific checks, not production performance guarantees. No CPU
optimization is needed on this evidence. Fake Sheets adapter tests model
values left outside a shorter write range. Live write verification remains
pending by user choice.

V3 imports the existing `.fccsv.json` mapping and reads v2 state, BASE, and
remote metadata as advisory evidence without altering them. A cutover takes
local and remote backups, verifies all mapped tables, then designates one writer for the
project. Rollback instructions must name the exact state and remote snapshots.
