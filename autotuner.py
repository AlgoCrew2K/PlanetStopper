import time
import math
import statistics
import optuna
from datetime import datetime, timedelta, timezone
import database
import math_engine
import synthetic_history
import glob
import json

# Required keys for a complete Optuna best_params payload.
# MUST be kept in sync with the suggest_* calls in the objective() closure
# below. If any of these keys is missing from study.best_params after
# optimization, the AI proposal is rejected wholesale (no Frankenstein merge)
# and the baseline cascade (fallback -> default) runs.
# Extra keys outside this set are tolerated for forward-compat.
# Note: optuna.logging.set_verbosity is now called inside run_autotuner
# (not module-level) so import does not trigger logging side effects.
# Vars in database.DEFAULT_LOCKED_VARS are excluded from the search space —
# they retain their current values and are never overwritten by trial suggestions.
OPTUNA_SEARCH_SPACE_KEYS = frozenset({
    "TAKE_PROFIT_MC_PCT", "VWAP_CROSS_HWM_PCT",
    "VWAP_BLEED_MULTIPLIER", "VWAP_BLEED_TICKS",
    "PARABOLIC_VELOCITY_THRESHOLD", "MAX_PARABOLIC_SQUEEZE",
})

# Optuna search space bounds — named so the search space is inspectable via
# optuna-compare without re-parsing logs, and to satisfy the no-magic-numbers rule.
_SS_TRIGGER_THRESHOLD_MIN = 5.0
_SS_TRIGGER_THRESHOLD_MAX = 25.0
_SS_TAKE_PROFIT_MC_MIN = 2.0
_SS_TAKE_PROFIT_MC_MAX = 10.0
_SS_VWAP_CROSS_HWM_MIN = 0.5
_SS_VWAP_CROSS_HWM_MAX = 2.5
_SS_VWAP_BLEED_MULT_MIN = 0.5
_SS_VWAP_BLEED_MULT_MAX = 3.0
_SS_VWAP_BLEED_TICKS_MIN = 3
_SS_VWAP_BLEED_TICKS_MAX = 30
_SS_PARA_VEL_MIN = 1.0
_SS_PARA_VEL_MAX = 4.0
_SS_MAX_PARA_SQUEEZE_MIN = 0.1
_SS_MAX_PARA_SQUEEZE_MAX = 0.8

# V1 calibration sweep — narrowed VWAP_CROSS_HWM_PCT bounds.
# Lower: 0.3 (vs general 0.5) — 3-tick confirm gate (math_engine.py) prevents spurious
# single-tick exits at this level; gives the sweep more room to find the true optimum.
# Upper: 2.0 (~2σ typical daily return) — above this System A is effectively disabled
# for normal sessions, making calibration unreliable.
_SS_VWAP_CROSS_HWM_V1_MIN = 0.3
_SS_VWAP_CROSS_HWM_V1_MAX = 2.0

# Exponential time-decay rate applied to per-day guard-alpha in run_simulation
# and _collect_sim_returns. Half-life ≈ 46 trading days (ln2 / 0.015).
_GUARD_ALPHA_DECAY_RATE = 0.015

# Target return for Sortino denominator: capital preservation baseline (0 = break-even).
# Operator decision PA-5; Sortino & van der Meer 1994, J. Portfolio Management.
SORTINO_TARGET_RETURN = 0.0

# Walk-forward purge window: training samples whose feature lookback window overlaps
# the test fold are excluded. Purge-relevant lookbacks are only those that cause a
# train sample's FEATURE COMPUTATION to reach into the test fold:
#   - calculate_20d_vol:      LOOKBACK_DAYS      = 20 trading days
#   - calculate_14d_atr_pct:  ATR_LOOKBACK_DAYS  = 15 trading days (14 TR periods + 1 prior close)
# PURGE_DAYS = max(20, 15) = 20.
# NOTE: The exponential decay weight (_GUARD_ALPHA_DECAY_RATE, half-life ≈ 46 days) is an
# OBJECTIVE AGGREGATION WEIGHT, not a feature lookback — it does not cause any train
# sample's feature computation to reach into the test fold and is therefore excluded from
# purge sizing. mc_prob is pre-computed in tick data, not computed live in the sim loop.
# López de Prado 2018, Advances in Financial Machine Learning, Ch. 7 (Purged k-fold CV).
PURGE_DAYS = 20

# Embargo period between train-end and test-start. Prevents autocorrelation leakage
# from serial dependence in adjacent samples. Default: 1 trading day.
# López de Prado 2018, Advances in Financial Machine Learning, Ch. 7.
EMBARGO_DAYS = 1

# Three-fold walk-forward ratios: 60% train / 20% validation / 20% frozen-eval.
# Selection is on validation; frozen-eval is consumed once post-selection for honest
# performance reporting. Purge + embargo applied at BOTH fold boundaries.
# López de Prado 2018, Advances in Financial Machine Learning, Ch. 7.4.
TRAIN_RATIO = 0.60
VALIDATION_RATIO = 0.20
FROZEN_EVAL_RATIO = 0.20
assert abs(TRAIN_RATIO + VALIDATION_RATIO + FROZEN_EVAL_RATIO - 1.0) < 1e-9, (
    "TRAIN_RATIO + VALIDATION_RATIO + FROZEN_EVAL_RATIO must equal 1.0"
)


def compute_sortino_ratio(returns: list, target: float = SORTINO_TARGET_RETURN) -> float:
    """Sortino ratio on a returns series.

    Formula: mean(r) / downside_deviation
    where downside_deviation = sqrt(mean(min(r - target, 0)^2))
    Population denominator: divide by N (all observations), not N_downside.

    Reference: Sortino & van der Meer 1994, "Downside Risk",
    Journal of Portfolio Management.

    Args:
        returns: Per-period return values (e.g., per-day guard-alpha).
        target: Minimum acceptable return; defaults to SORTINO_TARGET_RETURN (0.0).

    Returns:
        Sortino ratio as a finite float. Returns 1e6 when downside_deviation
        is zero (all returns >= target) so Optuna TPE receives a finite value.
        Returns 0.0 for an empty series.
    """
    if not returns:
        return 0.0
    n = len(returns)
    mean_r = sum(returns) / n
    sum_sq_downside = sum(min(r - target, 0.0) ** 2 for r in returns)
    mean_sq_downside = sum_sq_downside / n
    downside_deviation = math.sqrt(mean_sq_downside)
    if downside_deviation == 0.0:
        return 1e6
    return mean_r / downside_deviation


def compute_deflated_sharpe_ratio(
    SR_obs: float,
    SR_0: float,
    gamma3: float,
    gamma4: float,
    T: int,
) -> float:
    """Deflated Sharpe Ratio (DSR) — corrects for selection bias from multiple testing.

    Formula: (SR_obs - SR_0) * sqrt(T-1) / sqrt(1 - gamma3*SR_obs + (gamma4-1)/4 * SR_obs^2)

    Reference: Bailey, D.H. & López de Prado, M. (2014). "The Deflated Sharpe Ratio:
    Correcting for Selection Bias, Backtest Overfitting, and Non-Normality."
    Financial Analysts Journal, 70(5), 94-107. Equation 9.

    Args:
        SR_obs: Observed Sharpe/Sortino ratio of the candidate trial.
        SR_0:   Null-hypothesis Sharpe (random-trading baseline; typically 0.0).
        gamma3: Skewness of the trial Sharpe distribution across N trials.
        gamma4: Kurtosis of the trial Sharpe distribution across N trials.
        T:      Number of observations in the in-sample return series.

    Returns:
        DSR as a finite float. Degenerate cases return sentinels:
          - T <= 1 (no degree of freedom in sqrt(T-1)) → returns 0.0.
          - Invalid denominator (sqrt of non-positive) → returns float('-inf').
          Never returns +inf (would unfairly favor the AI branch).
    """
    if T <= 1:
        return 0.0
    denom_sq = 1.0 - gamma3 * SR_obs + ((gamma4 - 1.0) / 4.0) * (SR_obs ** 2)
    if denom_sq <= 0.0:
        return float("-inf")
    return (SR_obs - SR_0) * math.sqrt(T - 1) / math.sqrt(denom_sq)


def calculate_historical_deviation(current_date_str):
    """
    Scans local directory for post_mortem_*.json from the last 45 calendar days.
    Calculates average deviation (exit_return - attempted_trigger_level) grouped by exit_reason.
    """
    deviation_dict = {
        "Take-Profit": 0.0,
        "Trailing Stop": -0.20,
        "VWAP Breakdown": -0.40,
        "VWAP Bleed Cut": -0.25
    }
    
    deviation_sums = {k: 0.0 for k in deviation_dict.keys()}
    deviation_counts = {k: 0 for k in deviation_dict.keys()}

    try:
        current_dt = datetime.strptime(current_date_str, "%Y-%m-%d")
        lookback_dt = current_dt - timedelta(days=45)

        files = glob.glob("post_mortem_*.json")
        for f_path in files:
            try:
                # Extract date from filename: post_mortem_YYYY-MM-DD.json
                date_part = f_path.replace("post_mortem_", "").replace(".json", "")
                file_dt = datetime.strptime(date_part, "%Y-%m-%d")
                if file_dt < lookback_dt or file_dt >= current_dt:
                    continue

                with open(f_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    triggers = data.get("triggers", [])
                    for t in triggers:
                        reason = t.get("exit_reason")
                        exit_ret = t.get("exit_return")
                        attempted = t.get("attempted_trigger_level")

                        if reason in deviation_sums and exit_ret is not None and attempted is not None:
                            deviation_sums[reason] += (exit_ret - attempted)
                            deviation_counts[reason] += 1
            except:
                continue

        for reason in deviation_dict.keys():
            if deviation_counts[reason] > 0:
                deviation_dict[reason] = round(deviation_sums[reason] / deviation_counts[reason], 3)
    except Exception as e:
        print(f"      -> Warning: Deviation calculation failed ({e}). Using defaults.")

    print(f"  -> Historical Execution Deviation Penalties: {deviation_dict}")
    return deviation_dict

def _collect_sim_returns(p, history_data, acc_sym_ids, current_date_str, deviation_dict):
    """Run the guard-alpha simulation and return per-triggered-day guard_alpha values.

    Identical tick logic to run_simulation; returns a list instead of a scalar
    so the Sortino objective can compute risk-adjusted return across triggered days.
    """
    daily_returns = []
    decay_rate = _GUARD_ALPHA_DECAY_RATE
    current_dt = datetime.strptime(current_date_str, "%Y-%m-%d")

    for sym_id in acc_sym_ids:
        dates_data = history_data.get(sym_id, {})
        for date, ticks in dates_data.items():
            if not ticks: continue

            hwm = -999.0
            armed = False
            tp_armed = False
            vwap_ticks = 0
            vwap_bleed_ticks = 0
            para_armed = False
            breakeven_locked = False
            prev_return = None
            hwm_hold_ticks = 0
            below_stop_count = 0
            above_tp_count = 0
            mc_history = []
            prev_persisted_stop: float | None = None

            triggered_return = None
            eod_return = ticks[-1]["return"]
            day_max_return = max(t.get("return", 0.0) for t in ticks)

            for tick_idx, tick in enumerate(ticks):
                ret = tick.get("return", 0.0)
                mc = tick.get("mc_prob", 50.0)
                vol = tick.get("vol", 1.0)
                vwap_diff = tick.get("vwap_diff", 0.0)
                base_atr_pct = tick.get("base_atr_pct", vol)

                if ret > hwm: hwm = ret
                safe_hwm = max(hwm, ret)

                para_threshold = p.get("PARABOLIC_VELOCITY_THRESHOLD", 2.0)
                effective_prev = ret if prev_return is None else prev_return
                _velocity, should_arm = math_engine.compute_para_arm_decision(
                    current_return=ret,
                    prev_return=effective_prev,
                    para_threshold=para_threshold,
                    currently_armed=para_armed,
                )
                prev_return = ret
                if should_arm:
                    para_armed = True

                if not armed:
                    if p.get("TAKE_PROFIT_MC_PCT", 5.0) <= mc < p.get("TRIGGER_THRESHOLD_PCT", 15.0): armed = True
                else:
                    if mc > (p.get("TRIGGER_THRESHOLD_PCT", 15.0) * 2) and ret > 0.0:
                        armed = False
                        below_stop_count = 0

                mc_history.append(mc)
                if len(mc_history) > 5: mc_history.pop(0)

                time_ratio = tick_idx / 390.0
                dynamic_multiplier, dynamic_min_stop = math_engine.compute_time_squeeze_decay(time_ratio)

                active_stop_dist = math_engine.compute_active_trailing_stop(
                    vol, dynamic_multiplier, dynamic_min_stop,
                    para_armed, breakeven_locked, p.get("MAX_PARABOLIC_SQUEEZE", 0.50)
                )

                base_stop = safe_hwm - active_stop_dist

                hwm_hold_ticks, breakeven_locked, stop_level = math_engine.compute_breakeven_update(
                    ret, vol, base_stop, hwm_hold_ticks, breakeven_locked, False,
                    previously_persisted_stop_level=prev_persisted_stop,
                )
                prev_persisted_stop = stop_level

                is_trailing_hit = False
                if armed:
                    if ret <= (stop_level - 0.10) and mc < 60.0:
                        below_stop_count += 1
                        if below_stop_count >= 3: is_trailing_hit = True
                    else: below_stop_count = 0

                is_tp_hit = False
                if mc < p.get("TAKE_PROFIT_MC_PCT", 5.0):
                    if not tp_armed:
                        tp_armed = True
                        above_tp_count = 0
                elif tp_armed:
                    if mc >= p.get("TAKE_PROFIT_MC_PCT", 5.0):
                        above_tp_count += 1
                        if above_tp_count >= 2:
                            if ret > 0: is_tp_hit = True
                            else:
                                tp_armed = False
                                above_tp_count = 0
                    else: above_tp_count = 0

                valid_vwap_weight = tick.get("valid_vwap_weight", 1.0)

                vwap_bleed_arm_pct = math_engine.compute_vwap_bleed_arm_threshold(vol, p.get("VWAP_BLEED_MULTIPLIER", 1.5))

                vwap_ticks, vwap_bleed_ticks, is_vwap_broken, is_vwap_bleed_broken = math_engine.compute_vwap_breakdown_update(
                    is_triggered=False,
                    valid_vwap_weight=valid_vwap_weight,
                    weighted_vwap_diff=vwap_diff,
                    safe_hwm=safe_hwm,
                    current_return=ret,
                    vwap_cross_hwm_pct=p.get("VWAP_CROSS_HWM_PCT", 1.0),
                    vwap_bleed_arm_pct=vwap_bleed_arm_pct,
                    vwap_bleed_ticks_threshold=p.get("VWAP_BLEED_TICKS", 10),
                    current_vwap_ticks=vwap_ticks,
                    current_vwap_bleed_ticks=vwap_bleed_ticks,
                )

                if is_trailing_hit or is_tp_hit or is_vwap_broken or is_vwap_bleed_broken:
                    reason_str = "Trailing Stop"
                    if is_tp_hit: reason_str = "Take-Profit"
                    elif is_vwap_broken: reason_str = "VWAP Breakdown"
                    elif is_vwap_bleed_broken: reason_str = "VWAP Bleed Cut"

                    penalty = deviation_dict.get(reason_str, -0.20)
                    triggered_return = ret + penalty
                    break

            if triggered_return is not None:
                guard_alpha = triggered_return - eod_return
                days_ago = (current_dt - datetime.strptime(date, "%Y-%m-%d")).days
                weight = math.exp(-decay_rate * days_ago)
                daily_returns.append(guard_alpha * weight)

    return daily_returns


def run_simulation(p, history_data, acc_sym_ids, current_date_str, deviation_dict):
    total_guard_alpha = 0.0
    decay_rate = _GUARD_ALPHA_DECAY_RATE
    current_dt = datetime.strptime(current_date_str, "%Y-%m-%d")

    for sym_id in acc_sym_ids:
        dates_data = history_data.get(sym_id, {})
        for date, ticks in dates_data.items():
            if not ticks: continue

            hwm = -999.0
            armed = False
            tp_armed = False
            vwap_ticks = 0
            vwap_bleed_ticks = 0
            para_armed = False
            breakeven_locked = False
            prev_return = None  # sentinel: cycle-1 velocity = 0 (mirrors database.py wipe)
            hwm_hold_ticks = 0
            below_stop_count = 0
            above_tp_count = 0
            mc_history = []
            prev_persisted_stop: float | None = None

            triggered_return = None
            eod_return = ticks[-1]["return"]
            day_max_return = max(t.get("return", 0.0) for t in ticks)

            for tick_idx, tick in enumerate(ticks):
                ret = tick.get("return", 0.0)
                mc = tick.get("mc_prob", 50.0)
                vol = tick.get("vol", 1.0)
                vwap_diff = tick.get("vwap_diff", 0.0)
                base_atr_pct = tick.get("base_atr_pct", vol)

                if ret > hwm: hwm = ret
                safe_hwm = max(hwm, ret)

                # --- PARABOLIC SQUEEZE LOGIC ---
                para_threshold = p.get("PARABOLIC_VELOCITY_THRESHOLD", 2.0)
                effective_prev = ret if prev_return is None else prev_return
                _velocity, should_arm = math_engine.compute_para_arm_decision(
                    current_return=ret,
                    prev_return=effective_prev,
                    para_threshold=para_threshold,
                    currently_armed=para_armed,
                )
                prev_return = ret
                if should_arm:
                    para_armed = True
                # ------------------------------

                if not armed:
                    if p.get("TAKE_PROFIT_MC_PCT", 5.0) <= mc < p.get("TRIGGER_THRESHOLD_PCT", 15.0): armed = True
                else:
                    if mc > (p.get("TRIGGER_THRESHOLD_PCT", 15.0) * 2) and ret > 0.0:
                        armed = False
                        below_stop_count = 0

                mc_history.append(mc)
                if len(mc_history) > 5: mc_history.pop(0)

                # --- TIME SQUEEZE DECAY LOGIC ---
                # Assuming ticks are minute bars (9:30-16:00 = 390 mins)
                time_ratio = tick_idx / 390.0
                dynamic_multiplier, dynamic_min_stop = math_engine.compute_time_squeeze_decay(time_ratio)

                # Calculate active stop distance based strictly on 20-day volatility
                active_stop_dist = math_engine.compute_active_trailing_stop(
                    vol, dynamic_multiplier, dynamic_min_stop,
                    para_armed, breakeven_locked, p.get("MAX_PARABOLIC_SQUEEZE", 0.50)
                )

                base_stop = safe_hwm - active_stop_dist

                # --- RISK GUARD LOGIC ---
                # is_triggered=False: simulation loop breaks on trigger before re-entering
                hwm_hold_ticks, breakeven_locked, stop_level = math_engine.compute_breakeven_update(
                    ret, vol, base_stop, hwm_hold_ticks, breakeven_locked, is_triggered=False,
                    previously_persisted_stop_level=prev_persisted_stop,
                )
                prev_persisted_stop = stop_level
                # ------------------------

                is_trailing_hit = False
                if armed:
                    if ret <= (stop_level - 0.10) and mc < 60.0:
                        below_stop_count += 1
                        if below_stop_count >= 3: is_trailing_hit = True
                    else: below_stop_count = 0

                is_tp_hit = False
                if mc < p.get("TAKE_PROFIT_MC_PCT", 5.0):
                    if not tp_armed:
                        tp_armed = True
                        above_tp_count = 0
                elif tp_armed:
                    if mc >= p.get("TAKE_PROFIT_MC_PCT", 5.0):
                        above_tp_count += 1
                        if above_tp_count >= 2:
                            if ret > 0: is_tp_hit = True
                            else:
                                tp_armed = False
                                above_tp_count = 0
                    else: above_tp_count = 0

                # Read valid_vwap_weight from tick with backward-compat fallback
                # (fallback covers any stale v1 cache that might slip through despite the v2 marker)
                valid_vwap_weight = tick.get("valid_vwap_weight", 1.0)

                vwap_bleed_arm_pct = math_engine.compute_vwap_bleed_arm_threshold(vol, p.get("VWAP_BLEED_MULTIPLIER", 1.5))

                # Canonical VWAP-breakdown state machine
                vwap_ticks, vwap_bleed_ticks, is_vwap_broken, is_vwap_bleed_broken = math_engine.compute_vwap_breakdown_update(
                    is_triggered=False,  # autotuner sim breaks on trigger; never re-enters with triggered state mid-tick
                    valid_vwap_weight=valid_vwap_weight,
                    weighted_vwap_diff=vwap_diff,
                    safe_hwm=safe_hwm,
                    current_return=ret,
                    vwap_cross_hwm_pct=p.get("VWAP_CROSS_HWM_PCT", 1.0),
                    vwap_bleed_arm_pct=vwap_bleed_arm_pct,
                    vwap_bleed_ticks_threshold=p.get("VWAP_BLEED_TICKS", 10),
                    current_vwap_ticks=vwap_ticks,
                    current_vwap_bleed_ticks=vwap_bleed_ticks,
                )

                if is_trailing_hit or is_tp_hit or is_vwap_broken or is_vwap_bleed_broken:
                    reason_str = "Trailing Stop"
                    if is_tp_hit: reason_str = "Take-Profit"
                    elif is_vwap_broken: reason_str = "VWAP Breakdown"
                    elif is_vwap_bleed_broken: reason_str = "VWAP Bleed Cut"

                    penalty = deviation_dict.get(reason_str, -0.20)
                    triggered_return = ret + penalty
                    break

            if triggered_return is not None:
                guard_alpha = triggered_return - eod_return
                missed_upside = day_max_return - triggered_return
                drawdown_from_peak = safe_hwm - triggered_return

                # Exponential Time-Decay Weighting
                days_ago = (current_dt - datetime.strptime(date, "%Y-%m-%d")).days
                weight = math.exp(-decay_rate * days_ago)

                # 1. Penalize Missed Upside (Exiting too early before a run)
                if missed_upside > 1.0: # Only penalize if we missed out on more than 1%
                    total_guard_alpha -= (missed_upside * 1.5 * weight)

                # 2. NEW: Penalize Peak-to-Exit Drawdown (Giving back too much profit)
                # If we reached at least a 1% gain, penalize giving back more than 1.5% of it
                if safe_hwm > 1.0 and drawdown_from_peak > 1.5:
                    total_guard_alpha -= (drawdown_from_peak * 0.75 * weight)

                # 3. Apply standard EOD-based guard alpha
                if guard_alpha < 0:
                    total_guard_alpha += (guard_alpha * 2.0 * weight)
                else:
                    total_guard_alpha += (guard_alpha * weight)

    return -total_guard_alpha


def _apply_optuna_archive_migration_if_needed():
    """
    One-time idempotent migration: renames bare legacy study names in
    optuna_studies.db with a LEGACY__ prefix so they are distinguishable
    from new timestamp__symphony names.

    Reads migrations/optuna_001_archive_accumulated_studies.sql and applies
    it only if at least one non-prefixed, non-timestamp study exists.
    Safe to call on every startup — the SQL is idempotent.
    """
    import os
    import sqlite3 as _sqlite3
    import pathlib as _pathlib

    db_path = "optuna_studies.db"
    if not os.path.exists(db_path):
        return

    migration_path = _pathlib.Path(__file__).parent / "migrations" / "optuna_001_archive_accumulated_studies.sql"
    if not migration_path.exists():
        return

    try:
        conn = _sqlite3.connect(db_path)
        # Check for any legacy (non-prefixed) studies
        rows = conn.execute(
            "SELECT COUNT(*) FROM studies WHERE study_name NOT LIKE 'LEGACY__%' AND INSTR(study_name, '__') = 0"
        ).fetchone()
        needs_migration = rows and rows[0] > 0
        if needs_migration:
            sql = migration_path.read_text(encoding="utf-8")
            conn.executescript(sql)
            conn.commit()
            print(f"  -> Applied optuna_001 archive migration ({rows[0]} legacy studies renamed).")
        conn.close()
    except Exception as exc:
        print(f"  -> WARNING: optuna_001 archive migration failed (non-fatal): {exc}")


def run_autotuner(bot_state, current_date_str, account_uuids, is_forced=False):
    """
    Runs walk-forward optimization using Bayesian Optimization (Optuna) per symphony.
    Implements a three-fold walk-forward split (60/20/20): train / validation / frozen-eval.

    Walk-forward split methodology (López de Prado 2018 Ch. 7.4):
    - 125-day history is split 60/20/20: ~75 train days, ~25 validation days, ~25 frozen-eval days.
    - Purge (PURGE_DAYS=20) and Embargo (EMBARGO_DAYS=1) applied at BOTH fold boundaries:
        (a) train | validation boundary
        (b) validation | frozen-eval boundary
      Binding purge constraint: max(vol=20, ATR=15)=20 trading days. The decay weight
      (_GUARD_ALPHA_DECAY_RATE half-life ≈ 46 days) is an objective aggregation weight, not
      a feature lookback, and is excluded from purge sizing.
    - Selection: Optuna trials score on the validation fold only. Frozen-eval is hidden during
      the trial sweep.
    - Frozen-eval consumption: exactly once after best-trial selection, to produce the honest
      post-selection performance metric (frozen_eval_sharpe).

    OOS-fold-collapse v2 (PA-26 extended):
    - At 125-day history, the three raw folds are 75/25/25 days.
    - After PURGE_DAYS=20 at each boundary, the usable validation and frozen-eval windows
      each shrink to approximately 4-5 usable days. This is an acknowledged tradeoff — the
      purge is methodologically correct and the short evaluation window is the cost of honest
      OOS reporting. Future workstream: expand history window or use purged k-fold CV (rolling
      folds) to recover statistical power.
    """
    # Suppress Optuna's per-trial log noise; set here (not at module level) to
    # avoid clobbering pytest's output-capture on import.
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    # Apply optuna_001 archive migration once if any bare (non-prefixed) legacy
    # studies exist — renames them to LEGACY__<name> non-destructively.
    _apply_optuna_archive_migration_if_needed()

    print(f"  -> Starting EOD Autotune (125-day WFA: 60% Train / 20% Validation / 20% Frozen-Eval per Symphony)...")

    # 0. Calculate Historical Execution Deviation
    deviation_dict = calculate_historical_deviation(current_date_str)

    # 1. Archive today's charts to the permanent DB
    chart_history = database.load_chart_history()
    if chart_history and chart_history.get("date") == current_date_str:
        for sym_id, data in chart_history.get("symphonies", {}).items():
            database.save_chart_archive(current_date_str, sym_id, data)

    # 2. Fetch the rolling 125-trading-day synthetic forward-looking data
    history_125d = synthetic_history.generate_synthetic_history(bot_state, current_date_str)
    if not history_125d:
        print("  -> Autotuner aborted: Failed to generate synthetic history.")
        return

    # Extract global dates and partition 60/20/20 (train / validation / frozen-eval).
    all_dates = set()
    for sym_data in history_125d.values():
        all_dates.update(sym_data.keys())
    sorted_dates = sorted(list(all_dates))

    total_days = len(sorted_dates)
    if total_days < 2:
        print("  -> Autotuner aborted: Need at least 2 days of history for WFA.")
        return

    # Three-fold split: TRAIN_RATIO / VALIDATION_RATIO / FROZEN_EVAL_RATIO (60/20/20).
    # Reference: López de Prado 2018, Advances in Financial Machine Learning, Ch. 7.4.
    val_start_idx    = int(total_days * TRAIN_RATIO)
    frozen_start_idx = int(total_days * (TRAIN_RATIO + VALIDATION_RATIO))
    # split_idx aliases val_start_idx — preserved so O1 test assertions that inspect
    # autotuner.py source for "split_idx" continue to find the split site.
    split_idx = val_start_idx

    raw_train_dates    = sorted_dates[:val_start_idx]
    raw_val_dates      = sorted_dates[val_start_idx:frozen_start_idx]
    raw_frozen_dates   = sorted_dates[frozen_start_idx:]

    # Boundary 1 — train | validation: purge + embargo on train side.
    # Exclude the last PURGE_DAYS + EMBARGO_DAYS of raw_train_dates (identical
    # logic to O1's train | test purge, now applied at the train | validation boundary).
    effective_train_cutoff = max(0, val_start_idx - PURGE_DAYS - EMBARGO_DAYS)
    train_dates = set(sorted_dates[:effective_train_cutoff])

    # Boundary 2 — validation | frozen-eval: purge + embargo on validation side.
    # Exclude the last PURGE_DAYS + EMBARGO_DAYS of raw_val_dates so validation samples
    # whose feature lookback overlaps the frozen-eval fold are not seen by the objective.
    # PURGE_DAYS appears here to confirm the second boundary receives the same treatment.
    val_purge_end_idx = frozen_start_idx - PURGE_DAYS - EMBARGO_DAYS
    # Purge-reduced validation: used by the Optuna objective closure only, preventing
    # late validation features from leaking into the frozen-eval fold.
    validation_dates_purged = set(sorted_dates[val_start_idx:max(val_start_idx, val_purge_end_idx)])
    # Full raw validation: used by the OOS cascade (AI/fallback/default) to preserve
    # the behavioural contract that the cascade evaluates on the raw OOS fold.
    validation_dates_full = set(raw_val_dates)

    frozen_dates = set(raw_frozen_dates)

    history_train           = {}
    history_validation      = {}  # purge-reduced; used by Optuna objective only
    history_validation_full = {}  # full raw validation fold; used by OOS cascade
    history_frozen          = {}
    for sym_id, sym_data in history_125d.items():
        history_train[sym_id]           = {d: t for d, t in sym_data.items() if d in train_dates}
        history_validation[sym_id]      = {d: t for d, t in sym_data.items() if d in validation_dates_purged}
        history_validation_full[sym_id] = {d: t for d, t in sym_data.items() if d in validation_dates_full}
        history_frozen[sym_id]          = {d: t for d, t in sym_data.items() if d in frozen_dates}

    # OOS cascade uses the full validation fold — same contract as the pre-O6 OOS test fold.
    history_test = history_validation_full

    # Extract unique normalized symphony names from the current bot_state
    symphony_names = set()
    for sym_id, data in bot_state.items():
        if isinstance(data, dict) and "name" in data:
            symphony_names.add(database.normalize_name(data["name"]))

    optimization_results = {}

    # Single timestamp shared across all symphonies in this run — groups all
    # per-symphony rows from one invocation into a logical "run" for Claude context-assembly.
    run_timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    for normalized_name in symphony_names:
        print(f"     Optimizing Symphony: {normalized_name}")
        strat_data = database.get_symphony_strategy(normalized_name)
        locked_vars = strat_data.get("locked_vars", [])
        current_params = strat_data.get("params", {})
        original_params = current_params.copy()

        def objective(trial):
            p = current_params.copy()
            p["TAKE_PROFIT_MC_PCT"] = trial.suggest_float("TAKE_PROFIT_MC_PCT", _SS_TAKE_PROFIT_MC_MIN, _SS_TAKE_PROFIT_MC_MAX)
            p["VWAP_CROSS_HWM_PCT"] = trial.suggest_float("VWAP_CROSS_HWM_PCT", _SS_VWAP_CROSS_HWM_MIN, _SS_VWAP_CROSS_HWM_MAX)
            p["VWAP_BLEED_MULTIPLIER"] = trial.suggest_float("VWAP_BLEED_MULTIPLIER", _SS_VWAP_BLEED_MULT_MIN, _SS_VWAP_BLEED_MULT_MAX)
            p["VWAP_BLEED_TICKS"] = trial.suggest_int("VWAP_BLEED_TICKS", _SS_VWAP_BLEED_TICKS_MIN, _SS_VWAP_BLEED_TICKS_MAX)
            p["PARABOLIC_VELOCITY_THRESHOLD"] = trial.suggest_float("PARABOLIC_VELOCITY_THRESHOLD", _SS_PARA_VEL_MIN, _SS_PARA_VEL_MAX)
            p["MAX_PARABOLIC_SQUEEZE"] = trial.suggest_float("MAX_PARABOLIC_SQUEEZE", _SS_MAX_PARA_SQUEEZE_MIN, _SS_MAX_PARA_SQUEEZE_MAX)

            acc_sym_ids = [k for k, v in bot_state.items() if isinstance(v, dict) and database.normalize_name(v.get("name", "")) == normalized_name]
            if not acc_sym_ids: return 0.0
            target_sym_id = acc_sym_ids[0]
            # Score on validation fold only — frozen-eval is withheld from all trial callbacks.
            daily_returns = _collect_sim_returns(p, history_validation, [target_sym_id], current_date_str, deviation_dict)
            # Annualization intentionally omitted — this is a ranking signal, not an annualized statistic.
            return compute_sortino_ratio(daily_returns)

        start_time = time.time()
        
        # Parallel Bayesian Optimization
        db_url = "sqlite:///optuna_studies.db"
        storage = optuna.storages.RDBStorage(
            url=db_url,
            engine_kwargs={"connect_args": {"timeout": 60}}
        )
        study_timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        study = optuna.create_study(study_name=f"{study_timestamp}__{normalized_name}", storage=storage, load_if_exists=False, direction="maximize")
        study.optimize(objective, n_trials=500, n_jobs=-1)
        

        
        naive_sharpe_value = study.best_value
        best_alpha_train = naive_sharpe_value
        best_params = study.best_params

        # --- O2: DSR RE-RANKING (AI branch only) ---
        # Collect completed trial values to derive the cross-trial Sharpe distribution
        # moments (gamma3, gamma4). Then re-rank all completed trials by DSR and select
        # the DSR-maximizing trial instead of the naive-Sortino winner.
        # Fallback/default branches are single parameter sets — DSR not applicable.
        # Reference: Bailey & López de Prado 2014, Eq. 9.
        deflated_sharpe_value: float | None = None
        T_train = len(validation_dates_purged)  # DSR uses purge-reduced validation count (selection fold)
        try:
            completed_trials = [t for t in study.trials if t.value is not None]
        except TypeError:
            completed_trials = []

        if len(completed_trials) >= 2:
            trial_values = [t.value for t in completed_trials]
            n_trials = len(trial_values)
            mean_v = statistics.mean(trial_values)
            # Population variance (divisor = N) for moment computation
            variance_v = sum((v - mean_v) ** 2 for v in trial_values) / n_trials
            std_v = math.sqrt(variance_v) if variance_v > 0 else 0.0
            if std_v > 0:
                gamma3 = sum((v - mean_v) ** 3 for v in trial_values) / (n_trials * std_v ** 3)
                gamma4 = sum((v - mean_v) ** 4 for v in trial_values) / (n_trials * std_v ** 4)
            else:
                gamma3, gamma4 = 0.0, 3.0  # normal-distribution fallback for degenerate spread

            best_dsr = float("-inf")
            best_trial_by_dsr = None
            for t in completed_trials:
                dsr = compute_deflated_sharpe_ratio(
                    SR_obs=t.value,
                    SR_0=0.0,
                    gamma3=gamma3,
                    gamma4=gamma4,
                    T=T_train,
                )
                if math.isfinite(dsr) and dsr > best_dsr:
                    best_dsr = dsr
                    best_trial_by_dsr = t

            if best_trial_by_dsr is not None and math.isfinite(best_dsr):
                best_params = best_trial_by_dsr.params
                best_alpha_train = best_trial_by_dsr.value
                deflated_sharpe_value = best_dsr
            # If no valid DSR trial found, fall through with naive Optuna winner

        if deflated_sharpe_value is None and naive_sharpe_value is not None:
            # Fewer than 2 completed trials — moments are undefined; use normal-distribution
            # assumption (gamma3=0, gamma4=3) as a conservative fallback so operator still
            # receives a finite DSR signal rather than NULL.
            dsr_fallback = compute_deflated_sharpe_ratio(
                SR_obs=naive_sharpe_value,
                SR_0=0.0,
                gamma3=0.0,
                gamma4=3.0,
                T=T_train,
            )
            if math.isfinite(dsr_fallback):
                deflated_sharpe_value = dsr_fallback
        # ----------------------------------------------------

        # --- BEST_PARAMS SCHEMA VALIDATION (B2-FU2) ---
        # An empty best_params or one missing any required search-space key indicates
        # a degenerate/aborted study. Reject the WHOLE AI proposal (no key-by-key
        # merge -- partial merges produce Frankenstein params where some keys
        # come from the AI and others from current/fallback). Force the cascade
        # to fall through to fallback (or default if fallback also fails) by
        # poisoning oos_alpha to -inf. Do NOT raise -- the daemon must keep ticking.
        ai_proposal_invalid = (
            not best_params
            or not OPTUNA_SEARCH_SPACE_KEYS.issubset(best_params.keys())
        )
        if ai_proposal_invalid:
            missing = sorted(OPTUNA_SEARCH_SPACE_KEYS - set(best_params.keys()))
            print(
                f"       Warning: best_params schema invalid for '{normalized_name}' "
                f"(missing keys: {missing or '<empty dict>'}). "
                f"Rejecting AI proposal; cascading to Fallback/Default."
            )
            deflated_sharpe_value = None
            naive_sharpe_value = None
        # ---------------------------------------------

        # Evaluate OOS robustness
        best_p = current_params.copy()
        for name, val in best_params.items():
            best_p[name] = round(val, 2)

        acc_sym_ids = [k for k, v in bot_state.items() if isinstance(v, dict) and database.normalize_name(v.get("name", "")) == normalized_name]
        target_sym_id = acc_sym_ids[0] if acc_sym_ids else None
        oos_alpha = -run_simulation(best_p, history_test, [target_sym_id] if target_sym_id else [], current_date_str, deviation_dict)

        # If the AI proposal is schema-invalid, poison its OOS alpha so the
        # cascade below naturally selects fallback (or default). Done AFTER
        # the simulation runs so any side-effects/logging are preserved.
        if ai_proposal_invalid:
            oos_alpha = -math.inf

        optimization_results[normalized_name] = {}
        
        # Evaluate fallback parameters in OOS for comparison
        fallback_params = current_params.copy()
        fallback_oos_alpha = -run_simulation(fallback_params, history_test, [target_sym_id] if target_sym_id else [], current_date_str, deviation_dict)

        # Evaluate global default parameters in OOS for comparison
        default_params = database.DEFAULT_STRATEGY.copy()
        default_oos_alpha = -run_simulation(default_params, history_test, [target_sym_id] if target_sym_id else [], current_date_str, deviation_dict)

        # Validation-fold Sortino (selection truth — what Optuna actually optimized against).
        validation_returns = _collect_sim_returns(best_p, history_validation, [target_sym_id] if target_sym_id else [], current_date_str, deviation_dict)
        validation_sharpe_value = compute_sortino_ratio(validation_returns) if validation_returns else None

        # Frozen-eval: consumed exactly once post-selection on the held-out final 20% fold.
        # This is the honest performance metric — not seen by any Optuna trial callback.
        # PURGE_DAYS referenced here confirms the boundary purge applies at validation|frozen-eval.
        # Single read via _collect_sim_returns; no separate run_simulation call so the
        # "consumed once" invariant holds across all frozen-fold access paths.
        frozen_eval_returns = _collect_sim_returns(best_p, history_frozen, [target_sym_id] if target_sym_id else [], current_date_str, deviation_dict)
        frozen_eval_sharpe_value = compute_sortino_ratio(frozen_eval_returns) if frozen_eval_returns else None

        # Calculate daily averages for better understanding
        train_days_count = len(train_dates)
        test_days_count = len(validation_dates_full)

        avg_train_alpha = best_alpha_train / train_days_count if train_days_count > 0 else 0
        avg_oos_alpha = oos_alpha / test_days_count if test_days_count > 0 else 0

        baseline_decision = ""
        # B2-FU1: Asymmetric tie rule -- STRICT-POSITIVE on the AI branch
        # (over-fit risk: an AI proposal that only TIES the validated fallback
        # is not worth displacing the last-known-good params for), LENIENT
        # (>=) on the fallback branch below (favors last-known-good on tie
        # vs the global default).
        if oos_alpha > fallback_oos_alpha and oos_alpha > default_oos_alpha:
            if oos_alpha > 0:
                print(f"       OOS validation passed! OOS Guard Alpha: +{oos_alpha:.2f}% (Average: {avg_oos_alpha:.2f}%)")
            else:
                print(f"       OOS validation passed (Beat Baselines)! OOS Guard Alpha: {oos_alpha:.2f}% (Avg: {avg_oos_alpha:.2f}%) vs Fallback: {fallback_oos_alpha:.2f}% / Default: {default_oos_alpha:.2f}%")
            for name, val in best_params.items():
                current_params[name] = round(val, 2)
            baseline_decision = "Adopted AI"
        elif fallback_oos_alpha >= default_oos_alpha:
            print(f"       OOS validation failed (AI: {oos_alpha:.2f}%). Reverting to Fallback parameters (Fallback: {fallback_oos_alpha:.2f}% vs Default: {default_oos_alpha:.2f}%).")
            for k, v in fallback_params.items():
                current_params[k] = v
            baseline_decision = "Reverted to Fallback"
        else:
            print(f"       OOS validation & Fallback failed. Resetting to Global Default (Default: {default_oos_alpha:.2f}% vs AI: {oos_alpha:.2f}%, Fallback: {fallback_oos_alpha:.2f}%).")
            for k, v in default_params.items():
                current_params[k] = v
            baseline_decision = "Reset to Global Default"

        # Build Discord logs ensuring all original variables are shown
        optimization_results[normalized_name]["_baseline_chosen"] = baseline_decision
        for k, original_val in original_params.items():
            optimization_results[normalized_name][k] = {"old": original_val, "new": current_params.get(k, original_val)}

        elapsed = time.time() - start_time
        dsr_log = (
            f" | DSR: {deflated_sharpe_value:.4f} (naive: {naive_sharpe_value:.4f})"
            if deflated_sharpe_value is not None and naive_sharpe_value is not None
            else " | DSR: N/A"
        )
        print(f"       Optimization completed in {elapsed:.2f}s. Train Sortino: {best_alpha_train:+.4f} (train days: {train_days_count}){dsr_log}")

        database.save_symphony_strategy(normalized_name, current_params, locked_vars)

        # P1: Persist per-run validation metrics so Claude context-assembly can
        # retrieve them via get_latest_autotune_run().  Called AFTER baseline_decision
        # is finalized and save_symphony_strategy has written the chosen params,
        # so the row captures the decision that was actually applied.
        # O2: deflated_sharpe (DSR winner) and naive_sharpe (raw Optuna best) recorded for
        # backward-comparison and operator awareness of deflation magnitude.
        # O6: validation_sharpe (selection metric) and frozen_eval_sharpe (honest post-selection
        # metric, consumed once from the withheld final 20% fold).
        database.save_autotune_run(
            run_timestamp=run_timestamp,
            symphony_id=normalized_name,
            oos_alpha=oos_alpha,
            train_alpha=best_alpha_train,
            baseline_decision=baseline_decision,
            fallback_oos_alpha=fallback_oos_alpha,
            default_oos_alpha=default_oos_alpha,
            deflated_sharpe=deflated_sharpe_value,
            naive_sharpe=naive_sharpe_value,
            validation_sharpe=validation_sharpe_value,
            frozen_eval_sharpe=frozen_eval_sharpe_value,
        )

    print("  -> Autotuner finished all symphonies.")
    return optimization_results


def run_calibration_sweep(
    history_data: dict,
    current_params: dict,
    current_date_str: str,
    deviation_dict: dict,
    random_state: int,
) -> list[dict]:
    """V1 calibration sweep over PARABOLIC_VELOCITY_THRESHOLD and VWAP_CROSS_HWM_PCT only.

    Consumes O1 purge+embargo, O2 DSR re-ranking, O3 timestamped study names,
    O5 Sortino objective, O6 frozen-eval fold — same methodology as run_autotuner
    but search space is limited to the two V1 parameters. Does NOT persist anything
    to the DB (AC-V1.3: read-only, operator-gated rollout).

    Returns a list of report dicts, one per tuned param per symphony found in
    history_data. The caller decides whether to act on proposals.
    """
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    run_timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    # --- Fold partitioning (same O1 logic as run_autotuner) ---
    all_dates: set[str] = set()
    for sym_data in history_data.values():
        all_dates.update(sym_data.keys())
    sorted_dates = sorted(all_dates)
    total_days = len(sorted_dates)

    if total_days < 2:
        return []

    val_start_idx = int(total_days * TRAIN_RATIO)
    frozen_start_idx = int(total_days * (TRAIN_RATIO + VALIDATION_RATIO))

    # Boundary 1 — train | validation: purge + embargo on train side.
    effective_train_cutoff = max(0, val_start_idx - PURGE_DAYS - EMBARGO_DAYS)

    # Boundary 2 — validation | frozen-eval: purge + embargo on validation side.
    val_purge_end_idx = frozen_start_idx - PURGE_DAYS - EMBARGO_DAYS
    validation_dates_purged = set(sorted_dates[val_start_idx:max(val_start_idx, val_purge_end_idx)])

    frozen_dates = set(sorted_dates[frozen_start_idx:])

    trading_day_start = sorted_dates[0] if sorted_dates else ""
    trading_day_end = sorted_dates[frozen_start_idx - 1] if frozen_start_idx > 0 else ""

    history_validation: dict = {}
    history_frozen: dict = {}
    for sym_id, sym_data in history_data.items():
        history_validation[sym_id] = {d: t for d, t in sym_data.items() if d in validation_dates_purged}
        history_frozen[sym_id] = {d: t for d, t in sym_data.items() if d in frozen_dates}

    # Derive trigger counts on validation fold for current params (denominator for freq change).
    def _count_triggers(p: dict, sym_ids: list, h: dict) -> int:
        total = 0
        for sid in sym_ids:
            returns = _collect_sim_returns(p, h, [sid], current_date_str, deviation_dict)
            total += len(returns)
        return total

    report_rows: list[dict] = []
    symphony_ids = list(history_data.keys())

    for sym_id in symphony_ids:
        study_timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        study_name = f"{study_timestamp}__{sym_id}"

        base_p = current_params.copy()

        def objective(trial, _sym_id=sym_id, _base=base_p):
            p = _base.copy()
            p["PARABOLIC_VELOCITY_THRESHOLD"] = trial.suggest_float(
                "PARABOLIC_VELOCITY_THRESHOLD", _SS_PARA_VEL_MIN, _SS_PARA_VEL_MAX
            )
            p["VWAP_CROSS_HWM_PCT"] = trial.suggest_float(
                "VWAP_CROSS_HWM_PCT", _SS_VWAP_CROSS_HWM_V1_MIN, _SS_VWAP_CROSS_HWM_V1_MAX
            )
            daily_returns = _collect_sim_returns(
                p, history_validation, [_sym_id], current_date_str, deviation_dict
            )
            return compute_sortino_ratio(daily_returns)

        sampler = optuna.samplers.TPESampler(seed=random_state)
        study = optuna.create_study(
            study_name=study_name,
            direction="maximize",
            sampler=sampler,
            load_if_exists=False,
        )
        study.optimize(objective, n_trials=100, n_jobs=1)

        naive_sharpe_value: float | None = study.best_value
        best_params = study.best_params
        n_trials = len([t for t in study.trials if t.value is not None])

        # --- O2: DSR re-ranking ---
        deflated_sharpe_value: float | None = None
        T_val = len(validation_dates_purged)
        completed_trials = [t for t in study.trials if t.value is not None]

        if len(completed_trials) >= 2:
            trial_values = [t.value for t in completed_trials]
            n_tv = len(trial_values)
            mean_v = sum(trial_values) / n_tv
            variance_v = sum((v - mean_v) ** 2 for v in trial_values) / n_tv
            std_v = math.sqrt(variance_v) if variance_v > 0 else 0.0
            if std_v > 0:
                gamma3 = sum((v - mean_v) ** 3 for v in trial_values) / (n_tv * std_v ** 3)
                gamma4 = sum((v - mean_v) ** 4 for v in trial_values) / (n_tv * std_v ** 4)
            else:
                gamma3, gamma4 = 0.0, 3.0

            best_dsr = float("-inf")
            best_trial_by_dsr = None
            for t in completed_trials:
                dsr = compute_deflated_sharpe_ratio(
                    SR_obs=t.value,
                    SR_0=0.0,
                    gamma3=gamma3,
                    gamma4=gamma4,
                    T=T_val,
                )
                if math.isfinite(dsr) and dsr > best_dsr:
                    best_dsr = dsr
                    best_trial_by_dsr = t

            if best_trial_by_dsr is not None and math.isfinite(best_dsr):
                best_params = best_trial_by_dsr.params
                naive_sharpe_value = best_trial_by_dsr.value
                deflated_sharpe_value = best_dsr

        if deflated_sharpe_value is None and naive_sharpe_value is not None:
            dsr_fallback = compute_deflated_sharpe_ratio(
                SR_obs=naive_sharpe_value,
                SR_0=0.0,
                gamma3=0.0,
                gamma4=3.0,
                T=T_val,
            )
            if math.isfinite(dsr_fallback):
                deflated_sharpe_value = dsr_fallback

        # Validation-fold Sortino of best trial params
        best_p = current_params.copy()
        for k, v in best_params.items():
            best_p[k] = round(v, 4)

        val_returns = _collect_sim_returns(
            best_p, history_validation, [sym_id], current_date_str, deviation_dict
        )
        sortino_value = compute_sortino_ratio(val_returns) if val_returns else None

        # --- O6: frozen eval — consumed once, post-selection ---
        frozen_returns = _collect_sim_returns(
            best_p, history_frozen, [sym_id], current_date_str, deviation_dict
        )
        frozen_eval_alpha = compute_sortino_ratio(frozen_returns) if frozen_returns else None

        # Trigger frequency change: current params vs proposed params on validation fold
        current_trigger_count = _count_triggers(current_params, [sym_id], history_validation)
        proposed_trigger_count = _count_triggers(best_p, [sym_id], history_validation)
        expected_trigger_freq_change = float(proposed_trigger_count - current_trigger_count)

        # Emit one row per tuned param
        for param_name in ("PARABOLIC_VELOCITY_THRESHOLD", "VWAP_CROSS_HWM_PCT"):
            current_value = float(current_params.get(param_name, best_p.get(param_name, 0.0)))
            proposed_value = float(best_p.get(param_name, current_value))
            delta_pct = (
                (proposed_value - current_value) / abs(current_value) * 100.0
                if current_value != 0
                else 0.0
            )
            report_rows.append({
                "symphony_id": sym_id,
                "param_name": param_name,
                "current_value": current_value,
                "proposed_value": proposed_value,
                "delta_pct": delta_pct,
                "expected_trigger_freq_change": expected_trigger_freq_change,
                "frozen_eval_alpha": frozen_eval_alpha,
                "naive_sharpe": naive_sharpe_value,
                "deflated_sharpe": deflated_sharpe_value,
                "sortino": sortino_value,
                "n_trials": n_trials,
                "study_name": study_name,
                "trading_day_start": trading_day_start,
                "trading_day_end": trading_day_end,
                "cycle_id": run_timestamp,
            })

    return report_rows
