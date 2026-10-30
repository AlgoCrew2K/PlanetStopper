"""One review run at a time: the in-process run state and its owner token.

Module under test: advisors.tenure_review.REVIEW_RUN_STATE (every test builds a fresh state
of the same class, so no test depends on, or changes, the process-wide one).

Contract pinned here (the implementer reads this file, not the plan):

* ``try_start(trigger)`` returns an owner token (any truthy, hashable value, different for
  every start) when the slot was free, and a falsy value when a run is in progress.
* ``finish_success(token)``, ``finish_failure(token, reason, at)`` and ``release(token)`` act
  only when ``token`` is the current owner's token. A call with any other token (another run's,
  an earlier run's, ``None``, a made-up value) or on an idle state changes nothing: it neither
  frees the slot, nor records a failed attempt, nor clears one.
* ``finish_success`` frees the slot and forgets the last failed attempt; ``finish_failure``
  frees the slot and remembers ``reason`` and ``at``; ``release`` frees the slot and keeps the
  last failed attempt as it was (the path of a thread or executor that could not be started).
* ``snapshot()`` returns a fresh dict with exactly ``running``, ``running_since``,
  ``trigger`` and ``last_failed_attempt`` (``{"at", "reason"}`` or ``None``); the owner token
  is never part of it, because the snapshot is served to the browser.
"""

from __future__ import annotations

import json
import sys
import threading
from datetime import datetime

import pytest

import advisors.tenure_review as tr
from tests.advisors import _tenure_review_builders as b

FAILED_AT = b.et(2026, 12, 31, 3, 50)
OTHER_FAILED_AT = b.et(2026, 12, 30, 3, 50)
REASON = "Composer statistics unavailable"


@pytest.fixture()
def state():
    return type(tr.REVIEW_RUN_STATE)()


def _idle(snap: dict) -> bool:
    return snap["running"] is False and snap["running_since"] is None and snap["trigger"] is None


def test_an_idle_state_reports_no_run_and_no_failed_attempt(state):
    snap = state.snapshot()
    assert _idle(snap)
    assert snap["last_failed_attempt"] is None


def test_the_snapshot_has_exactly_the_four_keys_the_surfaces_read(state):
    assert set(state.snapshot()) == {"running", "running_since", "trigger", "last_failed_attempt"}
    assert state.try_start("nightly")
    assert set(state.snapshot()) == {"running", "running_since", "trigger", "last_failed_attempt"}


def test_the_first_start_wins_and_a_second_is_refused_while_the_first_runs(state):
    first = state.try_start("nightly")
    assert first
    assert not state.try_start("on_demand")
    assert not state.try_start("nightly")


def test_a_running_state_reports_who_started_it_and_when_and_the_loser_does_not_overwrite(state):
    assert state.try_start("on_demand")
    assert not state.try_start("nightly")
    snap = state.snapshot()
    assert snap["running"] is True
    assert snap["trigger"] == "on_demand"
    assert isinstance(snap["running_since"], str)
    assert datetime.fromisoformat(snap["running_since"]) is not None


@pytest.mark.parametrize("trigger", ["nightly", "on_demand"])
def test_both_triggers_are_recorded_as_given(state, trigger):
    assert state.try_start(trigger)
    assert state.snapshot()["trigger"] == trigger


def test_each_start_gets_its_own_token(state):
    first = state.try_start("nightly")
    state.finish_success(first)
    second = state.try_start("on_demand")
    assert first and second
    assert first != second


def test_the_token_is_not_in_the_snapshot_that_is_served_to_the_browser(state):
    token = state.try_start("nightly")
    assert str(token) not in json.dumps(state.snapshot(), default=str)
    state.finish_failure(token, REASON, FAILED_AT)
    assert str(token) not in json.dumps(state.snapshot(), default=str)


def test_finishing_successfully_with_the_owner_token_frees_the_state_for_the_next_run(state):
    token = state.try_start("nightly")
    state.finish_success(token)
    assert _idle(state.snapshot())
    assert state.try_start("on_demand")


def test_a_failed_run_frees_the_state_and_records_when_and_why(state):
    token = state.try_start("nightly")
    state.finish_failure(token, REASON, FAILED_AT)
    snap = state.snapshot()
    assert snap["running"] is False
    assert snap["last_failed_attempt"]["reason"] == REASON
    assert datetime.fromisoformat(snap["last_failed_attempt"]["at"]) == FAILED_AT
    assert state.try_start("on_demand")


def test_a_later_successful_run_clears_the_failed_attempt_notice(state):
    state.finish_failure(state.try_start("nightly"), REASON, FAILED_AT)
    state.finish_success(state.try_start("on_demand"))
    assert state.snapshot()["last_failed_attempt"] is None


def test_releasing_with_the_owner_token_frees_the_state_and_keeps_the_last_failed_attempt(state):
    """The path for a thread or executor that could not be started: not a review attempt,
    so it neither records a failure nor erases an earlier one."""
    state.finish_failure(state.try_start("nightly"), REASON, OTHER_FAILED_AT)
    token = state.try_start("on_demand")
    state.release(token)
    snap = state.snapshot()
    assert _idle(snap)
    assert snap["last_failed_attempt"]["reason"] == REASON
    assert datetime.fromisoformat(snap["last_failed_attempt"]["at"]) == OTHER_FAILED_AT
    assert state.try_start("nightly")


# ---------------------------------------------------------------------------
# Only the owner's token finishes a run.
# ---------------------------------------------------------------------------

WRONG_TOKENS = [None, "", 0, "not-a-token", object()]


@pytest.mark.parametrize("finish", ["success", "failure", "release"])
@pytest.mark.parametrize("wrong", range(len(WRONG_TOKENS)))
def test_a_finish_with_a_token_that_is_not_the_owners_changes_nothing(state, finish, wrong):
    """Rejects a finish that frees the slot (or records a failure) for whoever calls it."""
    token = state.try_start("on_demand")
    before = state.snapshot()
    bad = WRONG_TOKENS[wrong]
    if finish == "success":
        state.finish_success(bad)
    elif finish == "failure":
        state.finish_failure(bad, "someone else's failure", FAILED_AT)
    else:
        state.release(bad)
    assert state.snapshot() == before
    assert not state.try_start("nightly"), "the owner's run must still hold the slot"
    state.finish_success(token)
    assert _idle(state.snapshot())


@pytest.mark.parametrize("finish", ["success", "failure", "release"])
def test_a_finish_on_an_idle_state_changes_nothing_and_keeps_a_failed_attempt(state, finish):
    """A ``finally`` block may finish twice. Rejects a bare lock release that raises, and a
    finish that erases or overwrites the remembered failure."""
    stale = state.try_start("nightly")
    state.finish_failure(stale, REASON, FAILED_AT)
    before = state.snapshot()
    if finish == "success":
        state.finish_success(stale)
    elif finish == "failure":
        state.finish_failure(stale, "a second, later failure", OTHER_FAILED_AT)
    else:
        state.release(stale)
    assert state.snapshot() == before
    assert state.try_start("on_demand")


@pytest.mark.parametrize("finish", ["success", "failure", "release"])
def test_an_earlier_owners_late_finish_does_not_end_the_run_that_replaced_it(state, finish):
    """A released start whose thread later runs its own cleanup must not free the next run's
    slot or paint a failure on it."""
    stale = state.try_start("nightly")
    state.release(stale)
    current = state.try_start("on_demand")
    assert current
    before = state.snapshot()
    if finish == "success":
        state.finish_success(stale)
    elif finish == "failure":
        state.finish_failure(stale, "stale owner failure", FAILED_AT)
    else:
        state.release(stale)
    assert state.snapshot() == before
    assert state.snapshot()["trigger"] == "on_demand"
    assert not state.try_start("nightly")
    state.finish_success(current)
    assert _idle(state.snapshot())


def test_the_snapshot_is_a_copy_that_cannot_change_the_state(state):
    state.try_start("nightly")
    snap = state.snapshot()
    snap["running"] = False
    snap["trigger"] = "tampered"
    again = state.snapshot()
    assert again["running"] is True
    assert again["trigger"] == "nightly"


def test_exactly_one_of_many_simultaneous_starts_wins_every_time(state):
    """Eight threads released together, repeated with an aggressive thread-switch interval.
    Rejects a check-then-set that is not one atomic step."""
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        for _ in range(150):
            barrier = threading.Barrier(8)
            outcomes: list = []

            def contender(barrier=barrier, outcomes=outcomes):
                barrier.wait()
                outcomes.append(state.try_start("on_demand"))

            threads = [threading.Thread(target=contender) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            winners = [o for o in outcomes if o]
            assert len(winners) == 1, outcomes
            state.finish_success(winners[0])
    finally:
        sys.setswitchinterval(previous)
