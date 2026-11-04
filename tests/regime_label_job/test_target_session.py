"""RED tests -- regime_label_job.target_session and window_sessions (AC-1: S-1.2 to S-1.5; DC-3).

Contract (feature-plans/approach/regime-label-producer.md, Session arithmetic):
  target_session(start)  -> the first NYSE trading day whose 09:30 ET open is strictly after the
                            aware instant `start` (D is fixed from the job's start instant).
  window_sessions(D)     -> list of 31 NYSE sessions, oldest first: the 30 sessions strictly before
                            D plus the one before the oldest of them.

Every expected date is derived by ``_oracle_target`` below, an independent walk over
market_calendar.is_trading_day (never a literal copied from a run).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import market_calendar
from tests.regime_label_job import _sip as S

_OPEN = time(9, 30)


def _oracle_target(start: datetime) -> date:
    """First trading day whose 09:30 ET open is after `start` (independent reference)."""
    local = start.astimezone(S.ET)
    d = local.date()
    for _ in range(15):
        if market_calendar.is_trading_day(d) and datetime.combine(d, _OPEN, S.ET) > start:
            return d
        d += timedelta(days=1)
    raise AssertionError("no trading day within 15 days")


@pytest.fixture()
def job():
    import regime_label_job as mod

    return mod


# ---------------------------------------------------------------------------
# S-1.2  weekends, including a weekend before an NYSE Monday holiday
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "instant",
    [
        datetime(2026, 11, 7, 4, 30, tzinfo=UTC),  # Saturday, 23:30 ET Friday
        datetime(2026, 11, 7, 16, 0, tzinfo=UTC),  # Saturday midday
        datetime(2026, 11, 8, 4, 30, tzinfo=UTC),  # Sunday
        datetime(2026, 11, 9, 3, 0, tzinfo=UTC),  # Sunday 22:00 ET
        datetime(2026, 9, 5, 4, 30, tzinfo=UTC),  # Saturday before Labor Day (Mon 2026-09-07)
        datetime(2026, 9, 6, 4, 30, tzinfo=UTC),  # Sunday before Labor Day
    ],
    ids=lambda i: i.isoformat(),
)
def test_weekend_run_targets_the_first_trading_day_after_the_weekend(job, instant):
    got = job.target_session(instant)
    assert got == _oracle_target(instant)
    assert market_calendar.is_trading_day(got)
    assert got.weekday() < 5
    assert got > instant.astimezone(S.ET).date()


def test_weekend_before_a_monday_holiday_skips_the_holiday(job):
    holiday = date(2026, 9, 7)
    assert not market_calendar.is_trading_day(holiday), "precondition: Labor Day is closed"
    for instant in (
        datetime(2026, 9, 5, 4, 30, tzinfo=UTC),
        datetime(2026, 9, 6, 4, 30, tzinfo=UTC),
    ):
        got = job.target_session(instant)
        assert got != holiday
        assert got > holiday
        # nothing between the holiday and D is a trading day: D is the FIRST one after it
        d = holiday
        while d < got:
            assert not market_calendar.is_trading_day(d)
            d += timedelta(days=1)


# ---------------------------------------------------------------------------
# S-1.3  the night before a weekday holiday; the window holds no holiday date
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("instant", "holiday"),
    [
        (
            datetime(2026, 11, 26, 4, 30, tzinfo=UTC),
            date(2026, 11, 26),
        ),  # night before Thanksgiving
        (
            datetime(2026, 12, 25, 4, 30, tzinfo=UTC),
            date(2026, 12, 25),
        ),  # night before Christmas Day
        (datetime(2027, 1, 1, 4, 30, tzinfo=UTC), date(2027, 1, 1)),  # night before New Year's Day
    ],
    ids=["thanksgiving", "christmas", "new-years"],
)
def test_night_before_a_weekday_holiday_targets_the_session_after_it(job, instant, holiday):
    assert not market_calendar.is_trading_day(holiday), "precondition: the day is an NYSE closure"
    got = job.target_session(instant)
    assert got == _oracle_target(instant)
    assert got > holiday
    assert market_calendar.is_trading_day(got)
    sessions = job.window_sessions(got)
    assert holiday not in sessions
    assert all(market_calendar.is_trading_day(s) for s in sessions)


# ---------------------------------------------------------------------------
# S-1.4  04:30 host time (UTC) in EST and in EDT
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "instant",
    [
        datetime(2026, 11, 10, 4, 30, tzinfo=UTC),  # EST: 23:30 ET on Monday 2026-11-09
        datetime(2026, 10, 27, 4, 30, tzinfo=UTC),  # EDT: 00:30 ET on Tuesday 2026-10-27
    ],
    ids=["est", "edt"],
)
def test_04_30_utc_run_targets_the_trading_day_of_the_utc_date_in_both_offsets(job, instant):
    assert instant.weekday() == 1, "precondition: a Tuesday, not a holiday"
    got = job.target_session(instant)
    assert got == instant.date()
    assert got == _oracle_target(instant)


# ---------------------------------------------------------------------------
# S-1.5  a run after 09:30 ET on a trading day never targets that day
# ---------------------------------------------------------------------------


def test_run_at_ten_et_on_a_trading_day_targets_the_next_trading_day(job):
    ten_et = datetime(2026, 11, 4, 10, 0, tzinfo=S.ET).astimezone(UTC)
    got = job.target_session(ten_et)
    assert got != date(2026, 11, 4)
    assert got == _oracle_target(ten_et)
    assert got > date(2026, 11, 4)


def test_run_at_ten_et_on_a_friday_targets_monday(job):
    ten_et = datetime(2026, 11, 6, 10, 0, tzinfo=S.ET).astimezone(UTC)
    got = job.target_session(ten_et)
    assert got == _oracle_target(ten_et)
    assert got.weekday() == 0


@pytest.mark.parametrize(
    ("clock_et", "same_day"),
    [
        (time(9, 29, 59), True),  # one second before the open: the open is still ahead
        (time(9, 30, 0), False),  # exactly at the open: the open is not after the instant
        (time(9, 30, 1), False),
    ],
    ids=["one-second-before-open", "at-open", "one-second-after-open"],
)
def test_the_open_boundary_is_strict(job, clock_et, same_day):
    instant = datetime.combine(date(2026, 11, 4), clock_et, S.ET).astimezone(UTC)
    got = job.target_session(instant)
    assert (got == date(2026, 11, 4)) is same_day
    assert got == _oracle_target(instant)


_instants = st.datetimes(
    min_value=datetime(2026, 1, 1), max_value=datetime(2027, 6, 30), timezones=st.just(UTC)
)


@settings(max_examples=120, deadline=None)
@given(instant=_instants)
def test_target_is_the_earliest_trading_day_opening_after_the_instant(instant):
    import regime_label_job as job

    got = job.target_session(instant)
    assert market_calendar.is_trading_day(got)
    assert datetime.combine(got, _OPEN, S.ET) > instant
    # minimality: no earlier trading day opens after the instant
    d = got - timedelta(days=1)
    while d >= instant.astimezone(S.ET).date():
        assert not (
            market_calendar.is_trading_day(d) and datetime.combine(d, _OPEN, S.ET) > instant
        )
        d -= timedelta(days=1)


# ---------------------------------------------------------------------------
# window_sessions: 30 sessions strictly before D plus the one before the oldest
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target",
    [
        date(2026, 11, 4),
        date(2026, 11, 9),
        date(2026, 11, 27),
        date(2026, 12, 28),
        date(2026, 9, 8),
    ],
)
def test_window_sessions_are_31_contiguous_trading_days_strictly_before_the_target(job, target):
    assert market_calendar.is_trading_day(target)
    sessions = job.window_sessions(target)
    assert len(sessions) == job.REGIME_WINDOW_MAX_LOOKBACK_SESSIONS + 1
    assert sessions == sorted(sessions)
    assert len(set(sessions)) == len(sessions)
    assert all(s < target for s in sessions)
    assert all(market_calendar.is_trading_day(s) for s in sessions)
    # contiguous in trading days: no trading day is skipped between the first and the last
    expected = S.sessions_ending(sessions[-1], len(sessions))
    assert sessions == expected
    # the newest is the trading day immediately before D
    d = target - timedelta(days=1)
    while not market_calendar.is_trading_day(d):
        d -= timedelta(days=1)
    assert sessions[-1] == d


def test_window_never_includes_the_target_session_itself(job):
    target = date(2026, 11, 4)
    assert target not in job.window_sessions(target)


@settings(max_examples=40, deadline=None)
@given(offset=st.integers(min_value=0, max_value=300))
def test_window_sessions_length_and_order_hold_for_any_trading_day(offset):
    import regime_label_job as job

    d = date(2026, 1, 5) + timedelta(days=offset)
    while not market_calendar.is_trading_day(d):
        d += timedelta(days=1)
    sessions = job.window_sessions(d)
    assert len(sessions) == job.REGIME_WINDOW_MAX_LOOKBACK_SESSIONS + 1
    assert all(a < b for a, b in zip(sessions, sessions[1:], strict=False))
    assert sessions[-1] < d
