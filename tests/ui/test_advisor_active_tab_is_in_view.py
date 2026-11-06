"""The active AI Advisor tab is scrolled into view inside its row, on load and on every switch.

``static/ai_advisor.js`` runs unmodified over the page the real app renders
(tests/ui/_page_js_harness.py). On a phone the tab row scrolls sideways, so a tab past the first few
is off screen unless the page brings it in: the operator then cannot see which tab is open.

The harness DOM has no layout, so each scenario gives the tab row a geometry: every tab is 120 px
wide inside a 375 px row that starts scrolled to its left edge, and ``scrollIntoView`` on a tab moves
the row's ``scrollLeft`` the way the specification says for ``inline`` ``nearest``, ``center``,
``start`` and ``end``. The tab's bounding box follows ``scrollLeft`` too, so a script that reads
geometry and sets ``scrollLeft`` itself is judged on the same model. The model claims nothing about
a browser's smooth scrolling or snapping.

Contract pinned
* After load on a fragment naming a tab past the visible part of the row, that tab is wholly inside
  the row.
* After a click on any tab, and after a fragment change to any tab, it is wholly inside the row.
* Selecting a tab never scrolls the page itself: ``scrollIntoView`` is only used with
  ``block: 'nearest'`` (the default ``start`` would jump the page on every switch).
* A fix that only sets the active class leaves the tab off screen and fails.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.ui._page_js_harness import render_page, route, run_page

TAB_WIDTH = 120
ROW_WIDTH = 375
FIRST_HIDDEN_INDEX = ROW_WIDTH // TAB_WIDTH + 1

GEOMETRY = r"""
const tabs = H.$$('[role="tab"][data-tab]');
const row = tabs[0].parentNode;
const W = __W__, VIEW = __VIEW__;
row.scrollLeft = 0; row.clientWidth = VIEW; row.scrollWidth = W * tabs.length;
row.getBoundingClientRect = function () { return { left: 0, right: VIEW, width: VIEW, top: 0, bottom: 40, height: 40 }; };
const calls = [];
tabs.forEach(function (b, i) {
  b.offsetLeft = i * W; b.offsetWidth = W;
  b.getBoundingClientRect = function () {
    const l = i * W - row.scrollLeft;
    return { left: l, right: l + W, width: W, top: 0, bottom: 40, height: 40 };
  };
  b.scrollIntoView = function (opts) {
    const o = opts && typeof opts === 'object' ? opts : {};
    calls.push({ tab: b.getAttribute('data-tab'), block: o.block === undefined ? 'start' : o.block });
    const inline = o.inline || 'nearest';
    const l = i * W - row.scrollLeft;
    let delta = 0;
    if (inline === 'center') delta = l + W / 2 - VIEW / 2;
    else if (inline === 'start') delta = l;
    else if (inline === 'end') delta = l + W - VIEW;
    else if (l < 0) delta = l;
    else if (l + W > VIEW) delta = l + W - VIEW;
    row.scrollLeft = Math.max(0, Math.min(row.scrollWidth - VIEW, row.scrollLeft + delta));
  };
});
const inView = function (name) {
  const b = tabs.filter(function (t) { return t.getAttribute('data-tab') === name; })[0];
  const r = b.getBoundingClientRect();
  return { tab: name, left: r.left, right: r.right, whole: r.left >= 0 && r.right <= VIEW,
           active: b.getAttribute('aria-selected') === 'true' };
};
const pageJumps = function () { return calls.filter(function (c) { return c.block !== 'nearest'; }); };
""".replace("__W__", str(TAB_WIDTH)).replace("__VIEW__", str(ROW_WIDTH))

RUNS = route(r"/api/autotune-runs$", {"json": []}, method="GET")


def _tab_names() -> list[str]:
    template = (Path(__file__).resolve().parents[2] / "templates" / "ai_advisor.html").read_text(
        encoding="utf-8"
    )
    names = re.findall(r'role="tab"[^>]*?data-tab="([a-z0-9-]+)"', template, flags=re.S)
    assert len(names) > FIRST_HIDDEN_INDEX + 1, (
        f"the template renders {len(names)} tabs; the scenario needs some past the visible row"
    )
    return names


TAB_NAMES = _tab_names()
HIDDEN_TABS = TAB_NAMES[FIRST_HIDDEN_INDEX:]


def _run(scenario: str):
    return run_page(
        render_page("/ai-advisor"),
        GEOMETRY + scenario,
        routes=[RUNS],
        pathname="/ai-advisor",
    ).result


def test_precondition_the_hidden_tabs_are_off_screen_before_anything_scrolls():
    r = _run(f"return {HIDDEN_TABS!r}.map(function (n) {{ return inView(n); }});")
    assert r and all(not item["whole"] for item in r), r


@pytest.mark.parametrize("name", HIDDEN_TABS)
def test_a_page_opened_on_a_tab_past_the_visible_row_shows_that_tab(name):
    r = _run(
        f"""
        H.win('location').hash = '#{name}';
        await H.boot();
        return {{ view: inView('{name}'), jumps: pageJumps() }};
        """
    )
    assert r["view"]["active"], f"{name} is not the selected tab"
    assert r["view"]["whole"], f"{name} is selected but off screen: {r['view']}"
    assert r["jumps"] == [], f"the page itself was scrolled: {r['jumps']}"


@pytest.mark.parametrize("name", TAB_NAMES)
def test_a_click_on_any_tab_leaves_it_wholly_inside_the_row(name):
    r = _run(
        f"""
        await H.boot();
        await H.click('[role="tab"][data-tab="{name}"]');
        return {{ view: inView('{name}'), jumps: pageJumps() }};
        """
    )
    assert r["view"]["active"], f"{name} is not the selected tab"
    assert r["view"]["whole"], f"{name} is selected but off screen: {r['view']}"
    assert r["jumps"] == [], f"the page itself was scrolled: {r['jumps']}"


def test_a_walk_through_every_tab_and_back_always_ends_with_the_open_tab_in_view():
    r = _run(
        """
        await H.boot();
        const seen = [];
        const order = tabs.map(function (t) { return t.getAttribute('data-tab'); });
        const walk = order.concat(order.slice().reverse());
        for (const name of walk) {
          await H.click('[role="tab"][data-tab="' + name + '"]');
          seen.push(inView(name));
        }
        return { seen: seen, jumps: pageJumps() };
        """
    )
    off = [s for s in r["seen"] if not s["whole"]]
    assert off == [], f"tabs opened but left off screen: {off}"
    assert r["jumps"] == []


@pytest.mark.parametrize("name", HIDDEN_TABS[:2])
def test_a_fragment_change_to_a_hidden_tab_brings_it_into_view(name):
    r = _run(
        f"""
        await H.boot();
        H.win('location').hash = '#{name}';
        await H.fireWindow('hashchange');
        return {{ view: inView('{name}'), jumps: pageJumps() }};
        """
    )
    assert r["view"]["active"], f"{name} is not the selected tab"
    assert r["view"]["whole"], f"{name} is selected but off screen: {r['view']}"
    assert r["jumps"] == []


# ---------------------------------------------------------------------------
# The cue at each edge of the strip (the plan's AC-7): a cue shows only on an edge that has a tab
# out of view. The scenario gives the strip its geometry, as above; a cue is shown when it has no
# ``hidden`` attribute and no inline ``display: none`` (the harness's own notion of visible).
# ---------------------------------------------------------------------------

CUE_SCENARIO = r"""
const cueOf = function (side) { return H.$('[data-testid="cap-nav-cue-' + side + '"]'); };
const shown = function (side) { const c = cueOf(side); return !!c && H.visible(c); };
const cues = function () {
  if (!cueOf('start') || !cueOf('end')) throw new Error('the page has no cap-nav-cue-start / cap-nav-cue-end element');
  return { start: shown('start'), end: shown('end') };
};
const scrolled = async function (left) {
  row.scrollLeft = left;
  row.dispatchEvent({ type: 'scroll', bubbles: false });
  await H.settle();
};
const END = row.scrollWidth - row.clientWidth;
"""


def _cues(scenario: str):
    return _run(CUE_SCENARIO + scenario)


def test_the_cue_elements_exist_and_are_not_exposed_to_assistive_technology():
    r = _run(
        CUE_SCENARIO
        + """
        await H.boot();
        return ['start', 'end'].map(function (s) {
          const c = cueOf(s);
          return { side: s, there: !!c, ariaHidden: c ? c.getAttribute('aria-hidden') : null,
                   inTablist: c ? row.contains(c) && c.closest('[role="tablist"]') !== null : null };
        });
        """
    )
    for item in r:
        assert item["there"], f"no cap-nav-cue-{item['side']} element"
        assert item["ariaHidden"] == "true", f"the {item['side']} cue is read out"


def test_a_row_that_overflows_shows_the_end_cue_and_no_start_cue_before_it_is_scrolled():
    r = _cues("await H.boot(); return cues();")
    assert r == {"start": False, "end": True}, r


def test_scrolled_to_the_end_the_start_cue_shows_and_the_end_cue_goes_and_back_again():
    r = _cues(
        """
        await H.boot();
        await scrolled(END);
        const atEnd = cues();
        await scrolled(END / 2);
        const middle = cues();
        await scrolled(0);
        return { atEnd, middle, atStart: cues() };
        """
    )
    assert r["atEnd"] == {"start": True, "end": False}, r
    assert r["middle"] == {"start": True, "end": True}, r
    assert r["atStart"] == {"start": False, "end": True}, r


def test_a_row_with_every_tab_in_view_shows_no_cue():
    r = _cues(
        """
        row.scrollWidth = row.clientWidth;
        await H.boot();
        return cues();
        """
    )
    assert r == {"start": False, "end": False}, r


def test_a_resize_that_brings_every_tab_into_view_removes_the_end_cue_and_a_narrower_one_restores_it():
    r = _cues(
        """
        await H.boot();
        const before = cues();
        row.clientWidth = row.scrollWidth;
        await H.fireWindow('resize');
        const wide = cues();
        row.clientWidth = 200;
        await H.fireWindow('resize');
        return { before, wide, narrow: cues() };
        """
    )
    assert r["before"] == {"start": False, "end": True}, r
    assert r["wide"] == {"start": False, "end": False}, r
    assert r["narrow"] == {"start": False, "end": True}, r


def test_choosing_the_last_tab_scrolls_the_row_to_its_end_and_the_cues_follow_the_scroll():
    r = _cues(
        f"""
        await H.boot();
        await H.click('[role="tab"][data-tab="{TAB_NAMES[-1]}"]');
        row.dispatchEvent({{ type: 'scroll', bubbles: false }});
        await H.settle();
        return cues();
        """
    )
    assert r == {"start": True, "end": False}, r


def test_a_fraction_of_a_pixel_short_of_the_end_still_counts_as_the_end():
    r = _cues(
        """
        await H.boot();
        await scrolled(END - 0.4);
        return cues();
        """
    )
    assert r["end"] is False, "a rounding gap under one pixel kept a cue on a finished edge"
