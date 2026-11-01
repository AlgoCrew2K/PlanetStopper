"""The $-saved panel never sits beside another window's figures, and keeps asking until answered.

Real call path: the whole of ``static/index.js`` runs under node (tests/dashboard/_index_js_harness.py)
over the page ``GET /`` renders. The panel's answers are the real ``/api/guard-alpha-summary`` body of
the seeded world with its totals replaced (``sup.summary_with``), so two windows read differently:
the default 30d window reads a loss, the picked 90d window a gain with its own counts.

Contract pinned
* From the click on a window until that window's summary answers (held), every figure the panel prints
  reads as a dash or nothing: headline, net headline, realized headline, realized net headline, the
  exit count, the realized coverage, the basis label and each saved/lost word. None of the previous
  window's digits or direction word stays on screen under the picked button.
* When the answer is refused, lost, or not a summary, the panel stays without a figure and a visible
  notice inside the panel names the window that could not be loaded.
* A later poll asks again until the picked window has answered, with the event stream open (the
  common case) and with it down; the good answer clears the notice and writes the window's own figures.
* An answer for a window that is no longer the picked one never lands.
* An answer with no guard events is a good answer: no failure notice.
* A request that gets no answer within the page's own state-poll give-up time (read from
  ``STATE_REQUEST_TIMEOUT_MS`` in the script, not typed here) takes the failure path: the notice shows,
  the next poll asks again. Before that time a held pick shows dashes and no notice. A re-ask for the
  window that is still unanswered leaves a showing notice up until an answer lands. A timed-out
  request's late answer changes nothing.
* No element of the panel keeps a colour resolved for the other theme: a dash follows the theme
  (``var(--...)``), and the exit count and realized coverage lose the dim colour once an answer lands.
"""

from __future__ import annotations

import json
import re

import pytest

from tests.dashboard import _index_js_harness as hx
from tests.dashboard import _refresh_timing_support as sup

WORLD = "default"
DIGIT = re.compile(r"\d")

# The page's give-up time for its /api/state poll; the summary request must give up at the same time.
STATE_TIMEOUT_MS = int(
    re.search(r"var STATE_REQUEST_TIMEOUT_MS = (\d+);", hx.INDEX_JS.read_text(encoding="utf-8")).group(1)
)

# Totals chosen by the test (not producer values): the default window reads a loss, 90d a gain.
SAVED_30D, EVENTS_30D, COVERAGE_30D = -113.25, 7, (7, 7)
SAVED_90D, EVENTS_90D, COVERAGE_90D = 241.5, 12, (9, 12)

PANEL_FIELDS = {
    "headline": "dollar-saved-headline",
    "verb": "dollar-saved-verb",
    "count": "guard-event-count",
    "label": "dollar-saved-basis-label",
    "net": "dollar-saved-net-of-friction-headline",
    "netVerb": "dollar-saved-net-of-friction-verb",
    "realized": "dollar-saved-realized-headline",
    "realizedVerb": "dollar-saved-realized-verb",
    "coverage": "dollar-saved-realized-coverage",
    "realizedNet": "dollar-saved-realized-net-of-friction-headline",
    "realizedNetVerb": "dollar-saved-realized-net-of-friction-verb",
}
FIGURE_FIELDS = tuple(PANEL_FIELDS)

SNAPSHOT = r"""
const D = H.sandbox.document;
const FIELDS = __FIELDS__;
const panelNode = function () { return D.querySelector('[data-testid="dollar-saved-panel"]'); };
const visible = function (node) {
  if (node.nodeType === 3) return node.data;
  if (node.nodeType !== 1) return '';
  if (node.hasAttribute('hidden')) return '';
  if (/^(SCRIPT|STYLE|TEMPLATE)$/.test(node.tagName)) return '';
  return node.childNodes.map(visible).join(' ');
};
// A visible element inside the panel, short enough to be a notice and not a container, that says
// the figures could not be loaded and names the window.
const noticeIn = function (name) {
  const said = /could not|couldn.t|can.t be|cannot|unable to|failed to|not loaded|unavailable/i;
  const named = new RegExp('(^|[^a-z0-9])' + name + '([^a-z0-9]|$)', 'i');
  let found = null;
  (function walk(node) {
    if (found || node.nodeType !== 1 || node.hasAttribute('hidden')) return;
    if (/^(SCRIPT|STYLE|TEMPLATE)$/.test(node.tagName)) return;
    const text = visible(node).replace(/\s+/g, ' ').trim();
    if (text.length > 0 && text.length <= 200 && said.test(text) && named.test(text)) { found = text; return; }
    node.childNodes.forEach(walk);
  })(panelNode());
  return found;
};
const snap = function (name) {
  const out = { notice: noticeIn(name) };
  Object.keys(FIELDS).forEach(function (k) { out[k] = H.text('#' + FIELDS[k]); });
  return out;
};
const asked = function (token) { return H.requests(new RegExp('^/api/guard-alpha-summary\\?window=' + token + '$')).length; };
""".replace("__FIELDS__", json.dumps(PANEL_FIELDS))


def _bodies() -> dict:
    world = hx.get_world(WORLD)
    base = world["summaries"]
    return {
        "sum30": sup.summary_with(
            base["30d"], saved=SAVED_30D, events=EVENTS_30D, coverage=COVERAGE_30D
        ),
        "sum90": sup.summary_with(
            base["90d"], saved=SAVED_90D, events=EVENTS_90D, coverage=COVERAGE_90D
        ),
        "sum1y": sup.summary_with(base["1y"], saved=-77.0, events=3, coverage=(3, 3)),
        "empty90": {**base["90d"], "guard_event_count": 0},
        "stateNew": sup.with_stamp(
            world["next"]["state"], sup.later_stamp(sup.render_stamp(world))
        ),
    }


BOOT = r"""
H.failNewStreams = __DOWN__;
await H.boot({ extras: true, overrides: [[/^\/api\/guard-alpha-summary\?window=30d$/, { body: H.bodies.sum30 }]] });
"""
PICK_90D = "await H.click('[data-testid=\"window-90d\"]');"
ROUTE_90D = r"/^\/api\/guard-alpha-summary\?window=90d$/"


def _scenario(body: str, *, stream_down: bool = False) -> str:
    boot = BOOT.replace("__DOWN__", "true" if stream_down else "false")
    return SNAPSHOT + boot + body


def _run(body: str, *, stream_down: bool = False) -> dict:
    return sup.run(_scenario(body, stream_down=stream_down), world=WORLD, extra_bodies=_bodies())


def assert_without_figures(snapshot: dict, where: str) -> None:
    for name in FIGURE_FIELDS:
        text = snapshot[name]
        if text is None:
            continue
        assert not DIGIT.search(text), f"{where}: {name} still reads {text!r}"
    for name in ("verb", "netVerb", "realizedVerb", "realizedNetVerb"):
        assert snapshot[name] in (None, ""), (
            f"{where}: {name} still reads {snapshot[name]!r}, the direction word of a figure "
            "that is not on screen"
        )


def assert_with_figures_of_90d(snapshot: dict, where: str) -> None:
    want = sup.saved_headline_text(SAVED_90D)
    assert snapshot["headline"] == want, where
    assert snapshot["net"] == want, where
    assert snapshot["realized"] == want, where
    assert snapshot["realizedNet"] == want, where
    assert snapshot["verb"] == "saved", where
    assert snapshot["count"] == str(EVENTS_90D), where
    assert snapshot["coverage"] == f"{COVERAGE_90D[0]} of {COVERAGE_90D[1]}", where


class TestUntilThePickedWindowAnswers:
    def test_preconditions_the_two_windows_read_differently_on_every_figure(self):
        bodies = _bodies()
        assert sup.saved_headline_text(SAVED_30D) != sup.saved_headline_text(SAVED_90D)
        assert bodies["sum30"]["guard_event_count"] != bodies["sum90"]["guard_event_count"]

    def test_the_default_window_prints_its_own_figures_before_any_pick(self):
        r = _run("return { first: snap('90d') };")
        assert r["first"]["headline"] == sup.saved_headline_text(SAVED_30D)
        assert r["first"]["verb"] == "lost"

    @pytest.mark.parametrize("stream_down", [False, True], ids=["stream open", "stream down"])
    def test_a_pick_waiting_for_its_summary_shows_no_figure_of_the_previous_window(
        self, stream_down
    ):
        body = (
            "H.route("
            + ROUTE_90D
            + ", { pending: true });\n"
            + PICK_90D
            + r"""
        const during = snap('90d');
        await H.releasePending('/api/guard-alpha-summary?window=90d', { body: H.bodies.sum90 });
        return { during: during, after: snap('90d') };
        """
        )
        r = _run(body, stream_down=stream_down)
        assert_without_figures(r["during"], "while the 90d summary is held")
        assert_with_figures_of_90d(r["after"], "after the 90d summary answered")

    def test_a_held_pick_is_not_called_a_failure(self):
        body = (
            "H.route("
            + ROUTE_90D
            + ", { pending: true });\n"
            + PICK_90D
            + r"""
        return { during: snap('90d') };
        """
        )
        r = _run(body)
        assert r["during"]["notice"] is None, "a request still in flight was reported as failed"

    def test_two_picks_in_a_row_never_leave_the_first_windows_figures_under_the_second(self):
        body = (
            "H.route(/^\\/api\\/guard-alpha-summary\\?window=(1y|90d)$/, { pending: true });\n"
            + "await H.click('[data-testid=\"window-1y\"]');\n"
            + "await H.click('[data-testid=\"window-90d\"]');\n"
            + r"""
        const during = snap('90d');
        await H.releasePending('/api/guard-alpha-summary?window=1y', { body: H.bodies.sum1y });
        const afterStale = snap('90d');
        await H.releasePending('/api/guard-alpha-summary?window=90d', { body: H.bodies.sum90 });
        return { during: during, afterStale: afterStale, after: snap('90d') };
        """
        )
        r = _run(body)
        assert_without_figures(r["during"], "90d held after a 1y pick")
        assert_without_figures(r["afterStale"], "the 1y answer landed after the 90d pick")
        assert_with_figures_of_90d(r["after"], "after the 90d summary answered")


FAILURES = [
    "{ status: 500, body: { status: 'error', message: 'An internal error occurred' } }",
    "{ reject: true }",
    "{ status: 200, body: { error: 'no summary' } }",
    "{ status: 200, badJson: true }",
]
FAILURE_IDS = [
    "server error",
    "connection lost",
    "answer that is not a summary",
    "unparseable answer",
]


class TestWhenTheSummaryCannotBeLoaded:
    @pytest.mark.parametrize("spec", FAILURES, ids=FAILURE_IDS)
    @pytest.mark.parametrize("stream_down", [False, True], ids=["stream open", "stream down"])
    def test_the_figures_stay_dashed_and_a_notice_in_the_panel_names_the_window(
        self, spec, stream_down
    ):
        body = (
            "H.route("
            + ROUTE_90D
            + ", "
            + spec
            + ");\n"
            + PICK_90D
            + r"""
        const failed = snap('90d');
        await H.runTimers(100);
        return { failed: failed, later: snap('90d') };
        """
        )
        r = _run(body, stream_down=stream_down)
        assert_without_figures(r["failed"], "after the 90d summary failed")
        assert r["failed"]["notice"], (
            "no visible notice inside the panel says the 90d figures could not be loaded"
        )
        assert_without_figures(r["later"], "a moment after the failure")
        assert r["later"]["notice"], "the notice went away with nothing loaded"

    @pytest.mark.parametrize("spec", FAILURES, ids=FAILURE_IDS)
    @pytest.mark.parametrize("stream_down", [False, True], ids=["stream open", "stream down"])
    def test_a_later_poll_asks_again_and_the_good_answer_writes_the_windows_own_figures(
        self, spec, stream_down
    ):
        body = (
            "H.route("
            + ROUTE_90D
            + ", "
            + spec
            + ");\n"
            + PICK_90D
            + r"""
        const askedAtFailure = asked('90d');
        H.route('/api/state', { body: H.bodies.stateNew });
        await H.runTimers(30000);
        const askedWhileFailing = asked('90d');
        H.route(/^\/api\/guard-alpha-summary\?window=90d$/, { body: H.bodies.sum90 });
        await H.runTimers(30000);
        return { after: snap('90d'), askedAtFailure: askedAtFailure, askedWhileFailing: askedWhileFailing };
        """
        )
        r = _run(body, stream_down=stream_down)
        assert r["askedWhileFailing"] > r["askedAtFailure"], (
            "a poll after the failure did not ask for the picked window again"
        )
        assert_with_figures_of_90d(r["after"], "after a later poll was answered")
        assert r["after"]["notice"] is None, "the notice outlived the good answer"

    def test_a_pick_of_another_window_clears_the_notice_and_writes_that_windows_figures(self):
        body = (
            "H.route("
            + ROUTE_90D
            + ", { status: 500, body: { status: 'error' } });\n"
            + PICK_90D
            + r"""
        const failed = snap('90d');
        await H.click('[data-testid="window-30d"]');
        return { failed: failed, after: snap('90d'), afterAny: snap('30d') };
        """
        )
        r = _run(body)
        assert r["failed"]["notice"], "precondition: the failed pick showed its notice"
        assert r["after"]["notice"] is None
        assert r["afterAny"]["notice"] is None
        assert r["afterAny"]["headline"] == sup.saved_headline_text(SAVED_30D)

    def test_an_answer_with_no_guard_events_is_a_good_answer_without_a_failure_notice(self):
        body = (
            "H.route("
            + ROUTE_90D
            + ", { body: H.bodies.empty90 });\n"
            + PICK_90D
            + r"""
        return { shown: snap('90d') };
        """
        )
        r = _run(body)
        assert r["shown"]["notice"] is None, "a good answer without events was called a failure"
        assert not DIGIT.search(r["shown"]["headline"] or ""), r["shown"]["headline"]


STALLED_FETCH = r"""
// The first request for the 90d summary gets no answer until the scenario sends one. The page's
// abort ends it, as a browser's fetch does; a request the page never aborts stays open.
const realFetch = H.sandbox.fetch;
const stalled = [];
const sentFor90d = [];
H.sandbox.fetch = function (url, opts) {
  if (String(url) === '/api/guard-alpha-summary?window=90d') {
    sentFor90d.push(String(url));
    if (stalled.length === 0) {
      return new Promise(function (resolve, reject) {
        stalled.push(function () {
          resolve({ ok: true, status: 200, json: function () { return Promise.resolve(JSON.parse(JSON.stringify(H.bodies.sum1y))); } });
        });
        if (opts && opts.signal) {
          opts.signal.addEventListener('abort', function () {
            const e = new Error('The operation was aborted.'); e.name = 'AbortError'; reject(e);
          });
        }
      });
    }
  }
  return realFetch.apply(this, arguments);
};
"""


class TestAStalledRequestGivesUpWhenTheStatePollDoes:
    @pytest.mark.parametrize("stream_down", [False, True], ids=["stream open", "stream down"])
    def test_a_held_pick_is_called_a_failure_only_once_the_give_up_time_passes(self, stream_down):
        body = (
            "H.route("
            + ROUTE_90D
            + ", { pending: true });\n"
            + PICK_90D
            + f"""
        await H.runTimers({STATE_TIMEOUT_MS} - 500);
        const before = snap('90d');
        await H.runTimers(1000);
        return {{ before: before, after: snap('90d'), asked: asked('90d') }};
        """
        )
        r = _run(body, stream_down=stream_down)
        assert_without_figures(r["before"], "before the give-up time")
        assert r["before"]["notice"] is None, (
            f"a request not yet {STATE_TIMEOUT_MS} ms old was reported as failed"
        )
        assert_without_figures(r["after"], "after the give-up time")
        assert r["after"]["notice"], (
            f"a request with no answer after {STATE_TIMEOUT_MS} ms still shows no notice"
        )

    @pytest.mark.parametrize("stream_down", [False, True], ids=["stream open", "stream down"])
    def test_after_the_give_up_the_next_poll_asks_again_and_its_answer_clears_the_notice(
        self, stream_down
    ):
        body = (
            "H.route("
            + ROUTE_90D
            + ", { pending: true });\n"
            + PICK_90D
            + f"""
        await H.runTimers({STATE_TIMEOUT_MS} + 1000);
        const askedAtGiveUp = asked('90d');
        H.route('/api/state', {{ body: H.bodies.stateNew }});
        H.route({ROUTE_90D}, {{ body: H.bodies.sum90 }});
        await H.runTimers(30000);
        return {{ askedAtGiveUp: askedAtGiveUp, askedLater: asked('90d'), after: snap('90d') }};
        """
        )
        r = _run(body, stream_down=stream_down)
        assert r["askedLater"] > r["askedAtGiveUp"], (
            "no poll asked for the picked window again after the request gave up"
        )
        assert_with_figures_of_90d(r["after"], "after the poll that asked again was answered")
        assert r["after"]["notice"] is None, "the notice outlived the good answer"

    def test_a_late_answer_of_the_request_that_gave_up_changes_nothing(self):
        body = (
            STALLED_FETCH
            + PICK_90D
            + f"""
        await H.runTimers({STATE_TIMEOUT_MS} + 1000);
        const gaveUp = snap('90d');
        stalled[0]();
        await H.settle();
        return {{ gaveUp: gaveUp, late: snap('90d') }};
        """
        )
        r = _run(body)
        assert r["gaveUp"]["notice"], (
            f"a request with no answer after {STATE_TIMEOUT_MS} ms never gave up with a notice"
        )
        assert r["late"] == r["gaveUp"], "a late answer of a request that gave up changed the panel"

    def test_a_late_answer_never_replaces_the_figures_a_later_request_wrote(self):
        body = (
            STALLED_FETCH
            + PICK_90D
            + f"""
        await H.runTimers({STATE_TIMEOUT_MS} + 1000);
        H.route('/api/state', {{ body: H.bodies.stateNew }});
        await H.runTimers(30000);
        const settled = snap('90d');
        stalled[0]();
        await H.settle();
        return {{ settled: settled, late: snap('90d'), sent: sentFor90d.length }};
        """
        )
        r = _run(body)
        assert r["sent"] >= 2, (
            "no poll asked for the window again after its request stalled: the pending guard held"
        )
        assert_with_figures_of_90d(r["settled"], "after the request that asked again was answered")
        assert r["late"] == r["settled"], "the late answer of the first request changed the panel"


class TestAFailureNoticeStaysUpWhileTheWindowIsAskedAgain:
    @pytest.mark.parametrize("stream_down", [False, True], ids=["stream open", "stream down"])
    def test_a_re_ask_for_the_unanswered_window_keeps_the_notice_until_an_answer_lands(
        self, stream_down
    ):
        body = (
            "H.route("
            + ROUTE_90D
            + ", { status: 500, body: { status: 'error' } });\n"
            + PICK_90D
            + r"""
        const failed = snap('90d');
        H.route('/api/state', { body: H.bodies.stateNew });
        H.route(/^\/api\/guard-alpha-summary\?window=90d$/, { pending: true });
        await H.runTimers(30000);
        const waiting = H.waiting('/api/guard-alpha-summary?window=90d');
        const during = snap('90d');
        await H.releasePending('/api/guard-alpha-summary?window=90d', { body: H.bodies.sum90 });
        return { failed: failed, waiting: waiting, during: during, after: snap('90d') };
        """
        )
        r = _run(body, stream_down=stream_down)
        assert r["failed"]["notice"], "precondition: the failed pick showed its notice"
        assert r["waiting"] == 1, "precondition: a poll asked for the window again and is waiting"
        assert r["during"]["notice"], (
            "the notice was hidden when the window was asked again, before any answer"
        )
        assert_without_figures(r["during"], "while the re-ask is held")
        assert r["after"]["notice"] is None, "the notice outlived the good answer"
        assert_with_figures_of_90d(r["after"], "after the re-ask was answered")

    def test_a_pick_of_another_window_while_the_re_ask_is_held_still_clears_the_notice(self):
        body = (
            "H.route("
            + ROUTE_90D
            + ", { status: 500, body: { status: 'error' } });\n"
            + PICK_90D
            + r"""
        H.route('/api/state', { body: H.bodies.stateNew });
        H.route(/^\/api\/guard-alpha-summary\?window=90d$/, { pending: true });
        await H.runTimers(30000);
        const during = snap('90d');
        await H.click('[data-testid="window-30d"]');
        return { during: during, after: snap('90d'), afterAny: snap('30d') };
        """
        )
        r = _run(body)
        assert r["during"]["notice"], (
            "the notice was hidden when the window was asked again, before any answer"
        )
        assert r["after"]["notice"] is None
        assert r["afterAny"]["headline"] == sup.saved_headline_text(SAVED_30D)


# The six elements dash() writes a colour on, and the two of them that carry no sign colour once
# answered.
DASHED_COLOUR_IDS = [
    "dollar-saved-headline",
    "guard-event-count",
    "dollar-saved-net-of-friction-headline",
    "dollar-saved-realized-headline",
    "dollar-saved-realized-coverage",
    "dollar-saved-realized-net-of-friction-headline",
]
UNSIGNED_IDS = ["guard-event-count", "dollar-saved-realized-coverage"]

COLOURS = r"""
const COLOUR_IDS = __IDS__;
const colours = function () {
  const out = {};
  COLOUR_IDS.forEach(function (id) {
    const e = D.getElementById(id);
    out[id] = e ? String((e.style && e.style.color) || '') : null;
  });
  return out;
};
""".replace("__IDS__", json.dumps(DASHED_COLOUR_IDS))


def follows_theme(colour: str | None) -> bool:
    """No inline colour (the stylesheet's applies) or a custom-property reference; never a resolved value."""
    return colour in (None, "") or colour.startswith("var(--")


class TestNoElementKeepsAColourResolvedForTheOtherTheme:
    def test_after_the_load_answers_the_count_and_coverage_follow_the_theme_in_both_directions(self):
        body = (
            COLOURS
            + r"""
        const light = colours();
        await H.setTheme('dark');
        const dark = colours();
        await H.setTheme('light');
        return { light: light, dark: dark, back: colours() };
        """
        )
        r = _run(body)
        for where in ("light", "dark", "back"):
            for element in UNSIGNED_IDS:
                colour = r[where][element]
                assert follows_theme(colour), (
                    f"{where}: {element} keeps the resolved colour {colour!r} after its answer landed"
                )
                assert colour != "var(--studio-ink-dim)", (
                    f"{where}: {element} still has the dash colour after its answer landed"
                )

    def test_a_dash_follows_the_theme_it_is_shown_in(self):
        body = (
            COLOURS
            + "H.route("
            + ROUTE_90D
            + ", { pending: true });\n"
            + PICK_90D
            + r"""
        const dashed = colours();
        await H.setTheme('dark');
        const dark = colours();
        await H.setTheme('light');
        return { dashed: dashed, dark: dark, back: colours() };
        """
        )
        r = _run(body)
        for where in ("dashed", "dark", "back"):
            for element in DASHED_COLOUR_IDS:
                colour = r[where][element]
                assert colour is not None, f"{element} is missing from the page"
                assert follows_theme(colour), (
                    f"{where}: the dash in {element} has the resolved colour {colour!r}, "
                    "which belongs to one theme"
                )

    def test_a_failed_pick_leaves_dashes_that_follow_the_theme(self):
        body = (
            COLOURS
            + "H.route("
            + ROUTE_90D
            + ", { status: 500, body: { status: 'error' } });\n"
            + PICK_90D
            + r"""
        await H.setTheme('dark');
        return { dark: colours() };
        """
        )
        r = _run(body)
        for element in DASHED_COLOUR_IDS:
            assert follows_theme(r["dark"][element]), (
                f"{element} keeps the resolved colour {r['dark'][element]!r} beside a failure notice"
            )

    def test_a_picked_windows_answer_clears_the_dash_colour_of_the_count_and_coverage(self):
        body = (
            COLOURS
            + "H.route("
            + ROUTE_90D
            + ", { pending: true });\n"
            + PICK_90D
            + r"""
        await H.releasePending('/api/guard-alpha-summary?window=90d', { body: H.bodies.sum90 });
        const answered = colours();
        await H.setTheme('dark');
        return { answered: answered, dark: colours() };
        """
        )
        r = _run(body)
        for where in ("answered", "dark"):
            for element in UNSIGNED_IDS:
                colour = r[where][element]
                assert follows_theme(colour) and colour != "var(--studio-ink-dim)", (
                    f"{where}: {element} reads {colour!r} after the 90d answer landed"
                )
