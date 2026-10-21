# Localization sync proposal

Recommendation: include the authored localization catalog in FCCSV, but only
after adding its own format support. Today the period selector includes only
mapped CSVs under the current directory. The current mappings are game-data
CSVs under Assets/CSV.
It does not include Assets/Localization/key.csv.

## Why an ordinary table mapping is unsafe

Assets/Localization/key.csv has a header of display-language names. Its second
row begins LanguageKey,en,zh-TW,... and records language codes; it is not a
type row. The current game-data adapter treats every second row as types and
writes a type row on export. Assets/Localization/template.csv has no type row
at all. The current config loader also limits mapped files to one csv_dir and
one spreadsheet ID. Adding key.csv as an ordinary mapping would misread the
format and could rewrite it incorrectly.

## Proposed contract

- Make key.csv a named dataset, for example localization, with format set to
  localization and key identity set to Key. Keep template.csv outside sync;
  it is a scaffold, not the authored translation catalog.
- Allow the dataset to declare its source file and Google spreadsheet/tab
  explicitly. Distinguish source identity from tab name in BASE and journal
  state if multiple spreadsheets are allowed.
- Treat the LanguageKey row as protected metadata. Preserve language columns,
  row order, empty translations, and CSV quoting/encoding rules. Never infer
  game-data types from that row.
- Validate duplicate and blank keys, column widths, language-code mapping,
  and placeholder signatures such as {0} and {1}. Missing translations should
  be reported by language with a configurable severity, not silently filled.
- Start with local validation, status, diff, and plan. Test round-trip and
  merge with fake Sheets, then use a disposable localization spreadsheet for
  live push, pull, interruption, and recovery.
- After these gates pass, add localization to the configured dataset list.
  Then fccsv push localization targets it alone and fccsv push . includes it
  with every other configured dataset. A bare push remains invalid.

This is a design only. No localization file or live Sheet was changed.
