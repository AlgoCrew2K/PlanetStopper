"""
RED tests -- autotuner.collect_if_held_daily_returns (guard-alpha-preconditions
AC-5, amended per PM ruling on recon finding 1).

THE TRAP THIS FILE PINS: the feature plan's Architecture section originally
named autotuner._collect_sim_returns (autotuner.py:1449) as "the exact
accessor" for the primary if-held sample. That function returns
guard_alpha = triggered_return - eod_return -- the STOP'S edge over holding --
and only appends a value on days the stop actually triggered (sparse,
event-filtered; see autotuner.py:390-427's own comment: "the per-day
guard-alpha series"). That is a different quantity from "the if-held return
series" this feature needs, and is not even a continuous series.

collect_if_held_daily_returns must instead read ticks[-1]["return"] for EVERY
date in history_data[sym_id] -- the walk-forward EOD return regardless of
whether the stop fired, i.e. genuinely "if-held". No re-simulation, no I/O:
this is a pure extraction from the SAME history_data structure
_collect_sim_returns already consumes.

test_output_is_not_the_guard_alpha_series is the discriminating adversarial
test: it monkeypatches autotuner._replay_exit_tick to never trigger (so
_collect_sim_returns legitimately returns an EMPTY list -- nothing ever
triggers) and asserts collect_if_held_daily_returns on the SAME history_data
still returns ALL days. An implementation that aliases or wraps
_collect_sim_returns would fail this test with an empty (or near-empty)
result instead of len(history_data[sym_id]).
"""

from __future__ import annotations

import pytest

import autotuner


def _history(sym_id: str, day_returns: dict) -> dict:
    """Build a minimal history_data[sym_id][date] = [ticks...] structure.
    Only the last tick's "return" key is read by the function under test, so
    each day gets a single-tick list -- this mirrors the real shape (a list
    of per-minute ticks) without needing full engine-valid tick fields.
    """
    return {sym_id: {date: [{"return": ret}] for date, ret in day_returns.items()}}


_SYM_ID = "sym-if-held-001"


class TestAllDaysIncludedRegardlessOfTrigger:
    def test_length_equals_total_days_not_triggered_days_only(self):
        day_returns = {
            "2026-01-05": 1.5,
            "2026-01-06": -2.0,
            "2026-01-07": 0.3,
            "2026-01-08": 4.1,
            "2026-01-09": -1.1,
        }
        history_data = _history(_SYM_ID, day_returns)

        result = autotuner.collect_if_held_daily_returns(history_data, _SYM_ID)

        assert len(result) == len(day_returns), (
            f"Expected all {len(day_returns)} days (if-held is unconditional), "
            f"got {len(result)} -- looks trigger-filtered."
        )

    def test_values_are_eod_return_not_guard_alpha(self):
        day_returns = {"2026-01-05": 1.5, "2026-01-06": -2.0, "2026-01-07": 0.3}
        history_data = _history(_SYM_ID, day_returns)

        result = autotuner.collect_if_held_daily_returns(history_data, _SYM_ID)

        assert result == pytest.approx([1.5, -2.0, 0.3]), (
            f"Expected ticks[-1]['return'] per date in sorted order, got {result!r}."
        )

    def test_dates_returned_in_sorted_chronological_order(self):
        day_returns = {"2026-03-01": 9.0, "2026-01-05": 1.0, "2026-02-15": 5.0}
        history_data = _history(_SYM_ID, day_returns)

        result = autotuner.collect_if_held_daily_returns(history_data, _SYM_ID)

        assert result == pytest.approx([1.0, 5.0, 9.0]), (
            f"Expected chronological (sorted-date) order [1.0, 5.0, 9.0], got {result!r}."
        )

    def test_empty_ticks_list_day_is_skipped_not_crashed(self):
        """Mirrors _collect_sim_returns's own 'if not ticks: continue' guard
        (autotuner.py:1489-1490) -- an empty day must not raise IndexError."""
        history_data = {_SYM_ID: {"2026-01-05": [{"return": 1.0}], "2026-01-06": []}}

        result = autotuner.collect_if_held_daily_returns(history_data, _SYM_ID)

        assert result == pytest.approx([1.0])

    def test_symphony_not_in_history_data_returns_empty_not_keyerror(self):
        history_data = {"some-other-symphony": {"2026-01-05": [{"return": 1.0}]}}

        result = autotuner.collect_if_held_daily_returns(history_data, "unknown-sym")

        assert result == [], (
            "A symphony absent from history_data must degrade to an empty list "
            "(mirrors _collect_sim_returns's history_data.get(sym_id, {}) pattern), "
            "never raise KeyError."
        )


class TestOutputIsNotTheGuardAlphaSeries:
    def test_output_differs_from_collect_sim_returns_when_nothing_triggers(self, monkeypatch):
        """THE discriminating adversarial test (see module docstring)."""
        day_returns = {
            "2026-01-05": 1.5,
            "2026-01-06": -2.0,
            "2026-01-07": 0.3,
            "2026-01-08": 4.1,
        }
        history_data = _history(_SYM_ID, day_returns)

        # Force _replay_exit_tick to NEVER trigger on any tick, on any day --
        # so _collect_sim_returns legitimately returns [] (triggered_return
        # stays None for every day, nothing is ever appended).
        monkeypatch.setattr(autotuner, "_replay_exit_tick", lambda *args, **kwargs: None)

        guard_alpha_series = autotuner._collect_sim_returns(
            {}, history_data, [_SYM_ID], "2026-01-09", {}
        )
        if_held_series = autotuner.collect_if_held_daily_returns(history_data, _SYM_ID)

        assert guard_alpha_series == [], (
            "Sanity check on the test setup itself: with _replay_exit_tick "
            f"forced to never trigger, _collect_sim_returns must return an "
            f"empty list, got {guard_alpha_series!r}."
        )
        assert len(if_held_series) == len(day_returns), (
            f"collect_if_held_daily_returns must return all {len(day_returns)} "
            f"days regardless of _collect_sim_returns's (empty, in this "
            f"scenario) trigger-gated output. Got {if_held_series!r} -- if "
            "this is empty too, the implementation is aliasing/wrapping "
            "_collect_sim_returns instead of doing an unconditional extraction."
        )
