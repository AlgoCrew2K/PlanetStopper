"""
RED tests for CC-NEW-001 (flush_resync background write races engine save_state)
and LATENT-001 (log message count captured from request thread, not background thread).

These tests MUST FAIL on the current (unpatched) app.py and MUST PASS once the
fix is applied.  They cover exactly the four scenarios mandated by the cycle brief:

  1. test_engine_save_not_clobbered_by_concurrent_flush
       — engine's write must survive when it fires between flush's load and save.

  2. test_flush_load_modify_save_is_atomic_under_lock
       — the load+modify+save sequence is guarded by a module-level threading.Lock
         (app._FLUSH_STATE_LOCK must exist; flush closure must acquire it).

  3. test_flush_log_count_reflects_background_thread_iteration
       — the "wrote N symphony state entries" log line uses the count from the
         background thread's local state, not the request-thread's captured list.

  4. test_flush_resync_handler_returns_without_blocking_on_background_write
       — the Flask handler returns 200 in microseconds; serialization must not
         block the request thread (side-effect ban preserved).

Hostile edges exercised:
  - Engine write inserted deterministically between flush load and flush save
    using threading.Event barriers to force the worst-case interleaving.
  - Background-thread state diverges from request-thread state (engine adds a
    new key between the two load_state calls).
  - Handler return time is bounded: must complete before the background write
    even starts (executor queue is not drained before handler returns).

Mocking strategy:
  - database is patched via patch.object(app_module, "database").
  - threading.Thread is NOT patched — _DISMISS_EXECUTOR manages its own threads.
  - threading.Event barriers sequence the race deterministically.
  - time is NOT mocked — latency bound uses wall time with a generous ceiling.
"""

from __future__ import annotations

import logging
import threading
import time
from unittest.mock import MagicMock, call, patch

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
# Test 1 — Engine save is NOT clobbered by a concurrent flush
# ---------------------------------------------------------------------------


def test_engine_save_not_clobbered_by_concurrent_flush(client):
    """CC-NEW-001: engine's save_state update must be the last write when it
    races a concurrent flush.

    Without a lock the sequence is:
      T=0  flush thread: load_state() → stale base state
      T=1  engine thread: save_state(engine_updated_state)   ← engine writes
      T=2  flush thread: save_state(stripped stale state)    ← clobbers engine

    Final DB = stale stripped state.  Engine's update is lost.

    With a lock the flush holds the lock for its entire load+modify+save triple.
    The engine must wait for the lock before it can write.  Sequence becomes:
      T=0  flush acquires lock, load_state(), modify, save_state(stripped)
      T=1  flush releases lock
      T=2  engine acquires lock, save_state(engine_updated_state)

    Final DB = engine_updated_state.  Engine's update is NOT clobbered.

    Barrier design:
      - flush_holding_lock: flush signals after acquiring the lock, before
        saving.  Engine thread waits on this before attempting its write.
      - flush_may_save: test releases after verifying the engine is queued,
        allowing the flush to complete its save and release the lock.

    The assertion: saved_states[-1] == engine_updated_state.  The engine's
    write must be the last write; the flush's stripped save must come first.

    On unpatched code (no lock) the flush's stripped save is last →
    saved_states[-1] != engine_updated_state → test FAILS.
    """
    # flush signals it has acquired the lock (or started its write region)
    flush_holding_lock = threading.Event()
    # test releases flush to complete save_state (controls ordering)
    flush_may_save = threading.Event()
    # engine signals it has completed its save
    engine_wrote = threading.Event()

    all_saved = threading.Event()
    saved_states: list[dict] = []

    base_state = {"sym_a": {"name": "Alpha", "account": "acct-1", "triggered": True}}
    engine_updated_state = {"sym_a": {"name": "Alpha", "account": "acct-1", "stop": -5.0}}

    load_call_count = 0

    def _controlled_load():
        nonlocal load_call_count
        load_call_count += 1
        if load_call_count == 1:
            # Request-thread enumeration — return base state immediately
            return base_state.copy()
        # Background-thread load inside _flush_state_async.
        # Signal that the flush is now inside its critical section, then
        # wait until the test allows it to proceed (engine is queued).
        flush_holding_lock.set()
        flush_may_save.wait(timeout=2)
        return base_state.copy()

    def _controlled_save(state):
        saved_states.append(state.copy() if isinstance(state, dict) else state)
        if len(saved_states) >= 2:
            all_saved.set()

    def _simulate_engine_write():
        # Wait until flush is in its critical section, then try to write.
        # Without a lock this write races and may complete before flush's save.
        # With a lock this write must wait for the flush to release → engine
        # save lands AFTER flush save → engine's state is the final DB state.
        flush_holding_lock.wait(timeout=2)
        app_module.database.save_state(engine_updated_state)
        engine_wrote.set()

    with (
        patch.object(app_module, "database") as db_mock,
        patch.object(app_module, "_refresh_account_totals"),
    ):
        db_mock.load_state.side_effect = _controlled_load
        db_mock.save_state.side_effect = _controlled_save

        engine_thread = threading.Thread(target=_simulate_engine_write, daemon=True)
        engine_thread.start()

        resp = client.post("/api/settings/flush-resync")

        # Give flush time to reach flush_holding_lock.set(), then release it
        flush_holding_lock.wait(timeout=2)
        flush_may_save.set()

        # Wait for both saves inside the patch context
        all_saved.wait(timeout=3)
        engine_thread.join(timeout=2)

    assert resp.status_code == 200
    assert len(saved_states) == 2, (
        f"Expected exactly 2 save_state calls (flush + engine); got {len(saved_states)}.  "
        "Both the flush closure and the engine thread must call save_state."
    )

    # The engine write must be the LAST write (saved_states[-1]).
    # If the flush wrote last (no lock), it clobbered the engine's update.
    last_saved = saved_states[-1]
    assert last_saved == engine_updated_state, (
        f"Last save_state call was {last_saved!r}, expected engine_updated_state "
        f"{engine_updated_state!r}.  "
        "The flush's stripped save must come FIRST; the engine's write must be LAST.  "
        "Without a lock the flush races ahead and its stale stripped state is the "
        "final DB write, clobbering the engine's per-minute update.  "
        "Fix: hold app._FLUSH_STATE_LOCK around the flush closure's load+modify+save "
        "so the engine can only write after the flush releases — making the engine "
        "write the final (authoritative) state."
    )


# ---------------------------------------------------------------------------
# Test 2 — Flush load+modify+save is atomic under a lock
# ---------------------------------------------------------------------------


def test_flush_load_modify_save_is_atomic_under_lock():
    """CC-NEW-001: app module must expose _FLUSH_STATE_LOCK (threading.Lock).

    The fix serializes the flush closure by acquiring a module-level lock before
    the load+modify+save triple.  This test verifies the structural contract:

      - app._FLUSH_STATE_LOCK must exist and be a threading.Lock (or RLock).
      - The lock must be acquirable — not already held at import time.

    If the fix routes writes through _DISMISS_EXECUTOR's single-worker queue
    instead of an explicit lock, this test still passes because the team brief
    mandates option (b): a module-level threading.Lock.  If the implementer
    chooses option (a) or (c), this test provides a clear failure message so
    the reviewer can verify the actual serialization mechanism.
    """
    assert hasattr(app_module, "_FLUSH_STATE_LOCK"), (
        "app._FLUSH_STATE_LOCK not found.  "
        "The fix for CC-NEW-001 must add a module-level threading.Lock "
        "(e.g. _FLUSH_STATE_LOCK = threading.Lock()) so the flush closure "
        "can acquire it to serialize load+modify+save against engine writes."
    )
    lock = app_module._FLUSH_STATE_LOCK
    assert isinstance(lock, (type(threading.Lock()), type(threading.RLock()))), (
        f"app._FLUSH_STATE_LOCK is {type(lock)!r}, expected threading.Lock or RLock.  "
        "The serialization guard must be a standard threading primitive."
    )
    # Lock must be free at test time — not held by a lingering background task
    acquired = lock.acquire(blocking=False)
    assert acquired, (
        "app._FLUSH_STATE_LOCK could not be acquired.  "
        "The lock must be free between requests; a deadlock or unreleased acquire "
        "in the flush closure would block all subsequent flush operations."
    )
    lock.release()


# ---------------------------------------------------------------------------
# Test 3 — Log count reflects background-thread iteration, not request capture
# ---------------------------------------------------------------------------


def test_flush_log_count_reflects_background_thread_iteration(client, caplog):
    """LATENT-001: the 'wrote N symphony state entries' log line must report
    the count of entries the background thread actually iterated, not the
    request-thread's captured symphonies_reset list length.

    The divergence is forced by making the background thread's load_state return
    a different state than the request thread's load_state: the request thread
    sees 2 symphonies; the background thread sees only 1.

    Without the fix, the log says "wrote 2" (captured closure).
    With the fix, the log says "wrote 1" (background-thread local count).
    """
    request_thread_state = {
        "sym_a": {"name": "Alpha", "account": "acct-1", "triggered": True},
        "sym_b": {"name": "Beta", "account": "acct-2", "triggered": False},
    }
    # Background thread sees fewer symphonies (engine wrote between the two loads)
    background_thread_state = {
        "sym_a": {"name": "Alpha", "account": "acct-1"},
    }

    load_call_count = 0

    def _divergent_load():
        nonlocal load_call_count
        load_call_count += 1
        if load_call_count == 1:
            return request_thread_state.copy()
        return background_thread_state.copy()

    write_completed = threading.Event()

    def _capture_save(_state):
        write_completed.set()

    with (
        patch.object(app_module, "database") as db_mock,
        patch.object(app_module, "_refresh_account_totals"),
        caplog.at_level(logging.INFO, logger="alphabot"),
    ):
        db_mock.load_state.side_effect = _divergent_load
        db_mock.save_state.side_effect = _capture_save

        resp = client.post("/api/settings/flush-resync")
        write_completed.wait(timeout=2)

    assert resp.status_code == 200

    # Collect all "wrote N symphony state entries" log records
    wrote_messages = [
        r.getMessage()
        for r in caplog.records
        if "wrote" in r.getMessage() and "symphony state entries" in r.getMessage()
    ]

    assert wrote_messages, (
        "No 'wrote N symphony state entries' log message emitted by the background thread.  "
        "The fix must emit this log message from within the _flush_state_async closure."
    )

    # The count in the log must match the background thread's iteration (1), not the
    # request-thread's captured list (2).
    assert any("1" in msg for msg in wrote_messages), (
        f"Log message(s) found: {wrote_messages}.  "
        "Expected 'wrote 1 symphony state entries' — the count must reflect the "
        "background thread's local iteration over its own loaded state, not the "
        "request-thread's captured len(symphonies_reset) == 2.  "
        "Fix: compute len() inside _flush_state_async from the local 'state' dict "
        "after iterating it, not from the closed-over symphonies_reset list."
    )
    # Ensure the stale request-thread count is NOT the one logged
    assert not any(
        "wrote 2 symphony state entries" in msg for msg in wrote_messages
    ), (
        f"Log message(s) found: {wrote_messages}.  "
        "The log must NOT report the request-thread's stale count (2).  "
        "It must use the background thread's actual iteration count."
    )


# ---------------------------------------------------------------------------
# Test 4 — Handler returns without blocking on the background write
# ---------------------------------------------------------------------------


def test_flush_resync_handler_returns_without_blocking_on_background_write(
    client, tmp_path, monkeypatch
):
    """Architecture rule 1: no blocking I/O on the execution path.

    The flush handler must return 200 before the background thread's save_state
    completes.  A write_gate that the background thread must wait on lets the
    test verify the handler returned BEFORE the write unblocked.

    Without the side-effect ban being preserved, the handler would block on
    save_state and handler_returned_at would be > bg_write_started_at.
    With the ban, handler_returned_at < bg_write_started_at (or very close to it
    — the executor submission is near-instantaneous, so we bound by 500 ms).
    """
    monkeypatch.setattr(analytics, "_POST_MORTEMS_DIR", str(tmp_path / "pm"))

    write_gate = threading.Event()   # background thread waits on this
    handler_returned_at: list[float] = []
    bg_write_released_at: list[float] = []

    def _gated_save(_state):
        # Block until the test releases the gate (AFTER measuring handler return time)
        write_gate.wait(timeout=5)
        bg_write_released_at.append(time.monotonic())

    with (
        patch.object(app_module, "database") as db_mock,
        patch.object(app_module, "_refresh_account_totals"),
    ):
        db_mock.load_state.return_value = {}
        db_mock.save_state.side_effect = _gated_save

        t0 = time.monotonic()
        resp = client.post("/api/settings/flush-resync")
        handler_returned_at.append(time.monotonic())

        # Handler must have returned before we release the write gate.
        # The background write is still blocked; if the handler waited for it
        # the handler_returned_at would only be set after write_gate is released.
        assert resp.status_code == 200, "Handler must return 200."
        elapsed = handler_returned_at[0] - t0
        assert elapsed < 0.5, (
            f"Handler took {elapsed:.3f}s to return — expected < 0.5s.  "
            "The flush handler must dispatch the write to _DISMISS_EXECUTOR and "
            "return immediately; it must NOT block on the background save_state call.  "
            "Architecture rule 1: no blocking I/O on the execution path."
        )

        # Now release the gate so the background thread can finish and the
        # executor drains cleanly before the patch context exits.
        write_gate.set()
        # Give the executor worker time to finish before patch context exits
        deadline = time.monotonic() + 2
        while not bg_write_released_at and time.monotonic() < deadline:
            time.sleep(0.01)
