"""A swap result and a logic-change result line up, read in a real browser.

Browser-only (marker ``browser``, deselected by default; run with
``python -m pytest -m browser -n 0 tests/ui/test_swap_logic_result_alignment_browser.py``). Firefox
headless through Playwright, imported inside a fixture; a missing Playwright or Firefox fails with
a message naming it, never a skip.

Each case serves the Advisor page over a seeded world (the real writers, ``tests/ui/
_page_explainers.py``), picks the tab, types into the real form and presses Evaluate. The page's own
script (``static/ai_advisor_asset_swaps.js`` or ``static/ai_advisor_logic_changes.js``) writes the
result from the body the real route returned (``tests/ui/_swap_logic_answers.py``): the browser
answers the evaluate request with that body, because the routes' Composer and model calls are
patched in this process only while the body is made. The one edit made to a body is named at its
call site: the one real candidate repeated, so the grid holds several cards.

Contract pinned (the plan's AC-1 and AC-2 for TT-14, TT-31 and TT-32, and AC-4 for the labels):

* In each Baseline and each Variant column of a result card every label starts at one x and every
  value ends at one x, within 2 px, and the widest label and the nearest value are at most 48 px
  apart (a row of label and value is two packed tracks, not the two ends of a stretched row).
* The cards of a result sit in equal-width tracks, and each field of a card (objective line,
  heading, figures, verdict) starts at one offset from its card's left edge on every card.
* Nothing a card prints leaves the card at 1000, 390 and 320 px, and the page does not scroll
  sideways.
* From 1001 px up (AC-10, every width of the plan's Table W), every label and value of both
  columns lies inside the card's inner edges, with the figures of the edge formats (a negative, a
  zero, the longest value, a dash) included, and a Variant column that does not fit beside the
  Baseline column sits under it at the same left edge; a second Evaluate that replaces earlier
  cards holds the same.
* The Baseline and Variant labels carry their Glossary line as their title, after the script
  writes them.
"""

from __future__ import annotations

import copy
import json
from contextlib import contextmanager

import pytest

from tests.app.conftest import _reset_scheduler_run_state  # noqa: F401  (autouse, health state)
from tests.ui import _alignment as al
from tests.ui import _page_copy as copy_fixture
from tests.ui import _page_explainers as harness
from tests.ui import _swap_logic_answers as answers
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

CARD_COUNT = 5
SWAP = {
    "entry": "swap_result_figures",
    "tab": "asset-swaps",
    "glossary_host": "Asset Swaps",
    "glossary_key": "W06",
    "url": "**/ai-advisor/asset-swaps/evaluate",
    "results": "#swap-results-area",
    "button": "#swap-evaluate-btn",
    "fill": [
        ("#swap-symphony-select", None),
        ("#swap-from-ticker", "QQQ"),
        ("#swap-to-ticker", "IALT"),
    ],
}
LOGIC = {
    "entry": "logic_change_result_figures",
    "tab": "logic-changes",
    "glossary_host": "Logic Changes",
    "glossary_key": "L05",
    "url": "**/ai-advisor/logic-changes/evaluate",
    "results": "#logic-results-area",
    "button": "#tweak-evaluate-btn",
    "fill": [
        ("#tweak-symphony-select", None),
        ("#tweak-description", "raise the threshold"),
    ],
}
KINDS = {"swap": SWAP, "logic": LOGIC}

_OVERFLOW_JS = """(selector) => {
  const root = document.querySelector(selector);
  const out = { cards: 0, leaks: [], pageScroll: document.documentElement.scrollWidth,
                pageClient: document.documentElement.clientWidth };
  for (const card of root.querySelectorAll('.sym-card')) {
    out.cards += 1;
    const box = card.getBoundingClientRect();
    for (const el of card.querySelectorAll('.stat-label, .stat-value, .stats-col-label')) {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && (r.left < box.left - 1 || r.right > box.right + 1)) {
        out.leaks.push((el.className || el.tagName) + ' ' + Math.round(r.left) + '..' + Math.round(r.right)
                       + ' outside ' + Math.round(box.left) + '..' + Math.round(box.right));
      }
    }
  }
  return out;
}"""

_WIDTH_JS = """(selector) => [...document.querySelectorAll(selector)]
  .filter((el) => el.getClientRects().length > 0)
  .map((el) => el.getBoundingClientRect().width)"""


def _body_with_cards(response, count: int) -> dict:
    """The route's body with its one real candidate repeated into ``count`` cards (a named edit)."""
    body = response.get_json()
    cards = list(body.get("survivors_detail") or []) + list(body.get("rejected_detail") or [])
    assert cards, "premise: the real route returned a candidate card"
    repeated = [copy.deepcopy(cards[0]) for _ in range(count)]
    body["survivors_detail"] = repeated[:2]
    body["rejected_detail"] = repeated[2:]
    return body


def _prepare(tmp_path, monkeypatch):
    harness.credential_free_environment(
        tmp_path / "page.env", monkeypatch.setattr, lambda n: monkeypatch.delenv(n, raising=False)
    )
    harness.seed_world("full", tmp_path, monkeypatch.setattr)
    import advisors.asset_swap_engine as ase

    monkeypatch.setenv("ANTHROPIC_API_KEY", harness.PLACEHOLDER_KEY)
    monkeypatch.setattr(ase, "_has_composer_key", lambda: True)


@contextmanager
def _evaluated(firefox, server, monkeypatch, name: str, width: int, edit=None):  # noqa: F811
    """The tab after Evaluate, over the real route's body written by the real script. ``edit``, when
    given, is a named change to the body's cards (the figures of the edge formats)."""
    kind = KINDS[name]
    response = (
        answers.swap_response(monkeypatch)
        if name == "swap"
        else answers.logic_response(monkeypatch)
    )
    body = _body_with_cards(response, CARD_COUNT)
    if edit is not None:
        for card in body["survivors_detail"] + body["rejected_detail"]:
            edit(card)
    body = json.dumps(body)
    with open_probe(
        firefox, server, width=width, height=al.NARROW_VIEWPORT_HEIGHT.get(width, 1000)
    ) as probe:
        page = probe.page
        page.route(
            kind["url"],
            lambda route: route.fulfill(status=200, content_type="application/json", body=body),
        )
        open_page(probe, "/ai-advisor")
        page.click(f'[data-tab="{kind["tab"]}"]:not([data-testid^="tab-panel"])')
        page.wait_for_selector(f'[data-testid="tab-panel-{kind["tab"]}"]', state="visible")
        for selector, text in kind["fill"]:
            if text is None:
                # The picker of the seeded world: the first symphony the page lists, or one added
                # here when the world lists none (the request is answered whatever it names).
                page.evaluate(
                    "(s) => { const el = document.querySelector(s);"
                    " if (el.options.length < 2) el.add(new Option('sym-x', 'sym-x'));"
                    " el.selectedIndex = 1; el.dispatchEvent(new Event('change', {bubbles: true})); }",
                    selector,
                )
            else:
                page.fill(selector, text)
        page.click(kind["button"])
        page.wait_for_selector(f"{kind['results']} .stats-grid", state="visible", timeout=15_000)
        page.evaluate("() => document.fonts.ready.then(() => true)")
        page.wait_for_timeout(400)
        yield probe
        assert probe.page_errors == [], probe.page_errors


def _figure_spec(name: str) -> dict:
    entry = al.TABLE_GROUPS[SWAP["entry"] if name == "swap" else LOGIC["entry"]]
    return {"name": name, **entry["figure_columns"]}


# Every label and value of a result card lies inside the card's inner edges (inside its border),
# at every width; where the Variant column does not sit beside the Baseline column it sits under it
# at the same left edge. A box counts when it has a width: a hidden label cannot leak.
_CONTAINMENT_JS = """(selector) => {
  const root = document.querySelector(selector);
  const out = { cards: 0, leaks: [], misplaced: [], sideBySide: 0, stacked: 0 };
  for (const card of root.querySelectorAll('.sym-card')) {
    const grid = card.querySelector('.stats-grid');
    if (!grid) continue;
    out.cards += 1;
    const box = card.getBoundingClientRect();
    const left = box.left + card.clientLeft;
    const right = box.right - (card.offsetWidth - card.clientWidth - card.clientLeft);
    const named = (el) => (el.className || el.tagName) + ' ' + (el.textContent || '').trim();
    for (const el of card.querySelectorAll('.stat-label, .stat-value, .stats-col-label')) {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && (r.left < left - 1 || r.right > right + 1)) {
        out.leaks.push(named(el) + ' ' + Math.round(r.left) + '..' + Math.round(r.right)
                       + ' outside ' + Math.round(left) + '..' + Math.round(right));
      }
    }
    const g = grid.getBoundingClientRect();
    if (g.left < left - 1 || g.right > right + 1) {
      out.leaks.push('stats-grid ' + Math.round(g.left) + '..' + Math.round(g.right)
                     + ' outside ' + Math.round(left) + '..' + Math.round(right));
    }
    const [a, b] = [...grid.children].map((c) => c.getBoundingClientRect());
    if (b.top >= a.bottom - 1) {
      out.stacked += 1;
      if (Math.abs(b.left - a.left) > 2) out.misplaced.push('Variant under Baseline at ' + Math.round(b.left) + ', not ' + Math.round(a.left));
    } else {
      out.sideBySide += 1;
      if (b.left < a.right - 1) out.misplaced.push('Variant overlaps Baseline');
    }
  }
  return out;
}"""


# ---------------------------------------------------------------------------
# S-1.19: the Baseline and Variant columns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", al.WIDTHS)
@pytest.mark.parametrize("name", list(KINDS))
def test_each_columns_labels_start_at_one_x_and_values_end_at_one_x_within_48_px_of_them(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    name,
    width,  # noqa: F811
):
    """Rejects a figure row stretched between its two ends (the reference spreads label and value
    across the whole column, 60 to 100 px apart), and a row whose value does not end at the column's
    one right edge."""
    _prepare(tmp_path, monkeypatch)
    with _evaluated(firefox, advisor_server, monkeypatch, name, width) as probe:
        found = al.measure(probe.page, al.FIGURE_COLUMNS_JS, _figure_spec(name))
    assert found["found"] == CARD_COUNT, f"{found['found']} result cards, not {CARD_COUNT}"
    assert len(found["columns"]) == 2 * CARD_COUNT, (
        f"{len(found['columns'])} Baseline and Variant columns, not {2 * CARD_COUNT}"
    )
    assert not found["violations"], "\n".join(found["violations"])


# Where a card's Variant column sits under its Baseline column the two blocks share one label track
# and one value track (PM ruling R2-2): every label of the card starts at one x and every value ends
# at one x, across both blocks. Side by side, each block keeps its own tracks. The measure reports,
# per card, how the blocks sit and the spread of the label starts and the value ends across both.
_SHARED_TRACKS_JS = """(selector) => {
  const root = document.querySelector(selector);
  const out = { cards: 0, stacked: 0, sideBySide: 0, readings: [] };
  const shown = (el) => el.getClientRects().length > 0;
  // The edges of the printed text, not of the element's box: a value left in a wider track has a
  // box that ends at the track edge and text that does not.
  const textEdges = (el) => {
    const range = document.createRange();
    range.selectNodeContents(el);
    const rects = [...range.getClientRects()].filter((x) => x.width > 0);
    return { left: Math.min(...rects.map((x) => x.left)), right: Math.max(...rects.map((x) => x.right)) };
  };
  for (const card of root.querySelectorAll('.sym-card')) {
    const grid = card.querySelector('.stats-grid');
    if (!grid) continue;
    out.cards += 1;
    const [a, b] = [...grid.children].map((c) => c.getBoundingClientRect());
    const stacked = b.top >= a.bottom - 1;
    if (stacked) out.stacked += 1; else out.sideBySide += 1;
    const labels = [...grid.querySelectorAll('.stat-label')].filter(shown)
      .map((el) => textEdges(el).left);
    const values = [...grid.querySelectorAll('.stat-value')].filter(shown)
      .map((el) => textEdges(el).right);
    const spread = (xs) => Math.max(...xs) - Math.min(...xs);
    out.readings.push({ stacked, labelSpread: spread(labels), valueSpread: spread(values),
                        labels: labels.length, values: values.length });
  }
  return out;
}"""


def _assert_stacked_blocks_share_tracks(read: dict) -> None:
    assert read["cards"] == CARD_COUNT, f"{read['cards']} result cards, not {CARD_COUNT}"
    problems = []
    for index, card in enumerate(read["readings"], start=1):
        if not card["stacked"]:
            continue
        if card["labelSpread"] > al.TOLERANCE_PX:
            problems.append(
                f"card {index}: labels of Baseline and Variant start {card['labelSpread']:.1f} px apart"
            )
        if card["valueSpread"] > al.TOLERANCE_PX:
            problems.append(
                f"card {index}: values of Baseline and Variant end {card['valueSpread']:.1f} px apart"
            )
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("width", al.WIDTHS)
@pytest.mark.parametrize("name", list(KINDS))
def test_blocks_stacked_in_a_card_share_one_label_track_and_one_value_track(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    name,
    width,  # noqa: F811
):
    """PM ruling R2-2 (S-1.19). Rejects each stacked block packing its own tracks (Variant's values
    ended 77 px left of Baseline's at 1920 on the Logic Changes cards), a shared label track whose
    values are left-aligned, and a shared track set for blocks that sit side by side."""
    _prepare(tmp_path, monkeypatch)
    with _evaluated(firefox, advisor_server, monkeypatch, name, width) as probe:
        read = probe.page.evaluate(_SHARED_TRACKS_JS, KINDS[name]["results"])
    _assert_stacked_blocks_share_tracks(read)


# The Logic Changes cards at 1920 are the width the review captured with Variant under Baseline (the
# Asset Swaps cards sit side by side at every Table W width). The test above holds nothing for a
# width where no card stacks, so this one pins that the captured card stacks and is measured.
def test_the_captured_stacked_logic_cards_stack_every_card_so_the_shared_track_rule_is_measured(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,  # noqa: F811
):
    name, width = "logic", 1920
    _prepare(tmp_path, monkeypatch)
    with _evaluated(firefox, advisor_server, monkeypatch, name, width) as probe:
        read = probe.page.evaluate(_SHARED_TRACKS_JS, KINDS[name]["results"])
    assert read["stacked"] == CARD_COUNT, (
        f"{read['stacked']} of {read['cards']} cards stack: {read}"
    )
    _assert_stacked_blocks_share_tracks(read)


def test_logic_change_blocks_still_sit_side_by_side_at_1440_with_their_own_tracks(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,  # noqa: F811
):
    """PM ruling R2-2: side by side stays as S-1.19 has it. Rejects fixing the stacked case by
    stacking every card, or by one grid of both blocks that keeps them under each other always."""
    _prepare(tmp_path, monkeypatch)
    with _evaluated(firefox, advisor_server, monkeypatch, "logic", 1440) as probe:
        read = probe.page.evaluate(_SHARED_TRACKS_JS, LOGIC["results"])
        columns = al.measure(probe.page, al.FIGURE_COLUMNS_JS, _figure_spec("logic"))
    assert read["sideBySide"] == CARD_COUNT, (
        f"{read['sideBySide']} of {read['cards']} cards side by side"
    )
    assert not columns["violations"], "\n".join(columns["violations"])


# The lines each label and value of a result card takes: a box that wraps has text rectangles at more
# than one top.
_LINES_JS = """(selector) => {
  const root = document.querySelector(selector);
  const out = { cards: 0, wrapped: [] };
  const linesOf = (el) => {
    const range = document.createRange();
    range.selectNodeContents(el);
    return new Set([...range.getClientRects()].filter((g) => g.width > 0)
      .map((g) => Math.round(g.top))).size;
  };
  for (const card of root.querySelectorAll('.sym-card')) {
    if (!card.querySelector('.stats-grid')) continue;
    out.cards += 1;
    for (const el of card.querySelectorAll('.stat-label, .stat-value')) {
      if (el.getClientRects().length === 0) continue;
      const lines = linesOf(el);
      if (lines > 1) out.wrapped.push(el.className + ' "' + el.textContent.trim() + '" takes ' + lines + ' lines');
    }
  }
  return out;
}"""


@pytest.mark.parametrize("width", al.WIDTHS)
@pytest.mark.parametrize("name", list(KINDS))
def test_every_label_and_value_of_a_result_card_takes_one_line_at_every_width(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    name,
    width,  # noqa: F811
):
    """PM ruling R2-2 with S-10.2: the two blocks do not squeeze into one card split over two lines;
    where they do not fit side by side, Variant drops under Baseline. Rejects a stats grid that never
    wraps its blocks (flex-wrap: nowrap kept the five Logic Changes cards side by side at 1920 with
    twenty labels and figures broken across two lines inside the card, which a containment test
    passes because nothing leaves the card)."""
    _prepare(tmp_path, monkeypatch)
    with _evaluated(firefox, advisor_server, monkeypatch, name, width) as probe:
        read = probe.page.evaluate(_LINES_JS, KINDS[name]["results"])
    assert read["cards"] == CARD_COUNT, f"{read['cards']} result cards, not {CARD_COUNT}"
    assert read["wrapped"] == [], "\n".join(read["wrapped"])


@pytest.mark.parametrize("width", [1001, 1920])
@pytest.mark.parametrize("name", list(KINDS))
def test_stacked_blocks_share_tracks_when_the_figures_are_at_the_edge_of_their_formats(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    name,
    width,  # noqa: F811
):
    """PM ruling R2-2 over a negative, a zero, the longest figure and a dash: the one value track is
    as wide as the longest value of either block, and every value still ends at its right edge."""

    def edge_figures(card: dict) -> None:
        card["baseline_stats"] = {**(card.get("baseline_stats") or {}), **_EDGE_BASELINE}
        card["variant_stats"] = {**(card.get("variant_stats") or {}), **_EDGE_VARIANT}

    _prepare(tmp_path, monkeypatch)
    with _evaluated(firefox, advisor_server, monkeypatch, name, width, edit=edge_figures) as probe:
        read = probe.page.evaluate(_SHARED_TRACKS_JS, KINDS[name]["results"])
        contained = probe.page.evaluate(_CONTAINMENT_JS, KINDS[name]["results"])
    if (name, width) == ("logic", 1920):
        assert read["stacked"] >= 1, f"premise: the wide figures stack the blocks: {read}"
    _assert_stacked_blocks_share_tracks(read)
    _assert_contained(contained, CARD_COUNT)


@pytest.mark.parametrize("name", list(KINDS))
def test_a_figure_row_keeps_one_label_and_one_value_in_that_order(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    name,  # noqa: F811
):
    """The content of the rows is the reference's (guard): the label comes first, then the value, on
    one line each, and a column holds as many rows as the script writes for it."""
    _prepare(tmp_path, monkeypatch)
    with _evaluated(firefox, advisor_server, monkeypatch, name, 1440) as probe:
        rows = probe.page.evaluate(
            """(root) => [...document.querySelectorAll(root + ' .stat-row')].map((row) => {
              const l = row.querySelector('.stat-label'), v = row.querySelector('.stat-value');
              const lr = l.getBoundingClientRect(), vr = v.getBoundingClientRect();
              return { label: l.textContent.trim(), value: v.textContent.trim(),
                       order: lr.left < vr.left, oneLine: lr.height < 24 && vr.height < 24 };
            })""",
            KINDS[name]["results"],
        )
    per_card = 3 if name == "swap" else 4
    assert len(rows) == 2 * per_card * CARD_COUNT
    assert all(r["order"] and r["oneLine"] and r["value"] for r in rows), rows


# ---------------------------------------------------------------------------
# AC-10 (S-10.2): nothing a result card prints is clipped, at any width
# ---------------------------------------------------------------------------


def _assert_contained(read: dict, count: int) -> None:
    assert read["cards"] == count, f"{read['cards']} result cards, not {count}"
    assert read["leaks"] == [], "\n".join(read["leaks"])
    assert read["misplaced"] == [], "\n".join(read["misplaced"])


@pytest.mark.parametrize("width", al.WIDTHS)
@pytest.mark.parametrize("name", list(KINDS))
def test_every_label_and_value_of_a_result_card_lies_inside_the_card_at_every_width(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    name,
    width,  # noqa: F811
):
    """S-10.2 (G-38): the reference's two content-width columns overflow a narrow card (the Logic
    Changes variant column is cut by the card edge at 1920); a column that does not fit beside the
    other sits under it. Rejects a layout that keeps both beside each other whatever the card
    width, and one that clips by hiding the overflow instead of fitting."""
    _prepare(tmp_path, monkeypatch)
    with _evaluated(firefox, advisor_server, monkeypatch, name, width) as probe:
        read = probe.page.evaluate(_CONTAINMENT_JS, KINDS[name]["results"])
    _assert_contained(read, CARD_COUNT)


@pytest.mark.parametrize("width", [1001, 1920])
@pytest.mark.parametrize("name", list(KINDS))
def test_a_tab_with_no_result_shows_no_card_and_overflows_its_panel_nowhere(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    name,
    width,  # noqa: F811
):
    """S-10.9: before any Evaluate the results area holds no card, and the tab's panel holds
    everything it shows inside its own content width (the empty state sits inside its panel)."""
    _prepare(tmp_path, monkeypatch)
    kind = KINDS[name]
    with open_probe(
        firefox, advisor_server, width=width, height=al.NARROW_VIEWPORT_HEIGHT.get(width, 1000)
    ) as probe:
        page = probe.page
        open_page(probe, "/ai-advisor")
        page.click(f'[data-tab="{kind["tab"]}"]:not([data-testid^="tab-panel"])')
        page.wait_for_selector(f'[data-testid="tab-panel-{kind["tab"]}"]', state="visible")
        read = page.evaluate(
            """([panelSel, resultsSel]) => {
              const panel = document.querySelector(panelSel);
              const box = panel.getBoundingClientRect();
              const results = document.querySelector(resultsSel);
              const r = results.getBoundingClientRect();
              return { cards: results.querySelectorAll('.sym-card').length,
                       sideways: panel.scrollWidth - panel.clientWidth,
                       resultsInside: r.width === 0 || (r.left >= box.left - 1 && r.right <= box.right + 1) };
            }""",
            [f'[data-testid="tab-panel-{kind["tab"]}"]', kind["results"]],
        )
        assert probe.page_errors == [], probe.page_errors
    assert read["cards"] == 0, read
    assert read["sideways"] <= 1, read
    assert read["resultsInside"], read


# Figures at the edge of their formats: a negative, a zero, the longest value a script can print (an
# unrounded sixteen-digit float) and a missing figure (a dash). Named edits to the one real body.
_EDGE_BASELINE = {
    "sharpe_ratio": 1.2738249853431045,
    "sortino_ratio": 0,
    "max_drawdown": None,
    "annual_return": -0.12345678901234568,
}
_EDGE_VARIANT = {
    "sharpe_ratio": -12.345678901234567,
    "sortino_ratio": 123456.78901234567,
    "max_drawdown": -0.9999999999999999,
    "annual_return": None,
}


@pytest.mark.parametrize("width", [1001, 1120, 1920])
@pytest.mark.parametrize("name", list(KINDS))
def test_figures_at_the_edge_of_their_formats_stay_inside_their_card(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    name,
    width,  # noqa: F811
):
    """S-10.6: the longest figure, a negative, a zero and a dash at the narrowest desktop widths
    (and at 1920, where a card is narrowest against its content)."""
    _prepare(tmp_path, monkeypatch)
    kind = KINDS[name]
    response = (
        answers.swap_response(monkeypatch)
        if name == "swap"
        else answers.logic_response(monkeypatch)
    )
    body = _body_with_cards(response, CARD_COUNT)
    for card in body["survivors_detail"] + body["rejected_detail"]:
        card["baseline_stats"] = {**(card.get("baseline_stats") or {}), **_EDGE_BASELINE}
        card["variant_stats"] = {**(card.get("variant_stats") or {}), **_EDGE_VARIANT}
    payload = json.dumps(body)
    with open_probe(
        firefox, advisor_server, width=width, height=al.NARROW_VIEWPORT_HEIGHT.get(width, 1000)
    ) as probe:
        page = probe.page
        page.route(
            kind["url"],
            lambda route: route.fulfill(status=200, content_type="application/json", body=payload),
        )
        open_page(probe, "/ai-advisor")
        page.click(f'[data-tab="{kind["tab"]}"]:not([data-testid^="tab-panel"])')
        page.wait_for_selector(f'[data-testid="tab-panel-{kind["tab"]}"]', state="visible")
        for selector, text in kind["fill"]:
            if text is None:
                page.evaluate(
                    "(s) => { const el = document.querySelector(s);"
                    " if (el.options.length < 2) el.add(new Option('sym-x', 'sym-x'));"
                    " el.selectedIndex = 1; el.dispatchEvent(new Event('change', {bubbles: true})); }",
                    selector,
                )
            else:
                page.fill(selector, text)
        page.click(kind["button"])
        page.wait_for_selector(f"{kind['results']} .stats-grid", state="visible", timeout=15_000)
        page.evaluate("() => document.fonts.ready.then(() => true)")
        page.wait_for_timeout(300)
        read = page.evaluate(_CONTAINMENT_JS, kind["results"])
        shown = page.eval_on_selector_all(
            f"{kind['results']} .stat-value", "(els) => els.map((e) => e.textContent.trim())"
        )
        assert probe.page_errors == [], probe.page_errors
    assert "—" in shown, f"premise: a missing figure prints a dash: {shown}"
    _assert_contained(read, CARD_COUNT)


@pytest.mark.parametrize("width", [1001, 1120, 1920])
def test_new_logic_change_results_replace_the_old_cards_and_each_new_card_is_contained(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    width,  # noqa: F811
):
    """S-10.11: Evaluate pressed while earlier cards show; the new body replaces them and every new
    card meets S-10.2. Rejects a result written once whose layout is not applied to a rewrite."""
    _prepare(tmp_path, monkeypatch)
    response = answers.logic_response(monkeypatch)
    first = json.dumps(_body_with_cards(response, CARD_COUNT))
    second_count = 3
    second = json.dumps(_body_with_cards(response, second_count))
    kind = LOGIC
    with open_probe(
        firefox, advisor_server, width=width, height=al.NARROW_VIEWPORT_HEIGHT.get(width, 1000)
    ) as probe:
        page = probe.page
        page.route(
            kind["url"],
            lambda route: route.fulfill(status=200, content_type="application/json", body=first),
        )
        open_page(probe, "/ai-advisor")
        page.click(f'[data-tab="{kind["tab"]}"]:not([data-testid^="tab-panel"])')
        page.wait_for_selector(f'[data-testid="tab-panel-{kind["tab"]}"]', state="visible")
        page.evaluate(
            "(s) => { const el = document.querySelector(s);"
            " if (el.options.length < 2) el.add(new Option('sym-x', 'sym-x'));"
            " el.selectedIndex = 1; el.dispatchEvent(new Event('change', {bubbles: true})); }",
            kind["fill"][0][0],
        )
        page.fill(kind["fill"][1][0], kind["fill"][1][1])
        page.click(kind["button"])
        page.wait_for_selector(f"{kind['results']} .stats-grid", state="visible", timeout=15_000)
        assert page.locator(f"{kind['results']} .sym-card").count() == CARD_COUNT
        page.unroute(kind["url"])
        page.route(
            kind["url"],
            lambda route: route.fulfill(status=200, content_type="application/json", body=second),
        )
        page.click(kind["button"])
        page.wait_for_function(
            "([sel, n]) => document.querySelectorAll(sel + ' .sym-card').length === n",
            arg=[kind["results"], second_count],
            timeout=15_000,
        )
        page.evaluate("() => document.fonts.ready.then(() => true)")
        page.wait_for_timeout(300)
        read = page.evaluate(_CONTAINMENT_JS, kind["results"])
        assert probe.page_errors == [], probe.page_errors
    _assert_contained(read, second_count)


# ---------------------------------------------------------------------------
# TT-31 and TT-32: the cards share equal tracks and one offset per field (guards)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", [1001, 1440, 1920, 2560])
@pytest.mark.parametrize("name", list(KINDS))
def test_the_cards_of_a_result_have_one_width_and_each_field_starts_at_one_offset(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    name,
    width,  # noqa: F811
):
    """Guard: the reference's card grid already holds this, and the figure change must keep it. It
    rejects a figures layout that gives one card a different width or moves a field in one card."""
    _prepare(tmp_path, monkeypatch)
    entry = al.TABLE_GROUPS[KINDS[name]["entry"]]
    with _evaluated(firefox, advisor_server, monkeypatch, name, width) as probe:
        found = al.measure(
            probe.page, al.FIELD_OFFSETS_JS, al.spec_of(entry, name=name, rows=entry["rows"])
        )
        widths = probe.page.evaluate(_WIDTH_JS, entry["rows"])
    assert found["found"] == CARD_COUNT, f"{found['found']} cards, not {CARD_COUNT}"
    assert max(widths) - min(widths) <= al.TOLERANCE_PX, f"card widths differ: {widths}"
    assert not found["violations"], "\n".join(found["violations"])


@pytest.mark.parametrize("width", al.NARROW)
@pytest.mark.parametrize("name", list(KINDS))
def test_no_figure_leaves_its_card_below_1001_px_and_the_page_does_not_scroll_sideways(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    name,
    width,  # noqa: F811
):
    """Guard for AC-8: the narrow widths keep the reference's stacked cards, and packing the figure
    rows must not push a value out of its card."""
    _prepare(tmp_path, monkeypatch)
    with _evaluated(firefox, advisor_server, monkeypatch, name, width) as probe:
        read = probe.page.evaluate(_OVERFLOW_JS, KINDS[name]["results"])
    assert read["cards"] == CARD_COUNT
    assert read["leaks"] == [], read["leaks"]
    assert read["pageScroll"] == read["pageClient"], "the page scrolls sideways"


# ---------------------------------------------------------------------------
# TG3-16: the labels the script writes carry their Glossary line as their title
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", list(KINDS))
def test_the_baseline_and_variant_labels_the_script_writes_carry_their_glossary_line_as_title(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    name,  # noqa: F811
):
    kind = KINDS[name]
    line = next(
        x for x in copy_fixture.glossary(kind["glossary_host"]) if x["key"] == kind["glossary_key"]
    )
    wanted = " ".join(line["text"].split())
    _prepare(tmp_path, monkeypatch)
    with _evaluated(firefox, advisor_server, monkeypatch, name, 1440) as probe:
        titles = probe.page.eval_on_selector_all(
            f"{kind['results']} .stats-col-label",
            "(els) => els.map((e) => [e.textContent.trim(), e.getAttribute('title')])",
        )
    assert len(titles) == 2 * CARD_COUNT, titles
    assert {t[0] for t in titles} == {"Baseline", "Variant"}
    problems = [
        f"{t[0]}: title is {t[1]!r}" for t in titles if " ".join((t[1] or "").split()) != wanted
    ]
    assert not problems, "\n".join(problems)
