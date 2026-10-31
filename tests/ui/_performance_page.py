"""The /performance page over the real app's answers, for the client tests and the base-record script.

TEST-ONLY. ``seed_world`` writes the captured production records through the real writers (bot state
via ``database.save_state``, shadow rows via ``database.record_shadow_observation``, exits via
``database.record_exit_trigger``); ``real_routes`` then asks the real routes for the bodies the page
requests, at a frozen host clock, so no body is typed. ``tests/fixtures/refresh_timing/04-c4/
capture_requests_per_tick.py`` runs the same scenario against the page's script at the plan's base
and writes the request list the 60-second tick sent.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import app as app_module
import database
from tests.ui._history_answers import frozen_host_clock
from tests.ui._page_js_harness import answer, render_page, route, run_page
from tests.ui._shell_support import seed_dashboard_state

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "prod_droplet"
SHADOW_ROWS = json.loads((FIXTURES / "shadow_last_rows.json").read_text(encoding="utf-8"))
EXIT_ROWS = json.loads((FIXTURES / "exit_triggers.json").read_text(encoding="utf-8"))

# The page's own default window is 60d; the clock stands just after the last captured row so that
# window holds every captured day.
PERFORMANCE_PATH = "/performance"


def seed_world() -> dict:
    state = seed_dashboard_state()
    for index, row in enumerate(SHADOW_ROWS):
        database.record_shadow_observation(
            symphony_id=row["symphony_id"],
            account_id=row["account_id"],
            cycle_id=row["cycle_id"] or f"fixture-{index}",
            ts_utc=row["ts_utc"],
            ts_et=row["ts_et"],
            trading_day=row["trading_day"],
            current_return=row["current_return"],
            shadow_return=row["shadow_return"],
            is_post_trigger=row["is_post_trigger"],
            trigger_id=None,
            position_epoch=row.get("position_epoch"),
        )
    for row in EXIT_ROWS:
        database.record_exit_trigger(
            symphony_id=row["symphony_id"],
            account_id=row["account_id"],
            triggered_reason=row["triggered_reason"],
            at_return=row["at_return"],
            cycle_id=row["cycle_id"],
            ts_utc=row["ts_utc"],
            ts_et=row["ts_et"],
        )
    return state


def get_real(path: str) -> dict:
    with frozen_host_clock(), app_module.app.test_client() as client:
        response = client.get(path)
    assert response.status_code == 200, f"GET {path} answered {response.status_code}"
    return answer(response)


def symphony_ids() -> list[str]:
    body = get_real("/api/performance/symphonies")["json"]
    ids = [entry["id"] for entry in body["symphonies"]]
    assert len(ids) >= 2, "fixture integrity: the page needs two symphonies to show one fail alone"
    return ids


def real_routes(*, turnover_overrides: dict | None = None) -> list[dict]:
    """Routes answering every request the page makes; ``turnover_overrides`` maps a symphony id to
    the responses its exit-turnover route gives instead (a list of specs, the last repeating)."""
    routes = [
        route(
            r"/api/performance/symphonies$", get_real("/api/performance/symphonies"), method="GET"
        ),
        route(
            r"/api/guard-alpha-preconditions$",
            get_real("/api/guard-alpha-preconditions"),
            method="GET",
        ),
        route(
            r"/api/performance\?",
            get_real("/api/performance?scope=aggregate&days=60d"),
            method="GET",
        ),
    ]
    overrides = turnover_overrides or {}
    for sid in symphony_ids():
        responses = overrides.get(sid) or [get_real(f"/api/exit-turnover?symphony_id={sid}")]
        routes.append(route(rf"/api/exit-turnover\?symphony_id={sid}$", *responses, method="GET"))
    return routes


def run_performance(scenario: str, routes: list[dict], *, chart: str = "stub"):
    return run_page(
        render_page(PERFORMANCE_PATH),
        "await H.boot();\n" + scenario,
        routes=routes,
        pathname=PERFORMANCE_PATH,
        chart=chart,
    )


def one_minute_tick_requests() -> list[str]:
    """``METHOD url`` of every request the page sends when its 60-second timer fires, sorted."""
    out = run_performance(
        "const before = H.requests.length; await H.advance(60000);"
        " return H.requests.slice(before).map((r) => r.method + ' ' + r.url);",
        real_routes(),
    )
    # The header's own poll and the alert badge's poll run on their own timers; this page's tick is
    # the data requests the page itself sends.
    data = re.compile(r"^GET /api/(performance|exit-turnover|guard-alpha-preconditions)")
    return sorted(line for line in out.result if data.match(line))
