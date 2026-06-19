# Feature: Post-Audit Hardening — Cluster 7

Status: ready
Created: 2026-05-22

## Summary

Cluster 7 of the AlphaBot v3 math-audit remediation — the post-audit hardening pass. The independent post-remediation audit at `main @ 3b7d775` caught three substantive findings the 6-cluster remediation team graded as done (RM-H1 Sortino-Wald survival, E-1 intraday-rotation exit-state leak, INV-COV-1/2/3 regex-not-runtime tests) plus a quantified perf watch (shadow-trajectory warm-cache). This cluster closes the HIGH list. When it merges, the math audit *and* its independent re-audit are both fully discharged.

Branches from `main @ 3b7d775`. The audit reports + the consolidated brief are at `C:\Users\paulm\Documents\Projects\POC\AlphaBotPM\docs\research\math-audit-2\`.

## Acceptance Criteria

- [ ] AC-1 (RM-H1 — bootstrap Sortino SE): `compute_sortino_tstat` no longer uses Sharpe-specific Wald scaling. The t-statistic is constructed as `t = Sortino / SE_bootstrap` where `SE_bootstrap` is computed by bootstrap-resampling the trial's per-day return series (≥1000 resamples per Efron 1979; the resample count is a named constant with a sourced comment). The code comment "metric-neutral bridge" is corrected to honestly describe the bootstrap construction. Golden fixtures are recomputed from independently hand-derived bootstrap SEs. The BHY step-up + clamp machinery stays unchanged.
- [ ] AC-2 (E-1 / Cluster 6 CASE 2 / INV-COV-5 — intraday-rotation reset): on every execution cycle, per-symphony, AlphaBot detects holdings transitions zero→positive. On detection: bot_state[symphony_id]'s transient fields (`triggered`, stop levels, `breakeven_locked`, `prev_return`, `high_water_mark`, the position_epoch, and any other transient tracking) reset to a fresh-position baseline, and a new `position_epoch` is minted. Covers BOTH the AlphaBot-exit-then-Composer-rebuy case (same-session, 3:50-3:55 ET typical) AND the untriggered Composer-rotation case (Cluster 6 CASE 2). Pin tests include: an 11:00 ET AlphaBot exit followed by a 3:55 ET Composer rebuy in the same session; a multi-day no-rebuy then next-day-rebuy; a Composer-rotates-out-and-back-in without an AlphaBot exit; the carried-position-across-day case (no reset needed beyond the existing daily wipe). Closes the broader pattern — three convergent findings, one mechanism.
- [ ] AC-3 (N-1 — frozen_eval_sharpe reset on rejection): when the haircut/cascade demotes the AI proposal to Fallback/Default, `frozen_eval_sharpe` is set to None (matching `run_autotuner`'s symmetric reset path). The operator-facing column never carries the Sharpe of a rejected proposal as if it were the deployed parameter set's Sharpe. Test: pin both the accepted and rejected paths.
- [ ] AC-4 (RM-M2 — BHY clamp resize): `_HAIRCUT_PVALUE_EPSILON` is raised from `1e-12` to `q / (N · c(N))` (with `q = HARVEY_LIU_FDR_Q`, `N = MAX_OPTUNA_TRIALS`, `c(N) = sum_{j=1..N} 1/j`) — approximately `1.5e-5` at N=500, q=0.05. The clamp is a named constant with a comment explaining BOTH the IEEE-754 stability rationale AND the BHY-scaling-floor rationale that drove the resize. Pin test: a synthetic trial set where every t-stat exceeds the clamp threshold no longer collapses BHY into a no-op.
- [ ] AC-5 (N-3 — replay grace gate honors EXECUTION_START_TIME): the autotuner replay's VWAP open-window grace gate reads `EXECUTION_START_TIME` from env (not hardcoded `09:30 ET`). Pin test: a non-default `EXECUTION_START_TIME` value drives the same grace behavior end-to-end.
- [ ] AC-6 (INV-COV-1 — train|validation purge runtime test): the regex-only `test_o1_purge_embargo.py` train|validation check is replaced with a runtime date-set-spy test (the `test_o6_frozen_eval.py` Tests 3/4 pattern, back-ported). Asserts `set(train) ∩ set(validation_within_PURGE_DAYS) == ∅` against the actual splits the autotuner produces, not against text adjacency in source.
- [ ] AC-7 (INV-COV-2 — O6 second-boundary runtime test): same runtime conversion applied to the O6 second-boundary purge.
- [ ] AC-8 (INV-COV-3 — synthetic_history VWAP runtime test): the synthetic_history VWAP numeric value test (currently AST-only) is replaced with a runtime test that constructs a known input series and asserts the function's output against an independently hand-computed VWAP.
- [ ] AC-9 (INV-COV-4 — consumer-side stop-monotonicity audit): every consumer of `stop_level` / `active_trailing_stop` (reporting.py Discord embeds, app.py templates, dashboard tooltips, telemetry, charts) is audited for any stale "stop never decreases" assumption introduced before Cluster 1's HWM-anchored ratchet. Each consumer that handles the value gets a runtime pin test for the stop-decreasing case. If any consumer turns out to assume monotonic-up, it is corrected with a small additive fix.
- [x] AC-10 (migration 031 + RM-M1 citation): additive migration `031_shadow_history_sym_ts_index.sql` creates `idx_shadow_history_sym_ts ON shadow_history (symphony_id, ts_utc)` — closes the shadow-trajectory warm-cache perf watch (245×-9700× regression, dashboard-only). BUG-001 fix: file was orphaned as 016 (slot occupied by spec_bundles); renamed to 031 and registered in _MIGRATION_FILES after 030. Separately, `math_engine.py:273` Fu & Zhang citation initials corrected to "Fu, Y.B. & Zhang, Z.G." per Semantic Scholar verification. Bundled because both are one-line fixes.
- [ ] AC-11 (regression): every changed layer ships a golden-fixture or property test; the full tree stays green; behavior shifts (the bootstrap SE will re-rank haircut outcomes; the intraday-rotation reset will change bot_state lifecycle) re-pinned with provenance; genuine whole-tree count + HEAD SHA quoted in every handoff.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| D10 — RM-H1 methodology | Bootstrap Sortino SE (Option 2). `t = Sortino / SE_bootstrap` with ≥1000 resamples. | Preserves AlphaBot's deliberate downside-focused objective (Cluster 4 D3); practitioner-standard correctness fix when closed-form SE is unwieldy (Efron 1979); handles fat tails and small T better than the Sharpe-derived Wald scaling; implementation is a contained drop-in for the t-stat construction (BHY machinery untouched); computational cost is on the per-symphony per-day cadence, not the 1-minute execution path. |
| D11 — E-1 / CASE 2 scope reversal | The Cluster 6 D9(a) ruling that kept the intraday-rotation gap out of scope is REVERSED. Engine bookkeeping is now in scope. | Behavioral fact from the user: Composer re-buys at ~3:50-3:55 ET in the same session after an AlphaBot exit, making the exit-state-leak path active on every triggered exit. D9(a) rested on "display-only impact"; that premise is now false. The fix is one mechanism — holdings-transition tracking — that closes E-1, Cluster 6 CASE 2, and INV-COV-5 together. |
| D12 — Detection mechanism for AC-2 | Per-symphony holdings transition zero→positive on every execution cycle. | Deterministic, no Composer-API-specific knowledge needed (AlphaBot already polls holdings every cycle to detect rebalances). Fires on ANY transition (AlphaBot-caused or Composer-caused) — covers both E-1 and CASE 2 with one code path. |

## Architecture

| Finding | File / function | Change |
|---|---|---|
| RM-H1 | `autotuner.py:289-301` (`compute_sortino_tstat`) | Replace Wald scaling with `Sortino / SE_bootstrap`. Add named resample-count constant. Recompute golden fixtures. |
| E-1 / CASE 2 / INV-COV-5 | `alpha_bot_execution.py` (execution cycle) + `database.py` (bot_state schema if needed) + `analytics.py` (epoch mint integration) | Detect per-symphony zero→positive holdings transitions; reset bot_state transient fields + mint new position_epoch on transition. |
| N-1 | `autotuner.py` (proposal-cascade write path) | Reset `frozen_eval_sharpe` to None when haircut/cascade demotes. |
| RM-M2 | `autotuner.py:286` (`_HAIRCUT_PVALUE_EPSILON`) | Raise clamp to `q/(N·c(N))`; named, sourced. |
| N-3 | `autotuner.py` (replay grace gate) | Read `EXECUTION_START_TIME` from env. |
| INV-COV-1 | `tests/autotuner/test_o1_purge_embargo.py` | Regex → runtime date-set-spy. |
| INV-COV-2 | `tests/autotuner/test_o6_frozen_eval.py` (second boundary) | Same. |
| INV-COV-3 | `tests/synthetic_history/...` | AST → runtime VWAP fixture test. |
| INV-COV-4 | `reporting.py`, `app.py`, templates, telemetry | Consumer-side audit + pin tests + any required correction. |
| Migration 031 | `migrations/031_shadow_history_sym_ts_index.sql` | Additive composite index (BUG-001: renamed from orphaned 016 slot). |
| RM-M1 | `math_engine.py:273` | One-line citation initials fix. |

## Edge Cases

- A symphony's first cycle of the day (post-wipe): bot_state is fresh, holding may be positive — the AC-2 detection fires "new position" but the reset is idempotent. The new position_epoch mint should still happen for that day so per-day epochs are consistent with the existing wipe model.
- A symphony with zero allocation for an extended period: holding stays zero across many cycles; no reset, no new epoch (correct — no new position).
- AlphaBot exits at 3:55 ET (very late session, near Composer's trade window): the bot_state has triggered=True; Composer may or may not re-buy in the same minute. If Composer doesn't trade until 3:56, the next cycle reads holding=positive while triggered=True — the reset fires. If Composer doesn't re-buy at all, end-of-day wipe handles it.
- A symphony whose intraday holding fluctuates (partial fills, etc.) — the detection must not over-fire. Define "positive" with a tolerance (e.g. > some small absolute threshold or > 0 strict). The implementer chooses the right boundary with risk-engine-specialist's input.
- The bootstrap SE (AC-1) on a return series with very few observations (T < 5): bootstrap is unstable. Define a fallback (e.g. fall back to the existing Wald with an honest comment that calibration is degraded at small T, OR exclude such trials from the haircut pool entirely). risk-engine-specialist owns this boundary.
- Bootstrap SE on a constant return series (zero variance): SE_bootstrap = 0 → division by zero. Guard explicitly.

## Security Considerations

Internal engine + autotuner changes. No new user input, no external API contract change (the AC-2 detection consumes holdings AlphaBot already polls). The migration 016 index is additive/non-destructive. `quant-code-reviewer`'s gates apply — especially the live-trade-boundary gate (AC-2 touches the live execution path; the reset must not introduce a path that mishandles a live position).

## Testing Strategy

- Golden fixtures for the bootstrap-SE construction (AC-1): hand-compute SE from a fixed seed bootstrap; assert match to 1e-9.
- Property tests for AC-2: the carried-position case must NOT reset; the same-session-rebuy case MUST reset; the cross-day-rebuy case MUST reset (via existing wipe); the no-rebuy case must NOT reset (stays clean for next day's wipe).
- Pin a non-default `EXECUTION_START_TIME` for AC-5.
- Runtime date-set-spy for AC-6/AC-7; runtime VWAP fixture for AC-8.
- Consumer-side pin tests for every `stop_level` consumer (AC-9).
- Full tree green; characterization tests that shift under AC-1's re-ranked haircut outcomes re-pinned from independently hand-derived bootstrap values, never producer-pinned.

## Scope Boundaries

- **IN**: the HIGH-severity findings from `docs/research/math-audit-2/CONSOLIDATED__2026-05-22.md` (RM-H1, E-1/CASE 2/INV-COV-5, N-1, INV-COV-1/2/3/4, N-3) + RM-M2 (MEDIUM but trivial code change) + migration 016 + RM-M1.
- **OUT**: the remaining MEDIUM/LOW backlog (risk-math M1/M2/M3, INV-COV-6/7/8, E-4/5/6/7/8, N-4 NEEDS-VERIFICATION items, RM-L1/L2, etc.) — these go on a separate maintenance backlog. N-4 (Alpaca IEX RTH + tick_idx 0 == 09:30 assumption) is explicitly out because it's a `NEEDS BEHAVIORAL VERIFICATION` item that needs an Alpaca-feed check before fixing.
- If AC-2's detection mechanism turns out to need a Composer-API-specific assumption (e.g. about holdings-response shape or timing), the team escalates for a `composer-api-researcher` consult rather than expanding scope.

## Cycle Reference

This is Cluster 7, post-6-cluster remediation + post-independent-audit. When it merges, the math audit and its re-audit are both fully discharged. The remaining backlog after Cluster 7 is MEDIUM/LOW maintenance — separable, not blocking.
