import datetime as _dt
import json
import logging
import os
import time
from datetime import datetime, timedelta
from json import JSONDecodeError
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv
from joblib import Parallel, delayed

import market_calendar
import math_engine

load_dotenv()
ALPACA_KEY = os.getenv("ALPACA_KEY")
ALPACA_SECRET = os.getenv("ALPACA_SECRET")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://data.alpaca.markets/v2")

# US Eastern is the canonical exchange zone for NYSE/NASDAQ. ZoneInfo handles
# the EST<->EDT transition automatically, so the UTC->ET conversion is correct
# year-round (a fixed UTC-4 offset is wrong for the ~5 months ET is on EST).
_US_EASTERN = ZoneInfo("America/New_York")

# Environment-bounded parallelism degree for the intraday replay Parallel() call.
# PRODUCTION DEFAULT IS -1 (all cores) — byte-identical to the historical
# behavior. The per-day replay work (process_day -> build_replay_day) is
# deterministic and order-independent: each tick's Monte-Carlo seed is content-
# derived (math_engine.derive_cycle_mc_seed(f"{sym_id}_{date_str}")), so the
# degree of parallelism does NOT affect numerical results (verified: no RNG
# call in this module depends on worker index or scheduling order). Changing
# n_jobs is therefore reproducibility-neutral.
#
# WHY THIS EXISTS: generate_synthetic_history runs INSIDE pytest-xdist workers
# during the test suite. With joblib n_jobs=-1 nested inside each of N xdist
# workers, the process fan-out is multiplicative (workers x cores) and committed
# >90 GB of memory, causing two CRITICAL_PROCESS_DIED bugchecks (2026-06-11/12).
# tests/conftest.py sets ALPHABOT_MAX_JOBS=1 so the test environment runs this
# loop single-process; production leaves it unset (-> -1, all cores).
_MAX_JOBS_ENV = "ALPHABOT_MAX_JOBS"


def _resolve_replay_n_jobs() -> int:
    """Return the joblib n_jobs degree for the intraday replay.

    Reads ALPHABOT_MAX_JOBS from the environment; default -1 (all cores) so
    production behavior is unchanged. A garbled value falls back to the same
    safe production default. This is a test-environment throttle ONLY — it is
    reproducibility-neutral (see _MAX_JOBS_ENV comment above).
    """
    raw = os.environ.get(_MAX_JOBS_ENV)
    if raw is not None and raw.strip():
        try:
            return int(raw)
        except ValueError:
            pass
    return -1


def compute_vwap_from_bars(bars):
    """Cumulative session VWAP for a list of minute-bar dicts.

    Standard volume-weighted average price using the bar's close as the
    typical price (matches the per-bar typical price the production inline
    cumulant at generate_synthetic_history below uses — see the close-based
    PV / cumulative-sum / cum_v fallback path). Returns the final cumulative
    VWAP across the supplied bars; the running cumulant is required only
    incidentally (the test asserts the closed-form final value).

    Args:
        bars: iterable of dicts each with at least 'c' (close) and 'v'
            (volume) fields — the Alpaca minute-bar shape that the rest of
            the file consumes.

    Returns:
        Final cumulative VWAP across the bars as a float, or the last bar's
        close as the zero-volume fallback when total volume is zero (matches
        the np.where(cum_v > 0, cum_pv/cum_v, c) inline guard used downstream).
        Returns None for an empty input.

    AC-8 / INV-COV-3: extracted from the inline cumulant computation so the
    VWAP arithmetic is testable at the numeric level (the original audit
    flagged this as runtime-untested).
    """
    if not bars:
        return None
    total_pv = 0.0
    total_v = 0.0
    last_close = float(bars[-1].get("c", 0.0))
    for bar in bars:
        c = float(bar.get("c", 0.0))
        v = float(bar.get("v", 0.0))
        total_pv += c * v
        total_v += v
    if total_v <= 0.0:
        # Zero-volume fallback — mirrors the np.where(cum_v > 0, ..., c)
        # guard the inline cumulant uses downstream.
        return last_close
    return total_pv / total_v


# --- Fetch-window sizing (trading-day counted, not calendar-day literal) ----
# The autotuner slices the last 250 trading days of synthetic history for its
# walk-forward replay. Pinned to the autotuner replay-window length — also the
# minimum replay-day floor: fewer than this and the validation fold is
# degenerate (the shortfall guard below logs an ERROR).
# Phase-1 walk-forward window extension (feature-plans/walk-forward-overhaul.md,
# Gate-2 approved 2026-06-01): the prior half-year window collapsed to ~4 usable
# OOS validation days after the 60/20/20 split + purge(20)/embargo(1); 250 days
# yields ~29 usable validation days, providing meaningful statistical power for
# the CRRA-EU objective.
_WALK_FORWARD_TRADING_DAYS = 250
# The Monte-Carlo gate on the FIRST replay day still needs MC_MIN_HISTORY_DAYS
# eligible days of prior history, and each eligible day needs
# MC_VOL_WINDOW_DAYS - 1 raw days of vol-window warmup behind it
# (math_engine.run_monte_carlo eligible-pool guard). That warmup PRECEDES the
# replay window, so it is additive to the 250-day slice.
_MC_WARMUP_TRADING_DAYS = math_engine.MC_MIN_HISTORY_DAYS + (math_engine.MC_VOL_WINDOW_DAYS - 1)
# Holiday / margin buffer: a safety cushion of extra trading days so the
# fetched window comfortably clears the floor even if the upstream bar feed
# drops a trailing day or a half-day. 10 trading days ~ two market weeks.
_FETCH_WINDOW_BUFFER_TRADING_DAYS = 10
# Total trading days the fetch window must guarantee. This is the floor the
# AUTHORITY for which is the actual count of daily bars Alpaca returns (Alpaca
# delivers a bar only on a real trading day, so the returned-bar count is the
# self-validating exchange calendar) plus the post-fetch shortfall guards in
# generate_synthetic_history — NOT a hand-maintained holiday table.
_REQUIRED_FETCH_TRADING_DAYS = (
    _WALK_FORWARD_TRADING_DAYS + _MC_WARMUP_TRADING_DAYS + _FETCH_WINDOW_BUFFER_TRADING_DAYS
)

# Calendar-days-per-trading-day padding factor used only to pick a generously
# early fetch-window START. The raw weekday ratio is 7/5 = 1.4; NYSE closes
# ~9 days/year for holidays, so 1.5 leaves a comfortable cushion. The start
# does not need to be exact — it only needs to be early enough that the fetch
# returns at least _REQUIRED_FETCH_TRADING_DAYS bars; the genuine floor is
# enforced post-fetch on the real returned-bar count.
_CALENDAR_DAYS_PER_TRADING_DAY = 1.5

# Widen+refetch fallback policy. When a first fetch returns fewer trading days
# than _REQUIRED_FETCH_TRADING_DAYS, the window start is pushed earlier by this
# many calendar days and the fetch is retried. 60 calendar days ~ 42 trading
# days — a meaningful widen that recovers a short window in one or two steps.
_FETCH_WIDEN_STEP_CALENDAR_DAYS = 60
# Hard bound on the widen+refetch loop: after this many fetch attempts still
# short of the floor, the helper raises rather than widening forever. 3 keeps
# the rare fallback bounded — the first window is already generously padded, so
# a persistent shortfall means a genuine upstream data gap, not a sizing miss.
_MAX_FETCH_WIDEN_ATTEMPTS = 3


class HistoryShortfallError(Exception):
    """Raised when synthetic-history generation cannot meet the trading-day floor.

    Signals a persistent shortfall on either axis — the daily / MC-warmup fetch
    (after the bounded widen+refetch) or the intraday / replay-slice. A named
    exception type (not a bare RuntimeError with a magic string) so
    autotuner.run_autotuner can catch it precisely and convert it to a graceful,
    surfaced abort without swallowing unrelated failures.
    """


def utc_to_eastern(utc_dt):
    """Convert a UTC datetime to the corresponding US Eastern datetime.

    DST-aware: ZoneInfo("America/New_York") applies EST (UTC-5) or EDT (UTC-4)
    according to the instant, so the resolved wall clock is correct year-round.
    A naive input is assumed to be UTC. Returns a tz-aware ET datetime.
    """
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=_dt.UTC)
    return utc_dt.astimezone(_US_EASTERN)


def compute_fetch_window_start(end_date):
    """Return the fetch-window start date for a given end date.

    Picks a generously early start by padding the required trading-day floor
    into calendar days (_REQUIRED_FETCH_TRADING_DAYS * _CALENDAR_DAYS_PER_-
    TRADING_DAY). This deliberately over-reaches: the start only needs to be
    early enough that the Alpaca fetch returns at least the required number of
    trading days. The genuine trading-day floor is NOT enforced here — it is
    enforced post-fetch on the real returned-bar count (Alpaca returns a bar
    only on a real trading day, so the bar count is the self-validating
    exchange calendar). No hand-maintained holiday table — the old
    timedelta(days=180) literal under-delivered (audit finding 8); a holiday
    table would reintroduce a drift class of its own.

    Accepts a date or datetime; returns a datetime.date.
    """
    if isinstance(end_date, _dt.datetime):
        end_date = end_date.date()
    calendar_days = int(_REQUIRED_FETCH_TRADING_DAYS * _CALENDAR_DAYS_PER_TRADING_DAY)
    return end_date - timedelta(days=calendar_days)


def _count_distinct_trading_days(bars_payload):
    """Count distinct trading-day dates in a fetch_bars-shaped payload.

    fetch_bars returns {symbol: [bar, ...]} where each bar carries a 't' ISO
    timestamp. Alpaca emits a daily bar only on a real trading day, so the
    distinct t[:10] dates ARE the exchange calendar — the self-validating
    authority for the trading-day floor.
    """
    days = set()
    for bar_list in (bars_payload or {}).values():
        for bar in bar_list:
            days.add(bar["t"][:10])
    return len(days)


def fetch_daily_bars_with_floor(fetch_fn, end_date=None):
    """Fetch daily bars, widening the window and refetching until the floor is met.

    Drives a bounded count -> widen -> refetch loop: the first window start is
    the generous calendar pad from compute_fetch_window_start; if the returned
    bars hold fewer than _REQUIRED_FETCH_TRADING_DAYS distinct trading days the
    start is pushed _FETCH_WIDEN_STEP_CALENDAR_DAYS earlier and the fetch is
    retried, up to _MAX_FETCH_WIDEN_ATTEMPTS times. A still-short result after
    the bound is a HARD failure (HistoryShortfallError) — never a silent proceed
    with an under-floor history, which would feed the autotuner a degenerate
    replay.

    fetch_fn is the fetch callable, injected so the retry loop is testable
    without a live Alpaca fetch. It is called with the window start date and
    must return a fetch_bars-shaped {symbol: [bar, ...]} payload.

    Returns the first fetch_bars-shaped payload that clears the floor.
    """
    if end_date is None:
        end_date = utc_to_eastern(datetime.now(_dt.UTC)).date()
    window_start = compute_fetch_window_start(end_date)

    for attempt in range(_MAX_FETCH_WIDEN_ATTEMPTS):
        bars = fetch_fn(window_start)
        distinct_days = _count_distinct_trading_days(bars)
        if distinct_days >= _REQUIRED_FETCH_TRADING_DAYS:
            return bars
        logging.warning(
            "synthetic_history: daily-bar fetch short of the %d-trading-day "
            "floor (%d returned, attempt %d/%d) — widening the window %d "
            "calendar days and refetching",
            _REQUIRED_FETCH_TRADING_DAYS,
            distinct_days,
            attempt + 1,
            _MAX_FETCH_WIDEN_ATTEMPTS,
            _FETCH_WIDEN_STEP_CALENDAR_DAYS,
        )
        window_start = window_start - timedelta(days=_FETCH_WIDEN_STEP_CALENDAR_DAYS)

    raise HistoryShortfallError(
        "synthetic_history: daily-bar fetch remained short of the "
        f"{_REQUIRED_FETCH_TRADING_DAYS}-trading-day floor after "
        f"{_MAX_FETCH_WIDEN_ATTEMPTS} widen+refetch attempts — the upstream "
        "feed has an insufficient daily-bar history; aborting rather than "
        "running the autotuner on a degenerate replay window"
    )


def load_cached_history(cache_file):
    """Load a parsed synthetic-history cache file, or None on a corrupt/missing one.

    A corrupt or unreadable cache is a recoverable miss — the caller regenerates
    the history — so this never raises. On failure it logs a WARNING naming the
    cache file and the exception detail, then returns None.
    """
    try:
        with open(cache_file) as f:
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
    return {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}


def fetch_bars(tickers_list, start_str, end_str, timeframe="1Day"):
    headers = get_alpaca_headers()
    batch_size = 30
    all_data = {}

    for i in range(0, len(tickers_list), batch_size):
        batch = tickers_list[i : i + batch_size]
        symbol_string = ",".join(batch)
        print(
            f"      -> Fetching {timeframe} bars batch {i // batch_size + 1}/{len(tickers_list) // batch_size + 1}..."  # noqa: E501  # un-wrappable long line
        )

        page_token = None
        while True:
            url = f"{ALPACA_BASE_URL}/stocks/bars?symbols={symbol_string}&timeframe={timeframe}&start={start_str}&end={end_str}&limit=10000&adjustment=split&feed=iex"  # noqa: E501  # un-wrappable long line
            if page_token:
                url += f"&page_token={page_token}"

            success = False
            for attempt in range(10):  # Allow up to 10 retries for rate limits
                try:
                    response = requests.get(url, headers=headers, timeout=30)
                    if response.status_code == 200:
                        success = True
                        break
                    elif response.status_code == 429:
                        print(
                            f"      -> Rate limit hit (429). Sleeping for 15s... (Attempt {attempt + 1}/10)"  # noqa: E501  # un-wrappable long line
                        )
                        time.sleep(15)
                    else:
                        # Log status code only; omit response.text to avoid leaking payload in logs (cycle-#27 hardening)  # noqa: E501  # inline comment cannot be wrapped without splitting the annotation
                        logging.warning(
                            "fetch_bars: HTTP %s for batch %d (attempt %d/10)",
                            response.status_code,
                            i // batch_size + 1,
                            attempt + 1,
                        )
                        time.sleep(5)
                except requests.RequestException as e:
                    # Narrow to transport/connection errors raised by requests; re-raise unexpected errors upstream  # noqa: E501  # inline comment cannot be wrapped without splitting the annotation
                    logging.error("fetch_bars: request failed on attempt %d/10: %s", attempt + 1, e)
                    time.sleep(5)

            if not success:
                logging.error(
                    "fetch_bars: batch %d failed after 10 attempts; aborting symbol fetch",
                    i // batch_size + 1,
                )
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
        # AC-1 (MA-1): per-ticker last_percent_change for THIS tick, reusing
        # the same (c - y_close) / y_close fraction computed below for
        # agg_ret. Keyed by ticker, not stamped onto `holdings` in place —
        # `holdings` is closure-captured and reused across every day of the
        # same symphony's replay under Parallel(n_jobs=...), so an in-place
        # stamp would leak a stale value into a later call.
        tick_lpc: dict[str, float] = {}

        for h in holdings:
            ticker = h["ticker"]
            alloc = h["allocation"]

            if ticker in intraday_by_date[date_str] and i < len(intraday_by_date[date_str][ticker]):
                bar = intraday_by_date[date_str][ticker][i]
                c = bar["c"]
                v = bar["vwap"]

                y_close = yesterday_closes.get(ticker, c)
                if y_close > 0:
                    ret = (c - y_close) / y_close
                    agg_ret += alloc * ret
                    tick_lpc[ticker] = ret

                if v > 0:
                    weighted_vwap_diff += alloc * ((c - v) / v)
                valid_alloc += alloc

        # AC-1 (MA-1): stamp this tick's lpc onto a FRESH holdings copy —
        # never mutate `holdings`/`h` in place (see comment above). A ticker
        # with no bar this tick (or a non-positive y_close) has no tick_lpc
        # entry; last_percent_change is then None, and
        # math_engine.run_monte_carlo's existing lpc-exclusion contract
        # (:1162-1166) drops it from current_symphony_return exactly as it
        # does for a genuinely lpc-less live holding — never a fabricated 0.0.
        priced_holdings = [
            {**h, "last_percent_change": tick_lpc.get(h["ticker"])} for h in holdings
        ]

        # neighbor_k must equal production's MC_DEFAULT_NEIGHBOR_K so the
        # replay's mc_prob matches the live engine's estimand — with a smaller
        # k the kNN bootstrap CDF is a coarse step function. Seed is
        # per-(symphony, day) so cache rebuilds produce bit-identical series.
        mc_prob = math_engine.run_monte_carlo(
            priced_holdings,
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

        # Regime-match-quality guard (vision-audit Critical Rec #2): if today's
        # query point is unprecedented vs the candidate pool, the MC bootstrap
        # is unrepresentative — suppress the MC veto by carrying None, identical
        # to the insufficient-history path. Production wires the same guard in
        # alpha_bot_execution.py so both paths make bit-equivalent suppression
        # decisions given the same historical_data and spy_today inputs.
        _regime = math_engine.compute_regime_match_quality(hist_data_up_to_yesterday, spy_today)
        if _regime.is_unprecedented:
            mc_prob = None

        ticks.append(
            {
                "time": ts[11:16],
                "return": agg_ret * 100.0,
                "mc_prob": mc_prob,
                "vol": vol,
                "vwap_diff": weighted_vwap_diff,
                "base_atr_pct": base_atr,
                "valid_vwap_weight": valid_alloc,
            }
        )

    return ticks


def _resolve_history_cache_key(
    bot_state: dict, current_date_str: str
) -> tuple[set, dict, "str | None"]:
    """Derive (all_tickers, symphony_holdings, cache_file) for bot_state on
    current_date_str.

    SINGLE SOURCE OF TRUTH for the tickers -> holdings-hash -> versioned-
    filename cache-key derivation: generate_synthetic_history (the network-
    capable writer) and get_cached_synthetic_history_only (the cache-only
    reader used by autotuner.build_if_held_replay_series) both call this
    function rather than each deriving the key independently -- a mirrored
    re-implementation could silently drift from this one, making the
    cache-only reader permanently miss even on a genuine same-day,
    same-holdings hit, with no test catching it until it happens live.

    cache_file is None when bot_state has no holdings anywhere (an empty
    portfolio has nothing to cache or read -- mirrors
    generate_synthetic_history's own prior "if not all_tickers: return {}"
    short-circuit).
    """
    all_tickers = set()
    symphony_holdings = {}
    for sym_id, state in bot_state.items():
        if isinstance(state, dict) and "current_holdings" in state:
            holdings = state["current_holdings"]
            symphony_holdings[sym_id] = holdings
            for h in holdings:
                all_tickers.add(h["ticker"])

    if not all_tickers:
        return all_tickers, symphony_holdings, None

    import hashlib

    holdings_str = json.dumps(symphony_holdings, sort_keys=True)
    holdings_hash = hashlib.md5(holdings_str.encode("utf-8")).hexdigest()

    cache_dir = "cache"
    os.makedirs(cache_dir, exist_ok=True)
    # Cache marker bumped v2 -> v3 -> v4:
    # v2: old fixed 180-calendar-day window (Cluster-5 fix bumped to v3).
    # v3: trading-day-counted window sized for 125-day replay + MC warmup.
    # v4: Phase-1 window extension to 250 trading days (2026-06-01). A v3 file
    #     holds at most ~125 days of intraday history — exactly half the 250-day
    #     replay expectation. Loading it verbatim would feed the autotuner a
    #     degenerate half-window. v4 forces regeneration; stale v3 files are
    #     orphaned on disk (harmless — no matching key).
    cache_file = os.path.join(
        cache_dir, f"synthetic_history_v4_{current_date_str}_{holdings_hash}.json"
    )
    return all_tickers, symphony_holdings, cache_file


# Weekly autotune cadence (run_autotuner's own daily EOD invocation writes a
# fresh cache once per week's worth of walk-forward runs in practice) plus a
# buffer for a missed/aborted run or an operator pause -- PM live-gate
# ruling R2, 2026-07-24: the droplet's newest cache file was 6 calendar days
# (5 NYSE trading days) stale at gate time under a same-day-only probe, so
# 10 covers a full missed week with margin.
AUTOTUNE_CACHE_MAX_AGE_TRADING_DAYS: int = 10


def get_cached_synthetic_history_only(bot_state: dict, current_date_str: str) -> "dict | None":
    """CACHE-ONLY read of the synthetic-history file cache -- NEVER triggers
    a live fetch (contrast with generate_synthetic_history, which fetches on
    a cache miss). Uses the IDENTICAL cache-key derivation as
    generate_synthetic_history (via the shared _resolve_history_cache_key)
    so this reader and the network-capable writer always agree on which file
    represents "a given date's synthetic history for this bot_state".

    BACKWARD PROBE (PM live-gate ruling R2, 2026-07-24): the cache is
    written once per autotune cycle (weekly cadence), so a same-day-only
    probe is cold most days by construction (the droplet's cache was 6
    calendar days stale at live-gate time). Walks backward from
    current_date_str through up to AUTOTUNE_CACHE_MAX_AGE_TRADING_DAYS NYSE
    trading days (via market_calendar.is_trading_day -- this project's
    single source of truth for trading-day determination, never a
    re-derived weekday proxy), re-deriving the cache key for CURRENT
    holdings at EACH candidate date via the shared _resolve_history_cache_key
    -- so a holdings change since an aged file was written naturally
    degrades to a miss for that candidate (the aged file's embedded hash
    reflects the OLD holdings and never matches a freshly computed hash for
    current holdings; no special-casing needed). current_date_str itself
    (offset 0) is checked first and always wins when present -- still
    zero network, every candidate is only ever load_cached_history, never
    fetch_bars.

    Returns None on: no holdings anywhere in bot_state, every candidate in
    the backward window missing or corrupt/unreadable (see
    load_cached_history, which never raises).

    Sole consumer: autotuner.build_if_held_replay_series (the guard-alpha-
    preconditions dashboard route's cache-hit-only replay seam -- off the
    execution path, must never drive Alpaca fetch volume from a GET).
    """
    candidate_date = datetime.strptime(current_date_str, "%Y-%m-%d").date()
    trading_days_back = 0
    while trading_days_back <= AUTOTUNE_CACHE_MAX_AGE_TRADING_DAYS:
        candidate_date_str = candidate_date.strftime("%Y-%m-%d")
        _, _, cache_file = _resolve_history_cache_key(bot_state, candidate_date_str)
        if cache_file is not None:
            history_data = load_cached_history(cache_file)
            if history_data is not None:
                return history_data

        # Step to the previous NYSE trading day for the next candidate.
        candidate_date -= timedelta(days=1)
        while not market_calendar.is_trading_day(candidate_date):
            candidate_date -= timedelta(days=1)
        trading_days_back += 1

    return None


def generate_synthetic_history(bot_state, current_date_str, *, n_jobs: int | None = None):
    print("  -> Generating Synthetic Forward-Looking Intraday History...")

    all_tickers, symphony_holdings, cache_file = _resolve_history_cache_key(
        bot_state, current_date_str
    )
    if cache_file is None:
        return {}

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
    now_utc = datetime.now(_dt.UTC)
    # If the requested end_date is today (or in the future) relative to US market
    # hours, cap it to yesterday. Resolve "today in US markets" with a DST-aware
    # ET conversion so the day boundary is correct in both EST and EDT.
    now_us = utc_to_eastern(now_utc)
    today_us = now_us.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)

    if end_date >= today_us:
        end_date = today_us - timedelta(days=1)

    # Size the fetch window from a generously-padded calendar span (audit
    # finding 8: a fixed 180-day literal under-delivered trading days). The
    # daily fetch goes through the widen+refetch helper which enforces the
    # trading-day floor; the intraday fetch uses the same padded start.
    window_start = compute_fetch_window_start(end_date)
    start_1m_str = window_start.strftime("%Y-%m-%dT00:00:00Z")
    end_date_str_utc = end_date.strftime("%Y-%m-%dT23:59:59Z")

    # 3. Fetch Data
    # Route the daily fetch through the bounded widen+refetch helper: if the
    # first generously-padded window returns fewer than the required trading
    # days it widens and refetches, and hard-fails after the bound rather than
    # feeding a degenerate replay to the autotuner. The helper drives the
    # window start, so fetch_bars is wrapped in a closure taking that start.
    def _fetch_daily(window_start_date):
        return fetch_bars(
            tickers_list,
            window_start_date.strftime("%Y-%m-%dT00:00:00Z"),
            end_date_str_utc,
            "1Day",
        )

    daily_bars_raw = fetch_daily_bars_with_floor(_fetch_daily, end_date)
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
                    "close": curr_close,
                }

    # The daily / MC-warmup floor is enforced upstream by
    # fetch_daily_bars_with_floor — it widens + refetches and hard-fails if the
    # returned daily bars stay short of _REQUIRED_FETCH_TRADING_DAYS — so by
    # this point the daily history is guaranteed to clear the warmup floor.
    daily_dates = sorted(list(historical_daily.keys()))

    # 5. Process Intraday Bars
    intraday_by_date = {}
    for sym, bars in intraday_bars_raw.items():
        df = pd.DataFrame(bars)
        if df.empty:
            continue
        df["date"] = df["t"].str[:10]
        for date_str, group in df.groupby("date"):
            if date_str not in intraday_by_date:
                intraday_by_date[date_str] = {}

            group = group.copy()
            group["pv"] = group["c"] * group["v"]
            group["cum_pv"] = group["pv"].cumsum()
            group["cum_v"] = group["v"].cumsum()
            group["vwap"] = np.where(
                group["cum_v"] > 0, group["cum_pv"] / group["cum_v"], group["c"]
            )

            intraday_by_date[date_str][sym] = group[["t", "c", "vwap"]].to_dict(orient="records")

    # Last _WALK_FORWARD_TRADING_DAYS trading days — the autotuner replay slice.
    intraday_dates = sorted(list(intraday_by_date.keys()))[-_WALK_FORWARD_TRADING_DAYS:]

    # Surface a replay-window shortfall consistently with the daily axis: if the
    # upstream fetch returned fewer than the walk-forward floor (partial data /
    # feed hiccup), the [-N:] slice silently truncates and the autotuner would
    # run a degenerate validation fold. Log, then RAISE the shortfall exception
    # — both shortfall axes (daily / warmup and intraday / replay-slice) must
    # surface identically; run_autotuner catches it and aborts gracefully.
    if len(intraday_dates) < _WALK_FORWARD_TRADING_DAYS:
        logging.error(
            "synthetic_history: replay window shortfall — %d trading days "
            "available, autotuner expects >= %d; the walk-forward fold would "
            "be degenerate",
            len(intraday_dates),
            _WALK_FORWARD_TRADING_DAYS,
        )
        raise HistoryShortfallError(
            "synthetic_history: intraday replay-slice short of the "
            f"{_WALK_FORWARD_TRADING_DAYS}-trading-day floor "
            f"({len(intraday_dates)} available) — aborting rather than "
            "running the autotuner on a degenerate replay window"
        )

    history_125d = {sym_id: {} for sym_id in symphony_holdings}

    def process_day(date_str):
        day_history = {}
        # Get historical_data UP TO the day before
        prev_dates = [d for d in daily_dates if d < date_str]
        if not prev_dates:
            return date_str, {}

        hist_data_up_to_yesterday = {d: historical_daily[d] for d in prev_dates}

        yesterday_date = prev_dates[-1]
        yesterday_closes = {
            sym: historical_daily[yesterday_date][sym]["c"]
            for sym in historical_daily[yesterday_date]
        }

        spy_today = 0.0
        if "SPY" in historical_daily.get(date_str, {}):
            spy_today = historical_daily[date_str]["SPY"]["daily_ret"] * 100.0

        for sym_id, holdings in symphony_holdings.items():
            ref_sym = holdings[0]["ticker"] if holdings else None
            if not ref_sym or ref_sym not in intraday_by_date[date_str]:
                continue

            timestamps = [row["t"] for row in intraday_by_date[date_str][ref_sym]]

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

    print(
        f"  -> Simulating {len(intraday_dates)} days of Intraday Tick Data using Parallel Processing..."  # noqa: E501  # un-wrappable long line
    )
    # n_jobs is env-bounded (ALPHABOT_MAX_JOBS, default -1 = all cores in prod;
    # set to 1 by tests/conftest.py to neutralize the xdist x cores fan-out that
    # crashed the host — see _resolve_replay_n_jobs). Reproducibility-neutral.
    # Callers may pass an explicit n_jobs to override env-resolution (e.g. the
    # autotuner passes n_jobs=1 to bound peak RSS on the droplet — DE-AUTOTUNE-OOM).
    effective_n_jobs = n_jobs if n_jobs is not None else _resolve_replay_n_jobs()
    results = Parallel(n_jobs=effective_n_jobs)(delayed(process_day)(d) for d in intraday_dates)

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
