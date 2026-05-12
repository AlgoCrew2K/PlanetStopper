import math

import numpy as np

# ---------------------------------------------------------------------------
# Module-level named constants (project rule: no magic numbers in math_engine)
# ---------------------------------------------------------------------------

LOOKBACK_DAYS = 20  # 20-day realized-volatility window — AlphaBot risk-sizing standard
ATR_LOOKBACK_DAYS = 15  # 14-day true-range window (standard ATR period) + 1 prior close required to compute the first TR; matches AlphaBot's risk-sizing assumption
PCT_SCALAR = 100.0  # decimal return -> percentage points (math layer normalizes to pct)

# Time-squeeze decay constants (drives intraday tightening of trailing stops)
DECAY_CURVE_SCALAR = 9       # log10(1 + 9*t) maps t in [0,1] to decay in [0,1]; produces the characteristic AlphaBot intraday decay curve
MULT_OPEN = 1.5              # dynamic_multiplier at market open (loosest stop)
MULT_CLOSE = 0.5             # dynamic_multiplier at market close (tightest)
MIN_STOP_OPEN = 0.3          # min stop floor at market open, in percentage points
MIN_STOP_CLOSE = 0.15        # min stop floor at market close, in percentage points
VOL_FALLBACK = 1.0           # neutral fallback for safe_vol when symphony_vol <= 0 (preserves vol-scale arithmetic in the degenerate-vol case)

# Breakeven-lock constants (drives HWM-hold-based stop tightening)
BREAKEVEN_ACTIVATION_MIN = 0.4         # lower clamp for dynamic activation threshold (in percentage points)
BREAKEVEN_ACTIVATION_MAX = 3.0         # upper clamp for dynamic activation threshold
BREAKEVEN_ACTIVATION_DEADBAND = 0.2    # current_return must be within this distance below dynamic_activation to count a tick
HWM_HOLD_TICKS_THRESHOLD = 5          # consecutive qualifying ticks needed to lock breakeven (transition is one-way)
TRIGGERED_OVERRIDE_LEVEL = -999.0      # sentinel stop level when position is already triggered (suppresses re-exit)


def compute_para_arm_decision(
    current_return: float,
    prev_return: float,
    para_threshold: float,
    currently_armed: bool,
) -> tuple[float, bool]:
    """
    Pure decision for parabolic-squeeze arming.

    Returns (velocity, should_arm_transition).
      - velocity = current_return - prev_return (no scaling, no clamping)
      - should_arm_transition = True iff (velocity >= threshold) AND (not currently_armed)
      - Once armed, never re-arms. Caller is responsible for state mutation,
        prints, and DB logging.

    Extracted from alpha_bot_execution.py:559-569 to comply with the project
    file-map rule that math layers live in math_engine.py.
    """
    velocity = float(current_return) - float(prev_return)
    should_arm = bool((velocity >= para_threshold) and (not currently_armed))
    return velocity, should_arm


def compute_time_squeeze_decay(time_ratio: float) -> tuple[float, float]:
    """
    Returns (dynamic_multiplier, dynamic_min_stop) for the time-squeeze decay
    curve.

    - time_ratio in [0.0, 1.0] is fraction of trading session elapsed
      (0.0 = market open, 1.0 = close). CALLER clamps before passing; this
      function does not validate.
    - decay_curve = log10(1 + DECAY_CURVE_SCALAR * time_ratio), ranges from
      0.0 at open to 1.0 at close.
    - dynamic_multiplier linearly interpolates from MULT_OPEN at decay=0
      to MULT_CLOSE at decay=1.
    - dynamic_min_stop linearly interpolates from MIN_STOP_OPEN at decay=0
      to MIN_STOP_CLOSE at decay=1.

    Pure. No I/O. No state. No datetime handling.

    Extracted from alpha_bot_execution.py:574-585 (cycle 4 of math-layer
    extraction).
    """
    decay_curve = math.log10(1 + DECAY_CURVE_SCALAR * time_ratio)
    dynamic_multiplier = float(MULT_OPEN - (MULT_OPEN - MULT_CLOSE) * decay_curve)
    dynamic_min_stop = float(MIN_STOP_OPEN - (MIN_STOP_OPEN - MIN_STOP_CLOSE) * decay_curve)
    return dynamic_multiplier, dynamic_min_stop


def compute_active_trailing_stop(
    symphony_vol: float,
    dynamic_multiplier: float,
    dynamic_min_stop: float,
    para_armed: bool,
    breakeven_locked: bool,
    parabolic_squeeze_multiplier: float,
) -> float:
    """
    Computes the active trailing-stop distance (in percentage points).

    Logic (extracted verbatim from alpha_bot_execution.py):
      safe_vol = symphony_vol if symphony_vol > 0 else VOL_FALLBACK
      active = max(safe_vol * dynamic_multiplier, dynamic_min_stop)
      if para_armed or breakeven_locked:
          active *= parabolic_squeeze_multiplier
      return active

    Pure. No I/O. No state. CALLER is responsible for normalizing
    bot_state[...].get("para_armed") and ...get("breakeven_locked") to
    strict Python bool BEFORE passing (typically via bool(...)).
    """
    safe_vol = symphony_vol if symphony_vol > 0 else VOL_FALLBACK
    active = max(safe_vol * dynamic_multiplier, dynamic_min_stop)
    if para_armed or breakeven_locked:
        active *= parabolic_squeeze_multiplier
    return float(active)


def compute_breakeven_update(
    current_return: float,
    symphony_vol: float,
    base_stop_level: float,
    current_hold_ticks: int,
    currently_breakeven_locked: bool,
    is_triggered: bool,
) -> tuple[int, bool, float]:
    """
    Computes the breakeven-lock state update and the resolved stop trigger level.

    Returns (new_hold_ticks, new_breakeven_locked, stop_trigger_level).

    Logic (extracted verbatim from alpha_bot_execution.py):
      dynamic_activation = clamp(symphony_vol, BREAKEVEN_ACTIVATION_MIN, BREAKEVEN_ACTIVATION_MAX)
      if current_return >= dynamic_activation - BREAKEVEN_ACTIVATION_DEADBAND:
          new_hold_ticks = current_hold_ticks + 1
      else:
          new_hold_ticks = 0
      new_breakeven_locked = currently_breakeven_locked or (new_hold_ticks >= HWM_HOLD_TICKS_THRESHOLD)
      if new_breakeven_locked:
          stop_trigger_level = max(base_stop_level, 0.0)   # 0.0 is structural — semantic anchor "breakeven = no worse than zero loss"
      else:
          stop_trigger_level = base_stop_level
      if is_triggered:
          stop_trigger_level = TRIGGERED_OVERRIDE_LEVEL

    LATCHING INVARIANT: once currently_breakeven_locked=True, new_breakeven_locked
    is ALWAYS True regardless of any other input. The inline producer never
    resets breakeven_locked to False; that one-way transition must be preserved.

    Pure. No I/O. No state. Caller assigns the returned new_hold_ticks and
    new_breakeven_locked back into bot_state.
    """
    dynamic_activation = max(BREAKEVEN_ACTIVATION_MIN, min(BREAKEVEN_ACTIVATION_MAX, symphony_vol))
    if current_return >= (dynamic_activation - BREAKEVEN_ACTIVATION_DEADBAND):
        new_hold_ticks = current_hold_ticks + 1
    else:
        new_hold_ticks = 0
    new_breakeven_locked = bool(currently_breakeven_locked or (new_hold_ticks >= HWM_HOLD_TICKS_THRESHOLD))
    if new_breakeven_locked:
        stop_trigger_level = max(base_stop_level, 0.0)
    else:
        stop_trigger_level = base_stop_level
    if is_triggered:
        stop_trigger_level = TRIGGERED_OVERRIDE_LEVEL
    return int(new_hold_ticks), new_breakeven_locked, float(stop_trigger_level)


def run_monte_carlo(holdings, historical_data, spy_today_return, simulation_paths=5000, neighbor_k=150):
    """
    Vectorized Monte Carlo simulation using Nearest Neighbors matching.
    """
    current_symphony_return = sum(
        (h.get("last_percent_change", 0.0) * 100.0) * h.get("allocation", 0.0)
        for h in holdings if h.get("last_percent_change") is not None
    )
    valid_dates = sorted(list(historical_data.keys()))
    if len(valid_dates) < 20:
        return 100.0

    # 1. Calculate distances based on SPY return and rolling 20-day volatility
    spy_returns = np.array([historical_data[date].get("SPY", {}).get("daily_ret", 0.0) for date in valid_dates])
    
    spy_vols = np.zeros_like(spy_returns)
    for i in range(len(spy_returns)):
        start_idx = max(0, i - 19)
        if i > 0:
            spy_vols[i] = np.std(spy_returns[start_idx:i+1])
        else:
            spy_vols[i] = 0.0
            
    spy_today_ret_dec = spy_today_return / 100.0
    if len(spy_returns) >= 19:
        today_vol = np.std(np.append(spy_returns[-19:], spy_today_ret_dec))
    else:
        today_vol = np.std(np.append(spy_returns, spy_today_ret_dec))

    # Euclidean distance across 2 dimensions
    distances = np.sqrt((spy_returns - spy_today_ret_dec)**2 + (spy_vols - today_vol)**2)
    
    # 2. Get top K indices
    if len(distances) <= neighbor_k:
        nearest_indices = np.arange(len(distances))
    else:
        # argpartition is faster than full sort
        nearest_indices = np.argpartition(distances, neighbor_k)[:neighbor_k]
    
    nearest_days = [valid_dates[i] for i in nearest_indices]
    
    # 3. Weights and Tickers
    tickers = [h["ticker"] for h in holdings]
    weights = np.array([h.get("allocation", 0.0) for h in holdings])
    
    # 4. Build Returns Matrix (K days x N tickers)
    returns_matrix = np.zeros((len(nearest_days), len(tickers)))
    
    for i, date in enumerate(nearest_days):
        day_data = historical_data[date]
        spy_ret = day_data.get("SPY", {}).get("daily_ret", 0.0)
        for j, ticker in enumerate(tickers):
            if ticker in day_data:
                returns_matrix[i, j] = day_data[ticker].get("daily_ret", 0.0)
            else:
                returns_matrix[i, j] = spy_ret
                
    # 5. Calculate path returns (dot product is highly optimized in numpy)
    nearest_day_returns = returns_matrix.dot(weights) * 100.0
    
    # 6. Random selection & Cumulative Distribution
    sim_results = np.random.choice(nearest_day_returns, size=simulation_paths)
    
    sim_results.sort()
    below_count = np.searchsorted(sim_results, current_symphony_return)
    return ((simulation_paths - below_count) / simulation_paths) * 100.0

def calculate_20d_vol(holdings, historical_data):
    """
    Calculates the 20-day historical volatility of the given holdings based on historical_data.
    Vectorized for performance.
    """
    valid_dates = sorted(list(historical_data.keys()))[-LOOKBACK_DAYS:]
    if len(valid_dates) < LOOKBACK_DAYS:
        return 0.0

    tickers = [h.get("ticker") for h in holdings]
    weights = np.array([h.get("allocation", 0.0) for h in holdings])

    returns_matrix = np.zeros((len(valid_dates), len(tickers)))

    for i, date in enumerate(valid_dates):
        day_data = historical_data[date]
        spy_ret = day_data.get("SPY", {}).get("daily_ret", 0.0)
        for j, ticker in enumerate(tickers):
            if ticker in day_data:
                returns_matrix[i, j] = day_data[ticker].get("daily_ret", 0.0)
            else:
                returns_matrix[i, j] = spy_ret

    daily_returns = returns_matrix.dot(weights) * PCT_SCALAR

    if len(daily_returns) == 0:
        return 0.0

    return float(np.std(daily_returns))

def calculate_14d_atr_pct(holdings, historical_data):
    """
    Calculates the 14-day Volatility-Adjusted (ATR) percentage for the holdings.
    Falls back to calculate_20d_vol if high/low data is missing.
    """
    valid_dates = sorted(list(historical_data.keys()))[-ATR_LOOKBACK_DAYS:]
    if len(valid_dates) < ATR_LOOKBACK_DAYS:
        return calculate_20d_vol(holdings, historical_data)

    tickers = [h.get("ticker") for h in holdings]
    weights = np.array([h.get("allocation", 0.0) for h in holdings])
    
    atr_pct_array = np.zeros(len(tickers))
    
    for j, ticker in enumerate(tickers):
        tr_list = []
        last_close = None
        has_missing_data = False
        
        for date in valid_dates:
            day_data = historical_data[date].get(ticker)
            if not day_data or "high" not in day_data or "low" not in day_data or "close" not in day_data:
                has_missing_data = True
                break
                
            high = day_data["high"]
            low = day_data["low"]
            close = day_data["close"]
            
            if last_close is not None:
                tr = max(high - low, abs(high - last_close), abs(low - last_close))
                tr_list.append(tr)
            last_close = close
            
        if has_missing_data or len(tr_list) == 0:
            return calculate_20d_vol(holdings, historical_data)
            
        avg_tr = np.mean(tr_list)
        recent_close = last_close
        if recent_close and recent_close > 0:
            atr_pct_array[j] = (avg_tr / recent_close) * PCT_SCALAR
        else:
            return calculate_20d_vol(holdings, historical_data)
            
    portfolio_atr_pct = atr_pct_array.dot(weights)
    return float(portfolio_atr_pct)
