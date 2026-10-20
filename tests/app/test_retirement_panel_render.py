"""RED tests -- AC-7: retirement panel extension (explanation, status,
approve/reject buttons, checklist).

feature-plans/retirement-approval-lifecycle.md AC-7: the panel gains per
card -- persisted explanation text (honest "explanation unavailable" when
absent); the live approval_status; Approve/Reject buttons (rendered ONLY
while status=="pending" -- pinned choice, .claude/tdd-handoff.md, since a
Jinja {% if %} conditional is the established, directly-testable pattern
this codebase already uses for actionable-vs-decided card states, e.g. the
frontrunner proposal cards); and, on an APPROVED card, the deterministic
checklist rendered read-only. All fields HTML-escaped (no `| safe`), design
tokens only.

Pinned testids (.claude/tdd-handoff.md "templates/ai_advisor.html -- panel"):
  retirement-rec-explanation, retirement-rec-status, retirement-approve-btn,
  retirement-reject-btn, retirement-checklist.

Builds on tests/app/test_retirement_recommendations_panel.py's (Cycle-2a)
fixture pattern -- duplicated locally per this repo's established
fixtures-are-not-cross-file-shared convention.

Expected state: RED until templates/ai_advisor.html + app.py gain the AC-7
markup/wiring.
"""

from __future__ import annotations

import ast
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

import advisors.tenure_review as tenure_review
import app as app_module
import database as db_module
from database import init_db, run_migrations
from tests.app import _tenure_review_panel_builders as pb


def _sample_raw_response(candidate_id="cand-sym", sibling_id="sib-sym", explanation=None):
    return {
        "candidate_id": candidate_id,
        "sibling_id": sibling_id,
        "correlation": 0.80,
        "ci_lower": 0.72,
        "ci_upper": 0.86,
        "n_obs": 200,
        "candidate_composite": 0.20,
        "sibling_composite": 0.75,
        "candidate_metrics": {
            "annualized_return": 0.03,
            "sharpe": 0.2,
            "sortino": 0.25,
            "max_drawdown": -0.30,
            "calmar": 0.10,
        },
        "sibling_metrics": {
            "annualized_return": 0.18,
            "sharpe": 1.4,
            "sortino": 1.8,
            "max_drawdown": -0.06,
            "calmar": 3.0,
        },
        "uncertainty_gate_passed": True,
        "structural_redundancy_gate_passed": True,
        "stressed_correlation": 0.78,
        "holdings_overlap": None,
        "basis_label": "actual-traded (bot) daily returns",
        "explanation": explanation,
    }


def _seed_recommendation(candidate_id="cand-sym", sibling_id="sib-sym", explanation=None):
    db_module.insert_advisor_observation(
        advisor_role="RETIREMENT_RECOMMENDATION",
        subject_type="symphony",
        subject_id=candidate_id,
        symphony_id=candidate_id,
        verdict="retire_candidate",
        raw_response=_sample_raw_response(candidate_id, sibling_id, explanation=explanation),
    )


def _seed_symphony_roster(*symphony_ids):
    roster = {sid: {"name": sid, "logic_holdings": {"AAPL": 1.0}} for sid in symphony_ids}
    db_module.save_state(roster)


@pytest.fixture()
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_retirement_panel_render.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    run_migrations()
    yield db_path


# ===========================================================================
# Explanation rendering
# ===========================================================================


class TestExplanationRendering:
    def test_present_explanation_text_is_rendered(self, client, isolated_db):
        _seed_symphony_roster("cand-pr-1", "sib-pr-1")
        _seed_recommendation(
            candidate_id="cand-pr-1",
            sibling_id="sib-pr-1",
            explanation="This candidate is redundant with its stronger sibling.",
        )
        resp = client.get("/ai-advisor")
        body = resp.get_data(as_text=True)
        assert 'data-testid="retirement-rec-explanation"' in body
        assert "This candidate is redundant with its stronger sibling." in body

    def test_absent_explanation_shows_honest_unavailable_text(self, client, isolated_db):
        """AC-1/AC-9: explanation=None (LLM down / no producer run yet) must
        never render blank or crash -- an honest 'explanation unavailable'
        fallback, and the card + buttons still render (the explainer never
        gates the feature)."""
        _seed_symphony_roster("cand-pr-2", "sib-pr-2")
        _seed_recommendation(candidate_id="cand-pr-2", sibling_id="sib-pr-2", explanation=None)
        resp = client.get("/ai-advisor")
        body = resp.get_data(as_text=True)
        assert 'data-testid="retirement-recommendation-card"' in body
        assert 'data-testid="retirement-rec-explanation"' in body
        assert "unavailable" in body.lower()

    def test_absent_explanation_still_renders_approve_reject_buttons(self, client, isolated_db):
        _seed_symphony_roster("cand-pr-2b", "sib-pr-2b")
        _seed_recommendation(candidate_id="cand-pr-2b", sibling_id="sib-pr-2b", explanation=None)
        resp = client.get("/ai-advisor")
        body = resp.get_data(as_text=True)
        assert 'data-testid="retirement-approve-btn"' in body, (
            "A None explanation must never gate away the Approve button."
        )
        assert 'data-testid="retirement-reject-btn"' in body


# ===========================================================================
# Status + button visibility by decision state
# ===========================================================================


class TestStatusAndButtonVisibility:
    def test_pending_card_shows_pending_status_and_both_buttons(self, client, isolated_db):
        _seed_symphony_roster("cand-pr-3", "sib-pr-3")
        _seed_recommendation(candidate_id="cand-pr-3", sibling_id="sib-pr-3")
        resp = client.get("/ai-advisor")
        body = resp.get_data(as_text=True)
        assert 'data-testid="retirement-rec-status"' in body
        assert 'data-testid="retirement-approve-btn"' in body
        assert 'data-testid="retirement-reject-btn"' in body

    def test_approved_card_hides_both_buttons(self, client, isolated_db):
        _seed_symphony_roster("cand-pr-4", "sib-pr-4")
        _seed_recommendation(candidate_id="cand-pr-4", sibling_id="sib-pr-4")
        db_module.upsert_retirement_decision("cand-pr-4", approval_status="approved")

        resp = client.get("/ai-advisor")
        body = resp.get_data(as_text=True)
        assert 'data-testid="retirement-recommendation-card"' in body
        assert 'data-testid="retirement-approve-btn"' not in body, (
            "An already-approved card must not still render the Approve button."
        )
        assert 'data-testid="retirement-reject-btn"' not in body

    def test_rejected_card_hides_both_buttons(self, client, isolated_db):
        _seed_symphony_roster("cand-pr-5", "sib-pr-5")
        _seed_recommendation(candidate_id="cand-pr-5", sibling_id="sib-pr-5")
        db_module.upsert_retirement_decision("cand-pr-5", approval_status="rejected")

        resp = client.get("/ai-advisor")
        body = resp.get_data(as_text=True)
        assert 'data-testid="retirement-approve-btn"' not in body
        assert 'data-testid="retirement-reject-btn"' not in body


# ===========================================================================
# Checklist rendering -- APPROVED only
# ===========================================================================


class TestChecklistRendering:
    def test_pending_card_has_no_checklist(self, client, isolated_db):
        _seed_symphony_roster("cand-pr-6", "sib-pr-6")
        _seed_recommendation(candidate_id="cand-pr-6", sibling_id="sib-pr-6")
        resp = client.get("/ai-advisor")
        body = resp.get_data(as_text=True)
        assert 'data-testid="retirement-recommendation-card"' in body
        assert 'data-testid="retirement-checklist"' not in body

    def test_rejected_card_has_no_checklist(self, client, isolated_db):
        _seed_symphony_roster("cand-pr-7", "sib-pr-7")
        _seed_recommendation(candidate_id="cand-pr-7", sibling_id="sib-pr-7")
        db_module.upsert_retirement_decision("cand-pr-7", approval_status="rejected")
        resp = client.get("/ai-advisor")
        body = resp.get_data(as_text=True)
        assert 'data-testid="retirement-checklist"' not in body

    def test_approved_card_renders_the_checklist(self, client, isolated_db):
        _seed_symphony_roster("cand-pr-8", "sib-pr-8")
        _seed_recommendation(candidate_id="cand-pr-8", sibling_id="sib-pr-8")
        db_module.upsert_retirement_decision("cand-pr-8", approval_status="approved")
        resp = client.get("/ai-advisor")
        body = resp.get_data(as_text=True)
        assert 'data-testid="retirement-checklist"' in body, (
            "An approved recommendation card must render the deterministic checklist."
        )

    def test_approved_card_checklist_includes_the_candidates_own_tickers(self, client, isolated_db):
        """Non-vacuity guard: the checklist must reflect the ACTUAL seeded
        holdings (AAPL, from _seed_symphony_roster), not a placeholder."""
        _seed_symphony_roster("cand-pr-9", "sib-pr-9")
        _seed_recommendation(candidate_id="cand-pr-9", sibling_id="sib-pr-9")
        db_module.upsert_retirement_decision("cand-pr-9", approval_status="approved")
        resp = client.get("/ai-advisor")
        body = resp.get_data(as_text=True)
        assert 'data-testid="retirement-checklist"' in body
        assert "AAPL" in body


# ===========================================================================
# XSS / escaping
# ===========================================================================


class TestEscaping:
    def test_malicious_explanation_is_escaped(self, client, isolated_db):
        malicious = "<script>alert('xss')</script>"
        _seed_symphony_roster("cand-pr-10", "sib-pr-10")
        _seed_recommendation(
            candidate_id="cand-pr-10", sibling_id="sib-pr-10", explanation=malicious
        )
        resp = client.get("/ai-advisor")
        body = resp.get_data(as_text=True)
        assert 'data-testid="retirement-rec-explanation"' in body
        assert "&lt;script&gt;" in body
        assert "<script>alert('xss')</script>" not in body

    def test_no_jinja_safe_filter_used_anywhere_in_the_template(self):
        """No `| safe` anywhere in templates/ai_advisor.html -- checked
        OUTSIDE Jinja comment blocks {# ... #} (the file's existing
        docstring-style comments legitimately mention the string 'safe' in
        prose, e.g. 'XSS-safe: all values escaped with | e, no | safe used.',
        which must not itself trip this check)."""
        path = __import__("pathlib").Path(__file__).parents[2] / "templates" / "ai_advisor.html"
        source = path.read_text(encoding="utf-8")
        stripped = re.sub(r"\{#.*?#\}", "", source, flags=re.DOTALL)
        assert not re.search(r"\|\s*safe\b", stripped), (
            "templates/ai_advisor.html uses the Jinja `| safe` filter somewhere "
            "outside a comment block -- every retirement field must rely on "
            "Jinja's default autoescaping only."
        )


# ===========================================================================
# Empty state -- no explainer/checklist/buttons at all
# ===========================================================================


class TestEmptyState:
    def test_empty_recommendations_renders_no_buttons_or_checklist(self, client, isolated_db):
        resp = client.get("/ai-advisor")
        body = resp.get_data(as_text=True)
        assert 'data-testid="tenure-review-empty"' in body
        assert 'data-testid="retirement-approve-btn"' not in body
        assert 'data-testid="retirement-reject-btn"' not in body
        assert 'data-testid="retirement-checklist"' not in body


# ===========================================================================
# Symphony tenure review in the panel
#
# Run rows come from the real review compute (see _tenure_review_panel_builders) and
# are stored the way the run pipeline stores them; the page only reads. Assertions
# are scoped to one entry through a parsed tree, because the page also holds six
# tabs full of unrelated words such as "keep" and "watch".
# ===========================================================================

_PANEL_ITEMS = ("performance", "composite", "redundancy", "backtest", "marginal", "long_vix")
_MINUS = chr(0x2212)  # the typographic minus sign, accepted in place of a hyphen


def _page(client):
    resp = client.get("/ai-advisor")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def _panel(client):
    return pb.panel_of(_page(client))


def _two_decimal_forms(value: float) -> list[str]:
    text = f"{value:.2f}"
    return [text, text.replace("-", _MINUS)]


def _shows(node: pb.Node, value: float) -> bool:
    haystack = node.everything()
    return any(form in haystack for form in _two_decimal_forms(value))


def _stored_full_run(**kwargs):
    pb.seed_bot_state()
    return pb.store_full_run(**kwargs)


def _lifecycle_buttons(node: pb.Node):
    return node.find_all("retirement-approve-btn"), node.find_all("retirement-reject-btn")


class TestHeaderAndEmptyStates:
    def test_with_no_run_the_panel_says_no_review_yet_and_still_offers_the_run_button(
        self, client, isolated_db
    ):
        _seed_symphony_roster("cand-legacy", "sib-legacy")
        _seed_recommendation(candidate_id="cand-legacy", sibling_id="sib-legacy")
        panel = _panel(client)
        empty = panel.find("tenure-review-empty")
        assert empty is not None, "the panel must say there is no review yet"
        assert "No review yet" in empty.text()
        assert panel.find("tenure-review-run-btn") is not None, (
            "the operator needs the button to start the first review"
        )
        assert panel.find_all("tenure-review-entry") == []
        assert panel.find("tenure-review-running-notice") is None
        assert panel.find("tenure-review-failed-notice") is None

    def test_with_no_run_the_existing_recommendation_cards_stay_below_the_notice(
        self, client, isolated_db
    ):
        _seed_symphony_roster("cand-legacy", "sib-legacy")
        _seed_recommendation(candidate_id="cand-legacy", sibling_id="sib-legacy")
        panel = _panel(client)
        assert panel.find("tenure-review-empty") is not None
        assert len(panel.find_all("retirement-recommendation-card")) == 1
        assert len(panel.find_all("retirement-approve-btn")) == 1

    def test_a_run_with_zero_live_symphonies_says_no_live_symphonies(self, client, isolated_db):
        pb.store_run(pb.empty_record())
        panel = _panel(client)
        none_live = panel.find("tenure-review-no-symphonies")
        assert none_live is not None
        assert "No live symphonies" in none_live.text()
        assert panel.find("tenure-review-empty") is None, (
            "a completed empty run is not 'no review yet'"
        )
        assert panel.find("tenure-review-as-of") is not None
        assert panel.find_all("tenure-review-entry") == []

    def test_the_header_states_the_as_of_day_the_run_time_and_the_threshold_in_effect(
        self, client, isolated_db
    ):
        record = _stored_full_run()
        panel = _panel(client)
        assert record["as_of_trading_day"] in panel.find("tenure-review-as-of").text()
        run_time = panel.find("tenure-review-run-time").text()
        assert record["completed_at"][:10] in run_time
        assert "17:05" in run_time, "the time of day, not only the date"
        assert re.search(r"\bET\b", run_time), "the zone the time is shown in"
        assert str(record["threshold_in_effect"]) in panel.find("tenure-review-threshold").text()

    def test_a_valid_threshold_shows_no_invalid_note(self, client, isolated_db):
        _stored_full_run()
        panel = _panel(client)
        assert panel.find("tenure-review-threshold") is not None, "the header must be rendered"
        assert panel.find("tenure-review-threshold-note") is None

    def test_an_invalid_configured_threshold_is_stated_next_to_the_default_in_use(
        self, client, isolated_db
    ):
        pb.seed_bot_state()
        pb.store_run(pb.full_record(threshold_note="configured threshold invalid; 126 in use"))
        panel = _panel(client)
        note = panel.find("tenure-review-threshold-note")
        assert note is not None
        assert "configured threshold invalid; 126 in use" in note.text()


class TestOneEntryPerLiveSymphony:
    def test_every_live_symphony_has_one_entry_with_its_name_primary_and_its_hash_as_tooltip(
        self, client, isolated_db
    ):
        record = _stored_full_run()
        panel = _panel(client)
        nodes = panel.find_all("tenure-review-entry")
        assert len(nodes) == len(record["entries"])
        assert sorted(n.attrs["data-symphony-id"] for n in nodes) == sorted(
            e["symphony_id"] for e in record["entries"]
        )
        for stored in record["entries"]:
            name = pb.entry_node(panel, stored["symphony_id"]).find("tenure-review-name")
            assert name.text() == stored["display_name"]
            assert name.attrs.get("title") == stored["symphony_id"]

    def test_a_known_tenure_is_shown_as_a_whole_number_of_trading_days(self, client, isolated_db):
        record = _stored_full_run()
        panel = _panel(client)
        for stored in record["entries"]:
            if stored["tenure_trading_days"] is None:
                continue
            tenure = pb.entry_node(panel, stored["symphony_id"]).find("tenure-review-tenure")
            assert str(stored["tenure_trading_days"]) in tenure.text(), stored["display_name"]

    def test_an_unknown_tenure_is_never_shown_as_zero(self, client, isolated_db):
        record = _stored_full_run()
        panel = _panel(client)
        stored = pb.entry_of(record, pb.FULL_IDS["dateless"])
        node = pb.entry_node(panel, stored["symphony_id"])
        eligibility = node.find("tenure-review-eligibility").text()
        assert stored["eligibility_text"] in eligibility
        tenure = node.find("tenure-review-tenure")
        if tenure is not None:
            assert not re.search(r"(?<![\d.])0(?![\d.])", tenure.text()), tenure.text()

    def test_eligibility_is_stated_with_the_days_remaining_when_not_yet_eligible(
        self, client, isolated_db
    ):
        record = _stored_full_run()
        panel = _panel(client)
        young = pb.entry_of(record, pb.FULL_IDS["young"])
        text = pb.entry_node(panel, young["symphony_id"]).find("tenure-review-eligibility").text()
        assert young["eligibility_text"] in text
        assert str(young["days_remaining"]) in text
        eligible = pb.entry_of(record, pb.FULL_IDS["keep"])
        text = (
            pb.entry_node(panel, eligible["symphony_id"]).find("tenure-review-eligibility").text()
        )
        assert eligible["eligibility_text"] in text

    @pytest.mark.parametrize(
        "verdict, modifier",
        [
            ("retire_candidate", "tenure-verdict-chip--retire_candidate"),
            ("watch", "tenure-verdict-chip--watch"),
            ("keep", "tenure-verdict-chip--keep"),
            (None, "tenure-verdict-chip--none"),
        ],
    )
    def test_the_verdict_chip_shows_the_verdict_text_with_the_modifier_of_its_verdict(
        self, client, isolated_db, verdict, modifier
    ):
        record = _stored_full_run()
        panel = _panel(client)
        stored = pb.entry_with_verdict(record, verdict)
        chip = pb.entry_node(panel, stored["symphony_id"]).find("tenure-review-verdict-chip")
        assert chip is not None
        assert chip.text() == stored["verdict_text"]
        assert "tenure-verdict-chip" in chip.classes()
        assert modifier in chip.classes()
        other = {
            "tenure-verdict-chip--retire_candidate",
            "tenure-verdict-chip--watch",
            "tenure-verdict-chip--keep",
            "tenure-verdict-chip--none",
        } - {modifier}
        assert not other & set(chip.classes())

    def test_every_entry_without_a_verdict_says_why_in_the_chip_text(self, client, isolated_db):
        record = _stored_full_run()
        panel = _panel(client)
        reasons = set()
        for stored in record["entries"]:
            if stored["verdict"] is not None:
                continue
            chip = pb.entry_node(panel, stored["symphony_id"]).find("tenure-review-verdict-chip")
            assert chip.text().startswith("no verdict"), stored["display_name"]
            reasons.add(chip.text())
        assert len(reasons) >= 3, "not yet eligible, tenure unavailable and no evidence differ"

    def test_the_watch_reason_is_shown_on_the_watch_entry_only(self, client, isolated_db):
        record = _stored_full_run()
        panel = _panel(client)
        watch = pb.entry_with_verdict(record, "watch")
        shown = pb.entry_node(panel, watch["symphony_id"]).find("tenure-review-verdict-reasons")
        assert shown is not None
        for reason in watch["verdict_reasons"]:
            assert reason in shown.text()
        keep = pb.entry_with_verdict(record, "keep")
        assert (
            pb.entry_node(panel, keep["symphony_id"]).find("tenure-review-verdict-reasons") is None
        )

    def test_the_basis_line_names_the_basis_the_fallback_reason_and_where_the_series_begins(
        self, client, isolated_db
    ):
        record = _stored_full_run()
        panel = _panel(client)
        shadow = pb.entry_of(record, pb.FULL_IDS["shadow"])
        basis = pb.entry_node(panel, shadow["symphony_id"]).find("tenure-review-basis").text()
        assert shadow["basis"]["label"] in basis
        assert shadow["basis"]["fallback_reason"] in basis
        assert shadow["basis"]["series_begins"] in basis
        composer = pb.entry_of(record, pb.FULL_IDS["keep"])
        basis = pb.entry_node(panel, composer["symphony_id"]).find("tenure-review-basis").text()
        assert composer["basis"]["label"] in basis
        assert "Composer unavailable" not in basis


class TestEvidenceItems:
    def test_every_available_item_shows_its_label_window_subject_source_and_each_figure(
        self, client, isolated_db
    ):
        record = _stored_full_run()
        panel = _panel(client)
        checked = 0
        for stored in record["entries"]:
            node = pb.entry_node(panel, stored["symphony_id"])
            for name in _PANEL_ITEMS:
                item = stored["evidence"][name]
                if item["status"] != "available":
                    continue
                shown = node.find(f"tenure-review-evidence-{name}")
                where = f"{stored['display_name']} / {name}"
                assert shown is not None, where
                text = shown.everything()
                assert item["label"] in text, where
                assert item["subject"] in text, where
                assert item["source"] in text, where
                window = item["window"]
                if window:
                    assert window["first"] in text, where
                    assert window["last"] in text, where
                    assert str(window["days"]) in text, where
                for fig_name, fig in item["figures"].items():
                    if fig["value"] is not None:
                        assert _shows(shown, fig["value"]), f"{where} / {fig_name}"
                checked += 1
        assert checked >= 10, "the roster must exercise many available items"

    def test_the_redundancy_item_shows_the_pair_the_correlation_and_both_gate_results(
        self, client, isolated_db
    ):
        record = _stored_full_run()
        panel = _panel(client)
        stored = pb.entry_with_verdict(record, "retire_candidate")
        pair = stored["evidence"]["redundancy"]["pair"]
        shown = pb.entry_node(panel, stored["symphony_id"]).find(
            "tenure-review-evidence-redundancy"
        )
        assert pair["sibling_name"] in shown.text()
        assert _shows(shown, pair["correlation"]["value"])
        assert str(pair["n_obs"]) in shown.text()
        assert pair["uncertainty_gate"]["text"] in shown.text()
        assert pair["structural_gate"]["text"] in shown.text()

    def test_an_unavailable_item_states_its_reason_and_never_renders_a_null_as_zero(
        self, client, isolated_db
    ):
        record = _stored_full_run()
        panel = _panel(client)
        checked = 0
        for stored in record["entries"]:
            node = pb.entry_node(panel, stored["symphony_id"])
            for name in _PANEL_ITEMS:
                item = stored["evidence"][name]
                if item["status"] != "unavailable":
                    continue
                shown = node.find(f"tenure-review-evidence-{name}")
                where = f"{stored['display_name']} / {name}"
                assert shown is not None, where
                text = shown.text()
                assert "unavailable" in text.lower(), where
                assert item["reason"] in text, where
                bare = text
                for known in (item["label"], item["subject"], item["source"]):
                    bare = bare.replace(known, " ")
                if item["window"]:
                    bare = re.sub(r"[\d]{4}-[\d]{2}-[\d]{2}|\d+", " ", bare)
                assert not re.search(r"(?<![\d.])0(\.0+)?(?![\d.])", bare), (
                    f"{where} shows a null figure as zero: {text!r}"
                )
                checked += 1
        assert checked >= 15

    def test_counterfactual_items_carry_their_label_and_realized_items_never_do(
        self, client, isolated_db
    ):
        record = _stored_full_run()
        panel = _panel(client)
        for stored in record["entries"]:
            node = pb.entry_node(panel, stored["symphony_id"])
            assert (
                stored["evidence"]["backtest"]["label"]
                in node.find("tenure-review-evidence-backtest").text()
            )
            assert (
                stored["evidence"]["marginal"]["label"]
                in node.find("tenure-review-evidence-marginal").text()
            )
            for realized in ("performance", "composite", "redundancy"):
                text = node.find(f"tenure-review-evidence-{realized}").text()
                assert "counterfactual" not in text.lower(), (
                    f"{stored['display_name']} / {realized} is presented with a counterfactual label"
                )

    def test_the_long_vix_item_is_labelled_informational(self, client, isolated_db):
        record = _stored_full_run()
        panel = _panel(client)
        for stored in record["entries"]:
            node = pb.entry_node(panel, stored["symphony_id"])
            text = node.find("tenure-review-evidence-long_vix").text()
            assert stored["evidence"]["long_vix"]["label"] in text, stored["display_name"]
            assert "informational" in text

    def test_the_comparison_item_lists_each_excluded_symphony_by_name(self, client, isolated_db):
        pb.seed_bot_state(include_small=True)
        record = pb.small_record()
        pb.store_run(record)
        panel = _panel(client)
        stored = pb.entry_of(record, pb.SMALL_IDS["first"])
        shown = pb.entry_node(panel, stored["symphony_id"]).find("tenure-review-evidence-marginal")
        for excluded in stored["evidence"]["marginal"]["excluded"]:
            assert excluded["display_name"] in shown.text()
            assert excluded["reason"] in shown.text()
        assert _shows(
            shown, stored["evidence"]["marginal"]["figures"]["without_total_return"]["value"]
        )

    def test_missing_items_are_listed_on_the_entry_and_absent_when_nothing_is_missing(
        self, client, isolated_db
    ):
        record = _stored_full_run()
        panel = _panel(client)
        for stored in record["entries"]:
            node = pb.entry_node(panel, stored["symphony_id"])
            missing = node.find("tenure-review-missing")
            if not stored["missing_items"]:
                assert missing is None, stored["display_name"]
                continue
            assert missing is not None, stored["display_name"]
            text = missing.text().lower().replace("_", " ")
            assert "missing" in text
            for item in stored["missing_items"]:
                assert item.replace("_", " ") in text, (stored["display_name"], item)


class TestDecisionLifecycleOnEntries:
    def test_a_pending_retire_candidate_shows_its_explanation_with_the_date_and_both_buttons(
        self, client, isolated_db
    ):
        record = _stored_full_run(explanation="Redundant with its stronger sibling.")
        panel = _panel(client)
        stored = pb.entry_with_verdict(record, "retire_candidate")
        node = pb.entry_node(panel, stored["symphony_id"])
        explanation = node.find("retirement-rec-explanation")
        assert "Redundant with its stronger sibling." in explanation.text()
        assert "2026-12-31" in explanation.text(), "the day the explanation was written"
        assert node.find("retirement-rec-status").text() == "Pending"
        approve, reject = _lifecycle_buttons(node)
        assert len(approve) == 1 and len(reject) == 1
        assert approve[0].attrs["data-candidate-id"] == stored["symphony_id"]
        assert reject[0].attrs["data-candidate-id"] == stored["symphony_id"]
        assert approve[0].attrs["onclick"] == "retDispatchDecision('approve', this)"
        assert reject[0].attrs["onclick"] == "retDispatchDecision('reject', this)"

    def test_the_buttons_and_the_status_chip_share_one_card_the_client_script_can_find(
        self, client, isolated_db
    ):
        record = _stored_full_run()
        panel = _panel(client)
        retire = pb.entry_with_verdict(record, "retire_candidate")
        entry = pb.entry_node(panel, retire["symphony_id"])
        approve = entry.find("retirement-approve-btn")
        assert approve is not None
        card = approve.ancestor("retirement-recommendation-card")
        assert card is not None, "retDispatchDecision looks the card up from the clicked button"
        assert card is entry or card.is_inside(entry)
        assert card.find("retirement-rec-status") is not None
        assert card.find("retirement-reject-btn") is not None
        assert len(panel.find_all("retirement-recommendation-card")) == 1

    def test_a_retire_candidate_without_an_explanation_says_so_and_keeps_its_buttons(
        self, client, isolated_db
    ):
        record = _stored_full_run(explanation=None, written_on=None)
        panel = _panel(client)
        stored = pb.entry_with_verdict(record, "retire_candidate")
        node = pb.entry_node(panel, stored["symphony_id"])
        assert "explanation unavailable" in node.find("retirement-rec-explanation").text()
        approve, reject = _lifecycle_buttons(node)
        assert len(approve) == 1 and len(reject) == 1

    def test_only_the_retire_candidate_offers_approve_and_reject(self, client, isolated_db):
        record = _stored_full_run()
        panel = _panel(client)
        for stored in record["entries"]:
            approve, reject = _lifecycle_buttons(pb.entry_node(panel, stored["symphony_id"]))
            expected = 1 if stored["verdict"] == "retire_candidate" else 0
            assert (len(approve), len(reject)) == (expected, expected), stored["display_name"]
        assert len(panel.find_all("retirement-approve-btn")) == 1
        assert len(panel.find_all("retirement-reject-btn")) == 1

    def test_keep_and_watch_entries_with_no_decision_show_no_checklist(self, client, isolated_db):
        record = _stored_full_run()
        panel = _panel(client)
        for verdict in ("keep", "watch"):
            stored = pb.entry_with_verdict(record, verdict)
            node = pb.entry_node(panel, stored["symphony_id"])
            assert node.find("retirement-checklist") is None
            assert node.find("retirement-approve-btn") is None

    def test_an_approved_decision_on_a_watch_entry_shows_status_and_checklist_without_buttons(
        self, client, isolated_db
    ):
        record = _stored_full_run()
        watch = pb.entry_with_verdict(record, "watch")
        db_module.upsert_retirement_decision(watch["symphony_id"], approval_status="approved")
        before = pb.decisions_by_id()
        panel = _panel(client)
        node = pb.entry_node(panel, watch["symphony_id"])
        assert node.find("retirement-rec-status").text() == "Approved"
        checklist = node.find("retirement-checklist")
        assert checklist is not None
        assert "NVDA" in checklist.text(), "the checklist must list this symphony's own holdings"
        assert "AAPL" not in checklist.text()
        assert pb.FULL_NAMES["watch"] in checklist.text()
        approve, reject = _lifecycle_buttons(node)
        assert approve == [] and reject == []
        assert pb.decisions_by_id() == before, "rendering must not change the decision row"

    def test_an_approved_retire_candidate_shows_the_checklist_with_its_own_holdings(
        self, client, isolated_db
    ):
        record = _stored_full_run()
        retire = pb.entry_with_verdict(record, "retire_candidate")
        db_module.upsert_retirement_decision(retire["symphony_id"], approval_status="approved")
        node = pb.entry_node(_panel(client), retire["symphony_id"])
        assert node.find("retirement-rec-status").text() == "Approved"
        checklist = node.find("retirement-checklist")
        assert "AAPL" in checklist.text() and "MSFT" in checklist.text()
        assert "NVDA" not in checklist.text()
        assert _lifecycle_buttons(node) == ([], [])

    def test_each_approved_entry_gets_its_own_checklist(self, client, isolated_db):
        record = _stored_full_run()
        ids = pb.FULL_IDS
        for key in ("retire", "watch", "keep"):
            db_module.upsert_retirement_decision(ids[key], approval_status="approved")
        panel = _panel(client)
        expected = {"retire": "AAPL", "watch": "NVDA", "keep": "GLD"}
        for key, ticker in expected.items():
            checklist = pb.entry_node(panel, ids[key]).find("retirement-checklist")
            assert checklist is not None, key
            assert ticker in checklist.text(), key
            for other_key, other_ticker in expected.items():
                if other_key != key:
                    assert other_ticker not in checklist.text(), (key, other_key)
        assert len(record["entries"]) == 7

    def test_a_rejected_entry_shows_rejected_with_no_buttons_and_no_checklist(
        self, client, isolated_db
    ):
        record = _stored_full_run()
        keep = pb.entry_with_verdict(record, "keep")
        db_module.upsert_retirement_decision(keep["symphony_id"], approval_status="rejected")
        node = pb.entry_node(_panel(client), keep["symphony_id"])
        assert node.find("retirement-rec-status").text() == "Rejected"
        assert node.find("retirement-checklist") is None
        assert _lifecycle_buttons(node) == ([], [])


class TestEscaping:
    def test_hostile_entry_fields_are_escaped_and_cannot_open_an_attribute(
        self, client, isolated_db
    ):
        pb.seed_bot_state()
        record = pb.full_record()
        hostile = '"><script>alert(1)</script><b onmouseover="alert(2)'
        for stored in record["entries"]:
            stored["display_name"] = hostile
            stored["eligibility_text"] = hostile
            stored["verdict_text"] = hostile
            stored["verdict_reasons"] = [hostile]
            stored["missing_items"] = [hostile]
            stored["basis"]["label"] = hostile
            stored["evidence"]["performance"]["label"] = hostile
            stored["evidence"]["performance"]["subject"] = hostile
            stored["evidence"]["performance"]["source"] = hostile
            stored["evidence"]["marginal"]["excluded"] = [
                {"display_name": hostile, "reason": hostile}
            ]
        record["threshold_note"] = hostile
        pb.set_explanation(record, pb.FULL_IDS["retire"], hostile, hostile)
        pb.store_run(record)
        body = _page(client)
        assert "<script>alert(1)</script>" not in body
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
        panel = pb.parse_html(body).find("retirement-recommendations-panel")
        offenders = [
            node.tag for node in panel.walk() if "onmouseover" in {k.lower() for k in node.attrs}
        ]
        assert offenders == [], "a stored value opened an event handler attribute"
        assert not any(n.tag == "script" for n in panel.walk())


class TestRunStateNotices:
    def test_a_run_in_progress_shows_the_previous_run_with_a_running_since_notice(
        self, client, isolated_db
    ):
        record = _stored_full_run()
        assert tenure_review.REVIEW_RUN_STATE.try_start("on_demand")
        since = tenure_review.REVIEW_RUN_STATE.snapshot()["running_since"]
        panel = _panel(client)
        notice = panel.find("tenure-review-running-notice")
        assert notice is not None
        assert "review running since" in notice.text()
        assert since[:4] in notice.text(), "the start time is part of the notice"
        assert len(panel.find_all("tenure-review-entry")) == len(record["entries"])
        assert panel.find("tenure-review-empty") is None

    def test_a_run_in_progress_with_no_earlier_run_still_says_no_review_yet(
        self, client, isolated_db
    ):
        assert tenure_review.REVIEW_RUN_STATE.try_start("nightly")
        panel = _panel(client)
        assert panel.find("tenure-review-running-notice") is not None
        assert panel.find("tenure-review-empty") is not None

    def test_the_notice_is_gone_once_the_run_has_finished(self, client, isolated_db):
        _stored_full_run()
        state = tenure_review.REVIEW_RUN_STATE
        assert state.try_start("nightly")
        assert _panel(client).find("tenure-review-running-notice") is not None
        state.finish_success()
        assert _panel(client).find("tenure-review-running-notice") is None

    def test_a_failed_attempt_is_stated_with_its_reason_and_time_next_to_the_previous_run(
        self, client, isolated_db
    ):
        record = _stored_full_run()
        state = tenure_review.REVIEW_RUN_STATE
        assert state.try_start("nightly")
        state.finish_failure(
            "Composer statistics unavailable", datetime(2026, 12, 31, 15, 0, tzinfo=UTC)
        )
        panel = _panel(client)
        notice = panel.find("tenure-review-failed-notice")
        assert notice is not None
        assert "latest review attempt failed: Composer statistics unavailable" in notice.text()
        assert "2026-12-31" in notice.text(), "the attempt time"
        assert len(panel.find_all("tenure-review-entry")) == len(record["entries"])

    def test_no_failed_notice_is_shown_when_no_attempt_failed(self, client, isolated_db):
        _stored_full_run()
        panel = _panel(client)
        assert panel.find("tenure-review-as-of") is not None, "the header must be rendered"
        assert panel.find("tenure-review-failed-notice") is None


class TestRunButton:
    def test_the_button_is_wired_to_the_client_function_and_has_a_message_slot(
        self, client, isolated_db
    ):
        _stored_full_run()
        panel = _panel(client)
        button = panel.find("tenure-review-run-btn")
        assert button.tag == "button"
        assert button.attrs.get("type") == "button"
        assert button.attrs.get("onclick") == "retRunReview(this)"
        assert "run-trigger-btn" in button.classes()
        assert "Run review now" in button.text()
        assert "disabled" not in button.attrs
        message = panel.find("tenure-review-run-message")
        assert message is not None
        assert message.text() == "", "no message is shown before a request"

    def test_the_button_is_shown_while_a_run_is_in_progress_so_the_refusal_can_be_read(
        self, client, isolated_db
    ):
        _stored_full_run()
        assert tenure_review.REVIEW_RUN_STATE.try_start("nightly")
        assert _panel(client).find("tenure-review-run-btn") is not None


class TestReadPathsMakeNoComposerRequest:
    def test_rendering_a_completed_run_makes_no_outbound_request_and_writes_nothing(
        self, client, isolated_db, monkeypatch
    ):
        record = _stored_full_run()
        db_module.upsert_retirement_decision(pb.FULL_IDS["retire"], approval_status="approved")
        attempts = pb.forbid_network(monkeypatch)
        observations = pb.observation_count()
        decisions = pb.decisions_by_id()
        panel = _panel(client)
        assert len(panel.find_all("tenure-review-entry")) == len(record["entries"])
        assert attempts == []
        assert pb.observation_count() == observations
        assert pb.decisions_by_id() == decisions


class TestApproveThenReload:
    def test_approving_a_retire_candidate_writes_one_decision_and_a_reload_shows_the_checklist(
        self, client, isolated_db, monkeypatch
    ):
        record = _stored_full_run()
        retire = pb.entry_with_verdict(record, "retire_candidate")
        attempts = pb.forbid_network(monkeypatch)
        resp = client.post(
            "/ai-advisor/retirement/approve",
            json={"candidate_id": retire["symphony_id"]},
        )
        assert resp.status_code == 200
        assert resp.get_json() == {"success": True, "approval_status": "approved", "error": None}
        decisions = pb.decisions_by_id()
        assert list(decisions) == [retire["symphony_id"]]
        assert decisions[retire["symphony_id"]]["approval_status"] == "approved"

        node = pb.entry_node(_panel(client), retire["symphony_id"])
        assert node.find("retirement-rec-status").text() == "Approved"
        assert "AAPL" in node.find("retirement-checklist").text()
        assert _lifecycle_buttons(node) == ([], [])
        assert attempts == [], "approving and reloading must make no Composer request"


class TestPanelWiring:
    def test_the_template_receives_the_run_its_entries_and_the_run_state(
        self, client, isolated_db, monkeypatch
    ):
        record = _stored_full_run()
        seen = {}
        real = app_module.render_template

        def spy(name, **context):
            if name == "ai_advisor.html":
                seen.update(context)
            return real(name, **context)

        monkeypatch.setattr(app_module, "render_template", spy)
        _page(client)
        assert seen["tenure_review"]["run_id"] == record["run_id"]
        assert sorted(e["symphony_id"] for e in seen["tenure_review_entries"]) == sorted(
            e["symphony_id"] for e in record["entries"]
        )
        assert seen["tenure_review_status"]["running"] is False

    def test_the_template_context_is_empty_but_present_before_any_run(
        self, client, isolated_db, monkeypatch
    ):
        seen = {}
        real = app_module.render_template

        def spy(name, **context):
            if name == "ai_advisor.html":
                seen.update(context)
            return real(name, **context)

        monkeypatch.setattr(app_module, "render_template", spy)
        _page(client)
        assert seen["tenure_review"] is None
        assert list(seen["tenure_review_entries"]) == []
        assert seen["tenure_review_status"]["running"] is False

    def test_the_review_fetch_helper_exists_for_the_route_and_the_panel_to_share(self):
        assert callable(getattr(app_module, "_fetch_tenure_review", None))

    def test_all_review_prefetch_in_the_page_function_sits_inside_the_scanned_window(self):
        """The no-trade scan of the page function covers only the text between two
        comment markers, so review code placed outside them would go unscanned."""
        source = (Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")
        start = re.search(
            r"# Retirement Recommendations panel \([^)]*\): prefetch the", source
        ).start()
        end = source.index("# Frontrunner Builder panel: prefetch pending frontrunner_proposals")
        window = range(start, end)
        tree = ast.parse(source)
        page = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "ai_advisor_tab"
        )
        line_starts = [0]
        for line in source.splitlines(keepends=True):
            line_starts.append(line_starts[-1] + len(line))
        calls = 0
        for node in ast.walk(page):
            if isinstance(node, ast.Name) and node.id == "_fetch_tenure_review":
                calls += 1
                offset = line_starts[node.lineno - 1] + node.col_offset
                assert offset in window, (
                    f"_fetch_tenure_review is used in ai_advisor_tab at line {node.lineno}, "
                    "outside the block the no-trade scan covers"
                )
        assert calls >= 1, "ai_advisor_tab must prefetch the review through the shared helper"


class TestDesignSystem:
    @staticmethod
    def _css_rules():
        source = (Path(__file__).resolve().parents[2] / "templates" / "ai_advisor.html").read_text(
            encoding="utf-8"
        )
        style = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", source, flags=re.DOTALL))
        return re.findall(r"([^{}]+)\{([^{}]*)\}", style)

    def test_new_review_rules_use_design_tokens_and_no_raw_color_literals(self):
        rules = [(sel, body) for sel, body in self._css_rules() if "tenure-" in sel]
        assert rules, "the review panel has no styles of its own"
        for selector, body in rules:
            assert not re.search(r"#[0-9a-fA-F]{3,8}\b", body), f"raw hex in {selector.strip()}"
            assert not re.search(r"\b(rgb|rgba|hsl|hsla)\(", body), (
                f"raw color in {selector.strip()}"
            )

    def test_the_verdict_chip_modifiers_use_the_studio_tokens(self):
        css = " ".join(
            sel + body for sel, body in self._css_rules() if "tenure-verdict-chip" in sel
        )
        for token in ("--studio-pos", "--studio-neg", "--studio-ink-dim", "--studio-border"):
            assert token in css, f"verdict chip styles never use {token}"

    @staticmethod
    def _css_of_exact_classes(rules, classes) -> str:
        """The bodies of the rules whose selector names one of ``classes`` as a whole
        class (``.tenure-review-notice`` does not match ``.tenure-review-notice--failed``)."""
        patterns = [re.compile(rf"\.{re.escape(c)}(?![\w-])") for c in classes]
        return " ".join(body for sel, body in rules if any(pat.search(sel) for pat in patterns))

    def test_the_failed_attempt_notice_is_styled_with_the_negative_token(self, client, isolated_db):
        _stored_full_run()
        state = tenure_review.REVIEW_RUN_STATE
        assert state.try_start("nightly")
        state.finish_failure("Composer statistics unavailable", datetime.now(UTC))
        notice = _panel(client).find("tenure-review-failed-notice")
        classes = notice.classes()
        assert classes
        css = self._css_of_exact_classes(self._css_rules(), classes)
        assert "--studio-neg" in css

    def test_the_exact_class_check_does_not_credit_a_base_class_with_its_modifier_rule(self):
        """Mutation proof of the check itself: a notice that lost its modifier class keeps
        only the base class, whose own rule is not negative, so the check must then fail."""
        rules = [
            (".tenure-review-notice ", "color: var(--studio-ink-dim);"),
            (".tenure-review-notice--failed ", "color: var(--studio-neg);"),
        ]
        with_modifier = self._css_of_exact_classes(
            rules, ["tenure-review-notice", "tenure-review-notice--failed"]
        )
        assert "--studio-neg" in with_modifier
        base_only = self._css_of_exact_classes(rules, ["tenure-review-notice"])
        assert "--studio-neg" not in base_only

    def test_no_dash_characters_appear_in_the_new_review_text(self, client, isolated_db):
        _stored_full_run()
        state = tenure_review.REVIEW_RUN_STATE
        assert state.try_start("nightly")
        panel = _panel(client)
        review_nodes = [
            n for n in panel.walk() if n.testid and n.testid.startswith("tenure-review-")
        ]
        assert len(review_nodes) > 40, "the review text must actually be rendered to be checked"
        for node in review_nodes:
            text = node.text()
            assert chr(0x2014) not in text and chr(0x2013) not in text, (node.testid, text)


class TestLatestRunOnlyInThePanel:
    def test_two_runs_on_one_date_show_each_live_symphony_once_from_the_later_run(
        self, client, isolated_db
    ):
        pb.seed_bot_state()
        first = pb.full_record(pb.RUN_ID_FIRST, completed_at="2026-12-31T03:50:00+00:00")
        pb.set_explanation(first, pb.FULL_IDS["retire"], "Written by the first run.", "2026-12-30")
        pb.store_recommendation(
            pb.recommendation_for(
                first,
                pb.entry_of(first, pb.FULL_IDS["retire"]),
                explanation="Written by the first run.",
                written_on="2026-12-30",
            )
        )
        pb.store_run(first)
        second = pb.full_record(
            pb.RUN_ID_SECOND, trigger="on_demand", completed_at="2026-12-31T21:10:00+00:00"
        )
        pb.entry_of(second, pb.FULL_IDS["keep"])["display_name"] = "Yankee Tail Two Renamed"
        pb.set_explanation(
            second, pb.FULL_IDS["retire"], "Written by the second run.", "2026-12-31"
        )
        pb.store_recommendation(
            pb.recommendation_for(
                second,
                pb.entry_of(second, pb.FULL_IDS["retire"]),
                explanation="Written by the second run.",
                written_on="2026-12-31",
            )
        )
        pb.store_run(second)

        panel = _panel(client)
        nodes = panel.find_all("tenure-review-entry")
        ids = [n.attrs["data-symphony-id"] for n in nodes]
        assert len(ids) == len(set(ids)) == len(second["entries"])
        renamed = pb.entry_node(panel, pb.FULL_IDS["keep"]).find("tenure-review-name")
        assert renamed.text() == "Yankee Tail Two Renamed"
        text = pb.entry_node(panel, pb.FULL_IDS["retire"]).find("retirement-rec-explanation").text()
        assert "Written by the second run." in text
        assert "Written by the first run." not in panel.text()
        assert len(panel.find_all("retirement-recommendation-card")) == 1

    def test_recommendation_cards_from_before_the_review_do_not_sit_beside_a_completed_run(
        self, client, isolated_db
    ):
        _seed_symphony_roster("cand-before-review", "sib-before-review")
        _seed_recommendation(candidate_id="cand-before-review", sibling_id="sib-before-review")
        _stored_full_run()
        panel = _panel(client)
        assert "cand-before-review" not in panel.everything()
        assert len(panel.find_all("retirement-recommendation-card")) == 1


class TestMalformedStoredRun:
    def test_malformed_entries_do_not_break_the_page_and_good_entries_still_render(
        self, client, isolated_db
    ):
        pb.seed_bot_state()
        record = pb.full_record()
        good = pb.entry_of(record, pb.FULL_IDS["keep"])
        record["entries"] = [good, "garbage", 42, None, {"symphony_id": "partial-only"}]
        pb.store_run(record)
        panel = _panel(client)
        assert pb.entry_node(panel, pb.FULL_IDS["keep"]).find("tenure-review-name") is not None

    def test_a_failed_read_of_the_run_alone_leaves_a_working_page_that_says_the_review_is_unreadable(
        self, client, isolated_db, monkeypatch
    ):
        _stored_full_run()
        real = db_module.get_advisor_observations_for_role

        def selective(role, *args, **kwargs):
            if role == tenure_review.TENURE_REVIEW_RUN_ROLE:
                raise RuntimeError("simulated run read failure")
            return real(role, *args, **kwargs)

        monkeypatch.setattr(db_module, "get_advisor_observations_for_role", selective)
        panel = _panel(client)
        assert panel.find("tenure-review-read-failed") is not None
        assert panel.find("tenure-review-empty") is None, (
            "a run that could not be read is not 'no review yet'"
        )
        assert panel.find_all("tenure-review-entry") == []
        assert panel.find("tenure-review-run-btn") is not None


# ===========================================================================
# Findings round: what the panel must show for the figures a record stores.
#
# The edge record (tests/app/_tenure_review_panel_builders.py) is produced by the real
# review compute over four invented symphonies chosen so that figures are null, a pair
# is absent, two gates carry different texts and a series begins after the tenure does.
# ===========================================================================

_DECIMAL = re.compile(r"\d+\.\d+")
_PAIR_FIGURES = ("correlation", "ci_lower", "ci_upper", "stressed_correlation", "holdings_overlap")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _store_edge_run(**kwargs):
    pb.seed_edge_bot_state()
    record = pb.edge_record(**kwargs)
    pb.store_run(record)
    return record


def _null_figures(item: dict) -> list[tuple[str, str]]:
    """(figure name, stored reason) of every null figure an item holds, pair figures
    included."""
    found = [(n, f["reason"]) for n, f in (item.get("figures") or {}).items() if f["value"] is None]
    pair = item.get("pair")
    if pair:
        found += [(n, pair[n]["reason"]) for n in _PAIR_FIGURES if pair[n]["value"] is None]
    return found


class TestNullFiguresKeepTheirStoredReason:
    def test_every_null_figure_of_an_available_item_is_named_with_its_stored_reason(
        self, client, isolated_db
    ):
        record = _store_edge_run()
        panel = _panel(client)
        checked = []
        for stored in record["entries"]:
            node = pb.entry_node(panel, stored["symphony_id"])
            for name in _PANEL_ITEMS:
                item = stored["evidence"][name]
                if item["status"] != "available":
                    continue
                block = node.find(f"tenure-review-evidence-{name}")
                for fig_name, reason in _null_figures(item):
                    where = f"{stored['display_name']} / {name} / {fig_name}"
                    lines = pb.leaf_nodes_containing(block, fig_name.replace("_", " "))
                    assert lines, f"{where}: the figure is dropped from the panel"
                    shown = [line.text() for line in lines if reason in line.text()]
                    assert shown, f"{where}: the stored reason {reason!r} is not beside it"
                    assert not any(_DECIMAL.search(text) for text in shown), (
                        f"{where}: a null figure is shown with a number: {shown}"
                    )
                    checked.append(where)
        assert len(checked) >= 10, f"the record must exercise many null figures, got {checked}"

    def test_the_figures_that_are_defined_beside_a_null_one_still_show_their_values(
        self, client, isolated_db
    ):
        record = _store_edge_run()
        panel = _panel(client)
        steady = pb.entry_of(record, pb.EDGE_IDS["steady"])
        block = pb.entry_node(panel, steady["symphony_id"]).find(
            "tenure-review-evidence-performance"
        )
        figures = steady["evidence"]["performance"]["figures"]
        assert _shows(block, figures["total_return"]["value"])
        assert _shows(block, figures["sharpe"]["value"])


class TestTenureAndSeriesBeginningAreBothStated:
    def test_a_series_that_begins_after_the_tenure_states_both_dates(self, client, isolated_db):
        record = _store_edge_run()
        late = pb.entry_of(record, pb.EDGE_IDS["late"])
        basis = pb.entry_node(_panel(client), late["symphony_id"]).find("tenure-review-basis")
        text = basis.text()
        assert f"series begins {late['basis']['series_begins']}" in text
        assert f"tenure begins {late['basis']['tenure_begins']}" in text
        assert late["invested_since"] in text

    def test_every_entry_with_a_known_tenure_start_states_it_in_its_basis_line(
        self, client, isolated_db
    ):
        record = _stored_full_run()
        panel = _panel(client)
        checked = 0
        for stored in record["entries"]:
            if stored["basis"]["tenure_begins"] is None:
                continue
            text = pb.entry_node(panel, stored["symphony_id"]).find("tenure-review-basis").text()
            assert f"tenure begins {stored['basis']['tenure_begins']}" in text, stored[
                "display_name"
            ]
            checked += 1
        assert checked >= 5

    def test_an_unknown_tenure_start_is_never_printed_as_a_value(self, client, isolated_db):
        record = _stored_full_run()
        dateless = pb.entry_of(record, pb.FULL_IDS["dateless"])
        assert dateless["basis"]["tenure_begins"] is None
        text = (
            pb.entry_node(_panel(client), dateless["symphony_id"])
            .find("tenure-review-basis")
            .text()
        )
        assert not re.search(r"\bNone\b|\bnull\b", text), text
        for stated in re.findall(r"tenure begins (\S+)", text):
            assert _DATE.fullmatch(stated), f"'tenure begins' is followed by {stated!r}"


class TestNoRedundantSiblingIsStated:
    def test_an_eligible_symphony_with_no_screen_hit_says_there_is_no_redundant_sibling(
        self, client, isolated_db
    ):
        record = _store_edge_run()
        steady = pb.entry_of(record, pb.EDGE_IDS["steady"])
        item = steady["evidence"]["redundancy"]
        assert item["status"] == "available" and item["pair"] is None
        block = pb.entry_node(_panel(client), steady["symphony_id"]).find(
            "tenure-review-evidence-redundancy"
        )
        assert item["summary"] in block.text()
        assert "no redundant sibling" in block.text()
        assert "unavailable" not in block.text().lower(), (
            "a symphony that was checked and has no redundant sibling is not 'unavailable'"
        )

    def test_both_eligible_symphonies_of_the_small_run_say_so(self, client, isolated_db):
        pb.seed_bot_state(include_small=True)
        record = pb.small_record()
        pb.store_run(record)
        panel = _panel(client)
        for key in ("first", "second"):
            stored = pb.entry_of(record, pb.SMALL_IDS[key])
            block = pb.entry_node(panel, stored["symphony_id"]).find(
                "tenure-review-evidence-redundancy"
            )
            assert stored["evidence"]["redundancy"]["summary"] in block.text(), key

    def test_a_symphony_with_a_pair_does_not_claim_there_is_no_sibling(self, client, isolated_db):
        record = _stored_full_run()
        retire = pb.entry_with_verdict(record, "retire_candidate")
        block = pb.entry_node(_panel(client), retire["symphony_id"]).find(
            "tenure-review-evidence-redundancy"
        )
        assert "no redundant sibling" not in block.text()


def _gate_problems(block: pb.Node, pair: dict) -> list[str]:
    """Each gate's stored result must follow its own label; one that sits beside the other
    gate's label, or nowhere, is a problem."""
    text = block.text().lower()
    problems = []
    for label, gate in (
        ("uncertainty gate", pair["uncertainty_gate"]),
        ("structural redundancy gate", pair["structural_gate"]),
    ):
        pattern = rf"{re.escape(label)}\W*{re.escape(gate['text'].lower())}"
        if not re.search(pattern, text):
            problems.append(f"{label} is not followed by {gate['text']!r}")
    return problems


def _paired_entries() -> list[tuple[str, dict]]:
    """(name, entry) of every entry holding a redundancy pair, over both records."""
    return [
        (stored["display_name"], stored)
        for record in (pb.full_record(), pb.edge_record())
        for stored in record["entries"]
        if stored["evidence"]["redundancy"]["pair"]
    ]


class TestEachGateResultSitsBesideItsOwnLabel:
    def _render_both(self, client):
        """Store the full run, read its page, then store the edge run and read that: the
        newest run is the one shown."""
        pb.seed_bot_state()
        full = pb.full_record(pb.RUN_ID_FIRST)
        pb.store_run(full)
        full_panel = _panel(client)
        pb.seed_edge_bot_state()
        edge = pb.edge_record(pb.RUN_ID_SECOND)
        pb.store_run(edge)
        return (full, full_panel), (edge, _panel(client))

    def test_the_two_gates_of_every_pair_are_each_stated_after_their_own_label(
        self, client, isolated_db
    ):
        checked = 0
        for record, panel in self._render_both(client):
            for stored in record["entries"]:
                pair = stored["evidence"]["redundancy"]["pair"]
                if not pair:
                    continue
                block = pb.entry_node(panel, stored["symphony_id"]).find(
                    "tenure-review-evidence-redundancy"
                )
                assert _gate_problems(block, pair) == [], stored["display_name"]
                checked += 1
        assert checked >= 5

    def test_the_fixtures_include_pairs_whose_two_gates_read_differently(self):
        paired = _paired_entries()
        differing = [
            name
            for name, stored in paired
            if stored["evidence"]["redundancy"]["pair"]["uncertainty_gate"]["text"]
            != stored["evidence"]["redundancy"]["pair"]["structural_gate"]["text"]
        ]
        assert len(differing) >= 3, "a swapped or missing gate line must change some text"

    def test_the_check_rejects_a_panel_whose_uncertainty_gate_line_is_missing(
        self, client, isolated_db
    ):
        """Mutation proof: delete the uncertainty gate line from the parsed panel and the
        same check must fail, even though the bare word 'passed' is still on the page."""
        pb.seed_bot_state()
        record = pb.full_record()
        pb.store_run(record)
        watch = pb.entry_with_verdict(record, "watch")
        pair = watch["evidence"]["redundancy"]["pair"]
        assert pair["uncertainty_gate"]["text"] == "passed"
        block = pb.entry_node(_panel(client), watch["symphony_id"]).find(
            "tenure-review-evidence-redundancy"
        )
        assert _gate_problems(block, pair) == []
        line = pb.leaf_nodes_containing(block, "uncertainty gate")[0]
        line.parent.children.remove(line)
        assert _gate_problems(block, pair), "a deleted uncertainty gate line went unnoticed"

    def test_the_check_rejects_a_panel_whose_gate_results_are_swapped(self, client, isolated_db):
        pb.seed_bot_state()
        record = pb.full_record()
        pb.store_run(record)
        watch = pb.entry_with_verdict(record, "watch")
        pair = watch["evidence"]["redundancy"]["pair"]
        swapped = dict(
            pair,
            uncertainty_gate=pair["structural_gate"],
            structural_gate=pair["uncertainty_gate"],
        )
        block = pb.entry_node(_panel(client), watch["symphony_id"]).find(
            "tenure-review-evidence-redundancy"
        )
        assert _gate_problems(block, pair) == []
        assert _gate_problems(block, swapped), "swapped gate results went unnoticed"


class TestDayCountsAreWholeNumbers:
    def test_the_long_vix_day_counts_read_as_counts_not_as_decimals(self, client, isolated_db):
        record = _store_edge_run()
        panel = _panel(client)
        checked = 0
        for stored in record["entries"]:
            item = stored["evidence"]["long_vix"]
            if item["status"] != "available":
                continue
            block = pb.entry_node(panel, stored["symphony_id"]).find(
                "tenure-review-evidence-long_vix"
            )
            for fig_name, fig in item["figures"].items():
                if fig["unit"] != "days":
                    continue
                label = fig_name.replace("_", " ")
                lines = [
                    n
                    for n in pb.leaf_nodes_containing(block, label)
                    if n.text().lower().startswith(label)
                ]
                where = f"{stored['display_name']} / {fig_name}"
                assert len(lines) == 1, where
                text = lines[0].text()
                whole = int(fig["value"])
                assert re.search(rf"(?<![\d.]){whole}(?![\d.])", text), (where, text)
                assert not re.search(rf"{whole}\.\d", text), (
                    f"{where}: a count of days is shown with decimals: {text!r}"
                )
                checked += 1
        assert checked >= 6
        zero_counts = [
            f
            for s in record["entries"]
            for f in s["evidence"]["long_vix"]["figures"].values()
            if f["unit"] == "days" and f["value"] == 0
        ]
        assert zero_counts, "the record must hold a zero count, a computed value shown as 0"


class TestEvidenceBlocksHaveDistinctHeadings:
    def test_each_block_opens_with_a_heading_and_no_two_headings_are_alike(
        self, client, isolated_db
    ):
        record = _stored_full_run()
        panel = _panel(client)
        for stored in record["entries"]:
            node = pb.entry_node(panel, stored["symphony_id"])
            headings = {}
            for name in _PANEL_ITEMS:
                block = node.find(f"tenure-review-evidence-{name}")
                found = block.find_all("tenure-review-evidence-heading")
                where = f"{stored['display_name']} / {name}"
                assert len(found) == 1, f"{where}: expected one heading, got {len(found)}"
                headings[name] = found[0].text().strip().lower()
                assert headings[name], where
            assert len(set(headings.values())) == len(headings), (
                f"{stored['display_name']}: two blocks share a heading: {headings}"
            )
            for name in ("performance", "composite", "redundancy"):
                assert name in headings[name], (
                    f"{stored['display_name']}: the {name} heading does not say which item it is"
                )

    def test_the_basis_label_is_still_shown_in_each_block(self, client, isolated_db):
        record = _stored_full_run()
        panel = _panel(client)
        keep = pb.entry_with_verdict(record, "keep")
        node = pb.entry_node(panel, keep["symphony_id"])
        for name in _PANEL_ITEMS:
            assert (
                keep["evidence"][name]["label"]
                in node.find(f"tenure-review-evidence-{name}").text()
            ), name


class TestAnUnavailableItemStillShowsItsWindow:
    def test_a_window_the_record_carries_is_rendered_beside_the_reason(self, client, isolated_db):
        record = _stored_full_run()
        panel = _panel(client)
        checked = 0
        for stored in record["entries"]:
            node = pb.entry_node(panel, stored["symphony_id"])
            for name in _PANEL_ITEMS:
                item = stored["evidence"][name]
                window = item["window"]
                if item["status"] == "available" or not window:
                    continue
                text = node.find(f"tenure-review-evidence-{name}").text()
                where = f"{stored['display_name']} / {name}"
                assert item["reason"] in text, where
                assert window["first"] in text and window["last"] in text, where
                assert str(window["days"]) in text, where
                checked += 1
        assert checked >= 3, "the record must hold unavailable items that carry a window"


class TestExcludedSymphoniesKeepTheirOwnReason:
    def test_each_left_out_symphony_is_listed_with_the_reason_stored_for_it(
        self, client, isolated_db
    ):
        record = _stored_full_run()
        retire = pb.entry_with_verdict(record, "retire_candidate")
        excluded = retire["evidence"]["marginal"]["excluded"]
        assert len({x["reason"] for x in excluded}) >= 2, "the fixture must hold distinct reasons"
        block = pb.entry_node(_panel(client), retire["symphony_id"]).find(
            "tenure-review-evidence-marginal"
        )
        for ex in excluded:
            lines = pb.leaf_nodes_containing(block, ex["display_name"])
            assert any(ex["reason"] in line.text() for line in lines), ex


class TestExplanationUnavailableIsOnlyForRetireCandidates:
    def test_a_decided_entry_that_is_not_a_retire_candidate_claims_no_missing_explanation(
        self, client, isolated_db
    ):
        record = _stored_full_run()
        watch = pb.entry_with_verdict(record, "watch")
        keep = pb.entry_with_verdict(record, "keep")
        db_module.upsert_retirement_decision(watch["symphony_id"], approval_status="approved")
        db_module.upsert_retirement_decision(keep["symphony_id"], approval_status="rejected")
        panel = _panel(client)
        for stored in (watch, keep):
            node = pb.entry_node(panel, stored["symphony_id"])
            assert node.find("retirement-rec-status") is not None, "the decision must still show"
            assert "explanation unavailable" not in node.text(), stored["display_name"]

    def test_a_retire_candidate_without_an_explanation_still_says_so(self, client, isolated_db):
        record = _stored_full_run(explanation=None, written_on=None)
        retire = pb.entry_with_verdict(record, "retire_candidate")
        node = pb.entry_node(_panel(client), retire["symphony_id"])
        assert "explanation unavailable" in node.text()


class TestAFreshInstallShowsOneEmptyStateLine:
    def test_with_no_run_and_no_recommendation_there_is_one_empty_line_and_it_is_the_reviews(
        self, client, isolated_db
    ):
        panel = _panel(client)
        lines = [n for n in panel.walk() if "prism-empty" in n.classes()]
        assert len(lines) == 1, f"expected one empty-state line, got {[n.text() for n in lines]}"
        assert lines[0].testid == "tenure-review-empty"
        assert "No review yet" in lines[0].text()
        assert "No retirement recommendations yet" not in panel.text()

    def test_with_no_run_but_a_recommendation_the_card_follows_the_review_notice(
        self, client, isolated_db
    ):
        _seed_symphony_roster("cand-legacy", "sib-legacy")
        _seed_recommendation(candidate_id="cand-legacy", sibling_id="sib-legacy")
        panel = _panel(client)
        assert panel.find("tenure-review-empty") is not None
        assert len(panel.find_all("retirement-recommendation-card")) == 1
        assert "No retirement recommendations yet" not in panel.text()


class TestRunHeaderIsInEasternTime:
    _CASES = [
        ("2026-12-31T22:04:00+00:00", "2026-12-31", "17:04"),
        ("2026-07-15T02:30:00+00:00", "2026-07-14", "22:30"),
        ("2026-12-31T17:05:00-05:00", "2026-12-31", "17:05"),
    ]

    @pytest.mark.parametrize(
        "completed_at, et_date, et_time",
        _CASES,
        ids=["standard time", "daylight time, previous Eastern day", "already Eastern"],
    )
    def test_the_run_time_is_shown_in_us_eastern_with_its_zone(
        self, client, isolated_db, completed_at, et_date, et_time
    ):
        pb.seed_bot_state()
        pb.store_run(pb.full_record(completed_at=completed_at))
        text = _panel(client).find("tenure-review-run-time").text()
        assert et_date in text and et_time in text, text
        assert re.search(r"\bET\b", text), f"the zone is not stated: {text!r}"
        raw_date, raw_time = completed_at[:10], completed_at[11:16]
        if raw_date != et_date:
            assert raw_date not in text, "the UTC date is shown as if it were Eastern"
        if raw_time != et_time:
            assert raw_time not in text, "the stored UTC time is shown as if it were Eastern"

    @pytest.mark.parametrize(
        "trigger, words",
        [("nightly", "nightly"), ("on_demand", "on demand")],
        ids=["nightly", "on demand"],
    )
    def test_the_trigger_of_the_run_is_stated(self, client, isolated_db, trigger, words):
        pb.seed_bot_state()
        pb.store_run(pb.full_record(trigger=trigger))
        text = _panel(client).find("tenure-review-trigger").text()
        assert words in text
        assert "on_demand" not in text, "an identifier is shown instead of words"

    def test_the_as_of_day_the_run_time_and_the_trigger_sit_side_by_side(self, client, isolated_db):
        _stored_full_run()
        panel = _panel(client)
        as_of = panel.find("tenure-review-as-of")
        run_time = panel.find("tenure-review-run-time")
        trigger = panel.find("tenure-review-trigger")
        assert as_of.parent is run_time.parent is trigger.parent

    def test_a_nightly_run_and_a_later_on_demand_run_of_one_day_read_differently(
        self, client, isolated_db
    ):
        pb.seed_bot_state()
        pb.store_run(pb.full_record(pb.RUN_ID_FIRST, completed_at="2026-12-31T08:50:00+00:00"))
        first = _panel(client)
        first_time = first.find("tenure-review-run-time").text()
        first_trigger = first.find("tenure-review-trigger").text()
        pb.store_run(
            pb.full_record(
                pb.RUN_ID_SECOND, trigger="on_demand", completed_at="2026-12-31T21:10:00+00:00"
            )
        )
        second = _panel(client)
        second_time = second.find("tenure-review-run-time").text()
        second_trigger = second.find("tenure-review-trigger").text()
        assert first_time != second_time
        assert "03:50" in first_time and "16:10" in second_time
        assert "nightly" in first_trigger and "on demand" in second_trigger

    @pytest.mark.parametrize(
        "unusable", [None, "not a timestamp", 12345], ids=["missing", "text", "number"]
    )
    def test_a_run_time_that_cannot_be_read_does_not_break_the_page(
        self, client, isolated_db, unusable
    ):
        pb.seed_bot_state()
        record = pb.full_record()
        record["completed_at"] = unusable
        pb.store_run(record)
        panel = _panel(client)
        header = panel.find("tenure-review-as-of").parent
        assert not re.search(r"\bNone\b", header.text()), header.text()
        assert len(panel.find_all("tenure-review-entry")) == len(record["entries"])


class TestMalformedEntriesDegradeOnlyThatEntry:
    _POISON = [
        ("evidence.performance.figures", ["not", "a", "dict"]),
        ("evidence.performance.figures", "text"),
        ("evidence.performance.figures", 7),
        ("evidence.marginal.excluded", 7),
        ("evidence.marginal.excluded", "text"),
        ("evidence.marginal.excluded", {"a": 1}),
        ("missing_items", 5),
        ("missing_items", {"a": 1}),
        ("verdict_reasons", 5),
        ("evidence.redundancy.pair", ["x"]),
        ("evidence.performance.window", "text"),
        ("evidence.performance.window", 5),
        ("display_name", 5),
        ("eligibility_text", None),
    ]

    @pytest.mark.parametrize(
        "path, value",
        _POISON,
        ids=[f"{p} = {v!r}" for p, v in _POISON],
    )
    def test_a_malformed_field_never_takes_the_page_or_the_other_entries_down(
        self, client, isolated_db, path, value
    ):
        pb.seed_bot_state()
        record = pb.full_record()
        victim = pb.entry_with_verdict(record, "keep")
        *parents, leaf = path.split(".")
        target = victim
        for key in parents:
            target = target[key]
        target[leaf] = value
        pb.store_run(record)
        panel = _panel(client)
        for stored in record["entries"]:
            node = pb.entry_node(panel, stored["symphony_id"])
            assert node.find("tenure-review-name") is not None, stored["symphony_id"]
            if stored is victim:
                continue
            for name in _PANEL_ITEMS:
                assert node.find(f"tenure-review-evidence-{name}") is not None, (
                    f"{stored['display_name']} lost its {name} block"
                )


class TestAReviewThatCannotBeReadSaysSo:
    """The panel distinguishes 'no review yet' (nothing stored) from 'the review cannot be
    read' (a row exists or the read failed): the second shows no recommendation of any run,
    because which run owns them is unknown."""

    @pytest.fixture(params=["read raises", "record is not a dict", "module cannot be imported"])
    def unreadable_review(self, request, isolated_db, monkeypatch):
        _seed_symphony_roster("cand-legacy", "sib-legacy")
        _seed_recommendation(candidate_id="cand-legacy", sibling_id="sib-legacy")
        if request.param == "read raises":
            real = db_module.get_advisor_observations_for_role

            def selective(role, *args, **kwargs):
                if role == tenure_review.TENURE_REVIEW_RUN_ROLE:
                    raise RuntimeError("simulated run read failure")
                return real(role, *args, **kwargs)

            monkeypatch.setattr(db_module, "get_advisor_observations_for_role", selective)
        elif request.param == "record is not a dict":
            db_module.insert_advisor_observation(
                advisor_role=tenure_review.TENURE_REVIEW_RUN_ROLE,
                subject_type="tenure_review_run",
                subject_id=pb.RUN_ID_FIRST,
                verdict="completed",
                raw_response="this is not json {",
            )
        else:
            import advisors

            monkeypatch.delattr(advisors, "tenure_review")
            monkeypatch.setitem(sys.modules, "advisors.tenure_review", None)
        return request.param

    def test_the_page_answers_200_with_a_read_failure_notice_and_no_review_content(
        self, client, unreadable_review
    ):
        panel = _panel(client)
        failed = panel.find("tenure-review-read-failed")
        assert failed is not None, "the panel must say the review could not be read"
        assert "review unavailable" in failed.text().lower()
        assert panel.find("tenure-review-empty") is None
        assert panel.find_all("tenure-review-entry") == []
        assert panel.find_all("retirement-recommendation-card") == [], (
            "a recommendation of an unknown run must not be listed"
        )
        assert panel.find("tenure-review-run-btn") is not None

    def test_nothing_stored_is_still_no_review_yet_and_not_a_read_failure(
        self, client, isolated_db
    ):
        panel = _panel(client)
        assert panel.find("tenure-review-empty") is not None
        assert panel.find("tenure-review-read-failed") is None

    def test_a_run_state_snapshot_that_raises_leaves_the_page_working(
        self, client, isolated_db, monkeypatch
    ):
        _stored_full_run()

        def broken():
            raise RuntimeError("simulated run state failure")

        monkeypatch.setattr(tenure_review.REVIEW_RUN_STATE, "snapshot", broken)
        panel = _panel(client)
        assert panel.find("tenure-review-run-btn") is not None


class TestOneChecklistLoopServesEveryList:
    @staticmethod
    def _page_function():
        source = (Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")
        return next(
            n
            for n in ast.walk(ast.parse(source))
            if isinstance(n, ast.FunctionDef) and n.name == "ai_advisor_tab"
        )

    def test_the_page_function_imports_and_calls_the_checklist_builder_once(self):
        page = self._page_function()
        names = set()
        imports = 0
        for node in ast.walk(page):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "build_checklist":
                        names.add(alias.asname or alias.name)
                        imports += 1
        calls = [
            n
            for n in ast.walk(page)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in names
        ]
        assert imports == 1, f"build_checklist is imported {imports} times in the page function"
        assert len(calls) == 1, (
            f"build_checklist is called from {len(calls)} places in the page function; "
            "one loop must serve the recommendation list and the review entries"
        )


class TestChecklistWorkIsDoneOnlyForWhatThePanelRenders:
    def test_a_completed_run_builds_a_checklist_only_for_its_approved_entry(
        self, client, isolated_db, monkeypatch
    ):
        record = _stored_full_run()
        retire = pb.entry_with_verdict(record, "retire_candidate")
        db_module.upsert_retirement_decision(retire["symphony_id"], approval_status="approved")
        import advisors.retirement_checklist as checklist_module

        real = checklist_module.build_checklist
        calls = []

        def spy(recommendation, bot_state):
            calls.append(recommendation.get("candidate_id"))
            return real(recommendation, bot_state)

        monkeypatch.setattr(checklist_module, "build_checklist", spy)
        node = pb.entry_node(_panel(client), retire["symphony_id"])
        assert node.find("retirement-checklist") is not None
        assert calls == [retire["symphony_id"]], (
            "the recommendation row of the same run is not rendered, so it needs no checklist"
        )

    def test_with_no_run_the_approved_recommendation_card_still_gets_its_checklist(
        self, client, isolated_db
    ):
        _seed_symphony_roster("cand-legacy", "sib-legacy")
        _seed_recommendation(candidate_id="cand-legacy", sibling_id="sib-legacy")
        db_module.upsert_retirement_decision("cand-legacy", approval_status="approved")
        panel = _panel(client)
        assert panel.find("retirement-checklist") is not None


class TestApprovedEntryChecklistNamesTheSymphonyAsItsRowDoes:
    def test_a_name_the_engine_state_still_holds_differently_does_not_reach_the_checklist(
        self, client, isolated_db
    ):
        record = _stored_full_run()
        watch = pb.entry_with_verdict(record, "watch")
        state = db_module.load_state()
        state[watch["symphony_id"]]["name"] = "Name Held In Engine State"
        db_module.save_state(state)
        db_module.upsert_retirement_decision(watch["symphony_id"], approval_status="approved")
        node = pb.entry_node(_panel(client), watch["symphony_id"])
        row_name = node.find("tenure-review-name").text()
        checklist = node.find("retirement-checklist").text()
        assert row_name == watch["display_name"]
        assert row_name in checklist
        assert "Name Held In Engine State" not in checklist
        assert "NVDA" in checklist, "the holdings still come from the engine state of that hash"
