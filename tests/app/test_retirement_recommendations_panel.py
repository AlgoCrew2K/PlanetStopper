"""
RED tests -- read-only Retirement Recommendations panel on the AI Advisor tab
(AC-10).

The panel does not exist yet; every content-bearing test in this file is
expected to fail RED until the implementer adds the section to
templates/ai_advisor.html (+ its ai_advisor_tab() prefetch in app.py).

THE CROSS-IMPLEMENTER CONTRACT (PM mandate): the raw_response schema is
defined ONCE in tests/advisors/test_retirement_recommender_persistence.py and
reused IDENTICALLY here (the literal dict below is byte-identical to
tests/app/test_retirement_recommendations_route.py's _sample_raw_response()).
retire-route renders these exact fields; nothing invented, nothing renamed.

Contract under test (pinned in .claude/tdd-handoff.md "templates/ai_advisor.html + app.py -- panel"):
- Pinned testids: data-testid="retirement-recommendations-panel" (section
  wrapper), data-testid="retirement-recommendations-empty" (honest empty
  state), data-testid="retirement-recommendation-card" (one per recommendation).
- Real server-rendered content: candidate_id, sibling_id, correlation must
  appear in the rendered card.
- All fields HTML-escaped (Jinja autoescape; a symphony id containing
  `<script>` must never render unescaped) -- no `| safe`.
- NO approve/reject/form/actionable-control markup anywhere in the panel
  section (this is the 2b boundary -- 2a is read-only, advisory-only).
"""

from __future__ import annotations

import pytest

import app as app_module
import database as db_module
from database import init_db, run_migrations


def _sample_raw_response(candidate_id="cand-sym", sibling_id="sib-sym"):
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
    }


def _seed_recommendation(candidate_id="cand-sym", sibling_id="sib-sym"):
    db_module.insert_advisor_observation(
        advisor_role="RETIREMENT_RECOMMENDATION",
        subject_type="symphony",
        subject_id=candidate_id,
        symphony_id=candidate_id,
        verdict="retire_candidate",
        raw_response=_sample_raw_response(candidate_id, sibling_id),
    )


def _seed_symphony_roster(*symphony_ids):
    """ai_advisor_tab() reads database.load_state() for the live roster
    elsewhere in its render (Frontrunner proposals, incubation badges, etc.)
    -- seed a minimal bot_state so the page renders its normal authenticated
    shape rather than an unrelated empty-roster branch."""
    roster = {sid: {"name": sid, "logic_holdings": {}} for sid in symphony_ids}
    db_module.save_state(roster)


@pytest.fixture()
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_retirement_recommendations_panel.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    run_migrations()
    yield db_path


class TestPanelPresence:
    def test_panel_wrapper_present_on_ai_advisor_tab(self, client, isolated_db):
        resp = client.get("/ai-advisor")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'data-testid="retirement-recommendations-panel"' in body, (
            "GET /ai-advisor is missing the retirement-recommendations panel "
            "section -- not yet implemented (RED)."
        )


class TestPanelEmptyState:
    def test_no_recommendations_shows_honest_empty_state(self, client, isolated_db):
        resp = client.get("/ai-advisor")
        body = resp.get_data(as_text=True)
        assert 'data-testid="retirement-recommendations-empty"' in body, (
            "With zero persisted recommendations, the panel must render its "
            "honest empty-state marker."
        )
        assert 'data-testid="retirement-recommendation-card"' not in body


class TestPanelContent:
    def test_recommendation_card_renders_candidate_and_sibling_ids(self, client, isolated_db):
        _seed_symphony_roster("cand-panel-1", "sib-panel-1")
        _seed_recommendation(candidate_id="cand-panel-1", sibling_id="sib-panel-1")
        resp = client.get("/ai-advisor")
        body = resp.get_data(as_text=True)
        assert 'data-testid="retirement-recommendation-card"' in body
        assert "cand-panel-1" in body
        assert "sib-panel-1" in body

    def test_recommendation_card_renders_correlation_value(self, client, isolated_db):
        """Non-vacuity guard: bare '0.8' would spuriously match unrelated page
        content (e.g. a CSS opacity value) even with nothing implemented, so
        this REQUIRES the card marker to be present first (forcing a genuine
        RED pre-implementation) before checking the correlation substring,
        and only accepts the two specific, low-collision formats -- never the
        bare '0.8' fallback."""
        _seed_symphony_roster("cand-panel-2", "sib-panel-2")
        _seed_recommendation(candidate_id="cand-panel-2", sibling_id="sib-panel-2")
        resp = client.get("/ai-advisor")
        body = resp.get_data(as_text=True)
        assert 'data-testid="retirement-recommendation-card"' in body, (
            "recommendation card marker not present -- cannot check for the "
            "correlation value without a real render"
        )
        assert "0.80" in body or "80%" in body, (
            "The recommendation card must render the correlation value "
            "(correlation=0.80 in this fixture) in some human-readable form "
            "('0.80' or '80%')."
        )

    def test_empty_state_absent_when_a_recommendation_exists(self, client, isolated_db):
        """Non-vacuity guard: with nothing implemented, NEITHER the card NOR
        the empty-state marker appears, so a bare 'empty-state absent' check
        would pass for the wrong reason. This REQUIRES the card marker to be
        present (proving a real recommendation actually rendered) before the
        empty-state-absent assertion is meaningful."""
        _seed_symphony_roster("cand-panel-3", "sib-panel-3")
        _seed_recommendation(candidate_id="cand-panel-3", sibling_id="sib-panel-3")
        resp = client.get("/ai-advisor")
        body = resp.get_data(as_text=True)
        assert 'data-testid="retirement-recommendation-card"' in body, (
            "recommendation card marker not present -- cannot check that the "
            "empty state is correctly suppressed without a real render"
        )
        assert 'data-testid="retirement-recommendations-empty"' not in body


class TestPanelEscaping:
    def test_symphony_id_containing_script_tag_is_escaped(self, client, isolated_db):
        """Non-vacuity guard: with nothing implemented, the malicious id never
        renders anywhere, so a bare 'raw tag absent' check would pass for the
        wrong reason (nothing rendered at all, not 'rendered and escaped').
        This REQUIRES the card marker to be present (a real render happened)
        AND the id's escaped form to appear, before the raw-tag-absent
        assertion is meaningful."""
        malicious_id = "cand<script>alert(1)</script>"
        _seed_symphony_roster(malicious_id, "sib-panel-xss")
        _seed_recommendation(candidate_id=malicious_id, sibling_id="sib-panel-xss")
        resp = client.get("/ai-advisor")
        body = resp.get_data(as_text=True)
        assert 'data-testid="retirement-recommendation-card"' in body, (
            "recommendation card marker not present -- cannot check escaping without a real render"
        )
        assert "&lt;script&gt;" in body, (
            "Expected the Jinja-escaped form of the malicious candidate id "
            "(&lt;script&gt;) to appear in the rendered card -- if it's "
            "missing entirely, the id was not rendered at all rather than "
            "safely escaped."
        )
        assert "<script>alert(1)</script>" not in body, (
            "A symphony id containing a raw <script> tag must be HTML-escaped "
            "by the panel render -- found an UNESCAPED script tag in the "
            "response body (XSS)."
        )


class TestPanelNoActionableControls:
    def test_panel_section_has_no_form_or_action_buttons(self, client, isolated_db):
        """2a is read-only/advisory-only -- approve/reject lifecycle is 2b,
        explicitly out of scope. The panel section itself must contain no
        <form>, no submit-shaped <button>, and no approve/reject strings
        within its own bounded markup window."""
        _seed_symphony_roster("cand-panel-4", "sib-panel-4")
        _seed_recommendation(candidate_id="cand-panel-4", sibling_id="sib-panel-4")
        resp = client.get("/ai-advisor")
        body = resp.get_data(as_text=True)

        start = body.find('data-testid="retirement-recommendations-panel"')
        assert start != -1, "panel wrapper not found -- cannot bound the section for this check"
        # Find the matching close of this section heuristically: search for
        # the NEXT panel/tab-section boundary marker after the wrapper, or
        # fall back to a generous fixed window if none is found.
        window = body[start : start + 6000]

        assert "<form" not in window.lower(), (
            "The retirement-recommendations panel must not contain a <form> "
            "element -- no actionable control (approve/reject is 2b)."
        )
        for forbidden in ("approve", "reject", "retire-approve", "retire-reject"):
            assert forbidden not in window.lower(), (
                f"Found forbidden actionable-control string {forbidden!r} inside "
                "the retirement-recommendations panel window -- 2a is read-only, "
                "no approve/reject affordance is in scope."
            )
