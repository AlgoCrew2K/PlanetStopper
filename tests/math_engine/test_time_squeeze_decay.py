"""
Golden-fixture + property tests for the time-squeeze decay layer of
math_engine.py, post-M3 re-derivation.

Scope: the M3 re-derivation of math_engine.compute_time_squeeze_decay from the
pre-M3 practitioner heuristic `log10(1 + 9 * t)` to the THEORY-derived
`f(t) = 1 - sqrt(1 - t)` (Danielsson & Zigrand, 2003, LSE FMG DP-439). This is
a substantive behavioral change to a live-exit-logic layer; the change is
authorized by the M3 plan §72 ("Any test pinning the old DECAY_CURVE_SCALAR = 9
curve output is updated to the new curve and the change is called out in the
PR diff -- intentional behavior change, gated by S-1").

WHY THE CHANGE: the pre-M3 heuristic had no formal literature provenance; the
in-code comment self-flagged it for redrive. The derived form is the i.i.d.-
returns square-root-of-time remaining-variance curve: under the standard
square-root-of-time scaling for i.i.d. log-returns with constant per-unit-time
variance, the standard deviation of remaining-session returns scales as
sqrt(1-t); tightness (1 - remaining_std / full_std) is therefore 1 - sqrt(1-t).
Endpoints unchanged: f(0)=0, f(1)=1. The curve is concave, monotone, and
front-loaded -- same DIRECTION as the old heuristic, but with a meaningfully
LOOSER midday (delta ~0.45 in decay-weight at t=0.5 -- the M3 behavioral
budget). Research note: docs/research/m3-provenance/literature-pass.md §1.

PROPOSED PURE INTERFACE (unchanged from pre-M3 -- caller wiring untouched):

    def compute_time_squeeze_decay(time_ratio: float) -> tuple[float, float]:
        '''Returns (dynamic_multiplier, dynamic_min_stop).'''

CONTRACT - caller-clamped input:
The pure math layer TRUSTS the caller to pass time_ratio in [0.0, 1.0]. The
caller (in alpha_bot_execution.py) does max(0.0, min(1.0, ...)) before passing.
The math layer DOES validate (post-M1: reject-don't-coerce per the M-1 policy)
and raises ValueError on time_ratio outside [0, 1].

Tolerance policy:
- pytest.approx(rel=1e-9, abs=1e-12). The boundary fixtures (time_ratio=0 and
  time_ratio=1) are EXACT in IEEE-754 (sqrt(1)=1 and sqrt(0)=0 exactly). The
  midday, early, and late fixtures pick up ~1 ulp of drift from the sqrt step;
  the constructed-clean fixture (t=0.75, decay=0.5 exactly) picks up ~1 ulp
  from the final subtraction (0.3 - 0.075 evaluates to 0.22499999999999998,
  not exactly 0.225 -- same ulp pattern the pre-M3 log10 midpoint documented).
  rel=1e-9 is comfortably loose for these ulp-class errors and tight enough to
  catch any algorithmic divergence (wrong formula, sqrt -> log10 substitution,
  flipped endpoints, missed sign).

Provenance (HARD, D-2 load-bearing): every expected (dynamic_multiplier,
dynamic_min_stop) in tests/fixtures/math_engine/time_squeeze_decay/*.json is
DERIVED BY HAND from the closed-form THEORY formula `f(t) = 1 - sqrt(1 - t)`
and pinned in the fixture's 'derivation' field -- NOT captured from any
compute_time_squeeze_decay implementation. The math (single sqrt + linear
interpolation) is analytic; derivations are spelled out per-fixture. A future
contributor MUST NOT regenerate these fixtures from post-M3 code output --
that turns the gate into a tautology and an automatic D-2 fail.

Adversarial fixture intent (each fixture targets a specific class of wrong impl):
- Fixture 01 (t=0.0): boundary. f(0)=0 under BOTH old log10(1) and new sqrt(1);
  paired with interior fixtures to catch an unchanged-formula impl.
- Fixture 02 (t=1.0): boundary. f(1)=1 under BOTH old log10(10) and new sqrt(0);
  mirror of fixture 01.
- Fixture 03 (t=0.5): LOAD-BEARING DIVERGENCE FIXTURE -- 0.45 decay-weight gap
  between old (0.74) and new (0.29). An unchanged-formula impl diverges here
  by orders of magnitude relative to the rel=1e-9 tolerance.
- Fixture 04 (t=0.1): early-session interior; pre-M3 decay ~0.28, post-M3 ~0.05.
- Fixture 05 (t=0.9): late-session interior; pre-M3 decay ~0.96, post-M3 ~0.68.
- Fixture 06 (t=0.75): NEW algebraic clean point -- decay = 0.5 EXACTLY under
  the new formula (sqrt(0.25) = 0.5 rationally). Pins dynamic_multiplier =
  1.0 EXACTLY at the midpoint of [MULT_CLOSE, MULT_OPEN]. Under the old log10,
  this clean point lived at t ~= 0.2403; the move IS the formula change.
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

# rel=1e-9 catches any algorithmic divergence (wrong formula, sqrt -> log10
# substitution, flipped linear-interp end-points). abs=1e-12 lets exact-zero /
# exact-equal-MULT_OPEN expecteds match cleanly. A wrong impl would miss by
# orders of magnitude, not by ~1 ulp.
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
    BY HAND in the fixture's 'derivation' field from the closed-form
    f(t) = 1 - sqrt(1 - t). This test asserts compute_time_squeeze_decay
    produces that tuple under the post-M3 THEORY-derived formula. Pre-M3,
    this test passed against the log10(1 + 9t) heuristic; post-M3, the same
    fixtures with new derived values must pass against the sqrt formula.
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
    monotonically non-decreasing in time_ratio (sqrt is monotone on positive
    args, so 1 - sqrt(1-t) is monotone increasing in t), and
    dynamic_multiplier = MULT_OPEN - (positive coef) * decay_curve, so
    dynamic_multiplier is monotonically non-increasing.

    A wrong impl that flipped the sign of the coefficient, or used a
    non-monotone function, would break this. The property is framework-
    agnostic: it held under the pre-M3 log10 heuristic and must continue
    holding under the post-M3 sqrt formula.
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
    as dynamic_multiplier: monotone decay_curve * positive coef = monotone
    outputs.
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
    it is, since 1 - sqrt(1) = 0 and 1 - sqrt(0) = 1 are the boundary
    values and 1 - sqrt(1 - t) is monotone increasing).

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
    intent so a future signature change doesn't silently introduce mutation.
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
    sqlite3 + json + Discord. Same defensive pattern as the parabolic-arming
    cycle.
    """
    mult, min_stop = math_engine.compute_time_squeeze_decay(time_ratio=0.5)
    assert isinstance(mult, float), (
        f"dynamic_multiplier must be a Python float, got {type(mult).__name__}"
    )
    assert isinstance(min_stop, float), (
        f"dynamic_min_stop must be a Python float, got {type(min_stop).__name__}"
    )
    # Numpy scalars are NOT acceptable. Use the exact type check.
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


# --- M3 closed-form derivation cross-check ----------------------------------


@pytest.mark.parametrize("time_ratio", _MONO_SWEEP)
def test_decay_curve_matches_closed_form_sqrt_remaining_variance(
    time_ratio: float,
) -> None:
    """
    M3 derivation cross-check: at every sampled time_ratio, the implied
    decay_curve recovered from dynamic_multiplier must equal the closed-form
    1 - sqrt(1 - time_ratio) to within tolerance.

    decay_curve = (MULT_OPEN - dynamic_multiplier) / (MULT_OPEN - MULT_CLOSE)
                = 1 - sqrt(1 - time_ratio)  [under the M3 THEORY derivation]

    This is the load-bearing assertion that the post-M3 formula is in place.
    Under the pre-M3 log10(1 + 9*t) heuristic, the implied decay_curve at
    t=0.5 would be ~0.74 (vs the sqrt-derived ~0.29) -- this test fails by
    orders of magnitude for an unchanged formula.

    Citation: Danielsson & Zigrand (2003), LSE FMG DP-439,
    https://eprints.lse.ac.uk/24827/1/dp439.pdf -- the square-root-of-time
    scaling rule for i.i.d. log-returns with constant per-unit-time
    variance. Tightness (1 - remaining_std / full_std) = 1 - sqrt(1 - t).
    """
    mult, _ = math_engine.compute_time_squeeze_decay(time_ratio=time_ratio)
    implied_decay = (math_engine.MULT_OPEN - mult) / (
        math_engine.MULT_OPEN - math_engine.MULT_CLOSE
    )
    closed_form_decay = 1.0 - math.sqrt(1.0 - time_ratio)
    assert implied_decay == pytest.approx(
        closed_form_decay, rel=APPROX_REL, abs=APPROX_ABS
    ), (
        f"At time_ratio={time_ratio}, implied decay_curve={implied_decay} "
        f"does not match the M3 closed-form 1 - sqrt(1 - t) = {closed_form_decay}. "
        f"This indicates the pre-M3 log10(1 + 9*t) heuristic is still in place. "
        f"Per M3 plan, the formula must be replaced with 1 - sqrt(1 - t) "
        f"(Danielsson & Zigrand 2003)."
    )


def test_decay_curve_diverges_from_pre_m3_log10_heuristic_at_midday() -> None:
    """
    M3 unchanged-formula detector: at t=0.5, the post-M3 dynamic_multiplier
    must be at least 0.4 GREATER than the pre-M3 log10 heuristic would have
    produced. This is the explicit behavioral divergence the M3 spec_bundle
    declares as the intended_direction.

    Pre-M3 (log10(1 + 9*0.5)) -> decay ~0.74 -> dynamic_multiplier ~0.76.
    Post-M3 (1 - sqrt(0.5))   -> decay ~0.29 -> dynamic_multiplier ~1.21.
    Delta ~0.45.

    The bound 0.4 leaves a comfortable margin -- a true post-M3 impl returns
    1.2071 (delta 0.4474); a residual log10 impl returns 0.7596 (delta 0).
    Anything in between is a hybrid that should also fail this test.
    """
    PRE_M3_LOG10_AT_HALF = 0.7596373105057561  # log10 heuristic value at t=0.5
    mult, _ = math_engine.compute_time_squeeze_decay(time_ratio=0.5)
    assert mult - PRE_M3_LOG10_AT_HALF > 0.4, (
        f"At t=0.5, dynamic_multiplier={mult} -- the post-M3 sqrt curve must "
        f"be at least 0.4 looser than the pre-M3 log10 value of "
        f"{PRE_M3_LOG10_AT_HALF}. A small gap indicates the old log10(1 + 9*t) "
        f"heuristic is still in place or a hybrid was introduced."
    )


# --- M-1 reject-don't-coerce: out-of-range inputs ----------------------------


@pytest.mark.parametrize(
    "out_of_range_tr",
    [-0.0001, -0.1, -1.0, 1.0001, 1.1, 2.0],
)
def test_out_of_range_time_ratio_raises(out_of_range_tr: float) -> None:
    """
    M-1 reject-don't-coerce: time_ratio outside [0, 1] raises ValueError.
    Preserved from pre-M3 -- the M3 derivation does not change the input
    contract.
    """
    with pytest.raises(ValueError, match="time_ratio"):
        math_engine.compute_time_squeeze_decay(time_ratio=out_of_range_tr)


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

    Under the M3 re-derivation, the function body uses only:
      - math.sqrt(1.0 - time_ratio)   [1.0 is structural]
      - MULT_OPEN, MULT_CLOSE         [module-level named constants]
      - MIN_STOP_OPEN, MIN_STOP_CLOSE [module-level named constants]
    No new scalar constants are introduced; DECAY_CURVE_SCALAR is REMOVED
    (the sqrt formula has no tunable inner scalar -- it is closed-form).
    """
    src_path = pathlib.Path(math_engine.__file__)
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    src_lines = source.splitlines()

    # Trivially-structural literals that don't carry domain meaning.
    STRUCTURAL = {
        0,    # array index / increment
        1,    # universal one
        -1,   # last-element index
        0.0,  # caller-clamp floor; mathematical zero
        1.0,  # caller-clamp ceiling; the '1' inside sqrt(1 - t); universal one
    }

    target: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "compute_time_squeeze_decay":
            target = node
            break
    assert target is not None, (
        "compute_time_squeeze_decay not found in math_engine.py"
    )

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
        "Unnamed magic numbers in compute_time_squeeze_decay (project rule: "
        "every constant in math_engine.py must be named + source-commented). "
        f"Offenders (line, value): {offenders}. "
        "The post-M3 sqrt formula uses only structural literals and named "
        "constants; a new unnamed scalar would indicate a residual log10 "
        "heuristic or a non-THEORY hybrid."
    )


def test_decay_curve_scalar_constant_is_removed_post_m3() -> None:
    """
    M3 enforces the removal of DECAY_CURVE_SCALAR. Under the closed-form
    f(t) = 1 - sqrt(1 - t), there is NO tunable inner scalar -- the formula
    is THEORY (Danielsson & Zigrand 2003), zero free parameters. Leaving
    DECAY_CURVE_SCALAR as a module-level constant would be a residual magic
    number with no consumer (dead provenance debt).

    Per research-m3 literature pass §1.3: 'DECAY_CURVE_SCALAR removed
    (closed by sqrt-time derivation).'

    This test will FAIL until impl-m3 removes the module-level constant.
    """
    assert not hasattr(math_engine, "DECAY_CURVE_SCALAR"), (
        "math_engine.DECAY_CURVE_SCALAR is still defined post-M3. The M3 "
        "re-derivation (Danielsson & Zigrand 2003 square-root-of-time) has "
        "ZERO free parameters; this constant must be REMOVED to close the "
        "provenance gap. Research note: "
        "docs/research/m3-provenance/literature-pass.md §1.3."
    )


def test_time_squeeze_decay_endpoint_constants_unchanged() -> None:
    """
    M3 does NOT change the multiplier or min-stop endpoint values. Only the
    decay-curve SHAPE changes (log10 -> sqrt). The endpoints (1.5, 0.5, 0.3,
    0.15) are operationally meaningful (widest stop at open, tightest at
    close) and persist as named module-level constants with their canonical
    values. Renames are allowed only if this test is updated in lockstep.
    """
    # Vol multiplier end-points (at-open: 1.5x, at-close: 0.5x).
    assert hasattr(math_engine, "MULT_OPEN"), (
        "math_engine.MULT_OPEN not found -- the at-open vol multiplier "
        "must remain a named module-level constant post-M3."
    )
    assert math_engine.MULT_OPEN == 1.5, (
        f"MULT_OPEN should be 1.5 (widest stop at market open), "
        f"got {math_engine.MULT_OPEN}"
    )
    assert hasattr(math_engine, "MULT_CLOSE"), (
        "math_engine.MULT_CLOSE not found -- the at-close vol multiplier "
        "must remain a named module-level constant post-M3."
    )
    assert math_engine.MULT_CLOSE == 0.5, (
        f"MULT_CLOSE should be 0.5 (tightest stop at market close), "
        f"got {math_engine.MULT_CLOSE}"
    )

    # Min-stop floor end-points (at-open: 0.30%, at-close: 0.15%).
    assert hasattr(math_engine, "MIN_STOP_OPEN"), (
        "math_engine.MIN_STOP_OPEN not found -- the at-open minimum stop "
        "floor must remain a named module-level constant post-M3."
    )
    assert math_engine.MIN_STOP_OPEN == 0.3, (
        f"MIN_STOP_OPEN should be 0.3 (0.30% min stop at market open), "
        f"got {math_engine.MIN_STOP_OPEN}"
    )
    assert hasattr(math_engine, "MIN_STOP_CLOSE"), (
        "math_engine.MIN_STOP_CLOSE not found -- the at-close minimum stop "
        "floor must remain a named module-level constant post-M3."
    )
    assert math_engine.MIN_STOP_CLOSE == 0.15, (
        f"MIN_STOP_CLOSE should be 0.15 (0.15% min stop at market close), "
        f"got {math_engine.MIN_STOP_CLOSE}"
    )


# --- R1 provenance-comment closure -----------------------------------------


def test_r1_provenance_comment_cites_danielsson_zigrand_2003() -> None:
    """
    M3 provenance-comment closure for R1 (the time-squeeze decay curve).
    The in-source comment block at the R1 constants must cite the
    Danielsson-Zigrand 2003 derivation AND remove the pre-M3 self-flag
    'has no formal literature provenance' language. The research-note
    path must be referenced.

    This is a TEXTUAL closure assertion -- it pins the provenance
    documentation, not the runtime behavior. The behavioral assertions
    are the fixture-driven sqrt tests above.
    """
    src_path = pathlib.Path(math_engine.__file__)
    source = src_path.read_text(encoding="utf-8")

    # Extract the R1 comment region: from MULT_OPEN definition backwards
    # to a window covering the comment block that documents the layer.
    mult_open_idx = source.find("MULT_OPEN = 1.5")
    assert mult_open_idx != -1, (
        "MULT_OPEN = 1.5 not found in math_engine.py source"
    )
    # Pull a window of ~40 lines before MULT_OPEN to capture the comment block.
    pre_lines = source[:mult_open_idx].splitlines()
    r1_comment_region = "\n".join(pre_lines[-40:])

    # Positive citation assertions
    assert "Danielsson" in r1_comment_region, (
        "R1 provenance comment must cite Danielsson (2003) for the "
        "square-root-of-time derivation. Comment region scanned:\n"
        f"{r1_comment_region}"
    )
    assert "Zigrand" in r1_comment_region, (
        "R1 provenance comment must cite Zigrand (the second author of "
        "Danielsson & Zigrand 2003). Comment region scanned:\n"
        f"{r1_comment_region}"
    )
    assert "2003" in r1_comment_region, (
        "R1 provenance comment must cite the 2003 publication year of "
        "Danielsson & Zigrand. Comment region scanned:\n"
        f"{r1_comment_region}"
    )
    assert ("sqrt" in r1_comment_region.lower()
            or "square-root" in r1_comment_region.lower()
            or "square root" in r1_comment_region.lower()), (
        "R1 provenance comment must reference the square-root-of-time "
        "scaling in the derivation. Comment region scanned:\n"
        f"{r1_comment_region}"
    )
    assert "docs/research/m3-provenance/literature-pass.md" in r1_comment_region, (
        "R1 provenance comment must reference the research note at "
        "docs/research/m3-provenance/literature-pass.md. Comment region scanned:\n"
        f"{r1_comment_region}"
    )
    assert "THEORY" in r1_comment_region, (
        "R1 provenance comment must declare freeze_discipline = THEORY "
        "(NN1 binding). Comment region scanned:\n"
        f"{r1_comment_region}"
    )

    # Negative assertion: the pre-M3 self-flag string must be REMOVED.
    assert "no formal literature provenance" not in r1_comment_region, (
        "R1 provenance comment still contains the pre-M3 self-flag "
        "'no formal literature provenance' -- M3 closure requires this "
        "string be REMOVED. The whole point of the M3 redrive is to "
        "replace the practitioner-flag with the derivation citation. "
        "Comment region scanned:\n"
        f"{r1_comment_region}"
    )
