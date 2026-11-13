"""The dashboard, its detail panel and the settings modal line up, in a real browser.

Browser-only (marker ``browser``; run with
``python -m pytest -m browser -n 0 tests/ui/test_dashboard_alignment_browser.py``). Firefox headless
through Playwright; the app in this process on the per-test database, seeded through the real
writers from the captured droplet records (``tests/ui/_page_explainers.py``), every request to
anything but the server refused. What is measured, with the scripts and registry of
``tests/ui/_alignment.py``:

* the four comparison rows share one column template: the label, the Bot, the Held and the alpha
  of every row each start (or, for a figure, end) at one offset from the row's left edge, with no
  gap wider than 48 px between a column's widest text and the next column's nearest text;
* every symphony card places the name block, the three ledger columns and their figures, the ring,
  the sparkline and the settings button at one offset from its own left edge, before and after the
  page has polled;
* Tracked, Armed and Triggered sit on one line with equal gaps of at most 48 px and no label or
  value broken across lines, before and after a poll and a window pick and back (the dollar summary
  is measured by ``test_dashboard_dollar_summary_browser``);
* a sleeve card's name line sits within 48 px, and the rule rows of a sleeve whose rule names
  differ in length start the name, the mode and the actions at one offset;
* the detail panel's Risk math rows, event rows and Symphony variables rows, and the settings
  modal's parameter rows and locked-variable entries, each sit at one offset on every row.

The measures are shown to report a fault and to report none by ``test_alignment_measure_browser``.
The stacked forms below 1001 px are held by ``test_page_explainers_unchanged_browser``.

Provenance: the events of the detail panel are written through ``database.log_symphony_event``, the
real writer, into a temporary log file; their messages follow the engine's own forms (a name with
"ARMED (trail)", a reason with a colon) at three different lengths, so a text column has something
to be misaligned by.
"""

from __future__ import annotations

import pytest

import database
from tests.app.conftest import _reset_scheduler_run_state  # noqa: F401  (autouse, health state)
from tests.ui import _alignment as al
from tests.ui._shell_support import (  # noqa: F401  (fixtures: overrides, empty directory, browser)
    WIDE,
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    advisor_server,
    firefox,
    open_page,
    open_probe,
)
from tests.ui.test_page_explainers_dashboard_browser import (  # noqa: F401  (fixture and helpers)
    POLL_MS,
    _Dashboard,
    seeded,
)

pytestmark = pytest.mark.browser

WIDE_WIDTHS = tuple(w for w in al.WIDTHS if w >= 1440)
PANEL_SETTLE_MS = 700
RULE_NAMES = (
    "Short rule",
    "A rule whose name is a good deal longer than the one before it",
    "Mid length rule",
)
EVENT_MESSAGES = (
    ("armed", "Alpha Tail ARMED (trail)"),
    ("trigger", "STOP_LOSS: Alpha Tail trailing stop hit at -2.4 percent below its peak"),
    ("info", "Alpha Tail checked"),
    ("lock", "BREAKEVEN: Alpha Tail stop raised to breakeven"),
)


def _clean(found: dict, where: str) -> None:
    assert found["found"] > 0, f"{where}: the measure found nothing to measure ({found['name']})"
    assert not found["violations"], f"{where}: " + "; ".join(found["violations"])


def _offsets(page, name: str, where: str, *, at_least: int = 2) -> None:
    entry = al.TABLE_GROUPS[name]
    found = al.measure(page, al.FIELD_OFFSETS_JS, al.spec_of(entry, name=name))
    assert found["found"] >= at_least, (
        f"{where}: {found['found']} rows of {name}, premise {at_least}"
    )
    _clean(found, where)


def _gaps(page, name: str, where: str) -> None:
    entry = al.TABLE_GROUPS[name]
    found = al.measure(page, al.COLUMN_GAPS_JS, al.spec_of(entry, name=name))
    assert found["found"] > 0, f"{where}: {name} measured nothing"
    assert not found["violations"], f"{where}: " + "; ".join(found["violations"])


def _line(page, name: str, where: str) -> None:
    entry = al.FIGURE_LINES[name]
    found = al.measure(
        page, al.LINE_GAPS_JS, al.spec_of(entry, name=name, no_wrap=True, items=entry["items"])
    )
    _clean(found, where)
    assert all(line["items"] >= 2 for line in found["lines"]), f"{where}: a line has one item"


def _seed_events() -> None:
    state = database.load_state()
    for sid, entry in state.items():
        if isinstance(entry, dict) and "name" in entry:
            for kind, message in EVENT_MESSAGES:
                database.log_symphony_event(sid, message, kind)


@pytest.fixture
def with_events(tmp_path, monkeypatch, seeded):  # noqa: F811
    monkeypatch.setattr(database, "SYMPHONY_LOGS_FILE", str(tmp_path / "symphony_logs.json"))
    seeded("dashboard_full")
    _seed_events()


def _seed_sleeve_with_three_rules() -> None:
    sleeve_id = database.create_sleeve("Rule alignment sleeve", 1000.0)
    for name in RULE_NAMES:
        database.create_sleeve_rule(sleeve_id, name)


def _open_detail(probe) -> None:
    probe.page.locator('[data-testid="sym-card"] .card-name').first.click()
    probe.page.wait_for_selector("#detail-panel.open")
    probe.page.wait_for_selector("#events-timeline .event-row")
    probe.page.wait_for_selector("#vars-section .var-row .var-val")
    probe.page.wait_for_timeout(PANEL_SETTLE_MS)


def _open_modal(probe) -> None:
    probe.page.locator('[data-testid="sym-settings-btn"]').first.click()
    probe.page.wait_for_selector("#sym-settings-modal .ssm-param")
    probe.page.wait_for_timeout(PANEL_SETTLE_MS)


# ---------------------------------------------------------------------------
# The comparison rows
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", al.WIDTHS)
def test_the_comparison_rows_share_one_column_template_with_no_wide_gap(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,  # noqa: F811
    width,
):
    seeded("dashboard_full")
    with _Dashboard(firefox, advisor_server, width, al.VIEWPORT_HEIGHT) as probe:
        _offsets(probe.page, "comparison_rows", f"comparison rows at {width}", at_least=3)
        _gaps(probe.page, "comparison_rows", f"comparison rows at {width}")


def test_the_comparison_rows_still_share_their_columns_after_three_polls(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,  # noqa: F811
):
    seeded("dashboard_full")
    with _Dashboard(firefox, advisor_server, WIDE[0], al.VIEWPORT_HEIGHT) as probe:
        for refresh in range(3):
            probe.page.clock.run_for(POLL_MS)
            probe.page.wait_for_timeout(500)
            where = f"comparison rows after poll {refresh + 1}"
            _offsets(probe.page, "comparison_rows", where, at_least=3)
            _gaps(probe.page, "comparison_rows", where)


# ---------------------------------------------------------------------------
# The symphony cards
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", al.WIDTHS)
def test_every_card_places_each_field_at_one_offset_from_its_own_left_edge(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,  # noqa: F811
    width,
):
    seeded("dashboard_full")
    with _Dashboard(firefox, advisor_server, width, al.VIEWPORT_HEIGHT) as probe:
        _offsets(probe.page, "symphony_cards", f"symphony cards at {width}", at_least=5)


def test_the_cards_the_poll_rewrites_keep_each_field_at_one_offset(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,  # noqa: F811
):
    seeded("dashboard_full")
    with _Dashboard(firefox, advisor_server, WIDE[0], al.VIEWPORT_HEIGHT) as probe:
        for refresh in range(3):
            probe.page.clock.run_for(POLL_MS)
            probe.page.wait_for_timeout(500)
            _offsets(
                probe.page, "symphony_cards", f"symphony cards after poll {refresh + 1}", at_least=5
            )


# ---------------------------------------------------------------------------
# Tracked, Armed, Triggered and the four dollar figures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", WIDE_WIDTHS)
def test_tracked_armed_and_triggered_sit_with_equal_gaps_of_at_most_48_px(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,  # noqa: F811
    width,
):
    seeded("dashboard_full")
    with _Dashboard(firefox, advisor_server, width, al.VIEWPORT_HEIGHT) as probe:
        _line(probe.page, "tracked_armed_triggered", f"mini stats at {width}")


def test_the_mini_stats_and_comparison_rows_keep_their_layout_after_a_poll_and_a_pick(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,  # noqa: F811
):
    seeded("dashboard_full")
    with _Dashboard(firefox, advisor_server, WIDE[0], al.VIEWPORT_HEIGHT) as probe:
        probe.page.clock.run_for(POLL_MS)
        probe.page.wait_for_timeout(800)
        steps = [("after a poll", None), ("on 1y", "1y"), ("back on 30d", "30d")]
        for label, token in steps:
            if token:
                probe.page.locator(f'[data-testid="window-{token}"]').click()
                probe.page.wait_for_timeout(1500)
            _line(probe.page, "tracked_armed_triggered", f"mini stats {label}")
            _offsets(probe.page, "comparison_rows", f"comparison rows {label}", at_least=3)


@pytest.mark.parametrize("width", al.WIDTHS)
def test_the_hero_top_row_puts_no_text_at_its_far_end(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,  # noqa: F811
    width,
):
    """The headline block and the window selector: the selector is a control and may end the row; the
    text of the headline block starts at the row's left edge or within 24 px of what precedes it."""
    seeded("dashboard_full")
    with _Dashboard(firefox, advisor_server, width, al.VIEWPORT_HEIGHT) as probe:
        entry = al.TOOLBAR_ROWS["hero_top"]
        found = al.measure(
            probe.page,
            al.ROW_START_JS,
            {"name": "hero_top", "row": entry["row"], "max_gap": al.MAX_NON_CONTROL_GAP_PX},
        )
        _clean(found, f"hero top at {width}")


# ---------------------------------------------------------------------------
# Sleeves
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", WIDE_WIDTHS)
def test_a_sleeve_cards_name_line_items_sit_within_48_px_of_each_other(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,  # noqa: F811
    width,
):
    seeded("dashboard_sleeves")
    with _Dashboard(firefox, advisor_server, width, al.VIEWPORT_HEIGHT) as probe:
        entry = al.FIGURE_LINES["sleeve_card_line"]
        found = al.measure(
            probe.page, al.LINE_GAPS_JS, al.spec_of(entry, name="sleeve_card_line", no_wrap=True)
        )
        _clean(found, f"sleeve card line at {width}")


@pytest.mark.parametrize("width", al.WIDTHS)
def test_rule_rows_of_different_name_lengths_start_each_field_at_one_offset(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,  # noqa: F811
    width,
):
    seeded("dashboard_sleeves")
    _seed_sleeve_with_three_rules()
    with _Dashboard(firefox, advisor_server, width, al.VIEWPORT_HEIGHT) as probe:
        _offsets(probe.page, "sleeve_rule_rows", f"sleeve rule rows at {width}", at_least=4)


# ---------------------------------------------------------------------------
# The detail panel
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["risk_math_rows", "event_rows", "symphony_variable_rows"])
@pytest.mark.parametrize("width", [1001, 1440, 1920])
def test_the_detail_panel_rows_sit_at_one_offset_on_every_row(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    with_events,  # noqa: F811
    width,
    name,
):
    with _Dashboard(firefox, advisor_server, width, al.VIEWPORT_HEIGHT) as probe:
        _open_detail(probe)
        _offsets(probe.page, name, f"{name} at {width}", at_least=3)


def test_the_detail_panel_rows_keep_their_offsets_after_a_poll(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    with_events,  # noqa: F811
):
    with _Dashboard(firefox, advisor_server, WIDE[0], al.VIEWPORT_HEIGHT) as probe:
        _open_detail(probe)
        probe.page.clock.run_for(POLL_MS)
        probe.page.wait_for_timeout(800)
        for name in ("risk_math_rows", "event_rows", "symphony_variable_rows"):
            _offsets(probe.page, name, f"{name} after a poll", at_least=3)


# ---------------------------------------------------------------------------
# The settings modal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["modal_parameter_rows", "modal_locked_variable_entries"])
@pytest.mark.parametrize("width", [1001, 1440, 1920])
def test_the_modal_rows_sit_at_one_offset_on_every_row(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,  # noqa: F811
    width,
    name,
):
    seeded("dashboard_full")
    with _Dashboard(firefox, advisor_server, width, al.VIEWPORT_HEIGHT) as probe:
        _open_modal(probe)
        _offsets(probe.page, name, f"{name} at {width}", at_least=3)


def test_the_modal_opened_at_phone_width_does_not_scroll_the_page_sideways(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,  # noqa: F811
):
    seeded("dashboard_full")
    with _Dashboard(firefox, advisor_server, 390, al.NARROW_VIEWPORT_HEIGHT[390]) as probe:
        _open_modal(probe)
        scroll, viewport = probe.page.evaluate(
            "() => [document.documentElement.scrollWidth, innerWidth]"
        )
    assert scroll == viewport, (
        f"the page scrolls sideways with the modal open: {scroll} in {viewport}"
    )
