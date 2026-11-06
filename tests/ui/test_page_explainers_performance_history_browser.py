"""Performance and History in a real browser: the added copy shows, and no panel is left half empty.

Browser-only (marker ``browser``; run with
``python -m pytest -m browser -n 0 tests/ui/test_page_explainers_performance_history_browser.py``).
Firefox headless through Playwright; the app in this process on the per-test database, seeded through
the real writers from the captured records (``tests/ui/_performance_page.seed_world`` for the shadow
rows and exits, ``tests/ui/_shell_support.seed_history_exits`` for the captured post-mortem days). The
clock is fixed as in ``tests/ui/_page_explainers`` (app, date windows and browser). Never skips.

CONTRACT PINNED
* Every description of the two hosts is visible with its exact text at 1440 and at 375, not clipped,
  and every definition disclosure starts closed and opens to its exact rows.
* The disclosure looks the same on both pages (one shared rule): the summary's resolved font size,
  weight, colour and cursor are equal across hosts.
* At 1440 and at 1240 no panel of either page holds an empty rectangle of 240 by 80 px (the plan's
  AC-6 measure, ``EMPTY_REGION_JS``: glyph boxes, controls, canvases, SVGs and filled bars are
  content; borders and rules are not). Performance is measured with 10 metric rows and 11
  precondition rows, History with the 8-exit day open; and again when the exit-turnover or the
  preconditions route answers 500.
* History at 375 is at most five screens tall with every description visible.

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
    friction_text,
    norm,
    term_groups,
)

pytestmark = pytest.mark.browser

MIN_W, MIN_H = 240, 80
DESKTOP_WIDTHS = {"1440": WIDE, "1240": (1240, 900)}
EXIT_DAY = "2026-06-23"  # the captured day with 8 exits
PANELS = {
    "/performance": (
        "perf-controls-panel",
        "perf-chart-block",
        "metrics-table",
        "guard-alpha-preconditions-panel",
        "exit-turnover-panel",
    ),
    "/history": ("history-controls", "daily-strip", "by-reason-section", "recent-triggers"),
}


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    """Shadow rows and exits (Performance), captured post-mortem days (History), clock fixed."""
    import autotuner

    perf.seed_world()
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


def _empty_regions(probe) -> list[dict]:
    return probe.page.evaluate(harness.EMPTY_REGION_JS, [harness.PANEL_SELECTOR, MIN_W, MIN_H])


def _assert_no_empty_region(probe, path: str, label: str) -> None:
    found = {f["testid"]: f for f in _empty_regions(probe) if f["testid"]}
    missing = [t for t in PANELS[path] if t not in found]
    assert not missing, f"{label}: panels not displayed, so not measured: {missing}"
    offenders = [
        f"{testid}: {f['largest_empty']['w']:.0f} by {f['largest_empty']['h']:.0f} px at "
        f"({f['largest_empty']['x']:.0f}, {f['largest_empty']['y']:.0f})"
        for testid, f in found.items()
        if testid in PANELS[path] and f["largest_empty"] is not None
    ]
    assert not offenders, (
        f"{label}: empty rectangle of {MIN_W} by {MIN_H} px or more in {offenders}"
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
        assert element["text"] == norm(pc.text(key, "A")), key
        assert not element["clipped"], f"{key} is clipped at {size[0]} px"
        assert element["fontSize"] >= 11, f"{key} is below 11 px"
    assert sideways <= 1, f"{path} scrolls sideways at {size[0]} px by {sideways}"


GROUPS = [
    (path, anchor)
    for path in sorted(DESCRIPTION_KEYS)
    for anchor in term_groups(HOST_OF_PATH[path])
]


@pytest.mark.parametrize("size", [WIDE, NARROW], ids=["1440", "375"])
@pytest.mark.parametrize("path", sorted(DESCRIPTION_KEYS))
def test_each_disclosure_starts_closed_and_opens_to_its_exact_rows(
    firefox,
    advisor_server,
    seeded,
    path,
    size,  # noqa: F811
):
    with open_probe(firefox, advisor_server, width=size[0], height=size[1]) as probe:
        _open(probe, path)
        groups = term_groups(HOST_OF_PATH[path])
        for anchor, rows in groups.items():
            selector = f'details[data-terms="{anchor}"]'
            assert probe.page.evaluate("(s) => document.querySelector(s).open", selector) is False
            hidden_before = probe.page.evaluate(
                "(s) => [...document.querySelectorAll(s + ' [data-term]')]"
                ".every((p) => p.getClientRects().length === 0)",
                selector,
            )
            assert hidden_before, f"{anchor}: the definitions show while the disclosure is closed"
            probe.page.click(f"{selector} > summary")
            shown = probe.page.evaluate(_VISIBLE_TEXT_JS, f"{selector} [data-term]")
            expected = [norm(pc.fill(pc.text(r["key"], "B"), friction_pct="0.25")) for r in rows]
            assert [s["text"] for s in shown] == expected, anchor
            assert all(s["shown"] and not s["clipped"] for s in shown), anchor


def test_the_trading_cost_phrase_is_the_patched_authoritys_value(
    firefox,
    advisor_server,
    seeded,  # noqa: F811
):
    with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1]) as probe:
        _open(probe, "/performance")
        probe.page.click('details[data-terms="F21"] > summary')
        text = probe.page.evaluate(_VISIBLE_TEXT_JS, '[data-term="F24"]')[0]["text"]
    assert text == friction_text("F24", "0.25")


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


# ---------------------------------------------------------------------------
# AC-6: no empty rectangle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", sorted(DESKTOP_WIDTHS))
def test_performance_holds_no_empty_rectangle_with_10_metric_rows_and_11_precondition_rows(
    firefox,
    advisor_server,
    seeded,
    width,  # noqa: F811
):
    size = DESKTOP_WIDTHS[width]
    with open_probe(firefox, advisor_server, width=size[0], height=size[1]) as probe:
        _open(probe, "/performance", preconditions=(200, _usable_preconditions_body()))
        counts = probe.page.evaluate(
            "() => ({metrics: document.querySelectorAll('#metrics-tbody tr').length,"
            " precond: document.querySelectorAll('#guard-alpha-preconditions-tbody tr').length})"
        )
        assert counts == {"metrics": 10, "precond": 11}, counts
        _assert_no_empty_region(probe, "/performance", f"Performance at {width}")


@pytest.mark.parametrize("width", sorted(DESKTOP_WIDTHS))
def test_history_holds_no_empty_rectangle_with_the_8_exit_day_open(
    firefox,
    advisor_server,
    seeded,
    width,  # noqa: F811
):
    size = DESKTOP_WIDTHS[width]
    with open_probe(firefox, advisor_server, width=size[0], height=size[1]) as probe:
        _open(probe, "/history")
        _open_exit_day(probe)
        probe.page.wait_for_timeout(500)
        _assert_no_empty_region(probe, "/history", f"History at {width}")


FAILURES = {
    "turnover answers 500": {"turnover_status": 500},
    "preconditions answer 500": {"preconditions": (500, "Internal Server Error")},
    "both answer 500": {"turnover_status": 500, "preconditions": (500, "Internal Server Error")},
}


@pytest.mark.parametrize("width", sorted(DESKTOP_WIDTHS))
@pytest.mark.parametrize("failure", sorted(FAILURES))
def test_performance_holds_no_empty_rectangle_when_a_panel_route_answers_500(
    firefox,
    advisor_server,
    seeded,
    failure,
    width,  # noqa: F811
):
    size = DESKTOP_WIDTHS[width]
    with open_probe(firefox, advisor_server, width=size[0], height=size[1]) as probe:
        _open(probe, "/performance", **FAILURES[failure])
        probe.page.wait_for_timeout(500)
        _assert_no_empty_region(probe, "/performance", f"Performance at {width}, {failure}")


# ---------------------------------------------------------------------------
# AC-7: History at 375
# ---------------------------------------------------------------------------


def test_history_at_375_is_at_most_five_screens_with_every_description_visible(
    firefox,
    advisor_server,
    seeded,  # noqa: F811
):
    with open_probe(firefox, advisor_server, width=NARROW[0], height=NARROW[1]) as probe:
        _open(probe, "/history")
        facts = probe.page.evaluate("() => ({height: document.documentElement.scrollHeight})")
        shown = {
            key: probe.page.evaluate(_VISIBLE_TEXT_JS, f'[data-explainer="{key}"]')
            for key in DESCRIPTION_KEYS["/history"]
        }
    assert all(len(v) == 1 and v[0]["shown"] for v in shown.values()), (
        "every History description must be on screen while the page stays within five screens"
    )
    assert facts["height"] <= 5 * NARROW[1], f"History is {facts['height']} px tall at 375"
