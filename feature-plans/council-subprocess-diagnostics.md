# Feature: Council Subprocess Failure Diagnostics
Status: ready
Created: 2026-06-27

## Summary
The nightly Market Prism council runs as a headless `claude -p` subprocess launched by `prism_scheduler._run_prism()`. On a non-zero exit, `_run_prism` currently returns `False` and **silently discards** the captured `result.stderr` and `result.stdout` — only `main()` logs a generic `Attempt N failed (SubprocessError)`. This blind spot is why the unattended systemd nightly failures on 2026-06-25 and 2026-06-26 (fast ~3s non-zero exits) could never be root-caused, despite the OAuth token being valid, the model accessible, basic `claude -p` working, and single-subagent spawning working under the systemd env. This feature hardens `_run_prism` to **capture and log the subprocess stderr + stdout (tail-truncated, with credential redaction)** on non-zero exit, so the next real failure is diagnosable — while preserving the D-1 never-raises contract, the credential-safety guarantee that motivated the original type-only logging, and the byte-for-byte success path. It is diagnosability only; it deliberately does **not** attempt to fix the still-unconfirmed root cause of the 06-25/06-26 failures (the next instrumented run will reveal it).

## Acceptance Criteria
- [ ] AC-1: When the council subprocess exits with `returncode != 0`, `_run_prism` logs a single diagnostic block to `sys.stderr` prefixed `[prism_scheduler]` that includes the integer `returncode`, BEFORE returning `False`.
- [ ] AC-2: The diagnostic block logs the **tail** of the captured `stderr` (most recent `_STDERR_LOG_CAP` chars; tail because tracebacks/errors land at the end). If `stderr` is empty, it logs an explicit `(stderr empty)` marker rather than nothing.
- [ ] AC-3: The diagnostic block logs the **tail** of the captured `stdout` (most recent `_STDOUT_LOG_CAP` chars; the council emits JSON whose error payload may carry the cause). If `stdout` is empty, logs `(stdout empty)`.
- [ ] AC-4: **Credential redaction (mandatory).** Before anything is logged, every occurrence of the live `CLAUDE_CODE_OAUTH_TOKEN` value and the `ANTHROPIC_API_KEY` value (as present in the env actually passed to the subprocess) is replaced with `***REDACTED***`. Additionally, token-shaped substrings matching `sk-ant-[A-Za-z0-9_-]{8,}`, `sk-[A-Za-z0-9_-]{16,}`, and `oat_[A-Za-z0-9_-]{8,}` are redacted, so a secret never reaches the logs even if it appears in subprocess output. Redaction applies to BOTH the stderr and stdout that get logged.
- [ ] AC-5: On `returncode == 0` (success) the behavior is **byte-for-byte unchanged**: still calls `_persist_spend(run_id, result.stdout)`, returns `True`, emits no new diagnostic log.
- [ ] AC-6: The `except Exception` path (where `subprocess.run` itself raises — e.g. `FileNotFoundError`, `OSError`, no stdout/stderr exist) is **unchanged**: still logs `SubprocessError: {type(exc).__name__}` type-only (no message, no path) per D-1, returns `False`.
- [ ] AC-7: `_run_prism` still **never raises** (D-1). The new logging + redaction code is itself wrapped so that a failure inside redaction/formatting can never propagate — on internal error it falls back to logging `(diagnostic suppressed: <ExcType>)` and still returns `result.returncode == 0`.
- [ ] AC-8: All truncation caps are named module-level constants (`_STDERR_LOG_CAP`, `_STDOUT_LOG_CAP`) with a source comment — no magic numbers.

## Architecture
Single file: `prism_scheduler.py`.

- **New module constants** (near the existing `MAX_ATTEMPTS` / `MAX_BUDGET_USD`):
  - `_STDERR_LOG_CAP: int = 4000` — chars of stderr tail to log on failure (enough for a Python/CLI traceback; bounded so journald isn't flooded).
  - `_STDOUT_LOG_CAP: int = 2000` — chars of stdout tail (the council JSON error payload).
- **New pure helper** `_redact_secrets(text: str, secret_values: list[str]) -> str`:
  - Replaces each non-empty value in `secret_values` (the live token values) with `***REDACTED***` (literal substring replace).
  - Then applies the token-shape regexes (AC-4) for defense-in-depth.
  - Pure, no I/O, deterministic, never raises (caller still guards).
- **Modified `_run_prism`** (the `else` of `if result.returncode == 0:`):
  - Build `secret_values = [v for v in (_council_env.get("CLAUDE_CODE_OAUTH_TOKEN"), os.environ.get("ANTHROPIC_API_KEY")) if v]` (the OAuth token actually used + the API key that was stripped — both are sensitive if they leak).
  - Compose the diagnostic: returncode + redacted stderr tail + redacted stdout tail (empty markers per AC-2/AC-3).
  - `print(..., file=sys.stderr)` with the `[prism_scheduler]` prefix.
  - Wrap this whole block in `try/except Exception` → on failure log `(diagnostic suppressed: {type})` (AC-7).
  - Then `return result.returncode == 0` (unchanged).
- The success branch and the outer `except Exception` branch are untouched (AC-5, AC-6).

No new dependencies. Off-execution-path (council infra, not the live trading engine). No DB, no schema, no network, no UI.

## Design-System Mapping
N/A — backend infrastructure change; no UI, no components, no rendered surface.

## Edge Cases
- **Empty stderr / empty stdout on failure** → explicit `(stderr empty)` / `(stdout empty)` markers (the absence is itself diagnostic — e.g. a silent OOM).
- **Output shorter than the cap** → logged in full (tail of a short string is the whole string).
- **Output longer than the cap** → tail-truncated; a leading `…[truncated, showing last N]…` marker so the reader knows it was cut.
- **Secret appears mid-token / multiple times in output** → literal replace is global (all occurrences); regexes catch any token-shaped leftover.
- **`CLAUDE_CODE_OAUTH_TOKEN` / `ANTHROPIC_API_KEY` absent from env** → `secret_values` filters out `None`/empty; redaction still runs the shape regexes.
- **Redaction or formatting itself throws** (e.g. a pathological regex backtrack) → AC-7 guard logs `(diagnostic suppressed: …)`, never raises, return value preserved.
- **Non-UTF-8 bytes in output** → `subprocess.run(text=True)` already decodes with the default codec; redaction operates on `str`. (No bytes handling needed.)
- **returncode == 0 but row missing** (the F-4 path in `main()`) → out of scope; `main()` already logs that case distinctly.

## Security Considerations
- **PRIMARY RISK — credential leakage into logs.** The original type-only D-1 logging existed precisely to keep secrets out of logs. This feature deliberately logs subprocess output, so **redaction is the load-bearing security control** and is non-negotiable (AC-4). journald on the droplet is root-readable; even so, no token may land there. Redaction covers (a) the exact live token values and (b) token-shaped patterns as defense-in-depth.
- **Input validation / injection:** none — output is logged, never executed or interpolated into a shell/DB/query. No user input surface (off-execution-path, advisory infra, no route).
- **Authorization / access control:** unchanged — `_run_prism` is invoked only by the scheduler/systemd; no new entry point.
- **Data exposure via error messages:** the council stdout/stderr could contain operational detail (model ids, file paths) — acceptable for an internal ops log; only *secrets* are redacted.
- **Abuse / rate limiting:** N/A — not callable by users.

## Testing Strategy
New test module: `tests/prism_scheduler/test_run_prism_diagnostics.py` (follows the existing `tests/prism_scheduler/` convention; `DB_PATH` isolated via conftest). All tests **mock `subprocess.run`** — no real `claude -p`, no network, no LLM spend, fully hermetic. Capture logs via `capsys`.

Unit tests:
- `test_nonzero_exit_logs_stderr_with_returncode` — rc=2, stderr="Traceback…boom" → captured stderr text + `returncode=2` + `[prism_scheduler]` prefix present in `capsys.err`; returns `False`.
- `test_nonzero_exit_logs_stdout_tail` — rc=1, long stdout JSON error → tail present, truncation marker present when over cap.
- `test_empty_stderr_logs_marker` — rc=1, stderr="" → `(stderr empty)` logged.
- `describe('security')` group:
  - `test_redacts_live_oauth_token` — env OAuth token value embedded in stderr → token value NOT in output, `***REDACTED***` present.
  - `test_redacts_api_key_value` — `ANTHROPIC_API_KEY` value embedded in stdout → redacted.
  - `test_redacts_sk_ant_pattern` — stderr contains `sk-ant-api03-AAAA....` → redacted even though it's not the live value.
  - `test_redacts_oat_and_sk_patterns` — `oat_…` / `sk-…` shapes redacted.
  - `test_no_secret_in_any_log_line` — sweep all captured output, assert none of the secret values appear as substrings.
- `test_success_path_unchanged` — rc=0 → `_persist_spend` called once (mock), returns `True`, **no** `[prism_scheduler]` diagnostic line emitted.
- `test_exception_path_type_only` — `subprocess.run` raises `FileNotFoundError("…/claude…")` → output is exactly `SubprocessError: FileNotFoundError` (no message, no path), returns `False`.
- `test_never_raises_on_redaction_error` — monkeypatch `_redact_secrets` to raise → `_run_prism` still returns `False`, logs `(diagnostic suppressed: …)`, no exception escapes.
- `_redact_secrets` direct unit tests: empty input, no secrets, secret at start/middle/end, multiple occurrences, overlapping patterns, value shorter than pattern minimums.

No design-system tests (backend). No e2e/browser tests (no rendered surface). Behavioral/functional verification is the PM live step: after deploy, the next real council run's failure (if any) shows redacted diagnostics in `journalctl -u prism-council.service` — verified read-only on the droplet.

## Decisions
| Decision | Rationale |
|----------|-----------|
| Log to `sys.stderr` (not a new logger) | Matches existing `_run_prism` logging; journald captures stderr for the systemd unit. Minimal surface. |
| Redact rather than omit subprocess output | Diagnosability needs the output; security needs no secrets. Redaction satisfies both; pure omission (status quo) is what blocked root-causing. |
| Tail-truncate (not head) | Tracebacks / error payloads appear at the END of CLI output. |
| Redact live values AND token-shape regexes | Defense-in-depth: the exact value catch is precise; the regexes catch any other secret-shaped substring the subprocess might emit. |
| Diagnosability ONLY; no root-cause fix in this cycle | The 06-25/06-26 cause is unconfirmed (manual repros didn't reproduce the fast-fail). Instrument first, then the next real failure reveals the cause for a targeted follow-up. Avoids speculative changes (schedule stagger, resource tuning) without evidence. |
| Keep the `except` path type-only | On a `subprocess.run` raise there is no stdout/stderr to log; the existing D-1 type-only line is correct there. |

## Scope Boundaries
- **IN**: `prism_scheduler._run_prism` non-zero-exit logging; the `_redact_secrets` helper; the two named caps; the hermetic test module. All in `prism_scheduler.py` + `tests/prism_scheduler/`.
- **OUT**: the actual root-cause fix for the 06-25/06-26 failures (unknown until this instrumentation reveals it — tracked as a follow-up); any council schedule change (e.g. moving off the 07:00 UTC daemon-cycle collision); retry/backoff tuning; changes to the council prompt or the prism-* agents; the F-4 `rc==0-but-no-row` path (already logged distinctly in `main()`); enabling/changing `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` or any env/systemd-unit change.
