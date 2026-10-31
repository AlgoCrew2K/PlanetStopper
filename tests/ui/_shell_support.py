"""Shared plumbing for the app-shell browser modules (the ``browser`` marker).

TEST-ONLY. The five modules of the shell work (header, panic flow, page skeleton, narrow
containment, narrow close) share one way to start a page: the app in this process on the
per-test database (``tests/ui/_browser_server.py``), Firefox headless through Playwright
(imported inside a fixture, never at module level), every request to anything but that server
refused so no outside host is contacted, and the developer ``.env`` never read
(``tests/ui/_advisor_browser_support.advisor_server`` points ``app.ENV_FILE_PATH`` at an empty
temporary file and removes every credential variable).

The state each page is read over is the real writers' output from captured records:

* ``seed_dashboard_state``: ``database.save_state`` over ``tests/fixtures/prod_droplet/
  bot_state_positions.json`` verbatim (so ``/`` renders its symphony cards);
* ``seed_history_exits``: the captured post-mortem days, each trigger carrying the one derived
  key the read-time validity guard requires (``tests/fixtures/ux_data_truth/PROVENANCE.md``),
  read through ``analytics._POST_MORTEMS_DIR``;
* ``seed_pending_frontrunner_proposal``: ``database.insert_frontrunner_proposal`` over the
  captured incumbent tree.

A module imports the names it uses and lists the fixtures it needs with ``# noqa: F401``: the
two autouse overrides (this directory's conftest stubs ``database.load_state`` and the state
dict, which a real page needs real), ``advisor_server`` and ``firefox``.
"""

from __future__ import annotations

import json
import pathlib
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

import app as app_module
import database
from tests._ux_truth_support import load_captured_bot_state
from tests.ui._advisor_browser_support import (  # noqa: F401  (re-exported fixtures and helpers)
    PAGE_TIMEOUT_MS,
    WAIT_MS,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    advisor_server,
    fail,
    firefox,
    wait_until,
)
from tests.ui.test_static_js_page_harness import IF_HELD_STAMP, pm_capture

REPO = pathlib.Path(__file__).resolve().parents[2]

PAGES = ("/", "/history", "/performance", "/settings", "/ai-advisor")
PAGE_IDS = ("dashboard", "history", "performance", "settings", "ai-advisor")
NARROW = (375, 812)
VERY_NARROW = (320, 640)
WIDE = (1440, 900)

READY_SELECTOR = 'body[data-dashboard-ready="1"]'
READY_TIMEOUT_MS = 60_000

# The four header controls whose reachability the plan guards, by a selector each.
SETTINGS_LINK = '[data-testid="studio-nav"] a[href="/settings"]'
MODE_PILL = '[data-testid="mode-pill"]'
FORCE_RUN = '[data-testid="force-run-btn"]'
LIQUIDATE = "#emergency-liquidate"
ALERT_INDICATOR = '[data-testid="candidate-alert-indicator"]'
HEADER_CONTROLS = {
    "Settings": SETTINGS_LINK,
    "mode pill": MODE_PILL,
    "Force run now": FORCE_RUN,
    "Emergency Liquidate": LIQUIDATE,
}

CAPTURED_POST_MORTEM_DAYS = (
    "2026-06-22",
    "2026-06-23",
    "2026-06-24",
    "2026-06-25",
    "2026-06-29",
    "2026-06-30",
)

_INSIDE_VIEWPORT_AND_HIT_TESTABLE = """
(selector) => {
  const el = document.querySelector(selector);
  if (!el) return { found: false };
  const r = el.getBoundingClientRect();
  const cx = Math.min(Math.max(r.left + r.width / 2, 0), innerWidth - 1);
  const cy = Math.min(Math.max(r.top + r.height / 2, 0), innerHeight - 1);
  const hit = document.elementFromPoint(cx, cy);
  const style = getComputedStyle(el);
  return {
    found: true,
    left: r.left, right: r.right, top: r.top, bottom: r.bottom,
    width: r.width, height: r.height, viewport: innerWidth,
    displayed: style.display !== 'none' && style.visibility !== 'hidden',
    hit: !!hit && (hit === el || el.contains(hit)),
  };
}
"""


# ---------------------------------------------------------------------------
# Seeding (the real writers, captured records)
# ---------------------------------------------------------------------------


def seed_dashboard_state() -> dict:
    """The captured bot state, written through ``database.save_state``; returned."""
    state = load_captured_bot_state()
    database.save_state(state)
    return state


def seed_history_exits(tmp_path: pathlib.Path, setattr_fn=setattr) -> list[str]:
    """Captured post-mortem days with stamped triggers; ``analytics`` reads them. Days returned.

    ``setattr_fn`` is ``monkeypatch.setattr`` in a test (undone at teardown) or ``setattr`` in a
    capture script.
    """
    import analytics

    directory = tmp_path / "post_mortems"
    directory.mkdir()
    days = []
    for day in CAPTURED_POST_MORTEM_DAYS:
        record = pm_capture(day)
        record["triggers"] = [
            {**t, "if_held_source": IF_HELD_STAMP} for t in record.get("triggers") or []
        ]
        (directory / f"post_mortem_{day}.json").write_text(json.dumps(record), encoding="utf-8")
        days.append(day)
    setattr_fn(analytics, "_POST_MORTEMS_DIR", str(directory))
    return days


_REAL_INCUMBENT_TREE = (
    REPO
    / "tests"
    / "fixtures"
    / "advisors"
    / "frontrunner"
    / ("real_tree_07_iaSOOUsmnCJHiZvbrWfs.json")
)
_FR_INCUMBENT_HASH = "iaSOOUsmnCJHiZvbrWfs"

# The metrics a queued proposal carries (the shape ``_run_build_for_symphony`` persists).
_FR_METRICS = {
    "incumbent_cagr": 0.08,
    "incumbent_max_drawdown": -0.22,
    "incumbent_calmar": 0.36,
    "candidate_cagr": 0.15,
    "candidate_max_drawdown": -0.11,
    "candidate_calmar": 1.36,
    "candidate_sharpe": 1.42,
    "candidate_volatility": 0.14,
    "node_count_delta": -6,
    "overlay_summary": "relative-strength-index(SPY,10) gt 80 rotates into UVXY",
    "replaced_node_id": "cascade-node-id",
}


def seed_pending_frontrunner_proposal() -> int:
    """A pending Frontrunner proposal over the captured incumbent tree, through the writer."""
    state = seed_dashboard_state()
    assert _FR_INCUMBENT_HASH in state, "the captured state must hold the incumbent"
    return database.insert_frontrunner_proposal(
        symphony_id=_FR_INCUMBENT_HASH,
        proposal_source="frontrunner_builder",
        candidate_tree=json.loads(_REAL_INCUMBENT_TREE.read_text(encoding="utf-8")),
        metrics_json=dict(_FR_METRICS),
    )


def seed_candidate_alert_survivor() -> int:
    """One weekly-suggestion survivor row, so the header badge has a count to show."""
    return database.insert_advisor_observation(
        advisor_role="ASSET_SWAP",
        subject_type="shell_test_subject",
        subject_id="shell-survivor",
        verdict="ADOPT_CANDIDATE",
        raw_response={},
        symphony_id="shell-symphony",
    )


# ---------------------------------------------------------------------------
# Browser probes
# ---------------------------------------------------------------------------


def _dark_theme_init_script() -> str:
    # tweaks.js merges stored values over its defaults, so the theme alone is a complete value.
    return "if (!localStorage.getItem('studioTweaks')) { localStorage.setItem('studioTweaks', JSON.stringify({theme: 'dark'})); }"


@contextmanager
def open_probe(
    firefox, server, *, width: int, height: int, theme: str | None = None, allow: tuple = ()
):
    """A fresh browser context at ``width`` x ``height``; outbound requests refused.

    ``theme="dark"`` stores the dark tweak value before any page script runs. ``allow`` lists
    URL prefixes besides the server that may pass (none by default).
    """
    context = firefox.new_context(viewport={"width": width, "height": height})
    page = context.new_page()
    page.set_default_timeout(PAGE_TIMEOUT_MS)
    state = SimpleNamespace(
        page=page,
        server=server,
        context=context,
        console_errors=[],
        page_errors=[],
        refused=[],
        dialogs=[],
    )

    def route(request_route):
        url = request_route.request.url
        if url.startswith(server.base_url) or any(url.startswith(p) for p in allow):
            request_route.continue_()
        else:
            state.refused.append(url)
            request_route.abort()

    page.route("**/*", route)
    page.on("pageerror", lambda exc: state.page_errors.append(str(exc)))

    def on_dialog(dialog):
        state.dialogs.append(dialog.message)
        dialog.accept()

    page.on("dialog", on_dialog)
    if theme == "dark":
        context.add_init_script(_dark_theme_init_script())
    try:
        yield state
    finally:
        context.close()


def open_page(probe, path: str, *, fragment: str = "") -> None:
    """Load ``path`` and wait for what that page's own scripts have drawn (never network idle)."""
    probe.page.goto(probe.server.base_url + path + fragment)
    if path == "/":
        probe.page.wait_for_selector(READY_SELECTOR, timeout=READY_TIMEOUT_MS)
    elif path == "/history":
        wait_until(
            probe,
            "() => { const b = document.querySelector('#triggers-tbody');"
            " return !!b && !/Loading/.test(b.textContent); }",
            "History never replaced its Loading row",
        )
    elif path == "/performance":
        wait_until(
            probe,
            "() => { const c = document.querySelector('#obs-caption');"
            " return !!c && c.textContent.trim() !== '--'; }",
            "Performance never filled its observation caption",
        )
    elif path == "/settings":
        wait_until(
            probe,
            "() => document.querySelector('.param-input') !== null",
            "Settings never rendered a parameter input",
        )
    elif path == "/ai-advisor":
        wait_until(
            probe,
            "() => document.querySelectorAll('[data-tab]').length > 0",
            "the Advisor page never rendered its tab bar",
        )
    else:
        raise AssertionError(f"no readiness rule for {path}")


def measure(probe, selector: str) -> dict:
    """Position facts for the first element matching ``selector`` (see the JS above)."""
    return probe.page.evaluate(_INSIDE_VIEWPORT_AND_HIT_TESTABLE, selector)


def assert_reachable(probe, label: str, selector: str, context: str = "") -> None:
    """The control is displayed, inside the viewport horizontally, and not covered or clipped."""
    facts = measure(probe, selector)
    where = f"{label} on {context}".strip()
    assert facts["found"], f"{where}: no element matches {selector}"
    assert facts["displayed"], f"{where}: not displayed"
    assert facts["width"] > 0 and facts["height"] > 0, f"{where}: has no size"
    assert facts["left"] >= -0.5, f"{where}: starts left of the viewport ({facts['left']})"
    assert facts["right"] <= facts["viewport"] + 0.5, (
        f"{where}: ends right of the viewport ({facts['right']} > {facts['viewport']})"
    )
    assert facts["hit"], f"{where}: another element covers it, so it cannot be clicked"


def document_scroll_width(probe) -> tuple[int, int]:
    """``(scrollWidth, viewport width)`` of the document."""
    return tuple(probe.page.evaluate("() => [document.documentElement.scrollWidth, innerWidth]"))


def nav_height(probe) -> float:
    return probe.page.evaluate(
        "() => document.querySelector('[data-testid=\"studio-nav\"]').getBoundingClientRect().height"
    )


def nav_rows(probe) -> int:
    """How many rows the nav's direct children and link anchors occupy (distinct tops)."""
    return probe.page.evaluate(
        """() => {
          const nav = document.querySelector('[data-testid="studio-nav"]');
          const items = [...nav.querySelectorAll(':scope > *, :scope > div > a')]
            .filter((e) => { const s = getComputedStyle(e); const r = e.getBoundingClientRect();
                             return s.display !== 'none' && r.width > 0 && r.height > 0; });
          const rows = [];
          items.forEach((e) => {
            const r = e.getBoundingClientRect();
            const mid = r.top + r.height / 2;
            if (!rows.some((m) => Math.abs(m - mid) < r.height / 2)) rows.push(mid);
          });
          return rows.length;
        }"""
    )


def set_account_in_env(env_path: str, account_ids: list[str]) -> None:
    keys = ["ACCOUNT_INDIVIDUAL", "ACCOUNT_ROTH", "ACCOUNT_TRAD"]
    lines = [f"{k}={v}" for k, v in zip(keys, account_ids, strict=False)]
    pathlib.Path(env_path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def seed_every_page(tmp_path: pathlib.Path, setattr_fn=setattr) -> None:
    """The state all five pages are read over: cards, exits, a pending proposal, one survivor."""
    seed_history_exits(tmp_path, setattr_fn)
    seed_pending_frontrunner_proposal()
    seed_candidate_alert_survivor()


_FULLY_VISIBLE_FACTS = """
(el) => {
  const r = el.getBoundingClientRect();
  const cx = Math.min(Math.max(r.left + r.width / 2, 0), innerWidth - 1);
  const cy = Math.min(Math.max(r.top + r.height / 2, 0), innerHeight - 1);
  const hit = document.elementFromPoint(cx, cy);
  return {
    left: r.left, right: r.right, top: r.top, bottom: r.bottom,
    width: r.width, height: r.height, vw: innerWidth, vh: innerHeight,
    hit: !!hit && (hit === el || el.contains(hit)),
    displayed: getComputedStyle(el).display !== 'none' && getComputedStyle(el).visibility !== 'hidden',
  };
}
"""


def assert_fully_visible(locator, label: str) -> None:
    """The element is displayed and sits inside the viewport on both axes, uncovered."""
    facts = locator.evaluate(_FULLY_VISIBLE_FACTS)
    assert facts["displayed"] and facts["width"] > 0 and facts["height"] > 0, f"{label}: not shown"
    assert facts["left"] >= -0.5 and facts["right"] <= facts["vw"] + 0.5, (
        f"{label}: outside the viewport horizontally ({facts['left']}..{facts['right']} of "
        f"{facts['vw']})"
    )
    assert facts["top"] >= -0.5 and facts["bottom"] <= facts["vh"] + 0.5, (
        f"{label}: outside the viewport vertically ({facts['top']}..{facts['bottom']} of "
        f"{facts['vh']})"
    )
    assert facts["hit"], f"{label}: covered or clipped, so it cannot be clicked"


def assert_not_clipped_by_its_containers(locator, label: str) -> None:
    """No ancestor that clips horizontally (overflow hidden, auto or scroll) cuts the element."""
    cut = locator.evaluate(
        """
        (el) => {
          const r = el.getBoundingClientRect();
          const problems = [];
          for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
            const ox = getComputedStyle(a).overflowX;
            if (ox === 'visible') continue;
            const b = a.getBoundingClientRect();
            if (r.right > b.right + 0.5 || r.left < b.left - 0.5) {
              problems.push([a.tagName + '.' + String(a.className).slice(0, 30), ox,
                             Math.round(r.right - b.right), Math.round(b.left - r.left)]);
            }
          }
          return problems;
        }
        """
    )
    assert cut == [], f"{label}: clipped by {cut}"


SETTINGS_EDIT_VALUE = "7.5"


def settings_edit_and_save(probe, *, scroll_to_bottom: bool = False) -> dict:
    """Edit the first algorithm parameter, optionally scroll to the bottom, click Save.

    The Save request is intercepted in the browser and answered there (nothing is written); the
    page's own reload GET passes through. Returns the request the page sent and the state of
    Save and Discard after the edit (and after the scroll back to the top).
    """
    page = probe.page
    page.click('.sn-btn[data-section="algorithm"]')
    page.wait_for_selector("#algo-grid .param-input", state="visible")
    page.locator("#algo-grid .param-input").first.fill(SETTINGS_EDIT_VALUE)
    facts = {"save_enabled_after_edit": page.is_enabled("#btn-save")}
    if scroll_to_bottom:
        page.evaluate(
            "() => { const c = document.querySelector('.settings-content');"
            " if (c) c.scrollTop = c.scrollHeight;"
            " window.scrollTo(0, document.documentElement.scrollHeight); }"
        )
        facts["save_enabled_at_bottom"] = page.is_enabled("#btn-save")
        facts["discard_enabled_at_bottom"] = page.is_enabled("#btn-discard")
        page.evaluate("() => window.scrollTo(0, 0)")
        page.wait_for_timeout(200)
    captured: list = []

    def handler(route):
        if route.request.method == "POST":
            captured.append(route.request)
            route.fulfill(status=200, content_type="application/json", body='{"status":"success"}')
        else:
            route.continue_()

    page.route("**/api/settings", handler)
    save = page.locator("#btn-save")
    if scroll_to_bottom:
        assert_fully_visible(save, "Save changes after scrolling back up")
        assert_fully_visible(
            page.locator("#btn-discard"), "Discard changes after scrolling back up"
        )
    save.click()
    page.wait_for_timeout(600)
    assert len(captured) == 1, f"Save sent {len(captured)} requests"
    request = captured[0]
    facts["request"] = {
        "method": request.method,
        "content_type": (request.headers.get("content-type") or "").split(";")[0],
        "has_csrf_header": bool(request.headers.get("x-csrf-token")),
        "body": json.loads(request.post_data),
    }
    return facts


@pytest.fixture(autouse=True)
def _empty_post_mortem_directory(tmp_path, monkeypatch):
    """History and Performance read post-mortem files; start every browser test with none.

    The default directory is a path beside the code, which a developer checkout may fill with
    real days. A test that wants days calls ``seed_history_exits`` after this runs.
    """
    import analytics

    empty = tmp_path / "no_post_mortems"
    empty.mkdir()
    monkeypatch.setattr(analytics, "_POST_MORTEMS_DIR", str(empty))
