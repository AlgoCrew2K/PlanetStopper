"""
H1 — MC sanity gate inversion: the protective-stop semantics RED suite.

WHY THIS SUITE EXISTS
---------------------
The Monte-Carlo probability returned by ``run_monte_carlo`` is the fraction of
regime-matched analog days whose return is >= today's. It is HIGH (-> ~100)
when today is BAD (badly underperforming the analog distribution) and LOW
(-> ~0) when today is GOOD (outperforming). The legacy name ``prob_beating``
is the INVERSE of what the value computes; the synthesis verdict (validation/
VERDICT.md, finding H1) confirmed this empirically against the live function.

The latent bug (inherited verbatim from the fork base): the trailing-stop
sanity gate in ``compute_exit_confirmation`` read

    mc_sanity_ok = prob_beating is None or prob_beating < MC_SANITY_THRESHOLD(60)

Because the value is HIGH (~100) on a real -8% idiosyncratic crash, the gate
``100 < 60`` -> False SUPPRESSED the protective stop in exactly the scenario
the stop exists to defend (a single-position / sector drawdown while SPY is
calm). The market-wide-crash case was accidentally protected because the
regime-match-quality guard overrides the value to None (fail-open).

THE FIX (verified rename-only + ONE operator flip — NOT a recompute):
  * The ``run_monte_carlo`` VALUE is already correct (underperformance
    direction); do NOT recompute it. RENAME the metric ``prob_beating`` ->
    ``prob_underperforming`` everywhere (including the function parameters of
    compute_exit_confirmation and compute_tp_confirmation), the user_attr
    keys, comments, and the consumers-enumerated meta-test. Value unchanged.
  * The stop gate flips its operator to fire on confirmed high underperformance
    and fail open on None:

        mc_breakdown_ok = (prob_underperforming is None
                           or prob_underperforming >= MC_BREAKDOWN_THRESHOLD)

  * MC_SANITY_THRESHOLD (value 60.0) is renamed MC_BREAKDOWN_THRESHOLD (same
    value); the inverted "sanity" framing belonged to the wrong reading.
  * ALL OTHER consumers (TP "Exceptional Gain" arm, the 5-15 ARM band, the
    DISARM, dashboard/reporting display) are RENAME-ONLY: same operator, same
    threshold -> behavior preserved trivially.

RED STATUS (pre-fix)
--------------------
These tests call ``compute_exit_confirmation`` / ``compute_tp_confirmation``
with the NEW keyword ``prob_underperforming`` and assert MC_BREAKDOWN_THRESHOLD
exists. On the unfixed code the parameter and constant do not exist, so every
behavioral test RAISES (TypeError / AttributeError) -> RED. After the rename +
operator flip they pass. The crash-fires / dip-suppressed pair additionally
pins the operator direction so a rename WITHOUT the flip stays RED.

TOLERANCE POLICY
----------------
compute_exit_confirmation / compute_tp_confirmation return (int, bool) /
(bool, int, bool) — no floats — so exact equality applies. The one float
comparison (the MC_BREAKDOWN_THRESHOLD constant value 60.0) is an exact pin
of a named constant (a single decimal literal, no accumulated FP error), so
``== 60.0`` is correct without pytest.approx.
"""

from __future__ import annotations

import pytest

import math_engine


# ---------------------------------------------------------------------------
# Constant rename: MC_BREAKDOWN_THRESHOLD exists, value preserved at 60.0
# ---------------------------------------------------------------------------


def test_mc_breakdown_threshold_constant_exists_with_preserved_value() -> None:
    """
    The threshold constant is renamed MC_SANITY_THRESHOLD -> MC_BREAKDOWN_THRESHOLD
    to match the corrected reading (fire the stop when *breakdown* is confirmed
    high). The VALUE must be preserved at 60.0 — H1 is a semantic/naming fix,
    NOT a tuning change. RED pre-fix: the constant does not exist.
    """
    assert hasattr(math_engine, "MC_BREAKDOWN_THRESHOLD"), (
        "math_engine.MC_BREAKDOWN_THRESHOLD not found. H1 renames "
        "MC_SANITY_THRESHOLD -> MC_BREAKDOWN_THRESHOLD (same value); the "
        "'sanity' framing belonged to the inverted reading of prob_beating."
    )
    # Exact pin of a single named-constant literal (no FP accumulation).
    assert math_engine.MC_BREAKDOWN_THRESHOLD == 60.0, (
        f"MC_BREAKDOWN_THRESHOLD must preserve the value 60.0 (H1 is a naming "
        f"+ operator fix, not a retune); got {math_engine.MC_BREAKDOWN_THRESHOLD}."
    )


# ---------------------------------------------------------------------------
# The CENTERPIECE: the protective stop fires on a confirmed idiosyncratic crash
# ---------------------------------------------------------------------------


def test_protective_stop_fires_on_idiosyncratic_crash() -> None:
    """
    Scenario: an armed position has crashed -8% on its own while SPY is calm
    (the regime guard did NOT override to None, so a real MC opinion exists).
    The analog distribution says today is badly underperforming ->
    prob_underperforming ~ 100 (well above MC_BREAKDOWN_THRESHOLD=60). The
    magnitude condition is met (return far below the stop) and the count is at
    the threshold edge. The stop MUST fire.

    PRE-FIX (RED): compute_exit_confirmation has no prob_underperforming
    parameter -> TypeError. Even if the param existed but the operator was NOT
    flipped, `100 < 60` -> False would SUPPRESS the stop -> is_trailing_stop_hit
    stays False -> this assertion fails. Two ways to be RED; one way to be GREEN.

    This is the exact suppression the synthesis verdict flagged as the
    unprotected hole (idiosyncratic drawdown, SPY calm).
    """
    # current_below_stop_count=2 so a single qualifying tick reaches
    # EXIT_CONFIRM_TICKS=3 and flips the hit.
    new_count, is_trailing_stop_hit = math_engine.compute_exit_confirmation(
        armed=True,
        is_triggered=False,
        current_return=-8.0,
        stop_trigger_level=-1.0,  # threshold = -1.10; -8.0 is far below -> magnitude met
        prob_underperforming=100.0,  # confirmed high underperformance
        current_below_stop_count=2,
    )
    assert new_count == 3, (
        f"On a confirmed crash the qualifying tick must increment the count "
        f"to 3 (was 2); got {new_count}. A count that reset to 0 means the "
        f"gate wrongly vetoed the qualifying tick (operator not flipped)."
    )
    assert is_trailing_stop_hit is True, (
        "PROTECTIVE STOP SUPPRESSED on a confirmed idiosyncratic crash. "
        "prob_underperforming=100 (>= MC_BREAKDOWN_THRESHOLD=60) with the "
        "magnitude condition met MUST fire is_trailing_stop_hit. Pre-fix the "
        "inverted gate (prob_beating < 60) suppressed exactly this case."
    )


# ---------------------------------------------------------------------------
# The complement: the stop stays suppressed on a normal dip (no hair-trigger)
# ---------------------------------------------------------------------------


def test_protective_stop_suppressed_on_normal_dip() -> None:
    """
    Scenario: a shallow dip where the analog distribution says today is NOT
    unusual -> prob_underperforming LOW (e.g. 20, well below the 60 breakdown
    threshold). Even though the magnitude condition is met, the MC second
    opinion says "this is normal noise, don't capitulate" -> the gate vetoes
    and the count RESETS to 0. This proves the operator flip did not turn the
    stop into a hair-trigger that fires on any magnitude breach.

    PRE-FIX (RED): no prob_underperforming parameter -> TypeError. (Pre-fix the
    OLD gate `20 < 60` -> True would actually count this tick — the opposite
    behavior — so this test also pins that the NEW gate suppresses a low
    reading.)
    """
    new_count, is_trailing_stop_hit = math_engine.compute_exit_confirmation(
        armed=True,
        is_triggered=False,
        current_return=-8.0,
        stop_trigger_level=-1.0,  # magnitude met
        prob_underperforming=20.0,  # LOW: analogs say today is normal
        current_below_stop_count=2,
    )
    assert new_count == 0, (
        f"On a low-underperformance reading the gate must VETO the tick and "
        f"reset the count to 0 (no hair-trigger); got {new_count}."
    )
    assert is_trailing_stop_hit is False, (
        "A low prob_underperforming (20 < MC_BREAKDOWN_THRESHOLD=60) must "
        "SUPPRESS the stop even with the magnitude condition met — the MC "
        "second opinion says this dip is normal noise."
    )


# ---------------------------------------------------------------------------
# Regression: market-wide crash still fires via the regime-guard None path
# ---------------------------------------------------------------------------


def test_market_wide_crash_fires_via_regime_guard_none() -> None:
    """
    On a market-WIDE crash the regime-match-quality guard sets
    prob_underperforming=None (the kNN analog pool is unrepresentative;
    fail-open). The gate must PASS on None so the protective stop still fires
    on the magnitude+ticks condition alone. This was the only case accidentally
    protected pre-fix; the fix must NOT break it.

    PRE-FIX (RED): no prob_underperforming parameter -> TypeError. POST-FIX:
    None -> mc_breakdown_ok True -> the qualifying tick counts -> hit at the
    threshold.
    """
    new_count, is_trailing_stop_hit = math_engine.compute_exit_confirmation(
        armed=True,
        is_triggered=False,
        current_return=-8.0,
        stop_trigger_level=-1.0,
        prob_underperforming=None,  # regime guard override: MC unavailable
        current_below_stop_count=2,
    )
    assert new_count == 3, (
        f"None (MC unavailable) must FAIL OPEN: the magnitude-qualifying tick "
        f"increments the count to 3; got {new_count}."
    )
    assert is_trailing_stop_hit is True, (
        "FAIL-OPEN BROKEN: prob_underperforming=None (market-wide crash, "
        "regime guard override) must let the protective stop fire on the "
        "magnitude+ticks condition alone. An absent MC opinion must NEVER "
        "disable the protective stop."
    )


# ---------------------------------------------------------------------------
# Property: at-threshold boundary fires (>= is inclusive, matching the spec)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prob,expected_hit",
    [
        (59.999, False),  # just below breakdown threshold -> suppressed
        (60.0, True),  # exactly at threshold -> fires (>= is inclusive)
        (60.001, True),  # just above -> fires
        (100.0, True),  # max underperformance -> fires
        (0.0, False),  # outperforming -> suppressed
    ],
)
def test_breakdown_gate_boundary_is_inclusive_at_threshold(prob: float, expected_hit: bool) -> None:
    """
    The corrected gate uses `>= MC_BREAKDOWN_THRESHOLD` (inclusive at 60.0).
    A value exactly at the threshold counts as confirmed breakdown and fires;
    a value just below is suppressed. This pins the operator AND the boundary
    direction so a `>` (strict) impl is caught.

    Inputs put the count one tick from the EXIT_CONFIRM_TICKS=3 edge and the
    magnitude deeply met, so the only variable is the MC gate.

    PRE-FIX (RED): no prob_underperforming parameter -> TypeError.
    """
    _, is_trailing_stop_hit = math_engine.compute_exit_confirmation(
        armed=True,
        is_triggered=False,
        current_return=-8.0,
        stop_trigger_level=-1.0,
        prob_underperforming=prob,
        current_below_stop_count=2,
    )
    assert is_trailing_stop_hit is expected_hit, (
        f"Boundary mismatch at prob_underperforming={prob}: expected hit="
        f"{expected_hit}, got {is_trailing_stop_hit}. The gate must be "
        f"`>= MC_BREAKDOWN_THRESHOLD(60.0)` (inclusive)."
    )


# ---------------------------------------------------------------------------
# Regression: the TP "Exceptional Gain" arm is RENAME-ONLY (behavior preserved)
# ---------------------------------------------------------------------------


def test_tp_arm_behavior_unchanged_after_rename() -> None:
    """
    The TP "Exceptional Gain" arm in compute_tp_confirmation is RENAME-ONLY:
    same operator (`prob_underperforming < take_profit_mc_pct`), same threshold.
    An exceptional UP day has LOW prob_underperforming (outperforming the
    analog pool), which is below take_profit_mc_pct -> arms the TP. Behavior
    must be identical to the pre-rename `prob_beating < take_profit_mc_pct`
    (the value is unchanged; only the name changed).

    PRE-FIX (RED): compute_tp_confirmation has no prob_underperforming
    parameter -> TypeError.
    """
    # Low prob (outperforming) below the TP threshold -> arm.
    new_tp_armed, new_above_tp_count, is_tp_hit = math_engine.compute_tp_confirmation(
        mc_available=True,
        prob_underperforming=2.0,  # < take_profit_mc_pct=5.0 -> exceptional gain
        take_profit_mc_pct=5.0,
        current_return=10.0,
        is_triggered=False,
        tp_armed=False,
        above_tp_count=0,
    )
    assert new_tp_armed is True, (
        "TP arm behavior changed: a low prob_underperforming (2 < 5) on an "
        "exceptional up-day must ARM the take-profit, exactly as the pre-rename "
        "code did. The rename must preserve this behavior."
    )
    assert new_above_tp_count == 0, f"Arming resets above_tp_count to 0; got {new_above_tp_count}."
    assert is_tp_hit is False, "Arming alone does not fire the TP; got is_tp_hit True."


def test_tp_confirm_behavior_unchanged_after_rename() -> None:
    """
    The TP confirmation arm of compute_tp_confirmation is also RENAME-ONLY:
    while tp_armed, a tick where prob_underperforming >= take_profit_mc_pct
    (the exceptional gain faded) increments above_tp_count and, at
    TP_CONFIRM_TICKS with current_return > 0, fires the TP. Pins that the
    `>=` confirm operator survives the rename unchanged.

    PRE-FIX (RED): no prob_underperforming parameter -> TypeError.
    """
    # tp_armed, prob rose back to/above threshold, count one below confirm edge,
    # return positive -> confirm fires.
    new_tp_armed, new_above_tp_count, is_tp_hit = math_engine.compute_tp_confirmation(
        mc_available=True,
        prob_underperforming=5.0,  # >= take_profit_mc_pct=5.0 -> confirm tick
        take_profit_mc_pct=5.0,
        current_return=10.0,
        is_triggered=False,
        tp_armed=True,
        above_tp_count=math_engine.TP_CONFIRM_TICKS - 1,
    )
    assert is_tp_hit is True, (
        "TP confirm behavior changed: prob_underperforming >= threshold while "
        "armed at the confirm edge with a positive return must fire the TP — "
        "identical to the pre-rename behavior."
    )
    assert new_above_tp_count == math_engine.TP_CONFIRM_TICKS, (
        f"above_tp_count must reach TP_CONFIRM_TICKS; got {new_above_tp_count}."
    )
    assert new_tp_armed is True, "TP stays armed on a confirming fire."
