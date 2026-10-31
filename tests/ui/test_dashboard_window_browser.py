"""The picked hero window survives refreshes in a real browser (marker ``browser``).

Run with ``python -m pytest -m browser -n 0 tests/ui/test_dashboard_window_browser.py``. Firefox
headless through Playwright; the page and its API answers are the harness world's
(tests/ui/_dashboard_refresh_browser_support.py).
"""

from __future__ import annotations

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


@pytest.fixture
def probe(firefox, world):  # noqa: F811
    with running_server() as server:
        probe = new_probe(firefox, server, world)
        try:
            yield probe
        finally:
            probe.context.close()


def test_three_refreshes_with_1y_picked_keep_the_1y_headline_and_ask_for_its_strip_once_each(
    probe, world
):
    stamp = sup.render_stamp(world)
    probe.state = sup.with_stamp(world["state"], stamp)
    probe.page.goto(probe.server.base_url + "/")
    wait_refresh(probe, 1)
    probe.page.locator('[data-testid="window-1y"]').click()
    wait_for(
        probe,
        "() => document.getElementById('guard-alpha-window-label').textContent.trim() === '1Y'",
        "the window label never read 1Y after the pick",
    )
    want = sup.fmt_pct(world["strips"]["1y"]["guard_alpha"])
    assert text(probe, "#guard-alpha-headline") == want

    since = len(probe.requests)
    for refresh in range(3):
        probe.state = sup.with_stamp(world["next"]["state"], sup.later_stamp(stamp, refresh + 1))
        probe.page.evaluate("window.loadState()")
        wait_refresh(probe, 2 + refresh)
        probe.page.wait_for_timeout(300)  # let the strip request of this refresh answer
        assert text(probe, "#guard-alpha-window-label") == "1Y"
        assert text(probe, "#guard-alpha-headline") == want, (
            f"refresh {refresh + 1}: the poll's 30d value replaced the 1Y headline"
        )
        assert (
            text(probe, '[data-testid="comp-vol-bot-text"]')
            == sup.vol_texts(world["strips"]["1y"])["bot"]
        )

    assert count_requests(probe, "GET", "/api/strip/1y", since) == 3, probe.requests[since:]
    assert count_requests(probe, "GET", "/api/strip/30d", since) == 0, probe.requests[since:]
    assert probe.page_errors == []
