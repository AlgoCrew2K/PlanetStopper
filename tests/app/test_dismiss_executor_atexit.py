"""
Regression test — CC-003: _DISMISS_EXECUTOR atexit registration.

sprint1-auditor CC-003 (HIGH) found that _DISMISS_EXECUTOR was declared but
never registered with atexit, meaning in-flight dismiss writes could be
abandoned on daemon exit.

Fix: atexit.register(_DISMISS_EXECUTOR.shutdown, wait=True) added immediately
after the executor declaration in app.py.

Strategy: each test reloads app in a sandboxed import context (sys.modules
popped before; restored after) so the module-level atexit.register call is
intercepted by a spy without affecting the already-loaded app module that the
rest of the test suite uses.
"""

from __future__ import annotations

import atexit
import importlib
import sys
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REAL_ATEXIT_REGISTER = atexit.register  # captured at collection time


def _reload_app_with_spy():
    """Reload app in a fresh sys.modules slot, spy on atexit.register.

    Returns (app_module, registered_calls) where registered_calls is the list
    of (fn, args, kwargs) tuples captured during the reload.

    The previously loaded app module is restored to sys.modules after the
    reload so subsequent tests see the original module state.
    """
    original_app = sys.modules.get("app")
    registered_calls: list[tuple] = []

    def _spy(fn, *args, **kwargs):
        registered_calls.append((fn, args, kwargs))
        # Call the real atexit.register (captured before the patch) to avoid recursion.
        _REAL_ATEXIT_REGISTER(fn, *args, **kwargs)

    sys.modules.pop("app", None)
    try:
        with patch("atexit.register", side_effect=_spy), \
             patch("database.init_db"):
            fresh_app = importlib.import_module("app")
    finally:
        # Restore the original module so other tests are not affected.
        if original_app is not None:
            sys.modules["app"] = original_app
        else:
            sys.modules.pop("app", None)

    return fresh_app, registered_calls


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDismissExecutorAtexitRegistration:
    """
    CC-003: _DISMISS_EXECUTOR must be registered with atexit.shutdown(wait=True).

    Verifies both the callable and the wait=True kwarg so a future refactor
    that drops wait=True is caught — silent abandonment of in-flight writes is
    the exact failure mode CC-003 identified.
    """

    def test_dismiss_executor_shutdown_registered_with_atexit(self):
        """atexit.register must be called with _DISMISS_EXECUTOR.shutdown during app load.

        Reloads app in a sandboxed import context with atexit.register spied.
        Asserts the expected call is present among all atexit registrations.
        """
        fresh_app, registered_calls = _reload_app_with_spy()

        executor_shutdown_calls = [
            (fn, args, kwargs)
            for fn, args, kwargs in registered_calls
            if fn == fresh_app._DISMISS_EXECUTOR.shutdown
        ]

        assert executor_shutdown_calls, (
            "atexit.register was never called with _DISMISS_EXECUTOR.shutdown during app load. "
            "CC-003 fix: add atexit.register(_DISMISS_EXECUTOR.shutdown, wait=True) "
            "immediately after the _DISMISS_EXECUTOR declaration in app.py."
        )

    def test_dismiss_executor_shutdown_registered_with_wait_true(self):
        """atexit handler must pass wait=True as a keyword argument to shutdown.

        shutdown(wait=True) causes the daemon to block until all queued dismiss
        writes complete — the exact safety property CC-003 required.
        """
        fresh_app, registered_calls = _reload_app_with_spy()

        executor_shutdown_calls = [
            (fn, args, kwargs)
            for fn, args, kwargs in registered_calls
            if fn == fresh_app._DISMISS_EXECUTOR.shutdown
        ]

        assert executor_shutdown_calls, (
            "_DISMISS_EXECUTOR.shutdown not found in atexit registrations — "
            "atexit.register call is missing. See CC-003."
        )

        _, args, kwargs = executor_shutdown_calls[0]
        assert kwargs.get("wait") is True, (
            f"_DISMISS_EXECUTOR.shutdown is registered with atexit but wait=True "
            f"was not passed as a keyword argument (kwargs={kwargs!r}, args={args!r}). "
            "CC-003: shutdown(wait=True) ensures in-flight dismiss writes complete "
            "before the daemon exits."
        )

    def test_dismiss_executor_shutdown_wait_not_positional(self):
        """wait=True must be a keyword argument, not a positional arg to atexit.register.

        atexit.register(fn, *args, **kwargs) forwards *args positionally to fn at
        exit.  Passing True positionally would also work at runtime but is less
        explicit; the fix uses the keyword form for clarity.
        """
        fresh_app, registered_calls = _reload_app_with_spy()

        executor_shutdown_calls = [
            (fn, args, kwargs)
            for fn, args, kwargs in registered_calls
            if fn == fresh_app._DISMISS_EXECUTOR.shutdown
        ]

        assert executor_shutdown_calls, (
            "_DISMISS_EXECUTOR.shutdown not found in atexit registrations — "
            "atexit.register call is missing. See CC-003."
        )

        _, positional_args, _ = executor_shutdown_calls[0]
        # atexit.register(fn, True) would make positional_args == (True,).
        assert True not in positional_args, (
            f"True was passed positionally to atexit.register for _DISMISS_EXECUTOR.shutdown "
            f"(positional args forwarded to shutdown at exit: {positional_args!r}). "
            "Use atexit.register(_DISMISS_EXECUTOR.shutdown, wait=True) with keyword form."
        )
