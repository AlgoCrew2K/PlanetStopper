"""Performance and History in a real browser: the added copy shows and the disclosures work.

Browser-only (marker ``browser``; run with
``python -m pytest -m browser -n 0 tests/ui/test_page_explainers_performance_history_browser.py``).
Firefox headless through Playwright; the app in this process on the per-test database, seeded through
the real writers from the captured records (``tests/ui/_performance_page.seed_world`` for the shadow
rows and exits, ``tests/ui/_shell_support.seed_history_exits`` for the captured post-mortem days). The
clock is fixed as in ``tests/ui/_page_explainers`` (app, date windows and browser). Never skips.

CONTRACT PINNED
* Every description of the two hosts is visible with this plan's exact text at 1440 and at 375, not
  clipped, and each page's one Glossary starts closed and opens to its exact lines.
* The disclosure looks the same on both pages (one shared rule): the summary's resolved font size,
  weight, colour and cursor are equal across hosts.

Provenance of the preconditions body (Performance): the real ``/api/guard-alpha-preconditions``
answer over the captured shadow rows, with each symphony's shadow ``verdict`` changed from
``INSUFFICIENT_DATA`` to ``NOT_MET``; the captured shadow history (13 days) is shorter than the
40-observation floor, so the real answer renders no row, and the panel's 11-row state needs a usable
verdict. Every other figure is the route's own.
"""

from __future__ import annotations

import json
import os

import pytest

import analytics
from tests.app.conftest import _reset_scheduler_run_state  # noqa: F401  (autouse, health state)
from tests.ui import _page_copy as pc
from tests.ui import _page_explainers as harness
from tests.ui import _performance_page as perf
from tests.ui._heading_margin_capture import CAPTURE_INSTANT
from tests.ui._shell_support import (  # noqa: F401  (fixtures: overrides, server, browser)
    NARROW,
    WIDE,
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    advisor_server,
    firefox,
    open_page,
    open_probe,
    seed_history_exits,
    wait_until,
)
from tests.ui.test_page_copy_performance_history import (
    DESCRIPTION_KEYS,
    HOST_OF_PATH,
    norm,
    warm_market_calendar,
)

pytestmark = pytest.mark.browser

EXIT_DAY = "2026-06-23"  # the captured day with 8 exits


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    """Shadow rows and exits (Performance), captured post-mortem days (History), clock fixed."""
    import autotuner

    perf.seed_world()
    warm_market_calendar()
    harness.fix_clock_and_token(monkeypatch.setattr)
    monkeypatch.setattr(analytics, "DB_FILE", os.environ["DB_PATH"])
    seed_history_exits(tmp_path, monkeypatch.setattr)
    monkeypatch.setattr(autotuner, "SIM_EXIT_FRICTION_PCT", 0.25)


def _usable_preconditions_body() -> dict:
    body = perf.get_real("/api/guard-alpha-preconditions")["json"]
    assert len(body["symphonies"]) == 11, "premise: eleven captured symphonies"
    for entry in body["symphonies"].values():
        entry["shadow"]["verdict"] = "NOT_MET"
    return body


def _fulfil(probe, pattern: str, *, status: int, body) -> None:
    payload = body if isinstance(body, str) else json.dumps(body)
    content_type = "text/html" if isinstance(body, str) else "application/json"
    probe.page.route(
        pattern,
        lambda request_route: request_route.fulfill(
            status=status, content_type=content_type, body=payload
        ),
    )


def _open(probe, path: str, *, preconditions=None, turnover_status=None) -> None:
    probe.context.clock.install(time=CAPTURE_INSTANT)
    if path == "/performance":
        if preconditions is not None:
            status, body = preconditions
            _fulfil(probe, "**/api/guard-alpha-preconditions", status=status, body=body)
        if turnover_status is not None:
            _fulfil(
                probe,
                "**/api/exit-turnover?*",
                status=turnover_status,
                body="Internal Server Error",
            )
    open_page(probe, path)
    if path == "/performance":
        wait_until(
            probe,
            "() => document.querySelectorAll('#exit-turnover-tbody tr').length > 0",
            "the exit-turnover panel never drew a row",
        )
        if preconditions is not None and preconditions[0] == 200:
            wait_until(
                probe,
                "() => document.querySelectorAll('#guard-alpha-preconditions-tbody tr').length > 0",
                "the preconditions panel never drew a row",
            )
    probe.page.wait_for_timeout(1000)


def _open_exit_day(probe) -> None:
    probe.page.click(f'#daily-chart-svg rect[data-date="{EXIT_DAY}"]')
    wait_until(
        probe,
        "() => document.querySelectorAll('#triggers-tbody tr').length === 8",
        "the 8-exit day never filled the exits table",
    )


# ---------------------------------------------------------------------------
# Descriptions and definitions are visible
# ---------------------------------------------------------------------------

_VISIBLE_TEXT_JS = """
(selector) => [...document.querySelectorAll(selector)].map((el) => {
  const r = el.getBoundingClientRect();
  const cs = getComputedStyle(el);
  return {
    text: el.innerText.replace(/\\s+/g, ' ').trim(),
    shown: el.getClientRects().length > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'
        && r.width > 0 && r.height > 0,
    clipped: el.scrollWidth > el.clientWidth + 1,
    fontSize: parseFloat(cs.fontSize),
  };
})
"""


@pytest.mark.parametrize("path", sorted(DESCRIPTION_KEYS))
@pytest.mark.parametrize("size", [WIDE, NARROW], ids=["1440", "375"])
def test_every_description_is_visible_with_its_exact_text(
    firefox,
    advisor_server,
    seeded,
    path,
    size,  # noqa: F811
):
    with open_probe(firefox, advisor_server, width=size[0], height=size[1]) as probe:
        _open(probe, path)
        facts = {
            key: probe.page.evaluate(_VISIBLE_TEXT_JS, f'[data-explainer="{key}"]')
            for key in DESCRIPTION_KEYS[path]
        }
        sideways = probe.page.evaluate("() => document.documentElement.scrollWidth - innerWidth")
    for key, found in facts.items():
        assert len(found) == 1, f"{key}: {len(found)} elements"
        element = found[0]
        assert element["shown"], f"{key} is not visible at {size[0]} px"
        assert element["text"] == norm(pc.a_text(key)), key
        assert not element["clipped"], f"{key} is clipped at {size[0]} px"
        assert element["fontSize"] >= 11, f"{key} is below 11 px"
    assert sideways <= 1, f"{path} scrolls sideways at {size[0]} px by {sideways}"


@pytest.mark.parametrize("size", [WIDE, NARROW], ids=["1440", "375"])
@pytest.mark.parametrize("path", sorted(DESCRIPTION_KEYS))
def test_the_glossary_starts_closed_and_opens_to_its_exact_lines(
    firefox,
    advisor_server,
    seeded,
    path,
    size,  # noqa: F811
):
    host = HOST_OF_PATH[path]
    selector = f'details[data-terms="glossary"][data-glossary-host="{host}"]'
    with open_probe(firefox, advisor_server, width=size[0], height=size[1]) as probe:
        _open(probe, path)
        assert probe.page.locator('details[data-terms]:not([data-terms="glossary"])').count() == 0
        assert probe.page.locator(selector).count() == 1, f"no Glossary for {host}"
        assert probe.page.evaluate("(s) => document.querySelector(s).open", selector) is False
        hidden_before = probe.page.evaluate(
            "(s) => [...document.querySelectorAll(s + ' [data-term]')]"
            # A closed details shows only its summary: its other children are not visible
            # whatever boxes the browser keeps for them (no page rule removes them).
            ".every((p) => { const d = p.closest('details');"
            " return !!d && !d.open && !p.closest('summary'); })",
            selector,
        )
        assert hidden_before, f"{host}: the definitions show while the Glossary is closed"
        summary = probe.page.evaluate(
            "(s) => document.querySelector(s + ' > summary').textContent.trim()", selector
        )
        assert summary == "Glossary"
        probe.page.click(f"{selector} > summary")
        shown = probe.page.evaluate(_VISIBLE_TEXT_JS, f"{selector} [data-term]")
        expected = [norm(pc.fill(line["text"], friction_pct="0.25")) for line in pc.glossary(host)]
        assert [s["text"] for s in shown] == expected, host
        assert all(s["shown"] and not s["clipped"] for s in shown), host


def test_the_trading_cost_line_is_the_patched_authoritys_value(
    firefox,
    advisor_server,
    seeded,  # noqa: F811
):
    with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1]) as probe:
        _open(probe, "/performance")
        assert probe.page.locator('details[data-terms="glossary"]').count() == 1, "no Glossary"
        probe.page.click('details[data-terms="glossary"] > summary')
        text = probe.page.evaluate(_VISIBLE_TEXT_JS, '[data-term="F24"]')[0]["text"]
    line = next(x for x in pc.glossary("Performance") if x["key"] == "F24")
    assert text == norm(pc.fill(line["text"], friction_pct="0.25"))


_SUMMARY_STYLE_JS = """
(selector) => {
  const el = document.querySelector(selector);
  if (!el) return null;
  const cs = getComputedStyle(el);
  return {
    fontSize: cs.fontSize, fontWeight: cs.fontWeight, color: cs.color, cursor: cs.cursor,
    textTransform: cs.textTransform,
  };
}
"""


def test_the_disclosure_summary_looks_the_same_on_both_pages(
    firefox,
    advisor_server,
    seeded,  # noqa: F811
):
    styles = {}
    for path, selector in (
        ("/performance", "details[data-terms] > summary"),
        ("/history", "details[data-terms] > summary"),
        ("/history", "#reason-descriptions > summary"),
    ):
        with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1]) as probe:
            _open(probe, path)
            styles[(path, selector)] = probe.page.evaluate(_SUMMARY_STYLE_JS, selector)
    reference = styles[("/history", "#reason-descriptions > summary")]
    assert reference is not None
    for key, style in styles.items():
        assert style is not None, f"{key}: no such summary on the page"
        assert style == reference, (
            f"{key} differs from History's own disclosure: {style} vs {reference}"
        )
    assert reference["cursor"] == "pointer"
