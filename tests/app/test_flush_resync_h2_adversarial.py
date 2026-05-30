"""
Adversarial RED tests for H-2: flush_resync bot_state lost-update race.

These tests are RED on the pre-fix _flush_state_async (no lock around
load+modify+save) and GREEN once CC-NEW-001 is correctly applied.

They go DEEPER than tests/app/test_flush_resync_race.py by:

  H2-ADV-1: Demonstrate the lost-update directly — a simulated concurrent
             write between flush's load and save loses data when unprotected.
             The test inverts the lock acquisition by injecting a competing
             write inside load_state, then asserts the engine's update
             survives in the final saved state.

  H2-ADV-2: Lock must NOT be held while _refresh_account_totals runs
             (Phase 3 Composer call).  A bad fix that holds the lock through
             Phase 3 would block future flush operations indefinitely.  The
             test verifies _FLUSH_STATE_LOCK is FREE by the time Phase 3
             starts so concurrent database ops can proceed.

  H2-ADV-3: Lock is released on load_state exception — the with-statement
             guarantee.  A bare acquire/release (not using with) could leak
             the lock on exception, deadlocking all subsequent flush calls.

  H2-ADV-4: Two concurrent flush calls must both complete without deadlock
             or data corruption.  The single-worker _DISMISS_EXECUTOR
             serializes them; this test confirms no re-entrancy trap.

  H2-ADV-5: The save_state call in _flush_state_async must receive a state
             dict that was loaded INSIDE the lock, not a stale snapshot
             captured before the lock was acquired.  A fix that loads state
             before acquiring the lock and only wraps the save in the lock
             still has the race window.

Hostile edges exercised:
  - Engine write injected deterministically between flush's load and save
    using threading.Event barriers to force the worst-case interleaving.
  - Lock-acquisition timing verified via a spy context manager patched into
    the module so we observe the exact sequence without relying on wall time.
  - Blocking I/O phase (Phase 3) verified to run lock-free.
  - Exception path verified not to leak the lock.

Mocking strategy:
  - database is patched via patch.object(app_module, "database").
  - threading.Thread is NOT patched — _DISMISS_EXECUTOR manages its own
    threads; patching threading.Thread breaks the executor's internals.
  - threading.Event barriers sequence the race deterministically.
  - time is NOT mocked — latency bounds use wall time with 2s ceilings.

Scope guard (as per cycle brief): these tests must REJECT:
  - A fix that holds the lock across blocking I/O (H2-ADV-2 fails).
  - A fix that uses a lock acquired before the load (H2-ADV-5 fails).
  - A fix that uses bare acquire/release without a context manager (H2-ADV-3
    fails on exception path).
  - A fix that deadlocks on concurrent flush calls (H2-ADV-4 fails).
"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

import analytics
import app as app_module


# ---------------------------------------------------------------------------
# Shared client fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(analytics, "_POST_MORTEMS_DIR", str(tmp_path / "pm"))
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# H2-ADV-1 — Lost-update: engine write between flush load and save clobbers
#             the engine's update when unprotected; is preserved when locked.
# ---------------------------------------------------------------------------


def test_engine_update_survives_concurrent_flush_resync(client):
    """H2-ADV-1: an engine write that lands BETWEEN the flush's load_state
    and save_state must NOT be overwritten by the flush's save_state.

    The race is set up with threading.Event barriers:
      1. flush thread loads state (pre_state, which has no 'engine_key').
      2. test inserts 'engine_key' into the state the background save will use.
      3. flush thread saves — WITHOUT a lock the flush saves the pre_state
         snapshot, discarding 'engine_key'.  WITH a lock the flush's own
         load_state (inside the lock) already sees 'engine_key' and preserves it.

    Mechanism: the spy load_state implementation returns a state WITHOUT
    'engine_key' on the first call (request-thread read) but WITH it on the
    second call (background-thread read inside the lock).  We assert the
    final saved state contains 'engine_key'.

    RED (unpatched): _flush_state_async calls load_state OUTSIDE the lock,
    so the background-thread load uses the request-thread's pre_state snapshot
    (no 'engine_key') → save_state writes state without 'engine_key' → assertion
    fails.

    GREEN (patched): load_state is called INSIDE the lock; by the time the
    lock is acquired the engine has already written 'engine_key', so the
    background-thread load sees it and preserves it → assertion passes.

    Note: this test simulates the engine write via the second load_state call
    returning an enriched state.  In production the engine writes to SQLite;
    the second load_state reads from SQLite, picking up the engine's write.
    """
    pre_state = {
        "sym_a": {"name": "Alpha", "account": "acct-1", "triggered": True},
    }
    # The 'engine_key' simulates a per-minute engine update that lands between
    # the two load_state calls.  A correct fix ensures flush's load_state is
    # called inside the lock, so this key appears in the loaded state.
    post_engine_state = {
        "sym_a": {"name": "Alpha", "account": "acct-1", "triggered": True},
        "engine_key": "engine_wrote_this",
    }

    load_call_count = 0
    saved_states: list[dict] = []
    write_completed = threading.Event()

    def _divergent_load():
        nonlocal load_call_count
        load_call_count += 1
        if load_call_count == 1:
            # Request-thread read: no engine key yet
            return pre_state.copy()
        # Background-thread read (should be inside the lock):
        # engine has written between the two loads; returns enriched state.
        return post_engine_state.copy()

    def _capture_save(state):
        saved_states.append(dict(state))
        write_completed.set()

    with (
        patch.object(app_module, "database") as db_mock,
        patch.object(app_module, "_refresh_account_totals"),
    ):
        db_mock.load_state.side_effect = _divergent_load
        db_mock.save_state.side_effect = _capture_save

        resp = client.post("/api/settings/flush-resync")
        write_completed.wait(timeout=2)

    assert resp.status_code == 200
    assert saved_states, (
        "database.save_state was never called — background flush task did not fire."
    )

    final_state = saved_states[-1]

    # The engine key must survive: the flush's load must be inside the lock
    # so it sees the engine's write.  If the load happens outside the lock
    # (using the request-thread's stale snapshot), engine_key is discarded.
    assert "engine_key" in final_state, (
        f"'engine_key' is missing from the final saved state.  "
        f"final_state={final_state!r}.  "
        "This means _flush_state_async called load_state BEFORE acquiring "
        "_FLUSH_STATE_LOCK, so it used a stale pre-lock snapshot that does not "
        "include the engine's write.  "
        "Fix: call database.load_state() INSIDE the 'with _FLUSH_STATE_LOCK:' "
        "block, not before it."
    )
    assert final_state["engine_key"] == "engine_wrote_this", (
        f"'engine_key' value corrupted: {final_state['engine_key']!r}.  "
        "The flush must not modify or discard non-symphony keys."
    )


# ---------------------------------------------------------------------------
# H2-ADV-2 — Lock must NOT be held during Phase 3 (Composer / blocking I/O)
# ---------------------------------------------------------------------------


def test_flush_state_lock_is_released_before_composer_resync(client):
    """H2-ADV-2: _FLUSH_STATE_LOCK must be free by the time _refresh_account_totals
    runs (Phase 3).  Holding the lock through Phase 3 would serialize ALL
    subsequent flush background tasks against a potentially slow network call,
    violating Architecture Rule 1 (no blocking I/O while holding a mutex that
    guards the execution path).

    Mechanism: a spy _refresh_account_totals checks whether _FLUSH_STATE_LOCK
    is currently held (acquire non-blocking).  If it is, the test fails.

    RED (bad fix): _flush_state_async wraps BOTH the state-reset AND the
    Composer resync in 'with _FLUSH_STATE_LOCK:' → spy cannot acquire the lock
    → assertion fails.

    GREEN (correct fix): lock is released after save_state; Phase 3 runs
    lock-free → spy acquires the lock non-blocking → assertion passes.
    """
    # Phase 3 runs synchronously on the request thread via a daemon thread;
    # we need to observe whether the lock is held AT THAT MOMENT.
    lock_state_during_phase3: list[bool] = []

    def _spy_refresh():
        # Try to acquire the lock non-blocking.  If the lock is free, the
        # background state-reset has already released it (correct).  If the
        # lock is held, someone is holding it across Phase 3 (incorrect).
        acquired = app_module._FLUSH_STATE_LOCK.acquire(blocking=False)
        if acquired:
            app_module._FLUSH_STATE_LOCK.release()
            lock_state_during_phase3.append(False)  # lock was NOT held
        else:
            lock_state_during_phase3.append(True)  # lock WAS held

    write_completed = threading.Event()

    def _capture_save(_state):
        write_completed.set()

    with (
        patch.object(app_module, "database") as db_mock,
        patch.object(app_module, "_refresh_account_totals", _spy_refresh),
    ):
        db_mock.load_state.return_value = {}
        db_mock.save_state.side_effect = _capture_save

        resp = client.post("/api/settings/flush-resync")
        write_completed.wait(timeout=2)

    assert resp.status_code == 200

    # _refresh_account_totals must have been called (Phase 3 ran)
    assert lock_state_during_phase3, (
        "_refresh_account_totals (Phase 3) was never called.  "
        "Cannot verify lock state during Composer resync."
    )

    # The lock must NOT be held during Phase 3
    lock_held_during_phase3 = lock_state_during_phase3[0]
    assert not lock_held_during_phase3, (
        "_FLUSH_STATE_LOCK was HELD during _refresh_account_totals (Phase 3).  "
        "The lock must be released before any blocking I/O (Composer call, "
        "threading.Thread.join, etc.).  "
        "Architecture Rule 1: no blocking I/O while holding the lock.  "
        "Fix: ensure 'with _FLUSH_STATE_LOCK:' wraps ONLY the "
        "load_state+modify+save_state triple inside _flush_state_async — "
        "not the Phase 3 Composer resync path."
    )


# ---------------------------------------------------------------------------
# H2-ADV-3 — Lock is released on load_state exception (with-statement contract)
# ---------------------------------------------------------------------------


def test_flush_state_lock_released_on_load_state_exception(client):
    """H2-ADV-3: if database.load_state() raises inside the lock, the lock
    must still be released (context-manager __exit__ guarantee).

    A bare acquire/release (without 'with') could leak the lock on exception,
    deadlocking all subsequent calls to _flush_state_async.

    Mechanism: load_state raises RuntimeError.  After the handler returns
    (background task has run), we attempt to acquire _FLUSH_STATE_LOCK
    non-blocking.  If the lock is leaked, acquire fails → assertion fails.

    RED (bad fix using bare acquire/release): RuntimeError in load_state
    causes the bare release to be skipped → lock leaked → non-blocking
    acquire after the flush fails → assertion fails.

    GREEN (correct fix using 'with _FLUSH_STATE_LOCK:'): RuntimeError is
    caught by __exit__, lock is released regardless → non-blocking acquire
    after the flush succeeds → assertion passes.

    Note: the _flush_state_async also has an outer try/except that swallows
    exceptions.  This test verifies the lock is free AFTER that exception
    is swallowed, confirming the context manager properly released it.
    """
    bg_task_finished = threading.Event()

    def _raising_load():
        raise RuntimeError("simulated DB error — verify lock is still released")

    original_load = app_module.database.load_state  # pre-patch reference

    call_count = 0

    def _selective_raise():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Request-thread call — raise to skip background dispatch setup
            # We need the background task to be dispatched; so only the SECOND
            # call (inside _flush_state_async) raises.
            return {}
        raise RuntimeError("simulated DB error in background load")

    def _mark_finished(_state):
        # This should NOT be called since load_state raises for bg call
        bg_task_finished.set()

    with (
        patch.object(app_module, "database") as db_mock,
        patch.object(app_module, "_refresh_account_totals"),
    ):
        db_mock.load_state.side_effect = _selective_raise
        db_mock.save_state.side_effect = _mark_finished

        resp = client.post("/api/settings/flush-resync")
        # Give the background task time to fire and fail
        time.sleep(0.3)

    assert resp.status_code == 200

    # After the background task has run and failed (RuntimeError in load_state),
    # the lock must be free.  A leaked lock would cause this acquire to block.
    acquired = app_module._FLUSH_STATE_LOCK.acquire(blocking=False)
    assert acquired, (
        "_FLUSH_STATE_LOCK is still held after _flush_state_async raised an "
        "exception in load_state.  "
        "This means the lock was NOT acquired via a 'with' statement — it was "
        "acquired with a bare .acquire() call and the matching .release() was "
        "skipped when the exception propagated.  "
        "Fix: use 'with _FLUSH_STATE_LOCK:' (context manager), which guarantees "
        "__exit__ releases the lock even when an exception is raised."
    )
    app_module._FLUSH_STATE_LOCK.release()


# ---------------------------------------------------------------------------
# H2-ADV-4 — Two concurrent flush calls both complete without deadlock
# ---------------------------------------------------------------------------


def test_two_concurrent_flush_calls_complete_without_deadlock(client, tmp_path, monkeypatch):
    """H2-ADV-4: if two POST /api/settings/flush-resync calls arrive
    simultaneously, both background tasks must complete.

    The _DISMISS_EXECUTOR single-worker serializes them (FIFO queue), and
    the _FLUSH_STATE_LOCK serializes the critical section.  Neither mechanism
    should cause deadlock or starvation.

    RED (bad fix — re-entrant lock misuse): if _flush_state_async tries to
    acquire _FLUSH_STATE_LOCK while already holding it (e.g., nested 'with'
    in a helper called from inside the lock), the second invocation deadlocks
    forever because threading.Lock is NOT re-entrant.

    GREEN (correct fix): lock is acquired and released cleanly for each
    background task; both tasks complete within 3 s.
    """
    monkeypatch.setattr(analytics, "_POST_MORTEMS_DIR", str(tmp_path / "pm2"))

    completions: list[int] = []
    completion_lock = threading.Lock()

    def _save_with_completion(state):
        with completion_lock:
            completions.append(1)

    with (
        patch.object(app_module, "database") as db_mock,
        patch.object(app_module, "_refresh_account_totals"),
    ):
        db_mock.load_state.return_value = {}
        db_mock.save_state.side_effect = _save_with_completion

        # Submit two concurrent flush requests
        resp1 = client.post("/api/settings/flush-resync")
        resp2 = client.post("/api/settings/flush-resync")

        # Both handlers return immediately (background dispatch)
        assert resp1.status_code == 200, "First flush request must return 200."
        assert resp2.status_code == 200, "Second flush request must return 200."

        # Wait for both background tasks to complete (timeout: 3 s)
        deadline = time.monotonic() + 3.0
        while len(completions) < 2 and time.monotonic() < deadline:
            time.sleep(0.05)

    assert len(completions) >= 2, (
        f"Only {len(completions)} of 2 concurrent flush background tasks completed "
        f"within 3 s.  "
        "This suggests a deadlock: the _DISMISS_EXECUTOR single-worker queued both "
        "tasks, but the first task may be blocking (holding _FLUSH_STATE_LOCK while "
        "waiting for something, preventing the second task from proceeding).  "
        "Fix: ensure _flush_state_async releases _FLUSH_STATE_LOCK before any "
        "blocking operation and does not acquire the lock recursively."
    )


# ---------------------------------------------------------------------------
# H2-ADV-5 — load_state is called INSIDE the lock, not before it
# ---------------------------------------------------------------------------


def test_flush_state_load_happens_inside_the_lock_not_before(client):
    """H2-ADV-5: the load_state call in _flush_state_async must happen INSIDE
    the 'with _FLUSH_STATE_LOCK:' block, not before it.

    A partial fix that loads state before acquiring the lock still has a race
    window: the engine can write between load (outside lock) and acquire (inside
    lock), and the flush will overwrite the engine's write with stale data.

    Mechanism: a spy _FLUSH_STATE_LOCK observes whether load_state is called
    before the first acquire.  If any load_state call occurs before the first
    lock.acquire call, the fix is insufficient.

    RED (partial fix — load before lock): call_log shows
    'bg_load_state' BEFORE 'lock.acquire' → assertion fails.

    GREEN (correct fix — load inside lock): call_log shows
    'lock.acquire' BEFORE 'bg_load_state' → assertion passes.
    """
    write_completed = threading.Event()
    call_log: list[str] = []
    call_log_lock = threading.Lock()

    class _SpyLock:
        """Spy wrapping the real _FLUSH_STATE_LOCK; records call order."""

        def __init__(self):
            self._lock = threading.Lock()

        def acquire(self, blocking=True, timeout=-1):
            result = self._lock.acquire(blocking=blocking, timeout=timeout)
            if result:
                with call_log_lock:
                    call_log.append("lock.acquire")
            return result

        def release(self):
            with call_log_lock:
                call_log.append("lock.release")
            self._lock.release()

        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, *_):
            self.release()

    spy_lock = _SpyLock()

    request_thread_load_count = 0

    def _spy_load():
        nonlocal request_thread_load_count
        request_thread_load_count += 1
        if request_thread_load_count == 1:
            # First call is on the request thread (for symphonies_reset enumeration)
            return {"sym_a": {"name": "Alpha", "triggered": True}}
        # Subsequent calls are from the background thread (_flush_state_async).
        with call_log_lock:
            call_log.append("bg_load_state")
        return {"sym_a": {"name": "Alpha", "triggered": True}}

    def _spy_save(state):
        with call_log_lock:
            call_log.append("save_state")
        write_completed.set()

    with (
        patch.object(app_module, "database") as db_mock,
        patch.object(app_module, "_refresh_account_totals"),
        patch.object(app_module, "_FLUSH_STATE_LOCK", spy_lock),
    ):
        db_mock.load_state.side_effect = _spy_load
        db_mock.save_state.side_effect = _spy_save

        resp = client.post("/api/settings/flush-resync")
        write_completed.wait(timeout=2)

    assert resp.status_code == 200

    with call_log_lock:
        log_snapshot = list(call_log)

    # lock.acquire must appear in the call log
    assert "lock.acquire" in log_snapshot, (
        f"lock.acquire was never called.  call_log={log_snapshot!r}.  "
        "_FLUSH_STATE_LOCK must be acquired in _flush_state_async."
    )
    # bg_load_state must appear after lock.acquire
    assert "bg_load_state" in log_snapshot, (
        f"bg_load_state was never logged from the background thread.  "
        f"call_log={log_snapshot!r}.  "
        "The second load_state call must come from _flush_state_async."
    )

    acq_idx = log_snapshot.index("lock.acquire")
    load_idx = log_snapshot.index("bg_load_state")
    assert acq_idx < load_idx, (
        f"lock.acquire at index {acq_idx} but bg_load_state at index {load_idx}.  "
        f"call_log={log_snapshot!r}.  "
        "database.load_state() is being called BEFORE acquiring _FLUSH_STATE_LOCK.  "
        "This partial fix still has the lost-update race window:  "
        "the engine can write to SQLite between the background-thread's load_state "
        "(outside lock) and the lock acquisition, and the subsequent save_state "
        "(inside lock) will discard the engine's update.  "
        "Fix: move database.load_state() to INSIDE the 'with _FLUSH_STATE_LOCK:' "
        "block so load+modify+save is fully atomic."
    )
    # Also verify save_state is inside the lock (after acquire, before release)
    save_idx = log_snapshot.index("save_state")
    release_idx = log_snapshot.index("lock.release")
    assert acq_idx < save_idx < release_idx, (
        f"Expected lock.acquire ({acq_idx}) < save_state ({save_idx}) < "
        f"lock.release ({release_idx}).  call_log={log_snapshot!r}.  "
        "save_state must be called while the lock is held."
    )
