# prism_scheduler

> Nightly Market Prism scheduler wrapper — invokes the prism-synthesizer Claude agent via Windows Task Scheduler (Option B, daemon-decoupled).

**Source:** `prism_scheduler.py`
**Last updated:** 2026-06-18

## Overview

`prism_scheduler.py` is a standalone script registered with Windows Task Scheduler to run the Market Prism nightly pipeline at 03:00 local time (US Central). It is fully decoupled from the Flask daemon — the daemon's `run_scheduler()` 03:00 slot continues to run `advisors/lens_pipeline.py` (the data layer) and is not modified.

The script enforces three guards: an idempotency guard (skips if today's MARKET_PRISM row already exists), bounded retry with exponential backoff (prevents the persistent-429 infinite-loop crash that caused prior PC crashes), and the D-1 error contract (only `type(exc).__name__` is logged — never raw exception messages or paths).

Per-run Opus spend is captured from the `--output-format json` subprocess stdout and persisted to `prism_audit_log` via `_persist_spend()`. A hard per-run budget cap (`MAX_BUDGET_USD`) is passed as `--max-budget-usd` to the subprocess.

## Constants

| Name | Value | Description |
|------|-------|-------------|
| `MAX_ATTEMPTS` | `3` | Maximum subprocess invocations per run |
| `BACKOFF_BASE_SECONDS` | `30` | First retry wait in seconds |
| `BACKOFF_CAP_SECONDS` | `60` | Maximum wait between retries in seconds |
| `MAX_BUDGET_USD` | `5.0` | Per-run Opus spend cap passed as `--max-budget-usd` |

## API Reference

### `main() -> None`

Main entry point. Exits 0 on success (row already exists today, or subprocess succeeded). Exits 1 if all `MAX_ATTEMPTS` are exhausted without a successful run.

**Behavior:**
1. Loads `.env` from project root via `_load_env()`
2. Generates a fresh `uuid4` `run_id` for this invocation
3. Calls `_get_summary()` → `database.get_latest_market_prism_summary()`
4. If today's row exists (UTC date comparison) → prints skip message and `sys.exit(0)`
5. Otherwise: attempts `_run_prism(run_id)` up to `MAX_ATTEMPTS` times with exponential backoff
6. On first success → `sys.exit(0)`; on exhaustion → `sys.exit(1)`

### `_load_env() -> None`

Loads `.env` from project root using `python-dotenv`. Silent on failure (D-1: logs nothing to avoid echoing secrets).

### `_get_summary() -> dict | None`

Wrapper around `database.get_latest_market_prism_summary()`. Patchable in tests.

### `_is_todays_row(row: dict | None) -> bool`

Returns `True` if `row["created_at"]` (format `"YYYY-MM-DD HH:MM:SS"`, UTC) matches today's UTC date. Returns `False` for `None` or unparseable values.

### `_persist_spend(run_id: str, stdout: str) -> None`

Parses the subprocess JSON stdout for `cost_usd` and writes a `prism_audit_log` row via `database.insert_prism_audit_entry` with `agent_role="LAUNCHER"` and `phase="spend_log"`. Non-fatal — a parse or DB failure is logged as `type(exc).__name__` only (D-1) and swallowed. Called only on `returncode == 0`.

### `_run_prism(run_id: str = "unknown") -> bool`

Invokes the prism-synthesizer as a subprocess. Returns `True` on `returncode == 0`, `False` on non-zero or exception.

**Command built:**
```
claude -p --agent prism-synthesizer --dangerously-skip-permissions
       --model claude-opus-4-8
       --max-budget-usd 5.0
       --output-format json
       "Run the Market Prism nightly run."
```

**Subprocess options:**
- `cwd=str(_PROJECT_ROOT)` — project root, not caller's cwd
- `env=os.environ.copy()` — inherits `ANTHROPIC_API_KEY` from loaded `.env`
- `capture_output=True, text=True` — captures stdout for spend logging
- `shell=False` — no shell injection risk

On success, passes `result.stdout` to `_persist_spend(run_id, ...)` for spend logging. Exceptions caught and logged as `type(exc).__name__` only (D-1).

## Usage

### Registration (one-time)

```powershell
powershell -ExecutionPolicy Bypass -File schedule_prism.ps1
```

Registers `PlanetStopperMarketPrism` in Windows Task Scheduler to run daily at 03:00 local time. `$ProjectRoot` is derived from `$PSScriptRoot` — no hardcoded paths.

### Manual run

```bash
python prism_scheduler.py
```

Runs the idempotency check and, if no today's row exists, invokes the prism-synthesizer.

### Teardown

```powershell
Unregister-ScheduledTask -TaskName "PlanetStopperMarketPrism" -Confirm:$false
```

## Idempotency Contract

The script checks for a MARKET_PRISM row with `created_at` matching today UTC before invoking the subprocess. If two Task Scheduler triggers fire in one window (e.g., a restart + scheduled trigger overlap), the second invocation finds the row written by the first and exits 0 without writing a duplicate.

## Retry Contract

The bounded retry loop iterates `range(1, MAX_ATTEMPTS + 1)`. On each failed attempt, it sleeps `min(BACKOFF_BASE_SECONDS * 2^(attempt-1), BACKOFF_CAP_SECONDS)` seconds before the next attempt. After `MAX_ATTEMPTS` failures, it exits 1. The loop cannot run indefinitely — `while True` is structurally absent.

## Spend Logging Contract

On each successful subprocess invocation (`returncode == 0`), `_persist_spend()` parses `result.stdout` as JSON and extracts `cost_usd`. If present, it writes one `prism_audit_log` entry: `run_id=<uuid>`, `agent_role="LAUNCHER"`, `phase="spend_log"`, `content={"cost_usd": <value>}`. Failures in parsing or DB write are non-fatal — the run is still considered successful.

## D-1 Error Contract

All error paths surface `type(exc).__name__` only — no raw exception messages, file paths, or tracebacks are logged or propagated. This applies to `.env` load failures, DB query failures, subprocess exceptions, and spend-log parse/write failures.

## Internal Dependencies

- `database` — `get_latest_market_prism_summary()` (idempotency check) + `insert_prism_audit_entry()` (spend logging); both lazy-imported inside their respective wrappers
- `dotenv` — `.env` loading (lazy import inside `_load_env()`)
- `subprocess` — headless `claude` invocation
- `uuid` — `uuid4()` run_id generation in `main()`
- `schedule_prism.ps1` — companion Task Scheduler registration script (project root)

## Tests

`tests/ai_advisor/test_prism_scheduling.py` — 23 tests covering AC-1 through AC-8 plus HC-1 (spend cap), HC-2 (spend logging), and HC-3 (model pin). All tests mock `subprocess.run`, `time.sleep`, and `_get_summary()` — no real DB calls, no real subprocess invocations.
