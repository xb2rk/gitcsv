# Read-only cutover audit — 2026-10-07

This is a snapshot of the configured PlantsVsZombiesFCC project and its live
Google spreadsheet. The run used a temporary copy of the OAuth token and did
not write CSV files, Sheet values, v2 state, or v3 state.

`fccsv3 doctor --format json` scanned all 26 mapped tables. It returned no hard
blockers and 14 warnings. `BossLootData` used the validated compound identity
`ItemId,AccessKey`; the configured `ItemId` hint was not unique on both sides.

`fccsv3 plan sync --all --format json` then preflighted all 26 tables. It
reported `preflight_ok: false` and blocked four tables with `no trusted common
BASE`: `BossLootData`, `ItemTooltipCatalog`, `MissionReward`, and
`ShopSlotData`. An actual `sync --all` would therefore write no tables in this
state. `doctor`'s zero blocker count means the data can be reviewed; it does
not mean these differences can be merged automatically.

| Table | Read-only finding |
| --- | --- |
| BossLootData | CSV and Sheet differ. The v2 BASE header or type row differs from CSV, and CSV differs from the last v2 state hash. |
| ItemTooltipCatalog | CSV and Sheet differ; configured `ItemId,SortOrder` is not unique on both sides; CSV differs from the last v2 state hash. |
| MissionReward | CSV and Sheet differ; Sheet differs from the last v2 state hash. |
| ShopSlotData | CSV and Sheet differ; configured `ItemId` is not unique on both sides; CSV differs from the last v2 state hash. |
| ZombieData | The legacy `_fccsv` hash differs from v2 state. |

The four blocked tables were also inspected with a second read-only Sheet
request. These are the differences v3 sees before any write:

| Table | CSV / Sheet rows | CSV-only / Sheet-only identities | Changed cells | Schema and action to review |
| --- | ---: | ---: | ---: | --- |
| BossLootData | 77 / 12 | 75 / 10 | 0 | CSV replaces `BaseDropRatePct`, `DropRatePctPerLevel`, `BaseQty`, `QtyPerLevel` with `Pool`, `MinQty`, `MaxQty`, `Weight`. This is a deliberate schema migration to review, not just a stale key hint. |
| ItemTooltipCatalog | 203 / 178 | 25 / 0 | 0 | Same columns; review the 25 CSV-only rows before choosing an explicit direction. |
| MissionReward | 30 / 31 | 0 / 1 | 0 | Sheet has an extra `Weekly,12` identity; review whether it belongs in CSV. |
| ShopSlotData | 52 / 44 | 24 / 16 | 51 | Row order and values also differ; inspect individual changes and reconcile deliberately. |

Legacy `_fccsv` metadata also contains duplicate tab names. V3 treats it as
advisory and does not use it to choose row identity or advance BASE.

Before production cutover, review diffs for the four divergent tables, create
and retain a `fccsv3 backup --output ...` archive, and decide explicitly which
side or merged result should be authoritative for each table. Re-run `doctor`
and `plan sync --all` after reconciliation. Live Google writes and recovery
should first be exercised on a disposable spreadsheet; this audit verified
only live reads and preflight. A separate read-only Sheets request with
`valueRenderOption=FORMULA` returned all 26 mapped tabs (1,977 rows and
14,683 cells) and found zero formula cells on 2026-10-07. This is a snapshot;
v3 checks again before replacing a tab. The formula guard has fake-service
test coverage but has not been exercised in a live write.
