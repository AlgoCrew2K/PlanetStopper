"""The AI Advisor's descriptions, layout and tab strip, read in a real browser.

Browser-only (marker ``browser``, deselected by default; run with
``python -m pytest -m browser -n 0 tests/ui/test_page_explainers_advisor_browser.py``). Firefox
headless through Playwright, imported inside a fixture; a missing Playwright or Firefox fails with
a message naming it, never a skip.

Each case is one seeded world (``tests/ui/_page_explainers.py`` and
``tests/ui/_page_explainers_advisor.py``, written through the real writers) served by the app in
this process. The expected text of a row is read from ``tests/ui/_page_copy.py``; a slot is filled
from the value the world holds. Nothing here types a row.

Contract pinned (the plan's AC-1, AC-2, AC-6, AC-7 and AC-8 for the Advisor page):

* A description is visible text in the browser, exactly as the Copy table A row reads it, and a
  disclosure keeps its term lines hidden until it is opened and shows each one afterwards.
* Choosing tabs twice leaves each description on the page once.
* At 1440 and 1240 the Retirement tab's two subsections share one row while each is no taller than
  320 px in one column, and stack when they are taller; no panel or card holds an empty rectangle
  of 240 by 80 px; five Frontrunner cards leave no empty cell.
* At 375 and 320 the tab strip shows a cue at each edge past which a tab lies out of view and at no
  other edge; every tab is reached by scrolling the strip and chosen by a tap (a mouse click at the
  tab's centre: Firefox has no touch emulation here, so the gesture itself is the live phone check),
  then by Tab and Enter.
* The autotune table states identical columns once, at 1440 and at 375.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from tests.app import _retirement_tab_world as w
from tests.app.conftest import _reset_scheduler_run_state  # noqa: F401  (autouse, health state)
from tests.ui import _page_explainers as harness
from tests.ui import _page_explainers_advisor as adv
from tests.ui._shell_support import (  # noqa: F401  (fixtures: overrides, empty directory, browser)
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    advisor_server,
    firefox,
    open_page,
    open_probe,
)

pytestmark = pytest.mark.browser

TABS = (
    "overview",
    "correlations",
    "retirement",
    "asset-swaps",
    "logic-changes",
    "chat",
    "strategy-builder",
    "frontrunner-builder",
)
PHONE = (375, 812)
VERY_NARROW = (320, 812)
DESKTOPS = [(1440, 900), (1240, 900)]
MIN_W, MIN_H = 240, 80
PAIR_MAX_HEIGHT = 320
EDGE_TOLERANCE = 0.5

A_KEYS_OF_TAB = {tab: [k for k in adv.OWNED_A_KEYS if adv.TAB_OF_A_KEY[k] == tab] for tab in TABS}
A_KEYS_OF_TAB["overview"] = [
    k for k in adv.OWNED_A_KEYS if adv.TAB_OF_A_KEY[k] in (None, "overview")
]
TERM_KEYS = [k for k in adv.OWNED_A_KEYS if adv.b_rows_for(k)]

_TEXT_JS = """(selector) => [...document.querySelectorAll(selector)].map((el) => ({
  shown: el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden',
  text: el.innerText,
  cut: el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflowX !== 'visible',
}))"""

_STRIP_JS = """() => {
  const strip = document.querySelector('[role="tablist"]');
  const sr = strip.getBoundingClientRect();
  const tabs = [...strip.querySelectorAll('[role="tab"]')].map((t) => {
    const r = t.getBoundingClientRect();
    return { name: t.getAttribute('data-tab'), left: r.left, right: r.right };
  });
  const cue = (side) => {
    const c = document.querySelector('[data-testid="cap-nav-cue-' + side + '"]');
    if (!c) return null;
    const r = c.getBoundingClientRect();
    const cs = getComputedStyle(c);
    return {
      shown: c.getClientRects().length > 0 && cs.visibility !== 'hidden'
             && parseFloat(cs.opacity) > 0 && r.width > 0 && r.height > 0,
      left: r.left, right: r.right, top: r.top, bottom: r.bottom, pointerEvents: cs.pointerEvents,
    };
  };
  return {
    strip: { left: sr.left, right: sr.right, top: sr.top, bottom: sr.bottom,
             scrollLeft: strip.scrollLeft, scrollWidth: strip.scrollWidth, clientWidth: strip.clientWidth },
    tabs, start: cue('start'), end: cue('end'),
    pageScroll: document.documentElement.scrollWidth, pageClient: document.documentElement.clientWidth,
  };
}"""

_SCROLL_STRIP_JS = """(left) => {
  const strip = document.querySelector('[role="tablist"]');
  strip.scrollTo({ left: left, behavior: 'instant' });
  return strip.scrollLeft;
}"""

_CENTRE_TAB_JS = """(name) => {
  const strip = document.querySelector('[role="tablist"]');
  const tab = strip.querySelector('[role="tab"][data-tab="' + name + '"]');
  const sr = strip.getBoundingClientRect();
  const tr = tab.getBoundingClientRect();
  strip.scrollTo({ left: strip.scrollLeft + (tr.left + tr.width / 2) - (sr.left + sr.width / 2),
                   behavior: 'instant' });
}"""

_TAB_STATE_JS = """(name) => {
  const strip = document.querySelector('[role="tablist"]');
  const tab = strip.querySelector('[role="tab"][data-tab="' + name + '"]');
  const panel = document.querySelector('[data-testid="tab-panel-' + name + '"]');
  const sr = strip.getBoundingClientRect();
  const tr = tab.getBoundingClientRect();
  const hit = document.elementFromPoint(tr.left + tr.width / 2, tr.top + tr.height / 2);
  return {
    selected: tab.getAttribute('aria-selected') === 'true',
    panelShown: !!panel && panel.getClientRects().length > 0,
    whole: tr.left >= sr.left - 0.5 && tr.right <= sr.right + 0.5,
    centreIsTheTab: !!hit && hit.closest('[role="tab"]') === tab,
    centre: [tr.left + tr.width / 2, tr.top + tr.height / 2],
  };
}"""

_PAIR_JS = """() => {
  const rect = (el) => { const r = el.getBoundingClientRect();
    return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, height: r.height }; };
  const a = document.querySelector('[data-testid="retirement-recommender-section"]');
  const b = document.querySelector('[data-testid="tenure-review-section"]');
  const pair = a.closest('.retirement-pair');
  return { a: rect(a), b: rect(b),
           pair: pair ? { cls: pair.className, shared: pair === b.closest('.retirement-pair') } : null };
}"""


def _scoped(prefix: str) -> str:
    return ", ".join(f"{prefix} {part.strip()}" for part in harness.PANEL_SELECTOR.split(","))


def _prepare(tmp_path, monkeypatch, *, world="full", seed=None, key=True):
    harness.credential_free_environment(
        tmp_path / "page.env", monkeypatch.setattr, lambda n: monkeypatch.delenv(n, raising=False)
    )
    harness.seed_world(world, tmp_path, monkeypatch.setattr)
    if seed is not None:
        seed()
    if key:
        import advisors.asset_swap_engine as ase

        monkeypatch.setenv("ANTHROPIC_API_KEY", harness.PLACEHOLDER_KEY)
        monkeypatch.setattr(ase, "_has_composer_key", lambda: True)


def _seed_everything():
    adv.seed_strategy_builder(survivors=1, backtest_failures=1)
    adv.seed_frontrunner_proposals(2)


class _Opened:
    def __init__(self, probe):
        self.probe = probe
        self.page = probe.page

    def evaluate(self, expression, arg=None):
        return self.page.evaluate(expression, arg)


@contextmanager
def _open(firefox, server, size, tab=None):  # noqa: F811
    with open_probe(firefox, server, width=size[0], height=size[1]) as probe:
        open_page(probe, "/ai-advisor", fragment=f"#{tab}" if tab else "")
        probe.page.evaluate("() => document.fonts.ready.then(() => true)")
        probe.page.wait_for_timeout(400)
        yield _Opened(probe)
        assert probe.page_errors == [], probe.page_errors


def _show_tab(opened: _Opened, tab: str) -> None:
    opened.page.click(f'[role="tab"][data-tab="{tab}"]')
    opened.page.wait_for_timeout(150)


def _empty_regions(opened: _Opened, selector: str) -> list[str]:
    found = opened.evaluate(harness.EMPTY_REGION_JS, [selector, MIN_W, MIN_H])
    return [
        f"{f['testid'] or f['cls']} #{f['index']}: empty {f['largest_empty']['w']:.0f}x"
        f"{f['largest_empty']['h']:.0f} at ({f['largest_empty']['x']:.0f},{f['largest_empty']['y']:.0f})"
        for f in found
        if f["largest_empty"] is not None
    ]


# ---------------------------------------------------------------------------
# AC-1 and AC-2: descriptions and disclosures are visible text (S-1.7 .. S-1.14, S-2.6 .. S-2.10)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tab", TABS)
def test_every_description_of_a_tab_is_visible_with_its_exact_text(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    tab,  # noqa: F811
):
    _prepare(tmp_path, monkeypatch, seed=_seed_everything)
    values = adv.full_world_values()
    keys = A_KEYS_OF_TAB[tab]
    if not keys:
        pytest.fail(f"no Copy table A row is placed on the {tab} tab: the owner table is wrong")
    with _open(firefox, advisor_server, (1440, 900), tab) as opened:
        problems = []
        for key in keys:
            read = opened.evaluate(_TEXT_JS, f'[data-explainer="{key}"]')
            if len(read) != 1:
                problems.append(f"{key}: {len(read)} elements carry it")
                continue
            if not read[0]["shown"]:
                problems.append(f"{key}: not visible")
            wanted = adv.norm(adv.expected_a(key, values))
            if adv.norm(read[0]["text"]) != wanted:
                problems.append(f"{key}: reads {adv.norm(read[0]['text'])!r}, not {wanted!r}")
        assert not problems, "\n".join(problems)


@pytest.mark.parametrize("key", TERM_KEYS)
def test_a_disclosure_keeps_its_terms_hidden_until_opened_and_shows_each_after(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    key,  # noqa: F811
):
    _prepare(tmp_path, monkeypatch, seed=_seed_everything)
    values = adv.full_world_values()
    tab = adv.TAB_OF_A_KEY[key] or "overview"
    rows = adv.b_rows_for(key)
    with _open(firefox, advisor_server, (1440, 900), tab) as opened:
        summary = f'details[data-terms="{key}"] > summary'
        shown = opened.evaluate(_TEXT_JS, summary)
        assert len(shown) == 1 and shown[0]["shown"], f"{key}: the summary is not visible"
        assert adv.norm(shown[0]["text"]) == adv.DISCLOSURE_SUMMARY
        closed = opened.evaluate(_TEXT_JS, f'details[data-terms="{key}"] > p[data-term]')
        assert len(closed) == len(rows) and not any(c["shown"] for c in closed), (
            f"{key}: a term shows before the disclosure is opened"
        )
        opened.page.click(summary)
        opened.page.wait_for_timeout(100)
        read = opened.evaluate(_TEXT_JS, f'details[data-terms="{key}"] > p[data-term]')
        for record, item in zip(rows, read, strict=True):
            assert item["shown"], f"{record['key']}: not visible after opening"
            assert adv.norm(item["text"]) == adv.norm(
                adv.expected_row(record["key"], "B", **values)
            ), record["key"]
        opened.page.click(summary)
        opened.page.wait_for_timeout(100)
        again = opened.evaluate(_TEXT_JS, f'details[data-terms="{key}"] > p[data-term]')
        assert not any(c["shown"] for c in again), f"{key}: the terms stay after closing"


def test_choosing_each_tab_twice_leaves_each_description_on_the_page_once(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,  # noqa: F811
):
    _prepare(tmp_path, monkeypatch, seed=_seed_everything)
    with _open(firefox, advisor_server, (1440, 900)) as opened:
        for _ in range(2):
            for tab in TABS:
                _show_tab(opened, tab)
        for key in adv.OWNED_A_KEYS:
            count = len(opened.evaluate(_TEXT_JS, f'[data-explainer="{key}"]'))
            assert count == 1, (
                f"{key} is on the page {count} times after the tabs were chosen twice"
            )
        for row in (r for r in adv.b_rows_for("A01")):
            assert len(opened.evaluate(_TEXT_JS, f'[data-term="{row["key"]}"]')) == 1


def test_a_description_is_whole_on_a_phone_and_the_page_does_not_scroll_sideways(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,  # noqa: F811
):
    _prepare(tmp_path, monkeypatch, seed=_seed_everything)
    with _open(firefox, advisor_server, PHONE) as opened:
        for tab in TABS:
            _show_tab(opened, tab)
            for key in A_KEYS_OF_TAB[tab]:
                read = opened.evaluate(_TEXT_JS, f'[data-explainer="{key}"]')
                assert read and read[0]["shown"] and not read[0]["cut"], (
                    f"{key} is hidden or cut off at {PHONE[0]} px"
                )
            state = opened.evaluate(_STRIP_JS)
            assert state["pageScroll"] == state["pageClient"], f"{tab}: the page scrolls sideways"


# ---------------------------------------------------------------------------
# AC-6: the Retirement pair and the Frontrunner grid (S-6.3 .. S-6.5)
# ---------------------------------------------------------------------------


def _retirement_without_cards():
    w.seed_roster()
    w.store_captured_summary()


def _retirement_with_cards_and_long_review():
    w.seed_roster()
    adv.seed_recommendation_cards(3)
    adv.seed_long_tenure_review(11)


def _side_by_side(read: dict) -> bool:
    a, b = read["a"], read["b"]
    return abs(a["top"] - b["top"]) <= 2 and a["right"] <= b["left"] + 1


def _stacked(read: dict) -> bool:
    a, b = read["a"], read["b"]
    return b["top"] >= a["bottom"] - 1 and abs(a["left"] - b["left"]) <= 2


@pytest.mark.parametrize("size", DESKTOPS)
def test_two_short_retirement_subsections_share_one_row_and_leave_no_empty_region(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    size,  # noqa: F811
):
    _prepare(tmp_path, monkeypatch, world="empty", seed=_retirement_without_cards)
    with _open(firefox, advisor_server, size, "retirement") as opened:
        read = opened.evaluate(_PAIR_JS)
        assert read["pair"] is not None and read["pair"]["shared"], (
            "the two subsections are not wrapped in one .retirement-pair"
        )
        assert "retirement-pair--side" in read["pair"]["cls"], read["pair"]
        assert _side_by_side(read), f"the subsections do not share a row: {read}"
        offenders = _empty_regions(opened, _scoped('[data-testid="tab-panel-retirement"]'))
        assert not offenders, "empty regions in the Retirement tab:\n" + "\n".join(offenders)


@pytest.mark.parametrize("size", DESKTOPS)
def test_two_tall_retirement_subsections_stack_and_leave_no_empty_region(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    size,  # noqa: F811
):
    _prepare(tmp_path, monkeypatch, world="empty", seed=_retirement_with_cards_and_long_review)
    with _open(firefox, advisor_server, size, "retirement") as opened:
        read = opened.evaluate(_PAIR_JS)
        for part in ("a", "b"):
            assert read[part]["height"] > PAIR_MAX_HEIGHT, (
                f"premise: the {part} subsection is {read[part]['height']:.0f} px tall"
            )
        assert _stacked(read), f"tall subsections must stack: {read}"
        assert read["pair"] is not None and "retirement-pair--side" not in read["pair"]["cls"]
        offenders = _empty_regions(opened, _scoped('[data-testid="tab-panel-retirement"]'))
        assert not offenders, "empty regions in the Retirement tab:\n" + "\n".join(offenders)


_EVIDENCE_GRID_FAULT_CSS = (
    ".tenure-review-evidence-grid { columns: 4rem 24; column-gap: 4px; column-fill: auto; "
    "height: 300px; }"
)
_GRID_RECT_JS = """() => {
  const g = document.querySelector('.tenure-review-evidence-grid');
  const r = g.getBoundingClientRect();
  return { l: r.left, t: r.top, r: r.right, b: r.bottom };
}"""


@pytest.mark.parametrize("size", DESKTOPS)
def test_empty_columns_in_the_evidence_grid_are_still_reported(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    size,  # noqa: F811
):
    """The column-bottom allowance never hides a column that holds nothing.

    A fault gives the evidence grid far more columns than items, filled in order, so most
    columns are empty: the measurement must report a region that lies inside the grid.
    """
    _prepare(tmp_path, monkeypatch, world="empty", seed=_retirement_with_cards_and_long_review)
    with _open(firefox, advisor_server, size, "retirement") as opened:
        opened.page.add_style_tag(content=_EVIDENCE_GRID_FAULT_CSS)
        opened.page.wait_for_timeout(150)
        grid = opened.evaluate(_GRID_RECT_JS)
        found = opened.evaluate(
            harness.EMPTY_REGION_JS,
            [_scoped('[data-testid="tab-panel-retirement"]'), MIN_W, MIN_H],
        )
        inside = [
            f["largest_empty"]
            for f in found
            if f["largest_empty"] is not None
            and grid["l"] <= f["largest_empty"]["x"] + f["largest_empty"]["w"] / 2 <= grid["r"]
            and grid["t"] <= f["largest_empty"]["y"] + f["largest_empty"]["h"] / 2 <= grid["b"]
        ]
        assert inside, f"empty columns of the evidence grid were not reported: {found}"


@pytest.mark.parametrize("size", [PHONE, VERY_NARROW])
def test_the_retirement_subsections_always_stack_on_a_phone_even_when_short(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    size,  # noqa: F811
):
    _prepare(tmp_path, monkeypatch, world="empty", seed=_retirement_without_cards)
    with _open(firefox, advisor_server, size, "retirement") as opened:
        read = opened.evaluate(_PAIR_JS)
        assert _stacked(read), f"the subsections share a row at {size[0]} px: {read}"
        state = opened.evaluate(_STRIP_JS)
        assert state["pageScroll"] == state["pageClient"]


@pytest.mark.parametrize("size", DESKTOPS)
def test_five_pending_proposals_leave_no_empty_cell_in_the_grid_or_a_card(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    size,  # noqa: F811
):
    _prepare(tmp_path, monkeypatch, world="empty", seed=lambda: adv.seed_frontrunner_proposals(5))
    with _open(firefox, advisor_server, size, "frontrunner-builder") as opened:
        cards = opened.evaluate(_TEXT_JS, '[data-testid="frontrunner-proposal-card"]')
        assert len(cards) == 5 and all(c["shown"] for c in cards), "premise: five cards show"
        selector = '[data-testid="frontrunner-proposal-cards"], ' + _scoped(
            '[data-testid="tab-panel-frontrunner-builder"]'
        )
        offenders = _empty_regions(opened, selector)
        assert not offenders, "empty regions in the Frontrunner tab:\n" + "\n".join(offenders)


# ---------------------------------------------------------------------------
# AC-7: the tab strip cue and the eight tabs at 375 and 320 (S-7.7 .. S-7.9)
# ---------------------------------------------------------------------------


def _assert_cues_match_the_edges(state: dict) -> None:
    strip = state["strip"]
    beyond_start = any(t["left"] < strip["left"] - EDGE_TOLERANCE for t in state["tabs"])
    beyond_end = any(t["right"] > strip["right"] + EDGE_TOLERANCE for t in state["tabs"])
    for side, beyond in (("start", beyond_start), ("end", beyond_end)):
        cue = state[side]
        if not beyond:
            assert cue is None or not cue["shown"], (
                f"a {side} cue shows on an edge with no tab out of view: {state}"
            )
            continue
        assert cue is not None and cue["shown"], (
            f"a tab lies past the {side} edge of the strip and no {side} cue shows: {state}"
        )
        assert cue["pointerEvents"] == "none", f"the {side} cue would take a tap meant for a tab"
        assert cue["top"] >= strip["top"] - 2 and cue["bottom"] <= strip["bottom"] + 2
        if side == "start":
            assert abs(cue["left"] - strip["left"]) <= 2, (
                f"the start cue is not at the edge: {state}"
            )
        else:
            assert abs(cue["right"] - strip["right"]) <= 2, (
                f"the end cue is not at the edge: {state}"
            )


def test_at_375_every_tab_is_inside_the_strip_or_a_cue_marks_the_edge_past_it(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,  # noqa: F811
):
    _prepare(tmp_path, monkeypatch, seed=_seed_everything)
    with _open(firefox, advisor_server, PHONE) as opened:
        state = opened.evaluate(_STRIP_JS)
        assert state["pageScroll"] == state["pageClient"], "the page scrolls sideways at 375"
        assert state["strip"]["scrollWidth"] > state["strip"]["clientWidth"], (
            "premise: eight tabs do not fit in 375 px"
        )
        _assert_cues_match_the_edges(state)
        assert state["end"] is not None and state["end"]["shown"], (
            "the end cue is the one needed here"
        )
        assert state["start"] is None or not state["start"]["shown"]


def test_on_a_wide_page_where_every_tab_fits_no_cue_shows(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,  # noqa: F811
):
    _prepare(tmp_path, monkeypatch, seed=_seed_everything)
    with _open(firefox, advisor_server, (1440, 900)) as opened:
        state = opened.evaluate(_STRIP_JS)
        assert all(
            state["strip"]["left"] - 0.5 <= t["left"]
            and t["right"] <= state["strip"]["right"] + 0.5
            for t in state["tabs"]
        ), "premise: every tab fits at 1440"
        assert state["start"] is not None and state["end"] is not None, "the cue elements exist"
        _assert_cues_match_the_edges(state)


def test_scrolled_to_the_end_and_back_the_cues_follow_the_tabs_out_of_view(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,  # noqa: F811
):
    _prepare(tmp_path, monkeypatch, seed=_seed_everything)
    with _open(firefox, advisor_server, PHONE) as opened:
        maximum = opened.evaluate(_STRIP_JS)["strip"]
        end = maximum["scrollWidth"] - maximum["clientWidth"]
        for left in (end, end / 2, 0):
            opened.evaluate(_SCROLL_STRIP_JS, left)
            opened.page.wait_for_timeout(200)
            _assert_cues_match_the_edges(opened.evaluate(_STRIP_JS))
        opened.evaluate(_SCROLL_STRIP_JS, end)
        opened.page.wait_for_timeout(200)
        at_end = opened.evaluate(_STRIP_JS)
        assert at_end["start"]["shown"] and not at_end["end"]["shown"], at_end
        opened.evaluate(_SCROLL_STRIP_JS, 0)
        opened.page.wait_for_timeout(200)
        at_start = opened.evaluate(_STRIP_JS)
        assert at_start["end"]["shown"] and not at_start["start"]["shown"], at_start


def test_a_resize_that_widens_the_strip_removes_a_cue_that_no_longer_has_a_tab_to_mark(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,  # noqa: F811
):
    _prepare(tmp_path, monkeypatch, seed=_seed_everything)
    with _open(firefox, advisor_server, PHONE) as opened:
        assert opened.evaluate(_STRIP_JS)["end"]["shown"], "premise: the end cue shows at 375"
        opened.page.set_viewport_size({"width": 1440, "height": 900})
        opened.page.wait_for_timeout(300)
        state = opened.evaluate(_STRIP_JS)
        assert not state["end"]["shown"], "the end cue stayed after every tab came into view"
        _assert_cues_match_the_edges(state)


def test_at_320_each_of_the_eight_tabs_is_reached_by_scrolling_and_chosen_by_a_tap(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,  # noqa: F811
):
    _prepare(tmp_path, monkeypatch, seed=_seed_everything)
    with _open(firefox, advisor_server, VERY_NARROW) as opened:
        for name in TABS:
            opened.evaluate(_CENTRE_TAB_JS, name)
            opened.page.wait_for_timeout(200)
            before = opened.evaluate(_TAB_STATE_JS, name)
            assert before["centreIsTheTab"], (
                f"{name}: after the strip is scrolled to it something covers the tab's centre"
            )
            opened.page.mouse.click(*before["centre"])
            opened.page.wait_for_timeout(250)
            after = opened.evaluate(_TAB_STATE_JS, name)
            assert after["selected"], f"{name}: a tap on the tab did not choose it"
            assert after["panelShown"], f"{name}: the chosen tab's panel is not shown"
            assert after["whole"], f"{name}: the chosen tab is not wholly in view"
            _assert_cues_match_the_edges(opened.evaluate(_STRIP_JS))


def test_at_320_tab_and_enter_reach_and_choose_each_of_the_eight_tabs(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,  # noqa: F811
):
    _prepare(tmp_path, monkeypatch, seed=_seed_everything)
    with _open(firefox, advisor_server, VERY_NARROW) as opened:
        opened.page.focus(f'[role="tab"][data-tab="{TABS[0]}"]')
        for index, name in enumerate(TABS):
            if index:
                opened.page.keyboard.press("Tab")
                opened.page.wait_for_timeout(150)
            focused = opened.evaluate(
                "() => document.activeElement && document.activeElement.getAttribute('data-tab')"
            )
            assert focused == name, f"Tab reached {focused!r}, not {name!r}"
            focused_state = opened.evaluate(_TAB_STATE_JS, name)
            assert focused_state["centreIsTheTab"], (
                f"{name}: the focused tab is out of view or a cue covers it"
            )
            opened.page.keyboard.press("Enter")
            opened.page.wait_for_timeout(250)
            chosen = opened.evaluate(_TAB_STATE_JS, name)
            assert chosen["selected"] and chosen["panelShown"], f"{name}: Enter did not open it"
            assert chosen["whole"], f"{name}: the chosen tab is not wholly in view"


# ---------------------------------------------------------------------------
# AC-8: the autotune table states identical columns once (S-8.1 in the browser)
# ---------------------------------------------------------------------------

_AUTOTUNE_JS = """() => {
  const norm = (t) => String(t || '').replace(/\\s+/g, ' ').trim();
  const line = [...document.querySelectorAll('#autotune-runs-list [data-testid="autotune-shared-line"]')];
  const rows = [...document.querySelectorAll('#autotune-runs-list [data-testid="autotune-run-row"]')];
  const head = [...document.querySelectorAll('#autotune-runs-list .autotune-row--head [role="columnheader"]')];
  const box = (el) => { const r = el.getBoundingClientRect(); return { w: r.width, h: r.height }; };
  return {
    lines: line.map((l) => ({ text: norm(l.innerText), shown: l.getClientRects().length > 0, box: box(l) })),
    rows: rows.map((r) => ({ text: norm(r.innerText), name: norm(r.querySelector('.autotune-run-name').innerText),
                              time: norm(r.querySelector('.autotune-run-time').innerText) })),
    heads: head.map((h) => norm(h.textContent)),
    pageScroll: document.documentElement.scrollWidth, pageClient: document.documentElement.clientWidth,
  };
}"""


@pytest.mark.parametrize("size", [(1440, 900), PHONE])
def test_twelve_identical_runs_print_one_shared_line_and_no_row_repeats_it(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    size,  # noqa: F811
):
    _prepare(tmp_path, monkeypatch, world="empty", seed=lambda: adv.seed_autotune_runs(11))
    with _open(firefox, advisor_server, size) as opened:
        opened.page.wait_for_selector(
            '#autotune-runs-list [data-testid="autotune-run-row"]', state="attached"
        )
        read = opened.evaluate(_AUTOTUNE_JS)
        assert len(read["rows"]) == 11
        assert len(read["lines"]) == 1 and read["lines"][0]["shown"], read["lines"]
        text = read["lines"][0]["text"]
        score_head = read["heads"][4] if len(read["heads"]) > 4 else "Best objective score"
        assert text == (
            adv.SHARED_LINE_PREFIX
            + f"Outcome Fallback, Frozen-eval not evaluated; {score_head} N/A; Sel t-stat N/A"
        ), text
        assert read["lines"][0]["box"]["w"] > 100 and read["lines"][0]["box"]["h"] >= 10
        for row in read["rows"]:
            for word in ("N/A", "Frozen-eval", "Fallback"):
                assert word not in row["text"], f"a row repeats {word!r}: {row['text']!r}"
            assert row["name"] and row["time"], f"a row lost its symphony or run time: {row}"
        assert read["pageScroll"] == read["pageClient"], "the page scrolls sideways"
