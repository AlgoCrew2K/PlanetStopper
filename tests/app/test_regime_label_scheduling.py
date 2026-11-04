"""The 04:30 regime label slot: non-blocking, off-hours in every host zone, wired to the job.

Contract pinned here (``app.py``, next to the incubation and warm-up wrappers):
  - ``_run_regime_label_tick()`` starts a daemon thread running ``_regime_label_job_worker`` and
    returns at once, so a slow price fetch never delays the scheduler thread (the :00 engine spawn
    and account refresh keep firing every minute meanwhile);
  - ``_regime_label_job_worker()`` lazily imports ``regime_label_job`` and calls
    ``regime_label_job.run_regime_label_job(_regime_label_symphony_entries)``, looking the function
    up on the module at call time; it never raises and logs only an exception's class name;
  - the callable ``run_scheduler`` registers for the slot runs the whole job against the stored
    state, and its time of day is outside the 09:30 to 16:00 ET session for a host clock in UTC or
    in New York, in winter and in summer.

Registration (once, guarded, at 04:30 host time) is pinned by TestRegistrations in
tests/app/test_scheduler_survival.py. Run orchestration is in tests/regime_label_job/test_job_run.py.
"""

from __future__ import annotations

import datetime as dt
import logging
import sqlite3
import threading
import time
from datetime import time as dtime
from zoneinfo import ZoneInfo

import pytest
import requests

import app as app_module
import database
import regime_label_job
import synthetic_history
from tests.app.test_scheduler_survival import _func_of, _run_loop, _set_due
from tests.regime_label_job import _sip
from tests.regime_label_job.conftest import (
    ID_ONE,
    KEY_SENTINEL,
    SECRET_SENTINEL,
    captured_entry,
    golden_closes,
    golden_label,
)

ET = ZoneInfo("America/New_York")
SESSION_OPEN = dtime(9, 30)
SESSION_CLOSE = dtime(16, 0)


@pytest.fixture()
def worker_threads(monkeypatch):
    """Record every thread started with the slot's worker as its target."""
    real_thread = threading.Thread
    started: list[threading.Thread] = []

    class _Recorded(real_thread):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._recorded_target = kwargs.get("target")

        def start(self):
            if self._recorded_target is getattr(app_module, "_regime_label_job_worker", None):
                started.append(self)
            super().start()

    monkeypatch.setattr(threading, "Thread", _Recorded)
    return started


class TestTheSlotCallableDoesNotWait:
    def test_it_returns_while_the_job_is_still_running_on_a_daemon_thread(
        self, monkeypatch, worker_threads
    ):
        tick = getattr(app_module, "_run_regime_label_tick", None)
        assert tick is not None, "app._run_regime_label_tick is missing"
        entered, release = threading.Event(), threading.Event()

        def held_job(load_symphonies, **_kwargs):
            entered.set()
            release.wait(timeout=10)

        monkeypatch.setattr(regime_label_job, "run_regime_label_job", held_job)
        try:
            began = time.perf_counter()
            tick()
            elapsed = time.perf_counter() - began

            assert entered.wait(timeout=5), "the job never started"
            assert elapsed < 1.0, f"the slot callable took {elapsed:.1f} s: it ran the job inline"
            (thread,) = worker_threads
            assert thread.daemon is True
            assert thread.is_alive(), "the worker must still be inside the held job"
        finally:
            release.set()
            for thread in worker_threads:
                thread.join(timeout=10)


class TestTheWorker:
    def test_it_runs_the_job_over_the_symphony_entries_loader(self, monkeypatch):
        worker = getattr(app_module, "_regime_label_job_worker", None)
        assert worker is not None, "app._regime_label_job_worker is missing"
        calls: list[tuple] = []
        monkeypatch.setattr(
            regime_label_job,
            "run_regime_label_job",
            lambda load, **kwargs: calls.append((load, kwargs)),
        )

        worker()

        assert len(calls) == 1
        loader = getattr(app_module, "_regime_label_symphony_entries", None)
        assert loader is not None, "app._regime_label_symphony_entries is missing"
        assert calls[0][0] is loader

    def test_a_job_that_raises_is_logged_by_class_name_and_never_escapes(self, monkeypatch, caplog):
        worker = getattr(app_module, "_regime_label_job_worker", None)
        assert worker is not None, "app._regime_label_job_worker is missing"

        def failing_job(load, **_kwargs):
            raise RuntimeError("detail-that-must-not-appear")

        monkeypatch.setattr(regime_label_job, "run_regime_label_job", failing_job)

        with caplog.at_level(logging.DEBUG):
            worker()

        errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(errors) == 1 and "RuntimeError" in errors[0], errors
        assert "detail-that-must-not-appear" not in caplog.text


class TestTheRegisteredCallableRunsTheWholeJob:
    def test_the_scheduler_firing_the_slot_writes_the_rows_for_the_stored_symphonies(
        self, monkeypatch, worker_threads
    ):
        entry = captured_entry("Alpha Test Symphony", _sip.holdings_of(("AAA", 1.0)))
        database.save_state({ID_ONE: entry})
        monkeypatch.setattr(synthetic_history, "ALPACA_KEY", KEY_SENTINEL)
        monkeypatch.setattr(synthetic_history, "ALPACA_SECRET", SECRET_SENTINEL)
        monkeypatch.setattr(synthetic_history, "ALPACA_BASE_URL", "https://data.alpaca.markets/v2")
        monkeypatch.setattr(time, "sleep", lambda _s: None)
        monkeypatch.setattr(requests, "get", _sip.AlpacaStub({"AAA": golden_closes("trending")}))
        real_run = regime_label_job.run_regime_label_job
        monkeypatch.setattr(
            regime_label_job,
            "run_regime_label_job",
            lambda load, **_kw: real_run(load, clock=lambda: _sip.JOB_START),
        )

        tick_fn = getattr(app_module, "_run_regime_label_tick", None)
        assert tick_fn is not None, "app._run_regime_label_tick is missing"

        def _before(tick, jobs):
            _set_due(jobs, {tick_fn: 5})

        _run_loop(monkeypatch, ticks=1, before_tick=_before)
        for thread in worker_threads:
            thread.join(timeout=30)

        assert worker_threads, "the scheduler pass started no regime label worker"
        conn = sqlite3.connect(database._db_file())
        try:
            rows = conn.execute(
                "SELECT symphony_id, label, as_of_date FROM regime_label_cache"
            ).fetchall()
        finally:
            conn.close()
        assert rows == [(ID_ONE, golden_label("trending"), _sip.TARGET_D.isoformat())]


class TestTheSlotNeverDelaysTheMinuteJobs:
    def test_with_the_job_held_the_engine_spawn_and_account_refresh_still_fire_every_minute(
        self,
        monkeypatch,
        stub_engine,
        join_engine_threads,
        worker_threads,
    ):
        tick_fn = getattr(app_module, "_run_regime_label_tick", None)
        assert tick_fn is not None, "app._run_regime_label_tick is missing"
        entered, release = threading.Event(), threading.Event()

        def held_job(load_symphonies, **_kwargs):
            entered.set()
            release.wait(timeout=10)

        monkeypatch.setattr(regime_label_job, "run_regime_label_job", held_job)
        refreshes: list[str] = []
        monkeypatch.setattr(
            app_module, "_start_account_refresh", lambda *args: refreshes.append("start")
        )

        def _before(tick, jobs):
            join_engine_threads()
            _set_due(jobs, {tick_fn: 5} if tick == 1 else {})
            for job in jobs:
                if job.unit == "minutes":
                    job.next_run = dt.datetime.now() - dt.timedelta(seconds=1)

        try:
            began = time.perf_counter()
            _run_loop(monkeypatch, ticks=3, before_tick=_before)
            elapsed = time.perf_counter() - began
            assert entered.wait(timeout=5), "the slot never started the job"
            join_engine_threads()

            assert elapsed < 2.0, f"three :00 passes took {elapsed:.1f} s with the job held"
            assert stub_engine.starts == 3, "every :00 pass must spawn the engine"
            assert len(refreshes) == 3, "every :00 pass must start the account refresh"
            (thread,) = worker_threads
            assert thread.is_alive(), "the job must have been held across all three passes"
        finally:
            release.set()
            for thread in worker_threads:
                thread.join(timeout=10)


class TestTheSlotTimeIsOffHoursInEveryHostZone:
    @pytest.mark.parametrize("host_zone", ["UTC", "America/New_York"])
    @pytest.mark.parametrize(
        "day", [dt.date(2026, 1, 15), dt.date(2026, 7, 15)], ids=["winter-EST", "summer-EDT"]
    )
    def test_the_registered_time_maps_outside_the_session_in_eastern_time(
        self, monkeypatch, host_zone, day
    ):
        scheduler, _ = _run_loop(monkeypatch, ticks=1)
        tick_fn = getattr(app_module, "_run_regime_label_tick", None)
        matching = [j for j in scheduler.jobs if _func_of(j) is tick_fn and tick_fn is not None]
        assert len(matching) == 1, "the 04:30 slot must be registered exactly once"
        at_time = matching[0].at_time

        eastern = dt.datetime.combine(day, at_time, tzinfo=ZoneInfo(host_zone)).astimezone(ET)

        assert not (SESSION_OPEN <= eastern.time() <= SESSION_CLOSE), (
            f"slot at {at_time} host {host_zone} falls at {eastern} ET, inside the session"
        )
