"""Conftest for tests/realtime_push/.

Provides:
  - `client`: authenticated Flask test client (auth gate DISABLED, same as suite default)
  - `client_no_auth`: test client with auth gate ENABLED and no session cookie — used
    to verify /api/events returns 401 for unauthenticated requests (AC-2).
  - _stub_get_api_state_dict: autouse stub so get_api_state_dict() never reaches the
    live DB or engine (consistent with tests/app/conftest.py and tests/dashboard/conftest.py).
  - _reset_account_totals_cache: autouse fixture that drains in-flight "cycle-refresh"
    daemon threads and resets _account_totals_cache to a clean state before every test.

Auth pattern follows tests/app/test_dashboard_auth.py: the suite-level autouse
`_disable_auth_for_tests` in tests/conftest.py sets _auth_check_enabled=False.
`client_no_auth` re-enables the gate via monkeypatch for its own scope.

_account_totals_cache isolation note:
  _notify_cycle_complete() spawns threading.Thread(target=_refresh_account_totals,
  daemon=True, name="cycle-refresh").  The thread target is bound to the module-level
  name at the time the Thread object is created — patch("app._refresh_account_totals")
  replaces only the module ATTRIBUTE, not the thread's already-bound target function.
  Consequently the thread ALWAYS executes the real _refresh_account_totals, regardless
  of any mock.  Without joining these threads before each test the cache can be left in
  an indeterminate state (stale flag set or wrong values written) by a thread from a
  prior test, causing non-deterministic failures.  The _reset_account_totals_cache
  fixture joins all live "cycle-refresh" threads (up to a short timeout) and then
  hard-resets the cache so every test starts clean.
"""

from __future__ import annotations

import threading
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

# Timeout (seconds) to join a lingering "cycle-refresh" daemon thread before
# giving up and hard-resetting the cache.  Short enough not to slow the suite.
_CYCLE_REFRESH_JOIN_TIMEOUT = 0.5


@pytest.fixture(autouse=True)
def _reset_account_totals_cache():
    """Join in-flight cycle-refresh threads and hard-reset _account_totals_cache.

    Root cause: _notify_cycle_complete() spawns threading.Thread(target=
    _refresh_account_totals, name="cycle-refresh", daemon=True).  The thread's
    target is bound to the real _refresh_account_totals at thread-creation time —
    patch("app._refresh_account_totals") replaces the module attribute but NOT
    the already-bound thread target.  A thread from a previous test may still be
    running when the next test starts, and can:
      - Set the stale flag (via mark_stale() — only called by _notify_cycle_complete,
        not by _refresh_account_totals directly), or
      - Fail to call refresh_written() (e.g. Composer unreachable → stale flag stays
        set from the previous _notify_cycle_complete call).

    This fixture joins all live "cycle-refresh" threads (up to _CYCLE_REFRESH_JOIN_TIMEOUT
    each) before the test runs, then hard-resets the cache by calling both clear()
    (which resets dict contents + stale flag per _StaleFlagDict.clear) and
    refresh_written() (belt-and-suspenders: ensures stale=False even if a thread
    squeezed in a mark_stale() between the join and the clear).

    The same reset runs in teardown (yield) so a test cannot leak state to the next.
    """
    import app as app_module

    def _drain_and_reset():
        # Join any live "cycle-refresh" threads from prior tests.
        for t in threading.enumerate():
            if t.name == "cycle-refresh" and t.is_alive():
                t.join(timeout=_CYCLE_REFRESH_JOIN_TIMEOUT)
        # Hard-reset: clear contents + stale flag, then confirm stale=False.
        app_module._account_totals_cache.clear()  # also resets _stale per _StaleFlagDict
        app_module._account_totals_cache.refresh_written()  # belt-and-suspenders

    _drain_and_reset()
    yield
    _drain_and_reset()


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
