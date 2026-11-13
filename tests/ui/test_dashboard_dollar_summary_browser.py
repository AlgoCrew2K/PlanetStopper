"""The dashboard's dollar summary reads as a sentence beside a stacked column, in a real browser.

Browser-only (marker ``browser``; run with ``python -m pytest -m browser -n 0
tests/ui/test_dashboard_dollar_summary_browser.py``, a ``-k`` of a width id picks a chunk). Firefox
headless through Playwright, on the refresh harness world
(``tests/ui/_dashboard_refresh_browser_support``: the real render of the harness world, every API
answer served by a route handler, so a test names the ``/api/guard-alpha-summary`` answer).

The panel's four figures are the lead sentence ("$X lost across N exits") with its basis meta, and
three lines after it (net of trading costs, realized, realized net of trading costs). Three facts
hold at every width of ``al.WIDTHS`` from 1001 px:

* the lead sentence reads with normal word spacing: two words on one line are no further apart than
  one space of the font plus 1 px, and never touch;
* the basis meta starts on the line under the sentence, not beside it;
* the three other lines stack one under another in one column, beside the lead block, whose left
  edge is 32 to 48 px right of the lead block's right edge; each line, and each figure of the lead,
  is on one line, never broken.

Selectors read: ``[data-testid="dollar-saved-panel"]``, ``.dollar-lead`` (the sentence), ``.dollar-meta``
(its basis meta, inside or after the lead), ``.dollar-line`` (each of the three lines) and the
figures ``.dollar-figure``, ``.dollar-count`` and ``.dollar-sub``.

Provenance: the summary body is the harness world's real ``/api/guard-alpha-summary`` answer with
its totals replaced through ``sup.summary_with`` by made-up values (a lost total, a saved total, a
five-figure total) so the page's own script draws every figure; the dash case answers the route with
a 500.
"""

from __future__ import annotations

import json

import pytest

from tests.dashboard import _refresh_timing_support as sup
from tests.ui import _alignment as al
from tests.ui._browser_server import running_server
from tests.ui._dashboard_refresh_browser_support import (  # noqa: F401  (fixtures)
    firefox,
    new_probe,
    wait_for,
    wait_refresh,
    world,
)

pytestmark = pytest.mark.browser

GAP_MIN_PX = 32
GAP_MAX_PX = 48
SAME_LINE_PX = 4

# Lost, saved and a five-figure total: the formats the headline takes (a magnitude, two decimals).
TOTALS = {
    "lost": (-212.34, 7, (7, 11)),
    "saved": (48.5, 12, (9, 11)),
    "five-figure": (12345.67, 30, (11, 11)),
}

DOLLAR_JS = al._script(
    r"""
const out = { name: 'dollar_summary', found: 0, words: [], spacing: [], meta: [], stack: [], oneLine: [] };
const panel = document.querySelector('[data-testid="dollar-saved-panel"]');
const lead = panel && panel.querySelector('.dollar-lead');
if (!panel || !lead) { out.spacing.push('the page has no dollar summary lead'); return out; }
const meta = panel.querySelector('.dollar-meta');
const ctx = document.createElement('canvas').getContext('2d');
const spaceOf = (el) => {
  const s = getComputedStyle(el);
  ctx.font = s.fontStyle + ' ' + s.fontWeight + ' ' + s.fontSize + ' ' + s.fontFamily;
  return ctx.measureText(' ').width;
};
// The words of the sentence, in reading order, the basis meta left out.
const sentence = [];
const walker = document.createTreeWalker(lead, NodeFilter.SHOW_TEXT);
for (let n = walker.nextNode(); n; n = walker.nextNode()) {
  const holder = n.parentElement;
  if (meta && meta.contains(holder)) continue;
  if (!shown(holder)) continue;
  const re = /\S+/g;
  for (let m = re.exec(n.nodeValue); m; m = re.exec(n.nodeValue)) {
    const range = document.createRange();
    range.setStart(n, m.index);
    range.setEnd(n, m.index + m[0].length);
    const g = range.getClientRects()[0];
    if (!g || g.width <= 0) continue;
    sentence.push({ text: m[0], left: g.left, right: g.right, top: g.top, bottom: g.bottom,
                    space: spaceOf(holder) });
  }
}
out.found = sentence.length;
out.words = sentence.map((w) => w.text);
for (let i = 1; i < sentence.length; i++) {
  const a = sentence[i - 1], b = sentence[i];
  if (Math.abs(a.top - b.top) > 4) continue;  // a new line: no gap to measure
  const gap = b.left - a.right;
  const allowed = Math.max(a.space, b.space) + 1;
  if (gap > allowed) {
    out.spacing.push('"' + a.text + '" and "' + b.text + '" are ' + fix(gap) + ' px apart (a space is ' + fix(allowed - 1) + ' px)');
  } else if (gap < 1) {
    out.spacing.push('"' + a.text + '" and "' + b.text + '" touch (' + fix(gap) + ' px apart)');
  }
}
const leadRight = Math.max(...sentence.map((w) => w.right));
const leadBottom = Math.max(...sentence.map((w) => w.bottom));
const leadTop = Math.min(...sentence.map((w) => w.top));
const metaBox = meta ? contentBox(meta) : null;
if (!metaBox) {
  out.meta.push('the basis meta has no text on the page');
} else if (metaBox.top < leadBottom - 2) {
  out.meta.push('the basis meta starts ' + fix(leadBottom - metaBox.top) + ' px above the end of the sentence, not under it');
}
const blockRight = Math.max(leadRight, metaBox ? metaBox.right : 0);
const lines = [...panel.querySelectorAll('.dollar-line')].filter(shown);
if (lines.length !== 3) out.stack.push(lines.length + ' figure lines after the lead, the page holds 3');
const boxes = lines.map((l) => contentBox(l));
boxes.forEach((b, i) => {
  if (!b) { out.stack.push('figure line ' + (i + 1) + ' has no text'); return; }
  const offset = b.left - blockRight;
  if (offset < 32 - 0.5 || offset > 48 + 0.5) {
    out.stack.push('figure line ' + (i + 1) + ' starts ' + fix(offset) + ' px right of the lead block (32 to 48 allowed)');
  }
  if (i > 0 && boxes[i - 1] && Math.abs(b.left - boxes[0].left) > 2) {
    out.stack.push('figure line ' + (i + 1) + ' starts at x=' + fix(b.left) + ', line 1 at x=' + fix(boxes[0].left) + ' (one column)');
  }
  if (i > 0 && boxes[i - 1] && b.top < boxes[i - 1].bottom - 2) {
    out.stack.push('figure line ' + (i + 1) + ' is not under line ' + i);
  }
});
if (boxes[0] && boxes[0].top > Math.max(leadBottom, metaBox ? metaBox.bottom : 0)) {
  out.stack.push('the column starts under the lead block, not beside it');
}
const oneLine = (el, what) => {
  const tops = [];
  const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  for (let n = w.nextNode(); n; n = w.nextNode()) {
    if (!n.nodeValue.trim() || !shown(n.parentElement)) continue;
    const r = document.createRange();
    r.selectNodeContents(n);
    for (const g of r.getClientRects()) if (g.width > 0) tops.push(g.top);
  }
  if (tops.length && Math.max(...tops) - Math.min(...tops) > 4) out.oneLine.push(what + ' is broken across lines');
};
lines.forEach((l, i) => oneLine(l, 'figure line ' + (i + 1)));
panel.querySelectorAll('.dollar-figure, .dollar-count, .dollar-sub').forEach((el) => {
  if (shown(el)) oneLine(el, 'the figure "' + words(el) + '"');
});
return out;
"""
)


def _assert_summary(page, where: str) -> None:
    found = al.measure(page, DOLLAR_JS, {})
    assert found["found"] >= 4, f"{where}: the lead sentence holds {found['words']}"
    for key, label in (
        ("spacing", "the lead sentence's word spacing"),
        ("meta", "the basis meta"),
        ("stack", "the three figure lines"),
        ("oneLine", "figures on one line"),
    ):
        assert not found[key], f"{where}: {label}: " + "; ".join(found[key])


def _open(firefox, server, world, width: int, *, summary=None, fail_summary=False):  # noqa: F811
    context = firefox.new_context(viewport={"width": width, "height": al.VIEWPORT_HEIGHT})
    probe = new_probe(firefox, server, world, context=context)
    probe.state = sup.with_stamp(world["state"], sup.render_stamp(world))
    if summary is not None:
        probe.summaries["30d"] = summary
    if fail_summary:
        probe.page.route(
            "**/api/guard-alpha-summary*",
            lambda route: route.fulfill(
                status=500,
                content_type="application/json",
                body=json.dumps({"status": "error", "message": "An internal error occurred"}),
            ),
        )
    probe.page.goto(server.base_url + "/")
    wait_refresh(probe, 1)
    if not fail_summary:
        wait_for(
            probe,
            "() => document.getElementById('dollar-saved-verb').textContent.trim() !== ''",
            "the dollar summary never drew its figures",
        )
    probe.page.wait_for_timeout(300)
    return probe


def _total(world, kind: str) -> dict:
    saved, events, coverage = TOTALS[kind]
    return sup.summary_with(world["summaries"]["30d"], saved=saved, events=events, coverage=coverage)


# ---------------------------------------------------------------------------
# The rule at every width, for a lost total (the case the review read)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", al.WIDTHS)
def test_the_lead_sentence_reads_with_normal_word_spacing(firefox, world, width):  # noqa: F811
    """Rejects words laid out as flex items with a column gap (every word 12 px from the next)."""
    with running_server() as server:
        probe = _open(firefox, server, world, width, summary=_total(world, "lost"))
        try:
            found = al.measure(probe.page, DOLLAR_JS, {})
            assert found["found"] >= 4, f"{width}: the lead sentence holds {found['words']}"
            assert not found["spacing"], f"{width}: " + "; ".join(found["spacing"])
        finally:
            probe.context.close()


@pytest.mark.parametrize("width", al.WIDTHS)
def test_the_basis_meta_starts_on_the_line_under_the_sentence(firefox, world, width):  # noqa: F811
    """Rejects a meta laid on the sentence's own line, and one that is not on the page."""
    with running_server() as server:
        probe = _open(firefox, server, world, width, summary=_total(world, "lost"))
        try:
            found = al.measure(probe.page, DOLLAR_JS, {})
            assert found["found"] >= 4, f"{width}: the lead sentence holds {found['words']}"
            assert not found["meta"], f"{width}: " + "; ".join(found["meta"])
        finally:
            probe.context.close()


@pytest.mark.parametrize("width", al.WIDTHS)
def test_the_three_other_lines_stack_in_one_column_32_to_48_px_beside_the_lead(
    firefox,  # noqa: F811
    world,  # noqa: F811
    width,
):
    """Rejects the lines wrapping into rows under the lead, a column further than 48 px away (a grid
    that spreads its columns across the panel) and a column that touches the lead."""
    with running_server() as server:
        probe = _open(firefox, server, world, width, summary=_total(world, "lost"))
        try:
            found = al.measure(probe.page, DOLLAR_JS, {})
            assert found["found"] >= 4, f"{width}: the lead sentence holds {found['words']}"
            assert not found["stack"], f"{width}: " + "; ".join(found["stack"])
        finally:
            probe.context.close()


@pytest.mark.parametrize("width", al.WIDTHS)
def test_no_figure_of_the_summary_breaks_across_lines(firefox, world, width):  # noqa: F811
    """Rejects a figure line that wraps inside itself at the narrowest widths."""
    with running_server() as server:
        probe = _open(firefox, server, world, width, summary=_total(world, "lost"))
        try:
            found = al.measure(probe.page, DOLLAR_JS, {})
            assert found["found"] >= 4, f"{width}: the lead sentence holds {found['words']}"
            assert not found["oneLine"], f"{width}: " + "; ".join(found["oneLine"])
        finally:
            probe.context.close()


# ---------------------------------------------------------------------------
# Figures at the edge of their formats, a dash, a poll and a window pick
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", [1001, 1440])
@pytest.mark.parametrize("kind", ["saved", "five-figure"])
def test_a_saved_and_a_five_figure_total_keep_the_layout(firefox, world, kind, width):  # noqa: F811
    """The rule holds for a total that reads "saved" and for the longest figure the format makes."""
    with running_server() as server:
        probe = _open(firefox, server, world, width, summary=_total(world, kind))
        try:
            _assert_summary(probe.page, f"{kind} total at {width}")
        finally:
            probe.context.close()


@pytest.mark.parametrize("width", [1001, 1440])
def test_a_summary_that_answers_500_keeps_the_layout_with_its_dashes(firefox, world, width):  # noqa: F811
    """Every figure reads as a dash, and the sentence, meta and column keep the rule's layout."""
    with running_server() as server:
        probe = _open(firefox, server, world, width, fail_summary=True)
        try:
            headline = probe.page.locator("#dollar-saved-headline").first.inner_text().strip()
            assert not any(c.isdigit() for c in headline), f"the headline reads {headline!r}"
            _assert_summary(probe.page, f"dashes at {width}")
        finally:
            probe.context.close()


def test_the_layout_holds_after_a_poll_and_a_window_pick_and_back(firefox, world):  # noqa: F811
    """The page script rewrites the figures on a poll and on each pick; each state keeps the rule."""
    with running_server() as server:
        probe = _open(firefox, server, world, 1440, summary=_total(world, "lost"))
        try:
            probe.state = sup.with_stamp(
                world["next"]["state"], sup.later_stamp(sup.render_stamp(world), 1)
            )
            probe.page.evaluate("window.loadState()")
            wait_refresh(probe, 2)
            probe.page.wait_for_timeout(500)
            _assert_summary(probe.page, "after a poll")
            for token in ("1y", "30d"):
                probe.page.locator(f'[data-testid="window-{token}"]').click()
                probe.page.wait_for_timeout(1200)
                _assert_summary(probe.page, f"on {token}")
            assert probe.page_errors == []
        finally:
            probe.context.close()
