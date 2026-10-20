"""One review run at a time: the in-process run state.

Module under test: advisors.tenure_review.REVIEW_RUN_STATE.

The state is module-level and shared, so every test brings it to a known idle state
first and restores that state afterwards, inside the test body (release() on an idle
state is required to be a no-op, which is itself pinned below).
"""

from __future__ import annotations

import sys
import threading
from contextlib import contextmanager
from datetime import datetime

import advisors.tenure_review as tr
from tests.advisors import _tenure_review_builders as b


def _reset(state):
    state.release()
    if state.try_start("nightly"):
        state.finish_success()


@contextmanager
def idle_state():
    state = tr.REVIEW_RUN_STATE
    _reset(state)
    try:
        yield state
    finally:
        _reset(state)


def test_an_idle_state_reports_no_run_and_no_failed_attempt():
    with idle_state() as state:
        snap = state.snapshot()
        assert snap["running"] is False
        assert snap["running_since"] is None
        assert snap["trigger"] is None
        assert snap["last_failed_attempt"] is None


def test_the_first_start_wins_and_a_second_is_refused_while_the_first_runs():
    with idle_state() as state:
        assert state.try_start("nightly") is True
        assert state.try_start("on_demand") is False
        assert state.try_start("nightly") is False


def test_a_running_state_reports_who_started_it_and_when_and_the_loser_does_not_overwrite():
    with idle_state() as state:
        assert state.try_start("on_demand") is True
        assert state.try_start("nightly") is False
        snap = state.snapshot()
        assert snap["running"] is True
        assert snap["trigger"] == "on_demand"
        assert isinstance(snap["running_since"], str)
        assert datetime.fromisoformat(snap["running_since"]) is not None


def test_both_triggers_are_recorded_as_given():
    for trigger in ("nightly", "on_demand"):
        with idle_state() as state:
            assert state.try_start(trigger) is True
            assert state.snapshot()["trigger"] == trigger


def test_finishing_successfully_frees_the_state_for_the_next_run():
    with idle_state() as state:
        assert state.try_start("nightly") is True
        state.finish_success()
        snap = state.snapshot()
        assert snap["running"] is False
        assert snap["running_since"] is None
        assert snap["trigger"] is None
        assert state.try_start("on_demand") is True


def test_a_failed_run_frees_the_state_and_records_when_and_why():
    with idle_state() as state:
        assert state.try_start("nightly") is True
        failed_at = b.et(2026, 12, 31, 3, 50)
        state.finish_failure("Composer statistics unavailable", failed_at)
        snap = state.snapshot()
        assert snap["running"] is False
        assert snap["last_failed_attempt"]["reason"] == "Composer statistics unavailable"
        assert datetime.fromisoformat(snap["last_failed_attempt"]["at"]) == failed_at
        assert state.try_start("on_demand") is True  # the lock was released


def test_a_later_successful_run_clears_the_failed_attempt_notice():
    with idle_state() as state:
        state.try_start("nightly")
        state.finish_failure("Composer statistics unavailable", b.et(2026, 12, 31, 3, 50))
        state.try_start("on_demand")
        state.finish_success()
        assert state.snapshot()["last_failed_attempt"] is None


def test_releasing_without_finishing_frees_the_state_and_keeps_the_last_failed_attempt():
    """The path for a thread or executor that could not be started: not a review
    attempt, so it neither records a failure nor erases an earlier one."""
    with idle_state() as state:
        state.try_start("nightly")
        failed_at = b.et(2026, 12, 30, 3, 50)
        state.finish_failure("Composer statistics unavailable", failed_at)
        assert state.try_start("on_demand") is True
        state.release()
        snap = state.snapshot()
        assert snap["running"] is False
        assert snap["running_since"] is None
        assert snap["last_failed_attempt"]["reason"] == "Composer statistics unavailable"
        assert state.try_start("nightly") is True


def test_finishing_or_releasing_an_idle_state_is_harmless():
    """A finally block may release twice. Rejects a bare lock.release()."""
    with idle_state() as state:
        state.release()
        state.finish_success()
        state.finish_failure("nothing was running", b.et(2026, 12, 31, 3, 50))
        state.release()
        assert state.try_start("nightly") is True


def test_the_snapshot_is_a_copy_that_cannot_change_the_state():
    with idle_state() as state:
        state.try_start("nightly")
        snap = state.snapshot()
        snap["running"] = False
        snap["trigger"] = "tampered"
        again = state.snapshot()
        assert again["running"] is True
        assert again["trigger"] == "nightly"


def test_exactly_one_of_many_simultaneous_starts_wins_every_time():
    """Eight threads released together, repeated with an aggressive thread-switch
    interval. Rejects a check-then-set that is not one atomic step."""
    with idle_state() as state:
        previous = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)
        try:
            for _ in range(150):
                barrier = threading.Barrier(8)
                outcomes: list[bool] = []

                def contender(barrier=barrier, outcomes=outcomes):
                    barrier.wait()
                    outcomes.append(state.try_start("on_demand"))

                threads = [threading.Thread(target=contender) for _ in range(8)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()
                assert outcomes.count(True) == 1, outcomes
                state.finish_success()
        finally:
            sys.setswitchinterval(previous)
