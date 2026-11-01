"""While the market is closed the exit-turnover figures stay put: windows count back from the close.

``GET /api/exit-turnover`` counted its 30, 90 and 365 day windows back from the host's wall clock, so
a reload of the Performance page on a Sunday read a different exit count, retained-day figure and
friction drag than the same page a few hours earlier, with no new data. Every other window route
counts back from the session the page's other figures belong to (``_SessionView.cutoff_anchor``).

Real call path: the real ``/api/exit-turnover`` route over a temporary database, the real
``exit_triggers`` writer, the real XNYS calendar and the virtual host clock of
tests/app/_closed_session_world.py. That clock is also installed on ``database`` here, because the
stats function reads its own ``datetime`` and a clock moved only on ``app`` would hide the fault.

Fixture: exit rows at fixed instants around the 30 and 90 day edges counted from the Friday close,
one every two hours for four days either side, so any shift of a window edge by three hours or more
changes a count; the oldest row sits 94 days before the close, so the retained-days figure also
moves with the clock. Synthetic, labelled so: no number in a test is typed, each expectation is
recomputed from the seeded instants.

Contract pinned
* Closed, at later instants on the same data (the same evening, across a UTC date change, across an ET
  date change, a Sunday after the clock change): the response body is identical to the one read just
  after the close. An implementation frozen only while the instants share a date fails.
* The closed figures are the session's own: counts for rows far from any window edge, retained days
  near the oldest row's age at the close, and a drag consistent with the returned figures.
* Open session: windows still count back from now (two reads three hours apart differ and each
  equals the arithmetic from its own instant).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

import database as database_module
from tests.app._closed_session_world import (
    FRIDAY_SESSION,
    ClosedWorld,
    build_history_world,
    utc_of,
)
from tests.app.test_clocked_weekend_to_monday_open import host_datetime_class

SESSION_DAY = FRIDAY_SESSION.session_day
CLOSE = utc_of(SESSION_DAY, "16:00:00")
SYMPHONY = "turnover-closed-symphony"
URL = f"/api/exit-turnover?symphony_id={SYMPHONY}"
WINDOWS = (30, 90, 365)
OLDEST_AGE_DAYS = 94
EDGE_SPAN_HOURS = 96
EDGE_STEP_HOURS = 2

# Closed instants after the close (ET wall clock), each with the reason it is in the list.
SAME_EVENING = ("2026-10-30", "19:30:00")  # 23:30 UTC, same UTC date as the close
ACROSS_UTC_DATE = ("2026-10-30", "20:30:00")  # 00:30 UTC next day
BEFORE_ET_MIDNIGHT = ("2026-10-30", "23:30:00")
ACROSS_ET_DATE = ("2026-10-31", "00:30:00")
SATURDAY_MORNING = ("2026-10-31", "10:00:00")
SUNDAY_AFTER_CLOCK_CHANGE = ("2026-11-01", "12:00:00")  # EST, the clocks went back at 02:00
AFTER_CLOSE = ("2026-10-30", "16:30:00")  # the baseline read

LATER_INSTANTS = [
    SAME_EVENING,
    ACROSS_UTC_DATE,
    BEFORE_ET_MIDNIGHT,
    ACROSS_ET_DATE,
    SATURDAY_MORNING,
    SUNDAY_AFTER_CLOCK_CHANGE,
]


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _write(moments: list[datetime]) -> None:
    for moment in moments:
        row_id = database_module.record_exit_trigger(
            symphony_id=SYMPHONY,
            triggered_reason="Trailing Stop",
            ts_utc=_stamp(moment),
            ts_et=_stamp(moment),
        )
        assert row_id is not None, "the real exit-trigger writer refused a seeded row"


def _edge_rows() -> list[datetime]:
    """Rows every two hours for four days either side of the 30 and 90 day edges from the close,
    and the oldest row, 94 days before the close (the first row of the 90 day cluster)."""
    moments: list[datetime] = []
    for edge_days in (30, 90):
        centre = CLOSE - timedelta(days=edge_days)
        for hours in range(-EDGE_SPAN_HOURS, EDGE_SPAN_HOURS + 1, EDGE_STEP_HOURS):
            moments.append(centre + timedelta(hours=hours))
    assert min(moments) == CLOSE - timedelta(days=OLDEST_AGE_DAYS)
    return sorted(moments)


def _world(monkeypatch, tmp_path) -> ClosedWorld:
    cw = build_history_world(monkeypatch, tmp_path, first_history_day=date(2026, 9, 1))
    # The stats function reads the clock bound in its own module.
    monkeypatch.setattr(database_module, "datetime", host_datetime_class(cw.host))
    return cw


def _read(cw: ClosedWorld, instant: tuple[str, str]) -> dict:
    cw.at(*instant)
    return cw.poll(URL)


def _moved(before: dict, after: dict) -> list[str]:
    out = []
    for window in map(str, WINDOWS):
        if after["windows"][window] != before["windows"][window]:
            out.append(f"{window}d: {before['windows'][window]} -> {after['windows'][window]}")
    if after["est_annual_friction_drag_pct"] != before["est_annual_friction_drag_pct"]:
        out.append(
            "est. annual friction drag: "
            f"{before['est_annual_friction_drag_pct']} -> {after['est_annual_friction_drag_pct']}"
        )
    return out


def _expected(moments: list[datetime], reference: datetime) -> dict:
    """The windows, counted back from `reference`, by arithmetic on the seeded instants."""
    retained = max(0, (reference - min(moments)).days)
    return {
        str(window): {
            "exit_count": sum(1 for m in moments if m >= reference - timedelta(days=window)),
            "coverage_days": min(window, retained),
        }
        for window in WINDOWS
    }


def _drag(windows: dict) -> float:
    import autotuner  # lazy: Optuna stays off the module import, as in the route

    year = windows["365"]
    if year["coverage_days"] <= 0:
        return 0.0
    return year["exit_count"] * (365 / year["coverage_days"]) * autotuner.SIM_EXIT_FRICTION_PCT


class TestClosedFiguresDoNotMoveWithTheClock:
    def test_the_figures_read_after_the_close_hold_at_every_later_closed_instant(
        self, monkeypatch, tmp_path
    ):
        cw = _world(monkeypatch, tmp_path)
        _write(_edge_rows())
        baseline = _read(cw, AFTER_CLOSE)
        assert cw.poll("/api/state")["market_state"] != "open", (
            "fixture sanity: the market is closed"
        )
        assert baseline["windows"]["30"]["exit_count"] > 0, (
            "fixture sanity: the 30 day window has rows"
        )

        failures = []
        for instant in LATER_INSTANTS:
            failures += [f"{instant}: {m}" for m in _moved(baseline, _read(cw, instant))]
        assert not failures, (
            "the exit-turnover figures moved while the market was closed and nothing new was "
            "written; the windows must count back from the session's close, not the host's "
            "clock:\n" + "\n".join(failures)
        )

    def test_the_figures_hold_across_a_utc_date_change(self, monkeypatch, tmp_path):
        cw = _world(monkeypatch, tmp_path)
        _write(_edge_rows())
        before = _read(cw, SAME_EVENING)
        after = _read(cw, ACROSS_UTC_DATE)
        assert (after["windows"], after["est_annual_friction_drag_pct"]) == (
            before["windows"],
            before["est_annual_friction_drag_pct"],
        ), f"one hour across 00:00 UTC moved the figures: {_moved(before, after)}"

    def test_the_figures_hold_across_an_et_date_change(self, monkeypatch, tmp_path):
        cw = _world(monkeypatch, tmp_path)
        _write(_edge_rows())
        before = _read(cw, BEFORE_ET_MIDNIGHT)
        after = _read(cw, ACROSS_ET_DATE)
        assert (after["windows"], after["est_annual_friction_drag_pct"]) == (
            before["windows"],
            before["est_annual_friction_drag_pct"],
        ), f"one hour across midnight ET moved the figures: {_moved(before, after)}"

    def test_a_reload_after_the_clock_change_reads_what_the_evening_read(
        self, monkeypatch, tmp_path
    ):
        cw = _world(monkeypatch, tmp_path)
        _write(_edge_rows())
        evening = _read(cw, SAME_EVENING)
        sunday = _read(cw, SUNDAY_AFTER_CLOCK_CHANGE)
        assert not _moved(evening, sunday), _moved(evening, sunday)


class TestClosedFiguresAreTheSessionsOwn:
    AGES_DAYS = (1, 5, 45, 200)

    def test_counts_retained_days_and_drag_follow_the_rows_counted_back_from_the_close(
        self, monkeypatch, tmp_path
    ):
        cw = _world(monkeypatch, tmp_path)
        moments = [CLOSE - timedelta(days=age, hours=3) for age in self.AGES_DAYS]
        _write(moments)
        for instant in (AFTER_CLOSE, SATURDAY_MORNING):
            body = _read(cw, instant)
            windows = body["windows"]
            for window in WINDOWS:
                assert windows[str(window)]["exit_count"] == sum(
                    1 for age in self.AGES_DAYS if age <= window
                ), f"{instant} {window}d count: {windows[str(window)]}"
            oldest = max(self.AGES_DAYS)
            for window in WINDOWS:
                assert windows[str(window)]["coverage_days"] == pytest.approx(
                    min(window, oldest), abs=1
                ), f"{instant} {window}d retained days: {windows[str(window)]}"
            assert body["est_annual_friction_drag_pct"] == pytest.approx(_drag(windows), rel=1e-9)


class TestAnOpenSessionStillCountsBackFromNow:
    EARLY = ("2026-10-30", "11:00:00")
    LATE = ("2026-10-30", "14:00:00")

    def test_two_reads_three_hours_apart_each_equal_the_arithmetic_from_their_own_instant(
        self, monkeypatch, tmp_path
    ):
        cw = _world(monkeypatch, tmp_path)
        moments = _edge_rows()
        _write(moments)
        reads = {}
        for instant in (self.EARLY, self.LATE):
            body = _read(cw, instant)
            assert cw.poll("/api/state")["market_state"] == "open", "fixture sanity: market open"
            reads[instant] = body
            windows = _expected(moments, cw.host.utc)
            assert body["windows"] == windows, f"{instant}: {body['windows']} != {windows}"
            assert body["est_annual_friction_drag_pct"] == pytest.approx(_drag(windows), rel=1e-9)
        assert _moved(reads[self.EARLY], reads[self.LATE]), (
            "fixture sanity: the open-session reads do not differ, so the test cannot tell a "
            "window frozen at the close from one counted back from now"
        )
