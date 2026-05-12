"""
Golden-fixture + property tests for the parabolic-squeeze arming layer of
math_engine.py.

Scope (Gate-1 approved): NEW pure function math_engine.compute_para_arm_decision,
extracted from alpha_bot_execution.py:559-569. This is a refactor-extraction
cycle, not a new feature; the inline producer (those 11 source lines) is the
spec the GREEN phase must preserve.

Inline producer pinned verbatim (alpha_bot_execution.py:559-569 on main):

    # --- PARABOLIC SQUEEZE LOGIC ---
    prev_return = bot_state[symphony_id].get("prev_return", current_return)
    velocity = current_return - prev_return
    bot_state[symphony_id]["prev_return"] = current_return

    para_threshold = acc_params.get(
        "PARABOLIC_VELOCITY_THRESHOLD", PARABOLIC_VELOCITY_THRESHOLD
    )
    if velocity >= para_threshold:
        if not bot_state[symphony_id]["para_armed"]:
            bot_state[symphony_id]["para_armed"] = True
            print(...)
            database.log_symphony_event(...)

PROPOSED PURE INTERFACE (confirmed -- no revision; the gate-2 proposal already
isolates the smallest non-trivial unit: velocity computation + arming decision.
Splitting velocity out as a separate function would force every caller to make
two calls and pass the intermediate, raising boilerplate without isolating a
distinct mathematical concept. Returning the tuple is the right unit.):

    def compute_para_arm_decision(
        current_return: float,
        prev_return: float,
        para_threshold: float,
        currently_armed: bool,
    ) -> tuple[float, bool]:
        '''Returns (velocity, should_arm_transition).'''

The function is PURE: no state mutation, no I/O, no DB writes. Caller owns
storing prev_return, mutating para_armed on transition=True, logging, and
emitting the database event.

Tolerance policy:
- velocity: pytest.approx(rel=1e-9, abs=1e-12). All fixture inputs are chosen
  so the subtraction is exact in IEEE-754 binary (no float-noise excuse), but
  we keep the tolerance to absorb harmless representation drift if the GREEN
  impl adds intermediate operations.
- should_arm: exact bool equality. There is no tolerance for a boolean.

Provenance (HARD): every expected (velocity, should_arm) in
tests/fixtures/math_engine/parabolic_squeeze/*.json is DERIVED BY HAND from
the inline producer's formula and pinned in the fixture's 'derivation' field
-- NOT captured from a current implementation (compute_para_arm_decision does
not exist yet; this is RED). The math is trivial enough (subtraction +
comparison + AND) that hand-derivation is the canonical source of truth.

Adversarial fixture intent (each fixture targets a SPECIFIC class of wrong impl):
- Fixture 02 (negative_velocity): catches subtraction-order flip
  (prev - current instead of current - prev).
- Fixture 03 (velocity == threshold): catches > vs >= confusion -- inline
  uses >=, so this boundary MUST arm.
- Fixture 04 (just under threshold): the exclusive flip side of fixture 03.
- Fixture 06 (already armed): catches an impl that ignores currently_armed
  and re-arms on every qualifying velocity.
- Fixture 08 (custom threshold = 3.5): catches an impl that hardcodes the
  threshold (e.g., a default-typo) instead of using the parameter.
- Fixture 09 (zero velocity, zero threshold): another > vs >= probe, plus
  catches a sloppy "if velocity:" truthiness check that special-cases zero.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

import math_engine

FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent / "fixtures" / "math_engine" / "parabolic_squeeze"
)

# --- Tolerance --------------------------------------------------------------

# rel=1e-9 catches any algorithmic divergence (e.g., a scaling factor or a
# clamp); abs=1e-12 lets exact-zero expected velocities match cleanly. A wrong
# impl will miss by orders of magnitude (sign flip, missing subtraction,
# bogus scaling), not by 1 ulp.
APPROX_REL = 1e-9
APPROX_ABS = 1e-12


# --- Fixture discovery ------------------------------------------------------


def _load_fixtures() -> list[tuple[str, dict[str, Any]]]:
    paths = sorted(FIXTURE_DIR.glob("*.json"))
    out: list[tuple[str, dict[str, Any]]] = []
    for p in paths:
        with p.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        out.append((p.name, data))
    return out


FIXTURES = _load_fixtures()


# --- Golden-fixture parametrized test --------------------------------------


@pytest.mark.parametrize(
    "fixture_name,fixture",
    FIXTURES,
    ids=[name for name, _ in FIXTURES],
)
def test_para_arm_matches_derived_expected(
    fixture_name: str, fixture: dict[str, Any]
) -> None:
    """
    Every fixture's expected (velocity, should_arm) is DERIVED BY HAND in the
    fixture's 'derivation' field. This test asserts compute_para_arm_decision
    produces that tuple. Function does not exist yet -- this is RED.
    """
    func_name = fixture["function"]
    assert func_name == "compute_para_arm_decision", (
        f"{fixture_name}: only compute_para_arm_decision is in scope for this cycle"
    )

    inputs = fixture["inputs"]
    expected = fixture["expected"]

    velocity, should_arm = math_engine.compute_para_arm_decision(
        current_return=inputs["current_return"],
        prev_return=inputs["prev_return"],
        para_threshold=inputs["para_threshold"],
        currently_armed=inputs["currently_armed"],
    )

    assert velocity == pytest.approx(
        expected["velocity"], rel=APPROX_REL, abs=APPROX_ABS
    ), (
        f"Fixture {fixture_name}: expected velocity {expected['velocity']} "
        f"(derivation: {fixture['derivation']}), got {velocity}"
    )
    # Exact bool equality -- no tolerance for a boolean.
    assert should_arm is expected["should_arm"], (
        f"Fixture {fixture_name}: expected should_arm {expected['should_arm']} "
        f"(derivation: {fixture['derivation']}), got {should_arm}"
    )


# --- Property: velocity formula ---------------------------------------------


@pytest.mark.parametrize(
    "current_return,prev_return",
    [
        (0.0, 0.0),
        (1.0, 0.0),
        (0.0, 1.0),
        (-1.0, -1.0),
        (5.5, 2.25),
        (-3.5, 7.5),
        (1e-9, 0.0),
        (1e9, 1e9 - 1.0),
        (0.125, 0.0625),  # both exactly representable in binary float
        (100.0, -100.0),
    ],
)
def test_velocity_is_identically_current_minus_prev(
    current_return: float, prev_return: float
) -> None:
    """
    Invariant: velocity = current_return - prev_return EXACTLY (no scaling, no
    clamping, no abs(), no sign-flip). Sweeps signs, magnitudes, and
    exactly-representable pairs to catch a subtraction-order flip, an
    absolute-value, or a percent-to-decimal scaling drift.
    """
    velocity, _ = math_engine.compute_para_arm_decision(
        current_return=current_return,
        prev_return=prev_return,
        para_threshold=0.5,
        currently_armed=False,
    )
    expected_velocity = current_return - prev_return
    assert velocity == pytest.approx(
        expected_velocity, rel=APPROX_REL, abs=APPROX_ABS
    ), (
        f"velocity not equal to current_return - prev_return: "
        f"current={current_return}, prev={prev_return}, "
        f"expected={expected_velocity}, got={velocity}"
    )


# --- Property: should_arm iff (velocity >= threshold) AND (not armed) ------


@pytest.mark.parametrize(
    "current_return,prev_return,threshold",
    [
        # (velocity, threshold) pairs designed to exercise both sides of >=
        # at multiple magnitudes; subtraction is exact in binary for these
        # inputs so there is no float-noise smudging the comparison.
        (1.0, 0.0, 0.5),  # velocity 1.0  >= 0.5 -> True
        (0.5, 0.0, 0.5),  # velocity 0.5  >= 0.5 -> True (inclusive boundary)
        (0.25, 0.0, 0.5),  # velocity 0.25 >= 0.5 -> False
        (0.0, 0.0, 0.0),  # velocity 0.0  >= 0.0 -> True
        (-0.5, 0.0, 0.0),  # velocity -0.5 >= 0.0 -> False
        (2.0, 1.0, 1.0),  # velocity 1.0  >= 1.0 -> True
        (4.0, 1.0, 3.5),  # velocity 3.0  >= 3.5 -> False
        (5.0, 1.0, 3.5),  # velocity 4.0  >= 3.5 -> True
    ],
)
def test_should_arm_iff_velocity_geq_threshold_and_not_armed(
    current_return: float, prev_return: float, threshold: float
) -> None:
    """
    Invariant: should_arm == (velocity >= threshold) AND (not currently_armed).
    Verified in BOTH directions of currently_armed for every (velocity,
    threshold) pair. Catches:
      - > vs >= confusion at the inclusive boundary.
      - An impl that ignores currently_armed.
      - An impl that hardcodes the threshold.
    """
    velocity_expected = current_return - prev_return

    for currently_armed in (False, True):
        velocity, should_arm = math_engine.compute_para_arm_decision(
            current_return=current_return,
            prev_return=prev_return,
            para_threshold=threshold,
            currently_armed=currently_armed,
        )
        # Velocity must be identical regardless of arm state.
        assert velocity == pytest.approx(
            velocity_expected, rel=APPROX_REL, abs=APPROX_ABS
        ), (
            f"velocity changed with currently_armed={currently_armed}: "
            f"expected {velocity_expected}, got {velocity}"
        )
        expected_should_arm = (velocity_expected >= threshold) and (not currently_armed)
        assert should_arm is expected_should_arm, (
            f"should_arm wrong at velocity={velocity_expected}, threshold={threshold}, "
            f"currently_armed={currently_armed}: expected {expected_should_arm}, "
            f"got {should_arm}"
        )


# --- Property: monotonicity in current_return at fixed prev/threshold ------


def test_monotonic_in_current_return_when_not_armed() -> None:
    """
    Invariant: with prev_return and threshold fixed and currently_armed=False,
    should_arm is a step function: False for current_return < prev_return +
    threshold, True for current_return >= prev_return + threshold. Sweep
    current_return across both sides of the step. A wrong impl that bucketed
    or used the wrong operator would break monotonicity here.
    """
    prev_return = 1.0
    threshold = 0.5
    # step crossover: current_return = prev_return + threshold = 1.5
    cases = [
        (0.0, False),
        (0.5, False),
        (1.0, False),
        (1.25, False),
        (1.4999, False),
        (1.5, True),  # inclusive boundary
        (1.5001, True),
        (2.0, True),
        (10.0, True),
    ]
    for current_return, expected in cases:
        _, should_arm = math_engine.compute_para_arm_decision(
            current_return=current_return,
            prev_return=prev_return,
            para_threshold=threshold,
            currently_armed=False,
        )
        assert should_arm is expected, (
            f"Monotonicity broken at current_return={current_return}: "
            f"expected should_arm={expected}, got {should_arm}"
        )


# --- Property: currently_armed=True suppresses arming unconditionally ------


@pytest.mark.parametrize(
    "current_return,prev_return,threshold",
    [
        (5.0, 0.0, 0.5),  # huge velocity
        (1.0, 0.0, 0.0),  # threshold zero
        (0.0, 0.0, 0.0),  # zero everywhere
        (100.0, 1.0, 0.5),  # extreme velocity
        (1.5, 1.0, 0.5),  # velocity exactly at threshold
        (1e-9, 0.0, 0.0),  # tiny positive velocity
        (-1.0, 0.0, -2.0),  # negative threshold (degenerate but valid input)
    ],
)
def test_currently_armed_true_suppresses_arming(
    current_return: float, prev_return: float, threshold: float
) -> None:
    """
    Invariant: currently_armed=True -> should_arm=False UNCONDITIONALLY,
    regardless of velocity or threshold. Pinned across a sweep that includes
    cases that WOULD arm if currently_armed were False, to catch an impl
    that ignores currently_armed.
    """
    _, should_arm = math_engine.compute_para_arm_decision(
        current_return=current_return,
        prev_return=prev_return,
        para_threshold=threshold,
        currently_armed=True,
    )
    assert should_arm is False, (
        f"currently_armed=True did NOT suppress arming at "
        f"current_return={current_return}, prev_return={prev_return}, "
        f"threshold={threshold}: got should_arm={should_arm}"
    )


# --- Property: pure function (deterministic + no side effects) -------------


def test_function_is_pure_repeat_call_returns_identical_result() -> None:
    """
    Sanity: a pure function returns identical results when called twice with
    the same arguments. Catches an impl that accidentally stashes state in a
    module-level variable or has a hidden time-dependence.
    """
    args = {
        "current_return": 2.5,
        "prev_return": 1.0,
        "para_threshold": 0.5,
        "currently_armed": False,
    }
    v1, a1 = math_engine.compute_para_arm_decision(**args)
    v2, a2 = math_engine.compute_para_arm_decision(**args)
    assert v1 == v2, f"Non-deterministic velocity: {v1} vs {v2}"
    assert a1 is a2, f"Non-deterministic should_arm: {a1} vs {a2}"


def test_function_does_not_mutate_inputs() -> None:
    """
    Pure-function contract: the function must not mutate its inputs. The
    inputs here are floats and a bool (immutable in Python), so this is
    documentary -- but it pins the intent so a future signature change (e.g.,
    accepting a dict) doesn't silently introduce mutation.
    """
    # Sentinel values we can compare against post-call. For immutable types
    # this is tautological, but the test PIN says "don't mutate" -- so if a
    # future refactor swaps to a mutable container, this test must be
    # updated to do a deep-equal post-call check.
    current_return = 2.5
    prev_return = 1.0
    para_threshold = 0.5
    currently_armed = False
    math_engine.compute_para_arm_decision(
        current_return=current_return,
        prev_return=prev_return,
        para_threshold=para_threshold,
        currently_armed=currently_armed,
    )
    assert current_return == 2.5
    assert prev_return == 1.0
    assert para_threshold == 0.5
    assert currently_armed is False


# --- Property: return type contract ----------------------------------------


def test_return_types_are_float_and_bool() -> None:
    """
    Contract: the function returns (float, bool). A wrong impl that returned
    (int, int) or (np.float64, np.bool_) would still pass the value checks
    above but break downstream callers that type-check or persist the result.
    Numpy scalars are NOT acceptable here: bool(np.True_) works but
    `x is True` does not, which makes the golden tests' `is` checks pass
    inconsistently.
    """
    velocity, should_arm = math_engine.compute_para_arm_decision(
        current_return=2.5,
        prev_return=1.0,
        para_threshold=0.5,
        currently_armed=False,
    )
    # float (not int, not np.float64). isinstance(True, int) is True in
    # Python, so we check bool first.
    assert isinstance(should_arm, bool), (
        f"should_arm must be a Python bool, got {type(should_arm).__name__}"
    )
    assert isinstance(velocity, float), (
        f"velocity must be a Python float, got {type(velocity).__name__}"
    )
