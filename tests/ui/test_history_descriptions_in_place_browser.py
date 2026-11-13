"""History's descriptions sit under what they describe, never in a column beside it, in a real browser.

Browser-only (marker ``browser``; run with ``python -m pytest -m browser -n 0
tests/ui/test_history_descriptions_in_place_browser.py``, a ``-k`` of a width id picks a chunk).
Firefox headless through Playwright; the app in this process on the per-test database, seeded through
the real writers from the captured post-mortem days (``tests/ui/_shell_support.seed_history_exits``),
the clock fixed as in ``tests/ui/_page_explainers``, every request to anything but the server refused.
Three placements hold at every width of ``al.WIDTHS``, 1001 px included:

* the descriptor under the hero figures (``#total-alpha-descriptor``) is on its own line at the first
  panel's left content edge, below every hero figure, not in a column beside them (the review read it
  in a 380 px column at the row's far right, x=1487 in a 1920 px window while the figures ended at
  x=558);
* the first panel's description (``data-explainer="Y01"``) is the first thing in its panel, at the
  left content edge, with the window picker below it and not beside it (the review read it in a 400 px
  column beside the picker);
* the exits section's description (``data-explainer="Y15"``) is on the line under the exits heading,
  at that panel's left content edge, not on the heading's own row.

Provenance: the captured post-mortem days through the real writers; the text of each description is
the copy table's, read by ``test_page_explainers_performance_history_browser``.
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
from tests.ui.test_page_explainers_performance_history_browser import (  # noqa: F401  (fixture)
    seeded as performance_history_seeded,
)

pytestmark = pytest.mark.browser

LEFT_EDGE_PX = 2
TOP_EDGE_PX = 4
DIRECTLY_UNDER_PX = 24

PLACEMENT_JS = al._script(
    r"""
const out = { name: 'history_descriptions', found: 0, hero: [], first: [], exits: [] };
const content = (el) => {
  const r = el.getBoundingClientRect();
  const s = getComputedStyle(el);
  return { left: r.left + (parseFloat(s.borderLeftWidth) || 0) + (parseFloat(s.paddingLeft) || 0),
           top: r.top + (parseFloat(s.borderTopWidth) || 0) + (parseFloat(s.paddingTop) || 0) };
};
const need = (selector) => { const el = document.querySelector(selector); if (el && shown(el)) out.found++; return el; };

// The hero descriptor: its own line under the figures, at the first panel's left content edge.
const firstPanel = need('[data-testid="history-controls"]');
const descriptor = need('#total-alpha-descriptor');
const figures = need('[data-testid="history-hero"] .hp-figs');
if (!firstPanel || !descriptor || !figures) { out.hero.push('the first panel, the descriptor or the hero figures is not on the page'); return out; }
const panelEdge = content(firstPanel);
const d = contentBox(descriptor);
const f = contentBox(figures);
if (!d || !f) { out.hero.push('the descriptor or the hero figures show no text'); return out; }
if (d.top < f.bottom - 1) out.hero.push('the descriptor starts at y=' + fix(d.top) + ' while the figures end at y=' + fix(f.bottom) + ': it sits beside them, not under them');
if (Math.abs(d.left - panelEdge.left) > 2) out.hero.push('the descriptor starts at x=' + fix(d.left) + ', the panel content edge is x=' + fix(panelEdge.left));

// The first description: the panel's first line at its left edge, the window picker below it.
const y01 = need('[data-explainer="Y01"]');
const picker = need('[data-testid="window-selector"]');
if (!y01 || !picker) { out.first.push('Y01 or the window picker is not on the page'); }
else {
  const y = contentBox(y01);
  const p = picker.getBoundingClientRect();
  if (y.top - panelEdge.top > 4) out.first.push('Y01 starts ' + fix(y.top - panelEdge.top) + ' px below the top of its panel');
  if (Math.abs(y.left - panelEdge.left) > 2) out.first.push('Y01 starts at x=' + fix(y.left) + ', the panel content edge is x=' + fix(panelEdge.left));
  if (p.top < y.bottom - 1) out.first.push('the window picker starts at y=' + fix(p.top) + ' while Y01 ends at y=' + fix(y.bottom) + ': they sit side by side');
}

// The exits description: the line under the exits heading, at its panel's left edge.
const exitsPanel = need('[data-testid="recent-triggers"]');
const heading = need('#todays-exits-heading');
const y15 = need('[data-explainer="Y15"]');
if (!exitsPanel || !heading || !y15) { out.exits.push('the exits panel, its heading or Y15 is not on the page'); return out; }
const exitsEdge = content(exitsPanel);
const h = contentBox(heading);
const n = contentBox(y15);
if (!h || !n) { out.exits.push('the exits heading or Y15 shows no text'); return out; }
if (n.top < h.bottom - 1) out.exits.push('Y15 starts at y=' + fix(n.top) + ' while the heading ends at y=' + fix(h.bottom) + ': it is on the heading row');
if (n.top - h.bottom > 24) out.exits.push('Y15 starts ' + fix(n.top - h.bottom) + ' px under the heading (24 allowed)');
if (Math.abs(n.left - exitsEdge.left) > 2) out.exits.push('Y15 starts at x=' + fix(n.left) + ', the panel content edge is x=' + fix(exitsEdge.left));
return out;
"""
)


def _measure(firefox, advisor_server, width: int) -> dict:  # noqa: F811
    with open_probe(firefox, advisor_server, width=width, height=al.VIEWPORT_HEIGHT) as probe:
        open_page(probe, "/history")
        probe.page.wait_for_timeout(800)
        return al.measure(probe.page, PLACEMENT_JS, {})


@pytest.mark.parametrize("width", al.WIDTHS)
def test_the_hero_descriptor_sits_on_its_own_line_under_the_hero_figures(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    performance_history_seeded,  # noqa: F811
    width,
):
    """Rejects a descriptor in a side column of a two-column hero (x=1487 beside figures ending at
    x=558 at 1920 px)."""
    found = _measure(firefox, advisor_server, width)
    assert found["found"] >= 3
    assert not found["hero"], f"{width}: " + "; ".join(found["hero"])


@pytest.mark.parametrize("width", al.WIDTHS)
def test_the_first_description_is_the_panels_first_line_with_the_picker_below_it(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    performance_history_seeded,  # noqa: F811
    width,
):
    """Rejects a description in a 400 px column beside the window picker."""
    found = _measure(firefox, advisor_server, width)
    assert found["found"] >= 3
    assert not found["first"], f"{width}: " + "; ".join(found["first"])


@pytest.mark.parametrize("width", al.WIDTHS)
def test_the_exits_description_is_the_line_under_the_exits_heading(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    performance_history_seeded,  # noqa: F811
    width,
):
    """Rejects a description placed in the heading's own grid row between the heading and its button."""
    found = _measure(firefox, advisor_server, width)
    assert found["found"] >= 3
    assert not found["exits"], f"{width}: " + "; ".join(found["exits"])
