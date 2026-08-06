# app

> Flask daemon: minute-by-minute scheduler, operator dashboard routes, AI Advisor endpoints (single-page SPA), and daemon singleton lifecycle.

**Source:** `app.py`
**Last updated:** 2026-08-05 (BL-3, HEAD `fdcb07e1`, `DE-AUDIT-BL3-001` -- `guard_alpha_summary()` gains two additive net-of-friction $-saved fields (`cumulative_saved_dollars_net_of_friction`/`saved_dollars_realized_net_of_friction`), friction subtracted at the percentage level per entry via a lazily-imported `autotuner.SIM_EXIT_FRICTION_PCT` (single source of truth, never re-defined locally); closes audit #118 Finding M1 -- see the updated `GET /api/guard-alpha-summary` section below and `DE-AUDIT-BL3-001` in `DECISIONS.md`.) Prior: 2026-07-29 (guard-alpha-saved-coherence, `DE-GAS-COHERENCE-001` -- `guard_alpha_summary()` gains an optional `?window=<token>` query param (the SAME token vocabulary `/api/strip/<window>` accepts, resolved via the SAME `analytics._window_cutoff_date`), making the dashboard's $-saved panel byte-comparable to History's own `get_history_summary` total at any shared token -- see the updated route section below.) Prior: 2026-07-25 (hygiene cycle, `DE-HYGIENE-R1-001` -- `GET /api/guard-alpha-preconditions` gains a structural phantom-key exclusion filter (`_is_symphony_state_entry`), closing a gap where 5 non-symphony `bot_state` metadata keys rendered as phantom `INSUFFICIENT_DATA` "symphony" rows (16 served on the droplet vs. 11 real); see the new Phantom-key exclusion subsection below. `guard_preconditions.py` carries zero diff.) Prior: 2026-07-25 (strategy-incubation-gate, `DE-INCUBATION-GATE-001` -- new read-only `GET /api/incubation` route + `_incubation_badge()` helper; `run_scheduler()` gains a 03:30 daily incubation-tick slot (`_run_incubation_tick`/`_incubation_tick_worker`); `ai_advisor_tab()` additively live-joins each Strategy Builder survivor's persisted `candidate_hash` against the incubation ledger -- see the new sections below. Prior: 2026-07-24 (exit-friction-realized-savings, `DE-EXIT-FRICTION-REALIZED-001` -- `GET /api/guard-alpha-summary` extended additively with `saved_dollars_realized`/`realized_coverage` (dual-basis $-saved); new `GET /api/exit-turnover` read-only route; see the updated/new sections below. Prior: 2026-07-24 (guard-alpha-preconditions, live-gate correction R2, `DE-GUARD-ALPHA-PRECONDITIONS-001` -- the `GET /api/guard-alpha-preconditions` route section's cache-hit-only description corrected to reflect the underlying bounded backward-scan probe (was exact-today-only); see the updated section below). Prior: 2026-07-23 (guard-alpha-preconditions, `DE-GUARD-ALPHA-PRECONDITIONS-001` -- new `GET /api/guard-alpha-preconditions` route (`guard_alpha_preconditions()`), the Kaminski & Lo 2014 stop-justification precondition read; see the new route section below and `docs/generated/guard_preconditions.md`). Prior: 2026-07-21 (fix-ops-cluster, `DE-OPS-CLUSTER-001` -- FINAL confidence-program cycle, ops/observability cluster + residuals: F-1 (`/api/state` ~157-connects/poll fixed via a `conn=` kwarg threaded through 11 `analytics.py` functions, both the live and closed_frozen/pre_market branches of `get_state()`), F-005 (new `GET /health` unauthenticated liveness probe), F-010 (compact single-line Composer read-timeout logging, replacing ~958 full tracebacks/month), F-030 (`propose_strategies` gains `invocation_source` advisory-DB write attribution), F-003 residual (panic-stop malformed-entry extraction moved inside the per-symphony try, closing the queue-abort gap `DE-PANIC-STOP-CONFIRM-001` left open), and the suggest-hash BACKLOG item (`/ai-advisor/suggest`'s `composer_symphony_id` now hash-resolves on both hash and name input). See `DE-OPS-CLUSTER-001` in `DECISIONS.md`. Prior: 2026-07-21 (fix-display-cluster, `DE-DISPLAY-TRUTH-001` F-016/F-026 -- `_compute_portfolio_strip` hist_source fix, new `_safe_analytics`/`_tc_cr_mdd_floats` sections). Prior: 2026-07-20 (fix-f023-perf-view, `DE-PERFVIEW-ID-MISMATCH`, F-023 -- `GET /api/performance/symphonies` (`api_performance_symphonies()`, app.py:3383) rebuilt to source `{id, name}` pairs from `database.load_state()` (bot_state, keyed by the Composer hash) instead of a post-mortem-history-derived list of bare display NAMES; the old shape used the same NAME as both picker label and picker value, and that name was then sent as `symphony_id` into the hash-keyed `shadow_history` query -- matching zero rows for every one of the 11 live symphonies, silently disguised as the generic "Insufficient history" empty state (root cause, not a data gap -- 7,330+ real rows existed under the correct hash). `GET /api/performance?scope=symphony` gains a new `symphony_id_recognized` boolean (AC-4, scope=symphony only, never emitted on scope=aggregate) distinguishing a genuine no-data hash from a totally unrecognized id; `static/performance.js`'s `renderBanner()` and `static/ai_advisor.js`'s `loadSymphonies()` consume the new shape -- see `docs/generated/static_performance_js.md` (new) and `docs/generated/static_ai_advisor_js.md`); prior: 2026-07-20 (fix-f003-panic-stop, `DE-PANIC-STOP-CONFIRM-001` — `perform_account_liquidation()`'s per-symphony sell confirmation restructured: a status-code gate (200/201/202=success, else a `LIQUIDATION FAILED` line) replaces the prior unconditional "Liquidated" print, each symphony's sell attempt is isolated in its own try/except so one fault no longer aborts the remaining queue, and the function now returns a structured per-symphony outcome dict — log/isolation/return-value only, zero trade-behavior change (AC-5); no dashboard consumer yet, deferred); prior: 2026-07-20 (fix-f008-data-integrity, `DE-POSTMORTEM-INTEGRITY-001` -- `guard_alpha_summary()` gains a read-time provenance validity guard (routes through `analytics.is_valid_post_mortem_entry`), excluding post-mortem trigger entries lacking a recognized `if_held_source` stamp from the $-saved aggregate; new `excluded_invalid_count` response field; `/api/history/<int:days>`'s `get_history_summary` path gains the SAME guard (AC-5b) -- see `docs/generated/analytics.md`); prior: 2026-07-17 (math-r0, `DE-MATH-R0-001` -- AC-3..8/AC-8b Performance/History/strip render-truth fixes, closing `DE-MATH-AUDIT-001` MA-6/MA-7/MAPERF-03/04/05/06/13); prior: 2026-07-14 (branch-integration merge of `feature/frontrunner-builder` onto the R2 program. Frontrunner Builder wave-2 — DE-FRONTRUNNER-002: 4 new Frontrunner Builder AI Advisor routes — `POST /ai-advisor/frontrunner-builder/run` [async 202 dispatch via a dedicated executor], `POST /ai-advisor/proposal/approve`, `POST /ai-advisor/proposal/reject`, `GET /ai-advisor/frontrunner-builder` redirect stub — plus `ai_advisor_tab()` prefetches pending `frontrunner_proposals` with `candidate_tree` bounded to a 4000-char preview. R2-3: `POST /ai-advisor/asset-swaps/evaluate` gains a route-minted default `provenance` object present on EVERY return path (same stricter shape R2-2 established) plus the engine's own `provenance` on success, reusing `ai_advisor.build_reasoning_context` verbatim; the route also gains an objective-only REASONED mode (both tickers omitted) alongside the byte-preserved EXPLICIT-PAIR mode — see the route section below; `DE-ADVISOR-R2-3-001`, closes the R2 program 3-of-3); prior: R2-2: `POST /ai-advisor/logic-changes/evaluate` gains a route-minted default `provenance` object present on EVERY return path (stricter than R2-1's SB route, which only carries it on success) plus the engine's own `provenance` on success, reusing `ai_advisor.build_reasoning_context` verbatim — see the route section below; `DE-ADVISOR-R2-2-001`); prior: 2026-07-13 R2-1: `POST /ai-advisor/strategy-builder/run` gains a run-level `provenance` object (4-key: `generation_model`/`mode`/`evidence_injected`/`run_id`) for symphony-scoped runs, plus a route-boundary `isinstance(dict)` serialization guard against bare-`MagicMock` `ProposalRun` stand-ins — see the route section below; `DE-ADVISOR-R2-1-001`); prior: advisor-remediation-r1, `DE-ADVISOR-R1-001`: the three operator Evaluate routes — asset-swaps/evaluate, logic-changes/evaluate, strategy-builder/run — and `GET /api/candidate-alert` gained honesty/transparency response-JSON fields; see the route sections below); prior: 2026-07-12 (candidate-alert cycle: new `GET /api/candidate-alert` + `POST /api/candidate-alert/mark-viewed` back the header candidate-alert indicator — see `feature-plans/candidate-alert.md`; prior: 2026-07-09 DE-PROD-ACCURACY-001: `/api/history/<days>` fallback now date-filters to the current ET trading day and never clobbers the windowed `trigger_count`; `/api/performance` scope=aggregate now serves the canonical value-weighted `shadow_history` series with corrected `live_returns`/`shadow_returns` field semantics; `/api/guard-alpha-summary` basis_label gains freshness stamping; prior: 2026-07-02 DE-PRISM-NUMERIC-VERIFY-001, `ai_advisor_tab()` additively fetches the MARKET_PRISM_VERIFICATION row and attaches per-check fact-check badges to the Overview render; 2026-07-02 DE-EOD-BASIS-001, EOD/frozen account-basis unification + per-field stale-cache hardening; 2026-06-30 DE-PRISM-SOURCES-PER-LENS-001, per-lens Market Prism sources carousels; 2026-06-26 fix/today-change-account-basis, DE-TODAY-BASIS-001)

## Overview

`app.py` is the Flask application and process host. It owns:

- **Daemon singleton** — pidfile-based single-instance enforcement at startup.
- **Startup symphony seed** — calls `alpha_bot_execution.ensure_bot_state_seeded()` once after the pidfile is acquired and before the minute scheduler starts. Idempotent no-op when symphonies already exist; fail-safe if Composer is unreachable at startup. See `DE-SEED-STARTUP-001` in `DECISIONS.md`.
- **Minute scheduler** — spawns `alpha_bot_execution.py` at `:00` via `subprocess.run`; refreshes Composer account totals once per minute; prunes telemetry at 02:00.
- **Dashboard routes** — operator UI routes. Two CSRF-protected write paths exist: `POST /api/settings` (allowlisted .env keys) and `POST /api/symphony-settings/<name>` (per-symphony live-mode toggle). Templates open SQLite read-only; the dashboard is NOT a live-trade-action surface.
- **AI Advisor routes** — unified single-page SPA at `GET /ai-advisor` renders all 7 tabs in one server-side render (Frontrunner Builder added as the 7th tab, frontrunner-builder wave-2, 2026-07-11); GET sub-routes for the old per-tab pages now 302-redirect to `/ai-advisor`; POST action routes (suggest, evaluate, accept, reject, chat/send, strategy-builder/run, frontrunner-builder/run, proposal/approve, proposal/reject) kept their request shapes and paths. The three operator-Evaluate routes (asset-swaps/evaluate, logic-changes/evaluate, strategy-builder/run) gained substantial response-JSON extensions in the advisor-remediation-r1 cycle — N=1 statistical-honesty caveats, a granular `rejection_reason` per candidate, a `low_power` statistical-power flag (plus caveat text), and (Strategy Builder only) run-level built-new/Atlas provenance + degraded-mode reporting; Strategy Builder additionally gained R2-1's `provenance` object (generation model + injected-evidence manifest + run-id, symphony-scoped runs only), and Logic Changes gained R2-2's own reuse of the same contract (present on EVERY return path, not just success); Asset Swaps gained R2-3's reuse of the SAME contract on the SAME stricter present-on-every-path shape, plus a new objective-only REASONED mode (both `from_ticker`/`to_ticker` omitted) that runs alongside the byte-preserved EXPLICIT-PAIR mode. See `DE-ADVISOR-R1-001`/`DE-ADVISOR-R2-1-001`/`DE-ADVISOR-R2-2-001`/`DE-ADVISOR-R2-3-001` and `DE-FRONTRUNNER-002` in `DECISIONS.md` and the route sections below.
- **CSRF infrastructure** — `_validate_csrf()` hook; `_csrf_before_request` before-request handler; `GET /api/csrf-token` token endpoint; `_SETTINGS_WRITE_ALLOWLIST` restricts which .env keys the settings write path can touch.
- **Dashboard auth gate** — single-password Flask signed-session gate protecting the entire Flask surface (AC-1..AC-13). `_auth_before_request` before-request hook registered before CSRF; `_AUTH_EXEMPT_ENDPOINTS` frozenset allowlist (`login`, `logout`, `static`, `get_csrf_token`, `health`); `_resolve_dashboard_credential()` for hash-preferred credential resolution (`DASHBOARD_PASSWORD_HASH` over `DASHBOARD_PASSWORD`); `_is_api_or_xhr()` dispatches 401 JSON vs 302 redirect; in-memory throttle `_AUTH_FAILED_ATTEMPTS`; **fail-closed**: missing credential or `SECRET_KEY` denies ALL requests.
- **Event-driven dashboard push** — `GET /api/events` SSE endpoint streams `cycle-complete` notifications to all connected dashboard clients; `_notify_cycle_complete()` fans out after every engine subprocess exit (success or failure). Primary update path; 30 s poll is the resilience fallback. See `DE-SSE-PUSH-001` in `DECISIONS.md`.

Module-level thread-safety constructs:

- `_DISMISS_EXECUTOR` — `ThreadPoolExecutor(max_workers=1)` for fleet-alert dismiss writes. Registered with `atexit` for graceful shutdown.
- `_FLUSH_STATE_LOCK` — `threading.Lock()` serializing `flush_resync` background writes against engine `save_state` writes.
- `_CHAT_RATE_LIMITER` — per-IP rate-limiter for AI Advisor chat endpoint (cost-DoS guard; max `CHAT_RATE_LIMITER_MAX_TRACKED_IPS` IPs).
- `_account_totals_cache` — `_StaleFlagDict` instance holding Composer account-level totals; reads return empty-state when marked stale by `_notify_cycle_complete()`, unmasking after `_refresh_account_totals()` writes fresh values.
- `_account_totals_cache_lock` — `threading.Lock()` serializing multi-key writes from `_refresh_account_totals()` so concurrent readers never observe a partial write sequence.
- `_account_totals_last_good` (DE-EOD-BASIS-001) — plain `dict` (NOT `_StaleFlagDict`) holding the last SUCCESSFULLY-fetched Composer account totals (`portfolio_value`, `portfolio_cr`, `portfolio_tc`, ...). Survives `_account_totals_cache.mark_stale()` calls — that is its entire purpose. Written only inside `_refresh_account_totals()` on a genuine 200 response, under `_account_totals_cache_lock`. Tier-1 stale-cache fallback source for both the live and frozen `/api/state` paths.
- `_account_totals_last_success_at` (DE-EOD-BASIS-001) — `str | None`; ET-format timestamp (`"%Y-%m-%d %H:%M:%S ET"`) written immediately after `_account_totals_last_good` on each successful refresh. Surfaced as `portfolio_strip["account_basis_as_of"]` when the Tier-1 fallback fires; both the live and frozen paths fall back to a fresh `datetime.now(_ET)` string when it was never set, so `account_basis_as_of` is never `None` while `account_basis_stale=True`.
- `_ACCOUNT_TOTALS_HTTP_TIMEOUT_S` (DE-EOD-BASIS-001) — named constant (`10`) promoting the bare `timeout=10` literal previously inline in the Composer `requests.get` call inside `_refresh_account_totals()`.
- `_account_totals_timeout_count` (F-010, `DE-OPS-CLUSTER-001`) — module-global counter of known Composer `ReadTimeout` occurrences hit by `_refresh_account_totals()`, incremented under `_account_totals_cache_lock` (this function has 3 real concurrent call sites — scheduler tick, `_notify_cycle_complete`-spawned thread, flush-resync thread — so the increment is never an unsynchronized read-modify-write). Surfaced as aggregation context (`#N`) in the compact one-line WARNING log instead of a full traceback per occurrence; see `_refresh_account_totals()` below.
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

### Liveness Probe

#### `GET /health` — `health()`
Minimal unauthenticated liveness probe (F-005, `DE-OPS-CLUSTER-001`, 2026-07-21). Registered right after `get_csrf_token()` (app.py); exempt from the auth gate via `_AUTH_EXEMPT_ENDPOINTS` (endpoint name `'health'` — the frozenset already carried this entry from the original auth-gate cycle, but no route backed it until now). Read-only: sources `last_successful_cycle_at` from `database.load_state()` (the same top-level engine-written field `_compute_portfolio_strip` reads, see State Helpers below) wrapped in try/except (degrades to `{}` on failure) — never opens a read-write connection, per architecture constraint 5. GET-only; `POST /health` 405s via Flask's default routing (no `methods=["POST"]` registered).

**Response shape:**
```json
{"status": "ok", "daemon_started_at": "<ISO ts>", "last_successful_cycle_at": "<ISO ts> | null"}
```
`status` is a static `"ok"` (the route itself answering IS the liveness signal — a dead process never returns). `daemon_started_at` is the module-level `_DAEMON_STARTED_AT` timestamp. `last_successful_cycle_at` is `None` on a fresh droplet with no completed engine cycle yet — an honest empty-state, not a fabricated timestamp. No secrets, no config values, nothing beyond these three fields. Not in `_SETTINGS_WRITE_ALLOWLIST` scope (read-only route, no write path exists).

---

### Scheduler

#### `run_scheduler() → None`
Runs the `schedule` loop: every minute at `:00` triggers `threaded_trigger()` and `_refresh_account_totals()`; daily at 02:00 runs `_run_trigger_retention()`; daily at 03:00 runs `_run_lens_pipeline()` (gated by `DISABLE_DAEMON_LENS_PIPELINE`); daily at **03:30** runs `_run_incubation_tick()` (the Strategy Incubation Gate's forward-data tick, staggered 30 minutes after the lens-pipeline slot so the two off-hours jobs never contend for the same minute -- see below).

#### `trigger_alpha_bot(force: bool = False) → None`
Spawns `alpha_bot_execution.py` as a subprocess. Passes `--force` when `force=True`. Logs stdout/stderr to `alphabot_daemon.log`. After the subprocess exits (success or `CalledProcessError`), calls `_notify_cycle_complete()` in a `finally` block — the cycle notification is unconditional.

#### `_notify_cycle_complete() → None`
Fan-out hook called from `trigger_alpha_bot()` in a `finally` block. Never raises. Performs three steps in order:

1. **Mark cache stale** — calls `_account_totals_cache.mark_stale()` (O(1), lock-free). All subsequent reads from `_account_totals_cache` return `None`/empty until `_refresh_account_totals()` clears the flag. `mark_stale()` is used instead of `dict.clear()` to avoid a partial-write window: a bare `.clear()` under the write lock would not prevent a lock-free reader from observing an incomplete dict mid-write; `mark_stale()` masks ALL reads atomically in O(1).
2. **Spawn refresh thread** — starts a short-lived daemon thread (`threading.Thread(target=_refresh_account_totals, daemon=True, name="cycle-refresh")`). The thread is started BEFORE the SSE fan-out so the Composer API call is in-flight while the fan-out loop runs. This gives the refresh maximum lead time: by the time the connected client receives the event and its `/api/state` fetch arrives (~50–200 ms), the single Composer call (~100–500 ms) has typically completed. Without this, the cache stays masked until the NEXT per-minute scheduler tick (~55 s) — the SSE-triggered fetch would see blank totals, defeating the feature.
3. **Fan out SSE event** — copies `_sse_clients` under `_sse_clients_lock`, then calls `q.put_nowait("cycle-complete")` for each client queue. Exceptions (full queue, closed) are swallowed per client; the loop always completes. O(1) per client; no I/O.

#### `_refresh_account_totals() → None`
Fetches Composer account-level total-stats (`portfolio_value`, `simple_return`, `todays_percent_change`, `max_drawdown`) and populates `_account_totals_cache`. Must never raise (swallows all exceptions — D-1 contract). HTTP timeout is `_ACCOUNT_TOTALS_HTTP_TIMEOUT_S` (named constant, DE-EOD-BASIS-001).

**Write protocol:** all five keys are written under `_account_totals_cache_lock` (prevents concurrent readers from observing a partial write). After the last key is written, calls `_account_totals_cache.refresh_written()` to clear the stale flag atomically. If Composer returns non-200 or any exception occurs, `refresh_written()` is NOT called: the cache remains masked and consumers see empty-state (honest degradation triggering the AC-8 staleness cue in the client).

**Last-good snapshot (DE-EOD-BASIS-001):** on a genuine 200 response, after `refresh_written()` clears the stale flag, `_account_totals_last_good` is cleared and repopulated from `_account_totals_cache` (still under the lock), then `_account_totals_last_success_at` is stamped with an ET timestamp string. A non-200 response or an exception updates neither — `_account_totals_last_good` and its timestamp only ever advance on a real Composer fetch, so they always reflect the last time Composer actually answered, independent of how many `mark_stale()` cycles have fired since.

Called by the per-minute scheduler AND as a short-lived daemon thread spawned by `_notify_cycle_complete()`.

**Compact read-timeout logging (F-010, `DE-OPS-CLUSTER-001`, 2026-07-21).** A new `except requests.exceptions.ReadTimeout:` branch sits BEFORE the existing `except Exception as _exc:` branch (order matters — a swap would silently dead-code the narrow branch). The known Composer read-timeout case (~30/day in production, ~958 full tracebacks/month pre-fix per the confidence-program audit register) now logs one compact WARNING line (`"_refresh_account_totals: Composer read-timeout (#%d, timeout=%ss) — cache unchanged"`) with `_account_totals_timeout_count` as aggregation context, instead of a full traceback. Any OTHER `requests` exception (`ConnectionError`, etc.) or unexpected exception type still falls through to `except Exception`, keeping its full `exc_info=True` traceback unchanged — the match is timeout-only, never a blanket suppression. The counter increment happens under `_account_totals_cache_lock`; the log call itself reads a stable post-lock snapshot rather than holding the lock during logging I/O. `_ACCOUNT_TOTALS_HTTP_TIMEOUT_S` and all retry/timeout VALUES are byte-unchanged — this is a logging-only fix.

#### `_run_trigger_retention() → None`
Prunes `exit_triggers` rows older than `TRIGGER_TELEMETRY_RETENTION_DAYS` (default 90) and `shadow_history` rows older than `SHADOW_HISTORY_RETENTION_DAYS` (default 180).

#### `_run_incubation_tick() → None` / `_incubation_tick_worker() → None` (strategy-incubation-gate, `DE-INCUBATION-GATE-001`, 2026-07-25)
Non-blocking daily off-hours wrapper for the Strategy Incubation Gate's forward-data tick -- mirrors `_run_lens_pipeline()`/its worker exactly (same lazy-import-inside-a-daemon-thread shape). `_run_incubation_tick()` is the scheduler-facing entry point: it spawns a `daemon=True` `threading.Thread(target=_incubation_tick_worker, name="incubation-tick")` and returns immediately, so the scheduler thread is never blocked by the tick's Composer calls (architecture constraint 1). `_incubation_tick_worker()` does the actual work: lazy-imports `advisors.incubation.run_incubation_tick` (CC-2 -- keeps `advisors.incubation` off `app.py`'s module-load path) inside a `try/except`, calls it, and logs `type(exc).__name__` only on failure (D-1). See `docs/generated/advisors_incubation.md` for the tick's own algorithm.

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

**Stale-cache fallback (DE-EOD-BASIS-001):** a masked `_account_totals_cache` read is not the end of the line — see `_account_totals_last_good` above and the two-tier fallback documented under `_compute_portfolio_strip()` / `GET /api/state` below.

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

**F-1 shared connections, both branches (`DE-OPS-CLUSTER-001`, 2026-07-21).** `get_state()` opens a function-local read-only `_shadow_conn` before its live-branch per-symphony TC/CR/MDD enrichment loop and its `_compute_portfolio_strip()` call, closed in a `finally`. The `closed_frozen`/`pre_market` snapshot branch below opens its OWN `_frozen_shadow_conn` (same pattern, function-local, never a module-global) before ITS per-symphony TC/CR/MDD loop and 3 inline portfolio-level calls (`get_portfolio_today_change`/`get_portfolio_cumulative_return`/`get_portfolio_max_drawdown` — this branch computes its strip inline, not via `_compute_portfolio_strip`), closed once both sections converge. This second connection was added in a follow-up round after review traced the PM's original repro to a Saturday (market closed) — the frozen branch, not the live branch, almost certainly served the measured ~157-connects symptom. **Known residual (LOW, accepted, not blocking):** the frozen-branch close is a plain statement after the per-symphony-loop-through-portfolio-calls span converges, not a `try/finally` wrapping that whole span (unlike the live branch, which does use `try/finally`) — an exception in the per-symphony loop itself outside the narrow `(KeyError, TypeError, ValueError)` catch there could leak `_frozen_shadow_conn`. GC-recovered, no data-integrity risk, requires already-malformed snapshot data that would 500 the route regardless. See `feature-plans/BACKLOG.md`'s "Low priority / tracked follow-on" section for the accepted-residual record.

**Frozen/EOD snapshot recompute — per-field account-basis mirror (DE-EOD-BASIS-001):** When `market_state` is `closed_frozen` or `pre_market`, `get_state()` recomputes `portfolio_strip` from `last_market_close_snapshot` rather than passing through `_compute_portfolio_strip()` — the frozen branch is its own authoritative recompute, never a pass-through of the engine's written (VW) snapshot strip. TC, CR, and `account_value` are each resolved via the identical Tier-1 (last-good) / Tier-2 (honest floor) fallback described under `_compute_portfolio_strip()` below — `_snap_tc_stale` / `_snap_cr_stale` booleans track each field's own cache-vs-last-good source independently — then wrapped through the same `analytics.get_portfolio_today_change_account_basis()` / `get_portfolio_cumulative_return_account_basis()` helpers in two independent `if`/`else` blocks. Previously a single combined gate required BOTH TC and CR to be non-`None` before wrapping EITHER, so a cold CR could collaterally leave a warm TC unwrapped. The Tier-2 `basis="value_weighted"` marker and Tier-1 `account_basis_stale` / `account_basis_as_of` stamps mirror the live path's per-field logic exactly. On the Tier-2 (fully-missing) branch, both TC and CR surface raw VW unchanged on both paths — a post-review fix removed an earlier inconsistency where the frozen TC branch alone nulled `if_held` while every other Tier-2 branch surfaced raw VW; honesty is now signalled by the single `basis` marker on all four branches, not by selectively nulling a field (see "Post-review hardening" in `DECISIONS.md`). The engine's EOD snapshot writer (`alpha_bot_execution.py`) is unaffected — it keeps writing the raw VW strip; the account-basis wrap happens entirely at app read time. See `DE-EOD-BASIS-001` in `DECISIONS.md`.

#### `GET /history`
Render historical performance page.

#### `GET /performance`
Render performance analytics page.

#### `GET /api/performance` -- `api_performance()`

Returns per-day time-series + quantstats metrics. Accepts `scope` ("aggregate"/"symphony"), `days` (int), and `symphony_id` (required when scope=symphony) query params.

**Data-source precedence, scope=aggregate (corrected 2026-07-09, DE-PROD-ACCURACY-001 Finding 4):** the canonical aggregate series is `analytics.get_portfolio_bot_and_held_daily_returns(days=days)` -- the SAME value-weighted `shadow_history` series `/api/hero-chart` compounds. Every `shadow_history` trading day appears, including zero-trigger days. This REPLACES the pre-2026-07-09 primary path (below), which served only post-mortem `triggers` arrays -- a selection-biased sample of symphonies that triggered that day, valued at exit-moment snapshot, that silently dropped zero-trigger days and could show a materially different picture than the Overview hero chart over the identical period. **`scope=symphony` fixed 2026-07-17 (`DE-MATH-R0-001` AC-3, `DE-MATH-AUDIT-001` MA-6/MAPERF-01):** now sources `analytics.get_symphony_bot_and_held_daily_returns(symphony_id, days=...)` -- the per-symphony analogue of the aggregate's canonical continuous `shadow_history` source (Bot=`shadow_return`, Held=`current_return`, last row per trading day) -- NEVER the post-mortem trigger-array path this sentence used to describe as current. Every `shadow_history` trading day the symphony has appears, triggered or not; a selection-biased K-trigger event sample no longer gets annualized as K consecutive trading days (pre-fix: a 4-trigger sample could annualize to ~209.8% "CAGR").

**Day-1 fallback tiers (AC-2/AC-2b, DE-LIVE-DASH-001), field-mapping corrected 2026-07-09:** when the canonical series above is still empty (fresh droplet, shadow_history not yet populated for scope=aggregate; or scope=symphony's post-mortem-history path is empty):

1. **Multi-day shadow_history:** `analytics.get_portfolio_bot_and_held_daily_returns()` (no `days` arg) -- used when shadow_history has >= 2 distinct trading days.
2. **Single-day shadow_history (AC-2b):** `analytics.get_single_day_shadow_returns()` -- used when the multi-day function returns `None` (fewer than 2 distinct trading days, i.e. fresh droplet on its first trading day). Returns a 1-element `([date], [bot_pct], [held_pct])` tuple so the chart is never blank. `insufficient_history` remains `True` (honest -- 1 < `_PERFORMANCE_MIN_HISTORY_DAYS`).

The `< 2` guard in the tier-1 fallback is correct and unchanged; tier-2 handles day-one without weakening the statistical guard.

**AC-4 scope-gated fallbacks (`DE-MATH-R0-001`, `DE-MATH-AUDIT-001` MA-7/MAPERF-02):** both day-1-droplet `if not dates:` fallback tiers above are now gated to `scope == 'aggregate'` only. Pre-fix, a zero-trigger `scope=symphony` request fell through to these unconditional fallbacks and rendered the WHOLE PORTFOLIO's series/metrics under that symphony's name; a symphony-scoped request with no data now renders an honest empty state.

**Response field semantics (option B, corrected 2026-07-09, DE-PROD-ACCURACY-001 Finding 4 Revise):** all three producer paths above return a `(dates, bot, held)` tuple. The route maps this to the payload as **`live_returns` = if-held, the still-held Composer account** (`held` -- weighted `current_return`) and **`shadow_returns` = the Planet-Stopper-exited counterfactual** (`bot` -- weighted `shadow_return`) -- matching every `performance.js` legend label and the route's own docstring. Prior to this fix, the two day-1 fallback paths mapped this tuple INVERTED relative to the canonical path and every UI label; this is now consistent across all three producer paths. `live_metrics`/`shadow_metrics` (quantstats dicts) follow their corrected series.

**AC-5 YTD contract (`DE-MATH-R0-001`, `DE-MATH-AUDIT-001` ma-perf-03):** the `days` query param accepts the literal string `'ytd'` in addition to an integer trading-day count -- resolved via `analytics._window_cutoff_date` to a Jan-1 CALENDAR cutoff applied to the FULL fetched series (`_slice_series_by_window_cutoff`), the same cutoff `/api/hero-chart` and `/api/strip` use for the same token, closing the pre-fix mismatch where the same picker click windowed the chart by trading days and the strip by calendar days (~40% divergence at 1y). The six numeric buttons (30/60/90/125/252/1260) are UNCHANGED -- still deliberate trading-day counts.

**Response shape:** `{scope, dates, live_returns, shadow_returns, live_metrics, shadow_metrics, observation_count, insufficient_history, window_days}` -- plus `symphony_id_recognized` (bool) when `scope=='symphony'` (see AC-4 below).

**AC-4 `symphony_id_recognized` (F-023, `DE-PERFVIEW-ID-MISMATCH`, 2026-07-20):** when `scope == 'symphony'`, the response additionally carries `symphony_id_recognized: symphony_id in database.load_state()` -- `True` for a real bot_state hash key (even one with zero `shadow_history` rows, still the honest "Insufficient history" case), `False` for a totally unrecognized id (stale/typo'd picker value, a name sent where a hash was expected, etc.). Scoped to `scope=symphony` only -- never present on a `scope=aggregate` response. `static/performance.js`'s `renderBanner()` renders a distinct "Strategy not recognized" message on `False` instead of silently reusing the generic insufficient-history banner for a broken id -- see `GET /api/performance/symphonies` below for the companion id/name-source fix this pairs with.

Read-only: no DB writes, no network I/O, not in `_SETTINGS_WRITE_ALLOWLIST`.

#### `GET /api/performance/symphonies` -- `api_performance_symphonies()`

Sorted `[{id, name}]` list of live symphonies backing the Performance-tab (and AI Advisor tab) symphony picker -- `id` is the Composer hash key `shadow_history.symphony_id` actually stores, `name` is the display label. Sourced from `database.load_state()` (bot_state), skipping any entry that isn't a dict or is missing a `name` field; sorted by `name`. Empty bot_state returns `{"symphonies": []}`, never a crash.

**F-023 fix (`DE-PERFVIEW-ID-MISMATCH`, 2026-07-20):** previously sourced from `analytics.get_history_with_cache_invalidation` + `analytics.list_available_symphonies` -- a post-mortem-history-derived list of bare display NAMES used as both the picker's label AND its value. The picked NAME was then sent as `symphony_id` into `GET /api/performance?scope=symphony`, which queries `shadow_history WHERE symphony_id = ?` -- a hash-keyed column -- so every one of the 11 live symphonies matched zero rows, rendered behind the same "Insufficient history" banner genuinely-sparse data uses. Data was healthy (7,330+ real rows existed under the correct hash); this was a pure app-layer id/name mismatch, never a data gap. See `GET /api/performance`'s `symphony_id_recognized` field above for the companion AC-4 fix -- a future id/name mismatch now surfaces distinctly instead of masquerading as the honest empty state.

#### `GET /api/history/<int:days>` -- `get_history()`

Returns historical portfolio summary for the last `days` days.

**Bug fix (AC-3, DE-LIVE-DASH-001):** Previously called `analytics.get_history_summary(days=days)` without the `base_dir` argument, which defaulted to the process CWD and found no files. Now calls `analytics.get_history_summary(days=days, base_dir=analytics._POST_MORTEMS_DIR)` -- the same constant used by every other post-mortem reader.

**`todays_exits` fallback (corrected 2026-07-09, DE-PROD-ACCURACY-001 Finding 3):** When `stats["todays_exits"]` is empty (no post-mortem written yet today -- true EVERY trading day before the 15:54 ET write, not just day-one), the route reads `exit_triggers` filtered to the current ET trading day (`WHERE substr(ts_et, 1, 10) = <today ET date>`) and backfills them into the response, mapped to the shape `history.js` consumes: `ts` (time-of-day substring of `ts_et`), `symphony_id`, `symphony_name` (resolved via a name map built from `database.load_state()`, falling back to the raw id), `reason` (`triggered_reason`), `detail` (`at_return`). A zero-exit day today renders honestly empty rather than backfilling stale historical rows.

**Prior bug (fixed 2026-07-09):** the original fallback had no date filter at all (`SELECT ... ORDER BY ts_utc DESC LIMIT 50`) and ran every trading morning before 15:54 ET, not just on a fresh droplet's first day as its comment claimed -- so the History tab rendered up to 50 all-time triggers labeled "Today's exits," with most cells blank because the old fallback's field shape (`ts_utc`/`at_return`/`triggered_reason`) didn't match what `history.js` reads (`ts`/`reason`/`detail`), and the Symphony column showed the raw hash id with no name resolution. The old fallback also overwrote `stats["trigger_count"]` with the 50-row feed length while `total_saved`/`win_rate` still derived from the true windowed count -- headline stats were internally inconsistent. The current fallback never overwrites `trigger_count`. Verified against the real droplet DB copy: the corrected fallback returns exactly today's 11 exits (of 87 all-time) with 11/11 names resolved.

**Post-mortem-path Time-column fix (DE-PROD-ACCURACY-001 Finding 11):** on the healthy EOD path (`stats["todays_exits"]` populated from a written post-mortem file, not this fallback), the producer now maps `time_triggered` into the `ts` field the table consumes -- previously the Time column rendered an em-dash even when a post-mortem file existed for today.

**AC-6 Detail-column single semantic (`DE-MATH-R0-001`, `DE-MATH-AUDIT-001` MAPERF-06, fixed 2026-07-17):** the intraday `todays_exits` fallback's `detail` field previously emitted the raw `at_return` (exit-level return) under the same `"detail"` key and `"+X.XX%"` cell the post-mortem path uses for `saved_pct_guard_alpha` (a guard-alpha-pp value) -- two different quantities silently interchangeable depending on time-of-day, the root of an operator sighting ('TP saved me 10%' that didn't match the History total). The fallback query now joins each `exit_triggers` row against the symphony's latest `shadow_history.current_return`; a new `_guard_alpha_detail(at_return, current_return)` helper computes `at_return - current_return` (honest `None` on either missing input) -- matching the post-mortem path's semantic exactly. A schema-compatibility fallback (DB without `shadow_history`) emits `detail=None` explicitly rather than reintroducing the retired raw-`at_return` value.

**F-008 read-time validity guard (AC-5b, `DE-POSTMORTEM-INTEGRITY-001`, 2026-07-20):** `analytics.get_history_summary` -- the function that produces this route's `total_saved`/`total_alpha`/`win_rate`/`by_reason` headline stats on the EOD (post-mortem) path -- now routes every trigger entry through the SAME `analytics.is_valid_post_mortem_entry` guard `guard_alpha_summary()` uses (see `GET /api/guard-alpha-summary` above), so an entry with a missing/unrecognized `if_held_source` no longer contributes to the History tab's totals either. The `todays_exits` intraday fallback (above) is unaffected -- it reads live `exit_triggers`/`shadow_history` rows directly, not post-mortem files, so the provenance stamp doesn't apply there. See `docs/generated/analytics.md`.

#### `GET /api/logs/<symphony_id>`
Returns symphony execution logs.

#### `GET /api/triggers`
Returns recent exit trigger telemetry rows.

#### `GET /api/accounts`
Returns Composer account information.

#### `GET /api/hero-chart/<window>`
Returns hist_dates/hist_bot/hist_held for the requested time window (`30d`/`60d`/`90d`/`125d`/`ytd`/`1y`/`all`).

**AC-5 (`DE-MATH-R0-001`, `DE-MATH-AUDIT-001` ma-perf-03, fixed 2026-07-17):** fetches the FULL `shadow_history` series once (`analytics.get_portfolio_bot_and_held_daily_returns(days=None)`) and slices to the calendar cutoff `analytics._window_cutoff_date` resolves for the token, via `_slice_series_by_window_cutoff` -- the SAME cutoff `/api/strip` already used. Pre-fix, this route trading-day-sliced via a per-token `fetch_days` count (e.g. `30d` = last 30 TRADING days) while the strip calendar-windowed the same token -- same-click chart and strip could diverge by ~40% at `1y`. `window='all'` (and any unrecognized token) resolves to no cutoff (full series). The bespoke YTD trim (`_trim_ytd`, a manual Jan-1-string-compare helper) is deleted -- subsumed by the shared cutoff helper. Each series (bot/held) is compounded independently into its own cumulative curve; `insufficient` is a soft UI floor (`_min_days` per token, scaled to calendar-window semantics), not a math-correctness gate.

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
Operator-initiated emergency "panic-stop" account liquidation — the dashboard's only trade-action surface. Requires `confirm_account_id == account_id` AND `confirm_phrase == "LIQUIDATE"` (400 otherwise) before any env/credential check. Fires a Discord alert + `_daemon_log.error` audit line on EVERY invocation regardless of `live_mode`. When `LIVE_EXECUTION` is not true, returns `{"status": "dry_run", "dry_run": True, "executed": False, ...}` and never spawns the liquidation thread (real-money safety gate). When live, spawns `perform_account_liquidation(account_id, key, secret, live_mode)` on a background `threading.Thread`; the route itself returns immediately — the thread's own return value (see below) is NOT surfaced to the HTTP response.

**`perform_account_liquidation()` (`app.py:3392`) — per-symphony confirmation + isolation (F-003, `DE-PANIC-STOP-CONFIRM-001`, 2026-07-20):** GETs `{account_id}/symphony-stats-meta`, then for each returned symphony POSTs `.../go-to-cash`. Each symphony's sell attempt runs in its OWN `try`/`except Exception`, so a fault (a status rejection OR a raised exception) on one symphony never aborts the rest of the queue. Only `sell_resp.status_code in (200, 201, 202)` logs the success line (`"Liquidated {name} (HTTP {code})"`); any other status logs `"LIQUIDATION FAILED {name} — HTTP {code} — {text[:200]}"` (response body truncated to 200 chars — never logged unbounded); a raised exception logs `"LIQUIDATION FAILED {name} — {type(e).__name__}"` (exception TYPE only, never the raw message, mirroring `alpha_bot_execution.py`'s `[API CRASH]` convention). The function now RETURNS a structured per-symphony outcome dict keyed by name — `{"ok": True, "status": 200}` on success, `{"ok": False, "status": code, "reason": text[:200]}` on a non-2xx status, `{"ok": False, "reason": type(e).__name__}` on a raised exception — `{}` (never `None`) when the GET fails, the symphony list is empty, or `live_mode=False`. This is the operator's per-symphony ground truth during a real emergency; **no dashboard UI consumes it yet** (the calling `threading.Thread` discards the return value) — a follow-up display surface is an explicit deferral, out of scope for this cycle (`feature-plans/fix-f003-panic-stop.md`). No change to the GET/POST URLs, headers, `json={}` payload, `timeout=10`, or the `live_mode` gating — confirmation/logging/isolation/return-value only (AC-5 hard no-trade-behavior-change guard).

**F-003 residual — malformed-entry isolation (`DE-OPS-CLUSTER-001`, 2026-07-21).** The per-symphony `name = sym.get(...)` and `sell_url` extraction was moved INSIDE the per-symphony `try` (was outside it) — a malformed entry (non-dict `sym`) raising `AttributeError` during extraction used to escape to the function's OUTER `except`, aborting the ENTIRE liquidation queue instead of isolating just that one symphony, defeating the per-symphony isolation `DE-PANIC-STOP-CONFIRM-001` shipped. `name` is now reset to `None` INSIDE the for-loop at the top of each iteration (not once before the loop, so no stale name can bleed across entries) — if extraction itself raises, the `except` branch keys the outcome dict as `<malformed-entry-{idx}>` (the entry's list index) instead of `None`, so a malformed entry still produces a distinguishable per-symphony FAILED outcome and the queue continues. Valid-entry sell-request construction is byte-unchanged.

#### `GET /api/strip/<window>` -- `get_windowed_strip()`

Returns comparison strip metrics (guard-alpha/CR/MDD/vol) re-windowed for the selected window token (30d/60d/90d/125d/ytd/1y/all). Threads the token through `analytics.compute_windowed_portfolio_strip`.

**Intraday guard-alpha fallback (AC-4/AC-4b, DE-LIVE-DASH-001):** when `insufficient_history=True` AND `guard_alpha is None` (**AC-8, `DE-MATH-R0-001`, `DE-MATH-AUDIT-001` MAPERF-04, fixed 2026-07-17 -- explicit `is None`, no longer a falsy check**; `analytics.compute_windowed_symphony_guard_alpha` returns `None` specifically for the `<2`-in-window-rows conservatism floor (AC-8b, see below) and a genuinely-computed `0.0` for real zero divergence -- pre-fix the falsy check could not tell them apart and clobbered a legitimate `$0.00` window with a fallback estimate), the route queries `exit_triggers` + `shadow_history` for per-symphony returns and reads `position_value` from `database.load_state()` (the JSON blob accessor, `current_value` field per symphony_id). Computes `alpha_per_symphony = at_return - current_return` weighted by `position_value`. When at least one triggered symphony has valid values, the strip dict is updated with the computed `guard_alpha` and `intraday_only=True` (additive field -- callers show a "Today only" label instead of "+0.00%"). The 15-second auto-refresh floor keeps this current.

**AC-4b root cause (fixed 7b5f29d):** The original correlated subquery `SELECT position_value FROM bot_state WHERE symphony_id = t.symphony_id` assumed a columnar bot_state schema; the real schema is a single-row JSON blob. The `OperationalError` was swallowed by `except Exception` at `app.py:2214` → `guard_alpha` stayed `0.0`, `intraday_only` never set. Fixed with the same `load_state()` pattern as AC-1b.

**AC-8 day-filter (MAPERF-04, `DE-MATH-R0-001`):** the fallback's `exit_triggers` query is now filtered to the current ET trading day (`WHERE substr(t.ts_et, 1, 10) = ?`) -- pre-fix it paired an exit_triggers row from ANY day against the symphony's LATEST `current_return`, a cross-day-incoherent subtraction. A schema-compatibility except-branch degrades to the unfiltered query only when the DB lacks a `ts_et` column entirely (a real migrated schema always has it -- this is compatibility-only, not a reintroduction of the cross-day estimate). **AC-8b (`compute_windowed_symphony_guard_alpha`, `analytics.py`):** the underlying `<2`-row floor now returns `None` (unknown/insufficient) instead of a fabricated `0.0` -- the floor's statistical conservatism is unchanged, only its return encoding. `compute_windowed_portfolio_strip`'s per-symphony aggregation already skip-and-counted on `None` (pre-existing `if sym_alpha is None: continue`), so no consumer change was needed there.

Read-only; builds symphony list from state DB, never reruns the engine.

#### `GET /api/autotune-runs`
Returns paginated autotune run history via `database.get_all_autotune_runs`. Response normalized via `_normalize_autotune_row`.

#### `GET /api/advisor-observations`
Returns `advisor_observations` rows for a symphony. Accepts `?symphony_id=` query parameter.

#### `GET /api/guard-alpha-preconditions` -- `guard_alpha_preconditions()` (guard-alpha-preconditions cycle, `DE-GUARD-ALPHA-PRECONDITIONS-001`, 2026-07-23)

Per-symphony Kaminski & Lo (2014) stop-justification precondition read. **Distinct from `GET /api/guard-alpha-summary` below** -- that route reports REALIZED $-saved from stops that already fired; this route reports a THEORETICAL PRECONDITION (lag-1 return persistence vs. daily Sharpe ratio) on whether the statistical evidence supports a trailing stop's expected-return case at all, independent of realized outcomes. Math layer: `guard_preconditions.py` (see `docs/generated/guard_preconditions.md`).

**Response shape:** `{"symphonies": {<symphony_id>: {"replay": <sample>, "shadow": <sample>}, ...}}`, where each `<sample>` is:

| Field | Type | Description |
|-------|------|-------------|
| `rho` | float \| null | Lag-1 sample autocorrelation, or `null` when unavailable/degraded/NaN. |
| `rho_ci` | float \| null | 95% CI half-width for `rho`. |
| `sharpe_daily` | float \| null | Daily (non-annualized) Sharpe ratio. |
| `n_obs` | int | Observation count (`0` on a degraded row). |
| `verdict` | str | One of the 5 `guard_preconditions.classify_stop_justification` classes -- `INSUFFICIENT_DATA` on a degraded row. |
| `sample_source` | str | `"if_held_replay"` or `"shadow_history"` -- ALWAYS the stable sample-family identifier, never a reason string. |

**UNIFORM DEGRADED-ROW CONTRACT (symmetric full-object degradation):** both `"replay"` and `"shadow"` are ALWAYS a complete 6-field object, never bare `null`. An unavailable sample (cold/missing replay cache, or a symphony with no `shadow_history` rows yet) renders `{rho: null, rho_ci: null, sharpe_daily: null, n_obs: 0, verdict: "INSUFFICIENT_DATA", sample_source: <stable id>}` -- the same 5-class vocabulary a genuinely-thin real sample would produce, so callers never null-check a sample before reading its fields (AC-8's two-sample-disagreement display keys on stable `sample_source` identity, which would break if degradation used a different shape or a variable reason string in that field). This contract originated in the implementer's (ga-flask) WIP -- the test-writer's original RED tests assumed an asymmetric replay-null/shadow-null shape and adopted and extended the uniform contract after review.

**Strict-JSON-safety guard:** an internal `_json_safe_float()` helper sanitizes `NaN`/`Infinity` to `None` at the JSON-serialization boundary only -- `compute_persistence_stats` legitimately returns `math.nan` on a genuinely flat (zero-variance) series, and Python's `json` module serializes that as the bare, RFC-8259-invalid tokens `NaN`/`Infinity`, which a real browser's `response.json()` rejects, silently breaking the whole panel via `static/performance.js`'s catch-all `.catch()`. The verdict classification itself (`classify_stop_justification`) still runs against the original, unsanitized `PersistenceStats` object -- only the JSON-facing float fields are sanitized, never the classification logic. Found by the test-writer running the live route during sufficiency review, not by inspection alone; fixed by the implementer (ga-flask).

**Cache-hit-only on the replay sample (operator ruling, hard contract):** this route never triggers a network fetch or 250-day history assembly to backfill a cold replay cache -- `autotuner.build_if_held_replay_series` (see `docs/generated/autotuner.md`) may serve from its local file-cache only; exhausting the bounded backward-scan window (`AUTOTUNE_CACHE_MAX_AGE_TRADING_DAYS=10` NYSE trading days as of the R2 live-gate correction, 2026-07-24) without a hit degrades per the uniform contract above rather than fetching. See `DE-GUARD-ALPHA-PRECONDITIONS-001` in `DECISIONS.md` for the full ruling and rationale.

**Phantom-key exclusion (hygiene cycle, `DE-HYGIENE-R1-001`, 2026-07-25).** `bot_state`'s top level mixes real per-symphony sub-dicts with portfolio-level metadata (`date`, `last_execution_mode`, `last_market_close_snapshot`, `last_successful_cycle_at`, `post_mortem_run`) -- this route previously iterated ALL of them, rendering the 5 metadata keys as phantom `INSUFFICIENT_DATA` "symphony" rows (16 served on the droplet vs. 11 real). Fixed via a new structural discriminator, `_is_symphony_state_entry(value) -> bool` = `isinstance(value, dict) and "name" in value` (`app.py:3114`) -- the SAME shape check already used at 7 other `app.py` call sites (e.g. `app.py:1205, 1214, 1320, 1363, 1504, 2760, 3012`), not a name denylist: every real symphony is unconditionally stamped with `"name"` by the engine (`alpha_bot_execution.py:1633`) as soon as it is created, which no metadata value (a plain scalar, or the differently-shaped `last_market_close_snapshot` dict) ever carries -- so a future metadata key is excluded automatically without a code change. Wired at the route's single iteration site (`app.py:3218`, `for sym_id, sym_data in bot_state_dict.items():` with a `continue`-on-exclude guard, replacing a bare `for sym_id in bot_state_dict:`). A real-but-thin symphony sub-dict that still carries `"name"` still passes the discriminator and renders its existing honest degraded verdicts -- no-self-regression. `guard_preconditions.py`, `alpha_bot_execution.py`, and `math_engine.py` carry zero diff. See `DE-HYGIENE-R1-001` in `DECISIONS.md`.

**Key properties:**
- **Read-only.** No SQL directly in this route -- `database.load_state`, the two sample accessors (`autotuner.build_if_held_replay_series`, `analytics.get_shadow_current_return_daily_series`), and the math layer are the only calls made.
- **Never a 500 (AC-6).** Each sample source is independently `try/except`-wrapped; a failure degrades just that one sample. A per-symphony outer `try/except` skips that symphony's entry entirely on an unexpected failure rather than surfacing a half-built one -- this is the one case that produces a genuinely MISSING key rather than a degraded-but-present row (distinct from the uniform per-sample degradation contract above, which always produces a complete object).
- **Auth-gated.** Covered by the global `_auth_before_request` hook, same as `guard_alpha_summary()` -- no additional decorator.
- **Not in `_SETTINGS_WRITE_ALLOWLIST`, no `LIVE_EXECUTION` interaction, no CSRF needed (GET).**

**Consumed by:** `fetchGuardAlphaPreconditions()` in `static/performance.js` (401-guarded via the standard `if (!response.ok) return;` pattern). Renders into `templates/performance.html`'s `data-testid="guard-alpha-preconditions-panel"` -- see `docs/generated/static_performance_js.md` for the render logic.

#### `GET /api/guard-alpha-summary` -- `guard_alpha_summary()`

Returns cumulative dollar-saved aggregate and guard-event count.

**Dual-path data source (AC-1, DE-LIVE-DASH-001):**

- **Primary (EOD) path:** when `post_mortem_*.json` files exist in `analytics._POST_MORTEMS_DIR`, sums `saved_dollars` from all VALID trigger entries and sets `source="post_mortem_eod"`. Dollar figures are snapshot-time (computed by `reporting.py` at exit time). **Read-time validity guard (F-008, `DE-POSTMORTEM-INTEGRITY-001`, 2026-07-20):** a trigger entry contributes only if `analytics.is_valid_post_mortem_entry(entry)` returns True -- i.e. its `if_held_source` field is one of the 3 producer-recognized values (`shadow_history`/`shadow_history_post_cutoff`/`bot_state_fallback`, per `reporting.py`'s three-tier if-held sourcing). Entries with a missing or unrecognized `if_held_source` (e.g. pre-PR-#80 captures, the real 2026-06-22 and 2026-07-09 contaminated days) are excluded and counted in the new `excluded_invalid_count` field, never silently summed.
- **Intraday fallback path (AC-1/AC-1b):** when no post-mortem files exist (day-1 droplet), queries `exit_triggers` + `shadow_history` for per-symphony returns, and reads `position_value` from `database.load_state()` (the JSON blob accessor keyed by symphony_id -- `current_value` field). Formula per exit: `saved = (at_return - current_return) / 100 * position_value`. Rows where any value is NULL are skipped. Sets `source="exit_triggers_intraday"` and `basis_label="intraday estimate -- updates live"`. On fallback DB error, returns `guard_event_count=0`, `cumulative_saved_dollars=0.0`, `basis_label="no guard events yet"`.

  **AC-1b root cause (fixed 93bd62c):** The original implementation used a correlated subquery `SELECT position_value FROM bot_state WHERE symphony_id = t.symphony_id` which assumed a multi-row columnar schema. The real production `bot_state` is a single-row JSON blob (`id INTEGER, data TEXT`). The subquery raised `OperationalError`; the outer `except Exception` swallowed it silently, producing zero results despite real exit_triggers rows.

  **AC-8 day-filter (`DE-MATH-R0-001`, MAPERF-04, same-class sibling of the strip fallback above, fixed 2026-07-17):** the dollar-estimate rows are now day-filtered to the current ET trading day; `guard_event_count` stays the true all-time `COUNT(*)` -- only the money-math rows are day-scoped. Same schema-compatibility except-branch as the strip fallback.

**Windowed aggregation (`?window=<token>`, `DE-GAS-COHERENCE-001`, 2026-07-29):** the route now accepts an optional `window` query parameter using the SAME token vocabulary the hero-strip picker emits and `/api/strip/<window>` already accepts (`_STRIP_WINDOW_TOKENS = {"30d","60d","90d","125d","ytd","1y","all"}`), resolved via the SAME `analytics._window_cutoff_date` the strip route uses -- never a second cutoff scheme. The cutoff is applied by comparing each `post_mortem_<date>.json` filename's embedded date against the resolved cutoff BEFORE the file's triggers are folded into the running sum -- an out-of-window file is skipped entirely (its `saved_dollars`/`saved_dollars_realized`/dates never enter `cumulative_saved_dollars`, `saved_dollars_realized`, or `date_range`). Omitting `window` (or passing an unrecognized token) resolves to `"all"` -- BYTE-IDENTICAL to today's pre-cycle all-time default, a regression guard for existing callers (the Discord aggregation context and `tests/app/test_guard_alpha_summary_route.py`). Never a 404/500 on a garbage token -- this is a read-only advisory route.

**Revise 2 (gas-review sufficiency finding, 2026-07-29):** the day-1 `exit_triggers` fallback branch originally gated on the WINDOW-FILTERED `dates` list being empty -- but `dates` comes back empty in TWO genuinely different situations: (a) no post-mortem files exist at all (the true day-1 case the fallback exists for), and (b) real post-mortem files exist, just none of them fall inside the selected `window` (every file hit the cutoff `continue` before contributing a date). Case (b) was wrongly falling through to the day-1 fallback, flipping `source` to `"exit_triggers_intraday"` and `basis_label` to `"no guard events yet"` -- dishonestly implying no guard event has EVER occurred when a real one exists, just outside the selected window. Fixed: the day-1 fallback now gates on the UNFILTERED `files` glob being empty, not the window-filtered `dates` list. Case (b) now returns an honest window-scoped zero (`cumulative_saved_dollars=0.0`, `guard_event_count=0`, `source="post_mortem_eod"`, `basis_label="no guard events in this window"`) -- matching `analytics.get_history_summary`'s own honest 0.0 on the identical fixture (the byte-parity proof above extends to the zero case too).

**Byte-parity with History's own aggregate (the "same range -> same number" proof):** for any shared day-count token, the windowed sum here equals `analytics.get_history_summary(days=N)["total_saved"]` computed over the IDENTICAL post-mortem files -- both routes sum the identical `saved_dollars` field through the identical `is_valid_post_mortem_entry` gate and the identical cutoff arithmetic. This holds AT `1y` specifically because `templates/history.html`'s "1Y"/"5Y" window-picker buttons were corrected in the SAME cycle from the legacy trading-day-shaped `252`/`1260` to the app-wide calendar-day `365`/`1825` (see `docs/generated/static_history_js.md` and `docs/generated/analytics.md`) -- before that correction, "1 year" meant two different day-counts depending which surface you were on, so no byte-parity claim could have held at that token. See `tests/app/test_guard_alpha_summary_windowed.py`.

**Friction-aware net-of-friction disclosure (`DE-AUDIT-BL3-001`, BL-3, 2026-08-05):** the audit (`docs/audit/TWO-WEEK-REVIEW-2026-08-04.md` Finding M1) found both `$-saved` bases friction-blind -- `reporting.py`'s `saved_dollars`/`saved_dollars_realized` VALUE computations carry no trading-cost term, while the team's own optimizer already models `SIM_EXIT_FRICTION_PCT=0.5` percentage points (`autotuner.py:1463`) and is structurally FORBIDDEN from ever reaching the live engine (`tests/autotuner/test_exit_friction_blast_radius.py`'s `_FORBIDDEN_FILES={alpha_bot_execution.py, math_engine.py}`). This is display-layer-only: `guard_alpha_summary()` gains two additive net-of-friction fields (below) computed inside the SAME per-entry loop that already sums the gross fields; the gross `saved_dollars`/`saved_dollars_realized` VALUE computations in `reporting.py` and the friction constant's 3 replay-accounting use sites in `autotuner.py` are byte-unchanged. The day-1 `exit_triggers` intraday-fallback branch also nets friction (same percentage-before-dollar-conversion shape), so all three bases this route can report are friction-consistent. Both new fields honor the SAME empty-state gating the gross fields already use (`guard_event_count==0`/`realized_coverage.with_data==0`) -- a review-found gap (`bl3review`, fixed at commit `40b130e3`) initially let the net-of-friction lines render a spurious "$0.00" beside the gross line's honest "No guard events yet", now closed. See `docs/generated/static_index_js.md`'s `fetchGuardAlphaSummary` section for the render side and `DE-AUDIT-BL3-001` in `DECISIONS.md` for the full record.

**Response shape:**
| Field | Type | Description |
|-------|------|-------------|
| `cumulative_saved_dollars` | float | Aggregated savings. 0.0 when no events or values are NULL. Windowed to `window` when present (see above). |
| `guard_event_count` | int | Total exit-trigger count (VALID entries only, F-008). Windowed to `window` when present. |
| `excluded_invalid_count` | int | Count of trigger entries excluded by the F-008 validity guard (missing/unrecognized `if_held_source`). 0 when every entry is valid, or on the intraday-fallback path (the guard applies to the EOD path only). |
| `date_range` | dict | `{earliest, latest}` ISO dates from filenames. EOD path: brackets ONLY the in-window files' own dates when `window` is present (not the cutoff bound, not the all-time earliest/latest) -- the full history's earliest/latest when `window` is omitted/`"all"`. `{null, null}` when using the intraday path, OR the Revise-2 windowed-empty case (real files exist, none in-window). |
| `basis_label` | str | "snapshot-time basis, since <date>" (EOD, in-window events exist), "no guard events in this window" (`DE-GAS-COHERENCE-001` Revise 2, 2026-07-29 -- real post-mortem files exist overall but none fall inside the selected `window`; honest window-scoped zero, distinct from the next state), "intraday estimate -- updates live" (intraday fallback), "no guard events yet" (no post-mortem files exist AT ALL -- true day-1). |
| `source` | str | `"post_mortem_eod"` or `"exit_triggers_intraday"`. Callers use this to qualify the display. |
| `window` | str | `DE-GAS-COHERENCE-001`, 2026-07-29: echoes the RESOLVED window token (mirrors `/api/strip/<window>`'s own echo-back pattern, so a UI label can never silently mismatch the value backing it) -- `"all"` when the param was omitted or unrecognized. |
| `saved_dollars_realized` | float | exit-friction-realized-savings (`DE-EXIT-FRICTION-REALIZED-001`, AC-6): sum of `saved_dollars_realized` across VALID trigger entries that carry the field (Stage 2's realized/marks basis -- see `docs/generated/reporting.md`). Additive to `cumulative_saved_dollars`, NEVER a replacement (DE-GUARD-ALPHA-SAVED-001 snapshot-basis semantics preserved verbatim). `0.0` when no entries have realized data (intraday path, or every entry pre-dates the feature). |
| `realized_coverage` | dict | `{"with_data": int, "total": int}` (AC-7). An entry missing `saved_dollars_realized` is EXCLUDED from the sum, COUNTED in `total`, never counted in `with_data`, never silently substituted with the snapshot-basis value. Both `0` on the intraday path (the field only ever populates from post-mortem files). |
| `cumulative_saved_dollars_net_of_friction` | float | `DE-AUDIT-BL3-001` (BL-3, 2026-08-05): snapshot-basis sibling of `cumulative_saved_dollars`, net of `autotuner.SIM_EXIT_FRICTION_PCT` (0.5pp -- the SAME friction constant the optimizer's own replay accounting already assumes, imported via a lazy `import autotuner` and referenced by attribute access, never a second local constant). Friction is subtracted at the PERCENTAGE level per entry (`symphony_value * (saved_pct_guard_alpha - SIM_EXIT_FRICTION_PCT) / 100.0`) BEFORE dollar conversion -- matches `reporting.py:92-95`'s own `saved_dollars` formula shape, never a post-hoc subtraction on the aggregate dollar sum. An entry missing `symphony_value` or `saved_pct_guard_alpha` (a legacy pre-BL-3 capture) is EXCLUDED from this sum, never coerced to a fabricated `0.0`. Respects the same `?window=` filtering as the gross field. `0.0` in the honest empty state (`guard_event_count == 0`). Can be NEGATIVE even when `cumulative_saved_dollars` is positive -- this is the whole point of the fix (see the audit-quantified +0.13pp-gross/-0.37pp-net window finding below). |
| `saved_dollars_realized_net_of_friction` | float | `DE-AUDIT-BL3-001`: realized-basis sibling of `saved_dollars_realized`, same friction-net contract -- `saved_dollars_realized - symphony_value * SIM_EXIT_FRICTION_PCT / 100.0` per entry carrying `saved_dollars_realized` (algebraically equivalent to the percentage-first derivation for exact inputs). An entry with no realized coverage contributes nothing -- never substitutes the snapshot-basis value for a missing realized entry. `0.0` in the honest empty state (`realized_coverage.with_data == 0`). |

**Key properties:**
- **Read-only.** Not in `_SETTINGS_WRITE_ALLOWLIST`, no `LIVE_EXECUTION`, no DB writes.
- **Malformed-file resilient AND semantically-invalid-entry resilient (two distinct guards, F-008).** Each post-mortem FILE is wrapped in `try/except (OSError, json.JSONDecodeError)` for syntactic corruption; failures log the basename only and skip the whole file. Independently, each trigger ENTRY within a successfully-parsed file is checked via `analytics.is_valid_post_mortem_entry` for semantic provenance validity -- an untrustworthy entry is excluded without affecting its file's other (valid) entries. Always returns 200.
- **Auth-gated.** Covered by the global `_auth_before_request` hook (DE-AUTH-001); unauthenticated XHR receives 401.

See `DE-GAP-001`, `DE-LIVE-DASH-001`, `DE-POSTMORTEM-INTEGRITY-001`, `DE-EXIT-FRICTION-REALIZED-001`, `DE-GAS-COHERENCE-001`, and `DE-AUDIT-BL3-001` in `DECISIONS.md`.

**Consumed by:** `fetchGuardAlphaSummary(windowToken)` in `static/index.js` -- as of `DE-GAS-COHERENCE-001`, called with the currently-active hero window token on page load, on every window-picker click, and on every `cycle-complete` SSE event, so the panel re-windows in lockstep with the rest of the hero instead of always showing all-time. Populates `#dollar-saved-headline`, `#dollar-saved-verb` (new, sign-conditional "saved"/"lost"), `#guard-event-count`, `#dollar-saved-basis-label` in `templates/index.html` (`data-testid="dollar-saved-panel"`) -- plus, as of exit-friction-realized-savings, the additive `#dollar-saved-realized-headline`/`#dollar-saved-realized-verb`/`#dollar-saved-realized-coverage` sibling elements (STATIC "marks basis" caption in the template, never JS-injected -- RULING A honesty requirement) -- plus, as of `DE-AUDIT-BL3-001` (BL-3), the additive net-of-friction siblings `#dollar-saved-net-of-friction-headline`/`-verb` and `#dollar-saved-realized-net-of-friction-headline`/`-verb`, gated on the SAME empty-state conditions as their gross counterparts, plus a STATIC "gross of trading costs" caveat qualifying the gross headline(s) (never JS-injected). Does not clobber `#guard-alpha-headline` (windowed % guard alpha from `/api/strip/<window>`). See `docs/generated/static_index_js.md`.

#### `GET /api/exit-turnover` -- `exit_turnover()` (exit-friction-realized-savings, `DE-EXIT-FRICTION-REALIZED-001`, AC-8, 2026-07-24)

Per-symphony exit-trigger turnover stats (30/90/365-day exit counts, each with its actual `coverage_days`) plus an estimated annual friction drag. Read-only sibling to `guard_alpha_summary()` above.

**Query parameter:** `symphony_id` (required). Missing or empty (after `.strip()`) -> `400` with `{"error": "symphony_id is required"}` -- never a silent empty `200`.

Delegates to `database.get_exit_turnover_stats(symphony_id)` (30/90/365-day `exit_count` + `coverage_days` per window -- see `docs/generated/database.md` for the retention-honesty contract, RULING C) and `database.compute_est_annual_friction_drag_pct(stats, autotuner.SIM_EXIT_FRICTION_PCT)` (pure arithmetic). `autotuner` is lazy-imported (`# noqa: PLC0415`, CC-2 precedent -- keeps Optuna deps off module load, matching the existing lazy-import pattern at `app.py:3119`).

**Response shape:**
| Field | Type | Description |
|-------|------|-------------|
| `symphony_id` | str | Echoes the query parameter. |
| `windows` | dict | `{"30": {...}, "90": {...}, "365": {...}}` -- each window `{"exit_count": int, "coverage_days": int}`. String keys (JSON object keys are always strings; the underlying `database` dict uses int keys). |
| `est_annual_friction_drag_pct` | float | `exits_per_year * SIM_EXIT_FRICTION_PCT`, scaled from the 365-day window's `coverage_days` -- see `docs/generated/database.md`'s `compute_est_annual_friction_drag_pct`. |

**Key properties:**
- **Read-only.** Not in `_SETTINGS_WRITE_ALLOWLIST`, no `LIVE_EXECUTION` interaction.
- **Never a 500.** An unexpected lookup failure inside the try/except degrades to the same zeroed-window shape (`{"exit_count": 0, "coverage_days": 0}` per window, `est_annual_friction_drag_pct: 0.0`) rather than raising -- logged at `debug` level via `_daemon_log`. A symphony with genuinely zero `exit_triggers` rows is a valid, honest `200` empty-state, not a `404`.
- **Auth-gated.** Covered by the global `_auth_before_request` hook, same as `guard_alpha_summary()` -- no additional decorator.

See `DE-EXIT-FRICTION-REALIZED-001` in `DECISIONS.md`.

**Consumed by:** `fetchExitTurnover()` in `static/performance.js` -- fetches the symphony list from `/api/performance/symphonies` first, then fans out one `/api/exit-turnover` call per symphony. Renders into `templates/performance.html`'s `data-testid="exit-turnover-panel"` -- see `docs/generated/static_performance_js.md`.

#### `GET /api/candidate-alert` -- `candidate_alert()`

Returns the header candidate-alert badge count and the latest weekly-suggestion run status (`feature-plans/candidate-alert.md`).

**Response shape:**
| Field | Type | Description |
|-------|------|-------------|
| `new_valid_count` | int | Count of NEW, UNVIEWED survivor candidates (`verdict == "ADOPT_CANDIDATE"`) newer than the viewed-marker — see `database.get_candidate_alert_new_valid_count`. `0` when there are none, or on a DB accessor failure. |
| `last_run` | dict \| null | `{ran_at, evaluated, survivors}` for the latest weekly-suggestion batch (calendar-date grouped — see `database.get_candidate_alert_last_run`), or `null` when no weekly-suggestion row has ever been written. `survivors: 0` is a valid, honest result: it tells the operator the job ran and rejected everything (AC-3), not that the endpoint is broken. |

**Key properties:**
- **Read-only.** Both underlying accessors (`database.get_candidate_alert_new_valid_count`, `get_candidate_alert_last_run`) use `get_ro_connection()` (architecture constraint 5).
- **Never raises (AC-6).** Each accessor call is independently wrapped in `try/except Exception` at the route level; a DB failure degrades only that field (`new_valid_count=0` or `last_run=None`) without affecting the other. Always returns 200.
- **Auth-gated.** Covered by the global `_auth_before_request` hook, same as `guard_alpha_summary()` — no additional decorator.
- **"Valid" is stricter than the feature plan's original wording.** AC-2 defined valid as `verdict != "REJECT_VETO_FAILED"`; the shipped accessor requires `verdict == "ADOPT_CANDIDATE"`, additionally excluding `KEEP_INCUMBENT` (`acceptance_gate.py`'s third decision string — the common "no benefit, nothing changed" outcome). See `DE-CANDIDATE-ALERT-001` in `DECISIONS.md`.
- **`new_valid_count` reachability fix (advisor-remediation-r1 AC-17, `3fa2e7f8`, 2026-07-13):** before this cycle, `verdict == "ADOPT_CANDIDATE"` was mathematically UNREACHABLE on every one of the three weekly-suggestion engines' (Strategy Builder / Asset Swaps / Logic Changes) production call paths — `BacktestCandidate` construction left `candidate_params`/`incumbent_params` structurally empty, forcing a constant 0.5-vs-0.75 panel-comparison clause in `acceptance_gate.py` that failed unconditionally regardless of actual candidate performance. `new_valid_count` could therefore never be non-zero, independent of any candidate's real OOS quality. `advisors/backtest_gate_engine.py`'s panel-tie neutralization (see [advisors/backtest_gate_engine](advisors_backtest_gate_engine.md)'s "Panel-Tie Neutralization" section) fixed the underlying reachability; this route's own query and field semantics are unchanged. **Scope note (verified by this doc-writer via direct call-path read, 2026-07-13):** this fix does NOT extend to `ai_advisor.build_assessment_from_context`'s separate `oos_alpha=None` config-suggestion framing (`.claude/CLAUDE.md`'s "AI Advisor empty suggestions" gotcha, [ai_advisor.md](ai_advisor.md)) — that path is driven by `autotuner.py`'s own walk-forward BHY/Yekutieli haircut-select, which calls `acceptance_gate.evaluate_acceptance_gate` with hardcoded `candidate_stability_score=1.0, incumbent_stability_score=1.0` (an unconditional tie, unaffected by AC-17 either way) and, independently, short-circuits to `REJECT_VETO_FAILED` before the panel-comparison clause is ever reached when `winner_trial_is_none=True` (i.e. exactly the `oos_alpha=None` case). Different subsystem, never had the bug — that gotcha and doc entry remain accurate as written. See `DE-ADVISOR-R1-001` §AC-17 in `DECISIONS.md`.

**Consumed by:** `fetchCandidateAlert()` in `static/chrome.js` — polled every 30 s and once on `DOMContentLoaded`. Populates `#candidate-alert-badge` (hidden at count 0) and the `title` attribute of `#candidate-alert-indicator`, both in `templates/_chrome.html` (shared by all 4 screens — see AC-1).

#### `POST /api/candidate-alert/mark-viewed` -- `candidate_alert_mark_viewed()`

Advances the candidate-alert viewed-marker (AC-5) so currently-visible survivors stop badging.

**Request:** no body required. `database.mark_candidate_alert_viewed()` takes zero arguments and computes the new marker value itself (`MAX(id)` over the three weekly-suggestion roles) — any caller-supplied observation id in the request body is ignored by design, so a malicious or buggy client cannot set the marker to a value the operator hasn't actually seen.

**Response:** `{"status": "ok", "last_viewed_observation_id": <int>}`.

**Key properties:**
- **CSRF-protected** via the global `_csrf_before_request` before-request hook — no explicit in-route `_validate_csrf()` call, the same convention as the Strategy Builder `/run` route; `save_symphony_settings`'s explicit call is redundant/historical, not the pattern to follow for new routes.
- **Advisory-only write.** NOT in `_SETTINGS_WRITE_ALLOWLIST` (that allowlist exists exclusively for the separate `/api/settings` env-key write path) and never touches `LIVE_EXECUTION`.
- **Idempotent.** `database.set_candidate_alert_viewed_marker` stores `MAX(existing, new)` — a repeat or out-of-order call can never regress the marker.

**Consumed by:** `markCandidateAlertViewed()` in `static/chrome.js`, fired on click of `#candidate-alert-indicator` alongside its native `<a href>` navigation to `/ai-advisor` (`keepalive: true` so the POST completes even as the browser navigates away; a failure here never blocks navigation — AC-4 works without JS at all).

See `DE-CANDIDATE-ALERT-001` in `DECISIONS.md`.

#### `GET /api/incubation` -- `api_incubation()` (strategy-incubation-gate, `DE-INCUBATION-GATE-001`, 2026-07-25)

Returns the forward-incubation ledger for the Strategy Builder tab -- read-only, computed fresh on every request.

**No caching anywhere in this path (hard requirement, promoted by review):** calls `database.get_incubation_overview()` fresh on every call, no `functools.lru_cache`, no module-level memoized dict, no Flask response-caching decorator. Two requests straddling a real `set_incubation_status` transition must return different values -- pinned by `tests/app/test_incubation_route.py::TestIncubationRouteNoStaleCaching`. This reinforces the AC-5 live-join design (see `docs/generated/advisors_incubation.md`): there is no frozen status field to cache in the first place, since status is computed live from the ledger every call.

**Response shape:** `{"incubating": [{candidate_hash, status, status_reason, days_observed, admitted_at, promoted_at, objective, provenance, badge_label, badge_modifier}, ...]}`.

**`tree_json` is never in the projection.** The route builds an explicit allowlisted field set per row -- `tree_json` (the compiled Composer tree, potentially large) is excluded by construction, not by a redaction step; no tree exfil via this API (Strategy Builder already has its own tree display path with its own controls).

**`_incubation_badge(row: dict) -> {"label": str, "modifier": str}`** -- a pure, no-I/O helper shared by this route AND `ai_advisor_tab()`'s badge stamping below, so wording never drifts between the two render sites. Maps `status` to one of four labels: `"Incubating — day N of 63"` (INCUBATION_WINDOW_TRADING_DAYS lazy-imported from `advisors.incubation` inside a `try/except` -- an honest degrade to `"Incubating — day N"` if that module isn't present yet, never a duplicated magic number or a raise), `"Promoted — recommended"`, `"Failed incubation (reason)"`, `"Expired (reason)"`. `days_observed` (a SQL `COUNT(*)`, always a real int in production) degrades to `0` for display if it is ever `None` or NaN, rather than leaking a non-numeric token into the label text.

**Strict-JSON safe:** `days_observed` is sanitized through a local `_json_safe_float()` NaN/Infinity guard before `jsonify()` -- mirrors the shipped `guard_alpha_preconditions` pattern (this file's other `_json_safe_float`, `app.py:3128`). A real browser's `response.json()` rejects the whole response on a bare `NaN`/`Infinity` token; RFC 8259 has no such tokens.

**Key properties:**
- **Read-only.** Not in `_SETTINGS_WRITE_ALLOWLIST`, no `LIVE_EXECUTION` interaction.
- **Never a 500.** A ledger read failure degrades to `{"incubating": []}`. A per-row degrade (a malformed row, a badge-computation exception) skips just that row via a per-row `try/except` -- one bad row never breaks the whole response (AC-6).
- **Auth-gated.** Covered by the global `_auth_before_request` hook, same as `guard_alpha_summary()` -- no additional decorator.
- **Empty ledger -> `{"incubating": []}`**, never a 500 and never an informative-empty-state wrapper object -- the empty list IS the honest empty state.

See `DE-INCUBATION-GATE-001` in `DECISIONS.md` and `docs/generated/advisors_incubation.md`.

**Consumed by:** `refreshIncubationChips()` in `static/ai_advisor.js` -- see `docs/generated/static_ai_advisor_js.md`.

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

The AI Advisor SPA was extended from 5 to **6 in-place tabs** in the spa-port cycle (2026-06-13). The Strategy Builder was formerly a separate page (`GET /ai-advisor/strategy-builder` → `render_template("ai_advisor_strategy_builder.html")`); it is now the 6th tab panel in the unified `templates/ai_advisor.html` SPA. Its GET route now 302-redirects to `/ai-advisor` like all the other former sub-pages. The POST action route (`POST /ai-advisor/strategy-builder/run`) is unchanged in path/shape (see its response-JSON extensions below).

All 6 panels (Overview, Correlations, Asset Swaps, Logic Changes, Chat, Strategy Builder) are rendered in one server-side template at `GET /ai-advisor`. Tab switching is in-place via JS (`initTabSwitcher` in `static/ai_advisor.js`). All 5 old GET sub-routes 302-redirect to `/ai-advisor`; the POST action routes keep their paths (see the advisor-remediation-r1 response-JSON extensions on the three Evaluate routes below).

**Extended to 7 tabs in the frontrunner-builder wave-2 cycle (2026-07-11):** the Frontrunner Builder tab was added following the same pattern as Strategy Builder -- a new `GET /ai-advisor/frontrunner-builder` route 302-redirects to `/ai-advisor` (no standalone page ever existed for it), and the tab content is the 7th panel in the unified template. Three new POST action routes support it: `POST /ai-advisor/frontrunner-builder/run`, `POST /ai-advisor/proposal/approve`, `POST /ai-advisor/proposal/reject` -- see "Frontrunner Builder Routes" below.

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
| `market_prism_summary` | `database.get_latest_market_prism_summary()`; `dict` or `None`; wrapped in `try/except` — `None` on failure renders an informative empty state; **RF-1:** `per_lens_digest` summaries are pre-humanized in-place by `advisors.prism_render.humanize_lens_summary` before template render (no new context key — template's existing `_lens.get('summary')` reads humanized prose); **DE-PRISM-SOURCES-001:** if non-None, a `copy.deepcopy` is taken, then `run_id` from `raw_response` is used to fetch the matching MARKET_PRISM_SOURCES row via `database.get_latest_market_prism_sources_for_run(run_id)`; matching `article_corpus` lists are merged into `per_lens_digest` entries **before RF-1 humanization** — honest empty-state (no article links) when SOURCES row is absent, run_id mismatches, or merge raises | Overview (Market Prism block) |
| `market_prism_verification` | **New (DE-PRISM-NUMERIC-VERIFY-001, AC-10).** `dict` (`{"checks": [...], "summary": {...}, "verdict": ...}`) or `None`. Fetched via `database.get_latest_market_prism_verification_for_run(run_id)` — the same `run_id` extracted for the SOURCES merge — after a fresh `copy.deepcopy` of `market_prism_summary`; each `overridden` check gains a rendered `annotation` string (`"council cited {cited}; source says {truth}"`). `None` when `market_prism_summary` is `None`, `run_id` is absent, no VERIFICATION row exists yet, or the merge raises (wrapped `try/except`, logs `type(exc).__name__` only) | Overview (Market Prism block, numeric verification overlay) |

The Correlations, API-key, Symphonies, Strategy Builder, and Market Prism data assembly sections are wrapped in `try/except` — if those panels' data fails, the others still render. The Overview observations loop is not wrapped.

**RF-1 (prose render guard — two fixes):**

- **R1 (per-lens digest):** If `market_prism_summary` is present, `ai_advisor_tab()` iterates `per_lens_digest` and mutates each `_le["summary"]` in-place via `advisors.prism_render.humanize_lens_summary(_ln, _le)` (app.py:2966–2984). The template's existing `{{ _lens.get('summary') | e }}` renders humanized prose; no new context key is added. Council prose passes through unchanged; `lens_pipeline` JSON is humanized per lens type.

- **R2 (obs-raw-preview):** For each non-MARKET_PRISM observation in `observations`, `ai_advisor_tab()` stamps `obs["_preview_text"] = humanize_obs_preview(obs["raw_response"])` (app.py:2892–2902). The template renders `obs.get('_preview_text', '') | e` for non-MARKET_PRISM rows and `obs.verdict | e` for MARKET_PRISM rows.

See `DE-RF1-PROSE-RENDER` in `DECISIONS.md` and [advisors/prism_render](advisors_prism_render.md).

**DE-PRISM-SOURCES-001 (Overview citation overlay):**

If `market_prism_summary` is non-None, `ai_advisor_tab()` runs the SOURCES merge block **before** RF-1 humanization:

1. `copy.deepcopy(market_prism_summary)` — deep-copies the summary so the original DB row object is never mutated in-place (safe for shared references in tests/caches).
2. Decodes `raw_response` from JSON string if needed (guards against the row arriving as a string blob).
3. Extracts `run_id` from `raw_response.get("run_id")`. If absent or falsy, the merge is skipped.
4. Calls `database.get_latest_market_prism_sources_for_run(run_id)`. If the SOURCES row is found, iterates its `per_lens_digest` entries and copies each non-empty `article_corpus` list into the corresponding entry of `_mp_raw["per_lens_digest"]` — only if both the source and destination lens entries are dicts.
5. Entire block is wrapped in `try/except Exception: pass` — merge failure never crashes the route; honest empty-state (no article links) on any error, run_id mismatch, or absent SOURCES row.

Template updated first in **DE-SOURCES-CAROUSEL-001** (2026-06-29, superseded) and then in **DE-PRISM-SOURCES-PER-LENS-001** (2026-06-30): the Sources section now renders **one carousel per non-empty prism lens** in canonical order (technicals, sentiment, derivatives, macro, fundamentals), each prefixed with a `.prism-lens-carousel-label` and wrapped in `data-testid="prism-sources-lens-{lens}"`. A source attributed to multiple lenses appears in each matching carousel. Empty lenses are suppressed. The per-card `.prism-source-lens-tag` was removed (redundant inside a lens-labeled strip). The data path (article_corpus merge into per_lens_digest) is unchanged; only the Jinja render block and its CSS were updated. See `DECISIONS.md` §DE-PRISM-SOURCES-PER-LENS-001.

**DE-PRISM-NUMERIC-VERIFY-001 (Overview numeric verification overlay):**

Additive to the SOURCES merge above — runs as a second, independent block (also before RF-1 humanization, also on the same `copy.deepcopy`'d `market_prism_summary`):

1. Re-decodes `raw_response` from JSON string if needed and re-extracts `run_id` (the same `run_id` the SOURCES merge used — the MARKET_PRISM row's own identifier, not the verification row's).
2. If `run_id` is present, calls `database.get_latest_market_prism_verification_for_run(run_id)`.
3. If a VERIFICATION row is found, decodes its `raw_response` (string-or-dict, same guard pattern) and builds `market_prism_verification = {"checks": [...], "summary": {...}, "verdict": ...}` from it — the `checks` list is copied (never the original row's list object) and each `overridden` check gains a rendered `"annotation"` string: `f"council cited {cited_value}; source says {ground_truth_value}"`. `pass`/`flagged`/`unverifiable` checks pass through without an annotation.
4. Entire block is wrapped in `try/except Exception` — logs `type(exc).__name__` only (`_daemon_log.warning`, no exception args/message) and leaves `market_prism_verification = None`; honest empty-state, never crashes the route.

**Never mutates the underlying MARKET_PRISM row:** operates on the same `copy.deepcopy` taken for the SOURCES merge — the original DB row object referenced elsewhere (e.g. by a shared cache) is untouched.

**Render (`templates/ai_advisor.html`):** a `data-testid="prism-verification"` block renders only `{% if market_prism_verification and market_prism_verification.get('checks') %}` (honest empty-state — no block at all when no VERIFICATION row exists yet). Each check renders as a `data-testid="prism-verify-check-{indicator}"` badge (`prism-verify-badge--pass|flagged|overridden|unverifiable` CSS modifier keyed off `classification`) plus, for `overridden` checks only, a `data-testid="prism-verify-annotation"` `<p>` with the "council cited X; source says Y" text. **XSS-safe:** every interpolated value (`indicator`, `classification`, `annotation`) is escaped with `| e`; the template block uses no `| safe` filter anywhere (asserted by a dedicated test, `test_verification_overlay_template_block_never_uses_safe_filter`).

`"MARKET_PRISM_VERIFICATION"` is not added to `_ADVISOR_ROLES` — it never appears in the Overview `observations` loop or receives an R2 `_preview_text` stamp (asserted by `test_market_prism_verification_not_in_advisor_roles`).

See `DECISIONS.md` §`DE-PRISM-NUMERIC-VERIFY-001` and [advisors/prism_numeric_verifier](advisors_prism_numeric_verifier.md).

#### `GET /ai-advisor/correlations` → 302 redirect to `/ai-advisor`
#### `GET /ai-advisor/asset-swaps` → 302 redirect to `/ai-advisor`
#### `GET /ai-advisor/logic-changes` → 302 redirect to `/ai-advisor`
#### `GET /ai-advisor/chat` → 302 redirect to `/ai-advisor`
#### `GET /ai-advisor/strategy-builder` → 302 redirect to `/ai-advisor` — `ai_advisor_strategy_builder()`

Old bookmarks and links redirect cleanly rather than 404ing. The Strategy Builder content is now rendered as the 6th tab panel in the unified SPA. The POST action route is unaffected.

#### `POST /ai-advisor/asset-swaps/evaluate` — `ai_advisor_asset_swaps_evaluate()`

**R2-3 (`DE-ADVISOR-R2-3-001`, 2026-07-14) rewired this route to the real LLM-reasoned engine; the section below describes the SHIPPED behavior, not the pre-R2-3 deterministic one.**

Accepts JSON: `{ symphony_id, from_ticker?, to_ticker?, objective_type? }`. **R2-3: `from_ticker`/`to_ticker` are now OPTIONAL.** Three outcomes:
- Both tickers supplied → EXPLICIT-PAIR mode: that exact pair is evaluated — byte-preserves the pre-R2-3 flat response shape, additively gaining `provenance`/`survivors_detail`/`rejected_detail` (AC-12).
- Neither ticker supplied → objective-only REASONED mode: the LLM-reasoned generator proposes objective-directed swap pairs over the operator's real holdings + a validated tradeable universe; response is array-shaped (`survivors_detail`/`rejected_detail`), mirroring the logic-changes route's shape.
- Exactly ONE ticker supplied → an honest 200 error (`"supply both tickers for an explicit pair, or neither to let the advisor propose"`) — never silently reinterpreted as either mode (team-lead's R2-3 contract ruling), checked BEFORE any composer_hash/DB lookup so it can't fall through to a hash-resolution error instead.

Constructs a typed `SwapObjective`, injects the operator's real tree + live stats + 5 market-lens blocks via `ai_advisor.build_reasoning_context` (R2-3, mirrors R2-2's AC-1) for BOTH modes (EXPLICIT-PAIR also receives `reasoning_context` as an optional steering hint alongside the real pair, mirroring R2-2 retaining `change_description` as a hint), fetches the baseline tree via `symphony_logic`, calls `propose_operator_swap` from `advisors.asset_swap_engine` with `**_pair_kwargs` (empty dict for REASONED mode, `{incumbent_asset, candidate_asset}` for EXPLICIT-PAIR — AC-12: the two modes are genuinely disjoint at the call site), and returns the `SwapRunResult` fields as JSON.

Never runs a live trade; never calls Composer write endpoints (AC-X1). Persistence (`advisor_observation`) is handled inside `propose_operator_swap` (AC-X3).

**Key fix (2026-06-10, unaffected by R2-3):** the route resolves the display name → Composer hash before calling the engine (AC-8) — passing the display name to the engine causes silent empty results from the Composer backtest API.

**Advisor-remediation-r1 response-JSON extensions (`DE-ADVISOR-R1-001`, 2026-07-13), unaffected by R2-3:**
- **AC-6 (N=1 honesty):** on the EXPLICIT-PAIR shape, the top-level `caveats` field is built via `_n1_honest_caveats(proposal.caveats)` — strips any FDR/Yekutieli-branded text and appends the honest N=1 disclosure. This route's EXPLICIT-PAIR mode always evaluates exactly one operator-named candidate.
- **AC-7:** `gate_result`/each per-candidate dict carries `rejection_reason` (`pbo_veto` / `below_spy_alpha` / `fdr_not_winner` / `oos_inferior_to_incumbent` / `None`).
- **AC-9 (low-power caveat):** `gate_result.low_power` / each survivor's `low_power` boolean via `_low_power(validation_days)`; `_LOW_POWER_CAVEAT` appended to `caveats` when `True` on a genuine `ADOPT_CANDIDATE` survivor.
- **AC-13:** `_evaluate_single_variant` (shared with Logic Changes, R2-3-signature-changed — see [advisors/asset_swap_engine](advisors_asset_swap_engine.md)) returns a 4th tuple element, `baseline_returns_pct`, reused instead of a second duplicate baseline `run_backtest` call.

**R2-3 — LLM-reasoned generation + provenance (`DE-ADVISOR-R2-3-001`, engine commit `248469a5`, route/JS commit `5afb41bd`, 2026-07-14):**

- **Objective-only REASONED mode (AC-2/AC-12).** `_pair_kwargs = {"incumbent_asset": from_ticker, "candidate_asset": to_ticker} if explicit_pair else {}` — omitting both lets `propose_operator_swap`'s engine-internal branch fire the reasoned generator. The exactly-one-ticker honest-error check runs BEFORE hash resolution (see above).
- **Context injection (AC-1), called unconditionally for BOTH modes:** `reasoning_context, reasoning_manifest = ai_advisor.build_reasoning_context(symphony_id, objective, composer_symphony_id=composer_hash)` — same call shape as the SB/Logic-Changes routes. EXPLICIT-PAIR mode also threads `reasoning_context` through as an optional steering hint even though the pair is fixed — this is additive context only, it cannot change which pair gets evaluated.
- **Route-minted default provenance on EVERY return path (AC-8) — the SAME stricter shape R2-2 established, not R2-1's success-only shape.** Immediately after the docstring, the route builds `_default_provenance = {generation_model: model_config.get_advisor_suggestion_model(), mode: "asset-swap", evidence_injected: dict(ai_advisor._EMPTY_MANIFEST), run_id: str(uuid.uuid4())}` and returns it on every early-exit branch — no Composer key, exactly-one-ticker, missing `symphony_id`, hash-resolution failure, tree-fetch failure, and the engine-call exception handler — none of which carried a `provenance` key at all before this cycle.
- **Success-path provenance reads the ENGINE's own value, not the route default.** `provenance = getattr(run_result, "provenance", None)`, guarded by the same `isinstance(provenance, dict)` MagicMock-safety idiom R2-1/R2-2 established (`getattr(...,default)` alone does not fire against a `MagicMock`'s auto-vivified attributes) — falls back to `_default_provenance` rather than `None`, consistent with this route's never-absent contract.
- **Two response shapes, one unified renderer.** `explicit_pair == False` → array-shaped JSON (`{message, survivors, no_api_key, survivors_detail, rejected_detail, provenance}`), no single top-level `candidate_id`/`from_ticker`/`to_ticker` — mirrors the logic-changes route. `explicit_pair == True` → the pre-R2-3 flat single-candidate shape, byte-preserved, additively gaining `provenance`/`survivors_detail`/`rejected_detail` (AC-12) alongside the existing top-level fields.
- **`_swap_proposal_to_dict`** — the swap-flavored per-candidate serializer mirroring the logic-changes route's `_proposal_to_dict`; carries `incumbent_asset`/`candidate_asset` (not `tweak_*`, since `SwapProposalResult` already exposes them as top-level attributes) plus `objective_type`, `objective_rationale`, gate metrics, `low_power`, `rejection_reason`, `caveats` (N=1-honest), `apply_guidance`, `backtest_error`, `data_warnings`.
- **AC-X4 ordering, correct from first GREEN this cycle (no re-gate fix needed, unlike R2-2's `propose_operator_logic_change`):** the engine's REASONED branch checks the Composer API key BEFORE `generate_reasoned_swap_candidates` is ever called — see [advisors/asset_swap_engine](advisors_asset_swap_engine.md).

**Tests:** `tests/ui/test_asset_swap_route_reasoning_provenance.py` (R2-3, route-level provenance + two-mode contract, 505 new lines), `tests/ai_advisor/test_as_live_generation_provenance_render.py` (R2-3, JS render), plus the R1-era route-JSON extension tests unaffected by this cycle.

#### `POST /ai-advisor/logic-changes/evaluate` — `ai_advisor_logic_changes_evaluate()`

Accepts JSON: `{ symphony_id, objective_type?, change_description }`. Same name→hash resolution as asset-swaps. Calls `propose_operator_logic_change` with the Composer hash. Returns `LogicChangeRunResult` fields as JSON including FDR metadata (n_candidates, fdr_q, fdr_adjusted_threshold via Yekutieli c(n) harmonic-sum).

**Key fix (2026-06-10):** `gate_reason` is derived as `gr.verdict.decision.replace("_", " ").title()` when `vetoes_passed` is True, else `"veto failed"`. The previous code attempted `gr.verdict.reason` which does not exist on `AcceptanceVerdict`.

**Advisor-remediation-r1 response-JSON extensions (`DE-ADVISOR-R1-001`, 2026-07-13):** the same AC-6/AC-7/AC-13 additions as Asset Swaps above, applied to this route's run-level `gate_result` dict AND to `_proposal_to_dict`'s per-candidate dict — the latter is the shape `survivors_detail`/`rejected_detail` actually render, so both are covered, not just the run-level shortcut. **AC-9 (`a5eaa3b0`, closes the same r1-review Checkpoint-3 finding as Asset Swaps):** `gr` gains a `low_power` boolean via `getattr(gr, "validation_days", None)` (defensive — `gr` is a bare `SimpleNamespace` in two pre-existing tests, not always a real `CandidateGateResult`); a post-processing loop over `survivors_detail` then appends `_LOW_POWER_CAVEAT` to any survivor whose `low_power` is `True`, mirroring the Strategy Builder route's identical pattern.

**R2-2 — LLM-reasoned generation + provenance (`DE-ADVISOR-R2-2-001`, engine commit `d1d480dd`, route/JS commit `13f9863d`, 2026-07-14):**

- **`change_description` is no longer deterministically parsed.** The route passes `change_description=` straight through to `propose_operator_logic_change`, which now routes it through the LLM-reasoned `generate_reasoned_logic_candidates` (see [advisors/logic_change_engine](advisors_logic_change_engine.md)) rather than the deleted `_parse_change_description_to_tweak`.
- **Context injection (AC-1):** for a resolved `symphony_id`, the route calls `ai_advisor.build_reasoning_context(symphony_id, objective, composer_symphony_id=composer_hash)` — same call shape as the SB route above — and threads both return values into `propose_operator_logic_change(reasoning_context=, reasoning_manifest=)`. **Known cost, logged as a follow-up, not fixed this cycle:** `build_reasoning_context` re-fetches the tree internally (`symphony_logic.get_condensed_logic` → `fetch_symphony_score`) — a second `/score` read beyond the `raw_value` this route already fetched earlier in the same request for the tweak-apply step.
- **Route-minted default provenance on EVERY return path (AC-5) — deliberately STRICTER than the SB route above.** Immediately after the docstring, the route builds `_default_provenance = {generation_model: model_config.get_advisor_suggestion_model(), mode: "logic-change", evidence_injected: dict(ai_advisor._EMPTY_MANIFEST), run_id: str(uuid.uuid4())}` and returns it on every early-exit branch — import failure, no Composer key, missing `symphony_id`/`change_description`, hash-resolution failure, tree-fetch failure, and the engine-call exception handler — none of which carried a `provenance` key at all before this cycle. This is a team-lead-ruled divergence from the SB route above, which only carries `provenance` on its success path and OMITS the key entirely on error branches.
- **Success-path provenance reads the ENGINE's own value, not the route default.** `provenance = getattr(run_result, "provenance", None)`, guarded by the same `isinstance(provenance, dict)` MagicMock-safety idiom `DE-ADVISOR-R2-1-001` established for the SB route (`getattr(...).default` alone does not fire against a `MagicMock`'s auto-vivified attributes) — but here the guard falls back to `_default_provenance` rather than `None`, consistent with this route's never-absent contract.
- **Route docstring/comment RESOLVED this cycle (`f8361f46`):** the route's docstring and one inline comment previously read "the route parses [`change_description`] ... via a simple heuristic" and "the engine's own `_parse_change_description_to_tweak` runs internally" — stale, since that parser was deleted (a doc-writer finding flagged mid-cycle). Both are now fixed: the docstring describes `change_description` as an LLM-reasoning steering hint (real-tree resolution + `validate_tree` before backtest, honest degradation on LLM unavailability), and the inline comment names `generate_reasoned_logic_candidates`. No behavior change — docs-only, brings the source comments into agreement with the actual runtime behavior this doc already described.
- **AC-X4 billing-order re-gate fix (`6e1eabcd`, engine-internal — no `app.py` diff):** `advisors.logic_change_engine.propose_operator_logic_change` now checks the Composer API key BEFORE the billed LLM seam, not after — this route's own call sequence (build context, call the engine) is unaffected; see [advisors/logic_change_engine](advisors_logic_change_engine.md) for the fix detail.

**Tests:** `tests/ai_advisor/test_ac7_route_json_rejection_reason.py` (8/8 GREEN), `tests/ai_advisor/test_r1_power_caveat.py` (12/12 GREEN, both routes' caveat-text append), `tests/app/test_logic_change_route_reasoning_provenance.py` (R2-2, route-level provenance contract), `tests/advisors/test_lc_live_generation_provenance_render.py` (R2-2, JS render — see [static/ai_advisor.js](static_ai_advisor_js.md) note below for why this test file's name doesn't match a `docs/generated` page).

#### `POST /ai-advisor/suggest` — `ai_advisor_suggest()`

Resolves the Composer hash → normalized symphony name for DB lookups; pre-fetches the autotune run once via `database.get_latest_autotune_run` and passes it to `ai_advisor.assemble_advisor_context` (which now honors the passed value and skips its own internal fetch — single DB round-trip total). Passes both `symphony_id` (normalized name) and `composer_symphony_id` (original hash) to `ai_advisor.assemble_advisor_context`. Returns `{"suggestions": [...], "assessment": {...}}` — the `assessment` key carries `build_assessment_from_context` output so the UI can explain the empty-suggestions state per symphony.

D-1 security contract: fully honored on this route and all advisor routes (asset-swaps/evaluate, logic-changes/evaluate, and the ImportError handler) — on exception, returns `{"error": type(exc).__name__}` only, never `str(exc)`.

**suggest-hash fix (BACKLOG item, found during the F-023 doc-audit, shipped `DE-OPS-CLUSTER-001`, 2026-07-21).** `composer_symphony_id` previously passed the client's raw, unresolved `symphony_id` straight through — correct when the caller sent a Composer hash, but silently wrong when the caller sent a display NAME: `assemble_advisor_context`'s Composer `/score` call requires a hash, so a name-valued `composer_symphony_id` 400s that call and silently empties the condensed-logic context (D-1, never crashes, just degrades). Fix: a new `resolved_hash` variable tracks the HASH side of the SAME existing `_bot_state.items()` match loop that already resolves `resolved_id` (the name side) — no new loop, no new Composer/DB call. `composer_symphony_id=resolved_hash` now always carries the matching hash regardless of whether the caller supplied a hash or a name; an unresolvable id falls back to passing the raw value through unchanged (today's degrade path, preserved). See `feature-plans/BACKLOG.md` (entry marked shipped) and `DECISIONS.md`'s `DE-OPS-CLUSTER-001`.

#### `POST /ai-advisor/accept` — `ai_advisor_accept()`

Applies one suggestion through C2 safety gates: (1) allowlist, (2) risk-direction log, (3) OOS revalidation via `ai_advisor.revalidate_suggestion_oos`, (4) locked-var guard. On all-pass, writes new param value via `database.save_symphony_strategy` and persists the operator decision to `llm_suggestions` via `database.record_llm_suggestion`.

#### `POST /ai-advisor/reject` — `ai_advisor_reject()`

Records operator rejection to `llm_suggestions` audit trail. No config write, no `save_symphony_strategy` call.

#### `POST /ai-advisor/chat/send` — `ai_advisor_chat_send()`

Rate-limited (per-IP via `_CHAT_RATE_LIMITER`) explain-only chat endpoint. Accepts `{ artifact_type, artifact_id, artifact, history, message }`. Delegates to `advisors.advisor_chat.explain_artifact`. Hard constraints: no write path, no trade directives, no new unvalidated recommendations. Returns `{reply: str}` on success, `{error: str}` on failure; never returns 500 or HTML.

#### `POST /ai-advisor/strategy-builder/run` — `ai_advisor_strategy_builder_run()`

CSRF-protected. Accepts JSON: `{ objective, universe, symphony_id? }`. Lazy-imports `propose_strategies` from `advisors.strategy_builder_engine` and `load_atlas_candidates` from `advisors.build_plan_generator` (CC-2: both kept off the live 1-minute execution path).

*Line-number citations below (`app.py:38xx`) were accurate at R1's time of writing; several routes have been added to `app.py` since (e.g. candidate-alert, 2026-07-12), so the file has grown and these are now offset -- the route decorator currently lives at `app.py:4739`. Flagged, not corrected line-by-line in this pass (out of this cycle's scope); a dedicated citation sweep is a separate, tracked gap.*

**Request body:**

| Field | Type | Description |
|-------|------|-------------|
| `objective` | `str` | One of `diversify` / `cut_drawdown` / `lift_risk_adjusted` / `volatility_mitigation`. Unknown values default to `diversify`. |
| `universe` | `list[str]` or comma-string | Optional ticker override; `[]` (default) triggers C1 self-sourcing from `universe_provider.get_tradeable_set()`. |
| `symphony_id` | `str` | Optional. Keys persisted observations to this Composer symphony ID. Defaults to `""`. |

**Pipeline (C5 rewire, commit 1d5dd48):**

1. Parse `objective` → `Objective` enum (default `diversify` on unknown values, `app.py:3800`).
2. Call `build_plan_generator.load_atlas_candidates(objective)` — objective-matched Atlas community injection (AC-12/AC-13, `app.py:3807`). D-1 (never raises); bill-protected (`force_refresh=False` inside). Atlas failure → `community_candidates=[]`, template-only run proceeds.
3. **R2-1 (symphony-scoped runs only):** when `symphony_id` is truthy, resolve the Composer hash the same way the asset-swap route does (NAME→hash lookup over `bot_state`), then call `ai_advisor.build_reasoning_context(symphony_id, objective, composer_symphony_id=<resolved hash>)` — see the dedicated R2-1 subsection below.
4. Call `propose_strategies(objective, universe, screen_config=ScreenConfig(), live_returns=[], symphony_id=..., community_candidates=..., reasoning_context=..., reasoning_manifest=...)` (`app.py:3813`). Built-new (accessor-driven generation model — currently Fable via `model_config.get_advisor_suggestion_model()`, AC-16 — C1→C2→C3) AND atlas-suggested candidates flow into ONE FDR batch (AC-21).

**F-030 invocation attribution (`DE-OPS-CLUSTER-001`, 2026-07-21):** this call additionally passes `invocation_source="http-route:/ai-advisor/strategy-builder/run"` — see `docs/generated/advisors_strategy_builder_engine.md`'s F-030 section for the full contract (the register finding this closes: 3 production `STRATEGY_BUILDER` advisory-DB rows had no reconstructable origin because they were written via a direct off-HTTP engine call).
5. Serialize survivors and rejected candidates from `run.gated_batch` + `run.screened_survivors` (`app.py:3852–3879`); each entry carries `template_id` (provenance: `"built-new"` or `"atlas-suggested"`), gate metrics, and candidate params.

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

`template_id` carries provenance end-to-end: `"built-new"` for accessor-driven-model-generated candidates (currently Fable, `ADVISOR_SUGGESTION_MODEL` env var, AC-16 attribution coherence — no model name hardcoded in copy), `"atlas-suggested"` for objective-matched community candidates (AC-13).

**Advisor-remediation-r1 response-JSON extensions (`DE-ADVISOR-R1-001`, 2026-07-13):** survivors additionally carry `low_power` (AC-9) and rejected candidates carry `rejection_reason` (AC-7, four values: `pbo_veto` / `below_spy_alpha` / `fdr_not_winner` / `oos_inferior_to_incumbent`); the run-level payload gains `built_new_count`/`atlas_count` provenance rollup and a `mode_notice` degraded-run indicator (AC-11), plus `screens_skipped`/`screens_skipped_reason` (AC-12, set when `live_returns=[]` causes the drawdown/Pearson screens to be skipped rather than silently applied against nothing). **RESOLVED (r1-review Checkpoint-3, commits `fa691f6a` + `f6688ed4`):** `static/ai_advisor.js`'s `sbRunAnalysis()` — the LIVE-RUN handler wired to this route's "Run analysis" button — now consumes all of these fields (`built_new_count`/`atlas_count`/`mode_notice`/`screens_skipped`/`error_category`/`low_power`/`rejection_reason`); this was a genuine gap between the route-JSON tests and the render path when this section was first written, closed the same cycle. See [static/ai_advisor.js](static_ai_advisor_js.md).

**Advisor-outage-degrade AC-4/AC-5 (`DE-SB-DEGRADE-001`, commit `14adb451`, 2026-07-13):** the run-level payload additionally carries `backtest_unavailable` (`bool`), `backtest_unavailable_count` (`int`), and `backtest_unavailable_notice` (`str | None`) — an honest signal when one or more candidates were compiled but NOT tradeability-checked because Composer's `/backtest` endpoint was unreachable (an infra/transport outage — see `plan_tree_compiler`'s infra-vs-400 classifier — NOT a genuine gate rejection). Read directly off `ProposalRun.backtest_unavailable`/`.backtest_unavailable_count` (same pattern as the existing `run.error`/`run.error_category` reads above) rather than recomputed at the route layer — `run.candidates`/the serialized `survivors`/`rejected` lists exclude exactly this population (Step 2's own per-candidate backtest call hits the same outage and strips the candidate via `backtest_error` before this route ever sees it), so a route-side recount would silently read `0` in the outage case this cycle exists to catch. `backtest_unavailable_notice` is server-authored prose (`"{count} candidate(s) could not be tradeability-checked — Composer backtest unavailable"`), `None` when `backtest_unavailable` is `False`. All three fields absent/false/None on both error branches (`run.error`, outer exception) — never fabricated. See [static/ai_advisor.js](static_ai_advisor_js.md) for the render side.

**R2-1 — reasoning-context injection + provenance (`DE-ADVISOR-R2-1-001`, engine commit `fdc6a0aa`, route/JS commit `4063ec33`, 2026-07-13):**

- **Symphony-scoped context injection (AC-1/AC-2/AC-8):** when the request carries a truthy `symphony_id`, the route resolves the Composer hash by scanning `database.load_state()` for a `bot_state` entry whose `database.normalize_name(name)` matches `database.normalize_name(symphony_id)` — the SAME NAME→hash lookup pattern the asset-swap route already uses (`app.py:4373-4387`) — then calls `ai_advisor.build_reasoning_context(symphony_id, objective, composer_symphony_id=<resolved hash>)` and threads both return values into `propose_strategies(reasoning_context=, reasoning_manifest=)`. From-scratch runs (no `symphony_id`) never call `build_reasoning_context` at all — zero extra I/O, and `propose_strategies`/`_generate_candidate_trees`/`generate_build_plans`/`_build_generation_prompt` all receive `reasoning_context=None`, reproducing the exact pre-R2-1 prompt byte-for-byte (AC-8).
- **Response JSON gains a `provenance` object** (success path only): `{generation_model, mode, evidence_injected, run_id}` — read verbatim off `run.provenance` (the engine's exact 4-key contract; see [advisors/strategy_builder_engine](advisors_strategy_builder_engine.md)'s "R2-1" section — no route-side merge or re-derivation). `evidence_injected` is the SAME honest per-source manifest `ai_advisor.build_reasoning_context` returned (`tree`/`stats`: `present`\|`absent`; the 5 lenses: `available`\|`stale`\|`absent`) — never re-summarized, never fabricated.
- **`provenance` is ABSENT (not `null`) on both error branches** (`run.error`, the route's own outer exception) — mirroring the `backtest_unavailable`/`mode_notice` honest-absence convention above. The route never invents a provenance object for a run that never produced candidates. **Contrast with Logic Changes (R2-2, see above):** the Logic Changes route deliberately made the STRICTER choice of a provenance-on-every-path contract; this route's provenance-on-success-only contract was not retrofitted to match — the two routes intentionally carry different absence semantics, team-lead ruling.
- **Route-boundary `isinstance(dict)` serialization guard (a named pattern R2-2 already reused, in a stricter never-`None` form — see the Logic Changes route section above; R2-3 still pending):**
  ```python
  provenance = getattr(run, "provenance", None)
  if not isinstance(provenance, dict):
      provenance = None
  ```
  A plain `getattr(run, "provenance", None)` is NOT sufficient here: several pre-existing test fixtures construct a bare `MagicMock()` as a `ProposalRun` stand-in, and `MagicMock` auto-vivifies ANY attribute access into a new child `Mock` object rather than raising `AttributeError` — so `getattr`'s `default` branch never actually fires against a mock missing `.provenance`, and the resulting non-`None`, non-dict `Mock` blows up `jsonify()` with `TypeError: Object of type Mock is not JSON serializable`. The `isinstance(provenance, dict)` check is the only reliable guard, and it fails CLOSED (`None`) rather than raising or fabricating a dict-shaped value out of a `Mock`. This is the same defensive shape as the pre-existing `backtest_unavailable_count` read one paragraph above (`getattr(run, "backtest_unavailable_count", 0)`), but that field only needs `bool()`/`int()` coercion (safe against a truthy-but-wrong `Mock`) — `provenance` is handed straight to `jsonify()` as a nested object, where a bare `Mock` is fatal, not merely wrong. Regression-pinned by `test_route_survives_bare_mock_run_missing_provenance_attrs` (`tests/app/test_sb_route_reasoning_provenance.py`).
- **Naming collision, deliberately NOT unified:** the pre-existing `data-testid="sb-live-provenance"` render (AC-11/F5, built-new/Atlas COUNT rollup) and the new R2-1 `provenance` object are two independently-named concepts that share the English word "provenance" — per-candidate TEMPLATE origin (`built-new` vs `atlas-suggested`) versus this run's GENERATION-context provenance (model/evidence/run-id). See [static/ai_advisor.js](static_ai_advisor_js.md) for the disambiguated render testids.
- **Cross-cutting contract, not SB-only — R2-2 AND R2-3 confirm it, closing the 3-of-3 R2 program:** `build_reasoning_context` and the 4-key `provenance` shape are the shared enabler `DE-ADVISOR-R2-1-001` established. **R2-2 (Logic Changes, `DE-ADVISOR-R2-2-001`) reused both verbatim** on `POST /ai-advisor/logic-changes/evaluate` — see the route section above — wrapping the same shape in a stricter never-absent contract than this (Strategy Builder) route's. **R2-3 (Asset Swaps, `DE-ADVISOR-R2-3-001`, 2026-07-14) is the third and final reuse point, now shipped** — see the `POST /ai-advisor/asset-swaps/evaluate` route section above; it adopts R2-2's stricter present-on-every-path shape, not this route's success-only shape.

**Error handling (AC-23 boundary):**

- Route outer `except`: returns `{"error": type(exc).__name__}` — never `str(exc)` (`app.py:3826`).
- `run.error` branch: logs the full error server-side; surfaces the static token `"strategy-builder-error"` to the operator (`app.py:3840`) — never echoes `run.error` verbatim (it is set by `propose_strategies` via `str(exc)`, which can carry API keys or internal paths). **AC-11 extension:** the response also carries `error_category` (`getattr(run, "error_category", None)`) alongside the static token — a defensively-read, still-sanitized cause class, never the raw `run.error` string.

Advisory-only: never calls Composer write endpoints, never touches `LIVE_EXECUTION`, not in `_SETTINGS_WRITE_ALLOWLIST`.

---

### Frontrunner Builder Routes

Four routes support the Frontrunner Builder Advisor tab (feature-plans/frontrunner-builder.md AC-8/AC-9-route/AC-10-UX; wave-2, 2026-07-11). See [advisors/frontrunner_builder](advisors_frontrunner_builder.md) for the backend pipeline these routes trigger.

#### `GET /ai-advisor/frontrunner-builder` → 302 redirect to `/ai-advisor` — `ai_advisor_frontrunner_builder()`

Mirrors the existing redirect-stub pattern used by every other Advisor sub-route. No standalone page was ever built for this tab -- it only ever existed as the 7th panel of the unified SPA.

#### `POST /ai-advisor/frontrunner-builder/run` — `ai_advisor_frontrunner_builder_run()`

Operator-initiated on-demand build (AC-1's route, AC-8). Dispatches `advisors.frontrunner_builder.run_frontrunner_build` to a dedicated single-worker `ThreadPoolExecutor` (`_FRONTRUNNER_BUILD_EXECUTOR`, `atexit`-registered) and returns immediately -- **async 202, never a synchronous call**. `run_frontrunner_build` iterates every live symphony (up to `MAX_CASCADES_PER_SYMPHONY_RUN` cascades each) with rate-limited Fable + Composer calls and is genuinely multi-minute; the route must never block a Flask request thread. Results persist straight to `frontrunner_proposals` (SQLite) -- the operator "polls" by reloading `/ai-advisor` to see newly-queued server-rendered proposal cards; there is no synchronous result body and no new JSON polling endpoint.

**Dedicated executor, not shared:** deliberately not `_DISMISS_EXECUTOR` -- sharing a pool with the latency-sensitive dismiss/flush writes would queue those behind a long-running build. Single-worker serializes overlapping run requests rather than hammering Fable/Composer concurrently.

**Request body:** `{ symphony_ids?: [str] }`. Omitted/empty → full live roster (`run_frontrunner_build`'s own default).

**Fail-fast pre-check is ANTHROPIC_API_KEY-only:** returns `200 {"error": "advisor unavailable: ANTHROPIC_API_KEY not configured"}` -- without submitting to the executor -- when `ANTHROPIC_API_KEY` is absent, since the build needs it for Fable candidate generation and a doomed job should never be queued. **Deliberately does NOT pre-check Composer credentials** -- Composer infra is assumed present (the same posture as every other advisor route); a missing/invalid Composer key degrades per-symphony inside `run_frontrunner_build`'s own D-1 never-raises contract (that symphony is skipped and logged, not a route-level crash). `frreview` confirmed this asymmetry is deliberate, not an oversight.

**Log-and-swallow closure (ruling, team-lead, 2026-07-11):** the submitted work wraps `run_frontrunner_build` in `_run_frontrunner_build_background`, a closure that catches any exception and logs it via `_daemon_log.error(..., exc_info=True)` -- mirrors `_dismiss_async`. `run_frontrunner_build` is documented D-1/never-raises, but an unawaited `Future` silently drops any exception that somehow escapes that contract; the wrapper makes a D-1 violation observable in the logs (defense-in-depth) instead of silently lost.

CSRF-protected via `_csrf_before_request`. Not in `_SETTINGS_WRITE_ALLOWLIST` (not a settings write). No `LIVE_EXECUTION` interaction.

#### `POST /ai-advisor/proposal/approve` — `ai_advisor_proposal_approve()`

Generic approval route for `frontrunner_proposals` rows (AC-9/AC-10). Serves **both** proposal sources -- `'frontrunner_builder'` and `'strategy_builder_retrofit'` -- since both land in the same `frontrunner_proposals` table (migration 033) and both flow through the identical `advisors.frontrunner_builder.approve_frontrunner_proposal`, which is itself source-agnostic (keyed purely by row id). **Ruled** (team-lead, 2026-07-11): a single opaque `proposal_id`, no source-disambiguation parameter.

**This is the only route in the app that can reach `composer_draft_client.save_symphony`** -- exclusively via `approve_frontrunner_proposal`, never called directly here. Approval creates a NEW UNDEPLOYED Composer symphony (`verify_undeployed` enforced inside the called function) -- never a trade, never a deploy/invest call.

**Request body:** `{ proposal_id: <int> }` -- non-int/missing → `200 {"success": false, "error": "invalid proposal_id"}`.

Bounded (1-2 Composer calls) -- safe to run synchronously in-request, unlike `/run`. Route outer `except`: `{"error": type(exc).__name__}` -- D-1, never echoes `str(exc)` (may carry Composer credentials or internal paths). On success: `{"success": result.success, "symphony_id": result.symphony_id, "error": result.error}`.

CSRF-protected via `_csrf_before_request`. Not in `_SETTINGS_WRITE_ALLOWLIST`. No `LIVE_EXECUTION` interaction.

#### `POST /ai-advisor/proposal/reject` — `ai_advisor_proposal_reject()`

Status-only DB write for `frontrunner_proposals` rows (AC-9/AC-10) -- never touches `composer_draft_client` (same shared-table rationale as the approve route above). Calls `database.update_frontrunner_proposal_status(proposal_id, approval_status="rejected")`.

**Request body:** `{ proposal_id: <int> }` -- same validation/error shape as approve.

CSRF-protected via `_csrf_before_request`. Not in `_SETTINGS_WRITE_ALLOWLIST`. No `LIVE_EXECUTION` interaction.

---

### `ai_advisor_tab()` — Frontrunner Builder panel prefetch

Additive to the existing `GET /ai-advisor` context assembly (see the template-context table above). One query shared by both proposal sources -- the template branches per-card on `proposal_source`:

```python
frontrunner_proposals = database.get_pending_frontrunner_proposals()
```

**`candidate_tree` bounding (never rendered as a live dict):** the full spliced candidate symphony (potentially 8,000+ nodes) is popped off each row and replaced with a JSON-dumped, truncated preview string (`_FR_TREE_PREVIEW_MAX_CHARS = 4000`) stamped as `candidate_tree_preview` before the row ever reaches the template. Wrapped in `try/except` -- any failure (query error, malformed row) leaves `frontrunner_proposals = []` and the template renders its existing empty-state; never a 500.

**Frontrunner-signals cycle addition, built then removed (2026-07-16, `ae5fe22d` build / `f563f16c` + `6715d654` removal, feature-plans/frontrunner-signals.md AC-7/AC-R1/AC-R2):** `ai_advisor_tab()` briefly additively called `advisors.frontrunner_signals.get_latest_classifications()` and `get_latest_run_marker(symphony_id=<id>)` to prefetch a Frontrunner Builder tab "Live Signal Classification" subsection. Both the route-level calls and the underlying accessor functions were removed the same day per the operator's de-productization ruling — the classification tab productized a one-time PM cull-analysis deliverable that was never asked for as a product feature. `ai_advisor_tab()` carries zero diff related to this surface as of `6715d654`; the rest of the Frontrunner Builder tab (Run build, proposal cards, approval flow, `candidate_tree` bounding above) is unaffected. See `DE-FR-SIGNALS-001` in `DECISIONS.md` for the operator's verbatim ruling and the full account.

---

### `ai_advisor_tab()` — Strategy Incubation live-join badge stamping (`DE-INCUBATION-GATE-001`, 2026-07-25)

Additive, right after the existing `sb_observations` build block. Builds a `candidate_hash -> ledger row` lookup from ONE `database.get_incubation_overview()` call, then for every `sb_observations` entry whose `raw_response.candidate_hash` matches a ledger row, stamps `_incubation_badge_label`/`_incubation_badge_modifier` onto that observation dict in place (mirrors the RF-1 `_preview_text` in-place-stamp precedent above). Computed fresh per request, from the SAME `_incubation_badge()` helper the `GET /api/incubation` route uses (see above) -- never a frozen field, per the AC-5 amendment: `advisor_observations` rows are append-only/immutable, so a status frozen at persist time could never reflect a later promotion/failure.

A survivor with no `candidate_hash` (a pre-feature row, or an admission that failed/hit the cap) or no matching ledger row is left un-stamped -- `templates/ai_advisor.html` renders no chip for that card (`{% if obs._incubation_badge_modifier %}`-guarded) rather than fabricating a status. Wrapped in `try/except`: a ledger-read failure at this stamping step degrades to zero chips rendered, never a 500 of the whole `/ai-advisor` page.

---

### State Helpers

#### `get_api_state_dict() → dict`
Assembles the full state payload for `/api/state` and the dashboard template. Reads `bot_state`, computes `portfolio_strip`, builds `meta`, adds `exit_authority` via `os.getenv("EXIT_AUTHORITY")`.

**F-1 shared connection (`DE-OPS-CLUSTER-001`, 2026-07-21):** opens ONE function-local read-only `sqlite3.Connection` (`_shadow_conn`, `mode=ro` URI, `timeout=10.0`) before its single `_compute_portfolio_strip()` call and closes it in a `finally` — see `_compute_portfolio_strip()`'s own F-1 note below for why one connection replaces what used to be a per-symphony-per-helper `connect()`. Falls back to `conn=None` (today's pre-fix per-call behavior) if the shared connection itself fails to open; never a module-global.

#### `_compute_portfolio_strip(bot_state: dict, trading_day: str | None = None) → dict`
Shared by `get_api_state_dict()` (Jinja render path) and `get_state()` (JSON poll path) so both paths emit identical `portfolio_strip` shape.

**Signature (F-1, `DE-OPS-CLUSTER-001`, 2026-07-21):** `_compute_portfolio_strip(bot_state: dict, trading_day: str | None = None, conn: sqlite3.Connection | None = None) → dict`. `conn` is an optional pre-opened read-only connection, forwarded to the portfolio CR/TC/MDD/windowed helpers below. This function never owns `conn`'s lifecycle — the caller opens and closes it; `conn=None` (the default) falls back to each helper opening its own connection per call, byte-identical to pre-fix behavior.

**F-1 connection-count fix — the ~157-connects/poll finding.** The PM reproduced ~157 SQLite connects / 1.4 s on a real `/api/state` poll. Root cause: `_compute_portfolio_strip` calls 5 portfolio-level analytics helpers (`get_portfolio_cumulative_return`, `get_portfolio_today_change`, `get_portfolio_max_drawdown`, `compute_windowed_portfolio_strip`, and transitively `compute_windowed_symphony_guard_alpha`), each of which loops every symphony internally and opened its OWN `sqlite3.connect()` per symphony (via `analytics._value_weighted_portfolio` and `_get_windowed_divergence_trajectory`) — O(symphonies) connects per helper, times 5 helpers, times however many call sites in a single poll. Fix: an optional `conn: sqlite3.Connection | None = None` kwarg threaded through 11 `analytics.py` functions (see `docs/generated/analytics.md`'s F-1 section for the full list), with `_compute_portfolio_strip` forwarding its own `conn` param to every one of the 5 helpers above. The caller (`get_api_state_dict()` / `get_state()`) opens ONE shared read-only connection per request and passes it down; connection count per poll drops to a small constant regardless of symphony count. `conn=None` on every threaded function is byte-identical to pre-fix behavior — `alpha_bot_execution.py`'s one call site (the engine) and `dashboard()`'s own separate per-symphony loop (`app.py` ~1018-1033, ThreadPoolExecutor pattern) both call with no `conn=` and are confirmed unaffected (out of scope, not part of this fix).

**`data_as_of` derivation (AC-7 fix):** The `data_as_of` field is derived from the actual engine cycle timestamp, not the server render clock. Implementation:
1. Reads `bot_state.get("last_successful_cycle_at")` directly from the **top level** of the state dict (app.py:1283). The engine writes this key at the top level of `bot_state` in `alpha_bot_execution.py:948/1092/1878`; it is NOT nested inside per-symphony sub-dicts.
2. Parses the ISO timestamp; if timezone-naive, attaches `_ET` so `strftime` renders the correct HH:MM without a local-system offset shift (the engine writes `current_et.isoformat()` — an ET-local naive datetime).
3. Falls back to `datetime.now(_ET).strftime("%H:%M ET")` when no cycle timestamp is present.

This ensures the `data_as_of` display reflects when the cycle data was captured, not when the HTTP request was served. The BLOCK-B TOCTOU fix also ensures `data_as_of` is snapshotted at data-capture time on the historical branch in `get_api_state_dict()`.

**Prior defect (fixed this cycle):** The original implementation iterated `bot_state.values()` looking for the key inside per-symphony sub-dicts — a shape that production never emits. Every call fell through to `datetime.now()`, making `data_as_of` the server render clock rather than the cycle timestamp. The regression test that was supposed to guard this was GREEN-but-HOLLOW: its fixture wrote `last_successful_cycle_at` inside a per-symphony dict, matching the broken code's iteration path rather than the real production shape (top-level key). The fix and its test now both operate on the real top-level structure.

**Cache reads:** All `_account_totals_cache` reads use `.get()` (single call, TOCTOU-safe against `_StaleFlagDict.mark_stale()`). The `portfolio_value` is sourced from the cache when available; falls back to a per-symphony sum from `bot_state` when the cache is masked (stale window after `_notify_cycle_complete()`).

**B-2 today-change account-basis fix (DE-TODAY-BASIS-001):** The warm-cache today-change block now routes through `analytics.get_portfolio_today_change_account_basis()`. Previously, `if_held` was `_cached_tc` (Composer `todays_percent_change`, account-value denominator, cash-inclusive) while `dry_run` was the VW portfolio today-change (symphony-value denominator, cash-excluded). Different denominators produced phantom bot-vs-held divergence even when no guard had fired. The fix computes a guard delta on the VW basis (common denominator) and scales it by `invested_frac = symphony_value_sum / account_value` before applying it to the account-level Held today-change. With zero guard divergence, `guard_delta_vw == 0` and `dry_run == account_if_held_tc` exactly. `_symphony_value_sum` is now hoisted before both the CR and TC blocks (previously scoped inside the `if _cached_cr` branch, out of reach for TC). The cold-cache fallback (`else` branch, VW-both) is unchanged.

**EOD account-basis unification — two-tier per-field stale-cache hardening (DE-EOD-BASIS-001):** The warm-cache branch above assumes `_account_totals_cache.get(...)` returns a value. When it returns `None` (the every-minute `mark_stale()` window, a Composer timeout, or a fresh process with nothing fetched yet), TC, CR, and `account_value` are each resolved through an INDEPENDENT two-tier fallback — no field's fallback state gates another field's wrap:

- **Tier 1 (last-good):** if `_account_totals_last_good` holds the field, it is used in place of the masked cache value; the VW-basis wrap still runs through the same `analytics.get_portfolio_*_account_basis()` helper as the warm-cache path, and `_live_basis_stale = True` is set. `account_value` gained this Tier-1 fallback in this cycle — previously it fell straight from a masked cache read to the cash-EXCLUDED per-symphony sum with no last-good check at all (asymmetric vs. the CR/TC fields and vs. the frozen path's equivalent variable).
- **Tier 2 (honest floor):** if `_account_totals_last_good` has nothing for that field either (never fetched this process), the raw VW value is used unwrapped.

After both TC and CR are resolved, the strip is stamped:
- `portfolio_strip["basis"] = "value_weighted"` fires when EITHER TC or CR has no basis at all (Tier 2 for that field) — each field's own cache+last-good state is checked independently, so a warm TC is never silently mislabelled just because CR is on Tier 2 (previously the marker only checked TC's state, letting a CR-only degradation ship as unlabelled raw VW). The check is an explicit `is None` test, not a falsy check — a genuine last-good value of `0.0` (a real flat-day reading) counts as present, not fully-missing; a post-review pass fixed an earlier falsy-check version that would have mislabelled a correctly Tier-1-wrapped `0.0` as `value_weighted` (see "Post-review hardening" in `DECISIONS.md`).
- `portfolio_strip["account_basis_stale"] = True` + `portfolio_strip["account_basis_as_of"]` fire when `_live_basis_stale` is set (i.e. at least one field used Tier 1). `account_basis_as_of` falls back to a fresh `datetime.now(_ET)` string when `_account_totals_last_success_at` was never set, so it is never `None` while `account_basis_stale=True`.

See `DE-EOD-BASIS-001` in `DECISIONS.md` for the frozen-path mirror (`get_state()`, documented under `GET /api/state` above) and the 5 findings this hardening pass fixed.

**F-026 `hist_source` explicit fix (`DE-DISPLAY-TRUTH-001`, 2026-07-21):** `_compute_portfolio_strip`'s `_strip` dict now sets `"hist_source": "shadow_history"` explicitly. Its `hist_bot`/`hist_held` arrays are populated from `analytics.get_portfolio_bot_and_held_daily_returns()` -- genuinely shadow_history-sourced -- but the key was never set before this fix, so `_build_meta` (`app.py:1133`, `ps.get("hist_source", "post_mortem")`) silently served the wrong default label. The two other `hist_source` builders in this file (the shadow-history and post-mortem chart-fallback attempts inside the historical-chart branch, `app.py` ~1643/~1677) already set this key correctly and are unchanged/regression-pinned.

#### `_safe_analytics(fn, *args, coerce_none: bool = True, **kwargs) → dict` (inside `dashboard()`)
Local helper in the `GET /` route that wraps an `analytics.get_symphony_*` call, returning `{"if_held": ..., "dry_run": ...}` and never raising (any exception or non-dict result degrades to a safe default).

**`coerce_none` kwarg (F-016 locus 2, `DE-DISPLAY-TRUTH-001`, 2026-07-21):** previously `_safe_analytics` always coerced a `None` result value to `0.0` before the template ever saw it -- a genuinely missing Today-change value (`_tc`) was fabricated into a false `+0.0%` by the per-card SSR Jinja regardless of any template-side None-guard. `coerce_none` defaults to `True` (unchanged behavior) but the `_tc` call site now passes `coerce_none=False`, letting a real `None` survive to `templates/index.html`'s None-aware Today guard (mirrors the pre-existing MDD-row `is not none` precedent). **Scoped narrowly on purpose:** the `_cr` (Cumulative) and `_mdd` (Max Drawdown) calls are untouched -- they still coerce `None → 0.0` exactly as before. See `DE-DISPLAY-TRUTH-001` in `DECISIONS.md` for why this was deliberately not extended to `_cr`/`_mdd`.

#### `_tc_cr_mdd_floats(s: dict) → tuple` (inside `get_state()`, `app.py:2396`)
Extracts `(tc_bot, tc_held, cr_bot, cr_held, mdd_bot, mdd_held)` from a symphony's `_tc`/`_cr`/`_mdd` sub-dicts for the real `/api/state` JSON response (feeds `_symphonies_for_cards`, i.e. `data.symphonies[].tc_bot` etc., the source `static/index.js`'s `updateCards` reads on every 30 s poll).

**F-016 locus 3 fix (`DE-DISPLAY-TRUTH-001`, 2026-07-21, found by review):** each of the six fields previously ended in `... or None` (e.g. `tc_bot = (tc.get("dry_run") if isinstance(tc, dict) else tc) or None`). Since `0.0` is falsy in Python, `or None` silently converted a **genuine 0.0** ("no change today", a fully plausible real value) into a fabricated `null` -- misrendered as the honest-empty-state em-dash by `updateCards` on every live poll, the *opposite* direction from loci 1/2 (there: null wrongly became a false zero; here: a real zero wrongly became null). Fixed by dropping the trailing `or None` on all six fields -- `.get()` on a missing key already returns `None`, so the `or None` was redundant on top of being the sole source of the bug. A genuine `None` (symphony truly has no reading) still renders `None` unchanged; only a genuine `0.0` is affected by the fix. See `DE-DISPLAY-TRUTH-001` in `DECISIONS.md` for the review→escalation→PM-ruling trail this finding went through (the cr/mdd half of the fix required a dedicated PM ruling before it shipped).

#### `_compute_suggestion_gates(suggestion, symphony_id: str) → dict`
Computes four-gates verdict booleans for one suggestion: `allowlist`, `risk_direction`, `oos_frozen_eval`, `locked_vars`.

#### `_enrich_suggestion_impact(suggestion) → dict`
Builds impact dict with `before`, `after`, `delta`, `metric` fields from the suggestion's raw impact payload.

#### `_normalize_autotune_row(row: dict) → dict`
Normalizes an `autotune_runs` DB row for the `/api/autotune-runs` JSON response.

## Internal Dependencies

- `ai_advisor` — context assembly, Claude call, C2 safety gates, assessment builder; `build_reasoning_context` (R2-1, symphony-scoped Strategy Builder runs; R2-2, symphony-scoped Logic Changes runs — same call shape, two independent call sites); `_EMPTY_MANIFEST` (R2-2, the default `evidence_injected` value inside the Logic Changes route's `_default_provenance`)
- `model_config` — `get_advisor_suggestion_model()` (R2-2: the Logic Changes route's `_default_provenance["generation_model"]`; also read by the SB route's pipeline, `app.py:3813`)
- `uuid` — **NEW (R2-2), stdlib.** `str(uuid.uuid4())` mints the Logic Changes route's default `provenance["run_id"]` on every early-exit branch
- `database` — all state-DB reads and writes for advisor routes, including `get_latest_market_prism_verification_for_run` (DE-PRISM-NUMERIC-VERIFY-001 Overview overlay), `get_candidate_alert_new_valid_count`/`get_candidate_alert_last_run`/`mark_candidate_alert_viewed` (candidate-alert header indicator), `load_state`/`normalize_name` (NAME→hash resolution for `build_reasoning_context`, both the SB and Logic Changes call sites), and `get_pending_frontrunner_proposals`/`update_frontrunner_proposal_status` (frontrunner-builder wave-2)
- `analytics` — symphony history, correlation data, symphony list; account-basis translation helpers (`get_portfolio_today_change_account_basis`, `get_portfolio_cumulative_return_account_basis`, DE-TODAY-BASIS-001) consumed by both the live and frozen portfolio-strip paths
- `advisors.correlation_diagnostic` — `compute_pairwise_correlations`, `CRISIS_CAVEAT`
- `advisors.asset_swap_engine` — `propose_operator_swap`, `SwapObjective`, `_has_composer_key`
- `advisors.logic_change_engine` — `propose_operator_logic_change`, `LogicTweak`, `LogicChangeObjective`, `_has_composer_key` (lazy import, `app.py:ai_advisor_logic_changes_evaluate()`)
- `advisors.advisor_chat` — `explain_artifact`, `CHAT_ARTIFACT_MAX_FIELD_VALUE_CHARS`
- `advisors.strategy_builder_engine` — `propose_strategies`, `Objective`, `ScreenConfig` (lazy import)
- `advisors.build_plan_generator` — `load_atlas_candidates` (lazy import, C5 route rewire)
- `advisors.frontrunner_builder` — `run_frontrunner_build`, `approve_frontrunner_proposal` (lazy imports, frontrunner-builder wave-2 routes)
- `alpha_bot_execution` — `ensure_bot_state_seeded` (lazy import, startup seed)
- `symphony_logic` — `fetch_symphony_score`
- `werkzeug.security` — `check_password_hash` for `DASHBOARD_PASSWORD_HASH` verification
