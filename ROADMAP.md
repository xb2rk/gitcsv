# fccsv-v2 ROADMAP (memory + phase tracker)

Status: single source of truth for v2 progress. Update after each phase.

## Locked decisions
- Python v2, fresh `tools/fccsv-v2/`, wire-compat with v1 (`.fccsv.json`, `.fccsv-state.json`, `_fccsv` tab, CSV header+types+data).
- CLI first, TUI second (Textual, same `app/` calls).
- Partial `--only keys --exclude cols` (CLI flags; TUI checkbox builds same filter).
- Local-only `log` from `base/`. No shared log tab.
- Strict schema: designers values-only, dev `--allow-schema-change` + `fccompile`.
- `status --fail-on-diff` CI gate: yes.
- One spreadsheet per project. Config shaped as `sources: [...]` (one entry) for future expansion.
- Small tables: enforce MAX_ROWS/COLS, fail fast, no paging.
- Tab names immutable; `rename --old X --new Y` explicit migration; rename guard suggests `did you mean?`.
- Values-only sync; `format --apply` opt-in deferred.
- On-demand `fetch/status --fetch`; no background daemon in v2.0.

## Phases
- [x] Phase 0 — spec + skeleton (this file, pyproject, docs)
- [x] Phase 1 — `domain/` core: snapshot/keys/diff/merge3/schema/validate/errors
  (29 tests green, ruff clean, mypy strict clean)
- [x] Phase 2 — `adapters/` + `app/`: local/sheets/state/base/backup, sync plan/apply, auth/init/remote/fetch/doctor
  (59 tests green, ruff check + format clean, mypy strict clean on 37 files)
- [x] Phase 3 — `interfaces/cli.py`: auth/init/status/diff/push/pull/fetch/log/remote/rename/validate/doctor,
  partial --only/--exclude, --fail-on-diff CI gate, --short/--stat/--json, lineage-on-no-op
  (75 tests green = 59 + 16 CLI, ruff check + format clean, mypy strict clean on 32 files)
- [x] Phase 4 (done early, merged into Phase 2) — oracle port (`test_sync_roundtrip.py`:
  seed/pull/conflict/force/one-sided/missing-tab/batch/schema-gate/init) + fault matrix
  (`test_sync_faults.py`: dry-run purity, idempotency, corrupt-state quarantine, metadata
  fallback, stamp fast path, partial only/exclude, remote-set lineage reset, rename guard,
  BOM/CRLF, type-row preservation, backup rotation, doctor, auth resolution)
- [x] Phase 5 (TUI) — `interfaces/tui/` (`backend.py` delegates to CLI orchestration
  with captured output; `panels.py` tables/diff/status; `modals.py` conflict/`--only`/report;
  `app.py` lazygit-style dashboard + `fccsv tui` subcommand, 10 Pilot/backend tests)
- [ ] Phase 5 (remaining) — `setup.ps1` + PyInstaller release
  (done early: command renamed `fccsv2` -> `fccsv`, version 2.0.0; tier-5 bundled
  `credentials.json` with next-to-exe override + `doctor` origin line, 9 auth tests)

## Phase 1 exit criteria
- `domain/` imports with zero third-party deps.
- `TableSnapshot` carries `raw_widths` + `line_no` (fixes v1 `validate.py:42` dict-len ragged miss).
- `merge3(BASE,LOCAL,REMOTE)` cell-level: auto-merge when only one side changed, conflict on same-cell both-changed.
- `translate_snapshot` pure (no input mutation, fixes v1 `diff_merge.py:53`).
- Tab range escaping helper (`''` doubling) ready for adapters.
- `pytest tests/test_domain*.py` green.
