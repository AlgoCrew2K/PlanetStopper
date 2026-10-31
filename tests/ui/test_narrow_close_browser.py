"""The whole stack at the narrow and wide extremes: no page scrolls sideways at 375 or 320 px, the
header's controls are reachable at 320 px, and no table at 1440 px gained a scroll bar.

Browser-only (marker ``browser``, deselected by default; run with
``python -m pytest -m browser -n 0 tests/ui/test_narrow_close_browser.py``). Firefox headless
through Playwright, imported inside a fixture; a missing Playwright or Firefox fails with a
message naming it, never a skip.

These are the plan's cross-page facts, so they hold only when every earlier fix is in place; a
page still wider than the viewport is fixed in that page's template with a page-local layout rule.
The 320 px restatements of the table, settings, Chat, Frontrunner and matrix conditions are the
``[320]`` cases of ``tests/ui/test_narrow_containment_browser.py``.

The state each page is read over is seeded through the real writers from captured records
(``tests/ui/_shell_support.seed_every_page``), the same seeding the base capture
(``tests/fixtures/dashboard/shell_baseline``) was taken over, so the 1440 px table comparison is
like for like. ``/`` is measured only after the dashboard's readiness signal.
"""

from __future__ import annotations

import json

import pytest

from tests.ui import _shell_capture as shell
from tests.ui._shell_support import (  # noqa: F401
    HEADER_CONTROLS,
    NARROW,
    PAGE_IDS,
    PAGES,
    REPO,
    VERY_NARROW,
    WIDE,
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    advisor_server,
    assert_reachable,
    document_scroll_width,
    firefox,
    nav_rows,
    open_page,
    open_probe,
    seed_every_page,
)

pytestmark = pytest.mark.browser

BASELINE = json.loads(
    (REPO / "tests" / "fixtures" / "dashboard" / "shell_baseline" / "baseline.json").read_text(
        encoding="utf-8"
    )
)["pages_1440"]


@pytest.mark.parametrize("width, height", [NARROW, VERY_NARROW], ids=["375", "320"])
@pytest.mark.parametrize("path", PAGES, ids=PAGE_IDS)
def test_no_page_scrolls_sideways_at_narrow_widths(
    firefox, advisor_server, tmp_path, monkeypatch, path, width, height
):
    """(375) and (320): the document is exactly as wide as the viewport."""
    seed_every_page(tmp_path, monkeypatch.setattr)
    with open_probe(firefox, advisor_server, width=width, height=height) as probe:
        open_page(probe, path)
        probe.page.wait_for_timeout(500)
        scroll, viewport = document_scroll_width(probe)
        wide = probe.page.evaluate(
            """
            () => [...document.querySelectorAll('body *')]
              .filter((e) => e.getBoundingClientRect().right > innerWidth + 1
                             && getComputedStyle(e).position !== 'fixed')
              .slice(0, 6)
              .map((e) => e.tagName + '.' + String(e.className).slice(0, 30) + '#' + e.id)
            """
        )
        assert scroll == viewport, (
            f"{path} at {width} px scrolls sideways: {scroll} px wide in a {viewport} px viewport; "
            f"first elements past the right edge: {wide}"
        )


@pytest.mark.parametrize("path", PAGES, ids=PAGE_IDS)
def test_every_header_control_is_reachable_at_320(firefox, advisor_server, path):
    """the conditions at 320 px."""
    with open_probe(firefox, advisor_server, width=VERY_NARROW[0], height=VERY_NARROW[1]) as probe:
        open_page(probe, path)
        assert nav_rows(probe) > 1
        for label, selector in HEADER_CONTROLS.items():
            assert_reachable(probe, label, selector, f"{path} at {VERY_NARROW[0]} px")
        assert_reachable(
            probe,
            "candidate-alert indicator",
            '[data-testid="candidate-alert-indicator"]',
            f"{path} at {VERY_NARROW[0]} px",
        )


@pytest.mark.parametrize("path", PAGES, ids=PAGE_IDS)
def test_no_table_gained_a_scroll_bar_at_1440(firefox, advisor_server, tmp_path, monkeypatch, path):
    """per table, the box that holds it scrolls now only where it scrolled at the base."""
    seed_every_page(tmp_path, monkeypatch.setattr)
    with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1]) as probe:
        open_page(probe, path)
        probe.page.wait_for_timeout(1500)
        now = probe.page.evaluate(shell.TABLE_OVERFLOW_JS)
        base = BASELINE[path]["tables"]
        assert len(now) == len(base), (
            f"{path} has {len(now)} tables, the base capture {len(base)}; re-capture only with "
            "the PM's ruling"
        )
        gained = [
            (n["index"], n["testid"] or n["cls"])
            for n, b in zip(now, base, strict=True)
            if n["box_scrolls"] and not b["box_scrolls"]
        ]
        assert gained == [], f"{path}: tables that now scroll at 1440 px: {gained}"
        wide = [n["index"] for n in now if n["table_wider_than_viewport"]]
        assert wide == [], f"{path}: tables wider than the viewport at 1440 px: {wide}"
