"""While the market is not open, the dashboard's labels name the session, not "today" or "live".

Real call path: the page ``GET /`` renders and the bodies the real routes answer, for one Flask world at
two instants (a Saturday, the market closed, and the following Monday at 10:01, open), captured the way
tests/dashboard/test_sleeve_rule_fires_label_follows_session.py captures them; ``static/chrome.js`` and
``static/index.js`` run whole under node (tests/dashboard/_index_js_harness.py). No label text is typed
from a capture: the session's date text is ``session_label(anchor)`` of the anchor the closed body
names, the same helper the sleeve row and History heading tests use.

The labels are the ones named beside the last session's figures: the hero Today row, each card's
footer, the detail panel's Live return heading, its HWM sub-label, its events heading and its empty
events line, and the triggered card's "If held · live" heading.

Contract pinned
* First paint on a closed day: the hero row, every card footer, the panel's events heading, HWM
  sub-label and empty events line name the session date and do not say "today"; the Live return
  heading and every if-held heading do not say "live" (and the if-held heading is still there).
* The same texts after the first poll while closed (nothing changes between first paint and the poll),
  and in a panel opened on a card while closed (its events line is written by the page script).
* While open the wording is unchanged: Today, Live return, HWM today, Today's events, No events yet
  today., If held · live on a triggered card.
* The labels follow the state: an open page polled with a closed body reads as a closed render does,
  and a closed page polled with an open body reads as an open render does.
* Before any poll has answered (the first poll still in flight, or failing) a panel opened on a closed
  page already names the session in its empty events line, as the server render does; an open page
  keeps "No events yet today.".
* The stat under the panel's return reads "at close" while the market is not open and the symphony is
  not triggered, "frozen at exit" when it is triggered, and "real-time" only while open and not
  triggered; it is set when the panel opens and on every poll, with no change between the open and the
  first poll.
"""

from __future__ import annotations

import re
from datetime import date

import pytest

from tests.app._closed_session_world import build_history_world, utc_of
from tests.app._refresh_timing_frozen_world import SYMPHONY_IDS
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
const openCard = async function (id) { await H.press('.sym-card[data-sym-id="' + id + '"]'); await H.settle(); };
const liveSub = function () { return norm(D.getElementById('dp-live-ret-sub').textContent); };
"""


class Worlds:
    """The captured session world twice: closed (Saturday) and open (Monday 10:01)."""

    def __init__(self, monkeypatch, tmp_path):
        cw = build_history_world(monkeypatch, tmp_path, first_history_day=date(2026, 9, 1))
        cw.at(*SATURDAY)
        self.anchor = anchor_of(cw)
        self.session = session_label(self.anchor)
        self.closed = capture(cw)
        self.symphony_id = SYMPHONY_IDS[0]
        # The real route's answer for the first card's Risk Profile, read at the closed instant.
        performance = cw.world.client.get(
            f"/api/performance?scope=symphony&symphony_id={self.symphony_id}&days=60"
        )
        assert performance.status_code == 200, (
            f"/api/performance answered {performance.status_code}"
        )
        self.closed_performance = performance.get_json()
        self.closed_now = utc_of(*SATURDAY).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        write_engine_cycle(cw.world, local(MONDAY, "10:00:20"), 1)
        cw.at(*MONDAY_OPEN)
        self.opened = capture(cw)
        self.open_now = utc_of(*MONDAY_OPEN).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        assert self.closed["state"]["market_state"] != "open"
        assert self.opened["state"]["market_state"] == "open"

    def run(
        self,
        page: dict,
        scenario: str,
        other_state: dict | None = None,
        boot: str = "{ extras: true }",
    ) -> dict:
        now = self.open_now if page is self.opened else self.closed_now
        return hx.run_scenario(
            page,
            PRELUDE + f"await H.boot({boot});" + scenario,
            scripts=sup.CHROME_AND_INDEX,
            extra_bodies={"other": {"state": other_state}},
            now_iso=now,
        )["result"]


@pytest.fixture
def worlds(monkeypatch, tmp_path) -> Worlds:
    return Worlds(monkeypatch, tmp_path)


def assert_closed_wording(found: dict, session: str, where: str) -> None:
    """Every label is checked and every miss is reported together."""
    problems: list[str] = []
    hero = found["heroToday"]
    if session not in hero or TODAY_OR_LIVE.search(hero):
        problems.append(f"hero Today row reads {hero!r}")
    if not found["cardFooters"]:
        problems.append("the page has no card footer")
    for text in found["cardFooters"]:
        if session not in text or TODAY_OR_LIVE.search(text):
            problems.append(f"a card footer reads {text!r}")
    for name in ("eventsHeading", "hwm", "eventsEmpty"):
        text = found[name]
        if session not in text or TODAY_WORD.search(text):
            problems.append(f"{name} reads {text!r}")
    heading = found["liveReturn"]
    if LIVE_WORD.search(heading) or not re.search(r"return", heading, re.I):
        problems.append(f"the figure's heading reads {heading!r}")
    if not found["ifHeld"]:
        problems.append("no card has an if-held heading")
    for text in found["ifHeld"]:
        if LIVE_WORD.search(text):
            problems.append(f"an if-held heading reads {text!r}")
        if not re.match(r"if held", text, re.I):
            problems.append(f"the if-held heading was removed: {text!r}")
    assert not problems, f"{where} (session label {session!r}): " + "; ".join(problems)


OPEN_WORDING = {
    "heroToday": "Today",
    "liveReturn": "Live return",
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
        assert result["after"] == result["first"], (
            "a label changed between first paint and the poll"
        )
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


FIRST_POLL_NOT_ANSWERED = [
    "{ extras: true, statePending: true }",
    "{ extras: true, stateReject: true }",
]
FIRST_POLL_IDS = ["first poll in flight", "first poll failing"]


class TestBeforeTheFirstPollAnswers:
    @pytest.mark.parametrize("boot", FIRST_POLL_NOT_ANSWERED, ids=FIRST_POLL_IDS)
    def test_a_panel_opened_on_a_closed_page_names_the_session_in_its_empty_events_line(
        self, worlds, boot
    ):
        found = worlds.run(worlds.closed, "await openFirstCard(); return labels();", boot=boot)
        assert_closed_wording(found, worlds.session, "panel opened before the first poll, closed")

    @pytest.mark.parametrize("boot", FIRST_POLL_NOT_ANSWERED, ids=FIRST_POLL_IDS)
    def test_the_empty_events_line_does_not_change_when_the_first_poll_answers(self, worlds, boot):
        found = worlds.run(
            worlds.closed,
            "await openFirstCard(); const before = labels();"
            " H.route('/api/state', { body: H.bodies.state }); await H.tick(); await H.completeAnimations();"
            " return { before: before, after: labels() };",
            boot=boot,
        )
        assert found["after"]["eventsEmpty"] == found["before"]["eventsEmpty"], (
            "the empty events line flipped when the first poll answered"
        )

    @pytest.mark.parametrize("boot", FIRST_POLL_NOT_ANSWERED, ids=FIRST_POLL_IDS)
    def test_an_open_page_keeps_its_wording_before_the_first_poll(self, worlds, boot):
        found = worlds.run(worlds.opened, "await openFirstCard(); return labels();", boot=boot)
        assert found["eventsEmpty"] == OPEN_WORDING["eventsEmpty"]
        assert found["eventsHeading"] == OPEN_WORDING["eventsHeading"]


NOT_TRIGGERED, TRIGGERED = SYMPHONY_IDS[1], SYMPHONY_IDS[0]
AT_CLOSE, REAL_TIME, FROZEN = "at close", "real-time", "frozen at exit"


def closed_untriggered(worlds: Worlds) -> dict:
    """The closed body with the untriggered card's ``triggered`` flag cleared (one named field).

    The captured Saturday body holds both cards as triggered (the frozen snapshot of the Friday
    session), so a closed, untriggered symphony is the real body with that one field changed.
    """
    return sup.with_entry(worlds.closed["state"], NOT_TRIGGERED, triggered=False)


class TestTheStatUnderTheReturnNamesWhereTheFigureIsFrom:
    def test_the_captured_worlds_hold_the_cards_this_class_needs(self, worlds):
        assert sup.card_entry(worlds.opened["state"], TRIGGERED)["triggered"] is True
        assert not sup.card_entry(worlds.opened["state"], NOT_TRIGGERED)["triggered"]
        assert sup.card_entry(worlds.closed["state"], TRIGGERED)["triggered"] is True
        assert not sup.card_entry(closed_untriggered(worlds), NOT_TRIGGERED)["triggered"]

    def test_a_closed_untriggered_symphony_reads_at_close_on_open_and_on_every_poll(self, worlds):
        found = worlds.run(
            worlds.closed,
            f"await openCard('{NOT_TRIGGERED}'); const opened = liveSub();"
            " await poll(H.bodies.other.state); const first = liveSub();"
            " await poll(H.bodies.other.state); return { opened: opened, first: first, second: liveSub() };",
            closed_untriggered(worlds),
            boot="{ extras: true, state: H.bodies.other.state }",
        )
        assert found == {"opened": AT_CLOSE, "first": AT_CLOSE, "second": AT_CLOSE}

    @pytest.mark.parametrize("boot", FIRST_POLL_NOT_ANSWERED, ids=FIRST_POLL_IDS)
    def test_before_the_first_poll_answers_the_stat_claims_nothing_live_then_reads_at_close(
        self, worlds, boot
    ):
        found = worlds.run(
            worlds.closed,
            f"await openCard('{NOT_TRIGGERED}'); const opened = liveSub();"
            " await poll(H.bodies.other.state); return { opened: opened, polled: liveSub() };",
            closed_untriggered(worlds),
            boot=boot,
        )
        assert found["opened"] in ("", AT_CLOSE), f"the closed panel says {found['opened']!r}"
        assert found["polled"] == AT_CLOSE

    @pytest.mark.parametrize("page_name", ["closed", "opened"])
    def test_a_triggered_symphony_reads_frozen_at_exit_in_either_state(self, worlds, page_name):
        found = worlds.run(
            getattr(worlds, page_name),
            f"await openCard('{TRIGGERED}'); const opened = liveSub();"
            " await poll(H.bodies.state); return { opened: opened, polled: liveSub() };",
        )
        assert found == {"opened": FROZEN, "polled": FROZEN}

    def test_an_open_untriggered_symphony_still_reads_real_time(self, worlds):
        found = worlds.run(
            worlds.opened,
            f"await openCard('{NOT_TRIGGERED}'); const opened = liveSub();"
            " await poll(H.bodies.state); return { opened: opened, polled: liveSub() };",
        )
        assert found == {"opened": REAL_TIME, "polled": REAL_TIME}

    def test_the_stat_follows_the_state_a_poll_reports(self, worlds):
        closed_poll_on_open_page = worlds.run(
            worlds.opened,
            f"await openCard('{NOT_TRIGGERED}'); await poll(H.bodies.other.state); return liveSub();",
            closed_untriggered(worlds),
        )
        open_poll_on_closed_page = worlds.run(
            worlds.closed,
            f"await openCard('{NOT_TRIGGERED}'); await poll(H.bodies.other.state); return liveSub();",
            worlds.opened["state"],
        )
        assert closed_poll_on_open_page == AT_CLOSE, "an open page polled with a closed body"
        assert open_poll_on_closed_page == REAL_TIME, "a closed page polled with an open body"
