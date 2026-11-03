"""Sparklines and MC dials on ``/`` show only the current session's readings, and a refresh with no new
data asks for no chart.

Real call path: the whole of ``static/index.js`` runs under node (tests/dashboard/_index_js_harness.py)
over the page ``GET /`` renders, with the bodies the real ``/api/state`` and ``/api/chart/<id>`` routes
return for a seeded database. Charts are requested by the page's own first paint, 30 s poll and event;
answers are released by hand when order matters.

The dial's expected text is the server render's for the same card (the render and the poll print one
text); a caption is judged by the span the card holds (outside the box the chart sizes itself to). Derived bodies change one
named field of a real body: tests/fixtures/refresh_timing/03-c4/PROVENANCE.md.
"""

from __future__ import annotations

import copy
import datetime as dt

import pytest

from tests.dashboard import _index_js_harness as hx
from tests.dashboard import _refresh_timing_support as sup

WORLD = "default"
DASH = "—"
NO_DATA_TEXT = "No intraday data yet"
NOT_A_SYMPHONY = "zz-post-mortem-meta"
UNTRIGGERED = hx.UNTRIGGERED_ID
TRIGGERED = hx.TRIGGERED_TP_ID
CHART_URL = r"/^\/api\/chart\/(.+)$/"


def _world(name: str = WORLD) -> dict:
    return hx.get_world(name)


def _stamp() -> str:
    return sup.render_stamp(_world())


def _same_stamp(state: dict | None = None) -> dict:
    return sup.with_stamp(state or _world()["state"], _stamp())


def _later(minutes: int = 1, state: dict | None = None) -> dict:
    return sup.with_stamp(
        state or _world()["next"]["state"], sup.later_stamp(_stamp(), minutes=minutes)
    )


def _with_mc(state: dict, symphony_id: str, mc_prob) -> dict:
    return sup.with_entry(state, symphony_id, mc_prob=mc_prob)


def _chart_with_session(chart: dict, session_date: str | None, et_date: str | None) -> dict:
    out = copy.deepcopy(chart)
    if session_date is not None:
        out["session_date"] = session_date
    if et_date is not None:
        out["et_date"] = et_date
    return out


def _chart_with_last_mc(chart: dict, mc: float) -> dict:
    out = copy.deepcopy(chart)
    out["data"][-1]["mc_prob"] = mc
    return out


def _chart_truncated(chart: dict, keep: int) -> dict:
    out = copy.deepcopy(chart)
    out["data"] = out["data"][:keep]
    return out


def _server_dial_text(symphony_id: str) -> str:
    tree = hx.parse_html(_world()["html"])
    svg = hx.find_all(
        tree,
        lambda n: (
            n["a"].get("data-testid") == "mc-dial" and n["a"].get("data-sym-id") == symphony_id
        ),
    )[0]
    text = hx.find_all(svg, lambda n: "mc-text" in n["a"].get("class", "").split())[0]
    return " ".join(hx.tree_text(text).split())


def _run(scenario: str, world: str = WORLD, **bodies) -> dict:
    return sup.run(scenario, world=world, scripts=sup.INDEX_ONLY, extra_bodies=bodies)


PRELUDE = r"""
const chartRequests = function (n0) {
  return H.fetchLog.slice(n0 || 0).filter(function (e) { return /^\/api\/chart\//.test(e.url); })
    .map(function (e) { return e.url.slice('/api/chart/'.length); });
};
const caption = function (id) {
  const canvas = H.sandbox.document.querySelector('[data-testid="card-spark"][data-sym-id="' + id + '"]');
  let card = canvas;
  while (card && card.getAttribute && card.getAttribute('data-testid') !== 'sym-card') card = card.parentNode;
  const e = card ? card.querySelector('.spark-session') : null;
  if (!e) return null;
  return { text: e.textContent.trim(), hidden: e.hasAttribute('hidden') || e.style.display === 'none',
           classes: e.className };
};
// The section header lines that state a session once, and the cards that still name their own.
const headerCaptions = function () {
  return Array.prototype.slice.call(
    H.sandbox.document.querySelectorAll('[data-field="section-session"]')).map(function (e) {
    return { text: e.textContent.trim(), hidden: e.hasAttribute('hidden') || e.style.display === 'none' };
  });
};
const cardCaptions = function () {
  return Array.prototype.slice.call(
    H.sandbox.document.querySelectorAll('[data-testid="card-spark"]')).map(function (c) {
    return caption(c.getAttribute('data-sym-id'));
  }).filter(function (e) { return e && !e.hidden && e.text; });
};
const poll = async function (body) {
  H.route('/api/state', { body: body });
  await H.tick(); await H.completeAnimations();
};
"""


def _run_p(scenario: str, world: str = WORLD, **bodies) -> dict:
    return _run(PRELUDE + scenario, world=world, **bodies)


# ---------------------------------------------------------------------------
# S-9.9, S-9.10: which refreshes ask for charts, and for which keys
# ---------------------------------------------------------------------------


def _with_non_symphony_key(state: dict) -> dict:
    out = copy.deepcopy(state)
    for key in ("bot_state", "state"):
        if isinstance(out.get(key), dict):
            out[key]["post_mortem_run"] = {"id": NOT_A_SYMPHONY, "ran": True}
    return out


class TestWhichRefreshesAskForCharts:
    def test_a_refresh_with_a_new_stamp_asks_for_one_chart_per_card_and_none_for_another_key(self):
        symphony_ids = sorted(_world()["ids"])
        r = _run_p(
            r"""
        await H.boot({ extras: true, state: H.bodies.same });
        const n0 = H.fetchLog.length;
        await poll(H.bodies.fresh);
        return { asked: chartRequests(n0) };
        """,
            same=_with_non_symphony_key(_same_stamp()),
            fresh=_with_non_symphony_key(_later()),
        )
        assert sorted(r["asked"]) == symphony_ids, "one request per card, in no other key's name"

    def test_the_first_poll_after_load_and_a_later_poll_with_the_same_stamp_ask_for_no_chart(self):
        symphony_ids = sorted(_world()["ids"])
        r = _run_p(
            r"""
        await H.boot({ extras: true, state: H.bodies.same });
        const atLoad = chartRequests(0);
        const n0 = H.fetchLog.length;
        await poll(H.bodies.same);
        await poll(H.bodies.same);
        return { atLoad: atLoad, later: chartRequests(n0) };
        """,
            same=_same_stamp(),
        )
        assert sorted(r["atLoad"]) == symphony_ids, (
            "the first paint asks once per card, and the first poll (the render's own stamp) "
            "does not ask again"
        )
        assert r["later"] == []

    def test_after_a_new_stamp_the_next_poll_with_that_stamp_asks_for_nothing(self):
        r = _run_p(
            r"""
        await H.boot({ extras: true, state: H.bodies.same });
        await poll(H.bodies.fresh);
        const n0 = H.fetchLog.length;
        await poll(H.bodies.fresh);
        return { asked: chartRequests(n0) };
        """,
            same=_same_stamp(),
            fresh=_later(),
        )
        assert r["asked"] == []

    def test_a_cycle_event_that_brings_a_new_stamp_asks_for_the_cards_charts(self):
        symphony_ids = sorted(_world()["ids"])
        r = _run_p(
            r"""
        await H.boot({ extras: true, state: H.bodies.same });
        const n0 = H.fetchLog.length;
        H.route('/api/state', { body: H.bodies.fresh });
        await H.sse('cycle-complete', H.bodies.event); await H.completeAnimations();
        return { asked: chartRequests(n0) };
        """,
            same=_same_stamp(),
            fresh=_later(),
            event=sup.event_payload(changed=True),
        )
        assert sorted(r["asked"]) == symphony_ids


# ---------------------------------------------------------------------------
# S-9.6: an older chart answer never replaces a newer one
# ---------------------------------------------------------------------------


class TestAnOlderChartAnswerIsDiscarded:
    def test_chart_answers_for_two_refreshes_that_land_out_of_order_leave_the_newer_tape(self):
        world = _world()
        full = world["charts"][UNTRIGGERED]
        assert len(full["data"]) > 10
        older = _chart_truncated(full, len(full["data"]) - 5)
        url = f"/api/chart/{UNTRIGGERED}"
        r = _run_p(
            r"""
        await H.boot({ extras: true, state: H.bodies.same });
        H.route(__CHART__, { pending: true });
        await poll(H.bodies.freshA);
        await poll(H.bodies.freshB);
        const key = 'spark:' + __ID__;
        const waiting = H.waiting(__URL__);
        await H.releaseNth(__URL__, 1, { body: H.bodies.newer });   // the newer refresh answers first
        await H.releaseNth(__URL__, 0, { body: H.bodies.older });   // the older one lands last
        await H.completeAnimations();
        return { waiting: waiting, points: H.summary()[key].labels.length };
        """.replace("__CHART__", CHART_URL)
            .replace("__ID__", repr(UNTRIGGERED))
            .replace("__URL__", repr(url)),
            same=_same_stamp(),
            freshA=_later(1),
            freshB=_later(2),
            newer=full,
            older=older,
        )
        assert r["waiting"] == 2, "each refresh asked for the card's chart once"
        assert r["points"] == len(full["data"]), "the older answer replaced the newer tape"


# ---------------------------------------------------------------------------
# S-9.3, S-9.4, S-9.5: the dial reads the poll's reading, never an older tape reading
# ---------------------------------------------------------------------------


def _tape_mc_other_than_the_renders() -> float:
    """A tape reading that prints differently from the server render's dial for the card."""
    shown = _server_dial_text(UNTRIGGERED)
    assert shown.endswith("%"), f"the armed card must render a reading, got {shown!r}"
    return 41.0 if shown != "41%" else 58.0


class TestTheDialFollowsThePoll:
    def test_the_tape_that_lands_at_load_leaves_the_dial_the_server_rendered(self):
        tape = _chart_with_last_mc(
            _world()["charts"][UNTRIGGERED], _tape_mc_other_than_the_renders()
        )
        r = _run_p(
            r"""
        await H.boot({ extras: true, charts: H.bodies.charts, state: H.bodies.same });
        return { dial: H.dial(__ID__) };
        """.replace("__ID__", repr(UNTRIGGERED)),
            charts={**_world()["charts"], UNTRIGGERED: tape},
            same=_same_stamp(),
        )
        assert r["dial"]["text"] == _server_dial_text(UNTRIGGERED)

    @pytest.mark.parametrize("order", ["tape_last", "poll_last"])
    def test_a_reading_in_the_poll_wins_over_the_tapes_last_reading_in_both_orders(self, order):
        tape = _chart_with_last_mc(_world()["charts"][UNTRIGGERED], 58.0)
        state = _with_mc(_same_stamp(), UNTRIGGERED, 63.0)
        url = f"/api/chart/{UNTRIGGERED}"
        if order == "tape_last":
            scenario = r"""
            await H.boot({ extras: true, state: H.bodies.state, overrides: [[__CHART__, { pending: true }]] });
            await H.releasePending(__URL__, { body: H.bodies.tape });
            await H.completeAnimations();
            return { dial: H.dial(__ID__) };
            """
        else:
            scenario = r"""
            await H.boot({ extras: true, charts: H.bodies.charts, statePending: true });
            await H.releasePending('/api/state', { body: H.bodies.state });
            await H.completeAnimations();
            return { dial: H.dial(__ID__) };
            """
        r = _run_p(
            scenario.replace("__CHART__", CHART_URL)
            .replace("__URL__", repr(url))
            .replace("__ID__", repr(UNTRIGGERED)),
            state=state,
            tape=tape,
            charts={**_world()["charts"], UNTRIGGERED: tape},
        )
        assert r["dial"]["text"] == "63%", (
            f"the dial read {r['dial']['text']!r}, not the poll's 63%"
        )

    @pytest.mark.parametrize("order", ["tape_last", "poll_last"])
    def test_a_missing_reading_in_the_poll_reads_the_dash_whatever_the_tape_holds(self, order):
        tape = _chart_with_last_mc(_world()["charts"][UNTRIGGERED], 58.0)
        state = _with_mc(_same_stamp(), UNTRIGGERED, None)
        url = f"/api/chart/{UNTRIGGERED}"
        if order == "tape_last":
            scenario = r"""
            await H.boot({ extras: true, state: H.bodies.state, overrides: [[__CHART__, { pending: true }]] });
            await H.releasePending(__URL__, { body: H.bodies.tape });
            await H.completeAnimations();
            return { dial: H.dial(__ID__) };
            """
        else:
            scenario = r"""
            await H.boot({ extras: true, charts: H.bodies.charts, statePending: true });
            await H.releasePending('/api/state', { body: H.bodies.state });
            await H.completeAnimations();
            return { dial: H.dial(__ID__) };
            """
        r = _run_p(
            scenario.replace("__CHART__", CHART_URL)
            .replace("__URL__", repr(url))
            .replace("__ID__", repr(UNTRIGGERED)),
            state=state,
            tape=tape,
            charts={**_world()["charts"], UNTRIGGERED: tape},
        )
        assert r["dial"]["text"] == DASH, "an older tape reading stood in for a missing one"
        assert r["dial"]["offset"] == "94.25", "the arc must be empty"

    def test_a_poll_that_loses_its_reading_clears_a_dial_that_showed_one(self):
        state = _with_mc(_same_stamp(), UNTRIGGERED, None)
        r = _run_p(
            r"""
        await H.boot({ extras: true, state: H.bodies.same });
        const shown = H.dial(__ID__).text;
        await poll(H.bodies.lost);
        return { shown: shown, after: H.dial(__ID__).text };
        """.replace("__ID__", repr(UNTRIGGERED)),
            same=_same_stamp(),
            lost=state,
        )
        assert r["shown"] != DASH, "the render must have shown a reading to lose"
        assert r["after"] == DASH

    def test_a_triggered_symphonys_dial_reads_the_dash_after_the_tape_lands(self):
        r = _run_p(
            r"""
        await H.boot({ extras: true, state: H.bodies.same });
        await poll(H.bodies.same);
        return { dial: H.dial(__ID__) };
        """.replace("__ID__", repr(TRIGGERED)),
            same=_same_stamp(),
        )
        assert r["dial"]["text"] == DASH


# ---------------------------------------------------------------------------
# S-9.2, S-9.8: a previous session's tape says so
# ---------------------------------------------------------------------------


def _day_before(iso: str) -> str:
    return (dt.date.fromisoformat(iso) - dt.timedelta(days=1)).isoformat()


class TestAPreviousSessionsTapeCarriesItsDate:
    def test_a_tape_from_the_previous_session_is_captioned_with_that_sessions_date(self):
        today = _world()["seeded_today"]
        previous = _day_before(today)
        chart = _chart_with_session(_world()["charts"][UNTRIGGERED], previous, today)
        r = _run_p(
            r"""
        await H.boot({ extras: true, charts: H.bodies.charts, state: H.bodies.same });
        return { shown: caption(__ID__), other: caption(__OTHER__) };
        """.replace("__ID__", repr(UNTRIGGERED)).replace("__OTHER__", repr(TRIGGERED)),
            charts={**_world()["charts"], UNTRIGGERED: chart},
            same=_same_stamp(),
        )
        shown = r["shown"]
        assert shown is not None and not shown["hidden"], (
            "the previous session's tape has no caption"
        )
        assert shown["text"] == f"{previous} session"
        assert "spark-session" in shown["classes"].split()
        assert r["other"] is None or r["other"]["hidden"], (
            "a card with no date fields gets no caption"
        )

    @pytest.mark.parametrize("shape", ["same_date", "no_date_fields"])
    def test_today_s_tape_and_a_tape_that_names_no_session_have_no_caption(self, shape):
        today = _world()["seeded_today"]
        base = _world()["charts"][UNTRIGGERED]
        chart = _chart_with_session(base, today, today) if shape == "same_date" else base
        r = _run_p(
            r"""
        await H.boot({ extras: true, charts: H.bodies.charts, state: H.bodies.same });
        return { shown: caption(__ID__) };
        """.replace("__ID__", repr(UNTRIGGERED)),
            charts={**_world()["charts"], UNTRIGGERED: chart},
            same=_same_stamp(),
        )
        assert r["shown"] is None or r["shown"]["hidden"] or r["shown"]["text"] == ""

    def test_the_caption_goes_when_the_new_sessions_first_tape_arrives(self):
        today = _world()["seeded_today"]
        previous = _day_before(today)
        stale = _chart_with_session(_world()["charts"][UNTRIGGERED], previous, today)
        fresh = _chart_with_session(_world()["charts"][UNTRIGGERED], today, today)
        r = _run_p(
            r"""
        await H.boot({ extras: true, charts: H.bodies.charts, state: H.bodies.same });
        const before = caption(__ID__);
        H.route(__CHART__, function (url, o, m) { return { body: m[1] === __ID__ ? H.bodies.fresh : H.bodies.charts[m[1]] }; });
        await poll(H.bodies.later);
        return { before: before, after: caption(__ID__) };
        """.replace("__ID__", repr(UNTRIGGERED)).replace("__CHART__", CHART_URL),
            charts={**_world()["charts"], UNTRIGGERED: stale},
            same=_same_stamp(),
            later=_later(),
            fresh=fresh,
        )
        assert r["before"] is not None and not r["before"]["hidden"]
        assert r["after"] is None or r["after"]["hidden"] or r["after"]["text"] == ""

    def test_the_caption_is_written_as_text(self):
        today = _world()["seeded_today"]
        hostile = "<img src=x onerror=alert(1)>"
        chart = _chart_with_session(_world()["charts"][UNTRIGGERED], hostile, today)
        r = _run_p(
            r"""
        const writes0 = H.htmlWrites.length;
        await H.boot({ extras: true, charts: H.bodies.charts, state: H.bodies.same });
        return { shown: caption(__ID__),
                 htmlWrites: H.htmlWrites.slice(writes0).filter(function (w) { return w.within.indexOf('sym-card') >= 0; }).length };
        """.replace("__ID__", repr(UNTRIGGERED)),
            charts={**_world()["charts"], UNTRIGGERED: chart},
            same=_same_stamp(),
        )
        assert r["shown"] is not None and hostile in r["shown"]["text"], (
            "markup in a date shows literally"
        )
        assert r["htmlWrites"] == 0, "no markup write lands inside a symphony card"


class TestASessionIsStatedOnceInTheSectionHeader:
    """Tapes that all belong to one session name it once, in the section's header line."""

    @staticmethod
    def _charts_all(session_date: str, et_date: str) -> dict:
        return {
            sid: _chart_with_session(body, session_date, et_date)
            for sid, body in _world()["charts"].items()
        }

    def test_every_tape_from_the_previous_session_is_one_header_caption_and_no_card_caption(self):
        today = _world()["seeded_today"]
        previous = _day_before(today)
        r = _run_p(
            r"""
        await H.boot({ extras: true, charts: H.bodies.charts, state: H.bodies.same });
        return { headers: headerCaptions(), cards: cardCaptions() };
        """,
            charts=self._charts_all(previous, today),
            same=_same_stamp(),
        )
        shown = [h for h in r["headers"] if not h["hidden"]]
        assert shown, "no section header names the previous session"
        assert {h["text"] for h in shown} == {f"{previous} session"}
        assert r["cards"] == [], "a card repeats the session its section header already names"

    def test_tapes_of_one_section_that_disagree_leave_the_header_empty_and_each_card_names_its_own(
        self,
    ):
        today = _world()["seeded_today"]
        previous = _day_before(today)
        charts = self._charts_all(previous, today)
        charts[UNTRIGGERED] = _chart_with_session(charts[UNTRIGGERED], today, today)
        r = _run_p(
            r"""
        await H.boot({ extras: true, charts: H.bodies.charts, state: H.bodies.same });
        return { headers: headerCaptions(), cards: cardCaptions() };
        """,
            charts=charts,
            same=_same_stamp(),
        )
        assert all(h["hidden"] or not h["text"] for h in r["headers"]), r["headers"]
        assert r["cards"], "the cards whose tape is from the previous session name it themselves"
        assert {c["text"] for c in r["cards"]} == {f"{previous} session"}

    def test_the_header_caption_goes_when_the_new_sessions_tapes_arrive(self):
        today = _world()["seeded_today"]
        previous = _day_before(today)
        r = _run_p(
            r"""
        await H.boot({ extras: true, charts: H.bodies.charts, state: H.bodies.same });
        const before = headerCaptions();
        H.route(__CHART__, function (url, o, m) { return { body: H.bodies.fresh[m[1]] }; });
        await poll(H.bodies.later);
        return { before: before, after: headerCaptions(), cards: cardCaptions() };
        """.replace("__CHART__", CHART_URL),
            charts=self._charts_all(previous, today),
            same=_same_stamp(),
            later=_later(),
            fresh=self._charts_all(today, today),
        )
        assert any(not h["hidden"] and h["text"] for h in r["before"])
        assert all(h["hidden"] or not h["text"] for h in r["after"]), r["after"]
        assert r["cards"] == []

    def test_the_header_caption_is_written_as_text(self):
        today = _world()["seeded_today"]
        hostile = "<img src=x onerror=alert(1)>"
        r = _run_p(
            r"""
        const writes0 = H.htmlWrites.length;
        await H.boot({ extras: true, charts: H.bodies.charts, state: H.bodies.same });
        return { headers: headerCaptions(), htmlWrites: H.htmlWrites.length - writes0 };
        """,
            charts=self._charts_all(hostile, today),
            same=_same_stamp(),
        )
        assert any(hostile in h["text"] for h in r["headers"]), "markup in a date shows literally"
        assert not [h for h in r["headers"] if "<" in h["text"] and h["hidden"]]


# ---------------------------------------------------------------------------
# S-9.7: a symphony with no tape says so
# ---------------------------------------------------------------------------


class TestAnEmptyTape:
    def test_a_card_with_no_intraday_rows_reads_no_intraday_data_yet_after_a_refresh(self):
        world = _world("mixed")
        stamp = sup.render_stamp(world)
        r = hx.run_scenario(
            world,
            PRELUDE
            + r"""
        await H.boot({ extras: true });
        await poll(H.bodies.fresh);
        return { status: H.summary()['spark:' + __ID__].status };
        """.replace("__ID__", repr(UNTRIGGERED)),
            extra_bodies={"fresh": sup.with_stamp(world["state"], sup.later_stamp(stamp))},
            now_iso=hx.seeded_now_iso(world),
        )["result"]
        assert NO_DATA_TEXT in r["status"]
