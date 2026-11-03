"""Elements the server fixes at render follow the poll, in a real browser (marker ``browser``).

Run with ``python -m pytest -m browser -n 0 tests/ui/test_dashboard_render_follow_browser.py``. Firefox
headless through Playwright; the page and its API answers are the harness world's
(tests/ui/_dashboard_refresh_browser_support.py), the app in this process serving the static scripts. Two
tabs of one context stand for two tabs of the operator's: the flag that makes the dashboard LIVE changes
while both are open, and only the tab whose next poll reports it changes.

That the pill and the legend look right is judged by the capturer on a live render; this proves the
attributes and the text a poll writes in a real engine.
"""

from __future__ import annotations

import copy

import pytest

from tests.dashboard import _refresh_timing_support as sup
from tests.ui._browser_server import running_server
from tests.ui._dashboard_refresh_browser_support import (  # noqa: F401  (fixtures)
    firefox,
    new_probe,
    text,
    wait_refresh,
    world,
)

pytestmark = pytest.mark.browser

PILL = '[data-testid="mode-pill"]'


def _mode_value(probe) -> str:
    """The engine's own value the mode tag holds; the page shows it in sentence case."""
    return " ".join((probe.page.locator(PILL).first.text_content() or "").split())


LEGEND_WORD = '[data-field="legend-mode"]'


def _live(state: dict) -> dict:
    out = copy.deepcopy(state)
    out["meta"]["mode"] = "LIVE"
    return out


def test_a_second_tab_turns_live_on_its_next_poll_and_the_first_stays_as_it_was(firefox, world):  # noqa: F811
    with running_server() as server:
        context = firefox.new_context(viewport={"width": 1440, "height": 900})
        try:
            quiet = new_probe(firefox, server, world, context=context)
            polled = new_probe(firefox, server, world, context=context)
            same = sup.with_stamp(world["state"], sup.render_stamp(world))
            for probe in (quiet, polled):
                probe.state = same
                probe.page.goto(server.base_url + "/")
                wait_refresh(probe, 1)
            assert _mode_value(polled) == "DRY RUN"

            polled.state = _live(same)
            polled.page.evaluate("window.loadState()")
            wait_refresh(polled, 2)

            assert _mode_value(polled) == "LIVE"
            assert polled.page.locator(PILL).first.get_attribute("data-live") == "true"
            assert text(polled, LEGEND_WORD) == "live"
            assert _mode_value(quiet) == "DRY RUN", "a tab that did not poll changed"
            assert text(quiet, LEGEND_WORD) == "dry-run"
        finally:
            context.close()
