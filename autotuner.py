import time
import math
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
OPTUNA_SEARCH_SPACE_KEYS = frozenset({
    "TRIGGER_THRESHOLD_PCT", "TAKE_PROFIT_MC_PCT", "VWAP_CROSS_HWM_PCT",
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

# Exponential time-decay rate applied to per-day guard-alpha in run_simulation
# and _collect_sim_returns. Half-life ≈ 46 trading days (ln2 / 0.015).
_GUARD_ALPHA_DECAY_RATE = 0.015

# Target return for Sortino denominator: capital preservation baseline (0 = break-even).
# Operator decision PA-5; Sortino & van der Meer 1994, J. Portfolio Management.
SORTINO_TARGET_RETURN = 0.0

# Walk-forward purge window: training samples whose feature lookback window overlaps
# the test fold are excluded. The binding constraint is the exponential decay half-life
# of the composite objective (ln2 / _GUARD_ALPHA_DECAY_RATE = ln2 / 0.015 ≈ 46 trading
# days), which exceeds the vol (20 days) and ATR (14 days) lookbacks.
# PURGE_DAYS = max(20, 14, 46) = 46.
# López de Prado 2018, Advances in Financial Machine Learning, Ch. 7 (Purged k-fold CV).
PURGE_DAYS = 46

# Embargo period between train-end and test-start. Prevents autocorrelation leakage
# from serial dependence in adjacent samples. Default: 1 trading day.
# López de Prado 2018, Advances in Financial Machine Learning, Ch. 7.
EMBARGO_DAYS = 1


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
                    ret, vol, base_stop, hwm_hold_ticks, breakeven_locked, False
                )

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
                    ret, vol, base_stop, hwm_hold_ticks, breakeven_locked, is_triggered=False
                )
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
    Runs a 6-month walk-forward optimization to find the best variables using Bayesian Optimization per account.
    Implements True Walk-Forward Analysis (80% train, 20% OOS test) with purge + embargo.

    Walk-forward split methodology (López de Prado 2018 Ch. 7):
    - 125-day history is split 80/20: ~100 train days, ~25 raw OOS test days.
    - Purge (PURGE_DAYS=46): train samples whose feature lookback window overlaps the test
      fold are excluded. The binding constraint is the decay-weighted objective's half-life
      (46 trading days), which exceeds vol (20) and ATR (14) lookbacks.
    - Embargo (EMBARGO_DAYS=1): one additional trading day gap between train-end and
      test-start prevents autocorrelation leakage from serial dependence.
    - OOS fold collapse (PA-26): after a 46-day purge on a 125-day window, the usable
      test fold shrinks to approximately 5 trading days. This is an acknowledged tradeoff —
      the purge is methodologically correct and the short test window is the cost of
      honest OOS evaluation. Future workstream: expand history window or use purged k-fold
      CV (rolling folds) to recover statistical power.
    """
    # Suppress Optuna's per-trial log noise; set here (not at module level) to
    # avoid clobbering pytest's output-capture on import.
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    # Apply optuna_001 archive migration once if any bare (non-prefixed) legacy
    # studies exist — renames them to LEGACY__<name> non-destructively.
    _apply_optuna_archive_migration_if_needed()

    print(f"  -> Starting EOD Autotune (125-day WFA: 80% Train / 20% OOS per Symphony)...")

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

    # Extract global dates and partition 80/20
    all_dates = set()
    for sym_data in history_125d.values():
        all_dates.update(sym_data.keys())
    sorted_dates = sorted(list(all_dates))
    
    total_days = len(sorted_dates)
    if total_days < 2:
        print("  -> Autotuner aborted: Need at least 2 days of history for WFA.")
        return

    # Use 80/20 split for ~100 days train, ~25 days out-of-sample test.
    # Purge + embargo applied per López de Prado 2018 Ch. 7 — see run_autotuner docstring.
    split_idx = int(total_days * 0.8)

    test_dates = set(sorted_dates[split_idx:])
    test_start_date = sorted_dates[split_idx] if split_idx < total_days else None

    # Purge: exclude train dates whose feature-lookback window reaches into the test fold.
    # Any train date within PURGE_DAYS positions of test_start is excluded.
    # Embargo: additionally exclude train dates within EMBARGO_DAYS of the test_start.
    raw_train_dates = sorted_dates[:split_idx]
    if test_start_date is not None:
        test_start_idx = split_idx  # index of first test date in sorted_dates
        purge_cutoff_idx = test_start_idx - PURGE_DAYS
        embargo_cutoff_idx = test_start_idx - EMBARGO_DAYS
        effective_cutoff_idx = min(purge_cutoff_idx, embargo_cutoff_idx)
        train_dates = set(raw_train_dates[:max(0, effective_cutoff_idx)])
    else:
        train_dates = set(raw_train_dates)

    history_train = {}
    history_test = {}
    for sym_id, sym_data in history_125d.items():
        history_train[sym_id] = {d: t for d, t in sym_data.items() if d in train_dates}
        history_test[sym_id] = {d: t for d, t in sym_data.items() if d in test_dates}

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
            p["TRIGGER_THRESHOLD_PCT"] = trial.suggest_float("TRIGGER_THRESHOLD_PCT", _SS_TRIGGER_THRESHOLD_MIN, _SS_TRIGGER_THRESHOLD_MAX)
            p["TAKE_PROFIT_MC_PCT"] = trial.suggest_float("TAKE_PROFIT_MC_PCT", _SS_TAKE_PROFIT_MC_MIN, _SS_TAKE_PROFIT_MC_MAX)
            p["VWAP_CROSS_HWM_PCT"] = trial.suggest_float("VWAP_CROSS_HWM_PCT", _SS_VWAP_CROSS_HWM_MIN, _SS_VWAP_CROSS_HWM_MAX)
            p["VWAP_BLEED_MULTIPLIER"] = trial.suggest_float("VWAP_BLEED_MULTIPLIER", _SS_VWAP_BLEED_MULT_MIN, _SS_VWAP_BLEED_MULT_MAX)
            p["VWAP_BLEED_TICKS"] = trial.suggest_int("VWAP_BLEED_TICKS", _SS_VWAP_BLEED_TICKS_MIN, _SS_VWAP_BLEED_TICKS_MAX)
            p["PARABOLIC_VELOCITY_THRESHOLD"] = trial.suggest_float("PARABOLIC_VELOCITY_THRESHOLD", _SS_PARA_VEL_MIN, _SS_PARA_VEL_MAX)
            p["MAX_PARABOLIC_SQUEEZE"] = trial.suggest_float("MAX_PARABOLIC_SQUEEZE", _SS_MAX_PARA_SQUEEZE_MIN, _SS_MAX_PARA_SQUEEZE_MAX)

            acc_sym_ids = [k for k, v in bot_state.items() if isinstance(v, dict) and database.normalize_name(v.get("name", "")) == normalized_name]
            if not acc_sym_ids: return 0.0
            target_sym_id = acc_sym_ids[0]
            daily_returns = _collect_sim_returns(p, history_train, [target_sym_id], current_date_str, deviation_dict)
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
        

        
        best_alpha_train = study.best_value
        best_params = study.best_params

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

        # Calculate daily averages for better understanding
        train_days_count = len(train_dates)
        test_days_count = len(test_dates)
        
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
        print(f"       Optimization completed in {elapsed:.2f}s. Train Sortino: {best_alpha_train:+.4f} (train days: {train_days_count})")

        database.save_symphony_strategy(normalized_name, current_params, locked_vars)

        # P1: Persist per-run validation metrics so Claude context-assembly can
        # retrieve them via get_latest_autotune_run().  Called AFTER baseline_decision
        # is finalized and save_symphony_strategy has written the chosen params,
        # so the row captures the decision that was actually applied.
        database.save_autotune_run(
            run_timestamp=run_timestamp,
            symphony_id=normalized_name,
            oos_alpha=oos_alpha,
            train_alpha=best_alpha_train,
            baseline_decision=baseline_decision,
            fallback_oos_alpha=fallback_oos_alpha,
            default_oos_alpha=default_oos_alpha,
        )

    print("  -> Autotuner finished all symphonies.")
    return optimization_results
