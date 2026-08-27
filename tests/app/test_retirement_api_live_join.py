"""RED tests -- Cycle 2c AC-6: GET /api/retirement-recommendations gains the
approval-status live-join.

feature-plans/retirement-approval-polish.md AC-6: GET /api/retirement-
recommendations applies the SAME approval-status live-join the AI Advisor
panel does, so each returned recommendation carries approval_status --
programmatic consumers see decision state. Read-only, honest default
("pending"/absent) when no decision row exists.

Confirmed with ret3-route (implementer): approval_status is stamped at the
TOP LEVEL of each response item (matching the existing "raw_response
verbatim, flattened to top level" contract _fetch_retirement_recommendations
already documents), never nested under a sub-key.

Fixture pattern duplicated from tests/app/test_retirement_recommendations_route.py
per this repo's established fixtures-are-not-cross-file-shared convention.

Expected state: RED until app.py's api_retirement_recommendations()
(and/or _fetch_retirement_recommendations()) gains the live-join.
"""

from __future__ import annotations

import pytest


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
    import database as db_module

    db_module.insert_advisor_observation(
        advisor_role="RETIREMENT_RECOMMENDATION",
        subject_type="symphony",
        subject_id=candidate_id,
        symphony_id=candidate_id,
        verdict="retire_candidate",
        raw_response=_sample_raw_response(candidate_id, sibling_id),
    )


@pytest.fixture()
def client():
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    import database as db_module
    from database import init_db, run_migrations

    db_path = str(tmp_path / "test_retirement_api_live_join.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    run_migrations()
    yield db_path


def _row_for(data: dict, candidate_id: str) -> dict:
    row = next((r for r in data["recommendations"] if r.get("candidate_id") == candidate_id), None)
    assert row is not None, f"No recommendation row found for candidate_id={candidate_id!r}"
    return row


class TestApprovalStatusLiveJoin:
    def test_recommendation_row_carries_pending_by_default(self, client, isolated_db):
        _seed_recommendation(candidate_id="cand-pending-1", sibling_id="sib-pending-1")

        resp = client.get("/api/retirement-recommendations")
        assert resp.status_code == 200
        data = resp.get_json()
        row = _row_for(data, "cand-pending-1")

        assert "approval_status" in row, (
            "GET /api/retirement-recommendations must carry approval_status on every "
            "row, honest default when no decision exists."
        )
        assert row["approval_status"] == "pending"

    def test_recommendation_row_reflects_approved_decision(self, client, isolated_db):
        import database as db_module

        _seed_recommendation(candidate_id="cand-approved-1", sibling_id="sib-approved-1")
        db_module.upsert_retirement_decision("cand-approved-1", approval_status="approved")

        resp = client.get("/api/retirement-recommendations")
        data = resp.get_json()
        row = _row_for(data, "cand-approved-1")
        assert row["approval_status"] == "approved"

    def test_recommendation_row_reflects_rejected_decision(self, client, isolated_db):
        import database as db_module

        _seed_recommendation(candidate_id="cand-rejected-1", sibling_id="sib-rejected-1")
        db_module.upsert_retirement_decision("cand-rejected-1", approval_status="rejected")

        resp = client.get("/api/retirement-recommendations")
        data = resp.get_json()
        row = _row_for(data, "cand-rejected-1")
        assert row["approval_status"] == "rejected"

    def test_multiple_recommendations_reflect_independent_decisions(self, client, isolated_db):
        import database as db_module

        _seed_recommendation(candidate_id="cand-multi-pending", sibling_id="sib-multi-1")
        _seed_recommendation(candidate_id="cand-multi-approved", sibling_id="sib-multi-2")
        _seed_recommendation(candidate_id="cand-multi-rejected", sibling_id="sib-multi-3")
        db_module.upsert_retirement_decision("cand-multi-approved", approval_status="approved")
        db_module.upsert_retirement_decision("cand-multi-rejected", approval_status="rejected")

        resp = client.get("/api/retirement-recommendations")
        data = resp.get_json()

        assert _row_for(data, "cand-multi-pending")["approval_status"] == "pending"
        assert _row_for(data, "cand-multi-approved")["approval_status"] == "approved"
        assert _row_for(data, "cand-multi-rejected")["approval_status"] == "rejected"

    def test_live_reread_reflects_a_changed_decision_between_requests(self, client, isolated_db):
        """Never cached/stale -- a decision made between two GETs must be
        visible on the second GET without a restart."""
        import database as db_module

        _seed_recommendation(candidate_id="cand-live-1", sibling_id="sib-live-1")

        first = client.get("/api/retirement-recommendations").get_json()
        assert _row_for(first, "cand-live-1")["approval_status"] == "pending"

        db_module.upsert_retirement_decision("cand-live-1", approval_status="approved")

        second = client.get("/api/retirement-recommendations").get_json()
        assert _row_for(second, "cand-live-1")["approval_status"] == "approved", (
            "The route must read approval_status FRESH on every request, never cache "
            "a stale decision from an earlier request."
        )

    def test_decisions_read_failure_degrades_to_pending_default_not_500(
        self, client, isolated_db, monkeypatch
    ):
        import database as db_module

        _seed_recommendation(candidate_id="cand-degrade-1", sibling_id="sib-degrade-1")

        def _raise(*a, **kw):
            raise RuntimeError("simulated decisions-table read failure")

        monkeypatch.setattr(db_module, "get_retirement_decisions", _raise)

        resp = client.get("/api/retirement-recommendations")
        assert resp.status_code == 200, (
            "A decisions-table read failure must degrade to the honest pending default, never 500."
        )
        data = resp.get_json()
        row = _row_for(data, "cand-degrade-1")
        assert row["approval_status"] == "pending"

    def test_approval_status_present_at_top_level_not_nested(self, client, isolated_db):
        import database as db_module

        _seed_recommendation(candidate_id="cand-shape-1", sibling_id="sib-shape-1")
        db_module.upsert_retirement_decision("cand-shape-1", approval_status="approved")

        resp = client.get("/api/retirement-recommendations")
        data = resp.get_json()
        row = _row_for(data, "cand-shape-1")

        assert isinstance(row.get("approval_status"), str), (
            f"approval_status must be a plain top-level string field, got "
            f"{row.get('approval_status')!r} (type {type(row.get('approval_status'))})."
        )
        # Original raw_response fields must still be present alongside it
        # (additive join, never a replacement of the authoritative schema).
        assert row["candidate_id"] == "cand-shape-1"
        assert row["correlation"] == pytest.approx(0.80)
