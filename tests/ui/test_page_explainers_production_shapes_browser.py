"""Layouts that failed on production-shaped data, read in a real browser.

Browser-only (marker ``browser``; run with
``python -m pytest -m "browser and not live and not slow and not perf" -n 0 <this file>``).

The slice's tests pass on the plan's seeded worlds. A review that read a copy of the production
database found layouts that fail on the data the product really holds: a long Market Prism
rationale, eleven tenure entries with long redundancy text, autotune rows from one weekly batch, a
real hero headline. Each case here builds that state (``tests/ui/_page_explainers_production_shapes.py``
says what each seed is derived from) and asserts the layout contract, never a pixel value:

* the guard-effect description reads under the headline at full width (1440 and 1240);
* the detail panel's opening description is inset like its siblings;
* the Advisor tab strip shows all eight tabs whole whichever tab is active, and does so for any
  tab text width, because font metrics differ between machines (the production capture's Firefox
  measured 909 px for tabs this one measures at 872 px);
* the Market Prism disclosure sits directly under its description;
* the tenure entries stack in one column on a phone;
* History's tallest bar does not touch the description above the chart.
"""

from __future__ import annotations

import pytest

from tests.app import _retirement_tab_world as w
from tests.app import _tenure_review_panel_builders as pb
from tests.app.conftest import _reset_scheduler_run_state  # noqa: F401  (autouse, health state)
from tests.ui import _alignment as al
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
from tests.ui.test_page_explainers_advisor_browser import (  # noqa: F401  (helpers and fixtures)
    DESKTOPS,
    PHONE,
    TABS,
    VERY_NARROW,
    _open,
    _prepare,
)
from tests.ui.test_page_explainers_dashboard_browser import (  # noqa: F401
    _Dashboard,
    _open_first_card,
    seeded,
)

pytestmark = pytest.mark.browser

# The description under the guard effect is one sentence; four lines is the most a full-width
# line of this length takes at either desktop width.
DESCRIPTOR_MAX_LINES = 4
TOUCHING_GAP_PX = 4
DISCLOSURE_GAP_PX = 16


# ---------------------------------------------------------------------------
# The dashboard hero and the detail panel
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", [1440, 1240])
def test_the_guard_effect_description_reads_under_the_headline_at_full_width(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,
    width,
):
    seeded("dashboard_full")
    with _Dashboard(firefox, advisor_server, width) as probe:
        read = probe.page.evaluate(prod.HERO_JS)
    headline, descriptor = read["headline"], read["descriptor"]
    assert abs(descriptor["left"] - headline["left"]) <= 1, (
        f"the description does not start under the headline: {read}"
    )
    assert descriptor["top"] >= headline["bottom"] - 1, (
        f"the description sits beside the headline, not under it: {read}"
    )
    assert read["lines"] <= DESCRIPTOR_MAX_LINES, (
        f"the description wraps to {read['lines']} lines in a squeezed column: {read}"
    )


_ROW_FIGURES_JS = """() => {
  const lefts = (suffix) => [...document.querySelectorAll(
      '[data-testid^="comp-"][data-testid$="-' + suffix + '-text"]')]
    .filter((el) => el.getClientRects().length > 0)
    .map((el) => ({ id: el.getAttribute('data-testid'), left: el.getBoundingClientRect().left }));
  return { bot: lefts('bot'), held: lefts('held') };
}"""


@pytest.mark.parametrize("width", [1440, 1240])
def test_the_figures_of_every_hero_row_line_up_under_each_other_when_a_row_has_no_gap_figure(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,
    width,
):
    """The Max DD row hides its gap figure while it waits for history; its Bot and Held figures
    must still sit under those of the rows above, not shift right into the gap's place."""
    seeded("dashboard_full")
    with _Dashboard(firefox, advisor_server, width) as probe:
        hidden = probe.page.evaluate(
            '() => [...document.querySelectorAll(\'[data-testid^="comp-"][data-testid$="-delta"]\')]'
            ".filter((el) => el.hidden).length"
        )
        assert hidden >= 1, "premise: a row's gap figure is hidden in this state"
        read = probe.page.evaluate(_ROW_FIGURES_JS)
    for kind in ("bot", "held"):
        figures = read[kind]
        assert len(figures) >= 3, f"premise: three rows show their {kind} figure: {figures}"
        spread = max(f["left"] for f in figures) - min(f["left"] for f in figures)
        assert spread <= 1, f"the {kind} figures of the hero rows do not line up: {figures}"


@pytest.mark.parametrize("width", [1440, 375])
def test_the_detail_panels_opening_description_and_its_glossary_are_inset_like_the_lines_between(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,
    width,
):
    seeded("dashboard_full")
    with _Dashboard(firefox, advisor_server, width) as probe:
        _open_first_card(probe)
        read = probe.page.evaluate(prod.DETAIL_LEAD_JS)
    for part in ("lead", "glossary"):
        assert read[part] is not None, f"the panel has no {part}"
        assert read[part]["left"] >= read["sectionContentLeft"] - 1, (
            f"the {part} sits at {read[part]['left']}, left of the section text at "
            f"{read['sectionContentLeft']}: {read}"
        )


# ---------------------------------------------------------------------------
# The Advisor tab strip
# ---------------------------------------------------------------------------

# Two widenings: the production capture's measured ratio, and a much larger one that no fixed
# column sized for either machine can hold.
TAB_TEXT_WIDENINGS = [prod.WIDER_TAB_TEXT_CSS, ".cap-tab { letter-spacing: 0.08em; }"]


@pytest.mark.parametrize("css", TAB_TEXT_WIDENINGS)
@pytest.mark.parametrize("size", [(1240, 900), (1440, 900), (1920, 1000)])
def test_all_eight_tabs_show_whole_whichever_tab_is_active_and_whatever_their_text_width(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    tmp_path,
    monkeypatch,
    size,
    css,
):
    """Font metrics differ between machines, so the strip may not depend on an exact text width.

    The production capture's Firefox measured the eight tabs at 909 px; this one measures 872 px.
    The widening stands in for that difference, and a fixed column narrower than the tabs'
    max-content width fails here while a layout that fits the tabs passes.
    """
    _prepare(tmp_path, monkeypatch, seed=lambda: adv.seed_frontrunner_proposals(2))
    with _open(firefox, advisor_server, size, "overview") as opened:
        opened.page.add_style_tag(content=css)
        problems: dict[str, list[str]] = {}
        for tab in TABS:
            opened.page.click(f'[role="tab"][data-tab="{tab}"]')
            opened.page.wait_for_timeout(150)
            state = opened.evaluate(prod.TABS_JS)
            strip = state["strip"]
            if strip["scrollWidth"] > strip["clientWidth"] + 1:
                problems.setdefault(
                    f"the tabs need {strip['scrollWidth']} px of a {strip['clientWidth']} px strip",
                    [],
                ).append(tab)
            for t in state["tabs"]:
                if t["left"] < strip["left"] - 0.5 or t["right"] > strip["right"] + 0.5:
                    problems.setdefault(f"tab {t['name']} is cut by the strip's edge", []).append(
                        tab
                    )
            for cue in state["cues"]:
                if cue["shown"]:
                    problems.setdefault(f"the {cue['side']} fade shows over the strip", []).append(
                        tab
                    )
            assert state["pageScroll"] == state["pageClient"], "the page scrolls sideways"
        assert not problems, f"at {size[0]} px (with each of these tabs active):\n" + "\n".join(
            f"{message}: {', '.join(tabs)}" for message, tabs in problems.items()
        )


# ---------------------------------------------------------------------------
# The Overview: Market Prism and the autotune table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("size", [(1920, 1000), (1440, 900)])
def test_the_market_prism_description_sits_under_its_heading_and_the_stamp_follows_the_item_before_it(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    tmp_path,
    monkeypatch,
    size,
):
    """A council-length rationale: the captured fixture's 67-character one hides the defect. The
    description reads under the heading, the rationale under the description at the same left edge
    (never beside it), and the as-of stamp starts within 24 px of the item before it."""
    _prepare(tmp_path, monkeypatch, world="full", seed=prod.seed_long_prism_rationale)
    with _open(firefox, advisor_server, size, "overview") as opened:
        read = opened.evaluate(prod.PRISM_JS)
        assert read["description"] and read["rationale"], f"premise: both render: {read}"
        gap = read["description"]["top"] - read["header"]["bottom"]
        assert -1 <= gap <= DISCLOSURE_GAP_PX, f"the description is {gap:.0f} px under the heading"
        assert read["rationale"]["top"] >= read["description"]["bottom"] - 1, (
            f"the rationale sits beside the description: {read}"
        )
        assert abs(read["rationale"]["left"] - read["description"]["left"]) <= 2, read
        row = al.measure(
            opened.page,
            al.ROW_START_JS,
            al.spec_of(al.TOOLBAR_ROWS["market_prism_header"], name="market_prism_header"),
        )
        assert row["found"] == 1, "the Market Prism header row is not on the page"
        assert row["violations"] == [], row["violations"]


# ---------------------------------------------------------------------------
# The Retirement tab's tenure review
# ---------------------------------------------------------------------------


def _retirement_with_production_tenure_review():
    w.seed_roster()
    adv.seed_recommendation_cards(3)
    pb.store_run(pb.undecided_record())


@pytest.mark.parametrize("size", [PHONE, VERY_NARROW])
def test_the_evidence_items_of_a_tenure_entry_stack_in_one_column_on_a_phone(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    tmp_path,
    monkeypatch,
    size,
):
    _prepare(tmp_path, monkeypatch, world="empty", seed=_retirement_with_production_tenure_review)
    with _open(firefox, advisor_server, size, "retirement") as opened:
        entries = opened.evaluate(prod.TENURE_ENTRIES_JS)
        assert len(entries) == 11
        columns = {
            index: sorted({round(item["left"]) for item in entry["items"]})
            for index, entry in enumerate(entries)
        }
        assert all(len(c) == 1 for c in columns.values()), (
            f"the evidence items of an entry share a row at {size[0]} px: {columns}"
        )
        state = opened.evaluate(prod.TABS_JS)
        assert state["pageScroll"] == state["pageClient"]


_VISIBLE_ORDER = ("performance", "composite", "backtest", "marginal", "long_vix", "redundancy")


def _tenure_spec(**extra) -> dict:
    return al.spec_of(al.TABLE_GROUPS["tenure_cards"], name="tenure_cards", **extra)


@pytest.mark.parametrize("width", al.WIDTHS)
def test_every_tenure_cards_six_evidence_cells_start_at_one_x_in_the_visible_order(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    tmp_path,
    monkeypatch,
    width,
):
    """S-1.8: eleven cards, one of them not yet eligible, from the real review compute."""
    _prepare(tmp_path, monkeypatch, world="empty", seed=_retirement_with_production_tenure_review)
    with _open(firefox, advisor_server, (width, 1000), "retirement") as opened:
        result = al.measure(opened.page, al.FIELD_OFFSETS_JS, _tenure_spec())
        entries = opened.evaluate(prod.TENURE_ENTRIES_JS)
    assert result["found"] == 11, "eleven tenure cards are measured"
    assert result["violations"] == [], result["violations"]
    assert [f for f in result["fields"]] == [
        "performance",
        "composite score",
        "backtest",
        "marginal contribution",
        "long vix hedge",
        "redundancy",
    ], "the registry names the six cells in the visible order"
    for number, entry in enumerate(entries, start=1):
        lefts = [item["left"] for item in entry["items"]]
        tops = [round(item["top"]) for item in entry["items"]]
        assert len(lefts) == 6, f"card {number} holds {len(lefts)} evidence cells"
        by_position = [item["name"] for item in sorted(entry["items"], key=lambda i: i["left"])]
        assert by_position == list(_VISIBLE_ORDER), (
            f"card {number}: the cells read {by_position} from left to right"
        )
        assert lefts == sorted(lefts) and len(set(round(x) for x in lefts)) == 6, (
            f"card {number}: the cells do not run left to right in the visible order: {lefts}"
        )
        assert max(tops) - min(tops) <= 2, f"card {number}: the cells are not on one row: {tops}"


def test_the_not_yet_eligible_card_keeps_all_six_cells_where_the_other_cards_keep_theirs(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    tmp_path,
    monkeypatch,
):
    _prepare(tmp_path, monkeypatch, world="empty", seed=_retirement_with_production_tenure_review)
    young = pb.undecided_id(pb.UNDECIDED_YOUNG)
    with _open(firefox, advisor_server, (1440, 900), "retirement") as opened:
        rows = opened.evaluate(
            """(young) => [...document.querySelectorAll('[data-testid="tenure-review-entry"]')]
              .map((card) => ({ young: card.getAttribute('data-symphony-id') === young,
                lefts: [...card.querySelectorAll('.tenure-review-evidence')]
                  .map((c) => c.getBoundingClientRect().left - card.getBoundingClientRect().left) }))""",
            young,
        )
    assert sum(1 for r in rows if r["young"]) == 1
    reference = next(r for r in rows if not r["young"])["lefts"]
    for row in rows:
        assert len(row["lefts"]) == 6
        assert all(abs(a - b) <= 2 for a, b in zip(row["lefts"], reference, strict=True)), (
            f"a card's cells are offset from the others': {row['lefts']} against {reference}"
        )


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


def test_the_tallest_history_bar_does_not_touch_the_description_above_the_chart(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,
):
    seeded("full")
    with open_probe(firefox, advisor_server, width=1440, height=900) as probe:
        open_page(probe, "/history")
        probe.page.wait_for_timeout(harness.SETTLE_MS)
        read = probe.page.evaluate(prod.HISTORY_BARS_JS)
    assert read["barTops"], f"premise: the chart draws bars: {read}"
    gap = min(read["barTops"]) - read["noteBottom"]
    assert gap >= TOUCHING_GAP_PX, (
        f"the tallest bar starts {gap:.1f} px under the description (note bottom "
        f"{read['noteBottom']:.1f}, bar top {min(read['barTops']):.1f})"
    )
