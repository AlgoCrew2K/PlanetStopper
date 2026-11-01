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
import copy
import functools
import re
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

import advisors.tenure_review as tenure_review
import app as app_module
import database as db_module
from database import init_db, run_migrations
from tests.advisors import _tenure_review_builders as review_builders
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
_NO_FLAG = object()  # a stored run that carries no degraded field at all
_FLAG_MARKER = "DEGRADED-FLAG-MARKER-7f3a"


def _page(client):
    resp = client.get("/ai-advisor")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def _panel(client):
    return pb.panel_of(_page(client))


def _two_decimal_forms(value: float) -> list[str]:
    text = f"{value:.2f}"
    return [text, text.replace("-", _MINUS)]


# A gap between two drawdown legs: (the first leg, the second leg). The stored gap is the first
# leg's signed drawdown minus the second's.
_DRAWDOWN_GAPS = {
    "max_drawdown_gap": ("realized", "backtest"),
    "max_drawdown_difference": ("without", "with"),
}


def _is_drawdown_leg(name: str) -> bool:
    return name == "max_drawdown" or name.endswith("_max_drawdown")


def _printed_figure(name: str, value: float) -> float:
    """A stored figure as the panel prints it: a drawdown leg is a magnitude, and so is the size of a
    gap between two legs; any other figure keeps its sign."""
    return abs(value) if _is_drawdown_leg(name) or name in _DRAWDOWN_GAPS else value


def _gap_words(name: str, value: float) -> str:
    """The words that follow a gap's size: which leg is the shallower, in magnitudes (the first leg's
    magnitude minus the second's is the stored gap with its sign turned, both legs being at most zero)."""
    first, second = _DRAWDOWN_GAPS[name]
    shown = round(-value, 2)
    if shown == 0:
        return f"{first} and {second} are equally deep"
    return f"{first} is {'shallower' if shown < 0 else 'deeper'} than {second}"


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

    DEGRADED_NOTICE = (
        "Some Composer reads did not complete in this run; the affected items say why."
    )

    @staticmethod
    def _store_run_with_flag(flag, run_id=pb.RUN_ID_FIRST, **kwargs):
        """Store a completed run whose ``degraded`` field is ``flag``; ``flag`` as the absent
        sentinel stores a run with no such field (a run from before the flag existed)."""
        pb.seed_bot_state()
        record = pb.full_record(run_id, **kwargs)
        if flag is not _NO_FLAG:
            record["degraded"] = flag
        pb.store_run(record)
        return record

    def test_a_degraded_run_says_so_once_in_the_run_header_with_the_fixed_text(
        self, client, isolated_db
    ):
        self._store_run_with_flag(True)
        panel = _panel(client)
        notices = panel.find_all("tenure-review-degraded-notice")
        assert len(notices) == 1, "exactly one notice, not one per affected item"
        assert notices[0].text().strip() == self.DEGRADED_NOTICE
        assert panel.text().count(self.DEGRADED_NOTICE) == 1
        header = panel.find("tenure-review-as-of").parent
        assert notices[0].parent is header, "the notice sits in the run header with the as-of day"
        assert len(panel.find_all("tenure-review-entry")) > 0, "the entries are still listed"

    @pytest.mark.parametrize(
        "flag",
        [
            False,
            _NO_FLAG,
            None,
            "true",
            1,
            0,
            [],
            {},
            f"<b>{_FLAG_MARKER}</b>",
        ],
        ids=[
            "false",
            "no flag stored",
            "null",
            "the text true",
            "one",
            "zero",
            "list",
            "mapping",
            "markup",
        ],
    )
    def test_a_run_whose_flag_is_not_the_boolean_true_shows_no_notice_and_prints_no_stored_value(
        self, client, isolated_db, flag
    ):
        self._store_run_with_flag(flag)
        body = _page(client)
        panel = pb.panel_of(body)
        assert panel.find("tenure-review-as-of") is not None, "premise: the run header is rendered"
        assert panel.find("tenure-review-degraded-notice") is None
        assert self.DEGRADED_NOTICE not in body
        assert _FLAG_MARKER not in body, "a stored value is never printed, escaped or not"

    @pytest.mark.parametrize(
        "older, newer, shown",
        [(True, False, False), (False, True, True), (True, _NO_FLAG, False)],
        ids=["older degraded", "newer degraded", "newer has no flag"],
    )
    def test_only_the_newest_run_decides_whether_the_notice_is_shown(
        self, client, isolated_db, older, newer, shown
    ):
        self._store_run_with_flag(older, pb.RUN_ID_FIRST, completed_at="2026-12-31T03:50:00+00:00")
        self._store_run_with_flag(
            newer, pb.RUN_ID_SECOND, trigger="on_demand", completed_at="2026-12-31T21:10:00+00:00"
        )
        panel = _panel(client)
        assert (panel.find("tenure-review-degraded-notice") is not None) is shown

    def test_the_notice_is_styled_with_design_tokens_only(self, client, isolated_db):
        self._store_run_with_flag(True)
        notice = _panel(client).find("tenure-review-degraded-notice")
        assert notice is not None
        assert "style" not in notice.attrs, "no inline style"
        classes = notice.classes()
        assert classes, "the notice carries a class that has a rule"
        css = TestDesignSystem._css_of_exact_classes(TestDesignSystem._css_rules(), classes)
        assert "var(--studio-" in css, "the notice's rule uses the design tokens"
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css), "raw hex in the notice's rule"
        assert not re.search(r"\b(rgb|rgba|hsl|hsla)\(", css), "raw color in the notice's rule"

    @pytest.mark.parametrize("flag", [True, False, _NO_FLAG], ids=["degraded", "clean", "no flag"])
    def test_the_api_serves_the_stored_flag_as_it_is(self, client, isolated_db, flag):
        self._store_run_with_flag(flag)
        response = client.get("/api/retirement-recommendations")
        assert response.status_code == 200
        run = pb.strict_json(response)["review_run"]
        assert run is not None, "premise: the stored run is served"
        if flag is _NO_FLAG:
            assert "degraded" not in run, "no flag is made up for a run that never had one"
        else:
            assert run["degraded"] is flag


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
                        assert _shows(shown, _printed_figure(fig_name, fig["value"])), (
                            f"{where} / {fig_name}"
                        )
                        if fig_name in _DRAWDOWN_GAPS:
                            assert _gap_words(fig_name, fig["value"]) in text, (
                                f"{where} / {fig_name}: the gap does not say which leg is shallower"
                            )
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


class TestHostileEntryFieldsAreEscaped:
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
        token = state.try_start("nightly")
        assert token
        assert _panel(client).find("tenure-review-running-notice") is not None
        state.finish_success(token)
        assert _panel(client).find("tenure-review-running-notice") is None

    def test_a_failed_attempt_is_stated_with_its_reason_and_time_next_to_the_previous_run(
        self, client, isolated_db
    ):
        record = _stored_full_run()
        state = tenure_review.REVIEW_RUN_STATE
        token = state.try_start("nightly")
        assert token
        state.finish_failure(
            token, "Composer statistics unavailable", datetime(2026, 12, 31, 15, 0, tzinfo=UTC)
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
        assert "Run tenure review" in button.text()
        assert "disabled" not in button.attrs
        message = panel.find("tenure-review-run-message")
        assert message is not None
        assert message.text() == "", "no message is shown before a request"

    @pytest.mark.parametrize("a_review_is_stored", [True, False], ids=["review", "no review yet"])
    def test_the_message_slot_announces_what_it_says_without_interrupting(
        self, client, isolated_db, a_review_is_stored
    ):
        """The slot is the only feedback for the button, and the client writes it after the
        page loaded: a status region that is polite, so a screen reader reads it out once the
        reader is idle and never over what is being read."""
        if a_review_is_stored:
            _stored_full_run()
        panel = _panel(client)
        message = panel.find("tenure-review-run-message")
        assert message is not None
        assert message.attrs.get("role") == "status"
        assert message.attrs.get("aria-live") == "polite"
        button = panel.find("tenure-review-run-btn")
        assert "aria-live" not in button.attrs and "role" not in button.attrs, (
            "the live region is the message slot, not the button"
        )

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
        token = state.try_start("nightly")
        assert token
        state.finish_failure(token, "Composer statistics unavailable", datetime.now(UTC))
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
                # an unavailable redundancy item that shows a pair still prints its figures
                if item["status"] != "available" and not item.get("pair"):
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
        record = _store_record("quiet")
        panel = _panel(client)
        for stored in record["entries"]:
            item = stored["evidence"]["redundancy"]
            assert item["status"] == "available" and item["pair"] is None
            block = pb.entry_node(panel, stored["symphony_id"]).find(
                "tenure-review-evidence-redundancy"
            )
            assert item["summary"] in block.text(), stored["display_name"]
            assert "no redundant sibling" in block.text(), stored["display_name"]
            assert "unavailable" not in block.text().lower(), (
                "a symphony that was checked and has no redundant sibling is not 'unavailable'"
            )

    def test_each_eligible_symphony_of_the_small_run_states_what_its_check_found(
        self, client, isolated_db
    ):
        """One eligible symphony of the small run has a pair on too few days, which is shown
        and never denied; the other was compared on too few days to claim it has no
        redundant sibling. Both items are unavailable and carry their stored reason, and
        neither says there is no redundant sibling."""
        pb.seed_bot_state(include_small=True)
        record = pb.small_record()
        pb.store_run(record)
        panel = _panel(client)
        unproven = shown = 0
        for key in ("first", "second"):
            stored = pb.entry_of(record, pb.SMALL_IDS[key])
            item = stored["evidence"]["redundancy"]
            block = pb.entry_node(panel, stored["symphony_id"]).find(
                "tenure-review-evidence-redundancy"
            )
            assert "no redundant sibling" not in block.text(), key
            assert item["status"] == "unavailable", key
            assert item["reason"] in block.text(), key
            if item["pair"] is None:
                unproven += 1
            else:
                assert item.get("summary") is None, key
                assert item["pair"]["sibling_name"] in block.text(), key
                shown += 1
        assert (unproven, shown) == (1, 1), "the small record must hold one of each"

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
                raw_response='["not", "a", "record"]',
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


# ===========================================================================
# What the panel shows of the review compute's later behaviour: the number of
# symphonies a composite was normalized over, a pair shown on too few days or with an
# undefined statistic (it decides nothing, so the item is missing evidence), the
# dates a series and a tenure begin, and a withheld symphony that no longer lists
# itself as left out.
# ===========================================================================

_RECORD_BUILDERS = {
    "full": pb.full_record,
    "small": pb.small_record,
    "edge": pb.edge_record,
    "quiet": pb.quiet_record,
    "gap": pb.gap_record,
    "lone": pb.lone_record,
    "solo": pb.solo_record,
    "apart": pb.apart_record,
    "shadow_start": pb.shadow_start_record,
    **{f"state:{kind}": functools.partial(pb.state_record, kind) for kind in pb.STATE_ROSTERS},
}


def _seed_roster(which: str) -> None:
    if which == "full":
        pb.seed_bot_state()
    elif which == "small":
        pb.seed_bot_state(include_small=True)
    elif which == "quiet":
        pb.seed_quiet_bot_state()
    elif which == "edge":
        pb.seed_edge_bot_state()
    else:
        pb.seed_roster_state(_RECORD_BUILDERS[which]())


def _store_record(which: str, **kwargs):
    _seed_roster(which)
    record = _RECORD_BUILDERS[which](**kwargs)
    pb.store_run(record)
    return record


def _population_problems(block: pb.Node, item: dict) -> list[str]:
    """The element that shows the composite score must also state how many symphonies the
    score was normalized over: the element itself, or its parent when that parent is a
    figure line and not a wrapper of the whole block. The count is read as a whole number
    with the score and its unit taken out, and the word for symphonies must stand beside it."""
    figure = item["figures"]["composite"]
    forms = _two_decimal_forms(figure["value"])
    population = item["population_size"]
    leaves = [n for form in forms for n in pb.leaf_nodes_containing(block, form)]
    if not leaves:
        return ["the composite score is not shown at all"]
    candidates = []
    for leaf in leaves:
        candidates.append(leaf)
        parent = leaf.parent
        if parent is not None and parent is not block and item["subject"] not in parent.text():
            candidates.append(parent)
    for candidate in candidates:
        text = candidate.text()
        for form in forms:
            text = text.replace(form, " ")
        text = text.replace(figure["unit"], " ")
        counted = re.search(rf"(?<![\d.\-]){population}(?![\d.\-])", text)
        if counted and "symphon" in text.lower():
            return []
    return [f"no element beside the score states {population} symphonies"]


class TestTheCompositeScoreStatesHowManySymphoniesItWasNormalizedOver:
    def test_every_available_composite_item_states_its_stored_population_beside_its_score(
        self, client, isolated_db
    ):
        populations = set()
        checked = 0
        for which in ("full", "small", "edge"):
            record = _store_record(which)
            panel = _panel(client)
            for stored in record["entries"]:
                item = stored["evidence"]["composite"]
                if item["status"] != "available":
                    continue
                block = pb.entry_node(panel, stored["symphony_id"]).find(
                    "tenure-review-evidence-composite"
                )
                where = f"{which} / {stored['display_name']}"
                assert _population_problems(block, item) == [], where
                populations.add(item["population_size"])
                checked += 1
        assert checked >= 6, "the records must exercise many composite items"
        assert len(populations) >= 2, (
            "the records must hold different populations, so a constant cannot satisfy the check"
        )

    def test_the_count_is_read_from_the_stored_item_and_not_derived_from_the_page(
        self, client, isolated_db
    ):
        pb.seed_bot_state()
        record = pb.full_record()
        scored = [
            e for e in record["entries"] if e["evidence"]["composite"]["status"] == "available"
        ]
        assert len(scored) >= 2
        edited, untouched = scored[0], scored[1]
        stored_before = untouched["evidence"]["composite"]["population_size"]
        edited["evidence"]["composite"]["population_size"] = 9
        assert stored_before != 9 and len(record["entries"]) != 9
        pb.store_run(record)
        panel = _panel(client)
        for stored in (edited, untouched):
            block = pb.entry_node(panel, stored["symphony_id"]).find(
                "tenure-review-evidence-composite"
            )
            assert _population_problems(block, stored["evidence"]["composite"]) == [], stored[
                "display_name"
            ]

    def test_the_check_rejects_a_block_that_shows_the_score_without_the_count(
        self, client, isolated_db
    ):
        """Mutation proof: take the count out of the parsed block and the same check reports it."""
        record = _store_record("small")
        stored = pb.entry_of(record, pb.SMALL_IDS["first"])
        item = stored["evidence"]["composite"]
        block = pb.entry_node(_panel(client), stored["symphony_id"]).find(
            "tenure-review-evidence-composite"
        )
        assert _population_problems(block, item) == []
        for leaf in pb.leaf_nodes_containing(block, "symphon"):
            leaf.children[:] = [c for c in leaf.children if not isinstance(c, str)] + [
                _two_decimal_forms(item["figures"]["composite"]["value"])[0]
            ]
        assert _population_problems(block, item), "a block without the count went unnoticed"

    def test_an_unavailable_composite_item_states_no_count_and_never_prints_none(
        self, client, isolated_db
    ):
        checked = 0
        for which in ("full", "edge"):
            record = _store_record(which)
            panel = _panel(client)
            for stored in record["entries"]:
                item = stored["evidence"]["composite"]
                if item["status"] == "available":
                    continue
                block = pb.entry_node(panel, stored["symphony_id"]).find(
                    "tenure-review-evidence-composite"
                )
                assert not re.search(r"\bNone\b|\bnull\b", block.text()), stored["display_name"]
                assert item["reason"] in block.text(), stored["display_name"]
                checked += 1
        assert checked >= 4

    @pytest.mark.parametrize(
        "poison",
        [None, '<i data-poison="1">many</i>', [3], {"n": 3}],
        ids=["null", "markup text", "list", "dict"],
    )
    def test_a_population_the_record_cannot_give_as_a_number_never_breaks_the_entry_or_leaks(
        self, client, isolated_db, poison
    ):
        pb.seed_bot_state()
        record = pb.full_record()
        victim = pb.entry_with_verdict(record, "keep")
        victim["evidence"]["composite"]["population_size"] = poison
        pb.store_run(record)
        resp = client.get("/ai-advisor")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert '<i data-poison="1">' not in body, "stored text reached the page unescaped"
        panel = pb.panel_of(body)
        node = pb.entry_node(panel, victim["symphony_id"])
        for name in _PANEL_ITEMS:
            assert node.find(f"tenure-review-evidence-{name}") is not None, name
        block = node.find("tenure-review-evidence-composite")
        assert not re.search(r"\bNone\b|\bnull\b", block.text()), block.text()
        assert _shows(block, victim["evidence"]["composite"]["figures"]["composite"]["value"])


class TestPairsThatCouldNotDecideAreShownAndAreMissingEvidence:
    """Cedar's pair has too few aligned days, and the twins' correlation is exactly one so
    the interval is undefined: each pair is shown with its gate results, and the item is
    listed as missing evidence because nothing can be decided from it."""

    _CASES = [
        ("small", pb.SMALL_IDS["first"]),
        ("edge", pb.EDGE_IDS["twin_one"]),
        ("edge", pb.EDGE_IDS["twin_two"]),
    ]
    _IDS = ["short pair", "undefined interval, first twin", "undefined interval, second twin"]

    @pytest.fixture(params=_CASES, ids=_IDS)
    def shown(self, request, client, isolated_db):
        which, symphony_id = request.param
        record = _store_record(which)
        stored = pb.entry_of(record, symphony_id)
        item = stored["evidence"]["redundancy"]
        assert item["status"] == "unavailable" and item["pair"], "fixture sanity: a pair is shown"
        assert item["pair"]["uncertainty_gate"]["state"] in ("insufficient data", "undefined")
        assert "redundancy" in stored["missing_items"], "fixture sanity: it decides nothing"
        node = pb.entry_node(_panel(client), symphony_id)
        return stored, item, node

    def test_the_pair_is_shown_with_its_sibling_correlation_days_and_both_gate_texts(self, shown):
        stored, item, node = shown
        pair = item["pair"]
        block = node.find("tenure-review-evidence-redundancy")
        assert pair["sibling_name"] in block.text()
        assert _shows(block, pair["correlation"]["value"])
        assert str(pair["n_obs"]) in block.text()
        assert _gate_problems(block, pair) == []
        assert "no redundant sibling" not in block.text()

    def test_the_gate_that_could_not_decide_is_not_worded_as_a_pass(self, shown):
        stored, item, node = shown
        text = node.find("tenure-review-evidence-redundancy").text().lower()
        pair = item["pair"]
        assert pair["uncertainty_gate"]["text"].lower() in text
        assert pair["structural_gate"]["text"].lower() in text
        assert not re.search(r"uncertainty gate\W*passed", text)
        assert not re.search(r"structural redundancy gate\W*passed", text)

    def test_the_null_figures_of_the_pair_keep_their_stored_reasons(self, shown):
        stored, item, node = shown
        block = node.find("tenure-review-evidence-redundancy")
        nulls = _null_figures(item)
        assert nulls, "fixture sanity: this pair holds null figures"
        for fig_name, reason in nulls:
            lines = pb.leaf_nodes_containing(block, fig_name.replace("_", " "))
            assert any(reason in line.text() for line in lines), (fig_name, reason)
            assert not any(
                _DECIMAL.search(line.text()) for line in lines if reason in line.text()
            ), fig_name

    def test_the_entry_lists_redundancy_as_missing_and_offers_no_retirement(self, shown):
        stored, item, node = shown
        missing = node.find("tenure-review-missing")
        assert missing is not None, "an item that decides nothing must be listed as missing"
        assert "redundancy" in missing.text().lower()
        chip = node.find("tenure-review-verdict-chip")
        assert chip.text() == stored["verdict_text"]
        assert stored["verdict"] != "retire_candidate"
        assert "tenure-verdict-chip--retire_candidate" not in chip.classes()
        approve, reject = _lifecycle_buttons(node)
        assert approve == [] and reject == []


class TestMissingItemsAreListedOnEveryRecord:
    @pytest.mark.parametrize("which", ["small", "edge", "gap", "lone", "apart"])
    def test_each_entry_lists_exactly_the_items_the_record_calls_missing(
        self, client, isolated_db, which
    ):
        record = _store_record(which)
        panel = _panel(client)
        listed = 0
        for stored in record["entries"]:
            node = pb.entry_node(panel, stored["symphony_id"])
            missing = node.find("tenure-review-missing")
            where = stored["display_name"]
            if not stored["missing_items"]:
                assert missing is None, where
                continue
            assert missing is not None, where
            text = missing.text().lower().replace("_", " ")
            # a listed item carries its reason, which may itself name another item
            for listed_name in stored["missing_items"]:
                reason = stored["evidence"][listed_name].get("reason")
                if isinstance(reason, str) and reason:
                    text = text.replace(reason.lower(), " ")
            for name in (
                "performance",
                "composite",
                "redundancy",
                "backtest",
                "marginal",
                "long vix",
            ):
                stored_name = name.replace(" ", "_")
                assert (name in text) == (stored_name in stored["missing_items"]), (where, name)
            listed += 1
        assert listed >= 2

    def test_an_item_that_says_no_redundant_sibling_is_not_listed_as_missing(
        self, client, isolated_db
    ):
        record = _store_record("quiet")
        panel = _panel(client)
        for stored in record["entries"]:
            item = stored["evidence"]["redundancy"]
            assert item["status"] == "available" and item["pair"] is None
            node = pb.entry_node(panel, stored["symphony_id"])
            assert "no redundant sibling" in node.find("tenure-review-evidence-redundancy").text()
            missing = node.find("tenure-review-missing")
            assert missing is not None, "the quiet record still lacks a backtest"
            assert "redundancy" not in missing.text().lower(), stored["display_name"]


class TestTheBasisLineCarriesTheDatesAsTheRecordStatesThem:
    """The Composer basis stores the date of its first value, which it calls the series
    start. The shadow basis stores the date of its first return, which is measured from the
    previous session's close, so its window opens a session earlier; its line says so in
    those words and never calls that date a series start."""

    _DATE = r"(\d{4}-\d{2}-\d{2})"
    _PHRASES = {
        "series begins": re.compile(rf"series begins {_DATE}"),
        "first shadow return": re.compile(rf"first shadow return {_DATE}"),
        "tenure begins": re.compile(rf"tenure begins {_DATE}"),
    }
    _SHADOW_NOTE = "measured from the previous session's close"

    @staticmethod
    def _straight(text: str) -> str:
        return text.replace(chr(0x2019), "'")

    @pytest.mark.parametrize("which", ["full", "small", "edge"])
    def test_each_date_is_the_stored_one_under_the_phrase_of_its_basis_and_an_unknown_one_is_not_printed(
        self, client, isolated_db, which
    ):
        record = _store_record(which)
        panel = _panel(client)
        shadows = 0
        for stored in record["entries"]:
            text = pb.entry_node(panel, stored["symphony_id"]).find("tenure-review-basis").text()
            where = stored["display_name"]
            shadow = stored["basis"]["kind"] == "shadow"
            shadows += shadow
            series = "first shadow return" if shadow else "series begins"
            other = "series begins" if shadow else "first shadow return"
            for phrase, key in ((series, "series_begins"), ("tenure begins", "tenure_begins")):
                found = self._PHRASES[phrase].findall(text)
                expected = stored["basis"][key]
                assert found == ([expected] if expected else []), (where, phrase, found)
            assert self._PHRASES[other].findall(text) == [], (where, other)
        assert shadows == (1 if which == "full" else 0), "the full record holds one shadow entry"

    def test_a_shadow_series_states_its_first_return_and_the_session_close_it_is_measured_from(
        self, client, isolated_db
    ):
        record = _store_record("full")
        shadow = pb.entry_of(record, pb.FULL_IDS["shadow"])
        window_first = shadow["evidence"]["performance"]["window"]["first"]
        began = shadow["basis"]["series_begins"]
        assert window_first != began, "fixture sanity: the window opens a session before the series"
        text = self._straight(
            pb.entry_node(_panel(client), shadow["symphony_id"]).find("tenure-review-basis").text()
        )
        assert f"first shadow return {began}" in text
        assert f"first shadow return {window_first}" not in text
        assert self._SHADOW_NOTE in text
        assert "series begins" not in text

    def test_a_composer_series_keeps_its_series_start_wording_and_adds_no_shadow_note(
        self, client, isolated_db
    ):
        record = _store_record("full")
        panel = _panel(client)
        checked = 0
        for stored in record["entries"]:
            if stored["basis"]["kind"] != "composer" or not stored["basis"]["series_begins"]:
                continue
            text = self._straight(
                pb.entry_node(panel, stored["symphony_id"]).find("tenure-review-basis").text()
            )
            assert f"series begins {stored['basis']['series_begins']}" in text, stored[
                "display_name"
            ]
            assert "shadow return" not in text and self._SHADOW_NOTE not in text, stored[
                "display_name"
            ]
            checked += 1
        assert checked >= 4


class TestNoEntryListsItselfAsLeftOut:
    @pytest.mark.parametrize("which", ["full", "small"])
    def test_a_leaf_that_names_the_entry_never_carries_a_left_out_reason(
        self, client, isolated_db, which
    ):
        record = _store_record(which)
        panel = _panel(client)
        reasons = {
            x["reason"] for e in record["entries"] for x in e["evidence"]["marginal"]["excluded"]
        }
        assert reasons, "fixture sanity: some symphony is left out in this record"
        listed = 0
        for stored in record["entries"]:
            block = pb.entry_node(panel, stored["symphony_id"]).find(
                "tenure-review-evidence-marginal"
            )
            own = pb.leaf_nodes_containing(block, stored["display_name"])
            for leaf in own:
                assert not any(reason in leaf.text() for reason in reasons), (
                    f"{stored['display_name']} lists itself as left out: {leaf.text()!r}"
                )
            for other in stored["evidence"]["marginal"]["excluded"]:
                assert other["display_name"] != stored["display_name"]
                lines = pb.leaf_nodes_containing(block, other["display_name"])
                assert any(other["reason"] in line.text() for line in lines), other
                listed += 1
        assert listed >= 1

    def test_a_withheld_symphony_lists_the_other_withheld_ones_and_not_itself(
        self, client, isolated_db
    ):
        record = _store_record("full")
        young = pb.entry_of(record, pb.FULL_IDS["young"])
        names = {x["display_name"] for x in young["evidence"]["marginal"]["excluded"]}
        assert names == {pb.FULL_NAMES["dateless"], pb.FULL_NAMES["silent"]}
        block = pb.entry_node(_panel(client), young["symphony_id"]).find(
            "tenure-review-evidence-marginal"
        )
        for name in names:
            assert pb.leaf_nodes_containing(block, name), name


# ===========================================================================
# A stored field the panel prints is never shown as Python's None, nor left as an
# empty slot, whatever its type: each case stores a run whose one field is missing,
# null or of the wrong type, renders the page, and requires "not recorded" in the
# place that field is printed, no trace of the poison anywhere in the panel, and
# every other entry (and every other block of that entry) to read as it does when
# nothing is poisoned. The poison text is a marker no real value contains.
# ===========================================================================

_NOT_RECORDED = "not recorded"
_MARK = "zzmarkerzz"
_BLANK = "   "  # text of only spaces, which counts as missing
_GONE = object()  # the key is deleted from the stored record
_TRUE = r"\bTrue\b"
_TWELVE_POINT_FIVE = r"(?<![\d.])12\.5(?!\d)"
_TWO_POINT_FIVE = r"(?<![\d.])2\.5(?!\d)"
_ET_STAMP = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} ET\b")

_VICTIMS = {
    "watch": ("full", lambda r: pb.entry_with_verdict(r, "watch")),
    "keep": ("full", lambda r: pb.entry_with_verdict(r, "keep")),
    "steady": ("edge", lambda r: pb.entry_of(r, pb.EDGE_IDS["steady"])),
    "twin": ("edge", lambda r: pb.entry_of(r, pb.EDGE_IDS["twin_one"])),
    "retire": ("full", lambda r: pb.entry_with_verdict(r, "retire_candidate")),
    "short pair": ("small", lambda r: pb.entry_of(r, pb.SMALL_IDS["first"])),
}


@dataclass(frozen=True)
class _Poison:
    victim: str
    edits: tuple  # of (dotted path inside the victim entry, value or _GONE)
    scope: tuple  # where the field is printed; see _scoped
    not_recorded: bool = True  # an optional field only has to leave no trace
    absent: tuple = ()  # patterns the scoped text must not match
    shows: str | None = None  # path of a float the scoped text must still show
    markers: tuple = (_MARK,)  # patterns that must not match anywhere in the panel
    optional: bool = False  # a line the page may drop instead of wording
    keeps: tuple = ()  # paths of stored texts the scoped text must still carry


_BASIS = ("basis",)
_CHIP = ("chip",)


def _block(name):
    return ("block", name)


def _line(name, needle, mode="starts"):
    return ("line", name, needle, mode)


def _header(testid):
    return ("header", testid)


def _case(case_id, victim, edits, scope, **kwargs):
    return pytest.param(_Poison(victim, tuple(edits), scope, **kwargs), id=case_id)


def _get(root, path):
    node = root
    for key in path.split("."):
        node = node[int(key)] if isinstance(node, list) else node[key]
    return node


def _edit(root, path, value):
    *parents, leaf = path.split(".")
    node = _get(root, ".".join(parents)) if parents else root
    if isinstance(node, list):
        if int(leaf) == len(node):  # one element after the last: the element is added
            node.append(copy.deepcopy(value))
        else:
            node[int(leaf)] = value
    elif value is _GONE:
        node.pop(leaf, None)
    else:
        node[leaf] = copy.deepcopy(value)


def _texts_of(panel) -> dict:
    """What the unpoisoned panel reads like: per entry the whole entry and each block,
    plus the run header."""
    texts = {"[header]": panel.find("tenure-review-as-of").parent.everything()}
    for node in panel.find_all("tenure-review-entry"):
        texts[node.attrs["data-symphony-id"]] = {
            "entry": node.everything(),
            **{n: node.find(f"tenure-review-evidence-{n}").everything() for n in _PANEL_ITEMS},
        }
    return texts


_PRISTINE_TEXTS: dict[str, dict] = {}  # test-only memo of a deterministic render, per record


def _pristine(client, which: str) -> dict:
    if which not in _PRISTINE_TEXTS:
        _store_record(which)
        _PRISTINE_TEXTS[which] = _texts_of(_panel(client))
    return _PRISTINE_TEXTS[which]


def _render_edited(client, which: str, edit):
    """Render the page for the record of ``which`` after ``edit`` changed it; returns the
    edited record, the unpoisoned texts and the panel."""
    _seed_roster(which)
    pristine = _pristine(client, which)
    record = _RECORD_BUILDERS[which](run_id=pb.RUN_ID_SECOND)
    edit(record)
    pb.store_run(record)
    return record, pristine, _panel(client)


def _scoped(panel, victim_id, scope):
    kind = scope[0]
    if kind == "header":
        return panel.find(scope[1])
    node = pb.entry_node(panel, victim_id)
    if kind == "basis":
        return node.find("tenure-review-basis")
    if kind == "chip":
        return node.find("tenure-review-verdict-chip")
    if kind == "testid":
        return node.find(scope[1])
    block = node.find(f"tenure-review-evidence-{scope[1]}")
    if kind == "block":
        return block
    needle, mode = scope[2], scope[3]
    leaves = pb.leaf_nodes_containing(block, needle)
    if mode == "starts":
        leaves = [leaf for leaf in leaves if leaf.text().lower().startswith(needle.lower())]
    return leaves[0] if leaves else None


def _assert_others_unchanged(panel, pristine: dict, victim_id: str, scope: tuple) -> None:
    for sid, texts in pristine.items():
        if sid == "[header]":
            if scope[0] != "header":
                assert panel.find("tenure-review-as-of").parent.everything() == texts
            continue
        node = pb.entry_node(panel, sid)
        if sid != victim_id:
            assert node.everything() == texts["entry"], f"another entry changed: {sid}"
            continue
        for name in _PANEL_ITEMS:
            if scope[0] in ("block", "line") and scope[1] == name:
                continue
            assert node.find(f"tenure-review-evidence-{name}").everything() == texts[name], (
                f"another block of the entry changed: {name}"
            )


_LIST = [_MARK]
_DICT = {"k": _MARK}
_SHOWN_FIGURE = "evidence.performance.figures.total_return.value"
_TRUNCATED_TWELVE = r"(?<![\d.-])12(?![\d.-])"  # 12.5 cut to 12, a date's "-12-" excluded
_ELEMENT_MARKERS = (_MARK, "987654", _TRUE)
_EXPLANATION = "Plain stored explanation text."
# values an element of a stored list, or a stored text, can wrongly hold
_BAD_ELEMENTS = [
    ("null", None),
    ("a list", _LIST),
    ("a dict", _DICT),
    ("a number", 987654),
    ("a boolean", True),
]
# (figure of the pair, how it is wrong, the value stored in its place)
_PAIR_FIGURE_FAULTS = [
    ("correlation", "missing", _GONE),
    ("correlation", "null", None),
    ("correlation", "a list", _LIST),
    ("correlation", "text", _MARK),
    ("correlation", "a number", 987654),
    ("ci_lower", "missing", _GONE),
    ("ci_lower", "null", None),
    ("ci_upper", "null", None),
    ("ci_upper", "a dict", _DICT),
    ("stressed_correlation", "missing", _GONE),
    ("stressed_correlation", "a boolean", True),
    ("holdings_overlap", "missing", _GONE),
    ("holdings_overlap", "null", None),
    ("holdings_overlap", "a list", _LIST),
]
# (item, figure, how it is wrong, the value stored in its place)
_ITEM_FIGURE_FAULTS = [
    ("performance", "sharpe", "null", None),
    ("performance", "sharpe", "a list", _LIST),
    ("performance", "sharpe", "a number", 987654),
    ("performance", "sharpe", "text", _MARK),
    ("marginal", "with_total_return", "null", None),
    ("backtest", "total_return_gap", "a dict", _DICT),
]
_FIGURES_FAULTS = [
    ("null", None),
    ("a list", _LIST),
    ("text", _MARK),
    ("a number", 987654),
    ("missing", _GONE),
]
_POISONS = [
    # the basis line
    _case("basis is a list", "watch", [("basis", _LIST)], _BASIS),
    _case("basis label missing", "watch", [("basis.label", _GONE)], _BASIS),
    _case("basis label null", "watch", [("basis.label", None)], _BASIS),
    _case("basis label a list", "watch", [("basis.label", _LIST)], _BASIS),
    _case(
        "basis fallback reason a list",
        "watch",
        [("basis.fallback_reason", _LIST)],
        _BASIS,
        not_recorded=False,
    ),
    _case(
        "basis series start a number",
        "watch",
        [("basis.series_begins", 987654)],
        _BASIS,
        not_recorded=False,
        markers=(_MARK, "987654"),
    ),
    _case(
        "basis tenure start a list",
        "watch",
        [("basis.tenure_begins", _LIST)],
        _BASIS,
        not_recorded=False,
    ),
    # the name and the lines under it
    _case(
        "display name a list",
        "watch",
        [("display_name", _LIST)],
        ("testid", "tenure-review-name"),
        not_recorded=False,
    ),
    *[
        _case(
            f"display name {label}",
            "watch",
            [("display_name", value)],
            ("testid", "tenure-review-name"),
            not_recorded=False,
            keeps=("symphony_id",),
            absent=(_NOT_RECORDED,),
        )
        for label, value in (("missing", _GONE), ("null", None), ("blank", _BLANK))
    ],
    # a tenure that is null or of the wrong type reads not recorded on its own line; only a
    # key that is absent may drop the line
    _case(
        "tenure a boolean",
        "watch",
        [("tenure_trading_days", True)],
        ("testid", "tenure-review-tenure"),
        absent=(r"\bTrue\b", r"\b1 trading"),
        markers=(_MARK, _TRUE),
    ),
    _case(
        "tenure not a whole number",
        "watch",
        [("tenure_trading_days", 12.5)],
        ("testid", "tenure-review-tenure"),
        absent=(r"\b12 trading", _TWELVE_POINT_FIVE),
        markers=(_MARK, _TWELVE_POINT_FIVE),
    ),
    _case(
        "tenure missing",
        "watch",
        [("tenure_trading_days", _GONE)],
        ("testid", "tenure-review-tenure"),
        optional=True,
    ),
    *[
        _case(
            f"tenure {label}",
            "watch",
            [("tenure_trading_days", value)],
            ("testid", "tenure-review-tenure"),
            markers=_ELEMENT_MARKERS,
        )
        for label, value in (
            ("null", None),
            ("text", _MARK),
            ("a list", _LIST),
            ("a dict", _DICT),
            ("blank", _BLANK),
        )
    ],
    # the eligibility sentence
    _case(
        "eligibility missing",
        "watch",
        [("eligibility_text", _GONE)],
        ("testid", "tenure-review-eligibility"),
        optional=True,
    ),
    *[
        _case(
            f"eligibility {label}",
            "watch",
            [("eligibility_text", value)],
            ("testid", "tenure-review-eligibility"),
            markers=_ELEMENT_MARKERS,
        )
        for label, value in (
            ("null", None),
            ("a number", 987654),
            ("a list", _LIST),
            ("a dict", _DICT),
            ("a boolean", True),
            ("blank", _BLANK),
        )
    ],
    # the reasons and the missing items when the whole value is null or not a list
    _case(
        "verdict reasons missing",
        "watch",
        [("verdict_reasons", _GONE)],
        ("testid", "tenure-review-verdict-reasons"),
        optional=True,
    ),
    *[
        _case(
            f"verdict reasons {label}",
            "watch",
            [("verdict_reasons", value)],
            ("testid", "tenure-review-verdict-reasons"),
            markers=_ELEMENT_MARKERS,
        )
        for label, value in (
            ("null", None),
            ("text", _MARK),
            ("a number", 987654),
            ("a dict", _DICT),
            ("a boolean", True),
            ("blank", _BLANK),
        )
    ],
    _case(
        "missing items missing",
        "watch",
        [("missing_items", _GONE)],
        ("testid", "tenure-review-missing"),
        optional=True,
    ),
    *[
        _case(
            f"missing items {label}",
            "watch",
            [("missing_items", value)],
            ("testid", "tenure-review-missing"),
            markers=_ELEMENT_MARKERS,
        )
        for label, value in (
            ("null", None),
            ("text", _MARK),
            ("a number", 987654),
            ("a dict", _DICT),
            ("a boolean", True),
            ("blank", _BLANK),
        )
    ],
    # the verdict chip
    _case("verdict text missing", "watch", [("verdict_text", _GONE)], _CHIP),
    _case("verdict text null", "watch", [("verdict_text", None)], _CHIP),
    _case("verdict text a list", "watch", [("verdict_text", _LIST)], _CHIP),
    _case("verdict text blank", "watch", [("verdict_text", _BLANK)], _CHIP),
    # the run header
    _case(
        "as-of day missing",
        "watch",
        [("__run__.as_of_trading_day", _GONE)],
        _header("tenure-review-as-of"),
    ),
    _case(
        "as-of day null",
        "watch",
        [("__run__.as_of_trading_day", None)],
        _header("tenure-review-as-of"),
    ),
    _case(
        "as-of day a list",
        "watch",
        [("__run__.as_of_trading_day", _LIST)],
        _header("tenure-review-as-of"),
    ),
    _case(
        "threshold missing",
        "watch",
        [("__run__.threshold_in_effect", _GONE)],
        _header("tenure-review-threshold"),
    ),
    _case(
        "threshold null",
        "watch",
        [("__run__.threshold_in_effect", None)],
        _header("tenure-review-threshold"),
    ),
    _case(
        "threshold text",
        "watch",
        [("__run__.threshold_in_effect", _MARK)],
        _header("tenure-review-threshold"),
    ),
    _case(
        "threshold a boolean",
        "watch",
        [("__run__.threshold_in_effect", True)],
        _header("tenure-review-threshold"),
        absent=(r"\bTrue\b",),
        markers=(_MARK, _TRUE),
    ),
    _case(
        "threshold not a whole number",
        "watch",
        [("__run__.threshold_in_effect", 12.5)],
        _header("tenure-review-threshold"),
        markers=(_MARK, _TWELVE_POINT_FIVE),
    ),
    _case(
        "threshold note a list",
        "watch",
        [("__run__.threshold_note", _LIST)],
        _header("tenure-review-threshold"),
        not_recorded=False,
    ),
    # an evidence item that is not a record
    _case("item is a list", "watch", [("evidence.performance", _LIST)], _block("performance")),
    _case(
        "item label a list",
        "watch",
        [("evidence.performance.label", _LIST)],
        _block("performance"),
        not_recorded=False,
    ),
    _case(
        "item label null",
        "watch",
        [("evidence.performance.label", None)],
        _block("performance"),
        not_recorded=False,
    ),
    # subject and source of an available item
    _case(
        "subject missing",
        "watch",
        [("evidence.performance.subject", _GONE)],
        _line("performance", "subject"),
    ),
    _case(
        "subject null",
        "watch",
        [("evidence.performance.subject", None)],
        _line("performance", "subject"),
    ),
    _case(
        "subject a list",
        "watch",
        [("evidence.performance.subject", _LIST)],
        _line("performance", "subject"),
    ),
    _case(
        "source missing",
        "watch",
        [("evidence.performance.source", _GONE)],
        _line("performance", "source"),
    ),
    _case(
        "source null",
        "watch",
        [("evidence.performance.source", None)],
        _line("performance", "source"),
    ),
    _case(
        "source a list",
        "watch",
        [("evidence.performance.source", _LIST)],
        _line("performance", "source"),
    ),
    # the reason of an item that is unavailable or not applicable
    _case(
        "unavailable reason missing",
        "watch",
        [("evidence.long_vix.reason", _GONE)],
        _line("long_vix", "unavailable"),
    ),
    _case(
        "unavailable reason null",
        "watch",
        [("evidence.long_vix.reason", None)],
        _line("long_vix", "unavailable"),
    ),
    _case(
        "unavailable reason a list",
        "watch",
        [("evidence.long_vix.reason", _LIST)],
        _line("long_vix", "unavailable"),
    ),
    _case(
        "not applicable reason null",
        "watch",
        [("evidence.long_vix.status", "not_applicable"), ("evidence.long_vix.reason", None)],
        _line("long_vix", "not applicable"),
    ),
    # the window of an item
    _case(
        "window start missing",
        "watch",
        [("evidence.performance.window.first", _GONE)],
        _line("performance", "window"),
    ),
    _case(
        "window start null",
        "watch",
        [("evidence.performance.window.first", None)],
        _line("performance", "window"),
    ),
    _case(
        "window start a list",
        "watch",
        [("evidence.performance.window.first", _LIST)],
        _line("performance", "window"),
    ),
    _case(
        "window end missing",
        "watch",
        [("evidence.performance.window.last", _GONE)],
        _line("performance", "window"),
    ),
    _case(
        "window days null",
        "watch",
        [("evidence.performance.window.days", None)],
        _line("performance", "window"),
    ),
    _case(
        "window days text",
        "watch",
        [("evidence.performance.window.days", _MARK)],
        _line("performance", "window"),
    ),
    _case(
        "window sessions missing",
        "watch",
        [("evidence.performance.window.sessions", _GONE)],
        _line("performance", "window"),
    ),
    _case(
        "window sessions null",
        "watch",
        [("evidence.performance.window.sessions", None)],
        _line("performance", "window"),
    ),
    _case(
        "window sessions text",
        "watch",
        [("evidence.performance.window.sessions", _MARK)],
        _line("performance", "window"),
    ),
    _case(
        "window sessions a list",
        "watch",
        [("evidence.performance.window.sessions", _LIST)],
        _line("performance", "window"),
    ),
    # a figure
    _case(
        "figure unit missing",
        "watch",
        [("evidence.performance.figures.total_return.unit", _GONE)],
        _line("performance", "total return"),
        shows=_SHOWN_FIGURE,
    ),
    _case(
        "figure unit null",
        "watch",
        [("evidence.performance.figures.total_return.unit", None)],
        _line("performance", "total return"),
        shows=_SHOWN_FIGURE,
    ),
    _case(
        "figure unit a number",
        "watch",
        [("evidence.performance.figures.total_return.unit", 987654)],
        _line("performance", "total return"),
        shows=_SHOWN_FIGURE,
        markers=(_MARK, "987654"),
    ),
    _case(
        "figure value a boolean",
        "watch",
        [("evidence.performance.figures.sharpe.value", True)],
        _line("performance", "sharpe"),
        absent=(r"\bTrue\b", r"1\.00"),
        markers=(_MARK, _TRUE),
    ),
    _case(
        "figure value not a number",
        "watch",
        [("evidence.performance.figures.sharpe.value", float("nan"))],
        _line("performance", "sharpe"),
        absent=(r"\bnan\b",),
    ),
    _case(
        "figure value infinite",
        "watch",
        [("evidence.performance.figures.sharpe.value", float("inf"))],
        _line("performance", "sharpe"),
        absent=(r"\binf\b",),
    ),
    _case(
        "null figure reason missing",
        "steady",
        [("evidence.performance.figures.calmar.reason", _GONE)],
        _line("performance", "calmar"),
    ),
    _case(
        "null figure reason null",
        "steady",
        [("evidence.performance.figures.calmar.reason", None)],
        _line("performance", "calmar"),
    ),
    _case(
        "pair figure reason missing",
        "twin",
        [("evidence.redundancy.pair.ci_lower.reason", _GONE)],
        _line("redundancy", "ci lower"),
    ),
    # the pair: a sibling name that is missing or blank falls back to the sibling's Composer id
    *[
        _case(
            f"pair sibling {label}",
            "watch",
            [("evidence.redundancy.pair.sibling_name", value)],
            _line("redundancy", "paired with"),
            not_recorded=False,
            keeps=("evidence.redundancy.pair.sibling_id",),
            absent=(_NOT_RECORDED,),
        )
        for label, value in (("missing", _GONE), ("null", None), ("blank", _BLANK))
    ],
    _case(
        "pair sibling a list",
        "watch",
        [("evidence.redundancy.pair.sibling_name", _LIST)],
        _line("redundancy", "paired with"),
        not_recorded=False,
    ),
    *[
        _case(
            f"pair sibling {label}",
            "watch",
            [
                ("evidence.redundancy.pair.sibling_name", value),
                ("evidence.redundancy.pair.sibling_id", id_value),
            ],
            _line("redundancy", "paired with"),
        )
        for label, value, id_value in (
            ("name and id missing", _GONE, _GONE),
            ("name and id blank", _BLANK, _BLANK),
            ("name missing and id null", _GONE, None),
        )
    ],
    _case(
        "aligned days missing",
        "watch",
        [("evidence.redundancy.pair.n_obs", _GONE)],
        _line("redundancy", "aligned days"),
    ),
    _case(
        "aligned days null",
        "watch",
        [("evidence.redundancy.pair.n_obs", None)],
        _line("redundancy", "aligned days"),
    ),
    _case(
        "aligned days a boolean",
        "watch",
        [("evidence.redundancy.pair.n_obs", True)],
        _line("redundancy", "aligned days"),
        absent=(r"\bTrue\b", r"\b1\b"),
        markers=(_MARK, _TRUE),
    ),
    _case(
        "aligned days not a whole number",
        "watch",
        [("evidence.redundancy.pair.n_obs", 12.5)],
        _line("redundancy", "aligned days"),
        markers=(_MARK, _TWELVE_POINT_FIVE),
    ),
    _case(
        "aligned days text",
        "watch",
        [("evidence.redundancy.pair.n_obs", _MARK)],
        _line("redundancy", "aligned days"),
    ),
    _case(
        "uncertainty gate missing",
        "watch",
        [("evidence.redundancy.pair.uncertainty_gate", _GONE)],
        _line("redundancy", "uncertainty gate"),
    ),
    _case(
        "uncertainty gate text instead of a record",
        "watch",
        [("evidence.redundancy.pair.uncertainty_gate", _MARK)],
        _line("redundancy", "uncertainty gate"),
    ),
    _case(
        "uncertainty gate result missing",
        "watch",
        [("evidence.redundancy.pair.uncertainty_gate.text", _GONE)],
        _line("redundancy", "uncertainty gate"),
    ),
    _case(
        "uncertainty gate result a list",
        "watch",
        [("evidence.redundancy.pair.uncertainty_gate.text", _LIST)],
        _line("redundancy", "uncertainty gate"),
    ),
    _case(
        "structural gate missing",
        "watch",
        [("evidence.redundancy.pair.structural_gate", _GONE)],
        _line("redundancy", "structural redundancy gate"),
    ),
    _case(
        "structural gate text instead of a record",
        "watch",
        [("evidence.redundancy.pair.structural_gate", _MARK)],
        _line("redundancy", "structural redundancy gate"),
    ),
    _case(
        "structural gate result null",
        "watch",
        [("evidence.redundancy.pair.structural_gate.text", None)],
        _line("redundancy", "structural redundancy gate"),
    ),
    _case(
        "structural gate result a list",
        "watch",
        [("evidence.redundancy.pair.structural_gate.text", _LIST)],
        _line("redundancy", "structural redundancy gate"),
    ),
    _case(
        "pair note a list",
        "watch",
        [("evidence.redundancy.pair.note", _LIST)],
        _block("redundancy"),
        not_recorded=False,
    ),
    # a left-out symphony
    _case(
        "left-out name missing",
        "watch",
        [("evidence.marginal.excluded.0.display_name", _GONE)],
        _line("marginal", "left out"),
    ),
    _case(
        "left-out name null",
        "watch",
        [("evidence.marginal.excluded.0.display_name", None)],
        _line("marginal", "left out"),
    ),
    _case(
        "left-out name a list",
        "watch",
        [("evidence.marginal.excluded.0.display_name", _LIST)],
        _line("marginal", "left out"),
    ),
    _case(
        "left-out reason missing",
        "watch",
        [("evidence.marginal.excluded.0.reason", _GONE)],
        _line("marginal", "left out"),
    ),
    _case(
        "left-out reason null",
        "watch",
        [("evidence.marginal.excluded.0.reason", None)],
        _line("marginal", "left out"),
    ),
    _case(
        "left-out reason a list",
        "watch",
        [("evidence.marginal.excluded.0.reason", _LIST)],
        _line("marginal", "left out"),
    ),
    # a peer that was not compared
    _case(
        "not-compared name missing",
        "watch",
        [("evidence.redundancy.not_compared.0.display_name", _GONE)],
        _line("redundancy", "bases differ", "has"),
    ),
    _case(
        "not-compared name null",
        "watch",
        [("evidence.redundancy.not_compared.0.display_name", None)],
        _line("redundancy", "bases differ", "has"),
    ),
    _case(
        "not-compared name a list",
        "watch",
        [("evidence.redundancy.not_compared.0.display_name", _LIST)],
        _line("redundancy", "bases differ", "has"),
    ),
    _case(
        "not-compared reason missing",
        "watch",
        [("evidence.redundancy.not_compared.0.reason", _GONE)],
        _line("redundancy", pb.FULL_NAMES["shadow"], "has"),
    ),
    _case(
        "not-compared reason null",
        "watch",
        [("evidence.redundancy.not_compared.0.reason", None)],
        _line("redundancy", pb.FULL_NAMES["shadow"], "has"),
    ),
    _case(
        "not-compared reason a list",
        "watch",
        [("evidence.redundancy.not_compared.0.reason", _LIST)],
        _line("redundancy", pb.FULL_NAMES["shadow"], "has"),
    ),
    _case(
        "not-compared list is text",
        "watch",
        [("evidence.redundancy.not_compared", _MARK)],
        _block("redundancy"),
        not_recorded=False,
    ),
    _case(
        "not-compared list holds text",
        "watch",
        [("evidence.redundancy.not_compared", [_MARK])],
        _block("redundancy"),
        not_recorded=False,
    ),
    _case(
        "not-compared list is a dict",
        "watch",
        [("evidence.redundancy.not_compared", _DICT)],
        _block("redundancy"),
        not_recorded=False,
    ),
    # the population the composite was normalized over
    _case(
        "population missing",
        "watch",
        [("evidence.composite.population_size", _GONE)],
        _block("composite"),
    ),
    _case(
        "population null",
        "watch",
        [("evidence.composite.population_size", None)],
        _block("composite"),
    ),
    _case(
        "population a list",
        "watch",
        [("evidence.composite.population_size", _LIST)],
        _block("composite"),
    ),
    _case(
        "population a dict",
        "watch",
        [("evidence.composite.population_size", _DICT)],
        _block("composite"),
    ),
    _case(
        "population text",
        "watch",
        [("evidence.composite.population_size", _MARK)],
        _block("composite"),
    ),
    _case(
        "population a boolean",
        "watch",
        [("evidence.composite.population_size", True)],
        _block("composite"),
        absent=(r"\bTrue\b", r"\b1 symphon"),
        markers=(_MARK, _TRUE),
    ),
    _case(
        "population not a whole number",
        "watch",
        [("evidence.composite.population_size", 2.5)],
        _block("composite"),
        absent=(r"\b2 symphon", _TWO_POINT_FIVE),
        markers=(_MARK, _TWO_POINT_FIVE),
    ),
    # an element of the reasons or the missing list that is not a text
    *[
        _case(
            f"verdict reason element {label}",
            "watch",
            [("verdict_reasons.2", value)],
            ("testid", "tenure-review-verdict-reasons"),
            keeps=("verdict_reasons.0", "verdict_reasons.1"),
            markers=_ELEMENT_MARKERS,
        )
        for label, value in _BAD_ELEMENTS
    ],
    *[
        _case(
            f"missing item element {label}",
            "watch",
            [("missing_items.1", value)],
            ("testid", "tenure-review-missing"),
            keeps=("missing_items.0",),
            markers=_ELEMENT_MARKERS,
        )
        for label, value in _BAD_ELEMENTS
    ],
    # the explanation and the day it was written, which a retire-candidate entry carries
    *[
        _case(
            f"explanation {label}",
            "retire",
            [("explanation", value)],
            ("testid", "retirement-rec-explanation"),
            markers=_ELEMENT_MARKERS,
        )
        for label, value in _BAD_ELEMENTS
        if value is not None
    ],
    *[
        _case(
            f"explanation written on {label}",
            "retire",
            [("explanation", _EXPLANATION), ("explanation_written_on", value)],
            ("testid", "retirement-rec-explanation"),
            keeps=("explanation",),
            markers=_ELEMENT_MARKERS,
        )
        for label, value in _BAD_ELEMENTS
        if value is not None
    ],
    # a figure of the pair that is absent, null or not a record
    *[
        _case(
            f"pair {name.replace('_', ' ')} {label}",
            "watch",
            [(f"evidence.redundancy.pair.{name}", value)],
            _line("redundancy", name.replace("_", " ")),
            markers=_ELEMENT_MARKERS,
        )
        for name, label, value in _PAIR_FIGURE_FAULTS
    ],
    # a figure of an item that is null or not a record
    *[
        _case(
            f"{item} {name.replace('_', ' ')} {label}",
            "watch",
            [(f"evidence.{item}.figures.{name}", value)],
            _line(item, name.replace("_", " ")),
            markers=_ELEMENT_MARKERS,
        )
        for item, name, label, value in _ITEM_FIGURE_FAULTS
    ],
    *[
        _case(
            f"performance figures {label}",
            "watch",
            [("evidence.performance.figures", value)],
            _line("performance", "figures", "has"),
            markers=_ELEMENT_MARKERS,
        )
        for label, value in _FIGURES_FAULTS
    ],
    # a count of days that is a number but not a whole number
    _case(
        "window days not a whole number",
        "watch",
        [("evidence.performance.window.days", 12.5)],
        _line("performance", "window"),
        absent=(_TRUNCATED_TWELVE, _TWELVE_POINT_FIVE),
        markers=(_MARK, _TWELVE_POINT_FIVE),
    ),
    _case(
        "window sessions not a whole number",
        "watch",
        [("evidence.performance.window.sessions", 12.5)],
        _line("performance", "window"),
        absent=(_TRUNCATED_TWELVE, _TWELVE_POINT_FIVE),
        markers=(_MARK, _TWELVE_POINT_FIVE),
    ),
    _case(
        "holdings window days not a whole number",
        "steady",
        [("evidence.long_vix.window.days", 12.5)],
        _line("long_vix", "window"),
        absent=(_TRUNCATED_TWELVE, _TWELVE_POINT_FIVE),
        markers=(_MARK, _TWELVE_POINT_FIVE),
    ),
    _case(
        "holdings window sessions not a whole number",
        "steady",
        [("evidence.long_vix.window.sessions", 12.5)],
        _line("long_vix", "window"),
        absent=(_TRUNCATED_TWELVE, _TWELVE_POINT_FIVE),
        markers=(_MARK, _TWELVE_POINT_FIVE),
    ),
    # a figure counted in days whose stored value is not a whole number
    *[
        _case(
            f"{name.replace('_', ' ')} not a whole number",
            "steady",
            [(f"evidence.long_vix.figures.{name}.value", 12.5)],
            _line("long_vix", name.replace("_", " ")),
            absent=(_TRUNCATED_TWELVE, _TWELVE_POINT_FIVE),
            markers=(_MARK, _TWELVE_POINT_FIVE),
        )
        for name in ("held_days", "co_held_days")
    ],
    # text of only spaces counts as missing, in every place a stored text is printed
    _case("basis label blank", "watch", [("basis.label", _BLANK)], _BASIS),
    _case(
        "basis fallback reason blank",
        "watch",
        [("basis.fallback_reason", _BLANK)],
        _BASIS,
        not_recorded=False,
        absent=(r";\s*;", r";\s*$"),
    ),
    _case(
        "basis series start blank",
        "watch",
        [("basis.series_begins", _BLANK)],
        _BASIS,
        not_recorded=False,
        absent=(r"series begins\s*(;|$)",),
    ),
    _case(
        "basis tenure start blank",
        "watch",
        [("basis.tenure_begins", _BLANK)],
        _BASIS,
        not_recorded=False,
        absent=(r"tenure begins\s*$",),
    ),
    _case(
        "trigger blank", "watch", [("__run__.trigger", _BLANK)], _header("tenure-review-trigger")
    ),
    *[
        _case(
            f"trigger {label}",
            "watch",
            [("__run__.trigger", value)],
            _header("tenure-review-trigger"),
            optional=True,
            markers=_ELEMENT_MARKERS,
        )
        for label, value in (
            ("missing", _GONE),
            ("null", None),
            ("a list", _LIST),
            ("a number", 987654),
        )
    ],
    _case(
        "threshold note blank",
        "watch",
        [("__run__.threshold_note", _BLANK)],
        _header("tenure-review-threshold-note"),
        optional=True,
        not_recorded=False,
    ),
    _case(
        "explanation written on blank",
        "retire",
        [("explanation", _EXPLANATION), ("explanation_written_on", _BLANK)],
        ("testid", "retirement-rec-explanation"),
        not_recorded=False,
        keeps=("explanation",),
        absent=(r"written", _NOT_RECORDED),
    ),
    _case(
        "subject blank",
        "watch",
        [("evidence.performance.subject", _BLANK)],
        _line("performance", "subject"),
    ),
    _case(
        "source blank",
        "watch",
        [("evidence.performance.source", _BLANK)],
        _line("performance", "source"),
    ),
    _case(
        "unavailable reason blank",
        "watch",
        [("evidence.long_vix.reason", _BLANK)],
        _line("long_vix", "unavailable"),
    ),
    _case(
        "window start blank",
        "watch",
        [("evidence.performance.window.first", _BLANK)],
        _line("performance", "window"),
    ),
    _case(
        "window end blank",
        "watch",
        [("evidence.performance.window.last", _BLANK)],
        _line("performance", "window"),
    ),
    _case(
        "uncertainty gate result blank",
        "watch",
        [("evidence.redundancy.pair.uncertainty_gate.text", _BLANK)],
        _line("redundancy", "uncertainty gate"),
    ),
    _case(
        "left-out name blank",
        "watch",
        [("evidence.marginal.excluded.0.display_name", _BLANK)],
        _line("marginal", "left out"),
    ),
    _case(
        "left-out reason blank",
        "watch",
        [("evidence.marginal.excluded.0.reason", _BLANK)],
        _line("marginal", "left out"),
    ),
    _case(
        "not-compared name blank",
        "watch",
        [("evidence.redundancy.not_compared.0.display_name", _BLANK)],
        _line("redundancy", "bases differ", "has"),
    ),
    _case(
        "not-compared reason blank",
        "watch",
        [("evidence.redundancy.not_compared.0.reason", _BLANK)],
        _line("redundancy", pb.FULL_NAMES["shadow"], "has"),
    ),
    _case(
        "verdict reason element blank",
        "watch",
        [("verdict_reasons.2", _BLANK)],
        ("testid", "tenure-review-verdict-reasons"),
        keeps=("verdict_reasons.0", "verdict_reasons.1"),
    ),
    _case(
        "missing item element blank",
        "watch",
        [("missing_items.1", _BLANK)],
        ("testid", "tenure-review-missing"),
        keeps=("missing_items.0",),
    ),
    # subject and source of an unavailable item that shows its window
    *[
        _case(
            f"unavailable item {word} {label}",
            "keep",
            [(f"evidence.backtest.{word}", value)],
            _line("backtest", word),
        )
        for word in ("subject", "source")
        for label, value in (
            ("missing", _GONE),
            ("null", None),
            ("a list", _LIST),
            ("blank", _BLANK),
        )
    ],
    # a figure whose value rounds to zero or is a fraction of days is never a bare "not computed"
    *[
        _case(
            f"{name.replace('_', ' ')} not a whole number, wording",
            "steady",
            [(f"evidence.long_vix.figures.{name}.value", 12.5)],
            _line("long_vix", name.replace("_", " ")),
            absent=(r"\bnot computed\b",),
            markers=(_MARK, _TWELVE_POINT_FIVE),
        )
        for name in ("held_days", "co_held_days")
    ],
    # a unit of only spaces is a missing unit: the figure says so, whatever its unit family
    _case(
        "percent figure unit blank",
        "watch",
        [("evidence.performance.figures.total_return.unit", _BLANK)],
        _line("performance", "total return"),
        shows=_SHOWN_FIGURE,
    ),
    _case(
        "ratio figure unit blank",
        "watch",
        [("evidence.performance.figures.sharpe.unit", _BLANK)],
        _line("performance", "sharpe"),
        shows="evidence.performance.figures.sharpe.value",
    ),
    _case(
        "days figure unit blank",
        "steady",
        [("evidence.long_vix.figures.held_days.unit", _BLANK)],
        _line("long_vix", "held days"),
    ),
    _case(
        "composite figure unit blank",
        "watch",
        [("evidence.composite.figures.composite.unit", _BLANK)],
        _line("composite", "composite:"),
        shows="evidence.composite.figures.composite.value",
    ),
]


class TestAMissingOrWrongTypedFieldReadsNotRecorded:
    @pytest.mark.parametrize("poison", _POISONS)
    def test_the_field_reads_not_recorded_and_leaves_no_trace_while_everything_else_is_unchanged(
        self, client, isolated_db, poison
    ):
        which, pick = _VICTIMS[poison.victim]
        holder = {}

        def edit(record):
            victim = pick(record)
            holder["id"] = victim["symphony_id"]
            if poison.shows:
                holder["shown"] = _get(victim, poison.shows)
            for path, value in poison.edits:
                if path.startswith("__run__."):
                    _edit(record, path.removeprefix("__run__."), value)
                else:
                    _edit(victim, path, value)
            holder["keeps"] = [_get(victim, path) for path in poison.keeps]

        record, pristine, panel = _render_edited(client, which, edit)
        everything = panel.everything()
        assert not re.search(r"\bNone\b", everything), "Python's None reached the panel"
        for marker in poison.markers:
            assert not re.search(marker, everything), f"the stored value {marker!r} was printed"
        scoped = _scoped(panel, holder["id"], poison.scope)
        if scoped is None and poison.optional:
            _assert_others_unchanged(panel, pristine, holder["id"], poison.scope)
            return
        assert scoped is not None, "the line that prints the field is dropped, not worded"
        text = scoped.text()
        assert text, "the field leaves an empty slot"
        if poison.not_recorded:
            assert _NOT_RECORDED in text.lower(), f"nothing says it is not recorded: {text!r}"
        for pattern in poison.absent:
            assert not re.search(pattern, text, flags=re.IGNORECASE), (pattern, text)
        if poison.shows:
            assert _shows(scoped, holder["shown"]), f"the stored figure is gone: {text!r}"
        for kept in holder["keeps"]:
            assert kept.replace("_", " ") in text.replace("_", " "), (
                f"the stored text {kept!r} was lost beside the field that reads not recorded: {text!r}"
            )
        _assert_others_unchanged(panel, pristine, holder["id"], poison.scope)

    @pytest.mark.parametrize("which", ["full", "small", "edge", "quiet"])
    def test_the_unpoisoned_panels_print_none_of_the_tokens_the_cases_forbid(
        self, client, isolated_db, which
    ):
        _store_record(which)
        everything = _panel(client).everything()
        forbidden = re.compile(
            rf"\b(None|True|False|nan|inf)\b|{_MARK}|987654|{_TWELVE_POINT_FIVE}|{_TWO_POINT_FIVE}"
        )
        found = forbidden.search(everything)
        assert found is None, found.group(0)


class TestEveryPrintedFieldOfOneBrokenRunIsHonest:
    """The shape the ux review captured: one entry whose basis is a list and whose evidence
    is text, another whose unavailable item has a null reason, and a run header with no
    threshold."""

    def test_a_run_with_several_broken_fields_prints_no_none_and_leaves_the_rest_alone(
        self, client, isolated_db
    ):
        def edit(record):
            _edit(pb.entry_with_verdict(record, "keep"), "basis", ["a", "list"])
            _edit(pb.entry_with_verdict(record, "keep"), "evidence", "text")
            _edit(pb.entry_with_verdict(record, "watch"), "evidence.long_vix.reason", None)
            _edit(record, "threshold_in_effect", _GONE)

        record, pristine, panel = _render_edited(client, "full", edit)
        assert not re.search(r"\bNone\b", panel.everything())
        keep = pb.entry_with_verdict(record, "keep")
        node = pb.entry_node(panel, keep["symphony_id"])
        assert _NOT_RECORDED in node.find("tenure-review-basis").text().lower()
        for name in _PANEL_ITEMS:
            assert _NOT_RECORDED in node.find(f"tenure-review-evidence-{name}").text().lower(), name
        assert _NOT_RECORDED in panel.find("tenure-review-threshold").text().lower()
        watch = pb.entry_with_verdict(record, "watch")
        long_vix = pb.entry_node(panel, watch["symphony_id"]).find(
            "tenure-review-evidence-long_vix"
        )
        assert _NOT_RECORDED in long_vix.text().lower()
        for sid, texts in pristine.items():
            if sid in ("[header]", keep["symphony_id"], watch["symphony_id"]):
                continue
            assert pb.entry_node(panel, sid).everything() == texts["entry"], sid

    def test_an_entry_with_no_symphony_id_prints_no_none_and_offers_no_decision_on_it(
        self, client, isolated_db
    ):
        def edit(record):
            _edit(pb.entry_with_verdict(record, "keep"), "symphony_id", _GONE)

        record, pristine, panel = _render_edited(client, "full", edit)
        assert not re.search(r"\bNone\b", panel.everything())
        keep_id = pb.FULL_IDS["keep"]
        buttons = panel.find_all("retirement-approve-btn") + panel.find_all("retirement-reject-btn")
        for button in buttons:
            assert button.attrs.get("data-candidate-id"), "a decision button carries no id"
        for sid, texts in pristine.items():
            if sid in ("[header]", keep_id):
                continue
            assert pb.entry_node(panel, sid).everything() == texts["entry"], sid

    def test_a_stored_reason_is_printed_as_stored_even_when_it_says_none(self, client, isolated_db):
        reason = "None of the peers could be compared, undefined for this series"

        def edit(record):
            _edit(pb.entry_with_verdict(record, "watch"), "evidence.long_vix.reason", reason)

        record, _, panel = _render_edited(client, "full", edit)
        watch = pb.entry_with_verdict(record, "watch")
        long_vix = pb.entry_node(panel, watch["symphony_id"]).find(
            "tenure-review-evidence-long_vix"
        )
        assert reason in long_vix.text()
        assert _NOT_RECORDED not in long_vix.text().lower()


# ===========================================================================
# The running-since and failed-attempt notices read in the zone the run line does, so
# the order of events reads correctly.
# ===========================================================================

_ET_CASES = [
    ("2026-12-31T22:04:00+00:00", "2026-12-31 17:04"),
    ("2026-07-15T02:30:00+00:00", "2026-07-14 22:30"),
    ("2026-12-31T17:05:00-05:00", "2026-12-31 17:05"),
]
_ET_IDS = ["standard time", "daylight time, previous Eastern day", "already Eastern"]
_TIME_NOT_RECORDED = "time not recorded"
_UNREADABLE_TIMES = [None, "not a timestamp", 12345]
_UNREADABLE_IDS = ["missing", "text", "number"]


def _run_state(monkeypatch, **fields) -> None:
    """Make the review run state report exactly ``fields`` (the dict the panel reads)."""
    snapshot = {
        "running": False,
        "running_since": None,
        "trigger": None,
        "last_failed_attempt": None,
        **fields,
    }
    monkeypatch.setattr(tenure_review.REVIEW_RUN_STATE, "snapshot", lambda: dict(snapshot))


def _assert_eastern(notice_text: str, moment: str, expected: str) -> None:
    assert f"{expected} ET" in notice_text, notice_text
    assert "+00:00" not in notice_text and "-05:00" not in notice_text, notice_text
    assert not re.search(r"\d{4}-\d{2}-\d{2}T", notice_text), notice_text
    if moment[:10] != expected[:10]:
        assert moment[:10] not in notice_text, "the UTC date is shown as if it were Eastern"
    if moment[11:16] != expected[11:]:
        assert moment[11:16] not in notice_text, "the stored time is shown as if it were Eastern"


class TestNoticeTimesAreEastern:
    @pytest.mark.parametrize("moment, expected", _ET_CASES, ids=_ET_IDS)
    def test_the_running_since_time_reads_in_eastern_with_the_run_lines_format(
        self, client, isolated_db, monkeypatch, moment, expected
    ):
        _stored_full_run()
        _run_state(monkeypatch, running=True, running_since=moment)
        panel = _panel(client)
        notice = panel.find("tenure-review-running-notice").text()
        assert "review running since" in notice
        _assert_eastern(notice, moment, expected)
        assert _ET_STAMP.search(panel.find("tenure-review-run-time").text()), "fixture sanity"

    @pytest.mark.parametrize("moment, expected", _ET_CASES, ids=_ET_IDS)
    @pytest.mark.parametrize("as_text", [False, True], ids=["datetime", "text"])
    def test_the_failed_attempt_time_reads_in_eastern_with_the_run_lines_format(
        self, client, isolated_db, moment, expected, as_text
    ):
        _stored_full_run()
        state = tenure_review.REVIEW_RUN_STATE
        token = state.try_start("nightly")
        assert token
        state.finish_failure(
            token,
            "Composer statistics unavailable",
            moment if as_text else datetime.fromisoformat(moment),
        )
        notice = _panel(client).find("tenure-review-failed-notice").text()
        assert "latest review attempt failed: Composer statistics unavailable" in notice
        _assert_eastern(notice, moment, expected)

    def test_a_failed_attempt_before_the_completed_run_reads_as_earlier_than_it(
        self, client, isolated_db
    ):
        """The captured case: a failure at 21:45 UTC printed above a run completed at 17:04
        Eastern read as later than the run, when it was 16:45 Eastern."""
        pb.seed_bot_state()
        pb.store_run(pb.full_record(completed_at="2026-12-31T22:04:00+00:00"))
        state = tenure_review.REVIEW_RUN_STATE
        token = state.try_start("nightly")
        assert token
        state.finish_failure(
            token, "Composer statistics unavailable", datetime(2026, 12, 31, 21, 45, tzinfo=UTC)
        )
        panel = _panel(client)
        failed = _ET_STAMP.search(panel.find("tenure-review-failed-notice").text())
        completed = _ET_STAMP.search(panel.find("tenure-review-run-time").text())
        assert failed and completed, "a notice or the run line carries no Eastern time stamp"
        assert failed.group(0).startswith("2026-12-31 16:45")
        assert failed.group(0) < completed.group(0), "the order of events reads backwards"

    @pytest.mark.parametrize("moment", _UNREADABLE_TIMES, ids=_UNREADABLE_IDS)
    def test_a_running_since_time_that_cannot_be_read_says_so_and_prints_no_raw_value(
        self, client, isolated_db, monkeypatch, moment
    ):
        _stored_full_run()
        _run_state(monkeypatch, running=True, running_since=moment)
        notice = _panel(client).find("tenure-review-running-notice").text()
        assert "review running since" in notice
        assert _TIME_NOT_RECORDED in notice.lower(), notice
        assert not re.search(r"\bNone\b", notice)
        if isinstance(moment, str):
            assert moment not in notice
        elif moment is not None:
            assert str(moment) not in notice

    @pytest.mark.parametrize("moment", _UNREADABLE_TIMES, ids=_UNREADABLE_IDS)
    def test_a_failed_attempt_time_that_cannot_be_read_says_so_and_keeps_the_reason(
        self, client, isolated_db, monkeypatch, moment
    ):
        _stored_full_run()
        failed = {"reason": "Composer statistics unavailable", "at": moment}
        _run_state(monkeypatch, last_failed_attempt=failed)
        notice = _panel(client).find("tenure-review-failed-notice").text()
        assert "latest review attempt failed: Composer statistics unavailable" in notice
        assert _TIME_NOT_RECORDED in notice.lower(), notice
        assert not re.search(r"\bNone\b", notice)
        if isinstance(moment, str):
            assert moment not in notice
        elif moment is not None:
            assert str(moment) not in notice

    def test_a_failed_attempt_with_no_time_key_says_the_time_is_not_recorded(
        self, client, isolated_db, monkeypatch
    ):
        _stored_full_run()
        _run_state(monkeypatch, last_failed_attempt={"reason": "Composer statistics unavailable"})
        notice = _panel(client).find("tenure-review-failed-notice").text()
        assert _TIME_NOT_RECORDED in notice.lower(), notice
        assert not re.search(r"\bNone\b", notice)

    @pytest.mark.parametrize(
        "failed",
        [
            {"at": "2026-12-31T21:45:00+00:00"},
            {"reason": None, "at": "2026-12-31T21:45:00+00:00"},
            {"reason": [_MARK], "at": "2026-12-31T21:45:00+00:00"},
        ],
        ids=["reason missing", "reason null", "reason a list"],
    )
    def test_a_failed_attempt_with_no_readable_reason_says_so(
        self, client, isolated_db, monkeypatch, failed
    ):
        _stored_full_run()
        _run_state(monkeypatch, last_failed_attempt=failed)
        notice = _panel(client).find("tenure-review-failed-notice").text()
        assert "latest review attempt failed" in notice
        assert _NOT_RECORDED in notice.lower(), notice
        assert _MARK not in notice and not re.search(r"\bNone\b", notice)
        assert "2026-12-31 16:45 ET" in notice


# ===========================================================================
# The evidence labels and the row use the sibling classes the design mapping names, so a
# label is never heavier than its box heading.
# ===========================================================================


def _declared(css: str, prop: str) -> list[str]:
    return [v.strip() for v in re.findall(rf"(?<![\w-]){prop}\s*:\s*([^;}}]+)", css)]


def _size_in_rem(value: str) -> float | None:
    found = re.fullmatch(r"\s*([\d.]+)(rem|px)\s*", value)
    if not found:
        return None
    return float(found.group(1)) / (16 if found.group(2) == "px" else 1)


def _weight(value: str) -> int | None:
    named = {"normal": 400, "bold": 700, "bolder": 800, "lighter": 300}
    value = value.strip().lower()
    return named.get(value, int(value) if value.isdigit() else None)


def _label_outweighs_heading(label_css: str, heading_css: str) -> list[str]:
    """Reasons a label styled by ``label_css`` is larger or bolder than a heading styled by
    ``heading_css``. A label that declares no size inherits the row's, which is larger."""
    problems = []
    heading_size = [_size_in_rem(v) for v in _declared(heading_css, "font-size")]
    label_size = [_size_in_rem(v) for v in _declared(label_css, "font-size")]
    if not heading_size or None in heading_size:
        problems.append("the heading declares no size in rem or px")
    elif not label_size or None in label_size:
        problems.append("the label declares no size, so it inherits the row's larger one")
    elif label_size[-1] > heading_size[-1]:
        problems.append(f"the label is larger than its heading ({label_size[-1]} rem)")
    heading_weight = [_weight(v) for v in _declared(heading_css, "font-weight")] or [400]
    label_weight = [_weight(v) for v in _declared(label_css, "font-weight")] or [400]
    if None in label_weight or None in heading_weight:
        problems.append("a weight is not readable")
    elif label_weight[-1] > heading_weight[-1]:
        problems.append(f"the label is bolder than its heading ({label_weight[-1]})")
    return problems


_META_LINES = (
    "unavailable",
    "not applicable",
    "subject",
    "source",
    "window",
    "left out",
    "not used in the verdict",
)


def _label_nodes(block: pb.Node, label: str) -> list[pb.Node]:
    """The lines that carry the basis label: the lines holding it that are not the item's
    subject, source, window, reason, left-out or not-used lines, which can repeat its words."""
    return [
        n
        for n in pb.leaf_nodes_containing(block, label)
        if not n.text().lower().startswith(_META_LINES)
    ]


class TestTheEvidenceUsesTheSiblingClasses:
    @pytest.mark.parametrize("which", ["full", "small", "edge", "quiet"])
    def test_every_evidence_label_carries_the_basis_class_and_every_row_the_card_class(
        self, client, isolated_db, which
    ):
        record = _store_record(which)
        panel = _panel(client)
        checked = 0
        for stored in record["entries"]:
            node = pb.entry_node(panel, stored["symphony_id"])
            assert "retirement-rec-card" in node.classes(), stored["display_name"]
            for name in _PANEL_ITEMS:
                block = node.find(f"tenure-review-evidence-{name}")
                labels = _label_nodes(block, stored["evidence"][name]["label"])
                where = f"{stored['display_name']} / {name}"
                assert labels, f"{where}: the basis label is not shown on a line of its own"
                for label in labels:
                    assert "retirement-rec-basis" in label.classes(), (where, label.classes())
                checked += 1
        assert checked >= 18

    def test_the_css_check_reports_a_restyled_label_and_accepts_the_mapped_class(self):
        rules = TestDesignSystem._css_rules()
        heading_css = TestDesignSystem._css_of_exact_classes(
            rules, ["tenure-review-evidence-heading"]
        )
        assert heading_css, "fixture sanity: the heading has a style of its own"
        mapped = TestDesignSystem._css_of_exact_classes(rules, ["retirement-rec-basis"])
        assert _label_outweighs_heading(mapped, heading_css) == []
        no_size = "font-weight: 700; color: var(--studio-ink);"
        assert _label_outweighs_heading(no_size, heading_css)
        large = "font-size: 0.8125rem; font-style: italic;"
        assert _label_outweighs_heading(large, heading_css)
        heavy = "font-size: 0.5rem; font-weight: 800;"
        assert _label_outweighs_heading(heavy, heading_css)

    @pytest.mark.parametrize("which", ["full", "edge"])
    def test_no_label_is_larger_or_bolder_than_the_heading_of_its_box(
        self, client, isolated_db, which
    ):
        record = _store_record(which)
        panel = _panel(client)
        rules = TestDesignSystem._css_rules()
        checked = 0
        for stored in record["entries"]:
            node = pb.entry_node(panel, stored["symphony_id"])
            for name in _PANEL_ITEMS:
                block = node.find(f"tenure-review-evidence-{name}")
                heading = block.find("tenure-review-evidence-heading")
                heading_css = TestDesignSystem._css_of_exact_classes(rules, heading.classes())
                for label in _label_nodes(block, stored["evidence"][name]["label"]):
                    label_css = TestDesignSystem._css_of_exact_classes(rules, label.classes())
                    where = f"{stored['display_name']} / {name}"
                    assert _label_outweighs_heading(label_css, heading_css) == [], where
                    checked += 1
        assert checked >= 12

    def test_each_block_of_every_entry_still_shows_its_basis_label(self, client, isolated_db):
        record = _stored_full_run()
        panel = _panel(client)
        for stored in record["entries"]:
            node = pb.entry_node(panel, stored["symphony_id"])
            for name in _PANEL_ITEMS:
                text = node.find(f"tenure-review-evidence-{name}").text()
                assert stored["evidence"][name]["label"] in text, (stored["display_name"], name)


# ===========================================================================
# A pair that decides nothing is shown, and its box says it is not used in the verdict.
# ===========================================================================

_NOT_USED = "not used in the verdict"


def _not_used_lines(block: pb.Node) -> list[pb.Node]:
    return [
        leaf
        for leaf in pb.leaf_nodes_containing(block, _NOT_USED)
        if leaf.text().lower().startswith(_NOT_USED)
    ]


class TestAnUnusedPairSaysItIsNotUsedInTheVerdict:
    _CASES = [
        ("small", pb.SMALL_IDS["first"]),
        ("edge", pb.EDGE_IDS["twin_one"]),
        ("edge", pb.EDGE_IDS["twin_two"]),
    ]
    _IDS = ["short pair", "undefined interval, first twin", "undefined interval, second twin"]

    @pytest.fixture(params=_CASES, ids=_IDS)
    def unused(self, request, client, isolated_db):
        which, symphony_id = request.param
        record = _store_record(which)
        stored = pb.entry_of(record, symphony_id)
        item = stored["evidence"]["redundancy"]
        assert item["status"] == "unavailable" and item["pair"], "fixture sanity: a pair is shown"
        assert "redundancy" in stored["missing_items"], "fixture sanity: it decides nothing"
        node = pb.entry_node(_panel(client), symphony_id)
        return item, node.find("tenure-review-evidence-redundancy")

    def test_the_box_states_the_pair_is_not_used_followed_by_the_items_reason(self, unused):
        item, block = unused
        lines = _not_used_lines(block)
        assert len(lines) == 1, [n.text() for n in lines]
        text = lines[0].text()
        assert re.match(rf"{_NOT_USED}:\s*\S", text, flags=re.IGNORECASE), text
        stored = item.get("reason")
        expected = stored if isinstance(stored, str) and stored else _NOT_RECORDED
        assert expected in text, f"the line does not carry the item's reason {expected!r}: {text!r}"
        assert not re.search(r"\bNone\b", text)

    def test_the_line_sits_in_the_box_that_shows_the_pair(self, unused):
        item, block = unused
        assert item["pair"]["sibling_name"] in block.text()
        assert _not_used_lines(block), "the box shows a pair and does not say it decides nothing"

    @pytest.mark.parametrize("which", ["small", "edge"])
    def test_the_reason_is_read_from_the_stored_item(self, client, isolated_db, which):
        reason = "gates could not decide on this pair"
        victim = pb.SMALL_IDS["first"] if which == "small" else pb.EDGE_IDS["twin_one"]

        def edit(record):
            _edit(pb.entry_of(record, victim), "evidence.redundancy.reason", reason)

        _, _, panel = _render_edited(client, which, edit)
        block = pb.entry_node(panel, victim).find("tenure-review-evidence-redundancy")
        lines = _not_used_lines(block)
        assert len(lines) == 1 and reason in lines[0].text(), [n.text() for n in lines]

    @pytest.mark.parametrize("which", ["full", "quiet"])
    def test_a_box_whose_redundancy_decided_or_says_no_sibling_carries_no_such_line(
        self, client, isolated_db, which
    ):
        record = _store_record(which)
        panel = _panel(client)
        checked = 0
        for stored in record["entries"]:
            item = stored["evidence"]["redundancy"]
            if item["status"] != "available" or "redundancy" in stored["missing_items"]:
                continue
            block = pb.entry_node(panel, stored["symphony_id"]).find(
                "tenure-review-evidence-redundancy"
            )
            assert _NOT_USED not in block.text().lower(), stored["display_name"]
            checked += 1
        assert checked >= 3


# ===========================================================================
# Wording: a unit word that repeats the figure's name is not printed.
# ===========================================================================


class TestAUnitWordIsNotRepeatedBesideTheFigureItNames:
    def test_a_figure_named_for_its_unit_is_printed_as_the_bare_number(self, client, isolated_db):
        shown = 0
        for which in ("full", "edge"):
            record = _store_record(which)
            panel = _panel(client)
            for stored in record["entries"]:
                node = pb.entry_node(panel, stored["symphony_id"])
                for name in _PANEL_ITEMS:
                    item = stored["evidence"][name]
                    if item["status"] != "available":
                        continue
                    figures = dict(item["figures"])
                    if item.get("pair"):
                        figures.update({n: item["pair"][n] for n in _PAIR_FIGURES})
                    for fig_name, fig in figures.items():
                        label = fig_name.replace("_", " ")
                        if fig["value"] is None or fig["unit"].lower() != label:
                            continue
                        block = node.find(f"tenure-review-evidence-{name}")
                        lines = [
                            n
                            for n in pb.leaf_nodes_containing(block, label)
                            if n.text().lower().startswith(f"{label}:")
                        ]
                        where = f"{stored['display_name']} / {fig_name}"
                        assert len(lines) == 1, where
                        text = lines[0].text()
                        assert _shows(lines[0], fig["value"]), (where, text)
                        assert not re.search(rf"\d\s*{re.escape(fig['unit'])}\b", text), (
                            f"{where}: the unit repeats the name: {text!r}"
                        )
                        shown += 1
        assert shown >= 3, "the records must hold figures whose unit is their name"

    def test_a_unit_that_does_not_repeat_the_name_is_still_printed(self, client, isolated_db):
        shown = 0
        for which in ("full", "edge"):
            record = _store_record(which)
            panel = _panel(client)
            for stored in record["entries"]:
                node = pb.entry_node(panel, stored["symphony_id"])
                for name in _PANEL_ITEMS:
                    item = stored["evidence"][name]
                    if item["status"] != "available":
                        continue
                    for fig_name, fig in item["figures"].items():
                        label = fig_name.replace("_", " ")
                        unit_word = fig["unit"].split()[0].lower()
                        if fig["value"] is None or unit_word in label.split():
                            continue
                        block = node.find(f"tenure-review-evidence-{name}")
                        lines = [
                            n
                            for n in pb.leaf_nodes_containing(block, label)
                            if n.text().lower().startswith(f"{label}:")
                        ]
                        where = f"{stored['display_name']} / {fig_name}"
                        assert len(lines) == 1, where
                        # a percent figure carries the percent sign, not the word
                        shown_unit = "%" if fig["unit"].lower() == "percent" else unit_word
                        assert shown_unit in lines[0].text().lower(), (where, lines[0].text())
                        shown += 1
        assert shown >= 20, "the records must hold many figures with a unit of their own"


# ===========================================================================
# The comparison item lists the peers it could not compare; the population line reads
# in the singular for one symphony.
# ===========================================================================


class TestPeersNotComparedAreListedWithTheirReason:
    @pytest.mark.parametrize("which", ["full", "small", "edge", "quiet"])
    def test_every_peer_the_record_lists_is_named_with_its_reason_in_the_redundancy_box(
        self, client, isolated_db, which
    ):
        record = _store_record(which)
        panel = _panel(client)
        listed = 0
        for stored in record["entries"]:
            item = stored["evidence"]["redundancy"]
            block = pb.entry_node(panel, stored["symphony_id"]).find(
                "tenure-review-evidence-redundancy"
            )
            for peer in item.get("not_compared") or []:
                lines = pb.leaf_nodes_containing(block, peer["display_name"])
                where = f"{stored['display_name']} / {peer['display_name']}"
                assert any(peer["reason"] in line.text() for line in lines), where
                listed += 1
        assert listed >= (3 if which == "full" else 0)

    def test_the_list_is_read_from_the_stored_item_and_not_derived_from_the_bases(
        self, client, isolated_db
    ):
        peer = {"display_name": "Zed Peak Rotation", "reason": "bases differ, stored for this test"}

        def edit(record):
            _edit(
                pb.entry_with_verdict(record, "watch"), "evidence.redundancy.not_compared", [peer]
            )

        record, _, panel = _render_edited(client, "full", edit)
        watch = pb.entry_with_verdict(record, "watch")
        block = pb.entry_node(panel, watch["symphony_id"]).find("tenure-review-evidence-redundancy")
        lines = pb.leaf_nodes_containing(block, peer["display_name"])
        assert any(peer["reason"] in line.text() for line in lines), "the stored peer is not listed"
        assert pb.FULL_NAMES["shadow"] not in block.text(), "a peer the record does not list"

    @pytest.mark.parametrize("stored", [[], _GONE], ids=["empty list", "key absent"])
    def test_an_empty_or_absent_list_adds_no_line_and_no_none(self, client, isolated_db, stored):
        def edit(record):
            _edit(
                pb.entry_with_verdict(record, "watch"), "evidence.redundancy.not_compared", stored
            )

        record, _, panel = _render_edited(client, "full", edit)
        watch = pb.entry_with_verdict(record, "watch")
        block = pb.entry_node(panel, watch["symphony_id"]).find("tenure-review-evidence-redundancy")
        assert pb.FULL_NAMES["shadow"] not in block.text()
        assert not re.search(r"\bNone\b", block.text())
        assert watch["evidence"]["redundancy"]["pair"]["sibling_name"] in block.text()


class TestThePopulationLineReadsInTheSingularForOne:
    def _composite_block(self, client, population):
        def edit(record):
            _edit(
                pb.entry_with_verdict(record, "watch"),
                "evidence.composite.population_size",
                population,
            )

        record, _, panel = _render_edited(client, "full", edit)
        watch = pb.entry_with_verdict(record, "watch")
        return pb.entry_node(panel, watch["symphony_id"]).find("tenure-review-evidence-composite")

    def test_one_symphony_reads_as_one_symphony(self, client, isolated_db):
        text = self._composite_block(client, 1).text()
        assert re.search(r"(?<![\d.])1 symphony(?!\w)", text), text
        assert "1 symphonies" not in text

    @pytest.mark.parametrize("population", [2, 7])
    def test_a_larger_whole_population_keeps_the_plural(self, client, isolated_db, population):
        text = self._composite_block(client, population).text()
        assert re.search(rf"(?<![\d.]){population} symphonies", text), text


# ===========================================================================
# A window states the days it used against the sessions its span holds, so a reduced
# count never reads as a shorter window.
# ===========================================================================

_COUNT_RECORDS = ("gap", "full", "edge", "small", "quiet")
_TRADING_DAYS = re.compile(r"(\d+)\s+(?:of\s+(\d+)\s+)?trading days")
_BARE_DAYS = re.compile(r"\b\d+ days\b")


def _window_lines(block: pb.Node, window: dict) -> list[pb.Node]:
    """The innermost elements of a block that print both dates of a stored window."""
    return [
        leaf
        for leaf in pb.leaf_nodes_containing(block, window["first"])
        if window["last"] in leaf.text()
    ]


class TestAWindowStatesTheSessionsItsSpanHolds:
    def test_a_window_with_unused_sessions_reads_n_of_m_and_a_full_one_reads_n_trading_days(
        self, client, isolated_db
    ):
        short = whole = 0
        for which in _COUNT_RECORDS:
            record = _store_record(which)
            panel = _panel(client)
            for stored in record["entries"]:
                node = pb.entry_node(panel, stored["symphony_id"])
                for name in _PANEL_ITEMS:
                    window = stored["evidence"][name]["window"]
                    if name == "long_vix" or not window:
                        continue
                    where = f"{which} / {stored['display_name']} / {name}"
                    days, sessions = window["days"], window["sessions"]
                    lines = _window_lines(node.find(f"tenure-review-evidence-{name}"), window)
                    assert lines, f"{where}: the window is not shown"
                    for line in lines:
                        text = line.text()
                        found = _TRADING_DAYS.search(text)
                        assert found, f"{where}: no count of trading days in {text!r}"
                        assert int(found.group(1)) == days, (where, text)
                        assert not _BARE_DAYS.search(text), f"{where}: a bare count in {text!r}"
                        if days < sessions:
                            assert found.group(2) and int(found.group(2)) == sessions, (where, text)
                        else:
                            assert found.group(2) is None, (where, text)
                    short += days < sessions
                    whole += days == sessions
        assert short >= 6, (
            f"the records must hold windows that use fewer days than they span: {short}"
        )
        assert whole >= 10, f"the records must hold windows that use every session: {whole}"

    def test_the_gap_record_holds_days_two_below_the_sessions_the_calendar_counts(
        self, client, isolated_db
    ):
        """The sessions come from the exchange calendar for the window's own dates, and the
        panel reads the count of days used as two less than that."""
        record = _store_record("gap")
        panel = _panel(client)
        lead = next(
            e
            for e in record["entries"]
            if e["evidence"]["performance"]["window"]["days"]
            < e["evidence"]["performance"]["window"]["sessions"]
        )
        window = lead["evidence"]["performance"]["window"]
        span = review_builders.nyse_days(
            date.fromisoformat(window["first"]), date.fromisoformat(window["last"])
        )
        assert window["sessions"] == len(span), "fixture sanity: the record counts the calendar"
        assert window["days"] == len(span) - pb.GAP_SESSIONS_WITHOUT_A_VALUE
        text = (
            pb.entry_node(panel, lead["symphony_id"])
            .find("tenure-review-evidence-performance")
            .text()
        )
        assert f"{len(span) - pb.GAP_SESSIONS_WITHOUT_A_VALUE} of {len(span)} trading days" in text

    def test_the_long_vix_item_reads_holdings_on_n_of_m_trading_days(self, client, isolated_db):
        partial = whole = 0
        for which in ("gap", "edge"):
            record = _store_record(which)
            panel = _panel(client)
            for stored in record["entries"]:
                item = stored["evidence"]["long_vix"]
                window = item["window"]
                if item["status"] != "available" or not window:
                    continue
                text = (
                    pb.entry_node(panel, stored["symphony_id"])
                    .find("tenure-review-evidence-long_vix")
                    .text()
                )
                days, sessions = window["days"], window["sessions"]
                where = f"{which} / {stored['display_name']}"
                if days < sessions:
                    assert f"holdings on {days} of {sessions} trading days" in text, (where, text)
                    partial += 1
                else:
                    assert re.search(rf"\b{days} trading days", text), (where, text)
                    whole += 1
        assert partial >= 3 and whole >= 1, (partial, whole)


# ===========================================================================
# The comparison portfolio: every symphony left out is listed with the label of its kind,
# and an item that cannot compare says why in the reason the run stored.
# ===========================================================================

_EXCLUSION_LABELS = {
    "excluded: not yet eligible",
    "excluded: tenure unavailable",
    "excluded: no history",
    "excluded: different basis",
}
_COMPARISON_RECORDS = ("full", "small", "lone", "apart", "gap")


class TestTheComparisonPortfolioNamesWhoWasLeftOutAndWhy:
    def test_each_left_out_symphony_is_listed_with_the_label_stored_for_its_kind(
        self, client, isolated_db
    ):
        seen = set()
        for which in _COMPARISON_RECORDS:
            record = _store_record(which)
            panel = _panel(client)
            for stored in record["entries"]:
                block = pb.entry_node(panel, stored["symphony_id"]).find(
                    "tenure-review-evidence-marginal"
                )
                for ex in stored["evidence"]["marginal"].get("excluded") or []:
                    where = f"{which} / {stored['display_name']} / {ex['display_name']}"
                    lines = pb.leaf_nodes_containing(block, ex["display_name"])
                    assert any(ex["reason"] in line.text() for line in lines), where
                    seen.add(ex["reason"])
        assert seen >= _EXCLUSION_LABELS, "the records must hold one symphony of each kind"

    def test_a_label_is_read_from_the_stored_entry_and_not_derived_from_the_page(
        self, client, isolated_db
    ):
        stored_label = "excluded: held back for review"

        def edit(record):
            marginal = pb.entry_with_verdict(record, "retire_candidate")["evidence"]["marginal"]
            marginal["excluded"][0]["reason"] = stored_label

        record, _, panel = _render_edited(client, "full", edit)
        retire = pb.entry_with_verdict(record, "retire_candidate")
        block = pb.entry_node(panel, retire["symphony_id"]).find("tenure-review-evidence-marginal")
        name = retire["evidence"]["marginal"]["excluded"][0]["display_name"]
        assert any(stored_label in line.text() for line in pb.leaf_nodes_containing(block, name))

    @pytest.mark.parametrize("which", ["lone", "solo", "apart", "full"])
    def test_an_item_that_cannot_compare_shows_its_stored_reason_and_no_figure(
        self, client, isolated_db, which
    ):
        record = _store_record(which)
        panel = _panel(client)
        words = {"unavailable": "unavailable", "not_applicable": "not applicable"}
        seen = set()
        for stored in record["entries"]:
            item = stored["evidence"]["marginal"]
            if item["status"] == "available":
                continue
            where = f"{which} / {stored['display_name']}"
            block = pb.entry_node(panel, stored["symphony_id"]).find(
                "tenure-review-evidence-marginal"
            )
            text = block.text()
            assert item["reason"] in text, where
            assert words[item["status"]] in text.lower(), where
            bare = _DATE.sub(" ", text)
            for known in (item["label"], item["subject"], item["source"]):
                if isinstance(known, str):
                    bare = bare.replace(known, " ")
            assert not _DECIMAL.search(bare), f"{where}: a figure is shown: {text!r}"
            seen.add(item["reason"])
        assert seen, "the record must hold an item that cannot compare"

    def test_the_cases_the_records_hold_cover_both_kinds_of_item_and_the_fallback_basis(self):
        reasons = set()
        statuses = set()
        for which in ("lone", "apart", "full"):
            for stored in _RECORD_BUILDERS[which]()["entries"]:
                item = stored["evidence"]["marginal"]
                statuses.add(item["status"])
                reasons.add(item["reason"])
        assert {"unavailable", "not_applicable", "available"} <= statuses
        assert any("this basis" in (r or "") for r in reasons)
        assert any("shadow-history basis" in (r or "") for r in reasons)
        assert any(r == "no other eligible symphonies" for r in reasons)


# ===========================================================================
# An item that gives no evidence is listed as missing with its reason, and a row with
# no evidence at all has no verdict and no decision buttons.
# ===========================================================================

_MISSING_RECORDS = ("lone", "solo", "apart", "gap", "full", "small", "edge")


class TestEveryMissingItemIsListedWithItsReason:
    @pytest.mark.parametrize("which", _MISSING_RECORDS)
    def test_each_item_the_record_calls_missing_appears_with_the_reason_stored_for_it(
        self, client, isolated_db, which
    ):
        record = _store_record(which)
        panel = _panel(client)
        listed = not_applicable = 0
        for stored in record["entries"]:
            if not stored["missing_items"]:
                continue
            where = f"{which} / {stored['display_name']}"
            missing = pb.entry_node(panel, stored["symphony_id"]).find("tenure-review-missing")
            assert missing is not None, where
            text = missing.text()
            for name in stored["missing_items"]:
                item = stored["evidence"][name]
                assert item["status"] != "available", f"{where} / {name}: fixture sanity"
                assert item["reason"] in text, f"{where} / {name}: its reason is not listed"
                if item["status"] == "not_applicable":
                    assert "not applicable" in text.lower(), f"{where} / {name}"
                    not_applicable += 1
                listed += 1
        assert listed >= 3
        if which in ("lone", "solo", "apart"):
            assert not_applicable >= 1, "the record must hold a not applicable item"

    @pytest.mark.parametrize("which", ["lone", "solo", "apart", "full"])
    def test_a_row_whose_redundancy_backtest_and_comparison_give_no_evidence_has_no_verdict_and_no_buttons(
        self, client, isolated_db, which
    ):
        record = _store_record(which)
        panel = _panel(client)
        checked = 0
        for stored in record["entries"]:
            evidence = stored["evidence"]
            if any(
                evidence[n]["status"] == "available" for n in ("redundancy", "backtest", "marginal")
            ):
                continue
            if not stored["eligible"]:
                continue
            where = f"{which} / {stored['display_name']}"
            node = pb.entry_node(panel, stored["symphony_id"])
            chip = node.find("tenure-review-verdict-chip")
            assert stored["verdict"] is None, where
            # The chip names the comparison when a panel below it (performance, composite or
            # long-VIX) holds figures, and says no evidence is available only when all are empty.
            holds_figures = any(
                evidence[n]["status"] == "available"
                for n in ("performance", "composite", "long_vix")
            )
            expected = (
                "no verdict: the comparison has no figures yet"
                if holds_figures
                else "no verdict: no evidence available"
            )
            assert expected in chip.text(), (where, chip.text())
            assert "tenure-verdict-chip--none" in chip.classes(), where
            approve, reject = _lifecycle_buttons(node)
            assert approve == [] and reject == [], where
            checked += 1
        assert checked >= 1, "the record must hold a row with no evidence"


# ===========================================================================
# The redundancy item shows what the run stored: a result only on an available item,
# the pair whenever one is stored, and the reason an item decided nothing.
# ===========================================================================

_REDUNDANCY_RECORDS = (
    "full",
    "small",
    "edge",
    "quiet",
    "lone",
    "solo",
    "apart",
    *(f"state:{kind}" for kind in pb.STATE_ROSTERS),
)
_NO_SIBLING = "no redundant sibling"


def _pair_problems(block: pb.Node, pair: dict) -> list[str]:
    """What is missing from a box that shows a stored pair: its sibling, the days compared,
    every figure or the reason it is null, and each gate result beside its own label."""
    problems = []
    text = block.text()
    if pair["sibling_name"] not in text:
        problems.append("the sibling is not named")
    if str(pair["n_obs"]) not in text:
        problems.append("the aligned days are not shown")
    for name in _PAIR_FIGURES:
        figure = pair[name]
        lines = pb.leaf_nodes_containing(block, name.replace("_", " "))
        if figure["value"] is not None:
            if not _shows(block, figure["value"]):
                problems.append(f"{name} is not shown")
        elif not any(figure["reason"] in line.text() for line in lines):
            problems.append(f"{name} has no stored reason beside it")
    return problems + _gate_problems(block, pair)


def _redundancy_problems(stored: dict, block: pb.Node) -> list[str]:
    item = stored["evidence"]["redundancy"]
    text = block.text()
    pair = item.get("pair")
    problems = []
    available = item["status"] == "available"
    claims = _NO_SIBLING in text.lower()
    if available and pair is None and item.get("summary"):
        if not claims:
            problems.append("an available item with no pair does not say there is no sibling")
        if "unavailable" in text.lower():
            problems.append("an item that was checked is worded as unavailable")
    elif claims:
        problems.append(
            "the words 'no redundant sibling' appear on an item that does not carry them"
        )
    if not available and item["reason"] not in text:
        problems.append("the stored reason is not shown")
    if pair:
        problems += _pair_problems(block, pair)
    lines = _not_used_lines(block)
    if "redundancy" in stored["missing_items"]:
        if len(lines) != 1:
            problems.append(f"expected one 'not used in the verdict' line, got {len(lines)}")
        else:
            line = lines[0].text()
            if not re.match(rf"{_NOT_USED}:\s*\S", line, flags=re.IGNORECASE):
                problems.append(f"the line does not carry a reason: {line!r}")
            if item["reason"] not in line:
                problems.append("the line does not carry the item's stored reason")
    elif available and lines:
        problems.append("an item that decided carries a 'not used in the verdict' line")
    return problems


class TestTheRedundancyBoxShowsTheStoredState:
    def test_every_entry_of_every_record_shows_the_state_its_stored_item_holds(
        self, client, isolated_db
    ):
        seen = set()
        problems = []
        for which in _REDUNDANCY_RECORDS:
            record = _store_record(which)
            panel = _panel(client)
            for stored in record["entries"]:
                item = stored["evidence"]["redundancy"]
                block = pb.entry_node(panel, stored["symphony_id"]).find(
                    "tenure-review-evidence-redundancy"
                )
                for problem in _redundancy_problems(stored, block):
                    problems.append(f"{which} / {stored['display_name']}: {problem}")
                seen.add((item["status"], bool(item.get("pair")), bool(item.get("summary"))))
        assert problems == []
        assert seen >= {
            ("available", False, True),
            ("available", True, False),
            ("unavailable", True, False),
            ("unavailable", False, False),
            ("not_applicable", False, False),
        }, f"the records must hold every state of the item: {sorted(seen)}"

    def test_the_check_rejects_a_box_that_says_no_sibling_on_an_unavailable_item(
        self, client, isolated_db
    ):
        """Mutation proof: add the words to the parsed box of an unavailable item and the same
        check reports them."""
        record = _store_record("state:below_floor_no_hit")
        stored = pb.entry_of(record, pb.state_id("below_floor_no_hit", 0))
        block = pb.entry_node(_panel(client), stored["symphony_id"]).find(
            "tenure-review-evidence-redundancy"
        )
        assert _redundancy_problems(stored, block) == []
        block.children.append(f" {_NO_SIBLING} ")
        assert _redundancy_problems(stored, block), "the claim went unnoticed"


# Each case names the state the run stored for one symphony of a one-purpose roster:
# the item's status, whether a pair is shown, and whether it is the item's own result.
_STATE_CASES = [
    pytest.param("thin_coverage", 1, "unavailable", True, id="no composite: thin coverage"),
    pytest.param("flat_window", 1, "unavailable", True, id="no composite: not computable"),
    pytest.param("no_peer_composite", 0, "unavailable", False, id="no same-basis peer composite"),
    pytest.param("below_floor_no_hit", 0, "unavailable", False, id="one day below the floor"),
    pytest.param("below_floor_no_hit", 1, "unavailable", False, id="one day below, other side"),
    pytest.param("at_floor", 0, "available", False, id="exactly the floor"),
    pytest.param("peers_without_composite", 0, "available", False, id="peers without composite"),
    pytest.param("flagged_beside_short_peer", 0, "available", True, id="flagged beside short peer"),
    pytest.param("flagged_beside_short_peer", 2, "unavailable", True, id="unflagged beside short"),
    pytest.param("below_floor_hit", 0, "unavailable", True, id="screen hit below the floor"),
    pytest.param("structural_undefined", 0, "unavailable", True, id="structural gate undefined"),
    pytest.param("interval_not_met", 0, "available", True, id="interval gate not met"),
    pytest.param("structural_not_met", 0, "available", True, id="structural gate not met"),
]


class TestEachNamedRedundancyStateRendersAsStored:
    @pytest.mark.parametrize(("kind", "index", "status", "pair_shown"), _STATE_CASES)
    def test_the_box_follows_the_stored_state(
        self, client, isolated_db, kind, index, status, pair_shown
    ):
        record = _store_record(f"state:{kind}")
        stored = pb.entry_of(record, pb.state_id(kind, index))
        item = stored["evidence"]["redundancy"]
        assert item["status"] == status, "fixture sanity: the roster stores the state it names"
        assert bool(item["pair"]) is pair_shown, "fixture sanity: the pair is shown or not"
        assert ("redundancy" in stored["missing_items"]) is (status != "available")
        block = pb.entry_node(_panel(client), stored["symphony_id"]).find(
            "tenure-review-evidence-redundancy"
        )
        assert _redundancy_problems(stored, block) == []

    def test_a_comparison_below_the_floor_names_the_peer_and_its_days_in_the_stored_reason(
        self, client, isolated_db
    ):
        record = _store_record("state:below_floor_no_hit")
        floor_below = review_builders.days_needed_for_structural_gate() - 1
        for index, peer in ((0, 1), (1, 0)):
            stored = pb.entry_of(record, pb.state_id("below_floor_no_hit", index))
            item = stored["evidence"]["redundancy"]
            peer_name = pb.entry_of(record, pb.state_id("below_floor_no_hit", peer))["display_name"]
            assert f"{peer_name} ({floor_below} aligned days" in item["reason"], "fixture sanity"
            text = (
                pb.entry_node(_panel(client), stored["symphony_id"])
                .find("tenure-review-evidence-redundancy")
                .text()
            )
            assert f"{peer_name} ({floor_below} aligned days" in text
            assert _NO_SIBLING not in text.lower()

    def test_the_pair_beside_an_unused_item_and_the_item_alone_read_the_same_reason(
        self, client, isolated_db
    ):
        """One pair shown and one not: the line is in the box either way."""
        record_with = _store_record("state:below_floor_hit")
        with_pair = pb.entry_of(record_with, pb.state_id("below_floor_hit", 0))
        block = pb.entry_node(_panel(client), with_pair["symphony_id"]).find(
            "tenure-review-evidence-redundancy"
        )
        lines_with = _not_used_lines(block)
        record_alone = _store_record("state:below_floor_no_hit")
        alone = pb.entry_of(record_alone, pb.state_id("below_floor_no_hit", 0))
        block = pb.entry_node(_panel(client), alone["symphony_id"]).find(
            "tenure-review-evidence-redundancy"
        )
        lines_alone = _not_used_lines(block)
        assert (
            with_pair["evidence"]["redundancy"]["pair"]
            and not alone["evidence"]["redundancy"]["pair"]
        )
        assert len(lines_with) == 1 and len(lines_alone) == 1
        assert with_pair["evidence"]["redundancy"]["reason"] in lines_with[0].text()
        assert alone["evidence"]["redundancy"]["reason"] in lines_alone[0].text()


# ===========================================================================
# A shadow series whose first stored row is its own baseline.
# ===========================================================================


class TestAShadowSeriesWhoseFirstRowIsItsOwnBaseline:
    _SHADOW_NOTE = "measured from the previous session's close"

    def _basis_text(self, client, stored) -> str:
        return (
            pb.entry_node(_panel(client), stored["symphony_id"])
            .find("tenure-review-basis")
            .text()
            .replace(chr(0x2019), "'")
        )

    def test_a_first_row_on_the_tenure_start_reads_the_date_of_the_next_row(
        self, client, isolated_db
    ):
        record = _store_record("shadow_start")
        stored = next(e for e in record["entries"] if e["basis"]["series_begins"] is not None)
        window_first = stored["evidence"]["performance"]["window"]["first"]
        began = stored["basis"]["series_begins"]
        assert window_first != began, "fixture sanity: the baseline row is not the first return"
        text = self._basis_text(client, stored)
        assert f"first shadow return {began}" in text
        assert f"first shadow return {window_first}" not in text
        assert self._SHADOW_NOTE in text
        assert "series begins" not in text
        assert f"tenure begins {stored['basis']['tenure_begins']}" in text

    def test_a_series_with_no_first_return_prints_no_date_after_the_phrase_and_no_none(
        self, client, isolated_db
    ):
        record = _store_record("shadow_start")
        stored = next(e for e in record["entries"] if e["basis"]["series_begins"] is None)
        text = self._basis_text(client, stored)
        assert not re.search(r"first shadow return\s*\W*\d{4}-\d{2}-\d{2}", text), text
        assert "series begins" not in text, text
        assert not re.search(r"\bNone\b|\bnull\b", text), text
        assert f"tenure begins {stored['basis']['tenure_begins']}" in text
        assert not re.search(r"\bNone\b", _panel(client).everything())
        own_baseline = stored["basis"]["tenure_begins"]
        assert f"first shadow return {own_baseline}" not in text, "the baseline row is not a return"


# ===========================================================================
# A stored reason the plan does not list is the run's own record: it is shown as stored,
# escaped, and never replaced by "not recorded".
# ===========================================================================

_NOVEL_REASON = 'a reason the run wrote <i data-poison="1">in markup</i> & more'
_REASON_VICTIMS = [
    pytest.param("keep", "backtest", id="a backtest that was not run"),
    pytest.param("keep", "long_vix", id="holdings that were unavailable"),
    pytest.param("steady", "composite", id="a composite that is not computable"),
    pytest.param("short pair", "redundancy", id="a redundancy item that decides nothing"),
]


class TestAStoredReasonIsShownAsStoredAndEscaped:
    @pytest.mark.parametrize(("victim", "name"), _REASON_VICTIMS)
    def test_the_reason_reaches_the_item_and_the_missing_list_escaped(
        self, client, isolated_db, victim, name
    ):
        which, pick = _VICTIMS[victim]
        _seed_roster(which)
        record = _RECORD_BUILDERS[which](run_id=pb.RUN_ID_SECOND)
        entry = pick(record)
        assert entry["evidence"][name]["status"] == "unavailable", "fixture sanity"
        _edit(entry, f"evidence.{name}.reason", _NOVEL_REASON)
        pb.store_run(record)
        holder = {"id": entry["symphony_id"]}
        body = _page(client)
        assert '<i data-poison="1">' not in body, "the stored text reached the page unescaped"
        node = pb.entry_node(pb.panel_of(body), holder["id"])
        block = node.find(f"tenure-review-evidence-{name}")
        assert _NOVEL_REASON in block.text()
        assert _NOT_RECORDED not in block.text().lower()
        missing = node.find("tenure-review-missing")
        assert missing is not None and _NOVEL_REASON in missing.text()
        if name == "redundancy":
            lines = _not_used_lines(block)
            assert len(lines) == 1, [n.text() for n in lines]
            assert _NOVEL_REASON in lines[0].text(), "the not-used line lost the stored reason"


# ===========================================================================
# An unavailable redundancy item that shows its pair names the pair's subject and source,
# because every figure the panel shows carries both.
# ===========================================================================

_PAIR_ON_AN_UNAVAILABLE_ITEM = (
    pytest.param("small", pb.SMALL_IDS["first"], id="short pair"),
    pytest.param("edge", pb.EDGE_IDS["twin_one"], id="undefined interval, first twin"),
    pytest.param("edge", pb.EDGE_IDS["twin_two"], id="undefined interval, second twin"),
    pytest.param(
        "state:structural_undefined",
        pb.state_id("structural_undefined", 0),
        id="structural gate undefined",
    ),
    pytest.param(
        "state:below_floor_hit", pb.state_id("below_floor_hit", 0), id="screen hit below the floor"
    ),
)


class TestAnUnavailableItemThatShowsAPairNamesItsSubjectAndSource:
    @pytest.mark.parametrize(("which", "symphony_id"), _PAIR_ON_AN_UNAVAILABLE_ITEM)
    def test_the_box_carries_the_stored_subject_and_source_beside_the_pair(
        self, client, isolated_db, which, symphony_id
    ):
        record = _store_record(which)
        item = pb.entry_of(record, symphony_id)["evidence"]["redundancy"]
        assert item["status"] == "unavailable" and item["pair"], (
            "fixture sanity: a pair, no verdict"
        )
        assert item["subject"] and item["source"], "fixture sanity: the item stores both"
        block = pb.entry_node(_panel(client), symphony_id).find("tenure-review-evidence-redundancy")
        assert item["pair"]["sibling_name"] in block.text(), "the pair is not shown"
        for word in ("subject", "source"):
            lines = [
                leaf
                for leaf in pb.leaf_nodes_containing(block, item[word])
                if leaf.text().lower().startswith(word)
            ]
            assert len(lines) == 1, f"the {word} of the pair is not on a line of its own"

    def test_the_stored_subject_is_the_one_printed_and_not_a_fixed_phrase(
        self, client, isolated_db
    ):
        victim = pb.SMALL_IDS["first"]

        def edit(record):
            _edit(pb.entry_of(record, victim), "evidence.redundancy.subject", "an edited subject")
            _edit(pb.entry_of(record, victim), "evidence.redundancy.source", "an edited source")

        _, _, panel = _render_edited(client, "small", edit)
        text = pb.entry_node(panel, victim).find("tenure-review-evidence-redundancy").text()
        assert "subject: an edited subject" in text and "source: an edited source" in text


# ===========================================================================
# A stored number too large for a float is still a finite whole number: the page prints
# its digits or says "not recorded", and never answers a server error.
# ===========================================================================

_HUGE = 10**400


def _huge_case(case_id, victim, edits, scope):
    return pytest.param(victim, tuple(edits), scope, id=case_id)


_HUGE_CASES = [
    _huge_case(
        "threshold in effect",
        "watch",
        [("__run__.threshold_in_effect", _HUGE)],
        _header("tenure-review-threshold"),
    ),
    _huge_case(
        "tenure", "watch", [("tenure_trading_days", _HUGE)], ("testid", "tenure-review-tenure")
    ),
    _huge_case(
        "window with as many days as sessions",
        "watch",
        [
            ("evidence.performance.window.days", _HUGE),
            ("evidence.performance.window.sessions", _HUGE),
        ],
        _line("performance", "window"),
    ),
    _huge_case(
        "window with fewer days than sessions",
        "watch",
        [
            ("evidence.performance.window.days", _HUGE - 1),
            ("evidence.performance.window.sessions", _HUGE),
        ],
        _line("performance", "window"),
    ),
    _huge_case(
        "aligned days",
        "watch",
        [("evidence.redundancy.pair.n_obs", _HUGE)],
        _line("redundancy", "aligned days"),
    ),
    _huge_case(
        "population",
        "watch",
        [("evidence.composite.population_size", _HUGE)],
        _block("composite"),
    ),
    _huge_case(
        "figure",
        "watch",
        [("evidence.performance.figures.sharpe.value", _HUGE)],
        _line("performance", "sharpe"),
    ),
    _huge_case(
        "negative figure",
        "watch",
        [("evidence.performance.figures.sharpe.value", -_HUGE)],
        _line("performance", "sharpe"),
    ),
    _huge_case(
        "figure counted in days",
        "steady",
        [("evidence.long_vix.figures.held_days.value", _HUGE)],
        _line("long_vix", "held days"),
    ),
]


class TestAnIntegerTooLargeForAFloatDoesNotTakeThePageDown:
    @pytest.mark.parametrize(("victim", "edits", "scope"), _HUGE_CASES)
    def test_the_field_prints_its_digits_or_says_not_recorded_and_the_page_answers_200(
        self, client, isolated_db, victim, edits, scope
    ):
        which, pick = _VICTIMS[victim]
        holder = {}
        stored_values = []

        def edit(record):
            entry = pick(record)
            holder["id"] = entry["symphony_id"]
            for path, value in edits:
                stored_values.append(value)
                if path.startswith("__run__."):
                    _edit(record, path.removeprefix("__run__."), value)
                else:
                    _edit(entry, path, value)

        _, pristine, panel = _render_edited(client, which, edit)
        assert not re.search(r"\bNone\b", panel.everything())
        scoped = _scoped(panel, holder["id"], scope)
        assert scoped is not None, "the line that prints the field is dropped, not worded"
        text = scoped.text()
        digits = [str(abs(value)) for value in stored_values]
        assert _NOT_RECORDED in text.lower() or all(d in text for d in digits), (
            f"the field is neither worded as not recorded nor printed whole: {text[:200]!r}"
        )
        _assert_others_unchanged(panel, pristine, holder["id"], scope)
        resp = client.get("/api/retirement-recommendations")
        assert resp.status_code == 200
        assert pb.strict_json(resp)["review_run"] is not None


# ===========================================================================
# An entry whose stored symphony_id cannot be a dictionary key leaves the page and the API
# at 200, gets no decision and no buttons, and changes no other entry.
# ===========================================================================

_UNKEYABLE_IDS = [
    pytest.param(["abc"], id="a list"),
    pytest.param({"k": "v"}, id="a dict"),
    pytest.param([["abc"]], id="a list of lists"),
    pytest.param(987654, id="a number"),
    pytest.param(None, id="null"),
    pytest.param(_GONE, id="missing"),
    pytest.param("", id="empty"),
    pytest.param(_BLANK, id="blank"),
]


# A stored explanation the keyless entry must not print: the entry is a retire candidate, so
# its lifecycle block would show it if the block rendered.
_KEYLESS_EXPLANATION = "Redundant with its stronger sibling, written for the keyless entry."


class TestAnEntryWhoseIdCannotBeAKeyDegradesOnlyThatEntry:
    @pytest.fixture
    def stored(self, client, isolated_db, request):
        """A full run whose retire-candidate entry carries the id under test, with one
        decision made on another entry; returns (record, the broken entry)."""
        pb.seed_bot_state()
        record = pb.full_record(pb.RUN_ID_SECOND)
        broken = pb.entry_with_verdict(record, "retire_candidate")
        pb.set_explanation(record, broken["symphony_id"], _KEYLESS_EXPLANATION, "2026-12-31")
        _edit(broken, "symphony_id", request.param)
        pb.store_run(record)
        watch = pb.entry_with_verdict(record, "watch")
        db_module.upsert_retirement_decision(watch["symphony_id"], approval_status="approved")
        return record, broken

    @pytest.mark.parametrize("stored", _UNKEYABLE_IDS, indirect=True)
    def test_the_page_answers_200_and_the_entry_has_no_decision_and_no_buttons(
        self, client, stored
    ):
        record, broken = stored
        panel = _panel(client)
        nodes = panel.find_all("tenure-review-entry")
        assert len(nodes) == len(record["entries"]), "an entry was dropped"
        keyless = [n for n in nodes if n.attrs.get("data-symphony-id", "").strip() == ""]
        assert len(keyless) == 1, "the entry with the unusable id is not shown once"
        assert broken["display_name"] in keyless[0].text()
        assert _lifecycle_buttons(keyless[0]) == ([], [])
        assert not re.search(r"\bNone\b", keyless[0].everything())
        watch = pb.entry_with_verdict(record, "watch")
        chip = pb.entry_node(panel, watch["symphony_id"]).find("retirement-rec-status")
        assert chip is not None and "approved" in chip.text().lower(), (
            "another entry's decision was lost"
        )

    @pytest.mark.parametrize("stored", _UNKEYABLE_IDS, indirect=True)
    def test_the_entry_renders_no_lifecycle_block_status_chip_or_explanation(self, client, stored):
        """An id that is not non-blank text is a missing id for the whole entry, not only for
        its buttons: the entry keeps its name and evidence and shows no decision state."""
        record, broken = stored
        nodes = _panel(client).find_all("tenure-review-entry")
        keyless = [n for n in nodes if n.attrs.get("data-symphony-id", "").strip() == ""]
        assert len(keyless) == 1, "the entry with the unusable id is not shown once"
        node = keyless[0]
        assert broken["display_name"] in node.text(), "the entry lost its name"
        assert node.find("retirement-recommendation-card") is None, "a lifecycle block renders"
        assert node.find("retirement-rec-status") is None, "a status chip renders"
        assert node.find("retirement-rec-explanation") is None, "an explanation line renders"
        assert _KEYLESS_EXPLANATION not in node.everything(), "the stored explanation is printed"
        assert _lifecycle_buttons(node) == ([], [])

    @pytest.mark.parametrize("stored", _UNKEYABLE_IDS, indirect=True)
    def test_the_api_answers_200_with_no_decision_on_that_entry_and_the_others_joined(
        self, client, stored
    ):
        record, broken = stored
        resp = client.get("/api/retirement-recommendations")
        assert resp.status_code == 200
        data = pb.strict_json(resp)
        assert data["review_read_failed"] is False
        assert len(data["review_entries"]) == len(record["entries"])
        by_name = {e["display_name"]: e for e in data["review_entries"]}
        assert by_name[broken["display_name"]].get("approval_status") is None
        watch = pb.entry_with_verdict(record, "watch")
        assert by_name[watch["display_name"]]["approval_status"] == "approved"


# ===========================================================================
# A stored run whose entries are not a list cannot be read: it is not a completed run
# with no live symphonies.
# ===========================================================================

_NOT_A_LIST = [
    pytest.param(_GONE, id="missing"),
    pytest.param(None, id="null"),
    pytest.param("garbage", id="text"),
    pytest.param(7, id="a number"),
    pytest.param({"a": 1}, id="a dict"),
]


class TestARunWhoseEntriesAreNotAListSaysTheReviewCannotBeRead:
    @pytest.fixture
    def stored(self, isolated_db, request):
        _seed_symphony_roster("cand-legacy", "sib-legacy")
        _seed_recommendation(candidate_id="cand-legacy", sibling_id="sib-legacy")
        pb.seed_bot_state()
        record = pb.full_record()
        _edit(record, "entries", request.param)
        pb.store_run(record)

    @pytest.mark.parametrize("stored", _NOT_A_LIST, indirect=True)
    def test_the_panel_says_the_review_is_unavailable_and_not_that_it_has_no_symphonies(
        self, client, stored
    ):
        panel = _panel(client)
        failed = panel.find("tenure-review-read-failed")
        assert failed is not None and "review unavailable" in failed.text().lower()
        assert panel.find("tenure-review-no-symphonies") is None
        assert "no live symphonies" not in panel.everything().lower()
        assert panel.find("tenure-review-as-of") is None, "the header of an unreadable run"
        assert panel.find_all("tenure-review-entry") == []
        assert panel.find_all("retirement-recommendation-card") == []
        assert panel.find("tenure-review-run-btn") is not None

    def test_a_run_whose_entries_are_an_empty_list_is_still_a_run_with_no_live_symphonies(
        self, client, isolated_db
    ):
        pb.store_run(pb.empty_record())
        panel = _panel(client)
        assert panel.find("tenure-review-no-symphonies") is not None
        assert panel.find("tenure-review-read-failed") is None


# ===========================================================================
# The rows use the classes the design mapping names for them, instead of restating the
# same styles under classes of their own.
# ===========================================================================

_GATE_LABELS = ("uncertainty gate", "structural redundancy gate")


def _leaf_lines(block: pb.Node, prefixes: tuple) -> list[pb.Node]:
    """The elements below ``block`` with no element inside them whose text starts with one
    of ``prefixes`` (a line of the box, as opposed to the box)."""
    return [
        n
        for n in block.walk()
        if not any(isinstance(c, pb.Node) for c in n.children)
        and n.text().lower().startswith(prefixes)
    ]


class TestTheRowsUseTheMappedClasses:
    @pytest.fixture
    def shown(self, client, isolated_db):
        record = _store_record("full")
        return record, _panel(client)

    def test_the_display_name_sits_in_an_element_carrying_the_pair_class(self, shown):
        record, panel = shown
        for stored in record["entries"]:
            entry = pb.entry_node(panel, stored["symphony_id"])
            name = entry.find("tenure-review-name")
            chain, node = [name], name.parent
            while node is not None and node is not entry:
                chain.append(node)
                node = node.parent
            assert any("retirement-rec-pair" in n.classes() for n in chain), (
                f"{stored['display_name']}: no element holding the name carries the pair class"
            )

    def test_the_tenure_and_eligibility_lines_carry_the_meta_class(self, shown):
        record, panel = shown
        checked = 0
        for stored in record["entries"]:
            entry = pb.entry_node(panel, stored["symphony_id"])
            for testid in ("tenure-review-tenure", "tenure-review-eligibility"):
                line = entry.find(testid)
                if line is not None:
                    assert "retirement-rec-meta" in line.classes(), (stored["display_name"], testid)
                    checked += 1
        assert checked >= 6

    def test_the_provenance_and_status_lines_of_every_box_carry_the_meta_class(self, shown):
        record, panel = shown
        prefixes = (*_META_LINES, "not compared")
        checked = 0
        for stored in record["entries"]:
            entry = pb.entry_node(panel, stored["symphony_id"])
            for name in _PANEL_ITEMS:
                for line in _leaf_lines(entry.find(f"tenure-review-evidence-{name}"), prefixes):
                    assert "retirement-rec-meta" in line.classes(), (name, line.text())
                    checked += 1
        assert checked >= 30

    def test_the_gate_results_and_the_figures_carry_the_gates_class(self, shown):
        record, panel = shown
        gates = figures = 0
        for stored in record["entries"]:
            entry = pb.entry_node(panel, stored["symphony_id"])
            redundancy = entry.find("tenure-review-evidence-redundancy")
            for line in _leaf_lines(redundancy, _GATE_LABELS):
                assert "retirement-rec-gates" in line.classes(), line.text()
                gates += 1
            performance = stored["evidence"]["performance"]
            if performance["status"] != "available":
                continue
            labels = tuple(name.replace("_", " ") for name in performance["figures"])
            block = entry.find("tenure-review-evidence-performance")
            for line in _leaf_lines(block, labels):
                assert "retirement-rec-gates" in line.classes(), line.text()
                figures += 1
        assert gates >= 6 and figures >= 12


class TestALegacyRecommendationWhoseIdCannotBeAKeyDoesNotTakeThePageDown:
    """The decisions join serves the legacy recommendation cards too (keyed by candidate_id)."""

    @pytest.mark.parametrize("bad_id", [["abc"], {"k": "v"}], ids=["a list", "a dict"])
    def test_the_page_and_the_api_answer_200(self, client, isolated_db, bad_id):
        _seed_symphony_roster("cand-legacy", "sib-legacy")
        db_module.insert_advisor_observation(
            advisor_role="RETIREMENT_RECOMMENDATION",
            subject_type="symphony",
            subject_id="cand-legacy",
            symphony_id="cand-legacy",
            verdict="retire_candidate",
            raw_response=_sample_raw_response(bad_id, "sib-legacy"),
        )
        assert client.get("/ai-advisor").status_code == 200
        assert client.get("/api/retirement-recommendations").status_code == 200


# ===========================================================================
# A verdict chip whose text reads "not recorded" has no verdict colour to show: it takes the
# neutral chip style whatever verdict the entry holds.
# ===========================================================================

_VERDICT_MODIFIERS = (
    "tenure-verdict-chip--retire_candidate",
    "tenure-verdict-chip--watch",
    "tenure-verdict-chip--keep",
)
_UNUSABLE_CHIP_TEXTS = [
    pytest.param(_GONE, id="missing"),
    pytest.param(None, id="null"),
    pytest.param(_LIST, id="a list"),
    pytest.param(987654, id="a number"),
    pytest.param(_BLANK, id="blank"),
]


class TestAVerdictChipThatReadsNotRecordedTakesTheNeutralStyle:
    @pytest.mark.parametrize("verdict", ["retire_candidate", "watch", "keep"])
    @pytest.mark.parametrize("unusable", _UNUSABLE_CHIP_TEXTS)
    def test_the_chip_reads_not_recorded_in_the_neutral_style_whatever_the_verdict(
        self, client, isolated_db, verdict, unusable
    ):
        holder = {}

        def edit(record):
            victim = pb.entry_with_verdict(record, verdict)
            holder["id"] = victim["symphony_id"]
            _edit(victim, "verdict_text", unusable)

        _, _, panel = _render_edited(client, "full", edit)
        chip = pb.entry_node(panel, holder["id"]).find("tenure-review-verdict-chip")
        assert chip is not None
        assert chip.text().lower() == _NOT_RECORDED, chip.text()
        assert "tenure-verdict-chip--none" in chip.classes(), chip.classes()
        assert not set(_VERDICT_MODIFIERS) & set(chip.classes()), (
            f"a chip with no verdict text wears the colour of the verdict {verdict}"
        )

    @pytest.mark.parametrize("verdict", ["retire_candidate", "watch", "keep"])
    def test_a_chip_with_its_text_keeps_the_style_of_its_verdict(
        self, client, isolated_db, verdict
    ):
        record = _stored_full_run()
        stored = pb.entry_with_verdict(record, verdict)
        shown = pb.entry_node(_panel(client), stored["symphony_id"]).find(
            "tenure-review-verdict-chip"
        )
        assert f"tenure-verdict-chip--{verdict}" in shown.classes()


# ===========================================================================
# One wording for an unreadable time: the run line reads the way the notices do.
# ===========================================================================

_UNREADABLE_RUN_TIMES = [
    pytest.param(_GONE, id="missing"),
    pytest.param(None, id="null"),
    pytest.param("not a timestamp", id="text"),
    pytest.param(12345, id="a number"),
    pytest.param("", id="empty"),
    pytest.param(_BLANK, id="blank"),
]


class TestAnUnreadableRunTimeReadsLikeEveryOtherUnreadableTime:
    @pytest.mark.parametrize("unusable", _UNREADABLE_RUN_TIMES)
    def test_the_run_line_says_the_time_is_not_recorded(self, client, isolated_db, unusable):
        pb.seed_bot_state()
        record = pb.full_record()
        _edit(record, "completed_at", unusable)
        pb.store_run(record)
        text = _panel(client).find("tenure-review-run-time").text().lower()
        assert text.startswith("run completed"), text
        assert _TIME_NOT_RECORDED in text, text
        assert "unreadable" not in text, "a second wording for the same state"
        assert not re.search(r"\bNone\b", text)
        if isinstance(unusable, str) and unusable.strip():
            assert unusable not in text

    @pytest.mark.parametrize("unusable", _UNREADABLE_RUN_TIMES)
    def test_the_run_line_and_a_failed_attempt_notice_use_the_same_words(
        self, client, isolated_db, monkeypatch, unusable
    ):
        pb.seed_bot_state()
        record = pb.full_record()
        _edit(record, "completed_at", unusable)
        pb.store_run(record)
        _run_state(
            monkeypatch,
            last_failed_attempt={"reason": "Composer statistics unavailable", "at": None},
        )
        panel = _panel(client)
        run_line = panel.find("tenure-review-run-time").text().lower()
        notice = panel.find("tenure-review-failed-notice").text().lower()
        assert _TIME_NOT_RECORDED in run_line and _TIME_NOT_RECORDED in notice, (run_line, notice)


# ===========================================================================
# A figure reads in the words of the plan: a percent figure carries the percent sign,
# a figure with no value reads "not computable", and a value that rounds to zero prints
# 0.00 and never -0.00.
# ===========================================================================


def _figure_lines(block: pb.Node, label: str) -> list[pb.Node]:
    return [
        n
        for n in pb.leaf_nodes_containing(block, label)
        if n.text().lower().startswith(f"{label}:")
    ]


class TestAPercentFigureCarriesThePercentSign:
    def test_every_percent_figure_is_the_number_and_the_sign_and_never_the_word(
        self, client, isolated_db
    ):
        checked = 0
        for which in ("full", "edge", "small", "quiet"):
            record = _store_record(which)
            panel = _panel(client)
            for stored in record["entries"]:
                node = pb.entry_node(panel, stored["symphony_id"])
                for name in _PANEL_ITEMS:
                    item = stored["evidence"][name]
                    if item["status"] != "available":
                        continue
                    block = node.find(f"tenure-review-evidence-{name}")
                    for fig_name, fig in item["figures"].items():
                        if fig["unit"].lower() != "percent" or fig["value"] is None:
                            continue
                        label = fig_name.replace("_", " ")
                        where = f"{which} / {stored['display_name']} / {fig_name}"
                        lines = _figure_lines(block, label)
                        assert len(lines) == 1, where
                        text = lines[0].text()
                        assert any(
                            re.search(rf"{re.escape(form)}%(?![\w%])", text)
                            for form in _two_decimal_forms(_printed_figure(fig_name, fig["value"]))
                        ), (where, text)
                        assert not re.search(r"\bpercent\b", text.lower()), (where, text)
                        if _is_drawdown_leg(fig_name) and fig["value"] < 0:
                            magnitude = _two_decimal_forms(abs(fig["value"]))[0]
                            assert not re.search(rf"[-{_MINUS}]{re.escape(magnitude)}", text), (
                                f"{where}: a drawdown prints its magnitude, no minus sign",
                                text,
                            )
                        checked += 1
        assert checked >= 20, f"the records must hold many percent figures, got {checked}"

    def test_a_percent_figure_that_is_zero_reads_zero_point_zero_zero_percent(
        self, client, isolated_db
    ):
        holder = {}

        def edit(record):
            victim = pb.entry_with_verdict(record, "watch")
            holder["id"] = victim["symphony_id"]
            _edit(victim, "evidence.performance.figures.max_drawdown.value", 0.0)

        _, _, panel = _render_edited(client, "full", edit)
        block = pb.entry_node(panel, holder["id"]).find("tenure-review-evidence-performance")
        lines = _figure_lines(block, "max drawdown")
        assert len(lines) == 1
        assert re.fullmatch(r"max drawdown: 0\.00%", lines[0].text()), lines[0].text()

    def test_a_unit_that_is_not_percent_keeps_its_words(self, client, isolated_db):
        record = _store_record("full")
        watch = pb.entry_with_verdict(record, "watch")
        block = pb.entry_node(_panel(client), watch["symphony_id"]).find(
            "tenure-review-evidence-marginal"
        )
        lines = _figure_lines(block, "total return difference")
        assert len(lines) == 1
        assert "percentage points" in lines[0].text(), lines[0].text()


class TestAFigureWithNoValueReadsNotComputable:
    def test_no_figure_line_of_any_record_says_not_computed(self, client, isolated_db):
        """A stored reason may itself say a value was not computed; the figure line's own
        wording is what retired."""
        retired = re.compile(r"^[a-z][a-z ]*: not computed\b")
        checked = 0
        for which in ("full", "edge", "small", "quiet", "gap", "lone", "solo", "apart"):
            _store_record(which)
            panel = _panel(client)
            for node in panel.walk():
                if any(isinstance(child, pb.Node) for child in node.children):
                    continue
                text = node.text().lower()
                assert not retired.match(text), f"{which}: the retired wording is back: {text!r}"
                checked += text.startswith("holdings overlap:")
        assert checked >= 3, "the records must hold figure lines to read"

    def test_each_null_figure_starts_not_computable_and_keeps_its_stored_reason(
        self, client, isolated_db
    ):
        record = _store_record("edge")
        panel = _panel(client)
        checked = 0
        for stored in record["entries"]:
            node = pb.entry_node(panel, stored["symphony_id"])
            for name in _PANEL_ITEMS:
                item = stored["evidence"][name]
                if item["status"] != "available" and not item.get("pair"):
                    continue
                block = node.find(f"tenure-review-evidence-{name}")
                for fig_name, reason in _null_figures(item):
                    label = fig_name.replace("_", " ")
                    where = f"{stored['display_name']} / {name} / {fig_name}"
                    lines = [n for n in _figure_lines(block, label) if reason in n.text()]
                    assert len(lines) == 1, where
                    assert lines[0].text().lower().startswith(f"{label}: not computable"), (
                        where,
                        lines[0].text(),
                    )
                    checked += 1
        assert checked >= 10

    def test_an_undefined_calmar_reads_not_computable_beside_the_figures_that_have_values(
        self, client, isolated_db
    ):
        record = _store_record("edge")
        steady = pb.entry_of(record, pb.EDGE_IDS["steady"])
        figures = steady["evidence"]["performance"]["figures"]
        assert figures["calmar"]["value"] is None, "fixture sanity: an undefined Calmar"
        block = pb.entry_node(_panel(client), steady["symphony_id"]).find(
            "tenure-review-evidence-performance"
        )
        calmar = _figure_lines(block, "calmar")
        assert len(calmar) == 1 and calmar[0].text().lower().startswith("calmar: not computable")
        assert _shows(block, figures["total_return"]["value"])


# (item, the label the line starts with, the path of the stored value inside the entry)
_ZERO_ROUNDING_TARGETS = [
    pytest.param(
        "performance",
        "total return",
        "evidence.performance.figures.total_return.value",
        id="a percent figure",
    ),
    pytest.param(
        "marginal",
        "max drawdown difference",
        "evidence.marginal.figures.max_drawdown_difference.value",
        id="a figure in percentage points",
    ),
    pytest.param(
        "redundancy",
        "stressed correlation",
        "evidence.redundancy.pair.stressed_correlation.value",
        id="a figure of the pair",
    ),
]
_ROUNDS_TO_ZERO = [
    pytest.param(-0.0, id="negative zero"),
    pytest.param(0.0, id="zero"),
    pytest.param(-0.0017296, id="a small negative"),
    pytest.param(0.0017296, id="a small positive"),
    pytest.param(-0.004999, id="just short of the rounding edge"),
    pytest.param(-1e-12, id="far below the second decimal"),
]
_DOES_NOT_ROUND_TO_ZERO = [
    pytest.param(-0.0051, "-0.01", id="just past the rounding edge"),
    pytest.param(-0.5, "-0.50", id="half a point down"),
    pytest.param(0.0051, "0.01", id="just past the edge, positive"),
]


def _signed_forms(text: str) -> list[str]:
    return [text, text.replace("-", _MINUS)]


class TestAValueThatRoundsToZeroPrintsZero:
    @pytest.mark.parametrize(("item", "label", "path"), _ZERO_ROUNDING_TARGETS)
    @pytest.mark.parametrize("value", _ROUNDS_TO_ZERO)
    def test_the_line_reads_zero_point_zero_zero_with_no_minus_sign(
        self, client, isolated_db, item, label, path, value
    ):
        holder = {}

        def edit(record):
            victim = pb.entry_with_verdict(record, "watch")
            holder["id"] = victim["symphony_id"]
            _edit(victim, path, value)

        _, _, panel = _render_edited(client, "full", edit)
        block = pb.entry_node(panel, holder["id"]).find(f"tenure-review-evidence-{item}")
        lines = _figure_lines(block, label)
        assert len(lines) == 1, [n.text() for n in lines]
        text = lines[0].text()
        assert re.match(rf"{re.escape(label)}:\s*0\.00(?!\d)", text), text
        assert not re.search(rf"[-{_MINUS}]\s*0\.00", text), f"a negative zero is printed: {text!r}"

    @pytest.mark.parametrize(("item", "label", "path"), _ZERO_ROUNDING_TARGETS)
    @pytest.mark.parametrize(("value", "expected"), _DOES_NOT_ROUND_TO_ZERO)
    def test_a_value_that_does_not_round_to_zero_keeps_its_sign_and_digits(
        self, client, isolated_db, item, label, path, value, expected
    ):
        holder = {}

        def edit(record):
            victim = pb.entry_with_verdict(record, "watch")
            holder["id"] = victim["symphony_id"]
            _edit(victim, path, value)

        _, _, panel = _render_edited(client, "full", edit)
        block = pb.entry_node(panel, holder["id"]).find(f"tenure-review-evidence-{item}")
        lines = _figure_lines(block, label)
        assert len(lines) == 1
        text = lines[0].text()
        gap_name = label.replace(" ", "_")
        if gap_name in _DRAWDOWN_GAPS:
            # A gap between drawdown legs prints its size, with the words that carry the direction.
            size = f"{abs(round(-value, 2)):.2f}"
            assert re.match(rf"{re.escape(label)}:\s*{re.escape(size)}(?!\d)", text), text
            assert _gap_words(gap_name, value) in text, text
            return
        assert any(
            re.match(rf"{re.escape(label)}:\s*{re.escape(form)}(?!\d)", text)
            for form in _signed_forms(expected)
        ), text


# ===========================================================================
# A redundancy item that shows no pair prints its reason once, on the line that says the
# item is not used in the verdict, beside a line that names its status.
# ===========================================================================


class TestARedundancyItemThatShowsNoPairStatesItsReasonOnce:
    def test_the_reason_is_on_one_line_and_the_status_is_named_on_another(
        self, client, isolated_db
    ):
        checked = decided_nothing = 0
        problems = []
        for which in _REDUNDANCY_RECORDS:
            record = _store_record(which)
            panel = _panel(client)
            for stored in record["entries"]:
                item = stored["evidence"]["redundancy"]
                if item["status"] == "available" or item.get("pair"):
                    continue
                where = f"{which} / {stored['display_name']}"
                block = pb.entry_node(panel, stored["symphony_id"]).find(
                    "tenure-review-evidence-redundancy"
                )
                reason = item["reason"]
                # the box label may read like the reason; it is not a line that states one
                carrying = [
                    n
                    for n in pb.leaf_nodes_containing(block, reason)
                    if not n.text().lower().startswith(("not compared", "left out"))
                    and "retirement-rec-basis" not in n.classes()
                ]
                if len(carrying) != 1 or carrying[0].text().count(reason) != 1:
                    problems.append(f"{where}: reason on {[n.text() for n in carrying]}")
                    continue
                status_word = "unavailable" if item["status"] == "unavailable" else "not applicable"
                if "redundancy" in stored["missing_items"]:
                    decided_nothing += 1
                    if not carrying[0].text().lower().startswith(_NOT_USED):
                        problems.append(f"{where}: the reason is not on the not-used line")
                    status_lines = [
                        n for n in _leaf_lines(block, (status_word,)) if n is not carrying[0]
                    ]
                    if not status_lines:
                        problems.append(f"{where}: no line names the status {status_word!r}")
                elif not carrying[0].text().lower().startswith(status_word):
                    problems.append(f"{where}: the status line does not carry the reason")
                checked += 1
        assert problems == []
        assert checked >= 20 and decided_nothing >= 10, (checked, decided_nothing)

    def test_an_item_that_shows_a_pair_still_states_its_reason_on_the_not_used_line(
        self, client, isolated_db
    ):
        record = _store_record("small")
        stored = pb.entry_of(record, pb.SMALL_IDS["first"])
        item = stored["evidence"]["redundancy"]
        assert item["pair"] and item["status"] == "unavailable", "fixture sanity"
        block = pb.entry_node(_panel(client), stored["symphony_id"]).find(
            "tenure-review-evidence-redundancy"
        )
        lines = _not_used_lines(block)
        assert len(lines) == 1 and item["reason"] in lines[0].text()

    def test_an_item_that_shows_a_pair_keeps_its_reason_on_its_status_line_too(
        self, client, isolated_db
    ):
        checked = 0
        problems = []
        for which in _REDUNDANCY_RECORDS:
            record = _store_record(which)
            panel = _panel(client)
            for stored in record["entries"]:
                item = stored["evidence"]["redundancy"]
                if (
                    item["status"] == "available"
                    or not item.get("pair")
                    or "redundancy" not in stored["missing_items"]
                ):
                    continue
                where = f"{which} / {stored['display_name']}"
                block = pb.entry_node(panel, stored["symphony_id"]).find(
                    "tenure-review-evidence-redundancy"
                )
                status_word = "unavailable" if item["status"] == "unavailable" else "not applicable"
                status_lines = _leaf_lines(block, (status_word,))
                if len(status_lines) != 1 or item["reason"] not in status_lines[0].text():
                    problems.append(f"{where}: status line {[n.text() for n in status_lines]}")
                not_used = _not_used_lines(block)
                if len(not_used) != 1 or item["reason"] not in not_used[0].text():
                    problems.append(f"{where}: not-used line {[n.text() for n in not_used]}")
                checked += 1
        assert problems == []
        assert checked >= 1, "no record holds a listed-missing redundancy item that shows a pair"

    def test_the_stored_reason_is_the_one_printed_once(self, client, isolated_db):
        reason = "a stored reason no template carries"
        _seed_roster("state:no_peer_composite")
        record = _RECORD_BUILDERS["state:no_peer_composite"](run_id=pb.RUN_ID_SECOND)
        victim = pb.entry_of(record, pb.state_id("no_peer_composite", 0))
        assert victim["evidence"]["redundancy"]["pair"] is None, "fixture sanity"
        _edit(victim, "evidence.redundancy.reason", reason)
        pb.store_run(record)
        block = pb.entry_node(_panel(client), victim["symphony_id"]).find(
            "tenure-review-evidence-redundancy"
        )
        assert block.text().count(reason) == 1, block.text()


# ===========================================================================
# An unavailable or not applicable item that shows a window also prints its stored
# subject and source, because every figure and window the panel shows carries both.
# ===========================================================================


class TestAnItemThatShowsAWindowNamesItsSubjectAndSource:
    def test_every_unavailable_item_with_a_window_has_a_subject_line_and_a_source_line(
        self, client, isolated_db
    ):
        checked = 0
        problems = []
        for which in _REDUNDANCY_RECORDS:
            record = _store_record(which)
            panel = _panel(client)
            for stored in record["entries"]:
                node = pb.entry_node(panel, stored["symphony_id"])
                for name in _PANEL_ITEMS:
                    item = stored["evidence"][name]
                    if item["status"] == "available" or not isinstance(item.get("window"), dict):
                        continue
                    block = node.find(f"tenure-review-evidence-{name}")
                    for word in ("subject", "source"):
                        lines = [
                            n
                            for n in pb.leaf_nodes_containing(block, item[word])
                            if n.text().lower().startswith(f"{word}:")
                        ]
                        if len(lines) != 1:
                            problems.append(f"{which} / {stored['display_name']} / {name}: {word}")
                    checked += 1
        assert problems == []
        assert checked >= 30, (
            f"the records must hold many unavailable items with a window: {checked}"
        )

    def test_the_stored_subject_and_source_are_the_ones_printed(self, client, isolated_db):
        holder = {}

        def edit(record):
            victim = pb.entry_with_verdict(record, "keep")
            holder["id"] = victim["symphony_id"]
            assert victim["evidence"]["backtest"]["status"] == "unavailable", "fixture sanity"
            _edit(victim, "evidence.backtest.subject", "an edited backtest subject")
            _edit(victim, "evidence.backtest.source", "an edited backtest source")

        _, _, panel = _render_edited(client, "full", edit)
        text = pb.entry_node(panel, holder["id"]).find("tenure-review-evidence-backtest").text()
        assert "subject: an edited backtest subject" in text
        assert "source: an edited backtest source" in text


# ===========================================================================
# Text of only spaces counts as missing: it leaves no empty line behind.
# ===========================================================================

_SLOT_CLASSES = frozenset({"retirement-rec-meta", "retirement-rec-gates", "retirement-rec-basis"})


def _empty_lines(block: pb.Node) -> list[pb.Node]:
    return [
        n
        for n in block.walk()
        if not any(isinstance(c, pb.Node) for c in n.children)
        and not n.text()
        and _SLOT_CLASSES & set(n.classes())
    ]


class TestBlankTextLeavesNoEmptyLine:
    @pytest.mark.parametrize(
        ("which", "path", "item"),
        [
            pytest.param("full", "evidence.redundancy.pair.note", "redundancy", id="pair note"),
            pytest.param("quiet", "evidence.redundancy.summary", "redundancy", id="summary"),
            pytest.param("full", "evidence.performance.label", "performance", id="item label"),
        ],
    )
    def test_the_box_has_no_line_without_text(self, client, isolated_db, which, path, item):
        holder = {}

        def edit(record):
            victim = (
                pb.entry_with_verdict(record, "watch") if which == "full" else record["entries"][0]
            )
            holder["id"] = victim["symphony_id"]
            _edit(victim, path, _BLANK)

        _, _, panel = _render_edited(client, which, edit)
        block = pb.entry_node(panel, holder["id"]).find(f"tenure-review-evidence-{item}")
        assert [n.tag for n in _empty_lines(block)] == [], block.text()


# ===========================================================================
# The legacy recommendation cards read their numbers through the same helpers as the
# review entries: a value that is not a finite number reads "not recorded", an integer too
# large for a float never takes the page down, and a figure that rounds to zero prints 0.00.
# They show only while no review run is stored.
# ===========================================================================

_LEGACY_UNUSABLE = [
    pytest.param("0.7", id="text"),
    pytest.param([0.7], id="a list"),
    pytest.param({"k": 0.7}, id="a dict"),
    pytest.param(True, id="a boolean"),
]
_LEGACY_LEAKS = (r"\b0\.7\b", r"\bTrue\b", r"\[", r"\{")


def _store_legacy_row(candidate_id="cand-legacy", sibling_id="sib-legacy", **fields) -> dict:
    raw = _sample_raw_response(candidate_id, sibling_id)
    raw.update(fields)
    for key in [key for key, value in raw.items() if value is _GONE]:
        del raw[key]
    subject = candidate_id if isinstance(candidate_id, str) and candidate_id else "bad-row"
    db_module.insert_advisor_observation(
        advisor_role="RETIREMENT_RECOMMENDATION",
        subject_type="symphony",
        subject_id=subject,
        symphony_id=subject,
        verdict="retire_candidate",
        raw_response=raw,
    )
    return raw


def _only_legacy_card(client) -> pb.Node:
    cards = _panel(client).find_all("retirement-recommendation-card")
    assert len(cards) == 1, f"expected one legacy card, got {len(cards)}"
    return cards[0]


def _gap_text(card: pb.Node) -> str:
    lines = pb.leaf_nodes_containing(card, "composite gap")
    assert lines, "the card has no composite gap line"
    return lines[0].text()


class TestALegacyCardReadsItsNumbersThroughTheNumberHelpers:
    @pytest.fixture(autouse=True)
    def roster(self, isolated_db):
        _seed_symphony_roster("cand-legacy", "sib-legacy")

    @pytest.mark.parametrize("bad", _LEGACY_UNUSABLE)
    def test_a_correlation_of_the_wrong_type_reads_not_recorded_and_the_page_answers_200(
        self, client, bad
    ):
        _store_legacy_row(correlation=bad)
        card = _only_legacy_card(client)
        text = card.find("retirement-rec-correlation").text()
        assert _NOT_RECORDED in text.lower(), text
        for leak in _LEGACY_LEAKS:
            assert not re.search(leak, text), (leak, text)
        assert "0.55" in _gap_text(card), "another figure of the card changed"
        assert client.get("/api/retirement-recommendations").status_code == 200

    @pytest.mark.parametrize("side", ["sibling_composite", "candidate_composite"])
    @pytest.mark.parametrize("bad", _LEGACY_UNUSABLE)
    def test_a_composite_of_the_wrong_type_makes_the_gap_read_not_recorded(self, client, side, bad):
        _store_legacy_row(**{side: bad})
        card = _only_legacy_card(client)
        text = _gap_text(card)
        assert _NOT_RECORDED in text.lower(), text
        for leak in _LEGACY_LEAKS:
            assert not re.search(leak, text), (leak, text)
        assert "0.80" in card.find("retirement-rec-correlation").text()
        assert client.get("/api/retirement-recommendations").status_code == 200

    def test_a_correlation_too_large_for_a_float_prints_its_digits_or_reads_not_recorded(
        self, client
    ):
        _store_legacy_row(correlation=10**400)
        card = _only_legacy_card(client)
        text = card.find("retirement-rec-correlation").text()
        assert _NOT_RECORDED in text.lower() or str(10**400) in text, text[:120]
        assert client.get("/api/retirement-recommendations").status_code == 200

    @pytest.mark.parametrize("side", ["sibling_composite", "candidate_composite"])
    def test_a_composite_too_large_for_a_float_makes_the_gap_read_not_recorded(self, client, side):
        _store_legacy_row(**{side: 10**400})
        card = _only_legacy_card(client)
        assert _NOT_RECORDED in _gap_text(card).lower()
        assert client.get("/api/retirement-recommendations").status_code == 200

    def test_ordinary_numbers_print_as_two_decimals(self, client):
        _store_legacy_row(correlation=0.8, sibling_composite=0.75, candidate_composite=0.2)
        card = _only_legacy_card(client)
        assert card.find("retirement-rec-correlation").text().endswith("0.80")
        assert re.search(r"composite gap:\s*0\.55\b", _gap_text(card).lower())

    @pytest.mark.parametrize(
        ("sibling", "candidate"),
        [(0.2, 0.2005), (0.2, 0.2), (0.2005, 0.2), (0.25, 0.2500000001)],
        ids=["gap just below zero", "no gap", "gap just above zero", "gap far below the decimal"],
    )
    def test_a_gap_that_rounds_to_zero_reads_zero_point_zero_zero(self, client, sibling, candidate):
        _store_legacy_row(sibling_composite=sibling, candidate_composite=candidate)
        text = _gap_text(_only_legacy_card(client))
        assert re.search(r"composite gap:\s*0\.00\b", text.lower()), text
        assert not re.search(rf"[-{_MINUS}]\s*0\.00", text), text

    def test_a_correlation_that_rounds_to_zero_reads_zero_point_zero_zero(self, client):
        _store_legacy_row(correlation=-0.0004)
        text = _only_legacy_card(client).find("retirement-rec-correlation").text()
        assert re.search(r"correlation:\s*0\.00\b", text.lower()), text
        assert not re.search(rf"[-{_MINUS}]\s*0\.00", text), text


# ===========================================================================
# A recommendation row whose candidate_id or sibling_id is a list or a dict degrades only
# itself: the display-name refresh skips it, and every other row and card still renders.
# ===========================================================================

_BAD_ROW_IDS = [
    pytest.param("candidate_id", ["bad"], id="candidate id a list"),
    pytest.param("candidate_id", {"k": "v"}, id="candidate id a dict"),
    pytest.param("sibling_id", ["bad"], id="sibling id a list"),
    pytest.param("sibling_id", {"k": "v"}, id="sibling id a dict"),
]


def _seed_named_roster() -> None:
    db_module.save_state(
        {
            "cand-good": {"name": "Good Candidate Name", "logic_holdings": {"AAPL": 1.0}},
            "sib-good": {"name": "Good Sibling Name", "logic_holdings": {"AAPL": 1.0}},
        }
    )


def _insert_row(raw: dict, subject_id: str) -> None:
    db_module.insert_advisor_observation(
        advisor_role="RETIREMENT_RECOMMENDATION",
        subject_type="symphony",
        subject_id=subject_id,
        symphony_id=subject_id,
        verdict="retire_candidate",
        raw_response=raw,
    )


class TestARecommendationRowWhoseIdCannotBeAKeyDegradesOnlyItself:
    @pytest.mark.parametrize(("field", "bad"), _BAD_ROW_IDS)
    def test_with_no_run_the_other_rows_and_cards_keep_their_names(
        self, client, isolated_db, field, bad
    ):
        _seed_named_roster()
        _insert_row(_sample_raw_response("cand-good", "sib-good"), "cand-good")
        broken = _sample_raw_response("cand-other", "sib-other")
        broken[field] = bad
        _insert_row(broken, "cand-other")
        resp = client.get("/api/retirement-recommendations")
        assert resp.status_code == 200
        rows = pb.strict_json(resp)["recommendations"]
        listed = [r for r in rows if r.get("candidate_id") == "cand-good"]
        assert len(listed) == 1, "the valid row was lost beside the broken one"
        assert listed[0]["candidate_name"] == "Good Candidate Name"
        assert listed[0]["sibling_name"] == "Good Sibling Name"
        assert listed[0]["approval_status"] == "pending"
        shown = [
            card
            for card in _panel(client).find_all("retirement-recommendation-card")
            if "Good Candidate Name" in card.text()
        ]
        assert len(shown) == 1, "the valid card is not on the page"
        assert "kept: Good Sibling Name" in shown[0].text()

    @pytest.mark.parametrize(("field", "bad"), _BAD_ROW_IDS)
    def test_with_a_run_the_rows_it_owns_keep_their_names(self, client, isolated_db, field, bad):
        pb.seed_bot_state()
        record = pb.store_full_run()
        candidate = pb.entry_with_verdict(record, "retire_candidate")
        broken = pb.recommendation_for(record, candidate)
        broken["candidate_id"] = "cand-other"
        broken[field] = bad
        _insert_row(broken, "cand-other")
        resp = client.get("/api/retirement-recommendations")
        assert resp.status_code == 200
        data = pb.strict_json(resp)
        assert data["review_read_failed"] is False
        listed = [
            r for r in data["recommendations"] if r.get("candidate_id") == candidate["symphony_id"]
        ]
        assert len(listed) == 1, "the valid row was lost beside the broken one"
        assert listed[0]["candidate_name"] == candidate["display_name"]
        assert listed[0]["approval_status"] == "pending"
        entry = pb.entry_node(_panel(client), candidate["symphony_id"])
        approve, reject = _lifecycle_buttons(entry)
        assert len(approve) == 1 and len(reject) == 1, "the review entry lost its decision buttons"


# ===========================================================================
# A legacy recommendation card whose candidate_id, sibling_id or basis_label is not text
# with something in it reads "not recorded" in each place it prints, never the Python text
# of the stored value, and a card whose candidate_id cannot name a symphony offers no decision.
# ===========================================================================

_NOT_NONBLANK_TEXT = [
    pytest.param(None, id="null"),
    pytest.param(True, id="a boolean"),
    pytest.param(4242424, id="a number"),
    pytest.param(float("nan"), id="nan"),
    pytest.param(float("inf"), id="infinity"),
    pytest.param([_MARK], id="a list"),
    pytest.param({"k": _MARK}, id="a dict"),
    pytest.param("", id="empty"),
    pytest.param(_BLANK, id="blank"),
    pytest.param(_GONE, id="missing"),
]
# The Python text a stored value takes when it is printed without a check.
_REPR_LEAKS = (r"\bNone\b", r"\bTrue\b", r"4242424", r"\bnan\b", r"\binf\b", _MARK, r"\[", r"\{")


def _by_class(card: pb.Node, css_class: str) -> list[pb.Node]:
    return [n for n in card.walk() if css_class in n.classes()]


def _assert_no_repr_in(card: pb.Node) -> None:
    text = card.everything()
    for leak in _REPR_LEAKS:
        assert not re.search(leak, text), f"the card prints {leak!r}: {text[:300]}"


def _assert_title_is_absent_or_not_recorded(node: pb.Node) -> None:
    title = node.attrs.get("title")
    assert title is None or title.lower() == _NOT_RECORDED, f"title {title!r}"


# A stored name the card must not print. The family a stored name can take: only spaces, empty,
# null, a boolean, a number, NaN, a list, a dict and the key missing. A text name is the
# ordinary case and prints as stored.
_STORED_NUMBER_NAME = 4242424
_UNUSABLE_STORED_NAMES = [
    pytest.param(_BLANK, id="blank"),
    pytest.param("", id="empty"),
    pytest.param(None, id="null"),
    pytest.param(True, id="a boolean"),
    pytest.param(_STORED_NUMBER_NAME, id="a number"),
    pytest.param(float("nan"), id="nan"),
    pytest.param([_MARK], id="a list"),
    pytest.param({"k": _MARK}, id="a dict"),
    pytest.param(_GONE, id="missing"),
]


class TestALegacyCardNamesAnUnusableFieldAsNotRecorded:
    @pytest.fixture(autouse=True)
    def roster(self, isolated_db):
        _seed_named_roster()

    @pytest.mark.parametrize("bad", _NOT_NONBLANK_TEXT)
    def test_a_candidate_id_that_is_not_text_reads_not_recorded_and_offers_no_decision(
        self, client, bad
    ):
        _store_legacy_row(bad, "sib-good")
        card = _only_legacy_card(client)
        (candidate,) = _by_class(card, "retirement-rec-candidate")
        assert candidate.text().lower() == _NOT_RECORDED, candidate.text()
        _assert_title_is_absent_or_not_recorded(candidate)
        (sibling,) = _by_class(card, "retirement-rec-sibling")
        assert sibling.text() == "kept: Good Sibling Name", sibling.text()
        assert _lifecycle_buttons(card) == ([], []), "a decision is offered for no symphony"
        assert not any("data-candidate-id" in n.attrs for n in card.walk())
        _assert_no_repr_in(card)
        assert "0.80" in card.find("retirement-rec-correlation").text()
        assert client.get("/api/retirement-recommendations").status_code == 200

    @pytest.mark.parametrize("bad", _NOT_NONBLANK_TEXT)
    def test_a_candidate_id_that_is_not_text_shows_no_status_chip(self, client, bad):
        """A card that names no symphony has no decision state to show, as an entry with a
        missing id has none."""
        _store_legacy_row(bad, "sib-good")
        card = _only_legacy_card(client)
        assert card.find("retirement-rec-status") is None, "a status chip renders"
        assert "pending" not in card.everything().lower()

    @pytest.mark.parametrize("bad", _NOT_NONBLANK_TEXT)
    def test_a_sibling_id_that_is_not_text_reads_not_recorded_beside_the_candidates_name(
        self, client, bad
    ):
        _store_legacy_row("cand-good", bad)
        card = _only_legacy_card(client)
        (sibling,) = _by_class(card, "retirement-rec-sibling")
        assert re.fullmatch(r"kept:\s*not recorded", sibling.text().lower()), sibling.text()
        _assert_title_is_absent_or_not_recorded(sibling)
        (candidate,) = _by_class(card, "retirement-rec-candidate")
        assert candidate.text() == "Good Candidate Name", "the candidate lost its fresh name"
        assert candidate.attrs.get("title") == "cand-good"
        approve, reject = _lifecycle_buttons(card)
        assert len(approve) == 1 and len(reject) == 1, "the valid candidate lost its decision"
        assert approve[0].attrs["data-candidate-id"] == "cand-good"
        assert reject[0].attrs["data-candidate-id"] == "cand-good"
        _assert_no_repr_in(card)
        assert client.get("/api/retirement-recommendations").status_code == 200

    @pytest.mark.parametrize("bad", _NOT_NONBLANK_TEXT)
    def test_a_basis_label_that_is_not_text_reads_not_recorded(self, client, bad):
        _store_legacy_row("cand-good", "sib-good", basis_label=bad)
        card = _only_legacy_card(client)
        (basis,) = _by_class(card, "retirement-rec-basis")
        assert basis.text().lower() == _NOT_RECORDED, basis.text()
        approve, reject = _lifecycle_buttons(card)
        assert len(approve) == 1 and len(reject) == 1
        _assert_no_repr_in(card)
        assert client.get("/api/retirement-recommendations").status_code == 200

    @pytest.mark.parametrize("bad", _UNUSABLE_STORED_NAMES)
    def test_a_stored_candidate_name_that_is_not_text_is_never_printed_and_the_card_reads_the_id(
        self, client, bad
    ):
        """The id is text with no live name, so no fresh name replaces the stored one."""
        _store_legacy_row("cand-unknown", "sib-unknown", candidate_name=bad)
        card = _only_legacy_card(client)
        (candidate,) = _by_class(card, "retirement-rec-candidate")
        assert candidate.text() == "cand-unknown", candidate.text()
        assert candidate.attrs.get("title") == "cand-unknown"
        (sibling,) = _by_class(card, "retirement-rec-sibling")
        assert sibling.text() == "kept: sib-unknown", sibling.text()
        _assert_no_repr_in(card)
        assert len(_lifecycle_buttons(card)[0]) == 1

    @pytest.mark.parametrize("bad", _UNUSABLE_STORED_NAMES)
    def test_a_stored_sibling_name_that_is_not_text_is_never_printed_and_the_card_reads_the_id(
        self, client, bad
    ):
        _store_legacy_row("cand-unknown", "sib-unknown", sibling_name=bad)
        card = _only_legacy_card(client)
        (sibling,) = _by_class(card, "retirement-rec-sibling")
        assert sibling.text() == "kept: sib-unknown", sibling.text()
        assert sibling.attrs.get("title") == "sib-unknown"
        (candidate,) = _by_class(card, "retirement-rec-candidate")
        assert candidate.text() == "cand-unknown", candidate.text()
        _assert_no_repr_in(card)
        assert len(_lifecycle_buttons(card)[0]) == 1

    @pytest.mark.parametrize("bad_id", _NOT_NONBLANK_TEXT)
    def test_a_stored_name_that_is_not_text_never_prints_beside_an_id_that_is_not_text_either(
        self, client, bad_id
    ):
        _store_legacy_row(
            bad_id, bad_id, candidate_name=_STORED_NUMBER_NAME, sibling_name=_STORED_NUMBER_NAME
        )
        card = _only_legacy_card(client)
        (candidate,) = _by_class(card, "retirement-rec-candidate")
        assert candidate.text().lower() == _NOT_RECORDED, candidate.text()
        (sibling,) = _by_class(card, "retirement-rec-sibling")
        assert re.fullmatch(r"kept:\s*not recorded", sibling.text().lower()), sibling.text()
        _assert_no_repr_in(card)

    def test_a_card_whose_fields_are_all_text_prints_them_as_stored(self, client):
        _store_legacy_row("cand-good", "sib-good")
        card = _only_legacy_card(client)
        (candidate,) = _by_class(card, "retirement-rec-candidate")
        (sibling,) = _by_class(card, "retirement-rec-sibling")
        (basis,) = _by_class(card, "retirement-rec-basis")
        assert candidate.text() == "Good Candidate Name" and candidate.attrs["title"] == "cand-good"
        assert sibling.text() == "kept: Good Sibling Name" and sibling.attrs["title"] == "sib-good"
        assert basis.text() == _sample_raw_response()["basis_label"]
        approve, reject = _lifecycle_buttons(card)
        assert len(approve) == 1 and len(reject) == 1
        chip = card.find("retirement-rec-status")
        assert chip is not None and chip.text().lower() == "pending"

    def test_one_unusable_row_changes_neither_the_valid_card_nor_its_buttons(self, client):
        _store_legacy_row("cand-good", "sib-good")
        _store_legacy_row(["bad"], "sib-good", basis_label=None)
        cards = _panel(client).find_all("retirement-recommendation-card")
        assert len(cards) == 2, "both rows are listed"
        by_title = {
            (_by_class(c, "retirement-rec-candidate")[0].attrs.get("title") or ""): c for c in cards
        }
        valid = by_title["cand-good"]
        assert _by_class(valid, "retirement-rec-candidate")[0].text() == "Good Candidate Name"
        assert len(_lifecycle_buttons(valid)[0]) == 1
        broken = next(c for t, c in by_title.items() if t != "cand-good")
        assert _lifecycle_buttons(broken) == ([], [])
        _assert_no_repr_in(broken)


# ===========================================================================
# A recommendation row whose ids are not both text keeps the name of each id that is text:
# only the field that cannot be looked up is skipped, on the API and on the page.
# ===========================================================================

_ONE_UNUSABLE_ID = [
    pytest.param(["bad"], id="a list"),
    pytest.param({"k": "v"}, id="a dict"),
    pytest.param(None, id="null"),
    pytest.param(4242424, id="a number"),
    pytest.param(_BLANK, id="blank"),
    pytest.param(_GONE, id="missing"),
]
_STALE_CANDIDATE = "Stale Candidate Name"
_STALE_SIBLING = "Stale Sibling Name"


def _named_row(**edits) -> dict:
    raw = _sample_raw_response("cand-good", "sib-good")
    raw["candidate_name"] = _STALE_CANDIDATE
    raw["sibling_name"] = _STALE_SIBLING
    for key, value in edits.items():
        if value is _GONE:
            del raw[key]
        else:
            raw[key] = value
    return raw


class TestEachIdThatIsTextKeepsItsFreshName:
    @pytest.mark.parametrize("bad", _ONE_UNUSABLE_ID)
    def test_with_no_run_a_valid_candidate_id_is_resolved_beside_a_sibling_id_that_is_not_text(
        self, client, isolated_db, bad
    ):
        _seed_named_roster()
        _insert_row(_named_row(sibling_id=bad), "cand-good")
        resp = client.get("/api/retirement-recommendations")
        assert resp.status_code == 200
        rows = [
            r
            for r in pb.strict_json(resp)["recommendations"]
            if r.get("candidate_id") == "cand-good"
        ]
        assert len(rows) == 1
        assert rows[0]["candidate_name"] == "Good Candidate Name", (
            "the candidate's fresh name was skipped because the sibling id is not text"
        )
        assert rows[0].get("sibling_name") == _STALE_SIBLING, "the unusable field was rewritten"

    @pytest.mark.parametrize("bad", _ONE_UNUSABLE_ID)
    def test_with_no_run_a_valid_sibling_id_is_resolved_beside_a_candidate_id_that_is_not_text(
        self, client, isolated_db, bad
    ):
        _seed_named_roster()
        _insert_row(_named_row(candidate_id=bad), "cand-other")
        resp = client.get("/api/retirement-recommendations")
        assert resp.status_code == 200
        rows = [
            r for r in pb.strict_json(resp)["recommendations"] if r.get("sibling_id") == "sib-good"
        ]
        assert len(rows) == 1
        assert rows[0]["sibling_name"] == "Good Sibling Name", (
            "the sibling's fresh name was skipped because the candidate id is not text"
        )
        assert rows[0].get("candidate_name") == _STALE_CANDIDATE, "the unusable field was rewritten"

    @pytest.mark.parametrize("bad", _ONE_UNUSABLE_ID)
    def test_with_a_run_the_candidate_of_the_run_keeps_its_fresh_name(
        self, client, isolated_db, bad
    ):
        pb.seed_bot_state()
        record = pb.full_record()
        candidate = pb.entry_with_verdict(record, "retire_candidate")
        body = pb.recommendation_for(record, candidate)
        body["candidate_name"] = _STALE_CANDIDATE
        _edit(body, "sibling_id", bad)
        pb.store_recommendation(body)
        pb.store_run(record)
        resp = client.get("/api/retirement-recommendations")
        assert resp.status_code == 200
        rows = [
            r
            for r in pb.strict_json(resp)["recommendations"]
            if r.get("candidate_id") == candidate["symphony_id"]
        ]
        assert len(rows) == 1
        assert rows[0]["candidate_name"] == candidate["display_name"]


# ===========================================================================
# An id that is not non-blank text is looked up for no name: a row with no stored name beside
# such an id carries none after the refresh, whatever the id's type (a number, a boolean and a
# float are hashable and would resolve to themselves).
# ===========================================================================

_IDS_THAT_NAME_NO_SYMPHONY = [
    pytest.param(4242424, id="a number"),
    pytest.param(True, id="a boolean"),
    pytest.param(1.5, id="a float"),
    pytest.param(None, id="null"),
    pytest.param(["bad"], id="a list"),
    pytest.param({"k": "v"}, id="a dict"),
    pytest.param("", id="empty"),
    pytest.param(_BLANK, id="blank"),
    pytest.param(_GONE, id="missing"),
]


def _unnamed_row(**edits) -> dict:
    """A recommendation row whose two names are not stored."""
    raw = _sample_raw_response("cand-good", "sib-good")
    for key, value in edits.items():
        if value is _GONE:
            del raw[key]
        else:
            raw[key] = value
    return raw


def _only_row(client) -> dict:
    resp = client.get("/api/retirement-recommendations")
    assert resp.status_code == 200
    rows = pb.strict_json(resp)["recommendations"]
    assert len(rows) == 1, f"expected one row, got {len(rows)}"
    return rows[0]


class TestAnIdThatIsNotTextIsResolvedToNoName:
    @pytest.mark.parametrize("bad", _IDS_THAT_NAME_NO_SYMPHONY)
    def test_with_no_run_a_candidate_id_that_is_not_text_carries_no_candidate_name(
        self, client, isolated_db, bad
    ):
        _seed_named_roster()
        _insert_row(_unnamed_row(candidate_id=bad), "cand-other")
        row = _only_row(client)
        assert row.get("candidate_name") is None, (
            f"a name was resolved for an id that is not text: {row.get('candidate_name')!r}"
        )
        assert row["sibling_name"] == "Good Sibling Name", "the sibling lost its fresh name"

    @pytest.mark.parametrize("bad", _IDS_THAT_NAME_NO_SYMPHONY)
    def test_with_no_run_a_sibling_id_that_is_not_text_carries_no_sibling_name(
        self, client, isolated_db, bad
    ):
        _seed_named_roster()
        _insert_row(_unnamed_row(sibling_id=bad), "cand-good")
        row = _only_row(client)
        assert row.get("sibling_name") is None, (
            f"a name was resolved for an id that is not text: {row.get('sibling_name')!r}"
        )
        assert row["candidate_name"] == "Good Candidate Name", "the candidate lost its fresh name"

    @pytest.mark.parametrize("bad", _IDS_THAT_NAME_NO_SYMPHONY)
    def test_with_a_run_a_candidate_id_that_is_not_text_carries_no_candidate_name(
        self, client, isolated_db, bad
    ):
        pb.seed_bot_state()
        record = pb.full_record()
        candidate = pb.entry_with_verdict(record, "retire_candidate")
        body = pb.recommendation_for(record, candidate)
        _edit(body, "candidate_id", bad)
        _insert_row(body, candidate["symphony_id"])
        pb.store_run(record)
        row = _only_row(client)
        assert row.get("candidate_name") is None, (
            f"a name was resolved for an id that is not text: {row.get('candidate_name')!r}"
        )

    @pytest.mark.parametrize("bad", _IDS_THAT_NAME_NO_SYMPHONY)
    def test_with_a_run_a_sibling_id_that_is_not_text_carries_no_sibling_name(
        self, client, isolated_db, bad
    ):
        pb.seed_bot_state()
        record = pb.full_record()
        candidate = pb.entry_with_verdict(record, "retire_candidate")
        body = pb.recommendation_for(record, candidate)
        _edit(body, "sibling_id", bad)
        pb.store_recommendation(body)
        pb.store_run(record)
        row = _only_row(client)
        assert row.get("sibling_name") is None, (
            f"a name was resolved for an id that is not text: {row.get('sibling_name')!r}"
        )
        assert row["candidate_name"] == candidate["display_name"]
