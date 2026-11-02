# Refresh timing and server state truth

> How the daemon decides which session a response describes, holds closed-market figures still, runs and bounds the engine, reports its own health, and tells the pages when something changed.

**Source:** `app.py` (symbols named below), `analytics.py`, `database.py`, `market_calendar.py`, `timing_log.py`, `deploy/caddy/Caddyfile`
**Last updated:** 2026-11-02

## Overview

The goal of the program: every number, chart, label and status shows the newest data the engine has produced, carries a dated stamp that tells the truth about its age, never changes while the market is closed, and recovers on its own after a daemon restart, a sleep, a tab return or a lost network. The engine's exit decisions, trade and liquidation requests, `LIVE_EXECUTION` and the once a minute cadence are unchanged. The one engine file change is a single line in the close-snapshot builder of `alpha_bot_execution.py` (the account-map loop keeps only dict values that carry `name`); `math_engine.py` has no diff. The LF-normalized content hashes of both files equal the pins in `tests/test_mdd_window_truth_zero_diff_guard.py` (computed with Python's `hashlib`; I did not run the test).

## Which session a response describes (`_SessionView`)

`app._session_view(state, market_state, now_et)` is the one place the window anchor is decided. Its `kind` is one of:
- `open`: the live state; the anchor is today's session.
- `frozen`: the market is closed and a close snapshot is stored; the anchor is the session that snapshot closes.
- `held`: a session has closed but its snapshot is not stored yet. The snapshot is built from the last committed state of that session, cut at its close (`_held_close_snapshot`), so the page never falls back to the session before. This is the state in the minutes after 16:00 until the engine stores the real snapshot.
- `live`: the market is closed and no snapshot exists; the live state is served.

`cutoff_anchor` is the day every window of a closed market counts back from. Every route that windows data takes it from here: `/api/state`, `/api/strip/<window>`, `/api/hero-chart/<window>`, `/api/performance`, `/api/guard-alpha-summary`, `/api/history/<days>`, `/api/exit-turnover`, and the client (which takes the anchor from the server and never reads its own clock for a cutoff). `analytics.window_cutoff(window, anchor)` is the pure resolver; `analytics._window_cutoff_date(window)` is the wall-clock form used only when no anchor applies. Tokens: `30d`, `60d`, `90d`, `125d`, `ytd`, `1y`, `all`, and `5y` (`_WINDOW_TRAILING_DAYS` has `5y` = 1825 days; `/api/performance` accepts `5y`; the strip and hero routes take the seven tokens in `_STRIP_WINDOW_TOKENS`). While closed or pre-market the anchor changes only when a new session starts, so a window edge no longer slides at 00:00 UTC.

`GET /api/exit-turnover` counts its 30, 90 and 365 day windows back from the close of the session the page's other figures belong to (`_session_close_moment(cutoff_anchor)`) while the market is closed, so its counts do not change at a UTC or ET date change on the same data. While a session is open it counts back from now. A failed read answers HTTP 500 with `{"error": "exit turnover unavailable", "symphony_id": ...}`, never zero counts; the Performance page draws a dash row for it.

## Held closed-market results (`_ClosedHold`)

While the market is closed the poll and the page are answered from results held per snapshot identity (`_closed_identity`: the snapshot's day and capture time, the anchor and the newest completed session; a built snapshot is named by its session alone). A second poll runs no `shadow_history` query and returns byte-identical JSON. The hold keeps `_HOLD_MAX_IDENTITIES = 4` identities and `_HOLD_MAX_RESULTS = 40` results per identity, is thread-safe, and returns a copy to every caller. Account totals are added to the held result on every request, so a refresh never makes it stale.

A build in which a part failed (a locked database, an unreadable reader) is answered but not held: `analytics.note_build_failure()` marks the build through `analytics.BUILD_FAILURES`, and `_ClosedHold.get_or_build` skips storing it. A wider-query fallback is used only on a missing column (`analytics._is_missing_epoch_column`), never on an arbitrary fault, so a fault on the day-filtered exit read can no longer return every exit ever recorded and hold it for the weekend.

A daemon thread builds the closed results ahead of the first poll (`_warm_closed_results`): at daemon start and then at most every `_CLOSED_WARMUP_EVERY_S = 30` seconds while the market is closed, for the poll view and for every window token's strip and hero chart. It is poked by every scheduler pass (`_poke_closed_warmup`) and never runs on the scheduler's thread. Within one request, repeated readers share results through `analytics.request_reads()` (`_read_once`).

## Account totals and the close latch

Cumulative, Today and account value of a closed market rest on Composer's account totals. The first successful read taken at or after the close snapshot's capture moment is latched for that snapshot (`_close_totals_latch_key`, `_close_totals_for`, `_close_totals_key`), and every figure shown until the next session is computed from it. The latch is stored in the state database, table `close_account_totals` (migration 039; `database.save_close_totals_latch` writes once with `INSERT OR IGNORE`, `database.load_close_totals_latch` reads; rows of sessions beyond the newest `_CLOSE_TOTALS_KEEP_DAYS = 10` are pruned), so a daemon restart finds the same figures. The stored close snapshot holds no account totals, so the first start after deploy latches the first refresh taken after the capture. `bot_state` is not used for this because the engine rewrites it every cycle and drops keys it does not know.

Account totals are fetched at daemon start (`_start_account_refresh("startup")`), at each minute (`:00 account refresh`) and after each engine run (`_cycle_refresh_body`), one refresh in flight at a time (`_ACCOUNT_REFRESH_FLIGHT`). A fetch is current for `_ACCOUNT_BASIS_STALE_AFTER_S = 90` seconds on the live branch before the stale chip shows. `_notify_cycle_complete` marks the totals cache stale and starts a refresh, so the routine post-cycle refresh no longer raises the stale chip while a real outage still does.

## The engine run slot

At most one engine process runs at a time (`_begin_engine_run` / `_end_engine_run`, `_ENGINE_RUN_LOCK`). A minute that finds the previous run alive writes a `cycle` timing line with `outcome=skipped`. The daemon starts the engine as a child process (`_start_engine_process`, `subprocess.Popen`) and ends a run that is overdue (`_engine_run_overdue`), by `terminate`, then `kill` after `_ENGINE_TERMINATE_GRACE_S = 10` seconds, freeing the slot only after the process has been reaped (`_ENGINE_REAP_AFTER_KILL_S = 30`):
- `earlier_date`: a run begun on an earlier ET date, on a trading day, from 09:25 ET (`_ENGINE_EARLIER_DATE_END_AT`). So a wedged Friday autotune cannot block Monday's open; the autotune is left alone until then.
- `session_bound`: a run begun before the close of a trading day and alive `_ENGINE_SESSION_BOUND_S = 600` seconds or more, unless forced.
A run the daemon ended has outcome `terminated` whatever status it left.

Cycle outcomes (`_classify_outcome`): `failed` (non-zero exit), `terminated`, `closed` (not a trading day, before 09:30, or after the post-mortem cutoff `market_calendar.post_mortem_cutoff`), `blackout` (from `market_calendar.rebalance_blackout_start`, `REBALANCE_BLACKOUT_MINUTES = 7` before the close, until the close), `eod`, `cycle`. `changed` on the event is computed from the two engine-written markers (`database.read_cycle_markers`: `last_successful_cycle_at` and the close snapshot's `trading_day`) and the day's post-mortem file signature; an unobservable state reads as changed.

The scheduler loop survives any job's exception: each scheduled job is wrapped by `_guard` (`_GuardedJobCall` logs the exception type and returns), and `_scheduler_tick` stamps its own liveness.

## The event stream and `/health`

`GET /api/events` (Server-Sent Events) opens with `retry: 5000` so headers and the reconnect delay reach the client at once, sends `: heartbeat` every `_SSE_HEARTBEAT_S = 15` seconds, and emits `event: cycle-complete` with data `{cycle_id, started_at, completed_at, outcome, changed}` (`CycleRecord.event_payload`). `cycle_id` is `<second>#<n>` (`_mint_cycle_id`). The dashboard (`static/index.js`) refetches on every event except one whose payload states `changed: false`; an unreadable payload refetches. The response carries `Cache-Control: no-cache` and `X-Accel-Buffering: no`.

`GET /health` (unauthenticated) and the `degraded_reasons` of `/api/state` and `/api/header-status` come from one helper chain (`_health_view`, `_health_degraded_reasons`, `_state_degraded_reasons`). Reasons:

| Reason | Condition |
|--------|-----------|
| `scheduler_stalled` | no scheduler tick for `_HEALTH_SCHEDULER_STALL_S = 120` seconds |
| `engine_overrun` | one run alive over `_HEALTH_ENGINE_OVERRUN_S = 180` seconds |
| `engine_failing` | the last `_HEALTH_FAILING_RUNS = 5` finished runs all `failed` |
| `engine_terminated` | the newest finished run was ended by the daemon; reads until the next run finishes |
| `no_cycle_since_open` | the market has been open at least 5 minutes (`_OPENING_PHASE_MINUTES`) and the last committed cycle is older than the open; clears on the next commit; never raised while closed |
| `close_snapshot_missing` | a session closed more than `_CLOSE_SNAPSHOT_GRACE` ago, the engine committed cycles that day, and the stored close snapshot is still an earlier session's |

The `/health` body is `{status ("ok" or "degraded"), degraded_reasons, scheduler_last_tick_at, engine_running_since, last_cycle {cycle_id, outcome, completed_at}, daemon_started_at, last_successful_cycle_at}`. `GET /api/header-status` (new, behind the auth gate, under 10 KB, one state read, no write connection) gives the other four pages the header's data: the same freshness fields as `/api/state` plus `market_state`, `engine_phase` (`_engine_phase`: `closed`, `blackout`, `opening` for the first five minutes while the first cycle has not committed, `running`), `next_run_seconds` and a `meta` subset (`mode`, `system_online`, `account`, `next_run`).

## The close snapshot

The engine's close snapshot used to nest every earlier snapshot. The account-map loop in `alpha_bot_execution.py` now keeps only entries that carry `name`, so the snapshot holds only symphonies. `database.repair_close_snapshot_chain()` runs once at daemon start, takes the engine lock (waiting up to 65 seconds, else `engine_busy`), writes a backup of the pre-repair row first, and flattens the stored chain; its result is one of `repaired`, `not_needed`, `not_byte_stable`, `backup_failed`, `engine_busy`, `write_failed`. The latest close's values are kept exactly.

## Symphonies no longer held

`analytics.no_longer_held_symphony_ids(state, ...)` is the one rule every surface asks (through `app._no_longer_held_ids`). A state entry is no longer held when it has `shadow_history` rows and its newest trading day is older than the second most recent completed session that holds rows for the state's symphonies. An entry with no rows (new, not gone) is never excluded; with fewer than two completed sessions of rows anywhere nothing is excluded; a read failure excludes nothing; an excluded entry returns on its own when rows resume. Excluded entries are removed from dashboard cards, section counts, every portfolio aggregate and weight, the hero series, the Advisor and Retirement rosters and `GET /api/guard-alpha-preconditions` (and its "replay unavailable N of M" count). `/settings` lists them dimmed under "No longer held". Nothing is deleted, and the engine does not prune them.

## Logging and transport

`timing_log.py` (see its page) writes one line per engine run, per Composer call and per request. `deploy/caddy/Caddyfile` is the tracked copy of the production site block: security headers (`Strict-Transport-Security`, `X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options DENY`, `Content-Security-Policy: frame-ancestors 'none'` and no other policy, because the pages use inline scripts and handlers) and `encode gzip zstd` for JSON, HTML, CSS, JavaScript, SVG and plain text only (no `text/*` wildcard, which could buffer `text/event-stream`). Chart.js 4.6.1 is served from `static/vendor/` (`chart.js-PROVENANCE.txt` holds source and digest), so no page loads a script from another host. `docs/DEPLOYMENT.md` holds the apply and rollback steps.

## Dates and stamps

Every freshness stamp names the data's ET date and time, never the request time (`app._format_data_stamp`, `_newest_observation_stamp`, `analytics.format_et_stamp`). A stamp for a previous session carries the suffix ` · previous session` (`_PREVIOUS_SESSION_SUFFIX`); the missing value is `--` (`_NO_STAMP`). Every "today" the server computes is the ET trading date.

## Known defects not fixed in this program
- After a settings reset the engine reads the literals 1.5 and 10 for `VWAP_BLEED_MULTIPLIER` and `VWAP_BLEED_TICKS` (`acc_params.get("VWAP_BLEED_MULTIPLIER", 1.5)` and `acc_params.get("VWAP_BLEED_TICKS", 10)` in `alpha_bot_execution.py`) instead of the `.env` values. The engine file is frozen.
- Cash Now (`static/index.js`, the `PSRequest.postAction('/api/sell_account', ...)` call) posts only `symphony_id`, and `POST /api/sell_account` requires `account_id`, `confirm_account_id` and `confirm_phrase`, so it answers 400. This predates this work; the route and the liquidation request are byte-identical.

## Unconfirmed
Measured request times and sizes after deploy, the real journal line timing, and whether the droplet's stored close snapshot is already flat are not confirmable from code. Settle them on the droplet: `journalctl -u planetstopper` for `cycle`, `composer` and `access` lines; `curl -s https://<host>/health`; and a read-only `sqlite3 -readonly alphabot_state.db "select length(data) from bot_state where id=1"`.
