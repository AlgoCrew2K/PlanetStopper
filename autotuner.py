import time
import math
import optuna
from datetime import datetime, timedelta
import database
import synthetic_history
import glob
import json

optuna.logging.set_verbosity(optuna.logging.WARNING)

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

def run_autotuner(bot_state, current_date_str, account_uuids, is_forced=False):
    """
    Runs a 6-month walk-forward optimization to find the best variables using Bayesian Optimization per account.
    Implements True Walk-Forward Analysis (80% train, 20% OOS test).
    """
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

    # Use 80/20 split for ~100 days train, ~25 days out-of-sample test
    split_idx = int(total_days * 0.8)

    train_dates = set(sorted_dates[:split_idx])
    test_dates = set(sorted_dates[split_idx:])

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

    for normalized_name in symphony_names:
        print(f"     Optimizing Symphony: {normalized_name}")
        strat_data = database.get_symphony_strategy(normalized_name)
        locked_vars = strat_data.get("locked_vars", [])
        current_params = strat_data.get("params", {})
        original_params = current_params.copy()

        # Helper to run the simulation
        def run_simulation(p, history_data, acc_sym_ids, current_date_str, deviation_dict):
            total_guard_alpha = 0.0
            decay_rate = 0.015
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
                    prev_return = 0.0
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
                        velocity = ret - prev_return
                        prev_return = ret
                        para_threshold = p.get("PARABOLIC_VELOCITY_THRESHOLD", 2.0)
                        if velocity >= para_threshold:
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
                        decay_curve = math.log10(1 + 9 * time_ratio)
                        
                        # Calculate Dynamic Multiplier (Decays from 1.5x to 0.5x)
                        mult_open = 1.5
                        mult_close = 0.5
                        dynamic_multiplier = mult_open - ((mult_open - mult_close) * decay_curve)

                        # Calculate Minimum Floors (Decays from 0.3% to 0.15%)
                        min_stop_open = 0.3
                        min_stop_close = 0.15
                        dynamic_min_stop = min_stop_open - ((min_stop_open - min_stop_close) * decay_curve)

                        # Calculate active stop distance based strictly on 20-day volatility
                        safe_vol = vol if vol > 0 else 1.0
                        active_stop_dist = max((safe_vol * dynamic_multiplier), dynamic_min_stop)

                        if para_armed or breakeven_locked:
                            active_stop_dist *= p.get("MAX_PARABOLIC_SQUEEZE", 0.50)

                        base_stop = safe_hwm - active_stop_dist
                        
                        # --- RISK GUARD LOGIC ---
                        dynamic_activation = max(0.4, min(3.0, vol))
                        if ret >= (dynamic_activation - 0.2):
                            hwm_hold_ticks += 1
                        else:
                            hwm_hold_ticks = 0
                        
                        if hwm_hold_ticks >= 5:
                            breakeven_locked = True
                        
                        stop_level = max(base_stop, 0.0) if breakeven_locked else base_stop
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

                        is_vwap_broken = False
                        is_vwap_bleed_broken = False
                        if vwap_diff < 0:
                            if safe_hwm >= p.get("VWAP_CROSS_HWM_PCT", 1.0) and ret < safe_hwm:
                                vwap_ticks += 1
                                if vwap_ticks >= 3: is_vwap_broken = True
                            else: vwap_ticks = 0
                            raw_dynamic_bleed = -(vol * p.get("VWAP_BLEED_MULTIPLIER", 1.5))
                            vwap_bleed_arm_pct = max(-3.0, min(-0.5, raw_dynamic_bleed))
                            
                            if ret <= vwap_bleed_arm_pct:
                                vwap_bleed_ticks += 1
                                if vwap_bleed_ticks >= p.get("VWAP_BLEED_TICKS", 10): is_vwap_bleed_broken = True
                            else: vwap_bleed_ticks = 0
                        else:
                            vwap_ticks = 0
                            vwap_bleed_ticks = 0

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

        def objective(trial):
            p = current_params.copy()
            p["TRIGGER_THRESHOLD_PCT"] = trial.suggest_float("TRIGGER_THRESHOLD_PCT", 5.0, 25.0)
            p["TAKE_PROFIT_MC_PCT"] = trial.suggest_float("TAKE_PROFIT_MC_PCT", 2.0, 10.0)
            p["VWAP_CROSS_HWM_PCT"] = trial.suggest_float("VWAP_CROSS_HWM_PCT", 0.5, 2.5)
            p["VWAP_BLEED_MULTIPLIER"] = trial.suggest_float("VWAP_BLEED_MULTIPLIER", 0.5, 3.0)
            p["VWAP_BLEED_TICKS"] = trial.suggest_int("VWAP_BLEED_TICKS", 3, 30)
            p["PARABOLIC_VELOCITY_THRESHOLD"] = trial.suggest_float("PARABOLIC_VELOCITY_THRESHOLD", 1.0, 4.0)
            p["MAX_PARABOLIC_SQUEEZE"] = trial.suggest_float("MAX_PARABOLIC_SQUEEZE", 0.1, 0.8)

            acc_sym_ids = [k for k, v in bot_state.items() if isinstance(v, dict) and database.normalize_name(v.get("name", "")) == normalized_name]
            if not acc_sym_ids: return 0.0
            target_sym_id = acc_sym_ids[0]
            alpha = -run_simulation(p, history_train, [target_sym_id], current_date_str, deviation_dict)
            return alpha

        start_time = time.time()
        
        # Parallel Bayesian Optimization
        db_url = "sqlite:///optuna_studies.db"
        storage = optuna.storages.RDBStorage(
            url=db_url,
            engine_kwargs={"connect_args": {"timeout": 60}}
        )
        study = optuna.create_study(study_name=normalized_name, storage=storage, load_if_exists=True, direction="maximize")
        study.optimize(objective, n_trials=500, n_jobs=-1)
        

        
        best_alpha_train = study.best_value
        best_params = study.best_params

        # Evaluate OOS robustness
        best_p = current_params.copy()
        for name, val in best_params.items():
            best_p[name] = round(val, 2)

        acc_sym_ids = [k for k, v in bot_state.items() if isinstance(v, dict) and database.normalize_name(v.get("name", "")) == normalized_name]
        target_sym_id = acc_sym_ids[0] if acc_sym_ids else None
        oos_alpha = -run_simulation(best_p, history_test, [target_sym_id] if target_sym_id else [], current_date_str, deviation_dict)

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
        if oos_alpha >= fallback_oos_alpha and oos_alpha >= default_oos_alpha:
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
        print(f"       Optimization completed in {elapsed:.2f}s. Train Alpha: {best_alpha_train:+.2f}% (Average: {avg_train_alpha:.2f}%)")

        database.save_symphony_strategy(normalized_name, current_params, locked_vars)

    print("  -> Autotuner finished all symphonies.")
    return optimization_results