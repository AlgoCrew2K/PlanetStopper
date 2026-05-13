"""
Golden-fixture + property tests for the VWAP-bleed arm-threshold layer of
math_engine.py.

Scope (Gate-1 approved): NEW pure function
math_engine.compute_vwap_bleed_arm_threshold, extracted from
alpha_bot_execution.py:525-526 (cycle 9 -- VWAP block B of 3). This is a
refactor-extraction cycle, not a new feature; the inline producer (those 2
source lines on current main, post cycle-8 merge) is the spec the GREEN
phase must preserve.

Inline producer pinned verbatim (alpha_bot_execution.py:525-526 on main
after cycle 8 merge):

    raw_dynamic_bleed = -(symphony_vol * acc_VWAP_BLEED_MULTIPLIER)
    acc_VWAP_BLEED_ARM_PCT = max(-3.0, min(-0.5, raw_dynamic_bleed))

The clamp produces a value in [-3.0, -0.5] (always negative). This is the
dynamic exit threshold for the VWAP-bleed system: current_return must drop
to AT LEAST this value (in percentage points) for the bleed counter to
start ticking.

PROPOSED PURE INTERFACE (confirmed):

    def compute_vwap_bleed_arm_threshold(
        symphony_vol: float,
        bleed_multiplier: float,
    ) -> float:
        '''
        Returns the dynamic VWAP-bleed arm threshold (in percentage points,
        always negative -- bleeding means dropping below zero).

        Computation:
          raw = -(symphony_vol * bleed_multiplier)
          result = max(VWAP_BLEED_ARM_MIN, min(VWAP_BLEED_ARM_MAX, raw))

        Where (named constants introduced in GREEN):
          VWAP_BLEED_ARM_MIN = -3.0   # most-negative clamp (deepest arm)
          VWAP_BLEED_ARM_MAX = -0.5   # least-negative clamp (shallowest arm)

        Interpretation:
          - High vol or high multiplier produces a more-negative raw, which
            gets clamped at VWAP_BLEED_ARM_MIN (most permissive arm).
          - Low vol produces a near-zero raw, clamped at VWAP_BLEED_ARM_MAX
            (most cautious arm).

        Pure. No I/O. No state.
        '''

LOAD-BEARING INVARIANTS:
  - OUTPUT BOUND: result is always in the closed interval
    [VWAP_BLEED_ARM_MIN, VWAP_BLEED_ARM_MAX] = [-3.0, -0.5] regardless of
    input.
  - NON-POSITIVITY: result is always <= 0 (a "bleed threshold" -- never
    positive). This follows from the clamp window upper bound being -0.5.
  - MONOTONICITY: with bleed_multiplier fixed and strictly positive, result
    is non-increasing in symphony_vol until the lower clamp activates
    (then result is constant at VWAP_BLEED_ARM_MIN).
  - CONSTANT-PROVENANCE: -3.0 and -0.5 MUST be named module-level
    constants (VWAP_BLEED_ARM_MIN / VWAP_BLEED_ARM_MAX) with source
    comments. The function body MUST use the named constants, not bare
    literals -- per the project rule 'no magic numbers in math_engine.py'.

Tolerance policy (golden-fixture & property tests):
  - pytest.approx(rel=1e-9, abs=1e-12).
  - rel=1e-9 catches all algorithm errors (a wrong-by-1% impl fails by ~1e-2,
    seven orders of magnitude over the tolerance) while absorbing IEEE-754
    drift from chained mult/neg. abs=1e-12 anchors zero-magnitude
    comparisons. All clamp boundaries here (-3.0, -0.5) are exactly
    representable IEEE-754 doubles, but inputs that PRODUCE them via
    arithmetic may drift by ~1ulp, which the tolerance absorbs.

Provenance (HARD): every expected value in
tests/fixtures/math_engine/vwap_bleed_arm/*.json is DERIVED BY HAND from
the inline producer's formula and pinned in the fixture's 'derivation'
field -- NOT captured from a current implementation
(compute_vwap_bleed_arm_threshold does not exist yet; this is RED).
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
    / "vwap_bleed_arm"
)

# Tolerance: see module docstring. rel=1e-9 catches algorithm errors by
# seven+ orders of magnitude; abs=1e-12 anchors zero-magnitude comparisons.
TOL_REL = 1e-9
TOL_ABS = 1e-12

# Reference clamp window values used by property tests for cross-checking.
# These are the EXPECTED named constants in math_engine.py (asserted by the
# constant-provenance scanner). Hard-coded here in the TEST file is correct:
# tests own the spec; the impl must conform.
REF_VWAP_BLEED_ARM_MIN = -3.0
REF_VWAP_BLEED_ARM_MAX = -0.5


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
def test_vwap_bleed_arm_threshold_matches_derived_expected(
    fixture_name: str, fixture: dict[str, Any]
) -> None:
    """
    Every fixture's expected float is DERIVED BY HAND in the fixture's
    'derivation' field. This test asserts compute_vwap_bleed_arm_threshold
    produces those values. The function does not exist yet -- this is RED.
    """
    func_name = fixture["function"]
    assert func_name == "compute_vwap_bleed_arm_threshold", (
        f"{fixture_name}: only compute_vwap_bleed_arm_threshold is in scope "
        f"for this cycle"
    )

    inputs = fixture["inputs"]
    expected = fixture["expected"]

    result = math_engine.compute_vwap_bleed_arm_threshold(
        symphony_vol=inputs["symphony_vol"],
        bleed_multiplier=inputs["bleed_multiplier"],
    )

    assert result == pytest.approx(expected, rel=TOL_REL, abs=TOL_ABS), (
        f"Fixture {fixture_name}: bleed-arm threshold mismatch. "
        f"Expected {expected} (derivation: {fixture['derivation']}), "
        f"got {result}"
    )


# --- Property: output is always within the clamp window ---------------------


@pytest.mark.parametrize(
    "symphony_vol,bleed_multiplier",
    [
        # Range chosen to span: deep negative raw (clamped low), in-window
        # raw, raw near zero / above upper clamp, and degenerate negatives.
        (0.0,    0.0),
        (0.0,    1.5),
        (0.05,   1.0),   # raw = -0.05, above upper clamp
        (0.3,    1.0),   # raw = -0.3, above upper clamp
        (0.5,    1.0),   # raw = -0.5, on upper clamp
        (0.7,    1.0),   # raw = -0.7, in-window
        (1.0,    1.5),   # raw = -1.5, in-window
        (1.5,    1.5),   # raw = -2.25, in-window
        (2.0,    1.5),   # raw = -3.0, on lower clamp
        (3.0,    1.5),   # raw = -4.5, below lower clamp
        (10.0,   5.0),   # raw = -50.0, far below lower clamp
        (-1.0,   1.5),   # degenerate: raw > 0
        (-100.0, 100.0), # degenerate large: raw > 0
        (1e-12,  1.0),   # tiny positive vol
        (1e9,    1e-9),  # huge vol, tiny multiplier, raw = -1.0 in-window
    ],
)
def test_output_is_always_within_clamp_window(
    symphony_vol: float, bleed_multiplier: float
) -> None:
    """
    Invariant: for ANY input, the output is in the closed interval
    [VWAP_BLEED_ARM_MIN, VWAP_BLEED_ARM_MAX] = [-3.0, -0.5]. Sweeps
    several regimes (in-window, on each boundary, above upper, below
    lower, degenerate negative inputs producing positive raw, near-zero
    edge cases, and extreme magnitudes).
    """
    result = math_engine.compute_vwap_bleed_arm_threshold(
        symphony_vol=symphony_vol, bleed_multiplier=bleed_multiplier
    )
    assert REF_VWAP_BLEED_ARM_MIN <= result <= REF_VWAP_BLEED_ARM_MAX, (
        f"Output {result} is outside the clamp window "
        f"[{REF_VWAP_BLEED_ARM_MIN}, {REF_VWAP_BLEED_ARM_MAX}] for "
        f"inputs (symphony_vol={symphony_vol}, "
        f"bleed_multiplier={bleed_multiplier}). The clamp invariant is the "
        f"core guarantee of this function."
    )


# --- Property: output is always non-positive (it's a bleed threshold) -------


@pytest.mark.parametrize(
    "symphony_vol,bleed_multiplier",
    [
        (0.0, 0.0),
        (0.0, 1.5),
        (1.0, 1.5),
        (5.0, 5.0),
        (-1.0, 1.5),     # degenerate
        (1.0, -1.5),     # degenerate
        (-1.0, -1.5),    # degenerate
        (1e-15, 1e-15),  # near-zero
    ],
)
def test_output_is_always_non_positive(
    symphony_vol: float, bleed_multiplier: float
) -> None:
    """
    Invariant: output <= 0 for ALL inputs (a bleed threshold is a drop
    measured in percentage points; positive thresholds would be
    semantically wrong -- you cannot 'bleed up').

    The clamp window upper bound is -0.5, so this property is implied by
    the in-window invariant, but it's asserted SEPARATELY because the
    semantic meaning matters: an impl that accidentally inverted the sign
    of the clamp window (returning [+0.5, +3.0]) would fail this test
    immediately even before the in-window check picked up the wider issue.
    """
    result = math_engine.compute_vwap_bleed_arm_threshold(
        symphony_vol=symphony_vol, bleed_multiplier=bleed_multiplier
    )
    assert result <= 0.0, (
        f"Output {result} is positive, but a bleed-arm threshold must be "
        f"<= 0 (in percentage points). Inputs: symphony_vol={symphony_vol}, "
        f"bleed_multiplier={bleed_multiplier}."
    )


# --- Property: monotonicity in symphony_vol (multiplier fixed > 0) ----------


def test_monotone_non_increasing_in_vol_for_fixed_positive_multiplier() -> None:
    """
    Invariant: holding bleed_multiplier fixed at a strictly positive value,
    the output is non-increasing in symphony_vol. As symphony_vol grows,
    raw = -(vol * multiplier) becomes more negative, so the clamped result
    must move down (or stay clamped at VWAP_BLEED_ARM_MIN).

    Sweep symphony_vol monotonically increasing across regimes:
      - regime A: vol near zero -> raw near zero -> clamped to MAX (-0.5)
      - regime B: vol in middle -> raw in-window -> linear pass-through
      - regime C: vol large -> raw below MIN -> clamped to MIN (-3.0)

    Verifies STRICT NON-INCREASING (each next <= previous). Catches an
    impl that flipped a sign in the clamp (e.g., wrote min(-3.0, max(-0.5,
    raw)) instead of max(-3.0, min(-0.5, raw))), or dropped the negation.
    """
    multiplier = 1.5  # fixed, strictly positive
    vols = [
        0.0, 0.1, 0.2, 0.3, 0.33,    # tiny: raw above MAX -> all == -0.5
        0.5, 0.7, 1.0, 1.2, 1.5,     # mid: raw in-window -> linear
        2.0, 2.5, 3.0, 5.0, 100.0,   # large: raw below MIN -> all == -3.0
    ]
    results = [
        math_engine.compute_vwap_bleed_arm_threshold(
            symphony_vol=v, bleed_multiplier=multiplier
        )
        for v in vols
    ]
    for i in range(1, len(results)):
        assert results[i] <= results[i - 1] + TOL_ABS, (
            f"Monotonicity violation: at vol={vols[i]}, result={results[i]}; "
            f"at vol={vols[i - 1]}, result={results[i - 1]}. Output must be "
            f"non-increasing as symphony_vol grows (multiplier fixed > 0). "
            f"Full sweep: vols={vols}, results={results}."
        )


# --- Property: determinism --------------------------------------------------


@pytest.mark.parametrize(
    "symphony_vol,bleed_multiplier",
    [
        (0.0, 0.0),
        (1.0, 1.5),
        (2.0, 1.5),
        (5.0, 5.0),
        (-1.0, 1.5),
    ],
)
def test_function_is_pure_repeat_call_returns_identical_result(
    symphony_vol: float, bleed_multiplier: float
) -> None:
    """
    Sanity: a pure function returns identical results when called twice
    with the same arguments. Catches an impl that stashes state in a
    module-level variable, uses an unseeded RNG, or has a hidden
    time-dependence.
    """
    a = math_engine.compute_vwap_bleed_arm_threshold(
        symphony_vol=symphony_vol, bleed_multiplier=bleed_multiplier
    )
    b = math_engine.compute_vwap_bleed_arm_threshold(
        symphony_vol=symphony_vol, bleed_multiplier=bleed_multiplier
    )
    assert a == b, (
        f"Non-deterministic output: first call returned {a}, second "
        f"returned {b}. Inputs: symphony_vol={symphony_vol}, "
        f"bleed_multiplier={bleed_multiplier}."
    )


# --- Property: return type contract (plain float) ---------------------------


@pytest.mark.parametrize(
    "symphony_vol,bleed_multiplier",
    [
        # Path A: in-window raw (returns unchanged raw, a computed float)
        (1.0, 1.5),
        # Path B: clamped at MIN (returns the MIN constant)
        (10.0, 10.0),
        # Path C: clamped at MAX (returns the MAX constant)
        (0.1, 1.0),
        # Path D: zero input (returns MAX constant via -0.0 path)
        (0.0, 0.0),
    ],
)
def test_return_type_contract_plain_float(
    symphony_vol: float, bleed_multiplier: float
) -> None:
    """
    Contract: returns EXACTLY a plain Python float on every code path.
    Strict `type() is float` check because:
      - the returned value is serialized to SQLite (chart history /
        decision log) and JSON (Discord embed); numpy floats serialize
        inconsistently across SQLite/json modules.
      - bool is a subclass of int (which would auto-coerce in float
        contexts); strict type() blocks accidental int/bool return.
      - Each code path (in-window, clamped-low, clamped-high) returns a
        different SOURCE value: the raw arithmetic, VWAP_BLEED_ARM_MIN,
        or VWAP_BLEED_ARM_MAX. An impl that declared the constants as
        int (-3 and -0 are not legal but -3 is) would slip through a
        single-fixture check. Sweeping all three code paths catches it.

    Same defensive pattern as cycles 3-8.
    """
    result = math_engine.compute_vwap_bleed_arm_threshold(
        symphony_vol=symphony_vol, bleed_multiplier=bleed_multiplier
    )
    assert type(result) is float, (
        f"Return value must be EXACTLY a plain Python float, got "
        f"{type(result).__name__} (value={result!r}). Inputs: "
        f"symphony_vol={symphony_vol}, bleed_multiplier={bleed_multiplier}. "
        f"Numpy floats / ints / bools forbidden; downstream SQLite + JSON "
        f"serialization relies on plain float."
    )


# --- Constant-provenance: named constants exist + are sourced ---------------


def test_named_clamp_constants_exist_with_source_comments() -> None:
    """
    Project rule: 'No magic numbers in math_engine.py -- every constant
    named + source comment.'

    For compute_vwap_bleed_arm_threshold, the clamp boundaries -3.0 and
    -0.5 MUST be defined as module-level named constants
    (VWAP_BLEED_ARM_MIN, VWAP_BLEED_ARM_MAX), with the assigned literal
    on a line that ALSO has a trailing comment explaining the constant's
    role.

    The function does not exist yet (RED); the constants do not exist
    yet (RED). This assertion will pass only after GREEN introduces both.

    NOTE ON IMPLEMENTATION: uses runtime hasattr + getattr equality rather
    than AST constant-value scanning. In Python 3.8+, the CPython parser
    represents negative literals (e.g., -3.0) as
    ast.UnaryOp(op=ast.USub(), operand=ast.Constant(value=3.0)), NOT as
    ast.Constant(value=-3.0). An AST check for isinstance(node.value,
    ast.Constant) will never match a negative assignment and would produce
    a false failure. The runtime pattern (used for TRIGGERED_OVERRIDE_LEVEL
    in test_breakeven_update.py) is correct for any sign. Source-comment
    enforcement is still done via AST on the assignment line number.
    """
    src_path = pathlib.Path(math_engine.__file__)
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    src_lines = source.splitlines()

    expected_constants = {
        "VWAP_BLEED_ARM_MIN": -3.0,
        "VWAP_BLEED_ARM_MAX": -0.5,
    }

    # Step 1: runtime existence + value check (sign-safe; avoids AST
    # negative-literal representation issue described above).
    for name, expected_val in expected_constants.items():
        assert hasattr(math_engine, name), (
            f"Expected module-level constant {name} = {expected_val} in "
            f"math_engine.py, but it was not found at runtime. The project "
            f"rule 'every constant named + source comment' forbids bare "
            f"-3.0 / -0.5 literals inside compute_vwap_bleed_arm_threshold."
        )
        actual_val = getattr(math_engine, name)
        assert actual_val == pytest.approx(expected_val, rel=TOL_REL, abs=TOL_ABS), (
            # rel=1e-9 / abs=1e-12 matches the module tolerance policy; these
            # boundary values are exactly representable IEEE-754 doubles so
            # drift will be 0.0, but approx is used for consistency.
            f"Constant {name} in math_engine.py has value {actual_val!r}, "
            f"expected {expected_val!r}."
        )

    # Step 2: source-comment check -- the assignment line must have a
    # trailing comment (before = lhs/rhs, after = comment text, both
    # non-empty). Locate the lineno via AST, which reliably finds the
    # assignment node regardless of the sign of the RHS value.
    found_linenos: dict[str, int] = {}
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if (
                    isinstance(tgt, ast.Name)
                    and tgt.id in expected_constants
                ):
                    found_linenos[tgt.id] = node.lineno

    for name in expected_constants:
        assert name in found_linenos, (
            f"AST scan could not locate the assignment for {name} at module "
            f"level in math_engine.py. The constant exists at runtime but "
            f"may be set dynamically (e.g., in an __init__ block) which "
            f"violates the 'named module-level constant' requirement."
        )
        lineno = found_linenos[name]
        line = src_lines[lineno - 1] if lineno - 1 < len(src_lines) else ""
        before, _, after = line.partition("#")
        assert before.strip() != "" and after.strip() != "", (
            f"Constant {name} on line {lineno} of math_engine.py has no "
            f"trailing source comment. Project rule: 'every constant named "
            f"+ source comment'. Line content: {line!r}."
        )


def test_no_bare_clamp_literals_in_function_body() -> None:
    """
    Project rule reinforced: the function body MUST reference the named
    constants VWAP_BLEED_ARM_MIN / VWAP_BLEED_ARM_MAX -- NOT bare -3.0 /
    -0.5 literals. A copy-paste from the inline producer
    (max(-3.0, min(-0.5, raw))) would slip in bare literals; this scanner
    catches it.

    Whitelist for structural literals: {0, 1, -1, 0.0, 1.0}.
    Any other numeric literal in the function body is an offender unless
    it appears on a line with a named-constant assignment or an
    explanatory trailing source comment.

    The function does not exist yet (RED): the assertion that the function
    is present will fail until GREEN creates it.
    """
    src_path = pathlib.Path(math_engine.__file__)
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    src_lines = source.splitlines()

    STRUCTURAL = {0, 1, -1}  # int 0 hashes same as float 0.0 in a set

    target: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "compute_vwap_bleed_arm_threshold"
        ):
            target = node
            break
    assert target is not None, (
        "compute_vwap_bleed_arm_threshold not found in math_engine.py "
        "(expected in RED; the function will be created in GREEN)"
    )

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
            if isinstance(val, bool):
                continue
            if val in STRUCTURAL:
                continue
            if sub.lineno in named_literal_lines:
                continue
            if line_has_comment(sub.lineno):
                continue
            offenders.append((sub.lineno, val))

    assert not offenders, (
        "Unnamed magic numbers in compute_vwap_bleed_arm_threshold (project "
        "rule: every constant in math_engine.py must be named + "
        f"source-commented). Offenders (line, value): {offenders}. The "
        f"clamp boundaries must reference VWAP_BLEED_ARM_MIN / "
        f"VWAP_BLEED_ARM_MAX, not bare -3.0 / -0.5 literals copy-pasted "
        f"from the inline producer."
    )
