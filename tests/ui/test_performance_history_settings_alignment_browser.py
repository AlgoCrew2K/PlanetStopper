"""Performance, History and Settings line up, in a real browser.

Browser-only (marker ``browser``; run with
``python -m pytest -m browser -n 0 tests/ui/test_performance_history_settings_alignment_browser.py``).
Firefox headless through Playwright; the app in this process on the per-test database, seeded through
the real writers from the captured records (the shadow rows and exits of ``tests/ui/_performance_page``,
the captured post-mortem days of ``tests/ui/_shell_support.seed_history_exits``), the clock fixed as in
the sibling modules. Measured with the scripts and registry of ``tests/ui/_alignment.py``:

* Performance's risk metrics are ONE table: one header row of four cells above ten body rows that
  neither overlap nor sit side by side, no header cell repeated, every column's text sharing its
  edge (right edge for the three figure columns, header included);
* the stop-justification preconditions and the exit turnover tables meet the column rule, with no
  gap wider than 48 px between a column's widest text and the next column's nearest text, and the
  turnover counts and drag end at their column's right edge in every width band (the reference
  right-aligned them below 1240 px only);
* History's by-reason and exits tables meet the column rule with Detail and every figure column
  right-aligned, header included, and the exits' columns sit within 48 px of each other;
* no data stamp or caption sits beside another element at more than 24 px, or at a row's far end:
  the Performance and History toolbar rows and the Settings actions row, at every width;
* the headline strip and the History hero figures sit with equal gaps of at most 48 px and no
  label or value broken across lines at 1440 px and wider;
* Settings' Algorithm parameter cards and a symphony's override rows each place their fields at
  one offset from the card's or row's left edge.

Provenance of the preconditions body: the real ``/api/guard-alpha-preconditions`` answer over the
captured shadow rows with each symphony's shadow verdict changed to ``NOT_MET`` (named field edit;
the captured history is below the 40-observation floor, so the real answer renders no row).
"""

from __future__ import annotations

import pytest

from tests.app.conftest import _reset_scheduler_run_state  # noqa: F401  (autouse, health state)
from tests.ui import _alignment as al
from tests.ui._shell_support import (  # noqa: F401  (fixtures: overrides, empty directory, browser)
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    advisor_server,
    firefox,
    open_probe,
)
from tests.ui.test_page_explainers_performance_history_browser import (  # noqa: F401
    _open,
    _open_exit_day,
    _usable_preconditions_body,
    seeded,
)
from tests.ui.test_page_explainers_settings_browser import (  # noqa: F401  (fixture and helpers)
    go_to,
    settings_open,
    world,
)

pytestmark = pytest.mark.browser

WIDE_WIDTHS = tuple(w for w in al.WIDTHS if w >= 1440)
VIEW = al.VIEWPORT_HEIGHT


def _clean(found: dict, where: str) -> None:
    assert found["found"] > 0, f"{where}: the measure found nothing to measure ({found['name']})"
    assert not found["violations"], f"{where}: " + "; ".join(found["violations"])


def _table(page, name: str, where: str, *, rows: int | None = None, gaps: bool = False) -> None:
    entry = al.TABLE_GROUPS[name]
    edges = al.measure(page, al.COLUMN_EDGES_JS, al.spec_of(entry, name=name))
    _clean(edges, f"{where} column edges")
    if rows is not None:
        got = [i["body_rows"] for i in edges["instances"]]
        assert got == [rows], f"{where}: {name} has {got} body rows, premise {rows}"
    if gaps:
        found = al.measure(page, al.COLUMN_GAPS_JS, al.spec_of(entry, name=name))
        _clean(found, f"{where} column gaps")


def _toolbar(page, name: str, where: str) -> None:
    entry = al.TOOLBAR_ROWS[name]
    found = al.measure(
        page,
        al.ROW_START_JS,
        {"name": name, "row": entry["row"], "max_gap": al.MAX_NON_CONTROL_GAP_PX},
    )
    _clean(found, where)


def _line(page, name: str, where: str) -> None:
    entry = al.FIGURE_LINES[name]
    found = al.measure(
        page, al.LINE_GAPS_JS, al.spec_of(entry, name=name, no_wrap=True, items=entry["items"])
    )
    _clean(found, where)
    assert all(line["items"] >= 2 for line in found["lines"]), f"{where}: a line has one item"


def _performance(probe) -> None:
    _open(probe, "/performance", preconditions=(200, _usable_preconditions_body()))


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", al.WIDTHS)
def test_the_risk_metrics_are_one_table_with_one_header_row_and_aligned_columns(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,  # noqa: F811
    width,
):
    with open_probe(firefox, advisor_server, width=width, height=VIEW) as probe:
        _performance(probe)
        _table(probe.page, "risk_metrics", f"risk metrics at {width}", rows=10)
        headers = probe.page.evaluate(
            "() => [...document.querySelectorAll('table:has(#metrics-tbody) thead th')]"
            ".map((th) => th.textContent.replace(/\\s+/g, ' ').trim())"
        )
    assert headers == ["Metric", "If held", "Bot (shadow)", "Delta (DD and vol in %)"], (
        f"the header row holds {headers}: no header cell repeats"
    )


@pytest.mark.parametrize("width", al.WIDTHS)
def test_the_preconditions_table_meets_the_column_rule_with_no_wide_gap(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,  # noqa: F811
    width,
):
    with open_probe(firefox, advisor_server, width=width, height=VIEW) as probe:
        _performance(probe)
        _table(probe.page, "preconditions", f"preconditions at {width}", rows=11, gaps=True)


@pytest.mark.parametrize("width", al.WIDTHS)
def test_the_turnover_counts_and_drag_end_at_their_columns_right_edge_in_every_band(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,  # noqa: F811
    width,
):
    with open_probe(firefox, advisor_server, width=width, height=VIEW) as probe:
        _performance(probe)
        _table(probe.page, "exit_turnover", f"exit turnover at {width}", gaps=True)


@pytest.mark.parametrize("width", al.WIDTHS)
def test_the_performance_toolbar_rows_put_no_text_beside_another_element_at_the_far_end(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,  # noqa: F811
    width,
):
    with open_probe(firefox, advisor_server, width=width, height=VIEW) as probe:
        _performance(probe)
        _toolbar(probe.page, "performance_toolbars", f"performance toolbars at {width}")


@pytest.mark.parametrize("width", WIDE_WIDTHS)
def test_the_headline_strip_sits_with_equal_gaps_and_no_label_wraps(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,  # noqa: F811
    width,
):
    with open_probe(firefox, advisor_server, width=width, height=VIEW) as probe:
        _performance(probe)
        _line(probe.page, "performance_headline_strip", f"headline strip at {width}")


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", al.WIDTHS)
def test_the_by_reason_table_right_aligns_every_figure_column_header_included(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,  # noqa: F811
    width,
):
    with open_probe(firefox, advisor_server, width=width, height=VIEW) as probe:
        _open(probe, "/history")
        _table(probe.page, "exit_reasons", f"by exit reason at {width}", rows=4)


@pytest.mark.parametrize("width", al.WIDTHS)
def test_the_exits_table_right_aligns_detail_and_its_columns_sit_within_48_px(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,  # noqa: F811
    width,
):
    with open_probe(firefox, advisor_server, width=width, height=VIEW) as probe:
        _open(probe, "/history")
        _open_exit_day(probe)
        _table(probe.page, "exits", f"exits at {width}", rows=8, gaps=True)


@pytest.mark.parametrize("width", al.WIDTHS)
def test_the_history_toolbar_row_puts_no_text_beside_another_element_at_the_far_end(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,  # noqa: F811
    width,
):
    with open_probe(firefox, advisor_server, width=width, height=VIEW) as probe:
        _open(probe, "/history")
        _toolbar(probe.page, "history_toolbar", f"history toolbar at {width}")


@pytest.mark.parametrize("width", WIDE_WIDTHS)
def test_the_history_hero_figures_sit_with_equal_gaps_and_no_label_wraps(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,  # noqa: F811
    width,
):
    with open_probe(firefox, advisor_server, width=width, height=VIEW) as probe:
        _open(probe, "/history")
        _line(probe.page, "history_hero_figures", f"history hero figures at {width}")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", al.WIDTHS)
def test_the_settings_actions_row_puts_no_text_beside_another_element_at_the_far_end(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    world,  # noqa: F811
    width,
):
    with settings_open(firefox, advisor_server, size=(width, VIEW)) as probe:
        _toolbar(probe.page, "settings_actions", f"settings actions at {width}")


@pytest.mark.parametrize("width", al.WIDTHS)
def test_each_algorithm_parameter_card_places_its_fields_at_one_offset(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    world,  # noqa: F811
    width,
):
    with settings_open(firefox, advisor_server, size=(width, VIEW)) as probe:
        go_to(probe, "algorithm")
        entry = al.TABLE_GROUPS["algorithm_parameter_cards"]
        found = al.measure(
            probe.page, al.FIELD_OFFSETS_JS, al.spec_of(entry, name="algorithm_parameter_cards")
        )
        assert found["found"] >= 6, f"{found['found']} parameter cards, premise six or more"
        _clean(found, f"algorithm parameter cards at {width}")


@pytest.mark.parametrize("width", [1001, 1440, 1920])
def test_a_symphonys_override_rows_place_their_fields_at_one_offset(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    world,  # noqa: F811
    width,
):
    with settings_open(firefox, advisor_server, size=(width, VIEW)) as probe:
        go_to(probe, "symphonies")
        probe.page.locator("#sym-list .sym-list-btn").first.click()
        probe.page.wait_for_selector(".override-grid .override-row")
        entry = al.TABLE_GROUPS["symphony_override_rows"]
        found = al.measure(
            probe.page, al.FIELD_OFFSETS_JS, al.spec_of(entry, name="symphony_override_rows")
        )
        assert found["found"] >= 6, f"{found['found']} override rows, premise six or more"
        _clean(found, f"override rows at {width}")
