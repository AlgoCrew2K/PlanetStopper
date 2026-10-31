# 03-c1 fixtures: provenance

Plan: dashboard-refresh, cycle 1 (the headline and Ann. Vol keep the picked window).

This folder holds no record files. Every body a test serves is the output of a real producer, generated at test time over a seeded database by `tests/dashboard/_index_js_harness.py` (`get_world`): `GET /` (the server render), `GET /api/state`, `GET /api/strip/<window>`, `GET /api/guard-alpha-summary?window=<window>`, `GET /api/hero-chart/<window>` and `GET /api/chart/<id>`, over the symphony records of `tests/fixtures/prod_droplet/bot_state_positions.json` (captured 2026-07-09 from the production droplet). No launcher capture was available to the RED author, so the plan's launcher records (FX-STRIP-30D, FX-STRIP-1Y, FX-STRIP-ALL) are replaced by the route's output over that seeded world; a recapture on the launcher is the PM's call. Nothing is typed from memory and no expected text is copied from a response: each expected string is the served body's field formatted with the client's own format (`fmt_pct`, `vol_texts` in `tests/dashboard/_refresh_timing_support.py`).

## Producer worlds

| World | Producer calls | Case it covers | Scenarios |
|---|---|---|---|
| `default` | the harness world: 160 daily shadow rows, today's intraday tape and one more cycle (`world["next"]`) | a normal open-market page, two polls with different stamps | S-1.1 to S-1.13 |
| `ytd-jan1` | explicit shadow dates 2026-12-29 to 2026-12-31, clock frozen at 2027-01-01 | the year-to-date window holds no row: `/api/hero-chart/ytd` answers an empty series | S-1.14 |

## Derived records (builders in `tests/dashboard/_refresh_timing_support.py`)

| Builder | Source record | Changed fields | Reason | Scenarios | PM sign-off |
|---|---|---|---|---|---|
| `with_stamp` | `world["state"]` or `world["next"]["state"]` | `data_as_of`, `meta.portfolio.data_as_of`, `portfolio_strip.data_as_of` set to the render's own stamp (equal-stamp poll) or to that stamp plus one minute (new-stamp poll) | the captured pair's stamps are derived from the host clock and can fall in one minute; the tests need a guaranteed equal stamp and a guaranteed later one | S-1.1 to S-1.13 | requested |
| `with_guard_alpha` | the new-stamp poll | `portfolio_strip.guard_alpha` and `meta.portfolio.guard_alpha` set to `null` | a poll that carries no guard alpha (weight sum zero, as the plan's S-1.3 states) | S-1.3 | requested |
| `with_poll_vol` | the equal-stamp and new-stamp polls | `meta.portfolio.vol_bot` and `meta.portfolio.vol_held` set to 0.0123 and 0.0456 | a poll whose own lifetime vol pair is complete, so a poll that writes the vol row would show a lifetime pair under a window whose strip has a null side | S-1.6, S-1.13 | requested |
| `strip_with` | `world["strips"]["30d"]` | `vol_bot` set to `null` (S-1.6); `vol_held` set to `null` (S-1.13) | the plan's null-vol 30d strip (the audit measured it null on production) | S-1.6, S-1.13 | requested |

## Event payload

`event_payload` builds `app.CycleRecord(...).event_payload()`, the real producer of the cycle-complete data (`cycle_id`, `started_at`, `completed_at`, `outcome`, `changed`); only `changed` varies.
