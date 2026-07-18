"""
R3-c / MA-11 AC-2 — property-based proof of the no-widening invariant for the
optional ``squeeze_floor`` clamp on ``math_engine.compute_active_trailing_stop``.

The clamp must satisfy, for ANY squeezed position (para_armed or
breakeven_locked) with a positive finite floor, across the operating ranges:

  1. NO-WIDENING: result <= pre_squeeze_active   (the floor can only limit
     SHRINKAGE; arming the squeeze must never WIDEN the stop above its
     pre-squeeze value -- the exact inversion the min(floor, pre_squeeze)
     bound prevents when floor > pre_squeeze near close).
  2. FLOOR RAISES ONLY: result >= active * squeeze  (the floor never LOWERS
     the squeezed distance).
  3. POSITIVITY: result > 0.
  4. EXACT CLAMP: result == max(active*sq, min(floor, pre_squeeze))  (golden
     form; the reference is derived from the SAME inputs, never hardcoded).

Invariants 1-3 are implementation-independent (they hold for the CONTRACT, not
a captured value), so they are the non-circular core; invariant 4 is the
golden form.

RED taxonomy: CONTRACT RED at HEAD -- the seam has no ``squeeze_floor`` param,
so every hypothesis example raises TypeError. GREEN post-impl.

Tolerance: pytest.approx(rel=1e-9, abs=1e-12) on the exact-form check; the
inequality invariants use a tiny slack (1e-12) to absorb ~1 ulp of binary drift.
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import math_engine

_REL = 1e-9
_ABS = 1e-12
_SLACK = 1e-12  # ulp slack for the inequality invariants


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.filter_too_much])
@given(
    symphony_vol=st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False),
    dynamic_multiplier=st.floats(
        min_value=0.01, max_value=5.0, allow_nan=False, allow_infinity=False
    ),
    dynamic_min_stop=st.floats(
        min_value=0.01, max_value=1.0, allow_nan=False, allow_infinity=False
    ),
    squeeze=st.floats(min_value=0.01, max_value=1.0, allow_nan=False, allow_infinity=False),
    floor=st.floats(min_value=0.001, max_value=2.0, allow_nan=False, allow_infinity=False),
    which_flag=st.sampled_from(["para", "breakeven", "both"]),
)
def test_squeeze_floor_never_widens_and_only_raises(
    symphony_vol: float,
    dynamic_multiplier: float,
    dynamic_min_stop: float,
    squeeze: float,
    floor: float,
    which_flag: str,
) -> None:
    """AC-2: across the operating ranges the clamp bounds SHRINKAGE only."""
    para_armed = which_flag in ("para", "both")
    breakeven_locked = which_flag in ("breakeven", "both")

    # Reference derived from the SAME inputs (no hardcoded producer value).
    safe_vol = symphony_vol if symphony_vol > 0 else math_engine.VOL_FALLBACK
    pre_squeeze = max(safe_vol * dynamic_multiplier, dynamic_min_stop)
    squeezed = pre_squeeze * squeeze
    expected = max(squeezed, min(floor, pre_squeeze))

    result = math_engine.compute_active_trailing_stop(
        symphony_vol=symphony_vol,
        dynamic_multiplier=dynamic_multiplier,
        dynamic_min_stop=dynamic_min_stop,
        para_armed=para_armed,
        breakeven_locked=breakeven_locked,
        parabolic_squeeze_multiplier=squeeze,
        squeeze_floor=floor,
    )

    # 1. No widening: never above the pre-squeeze distance.
    assert result <= pre_squeeze + _SLACK, (
        f"WIDENING: result {result} > pre_squeeze {pre_squeeze} "
        f"(floor={floor}, squeeze={squeeze}). The floor must never widen the stop."
    )
    # 2. Floor raises only: never below the squeezed value.
    assert result >= squeezed - _SLACK, (
        f"LOWERING: result {result} < squeezed {squeezed}. The floor may only raise."
    )
    # 3. Positivity.
    assert result > 0.0, f"non-positive stop distance {result}"
    # 4. Exact clamp form.
    assert result == pytest.approx(expected, rel=_REL, abs=_ABS), (
        f"clamp form mismatch: expected max(squeezed={squeezed}, "
        f"min(floor={floor}, pre_squeeze={pre_squeeze}))={expected}, got {result}"
    )


@settings(max_examples=200, deadline=None)
@given(
    pre_base=st.floats(min_value=0.05, max_value=1.0, allow_nan=False, allow_infinity=False),
    squeeze=st.floats(min_value=0.05, max_value=0.95, allow_nan=False, allow_infinity=False),
    floor_a=st.floats(min_value=0.001, max_value=1.0, allow_nan=False, allow_infinity=False),
    floor_b=st.floats(min_value=0.001, max_value=1.0, allow_nan=False, allow_infinity=False),
)
def test_squeeze_floor_is_monotonic_nondecreasing_in_the_floor(
    pre_base: float, squeeze: float, floor_a: float, floor_b: float
) -> None:
    """AC-2 companion: at fixed other params, a higher floor yields a
    non-decreasing clamped stop (up to the pre_squeeze cap). A non-monotone
    result would betray a broken clamp direction.
    """
    # Force min_stop to win so pre_squeeze == pre_base exactly (vol term = 0).
    kw = dict(
        symphony_vol=0.0,  # -> VOL_FALLBACK*mult, made to lose below
        dynamic_multiplier=0.001,
        dynamic_min_stop=pre_base,
        para_armed=True,
        breakeven_locked=False,
        parabolic_squeeze_multiplier=squeeze,
    )
    lo, hi = sorted((floor_a, floor_b))
    out_lo = math_engine.compute_active_trailing_stop(squeeze_floor=lo, **kw)
    out_hi = math_engine.compute_active_trailing_stop(squeeze_floor=hi, **kw)
    assert out_hi >= out_lo - _SLACK, (
        f"non-monotone: floor {hi} gave {out_hi} < floor {lo} gave {out_lo}. "
        f"A higher floor must never shrink the clamped stop."
    )
