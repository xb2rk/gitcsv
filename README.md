# fccsv

General FC CSV <-> Google Sheets bidirectional sync. No game branding in code:
game-specific paths live in `.fccsv.json` config, so any Craftland FCC project
with `Assets/CSV/` can reuse it.

## Why this shape

FC loaders read CSVs positionally via generated `XxxCSVIndex` enums plus a
`Length` guard and skip malformed rows. A silent column add/remove/reorder
therefore drops rows in-game. `fccsv` treats **data edits as syncable** and
**schema edits as blocked by default** (`--allow-schema-change` + manual
`.fcc` + compile step).

Sheet tabs hold header + data rows only. The FC type row (row 2) stays local.

## Setup (teammates)

```powershell
pip install -e tools/fccsv            # CLI core, no Google deps
pip install -e "tools/fccsv[sheets]"  # real Sheets sync
Copy-Item tools/fccsv/.env.example credentials.json  # then fill from Google Cloud Console (Desktop app)
fccsv auth
fccsv init --sheet "<spreadsheet URL or id>"
fccsv status
```

## Commands

```powershell
fccsv status                       # aligned table: in-sync / data-diff / schema-diff / CONFLICT / invalid-local
fccsv push --tab ShopData           # local CSV -> sheet tab (omit --tab for all; creates missing tabs)
fccsv pull --tab ShopData           # sheet tab -> local CSV (backs up first)
fccsv validate                      # local-only check: row widths, duplicate keys/headers (no sheet needed)
fccsv push --allow-schema-change --dry-run  # preview a schema change
fccsv push --force push             # resolve a both-sides-changed conflict, keeping local
fccsv version
```

Exit codes: `0` ok, `1` error (incl. blocked schema/conflict/validation),
`2` CLI usage. Global flags: `--root <dir>`, `--offline` (no Google),
`--verbose`, `--quiet`.

## Keys

Row identity is the mapping's `key_column`, auto-inferred at `init` and
uniqueness-tested against local rows: single id-ish columns first
(`ShopData -> ShopId`), then composites (`ItemTooltipCatalog ->
ItemId,SortOrder`, `MissionReward -> Key,PointsRequired`). Tables with no
unique key (e.g. `ShopSlotData`, a weighted pool with repeat entries) match
positionally by row index — `status` still works, reorders just look noisy.
Override any key in `.fccsv.json` (`key_column: "ShopId,SlotIndex"`).

After any `pull`, run the required FC validation:

```powershell
fccompile.exe -i Assets
```

## Schema change procedure

1. `fccsv status` shows `schema added=[...] removed=[...] reordered=...`.
2. Get approval (column order/index is a public contract).
3. Update `Contracts/CSVIndex.fcc` member + `Length`, update the owning `Data/*.fcg` loader if the field is used.
4. Re-run with `--allow-schema-change`, then `fccompile.exe -i Assets`.

## Layout

```text
src/fccsv/
  main.py / __main__.py   CLI entry only
  interfaces/cli.py       argparse, no logic
  app/                    auth_service, init_service, sync_service (coordination)
  domain/                 mapping, diff_merge, schema_guard, models (pure, tested)
  adapters/               local_store, sheets_gateway, state_store, backup_store
  core/                   config, constants, logging
tests/                    pytest, no Google deps (InMemorySheetsGateway)
```

`GoogleSheetsGateway` is the only module importing Google libs, and only
lazily, so core + tests install with zero third-party dependencies.
