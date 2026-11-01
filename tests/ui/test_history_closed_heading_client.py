"""RED: /history names the session whose exits it shows while the market is closed.

The route answers `todays_exits` with two fields that say which session they belong to
(`tests/app/test_history_exits_hold_while_closed.py`): `todays_exits_day`, the ISO date of that
session, and `todays_exits_market_closed`, true from the close to the next open. The page's table
heading and the button that returns from a drilled day to it read them:

* closed: the heading is `Exits on <Mon D> (<n>)` (for example `Exits on Oct 30 (1)`), the month an
  English three-letter abbreviation and the day without a leading zero, and the button reads
  `Exits on <Mon D>`; neither says "Today", because on Saturday the exits are Friday's;
* open: the heading stays `Today's exits (<n>)` and the button `Today's exits`;
* the date is the session's calendar day whatever time zone the browser is in (a date parsed as UTC
  and printed in a zone west of it names the day before), and it is written as text, never markup.

`static/history.js` runs unmodified over the page the real app renders. The base body is the real
route's answer at a frozen host clock (`tests/ui/_history_answers`); the two fields and the exit
rows are set by the test (synthetic, row shape `ts`/`symphony_id`/`symphony_name`/`reason`/`detail`
as the route writes it), and every expected heading is built from those inputs.
"""

from __future__ import annotations

import copy
from datetime import date, datetime

import pytest

import market_calendar
from tests.ui import _history_answers as hist
from tests.ui._history_answers import ET
from tests.ui._page_js_harness import render_page, route, run_page
from tests.ui._shell_support import (  # noqa: F401
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    seed_history_exits,
)

HISTORY = r"/api/history/(\d+|ytd)$"
BUTTON = '[data-testid="history-todays-exits-btn"]'
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

EXITS = [
    {
        "ts": "10:05",
        "symphony_id": "sym-a",
        "symphony_name": "Symphony A",
        "reason": "Trailing Stop",
        "detail": 1.5,
    },
    {
        "ts": "13:40",
        "symphony_id": "sym-b",
        "symphony_name": "Symphony B",
        "reason": "Take-Profit",
        "detail": -0.4,
    },
]

PAGE_STATE = """
  const state = () => ({
    heading: H.text('#todays-exits-heading'),
    headingHtml: H.$('#todays-exits-heading').innerHTML,
    headingChildren: H.$('#todays-exits-heading').children.length,
    rows: H.$$('#triggers-tbody tr').length,
    button: H.text('[data-testid="history-todays-exits-btn"]'),
  });
"""


@pytest.fixture
def days(tmp_path, monkeypatch):
    # The XNYS calendar builds its holiday tables on first use; built under the frozen host clock
    # of `_history_answers` they fail, so a test that runs first in a process must warm them here.
    market_calendar.get_market_state(datetime(2026, 7, 24, 12, 0, tzinfo=ET))
    market_calendar.is_trading_day(date(2026, 7, 24))
    market_calendar.most_recent_completed_session(datetime(2026, 7, 25, 12, 0, tzinfo=ET))
    return seed_history_exits(tmp_path, monkeypatch.setattr)


def label(day: str) -> str:
    """`Mon D` of an ISO date, built from its own digits (no date parsing, so no zone)."""
    _, month, dom = day.split("-")
    return f"{MONTHS[int(month) - 1]} {int(dom)}"


def closed_session_answer(*, day: str, closed: bool, exits=EXITS) -> dict:
    body = hist.history_answer(30)
    body["json"] = copy.deepcopy(body["json"])
    body["json"]["todays_exits"] = copy.deepcopy(exits)
    body["json"]["todays_exits_day"] = day
    body["json"]["todays_exits_market_closed"] = closed
    return body


def run(scenario: str, *routes):
    return run_page(
        render_page("/history"), "await H.boot();\n" + PAGE_STATE + scenario, routes=list(routes)
    )


@pytest.mark.parametrize("day", ["2026-10-30", "2026-11-02", "2026-01-05"])
def test_a_closed_heading_names_the_session_date_with_the_count(days, day):
    out = run(
        "return state();", route(HISTORY, closed_session_answer(day=day, closed=True), method="GET")
    )
    heading = out.result["heading"]
    assert heading == f"Exits on {label(day)} ({len(EXITS)})", (
        f"the closed table's heading is {heading!r}"
    )
    assert "Today" not in heading


def test_a_closed_session_with_no_exits_still_names_its_date(days):
    body = closed_session_answer(day="2026-10-30", closed=True, exits=[])
    out = run("return state();", route(HISTORY, body, method="GET"))
    assert out.result["heading"] == "Exits on Oct 30 (0)"


@pytest.mark.parametrize("day", ["2026-11-02", "2026-12-31"])
def test_an_open_market_heading_stays_todays_exits(days, day):
    out = run(
        "return state();",
        route(HISTORY, closed_session_answer(day=day, closed=False), method="GET"),
    )
    assert out.result["heading"] == f"Today's exits ({len(EXITS)})"
    assert label(day) not in out.result["heading"]


@pytest.mark.parametrize("zone", ["America/Los_Angeles", "Pacific/Auckland", "UTC"])
def test_the_date_is_the_sessions_calendar_day_in_every_browser_zone(days, zone):
    out = run(
        f"process.env.TZ = '{zone}';\nawait H.advance(30000);\nreturn state();",
        route(
            HISTORY,
            closed_session_answer(day="2026-10-30", closed=True),
            closed_session_answer(day="2026-10-30", closed=True),
            method="GET",
        ),
    )
    assert out.result["heading"] == f"Exits on Oct 30 ({len(EXITS)})", (
        f"in {zone} the heading names {out.result['heading']!r}"
    )


def test_the_date_is_written_as_text_not_markup(days):
    body = closed_session_answer(day="<b>2026-10-30</b>", closed=True)
    out = run("return state();", route(HISTORY, body, method="GET"))
    assert out.result["headingChildren"] == 0
    assert "<b>" not in out.result["headingHtml"]


def test_the_heading_follows_a_poll_from_closed_to_open_and_back(days):
    out = run(
        """
        const closed = state().heading;
        await H.advance(30000);
        const open = state().heading;
        await H.advance(30000);
        return {closed, open, closedAgain: state().heading};
        """,
        route(
            HISTORY,
            closed_session_answer(day="2026-10-30", closed=True),
            closed_session_answer(day="2026-11-02", closed=False, exits=EXITS[:1]),
            closed_session_answer(day="2026-11-02", closed=True, exits=EXITS),
            method="GET",
        ),
    )
    r = out.result
    assert r["closed"] == f"Exits on Oct 30 ({len(EXITS)})"
    assert r["open"] == "Today's exits (1)"
    assert r["closedAgain"] == f"Exits on Nov 2 ({len(EXITS)})"


def drill_day(body: dict) -> str:
    daily = body["json"]["daily_exits"]
    return max(body["json"]["daily_dates"], key=lambda d: len(daily.get(d, [])))


@pytest.mark.parametrize(
    ("closed", "button"), [(True, "Exits on Oct 30"), (False, "Today's exits")]
)
def test_the_button_back_from_a_drilled_day_names_the_session_when_closed(days, closed, button):
    body = closed_session_answer(day="2026-10-30", closed=closed)
    day = drill_day(body)
    out = run(
        f"""
        await H.click('#daily-chart-svg rect[data-date="{day}"]');
        const drilled = state();
        await H.click('{BUTTON}');
        return {{drilled, back: state()}};
        """,
        route(HISTORY, body, method="GET"),
    )
    r = out.result
    assert r["drilled"]["button"] == button, f"the button reads {r['drilled']['button']!r}"
    assert r["drilled"]["heading"].startswith(hist.day_label(day)), (
        "fixture sanity: a day is drilled"
    )
    expected = f"Exits on Oct 30 ({len(EXITS)})" if closed else f"Today's exits ({len(EXITS)})"
    assert r["back"]["heading"] == expected, f"the way back reads {r['back']['heading']!r}"
