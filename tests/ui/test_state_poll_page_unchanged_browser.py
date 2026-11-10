"""The dashboard shows and requests exactly what it did while the poll still carried a table.

Browser-only (marker ``browser``, deselected by default; run with
``python -m pytest -m browser -n 0 tests/ui/test_state_poll_page_unchanged_browser.py``).
Firefox headless through Playwright, imported inside a fixture; a missing Playwright or Firefox
fails with a message naming it, never a skip.

Each scenario is a world of ``tests/app/_state_poll_worlds.py`` served by the app in this process
and opened at ``/`` (``tests/ui/_state_poll_page_capture.py``). The record the page produces is
compared with the record the reference commit's page produced for the same world, written by
``tests/ui/produce_state_poll_page_baseline.py`` into
``tests/fixtures/dashboard/state_poll_page_baseline/baseline.json``. No figure is typed here.

The page never read the ``html`` key, so a body without it must change nothing the page shows or
asks: first poll open and closed, the fallback poll, a refresh on a server event, the detail panel
while a poll lands, a failed poll, an expired session, a narrow dark view, and no state at all.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import database
from tests.app._state_poll_worlds import CORPORATE_ID, load_derived
from tests.app.conftest import _reset_scheduler_run_state  # noqa: F401  (autouse, health state)
from tests.ui._shell_support import (  # noqa: F401  (fixtures: the two overrides, the empty directory)
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    firefox,
)
from tests.ui._state_poll_page_capture import SCENARIOS, capture_scenario

pytestmark = pytest.mark.browser

BASELINE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "dashboard"
    / "state_poll_page_baseline"
    / "baseline.json"
)
REFERENCE_COMMIT = "6b63f53e0d718e230878df900a4aabc72c5b67ed"
FOOTER = re.compile(r"^id: (?P<id>\S+) · normalized: (?P<normalized>.+)$")


@pytest.fixture(scope="module")
def baseline() -> dict:
    if not BASELINE_PATH.is_file():
        pytest.fail(
            f"no baseline at {BASELINE_PATH}: the producer has not been run at the reference"
        )
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def _differences(record: dict, expected: dict) -> list[str]:
    missing = object()
    out = []
    for key in sorted(set(record) | set(expected)):
        got, want = record.get(key, missing), expected.get(key, missing)
        if got != want:
            out.append(f"{key}: page {got!r:.300} != reference {want!r:.300}")
    return out


def test_the_baseline_holds_every_scenario_with_something_on_the_page(baseline):
    assert set(baseline["scenarios"]) == set(SCENARIOS)
    assert baseline["reference_commit"] == REFERENCE_COMMIT
    for name, record in baseline["scenarios"].items():
        if name == "open_poll_answered_401":
            assert record["final_path"] != "/", "the expired session must leave the dashboard"
        else:
            assert record["page_text"], f"{name}: the reference capture holds an empty page"


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_the_page_equals_the_reference_page(firefox, baseline, monkeypatch, tmp_path, name):  # noqa: F811
    assert baseline["firefox_version"] == firefox.version, (
        "the browser differs from the one the baseline was captured with: recapture it from a "
        f"throwaway worktree at {REFERENCE_COMMIT}"
    )

    record = capture_scenario(firefox, name, monkeypatch, tmp_path)

    differing = _differences(record, baseline["scenarios"][name])
    assert not differing, f"{name}: differs from the reference page:\n" + "\n".join(differing)
    assert record["page_errors"] == [], "the page raised an error"


def test_the_detail_panel_footer_names_the_hash_and_the_normalized_name(
    firefox,
    baseline,
    monkeypatch,
    tmp_path,  # noqa: F811
):
    record = capture_scenario(firefox, "open_detail_panel_poll", monkeypatch, tmp_path)

    entries = load_derived("live_state_two_accounts.json")["entries"]
    for footer in (record["before_poll"]["footer"], record["footer"]):
        found = FOOTER.match(footer or "")
        assert found, f"the footer reads {footer!r}"
        assert found["id"] in entries, "the footer names an id no record holds"
        assert found["normalized"] == database.normalize_name(entries[found["id"]]["name"])
    assert CORPORATE_ID in entries, "fixture sanity: the record holds the captured symphony"


@pytest.mark.parametrize("name", ["open_fallback_poll", "open_sse_refresh"])
def test_a_fallback_poll_and_a_server_event_each_send_one_state_request_without_a_sort(
    firefox,
    monkeypatch,
    tmp_path,
    name,  # noqa: F811
):
    record = capture_scenario(firefox, name, monkeypatch, tmp_path)

    assert record["state_requests"] == ["/api/state"], f"{name}: {record['state_requests']}"
