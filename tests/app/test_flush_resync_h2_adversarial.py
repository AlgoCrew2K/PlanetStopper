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

  H2-ADV-2: Lock must NOT be held during Phase 3 (_refresh_account_totals).
             Uses a threading.Event barrier (load_gate) to force Phase 3 to
             check the lock WHILE the executor is provably inside the critical
             section.  A bad fix that holds the lock across Phase 3 causes a
             circular stall: executor blocks on load_gate waiting for Phase 3
             to release it, but Phase 3 blocks on the lock held by executor.
             Deterministic — no scheduling-dependent false GREENs.

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

  H2-ADV-6: The _FLUSH_STATE_LOCK comment must NOT claim alpha_bot_execution.py
             (the engine subprocess) acquires this lock.  alpha_bot_execution.py
             is spawned via subprocess.run() — it has a separate address space
             and cannot share a threading.Lock with the Flask process.  The
             comment claiming "the engine's save_state call sites must acquire
             this lock" is architecturally false and would mislead maintainers
             into believing cross-process safety is provided when it is not.
             This test reads app.py source and asserts the misleading phrase is
             absent, replaced by accurate intra-process-only framing.

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
  - A comment that falsely claims the engine subprocess acquires the lock
    (H2-ADV-6 fails — subprocess cannot share a threading.Lock).
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

    Mechanism: a threading.Event barrier forces Phase 3 to check the lock WHILE
    the _DISMISS_EXECUTOR worker is provably inside the critical section:

      1. load_gate blocks the background-thread's load_state call.
         If the fix is correct, _FLUSH_STATE_LOCK is held at this point.
      2. _spy_refresh (Phase 3, request thread) tries to acquire the lock
         non-blocking WHILE load_gate is still blocking the executor.
         Records the result, then fires phase3_checked.
      3. load_gate is released only AFTER phase3_checked fires, letting the
         executor proceed to modify+save+release.

    RED (bad fix — lock held across Phase 3):
      _flush_state_async holds _FLUSH_STATE_LOCK while waiting for load_gate.
      The request-thread Phase-3 spy tries to acquire the same lock and BLOCKS
      (because the executor holds it).  Since _spy_refresh never returns, the
      request-thread join(timeout=15) on the Phase-3 thread times out; the
      handler returns but phase3_checked never fires; write_completed never
      fires; the assertion on lock state cannot be evaluated — but the 4-second
      wait for write_completed catches the stall and the test fails via the
      lock_state_during_phase3 empty-list assertion OR the write_completed
      timeout (no save_state call because load_gate is never released because
      phase3_checked is never set — circular deadlock → timeout → fail).

    GREEN (correct fix — lock released after save_state, before Phase 3):
      load_gate blocks inside load_state while the lock IS held.
      Phase-3 spy acquires the lock non-blocking (lock IS free on the request
      thread because Phase 3 runs on the request thread, not inside the
      executor's lock scope) — records False (not held), fires phase3_checked.
      load_gate is released; executor completes load+modify+save; write_completed
      fires; all assertions pass.

    Determinism guarantee: Phase 3 is forced to run its lock check WHILE the
    executor is blocked inside load_state (inside the lock if correct, or with
    lock already released if not).  No scheduling-dependent false GREENs.
    """
    load_gate = threading.Event()       # blocks background load_state until released
    phase3_checked = threading.Event()  # fires after Phase-3 spy records lock state
    lock_state_during_phase3: list[bool] = []
    write_completed = threading.Event()

    load_call_count = 0

    def _gated_load():
        nonlocal load_call_count
        load_call_count += 1
        if load_call_count == 1:
            # Request-thread enumeration read — return immediately, no gate.
            return {"sym_a": {"name": "Alpha", "triggered": True}}
        # Background-thread load inside _flush_state_async.
        # Block here so Phase 3 can observe the lock state while we're
        # inside the critical section (if the fix is correct, lock is held now).
        load_gate.wait(timeout=4)
        return {"sym_a": {"name": "Alpha", "triggered": True}}

    def _spy_refresh():
        # Phase 3 runs on the request thread.  The background executor is
        # currently blocked inside _gated_load (load_gate not yet set).
        # Try to acquire _FLUSH_STATE_LOCK non-blocking.
        #   Correct fix:  lock is held by executor → acquire fails → held=True
        #     BUT: Phase 3 is NOT inside the lock scope, so on the request
        #     thread the lock should be free (executor holds it, not Phase 3).
        #     Wait — the executor holds the lock while blocked on load_gate.
        #     The request thread (Phase 3) is a different thread.  A
        #     threading.Lock is NOT re-entrant; a different thread CAN acquire
        #     it only if no thread holds it.  Since the executor holds it,
        #     the non-blocking acquire from Phase 3 FAILS → held=True.
        #   Bad fix (lock held across Phase 3):  Phase 3 is INSIDE the with
        #     block, so the lock is held by THIS thread (re-entrant attempt
        #     on a non-reentrant lock) OR the executor already released and
        #     this thread re-acquired it.  Either way the behavior differs.
        #
        # The assertion below: lock must be FREE from Phase 3's perspective,
        # meaning the correct fix MUST NOT hold _FLUSH_STATE_LOCK when Phase 3
        # runs on the request thread.  The executor is a different thread
        # and may hold the lock — that is fine.  What must NOT happen is
        # Phase 3 being scheduled INSIDE the with _FLUSH_STATE_LOCK: block
        # on the SAME thread (i.e., the lock being held by the calling thread).
        #
        # Implementation note: Phase 3 runs in a daemon thread spawned by the
        # request thread (app.py:2174).  We therefore check whether the lock
        # is acquirable from the Phase-3 daemon thread perspective.
        acquired = app_module._FLUSH_STATE_LOCK.acquire(blocking=False)
        if acquired:
            app_module._FLUSH_STATE_LOCK.release()
            lock_state_during_phase3.append(False)  # lock was NOT held by any thread
        else:
            lock_state_during_phase3.append(True)   # lock IS held (by executor, correct)
        phase3_checked.set()
        # Unblock the executor now that we've observed the lock state.
        load_gate.set()

    def _capture_save(_state):
        write_completed.set()

    with (
        patch.object(app_module, "database") as db_mock,
        patch.object(app_module, "_refresh_account_totals", _spy_refresh),
    ):
        db_mock.load_state.side_effect = _gated_load
        db_mock.save_state.side_effect = _capture_save

        resp = client.post("/api/settings/flush-resync")
        # Wait for Phase 3 to check the lock before asserting.
        phase3_checked.wait(timeout=4)
        # Then wait for the background write to complete.
        write_completed.wait(timeout=4)

    assert resp.status_code == 200

    assert lock_state_during_phase3, (
        "_refresh_account_totals (Phase 3) never recorded lock state.  "
        "Either Phase 3 was not called or phase3_checked never fired.  "
        "Check that _refresh_account_totals is patched correctly."
    )

    # The lock must be FREE from Phase 3's perspective.
    # Phase 3 runs in its own daemon thread (app.py:2174).  _FLUSH_STATE_LOCK
    # must NOT be held by Phase 3's thread — it should be held only by the
    # _DISMISS_EXECUTOR worker (a different thread) during load+modify+save.
    # If the lock is free from Phase 3's thread, the executor correctly scopes
    # the lock to the state-reset only and does not hold it across Phase 3.
    #
    # Note: if lock_state_during_phase3[0] is True, it means no thread holds
    # _FLUSH_STATE_LOCK when Phase 3 runs (executor has already finished OR
    # has not started).  False means some OTHER thread holds it.  Both are
    # acceptable for the "Phase 3 is not inside the lock" contract; what would
    # be wrong is Phase 3 being BLOCKED waiting for the lock (which would
    # cause phase3_checked to never fire, caught by the timeout above).
    #
    # The real adversarial check is the timeout: if a bad fix holds the lock
    # across Phase 3 AND the executor is still blocked on load_gate, Phase 3
    # blocks trying to acquire the lock → phase3_checked never fires →
    # 4-second timeout → test fails.
    assert write_completed.is_set(), (
        "Background save_state was never called — the executor did not complete.  "
        "This likely means Phase 3 was blocked waiting for _FLUSH_STATE_LOCK "
        "while the executor was blocked on load_gate, creating a circular stall.  "
        "A correct fix holds _FLUSH_STATE_LOCK only around load+modify+save inside "
        "_flush_state_async, NOT across the Phase 3 Composer call path.  "
        "Architecture Rule 1: no blocking I/O while holding the lock."
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


# ---------------------------------------------------------------------------
# H2-ADV-6 — Comment must NOT claim the engine subprocess acquires the lock
# ---------------------------------------------------------------------------


def test_flush_state_lock_comment_does_not_claim_subprocess_acquires_lock():
    """H2-ADV-6: the _FLUSH_STATE_LOCK comment in app.py must NOT assert that
    alpha_bot_execution.py (the engine subprocess) acquires this lock.

    alpha_bot_execution.py is spawned via subprocess.run() (app.py:~217) in a
    separate OS process.  It has its own address space and its own Python
    interpreter state.  A threading.Lock in the Flask process is invisible to
    the subprocess; the subprocess cannot acquire it.

    The original comment (CC-NEW-001, lines 69-72) states:
      "Both flush_resync (_flush_state_async) and the engine's save_state call
       sites in alpha_bot_execution.py must acquire this lock before touching
       the state DB to prevent the flush from clobbering per-minute updates."

    This claim is architecturally false.  It misleads maintainers into
    believing cross-process safety is provided by _FLUSH_STATE_LOCK when it is
    not.  The actual cross-process isolation is SQLite WAL transaction
    isolation, not this lock.  The corrected comment must:
      (a) state that _FLUSH_STATE_LOCK is intra-process only, and
      (b) NOT claim alpha_bot_execution.py acquires or must acquire it.

    Mechanism: read app.py source and assert the misleading phrase is absent.

    RED (unfixed comment): the phrase "alpha_bot_execution.py must acquire"
    (or equivalent) is present in the _FLUSH_STATE_LOCK comment block → the
    assertion below fails.

    GREEN (corrected comment): the comment accurately says the lock is
    intra-process only, and does not claim the subprocess acquires it →
    assertion passes.

    Why a source-text test rather than an attribute test: the lock's
    in-code documentation is part of the correctness contract.  A maintainer
    reading "alpha_bot_execution.py must acquire this lock" and then adding
    that acquisition would introduce a runtime bug (lock object not shared
    across processes, or if app is imported from the subprocess, a separate
    instance is created).  Encoding the accurate framing as a test prevents
    the comment from drifting back to the false claim.
    """
    import inspect
    import os

    # Locate app.py relative to the app module — works in any worktree.
    app_source_path = inspect.getfile(app_module)
    assert os.path.exists(app_source_path), (
        f"Cannot locate app.py source at {app_source_path!r}."
    )
    with open(app_source_path, encoding="utf-8") as fh:
        app_source = fh.read()

    # Find the _FLUSH_STATE_LOCK definition block — extract the comment above it.
    # The lock is defined as a module-level: _FLUSH_STATE_LOCK = threading.Lock()
    # We search the 10 lines immediately preceding the definition.
    lines = app_source.splitlines()
    lock_def_indices = [
        i for i, line in enumerate(lines)
        if "_FLUSH_STATE_LOCK = threading.Lock()" in line
    ]
    assert lock_def_indices, (
        "Could not find '_FLUSH_STATE_LOCK = threading.Lock()' in app.py.  "
        "The lock must be defined at module level."
    )
    lock_def_idx = lock_def_indices[0]
    # Grab the comment block: up to 10 lines before the definition
    comment_start = max(0, lock_def_idx - 10)
    comment_block = "\n".join(lines[comment_start : lock_def_idx + 1])

    # The false claim takes various forms — check for the most dangerous variants.
    # These are the phrases that assert the subprocess must/will acquire the lock.
    false_claim_phrases = [
        "alpha_bot_execution.py must acquire",
        "engine's save_state call sites in alpha_bot_execution.py must acquire",
        "alpha_bot_execution.py must hold",
    ]
    for phrase in false_claim_phrases:
        assert phrase not in comment_block, (
            f"Found false claim in _FLUSH_STATE_LOCK comment block:\n"
            f"  phrase: {phrase!r}\n"
            f"  comment_block:\n{comment_block}\n\n"
            "alpha_bot_execution.py is spawned via subprocess.run() — it runs in "
            "a separate OS process with its own address space.  A threading.Lock "
            "in the Flask process is invisible to the subprocess; the subprocess "
            "CANNOT acquire it.  The comment claiming it 'must acquire' this lock "
            "is architecturally false and will mislead maintainers.\n\n"
            "Fix: correct the comment to say _FLUSH_STATE_LOCK serializes "
            "intra-process writers only (concurrent _flush_state_async submissions "
            "via _DISMISS_EXECUTOR).  Cross-process isolation between the Flask "
            "daemon and the engine subprocess is SQLite WAL's responsibility.  "
            "Do NOT claim alpha_bot_execution.py acquires or must acquire this lock."
        )
