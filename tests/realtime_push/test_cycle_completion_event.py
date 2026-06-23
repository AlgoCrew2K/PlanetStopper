"""RED tests — AC-1 + AC-6: cycle-completion event + execution-path safety.

These tests fail until rt-impl:
  - Calls _notify_cycle_complete() in trigger_alpha_bot() after subprocess.run() exits
  - _notify_cycle_complete() is non-blocking and does not raise
  - _notify_cycle_complete() also triggers _refresh_account_totals() (AC-4 linkage)

No live subprocess execution. subprocess.run is patched to return immediately.
"""

from __future__ import annotations

import queue
import threading
from unittest.mock import MagicMock, patch

import pytest

import app as app_module

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_completed_process(returncode: int = 0) -> MagicMock:
    """Return a mock CompletedProcess-like object."""
    m = MagicMock()
    m.returncode = returncode
    return m


# ---------------------------------------------------------------------------
# AC-1: trigger_alpha_bot notifies SSE clients after subprocess exits
# ---------------------------------------------------------------------------


class TestTriggerAlphaBotPublishesEvent:
    def test_notify_cycle_complete_called_after_subprocess_exits_success(self):
        """trigger_alpha_bot() must call _notify_cycle_complete() when the
        engine subprocess exits successfully (returncode 0).

        Fails until rt-impl wires _notify_cycle_complete() into trigger_alpha_bot()
        after the subprocess.run() call.
        """
        with (
            patch("subprocess.run", return_value=_make_completed_process(0)),
            patch("builtins.open", MagicMock()),  # suppress LOG_FILE open
            patch.object(app_module, "_notify_cycle_complete") as mock_notify,
        ):
            app_module.trigger_alpha_bot()

        assert mock_notify.called, (
            "trigger_alpha_bot() must call _notify_cycle_complete() after "
            "subprocess.run() returns. rt-impl: add "
            "`_notify_cycle_complete()` immediately after the subprocess.run() call."
        )

    def test_notify_cycle_complete_called_after_subprocess_exits_failure(self):
        """trigger_alpha_bot() must call _notify_cycle_complete() even when the
        engine subprocess exits with a non-zero returncode (CalledProcessError).

        The cycle is done — clients need to poll to see the error/stale state.
        Fails until rt-impl wires the notify call outside the try/except success path.
        """
        import subprocess

        with (
            patch(
                "subprocess.run",
                side_effect=subprocess.CalledProcessError(1, "alpha_bot_execution.py"),
            ),
            patch("builtins.open", MagicMock()),
            patch.object(app_module, "_notify_cycle_complete") as mock_notify,
        ):
            app_module.trigger_alpha_bot()  # must not raise

        assert mock_notify.called, (
            "trigger_alpha_bot() must call _notify_cycle_complete() even when the "
            "subprocess raises CalledProcessError. "
            "rt-impl: place _notify_cycle_complete() in a finally: block."
        )

    def test_notify_puts_sentinel_on_registered_client_queues(self):
        """_notify_cycle_complete() must put a truthy sentinel on every queue in
        _sse_clients so that SSE generators can yield a cycle-complete event.

        Validates the notification mechanism is actually wired end-to-end.
        Fails until rt-impl implements the fan-out inside _notify_cycle_complete().
        """
        q1: queue.SimpleQueue = queue.SimpleQueue()
        q2: queue.SimpleQueue = queue.SimpleQueue()

        original_clients = list(getattr(app_module, "_sse_clients", []))
        lock = getattr(app_module, "_sse_clients_lock", threading.Lock())

        try:
            with lock:
                app_module._sse_clients.append(q1)
                app_module._sse_clients.append(q2)

            app_module._notify_cycle_complete()

            assert not q1.empty(), (
                "_notify_cycle_complete() must put a sentinel on q1 in _sse_clients; "
                "q1 is still empty. rt-impl: iterate _sse_clients and queue.put() a sentinel."
            )
            assert not q2.empty(), (
                "_notify_cycle_complete() must put a sentinel on q2 in _sse_clients; "
                "q2 is still empty."
            )
        finally:
            # Restore _sse_clients to its pre-test state
            with lock:
                for q in (q1, q2):
                    try:
                        app_module._sse_clients.remove(q)
                    except ValueError:
                        pass


# ---------------------------------------------------------------------------
# AC-6: _notify_cycle_complete does not block the scheduler thread
# ---------------------------------------------------------------------------


class TestNotifyCycleCompleteIsNonBlocking:
    def test_notify_does_not_raise_with_empty_client_list(self):
        """_notify_cycle_complete() must silently succeed with zero clients.

        Called from trigger_alpha_bot on every cycle — must never raise.
        Fails until rt-impl adds null-guard / try-except in _notify_cycle_complete.
        """
        lock = getattr(app_module, "_sse_clients_lock", threading.Lock())
        original_clients = list(getattr(app_module, "_sse_clients", []))

        try:
            with lock:
                app_module._sse_clients.clear()
            # Must not raise
            app_module._notify_cycle_complete()
        except Exception as exc:
            pytest.fail(
                f"_notify_cycle_complete() raised with empty _sse_clients: {exc!r}. "
                "It must be safe to call with zero registered clients."
            )
        finally:
            with lock:
                app_module._sse_clients.extend(original_clients)

    def test_notify_completes_within_short_wall_clock_budget(self):
        """_notify_cycle_complete() must return quickly — it runs on the scheduler
        thread which drives the minute-cadence engine execution.

        Wall-clock budget: 100 ms. Any blocking call inside the notifier
        (network, DB, long loop) violates AC-6.

        Fails if rt-impl accidentally puts blocking I/O inside _notify_cycle_complete.
        """
        import time

        q: queue.SimpleQueue = queue.SimpleQueue()
        lock = getattr(app_module, "_sse_clients_lock", threading.Lock())

        try:
            with lock:
                app_module._sse_clients.append(q)

            start = time.monotonic()
            app_module._notify_cycle_complete()
            elapsed_ms = (time.monotonic() - start) * 1000

            assert elapsed_ms < 100, (  # 100 ms ceiling — ample for a queue.put()
                f"_notify_cycle_complete() took {elapsed_ms:.1f} ms — exceeds 100 ms budget. "
                "It must not block the scheduler thread. "
                "rt-impl: use queue.SimpleQueue.put_nowait() — no I/O, no sleep."
            )
        finally:
            with lock:
                try:
                    app_module._sse_clients.remove(q)
                except ValueError:
                    pass

    def test_notify_does_not_raise_when_client_queue_is_full(self):
        """_notify_cycle_complete() must not raise if a client's queue is full.

        A slow/dead client may have a full bounded queue. The notifier must
        skip or swallow the exception so other clients still receive the event.

        Fails until rt-impl guards the put() with a try/except (Queue.Full).
        Note: SimpleQueue is unbounded — this test uses a bounded queue.Queue to
        explicitly probe the failure mode.
        """
        import queue as q_module

        bounded_q: q_module.Queue = q_module.Queue(maxsize=1)
        bounded_q.put("already-full")  # fill it

        lock = getattr(app_module, "_sse_clients_lock", threading.Lock())

        try:
            with lock:
                app_module._sse_clients.append(bounded_q)

            try:
                app_module._notify_cycle_complete()
            except Exception as exc:
                pytest.fail(
                    f"_notify_cycle_complete() raised when a client queue was full: {exc!r}. "
                    "It must catch queue.Full and continue to the next client."
                )
        finally:
            with lock:
                try:
                    app_module._sse_clients.remove(bounded_q)
                except ValueError:
                    pass
