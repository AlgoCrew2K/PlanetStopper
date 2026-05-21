"""
RED tests for AC-6 (audit finding M-1): compute_time_squeeze_decay must raise
an explicit ValueError on a time_ratio outside the closed interval [0, 1].

Background (math-audit, MEDIUM finding):

  compute_time_squeeze_decay computes
      decay_curve = log10(1 + DECAY_CURVE_SCALAR * time_ratio)
  The caller currently clamps time_ratio to [0, 1] before passing, so
  production is safe — but the pure function does not validate. Two failure
  modes if an unclamped value reaches it:
    - time_ratio < -1/9 makes (1 + 9*t) <= 0, so math.log10 raises an opaque
      "math domain error" crash on the live execution path.
    - time_ratio > 1 over-tightens the stop below MULT_CLOSE / MIN_STOP_CLOSE
      silently.

  Decision (plan D1 / M-1 handling): raise ValueError on out-of-range
  time_ratio — consistent with math_engine's reject-don't-coerce policy for
  non-finite inputs. NOT a silent clamp.

GREEN target: compute_time_squeeze_decay raises ValueError when time_ratio
< 0 or time_ratio > 1, BEFORE the log10 call. The endpoints 0.0 and 1.0
remain valid and yield the documented MULT_OPEN/MULT_CLOSE and
MIN_STOP_OPEN/MIN_STOP_CLOSE values.

Tolerance policy:
  - Rejection assertions: exact — pytest.raises(ValueError).
  - Endpoint assertions: pytest.approx(rel=1e-9, abs=1e-12). At t=0,
    decay_curve = log10(1) = 0.0 exactly; at t=1, log10(10) = 1.0 exactly.
    The expected MULT_*/MIN_STOP_* values are sourced from the named
    module-level constants, NOT hardcoded — see the assertions below.

Provenance: cases from tests/fixtures/math/time_squeeze_range/
time_squeeze_range_rejection_cases.json. Endpoint expecteds are
cross-checked against the math_engine named constants at runtime.
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
    / "time_squeeze_range"
    / "time_squeeze_range_rejection_cases.json"
)

APPROX_REL = 1e-9
APPROX_ABS = 1e-12


def _load() -> dict[str, Any]:
    with FIXTURE.open("r", encoding="utf-8") as fh:
        return json.load(fh)


_DATA = _load()
REJECT_CASES = _DATA["reject_cases"]
VALID_ENDPOINT_CASES = _DATA["valid_endpoint_cases"]


# ===========================================================================
# AC-6 — Rejection: time_ratio outside [0, 1] raises ValueError
# ===========================================================================


@pytest.mark.parametrize(
    "case",
    REJECT_CASES,
    ids=[c["label"] for c in REJECT_CASES],
)
def test_time_ratio_outside_unit_interval_raises_value_error(
    case: dict[str, Any]
) -> None:
    """
    AC-6 (M-1): compute_time_squeeze_decay must raise ValueError for
    time_ratio < 0 or time_ratio > 1.

    Cases (from fixture):
      - time_ratio just below 0 (-0.0001)
      - time_ratio in the log10 domain-crash region (-0.5, -10.0): without
        the fix math.log10(1 + 9*t) raises an opaque "math domain error"
      - time_ratio just above 1 (1.0001) and far above (5.0): silent
        over-tightening of the stop below MULT_CLOSE/MIN_STOP_CLOSE

    RED: the current impl does not validate time_ratio range — it only
    rejects non-finite values via _reject_non_finite.
    """
    with pytest.raises(ValueError):
        math_engine.compute_time_squeeze_decay(case["time_ratio"])


def test_negative_time_ratio_raises_value_error_not_math_domain_error() -> None:
    """
    AC-6: the explicit failure mode the audit named. A time_ratio of -0.5
    makes 1 + 9*(-0.5) = -3.5 <= 0; math.log10(-3.5) raises ValueError with
    the opaque message "math domain error".

    The fix must raise a ValueError whose message names time_ratio /
    out-of-range, so an operator reading the daemon log sees the real cause
    rather than a cryptic log10 crash.

    This test asserts a ValueError is raised; it additionally checks the
    message is NOT the bare math-domain-error string, proving the function
    validated the range itself rather than letting log10 blow up.
    """
    with pytest.raises(ValueError) as exc_info:
        math_engine.compute_time_squeeze_decay(-0.5)

    message = str(exc_info.value).lower()
    assert "math domain error" != message, (
        "compute_time_squeeze_decay let math.log10 crash with the opaque "
        "'math domain error' message. AC-6 requires an explicit range "
        "validation that raises a clear ValueError BEFORE the log10 call."
    )
    assert "time_ratio" in message or "range" in message or "0" in message, (
        f"The ValueError message should identify time_ratio / the valid "
        f"range so an operator can diagnose it. Got: {exc_info.value!r}"
    )


def test_time_ratio_above_one_raises_rather_than_over_tightening() -> None:
    """
    AC-6: time_ratio > 1 does NOT crash log10 (1 + 9*t stays positive) but
    silently over-tightens — decay_curve > 1 pushes dynamic_multiplier below
    MULT_CLOSE and dynamic_min_stop below MIN_STOP_CLOSE, a stop tighter
    than the documented end-of-session floor. The fix must reject it.
    """
    with pytest.raises(ValueError):
        math_engine.compute_time_squeeze_decay(1.5)


# ===========================================================================
# AC-6 — Endpoints 0.0 and 1.0 remain valid and exact
# ===========================================================================


def test_endpoint_zero_yields_market_open_values() -> None:
    """
    AC-6 (no over-rejection): time_ratio == 0.0 is the VALID lower endpoint
    (market open). It must NOT raise and must yield MULT_OPEN /
    MIN_STOP_OPEN exactly (decay_curve = log10(1) = 0.0).

    Expected values are read from the math_engine named constants at
    runtime — NOT hardcoded literals — so the test stays correct if the
    constants are retuned.
    """
    case = next(
        c for c in VALID_ENDPOINT_CASES if c["label"] == "endpoint_zero_market_open"
    )
    dynamic_multiplier, dynamic_min_stop = math_engine.compute_time_squeeze_decay(
        case["time_ratio"]
    )
    assert dynamic_multiplier == pytest.approx(
        math_engine.MULT_OPEN, rel=APPROX_REL, abs=APPROX_ABS
    ), (
        f"time_ratio=0.0 must yield dynamic_multiplier == MULT_OPEN "
        f"({math_engine.MULT_OPEN}); got {dynamic_multiplier}."
    )
    assert dynamic_min_stop == pytest.approx(
        math_engine.MIN_STOP_OPEN, rel=APPROX_REL, abs=APPROX_ABS
    ), (
        f"time_ratio=0.0 must yield dynamic_min_stop == MIN_STOP_OPEN "
        f"({math_engine.MIN_STOP_OPEN}); got {dynamic_min_stop}."
    )
    # Cross-check the fixture's recorded expected matches the named constant
    # (guards against fixture drift).
    assert case["expected"]["dynamic_multiplier"] == pytest.approx(
        math_engine.MULT_OPEN, rel=APPROX_REL, abs=APPROX_ABS
    ), "Fixture endpoint_zero dynamic_multiplier drifted from MULT_OPEN."


def test_endpoint_one_yields_market_close_values() -> None:
    """
    AC-6 (no over-rejection): time_ratio == 1.0 is the VALID upper endpoint
    (market close). It must NOT raise and must yield MULT_CLOSE /
    MIN_STOP_CLOSE exactly (decay_curve = log10(10) = 1.0).

    Expected values read from the named constants at runtime.
    """
    case = next(
        c for c in VALID_ENDPOINT_CASES if c["label"] == "endpoint_one_market_close"
    )
    dynamic_multiplier, dynamic_min_stop = math_engine.compute_time_squeeze_decay(
        case["time_ratio"]
    )
    assert dynamic_multiplier == pytest.approx(
        math_engine.MULT_CLOSE, rel=APPROX_REL, abs=APPROX_ABS
    ), (
        f"time_ratio=1.0 must yield dynamic_multiplier == MULT_CLOSE "
        f"({math_engine.MULT_CLOSE}); got {dynamic_multiplier}."
    )
    assert dynamic_min_stop == pytest.approx(
        math_engine.MIN_STOP_CLOSE, rel=APPROX_REL, abs=APPROX_ABS
    ), (
        f"time_ratio=1.0 must yield dynamic_min_stop == MIN_STOP_CLOSE "
        f"({math_engine.MIN_STOP_CLOSE}); got {dynamic_min_stop}."
    )
    assert case["expected"]["dynamic_multiplier"] == pytest.approx(
        math_engine.MULT_CLOSE, rel=APPROX_REL, abs=APPROX_ABS
    ), "Fixture endpoint_one dynamic_multiplier drifted from MULT_CLOSE."


@pytest.mark.parametrize("time_ratio", [0.0, 0.25, 0.5, 0.75, 1.0])
def test_in_range_time_ratio_does_not_raise(time_ratio: float) -> None:
    """
    AC-6 (no over-rejection): every time_ratio inside the closed interval
    [0, 1] is valid and must NOT raise. Pins that the range guard rejects
    ONLY out-of-range values — a guard that used a half-open interval or a
    strict inequality would wrongly reject 0.0 or 1.0.

    Asserts only that the call returns a 2-tuple of floats (shape), never a
    hardcoded producer value for the interior points.
    """
    result = math_engine.compute_time_squeeze_decay(time_ratio)
    assert isinstance(result, tuple) and len(result) == 2, (
        f"time_ratio={time_ratio} (in range) must return a 2-tuple; "
        f"got {result!r}"
    )
    dm, dms = result
    assert isinstance(dm, float) and isinstance(dms, float), (
        f"time_ratio={time_ratio}: expected (float, float), got "
        f"({type(dm).__name__}, {type(dms).__name__})"
    )


# ===========================================================================
# AC-7 — Decay-curve provenance comment
# ===========================================================================


def test_decay_curve_carries_sourced_rationale_comment() -> None:
    """
    AC-7 (M-4): the log10(1 + 9*t) decay curve must carry a sourced inline
    rationale for its concave shape, OR be explicitly flagged for a
    follow-up empirical review. The audit flagged DECAY_CURVE_SCALAR / the
    decay curve as provenance-light.

    This test asserts the source region around compute_time_squeeze_decay /
    DECAY_CURVE_SCALAR contains rationale language — it does not pin exact
    wording, only that the GREEN phase added a rationale or a review flag.

    RED: the current DECAY_CURVE_SCALAR comment describes WHAT the curve does
    ("maps t in [0,1] to decay in [0,1]") but gives no rationale for WHY a
    concave log curve was chosen.
    """
    source = pathlib.Path(math_engine.__file__).read_text(encoding="utf-8")
    # Look at the lines mentioning the decay curve / scalar.
    relevant = "\n".join(
        line
        for line in source.splitlines()
        if "DECAY_CURVE" in line
        or "decay_curve" in line
        or "time-squeeze" in line.lower()
        or "time squeeze" in line.lower()
    ).lower()
    rationale_markers = (
        "concave",
        "rationale",
        "empirical",
        "follow-up",
        "follow up",
        "tuned",
        "practitioner",
        "review",
        "source:",
        "because",
    )
    assert any(m in relevant for m in rationale_markers), (
        "The log10(1 + 9*t) decay curve has no sourced rationale for its "
        "concave shape and no explicit follow-up-review flag. AC-7 requires "
        "one or the other. Add an inline comment explaining WHY a concave "
        "log curve (faster early tightening, slower late) was chosen, or "
        "flag it for empirical review."
    )
