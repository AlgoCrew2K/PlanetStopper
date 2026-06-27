# TDD Handoff — DE-PRISM-DIAG-001 (Council Subprocess Diagnostics)

**For:** `council-impl` (the implementer)
**Written by:** `council-test` (quant-test-writer)
**Branch:** `fix/council-subprocess-diagnostics`
**Worktree:** `C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/council-diag`
**RED test file:** `tests/prism_scheduler/test_run_prism_diagnostics.py` (28 failing, 5 passing)

Do NOT read the feature plan — implement only what is in this file.
Do NOT merge, push, or touch any branch other than `fix/council-subprocess-diagnostics`.

---

## Your job

Make the 28 failing tests pass by modifying ONLY `prism_scheduler.py`.
Do NOT touch the test file. Do NOT add new dependencies. Write the minimum code.

---

## What the tests require (behavior contract)

### 1. Two new module-level constants (near the existing `MAX_ATTEMPTS` block)

```python
_STDERR_LOG_CAP: int = 4000   # chars of stderr tail logged on council failure (fits a full traceback, bounded for journald)
_STDOUT_LOG_CAP: int = 2000   # chars of stdout tail logged on council failure (council JSON error payload)
```

Both must be `int`, both must be `> 0`.

### 2. New pure helper: `_redact_secrets(text: str, secret_values: list[str]) -> str`

Contract (tested directly by `TestRedactSecrets`):
- Empty input -> empty output.
- Benign text with no secrets and no token shapes -> returned unchanged.
- Each non-empty value in `secret_values` is replaced globally (all occurrences) with `***REDACTED***`.
- Empty strings in `secret_values` are SKIPPED — do NOT call `str.replace("", ...)`.
- After literal-value replacement, apply these shape-regex patterns (defense-in-depth),
  all replaced with `***REDACTED***`:
  - `sk-ant-[A-Za-z0-9_-]{8,}`  — Anthropic API key shape
  - `sk-[A-Za-z0-9_-]{16,}`     — generic secret-key shape
  - `oat_[A-Za-z0-9_-]{8,}`     — Claude OAuth token shape
- `oat_ABC` (3 chars after `oat_`, below the 8-char floor) must NOT be redacted.
- Pure, no I/O. Should not raise, but the caller guards it anyway (see AC-7).

### 3. Modified `_run_prism` — non-zero-exit diagnostic block

The existing code after `result = subprocess.run(...)` is:

```python
if result.returncode == 0:
    _persist_spend(run_id, result.stdout)
return result.returncode == 0
```

Add a diagnostic block in the failure (`else`) branch, wrapped in `try/except`:

```python
if result.returncode == 0:
    _persist_spend(run_id, result.stdout)
else:
    try:
        # a) Build secret values from the env actually passed to the subprocess
        secret_values = [
            v for v in (
                _council_env.get("CLAUDE_CODE_OAUTH_TOKEN"),
                os.environ.get("ANTHROPIC_API_KEY"),
            )
            if v
        ]
        # b) Redact both output channels
        safe_stderr = _redact_secrets(result.stderr or "", secret_values)
        safe_stdout = _redact_secrets(result.stdout or "", secret_values)
        # c) Tail-truncate with marker
        def _tail(text, cap, label):
            if not text:
                return f"({label} empty)"
            if len(text) <= cap:
                return text
            return f"...[truncated, showing last {cap}]...\n{text[-cap:]}"
        # d) Print the diagnostic block
        print(
            f"[prism_scheduler] Council subprocess failed: returncode={result.returncode}\n"
            f"  stderr: {_tail(safe_stderr, _STDERR_LOG_CAP, 'stderr')}\n"
            f"  stdout: {_tail(safe_stdout, _STDOUT_LOG_CAP, 'stdout')}",
            file=sys.stderr,
        )
    except Exception as exc:  # noqa: BLE001
        # AC-7: diagnostic suppressed — never propagate; return value is preserved
        print(
            f"[prism_scheduler] (diagnostic suppressed: {type(exc).__name__})",
            file=sys.stderr,
        )
return result.returncode == 0
```

The exact formatting is yours — tests check for substrings, not exact format.
What they assert:
- `"[prism_scheduler]"` in captured stderr on non-zero exit.
- `"returncode=2"` (or `"returncode: 2"` or `"2"`) in captured stderr.
- The sentinel content of `result.stderr` appears in captured stderr.
- `"(stderr empty)"` when `result.stderr == ""`.
- `"(stdout empty)"` when `result.stdout == ""`.
- `"truncat"` (case-insensitive) OR `"..."` OR `"…"` when output exceeds cap.
- Tail chars (last 20+ chars of a long output) appear in captured stderr.
- No live credential value in ANY captured output (both `.out` and `.err`).
- `"***REDACTED***"` present when a secret was replaced.
- `"(diagnostic suppressed: RuntimeError)"` in captured stderr when `_redact_secrets` raises.
- `"RuntimeError"` appears in captured stderr in that suppressed case.

### 4. What you must NOT change

- The `if result.returncode == 0:` success branch — zero change. Tests assert `_persist_spend`
  called once on rc=0 and NO `[prism_scheduler]` diagnostic emitted.
- The outer `except Exception` block (catches `subprocess.run` raising) — tests assert it still
  logs ONLY `SubprocessError: {type(exc).__name__}`, no message, no path.
- Any other function in `prism_scheduler.py`.

---

## Security — the adversarial sweep test

`test_no_secret_value_appears_in_any_log_line` plants real values in both env vars,
embeds both in mocked stderr AND stdout, then sweeps ALL of `capsys.readouterr()`
(`.out` + `.err`) asserting neither value appears as a substring.

Critical: pull the OAuth token from `_council_env.get("CLAUDE_CODE_OAUTH_TOKEN")` —
NOT from `os.environ` — because `_council_env` is the dict actually passed to the
subprocess. Both are redacted: the OAuth token from `_council_env`, the API key from
`os.environ.get("ANTHROPIC_API_KEY")`.

---

## How to verify GREEN

In the worktree:
```
set ALPHABOT_TEST_MEM_CAP_GB=24
set DB_PATH=C:/Users/paulm/AppData/Local/Temp/test_diag_state.db
python -m pytest tests/prism_scheduler/test_run_prism_diagnostics.py -n0 --tb=short -q
```

Target: **33 passed, 0 failed**.

Run ruff on `prism_scheduler.py` before committing:
```
python -m ruff format prism_scheduler.py
python -m ruff check prism_scheduler.py
```

Commit path-scoped (NOT `git add -A`):
```
git add prism_scheduler.py
git commit -m "fix(prism_scheduler): capture+log council subprocess stderr/stdout on non-zero exit with credential redaction (DE-PRISM-DIAG-001)"
```

Then `SendMessage` to `council-test`: "GREEN: 33 passed / 0 failed / 0 errors. SHA=<sha>."
