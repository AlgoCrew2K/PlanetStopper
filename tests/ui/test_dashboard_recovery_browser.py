"""The dashboard recovers by itself in a real browser (marker ``browser``): failed polls, a refused event
stream, a return to the tab, and a weekend asleep across the Monday open.

Run with ``python -m pytest -m browser -n 0 tests/ui/test_dashboard_recovery_browser.py``. Firefox
headless through Playwright with its fake clock (``page.clock``); the page and its API answers are the
harness world's (tests/ui/_dashboard_refresh_browser_support.py). Playwright's clock jumps (``fast_forward``)
fire a due timer at most once, as a browser does after a sleep.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest

from tests.dashboard import _refresh_timing_support as sup
from tests.ui._browser_server import running_server
from tests.ui._dashboard_refresh_browser_support import (  # noqa: F401  (fixtures)
    count_requests,
    firefox,
    new_probe,
    text,
    wait_for,
    wait_refresh,
    world,
)

pytestmark = pytest.mark.browser

_FRIDAY_EVENING = dt.datetime(2026, 10, 30, 20, 30, 7, tzinfo=dt.UTC)
_MONDAY_OPEN_PLUS = dt.datetime(2026, 11, 2, 14, 31, 7, tzinfo=dt.UTC)


def _ms(moment: dt.datetime) -> int:
    return int(moment.timestamp() * 1000)


@pytest.fixture
def probe(firefox, world):  # noqa: F811
    with running_server() as server:
        probe = new_probe(firefox, server, world)
        try:
            yield probe
        finally:
            probe.context.close()


def _load(probe, at: dt.datetime | None = None) -> None:
    if at is not None:
        probe.page.clock.install(time=at)
    probe.page.goto(probe.server.base_url + "/")
    wait_refresh(probe, 1)


def test_a_refused_poll_keeps_the_numbers_and_says_connection_lost_until_data_returns(probe, world):
    stamp = sup.render_stamp(world)
    probe.state = sup.with_stamp(world["state"], stamp)
    _load(probe)
    tracked = text(probe, "#hero-tracked")

    probe.state = lambda route: route.fulfill(
        status=500,
        content_type="application/json",
        body='{"status": "error", "message": "An internal error occurred"}',
    )
    probe.page.evaluate("window.loadState()")
    wait_refresh(probe, 2)
    assert text(probe, "#engine-status-label") == "Connection Lost"
    assert text(probe, "#hero-data-as-of") == sup.CONNECTION_LOST_AS_OF_PREFIX + stamp
    assert text(probe, "#hero-tracked") == tracked, "a refused poll changed a count"

    probe.state = sup.with_stamp(world["next"]["state"], sup.later_stamp(stamp))
    probe.page.evaluate("window.loadState()")
    wait_refresh(probe, 3)
    assert text(probe, "#engine-status-label") != "Connection Lost"
    assert text(probe, "#hero-data-as-of") == sup.AS_OF_PREFIX + sup.later_stamp(stamp)


def test_a_stream_answered_502_is_reopened_by_its_own_tab_only(firefox, world):  # noqa: F811
    with running_server() as server:
        context = firefox.new_context(viewport={"width": 1440, "height": 900})
        context.clock.install()
        broken = new_probe(firefox, server, world, context=context)
        healthy = new_probe(firefox, server, world, context=context)
        try:
            broken.events = lambda route: route.fulfill(status=502, body="")
            for probe in (broken, healthy):
                probe.state = sup.with_stamp(world["state"], sup.render_stamp(world))
                probe.page.goto(server.base_url + "/")
                wait_refresh(probe, 1)
            before = count_requests(broken, "GET", "/api/events")
            context.clock.run_for(35_000)
            broken.page.wait_for_timeout(500)
            assert count_requests(broken, "GET", "/api/events") > before, (
                "the page never opened a new stream after the 502"
            )
            assert count_requests(healthy, "GET", "/api/events") == 1, (
                "the other tab's stream was touched"
            )
        finally:
            context.close()


def test_the_wake_signals_of_a_returning_tab_send_one_state_request(probe, world):
    probe.state = sup.with_stamp(world["state"], sup.render_stamp(world))
    _load(probe)
    since = len(probe.requests)
    probe.page.evaluate(
        "() => { document.dispatchEvent(new Event('visibilitychange'));"
        " window.dispatchEvent(new Event('online')); }"
    )
    wait_for(
        probe,
        "() => document.body.dataset.refreshCount === '2'",
        "the wake signals of a returning tab sent no state request",
    )
    probe.page.wait_for_timeout(600)
    assert count_requests(probe, "GET", "/api/state", since) == 1, probe.requests[since:]


def test_a_laptop_asleep_over_the_weekend_resumes_with_the_monday_open_and_no_reload(probe, world):
    friday = sup.closed_body(world["state"])
    friday = sup.with_cycle_at(friday, None)
    monday = sup.open_body(world["state"], "running")
    monday = sup.with_cycle_at(monday, sup.et_iso(_ms(_MONDAY_OPEN_PLUS) - 2_000))
    probe.state = friday
    _load(probe, at=_FRIDAY_EVENING)
    assert text(probe, "#engine-status-label") == "Closed"

    probe.state = monday  # the daemon has been serving since Sunday
    probe.context.clock.fast_forward(_ms(_MONDAY_OPEN_PLUS) - _ms(_FRIDAY_EVENING))
    probe.context.clock.run_for(35_000)
    wait_for(
        probe,
        "() => document.getElementById('engine-status-label').textContent.trim() !== 'Closed'",
        "the header still reads Closed after the page woke on the Monday open",
    )
    assert text(probe, "#engine-status-label") in ("Live", "Opening")
    assert re.fullmatch(r"NEXT \d\d:\d\d", text(probe, "#next-run-countdown"))
    assert text(probe, '[data-testid="et-clock"]').startswith("09:31:"), (
        "the header clock must read Eastern time on the Monday after the clocks went back"
    )
    assert len([u for u in probe.navigations if u.endswith("/")]) == 1, "the page reloaded"
    assert probe.page_errors == []
