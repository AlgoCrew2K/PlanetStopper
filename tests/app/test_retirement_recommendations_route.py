"""
RED tests -- GET /api/retirement-recommendations (AC-9).

The route does not exist yet; every test in this file is expected to fail RED
(404, or an AttributeError from monkeypatching a not-yet-present
database.get_advisor_observations_for_role usage inside the route) until the
implementer adds it. Directly mirrors tests/app/test_incubation_route.py's
structure (the plan's own architecture note: "mirrors api_incubation").

THE CROSS-IMPLEMENTER CONTRACT (PM mandate): the raw_response schema is
defined ONCE in tests/advisors/test_retirement_recommender_persistence.py and
reused IDENTICALLY here. This route's JSON response, for each recommendation,
must surface (at minimum) those same literal field names -- retire-route
renders what retire-math produces, never a divergent/renamed shape.

Contract under test (pinned in .claude/tdd-handoff.md "app.py -- route"):
- Read-only; global auth hook applies; NOT in _SETTINGS_WRITE_ALLOWLIST; no
  LIVE_EXECUTION interaction.
- Reads the latest persisted RETIREMENT_RECOMMENDATION advisor_observations
  rows via database.get_advisor_observations_for_role("RETIREMENT_RECOMMENDATION", ...)
  (test-writer ruling: this is the real accessor that exists for a role-wide,
  multi-subject read -- get_advisor_observations_for_subject is scoped to ONE
  subject_id and cannot list "all current recommendations across candidates").
- Empty -> {"recommendations": []}, never a 500.
- NaN/Infinity in any numeric field sanitized to null before jsonify.
- GET never writes (no persistence call reachable from this route).
- PR-level /code-review Finding 4 (PM ruling): the nightly 03:45 tick calls
  build_recommendations()+persist_recommendations() EVERY night into an
  APPEND-ONLY table, so a multi-night deployment accumulates one row per
  night per still-flagged pair, plus stale rows for pairs no longer flagged.
  _fetch_retirement_recommendations() must filter to ONLY the rows sharing
  the MOST RECENT row's calendar date (the same substr(created_at,1,10)
  trick database.get_candidate_alert_last_run already established) -- never
  the newest-N rows verbatim across multiple nights. See
  TestFetchRetirementRecommendationsLatestBatchOnly below.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta

import pytest

import app as app_module
import database as db_module
from database import init_db, run_migrations

_RAW_RESPONSE_KEYS = {
    "candidate_id",
    "sibling_id",
    "correlation",
    "ci_lower",
    "ci_upper",
    "n_obs",
    "candidate_composite",
    "sibling_composite",
    "candidate_metrics",
    "sibling_metrics",
    "uncertainty_gate_passed",
    "structural_redundancy_gate_passed",
    "stressed_correlation",
    "holdings_overlap",
    "basis_label",
}


@pytest.fixture()
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_retirement_recommendations_route.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    init_db()
    run_migrations()
    yield db_path


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


def _seed_recommendation_backdated(candidate_id: str, sibling_id: str, days_ago: int) -> None:
    """Insert a RETIREMENT_RECOMMENDATION row with an explicit, backdated
    created_at -- insert_advisor_observation has no created_at override (it
    always uses the schema's DEFAULT (datetime('now'))), so a raw SQL insert
    is required to simulate "an older night's batch". Mirrors
    tests/database/test_033_candidate_alert_state.py's
    test_older_batch_does_not_inflate_latest_run -- the same established
    idiom for backdating advisor_observations.created_at in this codebase."""
    old_date = (datetime.now(UTC) - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
    conn = db_module.get_connection()
    try:
        conn.execute(
            "INSERT INTO advisor_observations "
            "(advisor_role, subject_type, subject_id, verdict, raw_response, "
            " is_advisory_only, symphony_id, created_at) "
            "VALUES ('RETIREMENT_RECOMMENDATION', 'symphony', ?, 'retire_candidate', "
            " ?, 1, ?, ?)",
            (
                candidate_id,
                json.dumps(_sample_raw_response(candidate_id, sibling_id)),
                candidate_id,
                old_date,
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# PR-level /code-review Finding 4 (app.py's _fetch_retirement_recommendations,
# and the 03:45 nightly tick): the nightly tick calls
# build_recommendations()+persist_recommendations() EVERY night, and
# advisor_observations is APPEND-ONLY (never updated/deleted) -- so a
# multi-night deployment accumulates one row per night per still-flagged
# pair, and a prior night's candidate that's no longer flagged tonight still
# has its old row sitting in the table. The original _fetch_retirement_
# recommendations returned the newest-N rows VERBATIM (whatever
# get_advisor_observations_for_role's default limit/ordering produced),
# which folds multiple nights together -- duplicate/stale cards. PM ruling:
# filter to ONLY the rows sharing the MOST RECENT row's calendar date (the
# same substr(created_at,1,10)-equals-MAX(...) trick already established by
# database.get_candidate_alert_last_run for an analogous "one batch, not the
# whole table" read).
# ---------------------------------------------------------------------------


class TestFetchRetirementRecommendationsLatestBatchOnly:
    def test_fetch_returns_only_the_latest_nights_rows_not_a_stale_prior_night(self, isolated_db):
        """THE load-bearing pin for Finding 4: two nights of rows persisted;
        only the latest night's candidates must be returned. Currently RED
        (the un-fixed fetch returns both nights' rows verbatim)."""
        _seed_recommendation_backdated("old-night-cand", "old-night-sib", days_ago=1)
        _seed_recommendation("new-night-cand", "new-night-sib")

        result = app_module._fetch_retirement_recommendations()
        candidate_ids = {r.get("candidate_id") for r in result}

        assert "new-night-cand" in candidate_ids, (
            "The latest night's real recommendation must be present."
        )
        assert "old-night-cand" not in candidate_ids, (
            f"A prior night's stale recommendation ('old-night-cand', "
            f"backdated 1 day) leaked into the fetch alongside tonight's "
            f"batch: {candidate_ids!r}. _fetch_retirement_recommendations "
            "must return ONLY the rows sharing the most recent row's "
            "calendar date, not the newest-N rows verbatim across multiple "
            "nights (PR-level /code-review Finding 4)."
        )

    def test_route_response_excludes_a_stale_prior_night_batch(self, client, isolated_db):
        """Same invariant, exercised through the real HTTP route (not just the
        helper function directly) -- proves the fix is actually wired into
        the response path a real dashboard load would hit."""
        _seed_recommendation_backdated("old-night-cand-2", "old-night-sib-2", days_ago=3)
        _seed_recommendation("new-night-cand-2", "new-night-sib-2")

        resp = client.get("/api/retirement-recommendations")
        data = resp.get_json()
        candidate_ids = {r.get("candidate_id") for r in data["recommendations"]}

        assert "new-night-cand-2" in candidate_ids
        assert "old-night-cand-2" not in candidate_ids, (
            f"GET /api/retirement-recommendations surfaced a 3-day-stale "
            f"recommendation alongside tonight's batch: {candidate_ids!r}."
        )

    def test_same_candidate_persisted_two_nights_returns_only_the_latest_row(self, isolated_db):
        """A pair still flagged on consecutive nights produces a genuine
        DUPLICATE row (advisor_observations is append-only -- the nightly
        tick never upserts). The fetch must surface the candidate only ONCE
        (the latest night's row), not once per historical night it was ever
        flagged."""
        _seed_recommendation_backdated("repeat-cand", "repeat-sib", days_ago=1)
        _seed_recommendation("repeat-cand", "repeat-sib")

        result = app_module._fetch_retirement_recommendations()
        matching = [r for r in result if r.get("candidate_id") == "repeat-cand"]
        assert len(matching) == 1, (
            f"'repeat-cand' was flagged on 2 consecutive nights (append-only "
            f"duplicate rows) but appeared {len(matching)} times in the "
            f"fetch result -- expected exactly 1 (the latest night's row "
            "only)."
        )


# ---------------------------------------------------------------------------
# Route shape
# ---------------------------------------------------------------------------


class TestRetirementRecommendationsRouteShape:
    def test_returns_200(self, client, isolated_db):
        resp = client.get("/api/retirement-recommendations")
        assert resp.status_code == 200, (
            f"Expected 200 from GET /api/retirement-recommendations, got "
            f"{resp.status_code}. Route is not yet implemented -- RED."
        )

    def test_content_type_is_json(self, client, isolated_db):
        resp = client.get("/api/retirement-recommendations")
        assert resp.status_code == 200
        assert "application/json" in resp.content_type

    def test_response_has_recommendations_key(self, client, isolated_db):
        resp = client.get("/api/retirement-recommendations")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None
        assert "recommendations" in data
        assert isinstance(data["recommendations"], list)


# ---------------------------------------------------------------------------
# Empty / degraded states
# ---------------------------------------------------------------------------


class TestRetirementRecommendationsRouteEmptyState:
    def test_no_recommendations_returns_empty_list_not_error(self, client, isolated_db):
        resp = client.get("/api/retirement-recommendations")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["recommendations"] == []

    def test_read_failure_degrades_never_500(self, client, isolated_db, monkeypatch):
        monkeypatch.setattr(
            db_module,
            "get_advisor_observations_for_role",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("simulated DB failure")),
        )
        resp = client.get("/api/retirement-recommendations")
        assert resp.status_code != 500, (
            "A read failure must degrade honestly, never 500 the whole route."
        )


# ---------------------------------------------------------------------------
# Per-recommendation content: the authoritative raw_response schema surfaces
# ---------------------------------------------------------------------------


class TestRetirementRecommendationsRouteContent:
    def test_recommendation_row_surfaces_the_authoritative_field_names(self, client, isolated_db):
        _seed_recommendation(candidate_id="cand-sym-1", sibling_id="sib-sym-1")
        resp = client.get("/api/retirement-recommendations")
        data = resp.get_json()
        row = next(r for r in data["recommendations"] if r.get("candidate_id") == "cand-sym-1")
        missing = _RAW_RESPONSE_KEYS - row.keys()
        assert not missing, (
            f"GET /api/retirement-recommendations response row is missing the "
            f"authoritative raw_response fields: {missing}. The route must "
            "render the SAME literal key names retirement_recommender.py "
            "persists (see the raw_response schema in "
            "tests/advisors/test_retirement_recommender_persistence.py)."
        )

    def test_recommendation_row_values_match_the_persisted_raw_response(self, client, isolated_db):
        _seed_recommendation(candidate_id="cand-sym-2", sibling_id="sib-sym-2")
        resp = client.get("/api/retirement-recommendations")
        data = resp.get_json()
        row = next(r for r in data["recommendations"] if r.get("candidate_id") == "cand-sym-2")
        assert row["sibling_id"] == "sib-sym-2"
        assert row["correlation"] == pytest.approx(0.80)
        assert row["n_obs"] == 200
        assert row["candidate_metrics"]["annualized_return"] == pytest.approx(0.03)

    def test_multiple_recommendations_all_present(self, client, isolated_db):
        _seed_recommendation(candidate_id="cand-a", sibling_id="sib-a")
        _seed_recommendation(candidate_id="cand-b", sibling_id="sib-b")
        resp = client.get("/api/retirement-recommendations")
        data = resp.get_json()
        candidate_ids = {r.get("candidate_id") for r in data["recommendations"]}
        assert {"cand-a", "cand-b"} <= candidate_ids


# ---------------------------------------------------------------------------
# Strict-JSON safety (NaN/Infinity)
# ---------------------------------------------------------------------------


class TestRetirementRecommendationsRouteStrictJson:
    def test_no_nan_or_infinity_in_numeric_fields(self, client, isolated_db, monkeypatch):
        _seed_recommendation(candidate_id="cand-nan", sibling_id="sib-nan")

        real_getter = db_module.get_advisor_observations_for_role

        def _poisoned(role, *a, **k):
            rows = real_getter(role, *a, **k)
            for row in rows:
                if row.get("symphony_id") == "cand-nan":
                    row["raw_response"]["correlation"] = float("nan")
            return rows

        monkeypatch.setattr(db_module, "get_advisor_observations_for_role", _poisoned)

        resp = client.get("/api/retirement-recommendations")
        assert resp.status_code == 200
        raw_body = resp.get_data(as_text=True)
        assert "NaN" not in raw_body and "Infinity" not in raw_body, (
            f"Response body contains a bare NaN/Infinity token (invalid JSON) -- "
            f"must be sanitized to null. Body: {raw_body[:300]}"
        )
        data = resp.get_json()
        row = next(r for r in data["recommendations"] if r.get("candidate_id") == "cand-nan")
        val = row.get("correlation")
        if isinstance(val, float):
            assert not math.isnan(val)


# ---------------------------------------------------------------------------
# GET never writes
# ---------------------------------------------------------------------------


class TestRetirementRecommendationsRouteNeverWrites:
    def test_get_does_not_call_insert_advisor_observation(self, client, isolated_db, monkeypatch):
        def _explode(**kwargs):
            raise AssertionError("GET /api/retirement-recommendations must never write")

        monkeypatch.setattr(db_module, "insert_advisor_observation", _explode)
        resp = client.get("/api/retirement-recommendations")
        assert resp.status_code == 200

    def test_get_does_not_change_advisor_observations_row_count(self, client, isolated_db):
        before = db_module.get_advisor_observations_for_role("RETIREMENT_RECOMMENDATION", limit=50)
        client.get("/api/retirement-recommendations")
        client.get("/api/retirement-recommendations")
        after = db_module.get_advisor_observations_for_role("RETIREMENT_RECOMMENDATION", limit=50)
        assert len(before) == len(after) == 0


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------

_TEST_PASSWORD = "test-pw-retirement"
_TEST_SECRET_KEY = "test-secret-retirement"


@pytest.fixture()
def auth_client_no_session(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", _TEST_PASSWORD)
    monkeypatch.setenv("SECRET_KEY", _TEST_SECRET_KEY)
    monkeypatch.setattr(app_module, "_auth_check_enabled", True)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


class TestRetirementRecommendationsRouteAuthGate:
    def test_unauthenticated_xhr_returns_401(self, auth_client_no_session, isolated_db):
        resp = auth_client_no_session.get(
            "/api/retirement-recommendations", headers={"X-Requested-With": "XMLHttpRequest"}
        )
        assert resp.status_code == 401, (
            f"Unauthenticated XHR to /api/retirement-recommendations must return "
            f"401, got {resp.status_code}. If 404: route not yet implemented "
            "(RED). If 200/302: route is missing the auth gate."
        )


# ---------------------------------------------------------------------------
# Not a settings-write surface
# ---------------------------------------------------------------------------


class TestRetirementRecommendationsRouteNotWriteSurface:
    def test_route_path_not_in_settings_write_allowlist(self):
        allowlist = getattr(app_module, "_SETTINGS_WRITE_ALLOWLIST", None)
        if allowlist is None:
            pytest.skip("_SETTINGS_WRITE_ALLOWLIST not found on app module")
        assert "/api/retirement-recommendations" not in allowlist
