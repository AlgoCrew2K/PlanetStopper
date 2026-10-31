"""The headline and the Ann. Vol row keep the hero window the operator picked, through every refresh.

Real call path: the whole of ``static/index.js`` (and, where noted, ``static/chrome.js``) runs under
node (tests/dashboard/_index_js_harness.py) over the page ``GET /`` renders, with the bodies the real
``/api/state``, ``/api/strip/<window>`` and ``/api/hero-chart/<window>`` routes return for a seeded
database. The 30 s poll is the page's own interval, advanced with ``H.runTimers``; a picker click is a
click on the real button; a cycle event reaches the registered stream listener.

Expected text is recomputed from the body a scenario serves with the client's own format
(``fmt_pct``, ``vol_texts``), never copied. Derived bodies change one named field of a real body:
tests/fixtures/refresh_timing/03-c1/PROVENANCE.md.
"""

from __future__ import annotations

import pytest

from tests.dashboard import _index_js_harness as hx
from tests.dashboard import _refresh_timing_support as sup

WORLD = "default"


def _world() -> dict:
    return hx.get_world(WORLD)


def _bodies() -> dict:
    """Poll bodies: the same stamp as the page render, a later stamp, and a later stamp with no guard alpha."""
    world = _world()
    stamp = sup.render_stamp(world)
    equal = sup.with_stamp(world["state"], stamp)
    later = sup.with_stamp(world["next"]["state"], sup.later_stamp(stamp))
    return {
        "pollEqual": equal,
        "pollNew": later,
        "pollNewNullGuard": sup.with_guard_alpha(later, None),
        "eventChanged": sup.event_payload(changed=True),
    }


def _run(scenario: str, *, extra: dict | None = None, scripts=sup.INDEX_ONLY, world: str = WORLD):
    bodies = _bodies() if world == WORLD else {}
    bodies.update(extra or {})
    return sup.run(scenario, world=world, scripts=scripts, extra_bodies=bodies)


def _strip(token: str) -> dict:
    return _world()["strips"][token]


def _headline(token: str, window_label: str) -> dict:
    return {"headline": sup.fmt_pp(_strip(token)["guard_alpha"]), "label": window_label}


PICK_1Y = "await H.click('[data-testid=\"window-1y\"]');"


# ---------------------------------------------------------------------------
# S-1.1, S-1.2: a refresh never puts the poll's default-window value under the picked window
# ---------------------------------------------------------------------------


class TestPickedWindowSurvivesEveryRefresh:
    def test_preconditions_the_windows_differ_on_the_headline_and_the_vol_row(self):
        """Without this the scenarios below could not tell the picked window from the default."""
        bodies = _bodies()
        poll_text = sup.fmt_pp(bodies["pollNew"]["portfolio_strip"]["guard_alpha"])
        assert poll_text != sup.fmt_pp(_strip("1y")["guard_alpha"]), "poll and 1y headline equal"
        assert sup.vol_texts(_strip("30d")) != sup.vol_texts(_strip("1y")), (
            "30d and 1y vol rows equal"
        )
        assert bodies["pollNew"]["meta"]["portfolio"].get("vol_held") is None, (
            "the poll's own vol row needs both sides, and its held side is null"
        )

    def test_a_poll_after_picking_1y_keeps_the_1y_headline_label_and_vol_row(self):
        scenario = (
            r"""
        await H.boot({ extras: true });
        """
            + PICK_1Y
            + r"""
        const picked = { ga: H.guardAlpha(), vol: H.vol() };
        H.route('/api/state', { body: H.bodies.pollNew });
        await H.runTimers(30000);
        return { picked: picked, after: { ga: H.guardAlpha(), vol: H.vol() } };
        """
        )
        r = _run(scenario)
        assert r["picked"]["ga"] == _headline("1y", "1Y"), "the click shows the 1y strip"
        assert r["after"]["ga"] == r["picked"]["ga"], (
            "a 30 s poll put its 30d value under the 1Y label"
        )
        assert r["after"]["vol"] == sup.vol_texts(_strip("1y")), (
            "the poll's lifetime vol row replaced the 1y one"
        )

    def test_a_cycle_event_after_picking_1y_keeps_the_1y_headline_label_and_vol_row(self):
        scenario = (
            r"""
        await H.boot({ extras: true });
        """
            + PICK_1Y
            + r"""
        const picked = { ga: H.guardAlpha(), vol: H.vol() };
        H.route('/api/state', { body: H.bodies.pollNew });
        await H.sse('cycle-complete', H.bodies.eventChanged);
        return { picked: picked, after: { ga: H.guardAlpha(), vol: H.vol() }, open: H.liveStreams() };
        """
        )
        r = _run(scenario)
        assert r["open"] == 1, "the scenario needs the page's stream open to deliver the event"
        assert r["after"]["ga"] == r["picked"]["ga"] == _headline("1y", "1Y")
        assert r["after"]["vol"] == sup.vol_texts(_strip("1y"))


# ---------------------------------------------------------------------------
# S-1.3, S-1.11, S-1.12: which strip a refresh asks for, and when
# ---------------------------------------------------------------------------


class TestWhichStripARefreshRequests:
    def test_a_poll_without_guard_alpha_asks_for_the_picked_window_never_30d(self):
        scenario = (
            r"""
        await H.boot({ extras: true });
        """
            + PICK_1Y
            + r"""
        const before = H.guardAlpha();
        const n0 = H.fetchLog.length;
        H.route(/^\/api\/strip\/1y$/, { pending: true });
        H.route('/api/state', { body: H.bodies.pollNewNullGuard });
        await H.runTimers(30000);
        const during = H.guardAlpha();
        await H.releasePending('/api/strip/1y', { body: H.bodies.strips['1y'] });
        const after = H.guardAlpha();
        const strips = H.fetchLog.slice(n0).map(function (e) { return e.url; })
          .filter(function (u) { return /^\/api\/strip\//.test(u); });
        return { before: before, during: during, after: after, strips: strips };
        """
        )
        r = _run(scenario)
        assert r["strips"] == ["/api/strip/1y"], (
            f"a refresh with a new stamp asks for the picked window's strip once: {r['strips']}"
        )
        assert r["during"] == r["before"] == _headline("1y", "1Y"), (
            "the headline keeps its 1Y value until the strip answers"
        )
        assert r["after"] == r["before"]

    def test_a_refresh_with_a_new_stamp_sends_exactly_one_strip_request(self):
        scenario = (
            r"""
        await H.boot({ extras: true });
        """
            + PICK_1Y
            + r"""
        const n0 = H.fetchLog.length;
        H.route('/api/state', { body: H.bodies.pollNew });
        await H.runTimers(30000);
        return { strips: H.fetchLog.slice(n0).map(function (e) { return e.url; })
          .filter(function (u) { return /^\/api\/strip\//.test(u); }) };
        """
        )
        assert _run(scenario)["strips"] == ["/api/strip/1y"]

    def test_a_refresh_with_the_same_stamp_sends_no_strip_request(self):
        scenario = (
            r"""
        await H.boot({ extras: true, state: H.bodies.pollEqual });
        """
            + PICK_1Y
            + r"""
        const picked = H.guardAlpha();
        const n0 = H.fetchLog.length;
        H.route('/api/state', { body: H.bodies.pollEqual });
        await H.runTimers(30000);
        await H.runTimers(30000);
        return { picked: picked, after: H.guardAlpha(),
          strips: H.fetchLog.slice(n0).map(function (e) { return e.url; })
          .filter(function (u) { return /^\/api\/strip\//.test(u); }) };
        """
        )
        r = _run(scenario)
        assert r["strips"] == [], "an unchanged stamp must not refetch the strip"
        assert r["picked"] == _headline("1y", "1Y")
        assert r["after"] == r["picked"], (
            "with no request, the poll's own value replaced the headline"
        )


# ---------------------------------------------------------------------------
# S-1.4, S-1.13: no window picked (the 30d default)
# ---------------------------------------------------------------------------


class TestDefaultWindow:
    def test_three_polls_with_no_new_data_leave_the_headline_as_rendered(self):
        scenario = r"""
        const render = H.guardAlpha();
        await H.boot({ extras: true, state: H.bodies.pollEqual });
        const seen = [H.guardAlpha()];
        H.route('/api/state', { body: H.bodies.pollEqual });
        for (let i = 0; i < 3; i++) { await H.runTimers(30000); seen.push(H.guardAlpha()); }
        return { render: render, seen: seen };
        """
        r = _run(scenario)
        assert r["render"] == _headline("30d", "30d"), "the render holds the 30d value"
        assert r["seen"] == [r["render"]] * 4, "a poll moved the headline off its rendered value"

    def test_the_first_poll_shows_the_30d_strips_vol_not_the_polls_lifetime_vol(self):
        world = _world()
        poll = sup.with_poll_vol(_bodies()["pollEqual"], 0.0123, 0.0456)  # lifetime vol of the poll
        scenario = r"""
        await H.boot({ extras: true, state: H.bodies.pollLifetimeVol });
        return { vol: H.vol(), loaded: typeof H.sandbox.window.updateChromeTicker,
                 strips: H.requests(/^\/api\/strip\//) };
        """
        r = _run(scenario, extra={"pollLifetimeVol": poll}, scripts=sup.CHROME_AND_INDEX)
        assert r["loaded"] == "function", "static/chrome.js and static/index.js load into one page"
        assert r["strips"] == ["/api/strip/30d"], r["strips"]
        assert r["vol"] == sup.vol_texts(world["strips"]["30d"])

    def test_the_first_poll_shows_dashes_when_the_30d_strip_has_a_null_side(self):
        world = _world()
        poll = sup.with_poll_vol(_bodies()["pollEqual"], 0.0123, 0.0456)
        strips = dict(
            world["strips"], **{"30d": sup.strip_with(world["strips"]["30d"], vol_held=None)}
        )
        scenario = r"""
        await H.boot({ extras: true, state: H.bodies.pollLifetimeVol });
        return { vol: H.vol() };
        """
        r = _run(scenario, extra={"pollLifetimeVol": poll, "strips": strips})
        assert r["vol"] == sup.vol_texts(strips["30d"]), (
            "a null side on the strip reads as dashes, never the poll's lifetime pair"
        )

    def test_the_30d_vol_row_stays_dashes_through_a_click_and_three_polls(self):
        world = _world()
        base = _bodies()
        polls = {
            "pollEqual": sup.with_poll_vol(base["pollEqual"], 0.0123, 0.0456),
            "pollNew": sup.with_poll_vol(base["pollNew"], 0.0123, 0.0456),
        }
        strips = dict(
            world["strips"], **{"30d": sup.strip_with(world["strips"]["30d"], vol_bot=None)}
        )
        scenario = r"""
        await H.boot({ extras: true, state: H.bodies.pollEqual });
        const seen = [H.vol()];
        await H.click('[data-testid="window-30d"]'); seen.push(H.vol());
        for (let i = 0; i < 3; i++) {
          H.route('/api/state', { body: i % 2 ? H.bodies.pollEqual : H.bodies.pollNew });
          await H.runTimers(30000); seen.push(H.vol());
        }
        return { seen: seen };
        """
        r = _run(scenario, extra={**polls, "strips": strips})
        dashes = sup.vol_texts(strips["30d"])
        assert dashes["bot"].endswith(sup.DASH)
        assert r["seen"] == [dashes] * 5, "a lifetime value showed under 30d"


# ---------------------------------------------------------------------------
# S-1.5: the vol row keeps the picked window (see also the first test class)
# ---------------------------------------------------------------------------


def test_the_poll_with_a_lifetime_vol_and_a_null_held_side_leaves_the_1y_vol_row():
    scenario = (
        r"""
    await H.boot({ extras: true });
    """
        + PICK_1Y
        + r"""
    const picked = H.vol();
    H.route('/api/state', { body: H.bodies.pollNew });
    await H.runTimers(30000);
    await H.runTimers(30000);
    return { picked: picked, after: H.vol() };
    """
    )
    r = _run(scenario)
    assert r["picked"] == sup.vol_texts(_strip("1y"))
    assert r["after"] == r["picked"], "the row flipped between the 1y pair and the poll's dashes"


# ---------------------------------------------------------------------------
# S-1.7: a response for an earlier pick that lands after a later one is discarded
# ---------------------------------------------------------------------------


def test_the_1y_strip_that_lands_after_a_later_30d_pick_is_discarded():
    scenario = r"""
    await H.boot({ extras: true, state: H.bodies.pollEqual });
    const base = { ga: H.guardAlpha(), vol: H.vol() };
    H.route(/^\/api\/strip\/(1y|30d)$/, { pending: true });
    await H.click('[data-testid="window-1y"]');
    await H.click('[data-testid="window-30d"]');
    await H.releasePending('/api/strip/30d', { body: H.bodies.strips['30d'] });
    await H.releasePending('/api/strip/1y', { body: H.bodies.strips['1y'] });
    return { base: base, after: { ga: H.guardAlpha(), vol: H.vol() } };
    """
    r = _run(scenario)
    assert r["base"]["ga"] == _headline("30d", "30d")
    assert r["after"] == r["base"], "the 1y answer, issued first and landing last, was applied"


# ---------------------------------------------------------------------------
# S-1.8: a failed refresh request keeps the last 1Y values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spec",
    [
        "{ status: 500, body: { status: 'error', message: 'An internal error occurred' } }",
        "{ reject: true }",
    ],
    ids=["server error", "network failure"],
)
def test_a_failed_1y_strip_request_keeps_the_last_1y_values(spec):
    scenario = (
        r"""
    await H.boot({ extras: true, state: H.bodies.pollEqual });
    """
        + PICK_1Y
        + r"""
    const picked = { ga: H.guardAlpha(), vol: H.vol() };
    H.route(/^\/api\/strip\/1y$/, __SPEC__);
    H.route('/api/state', { body: H.bodies.pollNew });
    await H.runTimers(30000);
    return { picked: picked, after: { ga: H.guardAlpha(), vol: H.vol() },
             asked: H.requests(/^\/api\/strip\/1y$/).length };
    """
    ).replace("__SPEC__", spec)
    r = _run(scenario)
    assert r["asked"] >= 2, "the refresh must ask for the picked window (the click was the first)"
    assert r["picked"]["ga"] == _headline("1y", "1Y")
    assert r["after"] == r["picked"], "a failed answer changed the headline, label or vol row"


# ---------------------------------------------------------------------------
# S-1.9: everything else a refresh writes is unchanged by the picked window
# ---------------------------------------------------------------------------


def test_only_the_max_dd_row_follows_the_picked_window():
    def scenario(token: str) -> str:
        return (
            r"""
        await H.boot({ extras: true, state: H.bodies.pollEqual });
        await H.click('[data-testid="window-__TOKEN__"]');
        const n0 = H.fetchLog.length;
        H.route('/api/state', { body: H.bodies.pollNew });
        await H.sse('cycle-complete', H.bodies.eventChanged);
        const rows = {};
        ['today', 'cumulative', 'mdd'].forEach(function (id) {
          rows[id] = [H.text('[data-testid="comp-' + id + '-bot-text"]'),
                      H.text('[data-testid="comp-' + id + '-held-text"]'),
                      H.text('[data-testid="comp-' + id + '-delta"]')];
        });
        const urls = H.fetchLog.slice(n0).map(function (e) { return e.url; });
        return { rows: rows,
                 hero: urls.filter(function (u) { return /^\/api\/hero-chart\//.test(u); }),
                 saved: urls.filter(function (u) { return /^\/api\/guard-alpha-summary/.test(u); }) };
        """
        ).replace("__TOKEN__", token)

    control, picked = _run(scenario("30d")), _run(scenario("1y"))
    for row in ("today", "cumulative"):
        assert picked["rows"][row] == control["rows"][row], (
            f"the picked window changed the {row} row"
        )
    # The Max DD row is the picked window's own: the strip's figures, whatever the poll carries.
    for result, token in ((control, "30d"), (picked, "1y")):
        window = sup.mdd_texts(_strip(token))
        bot, held, delta = result["rows"]["mdd"]
        assert [bot, held] == [window["bot"], window["held"]], (
            f"{token}: the Max DD row is not the picked window's"
        )
        assert abs(sup.pp_delta_value(delta) - window["delta_pp"]) <= 0.006, (
            f"{token}: the Max DD delta {delta!r} is not the picked window's"
        )
    assert picked["rows"]["mdd"] != control["rows"]["mdd"], (
        "the two windows must differ for this to tell the picked window from the default"
    )
    assert picked["hero"] == ["/api/hero-chart/1y"], picked["hero"]
    assert picked["saved"] == ["/api/guard-alpha-summary?window=1y"], picked["saved"]


# ---------------------------------------------------------------------------
# S-1.10: All Time
# ---------------------------------------------------------------------------


def test_a_poll_after_picking_all_time_keeps_the_all_time_headline():
    scenario = r"""
    await H.boot({ extras: true, state: H.bodies.pollEqual });
    await H.click('[data-testid="window-all"]');
    const picked = H.guardAlpha();
    H.route('/api/state', { body: H.bodies.pollNew });
    await H.runTimers(30000);
    return { picked: picked, after: H.guardAlpha(), strips: H.requests(/^\/api\/strip\//) };
    """
    r = _run(scenario)
    want = _headline("all", "All Time")
    assert sup.fmt_pp(_bodies()["pollNew"]["portfolio_strip"]["guard_alpha"]) != want["headline"]
    assert r["picked"] == want
    assert r["after"] == want, "the poll replaced the All Time headline"
    assert "/api/strip/all" in r["strips"]


# ---------------------------------------------------------------------------
# S-1.13 (server half): the render carries the stamp the first poll is compared with
# ---------------------------------------------------------------------------


def test_the_server_render_embeds_the_stamp_it_prints_as_data_stamp():
    world = _world()
    block = hx.initial_data_block(hx.parse_html(world["html"]))
    assert "data_stamp" in block, "the embedded data block carries no data_stamp"
    assert block["data_stamp"] == sup.render_stamp(world), (
        "data_stamp must be the stamp the render prints, so the first poll can be compared with it"
    )


# ---------------------------------------------------------------------------
# S-1.14: a picked window with no data keeps the old series, with a visible cue
# ---------------------------------------------------------------------------

_NO_DATA_CUE = "No data yet for "
_JAN_1 = "2027-01-01T12:00:00.000Z"


class TestPickedWindowWithNoData:
    def _world(self) -> dict:
        world = hx.get_world("ytd-jan1")
        assert world["hero"]["ytd"]["hist_dates"] == [], (
            "fixture sanity: on 1 January the year-to-date window holds no row"
        )
        return world

    def _run(self, scenario: str) -> dict:
        self._world()
        return sup.run(scenario, world="ytd-jan1", scripts=sup.INDEX_ONLY, now_iso=_JAN_1)

    def test_the_empty_ytd_answer_keeps_the_drawn_series_and_shows_the_cue(self):
        scenario = r"""
        await H.boot({ extras: true });
        const before = H.summary()['cum-chart'];
        const hiddenBefore = H.el('#hero-window-no-data');
        await H.click('[data-testid="window-ytd"]'); await H.completeAnimations();
        return { before: before, after: H.summary()['cum-chart'], cueBefore: hiddenBefore,
                 cue: H.el('#hero-window-no-data'), label: H.text('#guard-alpha-window-label') };
        """
        r = self._run(scenario)
        assert r["cueBefore"]["exists"], "no #hero-window-no-data element on the page"
        assert r["cueBefore"]["hidden"] is True, "the cue shows before any window has no data"
        assert r["after"]["constructs"] == r["before"]["constructs"], "the hero was rebuilt"
        assert r["after"]["labels"] == r["before"]["labels"], "the drawn series changed"
        assert r["label"] == "YTD"
        assert r["cue"]["exists"] and r["cue"]["hidden"] is False, "the no-data cue is not shown"
        assert r["cue"]["text"] == _NO_DATA_CUE + "YTD"

    def test_a_refused_ytd_request_with_no_poll_row_in_the_window_shows_the_same_cue(self):
        scenario = r"""
        await H.boot({ extras: true });
        const before = H.summary()['cum-chart'];
        H.route(/^\/api\/hero-chart\/ytd$/, { status: 500, body: { status: 'error', message: 'An internal error occurred' } });
        await H.click('[data-testid="window-ytd"]'); await H.completeAnimations();
        return { before: before, after: H.summary()['cum-chart'], cue: H.el('#hero-window-no-data'),
                 label: H.text('#guard-alpha-window-label') };
        """
        r = self._run(scenario)
        assert r["after"]["labels"] == r["before"]["labels"], "a refused request changed the series"
        assert r["after"]["constructs"] == r["before"]["constructs"]
        assert r["label"] == "YTD"
        assert r["cue"]["exists"], "no #hero-window-no-data element on the page"
        assert r["cue"]["hidden"] is False and r["cue"]["text"] == _NO_DATA_CUE + "YTD"

    def test_the_cue_clears_when_data_for_the_window_arrives(self):
        scenario = r"""
        await H.boot({ extras: true });
        await H.click('[data-testid="window-ytd"]'); await H.completeAnimations();
        const shown = H.el('#hero-window-no-data');
        H.route(/^\/api\/hero-chart\/ytd$/, { body: H.bodies.hero.all });
        await H.click('[data-testid="window-ytd"]'); await H.completeAnimations();
        return { shown: shown, cue: H.el('#hero-window-no-data'), hero: H.summary()['cum-chart'] };
        """
        r = self._run(scenario)
        assert r["shown"]["exists"], "no #hero-window-no-data element on the page"
        assert r["shown"]["hidden"] is False, "precondition: the cue showed for the empty window"
        assert r["cue"]["hidden"] is True, "the cue stayed after a series for the window was drawn"
        assert r["hero"]["labels"] == self._world()["hero"]["all"]["hist_dates"]

    def test_the_cue_names_the_picked_window_when_another_empty_window_follows(self):
        scenario = r"""
        await H.boot({ extras: true });
        await H.click('[data-testid="window-ytd"]'); await H.completeAnimations();
        await H.click('[data-testid="window-30d"]'); await H.completeAnimations();
        return { cue: H.el('#hero-window-no-data'), hero: H.summary()['cum-chart'],
                 label: H.text('#guard-alpha-window-label') };
        """
        r = self._run(scenario)
        assert r["label"] == "30d"
        assert r["cue"]["exists"], "no #hero-window-no-data element on the page"
        assert r["cue"]["hidden"] is True, "the cue outlived the pick of a window that has data"
        assert r["hero"]["labels"] == self._world()["hero"]["30d"]["hist_dates"]
