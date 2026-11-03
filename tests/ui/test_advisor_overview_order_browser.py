"""The Advisor Overview's first screen in a real browser: what the operator acts on is on it.

Browser-only (marker ``browser``, deselected by default; run with
``python -m pytest -m browser -n 0 tests/ui/test_advisor_overview_order_browser.py``). Firefox
headless through Playwright, imported inside a fixture; a missing Playwright or Firefox fails
with a message naming it, never a skip.

The server is the app in this process on the per-test database, seeded with a stored Market
Prism read (the tallest block of the page), observation rows and autotune runs through their
real writers. At 1440x900 the Retirement pointer and the controls bar end inside the first
900 px, share one toolbar row at the head of the top panel (the grid), and the page reads top to
bottom: the top panel, the Prism block, the observations.
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
_TOOLBAR = ("retirement-pointer", "advisor-controls")
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


def test_the_pointer_and_the_controls_end_inside_the_first_screen(advisor_probe):
    _seed()
    open_advisor(advisor_probe)
    wait_until(
        advisor_probe,
        "() => document.querySelectorAll(\"#autotune-runs-list [data-testid='autotune-run-row']\").length === 11",
        "the autotune list never finished loading",
    )
    boxes = advisor_probe.page.evaluate(_BOXES_JS, [*_TOOLBAR, *_BLOCKS])
    missing = [name for name, box in boxes.items() if box is None]
    assert missing == [], f"the Overview has no {missing}"
    assert boxes["retirement-pointer"]["bottom"] <= _FIRST_SCREEN_PX, boxes["retirement-pointer"]
    assert boxes["advisor-controls"]["bottom"] <= _FIRST_SCREEN_PX, boxes["advisor-controls"]
    for name in ("retirement-pointer", "advisor-controls"):
        assert boxes[name]["height"] > 0 and boxes[name]["width"] > 0, f"{name} takes no space"


def test_the_pointer_and_the_controls_share_one_toolbar_row(advisor_probe):
    _seed()
    open_advisor(advisor_probe)
    boxes = advisor_probe.page.evaluate(_BOXES_JS, list(_TOOLBAR))
    pointer, controls = boxes["retirement-pointer"], boxes["advisor-controls"]
    assert abs(pointer["top"] - controls["top"]) < controls["height"], (
        "the pointer sits on its own row instead of beside the controls",
        pointer,
        controls,
    )


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
