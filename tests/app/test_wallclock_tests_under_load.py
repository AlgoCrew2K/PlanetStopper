"""Two daemon timing tests keep their verdict while every logical core is busy.

The hung-run test (`test_engine_run_bound`) and the Composer-held test (`test_scheduler_survival`)
once judged the daemon by how fast the machine ran: the first ticked its virtual clock without
waiting for the engine thread, the second asserted an elapsed number of seconds. Each now
decides from the order of events. This module is the proof that holds on a loaded machine: it
runs each test 20 times in a row while one busy process per logical core keeps every core
occupied.

Marker ``slow``: the default run and CI deselect it, so it runs on request (``-n0 -m slow``).
The busy processes are bare loops with no import beyond the standard library; each ends on its own
at a wall-clock deadline, and the fixture kills them in ``finally``, so an interrupted run cannot
leave them spinning.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from tests.app import test_engine_run_bound as _run_bound
from tests.app.test_engine_run_bound import _end_every_fake_process_and_thread  # noqa: F401
from tests.app.test_scheduler_survival import TestSlotsThatRaise as _SchedulerTests
from tests.app.test_scheduler_survival import (
    _no_network_and_no_leftover_threads,  # noqa: F401
)

pytestmark = pytest.mark.slow

RUNS_PER_TEST = 20
# The child ends itself after this many seconds, whatever happens to this process (seconds).
LOAD_DEADLINE_S = 1800

_BUSY_LOOP = (
    "import time\n"
    f"end = time.monotonic() + {LOAD_DEADLINE_S}\n"
    "n = 0\n"
    "while True:\n"
    "    n += 1\n"
    "    if n % 1000000 == 0 and time.monotonic() > end:\n"
    "        break\n"
)


@pytest.fixture(scope="module")
def busy_cores():
    """One busy process per logical core, killed when the module is done."""
    count = os.cpu_count() or 1
    processes = [
        subprocess.Popen(
            [sys.executable, "-I", "-S", "-c", _BUSY_LOOP],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(count)
    ]
    try:
        yield processes
    finally:
        for process in processes:
            process.kill()
        for process in processes:
            process.wait(timeout=30)


def test_the_load_is_one_busy_process_per_logical_core_and_all_are_alive(busy_cores):
    assert len(busy_cores) == (os.cpu_count() or 1)
    assert all(process.poll() is None for process in busy_cores), "a busy process ended early"


@pytest.mark.parametrize("run", range(RUNS_PER_TEST))
def test_the_hung_run_verdict_holds_with_every_core_busy(
    run, busy_cores, monkeypatch, tmp_path, caplog
):
    _run_bound.test_a_run_hung_in_the_session_is_ended_after_600_seconds_and_the_next_minute_spawns(
        monkeypatch, tmp_path, caplog
    )


@pytest.mark.parametrize("run", range(RUNS_PER_TEST))
def test_the_composer_held_verdict_holds_with_every_core_busy(
    run, busy_cores, monkeypatch, stub_engine, composer_total_stats, join_engine_threads
):
    _SchedulerTests().test_composer_held_for_the_whole_run_does_not_delay_either_engine_spawn(
        monkeypatch, stub_engine, composer_total_stats, join_engine_threads
    )
