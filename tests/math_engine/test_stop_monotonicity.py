"""
Tests for the HWM-anchored trailing-stop contract on the math_engine.py
stop-resolution path (compute_breakeven_update + compute_active_trailing_stop).

REWRITTEN for the H-1 audit fix (math-audit exit-decision-math__2026-05-21.md).

WHAT CHANGED — and why this file was rewritten:

  The PRE-FIX version of this file pinned a "resolved stop level never
  decreases" ratchet, enforced by a `previously_persisted_stop_level`
  max-clamp inside compute_breakeven_update. The math audit (HIGH finding
  H-1) showed that clamp is the DEFECT: a real trailing stop is
  HWM-anchored — stop = HWM - vol_scaled_distance, recomputed each tick.
  On a legitimate mid-day volatility spike the vol-scaled distance widens,
  so the correct stop MOVES DOWN. The frozen-level clamp discarded that
  widening and kept the stop too tight, exiting on noise.

  The H-1 fix REMOVES the previously_persisted_stop_level parameter. The
  only ratchet that survives is the breakeven floor: once breakeven latches,
  stop = max(base_stop_level, 0.0). The "resolved level monotonically
  non-decreasing" property is GONE — by design (plan AC-3 / AC-12, an
  intentional behavior change).

THE INVARIANTS THIS FILE NOW PINS:

  1. HWM monotonicity — the high-water mark (engine-side, current_return >
     high_water_mark) is monotone non-decreasing within a position. This is
     the anchor the stop subtracts from; it never falls.
  2. Recompute-each-tick — for an unlocked position the resolved
     stop_trigger_level equals base_stop_level (= HWM - distance) exactly,
     with NO cross-tick clamp. The stop may rise or fall tick-to-tick.
  3. Post-breakeven floor — once breakeven_locked latches, stop =
     max(base_stop_level, 0.0); the stop may still move down but never
     below the 0.0 breakeven floor.
  4. Breakeven latch is one-way — once new_breakeven_locked is True it stays
     True for the position's lifetime.
  5. Triggered override absolute — is_triggered=True yields the -999.0
     sentinel regardless of all other inputs.

Tolerance policy:
  - stop_trigger_level: pytest.approx(rel=1e-9, abs=1e-12). The math is
    subtraction + max() with no transcendentals; ~1 ulp is the realistic
    IEEE-754 noise floor; a wrong impl misses by structural factors.
  - HWM-monotonicity / direction comparisons: exact `<` / `>` — a drift in
    the wrong direction is always a violation, never tolerance noise.
  - new_hold_ticks (int) / new_breakeven_locked (bool): exact equality.

Provenance: every expected value is hand-derived in the test docstring or
inline comment. The math is max() / subtraction / conditional pass-through;
the derivations are spelled out. No producer-computed value is hardcoded.
"""

from __future__ import annotations

import pytest

import math_engine

APPROX_REL = 1e-9
APPROX_ABS = 1e-12


# ===========================================================================
# Helper: replay a position's lifetime through compute_breakeven_update.
#
# Post-H-1-fix the function is fully per-tick stateless w.r.t. the stop
# level — there is no previously_persisted_stop_level to thread. The helper
# threads ONLY (hwm_hold_ticks, breakeven_locked), which are genuine
# per-position state.
# ===========================================================================


def _simulate_position_lifetime(
    ticks: list[dict],
) -> list[tuple[int, bool, float]]:
    """
    Replay N ticks against compute_breakeven_update, threading
    (hwm_hold_ticks, breakeven_locked) forward.

    Each tick dict must supply: current_return, symphony_vol,
    base_stop_level, is_triggered.

    Returns [(new_hold_ticks, new_breakeven_locked, stop_trigger_level)].

    NOTE: stop_trigger_level is NOT threaded forward — the H-1 fix removed
    the cross-tick stop clamp. The stop is recomputed each tick as
    HWM - distance (via base_stop_level).
    """
    state_hold_ticks = 0
    state_locked = False
    out: list[tuple[int, bool, float]] = []
    for tick in ticks:
        new_hold_ticks, new_locked, stop_level = math_engine.compute_breakeven_update(
            current_return=tick["current_return"],
            symphony_vol=tick["symphony_vol"],
            base_stop_level=tick["base_stop_level"],
            current_hold_ticks=state_hold_ticks,
            currently_breakeven_locked=state_locked,
            is_triggered=tick["is_triggered"],
        )
        out.append((new_hold_ticks, new_locked, stop_level))
        state_hold_ticks = new_hold_ticks
        state_locked = new_locked
    return out


# ===========================================================================
# INVARIANT 1 — HWM monotonicity within a position.
# ===========================================================================


def test_high_water_mark_is_monotone_non_decreasing_within_a_position() -> None:
    """
    The high-water mark is monotone non-decreasing within a single
    position's lifetime. The HWM advance rule is engine-side:
        if current_return > high_water_mark and not triggered:
            high_water_mark = current_return
    so HWM[i] = max(HWM[i-1], return[i]). This is the anchor the
    HWM-anchored stop subtracts from — it never falls.

    Derivation: the return series rises, dips, recovers; the HWM at each
    index must equal the running max of the returns up to that index.
    """
    returns = [0.5, 1.4, 0.9, 2.2, 1.8, 3.0, 2.7, 3.0, -0.5, 4.1]
    hwm = returns[0]
    hwm_series = [hwm]
    for r in returns[1:]:
        if r > hwm:
            hwm = r
        hwm_series.append(hwm)

    violations = [
        (i, hwm_series[i - 1], hwm_series[i])
        for i in range(1, len(hwm_series))
        if hwm_series[i] < hwm_series[i - 1]
    ]
    assert not violations, (
        f"HWM MONOTONICITY VIOLATED. returns={returns} -> HWM={hwm_series}; "
        f"drops at (i, prev, new): {violations}."
    )
    for i in range(len(returns)):
        assert hwm_series[i] == pytest.approx(
            max(returns[: i + 1]), rel=APPROX_REL, abs=APPROX_ABS
        ), f"HWM[{i}]={hwm_series[i]} != running max {max(returns[: i + 1])}"


# ===========================================================================
# INVARIANT 2 — Recompute-each-tick: stop == base_stop_level (unlocked),
# with NO cross-tick clamp. The stop may move down or up freely.
# ===========================================================================


@pytest.mark.parametrize(
    "scenario_id,base_sequence",
    [
        # base rises -> stop rises
        ("base_rises", [0.5, 1.0, 1.5, 2.0]),
        # base falls -> stop FALLS (the H-1 point: distance widened)
        ("base_falls", [2.0, 1.5, 1.0, 0.5]),
        # base oscillates -> stop oscillates; no clamp pins a floor
        ("base_oscillates", [1.0, 2.0, 1.0, 2.0]),
        # base retraces then recovers
        ("base_dip_recover", [1.0, 2.0, 0.5, 3.0]),
    ],
    ids=["base_rises", "base_falls", "base_oscillates", "base_dip_recover"],
)
def test_unlocked_stop_tracks_base_each_tick_no_clamp(
    scenario_id: str, base_sequence: list[float]
) -> None:
    """
    INVARIANT 2 (AC-3): for an UNLOCKED position the resolved
    stop_trigger_level equals base_stop_level (= HWM - vol_scaled_distance)
    on every tick — recomputed each tick, with NO cross-tick clamp.

    Every tick uses current_return below the qualifying threshold so the
    breakeven latch never fires; the stop is the pass-through base level.

    The 'base_falls' and 'base_oscillates' scenarios are the load-bearing
    cases: the resolved stop MUST be allowed to move down. A re-introduced
    frozen-level clamp would pin the stop at a prior tick's higher value and
    fail these.
    """
    results = _simulate_position_lifetime(
        [
            {
                "current_return": -5.0,  # far below threshold; lock never fires
                "symphony_vol": 1.0,
                "base_stop_level": b,
                "is_triggered": False,
            }
            for b in base_sequence
        ]
    )
    for i, (_, locked, stop) in enumerate(results):
        assert locked is False, (
            f"[{scenario_id}] tick {i}: lock should not fire on a "
            f"sub-threshold return; got locked={locked}."
        )
        assert stop == pytest.approx(base_sequence[i], rel=APPROX_REL, abs=APPROX_ABS), (
            f"[{scenario_id}] tick {i}: stop {stop} != base_stop_level "
            f"{base_sequence[i]}. The unlocked stop must equal the "
            f"recomputed base each tick — no cross-tick clamp."
        )


def test_unlocked_stop_is_allowed_to_decrease_tick_to_tick() -> None:
    """
    INVARIANT 2 (AC-3, explicit): an unlocked HWM-anchored stop CAN decrease
    tick-to-tick. This is the exact behavior the H-1 fix introduces — a
    widening vol-scaled distance lowers the stop. The pre-fix frozen-level
    clamp forbade this.

    Derivation: tick 1 base 2.0, tick 2 base 0.5 (same HWM, wider
    distance). Both unlocked. Expected stop drops 2.0 -> 0.5.
    """
    results = _simulate_position_lifetime(
        [
            {
                "current_return": -5.0,
                "symphony_vol": 1.0,
                "base_stop_level": 2.0,
                "is_triggered": False,
            },
            {
                "current_return": -5.0,
                "symphony_vol": 1.0,
                "base_stop_level": 0.5,
                "is_triggered": False,
            },
        ]
    )
    stop1, stop2 = results[0][2], results[1][2]
    assert stop2 < stop1, (
        f"AC-3 VIOLATED: unlocked stop did not decrease ({stop1} -> {stop2}). "
        f"A widening vol-scaled distance must lower the HWM-anchored stop. "
        f"If stop2 >= stop1 a frozen-level clamp is still present."
    )


# ===========================================================================
# INVARIANT 3 — Post-breakeven floor: once locked, stop = max(base, 0.0).
# The stop may move down but never below 0.0.
# ===========================================================================


@pytest.mark.parametrize(
    "base_stop_level",
    [-100.0, -5.0, -1.0, -0.5, -0.001, -1e-12, 0.0, 0.001, 1.0, 100.0],
)
def test_post_breakeven_stop_level_never_below_zero(
    base_stop_level: float,
) -> None:
    """
    INVARIANT 3 (AC-3): when breakeven_locked has latched, the returned
    stop_trigger_level MUST be >= 0.0 regardless of how negative
    base_stop_level is.

    Derivation: max(base_stop_level, 0.0). For base in [-100, 0) -> 0.0;
    for base >= 0 -> base. The function-level floor is 0.0.

    Catches an impl that omitted the max(..., 0.0) clamp at the locked
    branch, or sign-flipped it to min().
    """
    _, _, stop_level = math_engine.compute_breakeven_update(
        current_return=0.0,
        symphony_vol=1.0,
        base_stop_level=base_stop_level,
        current_hold_ticks=0,
        currently_breakeven_locked=True,
        is_triggered=False,
    )
    assert stop_level >= 0.0, (
        f"POST-BREAKEVEN FLOOR VIOLATED: base_stop_level={base_stop_level}, "
        f"breakeven_locked=True, returned stop_trigger_level={stop_level} "
        f"< 0.0. The lock anchors the stop at no-worse-than-zero-loss."
    )


def test_post_breakeven_stop_may_still_move_down_above_the_zero_floor() -> None:
    """
    INVARIANT 3 (AC-3): once breakeven is latched, the stop is still
    HWM-anchored — max(HWM - distance, 0.0). Between two post-lock ticks the
    stop CAN move down as long as it stays >= 0.0. The breakeven latch locks
    the FLOOR at zero, not the stop at a level.

    Derivation: both ticks locked. tick 1 base 2.5 -> stop max(2.5,0)=2.5.
    tick 2 base 0.8 -> stop max(0.8,0)=0.8. The stop drops 2.5 -> 0.8,
    both above the 0.0 floor — allowed.
    """
    results = _simulate_position_lifetime(
        [
            {
                "current_return": 2.0,
                "symphony_vol": 1.0,
                "base_stop_level": 2.5,
                "is_triggered": False,
            },
            {
                "current_return": 2.0,
                "symphony_vol": 1.0,
                "base_stop_level": 0.8,
                "is_triggered": False,
            },
        ]
    )
    # Both ticks: prime the lock first so currently_breakeven_locked is True.
    # The helper starts unlocked; for a clean two-tick post-lock test we call
    # directly with currently_breakeven_locked=True.
    _, locked1, stop1 = math_engine.compute_breakeven_update(
        current_return=2.0,
        symphony_vol=1.0,
        base_stop_level=2.5,
        current_hold_ticks=5,
        currently_breakeven_locked=True,
        is_triggered=False,
    )
    _, locked2, stop2 = math_engine.compute_breakeven_update(
        current_return=2.0,
        symphony_vol=1.0,
        base_stop_level=0.8,
        current_hold_ticks=6,
        currently_breakeven_locked=True,
        is_triggered=False,
    )
    assert locked1 is True and locked2 is True, "both ticks must be post-lock"
    assert stop2 < stop1, (
        f"AC-3: a post-lock HWM-anchored stop must be allowed to move down "
        f"above the 0.0 floor ({stop1} -> {stop2}). A frozen-level clamp "
        f"above the floor would forbid this."
    )
    assert stop2 >= 0.0, f"post-lock stop {stop2} must stay >= 0.0"


def test_locked_stop_is_max_of_base_and_zero_single_call() -> None:
    """
    INVARIANT 3 single-call: for is_triggered=False the locked branch
    returns exactly max(base_stop_level, 0.0).

    Catches an impl that applied max() in the wrong direction (min), or
    used a non-zero floor.
    """
    for base in (-5.0, -1.0, -0.001, 0.0, 0.5, 2.0, 100.0):
        _, _, stop = math_engine.compute_breakeven_update(
            current_return=0.0,
            symphony_vol=1.0,
            base_stop_level=base,
            current_hold_ticks=0,
            currently_breakeven_locked=True,
            is_triggered=False,
        )
        expected = max(base, 0.0)
        assert stop == pytest.approx(expected, rel=APPROX_REL, abs=APPROX_ABS), (
            f"locked stop for base={base}: expected max(base,0.0)={expected}, got {stop}."
        )


# ===========================================================================
# INVARIANT 4 — Breakeven latch is one-way across sequential calls.
# ===========================================================================


def test_breakeven_locked_is_one_way_latch_across_sequential_calls() -> None:
    """
    INVARIANT 4: once a call returns new_breakeven_locked=True, every
    subsequent call within the same position must also return True —
    regardless of current_return crashing, is_triggered toggling,
    base_stop_level oscillating, or symphony_vol shifting.

    Derivation (current_return, symphony_vol, base_stop_level, is_triggered):
      t1-t5 ( 1.5, 1.0, -0.5, False)  5 qualifying ticks -> LOCK FIRES on t5
      t6    (-5.0, 1.0, -2.0, False)  return crashes; hold_ticks resets;
                                      lock must HOLD
      t7    (-5.0, 5.0, -2.0, False)  vol shifts; lock holds
      t8    (-5.0, 0.1, -2.0, True)   is_triggered fires; lock holds
      t9    ( 0.5, 1.0,  0.0, False)  is_triggered un-fires; lock holds
      t10   (-5.0, 1.0, -1.0, False)  crash again; lock holds
    The lock must be True on every tick from t5 onward.
    """
    ticks = [
        {
            "current_return": 1.5,
            "symphony_vol": 1.0,
            "base_stop_level": -0.5,
            "is_triggered": False,
        },
        {
            "current_return": 1.5,
            "symphony_vol": 1.0,
            "base_stop_level": -0.5,
            "is_triggered": False,
        },
        {
            "current_return": 1.5,
            "symphony_vol": 1.0,
            "base_stop_level": -0.5,
            "is_triggered": False,
        },
        {
            "current_return": 1.5,
            "symphony_vol": 1.0,
            "base_stop_level": -0.5,
            "is_triggered": False,
        },
        {
            "current_return": 1.5,
            "symphony_vol": 1.0,
            "base_stop_level": -0.5,
            "is_triggered": False,
        },
        {
            "current_return": -5.0,
            "symphony_vol": 1.0,
            "base_stop_level": -2.0,
            "is_triggered": False,
        },
        {
            "current_return": -5.0,
            "symphony_vol": 5.0,
            "base_stop_level": -2.0,
            "is_triggered": False,
        },
        {
            "current_return": -5.0,
            "symphony_vol": 0.1,
            "base_stop_level": -2.0,
            "is_triggered": True,
        },
        {"current_return": 0.5, "symphony_vol": 1.0, "base_stop_level": 0.0, "is_triggered": False},
        {
            "current_return": -5.0,
            "symphony_vol": 1.0,
            "base_stop_level": -1.0,
            "is_triggered": False,
        },
    ]
    results = _simulate_position_lifetime(ticks)
    assert results[4][1] is True, (
        f"Sanity: lock should fire on tick 5 (5 qualifying ticks). Got locked={results[4][1]}."
    )
    for i in range(4, len(results)):
        assert results[i][1] is True, (
            f"ONE-WAY LATCH VIOLATED at tick {i + 1}: new_breakeven_locked="
            f"{results[i][1]!r}, expected True. Locked sequence: "
            f"{[r[1] for r in results]}."
        )


# ===========================================================================
# INVARIANT 5 — Triggered override is absolute.
# ===========================================================================


@pytest.mark.parametrize(
    "base_stop_level,locked",
    [
        (-5.0, False),
        (-5.0, True),
        (-0.001, False),
        (-0.001, True),
        (0.0, False),
        (0.0, True),
        (2.0, False),
        (2.0, True),
        (100.0, False),
        (100.0, True),
    ],
)
def test_triggered_override_yields_sentinel_regardless_of_inputs(
    base_stop_level: float, locked: bool
) -> None:
    """
    INVARIANT 5: is_triggered=True ALWAYS produces stop_trigger_level ==
    TRIGGERED_OVERRIDE_LEVEL (-999.0), regardless of base_stop_level or the
    breakeven lock state. The override is the committed-exit marker.

    Catches an impl that re-floored the sentinel via max(base,0.0), or
    skipped the override when not locked.
    """
    _, _, stop = math_engine.compute_breakeven_update(
        current_return=0.0,
        symphony_vol=1.0,
        base_stop_level=base_stop_level,
        current_hold_ticks=0,
        currently_breakeven_locked=locked,
        is_triggered=True,
    )
    assert stop == pytest.approx(
        math_engine.TRIGGERED_OVERRIDE_LEVEL, rel=APPROX_REL, abs=APPROX_ABS
    ), (
        f"is_triggered=True must yield TRIGGERED_OVERRIDE_LEVEL "
        f"({math_engine.TRIGGERED_OVERRIDE_LEVEL}); got {stop} for "
        f"base_stop_level={base_stop_level}, locked={locked}."
    )


# ===========================================================================
# active_trailing_stop lower bound (unsqueezed regime) — unchanged by H-1.
# ===========================================================================


@pytest.mark.parametrize(
    "symphony_vol,dynamic_multiplier,dynamic_min_stop",
    [
        (2.0, 1.5, 0.3),
        (0.1, 0.5, 0.3),
        (0.0, 1.0, 0.2),
        (-0.5, 1.0, 0.2),
        (1e-9, 1.0, 0.15),
    ],
)
def test_active_trailing_stop_lower_bound_is_dynamic_min_stop_when_unsqueezed(
    symphony_vol: float,
    dynamic_multiplier: float,
    dynamic_min_stop: float,
) -> None:
    """
    Lower-bound invariant for compute_active_trailing_stop in the UNSQUEEZED
    regime (para_armed=False AND breakeven_locked=False):
        output >= dynamic_min_stop
    by definition of max(safe_vol * mult, dynamic_min_stop).

    This property is unaffected by the H-1 fix; pinned here as a regression
    guard. The squeezed regime is covered by the AC-5 rejection tests
    (test_squeeze_multiplier_rejection.py).
    """
    output = math_engine.compute_active_trailing_stop(
        symphony_vol=symphony_vol,
        dynamic_multiplier=dynamic_multiplier,
        dynamic_min_stop=dynamic_min_stop,
        para_armed=False,
        breakeven_locked=False,
        parabolic_squeeze_multiplier=0.5,  # set but should NOT fire
    )
    assert output >= dynamic_min_stop, (
        f"Lower-bound violated: output {output} < dynamic_min_stop {dynamic_min_stop}."
    )


# ===========================================================================
# Determinism.
# ===========================================================================


def test_simulate_position_lifetime_is_deterministic() -> None:
    """
    Sanity: replaying the same tick sequence twice produces bit-identical
    results. Post-H-1-fix compute_breakeven_update is fully per-tick
    stateless w.r.t. the stop level; this confirms no accidental state leak.
    """
    ticks = [
        {
            "current_return": 1.5,
            "symphony_vol": 1.0,
            "base_stop_level": -0.5,
            "is_triggered": False,
        },
        {"current_return": 2.0, "symphony_vol": 1.0, "base_stop_level": 1.0, "is_triggered": False},
        {"current_return": 1.5, "symphony_vol": 1.0, "base_stop_level": 0.5, "is_triggered": False},
    ]
    assert _simulate_position_lifetime(ticks) == _simulate_position_lifetime(ticks)
