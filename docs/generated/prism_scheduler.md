# prism_scheduler

> Nightly Market Prism scheduler wrapper — invokes the Market Prism council via a vanilla-primary headless Claude session, triggered by Windows Task Scheduler (Option B, daemon-decoupled).

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
| `MAX_BUDGET_USD` | `15.0` | Per-run spend ceiling passed as `--max-budget-usd`. A full 6-agent Opus council realistically costs $5–10/run; 15.0 is a runaway-prevention ceiling, not a target. |
| `PRISM_RUN_PROMPT` | (see below) | Prompt passed to the vanilla-primary headless session. Instructs the primary session to spawn all 6 agents directly (prism-synthesizer + 5 analysts). `prism-synthesizer` has no Agent/spawn tool — it coordinates the already-running analysts via SendMessage only. |

### `PRISM_RUN_PROMPT`

The full prompt passed as the final positional argument to `claude -p`. It instructs the primary session to spawn all 6 agents directly: `prism-synthesizer` (as team lead) plus the 5 analysts (`prism-technicals-analyst`, `prism-sentiment-analyst`, `prism-derivatives-analyst`, `prism-macro-analyst`, `prism-fundamentals-analyst`). Once all agents are running, `prism-synthesizer` coordinates them via SendMessage: it requests each analyst's read, runs Q&A and conditional debate (up to 3 rounds), integrates all outputs into one `MARKET_PRISM` observation, and writes it to the DB via `prism_audit_write`. The primary session includes a completion guard — it does not return until `prism-synthesizer` confirms the row is written. The session is fully unattended — no user input will arrive.

**Why the primary spawns all 6 (not prism-synthesizer):** `prism-synthesizer` is a named agent with a coordination-only role — it has no Agent/spawn tool in its toolset. Giving it spawn responsibility would require adding a general-purpose tool that blurs its role boundary. The primary session is the only session with the full Agent tool surface; it spawns all council members, then hands control to `prism-synthesizer` to coordinate. This keeps each agent's role clean: primary = dispatcher, synthesizer = coordinator, analysts = producers.

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

Parses the subprocess JSON stdout for `total_cost_usd` (CC 2.1.181+ envelope) with a tolerant fallback to the legacy `cost_usd` key, and writes a `prism_audit_log` row via `database.insert_prism_audit_entry` with `agent_role="LAUNCHER"` and `phase="spend_log"`. The persisted `content` JSON uses `total_cost_usd` as the key name. Non-fatal — a parse or DB failure is logged as `type(exc).__name__` only (D-1) and swallowed. Called only on `returncode == 0`.

### `_run_prism(run_id: str = "unknown") -> bool`

Invokes the Market Prism council as a vanilla-primary headless subprocess. Returns `True` on `returncode == 0`, `False` on non-zero or exception.

**Why vanilla `-p` (no `--agent` pin):** A session pinned with `--agent prism-synthesizer` cannot spawn additional agents — the `--agent` flag locks the session to a single agent role and disables the Agent/spawn tool. The Prism council requires the primary session to spawn all 6 agent roles (5 analysts + synthesizer). Using `claude -p` launches a full primary session with the Agent tool available.

**Command built:**
```
claude -p --dangerously-skip-permissions
       --model claude-opus-4-8
       --max-budget-usd 15.0
       --output-format json
       "<PRISM_RUN_PROMPT>"
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

Runs the idempotency check and, if no today's row exists, invokes the Market Prism council.

### Teardown

```powershell
Unregister-ScheduledTask -TaskName "PlanetStopperMarketPrism" -Confirm:$false
```

## Idempotency Contract

The script checks for a MARKET_PRISM row with `created_at` matching today UTC before invoking the subprocess. If two Task Scheduler triggers fire in one window (e.g., a restart + scheduled trigger overlap), the second invocation finds the row written by the first and exits 0 without writing a duplicate.

## Retry Contract

The bounded retry loop iterates `range(1, MAX_ATTEMPTS + 1)`. On each failed attempt, it sleeps `min(BACKOFF_BASE_SECONDS * 2^(attempt-1), BACKOFF_CAP_SECONDS)` seconds before the next attempt. After `MAX_ATTEMPTS` failures, it exits 1. The loop cannot run indefinitely — `while True` is structurally absent.

## Spend Logging Contract

On each successful subprocess invocation (`returncode == 0`), `_persist_spend()` parses `result.stdout` as JSON and extracts `total_cost_usd` (CC 2.1.181+ envelope key), falling back to the legacy `cost_usd` key for older/local CC builds. If a value is found, it writes one `prism_audit_log` entry: `run_id=<uuid>`, `agent_role="LAUNCHER"`, `phase="spend_log"`, `content={"total_cost_usd": <value>}`. Failures in parsing or DB write are non-fatal — the run is still considered successful.

## D-1 Error Contract

All error paths surface `type(exc).__name__` only — no raw exception messages, file paths, or tracebacks are logged or propagated. This applies to `.env` load failures, DB query failures, subprocess exceptions, and spend-log parse/write failures.

## Internal Dependencies

- `database` — `get_latest_market_prism_summary()` (idempotency check) + `insert_prism_audit_entry()` (spend logging); both lazy-imported inside their respective wrappers
- `dotenv` — `.env` loading (lazy import inside `_load_env()`)
- `subprocess` — headless `claude` invocation
- `uuid` — `uuid4()` run_id generation in `main()`
- `schedule_prism.ps1` — companion Task Scheduler registration script (project root)

## Tests

`tests/ai_advisor/test_prism_scheduling.py` — 33 tests covering AC-1 through AC-8 plus HC-1 (spend cap), HC-2 (spend logging), HC-3 (model pin), the Phase-4 invocation shape (vanilla `-p`, no `--agent` pin, `PRISM_RUN_PROMPT` as positional arg, `MAX_BUDGET_USD=15.0`), and the council architecture (primary spawns all 6; `prism-synthesizer` coordinates only via SendMessage). All tests mock `subprocess.run`, `time.sleep`, and `_get_summary()` — no real DB calls, no real subprocess invocations.
