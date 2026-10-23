"""RED tests: how the daemon names a cycle's outcome from the clock and the exit code.

The daemon cannot see which branch the engine took, so it classifies by the engine's own
windows (alpha_bot_execution.py: closed outside 09:30 to the post-mortem cutoff or on a
non-trading day; blackout from seven minutes before the close; eod from the close to five
minutes after it). Every case is built from the golden NYSE calendar fixtures, never from a
typed time, so a half-day or a weekday holiday cannot be misread as a regular session.

Expected values are the contract (plan AC-1), derived from the session close of each date.
Each test sweeps a whole family of cases and reports every mismatch at once, because a test
database is rebuilt around every test and a per-case test costs more than the sweep.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from datetime import time as dt_time
from zoneinfo import ZoneInfo

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

_SESSIONS = _SESSION_TIMES_FIXTURE["cases"]
_CLOSED_DAYS = [c for c in _TRADING_DAY_FIXTURE["cases"] if not c["expected_is_trading_day"]]


def _at_close(case: dict, offset_seconds: int) -> datetime:
    day = date.fromisoformat(case["date"])
    hour, minute = (int(part) for part in case["expected_session_close"].split(":"))
    return datetime.combine(day, dt_time(hour, minute), tzinfo=_ET) + timedelta(
        seconds=offset_seconds
    )


def _mismatches(rows) -> list[str]:
    """rows: (started_at, exit_code, expected, label); the classifier's misses as readable lines."""
    misses = []
    for started, exit_code, expected, label in rows:
        got = app_module._classify_outcome(started, exit_code)
        if got != expected:
            misses.append(
                f"{started.isoformat()} exit={exit_code} {label}: want {expected!r}, got {got!r}"
            )
    return misses


def test_outcome_follows_each_sessions_own_close():
    """Rejects fixed 15:53 and 16:05 constants (wrong on every half-day) and an off-by-one at
    either window edge: the blackout and the close step both include their first second."""
    rows = [
        (
            _at_close(case, offset),
            0,
            expected,
            f"{case['date']} close {case['expected_session_close']}",
        )
        for case in _SESSIONS
        for offset, expected in _CLOCK_POSITIONS
    ]

    assert _mismatches(rows) == []


def test_the_open_edge_is_nine_thirty_sharp_on_every_session():
    rows = []
    for case in _SESSIONS:
        day = date.fromisoformat(case["date"])
        rows.append(
            (datetime.combine(day, dt_time(9, 29, 59), tzinfo=_ET), 0, "closed", case["date"])
        )
        rows.append(
            (datetime.combine(day, dt_time(9, 30, 0), tzinfo=_ET), 0, "cycle", case["date"])
        )

    assert _mismatches(rows) == []


def test_a_non_trading_day_is_closed_at_every_hour():
    """A weekday holiday has no blackout or close step; rejects a classifier that reads the
    clock without asking the calendar."""
    assert _CLOSED_DAYS, "the golden calendar must list non-trading days"
    rows = [
        (
            datetime.combine(date.fromisoformat(case["date"]), clock, tzinfo=_ET),
            0,
            "closed",
            case["reason"],
        )
        for case in _CLOSED_DAYS
        for clock in (dt_time(11, 0), dt_time(15, 55), dt_time(16, 0))
    ]

    assert _mismatches(rows) == []


def test_a_weekend_is_closed():
    saturday = datetime(2026, 7, 11, 11, 0, tzinfo=_ET)
    assert saturday.weekday() == 5
    assert _mismatches([(saturday, 0, "closed", "saturday")]) == []


def test_a_non_zero_or_missing_exit_code_is_failed_at_any_time():
    instants = {
        "open": datetime(2026, 7, 9, 10, 43, tzinfo=_ET),
        "blackout": datetime(2026, 7, 9, 15, 55, tzinfo=_ET),
        "close": datetime(2026, 7, 9, 16, 0, tzinfo=_ET),
        "evening": datetime(2026, 7, 9, 20, 0, tzinfo=_ET),
        "weekend": datetime(2026, 7, 11, 11, 0, tzinfo=_ET),
    }
    rows = [
        (started, exit_code, "failed", label)
        for label, started in instants.items()
        for exit_code in (1, 2, 137, -9, None)
    ]

    assert _mismatches(rows) == []


def test_now_et_reads_the_clock_at_call_time_in_eastern_time(monkeypatch):
    """Both the _now_et replacement and a frozen datetime must reach the wall clock, so it is
    read when called, never captured at import."""
    instant = datetime(2026, 10, 22, 19, 30, 0, tzinfo=ZoneInfo("UTC"))
    monkeypatch.setattr(app_module, "datetime", _frozen_datetime_class(instant))

    now = app_module._now_et()

    assert now == instant
    assert now.utcoffset() == timedelta(hours=-4), "22 Oct is EDT: the clock must be Eastern"


def test_blackout_helper_agrees_with_the_classifier_on_every_session():
    """The classifier and the calendar helper are two readings of one window: the first
    blackout second per the helper must classify as blackout, the second before as cycle."""
    rows = []
    for case in _SESSIONS:
        day = date.fromisoformat(case["date"])
        start = datetime.combine(day, market_calendar.rebalance_blackout_start(day), tzinfo=_ET)
        rows.append((start, 0, "blackout", case["date"]))
        rows.append((start - timedelta(seconds=1), 0, "cycle", case["date"]))

    assert _mismatches(rows) == []
