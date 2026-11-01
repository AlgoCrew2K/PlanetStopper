"""Across the close the page never falls back to the previous session.

At 16:00:00 the market state turns closed, and the engine stores the session's close snapshot a
few seconds to a few minutes later. In that gap the last stored snapshot is the previous
session's. The page must not show it as the current figures: from the close until today's
snapshot is stored it serves the last committed state of the session that just closed, cut at the
close, so a poll on either side of 16:00:00 reads the same Today. When the snapshot never arrives
those figures hold, and `/health`, `/api/header-status` and the poll's own `degraded_reasons` say
`close_snapshot_missing` once the engine's post-close window (the post-mortem cutoff, five minutes
after the close) has passed.

Real routes over a temp database, the real XNYS calendar, the real account-totals refresh against
the captured Composer body, a virtual host clock. The world is Friday's close with its snapshot
and latched totals, then Monday's session up to its last committed cycle (15:52 ET, before the
rebalance pause), with no Monday snapshot stored. Expected values are never typed: the baseline is
the response read at 15:59:50, one poll before the close.
"""

from __future__ import annotations

from datetime import date

import pytest

import app as app_module
from tests.app._closed_session_world import ClosedWorld, build_history_world, store_close_snapshot
from tests.app._refresh_timing_frozen_world import PREVIOUS_SESSION_SUFFIX, stored_snapshot
from tests.app.test_clocked_weekend_to_monday_open import WEDNESDAY_SESSION
from tests.app.test_closed_market_numbers_never_move import local, write_engine_cycle

FRIDAY = "2026-10-30"
MONDAY = "2026-11-02"
CLOSE_GAP_REASON = "close_snapshot_missing"

# Read in one poll: the figures a Today row, the headline and the hero chart show.
STRIP_FIELDS = ("today_change", "cumulative_return", "guard_alpha", "account_value")


def monday_before_the_close(monkeypatch, tmp_path) -> ClosedWorld:
    """Friday's close stored, Monday's last committed cycle written at 15:52 ET, the account
    totals refreshed at 15:59:30 so the cache is warm when the market closes."""
    cw = build_history_world(monkeypatch, tmp_path, first_history_day=date(2026, 9, 1))
    write_engine_cycle(cw.world, local(MONDAY, "15:52:10"), 1)
    cw.composer.version += 1
    cw.at(MONDAY, "15:59:30")
    app_module._refresh_account_totals()
    return cw


def figures(cw: ClosedWorld) -> dict:
    """What a page open on the dashboard reads: the poll, the 30d strip and the 30d hero chart."""
    state = cw.poll("/api/state")
    strip = cw.poll("/api/strip/30d")
    hero = cw.poll("/api/hero-chart/30d")
    return {
        "state": {key: state["portfolio_strip"].get(key) for key in STRIP_FIELDS},
        "strip": {key: strip.get(key) for key in ("today_change", "cumulative_return")}
        | {"guard_alpha": strip.get("guard_alpha")},
        "hero": {key: hero.get(key) for key in ("hist_dates", "hist_bot", "hist_held")},
        "_markers": {
            "trading_day": state.get("trading_day"),
            "previous_session": state.get("snapshot_is_previous_session"),
            "data_as_of": state.get("data_as_of"),
        },
    }


def not_current_session(read: dict, what: str, stale_day: str = FRIDAY) -> list[str]:
    """What is wrong with the markers of a poll that should describe the session that closed."""
    markers = read["_markers"]
    wrong = []
    if markers["previous_session"] is True:
        wrong.append(f"{what}: the poll is marked as the previous session's snapshot")
    if PREVIOUS_SESSION_SUFFIX in str(markers["data_as_of"]):
        wrong.append(f"{what}: data_as_of reads {markers['data_as_of']!r}")
    if markers["trading_day"] == stale_day:
        wrong.append(f"{what}: the poll serves the snapshot of {stale_day}")
    return wrong


def brief(key: str, value: dict) -> object:
    """The hero chart is long; its last point and length say whether it is the same series."""
    if key != "hero":
        return value
    dates, bot = value["hist_dates"], value["hist_bot"]
    return {"points": len(dates), "last_date": dates[-1:], "last_bot": bot[-1:]}


def moved_since(before: dict, read: dict, what: str) -> list[str]:
    return [
        f"{what} {key}: {brief(key, before[key])!r} -> {brief(key, read[key])!r}"
        for key in ("state", "strip", "hero")
        if read[key] != before[key]
    ]


def reasons(cw: ClosedWorld) -> dict[str, list]:
    return {
        "/health": cw.poll("/health")["degraded_reasons"],
        "/api/header-status": cw.poll("/api/header-status")["degraded_reasons"],
        "/api/state": cw.poll("/api/state")["degraded_reasons"],
    }


def test_a_poll_across_the_close_reads_the_same_today(monkeypatch, tmp_path):
    cw = monday_before_the_close(monkeypatch, tmp_path)
    cw.at(MONDAY, "15:59:50")
    before = figures(cw)
    assert cw.poll("/api/state")["market_state"] == "open"
    assert before["state"]["today_change"] is not None, "fixture sanity: Today has a figure"

    failures = []
    for clock in ("16:00:00", "16:00:05", "16:00:35", "16:04:59"):
        cw.at(MONDAY, clock)
        read = figures(cw)
        assert cw.poll("/api/state")["market_state"] == "closed_frozen"
        failures += moved_since(before, read, f"Monday {clock} ET")
        failures += not_current_session(read, f"Monday {clock} ET")
    assert not failures, (
        "the page changed across the close, before any snapshot of the session was stored:\n"
        + "\n".join(failures)
    )


def test_the_gap_before_the_snapshot_is_not_reported_as_a_missing_snapshot(monkeypatch, tmp_path):
    cw = monday_before_the_close(monkeypatch, tmp_path)
    seen = {}
    for clock in ("16:00:05", "16:04:59"):
        cw.at(MONDAY, clock)
        seen[clock] = reasons(cw)
    wrong = {
        f"{clock} {route}": got
        for clock, by_route in seen.items()
        for route, got in by_route.items()
        if CLOSE_GAP_REASON in got
    }
    assert not wrong, f"the normal gap after the close was reported as degraded: {wrong}"


@pytest.mark.parametrize(
    ("day", "clock"),
    [
        (MONDAY, "16:06:00"),
        (MONDAY, "20:00:00"),
        ("2026-11-03", "03:00:00"),
        ("2026-11-03", "09:00:00"),
    ],
)
def test_a_snapshot_that_never_arrives_holds_the_figures_and_says_so(
    monkeypatch, tmp_path, day, clock
):
    cw = monday_before_the_close(monkeypatch, tmp_path)
    cw.at(MONDAY, "15:59:50")
    before = figures(cw)

    cw.at(day, clock)
    read = figures(cw)
    wrong = moved_since(before, read, f"{day} {clock} ET") + not_current_session(
        read, f"{day} {clock} ET"
    )
    assert not wrong, "with no snapshot stored the page must hold the session that closed:\n" + (
        "\n".join(wrong)
    )
    missing = {route: got for route, got in reasons(cw).items() if CLOSE_GAP_REASON not in got}
    assert not missing, (
        f"{day} {clock} ET: the close of {MONDAY} has no snapshot five minutes after the bell, "
        f"yet these carry no {CLOSE_GAP_REASON!r}: {missing}"
    )
    assert cw.poll("/health")["status"] == "degraded"


def test_a_snapshot_that_arrives_late_clears_the_reason_and_moves_nothing(monkeypatch, tmp_path):
    cw = monday_before_the_close(monkeypatch, tmp_path)
    cw.at(MONDAY, "15:59:50")
    before = figures(cw)
    cw.at(MONDAY, "16:06:00")
    assert CLOSE_GAP_REASON in cw.poll("/health")["degraded_reasons"], (
        f"six minutes after the close of {MONDAY} with no snapshot stored, /health must say "
        f"{CLOSE_GAP_REASON!r}"
    )

    # The engine's late close step stores Monday's snapshot from the state it committed.
    store_close_snapshot(cw.world, MONDAY)
    assert stored_snapshot()["trading_day"] == MONDAY

    cw.at(MONDAY, "16:08:00")
    read = figures(cw)
    assert read["_markers"]["trading_day"] == MONDAY
    wrong = not_current_session(read, "Monday 16:08 ET")
    assert not wrong, "\n".join(wrong)
    assert read["state"]["today_change"] == before["state"]["today_change"], (
        "Today moved when the session's own snapshot replaced the held figures"
    )
    still = {route: got for route, got in reasons(cw).items() if CLOSE_GAP_REASON in got}
    assert not still, f"the reason stayed after the snapshot was stored: {still}"


@pytest.mark.parametrize(
    ("day", "clock"),
    [
        (FRIDAY, "16:30:00"),
        ("2026-10-31", "12:00:00"),
        ("2026-11-01", "20:00:00"),
        (MONDAY, "08:00:00"),
    ],
)
def test_a_stored_snapshot_is_never_reported_missing(monkeypatch, tmp_path, day, clock):
    """After Friday's close, through the weekend and into Monday's pre-market, the session's
    snapshot is stored: no reason, whatever the clock."""
    cw = build_history_world(monkeypatch, tmp_path, first_history_day=date(2026, 9, 1))
    cw.at(day, clock)
    present = {route: got for route, got in reasons(cw).items() if CLOSE_GAP_REASON in got}
    assert not present, f"{day} {clock} ET: {present}"


# ---------------------------------------------------------------------------
# An early-close day: the close is 13:00 ET, and the grace runs from 13:00
# ---------------------------------------------------------------------------

EARLY_CLOSE_DAY = "2026-11-27"  # the Friday after Thanksgiving: closes at 13:00 ET
WEDNESDAY = WEDNESDAY_SESSION.session_day  # the last session with a snapshot stored


def early_close_day_before_the_close(monkeypatch, tmp_path) -> ClosedWorld:
    """Wednesday's close stored, the early-close day's last committed cycle written at 12:52 ET,
    the account totals refreshed at 12:59:30, no snapshot yet for the early-close day."""
    cw = build_history_world(
        monkeypatch, tmp_path, WEDNESDAY_SESSION, first_history_day=date(2026, 10, 1)
    )
    write_engine_cycle(cw.world, local(EARLY_CLOSE_DAY, "12:52:10"), 1)
    cw.composer.version += 1
    cw.at(EARLY_CLOSE_DAY, "12:59:30")
    app_module._refresh_account_totals()
    return cw


def test_a_poll_across_an_early_close_reads_the_same_today(monkeypatch, tmp_path):
    cw = early_close_day_before_the_close(monkeypatch, tmp_path)
    cw.at(EARLY_CLOSE_DAY, "12:59:50")
    before = figures(cw)
    assert cw.poll("/api/state")["market_state"] == "open"
    assert before["state"]["today_change"] is not None, "fixture sanity: Today has a figure"

    failures = []
    for clock in ("13:00:00", "13:00:05", "13:00:35", "13:04:59"):
        cw.at(EARLY_CLOSE_DAY, clock)
        read = figures(cw)
        assert cw.poll("/api/state")["market_state"] == "closed_frozen"
        failures += moved_since(before, read, f"{EARLY_CLOSE_DAY} {clock} ET")
        failures += not_current_session(read, f"{EARLY_CLOSE_DAY} {clock} ET", WEDNESDAY)
    assert not failures, (
        "the page changed across the 13:00 close, before any snapshot of the day was stored:\n"
        + "\n".join(failures)
    )


@pytest.mark.parametrize("clock", ["13:00:05", "13:04:59"])
def test_the_early_close_gap_is_not_reported_as_a_missing_snapshot(monkeypatch, tmp_path, clock):
    cw = early_close_day_before_the_close(monkeypatch, tmp_path)
    cw.at(EARLY_CLOSE_DAY, clock)
    present = {route: got for route, got in reasons(cw).items() if CLOSE_GAP_REASON in got}
    assert not present, f"{EARLY_CLOSE_DAY} {clock} ET is inside the grace: {present}"


@pytest.mark.parametrize("clock", ["13:06:00", "14:30:00", "15:30:00"])
def test_a_snapshot_missing_after_an_early_close_is_reported_from_the_early_grace(
    monkeypatch, tmp_path, clock
):
    """Six minutes after 13:00 the grace is over, long before 16:05."""
    cw = early_close_day_before_the_close(monkeypatch, tmp_path)
    cw.at(EARLY_CLOSE_DAY, "12:59:50")
    before = figures(cw)
    cw.at(EARLY_CLOSE_DAY, clock)
    read = figures(cw)
    wrong = moved_since(before, read, f"{EARLY_CLOSE_DAY} {clock} ET")
    assert not wrong, "\n".join(wrong)
    missing = {route: got for route, got in reasons(cw).items() if CLOSE_GAP_REASON not in got}
    assert not missing, (
        f"{EARLY_CLOSE_DAY} {clock} ET: the 13:00 close has no snapshot six minutes later, yet "
        f"these carry no {CLOSE_GAP_REASON!r}: {missing}"
    )
