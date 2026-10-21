# Phase B acceptance report — 2026-10-08

Phase B covers immutable scan inputs, CSV and Sheet validation, row identity,
schema and type comparison, and a read-only plan. The v3 package is still in
development. No project CSV, live Sheet, v2 state, or v3 state was written for
this acceptance run.

## Acceptance evidence

| Requirement | V3 evidence |
| --- | --- |
| Preserve every scanned value and report malformed input | `test_snapshot_is_detached_from_mutable_input`, `test_csv_reader_retains_ragged_line_number_for_validation`, `test_internal_blank_csv_row_is_reported_instead_of_dropped`, `test_sheet_reader_maps_headers_without_dropping_values` |
| Validate before keyed diff | `test_duplicate_headers_are_rejected_before_any_row_diff`, `test_remote_duplicate_key_falls_back_without_losing_a_row`, `test_force_does_not_bypass_invalid_duplicate_header` |
| Choose a verified identity or use position | `test_boss_loot_uses_composite_key_after_stale_item_id_hint`, `test_shop_offer_identity_uses_shop_and_item`, `test_tooltip_reorder_is_visible_even_with_a_valid_key`, `test_keyless_table_uses_position_and_reports_changes` |
| Compare rows, order, headers, and local types | `test_independent_edits_are_classified_on_both_sides`, `test_header_reorder_is_visible_without_false_cell_changes`, `test_remote_new_column_is_kept_in_plan`, `test_type_only_change_is_local_change_since_base` |
| Keep scan and preview read-only | `test_scan_reads_remote_tabs_once_and_never_writes_csv`, `test_status_flags_work_before_and_after_command`, `test_dry_run_does_not_create_state_or_write` |

The Phase A regression matrix also has v3 acceptance evidence:

| Case | V3 acceptance |
| --- | --- |
| A01 force hint | `test_plan_and_blocked_push_explain_complete_command` |
| A02 offline false lineage | V3 has no offline write command; `test_dry_run_does_not_create_state_or_write` and `test_read_bases_does_not_create_state` cover read-only preview. |
| A03 stale Sheet cells | `test_shorter_write_clears_old_rows_and_columns` uses a fake Google service. |
| A04 remote-added column | `test_diff_json_keeps_remote_added_column`, `test_pull_keeps_new_sheet_column_and_declared_type` |
| A05 duplicate-key collapse | `test_remote_duplicate_key_falls_back_without_losing_a_row`, `test_boss_loot_uses_composite_key_after_stale_item_id_hint` |
| A06 order-only CI miss | `test_status_flags_work_before_and_after_command` asserts exit code 1 for an order-only difference. |
| A07 type-only CI miss | `test_type_only_change_is_visible_using_v3_base` asserts exit code 1 for a type-only change. |
| A08 partial metadata loss | `test_push_preserves_legacy_metadata_and_mapping_overrides` asserts a one-table push leaves both legacy `_fccsv` entries intact. |
| A09 init discards overrides | The same test asserts custom key and column mapping are used, and `.fccsv.json` remains byte-identical through push and status. V3 has no `init` command that regenerates that file. |
| A10 TUI failure severity | `test_failed_write_has_error_severity_and_pending_recovery` exercises the v3 TUI input/output boundary with a failing fake gateway. |

The A01–A10 v2 characterization tests establish observed v2 behavior; they do
not establish v3 correctness. The acceptance tests above exercise v3 separately.

## Executed checks

- `python -B -m unittest discover -s tests -v`: 118 tests discovered,
  117 passed, 1 skipped. The skipped test is the v2 A10 Textual method test;
  Textual is unavailable in the default bundled Python. It passed in the
  disposable Textual environment recorded in `PHASE_A_REPORT.md`.
- `fccsv3 validate --project <project-root> --format json`: exit code 0;
  26 configured CSV tables, 2,048 data rows, zero shape errors. This command
  reads local CSVs and does not access Google Sheets.
- The prior live read-only audit is dated 2026-10-07 in
  `READ_ONLY_AUDIT.md`. It is not a fresh remote comparison for this report.

## Boundary and next gate

Phase B's local planning and acceptance criteria are met. Fake gateways cover
write behavior, including A03 and A08, but live Google writes and recovery
remain unverified. The user chose to defer live Google write testing. The four
divergent tables in the 2026-10-07 audit still require explicit review before
any automatic synchronization or production cutover. Continue with the state,
apply, and recovery gate using disposable data; keep the existing v2 writer
separate until a reviewed cutover.
