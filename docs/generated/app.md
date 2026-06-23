# app

> Flask daemon: minute-by-minute scheduler, operator dashboard routes, AI Advisor endpoints (single-page SPA), and daemon singleton lifecycle.

**Source:** `app.py`
**Last updated:** 2026-06-23 (feat/dashboard-realtime-push: SSE /api/events + _StaleFlagDict + _notify_cycle_complete + data_as_of fix; prior: DE-LIVE-DASH-001: live data-source wiring for six broken dashboard surfaces)

## Overview

`app.py` is the Flask application and process host. It owns:

- **Daemon singleton** — pidfile-based single-instance enforcement at startup.
- **Startup symphony seed** — calls `alpha_bot_execution.ensure_bot_state_seeded()` once after the pidfile is acquired and before the minute scheduler starts. Idempotent no-op when symphonies already exist; fail-safe if Composer is unreachable at startup. See `DE-SEED-STARTUP-001` in `DECISIONS.md`.
- **Minute scheduler** — spawns `alpha_bot_execution.py` at `:00` via `subprocess.run`; refreshes Composer account totals once per minute; prunes telemetry at 02:00.
- **Dashboard routes** — operator UI routes. Two CSRF-protected write paths exist: `POST /api/settings` (allowlisted .env keys) and `POST /api/symphony-settings/<name>` (per-symphony live-mode toggle). Templates open SQLite read-only; the dashboard is NOT a live-trade-action surface.
- **AI Advisor routes** — unified single-page SPA at `GET /ai-advisor` renders all 6 tabs in one server-side render; GET sub-routes for all 5 old per-tab pages now 302-redirect to `/ai-advisor`; POST action routes (suggest, evaluate, accept, reject, chat/send, strategy-builder/run) are unchanged.
- **CSRF infrastructure** — `_validate_csrf()` hook; `_csrf_before_request` before-request handler; `GET /api/csrf-token` token endpoint; `_SETTINGS_WRITE_ALLOWLIST` restricts which .env keys the settings write path can touch.
- **Dashboard auth gate** — single-password Flask signed-session gate protecting the entire Flask surface (AC-1..AC-13). `_auth_before_request` before-request hook registered before CSRF; `_AUTH_EXEMPT_ENDPOINTS` frozenset allowlist (`login`, `logout`, `static`, `get_csrf_token`, `health`); `_resolve_dashboard_credential()` for hash-preferred credential resolution (`DASHBOARD_PASSWORD_HASH` over `DASHBOARD_PASSWORD`); `_is_api_or_xhr()` dispatches 401 JSON vs 302 redirect; in-memory throttle `_AUTH_FAILED_ATTEMPTS`; **fail-closed**: missing credential or `SECRET_KEY` denies ALL requests.
- **Event-driven dashboard push** — `GET /api/events` SSE endpoint streams `cycle-complete` notifications to all connected dashboard clients; `_notify_cycle_complete()` fans out after every engine subprocess exit (success or failure). Primary update path; 30 s poll is the resilience fallback. See `DE-SSE-PUSH-001` in `DECISIONS.md`.

Module-level thread-safety constructs:

- `_DISMISS_EXECUTOR` — `ThreadPoolExecutor(max_workers=1)` for fleet-alert dismiss writes. Registered with `atexit` for graceful shutdown.
- `_FLUSH_STATE_LOCK` — `threading.Lock()` serializing `flush_resync` background writes against engine `save_state` writes.
- `_CHAT_RATE_LIMITER` — per-IP rate-limiter for AI Advisor chat endpoint (cost-DoS guard; max `CHAT_RATE_LIMITER_MAX_TRACKED_IPS` IPs).
- `_account_totals_cache` — `_StaleFlagDict` instance holding Composer account-level totals; reads return empty-state when marked stale by `_notify_cycle_complete()`, unmasking after `_refresh_account_totals()` writes fresh values.
- `_account_totals_cache_lock` — `threading.Lock()` serializing multi-key writes from `_refresh_account_totals()` so concurrent readers never observe a partial write sequence.
- `_sse_clients` / `_sse_clients_lock` — list of `queue.Queue` objects (one per connected `/api/events` client) and its `threading.Lock`. `_notify_cycle_complete()` fans out under the lock; the SSE generator registers/deregisters its queue under the same lock.

## Environment Variables

### Auth gate

| Variable | Required | Description |
|----------|----------|-------------|
| `DASHBOARD_PASSWORD_HASH` | preferred | Werkzeug hash of the dashboard password (`pbkdf2:`, `scrypt:`, or `bcrypt:` prefix). Takes precedence over `DASHBOARD_PASSWORD`. |
| `DASHBOARD_PASSWORD` | fallback | Plaintext dashboard password. Used only when `DASHBOARD_PASSWORD_HASH` is absent. Never written to logs. |
| `SECRET_KEY` | required | Flask session signing key. If absent/empty, all requests are denied (fail-closed). Fallback env name: `FLASK_SECRET_KEY`. |
| `SESSION_COOKIE_SECURE` | optional | Set to `1`, `true`, or `yes` to add `Secure` flag to the session cookie (enable when TLS terminates at a reverse proxy). |
| `TRUST_PROXY` | optional | When truthy, the login handler reads the real client IP from `X-Forwarded-For` (first entry) for throttle keying. Leave unset when not behind a trusted proxy. |
| `_AUTH_MAX_ATTEMPTS` | optional | Max consecutive wrong-password attempts before lockout. Default: `10`. |
| `_AUTH_LOCKOUT_SECONDS` | optional | Lockout duration in seconds after exceeding `_AUTH_MAX_ATTEMPTS`. Default: `300` (5 minutes). |

### Scheduler / Production

| Variable | Required | Description |
|----------|----------|-------------|
| `DISABLE_DAEMON_LENS_PIPELINE` | optional | When set to any non-empty value, `_run_lens_pipeline()` returns immediately without running. Set to `1` on the production droplet so `prism_scheduler.py` is the SOLE nightly `MARKET_PRISM` producer. Set BEFORE registering the council systemd timer (no idempotency guard between the two paths). See DE-PRISM-GATE-001. |
| `PORT` | optional | Flask listen port. Default: `5000`. Set to `8090` on the production droplet (behind a reverse proxy). |
| `ADVISOR_SYNTHESIS_MODEL` | optional | Claude model for AI Advisor synthesis calls. Default: `claude-opus-4-8`. Overridable at runtime without a code change. |

## API Reference

### Daemon Startup Sequence

The `if __name__ == "__main__":` block runs this sequence:

1. `_acquire_daemon_singleton(_PIDFILE_PATH)` — pidfile-based single-instance enforcement.
2. `from alpha_bot_execution import ensure_bot_state_seeded; ensure_bot_state_seeded()` — lazy-imported to avoid circular-import risk at module level; seeds `bot_state` with baseline symphony entries when none exist (idempotent no-op when already seeded; fail-safe on Composer errors). See `alpha_bot_execution.ensure_bot_state_seeded`.
3. `threading.Thread(target=run_scheduler, daemon=True).start()` — minute scheduler thread.
4. `app.run(...)` — Flask server.

---

### Dashboard Auth Gate

#### `_resolve_dashboard_credential() → str | None`
Returns the configured dashboard credential. Prefers `DASHBOARD_PASSWORD_HASH`; falls back to `DASHBOARD_PASSWORD`. Returns `None` when neither is set — callers treat `None` as a misconfig → fail-closed.

#### `_secret_key_configured() → bool`
Returns `True` when a non-empty `SECRET_KEY` or `FLASK_SECRET_KEY` env var is present. Used by `_auth_before_request` for the fail-closed misconfig check.

#### `_auth_before_request() → Response | None`
Flask `before_request` hook (registered before `_csrf_before_request`). Enforces the auth gate on every request (AC-1/AC-2/AC-8):
- Bypassed when `_auth_check_enabled` is `False` (test contexts).
- Exempt endpoints (`_AUTH_EXEMPT_ENDPOINTS`) pass through unconditionally.
- Fail-closed: missing `SECRET_KEY` or missing credential → 503 JSON / redirect to `/login`.
- Authenticated session (`session['authenticated']`) → pass through.
- Unauthenticated: `/api/*` or XHR → 401 JSON; HTML routes → 302 `/login`.

#### `_is_api_or_xhr() → bool`
Returns `True` when the request path starts with `/api/` or carries `X-Requested-With: XMLHttpRequest`. Used by `_auth_before_request` and `login()` to decide 401-vs-302 response.

#### `_check_throttle(client_ip: str) → bool`
Returns `True` if the client is currently locked out (fail-count >= `_AUTH_MAX_ATTEMPTS` and lockout window has not expired). Clears expired entries on check.

#### `_record_failed_attempt(client_ip: str) → int`
Increments the failed-attempt counter for `client_ip` in `_AUTH_FAILED_ATTEMPTS`; sets `lockout_until` when the count reaches `_AUTH_MAX_ATTEMPTS`. Returns the new count.

#### `_clear_failed_attempts(client_ip: str) → None`
Resets the throttle counter for a client on successful login.

#### `GET /login` / `POST /login` — `login()`
Login page (AC-3 through AC-9, AC-13).

- **GET:** Renders `templates/login.html` with the process-lifetime CSRF token. If the session is already authenticated, redirects to the dashboard (AC-13).
- **POST:** Throttle-check → credential resolution → constant-time compare (`hmac.compare_digest` for plaintext; `werkzeug.security.check_password_hash` for hashed credentials with `pbkdf2:`/`scrypt:`/`bcrypt:` prefix) → on success: `session.clear()` + `session['authenticated'] = True` + redirect (AC-4, session-fixation prevention); on failure: increment throttle + re-render with generic "Incorrect password." error (AC-5, AC-9). Client IP resolved via `X-Forwarded-For` when `TRUST_PROXY` is set.

#### `GET /logout` — `logout()`
Clears the session and redirects to `/login` (AC-11).

---

### CSRF Infrastructure

#### `GET /api/csrf-token`
Returns a fresh CSRF token for the current session. Required for all CSRF-protected write endpoints.

#### `_validate_csrf() → None`
Validates the CSRF token from two acceptance channels:
- **`X-CSRF-Token` request header** — used by `fetch()`/XHR callers (JSON POSTs from dashboard JS). Browsers block cross-site scripts from setting arbitrary request headers, so the header itself provides same-origin enforcement.
- **`csrf_token` form field** — used by the native browser form POST on the login page (which cannot set custom headers). The form embeds the server-minted token in a hidden input; a cross-site page cannot read or guess it. **Content-type-gated (dc6b8c7):** `request.form` is accessed only when `Content-Type` is `application/x-www-form-urlencoded` or `multipart/form-data`; JSON/XHR POSTs never touch the form parser. This preserves the CSRF-check-before-body-size guard ordering (accessing `request.form` on a JSON POST triggers Werkzeug body parsing, which enforces `MAX_CONTENT_LENGTH` before the 403 can fire).

Raises `403` when neither channel provides the correct token. Called at the top of every CSRF-protected route. See `8a34de6` for the original dual-channel docstring fix; `dc6b8c7` for the content-type gating fix.

#### `_csrf_before_request`
Flask `before_request` hook. Injects CSRF enforcement for the two guarded write paths (`POST /api/settings`, `POST /api/symphony-settings/<name>`).

---

### Scheduler

#### `run_scheduler() → None`
Runs the `schedule` loop: every minute at `:00` triggers `threaded_trigger()` and `_refresh_account_totals()`; daily at 02:00 runs `_run_trigger_retention()`; daily at 03:00 runs `_run_lens_pipeline()` (gated by `DISABLE_DAEMON_LENS_PIPELINE`).

#### `trigger_alpha_bot(force: bool = False) → None`
Spawns `alpha_bot_execution.py` as a subprocess. Passes `--force` when `force=True`. Logs stdout/stderr to `alphabot_daemon.log`. After the subprocess exits (success or `CalledProcessError`), calls `_notify_cycle_complete()` in a `finally` block — the cycle notification is unconditional.

#### `_notify_cycle_complete() → None`
Fan-out hook called from `trigger_alpha_bot()` in a `finally` block. Never raises. Performs three steps in order:

1. **Mark cache stale** — calls `_account_totals_cache.mark_stale()` (O(1), lock-free). All subsequent reads from `_account_totals_cache` return `None`/empty until `_refresh_account_totals()` clears the flag. `mark_stale()` is used instead of `dict.clear()` to avoid a partial-write window: a bare `.clear()` under the write lock would not prevent a lock-free reader from observing an incomplete dict mid-write; `mark_stale()` masks ALL reads atomically in O(1).
2. **Spawn refresh thread** — starts a short-lived daemon thread (`threading.Thread(target=_refresh_account_totals, daemon=True, name="cycle-refresh")`). The thread is started BEFORE the SSE fan-out so the Composer API call is in-flight while the fan-out loop runs. This gives the refresh maximum lead time: by the time the connected client receives the event and its `/api/state` fetch arrives (~50–200 ms), the single Composer call (~100–500 ms) has typically completed. Without this, the cache stays masked until the NEXT per-minute scheduler tick (~55 s) — the SSE-triggered fetch would see blank totals, defeating the feature.
3. **Fan out SSE event** — copies `_sse_clients` under `_sse_clients_lock`, then calls `q.put_nowait("cycle-complete")` for each client queue. Exceptions (full queue, closed) are swallowed per client; the loop always completes. O(1) per client; no I/O.

#### `_refresh_account_totals() → None`
Fetches Composer account-level total-stats (`portfolio_value`, `simple_return`, `todays_percent_change`, `max_drawdown`) and populates `_account_totals_cache`. Must never raise (swallows all exceptions — D-1 contract).

**Write protocol:** all five keys are written under `_account_totals_cache_lock` (prevents concurrent readers from observing a partial write). After the last key is written, calls `_account_totals_cache.refresh_written()` to clear the stale flag atomically. If Composer returns non-200 or any exception occurs, `refresh_written()` is NOT called: the cache remains masked and consumers see empty-state (honest degradation triggering the AC-8 staleness cue in the client).

Called by the per-minute scheduler AND as a short-lived daemon thread spawned by `_notify_cycle_complete()`.

#### `_run_trigger_retention() → None`
Prunes `exit_triggers` rows older than `TRIGGER_TELEMETRY_RETENTION_DAYS` (default 90) and `shadow_history` rows older than `SHADOW_HISTORY_RETENTION_DAYS` (default 180).

---

### Account Totals Cache

#### `class _StaleFlagDict(dict)`
`dict` subclass used for `_account_totals_cache`. Adds a boolean stale flag that, when set, causes all read operations (`.get()`, `.__getitem__()`, `.__contains__()`, `.keys()`, `.values()`, `.items()`) to return empty-state (default / `KeyError` / `False` / empty views) without modifying the underlying data. Writes always succeed regardless of the stale flag.

**Methods:**
| Method | Description |
|--------|-------------|
| `mark_stale() → None` | Sets `_stale = True`. O(1), requires no lock. Called by `_notify_cycle_complete()` at cycle end. |
| `refresh_written() → None` | Clears `_stale = False`. Called by `_refresh_account_totals()` after all keys are written under `_account_totals_cache_lock`. |
| `clear() → None` | Calls `super().clear()` AND resets `_stale = False` — mirrors standard dict.clear semantics. |
| `.get(key, default=None)` | Returns `default` when stale; otherwise delegates to `dict.get`. |
| `.__getitem__(key)` | Raises `KeyError` when stale; otherwise delegates to `dict.__getitem__`. |
| `.__contains__(key)` | Returns `False` when stale; otherwise delegates to `dict.__contains__`. |

**TOCTOU safety:** All `_compute_portfolio_strip` cache reads use `.get()` (single call) rather than `.__contains__()` + `.__getitem__()`. This eliminates the window where `mark_stale()` fires between the two calls, which would cause `__contains__` to return `True` but `__getitem__` to raise `KeyError`.

**Module-level instance:** `_account_totals_cache: _StaleFlagDict = _StaleFlagDict()`

---

### Event-Driven Push

#### `GET /api/events` — `sse_events()`
Server-Sent Events endpoint. Returns `text/event-stream`. Auth-gated by `_auth_before_request` (unauthenticated → 401 JSON). SSE is GET-only; CSRF infrastructure is unaffected.

**Lifecycle:**
1. Creates a `queue.Queue()` for this connection.
2. Appends the queue to `_sse_clients` under `_sse_clients_lock`.
3. Runs the `generate()` generator: blocks on `client_q.get(timeout=15)`; on a message yields `event: <msg>\ndata: {}\n\n`; on `queue.Empty` (15 s timeout) yields `: heartbeat\n\n` to keep the connection alive through proxies.
4. On `GeneratorExit` (client disconnect or server response close), removes the queue from `_sse_clients` under `_sse_clients_lock` via a `finally` block.

**Response headers:** `Cache-Control: no-cache`, `X-Accel-Buffering: no` (disables nginx buffering for streaming).

**Edge cases:**
- Auth-failed clients receive 401 JSON; `EventSource.onerror` fires and the client falls back to the 30 s poll.
- Daemon restart: existing clients get a connection error and reconnect via `EventSource`'s built-in retry.
- Engine cycle fails (`CalledProcessError`): `_notify_cycle_complete()` is still called unconditionally — the client polls fresh state reflecting the failure.

---

### Dashboard Routes

#### `GET /`
Dashboard root. Calls `get_api_state_dict()`, partitions symphonies into active/standby, enriches each with analytics data.

#### `GET /api/state`
Returns the full API state dict as JSON. Includes `bot_state`, `portfolio_strip`, `meta`, `exit_authority`, and `port_state` (SITE-D1 KEEP-DISPLAY).

**Freshness contract (AC-4):** After a new engine cycle writes state to the DB, the first call to `GET /api/state` returns data reflecting that cycle because `_notify_cycle_complete()` marks `_account_totals_cache` stale at cycle completion. `_compute_portfolio_strip()` reads the fresh DB state and falls back to per-symphony sum for `portfolio_value` while the cache is masked. A staleness indicator in the response (`data_as_of`) reflects the actual cycle timestamp, not the server render clock.

**AC-7 top-level `data_as_of` (in-scope fix, app.py:2117):** The response also carries a top-level `data_as_of` field used by `static/index.js:1168` as the hero freshness fallback (`portfolio.data_as_of || data.data_as_of`). This field now derives from `last_successful_cycle_at` in `state_data` — the same `last_successful_cycle_at` pattern as `_compute_portfolio_strip` (app.py:1281–1303). Also fixes a pre-existing naive `datetime.now()` bug (no `_ET`) that produced local-system time instead of ET.

#### `GET /history`
Render historical performance page.

#### `GET /performance`
Render performance analytics page.

#### `GET /api/performance` -- `api_performance()`

Returns per-day time-series + quantstats metrics. Accepts `scope` ("aggregate"/"symphony"), `days` (int), and `symphony_id` (required when scope=symphony) query params.

**Data-source precedence (AC-2/AC-2b, DE-LIVE-DASH-001):** Three-tier fallback:

1. **Post-mortem history (primary):** `analytics.get_history_with_cache_invalidation(base_dir=analytics._POST_MORTEMS_DIR)` -- used when EOD files exist.
2. **Multi-day shadow_history:** `analytics.get_portfolio_bot_and_held_daily_returns()` or `analytics.get_portfolio_daily_returns_from_shadow()` -- used on day-1 when no post-mortem files exist and shadow_history has >= 2 distinct trading days.
3. **Single-day shadow_history (AC-2b):** `analytics.get_single_day_shadow_returns()` -- used when both tier-2 functions return `None` (fewer than 2 distinct trading days, i.e. fresh droplet on its first trading day). Returns a 1-element `([date], [bot_pct], [held_pct])` tuple so the chart is never blank. `insufficient_history` remains `True` (honest -- 1 < `_PERFORMANCE_MIN_HISTORY_DAYS`).

The `< 2` guard in the tier-2 functions is correct and unchanged; tier-3 handles day-one without weakening the statistical guard.

**Response shape:** `{scope, dates, live_returns, shadow_returns, live_metrics, shadow_metrics, observation_count, insufficient_history}`.

Read-only: no DB writes, no network I/O, not in `_SETTINGS_WRITE_ALLOWLIST`.

#### `GET /api/performance/symphonies` -- `api_performance_symphonies()`

Per-symphony performance breakdowns. Same post-mortem primary path + `shadow_history` fallback as `/api/performance`.

#### `GET /api/history/<int:days>` -- `get_history()`

Returns historical portfolio summary for the last `days` days.

**Bug fix (AC-3, DE-LIVE-DASH-001):** Previously called `analytics.get_history_summary(days=days)` without the `base_dir` argument, which defaulted to the process CWD and found no files. Now calls `analytics.get_history_summary(days=days, base_dir=analytics._POST_MORTEMS_DIR)` -- the same constant used by every other post-mortem reader.

**`todays_exits` fallback (AC-3):** When `stats["todays_exits"]` is empty (no post-mortem written yet today), the route reads the 50 most-recent rows from `exit_triggers` and backfills them into the response. This ensures live exits appear in the History tab on day one, before the EOD post-mortem is written.

**`trigger_count` backfill (AC-3b):** `stats["trigger_count"]` is updated to `len(stats["todays_exits"])` immediately after the AC-3 backfill so the two fields stay consistent. Previously `trigger_count` was left at 0 from `get_history_summary()` while `todays_exits` had rows, causing the History tab to show "Today's exits (0)".

**Column name fix (AC-3c):** The backfill query and response dict used `trigger_reason`; the real `exit_triggers` column is `triggered_reason` (PRAGMA-confirmed on live droplet). Fixed in 56901e0 -- both the `SELECT` (`app.py:2589`) and the dict key (`app.py:2600`) use `triggered_reason`.

#### `GET /api/logs/<symphony_id>`
Returns symphony execution logs.

#### `GET /api/triggers`
Returns recent exit trigger telemetry rows.

#### `GET /api/accounts`
Returns Composer account information.

#### `GET /api/hero-chart/<window>`
Returns hero chart data for the given window.

#### `GET /api/chart/<symphony_id>`
Returns per-symphony chart data.

#### `POST /api/fleet-alert/dismiss`
CSRF-protected. Dismisses the active fleet alert via `_DISMISS_EXECUTOR`.

#### `POST /api/trigger`
Operator-initiated manual trigger (protected endpoint).

#### `POST /api/force_eod`
Operator-initiated force end-of-day (protected endpoint).

#### `POST /api/resend_discord`
Resends the most recent Discord notification.

#### `POST /api/sell_account`
Operator-initiated account sell (protected; writes via Composer API).

#### `GET /api/strip/<window>` -- `get_windowed_strip()`

Returns comparison strip metrics (guard-alpha/CR/MDD/vol) re-windowed for the selected window token (30d/60d/90d/125d/ytd/1y/all). Threads the token through `analytics.compute_windowed_portfolio_strip`.

**Intraday guard-alpha fallback (AC-4/AC-4b, DE-LIVE-DASH-001):** when `insufficient_history=True` AND `guard_alpha` is falsy (fewer than 2 distinct trading days in `shadow_history`), the route queries `exit_triggers` + `shadow_history` for per-symphony returns and reads `position_value` from `database.load_state()` (the JSON blob accessor, `current_value` field per symphony_id). Computes `alpha_per_symphony = at_return - current_return` weighted by `position_value`. When at least one triggered symphony has valid values, the strip dict is updated with the computed `guard_alpha` and `intraday_only=True` (additive field -- callers show a "Today only" label instead of "+0.00%"). The 15-second auto-refresh floor keeps this current.

**AC-4b root cause (fixed 7b5f29d):** The original correlated subquery `SELECT position_value FROM bot_state WHERE symphony_id = t.symphony_id` assumed a columnar bot_state schema; the real schema is a single-row JSON blob. The `OperationalError` was swallowed by `except Exception` at `app.py:2214` → `guard_alpha` stayed `0.0`, `intraday_only` never set. Fixed with the same `load_state()` pattern as AC-1b.

Read-only; builds symphony list from state DB, never reruns the engine.

#### `GET /api/autotune-runs`
Returns paginated autotune run history via `database.get_all_autotune_runs`. Response normalized via `_normalize_autotune_row`.

#### `GET /api/advisor-observations`
Returns `advisor_observations` rows for a symphony. Accepts `?symphony_id=` query parameter.

#### `GET /api/guard-alpha-summary` -- `guard_alpha_summary()`

Returns cumulative dollar-saved aggregate and guard-event count.

**Dual-path data source (AC-1, DE-LIVE-DASH-001):**

- **Primary (EOD) path:** when `post_mortem_*.json` files exist in `analytics._POST_MORTEMS_DIR`, sums `saved_dollars` from all trigger entries and sets `source="post_mortem_eod"`. Dollar figures are snapshot-time (computed by `reporting.py` at exit time).
- **Intraday fallback path (AC-1/AC-1b):** when no post-mortem files exist (day-1 droplet), queries `exit_triggers` + `shadow_history` for per-symphony returns, and reads `position_value` from `database.load_state()` (the JSON blob accessor keyed by symphony_id -- `current_value` field). Formula per exit: `saved = (at_return - current_return) / 100 * position_value`. Rows where any value is NULL are skipped. Sets `source="exit_triggers_intraday"` and `basis_label="intraday estimate -- updates live"`. On fallback DB error, returns `guard_event_count=0`, `cumulative_saved_dollars=0.0`, `basis_label="no guard events yet"`.

  **AC-1b root cause (fixed 93bd62c):** The original implementation used a correlated subquery `SELECT position_value FROM bot_state WHERE symphony_id = t.symphony_id` which assumed a multi-row columnar schema. The real production `bot_state` is a single-row JSON blob (`id INTEGER, data TEXT`). The subquery raised `OperationalError`; the outer `except Exception` swallowed it silently, producing zero results despite real exit_triggers rows.

**Response shape:**
| Field | Type | Description |
|-------|------|-------------|
| `cumulative_saved_dollars` | float | Aggregated savings. 0.0 when no events or values are NULL. |
| `guard_event_count` | int | Total exit-trigger count. |
| `date_range` | dict | `{earliest, latest}` ISO dates from filenames (EOD path); `{null, null}` when using intraday path. |
| `basis_label` | str | "snapshot-time basis, since <date>" (EOD), "intraday estimate -- updates live" (intraday), "no guard events yet" (empty). |
| `source` | str | `"post_mortem_eod"` or `"exit_triggers_intraday"`. Callers use this to qualify the display. |

**Key properties:**
- **Read-only.** Not in `_SETTINGS_WRITE_ALLOWLIST`, no `LIVE_EXECUTION`, no DB writes.
- **Malformed-file resilient.** Each post-mortem file is wrapped in `try/except (OSError, json.JSONDecodeError)`; failures log the basename only and skip the file. Always returns 200.
- **Auth-gated.** Covered by the global `_auth_before_request` hook (DE-AUTH-001); unauthenticated XHR receives 401.

See `DE-GAP-001` and `DE-LIVE-DASH-001` in `DECISIONS.md`.

**Consumed by:** `fetchGuardAlphaSummary()` in `static/index.js`. Populates `#dollar-saved-headline`, `#guard-event-count`, `#dollar-saved-basis-label` in `templates/index.html` (`data-testid="dollar-saved-panel"`). Does not clobber `#guard-alpha-headline` (windowed % guard alpha from `/api/strip/<window>`).

---

### Settings Write Paths

#### `GET /settings`
Renders the settings page.

#### `GET /api/settings`
Returns Globals from .env and Symphony Strategies from SQLite. Masks keys in `_MASKED_SETTINGS_KEYS` (e.g. `DISCORD_WEBHOOK_URL`). Includes `param_meta` (8-key `_ALGO_PARAM_META`).

#### `POST /api/settings`
CSRF-protected write path. Accepts JSON `{key: value}` pairs; only keys in `_SETTINGS_WRITE_ALLOWLIST` are written to `.env`. Excludes `LIVE_EXECUTION` and all credential keys.

#### `GET /api/settings/flush-resync`
Flushes and resyncs the settings cache.

#### `GET /api/symphony-settings/<symphony_name>`
Returns per-symphony live-mode state and strategy params for the gear-icon modal.

#### `POST /api/symphony-settings/<symphony_name>`
CSRF-protected write path. Requires explicit `confirmed=true` in request body. Calls `database.set_symphony_live_mode` to toggle per-symphony dry-run/live mode.

---

### AI Advisor Routes

The AI Advisor SPA was extended from 5 to **6 in-place tabs** in the spa-port cycle (2026-06-13). The Strategy Builder was formerly a separate page (`GET /ai-advisor/strategy-builder` → `render_template("ai_advisor_strategy_builder.html")`); it is now the 6th tab panel in the unified `templates/ai_advisor.html` SPA. Its GET route now 302-redirects to `/ai-advisor` like all the other former sub-pages. The POST action route (`POST /ai-advisor/strategy-builder/run`) is unchanged.

All 6 panels (Overview, Correlations, Asset Swaps, Logic Changes, Chat, Strategy Builder) are rendered in one server-side template at `GET /ai-advisor`. Tab switching is in-place via JS (`initTabSwitcher` in `static/ai_advisor.js`). All 5 old GET sub-routes 302-redirect to `/ai-advisor`; the POST action routes are unchanged.

#### `GET /ai-advisor` — `ai_advisor_tab()`

Unified single-page render for all 6 in-place tab panels. Server-side assembles all data needed for every panel in one request:

**Template context:**
| Key | Source | Panel |
|-----|--------|-------|
| `observations` | `database.get_advisor_observations_for_role` per `_ADVISOR_ROLES`; deduped; `NOT_APPLICABLE`/`feature_flag=off` stubs suppressed; **RF-1:** each non-MARKET_PRISM obs is stamped with `_preview_text` via `advisors.prism_render.humanize_obs_preview(obs["raw_response"])` before template render | Overview |
| `correlation_matrix` | `correlation_diagnostic.compute_pairwise_correlations` over analytics history | Correlations |
| `as_of` | `datetime.now(_ET).isoformat()` | Correlations |
| `crisis_caveat` | `correlation_diagnostic.CRISIS_CAVEAT` | Correlations |
| `insufficient_data` | `True` when matrix is empty | Correlations |
| `no_api_key` | `True` when Composer credentials absent | Asset Swaps, Logic Changes |
| `symphonies` | `analytics.list_available_symphonies` | Asset Swaps, Logic Changes forms |
| `chat_available` | `bool(os.environ.get("ANTHROPIC_API_KEY"))` — key presence only, value never passed to template | Chat |
| `sb_observations` | `database.get_advisor_observations_for_role("STRATEGY_BUILDER")`, reversed (oldest-first); empty list on error | Strategy Builder |
| `sb_card_artifacts` | dict keyed by `obs["id"]`; each value is an M6 `strategy_proposal` artifact dict for the Discuss/Chat affordance; built from `raw_response` fields per observation | Strategy Builder |
| `market_prism_summary` | `database.get_latest_market_prism_summary()`; `dict` or `None`; wrapped in `try/except` — `None` on failure renders an informative empty state; **RF-1:** `per_lens_digest` summaries are pre-humanized in-place by `advisors.prism_render.humanize_lens_summary` before template render (no new context key — template's existing `_lens.get('summary')` reads humanized prose) | Overview (Market Prism block) |

The Correlations, API-key, Symphonies, Strategy Builder, and Market Prism data assembly sections are wrapped in `try/except` — if those panels' data fails, the others still render. The Overview observations loop is not wrapped.

**RF-1 (prose render guard — two fixes):**

- **R1 (per-lens digest):** If `market_prism_summary` is present, `ai_advisor_tab()` iterates `per_lens_digest` and mutates each `_le["summary"]` in-place via `advisors.prism_render.humanize_lens_summary(_ln, _le)` (app.py:2966–2984). The template's existing `{{ _lens.get('summary') | e }}` renders humanized prose; no new context key is added. Council prose passes through unchanged; `lens_pipeline` JSON is humanized per lens type.

- **R2 (obs-raw-preview):** For each non-MARKET_PRISM observation in `observations`, `ai_advisor_tab()` stamps `obs["_preview_text"] = humanize_obs_preview(obs["raw_response"])` (app.py:2892–2902). The template renders `obs.get('_preview_text', '') | e` for non-MARKET_PRISM rows and `obs.verdict | e` for MARKET_PRISM rows.

See `DE-RF1-PROSE-RENDER` in `DECISIONS.md` and [advisors/prism_render](advisors_prism_render.md).

#### `GET /ai-advisor/correlations` → 302 redirect to `/ai-advisor`
#### `GET /ai-advisor/asset-swaps` → 302 redirect to `/ai-advisor`
#### `GET /ai-advisor/logic-changes` → 302 redirect to `/ai-advisor`
#### `GET /ai-advisor/chat` → 302 redirect to `/ai-advisor`
#### `GET /ai-advisor/strategy-builder` → 302 redirect to `/ai-advisor` — `ai_advisor_strategy_builder()`

Old bookmarks and links redirect cleanly rather than 404ing. The Strategy Builder content is now rendered as the 6th tab panel in the unified SPA. The POST action route is unaffected.

#### `POST /ai-advisor/asset-swaps/evaluate` — `ai_advisor_asset_swaps_evaluate()`

Accepts JSON: `{ symphony_id, from_ticker, to_ticker, objective_type? }`. Resolves the display name to a Composer hash (fails loudly if unresolvable — RC-6). Constructs a typed `SwapObjective`, calls `propose_operator_swap`, returns `SwapRunResult` fields as JSON. Never calls Composer write endpoints. Persistence handled inside `propose_operator_swap`.

**Key fix (2026-06-10):** Routes now resolve the display name → Composer hash before calling the engine (AC-8). Previously passing the display name to the engine caused silent empty results from the Composer backtest API.

#### `POST /ai-advisor/logic-changes/evaluate` — `ai_advisor_logic_changes_evaluate()`

Accepts JSON: `{ symphony_id, objective_type?, change_description }`. Same name→hash resolution as asset-swaps. Calls `propose_operator_logic_change` with the Composer hash. Returns `LogicChangeRunResult` fields as JSON including FDR metadata (n_candidates, fdr_q, fdr_adjusted_threshold via Yekutieli c(n) harmonic-sum).

**Key fix (2026-06-10):** `gate_reason` is derived as `gr.verdict.decision.replace("_", " ").title()` when `vetoes_passed` is True, else `"veto failed"`. The previous code attempted `gr.verdict.reason` which does not exist on `AcceptanceVerdict`.

#### `POST /ai-advisor/suggest` — `ai_advisor_suggest()`

Resolves the Composer hash → normalized symphony name for DB lookups; pre-fetches the autotune run once via `database.get_latest_autotune_run` and passes it to `ai_advisor.assemble_advisor_context` (which now honors the passed value and skips its own internal fetch — single DB round-trip total). Passes both `symphony_id` (normalized name) and `composer_symphony_id` (original hash) to `ai_advisor.assemble_advisor_context`. Returns `{"suggestions": [...], "assessment": {...}}` — the `assessment` key carries `build_assessment_from_context` output so the UI can explain the empty-suggestions state per symphony.

D-1 security contract: fully honored on this route and all advisor routes (asset-swaps/evaluate, logic-changes/evaluate, and the ImportError handler) — on exception, returns `{"error": type(exc).__name__}` only, never `str(exc)`.

#### `POST /ai-advisor/accept` — `ai_advisor_accept()`

Applies one suggestion through C2 safety gates: (1) allowlist, (2) risk-direction log, (3) OOS revalidation via `ai_advisor.revalidate_suggestion_oos`, (4) locked-var guard. On all-pass, writes new param value via `database.save_symphony_strategy` and persists the operator decision to `llm_suggestions` via `database.record_llm_suggestion`.

#### `POST /ai-advisor/reject` — `ai_advisor_reject()`

Records operator rejection to `llm_suggestions` audit trail. No config write, no `save_symphony_strategy` call.

#### `POST /ai-advisor/chat/send` — `ai_advisor_chat_send()`

Rate-limited (per-IP via `_CHAT_RATE_LIMITER`) explain-only chat endpoint. Accepts `{ artifact_type, artifact_id, artifact, history, message }`. Delegates to `advisors.advisor_chat.explain_artifact`. Hard constraints: no write path, no trade directives, no new unvalidated recommendations. Returns `{reply: str}` on success, `{error: str}` on failure; never returns 500 or HTML.

#### `POST /ai-advisor/strategy-builder/run` — `ai_advisor_strategy_builder_run()`

CSRF-protected. Accepts JSON: `{ objective, universe, symphony_id? }`. Lazy-imports `propose_strategies` from `advisors.strategy_builder_engine` and `load_atlas_candidates` from `advisors.build_plan_generator` (CC-2: both kept off the live 1-minute execution path).

**Request body:**

| Field | Type | Description |
|-------|------|-------------|
| `objective` | `str` | One of `diversify` / `cut_drawdown` / `lift_risk_adjusted` / `volatility_mitigation`. Unknown values default to `diversify`. |
| `universe` | `list[str]` or comma-string | Optional ticker override; `[]` (default) triggers C1 self-sourcing from `universe_provider.get_tradeable_set()`. |
| `symphony_id` | `str` | Optional. Keys persisted observations to this Composer symphony ID. Defaults to `""`. |

**Pipeline (C5 rewire, commit 1d5dd48):**

1. Parse `objective` → `Objective` enum (default `diversify` on unknown values, `app.py:3800`).
2. Call `build_plan_generator.load_atlas_candidates(objective)` — objective-matched Atlas community injection (AC-12/AC-13, `app.py:3807`). D-1 (never raises); bill-protected (`force_refresh=False` inside). Atlas failure → `community_candidates=[]`, template-only run proceeds.
3. Call `propose_strategies(objective, universe, screen_config=ScreenConfig(), live_returns=[], symphony_id=..., community_candidates=...)` (`app.py:3813`). Built-new (Opus C1→C2→C3) AND atlas-suggested candidates flow into ONE FDR batch (AC-21).
4. Serialize survivors and rejected candidates from `run.gated_batch` + `run.screened_survivors` (`app.py:3852–3879`); each entry carries `template_id` (provenance: `"built-new"` or `"atlas-suggested"`), gate metrics, and candidate params.

**Response JSON:**

```json
{
  "survivors": [
    {
      "candidate_id": "...",
      "template_id": "built-new",
      "gate_decision": "...",
      "winner_p_adj": 0.012,
      "caveats": ["SURVIVOR_OVERFITTING_CAVEAT"],
      "metrics": {},
      "params": {"provenance": "built-new", "objective": "diversify"},
      "n_candidates": 12,
      "fdr_q": 0.05,
      "fdr_adjusted_threshold": 0.012
    }
  ],
  "rejected": [...],
  "n_candidates": 12,
  "fdr_adjusted_threshold": 0.012,
  "error": null
}
```

`template_id` carries provenance end-to-end: `"built-new"` for Opus-generated candidates, `"atlas-suggested"` for objective-matched community candidates (AC-13).

**Error handling (AC-23 boundary):**

- Route outer `except`: returns `{"error": type(exc).__name__}` — never `str(exc)` (`app.py:3826`).
- `run.error` branch: logs the full error server-side; surfaces the static token `"strategy-builder-error"` to the operator (`app.py:3840`) — never echoes `run.error` verbatim (it is set by `propose_strategies` via `str(exc)`, which can carry API keys or internal paths). The internal normalization of `propose_strategies`' error string to a class name is a tracked follow-on (not done in C5).

Advisory-only: never calls Composer write endpoints, never touches `LIVE_EXECUTION`, not in `_SETTINGS_WRITE_ALLOWLIST`.

---

### State Helpers

#### `get_api_state_dict() → dict`
Assembles the full state payload for `/api/state` and the dashboard template. Reads `bot_state`, computes `portfolio_strip`, builds `meta`, adds `exit_authority` via `os.getenv("EXIT_AUTHORITY")`.

#### `_compute_portfolio_strip(bot_state: dict, trading_day: str | None = None) → dict`
Shared by `get_api_state_dict()` (Jinja render path) and `get_state()` (JSON poll path) so both paths emit identical `portfolio_strip` shape.

**`data_as_of` derivation (AC-7 fix):** The `data_as_of` field is derived from the actual engine cycle timestamp, not the server render clock. Implementation:
1. Iterates `bot_state` values for the first `last_successful_cycle_at` field.
2. Parses the ISO timestamp; if timezone-naive, attaches `_ET` so `strftime` renders the correct HH:MM without a local-system offset shift (the engine writes `current_et.isoformat()` — an ET-local naive datetime).
3. Falls back to `datetime.now(_ET).strftime("%H:%M ET")` when no cycle timestamp is present.

This ensures the `data_as_of` display reflects when the cycle data was captured, not when the HTTP request was served. The BLOCK-B TOCTOU fix also ensures `data_as_of` is snapshotted at data-capture time on the historical branch in `get_api_state_dict()`.

**Cache reads:** All `_account_totals_cache` reads use `.get()` (single call, TOCTOU-safe against `_StaleFlagDict.mark_stale()`). The `portfolio_value` is sourced from the cache when available; falls back to a per-symphony sum from `bot_state` when the cache is masked (stale window after `_notify_cycle_complete()`).

#### `_compute_suggestion_gates(suggestion, symphony_id: str) → dict`
Computes four-gates verdict booleans for one suggestion: `allowlist`, `risk_direction`, `oos_frozen_eval`, `locked_vars`.

#### `_enrich_suggestion_impact(suggestion) → dict`
Builds impact dict with `before`, `after`, `delta`, `metric` fields from the suggestion's raw impact payload.

#### `_normalize_autotune_row(row: dict) → dict`
Normalizes an `autotune_runs` DB row for the `/api/autotune-runs` JSON response.

## Internal Dependencies

- `ai_advisor` — context assembly, Claude call, C2 safety gates, assessment builder
- `database` — all state-DB reads and writes for advisor routes
- `analytics` — symphony history, correlation data, symphony list
- `advisors.correlation_diagnostic` — `compute_pairwise_correlations`, `CRISIS_CAVEAT`
- `advisors.asset_swap_engine` — `propose_operator_swap`, `SwapObjective`, `_has_composer_key`
- `advisors.logic_change_engine` — `propose_operator_logic_change`, `LogicTweak`, `LogicChangeObjective`
- `advisors.advisor_chat` — `explain_artifact`, `CHAT_ARTIFACT_MAX_FIELD_VALUE_CHARS`
- `advisors.strategy_builder_engine` — `propose_strategies`, `Objective`, `ScreenConfig` (lazy import)
- `advisors.build_plan_generator` — `load_atlas_candidates` (lazy import, C5 route rewire)
- `alpha_bot_execution` — `ensure_bot_state_seeded` (lazy import, startup seed)
- `symphony_logic` — `fetch_symphony_score`
- `werkzeug.security` — `check_password_hash` for `DASHBOARD_PASSWORD_HASH` verification
