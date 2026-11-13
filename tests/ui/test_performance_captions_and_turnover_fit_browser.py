"""Performance's captions sit where they are read and its turnover table fits its panel, in a real browser.

Browser-only (marker ``browser``; run with ``python -m pytest -m browser -n 0
tests/ui/test_performance_captions_and_turnover_fit_browser.py``, a ``-k`` of a width id picks a
chunk). Firefox headless through Playwright; the app in this process on the per-test database, seeded
through the real writers (``tests/ui/_performance_page.seed_world`` for the shadow rows and exits,
``tests/ui/_shell_support.seed_history_exits`` for the captured post-mortem days), the clock fixed
as in ``tests/ui/_page_explainers``, every request to anything but the server refused. Never skips.

* The first panel's three captions (the basis caption, the observation count and the data stamp)
  each start either no more than 24 px after the right edge of the element before them on the same
  line, or on their own line at the panel's left content edge; none sits in a column at the row's
  right side (the review read a 520 px right column from 1240 px, the captions starting at x=1268
  in a 1920 px window while the picker ended near 650).
* The exit turnover table, with the 11 captured symphonies' rows, fits the panel's content width at
  every width of ``al.WIDTHS``, 1001 px included: its right edge lies inside the panel's content
  edge; nothing inside the panel scrolls or hides overflow; every header's text is whole (not cut
  by the panel or by an ancestor that clips) and every drag figure is on screen (the review read
  "Est. annual t" cut at the panel edge and no drag figure visible at 1001 px).
* With no symphony the turnover panel shows its empty state inside its own box.

Provenance: the performance world is the captured shadow rows and post-mortem days through the real
writers; the 11 turnover rows come from the real ``/api/exit-turnover`` answers over them. The empty
state answers ``/api/performance/symphonies`` with an empty list, the shape the route returns when no
symphony is known.
"""

from __future__ import annotations

import json

import pytest

from tests.app.conftest import _reset_scheduler_run_state  # noqa: F401  (autouse, health state)
from tests.ui import _alignment as al
from tests.ui._heading_margin_capture import CAPTURE_INSTANT
from tests.ui._shell_support import (  # noqa: F401  (fixtures: overrides, server, browser)
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    advisor_server,
    firefox,
    open_page,
    open_probe,
    wait_until,
)
from tests.ui.test_page_explainers_performance_history_browser import (  # noqa: F401  (fixture)
    _open,
    _usable_preconditions_body,
)
from tests.ui.test_page_explainers_performance_history_browser import (
    seeded as performance_history_seeded,  # noqa: F401  (fixture)
)

pytestmark = pytest.mark.browser

MAX_CAPTION_GAP_PX = 24
OWN_LINE_EDGE_PX = 2
MIN_TURNOVER_ROWS = 5

CAPTIONS_JS = al._script(
    r"""
const out = { name: 'performance_captions', found: 0, violations: [], boxes: [] };
const panel = document.querySelector('[data-testid="perf-controls-panel"]');
if (!panel) { out.violations.push('no first panel on the page'); return out; }
const pr = panel.getBoundingClientRect();
const ps = getComputedStyle(panel);
const contentLeft = pr.left + (parseFloat(ps.borderLeftWidth) || 0) + (parseFloat(ps.paddingLeft) || 0);
const toolbars = [...panel.querySelectorAll('.hp-toolbar')].filter((t) => !t.classList.contains('perf-captions'));
const captionRow = panel.querySelector('.perf-captions');
if (!captionRow || !toolbars.length) { out.violations.push('the panel holds no caption row or no control row'); return out; }
const captions = [...captionRow.children].filter(shown);
const before = contentBox(toolbars[toolbars.length - 1]);
const boxes = [['the controls', before], ...captions.map((c) => [words(c).slice(0, 28) || c.id, contentBox(c)])];
out.found = captions.length;
for (let i = 1; i < boxes.length; i++) {
  const [name, box] = boxes[i];
  const [prevName, prev] = boxes[i - 1];
  if (!box || !prev) continue;
  out.boxes.push({ name, left: fix(box.left), top: fix(box.top), right: fix(box.right) });
  const sameLine = box.top < prev.bottom - 1 && box.bottom > prev.top + 1;
  if (sameLine) {
    const gap = box.left - prev.right;
    if (gap > 24) out.violations.push('"' + name + '" starts ' + fix(gap) + ' px after "' + prevName + '" on the same line (24 allowed)');
    if (gap < 0) out.violations.push('"' + name + '" overlaps "' + prevName + '"');
  } else if (Math.abs(box.left - contentLeft) > 2) {
    out.violations.push('"' + name + '" is on a line of its own but starts at x=' + fix(box.left) + ', the panel content edge is x=' + fix(contentLeft));
  }
}
return out;
"""
)

TURNOVER_JS = al._script(
    r"""
const out = { name: 'exit_turnover_fit', found: 0, violations: [] };
const panel = document.querySelector('[data-testid="exit-turnover-panel"]');
const table = document.querySelector('[data-testid="exit-turnover-table"]');
if (!panel || !table) { out.violations.push('no turnover panel or table on the page'); return out; }
const pr = panel.getBoundingClientRect();
const ps = getComputedStyle(panel);
const contentLeft = pr.left + (parseFloat(ps.borderLeftWidth) || 0) + (parseFloat(ps.paddingLeft) || 0);
const contentRight = pr.right - (parseFloat(ps.borderRightWidth) || 0) - (parseFloat(ps.paddingRight) || 0);
const tr = table.getBoundingClientRect();
out.found = table.querySelectorAll('tbody tr').length;
if (tr.right > contentRight + 0.5) out.violations.push('the table ends ' + fix(tr.right - contentRight) + ' px past the panel content edge');
if (tr.left < contentLeft - 0.5) out.violations.push('the table starts ' + fix(contentLeft - tr.left) + ' px before the panel content edge');
// Nothing inside the panel scrolls or hides a overflowing box.
for (const el of [panel, ...panel.querySelectorAll('*')]) {
  const cs = getComputedStyle(el);
  if (cs.overflowX !== 'visible' && el.scrollWidth > el.clientWidth + 1) {
    out.violations.push('<' + el.tagName.toLowerCase() + (el.className ? '.' + String(el.className).split(' ')[0] : '') +
      '> scrolls or hides ' + (el.scrollWidth - el.clientWidth) + ' px sideways');
  }
}
// The text of a header or a drag figure is whole: inside the panel and not cut by a clipping ancestor.
const cutBy = (node, r) => {
  for (let e = node.parentElement; e && e !== document.body; e = e.parentElement) {
    const cs = getComputedStyle(e);
    if (cs.overflowX === 'visible') continue;
    const b = e.getBoundingClientRect();
    if (r.right > b.right + 0.5 || r.left < b.left - 0.5) return e.tagName.toLowerCase();
  }
  return null;
};
const whole = (cell, what) => {
  const walker = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT);
  let seen = false;
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (!n.nodeValue.trim()) continue;
    const range = document.createRange();
    range.selectNodeContents(n);
    for (const g of range.getClientRects()) {
      if (g.width <= 0) continue;
      seen = true;
      if (g.right > contentRight + 0.5 || g.left < contentLeft - 0.5) {
        out.violations.push(what + ' "' + words(cell).slice(0, 32) + '" is outside the panel content edge');
        return;
      }
      const by = cutBy(n, g);
      if (by) { out.violations.push(what + ' "' + words(cell).slice(0, 32) + '" is cut by an enclosing <' + by + '>'); return; }
    }
  }
  if (!seen) out.violations.push(what + ' "' + words(cell).slice(0, 32) + '" shows no text');
};
table.querySelectorAll('thead th').forEach((th) => whole(th, 'header'));
table.querySelectorAll('tbody tr').forEach((row) => {
  const cells = [...row.children];
  if (cells.length) whole(cells[cells.length - 1], 'drag figure');
});
return out;
"""
)


def _check(page, script, where: str, *, rows: int | None = None) -> None:
    found = al.measure(page, script, {})
    if rows is not None:
        assert found["found"] >= rows, f"{where}: {found['found']} rows, premise {rows}"
    assert not found["violations"], f"{where}: " + "; ".join(found["violations"])


# ---------------------------------------------------------------------------
# The captions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", al.WIDTHS)
def test_the_basis_caption_and_the_data_stamp_never_sit_in_a_right_hand_column(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    performance_history_seeded,  # noqa: F811
    width,
):
    """Rejects the captions pinned to a 520 px column at the row's far end, which starts them far
    from the element before them and off the panel's left content edge."""
    with open_probe(firefox, advisor_server, width=width, height=al.VIEWPORT_HEIGHT) as probe:
        _open(probe, "/performance", preconditions=(200, _usable_preconditions_body()))
        wait_until(
            probe,
            "() => document.getElementById('performance-data-as-of').textContent.trim() !== ''",
            "Performance never wrote its data stamp",
        )
        found = al.measure(probe.page, CAPTIONS_JS, {})
        assert found["found"] == 3, f"{width}: {found['found']} captions shown, the panel has 3"
        assert not found["violations"], f"{width}: " + "; ".join(found["violations"])


# ---------------------------------------------------------------------------
# The exit turnover table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", al.WIDTHS)
def test_the_exit_turnover_table_fits_its_panel_with_whole_headers_and_every_drag_figure(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    performance_history_seeded,  # noqa: F811
    width,
):
    """Rejects ``white-space: nowrap`` columns that run past the panel behind an in-panel scroll
    (the drag column and its header hidden at 1001 px)."""
    with open_probe(firefox, advisor_server, width=width, height=al.VIEWPORT_HEIGHT) as probe:
        _open(probe, "/performance", preconditions=(200, _usable_preconditions_body()))
        _check(probe.page, TURNOVER_JS, f"exit turnover at {width}", rows=MIN_TURNOVER_ROWS)


@pytest.mark.parametrize("width", [1001, 1120])
def test_the_turnover_table_with_the_longest_figures_still_fits_its_panel(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    performance_history_seeded,  # noqa: F811
    width,
):
    """The widest counts and drag the format makes, from the route's own bodies with the figures
    replaced by made-up values (a labelled construction), at the two narrowest desktop widths."""

    def widest(route) -> None:
        response = route.fetch()
        body = response.json()
        for window in ("30", "90", "365"):
            stats = (body.get("windows") or {}).get(window)
            if isinstance(stats, dict):
                stats["exit_count"] = 99999
                stats["coverage_days"] = 365
        body["est_annual_friction_drag_pct"] = -123.456
        route.fulfill(response=response, json=body)

    with open_probe(firefox, advisor_server, width=width, height=al.VIEWPORT_HEIGHT) as probe:
        probe.page.route("**/api/exit-turnover?*", widest)
        _open(probe, "/performance", preconditions=(200, _usable_preconditions_body()))
        _check(
            probe.page, TURNOVER_JS, f"exit turnover with long figures at {width}", rows=MIN_TURNOVER_ROWS
        )


@pytest.mark.parametrize("width", [1001, 1440])
def test_the_turnover_panel_with_no_symphony_shows_its_empty_state_inside_its_box(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    performance_history_seeded,  # noqa: F811
    width,
):
    with open_probe(firefox, advisor_server, width=width, height=al.VIEWPORT_HEIGHT) as probe:
        probe.context.clock.install(time=CAPTURE_INSTANT)
        probe.page.route(
            "**/api/performance/symphonies",
            lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps([])
            ),
        )
        open_page(probe, "/performance")
        wait_until(
            probe,
            "() => { const e = document.getElementById('exit-turnover-empty-state');"
            " return !!e && getComputedStyle(e).display !== 'none'; }",
            "the empty turnover state never showed",
        )
        facts = probe.page.evaluate(
            """() => {
              const panel = document.querySelector('[data-testid="exit-turnover-panel"]').getBoundingClientRect();
              const note = document.getElementById('exit-turnover-empty-state');
              const range = document.createRange();
              range.selectNodeContents(note);
              const text = range.getBoundingClientRect();
              return { panelLeft: panel.left, panelRight: panel.right, left: text.left, right: text.right,
                       text: note.textContent.trim(), rows: document.querySelectorAll('#exit-turnover-tbody tr').length };
            }"""
        )
        assert facts["rows"] == 0, f"premise: no turnover row, {facts['rows']} drawn"
        assert facts["text"], "the empty state has no text"
        assert facts["panelLeft"] <= facts["left"] and facts["right"] <= facts["panelRight"] + 0.5, (
            f"the empty state runs outside its panel: {facts}"
        )
