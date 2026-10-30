# 01-c2 fixtures: provenance

Plan: server-state-truth, cycle 2 (R7 is also read by cycle 5). Batch FX-01.

All records here are captured, none derived. They come from the PM's read-only run of `capture.py` with `_cases_01.py` on the droplet, as the service user, against the live `/opt/planetstopper/alphabot_state.db`, opened `mode=ro` (plus `PRAGMA query_only`). The capture ran at deployed SHA `37c8e983` (`origin/main`). The records were copied to `.claude/review/refresh-timing-captures/20261023T005559Z/` in the main checkout (untracked) and committed here byte for byte; the sha256 values below were equal on the droplet and locally, and were re-checked after the copy into this folder.

## Records

| File | Case it covers | Source | Capture moment | Sidecar | sha256 |
|---|---|---|---|---|---|
| `shadow_rows_two_cycles.json` | R6: two consecutive engine cycles' `shadow_history` rows for one triggered and one untriggered symphony, plus each one's last row of the previous trading day (case `01-c2-shadow-rows`) | table `shadow_history`; `trading_day = 2026-10-21`; the two newest `cycle_id` values by `MAX(ts_utc)`, `20261021_1603` and `20261021_1604`; symphony ids `nOyb55RMGVCKPiYXv7TI` (triggered) and `5XjzXjdGnjh99MIsdM97` (untriggered); each one's newest earlier row (`trading_day` 2026-10-20, cycle `20261020_1604`) | `captured_at_utc` 2026-10-23T00:55:46Z; `newest_shadow_history_ts_utc_at_capture` 2026-10-22T20:04:03Z | `shadow_rows_two_cycles.capture.json` | `ccd51d83790659d622697f6df88c2c14281550a5f4ba89320d1dfa98f135190d` |
| `exit_trigger_row.json` | R7: a recorded exit of the triggered symphony (case `01-c2-exit-trigger`) | table `exit_triggers`; `symphony_id = nOyb55RMGVCKPiYXv7TI`, newest by `ts_utc DESC, id DESC`: id 509, `ts_et` 2026-10-22T11:33:08, `triggered_reason` Take-Profit | `captured_at_utc` 2026-10-23T00:55:47Z | `exit_trigger_row.capture.json` | `79729713552c14990d4ac65ed96dcf18099fb7ffbb248962714a6a81efdb45a9` |

Sidecar sha256 values: `shadow_rows_two_cycles.capture.json` `b2482e13ba7459bcbc8e325dad6409427c16820161ff44d99e66c17aa1e1bdcd`; `exit_trigger_row.capture.json` `054eed1995705732b8965452f07083f255d79428a3155dc53165b0364d7fb38a`.

## Droplet commands

As the service user, in this order (the exit case reads the symphony the shadow case chose, from the same `out/`). The flag `--day 2026-10-21` is the PM's choice for the shadow case: on 2026-10-22 all 11 symphonies had triggered or exited, so no untriggered symphony existed that day.

```
runuser -u planetstopper -- /opt/planetstopper/.venv/bin/python -I /tmp/rt_capture/capture.py 01-c2-shadow-rows --db /opt/planetstopper/alphabot_state.db --out /tmp/rt_capture/out --day 2026-10-21
runuser -u planetstopper -- /opt/planetstopper/.venv/bin/python -I /tmp/rt_capture/capture.py 01-c2-exit-trigger --db /opt/planetstopper/alphabot_state.db --out /tmp/rt_capture/out
```

The script is `tests/fixtures/refresh_timing/capture.py` with `_cases_01.py`, both from branch `refresh-timing/fixtures`. The exit sidecar records `symphony_source: shadow-rows capture sidecar in --out`.

## Shape of R6, checked by reading the committed record

- Two cycles, oldest first, `20261021_1603` (rows stamped 2026-10-21T20:03:05Z, 16:03:02 ET) and `20261021_1604` (20:04:04Z, 16:04:01 ET), consecutive engine cycles one minute apart. Each carries exactly one row for each of the two symphonies (11 symphonies wrote each cycle; only the two are kept).
- Triggered symphony `nOyb55RMGVCKPiYXv7TI`: `is_post_trigger` 1, `trigger_id` 498, `position_epoch` `87cef415...`, in both cycles. Untriggered `5XjzXjdGnjh99MIsdM97`: `is_post_trigger` 0, `trigger_id` null, `position_epoch` `28eb287d...`, in both cycles.
- `previous_day_last_row` for each: the last row of 2026-10-20 (cycle `20261020_1604`), same epoch as the capture-day rows for each symphony.
- Row columns are the `database.record_shadow_observation` keywords (plus `id`), so the rows can be seeded through the real writer with their original stamps.

## Shape of R7, and the day judgement

R7 is exit id 509, 2026-10-22T11:33:08 ET, which is NOT on R6's day (2026-10-21). R6's triggered symphony's post-trigger rows carry `trigger_id` 498, the exit of 2026-10-21T15:02:07 ET; that row is not in this folder. Judged against plan 01's approach:

- R7 is read only by C5's scenarios (S-6.1, S-6.4, S-6.9), always with R8, the snapshot derived from R1. R1's entry for `nOyb55RMGVCKPiYXv7TI` carries `_last_trigger_id` 509 and the snapshot's `trading_day` is 2026-10-22. So R7 is the exit row R1 and R8 point at, on the snapshot's own day. For these scenarios id 509 is the right record.
- No C2 scenario reads R7. C2's R6 scenarios read R6 with R2 or R8 only.
- At the ref, the card guard-alpha read, `database.get_guard_alpha_by_symphony`, takes the newest `at_return` per symphony with no day filter, and no `app.py` or `analytics.py` code joins `shadow_history.trigger_id` to an `exit_triggers` row (checked by search at this branch's base). A scenario that seeds R6 and R7 together therefore needs no exit on R6's day.
- A scenario that does need the exit R6's rows point at (an assertion that `trigger_id` 498 resolves to an `exit_triggers` row) is not in the plan as written. If one is added, record id 498 is needed: it is not captured here, and no replacement was made up.

## Cautions for the cycles that use these records

- R6 is the 2026-10-21 state and R1 (hence R2 and R8) is the 2026-10-22 state, one trading day later. In R1 both R6 symphonies are `triggered: true` (`_last_trigger_id` 509 and 505) and `nOyb55RMGVCKPiYXv7TI` is in a new position epoch (R1 `35768ca0...`, R6 `87cef415...`; `5XjzXjdGnjh99MIsdM97` keeps `28eb287d...`). A scenario that seeds R6 with R2 or R8 as one state must derive R8 with `trading_day` 2026-10-21 (the plan already names that variant for some scenarios) and accept that the entries' `triggered` flags and epoch are the 10-22 values.
- R6's rows are stamped 16:03 and 16:04 ET, after the 16:00 close. Scenarios that read R6 as market-open cycles (for example a route at 10:43 ET) are on rows stamped later than that clock; they take the stamp from the row, which is what the AC asks, but they are not mid-session cycles.
- Seeding R7 through `database.record_exit_trigger` assigns a new autoincrement id, not 509. A test reads the id the writer returns and does not compare it with R8's `_last_trigger_id`.
