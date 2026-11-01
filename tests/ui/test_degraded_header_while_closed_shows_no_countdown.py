"""A Degraded header while the market is closed shows no countdown, on every page.

The header label ranks Degraded above Closed, so a closed market with a failing engine reads Degraded.
Closed hides the countdown to the next engine run (no run is coming); Degraded kept it ticking, beside
a tooltip that says the next minute starts normally, which no closed-market run does.

Real call path: ``static/chrome.js`` (with the page's other scripts) runs unmodified under node. On
/history, /performance, /ai-advisor and /settings the header body is the real ``GET /api/header-status``
at a server instant (tests/ui/_header_status_answers.py); on the dashboard ``/`` it is the body the real
``/api/state`` route returns, with its market state and degraded reasons set by one named field each
(tests/dashboard/_refresh_timing_support.py ``closed_body``, ``with_top``).

Contract pinned
* Closed and degraded: the label reads Degraded and ``#next-run-countdown`` is hidden (display none or
  the hidden attribute), on each of the five pages, at first read and after a later one.
* Open and degraded: the label reads Degraded and the countdown is shown (a closed market is the only
  case that hides it), so the fix is not a blanket removal.
* Degraded then recovered while closed: the label reads Closed and the countdown stays hidden.
"""

from __future__ import annotations

import pytest

from tests.dashboard import _index_js_harness as hx
from tests.dashboard import _refresh_timing_support as sup
from tests.ui._header_status_answers import et, header_answer, wall_of
from tests.ui._page_js_harness import render_page, route, run_page

STATUS = r"/api/header-status$"
HEADER_PAGES = ["/history", "/performance", "/ai-advisor", "/settings"]
READ = """
const read = function () {
  const el = H.$('#next-run-countdown');
  return {
    label: H.text('#engine-status-label'),
    countdownHidden: !el || el.style.display === 'none' || el.hasAttribute('hidden'),
  };
};
"""
CLOSED_AT = et(20, 0)
OPEN_AT = et(10, 43, 5)


def _header_run(path: str, answers: list[dict], scenario: str, at):
    return run_page(
        render_page(path),
        "await H.boot();\n" + READ + scenario,
        routes=[route(STATUS, *answers, method="GET")],
        pathname=path,
        wall=wall_of(at),
    ).result


@pytest.mark.parametrize("path", HEADER_PAGES)
class TestHeaderPages:
    def test_closed_and_degraded_hides_the_countdown_at_first_read_and_after_a_later_one(
        self, path
    ):
        result = _header_run(
            path,
            [
                header_answer(CLOSED_AT, reasons=["engine_failing"]),
                header_answer(et(20, 0, 30), reasons=["engine_failing"]),
            ],
            "const first = read(); await H.advance(30000); return { first: first, later: read() };",
            CLOSED_AT,
        )
        for stage in ("first", "later"):
            assert result[stage]["label"] == "Degraded", f"{path} {stage}: {result[stage]}"
            assert result[stage]["countdownHidden"], (
                f"{path} {stage}: a degraded header on a closed market still shows the countdown"
            )

    def test_open_and_degraded_keeps_the_countdown(self, path):
        result = _header_run(
            path,
            [header_answer(OPEN_AT, reasons=["engine_failing"])],
            "return read();",
            OPEN_AT,
        )
        assert result["label"] == "Degraded"
        assert not result["countdownHidden"], f"{path}: an open degraded header lost its countdown"

    def test_recovering_while_closed_reads_closed_with_the_countdown_still_hidden(self, path):
        result = _header_run(
            path,
            [
                header_answer(CLOSED_AT, reasons=["engine_failing"]),
                header_answer(et(20, 0, 30), reasons=[]),
            ],
            "await H.advance(30000); return read();",
            CLOSED_AT,
        )
        assert result["label"] == "Closed"
        assert result["countdownHidden"]


DASH_READ = """
const D = H.sandbox.document;
const read = function () {
  const el = D.getElementById('next-run-countdown');
  const label = D.getElementById('engine-status-label');
  return {
    label: label ? label.textContent.trim() : null,
    countdownHidden: !el || el.style.display === 'none' || el.hasAttribute('hidden'),
  };
};
const poll = async function (body) {
  H.route('/api/state', { body: body });
  await H.tick(); await H.completeAnimations();
};
"""


def _dashboard(scenario: str, **bodies):
    return sup.run(
        DASH_READ + "await H.boot({ extras: true });" + scenario,
        world="default",
        scripts=sup.CHROME_AND_INDEX,
        extra_bodies=bodies,
    )


class TestDashboard:
    def _bodies(self):
        base = hx.get_world("default")["next"]["state"]
        stamped = sup.with_stamp(base, sup.later_stamp(sup.render_stamp(hx.get_world("default"))))
        closed = sup.with_top(sup.closed_body(stamped), degraded_reasons=["engine_failing"])
        opened = sup.with_top(sup.open_body(stamped), degraded_reasons=["engine_failing"])
        recovered = sup.with_top(sup.closed_body(stamped), degraded_reasons=[])
        return {"closed": closed, "opened": opened, "recovered": recovered}

    def test_closed_and_degraded_hides_the_countdown(self):
        result = _dashboard("await poll(H.bodies.closed); return read();", **self._bodies())
        assert result["label"] == "Degraded", result
        assert result["countdownHidden"], (
            "the dashboard's degraded header shows a countdown while closed"
        )

    def test_open_and_degraded_keeps_the_countdown(self):
        result = _dashboard("await poll(H.bodies.opened); return read();", **self._bodies())
        assert result["label"] == "Degraded", result
        assert not result["countdownHidden"], "an open degraded dashboard header lost its countdown"

    def test_recovering_while_closed_reads_closed_with_the_countdown_hidden(self):
        result = _dashboard(
            "await poll(H.bodies.closed); await poll(H.bodies.recovered); return read();",
            **self._bodies(),
        )
        assert result["label"] == "Closed", result
        assert result["countdownHidden"]
