# Feature: Math R3-b — MA-4 Disarm-Band Bug Fix (disarm-on-recovery, production + replay parity)
Status: ready
Created: 2026-07-18

> Part of the math-remediation program (charter `feature-plans/math-remediation-program.md`, audit DE-MATH-AUDIT-001). R3 gated sequence: R3-a (shipped @ 7e0b7778) → **R3-b (this, MA-4, LIVE-execution-path)** → R3-c (MA-11) → R3-d (retune, operator-gated). Scope basis: `feature-plans/math-r3b-scoping.md` (r3b-scout, file:line-verified @ `7e0b7778`). **Base: `origin/main @ 7e0b7778` — fork the worktree off it EXPLICITLY (local main is a stale pre-R0 husk).**

## Summary
MA-4: the live trailing-stop disarm band is INVERTED. It disarms when `prob_underperforming > 30 AND current_return > 0` (`alpha_bot_execution.py:1394-1402`) — which is *deterioration*, not recovery — while printing "DISARMED (Conditions Recovered)". Because `run_monte_carlo` returns the fraction of regime-matched paths that BEAT us (HIGH = worse), and `compute_exit_confirmation` only allows the trailing-stop exit at `prob ≥ 60`, the buggy disarm strips `armed` exactly as the exit gate opens — so a position that arms on a peak then gives back into a loss (the audit's `+3% → −1.5%` probe) can **never exit**. R3-b makes the disarm key on genuine RECOVERY (prob back below the arm-band lower edge `TAKE_PROFIT_MC_PCT=5.0`, with a recovery-tick hysteresis ladder to avoid chatter), fixes the message, and fixes the IDENTICAL duplicated disarm in the autotuner replay (`autotuner.py:1220-1223`) — because R3-d retunes by replaying exit decisions, so production and replay MUST share one exit surface. This is the FIRST cycle to change live exit decisions.

## Acceptance Criteria
- [ ] **AC-1 (disarm-on-recovery, production):** the disarm at `alpha_bot_execution.py:1394-1402` fires ONLY when `prob_underperforming` falls back below the arm-band lower edge (`< acc_TAKE_PROFIT_MC_PCT`, 5.0) — the `prob > 2×threshold` and `current_return > 0` legs are DELETED. On deterioration (prob ≥ 15, incl. ≥ 60) the stop STAYS armed.
- [ ] **AC-2 (hysteresis, no chatter):** disarm requires `prob < TAKE_PROFIT_MC_PCT` sustained for `DISARM_CONFIRM_TICKS` consecutive ticks (a named, source-commented, FROZEN theory-set constant — NOT added to the Optuna search space) before flipping `armed=False`. Mirrors the existing `below_stop_count→EXIT_CONFIRM_TICKS`/`above_tp_count→TP_CONFIRM_TICKS` machines. The tick count is sized against R3-a's `scripts/mc_band_edge_stability_probe.py` measured flip-rate (documented). `mc_available` remains required in the disarm expression (a real reading confirms recovery).
- [ ] **AC-3 (message honesty):** the "DISARMED (Conditions Recovered)" print now fires only on genuine recovery (true), and no telemetry/comment reintroduces the misleading phrasing or the `autotuner.py:278-293`-guarded "drives neither an arm nor a disarm" string.
- [ ] **AC-4 (behavioral golden — the giveback MUST exit):** a position that arms at a peak (prob ∈ [5,15)) then has prob rise ≥ 60 as `current_return` gives back through 0 into negative KEEPS `armed=True` and TRIGGERS with reason `"Trailing Stop"` (via `compute_exit_confirmation` → `resolve_trigger_priority`). Under the OLD code this never exits — the golden must be RED-then-GREEN.
- [ ] **AC-5 (PRODUCTION⇄REPLAY parity — HARD):** the identical inverted disarm in `autotuner.py:_replay_exit_tick:1220-1223` is fixed to the SAME behavior as production, structurally (see Architecture — extract one shared decision fn). A parity test asserts production and replay produce identical arm/disarm decisions across the giveback + boundary scenarios. WHY: R3-d retunes by replaying — divergent exit surfaces invalidate the tune.
- [ ] **AC-6 (TP-disarm UNTOUCHED):** `compute_tp_confirmation`'s TP-disarm (`math_engine.py:712-723`) and its telemetry (`alpha_bot_execution.py:1544-1561`) are UNCHANGED (adjudicated NOT to share the inversion — it stands down a profit-taking arm, removes no downside protection).
- [ ] **AC-7 (arm band + resets preserved):** the arm band (`:1373-1381`, 5.0 ≤ prob < 15.0) + MA-10 fail-open + the `below_stop_count=0` reset on the recovery-disarm + the legitimate epoch/post-trigger resets (`:853`, `:1883`) are unchanged.
- [ ] **AC-8 (existing OLD-behavior-pinning tests root-caused, not blind-made-green):** the tests that pin the bug as correct are REWRITTEN with root-cause verdicts (they encoded the buggy behavior): rewrite `test_replay_still_disarms_on_extreme_available_mc_with_positive_return`, `test_replay_disarm_behavior_unchanged` (expectations); rewrite the docstring of `test_replay_does_not_disarm_when_return_non_positive`; update the `test_h3_failopen_arming` / `test_main_insufficient_mc_failsafe` docstrings; the `test_misleading_..._comment_is_corrected` guard stays green (new comment must not reintroduce the phrase).

## Architecture
LIVE-execution-path change to `alpha_bot_execution.py` + `autotuner.py` + `math_engine.py`.

- **[PM DECISION — extraction REQUIRED for structural parity]** Extract the arm/disarm decision into ONE pure `math_engine` function — `math_engine.compute_arm_disarm_decision(...)` — called by BOTH `alpha_bot_execution.py:1373-1402` (production, inline in `main()`) AND `autotuner.py:_replay_exit_tick:1212-1223` (replay). Rationale: the disarm is currently DUPLICATED, and R3-d's retune depends on production⇄replay parity — hand-maintained parity is fragile (a future edit to one site silently diverges the exit surface). A single pure seam makes parity STRUCTURAL and gives a fixture-tested golden seam under `tests/math_engine/` (matching how `compute_exit_confirmation`/`compute_tp_confirmation` already live). The function is PURE (inputs: prob_underperforming, mc_available, current armed state, current_return-if-still-needed, the disarm-tick counter, the constants; outputs: new armed state + disarm-tick counter + a reason/telemetry flag) — no I/O. The RED tests target this seam, which forces the extraction.
- **Constants** in `math_engine.py` (project rule: no magic numbers): `DISARM_CONFIRM_TICKS` (recovery-tick ladder) + optional `H` margin, each named + source-commented, FROZEN (not in `autotuner.OPTUNA_SEARCH_SPACE_KEYS`).
- **Behavior on the non-buggy paths must be byte-preserved** — the extraction must not change arm-band arming, fail-open, or the resets; a parity/regression pin proves it.

## Edge Cases
- Prob oscillating around 5.0 near the band edge → the tick ladder must prevent arm/disarm chatter (the whole point of AC-2; R3-a's probe shows ~28% single-tick flip near a boundary).
- `mc_available=False` (MC-insufficient) while armed → must NOT disarm (fail-open keeps armed); the disarm requires `mc_available`.
- Recovery-tick counter reset: if prob rises back into/above the band mid-recovery-count, the disarm-tick counter resets (recovery not sustained).
- Prob exactly at 5.0 (boundary) — define `< 5.0` strictly (below the arm floor), documented.
- A position that arms, recovers genuinely (prob < 5 sustained), then re-deteriorates → must be able to RE-ARM (the arm band is re-entrant).
- Determinism in replay: the extraction must not introduce any nondeterminism in `_replay_exit_tick` (R1/R2 replay-fidelity invariants).

## Security Considerations
Minimal new attack surface (internal engine logic, no new external input/route/credential). Applicable safety rules: no live API in tests (synthetic fixtures + JSON goldens); DB via conftest DB_PATH; the change is to internal exit-decision math, validated at the golden-fixture boundary. This is a LIVE-money trade-decision path — the real "security" concern is CORRECTNESS (a wrong disarm loses real money), covered by the behavioral goldens + parity + the PM live E2E.

## Testing Strategy
- **RED (quant-test-writer, adversarial):** the AC-4 giveback golden (arm→deteriorate→giveback-to-loss→MUST exit) against the extracted `compute_arm_disarm_decision` pure seam (RED because the fn doesn't exist / the behavior is inverted); AC-1/AC-2 disarm-on-recovery + tick-ladder unit tests; AC-5 production⇄replay parity test; AC-8 rewrites of the 2 bug-pinning replay tests (with root-cause verdicts). JSON fixtures under `tests/fixtures/math_engine/`.
- **Non-vacuity:** the giveback golden must be demonstrably RED on the OLD inverted disarm (prove it fails before the fix), and the parity test must fail if only one site is fixed.
- **PM battery (targeted `-n0`):** MUST include the hot-file guard suites for this `alpha_bot_execution.py`/`math_engine.py`/`autotuner.py` touch — `tests/execution/`, `tests/math_engine/`, `tests/autotuner/`, `tests/error_handling/` (F7 CI-bounce lesson) + both ruff + credential-less. NEVER full/uncapped/-n>4 (238GB lesson).
- **PM live E2E (live-path):** confirm the fixed disarm on a real/seeded giveback scenario against the running engine (not just tests-green) before merge.

## Decisions
| Decision | Rationale |
|----------|-----------|
| Extract ONE shared `compute_arm_disarm_decision` (production + replay both call it) | Structural production⇄replay parity — R3-d retunes by replay; hand-maintained parity silently diverges. r3b-scout's highest-leverage recommendation. |
| Hysteresis = recovery-tick confirmation ladder (DISARM_CONFIRM_TICKS), frozen, not tuned | Mirrors existing tick-confirm machines; single-tick disarm chatters near the boundary (R3-a probe); don't widen the Optuna search mid-remediation. |
| TP-disarm `:1561` = NO change | Adjudicated (r3b-scout): stands down a profit-taking arm, removes no downside protection; structurally different + correct. |
| Existing bug-pinning tests REWRITTEN with root-cause verdicts, not blind-made-green | They encoded the buggy behavior; the settled ruling is the arbiter (charter:7,:35). |
| `[PM-ASSUMED]` DISARM_CONFIRM_TICKS value | Team sizes it via the mc_band_edge_stability probe; a judgment call documented, not asked of the operator (MA-4 is a settled bug). |

## Scope Boundaries
- **IN:** fix the inverted disarm (production `alpha_bot_execution.py:1394-1402` + replay `autotuner.py:1220-1223`) to disarm-on-recovery-with-hysteresis via one shared `math_engine.compute_arm_disarm_decision`; fix the message; behavioral goldens (giveback MUST exit) + parity test; rewrite the OLD-behavior-pinning tests; name the hysteresis constants.
- **OUT:** MA-11 MAX_SQUEEZE_FLOOR (R3-c); the retune (R3-d); the TP-disarm (adjudicated no-change); the arm band itself (unchanged); any Optuna search-space change; MA-4-unrelated exit logic.
