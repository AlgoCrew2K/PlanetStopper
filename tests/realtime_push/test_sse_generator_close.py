"""RED tests — reviewer BLOCK: GeneratorExit + cache race (rt-review findings).

Two defects found by rt-review in the AC-1..AC-6 GREEN at f4e93b9:

BLOCK-1: `except GeneratorExit: pass` in the sse_events generator (app.py:1311)
  Python 3.7+ raises RuntimeError("generator ignored GeneratorExit") when a generator
  catches GeneratorExit and doesn't re-raise (PEP 342). The finally: cleanup is safe,
  but the RuntimeError propagates to the WSGI server — logged as an error on every
  client disconnect.
  Fix: remove `except GeneratorExit: pass` entirely. The finally: block runs regardless.

BLOCK-2: Cache race — _notify_cycle_complete() calls _account_totals_cache.clear()
  without the _account_totals_cache_lock (or any lock). _refresh_account_totals()
  runs on the same `:00` scheduler tick and writes multiple keys into the same dict
  concurrently. A .clear() mid-write sequence produces a partially-populated or empty
  cache, causing the next /api/state call to show `--` for portfolio value.
  Fix: either (a) protect .clear() with the same lock used by _refresh_account_totals,
  (b) delete specific keys under a lock, or (c) replace .clear() with a "dirty" flag
  that causes _refresh_account_totals to re-populate on its next run.

Both tests fail until rt-impl applies the fixes.
"""

from __future__ import annotations

import threading
import types

import pytest

import app as app_module

# ---------------------------------------------------------------------------
# BLOCK-1: GeneratorExit must not be swallowed by the SSE generator
# ---------------------------------------------------------------------------


class TestSseGeneratorCloseIsClean:
    def test_sse_generate_function_does_not_catch_generatorexit(self):
        """The SSE generate() inner function must NOT contain `except GeneratorExit`.

        `except GeneratorExit: pass` is present in the current implementation at
        app.py:1311. While this does not raise RuntimeError in the specific case where
        no yield follows the catch, it IS a code smell that:
          1. Masks future bugs if a yield is accidentally added after the except
          2. Signals intent to suppress close signals — anti-pattern per PEP 342
          3. Is simply unnecessary: `finally:` runs on GeneratorExit without any `except`

        The correct pattern is `try: while True: yield ... finally: cleanup`.
        GeneratorExit propagates through `finally` naturally; no `except` needed.

        This test inspects the source of the generate() closure inside sse_events()
        and asserts `except GeneratorExit` is absent.

        Fails until rt-impl removes `except GeneratorExit: pass` from generate().
        """
        import inspect

        # Get source of the sse_events function — the generate() closure is nested inside.
        try:
            sse_source = inspect.getsource(app_module.sse_events)
        except (TypeError, OSError) as exc:
            pytest.fail(f"Could not inspect sse_events source: {exc!r}")

        assert "except GeneratorExit" not in sse_source, (
            "sse_events generate() must NOT contain `except GeneratorExit`. "
            "Found at app.py:1311 in the current implementation. "
            "rt-impl: remove the `except GeneratorExit: pass` block entirely. "
            "Replace with `try: while True: yield ... finally: cleanup` — "
            "GeneratorExit propagates through `finally` without any explicit `except`."
        )

    def test_sse_generator_deregisters_client_on_close(self, client):
        """When the SSE generator is closed, the client queue must be removed from
        _sse_clients — regardless of whether GeneratorExit was swallowed.

        The `finally` block must run. Verifies cleanup is correct after the fix.
        Fails if the generator's finally: block does not remove the client queue.
        """
        import queue as q_module

        original_clients = list(app_module._sse_clients)
        lock = getattr(app_module, "_sse_clients_lock", threading.Lock())

        try:
            with app_module.app.test_request_context("/api/events"):
                response_obj = app_module.sse_events()
                gen = response_obj.response
                if hasattr(gen, "__iter__"):
                    gen = iter(gen)

                # Capture how many clients are registered after the generator starts.
                len_before_close = len(app_module._sse_clients)

                # Close the generator — the finally block should remove the client.
                try:
                    gen.close()
                except (RuntimeError, Exception):
                    pass  # we're testing cleanup, not the GeneratorExit bug

                len_after_close = len(app_module._sse_clients)

            assert len_after_close < len_before_close or len_after_close == len(original_clients), (
                f"After generator.close(), _sse_clients still has the same count "
                f"({len_after_close} vs {len_before_close} before close). "
                "The finally: block must deregister the client queue on close. "
                "rt-impl: ensure the finally: block removes client_q from _sse_clients."
            )
        finally:
            # Restore any test clients that leaked
            with lock:
                app_module._sse_clients.clear()
                app_module._sse_clients.extend(original_clients)


# ---------------------------------------------------------------------------
# BLOCK-2: Cache race — _notify_cycle_complete must not race with _refresh_account_totals
# ---------------------------------------------------------------------------


class TestNotifyCycleCompleteDoesNotRaceWithCacheRefresh:
    def test_notify_cycle_complete_does_not_clear_cache_without_protection(self):
        """_notify_cycle_complete() must prevent partial-state (one key present, the
        other missing) from ever reaching /api/state after a concurrent refresh write.

        Production race scenario:
          _notify_cycle_complete() (trigger_alpha_bot finally block) and
          _refresh_account_totals() (same :00 scheduler tick) both touch
          _account_totals_cache concurrently. Without protection a bare .clear() can
          fire between the two writes of a multi-key batch, leaving `portfolio_cr`
          set but `portfolio_value` missing — /api/state then shows `--` for
          portfolio value.

        Correct implementations (any of):
          (a) _StaleFlagDict.mark_stale() — masks ALL reads atomically via a boolean
              flag without touching the dict; partial state is never exposed.
          (b) Lock-protected .clear() — holds _account_totals_cache_lock for both
              the clear AND the full write batch, preventing interleaving.

        Invariant (both implementations):
          After both threads complete, for any pair of reads:
            - Both keys return a value (fully populated), OR
            - Both keys return None (empty / stale).
          Partial state — one key Some, one None — is never observable.

        The simulated refresh acquires _account_totals_cache_lock around its two-key
        write (matching the production _refresh_account_totals codepath) and includes
        a sleep inside the lock to extend the contention window.

        Fails if _notify_cycle_complete neither calls mark_stale() nor holds the
        cache lock during .clear(), allowing the clear to interleave with the write.
        """
        import time
        from unittest.mock import MagicMock, patch

        _EXPECTED_VALUE = 55555.0
        _EXPECTED_CR = 12.34
        races_detected: list = []

        def _slow_refresh():
            """Simulates _refresh_account_totals writing multiple keys under the shared
            cache lock — matches the production path (_account_totals_cache_lock).

            The sleep inside the lock extends the contention window: an unprotected
            .clear() in _notify can fire between the two STORE instructions, leaving
            one key present. A mark_stale() call (no lock) sets _stale=True which
            masks both reads to None — so reads are always consistent (both None or
            both set) regardless of interleave order. Either protection passes.
            """
            with app_module._account_totals_cache_lock:
                app_module._account_totals_cache["portfolio_value"] = _EXPECTED_VALUE
                # Yield inside the lock: forces _notify to either (a) wait for the
                # lock before clearing or (b) set the stale flag (masking reads)
                # while both stores are still in-flight.
                time.sleep(0.005)
                app_module._account_totals_cache["portfolio_cr"] = _EXPECTED_CR

        def _run_notify():
            # Suppress _refresh_account_totals live API call inside _notify
            with patch("requests.get", return_value=MagicMock(status_code=200, json=lambda: {})):
                app_module._notify_cycle_complete()

        # Run both concurrently several times to expose the race
        N_TRIALS = 20
        for _ in range(N_TRIALS):
            app_module._account_totals_cache.clear()

            t_refresh = threading.Thread(target=_slow_refresh)
            t_notify = threading.Thread(target=_run_notify)

            t_refresh.start()
            t_notify.start()
            t_refresh.join()
            t_notify.join()

            # After both complete, the cache should have the refreshed values
            # OR be empty (the dirty-flag approach — acceptable if _refresh re-populates).
            # It must NOT have only one of the two keys (partial state from a mid-clear race).
            val = app_module._account_totals_cache.get("portfolio_value")
            cr = app_module._account_totals_cache.get("portfolio_cr")
            if (val is None) != (cr is None):
                # Partial state: one key present, the other missing — the race fired
                races_detected.append({"portfolio_value": val, "portfolio_cr": cr})

        app_module._account_totals_cache.clear()

        assert not races_detected, (
            f"Partial-state race detected in {len(races_detected)}/{N_TRIALS} trials: "
            f"concurrent _notify_cycle_complete() + refresh left exactly one key set. "
            f"Sample: {races_detected[:3]}. "
            "Fix: _notify_cycle_complete() must either (a) call mark_stale() to mask "
            "all reads atomically, OR (b) hold _account_totals_cache_lock around the "
            "full .clear() so it cannot interleave with the write batch."
        )

    def test_account_totals_cache_lock_exists_for_thread_safety(self):
        """app module must expose _account_totals_cache_lock (or a shared lock) to
        serialize access to _account_totals_cache between _notify_cycle_complete and
        _refresh_account_totals.

        This is the structural precondition for the cache race fix.
        Fails until rt-impl adds a lock for _account_totals_cache access.

        Acceptable names: _account_totals_cache_lock, _cache_lock,
        or reuse of _sse_clients_lock if documented as shared.
        """
        acceptable_lock_names = [
            "_account_totals_cache_lock",
            "_cache_lock",
            "_account_cache_lock",
        ]
        found = any(hasattr(app_module, name) for name in acceptable_lock_names)

        # Also accept reuse of _sse_clients_lock IF _notify explicitly uses it
        # for cache operations (structural check on intent — source inspection).
        if not found:
            import inspect

            notify_src = inspect.getsource(app_module._notify_cycle_complete)
            reuses_sse_lock = "_sse_clients_lock" in notify_src and (
                "_account_totals_cache" in notify_src
            )
            # If the same lock guards both the SSE fanout and the cache clear, that is
            # acceptable (single lock for all shared state in _notify).
            found = reuses_sse_lock

        assert found, (
            "app module must expose a lock for _account_totals_cache thread safety. "
            "Expected one of: "
            + str(acceptable_lock_names)
            + ", or reuse of _sse_clients_lock around _account_totals_cache.clear(). "
            "rt-impl: add `_account_totals_cache_lock = threading.Lock()` and use it in "
            "both _notify_cycle_complete() (around the cache clear/invalidate) and "
            "_refresh_account_totals() (around the cache writes)."
        )
