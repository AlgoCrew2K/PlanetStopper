"""Refreshes keep drawn dashboard charts, in place, on the selected calendar
window, in the colours the tokens then hold.

Everything runs through the whole of ``static/index.js`` under node
(tests/dashboard/_index_js_harness.py) against the real server render and the
real producers' output. The stub ``Chart`` only counts constructor, ``destroy``
and ``update`` calls and keeps the data it was given; visible drawing is judged
on a live render (tests/ui/test_dashboard_charts_browser.py).

Fixture provenance: tests/fixtures/dashboard/charts_refresh/PROVENANCE.md.
"""

from __future__ import annotations

import copy
import datetime as dt
import json

import pytest

from tests.dashboard import _index_js_harness as hx

IDS = hx.SYMPHONY_IDS
HERO_KEYS = ("hist_dates", "hist_bot", "hist_held")
DEFAULT_ACCENT = "--studio-accent"
DIM = "--studio-ink-dim"


def _spark(sym_id: str) -> str:
    return f"spark:{sym_id}"


def _run(scenario: str, world_name: str = "default", **kwargs) -> dict:
    return hx.run_scenario(hx.get_world(world_name), scenario, **kwargs)["result"]


def _hero_matches_body(hero: dict, body: dict, *, tol: float | None = None) -> None:
    assert hero["live"], "the hero chart must still be drawn"
    assert hero["labels"] == body["hist_dates"]
    if tol is None:
        assert hero["datasets"][0]["data"] == body["hist_bot"]
        assert hero["datasets"][1]["data"] == body["hist_held"]
    else:
        hx.assert_series_close(hero["datasets"][0]["data"], body["hist_bot"], tol, "bot line")
        hx.assert_series_close(hero["datasets"][1]["data"], body["hist_held"], tol, "held line")


def _state_variant(state: dict, mode: str) -> dict:
    """The real /api/state body with only the hero series fields changed."""
    out = copy.deepcopy(state)
    portfolio = out["meta"]["portfolio"]
    if mode == "empty lists":
        for key in HERO_KEYS:
            portfolio[key] = []
    elif mode == "keys absent":
        for key in HERO_KEYS:
            portfolio.pop(key, None)
    elif mode == "keys null":
        for key in HERO_KEYS:
            portfolio[key] = None
    elif mode == "portfolio object absent":
        del out["meta"]["portfolio"]
    else:  # pragma: no cover
        raise AssertionError(mode)
    return out


# ---------------------------------------------------------------------------
# AC-3: a refresh never empties, rebuilds or re-animates a drawn chart
# ---------------------------------------------------------------------------


class TestRefreshKeepsDrawnCharts:
    @pytest.mark.parametrize(
        "mode", ["empty lists", "keys absent", "keys null", "portfolio object absent"]
    )
    def test_a_poll_without_a_hero_series_keeps_the_drawn_points(self, mode):
        world = hx.get_world("default")
        variant = _state_variant(world["state"], mode)
        scenario = r"""
        await H.boot({});
        const before = H.summary()['cum-chart'];
        H.route('/api/state', { body: H.bodies.variant });
        await H.tick(); await H.completeAnimations();
        return { before: before, after: H.summary()['cum-chart'] };
        """
        r = _run(scenario, extra_bodies={"variant": variant})
        before, after = r["before"], r["after"]
        assert before["live"] and before["labels"], "the hero starts drawn"
        assert after["live"], f"{mode}: the hero chart disappeared"
        assert after["chartId"] == before["chartId"], f"{mode}: the hero chart was rebuilt"
        assert after["labels"] == before["labels"], f"{mode}: the hero lost its labels"
        assert after["datasets"][0]["data"] == before["datasets"][0]["data"], mode
        assert after["datasets"][1]["data"] == before["datasets"][1]["data"], mode
        assert after["destroys"] == 0

    def test_every_sparkline_keeps_its_chart_instance_and_takes_the_new_data(self):
        world = hx.get_world("default")
        scenario = r"""
        await H.boot({});
        const before = H.summary();
        H.route(/^\/api\/chart\/(.+)$/, function (url, o, m) { return { body: H.bodies.chartsNext[m[1]] }; });
        await H.tick(); await H.completeAnimations();
        return { before: before, after: H.summary() };
        """
        r = _run(scenario)
        for sym_id in IDS:
            key = _spark(sym_id)
            before, after = r["before"][key], r["after"][key]
            nxt = world["next"]["charts"][sym_id]["data"]
            assert after["chartId"] == before["chartId"], f"{sym_id}: chart instance replaced"
            assert after["constructs"] == 1 and after["destroys"] == 0, sym_id
            assert len(nxt) > len(before["labels"]), "the next capture carries a new point"
            assert after["labels"] == [p["time"] for p in nxt]
            assert after["datasets"][0]["data"] == [p["return"] for p in nxt]
            assert after["datasets"][1]["data"] == [p["held"] for p in nxt]

    @pytest.mark.parametrize(
        ("label", "spec"),
        [
            ("empty data", {"body": {"status": "success", "data": []}}),
            ("network failure", {"reject": True}),
            (
                "server error",
                {
                    "status": 500,
                    "body": {"status": "error", "message": "An internal error occurred"},
                },
            ),
        ],
    )
    def test_a_sparkline_response_without_new_data_keeps_the_drawn_chart(self, label, spec):
        scenario = r"""
        await H.boot({});
        const before = H.summary();
        H.route(/^\/api\/chart\//, %s);
        await H.tick(); await H.completeAnimations();
        return { before: before, after: H.summary() };
        """ % json.dumps(spec)
        r = _run(scenario)
        for sym_id in IDS:
            key = _spark(sym_id)
            before, after = r["before"][key], r["after"][key]
            assert after["live"], f"{label}: {sym_id} lost its chart"
            assert after["chartId"] == before["chartId"]
            assert after["labels"] == before["labels"], f"{label}: {sym_id} points changed"
            assert after["datasets"][0]["data"] == before["datasets"][0]["data"]
            assert after["status"] == "", f"{label}: a status text appeared over a drawn chart"
            assert after["destroys"] == 0

    def test_ten_polls_and_two_cycle_events_construct_each_canvas_once(self):
        scenario = r"""
        await H.boot({});
        for (let i = 0; i < 10; i++) { await H.tick(); await H.completeAnimations(); }
        for (let i = 0; i < 2; i++) { await H.sse('cycle-complete'); await H.completeAnimations(); }
        return { summary: H.summary() };
        """
        summary = _run(scenario)["summary"]
        for key in ["cum-chart"] + [_spark(i) for i in IDS]:
            assert summary[key]["constructs"] == 1, (
                f"{key} was constructed {summary[key]['constructs']} times"
            )
            assert summary[key]["destroys"] == 0, f"{key} was destroyed"
            assert summary[key]["live"]

    def test_refreshes_update_in_place_without_replaying_the_entry_animation(self):
        scenario = r"""
        await H.boot({});
        for (let i = 0; i < 3; i++) { await H.tick(); await H.completeAnimations(); }
        return { summary: H.summary() };
        """
        summary = _run(scenario)["summary"]
        for sym_id in IDS:
            updates = summary[_spark(sym_id)]["updates"]
            assert updates, f"{sym_id}: a refresh must update the existing chart"
            assert set(updates) == {"none"}, f"{sym_id}: an update replayed animation: {updates}"
        hero_updates = summary["cum-chart"]["updates"]
        assert set(hero_updates) <= {"none"}, f"the hero update replayed animation: {hero_updates}"

    def test_a_refused_state_request_keeps_every_chart(self):
        scenario = r"""
        await H.boot({});
        const before = H.summary();
        H.route('/api/state', { reject: true });
        await H.tick(); await H.completeAnimations();
        return { before: before, after: H.summary() };
        """
        r = _run(scenario)
        for key in ["cum-chart"] + [_spark(i) for i in IDS]:
            assert r["after"][key]["live"], key
            assert r["after"][key]["chartId"] == r["before"][key]["chartId"], key
            assert r["after"][key]["labels"] == r["before"][key]["labels"], key
            assert r["after"][key]["datasets"] == r["before"][key]["datasets"], key

    @pytest.mark.parametrize("token", ["30d", "all"])
    def test_a_poll_carrying_one_more_point_shows_one_more_point(self, token):
        world = hx.get_world("default")
        scenario = r"""
        await H.boot({});
        if ('%(t)s' !== '30d') { await H.click('[data-testid="window-%(t)s"]'); await H.completeAnimations(); }
        const before = H.summary()['cum-chart'];
        H.route(/^\/api\/hero-chart\/([a-z0-9]+)$/, function (url, o, m) { return { body: H.bodies.heroNext[m[1]] }; });
        H.route('/api/state', { body: H.bodies.stateNext });
        await H.tick(); await H.completeAnimations();
        return { before: before, after: H.summary()['cum-chart'] };
        """ % {"t": token}
        r = _run(scenario)
        n = len(world["hero"][token]["hist_dates"])
        assert len(r["before"]["labels"]) == n
        assert len(world["next"]["hero"][token]["hist_dates"]) == n + 1, (
            "the next capture holds one more day"
        )
        _hero_matches_body(r["after"], world["next"]["hero"][token])
        assert r["after"]["chartId"] == r["before"]["chartId"] and r["after"]["destroys"] == 0

    def test_an_empty_hero_chart_response_keeps_the_drawn_series(self):
        # the route's own exception-path body (app.py get_hero_chart fallback return)
        empty_body = {
            "hist_dates": [],
            "hist_bot": [],
            "hist_held": [],
            "window": "30d",
            "insufficient_history": True,
            "available_days": 0,
        }
        scenario = r"""
        await H.boot({});
        const before = H.summary()['cum-chart'];
        H.route(/^\/api\/hero-chart\//, { body: H.bodies.emptyHero });
        H.route('/api/state', { body: H.bodies.stateNext });
        await H.tick(); await H.completeAnimations();
        await H.click('[data-testid="window-60d"]');
        return { before: before, after: H.summary()['cum-chart'] };
        """
        r = _run(scenario, extra_bodies={"emptyHero": empty_body})
        assert r["after"]["live"]
        assert r["after"]["labels"] == r["before"]["labels"]
        assert r["after"]["datasets"][0]["data"] == r["before"]["datasets"][0]["data"]

    @pytest.mark.parametrize("sym_id", [hx.TRIGGERED_TP_ID, hx.TRIGGERED_VWAP_ID])
    def test_the_exit_marker_is_still_drawn_after_a_poll(self, sym_id):
        label = {hx.TRIGGERED_TP_ID: "TP", hx.TRIGGERED_VWAP_ID: "VWAP"}[sym_id]
        scenario = r"""
        await H.boot({});
        H.route(/^\/api\/chart\/(.+)$/, function (url, o, m) { return { body: H.bodies.chartsNext[m[1]] }; });
        await H.tick(); await H.completeAnimations();
        const chart = H.chartFor('spark:%s');
        const other = H.chartFor('spark:%s');
        const ops = H.draw(chart);
        return {
          texts: ops.filter(function (o) { return o.op === 'fillText'; }).map(function (o) { return o.args[0]; }),
          strokes: ops.filter(function (o) { return o.op === 'stroke'; }).length,
          untriggered: H.draw(other).filter(function (o) { return o.op === 'fillText'; }).length
        };
        """ % (sym_id, hx.UNTRIGGERED_ID)
        r = _run(scenario)
        assert r["texts"] == [label], "the exit badge is drawn after the poll"
        assert r["strokes"] >= 1, "the exit hairline is drawn after the poll"
        assert r["untriggered"] == 0, "an untriggered card never gets an exit badge"

    def test_the_detail_chart_is_not_rebuilt_by_a_poll_and_keeps_its_toggles(self):
        scenario = r"""
        await H.boot({});
        H.sandbox.openDetailPanel('%s');
        await H.settle();
        const opened = H.summary()['intraday-canvas'];
        await H.click('#toggle-stop');
        const toggled = H.summary()['intraday-canvas'];
        await H.tick(); await H.completeAnimations();
        const after = H.summary();
        return { opened: opened, toggled: toggled, detail: after['intraday-canvas'], spark: after['spark:%s'] };
        """ % (hx.TRIGGERED_TP_ID, hx.TRIGGERED_TP_ID)
        r = _run(scenario)
        stop_before = [d for d in r["toggled"]["datasets"] if d["label"] == "Stop"][0]
        stop_after = [d for d in r["detail"]["datasets"] if d["label"] == "Stop"][0]
        assert r["opened"]["live"], "the panel builds its intraday chart on open"
        assert stop_before["hidden"] is True, "the toggle click hides the Stop overlay"
        assert r["detail"]["constructs"] == 1 and r["detail"]["destroys"] == 0, (
            "a poll rebuilt the detail chart"
        )
        assert stop_after["hidden"] is True, "a poll reset the overlay toggle"
        assert r["spark"]["constructs"] == 1 and r["spark"]["destroys"] == 0, (
            "the card sparkline under the open panel was rebuilt by the poll"
        )


# ---------------------------------------------------------------------------
# AC-4: after a refresh the hero shows the selected calendar window
# ---------------------------------------------------------------------------


class TestRefreshShowsTheSelectedCalendarWindow:
    def test_one_year_selected_a_poll_leaves_the_one_year_series(self):
        world = hx.get_world("default")
        scenario = r"""
        await H.boot({});
        await H.click('[data-testid="window-1y"]'); await H.completeAnimations();
        const afterClick = H.summary()['cum-chart'];
        await H.tick(); await H.completeAnimations();
        return { afterClick: afterClick, afterPoll: H.summary()['cum-chart'] };
        """
        r = _run(scenario)
        body = world["hero"]["1y"]
        assert len(hx.poll_hist(world["state"])[0]) < len(body["hist_dates"]), (
            "the world must hold more history than a poll carries, or this test proves nothing"
        )
        _hero_matches_body(r["afterClick"], body)
        _hero_matches_body(r["afterPoll"], body)

    def test_the_first_poll_after_load_shows_the_default_window_route_series(self):
        world = hx.get_world("default")
        scenario = r"""
        await H.boot({});
        await H.tick(); await H.completeAnimations();
        return { hero: H.summary()['cum-chart'] };
        """
        body = world["hero"]["30d"]
        hero = _run(scenario)["hero"]
        _hero_matches_body(hero, body)
        assert hero["labels"][0] == body["hist_dates"][0]

    def test_all_time_selected_a_poll_leaves_the_full_series(self):
        world = hx.get_world("default")
        scenario = r"""
        await H.boot({});
        await H.click('[data-testid="window-all"]'); await H.completeAnimations();
        await H.tick(); await H.completeAnimations();
        return { hero: H.summary()['cum-chart'] };
        """
        body = world["hero"]["all"]
        assert len(body["hist_dates"]) > len(hx.poll_hist(world["state"])[0])
        _hero_matches_body(_run(scenario)["hero"], body)

    def test_year_to_date_on_2_january_holds_only_points_from_that_year(self):
        world = hx.get_world("ytd")
        body = world["hero"]["ytd"]
        assert body["hist_dates"] and all(d >= "2027-01-01" for d in body["hist_dates"]), (
            "fixture sanity: the route's own year-to-date series"
        )
        scenario = r"""
        await H.boot({});
        await H.click('[data-testid="window-ytd"]'); await H.completeAnimations();
        const afterClick = H.summary()['cum-chart'];
        await H.tick(); await H.completeAnimations();
        return { afterClick: afterClick, afterPoll: H.summary()['cum-chart'] };
        """
        r = _run(scenario, "ytd", now_iso="2027-01-02T15:00:00.000Z")
        _hero_matches_body(r["afterClick"], body)
        _hero_matches_body(r["afterPoll"], body)
        assert all(d >= "2027-01-01" for d in r["afterPoll"]["labels"]), (
            "a December point leaked into YTD"
        )

    def test_year_to_date_fallback_on_2_january_is_the_calendar_window_of_the_poll_series(self):
        world = hx.get_world("ytd")
        dates, bot, held = hx.poll_hist(world["state"])
        assert any(d < "2027-01-01" for d in dates), (
            "fixture sanity: the poll series crosses the year end"
        )
        scenario = r"""
        await H.boot({});
        H.route(/^\/api\/hero-chart\//, { reject: true });
        await H.click('[data-testid="window-ytd"]'); await H.completeAnimations();
        await H.tick(); await H.completeAnimations();
        return { hero: H.summary()['cum-chart'] };
        """
        r = _run(scenario, "ytd", now_iso="2027-01-02T15:00:00.000Z")
        exp_dates, exp_bot = hx.rebase_to_window(dates, bot, "2027-01-01")
        _, exp_held = hx.rebase_to_window(dates, held, "2027-01-01")
        hero = r["hero"]
        assert hero["labels"] == exp_dates, "only points dated 1 January or later"
        hx.assert_series_close(hero["datasets"][0]["data"], exp_bot, 1e-6, "bot line")
        hx.assert_series_close(hero["datasets"][1]["data"], exp_held, 1e-6, "held line")

    @pytest.mark.parametrize("token", ["60d", "1y"])
    def test_a_refused_window_request_falls_back_to_the_calendar_window_of_the_poll_series(
        self, token
    ):
        world = hx.get_world("default")
        label = {"60d": "60d", "1y": "1Y"}[token]
        scenario = (
            r"""
        await H.boot({});
        H.route(/^\/api\/hero-chart\//, { reject: true });
        await H.click('[data-testid="window-%s"]'); await H.completeAnimations();
        H.route('/api/state', { body: H.bodies.stateNext });
        await H.tick(); await H.completeAnimations();
        return { hero: H.summary()['cum-chart'], label: H.el('#guard-alpha-window-label').text };
        """
            % token
        )
        now = hx.utc_now_iso()
        r = _run(scenario, now_iso=now)
        dates, bot, held = hx.poll_hist(world["next"]["state"])
        cutoff = hx.window_cutoff_iso(token, dt.date.fromisoformat(now[:10]))
        exp_dates, exp_bot = hx.rebase_to_window(dates, bot, cutoff)
        _, exp_held = hx.rebase_to_window(dates, held, cutoff)
        hero = r["hero"]
        assert r["label"] == label, "the window label keeps reading the selected window"
        assert hero["labels"] == exp_dates, (
            f"{token}: the calendar window of the poll's dated series"
        )
        hx.assert_series_close(hero["datasets"][0]["data"], exp_bot, 1e-6, "bot line")
        hx.assert_series_close(hero["datasets"][1]["data"], exp_held, 1e-6, "held line")
        if token == "60d":
            body = world["next"]["hero"]["60d"]
            assert hero["labels"] == body["hist_dates"], (
                "60d lies inside the poll: same days as the route"
            )
            hx.assert_series_close(
                hero["datasets"][0]["data"], body["hist_bot"], 2e-3, "bot vs route"
            )

    @pytest.mark.parametrize("order", [["1y", "30d"], ["30d", "1y"]])
    def test_the_last_click_wins_whatever_order_the_responses_arrive_in(self, order):
        world = hx.get_world("default")
        scenario = r"""
        await H.boot({});
        H.route(/^\/api\/hero-chart\//, { pending: true });
        await H.click('[data-testid="window-1y"]');
        await H.click('[data-testid="window-30d"]');
        const order = %s;
        for (let i = 0; i < order.length; i++) {
          await H.releasePending('/api/hero-chart/' + order[i], { body: H.bodies.hero[order[i]] });
        }
        await H.completeAnimations();
        const afterResponses = H.summary()['cum-chart'];
        await H.tick(); await H.completeAnimations();
        return { afterResponses: afterResponses, afterPoll: H.summary()['cum-chart'],
                 label: H.el('#guard-alpha-window-label').text };
        """ % json.dumps(order)
        r = _run(scenario)
        body = world["hero"]["30d"]
        assert body["hist_dates"] != world["hero"]["1y"]["hist_dates"], (
            "fixture sanity: the windows differ"
        )
        _hero_matches_body(r["afterResponses"], body)
        _hero_matches_body(r["afterPoll"], body)
        assert r["label"] == "30d"

    def test_window_click_and_unchanged_poll_send_the_requests_they_sent_before(self):
        world = hx.get_world("default")
        scenario = r"""
        await H.boot({});
        const a = H.fetchLog.length;
        await H.click('[data-testid="window-60d"]');
        const clickUrls = H.fetchLog.slice(a).map(function (e) { return e.url; });
        const b = H.fetchLog.length;
        await H.tick();
        const pollUrls = H.fetchLog.slice(b).map(function (e) { return e.url; });
        return { clickUrls: clickUrls, pollUrls: pollUrls };
        """
        r = _run(scenario)
        assert sorted(r["clickUrls"]) == sorted(
            ["/api/hero-chart/60d", "/api/strip/60d", "/api/guard-alpha-summary?window=60d"]
        ), "a picker click re-windows the chart, the strip headline and the dollar-saved panel"
        ids = [
            v["id"]
            for v in world["state"]["bot_state"].values()
            if isinstance(v, dict) and v.get("id")
        ]
        assert ids, "fixture sanity: the poll carries symphony ids"
        poll = r["pollUrls"]
        assert poll.count("/api/state") == 1
        for sym_id in ids:
            assert poll.count(f"/api/chart/{sym_id}") == 1, sym_id
        extra = [u for u in poll if u != "/api/state" and not u.startswith("/api/chart/")]
        assert set(extra) <= {"/api/strip/30d"}, (
            f"an unchanged-series poll sent unexpected requests: {extra}"
        )
        assert not [u for u in poll if u.startswith("/api/hero-chart/")]

    def test_a_poll_requests_the_hero_series_only_when_the_series_changed(self):
        scenario = r"""
        await H.boot({});
        const hero = function () { return H.requests(/^\/api\/hero-chart\//); };
        const out = { afterLoad: hero().length };
        await H.tick(); out.afterUnchanged = hero().length;
        await H.tick(); out.afterUnchangedAgain = hero().length;
        H.route('/api/state', { body: H.bodies.stateNext });
        await H.tick(); out.afterNewDay = hero().length; out.urls = hero();
        await H.tick(); out.afterNewDayRepeated = hero().length;
        return out;
        """
        r = _run(scenario)
        assert r["afterLoad"] == 0, "the page already holds the default window"
        assert r["afterUnchanged"] == 0 and r["afterUnchangedAgain"] == 0, (
            "an unchanged series sends none"
        )
        assert r["afterNewDay"] == 1 and r["urls"] == ["/api/hero-chart/30d"], (
            "a new day sends exactly one"
        )
        assert r["afterNewDayRepeated"] == 1, "the same series again sends none"


# ---------------------------------------------------------------------------
# AC-5: a theme or accent change recolours the charts
# ---------------------------------------------------------------------------

_TOKENS = hx.load_tokens()
_LIGHT = _TOKENS["light"]
_DARK = {**_LIGHT, **_TOKENS["darkOverrides"]}


def _assert_line_colours(summary: dict, tokens: dict, what: str, accent: str | None = None) -> None:
    want_accent = accent or tokens[DEFAULT_ACCENT]
    hero = summary["cum-chart"]
    assert hero["datasets"][0]["borderColor"] == want_accent, f"{what}: hero bot line"
    assert hero["datasets"][1]["borderColor"] == tokens[DIM], f"{what}: hero held line"
    for sym_id in IDS:
        spark = summary[_spark(sym_id)]
        assert spark["datasets"][0]["borderColor"] == want_accent, f"{what}: {sym_id} line"
        if len(spark["datasets"]) > 1:
            assert spark["datasets"][1]["borderColor"] == tokens[DIM], f"{what}: {sym_id} held line"


class TestThemeAndAccentRecolourTheCharts:
    def test_tokens_differ_between_themes_so_a_recolour_is_observable(self):
        assert _LIGHT[DEFAULT_ACCENT] != _DARK[DEFAULT_ACCENT]
        assert _LIGHT[DIM] != _DARK[DIM]

    def test_switching_to_dark_recolours_the_hero_and_every_sparkline(self):
        scenario = r"""
        await H.boot({});
        const before = H.summary();
        await H.setTheme('dark');
        return { before: before, after: H.summary() };
        """
        r = _run(scenario)
        _assert_line_colours(r["before"], _LIGHT, "light at load")
        _assert_line_colours(r["after"], _DARK, "after the switch to dark")

    def test_picking_another_accent_swatch_recolours_the_bot_lines(self):
        accent = _LIGHT["--studio-swatch-2"]
        assert accent != _LIGHT[DEFAULT_ACCENT], (
            "fixture sanity: swatch 2 is not the default accent"
        )
        scenario = r"""
        await H.boot({});
        await H.setAccent(%s);
        return { after: H.summary() };
        """ % json.dumps(accent)
        _assert_line_colours(
            _run(scenario)["after"], _LIGHT, "after the accent pick", accent=accent
        )

    def test_recolouring_changes_neither_points_nor_labels_nor_chart_instances(self):
        scenario = r"""
        await H.boot({});
        const before = H.summary();
        await H.setTheme('dark'); await H.completeAnimations();
        return { before: before, after: H.summary() };
        """
        r = _run(scenario)
        for key in ["cum-chart"] + [_spark(i) for i in IDS]:
            b, a = r["before"][key], r["after"][key]
            assert a["chartId"] == b["chartId"], f"{key}: a recolour rebuilt the chart"
            assert a["constructs"] == 1 and a["destroys"] == 0, key
            assert a["labels"] == b["labels"], key
            for i, ds in enumerate(a["datasets"]):
                assert ds["data"] == b["datasets"][i]["data"], f"{key}: dataset {i} points changed"
            assert set(a["updates"]) <= {"none"}, (
                f"{key}: a recolour replayed animation: {a['updates']}"
            )

    def test_light_dark_light_ends_on_the_light_tokens(self):
        scenario = r"""
        await H.boot({});
        await H.setTheme('dark'); const dark = H.summary();
        await H.setTheme('light');
        return { dark: dark, final: H.summary() };
        """
        r = _run(scenario)
        _assert_line_colours(r["dark"], _DARK, "dark")
        _assert_line_colours(r["final"], _LIGHT, "light again")

    def test_an_open_detail_chart_takes_the_new_colours_too(self):
        scenario = (
            r"""
        await H.boot({});
        H.sandbox.openDetailPanel('%s');
        await H.settle();
        const before = H.summary()['intraday-canvas'];
        await H.setTheme('dark');
        return { before: before, after: H.summary()['intraday-canvas'] };
        """
            % hx.TRIGGERED_TP_ID
        )
        r = _run(scenario)
        wanted = {
            "Bot": "--studio-accent",
            "Held": "--studio-ink-faint",
            "Stop": "--studio-neg",
            "Breakeven": "--studio-accent",
            "VWAP": "--studio-cyan",
            "MC %": "--studio-warn",
        }
        by_label = {d["label"]: d for d in r["after"]["datasets"]}
        assert set(by_label) == set(wanted)
        for label, token in wanted.items():
            assert by_label[label]["borderColor"] == _DARK[token], f"detail chart {label}"
        assert r["after"]["constructs"] == 1 and r["after"]["destroys"] == 0
        before_by_label = {d["label"]: d for d in r["before"]["datasets"]}
        for label in wanted:
            assert by_label[label]["data"] == before_by_label[label]["data"], (
                f"{label} points changed"
            )

    @pytest.mark.parametrize(
        ("sym_id", "token"),
        [(hx.TRIGGERED_TP_ID, "--studio-pos"), (hx.TRIGGERED_VWAP_ID, "--studio-plum")],
    )
    def test_exit_marker_follows_the_theme_at_draw(self, sym_id, token):
        scenario = r"""
        await H.boot({});
        const light = H.draw(H.chartFor('spark:%s'));
        await H.setTheme('dark');
        const dark = H.draw(H.chartFor('spark:%s'));
        function strokeStyles(ops) {
          return ops.filter(function (o) { return o.op === 'set' && o.prop === 'strokeStyle'; }).map(function (o) { return o.value; });
        }
        function fillStyles(ops) {
          return ops.filter(function (o) { return o.op === 'set' && o.prop === 'fillStyle'; }).map(function (o) { return o.value; });
        }
        return { lightStroke: strokeStyles(light), darkStroke: strokeStyles(dark), darkFill: fillStyles(dark) };
        """ % (sym_id, sym_id)
        r = _run(scenario)
        assert r["lightStroke"][0] == _LIGHT[token], "light marker colour"
        assert r["darkStroke"][0] == _DARK[token], (
            "the marker keeps its light colour after a theme switch"
        )
        assert _DARK[token] in r["darkFill"], "the exit badge fill follows the theme too"


_DIAL_TOKEN_REF = "var(--studio-%s)"


def _dial_token(mc_prob: float | None, triggered: bool) -> str:
    """The thresholds of the dial's existing colour rule, as token references."""
    if triggered:
        return _DIAL_TOKEN_REF % "ink-faint"
    assert mc_prob is not None
    if mc_prob < 15:
        return _DIAL_TOKEN_REF % "warn"
    if mc_prob > 80:
        return _DIAL_TOKEN_REF % "ink-dim"
    return _DIAL_TOKEN_REF % "accent"


class TestDialArcFollowsTheTheme:
    def test_triggered_dials_reference_the_faint_ink_token_not_a_computed_colour(self):
        scenario = r"""
        await H.boot({});
        const light = {}; const dark = {};
        ["%s", "%s"].forEach(function (id) { light[id] = H.dial(id); });
        await H.setTheme('dark');
        ["%s", "%s"].forEach(function (id) { dark[id] = H.dial(id); });
        return { light: light, dark: dark };
        """ % (hx.TRIGGERED_TP_ID, hx.TRIGGERED_VWAP_ID, hx.TRIGGERED_TP_ID, hx.TRIGGERED_VWAP_ID)
        r = _run(scenario)
        for sym_id in (hx.TRIGGERED_TP_ID, hx.TRIGGERED_VWAP_ID):
            assert r["light"][sym_id]["stroke"] == _dial_token(None, True), sym_id
            assert r["dark"][sym_id]["stroke"] == _dial_token(None, True), (
                f"{sym_id}: the arc must reference the token so a theme change recolours it with no redraw"
            )

    @pytest.mark.parametrize("mc_prob", [0.0, 14.99, 15.0, 50.0, 80.0, 80.5, 100.0])
    def test_untriggered_dial_arc_references_the_token_its_reading_selects(self, mc_prob):
        world = hx.get_world("default")
        charts = copy.deepcopy(world["charts"])
        charts[hx.UNTRIGGERED_ID]["data"][-1]["mc_prob"] = mc_prob  # named field of a real point
        state = copy.deepcopy(world["state"])
        for sym in state["symphonies"]:
            if sym["id"] == hx.UNTRIGGERED_ID:
                sym["mc_prob"] = mc_prob  # the same named field of the real poll entry
        scenario = r"""
        await H.boot({ charts: H.bodies.editedCharts, state: H.bodies.editedState });
        const light = H.dial('%s');
        await H.setTheme('dark');
        return { light: light, dark: H.dial('%s') };
        """ % (hx.UNTRIGGERED_ID, hx.UNTRIGGERED_ID)
        r = _run(scenario, extra_bodies={"editedCharts": charts, "editedState": state})
        expected = _dial_token(mc_prob, False)
        assert r["light"]["stroke"] == expected, f"mc_prob {mc_prob}"
        assert r["dark"]["stroke"] == expected, (
            f"mc_prob {mc_prob}: still the same token reference after the switch"
        )
