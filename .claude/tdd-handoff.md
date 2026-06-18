# TDD Handoff — p4fix cycle (_persist_spend cost key regression fix)

**Branch:** feat/prism-phase4-scheduling  
**Worktree:** C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/prism-phase4  
**Test file:** tests/ai_advisor/test_prism_scheduling.py  
**RED state:** 2 FAILED / 23 passed (as of this handoff)  
**For:** p4fix-implementer (you are BLIND to the test code — do NOT read the test file)

---

## GREEN Target

Fix ONE function in ONE file.

**File:** `prism_scheduler.py` (project root of the shared worktree)  
**Function:** `_persist_spend(run_id, stdout)`

### What it currently does (broken)

```python
parsed = json.loads(stdout)
cost = parsed.get("cost_usd")   # BUG: real CC envelope uses total_cost_usd
if cost is not None:
    ...  # DB write — never reached on a real CC run
```

### What GREEN looks like

`_persist_spend` must read `total_cost_usd` as the primary key from the parsed JSON.
It MUST also tolerate a legacy `cost_usd`-only envelope (fallback) so old/local CC builds still log.

Minimal correct logic (pseudocode — implement as you see fit):

```python
cost = parsed.get("total_cost_usd") or parsed.get("cost_usd")
```

The persisted `content` JSON must include `total_cost_usd` as the key name
(not `cost_usd`) when the source envelope had `total_cost_usd`.

### Provenance (do not alter this contract)

PM-captured from live `claude -p --output-format json` (CC 2.1.181):
```json
{"total_cost_usd": 0.0728568, "type": "result", "subtype": "...", "usage": {...}, ...}
```
No `"cost_usd"` key is present in the real envelope.

---

## D-1 contract (MUST preserve)

`_persist_spend` is non-fatal. The existing `try/except Exception` that logs
`type(exc).__name__` only to stderr MUST remain intact. Never let a parse or
DB failure propagate.

---

## Scope boundary

- Touch ONLY `prism_scheduler.py`, the `_persist_spend` function.
- Do NOT touch the test file.
- Do NOT change any other function signature or behaviour.
- Do NOT add new constants.
- Stage path-scoped: `git add prism_scheduler.py` only.

---

## Verification

After your change, run:

```
cd C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/prism-phase4
python -m pytest tests/ai_advisor/test_prism_scheduling.py -o addopts= -p no:cacheprovider -q
```

Expected: **25 passed, 0 failed** (or 24 passed + 1 skipped if you implement primary
key only and omit the legacy fallback — see note below). The two RED tests from
this fix cycle must now be GREEN.

### Skip note

`TestPersistSpendEnvelopeKey::test_persist_spend_tolerant_fallback_legacy_cost_usd`
will SKIP (not fail) if your implementation does not handle the legacy `cost_usd`-only
envelope. That is acceptable — the primary RED gate is
`test_persist_spend_writes_row_when_only_total_cost_usd_present`.

---

## Commit instruction

After GREEN:
1. `git -C <worktree> add prism_scheduler.py`
2. Commit with prefix `fix(prism-scheduler):` on branch `feat/prism-phase4-scheduling`
3. Do NOT merge to main. Do NOT push to origin.
4. Quote the SHA + pass/fail count in your SendMessage to p4fix-test-writer.
