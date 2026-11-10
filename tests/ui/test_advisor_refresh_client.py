"""RED: /ai-advisor never draws an error as an empty list and shows a Frontrunner build's end.

``static/ai_advisor.js`` (with the page's other scripts) runs unmodified over the page the real app
renders. Bodies are the real routes': the runs list over rows from ``database.save_autotune_run``,
the build's start and its summary from the run route and ``GET /api/frontrunner-build/last-run``
(``tests/ui/_advisor_page_answers``), the 401/503 from the real auth hook.

CONTRACT PINNED
* ``loadRecentRuns`` draws only an array. Any other answer (non-2xx, error object, HTML) keeps the
  cards on screen and shows ``Could not refresh runs`` in the page; a later good poll removes it. A 401
  navigates to ``/login``.
* The runs table states a column whose cell text is the same in every row once, in one line above
  the rows (``data-testid="autotune-shared-line"``, inside ``#autotune-runs-list``, text beginning
  ``Same for every run: `` and listing each such column as its header followed by its cell text,
  joined by ``; ``); the rows leave those cells out. A column that differs in any row stays in every
  row, a single row and an empty list print no line, and the 15 s refresh moves a column between
  the line and the rows as the runs change (the plan's AC-8).
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
from tests.ui import _page_explainers_advisor as pe
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


class TestAdvisorPageRendersItsControls:
    @pytest.mark.parametrize(
        "testid",
        ["fr-run-btn", "sb-run-btn", "chat-input", "swap-evaluate-btn", "tweak-evaluate-btn"],
    )
    def test_the_controls_are_on_the_page_when_the_environment_holds_no_key(
        self, monkeypatch, testid
    ):
        import alpha_bot_execution

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr(alpha_bot_execution, "COMPOSER_KEY_ID", "")
        monkeypatch.setattr(alpha_bot_execution, "COMPOSER_SECRET", "")
        assert f'data-testid="{testid}"' in render_page("/ai-advisor")


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
            "await H.click('#fr-run-btn'); await H.advance(60000);"
            " return {s: fr(), alerts: H.alerts.join(' | ')};",
            route(START, outcome["started"], method="POST"),
            route(LAST, older, outcome["last_run"], method="GET"),
        )
        assert out.result["s"]["status"].strip() == "Build finished: 0 new proposals"
        assert out.result["s"]["disabled"] is False
        # The build's own status and any alert; the page's definitions may use the word "failed".
        assert "failed" not in (out.result["s"]["status"] + out.result["alerts"]).lower()

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


SHARED_LINE = '[data-testid="autotune-shared-line"]'
HEADERS = ".autotune-row--head [role='columnheader']"
ROWS_STATE = f"""
  const norm = (t) => String(t || '').replace(/\\s+/g, ' ').trim();
  const rowTexts = () => H.$$('#autotune-runs-list {CARDS}').map((r) => norm(r.textContent));
  const lineTexts = () => H.$$('{SHARED_LINE}').map((l) => norm(l.textContent));
  const heads = () => H.$$("{HEADERS}").map((h) => norm(h.textContent));
  const listHtml = () => H.$('#autotune-runs-list').innerHTML;
"""
OLD_CELL_WORDS = ("N/A", "Frozen-eval", "Fallback")
# The score column's header when the seeded runs carry no objective; a lifted column has no header.
SCORE_HEAD = "Best objective score"
STREAK_HEAD = "Runs without adopting a tuned config"


def shared_run(scenario, *routes_):
    return run_page(
        render_page("/ai-advisor"),
        "await H.boot();\n" + STATE + ROWS_STATE + scenario,
        routes=list(routes_),
        pathname="/ai-advisor",
    )


def _body_names(rows) -> list[str]:
    return [r.get("symphony_name") or r["symphony_id"] for r in rows]


class TestSharedLine:
    """The plan's AC-8 on the real page: the real route's rows, the page's own script."""

    def test_identical_columns_are_stated_once_in_the_line_and_left_out_of_every_row(self):
        pe.seed_autotune_runs(11)
        body = adv.autotune_runs_answer()
        out = shared_run(
            "return {rows: rowTexts(), lines: lineTexts(), heads: heads(), html: listHtml()};",
            route(RUNS, body, method="GET"),
        )
        r = out.result
        names = _body_names(body["json"])
        expected = (
            pe.SHARED_LINE_PREFIX
            + f"Outcome Fallback, Frozen-eval not evaluated; {SCORE_HEAD} N/A; Sel t-stat N/A"
        )
        assert r["heads"] == ["Symphony", "Run time (ET)", STREAK_HEAD], (
            f"a stated column keeps its header: {r['heads']!r}"
        )
        assert r["lines"] == [expected], f"the line reads {r['lines']!r}, not {expected!r}"
        assert len(r["rows"]) == 11
        for text in r["rows"]:
            for word in OLD_CELL_WORDS:
                assert word not in text, f"a row repeats {word!r}: {text!r}"
        for name, text in zip(names, r["rows"], strict=True):
            assert name in text, f"a row lost its symphony {name!r}: {text!r}"
            assert "adoption streak: insufficient history" in text, (
                f"a row lost its streak cell: {text!r}"
            )
        assert "adoption streak" not in r["lines"][0], "the streak column is never lifted"
        assert r["html"].index("autotune-shared-line") < r["html"].index("autotune-run-row"), (
            "the line is not above the rows"
        )

    def test_every_row_keeps_its_run_time_in_its_own_cell(self):
        pe.seed_autotune_runs(11)
        out = shared_run(
            "return H.$$('.autotune-run-time').map((c) => norm(c.textContent));",
            route(RUNS, adv.autotune_runs_answer(), method="GET"),
        )
        stamps = out.result
        assert len(stamps) == 11 and all(stamps), "a row lost its run time"
        assert len(set(stamps)) == 11, "premise: the seeded run times differ"

    def test_a_lifted_column_leaves_the_row_with_no_cell_left_behind(self):
        pe.seed_autotune_runs(11)
        out = shared_run(
            f"const rows = H.$$('#autotune-runs-list {CARDS}');"
            f' return {{head: H.$$("{HEADERS}").length,'
            " perRow: rows.map((r) => r.children.length),"
            " cells: rows.map((r) => Array.from(r.children).map((c) => norm(c.textContent))),"
            " leftover: rows.map((r) => r.querySelectorAll("
            "'.autotune-run-outcome, .autotune-run-score, .autotune-run-tstat').length)};",
            route(RUNS, adv.autotune_runs_answer(), method="GET"),
        )
        r = out.result
        assert r["head"] > 0 and len(r["perRow"]) == 11, "premise: the rows rendered"
        assert r["perRow"] == [r["head"]] * 11, (
            f"a row holds {r['perRow']!r} cells against {r['head']} header cells"
        )
        assert r["leftover"] == [0] * 11, f"a lifted column left a cell in a row: {r['leftover']!r}"
        for cells in r["cells"]:
            assert all(cells), f"a row holds an empty cell: {cells!r}"

    def test_a_column_that_differs_in_one_row_stays_in_every_row(self):
        pe.seed_autotune_runs(11, adopted_at=3)
        body = adv.autotune_runs_answer()
        out = shared_run(
            "return {rows: rowTexts(), lines: lineTexts(), heads: heads(),"
            f" pills: H.$$('{CARDS} .decision-pill').length}};",
            route(RUNS, body, method="GET"),
        )
        r = out.result
        assert r["heads"] == ["Symphony", "Run time (ET)", "Outcome", STREAK_HEAD], (
            f"only the two stated columns leave the header: {r['heads']!r}"
        )
        assert r["lines"] == [pe.SHARED_LINE_PREFIX + f"{SCORE_HEAD} N/A; Sel t-stat N/A"], r[
            "lines"
        ]
        assert r["pills"] == 11, "Outcome left a row although it differs in one"
        for text in r["rows"]:
            assert "Frozen-eval" in text, f"Outcome lost its frozen-eval text: {text!r}"
            assert "N/A" not in text, f"a row repeats a shared cell: {text!r}"

    def test_exactly_one_row_prints_no_line_and_shows_every_cell(self):
        pe.seed_autotune_runs(1)
        body = adv.autotune_runs_answer()
        out = shared_run(
            "return {rows: rowTexts(), lines: lineTexts(), html: listHtml()};",
            route(RUNS, body, method="GET"),
        )
        r = out.result
        assert r["lines"] == [] and "Same for every run" not in r["html"]
        assert len(r["rows"]) == 1
        text = r["rows"][0]
        assert "Fallback" in text and "Frozen-eval not evaluated" in text
        assert text.count("N/A") == 2, f"the score and the t-stat cells read N/A: {text!r}"

    def test_no_runs_keep_the_empty_state_and_print_no_line(self):
        out = shared_run(
            "return {text: H.pageText(), lines: lineTexts(), rows: cards()};",
            route(RUNS, {"json": []}, method="GET"),
        )
        assert out.result["lines"] == [] and out.result["rows"] == 0
        assert "No tuning runs recorded yet" in out.result["text"]
        assert "Same for every run" not in out.result["text"]

    def test_a_refresh_that_brings_a_differing_run_returns_the_column_to_every_row(self):
        keys = pe.seed_autotune_runs(11)
        first = adv.autotune_runs_answer()
        pe.save_run_like("adopted", keys[0], "2026-10-30T04:00:00")
        second = adv.autotune_runs_answer()
        out = shared_run(
            """
            const before = {lines: lineTexts(), rows: rowTexts()};
            await H.advance(15000);
            return {before, after: {lines: lineTexts(), rows: rowTexts()}};
            """,
            route(RUNS, first, second, method="GET"),
        )
        before, after = out.result["before"], out.result["after"]
        assert len(before["lines"]) == 1 and "Outcome" in before["lines"][0]
        assert len(after["lines"]) == 1
        assert "Outcome" not in after["lines"][0], "Outcome stayed in the line after it differed"
        assert len(after["rows"]) == 11
        for text in after["rows"]:
            assert "Frozen-eval" in text, f"Outcome did not return to every row: {text!r}"

    def test_a_refresh_that_fails_keeps_the_last_table_and_line(self):
        pe.seed_autotune_runs(11)
        good = adv.autotune_runs_answer()
        out = shared_run(
            """
            const before = {lines: lineTexts(), rows: rowTexts()};
            await H.advance(15000);
            return {before, after: {lines: lineTexts(), rows: rowTexts()}, shown: H.surfaced()};
            """,
            route(RUNS, good, {"reject": True}, method="GET"),
        )
        assert out.result["before"] == out.result["after"], "a failed refresh changed the table"
        assert len(out.result["after"]["lines"]) == 1
        assert "Could not refresh runs" in out.result["shown"]

    def test_polls_that_bring_the_same_runs_never_repeat_the_line(self):
        pe.seed_autotune_runs(11)
        good = adv.autotune_runs_answer()
        out = shared_run(
            "await H.advance(45000); return {lines: lineTexts(), rows: rowTexts().length};",
            route(RUNS, good, method="GET"),
        )
        assert len(out.result["lines"]) == 1, "a poll duplicated the line"
        assert out.result["rows"] == 11
