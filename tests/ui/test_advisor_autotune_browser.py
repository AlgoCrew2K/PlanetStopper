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
    # The library is served from static/vendor/, so refusing it is a route here, not the shared
    # outside-host refusal.
    advisor_probe.page.route("**/static/vendor/chart.js*", lambda route: route.abort())
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


_ON_SCREEN_JS = """() => {
  const colour = (value) => {
    const probe = document.createElement('span');
    probe.style.color = value;
    document.body.appendChild(probe);
    const resolved = getComputedStyle(probe).color;
    probe.remove();
    return resolved;
  };
  const box = (el) => {
    const r = el.getBoundingClientRect();
    return { w: Math.round(r.width), h: Math.round(r.height) };
  };
  const row = document.querySelector("#autotune-runs-list [data-testid='autotune-run-row']");
  const head = document.querySelector('#autotune-runs-list .autotune-row--head');
  const frozen = row.querySelector('.autotune-run-frozen');
  const scoreHead = head.querySelector('.autotune-run-score');
  const label = row.querySelector('.autotune-score-label');
  const streakHead = head.querySelector('.autotune-run-streak');
  const figure = row.querySelector('.autotune-streak-figure');
  const tail = row.querySelector('.autotune-streak-tail');
  return {
    inkDim: colour('var(--studio-ink-dim)'),
    frozenText: frozen.innerText,
    frozenBox: box(frozen),
    frozenColour: getComputedStyle(frozen).color,
    headDisplay: getComputedStyle(head).display,
    scoreHeadText: scoreHead.innerText,
    scoreHeadBox: box(scoreHead),
    labelText: label.textContent.trim(),
    labelBox: box(label),
    streakHeadText: streakHead.innerText,
    figureText: figure.innerText,
    figureAlign: getComputedStyle(row.querySelector('.autotune-run-streak')).textAlign,
    tailBox: box(tail),
    tailText: tail.textContent,
    cellText: row.querySelector('.autotune-run-streak').textContent,
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  };
}"""


def _open_at(probe, width: int, height: int):
    """Size the viewport first, then load: the page sizes its chart canvas at load."""
    probe.page.set_viewport_size({"width": width, "height": height})
    open_advisor(probe)
    _wait_for_cards(probe, 11)
    return probe.page.evaluate(_ON_SCREEN_JS)


def test_the_missing_frozen_verdict_and_the_scores_objective_are_on_screen_on_a_wide_page(
    advisor_probe,
):
    _seed(3)
    wide = _open_at(advisor_probe, 1440, 900)
    assert wide["frozenText"] == "Frozen-eval not evaluated", wide
    assert wide["frozenBox"]["w"] > 100 and wide["frozenBox"]["h"] >= 12, wide
    assert wide["frozenColour"] == wide["inkDim"], "the reason is in dim ink"
    # Wide: the header row names the column and the objective; a row holds the figure only.
    assert wide["headDisplay"] != "none", wide
    assert wide["scoreHeadText"] == "Best objective score", wide
    assert wide["scoreHeadBox"]["w"] > 60 and wide["scoreHeadBox"]["h"] >= 12, wide
    assert wide["streakHeadText"] == "Runs without adopting a tuned config", wide
    assert wide["figureText"].strip().isdigit() and wide["figureAlign"] == "right", wide
    assert wide["tailBox"]["w"] <= 1, "the streak sentence is the header's, not repeated per row"
    assert wide["scrollWidth"] == wide["clientWidth"], "the page scrolls horizontally"
    if advisor_probe.page_errors:
        fail(advisor_probe, "the page raised")


def test_the_missing_frozen_verdict_and_the_scores_objective_are_on_screen_on_a_phone(
    advisor_probe,
):
    _seed(3)
    narrow = _open_at(advisor_probe, 375, 812)
    assert narrow["frozenText"] == "Frozen-eval not evaluated", narrow
    assert narrow["frozenBox"]["w"] > 100 and narrow["frozenBox"]["h"] >= 10, narrow
    assert narrow["frozenColour"] == narrow["inkDim"], narrow
    # Narrow: there is no header, so the row names its own score objective and its own streak.
    assert narrow["headDisplay"] == "none", narrow
    assert narrow["labelText"] == "Best objective score", narrow
    assert narrow["labelBox"]["w"] > 60 and narrow["labelBox"]["h"] >= 10, narrow
    assert narrow["tailBox"]["w"] > 100, "the phone row says what the streak figure counts"
    assert narrow["cellText"].strip().endswith("runs without adopting a tuned config"), narrow
    assert narrow["scrollWidth"] == narrow["clientWidth"], "the page scrolls horizontally"
    if advisor_probe.page_errors:
        fail(advisor_probe, "the page raised")


def test_the_older_runs_table_names_its_own_columns_when_opened(advisor_probe):
    _seed(3)
    open_advisor(advisor_probe)
    _wait_for_cards(advisor_probe, 11)
    page = advisor_probe.page
    page.locator("#autotune-older-summary").click()
    head = page.locator("#autotune-older-list .autotune-row--head")
    assert head.count() == 1 and head.is_visible()
    assert head.locator(".autotune-run-streak").inner_text() == (
        "Runs without adopting a tuned config"
    )
    assert head.locator(".autotune-run-score").inner_text() == "Best objective score"
