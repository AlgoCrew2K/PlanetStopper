"""RED: /ai-advisor never draws an error as an empty list and shows a Frontrunner build's end.

``static/ai_advisor.js`` (with the page's other scripts) runs unmodified over the page the real app
renders. Bodies are the real routes': the runs list over rows from ``database.save_autotune_run``,
the build's start and its summary from the run route and ``GET /api/frontrunner-build/last-run``
(``tests/ui/_advisor_page_answers``), the 401/503 from the real auth hook.

CONTRACT PINNED
* ``loadRecentRuns`` draws only an array. Any other answer (non-2xx, error object, HTML) keeps the
  cards on screen and shows ``Could not refresh runs`` in the page; a later good poll removes it. A 401
  navigates to ``/login``.
* ``frRunBuild`` keeps ``#fr-run-btn`` disabled from the click until the page shows that build's
  outcome or 30 minutes pass, polling ``GET /api/frontrunner-build/last-run`` every 15 s until a
  summary whose ``run_id`` is the 202's appears. Completed: ``#fr-run-status`` reads
  ``Build finished: <n> new proposals`` and the button is enabled again. Failed: the failure and its
  ``error_code`` are shown and the button is enabled. After 30 minutes with no outcome (a build lost to
  a restart included) the button is enabled and the page reads
  ``Build still running or lost; reload to check``; polling stops. No second POST can start from the tab.
"""

from __future__ import annotations

import pytest

from tests.ui import _advisor_page_answers as adv
from tests.ui._header_status_answers import et, header_answer, lapsed_session_answer, wall_of
from tests.ui._history_answers import unavailable_answer
from tests.ui._page_js_harness import render_page, route, run_page

RUNS = r"/api/autotune-runs$"
LAST = r"/api/frontrunner-build/last-run$"
START = r"/ai-advisor/frontrunner-builder/run$"
CARDS = '[data-testid="autotune-run-row"]'
BUTTON = "#fr-run-btn"
STATUS = "#fr-run-status"
THIRTY_MINUTES = 30 * 60 * 1000
LOST_TEXT = "Build still running or lost; reload to check"
STATE = f"""
  const cards = () => H.$$('{CARDS}').length;
  const fr = () => ({{status: H.text('{STATUS}'), disabled: H.$('{BUTTON}').disabled, text: H.text('{BUTTON}')}});
  const lastRunReads = () => H.to('/api/frontrunner-build/last-run').length;
"""


def run(scenario, *routes_):
    return run_page(
        render_page("/ai-advisor"),
        "await H.boot();\n" + STATE + scenario,
        routes=list(routes_),
        pathname="/ai-advisor",
    )


@pytest.fixture
def runs():
    adv.seed_runs(3)


class TestRunsList:
    def test_a_healthy_list_renders_one_card_per_run(self, runs):
        body = adv.autotune_runs_answer()
        out = run("return cards();", route(RUNS, body, method="GET"))
        assert out.result == len(body["json"]) == 3

    @pytest.mark.parametrize("failure", ["json_503", "html_500", "network"])
    def test_a_poll_that_is_not_a_list_keeps_the_cards_and_says_so(self, runs, failure):
        from tests.ui._history_answers import server_error_answer

        bad = {
            "json_503": unavailable_answer("/api/autotune-runs"),
            "html_500": server_error_answer(),
            "network": {"reject": True},
        }[failure]
        out = run(
            """
            const before = cards();
            await H.advance(15000);
            return {before, after: cards(), shown: H.surfaced()};
            """,
            route(RUNS, adv.autotune_runs_answer(), bad, method="GET"),
        )
        assert out.result["before"] == out.result["after"] == 3, (
            "a failed poll changed the card count"
        )
        assert "Could not refresh runs" in out.result["shown"]
        assert "No tuning runs recorded yet" not in out.result["shown"], (
            "an error was drawn as an empty list"
        )

    def test_the_notice_clears_on_the_next_good_poll(self, runs):
        good = adv.autotune_runs_answer()
        out = run(
            """
            await H.advance(15000);
            const lost = H.surfaced().includes('Could not refresh runs');
            await H.advance(15000);
            return {lost, after: H.surfaced().includes('Could not refresh runs'), cards: cards()};
            """,
            route(RUNS, good, unavailable_answer("/api/autotune-runs"), good, method="GET"),
        )
        assert out.result == {"lost": True, "after": False, "cards": 3}

    def test_a_lapsed_session_goes_to_login(self, runs):
        out = run(
            "await H.advance(15000); return H.navigations;",
            route(
                RUNS,
                adv.autotune_runs_answer(),
                lapsed_session_answer("/api/autotune-runs"),
                method="GET",
            ),
        )
        assert "assign:/login" in out.result


class TestFrontrunnerBuildEnd:
    def start_page(self, *, built=(2, 1), older=True):
        """Routes for a started build: the 202, the older run's summary, then this run's."""
        if older:
            adv.build_outcome(built=(0, 0))
            older_answer = adv.last_run_answer()
        else:
            older_answer = adv.last_run_answer()
        outcome = adv.build_outcome(built=built)
        return outcome, older_answer

    def test_completion_shows_the_count_and_reenables_the_button_without_a_reload(self, runs):
        outcome, older = self.start_page(built=(2, 1))
        out = run(
            """
            await H.click('#fr-run-btn');
            const started = fr();
            await H.advance(15000);
            const waiting = fr();
            await H.advance(45000);
            return {started, waiting, done: fr(), reloads: H.navigations.filter((n) => n === 'reload').length};
            """,
            route(START, outcome["started"], method="POST"),
            route(LAST, older, older, outcome["last_run"], method="GET"),
        )
        r = out.result
        assert r["started"]["disabled"] is True
        assert r["waiting"]["disabled"] is True, (
            "the button re-enabled before the build's outcome was shown"
        )
        assert r["done"]["status"].strip() == "Build finished: 3 new proposals"
        assert r["done"]["disabled"] is False
        assert r["reloads"] == 0

    def test_a_build_with_no_proposals_is_a_completed_build_not_a_failure(self, runs):
        outcome, older = self.start_page(built=(0, 0))
        out = run(
            "await H.click('#fr-run-btn'); await H.advance(60000); return {s: fr(), all: H.surfaced()};",
            route(START, outcome["started"], method="POST"),
            route(LAST, older, outcome["last_run"], method="GET"),
        )
        assert out.result["s"]["status"].strip() == "Build finished: 0 new proposals"
        assert out.result["s"]["disabled"] is False
        assert "failed" not in out.result["all"].lower()

    def test_a_failed_build_shows_its_code_and_reenables(self, runs):
        outcome, older = self.start_page(built=(RuntimeError("a"), ValueError("b")))
        out = run(
            "await H.click('#fr-run-btn'); await H.advance(60000); return {s: fr(), all: H.surfaced()};",
            route(START, outcome["started"], method="POST"),
            route(LAST, older, outcome["last_run"], method="GET"),
        )
        assert "all_symphonies_failed" in out.result["all"]
        assert out.result["s"]["disabled"] is False

    def test_the_button_stays_disabled_and_a_second_start_sends_no_post(self, runs):
        outcome, older = self.start_page()
        out = run(
            """
            await H.click('#fr-run-btn');
            const second = await H.click('#fr-run-btn');
            await H.act('call', async () => { await window.frRunBuild(); });
            return {second, disabled: fr().disabled, posts: H.posts('frontrunner-builder/run').length};
            """,
            route(START, outcome["started"], method="POST"),
            route(LAST, older, method="GET"),
        )
        assert out.result == {"second": False, "disabled": True, "posts": 1}

    def test_no_outcome_in_thirty_minutes_reads_lost_and_stops_polling(self, runs):
        outcome, older = self.start_page()
        out = run(
            f"""
            await H.click('#fr-run-btn');
            await H.advance({THIRTY_MINUTES - 5000});
            const before = fr();
            await H.advance(10000);
            const after = fr();
            const reads = lastRunReads();
            await H.advance(120000);
            return {{before, after, grew: lastRunReads() - reads}};
            """,
            route(START, outcome["started"], method="POST"),
            route(LAST, older, method="GET"),
        )
        r = out.result
        assert r["before"]["disabled"] is True, "the button re-enabled before 30 minutes"
        assert r["after"]["disabled"] is False
        assert r["after"]["status"].strip() == LOST_TEXT
        assert r["grew"] == 0, "the page kept polling after it gave up"

    def test_a_restart_during_the_build_waits_the_same_thirty_minutes(self, runs):
        outcome, older = self.start_page()
        at = et(10, 43, 5)
        out = run_page(
            render_page("/ai-advisor"),
            "await H.boot();\n"
            + STATE
            + f"""
            await H.click('#fr-run-btn');
            await H.advance(600000);
            const tenMinutes = fr();
            await H.advance({THIRTY_MINUTES - 600000 + 10000});
            return {{tenMinutes, end: fr()}};
            """,
            routes=[
                route(START, outcome["started"], method="POST"),
                route(LAST, older, method="GET"),
                route(
                    r"/api/header-status$",
                    header_answer(at, daemon_started_at="2026-10-22T09:00:00Z"),
                    header_answer(at, daemon_started_at="2026-10-22T10:44:00Z"),
                    method="GET",
                ),
            ],
            pathname="/ai-advisor",
            wall=wall_of(at),
        )
        assert out.result["tenMinutes"]["disabled"] is True
        assert (
            out.result["end"]["disabled"] is False
            and out.result["end"]["status"].strip() == LOST_TEXT
        )

    def test_a_refusal_of_the_start_itself_re_enables_the_button_and_shows_the_servers_words(
        self, runs
    ):
        out = run(
            "await H.click('#fr-run-btn'); return {s: fr(), all: H.surfaced()};",
            route(
                START,
                {
                    "status": 200,
                    "json": {"error": "advisor unavailable: ANTHROPIC_API_KEY not configured"},
                },
                method="POST",
            ),
        )
        assert out.result["s"]["disabled"] is False
        assert "ANTHROPIC_API_KEY not configured" in out.result["all"]
