"""The header never makes a page wider than the screen and Emergency Liquidate stays on it, in a real browser.

Browser-only (marker ``browser``; run with ``python -m pytest -m browser -n 0
tests/ui/test_header_never_widens_page_browser.py``, a ``-k`` of a page picks a chunk). Firefox
headless through Playwright. Each test opens one page with the header in one engine state, then
walks one browser window through every width of ``WIDTHS`` (the layout bands from 1001 px up, the
widths either side of the header's own 1400 and 1500 px rules and 1460 px, where the review saw the
page scroll sideways) and measures:

* the document is no wider than the window (no sideways scroll because of the header);
* the Emergency Liquidate button lies wholly inside the window;
* the header's controls are all present and shown: Force run now, Emergency Liquidate and the five
  page links.

The header is read in two states, the engine label a short word (``Degraded``) and the longest the
header can show (``Rebalance pause``), on every in-scope page. The ET clock and the workspace chip
keep the visibility ``test_header_clock_widths_browser`` pins; this module asserts only the fit.

Provenance: the dashboard is the refresh harness world (``tests/ui/_dashboard_refresh_browser_support``)
with the poll answer put in the named engine state by the helpers of
``test_header_clock_widths_browser``; every other page is the real app on the per-test database with
``/api/header-status`` answered by the real route's own body at a named server instant
(``tests/ui/_header_status_answers``). No figure is typed here.
"""

from __future__ import annotations

import re

import pytest

from tests.ui import _alignment as al
from tests.ui._browser_server import running_server
from tests.ui._dashboard_refresh_browser_support import (  # noqa: F401  (fixtures)
    firefox,
    new_probe,
    wait_for,
    wait_refresh,
    world,
)
from tests.ui._header_status_answers import et, header_answer
from tests.ui._shell_support import (  # noqa: F401  (fixtures)
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    advisor_server,
    open_page,
    open_probe,
    seed_dashboard_state,
)
from tests.ui.test_header_clock_widths_browser import (
    HEIGHT,
    STATES,
    _state_body,
    serve_header_status,
    wait_until_label,
)
from tests.ui.test_page_explainers_performance_history_browser import (  # noqa: F401
    seeded as performance_history_seeded,
)

pytestmark = pytest.mark.browser

WIDTHS = tuple(sorted(set(al.WIDTHS) | {1366, 1401, 1460, 1500}))
SHORT = "degraded-open"
LONG = "rebalance-pause"
PAGES = ("/history", "/performance", "/settings", "/ai-advisor")
# The server instant and degradation each state is read at (a Degraded header needs an open session).
MOMENTS = {SHORT: (et(11, 0), ["engine_failing"]), LONG: (et(15, 56), None)}
LINKS = 5
# The account the review's header held: the chip is as wide as a real account label and short code
# make it. The id is made up in the real shape (the short code is its first eight characters).
ACCOUNT_ENV = {"ACCOUNT_ROTH": "880be47e-5d3c-4b1e-9f2a-3c8d7e6a1b04"}
CHIP_TEXT = "Roth IRA 880be47e"
# The pages set their text in Manrope, a web font the browser tests never download (every request
# but the server's is refused), so the browser falls back to a narrower system face and the header
# measures about 50 px short of the review's 1461 px content edge at a 1440 px window. A letter
# spacing of 0.55 px on the header's text brings the content edge to within 1% of the review's
# (1447, 1450 and 1452 px in windows of 1401, 1420 and 1440 px, against the 1455, 1458 and 1461 px
# it measured, with the brand 169, the links 406, the status 206, the chip 154, Force run now 113
# and Emergency Liquidate 152 px wide), so the fit is judged at widths close to the real font's.
# Only this stylesheet widens the text; no rule of the page is touched. ``!important`` outranks the
# header's own ``letter-spacing: 0 !important`` on its two buttons.
WEB_FONT_WIDTH_CSS = """
[data-testid="studio-nav"], [data-testid="studio-nav"] *,
[data-testid="studio-nav"] > #emergency-liquidate,
[data-testid="studio-nav"] > [data-testid="force-run-btn"],
[data-testid="studio-nav"] > [data-testid="mode-pill"] { letter-spacing: 0.55px !important; }
"""

FACTS_JS = """
() => {
  const vw = innerWidth;
  const q = (s) => document.querySelector(s);
  const box = (el) => { if (!el) return null; const r = el.getBoundingClientRect();
    return { left: r.left, right: r.right, width: r.width, height: r.height }; };
  const liquidate = box(q('#emergency-liquidate'));
  const force = box(q('[data-testid="force-run-btn"]'));
  const links = [...document.querySelectorAll('.shell-nav-links .shell-link')].map(box);
  return {
    viewport: vw,
    scrollWidth: document.documentElement.scrollWidth,
    label: (q('#engine-status-label') || {textContent: ''}).textContent.trim(),
    chip: (q('#ws-wrap') || {textContent: ''}).textContent.replace(/\\s+/g, ' ').trim(),
    liquidate, force, links,
  };
}
"""


def _faults(facts: dict, width: int) -> list[str]:
    problems = []
    if facts["scrollWidth"] > facts["viewport"] + 1:
        problems.append(
            f"the page is {facts['scrollWidth']} px wide in a {facts['viewport']} px window"
        )
    liquidate = facts["liquidate"]
    if not liquidate or liquidate["width"] <= 0:
        problems.append("Emergency Liquidate is not shown")
    elif liquidate["left"] < -0.5 or liquidate["right"] > facts["viewport"] + 0.5:
        problems.append(
            f"Emergency Liquidate spans x={liquidate['left']:.0f} to {liquidate['right']:.0f} "
            f"in a {facts['viewport']} px window"
        )
    if not facts["force"] or facts["force"]["width"] <= 0:
        problems.append("Force run now is not shown")
    shown_links = [link for link in facts["links"] if link and link["width"] > 0]
    if len(shown_links) != LINKS:
        problems.append(f"{len(shown_links)} of {LINKS} page links are shown")
    return [f"{width} px, label {facts['label']!r}: {p}" for p in problems]


def _walk(page, label_ok) -> list[str]:
    problems: list[str] = []
    page.add_style_tag(content=WEB_FONT_WIDTH_CSS)
    for width in WIDTHS:
        page.set_viewport_size({"width": width, "height": HEIGHT})
        page.wait_for_timeout(150)
        facts = page.evaluate(FACTS_JS)
        if not label_ok(facts["label"]):
            problems.append(
                f"{width} px: the header reads {facts['label']!r}, not the state under test"
            )
        if facts["chip"] != CHIP_TEXT:
            problems.append(
                f"{width} px: the workspace chip reads {facts['chip']!r}, not {CHIP_TEXT!r}"
            )
        problems.extend(_faults(facts, width))
    return problems


def _with_real_account_chip(world: dict) -> dict:
    """The harness world's page with its workspace chip reading what an account makes it read.

    The harness render has no account in its environment, so the server prints the chip's placeholder
    words; the page script never rewrites them on the dashboard. Only those two spans' text changes.
    """
    label = re.compile(r'(<span id="ws-label">)[^<]*(</span>)')
    short = re.compile(r'(<span id="ws-uuid"[^>]*>)[^<]*(</span>)')
    html = world["html"]
    assert label.search(html) and short.search(html), "premise: the render holds the chip's spans"
    html = label.sub(r"\g<1>Roth IRA\g<2>", html, count=1)
    html = short.sub(r"\g<1>880be47e\g<2>", html, count=1)
    return {**world, "html": html}


@pytest.mark.parametrize("name", [SHORT, LONG])
@pytest.mark.parametrize("path", PAGES)
def test_a_page_with_the_header_in_each_state_is_never_wider_than_its_window(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    performance_history_seeded,  # noqa: F811
    path,
    name,
):
    """Rejects a header whose row is wider than the window in the 1401 to 1460 px band (the review
    read 1455 to 1463 px in a 1401 to 1460 px window, with Emergency Liquidate cut by the edge)."""
    seed_dashboard_state()
    moment, reasons = MOMENTS[name]
    answer = header_answer(moment, reasons=reasons, accounts=ACCOUNT_ENV)
    labels, _ = STATES[name]
    with open_probe(firefox, advisor_server, width=WIDTHS[0], height=HEIGHT) as probe:
        probe.page.route("**/api/header-status", serve_header_status(answer))
        open_page(probe, path)
        wait_until_label(probe, labels)
        problems = _walk(probe.page, lambda label: label in labels)
    assert not problems, f"{path} in the {name} state:\n" + "\n".join(problems)


@pytest.mark.parametrize("name", [SHORT, LONG])
def test_the_dashboard_with_the_header_in_each_state_is_never_wider_than_its_window(
    firefox,  # noqa: F811
    world,  # noqa: F811
    name,
):
    """The dashboard writes the engine label from its own poll, so it is read through the harness."""
    labels, _ = STATES[name]
    with running_server() as server:
        context = firefox.new_context(viewport={"width": WIDTHS[0], "height": HEIGHT})
        probe = new_probe(firefox, server, _with_real_account_chip(world), context=context)
        try:
            probe.state = _state_body(world, name)
            probe.page.goto(server.base_url + "/")
            wait_refresh(probe, 1)
            wait_for(
                probe,
                "(ls) => ls.includes(document.getElementById('engine-status-label').textContent.trim())",
                f"the header never read one of {sorted(labels)}",
                arg=sorted(labels),
            )
            problems = _walk(probe.page, lambda label: label in labels)
        finally:
            context.close()
    assert not problems, f"/ in the {name} state:\n" + "\n".join(problems)
