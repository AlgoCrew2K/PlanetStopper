"""RED: /history keeps a drilled day, refuses an error as data and shows its data age.

``static/history.js`` runs unmodified over the page the real app renders; every ``/api/history``
body is the real route's over the captured post-mortem days at a frozen host clock
(``tests/ui/_history_answers``), the 500 is Flask's own page and the 401/503 are the real auth hook's.

CONTRACT PINNED
* A module-level drilled day, set by a bar click, makes each poll re-render THAT day's exits from
  the new payload (heading ``<date> exits (<n>)``) instead of today's exits.
* The ``[data-testid="history-todays-exits-btn"]`` button, and any window-picker click, clear it
  and return the table to ``Today's exits (<n>)``.
* The newest issued ``loadHistory`` request wins: an older response that lands last is dropped.
* Reads go through ``PSRequest.getJson``: a 401 navigates to ``/login``; a non-2xx, non-JSON or
  failed answer leaves the hero, bars, reason cards and table as they were.
* ``#history-data-as-of`` reads ``data as of <data_as_of>`` for a good load and
  ``connection lost`` plus ``data as of <last good data_as_of>`` after a failed one; a later good
  load clears the cue.
"""

from __future__ import annotations

import pytest

from tests.ui import _history_answers as hist
from tests.ui._page_js_harness import render_page, route, run_page
from tests.ui._shell_support import (  # noqa: F401
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    seed_history_exits,
)

HISTORY = r"/api/history/(\d+|ytd)$"
TODAYS_BUTTON = '[data-testid="history-todays-exits-btn"]'
STAMP = "#history-data-as-of"
HEADING = "#todays-exits-heading"
ROWS = "#triggers-tbody tr"

PAGE_STATE = """
  const state = () => ({
    heading: H.text('#todays-exits-heading'),
    rows: H.$$('#triggers-tbody tr').map((tr) => tr.children.map((td) => td.textContent)),
    hero: H.text('[data-testid="history-hero"]'),
    bars: H.$('#daily-chart-svg').innerHTML,
    reasons: H.$('[data-testid="by-reason-section"]').innerHTML,
    stamp: H.text('#history-data-as-of') || '',
  });
"""


@pytest.fixture
def days(tmp_path, monkeypatch):
    return seed_history_exits(tmp_path, monkeypatch.setattr)


def drill_target(body):
    """The day with the most exits among the days a bar can be clicked for, and its exit count."""
    day = max(body["daily_dates"], key=lambda d: len(body["daily_exits"].get(d, [])))
    return day, len(body["daily_exits"][day])


def run(scenario, *routes, path="/history"):
    return run_page(
        render_page(path), "await H.boot();\n" + PAGE_STATE + scenario, routes=list(routes)
    )


class TestMarkup:
    def test_the_page_has_a_stamp_and_a_todays_exits_button(self, days):
        html = render_page("/history")
        assert 'id="history-data-as-of"' in html, "the page has no data-age stamp element"
        assert 'data-testid="history-todays-exits-btn"' in html, "no way back to Today's exits"


class TestDrilledDay:
    def test_a_drilled_day_survives_the_poll(self, days):
        first, second = hist.history_answer(30), hist.history_answer(30)
        day, count = drill_target(first["json"])
        out = run(
            f"""
            await H.click('#daily-chart-svg rect[data-date="{day}"]');
            const drilled = state();
            await H.advance(30000);
            const afterPoll = state();
            await H.advance(30000);
            return {{drilled, afterPoll, afterTwo: state(), polls: H.to('/api/history/').length}};
            """,
            route(HISTORY, first, second, method="GET"),
        )
        r = out.result
        assert r["drilled"]["heading"] == f"{day} exits ({count})"
        assert r["polls"] >= 3, "the page stopped polling"
        for later in (r["afterPoll"], r["afterTwo"]):
            assert later["heading"] == r["drilled"]["heading"], "the poll erased the drilled day"
            assert later["rows"] == r["drilled"]["rows"]

    def test_the_drilled_day_is_redrawn_from_the_new_payload(self, days):
        first = hist.history_answer(30)
        day, count = drill_target(first["json"])
        changed = hist.history_answer(30)
        trimmed = changed["json"]["daily_exits"][day][:-1]
        changed["json"]["daily_exits"][day] = trimmed
        out = run(
            f"""
            await H.click('#daily-chart-svg rect[data-date="{day}"]');
            await H.advance(30000);
            return state();
            """,
            route(HISTORY, first, changed, method="GET", strict=False),
        )
        assert out.result["heading"] == f"{day} exits ({count - 1})", (
            "the drilled day must be re-rendered from the poll's payload, not kept stale"
        )

    def test_the_drilled_table_lists_exits_in_time_order_with_names(self, days):
        first = hist.history_answer(30)
        day, count = drill_target(first["json"])
        out = run(
            f"await H.click('#daily-chart-svg rect[data-date=\"{day}\"]'); return state().rows;",
            route(HISTORY, first, method="GET"),
        )
        rows = out.result
        assert len(rows) == count
        times = [row[0] for row in rows]
        assert times == sorted(times), "the drilled day's exits are not in time order"
        assert all(row[1] and row[1] != "—" for row in rows), "an exit lost its symphony name"

    def test_the_todays_exits_button_returns_to_today(self, days):
        body = hist.history_answer(30)
        day, _ = drill_target(body["json"])
        out = run(
            f"""
            const today = state();
            await H.click('#daily-chart-svg rect[data-date="{day}"]');
            const drilled = state();
            await H.click('{TODAYS_BUTTON}');
            const back = state();
            await H.advance(30000);
            return {{today, drilled, back, afterPoll: state()}};
            """,
            route(HISTORY, body, method="GET"),
        )
        r = out.result
        assert r["drilled"]["heading"].startswith(day)
        assert (
            r["back"]["heading"] == r["today"]["heading"]
            and r["back"]["rows"] == r["today"]["rows"]
        )
        assert r["afterPoll"]["heading"] == r["today"]["heading"], (
            "the poll resurrected the drilled day"
        )

    def test_a_window_click_clears_the_drilled_day(self, days):
        body30, body90 = hist.history_answer(30), hist.history_answer(90)
        day, _ = drill_target(body30["json"])
        out = run(
            f"""
            await H.click('#daily-chart-svg rect[data-date="{day}"]');
            const drilled = state().heading;
            await H.click('#window-picker button[data-window="90"]');
            return {{drilled, after: state().heading}};
            """,
            route(r"/api/history/30$", body30, method="GET"),
            route(r"/api/history/90$", body90, method="GET"),
        )
        assert out.result["drilled"].startswith(day)
        assert out.result["after"].startswith("Today's exits")


class TestNewestRequestWins:
    def test_a_window_click_beats_a_poll_that_lands_after_it(self, days):
        body30, body90 = hist.history_answer(30), hist.history_answer(90)
        assert body30["json"]["trigger_count"] != body90["json"]["trigger_count"], (
            "fixture integrity: the two windows must differ for this to prove anything"
        )
        out = run(
            """
            await H.click('#window-picker button[data-window="90"]');
            await H.advance(30000);
            await H.click('#window-picker button[data-window="30"]');
            const clicked = H.text('#val-trigger-count');
            await H.release('older-poll');
            return {clicked, after: H.text('#val-trigger-count')};
            """,
            route(r"/api/history/30$", body30, method="GET"),
            route(r"/api/history/90$", body90, body90 | {"hold": "older-poll"}, method="GET"),
        )
        assert out.result["clicked"] == str(body30["json"]["trigger_count"])
        assert out.result["after"] == out.result["clicked"], (
            "an older response replaced the clicked window"
        )


class TestNotData:
    def test_a_lapsed_session_goes_to_login(self, days):
        from tests.ui._header_status_answers import lapsed_session_answer

        out = run(
            "await H.advance(30000); return H.navigations;",
            route(
                HISTORY,
                hist.history_answer(30),
                lapsed_session_answer("/api/history/30"),
                method="GET",
            ),
        )
        assert "assign:/login" in out.result

    @pytest.mark.parametrize("which", ["html_500", "json_503", "network"])
    def test_a_failed_poll_leaves_the_page_and_says_connection_lost(self, days, which):
        bad = {
            "html_500": hist.server_error_answer(),
            "json_503": hist.unavailable_answer("/api/history/30"),
            "network": {"reject": True},
        }[which]
        good = hist.history_answer(30)
        out = run(
            """
            const before = state();
            await H.advance(30000);
            return {before, during: state()};
            """,
            route(HISTORY, good, bad, method="GET", strict=False),
        )
        before, during = out.result["before"], out.result["during"]
        for key in ("heading", "rows", "hero", "bars", "reasons"):
            assert during[key] == before[key], f"a failed poll changed {key}"
        assert "connection lost" in during["stamp"].lower()
        assert before["stamp"].replace("data as of ", "") in during["stamp"], (
            "the cue must keep the last good data stamp"
        )

    def test_the_cue_clears_when_a_good_poll_follows(self, days):
        good = hist.history_answer(30)
        out = run(
            """
            await H.advance(30000);
            const lost = state().stamp;
            await H.advance(30000);
            return {lost, recovered: state().stamp};
            """,
            route(HISTORY, good, {"reject": True}, good, method="GET"),
        )
        assert "connection lost" in out.result["lost"].lower()
        assert "connection lost" not in out.result["recovered"].lower()


class TestDataAge:
    def test_a_load_shows_the_servers_data_stamp(self, days):
        body = hist.history_answer(30)
        assert "data_as_of" in body["json"], "GET /api/history/<days> carries no data_as_of"
        out = run("return state().stamp;", route(HISTORY, body, method="GET"))
        assert out.result.startswith("data as of ")
        assert out.result.removeprefix("data as of ").strip() == body["json"]["data_as_of"]
        assert "connection lost" not in out.result.lower()

    def test_the_stamp_is_set_through_text_not_markup(self, days):
        body = hist.history_answer(30)
        body["json"]["data_as_of"] = "<img src=x onerror=alert(1)>"
        out = run(
            "return {stamp: state().stamp, imgs: H.$$('#history-data-as-of img').length};",
            route(HISTORY, body, method="GET"),
        )
        assert out.result["imgs"] == 0
        assert "<img" in out.result["stamp"]
