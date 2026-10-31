"""The Advisor's observation expanders in a real browser.

Browser-only (marker ``browser``, deselected by default; run with
``python -m pytest -m browser -n 0 tests/ui/test_advisor_observations_browser.py``). Firefox
headless through Playwright, imported inside a fixture; a missing Playwright or Firefox fails
with a message naming it, never a skip.

The server is the app in this process on the per-test database, seeded through the real
writer (``tests/_advisor_observation_records.py``). An expander the operator opens must stay
open when they visit another tab and come back: tab switching hides and shows panels in place
and must neither reload the page nor re-render the observations.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

import database
from tests._advisor_observation_records import FIRST_MOMENT, store
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

_OC = "OVERFITTING_CONSCIENCE"
_SC = "SPEC_CRITIC"
_HISTORY = "[data-testid='obs-producer'][data-role='{role}'] [data-testid='obs-producer-history']"


def _seed() -> None:
    w.seed_roster()
    keys = [database.normalize_name(name) for _, name in captured_symphonies()[:3]]
    for k in range(60):
        store("guardrail_clear", symphony_id=keys[k % 3], start=FIRST_MOMENT + timedelta(minutes=k))
    for k in range(3):
        store("guardrail_watch", symphony_id=keys[k % 3], start=FIRST_MOMENT + timedelta(minutes=k))


def _history(probe, role: str):
    return probe.page.locator(_HISTORY.format(role=role))


def _show_tab(probe, name: str):
    probe.page.locator(f"button[data-tab='{name}']").click()
    wait_until(
        probe,
        "(n) => document.getElementById('tab-panel-' + n).classList.contains('tab-panel--active')",
        f"the {name} tab never became active",
        arg=name,
    )


def test_an_opened_expander_is_still_open_after_visiting_another_tab(advisor_probe):
    _seed()
    open_advisor(advisor_probe)
    wait_until(
        advisor_probe,
        "(sel) => document.querySelectorAll(sel).length === 2",
        "the Overview never showed its two observation expanders",
        arg="[data-testid='obs-producer-history']",
    )
    oc, sc = _history(advisor_probe, _OC), _history(advisor_probe, _SC)
    assert oc.evaluate("(el) => el.open") is False
    assert oc.locator("[data-testid='obs-row']").first.is_hidden(), "rows show while closed"
    oc.locator("summary").first.click()
    assert oc.evaluate("(el) => el.open") is True
    assert oc.locator("[data-testid='obs-row']").first.is_visible()

    _show_tab(advisor_probe, "correlations")
    assert oc.locator("[data-testid='obs-row']").first.is_hidden(), "rows show on another tab"
    _show_tab(advisor_probe, "overview")

    assert oc.evaluate("(el) => el.open") is True, "the expander closed while its tab was away"
    assert oc.locator("[data-testid='obs-row']").count() == 50
    assert oc.locator("[data-testid='obs-row']").first.is_visible()
    assert sc.evaluate("(el) => el.open") is False, "an expander the operator never opened opened"
    if advisor_probe.page_errors:
        fail(advisor_probe, "the page raised")
