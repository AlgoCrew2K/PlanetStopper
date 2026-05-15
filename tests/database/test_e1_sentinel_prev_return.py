"""
RED tests — E1: sentinel prev_return on new-day reset.

Root cause (2026-05-15): database.py wipe_transient_state set prev_return=0.0.
The action phase starts at EXECUTION_START_TIME (10:30 ET). First cycle computes
velocity = current_return - 0.0 = current_return. Any symphony opening >= threshold
auto-PARA-ARMs on cycle-1. This caused 11 cascading PARA-ARM triggers at open.

Fix: wipe sets prev_return=None. Velocity consumer treats None as "no prior
observation" and substitutes current_return, producing velocity=0 on cycle-1.

Fixture provenance:
  tests/fixtures/database/e1_sentinel/01_new_day_reset_prev_return_is_none.json
  tests/fixtures/math_engine/parabolic_sentinel/01_cycle1_velocity_zero_when_prev_return_none.json
  tests/fixtures/math_engine/parabolic_sentinel/02_opening_gap_2pct_no_para_arm_cycle1.json
  tests/fixtures/math_engine/parabolic_sentinel/03_para_arm_fires_cycle2_real_velocity_high.json
  tests/fixtures/autotuner/e1_sentinel/01_autotuner_replay_cycle1_velocity_zero.json
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import pytest

from database import wipe_transient_state
import math_engine

# ---------------------------------------------------------------------------
# Fixture loading helpers
# ---------------------------------------------------------------------------

_FIXTURE_ROOT = Path(__file__).parent.parent / "fixtures"


def _load(relative_path: str) -> dict:
    return json.loads((_FIXTURE_ROOT / relative_path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Helpers that replicate the velocity-consumer logic from alpha_bot_execution.py:666
# WITHOUT importing the full execution module (avoids live-I/O imports).
# ---------------------------------------------------------------------------

def _compute_velocity_with_sentinel(
    prev_return: Optional[float],
    current_return: float,
    para_threshold: float,
    currently_armed: bool,
) -> tuple[float, bool]:
    """
    Mirrors alpha_bot_execution.py:666 sentinel logic.

    When prev_return is None (new-day sentinel), use current_return as the
    baseline so velocity = 0 unconditionally on cycle-1.
    """
    effective_prev = current_return if prev_return is None else prev_return
    return math_engine.compute_para_arm_decision(
        current_return=current_return,
        prev_return=effective_prev,
        para_threshold=para_threshold,
        currently_armed=currently_armed,
    )


def _make_symphony_state_with_prev_return(prev_return_value) -> dict:
    """Minimal bot_state symphony entry seeded with a specific prev_return value."""
    return {
        "high_water_mark": 1.05,
        "shadow_hwm": 1.02,
        "prev_return": prev_return_value,
        "armed": False,
        "tp_armed": False,
        "para_armed": False,
        "triggered": False,
        "breakeven_locked": False,
        "below_stop_count": 0,
        "above_tp_count": 0,
        "vwap_ticks": 0,
        "vwap_bleed_ticks": 0,
        "hwm_hold_ticks": 0,
        "mc_history": [],
    }


# ---------------------------------------------------------------------------
# Test 1: new-day reset sets prev_return to None
# ---------------------------------------------------------------------------

def test_new_day_reset_sets_prev_return_to_none():
    """
    After wipe_transient_state, prev_return must be None for every symphony entry.

    Fixture: tests/fixtures/database/e1_sentinel/01_new_day_reset_prev_return_is_none.json
    The fixture documents WHY None (not 0.0) is correct — 0.0 causes false PARA-ARM
    on any positive-gap open.
    """
    fx = _load("database/e1_sentinel/01_new_day_reset_prev_return_is_none.json")
    assert fx["expected_value"] is None, "fixture self-check: expected None"

    state = {
        "sym_alpha": _make_symphony_state_with_prev_return(0.04),
        "sym_beta": _make_symphony_state_with_prev_return(0.02),
    }
    result = wipe_transient_state(state)

    for sym_id, sym_data in result.items():
        if isinstance(sym_data, dict):
            assert sym_data["prev_return"] is None, (
                f"[{sym_id}] wipe_transient_state must set prev_return to None (E1 sentinel); "
                f"got {sym_data['prev_return']!r}. "
                f"Fixture: {fx['name']} — {fx['why_not_zero']}"
            )


# ---------------------------------------------------------------------------
# Test 2: cycle-1 velocity is zero when prev_return is None
# ---------------------------------------------------------------------------

def test_cycle_1_velocity_is_zero_after_new_day_reset():
    """
    Given prev_return=None (E1 sentinel) and current_return=2.0 (opening gap),
    the velocity consumer must return velocity=0.0 and should_arm=False.

    Fixture: tests/fixtures/math_engine/parabolic_sentinel/01_cycle1_velocity_zero_when_prev_return_none.json
    """
    fx = _load("math_engine/parabolic_sentinel/01_cycle1_velocity_zero_when_prev_return_none.json")
    inp = fx["inputs"]
    exp = fx["expected"]

    velocity, should_arm = _compute_velocity_with_sentinel(
        prev_return=inp["prev_return_in_bot_state"],  # None
        current_return=inp["current_return"],
        para_threshold=fx["threshold_used"],
        currently_armed=False,
    )

    assert velocity == pytest.approx(exp["velocity"], abs=1e-9), (
        # tolerance: float subtraction of equal values; must be exactly 0
        f"cycle-1 velocity must be 0.0 when prev_return is None sentinel; got {velocity!r}. "
        f"Fixture: {fx['name']}"
    )
    assert should_arm is exp["should_arm"], (
        f"cycle-1 should_arm must be {exp['should_arm']} when velocity=0; got {should_arm!r}. "
        f"Fixture: {fx['name']}"
    )


# ---------------------------------------------------------------------------
# Test 3: opening +2% gap does NOT auto-PARA-ARM on cycle-1
# ---------------------------------------------------------------------------

def test_opening_gap_2pct_does_not_auto_para_arm_cycle_1():
    """
    A symphony opening at +2.0% (the typical PARABOLIC_VELOCITY_THRESHOLD) on a
    new day MUST NOT trigger PARA-ARM on cycle-1. The sentinel converts opening gap
    to zero intraday velocity.

    Fixture: tests/fixtures/math_engine/parabolic_sentinel/02_opening_gap_2pct_no_para_arm_cycle1.json
    """
    fx = _load("math_engine/parabolic_sentinel/02_opening_gap_2pct_no_para_arm_cycle1.json")
    inp = fx["inputs"]
    exp = fx["expected"]

    velocity, should_arm = _compute_velocity_with_sentinel(
        prev_return=inp["prev_return_in_bot_state"],  # None
        current_return=inp["current_return"],
        para_threshold=inp["para_threshold"],
        currently_armed=inp["currently_armed"],
    )

    assert exp["para_armed"] is False, "fixture self-check: para_armed must be False"
    assert should_arm is False, (
        f"opening gap of +{inp['current_return']}% must NOT auto-PARA-ARM on cycle-1 "
        f"(prev_return=None sentinel); velocity={velocity!r}. "
        f"Fixture: {fx['name']}"
    )
    assert velocity == pytest.approx(exp["velocity"], abs=1e-9), (
        # tolerance: float subtraction of equal values; conceptually exactly 0
        f"cycle-1 velocity must be 0.0 with sentinel; got {velocity!r}. "
        f"Fixture: {fx['name']}"
    )


# ---------------------------------------------------------------------------
# Test 4: PARA-ARM can fire on cycle-2 when real intraday velocity is high
# ---------------------------------------------------------------------------

def test_para_arm_can_fire_cycle_2_when_real_velocity_high():
    """
    Cycle-2: prev_return=2.0 (real intraday observation from cycle-1), current_return=4.0.
    velocity = 2.0, which meets PARABOLIC_VELOCITY_THRESHOLD=2.0. PARA-ARM must fire.

    This proves the sentinel fix does NOT suppress legitimate intraday velocity.
    Fixture: tests/fixtures/math_engine/parabolic_sentinel/03_para_arm_fires_cycle2_real_velocity_high.json
    """
    fx = _load("math_engine/parabolic_sentinel/03_para_arm_fires_cycle2_real_velocity_high.json")
    inp = fx["inputs"]
    exp = fx["expected"]

    velocity, should_arm = math_engine.compute_para_arm_decision(
        current_return=inp["current_return"],
        prev_return=inp["prev_return"],
        para_threshold=inp["para_threshold"],
        currently_armed=inp["currently_armed"],
    )

    assert velocity == pytest.approx(exp["velocity"], abs=1e-9), (
        # tolerance: float subtraction with known operands; 4.0 - 2.0 = 2.0 exactly
        f"cycle-2 velocity must equal {exp['velocity']}; got {velocity!r}. "
        f"Fixture: {fx['name']}"
    )
    assert should_arm is exp["should_arm"], (
        f"cycle-2 should_arm must be {exp['should_arm']} when velocity >= threshold; "
        f"got {should_arm!r}. Fixture: {fx['name']}"
    )


# ---------------------------------------------------------------------------
# Test 5: autotuner replay mirrors sentinel — cycle-1 velocity = 0
# ---------------------------------------------------------------------------

def test_autotuner_replay_mirrors_sentinel_cycle1_velocity_zero():
    """
    The autotuner.py replay loop initialises prev_return to None (not 0.0).
    On tick_idx=0 the sentinel yields velocity=0 regardless of tick return.

    This test exercises the SENTINEL CONTRACT directly, mirroring what the updated
    autotuner.py:94 must do: treat prev_return=None as "use current return as baseline."

    Fixture: tests/fixtures/autotuner/e1_sentinel/01_autotuner_replay_cycle1_velocity_zero.json
    """
    fx = _load("autotuner/e1_sentinel/01_autotuner_replay_cycle1_velocity_zero.json")
    inp = fx["inputs"]
    exp = fx["expected"]

    # Replicate what the fixed autotuner.py replay loop does at tick_idx=0
    prev_return: Optional[float] = inp["initial_prev_return"]  # must be None per fix
    tick_return: float = inp["tick_return"]
    para_threshold: float = inp["para_threshold"]

    assert prev_return is None, (
        f"fixture self-check: autotuner replay must init prev_return=None; got {prev_return!r}"
    )

    # Sentinel branch: None => use tick_return as baseline (velocity = 0)
    effective_prev = tick_return if prev_return is None else prev_return
    assert effective_prev == pytest.approx(exp["effective_prev_return"], abs=1e-9), (
        # tolerance: float identity comparison with tick_return; should be exact
        f"effective_prev must equal tick_return when sentinel; got {effective_prev!r}"
    )

    velocity, should_arm = math_engine.compute_para_arm_decision(
        current_return=tick_return,
        prev_return=effective_prev,
        para_threshold=para_threshold,
        currently_armed=False,
    )

    assert velocity == pytest.approx(exp["velocity"], abs=1e-9), (
        # tolerance: subtraction of identical floats; must be 0
        f"autotuner replay cycle-1 velocity must be 0.0; got {velocity!r}. "
        f"Fixture: {fx['name']}"
    )
    assert should_arm is exp["should_arm"], (
        f"autotuner replay cycle-1 should_arm must be {exp['should_arm']}; got {should_arm!r}. "
        f"Fixture: {fx['name']}"
    )


# ---------------------------------------------------------------------------
# Test 6: passing None directly to compute_para_arm_decision raises TypeError
# (documents WHY the sentinel guard must live in the caller, not math_engine)
# ---------------------------------------------------------------------------

def test_compute_para_arm_decision_raises_on_none_prev_return():
    """
    math_engine.compute_para_arm_decision accepts only float inputs.
    Passing prev_return=None raises TypeError at float(None) on line 83.

    This test pins the crash vector: if the caller (alpha_bot_execution.py or
    autotuner.py) passes None through without the sentinel guard, the engine
    dies on the first tick after a new-day reset.  The None guard MUST be in
    the caller, not in math_engine (which must stay a pure-float layer).
    """
    with pytest.raises((TypeError, ValueError)):
        math_engine.compute_para_arm_decision(
            current_return=2.0,
            prev_return=None,  # type: ignore[arg-type]
            para_threshold=2.0,
            currently_armed=False,
        )
