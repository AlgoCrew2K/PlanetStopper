# TDD Handoff — calibration-sweep RED phase

**From:** cs-test-writer (LEAD)
**To:** cs-implementer
**Branch:** feat/calibration-sweep  
**RED commit:** ac39e3c  
**Result:** 21 failed / 5 passed on ac39e3c

---

## For the implementer (cs-implementer)

Read this file, NOT the feature plan. Your job is to make 21 failing tests go GREEN with the minimum code required.

## What is failing and why

### AC-1 — Search space expansion (8 tests in test_calibration_sweep_search_space.py)

`OPTUNA_SEARCH_SPACE_KEYS` in `autotuner.py:123-132` is missing 3 keys:
- `VWAP_BREAK_CONFIRM_TICKS`
- `VWAP_BLEED_ARM_MIN`
- `VWAP_BLEED_ARM_MAX`

You must also add named bound constants (no magic numbers — project rule):
- `_SS_VWAP_BLEED_ARM_MIN_LOW = -5.0`  (most-negative allowed)
- `_SS_VWAP_BLEED_ARM_MIN_HIGH = -1.0`
- `_SS_VWAP_BLEED_ARM_MAX_LOW = -1.0`
- `_SS_VWAP_BLEED_ARM_MAX_HIGH = -0.1`
- `_SS_VWAP_BREAK_CONFIRM_TICKS_LOW = <int>`  (suggest as int, not float)
- `_SS_VWAP_BREAK_CONFIRM_TICKS_HIGH = <int>`

Wire the 3 new params into the `objective()` closure inside `run_calibration_sweep` the same way the existing 2 params are wired (`trial.suggest_float` / `trial.suggest_int`).

Also wire them into the main `run_autotuner` objective closure at `autotuner.py:2264-2289` — AC-1 says the walk-forward search space is expanded (not just the calibration sweep).

The `validate_search_space_nn1()` check at `autotuner.py:1843` must still pass — none of the new keys are theory-frozen facets, so this should not require any change there.

### AC-2 — Report script (tests in test_calibration_sweep_report.py)

Create `scripts/vwap-calibration-report.py`. It must expose a callable named `generate_report`, `render_report`, or `build_report` that accepts a list of report row dicts (the same shape `run_calibration_sweep` returns) and returns either:
- A list of enriched row dicts, OR
- A markdown string

Required row keys: `symphony_id`, `param_name`, `current_value`, `proposed_value`, `expected_trigger_freq_change`, `pbo_veto_status`, `flag_for_operator_review`, `haircut_outcome`, `study_name`.

The script must NOT import `database` write-path symbols (insert_advisor_observation, set_symphony_live_mode, save_autotune_run, init_db, get_connection, etc.).

The script must NOT import `alpha_bot_execution` at module level.

### AC-3 — Advisory invariant (tests in test_calibration_sweep_advisory_invariant.py)

The 3 import-level tests fail because `scripts/vwap-calibration-report.py` does not exist. Once you create the script, make sure:
1. No database write-path symbols are imported or called.
2. `alpha_bot_execution` is not imported at module level.
3. `run_calibration_sweep` source must not contain: `insert_advisor_observation`, `set_symphony_live_mode`, `save_autotune_run`, `init_db(`, `get_connection(`, `.execute(`, `bot_state`.

The 4th advisory test (`test_run_calibration_sweep_return_has_no_write_confirmation_keys`) already passes — do not break it.

### AC-4 — Insufficient history skip (tests in test_calibration_sweep_insufficient_history.py)

`run_calibration_sweep` must skip symphonies with fewer than 125 trading days.

Add a named constant: `_CALSWEEP_MIN_HISTORY_DAYS = 125`

Add at the top of the per-symphony loop (before creating the Optuna study):
```python
if len(history_data.get(sym_id, {})) < _CALSWEEP_MIN_HISTORY_DAYS:
    logging.warning("run_calibration_sweep: skipping %s — only %d days (< %d required)",
                    sym_id, len(history_data.get(sym_id, {})), _CALSWEEP_MIN_HISTORY_DAYS)
    continue
```

### AC-5 — PBO veto status (tests in test_calibration_sweep_report.py)

Add `pbo_veto_status` key to each row appended in `run_calibration_sweep`. Use `haircut_outcome == "no_trial_cleared"` as the proxy, or a dedicated PBO check. The flag must be present on every row.

### AC-6 — Study name hygiene (tests in test_calibration_sweep_report.py)

Study names at `autotuner.py:2974-2975` currently use `f"{study_timestamp}__{sym_id}"`. For the calibration sweep, change to `f"{study_timestamp}__{sym_id}__calsweep"`.

The study_name regex expected: `r'^\d{8}T\d+Z__[^_].*__calsweep$'`

Only change the calibration sweep study names — do NOT alter `run_autotuner`'s production study name format.

### AC-7 — >2x trigger flip flag (tests in test_calibration_sweep_report.py)

Add `flag_for_operator_review` key to each row:
```python
flag_for_operator_review = (
    current_trigger_count > 0 and
    proposed_trigger_count / current_trigger_count > 2.0
)
```

## Tests that already pass — do not break them

- `test_nn1_validation_passes_after_search_space_expansion` — passes today; must still pass after you add keys
- `test_run_calibration_sweep_return_has_no_write_confirmation_keys` — passes today; do not add DB write calls
- `test_pbo_veto_status_key_present_in_all_rows` — fixture integrity; not affected by your changes
- `test_study_names_match_calsweep_pattern` — passes against fixture rows
- `test_study_names_are_unique_across_symphonies` — fixture uniqueness check

## Key files to touch

| File | What to change |
|------|---------------|
| `autotuner.py:123-132` | Add 3 keys to `OPTUNA_SEARCH_SPACE_KEYS` |
| `autotuner.py:262-283` | Add 6 named bound constants |
| `autotuner.py:2264-2289` | Wire 3 new params into `run_autotuner` objective |
| `autotuner.py:2970-2975` | Add `__calsweep` suffix to study name |
| `autotuner.py:2979-2993` | Wire 3 new params into `run_calibration_sweep` objective |
| `autotuner.py:3096-3114` | Add `pbo_veto_status` and `flag_for_operator_review` to each row; add skip gate above |
| `scripts/vwap-calibration-report.py` | CREATE: advisory-only report generator |

## Hard constraints (absolute — non-negotiable)

- NEVER merge to main. NEVER run `git merge`, `git checkout main`.
- Commit only to `feat/calibration-sweep`.
- Do NOT run the full test tree — run only the 4 target files with `-n0`.
- Do NOT write test code — only production code.
- When GREEN, signal `cs-test-writer` with: "GREEN: N passed / M failed on <sha>"
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
