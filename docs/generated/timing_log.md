# timing_log

> Timing lines for the daemon: one safe, greppable line per engine run, per Composer call and per HTTP request, written to the daemon's stdout so it reaches the journal line by line.

**Source:** `timing_log.py`
**Last updated:** 2026-11-02

## Overview

Before this module the daemon's stdout was block-buffered, so lines printed over several hours reached the journal at once. `configure_daemon_output()` replaces `sys.stdout` with a UTF-8 text wrapper that flushes at every newline (`make_daemon_stdout`) and attaches a stream handler to the `planetstopper.timing` logger. That logger sits outside the `alphabot` hierarchy and does not propagate, so a timing line prints once, prefixed with its own UTC time. Logging never raises into, or blocks, the caller: `emit` swallows every fault.

Every line is `<event> <word>... <key>=<value>...`. Values are sanitized (`_sanitize`): visible ASCII only, no spaces, so a value cannot forge another field or line. No secret reaches a line: request query values are masked as `***`, Composer lines name the route template (`/portfolio/accounts/{account}/total-stats`), never an account or symphony id.

## Events written
| Event | Written by | Fields |
|-------|------------|--------|
| `cycle` | `app._log_finished_cycle` (one per engine run) | `cycle_id`, `outcome` (`cycle`, `blackout`, `eod`, `closed`, `failed`, `terminated`), `started_at`, `ended_at`, `duration_s`, `exit_code` |
| `cycle` with `outcome=skipped` | `app._log_skipped_minute` (a minute that found the previous run still alive) | `cycle_id`, `started_at`, `running_cycle_id`, `running_since`, `running_s` |
| `composer` | `app._log_total_stats_call`, `advisors.composer_series_client._log_attempt` | `GET`, route template, `status` or `error` (exception type), `ms` |
| `access` | `AccessLogMiddleware`: one line when the response closes; an event stream also writes `stream=open` when it starts | method, masked path and query, `status`, `ms` and `bytes`; a stream's closing line carries `stream=closed`, `open_s` and `bytes`; an unhandled exception writes `status=500`, `bytes=0`, `error=<type>` |

## API Reference
- `make_daemon_stdout(buffer)`: line-buffered UTF-8 text stream over `buffer`.
- `attach_stream_handler(stream)`, `configure_daemon_output()`: wire the logger to the stream; `app.py` calls the latter at start.
- `emit(event, *words, **fields)`: write one line; never raises.
- `AccessLogMiddleware(wsgi_app)`: WSGI wrapper installed by `app.py` as `app.wsgi_app`. Reads only the method, path and query from the environ. Werkzeug's own request log stays off.
- `clock`: the monotonic clock, read at call time as `timing_log.clock()` so a test can replace it.

## Internal Dependencies
Standard library only.

## Unconfirmed
Whether the deployed unit's journal now shows lines at the moment they are printed, and the measured request sizes after gzip, cannot be confirmed from code. Settle it on the droplet with `journalctl -u planetstopper -f` during a cycle and `curl -sI -H 'Accept-Encoding: gzip' https://<host>/api/state`.
