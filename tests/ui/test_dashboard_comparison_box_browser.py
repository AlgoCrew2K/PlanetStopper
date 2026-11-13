"""The dashboard's four comparison rows fill their bordered box and stay inside it, in a real browser.

Browser-only (marker ``browser``; run with ``python -m pytest -m browser -n 0
tests/ui/test_dashboard_comparison_box_browser.py``, a ``-k`` of a width id picks a chunk). Firefox
headless through Playwright. Two worlds:

* the production-shape dashboard (``seeded("dashboard_full")``: the captured droplet records through
  the real writers, the app in this process), at every width of ``al.WIDTHS``, 1001 px included;
* the refresh harness world (``tests/ui/_dashboard_refresh_browser_support``: the real render of the
  harness world, every API answer served by a route handler), where a test names the answer: a poll,
  a window pick, a failing ``/api/state`` or figures at the edge of their formats.

What is measured, per row of ``#portfolio-strip`` (the bordered box):

* every figure of the row (the label, Bot, Held and the alpha figure) lies wholly between the box's
  inner left and right edges: nothing the box clips and nothing that overflows it;
* each of the row's two bars and the row itself (its bottom divider is the row's own border) end
  within 16 px of the box's inner right edge, and never past it: a grid sized to its content, which
  leaves the bars and dividers hundreds of pixels short of the box, fails.

The label-to-Bot gap and the column offsets are the groups ``comparison_rows`` in
``tests/ui/test_aligned_tables_browser.py``; they are not repeated here.

Provenance: the production-shape world is captured droplet output through the real writers. The edge
figures are a labelled construction: the harness world's own ``/api/state`` and ``/api/strip`` answers
with the named figures replaced by made-up values (a negative, a zero, the longest value in the copy
and a dash), so the page's own script draws them.
"""

from __future__ import annotations

import copy
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
from tests.ui._shell_support import (  # noqa: F401  (fixtures: overrides, empty directory, browser)
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    advisor_server,
)
from tests.ui.test_page_explainers_dashboard_browser import (  # noqa: F401  (fixture and helpers)
    POLL_MS,
    _Dashboard,
    seeded,
)

pytestmark = pytest.mark.browser

REACH_PX = 16
EDGE_WIDTHS = (1001, 1120)
MIN_ROWS = 3

# One measure of the box: ``inside`` lists every figure not wholly between the box's inner edges,
# ``reach`` every bar or row ending more than 16 px short of the inner right edge or past it.
BOX_JS = al._script(
    r"""
const out = { name: 'comparison_box', found: 0, inside: [], reach: [], rows: [] };
const box = document.querySelector('#portfolio-strip');
if (!box) { out.inside.push('no #portfolio-strip on the page'); return out; }
const br = box.getBoundingClientRect();
const bs = getComputedStyle(box);
const innerLeft = br.left + (parseFloat(bs.borderLeftWidth) || 0);
const innerRight = br.right - (parseFloat(bs.borderRightWidth) || 0);
const rows = [...box.querySelectorAll(':scope > .vs-row')].filter(shown);
out.found = rows.length;
const FIGURES = [
  ['label', '.vs-row-label'],
  ['Bot', '[data-testid$="-bot-text"]'],
  ['Held', '[data-testid$="-held-text"]'],
  ['alpha', '[data-testid$="-delta"]'],
];
rows.forEach((row, index) => {
  const which = 'row ' + (index + 1) + ' (' + words(row.querySelector('.vs-row-label') || row).slice(0, 24) + ')';
  const record = { which, figures: {}, bars: [] };
  for (const [name, selector] of FIGURES) {
    const el = row.querySelector(selector);
    if (!el || !shown(el)) continue;
    const b = contentBox(el);
    if (!b) continue;
    record.figures[name] = { left: fix(b.left), right: fix(b.right) };
    if (b.left < innerLeft - 0.5) {
      out.inside.push(which + ': the ' + name + ' figure starts ' + fix(innerLeft - b.left) + ' px left of the box');
    }
    if (b.right > innerRight + 0.5) {
      out.inside.push(which + ': the ' + name + ' figure ends ' + fix(b.right - innerRight) + ' px past the box');
    }
  }
  const ends = [['row divider', row.getBoundingClientRect()]];
  row.querySelectorAll('[data-testid="vs-bar"]').forEach((bar, i) => {
    ends.push(['bar ' + (i + 1), bar.getBoundingClientRect()]);
  });
  for (const [name, r] of ends) {
    record.bars.push({ name, right: fix(r.right) });
    if (r.right > innerRight + 0.5) {
      out.reach.push(which + ': the ' + name + ' ends ' + fix(r.right - innerRight) + ' px past the box');
    } else if (innerRight - r.right > 16) {
      out.reach.push(which + ': the ' + name + ' ends ' + fix(innerRight - r.right) + ' px short of the box edge (16 px allowed)');
    }
  }
  out.rows.push(record);
});
return out;
"""
)


def _assert_box(page, where: str) -> None:
    found = al.measure(page, BOX_JS, {})
    assert found["found"] >= MIN_ROWS, f"{where}: {found['found']} comparison rows, premise {MIN_ROWS}"
    assert not found["inside"], f"{where}: " + "; ".join(found["inside"])
    assert not found["reach"], f"{where}: " + "; ".join(found["reach"])


# ---------------------------------------------------------------------------
# The production-shape dashboard at every width
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", al.WIDTHS)
def test_the_comparison_rows_fill_the_box_and_every_figure_lies_inside_it(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,  # noqa: F811
    width,
):
    """Rejects a grid sized to its content (bars and dividers end hundreds of px short of the box at
    1440 and wider) and a grid wider than the box (the alpha cut by the box edge at 1001)."""
    seeded("dashboard_full")
    with _Dashboard(firefox, advisor_server, width, al.VIEWPORT_HEIGHT) as probe:
        _assert_box(probe.page, f"comparison rows at {width}")


# The pages set their text in Manrope, a web font the browser tests never download, so the browser
# measures a narrower system face; the review read the real font, which is wider. A letter spacing
# of 0.4 px on every text stands in for the difference, so a layout that fits only the narrower
# text fails.
WIDER_TEXT_CSS = "body, body * { letter-spacing: 0.4px !important; }"


@pytest.mark.parametrize("width", EDGE_WIDTHS)
def test_the_rows_stay_inside_the_box_with_the_wider_text_of_the_web_font(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,  # noqa: F811
    width,
):
    """Rejects a grid that fits the fallback face and overflows the box in the real font (the review
    saw the alpha cut at 1001 px)."""
    seeded("dashboard_full")
    with _Dashboard(firefox, advisor_server, width, al.VIEWPORT_HEIGHT) as probe:
        probe.page.add_style_tag(content=WIDER_TEXT_CSS)
        probe.page.wait_for_timeout(200)
        found = al.measure(probe.page, BOX_JS, {})
        assert found["found"] >= MIN_ROWS
        assert not found["inside"], f"wider text at {width}: " + "; ".join(found["inside"])


@pytest.mark.parametrize("width", [1001, al.WIDTHS[-2]])
def test_the_rows_keep_filling_the_box_after_three_polls_and_a_window_pick_and_back(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,  # noqa: F811
    width,
):
    """The page script rewrites the figures on each poll and each pick; a layout that holds only
    for the server-rendered text fails here."""
    seeded("dashboard_full")
    with _Dashboard(firefox, advisor_server, width, al.VIEWPORT_HEIGHT) as probe:
        for refresh in range(3):
            probe.page.clock.run_for(POLL_MS)
            probe.page.wait_for_timeout(500)
            _assert_box(probe.page, f"comparison rows at {width} after poll {refresh + 1}")
        for token in ("1y", "30d"):
            probe.page.locator(f'[data-testid="window-{token}"]').click()
            probe.page.wait_for_timeout(1500)
            _assert_box(probe.page, f"comparison rows at {width} on {token}")


# ---------------------------------------------------------------------------
# The refresh harness world: a failing poll and figures at the edge of their formats
# ---------------------------------------------------------------------------


def _open(firefox, server, world, width: int, *, state=None, strips=None):  # noqa: F811
    context = firefox.new_context(viewport={"width": width, "height": al.VIEWPORT_HEIGHT})
    probe = new_probe(firefox, server, world, context=context)
    probe.state = sup.with_stamp(world["state"], sup.render_stamp(world)) if state is None else state
    for token, body in (strips or {}).items():
        probe.strips[token] = body
    probe.page.goto(server.base_url + "/")
    wait_refresh(probe, 1)
    return probe


def _edge_state(world) -> dict:
    """The harness world's poll answer with the Today and Cumulative figures at the edge of their
    formats: a negative against a zero, and the longest value in the copy against a negative."""
    state = sup.with_stamp(world["state"], sup.render_stamp(world))
    strip = state["portfolio_strip"]
    strip["today_change"] = {"dry_run": -9.87, "if_held": 0.0}
    # The page treats any magnitude of 900 or more as the engine's "not set" value and draws a dash,
    # so 899.99 is the longest figure a row can print.
    strip["cumulative_return"] = {"dry_run": 899.99, "if_held": -899.99}
    return state


def _edge_strip(world) -> dict:
    """The 30d strip with the Max DD row's Bot leg at its longest and Held a dash, and the Ann. Vol
    row at its longest value; only the figures named change."""
    strip = copy.deepcopy(world["strips"]["30d"])
    strip["max_drawdown"] = {"dry_run": -99.99, "if_held": None}
    strip["vol_bot"] = 12.345
    strip["vol_held"] = 0.0001
    return strip


@pytest.mark.parametrize("width", EDGE_WIDTHS)
def test_figures_at_the_edge_of_their_formats_lie_inside_the_box(firefox, world, width):  # noqa: F811
    """A negative, a zero, the longest value and a dash, drawn by the page's own script from the
    poll and the strip, at the widths where the box is narrowest."""
    with running_server() as server:
        probe = _open(
            firefox, server, world, width, state=_edge_state(world), strips={"30d": _edge_strip(world)}
        )
        try:
            probe.page.evaluate("window.loadState()")
            wait_refresh(probe, 2)
            wait_for(
                probe,
                "() => document.querySelector('[data-testid=\"comp-cumulative-bot-text\"]')"
                ".textContent.includes('899.99')",
                "the Cumulative row never drew the longest figure",
            )
            _assert_box(probe.page, f"comparison rows with edge figures at {width}")
            assert probe.page_errors == []
        finally:
            probe.context.close()


@pytest.mark.parametrize("width", EDGE_WIDTHS)
def test_dashes_in_every_figure_lie_inside_the_box(firefox, world, width):  # noqa: F811
    """A poll that carries no figure draws a dash in each: the dash keeps its place inside the box."""
    with running_server() as server:
        state = sup.with_stamp(world["state"], sup.render_stamp(world))
        state["portfolio_strip"]["today_change"] = {"dry_run": None, "if_held": None}
        state["portfolio_strip"]["cumulative_return"] = {"dry_run": None, "if_held": None}
        probe = _open(firefox, server, world, width, state=state)
        try:
            probe.page.evaluate("window.loadState()")
            wait_refresh(probe, 2)
            wait_for(
                probe,
                "() => document.querySelector('[data-testid=\"comp-today-bot-text\"]')"
                ".textContent.includes('--')",
                "the Today row never drew a dash",
            )
            _assert_box(probe.page, f"comparison rows with dashes at {width}")
        finally:
            probe.context.close()


@pytest.mark.parametrize("width", EDGE_WIDTHS)
def test_a_poll_the_server_answers_with_500_leaves_the_rows_inside_the_box(
    firefox,  # noqa: F811
    world,  # noqa: F811
    width,
):
    """The page's error state after a failed ``/api/state``: the rows keep their figures or dashes
    and still lie inside the box."""
    failures = []

    def fail(route) -> None:
        failures.append(route.request.url)
        route.fulfill(
            status=500,
            content_type="application/json",
            body=json.dumps({"status": "error", "message": "An internal error occurred"}),
        )

    with running_server() as server:
        context = firefox.new_context(viewport={"width": width, "height": al.VIEWPORT_HEIGHT})
        probe = new_probe(firefox, server, world, context=context)
        try:
            probe.state = fail
            probe.page.goto(server.base_url + "/")
            wait_for(
                probe,
                "() => !!document.querySelector('#portfolio-strip')",
                "the dashboard never drew its comparison box",
            )
            probe.page.wait_for_timeout(1500)
            assert failures, "premise: the page asked for /api/state and the answer was 500"
            _assert_box(probe.page, f"comparison rows after a failed poll at {width}")
        finally:
            probe.context.close()
