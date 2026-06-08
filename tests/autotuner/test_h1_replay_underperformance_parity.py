"""
H1 — the autotuner replay tracks the corrected MC breakdown gate (parity oracle)
and the ARM-band / DISARM behavior is preserved by the rename.

WHY THIS SUITE EXISTS
---------------------
``_replay_exit_tick`` is the SINGLE per-tick exit core the autotuner walk-forward
optimizes against. If the replay's exit decision diverges from the production
exit path, the autotuner selects a different system than the one trading. H1
changes the live trailing-stop gate (fire on confirmed underperformance instead
of suppressing it); the replay MUST move in lockstep.

The replay already delegates the trailing-stop / TP decision to the SAME
math_engine primitives (compute_exit_confirmation, compute_tp_confirmation), so a
value-preserving rename + the single gate-operator flip propagates automatically
— but ONLY if the rename is complete and the replay passes the renamed value. We
pin that here by driving _replay_exit_tick directly on a crash scenario and on an
arm/disarm scenario.

WHAT IS PINNED
--------------
1. PARITY: on a sustained idiosyncratic crash with a confirmed-high MC reading
   (mc >= MC_BREAKDOWN_THRESHOLD), the replay fires the trailing stop — the same
   decision the production gate now makes. (RED pre-fix: the inverted gate
   suppressed it in the replay too.)
2. ARM BAND unchanged: the replay arms when the MC reading is in the
   [TAKE_PROFIT_MC_PCT, TRIGGER_THRESHOLD_PCT) band — rename-only, behavior
   preserved.
3. DISARM unchanged: the replay disarms ("Conditions Recovered") when the MC
   reading exceeds 2x TRIGGER_THRESHOLD_PCT AND the return is positive —
   rename-only, behavior preserved.

These exercise the REAL autotuner._replay_exit_tick (no mocking of math_engine).

TOLERANCE POLICY
----------------
The exit decision (reason string / None) and the boolean arm/disarm state are
discrete categorical outcomes — exact equality applies, no float tolerance.
"""

from __future__ import annotations

import pytest

import autotuner
import math_engine


# Replay state keys (from _replay_exit_tick): a mutable per-position dict carried
# across ticks within a day. Initialized to the day-start defaults the replay
# uses.
def _fresh_state(*, armed: bool = False) -> dict:
    return {
        "hwm": 0.0,
        "prev_return": None,
        "para_armed": False,
        "armed": armed,
        "below_stop_count": 0,
        "hwm_hold_ticks": 0,
        "breakeven_locked": False,
        "tp_armed": False,
        "above_tp_count": 0,
        "vwap_ticks": 0,
        "vwap_bleed_ticks": 0,
    }


_PARAMS = {
    "TRIGGER_THRESHOLD_PCT": 15.0,
    "TAKE_PROFIT_MC_PCT": 5.0,
    "VWAP_CROSS_HWM_PCT": 99.0,   # VWAP never arms
    "VWAP_BLEED_MULTIPLIER": 1.5,
    "VWAP_BLEED_TICKS": 999,      # bleed never confirms
    "PARABOLIC_VELOCITY_THRESHOLD": 99.0,
    "MAX_PARABOLIC_SQUEEZE": 0.5,
}


def _tick(ret: float, mc: float | None, *, time: str = "12:00") -> dict:
    """A replay tick. valid_vwap_weight 0 so the VWAP gate never evaluates;
    vwap_diff 0 keeps System A/B inert; mc_prob is the underperformance metric."""
    return {
        "time": time,
        "return": ret,
        "mc_prob": mc,
        "vol": 0.5,
        "vwap_diff": 0.0,
        "base_atr_pct": 0.5,
        "valid_vwap_weight": 0.0,
    }


def test_replay_fires_trailing_stop_on_confirmed_crash() -> None:
    """
    PARITY: drive _replay_exit_tick over an arm tick then a sustained -50% crash
    with mc held at 100 (confirmed underperformance, >= MC_BREAKDOWN_THRESHOLD).
    After EXIT_CONFIRM_TICKS qualifying ticks the replay must return the
    trailing-stop exit reason — the SAME decision the production gate now makes.

    RED pre-fix: _replay_exit_tick passes the value as prob_beating into the
    inverted gate (100 < 60 -> suppress), so no exit reason ever fires.
    POST-FIX: prob_underperforming=100 >= 60 -> the qualifying ticks confirm and
    the replay fires.
    """
    state = _fresh_state(armed=True)
    ticks = [_tick(-50.0, 100.0) for _ in range(6)]
    n = len(ticks)
    fired = None
    for idx, tk in enumerate(ticks):
        reason = autotuner._replay_exit_tick(
            state, tk, idx, n, _PARAMS, grace_minutes=0
        )
        if reason:
            fired = (idx, reason)
            break
    assert fired is not None, (
        "PARITY BROKEN: the replay never fired the trailing stop on a sustained "
        "confirmed crash (mc=100 >= MC_BREAKDOWN_THRESHOLD=60, return -50%). The "
        "replay must track the corrected production gate (fire on confirmed "
        "underperformance). Pre-fix the inverted gate suppressed it."
    )


def test_replay_suppresses_trailing_stop_on_low_underperformance() -> None:
    """
    PARITY (complement): a sustained drop with a LOW MC reading (mc=10, well
    below MC_BREAKDOWN_THRESHOLD) means the analog pool says today is normal —
    the replay must NOT fire the trailing stop (no hair-trigger), matching the
    production gate's suppression.

    RED pre-fix: _replay_exit_tick has no path for prob_underperforming; the
    rename is incomplete. POST-FIX: mc=10 < 60 -> mc_breakdown_ok False ->
    suppressed.
    """
    state = _fresh_state(armed=True)
    ticks = [_tick(-50.0, 10.0) for _ in range(6)]
    n = len(ticks)
    fired = None
    for idx, tk in enumerate(ticks):
        reason = autotuner._replay_exit_tick(
            state, tk, idx, n, _PARAMS, grace_minutes=0
        )
        if reason:
            fired = (idx, reason)
            break
    assert fired is None, (
        f"A low MC reading (mc=10 < MC_BREAKDOWN_THRESHOLD=60) must SUPPRESS the "
        f"trailing stop even on a magnitude breach — the analog pool says this "
        f"is normal. The replay fired anyway: {fired}."
    )


def test_replay_arm_band_behavior_unchanged() -> None:
    """
    ARM BAND (rename-only): the replay arms when the MC reading is in
    [TAKE_PROFIT_MC_PCT, TRIGGER_THRESHOLD_PCT) = [5, 15). Drive one tick with
    mc=10 (in band) on an unarmed state; the state must arm. Behavior identical
    to pre-rename.

    RED pre-fix: the rename is incomplete (the replay's arm branch / the value
    name). The arm logic itself is unchanged — same operator, same band.
    """
    state = _fresh_state(armed=False)
    autotuner._replay_exit_tick(state, _tick(2.0, 10.0), 0, 3, _PARAMS, grace_minutes=0)
    assert state["armed"] is True, (
        "ARM BAND changed: mc=10 in [5,15) must arm the position; the rename "
        "must preserve this (same band, same operator)."
    )


def test_replay_does_not_arm_outside_band() -> None:
    """
    ARM BAND boundary (rename-only): mc=50 is ABOVE the arm band's upper edge
    (TRIGGER_THRESHOLD_PCT=15), so an unarmed position must NOT arm via the MC
    band path. (50 is "badly underperforming" — the disarm/stop machinery, not
    the arm band, handles it.) Pins that the rename did not widen the band.
    """
    state = _fresh_state(armed=False)
    autotuner._replay_exit_tick(state, _tick(2.0, 50.0), 0, 3, _PARAMS, grace_minutes=0)
    assert state["armed"] is False, (
        "mc=50 is above the arm band [5,15) and must NOT arm via the MC band; "
        "the rename must not change the band edges."
    )


def test_replay_disarm_behavior_unchanged() -> None:
    """
    DISARM (rename-only): the replay disarms ("Conditions Recovered") when the
    MC reading exceeds 2x TRIGGER_THRESHOLD_PCT (= 30) AND the return is
    positive. Drive an already-armed state with mc=40, return +2% -> must
    disarm and reset below_stop_count. Behavior identical to pre-rename.

    RED pre-fix: the rename is incomplete. The disarm operator/threshold are
    unchanged — this pins behavior preservation.
    """
    state = _fresh_state(armed=True)
    state["below_stop_count"] = 2  # should reset on disarm
    autotuner._replay_exit_tick(state, _tick(2.0, 40.0), 0, 3, _PARAMS, grace_minutes=0)
    assert state["armed"] is False, (
        "DISARM changed: mc=40 (> 2x TRIGGER_THRESHOLD_PCT=30) with a positive "
        "return must disarm the position ('Conditions Recovered'); the rename "
        "must preserve this."
    )
    assert state["below_stop_count"] == 0, (
        f"DISARM must reset below_stop_count to 0; got {state['below_stop_count']}."
    )


def test_replay_does_not_disarm_when_return_non_positive() -> None:
    """
    DISARM guard (rename-only): the disarm requires return > 0. With mc=40 (high)
    but a NEGATIVE return, the armed position must STAY armed — recovery is not
    confirmed while still down. Pins that the rename preserved the `and ret > 0`
    guard.
    """
    state = _fresh_state(armed=True)
    autotuner._replay_exit_tick(state, _tick(-2.0, 40.0), 0, 3, _PARAMS, grace_minutes=0)
    assert state["armed"] is True, (
        "A high MC reading with a NEGATIVE return must NOT disarm (recovery "
        "unconfirmed while down); the rename must preserve the `ret > 0` guard."
    )
