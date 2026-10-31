"""/history in a real browser: the data-age stamp, a drilled day and the way back to Today's exits.

Browser-only (marker ``browser``; ``python -m pytest -m browser -n 0
tests/ui/test_history_refresh_browser.py``). Firefox headless through Playwright, imported inside a
fixture; a missing Playwright or Firefox fails with a message naming it, never a skip. The server is
this process over the captured post-mortem days (``seed_history_exits``); every other host is refused.

Asserted on the live render: the stamp reads ``data as of`` and a dated Eastern time, a bar click
drills a day and the heading names it, the Today's exits button returns to today, and nothing
overflows at 375 px. Screenshots go to ``REFRESH_TIMING_SHOTS`` for the ux-expert.
"""

from __future__ import annotations

import os
import pathlib
import re

import pytest

from tests.ui._shell_support import (  # noqa: F401
    NARROW,
    WIDE,
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    advisor_server,
    document_scroll_width,
    firefox,
    open_page,
    open_probe,
    seed_dashboard_state,
    seed_history_exits,
    wait_until,
)

pytestmark = pytest.mark.browser

STAMP = re.compile(r"^data as of \d{4}-\d{2}-\d{2} \d{2}:\d{2} ET$")


def shots_dir(tmp_path) -> pathlib.Path:
    target = pathlib.Path(os.environ.get("REFRESH_TIMING_SHOTS", str(tmp_path)))
    target.mkdir(parents=True, exist_ok=True)
    return target


@pytest.mark.parametrize("size", [WIDE, NARROW], ids=["1440", "375"])
def test_stamp_drill_and_return(firefox, advisor_server, tmp_path, monkeypatch, size):  # noqa: F811
    seed_dashboard_state()
    days = seed_history_exits(tmp_path, monkeypatch.setattr)
    with open_probe(firefox, advisor_server, width=size[0], height=size[1]) as probe:
        open_page(probe, "/history", fragment="")
        wait_until(
            probe,
            "() => /data as of/.test((document.querySelector('#history-data-as-of') || {}).textContent || '')",
            "History never showed its data-age stamp",
        )
        stamp = probe.page.inner_text("#history-data-as-of").strip()
        assert STAMP.match(stamp), f"the stamp reads {stamp!r}"

        window_button = probe.page.query_selector('#window-picker button[data-window="365"]')
        window_button.click()
        wait_until(
            probe,
            "() => document.querySelectorAll('#daily-chart-svg rect[data-date]').length > 0",
            "no day bar to click",
        )
        bar = probe.page.query_selector("#daily-chart-svg rect[data-date]")
        day = bar.get_attribute("data-date")
        bar.dispatch_event("click")
        wait_until(
            probe,
            f"() => (document.querySelector('#todays-exits-heading') || {{}}).textContent.startsWith('{day} exits')",
            "the bar click did not drill the day",
        )
        probe.page.screenshot(path=str(shots_dir(tmp_path) / f"history-drilled-{size[0]}.png"))

        probe.page.click('[data-testid="history-todays-exits-btn"]')
        wait_until(
            probe,
            "() => (document.querySelector('#todays-exits-heading') || {}).textContent.startsWith(\"Today's exits\")",
            "the Today's exits button did not return to today",
        )
        scroll, inner = document_scroll_width(probe)
        assert scroll <= inner, f"/history scrolls sideways at {size[0]} px"
        assert probe.page_errors == [], probe.page_errors
        assert days, "fixture integrity: captured days were seeded"
