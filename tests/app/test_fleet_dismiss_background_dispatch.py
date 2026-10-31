"""
RED tests — fleet-dismiss background-thread dispatch contract.

Cycle: cycle/sprint1-fleet-dismiss-fix
Scope: POST /api/fleet-alert/dismiss — Option-A implementation (background thread).

CONTRACT BEING ASSERTED (all tests must be RED on the current broken handler):

  1. The handler answers 200 only AFTER the write has landed, waiting a bounded time
     (app._FLEET_DISMISS_WRITE_WAIT_S); it still returns quickly when the write is quick.
  2. write_fleet_alert is dispatched to a background thread (NOT called directly
     on the Flask request thread).
  3. The background thread completes the write within a bounded timeout — the
     user-visible dismissed_at_et lands in the DB.
  4. After dismiss + bounded wait, dismissed_at_et IS NOT NULL in
     fleet_alert_state (the user-visible outcome).
  5. Side-effect ban preserved: the Flask handler frame itself does NOT call
     write_fleet_alert synchronously.  Background-thread dispatch is permitted.
  6. If write_fleet_alert raises, or the write misses the bounded wait, the handler answers
     503 {status: error, message: Dismissal not saved}, never ok.
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
from unittest.mock import MagicMock, patch

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


class TestDismissAnswersAfterItsWriteLands:
    """
    AC: the route answers ok only after dismissed_at_et has been written. The write still
    runs on the background executor (never on the request thread, see Section 2), and the
    route waits for it for a bounded time (app._FLEET_DISMISS_WRITE_WAIT_S).

    This replaces the earlier contract that the handler returned BEFORE the write: an ok
    that arrives before the write can be false (a lock held, a failed write), and the
    banner would hide for a dismissal that was never stored.
    """

    def test_the_answer_waits_for_the_write_and_is_ok_once_it_lands(self, flask_client):
        write_started = threading.Event()
        write_may_proceed = threading.Event()
        written = {}
        answered = {}

        def _gated_write(payload):
            written["thread"] = threading.current_thread().ident
            written["payload"] = dict(payload)
            write_started.set()
            write_may_proceed.wait(timeout=5)

        def _post():
            import app as flask_app

            answered["thread"] = threading.current_thread().ident
            # A client of its own: the fixture's client keeps its request context for its thread.
            answered["response"] = flask_app.app.test_client().post("/api/fleet-alert/dismiss")

        with (
            patch("database.read_fleet_alert", return_value=_active_row()),
            patch("database.write_fleet_alert", side_effect=_gated_write),
        ):
            requester = threading.Thread(target=_post)
            requester.start()
            assert write_started.wait(timeout=_FIXTURE["durable_write"]["wait_timeout_seconds"])
            requester.join(timeout=0.4)
            still_waiting = requester.is_alive()
            write_may_proceed.set()
            requester.join(timeout=5)

        assert still_waiting, "the route answered before dismissed_at_et was written"
        assert not requester.is_alive()
        assert answered["response"].status_code == 200
        assert answered["response"].get_json() == {"status": "ok"}
        assert isinstance(written["payload"].get("dismissed_at_et"), str)
        assert written["thread"] != answered["thread"], "the write must stay off the request thread"

    def test_the_wait_is_a_named_bound_above_the_database_lock_timeout(self):
        import app as flask_app

        bound = getattr(flask_app, "_FLEET_DISMISS_WRITE_WAIT_S", None)
        assert isinstance(bound, float), (
            "app._FLEET_DISMISS_WRITE_WAIT_S is the route's bounded wait"
        )
        assert 10.0 < bound <= 30.0, "it must outlast the state database's 10 s lock wait"

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

    def test_write_fleet_alert_has_landed_off_the_request_thread_when_the_handler_answers(
        self, flask_client
    ):
        """The handler answers only after the write has landed, and the write ran off the request thread.

        A 200 that came back before the write would let a later poll re-show an alert the operator
        had been told was dismissed. The mock records the thread it ran on and whether the handler
        had answered yet; both are read once the post call has returned.
        """
        request_thread_id = threading.current_thread().ident
        call_order: list[tuple[str, int]] = []
        handler_returned = threading.Event()

        def _write_with_order(_payload):
            call_order.append(
                (
                    "write_after_return" if handler_returned.is_set() else "write_before_return",
                    threading.current_thread().ident,
                )
            )

        with (
            patch("database.read_fleet_alert", return_value=_active_row()),
            patch("database.write_fleet_alert", side_effect=_write_with_order),
        ):
            response = flask_client.post("/api/fleet-alert/dismiss")
            handler_returned.set()

        assert response.status_code == 200
        assert call_order, "write_fleet_alert had not been called when the handler answered 200"
        assert call_order[0][0] == "write_before_return", (
            f"the handler answered 200 before the write landed (call_order={call_order}); "
            "an ok that comes first can be contradicted by the next poll."
        )
        assert call_order[0][1] != request_thread_id, (
            "write_fleet_alert ran on the Flask request thread; it must run on the dismiss executor."
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

    def test_write_fleet_alert_is_never_called_on_the_flask_request_thread(self, flask_client):
        """app.database.write_fleet_alert must not be invoked directly by the handler frame.

        The call is intercepted at the import boundary and the thread it ran on is recorded: the
        handler waits for the executor's write, so the call has happened by the time the post
        returns, and it must not have happened on the request thread.
        """
        request_thread_id = threading.current_thread().ident
        calls_on_request_thread: list[int] = []
        calls_total: list[int] = []

        def _spy(_payload):
            calls_total.append(1)
            if threading.current_thread().ident == request_thread_id:
                calls_on_request_thread.append(1)

        with (
            patch("database.read_fleet_alert", return_value=_active_row()),
            patch("database.write_fleet_alert", side_effect=_spy),
        ):
            flask_client.post("/api/fleet-alert/dismiss")

        assert calls_total, "write_fleet_alert was never called, so the dismissal was not written"
        assert not calls_on_request_thread, (
            f"write_fleet_alert was called {len(calls_on_request_thread)} time(s) on the request thread. "
            "Architecture constraint 2 / side-effect ban: the handler must dispatch to the executor."
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


class TestDismissAnswers503WhenTheWriteFails:
    """
    AC: a write that raises, or that does not land within the bounded wait, is answered 503
    {"status": "error", "message": "Dismissal not saved"} (a fixed string, never the exception's),
    and never ok.
    """

    NOT_SAVED = {"status": "error", "message": "Dismissal not saved"}

    def test_a_write_that_raises_is_503_not_ok(self, flask_client):
        def _write_raises(_payload):
            raise OSError("DB locked at /private/path/alphabot_state.db")

        with (
            patch("database.read_fleet_alert", return_value=_active_row()),
            patch("database.write_fleet_alert", side_effect=_write_raises),
        ):
            resp = flask_client.post("/api/fleet-alert/dismiss")

        assert resp.status_code == 503, f"a failed dismissal answered {resp.status_code}"
        assert resp.get_json() == self.NOT_SAVED
        assert "/private/path" not in resp.get_data(as_text=True)

    def test_a_read_that_raises_is_503_not_ok(self, flask_client):
        with patch("database.read_fleet_alert", side_effect=RuntimeError("locked")):
            resp = flask_client.post("/api/fleet-alert/dismiss")
        assert resp.status_code == 503
        assert resp.get_json() == self.NOT_SAVED

    def test_a_write_held_past_the_bounded_wait_is_503_not_ok(self, flask_client, monkeypatch):
        import app as flask_app

        monkeypatch.setattr(flask_app, "_FLEET_DISMISS_WRITE_WAIT_S", 0.3, raising=False)
        release = threading.Event()

        def _held_write(_payload):
            release.wait(timeout=5)

        try:
            with (
                patch("database.read_fleet_alert", return_value=_active_row()),
                patch("database.write_fleet_alert", side_effect=_held_write),
            ):
                started = time.monotonic()
                resp = flask_client.post("/api/fleet-alert/dismiss")
                waited = time.monotonic() - started
        finally:
            release.set()

        assert resp.status_code == 503, "a dismissal that had not landed was answered ok"
        assert resp.get_json() == self.NOT_SAVED
        assert 0.25 <= waited < 3.0, f"the route waited {waited:.2f}s for a 0.3s bound"

    def test_an_already_dismissed_alert_is_ok_with_no_write(self, flask_client):
        row = _active_row()
        row["dismissed_at_et"] = "2026-10-22T09:00:00-04:00"
        with (
            patch("database.read_fleet_alert", return_value=row),
            patch("database.write_fleet_alert") as mock_write,
        ):
            resp = flask_client.post("/api/fleet-alert/dismiss")
        assert resp.status_code == 200 and resp.get_json() == {"status": "ok"}
        mock_write.assert_not_called()


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
