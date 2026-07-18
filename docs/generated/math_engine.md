# math_engine

> Pure risk-math primitives: trailing-stop mechanics, CRRA-EU utility, CVaR diagnostics, Monte Carlo gating, regime-match guard, VWAP signals, and the 6-layer exit-trigger resolver.

**Source:** `math_engine.py`
**Last updated:** 2026-07-18 (Math Remediation R3-c, `DE-MATH-R3C-001`, code-complete on `fix/math-r3c` @ `a5c011dd`, independent review IN FLIGHT -- NOT yet merged/deployed) — `compute_active_trailing_stop` gains an optional `squeeze_floor` param, see the extended entry below. Prior: 2026-07-18 (Math Remediation R3-b, `DE-MATH-R3B-001`, SHIPPED @ `origin/main` `f3c7e050`, droplet-deployed + verified) — new `compute_arm_disarm_decision` seam + `DISARM_CONFIRM_TICKS` constant; see the Arm/Disarm Decision section below. Prior: 2026-06-02 (initial generation)

## Overview

`math_engine.py` contains all decision-math functions extracted from the execution path. Every function is pure (no I/O, no state, no DB writes) unless explicitly noted. Functions that accept float parameters call `_reject_non_finite` at entry — NaN/Inf inputs raise `ValueError` rather than propagating silently into exit decisions.

The module is the single source of truth for all named math constants (project no-magic-numbers rule). It is imported by `alpha_bot_execution.py`, `autotuner.py`, and `synthetic_history.py`.

Note: the CRRA-EU derivation helpers (`derive_wealth_argument`, `derive_floored_wealth_argument`, `compute_crra_eu_tstat`) and the Harvey & Liu haircut helpers (`compute_sortino_tstat`, `compute_haircut_pvalue`, `benjamini_hochberg_adjust`, `compute_n_effective`) all live in **`autotuner.py`**, not in this module. See `autotuner.md` for those functions.

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

### CRRA-EU Utility

#### `compute_crra_utility(W: float, gamma: float) → float`
Computes CRRA utility `u(W; γ)`:
- `(W^(1-γ) - 1) / (1-γ)` for `γ ≠ 1` (within `CRRA_LOG_UTILITY_GAMMA_TOL`)
- `ln(W)` for the `γ → 1` log-utility limit

W must be the floored wealth argument (`W >= WEALTH_ARG_FLOOR`). The floor is applied by the caller; flooring inside this function would hide a caller contract violation.

**Reference:** `decision-science-council-synthesis.md §3.9 W-H2 / W-H4`

#### `compute_crra_eu_objective(daily_returns: list[float], gamma: float) → float`
CRRA expected-utility objective: `mean(U)` over the fold. For each decimal-fraction return `r_i`:
1. Reject non-finite via `_reject_non_finite`
2. Floor wealth argument: `W_i = max(WEALTH_ARG_FLOOR, 1 + r_i)`
3. Compute `U_i = compute_crra_utility(W_i, gamma)`

Returns `mean(U) = sum(U) / T`. Returns `0.0` for an empty series.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `daily_returns` | `list[float]` | Decimal-fraction returns (not percent) |
| `gamma` | `float` | CRRA risk-aversion coefficient |

**Returns:** `float` — mean CRRA utility over the fold.

---

### Regime-Match Guard

#### `class RegimeMatchAssessment`
Frozen dataclass for the MC regime-match-quality guard (vision-audit Critical Rec #2).

**Fields:**
| Field | Type | Description |
|-------|------|-------------|
| `mean_sq_mahalanobis` | `float \| None` | Mean squared Mahalanobis-style distance to K nearest neighbours; `None` = insufficient eligible pool |
| `is_unprecedented` | `bool` | True when `mean_sq_mahalanobis > threshold_used`. MUST be False when `mean_sq_mahalanobis is None` (fail-safe) |
| `neighbor_k` | `int` | K used for the kNN test statistic |
| `threshold_used` | `float` | Threshold actually applied (`MC_REGIME_MATCH_CHI2_THRESHOLD` or env override) |
| `insufficient_reason` | `str \| None` | Human-readable explanation when `mean_sq_mahalanobis is None` |

`__post_init__` enforces: `mean_sq_mahalanobis is None → is_unprecedented is False` (fail-safe).

#### `compute_regime_match_quality(historical_data: dict, spy_today_return: float) → RegimeMatchAssessment`
Regime-match-quality guard for the MC bootstrap. Computes a Mahalanobis-style chi-squared test statistic on the K nearest candidate-pool neighbours of today's query point (SPY return, rolling vol). Uses the same z-score standardization as `run_monte_carlo`. Fires only on extreme regime breaks (`MC_REGIME_MATCH_CHI2_THRESHOLD = chi2(2)_{0.99} ≈ 9.21`).

Pure function, O(eligible pool size). No I/O. No blocking work (architecture constraint #1).

#### `apply_regime_exit_adjustment(regime_label: str | None, base_ticks: int) → int`
Maps a cached regime label to a bounded exit-confirmation tick count. Moves exactly one knob — the `exit_confirm_ticks` threshold. Constants are fixed theory-anchored values, not tuned by Optuna.

Safe default: `None`, empty string, or any unrecognized label returns `base_ticks` unchanged. Labels are exact case-sensitive matches (`'trending'`, `'mean-reverting'`, `'high-vol'`).

**Returns:** `int` clamped to `[REGIME_TICKS_LOWER_BOUND, REGIME_TICKS_UPPER_BOUND]`.

---

### Intraday Stop Mechanics

#### `compute_time_squeeze_decay(time_ratio: float) → tuple[float, float]`
Returns `(dynamic_multiplier, dynamic_min_stop)`. `time_ratio` must be in `[0.0, 1.0]` (fraction of session elapsed). Raises `ValueError` outside that range. Decay curve: `1 - sqrt(1 - time_ratio)` — i.i.d.-returns remaining-session uncertainty curve (Danielsson & Zigrand 2003). Zero free parameters, THEORY provenance.

**Returns:** `(dynamic_multiplier, dynamic_min_stop)` — both floats.

**Reference:** `docs/research/m3-provenance/literature-pass.md §1`

#### `compute_active_trailing_stop(symphony_vol, dynamic_multiplier, dynamic_min_stop, para_armed, breakeven_locked, parabolic_squeeze_multiplier, squeeze_floor: float | None = None) → float`
Returns the active trailing-stop distance in percentage points. `parabolic_squeeze_multiplier` must be strictly positive (rejects with `ValueError`). If `para_armed` or `breakeven_locked`, the stop is multiplied by `parabolic_squeeze_multiplier`.

**`squeeze_floor` (optional, Math Remediation R3-c, `DE-MATH-R3C-001`, `:463`):** a post-squeeze lower clamp on the stop DISTANCE (pp), wiring the previously-dead `MAX_SQUEEZE_FLOOR` knob (MA-11) — one prior repo-wide hit (`alpha_bot_execution.py:1236`, assigned, never read). `None` or `<= 0` means no clamp; every pre-existing 6-arg call site (incl. `docs/research/risk/scripts/i2_compounding_sim.py:73`) stays byte-identical. Scoped INSIDE the squeeze branch (`:515-519`): once `para_armed or breakeven_locked` fires, `pre_squeeze_active` is snapshotted BEFORE the `*= parabolic_squeeze_multiplier` step, and when `squeeze_floor` is a positive finite number the clamp applies as `active = max(active, min(squeeze_floor, pre_squeeze_active))` — a NO-WIDENING clamp: bounding the floor by `pre_squeeze_active` means it can only limit shrinkage, never raise the stop above its pre-squeeze value (at defaults the floor, 0.20, exceeds `dynamic_min_stop` near close, 0.15, so a naive `max(squeezed, floor)` would have inverted the squeeze). `squeeze_floor` participates in the entry `_reject_non_finite` check unconditionally — a non-finite value rejects even when the squeeze branch never fires. Independent review is IN FLIGHT (`DE-MATH-R3C-001`) — not yet merged/deployed.

#### `compute_breakeven_update(current_return, symphony_vol, base_stop_level, current_hold_ticks, currently_breakeven_locked, is_triggered) → tuple[int, bool, float]`
Returns `(new_hold_ticks, new_breakeven_locked, stop_trigger_level)`. The breakeven latch is one-way: once `currently_breakeven_locked=True`, it is always `True`. The floor `0.0` is applied once the latch fires ("lock gains hard"). When `is_triggered=True`, returns `TRIGGERED_OVERRIDE_LEVEL` (-999.0).

#### `compute_para_arm_decision(current_return, prev_return, para_threshold, currently_armed) → tuple[float, bool]`
Returns `(velocity, should_arm_transition)`. Velocity is `current_return - prev_return`. Arms once on the first tick where velocity ≥ threshold; never re-arms. Caller is responsible for state mutation.

---

### Arm/Disarm Decision (Trailing Stop)

#### `compute_arm_disarm_decision(prob_underperforming: float | None, is_triggered: bool, armed: bool, disarm_confirm_count: int, take_profit_mc_pct: float, trigger_threshold_pct: float, disarm_confirm_ticks: int = DISARM_CONFIRM_TICKS) → tuple[bool, int]`
Returns `(new_armed, new_disarm_confirm_count)`. Computes the trailing-stop's arm/disarm state update — the gate whose `armed` output feeds `compute_exit_confirmation`. Pure; `mc_available` is derived internally as `prob_underperforming is not None` (never a parameter); `current_return` is NOT a parameter. Extracted this cycle (Math Remediation R3-b, `DE-MATH-R3B-001`) from an inline block previously duplicated in both `alpha_bot_execution.py` and `autotuner.py:_replay_exit_tick`, replacing a disarm condition that had been INVERTED (MA-4) — it fired on a high `prob_underperforming` reading (deterioration) rather than a low one (recovery). See `DE-MATH-R3B-001` in `DECISIONS.md` for the full bug account and fix rationale.

**Behavior:**
- `is_triggered=True` → returns `(armed, disarm_confirm_count)` unchanged (frozen no-op).
- **Arm:** `should_arm = (mc_available and take_profit_mc_pct <= prob_underperforming < trigger_threshold_pct) or (not mc_available)` (the second clause is the MA-10 fail-open — an absent MC opinion arms the stop rather than leaving it dark). `should_arm and not armed` → `(True, 0)`, a fresh arm with the ladder reset.
- **Disarm (recovery-gated, hysteresis ladder):** while `armed`, a tick where `mc_available and prob_underperforming < take_profit_mc_pct` (strictly below the arm-band's own lower edge) increments `disarm_confirm_count`; once it reaches `disarm_confirm_ticks` (default `DISARM_CONFIRM_TICKS`), returns `(False, 0)` — disarmed. Any non-qualifying tick (still in-band, deteriorating, or MC-absent) resets the count to 0 and returns `(True, 0)` — stays armed. An MC-absent tick can never itself confirm a recovery.
- Otherwise (not armed, not arming) → `(False, 0)`.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `prob_underperforming` | `float \| None` | `run_monte_carlo`'s output — fraction of regime-matched paths that beat the portfolio; `None` = MC unavailable |
| `is_triggered` | `bool` | Freezes the decision (no-op) once the position has already triggered |
| `armed` | `bool` | Current arm state |
| `disarm_confirm_count` | `int` | Current recovery-tick ladder count |
| `take_profit_mc_pct` | `float` | Arm-band lower edge / recovery-disarm threshold (`TAKE_PROFIT_MC_PCT`, 5.0) |
| `trigger_threshold_pct` | `float` | Arm-band upper edge (`TRIGGER_THRESHOLD_PCT`, 15.0) |
| `disarm_confirm_ticks` | `int` | Consecutive qualifying ticks required to disarm; defaults to `DISARM_CONFIRM_TICKS` |

**Returns:** `(new_armed, new_disarm_confirm_count)`.

**Raises:** `ValueError` on non-finite float inputs (`_reject_non_finite`) or `disarm_confirm_ticks <= 0` (a disableable ladder would defeat the hysteresis it exists to provide).

**Caller responsibility:** returns no telemetry string — both call sites diff prev/new `armed` to drive ARM/DISARM prints and the `below_stop_count=0` reset on disarm, matching the `compute_tp_confirmation` idiom.

---

### Exit Confirmation

#### `compute_exit_confirmation(armed, is_triggered, current_return, stop_trigger_level, prob_beating: float | None, current_below_stop_count, exit_confirm_ticks=EXIT_CONFIRM_TICKS) → tuple[int, bool]`
Returns `(new_below_stop_count, is_trailing_stop_hit)`. `exit_confirm_ticks` consecutive qualifying ticks required (defaulting to `EXIT_CONFIRM_TICKS=3`; the regime-adjusted threshold is passed by the execution path). MC sanity gate: when `prob_beating >= MC_BREAKDOWN_THRESHOLD`, the exit is vetoed. When `prob_beating is None` (MC unavailable), the gate passes — insufficient MC data must never disable the protective stop.

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

### CVaR Diagnostics

#### `class CVaREstimate`
Frozen dataclass for the pure-math kNN-pool CVaR estimator result (distinct from `CVaRAssessment`).

**Fields:**
| Field | Type | Description |
|-------|------|-------------|
| `cvar_pct` | `float \| None` | R-U general-distribution CVaR; `None` when pool is empty or has fewer than `CVAR_MIN_TAIL_OBS` tail observations |
| `tail_obs_count` | `int` | Distinct tail observations used (H-2 stderr denominator) |
| `stderr` | `float \| None` | `std(tail_values, ddof=1) / sqrt(tail_obs_count)`; `None` when sentinel |
| `insufficient_reason` | `str \| None` | Human-readable explanation when `cvar_pct is None` |

#### `class CVaRAssessment`
Frozen dataclass for the kNN historical regime-match result. Phase-1 diagnostic only; the forward-path co-signal was **REJECTED** by decision-science council (see `docs/audit/vision-audit-2026-05-27/SYNTHESIS.md`). Phase-1 rule: zero production consumers permitted — tests only.

**Fields:**
| Field | Type | Description |
|-------|------|-------------|
| `cvar_pct` | `float \| None` | 5th-percentile CVaR as a percentage; `None` = insufficient |
| `breach` | `bool` | True when CVaR exceeds operator breach threshold. MUST be False when `cvar_pct is None` |
| `tail_obs_count` | `int` | Tail observations used; 0 when `cvar_pct is None` |
| `stderr` | `float \| None` | Standard error of the CVaR estimate; `None` when `cvar_pct is None` |
| `insufficient_reason` | `str \| None` | Human-readable explanation when `cvar_pct is None` |

`__post_init__` enforces: `cvar_pct is None → breach is False`, `cvar_pct is None → tail_obs_count == 0`, and stderr pairing invariants.

#### `compute_cvar_5pct_general_distribution(returns: list, alpha: float = CVAR_ALPHA_DEFAULT) → CVaREstimate`
Rockafellar-Uryasev (2002) general-distribution CVaR on a discrete pool. Correct atom handling: `CVaR = (1/alpha) * (1/N) * (sum_below + fractional_weight * VaR)`. Returns `CVaREstimate` with `cvar_pct=None` when pool is empty or insufficient. Raises `ValueError` on non-finite inputs.

#### `compute_cvar_stderr_distinct_tail(returns: list, alpha: float = CVAR_ALPHA_DEFAULT) → float | None`
Computes CVaR stderr using the DISTINCT GENUINE tail observation count (H-2 binding). Denominator is never the resample count. Returns `None` when pool is empty or has fewer than `CVAR_MIN_TAIL_OBS` observations.

#### `compute_portfolio_cvar(cycle_id, holdings, historical_data, spy_today_return, simulation_paths=5000, neighbor_k=150, *, mode=None) → CVaRAssessment`
M2 Phase-1 CVaR diagnostic — 5th-percentile expected shortfall. Computes CVaR for the portfolio using the same kNN regime-matching pool as `run_monte_carlo`. Seed is exclusively `derive_cycle_mc_seed(cycle_id)`. When `mode` is `"live"` or `"replay"`, persists the row via `database.record_cvar_diagnostic`. Phase-1: `breach` is always `False`.

---

### CSCV PBO Gate

#### `compute_pbo(configs_date_returns: list[dict[str, float]], eligible_dates: list[str], gamma: float, S: int | None = None) → float`
Computes the Probability of Backtest Overfitting (PBO) via reduced-N CSCV (Bailey & López de Prado 2014). Partitions `eligible_dates` into `S=_CSCV_S=8` blocks, evaluates `C(8,4)=70` IS/OOS combinations using CRRA-EU mean utility, computes `lambda_c` for each. Returns the fraction of combinations where `lambda_c <= 0`. PBO > `PBO_REJECT_THRESHOLD` (0.5) signals definitive backtest overfitting.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `configs_date_returns` | `list[dict]` | K config dicts, each mapping date-string → decimal return |
| `eligible_dates` | `list[str]` | Sorted date strings for the CPCV-eligible window |
| `gamma` | `float` | CRRA risk-aversion coefficient |
| `S` | `int \| None` | Block count override; default `_CSCV_S=8` |

**Returns:** `float` in [0.0, 1.0] — fraction of IS/OOS combos where IS-best ranked at or below median on OOS.

---

### Historical Deviation

#### `calculate_historical_deviation(current_date_str: str) → dict`
Scans local `post_mortem_*.json` files from the last 45 calendar days. Computes average execution deviation (exit return minus attempted trigger level) grouped by exit reason.

**Returns:** `dict` — keys: `"Take-Profit"`, `"Trailing Stop"`, `"VWAP Breakdown"`, `"VWAP Bleed Cut"`.

---

### Private Helpers (documented for completeness)

#### `_sorted_dates(historical_data: dict) → list[str]`
LRU-cached helper that extracts and sorts unique date keys from `historical_data`. Used by `run_monte_carlo`, `compute_portfolio_cvar`, and `compute_regime_match_quality`.

#### `_compute_rolling_spy_vol(spy_returns: np.ndarray) → np.ndarray`
Computes rolling `MC_VOL_WINDOW_DAYS`-day standard deviation of SPY returns. Returns array of same length; early elements use expanding window.

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
| `MC_REGIME_MATCH_CHI2_THRESHOLD` | 9.21034… | chi2(2)_{0.99} — conservative regime-match gate threshold |
| `CVAR_TAIL_PCT` | 0.05 | CVaR tail percentile (5th) |
| `CVAR_ALPHA_DEFAULT` | 0.05 | Default CVaR alpha |
| `CVAR_MIN_TAIL_OBS` | 1 | Minimum distinct tail observations |
| `MULT_OPEN` | 1.5 | Dynamic stop multiplier at market open |
| `MULT_CLOSE` | 0.5 | Dynamic stop multiplier at market close |
| `EXIT_CONFIRM_TICKS` | 3 | Consecutive ticks to confirm trailing-stop exit |
| `TP_CONFIRM_TICKS` | 2 | Consecutive ticks to confirm take-profit exit |
| `DISARM_CONFIRM_TICKS` | 3 | Consecutive recovery ticks (`prob_underperforming` below `TAKE_PROFIT_MC_PCT`) required before `compute_arm_disarm_decision` disarms the trailing stop; frozen, not in `autotuner.OPTUNA_SEARCH_SPACE_KEYS` |
| `MC_BREAKDOWN_THRESHOLD` | 60.0 | MC probability above which trailing stop is vetoed (H1 rename; the old name `MC_SANITY_THRESHOLD` survives only in a source-comment historical note, `math_engine.py:586`) |
| `PBO_REJECT_THRESHOLD` | 0.5 | PBO > this value signals backtest overfitting |
| `_CSCV_TOP_K` | 20 | Top-K PRE-BHY configs fed into `compute_pbo` |
| `_CSCV_S` | 8 | Number of contiguous chronological blocks for IS/OOS partition |

## Internal Dependencies

- `autotuner.py` — imports `WEALTH_ARG_FLOOR`, `_SORTINO_SENTINEL`; calls `run_monte_carlo`, `compute_pbo`, `compute_regime_match_quality`, `compute_crra_eu_objective`, `compute_*` functions
- `alpha_bot_execution.py` — calls all per-tick decision functions; `apply_regime_exit_adjustment`
- `synthetic_history.py` — uses `MC_MIN_HISTORY_DAYS`, `MC_VOL_WINDOW_DAYS`
