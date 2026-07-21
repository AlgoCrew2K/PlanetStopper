"""
RED tests — F-005: minimal unauthenticated GET /health liveness probe.

Source: docs/audit/confidence-program/INSTITUTIONAL-READINESS-REPORT.md (running-register.md
line 42) — "_AUTH_EXEMPT_ENDPOINTS exempts /health but NO such route exists (CLAUDE.md's claim
is currently false; no liveness probe possible)". `_AUTH_EXEMPT_ENDPOINTS` (app.py:103-105)
already lists "health" — confirmed no `def health` / no `/health` route currently exists
(grep returned nothing at plan-time).

AC-2: GET /health returns 200 + honest fields unauthenticated; NOT in
_SETTINGS_WRITE_ALLOWLIST scope; no secrets; CLAUDE.md's existing exempt-list claim becomes true.

Fields required (per feature-plans/fix-ops-cluster.md Summary): {status, daemon_started_at,
last_successful_cycle_at}. Both non-status fields are ALREADY-COMPUTED module/state values —
_DAEMON_STARTED_AT (app.py:463) and state_data["last_successful_cycle_at"] (engine-written,
read the same way app.py:2327 already reads it) — no new computation.

Mock boundary: database.load_state (for last_successful_cycle_at) + database.get_connection
(read-write spy, must never be called — this is a read-only probe, architecture constraint 5).
No live DB, no live network. -n0 only.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

import app as app_module

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture()
def auth_enabled_client(monkeypatch):
    """Client with the real auth gate re-enabled (overrides the conftest
    autouse _disable_auth_for_tests fixture) — mirrors the auth_client
    pattern in tests/app/test_dashboard_auth.py so this test proves /health
    is reachable pre-login, not just reachable because auth-checking is
    globally off for the rest of the suite.
    """
    monkeypatch.setenv("DASHBOARD_PASSWORD", "test-pass-health-probe")
    monkeypatch.setenv("SECRET_KEY", "test-secret-health-probe")
    monkeypatch.setattr(app_module, "_auth_check_enabled", True)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture()
def rw_connection_spy():
    """Spy on database.get_connection (read-write) — any call fails the test.

    Mirrors tests/app/test_dashboard_routes_read_only_db.py's rw_connection_spy.
    """
    real_conn = sqlite3.connect(":memory:")
    m = MagicMock(return_value=real_conn)
    with patch.object(app_module.database, "get_connection", m):
        yield m
    real_conn.close()


# ---------------------------------------------------------------------------
# AC-2: route exists, 200, unauthenticated
# ---------------------------------------------------------------------------


def test_health_route_returns_200(client):
    with patch.object(
        app_module.database, "load_state", return_value={"last_successful_cycle_at": None}
    ):
        resp = client.get("/health")
    assert resp.status_code == 200, f"/health must return 200, got {resp.status_code}"


def test_health_reachable_without_authentication(auth_enabled_client):
    """AC-2: /health must be reachable pre-login — the auth gate is ENABLED
    for this test (unlike most of the suite) so a 302-to-/login or 401 here
    is a genuine failure, not an artifact of the global test-suite auth bypass.
    """
    with patch.object(
        app_module.database, "load_state", return_value={"last_successful_cycle_at": None}
    ):
        resp = auth_enabled_client.get("/health", follow_redirects=False)
    assert resp.status_code == 200, (
        f"/health must be exempt from the auth gate (it's already listed in "
        f"_AUTH_EXEMPT_ENDPOINTS) but got {resp.status_code} — "
        "the route's endpoint name must be exactly 'health'."
    )


# ---------------------------------------------------------------------------
# AC-2: honest field shape — assert shape/presence, never a hardcoded value
# ---------------------------------------------------------------------------


def test_health_response_is_json_with_status_field(client):
    with patch.object(
        app_module.database, "load_state", return_value={"last_successful_cycle_at": None}
    ):
        resp = client.get("/health")
    data = resp.get_json()
    assert data is not None, "/health must return a JSON body"
    assert "status" in data, f"/health JSON missing 'status' key: {data}"


def test_health_response_has_daemon_started_at_matching_module_constant(client):
    """daemon_started_at must reflect the real _DAEMON_STARTED_AT module value
    (already computed at import time, app.py:463) — not a fabricated/omitted field.
    """
    with patch.object(
        app_module.database, "load_state", return_value={"last_successful_cycle_at": None}
    ):
        resp = client.get("/health")
    data = resp.get_json()
    assert "daemon_started_at" in data, f"/health JSON missing 'daemon_started_at': {data}"
    assert data["daemon_started_at"] == app_module._DAEMON_STARTED_AT, (
        "daemon_started_at must be sourced from the module-level _DAEMON_STARTED_AT "
        f"constant; got {data['daemon_started_at']!r} vs {app_module._DAEMON_STARTED_AT!r}"
    )


def test_health_response_surfaces_last_successful_cycle_at_from_state(client):
    """last_successful_cycle_at must be read from database.load_state(), the
    same top-level engine-written field app.py:2327 already reads — proven by
    injecting a known fixture value and asserting it round-trips unchanged.
    """
    _fixture_ts = "2026-07-20T14:31:07+00:00"
    with patch.object(
        app_module.database,
        "load_state",
        return_value={"last_successful_cycle_at": _fixture_ts},
    ):
        resp = client.get("/health")
    data = resp.get_json()
    assert "last_successful_cycle_at" in data, (
        f"/health JSON missing 'last_successful_cycle_at': {data}"
    )
    assert data["last_successful_cycle_at"] == _fixture_ts, (
        f"expected the fixture-injected timestamp {_fixture_ts!r} to round-trip "
        f"through /health, got {data['last_successful_cycle_at']!r}"
    )


def test_health_daemon_degraded_returns_honest_none_not_fabricated_ok(client):
    """Edge case (plan): daemon-degraded state still returns honestly — a
    missing/None last_successful_cycle_at must surface as None, never a
    fabricated timestamp or a status that claims full health.
    """
    with patch.object(
        app_module.database, "load_state", return_value={}
    ):  # no last_successful_cycle_at key at all — degraded/cold-start state
        resp = client.get("/health")
    data = resp.get_json()
    assert resp.status_code == 200, "an honestly-degraded daemon must still answer the probe"
    assert data.get("last_successful_cycle_at") is None, (
        "a missing engine cycle timestamp must surface as None, not a fabricated value: "
        f"got {data.get('last_successful_cycle_at')!r}"
    )


# ---------------------------------------------------------------------------
# AC-2: security — no secrets, read-only, not a write-action surface
# ---------------------------------------------------------------------------


def test_health_response_leaks_no_secret_env_values(client, monkeypatch):
    """/health must never echo credential/env values into its response body."""
    _sentinel_secret = "SENTINEL-SECRET-VALUE-DO-NOT-LEAK-9f3a"
    monkeypatch.setenv("COMPOSER_SECRET", _sentinel_secret)
    monkeypatch.setenv("DASHBOARD_PASSWORD", _sentinel_secret)
    monkeypatch.setenv("SECRET_KEY", _sentinel_secret)
    with patch.object(
        app_module.database, "load_state", return_value={"last_successful_cycle_at": None}
    ):
        resp = client.get("/health")
    assert resp.status_code == 200, (
        f"/health must return 200 for this security check to be meaningful, got "
        f"{resp.status_code}"
    )
    body = resp.get_data(as_text=True)
    assert _sentinel_secret not in body, (
        "/health response body must never contain env/credential values — "
        "found the sentinel secret value in the response"
    )


def test_health_does_not_open_a_read_write_db_connection(client, rw_connection_spy):
    """Architecture constraint 5 (read-only) — /health must never call
    database.get_connection() (the read-write path); it reads via
    database.load_state() only (or an equivalent read-only accessor).
    """
    with patch.object(
        app_module.database, "load_state", return_value={"last_successful_cycle_at": None}
    ):
        resp = client.get("/health")
    assert resp.status_code == 200, (
        f"/health must return 200 for this read-only check to be meaningful, got "
        f"{resp.status_code}"
    )
    assert rw_connection_spy.call_count == 0, (
        f"/health opened a read-write DB connection {rw_connection_spy.call_count} time(s) — "
        "it must be strictly read-only (no DB write path, AC-2)."
    )


def test_health_only_accepts_get_not_post():
    """/health is a liveness probe, not a write-action surface — POST must not
    be silently accepted as an alias for GET (it should 405, matching Flask's
    default behavior for a route registered without methods=["POST"]).
    """
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        resp = c.post("/health")
    assert resp.status_code == 405, (
        f"POST /health must be rejected (405) — it is a read-only GET-only probe, "
        f"got {resp.status_code}"
    )


def test_health_endpoint_name_not_in_settings_write_allowlist():
    """/health must never be treated as a settings-write path — its endpoint
    name/route string must not appear in _SETTINGS_WRITE_ALLOWLIST (which
    gates the two guarded write paths per architecture constraint 2).
    """
    allowlist = getattr(app_module, "_SETTINGS_WRITE_ALLOWLIST", None)
    assert allowlist is not None, "_SETTINGS_WRITE_ALLOWLIST must exist (pre-existing invariant)"
    assert "health" not in allowlist, (
        "'health' must never appear in _SETTINGS_WRITE_ALLOWLIST — /health is read-only"
    )
