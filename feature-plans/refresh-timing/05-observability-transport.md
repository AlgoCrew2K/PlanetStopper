# Feature: Observability and transport: timing is readable from the logs, and polls travel compressed
Status: planned
Story: none: operator chat request on 2026-10-22, saved verbatim in feature-plans/refresh-timing/00-overview.source.md
Type: chore
Suite: refresh-timing 05
Repos: PlanetStopper
Base: main
Ref: PlanetStopper@37c8e983b64c6980c28199a3f9318486b60f3864
Sensitive: security (AC-3, AC-4)
Depends on: the model-unpin plan (branch `model-unpin/fixtures`, 291454eb) merged into this plan's tip before its PR, because both edit `app.py`; plan 02 merged into this plan's tip before the AC-2 cycle, because the cycle log line carries plan 02's `cycle_id` and `outcome` (overview SD-3); the PM applies the Caddy change on the droplet at deploy (Configuration and Enablement)

## Trigger
The operator wrote "I expect any and all timing issues to be resolved as well, I'm tired of the wonky updates and / or no updates to data on the poll" (G-1, saved in `feature-plans/refresh-timing/00-overview.source.md`). The refresh-timing audit at the ref (a review artifact outside the tracked tree, dated 2026-10-22, with the PM's droplet reads) found that no production timing claim can be checked, and that the poll's body crosses the network uncompressed. Each cause was re-read at the ref for this spec:

1. TA-30: `app.py:9001` replaces `sys.stdout` with an `io.TextIOWrapper` that has no `line_buffering`, so the daemon's prints reach the journal in 8 KB blocks; a probe showed `PYTHONUNBUFFERED` cannot fix it, and the PM saw every line printed from 18:05 to 21:58 UTC arrive at 21:59 (G-10). The trigger and failure prints have no `flush` (`app.py:777`, `:787`). The engine's own output goes to `alphabot_daemon.log` through `subprocess.run(..., stdout=log_fh, stderr=log_fh)` (`app.py:784-785`), and the engine rewraps its stdout the same way (`alpha_bot_execution.py:12`), so its lines arrive when each run ends. Werkzeug's request log is set to ERROR (`app.py:415-416`), and the Caddyfile has no `log` (G-5). Nothing logs a cycle's start, end or duration, a Composer call's latency, or a request's time.
2. TA-13 (payload half): the production Caddyfile is a bare `reverse_proxy localhost:8090` with no `encode` (G-5, G-6; documented the same at `docs/DEPLOYMENT.md:113-118`). Measured on the audit's copy: an after-close `/api/state` of 5,666,593 B compresses to 845,419 B with gzip; each open tab moves about 17 MB a minute after the close.
3. The audit's section 8 lists what could not be measured and the read-only check that settles each: droplet route times, the engine cycle duration, Composer `total-stats` latency, whether events reach the browser through Caddy across a restart, how often the close snapshot is missed, how far the verdict figure drifts per cycle, how often `/api/state` answers non-OK, open-branch behaviour, `EXECUTION_START_TIME` on the droplet, the shape of the newest-request guard on `loadState` (ux-overhaul 05 C3 round 2) and live-browser rendering, animation and hidden-tab throttling (G-68).

## Summary
The PM can read every timing fact the suite depends on from the droplet's journal and `alphabot_daemon.log`, and each open tab's poll travels compressed, so the suite's live verification rests on measured numbers, not on inference.
- Daemon output reaches the journal line by line (AC-1).
- One log line per engine run, with its id, times, duration and outcome (AC-2).
- One log line per Composer call the daemon makes, with its latency (AC-3).
- One access line per request, with its status, time and size, and no secret (AC-4).
- Responses travel gzip-compressed through Caddy while events still stream (AC-5).
- Each section 8 unknown is measured after deploy and recorded (AC-6).

## Requirements as Given
[G-1] I expect any and all timing issues to be resolved as well, I'm tired of the wonky updates and / or no updates to data on the poll. 00-overview.source.md:6
[G-5] (Production fact) /etc/caddy/Caddyfile is exactly `104-248-7-101.sslip.io { reverse_proxy localhost:8090 }` (Caddy v2.11.4). 00-overview.source.md:13
[G-6] (Production fact) It has no `encode` and no `flush_interval`. 00-overview.source.md:13
[G-9] (Production fact) Its in-text log clock is UTC. 00-overview.source.md:14
[G-10] (Production fact) Stdout is block-buffered: lines printed from 18:05 to 21:58 UTC all reached the journal at 21:59. 00-overview.source.md:15
[G-12] PM Step-2 answers ([PM-ASSUMED]; the operator is not available for questions; project directive: record assumptions, do not stop) 00-overview.source.md:18
[G-17] (Building 2) The server side is the minute scheduler, the engine's close snapshot, `/api/state`, `/api/events`, `/health` and the other GET routes the pages poll. 00-overview.source.md:22
[G-18] (Building 2) Diagnosis uses the droplet journal and `alphabot_daemon.log`. 00-overview.source.md:22
[G-41] (Building 5) Log lines for cycle duration, Composer latency and requests. 00-overview.source.md:42
[G-44] (Building 5) Caddy response compression (deployment config and docs/DEPLOYMENT.md). 00-overview.source.md:44
[G-59] (Not building 3) Items the audit marks COVERED stay with their plans (ux-overhaul 05, C3 round 2, 04 AC-8/AC-9, 03 AC-2). 00-overview.source.md:60
[G-68] 05-observability-transport. TA-30 (stdout line buffering despite the re-wrap at app.py around :9001; cycle-duration, Composer-latency and request logs), TA-13 payload half (Caddy `encode` and payload size), and the section 8 production measurements as this suite's live verification. 00-overview.source.md:68

[G-92] (PM amendment) PLAN 05: New scenario, ruled in: the existing ERROR lines at app.py:905-909 (exception text plus traceback; a requests error message carries the URL with the account id) and app.py:6407 log the exception type name only at WARNING or above, with the detail at DEBUG. 00-overview.source.md:96

## Requirement Inventory
These are the overview's R ids. Overview items R-83 and R-84 (the Base and Depends on headers) and R-88 (the Earlier criteria section) are met by those parts of this plan.

| R-n | Requirement item | Source (G ids) | Maps to (AC ids, or NB-n) | Scope (full, or narrowed: what is left out) |
|---|---|---|---|---|
| R-1 | Every timing issue the refresh-timing audit found is resolved | G-1 | AC-1, AC-2, AC-3, AC-4, AC-5, AC-6 | narrowed: this plan's part is TA-30, TA-13's payload half and the section 8 measurements |
| R-7 | The suite covers the scheduler, `/api/state`, `/api/events` and the polled routes | G-17 | AC-2, AC-4, AC-5 | narrowed: their logging and transport |
| R-8 | Production timing is diagnosable from the droplet journal and `alphabot_daemon.log` | G-18, G-9 | AC-1, AC-2, AC-3, AC-4, AC-6 | full |
| R-30 | Log lines for each engine cycle's duration, each Composer call's latency and each request | G-41, G-68 | AC-2, AC-3, AC-4 | narrowed: Composer latency is logged for the account-totals refresh and the retirement series fetches only; left out are the engine's own calls (inside its cycle duration, NB-2), the Emergency Liquidate fetch and sell calls (`app.py:5406`, `:5424`; a protected surface, overview NB-14), the Strategy Builder and incubation backtests, the draft client's `save_symphony` and the advisor `/score` reads (advisory, off the refresh path, NB-8) |
| R-32 | Caddy compresses responses, in the deployment configuration and in `docs/DEPLOYMENT.md` | G-44, G-5, G-6, G-68 | AC-5 | full |
| R-45 | Findings the audit marks covered stay with their plans | G-59 | NB-4 | full |
| R-81 | Daemon output reaches the journal line by line | G-10, G-68 | AC-1 | full |
| R-82 | The audit's section 8 production measurements are this suite's live verification | G-68 | AC-6 | full |
| R-89 | Open questions are answered by recorded defaults, never by stopping | G-12 | AC-4, AC-5 | full |
| R-96 | The existing ERROR lines for a failed account refresh and a failed flush resync log the exception type name only, with the detail at DEBUG | G-92 | AC-3 | full |

## Earlier criteria on the same surface
Searched every `feature-plans/*.md` (with the `.completed.md` and `.obsolete.md` families and the suite folders), `DECISIONS.md` and `docs/DEPLOYMENT.md` for `Caddy`, `reverse_proxy`, `encode`, `stdout`, `TextIOWrapper`, `werkzeug`, `ReadTimeout`, `X-Accel-Buffering` and `alphabot_daemon.log`.

| Earlier criterion (plan and AC) | Surface | Disposition (kept, changed or contradicted) | Why |
|---|---|---|---|
| `DECISIONS.md` DE-DEPLOY-001 (production deployment: non-root service user, systemd units, Caddy TLS) and `docs/DEPLOYMENT.md` Step 4 (the Caddyfile is a bare `reverse_proxy`) | Caddy | changed | TLS, the proxy target and the units stay; the site gains response compression (AC-5) |
| `feature-plans/fix-ops-cluster.md` F-010 and DE-OPS-CLUSTER-001 (a compact one-line WARNING with a counter for the known Composer read-timeout, full tracebacks kept for other errors) | Account-refresh logging | kept | AC-3 adds a latency line per call and keeps both behaviours |
| `feature-plans/dashboard-realtime-push.md` AC-2 (the event stream works through proxies; `X-Accel-Buffering: no`) | `/api/events` | kept | S-5.3 proves events still stream within 2 s through Caddy with compression on |
| `DECISIONS.md` DE-PRISM-DIAG-001 (redact credential-keyed values and key shapes from logged subprocess output) | Log redaction | kept | AC-4 follows the same rule for request lines (S-4.3) |
| ux-overhaul plan 05 (branch `ux-overhaul/fixtures`) NB-6 (measuring the droplet's first-paint timings and `/api/state` cost deferred) and its Known-Unconfirmed droplet timings | Droplet timing | changed | AC-6 measures them after deploy |
| The `__main__` UTF-8 stdout rewrap (`app.py:9001`, a Windows fix for non-Latin-1 characters) | Daemon output | kept | S-1.4: the UTF-8 encoding with replacement stays |

## Acceptance Criteria (Building)
### AC-1: Daemon output reaches the journal line by line
Requirement items: R-1, R-8, R-81
Rule: When the daemon prints a line, the line shall reach the journal within 2 s of the time it carries; the engine's own output keeps reaching `alphabot_daemon.log` by the end of each run, as at the ref.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-1.1 | B | the daemon under systemd | the scheduler prints "[HH:MM:SS] Triggering Alpha Bot..." at `:00` | the line is in the journal with a journal time within 2 s of its own |
| S-1.2 | N7 | an engine run | the engine prints "Alpha Bot Waking Up..." and exits | the line is in `alphabot_daemon.log` when the run ends, as at the ref (the engine rewraps its own stdout at `alpha_bot_execution.py:12`, and the engine file may change only in the close-snapshot builder) |
| S-1.3 | N6 | the engine exits non-zero | the daemon prints "Execution failed" | the line reaches the journal within 2 s and before the next minute's trigger line |
| S-1.4 | N1 | a line holding a non-Latin-1 character | it is printed | it reaches the journal UTF-8 encoded, with replacement for anything unencodable, as at the ref |
| S-1.5 | N6 | the daemon is killed with SIGKILL | the journal is read | every line printed more than 2 s before the kill is in it |
| S-1.6 | N7 | the engine's output | it is written | it goes to `alphabot_daemon.log`, as at the ref, and the engine file is unchanged; its timing is read from AC-2's cycle line |

Negative coverage: N1 S-1.4 · N2 n/a: logging refuses nothing · N3 n/a: no computed value · N4 n/a: no caller · N5 n/a: one writer per stream · N6 S-1.3, S-1.5 · N7 S-1.2, S-1.6 · N8 n/a: no edit state
Questions: none

### AC-2: One log line per engine run, with its id, times, duration and outcome
Requirement items: R-1, R-7, R-8, R-30
Rule: When an engine run ends, or the scheduler skips a minute, the daemon shall log one line naming the run's `cycle_id`, start and end times, duration in seconds, `outcome` and exit code, using plan 02's values (overview SD-3).

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-2.1 | B | a market-hours run that takes 38.4 s and exits 0 | it ends | one journal line carries its `cycle_id`, start, end, "38.4 s", outcome "cycle" and exit code 0 |
| S-2.2 | B | a closed-market run | it ends | one line with outcome "closed" |
| S-2.3 | N5 | the previous engine is still running at `:00` | the scheduler skips the minute | one line with outcome "skipped", the running `cycle_id` and how long it has run |
| S-2.4 | N6 | the engine exits non-zero | it ends | one line with outcome "failed" and the exit code |
| S-2.5 | N7 | the event for the same run | it is emitted | its `cycle_id` and `outcome` equal the log line's |
| S-2.6 | N1 | a run that crosses midnight UTC | it ends | the times carry their dates and offsets, and the duration is correct |

Negative coverage: N1 S-2.6 · N2 n/a: logging refuses nothing · N3 n/a: every run has the fields · N4 n/a: no caller · N5 S-2.3 · N6 S-2.4 · N7 S-2.5 · N8 n/a: no edit state
Questions: none

### AC-3: One log line per Composer call the daemon makes, with its latency
Requirement items: R-1, R-8, R-30, R-96
Rule: When the daemon process calls Composer (the account totals, and the retirement series fetches), it shall log one line naming the call's route template, its HTTP status or exception type, and its duration in milliseconds, with no account id, key or token in the line; and the existing ERROR lines for a failed account refresh (`app.py:905-909`) and a failed flush resync (`app.py:6407`) shall log the exception type name only at WARNING or above, with the exception text and traceback at DEBUG.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-3.1 | B | the `:00` account refresh | Composer answers 200 in 640 ms | one line reads the route template `/portfolio/accounts/{account}/total-stats`, status 200 and 640 ms |
| S-3.2 | N6 | Composer times out at 30 s | the call ends | one line with "ReadTimeout" and about 30000 ms, and the compact F-010 warning as at the ref |
| S-3.3 | N6 | Composer answers 503 | the call ends | one line with status 503 and its duration |
| S-3.4 | N7 | any call | it is logged | the line holds no account id, key id, secret or bearer token |
| S-3.5 | B | the retirement tick's series fetches | each call ends | one line per call with its route template, status and duration |
| S-3.6 | N5 | the scheduler's refresh and the post-cycle refresh run at once | both end | two lines, each with its own duration |
| S-3.7 | N6 | the account refresh raises a `ConnectionError` whose message carries the URL with the account id | the error is logged | the WARNING-or-above line names "ConnectionError" only; the message and traceback appear only at DEBUG |
| S-3.8 | N6 | the flush resync's refresh raises | the error is logged | the WARNING-or-above line names the exception type only; the detail is at DEBUG |

Negative coverage: N1 n/a: no new input · N2 n/a: logging refuses nothing · N3 n/a: every call has a status or an exception type · N4 n/a: no caller identity · N5 S-3.6 · N6 S-3.2, S-3.3, S-3.7, S-3.8 · N7 S-3.4 · N8 n/a: no edit state
Questions: none

### AC-4: One access line per request, with its status, time and size, and no secret
Requirement items: R-1, R-7, R-8, R-30, R-89
Rule: When the daemon answers an HTTP request, it shall log one line with the method, the path, the status, the duration in milliseconds and the response size in bytes, never a cookie, a CSRF token, a password, a query value or a request body; an event-stream request is logged when it opens and when it closes, with how long it stayed open.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-4.1 | B | `GET /api/state` | it is answered | one line reads "GET /api/state 200", its duration and its size |
| S-4.2 | N4 | an unauthenticated `GET /api/state` | it is answered | one line with status 401 |
| S-4.3 | N7 | `POST /login` with a password and a CSRF token, and any request carrying a session cookie | it is answered | the line holds neither the password, the token nor the cookie, and no body |
| S-4.4 | N1 | `GET /api/state?account=1a2b3c4d-0000-4000-8000-000000000000` | it is answered | the line shows the path and the parameter name with its value masked |
| S-4.5 | B | a client connects to `/api/events` and leaves after 12 minutes | the stream opens and closes | one line at open and one at close with 12 minutes' duration |
| S-4.6 | N6 | a route raises | it answers 500 | one line with status 500 |
| S-4.7 | N5 | 30 requests in one second | they are answered | 30 lines |
| S-4.8 | N7 | `POST /api/settings` with a body and a CSRF header | it is answered | one line with method, path, status, duration and size, and no body or token |
| S-4.9 | B | `GET /static/index.js` and `GET /api/chart/{symphony_id}` | each is answered | one line each with status, duration and size |

Negative coverage: N1 S-4.4 · N2 S-4.2 · N3 n/a: every answered request has a status · N4 S-4.2 · N5 S-4.7 · N6 S-4.6 · N7 S-4.3, S-4.8 · N8 n/a: no edit state
Questions: Q-1

### AC-5: Responses travel gzip-compressed through Caddy while events still stream
Requirement items: R-1, R-7, R-32, R-89
Rule: When a client that accepts gzip requests the dashboard through Caddy, the response shall be gzip-encoded, the after-close `/api/state` shall arrive in under 1 MB, events on `/api/events` shall still reach the browser within 2 s of being sent, and `docs/DEPLOYMENT.md` shall show the same Caddyfile the droplet runs.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-5.1 | B | the droplet with the new Caddyfile, after the close | `GET /api/state` with `Accept-Encoding: gzip` | `Content-Encoding: gzip` and a transferred body under 1 MB |
| S-5.2 | N1 | a client that sends no `Accept-Encoding` | `GET /api/state` | an uncompressed body, as at the ref |
| S-5.3 | B | a browser connected to `/api/events` through Caddy | a `cycle-complete` is sent | it arrives within 2 s, and the stream's response carries no `Content-Encoding`, because `text/event-stream` is excluded from compression |
| S-5.4 | B | the tracked `deploy/caddy/Caddyfile` and `docs/DEPLOYMENT.md` | they are read | the tracked file equals the droplet's `/etc/caddy/Caddyfile`, the document's block matches it apart from the site name, and both show `encode gzip zstd` with `text/event-stream` excluded |
| S-5.5 | N7 | TLS and the proxy target | Caddy reloads | the site keeps its certificate and proxies to `localhost:8090` |
| S-5.6 | N6 | the new Caddyfile has a syntax error | the PM runs `caddy validate` before reload | validation fails and the running config is unchanged |

Negative coverage: N1 S-5.2 · N2 n/a: compression refuses nothing · N3 n/a: no computed value · N4 n/a: the auth gate is the daemon's, unchanged · N5 n/a: one config per host · N6 S-5.6 · N7 S-5.5 · N8 n/a: no edit state
Questions: Q-2

### AC-6: Each section 8 unknown is measured after deploy and recorded
Requirement items: R-1, R-8, R-82
Rule: When the assembled stack is deployed, the PM shall record, read-only, a number and the command that produced it for each unknown the audit's section 8 lists, in the review folder for the stack's head SHA.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-6.1 | B | the access log (AC-4) | five market-hours polls and five after 16:05 are read | `/api/state`'s duration and size, open and closed, are recorded |
| S-6.2 | B | the cycle log (AC-2) | one trading day's lines are read | the engine cycle duration's median and maximum, and the count of "skipped" minutes, are recorded |
| S-6.3 | B | the Composer log (AC-3) | one day's account-total lines are read | their median and maximum latency and the counts of timeouts and 503s are recorded |
| S-6.4 | B | a daemon restart with `/` open in a browser | DevTools and the access log are read | whether a second `/api/events` opened and when events resumed are recorded |
| S-6.5 | B | after 16:10 ET on three trading days | `sqlite3 -readonly /opt/planetstopper/alphabot_state.db "select json_extract(data,'$.last_market_close_snapshot.trading_day') from bot_state where id=1"` runs | each day's result is recorded |
| S-6.6 | B | two market-hours `/api/state` payloads one cycle apart | a triggered entry's `guard_alpha` is compared | the drift is recorded |
| S-6.7 | B | the access log | one day's lines are read | the count of non-2xx `/api/state` answers by status is recorded |
| S-6.8 | B | `grep EXECUTION_START_TIME /opt/planetstopper/.env` | it runs | the value is recorded |
| S-6.9 | N3 | an unknown that cannot be measured on the day (for example no exit happened) | the record is written | that row says "not measured" with the reason and the next date to try |
| S-6.10 | N7 | every measurement | it is taken | it is read-only: no write to the droplet's database, `.env` or files |
| S-6.11 | B | ux-overhaul 05 C3 round 2's newest-request guard on `main` | a timer poll and an event poll are issued in that order and answered newest-first in a browser | the record shows the newer response kept, closing the audit's TA-11 row |
| S-6.12 | B | `/` open in a real browser on the droplet | three refreshes land, the tab is hidden for 10 minutes, and the 1Y window is picked | the record notes the drawn charts, the animation, the catch-up request and the headline's window, closing the live-browser row |
| S-6.13 | B | market hours | the S-6.1, S-6.3 and S-6.6 reads are repeated before 16:00 ET | the open-branch `/api/state` size, the post-cycle chip and the mid-cycle pairing are recorded |

Negative coverage: N1 n/a: no input · N2 n/a: nothing is refused · N3 S-6.9 · N4 n/a: the PM runs the reads with the droplet's existing access · N5 n/a: each read runs once per row · N6 n/a: a failed read is S-6.9's "not measured" · N7 S-6.10 · N8 n/a: no edit state
Questions: none

## Rule-by-Entry-Point Matrix
| Rule | `GET /api/state` | `POST /login` | `/api/events` | `GET /` and other pages | Composer account totals | Composer retirement series |
|---|---|---|---|---|---|---|
| AC-3 Composer calls logged without secrets | n/a: no Composer call | n/a: no Composer call | n/a: no Composer call | n/a: no Composer call | S-3.1, S-3.4 | S-3.5 |
| AC-4 requests logged without secrets | S-4.1, S-4.4 | S-4.3 | S-4.5 | S-4.3 | n/a: an outbound call, AC-3 | n/a: an outbound call, AC-3 |

| Rule | Other POST routes | Static assets and polled JSON GET routes |
|---|---|---|
| AC-3 Composer calls logged without secrets | n/a: their Composer calls, where any, are AC-3's own callers or excluded (NB-8) | n/a: no Composer call |
| AC-4 requests logged without secrets | S-4.8 | S-4.9 |

## Not Building
Non-goals:
- NB-1: A metrics system, dashboards for the logs, or log shipping off the droplet (approved by the PM, [PM-ASSUMED]).
- NB-2: Logging the engine's own Composer and Alpaca calls; the engine file may change only in the close-snapshot builder (overview NB-5), so the engine's calls are measured inside AC-2's cycle duration (narrows R-30; approved by the PM, [PM-ASSUMED]).
- NB-3: Trimming `/api/state`'s fields; plan 01 removes the nested snapshot, and the remaining fields stay (plan 01 Q-4) (approved by the PM, [PM-ASSUMED]).
- NB-8: Composer latency lines for the advisory callers (backtests, `save_symphony`, `/score` reads) and for the Emergency Liquidate path; the first are off the refresh path, and the second is a protected surface (narrows R-30; approved by the PM, [PM-ASSUMED]).
- NB-5: Caddy's own access log; the daemon's access line (AC-4) is the one record (approved by the PM, [PM-ASSUMED]; Q-1).

Behaviour that stays exactly as it is:
- NB-6: The engine file and its output destination; the units' `ExecStart`, `User` and `EnvironmentFile`; TLS.

Deferred:
- NB-4: The covered findings stay with their plans (overview NB-12).

Protected surfaces consumed, not changed:
- NB-7: Composer's API, consumed only (overview NB-13).

## Defect Classes
| DC-n | Class (what goes wrong) | Trigger (B or N1-N8) | Sibling search (tool, query, scope) | Scenarios | Raised in review as |
|---|---|---|---|---|---|
| DC-1 | A log line carrying a secret (cookie, token, password, key, account id) | N7 | grep every new log call in `app.py` and `advisors/composer_series_client.py` for `headers`, `cookie`, `token`, `password`, `account`, `key` and request bodies | S-3.4, S-4.3, S-4.4 | - |
| DC-2 | An output stream buffered past its own timestamps | N6 | grep `TextIOWrapper`, `print(` without `flush` and `subprocess.run(` with a file `stdout` in `app.py` | S-1.1, S-1.2 | - |
| DC-3 | A compression layer that buffers a streaming response | N6 | read the Caddyfile's `encode` block and `/api/events`'s headers in `app.py` | S-5.3 | - |
| DC-4 | A run or request recorded in one place with values another place records differently (overview SD-3) | B | grep `cycle_id` and `outcome` in `app.py` across the event, `/health` and the log line | S-2.5 | - |
| DC-5 | Documentation that differs from the deployed configuration | B | diff the Caddyfile and unit blocks of `docs/DEPLOYMENT.md` against the droplet's files | S-5.4 | - |

## Worked Example
At 10:44:00 UTC the journal shows "[10:44:00] Triggering Alpha Bot..." stamped 10:44:00, then, 38 s later, the run's line with its `cycle_id`, start 10:44:00, end 10:44:38, "38.4 s", outcome "cycle" and exit code 0; between them, "GET /api/state 200" with 3,912 ms and 1,203,441 bytes, and the account refresh's "/portfolio/accounts/{account}/total-stats 200 640 ms" (S-1.1, S-2.1, S-4.1, S-3.1). A browser through Caddy receives that `/api/state` with `Content-Encoding: gzip` in about 0.2 MB (S-5.1).

Failure case: Composer times out at 10:45:00. The journal shows the account-total line "ReadTimeout 30012 ms" and the compact F-010 warning, and the 10:45 trigger line is still stamped 10:45:00 (S-3.2, S-1.1).

## Configuration and Enablement
- The repository tracks the Caddyfile as `deploy/caddy/Caddyfile`, with `encode gzip zstd` and `text/event-stream` excluded from compression from the start. Before the PR merges, the PM applies the PR head's tracked file to the droplet's `/etc/caddy/Caddyfile`, runs `caddy validate`, reloads Caddy once and runs the live check; rollback is restoring the previous file and one reload. `docs/DEPLOYMENT.md` shows the same block.
- No new `.env` key; the daemon's logging needs no configuration.

## Security Considerations
- Input validation and injection: log lines print paths and statuses only; request paths are written as data, never formatted from user input into format strings.
- Authorization and access control: no route changes guard.
- Data exposure (security): the access and Composer lines never carry cookies, CSRF tokens, passwords, bodies, query values, account ids or keys (S-3.4, S-4.3, S-4.4), following DE-PRISM-DIAG-001's redaction rule; the journal is readable only by the droplet's administrators.
- Client-side rendering: none.
- Abuse or rate limits: one line per request adds journal volume (about 3 polls a minute per tab plus their chart requests); journald's own rotation bounds it.

## Definition of Done (Verification)
| AC | Scenarios | Test level | Real-entrypoint test |
|---|---|---|---|
| AC-1 | S-1.1, S-1.2, S-1.3, S-1.4, S-1.5, S-1.6 | a subprocess test that runs the daemon's `__main__` stdout setup with a pipe and times each line's arrival; the engine file's diff checked empty outside the builder | `journalctl -u planetstopper -o short-iso` against the `[HH:MM:SS]` stamps on the droplet |
| AC-2 | S-2.1, S-2.2, S-2.3, S-2.4, S-2.5, S-2.6 | `trigger_alpha_bot()` with a stub engine and a captured log handler | the droplet journal over one trading day |
| AC-3 | S-3.1, S-3.2, S-3.3, S-3.4, S-3.5, S-3.6, S-3.7, S-3.8 | `_refresh_account_totals()` and the series client with mocked responses and a captured log handler | the droplet journal |
| AC-4 | S-4.1, S-4.2, S-4.3, S-4.4, S-4.5, S-4.6, S-4.7, S-4.8, S-4.9 | the Flask test client with a captured log handler, including a login POST and a cookie; S-4.2's test sets `app._auth_check_enabled` back to True, because `tests/conftest.py:297` turns it off | the droplet journal after a page load |
| AC-5 | S-5.1, S-5.2, S-5.3, S-5.4, S-5.5, S-5.6 | a document check of `docs/DEPLOYMENT.md`; no unit test can run Caddy | the PR head's tracked `deploy/caddy/Caddyfile` applied to the droplet before merge, then `curl -s -o /dev/null -H 'Accept-Encoding: gzip' -w '%{size_download}'` against the droplet and DevTools for the event stream; one-reload rollback |
| AC-6 | S-6.1, S-6.2, S-6.3, S-6.4, S-6.5, S-6.6, S-6.7, S-6.8, S-6.9, S-6.10, S-6.11, S-6.12, S-6.13 | none: a measurement record | the record itself, in the review folder for the stack's head SHA |

Gates: `ruff format --check`; `ruff check`; a scoped pytest run of `tests/app`, `tests/realtime_push`, `tests/security` and `tests/advisors`, at most 4 workers, never `-n auto`; CI green on the exact SHA; `/review`; then the PM's live check (the real-entrypoint column), because tests-green is necessary and never sufficient.
Fixture provenance (SD-7): Composer responses for AC-3 come from `tests/fixtures/composer/v3-audit/total_stats.json` (the captured 200); the 503 and timeout cases are derived from that record with only the status or the raised exception changed, and their PROVENANCE row is signed off by the PM ([PM-ASSUMED]); request cases use the Flask test client's real responses.

## Known-Unconfirmed and Standing Risk
- Whether Caddy v2.11.4 would buffer a compressed event stream is not tested, because `text/event-stream` is excluded from compression from the start (Q-2); S-5.3 checks the stream arrives uncompressed within 2 s.
- The journal volume added by AC-4 is unmeasured; S-6.1's read shows it.
- Section 8's restart check (S-6.4) needs a deliberate daemon restart in market hours or after; the PM picks a moment with no open position at risk, as a deploy already does.

## Open Questions
| Q-n | Question | Blocks (AC ids or "plan") | Proposed default | Answer |
|---|---|---|---|---|
| Q-1 | Should request lines come from the daemon or from Caddy's `log` directive? | AC-4 | The daemon, so local runs log the same way and the line sits beside the cycle and Composer lines in one journal | default approved by the PM, [PM-ASSUMED] |
| Q-2 | Which encodings should Caddy offer? | AC-5 | `encode gzip zstd`, so browsers get gzip; `text/event-stream` excluded from compression from the start | approved by the PM as `encode gzip zstd` with the event stream excluded (PM amendment), [PM-ASSUMED] |

## Decisions
| ID | Decision | Rationale |
|---|---|---|
| DEC-1 | The engine's output buffering stays as at the ref; its timing comes from the daemon's cycle line (AC-2) | The engine rewraps its own stdout (`alpha_bot_execution.py:12`), so no daemon-side setting can line-buffer it, and the engine file may change only in the close-snapshot builder (overview NB-5) |
| DEC-2 | Composer route templates are logged with `{account}` in place of the id | An account id is an identifier the logs do not need; the route and latency are what diagnosis reads |
| PM-1 | Caddy uses `encode gzip zstd`, with `text/event-stream` excluded from the start (Q-2, S-5.3) | PM amendment from the Gate-2 approach, [PM-ASSUMED] |
| PM-2 | The Caddy change is applied on the droplet before merge, from the PR head's tracked `deploy/caddy/Caddyfile`, with a one-reload rollback | PM amendment, [PM-ASSUMED] |
| PM-3 | The existing ERROR lines at `app.py:905-909` and `app.py:6407` log the exception type name only at WARNING or above, with the detail at DEBUG (S-3.7, S-3.8) | PM amendment, ruled in, [PM-ASSUMED] |
| PM-4 | The matrix adds "Other POST routes" and "Static assets and polled JSON GET routes" | PM amendment, [PM-ASSUMED] |
| PM-5 | The 503 and timeout fixtures derived from `v3-audit/total_stats.json` are signed off | PM amendment, [PM-ASSUMED] |

## Docs to Update
- `docs/DEPLOYMENT.md` (the Caddyfile block and a note on reading the cycle, Composer and access lines), `docs/generated/app.md` (logging), `docs/generated/INDEX.md`.
- `README.md` where it describes logs.
- A `DECISIONS.md` entry appended at ship by the doc writer.
- A drafted `.claude/CLAUDE.md` Key Files row update for `app.py`, for PM approval.

## Follow-Ups
- NB-2: logging the engine's own vendor calls, if the cycle duration shows they matter.
- NB-5: a Caddy access log, if a proxy-level record is ever needed.
