"""The first paint of /history on a closed day never reads "Today's exits".

``templates/history.html`` server-renders the exits heading and the return button as the literal
"Today's exits", and ``static/history.js`` replaces them with ``Exits on <Mon D>`` only after
``/api/history`` answers. On a closed day the page therefore reads "Today's exits" over Friday's
rows until the answer lands, and for good if it never does.

Real call path: the real ``GET /history`` at a frozen host clock (tests/ui/_history_answers.py): a
Saturday for the closed case and a Thursday for the open one. The session's date text is built from
the ``todays_exits_day`` the real ``GET /api/history/30`` names at the same clock, with the same
helper the sleeve row and History heading tests use (``session_label``).

Contract pinned
* Closed first paint: the exits heading and the (hidden) return button name the session date and do not
  say "Today".
* Open first paint: both read "Today's exits", unchanged.
* After the load on the closed day, the heading starts with the text the first paint had (only the
  count is added), so no label changes after load.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from unittest.mock import patch

import pytest

import app as app_module
import market_calendar
from tests.app.test_sleeve_rule_fires_names_the_closed_session import session_label
from tests.dashboard._index_js_harness import find_by_id, parse_html, tree_text
from tests.ui import _history_answers as hist
from tests.ui._history_answers import ET, frozen_host_clock
from tests.ui._page_js_harness import route, run_page
from tests.ui._shell_support import (  # noqa: F401
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    seed_history_exits,
)

CLOSED_AT = hist.NOW_ET  # Saturday 2026-07-25 12:00 ET: the session is Friday's
OPEN_AT = datetime(2026, 7, 23, 12, 0, tzinfo=ET)  # Thursday, market open
HISTORY = r"/api/history/(\d+|ytd)$"
BUTTON = '[data-testid="history-todays-exits-btn"]'
TODAY = re.compile(r"\btoday\b", re.I)


@pytest.fixture
def days(tmp_path, monkeypatch):
    # The XNYS calendar builds its holiday tables on first use; built under a frozen host clock they
    # fail, so a test that runs first in a process warms them here.
    market_calendar.get_market_state(datetime(2026, 7, 24, 12, 0, tzinfo=ET))
    market_calendar.is_trading_day(date(2026, 7, 24))
    market_calendar.most_recent_completed_session(datetime(2026, 7, 25, 12, 0, tzinfo=ET))
    return seed_history_exits(tmp_path, monkeypatch.setattr)


def _render(instant: datetime, tmp_path) -> str:
    with (
        frozen_host_clock(instant, stdlib=False),
        patch.object(app_module, "ENV_FILE_PATH", str(tmp_path / "no.env")),
        app_module.app.test_client() as client,
    ):
        response = client.get("/history")
    assert response.status_code == 200, f"GET /history answered {response.status_code}"
    return response.get_data(as_text=True)


def _first_paint(instant: datetime, tmp_path) -> dict:
    tree = parse_html(_render(instant, tmp_path))
    heading = find_by_id(tree, "todays-exits-heading")
    assert heading is not None, "the render holds no #todays-exits-heading"
    buttons = [
        n
        for n in _walk(tree)
        if n["a"].get("data-testid") == "history-todays-exits-btn"
    ]
    assert buttons, "the render holds no return button"
    return {
        "heading": " ".join(tree_text(heading).split()),
        "button": " ".join(tree_text(buttons[0]).split()),
    }


def _walk(node):
    if isinstance(node, str):
        return
    yield node
    for child in node["c"]:
        yield from _walk(child)


def _session_label_of_closed_day() -> str:
    body = hist.history_body(30)
    assert body["todays_exits_market_closed"] is True, "precondition: the clock is a closed day"
    return session_label(body["todays_exits_day"])


def test_a_closed_first_paint_names_the_session_in_the_heading_and_the_button(days, tmp_path):
    label = _session_label_of_closed_day()
    paint = _first_paint(CLOSED_AT, tmp_path)
    for name in ("heading", "button"):
        assert label in paint[name], f"the {name} reads {paint[name]!r}, not the session {label!r}"
        assert not TODAY.search(paint[name]), f"the {name} reads {paint[name]!r} on a closed day"


def test_an_open_first_paint_keeps_todays_exits(days, tmp_path):
    paint = _first_paint(OPEN_AT, tmp_path)
    assert paint["heading"] == "Today's exits", paint
    assert paint["button"] == "Today's exits", paint


def test_the_load_on_a_closed_day_only_adds_the_count_to_the_first_paint(days, tmp_path):
    first = _first_paint(CLOSED_AT, tmp_path)
    run = run_page(
        _render(CLOSED_AT, tmp_path),
        "await H.boot(); await H.advance(100); return H.text('#todays-exits-heading');",
        routes=[route(HISTORY, hist.history_answer(30), method="GET")],
        pathname="/history",
    )
    after = " ".join(str(run.result).split())
    assert after.startswith(first["heading"]), (
        f"the heading was {first['heading']!r} at first paint and {after!r} after the load"
    )
    assert re.fullmatch(re.escape(first["heading"]) + r" \(\d+\)", after), after
