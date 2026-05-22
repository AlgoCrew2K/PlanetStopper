# Feature: Autotuner Statistics — Remediation Cluster 4
Status: ready
Created: 2026-05-21

## Summary

Cluster 4 of the AlphaBot v3 math-audit remediation. Fixes the autotuner's selection-statistics layer — the Deflated Sharpe Ratio (DSR) re-ranking, the Sortino objective wiring, the OOS-cascade penalty scalars, the recency-decay weighting, and the walk-forward sample-size accounting. The audit and the methodology auditor concluded this layer needs a coherent re-specification, not point-patches: the DSR is fed the wrong moments AND the wrong input statistic.

Audited at `main @ 53ef340`; branches from `main @ 6dc4cf2` (post-Cluster-3). Line numbers reference the audit SHA — re-locate against current code.

## Acceptance Criteria

- [ ] AC-1 (H-5 — DSR moment provenance): `compute_deflated_sharpe_ratio`'s non-normality terms γ3/γ4 are computed from the strategy's OWN per-period return series (the `daily_returns` the objective was computed over), NOT from the cross-trial distribution of trial scores. The cross-trial mean/std remain ONLY in the expected-max-Sharpe `SR_0` term.
- [ ] AC-2 (H-6 — DSR metric consistency): a Sortino ratio is no longer passed as `SR_obs` into the Sharpe-derived DSR Eq.9. The selection-bias correction is made metric-consistent with the objective actually being optimized (Sortino). See Decision D3 — the objective STAYS Sortino; the team selects the specific correct correction with cited justification.
- [ ] AC-3 (H-7 + L-2 — dead code + T): `compute_dsr_T` (dead code on main, and the `n_symphonies` T-inflation it would apply is statistically invalid for correlated streams) is DELETED along with its tests. The DSR `T` is computed inline as the in-sample return-OBSERVATION count (`len(daily_returns)`), not the validation-fold calendar-day count.
- [ ] AC-4 (H-10 — penalty scalars): every `run_simulation` penalty scalar and threshold (1.5 / 0.75 / 2.0, thresholds 1.0 / 1.5) becomes a named module constant with a sourced rationale comment; the objective is documented as an explicit loss-averse utility; a fixture-backed test proves the combined objective preserves correct ordering on a hand-built better-vs-worse policy pair.
- [ ] AC-5 (M-3 — SR_0 null): when the expected-max-Sharpe expression is consumed as the DSR benchmark `SR_0`, it is evaluated under the zero-skill null (mean term = 0), keeping the spread term — or, if a "beat-the-average-trial" benchmark is intended, it is renamed so it is not presented as Bailey & López de Prado's `SR_0`.
- [ ] AC-6 (M-8 — recency weighting): the recency-decay weighting consumed by `compute_sortino_ratio` is either normalized by `Σ weights` (a proper weighted Sortino) or removed from the selection objective; whichever is chosen is documented with rationale. The unnormalized-sum interaction with the non-scale-invariant DSR numerator is eliminated.
- [ ] AC-7 (bare except): `calculate_historical_deviation`'s bare `except` is replaced with a specific exception set (`json.JSONDecodeError`, `OSError`, `KeyError`, `ValueError`) and a logged WARNING naming the file.
- [ ] AC-8 (expected-max-Sharpe overflow): `compute_expected_max_sharpe` clamps its `norm.ppf` arguments below 1.0 so a very large `n_trials` cannot overflow `SR_0` to `+inf`.
- [ ] AC-9 (M-7 — embargo): `EMBARGO_DAYS` is sized to (or documented against) the empirically estimated serial-dependence horizon of the per-day guard-alpha series; if that horizon is ≤1 day the current value is vindicated with a sourced comment.
- [ ] AC-10 (regression): every changed layer ships a golden-fixture or property test; the full suite stays green; DSR re-ranking outputs shift (intentional — the DSR was mis-specified) — characterization tests are re-pinned from the corrected statistics with provenance.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| D3 — DSR metric consistency (H-6) | The optimization objective STAYS Sortino. The selection-bias correction is made metric-consistent with it. The team (quant-test-writer + risk-engine-specialist) selects the specific correction — a metric-agnostic multiple-testing haircut applied to the selected statistic (Harvey & Liu 2015 style) or a downside-deviation analogue of the DSR — with cited justification. If the methodology is genuinely unsettled, escalate to team-lead and a `quant-risk-researcher` consult will be commissioned mid-cycle. | Sortino (downside deviation, capital-preservation focus) is AlphaBot's deliberate objective — changing it to Sharpe is a strategy change, out of remediation scope. The defect is feeding a Sortino into a Sharpe-derived formula; fix the correction, not the objective. |
| D4 — compute_dsr_T (H-7) | DELETE it and its tests. Do not wire it in; do not introduce the `n_symphonies` T-inflation. | The function is dead on main; the `n_symphonies` multiplication double-counts cross-sectionally correlated days and inflates significance — statistically invalid. The inline `T` is the more-correct path; just fix it to count return observations (AC-3). |

### D3 — RESOLVED (2026-05-22, team `autotuner-stats-fixes`; risk-engine-specialist determination, team-lead ruling)

The Sharpe-derived Deflated Sharpe Ratio (Bailey & López de Prado 2014 Eq. 9) is **REMOVED** from the autotuner selection path — not patched. It is replaced by a **Harvey & Liu 2015 multiple-testing haircut** applied to the Sortino.

- **Why remove, not patch:** Eq. 9's denominator `sqrt(1 - γ3·SR + (γ4-1)/4·SR²)` is the asymptotic standard error of the *Sharpe* estimator (a mean / full-standard-deviation statistic; Mertens 2002, Bailey & López de Prado 2012 PSR). The Sortino's denominator is downside deviation only; its sampling distribution is different and Eq. 9 does not describe it. No result in the literature licenses substituting a Sortino for SR in Eq. 9. A "downside-deviation DSR analogue" is **not shippable** — no peer-reviewed closed-form deflation for the downside-deviation estimator's standard error exists; deriving one would be original research, forbidden by the project no-unsourced-math rule and the global "adopt existing contracts, never invent" rule.
- **The haircut (Harvey & Liu 2015, "Backtesting", JPM 42(1), DOI 10.3905/jpm.2015.42.1.013 — metric-agnostic):** per trial `i` over the `N` sentinel-filtered completed trials: (1) t-statistic `t_i = Sortino_i · sqrt(T_i)` with `T_i = len(daily_returns_i)` the per-trial return-observation count; (2) one-sided p-value `p_i = 1 - Φ(t_i)` (large-sample normal survival); (3) **Benjamini-Hochberg (BHY)** step-up adjustment across the N p-values → adjusted p-value `p_adj_i`; (4) selection: `argmin p_adj_i`, gated by `p_adj_i <= HARVEY_LIU_FDR_Q` (FDR level, conventional 0.05, a named policy dial). If no trial clears the gate, the AI proposal falls through to the fallback/default cascade.
- **BHY (not Bonferroni/Holm):** FDR control is correct for a best-of-N selection; Bonferroni at N≈500 is brutally over-conservative.
- **Consequences:** `compute_dsr_T` is deleted unconditionally (D4). `compute_deflated_sharpe_ratio` and `compute_expected_max_sharpe` are unwired from the selection path; the implementer then greps the full production caller set — **no callers remain → both functions and their dedicated tests are DELETED** (project no-dead-code standard; keeping a dead function to dodge a golden-fixture obligation is itself the forbidden hack). AC-1/AC-5 collapse to "the removed Sharpe machinery cannot recur on the selection path"; AC-2 becomes "the H&L haircut is correctly applied and actually gates trial selection"; AC-8 is moot once `compute_expected_max_sharpe` is deleted. The H&L haircut is new autotuner math → golden-fixture test with hand-derived BHY expecteds. The `deflated_sharpe` DB column / Discord surfacing survive, repurposed to carry the winner's adjusted statistic.

### D5 — RESOLVED (2026-05-22): AC-6 recency weighting

The exponential recency-decay weighting (`_GUARD_ALPHA_DECAY_RATE`) is **REMOVED** from the selection objective, not normalized. Walk-forward CV already provides recency relevance by testing on the most recent fold; an in-objective decay weight double-counts recency and biases parameter selection toward the last ~6 weeks. Removal also eliminates the unnormalized-weighted-sum × scale-sensitive-t-stat interaction (the H&L t-stat `Sortino·sqrt(T)` is scale-sensitive, so fold-dependent weight mass would distort the selection gate) and deletes the unsourced `0.015` half-life constant. `_collect_sim_returns` / `run_simulation` append raw `guard_alpha`; `compute_sortino_ratio`'s plain-`n` denominator is then correct.

## Architecture

| Finding | File / function | Change |
|---|---|---|
| H-5 | `autotuner.py` (DSR γ3/γ4 computation) | source the non-normality moments from the return series |
| H-6 | `autotuner.py` DSR call sites / `compute_deflated_sharpe_ratio` | metric-consistent correction per D3 |
| H-7 / L-2 | `autotuner.py` `compute_dsr_T` + DSR `T` call sites | delete dead function + tests; inline `T = len(daily_returns)` |
| H-10 | `autotuner.py` `run_simulation` penalty block | named constants + sourced rationale + ordering-preservation test |
| M-3 | `math_engine.py` `compute_expected_max_sharpe` / its DSR consumer | null-mean `SR_0` |
| M-8 | `autotuner.py` recency-decay weighting + `compute_sortino_ratio` | normalize by Σweights or remove from the objective |
| bare except | `autotuner.py` `calculate_historical_deviation` | specific exceptions + WARNING log |
| overflow | `math_engine.py` `compute_expected_max_sharpe` | clamp `norm.ppf` args below 1.0 |
| M-7 | `autotuner.py` `EMBARGO_DAYS` | size/document against the serial-dependence horizon |

## Edge Cases

- A trial whose return series has fewer observations than the DSR `T` floor (`T<=1` sentinel).
- `n_trials` extremely large (the `norm.ppf` overflow — AC-8).
- A return series with zero downside deviation (the existing `1e6` Sortino sentinel must survive).
- Recency weights summing to zero / a single observation (AC-6 normalization guard).
- The DSR re-ranking changes which trial is selected — the OOS cascade output shifts; re-pin, do not regress-pin the old wrong ranking.

## Security Considerations

Internal autotuner statistics change. No new user input, no external calls, no auth surface. Offline (the autotuner does not place orders). `quant-code-reviewer`'s gates apply — especially Gate 6 (the penalty scalars and any new statistical constant must be named + sourced).

## Testing Strategy

- Golden-fixture tests for the corrected DSR (moments from the return series; metric-consistent deflation), the null-mean `SR_0`, the normalized weighted Sortino, the `norm.ppf` clamp.
- A fixture-backed ordering-preservation test for the `run_simulation` objective (AC-4) — a known better-vs-worse policy pair must rank correctly.
- DSR/Sortino assertions derive expecteds from independently hand-computed values, not from the producer (no regression-pinning the old wrong behavior).
- Full suite green; re-pinned characterization tests from the corrected statistics.

## Scope Boundaries

- **IN**: `autotuner.py` selection-statistics — DSR moment provenance + metric-consistent deflation, `compute_dsr_T` deletion, `T` observation-count, penalty scalars, recency-decay weighting, `calculate_historical_deviation` exception handling, `EMBARGO_DAYS` sizing; `math_engine.py` `compute_expected_max_sharpe` (overflow clamp + null-mean `SR_0`).
- **OUT**: the autotuner replay exit logic (Cluster 3, merged); `synthetic_history` fetch-window / timezone (Cluster 5); portfolio / analytics (Cluster 6); the walk-forward purge/embargo *split mechanics* — the audit verified purge/embargo are applied correctly at both boundaries, so only `EMBARGO_DAYS` *sizing* (AC-9) is in scope, not the split logic. The autotuner port-level replay blind spot was guarded in Cluster 3.
