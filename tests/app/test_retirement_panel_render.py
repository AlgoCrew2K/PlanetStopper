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

import re

import pytest

import app as app_module
import database as db_module
from database import init_db, run_migrations


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
        assert 'data-testid="retirement-recommendations-empty"' in body
        assert 'data-testid="retirement-approve-btn"' not in body
        assert 'data-testid="retirement-reject-btn"' not in body
        assert 'data-testid="retirement-checklist"' not in body
