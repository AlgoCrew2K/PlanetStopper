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
import threading
import time
from datetime import time as dtime
from types import SimpleNamespace

import pytest
import schedule

import app as app_module

# Bounds on waiting for something that must happen; none of them is a time a verdict is judged by.
# The first is how long the stub holds Composer unless the test releases it, longer than the other
# two together, so an early end of the hold can only be the test's own release.
HELD_FOR_S = 120.0
LOOP_JOIN_S = 20.0
SETTLE_S = 30.0


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
        busy machine cannot change the verdict."""
        composer_total_stats.mode = "hold"
        # Longer than any wait below, so the hold ends only when this test releases it.
        composer_total_stats.hold_timeout_s = HELD_FOR_S

        def _before(tick, jobs):
            join_engine_threads()
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
                _run_loop(monkeypatch, ticks=2, before_tick=_before)
            except BaseException as exc:  # noqa: BLE001 -- handed to the test thread below
                outcome["error"] = exc
            # Read on this thread at the instant the loop ended.
            outcome["composer_still_held"] = not composer_total_stats.returned.is_set()

        loop = threading.Thread(target=_loop_under_test, name="scheduler-under-test", daemon=True)
        loop.start()
        try:
            loop.join(timeout=LOOP_JOIN_S)
            loop_ended_in_time = not loop.is_alive()
            # Engine threads end their run with a refresh attempt; let them make it while the
            # first refresh is still held, so it finds that one in flight.
            join_engine_threads()
        finally:
            composer_total_stats.release.set()
            loop.join(timeout=SETTLE_S)
        assert not loop.is_alive(), (
            "the scheduler loop did not end even after Composer was released"
        )
        assert "error" not in outcome, f"the scheduler loop raised {outcome.get('error')!r}"
        assert loop_ended_in_time, (
            "the two :00 passes had not finished while Composer was held: the refresh ran on the "
            "scheduler thread"
        )
        assert outcome["composer_still_held"], (
            "the loop ended only after the held Composer call returned"
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
