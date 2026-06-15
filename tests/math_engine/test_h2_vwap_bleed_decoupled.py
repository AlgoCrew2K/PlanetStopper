"""
H2 — VWAP-bleed (System B) decoupled from the System-A gate.

WHY THIS SUITE EXISTS
---------------------
``compute_vwap_breakdown_update`` runs two independent exit sub-systems:
  * System A (profit-protection break) — keyed on the VWAP-cross HWM and a
    VWAP-coverage/sign gate.
  * System B (bleed) — keyed ONLY on the symphony return falling to/below the
    dynamic bleed-arm threshold (``current_return <= vwap_bleed_arm_pct``).
    System B references NEITHER the VWAP sign NOR coverage.

The docstring already claims System B is "INDEPENDENT of A", but the code is
not: the early gate

    if not (valid_vwap_weight > VWAP_WEIGHT_THRESHOLD and weighted_vwap_diff < 0):
        return 0, 0, False, False

wipes BOTH counters. So a real -2.5% bleed ladder is reset to 0 the instant the
price bounces back above VWAP (``weighted_vwap_diff > 0``) or VWAP coverage
drops below 0.5 — even though neither condition has anything to do with System
B's arm signal. The synthesis verdict (validation/VERDICT.md, finding H2)
confirmed this against the live exit path.

THE FIX (decouple — reviewer-ruled reading A)
---------------------------------------------
System B evaluates under ONLY the ``is_triggered`` short-circuit. The
System-A gate's early reset must reset the System-A counter ONLY; System B's
arm/increment/reset runs regardless of the VWAP weight/sign gate. Net:
  * a bleed ladder SURVIVES a VWAP bounce and low coverage;
  * System A still resets on its gate miss (we only decoupled B);
  * both counters stay frozen when is_triggered.

RED STATUS (pre-fix)
--------------------
On the unfixed code the gate at line ~864 wipes the bleed counter, so
``test_bleed_counter_survives_vwap_bounce`` / ``..._survives_low_coverage``
assert a non-zero bleed count where the code returns 0 -> RED. The two
regression tests (System A still gated; both off when triggered) are GREEN on
current code and must STAY green after the fix — they pin that the decoupling
did not over-reach.

TOLERANCE POLICY
----------------
The function returns (int, int, bool, bool) — no floats — so exact equality
applies throughout. No pytest.approx needed.
"""

from __future__ import annotations

import pytest

import math_engine


def _bleed_arm_inputs(
    *,
    valid_vwap_weight: float,
    weighted_vwap_diff: float,
    current_return: float,
    current_vwap_bleed_ticks: int,
    current_vwap_ticks: int = 0,
    is_triggered: bool = False,
    vwap_bleed_ticks_threshold: int = 3,
) -> dict:
    """
    Build a kwargs dict for compute_vwap_breakdown_update where System B's arm
    condition (current_return <= vwap_bleed_arm_pct) is MET via a fixed
    bleed_arm of -1.0 and a current_return of -2.5. System A is set to MISS so
    its state never confounds System B's:
      safe_hwm=0.0, vwap_cross_hwm_pct=5.0 (0.0 >= 5.0 is False -> System A miss).
    """
    return dict(
        is_triggered=is_triggered,
        valid_vwap_weight=valid_vwap_weight,
        weighted_vwap_diff=weighted_vwap_diff,
        safe_hwm=0.0,
        current_return=current_return,
        vwap_cross_hwm_pct=5.0,  # System A gate: 0.0 >= 5.0 -> False (A misses)
        vwap_bleed_arm_pct=-1.0,  # System B arms when current_return <= -1.0
        vwap_bleed_ticks_threshold=vwap_bleed_ticks_threshold,
        current_vwap_ticks=current_vwap_ticks,
        current_vwap_bleed_ticks=current_vwap_bleed_ticks,
    )


def test_bleed_counter_survives_vwap_bounce() -> None:
    """
    A bleed ladder is armed for 3 ticks at -2.5%. On tick 4 the price bounces
    back ABOVE VWAP (weighted_vwap_diff > 0), which FAILS System A's gate, but
    the symphony return is STILL -2.5% (System B's arm condition still met).
    System B must INCREMENT to 4 — not reset to 0.

    PRE-FIX (RED): the System-A gate `not (weight > 0.5 and diff < 0)` is True
    (diff > 0), so the function returns (0, 0, False, False) — the bleed count
    is wiped. POST-FIX: System B is decoupled and increments to 4.
    """
    new_a, new_b, is_a_broken, is_b_broken = math_engine.compute_vwap_breakdown_update(
        **_bleed_arm_inputs(
            valid_vwap_weight=0.9,  # coverage fine
            weighted_vwap_diff=0.5,  # BOUNCE above VWAP -> System A gate fails
            current_return=-2.5,  # still bleeding -> System B arm met
            current_vwap_bleed_ticks=3,
        )
    )
    assert new_b == 4, (
        f"BLEED COUNTER WIPED on a VWAP bounce: System B armed at -2.5% must "
        f"increment 3 -> 4 regardless of the VWAP sign (its arm condition does "
        f"not reference VWAP); got {new_b}. Pre-fix the System-A gate wiped it."
    )
    assert is_b_broken is True, (
        f"is_vwap_bleed_broken must be True once new_b (4) >= threshold (3); got {is_b_broken}."
    )


def test_bleed_counter_survives_low_coverage() -> None:
    """
    A bleed ladder at 8 ticks with VWAP coverage BELOW the threshold
    (valid_vwap_weight <= VWAP_WEIGHT_THRESHOLD=0.5) and the symphony return
    still at -2.5% (System B arm met). System B must INCREMENT (9), not be
    wiped — coverage is a System-A concern; System B keys on the symphony
    return alone.

    PRE-FIX (RED): low coverage fails the System-A gate -> both wiped -> 0.
    POST-FIX: System B increments to 9.
    """
    new_a, new_b, is_a_broken, is_b_broken = math_engine.compute_vwap_breakdown_update(
        **_bleed_arm_inputs(
            valid_vwap_weight=0.4,  # coverage BELOW VWAP_WEIGHT_THRESHOLD
            weighted_vwap_diff=-0.5,  # diff sign fine, but coverage fails the gate
            current_return=-2.5,  # System B arm met
            current_vwap_bleed_ticks=8,
            vwap_bleed_ticks_threshold=10,  # 9 < 10 -> not yet broken
        )
    )
    assert new_b == 9, (
        f"BLEED COUNTER WIPED on low VWAP coverage: System B armed at -2.5% "
        f"must increment 8 -> 9 regardless of coverage; got {new_b}."
    )
    assert is_b_broken is False, (
        f"new_b (9) < threshold (10): is_vwap_bleed_broken must be False; got {is_b_broken}."
    )


def test_bleed_counter_resets_when_its_own_arm_condition_fails() -> None:
    """
    Decoupling System B must NOT make it a one-way ratchet: when System B's OWN
    arm condition fails (current_return ABOVE vwap_bleed_arm_pct, i.e. the bleed
    stopped), the bleed counter resets to 0 — even if System A's gate is also
    failing. This pins that the fix preserves System B's own reset semantics and
    did not simply freeze the counter on a gate miss.

    GREEN on current code only by accident (gate-fail wipes it); the point is it
    must STAY 0 here AFTER the fix when B's arm misses.
    """
    new_a, new_b, is_a_broken, is_b_broken = math_engine.compute_vwap_breakdown_update(
        **_bleed_arm_inputs(
            valid_vwap_weight=0.9,
            weighted_vwap_diff=0.5,  # System A gate fails (bounce)
            current_return=-0.5,  # ABOVE bleed_arm -1.0 -> System B arm MISSES
            current_vwap_bleed_ticks=5,
        )
    )
    assert new_b == 0, (
        f"System B must reset to 0 when its OWN arm condition fails "
        f"(current_return -0.5 > bleed_arm -1.0), not ratchet; got {new_b}."
    )
    assert is_b_broken is False


def test_system_a_still_gated_on_gate_miss() -> None:
    """
    REGRESSION: we only decoupled System B. System A's counter must STILL reset
    to 0 on a System-A gate miss (VWAP bounce / low coverage). This guards
    against an over-broad fix that accidentally decoupled System A too.

    GREEN on current code; must STAY green after the fix.
    """
    new_a, new_b, is_a_broken, is_b_broken = math_engine.compute_vwap_breakdown_update(
        is_triggered=False,
        valid_vwap_weight=0.9,
        weighted_vwap_diff=0.5,  # bounce -> System A gate fails
        safe_hwm=10.0,
        current_return=1.0,  # < safe_hwm; would arm System A IF gate passed
        vwap_cross_hwm_pct=2.0,  # 10.0 >= 2.0 (System A condition would be met)
        vwap_bleed_arm_pct=-1.0,
        vwap_bleed_ticks_threshold=3,
        current_vwap_ticks=2,  # near System A threshold
        current_vwap_bleed_ticks=0,
    )
    assert new_a == 0, (
        f"System A must STILL reset on its gate miss (VWAP bounce); got "
        f"{new_a}. The H2 fix decouples ONLY System B."
    )
    assert is_a_broken is False, (
        f"is_vwap_broken must be False when System A's gate fails; got {is_a_broken}."
    )


def test_both_counters_frozen_when_triggered() -> None:
    """
    REGRESSION: the is_triggered short-circuit (the outermost guard) must STILL
    freeze BOTH counters at their input values regardless of every other input.
    The H2 decoupling lives INSIDE the is_triggered guard, so a triggered
    position never evaluates either system.

    GREEN on current code; must STAY green after the fix.
    """
    new_a, new_b, is_a_broken, is_b_broken = math_engine.compute_vwap_breakdown_update(
        **_bleed_arm_inputs(
            valid_vwap_weight=0.9,
            weighted_vwap_diff=-0.5,
            current_return=-2.5,  # would arm System B if not triggered
            current_vwap_bleed_ticks=7,
            current_vwap_ticks=4,
            is_triggered=True,
        )
    )
    assert new_a == 4, f"Triggered: System A count must be frozen at 4; got {new_a}."
    assert new_b == 7, f"Triggered: System B count must be frozen at 7; got {new_b}."
    assert is_a_broken is False and is_b_broken is False, (
        f"Triggered: both broken flags must be False; got a={is_a_broken}, b={is_b_broken}."
    )


def test_bleed_increments_even_when_system_a_simultaneously_arms() -> None:
    """
    Independence in the OTHER direction: when the System-A gate PASSES and
    System A also arms, System B's own count must still track its own arm
    condition independently. (This is GREEN on current code — the gate passes —
    but it pins that the decoupled structure keeps System B independent in the
    gate-pass branch too, so the fix doesn't accidentally couple them the other
    way.)
    """
    new_a, new_b, is_a_broken, is_b_broken = math_engine.compute_vwap_breakdown_update(
        is_triggered=False,
        valid_vwap_weight=0.9,
        weighted_vwap_diff=-0.5,  # gate passes
        safe_hwm=10.0,
        current_return=-2.5,  # System A: -2.5 < 10.0 (met); System B: -2.5 <= -1.0 (met)
        vwap_cross_hwm_pct=2.0,  # 10.0 >= 2.0 (System A condition met)
        vwap_bleed_arm_pct=-1.0,
        vwap_bleed_ticks_threshold=3,
        current_vwap_ticks=1,
        current_vwap_bleed_ticks=1,
    )
    assert new_a == 2, f"System A must increment 1 -> 2; got {new_a}."
    assert new_b == 2, f"System B must increment 1 -> 2 independently; got {new_b}."
