"""A morning on which no cycle commits is visible: `/health` and the header say so.

The engine can be spawned every minute and exit 0 without committing a cycle (a lost lock, a
missing key, Composer failing every read). `/health` then read "ok" all morning. The reason
`no_cycle_since_open` reports the market having been open three minutes or more with no cycle
committed since the open: the `last_successful_cycle_at` the engine stamps is older than the
session's open. It rides on the same `degraded_reasons` list as every other reason, so `/health`,
`/api/header-status` and the poll carry it and the header's label reads Degraded. It clears with
the next commit and never appears while the market is closed.

The first five minutes of a session are the header's Opening phase while no cycle has committed.
The plan states both "three minutes or more" and "never in the Opening state", which cannot both
hold between 09:33 and 09:35 on a morning with no commit. These tests pin what holds on either
reading: nothing before three minutes, the reason once the Opening phase has ended with no commit,
and nothing at all while closed.

Real routes over a temp database, the real XNYS calendar, a virtual host clock; the commits are the
shared `write_engine_cycle` (rows, then the state saved with the cycle's stamp).
"""

from __future__ import annotations

from datetime import date

import pytest

from tests.app._closed_session_world import ClosedWorld, build_history_world
from tests.app.test_closed_market_numbers_never_move import local, write_engine_cycle

MONDAY = "2026-11-02"
REASON = "no_cycle_since_open"


def carried_by(cw: ClosedWorld) -> dict[str, bool]:
    """Which of the three bodies list the reason."""
    bodies = {
        "/health": cw.poll("/health"),
        "/api/header-status": cw.poll("/api/header-status"),
        "/api/state": cw.poll("/api/state"),
    }
    return {route: REASON in body["degraded_reasons"] for route, body in bodies.items()}


def world(monkeypatch, tmp_path) -> ClosedWorld:
    return build_history_world(monkeypatch, tmp_path, first_history_day=date(2026, 9, 1))


@pytest.mark.parametrize("clock", ["09:30:00", "09:30:30", "09:31:30", "09:32:59"])
def test_a_session_open_under_three_minutes_is_not_reported(monkeypatch, tmp_path, clock):
    cw = world(monkeypatch, tmp_path)
    cw.at(MONDAY, clock)
    assert cw.poll("/api/state")["market_state"] == "open"
    assert not any(carried_by(cw).values()), f"Monday {clock} ET: {carried_by(cw)}"


@pytest.mark.parametrize("clock", ["09:35:00", "09:40:00", "10:15:00", "13:00:00"])
def test_a_morning_with_no_cycle_since_the_open_is_reported_on_every_surface(
    monkeypatch, tmp_path, clock
):
    cw = world(monkeypatch, tmp_path)
    cw.at(MONDAY, clock)
    assert cw.poll("/api/state")["market_state"] == "open"
    missing = [route for route, present in carried_by(cw).items() if not present]
    assert not missing, (
        f"Monday {clock} ET: the market has been open since 09:30 and the last cycle committed "
        f"on Friday; these do not list {REASON!r}: {missing}"
    )
    health = cw.poll("/health")
    assert health["status"] == "degraded", "the reason must make /health degraded"
    assert cw.poll("/api/header-status")["status"] == "degraded"


def test_a_stamp_from_before_the_open_does_not_count_as_a_cycle_since_the_open(
    monkeypatch, tmp_path
):
    """A closed-market run at 09:29:30 stamps the state too; the session has still had no cycle."""
    cw = world(monkeypatch, tmp_path)
    write_engine_cycle(cw.world, local(MONDAY, "09:29:30"), 1)
    cw.at(MONDAY, "09:36:00")
    assert carried_by(cw) == dict.fromkeys(("/health", "/api/header-status", "/api/state"), True)


def test_the_reason_clears_on_the_next_commit(monkeypatch, tmp_path):
    cw = world(monkeypatch, tmp_path)
    cw.at(MONDAY, "09:40:00")
    assert carried_by(cw)["/health"], "fixture sanity: the reason is listed before the commit"

    write_engine_cycle(cw.world, local(MONDAY, "09:40:20"), 1)
    cw.at(MONDAY, "09:41:10")
    still = {route: present for route, present in carried_by(cw).items() if present}
    assert not still, f"the reason stayed after a cycle committed at 09:40:20: {still}"
    assert cw.poll("/health")["status"] == "ok"


def test_a_cycle_committed_within_three_minutes_of_the_open_keeps_the_morning_clean(
    monkeypatch, tmp_path
):
    cw = world(monkeypatch, tmp_path)
    write_engine_cycle(cw.world, local(MONDAY, "09:31:20"), 1)
    seen = {}
    for clock in ("09:33:00", "09:36:00", "09:50:00"):
        cw.at(MONDAY, clock)
        seen[clock] = carried_by(cw)
    wrong = {clock: got for clock, got in seen.items() if any(got.values())}
    assert not wrong, f"a cycle committed at 09:31:20, yet: {wrong}"


@pytest.mark.parametrize(
    ("day", "clock"),
    [
        ("2026-10-31", "12:00:00"),  # Saturday
        ("2026-11-01", "20:00:00"),  # Sunday evening
        (MONDAY, "08:00:00"),  # pre-market
        (MONDAY, "09:29:59"),  # the last second before the open
        ("2026-11-26", "10:00:00"),  # Thanksgiving: a weekday on which the market never opens
        ("2026-11-26", "13:00:00"),
    ],
)
def test_it_never_appears_while_the_market_is_closed(monkeypatch, tmp_path, day, clock):
    cw = world(monkeypatch, tmp_path)
    cw.at(day, clock)
    assert cw.poll("/api/state")["market_state"] != "open", "fixture sanity: the market is closed"
    assert not any(carried_by(cw).values()), f"{day} {clock} ET: {carried_by(cw)}"
