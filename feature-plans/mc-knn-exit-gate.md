# Feature: Monte Carlo kNN Exit-Gate Fixes — Remediation Cluster 2
Status: ready
Created: 2026-05-21

## Summary

Cluster 2 of the AlphaBot v3 math-audit remediation. Fixes the Monte Carlo exit-gate math in `math_engine.py` — the kNN regime match that produces `prob_beating`, the value that gates every live trailing-stop exit (and the arm / take-profit / disarm logic). Covers audit finding C-1 (the audit's worst finding — an unstandardized kNN feature vector), the fail-dangerous insufficient-history sentinel, the 31-bit MC seed collision, and the early-window volatility bias. All changes are in `math_engine.py` and are shared by both exit altitudes.

Audited at `main @ 53ef340`; this cycle branches from `main @ 2bb39d9` (post-Cluster-1). Line numbers reference the audit SHA — the team re-locates against current code.

## Acceptance Criteria

- [ ] AC-1 (C-1 — unstandardized kNN): `run_monte_carlo` standardizes the 2-D kNN feature vector before the Euclidean distance. Both features — SPY daily return and SPY 20-day volatility — are z-scored (or min-max normalized) with window-fitted parameters; today's query point is transformed with the SAME parameters. Neither feature dominates the distance by a unit artifact. Proof: a golden-fixture regression with two historical days of near-identical SPY return but opposite volatility regimes — the high-vol query point must select the high-vol neighbor, not the return-only match.
- [ ] AC-2 (insufficient-history fail-safe): when MC history is insufficient, `run_monte_carlo` signals "insufficient" via a distinct out-of-band sentinel (e.g. `None` or a separate flag) — NOT the in-band `100.0`. `compute_exit_confirmation` treats insufficient-MC as "MC confirmation unavailable" and does NOT let it veto the trailing-stop exit: the protective stop fires on the ticks-below-stop condition alone. Insufficient MC history must never DISABLE the protective exit. The misleading "skips MC exit gate" comment is corrected to match real behavior.
- [ ] AC-3 (seed collision): `MC_SEED_MODULUS` is widened to `2**64` (or `derive_cycle_mc_seed` passes the full SHA-256 digest via `np.random.SeedSequence`) so cycle seeds do not collide at the birthday bound; the inaccurate "~98k distinct values/year with no collisions" comment is corrected.
- [ ] AC-4 (early-window vol bias): the first `MC_VOL_WINDOW_DAYS - 1` historical days — whose `spy_vols` are computed on short samples, with `spy_vols[0]` hard-set to `0.0` — are excluded from the kNN candidate pool (dropped, or their `spy_vols` marked `NaN` and NaN-vol days excluded from the distance).
- [ ] AC-5 (regression): every changed layer ships a golden-fixture or property-based test; the full `math_engine` + `portmode` + `autotuner` + `execution` suite stays green (1292-pass baseline, modulo the 2 known pre-existing dashboard failures); new tests are RED-verified against pre-fix code.

## Architecture

| Finding | File / function | Change |
|---|---|---|
| C-1 | `math_engine.py` `run_monte_carlo` (~649-650) | z-score both kNN features with window-fitted params before the Euclidean distance |
| insufficient-history | `math_engine.py` `run_monte_carlo` (~92-94, 628-629) + `compute_exit_confirmation` (~343) | emit a distinct insufficient sentinel; `compute_exit_confirmation` treats it as "no MC veto"; correct the comment |
| seed | `math_engine.py` `derive_cycle_mc_seed` + `MC_SEED_MODULUS` (~105) | widen to `2**64` / `SeedSequence`; correct the comment |
| early-window | `math_engine.py` `run_monte_carlo` (~636-642) | exclude short-sample early-window days from the kNN candidate pool |

## Edge Cases

- A query day whose SPY return matches a historical day but whose volatility regime is opposite (the C-1 proof case).
- A feature with zero variance over the window (z-score division by zero → guarded fallback).
- MC history exactly at the `MC_MIN_HISTORY_DAYS` boundary.
- MC history insufficient AND the position is below the stop — the protective exit must still fire (AC-2).
- The autotuner replay calls `run_monte_carlo` — changing the feature standardization changes its output; autotuner replay characterization fixtures will shift. That is intentional; Cluster 2 updates only those fixtures to keep the suite green. Fixing the autotuner `k=5` replay fidelity itself is Cluster 3, not this cycle.

## Security Considerations

Internal risk-engine math change — no new user input, no external calls, no auth surface. Safety-relevant invariant: AC-2 is itself a fail-safe correction — the change must ensure insufficient MC data fails toward PROTECTING the position (the stop still fires), never toward disabling it. `quant-code-reviewer`'s live-trade-boundary gate applies.

## Testing Strategy

- Golden-fixture tests: the standardized-kNN regime match (the equal-return / opposite-vol case), the insufficient-history fail-safe path, seed determinism + no-collision-at-scale, early-window exclusion.
- Property-based: standardized features are scale-invariant (neither dominates); `prob_beating` in `[0, 100]`; `derive_cycle_mc_seed` deterministic.
- Full `math_engine` + `portmode` + `autotuner` + `execution` suite green; new tests RED-verified against pre-fix code.
- `quant-test-writer` authors the adversarial RED tests — derive expected values from fixtures or assert shape/property; never hardcode producer outputs.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| D2 — insufficient-history policy | Bypass the MC gate (fail-safe), not "hold" | A risk engine must fail safe: when the MC second opinion is uncomputable, the protective trailing stop must still fire on its primary ticks-below-stop condition. Treating insufficient data as `prob=100` ("definitely recovers") disables the stop — the fail-dangerous behavior the audit flagged. |
| kNN standardization | z-score with window-fitted params | Literature standard (Hastie/Tibshirani/Friedman, ESL 2009 §13.3); restores the volatility feature from ~3% to a real share of the distance. |
| seed width | `2**64` | Eliminates birthday-bound collisions; numpy `default_rng` accepts the full 64-bit space. |

## Scope Boundaries

- **IN**: `math_engine.py` Monte Carlo exit-gate math — `run_monte_carlo` (kNN feature standardization, early-window candidate pool, insufficient-history sentinel), `derive_cycle_mc_seed` / `MC_SEED_MODULUS`, and `compute_exit_confirmation`'s handling of the insufficient-MC sentinel.
- **OUT**: the autotuner `k=5` replay fidelity (Cluster 3); the DSR layer and `compute_expected_max_sharpe` overflow (Cluster 4); `synthetic_history` (Cluster 5); portfolio / analytics (Cluster 6). Cluster 2 updates autotuner replay characterization fixtures only as needed to keep the suite green — it does not change autotuner logic.
