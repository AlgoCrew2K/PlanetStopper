"""Core execution logic for Alpha Bot with SQLite State Management and EOD Autotuner."""

import os
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import time
import json
import math
from datetime import datetime, timedelta, timezone, time as dt_time

import requests
import numpy as np
import pandas as pd


from dotenv import load_dotenv

# Import our SQLite DB Manager
import database
import math_engine
import reporting
import autotuner

# ==========================================
# 1. CONFIGURATION & CREDENTIALS
# ==========================================
ENV_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(ENV_FILE_PATH)

COMPOSER_KEY_ID = os.getenv("COMPOSER_KEY_ID")
COMPOSER_SECRET = os.getenv("COMPOSER_SECRET")
ACCOUNT_UUIDS = [uid.strip() for uid in os.getenv("ACCOUNT_UUIDS", "").split(",") if uid.strip()]

ALPACA_KEY = os.getenv("ALPACA_KEY")
ALPACA_SECRET = os.getenv("ALPACA_SECRET")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# --- EXECUTION MODE ---
LIVE_EXECUTION = os.getenv("LIVE_EXECUTION", "False").lower() in ("true", "1", "yes")
EXECUTION_START_TIME = os.getenv("EXECUTION_START_TIME", "09:30")

# --- STRATEGY PARAMETERS ---
TRIGGER_THRESHOLD_PCT = float(os.getenv("TRIGGER_THRESHOLD_PCT", "15.0"))
MAX_SQUEEZE_FLOOR = float(os.getenv("MAX_SQUEEZE_FLOOR", "0.20"))
TAKE_PROFIT_MC_PCT = float(os.getenv("TAKE_PROFIT_MC_PCT", "5.0"))


VWAP_CROSS_HWM_PCT = float(os.getenv("VWAP_CROSS_HWM_PCT", "1.0"))

# --- VOLATILITY REGIME PARAMETERS ---
# (Legacy VIX Macro-Awareness Removed)

# --- PARABOLIC PARAMETERS ---
PARABOLIC_VELOCITY_THRESHOLD = float(os.getenv("PARABOLIC_VELOCITY_THRESHOLD", "2.0"))
MAX_PARABOLIC_SQUEEZE = float(os.getenv("MAX_PARABOLIC_SQUEEZE", "0.50"))

SIMULATION_PATHS = int(os.getenv("SIMULATION_PATHS", "5000"))
NEIGHBOR_K = int(os.getenv("NEIGHBOR_K", "150"))

HISTORY_CACHE_FILE = "history_cache.json"

COMPOSER_BASE_URL = "https://api.composer.trade/api/v0.1"
ALPACA_BASE_URL = "https://data.alpaca.markets/v2"
YAHOO_FINANCE_BASE_URL = "https://query2.finance.yahoo.com/v8/finance/chart"

# ==========================================
# 4. API CONNECTORS
# ==========================================
def get_composer_headers(key=None, secret=None):
    return {
        "x-api-key-id": key or COMPOSER_KEY_ID,
        "authorization": f"Bearer {secret or COMPOSER_SECRET}",
        "Content-Type": "application/json",
    }

def get_alpaca_headers():
    return {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}

def fetch_symphony_stats(account_id):
    url = f"{COMPOSER_BASE_URL}/portfolio/accounts/{account_id}/symphony-stats-meta"
    try:
        response = requests.get(url, headers=get_composer_headers(), timeout=15)
        time.sleep(1.5)
        if response.status_code == 200:
            return response.json().get("symphonies", [])
        print(f"Error fetching account {account_id}: {response.text}")
    except requests.RequestException as e:
        print(f"Exception fetching account {account_id}: {e}")
    return []

def execute_sell_to_cash(actual_symphony_id, account_id):
    url = f"{COMPOSER_BASE_URL}/deploy/accounts/{account_id}/symphonies/{actual_symphony_id}/go-to-cash"
    backoff_intervals = [1, 2, 4, 10]
    
    for attempt in range(len(backoff_intervals) + 1):
        try:
            response = requests.post(url, headers=get_composer_headers(), json={}, timeout=10)
            print(f"     -> [API Status]: HTTP {response.status_code}")

            if response.status_code in [200, 201, 202]:
                time.sleep(0.5)
                return True
                
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 60))
                print(f"     !!! [RATE LIMIT HIT 429] Sleeping for {retry_after}s...")
                time.sleep(retry_after)
                continue
                
            if response.status_code >= 500 and attempt < len(backoff_intervals):
                delay = backoff_intervals[attempt]
                print(f"     !!! [COMPOSER ERROR HTTP {response.status_code}]: {response.text}")
                print(f"     -> Retrying in {delay}s...")
                time.sleep(delay)
                continue

            print(f"     !!! [COMPOSER REJECTED]: {response.text}")
            time.sleep(1.5)
            return False
        except requests.RequestException as e:
            print(f"     !!! [API CRASH]: {str(e)}")
            if attempt < len(backoff_intervals):
                delay = backoff_intervals[attempt]
                print(f"     -> Retrying in {delay}s due to transient network spike...")
                time.sleep(delay)
                continue
            return False
    return False


def fetch_alpaca_history(tickers, current_date_str):
    if "SPY" not in tickers:
        tickers.append("SPY")
    tickers_list = sorted(list(set(tickers)))

    if os.path.exists(HISTORY_CACHE_FILE):
        try:
            with open(HISTORY_CACHE_FILE, "r", encoding="utf-8") as f:
                cache = json.load(f)
            if cache.get("date") == current_date_str and cache.get("tickers") == tickers_list:
                print("  -> Loading static 3-year history from local cache.")
                return cache.get("data", {})
        except json.JSONDecodeError:
            pass

    print(f"Fetching 3-year history from Alpaca for Monte Carlo ({len(tickers)} tickers)...")
    start_date = (datetime.now() - timedelta(days=365 * 3 + 30)).strftime("%Y-%m-%dT00:00:00Z")
    headers = get_alpaca_headers()
    historical_data = {}
    batch_size = 30

    for i in range(0, len(tickers_list), batch_size):
        batch = tickers_list[i : i + batch_size]
        symbol_string = ",".join(batch)
        print(f"  -> Downloading batch {i // batch_size + 1}: {len(batch)} tickers...")

        page_token = None
        while True:
            url = f"{ALPACA_BASE_URL}/stocks/bars?symbols={symbol_string}&timeframe=1Day&start={start_date}&limit=10000&adjustment=split"
            if page_token:
                url += f"&page_token={page_token}"

            max_retries = 3
            success = False
            for attempt in range(max_retries):
                try:
                    response = requests.get(url, headers=headers, timeout=30)
                    if response.status_code == 200:
                        success = True
                        break
                    print(f"Alpaca API Error on batch (attempt {attempt+1}/{max_retries}): HTTP {response.status_code}")
                except requests.RequestException as e:
                    print(f"Alpaca API Request Exception (attempt {attempt+1}/{max_retries}): {e}")
                time.sleep(2 * (attempt + 1))

            if not success:
                print("Failed to download batch after multiple retries.")
                break

            data = response.json()
            if "bars" in data:
                for symbol, bars in data["bars"].items():
                    for j in range(1, len(bars)):
                        prev_close = bars[j - 1]["c"]
                        curr_close = bars[j]["c"]
                        if prev_close > 0:
                            daily_ret = (curr_close - prev_close) / prev_close
                            date_str = bars[j]["t"][:10]
                            if date_str not in historical_data:
                                historical_data[date_str] = {}
                            historical_data[date_str][symbol] = {
                                "c": curr_close, 
                                "daily_ret": daily_ret,
                                "high": bars[j]["h"],
                                "low": bars[j]["l"],
                                "close": curr_close
                            }

            page_token = data.get("next_page_token")
            if not page_token:
                break

    print("  -> History download complete. Saving to daily cache.")
    try:
        with open(HISTORY_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"date": current_date_str, "tickers": tickers_list, "data": historical_data}, f)
    except OSError as e:
        print(f"  -> Failed to write cache: {e}")

    return historical_data


def fetch_intraday_vwaps(tickers, headers, current_et):
    """Fetches minute bars for today to calculate true VWAP for all active holdings."""
    if not tickers:
        return {}

    start_et = current_et.replace(hour=9, minute=30, second=0, microsecond=0)
    start_utc_str = start_et.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    vwap_data = {}
    batch_size = 30
    tickers_list = list(tickers)

    for i in range(0, len(tickers_list), batch_size):
        batch = tickers_list[i : i + batch_size]
        symbol_string = ",".join(batch)
        url = f"{ALPACA_BASE_URL}/stocks/bars?symbols={symbol_string}&timeframe=1Min&start={start_utc_str}&limit=1000"

        try:
            response = requests.get(url, headers=headers, timeout=15)
            if response.status_code == 200:
                data = response.json().get("bars", {})
                for sym, bars in data.items():
                    if not bars:
                        continue
                    df = pd.DataFrame(bars)
                    df['pv'] = df['c'] * df['v']
                    cumulative_pv = df['pv'].sum()
                    cumulative_v = df['v'].sum()
                    if cumulative_v > 0:
                        vwap = cumulative_pv / cumulative_v
                        last_price = df['c'].iloc[-1]
                        vwap_data[sym] = {"vwap": vwap, "last_price": last_price}
        except Exception as e:
            print(f"Error fetching VWAP for batch {batch}: {e}")

    return vwap_data


def get_current_et():
    utc_now = datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        if 3 <= utc_now.month <= 11:
            return utc_now - timedelta(hours=4)
        return utc_now - timedelta(hours=5)


# ==========================================
# 6. MAIN EXECUTION LOOP
# ==========================================
def main():
    if not database.acquire_lock():
        print(f"[{datetime.now().strftime('%H:%M:%S')}] ⏳ Overlap Detected. Skipping...")
        return

    try:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Alpha Bot Waking Up...")
        mode_text = "LIVE EXECUTION (DANGER)" if LIVE_EXECUTION else "DRY RUN (SAFE)"
        print(f"MODE: {mode_text}")

        force_run = "--force" in sys.argv
        current_et = get_current_et()

        is_weekday = current_et.weekday() < 5
        current_time = current_et.time()

        try:
            start_h, start_m = map(int, EXECUTION_START_TIME.split(":"))
        except:
            start_h, start_m = 9, 30

        market_open = dt_time(start_h, start_m)
        market_close = dt_time(16, 0)
        rebalance_blackout = dt_time(15, 53)
        post_mortem_cutoff = dt_time(16, 5)

        if not is_weekday or current_time < market_open or current_time > post_mortem_cutoff:
            if not force_run:
                print(f"  -> Market closed or in Grace Period (ET: {current_et.strftime('%a %H:%M')}). Sleeping...")
                return
            print("  -> Market closed, but --force flag detected! Bypassing gatekeeper...")

        if rebalance_blackout <= current_time < market_close:
            bot_state = database.load_state()

            all_snapshotted_tickers = set()
            for s_id, s_data in bot_state.items():
                if isinstance(s_data, dict) and s_data.get("triggered"):
                    for h in s_data.get("triggered_basket_snapshot", []):
                        if h.get("ticker"):
                            all_snapshotted_tickers.add(h.get("ticker"))

            live_vwaps = fetch_intraday_vwaps(list(all_snapshotted_tickers), get_alpaca_headers(), current_et)

            reporting.generate_eod_snapshot(bot_state, current_et.strftime("%Y-%m-%d"), is_post_rebalance=False, discord_webhook_url=DISCORD_WEBHOOK_URL, live_prices=live_vwaps)

            if not force_run:
                print(f"  -> 🛑 COMPOSER REBALANCE BLACKOUT (ET: {current_et.strftime('%H:%M')}). Pausing...")
                return
            print("  -> Rebalance blackout active, but --force flag detected! Bypassing...")

        if not COMPOSER_KEY_ID or not ALPACA_KEY:
            print("CRITICAL: Missing API Keys. Please check your .env file.")
            return

        bot_state = database.load_state()
        chart_history = database.load_chart_history()

        current_date_str = current_et.strftime("%Y-%m-%d")
        current_time_str = current_et.strftime("%H:%M")

        # Check for execution mode toggle
        prev_live_execution = bot_state.get("last_execution_mode")
        state_changed = False
        if prev_live_execution is not None and prev_live_execution != LIVE_EXECUTION:
            print(f"  -> Execution mode toggle detected ({prev_live_execution} -> {LIVE_EXECUTION}). Wiping transient state.")
            database.wipe_transient_state(bot_state)
            state_changed = True
        
        if bot_state.get("last_execution_mode") != LIVE_EXECUTION:
            bot_state["last_execution_mode"] = LIVE_EXECUTION
            state_changed = True

        if bot_state.get("date") != current_date_str:
            print(f"  -> New trading day detected ({current_date_str} ET). Wiping transient state keys and chart memory.")
            bot_state["date"] = current_date_str
            database.wipe_transient_state(bot_state)
            database.clear_symphony_logs()
            state_changed = True

        if state_changed:
            database.save_state(bot_state)

        if chart_history.get("date") != current_date_str:
            chart_history = {"date": current_date_str, "symphonies": {}}
            database.save_chart_history(chart_history)

        m_open_dt = current_et.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
        m_close_dt = current_et.replace(hour=16, minute=0, second=0, microsecond=0)

        all_tickers = set()
        symphony_data_cache = {}

        for account in ACCOUNT_UUIDS:
            symphonies = fetch_symphony_stats(account)
            symphony_data_cache[account] = symphonies
            for sym in symphonies:
                for holding in sym.get("holdings", []):
                    raw_ticker = holding.get("ticker", "")
                    clean_ticker = raw_ticker.split("::")[-1].split("//")[0]
                    alpaca_ticker = clean_ticker.replace("/", ".")
                    if alpaca_ticker:
                        all_tickers.add(alpaca_ticker)
                        holding["working_ticker"] = alpaca_ticker

        # Add frozen tickers to all_tickers for true Shadow Return tracking
        for s_id, s_data in bot_state.items():
            if isinstance(s_data, dict) and s_data.get("triggered"):
                for h in s_data.get("current_holdings", []):
                    t = h.get("ticker")
                    if t and "cash" not in t.lower():
                        all_tickers.add(t)

        if (market_close <= current_time <= post_mortem_cutoff) or (force_run and current_et.weekday() >= 5):
            if bot_state.get("post_mortem_run") == current_date_str:
                if not force_run:
                    print("  -> EOD Post-Mortem already run for today. Sleeping...")
                    return
                print("  -> EOD Post-Mortem already run, but --force flag detected! Running again...")

            for account, symphonies in symphony_data_cache.items():
                for sym in symphonies:
                    s_id = sym["id"]
                    if s_id in bot_state:
                        bot_state[s_id]["current_holdings"] = [
                            {"ticker": h.get("working_ticker", h.get("ticker")), "allocation": h.get("allocation", 0.0)}
                            for h in sym.get("holdings", [])
                        ]
                        bot_state[s_id]["current_return"] = sym.get("last_percent_change", 0.0) * 100
            
            # Save post_mortem flag immediately to prevent race conditions if execution is slow
            bot_state["post_mortem_run"] = current_date_str
            database.save_state(bot_state)

            # NEW: Execute Phase 2 Reporting
            reporting.generate_eod_snapshot(bot_state, current_date_str, is_post_rebalance=True, discord_webhook_url=DISCORD_WEBHOOK_URL)

            # NEW: Execute Autotuner (Weekly on Fridays, or Manual Weekends/Force)
            autotuner_changes = None
            if current_et.weekday() >= 4 or force_run: # 4=Fri, 5=Sat, 6=Sun
                print(f"  -> {'Weekend/Force' if current_et.weekday() >= 5 else 'Friday'} Detected. Starting autotune...")
                autotuner_changes = autotuner.run_autotuner(bot_state, current_date_str, ACCOUNT_UUIDS, is_forced=force_run)
            else:
                print(f"  -> Day is {current_et.strftime('%A')}. Skipping weekly autotune.")
            
            reporting.send_eod_discord_post(current_date_str, f"post_mortem_{current_date_str}.json", autotuner_changes, DISCORD_WEBHOOK_URL)

            print("  -> EOD Post-Mortem complete. Ending execution for the day.")
            return

        historical_data = fetch_alpaca_history(list(all_tickers), current_date_str)
        if not historical_data:
            return

        live_vwaps = fetch_intraday_vwaps(list(all_tickers), get_alpaca_headers(), current_et)

        spy_today = 0.0
        if "SPY" in historical_data.get(current_date_str, {}):
            spy_today = historical_data[current_date_str]["SPY"].get("daily_ret", 0.0) * 100.0
        print(f"  -> Macro Environment: SPY {spy_today:.2f}%")
        print("\nEvaluating Symphonies...")

        execution_queue = []

        for account, symphonies in symphony_data_cache.items():
            for sym in symphonies:
                symphony_id = sym["id"]
                actual_symphony_id = sym.get("symphony_id", symphony_id)

                symphony_name = sym.get("name", "Unknown Symphony")
                normalized_name = database.normalize_name(symphony_name)
                symphony_strat = database.get_symphony_strategy(normalized_name)
                acc_params = symphony_strat.get("params", {})

                acc_TRIGGER_THRESHOLD_PCT = acc_params.get("TRIGGER_THRESHOLD_PCT", TRIGGER_THRESHOLD_PCT)
                acc_MAX_SQUEEZE_FLOOR = acc_params.get("MAX_SQUEEZE_FLOOR", MAX_SQUEEZE_FLOOR)
                acc_TAKE_PROFIT_MC_PCT = acc_params.get("TAKE_PROFIT_MC_PCT", TAKE_PROFIT_MC_PCT)

                acc_VWAP_CROSS_HWM_PCT = acc_params.get("VWAP_CROSS_HWM_PCT", VWAP_CROSS_HWM_PCT)
                acc_VWAP_BLEED_MULTIPLIER = acc_params.get("VWAP_BLEED_MULTIPLIER", 1.5)
                acc_VWAP_BLEED_TICKS = acc_params.get("VWAP_BLEED_TICKS", 10)



                holdings = sym.get("holdings", [])
                current_return = sym.get("last_percent_change", 0.0) * 100

                # --- TRUE SHADOW RETURN OVERRIDE ---
                if symphony_id in bot_state and bot_state[symphony_id].get("triggered"):
                    holdings = bot_state[symphony_id].get("current_holdings", [])
                    f_ret = bot_state[symphony_id].get("triggered_at_return", 0.0)
                    trigger_prices = bot_state[symphony_id].get("trigger_prices", {})
                    post_trigger_move = 0.0
                    for h in holdings:
                        t = h.get("ticker")
                        alloc = h.get("allocation", 0.0)
                        if t in trigger_prices and t in live_vwaps:
                            p_start = trigger_prices[t]
                            p_now = live_vwaps[t]["last_price"]
                            if p_start > 0:
                                post_trigger_move += alloc * ((p_now - p_start) / p_start)
                    current_return = f_ret + (post_trigger_move * 100.0)
                # -----------------------------------

                # Pre-calculate True VWAP difference unconditionally so it can be logged in the chart history
                weighted_vwap_diff = 0.0
                valid_vwap_weight = 0.0
                for h in holdings:
                    h["ticker"] = h.get("working_ticker", h.get("ticker"))
                    t = h["ticker"]
                    alloc = h.get("allocation", 0.0)
                    if t in live_vwaps:
                        p = live_vwaps[t]["last_price"]
                        v = live_vwaps[t]["vwap"]
                        if v > 0:
                            weighted_vwap_diff += alloc * ((p - v) / v)
                            valid_vwap_weight += alloc
                symphony_holdings = [h.get("ticker") for h in holdings]

                if symphony_id not in bot_state:
                    bot_state[symphony_id] = {
                        "high_water_mark": current_return,
                        "shadow_hwm": current_return,
                        "prev_return": current_return,
                        "armed": False,
                        "tp_armed": False,
                        "para_armed": False,
                        "triggered": False,
                        "mc_history": [],
                        "below_stop_count": 0,
                        "above_tp_count": 0,
                        "vwap_ticks": 0,
                        "vwap_bleed_ticks": 0,
                        "breakeven_locked": False,
                        "hwm_hold_ticks": 0,
                    }

                prev_armed = bot_state[symphony_id].get("armed", False)
                prev_tp_armed = bot_state[symphony_id].get("tp_armed", False)
                prev_triggered = bot_state[symphony_id].get("triggered", False)
                prev_para_armed = bot_state[symphony_id].get("para_armed", False)

                for key in ["triggered", "tp_armed", "breakeven_locked", "para_armed"]:
                    if key not in bot_state[symphony_id]:
                        bot_state[symphony_id][key] = False
                for key in ["below_stop_count", "above_tp_count", "vwap_ticks", "vwap_bleed_ticks", "hwm_hold_ticks"]:
                    if key not in bot_state[symphony_id]:
                        bot_state[symphony_id][key] = 0
                if "mc_history" not in bot_state[symphony_id]:
                    bot_state[symphony_id]["mc_history"] = []

                if current_return > bot_state[symphony_id]["high_water_mark"] and not bot_state[symphony_id]["triggered"]:
                    bot_state[symphony_id]["high_water_mark"] = current_return

                if "shadow_hwm" not in bot_state[symphony_id]:
                    bot_state[symphony_id]["shadow_hwm"] = current_return
                if current_return > bot_state[symphony_id]["shadow_hwm"]:
                    bot_state[symphony_id]["shadow_hwm"] = current_return

                high_water_mark = bot_state[symphony_id]["high_water_mark"]
                safe_hwm = high_water_mark if high_water_mark != -999.0 else current_return

                prob_beating = math_engine.run_monte_carlo(holdings, historical_data, spy_today, SIMULATION_PATHS, NEIGHBOR_K)
                symphony_vol = math_engine.calculate_20d_vol(holdings, historical_data)

                raw_dynamic_bleed = -(symphony_vol * acc_VWAP_BLEED_MULTIPLIER)
                acc_VWAP_BLEED_ARM_PCT = max(-3.0, min(-0.5, raw_dynamic_bleed))

                should_arm = False
                arm_reason = ""

                if acc_TAKE_PROFIT_MC_PCT <= prob_beating < acc_TRIGGER_THRESHOLD_PCT:
                    should_arm = True
                    arm_reason = f"MC Prob {prob_beating:.1f}%"

                if should_arm and not bot_state[symphony_id]["armed"] and not bot_state[symphony_id]["triggered"]:
                    bot_state[symphony_id]["armed"] = True
                    print(f"  *** {symphony_name} ARMED ({arm_reason}) ***")
                    database.log_symphony_event(symphony_id, f"{symphony_name} ARMED ({arm_reason})", "armed")

                elif bot_state[symphony_id]["armed"] and not bot_state[symphony_id]["triggered"]:
                    if prob_beating > (acc_TRIGGER_THRESHOLD_PCT * 2) and current_return > 0.0:
                        bot_state[symphony_id]["armed"] = False
                        bot_state[symphony_id]["below_stop_count"] = 0
                        print(f"  *** {symphony_name} DISARMED (Conditions Recovered) ***")

                bot_state[symphony_id]["mc_history"].append(prob_beating)
                if len(bot_state[symphony_id]["mc_history"]) > 5:
                    bot_state[symphony_id]["mc_history"].pop(0)

                # --- PARABOLIC SQUEEZE LOGIC ---
                prev_return = bot_state[symphony_id].get("prev_return", current_return)
                velocity = current_return - prev_return
                bot_state[symphony_id]["prev_return"] = current_return

                para_threshold = acc_params.get("PARABOLIC_VELOCITY_THRESHOLD", PARABOLIC_VELOCITY_THRESHOLD)
                if velocity >= para_threshold:
                    if not bot_state[symphony_id]["para_armed"]:
                        bot_state[symphony_id]["para_armed"] = True
                        print(f"  🚀 {symphony_name} PARA-ARMED (Velocity: {velocity:.2f}%) 🚀")
                        database.log_symphony_event(symphony_id, f"{symphony_name} PARA-ARMED (Velocity: {velocity:.2f}%)", "para-armed")

                # --- TIME SQUEEZE DECAY LOGIC ---
                m_open_dt = current_et.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
                m_close_dt = current_et.replace(hour=16, minute=0, second=0, microsecond=0)
                time_ratio = max(0.0, min(1.0, (current_et - m_open_dt).total_seconds() / (m_close_dt - m_open_dt).total_seconds()))
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

                safe_vol = symphony_vol if symphony_vol > 0 else 1.0
                active_trailing_stop = max((safe_vol * dynamic_multiplier), dynamic_min_stop)

                # Apply Parabolic Squeeze multiplier if armed
                if bot_state[symphony_id].get("para_armed") or bot_state[symphony_id].get("breakeven_locked"):
                    active_trailing_stop *= acc_params.get("MAX_PARABOLIC_SQUEEZE", MAX_PARABOLIC_SQUEEZE)

                base_stop_level = safe_hwm - active_trailing_stop

                dynamic_activation = max(0.4, min(3.0, symphony_vol))
                if current_return >= (dynamic_activation - 0.2):
                    bot_state[symphony_id]["hwm_hold_ticks"] += 1
                else:
                    bot_state[symphony_id]["hwm_hold_ticks"] = 0
                
                if bot_state[symphony_id]["hwm_hold_ticks"] >= 5:
                    bot_state[symphony_id]["breakeven_locked"] = True

                if bot_state[symphony_id]["breakeven_locked"]:
                    stop_trigger_level = max(base_stop_level, 0.0)
                else:
                    stop_trigger_level = base_stop_level

                if bot_state[symphony_id]["triggered"]:
                    stop_trigger_level = -999.0

                # Check 1: Trailing Stop
                is_trailing_stop_hit = False
                if bot_state[symphony_id]["armed"] and not bot_state[symphony_id]["triggered"]:
                    # Magnitude Floor (Return <= Stop - 0.10) AND MC Sanity Gate (Prob < 60.0)
                    if current_return <= (stop_trigger_level - 0.10) and prob_beating < 60.0:
                        bot_state[symphony_id]["below_stop_count"] += 1
                        # Hardcoded exit threshold: 3 consecutive ticks
                        if bot_state[symphony_id]["below_stop_count"] == 1:
                            print(f"  ⚠️ {symphony_name[:35]} dipped below stop. Awaiting 3-tick confirmation...")
                        elif bot_state[symphony_id]["below_stop_count"] >= 3:
                            is_trailing_stop_hit = True
                    else:
                        if bot_state[symphony_id]["below_stop_count"] > 0:
                            print(f"  ✅ {symphony_name[:35]} recovered or sanity check passed. Confirmation reset.")
                        bot_state[symphony_id]["below_stop_count"] = 0

                # Check 2: Take Profit
                tp_triggered_now = False
                if prob_beating < acc_TAKE_PROFIT_MC_PCT:
                    if not bot_state[symphony_id]["tp_armed"] and not bot_state[symphony_id]["triggered"]:
                        bot_state[symphony_id]["tp_armed"] = True
                        bot_state[symphony_id]["above_tp_count"] = 0
                        print(f"  *** {symphony_name} TP-ARMED (Exceptional Gain: MC Prob {prob_beating:.1f}% < {acc_TAKE_PROFIT_MC_PCT}%) ***")
                        database.log_symphony_event(symphony_id, f"{symphony_name} TP-ARMED (Exceptional Gain: MC Prob {prob_beating:.1f}% < {acc_TAKE_PROFIT_MC_PCT}%)", "tp-armed")
                elif bot_state[symphony_id]["tp_armed"] and not bot_state[symphony_id]["triggered"]:
                    if prob_beating >= acc_TAKE_PROFIT_MC_PCT:
                        bot_state[symphony_id]["above_tp_count"] += 1
                        if bot_state[symphony_id]["above_tp_count"] == 1:
                            print(f"  ⚠️ {symphony_name[:35]} TP signal flashed. Awaiting 2nd tick confirmation...")
                        elif bot_state[symphony_id]["above_tp_count"] >= 2:
                            if current_return > 0:
                                tp_triggered_now = True
                            else:
                                bot_state[symphony_id]["tp_armed"] = False
                                bot_state[symphony_id]["above_tp_count"] = 0
                                print(f"  *** {symphony_name} TP-DISARMED (MC Rose but Return <= 0) ***")
                    else:
                        if bot_state[symphony_id]["above_tp_count"] > 0:
                            print(f"  📉 {symphony_name[:35]} TP signal vanished. Still cranking.")
                        bot_state[symphony_id]["above_tp_count"] = 0

                # Check 3: True VWAP Breakdown
                is_vwap_broken = False
                is_vwap_bleed_broken = False
                
                # ADDED GATE: Only evaluate if the symphony hasn't already exited
                if not bot_state[symphony_id]['triggered']:
                    if valid_vwap_weight > 0.5 and weighted_vwap_diff < 0:
                        # System A (Profit):
                        if safe_hwm >= acc_VWAP_CROSS_HWM_PCT and current_return < safe_hwm:
                            bot_state[symphony_id]['vwap_ticks'] += 1
                            if bot_state[symphony_id]['vwap_ticks'] >= 3:
                                is_vwap_broken = True
                                print(f'  📉 {symphony_name[:35]} Portfolio VWAP broken. Forcing exit to protect gains.')
                        else:
                            bot_state[symphony_id]['vwap_ticks'] = 0
                            
                        # System B (Bleed):
                        if current_return <= acc_VWAP_BLEED_ARM_PCT:
                            bot_state[symphony_id]['vwap_bleed_ticks'] += 1
                            if bot_state[symphony_id]['vwap_bleed_ticks'] >= acc_VWAP_BLEED_TICKS:
                                is_vwap_bleed_broken = True
                                print(f'  🩸 {symphony_name[:35]} VWAP Bleed Limit Reached. Forcing exit.')
                        else:
                            bot_state[symphony_id]['vwap_bleed_ticks'] = 0
                    else:
                        bot_state[symphony_id]['vwap_ticks'] = 0
                        bot_state[symphony_id]['vwap_bleed_ticks'] = 0

                safe_name = symphony_name[:35].encode('ascii', 'ignore').decode('ascii')
                print(f"  -> {safe_name}: Ret: {current_return:.2f}% | HWM: {high_water_mark:.2f}% | Stop Dist: {active_trailing_stop:.2f}% | ArmProb: {prob_beating:.1f}%")

                bot_state[symphony_id]["name"] = symphony_name
                bot_state[symphony_id]["account"] = account
                bot_state[symphony_id]["current_return"] = current_return
                bot_state[symphony_id]["mc_prob"] = prob_beating
                bot_state[symphony_id]["stop_trigger"] = stop_trigger_level
                bot_state[symphony_id]["active_stop_distance"] = active_trailing_stop
                bot_state[symphony_id]["symphony_vol"] = symphony_vol
                bot_state[symphony_id]["current_value"] = sym.get("current_value", sym.get("value", 0.0))
                if not bot_state[symphony_id].get("triggered"):
                    bot_state[symphony_id]["current_holdings"] = [{"ticker": h.get("ticker"), "allocation": h.get("allocation", 0.0)} for h in holdings]

                chart_event = None
                if is_vwap_broken or is_vwap_bleed_broken:
                    chart_event = "VWAP_Break"
                elif is_trailing_stop_hit or tp_triggered_now:
                    chart_event = "Triggered"
                elif bot_state[symphony_id].get("para_armed") and not prev_para_armed:
                    chart_event = "Para-Armed"
                elif bot_state[symphony_id]["armed"] and not prev_armed:
                    chart_event = "Armed"
                elif bot_state[symphony_id]["tp_armed"] and not prev_tp_armed:
                    chart_event = "TP-Armed"

                tracked_stop = stop_trigger_level if (bot_state[symphony_id]["armed"] or bot_state[symphony_id]["tp_armed"] or bot_state[symphony_id]["triggered"] or prev_triggered) else None
                if prev_triggered:
                    tracked_stop = bot_state[symphony_id].get("triggered_at_stop", -999.0)
                    if tracked_stop == -999.0:
                        tracked_stop = None

                sym_chart_data = chart_history["symphonies"].setdefault(symphony_id, [])
                sym_chart_data.append({
                    "time": current_time_str,
                    "return": current_return,
                    "stop": tracked_stop,
                    "event": chart_event,
                    "mc_prob": prob_beating,
                    "vol": symphony_vol,
                    "vwap_diff": weighted_vwap_diff,
                    "base_atr_pct": 0.0,
                    "dynamic_multiplier": 1.0
                })

                if is_trailing_stop_hit or tp_triggered_now or is_vwap_broken or is_vwap_bleed_broken:
                    if tp_triggered_now:
                        reason = "Take-Profit"
                        attempted_level = current_return
                    elif is_vwap_bleed_broken:
                        reason = "VWAP Bleed Cut"
                        attempted_level = acc_VWAP_BLEED_ARM_PCT
                    elif is_vwap_broken:
                        reason = "VWAP Breakdown"
                        attempted_level = safe_hwm
                    else:
                        reason = "Trailing Stop"
                        attempted_level = stop_trigger_level

                    print(f"  🚨 {reason.upper()} HIT FOR {symphony_name} 🚨 - Queuing for Execution")
                    database.log_symphony_event(symphony_id, f"{reason.upper()} HIT FOR {symphony_name}. Level: {attempted_level:.2f}", "triggered")

                    execution_queue.append({
                        "symphony_id": symphony_id,
                        "actual_symphony_id": actual_symphony_id,
                        "account": account,
                        "symphony_name": symphony_name,
                        "reason": reason,
                        "attempted_level": attempted_level,
                        "current_return": current_return,
                        "safe_hwm": safe_hwm,
                        "stop_trigger_level": stop_trigger_level,
                        "prob_beating": prob_beating,
                        "weighted_vwap_diff": weighted_vwap_diff,
                        "acc_VWAP_BLEED_ARM_PCT": acc_VWAP_BLEED_ARM_PCT,
                        "acc_VWAP_BLEED_MULTIPLIER": acc_VWAP_BLEED_MULTIPLIER,
                        "symphony_vol": symphony_vol,
                        "acc_VWAP_BLEED_TICKS": acc_VWAP_BLEED_TICKS,
                        "vwap_ticks": bot_state[symphony_id]["vwap_ticks"],
                        "acc_TAKE_PROFIT_MC_PCT": acc_TAKE_PROFIT_MC_PCT
                    })

        # Process Execution Queue
        if execution_queue:
            print(f"\nProcessing Execution Queue ({len(execution_queue)} items)...")
            
            for i in range(0, len(execution_queue), 25):
                chunk = execution_queue[i:i+25]
                
                if i > 0:
                    print("  -> ⏳ Rate limit chunking: Sleeping for 60 seconds before next batch...")
                    time.sleep(60)

                for item in chunk:
                    sym_id = item["symphony_id"]
                    actual_id = item["actual_symphony_id"]
                    account = item["account"]
                    reason = item["reason"]
                    
                    sym_chart_data = chart_history["symphonies"].get(sym_id, [])

                    if LIVE_EXECUTION:
                        print(f"  -> [LIVE EXECUTION] Sending sell-to-cash command for {item['symphony_name']}...")
                        success = execute_sell_to_cash(actual_id, account)
                    else:
                        print(f"  -> [DRY RUN] Execution bypassed for {item['symphony_name']}.")
                        success = True
                    
                    if success:
                        bot_state[sym_id]["armed"] = False
                        bot_state[sym_id]["tp_armed"] = False
                        bot_state[sym_id]["triggered"] = True
                        bot_state[sym_id]["triggered_reason"] = reason
                        bot_state[sym_id]["triggered_at_return"] = item["current_return"]
                        bot_state[sym_id]["triggered_at_hwm"] = item["safe_hwm"]
                        bot_state[sym_id]["triggered_at_stop"] = item["attempted_level"]
                        bot_state[sym_id]["triggered_at_time"] = current_time_str
                        bot_state[sym_id]["high_water_mark"] = -999.0

                        # Freeze ticker prices and basket snapshot
                        trigger_prices = {}
                        triggered_basket_snapshot = []
                        for h in bot_state[sym_id].get("current_holdings", []):
                            t = h.get("ticker")
                            alloc = h.get("allocation", 0.0)
                            price = 0.0
                            if t in live_vwaps:
                                price = live_vwaps[t]["last_price"]
                                trigger_prices[t] = price
                            triggered_basket_snapshot.append({
                                "ticker": t,
                                "allocation": alloc,
                                "price": price
                            })
                        bot_state[sym_id]["trigger_prices"] = trigger_prices
                        bot_state[sym_id]["triggered_basket_snapshot"] = triggered_basket_snapshot

                        if sym_chart_data:
                            sym_chart_data[-1]["stop"] = item["attempted_level"]
                            sym_chart_data[-1]["event"] = reason

                        reporting.send_discord_alert(
                            item["symphony_name"], item["current_return"], item["prob_beating"], 
                            item["stop_trigger_level"], item["safe_hwm"], LIVE_EXECUTION, DISCORD_WEBHOOK_URL, 
                            exit_reason=reason, vwap_bleed_arm_pct=item["acc_VWAP_BLEED_ARM_PCT"], 
                            vwap_bleed_ticks=item["acc_VWAP_BLEED_TICKS"], vwap_diff=item["weighted_vwap_diff"], 
                            vwap_breakdown_ticks=item["vwap_ticks"], tp_threshold=item["acc_TAKE_PROFIT_MC_PCT"],
                            vwap_bleed_multiplier=item.get("acc_VWAP_BLEED_MULTIPLIER"),
                            symphony_vol=item.get("symphony_vol")
                        )
                    else:
                        print(f"     !!! EXECUTION FAILED FOR {item['symphony_name']}. Skipping state update !!!")

        database.save_state(bot_state)
        database.save_chart_history(chart_history)

    finally:
        database.release_lock()

if __name__ == "__main__":
    main()
