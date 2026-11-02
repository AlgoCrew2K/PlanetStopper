"""The first paint of /history on a closed day never reads "Today's exits".

``templates/history.html`` server-renders the exits heading and the return button as the literal
"Today's exits", and ``static/history.js`` replaces them with ``Exits on <Mon D>`` only after
``/api/history`` answers. On a closed day the page therefore reads "Today's exits" over Friday's
rows until the answer lands, and for good if it never does.

Real call path: the real ``GET /history`` and ``GET /api/history/30`` of the captured session world at
two instants (a Saturday, closed, and the following Monday at 10:01, open), built the way
tests/dashboard/test_sleeve_rule_fires_label_follows_session.py builds it. The session's date text is
built from the ``todays_exits_day`` the real route names, with the helper the sleeve row and History
heading tests use (``session_label``). ``static/history.js`` runs unmodified over the page
(tests/ui/_page_js_harness.py).

Contract pinned
* Closed first paint: the exits heading and the (hidden) return button name the session date and do not
  say "Today".
* Open first paint, once a cycle has committed since the open: both read "Today's exits", unchanged.
* After the load on the closed day, the heading is the first paint's text plus the count, so no label
  changes after load.
"""

from __future__ import annotations

import re
from datetime import date

import pytest

from tests.app._closed_session_world import build_history_world
from tests.app.test_closed_market_numbers_never_move import local, write_engine_cycle
from tests.app.test_history_exits_hold_while_closed import anchor_of
from tests.app.test_sleeve_rule_fires_names_the_closed_session import session_label
from tests.dashboard._index_js_harness import find_all, find_by_id, parse_html, tree_text
from tests.dashboard.test_sleeve_rule_fires_label_follows_session import MONDAY_OPEN, SATURDAY
from tests.ui._page_js_harness import route, run_page

HISTORY = r"/api/history/(\d+|ytd)$"
TODAY = re.compile(r"\btoday\b", re.I)


class Pages:
    def __init__(self, monkeypatch, tmp_path):
        cw = build_history_world(monkeypatch, tmp_path, first_history_day=date(2026, 9, 1))
        cw.at(*SATURDAY)
        self.anchor = anchor_of(cw)
        self.closed_html = self._page(cw)
        self.closed_body = cw.poll("/api/history/30")
        # A normal open morning: the engine has committed a cycle since the open. Without one the
        # morning is stalled and the page names the last session (the closed first paint above).
        write_engine_cycle(cw.world, local(MONDAY_OPEN[0], "09:40:20"), 1)
        cw.at(*MONDAY_OPEN)
        self.open_html = self._page(cw)
        assert self.closed_body["todays_exits_market_closed"] is True

    @staticmethod
    def _page(cw) -> str:
        response = cw.world.client.get("/history")
        assert response.status_code == 200, f"GET /history answered {response.status_code}"
        return response.get_data(as_text=True)


@pytest.fixture
def pages(monkeypatch, tmp_path) -> Pages:
    return Pages(monkeypatch, tmp_path)


def _first_paint(html: str) -> dict:
    tree = parse_html(html)
    heading = find_by_id(tree, "todays-exits-heading")
    assert heading is not None, "the render holds no #todays-exits-heading"
    buttons = find_all(tree, lambda n: n["a"].get("data-testid") == "history-todays-exits-btn")
    assert buttons, "the render holds no return button"
    return {
        "heading": " ".join(tree_text(heading).split()),
        "button": " ".join(tree_text(buttons[0]).split()),
    }


def test_a_closed_first_paint_names_the_session_in_the_heading_and_the_button(pages):
    label = session_label(pages.anchor)
    paint = _first_paint(pages.closed_html)
    for name in ("heading", "button"):
        assert label in paint[name], f"the {name} reads {paint[name]!r}, not the session {label!r}"
        assert not TODAY.search(paint[name]), f"the {name} reads {paint[name]!r} on a closed day"


def test_an_open_first_paint_keeps_todays_exits(pages):
    paint = _first_paint(pages.open_html)
    assert paint["heading"] == "Today's exits", paint
    assert paint["button"] == "Today's exits", paint


def test_the_load_on_a_closed_day_only_adds_the_count_to_the_first_paint(pages):
    first = _first_paint(pages.closed_html)
    run = run_page(
        pages.closed_html,
        "await H.boot(); await H.advance(100); return H.text('#todays-exits-heading');",
        routes=[route(HISTORY, {"status": 200, "json": pages.closed_body}, method="GET")],
        pathname="/history",
    )
    after = " ".join(str(run.result).split())
    assert re.fullmatch(re.escape(first["heading"]) + r" \(\d+\)", after), (
        f"the heading was {first['heading']!r} at first paint and {after!r} after the load"
    )
