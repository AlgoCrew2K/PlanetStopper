"""
Golden-fixture + property tests for the active-trailing-stop layer of
math_engine.py.

Scope (Gate-1 approved): NEW pure function
math_engine.compute_active_trailing_stop, extracted from
alpha_bot_execution.py:581-588. This is a refactor-extraction cycle (cycle 5
of 7), not a new feature; the inline producer (those 8 source lines on
current main) is the spec the GREEN phase must preserve.

Inline producer pinned verbatim (alpha_bot_execution.py:581-588 on main, post
cycle-4 merge):

    # Calculate active stop distance based strictly on 20-day volatility

    safe_vol = symphony_vol if symphony_vol > 0 else 1.0
    active_trailing_stop = max((safe_vol * dynamic_multiplier), dynamic_min_stop)

    # Apply Parabolic Squeeze multiplier if armed
    if bot_state[symphony_id].get("para_armed") or bot_state[symphony_id].get("breakeven_locked"):
        active_trailing_stop *= acc_params.get("MAX_PARABOLIC_SQUEEZE", MAX_PARABOLIC_SQUEEZE)

PROPOSED PURE INTERFACE (confirmed -- no revision; the gate-2 proposal isolates
the smallest non-trivial unit: a scalar computation with two-branch fallback
and one conditional multiplier. The caller continues to own bot_state lookups,
acc_params resolution, and the MAX_PARABOLIC_SQUEEZE module-level default --
the math layer must NOT import alpha_bot_execution constants; that is an
explicit architectural boundary):

    def compute_active_trailing_stop(
        symphony_vol: float,
        dynamic_multiplier: float,
        dynamic_min_stop: float,
        para_armed: bool,
        breakeven_locked: bool,
        parabolic_squeeze_multiplier: float,
    ) -> float:
        '''
        safe_vol = symphony_vol if symphony_vol > 0 else VOL_FALLBACK
        active = max(safe_vol * dynamic_multiplier, dynamic_min_stop)
        if para_armed or breakeven_locked:
            active *= parabolic_squeeze_multiplier
        return active
        '''

The function is PURE: no I/O, no state. Caller normalizes None/missing
bot_state values to strict Python bool BEFORE passing para_armed /
breakeven_locked. Caller also resolves the squeeze multiplier (acc_params.get
+ module-level fallback) before passing.

CONTRACT - caller-normalized inputs:
The pure math layer TRUSTS the caller to pass:
  - symphony_vol: a real-valued float (may be <= 0; the function handles
    that case via the VOL_FALLBACK branch).
  - dynamic_multiplier, dynamic_min_stop: real-valued floats (the function
    does not validate sign or range).
  - para_armed, breakeven_locked: strict Python bool. The function uses
    Python's `or` short-circuit, so truthy non-bools (e.g., int 1) work as a
    stress case, but the caller's contract is bool -- we test int as
    documentary stress, not as a supported regime.
  - parabolic_squeeze_multiplier: a real-valued float (the function does not
    validate sign, range, or non-zero-ness).

Tolerance policy:
- pytest.approx(rel=1e-9, abs=1e-12). Most fixtures are EXACT in IEEE-754
  (the arithmetic is multiplication + max + conditional multiplication, no
  transcendental ops). A handful of fixtures with 0.2 / 0.4 inputs pick up
  ~1 ulp of binary-representation drift; rel=1e-9 absorbs this comfortably
  while still catching any algorithmic divergence.

Provenance (HARD): every expected float in
tests/fixtures/math_engine/active_trailing_stop/*.json is DERIVED BY HAND
from the inline producer's formula and pinned in the fixture's 'derivation'
field -- NOT captured from a current implementation
(compute_active_trailing_stop does not exist yet; this is RED). The math is
straightforward (multiplication, max, conditional multiplication) and the
derivations are spelled out per-fixture.

Adversarial fixture intent (each fixture targets a SPECIFIC class of wrong
impl):
- Fixture 01 (vol-floor wins, no squeeze): catches an impl that omitted the
  max() clamp (would return the lower product instead of the floor).
- Fixture 02 (vol-scale wins, no squeeze): catches an impl that returned the
  floor unconditionally.
- Fixture 03 (exact tie, no squeeze): boundary sanity at one operating point.
- Fixture 04 (symphony_vol=0): catches an impl that omitted the VOL_FALLBACK
  branch (would produce 0 stop distance, fatal for live trading).
- Fixture 05 (symphony_vol<0): catches an impl that used >= 0 (negatives
  would slip through and produce a NEGATIVE stop product, fatal).
- Fixture 06 (symphony_vol tiny positive): catches an impl that clamped all
  small positives to VOL_FALLBACK (would over-scale by orders of magnitude).
- Fixtures 07/08/09 (squeeze OR semantics + idempotence): catch a double-
  multiplication bug or an AND condition typo.
- Fixture 10 (no flags, non-identity squeeze supplied): catches an impl
  that applied the squeeze unconditionally.
- Fixture 11 (squeeze=1.0 identity): pins that the squeeze code path runs
  but produces no effective change; useful baseline for the property test.
- Fixture 12 (squeeze=0.0): pins that no implicit floor is reapplied AFTER
  the squeeze -- a behavior-preserving constraint vs the inline producer.
- Fixture 13 (vol-scale equals min-stop at a SECOND operating point):
  triangulates the boundary condition.
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
    / "active_trailing_stop"
)

# --- Tolerance --------------------------------------------------------------

# rel=1e-9 catches any algorithmic divergence (omitted max(), flipped OR/AND,
# missing fallback, double-multiplication of squeeze). abs=1e-12 lets exact-
# zero expecteds (fixture 12) match cleanly. A wrong impl would miss by orders
# of magnitude or by a structural factor (2x, 10x), not by ~1 ulp.
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
def test_active_trailing_stop_matches_derived_expected(
    fixture_name: str, fixture: dict[str, Any]
) -> None:
    """
    Every fixture's expected stop-distance is DERIVED BY HAND in the
    fixture's 'derivation' field. This test asserts
    compute_active_trailing_stop produces that value. Function does not
    exist yet -- this is RED.
    """
    func_name = fixture["function"]
    assert func_name == "compute_active_trailing_stop", (
        f"{fixture_name}: only compute_active_trailing_stop is in scope for "
        f"this cycle"
    )

    inputs = fixture["inputs"]
    expected = fixture["expected"]

    actual = math_engine.compute_active_trailing_stop(
        symphony_vol=inputs["symphony_vol"],
        dynamic_multiplier=inputs["dynamic_multiplier"],
        dynamic_min_stop=inputs["dynamic_min_stop"],
        para_armed=inputs["para_armed"],
        breakeven_locked=inputs["breakeven_locked"],
        parabolic_squeeze_multiplier=inputs["parabolic_squeeze_multiplier"],
    )

    assert actual == pytest.approx(expected, rel=APPROX_REL, abs=APPROX_ABS), (
        f"Fixture {fixture_name}: expected {expected} "
        f"(derivation: {fixture['derivation']}), got {actual}"
    )


# --- Property: when both flags are False, squeeze multiplier is ignored ----


@pytest.mark.parametrize(
    "squeeze_mult",
    # Only strictly-positive multipliers: AC-5 (audit M-2) rejects
    # parabolic_squeeze_multiplier <= 0 with a ValueError at function entry,
    # UNCONDITIONALLY — even when both flags are False and the squeeze branch
    # would not fire. The <= 0 rejection (including the no-flags case) is
    # pinned in tests/math_engine/test_squeeze_multiplier_rejection.py.
    [0.25, 0.5, 1.0, 2.0, 1000.0],
)
def test_no_flags_set_squeeze_multiplier_is_ignored(squeeze_mult: float) -> None:
    """
    Invariant: when para_armed=False AND breakeven_locked=False, the output
    is EXACTLY max(safe_vol * dynamic_multiplier, dynamic_min_stop) for any
    in-range (strictly positive) parabolic_squeeze_multiplier — the squeeze
    branch does not fire so the multiplier value has no effect on the output.

    Catches an impl that applied the squeeze unconditionally, or that
    flipped OR -> AND with NOT.

    Note: non-positive multipliers are NOT exercised here — AC-5 rejects
    them at entry regardless of the flags (see test_squeeze_multiplier_
    rejection.py). "Ignored" applies only to the valid (0, inf) domain.
    """
    sym_vol = 2.0
    mult = 1.5
    min_stop = 0.30
    expected = max(sym_vol * mult, min_stop)  # no squeeze applied

    actual = math_engine.compute_active_trailing_stop(
        symphony_vol=sym_vol,
        dynamic_multiplier=mult,
        dynamic_min_stop=min_stop,
        para_armed=False,
        breakeven_locked=False,
        parabolic_squeeze_multiplier=squeeze_mult,
    )

    assert actual == pytest.approx(expected, rel=APPROX_REL, abs=APPROX_ABS), (
        f"With both flags False, squeeze_mult={squeeze_mult} should NOT affect "
        f"the output. Expected {expected} (= max(safe_vol*mult, min_stop)), "
        f"got {actual}."
    )


# --- Property: when either flag is True, squeeze fires exactly once ---------


@pytest.mark.parametrize(
    "para_armed,breakeven_locked",
    [
        (True, False),
        (False, True),
        (True, True),
    ],
)
def test_either_flag_set_applies_squeeze_exactly_once(
    para_armed: bool, breakeven_locked: bool
) -> None:
    """
    Invariant: when (para_armed OR breakeven_locked) AND
    parabolic_squeeze_multiplier > 0, the output is EXACTLY
    parabolic_squeeze_multiplier * max(safe_vol * dynamic_multiplier,
    dynamic_min_stop).

    Catches an impl that:
      - double-multiplied the squeeze when both flags were True (would yield
        0.25x instead of 0.5x for sq=0.5)
      - used AND instead of OR (would yield the no-squeeze branch for the
        single-flag cases)
      - re-applied the min_stop floor AFTER the squeeze (would clamp small
        outputs and break fixture 12).
    """
    sym_vol = 2.0
    mult = 1.5
    min_stop = 0.30
    sq = 0.5
    base = max(sym_vol * mult, min_stop)
    expected = sq * base  # squeeze fires ONCE

    actual = math_engine.compute_active_trailing_stop(
        symphony_vol=sym_vol,
        dynamic_multiplier=mult,
        dynamic_min_stop=min_stop,
        para_armed=para_armed,
        breakeven_locked=breakeven_locked,
        parabolic_squeeze_multiplier=sq,
    )

    assert actual == pytest.approx(expected, rel=APPROX_REL, abs=APPROX_ABS), (
        f"With para_armed={para_armed}, breakeven_locked={breakeven_locked}, "
        f"sq={sq}: expected {expected} (= sq * max(safe_vol*mult, min_stop), "
        f"squeeze fires ONCE), got {actual}."
    )


# --- Property: non-positive symphony_vol all collapse to VOL_FALLBACK ------


@pytest.mark.parametrize(
    "sym_vol",
    [0.0, -0.0, -1e-12, -0.001, -0.5, -1.0, -1e6],
)
def test_non_positive_symphony_vol_uses_fallback(sym_vol: float) -> None:
    """
    Invariant: for ANY symphony_vol <= 0, the output equals the case where
    symphony_vol=VOL_FALLBACK (with all other inputs identical), provided
    para_armed and breakeven_locked are both False (to isolate the fallback
    branch from the squeeze branch).

    The producer condition is `symphony_vol if symphony_vol > 0 else 1.0`
    (strict `>`), so zero AND negatives both fall through to the fallback.
    The named constant VOL_FALLBACK = 1.0 captures the 1.0 literal.

    Catches an impl that used `>= 0` (would let zero through and produce a
    zero-product stop distance) or that used a different fallback value.
    """
    mult = 1.5
    min_stop = 0.30
    reference = math_engine.compute_active_trailing_stop(
        symphony_vol=math_engine.VOL_FALLBACK,
        dynamic_multiplier=mult,
        dynamic_min_stop=min_stop,
        para_armed=False,
        breakeven_locked=False,
        parabolic_squeeze_multiplier=0.5,
    )

    actual = math_engine.compute_active_trailing_stop(
        symphony_vol=sym_vol,
        dynamic_multiplier=mult,
        dynamic_min_stop=min_stop,
        para_armed=False,
        breakeven_locked=False,
        parabolic_squeeze_multiplier=0.5,
    )

    assert actual == pytest.approx(reference, rel=APPROX_REL, abs=APPROX_ABS), (
        f"symphony_vol={sym_vol} (non-positive) should produce the same "
        f"output as symphony_vol=VOL_FALLBACK. Got {actual} vs reference "
        f"{reference}."
    )


# --- Property: OR is symmetric (idempotence of either-flag-True) -----------


def test_or_symmetry_para_only_equals_breakeven_only() -> None:
    """
    Invariant: at fixed other params, para_armed=True / breakeven_locked=False
    produces the SAME output as para_armed=False / breakeven_locked=True. The
    OR fires identically; downstream multiplication is identical.

    Catches an impl that special-cased one flag (e.g., only multiplied if
    para_armed, ignoring breakeven_locked entirely).
    """
    sym_vol = 2.0
    mult = 1.5
    min_stop = 0.30
    sq = 0.5

    out_para_only = math_engine.compute_active_trailing_stop(
        symphony_vol=sym_vol,
        dynamic_multiplier=mult,
        dynamic_min_stop=min_stop,
        para_armed=True,
        breakeven_locked=False,
        parabolic_squeeze_multiplier=sq,
    )
    out_breakeven_only = math_engine.compute_active_trailing_stop(
        symphony_vol=sym_vol,
        dynamic_multiplier=mult,
        dynamic_min_stop=min_stop,
        para_armed=False,
        breakeven_locked=True,
        parabolic_squeeze_multiplier=sq,
    )

    # Exact equality: identical inputs through identical code paths must
    # produce bit-identical outputs.
    assert out_para_only == out_breakeven_only, (
        f"OR symmetry broken: para_only={out_para_only}, "
        f"breakeven_only={out_breakeven_only}. Both flags should be "
        f"interchangeable when the other is False."
    )


# --- Property: determinism + purity ----------------------------------------


def test_function_is_pure_repeat_call_returns_identical_result() -> None:
    """
    Sanity: a pure function returns identical results when called twice with
    the same arguments. Catches an impl that accidentally stashes state in a
    module-level variable or has a hidden time-dependence.
    """
    args = {
        "symphony_vol": 1.7,
        "dynamic_multiplier": 1.2,
        "dynamic_min_stop": 0.25,
        "para_armed": True,
        "breakeven_locked": False,
        "parabolic_squeeze_multiplier": 0.42,
    }
    a = math_engine.compute_active_trailing_stop(**args)
    b = math_engine.compute_active_trailing_stop(**args)
    # Exact equality: a pure deterministic function MUST produce bit-
    # identical outputs, not approx-equal outputs.
    assert a == b, f"Non-deterministic output: {a} vs {b}"


# --- Property: return type contract ----------------------------------------


def test_return_type_is_python_float() -> None:
    """
    Contract: the function returns a Python float -- not a numpy scalar,
    not an int. Downstream callers persist this value to SQLite and emit it
    in Discord embeds; numpy scalars serialize inconsistently across
    sqlite3 + json + Discord (sqlite3 raises 'Error binding parameter' on
    np.float64 in some Python versions). Same defensive pattern as the
    time-squeeze-decay cycle.
    """
    out = math_engine.compute_active_trailing_stop(
        symphony_vol=1.5,
        dynamic_multiplier=1.0,
        dynamic_min_stop=0.3,
        para_armed=False,
        breakeven_locked=False,
        parabolic_squeeze_multiplier=0.5,
    )
    assert isinstance(out, float), (
        f"Output must be a Python float, got {type(out).__name__}"
    )
    # Strict type check: numpy scalars are NOT acceptable.
    assert type(out) is float, (
        f"Output must be EXACTLY float, got {type(out).__name__} "
        f"(numpy scalars are forbidden; downstream sqlite3/json/Discord "
        f"serialization breaks on np.float64)"
    )


# --- Property: int-as-bool stress (caller-contract is bool, but truthy ints
#               must still work because Python's `or` is truthiness-based) --


def test_int_in_place_of_bool_still_works_as_documentary_stress() -> None:
    """
    Caller's contract: pass strict Python bool. We TEST int-in-place-of-bool
    only as a stress case to document that the function's `or` short-circuit
    does not crash on truthy ints (1) or falsy ints (0). This is NOT a
    supported public regime -- it's a guard against a refactor that
    accidentally introduced an `is True` check (which would break for `1`).

    If the GREEN impl uses `if para_armed or breakeven_locked:` (Python
    truthiness), this test passes. If it uses `if para_armed is True or
    breakeven_locked is True:` (identity check), this test fails -- and
    that would be a behavior change vs the inline producer.
    """
    sym_vol = 2.0
    mult = 1.5
    min_stop = 0.30
    sq = 0.5

    # Truthy ints should fire the squeeze (same as True).
    out_int_true = math_engine.compute_active_trailing_stop(
        symphony_vol=sym_vol,
        dynamic_multiplier=mult,
        dynamic_min_stop=min_stop,
        para_armed=1,  # type: ignore[arg-type]
        breakeven_locked=0,  # type: ignore[arg-type]
        parabolic_squeeze_multiplier=sq,
    )
    out_bool_true = math_engine.compute_active_trailing_stop(
        symphony_vol=sym_vol,
        dynamic_multiplier=mult,
        dynamic_min_stop=min_stop,
        para_armed=True,
        breakeven_locked=False,
        parabolic_squeeze_multiplier=sq,
    )
    assert out_int_true == pytest.approx(
        out_bool_true, rel=APPROX_REL, abs=APPROX_ABS
    ), (
        f"int(1) should be truthy-equivalent to True (Python `or` semantics). "
        f"Got int-form {out_int_true} vs bool-form {out_bool_true}."
    )

    # Falsy ints should NOT fire the squeeze (same as False).
    out_int_false = math_engine.compute_active_trailing_stop(
        symphony_vol=sym_vol,
        dynamic_multiplier=mult,
        dynamic_min_stop=min_stop,
        para_armed=0,  # type: ignore[arg-type]
        breakeven_locked=0,  # type: ignore[arg-type]
        parabolic_squeeze_multiplier=sq,
    )
    out_bool_false = math_engine.compute_active_trailing_stop(
        symphony_vol=sym_vol,
        dynamic_multiplier=mult,
        dynamic_min_stop=min_stop,
        para_armed=False,
        breakeven_locked=False,
        parabolic_squeeze_multiplier=sq,
    )
    assert out_int_false == pytest.approx(
        out_bool_false, rel=APPROX_REL, abs=APPROX_ABS
    ), (
        f"int(0) should be falsy-equivalent to False. "
        f"Got int-form {out_int_false} vs bool-form {out_bool_false}."
    )


# --- Constant: VOL_FALLBACK is a module-level named constant ----------------


def test_vol_fallback_is_module_level_named_constant() -> None:
    """
    Project rule: 'No magic numbers in math_engine.py -- every constant
    named + source comment.'

    The fallback value 1.0 (when symphony_vol <= 0) MUST be lifted into a
    module-level constant named VOL_FALLBACK, with a source comment
    explaining the choice. The function body must NOT contain a bare 1.0
    literal as the fallback assignment.

    GREEN-phase contract: VOL_FALLBACK must exist on math_engine with
    value 1.0.
    """
    assert hasattr(math_engine, "VOL_FALLBACK"), (
        "math_engine.VOL_FALLBACK not found -- the fallback value for "
        "non-positive symphony_vol must be a named module-level constant."
    )
    assert math_engine.VOL_FALLBACK == 1.0, (
        f"VOL_FALLBACK should be 1.0 (neutral fallback so safe_vol * "
        f"dynamic_multiplier still produces a reasonable stop in the "
        f"degenerate-vol case), got {math_engine.VOL_FALLBACK}"
    )


# --- Constant-provenance scanner -------------------------------------------


def test_no_unnamed_magic_numbers_in_active_trailing_stop_path() -> None:
    """
    Project rule: 'No magic numbers in math_engine.py -- every constant
    named + source comment.'

    Scans the AST of compute_active_trailing_stop for numeric literals.
    Each literal must either be:
      (a) a 'trivially structural' value (0, 1, -1 -- explicitly whitelisted
          with a documented reason below; note that Python set hashing
          treats 0 and 0.0 as the same key, so the int forms cover the
          float forms automatically), or
      (b) accompanied by a named-constant assignment or an explanatory
          source comment on the same line.

    Specifically for THIS function: VOL_FALLBACK = 1.0 must be a module-
    level named constant; the ASSIGNMENT of fallback value inside the
    function body must reference VOL_FALLBACK by Name, NOT as a bare 1.0
    literal. (The comparison `symphony_vol > 0` is fine since 0 is in the
    structural whitelist; `> 0.0` would also be fine.)

    The function does not exist yet (RED), and a naive GREEN impl that
    copy-pasted `else 1.0` from the inline producer would fail this
    scanner.
    """
    src_path = pathlib.Path(math_engine.__file__)
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    src_lines = source.splitlines()

    # Trivially-structural literals that don't carry domain meaning in this
    # specific function's body. Each entry MUST have a comment justifying
    # why it is not a magic number.
    # int(0) == 0.0 and int(1) == 1.0 in Python's hash protocol, so the set
    # only retains one representative of each value -- listing both forms is
    # cosmetic. We list the canonical forms used in math_engine source.
    STRUCTURAL = {
        0,    # comparison threshold for `symphony_vol > 0`; universal zero
        1,    # likely unused in this function body, harmless if present
        -1,   # unlikely but harmless
    }
    # NOTE on 1.0: 1.0 is the value of VOL_FALLBACK. A bare 1.0 literal
    # INSIDE the function body would slip past this structural whitelist if
    # we added 1.0 to STRUCTURAL -- so we deliberately DO NOT add it here,
    # and the companion test test_vol_fallback_is_named_constant_not_bare_
    # literal_in_function_body walks the function body to catch a bare 1.0
    # specifically.

    target: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "compute_active_trailing_stop"
        ):
            target = node
            break
    assert target is not None, (
        "compute_active_trailing_stop not found in math_engine.py "
        "(this is expected in RED; the function will be created in GREEN)"
    )

    # Collect lines where ANY ast.Name is assigned a Constant -- those
    # count as 'named'. Module-level constants consumed by Name in the
    # function body produce ast.Name nodes (not ast.Constant), so
    # referring to VOL_FALLBACK by name is correctly NOT flagged here.
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
        "Unnamed magic numbers in compute_active_trailing_stop (project "
        "rule: every constant in math_engine.py must be named + source-"
        f"commented). Offenders (line, value): {offenders}. "
        "Fix in the GREEN phase by referencing VOL_FALLBACK (and any other "
        "extracted constants) by name."
    )


def test_vol_fallback_is_named_constant_not_bare_literal_in_function_body() -> None:
    """
    Sharper guard than the structural-whitelist scanner above: walks the
    BODY of compute_active_trailing_stop and asserts that NO ast.Constant
    with value 1.0 (float) appears inside it. The fallback assignment must
    use the module-level VOL_FALLBACK Name reference.

    Why this is needed in addition to the structural scanner: 1.0 is in
    the structural whitelist (because it's also a useful 'universal one'
    for things like identity multipliers in some math functions). But in
    THIS specific function, 1.0 IS the domain constant (the VOL_FALLBACK
    value), and a copy-paste of `else 1.0` from the inline producer would
    slip through the structural whitelist undetected. This test pins the
    project rule for this function.
    """
    src_path = pathlib.Path(math_engine.__file__)
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    target: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "compute_active_trailing_stop"
        ):
            target = node
            break
    assert target is not None, (
        "compute_active_trailing_stop not found in math_engine.py "
        "(expected RED state)"
    )

    bare_ones: list[int] = []
    for sub in ast.walk(target):
        # bool is not a float subclass in Python (bool subclasses int), so
        # the isinstance(..., float) check correctly excludes True/False.
        if (
            isinstance(sub, ast.Constant)
            and isinstance(sub.value, float)
            and sub.value == 1.0
        ):
            bare_ones.append(sub.lineno)

    assert not bare_ones, (
        "Bare 1.0 literal(s) found inside compute_active_trailing_stop "
        f"at line(s) {bare_ones}. The VOL_FALLBACK value MUST be referenced "
        "by name (math_engine.VOL_FALLBACK or VOL_FALLBACK), not inlined as "
        "a literal. Fix in GREEN by using the named constant."
    )
