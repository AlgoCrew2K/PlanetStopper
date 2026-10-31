"""The Advisor's autotune panel in a real browser, with the chart library's request refused.

Browser-only (marker ``browser``, deselected by default; run with
``python -m pytest -m browser -n 0 tests/ui/test_advisor_autotune_browser.py``). Firefox
headless through Playwright, imported inside a fixture; a missing Playwright or Firefox fails
with a message naming it, never a skip.

The server is the app in this process on the per-test database, seeded through the real
writer ``database.save_autotune_run`` for the normalized display names of
``tests/fixtures/prod_droplet/bot_state_positions.json``. Every request to anything but that
server is aborted, so the chart library never loads: the sparkline canvas must not take
space, the cards must still render, and the older-runs list must be reachable with the
page's real stylesheet.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import database
from tests._ux_truth_support import captured_symphonies
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

_FIRST_WEEK = datetime(2026, 9, 1, 4, 0, 0)
_CARDS_IN_LIST = "#autotune-runs-list [data-testid='autotune-run-row']"
_ROWS_IN_OLDER = "#autotune-older-list [data-testid='autotune-run-row']"
_COUNT_JS = "(sel) => document.querySelectorAll(sel).length"


def _seed(runs_each: int) -> int:
    """``runs_each`` scored runs for each of the 11 captured symphonies; the row count."""
    keys = [database.normalize_name(name) for _, name in captured_symphonies()]
    for j in range(runs_each):
        for i, key in enumerate(keys):
            moment = _FIRST_WEEK + timedelta(weeks=j, minutes=i)
            database.save_autotune_run(
                moment.strftime("%Y-%m-%dT%H:%M:%S"),
                key,
                0.5,
                1.0,
                "Reverted to Fallback",
                0.4,
                0.3,
                selection_tstat=2.0 + i * 0.01,
                naive_sharpe=1.0 + i * 0.01 + j * 0.1,
            )
    return len(keys) * runs_each


def _wait_for_some_cards(probe):
    wait_until(
        probe,
        "(sel) => document.querySelectorAll(sel).length > 0",
        "the autotune list never showed a card",
        arg=_CARDS_IN_LIST,
    )


def _wait_for_cards(probe, count: int):
    wait_until(
        probe,
        "([sel, n]) => document.querySelectorAll(sel).length === n",
        f"the autotune list never showed {count} cards",
        arg=[_CARDS_IN_LIST, count],
    )


def test_a_page_without_the_chart_library_hides_the_canvas_and_still_lists_the_cards(
    advisor_probe,
):
    _seed(2)
    open_advisor(advisor_probe)
    _wait_for_some_cards(advisor_probe)
    state = advisor_probe.page.evaluate(
        """() => {
          const canvas = document.getElementById('autotune-sparkline-canvas');
          const box = canvas.getBoundingClientRect();
          return {
            chartDefined: typeof Chart !== 'undefined',
            display: getComputedStyle(canvas).display,
            height: box.height,
            width: box.width,
          };
        }"""
    )
    assert state["chartDefined"] is False, (
        "premise: the library request was refused, so Chart must be undefined: "
        f"refused={advisor_probe.refused}"
    )
    assert state["display"] == "none", state
    assert state["height"] == 0 and state["width"] == 0, state
    assert advisor_probe.page_errors == [], advisor_probe.page_errors
    _wait_for_cards(advisor_probe, 11)


def test_the_older_runs_are_reachable_in_a_closed_details_when_there_are_any(advisor_probe):
    total = _seed(3)
    open_advisor(advisor_probe)
    _wait_for_cards(advisor_probe, 11)
    details = advisor_probe.page.locator("#autotune-older-runs")
    assert details.count() == 1, "the page has no older-runs details"
    assert details.is_visible(), "older runs exist but their details is not shown"
    assert details.evaluate("(el) => el.open") is False
    assert advisor_probe.page.locator(_ROWS_IN_OLDER).first.is_hidden(), (
        "the rows are visible while the details is closed"
    )
    advisor_probe.page.locator("#autotune-older-summary").click()
    wait_until(
        advisor_probe,
        "(sel) => document.querySelectorAll(sel).length > 0",
        "opening the details showed no older runs",
        arg=_ROWS_IN_OLDER,
    )
    assert advisor_probe.page.evaluate(_COUNT_JS, _ROWS_IN_OLDER) == total - 11
    assert advisor_probe.page.locator(_ROWS_IN_OLDER).first.is_visible()


def test_the_older_runs_details_takes_no_space_when_each_symphony_has_one_run(advisor_probe):
    _seed(1)
    open_advisor(advisor_probe)
    _wait_for_cards(advisor_probe, 11)
    details = advisor_probe.page.locator("#autotune-older-runs")
    assert details.count() == 1, "the page has no older-runs details"
    assert details.is_hidden(), "a details with no rows is shown"
    assert details.evaluate("(el) => el.getBoundingClientRect().height") == 0
    if advisor_probe.page_errors:
        fail(advisor_probe, "the page raised")
