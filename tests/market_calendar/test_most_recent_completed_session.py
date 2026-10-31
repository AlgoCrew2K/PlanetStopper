"""`market_calendar.most_recent_completed_session`: the newest session already over.

The dashboard decides whether a close snapshot belongs to the last finished session or to an
earlier one, and the account-totals latch decides which snapshot it is waiting for. Both ask
the same question of the real exchange calendar: which trading day's close has already
passed? Every case runs on the real XNYS calendar through `market_calendar`; no calendar
function is stubbed.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

import market_calendar

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def at(day: str, clock: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=ET)


def test_a_function_exists_for_callers_to_import():
    assert hasattr(market_calendar, "most_recent_completed_session"), (
        "market_calendar.most_recent_completed_session is missing"
    )


class TestWeekdaySessions:
    @pytest.mark.parametrize(
        ("now", "expected"),
        [
            # A regular weekday evening: today's session is over.
            (at("2026-10-21", "18:00:00"), date(2026, 10, 21)),
            # The close minute itself counts as over (the session ends at 16:00:00).
            (at("2026-10-21", "16:00:00"), date(2026, 10, 21)),
            # One second before the close the session is still running: yesterday's is the last.
            (at("2026-10-21", "15:59:59"), date(2026, 10, 20)),
            # Mid-session and pre-market read the session before.
            (at("2026-10-21", "10:43:05"), date(2026, 10, 20)),
            (at("2026-10-21", "08:00:00"), date(2026, 10, 20)),
            # Midnight belongs to the new calendar day, whose session has not started.
            (at("2026-10-22", "00:01:00"), date(2026, 10, 21)),
            (at("2026-10-21", "23:59:59"), date(2026, 10, 21)),
        ],
    )
    def test_the_newest_session_whose_close_has_passed(self, now, expected):
        assert market_calendar.most_recent_completed_session(now) == expected


class TestWeekendsAndHolidays:
    def test_monday_pre_market_reads_friday(self):
        assert market_calendar.most_recent_completed_session(at("2026-10-26", "08:00:00")) == date(
            2026, 10, 23
        )

    def test_saturday_and_sunday_read_friday(self):
        for day in ("2026-10-24", "2026-10-25"):
            assert market_calendar.most_recent_completed_session(at(day, "12:00:00")) == date(
                2026, 10, 23
            ), day

    def test_a_weekday_holiday_evening_reads_the_session_before_it(self):
        # Thanksgiving 2026-11-26 is a Thursday and the exchange is closed.
        assert market_calendar.most_recent_completed_session(at("2026-11-26", "18:00:00")) == date(
            2026, 11, 25
        )

    def test_a_monday_holiday_reads_the_friday_before_the_long_weekend(self):
        # 2027-01-18 is a Monday holiday.
        assert market_calendar.most_recent_completed_session(at("2027-01-18", "12:00:00")) == date(
            2027, 1, 15
        )


class TestHalfDays:
    # 2026-11-27 (Friday after Thanksgiving) closes at 13:00 ET.
    def test_before_the_early_close_the_session_is_still_running(self):
        assert market_calendar.most_recent_completed_session(at("2026-11-27", "12:59:59")) == date(
            2026, 11, 25
        )

    def test_at_and_after_the_early_close_the_session_is_over(self):
        for clock in ("13:00:00", "13:00:20", "16:30:00"):
            assert market_calendar.most_recent_completed_session(at("2026-11-27", clock)) == date(
                2026, 11, 27
            ), clock

    def test_a_regular_day_is_not_treated_as_a_half_day_at_one_pm(self):
        assert market_calendar.most_recent_completed_session(at("2026-10-21", "13:30:00")) == date(
            2026, 10, 20
        )


class TestInstantsInOtherZonesAndNaiveInstants:
    def test_an_aware_utc_instant_is_converted_to_eastern_time_first(self):
        # 2026-10-21 21:00 UTC is 17:00 ET: the session is over.
        utc_evening = datetime(2026, 10, 21, 21, 0, tzinfo=UTC)
        assert market_calendar.most_recent_completed_session(utc_evening) == date(2026, 10, 21)
        # 2026-10-21 19:30 UTC is 15:30 ET: still running, the day before is the answer.
        utc_afternoon = datetime(2026, 10, 21, 19, 30, tzinfo=UTC)
        assert market_calendar.most_recent_completed_session(utc_afternoon) == date(2026, 10, 20)

    def test_the_utc_date_does_not_decide_the_session(self):
        # 2026-10-21 23:30 ET is already 2026-10-22 03:30 UTC; the ET date is the 21st.
        late_et = at("2026-10-21", "23:30:00").astimezone(UTC)
        assert market_calendar.most_recent_completed_session(late_et) == date(2026, 10, 21)

    def test_a_naive_instant_is_read_as_eastern_wall_time(self):
        assert market_calendar.most_recent_completed_session(datetime(2026, 10, 21, 18, 0)) == date(
            2026, 10, 21
        )
        assert market_calendar.most_recent_completed_session(datetime(2026, 10, 21, 10, 0)) == date(
            2026, 10, 20
        )

    def test_either_side_of_the_autumn_clock_change_reads_the_right_close(self):
        # 2026-11-01 is a Sunday: the clocks go back. Friday's close is the answer on both sides.
        before = at("2026-10-31", "20:00:00")
        after = at("2026-11-02", "08:00:00")
        assert market_calendar.most_recent_completed_session(before) == date(2026, 10, 30)
        assert market_calendar.most_recent_completed_session(after) == date(2026, 10, 30)


class TestLookbackBound:
    def test_the_bound_is_the_documented_ten_days(self):
        assert getattr(market_calendar, "_COMPLETED_SESSION_LOOKBACK_DAYS", None) == 10, (
            "the walk-back bound must be the named constant, 10"
        )

    def test_a_walk_that_finds_no_session_inside_the_bound_returns_none(self, monkeypatch):
        # Monday 08:00: Sunday and Saturday are not sessions; with a one-day bound the walk
        # ends before Friday, so there is no answer. The caller reads None as "unknown".
        monkeypatch.setattr(market_calendar, "_COMPLETED_SESSION_LOOKBACK_DAYS", 1, raising=False)
        assert market_calendar.most_recent_completed_session(at("2026-10-26", "08:00:00")) is None

    def test_the_default_bound_reaches_across_a_long_weekend(self):
        # Tuesday after a Monday holiday, early: Friday is four calendar days back.
        assert market_calendar.most_recent_completed_session(at("2027-01-19", "08:00:00")) == date(
            2027, 1, 15
        )

    def test_the_answer_never_comes_from_the_future(self):
        now = at("2026-10-21", "09:00:00")
        found = market_calendar.most_recent_completed_session(now)
        assert found is not None and found < now.date()
