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
def test_time_ratio_outside_unit_interval_raises_value_error(case: dict[str, Any]) -> None:
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
    case = next(c for c in VALID_ENDPOINT_CASES if c["label"] == "endpoint_zero_market_open")
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
    case = next(c for c in VALID_ENDPOINT_CASES if c["label"] == "endpoint_one_market_close")
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
        f"time_ratio={time_ratio} (in range) must return a 2-tuple; got {result!r}"
    )
    dm, dms = result
    assert isinstance(dm, float) and isinstance(dms, float), (
        f"time_ratio={time_ratio}: expected (float, float), got "
        f"({type(dm).__name__}, {type(dms).__name__})"
    )


# ===========================================================================
# AC-7 — Decay-curve provenance (post-M3 closure)
# ===========================================================================
#
# AC-7 history:
#   PRE-M3: the audit flagged the log10(1 + 9*t) decay curve as
#   provenance-light; the AC-7 RED required a sourced rationale OR a
#   follow-up-empirical-review flag. That precondition was correct for
#   the pre-M3 era.
#
#   POST-M3 (cycle fix-m3-provenance): the M3 redrive REPLACES the log10
#   heuristic with the THEORY-derived f(t) = 1 - sqrt(1 - t) from Danielsson
#   & Zigrand (2003, LSE FMG DP-439). DECAY_CURVE_SCALAR is removed
#   entirely. AC-7 is closed by the published-derivation citation, not by a
#   rationale comment.
#
#   This test is UPDATED to assert the M3 closure (citations to Danielsson
#   & Zigrand + a THEORY discipline declaration + the sqrt-formula text)
#   AND the absence of the pre-M3 self-flag language. M3 plan §49 + §72
#   authorize this rewrite.


def test_decay_curve_provenance_comment_cites_m3_closure() -> None:
    """
    AC-7 (post-M3): the decay-curve provenance comment block must cite the
    Danielsson & Zigrand (2003) square-root-of-time derivation, declare
    freeze_discipline = THEORY, and reference the M3 research note.

    Inverted from pre-M3 AC-7: pre-M3 asserted a rationale OR a follow-up
    flag was present in lines mentioning the decay curve; post-M3 asserts
    the THEORY closure citations are present AND the pre-M3 self-flag
    language is absent. The closure is THEORY because the new formula is
    closed-form (zero free parameters); no rationale is needed beyond the
    citation.

    Anchor: the comment block immediately above the (post-M3) MULT_OPEN
    constant. The block contains the full M3 PROVENANCE narrative. We
    extract ~25 lines preceding MULT_OPEN to capture the multi-line block
    without reaching upward into unrelated dataclass docs.
    """
    source = pathlib.Path(math_engine.__file__).read_text(encoding="utf-8")
    mult_open_idx = source.find("MULT_OPEN = 1.5")
    assert mult_open_idx != -1, (
        "MULT_OPEN = 1.5 not found in math_engine.py source -- impl-m3 "
        "removed or renamed the at-open multiplier endpoint, which is "
        "out of scope for M3 (M3 changes the curve SHAPE, not the endpoints)."
    )
    pre_lines = source[:mult_open_idx].splitlines()
    block = "\n".join(pre_lines[-25:]).lower()

    # Positive: M3 closure citations + THEORY discipline + sqrt formula.
    required_substrings = (
        "danielsson",
        "zigrand",
        "2003",
        "theory",
        "docs/research/m3-provenance/literature-pass.md",
    )
    missing = [s for s in required_substrings if s not in block]
    assert not missing, (
        f"M3 decay-curve provenance comment missing required substrings: "
        f"{missing}. AC-7 post-M3 requires the comment to cite Danielsson "
        f"& Zigrand (2003), declare THEORY discipline, and reference the "
        f"research note. Block scanned:\n{block}"
    )

    # Either 'sqrt' or 'square-root' / 'square root' satisfies the formula
    # reference -- the comment may use either spelling.
    assert "sqrt" in block or "square-root" in block or "square root" in block, (
        "M3 decay-curve provenance comment must reference the sqrt /"
        "square-root-of-time scaling that defines f(t) = 1 - sqrt(1 - t). "
        f"Block scanned:\n{block}"
    )

    # Negative: the pre-M3 self-flag MUST be removed.
    gap_phrases = (
        "no formal literature provenance",
        "no formal literature",
    )
    surviving = [p for p in gap_phrases if p in block]
    assert not surviving, (
        f"Pre-M3 self-flag survived the closure: {surviving}. The M3 "
        f"redrive replaces 'has no formal literature provenance' with the "
        f"Danielsson-Zigrand citation; leaving the flag behind defeats "
        f"the closure. Block scanned:\n{block}"
    )


def test_decay_curve_scalar_constant_is_removed_by_m3() -> None:
    """
    AC-7 (post-M3): DECAY_CURVE_SCALAR is REMOVED from math_engine. The
    pre-M3 heuristic used `log10(1 + DECAY_CURVE_SCALAR * t)` with the
    tuned scalar 9; the post-M3 THEORY formula is `1 - sqrt(1 - t)` with
    zero free parameters. Leaving the constant on the module post-M3
    would be a dead-code provenance debt.

    Same intent as
    tests/math_engine/test_time_squeeze_decay.py::test_decay_curve_scalar_constant_is_removed_post_m3
    but expressed against the module surface (this file's AC-7 home). Both
    must pass on a clean GREEN.
    """
    assert not hasattr(math_engine, "DECAY_CURVE_SCALAR"), (
        "DECAY_CURVE_SCALAR is still defined on math_engine post-M3. The "
        "M3 redrive (Danielsson & Zigrand 2003 square-root-of-time) has "
        "zero free parameters; the constant must be REMOVED to close the "
        "provenance gap. Research note: "
        "docs/research/m3-provenance/literature-pass.md §1.3."
    )
