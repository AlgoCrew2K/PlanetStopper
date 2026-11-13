"""The dashboard and its detail panel in a real browser: the words are on screen, the layout is dense.

Browser-only (marker ``browser``, deselected by default; run with
``python -m pytest -m browser -n 0 tests/ui/test_page_explainers_dashboard_browser.py``). Firefox
headless through Playwright, imported inside a fixture; a missing Playwright or Firefox fails with
a message naming it, never a skip.

Each case is one seeded world of ``tests/ui/_page_explainers.py`` served by the app in this
process, every request to anything but the server refused, the browser clock fixed at the capture
instant. The expected text of every row comes from ``tests/fixtures/page_explainers/copy.json``
through ``tests/ui/_page_copy.py``; nothing is typed here. What is asserted, on the live render:

* every description row of the dashboard and of the detail panel that Copy table A keeps is visible
  with this plan's exact text (a box with size, nothing hiding it, not inside a closed disclosure),
  at 1440 and at 375, whole and not cut off, and shown once after the page has polled; the page line
  and the Tracked, Armed, Triggered line the plan removes are on no render;
* the dashboard and the detail panel each end with one Glossary: closed on load, named "Glossary",
  opened and closed from the keyboard, showing each line's exact text when opened, and still open
  with its lines unchanged across polls;
* the verdict, the VWAP pill and the held-line legend the script writes after a poll read the
  approved words and agree with the first paint;
* the detail panel has no View logs, Go to cash or Edit variables button, and its close control
  and overlay still work;
* at 1440 and 1240 the CVaR and Managed sleeves panels share one row, and a lone sleeves panel
  takes the full width, before and after a poll and a window pick;
* at 375 the page is as wide as the viewport, no added text is under 11 px, and its height
  returns to the closed height when every disclosure is opened and closed again.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from tests.app.conftest import _reset_scheduler_run_state  # noqa: F401  (autouse, health state)
from tests.dashboard._exit_verdict_copy import verdict_sentence
from tests.ui import _page_copy as copy
from tests.ui import _page_explainers as harness
from tests.ui._shell_support import (  # noqa: F401  (fixtures: overrides, empty directory, browser)
    NARROW,
    WIDE,
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    advisor_server,
    firefox,
    open_page,
    open_probe,
)

pytestmark = pytest.mark.browser

DASHBOARD_DESCRIPTIONS = (
    "dash.hero",
    "dash.rows",
    "D21",
    "D28",
    "D40",
    "D41",
    "D47",
    "D48",
)
DETAIL_DESCRIPTIONS = ("dp.lead", "D57", "dp.events", "D59", "D66", "D67")
REMOVED_DESCRIPTIONS = ("D01", "D20")
GLOSSARY_SUMMARY = "Glossary"
GLOSSARY = 'details[data-terms="glossary"][data-glossary-host="{host}"]'
MIN_TEXT_PX = 11
ROW_TOP_TOLERANCE_PX = 8
POLL_MS = 31_000
PROD_DROPLET = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "prod_droplet"
# Exit return minus held return, in points: beat, trailed, exactly level, and a hair above level.
EXIT_GAPS = (1.24, -0.42, 0.0, 0.04)
CLOSED_HEIGHT_TOLERANCE_PX = 1

VISIBILITY_JS = """
(selector) => {
  const el = document.querySelector(selector);
  if (!el) return { found: false };
  const box = el.getBoundingClientRect();
  let hiddenBy = null;
  for (let node = el; node && node !== document.documentElement; node = node.parentElement) {
    const style = getComputedStyle(node);
    if (node.hidden || style.display === 'none' || style.visibility === 'hidden') {
      hiddenBy = node.tagName + '#' + node.id + '.' + String(node.className).slice(0, 30);
      break;
    }
    if (node.tagName === 'DETAILS' && !node.open) {
      const summary = [...node.children].find((c) => c.tagName === 'SUMMARY');
      if (!(summary && summary.contains(el))) { hiddenBy = 'a closed disclosure'; break; }
    }
  }
  const style = getComputedStyle(el);
  return {
    found: true,
    width: box.width,
    height: box.height,
    hiddenBy,
    text: (el.textContent || '').replace(/\\s+/g, ' ').trim(),
    fontSize: parseFloat(style.fontSize),
    cutOff: el.scrollWidth > el.clientWidth + 1,
    ellipsis: style.textOverflow === 'ellipsis' && el.scrollWidth > el.clientWidth,
  };
}
"""

GEOMETRY_JS = """
() => {
  const read = (selector) => {
    const el = document.querySelector(selector);
    if (!el || el.getClientRects().length === 0) return null;
    const r = el.getBoundingClientRect();
    return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width };
  };
  return {
    cvar: read('#cvar-diagnostic-panel'),
    sleeves: read('[data-testid="sleeves-panel"]'),
    hero: read('[data-testid="hero-section"]'),
    scroll_width: document.documentElement.scrollWidth,
    viewport: innerWidth,
  };
}
"""

DISCLOSURE_STATE_JS = """
() => [...document.querySelectorAll('[data-terms]')].map((d) => ({
  key: d.getAttribute('data-terms'), host: d.getAttribute('data-glossary-host'), open: d.open,
  terms: [...d.querySelectorAll('p[data-term]')].map((p) => [p.getAttribute('data-term'),
          (p.textContent || '').replace(/\\s+/g, ' ').trim()]),
}))
"""


def _plain(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _slots() -> dict[str, str]:
    import autotuner

    return {"shadow_retention_days": "180", "friction_pct": _plain(autotuner.SIM_EXIT_FRICTION_PCT)}


def _expected_lines(host: str) -> list[tuple[str, str]]:
    """The host's Glossary lines in Table G's order, as (key, text) with the slots filled."""
    slots = _slots()
    return [(line["key"], copy.fill(line["text"], **slots)) for line in copy.glossary(host)]


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    harness.credential_free_environment(
        tmp_path / "page.env", monkeypatch.setattr, lambda n: monkeypatch.delenv(n, raising=False)
    )
    return lambda world: harness.seed_world(world, tmp_path, monkeypatch.setattr)


class _Dashboard:
    """A probe on ``/`` with the browser clock fixed, after the page's first draw settled."""

    def __init__(self, firefox, server, width: int, height: int = 900):  # noqa: F811
        self._firefox, self._server, self._size = firefox, server, (width, height)

    def __enter__(self):
        self._cm = open_probe(
            self._firefox, self._server, width=self._size[0], height=self._size[1]
        )
        probe = self._cm.__enter__()
        probe.context.clock.install(time=harness.CAPTURE_INSTANT)
        open_page(probe, "/")
        probe.page.wait_for_timeout(harness.SETTLE_MS)
        return probe

    def __exit__(self, *exc):
        return self._cm.__exit__(*exc)


def _see(probe, selector: str) -> dict:
    return probe.page.evaluate(VISIBILITY_JS, selector)


def _assert_visible_text(probe, key: str, selector: str, expected: str) -> None:
    facts = _see(probe, selector)
    assert facts["found"], f"{key}: no element matches {selector}"
    assert facts["hiddenBy"] is None, f"{key}: hidden by {facts['hiddenBy']}"
    assert facts["width"] > 0 and facts["height"] > 0, f"{key}: has no box"
    assert facts["text"] == expected, f"{key}: shows {facts['text']!r}, not {expected!r}"


def _open_first_card(probe) -> None:
    probe.page.locator('[data-testid="sym-card"] .card-name').first.click()
    probe.page.wait_for_selector("#detail-panel.open")
    probe.page.wait_for_timeout(600)


# ---------------------------------------------------------------------------
# Descriptions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", [WIDE[0], NARROW[0]])
def test_every_dashboard_description_is_visible_with_its_exact_text(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,
    width,
):
    seeded("dashboard_full")
    with _Dashboard(firefox, advisor_server, width) as probe:
        for key in DASHBOARD_DESCRIPTIONS:
            _assert_visible_text(probe, key, f'[data-explainer="{key}"]', copy.a_text(key))


@pytest.mark.parametrize("width", [WIDE[0], NARROW[0]])
def test_every_detail_panel_description_is_visible_once_a_card_is_opened(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,
    width,
):
    seeded("dashboard_full")
    with _Dashboard(firefox, advisor_server, width) as probe:
        _open_first_card(probe)
        for key in DETAIL_DESCRIPTIONS:
            _assert_visible_text(
                probe,
                key,
                f'[data-testid="detail-panel"] [data-explainer="{key}"]',
                copy.a_text(key),
            )


@pytest.mark.parametrize("width", [WIDE[0], NARROW[0]])
def test_the_two_removed_lines_are_on_no_render_of_the_page_or_the_panel(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,
    width,
):
    seeded("dashboard_full")
    with _Dashboard(firefox, advisor_server, width) as probe:
        _open_first_card(probe)
        found = {
            key: probe.page.evaluate(
                "(k) => document.querySelectorAll('[data-explainer=\"' + k + '\"]').length", key
            )
            for key in REMOVED_DESCRIPTIONS
        }
        body = probe.page.evaluate("() => document.body.innerText.replace(/\\s+/g, ' ')")
    assert found == {key: 0 for key in REMOVED_DESCRIPTIONS}
    for key in REMOVED_DESCRIPTIONS:
        assert " ".join(copy.text(key, "A").split()) not in body, f"the old text of {key} shows"


def test_a_description_is_whole_on_a_phone_and_never_ellipsed_or_tiny(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,
):
    seeded("dashboard_full")
    with _Dashboard(firefox, advisor_server, NARROW[0], NARROW[1]) as probe:
        _open_first_card(probe)
        problems = []
        for key in (*DASHBOARD_DESCRIPTIONS, *DETAIL_DESCRIPTIONS):
            facts = _see(probe, f'[data-explainer="{key}"]')
            if not facts["found"] or facts["hiddenBy"]:
                problems.append(f"{key}: not shown ({facts.get('hiddenBy')})")
            elif facts["cutOff"] or facts["ellipsis"]:
                problems.append(f"{key}: cut off")
            elif facts["fontSize"] < MIN_TEXT_PX:
                problems.append(f"{key}: {facts['fontSize']} px")
        geometry = probe.page.evaluate(GEOMETRY_JS)
    assert not problems, "\n".join(problems)
    assert geometry["scroll_width"] == geometry["viewport"], "the page scrolls sideways at 375"


def test_each_description_is_on_the_page_once_after_the_page_has_polled_three_times(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,
):
    seeded("dashboard_full")
    with _Dashboard(firefox, advisor_server, WIDE[0]) as probe:
        for _ in range(3):
            probe.page.clock.run_for(POLL_MS)
            probe.page.wait_for_timeout(500)
        counts = probe.page.evaluate(
            "(keys) => keys.map((k) => document.querySelectorAll('[data-explainer=\"' + k + '\"]').length)",
            list(DASHBOARD_DESCRIPTIONS),
        )
        glossaries = probe.page.evaluate("() => document.querySelectorAll('[data-terms]').length")
        for key in DASHBOARD_DESCRIPTIONS:
            _assert_visible_text(probe, key, f'[data-explainer="{key}"]', copy.a_text(key))
    assert counts == [1] * len(DASHBOARD_DESCRIPTIONS)
    assert glossaries == 2, "the page and the detail panel each hold one Glossary and nothing else"


# ---------------------------------------------------------------------------
# The Glossaries
# ---------------------------------------------------------------------------


def _check_glossary(probe, host: str) -> None:
    selector = GLOSSARY.format(host=host)
    summary = _see(probe, f"{selector} > summary")
    assert summary["hiddenBy"] is None and summary["text"] == GLOSSARY_SUMMARY, host
    first = copy.glossary(host)[0]["key"]
    assert _see(probe, f'[data-term="{first}"]')["hiddenBy"] == "a closed disclosure", host
    probe.page.locator(f"{selector} > summary").click()
    for key, text in _expected_lines(host):
        _assert_visible_text(probe, key, f'[data-term="{key}"]', text)
    probe.page.locator(f"{selector} > summary").click()
    assert _see(probe, f'[data-term="{first}"]')["hiddenBy"] == "a closed disclosure", host


def test_each_glossary_is_closed_on_load_named_and_shows_each_line_when_opened(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,
):
    seeded("dashboard_full")
    with _Dashboard(firefox, advisor_server, WIDE[0]) as probe:
        state = probe.page.evaluate(DISCLOSURE_STATE_JS)
        assert sorted(d["host"] for d in state) == ["/", "detail panel"], state
        assert all(d["key"] == "glossary" for d in state), "a disclosure that is not a Glossary"
        assert not any(d["open"] for d in state), "a Glossary is open on load"
        # The detail panel's scrim covers the page while it is open, so the page's own Glossary is
        # read first, with the panel closed, and the panel's after a card is opened.
        _check_glossary(probe, "/")
        _open_first_card(probe)
        _check_glossary(probe, "detail panel")


@pytest.mark.parametrize("host", ["/", "detail panel"])
def test_a_glossary_opens_and_closes_from_the_keyboard(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,
    host,
):
    seeded("dashboard_full")
    selector = GLOSSARY.format(host=host)
    with _Dashboard(firefox, advisor_server, WIDE[0]) as probe:
        if host == "detail panel":
            _open_first_card(probe)
        summary = probe.page.locator(f"{selector} > summary")
        name = probe.page.evaluate(
            "(s) => document.querySelector(s).querySelector('summary').textContent.trim()", selector
        )
        assert name == GLOSSARY_SUMMARY
        for key in ("Enter", " "):
            summary.focus()
            probe.page.keyboard.press(key)
            assert probe.page.evaluate("(s) => document.querySelector(s).open", selector), (
                f"{key!r} did not open the Glossary"
            )
            probe.page.keyboard.press(key)
            assert not probe.page.evaluate("(s) => document.querySelector(s).open", selector), (
                f"{key!r} did not close it"
            )


def test_an_open_glossary_stays_open_with_its_lines_unchanged_across_polls_and_a_panel_refresh(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,
):
    seeded("dashboard_full")
    with _Dashboard(firefox, advisor_server, WIDE[0]) as probe:
        probe.page.locator(f"{GLOSSARY.format(host='/')} > summary").click()
        before = probe.page.evaluate(DISCLOSURE_STATE_JS)
        for _ in range(3):
            probe.page.clock.run_for(POLL_MS)
            probe.page.wait_for_timeout(500)
        after = probe.page.evaluate(DISCLOSURE_STATE_JS)
        _open_first_card(probe)
        probe.page.clock.run_for(POLL_MS)
        probe.page.wait_for_timeout(500)
        panel = probe.page.evaluate(DISCLOSURE_STATE_JS)
    assert [d for d in after if d["host"] == "/"][0]["open"] is True
    assert {d["host"]: d["terms"] for d in before} == {d["host"]: d["terms"] for d in after}
    assert [d["host"] for d in before if d["open"]] == [d["host"] for d in after if d["open"]]
    assert {d["host"]: d["terms"] for d in panel} == {d["host"]: d["terms"] for d in before}
    assert [d for d in panel if d["host"] == "/"][0]["open"] is True


# ---------------------------------------------------------------------------
# What the script writes after a poll
# ---------------------------------------------------------------------------


def _seed_exit_rows() -> None:
    """One exit row per triggered symphony, the gap to its held return one of each kind.

    The gaps are above zero, below zero, exactly zero and a hair above zero, so the page must print
    all three sentences. The rows go through the real writer, over a captured exit row's fields.
    """
    import database

    state = database.load_state()
    triggered = [k for k, v in state.items() if isinstance(v, dict) and v.get("triggered")]
    assert len(triggered) >= len(EXIT_GAPS), "premise: the captured state holds enough exits"
    captured = json.loads((PROD_DROPLET / "exit_triggers.json").read_text(encoding="utf-8"))
    row = next(r for r in captured if r["triggered_reason"])
    for sid, gap in zip(triggered, EXIT_GAPS, strict=False):
        entry = state[sid]
        written = database.record_exit_trigger(
            symphony_id=sid,
            account_id=entry.get("account") or "",
            triggered_reason=entry.get("triggered_reason") or row["triggered_reason"],
            at_return=entry["current_return"] + gap,
            cycle_id=row["cycle_id"],
            ts_utc=row["ts_utc"],
            ts_et=row["ts_et"],
        )
        assert written is not None, "the real exit-trigger writer refused the row"


def test_the_verdict_the_poll_writes_is_the_sentence_the_first_paint_gave(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,
):
    seeded("dashboard_full")
    _seed_exit_rows()
    with _Dashboard(firefox, advisor_server, WIDE[0]) as probe:
        state = probe.page.evaluate(
            "() => fetch('/api/state').then((r) => r.json()).then((b) => b.bot_state)"
        )
        triggered = {k: v for k, v in state.items() if isinstance(v, dict) and v.get("triggered")}
        assert triggered, "premise: the captured state holds triggered symphonies"
        kinds = set()
        for sid, entry in triggered.items():
            ga = entry.get("guard_alpha")
            facts = _see(probe, f'[data-sym-id="{sid}"] [data-field="verdict-text"]')
            assert facts["found"], sid
            if not isinstance(ga, (int, float)):
                assert facts["text"] == "—", (sid, facts["text"])
                continue
            magnitude = probe.page.evaluate("(v) => Math.abs(v).toFixed(1)", ga)
            assert facts["text"] == verdict_sentence(ga, magnitude), (sid, ga, facts["text"])
            kinds.add("matched" if magnitude == "0.0" else ("beat" if ga > 0 else "trailed"))
    assert kinds == {"beat", "trailed", "matched"}, f"the seeded exits print only {kinds}"


def test_the_pill_and_the_held_line_legend_the_script_writes_read_the_new_words(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,
):
    seeded("dashboard_full")
    with _Dashboard(firefox, advisor_server, WIDE[0]) as probe:
        probe.page.clock.run_for(POLL_MS)
        probe.page.wait_for_timeout(800)
        pills = probe.page.evaluate(
            "() => [...document.querySelectorAll('[data-testid=\"status-pill\"].vwap')]"
            ".map((e) => e.textContent.replace(/\\s+/g, ' ').trim())"
        )
        legend = probe.page.evaluate(
            "() => document.querySelector('[data-field=\"if-held-legend\"]').textContent"
            ".replace(/\\s+/g, ' ').trim()"
        )
        label = probe.page.evaluate(
            "() => document.querySelector('[data-field=\"live-return-label\"]').textContent"
            ".replace(/\\s+/g, ' ').trim()"
        )
    assert pills, "premise: a symphony exited on a VWAP breakdown is on the page"
    assert set(pills) == {copy.text("D29v", "C")}
    open_session = label == "Live return"
    assert legend == (copy.text("NM6", "C") if open_session else "If held")


# ---------------------------------------------------------------------------
# The detail panel's controls
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", [WIDE[0], NARROW[0]])
def test_the_detail_panel_has_no_inert_buttons_and_its_close_control_and_overlay_work(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,
    width,
):
    seeded("dashboard_full")
    with _Dashboard(firefox, advisor_server, width) as probe:
        _open_first_card(probe)
        texts = probe.page.evaluate(
            "() => [...document.querySelectorAll('[data-testid=\"detail-panel\"] button')]"
            ".map((b) => b.textContent.replace(/\\s+/g, ' ').trim())"
        )
        for removed in harness.REMOVED_CONTROL_TEXTS:
            assert not any(t.startswith(removed) for t in texts), f"{removed} is still a button"
        assert probe.page.evaluate(
            "() => document.querySelector('#detail-scrim').classList.contains('open')"
        )
        close = _see(probe, '[data-testid="detail-close"]')
        assert close["hiddenBy"] is None and close["width"] > 0
        probe.page.locator('[data-testid="detail-close"]').click()
        probe.page.wait_for_timeout(300)
        assert not probe.page.evaluate(
            "() => document.querySelector('#detail-panel').classList.contains('open')"
        )
        assert not probe.page.evaluate(
            "() => document.querySelector('#detail-scrim').classList.contains('open')"
        )


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


def _foot_row_problems(geometry: dict, *, expect_cvar: bool) -> list[str]:
    problems = []
    cvar, sleeves, hero = geometry["cvar"], geometry["sleeves"], geometry["hero"]
    assert sleeves is not None and hero is not None, "premise: the sleeves panel and hero render"
    if expect_cvar:
        if cvar is None:
            return ["the CVaR panel is not shown"]
        if abs(cvar["top"] - sleeves["top"]) > ROW_TOP_TOLERANCE_PX:
            problems.append(f"tops differ: CVaR {cvar['top']}, sleeves {sleeves['top']}")
        side_by_side = cvar["right"] <= sleeves["left"] + 1 or sleeves["right"] <= cvar["left"] + 1
        if not side_by_side:
            problems.append(f"the panels overlap or stack: {cvar} {sleeves}")
    else:
        if cvar is not None:
            problems.append("the CVaR panel is shown")
        if sleeves["width"] < 0.95 * hero["width"]:
            problems.append(
                f"the lone sleeves panel is {sleeves['width']} px of {hero['width']} px"
            )
    return problems


@pytest.mark.parametrize(
    ("world", "width", "expect_cvar"),
    [
        ("dashboard_full", 1440, True),
        ("dashboard_full", 1240, True),
        ("dashboard_sleeves", 1440, True),
        ("empty", 1440, False),
    ],
)
def test_the_cvar_and_sleeves_panels_share_one_row_and_a_lone_panel_takes_the_width(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,
    world,
    width,
    expect_cvar,
):
    seeded(world)
    with _Dashboard(firefox, advisor_server, width) as probe:
        geometry = probe.page.evaluate(GEOMETRY_JS)
        _assert_visible_text(probe, "D47", '[data-explainer="D47"]', copy.a_text("D47"))
    problems = _foot_row_problems(geometry, expect_cvar=expect_cvar)
    assert not problems, f"{world} at {width}: " + "; ".join(problems)


def test_the_layout_holds_after_a_poll_and_a_window_pick_and_back(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,
):
    seeded("dashboard_full")
    with _Dashboard(firefox, advisor_server, WIDE[0]) as probe:
        probe.page.clock.run_for(POLL_MS)
        probe.page.wait_for_timeout(800)
        readings = {"after poll": probe.page.evaluate(GEOMETRY_JS)}
        probe.page.locator('[data-testid="window-1y"]').click()
        probe.page.wait_for_timeout(1500)
        readings["on 1y"] = probe.page.evaluate(GEOMETRY_JS)
        probe.page.locator('[data-testid="window-30d"]').click()
        probe.page.wait_for_timeout(1500)
        readings["back on 30d"] = probe.page.evaluate(GEOMETRY_JS)
    problems = []
    for name, geometry in readings.items():
        problems += [f"{name}: {p}" for p in _foot_row_problems(geometry, expect_cvar=True)]
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------------------
# 375 px
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("world", harness.WORLDS)
def test_the_page_is_no_wider_than_the_phone(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,
    world,
):
    seeded(world)
    with _Dashboard(firefox, advisor_server, NARROW[0], NARROW[1]) as probe:
        scroll, viewport = probe.page.evaluate(
            "() => [document.documentElement.scrollWidth, innerWidth]"
        )
    assert scroll == viewport, f"the page scrolls sideways at 375: {scroll} in {viewport}"


def test_every_added_line_is_at_least_eleven_pixels_and_opening_every_disclosure_and_closing_it_restores_the_height(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,
):
    seeded("dashboard_full")
    with _Dashboard(firefox, advisor_server, NARROW[0], NARROW[1]) as probe:
        height = "() => Math.max(document.documentElement.scrollHeight, document.body.scrollHeight)"
        closed = probe.page.evaluate(height)
        added = probe.page.evaluate(
            "() => [document.querySelectorAll('[data-explainer]').length,"
            " document.querySelectorAll('[data-terms]').length]"
        )
        assert min(added) > 0, f"premise: the page carries descriptions and disclosures: {added}"
        small = probe.page.evaluate(
            "() => [...document.querySelectorAll('[data-explainer], [data-terms] > summary')]"
            ".map((e) => [e.getAttribute('data-explainer') || 'summary', parseFloat(getComputedStyle(e).fontSize)])"
            ".filter(([, px]) => px < 11)"
        )
        assert not small, f"added text under {MIN_TEXT_PX} px: {small}"
        probe.page.evaluate(
            "() => document.querySelectorAll('[data-terms]').forEach((d) => { d.open = true; })"
        )
        probe.page.wait_for_timeout(200)
        tiny_terms = probe.page.evaluate(
            "() => [...document.querySelectorAll('[data-term]')]"
            ".map((e) => [e.getAttribute('data-term'), parseFloat(getComputedStyle(e).fontSize)])"
            ".filter(([, px]) => px < 11)"
        )
        assert not tiny_terms, f"term lines under {MIN_TEXT_PX} px: {tiny_terms}"
        probe.page.evaluate(
            "() => document.querySelectorAll('[data-terms]').forEach((d) => { d.open = false; })"
        )
        probe.page.wait_for_timeout(200)
        reclosed = probe.page.evaluate(height)
    assert abs(reclosed - closed) <= CLOSED_HEIGHT_TOLERANCE_PX, (closed, reclosed)
