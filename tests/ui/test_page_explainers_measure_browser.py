"""The measurement scripts of the page-explainers harness find what they are written to find.

Browser-only (marker ``browser``; run with
``python -m pytest -m browser -n 0 tests/ui/test_page_explainers_measure_browser.py``).
Each case is a small page built here with a known layout and no network. A measurement that
cannot report a fault proves nothing, so every script is shown both reporting a defect and
reporting none: ``EMPTY_REGION_JS`` (the plan's AC-6 search), ``FIGURE_SPAN_JS`` and
``HEIGHT_FACTS_JS`` (AC-7).
"""

from __future__ import annotations

import pytest

from tests.app.conftest import _reset_scheduler_run_state  # noqa: F401  (autouse, health state)
from tests.ui import _page_explainers as harness
from tests.ui._shell_support import (  # noqa: F401  (fixtures: overrides, empty directory, browser)
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    firefox,
)

pytestmark = pytest.mark.browser

MIN_W, MIN_H = 240, 80
PAGE = """<!doctype html><html><head><style>
 body {{ margin: 0; font: 14px/20px sans-serif; width: 1000px; }}
 .hp-panel {{ box-sizing: border-box; width: 600px; height: 240px; padding: 20px;
              border: 4px solid #888; margin: 10px; }}
 .bar {{ background: #369; }}
 .row {{ display: flex; }}
 {css}
</style></head><body>{body}</body></html>"""


def _measure(firefox, body: str, css: str = "", *, expression: str, arg):  # noqa: F811
    context = firefox.new_context(viewport={"width": 1000, "height": 700})
    try:
        page = context.new_page()
        page.set_content(PAGE.format(body=body, css=css))
        return page.evaluate(expression, arg)
    finally:
        context.close()


def _empty(firefox, body: str, css: str = "") -> list[dict]:  # noqa: F811
    return _measure(
        firefox, body, css, expression=harness.EMPTY_REGION_JS, arg=[".hp-panel", MIN_W, MIN_H]
    )


def test_a_panel_with_one_short_line_holds_an_empty_rectangle_the_text_box_does_not_hide(
    firefox,  # noqa: F811
):
    """The line's element box spans the panel; only its glyph boxes count as content."""
    found = _empty(firefox, '<div class="hp-panel"><p>hi</p></div>')
    assert len(found) == 1
    empty = found[0]["largest_empty"]
    assert empty is not None
    assert empty["w"] >= MIN_W and empty["h"] >= MIN_H


def test_a_panel_filled_edge_to_edge_holds_no_empty_rectangle(firefox):  # noqa: F811
    body = '<div class="hp-panel"><div class="bar" style="width:100%;height:100%"></div></div>'
    assert _empty(firefox, body)[0]["largest_empty"] is None


def test_a_filled_bar_over_only_the_top_leaves_the_rest_reported(firefox):  # noqa: F811
    body = '<div class="hp-panel"><div class="bar" style="width:100%;height:40px"></div></div>'
    empty = _empty(firefox, body)[0]["largest_empty"]
    assert empty is not None and empty["h"] >= MIN_H
    assert empty["y"] > 40, "the rectangle must lie below the bar"


def test_a_control_counts_as_content(firefox):  # noqa: F811
    body = (
        '<div class="hp-panel"><button style="width:100%;height:100%;box-sizing:border-box">'
        "go</button></div>"
    )
    assert _empty(firefox, body)[0]["largest_empty"] is None


def test_borders_and_rules_are_not_content(firefox):  # noqa: F811
    body = '<div class="hp-panel"></div>'
    found = _empty(firefox, body)[0]
    empty = found["largest_empty"]
    assert empty is not None
    assert empty["w"] == pytest.approx(found["content"]["w"])
    assert empty["h"] == pytest.approx(found["content"]["h"])
    assert found["content"]["w"] == pytest.approx(600 - 2 * 20 - 2 * 4)


def test_the_search_is_exact_on_the_width_threshold(firefox):  # noqa: F811
    """A bar leaving exactly 239 px beside it is not an offence; leaving 241 is."""
    inner = 600 - 2 * 20 - 2 * 4
    for gap, offends in ((239, False), (241, True)):
        body = (
            f'<div class="hp-panel"><div class="bar" '
            f'style="width:{inner - gap}px;height:100%"></div></div>'
        )
        empty = _empty(firefox, body)[0]["largest_empty"]
        assert (empty is not None) is offends, f"gap {gap}: {empty}"


def test_a_panel_that_is_not_displayed_is_not_measured(firefox):  # noqa: F811
    body = '<div class="hp-panel" style="display:none"></div><div class="hp-panel"></div>'
    assert len(_empty(firefox, body)) == 1


def test_a_row_of_figures_spread_across_its_container_measures_near_one(firefox):  # noqa: F811
    body = (
        '<div style="width:600px"><div class="hp-figs row" style="justify-content:space-between">'
        "<span>aaa</span><span>bbb</span><span>ccc</span></div></div>"
    )
    rows = _measure(firefox, body, expression=harness.FIGURE_SPAN_JS, arg=".hp-figs")
    assert len(rows) == 1 and rows[0]["children"] == 3
    assert rows[0]["ratio"] >= 0.9


def test_a_row_of_figures_clustered_at_the_left_measures_low(firefox):  # noqa: F811
    body = (
        '<div style="width:600px"><div class="hp-figs row">'
        "<span>aaa</span><span>bbb</span><span>ccc</span></div></div>"
    )
    rows = _measure(firefox, body, expression=harness.FIGURE_SPAN_JS, arg=".hp-figs")
    assert rows[0]["ratio"] < 0.5


def test_height_facts_sum_the_explainers_and_the_term_summaries_and_the_relabelled(
    firefox,  # noqa: F811
):
    body = (
        '<p data-explainer="A" style="height:30px;margin:0">one</p>'
        '<p data-explainer="B" style="height:50px;margin:0">two</p>'
        '<details data-terms="A"><summary style="height:24px;display:block">terms</summary></details>'
        '<div class="label" style="height:18px">x</div><div class="label" style="height:22px">y</div>'
    )
    facts = _measure(
        firefox, body, expression=harness.HEIGHT_FACTS_JS, arg={"/": [".label", ".absent"]}
    )
    assert facts["explainer_sum"] == pytest.approx(80)
    assert facts["terms_summary_sum"] == pytest.approx(24)
    assert facts["relabelled"]["/"][0] == [18, 22]
    assert facts["relabelled"]["/"][1] == []
    assert facts["document_height"] >= 80 + 24 + 40
    assert facts["scroll_width"] <= facts["viewport_width"]
