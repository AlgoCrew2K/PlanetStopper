# app

> Flask daemon: minute-by-minute scheduler, operator dashboard routes, AI Advisor endpoints, and daemon singleton lifecycle.

**Source:** `app.py`
**Last updated:** 2026-05-27

## Overview

`app.py` is the Flask application and process host. It owns:

- **Daemon singleton** — pidfile-based single-instance enforcement at startup.
- **Minute scheduler** — spawns `alpha_bot_execution.py` at `:00` via `subprocess.run`; refreshes Composer account totals once per minute; prunes telemetry at 02:00.
- **Dashboard routes** — read-only Flask routes for the operator UI. Templates open SQLite read-only; the UI never reruns the engine.
- **AI Advisor routes** — `/api/advisor-observations`, `/ai-advisor/suggest`, `/ai-advisor/accept`.

Two module-level thread-safety constructs:

- `_DISMISS_EXECUTOR` — `ThreadPoolExecutor(max_workers=1)` for fleet-alert dismiss writes. Registered with `atexit` for graceful shutdown.
- `_FLUSH_STATE_LOCK` — `threading.Lock()` serializing `flush_resync` background writes against engine `save_state` writes.

## API Reference

### Daemon Singleton

#### `_acquire_daemon_singleton(pidfile: str) → None`
Enforces one-process invariant at startup. Reads the pidfile (if present), checks whether the stored PID refers to a live AlphaBot process. Live → exit(1). Stale → take ownership. Registers atexit handler and SIGTERM handler to remove the pidfile on clean shutdown.

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

### Dashboard Routes (selected)

#### `GET /`
Dashboard root. Calls `get_api_state_dict()`, partitions symphonies into active/standby, enriches each with analytics data.

#### `GET /api/state`
Returns the full API state dict as JSON. Includes `bot_state`, `portfolio_strip`, `meta`, `exit_authority` (read from `os.getenv("EXIT_AUTHORITY")`), and `port_state` (SITE-D1 KEEP-DISPLAY).

#### `GET /api/advisor-observations`
Returns `advisor_observations` rows for a symphony. Accepts `?symphony_id=` query parameter; uses `database.get_advisor_observations_for_symphony` to resolve via the denormalized `symphony_id` column (migration 025, Sprint 3).

---

### AI Advisor Routes (Sprint 3)

#### `POST /ai-advisor/suggest`
Assembles the advisor context via `ai_advisor.assemble_advisor_context`, calls `ai_advisor.request_suggestions`, applies `enforce_suggestion_allowlist` and `check_risk_direction_agreement`. Returns the allowed suggestions list with risk-direction agreement flags.

#### `POST /ai-advisor/accept`
Accepts one Claude suggestion. Calls `ai_advisor.revalidate_suggestion_oos`; on pass, writes the new param value to the symphony strategy via `database.save_symphony_strategy`. Records the decision in `llm_suggestions` via `database.record_llm_suggestion`.

---

### State Helpers

#### `get_api_state_dict() → dict`
Assembles the full state payload for `/api/state` and the dashboard template. Reads `bot_state`, computes `portfolio_strip`, builds `meta`, adds `exit_authority` via `os.getenv("EXIT_AUTHORITY")`.

## Types

### Module-Level Globals

| Symbol | Type | Description |
|--------|------|-------------|
| `_DISMISS_EXECUTOR` | `ThreadPoolExecutor` | Single-worker executor for fleet-alert dismiss writes |
| `_FLUSH_STATE_LOCK` | `threading.Lock` | Serializes flush_resync against engine save_state |
| `_DAEMON_STARTED_AT` | `str` | ISO 8601 UTC timestamp captured at import time |
| `_account_totals_cache` | `dict` | TTL cache for Composer account totals (populated by scheduler) |
| `_PIDFILE_PATH` | `str` | Absolute path to the daemon pidfile |

## Internal Dependencies

- `database` — state reads/writes, advisor observations, strategy management
- `ai_advisor` — `assemble_advisor_context`, `request_suggestions`, C2 safety gates
- `analytics` — performance metrics for dashboard
- `market_calendar` — `get_market_state`
- `engine.exit_authority` — `get_exit_authority_badge_context`, `build_restart_notice_context`
