"""While the market is not open, the dashboard's labels name the session, not "today" or "live".

Real call path: the page ``GET /`` renders and the bodies the real routes answer, for one Flask world at
two instants (a Saturday, the market closed, and the following Monday at 10:01, open), captured the way
tests/dashboard/test_sleeve_rule_fires_label_follows_session.py captures them; ``static/chrome.js`` and
``static/index.js`` run whole under node (tests/dashboard/_index_js_harness.py). No label text is typed
from a capture: the session's date text is ``session_label(anchor)`` of the anchor the closed body
names, the same helper the sleeve row and History heading tests use.

The labels are the ones named beside the last session's figures: the hero Today row, each card's
footer, the detail panel's Live Return heading, its HWM sub-label, its events heading and its empty
events line, and the triggered card's "If held · live" heading.

Contract pinned
* First paint on a closed day: the hero row, every card footer, the panel's events heading, HWM
  sub-label and empty events line name the session date and do not say "today"; the Live Return
  heading and every if-held heading do not say "live" (and the if-held heading is still there).
* The same texts after the first poll while closed (nothing changes between first paint and the poll),
  and in a panel opened on a card while closed (its events line is written by the page script).
* While open the wording is unchanged: Today, Live Return, HWM today, Today's events, No events yet
  today., If held · live on a triggered card.
* The labels follow the state: an open page polled with a closed body reads as a closed render does,
  and a closed page polled with an open body reads as an open render does.
"""

from __future__ import annotations

import re
from datetime import date

import pytest

from tests.app._closed_session_world import build_history_world, utc_of
from tests.app.test_closed_market_numbers_never_move import local, write_engine_cycle
from tests.app.test_history_exits_hold_while_closed import MONDAY, anchor_of
from tests.app.test_sleeve_rule_fires_names_the_closed_session import session_label
from tests.dashboard import _index_js_harness as hx
from tests.dashboard import _refresh_timing_support as sup
from tests.dashboard.test_sleeve_rule_fires_label_follows_session import (
    MONDAY_OPEN,
    SATURDAY,
    capture,
)

TODAY_OR_LIVE = re.compile(r"\btoday(?:['’]s)?\b|\blive\b", re.I)
TODAY_WORD = re.compile(r"\btoday(?:['’]s)?\b", re.I)
LIVE_WORD = re.compile(r"\blive\b", re.I)

PRELUDE = r"""
const D = H.sandbox.document;
const norm = function (s) { return String(s == null ? '' : s).replace(/\s+/g, ' ').trim(); };
const without = function (el, parts) {
  let t = el.textContent;
  parts.forEach(function (p) { if (p) t = t.replace(p.textContent, ''); });
  return norm(t);
};
const kids = function (el) { return el.childNodes.filter(function (n) { return n.nodeType === 1; }); };
const labels = function () {
  const out = {};
  const row = D.querySelector('[data-testid="comp-today-bot-text"]').parentNode;
  const rowLabel = row.querySelector('.vs-row-label');
  out.heroToday = without(rowLabel, [rowLabel.querySelector('[data-testid="today-held-footnote-marker"]')]);
  out.cardFooters = Array.prototype.map.call(D.querySelectorAll('.card-footer-grid'), function (g) {
    const l = g.querySelector('.cfg-label');
    return without(l, [l.querySelector('[data-field="tc-alpha-badge"]')]);
  });
  out.ifHeld = Array.prototype.map.call(D.querySelectorAll('.dv-label'), function (e) { return norm(e.textContent); })
    .filter(function (t) { return /^if held/i.test(t); });
  out.liveReturn = norm(D.getElementById('dp-live-ret').parentNode.querySelector('.detail-stat-label').textContent);
  out.hwm = norm(D.getElementById('dp-shadow-peak').parentNode.querySelector('.detail-stat-sub').textContent);
  const events = D.getElementById('events-timeline');
  out.eventsHeading = norm(kids(events.parentNode)[0].textContent);
  out.eventsEmpty = norm(events.textContent);
  return out;
};
const poll = async function (body) {
  H.route('/api/state', { body: body });
  await H.tick(); await H.completeAnimations();
};
const openFirstCard = async function () { await H.press('.sym-card'); await H.settle(); };
"""


class Worlds:
    """The captured session world twice: closed (Saturday) and open (Monday 10:01)."""

    def __init__(self, monkeypatch, tmp_path):
        cw = build_history_world(monkeypatch, tmp_path, first_history_day=date(2026, 9, 1))
        cw.at(*SATURDAY)
        self.anchor = anchor_of(cw)
        self.session = session_label(self.anchor)
        self.closed = capture(cw)
        self.closed_now = utc_of(*SATURDAY).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        write_engine_cycle(cw.world, local(MONDAY, "10:00:20"), 1)
        cw.at(*MONDAY_OPEN)
        self.opened = capture(cw)
        self.open_now = utc_of(*MONDAY_OPEN).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        assert self.closed["state"]["market_state"] != "open"
        assert self.opened["state"]["market_state"] == "open"

    def run(self, page: dict, scenario: str, other_state: dict | None = None) -> dict:
        now = self.open_now if page is self.opened else self.closed_now
        return hx.run_scenario(
            page,
            PRELUDE + "await H.boot({ extras: true });" + scenario,
            scripts=sup.CHROME_AND_INDEX,
            extra_bodies={"other": {"state": other_state}},
            now_iso=now,
        )["result"]


@pytest.fixture
def worlds(monkeypatch, tmp_path) -> Worlds:
    return Worlds(monkeypatch, tmp_path)


def assert_closed_wording(found: dict, session: str, where: str) -> None:
    hero = found["heroToday"]
    assert session in hero and not TODAY_OR_LIVE.search(hero), f"{where}: hero row reads {hero!r}"
    assert found["cardFooters"], f"{where}: the page has no card footer"
    for text in found["cardFooters"]:
        assert session in text and not TODAY_OR_LIVE.search(text), (
            f"{where}: a card footer reads {text!r}"
        )
    for name in ("eventsHeading", "hwm", "eventsEmpty"):
        text = found[name]
        assert session in text and not TODAY_WORD.search(text), f"{where}: {name} reads {text!r}"
    assert not LIVE_WORD.search(found["liveReturn"]) and re.search(
        r"return", found["liveReturn"], re.I
    ), f"{where}: the figure's heading reads {found['liveReturn']!r}"
    assert found["ifHeld"], f"{where}: no card has an if-held heading"
    for text in found["ifHeld"]:
        assert not LIVE_WORD.search(text), f"{where}: an if-held heading reads {text!r}"
        assert re.match(r"if held", text, re.I), f"{where}: the if-held heading was removed: {text!r}"


OPEN_WORDING = {
    "heroToday": "Today",
    "liveReturn": "Live Return",
    "hwm": "HWM today",
    "eventsHeading": "Today’s events",
    "eventsEmpty": "No events yet today.",
}


def assert_open_wording(found: dict, where: str) -> None:
    for name, want in OPEN_WORDING.items():
        assert found[name] == want, f"{where}: {name} reads {found[name]!r}, not {want!r}"
    assert found["cardFooters"] and all(t == "Today" for t in found["cardFooters"]), (
        f"{where}: card footers read {found['cardFooters']}"
    )
    assert set(found["ifHeld"]) <= {"If held", "If held · live"}, found["ifHeld"]
    assert "If held · live" in found["ifHeld"], (
        f"{where}: precondition, the open page has a triggered card whose heading says live"
    )


def without_if_held(found: dict) -> dict:
    return {k: v for k, v in found.items() if k != "ifHeld"}


class TestFirstPaint:
    def test_a_closed_render_names_the_session_in_every_label(self, worlds):
        found = worlds.run(worlds.closed, "return labels();")
        assert_closed_wording(found, worlds.session, "first paint, closed")

    def test_an_open_render_keeps_its_wording(self, worlds):
        found = worlds.run(worlds.opened, "return labels();")
        assert_open_wording(found, "first paint, open")


class TestAfterThePoll:
    def test_the_first_closed_poll_changes_no_label(self, worlds):
        result = worlds.run(
            worlds.closed,
            "const first = labels(); await poll(H.bodies.state); return { first: first, after: labels() };",
        )
        assert result["after"] == result["first"], "a label changed between first paint and the poll"
        assert_closed_wording(result["after"], worlds.session, "after the closed poll")

    def test_a_panel_opened_while_closed_names_the_session_in_its_events_line(self, worlds):
        result = worlds.run(
            worlds.closed,
            "await openFirstCard(); await poll(H.bodies.state); return labels();",
        )
        assert_closed_wording(result, worlds.session, "panel opened, closed")

    def test_an_open_page_polled_with_a_closed_body_reads_as_a_closed_render_does(self, worlds):
        fresh = worlds.run(worlds.closed, "return labels();")
        after = worlds.run(
            worlds.opened,
            "await poll(H.bodies.other.state); return labels();",
            worlds.closed["state"],
        )
        # The if-held headings belong to each card's own triggered state, which differs between the
        # two captured worlds; they are judged on the word alone.
        assert without_if_held(after) == without_if_held(fresh)
        assert_closed_wording(after, worlds.session, "open page, closed poll")

    def test_a_closed_page_polled_with_an_open_body_reads_as_an_open_render_does(self, worlds):
        fresh = worlds.run(worlds.opened, "return labels();")
        after = worlds.run(
            worlds.closed,
            "await poll(H.bodies.other.state); return labels();",
            worlds.opened["state"],
        )
        assert without_if_held(after) == without_if_held(fresh)
        for name, want in OPEN_WORDING.items():
            assert after[name] == want, f"closed page, open poll: {name} reads {after[name]!r}"
        assert set(after["ifHeld"]) <= {"If held", "If held · live"}, after["ifHeld"]
