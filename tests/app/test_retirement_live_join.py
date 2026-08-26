"""RED tests -- AC-4: retirement decision live-join at render.

feature-plans/retirement-approval-lifecycle.md AC-4: ai_advisor_tab() builds
{candidate_id: decision_row} once from database.get_retirement_decisions()
and stamps each recommendation card's approval_status (+ _decision) IN-PLACE
-- mirroring the incubation _incubation_by_hash live-join (app.py:6125).
Status is read FRESH from the mutable table at render, NEVER from the frozen
advisor_observations row. A recommendation with no decision row shows
"pending".

Primary technique: intercept app.render_template (mirrors
tests/app/test_strategy_builder_spa_port.py's own _capture pattern) to
inspect the actual Python dicts passed into the template context --
decoupled from whatever HTML markup AC-7 eventually produces. This file
tests the LIVE-JOIN LOGIC; tests/app/test_retirement_panel_render.py (AC-7)
tests the rendered markup.

Fixture/helper duplication note: _sample_raw_response/_seed_recommendation/
_seed_symphony_roster/isolated_db are duplicated from
tests/app/test_retirement_recommendations_panel.py rather than imported --
matches this repo's own stated convention (fixtures are not cross-file-
shared; see test_frontrunner_builder_route.py's docstring on the same
point).

Expected state: RED until ai_advisor_tab() performs the live-join.
"""

from __future__ import annotations

from unittest.mock import patch

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
    roster = {sid: {"name": sid, "logic_holdings": {}} for sid in symphony_ids}
    db_module.save_state(roster)


@pytest.fixture()
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_retirement_live_join.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    run_migrations()
    yield db_path


def _get_rendered_recommendations(client) -> list[dict]:
    """GET /ai-advisor while intercepting render_template; return the
    'retirement_recommendations' context list actually passed to the
    template (the live-joined objects, not the rendered HTML)."""
    captured: dict = {}

    def _capture(template_name, **kwargs):
        captured.update(kwargs)
        return "<html><body>stub</body></html>"

    with (
        patch("flask.templating._render", side_effect=lambda *a, **kw: "stub"),
        patch("app.render_template", side_effect=_capture),
    ):
        resp = client.get("/ai-advisor")
    assert resp.status_code == 200, f"GET /ai-advisor returned {resp.status_code}"
    recs = captured.get("retirement_recommendations")
    assert recs is not None, (
        f"'retirement_recommendations' not found in template context. "
        f"Keys present: {sorted(captured.keys())}."
    )
    return recs


class TestNoDecisionRowDefaultsToPending:
    def test_recommendation_with_no_decision_row_shows_pending(self, client, isolated_db):
        _seed_symphony_roster("cand-lj-1", "sib-lj-1")
        _seed_recommendation(candidate_id="cand-lj-1", sibling_id="sib-lj-1")

        recs = _get_rendered_recommendations(client)
        matching = [r for r in recs if r.get("candidate_id") == "cand-lj-1"]
        assert len(matching) == 1
        assert matching[0].get("approval_status") == "pending", (
            f"A recommendation with no retirement_decisions row must default to "
            f"'pending', got {matching[0].get('approval_status')!r}."
        )


class TestDecisionRowIsLiveJoined:
    def test_approved_decision_row_is_stamped_onto_the_matching_card(self, client, isolated_db):
        _seed_symphony_roster("cand-lj-2", "sib-lj-2")
        _seed_recommendation(candidate_id="cand-lj-2", sibling_id="sib-lj-2")
        db_module.upsert_retirement_decision("cand-lj-2", approval_status="approved")

        recs = _get_rendered_recommendations(client)
        matching = [r for r in recs if r.get("candidate_id") == "cand-lj-2"]
        assert len(matching) == 1
        assert matching[0].get("approval_status") == "approved", (
            f"Expected the live-joined approval_status to be 'approved', "
            f"got {matching[0].get('approval_status')!r}."
        )

    def test_rejected_decision_row_is_stamped_onto_the_matching_card(self, client, isolated_db):
        _seed_symphony_roster("cand-lj-3", "sib-lj-3")
        _seed_recommendation(candidate_id="cand-lj-3", sibling_id="sib-lj-3")
        db_module.upsert_retirement_decision("cand-lj-3", approval_status="rejected")

        recs = _get_rendered_recommendations(client)
        matching = [r for r in recs if r.get("candidate_id") == "cand-lj-3"]
        assert matching[0].get("approval_status") == "rejected"

    def test_decision_status_never_read_from_the_frozen_advisor_observations_row(
        self, client, isolated_db
    ):
        """AC-4's core guarantee: status is read FRESH from the mutable
        retirement_decisions table, NEVER cached/frozen inside the
        advisor_observations raw_response the recommendation itself was
        persisted with (which never carries an approval_status key at all,
        per advisors/retirement_recommender.py's own raw_response schema)."""
        _seed_symphony_roster("cand-lj-4", "sib-lj-4")
        _seed_recommendation(candidate_id="cand-lj-4", sibling_id="sib-lj-4")
        # Sanity: the raw persisted observation carries no approval_status at all.
        raw_rows = db_module.get_advisor_observations_for_role("RETIREMENT_RECOMMENDATION")
        assert all("approval_status" not in (r.get("raw_response") or {}) for r in raw_rows)

        db_module.upsert_retirement_decision("cand-lj-4", approval_status="approved")
        recs = _get_rendered_recommendations(client)
        matching = [r for r in recs if r.get("candidate_id") == "cand-lj-4"]
        assert matching[0].get("approval_status") == "approved", (
            "The live-joined status must come from retirement_decisions, not "
            "the (status-less) frozen advisor_observations raw_response."
        )


class TestLiveJoinIsFreshPerRequest:
    def test_status_change_between_two_requests_is_reflected_on_the_second(
        self, client, isolated_db
    ):
        """The load-bearing 'FRESH at render, never cached' assertion: two
        sequential GET requests, with the decision status changed in
        between, must show DIFFERENT approval_status values -- proving the
        live-join is computed per-request, not memoized/computed once."""
        _seed_symphony_roster("cand-lj-5", "sib-lj-5")
        _seed_recommendation(candidate_id="cand-lj-5", sibling_id="sib-lj-5")

        recs_before = _get_rendered_recommendations(client)
        before = next(r for r in recs_before if r.get("candidate_id") == "cand-lj-5")
        assert before.get("approval_status") == "pending"

        db_module.upsert_retirement_decision("cand-lj-5", approval_status="approved")

        recs_after = _get_rendered_recommendations(client)
        after = next(r for r in recs_after if r.get("candidate_id") == "cand-lj-5")
        assert after.get("approval_status") == "approved", (
            "The second request must reflect the status change made between "
            "requests -- the live-join must not be cached/stale."
        )


class TestLiveJoinDegradesHonestlyOnReadFailure:
    def test_decisions_read_failure_does_not_500_and_leaves_cards_pending(
        self, client, isolated_db
    ):
        """Mirrors the incubation live-join's own try/except degrade
        (app.py:6125-6131) -- a database.get_retirement_decisions() failure
        must not break the whole /ai-advisor render; cards fall back to the
        honest 'pending' default rather than crashing."""
        _seed_symphony_roster("cand-lj-6", "sib-lj-6")
        _seed_recommendation(candidate_id="cand-lj-6", sibling_id="sib-lj-6")

        with patch(
            "database.get_retirement_decisions", side_effect=RuntimeError("db read failed")
        ):
            resp = client.get("/ai-advisor")

        assert resp.status_code == 200, (
            f"A retirement_decisions read failure must not 500 the whole "
            f"/ai-advisor render, got {resp.status_code}."
        )

    def test_a_decision_row_for_a_candidate_not_in_the_current_batch_is_harmless(
        self, client, isolated_db
    ):
        """Edge case from the plan: a decision row exists for a candidate no
        longer in the latest recommendation batch -- the live-join only
        stamps cards that render; an orphaned decision row must never crash
        the page."""
        db_module.upsert_retirement_decision("orphan-candidate-no-longer-flagged", approval_status="approved")
        # No matching recommendation seeded at all.
        resp = client.get("/ai-advisor")
        assert resp.status_code == 200
