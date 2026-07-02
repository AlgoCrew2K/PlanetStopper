# prism_scheduler

> Nightly Market Prism scheduler wrapper — invokes the Market Prism council via a vanilla-primary headless Claude session, triggered by Windows Task Scheduler (Option B, daemon-decoupled).

**Source:** `prism_scheduler.py`
**Last updated:** 2026-07-02 (DE-PRISM-NUMERIC-VERIFY-001: `main()` now shares one patch-time lens-section fetch between `_patch_provenance` and a new post-council numeric verifier — `_fetch_lens_sections`, `_extract_per_lens_digest`, `_run_numeric_verification`; `_patch_provenance` gained an optional `lens_sections=` kwarg, default-`None` behavior byte-identical to before; prior: DE-ADVISOR-LATENCY `_patch_provenance` MARKET_LENS_CACHE write; DE-PRISM-DIAG-001 redacted stderr/stdout logging; DE-PRISM-SOURCES-001 `_patch_provenance` MARKET_PRISM_SOURCES row; DE-PRISM-SUB-AUTH-001 subprocess pops ANTHROPIC_API_KEY)

## Overview

`prism_scheduler.py` is a standalone script registered with Windows Task Scheduler to run the Market Prism nightly pipeline at 03:00 local time (US Central). It is fully decoupled from the Flask daemon — the daemon's `run_scheduler()` 03:00 slot continues to run `advisors/lens_pipeline.py` (the data layer) and is not modified.

The script enforces three guards: an idempotency guard (skips if today's MARKET_PRISM row already exists), bounded retry with exponential backoff (prevents the persistent-429 infinite-loop crash that caused prior PC crashes), and the D-1 error contract (only `type(exc).__name__` is logged — never raw exception messages or paths).

Per-run Opus spend is captured from the `--output-format json` subprocess stdout and persisted to `prism_audit_log` via `_persist_spend()`. A hard per-run budget cap (`MAX_BUDGET_USD`) is passed as `--max-budget-usd` to the subprocess.

Since DE-PRISM-NUMERIC-VERIFY-001, a confirmed successful run also drives a post-council **numeric verifier** (`advisors/prism_numeric_verifier.py`) that fact-checks every number the council declared it cited against the same lens payloads `_patch_provenance` already fetches — see "Numeric Verifier Wiring" below.

## Constants

| Name | Value | Description |
|------|-------|-------------|
| `MAX_ATTEMPTS` | `3` | Maximum subprocess invocations per run |
| `BACKOFF_BASE_SECONDS` | `30` | First retry wait in seconds |
| `BACKOFF_CAP_SECONDS` | `60` | Maximum wait between retries in seconds |
| `MAX_BUDGET_USD` | `15.0` | Per-run spend ceiling passed as `--max-budget-usd`. A full 6-agent Opus council realistically costs $5–10/run; 15.0 is a runaway-prevention ceiling, not a target. |
| `PRISM_RUN_PROMPT` | (see below) | Prompt passed to the vanilla-primary headless session. Encodes the 5/5 council orchestration directives (DE-PRISM-5OF5). |
| `_STDERR_LOG_CAP` | `4000` | Chars of `stderr` tail captured and logged on non-zero subprocess exit. Sized to hold a full Python traceback; bounded to avoid flooding journald. |
| `_STDOUT_LOG_CAP` | `2000` | Chars of `stdout` tail captured and logged on non-zero subprocess exit. Captures the council JSON error payload. |
| `_CREDENTIAL_KEY_MARKERS` | `("SECRET","KEY","TOKEN","WEBHOOK","PASSWORD","URI")` | Key-name substrings used to identify credential env vars for redaction. Sweeps all of `_council_env` so secrets beyond the two Claude-specific keys (e.g. `COMPOSER_SECRET`, `DISCORD_WEBHOOK_URL`) are also redacted. |
| `_MIN_SWEEP_SECRET_LEN` | `8` | Minimum value length for the marker-keyed sweep. Values shorter than 8 chars (e.g. `LOG_LEVEL_KEY=info`) are excluded to prevent over-redaction that corrupts the logged output. |

### `PRISM_RUN_PROMPT`

The full prompt passed as the final positional argument to `claude -p`. It encodes four orchestration directives that ensure reliable 5/5 analyst participation (DE-PRISM-5OF5):

**(a) Scheduler-generated run_id threaded into the prompt at call time.** `main()` generates `run_id = str(uuid.uuid4())` and passes it to `_run_prism(run_id)`. `_run_prism` builds the final prompt by appending `" The run_id for this session is: {run_id}. Use this exact string as the run_id for ALL audit rows and the MARKET_PRISM observation. Do not generate a new run_id."` to the static `PRISM_RUN_PROMPT` preamble. The council uses exactly this run_id — it does NOT mint its own. This is the single authoritative join key shared by all analyst `initial_read` audit entries, the synthesizer audit entries, the MARKET_PRISM observation `raw_response`, and the LAUNCHER spend_log row written by `_persist_spend`.

**(b) Embed kickoff in each analyst's spawn prompt.** Each of the 5 analyst agents is spawned with the run_id and an explicit instruction to produce and file their `initial_read` immediately on their first turn. This eliminates the dormancy window: the prior pattern (spawn first, then send a kickoff via SendMessage) caused 2/5 participation when dormant agents missed the subsequent message (root cause: transient by-canonical-name resume failure of dormant subagents, independently falsified — see DE-PRISM-COUNCIL).

**(c) Capture agentIds at spawn; pass to synthesizer.** The primary captures each analyst's agentId at spawn time and passes the full list to `prism-synthesizer` in its spawn prompt. The synthesizer uses agentIds — not canonical names — for all Q&A and debate coordination. By-canonical-name addressing of dormant/resumed subagents is unreliable.

**(d) Wait-barrier directive for synthesizer (Hard Rule).** The synthesizer is instructed not to synthesize until all 5 `initial_read` rows are present in the audit DB for this specific `run_id` (queried directly via `database.get_prism_audit_for_run(run_id)`) or the barrier times out with honest `limited-inputs` degradation naming each missing lens. Inbox-only collection is rejected as the wait mechanism: the audit DB is the authoritative source of truth.

**(e) False-attribution prohibition (Hard Rule).** A spawned analyst that did not file its `initial_read` is missing or late — not absent. `prism-synthesizer` must never record it as "did not spawn". The correct attribution is `limited-inputs` with the reason being absence of an `initial_read` row in the audit DB after the wait-barrier timeout.

**Why the primary spawns all 6 (not prism-synthesizer):** `prism-synthesizer` has no Agent/spawn tool in its toolset — it coordinates the analysts via SendMessage only. Giving it spawn responsibility would require adding a general-purpose tool that blurs its role boundary. The primary session is the only session with the full Agent tool surface; it spawns all council members, then hands control to `prism-synthesizer` to coordinate. This keeps each agent's role clean: primary = dispatcher, synthesizer = coordinator, analysts = producers.

**Council contract (DE-PRISM-NUMERIC-VERIFY-001, AC-2):** the synthesizer's raw_response block and all 5 analyst role files (`.claude/agents/prism-*.md`) additionally instruct that every numeric indicator stated in prose must also be reported as a `{indicator, value, lens}` tuple, collected into the `MARKET_PRISM` row's `cited_numbers` array. This is what makes the numeric verifier's checks possible — see [advisors/prism_numeric_verifier](advisors_prism_numeric_verifier.md).

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
6. For each `_run_prism` call that returns `True` (rc==0): calls `_get_market_prism_row_for_run(run_id)`. If the row is present:
   - Calls `_extract_per_lens_digest(row)` to check the row is real council output (not a degraded/placeholder row).
   - If a valid digest is present, calls `_fetch_lens_sections()` **once** — the single shared patch-time fetch (AC-4) — else `lens_sections = None`.
   - Calls `_patch_provenance(run_id, row, lens_sections=lens_sections)` (D-1 never-raises; does not gate `sys.exit(0)`).
   - If `lens_sections` is truthy, calls `_run_numeric_verification(run_id, row, lens_sections)` (AC-8: D-1 never-raises; does not gate `sys.exit(0)`).
   - `sys.exit(0)`.

   If the row is absent → logs diagnostic to stderr, continues retry loop.
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

### `_patch_provenance(run_id: str, row: dict | None, lens_sections: dict | None = None) -> bool`

Post-council citation and lens-cache builder (DE-PRISM-SOURCES-001 v2 + DE-ADVISOR-LATENCY + DE-PRISM-NUMERIC-VERIFY-001). Called by `main()` after F-4 row-verification confirms the MARKET_PRISM row exists. Performs two additive writes:

**1. MARKET_PRISM_SOURCES row (existing — DE-PRISM-SOURCES-001):**
Re-fetches live lens data via `ai_advisor._build_*_section()` builders at patch time (minutes after the council exits) for the url-bearing lenses (`sentiment`, `macro`, `derivatives`, `fundamentals`), collects `{url, title, published}` dicts from `section["sources"]` and `section["article_corpus"]`, deduplicates by url (first occurrence wins), and inserts an append-only `advisor_observations` row with `advisor_role="MARKET_PRISM_SOURCES"`.

**2. MARKET_LENS_CACHE row (added DE-ADVISOR-LATENCY, `prism_scheduler.py:443-474`):**
As each builder runs in the `_BUILDERS` loop, its structured output (or an `_unavailable_block` on build failure or missing lens) is captured into `_lens_cache_sections[lens]`. After the SOURCES row is written, a separate nested `try/except` block fetches `ai_advisor._build_technicals_section()` (technicals is excluded from `_BUILDERS` — Alpaca bar data has no public URLs for SOURCES — but IS needed in the lens cache for the advisor's MA posture / breadth / momentum context), sets `_lens_cache_sections["technicals"]`, and calls `ai_advisor.persist_market_lens_cache(_lens_cache_sections)`. **Council-safety isolation:** any failure in this inner block is caught and printed to stderr as `[prism_scheduler] LensCacheError: {type}` — it never propagates to the outer `try/except`, so a cache-write failure can never prevent the `return True` that records the council run as successful.

**`lens_sections` parameter (added DE-PRISM-NUMERIC-VERIFY-001, AC-4):** optional pre-fetched `{lens: section}` bundle from the caller — `main()`'s single shared patch-time fetch (`_fetch_lens_sections()`), also reused by the numeric verifier. For each lens in the `_BUILDERS` loop (and separately for `technicals`), if the lens key is present in `lens_sections`, that payload is reused verbatim and the corresponding live builder is **not** re-invoked. When `lens_sections is None` — the default, and the shape of every call site that existed prior to this feature — behavior is byte-identical to before this parameter existed: each builder is called internally. A dedicated equivalence test (`tests/prism_scheduler/test_patch_provenance_lens_sections_equivalence.py`) asserts the SOURCES row is the same whether `_patch_provenance` fetches its own sections or reuses a caller-supplied bundle.

**Key properties common to both writes:**

- **technicals excluded from SOURCES, included in MARKET_LENS_CACHE:** Alpaca bar data has no public URLs, so no `article_corpus` for the technicals lens appears in the SOURCES row. The lens cache includes technicals because the advisor prompt needs the structured MA posture / breadth / momentum payload.
- **Ordering guarantee:** SOURCES row is written before the MARKET_LENS_CACHE row. A crash between the two leaves SOURCES intact but no MARKET_LENS_CACHE row — the advisor cold-starts gracefully.
- **D-1 / never-raises (AC-4):** A failed patch is logged as `type(exc).__name__` only (outer `except`) or as `[prism_scheduler] LensCacheError: {type}` (inner `except`). Does not gate or prevent `sys.exit(0)` — the council run is unaffected.
- **One SOURCES row per run:** keyed by `run_id`. The corresponding `get_latest_market_prism_sources_for_run(run_id)` accessor uses an exact `json_extract` match, preventing stale-citation bleed from a different run's row.
- **AC-6 idempotency guard (SOURCES only):** checks for an existing SOURCES row for this `run_id` before inserting. The MARKET_LENS_CACHE write is append-only; latest row wins on serve.

**Provenance note (SOURCES):** The `article_corpus` entries are rebuilt at patch time from current live data — NOT a guaranteed snapshot of the exact articles the council analyzed. `macro`, `fundamentals`, and `derivatives` source URLs are stable across the patch window; `sentiment` artlist top-N may drift slightly. UI copy must not imply "the exact articles the council read." See DE-PRISM-SOURCES-001 §Provenance honesty in `DECISIONS.md` for the full stability breakdown.

## Numeric Verifier Wiring (DE-PRISM-NUMERIC-VERIFY-001)

### `_extract_per_lens_digest(row: dict | None) -> dict | None`

Returns `row["raw_response"]["per_lens_digest"]` iff it is a `dict`, else `None` (handles `raw_response` arriving as a JSON string too). Used by `main()` as a cheap "is this real council output?" guard before triggering the shared live lens fetch — mirrors `_patch_provenance`'s own early-exit check, so a degraded/placeholder row (e.g. a bare test fixture) never causes a live builder fetch.

### `_fetch_lens_sections() -> dict`

The single shared "patch-time fetch" (AC-4). Calls all 5 `ai_advisor._build_*_section()` builders (`sentiment`, `macro`, `derivatives`, `technicals`, `fundamentals`) once and returns `{lens: section}`. Per-lens exception isolation: one builder raising degrades only that lens to an unavailable block (`{"lens": ..., "available": False, "reason": type(exc).__name__, "payload": None, "sources": []}`) — it never aborts the other 4 fetches.

`main()` calls this once per successful run and threads the same result into **both** `_patch_provenance` (SOURCES + MARKET_LENS_CACHE) and `_run_numeric_verification` (MARKET_PRISM_VERIFICATION) — one live fetch feeds both downstream writes, instead of each doing its own independent re-fetch.

### `_run_numeric_verification(run_id: str, row: dict, lens_sections: dict) -> None`

Lazy-imports `advisors.prism_numeric_verifier` (CC-2), calls `verify_cited_numbers(run_id, row, lens_sections=lens_sections)`, then `persist_verification(run_id, result)`. See [advisors/prism_numeric_verifier](advisors_prism_numeric_verifier.md) for the full classification and persistence contract.

**Never gates `sys.exit(0)` (AC-8):** the entire call is wrapped in `try/except Exception`; any failure logs `[prism_scheduler] NumericVerifierError: {type(exc).__name__}` to stderr and returns — by the time this runs, the council's own MARKET_PRISM row is already written and F-4-confirmed, so a verifier failure can never fail the nightly run.

### `_redact_secrets(text: str, secret_values: list[str]) -> str`

Pure helper (DE-PRISM-DIAG-001). Replaces known credential values and token-shaped patterns with `***REDACTED***`. No I/O. Never raises -- the caller (`_run_prism`) wraps it in `try/except`.

**Two-phase redaction:**
1. **Literal-value replace**: for each non-empty string in `secret_values`, replaces all occurrences globally. Empty strings are skipped (`str.replace("", ...)` would insert a marker between every character).
2. **Shape-regex patterns** (defense-in-depth for secrets not in `secret_values`):
   - `sk-ant-[A-Za-z0-9_-]{8,}` -- Anthropic API key shape
   - `sk-[A-Za-z0-9_-]{16,}` -- generic secret-key shape
   - `oat_[A-Za-z0-9_-]{8,}` -- Claude OAuth token shape

Redaction is applied **before** truncation in `_run_prism` so no secret escapes via a truncation boundary.

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
- `env=_council_env` where `_council_env = os.environ.copy()` then `_council_env.pop("ANTHROPIC_API_KEY", None)` — passes all env vars except the metered API key so `claude -p` falls back to `CLAUDE_CODE_OAUTH_TOKEN` (subscription billing); see DE-PRISM-SUB-AUTH-001
- `capture_output=True, text=True` -- captures stdout/stderr for spend logging and failure diagnostics
- `shell=False` -- no shell injection risk

**On success (`returncode == 0`):** passes `result.stdout` to `_persist_spend(run_id, ...)` for spend logging. Returns `True`. No diagnostic log emitted.

**On non-zero exit (DE-PRISM-DIAG-001):** executes a diagnostic block wrapped in `try/except` (D-1: block failure never propagates). Builds `secret_values` by sweeping `_council_env` for keys matching `_CREDENTIAL_KEY_MARKERS` with values `>= _MIN_SWEEP_SECRET_LEN` chars, plus `ANTHROPIC_API_KEY` explicitly. Redacts both `result.stderr` and `result.stdout` via `_redact_secrets`. Tail-truncates to `_STDERR_LOG_CAP` / `_STDOUT_LOG_CAP`. Prints a single `[prism_scheduler] Council subprocess failed: returncode=N` block to `sys.stderr` (journald). On internal diagnostic error, logs `(diagnostic suppressed: {ExcType})` and continues. Returns `False`.

**On `subprocess.run` exception:** logs `SubprocessError: {type(exc).__name__}` type-only (D-1 -- no message, no path) and returns `False`. No stdout/stderr exists on this path.

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

All error paths surface `type(exc).__name__` only — no raw exception messages, file paths, or tracebacks are logged or propagated. This applies to `.env` load failures, DB query failures, subprocess exceptions, spend-log parse/write failures, the MARKET_LENS_CACHE write inside `_patch_provenance`, and the numeric verifier call inside `_run_numeric_verification`.

## Internal Dependencies

- `database` — `get_latest_market_prism_summary()` (idempotency check + F-4 row verification in `_get_market_prism_row_for_run`) + `insert_prism_audit_entry()` (spend logging) + `insert_advisor_observation()` (MARKET_PRISM_SOURCES row + MARKET_LENS_CACHE row written by `_patch_provenance`); all lazy-imported inside their respective wrappers
- `ai_advisor` — `_build_*_section()` builders (called by `_fetch_lens_sections()` for the shared patch-time fetch, and internally by `_patch_provenance` when no `lens_sections` is supplied); `persist_market_lens_cache()` (called to write the MARKET_LENS_CACHE row — DE-ADVISOR-LATENCY)
- `advisors.prism_numeric_verifier` — `verify_cited_numbers()` + `persist_verification()`, lazy-imported inside `_run_numeric_verification` (CC-2, DE-PRISM-NUMERIC-VERIFY-001)
- `dotenv` — `.env` loading (lazy import inside `_load_env()`)
- `subprocess` — headless `claude` invocation
- `uuid` — `uuid4()` run_id generation in `main()`
- `schedule_prism.ps1` — companion Task Scheduler registration script (project root)

## Tests

`tests/ai_advisor/test_prism_scheduling.py` — 53 tests (43 pre-F-4 + 10 F-4 additions) covering AC-1 through AC-8, HC-1 (spend cap), HC-2 (spend logging), HC-3 (model pin), the Phase-4 invocation shape (vanilla `-p`, no `--agent` pin, `PRISM_RUN_PROMPT` as positional arg, `MAX_BUDGET_USD=15.0`), the council architecture (primary spawns all 6; `prism-synthesizer` coordinates only via SendMessage), the 5/5 orchestration directives (DE-PRISM-5OF5): run_id generated before spawning, kickoff embedded in analyst spawn prompts, agentIds captured and passed to synthesizer, wait-barrier before synthesis, and **F-4 row-verification** (`TestMarketPrismRowVerification`): (1) rc==0 + no row — all MAX_ATTEMPTS exhausted — non-zero exit (RED gate); (2) rc==0 + row — exit 0 (happy-path regression lock, skips pre-GREEN); (3) rc==0 + no row on attempt 1, rc==0 + row on attempt 2 — subprocess called twice + exit 0 (retry-on-empty RED gate). All tests mock `subprocess.run`, `time.sleep`, `_get_summary()`, and `_get_market_prism_row_for_run()` — no real DB calls, no real subprocess invocations.

`tests/prism_scheduler/test_council_sub_auth.py` — 3 tests (DE-PRISM-SUB-AUTH-001): AC-1 (`ANTHROPIC_API_KEY` excluded from subprocess env), AC-2 (`CLAUDE_CODE_OAUTH_TOKEN` passes through unchanged), AC-3 (all other env vars including `DB_PATH` preserved — surgical removal, not an allowlist). Tests use `monkeypatch.setenv` and patch `prism_scheduler.subprocess.run` to inspect the `env` kwarg.

`tests/prism_scheduler/test_run_prism_diagnostics.py` — hermetic test module (DE-PRISM-DIAG-001); all tests mock `subprocess.run` and capture output via `capsys` — no real `claude -p`, no network, no LLM spend. Covers: non-zero exit logs returncode + stderr/stdout tails to `sys.stderr` (AC-1/AC-2/AC-3); empty stderr/stdout logs explicit `(empty)` markers; truncation marker present when output exceeds cap; `_redact_secrets` replaces live OAuth token, `ANTHROPIC_API_KEY` value, and `sk-ant-`/`sk-`/`oat_` token-shaped patterns with `***REDACTED***`; `_CREDENTIAL_KEY_MARKERS` sweep redacts `COMPOSER_SECRET`/`ALPACA_SECRET_KEY`/`DISCORD_WEBHOOK_URL` (reviewer Finding 1); values shorter than `_MIN_SWEEP_SECRET_LEN=8` are NOT swept (over-redaction guard, reviewer Finding 2); success path (`rc==0`) emits no diagnostic log and calls `_persist_spend` unchanged; `subprocess.run` raise path logs type-only `SubprocessError`; monkeypatching `_redact_secrets` to raise causes `_run_prism` to log `(diagnostic suppressed: ...)` without raising (AC-7); `_redact_secrets` direct unit tests (empty input, no secrets, value at start/middle/end, multiple occurrences, overlapping patterns).

`tests/prism_scheduler/test_verifier_wiring.py` — 4 tests (DE-PRISM-NUMERIC-VERIFY-001, AC-8): `main()` calls `verify_cited_numbers`/`persist_verification` after `_patch_provenance`; the shared `lens_sections` are passed through so the 5 builders are invoked exactly once (AC-4 — no double fetch); a verifier exception does not change `sys.exit(0)`; the verifier is not invoked when no MARKET_PRISM row was found for the run.

`tests/prism_scheduler/test_patch_provenance_lens_sections_equivalence.py` — 1 test: `_patch_provenance`'s MARKET_PRISM_SOURCES row is byte-equivalent whether it performs its own live fetch or reuses a caller-supplied `lens_sections` bundle (proves the `lens_sections=None` default path is behavior-preserving).
