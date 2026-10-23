# Provenance: refresh-timing plan 02, cycle C4 (ET trading date, trajectory freshness)

Every record is copied from the tracked production captures in `tests/fixtures/prod_droplet/`
(captured 2026-07-09 from the production droplet by `capture_from_droplet.py`, read-only).
No production database copy is persisted. Tests derive every expected value from these
records at run time; no value below is typed into an assertion.

One record per case the code distinguishes. Verbatim means no field changed.

| File | Record | Source (file, record) | Case covered | Changed fields | Used by | Sign-off |
|---|---|---|---|---|---|---|
| `symphony_state_entry.json` | symphony `5XjzXjdGnjh99MIsdM97` | `bot_state_positions.json`, key `5XjzXjdGnjh99MIsdM97` | the live `bot_state` card entry the live branch of `/api/state` and `/` reads | none (verbatim) | tests/app/test_api_state_et_date.py (all route cases) | not needed |
| `cycle_marker.json` | `last_successful_cycle_at` | `exit_triggers.json`, row id 87, column `cycle_id` (the engine writes that column from `last_successful_cycle_at`, `alpha_bot_execution.py` cycle stamp) | the top-level marker field a live `bot_state` carries | none (verbatim value) | tests/app/test_api_state_et_date.py (all route cases) | not needed |
| `exit_trigger_row.json` | exit trigger row id 1 | `exit_triggers.json`, row id 1 | the trigger row shape for the symphony above (reason, `at_return`, ids) | none (verbatim) | base for the derived triggers below | not needed |
| `shadow_post_trigger_row.json` | shadow row, symphony above, 2026-06-22 | `shadow_last_rows.json`, symphony `5XjzXjdGnjh99MIsdM97`, trading day 2026-06-22 (`is_post_trigger` 1) | a post-trigger shadow row, which is what `get_shadow_divergence` reads | none (verbatim) | base for the derived shadow row below | not needed |
| `shadow_trajectory_rows.json` | four last-of-day rows, symphony above, 2026-06-24, 06-25, 06-26, 06-29 | `shadow_last_rows.json`, same symphony, those trading days (one position epoch, `04ee2fad07ef40aca5b0566bc18e30be`) | a daily trajectory of one position epoch, three days read first, the fourth written later by another process | none (verbatim) | tests/analytics/test_shadow_trajectory_position_epoch.py (freshness classes) | not needed |
| `derived_records.json` / `trigger_evening_of_d` | exit trigger | row id 1 | a trigger at 15:30 EDT (19:30 UTC) on D = 2026-10-22, read after the host's UTC date has rolled to D+1 | `ts_utc` = 2026-10-22T19:30:00Z, `ts_et` = 2026-10-22T15:30:00 | test_api_state_et_date.py: afternoon trigger case | PENDING |
| `derived_records.json` / `trigger_prior_et_evening` | exit trigger | row id 1 | a trigger at 23:30 EDT on D-1 (03:30 UTC on D), the previous ET trading day | `ts_utc` = 2026-10-22T03:30:00Z, `ts_et` = 2026-10-21T23:30:00 | test_api_state_et_date.py: prior ET evening case | PENDING |
| `derived_records.json` / `trigger_before_et_midnight_after_dst_end` | exit trigger | row id 1 | a trigger at 23:30 EST on Sunday 2026-11-01, the evening before the Monday after DST ends; the ET midnight bound is 05:00Z, not 04:00Z | `ts_utc` = 2026-11-02T04:30:00Z, `ts_et` = 2026-11-01T23:30:00 | test_api_state_et_date.py: DST Monday cases | PENDING |
| `derived_records.json` / `trigger_after_et_midnight_after_dst_end` | exit trigger | row id 1 | a trigger at 00:10 EST on Monday 2026-11-02, the first minutes of the Monday ET date | `ts_utc` = 2026-11-02T05:10:00Z, `ts_et` = 2026-11-02T00:10:00 | test_api_state_et_date.py: DST Monday cases | PENDING |
| `derived_records.json` / `trigger_midday_shared_date` | exit trigger | row id 1 | a trigger at 09:00 EDT on D, when UTC and ET share the date | `ts_utc` = 2026-10-22T13:00:00Z, `ts_et` = 2026-10-22T09:00:00 | test_api_state_et_date.py: shared date case | PENDING |
| `derived_records.json` / `shadow_row_on_d` | shadow row | `shadow_post_trigger_row.json` | a post-trigger shadow row on the ET trading day D = 2026-10-22 | `trading_day` = 2026-10-22, `ts_utc` = 2026-10-22T13:30:03Z, `ts_et` = 2026-10-22T09:30:01 | test_api_state_et_date.py: shadow divergence cases | PENDING |

Derived records change dates only. The PM signs each derived record as producer owner; until
the PENDING cells are replaced by a sign-off line, the coverage rows of the cases that use
them are held.
