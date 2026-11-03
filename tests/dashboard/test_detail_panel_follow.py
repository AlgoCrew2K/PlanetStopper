"""The open detail panel follows each poll and shows only the symphony it is open on.

Real call path: ``static/chrome.js`` and ``static/index.js`` run whole under node
(tests/dashboard/_index_js_harness.py) over the page ``GET /`` renders. The panel is opened the way a user
opens it (a press on the card, which runs the markup's inline handler), the 30 s poll is the page's own
interval, and the panel's four requests (``/api/chart/<id>``, ``/api/logs/<id>``, ``/api/settings``,
``/api/performance?...``) are answered by hand when order matters.

Figures are judged against the poll's entry formatted as the client formats them (``fmtPct``: signed,
two decimals); the panel's chart is the harness's recording ``Chart``, which counts constructs and
destroys and never claims a chart is visibly drawn. Derived bodies change one named field of a real
body: tests/fixtures/refresh_timing/03-c6/PROVENANCE.md.
"""

from __future__ import annotations

import copy

import pytest

from tests.dashboard import _index_js_harness as hx
from tests.dashboard import _refresh_timing_support as sup
from tests.dashboard._first_paint_dom import js_to_fixed

WORLD = "default"
UNTRIGGERED = hx.UNTRIGGERED_ID
TRIGGERED = hx.TRIGGERED_TP_ID
OTHER = hx.TRIGGERED_VWAP_ID
NO_DATA_TEXT = "No intraday data yet"
NOT_IN_VIEW = "not in the current view"

PRELUDE = r"""
const D = H.sandbox.document;
const T = function (id) { const e = D.getElementById(id); return e ? e.textContent.trim() : null; };
const figures = function () {
  return { live: T('dp-live-ret'), liveSub: T('dp-live-ret-sub'), stop: T('dp-stop-level'), peak: T('dp-shadow-peak'),
           alpha: T('dp-alpha'), alphaSub: T('dp-alpha-sub'), mc: T('dp-rm-mc'), dist: T('dp-rm-stop'),
           vol: T('dp-rm-vol'), para: T('dp-rm-para'), be: T('dp-rm-be'), vwap: T('dp-rm-vwap'),
           title: T('detail-panel-title') };
};
const open = async function (id) { return H.press('.sym-card[data-sym-id="' + id + '"]'); };
const poll = async function (body) {
  H.route('/api/state', { body: body });
  await H.tick(); await H.completeAnimations();
};
const intraday = function () { return H.summary()['intraday-canvas']; };
const html = function (id) { const e = D.getElementById(id); return e ? e.innerHTML : null; };
"""


def _world(name: str = WORLD) -> dict:
    return hx.get_world(name)


def _same(world: dict | None = None) -> dict:
    built = world or _world()
    return sup.with_stamp(built["state"], sup.render_stamp(built))


def _run(scenario: str, world: dict | None = None, **bodies) -> dict:
    built = world or _world()
    return hx.run_scenario(
        built,
        PRELUDE + scenario,
        scripts=sup.CHROME_AND_INDEX,
        extra_bodies=bodies,
        now_iso=hx.seeded_now_iso(built),
    )["result"]


def _pct(value: float) -> str:
    """The client's ``fmtPct`` for a value: signed, two decimals (the panel's figure format)."""
    shown = js_to_fixed([value], 2)[0]
    return ("+" if value >= 0 else "") + shown + "%"


def _entry(state: dict, symphony_id: str) -> dict:
    return sup.card_entry(state, symphony_id)


# ---------------------------------------------------------------------------
# S-8.1, S-8.2, S-8.5, S-8.9: the figures follow the poll, the chart stays
# ---------------------------------------------------------------------------


class TestThePanelsFiguresFollowThePoll:
    def test_the_live_return_is_the_polls_new_return_within_that_poll(self):
        before = _entry(_same(), UNTRIGGERED)
        assert before["current_return"] != 5.55
        r = _run(
            r"""
        await H.boot({ extras: true });
        await open(__ID__);
        const opened = figures();
        await poll(H.bodies.moved);
        return { opened: opened, after: figures() };
        """.replace("__ID__", repr(UNTRIGGERED)),
            moved=sup.with_entry(_same(), UNTRIGGERED, current_return=5.55),
        )
        assert r["opened"]["live"] != _pct(5.55)
        assert r["after"]["live"] == _pct(5.55)
        assert r["after"]["liveSub"] == "real-time"

    def test_the_stop_the_peak_and_the_risk_math_rows_take_the_new_state(self):
        entry = _entry(_same(), UNTRIGGERED)
        stop, peak, mc, vol = -1.25, 3.5, 44.0, 0.123
        moved = sup.with_entry(
            _same(), UNTRIGGERED, stop_trigger=stop, shadow_hwm=peak, mc_prob=mc, symphony_vol=vol
        )
        r = _run(
            r"""
        await H.boot({ extras: true });
        await open(__ID__);
        await H.completeAnimations();
        const opened = figures();
        await poll(H.bodies.moved);
        return { opened: opened, after: figures() };
        """.replace("__ID__", repr(UNTRIGGERED)),
            moved=moved,
        )
        after = r["after"]
        assert after["stop"] == _pct(stop)
        assert after["peak"] == _pct(peak)
        assert after["mc"] == js_to_fixed([mc], 1)[0] + "%"
        assert after["dist"] == js_to_fixed([peak - stop], 2)[0] + "%"
        assert after["vol"] == js_to_fixed([vol], 3)[0]
        assert after["mc"] != r["opened"]["mc"] or entry.get("mc_prob") == mc

    def test_the_row_only_the_chart_can_fill_keeps_the_text_the_chart_gave_it(self):
        r = _run(
            r"""
        await H.boot({ extras: true });
        await open(__ID__);
        await H.completeAnimations();
        const opened = figures();
        await poll(H.bodies.moved);
        return { opened: opened, after: figures() };
        """.replace("__ID__", repr(UNTRIGGERED)),
            moved=sup.with_entry(_same(), UNTRIGGERED, stop_trigger=-0.75, mc_prob=51.0),
        )
        assert r["after"]["vwap"] == r["opened"]["vwap"], (
            "a poll replaced the tape-derived VWAP row"
        )

    def test_a_symphony_that_exits_changes_the_label_and_the_figure_it_shows(self):
        exited = sup.with_entry(
            _same(),
            UNTRIGGERED,
            triggered=True,
            armed=False,
            status="triggered",
            triggered_at_return=-3.21,
            guard_alpha=0.8,
        )
        r = _run(
            r"""
        await H.boot({ extras: true });
        await open(__ID__);
        await poll(H.bodies.exited);
        return { after: figures() };
        """.replace("__ID__", repr(UNTRIGGERED)),
            exited=exited,
        )
        assert r["after"]["live"] == _pct(-3.21)
        assert r["after"]["liveSub"] == "frozen at exit"
        assert r["after"]["alpha"] == _pct(0.8)
        assert r["after"]["alphaSub"] == "saved vs hold"

    def test_a_poll_does_not_rebuild_the_chart_and_keeps_the_toggles(self):
        r = _run(
            r"""
        await H.boot({ extras: true });
        await open(__ID__);
        await H.completeAnimations();
        await H.press('#toggle-vwap');
        const built = intraday();
        const hiddenBefore = H.chartFor('intraday-canvas').data.datasets[4].hidden;
        await poll(H.bodies.moved);
        const after = intraday();
        return { constructs: [built.constructs, after.constructs], destroys: [built.destroys, after.destroys],
                 hiddenBefore: hiddenBefore, hiddenAfter: H.chartFor('intraday-canvas').data.datasets[4].hidden,
                 live: after.live };
        """.replace("__ID__", repr(UNTRIGGERED)),
            moved=sup.with_entry(_same(), UNTRIGGERED, current_return=2.5),
        )
        assert r["constructs"][0] == r["constructs"][1] == 1
        assert r["destroys"][0] == r["destroys"][1] == 0
        assert r["hiddenBefore"] is False, "the toggle must have shown the VWAP overlay"
        assert r["hiddenAfter"] is False and r["live"] is True

    def test_a_failed_poll_leaves_the_figures_alone(self):
        r = _run(
            r"""
        await H.boot({ extras: true });
        await open(__ID__);
        await H.completeAnimations();
        const before = figures();
        H.route('/api/state', { status: 500, body: { status: 'error', message: 'An internal error occurred' } });
        await H.tick(); await H.completeAnimations();
        return { before: before, after: figures() };
        """.replace("__ID__", repr(UNTRIGGERED)),
        )
        assert r["after"] == r["before"]

    def test_the_title_is_the_display_name_before_and_after_a_poll(self):
        name = _entry(_world()["state"], UNTRIGGERED).get("name") or _world()["bot_state"][
            UNTRIGGERED
        ].get("name")
        r = _run(
            r"""
        await H.boot({ extras: true });
        await open(__ID__);
        const opened = T('detail-panel-title');
        await poll(H.bodies.same);
        return { opened: opened, after: T('detail-panel-title') };
        """.replace("__ID__", repr(UNTRIGGERED)),
            same=_same(),
        )
        assert r["opened"] == r["after"] == name


# ---------------------------------------------------------------------------
# S-8.6, S-8.7: a symphony that leaves the view, a panel opened before the first poll
# ---------------------------------------------------------------------------


class TestAPanelWhoseSymphonyLeavesOrArrives:
    def test_a_symphony_that_leaves_the_view_keeps_its_figures_and_says_so(self):
        gone = sup.without_symphony(_same(), UNTRIGGERED)
        r = _run(
            r"""
        await H.boot({ extras: true });
        await open(__ID__);
        await H.completeAnimations();
        const before = figures();
        await poll(H.bodies.gone);
        const away = figures();
        await poll(H.bodies.same);
        return { before: before, away: away, back: figures() };
        """.replace("__ID__", repr(UNTRIGGERED)),
            gone=gone,
            same=_same(),
        )
        assert r["away"]["liveSub"] == NOT_IN_VIEW
        for key in ("live", "stop", "peak", "alpha", "mc", "title"):
            assert r["away"][key] == r["before"][key], (
                f"{key} changed when the symphony left the view"
            )
        assert r["back"]["liveSub"] == r["before"]["liveSub"], (
            "the cue stayed after the symphony came back"
        )

    def test_a_panel_opened_before_the_first_poll_fills_from_it_and_fetches_once(self):
        moved = sup.with_entry(_same(), UNTRIGGERED, current_return=4.25)
        r = _run(
            r"""
        await H.boot({ extras: true, statePending: true });
        await open(__ID__);
        const empty = figures();
        const n0 = H.fetchLog.length;
        await H.releasePending('/api/state', { body: H.bodies.moved });
        await H.completeAnimations();
        const asked = H.fetchLog.slice(n0).map(function (e) { return e.url; });
        return { empty: empty, filled: figures(),
                 logs: asked.filter(function (u) { return u === '/api/logs/' + __ID__; }).length,
                 performance: asked.filter(function (u) { return u.indexOf('/api/performance') === 0 && u.indexOf(__ID__) >= 0; }).length };
        """.replace("__ID__", repr(UNTRIGGERED)),
            moved=moved,
        )
        assert r["filled"]["live"] == _pct(4.25), "the first poll did not fill the open panel"
        assert r["empty"]["title"] != "Symphony detail", (
            "a panel opened before the first poll must carry the symphony's own title, not the default"
        )
        assert r["filled"]["title"] == _world()["bot_state"][UNTRIGGERED]["name"], (
            "the title stayed the default after the first poll named the symphony"
        )
        assert r["logs"] == 1 and r["performance"] == 1, "the panel's own requests must run once"


# ---------------------------------------------------------------------------
# S-8.3, S-8.4: only the panel's own symphony is ever shown
# ---------------------------------------------------------------------------


def _marked_chart(chart: dict, marker: str, drop: int = 0) -> dict:
    out = copy.deepcopy(chart)
    out["data"] = out["data"][drop:]
    out["marker"] = marker
    return out


class TestThePanelShowsOnlyItsOwnSymphony:
    def test_opening_a_symphony_with_no_tape_shows_its_empty_state_and_not_the_previous_chart(self):
        world = _world("mixed")
        r = hx.run_scenario(
            world,
            PRELUDE
            + r"""
        await H.boot({ extras: true });
        await open(__A__);
        await H.completeAnimations();
        const a = intraday();
        await open(__B__);
        await H.completeAnimations();
        const b = intraday();
        return { a: { live: a.live, constructs: a.constructs, destroys: a.destroys },
                 b: { live: b.live, constructs: b.constructs, destroys: b.destroys, status: b.status } };
        """.replace("__A__", repr(TRIGGERED)).replace("__B__", repr(UNTRIGGERED)),
            scripts=sup.CHROME_AND_INDEX,
            now_iso=hx.seeded_now_iso(world),
        )["result"]
        assert r["a"]["live"] is True, "the first symphony's chart must have been drawn"
        assert r["b"]["live"] is False, "the previous symphony's chart is still on the panel"
        assert r["b"]["destroys"] == r["b"]["constructs"]
        assert NO_DATA_TEXT in r["b"]["status"]

    def test_answers_for_the_first_symphony_that_land_after_the_second_are_discarded(self):
        world = _world()
        charts = world["charts"]
        a_chart = _marked_chart(charts[TRIGGERED], "A-chart", drop=5)
        b_chart = _marked_chart(charts[UNTRIGGERED], "B-chart")
        assert len(a_chart["data"]) != len(b_chart["data"]), "the two tapes must tell apart"
        settings = {
            "symphonies": {
                TRIGGERED: {"params": {"ONLY_FIRST_PARAM": 1}, "locked_vars": []},
                UNTRIGGERED: {"params": {"ONLY_SECOND_PARAM": 2}, "locked_vars": []},
            }
        }
        logs_a = [
            {
                "timestamp": "2026-10-21T10:00:00",
                "event_type": "info",
                "message": "FIRST_ONLY_EVENT",
            }
        ]
        logs_b = [
            {
                "timestamp": "2026-10-21T10:01:00",
                "event_type": "info",
                "message": "SECOND_ONLY_EVENT",
            }
        ]

        def perf(sharpe: float) -> dict:
            return {
                "shadow_metrics": {
                    "sharpe": sharpe,
                    "sortino": sharpe,
                    "max_drawdown": 0.1,
                    "volatility": 0.2,
                },
                "live_metrics": {
                    "sharpe": 0.5,
                    "sortino": 0.5,
                    "max_drawdown": 0.2,
                    "volatility": 0.3,
                },
            }

        r = _run(
            r"""
        await H.boot({ extras: true });
        H.route(/^\/api\/(chart|logs)\//, { pending: true });
        H.route('/api/settings', { pending: true });
        H.route(/^\/api\/performance/, { pending: true });
        await open(__A__);
        await open(__B__);
        const waiting = H.pending.filter(function (p) { return !p.done; }).length;
        const url = function (kind, id) { return '/api/' + kind + '/' + id; };
        const perfUrl = function (id) { return '/api/performance?scope=symphony&symphony_id=' + id + '&days=60'; };
        await H.releasePending(url('chart', __B__), { body: H.bodies.chartB });
        await H.releasePending(url('logs', __B__), { body: H.bodies.logsB });
        await H.releasePending(perfUrl(__B__), { body: H.bodies.perfB });
        await H.releaseNth('/api/settings', 1, { body: H.bodies.settings });
        await H.releasePending(url('chart', __A__), { body: H.bodies.chartA });
        await H.releasePending(url('logs', __A__), { body: H.bodies.logsA });
        await H.releasePending(perfUrl(__A__), { body: H.bodies.perfA });
        await H.releaseNth('/api/settings', 0, { body: H.bodies.settings });
        await H.completeAnimations();
        return { waiting: waiting, points: H.chartFor('intraday-canvas') ? H.chartFor('intraday-canvas').data.labels.length : null,
                 logs: html('events-timeline'), vars: html('vars-section'), risk: html('detail-risk-profile'),
                 title: T('detail-panel-title') };
        """.replace("__A__", repr(TRIGGERED)).replace("__B__", repr(UNTRIGGERED)),
            chartA=a_chart,
            chartB=b_chart,
            logsA=logs_a,
            logsB=logs_b,
            perfA=perf(1.11),
            perfB=perf(2.22),
            settings=settings,
        )
        assert r["waiting"] == 8, "each opening asks for its four things"
        assert r["points"] == len(b_chart["data"]), (
            "the first symphony's tape replaced the second's"
        )
        assert "SECOND_ONLY_EVENT" in r["logs"] and "FIRST_ONLY_EVENT" not in r["logs"]
        assert "ONLY_SECOND_PARAM" in r["vars"] and "ONLY_FIRST_PARAM" not in r["vars"]
        assert "2.22" in r["risk"] and "1.11" not in r["risk"]
        assert r["title"] != "Symphony detail"

    def test_the_symphony_the_panel_shows_is_the_one_opened_last(self):
        r = _run(
            r"""
        await H.boot({ extras: true });
        await open(__A__);
        await open(__B__);
        await poll(H.bodies.moved);
        return { live: T('dp-live-ret') };
        """.replace("__A__", repr(TRIGGERED)).replace("__B__", repr(UNTRIGGERED)),
            moved=sup.with_entry(
                sup.with_entry(_same(), UNTRIGGERED, current_return=7.5),
                TRIGGERED,
                triggered_at_return=-9.25,
            ),
        )
        assert r["live"] == _pct(7.5), "a poll wrote the first symphony's figures over the second's"
