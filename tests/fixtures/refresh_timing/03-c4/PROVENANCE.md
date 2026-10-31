# 03-c4 fixtures: provenance

Plan: dashboard-refresh, cycle 4 (frozen cards, verdicts and badges; session-true sparklines and dials).

This folder holds no record files. Every body a test serves is the output of a real producer generated at test time over a seeded database; a derived body changes one named field of such a body.

## Producer worlds

| World | Producer calls | Case it covers | Scenarios |
|---|---|---|---|
| `cards-open` | `tests/app/_refresh_timing_card_world.open_cards_world` (captured shadow rows of two symphonies, live state at the newer cycle, market open at 10:44 ET), then `GET /`, `GET /api/state`, `GET /api/hero-chart/<window>`, `GET /api/strip/<window>`, `GET /api/guard-alpha-summary`, `GET /api/chart/<id>` | a page open before the close | S-5.1, S-5.7 |
| `cards-frozen` | `closed_cards_world` plus the captured exit row (R7) through `database.record_exit_trigger`, clock 18:00 ET | the response after the close: `symphonies` and `guard_alpha` from plan 01 | S-5.1 to S-5.3, S-5.8 to S-5.10 |
| `cards-premarket` | as `cards-frozen`, clock 08:00 ET the next day | before the open | S-5.4 |
| `default`, `mixed` | the harness worlds (`_index_js_harness.py`) | the open-market page; one symphony with no intraday rows | S-9.3 to S-9.10 |
| `/api/chart/<id>` route | `tests/app/_refresh_timing_state_world.build_world`, shadow rows through `World.seed_rows`, the tape through `database.save_chart_history`, a clock on a UTC host (`now()` reads UTC, `now(tz)` converts) | the held overlay keyed to the ET date | S-9.1, S-9.2, S-9.8 |

## Derived records

| Builder | Source record | Changed fields | Reason | Scenarios | PM sign-off |
|---|---|---|---|---|---|
| `with_entry(state, id, guard_alpha=...)` | `cards-frozen` poll | one entry's `guard_alpha` in the card list and the state map set to 1e21, -1e21, 1e30, -1e30, 9.99e20, -9.99e20, 0.25, -0.25, 0.04, 0.0 or the string "n/a" | the server render's 1e21 bound and the missing or unusable figure need values no capture holds; the expected text is `app._first_paint_verdict`, the server render's own rule | S-5.9, S-5.10 | requested |
| `without_entry_key(state, id, "guard_alpha")`, `with_entry(..., guard_alpha=None)` | `cards-frozen` poll | the key removed, or set to null | an entry whose exit row is missing (plan 01 S-6.3 gives the absent case from the producer) | S-5.5, S-5.10 | requested |
| `with_state_only(state, id, armed=False, ...)` | `cards-frozen` poll | the state map's flags for one symphony cleared, the card list untouched | the state map and the card list disagreeing, so a badge that counts the map shows it | S-5.3 | requested |
| `with_entry(state, id, account=...)`, `for_account` | `cards-frozen` poll | one entry's account changed; the body reduced to the entries of one account as the route serves a picked account | the captured symphonies share one account; the id is invented (not a real account id) | S-5.6 | requested |
| `with_entry(state, id, armed=False, status="standby", ...)`, `with_card_order` | `cards-open` poll | an entry's flags cleared; the card list in another id order | a status change and the order the card list gives | S-5.7 | requested |
| `with_entry(state, id, mc_prob=63.0 / None)` | `default` poll | the armed symphony's reading set to 63 or null | state reading against the tape's last reading (58) | S-9.3, S-9.4 | requested |
| `_chart_with_last_mc`, `_chart_truncated` | `default` `/api/chart/<id>` | the last point's `mc_prob` set to 58 (or a value that prints unlike the render's); the tape shortened by five points | a tape reading that differs from the poll's; two refreshes' tapes that tell apart | S-9.4, S-9.6 | requested |
| `_chart_with_session` | `default` `/api/chart/<id>` | `session_date` and `et_date` added (the previous day and today; today and today; none) | the route's new fields; the captured body predates them | S-9.2, S-9.8 | requested |
| non-symphony key | `default` poll | `bot_state.post_mortem_run` and `state.post_mortem_run` set to a dict holding an `id` and no `name` | a state key that is not a symphony and carries an id | S-9.9 | requested |
| `seed_session` in `test_chart_route_session_date.py` | cycle `m` rows | the other symphony's `shadow_return` set to 7.77; `trading_day`, `ts_et`, `ts_utc`, `cycle_id` re-stamped by `retime_rows` | an overlay value that tells the two symphonies apart; the day under test | S-9.1, S-9.2, S-9.8 | requested |

## Clock instants chosen by the tests

| Instant | Meaning | Scenarios |
|---|---|---|
| 2026-10-22T00:30Z | 20:30 EDT on 21 October, the next UTC day | S-9.1 |
| 2026-10-22T05:00Z | 01:00 EDT on 22 October with the tape of 21 October | S-9.2 |
| 2026-10-22T13:31Z | 09:31 EDT with the day's first tape | S-9.8 |
