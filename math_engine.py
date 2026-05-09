import numpy as np

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
    valid_dates = sorted(list(historical_data.keys()))[-20:]
    if len(valid_dates) < 20:
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

    daily_returns = returns_matrix.dot(weights) * 100.0

    if len(daily_returns) == 0:
        return 0.0

    return float(np.std(daily_returns))

def calculate_14d_atr_pct(holdings, historical_data):
    """
    Calculates the 14-day Volatility-Adjusted (ATR) percentage for the holdings.
    Falls back to calculate_20d_vol if high/low data is missing.
    """
    valid_dates = sorted(list(historical_data.keys()))[-15:]
    if len(valid_dates) < 15:
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
            atr_pct_array[j] = (avg_tr / recent_close) * 100.0
        else:
            return calculate_20d_vol(holdings, historical_data)
            
    portfolio_atr_pct = atr_pct_array.dot(weights)
    return float(portfolio_atr_pct)
