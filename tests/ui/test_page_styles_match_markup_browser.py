"""Every disclosure rule of a page's own stylesheet still matches an element of that page, in a real browser.

Browser-only (marker ``browser``; run with ``python -m pytest -m browser -n 0
tests/ui/test_page_styles_match_markup_browser.py``). Firefox headless through Playwright; the app in
this process on the per-test database, seeded through the real writers (the dashboard world of
``tests/ui/_page_explainers``, the captured shadow rows and post-mortem days for Performance and
History), every request to anything but the server refused. Never skips.

Before this change the dashboard carried "What the terms mean" disclosures in its hero, its dollar
panel and its sleeves panel, and its stylesheet placed each by a selector naming ``.hp-more`` beside
its neighbours. The disclosures are gone (the one disclosure left on a page is its Glossary, the last
element), so a selector that places a ``.hp-more`` beside a hero heading, a description or a panel the
Glossary is not in styles nothing. The page's own ``<style>`` blocks are walked, media rules included,
and each selector of a list that names ``.hp-more`` must match an element of the rendered page.

The static counterparts (a class or id the page no longer builds, a grid area nothing fills, a
comment describing removed markup) are ``test_page_styles_leave_no_dead_rules``.
"""

from __future__ import annotations

import pytest

from tests.app.conftest import _reset_scheduler_run_state  # noqa: F401  (autouse, health state)
from tests.ui import _alignment as al
from tests.ui._shell_support import (  # noqa: F401  (fixtures: overrides, server, browser)
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    advisor_server,
    firefox,
    open_page,
    open_probe,
)
from tests.ui.test_page_explainers_dashboard_browser import (  # noqa: F401  (fixture and helpers)
    _Dashboard,
    seeded,
)
from tests.ui.test_page_explainers_performance_history_browser import (  # noqa: F401  (fixture)
    seeded as performance_history_seeded,
)

pytestmark = pytest.mark.browser

WIDTH = 1440

UNMATCHED_JS = """
(needle) => {
  const out = [];
  const topLevel = (text) => {
    const parts = []; let depth = 0; let current = '';
    for (const ch of text) {
      if ('([' .includes(ch)) depth++;
      if (')]'.includes(ch)) depth--;
      if (ch === ',' && depth === 0) { parts.push(current.trim()); current = ''; } else current += ch;
    }
    parts.push(current.trim());
    return parts.filter(Boolean);
  };
  const walk = (rules) => {
    for (const rule of rules) {
      if (rule.cssRules && !rule.selectorText) { walk(rule.cssRules); continue; }
      if (!rule.selectorText) continue;
      for (const part of topLevel(rule.selectorText)) {
        if (!part.includes(needle)) continue;
        const live = part.replace(/::[\\w-]+(\\([^)]*\\))?/g, '');
        let matched = false;
        try { matched = document.querySelector(live) !== null; } catch (e) { matched = true; }
        if (!matched) out.push(part);
      }
    }
  };
  for (const sheet of document.styleSheets) {
    if (!sheet.ownerNode || sheet.ownerNode.tagName !== 'STYLE') continue;
    try { walk(sheet.cssRules); } catch (e) { out.push('unreadable sheet: ' + e); }
  }
  return out;
}
"""


def test_the_dashboards_own_disclosure_rules_each_match_an_element(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,  # noqa: F811
):
    """Rejects the rules that placed the removed terms disclosures: ``.hero-top .hp-more``,
    ``[data-explainer] + .hp-more``, ``.dollar-panel > .hp-more`` and the sleeves panel's
    ``> details.hp-more``."""
    seeded("dashboard_full")
    with _Dashboard(firefox, advisor_server, WIDTH, al.VIEWPORT_HEIGHT) as probe:
        unmatched = probe.page.evaluate(UNMATCHED_JS, ".hp-more")
    assert not unmatched, (
        "the dashboard's stylesheet places disclosures that are gone:\n" + "\n".join(unmatched)
    )


@pytest.mark.parametrize("path", ["/history", "/performance"])
def test_history_and_performance_own_disclosure_rules_each_match_an_element(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    performance_history_seeded,  # noqa: F811
    path,
):
    with open_probe(firefox, advisor_server, width=WIDTH, height=al.VIEWPORT_HEIGHT) as probe:
        open_page(probe, path)
        unmatched = probe.page.evaluate(UNMATCHED_JS, ".hp-more")
    assert not unmatched, f"{path} styles disclosures that are gone:\n" + "\n".join(unmatched)
