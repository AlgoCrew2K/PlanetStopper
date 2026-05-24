"""
RED tests — Dashboard routes must never open a read-write DB connection.

Architecture constraint 5: Templates open SQLite read-only; UI never reruns
the engine.  Architecture constraint 2: the dashboard is a read-only operator
surface — never an action surface for live trades.

Every Flask route in app.py that touches the database must reach the DB
exclusively through `database.get_ro_connection()`.  Any route that calls
`database.get_connection()` (read-write) fails the test.

Strategy: patch both `database.get_connection` and `database.get_ro_connection`
with spies that record calls, then invoke each route and assert
  - get_connection was NOT called (no read-write path executed)
  - get_ro_connection WAS called when the route is known to read from DB

These tests are structural / behavioral, not data tests.  They will be RED
until every route that currently calls get_connection is moved to
get_ro_connection (or removed from the route).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch, call
import sqlite3

import pytest

import app as app_module
import database as database_module


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def rw_connection_spy():
    """Spy on database.get_connection — any call is a test failure.

    Returns a MagicMock that records calls so tests can assert it was never
    invoked.  The mock returns a real in-memory read-write SQLite connection
    so that routes don't crash mid-execution with a TypeError.
    """
    real_conn = sqlite3.connect(":memory:")
    m = MagicMock(return_value=real_conn)
    with patch.object(database_module, "get_connection", m):
        yield m
    real_conn.close()


def _make_ro_stub():
    """Return a MagicMock that behaves like a read-only SQLite connection."""
    conn = MagicMock(spec=sqlite3.Connection)
    conn.execute.return_value = MagicMock(fetchone=lambda: None, fetchall=lambda: [])
    conn.close.return_value = None
    return conn


# ---------------------------------------------------------------------------
# GET-only read routes — must use get_ro_connection exclusively
# ---------------------------------------------------------------------------


class TestReadRoutesDontOpenRWConnection:
    """Every GET route that reads state must NOT call get_connection (read-write)."""

    def _patch_db_and_analytics(self):
        """Return a context-manager tuple: patched database, patched analytics."""
        mock_db = MagicMock()
        mock_db.get_ro_connection.return_value = _make_ro_stub()
        mock_db.load_state.return_value = {}
        mock_db.load_chart_history.return_value = {"symphonies": {}}
        mock_db.get_triggers.return_value = []
        mock_db.read_fleet_alert.return_value = None
        mock_db.normalize_name.side_effect = lambda n: (n or "").lower()
        mock_db.get_symphony_strategy.return_value = None
        mock_db.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
        mock_db.get_all_autotune_runs.return_value = []
        return mock_db

    def test_api_state_does_not_call_get_connection(self, client, rw_connection_spy):
        """GET /api/state must not open a read-write DB connection.

        The route body explicitly acquires get_ro_connection() for the lock
        query.  get_connection() must never be reached.
        """
        mock_db = self._patch_db_and_analytics()
        mock_analytics = MagicMock()
        mock_analytics.get_portfolio_daily_returns_from_shadow.return_value = None
        mock_analytics.get_history_with_cache_invalidation.return_value = {}
        mock_analytics.compute_aggregate_returns.return_value = ([], [], [])
        mock_analytics._POST_MORTEMS_DIR = "/tmp/pm"
        mock_analytics._HISTORY_CACHE = {"key": None, "data": None}

        with patch.object(app_module, "database", mock_db):
            with patch.object(app_module, "analytics", mock_analytics):
                resp = client.get("/api/state")

        assert rw_connection_spy.call_count == 0, (
            f"/api/state opened a read-write DB connection {rw_connection_spy.call_count} time(s). "
            "Routes must use database.get_ro_connection() exclusively."
        )

    def test_api_logs_does_not_call_get_connection(self, client, rw_connection_spy):
        """GET /api/logs/<symphony_id> must not open a read-write connection."""
        mock_db = MagicMock()
        mock_db.get_ro_connection.return_value = _make_ro_stub()
        mock_db.get_symphony_logs.return_value = []

        with patch.object(app_module, "database", mock_db):
            resp = client.get("/api/logs/sym-001")

        assert rw_connection_spy.call_count == 0, (
            f"/api/logs opened a read-write DB connection. "
            "Routes must use database.get_ro_connection() exclusively."
        )

    def test_api_triggers_does_not_call_get_connection(self, client, rw_connection_spy):
        """GET /api/triggers must not open a read-write connection."""
        mock_db = MagicMock()
        mock_db.get_ro_connection.return_value = _make_ro_stub()
        mock_db.get_triggers.return_value = []

        with patch.object(app_module, "database", mock_db):
            resp = client.get("/api/triggers")

        assert rw_connection_spy.call_count == 0, (
            f"/api/triggers opened a read-write DB connection. "
            "Routes must use database.get_ro_connection() exclusively."
        )

    def test_api_chart_does_not_call_get_connection(self, client, rw_connection_spy):
        """GET /api/chart/<symphony_id> must not open a read-write connection."""
        mock_db = MagicMock()
        ro_stub = _make_ro_stub()
        ro_stub.execute.return_value = MagicMock(fetchall=lambda: [])
        mock_db.get_ro_connection.return_value = ro_stub
        mock_db.load_chart_history.return_value = {"symphonies": {}}

        with patch.object(app_module, "database", mock_db):
            resp = client.get("/api/chart/sym-001")

        assert rw_connection_spy.call_count == 0, (
            f"/api/chart opened a read-write DB connection. "
            "Routes must use database.get_ro_connection() exclusively."
        )

    def test_api_settings_get_does_not_call_get_connection(self, client, rw_connection_spy):
        """GET /api/settings must not open a read-write connection."""
        mock_db = MagicMock()
        mock_db.load_state.return_value = {}
        mock_db.normalize_name.side_effect = lambda n: (n or "").lower()
        mock_db.get_symphony_strategy.return_value = None
        mock_db.DEFAULT_STRATEGY = {}

        with patch.object(app_module, "database", mock_db):
            resp = client.get("/api/settings")

        assert rw_connection_spy.call_count == 0, (
            f"GET /api/settings opened a read-write DB connection. "
            "Read routes must use get_ro_connection() exclusively."
        )

    def test_api_autotune_runs_does_not_call_get_connection(self, client, rw_connection_spy):
        """GET /api/autotune-runs must not open a read-write connection."""
        mock_db = MagicMock()
        mock_db.get_ro_connection.return_value = _make_ro_stub()
        mock_db.get_all_autotune_runs.return_value = []

        with patch.object(app_module, "database", mock_db):
            resp = client.get("/api/autotune-runs")

        assert rw_connection_spy.call_count == 0, (
            f"/api/autotune-runs opened a read-write DB connection. "
            "Routes must use database.get_ro_connection() exclusively."
        )


# ---------------------------------------------------------------------------
# Write routes that exist by design must NOT use get_connection for DB state reads
# ---------------------------------------------------------------------------


class TestWriteRoutesReadPathIsReadOnly:
    """Write routes that mutate through helpers must still read via ro_connection.

    Even routes that legitimately write (save_settings, ai_advisor_accept)
    must not substitute get_connection() for the read-side of the operation.
    Specifically: load_state, get_symphony_strategy calls inside the route
    must be read-only; the write side goes through named write helpers
    (save_state, save_symphony_strategy) not through a raw get_connection call.
    """

    def test_ai_advisor_accept_reads_via_named_helper_not_get_connection(
        self, client, rw_connection_spy
    ):
        """POST /ai-advisor/accept reads strategy via get_symphony_strategy, not get_connection.

        get_connection is a raw read-write handle.  The accept route must call
        database.get_symphony_strategy() (which internally uses read-only path)
        and database.save_symphony_strategy() (write helper), never bare
        get_connection().
        """
        mock_db = MagicMock()
        mock_db.get_symphony_strategy.return_value = {"params": {}, "locked_vars": []}
        mock_db.save_symphony_strategy.return_value = None

        mock_advisor = MagicMock()
        mock_advisor.ConfigSuggestion.return_value = MagicMock(
            config_key="TRIGGER_THRESHOLD_PCT",
            suggested_value=0.72,
        )
        # Gate 1: allowlist passes
        mock_advisor.enforce_suggestion_allowlist.return_value = ([MagicMock()], [])
        # Gate 2: direction check
        mock_advisor.check_risk_direction_agreement.return_value = {"agrees": True}
        # Gate 3: OOS
        mock_advisor.revalidate_suggestion_oos.return_value = {"passed": True, "detail": ""}

        with patch.object(app_module, "database", mock_db):
            with patch.object(app_module, "ai_advisor", mock_advisor):
                resp = client.post(
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

        assert rw_connection_spy.call_count == 0, (
            "POST /ai-advisor/accept called get_connection() (read-write handle) directly. "
            "DB reads must go through named helpers (get_symphony_strategy); "
            "DB writes must go through save_symphony_strategy — never raw get_connection()."
        )
