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
* The Retirement tab's two subsections stack at every width, each taking the panel's full content
  width, whatever their heights; its five run tiles start at one height with equal gaps and the
  Blocked breakdown is a line of its own below them; the tenure run line and a tenure card's head
  and decision row sit with even gaps; the undecided-peers disclosure opens and closes by click,
  Enter and Space and is closed after a reload.
* At 375 and 320 the tab strip shows a cue at each edge past which a tab lies out of view and at no
  other edge; every tab is reached by scrolling the strip and chosen by a tap (a mouse click at the
  tab's centre: Firefox has no touch emulation here, so the gesture itself is the live phone check),
  then by Tab and Enter.
* The autotune table states identical columns once, at 1440 and at 375.
"""

from __future__ import annotations

import pathlib
from contextlib import contextmanager

import pytest

from tests.app import _retirement_tab_world as w
from tests.app import _tenure_review_panel_builders as pb
from tests.app.conftest import _reset_scheduler_run_state  # noqa: F401  (autouse, health state)
from tests.ui import _alignment as al
from tests.ui import _page_copy as copy_fixture
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
EDGE_TOLERANCE = 0.5

A_KEYS_OF_TAB = {tab: [k for k in adv.PLAN_A_KEYS if adv.TAB_OF_A_KEY[k] == tab] for tab in TABS}

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
  const panel = document.querySelector('[data-testid="retirement-recommendations-panel"]');
  const cs = getComputedStyle(panel), pr = panel.getBoundingClientRect();
  const content = {
    left: pr.left + parseFloat(cs.borderLeftWidth) + parseFloat(cs.paddingLeft),
    right: pr.right - parseFloat(cs.borderRightWidth) - parseFloat(cs.paddingRight),
  };
  return { a: rect(a), b: rect(b), content };
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


def test_choosing_each_tab_twice_leaves_each_description_and_glossary_line_on_the_page_once(
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
        for key in adv.PLAN_A_KEYS:
            count = len(opened.evaluate(_TEXT_JS, f'[data-explainer="{key}"]'))
            assert count == 1, (
                f"{key} is on the page {count} times after the tabs were chosen twice"
            )
        for host, tab in adv.TAB_OF_GLOSSARY_HOST.items():
            count = len(
                opened.evaluate(
                    _TEXT_JS, f'#tab-panel-{tab} details[data-terms="glossary"] > p[data-term]'
                )
            )
            assert count == len(copy_fixture.glossary(host)), (
                f"the {host} Glossary holds {count} lines after the tabs were chosen twice"
            )


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
# The Retirement tab: its two subsections stack, its run tiles and tenure run line sit on one
# line with even gaps, and the tenure disclosure opens by click and by keys
# (the plan's AC-2, AC-6 and AC-8 for the tab)
# ---------------------------------------------------------------------------


def _retirement_without_cards():
    w.seed_roster()
    w.store_captured_summary()


def _retirement_with_cards_and_long_review():
    w.seed_roster()
    adv.seed_recommendation_cards(3)
    adv.seed_long_tenure_review(11)


def _retirement_with_one_small_review():
    w.seed_roster()
    w.store_captured_summary()
    pb.store_run(pb.small_record())


def _retirement_with_the_undecided_review():
    w.seed_roster()
    w.store_captured_summary()
    pb.store_run(pb.undecided_record())


def _stacked(read: dict) -> bool:
    a, b = read["a"], read["b"]
    return b["top"] >= a["bottom"] - 1 and abs(a["left"] - b["left"]) <= 2


def _is_full_width(read: dict, part: str) -> bool:
    box, content = read[part], read["content"]
    return abs(box["left"] - content["left"]) <= 2 and abs(box["right"] - content["right"]) <= 2


def _assert_stacked_at_full_width(opened) -> dict:
    read = opened.evaluate(_PAIR_JS)
    assert _stacked(read), f"the subsections share a row or are offset: {read}"
    for part in ("a", "b"):
        assert _is_full_width(read, part), f"{part} does not span the content width: {read}"
    assert read["a"]["top"] < read["b"]["top"], "Correlated pairs is not above the tenure review"
    return read


@pytest.mark.parametrize("width", al.WIDTHS)
def test_two_short_retirement_subsections_each_take_the_panels_full_width_one_above_the_other(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    width,  # noqa: F811
):
    """S-2.18: a stored run with no cards and no review yet. The reference put two subsections of
    320 px or less in one row (and stacked them when either was taller); each now takes the whole
    content width, the recommender above the tenure review, whatever their heights."""
    _prepare(tmp_path, monkeypatch, world="empty", seed=_retirement_without_cards)
    with _open(firefox, advisor_server, (width, 1000), "retirement") as opened:
        _assert_stacked_at_full_width(opened)


@pytest.mark.parametrize("width", [1001, 1440, 1920])
def test_a_small_stored_review_below_a_stored_run_also_stacks_at_full_width(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    width,  # noqa: F811
):
    _prepare(tmp_path, monkeypatch, world="empty", seed=_retirement_with_one_small_review)
    with _open(firefox, advisor_server, (width, 1000), "retirement") as opened:
        _assert_stacked_at_full_width(opened)


@pytest.mark.parametrize("width", [1440, 1920])
def test_two_tall_retirement_subsections_also_each_take_the_full_width(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    width,  # noqa: F811
):
    _prepare(tmp_path, monkeypatch, world="empty", seed=_retirement_with_cards_and_long_review)
    with _open(firefox, advisor_server, (width, 1000), "retirement") as opened:
        read = opened.evaluate(_PAIR_JS)
    assert _stacked(read), read
    for part in ("a", "b"):
        assert _is_full_width(read, part), f"{part} does not span the content width: {read}"


@pytest.mark.parametrize("size", [PHONE, VERY_NARROW])
def test_the_retirement_subsections_stack_on_a_phone_and_the_page_does_not_scroll_sideways(
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


def test_no_script_and_no_rule_sets_a_side_by_side_modifier_on_the_retirement_subsections():
    """The pairing code is deleted, not left unused: neither the script nor the template names the
    modifier classes, the measuring function or its two size constants."""
    root = pathlib.Path(__file__).resolve().parents[2]
    script = (root / "static" / "ai_advisor.js").read_text(encoding="utf-8")
    template = (root / "templates" / "ai_advisor.html").read_text(encoding="utf-8")
    for needle in (
        "refreshRetirementPair",
        "RETIREMENT_PAIR_MAX_HEIGHT_PX",
        "RETIREMENT_PAIR_STACK_MAX_WIDTH_PX",
        "retirement-pair--side",
        "retirement-pair--never-run",
    ):
        assert needle not in script, f"static/ai_advisor.js still holds {needle}"
        assert needle not in template, f"templates/ai_advisor.html still holds {needle}"


_TILE_TESTIDS = (
    "retirement-last-run-symphonies",
    "retirement-last-run-depth",
    "retirement-last-run-pairs",
    "retirement-last-run-blocked",
    "retirement-last-run-recommendations",
)

# Where the five tiles' figures sit: the box of each tile's label and value (its breakdown line
# excluded), and where the Blocked breakdown (the one note of the block that is neither the run
# time nor the series date) sits.
_TILES_JS = """(ids) => {
  const box = (el) => {
    let out = null;
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      if (!n.nodeValue.trim()) continue;
      const r = document.createRange();
      r.selectNodeContents(n);
      for (const g of r.getClientRects()) {
        if (g.width <= 1 || g.height <= 1) continue;
        out = out
          ? { left: Math.min(out.left, g.left), right: Math.max(out.right, g.right),
              top: Math.min(out.top, g.top), bottom: Math.max(out.bottom, g.bottom) }
          : { left: g.left, right: g.right, top: g.top, bottom: g.bottom };
      }
    }
    return out;
  };
  const tiles = ids.map((id) => {
    const tile = document.querySelector('[data-testid="' + id + '"]');
    const value = tile && tile.querySelector('.mini-stat-value');
    const label = tile && tile.querySelector('.mini-stat-label');
    const v = value && box(value), l = label && box(label);
    const both = v && l ? { left: Math.min(v.left, l.left), right: Math.max(v.right, l.right),
                            top: Math.min(v.top, l.top), bottom: Math.max(v.bottom, l.bottom) } : null;
    return { id, value: v, label: l, figure: both };
  });
  const note = document.querySelector(
    '.retirement-last-run .adv-note:not([data-testid="retirement-last-run-time"])'
    + ':not([data-testid="retirement-last-run-series-through"])');
  const block = document.querySelector('[data-testid="retirement-last-run"]');
  const bs = getComputedStyle(block);
  const br = block.getBoundingClientRect();
  return {
    tiles, note: note ? box(note) : null,
    blockLeft: br.left + parseFloat(bs.paddingLeft) + parseFloat(bs.borderLeftWidth),
  };
}"""


def _tiles(opened) -> dict:
    return opened.evaluate(_TILES_JS, list(_TILE_TESTIDS))


@pytest.mark.parametrize("width", [1001, 1440, 1920, 2560])
def test_the_five_run_tiles_start_at_one_height_with_equal_gaps_and_the_breakdown_is_its_own_line(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    width,  # noqa: F811
):
    """S-2.9."""
    _prepare(tmp_path, monkeypatch, world="empty", seed=_retirement_without_cards)
    with _open(firefox, advisor_server, (width, 1000), "retirement") as opened:
        read = _tiles(opened)
    tiles = read["tiles"]
    assert all(t["value"] and t["label"] for t in tiles), f"a tile has no figure: {tiles}"
    tops = [t["value"]["top"] for t in tiles]
    assert max(tops) - min(tops) <= 2, f"the tiles' values start at different heights: {tops}"
    ordered = sorted(tiles, key=lambda t: t["figure"]["left"])
    assert [t["id"] for t in ordered] == list(_TILE_TESTIDS), "the tiles are out of order"
    gaps = [b["figure"]["left"] - a["figure"]["right"] for a, b in zip(ordered, ordered[1:])]
    assert max(gaps) <= 48, f"a gap between tiles is {max(gaps):.0f} px: {gaps}"
    assert max(gaps) - min(gaps) <= 2, f"the gaps between tiles are not equal: {gaps}"
    note = read["note"]
    assert note is not None, "the Blocked breakdown is not on the page"
    lowest = max(max(t["value"]["bottom"], t["label"]["bottom"]) for t in tiles)
    assert note["top"] >= lowest - 1, "the breakdown is not below every tile"
    assert abs(note["left"] - read["blockLeft"]) <= 2, (
        f"the breakdown does not begin its line at the block's left edge: {note} {read['blockLeft']}"
    )


@pytest.mark.parametrize("width", al.WIDTHS)
def test_the_tenure_run_line_items_sit_with_equal_gaps_from_the_rows_left_edge(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    width,  # noqa: F811
):
    """S-2.10."""
    _prepare(tmp_path, monkeypatch, world="empty", seed=_retirement_with_the_undecided_review)
    entry = al.FIGURE_LINES["tenure_run_line"]
    with _open(firefox, advisor_server, (width, 1000), "retirement") as opened:
        gaps = al.measure(
            opened.page, al.LINE_GAPS_JS, {"name": "tenure_run_line", **_line_spec(entry, width)}
        )
        start = al.measure(
            opened.page,
            al.ROW_START_JS,
            {"name": "tenure_run_line", "row": entry["line"], "max_gap": al.MAX_COLUMN_GAP_PX},
        )
    assert gaps["found"] == 1 and gaps["lines"][0]["items"] >= 3, gaps
    assert gaps["violations"] == [], gaps["violations"]
    assert start["violations"] == [], start["violations"]


def _line_spec(entry: dict, width: int) -> dict:
    """The registry entry's line and items; from 1440 px no figure may break across lines."""
    return {"line": entry["line"], "items": entry["items"], "no_wrap": width >= 1440}


@pytest.mark.parametrize("width", [1440, 1920])
def test_a_tenure_cards_head_and_lifecycle_items_sit_within_48_px_of_each_other(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    width,  # noqa: F811
):
    """S-2.19 for the tenure card (the card's head and its decision row)."""

    def seed():
        w.seed_roster()
        pb.seed_bot_state()
        pb.store_full_run()

    _prepare(tmp_path, monkeypatch, world="empty", seed=seed)
    head = al.FIGURE_LINES["tenure_card_head"]
    with _open(firefox, advisor_server, (width, 1000), "retirement") as opened:
        heads = al.measure(
            opened.page, al.LINE_GAPS_JS, {"name": "tenure_card_head", **_line_spec(head, width)}
        )
        lifecycle = al.measure(
            opened.page,
            al.LINE_GAPS_JS,
            {
                "name": "tenure_lifecycle",
                "line": ".tenure-review-lifecycle",
                "items": ":scope > *",
                "tolerance": al.MAX_COLUMN_GAP_PX,
            },
        )
    assert heads["found"] >= 3 and heads["violations"] == [], heads["violations"]
    assert lifecycle["found"] >= 1, "premise: a card carries a decision row"
    assert lifecycle["violations"] == [], lifecycle["violations"]


_UNDECIDED = '[data-testid="tenure-review-undecided"]'


def test_the_undecided_disclosure_is_closed_on_load_and_opens_and_closes_by_click_enter_and_space(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,  # noqa: F811
):
    """S-6.3 in a browser: closed on load, shows the stored reason when open, and the keys work."""
    _prepare(tmp_path, monkeypatch, world="empty", seed=_retirement_with_the_undecided_review)
    keep = pb.undecided_record()["entries"][pb.UNDECIDED_KEEP]
    reason = keep["evidence"]["redundancy"]["reason"]
    with _open(firefox, advisor_server, (1440, 900), "retirement") as opened:
        card = f'[data-testid="tenure-review-entry"][data-symphony-id="{keep["symphony_id"]}"]'
        details = opened.page.locator(f"{card} {_UNDECIDED}")
        summary = details.locator(":scope > summary")
        assert summary.inner_text().strip() == "Comparisons that decided nothing"
        assert details.evaluate("(d) => d.open") is False, "it is open on load"
        closed_text = opened.page.locator(card).inner_text()
        for name, _days in pb.undecided_peers(keep):
            assert name not in closed_text, f"{name} shows while the disclosure is closed"
        summary.click()
        assert details.evaluate("(d) => d.open") is True
        assert " ".join(details.inner_text().split()).endswith(" ".join(reason.split()))
        summary.click()
        assert details.evaluate("(d) => d.open") is False
        for key in ("Enter", "Space"):
            summary.focus()
            opened.page.keyboard.press(key)
            assert details.evaluate("(d) => d.open") is True, f"{key} did not open it"
            opened.page.keyboard.press(key)
            assert details.evaluate("(d) => d.open") is False, f"{key} did not close it"


def test_the_undecided_disclosure_is_closed_again_after_a_reload(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,  # noqa: F811
):
    _prepare(tmp_path, monkeypatch, world="empty", seed=_retirement_with_the_undecided_review)
    with _open(firefox, advisor_server, (1440, 900), "retirement") as opened:
        first = opened.page.locator(_UNDECIDED).first
        first.locator(":scope > summary").click()
        assert first.evaluate("(d) => d.open") is True
        opened.page.reload()
        opened.page.wait_for_selector(_UNDECIDED, state="attached")
        opened.page.click('[role="tab"][data-tab="retirement"]')
        states = opened.page.eval_on_selector_all(_UNDECIDED, "(ds) => ds.map((d) => d.open)")
        assert states and not any(states), f"a disclosure is open after a reload: {states}"


def test_a_peer_name_is_not_visible_on_the_closed_tenure_cards(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,  # noqa: F811
):
    """The tenure cards state N and name no peer while every disclosure is closed."""
    _prepare(tmp_path, monkeypatch, world="empty", seed=_retirement_with_the_undecided_review)
    record = pb.undecided_record()
    checked = 0
    with _open(firefox, advisor_server, (1440, 900), "retirement") as opened:
        for stored in record["entries"]:
            card = (
                f'[data-testid="tenure-review-entry"][data-symphony-id="{stored["symphony_id"]}"]'
            )
            shown = opened.evaluate(
                """(sel) => {
                  const card = document.querySelector(sel);
                  return card.innerText;
                }""",
                card,
            )
            # A card whose redundancy did not decide nothing lists no undecided peer.
            reason = stored["evidence"]["redundancy"].get("reason")
            if not (isinstance(reason, str) and reason.startswith(pb.tr._UNDECIDED_PREFIX)):
                continue
            for name, _days in pb.undecided_peers(stored):
                checked += 1
                assert name not in shown, f"{stored['display_name']} shows {name} while closed"
        assert checked >= 10, (
            "the cards listed too few undecided peers for the check to mean anything"
        )


def test_the_run_tiles_and_the_evidence_headings_carry_their_glossary_line_as_their_title(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,  # noqa: F811
):
    """TG3-13 in a browser: each tile label and each evidence heading has, as its title, the full
    text of its Glossary line (the Pairs tile reads "Pairs screened at 0.65 or above", which is not
    the line's lead term, so only the key reaches it)."""
    _prepare(tmp_path, monkeypatch, world="empty", seed=_retirement_with_the_undecided_review)
    lines = {line["key"]: line["text"] for line in copy_fixture.glossary("Retirement")}
    wanted = {
        '[data-testid="retirement-last-run-symphonies"] .mini-stat-label': "R06",
        '[data-testid="retirement-last-run-depth"] .mini-stat-label': "R07",
        '[data-testid="retirement-last-run-pairs"] .mini-stat-label': "R08",
        '[data-testid="retirement-last-run-blocked"] .mini-stat-label': "R09",
        **{
            f'[data-testid="tenure-review-entry"] [data-testid="tenure-review-evidence-{name}"]'
            ' [data-testid="tenure-review-evidence-heading"]': key
            for name, key in (
                ("performance", "R29a"),
                ("composite", "R29b"),
                ("redundancy", "R29c"),
                ("backtest", "R29d"),
                ("marginal", "R29e"),
                ("long_vix", "R29f"),
            )
        },
    }
    with _open(firefox, advisor_server, (1440, 900), "retirement") as opened:
        problems = []
        for selector, key in wanted.items():
            titles = opened.page.eval_on_selector_all(
                selector, "(els) => els.map((e) => e.getAttribute('title'))"
            )
            if not titles:
                problems.append(f"{key}: no element at {selector}")
            for title in titles:
                if title is None or " ".join(title.split()) != " ".join(lines[key].split()):
                    problems.append(f"{key}: title is {title!r}")
        assert not problems, "; ".join(problems)


# ---------------------------------------------------------------------------
# The Strategy Builder and Frontrunner Builder cards (the plan's AC-1: TT-13, TT-26)
# ---------------------------------------------------------------------------

SB_CARDS = "strategy-builder"
FR_CARDS = "frontrunner-builder"
# Five pending proposals: four frontrunner copies (a different figure width each) and one
# from-scratch retrofit, whose table has no Incumbent column. Three stored survivors, one with a
# live baseline column.
FR_PROPOSAL_COUNT = 5


def _seed_five_proposals():
    adv.seed_frontrunner_proposals_varied(FR_PROPOSAL_COUNT - 1, retrofit=1)


def _seed_three_survivors():
    adv.seed_strategy_builder_cards(3)


# tab -> (seed, registry entry of its cards, how many cards the seed renders)
CARD_WORLDS = {
    SB_CARDS: (_seed_three_survivors, "survivor_cards", 3),
    FR_CARDS: (_seed_five_proposals, "pending_proposal_cards", FR_PROPOSAL_COUNT),
}

_TABLE_COLUMNS_JS = """(selector) => {
  const box = (el) => {
    let b = null;
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      if (!n.nodeValue.trim()) continue;
      const range = document.createRange();
      range.selectNodeContents(n);
      for (const g of range.getClientRects()) {
        if (g.width <= 1 || g.height <= 1) continue;
        b = b ? { left: Math.min(b.left, g.left), right: Math.max(b.right, g.right) }
              : { left: g.left, right: g.right };
      }
    }
    return b;
  };
  const groups = {};
  for (const table of document.querySelectorAll(selector)) {
    if (table.getClientRects().length === 0) continue;
    const card = table.closest('.proposal-card');
    const cardLeft = card.getBoundingClientRect().left;
    const heads = [...table.querySelectorAll('thead th')];
    const names = heads.map((h) => h.textContent.replace(/\\s+/g, ' ').trim());
    const key = names.join('|');
    const group = groups[key] = groups[key] || { tables: 0, offsets: names.map(() => []) };
    group.tables += 1;
    heads.forEach((h, i) => {
      const b = box(h);
      if (b) group.offsets[i].push(i === 0 ? b.left - cardLeft : b.right - cardLeft);
    });
  }
  return groups;
}"""

_CARD_WIDTHS_JS = """(selector) => [...document.querySelectorAll(selector)]
  .filter((el) => el.getClientRects().length > 0)
  .map((el) => el.getBoundingClientRect().width)"""

_SIDEWAYS_JS = """() => ({ page: document.documentElement.scrollWidth,
                          client: document.documentElement.clientWidth })"""


def _prepare_cards(tmp_path, monkeypatch, tab):
    seed, _, _ = CARD_WORLDS[tab]
    _prepare(tmp_path, monkeypatch, world="empty", seed=seed)


@pytest.mark.parametrize("width", al.WIDTHS)
@pytest.mark.parametrize("tab", [SB_CARDS, FR_CARDS])
def test_each_figures_table_has_one_header_row_and_its_figure_columns_end_at_one_right_edge(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    tab,
    width,  # noqa: F811
):
    """S-1.9 for each card's table: one header row above the body rows, the Metric column starting
    at one x and each figure column, its header included, ending at one x. Rejects left-aligned
    figures and headers (the reference's, which end where their text happens to), and a Node count
    change figure that spans the figure columns without ending at the last of them."""
    _prepare_cards(tmp_path, monkeypatch, tab)
    _, entry_name, count = CARD_WORLDS[tab]
    entry = al.TABLE_GROUPS["stats_tables"]
    group = f'[data-testid="tab-panel-{tab}"] {entry["group"]}'
    with _open(firefox, advisor_server, (width, al.VIEWPORT_HEIGHT), tab) as opened:
        found = al.measure(
            opened.page, al.COLUMN_EDGES_JS, al.spec_of(entry, name=entry_name, group=group)
        )
    assert found["found"] == count, f"{found['found']} figure tables, not {count}"
    assert all(i["header_rows"] == 1 for i in found["instances"]), "a table lost its header row"
    assert not found["violations"], "\n".join(found["violations"])


@pytest.mark.parametrize("width", [1001, 1440, 1920, 2560])
@pytest.mark.parametrize("tab", [SB_CARDS, FR_CARDS])
def test_a_column_of_the_figure_tables_starts_at_one_x_on_every_card_of_a_tab(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    tab,
    width,  # noqa: F811
):
    """S-1.9: of the cards sharing a column set, each column (the Metric text by its left edge, a
    figure column by its right edge, headers included) sits at one offset from its card's left edge
    on every card. Rejects auto-sized columns that move with the width of the figures they hold."""
    _prepare_cards(tmp_path, monkeypatch, tab)
    with _open(firefox, advisor_server, (width, al.VIEWPORT_HEIGHT), tab) as opened:
        groups = opened.evaluate(
            _TABLE_COLUMNS_JS, f'[data-testid="tab-panel-{tab}"] [data-testid="stats-table"]'
        )
    shared = {k: g for k, g in groups.items() if g["tables"] >= 2}
    assert shared, f"premise: two cards of the {tab} tab share a column set ({list(groups)})"
    problems = []
    for key, group in shared.items():
        for name, offsets in zip(key.split("|"), group["offsets"], strict=True):
            spread = max(offsets) - min(offsets)
            if spread > al.TOLERANCE_PX:
                problems.append(f'[{key}] column "{name}": {spread:.1f} px apart across the cards')
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("width", al.WIDTHS)
@pytest.mark.parametrize("tab", [SB_CARDS, FR_CARDS])
def test_each_field_of_a_card_starts_at_one_offset_from_its_cards_left_edge_on_every_card(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    tab,
    width,  # noqa: F811
):
    """S-1.9 and S-1.20 (TT-26): title, chips, figures table, notes and actions start at one offset
    from the card's left edge on every card. The reference's last row of a wrapped Frontrunner grid
    is stretched across the panel and lays its notes and actions out beside the figures, so five
    cards (four in a row at 1920) put them elsewhere."""
    _prepare_cards(tmp_path, monkeypatch, tab)
    _, entry_name, count = CARD_WORLDS[tab]
    entry = al.TABLE_GROUPS[entry_name]
    with _open(firefox, advisor_server, (width, al.VIEWPORT_HEIGHT), tab) as opened:
        found = al.measure(opened.page, al.FIELD_OFFSETS_JS, al.spec_of(entry, name=entry_name))
    assert found["found"] == count, f"{found['found']} cards, not {count}"
    assert not found["violations"], "\n".join(found["violations"])


@pytest.mark.parametrize("width", [1001, 1120, 1440, 1920, 2560])
@pytest.mark.parametrize("tab", [SB_CARDS, FR_CARDS])
def test_the_cards_of_a_tab_sit_in_equal_width_tracks_and_none_is_stretched(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    tab,
    width,  # noqa: F811
):
    """TT-26: the equal-track card grid. A card alone in its row keeps the track's width (the
    reference stretched it across the panel), and no card is wider than the panel's track."""
    _prepare_cards(tmp_path, monkeypatch, tab)
    _, entry_name, count = CARD_WORLDS[tab]
    entry = al.TABLE_GROUPS[entry_name]
    with _open(firefox, advisor_server, (width, al.VIEWPORT_HEIGHT), tab) as opened:
        widths = opened.evaluate(_CARD_WIDTHS_JS, entry["rows"])
    assert len(widths) == count, f"{len(widths)} cards, not {count}"
    assert max(widths) - min(widths) <= al.TOLERANCE_PX, f"card widths differ: {widths}"


@pytest.mark.parametrize("width", [1440, 1920, 2560])
def test_eight_proposals_sit_in_equal_tracks_with_each_field_at_one_offset(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    width,  # noqa: F811
):
    """S-1.20: eight pending proposals wrap onto two rows of a wide panel; the reference stretched
    the cards of a short last row across it and laid their notes and actions out beside the
    figures, so those fields started elsewhere than on the first row's cards."""
    _prepare(
        tmp_path,
        monkeypatch,
        world="empty",
        seed=lambda: adv.seed_frontrunner_proposals_varied(8),
    )
    entry = al.TABLE_GROUPS["pending_proposal_cards"]
    with _open(firefox, advisor_server, (width, al.VIEWPORT_HEIGHT), FR_CARDS) as opened:
        found = al.measure(
            opened.page, al.FIELD_OFFSETS_JS, al.spec_of(entry, name="eight_proposals")
        )
        widths = opened.evaluate(_CARD_WIDTHS_JS, entry["rows"])
    assert found["found"] == 8, f"{found['found']} cards, not 8"
    assert max(widths) - min(widths) <= al.TOLERANCE_PX, f"card widths differ: {widths}"
    assert not found["violations"], "\n".join(found["violations"])


@pytest.mark.parametrize("width", al.NARROW)
@pytest.mark.parametrize("tab", [SB_CARDS, FR_CARDS])
def test_below_1001_px_the_cards_keep_the_references_form_and_their_tab_does_not_scroll_sideways(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    tab,
    width,  # noqa: F811
):
    """Guard for AC-8: the narrow widths keep the reference's card grid (one card per row on a
    phone, as many tracks as fit at 1000 px) inside the panel, with no sideways scroll."""
    _prepare_cards(tmp_path, monkeypatch, tab)
    _, entry_name, count = CARD_WORLDS[tab]
    entry = al.TABLE_GROUPS[entry_name]
    with _open(firefox, advisor_server, (width, al.NARROW_VIEWPORT_HEIGHT[width]), tab) as opened:
        widths = opened.evaluate(_CARD_WIDTHS_JS, entry["rows"])
        tops = opened.evaluate(
            "(s) => [...document.querySelectorAll(s)].map((e) => Math.round(e.getBoundingClientRect().top))",
            entry["rows"],
        )
        sideways = opened.evaluate(_SIDEWAYS_JS)
    assert len(widths) == count
    if width < 1000:
        assert len(set(tops)) == count, f"cards share a row at {width} px: tops {tops}"
    assert max(widths) - min(widths) <= al.TOLERANCE_PX, f"card widths differ: {widths}"
    assert sideways["page"] == sideways["client"], "the page scrolls sideways"


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
# The autotune table states what the runs share in one sentence
# ---------------------------------------------------------------------------

_AUTOTUNE_JS = """() => {
  const norm = (t) => String(t || '').replace(/\\s+/g, ' ').trim();
  const line = [...document.querySelectorAll('#autotune-runs-list [data-testid="autotune-sentence"]')];
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
def test_eleven_runs_with_one_outcome_print_one_sentence_and_no_row_repeats_it(
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
        assert read["lines"][0]["text"] == (
            "Every symphony kept its previous settings in its latest run."
        ), read["lines"][0]["text"]
        assert read["lines"][0]["box"]["w"] > 100 and read["lines"][0]["box"]["h"] >= 10
        assert "Outcome" not in read["heads"] and "Sel t-stat" not in read["heads"], read["heads"]
        for row in read["rows"]:
            for word in ("N/A", "Frozen-eval", "Fallback"):
                assert word not in row["text"], f"a row repeats {word!r}: {row['text']!r}"
            assert row["name"] and row["time"], f"a row lost its symphony or run time: {row}"
        assert read["pageScroll"] == read["pageClient"], "the page scrolls sideways"


_NAV_JS = """() => {
  const panel = document.querySelector('[data-testid="advisor-nav-panel"]');
  const wrap = panel.querySelector('.cap-nav-wrap');
  const strip = panel.querySelector('[role="tablist"]');
  const shown = (el) => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const outside = [...panel.querySelectorAll('*')]
    .filter((el) => !wrap.contains(el) && el !== wrap && shown(el))
    .map((el) => el.tagName.toLowerCase() + ':' + (el.textContent || '').trim().slice(0, 40));
  const insideNotTab = [...wrap.querySelectorAll('*')]
    .filter((el) => !strip.contains(el) && el !== strip && shown(el))
    .map((el) => el.tagName.toLowerCase());
  const lastTab = [...strip.querySelectorAll('[role="tab"]')].pop().getBoundingClientRect();
  const words = [...panel.querySelectorAll('*')].filter((el) => shown(el) && !strip.contains(el)
    && [...el.childNodes].some((n) => n.nodeType === 3 && n.nodeValue.trim()));
  const pr = panel.getBoundingClientRect();
  const sr = strip.getBoundingClientRect();
  return { outside, insideNotTab, words: words.length, lastRight: lastTab.right,
           stripTop: sr.top, panelTop: pr.top, scroll: strip.scrollWidth, client: strip.clientWidth };
}"""


@pytest.mark.parametrize("size", [(1920, 1000), (1440, 900), (1240, 900)])
def test_the_navigation_panels_only_rendered_child_is_the_tab_bar(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    size,  # noqa: F811
):
    """No text, disclosure or link sits right of the last tab or above the tab bar, and every tab
    is whole in the strip."""
    _prepare(tmp_path, monkeypatch, world="empty")
    with _open(firefox, advisor_server, size, "overview") as opened:
        read = opened.evaluate(_NAV_JS)
        assert read["outside"] == [], f"the navigation panel renders more than the bar: {read}"
        assert read["insideNotTab"] == [], f"the bar's wrapper renders more than the tabs: {read}"
        assert read["words"] == 0, f"text sits in the navigation panel outside the tabs: {read}"
        assert read["scroll"] <= read["client"] + 1, f"a tab is out of view at {size[0]}: {read}"
        assert read["stripTop"] - read["panelTop"] < 40, f"something sits above the bar: {read}"


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
