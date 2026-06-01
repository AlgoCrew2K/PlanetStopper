# Feature Plan: Autotuner Walk-Forward Overhaul (CPCV + PBO gate + window extension)
Status: GATE-2 APPROVED 2026-06-01 — Option A (PBO gate; DSR ruled out by D3). Build phased; Phase 2 (CPCV) starts after the OC-fix merge; Phase 1 feed pending the SIP A/B + the free Alpaca key.
Created: 2026-06-01

## Decisions locked (2026-06-01)
- **Gate-2 = Option A**: PBO acceptance gate (objective-agnostic). NO DSR on the selection path (Decision D3 / H-6 category error). DSR reporting-only would require a logged D3 amendment.
- **Data feed = free IEX, train/serve-consistent**: the live engine pins `feed=iex` on both daily (`alpha_bot_execution.py:325`) and intraday VWAP (`:409`) per the 2026-05-12 feed-pinning decision (`tests/alpaca/test_feed_pinning.py`), matching `synthetic_history.py:282`. The overhaul runs on free IEX (depth is free). Buying full-volume *training* data while live stays IEX would REINTRODUCE a train/serve mismatch — not done.
- **RESOLVED (2026-06-01) — NO SIP, stay free IEX ($0)**: a load-bearing analysis of the live `exit_triggers` data found VWAP is NOT the deciding protective exit for the 4 thin-volume symphonies (`8FAXAnQmYi1INDubazeC`, `n2ooAZTvBRN6ZzpMmWmU`, `qF5ZU7ALjrlhxrGEwsyJ`, `hvPiGP1O7AHfutHE3Fjy`). Evidence: System-A VWAP Breakdown (the profit-protection exit that degraded IEX coverage would impair) = **0 binding live exits** for these names; the only VWAP wins are deep-loss Bleed Cuts (−1.6%, −4.2%) that the MC-armed Trailing Stop backstops; VWAP params were economically inert in the re-tune (reverted-to-fallback / OOS dominated by non-VWAP P&L). $99/mo SIP would sharpen a layer that isn't protecting these symphonies — not worth it. NOTE: the data for an A/B (and even a full SIP training rebuild) is ~$0 one-time via Databento free credits ($125 covers the ~1GB pull); the only real SIP cost is the recurring *live* feed — moot given the verdict. CAVEAT: live attribution is a 23-event sample; verdict is mechanism-grounded. REVISIT trigger: a rise in live System-A VWAP Breakdown binding events for these names (currently 0) — and persist richer per-exit attribution (the `also_true` co-fire field exists but is unused) to enable a data-rich re-decision.
- **Multi-account A/C (hard requirement)**: ROTH is the only funded account today, but the re-tune + overhaul MUST handle 0/1/N accounts gracefully (INDIVIDUAL/TRAD if opened) — verify the `account_uuids` iteration + per-account tuning is robust, not ROTH-hardcoded.

## Gate-1 (WHAT) — approved by user 2026-06-01
Strengthen the autotuner walk-forward validation: adopt CPCV, add a robustness/overfitting acceptance gate, and extend the window from 125 → ~250 trading days (~1yr). FREE Alpaca IEX data only (no paid SIP/sources). Motivation: the 125-day window collapses to ~4 usable OOS validation days after the 60/20/20 split + purge(20)/embargo(1); the re-tune empirically showed 10/11 symphonies reverting to fallback because the gate can't establish significance on 4 days.

## CRITICAL prior-decision constraint discovered in the code (Decision D3 / H-6)
A Sharpe-derived DSR was ALREADY built and DELIBERATELY DELETED under Decision D3 (`feature-plans/autotuner-statistics.md`), pinned by `tests/autotuner/test_c4_dsr_machinery_removed.py` (asserts `compute_deflated_sharpe_ratio`, `compute_expected_max_sharpe`, `_GAMMA_EULER_MASCHERONI` are ABSENT; forbids `gamma3`/`gamma4`/"Eq.9" tokens). Reason: deflating a **CRRA-EU / Sortino** objective with a **Sharpe** sampling distribution is the "H-6 category error." The production objective is CRRA-EU (`_objective_kind=="crra_eu"`), NOT Sharpe.
=> The new gate must be **PBO** (Probability of Backtest Overfitting, via CSCV — objective-AGNOSTIC), NOT a DSR gate. DSR may only appear as a reporting/display scalar on a genuine per-path Sharpe series, never on the CRRA selection path — and even that reintroduces the Euler-Mascheroni constant, which requires a LOGGED AMENDMENT to the D3 test (a user/council decision, not a silent edit).

## Phase 0 (no code) — user decision required
Choose: **(A, recommended)** PBO-only acceptance veto (composable on any per-path score; does not reopen D3) + DSR as reporting-only (needs a small D3 amendment for the Euler-Mascheroni display constant); or **(B)** full DSR on the selection path (requires a full D3 council Amendment; likely a Gate-1 fail). Plan below assumes (A).

## Phase 1 — Window extension (ships first, lowest risk, high value)
- `synthetic_history.py:74` `_WALK_FORWARD_TRADING_DAYS 125 → 250` (propagates to `_REQUIRED_FETCH_TRADING_DAYS`, `compute_fetch_window_start`, the `[-N:]` slice).
- `synthetic_history.py:441-446` bump cache marker `v3 → v4` (MANDATORY backward-compat: a v3 125-day file would under-deliver under a 250-day expectation → `HistoryShortfallError`; v4 forces regenerate; old v3 orphaned/harmless).
- `autotuner.py` replace literal `125` in docstrings/prints; CRITICAL: `_OOS_USABLE_VALIDATION_DAYS_EXPECTED = int(125*VALIDATION_RATIO)` (line ~372) must reference the shared constant so it can't drift; recompute pin `int(250*0.2)-20-1 = 29`.
- Fresh-fetch implication: one-time fresh **free IEX** fetch on first run post-merge (URL `&feed=iex` unchanged) — needs the free Alpaca key in env. `fetch_daily_bars_with_floor` (≤3 widen attempts) already handles initial under-delivery.
- Tests: Amend `oos_fold_collapse_pin.json` (`history_length→250`, `usable_validation_days→29`) as a logged Amendment; new test that v4 key is emitted + v3 not loaded.
- Result: usable OOS fold ~4 → ~29 days. Independently valuable even if 2/3 slip.

## Phase 2 — CPCV (ships second; mechanics testable on the existing 125-day cache)
- Params: **N=6 groups, k=2 → C(6,2)=15 splits → φ[6,2]=5 backtest paths** (the sources.md worked example). ~42 usable OOS days/fold vs ~4 today.
- New pure helpers in `autotuner.py`: `_generate_cpcv_folds(sorted_dates, n_groups, k, purge, embargo)` (reuses existing per-seam purge/embargo arithmetic) + `_aggregate_cpcv_paths(...)` (stitch 5 φ-paths). Pure functions of a DATE LIST → testable on synthetic dates + the 125-day cache with NO dependence on the 250-day fetch (decouples from Phase 1).
- Objective rewiring: score the Optuna objective on a **CPCV aggregate (mean across paths) per trial** (NOT 15× per trial — see compute risk). Frozen-eval RETAINED as the honest post-selection read (CPCV augments, doesn't replace it).
- Multiplicity: `n_optuna` and `n_effective`/BHY are UNTOUCHED — CPCV changes WHAT data each trial scores on, not how many tests. Must NOT inflate N by C(N,k). Anti-double-count regression test required.
- Tests: golden fixtures for split membership + path assembly (hand-derived); leakage property test on EVERY seam of EVERY split (`train ∩ test = ∅`, embargo gap ≥ EMBARGO_DAYS, no feature-window overlap).

## Phase 3 — PBO acceptance gate (ships last; highest risk = D3)
- New STAGE-1 hard veto in `acceptance_gate.evaluate_acceptance_gate`, sequenced AFTER the existing BHY/NN1/purge vetoes. Compute PBO (CSCV rank-degradation) from the 5 CPCV paths for the BHY-winning config; `pbo > PBO_REJECT_THRESHOLD` (named constant ~0.5, sourced) → REJECT_VETO_FAILED. DSR computed as a reporting scalar in `panel_breakdown` only (never a selection-path veto).
- Orthogonality (no double-count): BHY/`n_effective` = multiplicity axis (how many trials); PBO = sample-robustness axis (in-sample vs OOS rank stability across splits). Composable; documented in both docstrings.
- `math_engine.py`: new `compute_pbo(...)` + a reporting-only DSR fn under a NEW name (NOT `compute_deflated_sharpe_ratio` — that name is asserted absent). The Euler-Mascheroni reintroduction = logged Amendment to `test_c4_dsr_machinery_removed.py`.
- `database.py` additive migration `026` (NULLable+DEFAULT) for `pbo`/`dsr` columns on `autotune_runs`.

## Risks (ranked)
1. D3 reopening (HIGH) — mitigate via Phase-0 (A): PBO gate, DSR reporting-only, logged amendment.
2. Compute blowup 15× (HIGH) — score objective on CPCV AGGREGATE per trial (multiplier ~1× for selection); reserve the 15-split expansion for post-selection PBO on the single winner (165 sims, negligible). Also trim `OPTUNA_N_TRIALS_PRODUCTION` toward the MinBTL-supportable count (methodology Amendment, pinned by `test_optuna_n_trials_named.py`).
3. CPCV leakage (HIGH) — per-seam leakage property test over all 15 splits.
4. Breaking BHY/`n_effective` (HIGH) — keep `_haircut_select`/`compute_n_effective` byte-identical except receiving CPCV-aggregated series; anti-double-count regression test.
5. Reproducibility (MED) — derive all seeds from `OPTUNA_SAMPLER_SEED` + split index; CPCV-determinism golden fixture.
6. Live-vs-replay boundary (MED) — all new code stays in autotuner/acceptance_gate/math_engine; import-boundary test that `alpha_bot_execution` never imports CPCV/PBO.

## Decomposition (each a Toxic Pair TDD cycle, independently mergeable)
- Phase 1: quant-test-writer + risk-engine-specialist + sqlite-specialist (cache key) + reviewer. Needs free key for the fetch.
- Phase 2: + optuna-specialist. Testable on the 125-day cache; no key needed.
- Phase 3: + quant-test-writer (math-layer adversarial). Depends on Phase 2; carries the D3 amendment.

## Critical files
autotuner.py · synthetic_history.py · acceptance_gate.py · math_engine.py · tests/autotuner/test_c4_dsr_machinery_removed.py (D3 tripwire) · docs/research/optuna/sources.md (formulas) · feature-plans/autotuner-statistics.md (D3)
