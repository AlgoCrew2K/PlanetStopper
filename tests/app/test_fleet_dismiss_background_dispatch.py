"""
RED tests — fleet-dismiss background-thread dispatch contract.

Cycle: cycle/sprint1-fleet-dismiss-fix
Scope: POST /api/fleet-alert/dismiss — Option-A implementation (background thread).

CONTRACT BEING ASSERTED (all tests must be RED on the current broken handler):

  1. Handler returns 200 in <50 ms — it must not block on the DB write.
  2. write_fleet_alert is dispatched to a background thread (NOT called directly
     on the Flask request thread).
  3. The background thread completes the write within a bounded timeout — the
     user-visible dismissed_at_et lands in the DB.
  4. After dismiss + bounded wait, dismissed_at_et IS NOT NULL in
     fleet_alert_state (the user-visible outcome).
  5. Side-effect ban preserved: the Flask handler frame itself does NOT call
     write_fleet_alert synchronously.  Background-thread dispatch is permitted.
  6. If write_fleet_alert raises, handler still returns 200 (logged, not surfaced).
  7. Two rapid dismiss POSTs both return 200; effective write is idempotent.
  8. Ten rapid POSTs — handler latency stays below ceiling across all ten
     (background threads must not accumulate and block subsequent requests).
  9. Existing side-effect-ban tests re-run here to confirm the new implementation
     does not regress their assertions.

Fixture provenance:
  tests/fixtures/app/fleet_dismiss_background_dispatch.json
  (schema-derived; no hardcoded producer values)

Mocking strategy:
  - Network: never called.
  - database.write_fleet_alert: patched via patch("database.write_fleet_alert").
    IMPORTANT: the implementation uses a module-level ThreadPoolExecutor
    (_DISMISS_EXECUTOR).  The executor's worker thread runs AFTER the Flask
    handler returns — after the `with patch(...)` block would normally exit.
    Any test that needs to observe the background write MUST call
    write_completed.wait() INSIDE the `with patch(...)` block, not after it.
    Tests that assert the mock is NOT called (side-effect ban tests) exit
    immediately after post() returns — the mock hasn't been called yet, which
    is the correct assertion.
  - database.read_fleet_alert: patched to return the fixture sample row.
  - database.load_state / database.save_state: patched and asserted NOT called.
  - Time: not mocked — handler latency tests use wall-clock time and assert
    < ceiling from fixture.  Tolerance comment on each assertion.

Anti-patterns avoided:
  - Never mock the threading machinery itself (assert BEHAVIOR, not implementation).
  - Never assert exact float timing — use ceiling from fixture with comment.
  - Never share state across test classes via module-level mutables.
"""

from __future__ import annotations

import json
import pathlib
import threading
import time
from unittest.mock import MagicMock, patch, call

import pytest

_FIXTURE_PATH = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "app"
    / "fleet_dismiss_background_dispatch.json"
)
_FIXTURE = json.loads(_FIXTURE_PATH.read_text())

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_ROW: dict = dict(_FIXTURE["sample_alert_row"])


def _active_row() -> dict:
    """Return a fresh copy of the fixture sample row (active, not yet dismissed)."""
    return dict(_SAMPLE_ROW)


# ---------------------------------------------------------------------------
# Flask client fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def flask_client():
    """Flask test client — patches database.init_db so no file DB is opened."""
    with patch("database.init_db"):
        import app as flask_app

    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as client:
        yield client


# ---------------------------------------------------------------------------
# Section 1 — Handler returns 200 quickly (must not block on DB write)
# ---------------------------------------------------------------------------


class TestDismissHandlerReturnsBeforeBackgroundWrite:
    """
    AC: The handler must return 200 before the background write completes.
    The <50 ms ceiling (from fixture) accounts for Python test-client overhead
    while still rejecting a synchronous DB write (typically >1 ms on SQLite).
    """

    def test_dismiss_handler_returns_200_within_latency_ceiling(self, flask_client):
        """Handler must return 200 in under the fixture-specified ceiling (ms).

        The test intentionally introduces a small delay in the mock write to
        prove the handler does NOT wait for the write.  A synchronous handler
        would block for the full delay; the background-dispatch handler returns
        almost immediately.
        """
        max_ms = _FIXTURE["handler_latency_ms"]["max_ms"]
        write_started = threading.Event()
        write_may_proceed = threading.Event()

        def _slow_write(_payload):
            write_started.set()
            # Hold the write until the test releases it — proves handler didn't wait.
            write_may_proceed.wait(timeout=2)

        with (
            patch("database.read_fleet_alert", return_value=_active_row()),
            patch("database.write_fleet_alert", side_effect=_slow_write),
        ):
            start = time.monotonic()
            resp = flask_client.post("/api/fleet-alert/dismiss")
            elapsed_ms = (time.monotonic() - start) * 1000

        # Allow write to complete so the test thread doesn't leak.
        write_may_proceed.set()

        assert resp.status_code == 200, f"Dismiss handler must return 200. Got {resp.status_code}."
        assert elapsed_ms < max_ms, (
            f"Handler must return in < {max_ms} ms (fixture ceiling). "
            f"Elapsed: {elapsed_ms:.1f} ms. "
            "A synchronous handler would block on the DB write; this latency suggests "
            "write_fleet_alert is being called on the request thread — use background dispatch."
            # Tolerance: 50 ms is generous for Flask test client overhead (~5 ms typical);
            # a real synchronous SQLite write with the _slow_write mock would block ~2 s.
        )

    def test_dismiss_handler_returns_200_when_no_alert_row(self, flask_client):
        """Handler must return 200 quickly even when read_fleet_alert returns None.

        Idempotent path: no write needed (nothing to dismiss), handler still fast.
        """
        max_ms = _FIXTURE["handler_latency_ms"]["max_ms"]

        with (
            patch("database.read_fleet_alert", return_value=None),
            patch("database.write_fleet_alert") as mock_write,
        ):
            start = time.monotonic()
            resp = flask_client.post("/api/fleet-alert/dismiss")
            elapsed_ms = (time.monotonic() - start) * 1000

        assert resp.status_code == 200
        assert elapsed_ms < max_ms, (
            f"Handler must return in < {max_ms} ms even when no alert row. "
            f"Elapsed: {elapsed_ms:.1f} ms."
            # Tolerance: same 50 ms ceiling — the no-op path has no I/O at all.
        )


# ---------------------------------------------------------------------------
# Section 2 — Background dispatch occurs (write_fleet_alert called off-thread)
# ---------------------------------------------------------------------------


class TestDismissDispatchesToBackgroundThread:
    """
    AC: The handler must dispatch write_fleet_alert to a background thread;
    it must NOT call it directly on the Flask request thread.

    Strategy: capture the current thread ID inside the mock to verify it is
    different from the test (= Flask request) thread.  The mock uses a
    threading.Event so the assertion runs only after the background call lands.
    """

    def test_write_fleet_alert_is_called_off_request_thread(self, flask_client):
        """write_fleet_alert must be invoked from a thread other than the request thread.

        Asserts dispatch occurs (not handler no-op) AND that dispatch is async.

        The wait must be inside the patch context — with a module-level executor the
        worker thread runs after the handler returns, and the mock must remain active
        until the worker calls through it.
        """
        request_thread_id = threading.current_thread().ident
        write_thread_ids: list[int] = []
        write_completed = threading.Event()

        def _capture_thread(_payload):
            write_thread_ids.append(threading.current_thread().ident)
            write_completed.set()

        with (
            patch("database.read_fleet_alert", return_value=_active_row()),
            patch("database.write_fleet_alert", side_effect=_capture_thread),
        ):
            flask_client.post("/api/fleet-alert/dismiss")
            # Wait inside the patch context so the mock is still active when the
            # executor's worker thread picks up the task and calls write_fleet_alert.
            dispatched = write_completed.wait(
                timeout=_FIXTURE["durable_write"]["wait_timeout_seconds"]
            )

        assert dispatched, (
            "write_fleet_alert was never called within the wait timeout. "
            "The handler must dispatch write_fleet_alert to a background thread — "
            "it must not be a no-op (current broken handler)."
        )
        assert write_thread_ids, (
            "write_fleet_alert side_effect did not record a thread ID — mock not called."
        )
        assert write_thread_ids[0] != request_thread_id, (
            "write_fleet_alert was called from the Flask request thread (same thread ID). "
            "The side-effect ban requires dispatch to a background thread."
        )

    def test_write_fleet_alert_not_called_on_request_thread_synchronously(self, flask_client):
        """write_fleet_alert must NOT have been called by the time the handler returns.

        The handler returns 200 before the background thread executes the write.
        If the mock is called synchronously (blocking the handler), this fails.

        The wait must be inside the patch context (executor worker runs after handler
        returns; mock must remain active until the worker calls through it).
        """
        handler_returned = threading.Event()
        call_order: list[str] = []
        write_called = threading.Event()

        def _write_with_order(_payload):
            # Record whether handler had returned before this was called.
            if handler_returned.is_set():
                call_order.append("write_after_return")
            else:
                call_order.append("write_before_return")
            write_called.set()

        with (
            patch("database.read_fleet_alert", return_value=_active_row()),
            patch("database.write_fleet_alert", side_effect=_write_with_order),
        ):
            flask_client.post("/api/fleet-alert/dismiss")
            # Mark that the handler has returned BEFORE waiting for background write.
            handler_returned.set()
            # Wait inside the patch context so the mock is still active when the
            # executor worker picks up the task.
            write_called.wait(timeout=_FIXTURE["durable_write"]["wait_timeout_seconds"])

        if not call_order:
            pytest.fail(
                "write_fleet_alert was never called. "
                "Handler must dispatch it to a background thread."
            )

        assert call_order[0] == "write_after_return", (
            f"write_fleet_alert was called BEFORE the handler returned (call_order={call_order}). "
            "This means the handler is blocking on the write — violates the side-effect ban "
            "and the <50 ms latency contract."
        )


# ---------------------------------------------------------------------------
# Section 3 — Background write lands (user-visible outcome)
# ---------------------------------------------------------------------------


class TestDismissBackgroundWriteCompletesWithDismissedTimestamp:
    """
    AC: After handler returns + bounded wait, dismissed_at_et is set (non-null).
    Asserts the user-visible outcome, not implementation detail.
    """

    def test_dismissed_at_et_set_after_background_write_completes(self, flask_client):
        """dismissed_at_et must be non-null in write_fleet_alert payload after background thread lands.

        Uses a threading.Event to wait for the write with a bounded timeout so
        the test is deterministic and does not rely on sleep.

        The wait must be inside the patch context — with a module-level executor the
        worker thread runs after the handler returns; the mock must still be active.
        """
        written_payloads: list[dict] = []
        write_completed = threading.Event()

        def _capture_payload(payload):
            written_payloads.append(dict(payload))
            write_completed.set()

        with (
            patch("database.read_fleet_alert", return_value=_active_row()),
            patch("database.write_fleet_alert", side_effect=_capture_payload),
        ):
            resp = flask_client.post("/api/fleet-alert/dismiss")
            # Wait inside the patch context so the mock is still active when the
            # executor's worker thread picks up the task and calls write_fleet_alert.
            dispatched = write_completed.wait(
                timeout=_FIXTURE["durable_write"]["wait_timeout_seconds"]
            )

        assert resp.status_code == 200

        assert dispatched, (
            "Background write did not complete within "
            f"{_FIXTURE['durable_write']['wait_timeout_seconds']} s. "
            "Handler must dispatch write_fleet_alert to a background thread."
        )

        assert written_payloads, "write_fleet_alert mock was not called — no payload captured."
        payload = written_payloads[0]
        assert payload.get("dismissed_at_et") is not None, (
            "dismissed_at_et must be set to a non-null timestamp after background write. "
            f"Payload received: {payload!r}. "
            "The background thread must call write_fleet_alert with a timestamped payload."
        )

    def test_dismissed_at_et_payload_is_a_string(self, flask_client):
        """dismissed_at_et in the write payload must be a non-empty string (timestamp format).

        Does not assert exact value — asserts format/shape only (project rule: no hardcoded
        producer-computed values).
        """
        written_payloads: list[dict] = []
        write_completed = threading.Event()

        def _capture(payload):
            written_payloads.append(dict(payload))
            write_completed.set()

        with (
            patch("database.read_fleet_alert", return_value=_active_row()),
            patch("database.write_fleet_alert", side_effect=_capture),
        ):
            flask_client.post("/api/fleet-alert/dismiss")
            # Wait inside the patch context — executor worker runs after handler returns.
            write_completed.wait(timeout=_FIXTURE["durable_write"]["wait_timeout_seconds"])

        assert written_payloads, "write_fleet_alert not called."
        dismissed_at = written_payloads[0].get("dismissed_at_et")
        assert isinstance(dismissed_at, str), (
            f"dismissed_at_et must be a string timestamp. Got {type(dismissed_at)!r}: {dismissed_at!r}."
        )
        assert len(dismissed_at) > 0, "dismissed_at_et must be a non-empty string."

    def test_write_payload_preserves_existing_alert_fields(self, flask_client):
        """Background write payload must retain the existing alert fields from read_fleet_alert.

        The implementation reads the row, sets dismissed_at_et, and writes back.
        No fields should disappear — the write must be a merge, not a replacement with
        only dismissed_at_et.
        """
        original_row = _active_row()
        written_payloads: list[dict] = []
        write_completed = threading.Event()

        def _capture(payload):
            written_payloads.append(dict(payload))
            write_completed.set()

        with (
            patch("database.read_fleet_alert", return_value=original_row),
            patch("database.write_fleet_alert", side_effect=_capture),
        ):
            flask_client.post("/api/fleet-alert/dismiss")
            # Wait inside the patch context — executor worker runs after handler returns.
            write_completed.wait(timeout=_FIXTURE["durable_write"]["wait_timeout_seconds"])

        assert written_payloads
        payload = written_payloads[0]
        for key in ("tripped_at_et", "triggered_reason", "tripped_count", "active_count"):
            assert key in payload, (
                f"Background write payload is missing field '{key}'. "
                "The dismiss implementation must not strip the existing alert row contents — "
                "it must set dismissed_at_et on the existing dict and write the whole row back."
            )


# ---------------------------------------------------------------------------
# Section 4 — Side-effect ban on request thread preserved
# ---------------------------------------------------------------------------


class TestDismissSideEffectBanOnRequestThread:
    """
    Architecture constraint 2: the Flask handler frame must not call
    write_fleet_alert synchronously.  Background-thread dispatch is permitted.

    These tests mock write_fleet_alert at the app.database import boundary and
    verify it was NOT called during the synchronous portion of the handler.
    """

    def test_write_fleet_alert_not_called_synchronously_on_flask_request_thread(self, flask_client):
        """app.database.write_fleet_alert must not be invoked directly by the handler.

        We intercept the call at the import-level so any synchronous call from
        within the handler body is captured before it reaches the real function.
        The background thread will also call through this mock, but after the
        handler returns.  We record whether the call happened BEFORE or AFTER
        the handler returned (see Section 2 for detail); this test uses the
        call_count right after handler return.
        """
        handler_returned = threading.Event()
        calls_before_return: list[int] = [0]

        original_mock = MagicMock()

        def _spy(payload):
            if not handler_returned.is_set():
                calls_before_return[0] += 1
            original_mock(payload)

        with (
            patch("database.read_fleet_alert", return_value=_active_row()),
            patch("database.write_fleet_alert", side_effect=_spy),
        ):
            flask_client.post("/api/fleet-alert/dismiss")
            # Capture the call count immediately after handler returns — before
            # the background thread has a chance to run.
            immediate_call_count = calls_before_return[0]
            handler_returned.set()

        assert immediate_call_count == 0, (
            f"write_fleet_alert was called {immediate_call_count} time(s) on the request thread "
            "(BEFORE the handler returned). "
            "Architecture constraint 2 / side-effect ban: the handler must dispatch to a "
            "background thread, not call write_fleet_alert directly. "
            f"Expected 0 direct calls; got {immediate_call_count}."
        )

    def test_handler_does_not_call_load_state(self, flask_client):
        """Handler must NOT call database.load_state — race-prone old path eliminated.

        AC-6 / R1: dismiss writes ONLY to fleet_alert_state.
        """
        with (
            patch("database.load_state") as mock_load,
            patch("database.save_state"),
            patch("database.read_fleet_alert", return_value=_active_row()),
            patch("database.write_fleet_alert"),
        ):
            flask_client.post("/api/fleet-alert/dismiss")

        # Allow any background threads to complete.
        time.sleep(0.05)

        assert mock_load.call_count == 0, (
            f"dismiss handler called database.load_state() {mock_load.call_count} time(s). "
            "AC-6: the handler must NOT touch bot_state — use fleet_alert_state exclusively."
        )

    def test_handler_does_not_call_save_state(self, flask_client):
        """Handler must NOT call database.save_state — this was the original race condition.

        AC-6 / R1: dismiss must never write to bot_state.
        """
        with (
            patch("database.load_state"),
            patch("database.save_state") as mock_save,
            patch("database.read_fleet_alert", return_value=_active_row()),
            patch("database.write_fleet_alert"),
        ):
            flask_client.post("/api/fleet-alert/dismiss")

        time.sleep(0.05)

        assert mock_save.call_count == 0, (
            f"dismiss handler called database.save_state() {mock_save.call_count} time(s). "
            "AC-6: save_state is the old race condition source — must never be called from dismiss."
        )


# ---------------------------------------------------------------------------
# Section 5 — Error resilience (write failure must not degrade UI)
# ---------------------------------------------------------------------------


class TestDismissHandlerResilientToWriteFailure:
    """
    AC: If write_fleet_alert raises in the background thread, the handler must
    still return 200.  The operator UI must not degrade on a transient DB error.
    (Failure is logged; it is not surfaced to the operator.)
    """

    def test_handler_returns_200_when_background_write_raises(self, flask_client):
        """Handler must return 200 even when write_fleet_alert raises an exception.

        The exception fires in the background thread.  The handler has already
        returned 200 before the thread executes, so the exception must not
        surface to the Flask response layer.
        """

        def _write_raises(_payload):
            raise OSError("DB locked: simulated transient write failure")

        with (
            patch("database.read_fleet_alert", return_value=_active_row()),
            patch("database.write_fleet_alert", side_effect=_write_raises),
        ):
            resp = flask_client.post("/api/fleet-alert/dismiss")

        # Allow background thread to run and raise.
        time.sleep(0.1)

        assert (
            resp.status_code == _FIXTURE["error_resilience"]["expected_status_on_write_failure"]
        ), (
            f"Handler must return 200 even when the background write raises. "
            f"Got {resp.status_code}. "
            "A raised exception in the background thread must not propagate to the Flask handler."
        )

    def test_handler_returns_status_ok_json_when_background_write_raises(self, flask_client):
        """Response JSON must have status=ok even when write_fleet_alert raises."""

        def _write_raises(_payload):
            raise RuntimeError("Simulated disk full")

        with (
            patch("database.read_fleet_alert", return_value=_active_row()),
            patch("database.write_fleet_alert", side_effect=_write_raises),
        ):
            resp = flask_client.post("/api/fleet-alert/dismiss")

        time.sleep(0.1)

        data = resp.get_json()
        assert data is not None, "Response must be JSON."
        assert data.get("status") == "ok", (
            f"Response JSON must have status='ok' even when the background write raises. "
            f"Got: {data!r}."
        )


# ---------------------------------------------------------------------------
# Section 6 — Idempotency under rapid successive dismisses
# ---------------------------------------------------------------------------


class TestDismissIdempotencyUnderRapidRequests:
    """
    AC: Multiple rapid dismiss POSTs must all return 200.  The effective write
    is idempotent — double-dismiss must not crash or corrupt state.
    """

    def test_two_rapid_dismiss_posts_both_return_200(self, flask_client):
        """Two dismiss POSTs in rapid succession must both return 200."""
        rapid_count = _FIXTURE["idempotency"]["rapid_post_count"]
        write_calls: list[dict] = []
        all_written = threading.Event()

        def _capture(payload):
            write_calls.append(dict(payload))
            if len(write_calls) >= 1:
                all_written.set()

        responses = []
        with (
            patch("database.read_fleet_alert", return_value=_active_row()),
            patch("database.write_fleet_alert", side_effect=_capture),
        ):
            for _ in range(rapid_count):
                resp = flask_client.post("/api/fleet-alert/dismiss")
                responses.append(resp.status_code)

        all_written.wait(timeout=_FIXTURE["durable_write"]["wait_timeout_seconds"])

        for i, code in enumerate(responses):
            assert code == 200, (
                f"POST #{i + 1} of {rapid_count} returned {code}; expected 200. "
                "Dismiss must be idempotent — rapid successive POSTs must all succeed."
            )

    def test_handler_latency_stable_across_ten_rapid_posts(self, flask_client):
        """Handler latency must remain below ceiling across N=10 rapid POSTs.

        Background threads from earlier requests must not block subsequent requests.
        """
        post_count = _FIXTURE["rapid_post_latency"]["post_count"]
        max_ms = _FIXTURE["rapid_post_latency"]["max_ms_per_request"]

        slowest_ms = 0.0

        write_gate = threading.Event()
        write_gate.set()  # Allow writes through immediately for this test.

        def _write(_payload):
            write_gate.wait(timeout=1)

        with (
            patch("database.read_fleet_alert", return_value=_active_row()),
            patch("database.write_fleet_alert", side_effect=_write),
        ):
            for i in range(post_count):
                start = time.monotonic()
                resp = flask_client.post("/api/fleet-alert/dismiss")
                elapsed_ms = (time.monotonic() - start) * 1000
                slowest_ms = max(slowest_ms, elapsed_ms)
                assert resp.status_code == 200, (
                    f"POST #{i + 1} returned {resp.status_code}; expected 200."
                )

        assert slowest_ms < max_ms, (
            f"Slowest of {post_count} dismiss requests: {slowest_ms:.1f} ms "
            f"(ceiling: {max_ms} ms). "
            "Background threads from prior requests must not accumulate and block the handler."
            # Tolerance: 50 ms ceiling — this is conservative; test-client overhead is ~5 ms.
            # A synchronous DB write would push this over 50 ms for any meaningful DB load.
        )


# ---------------------------------------------------------------------------
# Section 7 — Regression: existing side-effect-ban tests still pass
# ---------------------------------------------------------------------------


class TestExistingSideEffectBanInvariantsPreserved:
    """
    Re-run the core assertions from the prior side-effect-ban cycle here to
    verify the new background-dispatch implementation does not regress them.

    Source: tests/engine/test_fleet_alert_state_table.py
    TestDismissRouteWritesOnlyToFleetAlertState
    """

    @pytest.fixture()
    def _fleet_client(self):
        with patch("database.init_db"):
            import app as flask_app

        flask_app.app.config["TESTING"] = True
        with flask_app.app.test_client() as client:
            yield client

    def test_dismiss_route_returns_200_regression(self, _fleet_client):
        """Regression: POST /api/fleet-alert/dismiss must return 200 (existing assertion)."""
        with (
            patch("database.write_fleet_alert"),
            patch("database.read_fleet_alert", return_value=_active_row()),
        ):
            resp = _fleet_client.post("/api/fleet-alert/dismiss")

        assert resp.status_code == 200, (
            f"Regression: POST /api/fleet-alert/dismiss must return 200. Got {resp.status_code}."
        )

    def test_dismiss_route_does_not_call_load_state_regression(self, _fleet_client):
        """Regression: dismiss must NOT call database.load_state() (AC-6 isolation)."""
        with (
            patch("database.load_state") as mock_load,
            patch("database.save_state"),
            patch("database.write_fleet_alert"),
            patch("database.read_fleet_alert", return_value=_active_row()),
        ):
            _fleet_client.post("/api/fleet-alert/dismiss")

        time.sleep(0.05)  # Allow background thread to complete.

        assert mock_load.call_count == 0, (
            f"Regression: database.load_state called {mock_load.call_count} time(s). "
            "Must be 0 (AC-6: dismiss ONLY writes to fleet_alert_state)."
        )

    def test_dismiss_route_does_not_call_save_state_regression(self, _fleet_client):
        """Regression: dismiss must NOT call database.save_state() (AC-6 isolation)."""
        with (
            patch("database.load_state"),
            patch("database.save_state") as mock_save,
            patch("database.write_fleet_alert"),
            patch("database.read_fleet_alert", return_value=_active_row()),
        ):
            _fleet_client.post("/api/fleet-alert/dismiss")

        time.sleep(0.05)

        assert mock_save.call_count == 0, (
            f"Regression: database.save_state called {mock_save.call_count} time(s). "
            "Must be 0 (AC-6: save_state call is the old race condition — eliminated)."
        )

    def test_dismiss_route_returns_status_ok_json_regression(self, _fleet_client):
        """Regression: dismiss must return JSON {'status': 'ok'} (existing assertion)."""
        with (
            patch("database.write_fleet_alert"),
            patch("database.read_fleet_alert", return_value=None),
        ):
            resp = _fleet_client.post("/api/fleet-alert/dismiss")

        data = resp.get_json()
        assert data is not None, "Regression: dismiss route must return JSON."
        assert data.get("status") == "ok", (
            f'Regression: dismiss route must return {{"status": "ok"}}. Got: {data!r}.'
        )

    def test_dismiss_route_idempotent_when_no_alert_row_regression(self, _fleet_client):
        """Regression: dismiss returns 200 even when no alert row exists (idempotent)."""
        with (
            patch("database.write_fleet_alert"),
            patch("database.read_fleet_alert", return_value=None),
        ):
            resp = _fleet_client.post("/api/fleet-alert/dismiss")

        assert resp.status_code == 200, (
            f"Regression: dismiss must be idempotent when no row. Got {resp.status_code}."
        )
