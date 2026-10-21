# V3 state and recovery decision

Decision: use one project-local `.fccsv3/state.sqlite3` database for v3 BASE,
operation history, and content backups. Keep `.fccsv.json` as the imported user
mapping during transition; never write v2 `.fccsv-state.json` or `.fccsv-base/`.

## Evidence and alternatives

A single JSON state file would be easy to inspect and small enough for the
current project. A durable rewrite of all 26 existing BASE snapshots produced
about 344 KB; a SQLite table holding the same payloads used about 410 KB.
In a local Windows prototype with 100 forced, durable updates, replacing the
whole JSON took 1.284 seconds and updating one SQLite row took 0.010 seconds.
These are environment-specific measurements, not a product benchmark.

Speed is secondary here. A write to Google Sheets cannot share a filesystem
transaction. SQLite lets v3 atomically record a pending operation and its
before/after snapshots before a network write, then atomically commit BASE and
the operation result after verification. A second invocation sees the pending
operation and stops. V3 also holds a project-level OS lock while applying or
recovering a write so two v3 processes cannot work on the same project at once.
JSON would require coordinated writes to history, backups, and BASE.

## Transaction boundary

1. Scan, validate, and preview without database writes.
2. Take the project write lock. Begin an immediate transaction and refuse a
   new write if any operation is pending or BASE changed. Persist the operation
   ID, both before snapshots, intended after snapshots, and pending status;
   commit this transaction before touching either side.
3. Re-read both sides. If either differs from the plan, cancel the operation
   without an external write. Otherwise, write the selected CSV and/or Sheet
   values, then read them back. An interrupted or failed write leaves the
   pending record and blocks new writes.
4. After exact verification, update BASE and mark the operation committed in
   one SQLite transaction. A recovery command can inspect pending records and
   either finish verification or restore from the backed-up values.

The recheck narrows concurrent-edit risk, but Google Sheets offers no atomic
compare-and-swap with its value write. Recovery must compare current values
with the operation's expected before/after values before restoring. It must
never overwrite later edits without an explicit directional choice.

Use `PRAGMA synchronous=FULL`, `PRAGMA journal_mode=DELETE`, and a busy timeout.
The persistent metadata is one database file. V3 also keeps a small
`.fccsv3/write.lock` file for the OS lock, and SQLite may create a transient
rollback-journal file while committing. The database contains game table
values, so documentation must cover backup and access permissions.

## Migration

Read v2 BASE only as advisory evidence in `doctor`; `status`, `diff`, and write
plans use only verified v3 BASE. The first v3 write starts a fresh v3
lineage: identical CSV/Sheet values can be adopted without changing them;
divergent values need an explicit push, pull, or reviewed merge. At cutover,
back up the v2 state, v3 state, CSVs, and affected Sheet tabs, and designate
one writer. Legacy `_fccsv` metadata is advisory to v2 and must not be treated
as v3 authority. Reverting to v2 requires restoring a matching set of CSV,
Sheet, v2 state, and v2 BASE snapshots, then comparing them before writing.
