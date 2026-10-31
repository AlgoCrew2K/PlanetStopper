"""The dashboard ``/`` draws its charts from the server render, without waiting
for the ``/api/state`` poll, and says when it is ready.

Real entrypoints exercised:
  * ``GET /`` through the Flask test client (the server render, parsed).
  * the whole of ``static/index.js`` under node (tests/dashboard/_index_js_harness.py),
    run against that very server render, with the real producers' output for
    ``/api/hero-chart/<token>``, ``/api/chart/<id>`` and ``/api/state``.

Fixture provenance: tests/fixtures/dashboard/charts_refresh/PROVENANCE.md.
The stub ``Chart`` only counts calls; whether a canvas is visibly drawn is
judged on a live render (tests/ui/test_dashboard_charts_browser.py).
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.dashboard import _index_js_harness as hx
from tests.dashboard import _refresh_timing_support as sup

EMPTY_HERO_TEXT = "No portfolio history to chart yet"
NO_INTRADAY_TEXT = "No intraday data yet"
CHART_UNAVAILABLE_TEXT = "Chart unavailable"

IDS = hx.SYMPHONY_IDS


def _spark(sym_id: str) -> str:
    return f"spark:{sym_id}"


def _ids_with_intraday(world: dict) -> list[str]:
    return [i for i in world["ids"] if world["charts"][i]["data"]]


# ---------------------------------------------------------------------------
# The server render: what the page script reads at load
# ---------------------------------------------------------------------------


class TestServerRenderCarriesFirstPaintData:
    def test_embedded_hero_series_is_the_default_window_route_body(self):
        world = hx.get_world("default")
        block = hx.initial_data_block(hx.parse_html(world["html"]))
        assert "hero" in block, "the data block must carry the hero series under 'hero'"
        assert block["hero"] == world["hero"]["30d"], (
            "the series embedded in the page must equal what /api/hero-chart/30d returns "
            "for the same database (the default window), key for key"
        )
        assert block["hero"]["hist_dates"], "the default world holds a 30d history"

    def test_embedded_poll_tail_describes_the_series_a_poll_will_carry(self):
        world = hx.get_world("default")
        block = hx.initial_data_block(hx.parse_html(world["html"]))
        dates, bot, held = hx.poll_hist(world["state"])
        assert block["hero_poll_tail"] == {
            "length": len(dates),
            "last_date": dates[-1],
            "last_bot": bot[-1],
            "last_held": held[-1],
        }, "hero_poll_tail must describe meta.portfolio.hist_* exactly as /api/state serves it"

    def test_embedded_sparks_carry_each_cards_exit_marker_fields(self):
        world = hx.get_world("default")
        block = hx.initial_data_block(hx.parse_html(world["html"]))
        assert set(block["sparks"]) >= set(world["ids"]), "one entry per symphony card"
        for sym_id in world["ids"]:
            record = world["bot_state"][sym_id]
            entry = block["sparks"][sym_id]
            assert bool(entry["triggered"]) == bool(record.get("triggered")), sym_id
            if record.get("triggered"):
                assert entry["triggered_at_time"] == record["triggered_at_time"], sym_id
                assert entry["triggered_reason"] == record["triggered_reason"], sym_id

    @pytest.mark.parametrize("world_name", ["default", "empty", "onepoint"])
    def test_empty_history_text_element_exists_hidden_with_the_required_copy(self, world_name):
        world = hx.get_world(world_name)
        tree = hx.parse_html(world["html"])
        node = hx.find_by_id(tree, "hero-chart-empty")
        assert node is not None, (
            "the hero needs a #hero-chart-empty element for the no-history case"
        )
        assert hx.tree_text(node).strip() == EMPTY_HERO_TEXT
        assert "hidden" in node["a"], "the script shows it; the server render ships it hidden"

    def test_no_history_world_embeds_an_empty_hero_series(self):
        world = hx.get_world("empty")
        block = hx.initial_data_block(hx.parse_html(world["html"]))
        assert block["hero"]["hist_dates"] == []
        assert block["hero"]["hist_bot"] == []
        assert block["hero"]["hist_held"] == []
        assert block["hero_poll_tail"]["length"] == 0

    def test_single_point_world_embeds_exactly_one_point_per_line(self):
        world = hx.get_world("onepoint")
        block = hx.initial_data_block(hx.parse_html(world["html"]))
        assert len(block["hero"]["hist_dates"]) == 1
        assert len(block["hero"]["hist_bot"]) == 1
        assert len(block["hero"]["hist_held"]) == 1
        assert block["hero"] == world["hero"]["30d"]


class TestServerRenderSecurity:
    def test_a_hostile_exit_reason_cannot_break_out_of_the_data_block(self):
        world = hx.get_world("hostile")
        raw = world["html"]
        assert "</script><img" not in raw, (
            "a stored exit reason containing </script> must be escaped inside the embedded "
            "JSON block, never written raw into the page"
        )
        tree = hx.parse_html(raw)
        injected = hx.find_all(tree, lambda n: n["t"] == "img" and "onerror" in n["a"])
        assert injected == [], "the stored reason became live markup"
        block = hx.initial_data_block(tree)
        assert block["sparks"][hx.TRIGGERED_VWAP_ID]["triggered_reason"] == hx.HOSTILE_REASON, (
            "the escaped reason must round-trip through JSON parsing unchanged"
        )

    def test_the_data_block_is_data_not_script(self):
        world = hx.get_world("default")
        node = hx.find_by_id(hx.parse_html(world["html"]), "dashboard-initial-data")
        assert node is not None
        assert node["a"].get("type") == "application/json"
        json.loads(hx.tree_text(node))  # parses as JSON, nothing else in it


# ---------------------------------------------------------------------------
# First paint under node, whole static/index.js
# ---------------------------------------------------------------------------

_DRAWN_SNAPSHOT = r"""
await H.boot({ statePending: %(pending)s, stateReject: %(reject)s });
const s = H.summary();
return {
  summary: s, attrs: H.attrs(), heroEmpty: H.el('#hero-chart-empty'),
  asOf: H.el('#hero-data-as-of'), stateRequests: H.requests(/^\/api\/state$/).length,
  pendingUrls: H.pending.map(function (p) { return p.url; }),
  heroRequests: H.requests(/^\/api\/hero-chart\//).length
};
"""


def _drawn(world_name: str, *, pending: bool = False, reject: bool = False) -> tuple[dict, dict]:
    world = hx.get_world(world_name)
    scenario = _DRAWN_SNAPSHOT % {"pending": json.dumps(pending), "reject": json.dumps(reject)}
    return world, hx.run_scenario(world, scenario)


def _assert_hero_and_sparks_drawn(world: dict, result: dict) -> None:
    summary = result["result"]["summary"]
    hero = summary["cum-chart"]
    expected = world["hero"]["30d"]
    assert hero["live"], "the hero chart must be drawn from the embedded series"
    assert hero["constructs"] == 1
    assert hero["labels"] == expected["hist_dates"]
    assert hero["datasets"][0]["data"] == expected["hist_bot"], "bot line holds the embedded series"
    assert hero["datasets"][1]["data"] == expected["hist_held"], (
        "held line holds the embedded series"
    )
    drawn_ids = _ids_with_intraday(world)
    assert drawn_ids, "the default world holds intraday data"
    for sym_id in drawn_ids:
        spark = summary[_spark(sym_id)]
        assert spark["live"], f"card canvas {sym_id} has intraday data and must hold a chart"
        body = world["charts"][sym_id]["data"]
        assert spark["labels"] == [p["time"] for p in body]
        assert spark["datasets"][0]["data"] == [p["return"] for p in body]


class TestFirstPaintDoesNotWaitForTheStatePoll:
    def test_hero_and_sparklines_draw_while_state_is_still_pending(self):
        world, result = _drawn("default", pending=True)
        r = result["result"]
        _assert_hero_and_sparks_drawn(world, result)
        assert "/api/state" in r["pendingUrls"], "the state request must still be unanswered"
        assert r["stateRequests"] == 1
        assert r["attrs"]["refreshCount"] is None, "no refresh has settled yet"
        assert r["attrs"]["ready"] == "1", "readiness does not depend on the state poll"
        assert r["heroEmpty"]["exists"] and r["heroEmpty"]["hidden"] is True
        assert r["heroRequests"] == 0, "the hero needs no request: its series is in the page"

    def test_exit_markers_draw_on_first_paint_from_the_embedded_fields(self):
        world = hx.get_world("default")
        scenario = r"""
        await H.boot({ statePending: true });
        const out = {};
        ["%s", "%s", "%s"].forEach(function (id) {
          const chart = H.chartFor('spark:' + id);
          const ops = chart ? H.draw(chart) : null;
          out[id] = ops === null ? null : {
            labels: ops.filter(function (o) { return o.op === 'fillText'; }).map(function (o) { return o.args[0]; }),
            strokes: ops.filter(function (o) { return o.op === 'stroke'; }).length
          };
        });
        return out;
        """ % (hx.TRIGGERED_TP_ID, hx.TRIGGERED_VWAP_ID, hx.UNTRIGGERED_ID)
        out = hx.run_scenario(world, scenario)["result"]
        for sym_id in (hx.TRIGGERED_TP_ID, hx.TRIGGERED_VWAP_ID, hx.UNTRIGGERED_ID):
            assert out[sym_id] is not None, f"card {sym_id} holds no chart at first paint"
        assert out[hx.TRIGGERED_TP_ID]["labels"] == ["TP"], "Take-Profit exit marker at first paint"
        assert out[hx.TRIGGERED_VWAP_ID]["labels"] == ["VWAP"], "VWAP exit marker at first paint"
        assert out[hx.UNTRIGGERED_ID]["labels"] == [], (
            "an untriggered card must not draw an exit marker"
        )
        assert out[hx.UNTRIGGERED_ID]["strokes"] == 0, (
            "an untriggered card must not draw an exit hairline"
        )

    def test_a_refused_state_request_leaves_the_charts_and_says_connection_lost(self):
        world, result = _drawn("default", reject=True)
        r = result["result"]
        _assert_hero_and_sparks_drawn(world, result)
        assert r["asOf"]["text"] == (
            "connection lost \u00b7 data as of " + sup.render_stamp(world)
        ), "the cue keeps the last known stamp (the one the server rendered) beside it"
        assert r["attrs"]["ready"] == "1"
        assert r["attrs"]["refreshCount"] == "1", (
            "a failed refresh still counts, so a capture never hangs"
        )

    def test_no_history_shows_the_text_instead_of_a_blank_canvas(self):
        world, result = _drawn("empty", pending=True)
        r = result["result"]
        hero = r["summary"]["cum-chart"]
        assert hero["constructs"] == 0 and not hero["live"], "nothing to draw: no chart"
        assert r["heroEmpty"]["exists"], "no #hero-chart-empty element on the page"
        assert r["heroEmpty"]["hidden"] is False
        assert r["heroEmpty"]["text"] == EMPTY_HERO_TEXT
        assert r["attrs"]["ready"] == "1", "readiness holds with the empty text shown"
        for sym_id in _ids_with_intraday(world):
            assert r["summary"][_spark(sym_id)]["live"], "the cards still draw"

    def test_history_present_keeps_the_empty_text_hidden(self):
        _, result = _drawn("default", pending=True)
        assert result["result"]["heroEmpty"]["exists"], "no #hero-chart-empty element on the page"
        assert result["result"]["heroEmpty"]["hidden"] is True

    def test_one_symphony_without_intraday_data_reads_no_intraday_data_yet(self):
        world, result = _drawn("mixed", pending=True)
        summary = result["result"]["summary"]
        empty = summary[_spark(hx.UNTRIGGERED_ID)]
        assert world["charts"][hx.UNTRIGGERED_ID]["data"] == []
        assert not empty["live"]
        assert empty["status"] == NO_INTRADAY_TEXT
        for sym_id in _ids_with_intraday(world):
            assert summary[_spark(sym_id)]["live"], f"{sym_id} has data and must draw"
            assert summary[_spark(sym_id)]["status"] == "", "a drawn canvas shows no status text"
        assert result["result"]["attrs"]["ready"] == "1", "a status-text canvas counts as settled"

    @pytest.mark.parametrize(
        ("label", "spec"),
        [
            ("network failure", {"reject": True}),
            (
                "server error envelope",
                {
                    "status": 500,
                    "body": {"status": "error", "message": "An internal error occurred"},
                },
            ),
            ("unreadable body", {"badJson": True}),
        ],
    )
    def test_one_failed_sparkline_request_reads_chart_unavailable(self, label, spec):
        world = hx.get_world("default")
        scenario = r"""
        await H.boot({ statePending: true, overrides: [["/api/chart/%s", %s]] });
        return { summary: H.summary(), attrs: H.attrs() };
        """ % (hx.UNTRIGGERED_ID, json.dumps(spec))
        r = hx.run_scenario(world, scenario)["result"]
        failed = r["summary"][_spark(hx.UNTRIGGERED_ID)]
        assert not failed["live"], label
        assert failed["status"] == CHART_UNAVAILABLE_TEXT, label
        for sym_id in (hx.TRIGGERED_TP_ID, hx.TRIGGERED_VWAP_ID):
            assert r["summary"][_spark(sym_id)]["live"], f"{sym_id} still draws ({label})"
        assert r["attrs"]["ready"] == "1", f"a failed canvas must not hold readiness back ({label})"

    def test_a_single_point_history_draws_without_an_error(self):
        world, result = _drawn("onepoint", pending=True)
        hero = result["result"]["summary"]["cum-chart"]
        assert hero["live"]
        assert len(hero["labels"]) == 1
        assert len(hero["datasets"][0]["data"]) == 1 and len(hero["datasets"][1]["data"]) == 1
        assert hero["datasets"][0]["data"] == world["hero"]["30d"]["hist_bot"]
        assert result["consoleErrors"] == [], result["consoleErrors"]
        assert result["unhandled"] == [], result["unhandled"]
        assert (
            result["result"]["heroEmpty"]["exists"]
            and result["result"]["heroEmpty"]["hidden"] is True
        )

    def test_poll_and_cycle_event_each_request_state_once(self):
        world = hx.get_world("default")
        scenario = r"""
        await H.boot({});
        const counts = [H.requests(/^\/api\/state$/).length];
        await H.tick();
        counts.push(H.requests(/^\/api\/state$/).length);
        await H.sse('cycle-complete');
        counts.push(H.requests(/^\/api\/state$/).length);
        return { counts: counts, intervals: H.intervals.map(function (i) { return i.ms; }),
                 eventSources: H.eventSources.map(function (e) { return e.url; }) };
        """
        r = hx.run_scenario(world, scenario)["result"]
        assert r["counts"] == [1, 2, 3], (
            "one /api/state request at load, per 30 s tick and per event"
        )
        assert 30000 in r["intervals"], "the 30 s poll stays"
        assert r["eventSources"] == ["/api/events"]


# ---------------------------------------------------------------------------
# Readiness (body[data-dashboard-ready], body[data-refresh-count])
# ---------------------------------------------------------------------------


class TestEntryAnimationAndReadiness:
    def test_every_chart_is_constructed_with_its_entry_animation(self):
        _, result = _drawn("default", pending=True)
        summary = result["result"]["summary"]
        keys = ["cum-chart"] + [_spark(i) for i in IDS]
        for key in keys:
            built = summary[key]["constructed"]
            assert built, f"{key} was never constructed"
            animation = built[0]["animation"]
            assert animation["kind"] != "false", f"{key}: options.animation is false"
            assert animation.get("duration") != 0, f"{key}: the entry animation is disabled"

    def test_readiness_is_absent_until_the_last_charts_entry_animation_completes(self):
        world = hx.get_world("default")
        scenario = r"""
        await H.boot({ statePending: true, complete: false });
        const seen = [H.attrs().ready];
        const live = H.charts.filter(function (c) { return !c.destroyed; });
        for (let i = 0; i < live.length; i++) {
          await H.completeAnimations(function (c) { return c === live[i]; });
          seen.push(H.attrs().ready);
        }
        return { seen: seen, count: live.length };
        """
        r = hx.run_scenario(world, scenario)["result"]
        assert r["count"] == 1 + len(_ids_with_intraday(world)), (
            "hero plus one chart per card with data"
        )
        assert r["seen"][:-1] == [None] * r["count"], (
            "readiness must stay absent until every chart's entry animation has completed: "
            f"{r['seen']}"
        )
        assert r["seen"][-1] == "1", "readiness is set when the last animation completes"

    def test_a_first_render_that_ran_no_animation_counts_as_drawn_at_the_end_of_that_render(self):
        world = hx.get_world("default")
        scenario = r"""
        await H.boot({ statePending: true, complete: false });
        const live = H.charts.filter(function (c) { return !c.destroyed; });
        // a render pass while the entry animation still runs is not "drawn"
        live.forEach(function (c) { H.fireAfterRender(c); });
        const whileAnimating = H.attrs().ready;
        const seen = [];
        live.forEach(function (c) { H.animating.delete(c); H.fireAfterRender(c); seen.push(H.attrs().ready); });
        return { whileAnimating: whileAnimating, seen: seen, count: live.length };
        """
        r = hx.run_scenario(world, scenario)["result"]
        assert r["count"] > 0, "no chart was constructed at first paint"
        assert r["whileAnimating"] is None, "afterRender during a running animation must not count"
        assert r["seen"][:-1] == [None] * (len(r["seen"]) - 1)
        assert r["seen"][-1] == "1"

    def test_readiness_waits_for_charts_but_not_for_canvases_showing_status_text(self):
        world = hx.get_world("mixed")
        scenario = r"""
        await H.boot({ statePending: true, complete: false });
        const before = H.attrs().ready;
        await H.completeAnimations();
        return { before: before, after: H.attrs().ready };
        """
        r = hx.run_scenario(world, scenario)["result"]
        assert r["before"] is None
        assert r["after"] == "1"

    def test_readiness_is_set_once_and_never_reset_by_later_refreshes(self):
        world = hx.get_world("default")
        scenario = r"""
        await H.boot({});
        const seen = [H.attrs().ready];
        await H.tick(); await H.completeAnimations(); seen.push(H.attrs().ready);
        await H.sse('cycle-complete'); await H.completeAnimations(); seen.push(H.attrs().ready);
        H.route('/api/state', { reject: true });
        await H.tick(); seen.push(H.attrs().ready);
        return { seen: seen };
        """
        assert hx.run_scenario(world, scenario)["result"]["seen"] == ["1"] * 4


def _later_state(world: dict, count: int = 1) -> dict:
    """The world's poll body with a data stamp ``count`` minutes after the one its render printed.

    A refresh asks for the sparkline tapes only when its stamp is new, so a scenario about the
    requests a refresh waits on hands the poll a body of its own.
    """
    return sup.with_stamp(world["state"], sup.later_stamp(sup.render_stamp(world), minutes=count))


class TestRefreshCount:
    def test_refresh_count_is_absent_until_the_first_refresh_settles_then_reads_one(self):
        world = hx.get_world("default")
        scenario = r"""
        await H.boot({ statePending: true });
        const before = H.attrs().refreshCount;
        await H.releasePending('/api/state', { body: H.bodies.state });
        await H.completeAnimations();
        return { before: before, after: H.attrs().refreshCount };
        """
        r = hx.run_scenario(world, scenario)["result"]
        assert r["before"] is None
        assert r["after"] == "1"

    def test_each_settled_refresh_adds_one_including_a_refused_one(self):
        world = hx.get_world("default")
        scenario = r"""
        await H.boot({});
        const seen = [H.attrs().refreshCount];
        await H.tick(); seen.push(H.attrs().refreshCount);
        await H.sse('cycle-complete'); seen.push(H.attrs().refreshCount);
        H.route('/api/state', { reject: true });
        await H.tick(); seen.push(H.attrs().refreshCount);
        return { seen: seen };
        """
        assert hx.run_scenario(world, scenario)["result"]["seen"] == ["1", "2", "3", "4"]

    def test_each_settled_refresh_adds_one_whether_or_not_its_stamp_is_new(self):
        world = hx.get_world("default")
        scenario = r"""
        await H.boot({});
        const seen = [H.attrs().refreshCount];
        H.route('/api/state', { body: H.bodies.later1 });
        await H.tick(); seen.push(H.attrs().refreshCount);
        await H.tick(); seen.push(H.attrs().refreshCount);
        H.route('/api/state', { body: H.bodies.later2 });
        await H.tick(); seen.push(H.attrs().refreshCount);
        return { seen: seen };
        """
        extra = {"later1": _later_state(world, 1), "later2": _later_state(world, 2)}
        r = hx.run_scenario(world, scenario, extra_bodies=extra)["result"]
        assert r["seen"] == ["1", "2", "3", "4"]

    def test_a_refresh_is_not_settled_while_its_sparkline_requests_are_in_flight(self):
        world = hx.get_world("default")
        scenario = r"""
        await H.boot({});
        const url = '/api/chart/%s';
        H.route(url, { pending: true });
        H.route('/api/state', { body: H.bodies.later1 });
        await H.tick();
        const held = H.attrs().refreshCount;
        await H.releasePending(url, { body: H.bodies.charts['%s'] });
        return { held: held, released: H.attrs().refreshCount };
        """ % (hx.UNTRIGGERED_ID, hx.UNTRIGGERED_ID)
        r = hx.run_scenario(world, scenario, extra_bodies={"later1": _later_state(world)})["result"]
        assert r["held"] == "1", "the second refresh must wait for its /api/chart responses"
        assert r["released"] == "2"

    def test_a_refresh_that_constructs_a_new_chart_settles_after_that_charts_animation(self):
        mixed = hx.get_world("mixed")
        data_for_empty_canvas = hx.get_world("default")["charts"][hx.UNTRIGGERED_ID]
        scenario = r"""
        await H.boot({});
        const first = H.attrs().refreshCount;
        const before = H.summary()['spark:%s'];
        H.route('/api/chart/%s', { body: H.bodies.late });
        H.route('/api/state', { body: H.bodies.later1 });
        await H.tick();
        const during = H.attrs().refreshCount;
        const built = H.summary()['spark:%s'];
        await H.completeAnimations();
        return { first: first, before: before, during: during, built: built, after: H.attrs().refreshCount };
        """ % (hx.UNTRIGGERED_ID, hx.UNTRIGGERED_ID, hx.UNTRIGGERED_ID)
        r = hx.run_scenario(
            mixed,
            scenario,
            extra_bodies={"late": data_for_empty_canvas, "later1": _later_state(mixed)},
        )["result"]
        assert r["first"] == "1"
        assert not r["before"]["live"] and r["before"]["status"] == NO_INTRADAY_TEXT
        assert r["built"]["live"], "a canvas first seen during a refresh gets its chart"
        assert r["built"]["status"] == "", "the status text goes once the canvas holds a chart"
        assert r["built"]["constructed"][0]["animation"]["kind"] != "false"
        assert r["during"] == "1", "the refresh waits for the new chart's entry animation"
        assert r["after"] == "2"


# ---------------------------------------------------------------------------
# A failed hero series is logged by exception type, never by exception text
# ---------------------------------------------------------------------------


class TestHeroSeriesFailureLogging:
    DETAIL = "series-reader-detail-7c41e9"

    @pytest.fixture
    def failing_series(self, monkeypatch):
        import analytics

        def _raise(*args, **kwargs):
            raise RuntimeError(self.DETAIL)

        monkeypatch.setattr(analytics, "get_portfolio_bot_and_held_daily_returns", _raise)

    @staticmethod
    def _rendered(record: logging.LogRecord) -> str:
        return logging.Formatter("%(levelname)s %(name)s %(message)s").format(record)

    @pytest.mark.parametrize("entry", ["helper", "route", "page data"])
    def test_the_error_line_names_the_exception_type_and_carries_no_exception_text(
        self, entry, failing_series, caplog
    ):
        import app as app_module

        caplog.set_level(logging.DEBUG)
        if entry == "helper":
            body = app_module._hero_chart_body("30d")
        elif entry == "route":
            response = app_module.app.test_client().get("/api/hero-chart/30d")
            assert response.status_code == 200
            body = response.get_json()
        else:
            body = app_module._dashboard_initial_data({}, {})["hero"]
        assert body["hist_dates"] == [] and body["insufficient_history"] is True, (
            "the failure still degrades to the empty series"
        )
        loud = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert any(r.levelno >= logging.ERROR for r in loud), "the failure is still reported"
        for record in loud:
            text = self._rendered(record)
            assert self.DETAIL not in text, (
                f"exception text reached a {record.levelname} record: {text!r}"
            )
            assert record.exc_info is None and not record.exc_text, (
                "an exc_info traceback on a WARNING+ record carries the exception text"
            )
        assert any("RuntimeError" in self._rendered(r) for r in loud), (
            "the ERROR line carries the exception type name"
        )

    def test_the_full_detail_is_kept_in_a_debug_record(self, failing_series, caplog):
        import app as app_module

        caplog.set_level(logging.DEBUG)
        app_module._hero_chart_body("30d")
        debug = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert any(self.DETAIL in self._rendered(r) for r in debug), (
            "the exception detail must still be recoverable from a DEBUG record"
        )


# ---------------------------------------------------------------------------
# The harness never takes a conftest stub for the module's own producer
# ---------------------------------------------------------------------------


class TestHarnessProducerLookup:
    @pytest.mark.parametrize("kind", ["def", "lambda"])
    def test_a_plain_function_stub_is_replaced_by_the_modules_own_function(self, kind):
        import app as app_module

        def stub_def(*args, **kwargs):
            return "stub"

        stub = stub_def if kind == "def" else (lambda *args, **kwargs: "stub")
        # A context manager, undone here, before the suite's own stub is: monkeypatch would save
        # that stub as the value to restore and could write it back after the suite's undo.
        with patch.object(app_module, "get_api_state_dict", stub):
            real = hx._real_function(app_module, "get_api_state_dict")
        assert real is not stub, "a stub written as a plain function was taken for the producer"
        assert real.__code__.co_filename == app_module.__file__

    def test_a_decorated_target_is_refused_because_the_rebuild_would_rerun_the_decorator(
        self, tmp_path, monkeypatch
    ):
        import types

        source = tmp_path / "decorated_target.py"
        source.write_text(
            "def wrap(f):\n    return f\n\n\n@wrap\ndef target():\n    return 1\n", encoding="utf-8"
        )
        module = types.ModuleType("decorated_target")
        module.__file__ = str(source)
        module.target = lambda: "stub"
        with pytest.raises(RuntimeError, match="decorat"):
            hx._real_function(module, "target")


# ---------------------------------------------------------------------------
# The poll's day cap reaches the page whatever shape the producer's signature has
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]

# Run in a fresh interpreter because the cap is read when ``app`` is imported: the
# producer is replaced (or removed) first, then the page data is built and encoded
# the way the template encodes it.
_CAP_PROBE = """
import json, sys
import analytics
namespace = {}
exec(sys.argv[1], namespace)
if "producer" in namespace:
    analytics.get_portfolio_bot_and_held_daily_returns = namespace["producer"]
else:
    del analytics.get_portfolio_bot_and_held_daily_returns
import app
data = app._dashboard_initial_data({}, {})
json.dumps(data)
print("PROBE:" + json.dumps(data["hero_poll_cap"]))
"""


class TestPollDayCapSurvivesAProducerSignatureChange:
    @staticmethod
    def _page_cap(producer_source: str, tmp_path) -> object:
        env = dict(os.environ)
        env["DB_PATH"] = str(tmp_path / "probe_state.db")
        env["ATLAS_CACHE_DB_PATH"] = str(tmp_path / "probe_atlas.db")
        env["PYTHONPATH"] = str(REPO_ROOT)
        result = subprocess.run(
            [sys.executable, "-c", _CAP_PROBE, producer_source],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, (
            "importing the app and building the page data must not fail when the producer's "
            f"signature changes shape; stderr tail: {result.stderr[-600:]}"
        )
        lines = [ln for ln in result.stdout.splitlines() if ln.startswith("PROBE:")]
        assert lines, f"the probe printed no cap line; stdout tail: {result.stdout[-300:]}"
        return json.loads(lines[-1][len("PROBE:") :])

    @pytest.mark.parametrize(
        "producer_source",
        [
            "def producer(db_file=None, conn=None): return None",
            "def producer(db_file, days, conn): return None",
            "def producer(db_file=None, days='125', conn=None): return None",
            "def producer(db_file=None, days=0, conn=None): return None",
            "def producer(db_file=None, days=None, conn=None): return None",
            "not_a_producer = None",
        ],
        ids=[
            "days parameter removed",
            "days has no default",
            "default is text",
            "default is zero",
            "default is None",
            "producer removed",
        ],
    )
    def test_a_producer_without_a_usable_day_default_gives_no_cap_and_no_failure(
        self, producer_source, tmp_path
    ):
        assert self._page_cap(producer_source, tmp_path) is None, (
            "with no positive integer default the page carries a null cap, so the client "
            "never judges a series against a number nobody supplied"
        )

    def test_a_positive_integer_default_is_the_cap_the_page_carries(self, tmp_path):
        assert (
            self._page_cap("def producer(db_file=None, days=7, conn=None): return None", tmp_path)
            == 7
        ), "the cap follows the producer's own default, never a number restated in the app"

    def test_the_poll_dates_and_the_poll_values_are_capped_by_the_same_default(self):
        import analytics

        defaults = {
            name: inspect.signature(getattr(analytics, name)).parameters["days"].default
            for name in (
                "get_portfolio_daily_returns_from_shadow",
                "get_portfolio_bot_and_held_daily_returns",
            )
        }
        assert len(set(defaults.values())) == 1, (
            "the poll's dates come from one producer and its values from the other; the page "
            f"reads one cap, so the two defaults must stay equal: {defaults}"
        )
