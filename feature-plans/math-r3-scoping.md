# R3 Scoping Report (r3-scout, 2026-07-18) — feeds feature-plans/math-r3.md

All citations from `origin/main @ 77551f1c` (R0+R1+R2+F7 all shipped/live). Read-only scoping — NOT a plan yet.

## ⚠️ DISPATCH FOOT-GUN (handle first)
Local `main` is STALE at `cc1eaaa3` (PR #80, pre-R0) — the charter + all R0–F7 code do NOT exist locally. **The R3 worktree MUST fork off `origin/main` (`77551f1c`) explicitly** (`git worktree add -b fix/math-r3 <path> 77551f1c`), never off local `main`, and the kickoff must verify the base SHA (memory: isolation-worktree-stale-base-fork). (Reconciling local main is optional; R1/R2/F7 all forked off explicit origin/main SHAs.)

## R3 = a GATED SEQUENCE, not one cycle. Recommended SPLIT (r3-scout):
1. **R3-a — pre-retune checklist prerequisites (TESTS-ONLY PR, first, low-risk).**
2. **R3-b — MA-4 disarm-band bug fix (LIVE-PATH PR, own review + PM live E2E).**
3. **R3-c — MA-11 MAX_SQUEEZE_FLOOR knob (LIVE-PATH PR, own review + PM live E2E).**
4. **R3-d — the first trustworthy retune (an OPERATION, NOT a code PR).** Gated on R3-a green + R3-b/c merged+deployed + **explicit operator before/after sign-off before tuned params persist to live symphonies.**

> **SUPERSEDED (2026-07-18, `DE-MATH-R3A-001`, r3a-review APPROVE @ `c8615201`):** items (a) and (b) below are now MET / MET-WITH-FINDING -- this section is a dated snapshot of the 2026-07-18 scoping pass, kept verbatim for the historical record, not rewritten. See `DE-MATH-R3A-001` in `DECISIONS.md` for the current, authoritative status of all three checklist items.

## 1. PRE-RETUNE CHECKLIST (DECISIONS.md:6350-6355) — 1 of 3 MET
- **(a) Parabolic walk-forward variance demo — UNMET** (R1 AC-7 deferred it here; DECISIONS.md:6161-6173). Partial: `tests/autotuner/test_ac7_inert_dims_objective_variance_smoke.py` proves `TAKE_PROFIT_MC_PCT` at walk-forward but the 2 parabolic dims (`PARABOLIC_VELOCITY_THRESHOLD`, `MAX_PARABOLIC_SQUEEZE`) only at wiring level. R3-a must add a bounded deterministic-seed walk-forward smoke showing non-zero objective variance across swept values of ALL tuned dims. Hard rule: no retune ships live params without demonstrating objective variance on every tuned dim.
- **(b) 300-path band-edge stability — UNMET** (no artifact; `_MC_REPLAY_SIMULATION_PATHS=300`; DE-MATH-R1-001 ADDENDUM 4, DECISIONS.md:5969-5976). R3-a must add a stability probe: arm-decision flip-rate at 300 vs higher path count near the band edge → decide bump vs accept.
- **(c) Undated-Optuna-path tripwire XPASS — MET** (R2 AC-4 cleared it; `tests/autotuner/test_ac4_r2_residual_tripwire.py` has no xfail marker remaining; companion `test_ac4_undated_path_regime_faithful.py`).

## 2. MA-4 disarm-band BUG (ruling SETTLED, not open)
- **Location:** `alpha_bot_execution.py:1394-1402` — disarms when `prob_underperforming > (acc_TRIGGER_THRESHOLD_PCT*2) AND current_return > 0.0` (= DETERIORATION) while printing "DISARMED (Conditions Recovered)". Arm band at `:1373-1381` (`acc_TAKE_PROFIT_MC_PCT <= prob < acc_TRIGGER_THRESHOLD_PCT`).
- **Fix:** disarm when `prob_underperforming` falls back BELOW the arm band (with hysteresis to avoid chatter), never on `prob > 2×threshold`; fix the message.
- **Blast radius (charter = every arm/disarm consumer):** SECOND disarm site = TP path `alpha_bot_execution.py:1561` ("TP-DISARMED (MC Rose but Return <= 0)"), TP-arm `:1545-1561` — R3-b MUST adjudicate whether it shares the inversion. Also `below_stop_count` reset (:1400) + downstream `armed` readers.
- **Fixtures:** encode the audit's "slow +3%→−1.5% giveback MUST exit" probes as live-path goldens.

## 3. MA-11 MAX_SQUEEZE_FLOOR knob (design choice, not pure bug)
- **Dead knob:** `acc_MAX_SQUEEZE_FLOOR` has ONE repo occurrence — its assignment at `alpha_bot_execution.py:1234`; never read. Yet fully operator-wired: env `:89`, `DEFAULT_PARAMS` `database.py:45`, UI `app.py:3535`+`:5686`, advisor-suggestible `ai_advisor.py:115` (range 0.05–0.50).
- **What it should clamp:** `math_engine.py:379-381` — `active = max(safe_vol*mult, dynamic_min_stop)` then `active *= parabolic_squeeze_multiplier` with NO re-floor (floored stop multiplied below the floor).
- **Fix:** wire as post-squeeze lower clamp at `:381` (`active = max(active*squeeze, acc_MAX_SQUEEZE_FLOOR)`) OR remove from UI/advisor/allowlist/DEFAULT_PARAMS. Plus re-examine the `[0.1,0.8]` squeeze search range (`autotuner.py:308-309`, searched `:2492-2494`). Must land BEFORE the retune (shapes what it tunes).

## 4. THE RETUNE (R3-d, an operation)
- Mechanics: `autotuner.run_autotuner(bot_state, ...)` (`autotuner.py:2145`) — per-symphony walk-forward, 250-day, 60/20/20, PURGE=20/EMBARGO=1, 500 trials (`OPTUNA_N_TRIALS_PRODUCTION=500`, `:253`), TPE, R2 split-level CSCV + BHY + PBO veto + train-only holdout. Symphonies = live `bot_state` via nightly EOD (`app.py:3027`).
- **Reaches live money with NO code gate:** winners written by `database.save_symphony_strategy(...)` (`autotuner.py:3022`) → engine reads as `acc_params`. Operator before/after sign-off is the ONLY safety valve → EXPLICIT BLOCKING STEP in the plan.
- Gated on: checklist (a)+(b) green + MA-4/MA-11 merged+deployed (both change the decision surface the optimizer tunes against — retuning first invalidates the tune).

## 5. RISKS / ORDERING
- **Sequence:** R3-a (checklist, tests-only) → R3-b + R3-c (live-path, can be parallel PRs) → R3-d (retune, after a+b+c + operator sign-off).
- **Live-money:** first cycle to change live exit decisions (MA-4) + live stop distances (MA-11). Every golden pinning OLD disarm/squeeze behavior must be root-caused (real change → update expected + add behavioral goldens), never blind make-green.
- Scope guards (`test_scope_guard.py` DE-EOD-BASIS-001, `test_scope_guard_f7.py` F7) are anchored to their OWN cycles' RED commits → won't obstruct R3 engine edits; no standing engine-freeze.
- Confirm R2's own residuals are closed before the retune leans on R2's CSCV.
- Testing: targeted `-n0` only (238GB-crash lesson); **PM battery MUST include the hot-file guard suites** (tests/error_handling/, tests/execution/, tests/math_engine/) for every alpha_bot_execution.py/math_engine.py touch (the F7 CI-bounce lesson).
