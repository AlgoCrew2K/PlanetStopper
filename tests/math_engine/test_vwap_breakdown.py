"""
Golden-fixture + property tests for the VWAP-breakdown state-machine layer of
math_engine.py.

Scope (Gate-1 approved): NEW pure function
math_engine.compute_vwap_breakdown_update, extracted from
alpha_bot_execution.py:656-682 (cycle 10 -- VWAP block C of 3, FINAL VWAP
extraction; largest function in the project to date with 10 inputs, a
4-tuple output, 3 outer state-machine branches, and 2 independent inner
sub-systems). This is a refactor-extraction cycle, not a new feature; the
inline producer (those 27 source lines on main, post cycle-8 + cycle-9
merges) is the spec the GREEN phase must preserve.

Inline producer pinned verbatim (alpha_bot_execution.py:656-682 on main
after cycle 9 merge):

    # Check 3: True VWAP Breakdown
    is_vwap_broken = False
    is_vwap_bleed_broken = False

    # ADDED GATE: Only evaluate if the symphony hasn't already exited
    if not bot_state[symphony_id]['triggered']:
        if valid_vwap_weight > 0.5 and weighted_vwap_diff < 0:
            # System A (Profit):
            if safe_hwm >= acc_VWAP_CROSS_HWM_PCT and current_return < safe_hwm:
                bot_state[symphony_id]['vwap_ticks'] += 1
                if bot_state[symphony_id]['vwap_ticks'] >= 3:
                    is_vwap_broken = True
                    print(...)
            else:
                bot_state[symphony_id]['vwap_ticks'] = 0

            # System B (Bleed):
            if current_return <= acc_VWAP_BLEED_ARM_PCT:
                bot_state[symphony_id]['vwap_bleed_ticks'] += 1
                if bot_state[symphony_id]['vwap_bleed_ticks'] >= acc_VWAP_BLEED_TICKS:
                    is_vwap_bleed_broken = True
                    print(...)
            else:
                bot_state[symphony_id]['vwap_bleed_ticks'] = 0
        else:
            bot_state[symphony_id]['vwap_ticks'] = 0
            bot_state[symphony_id]['vwap_bleed_ticks'] = 0

PROPOSED PURE INTERFACE (confirmed):

    def compute_vwap_breakdown_update(
        is_triggered: bool,
        valid_vwap_weight: float,
        weighted_vwap_diff: float,
        safe_hwm: float,
        current_return: float,
        vwap_cross_hwm_pct: float,
        vwap_bleed_arm_pct: float,
        vwap_bleed_ticks_threshold: int,
        current_vwap_ticks: int,
        current_vwap_bleed_ticks: int,
    ) -> tuple[int, int, bool, bool]:
        '''
        Returns (new_vwap_ticks, new_vwap_bleed_ticks, is_vwap_broken,
        is_vwap_bleed_broken).

        Outer guard (is_triggered=True): both ticks PRESERVED at their input
        values; both broken flags False.

        Outer gate fail (NOT (weight > VWAP_WEIGHT_THRESHOLD AND diff < 0)):
        both ticks RESET to 0; both broken flags False.

        Otherwise: System A and System B run INDEPENDENTLY with their own
        increment / reset semantics, sharing only the outer guard + gate.

        Pure. No I/O. No state.
        '''

LOAD-BEARING INVARIANTS (state-machine):
  - TRIGGERED ABSOLUTENESS: when is_triggered=True, return
    (current_vwap_ticks, current_vwap_bleed_ticks, False, False) for ANY
    other inputs. Counters neither incremented nor reset.
  - SYSTEM-A GATE [H2 DECOUPLED]: when is_triggered=False AND
    NOT (valid_vwap_weight > VWAP_WEIGHT_THRESHOLD AND
         weighted_vwap_diff < 0), the SYSTEM-A counter resets to 0 and
    is_vwap_broken is False. This gate NO LONGER wipes System B. System B
    is independent of the VWAP weight/sign gate and follows its OWN arm
    condition (below) on every non-triggered tick. (Pre-H2 the code wrongly
    wiped BOTH counters here — the bug that reset a live bleed ladder on an
    unrelated VWAP bounce / coverage drop. See validation/VERDICT.md H2.)
  - SYSTEM A SEMANTICS (when outer guard passes AND the System-A gate passes):
        condition := (safe_hwm >= vwap_cross_hwm_pct AND
                      current_return < safe_hwm)
        if condition: new_vwap_ticks = current_vwap_ticks + 1
        else:         new_vwap_ticks = 0
        is_vwap_broken := (condition AND
                           new_vwap_ticks >= VWAP_BREAK_CONFIRM_TICKS)
  - SYSTEM B SEMANTICS [H2 DECOUPLED] (when outer guard passes; INDEPENDENT of
    the System-A weight/sign gate — evaluated on EVERY non-triggered tick):
        condition := (current_return <= vwap_bleed_arm_pct)
        if condition: new_vwap_bleed_ticks = current_vwap_bleed_ticks + 1
        else:         new_vwap_bleed_ticks = 0
        is_vwap_bleed_broken := (condition AND
                                 new_vwap_bleed_ticks >=
                                 vwap_bleed_ticks_threshold)
  - INDEPENDENCE: changing any System-A-only input (safe_hwm,
    vwap_cross_hwm_pct, current_vwap_ticks) does not change new_b or
    is_vwap_bleed_broken; changing any System-B-only input
    (vwap_bleed_arm_pct, vwap_bleed_ticks_threshold,
    current_vwap_bleed_ticks) does not change new_a or is_vwap_broken.
    current_return is shared by both systems.
  - THRESHOLD ASYMMETRY: System A uses the MODULE CONSTANT
    VWAP_BREAK_CONFIRM_TICKS = 3. System B uses the FUNCTION PARAMETER
    vwap_bleed_ticks_threshold (account-level, tunable per symphony --
    deliberately NOT a module constant).

GREEN-phase named constants introduced (project rule: no magic numbers in
math_engine.py):
  - VWAP_WEIGHT_THRESHOLD = 0.5
    (minimum allocation coverage needed for the weighted VWAP-diff signal
    to be considered reliable; below this, the weighted diff is too thin
    to act on -- the gate uses strict `>`, so 0.5 is the EXCLUSIVE
    boundary)
  - VWAP_BREAK_CONFIRM_TICKS = 3
    (consecutive qualifying ticks for System A to flip is_vwap_broken;
    matches EXIT_CONFIRM_TICKS semantically but is a separate constant
    because System A vs the trailing-stop exit are independent layers
    with independent risk budgets)

  NOTE: vwap_bleed_ticks_threshold is a FUNCTION PARAMETER, NOT a module
  constant -- the caller passes acc_VWAP_BLEED_TICKS (account-level,
  tuned per symphony by the autotuner). The constant-provenance scanner
  must NOT expect a module constant for it.

Tolerance policy:
  - No floats in expected outputs (the function returns (int, int, bool,
    bool) only); exact equality applies. pytest.approx is not used because
    there are no float outputs to compare. Boolean values use `is True`
    / `is False` where strict identity matters; integer values use `==`.

Provenance (HARD): every expected value in
tests/fixtures/math_engine/vwap_breakdown/*.json is DERIVED BY HAND from
the inline producer's logic and pinned in the fixture's 'derivation'
field -- NOT captured from a current implementation
(compute_vwap_breakdown_update does not exist yet; this is RED).

Constant-provenance scanner pattern: follows the cycle-9-fixed pattern
(runtime hasattr + value equality, AST only for line-number lookup of the
trailing source comment). Neither new constant is negative, but the
runtime pattern is sign-safe and consistent.
"""

from __future__ import annotations

import ast
import json
import pathlib
from typing import Any

import pytest

import math_engine

FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "math_engine"
    / "vwap_breakdown"
)

# Reference values used by property tests for cross-checking. These are the
# EXPECTED named constants in math_engine.py (asserted by the constant-
# provenance scanner). Hard-coding the literal values HERE in the TEST file
# is correct: tests own the spec; the impl must conform.
REF_VWAP_WEIGHT_THRESHOLD = 0.5
REF_VWAP_BREAK_CONFIRM_TICKS = 3


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
def test_vwap_breakdown_update_matches_derived_expected(
    fixture_name: str, fixture: dict[str, Any]
) -> None:
    """
    Every fixture's expected (new_vwap_ticks, new_vwap_bleed_ticks,
    is_vwap_broken, is_vwap_bleed_broken) tuple is DERIVED BY HAND in the
    fixture's 'derivation' field. This test asserts
    compute_vwap_breakdown_update produces those values. The function does
    not exist yet -- this is RED.

    Assertions:
      - new_vwap_ticks, new_vwap_bleed_ticks: EXACT integer equality.
      - is_vwap_broken, is_vwap_bleed_broken: EXACT bool equality.
    """
    func_name = fixture["function"]
    assert func_name == "compute_vwap_breakdown_update", (
        f"{fixture_name}: only compute_vwap_breakdown_update is in scope "
        f"for this cycle"
    )

    inputs = fixture["inputs"]
    expected = fixture["expected"]

    result = math_engine.compute_vwap_breakdown_update(
        is_triggered=inputs["is_triggered"],
        valid_vwap_weight=inputs["valid_vwap_weight"],
        weighted_vwap_diff=inputs["weighted_vwap_diff"],
        safe_hwm=inputs["safe_hwm"],
        current_return=inputs["current_return"],
        vwap_cross_hwm_pct=inputs["vwap_cross_hwm_pct"],
        vwap_bleed_arm_pct=inputs["vwap_bleed_arm_pct"],
        vwap_bleed_ticks_threshold=inputs["vwap_bleed_ticks_threshold"],
        current_vwap_ticks=inputs["current_vwap_ticks"],
        current_vwap_bleed_ticks=inputs["current_vwap_bleed_ticks"],
    )

    # Contract: 4-tuple. Defensive unpack with a clear failure message if
    # the impl returns a different shape.
    assert isinstance(result, tuple), (
        f"Fixture {fixture_name}: expected a tuple, got "
        f"{type(result).__name__}"
    )
    assert len(result) == 4, (
        f"Fixture {fixture_name}: expected a 4-tuple "
        f"(new_vwap_ticks, new_vwap_bleed_ticks, is_vwap_broken, "
        f"is_vwap_bleed_broken), got length {len(result)}"
    )
    (
        new_vwap_ticks,
        new_vwap_bleed_ticks,
        is_vwap_broken,
        is_vwap_bleed_broken,
    ) = result

    assert new_vwap_ticks == expected["new_vwap_ticks"], (
        f"Fixture {fixture_name}: new_vwap_ticks mismatch. "
        f"Expected {expected['new_vwap_ticks']} "
        f"(derivation: {fixture['derivation']}), got {new_vwap_ticks}"
    )
    assert new_vwap_bleed_ticks == expected["new_vwap_bleed_ticks"], (
        f"Fixture {fixture_name}: new_vwap_bleed_ticks mismatch. "
        f"Expected {expected['new_vwap_bleed_ticks']} "
        f"(derivation: {fixture['derivation']}), got {new_vwap_bleed_ticks}"
    )
    assert is_vwap_broken == expected["is_vwap_broken"], (
        f"Fixture {fixture_name}: is_vwap_broken mismatch. "
        f"Expected {expected['is_vwap_broken']} "
        f"(derivation: {fixture['derivation']}), got {is_vwap_broken}"
    )
    assert is_vwap_bleed_broken == expected["is_vwap_bleed_broken"], (
        f"Fixture {fixture_name}: is_vwap_bleed_broken mismatch. "
        f"Expected {expected['is_vwap_bleed_broken']} "
        f"(derivation: {fixture['derivation']}), got {is_vwap_bleed_broken}"
    )


# --- Property: TRIGGERED absoluteness ---------------------------------------


@pytest.mark.parametrize(
    "valid_vwap_weight,weighted_vwap_diff,safe_hwm,current_return,"
    "vwap_cross_hwm_pct,vwap_bleed_arm_pct,vwap_bleed_ticks_threshold,"
    "current_vwap_ticks,current_vwap_bleed_ticks",
    [
        # Inputs that WOULD fire System A and System B if not triggered.
        (0.9, -0.5, 3.0, -2.0, 2.0, -1.0, 3, 2, 2),
        # Inputs that WOULD pass the gate but fail BOTH inner conditions.
        (0.9, -0.5, 0.0, 5.0, 2.0, -1.0, 3, 7, 7),
        # Inputs that WOULD fail the gate (would have reset).
        (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 3, 0, 0),
        # High counters that would normally clip via either reset or break.
        (1.0, -10.0, 100.0, -100.0, 0.0, 0.0, 1, 99, 99),
        # Mixed.
        (0.6, -0.01, 5.0, -5.0, 1.0, -2.0, 5, 4, 4),
    ],
)
def test_triggered_absoluteness_preserves_counts_no_signals(
    valid_vwap_weight: float,
    weighted_vwap_diff: float,
    safe_hwm: float,
    current_return: float,
    vwap_cross_hwm_pct: float,
    vwap_bleed_arm_pct: float,
    vwap_bleed_ticks_threshold: int,
    current_vwap_ticks: int,
    current_vwap_bleed_ticks: int,
) -> None:
    """
    GUARD INVARIANT (load-bearing): when is_triggered=True, the function
    returns (current_vwap_ticks, current_vwap_bleed_ticks, False, False)
    regardless of every other input.

    Catches an impl that:
      - omitted the outer triggered guard (would re-fire VWAP exit on an
        already-exited position -- critical safety bug)
      - reset the counts under triggered (loses tick history)
      - flipped the broken flags True under triggered
    """
    (
        new_a,
        new_b,
        is_broken,
        is_bleed_broken,
    ) = math_engine.compute_vwap_breakdown_update(
        is_triggered=True,
        valid_vwap_weight=valid_vwap_weight,
        weighted_vwap_diff=weighted_vwap_diff,
        safe_hwm=safe_hwm,
        current_return=current_return,
        vwap_cross_hwm_pct=vwap_cross_hwm_pct,
        vwap_bleed_arm_pct=vwap_bleed_arm_pct,
        vwap_bleed_ticks_threshold=vwap_bleed_ticks_threshold,
        current_vwap_ticks=current_vwap_ticks,
        current_vwap_bleed_ticks=current_vwap_bleed_ticks,
    )
    assert new_a == current_vwap_ticks, (
        f"GUARD BROKEN (is_triggered): vwap_ticks must be preserved at "
        f"{current_vwap_ticks}, got {new_a}."
    )
    assert new_b == current_vwap_bleed_ticks, (
        f"GUARD BROKEN (is_triggered): vwap_bleed_ticks must be preserved "
        f"at {current_vwap_bleed_ticks}, got {new_b}."
    )
    assert is_broken is False, (
        f"GUARD BROKEN (is_triggered): is_vwap_broken must be False, got "
        f"{is_broken}. Already-triggered position must NEVER re-fire "
        f"VWAP exit."
    )
    assert is_bleed_broken is False, (
        f"GUARD BROKEN (is_triggered): is_vwap_bleed_broken must be False, "
        f"got {is_bleed_broken}. Already-triggered position must NEVER "
        f"re-fire VWAP-bleed exit."
    )


# --- Property: GATE absoluteness (gate-fail wipes both ticks) ---------------


@pytest.mark.parametrize(
    "valid_vwap_weight,weighted_vwap_diff",
    [
        (0.0, -1.0),    # weight far below threshold
        (0.5, -1.0),    # weight EXACTLY at threshold (strict `>` fails)
        (0.499, -1.0),  # weight just below
        (0.9, 0.0),     # diff EXACTLY 0.0 (strict `<` fails)
        (0.9, 0.5),     # diff positive
        (0.5, 0.0),     # both clauses fail
        (0.0, 1.0),     # both clauses fail, different way
    ],
)
@pytest.mark.parametrize(
    "current_vwap_ticks,current_vwap_bleed_ticks",
    [
        (0, 0),
        (1, 2),
        (5, 5),
        (100, 100),  # large stale counts
    ],
)
def test_system_a_gate_fail_resets_system_a_only_system_b_follows_own_arm(
    valid_vwap_weight: float,
    weighted_vwap_diff: float,
    current_vwap_ticks: int,
    current_vwap_bleed_ticks: int,
) -> None:
    """
    SYSTEM-A GATE INVARIANT (post H2 decoupling): when is_triggered=False AND
    NOT (weight > VWAP_WEIGHT_THRESHOLD AND diff < 0), the System-A counter
    resets to 0 and is_vwap_broken is False — BUT System B is INDEPENDENT and
    follows its OWN arm condition (current_return <= vwap_bleed_arm_pct), which
    does NOT reference the VWAP weight/sign gate.

    [H2 CHANGE] This test previously asserted BOTH counters reset to 0 on a
    System-A gate miss — that encoded the H2 bug (System B wiped by an unrelated
    gate). The synthesis verdict (H2) and the reviewer-ruled contract decouple
    System B: here current_return=-50.0 <= bleed_arm=-1.0 so System B ARMS and
    its counter increments by 1 from its prior value (and breaks once it crosses
    the threshold), regardless of the System-A gate.

    Sweeps the gate-fail surface (weight too low, weight on the strict
    boundary, diff zero, diff positive, both failing) AND multiple prev tick
    states (zero, mid, high).
    """
    (
        new_a,
        new_b,
        is_broken,
        is_bleed_broken,
    ) = math_engine.compute_vwap_breakdown_update(
        is_triggered=False,
        valid_vwap_weight=valid_vwap_weight,
        weighted_vwap_diff=weighted_vwap_diff,
        safe_hwm=10.0,
        current_return=-50.0,            # System B arm MET (-50 <= -1.0)
        vwap_cross_hwm_pct=1.0,
        vwap_bleed_arm_pct=-1.0,
        vwap_bleed_ticks_threshold=3,
        current_vwap_ticks=current_vwap_ticks,
        current_vwap_bleed_ticks=current_vwap_bleed_ticks,
    )
    assert new_a == 0, (
        f"SYSTEM-A GATE BROKEN: vwap_ticks must reset to 0 when the System-A "
        f"gate fails (weight={valid_vwap_weight}, diff={weighted_vwap_diff}); "
        f"got {new_a}. Prev count was {current_vwap_ticks}."
    )
    assert is_broken is False, (
        f"SYSTEM-A GATE BROKEN: is_vwap_broken must be False when the System-A "
        f"gate fails; got {is_broken}."
    )
    # System B is decoupled: it arms on its own condition (-50 <= -1.0) and
    # increments by exactly 1 from its prior value, regardless of System A's gate.
    expected_b = current_vwap_bleed_ticks + 1
    assert new_b == expected_b, (
        f"SYSTEM B WRONGLY COUPLED to the System-A gate: with current_return "
        f"-50.0 <= bleed_arm -1.0 (System B arm met), the bleed counter must "
        f"increment {current_vwap_bleed_ticks} -> {expected_b} regardless of "
        f"the System-A weight/diff gate; got {new_b}."
    )
    assert is_bleed_broken is (expected_b >= 3), (
        f"is_vwap_bleed_broken must be ({expected_b} >= 3); got {is_bleed_broken}."
    )


# --- Property: System A increment by EXACTLY 1 when condition met -----------


@pytest.mark.parametrize("starting_a", [0, 1, 2, 3, 4, 5, 10, 50, 1000])
def test_system_a_increments_by_exactly_one_when_condition_met(
    starting_a: int,
) -> None:
    """
    Invariant: when outer guard + gate pass AND System A condition
    (safe_hwm >= cross AND current_return < safe_hwm) is met, new_a is
    EXACTLY current_vwap_ticks + 1. No clamping, no scaling, no
    acceleration.

    Inputs are well inside both the gate and System A:
      weight=0.9, diff=-0.5 (gate passes)
      safe_hwm=4.0, cross=2.0 (4.0 >= 2.0)
      current_return=1.0 (1.0 < 4.0)
    System B is set to FAIL so its state is not confused with System A's:
      bleed_arm=-1.0, current_return=1.0 (1.0 > -1.0; B condition fails).
    """
    new_a, _, _, _ = math_engine.compute_vwap_breakdown_update(
        is_triggered=False,
        valid_vwap_weight=0.9,
        weighted_vwap_diff=-0.5,
        safe_hwm=4.0,
        current_return=1.0,
        vwap_cross_hwm_pct=2.0,
        vwap_bleed_arm_pct=-1.0,
        vwap_bleed_ticks_threshold=3,
        current_vwap_ticks=starting_a,
        current_vwap_bleed_ticks=0,
    )
    assert new_a == starting_a + 1, (
        f"System A: expected vwap_ticks to increment by EXACTLY 1 from "
        f"{starting_a} to {starting_a + 1}, got {new_a}."
    )


# --- Property: System A reset fully to 0 when condition fails --------------


@pytest.mark.parametrize("starting_a", [0, 1, 2, 3, 4, 5, 10, 50, 1000])
def test_system_a_resets_fully_to_zero_when_condition_fails(
    starting_a: int,
) -> None:
    """
    Invariant: when outer guard + gate pass AND System A condition fails
    (either safe_hwm < cross OR current_return >= safe_hwm), new_a is 0
    (full reset, not decrement).

    Inputs make System A FAIL (safe_hwm=0.5 < cross=2.0) while keeping
    the gate passing (weight=0.9, diff=-0.5). System B is also set to
    fail so its state is decoupled.
    """
    new_a, _, _, _ = math_engine.compute_vwap_breakdown_update(
        is_triggered=False,
        valid_vwap_weight=0.9,
        weighted_vwap_diff=-0.5,
        safe_hwm=0.5,
        current_return=0.0,
        vwap_cross_hwm_pct=2.0,
        vwap_bleed_arm_pct=-1.0,
        vwap_bleed_ticks_threshold=3,
        current_vwap_ticks=starting_a,
        current_vwap_bleed_ticks=0,
    )
    assert new_a == 0, (
        f"System A: expected vwap_ticks to RESET to 0 from {starting_a}, "
        f"got {new_a}. Reset is FULL, NOT decrement."
    )


# --- Property: System B increment by EXACTLY 1 when condition met ----------


@pytest.mark.parametrize("starting_b", [0, 1, 2, 3, 4, 5, 10, 50, 1000])
def test_system_b_increments_by_exactly_one_when_condition_met(
    starting_b: int,
) -> None:
    """
    Invariant: when outer guard + gate pass AND System B condition
    (current_return <= bleed_arm) is met, new_b is EXACTLY
    current_vwap_bleed_ticks + 1.

    Inputs make System B MET (current_return=-2.0 <= bleed_arm=-1.0) while
    keeping the gate passing (weight=0.9, diff=-0.5). System A is set to
    FAIL (safe_hwm=0.5 < cross=2.0) so its state is decoupled.
    """
    _, new_b, _, _ = math_engine.compute_vwap_breakdown_update(
        is_triggered=False,
        valid_vwap_weight=0.9,
        weighted_vwap_diff=-0.5,
        safe_hwm=0.5,
        current_return=-2.0,
        vwap_cross_hwm_pct=2.0,
        vwap_bleed_arm_pct=-1.0,
        vwap_bleed_ticks_threshold=999,  # huge so we never trip bleed_broken here
        current_vwap_ticks=0,
        current_vwap_bleed_ticks=starting_b,
    )
    assert new_b == starting_b + 1, (
        f"System B: expected vwap_bleed_ticks to increment by EXACTLY 1 "
        f"from {starting_b} to {starting_b + 1}, got {new_b}."
    )


# --- Property: System B reset fully to 0 when condition fails --------------


@pytest.mark.parametrize("starting_b", [0, 1, 2, 3, 4, 5, 10, 50, 1000])
def test_system_b_resets_fully_to_zero_when_condition_fails(
    starting_b: int,
) -> None:
    """
    Invariant: when outer guard + gate pass AND System B condition fails
    (current_return > bleed_arm), new_b is 0 (full reset).

    Inputs make System B FAIL (current_return=0.5 > bleed_arm=-1.0) while
    keeping gate passing. System A is set to FAIL too so its state is
    decoupled.
    """
    _, new_b, _, _ = math_engine.compute_vwap_breakdown_update(
        is_triggered=False,
        valid_vwap_weight=0.9,
        weighted_vwap_diff=-0.5,
        safe_hwm=0.5,
        current_return=0.5,
        vwap_cross_hwm_pct=2.0,
        vwap_bleed_arm_pct=-1.0,
        vwap_bleed_ticks_threshold=3,
        current_vwap_ticks=0,
        current_vwap_bleed_ticks=starting_b,
    )
    assert new_b == 0, (
        f"System B: expected vwap_bleed_ticks to RESET to 0 from "
        f"{starting_b}, got {new_b}. Reset is FULL, NOT decrement."
    )


# --- Property: System A break iff condition met AND new_a >= MODULE CONSTANT
#               VWAP_BREAK_CONFIRM_TICKS ---------------------------------


@pytest.mark.parametrize(
    "starting_a,a_condition_met,expected_broken",
    [
        # condition met -> new_a = starting+1; broken iff new_a >= 3
        (0, True, False),   # 0+1=1, not >= 3
        (1, True, False),   # 1+1=2, not >= 3
        (2, True, True),    # 2+1=3, broken (>= 3)
        (3, True, True),    # 3+1=4, broken
        (10, True, True),
        # condition fails -> new_a = 0; broken False
        (0, False, False),
        (2, False, False),
        (10, False, False),
    ],
)
def test_system_a_break_truth_table(
    starting_a: int, a_condition_met: bool, expected_broken: bool
) -> None:
    """
    Truth-table for is_vwap_broken: True iff outer guard + gate pass AND
    System A condition met AND new_a >= VWAP_BREAK_CONFIRM_TICKS.

    a_condition_met=True -> safe_hwm=4.0 (>= cross 2.0), current_return=1.0
    (< safe_hwm 4.0). a_condition_met=False -> safe_hwm=0.5
    (< cross 2.0; arm clause fails). System B is set to FAIL so its state
    is decoupled (bleed_arm=-1.0, current_return >= 0.5).
    """
    if a_condition_met:
        safe_hwm, current_return = 4.0, 1.0
    else:
        safe_hwm, current_return = 0.5, 0.5
    _, _, is_broken, _ = math_engine.compute_vwap_breakdown_update(
        is_triggered=False,
        valid_vwap_weight=0.9,
        weighted_vwap_diff=-0.5,
        safe_hwm=safe_hwm,
        current_return=current_return,
        vwap_cross_hwm_pct=2.0,
        vwap_bleed_arm_pct=-1.0,
        vwap_bleed_ticks_threshold=3,
        current_vwap_ticks=starting_a,
        current_vwap_bleed_ticks=0,
    )
    assert is_broken is expected_broken, (
        f"System A break mismatch. starting_a={starting_a}, "
        f"a_condition_met={a_condition_met}. Expected is_vwap_broken="
        f"{expected_broken}, got {is_broken}. (Threshold is the MODULE "
        f"CONSTANT VWAP_BREAK_CONFIRM_TICKS = {REF_VWAP_BREAK_CONFIRM_TICKS}.)"
    )


# --- Property: System B break iff condition met AND new_b >= PARAMETER
#               vwap_bleed_ticks_threshold -------------------------------


@pytest.mark.parametrize(
    "threshold_param,starting_b,b_condition_met,expected_bleed_broken",
    [
        # Default-ish threshold 3
        (3, 0, True, False),    # 0+1=1, not >= 3
        (3, 1, True, False),    # 1+1=2
        (3, 2, True, True),     # 2+1=3
        (3, 5, True, True),     # 5+1=6
        # Higher threshold 5 (autotuner could pick this per symphony)
        (5, 3, True, False),    # 3+1=4, not >= 5
        (5, 4, True, True),     # 4+1=5, broken
        (5, 10, True, True),
        # Lower threshold 1 (extreme; every tick breaks)
        (1, 0, True, True),     # 0+1=1, >= 1 broken
        (1, 5, True, True),
        # condition fails -> always False, regardless of threshold
        (3, 5, False, False),
        (5, 10, False, False),
        (1, 0, False, False),
    ],
)
def test_system_b_break_truth_table_uses_parameter_not_constant(
    threshold_param: int,
    starting_b: int,
    b_condition_met: bool,
    expected_bleed_broken: bool,
) -> None:
    """
    Truth-table for is_vwap_bleed_broken: True iff outer guard + gate
    pass AND System B condition met AND new_b >= vwap_bleed_ticks_threshold
    (the FUNCTION PARAMETER, not the module constant).

    Sweeps multiple threshold values to confirm the impl uses the parameter,
    not a hardcoded 3 (would catch confusion between System A's 3-tick
    module constant and System B's parameter).

    b_condition_met=True -> current_return=-2.0 <= bleed_arm=-1.0.
    b_condition_met=False -> current_return=0.5 > bleed_arm=-1.0.
    System A is set to FAIL so its state is decoupled (safe_hwm=0.5 <
    cross=2.0).
    """
    if b_condition_met:
        current_return = -2.0
    else:
        current_return = 0.5
    _, _, _, is_bleed_broken = math_engine.compute_vwap_breakdown_update(
        is_triggered=False,
        valid_vwap_weight=0.9,
        weighted_vwap_diff=-0.5,
        safe_hwm=0.5,
        current_return=current_return,
        vwap_cross_hwm_pct=2.0,
        vwap_bleed_arm_pct=-1.0,
        vwap_bleed_ticks_threshold=threshold_param,
        current_vwap_ticks=0,
        current_vwap_bleed_ticks=starting_b,
    )
    assert is_bleed_broken is expected_bleed_broken, (
        f"System B bleed-break mismatch. threshold_param={threshold_param}, "
        f"starting_b={starting_b}, b_condition_met={b_condition_met}. "
        f"Expected is_vwap_bleed_broken={expected_bleed_broken}, got "
        f"{is_bleed_broken}. The threshold MUST be the function parameter, "
        f"NOT a module constant."
    )


# --- Property: System A and System B are INDEPENDENT -----------------------


def test_system_a_inputs_do_not_affect_system_b_outputs() -> None:
    """
    Independence half 1: changing System-A-only inputs (safe_hwm,
    vwap_cross_hwm_pct, current_vwap_ticks) does not change new_b or
    is_vwap_bleed_broken.

    Fix System B inputs (current_return, vwap_bleed_arm_pct,
    vwap_bleed_ticks_threshold, current_vwap_bleed_ticks) -- System B
    should fire deterministically -- then sweep System A inputs.
    """
    # Fixed System B inputs so B condition is MET deterministically.
    fixed_b = dict(
        is_triggered=False,
        valid_vwap_weight=0.9,
        weighted_vwap_diff=-0.5,
        current_return=-2.0,           # <= bleed_arm -1.0
        vwap_bleed_arm_pct=-1.0,
        vwap_bleed_ticks_threshold=3,
        current_vwap_bleed_ticks=2,    # will become 3 -> bleed broken
    )
    # Each row is a different System A regime (met / failed / boundary /
    # huge prev). They must all leave System B's output identical.
    a_regimes = [
        # (safe_hwm, vwap_cross_hwm_pct, current_vwap_ticks)
        (4.0, 2.0, 0),     # A met, prev 0 -> 1
        (4.0, 2.0, 5),     # A met, prev 5 -> 6 (would break A)
        (0.5, 2.0, 0),     # A fails (hwm < cross), reset
        (0.5, 2.0, 9),     # A fails, big prev wiped
        (2.0, 2.0, 0),     # A boundary (hwm == cross, condition met)
    ]
    b_outputs: list[tuple[int, bool]] = []
    for safe_hwm, vwap_cross_hwm_pct, current_vwap_ticks in a_regimes:
        _, new_b, _, is_bleed_broken = math_engine.compute_vwap_breakdown_update(
            safe_hwm=safe_hwm,
            vwap_cross_hwm_pct=vwap_cross_hwm_pct,
            current_vwap_ticks=current_vwap_ticks,
            **fixed_b,
        )
        b_outputs.append((new_b, is_bleed_broken))
    first = b_outputs[0]
    for i, out in enumerate(b_outputs[1:], start=1):
        assert out == first, (
            f"Independence broken: changing System A inputs (regime "
            f"{a_regimes[i]} vs {a_regimes[0]}) changed System B outputs "
            f"from {first} to {out}. System B must be independent of "
            f"System A."
        )


def test_system_b_inputs_do_not_affect_system_a_outputs() -> None:
    """
    Independence half 2: changing System-B-only inputs
    (vwap_bleed_arm_pct, vwap_bleed_ticks_threshold,
    current_vwap_bleed_ticks) does not change new_a or is_vwap_broken.

    NOTE: current_return is SHARED by both systems (System A uses it in
    `current_return < safe_hwm`; System B uses it in
    `current_return <= bleed_arm`). It is NOT a System-B-only input and
    is held fixed here. The B-only inputs are bleed_arm, threshold, and
    prev bleed count.
    """
    # Fixed System A inputs so A condition is MET deterministically.
    fixed_a = dict(
        is_triggered=False,
        valid_vwap_weight=0.9,
        weighted_vwap_diff=-0.5,
        safe_hwm=4.0,                  # >= cross 2.0
        current_return=1.0,            # < safe_hwm 4.0 (A met);
                                       # also > any bleed_arm here so B fails
        vwap_cross_hwm_pct=2.0,
        current_vwap_ticks=2,          # will become 3 -> A broken
    )
    b_regimes = [
        # (vwap_bleed_arm_pct, vwap_bleed_ticks_threshold, current_vwap_bleed_ticks)
        (-1.0, 3, 0),     # B fails (1.0 > -1.0), reset
        (-1.0, 3, 9),     # B fails, big prev wiped
        (-5.0, 5, 0),     # B fails (1.0 > -5.0)
        (-100.0, 1, 50),  # B fails, extreme params
    ]
    a_outputs: list[tuple[int, bool]] = []
    for bleed_arm, threshold, prev_bleed in b_regimes:
        new_a, _, is_broken, _ = math_engine.compute_vwap_breakdown_update(
            vwap_bleed_arm_pct=bleed_arm,
            vwap_bleed_ticks_threshold=threshold,
            current_vwap_bleed_ticks=prev_bleed,
            **fixed_a,
        )
        a_outputs.append((new_a, is_broken))
    first = a_outputs[0]
    for i, out in enumerate(a_outputs[1:], start=1):
        assert out == first, (
            f"Independence broken: changing System B inputs (regime "
            f"{b_regimes[i]} vs {b_regimes[0]}) changed System A outputs "
            f"from {first} to {out}. System A must be independent of "
            f"System B's bleed-only inputs."
        )


# --- Property: determinism --------------------------------------------------


def test_function_is_pure_repeat_call_returns_identical_result() -> None:
    """
    Sanity: a pure function returns identical results when called twice
    with the same arguments. Catches an impl that accidentally stashes
    state in a module-level variable, uses an unseeded RNG, or has a
    hidden time-dependence.
    """
    args = {
        "is_triggered": False,
        "valid_vwap_weight": 0.9,
        "weighted_vwap_diff": -0.5,
        "safe_hwm": 3.0,
        "current_return": -1.5,
        "vwap_cross_hwm_pct": 2.0,
        "vwap_bleed_arm_pct": -1.0,
        "vwap_bleed_ticks_threshold": 3,
        "current_vwap_ticks": 2,
        "current_vwap_bleed_ticks": 2,
    }
    a = math_engine.compute_vwap_breakdown_update(**args)
    b = math_engine.compute_vwap_breakdown_update(**args)
    assert a == b, f"Non-deterministic output: {a} vs {b}"


# --- Property: return type contract (int, int, bool, bool) -----------------


@pytest.mark.parametrize(
    "is_triggered,valid_vwap_weight,weighted_vwap_diff,safe_hwm,"
    "current_return,starting_a,starting_b",
    [
        # Branch 1: triggered (preserve path)
        (True, 0.9, -0.5, 3.0, -2.0, 2, 2),
        # Branch 2: gate fail (reset path)
        (False, 0.5, -0.5, 3.0, -2.0, 2, 2),
        # Branch 3 / System A met, B met (both increment + break)
        (False, 0.9, -0.5, 3.0, -1.5, 2, 2),
        # Branch 3 / System A reset, B reset
        (False, 0.9, -0.5, 0.5, 0.5, 2, 2),
        # Branch 3 / System A met from 0 (no break)
        (False, 0.9, -0.5, 4.0, 1.0, 0, 0),
    ],
)
def test_return_type_contract_int_int_bool_bool(
    is_triggered: bool,
    valid_vwap_weight: float,
    weighted_vwap_diff: float,
    safe_hwm: float,
    current_return: float,
    starting_a: int,
    starting_b: int,
) -> None:
    """
    Contract: returns (int, int, bool, bool). Strict `type() is` checks
    because:
      - new_vwap_ticks and new_vwap_bleed_ticks are persisted to bot_state
        and incremented in subsequent ticks (`bot_state[...]['vwap_ticks']
        += 1`); numpy int subtypes break the simple int-increment pattern
        and serialize inconsistently to SQLite.
      - is_vwap_broken and is_vwap_bleed_broken gate downstream `if` checks
        that initiate the live exit; numpy bool serializes inconsistently
        and Python bool is a subclass of int, so plain int passing as bool
        would be a category error.

    Sweeps multiple code paths (triggered preserve, gate-fail reset, both
    systems running) to catch a type-bug that only appears in one branch
    (e.g., an `else: return 0, 0, False, False` returning bare 0 ints
    while another branch returns numpy ints from arithmetic).
    """
    result = math_engine.compute_vwap_breakdown_update(
        is_triggered=is_triggered,
        valid_vwap_weight=valid_vwap_weight,
        weighted_vwap_diff=weighted_vwap_diff,
        safe_hwm=safe_hwm,
        current_return=current_return,
        vwap_cross_hwm_pct=2.0,
        vwap_bleed_arm_pct=-1.0,
        vwap_bleed_ticks_threshold=3,
        current_vwap_ticks=starting_a,
        current_vwap_bleed_ticks=starting_b,
    )
    assert isinstance(result, tuple) and len(result) == 4, (
        f"Expected 4-tuple, got {result!r}"
    )
    new_a, new_b, is_broken, is_bleed_broken = result
    assert type(new_a) is int, (
        f"new_vwap_ticks must be EXACTLY int, got {type(new_a).__name__} "
        f"(value={new_a!r}). Numpy ints / bools forbidden; bot_state "
        f"increment relies on plain int."
    )
    assert type(new_b) is int, (
        f"new_vwap_bleed_ticks must be EXACTLY int, got "
        f"{type(new_b).__name__} (value={new_b!r}). Same reason as "
        f"new_vwap_ticks."
    )
    # bool is a subclass of int in Python; strict `type() is` is mandatory
    # to forbid plain int from passing where bool is contracted.
    assert type(is_broken) is bool, (
        f"is_vwap_broken must be EXACTLY bool, got "
        f"{type(is_broken).__name__} (value={is_broken!r}). Numpy bools / "
        f"plain ints forbidden; gates downstream exit -- ambiguous types "
        f"are a category error."
    )
    assert type(is_bleed_broken) is bool, (
        f"is_vwap_bleed_broken must be EXACTLY bool, got "
        f"{type(is_bleed_broken).__name__} (value={is_bleed_broken!r}). "
        f"Same reason as is_vwap_broken."
    )


# --- Module-level named constants exist ------------------------------------


def test_vwap_weight_threshold_is_module_level_named_constant() -> None:
    """
    Project rule: 'No magic numbers in math_engine.py -- every constant
    named + source comment.' The 0.5 weight-threshold gate must be a
    module-level constant named VWAP_WEIGHT_THRESHOLD.

    Uses runtime hasattr + value equality (sign-safe; this constant is
    positive but the pattern is shared with cycle-9). Source-comment
    enforcement is done via AST line-lookup below.
    """
    assert hasattr(math_engine, "VWAP_WEIGHT_THRESHOLD"), (
        "math_engine.VWAP_WEIGHT_THRESHOLD not found -- the minimum "
        "valid_vwap_weight gate (0.5) must be a named module-level "
        "constant."
    )
    assert math_engine.VWAP_WEIGHT_THRESHOLD == 0.5, (
        f"VWAP_WEIGHT_THRESHOLD should be 0.5, got "
        f"{math_engine.VWAP_WEIGHT_THRESHOLD}"
    )


def test_vwap_break_confirm_ticks_is_module_level_named_constant() -> None:
    """
    Project rule: every constant named. The 3-tick System A confirmation
    threshold must be a module-level constant named VWAP_BREAK_CONFIRM_TICKS.

    NOTE: this is the SAME numeric value as EXIT_CONFIRM_TICKS (also 3),
    but it MUST be a separate named constant. The two layers (trailing-stop
    exit confirmation and VWAP profit-protection confirmation) are
    INDEPENDENT risk budgets that happen to share a numeric coincidence on
    current main; tying them via a single shared constant would create
    spooky-action-at-a-distance if one were tuned later. The autotuner
    treats them as separate degrees of freedom.
    """
    assert hasattr(math_engine, "VWAP_BREAK_CONFIRM_TICKS"), (
        "math_engine.VWAP_BREAK_CONFIRM_TICKS not found -- the System A "
        "consecutive-tick threshold (3) for flipping is_vwap_broken must "
        "be a named module-level constant."
    )
    assert math_engine.VWAP_BREAK_CONFIRM_TICKS == 3, (
        f"VWAP_BREAK_CONFIRM_TICKS should be 3, got "
        f"{math_engine.VWAP_BREAK_CONFIRM_TICKS}"
    )


def test_vwap_bleed_ticks_threshold_is_NOT_a_module_constant() -> None:
    """
    Negative constant-provenance: vwap_bleed_ticks_threshold is a FUNCTION
    PARAMETER (account-level, tuned per symphony by the autotuner -- the
    caller passes acc_VWAP_BLEED_TICKS). It must NOT be promoted to a
    module-level constant; that would lose the per-symphony tunability.

    Catches an impl that 'helpfully' introduced VWAP_BLEED_TICKS_THRESHOLD
    at module level by reading the inline producer's parameter name.

    The negative assertion is bounded: a few naming variants (snake_case
    SCREAMING, camelCase, etc.) are excluded; the broader spirit (no
    module-level int constant with the bleed-ticks meaning) is enforced
    via this naming whitelist.
    """
    forbidden_names = (
        "VWAP_BLEED_TICKS",
        "VWAP_BLEED_TICKS_THRESHOLD",
        "VWAP_BLEED_THRESHOLD_TICKS",
        "ACC_VWAP_BLEED_TICKS",
    )
    offenders = [n for n in forbidden_names if hasattr(math_engine, n)]
    assert not offenders, (
        f"Found forbidden module-level constant(s): {offenders}. "
        f"vwap_bleed_ticks_threshold MUST remain a function parameter (the "
        f"caller passes the account-level acc_VWAP_BLEED_TICKS). Promoting "
        f"it to a module constant loses the per-symphony tunability."
    )


# --- Source-comment enforcement for the two named constants -----------------


def test_new_named_constants_have_source_comments() -> None:
    """
    Project rule reinforced: 'every constant named + source comment'. The
    two new constants must be assigned on lines that ALSO have a trailing
    source comment (before = lhs/rhs, after = comment text, both
    non-empty).

    Pattern (from cycle 9): use AST to find the assignment line number
    (sign-safe for any value); inspect the source line for a # with
    non-empty content before and after.
    """
    src_path = pathlib.Path(math_engine.__file__)
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    src_lines = source.splitlines()

    expected = {"VWAP_WEIGHT_THRESHOLD", "VWAP_BREAK_CONFIRM_TICKS"}
    found_linenos: dict[str, int] = {}
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id in expected:
                    found_linenos[tgt.id] = node.lineno

    for name in expected:
        assert name in found_linenos, (
            f"AST scan could not locate the module-level assignment for "
            f"{name} in math_engine.py. The constant exists at runtime but "
            f"is not a module-level Assign (or does not exist yet in RED)."
        )
        lineno = found_linenos[name]
        line = src_lines[lineno - 1] if lineno - 1 < len(src_lines) else ""
        before, _, after = line.partition("#")
        assert before.strip() != "" and after.strip() != "", (
            f"Constant {name} on line {lineno} of math_engine.py has no "
            f"trailing source comment. Project rule: 'every constant named "
            f"+ source comment'. Line content: {line!r}."
        )


# --- Constant-provenance scanner: no bare 0.5 / 3 in function body ---------


def test_no_unnamed_magic_numbers_in_vwap_breakdown_function_body() -> None:
    """
    Project rule: 'No magic numbers in math_engine.py -- every constant
    named + source comment.'

    Scans the AST of compute_vwap_breakdown_update for numeric literals.
    Each literal must either be:
      (a) a 'trivially structural' value (0, 1, -1), or
      (b) accompanied by a named-constant assignment or an explanatory
          source comment on the same line.

    Specifically forbids bare 0.5 (the gate threshold) and bare 3 (the
    System A confirmation threshold) -- these MUST be referenced by name
    as VWAP_WEIGHT_THRESHOLD / VWAP_BREAK_CONFIRM_TICKS.

    A naive GREEN impl that copy-pasted `> 0.5` and `>= 3` from the inline
    producer would fail this scanner. The function does not exist yet (RED)
    so the FunctionDef-not-found assertion will fire first.
    """
    src_path = pathlib.Path(math_engine.__file__)
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    src_lines = source.splitlines()

    # Structural literals (universal "structural" values that carry no
    # domain meaning in this function). Python set hashing: int 0 and
    # float 0.0 share a hash and equality, so {0} matches both.
    STRUCTURAL = {0, 1, -1}

    target: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "compute_vwap_breakdown_update"
        ):
            target = node
            break
    assert target is not None, (
        "compute_vwap_breakdown_update not found in math_engine.py "
        "(expected in RED; the function will be created in GREEN)"
    )

    # Lines where an ast.Name is assigned a Constant count as "named"
    # (local constants spelled inside the function body, if any).
    named_literal_lines: set[int] = set()
    for sub in ast.walk(target):
        if isinstance(sub, ast.Assign):
            for tgt in sub.targets:
                if isinstance(tgt, ast.Name) and isinstance(
                    sub.value, ast.Constant
                ):
                    named_literal_lines.add(sub.value.lineno)

    def line_has_comment(lineno: int) -> bool:
        if lineno - 1 >= len(src_lines):
            return False
        line = src_lines[lineno - 1]
        if "#" not in line:
            return False
        before, _, after = line.partition("#")
        return before.strip() != "" and after.strip() != ""

    offenders: list[tuple[int, Any]] = []
    for sub in ast.walk(target):
        if isinstance(sub, ast.Constant) and isinstance(
            sub.value, (int, float)
        ):
            val = sub.value
            if isinstance(val, bool):  # bool is a subclass of int
                continue
            if val in STRUCTURAL:
                continue
            if sub.lineno in named_literal_lines:
                continue
            if line_has_comment(sub.lineno):
                continue
            offenders.append((sub.lineno, val))

    assert not offenders, (
        "Unnamed magic numbers in compute_vwap_breakdown_update (project "
        "rule: every constant in math_engine.py must be named + "
        f"source-commented). Offenders (line, value): {offenders}. Fix in "
        "the GREEN phase by referencing VWAP_WEIGHT_THRESHOLD and "
        "VWAP_BREAK_CONFIRM_TICKS by name."
    )


def test_domain_constants_are_named_not_bare_literals_in_function_body() -> None:
    """
    Sharper guard than the structural-whitelist scanner: walks the BODY of
    compute_vwap_breakdown_update and asserts NO ast.Constant with a value
    matching one of the two extracted domain constants appears.

    Why needed in addition to the structural scanner: a future refactor
    that added (e.g.) 3 to STRUCTURAL would let a bare 3 slip through.
    This test pins the project rule for THIS specific function: the gate
    threshold and System A confirm threshold must be referenced by Name,
    never inlined as bare 0.5 / 3.

    Domain values forbidden as bare literals in the function body:
      0.5, 3
    """
    src_path = pathlib.Path(math_engine.__file__)
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    target: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "compute_vwap_breakdown_update"
        ):
            target = node
            break
    assert target is not None, (
        "compute_vwap_breakdown_update not found in math_engine.py "
        "(expected RED state)"
    )

    # Python set hashing: 3 and 3.0 hash identically, so a bare `3` would
    # match `3` (and `3.0` if anyone wrote that). 0.5 covers the gate
    # threshold literal.
    forbidden_values = {0.5, 3}

    offenders: list[tuple[int, Any]] = []
    for sub in ast.walk(target):
        if isinstance(sub, ast.Constant) and isinstance(
            sub.value, (int, float)
        ):
            if isinstance(sub.value, bool):
                continue
            if sub.value in forbidden_values:
                offenders.append((sub.lineno, sub.value))

    assert not offenders, (
        "Bare domain-constant literal(s) found inside "
        f"compute_vwap_breakdown_update at (line, value): {offenders}. "
        "The two extracted constants (VWAP_WEIGHT_THRESHOLD=0.5, "
        "VWAP_BREAK_CONFIRM_TICKS=3) MUST be referenced by name from the "
        "function body. Fix in GREEN by using the named constants."
    )
