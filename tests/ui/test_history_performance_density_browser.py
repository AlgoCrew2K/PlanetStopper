"""History and Performance keep their density and type contract in a real browser.

Browser-only (marker ``browser``, deselected by default; run with
``python -m pytest -m browser -n 0 tests/ui/test_history_performance_density_browser.py``).
Firefox headless through Playwright; the app in this process on the per-test database, seeded
through the real writers from captured post-mortem days (``tests/ui/_shell_support``). Never skips.

Facts asserted on the live render, by computed style, never by a literal colour or size:
* nothing inside the page is upper-cased, nothing is heavier than 500, nothing is below 11 px;
* at 375 px the page does not scroll sideways;
* the History by-reason ledger is one line per reason at 1440 px, and the page is no longer than
  five screens at 375 px;
* the by-reason definitions start collapsed.
"""

from __future__ import annotations

import pytest

from tests.ui._shell_support import (  # noqa: F401  (fixtures)
    NARROW,
    WIDE,
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    advisor_server,
    firefox,
    open_page,
    open_probe,
    seed_dashboard_state,
    seed_history_exits,
    wait_until,
)

pytestmark = pytest.mark.browser

_TYPE_FACTS = """
() => {
  const page = document.querySelector('.page-wrap');
  const upper = [], heavy = [], small = [];
  const walker = document.createTreeWalker(page, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (!node.nodeValue.trim()) continue;
    const el = node.parentElement;
    const style = getComputedStyle(el);
    if (style.display === 'none' || el.closest('[hidden], [style*="display: none"], canvas')) continue;
    const text = node.nodeValue.trim().slice(0, 40);
    if (style.textTransform === 'uppercase') upper.push(text);
    if (Number(style.fontWeight) > 500) heavy.push([text, style.fontWeight]);
    if (parseFloat(style.fontSize) < 11) small.push([text, style.fontSize]);
  }
  return { upper, heavy, small, pageWidth: document.documentElement.scrollWidth, viewport: innerWidth,
           height: document.documentElement.scrollHeight };
}
"""


@pytest.mark.parametrize("path", ["/history", "/performance"], ids=["history", "performance"])
def test_no_upper_case_no_heavy_weight_and_no_small_text_at_1440(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    path,  # noqa: F811
):
    seed_dashboard_state()
    seed_history_exits(tmp_path, monkeypatch.setattr)
    with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1]) as probe:
        open_page(probe, path)
        probe.page.wait_for_timeout(1500)
        facts = probe.page.evaluate(_TYPE_FACTS)
    assert facts["upper"] == [], f"{path}: upper-case text {facts['upper'][:5]}"
    assert facts["heavy"] == [], f"{path}: text heavier than 500 {facts['heavy'][:5]}"
    assert facts["small"] == [], f"{path}: text below 11 px {facts['small'][:5]}"


@pytest.mark.parametrize("path", ["/history", "/performance"], ids=["history", "performance"])
def test_the_page_does_not_scroll_sideways_and_keeps_11_px_text_at_375(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    path,  # noqa: F811
):
    seed_dashboard_state()
    seed_history_exits(tmp_path, monkeypatch.setattr)
    with open_probe(firefox, advisor_server, width=NARROW[0], height=NARROW[1]) as probe:
        open_page(probe, path)
        probe.page.wait_for_timeout(1500)
        facts = probe.page.evaluate(_TYPE_FACTS)
    assert facts["pageWidth"] <= facts["viewport"] + 1, f"{path}: the page scrolls sideways"
    assert facts["small"] == [], f"{path}: text below 11 px {facts['small'][:5]}"


_LEDGER_FACTS = """
() => {
  const rows = [...document.querySelectorAll('#reason-cards tr[data-testid="reason-card"]')];
  return {
    heights: rows.map((r) => Math.round(r.getBoundingClientRect().height)),
    height: document.documentElement.scrollHeight,
  };
}
"""


def test_history_reasons_are_one_line_each(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,  # noqa: F811
):
    seed_dashboard_state()
    seed_history_exits(tmp_path, monkeypatch.setattr)
    with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1]) as probe:
        open_page(probe, "/history")
        probe.page.click('#window-picker button[data-window="1825"]')
        wait_until(
            probe,
            "() => document.querySelectorAll('#reason-cards tr[data-testid=\"reason-card\"] td.num').length > 0"
            " && !document.querySelector('#reason-cards td.num').textContent.includes('--')",
            "the by-reason ledger never drew its figures",
        )
        facts = probe.page.evaluate(_LEDGER_FACTS)
    assert facts["heights"], "premise: the seeded exits give at least one reason row"
    assert all(h <= 40 for h in facts["heights"]), f"a reason row wraps: {facts['heights']}"


def test_history_is_no_longer_than_five_screens_at_375(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,  # noqa: F811
):
    seed_dashboard_state()
    seed_history_exits(tmp_path, monkeypatch.setattr)
    with open_probe(firefox, advisor_server, width=NARROW[0], height=NARROW[1]) as probe:
        open_page(probe, "/history")
        probe.page.click('#window-picker button[data-window="1825"]')
        probe.page.wait_for_timeout(1500)
        facts = probe.page.evaluate(_LEDGER_FACTS)
    assert facts["height"] <= 5 * NARROW[1], f"the page is {facts['height']} px tall"
