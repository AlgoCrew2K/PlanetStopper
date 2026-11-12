"""The Advisor Overview's first screen in a real browser: what the operator acts on is on it.

Browser-only (marker ``browser``, deselected by default; run with
``python -m pytest -m browser -n 0 tests/ui/test_advisor_overview_order_browser.py``). Firefox
headless through Playwright, imported inside a fixture; a missing Playwright or Firefox fails
with a message naming it, never a skip.

The server is the app in this process on the per-test database, seeded with a stored Market
Prism read (the tallest block of the page), observation rows and autotune runs through their
real writers. At 1440x900 the controls bar ends inside the first 900 px and the toolbar row at the
head of the top panel (the grid) holds only the symphony picker, Run, the error slot and the
suggestions slot, and the page reads top to bottom: the top panel, the Prism block, the
observations.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import database
from tests._advisor_observation_records import store
from tests._advisor_overview_prism import store_prism_row
from tests._ux_truth_support import captured_symphonies
from tests.app import _retirement_tab_world as w
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

_FIRST_SCREEN_PX = 900
_BLOCKS = (
    "advisor-grid",
    "market-prism-block",
    "advisor-observations-section",
)
_BOXES_JS = """
(ids) => Object.fromEntries(ids.map((id) => {
  const el = document.querySelector('[data-testid="' + id + '"]');
  if (!el) return [id, null];
  const box = el.getBoundingClientRect();
  return [id, { top: box.top + window.scrollY, bottom: box.bottom + window.scrollY,
                height: box.height, width: box.width }];
}))
"""


def _seed() -> None:
    w.seed_roster()
    store_prism_row()
    store("guardrail_clear", times=4)
    store("guardrail_watch", times=2)
    keys = [database.normalize_name(name) for _, name in captured_symphonies()]
    for i, key in enumerate(keys):
        moment = datetime(2026, 10, 1, 4, 0, 0) + timedelta(minutes=i)
        database.save_autotune_run(
            moment.strftime("%Y-%m-%dT%H:%M:%S"),
            key,
            0.5,
            1.0,
            "Reverted to Fallback",
            0.4,
            0.3,
            naive_sharpe=1.0 + i * 0.01,
        )


_ROW_JS = """
() => {
  const grid = document.querySelector('[data-testid="advisor-grid"]');
  const shown = (el) => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const controls = grid.querySelector('[data-testid="advisor-controls"]');
  const c = controls.getBoundingClientRect();
  const key = (el) => el.getAttribute('data-testid') || el.id || el.className;
  const row = [...grid.children].filter(shown).filter((el) => {
    const r = el.getBoundingClientRect();
    return r.top < c.bottom && r.bottom > c.top;
  }).map(key);
  const inside = [...controls.querySelectorAll('*')].filter(shown).map((el) => el.tagName.toLowerCase());
  return {
    row, inside,
    pointers: grid.querySelectorAll('a[href="#retirement"], [data-testid="retirement-pointer"]').length,
    links: grid.querySelectorAll('a').length,
    bottom: c.bottom + window.scrollY,
  };
}
"""
_ROW_PARTS = {"advisor-controls", "suggestions-column"}
_CONTROL_TAGS = {"div", "label", "select", "option", "button", "span"}


def _assert_toolbar_row(read: dict, state: str) -> None:
    assert set(read["row"]) <= _ROW_PARTS, f"{state}: the toolbar row holds {read['row']}"
    assert "advisor-controls" in read["row"], f"{state}: the controls are not on the row"
    assert read["pointers"] == 0 and read["links"] == 0, f"{state}: a link is on the toolbar"
    assert set(read["inside"]) <= _CONTROL_TAGS, f"{state}: the controls hold {read['inside']}"


def test_the_controls_end_inside_the_first_screen(advisor_probe):
    _seed()
    open_advisor(advisor_probe)
    wait_until(
        advisor_probe,
        "() => document.querySelectorAll(\"#autotune-runs-list [data-testid='autotune-run-row']\").length === 11",
        "the autotune list never finished loading",
    )
    boxes = advisor_probe.page.evaluate(_BOXES_JS, ["advisor-controls", *_BLOCKS])
    missing = [name for name, box in boxes.items() if box is None]
    assert missing == [], f"the Overview has no {missing}"
    assert boxes["advisor-controls"]["bottom"] <= _FIRST_SCREEN_PX, boxes["advisor-controls"]
    assert boxes["advisor-controls"]["height"] > 0 and boxes["advisor-controls"]["width"] > 0


def test_the_toolbar_row_holds_the_picker_run_the_error_slot_and_the_suggestions_slot_only(
    advisor_probe,
):
    _seed()
    open_advisor(advisor_probe)
    read = advisor_probe.page.evaluate(_ROW_JS)
    _assert_toolbar_row(read, "idle")
    assert "select" in read["inside"] and "button" in read["inside"]
    assert advisor_probe.page_errors == [], advisor_probe.page_errors


def test_the_toolbar_row_is_the_same_with_a_symphony_picked_and_while_a_run_is_in_progress(
    advisor_probe,
):
    """A picked symphony and a Run that has not answered (its request held open) change nothing on
    the row."""
    _seed()
    held: list = []
    advisor_probe.page.route("**/ai-advisor/suggest", lambda route: held.append(route))
    open_advisor(advisor_probe)
    wait_until(
        advisor_probe,
        "() => document.querySelectorAll('#symphony-id-input option').length > 1",
        "the symphony picker never listed a symphony",
    )
    advisor_probe.page.evaluate(
        """() => {
          const select = document.getElementById('symphony-id-input');
          select.value = select.options[1].value;
          select.dispatchEvent(new Event('change', { bubbles: true }));
        }"""
    )
    _assert_toolbar_row(advisor_probe.page.evaluate(_ROW_JS), "a symphony picked")
    advisor_probe.page.evaluate("() => document.getElementById('get-suggestions-btn').click()")
    advisor_probe.page.wait_for_timeout(400)
    assert held, "premise: the run's request is in flight"
    _assert_toolbar_row(advisor_probe.page.evaluate(_ROW_JS), "a run in progress")
    for route in held:
        route.abort()
    assert advisor_probe.page_errors == [], advisor_probe.page_errors


def test_the_page_reads_top_panel_prism_observations_top_to_bottom(advisor_probe):
    _seed()
    open_advisor(advisor_probe)
    boxes = advisor_probe.page.evaluate(_BOXES_JS, list(_BLOCKS))
    tops = [boxes[name]["top"] for name in _BLOCKS]
    assert tops == sorted(tops) and len(set(tops)) == len(tops), dict(
        zip(_BLOCKS, tops, strict=True)
    )
    prism, grid = boxes["market-prism-block"], boxes["advisor-grid"]
    assert grid["bottom"] <= prism["top"] + 1, "the Prism block overlaps the grid"
    if advisor_probe.page_errors:
        fail(advisor_probe, "the page raised")
