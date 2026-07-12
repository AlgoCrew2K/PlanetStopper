"""
RED tests — GET /api/candidate-alert (AC-2/AC-3/AC-6/AC-8, feature-plans/candidate-alert.md).

The route does not exist yet; every test in this file is expected to fail RED
until the implementer adds it.

Contract under test:
- AC-2: {"new_valid_count": int} — count of NEW, UNVIEWED, VALID (verdict ==
  'ADOPT_CANDIDATE') weekly-suggestion candidates. See
  tests/database/test_033_candidate_alert_state.py module docstring for the
  full verdict-classification trace (KEEP_INCUMBENT/REJECT_VETO_FAILED do NOT
  count — this corrects the feature-plan's literal, incorrect AC-2 text).
- AC-3: {"last_run": {"ran_at", "evaluated", "survivors"} | None} — the latest
  weekly batch aggregate, visible even at survivors==0 (the "know it's working"
  case); honest None when no weekly run has ever occurred.
- AC-6: malformed/empty observation data -> honest degrade (new_valid_count=0,
  last_run=None), never raises, never blocks render.
- AC-8: route sits behind the existing _auth_before_request hook.

Rows are seeded via database.insert_advisor_observation (the real production
write path) so every expected count is DERIVED from what the test seeded —
never a hardcoded literal (feedback_no_hardcoded_test_values).
"""

from __future__ import annotations

import time

import pytest

import app as app_module
import database as db_module

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """Flask test client — no port bound."""
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _seed_observation(advisor_role: str, verdict: str | None, symphony_id: str = "sym-a") -> int:
    """Insert a real advisor_observations row via the production write path."""
    return db_module.insert_advisor_observation(
        advisor_role=advisor_role,
        subject_type="test_subject",
        subject_id=f"cand-{time.time_ns()}",
        verdict=verdict,
        raw_response={},
        symphony_id=symphony_id,
    )


# ---------------------------------------------------------------------------
# AC-2/AC-3: Route exists, returns 200, correct JSON shape
# ---------------------------------------------------------------------------


class TestCandidateAlertRouteShape:
    def test_candidate_alert_returns_200(self, client):
        """Route must exist and return 200 even with zero rows seeded.

        Fails RED because the route does not exist (404).
        """
        resp = client.get("/api/candidate-alert")
        assert resp.status_code == 200, (
            f"Expected 200 from GET /api/candidate-alert, got {resp.status_code}. "
            "Route is not yet implemented — RED."
        )

    def test_response_has_required_keys(self, client):
        """Response must contain exactly the two documented top-level keys."""
        resp = client.get("/api/candidate-alert")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None, "Response must be valid JSON"
        for key in ("new_valid_count", "last_run"):
            assert key in data, (
                f"Missing required key '{key}' in /api/candidate-alert response. "
                f"Got keys: {list(data.keys())}"
            )

    def test_content_type_is_json(self, client):
        resp = client.get("/api/candidate-alert")
        assert resp.status_code == 200
        assert "application/json" in resp.content_type, (
            f"Expected JSON content-type, got '{resp.content_type}'"
        )


# ---------------------------------------------------------------------------
# AC-2: new_valid_count — the verdict-classification contract
# ---------------------------------------------------------------------------


class TestNewValidCountRoute:
    def test_survivor_row_increments_count(self, client):
        """A verdict=='ADOPT_CANDIDATE' row increments new_valid_count by 1."""
        _seed_observation("ASSET_SWAP", "ADOPT_CANDIDATE")
        resp = client.get("/api/candidate-alert")
        assert resp.status_code == 200
        assert resp.get_json()["new_valid_count"] == 1

    def test_keep_incumbent_row_does_not_increment_count(self, client):
        """THE crux adversarial case: KEEP_INCUMBENT must NOT badge the operator.

        The feature-plan's literal AC-2 text (`verdict != 'REJECT_VETO_FAILED'`)
        would incorrectly count this. The correct rule is verdict=='ADOPT_CANDIDATE'
        only — KEEP_INCUMBENT is the common no-change outcome, exactly the noise
        AC-2 says must not alert.
        """
        _seed_observation("LOGIC_CHANGE", "KEEP_INCUMBENT")
        resp = client.get("/api/candidate-alert")
        assert resp.status_code == 200
        assert resp.get_json()["new_valid_count"] == 0, (
            "KEEP_INCUMBENT must not increment new_valid_count — see the "
            "verdict-classification correction in this module's docstring."
        )

    def test_rejected_row_does_not_increment_count(self, client):
        """A REJECT_VETO_FAILED row must not increment the badge."""
        _seed_observation("STRATEGY_BUILDER", "REJECT_VETO_FAILED")
        resp = client.get("/api/candidate-alert")
        assert resp.status_code == 200
        assert resp.get_json()["new_valid_count"] == 0

    def test_mixed_batch_counts_only_the_survivor(self, client):
        """A batch of 1 survivor + 2 rejects reports new_valid_count == 1, not 3."""
        _seed_observation("ASSET_SWAP", "ADOPT_CANDIDATE")
        _seed_observation("ASSET_SWAP", "KEEP_INCUMBENT")
        _seed_observation("LOGIC_CHANGE", "REJECT_VETO_FAILED")
        resp = client.get("/api/candidate-alert")
        assert resp.status_code == 200
        assert resp.get_json()["new_valid_count"] == 1

    def test_non_weekly_role_never_counts(self, client):
        """AC-6/role-scoping: a MARKET_PRISM row with verdict=='ADOPT_CANDIDATE' must
        never contribute — role scope is exactly {ASSET_SWAP, LOGIC_CHANGE,
        STRATEGY_BUILDER}."""
        _seed_observation("MARKET_PRISM", "ADOPT_CANDIDATE")
        resp = client.get("/api/candidate-alert")
        assert resp.status_code == 200
        assert resp.get_json()["new_valid_count"] == 0

    def test_zero_rows_gives_zero_count_no_error(self, client):
        """AC-6: an empty table -> new_valid_count==0, 200, never raises."""
        resp = client.get("/api/candidate-alert")
        assert resp.status_code == 200
        assert resp.get_json()["new_valid_count"] == 0


# ---------------------------------------------------------------------------
# AC-3: last_run aggregate visibility
# ---------------------------------------------------------------------------


class TestLastRunRoute:
    def test_no_run_ever_gives_null_last_run(self, client):
        """Honest empty-state: no weekly-suggestion row ever -> last_run is null."""
        resp = client.get("/api/candidate-alert")
        assert resp.status_code == 200
        assert resp.get_json()["last_run"] is None, (
            "last_run must be null (JSON None) when no weekly run has ever occurred."
        )

    def test_all_rejected_run_shows_in_last_run_with_zero_survivors(self, client):
        """AC-3 'know it's working' case: an all-rejected run is still visible —
        evaluated>0, survivors==0, last_run is NOT null, and new_valid_count stays 0."""
        _seed_observation("ASSET_SWAP", "KEEP_INCUMBENT")
        _seed_observation("LOGIC_CHANGE", "REJECT_VETO_FAILED")

        resp = client.get("/api/candidate-alert")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["new_valid_count"] == 0
        assert data["last_run"] is not None, (
            "An all-rejected run must still populate last_run — this is the "
            "'the weekly job ran and found nothing' signal the operator needs "
            "to know it's alive."
        )
        assert data["last_run"]["survivors"] == 0
        assert data["last_run"]["evaluated"] == 2
        assert data["last_run"]["ran_at"], "ran_at must be a non-empty timestamp string"

    def test_last_run_has_required_keys(self, client):
        _seed_observation("ASSET_SWAP", "ADOPT_CANDIDATE")
        resp = client.get("/api/candidate-alert")
        assert resp.status_code == 200
        last_run = resp.get_json()["last_run"]
        for key in ("ran_at", "evaluated", "survivors"):
            assert key in last_run, f"last_run missing key '{key}': {last_run}"


# ---------------------------------------------------------------------------
# AC-6: honest degradation, never raises
# ---------------------------------------------------------------------------


class TestHonestDegradation:
    def test_null_verdict_row_never_raises_and_does_not_badge(self, client):
        """A row with verdict=None must not raise and must not count."""
        _seed_observation("ASSET_SWAP", None)
        resp = client.get("/api/candidate-alert")
        assert resp.status_code == 200, "A null-verdict row must never 500 the route."
        assert resp.get_json()["new_valid_count"] == 0

    def test_db_error_degrades_to_honest_empty_state_not_500(self, client, monkeypatch):
        """AC-6: if the underlying DB accessor raises, the route must degrade to
        an honest empty response, not crash with a 500."""

        def _raise(*args, **kwargs):
            raise RuntimeError("simulated DB failure")

        monkeypatch.setattr(db_module, "get_candidate_alert_new_valid_count", _raise)
        monkeypatch.setattr(db_module, "get_candidate_alert_last_run", _raise)

        resp = client.get("/api/candidate-alert")
        assert resp.status_code == 200, (
            f"A DB accessor failure must degrade honestly (200, empty state), "
            f"not surface as a 500. Got {resp.status_code}."
        )
        data = resp.get_json()
        assert data["new_valid_count"] == 0
        assert data["last_run"] is None


# ---------------------------------------------------------------------------
# Read-only: no DB writes on the GET path
# ---------------------------------------------------------------------------


class TestCandidateAlertReadOnly:
    def test_route_makes_no_database_writes(self, client, monkeypatch):
        """GET /api/candidate-alert must never write — no marker mutation on a
        plain read (mark-viewed is a SEPARATE, explicit POST)."""
        from unittest.mock import MagicMock

        write_calls: list[str] = []

        def _record_write(method_name):
            def _fn(*args, **kwargs):
                write_calls.append(method_name)
                return MagicMock()

            return _fn

        write_methods = [
            "insert_advisor_observation",
            "set_symphony_live_mode",
            "set_candidate_alert_viewed_marker",
            "mark_candidate_alert_viewed",
        ]
        for method in write_methods:
            if hasattr(db_module, method):
                monkeypatch.setattr(db_module, method, _record_write(method))

        resp = client.get("/api/candidate-alert")
        if resp.status_code == 200:
            assert not write_calls, f"GET /api/candidate-alert made write calls: {write_calls}"


# ---------------------------------------------------------------------------
# AC-8: Auth gate (route sits behind DE-AUTH-001)
# ---------------------------------------------------------------------------

_TEST_PASSWORD = "test-pw-candidate-alert"
_TEST_SECRET_KEY = "test-secret-candidate-alert"


@pytest.fixture
def auth_client_no_session(monkeypatch):
    """Flask test client with auth gate ENABLED but no active session."""
    monkeypatch.setenv("DASHBOARD_PASSWORD", _TEST_PASSWORD)
    monkeypatch.setenv("SECRET_KEY", _TEST_SECRET_KEY)
    monkeypatch.setattr(app_module, "_auth_check_enabled", True)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


class TestCandidateAlertAuthGate:
    def test_unauthenticated_xhr_returns_401(self, auth_client_no_session):
        """AC-8: unauthenticated XHR GET -> 401 (route is behind the auth gate).

        Fails RED because the route does not exist (404 rather than 401).
        """
        resp = auth_client_no_session.get(
            "/api/candidate-alert",
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert resp.status_code == 401, (
            f"AC-8: unauthenticated XHR to /api/candidate-alert must return 401, "
            f"got {resp.status_code}. "
            "If 404: route not yet implemented (RED). "
            "If 200/302: route is missing the auth gate."
        )
