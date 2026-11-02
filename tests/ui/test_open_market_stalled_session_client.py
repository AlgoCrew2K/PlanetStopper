"""RED: the page script says what the server rendered, on a stalled open morning and after it.

`tests/app/test_open_market_stalled_session_wording.py` pins the markup. This file runs the real
`static/index.js` and `static/history.js` over that markup (tests/ui/_page_js_harness.py, node) with
the fetches answered by the real `/api/state` and `/api/history` routes of the same world, so the
meta the session wording is derived from is the meta production sends, not a hand-built one.

* First paint, the load poll and the interval poll all read the same words: no label changes between
  the server render and the first poll, in the Opening phase and on a later stalled morning.
* When a cycle commits after the open, the next poll returns every label to today's wording, the
  triggered card's `If held · live` included (the closed-market rewrite is not one-way).
* A page left open across a session boundary (live wording on Monday, polled on Tuesday morning
  before any cycle) switches to the new stalled session's words.
* The closed market's words, and an open market whose last cycle is after the open, are unchanged.
* History: its table heading and the rows follow the payload, and the first paint and the poll agree.
"""

from __future__ import annotations

import html as html_lib
import re
from datetime import UTC

import pytest

from tests.app._closed_session_world import ClosedWorld, utc_of
from tests.app._open_session_wording import (
    FRIDAY,
    FRIDAY_LABEL,
    MONDAY,
    MONDAY_LABEL,
    READ_WORDING_JS,
    TODAYS_WORDING,
    TUESDAY,
    reduce_client_wording,
    session_wording,
    stalled_world,
)
from tests.app.test_closed_market_numbers_never_move import local, write_engine_cycle
from tests.ui._page_js_harness import render_page, route, run_page

STATE = r"/api/state$"


@pytest.fixture(autouse=True)
def _stub_get_api_state_dict():
    """Same name as the autouse stub of tests/ui/conftest.py, which this module overrides: the
    poll and the page are the real routes over the world's own state, not a stub body."""
    yield


@pytest.fixture(autouse=True)
def _stub_database_load_state_for_advisor_routes():
    """Overrides the autouse stub of tests/ui/conftest.py for the same reason: the state the page
    and the poll read is the world's `bot_state`, with its real cycle stamp."""
    yield


HISTORY = r"/api/history/(\d+|ytd)$"

POLL_SCENARIO = (
    READ_WORDING_JS
    + """
const first = wording();
await H.boot();
const loaded = wording();
await H.advance(31000);
const polled = wording();
return { first: first, loaded: loaded, polled: polled, stateReads: H.to('/api/state').length };
"""
)


def wall_of(day: str, clock: str) -> str:
    return utc_of(day, clock).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_dashboard(html: str, bodies: list[dict], day: str, clock: str) -> dict:
    """First paint, load poll and interval poll, each reduced to the table of expected words. The
    first body answers the load, the next the interval poll (the last repeats)."""
    out = run_page(
        html,
        POLL_SCENARIO,
        routes=[route(STATE, *[{"json": b} for b in bodies], method="GET")],
        pathname="/",
        wall=wall_of(day, clock),
    )
    result = out.result
    assert result["stateReads"] >= 2, "fixture: the page did not poll after its load"
    return {k: reduce_client_wording(result[k]) for k in ("first", "loaded", "polled")}


def page_and_body(cw: ClosedWorld, day: str, clock: str) -> tuple[str, dict]:
    cw.at(day, clock)
    body = cw.poll("/api/state")
    assert body["market_state"] == "open", "fixture: the market is open"
    return render_page("/"), body


# ---------------------------------------------------------------------------
# Stalled open: first paint and every poll say the session
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("clock", ["09:30:30", "09:32:30", "09:40:00", "13:00:00"])
def test_first_paint_and_every_poll_name_the_last_session_on_a_stalled_open_morning(
    monkeypatch, tmp_path, clock
):
    cw = stalled_world(monkeypatch, tmp_path)
    html, body = page_and_body(cw, MONDAY, clock)

    seen = read_dashboard(html, [body], MONDAY, clock)

    expected = session_wording(FRIDAY_LABEL)
    assert seen == {"first": expected, "loaded": expected, "polled": expected}, (
        f"Monday {clock} ET, stalled: first paint, load poll and interval poll read {seen}"
    )


def test_a_stalled_morning_after_a_monday_cycle_names_monday_in_the_page_and_the_polls(
    monkeypatch, tmp_path
):
    cw = stalled_world(monkeypatch, tmp_path)
    write_engine_cycle(cw.world, local(MONDAY, "20:00:05"), 1)
    html, body = page_and_body(cw, TUESDAY, "09:40:00")

    seen = read_dashboard(html, [body], TUESDAY, "09:40:00")

    expected = session_wording(MONDAY_LABEL)
    assert seen == {"first": expected, "loaded": expected, "polled": expected}


# ---------------------------------------------------------------------------
# Transitions: the first cycle after the open, and a page left open overnight
# ---------------------------------------------------------------------------


def test_the_poll_after_the_first_cycle_returns_every_label_to_todays_wording(
    monkeypatch, tmp_path
):
    cw = stalled_world(monkeypatch, tmp_path)
    html, stalled_body = page_and_body(cw, MONDAY, "09:40:00")
    write_engine_cycle(cw.world, local(MONDAY, "09:40:20"), 1)
    cw.at(MONDAY, "09:40:45")
    live_body = cw.poll("/api/state")

    seen = read_dashboard(html, [stalled_body, live_body], MONDAY, "09:40:00")

    assert seen["first"] == seen["loaded"] == session_wording(FRIDAY_LABEL)
    assert seen["polled"] == TODAYS_WORDING, (
        f"the poll that carries the first cycle of the day left the page saying {seen['polled']}"
    )


def test_a_page_rendered_with_todays_wording_switches_to_the_new_sessions_words_next_morning(
    monkeypatch, tmp_path
):
    cw = stalled_world(monkeypatch, tmp_path)
    write_engine_cycle(cw.world, local(MONDAY, "10:00:20"), 1)
    html, live_body = page_and_body(cw, MONDAY, "10:01:00")
    cw.at(TUESDAY, "09:40:00")
    stalled_body = cw.poll("/api/state")
    assert stalled_body["market_state"] == "open", "fixture: Tuesday 09:40 ET is an open market"

    seen = read_dashboard(html, [live_body, stalled_body], MONDAY, "10:01:00")

    assert seen["first"] == seen["loaded"] == TODAYS_WORDING
    assert seen["polled"] == session_wording(MONDAY_LABEL), (
        f"a page left open overnight and polled before any cycle says {seen['polled']}"
    )


# ---------------------------------------------------------------------------
# Unchanged: a cycle after the open, and the closed market
# ---------------------------------------------------------------------------


def test_an_open_market_with_a_cycle_since_the_open_reads_todays_wording_on_every_path(
    monkeypatch, tmp_path
):
    cw = stalled_world(monkeypatch, tmp_path)
    write_engine_cycle(cw.world, local(MONDAY, "09:40:20"), 1)
    html, body = page_and_body(cw, MONDAY, "09:41:10")

    seen = read_dashboard(html, [body], MONDAY, "09:41:10")

    assert seen == {"first": TODAYS_WORDING, "loaded": TODAYS_WORDING, "polled": TODAYS_WORDING}


@pytest.mark.parametrize(("day", "clock"), [(FRIDAY, "16:30:00"), ("2026-10-31", "12:00:00")])
def test_the_closed_markets_words_are_the_same_on_first_paint_and_every_poll(
    monkeypatch, tmp_path, day, clock
):
    cw = stalled_world(monkeypatch, tmp_path)
    cw.at(day, clock)
    html, body = render_page("/"), cw.poll("/api/state")
    assert body["market_state"] != "open", "fixture: the market is closed"

    seen = read_dashboard(html, [body], day, clock)

    expected = session_wording(FRIDAY_LABEL)
    assert seen == {"first": expected, "loaded": expected, "polled": expected}


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

HISTORY_SCENARIO = """
const first = H.text('#todays-exits-heading');
await H.boot();
const loaded = H.text('#todays-exits-heading');
await H.advance(31000);
return { first: first, loaded: loaded, polled: H.text('#todays-exits-heading'),
         rows: H.$$('#triggers-tbody tr').length };
"""


def read_history(cw: ClosedWorld, day: str, clock: str) -> tuple[dict, dict]:
    cw.at(day, clock)
    html, body = render_page("/history"), cw.poll("/api/history/30")
    out = run_page(
        html,
        HISTORY_SCENARIO,
        routes=[route(HISTORY, {"json": body}, method="GET")],
        pathname="/history",
        wall=wall_of(day, clock),
    )
    return {
        k: html_lib.unescape(v) if isinstance(v, str) else v for k, v in out.result.items()
    }, body


def heading_day(heading: str) -> str:
    return re.sub(r"\s*\(\d+\)$", "", heading)


@pytest.mark.parametrize("clock", ["09:30:30", "09:40:00"])
def test_history_heading_names_the_session_and_counts_its_exits_on_a_stalled_morning(
    monkeypatch, tmp_path, clock
):
    cw = stalled_world(monkeypatch, tmp_path)

    seen, body = read_history(cw, MONDAY, clock)

    exits = len(body["todays_exits"])
    assert exits >= 1, "fixture: the named session has exits to count"
    assert seen["loaded"] == f"Exits on {FRIDAY_LABEL} ({exits})", (
        f"Monday {clock} ET, stalled: the heading after the load reads {seen['loaded']!r}"
    )
    assert seen["rows"] == exits
    assert (
        heading_day(seen["first"]) == heading_day(seen["loaded"]) == f"Exits on {FRIDAY_LABEL}"
    ), f"first paint {seen['first']!r}, after load {seen['loaded']!r}: the day must not change"
    assert seen["polled"] == seen["loaded"]


def test_history_heading_is_todays_exits_once_a_cycle_has_committed(monkeypatch, tmp_path):
    cw = stalled_world(monkeypatch, tmp_path)
    write_engine_cycle(cw.world, local(MONDAY, "09:40:20"), 1)

    seen, body = read_history(cw, MONDAY, "09:41:10")

    assert heading_day(seen["first"]) == "Today's exits"
    assert seen["loaded"] == f"Today's exits ({len(body['todays_exits'])})"
    assert seen["polled"] == seen["loaded"]


def test_history_closed_heading_is_unchanged(monkeypatch, tmp_path):
    cw = stalled_world(monkeypatch, tmp_path)

    seen, body = read_history(cw, FRIDAY, "16:30:00")

    assert seen["loaded"] == f"Exits on {FRIDAY_LABEL} ({len(body['todays_exits'])})"
    assert heading_day(seen["first"]) == f"Exits on {FRIDAY_LABEL}"
