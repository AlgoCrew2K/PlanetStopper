"""An unknown Max DD figure reads as a dash with no sign colour, and the Risk Profile names its window.

Two findings on the dashboard:

* The Max DD row's Bot and Held spans kept ``vs-val neg`` when the figure was unknown (a dash), so an
  unknown figure read as a bad one; the Today and Cumulative dashes carry no sign colour.
* The detail panel's Risk Profile is fetched for a fixed 60-day window and printed no window, so the
  same symphony showed Held 31.3% on its card and Held 26.0% in its panel with nothing to explain it.

Real call path: the whole of ``static/index.js`` runs under node (tests/dashboard/_index_js_harness.py)
over the page ``GET /`` renders, with the bodies the real routes return: ``/api/strip/<window>`` of the
default world (one named field changed, ``sup.strip_with``), and for the Risk Profile the real
``GET /api/performance?scope=symphony`` of the captured session world (the world and its two instants
come from tests/dashboard/test_closed_market_labels_name_the_session.py).

Contract pinned
* A Max DD span that reads a dash carries neither ``pos`` nor ``neg``: on the first paint with no known
  figure, while a picked window's strip is pending, and after a strip whose legs are unknown.
* A Max DD span that reads a figure keeps the class it had (the fix is not to drop the colour from every
  figure).
* After the panel's Risk Profile loads, the section names the number of days the page asked for (read
  from the request it sent, not typed here) with the word days, and still prints its four rows.
"""

from __future__ import annotations

import re
from html import unescape

import pytest

from tests.dashboard import _index_js_harness as hx
from tests.dashboard import _refresh_timing_support as sup
from tests.dashboard.test_closed_market_labels_name_the_session import Worlds
from tests.dashboard.test_missing_figure_prints_dash import _first_paint

WORLD = "default"
DASHES = ("--", sup.DASH)
MDD = ("comp-mdd-bot-text", "comp-mdd-held-text")

SNAP = r"""
const D = H.sandbox.document;
const spans = function () {
  const out = {};
  ['comp-mdd-bot-text', 'comp-mdd-held-text'].forEach(function (id) {
    const e = D.querySelector('[data-testid="' + id + '"]');
    out[id] = e ? { text: e.textContent.trim(), cls: String(e.className).split(/\s+/) } : null;
  });
  return out;
};
"""


def _is_dash(text: str) -> bool:
    return not re.search(r"\d", text) and any(d in text for d in DASHES)


def _signed(classes: list[str]) -> list[str]:
    return [c for c in classes if c in ("pos", "neg")]


def _run(scenario: str, **bodies) -> dict:
    return sup.run(SNAP + scenario, world=WORLD, extra_bodies=bodies)


class TestAnUnknownMaxDdFigureHasNoSignColour:
    def test_on_the_first_paint_with_no_known_figure(self):
        html = _first_paint(
            today_change={"dry_run": None, "if_held": None},
            cumulative_return={"dry_run": None, "if_held": None},
        )
        for testid in MDD:
            match = re.search(rf'data-testid="{testid}"[^>]*class="([^"]*)"[^>]*>(.*?)</span>', html, re.S)
            assert match, f"the render holds no {testid}"
            text = unescape(re.sub(r"<[^>]+>", "", match.group(2))).strip()
            assert _is_dash(text), f"precondition: {testid} reads {text!r}"
            assert not _signed(match.group(1).split()), (
                f"{testid} reads a dash but carries {match.group(1)!r}"
            )

    def test_while_a_picked_windows_strip_is_pending(self):
        r = _run(
            r"""
        await H.boot({ extras: true });
        H.route(/^\/api\/strip\/1y$/, { pending: true });
        await H.click('[data-testid="window-1y"]');
        return spans();
        """
        )
        for testid in MDD:
            assert _is_dash(r[testid]["text"]), f"precondition: {testid} {r[testid]}"
            assert not _signed(r[testid]["cls"]), f"{testid} dash carries {r[testid]['cls']}"

    def test_after_a_strip_whose_legs_are_unknown(self):
        year = hx.get_world(WORLD)["strips"]["1y"]
        unknown = sup.strip_with(year, max_drawdown={"dry_run": None, "if_held": None})
        r = _run(
            r"""
        await H.boot({ extras: true });
        H.route(/^\/api\/strip\/1y$/, { body: H.bodies.unknown });
        await H.click('[data-testid="window-1y"]');
        return spans();
        """,
            unknown=unknown,
        )
        for testid in MDD:
            assert _is_dash(r[testid]["text"]), f"precondition: {testid} {r[testid]}"
            assert not _signed(r[testid]["cls"]), f"{testid} dash carries {r[testid]['cls']}"

    def test_a_known_figure_keeps_the_class_it_had(self):
        r = _run(
            r"""
        await H.boot({ extras: true });
        const before = spans();
        await H.click('[data-testid="window-1y"]');
        return { before: before, after: spans() };
        """
        )
        for testid in MDD:
            assert not _is_dash(r["after"][testid]["text"]), "precondition: the 1y strip has legs"
            assert _signed(r["after"][testid]["cls"]) == _signed(r["before"][testid]["cls"])


# ---------------------------------------------------------------------------
# The Risk Profile names its window
# ---------------------------------------------------------------------------


@pytest.fixture
def worlds(monkeypatch, tmp_path) -> Worlds:
    return Worlds(monkeypatch, tmp_path)


def test_the_risk_profile_names_the_window_the_page_asked_for(worlds):
    result = hx.run_scenario(
        worlds.closed,
        SNAP
        + r"""
        await H.boot({ extras: true });
        H.route(/^\/api\/performance\?/, { body: H.bodies.perf });
        await H.press('.sym-card[data-sym-id="__ID__"]');
        await H.settle(); await H.settle();
        const rp = D.getElementById('detail-risk-profile');
        const label = rp.parentNode.querySelector('.section-label');
        const shown = (label ? label.textContent : '') + ' ' + rp.innerHTML.replace(/<[^>]+>/g, ' ');
        const asked = H.fetchLog.map(function (e) { return e.url; })
          .filter(function (u) { return /^\/api\/performance\?scope=symphony/.test(u); });
        return { text: shown.replace(/\s+/g, ' ').trim(), asked: asked };
        """.replace("__ID__", worlds.symphony_id),
        scripts=sup.CHROME_AND_INDEX,
        extra_bodies={"perf": worlds.closed_performance},
        now_iso=worlds.closed_now,
    )["result"]
    assert result["asked"], "the panel asked for no risk profile"
    days = re.search(r"[?&]days=(\d+)", result["asked"][0])
    assert days, f"the request names no window: {result['asked'][0]}"
    text = result["text"]
    for label in ("Sharpe", "Sortino", "Max drawdown", "Ann. volatility"):
        assert label in text, f"the Risk Profile lost its {label} row: {text!r}"
    assert re.search(rf"{days.group(1)}[\s-]*(trading[\s-]*)?days?", text, re.I), (
        f"the Risk Profile does not name its {days.group(1)}-day window: {text!r}"
    )
