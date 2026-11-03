"""The shared chrome in a real browser: slim, dense, quiet, on every page.

Browser-only (marker ``browser``; run with
``python -m pytest -m browser -n 0 tests/ui/test_chrome_look_browser.py``). Firefox headless
through Playwright; the server is the app in this process on the per-test database
(``tests/ui/_shell_support``), seeded through the real writers from captured records, every request
to anything but that server refused.

Density and look facts asserted on a live render, per page:

* 1440 px: the header is one row, no taller than ``WIDE_HEADER_MAX_PX`` (it was 58 px), and the
  page does not scroll sideways.
* 375 px: the header is no taller than ``NARROW_HEADER_MAX_PX`` (it was 146 px) and takes at most
  three rows; Force run now, Emergency Liquidate and the five links are all reachable; no text in
  it is below 11 px; the page does not scroll sideways.
* The mode is a dot and a word (no fill, no rounding), shown as ``Dry run``.
* A status on a card is a dot and a word (no fill, no rounding); a row of stat figures has no box.
* The tweaks panel opens from the trigger, its five swatches share one row, and the panel has no
  blur.
* The panic Confirm button is dimmed until the phrase is typed (nothing is sent).
"""

from __future__ import annotations

import pytest

from tests.ui._shell_support import (  # noqa: F401
    FORCE_RUN,
    HEADER_CONTROLS,
    LIQUIDATE,
    MODE_PILL,
    NARROW,
    PAGE_IDS,
    PAGES,
    WIDE,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    advisor_server,
    assert_reachable,
    document_scroll_width,
    firefox,
    nav_height,
    nav_rows,
    open_page,
    open_probe,
    seed_dashboard_state,
)

pytestmark = pytest.mark.browser

WIDE_HEADER_MAX_PX = 56
NARROW_HEADER_MAX_PX = 124
MIN_PHONE_TEXT_PX = 11

_HEADER_TEXT_SIZES = (
    """
() => {
  const top = document.querySelector('[data-testid="top-bar"]');
  const small = [];
  for (const el of top.querySelectorAll('*')) {
    const hasText = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
    if (!hasText) continue;
    const style = getComputedStyle(el);
    if (style.display === 'none' || el.getBoundingClientRect().width === 0) continue;
    if (parseFloat(style.fontSize) < %d) small.push([el.id || el.className || el.tagName, style.fontSize]);
  }
  return small;
}
"""
    % MIN_PHONE_TEXT_PX
)


@pytest.mark.parametrize("path", PAGES, ids=PAGE_IDS)
def test_the_header_is_one_slim_row_at_1440(firefox, advisor_server, path):
    seed_dashboard_state()
    with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1]) as probe:
        open_page(probe, path)
        assert nav_rows(probe) == 1
        assert nav_height(probe) <= WIDE_HEADER_MAX_PX, nav_height(probe)
        scroll, viewport = document_scroll_width(probe)
        assert scroll <= viewport, (scroll, viewport)


@pytest.mark.parametrize("path", PAGES, ids=PAGE_IDS)
def test_the_header_is_compact_and_every_control_is_reachable_at_375(firefox, advisor_server, path):
    seed_dashboard_state()
    with open_probe(firefox, advisor_server, width=NARROW[0], height=NARROW[1]) as probe:
        open_page(probe, path)
        assert nav_rows(probe) <= 3, nav_rows(probe)
        assert nav_height(probe) <= NARROW_HEADER_MAX_PX, nav_height(probe)
        for label, selector in HEADER_CONTROLS.items():
            assert_reachable(probe, label, selector, f"{path} at {NARROW[0]} px")
        for href in ("/", "/performance", "/history", "/settings", "/ai-advisor"):
            assert_reachable(
                probe, f"link {href}", f'[data-testid="studio-nav"] a[href="{href}"]', path
            )
        assert probe.page.evaluate(_HEADER_TEXT_SIZES) == []
        scroll, viewport = document_scroll_width(probe)
        assert scroll <= viewport, (scroll, viewport)


@pytest.mark.parametrize("path", PAGES, ids=PAGE_IDS)
def test_the_two_action_buttons_are_one_size_at_1440(firefox, advisor_server, path):
    seed_dashboard_state()
    with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1]) as probe:
        open_page(probe, path)
        sizes = probe.page.evaluate(
            """(sels) => sels.map((s) => { const r = document.querySelector(s).getBoundingClientRect();
                                           return Math.round(r.height); })""",
            [FORCE_RUN, LIQUIDATE],
        )
        assert sizes[0] == sizes[1] == 32, sizes


def test_the_mode_is_a_dot_and_a_word_not_a_pill(firefox, advisor_server):
    seed_dashboard_state()
    with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1]) as probe:
        open_page(probe, "/history")
        facts = probe.page.evaluate(
            """(sel) => { const el = document.querySelector(sel); const s = getComputedStyle(el);
                          return { text: el.innerText.trim(), live: el.getAttribute('data-live'),
                                   dot: s.backgroundImage, fill: s.backgroundColor,
                                   radius: s.borderTopLeftRadius, weight: s.fontWeight }; }""",
            MODE_PILL,
        )
        assert facts["text"] == "Dry run" and facts["live"] == "false", facts
        assert facts["dot"].startswith("radial-gradient"), facts
        assert facts["fill"] in ("rgba(0, 0, 0, 0)", "transparent"), facts
        assert facts["radius"] == "0px" and facts["weight"] == "500", facts


def test_a_card_status_is_a_dot_and_a_word_and_stats_have_no_box(firefox, advisor_server):
    seed_dashboard_state()
    with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1]) as probe:
        open_page(probe, "/")
        facts = probe.page.evaluate(
            """() => {
              const pill = document.querySelector('.status-pill');
              const ps = getComputedStyle(pill);
              const dot = getComputedStyle(pill, '::before');
              const stat = getComputedStyle(document.querySelector('.mini-stat'));
              const card = getComputedStyle(document.querySelector('.sym-card'));
              return { pillFill: ps.backgroundColor, pillRadius: ps.borderTopLeftRadius,
                       dotW: dot.width, dotH: dot.height, dotRadius: dot.borderTopLeftRadius,
                       statFill: stat.backgroundColor, statTop: stat.borderTopWidth,
                       cardShadow: card.boxShadow };
            }"""
        )
        clear = ("rgba(0, 0, 0, 0)", "transparent")
        assert facts["pillFill"] in clear and facts["pillRadius"] == "0px", facts
        assert facts["dotW"] == facts["dotH"] == "8px" and facts["dotRadius"] == "50%", facts
        assert facts["statFill"] in clear and facts["statTop"] == "1px", facts
        assert facts["cardShadow"] == "none", facts


@pytest.mark.parametrize("width, height", [WIDE, NARROW], ids=["1440", "375"])
def test_the_tweaks_panel_opens_with_its_swatches_in_one_row_and_no_blur(
    firefox, advisor_server, width, height
):
    seed_dashboard_state()
    with open_probe(firefox, advisor_server, width=width, height=height) as probe:
        open_page(probe, "/history")
        probe.page.click('[data-testid="tweaks-btn"]')
        probe.page.wait_for_selector("#studio-tweaks-panel", state="visible")
        facts = probe.page.evaluate(
            """() => {
              const panel = document.getElementById('studio-tweaks-panel');
              const tops = [...panel.querySelectorAll('[data-testid="accent-swatch"]')]
                .map((b) => Math.round(b.getBoundingClientRect().top));
              const r = panel.getBoundingClientRect();
              return { tops, blur: getComputedStyle(panel).backdropFilter,
                       inside: r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight };
            }"""
        )
        assert len(facts["tops"]) == 5 and len(set(facts["tops"])) == 1, facts
        assert facts["blur"] in ("none", ""), facts
        assert facts["inside"], facts


def test_the_confirm_button_is_dimmed_until_the_phrase_is_typed(firefox, advisor_server):
    seed_dashboard_state()
    with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1]) as probe:
        open_page(probe, "/history")
        probe.page.click(LIQUIDATE)
        probe.page.wait_for_selector("#panic-modal", state="visible")
        opacity = lambda: probe.page.evaluate(  # noqa: E731
            "() => getComputedStyle(document.getElementById('panic-confirm-btn')).opacity"
        )
        assert probe.page.is_disabled("#panic-confirm-btn")
        assert float(opacity()) == pytest.approx(0.4)
        probe.page.fill("#panic-phrase", "LIQUIDATE")
        assert not probe.page.is_disabled("#panic-confirm-btn")
        assert float(opacity()) == pytest.approx(1.0)
        probe.page.fill("#panic-phrase", "")
        assert probe.page.is_disabled("#panic-confirm-btn")
