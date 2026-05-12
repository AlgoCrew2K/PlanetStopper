"""
Golden-fixture + property tests for the breakeven-update layer of
math_engine.py.

Scope (Gate-1 approved): NEW pure function math_engine.compute_breakeven_update,
extracted from alpha_bot_execution.py:598-613. This is a refactor-extraction
cycle (cycle 6 of 7), not a new feature; the inline producer (those 16 source
lines on current main) is the spec the GREEN phase must preserve.

Inline producer pinned verbatim (alpha_bot_execution.py:598-613 on main, post
cycle-5 merge):

    dynamic_activation = max(0.4, min(3.0, symphony_vol))
    if current_return >= (dynamic_activation - 0.2):
        bot_state[symphony_id]["hwm_hold_ticks"] += 1
    else:
        bot_state[symphony_id]["hwm_hold_ticks"] = 0

    if bot_state[symphony_id]["hwm_hold_ticks"] >= 5:
        bot_state[symphony_id]["breakeven_locked"] = True

    if bot_state[symphony_id]["breakeven_locked"]:
        stop_trigger_level = max(base_stop_level, 0.0)
    else:
        stop_trigger_level = base_stop_level

    if bot_state[symphony_id]["triggered"]:
        stop_trigger_level = -999.0

PROPOSED PURE INTERFACE (confirmed -- the gate-2 proposal binds two
tightly-coupled logical concerns into ONE pure function because they share
inputs and produce a coupled output: (1) breakeven state evolution
[hwm_hold_ticks counter + breakeven_locked latching] and (2) stop trigger
level resolution [base floored at 0.0 when locked, overridden to a sentinel
when triggered]. Caller assigns the returned state values back into bot_state):

    def compute_breakeven_update(
        current_return: float,
        symphony_vol: float,
        base_stop_level: float,
        current_hold_ticks: int,
        currently_breakeven_locked: bool,
        is_triggered: bool,
    ) -> tuple[int, bool, float]:
        '''
        activation = max(
            BREAKEVEN_ACTIVATION_MIN,
            min(BREAKEVEN_ACTIVATION_MAX, symphony_vol),
        )
        if current_return >= (activation - BREAKEVEN_ACTIVATION_DEADBAND):
            new_hold_ticks = current_hold_ticks + 1
        else:
            new_hold_ticks = 0
        new_breakeven_locked = (
            currently_breakeven_locked
            or (new_hold_ticks >= HWM_HOLD_TICKS_THRESHOLD)
        )
        if new_breakeven_locked:
            stop_trigger_level = max(base_stop_level, 0.0)
        else:
            stop_trigger_level = base_stop_level
        if is_triggered:
            stop_trigger_level = TRIGGERED_OVERRIDE_LEVEL
        return new_hold_ticks, new_breakeven_locked, stop_trigger_level
        '''

LATCHING INVARIANT (load-bearing): once currently_breakeven_locked=True, the
lock SHALL persist through the tick (new_breakeven_locked must always be True
in that case). The inline producer never resets breakeven_locked to False;
that semantics MUST be preserved. The `or` short-circuit captures this:
currently_breakeven_locked or (...) is True whenever the left operand is True.

CONTRACT - caller-normalized inputs (math layer trusts the caller):
  - current_return, symphony_vol, base_stop_level: real-valued floats
    (no NaN, no inf -- caller normalizes those before passing).
  - current_hold_ticks: non-negative int (caller normalization is contract;
    we do NOT pin behavior for negative or non-int values).
  - currently_breakeven_locked, is_triggered: strict Python bool. The
    function uses Python's `or` short-circuit, so truthy non-bools may work
    incidentally, but the caller's contract is bool.

GREEN-phase named constants (project rule: no magic numbers in math_engine.py):
  - BREAKEVEN_ACTIVATION_MIN = 0.4
  - BREAKEVEN_ACTIVATION_MAX = 3.0
  - BREAKEVEN_ACTIVATION_DEADBAND = 0.2
  - HWM_HOLD_TICKS_THRESHOLD = 5
  - TRIGGERED_OVERRIDE_LEVEL = -999.0
  - The 0.0 floor inside max(base_stop_level, 0.0) is a STRUCTURAL zero
    (semantic anchor: "breakeven means stop can't be worse than zero loss")
    and is whitelisted in the magic-number scanner for this function.

Tolerance policy:
- pytest.approx(rel=1e-9, abs=1e-12) on stop_trigger_level only.
- Exact equality (==) on integer new_hold_ticks and bool new_breakeven_locked
  -- pytest.approx is meaningless for ints/bools and would mask bugs.
- Most stop_trigger_level expecteds are EXACT in IEEE-754 (max, conditional
  pass-through, sentinel assignment; no transcendentals). rel=1e-9 absorbs
  any ~1 ulp drift while still catching algorithmic divergence.

Provenance (HARD): every expected value in
tests/fixtures/math_engine/breakeven_update/*.json is DERIVED BY HAND from
the inline producer's logic and pinned in the fixture's 'derivation' field
-- NOT captured from a current implementation (compute_breakeven_update does
not exist yet; this is RED). The math is conditional logic + clamp + max,
and the derivations are spelled out per-fixture.
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
    / "breakeven_update"
)

# --- Tolerance --------------------------------------------------------------

# rel=1e-9 catches any algorithmic divergence (omitted clamp, flipped
# inequality, missing latch, override re-floored). abs=1e-12 lets exact-zero
# expecteds (locked-with-zero-base, locked-with-negative-base floored to 0)
# match cleanly. A wrong impl would miss by orders of magnitude or by a
# structural factor (sentinel vs floor, decrement vs reset), not by ~1 ulp.
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
def test_breakeven_update_matches_derived_expected(
    fixture_name: str, fixture: dict[str, Any]
) -> None:
    """
    Every fixture's expected (new_hold_ticks, new_breakeven_locked,
    stop_trigger_level) tuple is DERIVED BY HAND in the fixture's 'derivation'
    field. This test asserts compute_breakeven_update produces those values.
    Function does not exist yet -- this is RED.

    Assertions:
      - new_hold_ticks: EXACT integer equality (ints, no tolerance).
      - new_breakeven_locked: EXACT bool equality (booleans, no tolerance).
      - stop_trigger_level: pytest.approx with rel=1e-9, abs=1e-12.
    """
    func_name = fixture["function"]
    assert func_name == "compute_breakeven_update", (
        f"{fixture_name}: only compute_breakeven_update is in scope for "
        f"this cycle"
    )

    inputs = fixture["inputs"]
    expected = fixture["expected"]

    result = math_engine.compute_breakeven_update(
        current_return=inputs["current_return"],
        symphony_vol=inputs["symphony_vol"],
        base_stop_level=inputs["base_stop_level"],
        current_hold_ticks=inputs["current_hold_ticks"],
        currently_breakeven_locked=inputs["currently_breakeven_locked"],
        is_triggered=inputs["is_triggered"],
    )

    # Contract: returns a 3-tuple. Unpack defensively to fail with a clear
    # message if the impl returns a different shape.
    assert isinstance(result, tuple), (
        f"Fixture {fixture_name}: expected a tuple, got {type(result).__name__}"
    )
    assert len(result) == 3, (
        f"Fixture {fixture_name}: expected a 3-tuple "
        f"(new_hold_ticks, new_breakeven_locked, stop_trigger_level), "
        f"got length {len(result)}"
    )
    new_hold_ticks, new_breakeven_locked, stop_trigger_level = result

    assert new_hold_ticks == expected["new_hold_ticks"], (
        f"Fixture {fixture_name}: new_hold_ticks mismatch. "
        f"Expected {expected['new_hold_ticks']} "
        f"(derivation: {fixture['derivation']}), got {new_hold_ticks}"
    )
    assert new_breakeven_locked == expected["new_breakeven_locked"], (
        f"Fixture {fixture_name}: new_breakeven_locked mismatch. "
        f"Expected {expected['new_breakeven_locked']} "
        f"(derivation: {fixture['derivation']}), got {new_breakeven_locked}"
    )
    assert stop_trigger_level == pytest.approx(
        expected["stop_trigger_level"], rel=APPROX_REL, abs=APPROX_ABS
    ), (
        f"Fixture {fixture_name}: stop_trigger_level mismatch. "
        f"Expected {expected['stop_trigger_level']} "
        f"(derivation: {fixture['derivation']}), got {stop_trigger_level}"
    )


# --- Property: latching invariant -----------------------------------------


@pytest.mark.parametrize(
    "current_return,symphony_vol,base_stop_level,current_hold_ticks,is_triggered",
    [
        (-10.0, 0.1, -5.0, 0, False),     # vol below MIN, return crashed, ticks zero
        (-10.0, 5.0, -5.0, 0, False),     # vol above MAX
        (0.0, 1.0, 0.0, 0, False),         # zero everything
        (5.0, 2.0, 1.0, 100, False),       # high return, high ticks
        (-1.0, 0.4, -2.0, 4, True),        # triggered=True still latches lock
        (1.5, 3.0, -0.5, 0, True),         # triggered with reset
        (-100.0, 1.0, -50.0, 0, False),    # deep negative return
        (100.0, 1.0, 0.5, 0, False),       # large positive return
    ],
)
def test_latching_invariant_locked_stays_locked(
    current_return: float,
    symphony_vol: float,
    base_stop_level: float,
    current_hold_ticks: int,
    is_triggered: bool,
) -> None:
    """
    LATCHING INVARIANT: when currently_breakeven_locked=True, new_breakeven_locked
    MUST be True for ANY combination of other inputs. The inline producer
    never resets breakeven_locked to False; that semantics is load-bearing
    for downstream stop-floor enforcement.

    Catches an impl that recomputes lock from ticks alone (would unlock when
    ticks reset to 0), or that uses AND instead of OR in the lock-or-threshold
    expression.
    """
    _, new_breakeven_locked, _ = math_engine.compute_breakeven_update(
        current_return=current_return,
        symphony_vol=symphony_vol,
        base_stop_level=base_stop_level,
        current_hold_ticks=current_hold_ticks,
        currently_breakeven_locked=True,
        is_triggered=is_triggered,
    )
    assert new_breakeven_locked is True, (
        f"LATCHING BROKEN: currently_breakeven_locked=True but "
        f"new_breakeven_locked={new_breakeven_locked}. Lock MUST persist "
        f"through the tick. Inputs: current_return={current_return}, "
        f"symphony_vol={symphony_vol}, current_hold_ticks={current_hold_ticks}, "
        f"is_triggered={is_triggered}."
    )


# --- Property: hwm_hold_ticks monotonic increment when return qualifies ----


@pytest.mark.parametrize(
    "starting_ticks",
    [0, 1, 2, 3, 4, 5, 6, 10, 50, 1000],
)
def test_ticks_increment_by_exactly_one_when_return_qualifies(
    starting_ticks: int,
) -> None:
    """
    Invariant: when current_return >= (dynamic_activation - DEADBAND), the
    new_hold_ticks is EXACTLY current_hold_ticks + 1 (no funky scaling,
    no acceleration, no clamping at THRESHOLD).

    Uses symphony_vol=1.0 (so dynamic_activation=1.0, threshold=0.8) and
    current_return=2.0 (well above threshold).

    Catches an impl that:
      - increments by 2 (typo)
      - clamps at THRESHOLD (would stop incrementing past 5)
      - resets when at threshold (would reset instead of continue)
    """
    new_hold_ticks, _, _ = math_engine.compute_breakeven_update(
        current_return=2.0,
        symphony_vol=1.0,
        base_stop_level=0.5,
        current_hold_ticks=starting_ticks,
        currently_breakeven_locked=False,
        is_triggered=False,
    )
    assert new_hold_ticks == starting_ticks + 1, (
        f"Expected ticks to increment by EXACTLY 1 from {starting_ticks} to "
        f"{starting_ticks + 1}. Got {new_hold_ticks}."
    )


# --- Property: ticks reset (not decrement) when return fails to qualify ----


@pytest.mark.parametrize(
    "starting_ticks",
    [0, 1, 2, 3, 4, 5, 6, 10, 50, 1000],
)
def test_ticks_reset_fully_to_zero_when_return_disqualifies(
    starting_ticks: int,
) -> None:
    """
    Invariant: when current_return < (dynamic_activation - DEADBAND), the
    new_hold_ticks MUST be 0 (full reset, NOT decrement). This is the
    reset semantics that the inline producer relies on -- a single
    qualifying-return-then-disqualifying-return pattern fully clears the
    counter.

    Uses symphony_vol=1.0 (threshold=0.8) and current_return=-5.0
    (deeply below).

    Catches an impl that decrements (e.g., max(0, ticks-1)), or that
    holds the previous value instead of resetting.
    """
    new_hold_ticks, _, _ = math_engine.compute_breakeven_update(
        current_return=-5.0,
        symphony_vol=1.0,
        base_stop_level=0.5,
        current_hold_ticks=starting_ticks,
        currently_breakeven_locked=False,
        is_triggered=False,
    )
    assert new_hold_ticks == 0, (
        f"Expected ticks to RESET to 0 from {starting_ticks}, but got "
        f"{new_hold_ticks}. Reset is full, NOT decrement."
    )


# --- Property: triggered override is absolute ------------------------------


@pytest.mark.parametrize(
    "current_return,symphony_vol,base_stop_level,current_hold_ticks,currently_locked",
    [
        (0.0, 1.0, 0.0, 0, False),         # baseline
        (5.0, 1.0, 100.0, 10, True),       # large positive base, locked
        (-5.0, 1.0, -100.0, 0, False),     # large negative base
        (1.0, 0.1, -0.5, 4, False),        # vol below MIN clamp
        (1.0, 5.0, 0.25, 4, True),         # vol above MAX clamp + locked
        (-1.0, 2.0, 50.0, 0, True),        # locked, large positive base
        (0.0, 1.0, 0.0, 0, True),          # locked, zero base
    ],
)
def test_triggered_override_is_absolute(
    current_return: float,
    symphony_vol: float,
    base_stop_level: float,
    current_hold_ticks: int,
    currently_locked: bool,
) -> None:
    """
    Invariant: is_triggered=True ALWAYS produces stop_trigger_level ==
    TRIGGERED_OVERRIDE_LEVEL, regardless of any other input.

    The override is the LAST operation in the producer's pipeline; it beats
    the floor (max(base, 0.0)) and beats the pass-through (base). State
    fields (new_hold_ticks, new_breakeven_locked) still evolve normally --
    ONLY stop_trigger_level is overridden.

    Catches an impl that:
      - applied the override BEFORE the floor (would re-floor -999.0 to 0.0)
      - skipped the override when not locked
      - used a different sentinel value
    """
    _, _, stop_trigger_level = math_engine.compute_breakeven_update(
        current_return=current_return,
        symphony_vol=symphony_vol,
        base_stop_level=base_stop_level,
        current_hold_ticks=current_hold_ticks,
        currently_breakeven_locked=currently_locked,
        is_triggered=True,
    )
    assert stop_trigger_level == pytest.approx(
        math_engine.TRIGGERED_OVERRIDE_LEVEL, rel=APPROX_REL, abs=APPROX_ABS
    ), (
        f"is_triggered=True MUST produce stop_trigger_level == "
        f"TRIGGERED_OVERRIDE_LEVEL ({math_engine.TRIGGERED_OVERRIDE_LEVEL}). "
        f"Got {stop_trigger_level}. Inputs: current_return={current_return}, "
        f"symphony_vol={symphony_vol}, base_stop_level={base_stop_level}, "
        f"current_hold_ticks={current_hold_ticks}, "
        f"currently_locked={currently_locked}."
    )


# --- Property: return type contract ----------------------------------------


def test_return_type_contract_int_bool_float() -> None:
    """
    Contract: returns (int, bool, float). Strict `type() is` checks because:
      - new_hold_ticks is persisted to bot_state and incremented in subsequent
        ticks; numpy int subtypes break the simple `dict[...] + 1` pattern.
      - new_breakeven_locked is persisted to bot_state and used in `if`
        guards; numpy bool serializes inconsistently to SQLite.
      - stop_trigger_level is persisted to SQLite and emitted in Discord
        embeds; numpy.float64 raises 'Error binding parameter' on sqlite3
        in some Python versions. Same defensive pattern as the
        time-squeeze-decay and active-trailing-stop cycles.
    """
    result = math_engine.compute_breakeven_update(
        current_return=1.5,
        symphony_vol=1.0,
        base_stop_level=0.5,
        current_hold_ticks=2,
        currently_breakeven_locked=False,
        is_triggered=False,
    )
    assert isinstance(result, tuple) and len(result) == 3, (
        f"Expected 3-tuple, got {result!r}"
    )
    new_hold_ticks, new_breakeven_locked, stop_trigger_level = result

    assert type(new_hold_ticks) is int, (
        f"new_hold_ticks must be EXACTLY int, got "
        f"{type(new_hold_ticks).__name__} (numpy ints forbidden; bot_state "
        f"increment relies on plain int)."
    )
    # bool is a subclass of int in Python, so we must check bool FIRST and
    # use strict `type() is` to forbid plain int from passing as bool.
    assert type(new_breakeven_locked) is bool, (
        f"new_breakeven_locked must be EXACTLY bool, got "
        f"{type(new_breakeven_locked).__name__} (numpy bools / plain ints "
        f"forbidden; SQLite serialization breaks)."
    )
    assert type(stop_trigger_level) is float, (
        f"stop_trigger_level must be EXACTLY float, got "
        f"{type(stop_trigger_level).__name__} (numpy floats forbidden; "
        f"sqlite3/json/Discord serialization breaks on np.float64)."
    )


# --- Property: determinism --------------------------------------------------


def test_function_is_pure_repeat_call_returns_identical_result() -> None:
    """
    Sanity: a pure function returns identical results when called twice with
    the same arguments. Catches an impl that accidentally stashes state in a
    module-level variable or has a hidden time-dependence.
    """
    args = {
        "current_return": 1.7,
        "symphony_vol": 1.2,
        "base_stop_level": -0.25,
        "current_hold_ticks": 3,
        "currently_breakeven_locked": False,
        "is_triggered": False,
    }
    a = math_engine.compute_breakeven_update(**args)
    b = math_engine.compute_breakeven_update(**args)
    # Exact equality for the full tuple: a pure deterministic function MUST
    # produce bit-identical outputs, not approx-equal.
    assert a == b, f"Non-deterministic output: {a} vs {b}"


# --- Module-level named constants exist ------------------------------------


def test_breakeven_activation_min_is_module_level_named_constant() -> None:
    """
    Project rule: 'No magic numbers in math_engine.py -- every constant
    named + source comment.' The 0.4 lower clamp must be lifted into a
    module-level constant named BREAKEVEN_ACTIVATION_MIN.
    """
    assert hasattr(math_engine, "BREAKEVEN_ACTIVATION_MIN"), (
        "math_engine.BREAKEVEN_ACTIVATION_MIN not found -- the lower clamp "
        "for dynamic_activation must be a named module-level constant."
    )
    assert math_engine.BREAKEVEN_ACTIVATION_MIN == 0.4, (
        f"BREAKEVEN_ACTIVATION_MIN should be 0.4 (lower clamp), got "
        f"{math_engine.BREAKEVEN_ACTIVATION_MIN}"
    )


def test_breakeven_activation_max_is_module_level_named_constant() -> None:
    """
    Project rule: every constant named. The 3.0 upper clamp must be a
    module-level constant named BREAKEVEN_ACTIVATION_MAX.
    """
    assert hasattr(math_engine, "BREAKEVEN_ACTIVATION_MAX"), (
        "math_engine.BREAKEVEN_ACTIVATION_MAX not found -- the upper clamp "
        "for dynamic_activation must be a named module-level constant."
    )
    assert math_engine.BREAKEVEN_ACTIVATION_MAX == 3.0, (
        f"BREAKEVEN_ACTIVATION_MAX should be 3.0 (upper clamp), got "
        f"{math_engine.BREAKEVEN_ACTIVATION_MAX}"
    )


def test_breakeven_activation_deadband_is_module_level_named_constant() -> None:
    """
    Project rule: every constant named. The 0.2 deadband must be a
    module-level constant named BREAKEVEN_ACTIVATION_DEADBAND.
    """
    assert hasattr(math_engine, "BREAKEVEN_ACTIVATION_DEADBAND"), (
        "math_engine.BREAKEVEN_ACTIVATION_DEADBAND not found -- the "
        "activation-deadband offset must be a named module-level constant."
    )
    assert math_engine.BREAKEVEN_ACTIVATION_DEADBAND == 0.2, (
        f"BREAKEVEN_ACTIVATION_DEADBAND should be 0.2, got "
        f"{math_engine.BREAKEVEN_ACTIVATION_DEADBAND}"
    )


def test_hwm_hold_ticks_threshold_is_module_level_named_constant() -> None:
    """
    Project rule: every constant named. The 5-tick threshold must be a
    module-level constant named HWM_HOLD_TICKS_THRESHOLD.
    """
    assert hasattr(math_engine, "HWM_HOLD_TICKS_THRESHOLD"), (
        "math_engine.HWM_HOLD_TICKS_THRESHOLD not found -- the consecutive-"
        "tick threshold for breakeven lock must be a named module-level "
        "constant."
    )
    assert math_engine.HWM_HOLD_TICKS_THRESHOLD == 5, (
        f"HWM_HOLD_TICKS_THRESHOLD should be 5, got "
        f"{math_engine.HWM_HOLD_TICKS_THRESHOLD}"
    )


def test_triggered_override_level_is_module_level_named_constant() -> None:
    """
    Project rule: every constant named. The -999.0 sentinel must be a
    module-level constant named TRIGGERED_OVERRIDE_LEVEL.
    """
    assert hasattr(math_engine, "TRIGGERED_OVERRIDE_LEVEL"), (
        "math_engine.TRIGGERED_OVERRIDE_LEVEL not found -- the sentinel "
        "value used to disable the trailing-stop check on already-triggered "
        "positions must be a named module-level constant."
    )
    assert math_engine.TRIGGERED_OVERRIDE_LEVEL == -999.0, (
        f"TRIGGERED_OVERRIDE_LEVEL should be -999.0, got "
        f"{math_engine.TRIGGERED_OVERRIDE_LEVEL}"
    )


# --- Constant-provenance scanner -------------------------------------------


def test_no_unnamed_magic_numbers_in_breakeven_update_path() -> None:
    """
    Project rule: 'No magic numbers in math_engine.py -- every constant
    named + source comment.'

    Scans the AST of compute_breakeven_update for numeric literals. Each
    literal must either be:
      (a) a 'trivially structural' value:
          0, 1, -1 (universal int constants),
          0.0 (the breakeven-floor structural zero -- semantic anchor for
                "stop can't be worse than zero loss"), or
      (b) accompanied by a named-constant assignment or an explanatory
          source comment on the same line.

    The five extracted domain constants (BREAKEVEN_ACTIVATION_MIN/MAX/
    DEADBAND, HWM_HOLD_TICKS_THRESHOLD, TRIGGERED_OVERRIDE_LEVEL) MUST be
    referenced by Name from the function body, NOT inlined as bare 0.4,
    3.0, 0.2, 5, -999.0 literals.

    The function does not exist yet (RED), and a naive GREEN impl that
    copy-pasted `0.4`, `3.0`, `0.2`, `5`, `-999.0` from the inline producer
    would fail this scanner.
    """
    src_path = pathlib.Path(math_engine.__file__)
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    src_lines = source.splitlines()

    # Structural literals (universal "structural" values that carry no
    # domain meaning in this function). Python hashes int 0 and float 0.0
    # as the same key, so a single `0` entry covers both the comparison
    # threshold and the breakeven floor at max(base_stop_level, 0.0) (the
    # 0.0 is a semantic anchor: "stop can't be worse than zero loss" --
    # explicitly whitelisted for this function only).
    STRUCTURAL = {
        0,    # universal zero AND breakeven floor 0.0 (same hash key)
        1,    # increment +1 in `ticks + 1` (universal one)
        -1,   # unlikely but harmless
    }

    target: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "compute_breakeven_update"
        ):
            target = node
            break
    assert target is not None, (
        "compute_breakeven_update not found in math_engine.py "
        "(this is expected in RED; the function will be created in GREEN)"
    )

    # Lines where an ast.Name is assigned a Constant count as 'named' (local
    # constants spelled out inside the function body). Module-level constants
    # referenced by Name produce ast.Name nodes (not ast.Constant), so they
    # are correctly NOT flagged here.
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
        "Unnamed magic numbers in compute_breakeven_update (project rule: "
        "every constant in math_engine.py must be named + source-commented). "
        f"Offenders (line, value): {offenders}. Fix in the GREEN phase by "
        "referencing BREAKEVEN_ACTIVATION_MIN, BREAKEVEN_ACTIVATION_MAX, "
        "BREAKEVEN_ACTIVATION_DEADBAND, HWM_HOLD_TICKS_THRESHOLD, and "
        "TRIGGERED_OVERRIDE_LEVEL by name."
    )


def test_domain_constants_are_named_not_bare_literals_in_function_body() -> None:
    """
    Sharper guard than the structural-whitelist scanner above: walks the
    BODY of compute_breakeven_update and asserts that NO ast.Constant with
    a value matching one of the five extracted domain constants appears.

    Why this is needed in addition to the structural scanner: a future
    refactor that adds (e.g.) 5 to STRUCTURAL would let a bare 5 slip
    through. This test pins the project rule for THIS specific function:
    domain constants must be referenced by Name, never inlined.

    Domain values to forbid as bare literals inside the function body:
      0.4, 3.0, 0.2, 5, -999.0
    """
    src_path = pathlib.Path(math_engine.__file__)
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    target: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "compute_breakeven_update"
        ):
            target = node
            break
    assert target is not None, (
        "compute_breakeven_update not found in math_engine.py "
        "(expected RED state)"
    )

    # Domain values that MUST come from named constants, never bare.
    # We list both int and float forms where ambiguity matters; Python's
    # `==` treats 5 == 5.0, so we check by exact value.
    forbidden_values = {0.4, 3.0, 0.2, 5, -999.0}

    offenders: list[tuple[int, Any]] = []
    for sub in ast.walk(target):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, int | float):
            if isinstance(sub.value, bool):
                continue
            # Use exact-value membership; Python set hashing treats 5 and
            # 5.0 identically, so a bare `5` will match `5.0` in the set.
            if sub.value in forbidden_values:
                offenders.append((sub.lineno, sub.value))

    assert not offenders, (
        "Bare domain-constant literal(s) found inside compute_breakeven_"
        f"update at (line, value): {offenders}. The five extracted constants "
        "(BREAKEVEN_ACTIVATION_MIN=0.4, BREAKEVEN_ACTIVATION_MAX=3.0, "
        "BREAKEVEN_ACTIVATION_DEADBAND=0.2, HWM_HOLD_TICKS_THRESHOLD=5, "
        "TRIGGERED_OVERRIDE_LEVEL=-999.0) MUST be referenced by name from "
        "the function body. Fix in GREEN by using the named constants."
    )
