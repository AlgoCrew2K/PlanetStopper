# math_engine

> Pure risk-math primitives: trailing-stop mechanics, CRRA-EU utility, CVaR diagnostic type, Monte Carlo gating, VWAP signals, and the 6-layer exit-trigger resolver.

**Source:** `math_engine.py`
**Last updated:** 2026-05-27

## Overview

`math_engine.py` contains all decision-math functions extracted from the execution path. Every function is pure (no I/O, no state, no DB writes) unless explicitly noted. Functions that accept float parameters call `_reject_non_finite` at entry — NaN/Inf inputs raise `ValueError` rather than propagating silently into exit decisions.

The module is the single source of truth for all named math constants (project no-magic-numbers rule). It is imported by `alpha_bot_execution.py`, `autotuner.py`, and `synthetic_history.py`.

## API Reference

### Input Validation

#### `_reject_non_finite(**kwargs) → None`
Raises `ValueError` for any NaN / +Inf / -Inf in named float parameters. Only float-typed values are validated; ints and bools are intentionally skipped.

#### `_reject_non_finite_in_records(records: list[dict], *field_names) → None`
Iterates a list of dicts and calls `_reject_non_finite` on named fields. Missing keys are skipped silently.

---

### Sortino Sentinel Filter

#### `filter_sortino_sentinels(sortino_values: list[float]) → list[float]`
Removes `_SORTINO_SENTINEL` (1e6) entries from the trial Sortino series before the Harvey & Liu selection haircut. A sentinel's ~1e6 magnitude would produce an extreme t-statistic that dominates the cross-trial distribution. The input list is not mutated.

---

### CRRA-EU Utility (Phase-1 M1)

#### `derive_wealth_argument(r_policy_fraction: float) → float`
Returns the raw per-period gross wealth ratio `W = 1 + r_policy_fraction`. This is the W-H2 formula. The floor is NOT applied here — use `derive_floored_wealth_argument` when feeding into CRRA utility.

**Reference:** `docs/decision-science/w-h2-wealth-argument-derivation.md §3`

#### `derive_floored_wealth_argument(r_policy_fraction: float) → float`
Returns `max(WEALTH_ARG_FLOOR, 1 + r_policy_fraction)`. This is the complete W-H2 + W-H4 construction. Call this before `compute_crra_utility`. The floor is on the INPUT `W`, never on the output `U`.

#### `compute_crra_utility(W: float, gamma: float) → float`
<!-- TODO: Add documentation -->
Computes CRRA utility `u(W; γ)`:
- `(W^(1-γ) - 1) / (1-γ)` for `γ ≠ 1` (within `CRRA_LOG_UTILITY_GAMMA_TOL`)
- `ln(W)` for the `γ → 1` log-utility limit

#### `compute_crra_eu_tstat(U_series: list[float]) → float`
Per-trial t-statistic for the CRRA-EU objective: `mean(U) / (sd(U) / sqrt(T))`. Uses sample stdev (ddof=1). Returns `0.0` for T ≤ 1 or a constant series. This is the correct form for a mean-valued objective — do NOT use `compute_sortino_tstat` for CRRA-EU (that is the H-6 category error).

**Returns:** `float` — t-statistic; `0.0` for degenerate series.

**Reference:** S-2 binding condition; council synthesis §4.

---

### Harvey & Liu BHY Haircut

#### `compute_sortino_tstat(sortino: float, T: int) → float`
Per-trial t-statistic for the Sortino objective: `sortino * sqrt(T)`. Appropriate ONLY for ratio-valued objectives. Using this for a mean-valued objective (e.g. CRRA-EU) is the H-6 category error.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `sortino` | `float` | Sortino ratio |
| `T` | `int` | In-sample observation count |

**Returns:** `float`

#### `compute_haircut_pvalue(t_stat: float) → float`
One-sided p-value `1 - Φ(t)`, clamped to `[_HAIRCUT_PVALUE_EPSILON, 1 - _HAIRCUT_PVALUE_EPSILON]`. Prevents IEEE-754 underflow/saturation for extreme t-statistics.

#### `benjamini_hochberg_adjust(p_values: list[float]) → list[float]`
Benjamini-Hochberg-Yekutieli (BHY) step-up adjustment. The Yekutieli `c(N) = sum(1/j)` factor corrects for arbitrary dependence (Optuna TPE concentrates the search; plain BH assumes independence). Returns one adjusted p-value per input in the original order.

**Reference:** Harvey & Liu 2015, DOI 10.3905/jpm.2015.42.1.013; Benjamini-Hochberg-Yekutieli 2001.

#### `compute_n_effective(n_optuna: int, ledger_query, winning_spec_bundle_id: str | None = None) → int`
Returns `N_optuna + S`, the honest multiple-testing count. `S` is the sum of `n_configs_searched` over `BACKTEST_SELECTION` ledger rows, excluding frozen-eval-tainted rows and the winning bundle. NN1-honest case (S=0) makes this byte-identical to today's haircut. `ledger_query` is a callable injected for testability.

---

### Intraday Stop Mechanics

#### `compute_time_squeeze_decay(time_ratio: float) → tuple[float, float]`
Returns `(dynamic_multiplier, dynamic_min_stop)`. `time_ratio` must be in `[0.0, 1.0]` (fraction of session elapsed). Raises `ValueError` outside that range. Decay curve: `log10(1 + 9 * time_ratio)` — concave, tightening faster early and slower near the close.

**Returns:** `(dynamic_multiplier, dynamic_min_stop)` — both floats.

#### `compute_active_trailing_stop(symphony_vol, dynamic_multiplier, dynamic_min_stop, para_armed, breakeven_locked, parabolic_squeeze_multiplier) → float`
Returns the active trailing-stop distance in percentage points. `parabolic_squeeze_multiplier` must be strictly positive (rejects with `ValueError`). If `para_armed` or `breakeven_locked`, the stop is multiplied by `parabolic_squeeze_multiplier`.

#### `compute_breakeven_update(current_return, symphony_vol, base_stop_level, current_hold_ticks, currently_breakeven_locked, is_triggered) → tuple[int, bool, float]`
Returns `(new_hold_ticks, new_breakeven_locked, stop_trigger_level)`. The breakeven latch is one-way: once `currently_breakeven_locked=True`, it is always `True`. The floor `0.0` is applied once the latch fires ("lock gains hard"). When `is_triggered=True`, returns `TRIGGERED_OVERRIDE_LEVEL` (-999.0).

#### `compute_para_arm_decision(current_return, prev_return, para_threshold, currently_armed) → tuple[float, bool]`
Returns `(velocity, should_arm_transition)`. Velocity is `current_return - prev_return`. Arms once on the first tick where velocity ≥ threshold; never re-arms. Caller is responsible for state mutation.

---

### Exit Confirmation

#### `compute_exit_confirmation(armed, is_triggered, current_return, stop_trigger_level, prob_beating: float | None, current_below_stop_count) → tuple[int, bool]`
Returns `(new_below_stop_count, is_trailing_stop_hit)`. `EXIT_CONFIRM_TICKS` consecutive qualifying ticks required. MC sanity gate: when `prob_beating >= MC_SANITY_THRESHOLD`, the exit is vetoed. When `prob_beating is None` (MC unavailable), the gate passes — insufficient MC data must never disable the protective stop.

#### `compute_tp_confirmation(mc_available, prob_beating, take_profit_mc_pct, current_return, is_triggered, tp_armed, above_tp_count) → tuple[bool, int, bool]`
Returns `(new_tp_armed, new_above_tp_count, is_tp_hit)`. Arms when MC drops below `take_profit_mc_pct`; confirms when MC rises back above and `TP_CONFIRM_TICKS` ticks elapse. MC-unavailable ticks reset the counter.

---

### VWAP Signals

#### `compute_vwap_signals(holdings: list[dict], live_vwaps: dict[str, dict]) → tuple[float, float]`
Returns `(weighted_vwap_diff, valid_vwap_weight)`. Allocation-weighted VWAP deviation across holdings. Skips tickers with no live VWAP, zero/negative VWAP, or zero volume.

#### `compute_vwap_bleed_arm_threshold(symphony_vol: float, bleed_multiplier: float) → float`
Returns the dynamic VWAP-bleed arm threshold (always negative, percentage points). Clamped to `[VWAP_BLEED_ARM_MIN, VWAP_BLEED_ARM_MAX]` = `[-3.0, -0.5]`.

#### `compute_vwap_breakdown_update(is_triggered, valid_vwap_weight, weighted_vwap_diff, safe_hwm, current_return, vwap_cross_hwm_pct, vwap_bleed_arm_pct, vwap_bleed_ticks_threshold, current_vwap_ticks, current_vwap_bleed_ticks) → tuple[int, int, bool, bool]`
Returns `(new_vwap_ticks, new_vwap_bleed_ticks, is_vwap_broken, is_vwap_bleed_broken)`. System A (profit-protection break) and System B (bleed) run independently. Gate fails (both counters reset) when VWAP coverage weight is too low or VWAP diff is non-negative.

#### `is_in_open_window_grace(current_et, execution_start_hhmm: str, grace_minutes: int) → bool`
Returns True iff `current_et` falls within `[exec_start, exec_start + grace_minutes)`. The input must be timezone-aware; raises `ValueError` for naive datetimes.

---

### Exit-Priority Resolver

#### `resolve_trigger_priority(is_vwap_broken, is_tp_hit, is_vwap_bleed_broken, is_trailing_stop_hit) → tuple[str | None, list[str]]`
Returns `(winner, co_fired_list)`. Canonical priority: VWAP Breakdown > Take-Profit > VWAP Bleed Cut > Trailing Stop. Returns `(None, [])` when no flag is True.

---

### Monte Carlo

#### `run_monte_carlo(holdings, historical_data, spy_today_return, simulation_paths=5000, neighbor_k=150, seed=None) → float | None`
Vectorized kNN Monte Carlo using SPY return and 20-day rolling vol as regime features. Returns the probability (0–100) that the portfolio beats the current intraday return. Returns `None` (the out-of-band sentinel) when the eligible kNN pool is below `MC_MIN_HISTORY_DAYS` — callers must treat `None` as "MC unavailable" and NOT veto the protective stop.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `holdings` | `list[dict]` | Each entry: `ticker`, `allocation`, `last_percent_change` |
| `historical_data` | `dict` | Date-keyed dict of ticker-keyed daily returns |
| `spy_today_return` | `float` | Today's SPY return in percent |
| `simulation_paths` | `int` | Path count (default 5000) |
| `neighbor_k` | `int` | kNN pool size (default 150) |
| `seed` | `int \| None` | Isolated RNG seed; `None` uses a non-deterministic seed |

**Returns:** `float` (probability 0–100) or `None` (insufficient history).

#### `derive_cycle_mc_seed(cycle_id: str) → int`
Returns a deterministic 64-bit seed from the SHA-256 of the cycle_id string. Pure function, safe across daemon restarts.

---

### CVaR Diagnostic Type

#### `class CVaRAssessment`
Frozen dataclass (`frozen=True`). Typed result for the Phase-2 forward-path CVaR co-signal.

**Fields:**
| Field | Type | Description |
|-------|------|-------------|
| `cvar_pct` | `float \| None` | 5th-percentile CVaR as a percentage; `None` = insufficient |
| `breach` | `bool` | True when CVaR exceeds operator breach threshold. MUST be False when `cvar_pct is None` |
| `tail_obs_count` | `int` | Tail observations used; 0 when `cvar_pct is None` |
| `insufficient_reason` | `str \| None` | Human-readable explanation when `cvar_pct is None` |

`__post_init__` enforces the fail-safe invariant: `cvar_pct is None → breach is False`.

#### `compute_portfolio_cvar(...) → CVaRAssessment`
<!-- TODO: Add documentation -->

---

### Historical Deviation

#### `calculate_historical_deviation(current_date_str: str) → dict`
Scans local `post_mortem_*.json` files from the last 45 calendar days. Computes average execution deviation (exit return minus attempted trigger level) grouped by exit reason. Used by the autotuner to apply realistic exit penalties.

**Returns:** `dict` — keys: `"Take-Profit"`, `"Trailing Stop"`, `"VWAP Breakdown"`, `"VWAP Bleed Cut"`.

## Types

### Named Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `LOOKBACK_DAYS` | 20 | 20-day realized-volatility window |
| `ATR_LOOKBACK_DAYS` | 15 | 14-day true-range window + 1 prior close |
| `PCT_SCALAR` | 100.0 | Decimal → percentage conversion |
| `CRRA_LOG_UTILITY_GAMMA_TOL` | 1e-9 | Tolerance for γ = 1 log-utility branch |
| `WEALTH_ARG_FLOOR` | 0.001 | Floor on W input to CRRA utility (W-H4) |
| `MC_INSUFFICIENT_HISTORY_SENTINEL` | `None` | Out-of-band sentinel for insufficient MC history |
| `MC_MIN_HISTORY_DAYS` | 20 | Minimum eligible kNN pool days |
| `MC_VOL_WINDOW_DAYS` | 20 | Rolling SPY vol window |
| `MC_DEFAULT_SIMULATION_PATHS` | 5000 | Default MC path count |
| `MC_DEFAULT_NEIGHBOR_K` | 150 | Default kNN pool size |
| `MC_SEED_MODULUS` | 2^64 | SHA-256 seed space |
| `CVAR_TAIL_PCT` | 0.05 | CVaR tail percentile (5th) |
| `CVAR_ALPHA_DEFAULT` | 0.05 | Default CVaR alpha |
| `CVAR_MIN_TAIL_OBS` | 1 | Minimum distinct tail observations |
| `MULT_OPEN` | 1.5 | Dynamic stop multiplier at open |
| `MULT_CLOSE` | 0.5 | Dynamic stop multiplier at close |
| `EXIT_CONFIRM_TICKS` | 3 | Consecutive ticks to confirm trailing-stop exit |
| `TP_CONFIRM_TICKS` | 2 | Consecutive ticks to confirm take-profit exit |
| `MC_SANITY_THRESHOLD` | 60.0 | MC probability above which trailing stop is vetoed |

## Internal Dependencies

- `autotuner.py` — imports `WEALTH_ARG_FLOOR`, `_SORTINO_SENTINEL`; calls `run_monte_carlo`, `compute_*` functions
- `alpha_bot_execution.py` — calls all per-tick decision functions
- `synthetic_history.py` — uses `MC_MIN_HISTORY_DAYS`, `MC_VOL_WINDOW_DAYS`
