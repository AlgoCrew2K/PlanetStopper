"""RED tests: how the daemon names a cycle's outcome from the clock and the exit code.

The daemon cannot see which branch the engine took, so it classifies by the engine's own
windows (alpha_bot_execution.py: closed outside 09:30 to the post-mortem cutoff or on a
non-trading day; blackout from seven minutes before the close; eod from the close to five
minutes after it). Every case is built from the golden NYSE calendar fixtures, never from a
typed time, so a half-day or a weekday holiday cannot be misread as a regular session.

Expected values are the contract (plan AC-1), derived from the session close of each date.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from datetime import time as dt_time
from zoneinfo import ZoneInfo

import pytest

import app as app_module
import market_calendar
from tests.app.test_held_basis_route_convergence import _frozen_datetime_class
from tests.holiday_calendar.test_holiday_calendar import (
    _SESSION_TIMES_FIXTURE,
    _TRADING_DAY_FIXTURE,
)

_ET = ZoneInfo("America/New_York")

# Seconds from the session close; the engine's blackout starts 7 minutes before it and its
# post-mortem window ends 5 minutes after it (alpha_bot_execution.py, checked by the
# calendar tripwire in tests/holiday_calendar).
_CLOCK_POSITIONS = [
    (-8 * 60, "cycle"),
    (-7 * 60 - 1, "cycle"),
    (-7 * 60, "blackout"),
    (-1, "blackout"),
    (0, "eod"),
    (5 * 60, "eod"),
    (5 * 60 + 1, "closed"),
    (60 * 60, "closed"),
]


def _at_close(case: dict, offset_seconds: int) -> datetime:
    day = date.fromisoformat(case["date"])
    hour, minute = (int(part) for part in case["expected_session_close"].split(":"))
    return datetime.combine(day, dt_time(hour, minute), tzinfo=_ET) + timedelta(
        seconds=offset_seconds
    )


_SESSIONS = _SESSION_TIMES_FIXTURE["cases"]
_CLOSED_DAYS = [c for c in _TRADING_DAY_FIXTURE["cases"] if not c["expected_is_trading_day"]]


@pytest.mark.parametrize("case", _SESSIONS, ids=[c["date"] for c in _SESSIONS])
@pytest.mark.parametrize(
    ("offset", "expected"), _CLOCK_POSITIONS, ids=[f"close{o:+d}s" for o, _ in _CLOCK_POSITIONS]
)
def test_outcome_follows_the_sessions_own_close(case, offset, expected):
    """Rejects fixed 15:53 and 16:05 constants (wrong on every half-day) and an off-by-one at
    either window edge: the blackout and the close step both include their first second."""
    started = _at_close(case, offset)

    assert app_module._classify_outcome(started, 0) == expected, (
        f"{started.isoformat()} on a session closing {case['expected_session_close']} "
        f"({case['reason']})"
    )


@pytest.mark.parametrize("case", _SESSIONS, ids=[c["date"] for c in _SESSIONS])
def test_open_window_edge_is_nine_thirty_sharp(case):
    day = date.fromisoformat(case["date"])
    before_open = datetime.combine(day, dt_time(9, 29, 59), tzinfo=_ET)
    at_open = datetime.combine(day, dt_time(9, 30, 0), tzinfo=_ET)

    assert app_module._classify_outcome(before_open, 0) == "closed"
    assert app_module._classify_outcome(at_open, 0) == "cycle"


@pytest.mark.parametrize("case", _CLOSED_DAYS, ids=[c["date"] for c in _CLOSED_DAYS])
@pytest.mark.parametrize("clock", [dt_time(11, 0), dt_time(15, 55), dt_time(16, 0)])
def test_a_non_trading_day_is_closed_at_every_hour(case, clock):
    """A weekday holiday has no blackout or close step; rejects a classifier that reads the
    clock without asking the calendar."""
    started = datetime.combine(date.fromisoformat(case["date"]), clock, tzinfo=_ET)

    assert app_module._classify_outcome(started, 0) == "closed"


def test_a_weekend_is_closed():
    saturday = datetime(2026, 7, 11, 11, 0, tzinfo=_ET)
    assert saturday.weekday() == 5
    assert app_module._classify_outcome(saturday, 0) == "closed"


@pytest.mark.parametrize("exit_code", [1, 2, 137, -9, None])
@pytest.mark.parametrize(
    "started",
    [
        datetime(2026, 7, 9, 10, 43, tzinfo=_ET),
        datetime(2026, 7, 9, 15, 55, tzinfo=_ET),
        datetime(2026, 7, 9, 16, 0, tzinfo=_ET),
        datetime(2026, 7, 9, 20, 0, tzinfo=_ET),
        datetime(2026, 7, 11, 11, 0, tzinfo=_ET),
    ],
    ids=["open", "blackout", "close", "evening", "weekend"],
)
def test_a_non_zero_or_missing_exit_code_is_failed_at_any_time(started, exit_code):
    assert app_module._classify_outcome(started, exit_code) == "failed"


def test_now_et_reads_the_clock_at_call_time_in_eastern_time(monkeypatch):
    """Both the _now_et replacement and a frozen datetime must reach the wall clock, so it is
    read when called, never captured at import."""
    instant = datetime(2026, 10, 22, 19, 30, 0, tzinfo=ZoneInfo("UTC"))
    monkeypatch.setattr(app_module, "datetime", _frozen_datetime_class(instant))

    now = app_module._now_et()

    assert now == instant
    assert now.utcoffset() == timedelta(hours=-4), "20 Oct is EDT: the clock must be Eastern"


def test_blackout_helper_agrees_with_the_classifier_on_every_session():
    """The classifier and the calendar helper are two readings of one window: the first
    blackout second per the helper must classify as blackout, the second before as cycle."""
    for case in _SESSIONS:
        day = date.fromisoformat(case["date"])
        start = datetime.combine(day, market_calendar.rebalance_blackout_start(day), tzinfo=_ET)

        assert app_module._classify_outcome(start, 0) == "blackout", case["date"]
        assert app_module._classify_outcome(start - timedelta(seconds=1), 0) == "cycle", case[
            "date"
        ]
