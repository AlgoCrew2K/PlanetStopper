"""RED tests: at most one engine runs at a time, and a skipped minute is logged and published.

``threaded_trigger()`` is the callable the minute scheduler registers at ``:00``. Contract:
  - It reads the wall clock once (``app._now_et``), asks ``_begin_engine_run(started_at)`` for
    the run slot and, when it gets one, starts a daemon thread named ``engine-cycle`` that runs
    ``trigger_alpha_bot(cycle_id=..., started_at=...)``.
  - When a run is already in flight it spawns nothing: it builds a ``CycleRecord`` with outcome
    "skipped" (``changed`` False, no exit code, ``running_cycle_id`` and ``running_since`` naming
    the run in flight) and hands it to ``_publish_cycle``, which writes one WARNING on the
    ``alphabot`` logger through ``_log_skipped_minute(record)`` and emits the event.
  - ``trigger_alpha_bot``'s ``finally`` ends the run slot (``_end_engine_run(cycle_id)``) before it
    publishes, each in its own guard, so a publishing fault never leaves the slot taken.
  - Nothing kills an engine: a hung run is reported and each minute skipped until it exits.
  - A thread that cannot start frees the slot and logs one ERROR; the next ``:00`` spawns normally.

The engine process is replaced by ``StubEngine`` (tests/app/conftest.py), which blocks on an
Event, so the tests drive real threads, the real run state and the real publisher. Clocks are
``app._now_et`` and ``app._monotonic``. The stub is the engine's process boundary (it mirrors the
exit behaviour at ``alpha_bot_execution.py``), not producer data.
"""

from __future__ import annotations

import logging
import threading
from datetime import timedelta

import pytest

import app as app_module
import database

_WAIT_S = 5.0


def _iso(value) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _engine_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == "engine-cycle" and t.is_alive()]


def _run_state():
    state_fn = getattr(app_module, "_engine_run_state", None)
    if state_fn is None:
        pytest.fail(
            "app._engine_run_state() is missing: it returns the EngineRun in flight or None"
        )
    return state_fn()


class TestRunSlot:
    def test_begin_returns_the_run_and_a_second_begin_while_it_runs_returns_none(
        self, et_clock, monotonic_clock
    ):
        first = app_module._begin_engine_run(et_clock.now)
        second = app_module._begin_engine_run(et_clock.now + timedelta(minutes=1))

        assert first is not None
        assert second is None, "a second run must not be admitted while the first is in flight"
        assert _run_state() is first
        assert first.started_mono == monotonic_clock.now
        assert _iso(first.started_at) == et_clock.now.isoformat()
        assert first.cycle_id.startswith(et_clock.now.replace(microsecond=0).isoformat())

    def test_ending_another_cycles_id_leaves_the_run_in_flight(self, et_clock):
        run = app_module._begin_engine_run(et_clock.now)

        app_module._end_engine_run("2026-10-22T09:00:00-04:00#1")

        assert _run_state() is run, "only the run's own id may end its slot"

    def test_ending_the_run_frees_the_slot_for_the_next(self, et_clock):
        run = app_module._begin_engine_run(et_clock.now)

        app_module._end_engine_run(run.cycle_id)

        assert _run_state() is None
        assert app_module._begin_engine_run(et_clock.now + timedelta(minutes=1)) is not None

    def test_ending_with_no_run_in_flight_is_harmless(self):
        app_module._end_engine_run("2026-10-22T09:00:00-04:00#1")

        assert _run_state() is None


class TestSkippedMinute:
    def test_a_run_in_flight_skips_the_next_minute_logs_it_and_the_minute_after_spawns(
        self, stub_engine, et_clock, published_cycles, join_engine_threads, caplog, monotonic_clock
    ):
        stub_engine.hold = True
        et_clock.now = et_clock.now.replace(minute=43, second=0)
        app_module.threaded_trigger()
        assert stub_engine.started.wait(timeout=_WAIT_S), "the first :00 must spawn the engine"
        running = _run_state()
        assert running is not None

        et_clock.now += timedelta(minutes=1)  # 10:44:00
        with caplog.at_level(logging.WARNING, logger="alphabot"):
            app_module.threaded_trigger()

        assert stub_engine.starts == 1, "no second engine may start while one is running"
        (skip,) = [r for r in published_cycles if r.outcome == "skipped"]
        assert skip.changed is False
        assert skip.exit_code is None
        assert skip.running_cycle_id == running.cycle_id
        assert skip.running_since == _iso(running.started_at)
        assert skip.cycle_id != running.cycle_id, "a skip carries its own id"
        assert skip.started_at == et_clock.now.isoformat()
        assert set(skip.event_payload()) == {
            "cycle_id",
            "started_at",
            "completed_at",
            "outcome",
            "changed",
        }
        lines = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        (line,) = [m for m in lines if running.cycle_id in m]
        assert skip.cycle_id in line, "the line names the skip's own id"
        assert _iso(running.started_at) in line, "the line names the running cycle's start time"
        assert "10:44" in line, "the line names the skipped minute"

        stub_engine.release.set()
        assert join_engine_threads()
        et_clock.now += timedelta(minutes=1)  # 10:45:00
        app_module.threaded_trigger()
        assert join_engine_threads()
        assert stub_engine.starts == 2, "once the run ended the next :00 spawns a new engine"

    def test_the_skip_event_reaches_the_clients_as_a_skipped_unchanged_event(
        self, stub_engine, et_clock, join_engine_threads, monkeypatch
    ):
        import json
        import queue

        stub_engine.hold = True
        client_q: queue.Queue = queue.Queue()
        monkeypatch.setattr(app_module, "_sse_clients", [client_q])
        refreshes: list = []
        monkeypatch.setattr(app_module, "_refresh_account_totals", lambda: refreshes.append(1))
        app_module.threaded_trigger()
        assert stub_engine.started.wait(timeout=_WAIT_S)
        et_clock.now += timedelta(minutes=1)

        app_module.threaded_trigger()

        name, data = client_q.get(timeout=_WAIT_S)
        payload = json.loads(data)
        assert name == "cycle-complete"
        assert (payload["outcome"], payload["changed"]) == ("skipped", False)
        assert refreshes == [], "a skipped minute must not start an account refresh"
        stub_engine.release.set()
        assert join_engine_threads()

    def test_runs_in_sequence_never_overlap(
        self, stub_engine, et_clock, published_cycles, join_engine_threads
    ):
        stub_engine.hold = True
        app_module.threaded_trigger()  # run 1 in flight
        assert stub_engine.started.wait(timeout=_WAIT_S)
        et_clock.now += timedelta(minutes=1)
        app_module.threaded_trigger()  # skipped while run 1 runs
        stub_engine.release.set()
        assert join_engine_threads()
        stub_engine.hold = False
        et_clock.now += timedelta(minutes=1)
        app_module.threaded_trigger()  # run 2
        assert join_engine_threads()

        assert stub_engine.events == [("start", 1), ("end", 1), ("start", 2), ("end", 2)], (
            "the second run may only start after the first ended"
        )

    def test_a_hung_engine_is_never_killed_and_every_minute_is_skipped(
        self,
        stub_engine,
        et_clock,
        monotonic_clock,
        published_cycles,
        join_engine_threads,
        monkeypatch,
    ):
        kills: list = []
        monkeypatch.setattr(app_module.os, "kill", lambda *a, **k: kills.append(a), raising=False)
        stub_engine.hold = True
        app_module.threaded_trigger()
        assert stub_engine.started.wait(timeout=_WAIT_S)
        first_id = _run_state().cycle_id

        for _ in range(10):
            et_clock.now += timedelta(minutes=1)
            monotonic_clock.advance(60.0)
            app_module.threaded_trigger()

        assert stub_engine.starts == 1
        assert len([r for r in published_cycles if r.outcome == "skipped"]) == 10
        assert all(
            r.running_cycle_id == first_id for r in published_cycles if r.outcome == "skipped"
        )
        assert _engine_threads(), "the daemon must not end the engine run"
        assert kills == [], "the daemon must not signal the engine"
        assert _run_state() is not None
        stub_engine.release.set()
        assert join_engine_threads()

    def test_runs_that_finish_within_the_minute_spawn_once_per_minute_and_never_skip(
        self, stub_engine, et_clock, published_cycles, join_engine_threads
    ):
        for _ in range(5):
            app_module.threaded_trigger()
            assert join_engine_threads()
            et_clock.now += timedelta(minutes=1)

        assert stub_engine.starts == 5
        assert [r.outcome for r in published_cycles].count("skipped") == 0
        assert len(published_cycles) == 5
        assert _run_state() is None


class TestRunSlotIsAlwaysFreed:
    def test_the_slot_is_ended_before_the_record_is_published(
        self, stub_engine, et_clock, join_engine_threads, monkeypatch
    ):
        seen_in_flight: list = []

        def _spy(record):
            seen_in_flight.append(_run_state())

        monkeypatch.setattr(app_module, "_publish_cycle", _spy)
        app_module.threaded_trigger()
        assert join_engine_threads()

        assert seen_in_flight == [None], (
            "the run slot must already be free when the record is published"
        )

    def test_a_publishing_fault_never_leaves_the_slot_taken(
        self, stub_engine, et_clock, join_engine_threads, monkeypatch
    ):
        def _boom(record):
            raise RuntimeError("publishing fault")

        monkeypatch.setattr(app_module, "_publish_cycle", _boom)
        app_module.threaded_trigger()
        assert join_engine_threads()

        assert _run_state() is None
        assert app_module._begin_engine_run(et_clock.now + timedelta(minutes=1)) is not None

    def test_a_failing_engine_frees_the_slot_and_publishes_a_failed_record(
        self, stub_engine, et_clock, published_cycles, join_engine_threads
    ):
        stub_engine.exit_code = 1
        app_module.threaded_trigger()
        assert join_engine_threads()

        assert _run_state() is None
        assert [r.outcome for r in published_cycles] == ["failed"]

    def test_a_thread_that_cannot_start_frees_the_slot_logs_one_error_and_the_next_minute_spawns(
        self, stub_engine, et_clock, published_cycles, join_engine_threads, caplog, monkeypatch
    ):
        class _ThreadThatCannotStart(threading.Thread):
            def start(self):
                raise RuntimeError("can't start new thread")

        with monkeypatch.context() as patch_scope:
            patch_scope.setattr(app_module.threading, "Thread", _ThreadThatCannotStart)
            with caplog.at_level(logging.ERROR):
                app_module.threaded_trigger()

        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(errors) == 1, f"one ERROR line for the failed start; got {len(errors)}"
        assert "RuntimeError" in errors[0].getMessage()
        assert _run_state() is None, "a run that never started must not hold the slot"
        assert stub_engine.starts == 0

        et_clock.now += timedelta(minutes=1)
        app_module.threaded_trigger()
        assert join_engine_threads()
        assert stub_engine.starts == 1, "the next :00 spawns normally"


class TestTheEnginesOwnLockIsUntouched:
    def test_a_lock_left_set_by_a_killed_run_expires_and_the_next_minute_still_spawns(
        self, stub_engine, et_clock, join_engine_threads
    ):
        from unittest.mock import patch

        base_time = 1_000_000.0
        with patch("database.time") as mock_time:
            mock_time.time.return_value = base_time
            assert database.acquire_lock() is True  # the killed run never released it

        app_module.threaded_trigger()
        assert join_engine_threads()
        assert stub_engine.starts == 1, "the daemon spawns whatever the engine's lock says"

        with patch("database.time") as mock_time:
            mock_time.time.return_value = base_time + 61.0
            assert database.acquire_lock() is True, (
                "the engine's own stale-lock expiry still applies after 60 s"
            )
