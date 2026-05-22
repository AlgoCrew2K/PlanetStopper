import logging
import os
import json
import time
import datetime as _dt
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from json import JSONDecodeError
from zoneinfo import ZoneInfo
from joblib import Parallel, delayed

import math_engine

from dotenv import load_dotenv
load_dotenv()
ALPACA_KEY = os.getenv("ALPACA_KEY")
ALPACA_SECRET = os.getenv("ALPACA_SECRET")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://data.alpaca.markets/v2")

# US Eastern is the canonical exchange zone for NYSE/NASDAQ. ZoneInfo handles
# the EST<->EDT transition automatically, so the UTC->ET conversion is correct
# year-round (a fixed UTC-4 offset is wrong for the ~5 months ET is on EST).
_US_EASTERN = ZoneInfo("America/New_York")

# --- Fetch-window sizing (trading-day counted, not calendar-day literal) ----
# The autotuner slices the last 125 trading days of synthetic history for its
# walk-forward replay. Pinned to the autotuner replay-window length.
_WALK_FORWARD_TRADING_DAYS = 125
# The Monte-Carlo gate on the FIRST replay day still needs MC_MIN_HISTORY_DAYS
# eligible days of prior history, and each eligible day needs
# MC_VOL_WINDOW_DAYS - 1 raw days of vol-window warmup behind it
# (math_engine.run_monte_carlo eligible-pool guard). That warmup PRECEDES the
# replay window, so it is additive to the 125-day slice.
_MC_WARMUP_TRADING_DAYS = (
    math_engine.MC_MIN_HISTORY_DAYS + (math_engine.MC_VOL_WINDOW_DAYS - 1)
)
# Holiday / margin buffer: a safety cushion of extra trading days so the
# fetched window comfortably clears the floor even if the upstream bar feed
# drops a trailing day or a half-day. 10 trading days ~ two market weeks.
_FETCH_WINDOW_BUFFER_TRADING_DAYS = 10
# Total trading days the fetch window must span, counting back from the end
# date. busday_offset accounts for weekends + holidays exactly, so this count
# is the genuine number of trading days delivered.
_REQUIRED_FETCH_TRADING_DAYS = (
    _WALK_FORWARD_TRADING_DAYS
    + _MC_WARMUP_TRADING_DAYS
    + _FETCH_WINDOW_BUFFER_TRADING_DAYS
)

# US equity market holidays — the NYSE published calendar. Used by
# numpy.busday_offset to count trading days exactly (pandas_market_calendars
# is not a dependency). Spans late-2024 through 2026 so any fetch window the
# producer requests is fully covered; extend as the calendar advances.
_US_MARKET_HOLIDAYS = np.array(
    [
        # 2024
        "2024-01-01",  # New Year's Day
        "2024-01-15",  # MLK Jr. Day
        "2024-02-19",  # Washington's Birthday
        "2024-03-29",  # Good Friday
        "2024-05-27",  # Memorial Day
        "2024-06-19",  # Juneteenth
        "2024-07-04",  # Independence Day
        "2024-09-02",  # Labor Day
        "2024-11-28",  # Thanksgiving
        "2024-12-25",  # Christmas
        # 2025
        "2025-01-01",  # New Year's Day
        "2025-01-20",  # MLK Jr. Day
        "2025-02-17",  # Washington's Birthday
        "2025-04-18",  # Good Friday
        "2025-05-26",  # Memorial Day
        "2025-06-19",  # Juneteenth
        "2025-07-04",  # Independence Day
        "2025-09-01",  # Labor Day
        "2025-11-27",  # Thanksgiving
        "2025-12-25",  # Christmas
        # 2026
        "2026-01-01",  # New Year's Day
        "2026-01-19",  # MLK Jr. Day
        "2026-02-16",  # Washington's Birthday
        "2026-04-03",  # Good Friday
        "2026-05-25",  # Memorial Day
        "2026-06-19",  # Juneteenth
        "2026-07-03",  # Independence Day (observed)
        "2026-09-07",  # Labor Day
        "2026-11-26",  # Thanksgiving
        "2026-12-25",  # Christmas
    ],
    dtype="datetime64[D]",
)


def utc_to_eastern(utc_dt):
    """Convert a UTC datetime to the corresponding US Eastern datetime.

    DST-aware: ZoneInfo("America/New_York") applies EST (UTC-5) or EDT (UTC-4)
    according to the instant, so the resolved wall clock is correct year-round.
    A naive input is assumed to be UTC. Returns a tz-aware ET datetime.
    """
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    return utc_dt.astimezone(_US_EASTERN)


def compute_fetch_window_start(end_date):
    """Return the fetch-window start date for a given end date.

    Counts back _REQUIRED_FETCH_TRADING_DAYS trading days from end_date using
    numpy.busday_offset against the US market holiday calendar, so the
    [start, end] span yields at least that many trading days regardless of how
    many weekends or holidays it straddles. Replaces the old fixed
    timedelta(days=180) literal, which under-delivered trading days once
    holidays were counted (audit finding 8).

    Accepts a date or datetime; returns a datetime.date.
    """
    if isinstance(end_date, _dt.datetime):
        end_date = end_date.date()
    end64 = np.datetime64(end_date, "D")
    # roll="backward": if end_date is itself a holiday/weekend, start counting
    # from the prior trading day. Offsetting by -(N-1) lands on the trading day
    # that is N trading days back inclusive of the end date.
    start64 = np.busday_offset(
        end64,
        -(_REQUIRED_FETCH_TRADING_DAYS - 1),
        roll="backward",
        holidays=_US_MARKET_HOLIDAYS,
    )
    return start64.astype(_dt.date)


def load_cached_history(cache_file):
    """Load a parsed synthetic-history cache file, or None on a corrupt/missing one.

    A corrupt or unreadable cache is a recoverable miss — the caller regenerates
    the history — so this never raises. On failure it logs a WARNING naming the
    cache file and the exception detail, then returns None.
    """
    try:
        with open(cache_file, "r") as f:
            return json.load(f)
    except (OSError, JSONDecodeError, ValueError, UnicodeDecodeError) as e:
        logging.warning(
            "synthetic_history: corrupt or unreadable cache file %s: %s",
            cache_file,
            e,
        )
        return None

# Insufficient-MC handling: run_monte_carlo returns the out-of-band sentinel
# None (math_engine.MC_INSUFFICIENT_HISTORY_SENTINEL) when MC history is too
# short. The replay carries that None straight through into the tick's
# mc_prob — production's Cluster 2 fail-safe contract treats None as "MC
# opinion absent": no arm, no disarm, no TP transition, no MC veto of the
# trailing stop. The autotuner replay's exit core (autotuner._replay_exit_tick)
# gates every MC-driven branch on mc_available, so a None tick is handled
# exactly as production handles it. No fabricated in-band substitute value.

# Monte-Carlo simulation path count for the replay. Paths only add unbiased
# variance to the bootstrap estimate, so a lower count than production's
# default (5000) is an acceptable speed/precision tradeoff for tuning — only
# neighbor_k must match production (it shapes the CDF resolution). 300 paths
# is sufficient for the tuning approximation.
_MC_REPLAY_SIMULATION_PATHS = 300

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
                        # Log status code only; omit response.text to avoid leaking payload in logs (cycle-#27 hardening)
                        logging.warning("fetch_bars: HTTP %s for batch %d (attempt %d/10)", response.status_code, i // batch_size + 1, attempt + 1)
                        time.sleep(5)
                except requests.RequestException as e:
                    # Narrow to transport/connection errors raised by requests; re-raise unexpected errors upstream
                    logging.error("fetch_bars: request failed on attempt %d/10: %s", attempt + 1, e)
                    time.sleep(5)

            if not success:
                logging.error("fetch_bars: batch %d failed after 10 attempts; aborting symbol fetch", i // batch_size + 1)
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

def build_replay_day(
    sym_id,
    date_str,
    holdings,
    intraday_by_date,
    timestamps,
    hist_data_up_to_yesterday,
    yesterday_closes,
    spy_today,
):
    """Build one symphony's replay tick list for a single day — API-free.

    Module-level, independently-callable per-day tick builder. Consumes
    pre-fetched bar data (no live Alpaca fetch) so the replay's Monte-Carlo
    parity — neighbor_k and insufficient-MC handling — is testable from
    fixtures. generate_synthetic_history's process_day delegates to this so the
    tick-building logic has one home.

    Returns a list of tick dicts with the replay tick schema:
    {time, return, mc_prob, vol, vwap_diff, base_atr_pct, valid_vwap_weight}.
    mc_prob is the run_monte_carlo result verbatim — including the None
    sentinel for an insufficient-MC tick (production's fail-safe contract).
    """
    ticks = []

    ref_sym = holdings[0]["ticker"] if holdings else None
    if not ref_sym or ref_sym not in intraday_by_date.get(date_str, {}):
        return ticks

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

        # neighbor_k must equal production's MC_DEFAULT_NEIGHBOR_K so the
        # replay's mc_prob matches the live engine's estimand — with a smaller
        # k the kNN bootstrap CDF is a coarse step function. Seed is
        # per-(symphony, day) so cache rebuilds produce bit-identical series.
        mc_prob = math_engine.run_monte_carlo(
            holdings,
            hist_data_up_to_yesterday,
            spy_today,
            _MC_REPLAY_SIMULATION_PATHS,
            math_engine.MC_DEFAULT_NEIGHBOR_K,
            seed=math_engine.derive_cycle_mc_seed(f"{sym_id}_{date_str}"),
        )
        # run_monte_carlo returns the insufficient sentinel (None) when MC
        # history is too short. Carry it straight through — production treats
        # None as "MC opinion absent" and the autotuner replay's exit core
        # gates every MC-driven branch on mc_available. No fabricated value.

        ticks.append({
            "time": ts[11:16],
            "return": agg_ret * 100.0,
            "mc_prob": mc_prob,
            "vol": vol,
            "vwap_diff": weighted_vwap_diff,
            "base_atr_pct": base_atr,
            "valid_vwap_weight": valid_alloc,
        })

    return ticks


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
    cache_file = os.path.join(cache_dir, f"synthetic_history_v2_{current_date_str}_{holdings_hash}.json")
    
    if os.path.exists(cache_file):
        print(f"  -> Loading cached synthetic history from {cache_file}...")
        cached = load_cached_history(cache_file)
        if cached is not None:
            return cached
        print("  -> Cache load failed. Regenerating...")

    tickers_list = list(all_tickers)
    if "SPY" not in tickers_list:
        tickers_list.append("SPY")
    
    # 2. Compute date ranges
    end_date = datetime.strptime(current_date_str, "%Y-%m-%d")
    
    # Use UTC to prevent local timezone (e.g. Japan) from messing up the 'today' comparison.
    now_utc = datetime.now(timezone.utc)
    # If the requested end_date is today (or in the future) relative to US market
    # hours, cap it to yesterday. Resolve "today in US markets" with a DST-aware
    # ET conversion so the day boundary is correct in both EST and EDT.
    now_us = utc_to_eastern(now_utc)
    today_us = now_us.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)

    if end_date >= today_us:
        end_date = today_us - timedelta(days=1)

    # Size the fetch window from a trading-day count (walk-forward + MC warmup +
    # buffer) so it delivers the required trading days even across holidays — a
    # fixed calendar-day span under-delivers (audit finding 8).
    window_start = compute_fetch_window_start(end_date)
    start_daily_str = window_start.strftime("%Y-%m-%dT00:00:00Z")
    start_1m_str = start_daily_str
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
            ref_sym = holdings[0]["ticker"] if holdings else None
            if not ref_sym or ref_sym not in intraday_by_date[date_str]:
                continue

            timestamps = [row['t'] for row in intraday_by_date[date_str][ref_sym]]

            # Delegate per-symphony tick building to the module-level,
            # API-free per-day builder.
            day_history[sym_id] = build_replay_day(
                sym_id=sym_id,
                date_str=date_str,
                holdings=holdings,
                intraday_by_date=intraday_by_date,
                timestamps=timestamps,
                hist_data_up_to_yesterday=hist_data_up_to_yesterday,
                yesterday_closes=yesterday_closes,
                spy_today=spy_today,
            )

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
    except (OSError, TypeError, ValueError, UnicodeError) as e:
        # A failed cache write is non-fatal — the history was still generated.
        # Log a WARNING naming the cache file so the operator sees the miss.
        logging.warning(
            "synthetic_history: failed to write cache file %s: %s",
            cache_file,
            e,
        )
                
    return history_125d
