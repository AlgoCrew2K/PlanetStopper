"""The dead-ticker warning and notice sit in the dashboard's banner family (marker ``browser``).

Run with ``python -m pytest -m browser -n 0 tests/app/test_ticker_health_banners_browser.py``.
Firefox headless through Playwright. Never skips: a missing Playwright or Firefox fails with a
message.

Contract pinned. The page is the real ``GET /`` render over a stored result written by the real
runner (the warning, one muted notice, and a fleet alert so the fleet banner shows), served to
Firefox with the page's own scripts blocked so nothing moves the boxes after load. Each check reads
the computed style of the box and of its sibling banner and asserts the two are equal, never a pixel
literal:

- The warning box's corner radii, padding, bottom margin and border widths equal the fleet banner's,
  so the warning's text starts at the same left edge as the fleet banner's text.
- The notice box's corner radii, padding, bottom margin, border widths, font size and font weight
  equal the market-state banner's (that banner is hidden by default; its computed values are still
  readable).
- Both boxes' text is left-aligned and wraps inside the box and the page: no box is wider than the
  viewport, and the page does not scroll sideways, at 1440, 390 and 375 px, with a long unbroken
  symphony name in the warning.

Fixture provenance: the stored result comes from the runner over the captured roster, tree and
Alpaca records behind ``tests/advisors/_ticker_health_http_router.py``; the long symphony name is a
derived edit of the captured roster entry; the fleet alert is the captured example shape.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest
import requests

import app as app_module
from tests.advisors import _composer_http_router as h
from tests.advisors.test_ticker_health_runner import X, night, roster
from tests.app.test_ticker_health_dashboard import _fleet_alert
from tests.app.test_ticker_health_dashboard import world as health_world  # noqa: F401  (fixture)
from tests.ui._browser_server import running_server
from tests.ui.test_dashboard_charts_browser import firefox  # noqa: F401  (module-scoped fixture)

pytestmark = pytest.mark.browser

HEIGHT = 900
WIDTHS = [1440, 390, 375]
LONG_UNBROKEN_NAME = "Example" + "UnbrokenSymphonyNameWithNoSpaces" * 4

# Every computed fact of a box the two checks compare, read in one task.
_BOX_FACTS = """
(selector) => {
  const el = document.querySelector(selector);
  const s = getComputedStyle(el);
  const box = el.getBoundingClientRect();
  return {
    present: true,
    radius: [s.borderTopLeftRadius, s.borderTopRightRadius, s.borderBottomRightRadius,
             s.borderBottomLeftRadius].join(' '),
    padding: [s.paddingTop, s.paddingRight, s.paddingBottom, s.paddingLeft].join(' '),
    border: [s.borderTopWidth, s.borderRightWidth, s.borderBottomWidth,
             s.borderLeftWidth].join(' '),
    marginBottom: s.marginBottom,
    fontSize: s.fontSize,
    fontWeight: s.fontWeight,
    textAlign: s.textAlign,
    left: box.left,
    right: box.right,
    clientWidth: el.clientWidth,
    scrollWidth: el.scrollWidth,
  };
}
"""

# The left edge of the first line of text inside an element.
_TEXT_LEFT = """
(selector) => {
  const range = document.createRange();
  range.selectNodeContents(document.querySelector(selector));
  const rects = [...range.getClientRects()].filter((r) => r.width > 0);
  return Math.min(...rects.map((r) => r.left));
}
"""

_PAGE_FACTS = """
() => ({
  scrollWidth: document.documentElement.scrollWidth,
  clientWidth: document.documentElement.clientWidth,
})
"""

WARNING = '[data-testid="ticker-health-warning"]'
NOTICE = '[data-testid="ticker-health-notice"]'
FLEET = "#fleet-correlation-banner"
MARKET = "#market-state-banner"


@pytest.fixture
def stored_page_html(health_world) -> str:  # noqa: F811
    """The page after a night with a dead ticker and a night Alpaca could not be read."""
    world = health_world
    roster(world.router, X, **{X: {"name": LONG_UNBROKEN_NAME}})
    world.run(night(0))
    world.router.alpaca_by_symbol.clear()
    world.router.set_all_alpaca(h.raising(requests.ConnectionError("down")))
    world.run(night(1), status="unavailable")
    alert = {**_fleet_alert(), "dismissed_at_et": None}
    with patch.object(app_module.database, "read_fleet_alert", return_value=alert):
        page = world.page()
    assert page.warning is not None, "the dead-ticker warning is not on the page"
    assert page.notice is not None, "the check notice is not on the page"
    return page.html


@pytest.fixture
def probe(firefox, stored_page_html):  # noqa: F811
    with running_server() as server:
        context = firefox.new_context(viewport={"width": WIDTHS[0], "height": HEIGHT})
        page = context.new_page()
        held: list = []

        def serve(route):
            url = route.request.url
            path = urlsplit(url).path
            if not url.startswith(server.base_url):
                route.abort()
            elif path == "/":
                route.fulfill(
                    status=200, content_type="text/html; charset=utf-8", body=stored_page_html
                )
            elif path.startswith("/static/") and path.endswith(".js"):
                # The page's own scripts answer empty, so nothing moves a box after load.
                route.fulfill(status=200, content_type="text/javascript", body="")
            elif path.startswith("/static/"):
                route.continue_()
            elif path == "/api/events":
                held.append(route)  # never answered: the stream stays connecting
            else:
                route.fulfill(status=200, content_type="application/json", body="{}")

        page.route("**/*", serve)
        try:
            yield SimpleNamespace(page=page, server=server)
        finally:
            context.close()


def _open(probe, width: int) -> None:
    probe.page.set_viewport_size({"width": width, "height": HEIGHT})
    probe.page.goto(probe.server.base_url + "/")
    probe.page.wait_for_selector(WARNING)


def _facts(probe, selector: str) -> dict:
    return probe.page.evaluate(_BOX_FACTS, selector)


def _assert_inside_the_page(probe, facts: dict, label: str) -> None:
    page = probe.page.evaluate(_PAGE_FACTS)
    assert page["scrollWidth"] <= page["clientWidth"], f"{label}: the page scrolls sideways"
    assert facts["right"] <= page["clientWidth"] + 1, f"{label}: the box runs past the viewport"
    assert facts["scrollWidth"] <= facts["clientWidth"], f"{label}: the text overflows the box"
    assert facts["textAlign"] in ("left", "start"), f"{label}: text-align is {facts['textAlign']}"


@pytest.mark.parametrize("width", WIDTHS)
def test_the_warning_box_matches_the_fleet_banner_and_its_text_starts_at_the_same_edge(
    probe, width
):
    _open(probe, width)
    warning, fleet = _facts(probe, WARNING), _facts(probe, FLEET)
    for fact in ("radius", "padding", "marginBottom", "border"):
        assert warning[fact] == fleet[fact], (
            f"{width}px: the warning's {fact} is {warning[fact]!r}, the fleet banner's is "
            f"{fleet[fact]!r}"
        )
    assert warning["left"] == pytest.approx(fleet["left"], abs=0.5), f"{width}px: box left edges"
    warning_text = probe.page.evaluate(_TEXT_LEFT, f"{WARNING} p")
    fleet_text = probe.page.evaluate(_TEXT_LEFT, "#fleet-correlation-banner-text")
    assert warning_text == pytest.approx(fleet_text, abs=0.5), (
        f"{width}px: the warning's text starts at {warning_text}, the fleet banner's at {fleet_text}"
    )
    _assert_inside_the_page(probe, warning, f"{width}px warning")


@pytest.mark.parametrize("width", WIDTHS)
def test_the_notice_box_matches_the_market_state_banner_in_box_and_type(probe, width):
    _open(probe, width)
    notice, market = _facts(probe, NOTICE), _facts(probe, MARKET)
    for fact in ("radius", "padding", "marginBottom", "border", "fontSize", "fontWeight"):
        assert notice[fact] == market[fact], (
            f"{width}px: the notice's {fact} is {notice[fact]!r}, the market-state banner's is "
            f"{market[fact]!r}"
        )
    assert notice["left"] == pytest.approx(market["left"], abs=0.5), f"{width}px: box left edges"
    _assert_inside_the_page(probe, notice, f"{width}px notice")


@pytest.mark.parametrize("width", WIDTHS)
def test_the_sibling_banners_do_not_take_the_new_boxes_values(probe, width):
    """The boxes match their siblings by taking the siblings' values; a sibling edited to match
    a box would pass the checks above, so the siblings' own radius and padding are pinned to each
    other here: the fleet banner and the market-state banner already agree with each other."""
    _open(probe, width)
    fleet, market = _facts(probe, FLEET), _facts(probe, MARKET)
    for fact in ("radius", "padding", "marginBottom", "fontSize", "fontWeight"):
        assert fleet[fact] == market[fact], f"{width}px: the banners disagree on {fact}"
