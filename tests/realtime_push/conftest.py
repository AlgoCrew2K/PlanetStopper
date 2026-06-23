"""Conftest for tests/realtime_push/.

Provides:
  - `client`: authenticated Flask test client (auth gate DISABLED, same as suite default)
  - `client_no_auth`: test client with auth gate ENABLED and no session cookie — used
    to verify /api/events returns 401 for unauthenticated requests (AC-2).
  - _stub_get_api_state_dict: autouse stub so get_api_state_dict() never reaches the
    live DB or engine (consistent with tests/app/conftest.py and tests/dashboard/conftest.py).

Auth pattern follows tests/app/test_dashboard_auth.py: the suite-level autouse
`_disable_auth_for_tests` in tests/conftest.py sets _auth_check_enabled=False.
`client_no_auth` re-enables the gate via monkeypatch for its own scope.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

_API_STATE_STUB = {
    "bot_state": {},
    "is_locked": False,
    "port_state": {},
    "exit_authority": {},
    "daemon_started_at": None,
}

_TEST_PASSWORD = "rt-push-test-pass-abc123"
_TEST_SECRET_KEY = "rt-push-test-secret-key-xyz789"


@pytest.fixture(autouse=True)
def _stub_get_api_state_dict():
    """Prevent get_api_state_dict from reaching live DB or engine for all tests here."""
    import app as app_module

    with patch.object(app_module, "get_api_state_dict", return_value=_API_STATE_STUB):
        yield


@pytest.fixture()
def client():
    """Authenticated Flask test client with auth gate DISABLED (suite default)."""
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture()
def client_no_auth(monkeypatch):
    """Flask test client with auth gate ENABLED and no session — produces 401 on /api/*."""
    import app as app_module

    monkeypatch.setenv("DASHBOARD_PASSWORD", _TEST_PASSWORD)
    monkeypatch.setenv("SECRET_KEY", _TEST_SECRET_KEY)
    # Override the suite-level _disable_auth_for_tests to enable the real gate.
    monkeypatch.setattr(app_module, "_auth_check_enabled", True)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c
