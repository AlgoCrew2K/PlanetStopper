"""Age cue, catch-up on return, a true countdown, the server-reckoned "Stale" and a page left open overnight.

Real call path: ``static/chrome.js`` and ``static/index.js`` run whole under node
(tests/dashboard/_index_js_harness.py) over the page ``GET /`` renders, with the bodies the real
``/api/state`` route returns for a seeded database. Time is the page's own: ``H.runTimers`` advances
``Date`` and ``performance.now()`` together and runs the timers that fall due, ``H.sleepFor`` jumps them
with no timer running (a closed laptop; ``perf=False`` moves ``Date`` alone), and a response's HTTP
``Date`` header is the page clock at the moment it is made unless a scenario skews it.

The header is read from the DOM (label, tooltip, colour token, countdown). Expected sentences are the
fixed ones the plan states for each reason (the suite approach's RC-1); expected figures are computed
from the clock the scenario set. Derived bodies: tests/fixtures/refresh_timing/03-c3/PROVENANCE.md.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest

from tests.dashboard import _index_js_harness as hx
from tests.dashboard import _refresh_timing_support as sup

WORLD = "default"
SCRIPTS = sup.CHROME_AND_INDEX

NOT_UPDATING_AFTER_MS = 90_000
STALE_AFTER_MS = 120_000

# A mid-session instant (10:00:20 EDT), 40 s before the next engine minute.
NOW = dt.datetime(2026, 10, 30, 14, 0, 20, tzinfo=dt.UTC)
NOW_MS = int(NOW.timestamp() * 1000)

BLACKOUT_TITLE = "Scheduled pause before the close; stops resume at the close."
OPENING_TITLE = "Scheduled session start; the first cycle is running."
STALE_TITLE = "Positions may not be actively guarded."
DEGRADED_TEXT = {
    "scheduler_stalled": "Scheduler stalled: no engine spawn for 2 minutes or more.",
    "engine_overrun": "An engine run has lasted over 3 minutes; minutes are being skipped.",
    "engine_failing": "The last 5 engine runs failed.",
}
UNKNOWN_DEGRADED_TEXT = "The daemon reports a degraded state."
POS, WARN, NEG, DIM = (f"var(--studio-{n})" for n in ("pos", "warn", "neg", "ink-dim"))

PRELUDE = r"""
const quiet = function () {            // only the cue's own tick runs: the 30 s polls are taken out
  H.intervals = H.intervals.filter(function (i) { return i.ms !== 30000; });
};
const stateCount = function (n0) {
  return H.fetchLog.slice(n0).filter(function (e) { return /^\/api\/state/.test(e.url); }).length;
};
const header = function () {
  const wrap = H.el('#engine-status');
  return { label: H.text('#engine-status-label'), title: (wrap.attrs && wrap.attrs.title) || '',
           color: H.el('#engine-status-label').style.color, dot: H.el('#engine-status-dot').style.background,
           countdown: H.el('#next-run-countdown').style.display, countdownText: H.text('#next-run-countdown'),
           asOf: H.text('#hero-data-as-of') };
};
const now = function () { return Date.parse(H.nowIso()); };
const secsToNextMinute = function (ms) { return 60 - (Math.floor(ms / 1000) % 60); };
"""


def _world() -> dict:
    return hx.get_world(WORLD)


iso_z = sup.iso_z
et_iso = sup.et_iso


def poll_body(
    *,
    cycle_age_ms: int | None = 10_000,
    at_ms: int = NOW_MS,
    phase: str = "running",
    reasons: list | None = None,
    next_run: int | None = None,
    closed: bool = False,
) -> dict:
    """A real /api/state body with the named liveness fields set (derived: see PROVENANCE.md)."""
    state = (
        sup.closed_body(_world()["state"]) if closed else sup.open_body(_world()["state"], phase)
    )
    state["engine_phase"] = phase
    state = sup.with_cycle_at(state, None if cycle_age_ms is None else et_iso(at_ms - cycle_age_ms))
    if reasons is not None:
        state["degraded_reasons"] = reasons
    secs = 60 - ((at_ms // 1000) % 60) if next_run is None else next_run
    return sup.with_next_run(state, secs)


def _run(scenario: str, *, now_ms: int = NOW_MS, **bodies) -> dict:
    return sup.run(
        PRELUDE + scenario,
        world=WORLD,
        scripts=SCRIPTS,
        extra_bodies=bodies,
        now_iso=iso_z(now_ms),
        timeout=420,
    )


def _stamp_of(body: dict) -> str:
    stamp = sup.stamp_of(body)
    assert stamp, "the body carries no data stamp"
    return stamp


def _minutes_text(ms: int) -> str:
    return f"No data received for {ms // 60_000} min."


# ---------------------------------------------------------------------------
# S-4.1, S-4.10, S-4.13, S-4.14: the not-updating cue
# ---------------------------------------------------------------------------


class TestTheAgeCue:
    def test_after_90_seconds_without_data_the_header_and_the_as_of_say_so(self):
        body = poll_body()
        r = _run(
            r"""
        await H.boot({ extras: true, state: H.bodies.poll });
        quiet();
        await H.runTimers(89000);
        const early = header();
        await H.runTimers(6000);
        return { early: early, late: header() };
        """,
            poll=body,
        )
        assert r["early"]["label"] == "Live", "the cue shows at 90 s, not before"
        assert r["early"]["asOf"] == sup.AS_OF_PREFIX + _stamp_of(body)
        assert r["late"]["label"] == "Not updating"
        assert r["late"]["title"] == _minutes_text(95_000)
        assert r["late"]["color"] == WARN and r["late"]["dot"] == WARN
        assert r["late"]["asOf"] == sup.AS_OF_PREFIX + _stamp_of(body) + " · not updated for 1 min"

    def test_the_cue_counts_whole_minutes(self):
        r = _run(
            r"""
        await H.boot({ extras: true, state: H.bodies.poll });
        quiet();
        await H.runTimers(305000, { skipBelow: 5000 });
        return header();
        """,
            poll=poll_body(),
        )
        assert r["label"] == "Not updating"
        assert r["title"] == _minutes_text(305_000)
        assert r["asOf"].endswith(" · not updated for 5 min")

    def test_the_cue_measures_receipt_time_on_the_page_not_the_browser_clock(self):
        skew = 187_000  # the browser's Date runs three minutes and seven seconds ahead
        skewed_now = NOW_MS + skew
        body = poll_body(cycle_age_ms=30_000)
        r = _run(
            r"""
        const SKEW = __SKEW__;
        const serverDate = function () { return new Date(now() - SKEW).toUTCString(); };
        await H.boot({ extras: true, overrides: [['/api/state', function () { return { body: H.bodies.poll, headers: { Date: serverDate() } }; }]] });
        const fresh = header();
        quiet();
        await H.runTimers(60000);
        const sixty = header();
        await H.runTimers(35000);
        return { fresh: fresh, sixty: sixty, ninetyFive: header() };
        """.replace("__SKEW__", str(skew)),
            now_ms=skewed_now,
            poll=body,
        )
        assert r["fresh"]["label"] == "Live", "a browser clock ahead must not make fresh data Stale"
        assert r["sixty"]["label"] == "Live", "60 s of silence is not yet the cue"
        assert r["ninetyFive"]["label"] == "Not updating"

    def test_a_failed_poll_after_95_seconds_of_silence_reads_connection_lost(self):
        r = _run(
            r"""
        await H.boot({ extras: true, state: H.bodies.poll });
        quiet();
        await H.runTimers(95000);
        const silent = header();
        H.route('/api/state', { reject: true });
        await H.sandbox.window.loadState();
        return { silent: silent, failed: header() };
        """,
            poll=poll_body(),
        )
        assert r["silent"]["label"] == "Not updating"
        assert r["failed"]["label"] == "Connection Lost"
        assert r["failed"]["color"] == NEG
        assert r["failed"]["countdown"] == "none"

    def test_the_cue_outranks_a_rebalance_pause(self):
        r = _run(
            r"""
        await H.boot({ extras: true, state: H.bodies.poll });
        quiet();
        await H.runTimers(89000);
        const paused = header();
        await H.runTimers(6000);
        return { paused: paused, late: header() };
        """,
            poll=poll_body(phase="blackout"),
        )
        assert r["paused"]["label"] == "Rebalance pause"
        assert r["paused"]["title"] == BLACKOUT_TITLE
        assert r["late"]["label"] == "Not updating"
        assert r["late"]["title"] == _minutes_text(95_000)

    def test_the_tick_that_drives_the_cue_sends_no_request(self):
        r = _run(
            r"""
        await H.boot({ extras: true, state: H.bodies.poll });
        quiet();
        const n0 = H.fetchLog.length;
        await H.runTimers(30000);
        return { sent: H.fetchLog.length - n0 };
        """,
            poll=poll_body(),
        )
        assert r["sent"] == 0, "the one-second tick must not send requests"

    def test_with_the_poll_running_a_visible_tab_sends_only_the_polls_request(self):
        r = _run(
            r"""
        await H.boot({ extras: true, state: H.bodies.poll });
        await H.runTimers(20000);
        const n0 = H.fetchLog.length;
        await H.runTimers(30000);
        return { states: stateCount(n0), urls: H.fetchLog.slice(n0).map(function (e) { return e.method + ' ' + e.url; }) };
        """,
            poll=poll_body(),
        )
        assert r["states"] == 1, r["urls"]
        assert not [u for u in r["urls"] if u.startswith("HEAD ")], (
            "no probe belongs to a healthy page"
        )


# ---------------------------------------------------------------------------
# S-4.2 to S-4.5: one request at once on return, wake, back-forward restore and network return
# ---------------------------------------------------------------------------


class TestCatchUp:
    def test_becoming_visible_after_ten_minutes_sends_one_request_at_once(self):
        r = _run(
            r"""
        await H.boot({ extras: true, state: H.bodies.poll });
        quiet();
        await H.setVisibility('hidden');
        await H.runTimers(600000, { skipBelow: 5000 });
        const hiddenSent = stateCount(0) - 1;
        const n0 = H.fetchLog.length;
        await H.setVisibility('visible');
        return { sent: stateCount(n0), hiddenSent: hiddenSent };
        """,
            poll=poll_body(),
        )
        assert r["hiddenSent"] == 0, "hiding the tab sends nothing"
        assert r["sent"] == 1, "returning to the tab must refresh at once, exactly once"

    def test_becoming_visible_reopens_a_closed_stream_at_once(self):
        r = _run(
            r"""
        await H.boot({ extras: true, state: H.bodies.poll });
        quiet();
        H.route('/api/events', { status: 502, body: {} });
        H.failNewStreams = true;
        await H.setVisibility('hidden');
        await H.streamError(0, true);
        await H.runTimers(600000, { skipBelow: 5000 });
        H.route('/api/events', { status: 200, body: {} });
        H.failNewStreams = false;
        const before = H.eventSources.length;
        await H.setVisibility('visible');
        const at = H.eventSources.length;
        await H.runTimers(70000);
        return { before: before, at: at, live: H.liveStreams(),
                 overlap: H.eventSources.slice(before).map(function (e) { return e.liveWhenCreated; }) };
        """,
            poll=poll_body(),
        )
        assert r["at"] == r["before"] + 1, "no new stream was opened on return"
        assert r["live"] == 1 and max(r["overlap"]) == 0, (
            "a pending retry timer opened a second stream beside the one the return opened"
        )

    @pytest.mark.parametrize("trigger", ["online", "visible"])
    def test_waking_after_five_minutes_sends_one_request_at_once(self, trigger):
        wake = (
            "await H.fireWindow('online');"
            if trigger == "online"
            else "await H.setVisibility('hidden'); await H.setVisibility('visible');"
        )
        r = _run(
            r"""
        await H.boot({ extras: true, state: H.bodies.poll });
        quiet();
        H.sleepFor(300000, { perf: true });
        H.route('/api/state', { body: H.bodies.atWake });     // the daemon kept cycling while the lid was shut
        const n0 = H.fetchLog.length;
        __WAKE__
        await H.completeAnimations();
        return { sent: stateCount(n0), label: H.text('#engine-status-label') };
        """.replace("__WAKE__", wake),
            poll=poll_body(),
            atWake=poll_body(at_ms=NOW_MS + 300_000),
        )
        assert r["sent"] == 1
        assert r["label"] == "Live", "the refresh that follows the wake clears the age cue"

    def test_a_page_restored_from_the_back_forward_cache_refreshes_at_once(self):
        r = _run(
            r"""
        await H.boot({ extras: true, state: H.bodies.poll });
        quiet();
        const n0 = H.fetchLog.length;
        await H.fireWindow('pageshow', { persisted: false });
        const fresh = stateCount(n0);
        await H.fireWindow('pageshow', { persisted: true });
        return { fresh: fresh, restored: stateCount(n0) };
        """,
            poll=poll_body(),
        )
        assert r["fresh"] == 0, "a normal load's pageshow must not send a second request"
        assert r["restored"] == 1

    def test_two_wake_signals_within_a_second_send_one_request(self):
        r = _run(
            r"""
        await H.boot({ extras: true, state: H.bodies.poll });
        quiet();
        await H.setVisibility('hidden');
        const n0 = H.fetchLog.length;
        await H.setVisibility('visible');
        await H.runTimers(400);
        await H.fireWindow('online');
        return { sent: stateCount(n0) };
        """,
            poll=poll_body(),
        )
        assert r["sent"] == 1

    def test_a_later_wake_signal_is_a_catch_up_of_its_own(self):
        r = _run(
            r"""
        await H.boot({ extras: true, state: H.bodies.poll });
        quiet();
        await H.setVisibility('hidden');
        const n0 = H.fetchLog.length;
        await H.setVisibility('visible');
        await H.runTimers(1500);
        await H.fireWindow('online');
        return { sent: stateCount(n0) };
        """,
            poll=poll_body(),
        )
        assert r["sent"] == 2


# ---------------------------------------------------------------------------
# S-4.7 to S-4.9, S-4.11: the countdown
# ---------------------------------------------------------------------------

_COUNTDOWN = re.compile(r"^NEXT (\d\d):(\d\d)$")


def _secs(text: str) -> int:
    match = _COUNTDOWN.match(text)
    assert match, f"the countdown must read 'NEXT MM:SS' like the server render; got {text!r}"
    return int(match[1]) * 60 + int(match[2])


class TestTheCountdown:
    def test_the_server_render_and_the_first_poll_share_one_form(self):
        r = _run(
            r"""
        const rendered = H.text('#next-run-countdown');
        await H.boot({ extras: true, state: H.bodies.poll });
        return { rendered: rendered, polled: H.text('#next-run-countdown') };
        """,
            poll=poll_body(),
        )
        _secs(r["rendered"])
        _secs(r["polled"])

    def test_a_response_that_took_four_seconds_still_gives_the_true_countdown(self):
        r = _run(
            r"""
        await H.boot({ extras: true, statePending: true });
        await H.runTimers(4000, { skipBelow: 5000 });
        await H.releasePending('/api/state', { body: H.bodies.poll });
        await H.completeAnimations();
        const text = H.text('#next-run-countdown');
        return { text: text, truth: secsToNextMinute(now()) };
        """,
            poll=poll_body(next_run=40),
        )
        assert abs(_secs(r["text"]) - r["truth"]) <= 1, (
            f"countdown {r['text']!r} is not the true {r['truth']} s to the next :00"
        )

    def test_the_countdown_passes_the_minute_without_sitting_at_zero(self):
        at = NOW_MS + 30_000  # 10:00:50
        r = _run(
            r"""
        await H.boot({ extras: true, state: H.bodies.poll });
        const seen = [H.text('#next-run-countdown')];
        for (let i = 0; i < 15; i++) { await H.runTimers(1000); seen.push(H.text('#next-run-countdown')); }
        return { seen: seen };
        """,
            now_ms=at,
            poll=poll_body(at_ms=at, next_run=10),
        )
        secs = [_secs(t) for t in r["seen"]]
        assert secs[0] in (9, 10, 11), secs
        for a, b in zip(secs, secs[1:], strict=False):
            assert b == a - 1 or (a == 0 and b in (0, 59)), f"countdown jumped {a} -> {b}: {secs}"
        zeros = max(
            (
                len(m.group(0))
                for m in re.finditer(r"0+", "".join("0" if s == 0 else "x" for s in secs))
            ),
            default=0,
        )
        assert zeros <= 2, f"the countdown sat at NEXT 00:00 for {zeros} ticks: {secs}"
        assert abs(secs[-1] - 55) <= 1, (
            f"after the minute it must continue from the next count: {secs}"
        )

    def test_a_fast_browser_clock_does_not_move_the_countdown(self):
        skew = 187_000
        r = _run(
            r"""
        const SKEW = __SKEW__;
        const route = function () { return { body: H.bodies.poll, headers: { Date: new Date(now() - SKEW).toUTCString() } }; };
        await H.boot({ extras: true, overrides: [['/api/state', route]] });
        const first = H.text('#next-run-countdown');
        await H.runTimers(7000);
        return { first: first, later: H.text('#next-run-countdown') };
        """.replace("__SKEW__", str(skew)),
            now_ms=NOW_MS + skew,
            poll=poll_body(next_run=40),
        )
        assert _secs(r["later"]) == _secs(r["first"]) - 7, (r["first"], r["later"])

    def test_a_closed_market_hides_the_countdown(self):
        r = _run(
            r"""
        await H.boot({ extras: true, state: H.bodies.poll });
        return header();
        """,
            poll=poll_body(closed=True, phase="closed"),
        )
        assert r["label"] == "Closed"
        assert r["countdown"] == "none"


# ---------------------------------------------------------------------------
# S-4.12 and the label order (overview SD-13, suite RC-1)
# ---------------------------------------------------------------------------


class TestTheLabelFollowsTheServersClock:
    def test_a_fast_browser_clock_never_makes_a_recent_cycle_stale(self):
        skew = 187_000
        r = _run(
            r"""
        const SKEW = __SKEW__;
        const route = function () { return { body: H.bodies.poll, headers: { Date: new Date(now() - SKEW).toUTCString() } }; };
        await H.boot({ extras: true, overrides: [['/api/state', route]] });
        return header();
        """.replace("__SKEW__", str(skew)),
            now_ms=NOW_MS + skew,
            poll=poll_body(cycle_age_ms=30_000),
        )
        assert r["label"] == "Live", "the cycle was 30 s old by the server's own clock"

    def test_a_slow_browser_clock_still_shows_a_stale_cycle(self):
        slow = 600_000
        r = _run(
            r"""
        const SLOW = __SLOW__;
        const route = function () { return { body: H.bodies.poll, headers: { Date: new Date(now() + SLOW).toUTCString() } }; };
        await H.boot({ extras: true, overrides: [['/api/state', route]] });
        return header();
        """.replace("__SLOW__", str(slow)),
            now_ms=NOW_MS - slow,
            poll=poll_body(cycle_age_ms=4 * 60_000, at_ms=NOW_MS),
        )
        assert r["label"] == "Stale"
        assert r["title"] == STALE_TITLE

    def test_the_cycle_ages_while_the_page_waits_and_turns_stale_at_two_minutes(self):
        r = _run(
            r"""
        await H.boot({ extras: true, state: H.bodies.poll });
        quiet();
        await H.runTimers(15000);
        const early = header();
        await H.runTimers(10000);
        return { early: early, late: header() };
        """,
            poll=poll_body(cycle_age_ms=100_000),
        )
        assert r["early"]["label"] == "Live", "115 s old by the server's reckoning is not yet stale"
        assert r["late"]["label"] == "Stale", (
            "125 s old must read Stale before the 90 s cue applies"
        )
        assert r["late"]["color"] == WARN

    def test_a_body_with_no_server_time_keeps_the_previous_stale_judgement(self):
        """``updateEngineStatus(data)`` is the alias that passes no receipt record."""
        r = _run(
            r"""
        await H.boot({ extras: true, state: H.bodies.poll });
        const before = header();
        H.sandbox.window.updateEngineStatus(H.bodies.old);
        return { before: before, after: header() };
        """,
            poll=poll_body(),
            old=poll_body(cycle_age_ms=10 * 60_000),
        )
        assert r["before"]["label"] == "Live"
        assert r["after"]["label"] == "Live", (
            "without a server time the browser clock must not decide Stale"
        )

    # (name, setup, expected label, title, colour token, countdown shown)
    CASES = [
        ("closed", dict(closed=True, phase="closed"), "Closed", "", DIM, False),
        (
            "closed outranks a blackout phase",
            dict(closed=True, phase="blackout"),
            "Closed",
            "",
            DIM,
            False,
        ),
        ("blackout", dict(phase="blackout"), "Rebalance pause", BLACKOUT_TITLE, DIM, True),
        (
            "blackout outranks a stale cycle",
            dict(phase="blackout", cycle_age_ms=4 * 60_000),
            "Rebalance pause",
            BLACKOUT_TITLE,
            DIM,
            True,
        ),
        ("opening", dict(phase="opening", cycle_age_ms=None), "Opening", OPENING_TITLE, DIM, True),
        (
            "opening outranks a stale cycle",
            dict(phase="opening", cycle_age_ms=2 * 60 * 60_000),
            "Opening",
            OPENING_TITLE,
            DIM,
            True,
        ),
        ("stale", dict(cycle_age_ms=4 * 60_000), "Stale", STALE_TITLE, WARN, True),
        ("live", dict(cycle_age_ms=10_000), "Live", "", POS, True),
        (
            "degraded outranks closed",
            dict(closed=True, phase="closed", reasons=["scheduler_stalled"]),
            "Degraded",
            DEGRADED_TEXT["scheduler_stalled"],
            NEG,
            True,
        ),
        (
            "degraded outranks a rebalance pause",
            dict(phase="blackout", reasons=["engine_failing"]),
            "Degraded",
            DEGRADED_TEXT["engine_failing"],
            NEG,
            True,
        ),
        (
            "degraded outranks stale",
            dict(cycle_age_ms=4 * 60_000, reasons=["engine_overrun"]),
            "Degraded",
            DEGRADED_TEXT["engine_overrun"],
            NEG,
            True,
        ),
        ("an empty reason list changes nothing", dict(reasons=[]), "Live", "", POS, True),
    ]

    @pytest.mark.parametrize(
        ("setup", "label", "title", "colour", "countdown"),
        [pytest.param(*case[1:], id=case[0]) for case in CASES],
    )
    def test_the_label_order(self, setup, label, title, colour, countdown):
        r = _run(
            r"""
        await H.boot({ extras: true, state: H.bodies.poll });
        return header();
        """,
            poll=poll_body(**setup),
        )
        assert (r["label"], r["title"]) == (label, title)
        assert r["color"] == colour and r["dot"] == colour, (r["color"], r["dot"])
        assert (r["countdown"] != "none") is countdown

    @pytest.mark.parametrize(
        ("reasons", "title"),
        [
            (["scheduler_stalled"], DEGRADED_TEXT["scheduler_stalled"]),
            (["engine_overrun"], DEGRADED_TEXT["engine_overrun"]),
            (["engine_failing"], DEGRADED_TEXT["engine_failing"]),
            (["something_new"], UNKNOWN_DEGRADED_TEXT),
            (
                ["scheduler_stalled", "engine_failing"],
                DEGRADED_TEXT["scheduler_stalled"] + " " + DEGRADED_TEXT["engine_failing"],
            ),
            (
                ["engine_overrun", "something_new"],
                DEGRADED_TEXT["engine_overrun"] + " " + UNKNOWN_DEGRADED_TEXT,
            ),
        ],
        ids=["stalled", "overrun", "failing", "unknown", "two reasons", "known and unknown"],
    )
    def test_the_degraded_tooltip_is_a_fixed_sentence_per_reason_never_the_raw_key(
        self, reasons, title
    ):
        r = _run(
            r"""
        await H.boot({ extras: true, state: H.bodies.poll });
        return header();
        """,
            poll=poll_body(reasons=reasons),
        )
        assert r["label"] == "Degraded"
        assert r["title"] == title
        for raw in reasons:
            assert raw not in r["title"], "the raw reason key leaked into the tooltip"


# ---------------------------------------------------------------------------
# The ET clock across the end of daylight saving (added: the overnight requirement)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("instant", "reads"),
    [
        ("2026-10-30T13:30:05.000Z", "09:30:05"),  # Friday, still EDT
        ("2026-11-02T14:30:05.000Z", "09:30:05"),  # Monday, after the change to EST
        ("2026-11-02T13:30:05.000Z", "08:30:05"),  # an hour earlier on the new offset
    ],
    ids=["friday-edt", "monday-est", "monday-an-hour-before-the-open"],
)
def test_the_header_clock_reads_eastern_time_on_both_sides_of_the_clock_change(instant, reads):
    start = int(dt.datetime.fromisoformat(instant.replace("Z", "+00:00")).timestamp() * 1000) - 1000
    r = _run(
        r"""
    await H.boot({ extras: true, state: H.bodies.poll });
    await H.runTimers(1000);
    return { clock: H.text('[data-testid="et-clock"]') };
    """,
        now_ms=start,
        poll=poll_body(at_ms=start),
    )
    assert r["clock"] == reads


# ---------------------------------------------------------------------------
# A page left open overnight (added: the operator's requirement; see the handoff)
# ---------------------------------------------------------------------------

_DOWN = "2026-11-01T23:00:00Z"  # Sunday evening: the daemon and the proxy stop answering
_UP = "2026-11-02T13:00:00Z"  # Monday 08:00 EST: they answer again
_OPEN = "2026-11-02T14:30:00Z"  # 09:30 EST, the first Monday after the clocks went back


def _ms(iso: str) -> int:
    return int(dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


# What the server answers by the page's own clock: down overnight, closed until 09:30 ET, then the
# opening phase and, a minute after, the first cycle.
SERVER = (
    r"""
const DOWN = Date.parse('__DOWN__'), UP = Date.parse('__UP__'), OPEN = Date.parse('__OPEN__');
const stateFor = function () {
  const t = now();
  if (t >= DOWN && t < UP) return { reject: true };
  let body = H.bodies.closed;
  if (t >= OPEN && t < OPEN + 65000) body = H.bodies.opening;
  if (t >= OPEN + 65000) {
    body = Object.assign({}, H.bodies.running, {
      last_successful_cycle_at: new Date(Math.floor((t - 5000) / 60000) * 60000 + 5000).toISOString() });
  }
  return { body: Object.assign({}, body, { next_run_seconds: secsToNextMinute(t) }) };
};
const eventsFor = function () {
  const t = now();
  return t >= DOWN && t < UP ? { status: 502, body: {} } : { status: 200, body: {} };
};
""".replace("__DOWN__", _DOWN)
    .replace("__UP__", _UP)
    .replace("__OPEN__", _OPEN)
)


def _overnight_bodies(start_ms: int) -> dict:
    return {
        "closed": poll_body(closed=True, phase="closed", at_ms=start_ms, cycle_age_ms=None),
        "opening": poll_body(phase="opening", at_ms=_ms(_OPEN), cycle_age_ms=None),
        "running": poll_body(phase="running", at_ms=_ms(_OPEN) + 70_000),
        "eventChanged": sup.event_payload(changed=True),
    }


class TestAPageLeftOpenOvernight:
    def test_the_poll_the_stream_and_the_label_resume_by_themselves_at_the_monday_open(self):
        start = _ms("2026-11-01T22:00:07Z")
        r = _run(
            SERVER
            + r"""
        await H.boot({ extras: true, overrides: [['/api/state', stateFor], ['/api/events', eventsFor]] });
        const sunday = header();
        await H.runTimers(DOWN - now(), { skipBelow: 5000 });
        H.failNewStreams = true;                              // the stream drops as the night begins
        const errorAt = H.sandbox.performance.now();
        await H.streamError(0, true);
        await H.runTimers(UP - now(), { skipBelow: 600000 });
        const night = { errorAt: errorAt, created: H.eventSources.map(function (e) { return e.createdAt; }),
                        overlap: H.eventSources.map(function (e) { return e.liveWhenCreated; }) };
        H.failNewStreams = false;                             // the daemon answers again
        await H.runTimers(120000);
        const up = { header: header(), live: H.liveStreams() };
        await H.runTimers(OPEN - 10000 - now(), { skipBelow: 5000 });
        const beforeOpen = header();
        await H.runTimers(40000);                             // 09:30:30 EST
        const afterOpen = { header: header(), truth: secsToNextMinute(now()), clock: H.text('[data-testid="et-clock"]') };
        await H.runTimers(40000);                             // 09:31:10 EST
        await H.sse('cycle-complete', H.bodies.eventChanged); await H.completeAnimations();
        const running = header();
        return { sunday: sunday, night: night, up: up, beforeOpen: beforeOpen, afterOpen: afterOpen,
                 running: running, live: H.liveStreams(), reloads: H.reloads, navigations: H.navigations.slice() };
        """,
            now_ms=start,
            **_overnight_bodies(start),
        )
        # Retry gaps run from the error that closed the stream, never from the boot stream.
        attempts = [r["night"]["errorAt"], *r["night"]["created"][1:]]
        gaps = [b - a for a, b in zip(attempts, attempts[1:], strict=False)]
        assert len(gaps) >= 800, f"the page gave up on the stream: {len(gaps)} attempts overnight"
        assert max(gaps) <= 60_000, f"a retry gap of {max(gaps)} ms overnight"
        assert max(r["night"]["overlap"][1:]) == 0, "two streams were open at once"
        assert r["sunday"]["label"] == "Closed"
        assert r["up"]["header"]["label"] == "Closed", (
            "the page recovered the market-closed state on its own"
        )
        assert r["up"]["live"] == 1, (
            "the stream is open again within two minutes of the daemon returning"
        )
        assert r["beforeOpen"]["label"] == "Closed" and r["beforeOpen"]["countdown"] == "none"
        assert r["afterOpen"]["header"]["label"] == "Opening", (
            "the 09:30 open was not picked up by the poll"
        )
        assert r["afterOpen"]["header"]["title"] == OPENING_TITLE
        assert abs(_secs(r["afterOpen"]["header"]["countdownText"]) - r["afterOpen"]["truth"]) <= 1
        assert r["afterOpen"]["clock"].startswith("09:30:"), (
            f"the header clock reads {r['afterOpen']['clock']!r} at 09:30 EST"
        )
        assert r["running"]["label"] == "Live", (
            "an event on the resumed stream did not refresh the page"
        )
        assert r["live"] == 1
        assert r["reloads"] == 0 and r["navigations"] == []

    @pytest.mark.parametrize(
        "perf", [True, False], ids=["monotonic-clock-keeps-running", "monotonic-clock-stops"]
    )
    def test_a_laptop_that_slept_through_the_weekend_resumes_with_no_reload_or_flood(self, perf):
        start = _ms("2026-10-30T20:30:07Z")
        wake = _ms("2026-11-02T14:31:07Z")
        r = _run(
            SERVER.replace(f"Date.parse('{_DOWN}')", "Date.parse('2099-01-01T00:00:00Z')")
            + r"""
        await H.boot({ extras: true, overrides: [['/api/state', stateFor], ['/api/events', eventsFor]] });
        const friday = header();
        H.failNewStreams = true;                              // the stream closed before the lid did
        await H.streamError(0, true);
        H.failNewStreams = false;
        const n0 = H.fetchLog.length;
        H.sleepFor(__WAKE__ - now(), { perf: __PERF__ });
        let sent;
        if (__PERF__) {
          await H.wakeUp();                                   // the page's own overdue timers run once each
          await H.completeAnimations();
          sent = stateCount(n0);
        } else {
          await H.runTimers(65000);                           // no timer is overdue: they continue
          await H.completeAnimations();
          sent = stateCount(n0);
        }
        return { friday: friday, sent: sent, header: header(), live: H.liveStreams(),
                 asOf: H.text('#hero-data-as-of'), reloads: H.reloads, navigations: H.navigations.slice() };
        """.replace("__WAKE__", str(wake)).replace("__PERF__", "true" if perf else "false"),
            now_ms=start,
            **_overnight_bodies(start),
        )
        assert r["friday"]["label"] == "Closed"
        assert 1 <= r["sent"] <= 3, f"{r['sent']} state requests after waking: a flood or a stall"
        assert r["header"]["label"] == "Live", f"the label did not resume: {r['header']}"
        assert r["live"] == 1, "no open stream after the wake"
        assert r["reloads"] == 0 and r["navigations"] == []

    def test_a_stream_left_open_across_a_sleep_is_replaced_when_the_tab_returns(self):
        """Added beyond the plan: an open stream that slept cannot be trusted to still be connected."""
        start = _ms("2026-10-30T20:30:07Z")
        wake = _ms("2026-11-02T14:31:07Z")
        r = _run(
            SERVER.replace(f"Date.parse('{_DOWN}')", "Date.parse('2099-01-01T00:00:00Z')")
            + r"""
        await H.boot({ extras: true, overrides: [['/api/state', stateFor], ['/api/events', eventsFor]] });
        await H.setVisibility('hidden');
        H.sleepFor(__WAKE__ - now(), { perf: true });
        const before = H.eventSources.length;
        await H.setVisibility('visible');
        await H.runTimers(2000);
        return { before: before, after: H.eventSources.length, states: H.eventSources.map(function (e) { return e.readyState; }),
                 live: H.liveStreams() };
        """.replace("__WAKE__", str(wake)),
            now_ms=start,
            **_overnight_bodies(start),
        )
        assert r["after"] == r["before"] + 1, "the stream that slept was kept"
        assert r["states"][0] == 2, "the old stream must be closed once it is replaced"
        assert r["live"] == 1

    def test_a_poll_in_flight_when_the_lid_closed_does_not_wedge_the_page(self):
        start = _ms("2026-10-30T20:30:07Z")
        wake = _ms("2026-11-02T14:31:07Z")
        r = _run(
            SERVER.replace(f"Date.parse('{_DOWN}')", "Date.parse('2099-01-01T00:00:00Z')")
            + r"""
        await H.boot({ extras: true, overrides: [['/api/state', stateFor], ['/api/events', eventsFor]] });
        H.route('/api/state', { pending: true });
        await H.runTimers(30000);                             // a poll is sent and the lid closes
        H.sleepFor(__WAKE__ - now(), { perf: true });
        H.route('/api/state', stateFor);                      // the network is back
        await H.wakeUp();
        await H.runTimers(35000); await H.completeAnimations();
        return { header: header(), waiting: H.waiting('/api/state'), reloads: H.reloads };
        """.replace("__WAKE__", str(wake)),
            now_ms=start,
            **_overnight_bodies(start),
        )
        assert r["waiting"] == 0, "a request from before the sleep is still waiting"
        assert r["header"]["label"] == "Live", r["header"]
        assert r["reloads"] == 0
