# 01-c2 fixtures: provenance

Plan: server-state-truth, cycle 2 (R7 is also read by cycle 5). Batch FX-01.

The records in the two tables below (`shadow_rows_two_cycles.json`, `exit_trigger_row.json` and their sidecars) are captured, not derived. They come from the PM's read-only run of `capture.py` with `_cases_01.py` on the droplet, as the service user, against the live `/opt/planetstopper/alphabot_state.db`, opened `mode=ro` (plus `PRAGMA query_only`). The capture ran at deployed SHA `37c8e983` (`origin/main`). The records were copied to `.claude/review/refresh-timing-captures/20261023T005559Z/` in the main checkout (untracked) and committed here byte for byte; the sha256 values below were equal on the droplet and locally, and were re-checked after the copy into this folder.

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

## Derived records (written by cycle 2's RED)

`derive_records.py` (stdlib, `python -I derive_records.py`) writes these three from R1 (`../01-c1/nested_bot_state_row.json.gz`) and R6 (`shadow_rows_two_cycles.json`) and `serialize()`s them with sorted keys; a test can re-run `derive_all()` and compare byte for byte. Each changes only the fields named. PM sign-off as producer owner: recorded in each row below (granted to the closer for the rt-01 round).

| File | Case it covers | Source record | Changed fields | Reason | Scenarios | sha256 | PM sign-off |
|---|---|---|---|---|---|---|---|
| `snapshot_two_symphonies.json` (R8) | the close snapshot of R6's day, flat, holding R6's two symphonies | R1 `last_market_close_snapshot` | `trading_day` set to R6's day `2026-10-21` (R1's is `2026-10-22`); `accounts_map` narrowed to the two R6 symphonies under the real account key (the phantom "Unknown" entry dropped); `shadow_divergence.by_symphony` narrowed to the same two; every other field and every entry as captured | R6 is the 2026-10-21 state and R1 the 2026-10-22 state, so a scenario that seeds R6 with a snapshot needs the snapshot dated on R6's day | S-2.2, S-2.3, S-2.4, S-2.7, S-5.x, S-6.x later | `1bed26181ee3c2512b90590a1cdfb53de2986e12e7fec91e05959507658cf18f` | signed off (PM, granted to the closer for this round) |
| `live_state_two_cycles.json` | the live `bot_state` entries of the two symphonies as the engine saves them at the end of cycle M-1 and cycle M, plus the reserved keys | R1 symphony entries, R6 rows | each entry's `current_return` set to the value of the matching shadow row of that cycle; the untriggered symphony set `triggered: false`, `armed: true`; `last_successful_cycle_at` is R1's sub-second part and offset on the row's `ts_et`; reserved `date` set to R6's day | the engine writes `current_return` into `bot_state` and the shadow row in the same cycle; R6 shows the untriggered symphony was not triggered that day | S-2.x, S-7.x | `a8a8e7af26aa2eca9fed3fe4ef05689f2c84fadad0343dc11fda51554ebb1305` | signed off (PM, granted to the closer for this round) |
| `shadow_rows_cycle_m_moved.json` | the newer R6 cycle with moved figures, so a test can tell which cycle a figure came from | R6 newer cycle (`20261021_1604`) | `current_return` + `CYCLE_MOVE_PCT` (0.35 percentage points) on both symphonies; `shadow_return` + the same offset for the untriggered symphony only (the triggered symphony's bot series stays at its frozen exit value, as the engine writes it) | the two captured cycles ran after the close and carry identical return values | S-7.1 to S-7.6 | `e8a06554f419edcaeba5d825609e42ceeb90de1050c4520b82136379bab446a7` | signed off (PM, granted to the closer for this round) |

## Goldens (the reference commit's output)

Produced by `produce_golden_ref.py` (a pytest module not collected in this tree) run in a throwaway `git worktree add --detach <tmp> 37c8e983`, with `tests/app/_refresh_timing_state_world.py` and this folder's records copied in at the same relative paths and `total_stats.json` as already committed: `GOLDEN_OUT=<dir> python -m pytest -n0 tests/app/produce_golden_ref.py`. Each file is the whole `/api/state` body (`html` dropped) that 37c8e983 served for the seeded world of one market state (`seeded_world`, frozen clock per state: open 2026-10-21 16:04:20 ET, closed_frozen 18:00 ET, pre_market 2026-10-22 08:00 ET). Producer output, not a hand-built mock. The reference body shows the defects the plan fixes (meta and strip stamps differ, `frozen_at` has no date, no frozen `symphonies`); tests compare only the figures the plan keeps.

| File | Case it covers | Source | Scenarios | sha256 | PM sign-off |
|---|---|---|---|---|---|
| `golden_ref_api_state_open.json` | reference `/api/state`, market open | 37c8e983 producer, plus `mdd_lifetime` on each card (see "Revision: mdd_lifetime" below) and the headline `guard_alpha` (see "Revision: guard_alpha" below) | S-2.6, S-7.4, S-6.6, S-8.4 | `06dc797f257b27df48c955996a78ae66059dfbccae146fc22c2a695c1568f79f` | n/a (producer output) |
| `golden_ref_api_state_closed_frozen.json` | reference `/api/state`, after the close | 37c8e983 producer, headline `guard_alpha` revised (see "Revision: guard_alpha" below) | S-2.6, S-8.4 | `2fa1d45d6cfb90a7a1b64001b48b32bdf80bf04f6d0c2bbdd9b895641dcb5da9` | n/a (producer output) |
| `golden_ref_api_state_pre_market.json` | reference `/api/state`, pre-market | 37c8e983 producer, headline `guard_alpha` revised (see "Revision: guard_alpha" below) | S-2.6, S-8.4 | `c05c8cc16c0a2d54dfe72083fc5fada7e47b91c2d0f9c6220e5c65c1d2641005` | n/a (producer output) |

### Revision: mdd_lifetime

Refresh-timing 03 adds `mdd_lifetime` (Composer's lifetime max-drawdown scalar, `_mdd.if_held_lifetime`) to every card of `/api/state` `symphonies`. 37c8e983 serves no such field, so `golden_ref_api_state_open.json` carries it added. The steps, so the change can be checked:

1. The golden was regenerated at 37c8e983 as described above (the tree extracted with `git archive 37c8e983`, this folder's `01-c1` and `01-c2` records and `tests/app/_refresh_timing_state_world.py` copied in, `produce_golden_ref.py` run). Every value equals the committed golden except `daemon_started_at`, the process start time, which no test compares.
2. `produce_golden_ref.py` was run again in this tree at the refresh-timing 03 head. For the two cards of the open world, compared with step 1's: the only key added is `mdd_lifetime` (`5XjzXjdGnjh99MIsdM97` 27.18, `nOyb55RMGVCKPiYXv7TI` 27.07), plus `account`, which the tests drop before comparing; no value of any other key differs.
3. The committed golden is the 37c8e983 body with those two `mdd_lifetime` values added, written with `json.dumps(indent=2, sort_keys=True)` and a trailing newline. Nothing else in the file changed (two inserted lines).

The `mdd_lifetime` values are therefore this tree's producer output, not the reference commit's; the tests that read them (`test_api_state_frozen_cards.py`, `test_api_state_one_cycle.py`) check that the live branch changed no other figure. The `mdd_lifetime` figure itself is not independently pinned by a reference value: `test_dashboard_panels_payload.py` checks that every card carries it and its shape.

### Revision: guard_alpha

The UX review fixes redefine the dashboard headline `guard_alpha` as the compounded gap between the Simulated Bot return and the If-held return over the window (the gap between the hero chart's two lines), where 37c8e983 served the additive epoch sum over symphonies with a Composer cost basis. The figure the seeded world serves therefore changes, in the open, closed_frozen and pre_market goldens alike: `portfolio_strip.guard_alpha` and its copy `meta.portfolio.guard_alpha` (two lines per file, six in all) go from `-0.2774829692757424` (37c8e983 producer) to `-0.2766452878182668`. The steps, so the change can be checked:

1. `produce_golden_ref.py` was run in this tree at the head of the review fixes (`GOLDEN_OUT=<dir> python -m pytest -n0 tests/app/produce_golden_ref.py`, with the file copied to `tests/app/` for the run). All three branches serve `portfolio_strip.guard_alpha` `-0.2766452878182668`.
2. Compared with the committed goldens, every `portfolio_strip` key the tests compare (all but `data_as_of`) is equal except `guard_alpha`. The producer's other differences are the stamps, the frozen `symphonies`, the engine phase and the window fields that earlier plans added; the tests do not compare them against these files.
3. Cross-check on the open response: its own hero series ends `hist_bot` -0.3328 and `hist_held` -0.0562, a gap of -0.2766 (the series are rounded to four decimals), which is the new `guard_alpha`; the old value was the epoch sum, which no pair of points in the response gives. `test_api_state_single_computation.py::TestValuesMatchReference` asserts that equality on the served body.
4. The committed files were edited by replacing those six occurrences in place; nothing else in them changed (`git diff` shows six replaced lines), and the sha256 column above is of the edited files.
