# prism_scheduler

> Nightly Market Prism scheduler wrapper — invokes the Market Prism council via a vanilla-primary headless Claude session, triggered by Windows Task Scheduler (Option B, daemon-decoupled).

**Source:** `prism_scheduler.py`
**Last updated:** 2026-06-18 (post-fix: F-1 run_id unification, F-2 synthesizer Hard Rules, F-4 row-verification + retry-on-empty)

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
| `PRISM_RUN_PROMPT` | (see below) | Prompt passed to the vanilla-primary headless session. Encodes the 5/5 council orchestration directives (DE-PRISM-5OF5). |

### `PRISM_RUN_PROMPT`

The full prompt passed as the final positional argument to `claude -p`. It encodes four orchestration directives that ensure reliable 5/5 analyst participation (DE-PRISM-5OF5):

**(a) Scheduler-generated run_id threaded into the prompt at call time.** `main()` generates `run_id = str(uuid.uuid4())` and passes it to `_run_prism(run_id)`. `_run_prism` builds the final prompt by appending `" The run_id for this session is: {run_id}. Use this exact string as the run_id for ALL audit rows and the MARKET_PRISM observation. Do not generate a new run_id."` to the static `PRISM_RUN_PROMPT` preamble. The council uses exactly this run_id — it does NOT mint its own. This is the single authoritative join key shared by all analyst `initial_read` audit entries, the synthesizer audit entries, the MARKET_PRISM observation `raw_response`, and the LAUNCHER spend_log row written by `_persist_spend`.

**(b) Embed kickoff in each analyst's spawn prompt.** Each of the 5 analyst agents is spawned with the run_id and an explicit instruction to produce and file their `initial_read` immediately on their first turn. This eliminates the dormancy window: the prior pattern (spawn first, then send a kickoff via SendMessage) caused 2/5 participation when dormant agents missed the subsequent message (root cause: transient by-canonical-name resume failure of dormant subagents, independently falsified — see DE-PRISM-COUNCIL).

**(c) Capture agentIds at spawn; pass to synthesizer.** The primary captures each analyst's agentId at spawn time and passes the full list to `prism-synthesizer` in its spawn prompt. The synthesizer uses agentIds — not canonical names — for all Q&A and debate coordination. By-canonical-name addressing of dormant/resumed subagents is unreliable.

**(d) Wait-barrier directive for synthesizer (Hard Rule).** The synthesizer is instructed not to synthesize until all 5 `initial_read` rows are present in the audit DB for this specific `run_id` (queried directly via `database.get_prism_audit_for_run(run_id)`) or the barrier times out with honest `limited-inputs` degradation naming each missing lens. Inbox-only collection is rejected as the wait mechanism: the audit DB is the authoritative source of truth.

**(e) False-attribution prohibition (Hard Rule).** A spawned analyst that did not file its `initial_read` is missing or late — not absent. `prism-synthesizer` must never record it as "did not spawn". The correct attribution is `limited-inputs` with the reason being absence of an `initial_read` row in the audit DB after the wait-barrier timeout.

**Why the primary spawns all 6 (not prism-synthesizer):** `prism-synthesizer` has no Agent/spawn tool in its toolset — it coordinates the analysts via SendMessage only. Giving it spawn responsibility would require adding a general-purpose tool that blurs its role boundary. The primary session is the only session with the full Agent tool surface; it spawns all council members, then hands control to `prism-synthesizer` to coordinate. This keeps each agent's role clean: primary = dispatcher, synthesizer = coordinator, analysts = producers.

## API Reference

### `main() -> None`

Main entry point. Exits 0 on success (row already exists today, or subprocess completed with a confirmed MARKET_PRISM row). Exits 1 if all `MAX_ATTEMPTS` are exhausted without a confirmed row.

**Per-attempt success criterion (F-4):** `_run_prism(run_id)` returns `True` (subprocess `rc==0`) AND `_get_market_prism_row_for_run(run_id)` returns a non-None dict. `rc==0` without a matching MARKET_PRISM row is classified as a failed attempt — the loop prints a diagnostic to stderr and retries. Spend logging (`_persist_spend`) fires on `rc==0` before the row check and is not skipped on an empty-row attempt.

**Behavior:**
1. Loads `.env` from project root via `_load_env()`
2. Generates a fresh `uuid4` `run_id` for this invocation
3. Calls `_get_summary()` → `database.get_latest_market_prism_summary()`
4. If today's row exists (UTC date comparison) → prints skip message and `sys.exit(0)`
5. Otherwise: attempts `_run_prism(run_id)` up to `MAX_ATTEMPTS` times with exponential backoff
6. For each `_run_prism` call that returns `True` (rc==0): calls `_get_market_prism_row_for_run(run_id)`. If the row is present → `sys.exit(0)`. If absent → logs diagnostic to stderr, continues retry loop.
7. On exhaustion of all `MAX_ATTEMPTS` without a confirmed row → `sys.exit(1)`

### `_load_env() -> None`

Loads `.env` from project root using `python-dotenv`. Silent on failure (D-1: logs nothing to avoid echoing secrets).

### `_get_summary() -> dict | None`

Wrapper around `database.get_latest_market_prism_summary()`. Patchable in tests.

### `_is_todays_row(row: dict | None) -> bool`

Returns `True` if `row["created_at"]` (format `"YYYY-MM-DD HH:MM:SS"`, UTC) matches today's UTC date. Returns `False` for `None` or unparseable values.

### `_persist_spend(run_id: str, stdout: str) -> None`

Parses the subprocess JSON stdout for `total_cost_usd` (CC 2.1.181+ envelope) with a tolerant fallback to the legacy `cost_usd` key, and writes a `prism_audit_log` row via `database.insert_prism_audit_entry` with `agent_role="LAUNCHER"` and `phase="spend_log"`. The persisted `content` JSON uses `total_cost_usd` as the key name. Non-fatal — a parse or DB failure is logged as `type(exc).__name__` only (D-1) and swallowed. Called only on `returncode == 0`.

### `_get_market_prism_row_for_run(run_id: str) -> dict | None`

Row-verification seam (F-4). Returns the MARKET_PRISM `advisor_observations` row for this `run_id`, or `None`.

Calls `database.get_latest_market_prism_summary()` and confirms `raw_response["run_id"] == run_id`. Since the scheduler's `run_id` is a unique uuid4, the latest row is this run's row iff it was written by the council. Non-fatal — returns `None` on any DB query failure or JSON parse error; logs `type(exc).__name__` only to stderr (D-1). Never raises.

Patchable in tests as `patch.object(mod, "_get_market_prism_row_for_run", ...)`. Pre-existing happy-path tests supply a `_SAMPLE_MARKET_PRISM_ROW` fixture; `TestMarketPrismRowVerification` tests exercise the `None` path (false-green kill) and the retry-on-empty path.

### `_run_prism(run_id: str = "unknown") -> bool`

Invokes the Market Prism council as a vanilla-primary headless subprocess. Returns `True` on `returncode == 0`, `False` on non-zero or exception.

**Why vanilla `-p` (no `--agent` pin):** A session pinned with `--agent prism-synthesizer` cannot spawn additional agents — the `--agent` flag locks the session to a single agent role and disables the Agent/spawn tool. The Prism council requires the primary session to spawn all 6 agent roles (5 analysts + synthesizer). Using `claude -p` launches a full primary session with the Agent tool available.

**Command built:**
```
claude -p --dangerously-skip-permissions
       --model claude-opus-4-8
       --max-budget-usd 15.0
       --output-format json
       "<PRISM_RUN_PROMPT> + scheduler-generated run_id suffix"
```

The final positional argument is `PRISM_RUN_PROMPT + f" The run_id for this session is: {run_id}. Use this exact string as the run_id for ALL audit rows and the MARKET_PRISM observation. Do not generate a new run_id."` — the run_id is appended at call time by `_run_prism(run_id)`, not embedded statically in the constant.

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

> **Note:** `schedule_prism.ps1` is retained only for test-green purposes. The production nightly trigger will move to droplet cron/systemd when the deployment environment is provisioned.

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

- `database` — `get_latest_market_prism_summary()` (idempotency check + F-4 row verification in `_get_market_prism_row_for_run`) + `insert_prism_audit_entry()` (spend logging); both lazy-imported inside their respective wrappers
- `dotenv` — `.env` loading (lazy import inside `_load_env()`)
- `subprocess` — headless `claude` invocation
- `uuid` — `uuid4()` run_id generation in `main()`
- `schedule_prism.ps1` — companion Task Scheduler registration script (project root)

## Tests

`tests/ai_advisor/test_prism_scheduling.py` — 53 tests (43 pre-F-4 + 10 F-4 additions) covering AC-1 through AC-8, HC-1 (spend cap), HC-2 (spend logging), HC-3 (model pin), the Phase-4 invocation shape (vanilla `-p`, no `--agent` pin, `PRISM_RUN_PROMPT` as positional arg, `MAX_BUDGET_USD=15.0`), the council architecture (primary spawns all 6; `prism-synthesizer` coordinates only via SendMessage), the 5/5 orchestration directives (DE-PRISM-5OF5): run_id generated before spawning, kickoff embedded in analyst spawn prompts, agentIds captured and passed to synthesizer, wait-barrier before synthesis, and **F-4 row-verification** (`TestMarketPrismRowVerification`): (1) rc==0 + no row — all MAX_ATTEMPTS exhausted — non-zero exit (RED gate); (2) rc==0 + row — exit 0 (happy-path regression lock, skips pre-GREEN); (3) rc==0 + no row on attempt 1, rc==0 + row on attempt 2 — subprocess called twice + exit 0 (retry-on-empty RED gate). All tests mock `subprocess.run`, `time.sleep`, `_get_summary()`, and `_get_market_prism_row_for_run()` — no real DB calls, no real subprocess invocations.
