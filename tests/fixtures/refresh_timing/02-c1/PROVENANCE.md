# Provenance: refresh-timing plan 02, cycle C1

This folder holds no data files. Every record the C1 tests use is read by path from the
tracked 2026-07-09 droplet captures in `tests/fixtures/prod_droplet/` (provenance in
`tests/fixtures/prod_droplet/capture_from_droplet.py`, lines 1 to 24: read-only copy of the
production state database, captured 2026-07-09, deployed SHA 0bcbd1a3). Nothing is derived,
so no producer-owner sign-off is needed and no coverage row is held for one.

The values are chosen in the `captured_markers` fixture of `tests/realtime_push/conftest.py`.

| Record used | Source file and record | Case it covers | Changed fields |
|---|---|---|---|
| `last_successful_cycle_at` before a run | `exit_triggers.json`, row id 86, column `cycle_id` (`2026-07-09T10:10:01.071429-04:00`) | the marker's value before an engine run | none |
| `last_successful_cycle_at` after a run | `exit_triggers.json`, row id 87, column `cycle_id` (`2026-07-09T10:14:01.308786-04:00`) | the marker advanced by one real consecutive engine cycle | none |
| snapshot `trading_day` before | `shadow_last_rows.json`, the second-newest distinct `trading_day` of the capture | the close snapshot's day before the close step | none |
| snapshot `trading_day` after | `shadow_last_rows.json`, the newest distinct `trading_day` of the capture (`2026-07-09`) | the close snapshot's day after the close step | none |
| a symphony entry in `bot_state` | `bot_state_positions.json`, key `iaSOOUsmnCJHiZvbrWfs` | a realistic `bot_state` blob around the two markers | none |
| a post-mortem body | `post_mortems/post_mortem_2026-06-24.json`, copied byte for byte into a temporary directory under the name `post_mortem_2026-06-24.json` | the stage-1 file the engine writes once a day; the run is dated 2026-06-24 so the file name and the body agree | none |

`exit_triggers.cycle_id` is the engine's own `last_successful_cycle_at` at the time of the
trigger (`alpha_bot_execution.py`, the `cycle_id=bot_state.get("last_successful_cycle_at")`
argument of the trigger insert), so two consecutive rows are a real advance of that marker.

Not captured, by design: the engine's exit status and which fields it writes on each exit
path. The stub engine in `tests/realtime_push/conftest.py` mirrors the cited engine branches
(closed-market return, blackout return, close step, a raise after the close writes) and is
the PM-signed stub of plan 02's Fixture provenance.

Test-supplied instants (`_now_et` frozen at 10:14, 10:43, 15:53, 16:00 and 20:00 ET on
2026-07-09, and 15:53 to 15:55 ET on 2026-06-24) are literals chosen on real trading days
from the golden calendar in `tests/fixtures/math/holiday_calendar/`; they are not producer
data. Calendar cases in `tests/realtime_push/test_cycle_outcome_classification.py` come from
that golden calendar, whose own derivation is stated in its files.
