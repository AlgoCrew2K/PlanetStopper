"""
RED tests for /api/state route additive fields (AC-P2.12.2).

Exercises the actual HTTP route via Flask test client — not get_api_state_dict()
in isolation. Tests will FAIL until get_api_state_dict() is wired into get_state().

All three response branches must carry port_state, exit_authority, daemon_started_at:
  - waiting (no bot_state, market open or closed)
  - frozen (market closed_frozen/pre_market with snapshot)
  - active (live state present, market open)
"""

from __future__ import annotations

import json
import logging
import os
import tempfile

import pytest


@pytest.fixture(scope="module")
def flask_client():
    fd, path = tempfile.mkstemp(suffix=".db", prefix="test_route_")
    os.close(fd)
    orig = os.environ.get("DB_PATH")
    orig_auth = os.environ.get("EXIT_AUTHORITY")
    os.environ["DB_PATH"] = path
    os.environ.setdefault("EXIT_AUTHORITY", "per_symphony")
    import database
    _old_level = logging.getLogger().level
    logging.getLogger().setLevel(logging.CRITICAL)
    try:
        database.init_db()
    finally:
        logging.getLogger().setLevel(_old_level)
    import app as app_module
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        yield client
    os.environ.pop("DB_PATH", None)
    if orig is not None:
        os.environ["DB_PATH"] = orig
    os.environ.pop("EXIT_AUTHORITY", None)
    if orig_auth is not None:
        os.environ["EXIT_AUTHORITY"] = orig_auth
    try:
        os.unlink(path)
    except OSError:
        pass


_ADDITIVE_FIELDS = {"port_state", "exit_authority", "daemon_started_at"}


class TestApiStateWaitingBranch:
    """Waiting branch: no bot_state present — additive fields must still appear."""

    def test_waiting_response_has_port_state(self, flask_client):
        resp = flask_client.get("/api/state")
        data = json.loads(resp.data)
        assert "port_state" in data, (
            "AC-P2.12.2: /api/state waiting branch must include 'port_state'"
        )

    def test_waiting_response_has_exit_authority(self, flask_client):
        resp = flask_client.get("/api/state")
        data = json.loads(resp.data)
        assert "exit_authority" in data, (
            "AC-P2.12.2: /api/state waiting branch must include 'exit_authority'"
        )

    def test_waiting_response_has_daemon_started_at(self, flask_client):
        resp = flask_client.get("/api/state")
        data = json.loads(resp.data)
        assert "daemon_started_at" in data, (
            "AC-P2.12.2: /api/state waiting branch must include 'daemon_started_at'"
        )

    def test_waiting_response_all_additive_fields_present(self, flask_client):
        resp = flask_client.get("/api/state")
        data = json.loads(resp.data)
        missing = _ADDITIVE_FIELDS - data.keys()
        assert not missing, (
            f"AC-P2.12.2: /api/state waiting branch missing additive fields: {missing}"
        )

    def test_exit_authority_value_is_valid(self, flask_client):
        resp = flask_client.get("/api/state")
        data = json.loads(resp.data)
        assert data.get("exit_authority") in ("per_symphony", "port_level"), (
            "AC-P2.12.2: exit_authority must reflect the active EXIT_AUTHORITY env value"
        )

    def test_daemon_started_at_is_iso8601(self, flask_client):
        resp = flask_client.get("/api/state")
        data = json.loads(resp.data)
        ts = data.get("daemon_started_at", "")
        assert "T" in ts and ts.endswith("Z"), (
            "AC-P2.12.2: daemon_started_at must be an ISO 8601 UTC string"
        )

    def test_existing_fields_preserved(self, flask_client):
        """AC-P2.12.2: No existing field renamed or removed — backward compat."""
        resp = flask_client.get("/api/state")
        data = json.loads(resp.data)
        assert "status" in data, "existing field 'status' must not be removed"
        assert "market_state" in data, "existing field 'market_state' must not be removed"
