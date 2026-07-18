"""
R3-b MA-4 — pure-seam arm/disarm decision battery (AC-1 disarm-on-recovery,
AC-2 hysteresis ladder, AC-7 arm-band / fail-open / boundary / freeze / re-entrancy).

Targets the EXTRACTED pure seam ``math_engine.compute_arm_disarm_decision(...)``,
the single source of arm/disarm truth called by BOTH ``alpha_bot_execution.py``
(production, inline in main()) AND ``autotuner._replay_exit_tick`` (replay) — see
``.claude/tdd-handoff.md`` for the full contract.

WHY (root cause): the live disarm was INVERTED (MA-4) — it disarmed on
DETERIORATION (``prob > 2x trigger AND current_return > 0``) while printing
"Conditions Recovered", stripping the protective stop exactly as
``compute_exit_confirmation``'s exit gate opened (prob >= MC_BREAKDOWN_THRESHOLD).
The fix disarms ONLY on genuine recovery (``prob < TAKE_PROFIT_MC_PCT``, with a
hysteresis ladder) and is prob-based ONLY — the ``return > 0`` leg is DELETED,
which is why ``current_return`` is not a seam parameter.

NON-VACUITY: every test here fails on the current code because the seam does not
exist yet (AttributeError). The behavioral proof that the OLD inverted logic
never exits lives in ``tests/autotuner/test_r3b_giveback_exits_via_replay.py``
(drives the real replay path on base).

TOLERANCE: arm/disarm state (bool) and the ladder count (int) are DISCRETE
categorical outcomes — exact equality, no float tolerance.
"""

from __future__ import annotations

import json
import pathlib

import pytest

import math_engine

_FIXTURE_DIR = (
    pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "math_engine" / "arm_disarm_decision"
)


def _load(name: str) -> dict:
    with (_FIXTURE_DIR / name).open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _run_sequence(fixture: dict) -> None:
    """Thread the fixture's tick sequence through the seam, asserting the
    expected ``(armed, disarm_confirm_count)`` AFTER each call. Each step's output
    state becomes the next step's input — modelling the stateful ladder exactly
    as the production/replay callers do. Keyword args pin the parameter names."""
    c = fixture["constants"]
    armed = fixture["initial"]["armed"]
    count = fixture["initial"]["disarm_confirm_count"]
    for i, step in enumerate(fixture["sequence"]):
        armed, count = math_engine.compute_arm_disarm_decision(
            prob_underperforming=step["prob_underperforming"],
            is_triggered=step["is_triggered"],
            armed=armed,
            disarm_confirm_count=count,
            take_profit_mc_pct=c["take_profit_mc_pct"],
            trigger_threshold_pct=c["trigger_threshold_pct"],
            disarm_confirm_ticks=c["disarm_confirm_ticks"],
        )
        assert armed == step["expect_armed"], (
            f"{fixture['scenario']} step {i} ({step['note']}): "
            f"expected armed={step['expect_armed']}, got {armed}"
        )
        assert count == step["expect_count"], (
            f"{fixture['scenario']} step {i} ({step['note']}): "
            f"expected disarm_confirm_count={step['expect_count']}, got {count}"
        )


# ===========================================================================
# AC-1 — disarm ONLY on recovery; deterioration / breakdown keep the stop armed
# ===========================================================================


def test_deterioration_and_breakdown_keep_stop_armed() -> None:
    """AC-1 core fix: prob >= trigger (incl. >= breakdown 60) is DETERIORATION,
    not recovery -> armed STAYS True. The OLD inverted disarm stripped armed at
    prob > 30 with a positive return; that whole leg is deleted."""
    _run_sequence(_load("deterioration_stays_armed.json"))


def test_recovery_ladder_disarms_after_confirm_ticks() -> None:
    """AC-1/AC-2: prob < TAKE_PROFIT_MC_PCT sustained for disarm_confirm_ticks
    CONSECUTIVE ticks -> disarm, ladder reset to 0."""
    _run_sequence(_load("recovery_ladder_disarms.json"))


@pytest.mark.parametrize("prob", [5.0, 8.0, 14.999, 15.0, 30.0, 30.01, 60.0, 100.0])
def test_single_high_or_inband_tick_never_disarms_armed_position(prob: float) -> None:
    """AC-1 invariant (adversarial): NO single tick with prob >= TAKE_PROFIT_MC_PCT
    (in-band, deteriorating, or breakdown) disarms an armed position — disarm
    requires recovery BELOW the floor. Directly refutes the OLD inverted disarm,
    which fired at prob > 30 (values 30.01, 60.0, 100.0 are its exact domain)."""
    armed, count = math_engine.compute_arm_disarm_decision(
        prob_underperforming=prob,
        is_triggered=False,
        armed=True,
        disarm_confirm_count=0,
        take_profit_mc_pct=5.0,
        trigger_threshold_pct=15.0,
        disarm_confirm_ticks=3,
    )
    assert armed is True, f"prob={prob} (>= floor) wrongly disarmed an armed position"
    assert count == 0, f"prob={prob} is a non-qualifying tick -> ladder must stay 0"


# ===========================================================================
# AC-2 — hysteresis (no chatter): the recovery ladder resets on interruption
# ===========================================================================


def test_interrupted_recovery_resets_ladder() -> None:
    """AC-2: a non-qualifying tick (prob back in-band, >= floor) mid-count resets
    the recovery ladder; a resumed recovery re-counts from 0 (no premature
    disarm / no chatter)."""
    _run_sequence(_load("recovery_interrupted_resets.json"))


def test_mc_absent_mid_recovery_resets_ladder_and_stays_armed() -> None:
    """AC-2: an MC-absent tick (prob=None) mid-recovery is NON-QUALIFYING (an
    absent opinion cannot CONFIRM recovery — mc_available is required in the
    disarm) -> the ladder resets to 0, the position STAYS armed, and a resumed
    recovery re-counts from 0. No special freeze/hold."""
    _run_sequence(_load("mc_absent_mid_recovery_resets.json"))


@pytest.mark.parametrize("confirm_ticks", [1, 2, 3])
def test_recovery_ladder_requires_exactly_n_consecutive_ticks(confirm_ticks: int) -> None:
    """AC-2 ladder mechanics, VALUE-AGNOSTIC: for any disarm_confirm_ticks N the
    disarm fires on exactly the Nth consecutive recovery tick and not before.
    disarm_confirm_ticks is passed EXPLICITLY so this does NOT depend on the
    frozen default value (r3b-engine's [PM-ASSUMED] probe-sized call)."""
    armed = True
    count = 0
    for tick in range(1, confirm_ticks):
        armed, count = math_engine.compute_arm_disarm_decision(
            prob_underperforming=2.0,
            is_triggered=False,
            armed=armed,
            disarm_confirm_count=count,
            take_profit_mc_pct=5.0,
            trigger_threshold_pct=15.0,
            disarm_confirm_ticks=confirm_ticks,
        )
        assert armed is True, f"disarmed prematurely at recovery tick {tick} of {confirm_ticks}"
        assert count == tick, f"ladder count should be {tick} after {tick} recovery ticks"
    armed, count = math_engine.compute_arm_disarm_decision(
        prob_underperforming=2.0,
        is_triggered=False,
        armed=armed,
        disarm_confirm_count=count,
        take_profit_mc_pct=5.0,
        trigger_threshold_pct=15.0,
        disarm_confirm_ticks=confirm_ticks,
    )
    assert armed is False, f"did not disarm on the {confirm_ticks}th consecutive recovery tick"
    assert count == 0, "the ladder must reset to 0 on disarm"


# ===========================================================================
# AC-7 — arm band, fail-open, boundary, triggered freeze, re-entrancy preserved
# ===========================================================================


def test_arm_band_entry_arms_unarmed_position() -> None:
    """AC-7: the arm band [TAKE_PROFIT_MC_PCT, TRIGGER_THRESHOLD_PCT) is preserved
    by the extraction."""
    _run_sequence(_load("arm_band_entry.json"))


def test_mc_absent_fail_open_arms_unarmed_position() -> None:
    """AC-7 / MA-10: an absent MC opinion (prob=None) fail-opens to ARM — the
    protective stop is never left dark. Preserved through the extraction."""
    _run_sequence(_load("fail_open_arm_mc_absent.json"))


def test_unarmed_prob_below_floor_does_not_arm() -> None:
    """AC-7: prob < floor while UNARMED is neither the arm band nor the fail-open
    case (MC is present) -> no arm."""
    _run_sequence(_load("unarmed_below_floor_does_not_arm.json"))


def test_prob_exactly_at_floor_is_not_recovery() -> None:
    """AC-7 boundary: recovery is STRICTLY prob < TAKE_PROFIT_MC_PCT; prob == 5.0
    (exactly the floor) is NOT recovery -> non-qualifying, ladder resets, stays armed."""
    _run_sequence(_load("boundary_prob_at_floor_no_disarm.json"))


def test_triggered_position_freezes_arm_and_ladder() -> None:
    """AC-7: a triggered position is frozen (production guards the whole block on
    not-triggered) -> the seam returns its inputs unchanged, even for a
    recovery-looking prob."""
    _run_sequence(_load("triggered_freezes_state.json"))


def test_arm_band_is_reentrant_after_recovery_disarm() -> None:
    """AC-7 / Edge: after a genuine recovery-disarm the arm band re-arms on
    re-deterioration (the band is re-entrant)."""
    _run_sequence(_load("rearm_after_genuine_recovery.json"))


# ===========================================================================
# Type contract — the seam returns (bool, int); callers write these to state
# and diff prev/new armed for telemetry.
# ===========================================================================


def test_seam_returns_plain_bool_and_int_types() -> None:
    """Contract: ``compute_arm_disarm_decision`` returns ``(bool, int)`` — a real
    bool (not int-for-bool, not a numpy scalar) and a plain int. Callers diff
    prev/new armed with identity semantics for the ARM/DISARM telemetry."""
    new_armed, new_count = math_engine.compute_arm_disarm_decision(
        prob_underperforming=10.0,
        is_triggered=False,
        armed=False,
        disarm_confirm_count=0,
        take_profit_mc_pct=5.0,
        trigger_threshold_pct=15.0,
        disarm_confirm_ticks=3,
    )
    assert isinstance(new_armed, bool), f"new_armed must be a bool, got {type(new_armed)}"
    assert isinstance(new_count, int) and not isinstance(new_count, bool), (
        f"new_disarm_confirm_count must be a plain int, got {type(new_count)}"
    )
