"""
RED tests for AC-5 (audit finding M-2): compute_active_trailing_stop must
REJECT a non-positive parabolic_squeeze_multiplier with an explicit
ValueError, consistent with math_engine's reject-don't-coerce policy.

Background (math-audit, MEDIUM finding + policy ruling):

  compute_active_trailing_stop applies the squeeze multiplier AFTER the
  dynamic_min_stop floor:
      active = max(safe_vol * dynamic_multiplier, dynamic_min_stop)
      if para_armed or breakeven_locked:
          active *= parabolic_squeeze_multiplier
  With multiplier == 0 the active stop collapses to 0 (the position exits on
  the first noise tick). With multiplier < 0 the stop distance goes negative,
  placing the stop ABOVE the high-water mark — nonsensical.

  Policy ruling (audit addendum): multiplier <= 0 is REJECTED at entry, not
  clamped. Clamping would mask a mistuned Optuna parameter. This mirrors the
  module's existing _reject_non_finite policy for NaN/Inf inputs.

GREEN target: compute_active_trailing_stop raises ValueError when
parabolic_squeeze_multiplier <= 0, BEFORE any arithmetic. For multiplier in
(0, 1] the function behaves as before and the output is strictly positive.

Tolerance policy:
  - Rejection assertions: exact — pytest.raises(ValueError). No tolerance.
  - Valid-case assertions: the test asserts SHAPE/SIGN (output is a strictly
    positive float), never a hardcoded producer value. The exact stop
    distance is producer-computed; per the project rule we assert the
    invariant (output > 0), not the number.

Provenance: cases from tests/fixtures/math/squeeze_rejection/
squeeze_rejection_multiplier_cases.json. The fixture lists inputs +
expect_raises only; no producer-computed output is stored or asserted.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

import math_engine


FIXTURE = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "math"
    / "squeeze_rejection"
    / "squeeze_rejection_multiplier_cases.json"
)


def _load_cases() -> list[dict[str, Any]]:
    with FIXTURE.open("r", encoding="utf-8") as fh:
        return json.load(fh)["cases"]


CASES = _load_cases()
REJECT_CASES = [c for c in CASES if c["expect_raises"] is not None]
VALID_CASES = [c for c in CASES if c["expect_raises"] is None]


def _call(inputs: dict[str, Any]) -> float:
    return math_engine.compute_active_trailing_stop(
        symphony_vol=inputs["symphony_vol"],
        dynamic_multiplier=inputs["dynamic_multiplier"],
        dynamic_min_stop=inputs["dynamic_min_stop"],
        para_armed=inputs["para_armed"],
        breakeven_locked=inputs["breakeven_locked"],
        parabolic_squeeze_multiplier=inputs["parabolic_squeeze_multiplier"],
    )


# ===========================================================================
# AC-5 — Rejection: multiplier <= 0 raises ValueError
# ===========================================================================


@pytest.mark.parametrize(
    "case",
    REJECT_CASES,
    ids=[c["label"] for c in REJECT_CASES],
)
def test_non_positive_squeeze_multiplier_raises_value_error(case: dict[str, Any]) -> None:
    """
    AC-5 (M-2): compute_active_trailing_stop must raise ValueError when
    parabolic_squeeze_multiplier <= 0 — at entry, before any arithmetic.

    Cases (from fixture):
      - multiplier == 0.0  (would collapse the stop to 0 -> instant exit)
      - multiplier < 0     (would place the stop above the HWM)
      - multiplier < 0 even when para_armed/breakeven_locked are both False
        (the squeeze branch would not fire, but a mistuned negative
        multiplier is still an invalid parameter and must be rejected at
        entry — defense in depth, not "reject only when used").

    RED: the current impl does not validate the multiplier sign; multiplier
    == 0 returns 0.0 and multiplier < 0 returns a negative distance.
    """
    with pytest.raises(ValueError):
        _call(case["inputs"])


def test_squeeze_multiplier_zero_collapse_is_rejected_not_returned() -> None:
    """
    AC-5: pin the exact failure mode the audit named — multiplier == 0.0 with
    para_armed=True. The pre-fix impl RETURNED 0.0 (a zero stop distance =
    instant exit). The fix must REJECT this with a ValueError so a mistuned
    Optuna MAX_PARABOLIC_SQUEEZE surfaces loudly instead of silently
    disarming the trailing stop.
    """
    with pytest.raises(ValueError):
        math_engine.compute_active_trailing_stop(
            symphony_vol=2.0,
            dynamic_multiplier=1.5,
            dynamic_min_stop=0.30,
            para_armed=True,
            breakeven_locked=False,
            parabolic_squeeze_multiplier=0.0,
        )


def test_squeeze_multiplier_negative_does_not_place_stop_above_hwm() -> None:
    """
    AC-5: a negative multiplier would yield a NEGATIVE stop distance. The
    stop distance is subtracted from the HWM by the caller
    (base_stop_level = safe_hwm - active_trailing_stop); a negative distance
    places the stop ABOVE the HWM — guaranteeing an exit on the next tick.
    The fix must reject it before that nonsense reaches the caller.
    """
    with pytest.raises(ValueError):
        math_engine.compute_active_trailing_stop(
            symphony_vol=1.0,
            dynamic_multiplier=1.0,
            dynamic_min_stop=0.20,
            para_armed=False,
            breakeven_locked=True,
            parabolic_squeeze_multiplier=-0.25,
        )


# ===========================================================================
# AC-5 — Valid multipliers in (0, 1]: stop distance stays strictly positive
# ===========================================================================


@pytest.mark.parametrize(
    "case",
    VALID_CASES,
    ids=[c["label"] for c in VALID_CASES],
)
def test_positive_squeeze_multiplier_yields_strictly_positive_stop(case: dict[str, Any]) -> None:
    """
    AC-5: for parabolic_squeeze_multiplier in (0, 1] the function must NOT
    raise, and the returned stop distance must be a strictly positive float.

    We assert the INVARIANT (output is a positive float), not a hardcoded
    producer value — the project rule forbids hardcoding producer-computed
    numbers. The unsqueezed distance max(safe_vol*mult, dynamic_min_stop) is
    strictly positive, and multiplying by a value in (0, 1] keeps it
    strictly positive.

    Catches an impl that over-broadened the rejection (e.g. rejected
    multiplier < 1, or rejected the valid (0,1] range).
    """
    result = _call(case["inputs"])
    assert isinstance(result, float), (
        f"[{case['label']}] expected a float, got {type(result).__name__}"
    )
    assert result > 0.0, (
        f"[{case['label']}] expected a strictly positive stop distance for a "
        f"multiplier in (0, 1]; got {result}. The squeeze must tighten the "
        f"stop, never collapse it to <= 0."
    )


def test_small_positive_squeeze_multiplier_is_accepted() -> None:
    """
    AC-5 boundary: a small-but-positive multiplier (0.05) is VALID — only
    multiplier <= 0 is rejected. The fix must not over-reject; tight
    parabolic squeezes are a legitimate tuned regime.

    Asserts shape/sign only (strictly positive float), never a literal
    producer value.
    """
    result = math_engine.compute_active_trailing_stop(
        symphony_vol=2.0,
        dynamic_multiplier=1.5,
        dynamic_min_stop=0.30,
        para_armed=True,
        breakeven_locked=False,
        parabolic_squeeze_multiplier=0.05,
    )
    assert isinstance(result, float) and result > 0.0, (
        f"A small positive multiplier (0.05) is valid; expected a strictly "
        f"positive float, got {result!r}. Only multiplier <= 0 is rejected."
    )


def test_rejection_check_precedes_nan_check_or_coexists_cleanly() -> None:
    """
    AC-5: a non-finite multiplier must still raise ValueError (the existing
    _reject_non_finite policy is preserved). A NaN multiplier is also <= 0
    by no comparison being True, so the impl must not let NaN slip past the
    sign check. Either guard raising ValueError is acceptable.
    """
    with pytest.raises(ValueError):
        math_engine.compute_active_trailing_stop(
            symphony_vol=2.0,
            dynamic_multiplier=1.5,
            dynamic_min_stop=0.30,
            para_armed=True,
            breakeven_locked=False,
            parabolic_squeeze_multiplier=float("nan"),
        )
