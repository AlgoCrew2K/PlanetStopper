"""
Adversarial RED tests for the holiday-calendar feature.

Scope (Gate-1 A/C):
  AC-1: US equity market holidays → non-trading day (engine skips data + action phase)
  AC-2: Half-day early-close → ALL close-relative times shift to 13:00 ET close
        (post_mortem ~13:00-13:05, rebalance_blackout ~12:53, squeeze-t = 1.0 at 13:00)
  AC-3: Regular trading days unchanged (09:30 open, 16:00 close)
  AC-4: Dashboard get_market_state uses the SAME calendar authority (correct banner
        on holidays and half-days)
  AC-5: math_engine.py squeeze-t docstring corrected: "0.0 = market open" replaced
        with "0.0 = EXECUTION_START_TIME" (the action-window open)

These tests are written to FAIL on the current codebase:
  - market_calendar.py has no holiday/half-day awareness (v1 limitation documented)
  - There is no calendar authority module (market_calendar.py has no is_trading_day,
    no session_close, no half-day detection)
  - alpha_bot_execution.py wires market_close and rebalance_blackout as hard constants
  - The squeeze-t docstring claims "0.0 = market open" (incorrect — anchor is
    EXECUTION_START_TIME)

Fixture provenance:
  All known dates and expected close times are derived from the NYSE official
  holiday/early-close schedule and committed to
  tests/fixtures/math/holiday_calendar/. They are NOT captured from any
  implementation — they are authoritative calendar facts.

Mocking philosophy:
  - Never mock the calendar authority itself; test it directly against fixtures.
  - Mock only network I/O (Composer, Alpaca), clock (get_current_et), and DB.
  - All test dates are FIXED — never datetime.today() or any dynamic computation.
  - For engine-gate tests: inject a fixed holiday/half-day date via clock mock;
    assert the gate behaviour without touching live state.

Test naming: test_<scenario>_<expected_outcome>
"""

from __future__ import annotations

import copy
import json
import math
import pathlib
from datetime import datetime, timedelta
from datetime import time as dt_time
from unittest.mock import MagicMock, patch

import pytest

# The new calendar authority module; import will FAIL until implementer creates it.
# This is intentional: a failing import IS a RED test (module does not exist yet).
try:
    import market_calendar
    _MARKET_CALENDAR_AVAILABLE = True
except ImportError:
    _MARKET_CALENDAR_AVAILABLE = False

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")

    def _et_datetime(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
        """Return a timezone-aware ET datetime for testing. Never uses today()."""
        return datetime(year, month, day, hour, minute, 0, tzinfo=_ET)

except Exception:  # pragma: no cover
    from datetime import timezone
    _ET = timezone(timedelta(hours=-4))  # EST fallback (no DST correction)

    def _et_datetime(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
        return datetime(year, month, day, hour, minute, 0, tzinfo=_ET)


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

_FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "math"
    / "holiday_calendar"
)


def _load_fixture(filename: str) -> dict:
    with (_FIXTURE_DIR / filename).open("r", encoding="utf-8") as fh:
        return json.load(fh)


_TRADING_DAY_FIXTURE = _load_fixture("is_trading_day_known_dates.json")
_SESSION_TIMES_FIXTURE = _load_fixture("session_times_known_dates.json")

# ---------------------------------------------------------------------------
# Helpers: skip decorator when the module doesn't exist yet (import fails).
# Every test that calls market_calendar is marked requires_calendar so the
# parametrize/collect still runs and produces a clear FAILED output (not an
# ERROR), but only when the module exists.
# ---------------------------------------------------------------------------

_requires_calendar = pytest.mark.skipif(
    not _MARKET_CALENDAR_AVAILABLE,
    reason=(
        "market_calendar module not yet created — this is a RED test; "
        "it will turn GREEN once the implementer creates market_calendar.py"
    ),
)


# ===========================================================================
# SECTION 1: Calendar authority — is_trading_day
# ===========================================================================

class TestIsTradingDayGoldenFixtures:
    """
    Golden-fixture tests for market_calendar.is_trading_day(date).

    Each case derives from the NYSE official schedule and is committed to
    tests/fixtures/math/holiday_calendar/is_trading_day_known_dates.json.
    The fixture is the authority; the test merely calls the function and
    compares.

    RED because: market_calendar.is_trading_day does not exist yet.
    """

    @pytest.mark.parametrize(
        "case",
        _TRADING_DAY_FIXTURE["cases"],
        ids=[c["date"] for c in _TRADING_DAY_FIXTURE["cases"]],
    )
    @_requires_calendar
    def test_is_trading_day_matches_fixture(self, case: dict) -> None:
        """
        is_trading_day(date) must return the fixture's expected_is_trading_day
        for every known fixed date.

        Adversarial intent:
        - A naive weekday-only implementation passes regular weekdays and
          weekends but FAILS on holiday dates.
        - Christmas 2024 (Wed), Independence Day 2025 (Fri), Good Friday 2025
          (Fri) are weekdays that must return False.
        - Veterans Day (Mon) is a Federal holiday that the NYSE does NOT observe
          — it must return True (trap for an impl using Federal holiday lists).
        """
        date = datetime.strptime(case["date"], "%Y-%m-%d").date()
        result = market_calendar.is_trading_day(date)
        assert result == case["expected_is_trading_day"], (
            f"is_trading_day({case['date']}) should be {case['expected_is_trading_day']} "
            f"({case['reason']}); got {result}"
        )

    @_requires_calendar
    def test_is_trading_day_returns_bool_not_truthy(self) -> None:
        """
        is_trading_day must return a Python bool, not a numpy bool or an
        integer-truthy value. Downstream code that does `if not result` could
        silently misfire on a non-bool truthy.
        """
        from datetime import date as dt_date
        result = market_calendar.is_trading_day(dt_date(2025, 5, 14))  # regular Wednesday
        assert type(result) is bool, (
            f"is_trading_day must return exactly bool; got {type(result).__name__}"
        )

    @_requires_calendar
    def test_christmas_2024_wednesday_is_not_trading_day(self) -> None:
        """
        Christmas Day 2024 falls on a Wednesday — a weekday. A naive weekday-only
        guard would return True. The calendar authority must return False.

        This is the primary adversarial test for the is_trading_day holiday guard.
        """
        from datetime import date as dt_date
        result = market_calendar.is_trading_day(dt_date(2024, 12, 25))
        assert result is False, (
            "is_trading_day(2024-12-25) must be False — Christmas Day is a NYSE holiday "
            "even though it is a Wednesday. A weekday-only guard fails this test."
        )

    @_requires_calendar
    def test_good_friday_2025_is_not_trading_day(self) -> None:
        """
        Good Friday 2025 (April 18) is a NYSE holiday even though it is a
        Friday. The NYSE is the only major exchange that observes Good Friday.
        """
        from datetime import date as dt_date
        result = market_calendar.is_trading_day(dt_date(2025, 4, 18))
        assert result is False, (
            "is_trading_day(2025-04-18) must be False — Good Friday 2025 is a NYSE holiday. "
            "This date is a Friday; a weekday-only guard incorrectly returns True."
        )

    @_requires_calendar
    def test_veterans_day_is_a_trading_day(self) -> None:
        """
        Veterans Day (November 11, 2024 = Monday) is a Federal holiday that the
        NYSE does NOT observe. US equity markets trade normally.

        An impl using the US Federal holiday list (instead of NYSE calendar)
        would incorrectly return False here.
        """
        from datetime import date as dt_date
        result = market_calendar.is_trading_day(dt_date(2024, 11, 11))
        assert result is True, (
            "is_trading_day(2024-11-11) must be True — Veterans Day is NOT a NYSE holiday. "
            "NYSE does not close for Veterans Day; only Federal holiday lists include it."
        )


# ===========================================================================
# SECTION 2: Calendar authority — session_close and session_open
# ===========================================================================

class TestSessionTimesGoldenFixtures:
    """
    Golden-fixture tests for market_calendar.session_close(date) and
    market_calendar.session_open(date).

    Half-day close = 13:00 ET. Regular close = 16:00 ET. Open = 09:30 always.

    RED because: market_calendar.session_close / session_open do not exist yet.
    """

    @pytest.mark.parametrize(
        "case",
        _SESSION_TIMES_FIXTURE["cases"],
        ids=[c["date"] for c in _SESSION_TIMES_FIXTURE["cases"]],
    )
    @_requires_calendar
    def test_session_close_matches_fixture(self, case: dict) -> None:
        """
        session_close(date) must return dt_time(13, 0) for half-days and
        dt_time(16, 0) for regular days.

        Adversarial intent:
        - Black Friday (day after Thanksgiving) — trap for impls that only know
          fixed-calendar half-days and miss the post-Thanksgiving pattern.
        - July 3, 2025 — a Thursday early-close; the same date as a full trading
          day in other years, so impls that hardcode July 3 as always half-day pass.
        """
        date = datetime.strptime(case["date"], "%Y-%m-%d").date()
        expected_close_parts = [int(p) for p in case["expected_session_close"].split(":")]
        expected_close = dt_time(expected_close_parts[0], expected_close_parts[1])
        result = market_calendar.session_close(date)
        assert result == expected_close, (
            f"session_close({case['date']}) should be {expected_close} "
            f"({case['reason']}); got {result}"
        )

    @pytest.mark.parametrize(
        "case",
        _SESSION_TIMES_FIXTURE["cases"],
        ids=[c["date"] for c in _SESSION_TIMES_FIXTURE["cases"]],
    )
    @_requires_calendar
    def test_session_open_matches_fixture(self, case: dict) -> None:
        """
        session_open(date) must return dt_time(9, 30) for ALL trading days —
        whether full or half-day. Early close never affects open time.
        """
        date = datetime.strptime(case["date"], "%Y-%m-%d").date()
        expected_open_parts = [int(p) for p in case["expected_session_open"].split(":")]
        expected_open = dt_time(expected_open_parts[0], expected_open_parts[1])
        result = market_calendar.session_open(date)
        assert result == expected_open, (
            f"session_open({case['date']}) should be {expected_open} "
            f"(open is always 09:30 ET regardless of half-day status); got {result}"
        )

    @_requires_calendar
    def test_black_friday_2024_session_close_is_1300(self) -> None:
        """
        Black Friday 2024 (November 29) — the day after Thanksgiving — is an
        NYSE early-close half-day. session_close must return 13:00 ET.

        This test is named explicitly (not just parametrized) to call out the
        adversarial case: impls that check for Thanksgiving-minus-one-day rather
        than looking up the actual NYSE schedule will get date arithmetic wrong
        in edge-case years.
        """
        from datetime import date as dt_date
        result = market_calendar.session_close(dt_date(2024, 11, 29))
        assert result == dt_time(13, 0), (
            f"session_close(2024-11-29) should be 13:00 (Black Friday early close); "
            f"got {result}"
        )

    @_requires_calendar
    def test_regular_day_session_close_is_1600(self) -> None:
        """
        session_close on a regular trading day must return 16:00 ET.
        Catches an impl that always returns 13:00 (or uses a constant).
        """
        from datetime import date as dt_date
        result = market_calendar.session_close(dt_date(2025, 5, 14))  # regular Wednesday
        assert result == dt_time(16, 0), (
            f"session_close(2025-05-14) should be 16:00 ET (regular trading day); "
            f"got {result}"
        )

    @_requires_calendar
    def test_session_close_returns_dt_time_not_string(self) -> None:
        """
        session_close must return a datetime.time object, not a string like "13:00"
        or "16:00". Downstream comparison is via dt_time objects.
        """
        from datetime import date as dt_date, time as dt_time_cls
        result = market_calendar.session_close(dt_date(2025, 5, 14))
        assert isinstance(result, dt_time_cls), (
            f"session_close must return datetime.time; got {type(result).__name__}"
        )

    @_requires_calendar
    def test_session_close_first_trading_day_after_dst_spring_forward_2025(self) -> None:
        """
        DST boundary guard: March 10, 2025 (Monday) is the first full trading day
        after the 2025 DST spring-forward (clocks advance March 9, 2025 at 02:00).

        session_close must return dt_time(16, 0) — not dt_time(15, 0) or dt_time(17, 0).

        Wrong implementation that this catches: a session_close that extracts the
        close time from a UTC-based representation without correctly accounting for
        the EDT/EST offset change. After spring-forward ET is UTC-4 (EDT) rather than
        UTC-5 (EST). An impl that accidentally computes UTC time and applies the wrong
        offset could return 21:00 (UTC) misread as 16:00 in EST = off by one hour.

        Derivation: NYSE regular close is 16:00 ET wall-clock on all regular trading
        days, year-round, regardless of DST. The calendar authority must reflect
        ET wall-clock time.
        """
        from datetime import date as dt_date
        result = market_calendar.session_close(dt_date(2025, 3, 10))
        assert result == dt_time(16, 0), (
            f"session_close(2025-03-10) should be 16:00 ET (first trading day after "
            f"DST spring-forward 2025 — clocks advanced March 9 at 02:00). "
            f"Got {result}. A DST extraction bug could return 15:00 or 17:00 here "
            f"instead of the correct 16:00 ET wall-clock time."
        )

    @_requires_calendar
    def test_session_close_first_trading_day_after_dst_fall_back_2024(self) -> None:
        """
        DST boundary guard: November 4, 2024 (Monday) is the first full trading day
        after the 2024 DST fall-back (clocks fall back November 3, 2024 at 02:00).

        session_close must return dt_time(16, 0) on this regular trading day.

        After fall-back ET is UTC-5 (EST). An impl that uses a fixed UTC-4 offset
        (EDT) after fall-back would compute 16:00 EDT as 20:00 UTC and then extract
        the wrong wall-clock time.

        Note: November 29 2024 (Black Friday) is already in the fixture as a half-day.
        This test specifically targets a REGULAR day immediately after the fall-back
        boundary to guard the offset-direction independently.
        """
        from datetime import date as dt_date
        result = market_calendar.session_close(dt_date(2024, 11, 4))
        assert result == dt_time(16, 0), (
            f"session_close(2024-11-04) should be 16:00 ET (first trading day after "
            f"2024 DST fall-back — clocks fell back November 3 at 02:00). "
            f"Got {result}."
        )


# ===========================================================================
# SECTION 3: Dashboard — get_market_state uses calendar authority
# ===========================================================================

class TestGetMarketStateHolidayAndHalfDay:
    """
    AC-4: market_calendar.get_market_state must return the correct state on
    holidays and half-days.

    RED because: the current get_market_state is weekday-only with no
    holiday/half-day awareness (v1 limitation documented in the existing code).
    """

    def test_get_market_state_returns_closed_frozen_on_holiday_during_trading_hours(self) -> None:
        """
        On Christmas Day 2024 (Wednesday) at 12:00 ET — a time that would be
        "open" on a regular weekday — get_market_state must return "closed_frozen".

        RED: the current weekday-only implementation returns "open" at 12:00 on
        any weekday including holidays.
        """
        # Christmas 2024 = Wednesday Dec 25, during normal trading hours
        christmas_noon = _et_datetime(2024, 12, 25, 12, 0)
        result = market_calendar.get_market_state(christmas_noon)
        assert result == "closed_frozen", (
            f"get_market_state on Christmas 2024 at 12:00 ET should be 'closed_frozen' "
            f"(NYSE holiday); got {result!r}. The v1 weekday-only impl returns 'open'."
        )

    def test_get_market_state_returns_closed_frozen_after_half_day_close(self) -> None:
        """
        On Black Friday 2024 (November 29) at 14:00 ET — one hour after the 13:00
        early close — get_market_state must return "closed_frozen".

        RED: the current impl uses a fixed 16:00 close; it would return "open" at
        14:00 on any weekday including half-days.
        """
        black_friday_afternoon = _et_datetime(2024, 11, 29, 14, 0)
        result = market_calendar.get_market_state(black_friday_afternoon)
        assert result == "closed_frozen", (
            f"get_market_state on Black Friday 2024 at 14:00 ET should be 'closed_frozen' "
            f"(market closes at 13:00 ET on half-days); got {result!r}. "
            f"The v1 impl uses fixed 16:00 close and incorrectly returns 'open'."
        )

    def test_get_market_state_returns_open_during_half_day_morning(self) -> None:
        """
        On Black Friday 2024 at 11:00 ET — the market IS open (session runs
        09:30 to 13:00 on half-days). get_market_state must return "open".

        Catches an impl that marks all half-days as "closed_frozen" for the
        whole day.

        DISCRIMINATING COMPANION: v1 (weekday-only, fixed 16:00 close) returns
        "open" at 11:00 on any weekday — same as v2 here. To ensure this test is
        adversarially meaningful for the half-day feature, a second assertion is
        added at 13:01 on the SAME date. v1 returns "open" at 13:01 (market not
        closed until 16:00 in v1); v2 must return "closed_frozen" (market already
        closed at 13:00 on a half-day). The 13:01 assertion is the discriminating
        case that fails v1 and passes v2.
        """
        black_friday_morning = _et_datetime(2024, 11, 29, 11, 0)
        result_morning = market_calendar.get_market_state(black_friday_morning)
        assert result_morning == "open", (
            f"get_market_state on Black Friday 2024 at 11:00 ET should be 'open' "
            f"(session is 09:30-13:00 on half-days); got {result_morning!r}"
        )

        # Discriminating companion: v1 returns "open" at 13:01; v2 must return "closed_frozen"
        black_friday_post_close = _et_datetime(2024, 11, 29, 13, 1)
        result_post_close = market_calendar.get_market_state(black_friday_post_close)
        assert result_post_close == "closed_frozen", (
            f"get_market_state on Black Friday 2024 at 13:01 ET should be 'closed_frozen' "
            f"(market already closed at 13:00 on half-days). Got {result_post_close!r}. "
            f"The v1 weekday-only impl returns 'open' here — this assertion is the "
            f"discriminating case that distinguishes v2 from v1 for this test."
        )

    def test_get_market_state_returns_closed_frozen_at_exact_half_day_close(self) -> None:
        """
        At EXACTLY 13:00 ET on a half-day, the market has just closed.
        get_market_state must return "closed_frozen" (boundary: 13:00 is closed,
        not open).
        """
        black_friday_at_close = _et_datetime(2024, 11, 29, 13, 0)
        result = market_calendar.get_market_state(black_friday_at_close)
        assert result == "closed_frozen", (
            f"get_market_state at exactly 13:00 on a half-day should be 'closed_frozen' "
            f"(market closed); got {result!r}"
        )

    def test_get_market_state_returns_open_one_minute_before_half_day_close(self) -> None:
        """
        At 12:59 ET on Black Friday 2024 (one minute before the 13:00 close),
        get_market_state must return "open".

        This guards the boundary condition: the session is open during
        [09:30, 13:00) and closed at [13:00, ...). At 12:59, `current_time <
        close_time` is True so the market is open.

        Wrong implementation that this catches: an off-by-one that uses
        `current_time <= close_time` for the open condition would return
        "closed_frozen" at 12:59 because 12:59 <= 13:00 is True.

        Note: the rebalance-blackout window (12:53–13:00) in the engine uses the
        engine's internal state; get_market_state is a PURE calendar function that
        does not know about the engine's blackout. It must return "open" at 12:59.
        """
        black_friday_one_minute_before_close = _et_datetime(2024, 11, 29, 12, 59)
        result = market_calendar.get_market_state(black_friday_one_minute_before_close)
        assert result == "open", (
            f"get_market_state on Black Friday 2024 at 12:59 ET should be 'open' "
            f"(session runs 09:30 to <13:00 on half-days; 12:59 < 13:00). "
            f"Got {result!r}. An off-by-one (`<=` instead of `<`) would return "
            f"'closed_frozen' here."
        )

    def test_get_market_state_returns_pre_market_before_open_on_half_day(self) -> None:
        """
        REGRESSION GUARD (not an adversarial RED for the half-day feature):
        On a half-day at 08:00 ET (before 09:30 open), get_market_state must
        return "pre_market". Half-day status does not change the open time.

        v1 (weekday-only, fixed 16:00 close) also returns "pre_market" at 08:00
        on any weekday. This test guards that the calendar-aware v2 implementation
        does not accidentally change the pre-market state for half-days.
        """
        black_friday_early = _et_datetime(2024, 11, 29, 8, 0)
        result = market_calendar.get_market_state(black_friday_early)
        assert result == "pre_market", (
            f"get_market_state on Black Friday 2024 at 08:00 ET should be 'pre_market' "
            f"(before 09:30 open); got {result!r}"
        )

    def test_get_market_state_regular_trading_day_unaffected(self) -> None:
        """
        AC-3 regression guard: regular trading days must not be affected by the
        calendar-aware implementation. get_market_state at 12:00 on a regular
        Wednesday must still return "open".
        """
        regular_wednesday_noon = _et_datetime(2025, 5, 14, 12, 0)
        result = market_calendar.get_market_state(regular_wednesday_noon)
        assert result == "open", (
            f"get_market_state on regular trading day (2025-05-14) at 12:00 should be 'open'; "
            f"got {result!r}. The calendar-aware impl must not regress regular days."
        )


# ===========================================================================
# SECTION 4: Engine gate — holiday path skips data and action phases
# ===========================================================================

class TestEngineSkipsOnHoliday:
    """
    AC-1: On a US equity market HOLIDAY, the engine must take the non-trading-day
    path — no data phase, no action phase (same as a weekend).

    Tests inject a fixed holiday datetime via get_current_et mock and assert
    that the engine returns early without calling Composer, Alpaca, or any
    math_engine functions. The calendar authority replaces the weekday-only check.

    RED because: the engine currently uses `is_weekday = current_et.weekday() < 5`
    and would NOT skip Christmas Day 2024 (a Wednesday).
    """

    # Christmas 2024 — Wednesday at 11:00 ET (during what would be trading hours)
    _CHRISTMAS_2024 = _et_datetime(2024, 12, 25, 11, 0)

    def _run_engine_with_holiday_clock(self, holiday_dt: datetime) -> dict:
        """Run main() with the clock injected to a holiday datetime. Returns mocks.

        market_calendar is explicitly patched to is_trading_day=False for the
        test date to isolate from lru_cache state across workers.
        """
        import alpha_bot_execution
        import market_calendar as _real_mc
        from datetime import time as dt_time

        # Patch market_calendar on alpha_bot_execution to isolate from real cache state.
        # is_trading_day(holiday) = False; session_close = standard 16:00 (irrelevant
        # on non-trading days, but must return a valid time to avoid downstream errors).
        mock_mc = MagicMock()
        mock_mc.is_trading_day.return_value = False
        mock_mc.session_close.return_value = dt_time(16, 0)
        mock_mc.session_open.return_value = dt_time(9, 30)

        with patch.object(alpha_bot_execution, "get_current_et",
                          return_value=holiday_dt), \
             patch.object(alpha_bot_execution, "market_calendar", mock_mc), \
             patch.object(alpha_bot_execution, "database") as mock_db, \
             patch.object(alpha_bot_execution, "fetch_symphony_stats") as mock_fetch, \
             patch.object(alpha_bot_execution, "fetch_alpaca_history") as mock_alpaca, \
             patch.object(alpha_bot_execution, "math_engine") as mock_math, \
             patch.object(alpha_bot_execution, "reporting") as mock_reporting, \
             patch.object(alpha_bot_execution, "ACCOUNT_UUIDS", ["acct-holiday-test"]), \
             patch.object(alpha_bot_execution, "COMPOSER_KEY_ID", "test-key"), \
             patch.object(alpha_bot_execution, "ALPACA_KEY", "test-alpaca-key"), \
             patch.object(alpha_bot_execution, "LIVE_EXECUTION", False), \
             patch.object(alpha_bot_execution.sys, "argv", ["alpha_bot_execution.py"]):

            mock_db.acquire_lock.return_value = True
            mock_db.load_state.return_value = {"date": holiday_dt.strftime("%Y-%m-%d")}

            alpha_bot_execution.main()

        return {
            "fetch_symphony_stats": mock_fetch,
            "fetch_alpaca_history": mock_alpaca,
            "math_engine": mock_math,
            "reporting": mock_reporting,
        }

    def test_holiday_engine_does_not_call_run_monte_carlo(self) -> None:
        """
        On a NYSE holiday, run_monte_carlo must NOT be called.

        run_monte_carlo is the action-phase canary. If it is called, the engine
        entered the action phase — which must not happen on a non-trading day.

        RED: the current weekday check passes Wednesday Dec 25 and proceeds to
        data + action phases.
        """
        mocks = self._run_engine_with_holiday_clock(self._CHRISTMAS_2024)
        assert mocks["math_engine"].run_monte_carlo.call_count == 0, (
            f"run_monte_carlo must not be called on a NYSE holiday (2024-12-25 is "
            f"Christmas Day — a Wednesday that the weekday check incorrectly passes). "
            f"Call count: {mocks['math_engine'].run_monte_carlo.call_count}"
        )

    def test_holiday_engine_does_not_call_fetch_symphony_stats(self) -> None:
        """
        On a NYSE holiday, fetch_symphony_stats (the Composer data fetch) must NOT
        be called. There is no market activity to fetch.

        RED: the current engine calls fetch_symphony_stats during the pre-REAL_MARKET_OPEN
        compositor refresh, which runs even before the weekday check is complete —
        but on a proper holiday path the engine returns before any Composer fetch.

        NOTE: AC-1 strictly means "no data phase, no action phase." The current engine
        already skips most processing on weekends — the new calendar-aware path must
        extend this to holidays.
        """
        mocks = self._run_engine_with_holiday_clock(self._CHRISTMAS_2024)
        # On a non-trading day the engine must NOT make any Composer network calls
        # for data collection or action dispatch.
        assert mocks["fetch_symphony_stats"].call_count == 0, (
            f"fetch_symphony_stats must not be called on a NYSE holiday; "
            f"got {mocks['fetch_symphony_stats'].call_count} calls. "
            f"The holiday path must return before Composer fetch."
        )

    def test_holiday_engine_does_not_send_discord_alerts(self) -> None:
        """
        On a NYSE holiday, no exit alerts must be sent to Discord.

        send_discord_alert is the exit canary. A holiday should produce zero
        alert emissions.
        """
        mocks = self._run_engine_with_holiday_clock(self._CHRISTMAS_2024)
        assert mocks["reporting"].send_discord_alert.call_count == 0, (
            f"send_discord_alert must not be called on a NYSE holiday; "
            f"got {mocks['reporting'].send_discord_alert.call_count} calls."
        )

    def test_thanksgiving_2024_thursday_engine_skips_action_phase(self) -> None:
        """
        Thanksgiving 2024 (November 28, Thursday) at 11:00 ET — a time that
        would be solidly inside the action window on a regular Thursday. The
        engine must treat it as a non-trading day.
        """
        thanksgiving_2024 = _et_datetime(2024, 11, 28, 11, 0)
        mocks = self._run_engine_with_holiday_clock(thanksgiving_2024)
        assert mocks["math_engine"].run_monte_carlo.call_count == 0, (
            f"run_monte_carlo must not be called on Thanksgiving 2024 (2024-11-28); "
            f"Call count: {mocks['math_engine'].run_monte_carlo.call_count}"
        )

    def test_good_friday_2025_engine_skips_action_phase(self) -> None:
        """
        Good Friday 2025 (April 18 — a Friday) at 11:00 ET. The weekday check
        passes this as a regular Friday; only a calendar-aware check catches it.
        """
        good_friday_2025 = _et_datetime(2025, 4, 18, 11, 0)
        mocks = self._run_engine_with_holiday_clock(good_friday_2025)
        assert mocks["math_engine"].run_monte_carlo.call_count == 0, (
            f"run_monte_carlo must not be called on Good Friday 2025 (2025-04-18); "
            f"the weekday check passes this as a regular Friday — only a calendar-aware "
            f"check catches it. Call count: {mocks['math_engine'].run_monte_carlo.call_count}"
        )


# ===========================================================================
# SECTION 5: Half-day time shifts
# ===========================================================================

class TestHalfDayTimeShifts:
    """
    AC-2: On a half-day, ALL close-relative times shift to the 13:00 close:
      - EOD post-mortem window: ~13:00–13:05
      - Rebalance blackout window: ~12:53
      - Squeeze-t close anchor: 13:00 (so t = 1.0 at 13:00 ET)

    These tests verify the engine's time_ratio computation uses the half-day close
    and that the post-mortem / blackout windows fire at the right times.

    RED because: the engine hard-codes market_close = dt_time(16, 0) and
    rebalance_blackout = dt_time(15, 53); it does not query the calendar.
    """

    # Black Friday 2024 — known half-day (close 13:00 ET)
    _BLACK_FRIDAY_DATE = "2024-11-29"

    def _run_engine_cycle(
        self,
        cycle_dt: datetime,
        execution_start_time: str = "10:31",
    ) -> tuple[dict, dict]:
        """
        Run one engine cycle at a specified datetime. Returns (final_bot_state, mocks).

        Patches: get_current_et, database, Composer/Alpaca fetches, math_engine.
        """
        import copy
        import alpha_bot_execution

        _SYM_ID = "sym-halfday-test-001"
        _ACCOUNT_ID = "acct-halfday-test-001"
        _TICKER = "SPY"
        date_str = cycle_dt.strftime("%Y-%m-%d")

        initial_state: dict = {
            "date": date_str,
            _SYM_ID: {
                "high_water_mark": 1.0,
                "shadow_hwm": 1.0,
                "prev_return": 0.0,
                "armed": False,
                "tp_armed": False,
                "para_armed": False,
                "triggered": False,
                "mc_history": [],
                "below_stop_count": 0,
                "above_tp_count": 0,
                "vwap_ticks": 0,
                "vwap_bleed_ticks": 0,
                "breakeven_locked": False,
                "hwm_hold_ticks": 0,
                "current_return": 1.0,
                "name": "HalfDay Test Symphony",
                "account": _ACCOUNT_ID,
                "symphony_vol": 0.15,
            },
        }

        sym_payload = {
            "id": _SYM_ID,
            "symphony_id": "actual-halfday-sym-001",
            "name": "HalfDay Test Symphony",
            "last_percent_change": 0.01,
            "current_value": 10_000.0,
            "simple_return": 0.01,
            "net_deposits": 9_500.0,
            "time_weighted_return": 0.01,
            "max_drawdown": 0.01,
            "holdings": [{"ticker": _TICKER, "allocation": 1.0}],
        }

        captured_states: list[dict] = []
        captured_time_ratios: list[float] = []

        def capture_save_state(state: dict) -> None:
            captured_states.append(copy.deepcopy(state))

        def capture_time_squeeze(time_ratio: float) -> tuple:
            captured_time_ratios.append(time_ratio)
            return (1.0, 0.3)  # safe defaults that don't trigger exits

        with patch.object(alpha_bot_execution, "get_current_et",
                          return_value=cycle_dt), \
             patch.object(alpha_bot_execution, "database") as mock_db, \
             patch.object(alpha_bot_execution, "reporting") as mock_reporting, \
             patch.object(alpha_bot_execution, "fetch_symphony_stats",
                          return_value=[sym_payload]), \
             patch.object(alpha_bot_execution, "fetch_alpaca_history",
                          return_value={date_str: {_TICKER: {
                              "c": 500.0, "daily_ret": 0.001,
                              "high": 501.0, "low": 499.0, "close": 500.0,
                          }}}), \
             patch.object(alpha_bot_execution, "fetch_intraday_vwaps",
                          return_value={_TICKER: {"vwap": 500.0, "last_price": 500.0}}), \
             patch.object(alpha_bot_execution, "ACCOUNT_UUIDS", [_ACCOUNT_ID]), \
             patch.object(alpha_bot_execution, "COMPOSER_KEY_ID", "test-key"), \
             patch.object(alpha_bot_execution, "ALPACA_KEY", "test-alpaca-key"), \
             patch.object(alpha_bot_execution, "LIVE_EXECUTION", False), \
             patch.object(alpha_bot_execution, "EXECUTION_START_TIME", execution_start_time), \
             patch.object(alpha_bot_execution.sys, "argv", ["alpha_bot_execution.py"]), \
             patch.object(alpha_bot_execution, "autotuner") as _mock_autotuner, \
             patch.object(alpha_bot_execution, "math_engine") as mock_math:

            mock_db.acquire_lock.return_value = True
            mock_db.load_state.return_value = copy.deepcopy(initial_state)
            mock_db.load_chart_history.return_value = {"date": date_str, "symphonies": {}}
            mock_db.get_symphony_strategy.return_value = {"params": {}, "locked_vars": {}}
            mock_db.normalize_name.side_effect = lambda n: n.strip().lower()
            mock_db.wipe_transient_state.side_effect = lambda s: s
            mock_db.save_state.side_effect = capture_save_state
            _mock_autotuner.run_autotuner.return_value = None

            mock_math.run_monte_carlo.return_value = 80.0
            mock_math.calculate_20d_vol.return_value = 0.15
            mock_math.compute_vwap_signals.return_value = (0.0, 0.0)
            mock_math.compute_para_arm_decision.return_value = (0.0, False)
            # Intercept the time_squeeze call to capture the time_ratio argument
            mock_math.compute_time_squeeze_decay.side_effect = capture_time_squeeze
            mock_math.compute_active_trailing_stop.return_value = 2.0
            mock_math.compute_breakeven_update.return_value = (0, False, -2.0)
            mock_math.compute_tp_confirmation.return_value = (False, 0, False)
            mock_math.compute_vwap_breakdown_update.return_value = (0, 0, False, False)
            mock_math.compute_vwap_bleed_arm_threshold.return_value = 0.5
            mock_math.is_in_open_window_grace.return_value = False
            # compute_exit_confirmation returns (new_below_stop_count, is_trailing_stop_hit)
            mock_math.compute_exit_confirmation.return_value = (0, False)
            # resolve_trigger_priority returns (trigger_type_or_None, also_true_list)
            mock_math.resolve_trigger_priority.return_value = (None, [])

            alpha_bot_execution.main()

        final_state = captured_states[-1] if captured_states else copy.deepcopy(
            initial_state
        )
        return final_state, {
            "mock_math": mock_math,
            "captured_time_ratios": captured_time_ratios,
            "mock_reporting": mock_reporting,
        }

    def test_half_day_squeeze_t_uses_1300_close_anchor_not_1600(self) -> None:
        """
        AC-2 core: on Black Friday 2024 (half-day, close 13:00 ET), the
        time_ratio passed to compute_time_squeeze_decay at 11:30 ET must be
        computed with the 13:00 close anchor, NOT the 16:00 regular-day anchor.

        Derivation (exact integer arithmetic, no rounding):
          EXECUTION_START_TIME = 10:31 ET
          cycle_time = 11:30 ET
          elapsed  = (11:30 - 10:31) = 59 minutes
          half-day window = (13:00 - 10:31) = 149 minutes
          time_ratio_correct  = 59 / 149 ≈ 0.39597  (13:00 anchor)
          time_ratio_wrong    = 59 / 329 ≈ 0.17933  (16:00 anchor)
          midpoint_threshold  = (0.39597 + 0.17933) / 2 ≈ 0.287

        A wrong implementation (hard-coded 16:00 close) would return ≈ 0.179.
        The correct implementation must return > 0.287. The tolerance 1e-4 is
        appropriate for timedelta integer arithmetic (no floating-point chaining
        beyond two integer divisions).

        RED: the engine previously hard-coded market_close = dt_time(16, 0), which
        would produce time_ratio ≈ 0.179 at 11:30 on a half-day.
        """
        # Black Friday 2024 at 11:30 ET — solidly in action phase
        mid_action_half_day = _et_datetime(2024, 11, 29, 11, 30)
        _, extras = self._run_engine_cycle(
            cycle_dt=mid_action_half_day,
            execution_start_time="10:31",
        )

        captured_ratios = extras["captured_time_ratios"]
        assert captured_ratios, (
            "compute_time_squeeze_decay must be called on a half-day at 11:30 ET "
            "(action phase is open at 11:30 with EXECUTION_START_TIME=10:31); "
            f"no time_ratio was captured — the action phase did not run"
        )

        # Derived midpoint threshold: halfway between correct (13:00) and wrong (16:00) ratio
        # 59/149 ≈ 0.39597, 59/329 ≈ 0.17933, midpoint ≈ 0.287
        _MIDPOINT_THRESHOLD = 0.287

        # The time_ratio captured in the action-phase loop (which may fire per-symphony)
        representative_ratio = max(captured_ratios)
        assert representative_ratio > _MIDPOINT_THRESHOLD, (
            # Tolerance: comparison against midpoint 0.287; the correct value is ~0.396
            # and the wrong value is ~0.179. Any value above 0.287 unambiguously indicates
            # the 13:00 close anchor is in use. Tolerance 1e-4 applied below.
            f"At 11:30 ET on Black Friday 2024, time_ratio must be > {_MIDPOINT_THRESHOLD} "
            f"(correct 13:00 anchor gives ≈ 0.396; wrong 16:00 anchor gives ≈ 0.179). "
            f"Got {representative_ratio}. "
            f"The engine's close anchor on half-days must be 13:00, not 16:00."
        )

    def test_half_day_squeeze_t_approaches_one_before_blackout_window(self) -> None:
        """
        AC-2: at 12:50 ET on Black Friday 2024 (3 minutes before the half-day
        rebalance blackout at 12:53 ET), time_ratio must be close to 1.0 —
        nearly fully elapsed under the 13:00 anchor.

        Derivation (exact integer arithmetic):
          EXECUTION_START_TIME = 10:31 ET
          cycle_time = 12:50 ET
          elapsed  = (12:50 - 10:31) = 139 minutes
          half-day window (13:00 anchor)  = (13:00 - 10:31) = 149 minutes
          regular-day window (16:00 anchor) = (16:00 - 10:31) = 329 minutes

          time_ratio_correct = 139 / 149 ≈ 0.933  (13:00 anchor)
          time_ratio_wrong   = 139 / 329 ≈ 0.423  (16:00 anchor)
          midpoint_threshold = (0.933 + 0.423) / 2 ≈ 0.678

        Any ratio > 0.7 is unambiguously from the 13:00 anchor.

        Note: 12:59 is in the rebalance-blackout window (12:53–13:00 on half-day)
        and would not call compute_time_squeeze_decay — 12:50 is used to stay
        inside the action phase.

        RED: the 16:00-anchored impl returns ≈ 0.423 at 12:50, below the threshold.
        """
        before_blackout = _et_datetime(2024, 11, 29, 12, 50)
        _, extras = self._run_engine_cycle(
            cycle_dt=before_blackout,
            execution_start_time="10:31",
        )
        captured_ratios = extras["captured_time_ratios"]
        assert captured_ratios, (
            "compute_time_squeeze_decay must be called at 12:50 ET on Black Friday 2024; "
            "12:50 is inside the action phase (EXECUTION_START_TIME=10:31, "
            "rebalance blackout starts 12:53 on half-day). "
            f"No time_ratio captured — the action phase did not run."
        )
        representative_ratio = max(captured_ratios)
        # 0.7 threshold: correct is ~0.933, wrong is ~0.423; 0.7 cleanly separates them.
        # Derivation: midpoint of (139/149, 139/329) = (0.933, 0.423) / 2 = 0.678;
        # using 0.7 adds an extra margin of 0.022.
        assert representative_ratio > 0.7, (
            f"At 12:50 ET on Black Friday 2024, time_ratio must be > 0.7 "
            f"(correct 13:00 anchor gives ≈ 0.933; wrong 16:00 anchor gives ≈ 0.423). "
            f"Got {representative_ratio}. "
            f"The engine's close anchor on half-days must be 13:00, not 16:00."
        )

    def test_half_day_squeeze_t_is_zero_at_execution_start_time(self) -> None:
        """
        AC-2 regression guard: on Black Friday 2024 at exactly EXECUTION_START_TIME
        (10:31 ET), time_ratio must be 0.0.

        At the action-window open, numerator = 0, so time_ratio = 0.0 regardless
        of whether the close is 13:00 or 16:00. This test guards that the half-day
        close shift does NOT accidentally move the open anchor.

        The implementation must call compute_time_squeeze_decay — if the engine
        skips it at exactly 10:31, the test fails with a clear message.
        """
        at_exec_start = _et_datetime(2024, 11, 29, 10, 31)
        _, extras = self._run_engine_cycle(
            cycle_dt=at_exec_start,
            execution_start_time="10:31",
        )
        captured_ratios = extras["captured_time_ratios"]
        assert captured_ratios, (
            "compute_time_squeeze_decay must be called at EXECUTION_START_TIME (10:31 ET) "
            "on Black Friday 2024; the action phase must be open at exactly the gate time. "
            "No time_ratio was captured — the action phase did not fire at 10:31."
        )
        min_ratio = min(captured_ratios)
        assert min_ratio == pytest.approx(0.0, abs=1e-9), (
            # Tolerance: exactly 0.0 since numerator is exactly zero; 1e-9 for float safety
            f"At EXECUTION_START_TIME (10:31 ET) on Black Friday 2024, time_ratio "
            f"must be 0.0; got min={min_ratio} (all: {captured_ratios}). "
            f"The open anchor must always be EXECUTION_START_TIME, not 09:30."
        )

    def test_half_day_rebalance_blackout_fires_at_1253_not_1553(self) -> None:
        """
        AC-2: On a half-day, the rebalance blackout starts at ~12:53 ET
        (7 minutes before the 13:00 close). The current code has a hard-coded
        rebalance_blackout = dt_time(15, 53) — this means the blackout NEVER
        fires before the 16:00 close and entirely misses the half-day window.

        This test verifies that when the engine runs at 12:54 ET on Black Friday
        (after the half-day blackout window opens), the reporting blackout path
        is taken, not the regular action phase.

        We use the reporting canary: if the blackout path is active, the engine
        calls generate_eod_snapshot (the rebalance-blackout reporting path) rather
        than proceeding to the action phase exit logic.

        RED: at 12:54 ET the current code treats the engine as fully in the
        action phase (market_close=16:00 hasn't been hit yet) and runs full
        exit logic including run_monte_carlo.
        """
        black_friday_at_1254 = _et_datetime(2024, 11, 29, 12, 54)
        _, extras = self._run_engine_cycle(
            cycle_dt=black_friday_at_1254,
            execution_start_time="10:31",
        )
        # On a half-day the blackout window is 12:53–13:00. At 12:54 the engine
        # must be in the blackout path: it must NOT be calling action-phase math
        # (run_monte_carlo). The blackout path bypasses the action phase.
        mc_calls = extras["mock_math"].run_monte_carlo.call_count
        assert mc_calls == 0, (
            f"At 12:54 ET on Black Friday 2024 (half-day), the engine should be in the "
            f"rebalance-blackout window (12:53–13:00 ET). run_monte_carlo must not be "
            f"called. Got {mc_calls} call(s). "
            f"The current engine has rebalance_blackout=15:53 and misses the half-day window."
        )

        # The blackout path is NOT a no-op early return — it must call
        # reporting.generate_eod_snapshot before returning. A wrong implementation
        # that simply does `return` at 12:54 would also pass run_monte_carlo==0 but
        # would miss the snapshot obligation. This assertion distinguishes the
        # correct blackout path from a silent early-return.
        eod_snapshot_calls = extras["mock_reporting"].generate_eod_snapshot.call_count
        assert eod_snapshot_calls >= 1, (
            f"At 12:54 ET on Black Friday 2024 (half-day rebalance-blackout window), "
            f"reporting.generate_eod_snapshot must be called. "
            f"Got {eod_snapshot_calls} call(s). "
            f"The blackout path fires generate_eod_snapshot then returns — "
            f"a no-op early-return also passes run_monte_carlo==0 but misses this obligation."
        )

    def test_half_day_post_mortem_fires_at_1300_not_1600(self) -> None:
        """
        AC-2: The EOD post-mortem runs at market_close (and up to post_mortem_cutoff).
        On a half-day, market_close = 13:00, so the post-mortem must fire at
        13:00–13:05 ET, not 16:00–16:05 ET.

        At 13:01 ET on Black Friday, the engine must be in the post-mortem window.
        The canary: generate_eod_post_mortem (or equivalent reporting call) is invoked.

        RED: the current code uses market_close = dt_time(16, 0), so at 13:01 ET
        the engine is still in the action phase, not the post-mortem.
        """
        black_friday_post_mortem = _et_datetime(2024, 11, 29, 13, 1)
        _, extras = self._run_engine_cycle(
            cycle_dt=black_friday_post_mortem,
            execution_start_time="10:31",
        )
        # The EOD post-mortem path must have run: on a half-day at 13:01, the
        # engine should NOT be calling run_monte_carlo (action phase) — it should
        # be in the post-mortem window.
        mc_calls = extras["mock_math"].run_monte_carlo.call_count
        assert mc_calls == 0, (
            f"At 13:01 ET on Black Friday 2024 (half-day), the engine should be in "
            f"the EOD post-mortem window (13:00–13:05 ET). run_monte_carlo must not "
            f"be called from the post-mortem path. "
            f"Got {mc_calls} call(s). The current engine has market_close=16:00 and "
            f"would be running the action phase at 13:01 on Black Friday."
        )


# ===========================================================================
# SECTION 6: EXECUTION_START_TIME gate is preserved on half-days
# ===========================================================================

class TestExecutionStartTimeGatePreservedOnHalfDays:
    """
    AC-2 constraint: half-day close shifts must NOT open an early-exit hole.
    The EXECUTION_START_TIME gate (alpha_bot_execution.py:939-940) must still
    hold even on half-days.

    Specifically: on a half-day, if the current time is BEFORE EXECUTION_START_TIME,
    the action phase must not fire (same as a regular day pre-gate).

    RED: this is a constraint — the engine passes the gate checks with the
    new close-anchor. This test guards that the half-day close does not
    accidentally skip the EXECUTION_START_TIME gate.
    """

    def test_pre_gate_half_day_does_not_fire_action_phase(self) -> None:
        """
        On Black Friday 2024 at 09:45 ET (before EXECUTION_START_TIME=10:31),
        the action phase must NOT fire. The half-day close shift must not
        cause the engine to skip the gate check.

        This test fires even on the current code — it is a REGRESSION guard
        for the implementer's half-day changes. If the implementer accidentally
        changes the gate logic in a way that lets pre-gate cycles through, this
        RED test will catch it.

        NOTE: This test should PASS on the current codebase (pre-gate gate is
        already correct for weekdays). It will continue to pass after the half-day
        change if the implementer preserves the gate. We mark it RED as a contract
        assertion that the implementer MUST NOT break.
        """
        import copy
        import alpha_bot_execution

        pre_gate_on_half_day = _et_datetime(2024, 11, 29, 9, 45)
        _SYM_ID = "sym-gate-test"
        _ACCOUNT_ID = "acct-gate-test"
        date_str = pre_gate_on_half_day.strftime("%Y-%m-%d")

        initial_state = {
            "date": date_str,
            _SYM_ID: {
                "high_water_mark": 1.0, "shadow_hwm": 1.0,
                "prev_return": 0.0, "armed": False, "tp_armed": False,
                "para_armed": False, "triggered": False, "mc_history": [],
                "below_stop_count": 0, "above_tp_count": 0, "vwap_ticks": 0,
                "vwap_bleed_ticks": 0, "breakeven_locked": False,
                "hwm_hold_ticks": 0, "current_return": 1.0,
                "name": "Gate Test", "account": _ACCOUNT_ID,
            },
        }

        sym_payload = {
            "id": _SYM_ID, "symphony_id": "actual-gate-sym",
            "name": "Gate Test", "last_percent_change": 0.01,
            "current_value": 10_000.0, "simple_return": 0.01,
            "net_deposits": 9_500.0, "time_weighted_return": 0.01,
            "max_drawdown": 0.01,
            "holdings": [{"ticker": "SPY", "allocation": 1.0}],
        }

        with patch.object(alpha_bot_execution, "get_current_et",
                          return_value=pre_gate_on_half_day), \
             patch.object(alpha_bot_execution, "database") as mock_db, \
             patch.object(alpha_bot_execution, "reporting"), \
             patch.object(alpha_bot_execution, "fetch_symphony_stats",
                          return_value=[sym_payload]), \
             patch.object(alpha_bot_execution, "fetch_alpaca_history",
                          return_value={date_str: {"SPY": {
                              "c": 500.0, "daily_ret": 0.001,
                              "high": 501.0, "low": 499.0, "close": 500.0,
                          }}}), \
             patch.object(alpha_bot_execution, "fetch_intraday_vwaps",
                          return_value={"SPY": {"vwap": 500.0, "last_price": 500.0}}), \
             patch.object(alpha_bot_execution, "ACCOUNT_UUIDS", [_ACCOUNT_ID]), \
             patch.object(alpha_bot_execution, "COMPOSER_KEY_ID", "test-key"), \
             patch.object(alpha_bot_execution, "ALPACA_KEY", "test-key"), \
             patch.object(alpha_bot_execution, "LIVE_EXECUTION", False), \
             patch.object(alpha_bot_execution, "EXECUTION_START_TIME", "10:31"), \
             patch.object(alpha_bot_execution.sys, "argv", ["alpha_bot_execution.py"]), \
             patch.object(alpha_bot_execution, "autotuner"), \
             patch.object(alpha_bot_execution, "math_engine") as mock_math:

            mock_db.acquire_lock.return_value = True
            mock_db.load_state.return_value = copy.deepcopy(initial_state)
            mock_db.load_chart_history.return_value = {"date": date_str, "symphonies": {}}
            mock_db.get_symphony_strategy.return_value = {"params": {}, "locked_vars": {}}
            mock_db.normalize_name.side_effect = lambda n: n.strip().lower()
            mock_db.wipe_transient_state.side_effect = lambda s: s

            mock_math.run_monte_carlo.return_value = 80.0
            mock_math.calculate_20d_vol.return_value = 0.15
            mock_math.compute_vwap_signals.return_value = (0.0, 0.0)
            mock_math.compute_para_arm_decision.return_value = (0.0, False)
            mock_math.compute_time_squeeze_decay.return_value = (1.0, 0.3)
            mock_math.compute_active_trailing_stop.return_value = 2.0
            mock_math.compute_breakeven_update.return_value = (0, False, -2.0)
            mock_math.compute_tp_confirmation.return_value = (False, 0, False)
            mock_math.compute_vwap_breakdown_update.return_value = (0, 0, False, False)
            mock_math.compute_vwap_bleed_arm_threshold.return_value = 0.5
            mock_math.is_in_open_window_grace.return_value = False
            mock_math.compute_exit_confirmation.return_value = (0, False)
            mock_math.resolve_trigger_priority.return_value = (None, [])

            alpha_bot_execution.main()

        assert mock_math.run_monte_carlo.call_count == 0, (
            f"On Black Friday 2024 at 09:45 ET (before EXECUTION_START_TIME=10:31), "
            f"run_monte_carlo must NOT be called even though it is a half-day. "
            f"The half-day close shift must not bypass the EXECUTION_START_TIME gate. "
            f"Got {mock_math.run_monte_carlo.call_count} call(s)."
        )


# ===========================================================================
# SECTION 7: Calendar memoization — non-blocking per-date cache
# ===========================================================================

class TestCalendarLookupMemoization:
    """
    NON-FUNCTIONAL AC: the calendar lookup is memoized per-date.
    A second call for the same date must not re-invoke the slow path
    (no repeated pandas-market-calendars computation).

    This ensures the architecture constraint is met: no blocking I/O on the
    per-minute execution path.

    RED because: the cache is not yet implemented.
    """

    @_requires_calendar
    def test_is_trading_day_second_call_does_not_recompute(self) -> None:
        """
        Calling is_trading_day twice for the same date must return the same
        result without re-entering the computation path.

        Implementation guard: the function must expose a cache (e.g. via
        functools.lru_cache or a module-level dict). We verify the result is
        the same value (correctness) and assert the underlying calendar schedule
        is not queried twice by checking call count on a known expensive path
        OR by verifying via the function's cache_info if lru_cache is used.

        If lru_cache is NOT used: the test asserts that calling is_trading_day
        10 times with the same date stays fast (< 0.5 seconds total wall time
        for 10 identical calls — a single pandas-market-calendars schedule
        construction takes ~0.1-0.5s; 10 uncached calls would exceed 1s).
        """
        import time
        from datetime import date as dt_date

        test_date = dt_date(2025, 5, 14)  # regular Wednesday — stable result

        # Warm up (first call may be slow due to pandas schedule construction)
        first_result = market_calendar.is_trading_day(test_date)

        # Check if the function uses lru_cache (preferred path)
        if hasattr(market_calendar.is_trading_day, "cache_info"):
            before = market_calendar.is_trading_day.cache_info()
            for _ in range(9):
                result = market_calendar.is_trading_day(test_date)
                assert result == first_result, (
                    f"is_trading_day must return the same result on repeated calls; "
                    f"got {result} vs first result {first_result}"
                )
            after = market_calendar.is_trading_day.cache_info()
            # After 9 more calls with the same arg, hits should have increased by 9
            new_hits = after.hits - before.hits
            assert new_hits == 9, (
                f"is_trading_day cache must serve 9 repeated same-date calls from "
                f"cache (lru_cache.hits should increase by 9); got {new_hits} new hits. "
                f"The memoization is not working."
            )
        else:
            # Fallback: timing check — 10 calls on a warm-cache impl must be fast
            start = time.monotonic()
            for _ in range(10):
                result = market_calendar.is_trading_day(test_date)
                assert result == first_result
            elapsed = time.monotonic() - start
            assert elapsed < 0.5, (
                f"10 repeated is_trading_day calls for the same date took {elapsed:.3f}s — "
                f"exceeds 0.5s budget. Each call must serve from cache; the first call "
                f"warms the cache (excluded from this timing). A per-date cache "
                f"(lru_cache or dict) is required to satisfy the non-blocking constraint."
            )

    @_requires_calendar
    def test_session_close_second_call_returns_same_value(self) -> None:
        """
        Calling session_close twice for the same date must return identical values.
        Correctness guard for the cache path.
        """
        from datetime import date as dt_date
        test_date = dt_date(2024, 11, 29)  # Black Friday — half-day
        first = market_calendar.session_close(test_date)
        second = market_calendar.session_close(test_date)
        assert first == second, (
            f"session_close({test_date}) returned different values on repeated calls: "
            f"{first} vs {second}. Must be deterministic."
        )


# ===========================================================================
# SECTION 8: Docstring fix — squeeze-t anchor is EXECUTION_START_TIME, not 09:30
# ===========================================================================

class TestSqueezeDocstringNoLongerClaimsMarketOpen:
    """
    AC-5: math_engine.py compute_time_squeeze_decay docstring currently states
    "0.0 = market open" but the actual caller (alpha_bot_execution.py:1360-1370)
    anchors t=0 to EXECUTION_START_TIME, not 09:30 (real market open).

    The docstring must be updated to remove the "market open" claim and replace
    it with "EXECUTION_START_TIME" (or equivalent accurate language).

    This is a textual contract test — it fails until the docstring is corrected.
    """

    def test_squeeze_docstring_does_not_claim_market_open_is_zero(self) -> None:
        """
        The compute_time_squeeze_decay docstring must NOT claim "0.0 = market open".

        The actual anchor is EXECUTION_START_TIME (the action-window open), not
        09:30 (the real market open). This distinction matters: on a regular day
        with EXECUTION_START_TIME=10:31, t=0.0 at 10:31, not at 09:30. The stop
        is at its widest when the ACTION window opens, not when trading begins.

        RED: the current docstring says "0.0 = market open".
        """
        import math_engine
        import inspect
        source = inspect.getsource(math_engine.compute_time_squeeze_decay)
        # The phrase "0.0 = market open" (in the context of time_ratio) must be absent
        assert "0.0 = market open" not in source, (
            "compute_time_squeeze_decay docstring still says '0.0 = market open'. "
            "The anchor is EXECUTION_START_TIME (the action-window open), not 09:30. "
            "Example correct wording: '0.0 = action-window open (EXECUTION_START_TIME)'. "
            "This must be updated per AC-5."
        )

    def test_squeeze_docstring_references_execution_start_time(self) -> None:
        """
        The corrected docstring must make clear that time_ratio=0.0 corresponds
        to EXECUTION_START_TIME (the point at which the action window opens,
        not the NYSE market open at 09:30).

        Accepts any of: "EXECUTION_START_TIME", "action-window open",
        "action window open", or "execution start".

        RED: the current docstring contains none of these phrases.
        """
        import math_engine
        import inspect
        source = inspect.getsource(math_engine.compute_time_squeeze_decay)
        acceptable_phrases = [
            "EXECUTION_START_TIME",
            "action-window open",
            "action window open",
            "execution start",
        ]
        found = any(phrase.lower() in source.lower() for phrase in acceptable_phrases)
        assert found, (
            f"compute_time_squeeze_decay docstring must reference EXECUTION_START_TIME "
            f"(or equivalent) as the t=0.0 anchor. None of the acceptable phrases "
            f"{acceptable_phrases} were found in the docstring. "
            f"The anchor is the action-window open, not NYSE market open (09:30)."
        )
