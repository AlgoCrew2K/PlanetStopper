"""The header's ET clock and workspace chip, by engine state and viewport width, in a real browser.

Browser-only (marker ``browser``; run with ``python -m pytest -m browser -n 0
tests/ui/test_header_clock_widths_browser.py``). Firefox headless through Playwright. The dashboard
page and its API answers are the harness world's (tests/ui/_dashboard_refresh_browser_support.py);
the other pages are the real app on the per-test database, with the header-status answer replaced by
the real route's body at a named server instant (tests/ui/_header_status_answers.py). Never skips.

Contract pinned, per state the header can be in:
* 1441 to 1500 px: the clock and the workspace chip show in every state except Rebalance pause, where
  the longest engine label would crowd the countdown; there they are hidden, the countdown reads
  whole (its box holds its text, it is on screen and uncovered), the wordmark stays on one line and
  the header is as tall as in every other state.
* 1501 px and wider: the clock and chip show in every state, Rebalance pause included.
* 1400 px and narrower: they stay hidden in every state (static/tokens.css governs).

Colours and sizes are not asserted: only whether the two elements are displayed, and the layout facts
above (a box holds its text, a line is one line, a header keeps its height).
"""

from __future__ import annotations

import pytest

from tests.dashboard import _refresh_timing_support as sup
from tests.ui._browser_server import running_server
from tests.ui._dashboard_refresh_browser_support import (  # noqa: F401  (fixtures)
    firefox,
    new_probe,
    wait_for,
    wait_refresh,
    world,
)
from tests.ui._header_status_answers import et, header_answer
from tests.ui._shell_support import (  # noqa: F401  (fixtures)
    _empty_post_mortem_directory,
    advisor_server,
    open_page,
    open_probe,
    seed_dashboard_state,
)

pytestmark = pytest.mark.browser

HEIGHT = 900
WIDTH_IN_RANGE = 1440
WIDTHS_ABOVE_RANGE = [1501, 1600, 1920]
WIDTHS_BELOW_RANGE = [1400, 1366, 1280]
LONG_LABEL = "Rebalance pause"
DEGRADED = ["engine_failing"]
HEIGHT_TOLERANCE_PX = 1.0

# state name -> (labels the state may read, whether the countdown is expected to show)
STATES = {
    "closed": ({"Closed"}, False),
    "live": ({"Live", "Stale"}, True),
    "opening": ({"Opening"}, True),
    "degraded-open": ({"Degraded"}, True),
    "degraded-closed": ({"Degraded"}, False),
    "rebalance-pause": ({LONG_LABEL}, True),
}

_HEADER_FACTS = """
() => {
  const q = (s) => document.querySelector(s);
  const shown = (e) => {
    if (!e) return false;
    const s = getComputedStyle(e);
    const r = e.getBoundingClientRect();
    return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
  };
  const nav = q('[data-testid="studio-nav"]');
  const brand = nav.firstElementChild;
  const countdown = q('#next-run-countdown');
  const cr = countdown.getBoundingClientRect();
  let covered = false;
  if (shown(countdown)) {
    const hit = document.elementFromPoint(
      Math.min(Math.max(cr.left + cr.width / 2, 0), innerWidth - 1),
      Math.min(Math.max(cr.top + cr.height / 2, 0), innerHeight - 1));
    covered = !(hit && (hit === countdown || countdown.contains(hit)));
  }
  return {
    label: q('#engine-status-label').textContent.trim(),
    clockShown: shown(q('#et-clock')),
    chipShown: shown(q('#ws-wrap')),
    navHeight: nav.getBoundingClientRect().height,
    brandOneLine: brand.getBoundingClientRect().height < 1.8 * parseFloat(getComputedStyle(brand).fontSize),
    countdownShown: shown(countdown),
    countdownText: countdown.textContent.trim(),
    countdownClipped: countdown.scrollWidth > countdown.clientWidth + 1,
    countdownOnScreen: cr.left >= -0.5 && cr.right <= innerWidth + 0.5,
    countdownCovered: covered,
    pageScrollsSideways: document.documentElement.scrollWidth > innerWidth,
  };
}
"""


def _state_body(world: dict, name: str) -> dict:
    stamped = sup.with_stamp(world["state"], sup.render_stamp(world))
    if name == "closed":
        return sup.closed_body(stamped)
    if name == "live":
        return sup.open_body(stamped)
    if name == "opening":
        return sup.open_body(stamped, phase="opening")
    if name == "degraded-open":
        return sup.with_top(sup.open_body(stamped), degraded_reasons=DEGRADED)
    if name == "degraded-closed":
        return sup.with_top(sup.closed_body(stamped), degraded_reasons=DEGRADED)
    if name == "rebalance-pause":
        return sup.open_body(stamped, phase="blackout")
    raise AssertionError(name)


def _dashboard_header(firefox, server, world, name: str, width: int) -> dict:  # noqa: F811
    context = firefox.new_context(viewport={"width": width, "height": HEIGHT})
    probe = new_probe(firefox, server, world, context=context)
    try:
        probe.state = _state_body(world, name)
        probe.page.goto(server.base_url + "/")
        wait_refresh(probe, 1)
        labels, _ = STATES[name]
        wait_for(
            probe,
            "(ls) => ls.includes(document.getElementById('engine-status-label').textContent.trim())",
            f"the header never read one of {sorted(labels)} for the {name} state",
            arg=sorted(labels),
        )
        facts = probe.page.evaluate(_HEADER_FACTS)
        assert facts["label"] in labels, f"{name}: the label reads {facts['label']!r}"
        return facts
    finally:
        context.close()


def _assert_clock_and_chip(facts: dict, name: str, width: int, *, shown: bool) -> None:
    want = "shown" if shown else "hidden"
    for key, what in (("clockShown", "the ET clock"), ("chipShown", "the workspace chip")):
        assert facts[key] is shown, (
            f"{name} at {width} px: {what} should be {want} (label {facts['label']!r})"
        )


@pytest.fixture
def closed_header_height(firefox, world) -> float:  # noqa: F811
    with running_server() as server:
        return _dashboard_header(firefox, server, world, "closed", WIDTH_IN_RANGE)["navHeight"]


@pytest.mark.parametrize("name", [n for n in STATES if n != "rebalance-pause"])
def test_the_clock_and_chip_show_at_1440_in_every_state_but_the_rebalance_pause(
    firefox,  # noqa: F811
    world,  # noqa: F811
    name,
):
    with running_server() as server:
        facts = _dashboard_header(firefox, server, world, name, WIDTH_IN_RANGE)
    _assert_clock_and_chip(facts, name, WIDTH_IN_RANGE, shown=True)
    assert not facts["pageScrollsSideways"], f"{name}: the page scrolls sideways at 1440 px"
    assert facts["brandOneLine"], f"{name}: the wordmark wraps at 1440 px"


def test_the_rebalance_pause_hides_the_clock_and_keeps_the_countdown_whole_at_1440(
    firefox,  # noqa: F811
    world,  # noqa: F811
    closed_header_height,
):
    with running_server() as server:
        facts = _dashboard_header(firefox, server, world, "rebalance-pause", WIDTH_IN_RANGE)
    _assert_clock_and_chip(facts, "rebalance-pause", WIDTH_IN_RANGE, shown=False)
    assert facts["countdownShown"], "the countdown is hidden in a Rebalance pause"
    assert facts["countdownText"].startswith("Next "), facts["countdownText"]
    assert not facts["countdownClipped"], "the countdown's box is narrower than its text"
    assert facts["countdownOnScreen"], "the countdown is cut at the screen's edge"
    assert not facts["countdownCovered"], "another element covers the countdown"
    assert facts["brandOneLine"], "the wordmark wraps in a Rebalance pause"
    assert abs(facts["navHeight"] - closed_header_height) <= HEIGHT_TOLERANCE_PX, (
        f"the header is {facts['navHeight']:.0f} px tall in a Rebalance pause against "
        f"{closed_header_height:.0f} px when closed"
    )
    assert not facts["pageScrollsSideways"]


@pytest.mark.parametrize("width", WIDTHS_ABOVE_RANGE)
@pytest.mark.parametrize("name", list(STATES))
def test_the_clock_and_chip_show_in_every_state_above_1500(firefox, world, name, width):  # noqa: F811
    with running_server() as server:
        facts = _dashboard_header(firefox, server, world, name, width)
    _assert_clock_and_chip(facts, name, width, shown=True)


@pytest.mark.parametrize("width", WIDTHS_BELOW_RANGE)
@pytest.mark.parametrize("name", list(STATES))
def test_the_clock_and_chip_stay_hidden_in_every_state_at_1400_and_below(
    firefox,  # noqa: F811
    world,  # noqa: F811
    name,
    width,
):
    with running_server() as server:
        facts = _dashboard_header(firefox, server, world, name, width)
    _assert_clock_and_chip(facts, name, width, shown=False)


# ---------------------------------------------------------------------------
# A page whose header is read from /api/header-status
# ---------------------------------------------------------------------------

HEADER_PAGE = "/history"
HEADER_PAGE_MOMENTS = {
    "closed": (et(20, 0), None),
    "rebalance-pause": (et(15, 56), None),
}


def serve_header_status(answer: dict):
    """A route handler answering ``/api/header-status`` with the body of ``answer``."""

    def serve(route):
        route.fulfill(status=200, content_type="application/json", body=_json(answer["json"]))

    return serve


def _header_page_facts(firefox, advisor_server, name: str, width: int) -> dict:  # noqa: F811
    moment, reasons = HEADER_PAGE_MOMENTS[name]
    seed_dashboard_state()
    answer = header_answer(moment, reasons=reasons)
    with open_probe(firefox, advisor_server, width=width, height=HEIGHT) as probe:
        probe.page.route("**/api/header-status", serve_header_status(answer))
        open_page(probe, HEADER_PAGE)
        labels, _ = STATES[name]
        wait_until_label(probe, labels)
        facts = probe.page.evaluate(_HEADER_FACTS)
    assert facts["label"] in labels, f"{HEADER_PAGE} {name}: the label reads {facts['label']!r}"
    return facts


def _json(body: dict) -> str:
    import json

    return json.dumps(body)


def wait_until_label(probe, labels: set[str]) -> None:
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    try:
        probe.page.wait_for_function(
            "(ls) => ls.includes(document.getElementById('engine-status-label').textContent.trim())",
            arg=sorted(labels),
            timeout=15_000,
        )
    except PlaywrightTimeout:
        raise AssertionError(f"the header never read one of {sorted(labels)}") from None


def test_a_header_status_page_shows_the_clock_when_closed_at_1440(firefox, advisor_server):  # noqa: F811
    facts = _header_page_facts(firefox, advisor_server, "closed", WIDTH_IN_RANGE)
    _assert_clock_and_chip(facts, f"{HEADER_PAGE} closed", WIDTH_IN_RANGE, shown=True)


def test_a_header_status_page_hides_the_clock_in_a_rebalance_pause_at_1440_only(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
):
    facts = _header_page_facts(firefox, advisor_server, "rebalance-pause", WIDTH_IN_RANGE)
    _assert_clock_and_chip(facts, f"{HEADER_PAGE} rebalance-pause", WIDTH_IN_RANGE, shown=False)
    assert facts["countdownShown"] and not facts["countdownClipped"]
    assert facts["countdownOnScreen"] and not facts["countdownCovered"]
    assert facts["brandOneLine"]
    wide = _header_page_facts(firefox, advisor_server, "rebalance-pause", WIDTHS_ABOVE_RANGE[0])
    _assert_clock_and_chip(
        wide, f"{HEADER_PAGE} rebalance-pause", WIDTHS_ABOVE_RANGE[0], shown=True
    )
