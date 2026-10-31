"""The event stream on ``/`` re-opens after it closes, and the $-saved panel refreshes without it.

Real call path: ``static/chrome.js`` and ``static/index.js`` run whole under node
(tests/dashboard/_index_js_harness.py) over the page ``GET /`` renders. The stream is the harness's
``EventSource`` with the HTML specification's ``readyState``: a reconnect answered 502 closes it for
good (``H.streamError(i, true)``), a transport drop leaves it CONNECTING (``H.streamError(i, false)``),
and ``H.failNewStreams`` makes every new stream fail as a refused reconnect does. Time is the page's own
timers, advanced with ``H.runTimers``; the harness claims nothing about a browser's real timer rules.

The cycle event payloads come from ``app.CycleRecord.event_payload``. The $-saved bodies are the real
``/api/guard-alpha-summary`` route's, two of them re-stamped with chosen totals so two windows read
differently: tests/fixtures/refresh_timing/03-c2/PROVENANCE.md.
"""

from __future__ import annotations

import pytest

from tests.dashboard import _index_js_harness as hx
from tests.dashboard import _refresh_timing_support as sup

WORLD = "default"
SCRIPTS = sup.CHROME_AND_INDEX
REOPEN_WITHIN_MS = 30_000
RETRY_CAP_MS = 60_000

SAVED_30D = 1234.56
SAVED_1Y = -222.25
HEAD_502 = "{ status: 502, body: {} }"


def _world() -> dict:
    return hx.get_world(WORLD)


@pytest.fixture(autouse=True)
def _world_captured_before_the_failure_is_staged():
    """The world is captured through the app's own routes; the staged 401 (auth gate on) must not
    be what builds it, whichever test runs first."""
    _world()


def _bodies(**extra) -> dict:
    world = _world()
    stamp = sup.render_stamp(world)
    return {
        "pollEqual": sup.with_stamp(world["state"], stamp),
        "pollNew": sup.with_stamp(world["next"]["state"], sup.later_stamp(stamp)),
        "eventChanged": sup.event_payload(changed=True),
        "eventUnchanged": sup.event_payload(changed=False),
        **extra,
    }


def _summaries() -> dict:
    out = dict(_world()["summaries"])
    out["30d"] = sup.summary_with(out["30d"], saved=SAVED_30D, events=3, coverage=(2, 3))
    out["1y"] = sup.summary_with(out["1y"], saved=SAVED_1Y, events=7, coverage=(4, 5))
    return out


def _run(scenario: str, *, scripts=SCRIPTS, **extra) -> dict:
    return sup.run(scenario, world=WORLD, scripts=scripts, extra_bodies=_bodies(**extra))


SAVED_URL = r"/^\/api\/guard-alpha-summary/"
PANEL = r"""
const panel = function () {
  return { headline: H.text('#dollar-saved-headline'), verb: H.text('#dollar-saved-verb'),
           count: H.text('#guard-event-count') };
};
"""


# ---------------------------------------------------------------------------
# S-3.1, S-3.2: a closed stream is re-opened, with a growing and capped delay, one stream at most
# ---------------------------------------------------------------------------


class TestAClosedStreamIsReopened:
    def test_a_reconnect_answered_502_is_followed_by_a_new_stream_within_30_seconds(self):
        scenario = (
            r"""
        await H.boot({ extras: true, state: H.bodies.pollEqual });
        H.route('/api/events', __HEAD_502__);
        const before = H.eventSources.length;
        await H.streamError(0, true);
        await H.runTimers(30000);
        return { before: before, after: H.eventSources.length, urls: H.eventSources.map(function (e) { return e.url; }),
                 live: H.liveStreams(), states: H.eventSources.map(function (e) { return e.readyState; }) };
        """
        ).replace("__HEAD_502__", HEAD_502)
        r = _run(scenario)
        assert r["before"] == 1
        assert r["after"] >= 2, "no new EventSource was opened after the stream closed"
        assert set(r["urls"]) == {"/api/events"}
        assert r["live"] == 1, f"at most one stream may be open: {r['states']}"

    def test_failing_reconnects_back_off_up_to_60_seconds_and_never_overlap(self):
        scenario = (
            r"""
        await H.boot({ extras: true, state: H.bodies.pollEqual });
        H.route('/api/events', __HEAD_502__);
        H.failNewStreams = true;
        const errorAt = H.perfNow();
        await H.streamError(0, true);
        await H.runTimers(300000, { skipBelow: 5000 });
        return { errorAt: errorAt, created: H.eventSources.slice(1).map(function (e) { return e.createdAt; }),
                 overlap: H.eventSources.map(function (e) { return e.liveWhenCreated; }) };
        """
        ).replace("__HEAD_502__", HEAD_502)
        r = _run(scenario)
        times = [r["errorAt"], *r["created"]]
        gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
        assert len(gaps) >= 5, f"the page stopped retrying after {len(gaps)} attempts in 5 minutes"
        assert gaps[0] <= REOPEN_WITHIN_MS, f"first reopen after {gaps[0]} ms"
        assert max(gaps) <= RETRY_CAP_MS, f"a gap of {max(gaps)} ms exceeds the 60 s cap"
        assert max(gaps) > gaps[0], f"the gap never grew: {gaps}"
        assert all(b >= a for a, b in zip(gaps, gaps[1:], strict=False)), f"the gap shrank: {gaps}"
        assert max(r["overlap"][1:]) == 0, (
            "a new stream was opened while another was open or connecting"
        )

    def test_a_stream_that_opens_again_resets_the_delay(self):
        scenario = (
            r"""
        await H.boot({ extras: true, state: H.bodies.pollEqual });
        H.route('/api/events', __HEAD_502__);
        H.failNewStreams = true;
        await H.streamError(0, true);
        await H.runTimers(600000, { skipBelow: 5000 });          // the delay has grown to its cap
        H.failNewStreams = false;
        await H.runTimers(90000, { skipBelow: 5000 });           // a stream opens
        const opened = H.eventSources[H.eventSources.length - 1];
        const live = opened.readyState;
        const errorAt = H.perfNow();
        const count = H.eventSources.length;
        await H.streamError(H.eventSources.length - 1, true);   // the server drops it again
        await H.runTimers(30000, { skipBelow: 5000 });
        return { live: live, gap: H.eventSources[count] ? H.eventSources[count].createdAt - errorAt : null };
        """
        ).replace("__HEAD_502__", HEAD_502)
        r = _run(scenario)
        assert r["live"] == 1, "the page never opened a stream once reconnects succeeded"
        assert r["gap"] is not None, "no reopen within 30 s of a fresh close"
        assert r["gap"] <= REOPEN_WITHIN_MS, (
            f"after a successful open the delay must start over; reopened after {r['gap']} ms"
        )

    def test_a_transport_drop_the_browser_reconnects_by_itself_opens_no_second_stream(self):
        scenario = r"""
        await H.boot({ extras: true, state: H.bodies.pollEqual });
        await H.streamError(0, false);                       // still CONNECTING: the browser retries
        await H.runTimers(300000, { skipBelow: 5000 });
        const during = H.eventSources.length;
        await H.streamOpen(0);                               // the browser reconnected
        const n0 = H.fetchLog.length;
        await H.sse('cycle-complete', H.bodies.eventChanged);
        return { during: during, live: H.liveStreams(),
                 states: H.fetchLog.slice(n0).filter(function (e) { return /^\/api\/state/.test(e.url); }).length };
        """
        r = _run(scenario)
        assert r["during"] == 1, (
            "the page opened another stream beside one the browser is reconnecting"
        )
        assert r["live"] == 1
        assert r["states"] == 1, "events must resume on the reconnected stream"

    def test_a_stream_dropped_across_a_night_is_still_retried_at_the_end_of_it(self):
        scenario = (
            r"""
        await H.boot({ extras: true, state: H.bodies.pollEqual });
        H.route('/api/events', __HEAD_502__);
        H.failNewStreams = true;
        await H.streamError(0, true);
        const night = 14 * 60 * 60 * 1000;
        await H.runTimers(night, { skipBelow: 600000 });
        const created = H.eventSources.map(function (e) { return e.createdAt; });
        const overlap = H.eventSources.map(function (e) { return e.liveWhenCreated; });
        H.failNewStreams = false;
        H.route('/api/events', { status: 200, body: {} });
        await H.runTimers(90000);
        return { created: created, overlap: overlap, live: H.liveStreams(),
                 lastState: H.eventSources[H.eventSources.length - 1].readyState };
        """
        ).replace("__HEAD_502__", HEAD_502)
        r = _run(scenario)
        gaps = [b - a for a, b in zip(r["created"], r["created"][1:], strict=False)]
        assert len(gaps) >= 800, f"the page gave up: {len(gaps)} attempts over 14 hours"
        assert max(gaps) <= RETRY_CAP_MS, f"a gap of {max(gaps)} ms over the night"
        assert max(r["overlap"][1:]) == 0
        assert r["lastState"] == 1 and r["live"] == 1, (
            "within 90 s of the server answering again the page holds one open stream"
        )


# ---------------------------------------------------------------------------
# S-3.3, S-3.4: when the $-saved panel is refreshed
# ---------------------------------------------------------------------------


class TestWhenThePanelRefreshes:
    @pytest.mark.parametrize("pick", [None, "1y"])
    def test_with_no_stream_open_each_poll_refreshes_the_panel_for_the_picked_window(self, pick):
        click = f"await H.click('[data-testid=\"window-{pick}\"]');" if pick else ""
        token = pick or "30d"
        scenario = (
            (
                r"""
        await H.boot({ extras: true, state: H.bodies.pollEqual });
        __CLICK__
        H.route('/api/events', __HEAD_502__);
        H.failNewStreams = true;
        await H.streamError(0, true);
        const n0 = H.fetchLog.length;
        await H.runTimers(30000, { skipBelow: 5000 });
        return { saved: H.fetchLog.slice(n0).map(function (e) { return e.url; })
                          .filter(function (u) { return /^\/api\/guard-alpha-summary/.test(u); }),
                 live: H.liveStreams() };
        """
            )
            .replace("__CLICK__", click)
            .replace("__HEAD_502__", HEAD_502)
        )
        r = _run(scenario, summaries=_summaries())
        assert r["live"] == 0, "the scenario needs the stream down"
        assert r["saved"] == [f"/api/guard-alpha-summary?window={token}"], r["saved"]

    def test_with_a_stream_open_a_poll_does_not_refresh_the_panel(self):
        scenario = r"""
        await H.boot({ extras: true, state: H.bodies.pollEqual });
        const n0 = H.fetchLog.length;
        await H.runTimers(30000, { skipBelow: 5000 });
        return { saved: H.fetchLog.slice(n0).filter(function (e) { return /^\/api\/guard-alpha-summary/.test(e.url); }).length,
                 states: H.fetchLog.slice(n0).filter(function (e) { return /^\/api\/state/.test(e.url); }).length,
                 live: H.liveStreams() };
        """
        r = _run(scenario, summaries=_summaries())
        assert r["live"] == 1 and r["states"] == 1
        assert r["saved"] == 0, "the panel is refreshed by events while the stream is open"

    def test_the_panel_refreshes_once_for_a_changed_event_and_not_for_an_unchanged_one(self):
        scenario = r"""
        await H.boot({ extras: true, state: H.bodies.pollEqual });
        const n0 = H.fetchLog.length;
        await H.sse('cycle-complete', H.bodies.eventChanged);
        await H.sse('cycle-complete', H.bodies.eventUnchanged);
        const urls = H.fetchLog.slice(n0).map(function (e) { return e.url; });
        return { saved: urls.filter(function (u) { return /^\/api\/guard-alpha-summary/.test(u); }),
                 states: urls.filter(function (u) { return /^\/api\/state/.test(u); }) };
        """
        r = _run(scenario, summaries=_summaries())
        assert r["saved"] == ["/api/guard-alpha-summary?window=30d"]
        assert len(r["states"]) == 1, "an unchanged event must send no /api/state either"

    def test_a_failed_panel_request_keeps_the_last_values(self):
        scenario = (
            PANEL
            + r"""
        await H.boot({ extras: true, state: H.bodies.pollEqual });
        const before = panel();
        H.route(/^\/api\/guard-alpha-summary/, { status: 500, body: { status: 'error', message: 'An internal error occurred' } });
        await H.sse('cycle-complete', H.bodies.eventChanged);
        const afterError = panel();
        H.route(/^\/api\/guard-alpha-summary/, { reject: true });
        await H.sse('cycle-complete', H.bodies.eventChanged);
        return { before: before, afterError: afterError, afterReject: panel() };
        """
        )
        r = _run(scenario, summaries=_summaries())
        assert r["before"]["headline"] == sup.saved_headline_text(SAVED_30D), r["before"]
        assert r["afterError"] == r["before"]
        assert r["afterReject"] == r["before"]

    def test_an_answer_for_an_earlier_window_that_lands_last_is_discarded(self):
        scenario = (
            PANEL
            + r"""
        await H.boot({ extras: true, state: H.bodies.pollEqual });
        H.route(/^\/api\/guard-alpha-summary\?window=1y$/, { pending: true });
        await H.click('[data-testid="window-1y"]');
        await H.click('[data-testid="window-30d"]');
        const waiting = H.waiting('/api/guard-alpha-summary?window=1y');
        await H.releasePending('/api/guard-alpha-summary?window=1y', { body: H.bodies.summaries['1y'] });
        return { waiting: waiting, panel: panel() };
        """
        )
        r = _run(scenario, summaries=_summaries())
        assert r["waiting"] == 1
        assert r["panel"]["headline"] == sup.saved_headline_text(SAVED_30D)
        assert r["panel"]["verb"] == "saved"
        assert r["panel"]["count"] == "3", "the panel shows the 1y window's count after a 30d pick"


# ---------------------------------------------------------------------------
# S-3.5: a reconnect refused with 401 goes to login
# ---------------------------------------------------------------------------


def test_a_closed_stream_whose_probe_is_answered_401_sends_the_page_to_login(monkeypatch):
    refusal = sup.as_route_spec(sup.error_response("401", monkeypatch))
    scenario = r"""
    await H.boot({ extras: true, state: H.bodies.pollEqual });
    H.route('/api/events', H.bodies.refusal);
    await H.streamError(0, true);
    await H.runTimers(30000);
    return { navigations: H.navigations.slice(), probes: H.requestsTo(/^\/api\/events$/).map(function (e) { return e.method; }) };
    """
    r = _run(scenario, refusal=refusal)
    assert r["navigations"] == ["/login"], r["navigations"]
    assert "HEAD" in r["probes"], "the page must ask the endpoint why the stream closed (HEAD)"


# ---------------------------------------------------------------------------
# S-3.6: the daemon restarts with the page open
# ---------------------------------------------------------------------------


def test_after_a_daemon_restart_events_the_header_and_values_recover_without_a_reload():
    scenario = r"""
    await H.boot({ extras: true, state: H.bodies.pollEqual });
    // The daemon goes away: polls and the stream both fail for ten minutes.
    H.route('/api/state', { reject: true });
    H.route('/api/events', { reject: true });
    H.failNewStreams = true;
    await H.streamError(0, true);
    await H.runTimers(600000, { skipBelow: 5000 });
    const down = H.text('#engine-status-label');
    // The new daemon serves.
    H.route('/api/state', { body: H.bodies.pollNew });
    H.route('/api/events', { status: 200, body: {} });
    H.failNewStreams = false;
    await H.runTimers(90000, { skipBelow: 5000 }); await H.completeAnimations();
    const n0 = H.fetchLog.length;
    await H.sse('cycle-complete', H.bodies.eventChanged);
    return { down: down, label: H.text('#engine-status-label'), asOf: H.text('#hero-data-as-of'),
             live: H.liveStreams(), reloads: H.reloads, navigations: H.navigations.slice(),
             refreshed: H.fetchLog.slice(n0).filter(function (e) { return /^\/api\/state/.test(e.url); }).length };
    """
    r = _run(scenario)
    assert r["down"] == "Connection Lost"
    assert r["label"] == "Live"
    assert r["asOf"] == sup.AS_OF_PREFIX + sup.later_stamp(sup.render_stamp(_world()))
    assert r["live"] == 1, "events must resume within 90 s of the daemon serving"
    assert r["refreshed"] == 1, "an event on the new stream refreshes the page"
    assert r["reloads"] == 0 and r["navigations"] == []


# ---------------------------------------------------------------------------
# S-3.10: a healthy stream is left alone
# ---------------------------------------------------------------------------


def test_an_hour_with_a_healthy_stream_opens_no_second_stream_and_keeps_the_30_second_poll():
    scenario = r"""
    await H.boot({ extras: true, state: H.bodies.pollEqual });
    await H.runTimers(3600000, { skipBelow: 5000 });
    return { streams: H.eventSources.length, polls: H.intervals.filter(function (i) { return i.ms === 30000; }).length,
             states: H.requests(/^\/api\/state/).length };
    """
    r = _run(scenario, scripts=sup.INDEX_ONLY)
    assert r["streams"] == 1
    assert r["polls"] == 1, "the poll's interval must stay a single 30000 ms interval"
    assert r["states"] == 1 + 3600 // 30, "one request at load and one per 30 s"
