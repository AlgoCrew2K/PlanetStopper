"""
AC-5 (F6 conditional) — the replay's exit tick must not evaluate the action
phase (arm, disarm, trailing-stop confirm, TP confirm, exit firing) before
EXECUTION_START_TIME. This is DISTINCT from and NOT satisfied by the
already-shipped N-3 fix.

What N-3 already fixed (autotuner.py:41-103, test_n3_replay_grace_execution_start_time.py):
ONLY the VWAP-Breakdown / VWAP-Bleed-Cut open-window GRACE SUPPRESSION is
anchored on EXECUTION_START_TIME via _replay_execution_start_time() /
_replay_in_open_window_grace(). `execution_start_hhmm` is already threaded
into every _replay_exit_tick call site (replay_exit_sequence, run_simulation,
_collect_sim_returns, _collect_sim_returns_dated all resolve it via
_replay_execution_start_time() and pass it through) -- so the VALUE reaches
_replay_exit_tick correctly today. The gap is what _replay_exit_tick DOES
with it: today the value is consulted in exactly ONE place (the
_replay_in_open_window_grace call at the VWAP check), nowhere else.

The still-open gap (this file): production's ENTIRE action phase is hard-
gated behind EXECUTION_START_TIME --

    # alpha_bot_execution.py:951-953
    if current_time < market_open and not force_run:
        return

-- where market_open is derived from EXECUTION_START_TIME (default 09:30).
Before that gate, only the DATA PHASE runs (HWM/shadow_hwm tracking,
Composer-field persistence -- alpha_bot_execution.py:876-885). The ACTION
PHASE -- para-arm (prev_return/para_armed are action-phase-only fields,
verified never touched before the gate), MC arm/disarm, trailing-stop
confirm, TP confirm, VWAP checks, and exit firing -- never runs at all
before EXECUTION_START_TIME. armed/below_stop_count/tp_armed/above_tp_count
stay exactly as they were on the previous cycle; no exit can ever fire.

_replay_exit_tick (autotuner.py:1119-1294) evaluates para-arm, MC arm/
disarm, trailing-stop confirm, and TP confirm UNCONDITIONALLY starting at
tick_idx 0 -- the only exec-start-aware code in the whole function is the
VWAP grace call. So the replay CAN arm/confirm/fire on a tick production
could never have evaluated. With the droplet's real EXECUTION_START_TIME
('9:35' -- a phase-2 droplet check, 2026-07-17), this is a 5-tick window
(tick_idx 0-4, 09:30-09:34) where the replay is action-live and production
is not.

Anchor convention (established by N-3, reused here): tick_idx 0 == 09:30 ET
session open (the data-phase loop runs from 09:30 -- alpha_bot_execution.py:
681). start_offset = (h - 9) * 60 + (m - 30) is the minute-bar offset at
which EXECUTION_START_TIME sits past tick_idx 0. Ticks with
tick_idx < start_offset are BEFORE the action phase opens; production's
_replay_exit_tick equivalent must not arm/confirm/fire on them.
"""

from __future__ import annotations

import pytest

import autotuner
import math_engine

_DEFAULT_PARAMS = {
    "TRIGGER_THRESHOLD_PCT": 15.0,
    "TAKE_PROFIT_MC_PCT": 5.0,
    "VWAP_CROSS_HWM_PCT": 99.0,  # effectively disable VWAP for this file's tests
    "VWAP_BLEED_MULTIPLIER": 1.5,
    "VWAP_BLEED_TICKS": 999,
    "PARABOLIC_VELOCITY_THRESHOLD": 99.0,  # effectively disable parabolic squeeze
    "MAX_PARABOLIC_SQUEEZE": 0.5,
}

_EXEC_CONFIRM_TICKS = math_engine.EXIT_CONFIRM_TICKS  # 3


def _start_offset(execution_start_hhmm: str) -> int:
    """Independent re-derivation of N-3's start_offset formula -- used only to
    compute WHICH tick indices this file's fixtures should probe, never to
    assert against a producer-computed value."""
    h, m = execution_start_hhmm.split(":")
    return (int(h) - 9) * 60 + (int(m) - 30)


def _in_band_arm_tick(ret: float = 1.0) -> dict:
    return {
        "time": "tick",
        "return": ret,
        "mc_prob": 10.0,  # in [TAKE_PROFIT_MC_PCT=5, TRIGGER_THRESHOLD_PCT=15)
        "vol": 0.5,
        "vwap_diff": 0.0,
        "base_atr_pct": 0.5,
        "valid_vwap_weight": 0.0,
    }


def _qualifying_stop_tick(ret: float = -20.0, mc: float = 70.0) -> dict:
    """A tick whose magnitude and MC breakdown gate would confirm a trailing
    stop tick IF the confirm-count machinery were live (ret deep below any
    plausible stop level, mc >= MC_BREAKDOWN_THRESHOLD=60)."""
    return {
        "time": "tick",
        "return": ret,
        "mc_prob": mc,
        "vol": 0.5,
        "vwap_diff": 0.0,
        "base_atr_pct": 0.5,
        "valid_vwap_weight": 0.0,
    }


# ===========================================================================
# 1 — Arm is blocked before EXECUTION_START_TIME.
# ===========================================================================


def test_replay_does_not_arm_before_execution_start_time() -> None:
    """RED (pre-fix): _replay_exit_tick has no action-phase gate, so an
    in-band-MC tick arms regardless of tick_idx. With EXECUTION_START_TIME=
    '09:35' (start_offset=5), ticks 0-4 are before the action phase opens --
    production would never evaluate the arm condition on them."""
    exec_start = "09:35"
    offset = _start_offset(exec_start)
    assert offset == 5, "fixture self-check: 09:35 session-open offset must be 5."

    state = autotuner._fresh_replay_state()
    for tick_idx in range(offset):  # 0..4 -- strictly before the action phase
        autotuner._replay_exit_tick(
            state,
            _in_band_arm_tick(),
            tick_idx=tick_idx,
            n_ticks=20,
            p=_DEFAULT_PARAMS,
            grace_minutes=0,
            execution_start_hhmm=exec_start,
        )

    assert state["armed"] is False, (
        f"AC-5 VIOLATED: an in-band-MC tick armed the replay's stop at tick_idx "
        f"< {offset} (before EXECUTION_START_TIME={exec_start!r}). Production's "
        "action phase (alpha_bot_execution.py:951-953) never runs before "
        "EXECUTION_START_TIME -- armed must stay False for every pre-action-phase "
        "tick, regardless of how favorable the MC/return inputs are."
    )


def test_replay_arms_once_execution_start_time_is_reached() -> None:
    """Isolates 'gated before, live at-and-after' from 'never fires at all':
    the SAME in-band tick that must not arm before tick_idx=offset must arm
    starting exactly at tick_idx=offset."""
    exec_start = "09:35"
    offset = _start_offset(exec_start)

    state = autotuner._fresh_replay_state()
    # Drive through the pre-action-phase window first (must stay unarmed).
    for tick_idx in range(offset):
        autotuner._replay_exit_tick(
            state,
            _in_band_arm_tick(),
            tick_idx=tick_idx,
            n_ticks=20,
            p=_DEFAULT_PARAMS,
            grace_minutes=0,
            execution_start_hhmm=exec_start,
        )
    assert state["armed"] is False, "Fixture self-check: must still be unarmed entering tick_idx=offset."

    autotuner._replay_exit_tick(
        state,
        _in_band_arm_tick(),
        tick_idx=offset,
        n_ticks=20,
        p=_DEFAULT_PARAMS,
        grace_minutes=0,
        execution_start_hhmm=exec_start,
    )

    assert state["armed"] is True, (
        f"An in-band-MC tick at tick_idx == {offset} (exactly EXECUTION_START_TIME= "
        f"{exec_start!r}) did not arm. The gate must block STRICTLY BEFORE the "
        "action phase opens, not at or after it -- production evaluates the cycle "
        "at EXECUTION_START_TIME itself (>=, not >)."
    )


# ===========================================================================
# 2 — HWM still advances pre-action-phase; confirm-count does not (mirrors
#     production's data-phase / action-phase split).
# ===========================================================================


def test_replay_confirm_count_does_not_advance_before_execution_start_time() -> None:
    """RED (pre-fix): a magnitude- and MC-qualifying tick increments
    below_stop_count and can fire a trailing stop regardless of tick_idx.
    With EXECUTION_START_TIME='09:35', an already-armed position's
    below_stop_count must NOT advance on ticks 0-4, and no exit can fire --
    production's action phase (which owns the confirm-count ladder) is not
    running yet."""
    exec_start = "09:35"
    offset = _start_offset(exec_start)

    state = autotuner._fresh_replay_state()
    state["armed"] = True  # isolate confirm-count gating from arm gating (test 1)
    state["hwm"] = 0.0  # so the qualifying tick's deep-negative return sets up a real stop breach

    for tick_idx in range(offset):
        reason = autotuner._replay_exit_tick(
            state,
            _qualifying_stop_tick(),
            tick_idx=tick_idx,
            n_ticks=20,
            p=_DEFAULT_PARAMS,
            grace_minutes=0,
            execution_start_hhmm=exec_start,
        )
        assert reason is None, (
            f"AC-5 VIOLATED: an exit fired at tick_idx={tick_idx}, strictly before "
            f"EXECUTION_START_TIME={exec_start!r} (offset={offset}). Production's "
            "action phase cannot fire an exit before this point by construction."
        )

    assert state["below_stop_count"] == 0, (
        f"below_stop_count advanced to {state['below_stop_count']} on pre-action-phase "
        "ticks despite qualifying magnitude/MC conditions. Production's confirm-count "
        "ladder (part of the action phase) never runs before EXECUTION_START_TIME, so "
        "it cannot have advanced either."
    )


def test_replay_hwm_still_advances_before_execution_start_time() -> None:
    """The DATA phase (HWM/shadow_hwm tracking) runs from 09:30 regardless of
    EXECUTION_START_TIME (alpha_bot_execution.py:876-885, before the
    action-phase gate at :951-953) -- only the action phase is gated. Pin
    that the AC-5 fix does not over-gate and freeze HWM tracking too."""
    exec_start = "09:35"
    offset = _start_offset(exec_start)

    state = autotuner._fresh_replay_state()
    assert state["hwm"] == -999.0

    autotuner._replay_exit_tick(
        state,
        _in_band_arm_tick(ret=7.5),
        tick_idx=0,  # strictly before the action phase (offset=5)
        n_ticks=20,
        p=_DEFAULT_PARAMS,
        grace_minutes=0,
        execution_start_hhmm=exec_start,
    )

    assert state["hwm"] == pytest.approx(7.5, abs=1e-9), (
        f"state['hwm'] did not advance on a pre-action-phase tick (tick_idx=0 < "
        f"offset={offset}). Production's HWM tracking is a DATA-phase concern that "
        "runs unconditionally from 09:30 -- only the action phase (arm/confirm/fire) "
        "is gated by EXECUTION_START_TIME. Over-gating HWM would itself be a new "
        "replay/production divergence."
    )


# ===========================================================================
# 3 — Default EXECUTION_START_TIME='09:30' is fully back-compat.
# ===========================================================================


def test_default_execution_start_time_preserves_current_behavior() -> None:
    """With EXECUTION_START_TIME='09:30' (start_offset=0), EVERY tick_idx >= 0
    is already inside the action phase -- the AC-5 fix must be a no-op for
    the default/most-common case. An in-band tick at tick_idx=0 must arm
    exactly as it did before this fix existed."""
    exec_start = "09:30"
    assert _start_offset(exec_start) == 0, "fixture self-check: 09:30 offset must be 0."

    state = autotuner._fresh_replay_state()
    autotuner._replay_exit_tick(
        state,
        _in_band_arm_tick(),
        tick_idx=0,
        n_ticks=20,
        p=_DEFAULT_PARAMS,
        grace_minutes=0,
        execution_start_hhmm=exec_start,
    )
    assert state["armed"] is True, (
        "Regression: with the default EXECUTION_START_TIME='09:30', tick_idx=0 must "
        "still arm on an in-band MC tick -- the AC-5 gate must not block the "
        "default/back-compat case."
    )


# ===========================================================================
# 4 — Droplet-real regression pin ('9:35').
# ===========================================================================


def test_droplet_execution_start_935_blocks_pre_window_exit_and_fires_after() -> None:
    """Regression pin naming the real operator value (droplet
    EXECUTION_START_TIME='9:35', phase-2 check, 2026-07-17): a full
    trailing-stop sequence engineered to confirm within the first
    EXIT_CONFIRM_TICKS ticks must NOT fire during the pre-action-phase
    window, and must fire once the confirm ladder completes AFTER the
    action phase opens."""
    exec_start = "9:35"  # droplet's literal .env value -- unpadded hour
    offset = _start_offset(exec_start)
    assert offset == 5

    state = autotuner._fresh_replay_state()
    state["armed"] = True
    state["hwm"] = 0.0

    fired_at = None
    # Ticks 0..(offset + EXIT_CONFIRM_TICKS) -- enough qualifying ticks both
    # before and after the action-phase boundary to prove the gate, not just
    # the absence of an exit.
    for tick_idx in range(offset + _EXEC_CONFIRM_TICKS + 2):
        reason = autotuner._replay_exit_tick(
            state,
            _qualifying_stop_tick(),
            tick_idx=tick_idx,
            n_ticks=40,
            p=_DEFAULT_PARAMS,
            grace_minutes=0,
            execution_start_hhmm=exec_start,
        )
        if reason is not None:
            fired_at = tick_idx
            break

    assert fired_at is not None, (
        "Trailing stop never fired even after the action phase opened -- fixture "
        "self-check failure (the qualifying-stop tick sequence should confirm "
        f"within EXIT_CONFIRM_TICKS={_EXEC_CONFIRM_TICKS} ticks of the action phase "
        "opening)."
    )
    assert fired_at >= offset, (
        f"Trailing stop fired at tick_idx={fired_at}, before EXECUTION_START_TIME "
        f"offset={offset} (droplet EXECUTION_START_TIME={exec_start!r}). Production "
        "cannot fire an exit before its action phase opens."
    )
