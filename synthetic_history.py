import os
import json
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from joblib import Parallel, delayed

import math_engine

from dotenv import load_dotenv
load_dotenv()
ALPACA_KEY = os.getenv("ALPACA_KEY")
ALPACA_SECRET = os.getenv("ALPACA_SECRET")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://data.alpaca.markets/v2")

def get_alpaca_headers():
    return {
        "APCA-API-KEY-ID": ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET
    }

def fetch_bars(tickers_list, start_str, end_str, timeframe="1Day"):
    headers = get_alpaca_headers()
    batch_size = 30
    all_data = {}
    
    for i in range(0, len(tickers_list), batch_size):
        batch = tickers_list[i : i + batch_size]
        symbol_string = ",".join(batch)
        print(f"      -> Fetching {timeframe} bars batch {i // batch_size + 1}/{len(tickers_list)//batch_size + 1}...")

        page_token = None
        while True:
            url = f"{ALPACA_BASE_URL}/stocks/bars?symbols={symbol_string}&timeframe={timeframe}&start={start_str}&end={end_str}&limit=10000&adjustment=split&feed=iex"
            if page_token:
                url += f"&page_token={page_token}"

            success = False
            for attempt in range(10): # Allow up to 10 retries for rate limits
                try:
                    response = requests.get(url, headers=headers, timeout=30)
                    if response.status_code == 200:
                        success = True
                        break
                    elif response.status_code == 429:
                        print(f"      -> Rate limit hit (429). Sleeping for 15s... (Attempt {attempt+1}/10)")
                        time.sleep(15)
                    else:
                        print(f"      -> API Error {response.status_code}: {response.text}")
                        time.sleep(5)
                except Exception as e:
                    print(f"      -> Request Exception: {e}")
                    time.sleep(5)

            if not success:
                print(f"      -> Failed to fetch batch {i // batch_size + 1} after 10 attempts. Aborting.")
                break

            data = response.json()
            if "bars" in data:
                for symbol, bars in data["bars"].items():
                    if symbol not in all_data:
                        all_data[symbol] = []
                    all_data[symbol].extend(bars)

            page_token = data.get("next_page_token")
            if not page_token:
                break
            time.sleep(0.35)
                
    return all_data

def generate_synthetic_history(bot_state, current_date_str):
    print("  -> Generating Synthetic Forward-Looking Intraday History...")
    
    # 1. Extract tickers
    all_tickers = set()
    symphony_holdings = {}
    for sym_id, state in bot_state.items():
        if isinstance(state, dict) and "current_holdings" in state:
            holdings = state["current_holdings"]
            symphony_holdings[sym_id] = holdings
            for h in holdings:
                all_tickers.add(h["ticker"])
                
    if not all_tickers:
        return {}

    # Check cache based on date and exact holdings
    import hashlib
    holdings_str = json.dumps(symphony_holdings, sort_keys=True)
    holdings_hash = hashlib.md5(holdings_str.encode('utf-8')).hexdigest()
    
    cache_dir = "cache"
    os.makedirs(cache_dir, exist_ok=True)
    cache_file = os.path.join(cache_dir, f"synthetic_history_{current_date_str}_{holdings_hash}.json")
    
    if os.path.exists(cache_file):
        print(f"  -> Loading cached synthetic history from {cache_file}...")
        try:
            with open(cache_file, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"  -> Cache load failed: {e}. Regenerating...")

    tickers_list = list(all_tickers)
    if "SPY" not in tickers_list:
        tickers_list.append("SPY")
    
    # 2. Compute date ranges
    end_date = datetime.strptime(current_date_str, "%Y-%m-%d")
    
    # Use UTC to prevent local timezone (e.g. Japan) from messing up the 'today' comparison
    from datetime import timezone
    now_utc = datetime.now(timezone.utc)
    # If the requested end_date is today (or in the future) relative to US market hours, cap it to yesterday
    # US Eastern is UTC-4 (EDT) or UTC-5 (EST). We can approximate by shifting UTC by -4 hours.
    now_us = now_utc + timedelta(hours=-4)
    today_us = now_us.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    
    if end_date >= today_us:
        end_date = today_us - timedelta(days=1)
    
    # 120 trading days is ~180 calendar days
    start_daily = end_date - timedelta(days=180)
    start_daily_str = start_daily.strftime("%Y-%m-%dT00:00:00Z")
    
    # 6 months lookback (125 trading days is ~180 calendar days)
    start_1m = end_date - timedelta(days=180)
    start_1m_str = start_1m.strftime("%Y-%m-%dT00:00:00Z")
    end_date_str_utc = end_date.strftime("%Y-%m-%dT23:59:59Z")
    
    # 3. Fetch Data
    daily_bars_raw = fetch_bars(tickers_list, start_daily_str, end_date_str_utc, "1Day")
    intraday_bars_raw = fetch_bars(list(all_tickers), start_1m_str, end_date_str_utc, "1Min")
    
    # 4. Process Daily Bars into historical_data format
    historical_daily = {}
    for sym, bars in daily_bars_raw.items():
        for i in range(1, len(bars)):
            prev_close = bars[i - 1]["c"]
            curr_close = bars[i]["c"]
            if prev_close > 0:
                date_str = bars[i]["t"][:10]
                if date_str not in historical_daily:
                    historical_daily[date_str] = {}
                historical_daily[date_str][sym] = {
                    "c": curr_close,
                    "daily_ret": (curr_close - prev_close) / prev_close,
                    "high": bars[i]["h"],
                    "low": bars[i]["l"],
                    "close": curr_close
                }
                
    daily_dates = sorted(list(historical_daily.keys()))
    
    # 5. Process Intraday Bars
    intraday_by_date = {}
    for sym, bars in intraday_bars_raw.items():
        df = pd.DataFrame(bars)
        if df.empty: continue
        df['date'] = df['t'].str[:10]
        for date_str, group in df.groupby('date'):
            if date_str not in intraday_by_date:
                intraday_by_date[date_str] = {}
            
            group = group.copy()
            group['pv'] = group['c'] * group['v']
            group['cum_pv'] = group['pv'].cumsum()
            group['cum_v'] = group['v'].cumsum()
            group['vwap'] = np.where(group['cum_v'] > 0, group['cum_pv'] / group['cum_v'], group['c'])
            
            intraday_by_date[date_str][sym] = group[['t', 'c', 'vwap']].to_dict(orient='records')

    intraday_dates = sorted(list(intraday_by_date.keys()))[-125:] # Get last 125 trading days (~6 months)
    
    history_125d = {sym_id: {} for sym_id in symphony_holdings.keys()}
    
    def process_day(date_str):
        day_history = {}
        # Get historical_data UP TO the day before
        prev_dates = [d for d in daily_dates if d < date_str]
        if not prev_dates: return date_str, {}
        
        hist_data_up_to_yesterday = {d: historical_daily[d] for d in prev_dates}
        
        yesterday_date = prev_dates[-1]
        yesterday_closes = {sym: historical_daily[yesterday_date][sym]["c"] 
                          for sym in historical_daily[yesterday_date]}
                          
        spy_today = 0.0
        if "SPY" in historical_daily.get(date_str, {}):
            spy_today = historical_daily[date_str]["SPY"]["daily_ret"] * 100.0
            
        for sym_id, holdings in symphony_holdings.items():
            ticks = []
            
            ref_sym = holdings[0]["ticker"] if holdings else None
            if not ref_sym or ref_sym not in intraday_by_date[date_str]:
                continue
                
            timestamps = [row['t'] for row in intraday_by_date[date_str][ref_sym]]
            
            vol = math_engine.calculate_20d_vol(holdings, hist_data_up_to_yesterday)
            base_atr = math_engine.calculate_14d_atr_pct(holdings, hist_data_up_to_yesterday)
            
            for i, ts in enumerate(timestamps):
                agg_ret = 0.0
                weighted_vwap_diff = 0.0
                valid_alloc = 0.0
                
                for h in holdings:
                    ticker = h["ticker"]
                    alloc = h["allocation"]
                    
                    if ticker in intraday_by_date[date_str] and i < len(intraday_by_date[date_str][ticker]):
                        bar = intraday_by_date[date_str][ticker][i]
                        c = bar['c']
                        v = bar['vwap']
                        
                        y_close = yesterday_closes.get(ticker, c)
                        if y_close > 0:
                            ret = (c - y_close) / y_close
                            agg_ret += alloc * ret
                            
                        if v > 0:
                            weighted_vwap_diff += alloc * ((c - v) / v)
                        valid_alloc += alloc
                        
                # Reduce neighbor_k and paths for speed, 300 paths is fine for tuning approximation
                mc_prob = math_engine.run_monte_carlo(holdings, hist_data_up_to_yesterday, spy_today, 300, 5)
                
                ticks.append({
                    "time": ts[11:16], 
                    "return": agg_ret * 100.0,
                    "mc_prob": mc_prob,
                    "vol": vol,
                    "vwap_diff": weighted_vwap_diff,
                    "base_atr_pct": base_atr
                })
                
            day_history[sym_id] = ticks
            
        return date_str, day_history

    print(f"  -> Simulating {len(intraday_dates)} days of Intraday Tick Data using Parallel Processing...")
    results = Parallel(n_jobs=-1)(delayed(process_day)(d) for d in intraday_dates)
    
    for date_str, day_history in results:
        for sym_id, ticks in day_history.items():
            if ticks:
                history_125d[sym_id][date_str] = ticks
                
    try:
        with open(cache_file, "w") as f:
            json.dump(history_125d, f)
    except Exception as e:
        print(f"  -> Failed to write cache file: {e}")
                
    return history_125d
