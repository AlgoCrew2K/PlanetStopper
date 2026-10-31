"""The dashboard keeps polling and refreshing its numbers when the chart library never loads.

Contract under test: the page loads Chart.js from a CDN. When that request fails, ``Chart`` is
undefined, and the operator still needs the numbers. With no chart library:
  * the poll timer is still registered and each tick requests ``/api/state``;
  * the numbers the state body carries still reach the page;
  * a state body that arrived is never reported as "Connection Lost";
  * no promise is left rejected by the missing library.

Real call path: the real ``static/chrome.js`` and ``static/index.js`` run whole under node over the
page ``GET /`` really renders, with the ``/api/state`` body produced by the real route (see
``_account_page_harness.py``, ``run_page(chart_library=False)``). Fixture provenance:
``tests/fixtures/dashboard/account_filter/PROVENANCE.md``.
"""

from __future__ import annotations

import pytest

from tests.dashboard import _account_page_harness as harness

pytestmark = pytest.mark.usefixtures("_isolated_env")

# A count no rendered page starts with: the server renders the tracked count from the same state
# the route reports, so only a refresh that really ran can show this value.
_TRACKED_AFTER_REFRESH = 977


@pytest.fixture
def _isolated_env(monkeypatch, tmp_path):
    harness.isolate_environment(monkeypatch, tmp_path)


@pytest.fixture
def page_without_chart(monkeypatch, tmp_path) -> dict:
    """The scenario result of loading the page, then one poll tick, with ``Chart`` undefined."""
    fx = harness.load_accounts_fixture()
    html = harness.render_dashboard_html(fx["bot_state"])
    with harness.state_api(monkeypatch, fx["bot_state"]) as client:
        response = client.get("/api/state")
        assert response.status_code == 200
        body = response.get_json()
    body["meta"]["tracked"] = _TRACKED_AFTER_REFRESH
    return harness.run_page(
        tmp_path,
        """async function (H) {
          H.fire('DOMContentLoaded'); await H.flush();
          const afterLoad = {
            tracked: H.byId('hero-tracked').textContent,
            statusLabel: H.byId('engine-status-label').textContent,
            dataAsOf: H.byId('hero-data-as-of').textContent,
          };
          const mark = H.mark();
          H.poll(); await H.flush();
          return { afterLoad: afterLoad, pollRequests: H.stateRequests(mark),
                   statusLabelAfterPoll: H.byId('engine-status-label').textContent };
        }""",
        html=html,
        routes={"/api/state": body},
        chart_library=False,
        collect_unhandled=True,
    )


def test_each_poll_tick_requests_the_state_when_the_chart_library_is_missing(page_without_chart):
    requests = page_without_chart["result"]["pollRequests"]
    assert len(requests) == 1, (
        f"one poll tick must issue one state request without the chart library, got {requests}"
    )


def test_the_state_numbers_reach_the_page_when_the_chart_library_is_missing(page_without_chart):
    assert page_without_chart["result"]["afterLoad"]["tracked"] == str(_TRACKED_AFTER_REFRESH)


def test_a_state_body_that_arrived_is_not_reported_as_connection_lost(page_without_chart):
    result = page_without_chart["result"]
    shown = {
        "status label after load": result["afterLoad"]["statusLabel"],
        "status label after a poll": result["statusLabelAfterPoll"],
        "data-as-of after load": result["afterLoad"]["dataAsOf"],
    }
    lost = {k: v for k, v in shown.items() if "connection lost" in v.lower()}
    assert not lost, f"a state body arrived, yet the page says connection lost: {lost}"


def test_the_missing_chart_library_leaves_no_rejected_promise(page_without_chart):
    assert page_without_chart["unhandled"] == [], (
        "the page's scripts left promises rejected: "
        f"{page_without_chart['unhandled']}"
    )
