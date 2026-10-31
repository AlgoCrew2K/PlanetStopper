"""An error, refused or timed-out poll keeps every value on ``/``, shows the connection-lost cue and retries.

Real call path: ``static/chrome.js`` and ``static/index.js`` run whole under node
(tests/dashboard/_index_js_harness.py, ``scripts=CHROME_AND_INDEX``) over the page ``GET /`` renders.
The 30 s poll is the page's own interval; the failing answers are the real app's: its 500 body, its
401 and 503 refusals from the auth gate, Werkzeug's 403 page from the CSRF gate (``error_response``).

"Keeps every value" is read from the page: counts, section badges, hero points, the headline and vol
rows and every card's text are captured before and after the failing poll and must be equal.
Provenance of the error bodies and the derived ones: tests/fixtures/refresh_timing/03-c2/PROVENANCE.md.
"""

from __future__ import annotations

import pytest

from tests.dashboard import _index_js_harness as hx
from tests.dashboard import _refresh_timing_support as sup

WORLD = "default"
SCRIPTS = sup.CHROME_AND_INDEX

# Everything a failing poll must leave alone, read from the DOM.
SNAP = r"""
const snap = function () {
  const cards = {};
  H.sandbox.document.querySelectorAll('.sym-card[data-sym-id]').forEach(function (c) {
    cards[c.getAttribute('data-sym-id')] = c.textContent.replace(/\s+/g, ' ').trim();
  });
  const hero = H.summary()['cum-chart'];
  return {
    tracked: H.text('#hero-tracked'), armed: H.text('#hero-armed'), triggered: H.text('#hero-triggered'),
    active: H.text('[data-testid="active-section-count"]'), standby: H.text('[data-testid="standby-section-count"]'),
    ga: H.guardAlpha(), vol: H.vol(), cards: cards,
    heroLabels: hero.labels, heroBot: hero.datasets && hero.datasets[0].data
  };
};
"""


def _world() -> dict:
    return hx.get_world(WORLD)


@pytest.fixture(autouse=True)
def _world_captured_before_the_failure_is_staged():
    """The world is captured through the app's own routes; a staged failure (auth gate on, the
    state route raising) must not be what builds it, whichever test runs first."""
    _world()


def _stamp() -> str:
    return sup.render_stamp(_world())


def _bodies(**extra) -> dict:
    world = _world()
    equal = sup.with_stamp(world["state"], _stamp())
    later = sup.with_stamp(world["next"]["state"], sup.later_stamp(_stamp()))
    return {
        "pollEqual": equal,
        "pollNew": later,
        "eventChanged": sup.event_payload(changed=True),
        **extra,
    }


def _run(scenario: str, **extra) -> dict:
    return sup.run(scenario, world=WORLD, scripts=SCRIPTS, extra_bodies=_bodies(**extra))


FAILURE_KINDS = ["500", "503", "403-html", "network", "200-not-json", "200-without-data"]


def _failure_spec(kind: str, monkeypatch) -> dict:
    if kind == "network":
        return {"reject": True}
    if kind == "200-not-json":
        html = sup.error_response("403-html", monkeypatch)
        return {"status": 200, "text": html["text"]}
    if kind == "200-without-data":
        refused = sup.error_response("500", monkeypatch)
        return {"status": 200, "body": refused["body"]}
    return sup.as_route_spec(sup.error_response(kind, monkeypatch))


# ---------------------------------------------------------------------------
# S-2.1, S-2.3, S-2.4, S-2.5, S-2.7: nothing that is not a data body is drawn as data
# ---------------------------------------------------------------------------


class TestAFailedPollKeepsEveryValue:
    @pytest.mark.parametrize("kind", FAILURE_KINDS)
    def test_values_stay_and_the_header_and_as_of_say_connection_lost(self, kind, monkeypatch):
        failure = _failure_spec(kind, monkeypatch)
        scenario = (
            r"""
        await H.boot({ extras: true, state: H.bodies.pollEqual });
        """
            + SNAP
            + r"""
        const before = snap();
        H.route('/api/state', H.bodies.failure);
        await H.runTimers(30000); await H.completeAnimations();
        return { before: before, after: snap(), label: H.text('#engine-status-label'),
                 asOf: H.text('#hero-data-as-of') };
        """
        )
        r = _run(scenario, failure=failure)
        assert r["after"] == r["before"], f"{kind}: a failed poll changed a value on the page"
        assert r["label"] == "Connection Lost"
        assert r["asOf"] == sup.CONNECTION_LOST_AS_OF_PREFIX + _stamp(), (
            "the as-of keeps the last good stamp beside the cue"
        )

    def test_the_cue_uses_token_colours_and_hides_the_countdown(self):
        scenario = r"""
        await H.boot({ extras: true, state: H.bodies.pollEqual });
        H.route('/api/state', { reject: true });
        await H.runTimers(30000);
        return { label: H.el('#engine-status-label'), dot: H.el('#engine-status-dot'),
                 countdown: H.el('#next-run-countdown') };
        """
        r = _run(scenario)
        assert r["label"]["style"].get("color") == "var(--studio-neg)", r["label"]["style"]
        assert r["dot"]["style"].get("background") == "var(--studio-neg)", r["dot"]["style"]
        assert r["countdown"]["style"].get("display") == "none", (
            "Connection Lost hides the countdown"
        )

    def test_a_401_sends_the_page_to_login(self, monkeypatch):
        refusal = sup.as_route_spec(sup.error_response("401", monkeypatch))
        assert refusal["status"] == 401
        scenario = (
            r"""
        await H.boot({ extras: true, state: H.bodies.pollEqual });
        """
            + SNAP
            + r"""
        const before = snap();
        H.route('/api/state', H.bodies.failure);
        await H.runTimers(30000);
        return { navigations: H.navigations.slice(), before: before, after: snap() };
        """
        )
        r = _run(scenario, failure=refusal)
        assert r["navigations"] == ["/login"], r["navigations"]
        assert r["after"] == r["before"], "the values must not be redrawn on the way out"


# ---------------------------------------------------------------------------
# S-2.6: no answer within 20 s
# ---------------------------------------------------------------------------


class TestARequestWithNoAnswerIsAbandoned:
    def test_after_20_seconds_the_page_shows_the_failed_state_and_ignores_a_late_answer(self):
        scenario = (
            r"""
        await H.boot({ extras: true, state: H.bodies.pollEqual });
        """
            + SNAP
            + r"""
        const before = snap();
        H.route('/api/state', { pending: true });
        await H.runTimers(30000);                      // the poll is sent and is not answered
        await H.runTimers(19000);
        const early = H.text('#engine-status-label');
        await H.runTimers(2000);                       // 21 s after it was sent
        const abandoned = { label: H.text('#engine-status-label'), asOf: H.text('#hero-data-as-of'),
                            snap: snap() };
        await H.releasePending('/api/state', { body: H.bodies.pollNew });   // a late answer, if it can still arrive
        await H.completeAnimations();
        return { before: before, early: early, abandoned: abandoned,
                 later: { label: H.text('#engine-status-label'), asOf: H.text('#hero-data-as-of'), snap: snap() } };
        """
        )
        r = _run(scenario)
        assert r["early"] != "Connection Lost", "the request was abandoned before 20 s"
        assert r["abandoned"]["label"] == "Connection Lost"
        assert r["abandoned"]["asOf"] == sup.CONNECTION_LOST_AS_OF_PREFIX + _stamp()
        assert r["abandoned"]["snap"] == r["before"]
        assert r["later"] == r["abandoned"], "a late answer to an abandoned request was drawn"

    def test_every_state_request_carries_a_signal_the_page_can_abort(self):
        scenario = r"""
        await H.boot({ extras: true, state: H.bodies.pollEqual });
        await H.runTimers(30000);
        return { states: H.requestsTo(/^\/api\/state/).map(function (e) { return e.hasSignal; }) };
        """
        r = _run(scenario)
        assert r["states"] and all(r["states"]), (
            "a request with no signal can never be abandoned: " + str(r["states"])
        )


# ---------------------------------------------------------------------------
# S-2.8: the next data poll clears the cue
# ---------------------------------------------------------------------------


def test_the_next_data_poll_updates_every_value_and_clears_the_cue():
    scenario = r"""
    await H.boot({ extras: true, state: H.bodies.pollEqual });
    H.route('/api/state', { status: 500, body: { status: 'error', message: 'An internal error occurred' } });
    await H.runTimers(30000);
    const failed = { label: H.text('#engine-status-label'), asOf: H.text('#hero-data-as-of') };
    H.route('/api/state', { body: H.bodies.pollNew });
    await H.runTimers(30000); await H.completeAnimations();
    return { failed: failed, label: H.text('#engine-status-label'), asOf: H.text('#hero-data-as-of'),
             tracked: H.text('#hero-tracked'), states: H.requests(/^\/api\/state/).length };
    """
    r = _run(scenario)
    assert r["failed"]["label"] == "Connection Lost"
    assert r["label"] == "Live", "the cue must clear when data arrives"
    assert r["asOf"] == sup.AS_OF_PREFIX + sup.later_stamp(_stamp()), r["asOf"]
    assert r["tracked"] == str(_bodies()["pollNew"]["meta"]["tracked"])
    assert r["states"] == 3, "the failed poll is retried at the next poll"


# ---------------------------------------------------------------------------
# S-2.9: the waiting body is data
# ---------------------------------------------------------------------------


def test_the_waiting_body_with_its_notice_is_data_not_an_error(monkeypatch):
    body = sup.waiting_body(monkeypatch)
    assert body.get("status") == "waiting" and body.get("notice"), body
    scenario = r"""
    await H.boot({ extras: true, state: H.bodies.waiting });
    await H.completeAnimations();
    return { label: H.text('#engine-status-label'), asOf: H.text('#hero-data-as-of'),
             tracked: H.text('#hero-tracked'), count: H.attrs().refreshCount };
    """
    r = _run(scenario, waiting=body)
    assert r["label"] != "Connection Lost", "the waiting body was treated as an error"
    assert not r["asOf"].startswith("connection lost"), r["asOf"]
    assert r["count"] == "1", "the waiting body counts as a handled refresh"


def test_the_waiting_body_after_a_failed_poll_clears_the_cue(monkeypatch):
    """The waiting body is data: it ends the failed state like any other data body."""
    body = sup.waiting_body(monkeypatch)
    scenario = r"""
    await H.boot({ extras: true, state: H.bodies.pollEqual });
    H.route('/api/state', { reject: true });
    await H.runTimers(30000);
    const failed = H.text('#engine-status-label');
    H.route('/api/state', { body: H.bodies.waiting });
    await H.runTimers(30000); await H.completeAnimations();
    return { failed: failed, label: H.text('#engine-status-label'), asOf: H.text('#hero-data-as-of') };
    """
    r = _run(scenario, waiting=body)
    assert r["failed"] == "Connection Lost"
    assert r["label"] != "Connection Lost", "the waiting body was treated as an error"
    assert not r["asOf"].startswith("connection lost"), r["asOf"]


# ---------------------------------------------------------------------------
# S-2.10, S-2.13: a superseded request never counts and never shows a cue
# ---------------------------------------------------------------------------


class TestOnlyTheNewestRequestCounts:
    def test_an_older_error_that_lands_after_a_newer_success_is_ignored(self):
        scenario = r"""
        await H.boot({ extras: true, state: H.bodies.pollEqual });
        H.route('/api/state', { pending: true });
        await H.sse('cycle-complete', H.bodies.eventChanged);     // the older request
        await H.sse('cycle-complete', H.bodies.eventChanged);     // the newer request
        const issued = H.waiting('/api/state');
        await H.releaseNth('/api/state', 1, { body: H.bodies.pollNew });      // the newer succeeds
        await H.completeAnimations();
        const mid = { label: H.text('#engine-status-label'), asOf: H.text('#hero-data-as-of') };
        await H.releaseNth('/api/state', 0, { status: 500, body: { status: 'error', message: 'An internal error occurred' } });
        await H.completeAnimations();
        return { issued: issued, mid: mid, label: H.text('#engine-status-label'), asOf: H.text('#hero-data-as-of') };
        """
        r = _run(scenario)
        assert r["issued"] == 2, "both requests must be in flight for this to test ordering"
        assert r["mid"]["label"] != "Connection Lost"
        assert r["label"] == r["mid"]["label"], (
            "the older error raised the cue after a newer success"
        )
        assert r["asOf"] == r["mid"]["asOf"] == sup.AS_OF_PREFIX + sup.later_stamp(_stamp())

    def test_the_counter_adds_one_for_a_rendered_poll(self):
        scenario = r"""
        await H.boot({ extras: true, state: H.bodies.pollEqual }); await H.completeAnimations();
        const n = Number(H.attrs().refreshCount);
        H.route('/api/state', { body: H.bodies.pollNew });
        await H.runTimers(30000); await H.completeAnimations();
        return { n: n, after: Number(H.attrs().refreshCount) };
        """
        r = _run(scenario)
        assert r["after"] == r["n"] + 1

    def test_the_counter_adds_one_for_a_handled_failure(self):
        scenario = r"""
        await H.boot({ extras: true, state: H.bodies.pollEqual }); await H.completeAnimations();
        const n = Number(H.attrs().refreshCount);
        H.route('/api/state', { status: 500, body: { status: 'error', message: 'An internal error occurred' } });
        await H.runTimers(30000); await H.completeAnimations();
        return { n: n, after: Number(H.attrs().refreshCount), label: H.text('#engine-status-label') };
        """
        r = _run(scenario)
        assert r["label"] == "Connection Lost"
        assert r["after"] == r["n"] + 1

    @pytest.mark.parametrize(
        "older",
        [
            "{ body: H.bodies.pollNew }",
            "{ reject: true }",
            "{ status: 500, body: { status: 'error' } }",
        ],
    )
    def test_an_older_request_that_lands_while_a_newer_one_is_pending_does_not_count(self, older):
        scenario = (
            r"""
        await H.boot({ extras: true, state: H.bodies.pollEqual }); await H.completeAnimations();
        const n = Number(H.attrs().refreshCount);
        H.route('/api/state', { pending: true });
        await H.sse('cycle-complete', H.bodies.eventChanged);
        await H.sse('cycle-complete', H.bodies.eventChanged);
        await H.releaseNth('/api/state', 0, __OLDER__);
        await H.completeAnimations();
        const afterOlder = Number(H.attrs().refreshCount);
        await H.releaseNth('/api/state', 0, { body: H.bodies.pollNew });
        await H.completeAnimations();
        return { n: n, afterOlder: afterOlder, afterNewer: Number(H.attrs().refreshCount) };
        """
        ).replace("__OLDER__", older)
        r = _run(scenario)
        assert r["afterOlder"] == r["n"], "a superseded request was counted"
        assert r["afterNewer"] == r["n"] + 1, "the newest request's outcome counts once"


# ---------------------------------------------------------------------------
# S-2.14: Force run, and S-2.15: the first request of the page
# ---------------------------------------------------------------------------


def test_force_run_whose_refresh_fails_shows_the_failed_state_and_posts_once():
    scenario = r"""
    await H.boot({ extras: true, state: H.bodies.pollEqual });
    H.route('/api/trigger', { body: H.bodies.trigger });
    H.route('/api/state', { status: 500, body: { status: 'error', message: 'An internal error occurred' } });
    await H.click('[data-testid="force-run-btn"]'); await H.completeAnimations();
    return { label: H.text('#engine-status-label'), asOf: H.text('#hero-data-as-of'),
             posts: H.requestsTo(/^\/api\/trigger$/).map(function (e) { return e.method; }) };
    """
    import app as app_module

    trigger = app_module.app.test_client().post("/api/trigger").get_json()
    r = _run(scenario, trigger=trigger)
    assert r["label"] == "Connection Lost"
    assert r["asOf"] == sup.CONNECTION_LOST_AS_OF_PREFIX + _stamp()
    assert r["posts"] == ["POST"], "Force run's POST must be sent once and never re-sent"


def test_a_failed_first_request_leaves_the_server_rendered_values_with_the_cue():
    scenario = (
        r"""
    """
        + SNAP
        + r"""
    // The first paint draws the charts; a failed request must leave the rendered numbers alone.
    const core = function () {
      const c = snap(); delete c.heroLabels; delete c.heroBot; return c;
    };
    const render = core();
    await H.boot({ extras: true, overrides: [['/api/state', { status: 500, body: { status: 'error', message: 'An internal error occurred' } }]] });
    return { render: render, after: core(), label: H.text('#engine-status-label'),
             asOf: H.text('#hero-data-as-of'), count: H.attrs().refreshCount };
    """
    )
    r = _run(scenario)
    assert r["after"] == r["render"], "a failed first request changed what the server rendered"
    assert r["label"] == "Connection Lost"
    assert r["asOf"] == sup.CONNECTION_LOST_AS_OF_PREFIX + _stamp(), (
        "the last known stamp is the one the server rendered"
    )
    assert r["count"] == "1", "a handled failure still counts, so a capture never hangs"
