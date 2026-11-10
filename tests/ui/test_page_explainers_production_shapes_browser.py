"""Layouts that failed on production-shaped data, read in a real browser.

Browser-only (marker ``browser``; run with
``python -m pytest -m "browser and not live and not slow and not perf" -n 0 <this file>``).

The slice's tests pass on the plan's seeded worlds. A review that read a copy of the production
database found layouts that fail on the data the product really holds: a long Market Prism
rationale, eleven tenure entries with long redundancy text, autotune rows from one weekly batch, a
real hero headline. Each case here builds that state (``tests/ui/_page_explainers_production_shapes.py``
says what each seed is derived from) and asserts the layout contract, never a pixel value:

* the guard-effect description reads under the headline at full width and the hero holds no empty
  region (1440 and 1240);
* the detail panel's opening description is inset like its siblings;
* the Advisor tab strip shows all eight tabs whole whichever tab is active, and does so for any
  tab text width, because font metrics differ between machines (the production capture's Firefox
  measured 909 px for tabs this one measures at 872 px);
* the Market Prism disclosure sits directly under its description and the block holds no hole;
* the tenure entries hold no empty region and stack in one column on a phone;
* the autotune table holds no band between its name and its count;
* History's tallest bar does not touch the description above the chart.
"""

from __future__ import annotations

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
from tests.ui.test_page_explainers_advisor_browser import (  # noqa: F401  (helpers and fixtures)
    DESKTOPS,
    PHONE,
    TABS,
    VERY_NARROW,
    _empty_regions,
    _open,
    _prepare,
    _scoped,
)
from tests.ui.test_page_explainers_dashboard_browser import (  # noqa: F401
    _Dashboard,
    _open_first_card,
    seeded,
)

pytestmark = pytest.mark.browser

MIN_W, MIN_H = 240, 80
# The description under the guard effect is one sentence; four lines is the most a full-width
# line of this length takes at either desktop width.
DESCRIPTOR_MAX_LINES = 4
TOUCHING_GAP_PX = 4
DISCLOSURE_GAP_PX = 16


def _offenders(found: list[dict]) -> list[str]:
    return [
        f"{f['testid'] or f['id'] or f['cls']} #{f['index']}: empty "
        f"{f['largest_empty']['w']:.0f}x{f['largest_empty']['h']:.0f} at "
        f"({f['largest_empty']['x']:.0f},{f['largest_empty']['y']:.0f})"
        for f in found
        if f["largest_empty"] is not None
    ]


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


@pytest.mark.parametrize("width", [1440, 1240])
def test_the_hero_holds_no_empty_region_once_the_headline_has_a_real_figure(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,
    width,
):
    """The production hero: a real headline, Ann. vol hidden and Max DD still waiting."""
    seeded("dashboard_full")
    with _Dashboard(firefox, advisor_server, width) as probe:
        probe.page.evaluate(prod.HERO_JS)
        probe.page.wait_for_timeout(150)
        found = probe.page.evaluate(harness.EMPTY_REGION_JS, [".hero-section", MIN_W, MIN_H])
    assert found, "premise: the hero is measured"
    assert not _offenders(found), "empty regions in the hero:\n" + "\n".join(_offenders(found))


@pytest.mark.parametrize("width", [1440, 375])
def test_the_detail_panels_opening_description_is_inset_like_the_lines_below_it(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,
    width,
):
    seeded("dashboard_full")
    with _Dashboard(firefox, advisor_server, width) as probe:
        _open_first_card(probe)
        read = probe.page.evaluate(prod.DETAIL_LEAD_JS)
    for part in ("lead", "disclosure"):
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


@pytest.mark.parametrize("size", DESKTOPS)
def test_the_market_prism_disclosure_follows_its_description_and_the_block_holds_no_hole(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    tmp_path,
    monkeypatch,
    size,
):
    """A council-length rationale: the captured fixture's 67-character one hides the defect."""
    _prepare(tmp_path, monkeypatch, world="full", seed=prod.seed_long_prism_rationale)
    with _open(firefox, advisor_server, size, "overview") as opened:
        read = opened.evaluate(prod.PRISM_JS)
        assert read["description"] and read["disclosure"], f"premise: both render: {read}"
        gap = read["disclosure"]["top"] - read["description"]["bottom"]
        assert gap <= DISCLOSURE_GAP_PX, (
            f"the disclosure lands {gap:.0f} px under its description: {read}"
        )
        selector = ".prism-block, " + _scoped('[data-testid="tab-panel-overview"]')
        found = opened.evaluate(harness.EMPTY_REGION_JS, [selector, MIN_W, MIN_H])
        assert not _offenders(found), "empty regions in the Overview:\n" + "\n".join(
            _offenders(found)
        )


def test_the_autotune_table_holds_no_band_between_a_name_and_its_count(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    tmp_path,
    monkeypatch,
):
    """Every symphony's latest run from one weekly batch, as in production."""
    _prepare(tmp_path, monkeypatch, world="empty", seed=prod.seed_same_batch_autotune_runs)
    with _open(firefox, advisor_server, (1440, 900), "overview") as opened:
        opened.page.wait_for_selector(
            '#autotune-runs-list [data-testid="autotune-run-row"]', state="attached"
        )
        opened.page.wait_for_timeout(200)
        line = opened.evaluate(
            "() => [...document.querySelectorAll('[data-testid=\"autotune-shared-line\"]')]"
            ".map((e) => e.innerText)"
        )
        assert len(line) == 1 and "Run time" in line[0], f"premise: the run time is lifted: {line}"
        selector = ".autotune-panel, " + _scoped('[data-testid="tab-panel-overview"]')
        found = opened.evaluate(harness.EMPTY_REGION_JS, [selector, MIN_W, MIN_H])
        assert not _offenders(found), "empty regions in the Overview:\n" + "\n".join(
            _offenders(found)
        )


# ---------------------------------------------------------------------------
# The Retirement tab's tenure review
# ---------------------------------------------------------------------------


def _retirement_with_production_tenure_review():
    w.seed_roster()
    adv.seed_recommendation_cards(3)
    prod.seed_production_tenure_review(11)


@pytest.mark.parametrize("size", DESKTOPS)
def test_eleven_tenure_entries_with_long_redundancy_text_hold_no_empty_region(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    tmp_path,
    monkeypatch,
    size,
):
    _prepare(tmp_path, monkeypatch, world="empty", seed=_retirement_with_production_tenure_review)
    with _open(firefox, advisor_server, size, "retirement") as opened:
        entries = opened.evaluate(prod.TENURE_ENTRIES_JS)
        assert len(entries) == 11, f"premise: eleven entries render, not {len(entries)}"
        found = opened.evaluate(
            harness.EMPTY_REGION_JS, [_scoped('[data-testid="tab-panel-retirement"]'), MIN_W, MIN_H]
        )
        assert not _offenders(found), "empty regions in the Retirement tab:\n" + "\n".join(
            _offenders(found)
        )


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
