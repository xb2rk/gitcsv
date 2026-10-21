# Phase A evidence report

Date: 2026-10-07 (Asia/Bangkok)

## Completed

- Created an isolated `tools/fccsv-v3` workspace with a design review and a
  regression matrix. At the end of Phase A, no v3 command wrote data yet;
  later implementation added journaled write and recovery commands.
- Ran ten v2 characterization tests with Python 3.12.14 and stdlib
  `unittest`: **10 passed, 0 failed** when the optional Textual dependency was
  installed in a disposable directory. Passing means the documented v2 defects
  were reproduced; it does not mean v3 fixes them.
- All test CSVs and config/state files lived under `TemporaryDirectory`.
  The Sheets gateways were in-memory or fake. No OAuth token, live spreadsheet,
  game CSV, or FC asset was used.

Command:

```powershell
python -B -m unittest discover -s tests -p 'test_v2_characterization.py' -v
```

Reproduced cases: A01 incorrect force hint; A02 offline false sync lineage;
A03 stale Google Sheet cells after a shorter write; A04 remote-added column
lost on pull; A05 duplicate-key row collapse in diff; A06 order-only CI miss;
A07 type-only CI miss; A08 partial metadata loss; A09 `init` discarding table
overrides; A10 normal-severity sync failure and false connection message after
failed TUI onboarding. See `REGRESSION_MATRIX.md` for expected v3 behavior.

## Optional TUI dependency and test scope

A10 runs the actual v2 TUI sync and onboarding methods with a fake backend
returning nonzero status. It checks that sync omits error severity and failed
onboarding with empty output announces "sheet connected".
This is a method-boundary test; it does not render a Textual screen. The
bundled Python does not include Textual, so A10 skips in the default suite.
For the full Phase A run, Textual 8.2.8 was installed into a disposable
directory, all ten tests passed, and that directory was removed. Later v3
`test_failed_write_has_error_severity_and_pending_recovery` checks that an
interrupted write is shown as an error and retains a pending operation.

The bundled Python also lacks pytest, so Phase A uses the standard library
test runner. The temporary Textual installation fetched packages from PyPI;
the characterization itself used no network, OAuth token, or live Sheet.

## Next implementation gate

Phase B starts with the v3 package, immutable scan inputs, explicit row
identity modes, two-sided validation, schema/type fingerprints, and a pure
plan result. The v2 characterization suite stays separate from v3 acceptance
tests. V3 must not claim an issue fixed merely because its v2 characterization
test passes.
