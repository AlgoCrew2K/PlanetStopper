# TDD Handoff -- calibration-sweep RED phase (2-param, CORRECTED)

**From:** cs-test-writer (LEAD)
**To:** cs-implementer
**Branch:** feat/calibration-sweep
**Worktree:** C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/calibration-sweep

---

## Current state after RED reconciliation

Run these 4 files first to confirm the RED profile:

    cd C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/calibration-sweep
    python -m pytest tests/test_calibration_sweep_search_space.py tests/test_calibration_sweep_report.py tests/test_calibration_sweep_advisory_invariant.py tests/test_calibration_sweep_insufficient_history.py -v -p no:xdist -o addopts= --timeout=60

Expected: **3 FAILED / 25 passed**

---

## What is failing and why (ONLY 3 tests)

### AC-1 violation -- wrong params in OPTUNA_SEARCH_SPACE_KEYS (autotuner.py:123-135)

    FAILED test_calibration_sweep_search_space.py::test_vwap_bleed_arm_min_not_in_search_space
    FAILED test_calibration_sweep_search_space.py::test_vwap_bleed_arm_max_not_in_search_space
    FAILED test_calibration_sweep_search_space.py::test_vwap_break_confirm_ticks_not_in_search_space

The current OPTUNA_SEARCH_SPACE_KEYS frozenset contains 3 params that must NOT be there:

    "VWAP_BREAK_CONFIRM_TICKS",   # WRONG -- hand-set, must be REMOVED
    "VWAP_BLEED_ARM_MIN",          # WRONG -- hand-set, must be REMOVED
    "VWAP_BLEED_ARM_MAX",          # WRONG -- hand-set, must be REMOVED

These were added by a stale prior-team cycle (superseded by AC-1 plan correction
2026-06-19). They are intentionally hand-set per V1 methodology review.
**Remove them from OPTUNA_SEARCH_SPACE_KEYS.** That is the ENTIRE implementation change.

---

## What is already complete (DO NOT undo or rewrite)

- scripts/vwap-calibration-report.py -- EXISTS, all 9 report + 4 advisory tests GREEN
- run_calibration_sweep -- AC-4 skip gate, AC-5 pbo_veto_status, AC-6 __calsweep
  study-name suffix, AC-7 flag_for_operator_review -- all 4 history tests GREEN

---

## Your one implementation change

File: autotuner.py:123-135

Remove these 3 keys from OPTUNA_SEARCH_SPACE_KEYS:
- VWAP_BREAK_CONFIRM_TICKS
- VWAP_BLEED_ARM_MIN
- VWAP_BLEED_ARM_MAX

Also remove any orphaned named bound constants added ONLY for these params
(_SS_VWAP_BLEED_ARM_MIN_LOW/HIGH, _SS_VWAP_BLEED_ARM_MAX_LOW/HIGH,
_SS_VWAP_BREAK_CONFIRM_TICKS_LOW/HIGH) if they appear around lines 285-315.
Also remove their trial.suggest_* calls from run_calibration_sweep's objective()
closure -- the sweep is 2-param only.

DO NOT touch _CALSWEEP_MIN_HISTORY_DAYS, AC-4 skip gate, AC-5/AC-6/AC-7 fields,
or the report script.

---

## After your change, verify GREEN

    python -m pytest tests/test_calibration_sweep_search_space.py tests/test_calibration_sweep_report.py tests/test_calibration_sweep_advisory_invariant.py tests/test_calibration_sweep_insufficient_history.py -v -p no:xdist -o addopts= --timeout=60

Expected: **28 passed / 0 failed**

Also confirm NN1 still passes after removing keys (should be fine -- none are theory-frozen).

---

## Stash reference (treat as reference, not trusted truth)

stash@{0} contains the prior team's WIP.
- The stash autotuner diff ADDS the 3 wrong params -- do NOT apply stash to autotuner.
- scripts/vwap-calibration-report.py in the stash matches the live file.
- DO NOT git stash pop -- it would re-introduce the wrong autotuner expansion.

---

## Scope boundaries -- DO NOT touch

- tests/ -- implementer writes no test code
- scripts/vwap-calibration-report.py -- already complete, do not modify
- run_calibration_sweep AC-4/AC-5/AC-6/AC-7 additions -- leave them
- TAKE_PROFIT_MC_PCT, VWAP_CROSS_HWM_PCT, VWAP_BLEED_MULTIPLIER, VWAP_BLEED_TICKS,
  PARABOLIC_VELOCITY_THRESHOLD, MAX_PARABOLIC_SQUEEZE -- stay in the frozenset
- NEVER merge to main. NEVER git checkout main. NEVER git push.

---

## When GREEN

Commit path-scoped (git add autotuner.py only or minimal set touched).
Commit prefix: fix(calibration-sweep):

Then SendMessage cs-test-writer: "GREEN: 28 passed / 0 failed on <sha>"
