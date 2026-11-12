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
  320 px in one column, and stack when they are taller.
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
from tests.ui import _page_explainers_production_shapes as prod
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
PAIR_MAX_HEIGHT = 320
EDGE_TOLERANCE = 0.5

A_KEYS_OF_TAB = {tab: [k for k in adv.OWNED_A_KEYS if adv.TAB_OF_A_KEY[k] == tab] for tab in TABS}
A_KEYS_OF_TAB["overview"] = [
    k for k in adv.OWNED_A_KEYS if adv.TAB_OF_A_KEY[k] in (None, "overview")
]
TERM_KEYS = [k for k in adv.OWNED_A_KEYS if adv.b_rows_for(k)]

_TEXT_JS = """(selector) => [...document.querySelectorAll(selector)].map((el) => ({
  // Whatever a closed <details> holds besides its summary is not visible, whatever boxes the
  // browser keeps for it (no page rule removes them).
  shown: el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden'
         && !(() => {
           for (let n = el; n; n = n.parentElement) {
             const holder = n.parentElement;
             if (holder && holder.tagName === 'DETAILS' && !holder.open && n.tagName !== 'SUMMARY') return true;
           }
           return false;
         })(),
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
def test_two_short_retirement_subsections_share_one_row(
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


@pytest.mark.parametrize("size", DESKTOPS)
def test_two_tall_retirement_subsections_stack(
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


def _card_rows(layout: dict) -> list[list[dict]]:
    rows: list[list[dict]] = []
    for card in sorted(layout["cards"], key=lambda c: (round(c["card"]["top"]), c["card"]["left"])):
        if rows and abs(rows[-1][0]["card"]["top"] - card["card"]["top"]) <= 2:
            rows[-1].append(card)
        else:
            rows.append([card])
    return rows


@pytest.mark.parametrize("count", [1, 4])
@pytest.mark.parametrize("size", DESKTOPS)
def test_each_row_of_proposal_cards_spans_the_tab_panels_content_width(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    size,
    count,  # noqa: F811
):
    """No cap on a card's width and no justify trick: the cards of a row meet both content edges."""
    _prepare(
        tmp_path, monkeypatch, world="empty", seed=lambda: adv.seed_frontrunner_proposals(count)
    )
    with _open(firefox, advisor_server, size, "frontrunner-builder") as opened:
        layout = opened.evaluate(prod.FR_LAYOUT_JS)
    assert len(layout["cards"]) == count
    content = layout["content"]
    problems = []
    for row in _card_rows(layout):
        left = min(c["card"]["left"] for c in row)
        right = max(c["card"]["right"] for c in row)
        if abs(left - content["left"]) > 1 or abs(right - content["right"]) > 1:
            problems.append(
                f"a row of {len(row)} card(s) spans {left:.0f}..{right:.0f}, the panel's "
                f"content spans {content['left']:.0f}..{content['right']:.0f}"
            )
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("size", DESKTOPS)
def test_a_card_wide_enough_lays_its_figures_left_and_its_identity_and_actions_right(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    size,  # noqa: F811
):
    """Inside a card of 44 rem and up two columns; below that one, whatever the page width.

    Four cards: three share the first row (narrow, one column) and the last stretches across the
    next (wide, two columns), so one page shows both sides of the rule.
    """
    _prepare(tmp_path, monkeypatch, world="empty", seed=lambda: adv.seed_frontrunner_proposals(4))
    with _open(firefox, advisor_server, size, "frontrunner-builder") as opened:
        layout = opened.evaluate(prod.FR_LAYOUT_JS)
    threshold = 44 * layout["rootSize"]
    # A container query reads the card's content box; the card's own padding and border are
    # well under 40 px a side, so these bands leave the in-between widths unjudged.
    wide = [c for c in layout["cards"] if c["card"]["width"] >= threshold + 80]
    narrow = [c for c in layout["cards"] if c["card"]["width"] < threshold]
    assert wide and narrow, (
        f"the last-row card must stretch across its row (a width cap keeps it narrow) and the "
        f"first row's stay narrow; card widths are "
        f"{[round(c['card']['width']) for c in layout['cards']]} against {threshold:.0f} px"
    )
    problems = []
    for card in wide:
        table, approve, summary = card["table"], card["approve"], card["summary"]
        if not table["right"] <= approve["left"] + 1:
            problems.append("a wide card puts Approve under its figures, not beside them")
        if not table["right"] <= summary["left"] + 1:
            problems.append("a wide card puts the overlay line under its figures")
        if not table["left"] < approve["left"]:
            problems.append("a wide card puts its figures right of its actions")
    for card in narrow:
        table, approve = card["table"], card["approve"]
        if not approve["top"] >= table["bottom"] - 1:
            problems.append("a narrow card does not stack its actions under its figures")
    assert not problems, "\n".join(dict.fromkeys(problems))


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


_NAV_JS = """() => {
  const panel = document.querySelector('.adv-panel--nav');
  const strip = panel.querySelector('[role="tablist"]');
  const note = panel.querySelector(':scope > .adv-note');
  const rect = (el) => { const b = el.getBoundingClientRect();
    return { left: b.left, top: b.top, right: b.right, bottom: b.bottom }; };
  return { strip: rect(strip), note: rect(note), scroll: strip.scrollWidth, client: strip.clientWidth };
}"""


@pytest.mark.parametrize("size", DESKTOPS)
def test_the_tab_strip_shows_every_tab_whole_and_the_description_never_covers_it(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    size,  # noqa: F811
):
    """At 1440 the description sits right of the strip; at 1240 it sits under it; never on it."""
    _prepare(tmp_path, monkeypatch, world="empty")
    with _open(firefox, advisor_server, size, "overview") as opened:
        read = opened.evaluate(_NAV_JS)
        strip, note = read["strip"], read["note"]
        overlap = (
            note["left"] < strip["right"]
            and note["right"] > strip["left"]
            and note["top"] < strip["bottom"]
            and note["bottom"] > strip["top"]
        )
        assert not overlap, f"the description covers the tab strip: {read}"
        assert read["scroll"] <= read["client"] + 1, f"a tab is out of view at {size[0]}: {read}"
        if size[0] >= 1440:
            assert note["left"] >= strip["right"], (
                f"the description is not beside the strip: {read}"
            )
            assert note["top"] < strip["bottom"], f"the description is not level with it: {read}"


_LONG_SUMMARY = "a summary of many short words that keeps going " * 8
_OPEN_HISTORY_JS = """() => {
  document.querySelectorAll('details.obs-history').forEach((d) => { d.open = true; });
  return [...document.querySelectorAll('.obs-scroll')]
    .filter((s) => s.getClientRects().length)
    .map((s) => {
      const t = s.querySelector('.obs-table');
      return { box: s.clientWidth, table: t.getBoundingClientRect().width, scroll: s.scrollWidth,
               rows: t.querySelectorAll('tbody tr').length };
    });
}"""


def _observations_with_a_long_summary():
    import database

    for index, verdict in enumerate(("flag", "ok", "flag")):
        database.insert_advisor_observation(
            advisor_role="OVERFITTING_CONSCIENCE",
            subject_type="autotune_run",
            subject_id=f"history-run-{index}",
            verdict=verdict,
            raw_response={"note": _LONG_SUMMARY if index != 1 else "short"},
            symphony_id="",
        )


@pytest.mark.parametrize("size", DESKTOPS)
def test_an_open_history_table_with_a_long_summary_shares_its_box_without_scrolling_sideways(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    size,  # noqa: F811
):
    """From 1240 px the history columns share the table's width, so a long summary wraps.

    Without that, the summary column takes the whole width of its text and the other columns
    shrink to a sliver beside it, so the table scrolls inside its box.
    """
    _prepare(tmp_path, monkeypatch, world="empty", seed=_observations_with_a_long_summary)
    with _open(firefox, advisor_server, size, "overview") as opened:
        read = opened.evaluate(_OPEN_HISTORY_JS)
        opened.page.wait_for_timeout(150)
        read = opened.evaluate(_OPEN_HISTORY_JS)
        assert read and read[0]["rows"] == 3, f"premise: the history shows its three rows: {read}"
        for box in read:
            assert box["table"] <= box["box"] + 1, f"the history table is wider than its box: {box}"
            assert box["scroll"] <= box["box"] + 1, f"the history box scrolls sideways: {box}"
