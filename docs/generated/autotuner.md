# autotuner

> Optuna walk-forward optimizer: runs 500 trials per symphony over a 250-day sliding window, selects the best trial via the CRRA-EU objective + Harvey & Liu BHY haircut + CSCV PBO acceptance gate, and enforces NN1 spec-freeze discipline throughout. Also provides `run_calibration_sweep` — a separate, advisory-only 2-param sweep over `PARABOLIC_VELOCITY_THRESHOLD` and `VWAP_CROSS_HWM_PCT`.

**Source:** `autotuner.py`
**Last updated:** 2026-07-17 (Math Remediation R1 — replay fail-open arm (MA-10), regime-conditional exit ticks (F5), session-window action-phase gate (F6); prior: 2026-07-12, Workstream E)

## Overview

`autotuner.py` implements the per-symphony Bayesian optimization loop. At each autotuner run it:

1. Fetches 250 trading days of synthetic replay history via `synthetic_history.generate_synthetic_history`, passing `n_jobs=_AUTOTUNE_REPLAY_N_JOBS` (= 1) to bound intraday-replay parallelism on the 2-core / 3.0 GiB droplet (DE-AUTOTUNE-OOM). **Math Remediation R1 (`DE-MATH-R1-001`):** as of this cycle, the holdings this history carries are stamped with a REAL per-tick `last_percent_change` by `synthetic_history.build_replay_day` itself (see [synthetic_history](synthetic_history.md) — the fix lives there, not here) — this module's replay loop now receives genuinely price-sensitive MC opinions instead of a day-constant degenerate baseline.
2. Splits history 60/20/20 (train/validation/frozen-eval) with `PURGE_DAYS=20` and `EMBARGO_DAYS=1` per López de Prado 2018.
3. Builds CPCV folds via `_generate_cpcv_folds` (N=6 groups, k=2 test groups, 15 splits, 5 complete OOS paths) and aggregates paths via `_aggregate_cpcv_paths`.
4. Runs `OPTUNA_N_TRIALS_PRODUCTION` (500) Optuna TPE trials; each trial's objective uses the CRRA-EU branch (`run_simulation_crra_eu`) or the legacy Sortino branch. `locked_vars` keys are excluded from `suggest_*` calls.
5. Applies the Harvey & Liu BHY selection haircut via `_haircut_select`, using `compute_n_effective` to compute the honest `N_effective = N_optuna + S` multiple-testing count.
6. Applies STAGE-1 PBO veto gate via `math_engine.compute_pbo` on the top-`_CSCV_TOP_K` (20) pre-BHY configs: rejects if PBO > `PBO_REJECT_THRESHOLD` (0.5).
7. Enforces NN1 spec-freeze: the caller must supply `spec_bundle_id`; `run_autotuner` raises `ValueError` if it is `None`. The caller resolves it via `database.get_or_create_phase1_theory_bundle_id` before calling.
8. **Queries the DoF ledger** (`researcher_dof_ledger WHERE spec_bundle_id = ?` via `advisor_ro_query`) and sums `n_configs_searched` over `BACKTEST_SELECTION` rows into `s_count` (Workstream E, AC-E2, hoisted 2026-07-12 to run BEFORE step 9 so the sum is available to persist — previously this query ran only after the save, feeding solely the in-memory Overfitting Conscience call in step 10 below).
9. Persists the autotune run row via `database.save_autotune_run(..., s_count=...)` (returns int row id used by the OC producer). `s_count` feeds Indicator-3 (operator drift) on LATER runs via their own `prior_runs` query — see "s_count Wiring" below.
10. Post-walk-forward: invokes `run_overfitting_conscience`, `run_spec_critic` (with `symphony_id=None`, called once per bundle), `run_divergence_explainer` (each wrapped in `try/except logger.warning` — Advisor failures are non-fatal). `run_overfitting_conscience` reuses the SAME ledger rows queried in step 8 — the query was hoisted, not duplicated.

## API Reference

### Public Entry Point

#### `run_autotuner(bot_state, current_date_str, account_uuids, is_forced=False, spec_bundle_id: "int | None" = None) → dict`
Main entry point. Runs the full walk-forward optimization for one symphony.

Raises `ValueError` if `spec_bundle_id` is `None` — the caller must resolve it via `database.get_or_create_phase1_theory_bundle_id` before calling. This is the NN1 Phase-1 spec-freeze contract.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `bot_state` | `dict` | Current daemon state dict (all symphonies) |
| `current_date_str` | `str` | ISO-8601 date string for today |
| `account_uuids` | `list[str]` | Composer account UUIDs for this run |
| `is_forced` | `bool` | When True, bypasses market-hours guard |
| `spec_bundle_id` | `int \| None` | Phase-1 theory bundle id; raises ValueError if None |

**Returns:** dict with keys:
- `baseline_decision`: `"AI"`, `"fallback"`, or `"default"`
- `oos_alpha`: OOS guard-alpha of the selected strategy
- `train_alpha`: train-fold guard-alpha
- `fallback_oos_alpha`, `default_oos_alpha`: cascade alternatives
- `selection_tstat`: Harvey & Liu haircut t-statistic of the winning trial
- `n_effective`: `N_optuna + S`
- `spec_bundle_id`: bundle_hash of the active Phase-1 theory bundle
- `eval_window_days`: per-fold day-counts for operator visibility (OPTUNA-4)

---

### Walk-Forward Simulation

#### `run_simulation(params: dict, history_data: dict, acc_sym_ids: list, current_date_str: str, deviation_dict: dict) → float`
Runs the validation-fold simulation with `params` and returns the aggregate guard-alpha. Used for OOS re-validation in `ai_advisor.revalidate_suggestion_oos`. **R1 residual (`DE-MATH-R1-001` AC-4, tripwired):** this is one of the two UNDATED simulation entry points — it still resolves `exit_confirm_ticks` via `_replay_exit_tick`'s module default (`math_engine.EXIT_CONFIRM_TICKS`, not regime-conditional). Deferred to R2 (see `_replay_resolve_regime_exit_ticks` below); guarded by `tests/autotuner/test_ac4_r2_residual_tripwire.py`'s `xfail(strict=False)`, which will XPASS the day this changes.

#### `_collect_sim_returns(p, history_data, acc_sym_ids, current_date_str, deviation_dict) → list[float]`
Runs the guard-alpha simulation and returns per-triggered-day guard-alpha values. Shared tick loop with `run_simulation` via `_replay_exit_tick`. No recency decay (Decision D5). **R1 residual:** the second UNDATED entry point — same AC-4 deferral as `run_simulation` above.

#### `_collect_sim_returns_dated(p, history_data, acc_sym_ids, current_date_str, deviation_dict) → dict[str, float]`
Runs the guard-alpha simulation and returns a date-keyed dict of decimal-fraction returns. Used by `_haircut_select` to build `configs_date_returns` for `math_engine.compute_pbo` (STAGE-1 PBO veto). **Math Remediation R1 (AC-4/F5, `DE-MATH-R1-001`):** this is the DATED entry point, and the only one wired to regime-conditional `exit_confirm_ticks` this cycle — for each simulated date it calls `_replay_resolve_regime_exit_ticks` once (sorted-dates precomputed once per symphony, not resorted per date) and threads the result into `_replay_exit_tick`. Feeds the CSCV/PBO STAGE-1 veto gate and the BHY selection haircut, so the layer that decides which candidate parameters survive IS regime-faithful even though the undated search-score path (`run_simulation`/`_collect_sim_returns` above) is not yet.

#### `replay_exit_sequence(ticks, params, *, grace_minutes) → list[dict]`
Pure observability helper. Runs the per-tick exit loop over one day and returns one `{"tick_idx", "exit_reason", "armed", "tp_armed", "para_armed"}` dict per executed tick — exit_reason is the `resolve_trigger_priority` exit-reason string on the tick the position exits, `None` on every non-exit tick; the loop stops after the first exit (production commits the exit and freezes the symphony for the day). **Math Remediation R1 (AC-6, `DE-MATH-R1-001`):** the three `*_armed` keys are new this cycle — they mirror `test_c3_replay_exit_parity.py`'s `_production_exit_sequence` output shape so the AC-6 bar-level parity battery can compare per-tick STATE, not just the final exit decision. AC-4 is deliberately NOT wired through this function — day-level regime resolution lives one layer up, in `_collect_sim_returns_dated` above, and every AC-6 scenario uses `regime_label=None` to match this function's implicit default; extending this signature would break that alignment. Used by AC-6 parity tests.

#### `_replay_exit_tick(state, tick, tick_idx, n_ticks, p, grace_minutes, execution_start_hhmm: str = "09:30", exit_confirm_ticks: int = math_engine.EXIT_CONFIRM_TICKS) → str | None`
Single-tick exit core. All three simulation callers (`run_simulation`, `_collect_sim_returns`, `_collect_sim_returns_dated` via `replay_exit_sequence`) call this function — one canonical copy of the exit orchestration. Returns the `resolve_trigger_priority` exit-reason string when an exit fires, else `None`.

**Math Remediation R1 (`DE-MATH-R1-001`) — three fixes landed in this function:**
- **AC-5/F6 (session-window action-phase gate):** immediately after the unconditional DATA-phase HWM update, calls `_replay_in_action_phase(tick_idx, execution_start_hhmm)` and returns `None` before any decision logic if the action phase has not opened yet — mirroring production's `if current_time < market_open and not force_run: return` gate (`alpha_bot_execution.py:951-953`). Distinct from the pre-existing N-3 VWAP-grace suppression, which only silences VWAP signals inside a grace window AFTER the action phase has already opened.
- **AC-3/MA-10 (fail-open arm):** `should_arm` resets to `False` every tick and is set `True` either on the pre-existing `mc_available and take_profit_mc <= mc < trigger_threshold` condition OR, new this cycle, when `not mc_available` — an absent MC opinion now ARMS the protective stop (mirrors `alpha_bot_execution.py:1324-1326`, audit rule H-3) instead of leaving it dark. Disarm is unchanged: still requires an available, extreme MC reading with a positive return; MC-absent can never disarm.
- **AC-4/F5 (regime-conditional confirm ticks):** the new `exit_confirm_ticks` keyword param is passed explicitly into `math_engine.compute_exit_confirmation` rather than left to that function's own module-level default — defaults to the SAME `math_engine.EXIT_CONFIRM_TICKS` constant, so a caller that never resolves a regime label (both undated entry points, see above) sees byte-unchanged behavior.

`state` is a mutable dict carrying per-position transient state across ticks within a single day. `n_ticks` is the day's tick count, used to derive time_ratio from the actual session length.

#### `_replay_in_action_phase(tick_idx: int, execution_start_hhmm: str) → bool`
**New, Math Remediation R1 (AC-5/F6).** Returns `True` iff `tick_idx` is at or after `EXECUTION_START_TIME`'s session-open-anchored offset — i.e. production's ACTION PHASE would have run on this tick. Mirrors `alpha_bot_execution.py:951-953`'s hard gate; the DATA phase (HWM tracking) runs unconditionally from the true 09:30 open regardless of this gate.

#### `_replay_execution_start_offset_minutes(execution_start_hhmm: str) → int`
**New, Math Remediation R1.** Returns `EXECUTION_START_TIME`'s minute-bar offset past the 09:30 ET session open (tick_idx 0): `(h - 9) * 60 + (m - 30)`. Single source of truth for this formula, extracted this cycle so the pre-existing `_replay_in_open_window_grace` (N-3, VWAP-grace suppression) and the new `_replay_in_action_phase` (AC-5) can never drift apart — both now call this helper instead of each computing the offset inline.

#### `_replay_resolve_regime_exit_ticks(dates_data: dict, sorted_dates: list, date_idx: int) → int`
**New, Math Remediation R1 (AC-4/F5).** Recomputes the regime-conditional `exit_confirm_ticks` FRESH for one replay day, using ONLY EOD daily returns from dates strictly before `sorted_dates[date_idx]` (no lookahead). Resolves the regime label via `regime_classifier.classify_regime()` over the trailing `regime_classifier.MIN_LABEL_SERIES_LENGTH` (=20) days rather than reading `database.get_cached_regime_label` — that accessor is a single-row, latest-wins LIVE cache with no per-historical-date granularity, so consulting it during a walk-forward replay would inject today's label into every one of the ~250 replayed days (a lookahead violation). Insufficient trailing history → `classify_regime` returns `None` → `math_engine.apply_regime_exit_adjustment`'s own safe default fires (base ticks unchanged), never an invented replay-only fallback. Mirrors production's `apply_regime_exit_adjustment(regime_label, base_ticks)` composition (`alpha_bot_execution.py:1436-1448`) with a walk-forward-safe label source.

**R1 residual (accepted, `DE-MATH-R1-001` ADDENDUM 6):** only wired into `_collect_sim_returns_dated` (the CSCV/PBO selection path), NOT into the undated `run_simulation`/`_collect_sim_returns` (Optuna's per-trial search-score objective). Deferred to R2 — the unwired surfaces are exactly the objective-computation machinery R2's CPCV redesign rebuilds; wiring now would be immediately churned. Ruled a search-efficiency wart, not a shipped-decision correctness cliff, since the layer that decides which surviving params ship (the CSCV/PBO gate + BHY haircut) IS regime-faithful. **Binding rider: no R3 retune ships until this is wired** (the search objective itself must be regime-faithful before a retune is meaningful). Guarded by `tests/autotuner/test_ac4_r2_residual_tripwire.py` (`xfail(strict=False)` — XPASSes the day R2 wires the undated path).

---

### CPCV Fold Generation (Phase 2)

#### `_generate_cpcv_folds(sorted_dates, n_groups=_CPCV_N_GROUPS, k_test=_CPCV_K_TEST_GROUPS, purge_days=PURGE_DAYS, embargo_days=EMBARGO_DAYS) → list`
Partitions dates into Combinatorial Purged Cross-Validation (CPCV) folds. Generates every `C(n_groups, k_test)` = `C(6,2)` = 15 splits. Applies purge+embargo at EVERY train/test seam. Each fold descriptor dict contains:
- `train_dates` — effective (post-purge/embargo) training date set
- `test_dates` — raw test date set
- `path_membership` — list of path indices this split contributes to (variable length: adjacent pairs → length 1, non-adjacent → length 2; permissive `len >= 1` is correct)

Path assignment uses canonical mlfinlab first-available-slot algorithm.

#### `_aggregate_cpcv_paths(folds: list, n_paths: int = _CPCV_N_PATHS) → list`
Assembles `n_paths` OOS backtest paths from fold descriptors. Returns a list of `n_paths` sorted date lists.

**Known limitation (out of R1 scope — see `DE-MATH-AUDIT-001` MA-2, CRITICAL, R2 territory):** `_aggregate_cpcv_paths` reads only `test_dates`; `train_dates` and all purge/embargo arithmetic have zero consumers, so every one of the 5 assembled paths currently resolves to the identical full in-sample window — trial selection is in-sample dressed as walk-forward validation. Not touched by this cycle; deferred to R2.

---

### CRRA-EU Objective Branch

#### `run_simulation_crra_eu(params, history_data, acc_sym_ids, current_date_str, deviation_dict, *, gamma: float) → float`
Runs the Phase-1 CRRA-EU objective. Collects per-triggered-day percent returns via `_collect_sim_returns`, converts to decimal fractions (`/ RETURN_PCT_TO_FRACTION`), then calls `math_engine.compute_crra_eu_objective(daily_returns_fraction, gamma)`.

**Returns:** `float` — mean CRRA utility over triggered days. Returns `0.0` if no triggered days. Note: returns a single `float`, not a tuple.

---

### CRRA-EU Derivation Helpers

These functions live in `autotuner.py` (not `math_engine.py`) and are called by `_haircut_select` for the CRRA-EU branch.

#### `derive_wealth_argument(r_policy_fraction: float) → float`
Returns the raw per-period gross wealth ratio `W = 1 + r_policy_fraction` (W-H2 formula). The floor is NOT applied here.

#### `derive_floored_wealth_argument(r_policy_fraction: float) → float`
Returns `max(WEALTH_ARG_FLOOR, 1 + r_policy_fraction)`. Complete W-H2 + W-H4 construction. Call this before `compute_crra_utility`.

#### `compute_crra_eu_tstat(U_series: list[float]) → float`
Per-trial t-statistic for the CRRA-EU objective: `mean(U) / (sd(U) / sqrt(T))`. Uses sample stdev (ddof=1). Returns `0.0` for T ≤ 1 or a constant series. Do NOT use `compute_sortino_tstat` for CRRA-EU (H-6 category error).

---

### Harvey & Liu Selection Haircut

#### `_haircut_select(completed_trials, n_effective=None, tstat_fn=compute_sortino_tstat, gamma=None) → tuple`
Applies the BHY selection-bias haircut to a set of completed Optuna trials.

Steps per trial:
1. t-statistic via `tstat_fn(daily_returns)` (for CRRA-EU: U-transforms the returns first)
2. One-sided p-value via `compute_haircut_pvalue(t)`
3. BHY adjustment via `benjamini_hochberg_adjust` over `N_effective` p-values (Shape A: pads with `S` copies of 1.0)
4. Winner = argmin(p_adj); deployable iff `p_adj <= HARVEY_LIU_FDR_Q`

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `completed_trials` | `list` | Optuna trial objects with `value` and `user_attrs["daily_returns"]` |
| `n_effective` | `int \| None` | Honest N from `compute_n_effective`; defaults to `len(trials)` |
| `tstat_fn` | `callable` | `compute_sortino_tstat` (Sortino) or `compute_crra_eu_tstat` (CRRA-EU) |
| `gamma` | `float \| None` | CRRA gamma for U-transform; required when `tstat_fn` is CRRA-EU |

**Returns:** `(winner_trial, winner_p_adj, winner_tstat)` — all `None` when no trial clears the gate.

#### `compute_sortino_tstat(returns, seed: int = 0) → float`
Per-trial t-statistic via nonparametric bootstrap SE (Efron 1979): `Sortino / SE_bootstrap`. Returns `0.0` (conservative rejection) when bootstrap SE is unavailable. **Not the same seed-derivation context as `advisors.backtest_gate_engine.evaluate_candidate_batch`'s AC-D3 fix** — that call site now derives its seed from a stable hash of each candidate's own id (not batch position); this module's own `_haircut_select` caller uses `seed=trial_idx` within one fixed, never-reordered Optuna study, which was never order-dependent and was intentionally left untouched by the AC-D3 fix.

#### `compute_haircut_pvalue(t_stat: float) → float`
One-sided p-value `1 - Φ(t)`, clamped to `[_HAIRCUT_PVALUE_EPSILON, 1 - _HAIRCUT_PVALUE_EPSILON]`.

#### `benjamini_hochberg_adjust(p_values: list[float]) → list[float]`
BHY step-up adjustment with Yekutieli `c(N) = sum(1/j)` arbitrary-dependence factor. Returns one adjusted p-value per input in the original order.

#### `compute_n_effective(n_optuna: int, ledger_query, winning_spec_bundle_id: str | None = None) → int`
Returns `N_optuna + S`, the honest multiple-testing count. `S` = sum of `n_configs_searched` over `BACKTEST_SELECTION` ledger rows, excluding frozen-eval-tainted rows and the winning bundle. `ledger_query` is a callable injected for testability.

**Distinct from `s_count` (Workstream E):** `compute_n_effective`'s `S` is computed for THIS RUN's own N_effective (current-run I-1/I-2 math, unchanged by Workstream E) and excludes the winning bundle; the `s_count` persisted via `save_autotune_run` (see below) is a separate accumulator over ALL `BACKTEST_SELECTION` rows for the run's `spec_bundle_id` (no winning-bundle exclusion), consumed by LATER runs' `overfitting_conscience` drift check, not by this run's own haircut.

---

### NN1 Spec-Freeze Enforcement

#### `validate_search_space_nn1() → None`
Asserts that `OPTUNA_SEARCH_SPACE_KEYS` contains no frozen facets. Called at the top of `run_autotuner` before any trial runs.

#### `validate_nn1_compliance(spec_bundle_id: int) → tuple[bool, list[str]]`
Returns `(is_nn1_honest, violations)`. Checks spec_facets discipline (must all be in `NN1_HONEST_DISCIPLINES`) AND researcher_dof_ledger for `evidence_source='OOS'` (frozen-eval peek). Default-deny: any unrecognized discipline is a violation.

---

### Calibration Sweep

`run_calibration_sweep` is a standalone advisory function — separate from the production walk-forward. It sweeps exactly 2 parameters (`PARABOLIC_VELOCITY_THRESHOLD` and `VWAP_CROSS_HWM_PCT`) per symphony, applies the same overfitting controls as `run_autotuner` (Harvey & Liu BHY haircut, PBO veto), and returns report rows for the operator. It never writes to the state DB, never applies any parameter to live settings, and is not on the execution path.

**Search-space scope (2-param, research-verified):** Three candidates were evaluated and excluded:
- `VWAP_BLEED_ARM_MIN` / `VWAP_BLEED_ARM_MAX` — output clamps on the already-swept `VWAP_BLEED_MULTIPLIER`; the trailing-stop literature (Kaminski & Lo 2014; Dai et al. 2021) shows guardrail threshold response is flat across a wide range — no fittable optimum exists for an optimizer to find.
- `VWAP_BREAK_CONFIRM_TICKS` — excluded this cycle on data-sufficiency grounds: adding it moves the dedicated sweep 2-D → 3-D at the 100-trial floor (`100^(1/3) ≈ 4.6` levels/axis, a >2x density drop vs. 2-D); requires ~1,000-trial floor raise before inclusion.

See `DE-CALSWEEP-001` in `DECISIONS.md` and `.claude/calibration-methodology-verdict.md` for the full research basis.

#### `run_calibration_sweep(history_data, current_params, current_date_str, deviation_dict, random_state, *, min_history_days: int = _CALSWEEP_MIN_HISTORY_DAYS) → list[dict]`
V1 calibration sweep over `PARABOLIC_VELOCITY_THRESHOLD` and `VWAP_CROSS_HWM_PCT` only. Applies identical fold methodology to `run_autotuner` (60/20/20 split with O1 purge+embargo) but search space is limited to 2 params. Does NOT persist anything to the DB (read-only; operator-gated rollout).

**AC-4 — insufficient-history skip:** Symphonies with fewer than `min_history_days` days of history are skipped with a warning log. The production default is `_CALSWEEP_MIN_HISTORY_DAYS` (125) — production behavior is unchanged. Below this threshold the fold partitioning produces validation windows too small for the Sortino objective to yield meaningful signal. The param is injectable so test suites can pass `min_history_days=0` to exercise contracts on short fixtures without weakening the production floor (see DE-CALSWEEP-002).

**AC-5 — PBO veto surfaced per symphony:** When the BHY haircut finds no trial that clears the FDR gate, `pbo_veto_status=True` is set on every report row for that symphony. This is surfaced prominently in the advisory report so the operator knows the proposed value is the naive Optuna winner and is NOT statistically qualified.

**AC-6 — timestamped study-name with `__calsweep` suffix:** Each symphony's study name is `{timestamp}__{symphony_id}__calsweep` — identifiable at a glance and guaranteed never to collide with production `run_autotuner` study names.

**AC-7 — trigger-frequency operator-review flag:** When the proposed params would cause trigger frequency to exceed `_CALSWEEP_TRIGGER_FREQ_FLAG_MULTIPLIER` (2.0×) the current count on the validation fold, `flag_for_operator_review=True` is set. The operator must review before any per-symphony deploy.

**VWAP_CROSS_HWM_PCT bounds asymmetry:** The V1 calibration sweep uses asymmetric bounds (`_SS_VWAP_CROSS_HWM_V1_MIN=0.3`, `_SS_VWAP_CROSS_HWM_V1_MAX=2.0`) vs. the production walk-forward bounds (0.5, 2.5). The lower bound expands below production (3-tick confirm gate behaviour at low values); the upper bound narrows (above ~2.0, System A is effectively disabled for normal sessions). A proposed value in `[0.3, 0.5)` falls outside the production walk-forward search space — treat such proposals as informational only.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `history_data` | `dict` | Per-symphony tick-history dict (same format as `run_autotuner`) |
| `current_params` | `dict` | Current live parameter dict for delta/frequency comparison |
| `current_date_str` | `str` | ISO-8601 date string for today |
| `deviation_dict` | `dict` | Execution-deviation penalties from `calculate_historical_deviation` |
| `random_state` | `int` | TPE sampler seed for reproducibility |
| `min_history_days` | `int` | History floor (days) below which a symphony is skipped (AC-4). Default: `_CALSWEEP_MIN_HISTORY_DAYS` = 125 — production-unchanged. Pass a lower value (e.g. 0) in test suites to exercise contracts on short fixtures (DE-CALSWEEP-002). |

**Returns:** `list[dict]` — one dict per (symphony, param_name) pair. Keys:
| Key | Type | Description |
|-----|------|-------------|
| `symphony_id` | `str` | Symphony identifier |
| `param_name` | `str` | `"PARABOLIC_VELOCITY_THRESHOLD"` or `"VWAP_CROSS_HWM_PCT"` |
| `current_value` | `float` | Current live value of the param |
| `proposed_value` | `float` | Best haircut-selected value (or naive winner if haircut found no cleared trial) |
| `delta_pct` | `float` | `(proposed - current) / abs(current) * 100` |
| `expected_trigger_freq_change` | `float` | Proposed minus current trigger count on validation fold |
| `frozen_eval_alpha` | `float \| None` | Sortino on the 20% frozen-eval fold with proposed params |
| `naive_sharpe` | `float \| None` | Optuna best-value (Sortino) before haircut selection |
| `selection_tstat` | `float \| None` | BHY haircut t-stat of the winner; `None` when no trial cleared the gate |
| `haircut_outcome` | `str` | `"cleared"`, `"no_trial_cleared"`, `"not_run"`, or `"no_completed_trials"` |
| `pbo_veto_status` | `bool` | True when haircut found no qualified winner (AC-5) |
| `flag_for_operator_review` | `bool` | True when proposed trigger frequency >2x current (AC-7) |
| `sortino` | `float \| None` | Sortino of best-params on validation fold |
| `n_trials` | `int` | Number of completed Optuna trials |
| `study_name` | `str` | `{timestamp}__{symphony_id}__calsweep` (AC-6) |
| `trading_day_start` | `str` | First date in the history window |
| `trading_day_end` | `str` | Last date before the frozen-eval boundary |
| `cycle_id` | `str` | UTC ISO-8601 timestamp identifying this sweep run |

**Overfitting controls (same methodology as production):**
- Harvey & Liu BHY haircut (`_haircut_select`) — multiplicity axis
- `compute_n_effective` wired via `lambda: []` (no prior-run ledger for the calibration context; NEFF-001 fix — ledger returns empty, so `n_eff == len(haircut_trials)`, byte-identical to pre-wiring behavior)
- `filter_sortino_sentinels` removes zero-downside and partial-sentinel trials before the haircut
- PBO veto status exposed but NOT applied as a hard veto here (the advisory report surfaces it; the operator decides)

---

### Sortino Objective (Legacy)

#### `run_simulation_sortino_legacy(params, history_data, acc_sym_ids, current_date_str, deviation_dict) → float`
Legacy objective retained under Option B. Returns the Sortino ratio on the validation-fold guard-alpha series.

#### `compute_sortino_ratio(returns: list, target: float = 0.0) → float`
Sortino ratio: `mean(r) / downside_deviation`. Population denominator. Returns 1e6 when downside_deviation is zero (sentinel); returns 0.0 for empty series.

---

### s_count Wiring (Workstream E, advisor-rewire cycle, 2026-07-12)

**The gap:** migration `023_autotune_runs_s_count.sql` added the `s_count` column to `autotune_runs` long before this cycle, but no caller ever populated it — every row's `s_count` stayed `NULL` forever. `advisors.overfitting_conscience`'s Indicator-3 (operator drift — comparing this run's `S` accumulation against PRIOR runs' `s_count`) requires `>= 2` prior runs with non-`NULL`, increasing `s_count` to fire; with every historical row `NULL`, `drift_signal_available` could structurally never become `True` on live data, no matter how much genuine researcher drift had occurred.

**The fix:** the DoF-ledger query (`SELECT evidence_source, n_configs_searched, ... FROM researcher_dof_ledger WHERE spec_bundle_id = ?`, run via `advisor_ro_query`) was hoisted from AFTER `save_autotune_run` (where it fed only the in-memory Overfitting Conscience call) to BEFORE it. The same query result is now used for BOTH: (a) `_s_count_for_persistence = sum(n_configs_searched for BACKTEST_SELECTION rows)`, passed as `save_autotune_run(s_count=...)`; (b) the pre-existing Overfitting Conscience call, unchanged. **This is a control-flow reorder only** — the current-run I-1/I-2 `S` computation and verdict logic in `overfitting_conscience.py` were NOT modified; they re-derive their own `S` from the same ledger rows independently of the persisted `s_count`.

**NULL tolerance (AC-E4):** legacy rows with `s_count IS NULL` (everything written before this cycle) are tolerated — `overfitting_conscience`'s prior-runs scan skips `NULL` entries rather than crashing; drift detection needs `>= 2` non-`NULL` priors, so it will not fire until enough post-fix runs accumulate, which is expected and correct (no retroactive backfill of historical rows).

### Advisor Invocations (Sprint 3)

Post-walk-forward, after `save_autotune_run` (see "s_count Wiring" above for the 2026-07-12 hoist that now feeds `s_count` into that same call):

1. **Overfitting Conscience** — `run_overfitting_conscience(autotune_run, ledger_rows, prior_runs=advisor_ro_query(...))`.
2. **Spec Critic** — `run_spec_critic(stored_hash, sc_facets_rows, symphony_id=None)`. Called once per bundle; `symphony_id=None` is intentional.
3. **Divergence Explainer** — `run_divergence_explainer(autotune_run, cvar_row=None)`.

All three are wrapped in `try/except logger.warning` — Advisor failures are non-fatal.

---

### Walk-Forward State Machine Helpers

#### `build_symphony_study_name(timestamp: str, symphony_id: str) → str`
Returns `"{timestamp}__{symphony_id}"`. Study names must never be reused.

#### `_fresh_replay_state() → dict`
Returns a fresh per-position transient-state dict. Used by all three simulation callers.

#### `_replay_grace_minutes() → int`
Returns `alpha_bot_execution.VWAP_OPEN_WINDOW_GRACE_MINUTES`. Function-local import avoids circular import.

#### `_build_optuna_sampler_from_env() → optuna.samplers.TPESampler`
Returns `TPESampler` with seed sourced from `OPTUNA_SAMPLER_SEED` env var (None when unset; preserves non-deterministic behaviour for operators who have not opted in to reproducibility).

#### `_resolve_optuna_n_jobs_from_env() → int`
Returns n_jobs from `OPTUNA_N_JOBS` env var; falls back to 1 on unset or garbled. Default 1 (not cpu_count) because SQLite RDBStorage does not support parallel writes.

#### `calculate_historical_deviation(current_date_str: str) → dict`
Scans `post_mortem_*.json` files (last 45 days). Returns average execution-deviation penalties by exit reason.

## Types

### NN1 Freeze Discipline Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `FREEZE_DISCIPLINE_THEORY` | `"THEORY"` | Source: published theory |
| `FREEZE_DISCIPLINE_MANDATE` | `"MANDATE"` | Operator mandate |
| `FREEZE_DISCIPLINE_STYLIZED_FACT` | `"STYLIZED_FACT"` | Empirical codebase fact |
| `FREEZE_DISCIPLINE_POLITIS_WHITE` | `"POLITIS_WHITE"` | Politis-White bootstrap |
| `FREEZE_DISCIPLINE_CADENCE` | `"CADENCE"` | Scheduling convention |
| `FREEZE_DISCIPLINE_CALIBRATION` | `"CALIBRATION"` | Calibration result |
| `FREEZE_DISCIPLINE_BACKTEST_SELECTION` | `"BACKTEST_SELECTION"` | NN1 VIOLATION |
| `NN1_HONEST_DISCIPLINES` | frozenset | All disciplines that do NOT constitute an NN1 violation |

### Walk-Forward Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `TRAIN_RATIO` | 0.60 | 60% training fold |
| `VALIDATION_RATIO` | 0.20 | 20% validation fold (selection metric) |
| `FROZEN_EVAL_RATIO` | 0.20 | 20% frozen-eval fold (honest post-selection report) |
| `PURGE_DAYS` | 20 | Feature-lookback purge window |
| `EMBARGO_DAYS` | 1 | Post-fold embargo window |
| `HARVEY_LIU_FDR_Q` | 0.05 | BHY false-discovery rate level |
| `SORTINO_TARGET_RETURN` | 0.0 | Sortino denominator target (operator decision PA-5) |
| `RETURN_PCT_TO_FRACTION` | 100.0 | Percent-to-decimal unit conversion for CRRA |
| `OPTUNA_N_TRIALS_PRODUCTION` | 500 | Production walk-forward main study; 5x the 100-trial stability floor |
| `OPTUNA_N_TRIALS_CALIBRATION` | 100 | Calibration sweep; equals the statistical-stability floor exactly |
| `ACTIVE_OPTUNA_PRUNER_FAMILY` | `"NOP"` | Explicit NOP pruner — prevents silent MedianPruner activation |
| `_AUTOTUNE_REPLAY_N_JOBS` | 1 | Intraday-replay `n_jobs` override passed to `synthetic_history.generate_synthetic_history` on the autotune path. `n_jobs=1` uses joblib sequential backend (no fork), keeping peak RSS at 2.03 GiB on the 2-core / 3.0 GiB droplet (990 MB headroom, AC-1 empirical profile 2026-06-29, DE-AUTOTUNE-OOM). Other callers of `generate_synthetic_history` are untouched. |

### CPCV Constants (Phase 2)

| Constant | Value | Description |
|----------|-------|-------------|
| `_CPCV_N_GROUPS` | 6 | Number of contiguous date groups |
| `_CPCV_K_TEST_GROUPS` | 2 | Groups held out as test per split |
| `_CPCV_N_SPLITS` | 15 | `C(6,2)` total splits |
| `_CPCV_N_PATHS` | 5 | `φ[6,2] = (2/6)·15 = 5` complete OOS backtest paths |

### Calibration Sweep Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `_CALSWEEP_MIN_HISTORY_DAYS` | 125 | AC-4: minimum days before running the sweep for a symphony |
| `_CALSWEEP_TRIGGER_FREQ_FLAG_MULTIPLIER` | 2.0 | AC-7: proposed/current trigger count ratio above which operator review is required |
| `_SS_VWAP_CROSS_HWM_V1_MIN` | 0.3 | V1 calibration lower bound — expands below production 0.5 (3-tick confirm gate) |
| `_SS_VWAP_CROSS_HWM_V1_MAX` | 2.0 | V1 calibration upper bound — narrows below production 2.5 (~2sigma reliability limit) |
| `_SS_PARA_VEL_MIN` | 1.0 | Shared with production walk-forward |
| `_SS_PARA_VEL_MAX` | 4.0 | Shared with production walk-forward |

### Optuna Search Space

`OPTUNA_SEARCH_SPACE_KEYS` = `{"TAKE_PROFIT_MC_PCT", "VWAP_CROSS_HWM_PCT", "VWAP_BLEED_MULTIPLIER", "VWAP_BLEED_TICKS", "PARABOLIC_VELOCITY_THRESHOLD", "MAX_PARABOLIC_SQUEEZE"}`

NN1-frozen facets that must NEVER appear in the search space: `gamma`, `utility_family`, `wealth_argument`, `generator_family`, `horizon_convention`, `lambda`.

The calibration sweep uses a SEPARATE, narrower 2-key space (`PARABOLIC_VELOCITY_THRESHOLD`, `VWAP_CROSS_HWM_PCT`) — it does NOT modify `OPTUNA_SEARCH_SPACE_KEYS`.

**Math Remediation R1 note (`DE-MATH-R1-001`):** as of this cycle, `TAKE_PROFIT_MC_PCT` is no longer objective-inert in the replay (AC-1/AC-2/AC-7 fixed the day-constant MC degeneracy that made it unreachable); `PARABOLIC_VELOCITY_THRESHOLD`/`MAX_PARABOLIC_SQUEEZE` have had their inertness CAUSE removed (see `_replay_exit_tick`'s AC-3/AC-5 fixes above) but a full walk-forward objective-variance demonstration for those two specifically is deferred to the R3 pre-retune checklist. **No retune ships on any of the six search-space keys until that checklist clears** — see `DE-MATH-R1-001`.

## Internal Dependencies

- `math_engine` — all per-tick decision primitives, `WEALTH_ARG_FLOOR`, `_SORTINO_SENTINEL`, `compute_pbo`, `compute_crra_eu_objective`, `EXIT_CONFIRM_TICKS`, `apply_regime_exit_adjustment` (R1: regime-conditional confirm-tick resolution)
- `database` — `get_spec_bundle_by_id`, `save_autotune_run` (now called with `s_count=`, Workstream E), `advisor_ro_query`. **`get_cached_regime_label` is explicitly NEVER called from this module** — forbidden by ruling (`DE-MATH-R1-001` AC-4): it is a live, latest-wins single-row cache and would inject lookahead into the replay if consulted for a historical date.
- `regime_classifier` — `classify_regime`, `MIN_LABEL_SERIES_LENGTH` (R1: fresh per-day regime-label recomputation for the replay, no-lookahead; pre-existing module, newly wired here)
- `synthetic_history` — `generate_synthetic_history`, `_MC_REPLAY_SIMULATION_PATHS` (R1: the AC-6 parity battery shares this MC-path-count config rather than `math_engine`'s 5000-path default)
- `acceptance_gate` — reusable overfitting acceptance gate
- `advisors.overfitting_conscience` — `run_overfitting_conscience`
- `advisors.spec_critic` — `run_spec_critic`
- `advisors.divergence_explainer` — `run_divergence_explainer`
- `optuna` — TPE sampler, study management
