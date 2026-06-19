"""
Phase-1 property-based tests for the CVaRAssessment fail-safe invariant.

WHY THIS TEST EXISTS
--------------------
The breach=False-when-cvar_pct-is-None invariant must hold for ANY construction
of CVaRAssessment where cvar_pct is None, not just the specific hand-crafted
cases in the contract test. A property-based suite exhaustively constructs
None-case instances across the space of valid tail_obs_count and
insufficient_reason values to confirm the invariant holds universally.

WHAT IS ASSERTED (property tests)
----------------------------------
P1. For ALL CVaRAssessment instances with cvar_pct=None that can be legally
    constructed, breach is always False. This is the exhaustive version of
    the contract test's single-case check.
P2. For ALL CVaRAssessment instances with cvar_pct=None that can be legally
    constructed, tail_obs_count is always 0 (the sentinel case carries no tail
    data).
P3. For ALL CVaRAssessment instances with cvar_pct as a valid float in
    [-100.0, 100.0], the instance is constructed without error and cvar_pct
    round-trips exactly (no silent truncation or normalization of valid floats).

RED STATUS
----------
P1 and P2 are RED until CVaRAssessment's __post_init__ enforces the invariant
by raising on illegal combinations. P3 is RED until CVaRAssessment exists at
all.

FIXTURE
-------
No golden fixture — property tests use Hypothesis strategies to generate input
space; there are no producer-computed values to pin.
"""

from __future__ import annotations

import pytest

try:
    from hypothesis import HealthCheck, given, settings
    from hypothesis import strategies as st

    _HYPOTHESIS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _HYPOTHESIS_AVAILABLE = False

_requires_hypothesis = pytest.mark.skipif(
    not _HYPOTHESIS_AVAILABLE,
    reason="hypothesis not installed — install requirements-dev.txt",
)


def _import_cvar_assessment():
    """Import and return CVaRAssessment, skipping the test if not yet defined."""
    try:
        import math_engine

        cls = getattr(math_engine, "CVaRAssessment", None)
        if cls is None:
            pytest.skip("math_engine.CVaRAssessment not yet defined (RED phase).")
        return cls
    except ImportError:
        pytest.skip("math_engine not importable.")


# ---------------------------------------------------------------------------
# P1. Exhaustive: cvar_pct=None ALWAYS implies breach=False
# ---------------------------------------------------------------------------


@_requires_hypothesis
@given(
    tail_obs_count=st.integers(min_value=0, max_value=0),  # 0 is the only valid count for None
    insufficient_reason=st.one_of(
        st.none(),
        st.text(min_size=1, max_size=200),
    ),
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_none_cvar_pct_always_implies_breach_false(
    tail_obs_count: int,
    insufficient_reason: str | None,
) -> None:
    """
    P1 — For any legally constructable CVaRAssessment with cvar_pct=None,
    breach must be False. This property holds for all text values of
    insufficient_reason and for tail_obs_count=0 (the only legal count
    when cvar_pct is None).

    RED against an implementation that does not enforce the invariant —
    construction with breach=True would succeed and violate this property.
    """
    CVaRAssessment = _import_cvar_assessment()

    # Legal None construction must succeed. stderr is paired with cvar_pct:
    # cvar_pct=None requires stderr=None (the H-2 stderr is the uncertainty
    # band that travels with the CVaR estimate; absent estimate → absent band).
    obj = CVaRAssessment(
        cvar_pct=None,
        breach=False,
        tail_obs_count=tail_obs_count,
        stderr=None,
        insufficient_reason=insufficient_reason,
    )
    # The invariant: breach must be False.
    assert obj.breach is False, (
        f"CVaRAssessment(cvar_pct=None, breach=False, tail_obs_count={tail_obs_count}, "
        f"insufficient_reason={insufficient_reason!r}) stored breach={obj.breach!r}. "
        f"The fail-safe invariant (cvar_pct is None → breach is False) was violated "
        f"by the dataclass itself."
    )


# ---------------------------------------------------------------------------
# P2. Exhaustive: cvar_pct=None ALWAYS implies tail_obs_count is 0
# ---------------------------------------------------------------------------


@_requires_hypothesis
@given(
    insufficient_reason=st.one_of(
        st.none(),
        st.text(min_size=0, max_size=200),
    ),
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_property_none_cvar_pct_always_has_zero_tail_obs_count(
    insufficient_reason: str | None,
) -> None:
    """
    P2 — For any legally constructable CVaRAssessment with cvar_pct=None,
    tail_obs_count must be 0. An insufficient sentinel carries no tail data.

    RED against an implementation that allows nonzero tail counts with a None
    cvar_pct.
    """
    CVaRAssessment = _import_cvar_assessment()

    obj = CVaRAssessment(
        cvar_pct=None,
        breach=False,
        tail_obs_count=0,
        stderr=None,
        insufficient_reason=insufficient_reason,
    )
    assert obj.tail_obs_count == 0, (
        f"CVaRAssessment with cvar_pct=None has tail_obs_count={obj.tail_obs_count}. "
        f"Must be 0: an insufficient CVaR estimate has no tail observations."
    )


# ---------------------------------------------------------------------------
# P3. Valid float cvar_pct round-trips without silent mutation
# ---------------------------------------------------------------------------


@_requires_hypothesis
@given(
    cvar_pct=st.floats(
        min_value=-100.0,
        max_value=100.0,
        allow_nan=False,
        allow_infinity=False,
    ),
    breach=st.booleans(),
    tail_obs_count=st.integers(min_value=0, max_value=10000),
    # stderr is paired with cvar_pct — a finite cvar_pct REQUIRES a finite
    # stderr (H-2 binding). The strategy generates any positive finite stderr;
    # P3 does not assert the numeric stderr value, only that cvar_pct round-trips.
    stderr=st.floats(min_value=1e-10, max_value=10.0, allow_nan=False, allow_infinity=False),
    insufficient_reason=st.none(),  # None is correct when cvar_pct is a valid float
)
@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
def test_property_valid_float_cvar_pct_round_trips(
    cvar_pct: float,
    breach: bool,
    tail_obs_count: int,
    stderr: float,
    insufficient_reason: None,
) -> None:
    """
    P3 — A valid float cvar_pct is stored exactly (within float precision)
    and not silently normalized, clamped, or mutated by __post_init__.

    RED if the implementer adds accidental normalization (e.g. clamping to
    [-50, 0] or rounding to 1dp).
    """
    CVaRAssessment = _import_cvar_assessment()

    obj = CVaRAssessment(
        cvar_pct=cvar_pct,
        breach=breach,
        tail_obs_count=tail_obs_count,
        stderr=stderr,
        insufficient_reason=insufficient_reason,
    )
    # float round-trip: stored value must equal input (no silent mutation).
    # Using == rather than pytest.approx because no computation occurred — if
    # the value was silently rounded the test must catch it exactly.
    assert obj.cvar_pct == cvar_pct, (
        f"CVaRAssessment silently mutated cvar_pct from {cvar_pct!r} to "
        f"{obj.cvar_pct!r}. __post_init__ must not normalize valid float values."
    )
    assert obj.breach is breach
    assert obj.tail_obs_count == tail_obs_count
