# Approach: Observability and transport: timing is readable from the logs, and polls travel compressed
Spec: ../05-observability-transport.md
Ref: PlanetStopper@37c8e983b64c6980c28199a3f9318486b60f3864

## Architecture

Every file:line below is at PlanetStopper@37c8e983. Plan 02 and the model-unpin plan move `app.py` lines before cycle C4 and the PR; each cycle re-reads its anchors by function name after its merges. The suite approach, `feature-plans/refresh-timing/approach/00-suite.md`, owns the contracts this plan shares, cited here by id and not repeated: RC-4 (plan 02's seams), RC-7 (fixture folders), RC-8 (branch names and the stack), RC-9 (integration merges as test-writer and implementer tasks) and RC-11 (the test-run governor).

**One new module, `timing_log.py` (repo root).** Searched first: `git grep -n "basicConfig\|getLogger(" 37c8e983 -- '*.py' ':(exclude)tests/**'` finds 29 `getLogger(__name__)` module loggers, the `werkzeug` level set at `app.py:415-416`, the `alphabot` logger with its file handler at `app.py:482-486`, and two CLI-only `basicConfig` calls (`advisors/strategy_builder_scheduler.py:211`, `advisors/weekly_suggestions_scheduler.py:454`). No shared logging or stdout helper exists, and `advisors/composer_series_client.py` cannot import `app` (it is lazily imported by the daemon, `advisors/retirement_recommender.py:594`), so the shared emitter needs its own module. Its parts:
- `TIMING_LOGGER_NAME = "planetstopper.timing"` and `timing_logger`, level INFO, `propagate = False`. It sits outside the `alphabot` hierarchy on purpose: `tests/app/test_account_totals_timeout_logging.py:62` asserts exactly one record under `alphabot` for a read timeout, and S-3.2 adds a second line beside it. With no propagation the line can never be duplicated by the root handler that the module-level `logging.info` at `database.py:1827` installs implicitly (`basicConfig` format, the `WARNING:alphabot:` prefix seen in the droplet journal). Under pytest the logger has no handler, so INFO records fall to `logging.lastResort` (WARNING) and are dropped; tests attach their own capture handler.
- `clock = time.monotonic`, a module attribute every caller reads at call time (`timing_log.clock()`), never imported by name, so tests replace it with a fake clock.
- `make_daemon_stdout(buffer) -> io.TextIOWrapper`: `io.TextIOWrapper(buffer, encoding="utf-8", errors="replace", line_buffering=True)`. This is the `app.py:9001` rewrap with `line_buffering=True` added; the encoding and replacement stay (S-1.4, the kept earlier criterion).
- `attach_stream_handler(stream)`: idempotent; one `logging.StreamHandler(stream)` on `timing_logger` whose formatter prefixes each line with its own UTC time, `YYYY-MM-DDTHH:MM:SS.mmmZ`, so a line reads the same from the journal and from a local console.
- `configure_daemon_output()`: sets `sys.stdout = make_daemon_stdout(sys.stdout.buffer)` and calls `attach_stream_handler(sys.stdout)`. The old wrapper stays referenced by `sys.__stdout__`, as at the ref, so it is never collected and never closes the descriptor.
- `emit(event, *words, **fields)`: renders `event word ... key=value ...` and logs it at INFO through `timing_logger`, with the rendered text passed as a `%s` argument, never as the format string. Every word and value is passed through one sanitizer that percent-encodes space, `%`, CR, LF and any non-printable or non-ASCII character, so no caller value can split, forge or extend a line (log injection); `None` renders as `-`. `emit` catches everything and never raises into its caller.
- `AccessLogMiddleware` (added by C3; see below).

**Line formats** (fixed here so the test-writer and implementer share them; DEC-2's `{account}` template applies):
- Cycle (AC-2): `cycle cycle_id=<id> outcome=<outcome> started_at=<ISO 8601 with offset> ended_at=<ISO 8601 with offset> duration_s=<one decimal> exit_code=<int or ->`. A skipped minute: `cycle cycle_id=<the skip's own id> outcome=skipped started_at=<skip time> running_cycle_id=<running run's id> running_since=<ISO> running_s=<one decimal>`. `cycle_id`, `outcome` and both times come from plan 02's `CycleRecord` (overview SD-3); `duration_s` and `running_s` come from differences of plan 02's `_monotonic` (the one monotonic seam on the engine path, so C4's tests patch one clock), never from wall-clock subtraction, so a midnight or DST crossing cannot distort them (S-2.6).
- Composer (AC-3): `composer GET /portfolio/accounts/{account}/total-stats status=<code> ms=<int>`, or `error=<ExceptionTypeName>` in place of `status=`. The series fetch uses the existing template constant `_ENDPOINT_LOG_NAME` (`advisors/composer_series_client.py:121`), split into its method and path words.
- Access (AC-4): `access <METHOD> <path>[?<name>=***&...] status=<code> ms=<int> bytes=<int>`. An event stream writes `access GET /api/events status=200 stream=open` when the response starts and `access GET /api/events status=200 stream=closed open_s=<one decimal> bytes=<int>` when it closes. When the wrapped application raises out of `wsgi_app`, the line carries `status=500 bytes=0 error=<ExceptionTypeName>` and the exception is re-raised. No line carries exception text, a cookie, a header value, a query value or a body.

**AC-1, stdout.** `app.py:9001` (the `__main__` rewrap) becomes `timing_log.configure_daemon_output()`, the first statement of `__main__` (`app.py:8997`), before `_acquire_daemon_singleton` prints. The trigger and failure prints (`app.py:777`, `:787`) then flush at their newline with no edit. Python 3.9 and later already line-buffer a non-interactive stderr, so the WARNING lines were never late; only stdout was. The engine's own rewrap (`alpha_bot_execution.py:12`) and its output to `alphabot_daemon.log` (`app.py:783-785`) are untouched (DEC-1, NB-6).

**AC-2, the cycle line.** Lands in `trigger_alpha_bot()` (`app.py:776-789`) as plan 02 rewrites it, through plan 02's seams (RC-4): `CycleRecord`, whose `event_payload()` gives the five contract keys and which also carries `exit_code`, `running_cycle_id` and `running_since`; `_publish_cycle(record)`, the one choke point every run and every skip passes through in `trigger_alpha_bot()`'s `finally`, replacing the bare fan-out of `_notify_cycle_complete` (`app.py:726-773`); `_begin_engine_run(started_at)`, `_end_engine_run(cycle_id)` and `_engine_run_state()`; the clocks `_now_et` and `_monotonic`; and `_log_skipped_minute(record)`, called only from `_publish_cycle` for a skipped record. C4's changes:
- `_publish_cycle` emits the cycle line from the record it publishes, for every non-skipped outcome, so the event and the line come from one object and cannot disagree (S-2.5, DC-4). `exit_code` is read from the record, never recomputed. `duration_s` is `_monotonic()` at publish minus a `_monotonic()` reading that `trigger_alpha_bot()` takes immediately before the spawn and passes along with the record; no wall-clock subtraction.
- `_log_skipped_minute(record)` is replaced: its body becomes the skipped form of the cycle line, taking `running_cycle_id` and `running_since` from the record and the running run's age from `_engine_run_state()` in the same way 02's `_health_status()` measures it for its 180 s rule, so the line and `/health` agree. Each skip then has exactly one line (plan 02 S-5.1 and this plan's S-2.3 describe that one line).

**AC-3, Composer latency and the two failure lines.** Two timed call sites and two rewritten failure lines, all in the daemon process:
- `_refresh_account_totals()` (`app.py:813-910`): the single `requests.get` at `app.py:843` is wrapped in an inner `try`: on a response, `emit("composer", "GET", _TOTAL_STATS_LOG_ROUTE, status=resp.status_code, ms=...)`; on any exception, `emit(..., error=type(exc).__name__, ms=...)` and re-raise into the existing `except ReadTimeout` and `except Exception` branches (`app.py:886-909`). The `ReadTimeout` branch (`app.py:886-903`, the F-010 compact WARNING and its counter) stays byte-unchanged. New constant `_TOTAL_STATS_LOG_ROUTE = "/portfolio/accounts/{account}/total-stats"` beside `_ACCOUNT_TOTALS_HTTP_TIMEOUT_S` (`app.py:590`). All three callers go through this one function: the scheduler tick (`app.py:1429`), the post-cycle thread (`app.py:763`) and the flush-resync thread (`app.py:6403`); durations are locals, so concurrent calls each log their own (S-3.6).
- The generic branch (`app.py:904-909`, S-3.7, PM-3) becomes two records on the same `alphabot` logger: an ERROR record keeping its existing message text, whose `%s` argument becomes `type(exc).__name__`, with no `exc_info`, then `_daemon_log.debug("_refresh_account_totals failure detail: %s", exc, exc_info=True)`. The two F-010 tests that pin a traceback (`tests/app/test_account_totals_timeout_logging.py:131`, `:153`) capture at DEBUG and assert that some record carries `exc_info`, so they stay green on the DEBUG record; S-3.7's test pins that no WARNING-or-higher record carries `exc_info` or the message text.
- The flush-resync failure line (`app.py:6407`, S-3.8, PM-3) becomes `_daemon_log.error("flush_resync: Composer resync failed: %s", type(exc).__name__)` plus the same DEBUG detail record. Its `except` (`app.py:6406`) is reached only when starting or joining the refresh thread raises, because `_refresh_account_totals` never raises; S-3.8's test makes `threading.Thread.start` raise. The `errors.append(f"resync: {exc}")` at `app.py:6408`, which puts the message into the route's JSON response (`app.py:6430`), is outside S-3.8 and stays (Items Raised).
- `_fetch_with_retry()` (`advisors/composer_series_client.py:243-293`): the `requests.get` at `:255` is timed per attempt and emits one line per HTTP call (a retried fetch logs each attempt). The existing per-attempt `logger.info` calls (`:262-267`, `:270-276`) stay as they are. The client imports `timing_log` (a root module, as its `alpha_bot_execution` import at `:66` already is). Nothing else changes there: the no-trade Group G scan forbids `urllib` and any attribute named `post`, `put`, `patch`, `delete`, `request` or `send` in this file (`tests/security/test_retirement_action_no_trade_boundary.py:893-916`), so the sanitizer and query helpers live in `timing_log.py`, never here.

**AC-4, access lines.** `timing_log.AccessLogMiddleware` wraps `app.wsgi_app` (installed at `app.py:415-416`, where `werkzeug` stays at ERROR so each request has exactly one line). Searched first: `grep -n "wsgi_app\|ProxyFix\|after_request\|teardown_request" app.py` finds no middleware and no after-request hook, only the auth and CSRF `before_request` hooks (`app.py:193`, `:489`). A WSGI wrapper rather than an after-request hook because only the WSGI layer sees the response body's real size, the time to its last byte, and the close of a stream (S-4.5). Behaviour:
- Reads `REQUEST_METHOD`, `PATH_INFO` and `QUERY_STRING` only. Never reads `HTTP_COOKIE`, any other header, or `wsgi.input`, so no body, cookie, CSRF token or password can reach the line (S-4.3).
- The path is percent-encoded by the shared sanitizer; the query is rendered as its parameter names, each `=***`, from `urllib.parse.parse_qsl(qs, keep_blank_values=True)`, and as `?***` when it does not parse (S-4.4).
- `start_response` is wrapped to record the status code and `Content-Type`; a second call with `exc_info` updates the status. A `text/event-stream` response emits the open line inside that wrapper.
- The response iterable is wrapped, never consumed eagerly: it counts `len(chunk)` per yielded chunk and, in its `close()`, first forwards to the inner iterable's `close()` (the SSE generator's `finally` that removes its client queue, `app.py:2497-2504`, depends on it), then emits the closed or access line with `ms` from `timing_log.clock()`. Werkzeug's server calls `close()` in its `finally` after the last write or a failed write.
- An exception raised out of `wsgi_app` is logged with `status=500 error=<type>` and re-raised. In production Flask turns a route exception into a 500 response first (S-4.6); in tests `TESTING` propagates exceptions, which the S-4.6 test turns off.
- Each line is one `logger.info` call; logging's handler lock keeps concurrent lines whole (S-4.7).

**AC-5, Caddy.** New tracked file `deploy/caddy/Caddyfile`, following the `deploy/planetstopper.service.d/memory.conf` convention (a header comment saying how the PM applies it). Body (Q-2 and PM-1: `encode gzip zstd`, with `text/event-stream` excluded from the start):
```
104-248-7-101.sslip.io {
    encode gzip zstd {
        match {
            header Content-Type application/json*
            header Content-Type text/html*
            header Content-Type text/css*
            header Content-Type text/plain*
            header Content-Type text/javascript*
            header Content-Type application/javascript*
            header Content-Type image/svg+xml*
        }
    }
    reverse_proxy localhost:8090
}
```
- `gzip` is listed first because Caddy picks the first configured encoding when the client weights them equally, and browsers send `gzip, deflate, br, zstd` at equal weight; a browser therefore gets gzip, as AC-5's rule and the overview's Suite Gate item 3 require, and zstd serves a client that accepts zstd and not gzip.
- The explicit `match` replaces Caddy's default matcher, which includes `text/*` and so would compress `text/event-stream`. Every type the dashboard serves is listed (JSON from every `/api` route, HTML pages, the static CSS and JS, SVG, plain-text errors), and no `text/*` wildcard appears, so the event stream is never compressed (S-5.3, DC-3). Whether Caddy would buffer a compressed stream is therefore never exercised (plan Known-Unconfirmed).
- `docs/DEPLOYMENT.md` Step 4 (`docs/DEPLOYMENT.md:113-125`) shows the same block with `your-domain.example` and gains the apply, verify and rollback procedure below. Nothing in the daemon changes for AC-5.

Apply (PM-2: before the merge, from the PR head's tracked file; the PM, as root on the droplet, after `runuser -u planetstopper -- git -C /opt/planetstopper status` is clean and the PR head is checked out for the live check):
1. `cp -p /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak-$(date -u +%Y%m%d)`.
2. `install -m 0644 /opt/planetstopper/deploy/caddy/Caddyfile /etc/caddy/Caddyfile.new`.
3. `caddy validate --config /etc/caddy/Caddyfile.new --adapter caddyfile` must print `Valid configuration`; on failure stop here: the live file and the running config are untouched (S-5.6).
4. `systemctl cat caddy | grep ExecReload` confirms the unit reloads through `caddy reload`; then `mv /etc/caddy/Caddyfile.new /etc/caddy/Caddyfile && systemctl reload caddy`, one reload, never a restart.
5. Verify: `systemctl is-active caddy`; `journalctl -u caddy --since "-2 min" --no-pager` shows no error; `cmp /etc/caddy/Caddyfile /opt/planetstopper/deploy/caddy/Caddyfile` is silent (S-5.4, DC-5); the S-5.1 to S-5.5 reads in C5's table.
6. Rollback: `cp -p /etc/caddy/Caddyfile.bak-<date> /etc/caddy/Caddyfile && systemctl reload caddy`.
7. After the merge the droplet returns to `main` at the merge commit, whose `deploy/caddy/Caddyfile` is the same file, so the `cmp` still holds.

**AC-6.** No code. The PM's measurement record at `.claude/review/<stack-head-sha12>/live/section8-measurements.md` (overview SD-11), one row per S-6.x with the number, the read-only command and its UTC time; the commands are named in C6's table.

**Routes, schema, dependencies.** No route is added or changed, no response body or header changes, no schema change, no new dependency (`urllib.parse`, `io`, `logging` and `time` are stdlib). `alpha_bot_execution.py` and `math_engine.py` carry zero diff from this plan.

## Criterion Thread

| AC | R items | Design element (file:line, or "new: <reason and search>") | Test seam (test file:line; fixture and provenance; harness pattern) | Cycle |
|---|---|---|---|---|
| AC-1 | R-1, R-8, R-81 | `app.py:9001` (the `__main__` rewrap) replaced by `timing_log.configure_daemon_output()`; prints at `app.py:777`, `:787` unchanged; new `timing_log.py`: no shared logging or stdout helper exists (search in Architecture) | new `tests/app/test_daemon_output_line_buffering.py` (an in-process recording raw buffer under `make_daemon_stdout`, plus a Popen child that runs `configure_daemon_output()`, following the subprocess pattern at `tests/advisors/test_prism_dotenv_hardening.py:54`); `tests/execution/test_reliability_expansion.py:833`, `:869` kept green for the engine's destination; fixtures: none recorded, the lines are the test's own literals | C1 |
| AC-2 | R-1, R-7, R-8, R-30 | `trigger_alpha_bot()` `app.py:776-789` as plan 02 rewrites it; plan 02's `CycleRecord` through `_publish_cycle` (replacing the fan-out at `app.py:726-773`); `_log_skipped_minute(record)` replaced (RC-4 in `approach/00-suite.md`) | new `tests/realtime_push/test_cycle_log_line.py`, patching plan 02's `_now_et` and `_monotonic`, using `_make_completed_process` (`tests/realtime_push/test_cycle_completion_event.py:26`) and the `subprocess.run` patch pattern at `:38-57`; `FakeClock` and `capture_timing_records` from C1's `tests/_timing_capture.py`; fixture: the stub `CompletedProcess` and `CalledProcessError` exit codes, no producer data | C4 |
| AC-3 | R-1, R-8, R-30, R-96 | `app.py:843` inside `_refresh_account_totals()` `app.py:813-910`; the failure lines at `app.py:904-909` and `app.py:6407`; `advisors/composer_series_client.py:255` inside `_fetch_with_retry()` `:243-293` | new `tests/app/test_composer_call_latency_lines.py` (mocks `app_module.requests.get` as `tests/app/test_account_totals_timeout_logging.py:62` does; S-3.8 through the flush-resync client at `tests/app/test_flush_resync.py:45`); `tests/advisors/test_composer_series_client.py:803` extended with the `client_mod` fixture (`:119`), `_Http` (`:96`) and `_fetch` (`:139`); fixture: `tests/fixtures/composer/v3-audit/total_stats.json` (captured from the producer, 2026-06-04) reused by path, with the 503 and timeout derivations recorded in `tests/fixtures/refresh_timing/05-c2/PROVENANCE.md` (RC-7), signed off by the PM (SD-7, PM-5) | C2 |
| AC-4 | R-1, R-7, R-8, R-30, R-89 | new `timing_log.AccessLogMiddleware` installed at `app.py:415-416`: no WSGI middleware or after-request hook exists (`grep -n "wsgi_app\|after_request\|teardown_request" app.py` finds none) | new `tests/app/test_access_log_lines.py` with the Flask test client; auth re-enabled per `tests/app/test_health_endpoint.py:45`; SSE client from `tests/realtime_push/conftest.py:101`; settings and chart harness from `tests/app/test_app_routes.py:44`, `:51`, `:71`; fixtures: the test client's real responses | C3 |
| AC-5 | R-1, R-7, R-32, R-89 | new `deploy/caddy/Caddyfile`, following `deploy/planetstopper.service.d/memory.conf`; `docs/DEPLOYMENT.md:113-125` (Step 4's Caddyfile block) | new `tests/docs/test_caddyfile_deploy_docs.py` following the `docs/DEPLOYMENT.md` content pin at `tests/advisors/test_weekly_suggestions_orchestrator.py:40`, `:297`; fixture: the tracked deploy file itself; live: the curl, DevTools and openssl reads in C5's table | C5 |
| AC-6 | R-1, R-8, R-82 | new: `.claude/review/<stack-head-sha12>/live/section8-measurements.md`, a record; the unknowns are listed at `.claude/review/37c8e983b64c/timing-audit/TIMING-AUDIT.md:897-911` and no record of them exists | none in code: the PM's read-only droplet commands named in C6's table, each recorded with its output | C6 |

## Execution Model

Each cycle C1 to C5 is one Toxic Pair (a fresh `test-writer` running `/tdd` and a fresh `implementer` running `/tdd-implement`) in its own isolated worktree on its own branch, named and set up per RC-8 (worktree under `.claude/worktrees/`, `.env` copied in, `git rev-parse --show-toplevel` and `git rev-parse --abbrev-ref HEAD` checked before the first edit), never the primary checkout. At each cycle-complete the PM dispatches `qa`, `code-reviewer` and `pipelines-expert` in parallel against that head:
- `qa` judges the criteria; `code-reviewer` judges code craft on every head, since every cycle changes code or a test.
- `pipelines-expert` on every head: C1 changes the daemon's output stream, C2 the scheduler's and the retirement thread's Composer calls, C3 the streaming response path, C4 the scheduler's spawn path, and C5 the proxy in front of the event stream (DC-3).
- No `ux-expert` or capturer: no head renders UI. No `api-expert` or integration auditor: no route contract, response body, header or cross-repo contract changes.
- The plan is Sensitive (security, overview SD-4), so one `qa` dispatch in hidden mode is added.
- One doc-gen runs at the slice's tail, after every reviewer passes every head, and owns `docs/DEPLOYMENT.md`'s reading-the-lines note, `docs/generated/app.md`, a new `docs/generated/timing_log.md`, `docs/generated/INDEX.md`, `README.md`, the `DECISIONS.md` entry and the drafted `.claude/CLAUDE.md` Key Files row.

Merges follow RC-9 and are never the PM's by hand. C4's base merge (02 C2's passed head into C4's branch) is done by C4's own pair as its first commit, before RED: the `implementer` resolves production files and commits the merge naming both input SHAs, the `test-writer` resolves test files and runs the slices of every file either side touched. Entering C1 to C5, plan 02's tip, `main` with model-unpin, and `refresh-timing/fixtures` into the assembly branch `refresh-timing/05` is one RC-9 integration task by a fresh `test-writer` and `implementer` pair; that pair is counted in the suite's team size, not below. An integration head that resolved a conflict in production code gets one `qa` and one `code-reviewer` before the PR (RC-9).

C6 is the PM's own post-deploy measurement record, not a pair and not a builder round; it is excluded from the sizing below.

Team size: 21 = 5 test-writer + 5 implementer + 2 qa + 1 qa (hidden mode) + 2 code-reviewer + 5 pipelines-expert + 1 doc-gen

Test-run governor (RC-11): every run is a named slice expected under 240 s, launched as a background task with output to a file, never detached. The cycle's own files run with `-n 0`; consumer shards run with at most `-n 4` under the conftest memory cap, never `-n auto`. A slice whose wall time reaches 240 s is split by file or class before it runs again. A consumer sweep runs once per round, after GREEN, never on each RED or GREEN iteration; CI on the pushed branch is the full-tree evidence.

## Dispatch Ordering

| Cycle | Consumes (cycle: committed artifact) | Branch base |
|---|---|---|
| C1 | | `main` at 37c8e983 |
| C2 | C1: `timing_log.py` (`timing_logger`, `clock`, `emit`) and `tests/_timing_capture.py` | C1's branch at its cycle-complete SHA |
| C3 | C1: `timing_log.py` (`timing_logger`, `clock`, `emit`, the sanitizer) and `tests/_timing_capture.py` | C1's branch at its cycle-complete SHA |
| C4 | C1: `timing_log.py` and `tests/_timing_capture.py`; plan-02: `CycleRecord`, `_publish_cycle`, `_engine_run_state()`, `_begin_engine_run()`, `_end_engine_run()`, `_now_et`, `_monotonic` and `_log_skipped_minute(record)` from 02 C2's passed head (which carries 02 C1 and C3; RC-4) | C1's branch at its cycle-complete SHA, with 02 C2's passed head merged in as C4's RC-9 base merge (by C4's own pair, before RED; both SHAs named in the merge commit) |
| C5 | | `main` at 37c8e983 |
| C6 | C1: daemon output; C2: Composer lines; C3: access lines; C4: cycle lines; C5: `deploy/caddy/Caddyfile`, all merged to `main` and deployed with the rest of the stack | `main` after the stack deploys (a record, no branch) |

Branches are `refresh-timing/05-c1` to `refresh-timing/05-c5`, assembled into `refresh-timing/05` (RC-8). Every merge is a merge commit by a test-writer and implementer pair, never a rebase and never the PM by hand (RC-9). Plan 02 merges to `main` before this PR (RC-8 order: 02, then 05).

## Per-Cycle Detail

### C1: daemon output and the shared emitter (AC-1)
- Files added: `timing_log.py` (`TIMING_LOGGER_NAME`, `timing_logger`, `clock`, `make_daemon_stdout`, `attach_stream_handler`, `configure_daemon_output`, `emit`, the sanitizer); `tests/_timing_capture.py` (a `capture_timing_records()` context manager that attaches a list handler to `planetstopper.timing` at INFO and restores the logger's handlers and level on exit, and a `FakeClock` with an optional per-thread mode that tests install over `timing_log.clock`; the `tests/_mem_cap.py` top-level helper is the precedent; it is a test helper, not a fixture, so it lives outside RC-7's fixture folders); `tests/app/test_daemon_output_line_buffering.py`.
- Files modified: `app.py:9001` (the `__main__` rewrap becomes `timing_log.configure_daemon_output()`); `app.py` imports `timing_log` at module level beside `import model_config` (`app.py:35`).
- Primitives imported: `io.TextIOWrapper`, `logging.StreamHandler`, `time.monotonic`.
- First production use: the daemon's `__main__` (`app.py:8997`).

### C2: Composer call latency lines and the two failure lines (AC-3)
- Files modified: `app.py:590` (new `_TOTAL_STATS_LOG_ROUTE` beside `_ACCOUNT_TOTALS_HTTP_TIMEOUT_S`); `app.py:843` (the timed `requests.get` and its two `emit` paths inside `_refresh_account_totals()`, `app.py:813-910`); `app.py:904-909` and `app.py:6407` (type name at ERROR, detail at DEBUG, S-3.7 and S-3.8); `advisors/composer_series_client.py:66` (adds `import timing_log`), `:255` (the timed attempt and its `emit`).
- Files added: `tests/app/test_composer_call_latency_lines.py`; `tests/fixtures/refresh_timing/05-c2/PROVENANCE.md` (RC-7: one row per AC-3 case, naming the source record, the changed field, the reason, the scenario and the PM's sign-off) and its entry in the root `tests/fixtures/refresh_timing/PROVENANCE.md`. No record file is copied: the 200 body is reused by path (RC-7 source 2), and the 503 and timeout cases change only the status or the raised exception, which the test applies to the reused record.
- Tests extended: `tests/advisors/test_composer_series_client.py:803` (a latency class beside `TestD1ErrorDisclosure`). `tests/app/test_account_totals_timeout_logging.py:131` and `:153` keep passing on the DEBUG detail record; their docstrings are updated to say the traceback now sits at DEBUG.
- Primitives imported: C1's `timing_log.emit` and `timing_log.clock`.
- First production use: the `:00` scheduler tick (`app.py:1429`) and the 03:45 retirement tick's series fetches (`advisors/retirement_recommender.py:608`).

### C3: access lines (AC-4)
- Files modified: `timing_log.py` (adds `AccessLogMiddleware` and the query masker); `app.py:415-416` (installs `app.wsgi_app = timing_log.AccessLogMiddleware(app.wsgi_app)`; `werkzeug` stays at ERROR).
- Files added: `tests/app/test_access_log_lines.py`.
- Primitives imported: `urllib.parse.parse_qsl`, C1's `emit`, sanitizer and `clock`.
- First production use: every request the daemon answers, `/api/state` (`app.py:2513`) and `/api/events` (`app.py:2477`) included.

### C4: one cycle line per engine run (AC-2)
- Files modified: `app.py:776-789` (`trigger_alpha_bot()` as plan 02 leaves it: the exit code and the `emit("cycle", ...)` from the `CycleRecord` handed to `_publish_cycle`, in the same `finally`); plan 02's `_log_skipped_minute(record)` (its body becomes the skipped form of the cycle line, so each skip has one line).
- Files added: `tests/realtime_push/test_cycle_log_line.py`.
- Tests updated: plan 02's S-5.1 test of `_log_skipped_minute`, to read the unified line (the test-writer owns the change).
- Primitives imported: C1's `emit`; plan 02's `CycleRecord`, `_engine_run_state()`, `_now_et` and `_monotonic`.
- First production use: the per-minute `threaded_trigger` (`app.py:1428`).

### C5: Caddy compression, versioned (AC-5)
- Files added: `deploy/caddy/Caddyfile` (the site block with its explicit `match`, plus a header comment with the apply, verify and rollback steps); `tests/docs/test_caddyfile_deploy_docs.py`.
- Files modified: `docs/DEPLOYMENT.md:113-125` (Step 4's block gains the `encode gzip zstd` block with the event stream excluded, and the apply, verify and rollback procedure, applied before merge from the PR head).
- Primitives imported: none.
- First production use: the PM's apply on the droplet before merge (Architecture, AC-5; PM-2).

### C6: the section 8 record (AC-6)
- Files added: `.claude/review/<stack-head-sha12>/live/section8-measurements.md` (PM, after deploy).
- No code, no branch.

## Per-Cycle Test Plan

Fixtures follow RC-7. Only C2 keeps a folder, `tests/fixtures/refresh_timing/05-c2/`, for its provenance rows; C1, C4 and C5 use no recorded data, C3 uses producer output at test time (the Flask test client's real responses, RC-7 source 4, never committed), and C6 records live reads in the review folder. A cycle that finds it needs a recorded case adds `tests/fixtures/refresh_timing/05-c<K>/` with its own `PROVENANCE.md`.

Census of the entry points that reach a matrix rule (AC-3, AC-4), each a column of the plan's two matrix tables. Each is a round-1 RED row in the cycle that owns its rule. The middleware wraps every request, so every route reaches AC-4.

| Entry point | Rules |
|---|---|
| `GET /api/state` (`app.py:2513`) | AC-4 |
| `POST /login` (`app.py:241`) | AC-4 |
| `/api/events` (`app.py:2477`) | AC-4 |
| `GET /` and other pages (`/history`, `/performance`, `/ai-advisor`, `/settings`) | AC-4 |
| Composer account totals: the scheduler tick (`app.py:1429`), the post-cycle thread (`app.py:763`) and the flush-resync thread (`app.py:6403`), all through `app.py:843` | AC-3 |
| Composer retirement series: the 03:45 tick and `POST /ai-advisor/retirement/run`, through `advisors/retirement_recommender.py:608` to `advisors/composer_series_client.py:255` | AC-3 |
| Other POST routes: `POST /api/settings` and the other mutating routes (per-symphony save `app.py:6219`, sell account, flush-resync `app.py:6308`, chat send, sleeves, approvals) | AC-4 |
| Static assets and polled JSON GET routes: `/static/<path>`, `/api/chart/<symphony_id>` (`app.py:4846`), `/api/history/<int:days>` (`app.py:5046`), `/health` (`app.py:393`) | AC-4 |

### C1
Slice: `pytest -n 0 tests/app/test_daemon_output_line_buffering.py tests/execution/test_reliability_expansion.py -k "line_buffering or trigger_alpha_bot"`.

| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-1.1 | AC-1 | unit (in process) and subprocess | `trigger_alpha_bot()` printing through `make_daemon_stdout` over a recording raw buffer; a Popen child running `configure_daemon_output()` then printing and sleeping 30 s | new: `tests/app/test_daemon_output_line_buffering.py` (no stdout test exists; subprocess pattern from `tests/advisors/test_prism_dotenv_hardening.py:54`) | none recorded: the printed line is the code's own; the child's `DB_PATH` points at `tmp_path` |
| S-1.2 | AC-1 | integration (real child process) | `trigger_alpha_bot()` with `subprocess.run` wrapped to swap argv for a stub child that rewraps stdout exactly as `alpha_bot_execution.py:12` does, prints "Alpha Bot Waking Up..." and exits; `LOG_FILE` patched to `tmp_path` | `tests/execution/test_reliability_expansion.py:869` | the stub child's source, copied from `alpha_bot_execution.py:12` and `:616` |
| S-1.3 | AC-1 | unit (in process) | `trigger_alpha_bot()` with `subprocess.run` raising `CalledProcessError(1, ...)`, stdout from `make_daemon_stdout` over a recording raw buffer | new: `tests/app/test_daemon_output_line_buffering.py` | `CalledProcessError` from the stdlib; `_notify_cycle_complete` patched as at `tests/realtime_push/test_cycle_completion_event.py:59` |
| S-1.4 | AC-1 | unit | `make_daemon_stdout` writing "→" and a lone surrogate | new: `tests/app/test_daemon_output_line_buffering.py` | literal characters; expected bytes derived with `str.encode("utf-8", "replace")` in the test |
| S-1.5 | AC-1 | subprocess | a Popen child running `configure_daemon_output()`, printing two lines and one `timing_log.emit` line, then sleeping; the parent reads all three, then calls `proc.kill()` and reads to EOF | new: `tests/app/test_daemon_output_line_buffering.py` | none: the child's own literals; the kill targets the test's own helper child, never a fault signal |
| S-1.6 | AC-1 | unit plus cycle-close diff | `trigger_alpha_bot()` passing the log file handle as `stdout` and `stderr`; `git diff main...HEAD -- alpha_bot_execution.py` empty | `tests/execution/test_reliability_expansion.py:833` | the existing mocks at `:833-900` |

### C2
Slice A: `pytest -n 0 tests/app/test_composer_call_latency_lines.py tests/app/test_account_totals_timeout_logging.py tests/app/test_account_totals_cache.py tests/app/test_flush_resync.py`. Slice B: `pytest -n 0 tests/advisors/test_composer_series_client.py tests/advisors/test_retirement_recommender_composer_source.py tests/security/test_retirement_action_no_trade_boundary.py`.

| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-3.1 | AC-3 | unit | `_refresh_account_totals()` with `requests.get` mocked to advance a `FakeClock` by the fixture's duration and return 200 | new: `tests/app/test_composer_call_latency_lines.py` (mock boundary as at `tests/app/test_account_totals_timeout_logging.py:62`) | `tests/fixtures/composer/v3-audit/total_stats.json` (captured from the producer, 2026-06-04) as the 200 body, reused by path; row in `tests/fixtures/refresh_timing/05-c2/PROVENANCE.md` |
| S-3.2 | AC-3 | unit | `_refresh_account_totals()` with `requests.get` advancing the clock by the timeout then raising `ReadTimeout` | new: `tests/app/test_composer_call_latency_lines.py`; `tests/app/test_account_totals_timeout_logging.py:62` stays green (one `alphabot` record) | timeout case derived from the captured record with only the raised exception changed; row in `tests/fixtures/refresh_timing/05-c2/PROVENANCE.md` with the PM's sign-off (SD-7, PM-5) |
| S-3.3 | AC-3 | unit | `_refresh_account_totals()` with `requests.get` returning 503 | new: `tests/app/test_composer_call_latency_lines.py` | the captured record with only the status changed to 503; row in `tests/fixtures/refresh_timing/05-c2/PROVENANCE.md` with the PM's sign-off (SD-7, PM-5) |
| S-3.4 | AC-3 | unit | both call sites with a UUID-shaped account id, key id and secret in the env (`app_module.dotenv_values` patched) and `requests.get` asserted to receive the id in its URL | new: `tests/app/test_composer_call_latency_lines.py`, plus the series case at `tests/advisors/test_composer_series_client.py:803` | values in the real formats (a UUID account id, a UUID-shaped key id, a 40-character secret); the captured 200 body |
| S-3.5 | AC-3 | unit | `fetch_symphony_daily_returns()` through the `client_mod` fixture, three attempts (503, timeout, 200), one line each | `tests/advisors/test_composer_series_client.py:803` (fixtures `:119`, `:96`, `:139`, `:642`) | the file's `_good_resp()` (`:642`); the asserted line reads no body field, so the body's `synthetic-from-docs` provenance reaches no assertion |
| S-3.6 | AC-3 | unit (two threads) | two concurrent `_refresh_account_totals()` calls held together by a `threading.Barrier` inside the mocked `requests.get`, each advancing its own per-thread `FakeClock` | new: `tests/app/test_composer_call_latency_lines.py` | the captured 200 body; the two durations are distinct test literals |
| S-3.7 | AC-3 | unit | `_refresh_account_totals()` with `requests.get` raising `requests.exceptions.ConnectionError` whose message carries the total-stats URL with a UUID account id; captured at DEBUG on `alphabot` | new: `tests/app/test_composer_call_latency_lines.py`; `tests/app/test_account_totals_timeout_logging.py:131`, `:153` stay green on the DEBUG record | a UUID account id; asserts every WARNING-or-higher record's text is the type name only, with no `exc_info`, no URL and no id, and that a DEBUG record carries the message and `exc_info` |
| S-3.8 | AC-3 | integration (Flask test client) | `POST /api/settings/flush-resync` with `app_module.threading.Thread` replaced by a class whose `start()` raises `RuntimeError` carrying a marker message | new: `tests/app/test_composer_call_latency_lines.py` (client and patches as at `tests/app/test_flush_resync.py:45`, `:141`) | a marker message; asserts the ERROR record names `RuntimeError` only and the marker appears only at DEBUG |

### C3
Slice A: `pytest -n 0 tests/app/test_access_log_lines.py tests/realtime_push tests/app/test_health_endpoint.py tests/app/test_app_routes.py tests/app/test_dashboard_auth.py tests/security/test_a3_csrf_protection.py tests/security/test_d1_error_disclosure.py`. Consumer shards, once per round after GREEN, each at most `-n 4`: `tests/app/test_[a-d]*.py`, `tests/app/test_[e-l]*.py`, `tests/app/test_[m-r]*.py`, `tests/app/test_[s-z]*.py`, `tests/dashboard`, `tests/ui`, then `tests/security`.

| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-4.1 | AC-4 | integration (Flask test client, `buffered=True`) | `GET /api/state` with `get_api_state_dict` stubbed as `tests/realtime_push/conftest.py:92` does | new: `tests/app/test_access_log_lines.py` | the test client's real response; `bytes` asserted equal to `len(response.data)`, never a literal |
| S-4.2 | AC-4 | integration | unauthenticated `GET /api/state` with `_auth_check_enabled` set back to True and `DASHBOARD_PASSWORD` and `SECRET_KEY` set, as `tests/app/test_health_endpoint.py:45` does | new: `tests/app/test_access_log_lines.py` | the real 401 response |
| S-4.3 | AC-4 | integration | `POST /login` with a marker password and `csrf_token` form field, a `session` cookie and an `X-CSRF-Token` header; a second request with only the cookie | new: `tests/app/test_access_log_lines.py` (login pattern at `tests/app/test_dashboard_auth.py:87`) | distinct marker strings; each asserted absent from every captured message and its args |
| S-4.4 | AC-4 | integration | `GET /api/state?account=1a2b3c4d-0000-4000-8000-000000000000`, plus a path holding a space and a CR and LF pair | new: `tests/app/test_access_log_lines.py` | the plan's UUID; the line asserted to hold `account=***` and no raw CR or LF |
| S-4.5 | AC-4 | integration (streaming) | `client.get("/api/events")` from `tests/realtime_push/conftest.py:101`, open line asserted at once; the `FakeClock` advanced 720 s; `response.close()`; the closed line and the SSE client queue's removal asserted | new: `tests/app/test_access_log_lines.py` | the real stream; the duration is the clock advance |
| S-4.6 | AC-4 | integration | a GET whose view raises: `app.view_functions["health"]` replaced by a raiser through `monkeypatch.setitem`, `PROPAGATE_EXCEPTIONS` set False through `monkeypatch.setitem(app.config, ...)` | new: `tests/app/test_access_log_lines.py` | the real 500 response |
| S-4.7 | AC-4 | integration (threads) | 30 concurrent `GET /health` requests, one test client per thread, `database.load_state` patched | new: `tests/app/test_access_log_lines.py` (client fixture at `tests/app/test_health_endpoint.py:38`) | the real responses; 30 whole lines asserted |
| S-4.8 | AC-4 | integration | `POST /api/settings` with a JSON body holding a marker value and an `X-CSRF-Token` header holding another, `set_key` faked and `ENV_FILE_PATH` isolated as at `tests/app/test_app_routes.py:445-473` and `:71` | new: `tests/app/test_access_log_lines.py` | the real response; both markers asserted absent; `bytes` equal to `len(response.data)` |
| S-4.9 | AC-4 | integration | `GET /static/index.js` (a real tracked file, auth-exempt endpoint `static`) and `GET /api/chart/<symphony_id>` with `mock_database` (`tests/app/test_app_routes.py:51`, `:733`) | new: `tests/app/test_access_log_lines.py` | the real responses; a symphony id in Composer's 20-character alphanumeric form; `bytes` from each response |

### C4
Slice: `pytest -n 0 tests/realtime_push/test_cycle_log_line.py tests/realtime_push/test_cycle_completion_event.py`, plus the files where plan 02 commits its `_publish_cycle` and `_log_skipped_minute` tests (named in 02's approach), plus `tests/execution/test_reliability_expansion.py -k trigger_alpha_bot`.

| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-2.1 | AC-2 | unit | `trigger_alpha_bot()` with `subprocess.run` advancing plan 02's patched `_monotonic` by the run length and returning `_make_completed_process(0)`, plan 02's outcome resolving to "cycle" | new: `tests/realtime_push/test_cycle_log_line.py` (helper `tests/realtime_push/test_cycle_completion_event.py:26`) | stub exit code; plan 02's record, read back, never re-typed |
| S-2.2 | AC-2 | unit | as S-2.1 with plan 02's closed-market inputs | new: `tests/realtime_push/test_cycle_log_line.py` | plan 02's own closed-market test inputs |
| S-2.3 | AC-2 | unit | `_log_skipped_minute(record)` while `_engine_run_state()` reports a run begun through `_begin_engine_run()` 75 s earlier on the patched `_monotonic` | new: `tests/realtime_push/test_cycle_log_line.py` | plan 02's running-engine state, set through `_begin_engine_run()` |
| S-2.4 | AC-2 | unit | `subprocess.run` raising `CalledProcessError(3, ...)` | new: `tests/realtime_push/test_cycle_log_line.py` (pattern `tests/realtime_push/test_cycle_completion_event.py:59`) | the stdlib exception; the exit code read from it |
| S-2.5 | AC-2 | unit | one run with a registered SSE client queue; the payload `_publish_cycle` queues and the cycle line compared field by field | new: `tests/realtime_push/test_cycle_log_line.py` (queue pattern `tests/realtime_push/test_cycle_completion_event.py:84`) | the run's own record |
| S-2.6 | AC-2 | unit | a run whose `_now_et` reads 2026-10-22 23:59:50Z at start and 2026-10-23 00:00:28Z at end while `_monotonic` advances by the same span | new: `tests/realtime_push/test_cycle_log_line.py` | the two instants are test literals; the expected duration is computed from them in the test |

### C5
Slice: `pytest -n 0 tests/docs/test_caddyfile_deploy_docs.py tests/advisors/test_weekly_suggestions_orchestrator.py -k "deployment or caddy"`.

| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-5.1 | AC-5 | live (PM, after the pre-merge apply) | `curl -s -o /dev/null -D - -H 'Accept-Encoding: gzip' -H 'Cookie: session=<redacted>' -w '%{size_download}\n' https://104-248-7-101.sslip.io/api/state` after 16:05 ET: proves `content-encoding: gzip` and a transferred size under 1,000,000 B | new: live record in `.claude/review/<pr-head-sha12>/live/` (Caddy cannot run in a unit test) | production response, read-only |
| S-5.2 | AC-5 | live (PM) | the same curl with no `Accept-Encoding`: proves no `content-encoding` header and the uncompressed size | new: live record in `.claude/review/<pr-head-sha12>/live/` | production response, read-only |
| S-5.3 | AC-5 | live (PM) | DevTools on `/` (the `/api/events` response headers and its EventStream tab), plus `curl -sN -D /dev/stderr -H 'Accept-Encoding: gzip, zstd' -H 'Cookie: session=<redacted>' https://104-248-7-101.sslip.io/api/events` with each line stamped by `date -u +%T.%N` and compared with the journal's `cycle` line `ended_at`: proves the response has no `content-encoding` header although the client offered both encodings, and that each `cycle-complete` arrives as plain text within 2 s | new: live record in `.claude/review/<pr-head-sha12>/live/` | production stream, read-only |
| S-5.4 | AC-5 | unit (document check) plus live `cmp` | the test extracts the fenced block under Step 4 of `docs/DEPLOYMENT.md` and compares it with `deploy/caddy/Caddyfile` with comments dropped and the site name normalized; it asserts both carry `encode gzip zstd` with gzip first, a `match` block, no `text/*` wildcard and no `text/event-stream` among the matched types; live, after the pre-merge apply: `cmp /etc/caddy/Caddyfile /opt/planetstopper/deploy/caddy/Caddyfile` is silent | new: `tests/docs/test_caddyfile_deploy_docs.py` (pin pattern `tests/advisors/test_weekly_suggestions_orchestrator.py:297`) | the tracked deploy file and the docs, both read from the tree |
| S-5.5 | AC-5 | live (PM) | `echo \| openssl s_client -connect 104-248-7-101.sslip.io:443 -servername 104-248-7-101.sslip.io 2>/dev/null \| openssl x509 -noout -fingerprint -sha256 -enddate` before and after the reload, and `curl -s https://104-248-7-101.sslip.io/health`: proves the same certificate and that the proxy still reaches `localhost:8090` | new: live record in `.claude/review/<pr-head-sha12>/live/` | production TLS and `/health`, read-only |
| S-5.6 | AC-5 | live (PM) | `caddy validate --config /tmp/Caddyfile.broken --adapter caddyfile; echo $?` on a copy with its closing brace removed, then `systemctl show caddy -p ActiveEnterTimestamp` and `curl -sI https://104-248-7-101.sslip.io/health` unchanged: proves validation fails and the running config is untouched | new: live record in `.claude/review/<pr-head-sha12>/live/` | a throwaway file in `/tmp`, never `/etc/caddy` |

### C6
Every command runs on the droplet, read-only, after the stack deploys; journal windows are in UTC (16:05 ET is 20:05 UTC until 2026-11-01 and 21:05 UTC after it); database reads run as the service user (`runuser -u planetstopper --`) so no root-owned `-shm` file appears; the session cookie is never written into the record.

| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-6.1 | AC-6 | live (PM) | `journalctl -u planetstopper -o cat --since "<date> 13:30" --until "<date> 20:00" \| grep ' access GET /api/state ' \| tail -5`, the same after 16:05 ET, and section 8's `curl -s -o /dev/null -H 'Cookie: session=<redacted>' -w '%{time_total} %{size_download}\n' https://104-248-7-101.sslip.io/api/state` five times each: proves route time and size open and closed, and the line count per minute shows journal volume | new: `.claude/review/<stack-head-sha12>/live/section8-measurements.md` (a record) | production journal, read-only |
| S-6.2 | AC-6 | live (PM) | `journalctl -u planetstopper -o cat --since "<date> 00:00" --until "<date+1> 00:00" \| grep ' cycle '`, `duration_s` median and maximum by `sort -n`, and `grep -c 'outcome=skipped'`; section 8's `grep -c "Overlap Detected" /opt/planetstopper/alphabot_daemon.log`: proves the engine cycle duration and how often a minute is skipped | new: `.claude/review/<stack-head-sha12>/live/section8-measurements.md` | production journal and log, read-only |
| S-6.3 | AC-6 | live (PM) | `journalctl ... \| grep ' composer GET /portfolio/accounts/{account}/total-stats '`, `ms` median and maximum, and counts of `error=ReadTimeout` and `status=503`: proves Composer `total-stats` latency and its failure rate | new: `.claude/review/<stack-head-sha12>/live/section8-measurements.md` | production journal, read-only |
| S-6.4 | AC-6 | live (PM, browser) | `systemctl restart planetstopper` with `/` open (a moment with no open position at risk), DevTools Network filtered to `events`, and `journalctl -u planetstopper --since "-10 min" -o cat \| grep ' access GET /api/events '`: proves whether a second stream opened and when events resumed | new: `.claude/review/<stack-head-sha12>/live/section8-measurements.md` | production browser and journal |
| S-6.5 | AC-6 | live (PM) | `runuser -u planetstopper -- sqlite3 -readonly /opt/planetstopper/alphabot_state.db "select json_extract(data,'$.last_market_close_snapshot.trading_day') from bot_state where id=1"` after 16:10 ET on three trading days (a `python3` read with `?mode=ro` when `sqlite3` is absent): proves how often the close snapshot is missed | new: `.claude/review/<stack-head-sha12>/live/section8-measurements.md` | production state DB, read-only |
| S-6.6 | AC-6 | live (PM) | two `curl -s -H 'Cookie: session=<redacted>' https://104-248-7-101.sslip.io/api/state` reads one cycle apart in market hours, each piped to `python3 -c` printing every `bot_state` entry's `guard_alpha` where it is set: proves the verdict figure's drift per cycle | new: `.claude/review/<stack-head-sha12>/live/section8-measurements.md` | production responses, read-only |
| S-6.7 | AC-6 | live (PM) | `journalctl ... -o cat \| grep ' access GET /api/state ' \| grep -v ' status=2' \| grep -o 'status=[0-9]*' \| sort \| uniq -c` over one day: proves how often `/api/state` answers non-OK, by status | new: `.claude/review/<stack-head-sha12>/live/section8-measurements.md` | production journal, read-only |
| S-6.8 | AC-6 | live (PM) | `grep EXECUTION_START_TIME /opt/planetstopper/.env`: proves the droplet's start time (only that line is recorded) | new: `.claude/review/<stack-head-sha12>/live/section8-measurements.md` | production `.env`, read-only |
| S-6.9 | AC-6 | live (PM) | any row above that cannot be taken on the day: the record says "not measured", the reason, and the next date to try | new: `.claude/review/<stack-head-sha12>/live/section8-measurements.md` | none: a recorded gap |
| S-6.10 | AC-6 | live (PM) | the record lists each command verbatim; none writes the database, `.env` or any file outside `/tmp`, and the S-6.4 restart is the only state change | new: `.claude/review/<stack-head-sha12>/live/section8-measurements.md` | the record itself |
| S-6.11 | AC-6 | live (PM, browser) | with ux-overhaul 05 C3 round 2 on `main`, DevTools throttling holds a timer poll while an event poll is issued and answered first: proves the newer response is kept (TA-11); without it on `main`, S-6.9's "not measured" | new: `.claude/review/<stack-head-sha12>/live/section8-measurements.md` | production browser |
| S-6.12 | AC-6 | live (PM, browser) | `/` in a real browser: three refreshes, the tab hidden for 10 minutes, the 1Y window picked; notes the drawn charts, the animation, the catch-up request in DevTools and the headline's window | new: `.claude/review/<stack-head-sha12>/live/section8-measurements.md` | production browser |
| S-6.13 | AC-6 | live (PM) | the S-6.1, S-6.3 and S-6.6 reads repeated before 16:00 ET: proves the open-branch `/api/state` size, the post-cycle account-basis chip and the mid-cycle pairing | new: `.claude/review/<stack-head-sha12>/live/section8-measurements.md` | production journal and responses, read-only |

## Per-Cycle Risk Register

| Cycle | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| C1 | An in-process test attaches a handler to the global timing logger or swaps `sys.stdout` and leaks into later tests | Medium | Medium | In-process tests use the pure `make_daemon_stdout` over a recording buffer; `capture_timing_records()` restores handlers and level; `configure_daemon_output()` itself runs only in a subprocess child |
| C1 | The subprocess timing check flakes on a loaded CI runner | Medium | Low | The test asserts order (the line arrives while the child is still sleeping, before the kill), never a tight wall-clock bound; the 2 s figure is proven live with `journalctl -o short-iso-precise` |
| C1 | The replaced wrapper is collected and closes stdout (`app.py:9001` rebinds `sys.stdout`) | Low | High | `sys.__stdout__` keeps the original wrapper alive, as at the ref; S-1.5's child proves output survives the rebind |
| C2 | The new line breaks the F-010 test that asserts exactly one record under `alphabot` (`tests/app/test_account_totals_timeout_logging.py:62`) | High if under `alphabot` | Medium | The timing logger is `planetstopper.timing` with `propagate = False`; the F-010 file is in C2's slice |
| C2 | The Group G no-trade scan fails on the series client's new code (`tests/security/test_retirement_action_no_trade_boundary.py:893-916` forbids `urllib` and the attributes `post`, `put`, `patch`, `delete`, `request`, `send`) | Medium | Medium | The series client adds only `import timing_log` and `timing_log.emit` and `timing_log.clock` calls; the scan is in C2's slice B |
| C2 | An account id reaches a line through the URL or an exception message (`app.py:837` builds the URL with the id) | Medium | High (security) | The line takes the route template constant and `type(exc).__name__` only; S-3.4 asserts the id was in the request URL and is absent from the line |
| C2 | Moving the traceback to DEBUG breaks the two F-010 tests that pin it (`tests/app/test_account_totals_timeout_logging.py:131`, `:153`) | Medium | Low | Both capture at DEBUG and accept any record with `exc_info`, so the DEBUG detail record satisfies them; both are in C2's slice A |
| C2 | S-3.8's failure branch (`app.py:6406`) is unreachable through `_refresh_account_totals`, which never raises, so a test that makes the refresh raise proves nothing | High | Medium | The test makes `threading.Thread.start` raise, the one path into that branch |
| C3 | The middleware breaks every route: about 7,000 route tests and the SSE cleanup run through it (`app.py:2497-2504`, `tests/realtime_push/test_sse_generator_close.py`) | Medium | High | The wrapper never consumes eagerly and forwards `close()` before logging; `emit` and the wrapper's logging never raise; the consumer shards run once per round |
| C3 | Under `TESTING`, Flask re-raises route exceptions, so a 500 is never produced in tests | High | Low | S-4.6 sets `PROPAGATE_EXCEPTIONS` False; the middleware also logs and re-raises an exception that escapes `wsgi_app` |
| C3 | The test client defers `close()` for a non-streamed response, so the line is not yet written when asserted | High | Low | Non-stream tests pass `buffered=True`, which closes inside the call; S-4.5 closes the stream explicitly |
| C3 | A stream's close is seen only at the next failed write, up to the 15 s heartbeat (`app.py:2493`), so `open_s` reads up to 15 s long | High | Low | Documented in `docs/DEPLOYMENT.md`'s reading note; plan 02's stream changes may shorten it |
| C3 | A path holding CR, LF or `%` forges or splits a journal line | Low | Medium (security) | One sanitizer percent-encodes every word and value; S-4.4 asserts no raw CR or LF |
| C3 | The added journal volume is unmeasured (plan Known-Unconfirmed) | Medium | Low | S-6.1 counts lines per minute; journald's own rotation bounds the size |
| C4 | Plan 02's committed seam differs from the names the PM ruled (`CycleRecord`, `_publish_cycle`, `_engine_run_state()`, `_begin_engine_run()`, `_end_engine_run()`, `_now_et`, `_monotonic`, `_log_skipped_minute`) | Low | Medium | C4 forks only after 02 C2's passed head is merged in; RC-4 is the reference, and a mismatch goes to the PM as a blocker, never a local rename |
| C4 | Plans 02 (S-5.1) and 05 (S-2.3) each write a skip line, giving two lines per skip | High | Low | C4 replaces the body of `_log_skipped_minute(record)` with the skipped cycle line and updates 02's test to read it |
| C4 | Logging in the trigger thread delays the spawn or the notify | Low | Medium (money, overview SD-4) | The trigger thread is not the engine's execution path (the engine is a subprocess); one INFO write per run; NB-9 holds |
| C5 | The explicit `match` misses a content type the dashboard serves, so that response travels uncompressed | Low | Low | The list covers JSON, HTML, CSS, JS, plain text and SVG; S-5.1 measures the response that matters (`/api/state`); a missed type costs bytes, never correctness |
| C5 | A future edit adds `text/*` to the matcher and compresses the event stream, which may buffer it on v2.11.4 (plan Known-Unconfirmed) | Low | High (push stops) | S-5.4's test fails on any `text/*` wildcard or `text/event-stream` in the matcher; S-5.3 checks the stream's headers live |
| C5 | A browser that accepts both encodings receives zstd, against AC-5's gzip rule | High if zstd is listed first | Medium | `encode gzip zstd` lists gzip first |
| C5 | The droplet's file drifts from the tracked file (DC-5) | Medium | Medium | The PM installs the tracked file verbatim and verifies with `cmp`; S-5.4's test keeps the docs and the tracked file equal |
| C5 | A restart instead of a reload, or an unvalidated file, takes the site down | Low | High | The procedure validates a staged copy, checks `ExecReload`, reloads once, and keeps a dated backup for the one-reload rollback |
| C5 | The pre-merge apply (PM-2) runs a Caddyfile that `main` does not yet hold, and a rejected PR would leave the droplet ahead of `main` | Medium | Low | A rejected PR triggers the one-reload rollback; after the merge, `cmp` against `main`'s tracked file confirms the droplet matches |
| C6 | The DST change on 2026-11-01 shifts every ET-to-UTC window by an hour | High | Low | Windows are given in UTC with both conversions stated |
| C6 | A root-run `sqlite3` read on the WAL database leaves a root-owned `-shm` file | Medium | Medium | Database reads run through `runuser -u planetstopper --` with `-readonly` |

## Cycle-Close Criteria

A cycle's cycle-complete is final only when all of these hold at its head:
- Every scenario in the cycle's table has a passing test or a held row with its reason in `.claude/review/<HEAD-sha12>/records/coverage.json`, and `python3 ~/.claude/scripts/plan-trace check-coverage <coverage.json> --plan feature-plans/refresh-timing/05-observability-transport.md --approach feature-plans/refresh-timing/approach/05-observability-transport.md` is clean; live rows (C5's S-5.1 to S-5.3, S-5.5, S-5.6, the live half of S-5.4, all of C6) are held with "live, PM, pre-merge apply" or "live, PM, after deploy" as the reason.
- `ruff format --check` and `ruff check` are clean (line length 100).
- The cycle's named slices pass, each quoted with its count and the head SHA from `git rev-parse HEAD` in the same turn; C3 also passes its consumer shards once.
- `git diff main...HEAD -- alpha_bot_execution.py math_engine.py` is empty.
- The plan's sibling searches are run and recorded in the coverage map: DC-1, every new log call in `app.py`, `timing_log.py` and `advisors/composer_series_client.py` checked for `headers`, `cookie`, `token`, `password`, `account`, `key` and request bodies; DC-2, `TextIOWrapper` in `app.py` appears only through `timing_log`; DC-3, `/api/events`' headers unchanged; DC-4, `cycle_id` and `outcome` read from one record (C4).
- No new or rewritten WARNING-or-higher log call carries `str(exc)`, `%s` of an exception or `exc_info` (the two rewritten lines at `app.py:904-909` and `app.py:6407` included); exception detail appears only at DEBUG; every new timing line is INFO and carries type names only.
- No live-backend check gates a cycle; those belong to the merge.

## Merge Criteria

One PR from `refresh-timing/05` to `main`, merged with `gh pr merge --merge`, after plan 02's PR has merged (RC-8). The head being merged passes:
1. The RC-9 integration task has entered C1 to C5, `main` holding plan 02 and model-unpin, and `refresh-timing/fixtures` (if any 05 cycle read a parked capture) by merge commits, and `main` is merged into the tip; no unmerged commit of another plan rides to `main`; a conflicted integration head has its `qa` and `code-reviewer` PASS.
2. `ruff format --check`, `ruff check`, and every cycle's slices plus C3's consumer shards on the assembled head, quoted with the head SHA; the overview's NB-14 boundary tests (`tests/security/test_frontrunner_no_trade_boundary.py`, `tests/security/test_retirement_action_no_trade_boundary.py`, `tests/security/test_retirement_recommender_no_trade_boundary.py`, `tests/app/test_sell_account_panic_confirm.py`, `tests/app/test_f003_liquidation_confirmation.py`, `tests/app/test_dashboard_no_order_path.py`) pass.
3. CI green on the exact head SHA; `git diff main...HEAD -- math_engine.py alpha_bot_execution.py` empty.
4. Every reviewer verdict for every head reads PASS; the doc-gen record is present; `/review` (the built-in PR review skill the project mandates) run on the PR with its findings resolved or accepted.
5. The PM's live functional check, because mocked-green has repeatedly diverged from live behaviour here: the PR head deployed to the droplet (`git status` before the pull, `runuser -u planetstopper`, a moment with no open position at risk, NYSE calendar checked), then:
   - AC-1: `journalctl -u planetstopper -o short-iso-precise --since "-10 min" | grep 'Triggering Alpha Bot'` shows each journal stamp within 2 s of its `[HH:MM:SS]`.
   - AC-2 to AC-4: the journal shows one `cycle` line per minute, a `composer GET /portfolio/accounts/{account}/total-stats` line per refresh, and `access` lines for a page load, with no cookie, token, query value or account id (checked with `grep -E 'session=|csrf|Bearer|[0-9a-f]{8}-[0-9a-f]{4}-'` on those lines returning nothing).
   - AC-3 failure lines: `journalctl -u planetstopper -p warning --since "-1 day" -o cat | grep -E '_refresh_account_totals failed|flush_resync: Composer resync failed'` shows type names only, with no URL or account id.
   - AC-5 (PM-2): the PR head's tracked `deploy/caddy/Caddyfile` applied with the procedure in Architecture (validate a staged copy, one reload, `cmp`), then the S-5.1 to S-5.6 reads in C5's table; the one-reload rollback restores the backup if any read fails or the PR is rejected.
   - Recorded in `.claude/review/<pr-head-sha12>/live/`.
6. The C6 record (AC-6) is not a merge gate for this PR: it is taken after the stack deploys and closes the overview's Suite Gate item 4.

## Items Raised to the PM

1. Where the DEBUG detail lands (S-3.7, S-3.8, PM-3). The rule holds: no WARNING-or-higher line carries the exception text. But the DEBUG record still reaches two places. The `alphabot` logger and its file handler are at DEBUG (`app.py:482-486`), so the detail, including a `ConnectionError` message with the account id in its URL, is written to `alphabot_daemon.log`. The root handler that the module-level `logging.info` at `database.py:1827` installs implicitly has no level of its own, so the record also propagates to stderr and the journal. The droplet journal's `WARNING:alphabot:` prefix shows that handler is present. If the intent is to keep the account id out of both, the detail needs a logger that does not propagate and writes to neither; the design follows the plan as written until the PM rules.
2. The flush-resync route also returns the exception text in its JSON response: `errors.append(f"resync: {exc}")` at `app.py:6408` goes into the body at `app.py:6430`. S-3.8 covers only the log line, so the design leaves the response as it is; it is one backlog line unless the PM rules it in.
