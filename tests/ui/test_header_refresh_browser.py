"""The header on /history, /performance, /ai-advisor and /settings in a real browser.

Browser-only (marker ``browser``; run with ``python -m pytest -m browser -n 0
tests/ui/test_header_refresh_browser.py``). Firefox headless through Playwright, imported inside a
fixture; a missing Playwright or Firefox fails with a message naming it, never a skip. The server is
this process on the per-test database; every other host is refused.

What is asserted on the live render: the page asked the server for its header status on its own
(no reload, no click), the label and the mode pill read values the contract allows, nothing on the
header overflows at 1440 px or 375 px, and the page threw nothing. Colours and sizes are never
asserted. The screenshots land in ``REFRESH_TIMING_SHOTS`` (default: the pytest temp directory) for
the ux-expert.
"""

from __future__ import annotations

import os
import pathlib

import pytest

from tests.ui._shell_support import (  # noqa: F401
    HEADER_CONTROLS,
    NARROW,
    WIDE,
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    advisor_server,
    assert_reachable,
    document_scroll_width,
    fail,
    firefox,
    open_page,
    open_probe,
    seed_dashboard_state,
    wait_until,
)

pytestmark = pytest.mark.browser

PAGES = ["/history", "/performance", "/ai-advisor", "/settings"]
LABELS = {
    "Live",
    "Closed",
    "Stale",
    "Rebalance pause",
    "Opening",
    "Degraded",
    "Not updating",
    "Connection Lost",
}


def shots_dir(tmp_path) -> pathlib.Path:
    target = pathlib.Path(os.environ.get("REFRESH_TIMING_SHOTS", str(tmp_path)))
    target.mkdir(parents=True, exist_ok=True)
    return target


@pytest.mark.parametrize("path", PAGES)
@pytest.mark.parametrize("size", [WIDE, NARROW], ids=["1440", "375"])
def test_the_header_reads_the_engine_by_itself_and_fits(
    firefox, advisor_server, tmp_path, path, size
):  # noqa: F811
    seed_dashboard_state()
    with open_probe(firefox, advisor_server, width=size[0], height=size[1]) as probe:
        requested = []
        probe.page.on("request", lambda r: requested.append(r.url))
        open_page(probe, path)
        wait_until(
            probe,
            "() => performance.getEntriesByType('resource').some((e) => e.name.includes('/api/header-status'))",
            f"{path} never asked for /api/header-status on its own",
        )
        label = probe.page.inner_text("#engine-status-label").strip()
        assert label in LABELS, f"the engine label reads {label!r}"
        pill = probe.page.inner_text('[data-testid="mode-pill"]').strip()
        assert pill in {"LIVE", "DRY RUN"}, f"the mode pill reads {pill!r}"
        for name, selector in HEADER_CONTROLS.items():
            assert_reachable(probe, name, selector, f"{path} at {size[0]} px")
        scroll, inner = document_scroll_width(probe)
        assert scroll <= inner, f"{path} scrolls sideways at {size[0]} px ({scroll} > {inner})"
        assert probe.page_errors == [], probe.page_errors
        probe.page.screenshot(
            path=str(shots_dir(tmp_path) / f"header{path.replace('/', '-')}-{size[0]}.png")
        )
        assert any("/api/header-status" in u for u in requested)
