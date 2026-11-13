"""RED: /ai-advisor never draws an error as an empty list and shows a Frontrunner build's end.

``static/ai_advisor.js`` (with the page's other scripts) runs unmodified over the page the real app
renders. Bodies are the real routes': the runs list over rows from ``database.save_autotune_run``,
the build's start and its summary from the run route and ``GET /api/frontrunner-build/last-run``
(``tests/ui/_advisor_page_answers``), the 401/503 from the real auth hook.

CONTRACT PINNED
* ``loadRecentRuns`` draws only an array. Any other answer (non-2xx, error object, HTML) keeps the
  cards on screen and shows ``Could not refresh runs`` in the page; a later good poll removes it. A 401
  navigates to ``/login``.
* The runs table states what every run shares in one sentence above the rows
  (``data-testid="autotune-sentence"``, inside ``#autotune-runs-list``): only the run time and the
  outcome are ever lifted, a column that reads N/A in every row is absent with its header, and the
  older-runs summary counts the older runs and says how far back the list reaches. The table is the
  shared columns primitive. A single row and an empty list print no sentence, and the 15 s refresh
  moves the run time and the outcome between the sentence and the rows as the runs change.
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


SENTENCE = '[data-testid="autotune-sentence"]'
HEADERS = "#autotune-runs-list [role='columnheader']"
OLDER_HEADERS = "#autotune-older-list [role='columnheader']"
ROWS_STATE = f"""
  const norm = (t) => String(t || '').replace(/\\s+/g, ' ').trim();
  const rowTexts = () => H.$$('#autotune-runs-list {CARDS}').map((r) => norm(r.textContent));
  const olderRowTexts = () => H.$$('#autotune-older-list {CARDS}').map((r) => norm(r.textContent));
  const sentences = () => H.$$('{SENTENCE}').map((l) => norm(l.textContent));
  const heads = () => H.$$("{HEADERS}").map((h) => norm(h.textContent));
  const olderHeads = () => H.$$("{OLDER_HEADERS}").map((h) => norm(h.textContent));
  const listHtml = () => H.$('#autotune-runs-list').innerHTML;
  const panelText = () => norm(H.$('[data-testid="autotune-panel"]').textContent);
  const olderSummary = () => norm(H.$('#autotune-older-summary').textContent);
"""
# The score column's header when the seeded runs carry no objective.
SCORE_HEAD = "Best objective score"
STREAK_HEAD = "Runs without adopting a tuned config"
TIME_HEAD = "Run time (ET)"
OUTCOME_HEAD = "Outcome"
TSTAT_HEAD = "Sel t-stat"
KEPT = "every symphony kept its previous settings"
ADOPTED = "every symphony took new tuned settings"
RESET = "every symphony was reset to the global default settings"
# The producer's own outcome literal for a run that took the global default (autotuner.py).
RESET_DECISION = "Reset to Global Default"
BATCH_STAMP = "2026-11-06T19:08:00"
LATER_STAMP = "2026-11-13T19:08:00"


def et_label(stamp: str) -> str:
    """The run-time cell the page prints for a naive UTC stamp, by stated arithmetic."""
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    moment = datetime.fromisoformat(stamp).replace(tzinfo=UTC)
    return moment.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M ET")


def shared_run(scenario, *routes_):
    return run_page(
        render_page("/ai-advisor"),
        "await H.boot();\n" + STATE + ROWS_STATE + scenario,
        routes=list(routes_),
        pathname="/ai-advisor",
    )


def _seed_batch(kinds, *, stamp=BATCH_STAMP, **per_kind):
    """One run per captured symphony, every run stamped ``stamp``; ``kinds`` names each run's
    kind (``reverted``, ``adopted``, ``scored`` or ``reset``, ``hold``)."""
    keys = pe._symphony_keys(len(kinds))
    for key, kind in zip(keys, kinds, strict=True):
        if kind == "scored":
            pe.save_run_like("reverted", key, stamp, naive_sharpe=1.25)
        elif kind == "reset":
            pe.save_run_like("reverted", key, stamp, baseline_decision=RESET_DECISION)
        elif kind == "hold":
            pe.save_run_like("reverted", key, stamp, baseline_decision="Hold")
        elif kind in ("Rejected", "Applied"):
            # the words the producer stored before the route mapped them to its own tokens
            pe.save_run_like("reverted", key, stamp, baseline_decision=kind)
        else:
            pe.save_run_like(kind, key, stamp)
    return keys


def _body_names(rows) -> list[str]:
    return [r.get("symphony_name") or r["symphony_id"] for r in rows]


def _read(scenario="", body=None):
    body = body if body is not None else adv.autotune_runs_answer()
    everything = (
        "return {rows: rowTexts(), sentences: sentences(), heads: heads(), html: listHtml(),"
        " panel: panelText(), older: olderRowTexts(), olderHeads: olderHeads(),"
        " olderSummary: olderSummary()};"
    )
    out = shared_run(scenario or everything, route(RUNS, body, method="GET"))
    return out.result, body


class TestAutotuneSentence:
    """The Overview's autotune runs state what every run shares in one plain sentence (the rows the
    page script writes on each refresh, over the real route's rows)."""

    def test_a_shared_run_time_and_outcome_read_as_one_sentence_and_leave_the_rows(self):
        _seed_batch(["reverted"] * 11)
        r, body = _read()
        assert r["sentences"] == [f"Latest run {et_label(BATCH_STAMP)}: {KEPT}."]
        assert r["heads"] == ["Symphony", STREAK_HEAD], (
            f"only the symphony and the streak stay as columns: {r['heads']!r}"
        )
        assert len(r["rows"]) == 11
        names = _body_names(body["json"])
        for name, text in zip(names, r["rows"], strict=True):
            assert name in text, f"a row lost its symphony {name!r}: {text!r}"
            assert et_label(BATCH_STAMP) not in text, f"a row repeats the shared time: {text!r}"
            for word in ("Fallback", "Frozen-eval", "N/A"):
                assert word not in text, f"a row repeats {word!r}: {text!r}"

    def test_the_words_of_the_dropped_columns_and_the_old_line_are_on_screen_nowhere(self):
        _seed_batch(["reverted"] * 11)
        r, _ = _read()
        for word in ("N/A", "Best CRRA-EU score", SCORE_HEAD, TSTAT_HEAD, "Same for every run"):
            assert word not in r["panel"], f"{word!r} is still in the autotune panel"
        assert "autotune-shared-line" not in r["html"]

    def test_the_sentence_sits_above_the_rows_in_the_runs_list(self):
        _seed_batch(["reverted"] * 11)
        r, _ = _read()
        assert r["html"].index("autotune-sentence") < r["html"].index("autotune-run-row")

    def test_a_shared_adopted_outcome_names_its_frozen_evaluation(self):
        _seed_batch(["adopted"] * 11)
        r, _ = _read()
        assert r["sentences"] == [
            f"Latest run {et_label(BATCH_STAMP)}: {ADOPTED} (frozen-eval passed)."
        ]

    def test_a_shared_global_default_outcome_reads_its_own_clause(self):
        _seed_batch(["reset"] * 11)
        r, _ = _read()
        assert r["sentences"] == [f"Latest run {et_label(BATCH_STAMP)}: {RESET}."]

    def test_a_shared_outcome_the_page_has_no_clause_for_names_the_label(self):
        _seed_batch(["hold"] * 11)
        r, _ = _read()
        assert r["sentences"] == [
            f"Latest run {et_label(BATCH_STAMP)}: every run's outcome was Hold."
        ]

    @pytest.mark.parametrize(("stored", "label"), [("Rejected", "Reject"), ("Applied", "Apply")])
    def test_a_shared_outcome_the_label_map_translates_reads_the_label_not_the_route_word(
        self, stored, label
    ):
        """S-5.9: the route sends the token ("reject", "apply"); the sentence prints the label the
        page maps it to. Rejects printing the raw token, which a word the map leaves alone ("Hold")
        cannot tell from the label."""
        _seed_batch([stored] * 11)
        r, _ = _read()
        assert r["sentences"] == [
            f"Latest run {et_label(BATCH_STAMP)}: every run's outcome was {label}."
        ]

    def test_a_shared_run_time_with_different_outcomes_keeps_the_outcome_in_every_row(self):
        _seed_batch(["reverted"] * 3 + ["adopted"] + ["reverted"] * 7)
        r, _ = _read()
        assert r["sentences"] == [f"Latest run {et_label(BATCH_STAMP)} for every symphony."]
        assert r["heads"] == ["Symphony", OUTCOME_HEAD, STREAK_HEAD]
        for text in r["rows"]:
            assert "Fallback" in text or "Adopted AI" in text, f"a row lost its outcome: {text!r}"
            assert et_label(BATCH_STAMP) not in text

    def test_a_shared_outcome_with_different_run_times_keeps_the_time_in_every_row(self):
        pe.seed_autotune_runs(11)
        r, body = _read()
        assert r["sentences"] == [f"{KEPT[0].upper()}{KEPT[1:]} in its latest run."]
        assert r["heads"] == ["Symphony", TIME_HEAD, STREAK_HEAD]
        stamps = [et_label(row["run_timestamp"]) for row in body["json"]]
        assert len(set(stamps)) == 11, "premise: the seeded run times differ"
        for stamp, text in zip(stamps, r["rows"], strict=True):
            assert stamp in text, f"a row lost its run time {stamp!r}: {text!r}"

    def test_run_times_and_outcomes_that_all_differ_print_no_sentence(self):
        pe.seed_autotune_runs(11, adopted_at=3)
        r, _ = _read()
        assert r["sentences"] == []
        assert r["heads"] == ["Symphony", TIME_HEAD, OUTCOME_HEAD, STREAK_HEAD]
        assert len(r["rows"]) == 11

    def test_exactly_one_row_prints_no_sentence_and_shows_its_time_outcome_and_streak(self):
        pe.seed_autotune_runs(1)
        r, body = _read()
        assert r["sentences"] == []
        assert r["heads"] == ["Symphony", TIME_HEAD, OUTCOME_HEAD, STREAK_HEAD]
        (text,) = r["rows"]
        assert et_label(body["json"][0]["run_timestamp"]) in text
        assert "Fallback" in text and "Frozen-eval not evaluated" in text
        assert "adoption streak: insufficient history" in text
        assert "N/A" not in text, "a column that reads N/A in every row has no cell"

    def test_a_score_in_one_row_keeps_the_score_column_in_every_row_and_drops_the_t_stat(self):
        _seed_batch(["scored"] + ["reverted"] * 10)
        r, _ = _read()
        assert r["heads"] == ["Symphony", STREAK_HEAD, SCORE_HEAD], (
            f"the score column stays with its header, the t-stat column follows its own rule: "
            f"{r['heads']!r}"
        )
        assert sum("N/A" in t for t in r["rows"]) == 10, "a row without a score reads N/A"
        assert sum("1.25" in t for t in r["rows"]) == 1, "the row with a score shows it"
        assert TSTAT_HEAD not in r["panel"]

    def test_the_sentence_and_the_rows_carry_every_value_a_row_showed_but_the_na_cells(self):
        _seed_batch(["reverted"] * 11)
        r, body = _read()
        shown = " ".join([*r["sentences"], *r["rows"]])
        for row in body["json"]:
            assert (row.get("symphony_name") or row["symphony_id"]) in shown
            assert et_label(row["run_timestamp"]) in shown
        assert "kept their previous settings" in shown or "kept its previous settings" in shown

    @pytest.mark.parametrize(
        ("kind", "verdict"),
        [("reverted", "passed"), ("reverted", "failed"), ("hold", "failed")],
        ids=["fallback_passed", "fallback_failed", "unmapped_outcome_failed"],
    )
    def test_a_shared_outcome_with_a_frozen_evaluation_keeps_that_fact_on_screen(
        self, kind, verdict
    ):
        """The outcome cell holds the word and the frozen-eval verdict; lifting the cell must not
        lose the verdict a run recorded (the route's own body, one named field edit per row)."""
        _seed_batch([kind] * 11)
        body = adv.autotune_runs_answer()
        for row in body["json"]:
            row["frozen_eval_verdict"] = verdict
        r, _ = _read(body=body)
        assert len(r["sentences"]) == 1, "premise: the outcome is shared, so one sentence"
        shown = " ".join([*r["sentences"], *r["rows"]]).lower()
        assert "frozen-eval" in shown and verdict in shown, (
            f"the frozen-eval verdict {verdict!r} is on screen nowhere: {r['sentences']!r}"
        )

    def test_no_runs_keep_the_empty_state_and_print_no_sentence(self):
        out = shared_run(
            "return {text: H.pageText(), sentences: sentences(), rows: cards()};",
            route(RUNS, {"json": []}, method="GET"),
        )
        assert out.result["sentences"] == [] and out.result["rows"] == 0
        assert "No tuning runs recorded yet" in out.result["text"]
        assert "Same for every run" not in out.result["text"]

    def test_a_refresh_that_brings_a_differing_outcome_changes_the_sentence_and_returns_the_outcome(
        self,
    ):
        import copy

        _seed_batch(["reverted"] * 11)
        first = adv.autotune_runs_answer()
        # The refresh's second answer: the route's own body with one run's outcome changed to the
        # adopted outcome (its frozen evaluation passed), as the next weekly batch could bring.
        second = copy.deepcopy(first)
        second["json"][0]["baseline_decision"] = "Adopted AI"
        second["json"][0]["frozen_eval_verdict"] = "passed"
        out = shared_run(
            """
            const before = {sentences: sentences(), heads: heads()};
            await H.advance(15000);
            return {before, after: {sentences: sentences(), rows: rowTexts(), heads: heads()}};
            """,
            route(RUNS, first, second, method="GET"),
        )
        before, after = out.result["before"], out.result["after"]
        assert before["sentences"] == [f"Latest run {et_label(BATCH_STAMP)}: {KEPT}."]
        assert len(after["sentences"]) == 1
        assert OUTCOME_HEAD in after["heads"], "the outcome did not return to the header"
        assert KEPT not in after["sentences"][0]
        assert len(after["rows"]) == 11
        for text in after["rows"]:
            assert "Frozen-eval" in text, f"the outcome did not return to every row: {text!r}"

    def test_a_refresh_that_fails_keeps_the_last_sentence_and_table(self):
        _seed_batch(["reverted"] * 11)
        good = adv.autotune_runs_answer()
        out = shared_run(
            """
            const before = {sentences: sentences(), rows: rowTexts()};
            await H.advance(15000);
            return {before, after: {sentences: sentences(), rows: rowTexts()}, shown: H.surfaced()};
            """,
            route(RUNS, good, {"reject": True}, method="GET"),
        )
        assert out.result["before"] == out.result["after"], "a failed refresh changed the table"
        assert len(out.result["after"]["sentences"]) == 1
        assert "Could not refresh runs" in out.result["shown"]

    def test_a_refresh_answering_500_keeps_the_last_sentence_and_table_and_the_notice(self):
        _seed_batch(["reverted"] * 11)
        good = adv.autotune_runs_answer()
        out = shared_run(
            """
            const before = {sentences: sentences(), rows: rowTexts(), heads: heads()};
            await H.advance(15000);
            return {before, after: {sentences: sentences(), rows: rowTexts(), heads: heads()},
                    shown: H.surfaced()};
            """,
            route(RUNS, good, unavailable_answer("/api/autotune-runs"), method="GET"),
        )
        assert out.result["before"] == out.result["after"]
        assert "Could not refresh runs" in out.result["shown"]

    def test_polls_that_bring_the_same_runs_never_repeat_the_sentence(self):
        _seed_batch(["reverted"] * 11)
        good = adv.autotune_runs_answer()
        out = shared_run(
            "await H.advance(45000); return {sentences: sentences(), rows: rowTexts().length,"
            " heads: heads()};",
            route(RUNS, good, method="GET"),
        )
        assert len(out.result["sentences"]) == 1, "a poll duplicated the sentence"
        assert out.result["rows"] == 11 and out.result["heads"] == ["Symphony", STREAK_HEAD]


class TestAutotuneSentenceSecurity:
    """A stored word or name reaches the page as text: never as an element, never run."""

    def test_a_stored_outcome_word_carrying_markup_is_text_in_the_sentence_and_runs_nothing(self):
        hostile = "<img src=x onerror=window.pwned=1>"
        for key in pe._symphony_keys(11):
            pe.save_run_like("reverted", key, BATCH_STAMP, baseline_decision=hostile)
        out = shared_run(
            "return {sentences: sentences(), imgs: H.$$('"
            + SENTENCE
            + " img').length, all: H.$$('#autotune-runs-list img').length, pwned: !!window.pwned};",
            route(RUNS, adv.autotune_runs_answer(), method="GET"),
        )
        r = out.result
        assert r["sentences"] == [
            f"Latest run {et_label(BATCH_STAMP)}: every run's outcome was {hostile}."
        ]
        assert r["imgs"] == 0 and r["all"] == 0, "the stored word became an element"
        assert r["pwned"] is False

    def test_a_symphony_name_carrying_markup_is_text_in_its_cell(self):
        hostile = "<b onmouseover=1>hostile</b>"
        pe.save_run_like("reverted", hostile, BATCH_STAMP)
        pe.save_run_like("adopted", "second symphony", BATCH_STAMP)
        r, _ = _read()
        assert any(hostile in t for t in r["rows"]), r["rows"]
        assert "<b onmouseover" not in r["html"], "the stored name was written as markup"


class TestAutotuneColumns:
    """The table is the shared columns primitive, and each outcome word names its Glossary line."""

    def test_the_table_is_the_columns_primitive_with_its_column_count_set_and_every_row_a_subgrid(
        self,
    ):
        import re

        pe.seed_autotune_runs(11, adopted_at=3)
        r, _ = _read()
        html = r["html"]
        table = re.search(r"<div[^>]*role=\"table\"[^>]*>", html)
        assert table is not None, "the runs list holds no table"
        assert "cols-table" in table.group(0), (
            f"the table is not the columns primitive: {table.group(0)!r}"
        )
        count = re.search(r"--cols:\s*(\d+)", table.group(0))
        assert count is not None and int(count.group(1)) == len(r["heads"]) == 4, (
            f"the column count is not set from the shown columns: {table.group(0)!r}"
        )
        rows = re.findall(r"<div[^>]*role=\"row\"[^>]*>", html)
        assert len(rows) == 12, f"expected a header row and 11 rows, found {len(rows)}"
        assert all("cols-row" in row for row in rows), "a row is not a subgrid row"

    def test_a_column_that_is_dropped_leaves_the_count_at_the_shown_columns(self):
        import re

        _seed_batch(["reverted"] * 11)
        r, _ = _read()
        table = re.search(r"<div[^>]*role=\"table\"[^>]*>", r["html"]).group(0)
        assert int(re.search(r"--cols:\s*(\d+)", table).group(1)) == 2 == len(r["heads"])

    def test_the_older_runs_table_is_the_same_primitive(self):
        import re

        pe.seed_autotune_runs(11, older_for=3)
        out = shared_run(
            "return H.$('#autotune-older-list').innerHTML;",
            route(RUNS, adv.autotune_runs_answer(), method="GET"),
        )
        table = re.search(r"<div[^>]*role=\"table\"[^>]*>", out.result)
        assert table is not None and "cols-table" in table.group(0)
        assert re.search(r"--cols:\s*(\d+)", table.group(0))

    def test_the_outcome_words_name_their_glossary_lines_in_the_rows(self):
        pe.seed_autotune_runs(11, adopted_at=3)
        r, _ = _read()
        assert 'data-glossary="O10"' in r["html"], "the Fallback word names no Glossary line"
        assert 'data-glossary="O11"' in r["html"], "the Frozen-eval word names no Glossary line"

    def test_the_streak_header_reads_the_glossary_term_in_both_tables(self):
        pe.seed_autotune_runs(11, adopted_at=3, older_for=1)
        r, _ = _read()
        assert STREAK_HEAD in r["heads"]
        assert STREAK_HEAD in r["olderHeads"]


class TestOlderAutotuneRuns:
    def _seed_history(self, per_symphony):
        keys = pe._symphony_keys(len(per_symphony))
        from datetime import datetime, timedelta

        batch = datetime.fromisoformat(BATCH_STAMP)
        for key, runs in zip(keys, per_symphony, strict=True):
            for week in range(runs):
                stamp = (batch - timedelta(weeks=week)).strftime("%Y-%m-%dT%H:%M:%S")
                pe.save_run_like("reverted", key, stamp)

    def test_the_summary_counts_the_older_runs_and_says_how_far_back_the_list_reaches(self):
        self._seed_history([5] * 5 + [4] * 6)  # 49 runs, 11 rows, 38 older
        body = adv.autotune_runs_answer()
        older = len(body["json"]) - len({row["symphony_id"] for row in body["json"]})
        assert older == 38, "premise: 38 older runs"
        r, _ = _read(body=body)
        assert r["olderSummary"] == "38 older runs from the 50 most recent"
        assert len(r["older"]) == 38

    def test_one_older_run_reads_in_the_singular(self):
        self._seed_history([2] + [1] * 10)
        r, _ = _read()
        assert r["olderSummary"] == "1 older run from the 50 most recent"

    def test_the_older_table_drops_a_column_that_reads_na_in_every_row_and_never_lifts_one(self):
        self._seed_history([5] * 5 + [4] * 6)
        r, _ = _read()
        assert r["olderHeads"] == ["Symphony", TIME_HEAD, OUTCOME_HEAD, STREAK_HEAD], (
            f"the older table keeps its time and outcome columns: {r['olderHeads']!r}"
        )
        text = " ".join(r["older"])
        for word in ("N/A", SCORE_HEAD, TSTAT_HEAD):
            assert word not in text, f"{word!r} is in an older row"

    def test_the_older_table_keeps_a_score_column_a_row_fills(self):
        keys = pe._symphony_keys(2)
        # The newer run is the row; the older run, the one in the older table, holds the score.
        pe.save_run_like("reverted", keys[0], BATCH_STAMP, naive_sharpe=1.5)
        pe.save_run_like("reverted", keys[0], LATER_STAMP)
        r, _ = _read()
        assert SCORE_HEAD in r["olderHeads"] or "Best score" in r["olderHeads"]
        assert TSTAT_HEAD not in r["olderHeads"]
