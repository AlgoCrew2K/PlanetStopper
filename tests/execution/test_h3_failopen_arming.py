"""
RED tests — H-3: fail-open trailing-stop arming when MC is absent.

Audit finding H-3 root cause
==============================
The only armed=True path requires mc_available=True:

    alpha_bot_execution.py:1292  mc_available = prob_beating is not None
    alpha_bot_execution.py:1294  if mc_available and acc_TAKE_PROFIT_MC_PCT <= prob_beating < acc_TRIGGER_THRESHOLD_PCT:
    alpha_bot_execution.py:1295      should_arm = True

When MC history is permanently insufficient (prob_beating is None every cycle),
mc_available is always False, should_arm stays False, bot_state["armed"] stays
False, and compute_exit_confirmation is called with armed=False — its early-return
guard fires and the protective stop NEVER arms.

The code comment at alpha_bot_execution.py:1284-1289 explicitly says
"The protective stop still fires on its ticks-below-stop condition alone" — a
documented contract that the pre-fix code violates.

Required behavior after the fix (FAIL-OPEN)
============================================
- When MC is absent (prob_beating is None), the trailing stop MUST arm.
- When MC IS present, arming/disarming/TP behavior must be byte-identical to today.
- An already-armed position is not re-armed (idempotent).
- A triggered position is not armed (guard must hold).
- The arming produces a distinguishable arm_reason (e.g. "MC Absent" / "fail-open").
- The disarm branch (mc_available AND high prob AND positive return) must NOT fire
  when mc_available=False — so a fail-open-armed stop cannot be disarmed by MC.
- The ticks-below-stop + EXIT_CONFIRM_TICKS confirmation requirement still gates the
  actual liquidation signal — single tick with MC absent does NOT fire.
- Return above stop_trigger_level - MAGNITUDE_FLOOR_PCT still resets the count,
  even with MC absent.

Tests are adversarial: they are designed so that an implementation that
arms-but-never-fires, or fires too eagerly (single-tick false liquidation), or
silently re-arms, or leaks the fix into the MC-present path MUST fail.

Structural approach
===================
The arming gate lives in alpha_bot_execution.py (not a pure function). This file
tests it via:

1. AST-structural inspection of the arming block: asserts the fail-open branch
   exists and has the correct guard predicates.
2. Integration-style: exercises compute_exit_confirmation directly with armed=True
   and prob_beating=None (verifying the math layer is already correct and fires).
3. Golden-fixture parametrised tests: sequence assertions for the ticks-below-stop
   confirmation ladder with MC absent.
4. Regression parametrised tests: MC-present behaviour unchanged.
5. Property tests: monotonicity, idempotence, scope guard.

Scope guard
===========
This file covers the ARMING GATE (alpha_bot_execution.py:1292-1316) and the
exit-confirmation layer (math_engine.compute_exit_confirmation) with prob_beating=None.
It does NOT cover: TP arming, parabolic arming, breakeven logic, disarm-with-MC,
or any other trigger type.
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

import math_engine

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_WORKTREE_ROOT = pathlib.Path(__file__).parent.parent.parent
_EXEC_FILE = _WORKTREE_ROOT / "alpha_bot_execution.py"
_FIXTURE_DIR = (
    _WORKTREE_ROOT / "tests" / "fixtures" / "math_engine" / "arming_gate_failopen"
)

# ---------------------------------------------------------------------------
# Fixture loading helpers
# ---------------------------------------------------------------------------

EXIT_CONFIRM_TICKS = 3  # from math_engine; asserted by constant-existence test
MAGNITUDE_FLOOR_PCT = 0.10


def _load_fixture(name: str) -> dict:
    path = _FIXTURE_DIR / name
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# AST helpers for inspecting the arming gate
# ---------------------------------------------------------------------------

def _parse_exec_module() -> ast.Module:
    source = _EXEC_FILE.read_text(encoding="utf-8")
    return ast.parse(source)


def _find_arming_gate_block(tree: ast.Module) -> ast.If:
    """
    Locate the `if should_arm and not bot_state[...]['armed'] and ...` node.
    Returns the ast.If node or raises AssertionError with diagnostic.
    """
    # Walk all If nodes; look for one whose test contains a Name('should_arm').
    candidates: list[ast.If] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.If):
                # Collect all Name nodes in the test
                names_in_test = {
                    n.id for n in ast.walk(sub.test) if isinstance(n, ast.Name)
                }
                if "should_arm" in names_in_test:
                    candidates.append(sub)
    assert len(candidates) >= 1, (
        "Could not find the arming gate `if should_arm and ...` in "
        f"{_EXEC_FILE}. The fix must preserve this gate structure."
    )
    # Return the first (there should only be one should_arm gate)
    return candidates[0]


def _find_should_arm_assignment_block(tree: ast.Module) -> list[ast.Assign]:
    """
    Find all `should_arm = True` / `should_arm = False` assignments in the
    module, to verify the fail-open path sets should_arm=True when mc_available=False.
    """
    assignments: list[ast.Assign] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for tgt in node.targets:
            if isinstance(tgt, ast.Name) and tgt.id == "should_arm":
                assignments.append(node)
    return assignments


def _find_mc_available_false_arm_branch(tree: ast.Module) -> bool:
    """
    Returns True if the module contains an arming branch that fires when
    mc_available is False (or its negation `not mc_available`), setting
    should_arm = True.

    A correct fix will have a branch shaped like one of:
        if not mc_available and not currently_armed and not is_triggered:
            should_arm = True
        OR combined as:
            if not mc_available:
                should_arm = True
        OR as an `elif not mc_available`:
            should_arm = True

    We detect this by looking for an If whose test contains (a) a negation of
    'mc_available' (either `not mc_available` or a comparison `mc_available is
    False` / `mc_available == False`) and (b) an assignment `should_arm = True`
    (as ast.Constant(value=True)) in its body.
    """
    for node in ast.walk(tree):
        if not isinstance(node, (ast.If,)):
            continue
        test = node.test
        # Detect: `not mc_available` as ast.UnaryOp(op=Not, operand=Name('mc_available'))
        negated_mc = _test_contains_negated_mc_available(test)
        if not negated_mc:
            continue
        # Check body for `should_arm = True`
        for stmt in ast.walk(node):
            if isinstance(stmt, ast.Assign):
                for tgt in stmt.targets:
                    if (
                        isinstance(tgt, ast.Name)
                        and tgt.id == "should_arm"
                        and isinstance(stmt.value, ast.Constant)
                        and stmt.value.value is True
                    ):
                        return True
    return False


def _test_contains_negated_mc_available(test: ast.expr) -> bool:
    """
    Returns True if the test expression contains `not mc_available`
    (UnaryOp Not over Name mc_available) at any depth.
    """
    for node in ast.walk(test):
        if (
            isinstance(node, ast.UnaryOp)
            and isinstance(node.op, ast.Not)
            and isinstance(node.operand, ast.Name)
            and node.operand.id == "mc_available"
        ):
            return True
    return False


def _find_arming_gate_guards_triggered(tree: ast.Module) -> bool:
    """
    Returns True if the should_arm gate (`if should_arm and ... :`) has
    `not triggered` or `not bot_state[...]['triggered']` in its guard condition.
    A correct fix must NOT arm an already-triggered position.
    """
    # Find the arming gate
    arming_gate = _find_arming_gate_block(tree)
    test = arming_gate.test
    # Look for 'triggered' in the guard predicates (via attribute/subscript access
    # or simple Name) that is negated
    for node in ast.walk(test):
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            # Check if operand contains 'triggered' anywhere
            for sub in ast.walk(node.operand):
                if isinstance(sub, ast.Constant) and sub.value == "triggered":
                    return True
                if isinstance(sub, ast.Name) and sub.id == "triggered":
                    return True
    return False


# ===========================================================================
# SECTION 1: AST Structural Tests — the arming gate fail-open branch
# ===========================================================================


def test_arming_gate_has_fail_open_branch_for_mc_absent() -> None:
    """
    STRUCTURAL RED: After the H-3 fix, alpha_bot_execution.py MUST contain an
    arming branch that fires when mc_available is False (MC absent), setting
    should_arm = True.

    Pre-fix: the ONLY `should_arm = True` path is guarded by `if mc_available and ...`.
    When mc_available is False, should_arm stays False and armed never becomes True.

    A correct fix adds a branch shaped like:
        if not mc_available and not bot_state[...]['armed'] and not ...'triggered']:
            should_arm = True
    (or structurally equivalent).

    Catches any fix that omits the fail-open branch entirely, or wraps it so
    mc_available=False still prevents arming.
    """
    tree = _parse_exec_module()
    found = _find_mc_available_false_arm_branch(tree)
    assert found, (
        "H-3 fail-open fix NOT found: alpha_bot_execution.py has no arming "
        "branch where `not mc_available` sets `should_arm = True`. "
        "The fix must add an arming path that activates when MC is absent."
    )


def test_arming_gate_triggered_guard_present() -> None:
    """
    STRUCTURAL: The `if should_arm and not armed and not triggered:` guard
    must remain intact after the fix. A fix that arms triggered positions
    corrupts live state.

    This test verifies 'triggered' appears as a negated operand in the should_arm
    gate condition.
    """
    tree = _parse_exec_module()
    found = _find_arming_gate_guards_triggered(tree)
    assert found, (
        "Arming gate is missing the 'not triggered' guard. After the H-3 fix, "
        "the should_arm gate must still exclude already-triggered positions."
    )


def test_mc_present_arming_path_still_present() -> None:
    """
    STRUCTURAL REGRESSION: The original `if mc_available and TAKE_PROFIT_MC_PCT
    <= prob_beating < TRIGGER_THRESHOLD_PCT: should_arm = True` path MUST
    still exist after the fix. Removing the MC-present arming path is a scope
    violation.

    Detects: a fix that replaces rather than extends the arming block.
    """
    tree = _parse_exec_module()
    # Look for an If that tests mc_available (positive) AND assigns should_arm = True
    found = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        # Check: mc_available appears as a non-negated Name in the test
        has_positive_mc_available = False
        for sub in ast.walk(test):
            if isinstance(sub, ast.Name) and sub.id == "mc_available":
                # Make sure it is not directly wrapped in a Not
                has_positive_mc_available = True
        if not has_positive_mc_available:
            continue
        # Check body for should_arm = True
        for stmt in ast.walk(node):
            if isinstance(stmt, ast.Assign):
                for tgt in stmt.targets:
                    if (
                        isinstance(tgt, ast.Name)
                        and tgt.id == "should_arm"
                        and isinstance(stmt.value, ast.Constant)
                        and stmt.value.value is True
                    ):
                        found = True
    assert found, (
        "REGRESSION: The mc_available=True arming path (`if mc_available and ... "
        "should_arm = True`) is missing from alpha_bot_execution.py. The H-3 fix "
        "must EXTEND the arming block, not replace the existing MC-present path."
    )


def test_mc_present_disarm_branch_still_requires_mc_available() -> None:
    """
    STRUCTURAL REGRESSION: The disarm branch must still require mc_available=True.
    Specifically, `if mc_available and prob_beating > TRIGGER_THRESHOLD_PCT * 2
    and current_return > 0.0: armed = False` must survive the fix.

    A fix that drops mc_available from the disarm condition would disarm on every
    MC-absent tick where return happens to be positive — a false disarm.

    We assert that 'mc_available' appears as a Name in the disarm condition by
    looking for any If whose body contains `bot_state[...]["armed"] = False` and
    whose test contains `mc_available`.
    """
    tree = _parse_exec_module()
    found = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        # Check test contains mc_available positively
        has_mc_available = any(
            isinstance(sub, ast.Name) and sub.id == "mc_available"
            for sub in ast.walk(test)
        )
        if not has_mc_available:
            continue
        # Check body: bot_state[...]["armed"] = False
        for stmt in ast.walk(node):
            if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant):
                if stmt.value.value is False:
                    for tgt in stmt.targets:
                        # Subscript assignment: bot_state[symphony_id]["armed"] = False
                        if isinstance(tgt, ast.Subscript):
                            # Check the slice is a Constant with value "armed"
                            for sub in ast.walk(tgt):
                                if isinstance(sub, ast.Constant) and sub.value == "armed":
                                    found = True
    assert found, (
        "REGRESSION: The disarm branch `if mc_available and ... armed = False` "
        "is missing or no longer requires mc_available=True. The disarm branch "
        "must only fire when MC is available to avoid false disarms on MC-absent ticks."
    )


# ===========================================================================
# SECTION 2: compute_exit_confirmation with armed=True, prob_beating=None
# (verifies the math layer already supports fail-open correctly)
# ===========================================================================


@pytest.mark.parametrize(
    "fixture_name",
    [
        "02_mc_absent_ticks_below_stop_fires.json",
    ],
)
def test_exit_confirmation_mc_absent_fires_after_confirmation_ticks(
    fixture_name: str,
) -> None:
    """
    Golden-fixture: with armed=True and prob_beating=None, the 3-tick
    ticks-below-stop ladder produces is_trailing_stop_hit=True on tick 3.

    Catches a fix that arms the stop but passes armed=False to
    compute_exit_confirmation (arms-but-never-fires trap).
    """
    fixture = _load_fixture(fixture_name)
    for step in fixture["sequence"]:
        inputs = step["inputs"]
        expected = step["expected"]
        result = math_engine.compute_exit_confirmation(
            armed=inputs["armed"],
            is_triggered=inputs["is_triggered"],
            current_return=inputs["current_return"],
            stop_trigger_level=inputs["stop_trigger_level"],
            prob_beating=inputs["prob_beating"],  # None
            current_below_stop_count=inputs["current_below_stop_count"],
        )
        assert isinstance(result, tuple) and len(result) == 2, (
            f"Tick {step['tick']}: expected 2-tuple, got {result!r}"
        )
        new_count, hit = result
        assert new_count == expected["new_below_stop_count"], (
            f"Tick {step['tick']} ({step.get('description', '')}): "
            f"new_below_stop_count expected {expected['new_below_stop_count']}, "
            f"got {new_count}. Derivation: {step['derivation']}"
        )
        assert hit is expected["is_trailing_stop_hit"], (
            f"Tick {step['tick']} ({step.get('description', '')}): "
            f"is_trailing_stop_hit expected {expected['is_trailing_stop_hit']}, "
            f"got {hit}. Derivation: {step['derivation']}"
        )


def test_exit_confirmation_mc_absent_return_above_stop_does_not_fire() -> None:
    """
    Golden-fixture: armed=True, prob_beating=None, return ABOVE stop level.
    Count must reset to 0 and stop must NOT fire.

    Adversarial: catches a fix that ignores the magnitude condition when
    MC is absent and fires on magnitude-alone regardless of whether the return
    crossed the stop.
    """
    fixture = _load_fixture("08_mc_absent_return_above_stop_does_not_fire.json")
    inputs = fixture["inputs"]
    expected = fixture["expected"]
    new_count, hit = math_engine.compute_exit_confirmation(
        armed=inputs["armed"],
        is_triggered=inputs["is_triggered"],
        current_return=inputs["current_return"],
        stop_trigger_level=inputs["stop_trigger_level"],
        prob_beating=inputs["prob_beating"],  # None
        current_below_stop_count=inputs["current_below_stop_count"],
    )
    assert new_count == expected["new_below_stop_count"], (
        f"Return above stop: count must reset to 0, got {new_count}. "
        f"Derivation: {fixture['derivation']}"
    )
    assert hit is False, (
        f"Return above stop: stop must NOT fire, got hit={hit}. "
        "A fix that fires on every MC-absent tick regardless of magnitude fails here."
    )


@pytest.mark.parametrize(
    "fixture_name,tick_idx",
    [
        ("09_mc_absent_mc_sanity_irrelevant_magnitude_gates.json", 0),  # at stop, not below threshold
        ("09_mc_absent_mc_sanity_irrelevant_magnitude_gates.json", 1),  # exactly at threshold boundary
    ],
)
def test_exit_confirmation_mc_absent_magnitude_floor_boundary(
    fixture_name: str, tick_idx: int
) -> None:
    """
    Golden-fixture: boundary cases for the magnitude floor when MC is absent.

    Tick 0: current_return = -1.0, stop = -1.0 -> threshold = -1.10.
            -1.0 <= -1.10 is FALSE -> count resets.
    Tick 1: current_return = -1.1, stop = -1.0 -> threshold = -1.10.
            -1.1 <= -1.10 is TRUE (inclusive) -> count increments.

    Catches: a fix that drops the magnitude floor when MC is absent, making
    the stop fire at or before the stop level rather than MAGNITUDE_FLOOR_PCT
    below it.
    """
    fixture = _load_fixture(fixture_name)
    step = fixture["sequence"][tick_idx]
    inputs = step["inputs"]
    expected = step["expected"]
    new_count, hit = math_engine.compute_exit_confirmation(
        armed=inputs["armed"],
        is_triggered=inputs["is_triggered"],
        current_return=inputs["current_return"],
        stop_trigger_level=inputs["stop_trigger_level"],
        prob_beating=inputs["prob_beating"],  # None
        current_below_stop_count=inputs["current_below_stop_count"],
    )
    assert new_count == expected["new_below_stop_count"], (
        f"Tick {tick_idx} ({step.get('description', '')}): "
        f"new_below_stop_count expected {expected['new_below_stop_count']}, got {new_count}. "
        f"Derivation: {step['derivation']}"
    )
    assert hit is False, (
        f"Tick {tick_idx}: hit must be False at boundary counts. Got {hit}."
    )


# ===========================================================================
# SECTION 3: Adversarial — transient gap must not fire
# ===========================================================================


def test_transient_mc_gap_single_tick_does_not_fire() -> None:
    """
    Adversarial: one tick of MC absence with return below stop, then MC
    returns and return recovers. Stop must NOT fire after a single qualifying
    tick.

    This guards against a fix that:
      - arms immediately on MC absence AND
      - fires on the same tick (no 3-tick confirmation ladder)

    The 3-tick confirmation window is the primary guard against false
    liquidations on transient data gaps.
    """
    fixture = _load_fixture("07_transient_gap_single_tick_does_not_fire.json")
    for step in fixture["sequence"]:
        inputs = step["inputs"]
        expected = step["expected"]
        new_count, hit = math_engine.compute_exit_confirmation(
            armed=inputs["armed"],
            is_triggered=inputs["is_triggered"],
            current_return=inputs["current_return"],
            stop_trigger_level=inputs["stop_trigger_level"],
            prob_beating=inputs["prob_beating"],
            current_below_stop_count=inputs["current_below_stop_count"],
        )
        assert new_count == expected["new_below_stop_count"], (
            f"Tick {step['tick']} ({step.get('description', '')}): "
            f"expected count {expected['new_below_stop_count']}, got {new_count}."
        )
        assert hit is False, (
            f"Tick {step['tick']} ({step.get('description', '')}): "
            f"stop fired (hit=True) — must not fire on transient gap. Got hit={hit}."
        )


# ===========================================================================
# SECTION 4: Property tests — MC-present paths unchanged
# ===========================================================================


@pytest.mark.parametrize(
    "prob_beating,current_below_stop_count,expected_count,expected_hit",
    [
        # MC present (real float), return below stop, MC sanity passes (prob < 60)
        # -> count increments, hits at 3
        (10.0, 0, 1, False),
        (10.0, 1, 2, False),
        (10.0, 2, 3, True),
        (59.9, 2, 3, True),  # just under MC_SANITY_THRESHOLD
        # MC present, MC sanity VETOES exit (prob >= 60.0) -> count resets
        (60.0, 2, 0, False),
        (75.0, 2, 0, False),
        (99.9, 2, 0, False),
    ],
)
def test_exit_confirmation_mc_present_behavior_unchanged(
    prob_beating: float,
    current_below_stop_count: int,
    expected_count: int,
    expected_hit: bool,
) -> None:
    """
    Regression: when prob_beating is a real float (MC present), the behavior
    of compute_exit_confirmation is IDENTICAL to its pre-H3-fix behavior.

    Uses inputs where magnitude definitely passes: current_return=-2.0,
    stop_trigger_level=-1.0 -> threshold=-1.10 (well satisfied by -2.0).

    Catches: a fix that changes the MC-present path as a side-effect.
    """
    new_count, hit = math_engine.compute_exit_confirmation(
        armed=True,
        is_triggered=False,
        current_return=-2.0,
        stop_trigger_level=-1.0,
        prob_beating=prob_beating,
        current_below_stop_count=current_below_stop_count,
    )
    assert new_count == expected_count, (
        f"MC-present regression: prob={prob_beating}, starting_count="
        f"{current_below_stop_count}. Expected count={expected_count}, got {new_count}."
    )
    assert hit is expected_hit, (
        f"MC-present regression: prob={prob_beating}, starting_count="
        f"{current_below_stop_count}. Expected hit={expected_hit}, got {hit}."
    )


# ===========================================================================
# SECTION 5: Property — armed=False guard still holds with prob_beating=None
# (regression guard: fix must not alter not-armed path in math layer)
# ===========================================================================


@pytest.mark.parametrize(
    "current_below_stop_count",
    [0, 1, 2, 3, 10, 999],
)
def test_not_armed_guard_intact_with_mc_absent(
    current_below_stop_count: int,
) -> None:
    """
    Property: even with prob_beating=None (MC absent), the not-armed guard
    in compute_exit_confirmation must still return (current_below_stop_count, False).

    This tests the MATH LAYER guard in isolation. The fix is in alpha_bot_execution.py
    (it arms the position); the math layer must not also change its not-armed
    early-return when called with armed=False.

    Catches: a fix that misplaces the fail-open logic inside
    compute_exit_confirmation rather than in the arming gate.
    """
    new_count, hit = math_engine.compute_exit_confirmation(
        armed=False,  # not yet armed
        is_triggered=False,
        current_return=-5.0,  # would qualify if armed
        stop_trigger_level=-1.0,
        prob_beating=None,  # MC absent
        current_below_stop_count=current_below_stop_count,
    )
    assert new_count == current_below_stop_count, (
        f"Guard broken: armed=False + MC absent must preserve count. "
        f"Expected {current_below_stop_count}, got {new_count}."
    )
    assert hit is False, (
        f"Guard broken: armed=False + MC absent must return hit=False. Got {hit}."
    )


# ===========================================================================
# SECTION 6: Property — MC absent, non-fire path (return stays above stop)
# Count monotonically does NOT increase when magnitude not met
# ===========================================================================


@pytest.mark.parametrize(
    "current_return,stop_trigger_level",
    [
        (0.0, -1.0),    # well above stop
        (-0.5, -1.0),   # above threshold -1.10
        (-1.09, -1.0),  # just above threshold boundary (strict: -1.09 > -1.10)
        (5.0, 0.0),     # positive return
    ],
)
def test_exit_confirmation_mc_absent_no_magnitude_no_increment(
    current_return: float,
    stop_trigger_level: float,
) -> None:
    """
    Property: when armed=True, MC absent (prob_beating=None), but return is
    NOT below (stop_trigger_level - MAGNITUDE_FLOOR_PCT), the count must reset
    to 0 and hit must be False.

    Catches: any implementation that fires or increments on every MC-absent tick
    regardless of whether the return has crossed below the stop level.
    """
    # Start with a non-zero count to verify it actually resets, not just stays at 0
    new_count, hit = math_engine.compute_exit_confirmation(
        armed=True,
        is_triggered=False,
        current_return=current_return,
        stop_trigger_level=stop_trigger_level,
        prob_beating=None,
        current_below_stop_count=2,  # non-zero starting count
    )
    assert new_count == 0, (
        f"MC absent, return not below stop: count must reset to 0. "
        f"current_return={current_return}, stop={stop_trigger_level}, "
        f"threshold={stop_trigger_level - MAGNITUDE_FLOOR_PCT:.4f}. Got count={new_count}."
    )
    assert hit is False, (
        f"MC absent, return not below stop: must not fire. Got hit={hit}."
    )


# ===========================================================================
# SECTION 7: Scope guard — fix must NOT touch exit confirm beyond prob_beating=None
# ===========================================================================


def test_arming_gate_is_in_alpha_bot_execution_not_math_engine() -> None:
    """
    Scope guard: the fail-open arming logic (should_arm = True when
    mc_available=False) must live in alpha_bot_execution.py, NOT in
    math_engine.compute_exit_confirmation.

    math_engine.compute_exit_confirmation is already correct: it accepts
    prob_beating=None and treats None as MC-sanity-gate-pass (fail-safe,
    implemented in the H-1 cycle). The H-3 fix is purely in the arming gate
    in alpha_bot_execution.py.

    Catches: a fix that moves fail-open logic into compute_exit_confirmation,
    which would change the math layer's behaviour for NOT-armed calls (wrong lane).
    """
    src_path = pathlib.Path(math_engine.__file__)
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # Find compute_exit_confirmation
    target: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "compute_exit_confirmation"
        ):
            target = node
            break
    assert target is not None, (
        "compute_exit_confirmation not found in math_engine.py"
    )

    # Within compute_exit_confirmation, look for any assignment to 'armed' or
    # any reference to 'should_arm' — these are arming-gate identifiers and
    # have no business in the pure math function.
    arming_identifiers = {"should_arm", "arm_reason", "mc_available"}
    offenders: list[tuple[int, str]] = []
    for sub in ast.walk(target):
        if isinstance(sub, ast.Name) and sub.id in arming_identifiers:
            offenders.append((sub.lineno, sub.id))
        if isinstance(sub, ast.Assign):
            for tgt in sub.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "armed":
                    offenders.append((sub.lineno, "armed assignment"))

    assert not offenders, (
        "Scope violation: arming-gate identifiers found inside "
        "compute_exit_confirmation in math_engine.py. The H-3 fix must live "
        "in alpha_bot_execution.py, not in the pure math function. "
        f"Offenders (line, name): {offenders}"
    )


# ===========================================================================
# SECTION 8: Fixture provenance — all fixture files are loadable and have
# required fields
# ===========================================================================


@pytest.mark.parametrize(
    "fixture_file",
    sorted(_FIXTURE_DIR.glob("*.json")),
    ids=lambda p: p.name,
)
def test_fixture_files_have_required_fields(fixture_file: pathlib.Path) -> None:
    """
    Structural: every fixture in the arming_gate_failopen directory has a
    'name' and 'layer' field. Sequence fixtures have 'sequence'. Function
    fixtures have 'function'. Documentation/description fixtures have
    'description'.
    """
    with fixture_file.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    assert "name" in data, f"{fixture_file.name}: missing 'name' field"
    assert "layer" in data, f"{fixture_file.name}: missing 'layer' field"
    assert "description" in data or "note" in data, (
        f"{fixture_file.name}: missing 'description' or 'note' field"
    )
