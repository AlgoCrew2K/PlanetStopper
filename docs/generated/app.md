# app

> Flask daemon: minute-by-minute scheduler, operator dashboard routes, AI Advisor endpoints, and daemon singleton lifecycle.

**Source:** `app.py`
**Last updated:** 2026-06-02

## Overview

`app.py` is the Flask application and process host. It owns:

- **Daemon singleton** — pidfile-based single-instance enforcement at startup.
- **Minute scheduler** — spawns `alpha_bot_execution.py` at `:00` via `subprocess.run`; refreshes Composer account totals once per minute; prunes telemetry at 02:00.
- **Dashboard routes** — operator UI routes. Two CSRF-protected write paths exist: `POST /api/settings` (allowlisted .env keys) and `POST /api/symphony-settings/<name>` (per-symphony live-mode toggle). Templates open SQLite read-only; the dashboard is NOT a live-trade-action surface.
- **AI Advisor routes** — 13+ routes across context assembly, suggestion, accept/reject, chat, and tab-view endpoints.
- **CSRF infrastructure** — `_validate_csrf()` hook; `_csrf_before_request` before-request handler; `GET /api/csrf-token` token endpoint; `_SETTINGS_WRITE_ALLOWLIST` restricts which .env keys the settings write path can touch.

Module-level thread-safety constructs:

- `_DISMISS_EXECUTOR` — `ThreadPoolExecutor(max_workers=1)` for fleet-alert dismiss writes. Registered with `atexit` for graceful shutdown.
- `_FLUSH_STATE_LOCK` — `threading.Lock()` serializing `flush_resync` background writes against engine `save_state` writes.
- `_CHAT_RATE_LIMITER` — per-IP rate-limiter for AI Advisor chat endpoint (cost-DoS guard; max `CHAT_RATE_LIMITER_MAX_TRACKED_IPS` IPs).

## API Reference

### Daemon Singleton

#### `_acquire_daemon_singleton(pidfile: str) → None`
Enforces one-process invariant at startup. Reads the pidfile (if present), checks whether the stored PID refers to a live Planet Stopper process. Live → exit(1). Stale → take ownership. Registers atexit handler and SIGTERM handler to remove the pidfile on clean shutdown.

---

### CSRF Infrastructure

#### `GET /api/csrf-token`
Returns a fresh CSRF token for the current session. Required for all CSRF-protected write endpoints.

#### `_validate_csrf() → None`
Validates the `X-CSRF-Token` header against the session token. Raises `403` on mismatch. Called at the top of every CSRF-protected route.

#### `_csrf_before_request`
Flask `before_request` hook. Injects CSRF enforcement for the two guarded write paths.

---

### Scheduler

#### `run_scheduler() → None`
Runs the `schedule` loop: every minute at `:00` triggers `threaded_trigger()` and `_refresh_account_totals()`; daily at 02:00 runs `_run_trigger_retention()`.

#### `trigger_alpha_bot(force: bool = False) → None`
Spawns `alpha_bot_execution.py` as a subprocess. Passes `--force` when `force=True`. Logs stdout/stderr to `alphabot_daemon.log`.

#### `_refresh_account_totals() → None`
Fetches Composer account-level total-stats (`portfolio_value`, `simple_return`, `todays_percent_change`, `max_drawdown`) and populates `_account_totals_cache`. Swallows all exceptions — stale cache is preferred over an empty one.

#### `_run_trigger_retention() → None`
Prunes `exit_triggers` rows older than `TRIGGER_TELEMETRY_RETENTION_DAYS` (default 90) and `shadow_history` rows older than `SHADOW_HISTORY_RETENTION_DAYS` (default 180).

---

### Dashboard Routes

#### `GET /`
Dashboard root. Calls `get_api_state_dict()`, partitions symphonies into active/standby, enriches each with analytics data.

#### `GET /api/state`
Returns the full API state dict as JSON. Includes `bot_state`, `portfolio_strip`, `meta`, `exit_authority`, and `port_state` (SITE-D1 KEEP-DISPLAY).

#### `GET /history`
Render historical performance page.

#### `GET /performance`
Render performance analytics page.

#### `GET /api/performance`
Returns per-day performance metrics as JSON.

#### `GET /api/performance/symphonies`
Returns per-symphony performance breakdowns.

#### `GET /api/history/<int:days>`
Returns historical portfolio data for the last `days` days.

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

#### `GET /api/autotune-runs`
Returns paginated autotune run history via `database.get_all_autotune_runs`. Response normalized via `_normalize_autotune_row`.

#### `GET /api/advisor-observations`
Returns `advisor_observations` rows for a symphony. Accepts `?symphony_id=` query parameter.

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

#### `GET /ai-advisor`
Main AI Advisor tab. Renders suggestion context for all `_ADVISOR_ROLES`.

#### `GET /ai-advisor/correlations`
AI Advisor correlation diagnostic tab.

#### `GET /ai-advisor/asset-swaps`
AI Advisor asset-swap tab — lists current portfolio tickers and potential swap candidates.

#### `POST /ai-advisor/asset-swaps/evaluate`
Evaluates a proposed asset swap through the OOS backtest gate.

#### `GET /ai-advisor/logic-changes`
AI Advisor logic-change tab — lists current Composer symphony logic and potential structural changes.

#### `POST /ai-advisor/logic-changes/evaluate`
Evaluates a proposed logic change through the OOS backtest gate.

#### `POST /ai-advisor/suggest`
Assembles advisor context via `ai_advisor.assemble_advisor_context`, calls `ai_advisor.request_suggestions`, applies `enforce_suggestion_allowlist` and `check_risk_direction_agreement`. Returns the allowed suggestions with risk-direction flags and enriched impact via `_enrich_suggestion_impact`.

#### `POST /ai-advisor/accept`
Accepts one Claude suggestion. Calls `ai_advisor.revalidate_suggestion_oos`; on pass, writes the new param value to the symphony strategy via `database.save_symphony_strategy` (`app.py:3029`). Records the accepted suggestion in `llm_suggestions` via the audit path. Does NOT call `database.record_llm_suggestion`.

#### `POST /ai-advisor/reject`
Records a rejected suggestion to the audit trail.

#### `GET /ai-advisor/chat`
Renders AI Advisor chat page.

#### `POST /ai-advisor/chat/send`
Rate-limited (per-IP via `_CHAT_RATE_LIMITER`) chat endpoint. Enforces CSRF. Calls `ai_advisor.advisor_chat` or equivalent.

---

### State Helpers

#### `get_api_state_dict() → dict`
Assembles the full state payload for `/api/state` and the dashboard template. Reads `bot_state`, computes `portfolio_strip`, builds `meta`, adds `exit_authority` via `os.getenv("EXIT_AUTHORITY")`.

#### `_compute_suggestion_gates(suggestion, symphony_id) → dict`
Computes display-side gate indicators for a suggestion (allowlist, risk-direction, OOS status).

#### `_enrich_suggestion_impact(suggestion, symphony_id) → dict`
Enriches a suggestion dict with OOS-alpha delta and other impact metadata.

#### `_normalize_autotune_row(row: dict) → dict`
Normalizes an `autotune_runs` DB row for the `/api/autotune-runs` JSON response.

## Types

### Module-Level Globals

| Symbol | Type | Description |
|--------|------|-------------|
| `_DISMISS_EXECUTOR` | `ThreadPoolExecutor` | Single-worker executor for fleet-alert dismiss writes |
| `_FLUSH_STATE_LOCK` | `threading.Lock` | Serializes flush_resync against engine save_state |
| `_DAEMON_STARTED_AT` | `str` | ISO 8601 UTC timestamp captured at import time |
| `_account_totals_cache` | `dict` | TTL cache for Composer account totals |
| `_symphony_settings_cache` | `dict` | Per-symphony settings cache for gear-icon modal |
| `_SETTINGS_WRITE_ALLOWLIST` | `frozenset` | Allowlisted .env keys writable via `POST /api/settings`; excludes credentials and safety flags |
| `_MASKED_SETTINGS_KEYS` | `frozenset` | Keys whose values are redacted in `GET /api/settings` responses |
| `_ALGO_PARAM_META` | `dict` | 8-key algorithm parameter metadata (help text, unit, kind) for the Settings screen |
| `_ADVISOR_ROLES` | `list` | Valid advisor role strings |
| `_CHAT_RATE_LIMITER` | `dict` | Per-IP rate-limiter for AI Advisor chat (cost-DoS guard) |

## Internal Dependencies

- `database` — state reads/writes, advisor observations, strategy management, `set_symphony_live_mode`
- `ai_advisor` — `assemble_advisor_context`, `request_suggestions`, C2 safety gates
- `analytics` — performance metrics for dashboard
- `market_calendar` — `get_market_state`
- `engine.exit_authority` — exit-authority badge and restart-notice context helpers
