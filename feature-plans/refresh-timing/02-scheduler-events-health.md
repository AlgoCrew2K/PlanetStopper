# Feature: Scheduler, cycle event and health: the daemon keeps cycling, says what each cycle did, and reports when it cannot
Status: planned
Story: none: operator chat request on 2026-10-22, saved verbatim in feature-plans/refresh-timing/00-overview.source.md
Type: fix
Suite: refresh-timing 02
Repos: PlanetStopper
Base: main
Ref: PlanetStopper@37c8e983b64c6980c28199a3f9318486b60f3864
Sensitive: money (AC-3, AC-5)
Depends on: the model-unpin plan (branch `model-unpin/fixtures`, 291454eb) merged into this plan's tip before its PR, because its C9 cycle adds a daily 04:15 slot to `run_scheduler()` and a warm-up to the startup block, both of which this plan's scheduler and startup criteria cover. Overlap: AC-6 edits `static/chrome.js` `updateEngineStatus`, which ux-overhaul 04 rewrites; plan 04's tip merges this plan and resolves it

## Trigger
The operator wrote "I expect any and all timing issues to be resolved as well, I'm tired of the wonky updates and / or no updates to data on the poll" (G-1, saved in `feature-plans/refresh-timing/00-overview.source.md`). The refresh-timing audit at the ref (a review artifact outside the tracked tree, dated 2026-10-22, with the PM's droplet reads) assigned this plan the daemon's scheduler, event and health findings. Each was re-read at the ref for this spec:

1. TA-13 (event half): the scheduler spawns the engine every minute with no market gate (`app.py:1428`), and `trigger_alpha_bot()` emits `cycle-complete` in its `finally` for every exit (`app.py:776-789`): a closed-market sleep (`alpha_bot_execution.py:655-677`), the rebalance blackout (`:678-706`), an EOD already run (`:1029-1032`), an overlap skip (`:611-613`) and a failure. The payload is `data: {}` (`app.py:2495`). Each event makes every open `/` tab fetch all of `/api/state` (3.3 to 3.9 s and 5.67 MB after the close on the audit's copy), so about 1,000 refetches a day bring nothing new.
2. TA-31: the event generator blocks in `client_q.get(timeout=15)` before its first `yield` (`app.py:2494`), so the response's headers arrive 15 s after connect (measured 15.01 s), and the server sends no `retry:` field.
3. TA-15: the account refresh runs inline on the scheduler thread (`app.py:1429`) with a 30 s timeout bounded separately for connect and read (`app.py:591`, `:843`); `while True: schedule.run_pending(); time.sleep(1)` has no exception guard (`app.py:1441-1443`); the 02:00 retention slot parses `.env` integers with no guard (`app.py:796-803`); the thread is started once (`app.py:9016`). One exception ends every engine spawn, event and account refresh while `/health` still returns `"status": "ok"` (`app.py:392-411`, the status literal at `:408`). Production logged 9 account-total read timeouts and 5 HTTP 503s on 2026-10-22.
4. TA-16: `acquire_lock` treats a lock older than 60 s as stale with a read and an update outside one transaction (`database.py:293-307`), `release_lock` is unconditional (`database.py:310-318`), and `subprocess.run` has no timeout (`app.py:785`), so a cycle longer than 60 s either skips the next minute or runs beside it, and the last writer of `bot_state` wins.
5. TA-17: from 15:53 the engine runs post-mortem stage 1 and returns before stamping a cycle (`alpha_bot_execution.py:678-706`), while `get_market_state` reports `open` until the close (`market_calendar.py:107-139`); the header turns "Stale" with "Positions may not be actively guarded." when the last cycle is over two minutes old (`static/chrome.js:225-232`), so it reads as a fault every afternoon. At 09:30 the first cycle saves the day-rollover wipe early (`alpha_bot_execution.py:731-746`) and stamps only at its end (`:2007`, `:2048`).
6. TA-12: `_notify_cycle_complete()` masks the account totals (`app.py:758`) and then starts a refresh (`app.py:763`); the event-triggered `/api/state` reads the masked cache and falls back to the last good totals with `account_basis_stale` true (`app.py:2340-2353`), so the "Stale account data" chip can flash after every cycle, around the clock.
7. TA-33: the account totals live in process memory (`app.py:577`, `:586-591`) and startup runs no refresh (`app.py:8996-9021`), so for up to a minute after each restart the strip falls to the value-weighted floor.
8. TA-39: on the UTC host (G-4), the live and waiting branches build "today" from the naive host date (`app.py:3098`, `:3264`, `:3290`), and the daily slots run at host-clock times (`app.py:1430-1440`).
9. TA-42: `database._shadow_cr_cache` (`database.py:3000`) is cleared only in `record_shadow_observation` (`database.py:3027-3029`), which runs in the engine process, so a daemon-side reader (`analytics.py:662-756`) would serve one trajectory all session; no production caller exists at the ref (`git grep`), so the defect is latent.

## Summary
The operator's dashboard keeps updating through every scheduler fault, never runs two engines at once, refetches only when a cycle produced new data, opens its event stream at once, and never reads a scheduled pause or a routine refresh as a fault, while `/health` says when the daemon is degraded.
- The event names its cycle, its outcome and whether data changed; `/` skips unchanged events (AC-1).
- The event stream opens within a second and carries a retry interval (AC-2).
- The scheduler survives any job's exception and never waits on Composer (AC-3).
- `/health` reports scheduler liveness and degradation (AC-4).
- At most one engine runs at a time; a skipped minute is logged (AC-5).
- The 09:30 start and the 15:53-15:59 blackout read as scheduled (AC-6).
- The post-cycle refresh no longer raises the stale chip; a real outage still does (AC-7).
- Account totals are fetched at daemon start (AC-8).
- Every "today" the server computes is the ET trading date (AC-9).
- A daemon-side trajectory read sees the engine's newest rows (AC-10).

## Requirements as Given
[G-1] I expect any and all timing issues to be resolved as well, I'm tired of the wonky updates and / or no updates to data on the poll. 00-overview.source.md:6
[G-4] (Production fact) Host timezone is Etc/UTC, with NTP synchronized. 00-overview.source.md:12
[G-7] (Production fact) One daemon runs (planetstopper.service, `.venv/bin/python app.py`) with one engine child. 00-overview.source.md:14
[G-11] (Production fact) 4 daemon restarts on 2026-10-22. 00-overview.source.md:16
[G-12] PM Step-2 answers ([PM-ASSUMED]; the operator is not available for questions; project directive: record assumptions, do not stop) 00-overview.source.md:18
[G-13] (Building 1) Every number, chart, label and status on every page shows the newest data the engine has produced. 00-overview.source.md:21
[G-14] (Building 1) It updates without a reload, never reverts or flickers when no new data arrived, and never shows stale or wrong-day data without a dated, honest cue. 00-overview.source.md:21
[G-15] (Building 1) It recovers by itself after a daemon restart, laptop sleep, tab return or network loss. 00-overview.source.md:21
[G-17] (Building 2) The server side is the minute scheduler, the engine's close snapshot, `/api/state`, `/api/events`, `/health` and the other GET routes the pages poll. 00-overview.source.md:22
[G-19] (Building 3) Flows: keep `/` open through a whole trading day: pre-market, the 09:30 open, intraday, the 15:53-15:59 blackout, the 16:00 close, the evening and overnight. 00-overview.source.md:23
[G-22] (Building 3) Expected: values change within one cycle of new data; the operator's selections persist through polls; every freshness stamp carries a date and its true age; after a restart, live updates, actions and the header recover without a reload. 00-overview.source.md:26
[G-26] (Building 4) A Composer timeout or 503 shows a stale marker with its as-of. 00-overview.source.md:30
[G-27] (Building 4) An engine cycle longer than 60 s never runs two engines at once and never lets an older writer win. 00-overview.source.md:31
[G-28] (Building 4) An exception in the scheduler loop never stops updates, and `/health` reports the degradation. 00-overview.source.md:32
[G-29] (Building 4) After a restart, the event stream reconnects and the CSRF token refreshes. 00-overview.source.md:33
[G-39] (Building 5) The `cycle-complete` event payload may gain a cycle id and timestamp so the client can skip unchanged data. 00-overview.source.md:40
[G-40] (Building 5) `/health` gains scheduler liveness. 00-overview.source.md:41
[G-51] (Not building 1) Server-sent events stay; they are fixed, not replaced. 00-overview.source.md:51
[G-55] (Not building 2) the engine's once-a-minute cadence, around the clock; 00-overview.source.md:56
[G-56] (Not building 2) the 1-minute execution path gets no blocking I/O; 00-overview.source.md:57
[G-59] (Not building 3) Items the audit marks COVERED stay with their plans (ux-overhaul 05, C3 round 2, 04 AC-8/AC-9, 03 AC-2). 00-overview.source.md:60
[G-64] 02-scheduler-events-health. TA-12, TA-13 event half (fire with a cycle id; the client can skip unchanged data), TA-15, TA-16, TA-17, TA-31, TA-33, TA-39, TA-42. 00-overview.source.md:65

## Requirement Inventory
These are the overview's R ids. Overview items R-15 (the workflow line), R-83 and R-84 (the Base and Depends on headers) and R-88 (the Earlier criteria section) are met by those parts of this plan.

| R-n | Requirement item | Source (G ids) | Maps to (AC ids, or NB-n) | Scope (full, or narrowed: what is left out) |
|---|---|---|---|---|
| R-1 | Every timing issue the refresh-timing audit found is resolved | G-1 | AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-7, AC-8, AC-9, AC-10 | narrowed: this plan's part is the findings G-64 assigns it |
| R-3 | Every datum the poll carries reaches the screen without a reload | G-1, G-14 | AC-1, AC-3 | narrowed: this plan keeps the producer and the event alive; the client writers are plans 03 and 04 |
| R-4 | Every number, chart, label and status on every page shows the newest data the engine has produced | G-13 | AC-1, AC-6 | narrowed: the event's meaning and the header's scheduled-pause label |
| R-6 | Every page recovers by itself after a daemon restart, a laptop sleep, a tab return or a network loss | G-15, G-11 | AC-2, AC-8 | narrowed: the daemon side of a restart; the client side is plans 03 and 04 |
| R-7 | The suite covers the scheduler, `/api/events`, `/health` and the polled routes | G-17 | AC-1, AC-3, AC-4 | narrowed: this plan's surfaces are the scheduler, the engine spawn, `/api/events`, `/health` and the date reads of `/api/state` |
| R-9 | A whole trading day on `/` shows correct, current values | G-19 | AC-6, AC-9 | narrowed: the 09:30 start, the blackout and the evening date reads |
| R-12 | Values change within one engine cycle of new data | G-22 | AC-1 | full |
| R-14 | After a restart, live updates, actions and the header recover without a reload | G-22, G-29 | AC-2, AC-8 | narrowed: the stream's opening and the account totals; the client's reconnect is plan 03's and the token is plan 04's |
| R-17 | A Composer timeout or 503 shows a stale marker with its as-of | G-26 | AC-7 | full |
| R-18 | An engine cycle longer than 60 s never runs two engines at once and never lets an older writer win | G-27, G-7, G-64 | AC-5 | full |
| R-19 | An exception in the scheduler loop never stops updates, and a slow account refresh never delays the engine spawn | G-28, G-64 | AC-3 | full |
| R-28 | The `cycle-complete` event names its cycle, its time and whether data changed, and an open `/` tab skips unchanged data | G-39, G-64 | AC-1 | full |
| R-29 | `/health` reports scheduler liveness and its degradation | G-40, G-28 | AC-4 | full |
| R-37 | Server-sent events stay, fixed and not replaced | G-51 | AC-1, AC-2, NB-1 | narrowed: the server's half; the client's reconnect is plan 03's |
| R-41 | The engine's once-a-minute cadence, around the clock, stays | G-55 | NB-2 | full |
| R-42 | The 1-minute execution path gets no blocking I/O | G-56 | NB-3 | full |
| R-45 | Findings the audit marks covered stay with their plans | G-59 | NB-4 | full |
| R-55 | The account-basis stale chip is not raised by the routine post-cycle refresh | G-64 | AC-7 | full |
| R-56 | The scheduled 09:30 start and the 15:53-15:59 blackout never read as a fault | G-64 | AC-6 | narrowed: the dated as-of before the first 09:30 cycle is plan 01 AC-4 (S-4.6) |
| R-57 | The event stream opens at once on connect | G-64 | AC-2 | full |
| R-58 | After a restart the account totals are filled before the first engine minute | G-64 | AC-8 | full |
| R-59 | On the UTC host every "today" the server uses is the ET trading date | G-64, G-4 | AC-9 | narrowed: `/api/chart`'s held overlay is plan 03 AC-9 and `/api/history`'s "today" is plan 04 AC-3; the daily slots keep their host-clock times (Q-1) |
| R-60 | A daemon-side reader of the shadow trajectory sees the engine's newest rows | G-64 | AC-10 | full |
| R-89 | Open questions are answered by recorded defaults, never by stopping | G-12 | AC-4, AC-5, AC-6, AC-7, AC-9 | full |

## User Tasks
Workflow (Dashboard `/` header): no workflow file: this repository has no `.claude/workflows/` directory at the ref.

### T-1: Watch the header through the open and the close
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Keep `/` open from 09:29 ET | a trading day | The header reads "Opening" from 09:30 until the first cycle completes, then "Live" | S-6.2, S-6.7 |
| 2 | Keep it open past 15:53 ET | the rebalance blackout | The header reads "Rebalance pause" with its tooltip, never "Stale" | S-6.1 |
| 3 | Leave it open in the evening | closed-market cycles every minute | No refetch for unchanged cycles; the header reads "Closed" | S-1.2, S-6.6 |

## Earlier criteria on the same surface
Searched every `feature-plans/*.md` (with the `.completed.md` and `.obsolete.md` families and the suite folders, the UX overhaul suite read from branch `ux-overhaul/fixtures` at 340b9d03 and the model-unpin plan from branch `model-unpin/fixtures` at 291454eb), `DECISIONS.md`, `docs/generated/app.md` and `docs/DEPLOYMENT.md` for `cycle-complete`, `/api/events`, `/health`, `run_scheduler`, `acquire_lock`, `mark_stale`, `_refresh_account_totals` and `EXECUTION_START_TIME`.

| Earlier criterion (plan and AC) | Surface | Disposition (kept, changed or contradicted) | Why |
|---|---|---|---|
| `feature-plans/dashboard-realtime-push.md` AC-1 (the daemon publishes `cycle-complete` within about 1 s of the engine's exit, without blocking the scheduler) | Cycle event | kept | AC-1 keeps the timing and the non-blocking fan-out (S-1.8) |
| `feature-plans/dashboard-realtime-push.md` AC-2 (`GET /api/events` streams, auth-gated, 15 s heartbeats) | `/api/events` | changed | Auth and heartbeats stay; the stream now opens with a retry line before its first wait (AC-2) |
| `feature-plans/dashboard-realtime-push.md` AC-3 (the client calls `loadState()` on every event) | `/` event handler | changed | An event that reports no change triggers no refetch (S-1.2) |
| `feature-plans/dashboard-realtime-push.md` AC-4 and DE-SSE-PUSH-001 "`mark_stale()` over `dict.clear()`" (mask the account totals at every cycle so the first poll after it is fresh) | Account totals | changed | The mask stays, but a masked read whose last success is under 90 s old no longer sets `account_basis_stale` (AC-7) |
| `feature-plans/dashboard-realtime-push.md` AC-5 and AC-6 (the poll is the fallback; nothing blocks or raises in `trigger_alpha_bot()` or the scheduler loop) | Scheduler | kept | AC-3 makes AC-6 hold for every job, not only the event |
| `feature-plans/fix-ops-cluster.md` AC-2 (F-005: `GET /health` unauthenticated, honest fields, no secrets; its edge case "a degraded daemon returns honestly, no fabricated ok") and DE-OPS-CLUSTER-001 | `/health` | changed | At the ref it always returns "ok" (`app.py:408`); AC-4 adds scheduler liveness and a degraded status, keeps every existing field, no auth and no secrets |
| `feature-plans/eod-today-change-account-basis.completed.md` AC-3 (Tier 1 last-good with the stale stamp, Tier 2 value-weighted floor) and AC-4 (bounded refresh timeout, last-success stamp) and DE-EOD-BASIS-001 | Account totals | kept | AC-7 changes only when Tier 1 is flagged stale; AC-8 fills Tier 1 at start |
| `feature-plans/audit-bl4-account-basis-honesty-render.md` AC-6 and DE-AUDIT-BL4-001 (timeout raised to 30 s, accepting that the scheduler thread can block for 30 s) | Scheduler | changed | AC-3 moves the refresh off the scheduler thread, so the 30 s timeout no longer delays the `:00` spawn |
| `feature-plans/dm-dashboard-market-mode.completed.md` AC-DM.5.1 (the engine runs every minute, around the clock) | Engine cadence | kept | NB-2 |
| model-unpin plan (branch `model-unpin/fixtures`) AC-6 S-6.7, S-6.14 and Q-8 (a page render never waits on the Models API; after a restart the badges resolve with no operator action) and its C9 cycle (a daily 04:15 warm-up slot and a startup warm-up, `model-unpin/c9` at 0d771793) | Scheduler, startup | kept | AC-3's guard covers the 04:15 slot; AC-8's startup refresh runs off the startup path, so neither the warm-up nor a page render waits on it |
| ux-overhaul plan 05 NB-4 (the 30 s poll and the event stream exactly as at the ref) | Cycle event | changed | The event payload changes (AC-1); plan 03 records the client half of the supersession |
| ux-overhaul suite SD-10 (the droplet clock is UTC; the nightly retirement run is labelled 03:45 UTC) | Daily slots | kept | Q-1: the slots keep their host-clock times |
| `feature-plans/retirement-visibility.md` (Run now refused while the market is open; the 03:45 tick is never market-gated) | Retirement tick | kept | AC-3 changes only how a job's exception is contained |
| ux-overhaul plan 04 (branch `ux-overhaul/fixtures`) AC-1 (every page header conforms to the dashboard's) | Header engine status | kept | AC-6 changes only the engine-status label's text and tooltip in the pause windows |

## Acceptance Criteria (Building)
### AC-1: The event names its cycle, its outcome and whether data changed, and `/` skips unchanged events
Requirement items: R-1, R-3, R-4, R-7, R-12, R-28, R-37
Rule: When an engine run ends or the scheduler skips a minute, the daemon shall emit `cycle-complete` with `cycle_id`, `started_at`, `completed_at`, `outcome` and `changed` (Contract Changes), and when an open `/` tab receives an event whose `changed` is false, it shall send no `/api/state` and no `/api/guard-alpha-summary` request for it.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-1.1 | B | the market is open and a cycle advances `last_successful_cycle_at` | the engine exits 0 | one `cycle-complete` reaches every connected client with `outcome` "cycle", `changed` true and a `cycle_id` no earlier event carried; `/` sends one `/api/state` request |
| S-1.2 | B | 20:00 ET, the engine takes its closed-market path | the engine exits | the event carries `outcome` "closed" and `changed` false; `/` sends no `/api/state` and no `/api/guard-alpha-summary` request for it |
| S-1.3 | B | 15:53 ET, the engine runs post-mortem stage 1 and writes today's post-mortem file | the engine exits | `outcome` "blackout" and `changed` true, because the post-mortem the $-saved panel reads changed |
| S-1.4 | B | 15:55 ET, a blackout minute that writes nothing | the engine exits | `outcome` "blackout" and `changed` false |
| S-1.5 | B | 16:00 ET, the engine writes the close snapshot | the engine exits | `outcome` "eod" and `changed` true |
| S-1.6 | N6 | the engine exits non-zero before any write the dashboard reads | the run ends | `outcome` "failed" and `changed` false; the event is still emitted |
| S-1.11 | N6 | at 16:00 the EOD step saves the close snapshot and `last_successful_cycle_at` (`alpha_bot_execution.py:1166-1168`) and then raises in reporting (`:1171`) | the engine exits non-zero | `outcome` "failed" and `changed` true, because `changed` is read from the observed state, never from the exit code; `/` refetches |
| S-1.7 | N5 | the previous engine is still running at `:00` (AC-5) | the scheduler skips the minute | one event with `outcome` "skipped" and `changed` false |
| S-1.8 | N7 | any run | the event is emitted | the fan-out stays non-blocking and the event reaches clients within 1 s of the engine's exit, as at the ref |
| S-1.9 | N1 | an event whose data cannot be parsed as JSON (a client connected to an older daemon) | `/` receives it | `/` treats it as `changed` true and sends one `/api/state`, as at the ref |
| S-1.10 | N3 | an event with no `changed` key | `/` receives it | as S-1.9 |

Negative coverage: N1 S-1.9 · N2 n/a: the event refuses nothing · N3 S-1.10 · N4 n/a: `/api/events` stays behind the global auth hook, unchanged · N5 S-1.7 · N6 S-1.6, S-1.11 · N7 S-1.8 · N8 n/a: no edit state
Questions: none

### AC-2: The event stream opens within a second and carries a retry interval
Requirement items: R-1, R-6, R-14, R-37, R-57
Rule: When a client connects to `/api/events`, the daemon shall send the status line, the headers and a `retry:` field within 1 s, before waiting for the first event or heartbeat.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-2.1 | B | an authenticated client | it connects to `/api/events` | status 200, `Content-Type: text/event-stream` and a `retry: 5000` line arrive within 1 s |
| S-2.2 | N7 | a connected client with no events for 15 s | 15 s pass | a heartbeat comment arrives, as at the ref |
| S-2.3 | B | a client connected 0.5 s before a cycle ends | the event is emitted | the client receives it |
| S-2.4 | N4 | an unauthenticated client | it connects | 401 JSON, as at the ref |
| S-2.5 | N5 | three clients connected | one event is emitted | each receives exactly one copy |
| S-2.6 | N6 | a client disconnects | the next event is emitted | its queue is removed and the other clients still receive the event, as at the ref |

Negative coverage: N1 n/a: the route takes no input · N2 S-2.4 · N3 n/a: the stream always opens · N4 S-2.4 · N5 S-2.5 · N6 S-2.6 · N7 S-2.2 · N8 n/a: no edit state
Questions: none

### AC-3: The scheduler survives any job's exception and never waits on Composer
Requirement items: R-1, R-3, R-7, R-19
Rule: If any scheduled job raises or blocks on Composer, then the scheduler shall log it, keep running, and still spawn the engine within 2 s of the next `:00`.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-3.1 | N1 | `TRIGGER_TELEMETRY_RETENTION_DAYS=abc` in `.env` | the 02:00 slot runs | one ERROR line names the slot and the error; the engine is still spawned at the next `:00` |
| S-3.2 | N6 | Composer `total-stats` hangs for 30 s | the `:00` account refresh runs | the engine for that `:00` is spawned within 2 s of `:00`, and the next `:00`'s too |
| S-3.3 | N6 | a job raises on every run | 10 minutes pass | 10 engine spawns happen, one per `:00` |
| S-3.4 | N7 | every slot at the ref and model-unpin's 04:15 slot | the day passes | each runs at its time, as at the ref (Q-1) |
| S-3.5 | N6 | `threading.Thread.start` fails for the engine spawn | the `:00` passes | one ERROR line; the next `:00` spawns normally |
| S-3.6 | B | the scheduler is healthy | `/health` is read | `scheduler_last_tick_at` is under 2 s old (AC-4) |

Negative coverage: N1 S-3.1 · N2 n/a: the scheduler refuses nothing · N3 n/a: no computed value is shown · N4 n/a: a daemon thread with no caller · N5 S-3.3 · N6 S-3.2, S-3.5 · N7 S-3.4 · N8 n/a: no edit state
Questions: Q-1

### AC-4: `/health` reports scheduler liveness and degradation
Requirement items: R-1, R-7, R-29, R-89
Rule: When `/health` is requested, it shall return the existing fields plus the scheduler's last tick, whether an engine is running and since when, and the last run's `cycle_id`, `outcome` and `completed_at`, and its `status` shall read "degraded", with each reason listed, whenever the scheduler has not ticked for 120 s, an engine has run for more than 180 s, or the last 5 runs all failed.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-4.1 | B | a healthy daemon | `GET /health` | 200, `status` "ok", `scheduler_last_tick_at` under 2 s old, `engine_running_since` null between runs, `last_cycle` with the newest run's id, outcome and time, plus `daemon_started_at` and `last_successful_cycle_at` as at the ref |
| S-4.2 | N6 | the scheduler thread has died | `GET /health` 130 s later | `status` "degraded" with reason "scheduler_stalled" |
| S-4.3 | N6 | an engine has run for 200 s | `GET /health` | `status` "degraded" with reason "engine_overrun" and `engine_running_since` set |
| S-4.4 | N6 | the last 5 runs exited non-zero | `GET /health` | `status` "degraded" with reason "engine_failing" |
| S-4.5 | N4 | no session | `GET /health` | 200 with the same body; still exempt from the auth gate, as at the ref |
| S-4.6 | N7 | any state | `GET /health` | the body carries no credential, account id, `.env` value or path |
| S-4.7 | N3 | the daemon has just started and no run has completed | `GET /health` | `last_cycle` is null and `status` is "ok" |
| S-4.8 | N1 | 20:00 ET, the last cycle that stamped `last_successful_cycle_at` was at 16:00 | `GET /health` | `status` "ok": closed-market runs are healthy runs |
| S-4.9 | N5 | two reasons hold at once | `GET /health` | both reasons are listed |
| S-4.10 | N1 | a Friday after the close, the post-close autotune running inside the engine for more than 180 s | `GET /health` | `status` "degraded" with reason "engine_overrun"; this is a true report and is accepted |

Negative coverage: N1 S-4.8, S-4.10 · N2 n/a: the route refuses nothing · N3 S-4.7 · N4 S-4.5 · N5 S-4.9 · N6 S-4.2, S-4.3, S-4.4 · N7 S-4.6 · N8 n/a: no edit state
Questions: Q-2

### AC-5: At most one engine runs at a time, and a skipped minute is logged
Requirement items: R-1, R-18, R-89
Rule: If an engine run is still in progress when the next `:00` arrives, then the scheduler shall not spawn a second engine, shall log the skip with the running cycle's id and start time, and shall emit the skipped event (S-1.7); no engine run shall be ended by the daemon.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-5.1 | B | an engine run that lasts 75 s from 10:43:00 | 10:44:00 arrives | no second engine starts; one log line names the skipped minute, the running `cycle_id` and its start time; at 10:45:00 a new engine starts |
| S-5.2 | N5 | a run lasting 75 s | it ends | no `bot_state` save from it lands after a later run's save, because no later run started while it ran |
| S-5.3 | N6 | an engine hangs for 10 minutes | the minutes pass | it is not killed; each `:00` logs a skip; `/health` reports "engine_overrun" (S-4.3) |
| S-5.4 | N7 | runs that finish within the minute | a day passes | one spawn per `:00`, as at the ref |
| S-5.5 | N1 | the engine's own lock was left set by a killed run more than 60 s ago | the next `:00` arrives | the engine runs, as at the ref's stale-lock expiry |

Negative coverage: N1 S-5.5 · N2 n/a: a skip is not a refusal to a caller · N3 n/a: no computed value · N4 n/a: a daemon thread with no caller · N5 S-5.2 · N6 S-5.3 · N7 S-5.4 · N8 n/a: no edit state
Questions: Q-3

### AC-6: The 09:30 start and the 15:53-15:59 blackout read as scheduled, never "Stale"
Requirement items: R-1, R-4, R-9, R-56, R-89
Rule: When `/api/state` is served during the rebalance blackout (seven minutes before the session close until the close) or between the 09:30 open and the day's first completed cycle (at most until 09:35), it shall carry `engine_phase` "blackout" or "opening", and the header shall read "Rebalance pause" or "Opening" with a tooltip that says the pause is scheduled, instead of "Stale".

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-6.1 | B | 15:56 ET on a trading day, the last cycle stamped 15:52 | `/` polls | `engine_phase` is "blackout"; the header reads "Rebalance pause" with the tooltip "Scheduled pause before the close; stops resume at the close."; it never reads "Stale" |
| S-6.2 | B | 09:30:20 ET, the day's first cycle still running, the last stamp from the prior session | `/` polls | `engine_phase` is "opening"; the header reads "Opening" |
| S-6.3 | N6 | 09:36 ET and no cycle has completed today | `/` polls | `engine_phase` is "running" and the header reads "Stale", as at the ref |
| S-6.4 | N1 | a half-day closing at 13:00 | `/` polls at 12:56 | `engine_phase` is "blackout" |
| S-6.5 | N6 | 11:00 ET and the last cycle is 3 minutes old | `/` polls | the header reads "Stale" with its tooltip, as at the ref |
| S-6.6 | N7 | the market is closed | `/` polls | the header reads "Closed", as at the ref |
| S-6.7 | B | 15:52:30 ET, a cycle stamped at 15:52 | `/` polls | `engine_phase` is "running" and the header reads "Live" |
| S-6.8 | B | 15:56 ET on a trading day | `GET /` renders | the server-rendered header reads "Rebalance pause", never "Live" or "Stale" |

Negative coverage: N1 S-6.4 · N2 n/a: display only · N3 n/a: `engine_phase` is always computable from the clock and the calendar · N4 n/a: `/api/state` is behind the global auth hook, unchanged · N5 n/a: repeated polls give the same phase for the same moment · N6 S-6.3, S-6.5 · N7 S-6.6 · N8 n/a: no edit state
Questions: Q-4

### AC-7: The routine post-cycle refresh no longer raises the stale chip; a real outage still does
Requirement items: R-1, R-17, R-55, R-89
Rule: When the live branch of `/api/state`, or the server render of `/` while the market is open, reads the account totals while a refresh is in flight and the last successful refresh is under 90 s old, it shall not mark the account basis stale; if the last success is 90 s old or more, then it shall set `account_basis_stale` with the last success's as-of, as at the ref; after the close the stale flag is plan 01's (AC-5, S-5.5).

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-7.1 | B | the last successful refresh was 40 s ago and the post-cycle refresh is in flight | the event-triggered `/api/state` is computed | `portfolio_strip.account_basis_stale` is absent and the figures use the last good totals |
| S-7.2 | N6 | Composer answered 503 at the last two refreshes and the last success was 130 s ago | `/api/state` is computed | `account_basis_stale` is true and `account_basis_as_of` names the last success |
| S-7.3 | N6 | Composer times out at 30 s | the next poll lands 95 s after the last success | the stale chip shows with its as-of |
| S-7.4 | N3 | no refresh has ever succeeded in this process | `/api/state` is computed | `basis` reads "value_weighted", as at the ref (Tier 2) |
| S-7.5 | N1 | the last success is exactly 90 s old | `/api/state` is computed | `account_basis_stale` is true |
| S-7.6 | N7 | the refresh succeeds | the next `/api/state` | the fresh totals are used, as at the ref |
| S-7.7 | B | the flush-resync refresh is in flight and the last success was 40 s ago | `/api/state` is computed in market hours | `account_basis_stale` is absent |
| S-7.8 | B | the market is open, the post-cycle refresh in flight, the last success 40 s ago | `GET /` renders | the server-rendered rows carry no stale chip |
| S-7.9 | N7 | after the close, the frozen branch | `/api/state` is computed | the stale flag follows plan 01 AC-5 and S-5.5, not this criterion's 90 s grace |

Negative coverage: N1 S-7.5 · N2 n/a: nothing is refused · N3 S-7.4 · N4 n/a: behind the global auth hook, unchanged · N5 n/a: one flag per response · N6 S-7.2, S-7.3 · N7 S-7.6, S-7.9 · N8 n/a: no edit state
Questions: Q-5

### AC-8: Account totals are fetched at daemon start
Requirement items: R-1, R-6, R-14, R-58
Rule: When the daemon starts, it shall start one account-totals refresh before the first `:00`, without delaying the first request it serves.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-8.1 | B | a fresh daemon with Composer reachable | the first `/api/state` after the startup refresh succeeds | `portfolio_strip.basis` is not "value_weighted" |
| S-8.2 | N6 | Composer unreachable at start | the first `/api/state` | the value-weighted chip shows, as at the ref, and the response is not delayed by the refresh |
| S-8.3 | N7 | the startup | it runs | the singleton check, the bot_state seed and the scheduler thread run in the ref's order; the model warm-up from model-unpin still runs |
| S-8.4 | N5 | a request arrives while the startup refresh is in flight | it is served | it is served at once from the empty cache (the Tier 2 chip) |

Negative coverage: N1 n/a: no input · N2 n/a: nothing is refused · N3 S-8.4 · N4 n/a: the startup has no caller · N5 S-8.4 · N6 S-8.2 · N7 S-8.3 · N8 n/a: no edit state
Questions: none

### AC-9: Every "today" the server computes is the ET trading date
Requirement items: R-1, R-9, R-59, R-89
Rule: When `/api/state` builds "today" for its symphony list, its waiting branch or its today's-trigger query, it shall use the ET trading date, never the host's date.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-9.1 | B | a UTC host at 20:30 EDT on D (00:30 UTC on D+1) | `GET /` and `/api/state` | each card's `trading_day` is D |
| S-9.2 | B | the same moment, a trigger at 15:30 EDT on D | `/api/state` | it counts as today's trigger for its card's status line |
| S-9.3 | N1 | a trigger at 23:30 EDT on D-1 (03:30 UTC on D) | `/api/state` at 10:00 EDT on D | it is not today's trigger |
| S-9.4 | N1 | the DST change weekend | `/api/state` on the Monday after | "today" is Monday's ET date |
| S-9.5 | N7 | 12:00 EDT, when UTC and ET share a date | `/api/state` | the values equal the ref's |
| S-9.6 | N7 | the daily slots | the day passes | each keeps its host-clock time (Q-1) |

Negative coverage: N1 S-9.3, S-9.4 · N2 n/a: nothing is refused · N3 n/a: the date is always computable · N4 n/a: behind the global auth hook, unchanged · N5 n/a: no concurrency effect · N6 n/a: no dependency · N7 S-9.5, S-9.6 · N8 n/a: no edit state
Questions: Q-1

### AC-10: A daemon-side trajectory read sees the engine's newest rows
Requirement items: R-1, R-60
Rule: When a daemon-side caller reads a symphony's shadow trajectory after the engine has written a new `shadow_history` row for it, the read shall include that row.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-10.1 | B | the daemon read symphony S's trajectory at 10:43 | the engine process writes S's 10:44 row and the daemon reads again | the trajectory includes the 10:44 row |
| S-10.2 | N7 | the engine process's own reads | a cycle runs | they return what they return at the ref |
| S-10.3 | N3 | S has no rows | the daemon reads | the reader returns nothing, as at the ref |
| S-10.4 | N5 | two reads with no write between them | both run | they return the same trajectory |

Negative coverage: N1 n/a: the reader takes an id and a database path, unchanged · N2 n/a: nothing is refused · N3 S-10.3 · N4 n/a: an internal reader with no caller identity · N5 S-10.4 · N6 n/a: a failed read returns nothing, as at the ref · N7 S-10.2 · N8 n/a: no edit state
Questions: none

## Rule-by-Entry-Point Matrix
| Rule | `:00` engine spawn | 02:00 retention slot | 03:00, 03:30, 03:45 and 04:15 slots | `:00` account refresh | post-cycle refresh thread | daemon start |
|---|---|---|---|---|---|---|
| AC-3 a job's failure never stops the scheduler | S-3.5 | S-3.1 | S-3.4 | S-3.2 | n/a: runs on its own thread, never the scheduler's | n/a: before the loop starts |
| AC-5 one engine at a time | S-5.1 | n/a: spawns no engine | n/a: spawns no engine | n/a: spawns no engine | n/a: spawns no engine | n/a: the first spawn is the first `:00` |
| AC-7 stale only after 90 s | n/a: reads no totals | n/a: reads no totals | n/a: reads no totals | S-7.3 | S-7.1 | n/a: at start no refresh has succeeded, so Tier 2 applies (AC-8) |

| Rule | Flush-resync refresh thread | `GET /api/state` live branch | `GET /` | `GET /api/events` | `GET /health` |
|---|---|---|---|---|---|
| AC-1 the event names its cycle and change | n/a: emits no event | n/a: emits no event | n/a: emits no event | S-1.1, S-1.2 | n/a: reports, never emits (AC-4) |
| AC-4 health reports liveness and degradation | n/a: not a health read | n/a: not a health read | n/a: not a health read | n/a: not a health read | S-4.1, S-4.2, S-4.10 |
| AC-6 scheduled pauses read as scheduled | n/a: no header | S-6.1, S-6.2 | S-6.8 | n/a: carries no header state | n/a: carries no header state |
| AC-7 stale only after 90 s | S-7.7 | S-7.1 | S-7.8 | n/a: reads no totals | n/a: reads no totals |
| AC-9 every "today" is the ET date | n/a: computes no date | S-9.2, S-9.3 | S-9.1 | n/a: computes no date | n/a: computes no date |

## Not Building
Non-goals:
- NB-1: Replacing server-sent events, or changing the event's name `cycle-complete` (R-37; overview NB-4).
- NB-5: Killing an engine that runs long; a killed engine can stop between an order and its state write, so an overrun is reported (AC-4) and waited out (AC-5) (approved by the PM, [PM-ASSUMED]; Q-3).
- NB-6: Moving the daily slots to ET times (approved by the PM, [PM-ASSUMED]; Q-1).
- NB-7: Reducing the two account-total calls a minute to one; the call count stays as at the ref (approved by the PM, [PM-ASSUMED]).

Behaviour that stays exactly as it is:
- NB-2: The engine's once-a-minute spawn at every `:00`, around the clock, and the engine file (overview NB-5, NB-8).
- NB-3: No blocking I/O is added to the 1-minute execution path; every new read in this plan runs on the daemon's request or scheduler threads (overview NB-9).

Deferred:
- NB-4: The covered findings stay with their plans (overview NB-12).

Protected surfaces consumed, not changed:
- NB-8: `LIVE_EXECUTION`, `POST /api/sell_account`, `POST /api/trigger` and the no-trade boundary tests (overview NB-14).

## Defect Classes
| DC-n | Class (what goes wrong) | Trigger (B or N1-N8) | Sibling search (tool, query, scope) | Scenarios | Raised in review as |
|---|---|---|---|---|---|
| DC-1 | A scheduled job run inline on the scheduler thread, or with no exception guard | N6 | grep `schedule.every` in `app.py` and check each job's thread and guard, including model-unpin's 04:15 slot | S-3.1, S-3.2, S-3.4 | - |
| DC-2 | A liveness report that cannot report its own failure | N6 | grep every `"status": "ok"` and liveness field in `app.py` | S-4.2, S-4.3 | - |
| DC-3 | A host-local date where the ET trading date is meant (overview DC-3) | N1 | grep `datetime.now()` with no zone, `date.today()` and `.strftime("%Y-%m-%d")` in `app.py`, `analytics.py`, `database.py` and `reporting.py` | S-9.1, S-9.3 | - |
| DC-4 | A process-local cache invalidated only by another process | N5 | grep module-level dicts used as caches in `database.py`, `analytics.py` and `app.py` and their invalidation call sites | S-10.1 | - |
| DC-5 | An event or log field that two plans produce with different values (overview SD-3) | B | grep `cycle_id` and `outcome` in `app.py` once plan 05 lands | S-1.1, S-4.1 | - |
| DC-6 | A changed field or label not carried to every consumer, tests included (overview DC-8) | B | grep `cycle-complete`, `"status": "ok"`, `Stale` and `engine_phase` in `static/`, `templates/`, `app.py` and `tests/` | S-1.9, S-6.1 | - |

## Worked Example
At 20:00 ET the engine's closed-market run exits 0 after 2.1 s. The daemon emits `event: cycle-complete` with `{"cycle_id": "2026-10-22T20:00:00-04:00#1", "started_at": "2026-10-22T20:00:00-04:00", "completed_at": "2026-10-22T20:00:02-04:00", "outcome": "closed", "changed": false}`; an open `/` tab sends no request for it. `/health` reads `status` "ok", `scheduler_last_tick_at` one second old and `last_cycle.outcome` "closed" (S-1.2, S-4.8).

Failure case: at 02:00 UTC the retention slot raises on `TRIGGER_TELEMETRY_RETENTION_DAYS=abc`. One ERROR line names the slot; the engine is spawned at 02:01:00 as usual; `/health` stays "ok" because the scheduler kept ticking (S-3.1). If instead the scheduler thread had died, `/health` would read "degraded" with "scheduler_stalled" 120 s later (S-4.2).

## Contract Changes and Consumers
- `cycle-complete` event data, before `{}`, after `{"cycle_id": str, "started_at": str, "completed_at": str, "outcome": str, "changed": bool}`. `cycle_id` is unique per spawn or skip and stable across the event, `/health` and plan 05's log line (overview SD-3); the times are ISO 8601 with an offset; `outcome` is one of "cycle" (the engine ran its data cycle), "closed" (its closed-market path), "blackout" (the rebalance blackout), "eod" (the close step), "skipped" (the scheduler did not spawn because the previous engine was running) and "failed" (a non-zero exit); `changed` is true when the run advanced `last_successful_cycle_at`, wrote the close snapshot or wrote today's post-mortem file. Backward compatible for the event name; consumers: `static/index.js:1600` (`/` reads the data from this plan on), plan 05's log line.
- `/api/events`: first bytes are `retry: 5000` before the first wait. Backward compatible.
- `/health`: adds `scheduler_last_tick_at`, `engine_running_since`, `last_cycle` (`cycle_id`, `outcome`, `completed_at`) and `degraded_reasons`; `status` can now read "degraded". Existing fields unchanged. Consumers: none in the repository besides `tests/app/test_health_endpoint.py`; an external monitor that matched "ok" now sees "degraded" when it applies.
- `/api/state`: adds `engine_phase` ("opening", "running", "blackout", "closed"). Additive. Consumer: `static/chrome.js` `updateEngineStatus`.
- `portfolio_strip.account_basis_stale`: no longer set when a masked read's last success is under 90 s old (AC-7). Consumers: `static/index.js` chip renderers (unchanged code).

## Security Considerations
- Input validation and injection: no new input; `.env` integers that fail to parse are logged, never executed.
- Authorization and access control: `/health` stays unauthenticated and `/api/events` stays behind the auth hook (S-2.4, S-4.5).
- Data exposure: `/health`'s new fields hold times, a cycle id and an outcome only, never a credential, account id or path (S-4.6).
- Client-side rendering: the header label is set through `textContent`.
- Abuse or rate limits: unchanged events stop roughly 1,000 full `/api/state` refetches a day per open tab; account-total calls per minute stay at two (NB-7).
- Money (Sensitive): AC-3 and AC-5 decide whether the engine that runs trailing stops is spawned or skipped; S-5.3 and NB-5 rule out killing an engine mid-run, and S-5.4 pins one spawn per `:00`.

## Design-System Mapping
| Component | Element | Required primitive or token | Notes |
|---|---|---|---|
| Header engine status | "Rebalance pause" and "Opening" labels | the existing `#engine-status-label` with `--studio-ink-dim`, as the "Closed" state uses | text and tooltip only |

## Definition of Done (Verification)
| AC | Scenarios | Test level | Real-entrypoint test |
|---|---|---|---|
| AC-1 | S-1.1, S-1.2, S-1.3, S-1.4, S-1.5, S-1.6, S-1.7, S-1.8, S-1.9, S-1.10, S-1.11 | `trigger_alpha_bot()` with a stubbed engine per exit path and a frozen clock; the `/` event handler under node | DevTools on the droplet: events across 15:52 to 16:01 and one at 20:00 |
| AC-2 | S-2.1, S-2.2, S-2.3, S-2.4, S-2.5, S-2.6 | a raw-socket test against the Flask test server timing the first bytes; S-2.4's test sets `app._auth_check_enabled` back to True, because `tests/conftest.py:297` turns it off for every test | `curl -N` against the droplet's `/api/events` with a session cookie |
| AC-3 | S-3.1, S-3.2, S-3.3, S-3.4, S-3.5, S-3.6 | `run_scheduler()` with a fake clock and faulting jobs | the droplet journal across a retention slot |
| AC-4 | S-4.1, S-4.2, S-4.3, S-4.4, S-4.5, S-4.6, S-4.7, S-4.8, S-4.9, S-4.10 | route tests with seeded scheduler and run state; S-4.5's test sets `app._auth_check_enabled` back to True so the exemption is really exercised | `curl` of the droplet's `/health` with no cookie |
| AC-5 | S-5.1, S-5.2, S-5.3, S-5.4, S-5.5 | the scheduler with a stub engine that sleeps 75 s | the journal's skip line on a forced long run on a staging copy |
| AC-6 | S-6.1, S-6.2, S-6.3, S-6.4, S-6.5, S-6.6, S-6.7, S-6.8 | route tests at frozen clock times; `updateEngineStatus` under node | the droplet header at 09:31 and 15:56 |
| AC-7 | S-7.1, S-7.2, S-7.3, S-7.4, S-7.5, S-7.6, S-7.7, S-7.8, S-7.9 | strip computation with a masked cache and stubbed last-success times | the post-cycle poll's chip on the droplet over 15 minutes |
| AC-8 | S-8.1, S-8.2, S-8.3, S-8.4 | the startup block with a stubbed refresh | the first `/api/state` after a droplet restart |
| AC-9 | S-9.1, S-9.2, S-9.3, S-9.4, S-9.5, S-9.6 | route tests with a frozen UTC clock | `/api/state` on the droplet at 20:30 EDT |
| AC-10 | S-10.1, S-10.2, S-10.3, S-10.4 | the reader in one process with a row inserted from a second connection | none: no production caller at the ref, so the unit test is the entry point |

Gates: `ruff format --check`; `ruff check`; a scoped pytest run of `tests/app`, `tests/realtime_push`, `tests/dashboard`, `tests/database` and `tests/execution`, at most 4 workers, never `-n auto`; CI green on the exact SHA; `/review`; then the PM's live functional check on the droplet (the real-entrypoint column).
Fixture provenance (SD-7): `/api/state` payloads and `bot_state` rows are captured from the production state database copy the audit used; engine exit paths are exercised with a stub process whose exit codes and state writes mirror the engine's real paths at the cited lines, signed off by the PM.

## Known-Unconfirmed and Standing Risk
- The engine cycle duration on the droplet is unmeasured, so how often AC-5 skips a minute is unknown until plan 05's cycle log runs. Read-only check: `grep -c "Overlap Detected" /opt/planetstopper/alphabot_daemon.log`.
- Composer `total-stats` latency is unmeasured, so how often AC-7's 90 s threshold is crossed is unknown. Read-only check: ten timed GETs of the route from the droplet (plan 05 AC-3 logs it afterwards).
- `EXECUTION_START_TIME` on the droplet is unread; the 09:30 "opening" window's length depends on it. Read-only check: `grep EXECUTION_START_TIME /opt/planetstopper/.env`.
- Whether browsers honour `retry:` after a 502 from Caddy is browser behaviour; plan 03 re-arms the stream itself.

## Open Questions
| Q-n | Question | Blocks (AC ids or "plan") | Proposed default | Answer |
|---|---|---|---|---|
| Q-1 | The daily slots (02:00, 03:00, 03:30, 03:45 and model-unpin's 04:15) run on the host's UTC clock, which is 22:00 to 00:15 ET. Move them to ET times? | AC-3, AC-9 | No: keep the host-clock times, which other plans already label (ux-overhaul SD-10); only "today" in routes becomes the ET date | default approved by the PM, [PM-ASSUMED] |
| Q-2 | What makes `/health` "degraded"? | AC-4 | No scheduler tick for 120 s; an engine running for more than 180 s; or the last 5 runs all failed | default approved by the PM, [PM-ASSUMED] |
| Q-3 | Should the daemon kill an engine that runs too long? | AC-5 | No: a killed engine can stop between an order and its state write; report the overrun and skip minutes until it exits | default approved by the PM, [PM-ASSUMED] |
| Q-4 | What does the header read in the scheduled pauses? | AC-6 | "Rebalance pause" with the tooltip "Scheduled pause before the close; stops resume at the close." from seven minutes before the close; "Opening" from 09:30 until the day's first completed cycle or 09:35, whichever is first | default approved by the PM, [PM-ASSUMED] |
| Q-5 | After how long without a successful account refresh should the stale chip show? | AC-7 | 90 s: one missed `:00` refresh plus the post-cycle one | default approved by the PM, [PM-ASSUMED] |

## Decisions
| ID | Decision | Rationale |
|---|---|---|
| DEC-1 | The daemon derives `outcome` and `changed` from what it can observe (the clock and market calendar, the exit code, `last_successful_cycle_at`, the close snapshot's trading day and today's post-mortem file), never from engine output | The engine file may change only in the close-snapshot builder (overview NB-5) |
| DEC-2 | `retry: 5000` | Five seconds rides through a restart's 502 window without a reconnect storm; plan 03's own re-arm handles a closed stream |
| PM-1 | AC-7's 90 s grace applies to the live branch only; the after-close stale flag is plan 01's AC-5 and S-5.5 | PM amendment from the Gate-2 approach, [PM-ASSUMED] |
| PM-2 | `/health` reading "degraded" with "engine_overrun" during the Friday post-close autotune is accepted as a true report (S-4.10) | PM amendment from the Gate-2 approach, [PM-ASSUMED] |
| PM-3 | The matrix adds the flush-resync refresh thread, the live branch of `GET /api/state`, `GET /`, `GET /api/events` and `GET /health` | PM amendment from the Gate-2 approach, [PM-ASSUMED] |

## Docs to Update
- `docs/generated/app.md` (`trigger_alpha_bot()`, `_notify_cycle_complete()`, `run_scheduler()`, `/health`, `/api/events`, the startup block), `docs/generated/static_chrome_js.md` if present, `docs/generated/INDEX.md`.
- `README.md` where it describes `/health` and the live updates.
- A `DECISIONS.md` entry appended at ship by the doc writer.
- A drafted `.claude/CLAUDE.md` Key Files row update for `app.py`, for PM approval.

## Follow-Ups
- NB-5 if an engine overrun is ever observed in plan 05's cycle log.
- NB-7: one account-total call a minute, if Composer rate limits bite.
