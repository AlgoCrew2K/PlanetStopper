"""Sufficiency-review tightening — BL-8 (audit #118 T3), pre-agreed direct
pins per team-lead priority (1): the RENDERED text contract for both streak
variants (with genuine escHtml usage) + the streak computed off the RAW
(pre-normalization) baseline_decision, not `_normalize_autotune_row`'s
short-token remap.

Both concerns were flagged by bl5impl's own GREEN commit (9b1f349b) as
subtleties worth a dedicated regression guard:

  "the streak is computed from the RAW (pre-normalization) rows so its
  'Adopted AI' comparison always matches autotuner.py's real persisted
  literal, independent of _normalize_autotune_row's short-token remap."
  (app.py's api_autotune_runs docstring)

TestRawBaselineDecisionIndependence proves this is a REAL invariant, not
just a comment — even though `_BASELINE_DECISION_TOKENS` today happens not
to remap "Adopted AI" (confirmed: only "Reverted to Fallback"/"Applied"/
"Rejected" are mapped), a future change to that table must not silently
break the streak boundary detection. Same "defensive-code correctness,
currently unreachable, second-layer defense" class as this codebase's own
`_resolve_admission_mdd_baseline` precedent (advisors/strategy_builder_
engine.py) — worth testing on its own merits, not contingent on today's
map contents.

TestStreakRenderTextContract source-scans static/ai_advisor.js (no JS test
runner in this repo — consistent with every other JS-behavior test here,
e.g. tests/app/test_bl11_ai_advisor_oos_alpha_label_disclosure.py) for both
rendered text variants, confirms escHtml wraps the dynamic (streak_weeks)
variant specifically, and confirms the <2/streak case renders nothing.
"""

from __future__ import annotations

import copy
import json
import pathlib
import re

import pytest

import app as app_module
import database
from tests._ai_advisor_autotune_panel_harness import drive
from tests._ai_advisor_js_harness import render_autotune_list
from tests._ux_truth_support import (
    MARKUP_NAME,
    captured_symphonies,
    load_captured_bot_state,
    parse_html,
    phase1_facets,
    pick_symphony,
    register_bundle,
    save_run,
    state_with_markup_name,
)

_AI_ADVISOR_JS = pathlib.Path(__file__).parent.parent.parent / "static" / "ai_advisor.js"

_SYMPHONY_ID = "sym-bl8-tighten-001"


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


class TestRawBaselineDecisionIndependence:
    def test_streak_boundary_survives_a_future_adopted_ai_remap(self, client, monkeypatch):
        """Seeds 3 real autotune_runs rows for one symphony: oldest is a
        genuine "Adopted AI" streak-breaker, the 2 more recent rows are
        non-adopted. Monkeypatches _BASELINE_DECISION_TOKENS to ALSO remap
        "Adopted AI" -> a short token (simulating a future extension of that
        table, which does not exist today) and asserts the streak still
        reads 2 (not 3) — proving the streak computation reads the RAW
        persisted baseline_decision literal via database.get_all_autotune_
        runs, never the row dict AFTER _normalize_autotune_row's remap has
        already been applied to it.
        """
        monkeypatch.setattr(
            app_module,
            "_BASELINE_DECISION_TOKENS",
            {**app_module._BASELINE_DECISION_TOKENS, "Adopted AI": "adopted"},
        )

        database.save_autotune_run(
            "2026-06-01T04:00:00",
            _SYMPHONY_ID,
            None,
            None,
            "Adopted AI",
            None,
            None,
        )
        database.save_autotune_run(
            "2026-06-08T04:00:00",
            _SYMPHONY_ID,
            None,
            None,
            "Reverted to Fallback",
            None,
            None,
        )
        database.save_autotune_run(
            "2026-06-15T04:00:00",
            _SYMPHONY_ID,
            None,
            None,
            "Reset to Global Default",
            None,
            None,
        )

        resp = client.get("/api/autotune-runs")
        assert resp.status_code == 200
        rows = resp.get_json()
        this_symphony_rows = [r for r in rows if r["symphony_id"] == _SYMPHONY_ID]
        assert len(this_symphony_rows) == 3, (
            f"fixture integrity: expected 3 rows for {_SYMPHONY_ID}, got {len(this_symphony_rows)}"
        )

        # Sanity: the DISPLAYED baseline_decision on the oldest row IS
        # normalized under the monkeypatched map (proves the monkeypatch
        # actually took effect on the render side).
        oldest = min(this_symphony_rows, key=lambda r: r["run_timestamp"])
        assert oldest["baseline_decision"] == "adopted", (
            "fixture integrity: the monkeypatched remap must apply to the "
            "row's own displayed baseline_decision field"
        )

        # The streak, computed from the SAME rows, must still correctly
        # recognize the Adopted AI boundary — 2, not 3 (which would mean the
        # remapped "adopted" token was compared against the literal
        # "Adopted AI" and never matched, silently inflating the streak).
        streak = this_symphony_rows[0]["never_adopted_streak"]
        assert streak["status"] == "streak"
        assert streak["streak_weeks"] == 2, (
            f"never_adopted_streak.streak_weeks={streak['streak_weeks']} — the "
            f"streak computation must read the RAW 'Adopted AI' literal (via "
            f"the un-normalized rows database.get_all_autotune_runs returns), "
            f"never the row dict AFTER _normalize_autotune_row's remap has "
            f"already replaced it with a short token that no longer matches "
            f"analytics._NEVER_ADOPTED_ADOPTED_TOKEN"
        )


class TestStreakRenderTextContract:
    def test_multi_run_streak_variant_wrapped_in_eschtml(self):
        """status=='streak' && streak_weeks>=2 branch: the cell must carry
        'runs without adopting a tuned config' (the column header says it once;
        the row keeps it as hidden text, shown on a phone), and the dynamic
        (streak_weeks-containing) string must be passed through escHtml —
        never raw string concatenation directly into the DOM.
        """
        src = _AI_ADVISOR_JS.read_text(encoding="utf-8")
        idx = src.find("runs without adopting a tuned config")
        assert idx != -1, (
            "static/ai_advisor.js must render 'N runs without "
            "adopting a tuned config' for a genuine multi-run streak"
        )
        # The literal lives inside a string argument to escHtml(...) — walk
        # backward from the match to the nearest escHtml( call opening.
        window_before = src[max(0, idx - 200) : idx]
        assert "escHtml(" in window_before, (
            "the multi-run streak text must be passed through escHtml(...) — "
            f"found the text but no escHtml( call within 200 chars before it:"
            f"\n{window_before}"
        )

    def test_insufficient_history_variant_present(self):
        """status=='insufficient_history' branch: distinct informative text,
        AC-11's degrade — must never be confused with the multi-run variant."""
        src = _AI_ADVISOR_JS.read_text(encoding="utf-8")
        assert "adoption streak: insufficient history" in src, (
            "static/ai_advisor.js must render the AC-11 insufficient-history "
            "degrade text distinctly from the multi-run streak variant"
        )

    def test_both_variants_gated_on_never_adopted_streak_field(self):
        """Both variants must read from r.never_adopted_streak (the field
        /api/autotune-runs stamps per row) — not a re-derived or differently
        named field."""
        src = _AI_ADVISOR_JS.read_text(encoding="utf-8")
        assert "r.never_adopted_streak" in src or ".never_adopted_streak" in src, (
            "the streak render must read the never_adopted_streak field the "
            "route actually stamps onto each row"
        )

    def test_sub_two_week_streak_status_renders_nothing(self):
        """streak_weeks < 2 with status=='streak': the code must NOT fall
        through to an unconditional render — structural proof: streakHtml's
        only non-empty assignments are inside the two explicitly-guarded
        branches (>= 2 multi-run, or insufficient_history); there is no bare
        `else` branch between them that would render for the <2 case."""
        src = _AI_ADVISOR_JS.read_text(encoding="utf-8")
        decl_idx = src.find("var streakHtml = '';")
        assert decl_idx != -1, (
            "expected streakHtml to default to an empty string before the "
            "guarded branches assign it"
        )
        # The two branches are `if (...) { streakHtml = ...; } else if (...) { streakHtml = ...; }`
        # -- confirm there is NO bare `} else {` between the declaration and
        # the next `streakHtml;` usage (which would mean a fallback render
        # exists for every other case, including streak_weeks < 2).
        window_after = src[decl_idx : decl_idx + 900]
        assert "} else {" not in window_after, (
            "found a bare `else` branch after streakHtml's declaration — this "
            "would render something for the streak_weeks < 2 case, which "
            "must render NOTHING (a single non-adopted run isn't a pattern "
            f"worth flagging); window:\n{window_after}"
        )


# ---------------------------------------------------------------------------
# The run card names its objective, its Frozen-eval state and its symphony truly.
#
# Route half: GET /api/autotune-runs reports, for every row, the objective its spec bundle
# set (or null when the bundle cannot be read) and the symphony's display name from the live
# state (or null). Card half: the real static/ai_advisor.js renders those rows under node.
# ---------------------------------------------------------------------------

_LEGACY_NULLS = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "autotuner"
    / "dsr_surfacing"
    / "autotune_run_legacy_nulls.json"
)
_ROUTE_URL = "/api/autotune-runs"
# The most cards the list draws (AUTOTUNE_CARD_LIMIT in static/ai_advisor.js).
_CARD_LIMIT = 12
_UNREADABLE_BUNDLE_HASH = "f" * 64
_DECISION_LABEL = {"apply": "Apply", "reject": "Reject", "fallback": "Fallback"}


def _rows(client) -> list[dict]:
    resp = client.get(_ROUTE_URL)
    assert resp.status_code == 200
    rows = resp.get_json()
    assert isinstance(rows, list), "the route stays a bare JSON array"
    return rows


def _row(rows: list[dict], run_id: int) -> dict:
    return next(r for r in rows if r["id"] == run_id)


def _cards(list_html: str) -> list:
    return parse_html(list_html).find_all(testid="autotune-run-row")


def _card_for(rows: list[dict], run_id: int):
    """Render the real list once under node and return the card for ``run_id``."""
    row = _row(rows, run_id)
    cards = _cards(render_autotune_list({_ROUTE_URL: rows})[0])
    # A symphony's card shows its newest row; its other rows are older runs, not cards.
    carded: list[dict] = []
    seen: set[str] = set()
    for candidate in rows:
        if str(candidate["symphony_id"]) not in seen:
            seen.add(str(candidate["symphony_id"]))
            carded.append(candidate)
    assert len(cards) == len(carded) <= _CARD_LIMIT, "one card per symphony"
    assert row in carded, "the run asked for is its symphony's newest, so it has a card"
    return cards[carded.index(row)]


def _ancestors_within(node, ceiling):
    found = []
    current = node.parent
    while current is not None and current is not ceiling:
        found.append(current)
        current = current.parent
    return found


def _save_legacy_null_row(symphony_id: str = "legacy_symphony") -> int:
    fixture = json.loads(_LEGACY_NULLS.read_text(encoding="utf-8"))["row"]
    return database.save_autotune_run(
        fixture["run_timestamp"],
        symphony_id,
        fixture["oos_alpha"],
        fixture["train_alpha"],
        fixture["baseline_decision"],
        fixture["fallback_oos_alpha"],
        fixture["default_oos_alpha"],
        selection_tstat=fixture["selection_tstat"],
        naive_sharpe=fixture["naive_sharpe"],
        validation_sharpe=fixture["validation_sharpe"],
        frozen_eval_sharpe=fixture["frozen_eval_sharpe"],
    )


class TestAutotuneRunsRouteObjectiveAndName:
    def test_crra_bundle_row_reports_crra_eu(self, client):
        run_id = save_run("crra-sym", register_bundle(phase1_facets()))
        assert _row(_rows(client), run_id).get("objective") == "crra_eu"

    def test_sortino_bundle_row_reports_sortino_loss_aversion(self, client):
        bundle = register_bundle({**phase1_facets(), "objective_kind": "sortino_loss_aversion"})
        run_id = save_run("sortino-sym", bundle)
        assert _row(_rows(client), run_id).get("objective") == "sortino_loss_aversion"

    def test_rows_with_no_readable_bundle_report_null_under_the_key(self, client):
        legacy = _save_legacy_null_row()
        unstored = save_run("unstored-sym", _UNREADABLE_BUNDLE_HASH, "2026-10-27T04:00:00")
        rows = _rows(client)
        for run_id in (legacy, unstored):
            row = _row(rows, run_id)
            assert "objective" in row, "the key is always present, null when unreadable"
            assert row["objective"] is None

    def test_every_row_carries_objective_and_symphony_name_keys(self, client):
        save_run("a", register_bundle(phase1_facets()), "2026-10-27T04:00:00")
        save_run("b", None, "2026-10-27T05:00:00")
        for row in _rows(client):
            assert {"objective", "symphony_name"} <= set(row), sorted(row)

    def test_existing_row_fields_are_untouched(self, client):
        run_id = save_run("keep-sym", register_bundle(phase1_facets()), frozen_eval_sharpe=0.7)
        row = _row(_rows(client), run_id)
        for key in (
            "run_timestamp",
            "symphony_id",
            "baseline_decision",
            "naive_sharpe",
            "selection_tstat",
            "frozen_eval_verdict",
            "never_adopted_streak",
        ):
            assert key in row, f"{key} must stay on the row"
        assert row["frozen_eval_verdict"] == "passed"

    def test_symphony_name_is_the_live_display_name_for_a_normalized_key(self, client):
        database.save_state(load_captured_bot_state())
        picked = [
            pick_symphony(lambda n: n != n.strip()),  # trailing space in the stored name
            pick_symphony(lambda n: "'" in n),  # apostrophe
            pick_symphony(lambda n: "(" in n),  # parentheses
        ]
        ids = {
            name: save_run(database.normalize_name(name), None, f"2026-10-27T0{i}:00:00")
            for i, (_hash, name) in enumerate(picked)
        }
        rows = _rows(client)
        for _hash, name in picked:
            assert _row(rows, ids[name]).get("symphony_name") == name, (
                "symphony_name is the entry's display name exactly as stored"
            )

    def test_symphony_name_is_null_when_the_symphony_is_not_in_the_live_state(self, client):
        database.save_state(load_captured_bot_state())
        run_id = save_run("a symphony no longer held", None)
        row = _row(_rows(client), run_id)
        assert "symphony_name" in row and row["symphony_name"] is None
        assert row["symphony_id"] == "a symphony no longer held"

    def test_symphony_name_is_null_when_no_state_is_stored(self, client):
        run_id = save_run("any key", None)
        row = _row(_rows(client), run_id)
        assert "symphony_name" in row and row["symphony_name"] is None

    def test_live_state_is_read_once_per_request_not_once_per_row(self, client, monkeypatch):
        database.save_state(load_captured_bot_state())
        held = captured_symphonies()[:4]
        for i, (_hash, name) in enumerate(held):
            save_run(database.normalize_name(name), None, f"2026-10-27T0{i}:00:00")
        real_load_state = database.load_state
        calls: list[int] = []

        def counting_load_state():
            calls.append(1)
            return real_load_state()

        monkeypatch.setattr(database, "load_state", counting_load_state)
        rows = _rows(client)

        assert len(rows) == len(held)
        assert len(calls) == 1, "one state read serves every row"
        assert sorted(r.get("symphony_name") or "" for r in rows) == sorted(n for _h, n in held)

    def test_a_metadata_entry_in_the_state_is_never_a_symphony_name(self, client):
        """bot_state also holds portfolio metadata; only entries shaped like a symphony count."""
        state = load_captured_bot_state()
        state["last_market_close_snapshot"] = {"date": "2026-10-26", "value": 1.0}
        state["post_mortem_run"] = {"label": "not a symphony"}
        database.save_state(state)
        run_id = save_run("post_mortem_run", None)
        row = _row(_rows(client), run_id)
        assert "symphony_name" in row and row["symphony_name"] is None


class TestAutotuneRunCardRender:
    def test_crra_card_names_its_score_with_the_haircut_tooltip(self, client):
        run_id = save_run("crra-sym", register_bundle(phase1_facets()), naive_sharpe=1.2345678)
        card = _card_for(_rows(client), run_id)
        assert re.search(r"Best CRRA-EU score\s*1\.2346", card.text), card.text
        tips = [t.lower() for t in card.titles_in_subtree()]
        assert any("best trial" in t and "haircut" in t for t in tips), tips

    def test_sortino_card_names_its_score(self, client):
        bundle = register_bundle({**phase1_facets(), "objective_kind": "sortino_loss_aversion"})
        run_id = save_run("sortino-sym", bundle, naive_sharpe=0.8765432)
        card = _card_for(_rows(client), run_id)
        assert re.search(r"Best Sortino score\s*0\.8765", card.text), card.text
        assert "CRRA-EU" not in card.text

    @pytest.mark.parametrize("which", ["no_bundle", "unstored_bundle"])
    def test_unreadable_bundle_falls_back_to_a_generic_label_naming_both_objectives(
        self, client, which
    ):
        bundle = None if which == "no_bundle" else _UNREADABLE_BUNDLE_HASH
        run_id = save_run("fallback-sym", bundle, naive_sharpe=1.2345678)
        card = _card_for(_rows(client), run_id)
        assert re.search(r"Best objective score\s*1\.2346", card.text), card.text
        assert "CRRA-EU" not in card.text and "Sortino" not in card.text
        tips = " ".join(card.titles_in_subtree())
        assert "CRRA-EU" in tips and "Sortino" in tips, "the fallback tooltip names both objectives"

    @pytest.mark.parametrize("objective_kind", ["crra_eu", "sortino_loss_aversion", None])
    def test_no_bare_sortino_or_sharpe_label_remains_on_the_card(self, client, objective_kind):
        bundle = (
            None
            if objective_kind is None
            else register_bundle({**phase1_facets(), "objective_kind": objective_kind})
        )
        run_id = save_run("labels-sym", bundle)
        text = _card_for(_rows(client), run_id).text
        remainder = re.sub(r"Best (CRRA-EU|Sortino|objective) score", "", text)
        assert "Sortino" not in remainder and "Sharpe" not in remainder, text

    def test_null_naive_score_reads_na_under_the_generic_label(self, client):
        run_id = _save_legacy_null_row()
        card = _card_for(_rows(client), run_id)
        assert re.search(r"Best objective score\s*N/A", card.text), card.text

    def test_zero_naive_score_is_a_number_never_na(self, client):
        run_id = save_run("zero-sym", None, naive_sharpe=0.0)
        text = _card_for(_rows(client), run_id).text
        assert re.search(r"Best objective score\s*0\.0000", text), text
        assert not re.search(r"Best objective score\s*N/A", text)

    def test_passed_verdict_renders_a_pill_in_the_positive_token(self, client):
        run_id = save_run("pass-sym", register_bundle(phase1_facets()), frozen_eval_sharpe=0.9)
        card = _card_for(_rows(client), run_id)
        assert "Best CRRA-EU score" in card.text, "the pill sits on a card that names its score"
        pill = card.find(cls="frozen-eval-pill")
        assert pill is not None and pill.text == "passed"
        # The verdict is a status dot and one word: the modifier class carries the positive token.
        assert "frozen-eval-pill--pos" in pill.attrs.get("class", "").split()
        assert "style" not in pill.attrs
        assert "not evaluated" not in card.text

    def test_failed_verdict_renders_a_pill_in_the_negative_token(self, client):
        run_id = save_run("fail-sym", register_bundle(phase1_facets()), frozen_eval_sharpe=-0.4)
        card = _card_for(_rows(client), run_id)
        assert "Best CRRA-EU score" in card.text, "the pill sits on a card that names its score"
        pill = card.find(cls="frozen-eval-pill")
        assert pill is not None and pill.text == "failed"
        assert "frozen-eval-pill--neg" in pill.attrs.get("class", "").split()
        assert "style" not in pill.attrs

    def test_null_verdict_reads_not_evaluated_with_no_pill_element(self, client):
        run_id = save_run("none-sym", None, frozen_eval_sharpe=None)
        rows = _rows(client)
        assert _row(rows, run_id)["frozen_eval_verdict"] is None, "self-guard: route sends null"
        card = _card_for(rows, run_id)
        assert "Frozen-eval not evaluated" in card.text
        assert card.find(cls="frozen-eval-pill") is None
        notice = next(n for n in card.iter() if n.text == "Frozen-eval not evaluated")
        # The line is the dim-ink frozen-eval line and it stays on screen: it is the reason this run
        # has no verdict, so it is not visually hidden (not an adv-vh line).
        assert "autotune-run-frozen" in notice.attrs.get("class", "").split()
        assert "adv-vh" not in notice.attrs.get("class", "").split()

    def test_missing_verdict_key_reads_the_same_as_a_null_verdict(self, client):
        run_id = save_run("missing-sym", None, frozen_eval_sharpe=None)
        rows = copy.deepcopy(_rows(client))
        del _row(rows, run_id)["frozen_eval_verdict"]
        card = _card_for(rows, run_id)
        assert "Frozen-eval not evaluated" in card.text
        assert card.find(cls="frozen-eval-pill") is None

    def test_repeated_poll_renders_hold_one_score_and_one_frozen_eval_line_per_card(self, client):
        save_run(
            "a", register_bundle(phase1_facets()), "2026-10-27T04:00:00", frozen_eval_sharpe=0.5
        )
        save_run("b", None, "2026-10-27T05:00:00", frozen_eval_sharpe=None)
        rows = _rows(client)
        renders = render_autotune_list({_ROUTE_URL: rows}, polls=2)
        assert len(renders) == 3
        for markup in renders:
            cards = _cards(markup)
            assert len(cards) == len(rows)
            for card in cards:
                assert len(re.findall(r"Best (?:CRRA-EU|Sortino|objective) score", card.text)) == 1
                assert card.text.count("Frozen-eval") == 1
        assert renders[0] == renders[1] == renders[2], "a re-render replaces, never appends"

    def test_failed_fetch_shows_the_load_failure_and_no_card(self):
        (loaded,) = drive([{"op": "load"}], {_ROUTE_URL: {"__reject__": "network down"}})
        assert loaded["notice"] == "Could not refresh runs"
        assert parse_html(loaded["list"]).find_all(testid="autotune-run-row") == []

    def test_decision_pill_t_stat_and_streak_line_render_from_the_row(self, client):
        stamps = ["2026-10-12T04:00:00", "2026-10-19T04:00:00", "2026-10-26T04:00:00"]
        ids = [
            save_run("streak-sym", None, stamps[0], baseline_decision="Reverted to Fallback"),
            save_run("streak-sym", None, stamps[1], baseline_decision="Reset to Global Default"),
            save_run(
                "streak-sym",
                None,
                stamps[2],
                baseline_decision="Rejected",
                selection_tstat=2.3456789,
            ),
        ]
        rows = _rows(client)
        newest = _row(rows, ids[2])
        assert newest["never_adopted_streak"]["status"] == "streak", "self-guard"
        card = _card_for(rows, ids[2])
        pill = card.find(cls="decision-pill")
        assert pill is not None and pill.text == _DECISION_LABEL[newest["baseline_decision"]]
        assert re.search(r"Sel t-stat\s*2\.3457", card.text), card.text
        streak = card.find(cls="autotune-run-streak")
        assert streak is not None
        assert streak.text == (
            f"{newest['never_adopted_streak']['streak_weeks']} runs without adopting a tuned config"
        )

    def test_name_shows_the_display_name_with_the_key_only_in_the_title(self, client):
        database.save_state(load_captured_bot_state())
        _hash, name = captured_symphonies()[0]
        key = database.normalize_name(name)
        run_id = save_run(key, None)
        label = _card_for(_rows(client), run_id).find(cls="autotune-run-name")
        assert label is not None
        assert label.text == name.strip()
        assert key in label.title and key != label.text

    def test_name_falls_back_to_the_stored_key_when_the_symphony_is_not_held(self, client):
        database.save_state(load_captured_bot_state())
        run_id = save_run("a symphony no longer held", None)
        label = _card_for(_rows(client), run_id).find(cls="autotune-run-name")
        assert label is not None and label.text == "a symphony no longer held"


class TestAutotuneCardSecurity:
    def test_a_display_name_carrying_markup_renders_as_literal_text(self, client):
        symphony_hash, _name = captured_symphonies()[0]
        database.save_state(state_with_markup_name(symphony_hash))
        run_id = save_run(database.normalize_name(MARKUP_NAME), None)
        rows = _rows(client)
        assert _row(rows, run_id).get("symphony_name") == MARKUP_NAME, "self-guard: name resolved"
        card = _card_for(rows, run_id)
        label = card.find(cls="autotune-run-name")
        assert label is not None and label.text == MARKUP_NAME
        assert [n for n in card.iter() if n.tag == "b"] == [], "no markup element was created"
