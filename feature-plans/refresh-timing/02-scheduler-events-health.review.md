# Approval packet: Scheduler, cycle event and health: the daemon keeps cycling, says what each cycle did, and reports when it cannot

## What is being built

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

## Acceptance criteria and scenarios

### AC-1: The event names its cycle, its outcome and whether data changed, and `/` skips unchanged events
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

Negative coverage: N1 S-1.9 | N2 n/a: the event refuses nothing | N3 S-1.10 | N4 n/a: `/api/events` stays behind the global auth hook, unchanged | N5 S-1.7 | N6 S-1.6, S-1.11 | N7 S-1.8 | N8 n/a: no edit state

### AC-2: The event stream opens within a second and carries a retry interval
Rule: When a client connects to `/api/events`, the daemon shall send the status line, the headers and a `retry:` field within 1 s, before waiting for the first event or heartbeat.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-2.1 | B | an authenticated client | it connects to `/api/events` | status 200, `Content-Type: text/event-stream` and a `retry: 5000` line arrive within 1 s |
| S-2.2 | N7 | a connected client with no events for 15 s | 15 s pass | a heartbeat comment arrives, as at the ref |
| S-2.3 | B | a client connected 0.5 s before a cycle ends | the event is emitted | the client receives it |
| S-2.4 | N4 | an unauthenticated client | it connects | 401 JSON, as at the ref |
| S-2.5 | N5 | three clients connected | one event is emitted | each receives exactly one copy |
| S-2.6 | N6 | a client disconnects | the next event is emitted | its queue is removed and the other clients still receive the event, as at the ref |

Negative coverage: N1 n/a: the route takes no input | N2 S-2.4 | N3 n/a: the stream always opens | N4 S-2.4 | N5 S-2.5 | N6 S-2.6 | N7 S-2.2 | N8 n/a: no edit state

### AC-3: The scheduler survives any job's exception and never waits on Composer
Rule: If any scheduled job raises or blocks on Composer, then the scheduler shall log it, keep running, and still spawn the engine within 2 s of the next `:00`.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-3.1 | N1 | `TRIGGER_TELEMETRY_RETENTION_DAYS=abc` in `.env` | the 02:00 slot runs | one ERROR line names the slot and the error; the engine is still spawned at the next `:00` |
| S-3.2 | N6 | Composer `total-stats` hangs for 30 s | the `:00` account refresh runs | the engine for that `:00` is spawned within 2 s of `:00`, and the next `:00`'s too |
| S-3.3 | N6 | a job raises on every run | 10 minutes pass | 10 engine spawns happen, one per `:00` |
| S-3.4 | N7 | every slot at the ref and model-unpin's 04:15 slot | the day passes | each runs at its time, as at the ref (Q-1) |
| S-3.5 | N6 | `threading.Thread.start` fails for the engine spawn | the `:00` passes | one ERROR line; the next `:00` spawns normally |
| S-3.6 | B | the scheduler is healthy | `/health` is read | `scheduler_last_tick_at` is under 2 s old (AC-4) |

Negative coverage: N1 S-3.1 | N2 n/a: the scheduler refuses nothing | N3 n/a: no computed value is shown | N4 n/a: a daemon thread with no caller | N5 S-3.3 | N6 S-3.2, S-3.5 | N7 S-3.4 | N8 n/a: no edit state

### AC-4: `/health` reports scheduler liveness and degradation
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

Negative coverage: N1 S-4.8, S-4.10 | N2 n/a: the route refuses nothing | N3 S-4.7 | N4 S-4.5 | N5 S-4.9 | N6 S-4.2, S-4.3, S-4.4 | N7 S-4.6 | N8 n/a: no edit state

### AC-5: At most one engine runs at a time, and a skipped minute is logged
Rule: If an engine run is still in progress when the next `:00` arrives, then the scheduler shall not spawn a second engine, shall log the skip with the running cycle's id and start time, and shall emit the skipped event (S-1.7); no engine run shall be ended by the daemon.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-5.1 | B | an engine run that lasts 75 s from 10:43:00 | 10:44:00 arrives | no second engine starts; one log line names the skipped minute, the running `cycle_id` and its start time; at 10:45:00 a new engine starts |
| S-5.2 | N5 | a run lasting 75 s | it ends | no `bot_state` save from it lands after a later run's save, because no later run started while it ran |
| S-5.3 | N6 | an engine hangs for 10 minutes | the minutes pass | it is not killed; each `:00` logs a skip; `/health` reports "engine_overrun" (S-4.3) |
| S-5.4 | N7 | runs that finish within the minute | a day passes | one spawn per `:00`, as at the ref |
| S-5.5 | N1 | the engine's own lock was left set by a killed run more than 60 s ago | the next `:00` arrives | the engine runs, as at the ref's stale-lock expiry |

Negative coverage: N1 S-5.5 | N2 n/a: a skip is not a refusal to a caller | N3 n/a: no computed value | N4 n/a: a daemon thread with no caller | N5 S-5.2 | N6 S-5.3 | N7 S-5.4 | N8 n/a: no edit state

### AC-6: The 09:30 start and the 15:53-15:59 blackout read as scheduled, never "Stale"
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

Negative coverage: N1 S-6.4 | N2 n/a: display only | N3 n/a: `engine_phase` is always computable from the clock and the calendar | N4 n/a: `/api/state` is behind the global auth hook, unchanged | N5 n/a: repeated polls give the same phase for the same moment | N6 S-6.3, S-6.5 | N7 S-6.6 | N8 n/a: no edit state

### AC-7: The routine post-cycle refresh no longer raises the stale chip; a real outage still does
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

Negative coverage: N1 S-7.5 | N2 n/a: nothing is refused | N3 S-7.4 | N4 n/a: behind the global auth hook, unchanged | N5 n/a: one flag per response | N6 S-7.2, S-7.3 | N7 S-7.6, S-7.9 | N8 n/a: no edit state

### AC-8: Account totals are fetched at daemon start
Rule: When the daemon starts, it shall start one account-totals refresh before the first `:00`, without delaying the first request it serves.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-8.1 | B | a fresh daemon with Composer reachable | the first `/api/state` after the startup refresh succeeds | `portfolio_strip.basis` is not "value_weighted" |
| S-8.2 | N6 | Composer unreachable at start | the first `/api/state` | the value-weighted chip shows, as at the ref, and the response is not delayed by the refresh |
| S-8.3 | N7 | the startup | it runs | the singleton check, the bot_state seed and the scheduler thread run in the ref's order; the model warm-up from model-unpin still runs |
| S-8.4 | N5 | a request arrives while the startup refresh is in flight | it is served | it is served at once from the empty cache (the Tier 2 chip) |

Negative coverage: N1 n/a: no input | N2 n/a: nothing is refused | N3 S-8.4 | N4 n/a: the startup has no caller | N5 S-8.4 | N6 S-8.2 | N7 S-8.3 | N8 n/a: no edit state

### AC-9: Every "today" the server computes is the ET trading date
Rule: When `/api/state` builds "today" for its symphony list, its waiting branch or its today's-trigger query, it shall use the ET trading date, never the host's date.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-9.1 | B | a UTC host at 20:30 EDT on D (00:30 UTC on D+1) | `GET /` and `/api/state` | each card's `trading_day` is D |
| S-9.2 | B | the same moment, a trigger at 15:30 EDT on D | `/api/state` | it counts as today's trigger for its card's status line |
| S-9.3 | N1 | a trigger at 23:30 EDT on D-1 (03:30 UTC on D) | `/api/state` at 10:00 EDT on D | it is not today's trigger |
| S-9.4 | N1 | the DST change weekend | `/api/state` on the Monday after | "today" is Monday's ET date |
| S-9.5 | N7 | 12:00 EDT, when UTC and ET share a date | `/api/state` | the values equal the ref's |
| S-9.6 | N7 | the daily slots | the day passes | each keeps its host-clock time (Q-1) |

Negative coverage: N1 S-9.3, S-9.4 | N2 n/a: nothing is refused | N3 n/a: the date is always computable | N4 n/a: behind the global auth hook, unchanged | N5 n/a: no concurrency effect | N6 n/a: no dependency | N7 S-9.5, S-9.6 | N8 n/a: no edit state

### AC-10: A daemon-side trajectory read sees the engine's newest rows
Rule: When a daemon-side caller reads a symphony's shadow trajectory after the engine has written a new `shadow_history` row for it, the read shall include that row.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-10.1 | B | the daemon read symphony S's trajectory at 10:43 | the engine process writes S's 10:44 row and the daemon reads again | the trajectory includes the 10:44 row |
| S-10.2 | N7 | the engine process's own reads | a cycle runs | they return what they return at the ref |
| S-10.3 | N3 | S has no rows | the daemon reads | the reader returns nothing, as at the ref |
| S-10.4 | N5 | two reads with no write between them | both run | they return the same trajectory |

Negative coverage: N1 n/a: the reader takes an id and a database path, unchanged | N2 n/a: nothing is refused | N3 S-10.3 | N4 n/a: an internal reader with no caller identity | N5 S-10.4 | N6 n/a: a failed read returns nothing, as at the ref | N7 S-10.2 | N8 n/a: no edit state

## Rule-by-entry-point matrix

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

## Narrowed mappings

R-1: Every timing issue the refresh-timing audit found is resolved; maps to AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-7, AC-8, AC-9, AC-10; left out: this plan's part is the findings G-64 assigns it
R-3: Every datum the poll carries reaches the screen without a reload; maps to AC-1, AC-3; left out: this plan keeps the producer and the event alive; the client writers are plans 03 and 04
R-4: Every number, chart, label and status on every page shows the newest data the engine has produced; maps to AC-1, AC-6; left out: the event's meaning and the header's scheduled-pause label
R-6: Every page recovers by itself after a daemon restart, a laptop sleep, a tab return or a network loss; maps to AC-2, AC-8; left out: the daemon side of a restart; the client side is plans 03 and 04
R-7: The suite covers the scheduler, `/api/events`, `/health` and the polled routes; maps to AC-1, AC-3, AC-4; left out: this plan's surfaces are the scheduler, the engine spawn, `/api/events`, `/health` and the date reads of `/api/state`
R-9: A whole trading day on `/` shows correct, current values; maps to AC-6, AC-9; left out: the 09:30 start, the blackout and the evening date reads
R-14: After a restart, live updates, actions and the header recover without a reload; maps to AC-2, AC-8; left out: the stream's opening and the account totals; the client's reconnect is plan 03's and the token is plan 04's
R-37: Server-sent events stay, fixed and not replaced; maps to AC-1, AC-2, NB-1; left out: the server's half; the client's reconnect is plan 03's
R-56: The scheduled 09:30 start and the 15:53-15:59 blackout never read as a fault; maps to AC-6; left out: the dated as-of before the first 09:30 cycle is plan 01 AC-4 (S-4.6)
R-59: On the UTC host every "today" the server uses is the ET trading date; maps to AC-9; left out: `/api/chart`'s held overlay is plan 03 AC-9 and `/api/history`'s "today" is plan 04 AC-3; the daily slots keep their host-clock times (Q-1)

## User tasks

Workflow (Dashboard `/` header): no workflow file: this repository has no `.claude/workflows/` directory at the ref.

### T-1: Watch the header through the open and the close
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Keep `/` open from 09:29 ET | a trading day | The header reads "Opening" from 09:30 until the first cycle completes, then "Live" | S-6.2, S-6.7 |
| 2 | Keep it open past 15:53 ET | the rebalance blackout | The header reads "Rebalance pause" with its tooltip, never "Stale" | S-6.1 |
| 3 | Leave it open in the evening | closed-market cycles every minute | No refetch for unchanged cycles; the header reads "Closed" | S-1.2, S-6.6 |

## Open questions

| Q-n | Question | Blocks (AC ids or "plan") | Proposed default | Answer |
|---|---|---|---|---|
| Q-1 | The daily slots (02:00, 03:00, 03:30, 03:45 and model-unpin's 04:15) run on the host's UTC clock, which is 22:00 to 00:15 ET. Move them to ET times? | AC-3, AC-9 | No: keep the host-clock times, which other plans already label (ux-overhaul SD-10); only "today" in routes becomes the ET date | default approved by the PM, [PM-ASSUMED] |
| Q-2 | What makes `/health` "degraded"? | AC-4 | No scheduler tick for 120 s; an engine running for more than 180 s; or the last 5 runs all failed | default approved by the PM, [PM-ASSUMED] |
| Q-3 | Should the daemon kill an engine that runs too long? | AC-5 | No: a killed engine can stop between an order and its state write; report the overrun and skip minutes until it exits | default approved by the PM, [PM-ASSUMED] |
| Q-4 | What does the header read in the scheduled pauses? | AC-6 | "Rebalance pause" with the tooltip "Scheduled pause before the close; stops resume at the close." from seven minutes before the close; "Opening" from 09:30 until the day's first completed cycle or 09:35, whichever is first | default approved by the PM, [PM-ASSUMED] |
| Q-5 | After how long without a successful account refresh should the stale chip show? | AC-7 | 90 s: one missed `:00` refresh plus the post-cycle one | default approved by the PM, [PM-ASSUMED] |

Sensitive: money (AC-3, AC-5). qa writes hidden scenarios for the named criteria after approval; they are not shown to the builders.
