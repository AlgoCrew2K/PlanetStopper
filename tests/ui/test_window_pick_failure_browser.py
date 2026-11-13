"""A picked hero window never sits beside another window's figures, in a real browser (marker ``browser``).

Run with ``python -m pytest -m browser -n 0 tests/ui/test_window_pick_failure_browser.py``. Firefox
headless through Playwright; the page and its API answers are the harness world's
(tests/ui/_dashboard_refresh_browser_support.py), the app in this process serves only the static
scripts, and every other request is answered by a route handler, so nothing is written anywhere and
no outside host is contacted.

The strip request for the picked window is held, or answered with a server error, by a handler this
module registers on top of the shared one. What the operator can read is judged from the page's
rendered text (``innerText``, which leaves out hidden elements), not from the markup.

Contract pinned (the node-harness twin is tests/dashboard/test_window_pick_never_shows_another_windows_figures.py)
* From the click until the strip answers, the headline is a dash and no visible Max DD or
  Ann. Vol cell holds the previous window's digits.
* A failed strip request leaves the figures dashed and puts a visible notice naming the window on the page.
* A later pick clears the notice and shows that window's own figures.
"""

from __future__ import annotations

import json
import re

import pytest

from tests.dashboard import _refresh_timing_support as sup
from tests.ui import _alignment as al
from tests.ui._dashboard_refresh_browser_support import (  # noqa: F401  (fixtures)
    firefox,
    text,
    wait_for,
    wait_refresh,
    world,
)
from tests.ui.test_dashboard_window_browser import probe  # noqa: F401  (fixture)

pytestmark = pytest.mark.browser

HEADLINE = "#guard-alpha-headline"
NO_DIGIT = "(selector) => !/\\d/.test(document.querySelector(selector).textContent)"
FIGURE_CELLS = (
    '[data-testid="comp-mdd-bot-text"]',
    '[data-testid="comp-mdd-held-text"]',
    '[data-testid="comp-vol-bot-text"]',
    '[data-testid="comp-vol-held-text"]',
)
# A visible sentence that names the 1y window and says it could not be loaded.
NOTICE = (
    "() => /(1y[^.\\n]{0,60}(could not|couldn.t|cannot|unable to|failed to|not loaded|unavailable)"
    "|(could not|couldn.t|cannot|unable to|failed to)[^.\\n]{0,60}1y)/i.test(document.body.innerText)"
)


def _open(probe):
    probe.state = sup.with_stamp(probe.world["state"], sup.render_stamp(probe.world))
    probe.page.goto(probe.server.base_url + "/")
    wait_refresh(probe, 1)


def _shown_digits(probe) -> list[str]:
    """The figure cells that are visible and hold a digit."""
    return probe.page.evaluate(
        """(cells) => cells.filter((sel) => {
            const el = document.querySelector(sel);
            return el && el.getClientRects().length > 0 && /\\d/.test(el.innerText);
        })""",
        list(FIGURE_CELLS),
    )


def test_a_pick_waiting_for_its_strip_shows_a_dash_and_no_old_digits(probe):
    held = []
    _open(probe)
    before = text(probe, HEADLINE)
    assert re.search(r"\d", before), "precondition: the 30d headline reads a figure"
    probe.page.route("**/api/strip/1y", lambda route: held.append(route))

    probe.page.locator('[data-testid="window-1y"]').click()

    wait_for(probe, NO_DIGIT, "the headline kept the previous window's figure", arg=HEADLINE)
    assert _shown_digits(probe) == [], "a Max DD or Ann. Vol cell kept the previous window's digits"
    assert len(held) == 1, "the pick never asked for its strip"
    held[0].fulfill(
        status=200, content_type="application/json", body=json.dumps(probe.strips["1y"])
    )
    want = sup.fmt_pp(probe.strips["1y"]["guard_alpha"])
    wait_for(
        probe,
        "(w) => document.querySelector('#guard-alpha-headline').textContent.trim() === w",
        "the strip's answer never wrote the 1y headline",
        arg=want,
    )
    assert probe.page_errors == []


def test_a_failed_strip_leaves_the_figures_dashed_and_a_notice_naming_the_window(probe):
    _open(probe)
    probe.page.route(
        "**/api/strip/1y",
        lambda route: route.fulfill(
            status=500,
            content_type="application/json",
            body=json.dumps({"status": "error", "message": "An internal error occurred"}),
        ),
    )

    probe.page.locator('[data-testid="window-1y"]').click()

    wait_for(probe, NOTICE, "no visible notice says the 1y window could not be loaded")
    assert not re.search(r"\d", text(probe, HEADLINE)), text(probe, HEADLINE)
    assert _shown_digits(probe) == []

    probe.page.locator('[data-testid="window-30d"]').click()
    want = sup.fmt_pp(probe.strips["30d"]["guard_alpha"])
    wait_for(
        probe,
        "(w) => document.querySelector('#guard-alpha-headline').textContent.trim() === w",
        "picking 30d never wrote the 30d headline",
        arg=want,
    )
    assert probe.page.evaluate(f"!({NOTICE})()"), "the notice outlived the next pick"
    assert probe.page_errors == []


def test_the_dollar_figures_keep_equal_gaps_when_the_summary_answers_500_and_after_a_failed_pick(
    probe,
):
    """S-2.14: the four dollar figures read as dashes and still sit on one line each with equal
    gaps of at most 48 px, on the first paint and after a pick whose summary also fails."""
    probe.page.route(
        "**/api/guard-alpha-summary*",
        lambda route: route.fulfill(
            status=500,
            content_type="application/json",
            body=json.dumps({"status": "error", "message": "An internal error occurred"}),
        ),
    )
    _open(probe)
    entry = al.FIGURE_LINES["dollar_saved_line"]
    spec = al.spec_of(entry, name="dollar_saved_line", no_wrap=True)
    for label in ("first paint", "after a failed pick"):
        if label == "after a failed pick":
            probe.page.locator('[data-testid="window-1y"]').click()
            probe.page.wait_for_timeout(1200)
        headline = text(probe, "#dollar-saved-headline")
        assert not re.search(r"\d", headline), f"{label}: the headline reads {headline!r}"
        found = al.measure(probe.page, al.LINE_GAPS_JS, spec)
        assert found["found"] == 1, f"{label}: the dollar line is not on the page"
        assert found["violations"] == [], f"{label}: " + "; ".join(found["violations"])
    assert probe.page_errors == []
