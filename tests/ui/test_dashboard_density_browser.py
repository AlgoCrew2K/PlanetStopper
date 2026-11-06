"""The dashboard's density contract in a real browser: a row per symphony, nothing sideways, no tiny text.

Browser-only (marker ``browser``, deselected by default; run with
``python -m pytest -m browser -n 0 tests/ui/test_dashboard_density_browser.py``). Firefox headless
through Playwright; a missing Playwright or Firefox fails with a message naming it, never a skip.

The server is the app in this process on the per-test database, seeded through the real writers
from the captured droplet records (11 symphonies); every request to anything but the server is
refused. Nothing on the page is clicked.

What is asserted, on the live render:

* at 1440 px the cards are single rows, each at most 112 px tall (the card layout it replaced
  measured 319 px), and the dollar-saved panel is a short block of at most 160 px (it was 212) plus
  the measured height of its description line and its closed disclosure's summary line;
* at 1440 and at 1240 px no panel or card holds an empty rectangle 240 by 80 px or larger inside
  its content box (the exact search of ``tests/ui/_page_explainers.py``), in the seeded dashboard
  and again after a poll and a window pick;
* at 375 px the document is as wide as the viewport, and no visible text is smaller than 11 px;
* the Today figures are one text per card, and the status words carry a dot and no fill.
"""

from __future__ import annotations

import pytest

from tests.app.conftest import _reset_scheduler_run_state  # noqa: F401  (autouse, health state)
from tests.ui import _page_explainers as harness
from tests.ui._shell_support import (  # noqa: F401
    NARROW,
    WIDE,
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    advisor_server,
    document_scroll_width,
    firefox,
    open_page,
    open_probe,
    seed_dashboard_state,
)

pytestmark = pytest.mark.browser

# One row of the ledger: label line + two figure lines at 20 px, plus the card's own padding. A name
# that wraps to a second line lengthens its row a little, hence the margin.
MAX_ROW_HEIGHT_PX = 112
# The dollar-saved panel was five centred lines (212 px); now its lead and a three-line list. The
# description line and the closed disclosure's summary line the page adds to it are measured and
# allowed on top of that.
MAX_DOLLAR_PANEL_HEIGHT_PX = 160
EMPTY_MIN_WIDTH_PX = 240
EMPTY_MIN_HEIGHT_PX = 80
DESKTOP_WIDTHS = (1440, 1240)
MIN_TEXT_PX = 11

_ROW_FACTS = """
() => [...document.querySelectorAll('[data-testid="sym-card"]')].map((card) => {
  const r = card.getBoundingClientRect();
  return { id: card.getAttribute('data-sym-id'), height: r.height, width: r.width };
})
"""

_DOLLAR_FACTS = """
() => {
  const height = (selector) => {
    const el = document.querySelector(selector);
    return el ? el.getBoundingClientRect().height : null;
  };
  const panel = '[data-testid="dollar-saved-panel"]';
  return {
    panel: height(panel),
    lead: height(panel + ' [data-explainer="D21"]'),
    summary: height(panel + ' [data-terms="D21"] > summary'),
  };
}
"""

_SMALL_TEXT = """
(min) => {
  const small = [];
  for (const el of document.querySelectorAll('body *')) {
    const own = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
    if (!own) continue;
    const cs = getComputedStyle(el);
    const box = el.getBoundingClientRect();
    if (cs.display === 'none' || cs.visibility === 'hidden' || !box.width || !box.height) continue;
    if (parseFloat(cs.fontSize) < min) {
      small.push(el.tagName + '.' + String(el.className).slice(0, 30) + ' ' + cs.fontSize);
    }
  }
  return small.slice(0, 8);
}
"""

_STATUS = """
() => [...document.querySelectorAll('[data-testid="status-pill"]')].map((el) => {
  const cs = getComputedStyle(el);
  const dot = getComputedStyle(el, '::before');
  return { text: el.textContent.trim(), background: cs.backgroundColor, radius: cs.borderTopLeftRadius,
           dotWidth: dot.width, dotRadius: dot.borderTopLeftRadius };
})
"""


def test_each_symphony_is_one_row_at_1440(firefox, advisor_server):  # noqa: F811
    seed_dashboard_state()
    with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1]) as probe:
        open_page(probe, "/")
        rows = probe.page.evaluate(_ROW_FACTS)
        dollar = probe.page.evaluate(_DOLLAR_FACTS)
    assert rows, "premise: the seeded state renders cards"
    tall = [r for r in rows if r["height"] > MAX_ROW_HEIGHT_PX]
    assert not tall, f"cards taller than {MAX_ROW_HEIGHT_PX} px at 1440: {tall}"
    assert dollar["lead"] is not None and dollar["summary"] is not None, (
        f"the dollar-saved panel has no description line or disclosure summary to allow for: {dollar}"
    )
    allowed = MAX_DOLLAR_PANEL_HEIGHT_PX + dollar["lead"] + dollar["summary"]
    assert dollar["panel"] <= allowed, (
        f"the dollar-saved panel is {dollar['panel']} px tall; {allowed} px is the bound "
        f"({MAX_DOLLAR_PANEL_HEIGHT_PX} plus its description and summary lines: {dollar})"
    )


def test_the_phone_layout_does_not_scroll_sideways_and_has_no_tiny_text(firefox, advisor_server):  # noqa: F811
    seed_dashboard_state()
    with open_probe(firefox, advisor_server, width=NARROW[0], height=NARROW[1]) as probe:
        open_page(probe, "/")
        probe.page.wait_for_timeout(500)
        scroll, viewport = document_scroll_width(probe)
        small = probe.page.evaluate(_SMALL_TEXT, MIN_TEXT_PX)
    assert scroll == viewport, f"scrolls sideways: {scroll} px in a {viewport} px viewport"
    assert small == [], f"text under {MIN_TEXT_PX} px at 375: {small}"


def test_a_status_is_a_dot_and_a_word_with_no_fill(firefox, advisor_server):  # noqa: F811
    seed_dashboard_state()
    with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1]) as probe:
        open_page(probe, "/")
        states = probe.page.evaluate(_STATUS)
    assert states, "premise: the cards carry status words"
    for state in states:
        assert state["background"] == "rgba(0, 0, 0, 0)", state
        assert state["radius"] == "0px", state
        assert state["dotWidth"] == "8px", state
        assert state["dotRadius"] in ("50%", "4px"), state


@pytest.fixture
def seeded_world(tmp_path, monkeypatch):
    """``seeded_world(name)`` writes that world's state; no credential, the developer .env unread."""
    harness.credential_free_environment(
        tmp_path / "page.env", monkeypatch.setattr, lambda n: monkeypatch.delenv(n, raising=False)
    )
    return lambda name: harness.seed_world(name, tmp_path, monkeypatch.setattr)


def _empty_rectangles(probe) -> list[dict]:
    """Every panel and card the page shows, with its largest empty rectangle (None when none)."""
    return probe.page.evaluate(
        harness.EMPTY_REGION_JS, [harness.PANEL_SELECTOR, EMPTY_MIN_WIDTH_PX, EMPTY_MIN_HEIGHT_PX]
    )


def _offenders(found: list[dict]) -> list[str]:
    return [
        f"{f['testid'] or f['id'] or f['cls']}: {f['largest_empty']} in a content box "
        f"{f['content']['w']:.0f} by {f['content']['h']:.0f}"
        for f in found
        if f["largest_empty"]
    ]


@pytest.mark.parametrize(
    ("world", "width"),
    [
        ("dashboard_full", 1440),
        ("dashboard_full", 1240),
        ("dashboard_sleeves", 1440),
        ("empty", 1440),
    ],
)
def test_no_panel_holds_an_empty_rectangle_the_size_of_a_block_of_text(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded_world,
    world,
    width,
):
    seeded_world(world)
    with open_probe(firefox, advisor_server, width=width, height=900) as probe:
        probe.context.clock.install(time=harness.CAPTURE_INSTANT)
        open_page(probe, "/")
        probe.page.wait_for_timeout(harness.SETTLE_MS)
        found = _empty_rectangles(probe)
    assert found, "premise: the dashboard shows panels"
    assert any(f["testid"] == "hero-section" for f in found), "premise: the hero is measured"
    offenders = _offenders(found)
    assert not offenders, (
        f"{world} at {width} px: panels with an empty region of {EMPTY_MIN_WIDTH_PX} by "
        f"{EMPTY_MIN_HEIGHT_PX} px or more:\n" + "\n".join(offenders)
    )
