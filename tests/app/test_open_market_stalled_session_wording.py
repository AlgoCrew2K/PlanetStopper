"""RED: an open market with no cycle committed since the open says whose session its figures are.

While the market is open and the last cycle the engine committed is older than today's open (every
morning from the open until the first cycle lands, the Opening phase included, and any session on
which the engine has not yet committed), every figure on the pages is the last committed cycle's
session. Labels that say today, live or now name that session the way the closed market does:

* the dashboard's day word reads that session's day (`Oct 30`), not `Today`;
* a triggered card's headline label reads `If held`, not `If held · live`;
* the verdict basis reads `at close`, not `vs held now`;
* the live-return label, the legend, the HWM label and the events heading and empty text follow;
* History's table heading names the session (`Exits on Oct 30`), not `Today's exits`, and the table
  lists that session's exits, so the heading's day and its rows agree.

The key is the session of the last committed cycle against today's open, not the market state: once
a cycle commits after the open, every label is today's again (byte-identical to the open market's
wording before this change); a state that carries no cycle stamp keeps today's wording; the closed
market's wording does not change.

Method: the virtual-clock world of `tests/app/_closed_session_world` (the same one
`test_health_no_cycle_since_open` reads): captured shadow rows through the real writers, Friday's
close cycle in `bot_state`, the real XNYS calendar, the real routes through the Flask test client.
Cycles after the open are the shared `write_engine_cycle`. The expected words are the pages' own
wording (`tests/app/_open_session_wording`), and the day in each is built from the cycle's own date.
"""

from __future__ import annotations

import pytest

import database as database_module
from tests.app._open_session_wording import (
    FRIDAY,
    FRIDAY_LABEL,
    MONDAY,
    MONDAY_LABEL,
    TODAYS_WORDING,
    TUESDAY,
    session_wording,
    stalled_world,
    wording_of,
)
from tests.app.test_closed_market_numbers_never_move import local, write_engine_cycle

HISTORY = "/api/history/30"


def dashboard_wording(cw, day: str, clock: str, *, market_open: bool = True) -> dict:
    cw.at(day, clock)
    state = cw.poll("/api/state")["market_state"]
    assert (state == "open") is market_open, (
        f"fixture: {day} {clock} ET is {state!r}, expected {'open' if market_open else 'not open'}"
    )
    return wording_of(cw.raw("/").decode("utf-8"))


def history_heading(cw) -> str:
    """The heading the server renders before any script runs."""
    import re

    page = cw.raw("/history").decode("utf-8")
    found = re.search(r'id="todays-exits-heading">([^<]*)<', page)
    assert found, "the History page has no exits heading"
    return found.group(1).replace("&#39;", "'")


# ---------------------------------------------------------------------------
# The stalled open: figures are the last committed cycle's session
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "clock",
    [
        "09:30:30",  # the Opening phase, before the first cycle
        "09:32:59",  # still Opening
        "09:35:00",  # first minute the header reads Degraded
        "09:40:00",
        "13:00:00",
    ],
)
def test_an_open_morning_with_no_cycle_since_the_open_names_the_last_session_not_today(
    monkeypatch, tmp_path, clock
):
    cw = stalled_world(monkeypatch, tmp_path)

    seen = dashboard_wording(cw, MONDAY, clock)

    assert seen == session_wording(FRIDAY_LABEL), (
        f"Monday {clock} ET, the last cycle committed on Friday: the page says {seen}"
    )


@pytest.mark.parametrize(
    ("cycle_clock", "cycle_day"),
    [
        ("15:59:30", MONDAY),  # inside the session
        ("20:00:05", MONDAY),  # after the close: Tuesday in UTC, still Monday in ET
    ],
)
def test_the_session_named_is_the_et_date_of_the_last_cycle_not_a_fixed_friday(
    monkeypatch, tmp_path, cycle_clock, cycle_day
):
    cw = stalled_world(monkeypatch, tmp_path)
    write_engine_cycle(cw.world, local(cycle_day, cycle_clock), 1)

    seen = dashboard_wording(cw, TUESDAY, "09:40:00")

    assert seen == session_wording(MONDAY_LABEL), (
        f"Tuesday 09:40 ET, the last cycle committed Monday {cycle_clock} ET: the page says {seen}"
    )


def test_the_stalled_wording_is_the_closed_markets_wording_for_the_same_session(
    monkeypatch, tmp_path
):
    """Friday evening, closed, names the same session in the same words as Monday's stalled open."""
    cw = stalled_world(monkeypatch, tmp_path)
    closed = dashboard_wording(cw, FRIDAY, "16:30:00", market_open=False)

    stalled = dashboard_wording(cw, MONDAY, "09:40:00")

    assert stalled == closed


# ---------------------------------------------------------------------------
# A cycle after the open: today's wording, unchanged
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cycle_clock", "read_clock"),
    [
        ("09:30:05", "09:30:40"),  # the first cycle, seconds after the open
        ("09:40:20", "09:41:10"),
        ("10:00:20", "13:00:00"),  # an old cycle of today is still a cycle since the open
    ],
)
def test_a_cycle_committed_after_the_open_returns_every_label_to_todays_wording(
    monkeypatch, tmp_path, cycle_clock, read_clock
):
    cw = stalled_world(monkeypatch, tmp_path)
    write_engine_cycle(cw.world, local(MONDAY, cycle_clock), 1)

    seen = dashboard_wording(cw, MONDAY, read_clock)

    assert seen == TODAYS_WORDING, (
        f"a cycle committed at {cycle_clock} ET and the page, read at {read_clock}, says {seen}"
    )


def test_the_labels_change_back_on_the_next_read_after_the_first_cycle_lands(monkeypatch, tmp_path):
    cw = stalled_world(monkeypatch, tmp_path)
    before = dashboard_wording(cw, MONDAY, "09:40:00")
    assert before == session_wording(FRIDAY_LABEL), "fixture: the morning is stalled"

    write_engine_cycle(cw.world, local(MONDAY, "09:40:20"), 1)

    assert dashboard_wording(cw, MONDAY, "09:41:10") == TODAYS_WORDING


def test_a_state_with_no_cycle_stamp_at_all_keeps_todays_wording(monkeypatch, tmp_path):
    cw = stalled_world(monkeypatch, tmp_path)
    state = database_module.load_state()
    state.pop("last_successful_cycle_at")
    database_module.save_state(state)
    assert "last_successful_cycle_at" not in database_module.load_state(), "fixture: stamp removed"

    seen = dashboard_wording(cw, MONDAY, "09:40:00")

    assert seen == TODAYS_WORDING, f"a state with no cycle stamp says {seen}"


# ---------------------------------------------------------------------------
# The closed market is untouched
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "clock"),
    [
        (FRIDAY, "16:30:00"),
        ("2026-10-31", "12:00:00"),  # Saturday
        ("2026-11-01", "20:00:00"),  # Sunday evening
        (MONDAY, "08:00:00"),  # pre-market
        (MONDAY, "09:29:59"),  # the last second before the open
    ],
)
def test_the_closed_markets_wording_names_the_last_session(monkeypatch, tmp_path, day, clock):
    cw = stalled_world(monkeypatch, tmp_path)

    seen = dashboard_wording(cw, day, clock, market_open=False)

    assert seen == session_wording(FRIDAY_LABEL), f"{day} {clock} ET, closed: the page says {seen}"


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


def test_the_first_paint_of_history_names_the_session_on_a_stalled_morning(monkeypatch, tmp_path):
    cw = stalled_world(monkeypatch, tmp_path)
    cw.at(MONDAY, "09:40:00")

    heading = history_heading(cw)

    assert heading == f"Exits on {FRIDAY_LABEL}", f"the stalled morning's heading is {heading!r}"


def test_the_first_paint_of_history_in_the_opening_phase_names_the_session(monkeypatch, tmp_path):
    cw = stalled_world(monkeypatch, tmp_path)
    cw.at(MONDAY, "09:30:30")

    assert history_heading(cw) == f"Exits on {FRIDAY_LABEL}"


def test_the_first_paint_of_history_is_todays_exits_once_a_cycle_has_committed(
    monkeypatch, tmp_path
):
    cw = stalled_world(monkeypatch, tmp_path)
    write_engine_cycle(cw.world, local(MONDAY, "09:40:20"), 1)
    cw.at(MONDAY, "09:41:10")

    assert history_heading(cw) == "Today's exits"


def test_history_closed_heading_is_unchanged(monkeypatch, tmp_path):
    cw = stalled_world(monkeypatch, tmp_path)
    cw.at(FRIDAY, "16:30:00")

    assert history_heading(cw) == f"Exits on {FRIDAY_LABEL}"


def test_the_history_answer_on_a_stalled_morning_lists_the_named_sessions_exits(
    monkeypatch, tmp_path
):
    """The heading names a day, so the rows are that day's: not an empty table of the new morning."""
    cw = stalled_world(monkeypatch, tmp_path)
    cw.at(FRIDAY, "16:30:00")
    closed = cw.poll(HISTORY)
    assert closed["todays_exits_day"] == FRIDAY and closed["todays_exits"], (
        "fixture: the closed session has exits to list"
    )

    cw.at(MONDAY, "09:40:00")
    stalled = cw.poll(HISTORY)

    assert stalled["todays_exits_day"] == FRIDAY, (
        f"the stalled morning's exits are of {stalled['todays_exits_day']!r}, not {FRIDAY}"
    )
    assert stalled["todays_exits"] == closed["todays_exits"]


def test_the_history_answer_after_a_cycle_commits_is_todays_again(monkeypatch, tmp_path):
    cw = stalled_world(monkeypatch, tmp_path)
    write_engine_cycle(cw.world, local(MONDAY, "09:40:20"), 1)
    cw.at(MONDAY, "09:41:10")

    body = cw.poll(HISTORY)

    assert body["todays_exits_day"] == MONDAY
