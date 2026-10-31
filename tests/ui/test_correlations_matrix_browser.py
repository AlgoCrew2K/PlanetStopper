"""The Correlations matrix in a real browser: labels and cells reachable by hover and keyboard.

Browser-only (marker ``browser``, deselected by default; run with
``python -m pytest -m browser -n 0 tests/ui/test_correlations_matrix_browser.py``). Firefox
headless through Playwright, imported inside a fixture; a missing Playwright or Firefox fails with a
message naming it, never a skip.

The server is the app in this process on the per-test database. The database is seeded by
``tests.advisors._series_captures.seed_run_from_captures``: the real recommender over the PM's real
Composer captures, its summary persisted through the production writer. Every request to anything
but that server is aborted.

What a browser adds over the Flask-client render tests: a label or cell is reachable with the
keyboard (``Tab`` lands on it), its accessible name (computed by the browser, not read from an
attribute) equals the hover text, and the page raises no script error. A native tooltip is not part
of the page's DOM, so hover is checked through the ``title`` the browser shows.
"""

from __future__ import annotations

import os
import re

import pytest

from tests.advisors import _series_captures as captures
from tests.ui._advisor_browser_support import (  # noqa: F401
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    advisor_probe,
    advisor_server,
    fail,
    firefox,
    open_advisor,
    wait_until,
)

pytestmark = pytest.mark.browser

_WHOLE_SERIES = "q7WQHwLzt49cMgFBzHsX"  # 29 returns: the matrix window is its whole series
_LONG_SERIES = "n2ooAZTvBRN6ZzpMmWmU"  # 571 returns: the matrix window is shorter than the series
_MID_SERIES = "qF5ZU7ALjrlhxrGEwsyJ"
_DEEP_SERIES = "Gpaw3IhZghQPRE6AdEKx"
_MAX_TABS = 600

_ROW_LABEL = '[data-testid="corr-row-label"][data-sym="{sym}"]'
_PAIR_CELL = '[data-testid="corr-cell"][data-sym-a="{a}"][data-sym-b="{b}"]'
_IS_ACTIVE = "(el) => el === document.activeElement"


def _seed_and_open(probe) -> dict:
    summary = captures.seed_run_from_captures(os.environ["DB_PATH"])
    open_advisor(probe, "#correlations")
    probe.page.locator('[data-testid="tab-correlations"]').click()
    wait_until(
        probe,
        "() => { const t = document.querySelector('[data-testid=\"correlation-matrix-table\"]');"
        " return !!t && t.getBoundingClientRect().height > 0; }",
        "the Correlations matrix never became visible",
    )
    return summary


def _tab_until_active(probe, target, start) -> int:
    """Focus ``start`` and press Tab until ``target`` is the active element; the presses used."""
    start.focus()
    for presses in range(1, _MAX_TABS + 1):
        probe.page.keyboard.press("Tab")
        if target.evaluate(_IS_ACTIVE):
            return presses
    fail(probe, f"Tab never reached the element within {_MAX_TABS} presses")
    return -1


def _accessible_name_count(probe, role: str, name: str) -> int:
    return probe.page.get_by_role(role, name=name, exact=True).count()


@pytest.mark.parametrize("symphony_id", [_LONG_SERIES, _WHOLE_SERIES])
def test_a_row_label_shows_its_matrix_window_and_full_series_on_hover_and_to_the_keyboard(
    advisor_probe, symphony_id
):
    summary = _seed_and_open(advisor_probe)
    windows = summary["series_windows"][symphony_id]
    label = advisor_probe.page.locator(_ROW_LABEL.format(sym=symphony_id))
    assert label.count() == 1, "the symphony has no row label"

    label.hover()
    title = label.get_attribute("title")
    assert f"{windows['window_first']} to {windows['window_last']}" in title
    assert f"{windows['window_days']} returns" in title
    assert f"{windows['series_first']} to {windows['series_last']}" in title
    assert f"{windows['series_points']} points" in title

    first_col = advisor_probe.page.locator('[data-testid="corr-col-label"]').first
    _tab_until_active(advisor_probe, label, first_col)
    aria = label.get_attribute("aria-label")
    assert f"{windows['window_first']} to {windows['window_last']}" in aria
    assert _accessible_name_count(advisor_probe, "rowheader", aria) == 1, (
        "the browser's accessible name for the row label is not its aria-label"
    )
    assert advisor_probe.page_errors == [], advisor_probe.page_errors


def test_the_two_labels_show_different_windows_where_the_series_differ(advisor_probe):
    summary = _seed_and_open(advisor_probe)
    long_windows = summary["series_windows"][_LONG_SERIES]
    short_windows = summary["series_windows"][_WHOLE_SERIES]
    assert long_windows["window_days"] < long_windows["series_points"], "premise"
    assert short_windows["window_days"] == short_windows["series_points"], "premise"
    long_title = advisor_probe.page.locator(_ROW_LABEL.format(sym=_LONG_SERIES)).get_attribute(
        "title"
    )
    short_title = advisor_probe.page.locator(_ROW_LABEL.format(sym=_WHOLE_SERIES)).get_attribute(
        "title"
    )
    assert long_title != short_title
    assert str(long_windows["series_points"]) in long_title
    assert str(short_windows["window_days"]) in short_title


def test_hovering_a_cell_offers_both_names_the_correlation_the_depth_and_the_window(advisor_probe):
    summary = _seed_and_open(advisor_probe)
    a, b = sorted((_MID_SERIES, _DEEP_SERIES))
    stored = next(p for p in summary["pairs"] if (p["sym_a"], p["sym_b"]) == (a, b))
    cell = advisor_probe.page.locator(_PAIR_CELL.format(a=a, b=b)).first
    cell.scroll_into_view_if_needed()
    cell.hover()

    title = cell.get_attribute("title")
    for sym in (a, b):
        assert " ".join(captures.capture_name(sym).split()) in " ".join(title.split())
    assert f"{stored['correlation']:+.2f}" in title
    assert f"{stored['n_obs']} aligned days" in title
    assert f"{stored['window_first']} to {stored['window_last']}" in title
    # The page script prints the aligned days under the number, so the depth is read without a hover.
    assert (cell.inner_text() or "").strip() == f"{stored['correlation']:+.2f}\nn={stored['n_obs']}"
    assert advisor_probe.page_errors == [], advisor_probe.page_errors


def test_a_cell_is_reached_with_the_tab_key_and_its_accessible_name_is_the_hover_text(
    advisor_probe,
):
    summary = _seed_and_open(advisor_probe)
    a, b = sorted((_MID_SERIES, _DEEP_SERIES))
    assert any((p["sym_a"], p["sym_b"]) == (a, b) for p in summary["pairs"])
    cell = advisor_probe.page.locator(_PAIR_CELL.format(a=a, b=b)).first
    row_label = cell.locator("xpath=ancestor::tr[1]").locator('[data-testid="corr-row-label"]')

    presses = _tab_until_active(advisor_probe, cell, row_label)
    assert presses <= _MAX_TABS
    title = cell.get_attribute("title")
    assert cell.get_attribute("aria-label") == title
    assert _accessible_name_count(advisor_probe, "cell", title) >= 1, (
        "the browser's accessible name for the cell is not its aria-label"
    )
    assert re.search(r"\d+ aligned days", title)
    assert advisor_probe.page_errors == [], advisor_probe.page_errors
