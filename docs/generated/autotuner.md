# autotuner

> Optuna walk-forward optimizer: runs 500 trials per symphony over a 250-day sliding window, selects the best trial via the CRRA-EU objective + Harvey & Liu BHY haircut + CSCV PBO acceptance gate, and enforces NN1 spec-freeze discipline throughout.

**Source:** `autotuner.py`
**Last updated:** 2026-06-02

## Overview

`autotuner.py` implements the per-symphony Bayesian optimization loop. At each autotuner run it:

1. Fetches 250 trading days of synthetic replay history via `synthetic_history.generate_synthetic_history`.
2. Splits history 60/20/20 (train/validation/frozen-eval) with `PURGE_DAYS=20` and `EMBARGO_DAYS=1` per López de Prado 2018.
3. Builds CPCV folds via `_generate_cpcv_folds` (N=6 groups, k=2 test groups, 15 splits, 5 complete OOS paths) and aggregates paths via `_aggregate_cpcv_paths`.
4. Runs `OPTUNA_N_TRIALS_PRODUCTION` (500) Optuna TPE trials; each trial's objective uses the CRRA-EU branch (`run_simulation_crra_eu`) or the legacy Sortino branch. `locked_vars` keys are excluded from `suggest_*` calls.
5. Applies the Harvey & Liu BHY selection haircut via `_haircut_select`, using `compute_n_effective` to compute the honest `N_effective = N_optuna + S` multiple-testing count.
6. Applies STAGE-1 PBO veto gate via `math_engine.compute_pbo` on the top-`_CSCV_TOP_K` (20) pre-BHY configs: rejects if PBO > `PBO_REJECT_THRESHOLD` (0.5).
7. Enforces NN1 spec-freeze: the caller must supply `spec_bundle_id`; `run_autotuner` raises `ValueError` if it is `None`. The caller resolves it via `database.get_or_create_phase1_theory_bundle_id` before calling.
8. Post-walk-forward: invokes `run_overfitting_conscience`, `run_spec_critic` (with `symphony_id=None`, called once per bundle), `run_divergence_explainer` (each wrapped in `try/except logger.warning` — Advisor failures are non-fatal).
9. Persists the autotune run row via `database.save_autotune_run` (returns int row id used by OC producer).

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
Runs the validation-fold simulation with `params` and returns the aggregate guard-alpha. Used for OOS re-validation in `ai_advisor.revalidate_suggestion_oos`.

#### `_collect_sim_returns(p, history_data, acc_sym_ids, current_date_str, deviation_dict) → list[float]`
Runs the guard-alpha simulation and returns per-triggered-day guard-alpha values. Shared tick loop with `run_simulation` via `_replay_exit_tick`. No recency decay (Decision D5).

#### `_collect_sim_returns_dated(p, history_data, acc_sym_ids, current_date_str, deviation_dict) → dict[str, float]`
Runs the guard-alpha simulation and returns a date-keyed dict of decimal-fraction returns. Used by `_haircut_select` to build `configs_date_returns` for `math_engine.compute_pbo` (STAGE-1 PBO veto).

#### `replay_exit_sequence(ticks, params, *, grace_minutes) → list[dict]`
Pure observability helper. Runs the per-tick exit loop over one day and returns one `{"tick_idx", "exit_reason"}` dict per executed tick. Used by AC-6 parity tests.

#### `_replay_exit_tick(state, tick, tick_idx, n_ticks, p, grace_minutes) → str | None`
Single-tick exit core. All three simulation callers call this function — one canonical copy of the exit orchestration. Returns the `resolve_trigger_priority` exit-reason string when an exit fires, else `None`.

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
Per-trial t-statistic via nonparametric bootstrap SE (Efron 1979): `Sortino / SE_bootstrap`. Returns `0.0` (conservative rejection) when bootstrap SE is unavailable.

#### `compute_haircut_pvalue(t_stat: float) → float`
One-sided p-value `1 - Φ(t)`, clamped to `[_HAIRCUT_PVALUE_EPSILON, 1 - _HAIRCUT_PVALUE_EPSILON]`.

#### `benjamini_hochberg_adjust(p_values: list[float]) → list[float]`
BHY step-up adjustment with Yekutieli `c(N) = sum(1/j)` arbitrary-dependence factor. Returns one adjusted p-value per input in the original order.

#### `compute_n_effective(n_optuna: int, ledger_query, winning_spec_bundle_id: str | None = None) → int`
Returns `N_optuna + S`, the honest multiple-testing count. `S` = sum of `n_configs_searched` over `BACKTEST_SELECTION` ledger rows, excluding frozen-eval-tainted rows and the winning bundle. `ledger_query` is a callable injected for testability.

---

### NN1 Spec-Freeze Enforcement

#### `validate_search_space_nn1() → None`
Asserts that `OPTUNA_SEARCH_SPACE_KEYS` contains no frozen facets. Called at the top of `run_autotuner` before any trial runs.

#### `validate_nn1_compliance(spec_bundle_id: int) → tuple[bool, list[str]]`
Returns `(is_nn1_honest, violations)`. Checks spec_facets discipline (must all be in `NN1_HONEST_DISCIPLINES`) AND researcher_dof_ledger for `evidence_source='OOS'` (frozen-eval peek). Default-deny: any unrecognized discipline is a violation.

---

### Calibration Sweep

#### `run_calibration_sweep(history_data, current_params, current_date_str, deviation_dict, random_state) → list[dict]`
V1 calibration sweep over `PARABOLIC_VELOCITY_THRESHOLD` and `VWAP_CROSS_HWM_PCT` only. Uses same O1 purge+embargo and Harvey & Liu haircut methodology as `run_autotuner` but search space is limited. Does NOT persist anything to the DB (AC-V1.3: read-only). Uses asymmetric VWAP_CROSS_HWM_PCT bounds (`_SS_VWAP_CROSS_HWM_V1_MIN=0.3`, `_SS_VWAP_CROSS_HWM_V1_MAX=2.0`).

**Returns:** list of report dicts, one per tuned param per symphony in `history_data`.

---

### Sortino Objective (Legacy)

#### `run_simulation_sortino_legacy(params, history_data, acc_sym_ids, current_date_str, deviation_dict) → float`
Legacy objective retained under Option B. Returns the Sortino ratio on the validation-fold guard-alpha series.

#### `compute_sortino_ratio(returns: list, target: float = 0.0) → float`
Sortino ratio: `mean(r) / downside_deviation`. Population denominator. Returns 1e6 when downside_deviation is zero (sentinel); returns 0.0 for empty series.

---

### Advisor Invocations (Sprint 3)

Post-walk-forward, after `save_autotune_run`:

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
| `OPTUNA_N_TRIALS_CALIBRATION` | 100 | Calibration sweep; equals the statistical-stability floor |
| `ACTIVE_OPTUNA_PRUNER_FAMILY` | `"NOP"` | Explicit NOP pruner — prevents silent MedianPruner activation |

### CPCV Constants (Phase 2)

| Constant | Value | Description |
|----------|-------|-------------|
| `_CPCV_N_GROUPS` | 6 | Number of contiguous date groups |
| `_CPCV_K_TEST_GROUPS` | 2 | Groups held out as test per split |
| `_CPCV_N_SPLITS` | 15 | `C(6,2)` total splits |
| `_CPCV_N_PATHS` | 5 | `φ[6,2] = (2/6)·15 = 5` complete OOS backtest paths |

### Optuna Search Space

`OPTUNA_SEARCH_SPACE_KEYS` = `{"TAKE_PROFIT_MC_PCT", "VWAP_CROSS_HWM_PCT", "VWAP_BLEED_MULTIPLIER", "VWAP_BLEED_TICKS", "PARABOLIC_VELOCITY_THRESHOLD", "MAX_PARABOLIC_SQUEEZE"}`

NN1-frozen facets that must NEVER appear in the search space: `gamma`, `utility_family`, `wealth_argument`, `generator_family`, `horizon_convention`, `lambda`.

## Internal Dependencies

- `math_engine` — all per-tick decision primitives, `WEALTH_ARG_FLOOR`, `_SORTINO_SENTINEL`, `compute_pbo`, `compute_crra_eu_objective`
- `database` — `get_spec_bundle_by_id`, `save_autotune_run`, `advisor_ro_query`
- `synthetic_history` — `generate_synthetic_history`
- `acceptance_gate` — reusable overfitting acceptance gate
- `advisors.overfitting_conscience` — `run_overfitting_conscience`
- `advisors.spec_critic` — `run_spec_critic`
- `advisors.divergence_explainer` — `run_divergence_explainer`
- `optuna` — TPE sampler, study management
