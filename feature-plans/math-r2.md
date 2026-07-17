# Feature: Math Remediation R2 — Honest Validation Statistics (CPCV + adoption + frozen-eval + search-path regime wiring + quantstats units)
Status: ready
Created: 2026-07-17
Program: feature-plans/math-remediation-program.md · Findings basis: docs/audit/math-audit/VERDICT.md @ audit/app-math (DE-MATH-AUDIT-001) · Predecessor: DE-MATH-R1-001 (shipped PR #97 @ c38af283) · Design authority: domain correctness (operator delegation 2026-07-17). Authored by the PM under the standing autonomy directive; assumptions marked [PM-ASSUMED].

## Summary
Make the autotuner's validation statistics honest. Today the CPCV cross-validation is a structural no-op (MA-2 CRITICAL): `_aggregate_cpcv_paths` (autotuner.py:563-590) reads only `test_dates`, `train_dates` + all purge/embargo arithmetic have zero consumers, and each of the 6 groups lands in each of the 5 paths exactly once — so every path is the identical full ~200-day in-sample window, the 5 per-path scores are identical, and trial selection is in-sample dressed as walk-forward validation (the code's own comment at :2337-2339 admits each path covers the full window). Downstream: the adoption cascade's "OOS validation" scores the Optuna winner on `history_test = history_validation_full` — a subset of its own selection window — with `purge_integrity_ok=True` hardcoded as a false attestation (MA-5), and frozen-eval returns None under the production CRRA objective, so 20% of the data budget buys zero measurement (MA-9). This cycle also clears R1's xfail tripwire (regime-conditional exit ticks into the undated Optuna search path) and fixes the advisor-tab quantstats unit corruption (M1). Trade-touching (autotuner selection/adoption feeds live params) → PR ship path.

## Acceptance Criteria
- [ ] **AC-1 (MA-2 CRITICAL):** CPCV paths score genuinely disjoint test folds — the path aggregation consumes the fold structure as designed (per-path composite of that path's test folds ONLY, with `train_dates`/purge/embargo actually constraining what each fold's replay scores), so different paths produce genuinely different window compositions. The audit's lead probe becomes a regression pin: 5×identical-full-window path scores must be IMPOSSIBLE post-fix (a test asserts path test-window sets are pairwise distinct and per-path scores are not structurally identical). **[PM-ASSUMED] preference: consume the existing CPCV machinery as designed** (N=6/k=2/15 splits/5 paths already built); if investigation shows genuine consumption is intractable in-cycle, ESCALATE with evidence for the charter's explicit fallback ruling (honest purged single-fold split) — never silently choose.
- [ ] **AC-2 (MA-5):** the adoption cascade's "OOS validation" evaluates the Optuna winner on data OUTSIDE its selection window (a genuine holdout consistent with AC-1's fold structure); baselines and the AI arm are evaluated symmetrically (no pro-adoption asymmetry); the hardcoded `purge_integrity_ok=True` (:2714) false attestation is KILLED — computed for real or removed with its consumers reconciled.
- [ ] **AC-3 (MA-9):** frozen-eval produces a REAL metric under the production CRRA-EU objective (no more `frozen_eval_sharpe_value = None` on the crra_eu branch at :2678-2690), and that metric is reported AND consumed by at least one gate or persisted surface — the "honest post-selection metric" the design promises must exist and be visible.
- [ ] **AC-4 (R1 tripwire clearance):** regime-conditional `exit_confirm_ticks` wired into the undated search path (`_collect_sim_returns` / `run_simulation` / `run_simulation_crra_eu`) reusing R1's `_replay_resolve_regime_exit_ticks` — Optuna's per-trial score and the selection/diagnostic layer finally share ONE exit semantic. `tests/autotuner/test_ac4_r2_residual_tripwire.py` XPASSes and its xfail marker is REMOVED (the test passes for real). The pre-existing ≥20-day-history test assertions r1-tuner flagged as blast radius get root-cause triage (stale-expectation vs regression) — never blind make-green.
- [ ] **AC-5 (M1):** the advisor-path quantstats unit boundary is fixed — `analytics.py:370-375` compounds what `composer_backtest_client.py:182` emits as log×100 returns as if they were simple percent (CAGR −2pp/yr @1x vol, −30pp/yr @3x, Calmar-gate levels wrong). One conversion at the boundary, named constants, golden fixture reproducing the audit's error magnitudes; absolute metrics AND deltas corrected.
- [ ] **AC-6 (no-regression):** R1's entire battery (parity, goldens, N-3, AC-8 pins) stays green; `alpha_bot_execution.py`/`math_engine.py` carry ZERO diff (structural, same as R1); the charter's exit criterion lands as a test — a bounded selection/adoption run whose numbers are demonstrably out-of-sample (probe harness kept as a regression test).

## Architecture
Surfaces: `autotuner.py` (CPCV aggregation :563-590, adoption cascade :2606-2718, frozen-eval :2678-2690, undated replay loop — optuna-specialist territory), `analytics.py` + `advisors/composer_backtest_client.py` unit boundary (AC-5 — risk-engine-specialist territory), `math_engine.py` READ-ONLY, `alpha_bot_execution.py` ZERO diff. Fork base `3835f8e6` (current origin/main). No routes, no UI, no schema.

## Design-System Mapping
N/A — no UI surface.

## Edge Cases
Symphonies with short history (fold structure must degrade honestly, not fabricate folds); purge/embargo consuming days at fold boundaries (n_effective accounting stays additive); identical-scores-by-coincidence vs structurally-identical (the AC-1 pin must discriminate); regime resolver at the undated path's per-day boundary (same no-lookahead contract as R1); quantstats series with zero-vol/empty windows (existing `_safe` idiom preserved); trials whose fold coverage is empty → honest rejection, never a fabricated score.

## Security Considerations
No new inputs, routes, credentials, or write paths. Replay/validation remains offline computation; no order surface touched.

## Testing Strategy
TDD via /tdd → /tdd-implement → /tdd-finalize, quant-test-writer adversarial (math-layer ⇒ golden-fixture rule). RED first on: the pairwise-distinct-path pin + not-structurally-identical scores (AC-1), holdout-disjointness + symmetric-arms + attestation-killed (AC-2), frozen-eval-real-metric + consumed (AC-3), tripwire XPASS conversion + blast-radius triage (AC-4), quantstats unit golden (AC-5), the demonstrably-OOS exit-criterion probe (AC-6). Targeted `-n0` batteries only; credential-less pass; **both ruff gates in every formal GREEN report (standing rule)**; R1 battery as regression floor. Run tests/execution/ + tests/math_engine/ for any shared-surface touch.

## Decisions
| Decision | Rationale |
|----------|-----------|
| Prefer genuine CPCV consumption over single-fold fallback | The machinery exists and PBO/t-stat consumers assume path multiplicity; fallback only on evidence, via escalation (charter permits either) |
| AC-4 rides R2, not R3 | The undated surfaces are being rebuilt here anyway (ADDENDUM 6 ruling); the R3 retune is gated on the tripwire clearing |
| M1 folded into R2 | Same statistics-honesty theme, small boundary fix, keeps R4 pure advisor-classification work |
| Retune still NOT this cycle | R3, gated on R1+R2 + the pre-retune checklist (parabolic variance demo, 300-path band-edge stability, tripwire XPASS) |

## Scope Boundaries
- **IN**: AC-1..6 above; the charter's exit-criterion probe as a kept regression test.
- **OUT**: any retune (R3); MA-4 disarm band + MA-11 dead knob (R3); F7 MC-prob display fabrication (own small cycle after R2); MAPERF-15 (gated on the Composer post-sale semantics research, running in parallel); advisor classification re-base (R4); any live-path behavior change.
