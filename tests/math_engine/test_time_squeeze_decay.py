"""
Golden-fixture + property tests for the time-squeeze decay layer of
math_engine.py.

Scope (Gate-1 approved): NEW pure function math_engine.compute_time_squeeze_decay,
extracted from alpha_bot_execution.py:574-585. This is a refactor-extraction
cycle (cycle 4 of 7), not a new feature; the inline producer (those 12 source
lines, less the time_ratio computation on line 572-574 which stays in the
caller) is the spec the GREEN phase must preserve.

Inline producer pinned verbatim (alpha_bot_execution.py:574-585 on main):

    # time_ratio computed earlier (stays in caller):
    # m_open_dt = current_et.replace(hour=start_h, minute=start_m, ...)
    # m_close_dt = current_et.replace(hour=16, minute=0, ...)
    # time_ratio = max(0.0, min(1.0, (current_et - m_open_dt).total_seconds()
    #                                  / (m_close_dt - m_open_dt).total_seconds()))

    decay_curve = math.log10(1 + 9 * time_ratio)

    # Calculate Dynamic Multiplier (Decays from 1.5x to 0.5x)
    mult_open = 1.5
    mult_close = 0.5
    dynamic_multiplier = mult_open - ((mult_open - mult_close) * decay_curve)

    # Calculate Minimum Floors (Decays from 0.3% to 0.15%)
    min_stop_open = 0.3
    min_stop_close = 0.15
    dynamic_min_stop = min_stop_open - ((min_stop_open - min_stop_close) * decay_curve)

PROPOSED PURE INTERFACE (confirmed -- no revision; the gate-2 proposal isolates
the smallest non-trivial unit: a one-input scalar transform returning two
co-derived scalars. Splitting multiplier and min_stop into two functions would
force callers to make two calls and would re-compute log10 twice; returning a
tuple is the right unit):

    def compute_time_squeeze_decay(time_ratio: float) -> tuple[float, float]:
        '''Returns (dynamic_multiplier, dynamic_min_stop).'''

The function is PURE: no I/O, no state, no datetime handling. Caller owns the
time_ratio calculation (datetime arithmetic + the [0, 1] clamp).

CONTRACT - caller-clamped input:
The pure math layer TRUSTS the caller to pass time_ratio in [0.0, 1.0]. The
caller already does `max(0.0, min(1.0, ...))` on line 574 before passing.
The math layer does NOT re-clamp and does NOT validate. Out-of-range inputs
produce undefined results -- no fixtures pin this regime.

Open question (NOT pinning behavior):
- For time_ratio > 1, decay_curve > 1 and dynamic_multiplier drops below
  MULT_CLOSE (i.e., a negative quantity for very large time_ratio); the
  function is mathematically well-defined for time_ratio > -1/9 but the
  contract says callers don't pass these values. If a future cycle adds
  validation, that decision belongs in its own A/C cycle, not this one.

Tolerance policy:
- pytest.approx(rel=1e-9, abs=1e-12). The boundary fixtures (time_ratio=0 and
  time_ratio=1) are EXACT in IEEE-754 (log10(1)=0 and log10(10)=1 exactly).
  The midday + early + late fixtures pick up ~1 ulp of drift from the log10
  step; the constructed-clean fixture (decay=0.5 exactly) picks up ~1 ulp from
  the final subtraction (0.3 - 0.075 evaluates to 0.22499999999999998, not
  exactly 0.225). rel=1e-9 is comfortably loose for these ulp-class errors
  and tight enough to catch any algorithmic divergence (wrong scalar inside
  log10, wrong linear-interp end-points, sign flip, log2/ln substitution).

Provenance (HARD): every expected (dynamic_multiplier, dynamic_min_stop) in
tests/fixtures/math_engine/time_squeeze_decay/*.json is DERIVED BY HAND from
the inline producer's formula and pinned in the fixture's 'derivation' field --
NOT captured from a current implementation (compute_time_squeeze_decay does
not exist yet; this is RED). The math (log10 + linear interpolation) is
analytic and the derivations are spelled out per-fixture.

Adversarial fixture intent (each fixture targets a SPECIFIC class of wrong impl):
- Fixture 01 (time_ratio=0): catches a flipped linear-interp direction (impl
  that uses MULT_CLOSE as the at-open value).
- Fixture 02 (time_ratio=1): catches the same flip from the other side.
- Fixture 03 (midday=0.5): catches an impl that used LINEAR decay instead of
  log10 (linear would give 1.0 here; log10 gives ~0.76).
- Fixture 04 (early=0.1): catches an off-by-scaling-factor inside log10
  (e.g., log10(1 + time_ratio) instead of log10(1 + 9*time_ratio)).
- Fixture 05 (late=0.9): pins the plateau region; catches an impl that
  swapped log10 for ln (ln(9.1) ~= 2.21, very different from 0.96).
- Fixture 06 (decay=0.5 exactly): catches an impl that miscomputed the
  linear-interp coefficient, since dynamic_multiplier must be EXACTLY 1.0
  (the midpoint of [MULT_CLOSE, MULT_OPEN]) at this constructed time_ratio.
"""

from __future__ import annotations

import ast
import json
import math
import pathlib
from typing import Any

import pytest

import math_engine

FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "math_engine"
    / "time_squeeze_decay"
)

# --- Tolerance --------------------------------------------------------------

# rel=1e-9 catches any algorithmic divergence (wrong inner scalar in log10,
# log10 -> ln swap, flipped linear-interp end-points). abs=1e-12 lets exact-
# zero / exact-equal-MULT_OPEN expecteds match cleanly. A wrong impl would
# miss by orders of magnitude, not by ~1 ulp.
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
def test_time_squeeze_decay_matches_derived_expected(
    fixture_name: str, fixture: dict[str, Any]
) -> None:
    """
    Every fixture's expected (dynamic_multiplier, dynamic_min_stop) is DERIVED
    BY HAND in the fixture's 'derivation' field. This test asserts
    compute_time_squeeze_decay produces that tuple. Function does not exist
    yet -- this is RED.
    """
    func_name = fixture["function"]
    assert func_name == "compute_time_squeeze_decay", (
        f"{fixture_name}: only compute_time_squeeze_decay is in scope for this cycle"
    )

    inputs = fixture["inputs"]
    expected = fixture["expected"]

    dynamic_multiplier, dynamic_min_stop = math_engine.compute_time_squeeze_decay(
        time_ratio=inputs["time_ratio"],
    )

    assert dynamic_multiplier == pytest.approx(
        expected["dynamic_multiplier"], rel=APPROX_REL, abs=APPROX_ABS
    ), (
        f"Fixture {fixture_name}: expected dynamic_multiplier "
        f"{expected['dynamic_multiplier']} "
        f"(derivation: {fixture['derivation']}), got {dynamic_multiplier}"
    )
    assert dynamic_min_stop == pytest.approx(
        expected["dynamic_min_stop"], rel=APPROX_REL, abs=APPROX_ABS
    ), (
        f"Fixture {fixture_name}: expected dynamic_min_stop "
        f"{expected['dynamic_min_stop']} "
        f"(derivation: {fixture['derivation']}), got {dynamic_min_stop}"
    )


# --- Property: boundary contracts pin to named constants -------------------


def test_at_time_ratio_zero_returns_open_constants() -> None:
    """
    Invariant: compute_time_squeeze_decay(0.0) == (MULT_OPEN, MIN_STOP_OPEN).
    Pulls the named constants directly from math_engine to confirm the
    function uses THE SAME constants the module declares -- catches an impl
    that hardcoded the float literals locally instead of consuming the
    module-level constants.
    """
    mult, min_stop = math_engine.compute_time_squeeze_decay(time_ratio=0.0)
    assert mult == pytest.approx(
        math_engine.MULT_OPEN, rel=APPROX_REL, abs=APPROX_ABS
    ), (
        f"At time_ratio=0, dynamic_multiplier should equal math_engine.MULT_OPEN "
        f"({math_engine.MULT_OPEN}), got {mult}"
    )
    assert min_stop == pytest.approx(
        math_engine.MIN_STOP_OPEN, rel=APPROX_REL, abs=APPROX_ABS
    ), (
        f"At time_ratio=0, dynamic_min_stop should equal math_engine.MIN_STOP_OPEN "
        f"({math_engine.MIN_STOP_OPEN}), got {min_stop}"
    )


def test_at_time_ratio_one_returns_close_constants() -> None:
    """
    Invariant: compute_time_squeeze_decay(1.0) == (MULT_CLOSE, MIN_STOP_CLOSE).
    Mirror of the open-boundary test; catches a flipped linear-interp
    direction.
    """
    mult, min_stop = math_engine.compute_time_squeeze_decay(time_ratio=1.0)
    assert mult == pytest.approx(
        math_engine.MULT_CLOSE, rel=APPROX_REL, abs=APPROX_ABS
    ), (
        f"At time_ratio=1, dynamic_multiplier should equal math_engine.MULT_CLOSE "
        f"({math_engine.MULT_CLOSE}), got {mult}"
    )
    assert min_stop == pytest.approx(
        math_engine.MIN_STOP_CLOSE, rel=APPROX_REL, abs=APPROX_ABS
    ), (
        f"At time_ratio=1, dynamic_min_stop should equal math_engine.MIN_STOP_CLOSE "
        f"({math_engine.MIN_STOP_CLOSE}), got {min_stop}"
    )


# --- Property: monotonicity in time_ratio ----------------------------------


# A dense sweep across [0, 1] covering all 4 quartiles plus a handful of
# off-grid points. Sorted ascending; the test asserts non-increasing
# outputs.
_MONO_SWEEP = [
    0.0,
    0.001,
    0.05,
    0.1,
    0.15,
    0.2,
    0.25,
    0.3,
    0.4,
    0.5,
    0.6,
    0.7,
    0.75,
    0.8,
    0.85,
    0.9,
    0.95,
    0.99,
    1.0,
]


def test_dynamic_multiplier_is_monotonically_non_increasing() -> None:
    """
    Invariant: as time_ratio increases over [0, 1], dynamic_multiplier
    monotonically NON-INCREASES (it decays from 1.5 to 0.5). decay_curve is
    monotonically non-decreasing in time_ratio (log10 is monotone increasing
    on positive args), and dynamic_multiplier = MULT_OPEN - (positive coef) *
    decay_curve, so dynamic_multiplier is monotonically non-increasing.

    A wrong impl that flipped the sign of the coefficient, or used a
    non-monotone function (e.g., sin), would break this.
    """
    prev_mult = math.inf
    for tr in _MONO_SWEEP:
        mult, _ = math_engine.compute_time_squeeze_decay(time_ratio=tr)
        assert mult <= prev_mult + APPROX_ABS, (
            f"dynamic_multiplier non-monotone at time_ratio={tr}: "
            f"got {mult}, previous was {prev_mult}"
        )
        prev_mult = mult


def test_dynamic_min_stop_is_monotonically_non_increasing() -> None:
    """
    Invariant: as time_ratio increases over [0, 1], dynamic_min_stop
    monotonically NON-INCREASES (it decays from 0.3 to 0.15). Same argument
    as dynamic_multiplier: monotone log10 * positive coef = monotone outputs.
    """
    prev_min_stop = math.inf
    for tr in _MONO_SWEEP:
        _, min_stop = math_engine.compute_time_squeeze_decay(time_ratio=tr)
        assert min_stop <= prev_min_stop + APPROX_ABS, (
            f"dynamic_min_stop non-monotone at time_ratio={tr}: "
            f"got {min_stop}, previous was {prev_min_stop}"
        )
        prev_min_stop = min_stop


# --- Property: outputs stay within their declared end-point ranges ---------


@pytest.mark.parametrize("time_ratio", _MONO_SWEEP)
def test_dynamic_multiplier_stays_within_open_close_range(time_ratio: float) -> None:
    """
    Invariant: for any time_ratio in [0, 1], MULT_CLOSE <= dynamic_multiplier
    <= MULT_OPEN. The linear interpolation between the two end-points can
    never escape the closed interval as long as decay_curve in [0, 1] (which
    it is, since log10(1) = 0 and log10(10) = 1 are the boundary values).

    Catches an impl that flipped the linear-interp direction (would yield
    values OUTSIDE [MULT_CLOSE, MULT_OPEN] for any interior time_ratio).
    """
    mult, _ = math_engine.compute_time_squeeze_decay(time_ratio=time_ratio)
    assert math_engine.MULT_CLOSE - APPROX_ABS <= mult <= math_engine.MULT_OPEN + APPROX_ABS, (
        f"dynamic_multiplier escaped [MULT_CLOSE, MULT_OPEN] = "
        f"[{math_engine.MULT_CLOSE}, {math_engine.MULT_OPEN}] "
        f"at time_ratio={time_ratio}: got {mult}"
    )


@pytest.mark.parametrize("time_ratio", _MONO_SWEEP)
def test_dynamic_min_stop_stays_within_open_close_range(time_ratio: float) -> None:
    """
    Invariant: for any time_ratio in [0, 1], MIN_STOP_CLOSE <=
    dynamic_min_stop <= MIN_STOP_OPEN. Same argument as dynamic_multiplier.
    """
    _, min_stop = math_engine.compute_time_squeeze_decay(time_ratio=time_ratio)
    assert (
        math_engine.MIN_STOP_CLOSE - APPROX_ABS
        <= min_stop
        <= math_engine.MIN_STOP_OPEN + APPROX_ABS
    ), (
        f"dynamic_min_stop escaped [MIN_STOP_CLOSE, MIN_STOP_OPEN] = "
        f"[{math_engine.MIN_STOP_CLOSE}, {math_engine.MIN_STOP_OPEN}] "
        f"at time_ratio={time_ratio}: got {min_stop}"
    )


# --- Property: determinism + purity ----------------------------------------


def test_function_is_pure_repeat_call_returns_identical_result() -> None:
    """
    Sanity: a pure function returns identical results when called twice with
    the same arguments. Catches an impl that accidentally stashes state in a
    module-level variable or has a hidden time-dependence (e.g., calling
    datetime.now() inside the math layer).
    """
    tr = 0.42
    m1, s1 = math_engine.compute_time_squeeze_decay(time_ratio=tr)
    m2, s2 = math_engine.compute_time_squeeze_decay(time_ratio=tr)
    # Exact equality: a pure deterministic function MUST produce bit-identical
    # outputs, not approx-equal outputs.
    assert m1 == m2, f"Non-deterministic dynamic_multiplier: {m1} vs {m2}"
    assert s1 == s2, f"Non-deterministic dynamic_min_stop: {s1} vs {s2}"


def test_function_does_not_mutate_inputs() -> None:
    """
    Pure-function contract: must not mutate its input. The input here is a
    Python float (immutable), so this is documentary -- but it pins the
    intent so a future signature change (e.g., accepting a dict for richer
    time-state) doesn't silently introduce mutation.
    """
    time_ratio = 0.42
    math_engine.compute_time_squeeze_decay(time_ratio=time_ratio)
    assert time_ratio == 0.42


# --- Property: return type contract ----------------------------------------


def test_return_types_are_python_floats() -> None:
    """
    Contract: the function returns (float, float) -- Python floats, not numpy
    scalars. Downstream callers persist these values to SQLite and emit them
    in Discord embeds; numpy scalars serialize inconsistently across
    sqlite3 + json + Discord (sqlite3 raises 'Error binding parameter'
    in some Python versions when given np.float64). Same defensive pattern
    as the parabolic-arming cycle.

    isinstance(True, int) is True in Python, but bool is not a concern here
    since we're returning math outputs not predicates -- the float check is
    sufficient to exclude np.float64 (which is NOT a subclass of float).
    """
    mult, min_stop = math_engine.compute_time_squeeze_decay(time_ratio=0.5)
    assert isinstance(mult, float), (
        f"dynamic_multiplier must be a Python float, got {type(mult).__name__}"
    )
    assert isinstance(min_stop, float), (
        f"dynamic_min_stop must be a Python float, got {type(min_stop).__name__}"
    )
    # Numpy scalars are NOT acceptable. We explicitly reject np.float64
    # (it would pass isinstance(..., float) on some platforms because
    # np.float64 IS a subclass of Python float -- but it's not the same as
    # plain float for sqlite3/json purposes). Use the exact type check.
    assert type(mult) is float, (
        f"dynamic_multiplier must be EXACTLY float, got {type(mult).__name__} "
        f"(numpy scalars are forbidden; downstream sqlite3/json/Discord "
        f"serialization breaks on np.float64)"
    )
    assert type(min_stop) is float, (
        f"dynamic_min_stop must be EXACTLY float, got {type(min_stop).__name__} "
        f"(numpy scalars are forbidden; downstream sqlite3/json/Discord "
        f"serialization breaks on np.float64)"
    )


# --- Constant / magic-number provenance scan --------------------------------


def test_no_unnamed_magic_numbers_in_time_squeeze_decay_path() -> None:
    """
    Project rule: 'No magic numbers in math_engine.py -- every constant named
    + source comment.'

    Scans the AST of compute_time_squeeze_decay for numeric literals. Each
    literal must either be:
      (a) a 'trivially structural' value (0, 1, -1, 0.0, 1.0 -- explicitly
          whitelisted with a documented reason below), or
      (b) accompanied by a named-constant assignment or an explanatory
          source comment on the same line.

    For THIS function the 4 boundary constants (1.5, 0.5, 0.3, 0.15) and the
    inner-log10 scalar (9) must be named module-level constants. This test
    SHOULD FAIL in RED because (a) the function doesn't exist yet and (b)
    when the GREEN impl is naive (i.e., copy-pastes the inline literals
    instead of extracting named constants), this scanner will reject it.
    """
    src_path = pathlib.Path(math_engine.__file__)
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    src_lines = source.splitlines()

    # Trivially-structural literals that don't carry domain meaning in this
    # specific function's body. Each entry MUST have a comment justifying
    # why it is not a magic number.
    STRUCTURAL = {
        0,    # array index / increment (likely unused here, but harmless)
        1,    # the additive '1' inside log10(1 + ...); this is a universal
              # mathematical constant for the log10 shift, NOT a tunable
              # parameter -- pinning it would be ceremony for ceremony's sake
        -1,   # last-element index (unlikely but harmless)
        0.0,  # universal zero -- caller-clamp floor; mathematical constant
        1.0,  # universal one -- caller-clamp ceiling; mathematical constant
    }

    # Find the FunctionDef node for compute_time_squeeze_decay.
    target: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "compute_time_squeeze_decay":
            target = node
            break
    assert target is not None, (
        "compute_time_squeeze_decay not found in math_engine.py "
        "(this is expected in RED; the function will be created in GREEN)"
    )

    # Collect lines where ANY ast.Name is assigned a Constant -- those count
    # as 'named'. Module-level constants (consumed by Name in the function
    # body) are also acceptable since referring to MULT_OPEN by name in the
    # function body produces an ast.Name node, not an ast.Constant.
    named_literal_lines: set[int] = set()
    for sub in ast.walk(target):
        if isinstance(sub, ast.Assign):
            for tgt in sub.targets:
                if isinstance(tgt, ast.Name) and isinstance(sub.value, ast.Constant):
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
        if isinstance(sub, ast.Constant) and isinstance(sub.value, int | float):
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
        "Unnamed magic numbers in compute_time_squeeze_decay (project rule: "
        "every constant in math_engine.py must be named + source-commented). "
        f"Offenders (line, value): {offenders}. "
        "Fix in the GREEN phase by extracting module-level named constants "
        "(e.g., DECAY_CURVE_SCALAR = 9, MULT_OPEN = 1.5, MULT_CLOSE = 0.5, "
        "MIN_STOP_OPEN = 0.3, MIN_STOP_CLOSE = 0.15) and referencing them by "
        "name in the function body."
    )


def test_time_squeeze_decay_constants_are_module_level_with_canonical_values() -> None:
    """
    Sanity guard for the scanner above (analogous to the vol-scaling +
    ATR cycles): confirms the domain constants for the time-squeeze-decay
    path are present as MODULE-LEVEL named constants with their canonical
    values. Cross-checks that the magic-number scanner is not passing for
    the wrong reason (e.g., the implementer renamed the constants to
    something else and the AST-walk happened to skip).

    GREEN-phase contract: the 5 constants must exist on the math_engine
    module with their values. Renames are allowed only if the test is
    updated in lockstep.
    """
    # Inner-log10 scalar: log10(1 + DECAY_CURVE_SCALAR * time_ratio).
    # Choosing 9 makes decay_curve land exactly on 1.0 at time_ratio=1.0
    # (because log10(10) = 1), which is the only choice that makes the
    # linear-interp end-points coincide with the boundary values.
    assert hasattr(math_engine, "DECAY_CURVE_SCALAR"), (
        "math_engine.DECAY_CURVE_SCALAR not found -- the inner-log10 "
        "scalar must be a named module-level constant."
    )
    assert math_engine.DECAY_CURVE_SCALAR == 9, (
        f"DECAY_CURVE_SCALAR should be 9 (so log10(1 + 9*1) = log10(10) = 1 "
        f"at market close), got {math_engine.DECAY_CURVE_SCALAR}"
    )

    # Vol multiplier end-points (at-open: 1.5x, at-close: 0.5x).
    assert hasattr(math_engine, "MULT_OPEN"), (
        "math_engine.MULT_OPEN not found -- the at-open vol multiplier "
        "must be a named module-level constant."
    )
    assert math_engine.MULT_OPEN == 1.5, (
        f"MULT_OPEN should be 1.5 (widest stop at market open), "
        f"got {math_engine.MULT_OPEN}"
    )
    assert hasattr(math_engine, "MULT_CLOSE"), (
        "math_engine.MULT_CLOSE not found -- the at-close vol multiplier "
        "must be a named module-level constant."
    )
    assert math_engine.MULT_CLOSE == 0.5, (
        f"MULT_CLOSE should be 0.5 (tightest stop at market close), "
        f"got {math_engine.MULT_CLOSE}"
    )

    # Min-stop floor end-points (at-open: 0.30%, at-close: 0.15%).
    assert hasattr(math_engine, "MIN_STOP_OPEN"), (
        "math_engine.MIN_STOP_OPEN not found -- the at-open minimum stop "
        "floor must be a named module-level constant."
    )
    assert math_engine.MIN_STOP_OPEN == 0.3, (
        f"MIN_STOP_OPEN should be 0.3 (0.30% min stop at market open), "
        f"got {math_engine.MIN_STOP_OPEN}"
    )
    assert hasattr(math_engine, "MIN_STOP_CLOSE"), (
        "math_engine.MIN_STOP_CLOSE not found -- the at-close minimum stop "
        "floor must be a named module-level constant."
    )
    assert math_engine.MIN_STOP_CLOSE == 0.15, (
        f"MIN_STOP_CLOSE should be 0.15 (0.15% min stop at market close), "
        f"got {math_engine.MIN_STOP_CLOSE}"
    )
