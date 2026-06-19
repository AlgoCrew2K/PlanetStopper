"""Market calendar helpers for dashboard market-state rendering (DM feature).

This module is a pure, side-effect-free helper — no DB access, no network calls.
Used by app.py to determine whether to serve live state or the frozen close snapshot.
The engine (alpha_bot_execution.py) calls is_trading_day, session_open, and
session_close from this module for holiday-aware gating and dynamic close anchors.
"""

from datetime import date
from datetime import time as dt_time
from functools import lru_cache

import pandas_market_calendars as mcal

# US equity real market open per NYSE rules
REAL_MARKET_OPEN_TIME = dt_time(9, 30)
# US equity regular market close per NYSE rules (half-day close is 13:00 ET)
MARKET_CLOSE_TIME = dt_time(16, 0)
# NYSE early-close (half-day) time per exchange rules
HALF_DAY_CLOSE_TIME = dt_time(13, 0)

# Lazily initialised once; pandas_market_calendars I/O is expensive so we
# hold a single module-level instance and never reconstruct it on the hot path.
_NYSE_CALENDAR = mcal.get_calendar("XNYS")


@lru_cache(maxsize=512)
def _get_schedule_for_year(year: int):
    """Return the NYSE schedule DataFrame for *year* (cached per calendar year).

    Caches on (year,) so the hot-path 1-minute engine tick never hits the
    calendar library more than once per year. Each call is pure and
    side-effect-free beyond the lru_cache store.
    """
    start = f"{year}-01-01"
    end = f"{year}-12-31"
    return _NYSE_CALENDAR.schedule(start_date=start, end_date=end, tz="America/New_York")


@lru_cache(maxsize=512)
def is_trading_day(d: date) -> bool:
    """Return True when *d* is a NYSE equity trading day (not a weekend or holiday).

    Args:
        d: A calendar date (datetime.date). Must be a concrete date — naive date
           objects are fine; tz is irrelevant because NYSE defines sessions in ET.

    Returns:
        True  — market is open for *d* (regular or half-day session)
        False — market is closed for *d* (weekend, NYSE holiday, or observed holiday)

    Implementation: queries the XNYS pandas-market-calendars schedule. Results are
    cached per-date (lru_cache) so the per-minute engine path incurs zero I/O after
    the first call for each date.
    """
    schedule = _get_schedule_for_year(d.year)
    # The schedule index contains only trading days; date presence = open.
    date_str = d.isoformat()
    return date_str in schedule.index.strftime("%Y-%m-%d")


@lru_cache(maxsize=512)
def session_open(d: date) -> dt_time:
    """Return the NYSE market open time (ET) for trading day *d*.

    Always 09:30 ET per NYSE rules — no early-open exceptions exist.

    Args:
        d: A calendar date that must be a trading day. Calling on a non-trading
           day is permitted but returns 09:30 (the standard open is returned as a
           best-effort; callers should guard with is_trading_day if they care).

    Returns:
        datetime.time(9, 30) — NYSE official open time.
    """
    # NYSE open is always 09:30; no exceptions in the calendar
    return REAL_MARKET_OPEN_TIME


@lru_cache(maxsize=512)
def session_close(d: date) -> dt_time:
    """Return the NYSE market close time (ET) for trading day *d*.

    Returns 13:00 on NYSE-designated early-close (half-day) sessions and
    16:00 on all regular trading days.

    Args:
        d: A calendar date. Non-trading days return 16:00 (standard close
           as a best-effort; callers should guard with is_trading_day).

    Returns:
        datetime.time(13, 0) — NYSE early close (half-day) sessions
        datetime.time(16, 0) — all other trading days
    """
    schedule = _get_schedule_for_year(d.year)
    date_str = d.isoformat()
    matched = [idx for idx in schedule.index if idx.strftime("%Y-%m-%d") == date_str]
    if not matched:
        # Non-trading day — return standard close as best-effort
        return MARKET_CLOSE_TIME
    row = schedule.loc[matched[0]]
    # market_close column is timezone-aware; extract the time component in ET
    close_ts = row["market_close"]
    return close_ts.to_pydatetime().timetz().replace(tzinfo=None).replace(second=0, microsecond=0)


def get_market_state(current_et) -> str:
    """Return the current US equity market state based on ET clock time and calendar.

    Args:
        current_et: A timezone-aware datetime in America/New_York (ET).
                    Must carry a zoneinfo-based tzinfo so DST is handled correctly.

    Returns:
        "open"          — trading day, 09:30 <= time < session_close(date) ET
        "pre_market"    — trading day, time < 09:30 ET
        "closed_frozen" — holiday, weekend, OR time >= session_close(date) ET

    Holiday and half-day awareness: delegates to is_trading_day and session_close,
    which are backed by the pandas-market-calendars XNYS schedule. No longer limited
    to weekday checks only.
    """
    today = current_et.date()
    if not is_trading_day(today):
        return "closed_frozen"

    current_time = current_et.time().replace(tzinfo=None)
    close_time = session_close(today)

    if current_time < REAL_MARKET_OPEN_TIME:
        return "pre_market"

    if REAL_MARKET_OPEN_TIME <= current_time < close_time:
        return "open"

    return "closed_frozen"
