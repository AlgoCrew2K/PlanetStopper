# TDD Handoff — symphony-schema required-fields fix (RED → GREEN)

**From:** sf-test-writer
**To:** sf-implementer
**Branch:** feat/symphony-schema-required-fields
**Worktree:** C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/symphony-fields
**Plan:** feature-plans/symphony-schema-required-fields.md (AC-1..AC-6)
**Phase:** green

---

## Your mission: GREEN (minimum changes to pass the 15 RED tests)

You are the implementer. Read ONLY this handoff — not the feature plan.
Write the MINIMUM production code to make the 15 failing tests pass.
Do NOT touch composer_backtest_client.py (the raw_value wrapper is correct).

Confirm RED first:

```
cd C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/symphony-fields
python -m pytest tests/advisors/test_symphony_schema_required_fields.py -p no:xdist -o addopts= -m "not live and not slow and not perf" -q
```

Expected: 15 FAILED / 5 passed.

---

## Files to modify

### ONLY change: `advisors/symphony_schema.py`

Two one-line additions:

**1. `make_root` (line ~784)**

Current output dict:
```python
{
    "step": "root",
    "name": name,
    "rebalance": rebalance,
    "id": _fresh_id(),
    "children": [copy.deepcopy(child) for child in children],
}
```

Required output dict (add `"description": ""`):
```python
{
    "step": "root",
    "name": name,
    "rebalance": rebalance,
    "description": "",        # required by live Composer /backtest API (was HTTP 400)
    "id": _fresh_id(),
    "children": [copy.deepcopy(child) for child in children],
}
```

**2. `make_inverse_vol` (line ~823)**

Current output dict:
```python
{
    "step": "wt-inverse-vol",
    "id": _fresh_id(),
    "children": [copy.deepcopy(child) for child in children],
}
```

Required output dict (add `"window-days": 30`):
```python
{
    "step": "wt-inverse-vol",
    "window-days": 30,        # required by live Composer /backtest API (was HTTP 422)
    "id": _fresh_id(),
    "children": [copy.deepcopy(child) for child in children],
}
```

That is the ENTIRE implementation. Two additive lines. Nothing else.

---

## Running the tests

```
python -m pytest tests/advisors/test_symphony_schema_required_fields.py -p no:xdist -o addopts= -m "not live and not slow and not perf" -q
```

Target: **20 passed / 0 failed** (15 newly GREEN + 5 already passing).

Also confirm the existing 210 symphony_schema tests still pass:
```
python -m pytest tests/advisors/test_symphony_schema.py -p no:xdist -o addopts= -m "not live and not slow and not perf" -q
```

Expected: 210 passed.

---

## Scope boundaries — DO NOT touch

- `advisors/composer_backtest_client.py` — the raw_value wrapper is correct, out of scope
- `advisors/strategy_builder_engine.py` — the T1–T7 template builders consume the constructors, no change needed
- Any other file — this is a two-line fix in symphony_schema.py only
- **NEVER merge, NEVER git checkout main, NEVER git push**

---

## Test Files Written (for reference)

- `tests/advisors/test_symphony_schema_required_fields.py` — 20 tests (15 RED, 5 already passing)
- `tests/ai_advisor/test_live_backtest_required_fields.py` — 1 LIVE test (excluded from default gate, opt-in with -m live)

---

## A/C Coverage Matrix

| A/C ID | Description | Test File | Test Name(s) | Status |
|--------|-------------|-----------|--------------|--------|
| AC-1 | `make_root` output includes `"description"` defaulting to `""` | test_symphony_schema_required_fields.py | TestMakeRootDescriptionField (5 tests) | RED |
| AC-2 | `make_inverse_vol` output includes `"window-days"` defaulting to `30` | test_symphony_schema_required_fields.py | TestMakeInverseVolWindowDaysField (5 tests) | RED |
| AC-3 | Every T1–T7 template tree backtests HTTP 200 (live) | test_live_backtest_required_fields.py | test_all_strategy_builder_templates_backtest_200 | RED (LIVE only) |
| AC-4 | Additive backward compat: new keys don't break existing assertions | test_symphony_schema_required_fields.py | TestAdditiveKeysBackwardCompatibility (4 tests, 2 RED/2 pass) | RED |
| AC-5 | validate_tree/lint_tree accept trees with the new fields | test_symphony_schema_required_fields.py | TestNewFieldsPassValidation (6 tests, 3 RED/3 pass) | RED |

## Import Stubs Created

None. `advisors/symphony_schema.py` already exists — the fix only adds keys to existing constructors.

## Test File Issues (for test-writer to fix)

None. All 15 RED tests were implementation bugs (missing constructor fields), not test bugs.

## Implementation Notes

- `make_root`: added `"description": ""` between `"rebalance"` and `"id"` — one additive line.
- `make_inverse_vol`: added `"window-days": 30` between `"step"` and `"id"` — one additive line.
- No other files touched. `composer_backtest_client.py` and `strategy_builder_engine.py` untouched.
- Staged path-scoped (`git add advisors/symphony_schema.py` only) — doc WIP (DECISIONS.md, docs/generated/*, feature-plans/*) not included.

## Status Log

- [2026-06-18] sf-test-writer: RED phase complete — 15 FAILED / 5 passed on RED SHA (committed below)
- [2026-06-18] sf-implementer: GREEN complete — 20/20 tests passing (new file) + 210/210 pre-existing tests passing. 0 test bugs documented. Typecheck N/A (stdlib only). Lint pending commit.
