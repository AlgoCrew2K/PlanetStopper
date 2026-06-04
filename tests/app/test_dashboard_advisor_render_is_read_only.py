"""
RED tests — Advisor dashboard routes must be read-only.

Plan deliverable (4): the route that renders Advisor output reads exclusively
from `advisor_observations` (append-only table) via the `advisor_ro_query`
accessor (rubric M-1), never calls an Advisor-write path.

Three contracts pinned here:

  A) GET /ai-advisor renders without any DB write call.
  B) POST /ai-advisor/suggest: calls ai_advisor.assemble_advisor_context and
     ai_advisor.request_suggestions but never calls any database mutator.
  C) POST /ai-advisor/accept: two permitted writes — save_symphony_strategy
     (config change) + record_llm_suggestion (AC-5 audit trail).  No other
     mutators: never save_state, write_fleet_alert, write_telemetry_row, etc.
  D) POST /ai-advisor/reject: one permitted write — record_llm_suggestion
     (AC-5 audit trail).  Never calls save_symphony_strategy (a rejected
     suggestion must NOT reach the config store).

The Advisor's read-only contract from rubric I-3 is: the Advisor cannot
trigger live action; the dashboard cannot gain action surface.  Phase 1
(Specification Critic + Overfitting Conscience) renders verdict data only;
no API mutation must be reachable from any of these routes.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch, call

import pytest

import app as app_module
import database as database_module


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def flask_client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _make_db_mock() -> MagicMock:
    mock = MagicMock(spec=database_module)
    mock.load_state.return_value = {}
    mock.get_symphony_strategy.return_value = {"params": {}, "locked_vars": []}
    mock.normalize_name.side_effect = lambda n: (n or "").lower()
    mock.DEFAULT_STRATEGY = {}
    mock.DEFAULT_LOCKED_VARS = []
    return mock


def _make_advisor_mock() -> MagicMock:
    mock = MagicMock()
    suggestion = MagicMock()
    suggestion.config_key = "TRIGGER_THRESHOLD_PCT"
    suggestion.current_value = 0.65
    suggestion.suggested_value = 0.72
    suggestion.rationale = "test rationale"
    suggestion.risk_direction = "tightens"
    suggestion.confidence = "high"
    suggestion.data_sufficiency = "sufficient"
    suggestion.oos_status = "passed"
    suggestion.oos_reason = None
    suggestion.impact = {"metric": "sharpe", "delta": 0.1}
    suggestion.model_dump.return_value = {
        "config_key": "TRIGGER_THRESHOLD_PCT",
        "current_value": 0.65,
        "suggested_value": 0.72,
        "rationale": "test rationale",
        "risk_direction": "tightens",
        "confidence": "high",
        "data_sufficiency": "sufficient",
        "oos_status": "passed",
        "oos_reason": None,
        "impact": {"metric": "sharpe", "delta": 0.1},
    }
    response = MagicMock()
    response.suggestions = [suggestion]
    mock.request_suggestions.return_value = (response, None)
    mock.assemble_advisor_context.return_value = {}
    mock.enforce_suggestion_allowlist.return_value = ([suggestion], [])
    mock.check_risk_direction_agreement.return_value = {"agrees": True}
    mock._read_current_strategy.return_value = ({}, [])
    mock.revalidate_suggestion_oos.return_value = {"passed": True, "detail": ""}
    mock.ConfigSuggestion.return_value = MagicMock(
        config_key="TRIGGER_THRESHOLD_PCT",
        suggested_value=0.72,
    )
    return mock


# ---------------------------------------------------------------------------
# A) GET /ai-advisor — pure template render, no DB writes
# ---------------------------------------------------------------------------


class TestAdvisorTabRenderIsReadOnly:
    """GET /ai-advisor must render without touching any DB write path."""

    def test_get_ai_advisor_tab_calls_no_database_mutators(self, flask_client):
        """GET /ai-advisor must not call any database write helper."""
        mock_db = _make_db_mock()

        with patch.object(app_module, "database", mock_db):
            resp = flask_client.get("/ai-advisor")

        # Template render is read-only; no write helpers should have been called
        mock_db.save_state.assert_not_called()
        mock_db.write_fleet_alert.assert_not_called()
        mock_db.save_symphony_strategy.assert_not_called()
        mock_db.record_exit_trigger.assert_not_called()
        mock_db.record_shadow_observation.assert_not_called()
        mock_db.write_telemetry_row.assert_not_called() if hasattr(mock_db, "write_telemetry_row") else None
        assert resp.status_code == 200, (
            f"GET /ai-advisor returned {resp.status_code}; expected 200."
        )

    def test_get_ai_advisor_tab_does_not_call_advisor_write_paths(self, flask_client):
        """GET /ai-advisor must not call any ai_advisor write path (no Claude invocation)."""
        mock_db = _make_db_mock()
        mock_advisor = MagicMock()

        with patch.object(app_module, "database", mock_db):
            with patch.object(app_module, "ai_advisor", mock_advisor):
                resp = flask_client.get("/ai-advisor")

        # The tab render must not trigger a Claude API call
        mock_advisor.request_suggestions.assert_not_called()
        mock_advisor.assemble_advisor_context.assert_not_called()
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# B) POST /ai-advisor/suggest — reads context, calls Claude, no DB mutation
# ---------------------------------------------------------------------------


class TestAdvisorSuggestIsReadOnly:
    """POST /ai-advisor/suggest must not mutate the database."""

    def test_suggest_does_not_call_save_state(self, flask_client):
        """POST /ai-advisor/suggest must not call database.save_state."""
        mock_db = _make_db_mock()
        mock_advisor = _make_advisor_mock()

        with patch.object(app_module, "database", mock_db):
            with patch.object(app_module, "ai_advisor", mock_advisor):
                resp = flask_client.post(
                    "/ai-advisor/suggest",
                    json={"symphony_id": "sym-001"},
                )

        mock_db.save_state.assert_not_called()

    def test_suggest_does_not_call_write_fleet_alert(self, flask_client):
        """POST /ai-advisor/suggest must not call database.write_fleet_alert."""
        mock_db = _make_db_mock()
        mock_advisor = _make_advisor_mock()

        with patch.object(app_module, "database", mock_db):
            with patch.object(app_module, "ai_advisor", mock_advisor):
                flask_client.post("/ai-advisor/suggest", json={"symphony_id": "sym-001"})

        mock_db.write_fleet_alert.assert_not_called()

    def test_suggest_does_not_call_save_symphony_strategy(self, flask_client):
        """POST /ai-advisor/suggest is a read+Claude-call; it must not write config."""
        mock_db = _make_db_mock()
        mock_advisor = _make_advisor_mock()

        with patch.object(app_module, "database", mock_db):
            with patch.object(app_module, "ai_advisor", mock_advisor):
                flask_client.post("/ai-advisor/suggest", json={"symphony_id": "sym-001"})

        mock_db.save_symphony_strategy.assert_not_called()

    def test_suggest_returns_suggestions_list(self, flask_client):
        """POST /ai-advisor/suggest must return a 'suggestions' key in the response."""
        mock_db = _make_db_mock()
        mock_advisor = _make_advisor_mock()

        with patch.object(app_module, "database", mock_db):
            with patch.object(app_module, "ai_advisor", mock_advisor):
                resp = flask_client.post("/ai-advisor/suggest", json={"symphony_id": "sym-001"})

        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None
        assert "suggestions" in data, (
            f"POST /ai-advisor/suggest must return 'suggestions'; got keys: {list(data.keys())}"
        )


# ---------------------------------------------------------------------------
# C) POST /ai-advisor/accept — only save_symphony_strategy is the allowed write
# ---------------------------------------------------------------------------


class TestAdvisorAcceptWriteIsNarrow:
    """POST /ai-advisor/accept may only write via save_symphony_strategy and
    record_llm_suggestion (AC-5 audit trail).

    All other database mutators must be untouched.  This pins the boundary:
    the accept route is a config-write + audit-trail path, not a state-mutation
    path.
    """

    def test_accept_only_writes_via_save_symphony_strategy(self, flask_client):
        """After all gates pass, accept calls save_symphony_strategy and
        record_llm_suggestion — no other database mutators."""
        mock_db = _make_db_mock()
        mock_advisor = _make_advisor_mock()

        with patch.object(app_module, "database", mock_db):
            with patch.object(app_module, "ai_advisor", mock_advisor):
                resp = flask_client.post(
                    "/ai-advisor/accept",
                    json={
                        "symphony_id": "sym-001",
                        "suggestion": {
                            "config_key": "TRIGGER_THRESHOLD_PCT",
                            "current_value": 0.65,
                            "suggested_value": 0.72,
                            "rationale": "test",
                            "risk_direction": "tightens",
                            "confidence": "high",
                            "data_sufficiency": "sufficient",
                        },
                    },
                )

        data = resp.get_json()
        assert data is not None

        # Only permitted writes: save_symphony_strategy + record_llm_suggestion (AC-5).
        mock_db.save_state.assert_not_called()
        mock_db.write_fleet_alert.assert_not_called()
        mock_db.record_exit_trigger.assert_not_called()
        mock_db.record_shadow_observation.assert_not_called()
        mock_db.record_autotune_run.assert_not_called()
        # AC-5: record_llm_suggestion IS now called on /accept — audit trail write.
        mock_db.record_llm_suggestion.assert_called_once()

    def test_accept_with_rejected_key_writes_nothing(self, flask_client):
        """POST /ai-advisor/accept when allowlist rejects must write nothing at all."""
        mock_db = _make_db_mock()
        mock_advisor = _make_advisor_mock()
        # Gate 1 rejects the key
        mock_advisor.enforce_suggestion_allowlist.return_value = ([], [MagicMock()])

        with patch.object(app_module, "database", mock_db):
            with patch.object(app_module, "ai_advisor", mock_advisor):
                resp = flask_client.post(
                    "/ai-advisor/accept",
                    json={
                        "symphony_id": "sym-001",
                        "suggestion": {
                            "config_key": "FORBIDDEN_KEY",
                            "current_value": 1.0,
                            "suggested_value": 2.0,
                            "rationale": "test",
                            "risk_direction": "tightens",
                            "confidence": "high",
                            "data_sufficiency": "sufficient",
                        },
                    },
                )

        data = resp.get_json()
        assert data is not None
        assert data.get("status") == "rejected"
        mock_db.save_symphony_strategy.assert_not_called()
        mock_db.save_state.assert_not_called()


# ---------------------------------------------------------------------------
# D) POST /ai-advisor/reject — pure acknowledgement, zero DB writes
# ---------------------------------------------------------------------------


class TestAdvisorRejectIsNarrow:
    """POST /ai-advisor/reject writes only to record_llm_suggestion (AC-5 audit
    trail) — never to save_symphony_strategy or any engine-state mutator.

    Contract change (AC-5): /reject now reads the payload and records the
    operator decision in the immutable llm_suggestions audit trail.  It must
    NOT mutate config (save_symphony_strategy) or engine state.
    """

    def test_reject_writes_nothing_to_database(self, flask_client):
        """POST /ai-advisor/reject must not call any database write helper other
        than record_llm_suggestion (the AC-5 audit trail write)."""
        mock_db = _make_db_mock()

        with patch.object(app_module, "database", mock_db):
            resp = flask_client.post(
                "/ai-advisor/reject",
                json={"symphony_id": "sym-001", "suggestion": {"config_key": "TRIGGER_THRESHOLD_PCT"}},
            )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None
        assert data.get("status") == "rejected"

        mock_db.save_state.assert_not_called()
        mock_db.write_fleet_alert.assert_not_called()
        mock_db.save_symphony_strategy.assert_not_called()
        # AC-5: record_llm_suggestion IS now called on /reject — audit trail write.
        mock_db.record_llm_suggestion.assert_called_once()
        mock_db.record_exit_trigger.assert_not_called()

    def test_reject_does_not_call_advisor_write_paths(self, flask_client):
        """POST /ai-advisor/reject must not trigger a Claude API call or advisor write."""
        mock_db = _make_db_mock()
        mock_advisor = _make_advisor_mock()

        with patch.object(app_module, "database", mock_db):
            with patch.object(app_module, "ai_advisor", mock_advisor):
                resp = flask_client.post(
                    "/ai-advisor/reject",
                    json={"config_key": "TRIGGER_THRESHOLD_PCT"},
                )

        # Reject is a no-op acknowledgement — no advisor calls should fire
        mock_advisor.request_suggestions.assert_not_called()
        mock_advisor.assemble_advisor_context.assert_not_called()
        mock_advisor.revalidate_suggestion_oos.assert_not_called()


# ---------------------------------------------------------------------------
# E) Advisor routes must use advisor_ro_query, not raw get_ro_connection, for DB reads
# ---------------------------------------------------------------------------


class TestAdvisorRouteUsesAdvisorRoQueryGateway:
    """The Advisor DB read path must go through database.advisor_ro_query (M-1 wall).

    app.py routes that render Advisor data must not call get_ro_connection()
    directly — they must call database.advisor_ro_query() or read-only helpers
    (get_symphony_strategy, load_state) that respect the frozen_eval wall.
    Direct get_ro_connection() bypasses the wall-breach tripwire.
    """

    def test_suggest_route_does_not_call_get_ro_connection_directly(self, flask_client):
        """POST /ai-advisor/suggest must not call database.get_ro_connection() directly.

        Advisor context assembly must go through named helpers or
        advisor_ro_query, never raw connection handles.
        """
        ro_connection_calls: list = []

        original_get_ro = database_module.get_ro_connection

        def _spy_get_ro():
            ro_connection_calls.append("get_ro_connection")
            return original_get_ro()

        mock_db = _make_db_mock()
        mock_advisor = _make_advisor_mock()

        # Patch both: the module attribute used by app.py AND the real database module
        with patch.object(app_module, "database", mock_db):
            with patch.object(app_module, "ai_advisor", mock_advisor):
                # Also intercept any direct calls via the database module imported in app
                mock_db.get_ro_connection.side_effect = _spy_get_ro
                resp = flask_client.post("/ai-advisor/suggest", json={"symphony_id": "sym-001"})

        # The suggest route body itself (not the named helpers) must not call get_ro_connection
        # directly — it should delegate to assemble_advisor_context which uses named helpers.
        # If get_ro_connection is called FROM the route body (not a helper), that is a bypass.
        # We verify this by checking that mock_db.get_ro_connection was not called
        # (it would be called if the route directly opens a connection).
        mock_db.get_ro_connection.assert_not_called(), (
            "POST /ai-advisor/suggest called database.get_ro_connection() directly. "
            "Advisor DB reads must go through named helpers (get_symphony_strategy, "
            "advisor_ro_query) — raw connection handles bypass the wall-breach tripwire."
        )
