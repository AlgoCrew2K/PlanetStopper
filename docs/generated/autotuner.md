# autotuner

> Optuna walk-forward optimizer: runs 500 trials per symphony over a 125-day sliding window, selects the best trial via the CRRA-EU objective + Harvey & Liu BHY haircut, and enforces NN1 spec-freeze discipline throughout.

**Source:** `autotuner.py`
**Last updated:** 2026-05-27

## Overview

`autotuner.py` implements the per-symphony Bayesian optimization loop. At each autotuner run it:

1. Fetches 125 trading days of synthetic replay history via `synthetic_history.generate_synthetic_history`.
2. Splits history 60/20/20 (train/validation/frozen-eval) with `PURGE_DAYS=20` and `EMBARGO_DAYS=1` per López de Prado 2018.
3. Runs `N_trials` Optuna TPE trials; each trial's objective uses the CRRA-EU branch (`run_simulation_crra_eu`) or the legacy Sortino branch (`run_simulation_sortino_legacy`).
4. Applies the Harvey & Liu BHY selection haircut via `_haircut_select`, using `compute_n_effective` to compute the honest `N_effective = N_optuna + S` multiple-testing count.
5. Enforces NN1 spec-freeze: the spec bundle id is resolved via `database.get_or_create_phase1_theory_bundle_id` at entry, before any trial runs.
6. Post-walk-forward: invokes `run_overfitting_conscience`, `run_spec_critic`, `run_divergence_explainer` (each wrapped in `try/except logger.warning` — Advisor failures are non-fatal).
7. Persists the autotune run row via `database.save_autotune_run` (returns int row id used by OC producer).

## API Reference

### Public Entry Point

#### `run_autotuner(symphony_id: str, ...) → dict`
Main entry point. Runs the full walk-forward optimization for one symphony.

<!-- TODO: Add full parameter table — run_autotuner signature is complex; see autotuner.py directly -->

**Returns:** dict with keys:
- `baseline_decision`: `"AI"`, `"fallback"`, or `"default"`
- `oos_alpha`: OOS guard-alpha of the selected strategy
- `train_alpha`: train-fold guard-alpha
- `fallback_oos_alpha`, `default_oos_alpha`: cascade alternatives
- `selection_tstat`: Harvey & Liu haircut t-statistic of the winning trial
- `n_effective`: `N_optuna + S`
- `spec_bundle_id`: bundle_hash of the active Phase-1 theory bundle

---

### Walk-Forward Simulation

#### `run_simulation(params: dict, history_data: dict, acc_sym_ids: list, current_date_str: str, deviation_dict: dict) → float`
Runs the validation-fold simulation with `params` and returns the aggregate guard-alpha. Used for OOS re-validation in `ai_advisor.revalidate_suggestion_oos`.

#### `_collect_sim_returns(p, history_data, acc_sym_ids, current_date_str, deviation_dict) → list[float]`
Runs the guard-alpha simulation and returns per-triggered-day guard-alpha values. Shared tick loop with `run_simulation` via `_replay_exit_tick`. No recency decay (Decision D5 — walk-forward CV supplies recency relevance).

#### `replay_exit_sequence(ticks, params, *, grace_minutes) → list[dict]`
Pure observability helper. Runs the per-tick exit loop over one day and returns one `{"tick_idx", "exit_reason"}` dict per executed tick. Used by AC-6 parity tests.

#### `_replay_exit_tick(state, tick, tick_idx, n_ticks, p, grace_minutes) → str | None`
Single-tick exit core. All three simulation callers (`run_simulation`, `_collect_sim_returns`, `replay_exit_sequence`) call this function — one canonical copy of the exit orchestration. Returns the `resolve_trigger_priority` exit-reason string when an exit fires, else `None`.

---

### CRRA-EU Objective Branch

#### `run_simulation_crra_eu(params, history_data, acc_sym_ids, current_date_str, deviation_dict) → tuple`
Runs the Phase-1 CRRA-EU objective. Per-triggered-day: converts percent returns to decimal fractions (`/ RETURN_PCT_TO_FRACTION`), applies `derive_floored_wealth_argument`, computes `compute_crra_utility(W, gamma)`, accumulates the U-series. Returns `(crra_eu_tstat, daily_returns_pct)`.

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

---

### Sortino Objective (Legacy)

#### `run_simulation_sortino_legacy(params, history_data, acc_sym_ids, current_date_str, deviation_dict) → float`
Legacy objective retained under Option B. Returns the Sortino ratio on the validation-fold guard-alpha series.

#### `compute_sortino_ratio(returns: list, target: float = 0.0) → float`
Sortino ratio: `mean(r) / downside_deviation`. Population denominator. Returns 1e6 when downside_deviation is zero (sentinel); returns 0.0 for empty series.

---

### NN1 Spec-Freeze Enforcement

NN1 is enforced at entry to `run_autotuner`: `database.get_or_create_phase1_theory_bundle_id()` is called before any trial runs. The returned bundle id is written to every `autotune_runs` row.

Frozen facets (must never appear in `OPTUNA_SEARCH_SPACE_KEYS`):
- `gamma`, `utility_family`, `wealth_argument` (THEORY)
- `generator_family`, `horizon_convention` (Phase 2)
- `lambda` (CVaR budget, Phase 2)

Adding any of these to the search space is a NN1 violation — the Yekutieli `c(N)` factor would not account for the spec-facet tour.

---

### Advisor Invocations (Sprint 3)

Post-walk-forward, after `save_autotune_run`:

1. **Overfitting Conscience** — `run_overfitting_conscience(autotune_run, ledger_rows, prior_runs=advisor_ro_query(...))`. `prior_runs` are fetched via `database.advisor_ro_query`.
2. **Spec Critic** — `run_spec_critic(spec_bundle_id, spec_facets_rows, symphony_id=symphony_id)`.
3. **Divergence Explainer** — `run_divergence_explainer(autotune_run, cvar_row=None)` (fetches CVaR row internally when §B flag is on).

All three are wrapped in `try/except logger.warning` — Advisor failures are non-fatal and never block the autotuner output.

---

### Walk-Forward State Machine Helpers

#### `build_symphony_study_name(timestamp: str, symphony_id: str) → str`
Returns `"{timestamp}__{symphony_id}"`. Study names must never be reused.

#### `_fresh_replay_state() → dict`
Returns a fresh per-position transient-state dict. Used by all three simulation callers.

#### `_replay_grace_minutes() → int`
Returns `alpha_bot_execution.VWAP_OPEN_WINDOW_GRACE_MINUTES`. Function-local import avoids circular import.

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

### Optuna Search Space

`OPTUNA_SEARCH_SPACE_KEYS` = `{"TAKE_PROFIT_MC_PCT", "VWAP_CROSS_HWM_PCT", "VWAP_BLEED_MULTIPLIER", "VWAP_BLEED_TICKS", "PARABOLIC_VELOCITY_THRESHOLD", "MAX_PARABOLIC_SQUEEZE"}`

## Internal Dependencies

- `math_engine` — all per-tick decision primitives, `WEALTH_ARG_FLOOR`, `_SORTINO_SENTINEL`
- `database` — `get_or_create_phase1_theory_bundle_id`, `save_autotune_run`, `advisor_ro_query`
- `synthetic_history` — `generate_synthetic_history`
- `advisors.overfitting_conscience` — `run_overfitting_conscience`
- `advisors.spec_critic` — `run_spec_critic`
- `advisors.divergence_explainer` — `run_divergence_explainer`
- `optuna` — TPE sampler, study management
