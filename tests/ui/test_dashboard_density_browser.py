"""The dashboard's density contract in a real browser: a row per symphony, nothing sideways, no tiny text.

Browser-only (marker ``browser``, deselected by default; run with
``python -m pytest -m browser -n 0 tests/ui/test_dashboard_density_browser.py``). Firefox headless
through Playwright; a missing Playwright or Firefox fails with a message naming it, never a skip.

The server is the app in this process on the per-test database, seeded through the real writers
from the captured droplet records (11 symphonies); every request to anything but the server is
refused. Nothing on the page is clicked.

What is asserted, on the live render:

* at 1440 px the cards are single rows, each at most 112 px tall (the card layout it replaced
  measured 319 px), and the dollar-saved panel is a short block of at most 160 px (it was 212);
* at 375 px the document is as wide as the viewport, and no visible text is smaller than 11 px;
* the Today figures are one text per card, the Cash Now control is a visible 28 px button on a
  card that has not exited, and the status words carry a dot and no fill.
"""

from __future__ import annotations

import pytest

from tests.ui._shell_support import (  # noqa: F401
    NARROW,
    WIDE,
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
# The dollar-saved panel was five centred lines (212 px); now its lead and a three-line list.
MAX_DOLLAR_PANEL_HEIGHT_PX = 160
MIN_TEXT_PX = 11

_ROW_FACTS = """
() => [...document.querySelectorAll('[data-testid="sym-card"]')].map((card) => {
  const r = card.getBoundingClientRect();
  return { id: card.getAttribute('data-sym-id'), height: r.height, width: r.width };
})
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

_CASH_NOW = """
() => {
  const card = [...document.querySelectorAll('[data-testid="sym-card"]')]
    .find((c) => !c.querySelector('[data-testid="cash-now-btn"]').disabled);
  if (!card) return null;
  const btn = card.querySelector('[data-testid="cash-now-btn"]');
  const r = btn.getBoundingClientRect();
  const cs = getComputedStyle(btn);
  return { text: btn.textContent.trim(), height: r.height, width: r.width,
           border: cs.borderTopWidth, display: cs.display };
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
        dollar = probe.page.evaluate(
            "() => document.querySelector('[data-testid=\"dollar-saved-panel\"]')"
            ".getBoundingClientRect().height"
        )
    assert rows, "premise: the seeded state renders cards"
    tall = [r for r in rows if r["height"] > MAX_ROW_HEIGHT_PX]
    assert not tall, f"cards taller than {MAX_ROW_HEIGHT_PX} px at 1440: {tall}"
    assert dollar <= MAX_DOLLAR_PANEL_HEIGHT_PX, f"the dollar-saved panel is {dollar} px tall"


def test_the_phone_layout_does_not_scroll_sideways_and_has_no_tiny_text(firefox, advisor_server):  # noqa: F811
    seed_dashboard_state()
    with open_probe(firefox, advisor_server, width=NARROW[0], height=NARROW[1]) as probe:
        open_page(probe, "/")
        probe.page.wait_for_timeout(500)
        scroll, viewport = document_scroll_width(probe)
        small = probe.page.evaluate(_SMALL_TEXT, MIN_TEXT_PX)
    assert scroll == viewport, f"scrolls sideways: {scroll} px in a {viewport} px viewport"
    assert small == [], f"text under {MIN_TEXT_PX} px at 375: {small}"


def test_cash_now_stays_a_visible_button_on_a_card_that_has_not_exited(firefox, advisor_server):  # noqa: F811
    seed_dashboard_state()
    with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1]) as probe:
        open_page(probe, "/")
        button = probe.page.evaluate(_CASH_NOW)
    assert button is not None, "premise: a card that has not exited has an enabled Cash Now"
    assert button["text"] == "Cash Now"
    assert button["height"] == 28
    assert button["border"] == "1px"
    assert button["display"] != "none"


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
