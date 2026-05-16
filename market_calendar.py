"""Market calendar helpers for dashboard market-state rendering (DM feature).

This module is a pure, side-effect-free helper — no DB access, no network calls.
Used by app.py to determine whether to serve live state or the frozen close snapshot.
The engine (alpha_bot_execution.py) does NOT call this module; engine gating is
controlled solely by EXECUTION_START_TIME and the existing weekday/time checks.
"""

from datetime import datetime, time as dt_time

# US equity real market open per NYSE rules
REAL_MARKET_OPEN_TIME = dt_time(9, 30)
# US equity market close per NYSE rules
MARKET_CLOSE_TIME = dt_time(16, 0)


def get_market_state(current_et: datetime) -> str:
    """Return the current US equity market state based on ET clock time and weekday.

    Args:
        current_et: A timezone-aware datetime in America/New_York (ET).
                    Must carry a zoneinfo-based tzinfo so DST is handled correctly.

    Returns:
        "open"          — weekday, 09:30 <= time < 16:00 ET
        "pre_market"    — weekday, time < 09:30 ET
        "closed_frozen" — weekend OR weekday >= 16:00 ET

    v1 limitations (documented in feature-plans/dm-dashboard-market-mode.md):
        - Half-day / holiday handling not implemented; fixed MARKET_CLOSE_TIME used.
        - Returns "open" between actual half-day close and MARKET_CLOSE_TIME on those days.
    """
    is_weekday = current_et.weekday() < 5
    current_time = current_et.time().replace(tzinfo=None)  # strip tz for comparison

    if not is_weekday:
        return "closed_frozen"

    if current_time < REAL_MARKET_OPEN_TIME:
        return "pre_market"

    if REAL_MARKET_OPEN_TIME <= current_time < MARKET_CLOSE_TIME:
        return "open"

    return "closed_frozen"
