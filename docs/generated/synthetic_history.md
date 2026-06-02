# synthetic_history

> 250-day Alpaca historical fetcher with parallel bar download, file cache, and eligibility guards — feeds the autotuner walk-forward replay.

**Source:** `synthetic_history.py`
**Last updated:** 2026-06-02

## Overview

`synthetic_history.py` fetches the trading-day history required by the autotuner's walk-forward replay. It downloads daily and intraday bars from Alpaca for every ticker held across all symphonies, caches results to disk, and builds the structured history dict consumed by `autotuner._collect_sim_returns`.

The fetch window is computed to guarantee at least `_REQUIRED_FETCH_TRADING_DAYS = 250 + 39 + 10 = 299` trading days (replay window + MC warmup + buffer). A widen-and-refetch loop (max 3 attempts, 60-day calendar step) handles upstream data gaps. A persistent shortfall raises `HistoryShortfallError`.

Per-day returns are emitted in **percent** units (`tick["return"] = agg_ret * 100.0` at the producer boundary). This is the canonical return frame for the autotuner; the CRRA-EU branch converts to decimal fractions at its entry boundary via `RETURN_PCT_TO_FRACTION`.

## API Reference

### Main Entry Point

#### `generate_synthetic_history(bot_state: dict, current_date_str: str) → dict`
Generates the full synthetic history dict for all symphonies present in `bot_state`. Returns a symphony-keyed dict where each value is a date-keyed dict of tick lists.

This is the single call site for both `autotuner.run_autotuner` (pre-market) and `ai_advisor.revalidate_suggestion_oos` (on-demand).

---

### Fetch Helpers

#### `compute_fetch_window_start(end_date) → date`
Returns the fetch-window start date. Adds `_REQUIRED_FETCH_TRADING_DAYS * _CALENDAR_DAYS_PER_TRADING_DAY` calendar days as a generous early bound; the genuine floor is enforced post-fetch on the returned bar count.

#### `utc_to_eastern(utc_dt) → datetime`
Converts a UTC datetime to US Eastern (DST-aware via `ZoneInfo("America/New_York")`).

---

### Exception

#### `class HistoryShortfallError(Exception)`
Raised when synthetic-history generation cannot meet the trading-day floor after the bounded widen-and-refetch loop. Caught precisely by `autotuner.run_autotuner` to convert to a graceful abort.

## Types

### Fetch-Window Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `_WALK_FORWARD_TRADING_DAYS` | 250 | Autotuner replay window length |
| `_MC_WARMUP_TRADING_DAYS` | 39 | `MC_MIN_HISTORY_DAYS + (MC_VOL_WINDOW_DAYS - 1)` |
| `_FETCH_WINDOW_BUFFER_TRADING_DAYS` | 10 | Holiday / margin safety buffer |
| `_REQUIRED_FETCH_TRADING_DAYS` | 299 | Total trading days the fetch must guarantee |
| `_CALENDAR_DAYS_PER_TRADING_DAY` | 1.5 | Calendar-to-trading-day padding factor |
| `_FETCH_WIDEN_STEP_CALENDAR_DAYS` | 60 | Step size for widen-and-refetch |
| `_MAX_FETCH_WIDEN_ATTEMPTS` | 3 | Hard bound on widen loop |

## Internal Dependencies

- `math_engine` — `MC_MIN_HISTORY_DAYS`, `MC_VOL_WINDOW_DAYS` (for `_MC_WARMUP_TRADING_DAYS` computation)
- Alpaca Markets data API (`ALPACA_BASE_URL`, `ALPACA_KEY`, `ALPACA_SECRET`)
- `joblib.Parallel` — parallel ticker bar downloads
