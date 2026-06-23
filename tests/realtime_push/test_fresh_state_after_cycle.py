"""RED tests — AC-4: /api/state returns freshest cycle data after engine write.

The stale-cache regression: if _account_totals_cache is populated with values from
a prior cycle, and the engine cycle completes (trigger_alpha_bot exits), /api/state
must NOT serve the stale cached values.

The hook: _notify_cycle_complete() / trigger_alpha_bot() must also invalidate or
refresh _account_totals_cache so the very next get_state() call serves fresh data.

Empty-window contract (PM BLOCK — 2026-06-23):
  mark_stale() alone leaves the cache masked until the NEXT per-minute scheduler
  tick (~55 s). The SSE-event fetch the client makes immediately after a cycle
  lands in that blank window → "--" for all account totals, defeating the feature.
  _notify_cycle_complete() must trigger a non-blocking refresh (daemon thread or
  direct call) AFTER mark_stale() and fan-out so the SSE-event fetch sees FRESH
  post-cycle data within seconds, not after the next scheduler tick.

  Acceptable implementations:
    (a) Spawn a daemon thread: threading.Thread(target=_refresh_account_totals).start()
    (b) Call _refresh_account_totals() directly (blocks the finally path — Arch #1 risk)
  Correct implementation: (a) — non-blocking spawn closes the window without blocking.

These tests fail until rt-impl:
  - Calls _refresh_account_totals() (or clears _account_totals_cache) in the
    cycle-completion hook inside trigger_alpha_bot() / _notify_cycle_complete().
  - Triggers a non-blocking cache refresh so /api/state returns FRESH data
    (not masked/empty) after a cycle, not blank for ~55 s.

No live Composer API. All external calls mocked.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import app as app_module

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


# ---------------------------------------------------------------------------
# AC-4 empty-window: cache must re-populate PROMPTLY after mark_stale()
# ---------------------------------------------------------------------------


class TestCacheRestoresFreshDataAfterCycle:
    """_notify_cycle_complete() must not leave _account_totals_cache permanently
    masked after calling mark_stale().

    The empty-window regression: if _notify_cycle_complete() only calls mark_stale()
    and does NOT trigger _refresh_account_totals(), the cache stays masked until the
    next per-minute scheduler tick (~55 s later). Any /api/state call in that window
    returns empty-state for account totals (portfolio_value/cr/tc/mdd), showing '--'
    in the hero section — the exact staleness problem this feature is supposed to fix.

    Fix: _notify_cycle_complete() must spawn a thread (or call directly) to run
    _refresh_account_totals() after the SSE fan-out. With a successful Composer
    response (mocked here), the cache is populated and refresh_written() is called,
    clearing the stale flag so subsequent reads return FRESH data.
    """

    def test_notify_cycle_complete_triggers_account_totals_refresh(self):
        """_notify_cycle_complete() must invoke _refresh_account_totals() (directly
        or via a spawned thread) so the cache re-populates after mark_stale().

        Structural test: patches _refresh_account_totals with a spy and asserts it
        is called exactly once during _notify_cycle_complete() (possibly in a
        background thread — the test waits up to 3 s for the thread to complete).

        Fails if _notify_cycle_complete() only calls mark_stale() without triggering
        any subsequent refresh — the cache would then remain masked for ~55 s.
        """
        import threading

        refresh_called = threading.Event()
        original_refresh = app_module._refresh_account_totals

        def _spy_refresh():
            """Wraps the real refresh — signals the event and suppresses live calls."""
            refresh_called.set()
            # Suppress live Composer call: write known sentinel under the lock and
            # call refresh_written() so mark_stale() is cleared correctly.
            with app_module._account_totals_cache_lock:
                app_module._account_totals_cache["portfolio_value"] = 12345.0
                app_module._account_totals_cache["portfolio_cr"] = 1.23
            app_module._account_totals_cache.refresh_written()

        with patch("app._refresh_account_totals", side_effect=_spy_refresh):
            with patch("requests.get", return_value=MagicMock(status_code=200, json=lambda: {})):
                app_module._notify_cycle_complete()

        # Give the background thread up to 3 s to complete.
        refresh_was_called = refresh_called.wait(timeout=3.0)

        assert refresh_was_called, (
            "_notify_cycle_complete() did NOT call _refresh_account_totals() within 3 s. "
            "Without a refresh trigger, _account_totals_cache stays masked (stale flag set "
            "by mark_stale()) until the next per-minute scheduler tick (~55 s), leaving "
            "the hero account totals blank for the SSE-event fetch. "
            "rt-impl: spawn a daemon thread calling _refresh_account_totals() inside "
            "_notify_cycle_complete(), AFTER the SSE fan-out."
        )

    def test_cache_is_populated_with_fresh_data_after_notify_cycle_complete(self):
        """After _notify_cycle_complete() completes and the refresh thread finishes,
        _account_totals_cache must contain FRESH non-empty data — not be masked.

        Scenario:
        1. Start with an empty cache (fixture clears it).
        2. Call _notify_cycle_complete() with a mocked _refresh_account_totals that
           writes known fresh values (simulating a successful Composer response) and
           calls refresh_written() to clear the stale flag.
        3. Wait up to 3 s for the refresh thread.
        4. Assert _account_totals_cache['portfolio_value'] is the fresh value
           (not None / not masked).

        This is the behavioral complement to the structural test above — it proves
        the cache is READABLE with fresh data after the cycle, not just that refresh
        was triggered.

        Fails if _notify_cycle_complete() leaves the cache in masked/empty state:
        - mark_stale() only, no refresh → portfolio_value reads None.
        - refresh thread spawned but refresh_written() never called → still masked.
        """
        import threading

        _FRESH_VALUE = 99999.0
        refresh_done = threading.Event()

        def _mock_refresh():
            """Writes fresh sentinel values and calls refresh_written()."""
            with app_module._account_totals_cache_lock:
                app_module._account_totals_cache["portfolio_value"] = _FRESH_VALUE
                app_module._account_totals_cache["portfolio_cr"] = 5.55
            app_module._account_totals_cache.refresh_written()
            refresh_done.set()

        with patch("app._refresh_account_totals", side_effect=_mock_refresh):
            app_module._notify_cycle_complete()

        refresh_done.wait(timeout=3.0)

        portfolio_value = app_module._account_totals_cache.get("portfolio_value")
        assert portfolio_value is not None, (
            "After _notify_cycle_complete() + mocked refresh, "
            "_account_totals_cache['portfolio_value'] is None (cache still masked). "
            "Either _notify_cycle_complete() did not call _refresh_account_totals(), "
            "or refresh_written() was not called — so mark_stale() was never cleared. "
            "rt-impl: ensure _notify_cycle_complete() spawns _refresh_account_totals() "
            "and that _refresh_account_totals() calls refresh_written() after writing."
        )
        assert portfolio_value == pytest.approx(_FRESH_VALUE, rel=1e-6), (
            f"After cycle completion, _account_totals_cache['portfolio_value'] = {portfolio_value} "
            f"but expected the fresh value {_FRESH_VALUE} written by the mocked refresh. "
            "The cache must hold the most-recent Composer data after each cycle."
        )

    def test_get_state_returns_fresh_account_totals_after_cycle_with_successful_composer(
        self, client
    ):
        """GET /api/state must return non-empty portfolio_strip.account_value after
        a simulated cycle completion where Composer responds successfully.

        Scenario:
        1. Seed minimal bot_state (one symphony) so get_state() builds a portfolio_strip.
        2. Call _notify_cycle_complete() with a mocked _refresh_account_totals that
           writes a known fresh portfolio_value and calls refresh_written().
        3. Wait for the refresh thread (up to 3 s).
        4. GET /api/state.
        5. Assert portfolio_strip.account_value == _FRESH_ACCT_VALUE (not None, not stale).

        The empty-window regression: if account_value is None here, the hero section
        shows '--' immediately after the SSE event — the user sees blank numbers despite
        a successful engine run.

        Fails if the refresh isn't triggered or refresh_written() isn't called.
        """
        import threading

        import database as db

        _FRESH_ACCT_VALUE = 77777.0
        refresh_done = threading.Event()

        # Seed minimal bot_state so _compute_portfolio_strip has data to work with.
        db.save_state(
            {
                "sym-ew-test": {
                    "name": "Empty-Window Test Symphony",
                    "current_value": 5000.0,
                    "current_return": 2.0,
                    "simple_return": 0.02,
                    "net_deposits": 4900.0,
                    "time_weighted_return": 0.025,
                    "max_drawdown": 0.01,
                }
            }
        )

        def _mock_refresh():
            """Simulate a successful Composer response with a known portfolio_value."""
            with app_module._account_totals_cache_lock:
                app_module._account_totals_cache["portfolio_value"] = _FRESH_ACCT_VALUE
                app_module._account_totals_cache["portfolio_cr"] = 3.14
            app_module._account_totals_cache.refresh_written()
            refresh_done.set()

        with patch("app._refresh_account_totals", side_effect=_mock_refresh):
            app_module._notify_cycle_complete()

        refresh_done.wait(timeout=3.0)

        response = client.get("/api/state")
        assert response.status_code == 200, (
            f"GET /api/state returned {response.status_code}; expected 200."
        )

        data = response.get_json()
        port_strip = data.get("portfolio_strip") if isinstance(data, dict) else None
        assert isinstance(port_strip, dict), (
            f"portfolio_strip missing from /api/state response: {type(data)!r}. "
            "Expected a portfolio_strip block since bot_state was seeded with one symphony."
        )

        account_value = port_strip.get("account_value")
        assert account_value is not None, (
            "GET /api/state portfolio_strip.account_value is None after a successful "
            "simulated cycle + Composer refresh. The hero section would show '--'. "
            "rt-impl: _notify_cycle_complete() must trigger _refresh_account_totals() "
            "so /api/state returns FRESH data on the SSE-event fetch."
        )
        assert account_value == pytest.approx(_FRESH_ACCT_VALUE, rel=1e-6), (
            f"portfolio_strip.account_value = {account_value}, "
            f"expected {_FRESH_ACCT_VALUE} (the fresh mocked Composer value). "
            "Check that refresh_written() is called after writing to _account_totals_cache."
        )
