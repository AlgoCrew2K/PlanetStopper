"""The whole stack at the narrow and wide extremes: no page scrolls sideways at 1000, 390 or 320 px,
the header's controls are reachable at 320 px, no table at 1440 px gained a scroll bar, and at the
phone width (390 by 844) a refresh that fails, an unsaved Settings edit and every opened Glossary
leave the page inside the viewport.

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
import re

import pytest

from tests.app.conftest import _reset_scheduler_run_state  # noqa: F401  (autouse, health state)
from tests.ui import _alignment as al
from tests.ui import _page_explainers as harness
from tests.ui import _shell_capture as shell
from tests.ui import test_page_explainers_cross_host_browser as cross_host_module
from tests.ui import test_page_explainers_dashboard_browser as dashboard_module
from tests.ui import test_page_explainers_settings_browser as settings_module
from tests.ui._shell_support import (  # noqa: F401
    HEADER_CONTROLS,
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
from tests.ui.test_page_explainers_dashboard_browser import seeded  # noqa: F401  (seeding fixture)
from tests.ui.test_page_explainers_settings_browser import world  # noqa: F401  (dashboard world)

pytestmark = pytest.mark.browser

BASELINE = json.loads(
    (REPO / "tests" / "fixtures" / "dashboard" / "shell_baseline" / "baseline.json").read_text(
        encoding="utf-8"
    )
)["pages_1440"]


@pytest.mark.parametrize(
    "width, height",
    [(w, al.NARROW_VIEWPORT_HEIGHT[w]) for w in al.NARROW],
    ids=[str(w) for w in al.NARROW],
)
@pytest.mark.parametrize("path", PAGES, ids=PAGE_IDS)
def test_no_page_scrolls_sideways_at_narrow_widths(
    firefox, advisor_server, tmp_path, monkeypatch, path, width, height
):
    """(1000, 390 and 320): the document is exactly as wide as the viewport."""
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


# ---------------------------------------------------------------------------
# The phone width (390 by 844): a failed refresh, an unsaved edit and every opened Glossary
# ---------------------------------------------------------------------------

PHONE = (390, al.NARROW_VIEWPORT_HEIGHT[390])
FAILING_ROUTES = {
    "/": re.compile(r"/api/state(\?|$)"),
    "/performance": re.compile(r"/api/performance"),
    "/history": re.compile(r"/api/history/"),
}
LEAK = re.compile(r"\b(NaN|undefined|Infinity|null|None)\b|-inf\b")
# The detail panel is a drawer off the screen until a card is opened, so its Glossary is read in its
# own call, once the panel is open.
GLOSSARY_LINES_JS = r"""(inPanel) => [...document.querySelectorAll('details[data-terms="glossary"]')]
  .filter((d) => d.getClientRects().length > 0 && !!d.closest('#detail-panel') === inPanel)
  .map((d) => {
    d.open = true;
    const lines = [...d.querySelectorAll('p[data-term]')].map((p) => {
      const r = p.getBoundingClientRect();
      return { key: p.getAttribute('data-term'), shown: p.getClientRects().length > 0,
               left: r.left, right: r.right };
    });
    return { host: d.getAttribute('data-glossary-host'), lines, vw: innerWidth };
  })"""


@pytest.mark.parametrize("width", [PHONE[0], VERY_NARROW[0]])
@pytest.mark.parametrize("host", sorted(FAILING_ROUTES))
def test_a_refresh_answering_500_leaves_the_page_inside_the_viewport_with_its_descriptions(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,  # noqa: F811
    host,
    width,
):
    """Rejects a page whose failed refresh writes a placeholder word, drops the descriptions the
    server drew, or leaves a row wider than the phone."""
    seeded("full")
    with open_probe(
        firefox, advisor_server, width=width, height=al.NARROW_VIEWPORT_HEIGHT[width]
    ) as probe:
        probe.page.route(
            FAILING_ROUTES[host],
            lambda route: route.fulfill(
                status=500, content_type="text/plain", body="Internal Server Error"
            ),
        )
        probe.page.goto(probe.server.base_url + host)
        probe.page.wait_for_timeout(harness.SETTLE_MS * 2)
        scroll, viewport = document_scroll_width(probe)
        described = probe.page.evaluate(
            "() => document.querySelectorAll('[data-explainer]').length"
        )
        text = probe.page.evaluate("() => document.body.innerText")
    assert scroll == viewport, f"{host} at {width} px scrolls sideways: {scroll} in {viewport}"
    assert described > 0, f"{host}: the failed refresh took the descriptions off the page"
    assert not LEAK.search(text), f"{host}: a placeholder in the page text: {LEAK.search(text)}"


def test_an_unsaved_settings_edit_survives_a_section_switch_at_390_and_the_page_fits(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    world,  # noqa: F811
):
    with settings_module.settings_open(firefox, advisor_server, size=PHONE) as probe:
        probe.page.fill("#execution-start-time", "09:35")
        assert probe.page.is_enabled("#btn-save")
        for section in ("algorithm", "credentials", "symphonies", "maintenance", "master"):
            settings_module.go_to(probe, section)
            scroll, viewport = document_scroll_width(probe)
            assert scroll == viewport, (
                f"{section} at 390 px scrolls sideways: {scroll} in {viewport}"
            )
        assert probe.page.input_value("#execution-start-time") == "09:35", "the edit was dropped"
        assert probe.page.is_enabled("#btn-save") and probe.page.is_enabled("#btn-discard")
        assert probe.posts == [], "switching section must send nothing"


@pytest.mark.parametrize(("world_name", "host"), harness.WORLD_HOST_PAIRS)
def test_every_glossary_opens_at_390_and_shows_each_of_its_lines_inside_the_viewport(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,  # noqa: F811
    world_name,
    host,
):
    seeded(world_name)
    with open_probe(firefox, advisor_server, width=PHONE[0], height=PHONE[1]) as probe:
        probe.context.clock.install(time=harness.CAPTURE_INSTANT)
        open_page(probe, host)
        probe.page.wait_for_timeout(harness.SETTLE_MS)
        opened = []
        for tab in cross_host_module._tabs(probe, host):
            cross_host_module._show(probe, tab)
            opened += probe.page.evaluate(GLOSSARY_LINES_JS, False)
        if host == "/" and world_name != "empty":
            dashboard_module._open_first_card(probe)
            opened += probe.page.evaluate(GLOSSARY_LINES_JS, True)
        scroll, viewport = document_scroll_width(probe)
    assert opened, f"premise: {host} carries a Glossary"
    problems = [
        f"{g['host']} {line['key']}: {'hidden' if not line['shown'] else 'outside the viewport'}"
        for g in opened
        for line in g["lines"]
        if not line["shown"] or line["left"] < -0.5 or line["right"] > g["vw"] + 0.5
    ]
    assert not problems, "\n".join(problems)
    assert scroll == viewport, f"{host} with every Glossary open scrolls sideways at 390 px"
