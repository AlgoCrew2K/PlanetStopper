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
from tests.ui import _alignment as al
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


# ---------------------------------------------------------------------------
# One line per producer, and the Retirement row among them: every field of every line starts at the
# same offset (rows the page renders on the server, over the stored producers)
# ---------------------------------------------------------------------------


def _seed_lines() -> None:
    """Two guardrail producers, the Market Prism read and a stored good Retirement run."""
    from tests._advisor_overview_prism import store_prism_row

    _seed()
    store_prism_row()
    w.store_captured_summary()


def _open_lines(probe, width: int) -> None:
    probe.page.set_viewport_size(al.viewport(width))
    open_advisor(probe)
    wait_until(
        probe,
        "(sel) => document.querySelectorAll(sel).length === 4",
        "the Overview never showed three producer lines and the Retirement row",
        arg=".obs-producer",
    )
    probe.page.evaluate("() => document.fonts.ready.then(() => true)")


def _line_spec(**extra) -> dict:
    return al.spec_of(al.TABLE_GROUPS["observation_lines"], name="observation_lines", **extra)


@pytest.mark.parametrize("width", al.WIDTHS)
def test_each_lines_label_verdict_time_count_and_last_cell_start_at_one_offset(
    advisor_probe, width
):
    _seed_lines()
    _open_lines(advisor_probe, width)
    result = al.measure(advisor_probe.page, al.FIELD_OFFSETS_JS, _line_spec())
    assert result["found"] == 4, "three producers and the Retirement row are measured"
    assert result["violations"] == [], result["violations"]
    # The count is a figure: it ends at one right edge on the three producers that have one, and
    # the Retirement row, whose count cell is empty, still holds its place for the last cell.
    assert result["fields"]["count"]["rows"] == 3, result["fields"]
    assert result["fields"]["last"]["rows"] == 4, "the Retirement row's last cell is not measured"
    assert advisor_probe.page_errors == [], advisor_probe.page_errors


def test_the_retirement_row_is_the_last_line_and_its_open_tab_link_is_its_last_cell(
    advisor_probe,
):
    _seed_lines()
    _open_lines(advisor_probe, 1440)
    rows = advisor_probe.page.evaluate(
        """() => [...document.querySelectorAll('.obs-producer')].map((r) => ({
          role: r.getAttribute('data-role'),
          label: (r.querySelector('.obs-producer-label') || {}).textContent,
          last: (r.querySelector('.obs-history-toggle, [data-testid="retirement-pointer"]') || {}).textContent,
        }))"""
    )
    assert rows[-1]["role"] == "RETIREMENT", rows
    assert rows[-1]["label"].strip() == "Retirement" and rows[-1]["last"].strip() == "Open tab"
    assert all(r["role"] != "RETIREMENT" for r in rows[:-1]), "only one Retirement row"
    assert all(r["last"].strip() == "History" for r in rows[:-1]), rows


@pytest.mark.parametrize("width", [1920, 1440])
def test_the_lines_columns_sit_close_enough_to_read_across(advisor_probe, width):
    _seed_lines()
    _open_lines(advisor_probe, width)
    entry = al.TABLE_GROUPS["observation_lines"]
    spec = {
        "name": "observation_lines",
        "kind": "fields",
        "rows": entry["rows"],
        "fields": [{"name": f["name"], "selector": f["selector"]} for f in entry["fields"]],
        "max_gap": al.MAX_COLUMN_GAP_PX,
    }
    result = al.measure(advisor_probe.page, al.COLUMN_GAPS_JS, spec)
    assert result["violations"] == [], result["violations"]


@pytest.mark.parametrize("width", [1920, 1440])
def test_an_open_history_leaves_every_line_and_its_own_table_aligned(advisor_probe, width):
    _seed_lines()
    _open_lines(advisor_probe, width)
    advisor_probe.page.locator(_HISTORY.format(role=_OC)).locator("summary").first.click()
    advisor_probe.page.wait_for_timeout(150)
    lines = al.measure(advisor_probe.page, al.FIELD_OFFSETS_JS, _line_spec())
    assert lines["found"] == 4 and lines["violations"] == [], lines["violations"]
    table = al.spec_of(al.TABLE_GROUPS["observation_histories"], name="observation_histories")
    result = al.measure(advisor_probe.page, al.COLUMN_EDGES_JS, table)
    assert result["found"] == 1, "the open history's table is not shown"
    assert result["violations"] == [], result["violations"]


def _requests_during(probe, action) -> set[tuple[str, str]]:
    seen: list[tuple[str, str]] = []

    def record(request):
        seen.append((request.method, request.url.split("#")[0]))

    probe.page.on("request", record)
    try:
        action()
        probe.page.wait_for_timeout(500)
    finally:
        probe.page.remove_listener("request", record)
    return set(seen)


@pytest.mark.parametrize("how", ["click", "enter"])
def test_the_open_tab_link_opens_the_retirement_tab_and_asks_for_nothing_the_tab_does_not(
    advisor_probe, how
):
    _seed_lines()
    _open_lines(advisor_probe, 1440)
    link = advisor_probe.page.locator(
        '.obs-producer[data-role="RETIREMENT"] [data-testid="retirement-pointer"]'
    )

    def use_the_link():
        if how == "click":
            link.click()
        else:
            link.focus()
            advisor_probe.page.keyboard.press("Enter")

    via_link = _requests_during(advisor_probe, use_the_link)
    wait_until(
        advisor_probe,
        "() => document.getElementById('tab-panel-retirement').classList.contains('tab-panel--active')",
        "the link did not open the Retirement tab",
    )
    open_advisor(advisor_probe)
    via_tab = _requests_during(
        advisor_probe, lambda: advisor_probe.page.locator("button[data-tab='retirement']").click()
    )
    assert via_link <= via_tab, f"the link asked for more than the tab does: {via_link - via_tab}"
    assert advisor_probe.page_errors == [], advisor_probe.page_errors
