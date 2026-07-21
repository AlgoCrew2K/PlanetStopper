"""
RED test -- F-025: hero chart needs an explicit metric label distinguishing its
day-by-day cumulative (shadow-history) basis from the adjacent VW-lifetime
scalar headline.

The Y-axis-visibility half of F-025 is covered in
tests/dashboard/test_display_truth_cluster_js.py (TestF025HeroChartYAxisVisible)
-- this file covers the LABEL half: templates/index.html's chart-legend area
(:836-849, sibling of the existing `hero-data-as-of` span) has no text at all
distinguishing the chart's metric from the "Cumulative · lifetime" comparison
row a few dozen lines below it. Without a distinguishing label, an operator
reading the hero chart's ~-3% line beside the ~+34% lifetime scalar headline
sees two contradictory numbers with no way to self-diagnose that they are
DIFFERENT metrics on DIFFERENT bases (audit: "reads as if the tool
contradicts itself").

Scoped STRICTLY to the chart-legend/cum-chart-wrap area -- NOT the whole page
-- because the page separately has a "Cumulative · lifetime" comparison-row
label (templates/index.html:920) which legitimately contains "Cumulative" for
an unrelated reason; a page-wide search would false-pass against that
pre-existing text without the chart itself ever gaining a label.
"""

from __future__ import annotations

import pathlib
import re

_INDEX_HTML = pathlib.Path(__file__).parent.parent.parent / "templates" / "index.html"


def _chart_legend_area() -> str:
    src = _INDEX_HTML.read_text(encoding="utf-8")
    start = src.find("<!-- Cumulative Bot-vs-Held chart -->")
    assert start != -1, (
        "the hero chart section anchor comment was not found -- this test's "
        "location assumptions may be stale; update the markers."
    )
    # The hero chart block ends before the "Right column" comment that opens
    # the VsRows/mini-stats section.
    end = src.find("<!-- Right column:", start)
    assert end != -1, "the hero chart section's closing anchor was not found."
    return src[start:end]


def test_hero_chart_area_has_a_day_by_day_cumulative_basis_label():
    block = _chart_legend_area()
    # Strip HTML comments first (e.g. the pre-existing "<!-- Cumulative
    # Bot-vs-Held chart -->" anchor comment) so only text a browser actually
    # RENDERS can satisfy this check -- a comment is invisible to the operator.
    rendered = re.sub(r"<!--.*?-->", "", block, flags=re.DOTALL)
    assert re.search(r"(?i)cumulative", rendered) and re.search(r"(?i)\bday", rendered), (
        "F-025 FAIL: the hero chart's own legend/caption area has no wording "
        "distinguishing its DAY-BY-DAY CUMULATIVE (shadow-history) basis from "
        "the adjacent scalar 'Cumulative · lifetime' headline a few rows below "
        "it -- an operator has no way to tell the chart's ~-3% line and the "
        "~+34% lifetime scalar are different metrics on different bases. "
        f"Scoped block: {block!r}"
    )


def test_hero_data_as_of_span_still_present_regression():
    """Regression pin: the existing `hero-data-as-of` legend span (id, JS-updated
    each poll) must survive the label addition unchanged."""
    block = _chart_legend_area()
    assert 'id="hero-data-as-of"' in block, (
        "regression FAIL: the hero-data-as-of legend span was removed/renamed -- "
        "static/index.js's updateSectionMeta writes to this id every poll."
    )
