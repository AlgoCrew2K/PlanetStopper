# TDD Handoff — Market Prism Phase 4 Hardening (Option B gap closure)

**Branch:** feat/prism-phase4-scheduling
**Worktree:** C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/prism-phase4
**Test file:** tests/ai_advisor/test_prism_scheduling.py
**RED state:** 18 passed / 5 FAILED (as of this handoff)
**Implementer:** p4-impl-launcher → prism_scheduler.py

---

## Context

Option B is adopted. prism_scheduler.py (at project root) is the standalone
nightly scheduler invoked by Windows Task Scheduler. The 18 existing tests
pass. Three gaps remain unaddressed (HC-1, HC-2, HC-3).

## IMPLEMENTER SECTION — p4-impl-launcher

**File to modify:** `prism_scheduler.py` (project root — NOT under advisors/)

### HC-1: Spend cap — add `MAX_BUDGET_USD` constant + `--max-budget-usd` flag

1. Add a named constant at the top of the module (with the other constants):
   ```python
   MAX_BUDGET_USD: float = 5.0   # Per-run Opus spend cap — adjust as needed
   ```

2. In `_run_prism()`, add `"--max-budget-usd", str(MAX_BUDGET_USD)` to the `cmd` list.

The test `test_claude_command_includes_max_budget_usd_flag` asserts:
- `"--max-budget-usd"` is in the args list
- The value after it is numeric AND equals `float(MAX_BUDGET_USD)` (not a magic number)

### HC-2: Spend logging — `--output-format json` + persist spend to prism_audit_log

1. Add `"--output-format", "json"` to the `cmd` list in `_run_prism()`.

2. Capture `stdout` from the subprocess call:
   - Add `capture_output=True, text=True` to `subprocess.run(...)`.
   - After a successful run (returncode == 0), parse `result.stdout` as JSON and
     extract the cost. Persist it via `database.insert_prism_audit_entry`.

3. The spend log entry must use:
   - `agent_role="LAUNCHER"` (or similar — whatever identifies the scheduler)
   - `phase="spend_log"` (exact string — the test queries for this)
   - `content=json.dumps({"cost_usd": <parsed_value>})`
   - `run_id` — use a stable run ID (e.g. today's UTC ISO date string, or
     generate a UUID at the start of `main()` and thread it through to `_run_prism`)

4. `database` import: add `sys.path.insert(0, str(_PROJECT_ROOT))` then
   `import database` inside the persistence helper (the pattern already used
   by `_get_summary()`). Or import at the top of `_run_prism` after the path insert.

5. D-1: if stdout parsing fails (malformed JSON, missing key), catch the exception
   and log only `type(exc).__name__` — never the raw message. This is non-fatal;
   the run already succeeded.

The test `test_successful_run_persists_spend_log_audit_entry`:
- Mocks `subprocess.run` to return `returncode=0` with `stdout='{"cost_usd": 1.23}'`
- Queries `prism_audit_log` for rows with `phase='spend_log'`
- Asserts a row exists with a positive float cost value under `cost_usd`/`cost`/`spend_usd`

### HC-3: Model pin — `claude-opus-4-8` not `opus`

In `_run_prism()`, change:
```python
"--model", "opus",
```
to:
```python
"--model", "claude-opus-4-8",
```

The test `test_claude_command_uses_pinned_model_not_alias` asserts:
- `"claude-opus-4-8"` is in the args list
- The value after `"--model"` is NOT the bare alias `"opus"`

### Note on existing tests

The existing `EXPECTED_CLAUDE_ARGS` at the top of the test file still includes
`"opus"` as the expected model value. After your HC-3 fix, `test_no_row_invokes_claude_subprocess`
will FAIL because `"opus"` is no longer in the command. You must ALSO update
`EXPECTED_CLAUDE_ARGS` to replace `"opus"` with `"claude-opus-4-8"`.

```python
# Before (in test file — you update this):
EXPECTED_CLAUDE_ARGS = [
    "claude",
    "-p",
    "--agent",
    "prism-synthesizer",
    "--dangerously-skip-permissions",
    "--model",
    "opus",          # <-- change this
]

# After:
EXPECTED_CLAUDE_ARGS = [
    "claude",
    "-p",
    "--agent",
    "prism-synthesizer",
    "--dangerously-skip-permissions",
    "--model",
    "claude-opus-4-8",   # <-- pinned
]
```

This is a legitimate test update — the test was asserting the wrong (pre-fix) value.
The test-writer (p4-test-writer) authorises this update as part of HC-3 GREEN.

---

## Verification

After all changes:

```
python -m pytest tests/ai_advisor/test_prism_scheduling.py -p no:xdist --override-ini="addopts=" -q
```

Expected: **23 passed / 0 failed / 0 errors** (18 existing + 5 new).

Quote the HEAD SHA. Then SendMessage `p4-test-writer`: `GREEN: <SHA> — 23/23 passed`

Do NOT merge to main. Do NOT push. PM gates with full-tree verifier + /review + live run.
