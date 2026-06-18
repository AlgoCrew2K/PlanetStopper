# TDD Handoff — prism-council F-1/F-2/F-3 + F-4 fixes

**From:** pc-test-writer
**To:** pc-implementer
**Branch:** feat/prism-council-5of5
**Worktree:** C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/prism-council
**RED commit:** (pending — F-4 tests written, commit next)
**Prior GREEN commit:** 87ba7ae (F-1/F-2/F-3 GREEN)
**Suite state after F-4 RED:** 2 FAILED / 49 passed / 2 skipped

---

## Your mission: GREEN (minimum changes to pass the 2 RED tests)

You are the implementer. Read ONLY this handoff — not the feature plan or the
test file. Write the minimum production code to make the 2 failing tests pass.

F-1/F-2/F-3 are already GREEN at 87ba7ae — do not undo those changes.
F-4 adds 3 new tests; 2 are RED, 1 skips (happy-path regression lock).

Confirm RED first:

```
cd C:/Windows/Temp && python -m pytest \
  "C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/prism-council/tests/ai_advisor/test_prism_scheduling.py::TestMarketPrismRowVerification" \
  --override-ini="addopts=" -v
```

Expected: 2 FAILED, 0 passed, 1 skipped.

---

## Files to change

### 1. `prism_scheduler.py` — add row-verification seam + retry-on-empty (F-4)

**Root cause:** `main()` treats subprocess `rc==0` as per-attempt success with no
check that the council actually persisted a MARKET_PRISM observation row.  A
council that exits cleanly but writes no row is indistinguishable from a genuine
success (the silent false-green exposed by the live exam).

**Seam name (REQUIRED — pinned by tests):** `_get_market_prism_row_for_run`

The tests patch `prism_scheduler._get_market_prism_row_for_run` by name.  The
function MUST be named exactly `_get_market_prism_row_for_run`.

**What to add:**

```python
def _get_market_prism_row_for_run(run_id: str) -> dict | None:
    """Return the MARKET_PRISM advisor_observations row for this run_id, or None.

    Queries advisor_observations for a row with advisor_role='MARKET_PRISM' whose
    raw_response contains a matching run_id.  Used by main() to verify the council
    actually produced an observation before declaring success.

    Non-fatal — returns None on any DB error (D-1: logs type-only to stderr).
    """
    try:
        sys.path.insert(0, str(_PROJECT_ROOT))
        import database  # noqa: PLC0415
        # get_latest_market_prism_summary returns the most recent MARKET_PRISM row.
        # Since run_id is unique per nightly, the latest row is this run's row if
        # it was written.  Confirm by checking raw_response.run_id matches.
        row = database.get_latest_market_prism_summary()
        if row is None:
            return None
        raw = row.get("raw_response") or {}
        if isinstance(raw, str):
            import json as _json
            raw = _json.loads(raw)
        if raw.get("run_id") == run_id:
            return row
        return None
    except Exception as exc:  # noqa: BLE001
        print(
            f"[prism_scheduler] RowCheckError: {type(exc).__name__}", file=sys.stderr
        )
        return None
```

**What to change in `main()`:**

Replace the existing per-attempt success block:

```python
        success = _run_prism(run_id=run_id)
        if success:
            print("[prism_scheduler] Run completed successfully.")
            sys.exit(0)
```

With a row-verification step:

```python
        proc_ok = _run_prism(run_id=run_id)
        if proc_ok:
            row = _get_market_prism_row_for_run(run_id)
            if row is not None:
                print("[prism_scheduler] Run completed successfully.")
                sys.exit(0)
            # rc==0 but no row — council ran but wrote nothing.
            # Treat as a failed attempt; the retry loop continues.
            print(
                f"[prism_scheduler] Attempt {attempt}: subprocess exited 0 but "
                "no MARKET_PRISM row found for run_id — treating as failed.",
                file=sys.stderr,
            )
```

This is the **retry-on-empty design**: per-attempt success = rc==0 AND row present.
The existing MAX_ATTEMPTS loop handles the retry and the final exit(1) on exhaustion
without any other changes.

**Spend logging preservation:** `_persist_spend` fires on `rc==0` BEFORE the row
check (inside `_run_prism`'s existing call to `_persist_spend`).  Do NOT move or
suppress `_persist_spend` — it is called unconditionally on rc==0, even for
attempts where the row is absent.  The existing tests in `TestPersistSpend` cover
this; do not break them.

---

## Scope boundary

- Touch ONLY: `prism_scheduler.py`
- Do NOT modify any test files
- Do NOT modify `.claude/agents/prism-synthesizer.md` (F-2/F-3 already GREEN)
- Do NOT modify any other production code (app.py, database.py, etc.)
- NEVER merge, push, or checkout main

---

## Verify GREEN

Run the full test file (not just the new class) to confirm all prior tests
still pass:

```
cd C:/Windows/Temp && python -m pytest \
  "C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/prism-council/tests/ai_advisor/test_prism_scheduling.py" \
  --override-ini="addopts=" -q
```

Expected: 0 FAILED, 51 passed (49 + 2 new GREEN + Test 2 now runs), 1 skipped.

Note: Test 2 (`test_scheduler_succeeds_when_subprocess_succeeds_and_market_prism_row_exists`)
skips before the seam exists and runs (passes) after.  The 1 remaining skip is the
original F-3 skip from `TestSynthesizerRoleFileAgentIdAddressing`.

---

## After GREEN: commit and signal pc-test-writer

Branch check (must NOT be main):
```
git -C "C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/prism-council" branch --show-current
```

Stage path-scoped (prism_scheduler.py only — tests committed by pc-test-writer):
```
git -C "C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/prism-council" add \
  prism_scheduler.py
```

Commit prefix: `fix(prism-council):`

Then SendMessage to `pc-test-writer`:
"GREEN: N passed / 0 failed / M skipped on feat/prism-council-5of5 HEAD=<sha>. Ready for R/G/R review."
