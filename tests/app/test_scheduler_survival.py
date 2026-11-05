"""RED tests: the scheduler survives any slot's exception and never waits on Composer.

Contract pinned here (``run_scheduler`` and its helpers in app.py):
  - Every registration is wrapped by ``_guard(job, label)``: the job's ``job_func`` becomes a
    ``_GuardedJobCall`` (a ``functools.partial`` subclass) over the same function, arguments and
    keywords, which catches ``Exception``, logs ONE ERROR naming the slot and the exception's type
    (never its message) and returns normally, so ``schedule`` reschedules the job. Without it the
    library never reschedules a raising job, runs it again on every ``run_pending()`` and skips
    the due jobs sorted after it.
  - The loop is ``while True: _scheduler_tick(); time.sleep(1)``. ``_scheduler_tick()`` runs
    ``schedule.run_pending()`` inside ``try/except Exception`` (one ERROR line with the type name),
    then stamps ``_scheduler_last_tick_mono`` (``_monotonic()``) and ``_scheduler_last_tick_at``
    (``_now_et()``). A ``BaseException`` is never swallowed, and ``time.sleep`` stays outside the
    ``try`` so a stopper raised from it ends the loop.
  - ``run_scheduler`` stamps ``_scheduler_started_mono`` when it starts.
  - The ``:00`` account refresh is registered as ``_start_account_refresh`` (a thread start), so no
    Composer call runs on the scheduler thread; the engine spawn is ``threaded_trigger``.
  - Slots keep their host-clock times.

The jobs run on a real ``schedule.Scheduler`` (through its real ``Job.run``), made due by setting
``job.next_run``. The loop is left by a stopper derived from ``BaseException``. The engine is the
``StubEngine`` of tests/app/conftest.py; Composer is the captured ``total_stats`` body behind
``requests.get``.
"""

from __future__ import annotations

import datetime as dt
import functools
import logging
import os
import sys
import threading
import time
from datetime import time as dtime
from types import SimpleNamespace

import pytest
import requests
import schedule

import app as app_module

# Bounds on waiting for something that must happen; none of them is a time a verdict is judged by.
# SETTLE_S is how long the test's own hooks wait for an engine run or a refresh to end. The loop
# guard is a hang guard only: the Composer-held test's hook makes three such waits inside the loop
# (one on the first pass, two on the second), so the loop may legitimately take three SETTLE_S on a
# starved machine; the guard allows one more, and a loop still running after it is hung, not slow.
# HELD_FOR_S is how long the stub holds Composer unless the test releases it, longer than the guard
# and a settle together, so an early end of the hold can only be the test's own release.
SETTLE_S = 30.0
LOOP_GUARD_S = 4 * SETTLE_S
HELD_FOR_S = 2 * LOOP_GUARD_S + SETTLE_S
# How long teardown waits for a thread an engine run or the scheduler started to end.
LEFTOVER_THREAD_JOIN_S = 10.0
LOOP_POLL_S = 0.2


class _StopLoop(BaseException):
    """Leaves run_scheduler's endless loop; BaseException so no loop-level catch swallows it."""


class _TimeWithSleep:
    """app.time with sleep replaced and every other attribute real."""

    def __init__(self, sleep) -> None:
        self.sleep = sleep

    def __getattr__(self, name):
        return getattr(time, name)


def _func_of(job):
    return getattr(job.job_func, "func", job.job_func)


def _run_loop(monkeypatch, *, ticks: int, before_tick=None):
    """Run app.run_scheduler on a real Scheduler for `ticks` iterations.

    `before_tick(n, jobs)` runs before the n-th run_pending (1-based) so the test decides which
    jobs are due. Returns (scheduler, the list of sleep arguments).
    """
    scheduler = schedule.Scheduler()
    sleeps: list[float] = []

    def _run_pending():
        if before_tick is not None:
            before_tick(len(sleeps) + 1, scheduler.jobs)
        else:
            _set_due(scheduler.jobs, {})  # no job may fire when the real minute turns over
        scheduler.run_pending()

    def _sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= ticks:
            raise _StopLoop

    monkeypatch.setattr(
        app_module,
        "schedule",
        SimpleNamespace(every=scheduler.every, run_pending=_run_pending),
    )
    monkeypatch.setattr(app_module, "time", _TimeWithSleep(_sleep))
    with pytest.raises(_StopLoop):
        app_module.run_scheduler()
    return scheduler, sleeps


def _set_due(jobs, due: dict) -> None:
    """Make exactly the jobs whose function is a key of `due` run on this pass; the value is how
    many seconds ago each was due (a larger value sorts first). Everything else is a day away."""
    now = dt.datetime.now()
    for job in jobs:
        fn = _func_of(job)
        if fn in due:
            job.next_run = now - dt.timedelta(seconds=due[fn])
        else:
            job.next_run = now + dt.timedelta(days=1)


def _errors(caplog, needle: str) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno >= logging.ERROR and needle in r.getMessage()
    ]


class TestRegistrations:
    _EXPECTED = [
        ("threaded_trigger", "minutes", dtime(0, 0)),
        ("_start_account_refresh", "minutes", dtime(0, 0)),
        ("_run_trigger_retention", "days", dtime(2, 0)),
        ("_run_lens_pipeline", "days", dtime(3, 0)),
        ("_run_incubation_tick", "days", dtime(3, 30)),
        ("_run_retirement_recommender_tick", "days", dtime(3, 45)),
        ("_start_model_warmup", "days", dtime(4, 15)),
        ("_run_regime_label_tick", "days", dtime(4, 30)),
    ]

    def test_every_slot_is_guarded_registered_once_and_keeps_its_time(self, monkeypatch):
        scheduler, _ = _run_loop(monkeypatch, ticks=1)
        guarded = getattr(app_module, "_GuardedJobCall", None)
        assert guarded is not None, "app._GuardedJobCall is missing"
        problems = []

        for name, unit, at_time in self._EXPECTED:
            target = getattr(app_module, name, None)
            matching = [j for j in scheduler.jobs if _func_of(j) is target]
            if len(matching) != 1:
                problems.append(f"{name}: registered {len(matching)} times, expected once")
                continue
            (job,) = matching
            if not isinstance(job.job_func, guarded):
                problems.append(f"{name}: not wrapped by _GuardedJobCall")
            if (job.unit, job.interval, job.at_time) != (unit, 1, at_time):
                problems.append(
                    f"{name}: runs {(job.unit, job.interval, job.at_time)}, expected "
                    f"{(unit, 1, at_time)}"
                )
            if job.at_time_zone is not None:
                problems.append(f"{name}: carries a time zone; slots keep host-clock times")

        assert not problems, "\n".join(problems)
        assert len(scheduler.jobs) == len(self._EXPECTED), (
            "run_scheduler registers a slot this test does not know, or one without a guard: "
            f"{[getattr(_func_of(j), '__name__', _func_of(j)) for j in scheduler.jobs]}"
        )

    def test_the_minute_refresh_is_a_thread_start_never_the_refresh_itself(self, monkeypatch):
        scheduler, _ = _run_loop(monkeypatch, ticks=1)

        functions = [_func_of(j) for j in scheduler.jobs]

        assert app_module._refresh_account_totals not in functions, (
            "the :00 slot must start the refresh on its own thread, not run it on the scheduler"
        )

    def test_the_scheduler_records_when_it_started(self, monkeypatch, monotonic_clock):
        _run_loop(monkeypatch, ticks=1)

        assert getattr(app_module, "_scheduler_started_mono", None) == monotonic_clock.now


# Name prefix of the thread that runs one account refresh (``_start_account_refresh``).
_ACCOUNT_REFRESH_THREAD_PREFIX = "account-refresh-"
# Threads a scheduler pass or an engine run starts and never joins: the refresh an engine run
# makes when it ends, the closed-market warm-up the loop starts, and the engine run itself.
_LEFTOVER_THREAD_NAMES = ("cycle-refresh", "closed-warmup", "engine-cycle")


@pytest.fixture(autouse=True)
def _no_network_and_no_leftover_threads(monkeypatch, composer_total_stats):
    """No test of this file can reach a network, and none leaves a thread running.

    A finished engine run starts a cycle refresh that calls Composer's total-stats route. The
    conftest stub stands behind ``requests.get`` (credentials and the env file isolated), set to
    fail like a dropped connection so nothing is cached; ``requests.post`` and every other
    session request are replaced by a recorder that fails the test at teardown, so a call that
    bypasses the stub cannot pass unseen. Teardown releases a held Composer call and joins the
    refresh, warm-up and engine threads, so the next test never meets a thread of this one.
    """
    attempts: list[str] = []

    def _no_network(label: str):
        def _call(*_args, **_kwargs):
            attempts.append(label)
            raise requests.exceptions.ConnectionError(f"no network in this test: {label}")

        return _call

    composer_total_stats.mode = "connection_error"
    monkeypatch.setattr(requests, "post", _no_network("requests.post"))
    monkeypatch.setattr(
        requests.Session,
        "request",
        lambda _session, *args, **kwargs: _no_network("requests.Session.request")(),
    )
    yield
    composer_total_stats.release.set()
    leftover = []
    for thread in threading.enumerate():
        if thread.name in _LEFTOVER_THREAD_NAMES or thread.name.startswith(
            _ACCOUNT_REFRESH_THREAD_PREFIX
        ):
            thread.join(timeout=LEFTOVER_THREAD_JOIN_S)
            if thread.is_alive():
                leftover.append(thread.name)
    assert not leftover, f"threads still running after the test: {leftover}"
    assert not attempts, f"the test reached the network: {attempts}"


def _is_engine_finish_wait(code) -> bool:
    """True for the one wait the scheduler pass makes on an engine run's own done event.

    ``_reap_exited_engine_run`` waits for a run whose process already ended to finish its
    bookkeeping. That wait belongs to the engine run, not to the account refresh, and on a loaded
    machine it is real, so it is exempt. The match is the function's name and the app module's
    file, so no other caller is exempt.
    """
    return code.co_name == "_reap_exited_engine_run" and os.path.normcase(
        code.co_filename
    ) == os.path.normcase(app_module.__file__)


class _SchedulerWaitRecorder:
    """Records every wait the scheduler loop's thread makes while jobs run, never a duration.

    Active only between the end of the test's own ``before_tick`` hook and the start of the next
    one, so the waits the test makes itself are not counted. A wait is a blocking acquire of the
    account-refresh lock (``acquire`` with a timeout or without ``blocking=False``, or ``with``),
    a ``Condition`` or ``Event`` wait, or a ``Thread.join``; a non-blocking acquire and a wait
    with a zero timeout are polls and are not recorded. Starting a thread waits on that thread's
    started event inside ``threading``, which the recorder ignores.
    """

    def __init__(self, loop_thread_name: str) -> None:
        self._loop_thread_name = loop_thread_name
        self.active = False
        self.waits: list[str] = []

    def _record(self, what: str) -> None:
        if self.active and threading.current_thread().name == self._loop_thread_name:
            self.waits.append(what)

    def around_hook(self, hook):
        """before_tick wrapper that switches recording off while the hook runs."""

        def _wrapped(tick, jobs):
            self.active = False
            try:
                hook(tick, jobs)
            finally:
                self.active = True

        return _wrapped

    def install(self, monkeypatch) -> None:
        recorder = self
        real_flight = app_module._ACCOUNT_REFRESH_FLIGHT

        class _RecordingFlight:
            def acquire(self, blocking=True, timeout=-1):
                if blocking and timeout != 0:
                    recorder._record("account-refresh lock acquire")
                return real_flight.acquire(blocking, timeout)

            def release(self):
                real_flight.release()

            def locked(self):
                return real_flight.locked()

            def __enter__(self):
                recorder._record("account-refresh lock entered with a with-block")
                return real_flight.__enter__()

            def __exit__(self, *exc):
                return real_flight.__exit__(*exc)

        monkeypatch.setattr(app_module, "_ACCOUNT_REFRESH_FLIGHT", _RecordingFlight())

        real_event_wait = threading.Event.wait
        real_condition_wait = threading.Condition.wait
        real_join = threading.Thread.join

        def _event_wait(event, timeout=None):
            caller = sys._getframe(1).f_code
            starting_a_thread = (
                caller.co_filename == threading.__file__ and caller.co_name == "start"
            )
            if timeout != 0 and not starting_a_thread and not _is_engine_finish_wait(caller):
                recorder._record("Event.wait")
            return real_event_wait(event, timeout)

        def _condition_wait(condition, timeout=None):
            # Event.wait and Thread.start wait on a condition inside threading itself.
            inside_threading = sys._getframe(1).f_code.co_filename == threading.__file__
            if timeout != 0 and not inside_threading:
                recorder._record("Condition.wait")
            return real_condition_wait(condition, timeout)

        def _join(thread, timeout=None):
            if timeout != 0 and thread.name.startswith(_ACCOUNT_REFRESH_THREAD_PREFIX):
                recorder._record("Thread.join on the account refresh thread")
            return real_join(thread, timeout)

        monkeypatch.setattr(threading.Event, "wait", _event_wait)
        monkeypatch.setattr(threading.Condition, "wait", _condition_wait)
        monkeypatch.setattr(threading.Thread, "join", _join)


class TestEngineFinishWaitExemption:
    def test_the_exempt_function_still_exists_in_the_app_module(self):
        """A rename must break here, never silently count or skip a different wait."""
        function = getattr(app_module, "_reap_exited_engine_run", None)

        assert callable(function), "the engine finish wait function was renamed or removed"
        assert _is_engine_finish_wait(function.__code__), (
            "the exemption no longer matches the function by its name and file"
        )

    def test_the_real_engine_finish_wait_is_not_recorded_but_another_event_wait_is(
        self, monkeypatch
    ):
        """The wait is made for real here, so the verdict never depends on machine load."""
        recorder = _SchedulerWaitRecorder(threading.current_thread().name)
        recorder.install(monkeypatch)
        recorder.active = True
        exited_process = SimpleNamespace(poll=lambda: 0)
        monkeypatch.setattr(
            app_module, "_engine_process", ("cycle", exited_process, threading.Event())
        )
        monkeypatch.setattr(app_module, "_ENGINE_FINISH_WAIT_S", 0.01)

        app_module._reap_exited_engine_run()
        assert recorder.waits == [], "the engine run's finish wait is not a wait on the refresh"

        threading.Event().wait(0.01)
        assert recorder.waits == ["Event.wait"], "an Event wait from any other caller is recorded"

    def test_a_wait_from_any_other_function_is_not_exempt(self):
        def _reap_exited_engine_run():  # same name, defined in this file, not in app.py
            return sys._getframe(0).f_code

        assert not _is_engine_finish_wait(_reap_exited_engine_run())


class TestSlotsThatRaise:
    def test_a_raising_early_slot_is_logged_with_its_name_and_the_engine_still_spawns(
        self, monkeypatch, tmp_path, stub_engine, join_engine_threads, caplog
    ):
        env = tmp_path / ".env"
        env.write_text("TRIGGER_TELEMETRY_RETENTION_DAYS=abc\n", encoding="utf-8")
        monkeypatch.setattr(app_module, "ENV_FILE_PATH", str(env))

        def _before(tick, jobs):
            if tick == 1:
                _set_due(
                    jobs,
                    {app_module._run_trigger_retention: 5, app_module.threaded_trigger: 1},
                )
            else:
                _set_due(jobs, {})
            join_engine_threads()

        with caplog.at_level(logging.ERROR):
            _run_loop(monkeypatch, ticks=3, before_tick=_before)
        join_engine_threads()

        assert stub_engine.starts == 1, "the engine must still be spawned on that pass"
        slot_errors = _errors(caplog, "02:00")
        assert len(slot_errors) == 1, (
            f"one ERROR line for the one failed run of the 02:00 slot; got {slot_errors}"
        )
        assert "ValueError" in slot_errors[0]
        assert "abc" not in slot_errors[0] and "invalid literal" not in slot_errors[0], (
            "the line names the exception type, never its message"
        )

    def test_a_slot_that_raises_every_minute_never_costs_an_engine_spawn(
        self, monkeypatch, stub_engine, join_engine_threads, caplog
    ):
        def _lens_pipeline_fails():
            raise ValueError("secret-detail-in-the-message")

        monkeypatch.setattr(app_module, "_run_lens_pipeline", _lens_pipeline_fails)

        def _before(tick, jobs):
            join_engine_threads()
            _set_due(jobs, {app_module._run_lens_pipeline: 5, app_module.threaded_trigger: 1})

        with caplog.at_level(logging.ERROR):
            _run_loop(monkeypatch, ticks=10, before_tick=_before)
        join_engine_threads()

        assert stub_engine.starts == 10, "ten simulated :00 passes must spawn ten engines"
        slot_errors = _errors(caplog, "03:00")
        assert len(slot_errors) == 10, f"one ERROR per failed run; got {len(slot_errors)}"
        assert not any("secret-detail" in m for m in slot_errors)

    def test_composer_held_for_the_whole_run_does_not_delay_either_engine_spawn(
        self, monkeypatch, stub_engine, composer_total_stats, join_engine_threads
    ):
        """Both passes finish while Composer is still held. The proof is the order of events, the
        loop thread ending before the stub's held call returns, never a count of seconds, so a
        busy machine cannot change the verdict. The scheduler thread must also make no waiting
        call on the account refresh at all, however short: a bounded wait would still end while
        Composer is held and pass the order check alone."""
        composer_total_stats.mode = "hold"
        loop_thread_name = "scheduler-under-test"
        waits = _SchedulerWaitRecorder(loop_thread_name)
        waits.install(monkeypatch)
        # Longer than any wait below, so the hold ends only when this test releases it.
        composer_total_stats.hold_timeout_s = HELD_FOR_S

        def _before(tick, jobs):
            # A first run still alive here would make the second :00 skip its spawn, so a busy
            # machine gets the whole settle bound to end it, never the helper's default.
            assert join_engine_threads(SETTLE_S), "the earlier engine run did not end"
            if tick == 2:
                # The second :00 must meet a refresh that is really in flight.
                assert composer_total_stats.started.wait(timeout=SETTLE_S), (
                    "the first :00 pass started no account refresh"
                )
            _set_due(jobs, {})
            for job in jobs:
                if (
                    job.unit == "minutes"
                ):  # the engine spawn and the account refresh, whatever it is
                    job.next_run = dt.datetime.now() - dt.timedelta(seconds=1)

        outcome: dict = {}

        def _loop_under_test():
            try:
                _run_loop(monkeypatch, ticks=2, before_tick=waits.around_hook(_before))
            except BaseException as exc:  # noqa: BLE001 -- handed to the test thread below
                outcome["error"] = exc
            # Read on this thread at the instant the loop ended.
            outcome["composer_still_held"] = not composer_total_stats.returned.is_set()

        loop = threading.Thread(target=_loop_under_test, name=loop_thread_name, daemon=True)
        loop.start()
        try:
            # The verdict is the order of events (composer_still_held, read on the loop thread
            # when it ended). This wait only leaves early when the scheduler thread has begun a
            # wait, which the recorder sees before the thread blocks, so a fault does not cost the
            # whole guard; the guard bounds a hang and is longer than every wait the hook makes.
            guard_ends = time.monotonic() + LOOP_GUARD_S
            while loop.is_alive() and not waits.waits and time.monotonic() < guard_ends:
                loop.join(timeout=LOOP_POLL_S)
            loop_hung = loop.is_alive() and not waits.waits
            if not waits.waits:
                # Engine threads end their run with a refresh attempt; let them make it while the
                # first refresh is still held, so it finds that one in flight.
                assert join_engine_threads(SETTLE_S), "an engine run did not end"
        finally:
            composer_total_stats.release.set()
            loop.join(timeout=SETTLE_S)
        assert not loop.is_alive(), (
            "the scheduler loop did not end even after Composer was released"
        )
        assert "error" not in outcome, f"the scheduler loop raised {outcome.get('error')!r}"
        assert waits.waits == [], (
            "the scheduler thread waited on the account refresh while spawning the engine: "
            f"{waits.waits}"
        )
        assert not loop_hung, (
            f"the scheduler loop was still running after the {LOOP_GUARD_S:.0f} s hang guard, "
            "with Composer held and no wait recorded"
        )
        assert outcome["composer_still_held"], (
            "the loop ended only after the held Composer call returned: the two :00 passes had "
            "not finished while Composer was held"
        )

        assert stub_engine.starts == 2
        assert composer_total_stats.call_count == 1, (
            "the second :00 must find the first refresh still in flight and start no second one"
        )


class TestSchedulerTick:
    def test_an_exception_from_run_pending_is_logged_by_type_and_the_tick_is_still_stamped(
        self, monkeypatch, monotonic_clock, et_clock, caplog
    ):
        def _run_pending():
            raise RuntimeError("detail-that-must-not-appear")

        monkeypatch.setattr(app_module, "schedule", SimpleNamespace(run_pending=_run_pending))

        with caplog.at_level(logging.ERROR):
            app_module._scheduler_tick()

        errors = _errors(caplog, "RuntimeError")
        assert len(errors) == 1
        assert "detail-that-must-not-appear" not in errors[0]
        assert app_module._scheduler_last_tick_mono == monotonic_clock.now
        assert dt.datetime.fromisoformat(app_module._scheduler_last_tick_at) == et_clock.now

    def test_a_healthy_tick_stamps_both_clocks(self, monkeypatch, monotonic_clock, et_clock):
        monkeypatch.setattr(app_module, "schedule", SimpleNamespace(run_pending=lambda: None))
        monotonic_clock.advance(37.0)
        et_clock.now += dt.timedelta(seconds=37)

        app_module._scheduler_tick()

        assert app_module._scheduler_last_tick_mono == monotonic_clock.now
        assert dt.datetime.fromisoformat(app_module._scheduler_last_tick_at) == et_clock.now

    def test_a_base_exception_is_never_swallowed(self, monkeypatch):
        def _run_pending():
            raise _StopLoop

        monkeypatch.setattr(app_module, "schedule", SimpleNamespace(run_pending=_run_pending))

        with pytest.raises(_StopLoop):
            app_module._scheduler_tick()

    def test_the_loop_sleeps_one_second_between_ticks_outside_the_catch(self, monkeypatch):
        scheduler, sleeps = _run_loop(monkeypatch, ticks=3)

        assert sleeps == [1, 1, 1]


class TestGuard:
    def _job(self, fn, *args, **kwargs):
        scheduler = schedule.Scheduler()
        return scheduler.every().day.at("01:00").do(fn, *args, **kwargs)

    def test_guard_keeps_the_function_arguments_and_wrapper_attributes(self):
        guard = getattr(app_module, "_guard", None)
        assert guard is not None, "app._guard(job, label) is missing"
        calls: list = []

        def slot(a, *, k):
            """slot docstring"""
            calls.append((a, k))

        job = self._job(slot, 1, k=2)

        returned = guard(job, "01:00 test slot")

        assert returned is job
        assert isinstance(job.job_func, functools.partial)
        assert isinstance(job.job_func, app_module._GuardedJobCall)
        assert job.job_func.func is slot
        assert (job.job_func.args, job.job_func.keywords) == ((1,), {"k": 2})
        assert job.job_func.__name__ == "slot" and job.job_func.__doc__ == "slot docstring"
        job.job_func()
        assert calls == [(1, 2)]

    def test_a_raising_slot_is_logged_once_by_label_and_type_and_returns_normally(self, caplog):
        def slot():
            raise KeyError("detail-that-must-not-appear")

        job = self._job(slot)
        app_module._guard(job, "01:00 test slot")

        with caplog.at_level(logging.ERROR):
            result = job.job_func()

        assert result is None
        errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(errors) == 1
        assert "01:00 test slot" in errors[0] and "KeyError" in errors[0]
        assert "detail-that-must-not-appear" not in errors[0]

    def test_a_base_exception_from_a_slot_is_not_swallowed(self):
        def slot():
            raise _StopLoop

        job = self._job(slot)
        app_module._guard(job, "01:00 test slot")

        with pytest.raises(_StopLoop):
            job.job_func()

    def test_a_guarded_slot_that_raises_is_rescheduled_by_the_library(self):
        def slot():
            raise ValueError("boom")

        scheduler = schedule.Scheduler()
        job = scheduler.every().day.at("01:00").do(slot)
        app_module._guard(job, "01:00 test slot")
        job.next_run = dt.datetime.now() - dt.timedelta(seconds=1)

        scheduler.run_pending()

        assert job.next_run > dt.datetime.now(), (
            "a raising slot must be rescheduled, or it runs again on every pass"
        )
