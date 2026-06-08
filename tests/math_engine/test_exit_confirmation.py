"""
Golden-fixture + property tests for the exit-confirmation layer of
math_engine.py.

[H1 UPDATE] The MC second-opinion metric is ``prob_underperforming`` (the
fraction of regime-matched analog days that beat us — HIGH when badly
underperforming). The legacy name ``prob_beating`` was the INVERSE of the value;
H1 renames it AND flips the gate operator. This file pins the CORRECTED gate:
the protective trailing stop FIRES on a confirmed breakdown
(``prob_underperforming >= MC_BREAKDOWN_THRESHOLD``) and is suppressed when the
analogs say today is normal. (See validation/VERDICT.md finding H1; the prior
``prob_beating < MC_SANITY_THRESHOLD`` gate SUPPRESSED the stop in exactly the
idiosyncratic-crash scenario it exists to defend.)

CORRECTED PURE INTERFACE:

    def compute_exit_confirmation(
        armed: bool,
        is_triggered: bool,
        current_return: float,
        stop_trigger_level: float,
        prob_underperforming: float | None,
        current_below_stop_count: int,
        exit_confirm_ticks: int = EXIT_CONFIRM_TICKS,
    ) -> tuple[int, bool]:
        '''
        Returns (new_below_stop_count, is_trailing_stop_hit).

        if not armed or is_triggered:
            return current_below_stop_count, False
        # Fail-open on None (MC unavailable / regime override): the magnitude
        # condition alone may fire the protective stop.
        mc_breakdown_ok = (prob_underperforming is None
                           or prob_underperforming >= MC_BREAKDOWN_THRESHOLD)
        below_stop_condition = (
            current_return <= stop_trigger_level - MAGNITUDE_FLOOR_PCT
            and mc_breakdown_ok
        )
        if below_stop_condition:
            new_count = current_below_stop_count + 1
            hit = new_count >= exit_confirm_ticks
            return new_count, hit
        return 0, False
        '''

GUARD INVARIANT (load-bearing): when (not armed) or is_triggered, the function
returns the INPUT below_stop_count UNCHANGED and False for hit. The entire
stop-check block is skipped under those conditions -- the counter is NEITHER
incremented NOR reset.

CONTRACT - caller-normalized inputs (math layer trusts the caller):
  - armed, is_triggered: strict Python bool.
  - current_return, stop_trigger_level: real-valued floats (caller normalizes
    NaN/inf before passing).
  - prob_underperforming: real-valued float OR None (the MC-unavailable / regime
    override sentinel; the gate fails open on None).
  - current_below_stop_count: non-negative int (caller's contract; no
    behavior pinned for negative or non-int values).

Named constants (project rule: no magic numbers in math_engine.py):
  - MAGNITUDE_FLOOR_PCT = 0.10
    (return must drop at least this far BELOW stop_trigger_level to count
    toward exit confirmation -- a buffer against MC-noise / single-tick spikes)
  - MC_BREAKDOWN_THRESHOLD = 60.0
    ([H1] underperformance probability AT OR ABOVE which the breakdown is
    'confirmed' and the protective stop may fire -- 'capitulate only once the
    analog distribution confirms we are badly underperforming'. Renamed from
    MC_SANITY_THRESHOLD; same value, corrected direction.)
  - EXIT_CONFIRM_TICKS = 3
    (consecutive qualifying ticks needed to flip is_trailing_stop_hit)

Tolerance policy:
- No floats in expected outputs (the function returns (int, bool) only); exact
  equality applies. pytest.approx is not used because there are no float
  outputs to compare. Boolean values use `is True`/`is False` where strict
  identity matters; integer values use `==`.

Provenance (HARD): every expected value in
tests/fixtures/math_engine/exit_confirmation/*.json is DERIVED BY HAND from
the inline producer's logic and pinned in the fixture's 'derivation' field --
NOT captured from a current implementation (compute_exit_confirmation does
not exist yet; this is RED).
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
    / "exit_confirmation"
)


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
def test_exit_confirmation_matches_derived_expected(
    fixture_name: str, fixture: dict[str, Any]
) -> None:
    """
    Every fixture's expected (new_below_stop_count, is_trailing_stop_hit)
    tuple is DERIVED BY HAND in the fixture's 'derivation' field. This test
    asserts compute_exit_confirmation produces those values. The function
    does not exist yet -- this is RED.

    Assertions:
      - new_below_stop_count: EXACT integer equality (no tolerance; ints).
      - is_trailing_stop_hit: EXACT bool equality (no tolerance; booleans).
    """
    func_name = fixture["function"]
    assert func_name == "compute_exit_confirmation", (
        f"{fixture_name}: only compute_exit_confirmation is in scope for "
        f"this cycle"
    )

    inputs = fixture["inputs"]
    expected = fixture["expected"]

    result = math_engine.compute_exit_confirmation(
        armed=inputs["armed"],
        is_triggered=inputs["is_triggered"],
        current_return=inputs["current_return"],
        stop_trigger_level=inputs["stop_trigger_level"],
        prob_underperforming=inputs["prob_underperforming"],
        current_below_stop_count=inputs["current_below_stop_count"],
    )

    # Contract: 2-tuple. Defensive unpack with a clear failure message if
    # the impl returns a different shape.
    assert isinstance(result, tuple), (
        f"Fixture {fixture_name}: expected a tuple, got {type(result).__name__}"
    )
    assert len(result) == 2, (
        f"Fixture {fixture_name}: expected a 2-tuple "
        f"(new_below_stop_count, is_trailing_stop_hit), "
        f"got length {len(result)}"
    )
    new_below_stop_count, is_trailing_stop_hit = result

    assert new_below_stop_count == expected["new_below_stop_count"], (
        f"Fixture {fixture_name}: new_below_stop_count mismatch. "
        f"Expected {expected['new_below_stop_count']} "
        f"(derivation: {fixture['derivation']}), got {new_below_stop_count}"
    )
    assert is_trailing_stop_hit == expected["is_trailing_stop_hit"], (
        f"Fixture {fixture_name}: is_trailing_stop_hit mismatch. "
        f"Expected {expected['is_trailing_stop_hit']} "
        f"(derivation: {fixture['derivation']}), got {is_trailing_stop_hit}"
    )


# --- Property: guard absoluteness for not-armed -----------------------------


@pytest.mark.parametrize(
    "current_return,stop_trigger_level,prob_underperforming,current_below_stop_count,is_triggered",
    [
        (-100.0, -1.0, 1.0, 0, False),     # would qualify if armed
        (-100.0, -1.0, 1.0, 2, False),     # mid-count
        (-100.0, -1.0, 1.0, 10, False),    # well above EXIT_CONFIRM_TICKS
        (5.0, 0.0, 99.9, 0, False),         # condition would fail (no reset either)
        (5.0, 0.0, 99.9, 7, False),         # would reset if armed
        (0.0, 0.0, 60.0, 5, True),          # also triggered
        (-5.0, -1.0, 30.0, 4, True),        # armed=False AND triggered=True; armed=False short-circuits
    ],
)
def test_guard_not_armed_preserves_count_and_no_hit(
    current_return: float,
    stop_trigger_level: float,
    prob_underperforming: float,
    current_below_stop_count: int,
    is_triggered: bool,
) -> None:
    """
    GUARD INVARIANT: when armed=False, the function returns
    (current_below_stop_count, False) regardless of every other input.

    Catches an impl that:
      - reset the count when not armed (forgot to skip the else-branch)
      - incremented the count when not armed (no guard at all)
      - flipped is_trailing_stop_hit to True without armed check
    """
    new_count, hit = math_engine.compute_exit_confirmation(
        armed=False,
        is_triggered=is_triggered,
        current_return=current_return,
        stop_trigger_level=stop_trigger_level,
        prob_underperforming=prob_underperforming,
        current_below_stop_count=current_below_stop_count,
    )
    assert new_count == current_below_stop_count, (
        f"GUARD BROKEN (not armed): count must be preserved at "
        f"{current_below_stop_count}, got {new_count}. Inputs: "
        f"current_return={current_return}, stop={stop_trigger_level}, "
        f"prob={prob_underperforming}, is_triggered={is_triggered}."
    )
    assert hit is False, (
        f"GUARD BROKEN (not armed): hit must be False, got {hit}. Inputs: "
        f"current_return={current_return}, stop={stop_trigger_level}, "
        f"prob={prob_underperforming}, count={current_below_stop_count}, "
        f"is_triggered={is_triggered}."
    )


# --- Property: guard absoluteness for is_triggered --------------------------


@pytest.mark.parametrize(
    "armed,current_return,stop_trigger_level,prob_underperforming,current_below_stop_count",
    [
        (True, -100.0, -1.0, 1.0, 0),     # condition would qualify if not triggered
        (True, -100.0, -1.0, 1.0, 2),     # mid-count
        (True, -100.0, -1.0, 1.0, 10),    # would hit if not triggered
        (True, 5.0, 0.0, 99.9, 0),         # condition would fail (reset path)
        (True, 5.0, 0.0, 99.9, 7),         # would reset if not triggered
        (False, 0.0, 0.0, 60.0, 5),        # also not armed
        (True, 1.0, 1.0, 1.0, 999),        # huge stale count
    ],
)
def test_guard_is_triggered_preserves_count_and_no_hit(
    armed: bool,
    current_return: float,
    stop_trigger_level: float,
    prob_underperforming: float,
    current_below_stop_count: int,
) -> None:
    """
    GUARD INVARIANT: when is_triggered=True, the function returns
    (current_below_stop_count, False) regardless of every other input
    (including armed=True). The inline producer's `and not triggered` clause
    short-circuits the whole block.

    Catches an impl that omitted the triggered check (would re-fire exit on
    an already-exited position) or that reset the count under triggered.
    """
    new_count, hit = math_engine.compute_exit_confirmation(
        armed=armed,
        is_triggered=True,
        current_return=current_return,
        stop_trigger_level=stop_trigger_level,
        prob_underperforming=prob_underperforming,
        current_below_stop_count=current_below_stop_count,
    )
    assert new_count == current_below_stop_count, (
        f"GUARD BROKEN (is_triggered): count must be preserved at "
        f"{current_below_stop_count}, got {new_count}. Inputs: armed={armed}, "
        f"current_return={current_return}, stop={stop_trigger_level}, "
        f"prob={prob_underperforming}."
    )
    assert hit is False, (
        f"GUARD BROKEN (is_triggered): hit must be False (already-triggered "
        f"position should NEVER re-fire), got {hit}. Inputs: armed={armed}, "
        f"current_return={current_return}, stop={stop_trigger_level}, "
        f"prob={prob_underperforming}, count={current_below_stop_count}."
    )


# --- Property: count increments by EXACTLY 1 when condition met -------------


@pytest.mark.parametrize(
    "starting_count",
    [0, 1, 2, 3, 4, 5, 10, 50, 1000],
)
def test_count_increments_by_exactly_one_when_condition_met(
    starting_count: int,
) -> None:
    """
    Invariant: when armed AND not triggered AND magnitude met AND breakdown
    confirmed, the new_below_stop_count is EXACTLY current_below_stop_count + 1
    (no scaling, no clamping, no acceleration).

    Uses inputs well inside both gates so float-boundary effects cannot
    interfere: current_return=-5.0, stop_trigger_level=-1.0 (so threshold is
    -1.10, deeply under), prob_underperforming=90.0 ([H1] well ABOVE
    MC_BREAKDOWN_THRESHOLD 60.0 -> confirmed breakdown).

    Catches an impl that:
      - increments by 2 (typo)
      - clamps the count at EXIT_CONFIRM_TICKS (would stop incrementing past 3)
      - resets to 1 once threshold is crossed (would lose the "stays above"
        semantics of the producer's `>= 3`)
    """
    new_count, _ = math_engine.compute_exit_confirmation(
        armed=True,
        is_triggered=False,
        current_return=-5.0,
        stop_trigger_level=-1.0,
        prob_underperforming=90.0,
        current_below_stop_count=starting_count,
    )
    assert new_count == starting_count + 1, (
        f"Expected count to increment by EXACTLY 1 from {starting_count} to "
        f"{starting_count + 1}. Got {new_count}."
    )


# --- Property: count resets fully to 0 (NOT decrement) on condition fail ----


@pytest.mark.parametrize(
    "starting_count",
    [0, 1, 2, 3, 4, 5, 10, 50, 1000],
)
def test_count_resets_fully_to_zero_when_condition_fails(
    starting_count: int,
) -> None:
    """
    Invariant: when armed AND not triggered AND condition fails (either
    magnitude OR breakdown gate), new_below_stop_count MUST be 0 (full reset,
    not decrement). This matches the producer's unconditional `= 0` in the
    else-branch.

    Uses inputs that fail BOTH gates so the result is unambiguous:
    current_return=5.0 (well above stop_trigger_level=-1.0, so magnitude
    fails), prob_underperforming=10.0 ([H1] well BELOW MC_BREAKDOWN_THRESHOLD
    60.0, so the breakdown gate fails -- analogs say today is normal).

    Catches an impl that decremented (e.g., max(0, count-1)), held the
    previous value, or only reset under one gate.
    """
    new_count, _ = math_engine.compute_exit_confirmation(
        armed=True,
        is_triggered=False,
        current_return=5.0,
        stop_trigger_level=-1.0,
        prob_underperforming=10.0,
        current_below_stop_count=starting_count,
    )
    assert new_count == 0, (
        f"Expected count to RESET to 0 from {starting_count}, but got "
        f"{new_count}. Reset is FULL, NOT decrement."
    )


# --- Property: hit iff (armed AND !triggered AND condition_met AND
#                       new_count >= EXIT_CONFIRM_TICKS) -------------------


@pytest.mark.parametrize(
    "armed,is_triggered,condition_met,starting_count,expected_hit",
    [
        # armed=False -> hit must be False under all sub-conditions
        (False, False, True,  2, False),
        (False, False, True,  10, False),
        (False, True,  True,  2, False),
        # is_triggered=True -> hit must be False
        (True,  True,  True,  2, False),
        (True,  True,  True,  10, False),
        # armed AND !triggered AND condition met
        (True,  False, True,  0, False),    # 0+1=1, not >= 3
        (True,  False, True,  1, False),    # 1+1=2, not >= 3
        (True,  False, True,  2, True),     # 2+1=3, hit
        (True,  False, True,  3, True),     # 3+1=4, still hit (elif >= 3)
        (True,  False, True,  100, True),
        # armed AND !triggered AND condition fails -> hit False
        (True,  False, False, 0, False),
        (True,  False, False, 2, False),
        (True,  False, False, 10, False),
    ],
)
def test_hit_exactly_when_threshold_reached_and_guards_pass(
    armed: bool,
    is_triggered: bool,
    condition_met: bool,
    starting_count: int,
    expected_hit: bool,
) -> None:
    """
    The truth-table for is_trailing_stop_hit. Hit is True iff:
        armed AND (not is_triggered) AND condition_met AND
        (current_below_stop_count + 1 >= EXIT_CONFIRM_TICKS)

    `condition_met` is set up via input values:
      - True: current_return=-5.0, stop=-1.0 (-> threshold -1.10), prob=90.0
              ([H1] both gates pass: magnitude met AND confirmed breakdown
              prob_underperforming 90 >= MC_BREAKDOWN_THRESHOLD 60)
      - False: current_return=5.0, stop=-1.0, prob=10.0 ([H1] both gates fail:
              price up AND low underperformance 10 < 60)
    """
    if condition_met:
        current_return, stop, prob = -5.0, -1.0, 90.0
    else:
        current_return, stop, prob = 5.0, -1.0, 10.0
    _, hit = math_engine.compute_exit_confirmation(
        armed=armed,
        is_triggered=is_triggered,
        current_return=current_return,
        stop_trigger_level=stop,
        prob_underperforming=prob,
        current_below_stop_count=starting_count,
    )
    assert hit is expected_hit, (
        f"Hit truth-table mismatch. Inputs: armed={armed}, "
        f"is_triggered={is_triggered}, condition_met={condition_met}, "
        f"starting_count={starting_count}. Expected hit={expected_hit}, "
        f"got {hit}."
    )


# --- Property: return type contract (int, bool) -----------------------------


def test_return_type_contract_int_bool() -> None:
    """
    Contract: returns (int, bool). Strict `type() is` checks because:
      - new_below_stop_count is persisted to bot_state and incremented in
        subsequent ticks (`bot_state[...]['below_stop_count'] += 1`);
        numpy int subtypes break the simple int-increment pattern and
        serialize inconsistently to SQLite.
      - is_trailing_stop_hit gates a downstream `if` that initiates the live
        exit; numpy bool serializes inconsistently and Python bool is a
        subclass of int so plain int passing as bool would be a category
        error. Same defensive pattern as cycles 3-6.
    """
    result = math_engine.compute_exit_confirmation(
        armed=True,
        is_triggered=False,
        current_return=-5.0,
        stop_trigger_level=-1.0,
        prob_underperforming=90.0,
        current_below_stop_count=2,
    )
    assert isinstance(result, tuple) and len(result) == 2, (
        f"Expected 2-tuple, got {result!r}"
    )
    new_count, hit = result

    assert type(new_count) is int, (
        f"new_below_stop_count must be EXACTLY int, got "
        f"{type(new_count).__name__} (numpy ints / bools forbidden; "
        f"bot_state increment relies on plain int)."
    )
    # bool is a subclass of int in Python; strict `type() is` is mandatory
    # to forbid plain int from passing where bool is contracted.
    assert type(hit) is bool, (
        f"is_trailing_stop_hit must be EXACTLY bool, got "
        f"{type(hit).__name__} (numpy bools / plain ints forbidden; gates "
        f"downstream exit -- ambiguous types are a category error)."
    )


# --- Property: determinism --------------------------------------------------


def test_function_is_pure_repeat_call_returns_identical_result() -> None:
    """
    Sanity: a pure function returns identical results when called twice with
    the same arguments. Catches an impl that accidentally stashes state in a
    module-level variable or has a hidden time-dependence.
    """
    args = {
        "armed": True,
        "is_triggered": False,
        "current_return": -1.5,
        "stop_trigger_level": -1.0,
        "prob_underperforming": 30.0,
        "current_below_stop_count": 2,
    }
    a = math_engine.compute_exit_confirmation(**args)
    b = math_engine.compute_exit_confirmation(**args)
    assert a == b, f"Non-deterministic output: {a} vs {b}"


# --- Module-level named constants exist ------------------------------------


def test_magnitude_floor_pct_is_module_level_named_constant() -> None:
    """
    Project rule: 'No magic numbers in math_engine.py -- every constant
    named + source comment.' The 0.10 magnitude offset must be a module-level
    constant named MAGNITUDE_FLOOR_PCT.
    """
    assert hasattr(math_engine, "MAGNITUDE_FLOOR_PCT"), (
        "math_engine.MAGNITUDE_FLOOR_PCT not found -- the magnitude offset "
        "(stop_trigger_level - MAGNITUDE_FLOOR_PCT) must be a named module-"
        "level constant."
    )
    assert math_engine.MAGNITUDE_FLOOR_PCT == 0.10, (
        f"MAGNITUDE_FLOOR_PCT should be 0.10, got "
        f"{math_engine.MAGNITUDE_FLOOR_PCT}"
    )


def test_mc_breakdown_threshold_is_module_level_named_constant() -> None:
    """
    Project rule: every constant named. [H1] The 60.0 breakdown threshold must
    be a module-level constant named MC_BREAKDOWN_THRESHOLD (renamed from
    MC_SANITY_THRESHOLD; same value, corrected direction). The OLD name must be
    GONE — a surviving MC_SANITY_THRESHOLD would mean an incomplete rename.
    """
    assert hasattr(math_engine, "MC_BREAKDOWN_THRESHOLD"), (
        "math_engine.MC_BREAKDOWN_THRESHOLD not found -- the breakdown "
        "probability threshold must be a named module-level constant (H1 "
        "rename of MC_SANITY_THRESHOLD)."
    )
    assert math_engine.MC_BREAKDOWN_THRESHOLD == 60.0, (
        f"MC_BREAKDOWN_THRESHOLD should be 60.0 (value preserved), got "
        f"{math_engine.MC_BREAKDOWN_THRESHOLD}"
    )
    assert not hasattr(math_engine, "MC_SANITY_THRESHOLD"), (
        "math_engine.MC_SANITY_THRESHOLD still exists -- the H1 rename to "
        "MC_BREAKDOWN_THRESHOLD must REMOVE the old constant name (a lingering "
        "old name is an incomplete rename and a value-vs-name hazard)."
    )


def test_exit_confirm_ticks_is_module_level_named_constant() -> None:
    """
    Project rule: every constant named. The 3-tick exit-confirmation
    threshold must be a module-level constant named EXIT_CONFIRM_TICKS.
    """
    assert hasattr(math_engine, "EXIT_CONFIRM_TICKS"), (
        "math_engine.EXIT_CONFIRM_TICKS not found -- the consecutive-tick "
        "threshold for flipping is_trailing_stop_hit must be a named "
        "module-level constant."
    )
    assert math_engine.EXIT_CONFIRM_TICKS == 3, (
        f"EXIT_CONFIRM_TICKS should be 3, got {math_engine.EXIT_CONFIRM_TICKS}"
    )


# --- Constant-provenance scanner -------------------------------------------


def test_no_unnamed_magic_numbers_in_exit_confirmation_path() -> None:
    """
    Project rule: 'No magic numbers in math_engine.py -- every constant
    named + source comment.'

    Scans the AST of compute_exit_confirmation for numeric literals. Each
    literal must either be:
      (a) a 'trivially structural' value:
          0, 1, -1 (universal int constants), or
      (b) accompanied by a named-constant assignment or an explanatory
          source comment on the same line.

    The three extracted domain constants (MAGNITUDE_FLOOR_PCT,
    MC_BREAKDOWN_THRESHOLD, EXIT_CONFIRM_TICKS) MUST be referenced by Name
    from the function body, NOT inlined as bare 0.10, 60.0, 3 literals.

    The function does not exist yet (RED); a naive GREEN impl that copy-
    pasted 0.10, 60.0, 3 from the inline producer would fail this scanner.
    """
    src_path = pathlib.Path(math_engine.__file__)
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    src_lines = source.splitlines()

    # Structural literals (universal "structural" values that carry no
    # domain meaning in this function). Python hashes int 0 and float 0.0
    # as the same key.
    STRUCTURAL = {
        0,    # universal zero (reset value)
        1,    # increment +1
        -1,   # unlikely but harmless
    }

    target: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "compute_exit_confirmation"
        ):
            target = node
            break
    assert target is not None, (
        "compute_exit_confirmation not found in math_engine.py "
        "(expected in RED; the function will be created in GREEN)"
    )

    # Lines where an ast.Name is assigned a Constant count as 'named' (local
    # constants spelled inside the function body). Module-level constants
    # referenced by Name produce ast.Name nodes (not ast.Constant) and are
    # correctly NOT flagged here.
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
        "Unnamed magic numbers in compute_exit_confirmation (project rule: "
        "every constant in math_engine.py must be named + source-commented). "
        f"Offenders (line, value): {offenders}. Fix in the GREEN phase by "
        "referencing MAGNITUDE_FLOOR_PCT, MC_BREAKDOWN_THRESHOLD, and "
        "EXIT_CONFIRM_TICKS by name."
    )


def test_domain_constants_are_named_not_bare_literals_in_function_body() -> None:
    """
    Sharper guard than the structural-whitelist scanner above: walks the
    BODY of compute_exit_confirmation and asserts NO ast.Constant with a
    value matching one of the three extracted domain constants appears.

    Why needed in addition to the structural scanner: a future refactor that
    added (e.g.) 3 to STRUCTURAL would let a bare 3 slip through. This test
    pins the project rule for THIS specific function: domain constants must
    be referenced by Name, never inlined.

    Domain values forbidden as bare literals in the function body:
      0.10, 60.0, 3
    """
    src_path = pathlib.Path(math_engine.__file__)
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    target: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "compute_exit_confirmation"
        ):
            target = node
            break
    assert target is not None, (
        "compute_exit_confirmation not found in math_engine.py "
        "(expected RED state)"
    )

    forbidden_values = {0.10, 60.0, 3}

    offenders: list[tuple[int, Any]] = []
    for sub in ast.walk(target):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, int | float):
            if isinstance(sub.value, bool):
                continue
            # Python set hashing: 3 and 3.0 hash identically, so a bare `3`
            # would match `3` (and `3.0` if added). 0.10 is the float-form
            # of the magnitude offset.
            if sub.value in forbidden_values:
                offenders.append((sub.lineno, sub.value))

    assert not offenders, (
        "Bare domain-constant literal(s) found inside compute_exit_"
        f"confirmation at (line, value): {offenders}. The three extracted "
        "constants (MAGNITUDE_FLOOR_PCT=0.10, MC_BREAKDOWN_THRESHOLD=60.0, "
        "EXIT_CONFIRM_TICKS=3) MUST be referenced by name from the function "
        "body. Fix in GREEN by using the named constants."
    )
