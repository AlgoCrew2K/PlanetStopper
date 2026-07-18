# R3-b MA-4 disarm-band — scoping report (r3b-scout, 2026-07-18) — feeds feature-plans/math-r3b.md

All citations verified against **`origin/main @ 7e0b7778`** (PR #100, R3-a merged). Read-only scope — NOT a fix proposal. R3-b = the MA-4 disarm-band BUG fix, the FIRST live-execution-path cycle of the math-remediation program.

## 0. DISPATCH FOOT-GUN
Local primary checkout is a STALE husk (`cc1eaaa3`, pre-R0); `alpha_bot_execution.py` differs +88/−5 vs `origin/main 7e0b7778`. **The R3-b worktree MUST fork off `7e0b7778` explicitly** (`git worktree add -b fix/math-r3b <path> 7e0b7778`) + verify base SHA in kickoff. All MA-4 line numbers below verified against `7e0b7778`.

## 1. MA-4 the SETTLED bug — verified exact conditional
`alpha_bot_execution.py` @ 7e0b7778:
- Constants: `TRIGGER_THRESHOLD_PCT = 15.0` (`:88`), `TAKE_PROFIT_MC_PCT = 5.0` (`:90`).
- `mc_available = prob_underperforming is not None` (`:1371`).
- **ARM band `:1373-1381`:** arm when `mc_available and 5.0 <= prob_underperforming < 15.0`; `elif not mc_available:` → arm (MA-10 fail-open). ARM apply `:1383-1392` (guarded `not armed and not triggered`).
- **DISARM (THE BUG) `:1394-1402`:** `elif armed and not triggered:` then `if mc_available and prob_underperforming > (acc_TRIGGER_THRESHOLD_PCT*2) and current_return > 0.0:` (= **prob > 30.0 AND return > 0**) → `armed=False` (`:1400`), `below_stop_count=0` (`:1401`), print `"DISARMED (Conditions Recovered)"` (`:1402`).

**Why inverted (proven from source):** `run_monte_carlo` returns `((paths−below_count)/paths)*100` (`math_engine.py:1264-1266`) = fraction of regime-matched analog paths that BEAT us → **HIGH (~100) = badly underperforming, LOW (~0) = outperforming** (`compute_exit_confirmation` docstring `math_engine.py:509-513`). So `prob > 30` is **deterioration**, never "recovered" — the message is a lie and the disarm strips protection at the worst moment.

**Decisive interaction:** `compute_exit_confirmation` only ALLOWS the trailing-stop exit at `prob >= MC_BREAKDOWN_THRESHOLD = 60.0` (`math_engine.py:471`, gate `:552-555`). The buggy disarm fires at prob > 30 (a superset of ≥60) → removes `armed=True` EXACTLY as the exit gate opens. In the audit's "+3%→−1.5% giveback": arms at peak (prob∈[5,15)), prob climbs past 30 while return still nominally positive → disarm → can never re-arm (re-arm needs prob back in [5,15)) → bleeds to −1.5% with `compute_exit_confirmation` short-circuiting on `not armed` (`:546-547`). **Never exits. That is the bug.**

**Correct behavior (settled, cited `feature-plans/math-remediation-program.md:7,:35`):** disarm ONLY when prob falls back **below the arm-band lower edge (prob < TAKE_PROFIT_MC_PCT = 5.0)**, with hysteresis; DELETE the `prob > 2×threshold` / `return > 0` legs; fix the message. On the high side (prob ≥ 15, incl. ≥ 60) the stop STAYS armed so `compute_exit_confirmation` can fire.

## 2. TP-disarm `:1560-1561` — ADJUDICATED: does NOT share the inversion → LEAVE IT ALONE
Production TP is delegated to `math_engine.compute_tp_confirmation` (`:656`); the abe prints at `:1544-1552`/`:1560-1561` are pure telemetry (no decision logic). The TP-disarm (`compute_tp_confirmation:712-723`): while `tp_armed`, if MC rises to `prob >= take_profit_mc_pct` for `TP_CONFIRM_TICKS=2` → if `return>0` take profit, ELSE disarm. **Structurally different + correct:** TP arms on an exceptional GAIN (prob<5.0); when the gain mean-reverts with no profit left (return≤0), it stands down the PROFIT-TAKING arm only — removes NO downside protection (the trailing stop is independent, untouched; `is_tp_hit` stays False). Message is accurate. **Verdict: OUT OF SCOPE — do not touch.** (Resolves the R3-a checklist's ":1561 blast-radius" adjudication request.)

## 3. Full blast radius — every `armed` consumer + the REPLAY DUPLICATION
### 3a. `armed` sites in alpha_bot_execution.py
- **DECISION-CRITICAL (the only place `armed` feeds an exit):** `:1497` passes `armed=` into `compute_exit_confirmation` (`:1496-1505`) → `is_trailing_stop_hit` feeds `resolve_trigger_priority` (`:1741-1746`, gated `:1733`). So the disarm DOES feed the 6-layer exit — by zeroing the "Trailing Stop" input. The fix keeps `armed=True` through the giveback so this fires.
- **Cosmetic/telemetry (behavior-preserving, will visibly change post-fix):** `:1508-1516` below-stop-count prints; `:1648-1649` chart `"Armed"`; `:1653-1662` `tracked_stop` (stop line persists correctly through deterioration post-fix).
- **Legitimate resets (LEAVE ALONE):** `:853` position-recycle (new epoch); `:1883` post-trigger reset. Preserve the `below_stop_count=0` reset on the recovery-disarm.

### 3b. ⚠️ BIGGEST FINDING — the disarm is DUPLICATED in the autotuner replay (prior scope MISSED it)
`autotuner.py` `_replay_exit_tick` (`:1126`) carries the SAME inverted disarm, no print: ARM band `:1212-1216` (identical), ARM apply `:1218-1219`, **DISARM `:1220-1223`:** `elif state["armed"]:` `if mc_available and mc > (trigger_threshold*2) and ret > 0.0:` → `armed=False`, `below_stop_count=0`. Stale comment `:1204-1211`.
**HARD PARITY REQUIREMENT (two ways):** (1) the AC-3/AC-6 production⇄replay parity battery (`tests/autotuner/`) breaks if only one side is fixed; (2) **R3-d retunes by replaying exit decisions** — replay disarm ≠ production disarm ⟹ the tune optimizes against a different exit surface than live, invalidating it. **R3-b MUST fix both sites identically.**

## 4. Behavioral goldens + existing-test impact (root-cause each, NOT blind make-green)
The arm/disarm block is **inline in `main()`'s loop** — the one exit primitive NOT extracted to `math_engine` (contrast `compute_exit_confirmation`/`compute_tp_confirmation`, both pure + fixture-tested under `tests/math_engine/`). **HIGHEST-LEVERAGE RECOMMENDATION: extract `math_engine.compute_arm_disarm_decision(...)` called by BOTH `alpha_bot_execution.py:1373-1402` and `autotuner.py:1212-1223`** — kills the duplication, makes parity STRUCTURAL (not hand-maintained), and gives a pure seam for the giveback golden battery under `tests/math_engine/` + JSON fixtures. If extraction declined, the "+3%→−1.5% MUST exit" goldens must be written in BOTH `tests/execution/` (production via `main()`) and `tests/autotuner/` (replay), duplicated. Encode the probe: arm at peak (prob∈[5,15)) → prob rises ≥60 as return gives back through 0 into negative → assert `armed` stays True AND reason=="Trailing Stop" fires.

**Existing tests — classified:**
- **BREAKS (pins the bug as correct → REWRITE expectation):** (1) `tests/autotuner/test_ac3_replay_fail_open_arm_parity.py::test_replay_still_disarms_on_extreme_available_mc_with_positive_return` (`:227-250`) + helper `_mc_disarm_tick` (`:103-114`) — after fix, mc=31/ret>0 must KEEP armed; keep its `below_stop_count==0` reset assertion. (2) `tests/autotuner/test_h1_replay_underperformance_parity.py::test_replay_disarm_behavior_unchanged` (`:179-200`) — mc=40/ret+2% "must disarm" is the bug; rewrite (reset assertion `:198` survives).
- **SURVIVES-BUT-WRONG-REASONING (rewrite docstring):** (3) `test_h1_replay_underperformance_parity.py::test_replay_does_not_disarm_when_return_non_positive` (`:202-214`) — passes post-fix but for the right reason.
- **SURVIVES (watch-items):** (4) `tests/execution/test_h3_failopen_arming.py::test_mc_present_disarm_branch_still_requires_mc_available` (`:312-352`) — LENIENT AST, survives IFF the fix keeps `mc_available` in the disarm expr + `armed=False` in the same `If` subtree; update docstring. (5) `tests/execution/test_main_insufficient_mc_failsafe.py::test_insufficient_mc_does_not_spuriously_disarm_symphony` (`:316-342`). (6) arm-band survivors (unchanged). (7) `tests/autotuner/test_ac3…::test_misleading_neither_arm_nor_disarm_comment_is_corrected` (`:278-293`) — WATCH: the new comment must NOT reintroduce "drives neither an arm nor a disarm". (8) TP test `test_c3_replay_tp_rearm.py::test_replay_tp_disarm_on_above_threshold_with_nonpositive_return` (`:305-347`) survives (TP unchanged).
`tests/math_engine/` unaffected today (disarm isn't in math_engine — UNTIL the extraction moves it there).

## 5. Hysteresis design (proposal)
A bare single-tick `prob < 5.0` disarm would chatter (R3-a's `mc_band_edge_stability` probe: MC arm-decision flip-rate near a band boundary is proximity-driven, ~28% even at 5000-vs-5000 paths — a dead-zone is REQUIRED). **Recommended (codebase idiom): a recovery-tick confirmation ladder** — require `prob < TAKE_PROFIT_MC_PCT` for `DISARM_CONFIRM_TICKS` consecutive ticks before flipping `armed=False` (mirrors `below_stop_count→EXIT_CONFIRM_TICKS=3`, `above_tp_count→TP_CONFIRM_TICKS=2`). Optional secondary value margin `H` (disarm at `prob < 5.0 − H`). **Name the constants + source comments** (no magic numbers); recommend **frozen theory-set, NOT added to the Optuna search space** (don't widen the search mid-remediation). Size the tick count via R3-a's `scripts/mc_band_edge_stability_probe.py` against measured flip-rate.

## 6. Sequencing / risk
LIVE-money exit-decision change — own PR + `/review` + PM live E2E + DROPLET DEPLOY after merge. PM battery MUST include hot-file guard suites (`tests/execution/`, `tests/math_engine/`, `tests/autotuner/`) for the `alpha_bot_execution.py`/`math_engine.py`/`autotuner.py` touch (F7 CI-bounce lesson); targeted `-n0` only (238GB-crash lesson). R3-d gated on R3-b + R3-c merged+deployed.
