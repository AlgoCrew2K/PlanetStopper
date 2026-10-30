# Approval packet: Observability and transport: timing is readable from the logs, and polls travel compressed

## What is being built

The PM can read every timing fact the suite depends on from the droplet's journal and `alphabot_daemon.log`, and each open tab's poll travels compressed, so the suite's live verification rests on measured numbers, not on inference.
- Daemon output reaches the journal line by line (AC-1).
- One log line per engine run, with its id, times, duration and outcome (AC-2).
- One log line per Composer call the daemon makes, with its latency (AC-3).
- One access line per request, with its status, time and size, and no secret (AC-4).
- Responses travel gzip-compressed through Caddy while events still stream (AC-5).
- Each section 8 unknown is measured after deploy and recorded (AC-6).

## Acceptance criteria and scenarios

### AC-1: Daemon output reaches the journal line by line
Rule: When the daemon prints a line, the line shall reach the journal within 2 s of the time it carries; the engine's own output keeps reaching `alphabot_daemon.log` by the end of each run, as at the ref.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-1.1 | B | the daemon under systemd | the scheduler prints "[HH:MM:SS] Triggering Alpha Bot..." at `:00` | the line is in the journal with a journal time within 2 s of its own |
| S-1.2 | N7 | an engine run | the engine prints "Alpha Bot Waking Up..." and exits | the line is in `alphabot_daemon.log` when the run ends, as at the ref (the engine rewraps its own stdout at `alpha_bot_execution.py:12`, and the engine file may change only in the close-snapshot builder) |
| S-1.3 | N6 | the engine exits non-zero | the daemon prints "Execution failed" | the line reaches the journal within 2 s and before the next minute's trigger line |
| S-1.4 | N1 | a line holding a non-Latin-1 character | it is printed | it reaches the journal UTF-8 encoded, with replacement for anything unencodable, as at the ref |
| S-1.5 | N6 | the daemon is killed with SIGKILL | the journal is read | every line printed more than 2 s before the kill is in it |
| S-1.6 | N7 | the engine's output | it is written | it goes to `alphabot_daemon.log`, as at the ref, and the engine file is unchanged; its timing is read from AC-2's cycle line |

Negative coverage: N1 S-1.4 | N2 n/a: logging refuses nothing | N3 n/a: no computed value | N4 n/a: no caller | N5 n/a: one writer per stream | N6 S-1.3, S-1.5 | N7 S-1.2, S-1.6 | N8 n/a: no edit state

### AC-2: One log line per engine run, with its id, times, duration and outcome
Rule: When an engine run ends, or the scheduler skips a minute, the daemon shall log one line naming the run's `cycle_id`, start and end times, duration in seconds, `outcome` and exit code, using plan 02's values (overview SD-3).

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-2.1 | B | a market-hours run that takes 38.4 s and exits 0 | it ends | one journal line carries its `cycle_id`, start, end, "38.4 s", outcome "cycle" and exit code 0 |
| S-2.2 | B | a closed-market run | it ends | one line with outcome "closed" |
| S-2.3 | N5 | the previous engine is still running at `:00` | the scheduler skips the minute | one line with outcome "skipped", the running `cycle_id` and how long it has run |
| S-2.4 | N6 | the engine exits non-zero | it ends | one line with outcome "failed" and the exit code |
| S-2.5 | N7 | the event for the same run | it is emitted | its `cycle_id` and `outcome` equal the log line's |
| S-2.6 | N1 | a run that crosses midnight UTC | it ends | the times carry their dates and offsets, and the duration is correct |

Negative coverage: N1 S-2.6 | N2 n/a: logging refuses nothing | N3 n/a: every run has the fields | N4 n/a: no caller | N5 S-2.3 | N6 S-2.4 | N7 S-2.5 | N8 n/a: no edit state

### AC-3: One log line per Composer call the daemon makes, with its latency
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

Negative coverage: N1 n/a: no new input | N2 n/a: logging refuses nothing | N3 n/a: every call has a status or an exception type | N4 n/a: no caller identity | N5 S-3.6 | N6 S-3.2, S-3.3, S-3.7, S-3.8 | N7 S-3.4 | N8 n/a: no edit state

### AC-4: One access line per request, with its status, time and size, and no secret
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

Negative coverage: N1 S-4.4 | N2 S-4.2 | N3 n/a: every answered request has a status | N4 S-4.2 | N5 S-4.7 | N6 S-4.6 | N7 S-4.3, S-4.8 | N8 n/a: no edit state

### AC-5: Responses travel gzip-compressed through Caddy while events still stream
Rule: When a client that accepts gzip requests the dashboard through Caddy, the response shall be gzip-encoded, the after-close `/api/state` shall arrive in under 1 MB, events on `/api/events` shall still reach the browser within 2 s of being sent, and `docs/DEPLOYMENT.md` shall show the same Caddyfile the droplet runs.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-5.1 | B | the droplet with the new Caddyfile, after the close | `GET /api/state` with `Accept-Encoding: gzip` | `Content-Encoding: gzip` and a transferred body under 1 MB |
| S-5.2 | N1 | a client that sends no `Accept-Encoding` | `GET /api/state` | an uncompressed body, as at the ref |
| S-5.3 | B | a browser connected to `/api/events` through Caddy | a `cycle-complete` is sent | it arrives within 2 s, and the stream's response carries no `Content-Encoding`, because `text/event-stream` is excluded from compression |
| S-5.4 | B | the tracked `deploy/caddy/Caddyfile` and `docs/DEPLOYMENT.md` | they are read | the tracked file equals the droplet's `/etc/caddy/Caddyfile`, the document's block matches it apart from the site name, and both show `encode gzip zstd` with `text/event-stream` excluded |
| S-5.5 | N7 | TLS and the proxy target | Caddy reloads | the site keeps its certificate and proxies to `localhost:8090` |
| S-5.6 | N6 | the new Caddyfile has a syntax error | the PM runs `caddy validate` before reload | validation fails and the running config is unchanged |

Negative coverage: N1 S-5.2 | N2 n/a: compression refuses nothing | N3 n/a: no computed value | N4 n/a: the auth gate is the daemon's, unchanged | N5 n/a: one config per host | N6 S-5.6 | N7 S-5.5 | N8 n/a: no edit state

### AC-6: Each section 8 unknown is measured after deploy and recorded
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

Negative coverage: N1 n/a: no input | N2 n/a: nothing is refused | N3 S-6.9 | N4 n/a: the PM runs the reads with the droplet's existing access | N5 n/a: each read runs once per row | N6 n/a: a failed read is S-6.9's "not measured" | N7 S-6.10 | N8 n/a: no edit state

## Rule-by-entry-point matrix

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

## Narrowed mappings

R-1: Every timing issue the refresh-timing audit found is resolved; maps to AC-1, AC-2, AC-3, AC-4, AC-5, AC-6; left out: this plan's part is TA-30, TA-13's payload half and the section 8 measurements
R-7: The suite covers the scheduler, `/api/state`, `/api/events` and the polled routes; maps to AC-2, AC-4, AC-5; left out: their logging and transport
R-30: Log lines for each engine cycle's duration, each Composer call's latency and each request; maps to AC-2, AC-3, AC-4; left out: Composer latency is logged for the account-totals refresh and the retirement series fetches only; left out are the engine's own calls (inside its cycle duration, NB-2), the Emergency Liquidate fetch and sell calls (`app.py:5406`, `:5424`; a protected surface, overview NB-14), the Strategy Builder and incubation backtests, the draft client's `save_symphony` and the advisor `/score` reads (advisory, off the refresh path, NB-8)

## Open questions

| Q-n | Question | Blocks (AC ids or "plan") | Proposed default | Answer |
|---|---|---|---|---|
| Q-1 | Should request lines come from the daemon or from Caddy's `log` directive? | AC-4 | The daemon, so local runs log the same way and the line sits beside the cycle and Composer lines in one journal | default approved by the PM, [PM-ASSUMED] |
| Q-2 | Which encodings should Caddy offer? | AC-5 | `encode gzip zstd`, so browsers get gzip; `text/event-stream` excluded from compression from the start | approved by the PM as `encode gzip zstd` with the event stream excluded (PM amendment), [PM-ASSUMED] |

Sensitive: security (AC-3, AC-4). qa writes hidden scenarios for the named criteria after approval; they are not shown to the builders.
