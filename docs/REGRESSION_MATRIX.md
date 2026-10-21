# Phase A regression matrix

The tests in `../tests/test_v2_characterization.py` run against v2 using
temporary projects and fake Sheet gateways. They assert observed v2 behavior
to make the baseline reproducible. V3 acceptance tests will assert the desired
behavior in the final column. A passing characterization test does not mean
the defect is fixed.

### A01 — Incorrect force hint

V2 recommends `--force push` / `--force pull`, although the parser requires the
command first. V3 shows complete `fccsv3 push TABLE --force` and
`fccsv3 pull TABLE --force` commands.

### A02 — Offline false sync lineage

V2 `--offline push` returns success and writes sync state despite using an
in-memory Sheet. V3 offline planning is read-only and claims no real sync.

### A03 — Stale Google Sheet cells

V2 `values.batchUpdate` leaves old rows or columns outside a shorter write.
V3 clears surplus values while preserving cell formatting.

### A04 — Remote-added column lost on pull

V2 pull with `--allow-schema-change` drops a new Sheet column. V3 previews the
schema change and preserves the selected source schema and values.

### A05 — Duplicate-key row collapse

V2 diff can process duplicate configured keys before validation. V3 validates
both sides before keyed diff, then finds stable identity or reports ambiguity.

### A06 — Reorder missed by CI

An order-only CSV change passes v2 `status --fail-on-diff`. V3 reports reorder
and returns a nonzero CI status.

### A07 — Type-row change missed by CI

A type-row-only CSV change passes v2 `status --fail-on-diff`. V3 tracks the
local type row through a separate schema fingerprint.

### A08 — Partial metadata loss

Syncing one tab replaces v2 `_fccsv` metadata with only that tab in the
in-memory adapter. V3 keeps its authoritative BASE and history in SQLite and
does not modify legacy `_fccsv` during shadow mode or after cutover. A v3
table write clears trailing values in the target tab. V2 must no longer write
the spreadsheet after cutover because its `_fccsv` metadata will be stale.

### A09 — Init discards overrides

Re-running v2 `init` discards per-table mapping overrides. V3 preserves them
or shows an explicit migration diff.

### A10 — TUI failure severity

V2 TUI uses a normal notification when sync returns a nonzero code, and
onboarding can announce a connection after failed init. V3 must show failure
severity and must not claim a write succeeded if it failed. Test this through
the v3 TUI input/output boundary. The optional A10 v2 characterization test
executes both TUI methods with nonzero fake backend results; it requires
Textual and skips when that extra is unavailable.

The v2 code also builds partial overlays from dictionaries keyed by row identity
and does not reject keyless partial operations. Phase B must add focused tests
before exposing partial selection in v3.

## Exit criteria for Phase A

1. Design review and CLI semantics are recorded before implementing public
   commands or a persisted state format.
2. Every runnable v2 characterization uses disposable local files and a fake
   or in-memory gateway; no OAuth token or live Sheet is required.
3. The suite is executed and actual outcomes are reported, including any case
   that could not be automated in this environment.
4. V2 source and the game's `Assets/` are unchanged by Phase A.
