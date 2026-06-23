"""RED tests — AC-4: /api/state returns freshest cycle data after engine write.

The stale-cache regression: if _account_totals_cache is populated with values from
a prior cycle, and the engine cycle completes (trigger_alpha_bot exits), /api/state
must NOT serve the stale cached values.

The hook: _notify_cycle_complete() / trigger_alpha_bot() must also invalidate or
refresh _account_totals_cache so the very next get_state() call serves fresh data.

These tests fail until rt-impl:
  - Calls _refresh_account_totals() (or clears _account_totals_cache) in the
    cycle-completion hook inside trigger_alpha_bot() / _notify_cycle_complete().

No live Composer API. All external calls mocked.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import app as app_module
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_account_totals_cache():
    """Ensure _account_totals_cache is clean before and after each test."""
    try:
        app_module._account_totals_cache.clear()
    except AttributeError:
        pytest.fail(
            "_account_totals_cache not found on app module. "
            "rt-impl must add `_account_totals_cache: dict = {}` at module level."
        )
    yield
    try:
        app_module._account_totals_cache.clear()
    except AttributeError:
        pass


# ---------------------------------------------------------------------------
# AC-4: cache cleared/refreshed at cycle completion
# ---------------------------------------------------------------------------


class TestCycleCompletionInvalidatesStaleCache:
    def test_account_totals_cache_is_cleared_or_refreshed_after_trigger_alpha_bot(self):
        """After trigger_alpha_bot() completes, _account_totals_cache must NOT contain
        the stale values that were present before the cycle.

        Scenario:
        1. Pre-populate cache with stale sentinel values.
        2. Run trigger_alpha_bot() with mocked subprocess (no real engine).
        3. Assert the stale sentinel is gone (cache cleared) OR was replaced
           (a real refresh ran and wrote new values).

        Fails until rt-impl adds cache invalidation to the cycle-completion hook.
        """
        _STALE_SENTINEL = -9999.0
        app_module._account_totals_cache["portfolio_value"] = _STALE_SENTINEL
        app_module._account_totals_cache["portfolio_cr"] = _STALE_SENTINEL

        with (
            patch("subprocess.run", return_value=MagicMock(returncode=0)),
            patch("builtins.open", MagicMock()),
            # Prevent _refresh_account_totals from reaching Composer — it will either
            # succeed with a mock (replacing the stale value) or be called and fail
            # gracefully. Either way the STALE_SENTINEL must not remain.
            patch("requests.get", return_value=MagicMock(status_code=200, json=lambda: {})),
        ):
            app_module.trigger_alpha_bot()

        current_value = app_module._account_totals_cache.get("portfolio_value")
        assert current_value != pytest.approx(_STALE_SENTINEL, rel=1e-6), (
            f"After trigger_alpha_bot(), _account_totals_cache['portfolio_value'] still holds "
            f"the stale sentinel {_STALE_SENTINEL}. "
            "rt-impl: call _account_totals_cache.clear() (or _refresh_account_totals()) "
            "in the cycle-completion hook so /api/state never serves data from the prior cycle."
        )

    def test_notify_cycle_complete_clears_account_totals_cache(self):
        """_notify_cycle_complete() must clear or refresh _account_totals_cache.

        This is the direct unit test for the cache-invalidation contract on the
        notification helper itself — separate from trigger_alpha_bot's orchestration.

        Fails until rt-impl adds cache invalidation to _notify_cycle_complete().
        """
        _STALE_SENTINEL = -8888.0
        app_module._account_totals_cache["portfolio_value"] = _STALE_SENTINEL

        # _notify_cycle_complete may call _refresh_account_totals (live Composer) —
        # mock requests.get to prevent a live call.
        with patch("requests.get", return_value=MagicMock(status_code=200, json=lambda: {})):
            app_module._notify_cycle_complete()

        current_value = app_module._account_totals_cache.get("portfolio_value", None)
        assert current_value != pytest.approx(_STALE_SENTINEL, rel=1e-6), (
            f"After _notify_cycle_complete(), _account_totals_cache still has stale "
            f"sentinel {_STALE_SENTINEL}. "
            "rt-impl: call _account_totals_cache.clear() or _refresh_account_totals() "
            "inside _notify_cycle_complete() to ensure /api/state is always fresh post-cycle."
        )


class TestGetStateReturnsUpdatedValuesAfterCycleWrite:
    def test_get_state_does_not_return_stale_cached_portfolio_value(self, client):
        """/api/state must not return a stale portfolio_value that predates the
        most recently completed engine cycle.

        Scenario:
        1. Pre-populate cache with a stale sentinel value.
        2. Simulate a cycle completion (call _notify_cycle_complete or the equivalent
           invalidation hook directly).
        3. Call GET /api/state.
        4. Assert the response does NOT contain the stale sentinel as portfolio_value.

        This is the end-to-end regression guard for AC-4.
        Fails until rt-impl clears/refreshes the cache on cycle completion.
        """
        _STALE_SENTINEL = -7777.0
        app_module._account_totals_cache["portfolio_value"] = _STALE_SENTINEL

        # Simulate cycle completion: call invalidation hook directly
        with patch("requests.get", return_value=MagicMock(status_code=200, json=lambda: {})):
            app_module._notify_cycle_complete()

        response = client.get("/api/state")
        assert response.status_code == 200, (
            f"GET /api/state returned {response.status_code}; expected 200."
        )

        data = response.get_json()
        # The portfolio_strip block (if present) must not carry the stale sentinel.
        port_strip = data.get("portfolio_strip") if isinstance(data, dict) else None
        if port_strip and isinstance(port_strip, dict):
            account_value = port_strip.get("account_value")
            if account_value is not None:
                assert account_value != pytest.approx(_STALE_SENTINEL, rel=1e-6), (
                    f"GET /api/state returned portfolio_strip.account_value = {account_value} "
                    f"which matches the stale sentinel {_STALE_SENTINEL}. "
                    "rt-impl: cache must be cleared/refreshed before get_state() reads it."
                )
