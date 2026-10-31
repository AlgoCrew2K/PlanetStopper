"""A picked hero window never sits beside another window's figures.

Real call path: the whole of ``static/index.js`` runs under node (tests/dashboard/_index_js_harness.py)
over the page ``GET /`` renders, with the bodies the real ``/api/state`` and ``/api/strip/<window>``
routes return for a seeded database. A pick is a click on the real picker button; the strip answer
is held, refused, lost or malformed by a route spec, as the scenario needs.

Contract pinned
* From the click until ``/api/strip/<window>`` answers, the headline, the Max DD row and the Ann. Vol
  row show no figure (a dash, or a hidden row); none of the previous window's digits stays on screen.
* When the strip request fails (a refusal, a lost connection, an answer that is not a strip), the
  three stay without a figure and a visible notice names the window that could not be loaded.
* A later pick, or the next good answer for the picked window, clears the notice and writes that
  window's own figures.
* An answer that is a genuine "no figure for this window" (a null guard effect) is a good answer: a
  dash and no failure notice.
* The deltas beside the headline's ``pp`` print ``pp`` too, never ``%``: the Today, Cumulative and
  Max DD rows on the first paint, after a poll and after a pick.

Derived bodies change one named field of a real body: tests/fixtures/refresh_timing/03-c1/PROVENANCE.md.
"""

from __future__ import annotations

import re

import pytest

from tests.dashboard import _index_js_harness as hx
from tests.dashboard import _refresh_timing_support as sup

WORLD = "default"
DIGIT = re.compile(r"\d")
DASHES = ("--", sup.DASH)

# What the page can see: the text of every element no `hidden` attribute covers.
SNAPSHOT = r"""
const visible = function (node) {
  if (node.nodeType === 3) return node.data;
  if (node.nodeType !== 1) return '';
  if (node.hasAttribute('hidden')) return '';
  if (/^(SCRIPT|STYLE|TEMPLATE)$/.test(node.tagName)) return '';
  return node.childNodes.map(visible).join(' ');
};
const shown = function (el) {
  for (let e = el; e && e.nodeType === 1; e = e.parentNode) { if (e.hasAttribute('hidden')) return false; }
  return true;
};
const D = H.sandbox.document;
const probe = function (sel) {
  const el = D.querySelector(sel);
  return el ? { exists: true, shown: shown(el), text: el.textContent.trim() } : { exists: false };
};
// A visible element, short enough to be a notice and not a container, that names the window and
// says it could not be loaded.
const noticeFor = function (name) {
  const said = /could not|couldn.t|can.t be|cannot|unable to|failed to|not loaded|unavailable/i;
  const named = new RegExp('(^|[^a-z0-9])' + name + '([^a-z0-9]|$)', 'i');
  let found = null;
  (function walk(node) {
    if (found || node.nodeType !== 1 || node.hasAttribute('hidden')) return;
    if (/^(SCRIPT|STYLE|TEMPLATE)$/.test(node.tagName)) return;
    const text = visible(node).replace(/\s+/g, ' ').trim();
    if (text.length > 0 && text.length <= 200 && said.test(text) && named.test(text)) { found = text; return; }
    node.childNodes.forEach(walk);
  })(D.body);
  return found;
};
const snap = function (name) {
  return {
    headline: probe('#guard-alpha-headline'),
    label: probe('#guard-alpha-window-label'),
    mddBot: probe('[data-testid="comp-mdd-bot-text"]'),
    mddHeld: probe('[data-testid="comp-mdd-held-text"]'),
    mddDelta: probe('[data-testid="comp-mdd-delta"]'),
    volBot: probe('[data-testid="comp-vol-bot-text"]'),
    volHeld: probe('[data-testid="comp-vol-held-text"]'),
    volDelta: probe('[data-testid="comp-vol-delta"]'),
    notice: noticeFor(name),
    anyNotice: noticeFor('(30d|60d|90d|125d|ytd|1y|all time)'),
  };
};
"""

FIGURE_ELEMENTS = ("mddBot", "mddHeld", "mddDelta", "volBot", "volHeld", "volDelta")


def _world() -> dict:
    return hx.get_world(WORLD)


def _strip(token: str) -> dict:
    return _world()["strips"][token]


def _bodies() -> dict:
    world = _world()
    stamp = sup.render_stamp(world)
    return {
        "pollNew": sup.with_stamp(world["next"]["state"], sup.later_stamp(stamp)),
        "pollEqual": sup.with_stamp(world["state"], stamp),
    }


def _run(scenario: str, *, extra: dict | None = None):
    bodies = _bodies()
    bodies.update(extra or {})
    return sup.run(SNAPSHOT + scenario, world=WORLD, scripts=sup.INDEX_ONLY, extra_bodies=bodies)


def assert_without_figures(snapshot: dict, where: str) -> None:
    """The headline reads a dash; every other figure element is hidden or holds no digit."""
    headline = snapshot["headline"]["text"]
    assert not DIGIT.search(headline) and any(d in headline for d in DASHES), (
        f"{where}: the headline reads {headline!r}, not a dash"
    )
    for name in FIGURE_ELEMENTS:
        element = snapshot[name]
        if element["exists"] and element["shown"]:
            assert not DIGIT.search(element["text"]), (
                f"{where}: {name} still shows {element['text']!r}"
            )


def assert_with_figures_of(snapshot: dict, token: str, label: str, where: str) -> None:
    strip = _strip(token)
    assert snapshot["headline"]["text"] == sup.fmt_pp(strip["guard_alpha"]), where
    assert snapshot["label"]["text"] == label, where
    assert snapshot["mddBot"]["text"] == sup.mdd_texts(strip)["bot"], where
    assert snapshot["volBot"]["text"] == sup.vol_texts(strip)["bot"], where


PICK_1Y = "await H.click('[data-testid=\"window-1y\"]');"


class TestUntilTheStripAnswers:
    def test_preconditions_the_two_windows_differ_on_every_figure_the_pick_replaces(self):
        thirty, year = _strip("30d"), _strip("1y")
        assert sup.fmt_pp(thirty["guard_alpha"]) != sup.fmt_pp(year["guard_alpha"])
        assert sup.mdd_texts(thirty)["bot"] != sup.mdd_texts(year)["bot"]
        assert sup.vol_texts(thirty)["bot"] != sup.vol_texts(year)["bot"]

    def test_a_pick_waiting_for_its_strip_shows_no_figure_of_the_previous_window(self):
        scenario = (
            r"""
        await H.boot({ extras: true });
        const before = snap('1y');
        H.route(/^\/api\/strip\/1y$/, { pending: true });
        """
            + PICK_1Y
            + r"""
        const during = snap('1y');
        await H.releasePending('/api/strip/1y', { body: H.bodies.strips['1y'] });
        return { before: before, during: during, after: snap('1y') };
        """
        )
        r = _run(scenario)
        assert_with_figures_of(r["before"], "30d", "30d", "before the pick")
        assert_without_figures(r["during"], "while the 1y strip is pending")
        assert r["during"]["notice"] is None, "a pending request is not a failure"
        assert_with_figures_of(r["after"], "1y", "1Y", "after the 1y strip answered")

    def test_two_picks_in_a_row_never_leave_the_first_windows_figures_under_the_second(self):
        scenario = (
            r"""
        await H.boot({ extras: true });
        H.route(/^\/api\/strip\/(1y|90d)$/, { pending: true });
        """
            + PICK_1Y
            + r"""
        await H.click('[data-testid="window-90d"]');
        const during = snap('90d');
        await H.releasePending('/api/strip/1y', { body: H.bodies.strips['1y'] });
        const afterStale = snap('90d');
        await H.releasePending('/api/strip/90d', { body: H.bodies.strips['90d'] });
        return { during: during, afterStale: afterStale, after: snap('90d') };
        """
        )
        r = _run(scenario)
        assert_without_figures(r["during"], "90d pending after a 1y pick")
        assert_without_figures(r["afterStale"], "the 1y answer landed after the 90d pick")
        assert_with_figures_of(r["after"], "90d", "90d", "after the 90d strip answered")


class TestWhenTheStripCannotBeLoaded:
    @pytest.mark.parametrize(
        "spec",
        [
            "{ status: 500, body: { status: 'error', message: 'An internal error occurred' } }",
            "{ reject: true }",
            "{ status: 200, body: { error: 'no strip' } }",
        ],
        ids=["server error", "connection lost", "answer that is not a strip"],
    )
    def test_the_figures_stay_dashed_and_a_notice_names_the_window(self, spec):
        scenario = (
            r"""
        await H.boot({ extras: true });
        H.route(/^\/api\/strip\/1y$/, __SPEC__);
        """.replace("__SPEC__", spec)
            + PICK_1Y
            + r"""
        const failed = snap('1y');
        await H.runTimers(100);
        return { failed: failed, later: snap('1y') };
        """
        )
        r = _run(scenario)
        assert_without_figures(r["failed"], "after the 1y strip failed")
        assert r["failed"]["notice"], "no visible notice says the 1y window could not be loaded"
        assert_without_figures(r["later"], "a moment after the failure")
        assert r["later"]["notice"], "the notice went away with nothing loaded"

    def test_a_later_pick_clears_the_notice_and_writes_that_windows_own_figures(self):
        scenario = (
            r"""
        await H.boot({ extras: true });
        H.route(/^\/api\/strip\/1y$/, { status: 500, body: { status: 'error', message: 'An internal error occurred' } });
        """
            + PICK_1Y
            + r"""
        const failed = snap('1y');
        await H.click('[data-testid="window-30d"]');
        return { failed: failed, after: snap('1y') };
        """
        )
        r = _run(scenario)
        assert r["failed"]["notice"], "precondition: the failed pick showed its notice"
        assert r["after"]["anyNotice"] is None, f"the notice outlived the pick: {r['after']}"
        assert_with_figures_of(r["after"], "30d", "30d", "after picking 30d")

    def test_the_next_good_answer_for_the_picked_window_clears_the_notice(self):
        scenario = (
            r"""
        await H.boot({ extras: true, state: H.bodies.pollEqual });
        H.route(/^\/api\/strip\/1y$/, { status: 500, body: { status: 'error', message: 'An internal error occurred' } });
        """
            + PICK_1Y
            + r"""
        const failed = snap('1y');
        H.route(/^\/api\/strip\/1y$/, { body: H.bodies.strips['1y'] });
        H.route('/api/state', { body: H.bodies.pollNew });
        await H.runTimers(30000);
        return { failed: failed, after: snap('1y') };
        """
        )
        r = _run(scenario)
        assert r["failed"]["notice"], "precondition: the failed pick showed its notice"
        assert r["after"]["anyNotice"] is None, f"the notice outlived the good answer: {r['after']}"
        assert_with_figures_of(r["after"], "1y", "1Y", "after the refresh answered")

    def test_an_answer_with_no_guard_effect_is_a_dash_without_a_failure_notice(self):
        year = _strip("1y")
        empty = sup.strip_with(year, guard_alpha=None)
        scenario = (
            r"""
        await H.boot({ extras: true });
        H.route(/^\/api\/strip\/1y$/, { body: H.bodies.emptyYear });
        """
            + PICK_1Y
            + r"""
        return { shown: snap('1y') };
        """
        )
        r = _run(scenario, extra={"emptyYear": empty})
        headline = r["shown"]["headline"]["text"]
        assert headline == sup.fmt_pp(None), f"a null guard effect reads {headline!r}"
        assert r["shown"]["label"]["text"] == "1Y"
        assert r["shown"]["anyNotice"] is None, (
            "a good answer without a figure was called a failure"
        )


# ---------------------------------------------------------------------------
# The deltas beside the pp headline
# ---------------------------------------------------------------------------

DELTA_ROWS = ("today", "cumulative", "mdd")


def _delta_texts_scenario(after: str) -> str:
    return (
        r"""
    await H.boot({ extras: true });
    const rows = function () {
      const out = {};
      ['today', 'cumulative', 'mdd'].forEach(function (id) { out[id] = probe('[data-testid="comp-' + id + '-delta"]'); });
      return out;
    };
    const first = rows();
    """
        + after
        + r"""
    return { first: first, last: rows() };
    """
    )


class TestRowDeltasPrintPercentagePoints:
    @pytest.mark.parametrize(
        "after",
        [
            "H.route('/api/state', { body: H.bodies.pollNew }); await H.runTimers(30000);",
            "await H.click('[data-testid=\"window-1y\"]');",
        ],
        ids=["after a poll", "after a pick"],
    )
    def test_no_row_delta_prints_a_percent_sign_and_each_prints_pp(self, after):
        r = _run(_delta_texts_scenario(after))
        for stage in ("first", "last"):
            for row in DELTA_ROWS:
                element = r[stage][row]
                if not element["exists"] or not element["shown"]:
                    continue
                text = element["text"]
                assert "%" not in text, f"{stage}: the {row} delta prints {text!r}"
                if DIGIT.search(text):
                    sup.pp_delta_value(text)

    def test_the_max_dd_delta_is_held_minus_bot_in_percentage_points(self):
        thirty = _strip("30d")["max_drawdown"]
        want = abs(thirty["if_held"]) - abs(thirty["dry_run"])
        r = _run(_delta_texts_scenario("await H.click('[data-testid=\"window-30d\"]');"))
        text = r["last"]["mdd"]["text"]
        got = sup.pp_delta_value(text)
        assert abs(got - want) <= 0.006, f"the delta reads {got}, held minus bot is {want:.4f}"

    def test_the_first_paint_of_the_server_render_prints_no_percent_sign_in_a_row_delta(self):
        html = _world()["html"]
        for row in DELTA_ROWS:
            match = re.search(
                rf'data-testid="comp-{row}-delta"[^>]*>(.*?)</span>', html, flags=re.S
            )
            assert match, f"the render holds no {row} delta"
            text = re.sub(r"<[^>]+>", "", match.group(1)).strip()
            assert "%" not in text, f"the first paint of the {row} delta prints {text!r}"
