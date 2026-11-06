"""The detail chart's if-held legend and the headline descriptor, by market state.

Real call path: the page ``GET /`` renders and the bodies the real routes answer, for one Flask world at
two instants (a Saturday, the market closed, and the following Monday at 10:01, open), through the
``Worlds`` capture of tests/dashboard/test_closed_market_labels_name_the_session.py; ``static/chrome.js``
and ``static/index.js`` run whole under node (tests/dashboard/_index_js_harness.py).

Contract pinned
* The detail panel's chart legend names the if-held line "Live (if held)" while the market is open and
  "If held" while it is not, at first paint and after the first poll (nothing changes between them),
  and it follows a state change in either direction, as the panel's Live return heading does.
* The headline descriptor ends "All symphonies at their latest values, before exit friction." in every
  market state, at first paint and after a poll, and never says "today".
* The Performance page keeps its own series name for the if-held path ("If held", Copy table C row
  F12b): it names a series, not a clock, and is not the dashboard's open-market legend.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.dashboard import _index_js_harness as hx
from tests.dashboard import _refresh_timing_support as sup
from tests.dashboard.test_closed_market_labels_name_the_session import Worlds
from tests.ui import _page_copy as page_copy

REPO = Path(__file__).resolve().parents[2]
OPEN_LEGEND = "Live (if held)"
CLOSED_LEGEND = "If held"
DESCRIPTOR_TAIL = "All symphonies at their latest values, before exit friction."

PRELUDE = r"""
const D = H.sandbox.document;
const norm = function (s) { return String(s == null ? '' : s).replace(/\s+/g, ' ').trim(); };
const legend = function () {
  return Array.prototype.filter.call(D.querySelectorAll('[data-testid="detail-panel"] span'), function (e) {
    return /^(Live \(if held\)|If held)$/.test(norm(e.textContent));
  }).map(function (e) { return norm(e.textContent); });
};
const descriptor = function () {
  return norm(D.querySelector('[data-testid="guard-alpha-descriptor"]').textContent).replace(/^Simulated /, '');
};
const read = function () { return { legend: legend(), descriptor: descriptor() }; };
const poll = async function (body) {
  H.route('/api/state', { body: body });
  await H.tick(); await H.completeAnimations();
};
"""


@pytest.fixture
def worlds(monkeypatch, tmp_path) -> Worlds:
    return Worlds(monkeypatch, tmp_path)


def run(worlds: Worlds, page: dict, scenario: str, other_state: dict | None = None):
    now = worlds.open_now if page is worlds.opened else worlds.closed_now
    return hx.run_scenario(
        page,
        PRELUDE + "await H.boot({ extras: true });" + scenario,
        scripts=sup.CHROME_AND_INDEX,
        extra_bodies={"other": {"state": other_state}},
        now_iso=now,
    )["result"]


def assert_descriptor(found: dict, where: str) -> None:
    assert found["descriptor"].endswith(DESCRIPTOR_TAIL), (
        f"{where}: descriptor reads {found['descriptor']!r}"
    )
    assert "today" not in found["descriptor"].lower(), f"{where}: descriptor says today"


class TestFirstPaint:
    def test_a_closed_render_labels_the_if_held_line_if_held(self, worlds):
        found = run(worlds, worlds.closed, "return read();")
        assert found["legend"] == [CLOSED_LEGEND], (
            f"closed first paint: legend reads {found['legend']}"
        )

    def test_an_open_render_labels_the_if_held_line_live(self, worlds):
        found = run(worlds, worlds.opened, "return read();")
        assert found["legend"] == [OPEN_LEGEND], f"open first paint: legend reads {found['legend']}"

    @pytest.mark.parametrize("page", ["closed", "opened"])
    def test_the_descriptor_reads_the_same_tail_in_every_market_state(self, worlds, page):
        found = run(worlds, getattr(worlds, page), "return read();")
        assert_descriptor(found, f"{page} first paint")


class TestAfterThePoll:
    def test_the_first_closed_poll_changes_neither_text(self, worlds):
        result = run(
            worlds,
            worlds.closed,
            "const first = read(); await poll(H.bodies.other.state); return { first: first, after: read() };",
            worlds.closed["state"],
        )
        assert result["after"] == result["first"], (
            "a text changed between first paint and the first poll"
        )
        assert result["after"]["legend"] == [CLOSED_LEGEND]
        assert_descriptor(result["after"], "closed, after the poll")

    def test_the_first_open_poll_changes_neither_text(self, worlds):
        result = run(
            worlds,
            worlds.opened,
            "const first = read(); await poll(H.bodies.other.state); return { first: first, after: read() };",
            worlds.opened["state"],
        )
        assert result["after"] == result["first"], (
            "a text changed between first paint and the first poll"
        )
        assert result["after"]["legend"] == [OPEN_LEGEND]
        assert_descriptor(result["after"], "open, after the poll")

    def test_an_open_page_polled_with_a_closed_body_reads_if_held(self, worlds):
        result = run(
            worlds,
            worlds.opened,
            "await poll(H.bodies.other.state); return read();",
            worlds.closed["state"],
        )
        assert result["legend"] == [CLOSED_LEGEND], f"open page, closed poll: {result['legend']}"
        assert_descriptor(result, "open page, closed poll")

    def test_a_closed_page_polled_with_an_open_body_reads_live_if_held(self, worlds):
        result = run(
            worlds,
            worlds.closed,
            "await poll(H.bodies.other.state); return read();",
            worlds.opened["state"],
        )
        assert result["legend"] == [OPEN_LEGEND], f"closed page, open poll: {result['legend']}"
        assert_descriptor(result, "closed page, open poll")


def test_the_performance_page_keeps_its_own_if_held_series_name():
    template = (REPO / "templates" / "performance.html").read_text(encoding="utf-8")
    script = (REPO / "static" / "performance.js").read_text(encoding="utf-8")
    series_name = page_copy.text("F12b", "C")
    assert f"<span>{series_name}</span>" in template
    assert f"label: '{series_name}'" in script
    assert f"<span>{OPEN_LEGEND}</span>" not in template
    assert f"label: '{OPEN_LEGEND}'" not in script
