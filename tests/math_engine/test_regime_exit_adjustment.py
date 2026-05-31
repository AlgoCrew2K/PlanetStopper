"""
RED tests — Phase 3c: regime-conditional reactive response (exit lever).

Scope (Gate-1 + team brief): a new pure function
math_engine.apply_regime_exit_adjustment(regime_label, base_ticks) -> int
that applies a FIXED, theory-pre-specified per-regime adjustment to the
confirmation-tick threshold used by compute_exit_confirmation.  The
adjustment table (named constants) is the ONLY knob; it is NOT learned.

Design basis: PHASE3_DECISIONS.md, 00-ADAPTIVE-RECOMMENDATION.md §1A
(Kaminski-Lo 2014 direction), team-brief hard constraints 1-5.

Per-regime direction (team brief, Kaminski-Lo-sourced):
  TRENDING       -> more-active  (fewer confirmation ticks -> exits faster)
  MEAN-REVERTING -> RESTRAINT    (more ticks -> harder to confirm -> fewer exits)
  HIGH-VOL       -> bounded-protective (ticks at or below base, bounded >= 1)
  None / unknown -> SAFE DEFAULT (base_ticks unchanged, no adjustment)

Hard constraints this file tests (team brief):
  1. NO-BLOCKING-I/O / OFFLINE LABEL: the execution path reads a CACHED
     label from the database, never calls classify_regime() live.
     Tested here (structural) and in tests/execution/test_regime_cache_isolation.py.
  2. BOUNDED: adjustments are named constants bounded by REGIME_TICKS_LOWER_BOUND
     and REGIME_TICKS_UPPER_BOUND.  No regime can push ticks outside [1, 8].
  3. COMPOSES with fail-safes: H-3 fail-open path + MC-None path continue to work
     under all regimes.  Tested in test_fail_safe_composes_with_regime_adjustment.
  4. SAFE DEFAULT: absent/unknown regime -> current behavior (base_ticks unchanged).
  5. DIRECTION: mean-reverting demonstrably LESS-aggressive (adjusted > base);
     trending demonstrably more-active (adjusted < base).

The function does NOT exist yet.  All tests are RED by construction — they will
fail with AttributeError / ImportError until the implementer adds the function.

Golden fixtures live in:
  tests/fixtures/math/regime_exit_adjustment/

Each fixture records direction / bound contract (never a hardcoded implementer-
produced value) per the project rule: 'tests never hardcode producer outputs'.
The EXACT per-regime tick values are pinned in the NAMED CONSTANTS
(TRENDING_EXIT_TICKS, MEAN_REVERTING_EXIT_TICKS, HIGH_VOL_EXIT_TICKS) in
math_engine.py — this file tests properties and directions, not specific values.

Tolerance policy: all expected values are integers or booleans; exact equality
throughout.  No pytest.approx (no floats in outputs).

Provenance: fixture 'expected' fields record direction + bounds derived from
PHASE3_DECISIONS.md; no value was captured from a running implementation.
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

import math_engine

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "math"
    / "regime_exit_adjustment"
)


def _load_fixtures() -> list[tuple[str, dict]]:
    paths = sorted(FIXTURE_DIR.glob("*.json"))
    out: list[tuple[str, dict]] = []
    for p in paths:
        with p.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        out.append((p.name, data))
    return out


FIXTURES = _load_fixtures()


# ---------------------------------------------------------------------------
# Helper: call apply_regime_exit_adjustment from a fixture dict
# ---------------------------------------------------------------------------


def _call_from_fixture(fixture: dict) -> int:
    inputs = fixture["inputs"]
    return math_engine.apply_regime_exit_adjustment(
        regime_label=inputs["regime_label"],
        base_ticks=inputs["base_ticks"],
    )


# ---------------------------------------------------------------------------
# 1. Golden-fixture direction + bound tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture_name,fixture",
    FIXTURES,
    ids=[name for name, _ in FIXTURES],
)
def test_regime_adjustment_fixture_direction_and_bounds(
    fixture_name: str, fixture: dict
) -> None:
    """
    For each golden fixture, apply_regime_exit_adjustment(regime_label, base_ticks)
    must satisfy:
      - 'unknown_regime' fixtures: adjusted_ticks == base_ticks (exact integer)
      - 'trending' fixture: adjusted_ticks < base_ticks AND adjusted_ticks >= 1
      - 'mean_reverting' fixture: adjusted_ticks > base_ticks
      - 'high_vol' fixture: 1 <= adjusted_ticks <= base_ticks

    Direction contracts are derived from PHASE3_DECISIONS.md / Kaminski-Lo (2014).
    No specific implementer-produced integer is asserted (the exact constant lives
    in math_engine.py as a named constant per project rule).
    """
    assert fixture["function"] == "apply_regime_exit_adjustment", (
        f"{fixture_name}: unexpected function field {fixture['function']!r}"
    )

    result = _call_from_fixture(fixture)
    base_ticks = fixture["inputs"]["base_ticks"]
    expected = fixture["expected"]

    assert isinstance(result, int), (
        f"{fixture_name}: apply_regime_exit_adjustment must return int, "
        f"got {type(result).__name__}"
    )

    direction = expected.get("direction")

    if "adjusted_ticks" in expected:
        # Safe-default fixtures pin exact value (None / unrecognized -> base_ticks)
        assert result == expected["adjusted_ticks"], (
            f"{fixture_name}: expected adjusted_ticks={expected['adjusted_ticks']}, "
            f"got {result} (safe-default path: unknown regime must not adjust)"
        )
    elif direction == "strictly_less_than_base":
        assert result < base_ticks, (
            f"{fixture_name}: TRENDING direction violated — "
            f"adjusted_ticks must be < base_ticks={base_ticks}, got {result}"
        )
        assert result >= expected.get("lower_bound", 1), (
            f"{fixture_name}: adjusted_ticks={result} below lower_bound "
            f"{expected.get('lower_bound', 1)} — TRENDING adjustment cannot "
            f"collapse the stop to zero confirmation ticks"
        )
    elif direction == "strictly_greater_than_base":
        assert result > base_ticks, (
            f"{fixture_name}: MEAN-REVERTING RESTRAINT direction violated — "
            f"adjusted_ticks must be > base_ticks={base_ticks}, got {result}. "
            f"Mean-reverting exits LESS (Kaminski-Lo 2014: returns reverse, "
            f"stops neutral-to-harmful)."
        )
    elif direction == "at_or_below_base":
        lower = expected.get("lower_bound", 1)
        upper = expected.get("upper_bound_inclusive", base_ticks)
        assert lower <= result <= upper, (
            f"{fixture_name}: HIGH-VOL bounded-protective contract violated — "
            f"adjusted_ticks must be in [{lower}, {upper}], got {result}. "
            f"HIGH-VOL must stay protective (not loosened like mean-reverting) "
            f"and bounded above 0."
        )
    else:
        pytest.fail(
            f"{fixture_name}: fixture 'expected' has no recognised direction or "
            f"'adjusted_ticks' field: {expected!r}"
        )


# ---------------------------------------------------------------------------
# 2. Named-constant existence (project rule: no magic numbers in math_engine.py)
# ---------------------------------------------------------------------------


def test_trending_exit_ticks_named_constant_exists() -> None:
    """
    Project rule: no magic numbers — every constant named + source comment.
    The per-regime tick values must be module-level named constants, not
    inline literals inside apply_regime_exit_adjustment.
    """
    assert hasattr(math_engine, "TRENDING_EXIT_TICKS"), (
        "math_engine.TRENDING_EXIT_TICKS not found. The TRENDING per-regime "
        "tick adjustment must be a named module-level constant with a source "
        "comment (PHASE3_DECISIONS.md §Regime->response; Kaminski-Lo 2014)."
    )
    val = math_engine.TRENDING_EXIT_TICKS
    assert isinstance(val, int), (
        f"TRENDING_EXIT_TICKS must be int, got {type(val).__name__}"
    )
    # Direction: trending -> more-active -> fewer ticks than the baseline
    assert val < math_engine.EXIT_CONFIRM_TICKS, (
        f"TRENDING_EXIT_TICKS={val} must be < EXIT_CONFIRM_TICKS="
        f"{math_engine.EXIT_CONFIRM_TICKS} (more-active direction)"
    )
    assert val >= 1, (
        f"TRENDING_EXIT_TICKS={val} must be >= 1 "
        f"(cannot confirm on 0 ticks — safety lower bound)"
    )


def test_mean_reverting_exit_ticks_named_constant_exists() -> None:
    """
    MEAN_REVERTING_EXIT_TICKS must be > EXIT_CONFIRM_TICKS (restraint direction).
    """
    assert hasattr(math_engine, "MEAN_REVERTING_EXIT_TICKS"), (
        "math_engine.MEAN_REVERTING_EXIT_TICKS not found. The MEAN-REVERTING "
        "per-regime tick adjustment must be a named module-level constant "
        "(RESTRAINT direction: more ticks = harder to confirm = exits less)."
    )
    val = math_engine.MEAN_REVERTING_EXIT_TICKS
    assert isinstance(val, int), (
        f"MEAN_REVERTING_EXIT_TICKS must be int, got {type(val).__name__}"
    )
    assert val > math_engine.EXIT_CONFIRM_TICKS, (
        f"MEAN_REVERTING_EXIT_TICKS={val} must be > EXIT_CONFIRM_TICKS="
        f"{math_engine.EXIT_CONFIRM_TICKS} (RESTRAINT: exits less "
        f"in mean-reverting regime per Kaminski-Lo 2014)"
    )


def test_high_vol_exit_ticks_named_constant_exists() -> None:
    """
    HIGH_VOL_EXIT_TICKS must be in [1, EXIT_CONFIRM_TICKS] (bounded-protective).
    It must NOT equal MEAN_REVERTING_EXIT_TICKS (which would loosen the stop
    rather than keep it protective).
    """
    assert hasattr(math_engine, "HIGH_VOL_EXIT_TICKS"), (
        "math_engine.HIGH_VOL_EXIT_TICKS not found. The HIGH-VOL per-regime "
        "tick adjustment must be a named module-level constant "
        "(bounded-protective: at or below base EXIT_CONFIRM_TICKS, >= 1)."
    )
    val = math_engine.HIGH_VOL_EXIT_TICKS
    assert isinstance(val, int), (
        f"HIGH_VOL_EXIT_TICKS must be int, got {type(val).__name__}"
    )
    assert 1 <= val <= math_engine.EXIT_CONFIRM_TICKS, (
        f"HIGH_VOL_EXIT_TICKS={val} must be in [1, EXIT_CONFIRM_TICKS="
        f"{math_engine.EXIT_CONFIRM_TICKS}] (bounded-protective: not loosened "
        f"to restraint territory, not collapsed to 0)"
    )
    assert val != math_engine.MEAN_REVERTING_EXIT_TICKS, (
        f"HIGH_VOL_EXIT_TICKS={val} must NOT equal MEAN_REVERTING_EXIT_TICKS="
        f"{math_engine.MEAN_REVERTING_EXIT_TICKS}: HIGH-VOL is protective, "
        f"not restraint"
    )


def test_regime_ticks_bounds_constants_exist() -> None:
    """
    REGIME_TICKS_LOWER_BOUND and REGIME_TICKS_UPPER_BOUND must exist as named
    constants.  These make the safety envelope explicit and auditable.
    Hard constraint 2 (BOUNDED): adjustments are bounded fixed theory-set constants.
    """
    assert hasattr(math_engine, "REGIME_TICKS_LOWER_BOUND"), (
        "math_engine.REGIME_TICKS_LOWER_BOUND not found. The safety lower "
        "bound for regime-adjusted ticks must be a named constant."
    )
    assert hasattr(math_engine, "REGIME_TICKS_UPPER_BOUND"), (
        "math_engine.REGIME_TICKS_UPPER_BOUND not found. The safety upper "
        "bound for regime-adjusted ticks must be a named constant."
    )
    lower = math_engine.REGIME_TICKS_LOWER_BOUND
    upper = math_engine.REGIME_TICKS_UPPER_BOUND
    assert isinstance(lower, int) and lower >= 1, (
        f"REGIME_TICKS_LOWER_BOUND must be int >= 1, got {lower!r}"
    )
    assert isinstance(upper, int) and upper >= math_engine.EXIT_CONFIRM_TICKS, (
        f"REGIME_TICKS_UPPER_BOUND={upper} must be int >= EXIT_CONFIRM_TICKS="
        f"{math_engine.EXIT_CONFIRM_TICKS}"
    )


# ---------------------------------------------------------------------------
# 3. Function exists and returns int
# ---------------------------------------------------------------------------


def test_apply_regime_exit_adjustment_function_exists() -> None:
    """
    The function must exist as a callable on math_engine.
    The function does not exist yet — this test is RED by construction.
    """
    assert hasattr(math_engine, "apply_regime_exit_adjustment"), (
        "math_engine.apply_regime_exit_adjustment not found. "
        "Implement the pure function per team brief."
    )
    assert callable(math_engine.apply_regime_exit_adjustment), (
        "math_engine.apply_regime_exit_adjustment is not callable"
    )


def test_return_type_is_int() -> None:
    """
    apply_regime_exit_adjustment must return exactly int (not numpy int, not
    float).  The result feeds compute_exit_confirmation's EXIT_CONFIRM_TICKS
    threshold comparison — non-int types would be a category error.
    """
    result = math_engine.apply_regime_exit_adjustment(
        regime_label=None, base_ticks=3
    )
    assert type(result) is int, (
        f"apply_regime_exit_adjustment must return EXACTLY int, got "
        f"{type(result).__name__}. numpy int subtypes or floats are forbidden; "
        f"the result feeds an integer comparison (new_count >= adjusted_ticks)."
    )


# ---------------------------------------------------------------------------
# 4. Safe-default property: None and all unrecognized labels -> base_ticks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "regime_label",
    [None, "sideways", "unknown", "", "TRENDING", "Mean-Reverting", "HIGH_VOL", "unknown-regime"],
)
def test_safe_default_unknown_labels_return_base_ticks(
    regime_label: str | None,
) -> None:
    """
    Hard constraint 4: regime absent/unknown/stale -> current behavior (no adjustment).

    None and any unrecognized string must return base_ticks unchanged.
    This is not just a nice-to-have: the execution path cannot distinguish
    a stale label from an unknown label in the general case.  Any label
    outside the recognized set must be treated as absent.

    Case sensitivity: 'TRENDING' (uppercase) must NOT be recognized —
    the classifier produces lowercase labels ('trending', 'mean-reverting',
    'high-vol').  An impl that does case-insensitive matching would silently
    widen the recognized set beyond the three production labels, violating
    the principle of least surprise.
    """
    base_ticks = 3
    result = math_engine.apply_regime_exit_adjustment(
        regime_label=regime_label, base_ticks=base_ticks
    )
    assert result == base_ticks, (
        f"apply_regime_exit_adjustment(regime_label={regime_label!r}, "
        f"base_ticks={base_ticks}) must return {base_ticks} (safe default "
        f"— unknown/absent label must not adjust). Got {result}."
    )


# ---------------------------------------------------------------------------
# 5. Bounds property: no regime can push adjusted_ticks outside safe envelope
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "regime_label",
    ["trending", "mean-reverting", "high-vol", None, "sideways"],
)
@pytest.mark.parametrize("base_ticks", [1, 2, 3, 4, 5, 8])
def test_adjusted_ticks_always_within_safety_bounds(
    regime_label: str | None, base_ticks: int
) -> None:
    """
    Hard constraint 2: adjustments are bounded fixed theory-set constants.

    For ANY recognized or unrecognized regime label, the result must be in
    [REGIME_TICKS_LOWER_BOUND, REGIME_TICKS_UPPER_BOUND].  This is the
    'no regime can push the lever past safe bounds' invariant from the
    team brief.

    Catches an impl that:
      - Returns 0 for TRENDING (would arm a zero-confirmation stop that fires
        on the very first tick below the stop level — instant exit on noise)
      - Returns a huge value for MEAN-REVERTING that makes the stop
        practically unarmed (e.g. 100 ticks before confirmation)
    """
    lower = math_engine.REGIME_TICKS_LOWER_BOUND
    upper = math_engine.REGIME_TICKS_UPPER_BOUND
    result = math_engine.apply_regime_exit_adjustment(
        regime_label=regime_label, base_ticks=base_ticks
    )
    assert lower <= result <= upper, (
        f"apply_regime_exit_adjustment(regime_label={regime_label!r}, "
        f"base_ticks={base_ticks}) returned {result}, which is outside the "
        f"safety envelope [{lower}, {upper}]. Hard constraint 2 violated: "
        f"no regime can push ticks outside the bounded constants."
    )


# ---------------------------------------------------------------------------
# 6. Monotonicity: mean-reverting always > trending (direction order preserved)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("base_ticks", [1, 2, 3, 4, 5, 8])
def test_mean_reverting_always_more_ticks_than_trending(base_ticks: int) -> None:
    """
    Direction invariant (Kaminski-Lo 2014): the restraint order must hold for
    every base_ticks value:

        apply_regime_exit_adjustment('mean-reverting', base_ticks)
        > apply_regime_exit_adjustment('trending', base_ticks)

    Catches an impl that accidentally inverts the table, or that clamps both
    values to the same constant regardless of regime.
    """
    mr = math_engine.apply_regime_exit_adjustment(
        regime_label="mean-reverting", base_ticks=base_ticks
    )
    tr = math_engine.apply_regime_exit_adjustment(
        regime_label="trending", base_ticks=base_ticks
    )
    assert mr > tr, (
        f"Direction invariant violated for base_ticks={base_ticks}: "
        f"mean-reverting adjusted_ticks={mr} must be STRICTLY GREATER THAN "
        f"trending adjusted_ticks={tr}. "
        f"Kaminski-Lo (2014): mean-reverting -> restraint, trending -> more-active."
    )


# ---------------------------------------------------------------------------
# 7. Fail-safe composition: MC-None path still works under all regime labels
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "regime_label",
    ["trending", "mean-reverting", "high-vol", None],
)
def test_fail_safe_composes_with_regime_adjustment(regime_label: str | None) -> None:
    """
    Hard constraint 3: H-3 fail-open arming + MC-None fail-safe must still work
    under EACH regime.

    This test simulates the execution path:
      1. Apply regime adjustment to get adjusted_ticks.
      2. Call compute_exit_confirmation with prob_beating=None (MC absent, fail-safe).
      3. Assert the function still fires after adjusted_ticks consecutive confirmations.

    The MC-None path MUST NOT be disabled by the regime adjustment.  A mean-
    reverting regime that raises ticks to 5 still fires after 5 qualifying ticks
    with prob_beating=None.

    Specifically: starting_count set to (adjusted_ticks - 1) so the next call
    should flip is_trailing_stop_hit to True.
    """
    base_ticks = math_engine.EXIT_CONFIRM_TICKS
    adjusted_ticks = math_engine.apply_regime_exit_adjustment(
        regime_label=regime_label, base_ticks=base_ticks
    )

    # Build a state that is one tick below the threshold
    starting_count = adjusted_ticks - 1

    # Use a stop condition that clearly passes: return well below stop, MC absent
    _, hit = math_engine.compute_exit_confirmation(
        armed=True,
        is_triggered=False,
        current_return=-10.0,          # deep below any stop
        stop_trigger_level=-1.0,       # stop at -1.0 -> threshold -1.10
        prob_beating=None,             # MC absent (fail-safe path)
        current_below_stop_count=starting_count,
        exit_confirm_ticks=adjusted_ticks,  # regime-adjusted ticks parameter
    )

    assert hit is True, (
        f"Fail-safe composition broken for regime_label={regime_label!r}: "
        f"compute_exit_confirmation should fire (is_trailing_stop_hit=True) "
        f"after {adjusted_ticks} qualifying ticks with MC absent (prob_beating=None). "
        f"Starting at count={starting_count} (one below threshold), next tick "
        f"should produce count={adjusted_ticks} >= {adjusted_ticks} -> hit=True. "
        f"MC-None fail-safe must not be disabled by the regime adjustment."
    )


@pytest.mark.parametrize(
    "regime_label",
    ["trending", "mean-reverting", "high-vol", None],
)
def test_fail_safe_mc_absent_does_not_veto_stop_under_any_regime(
    regime_label: str | None,
) -> None:
    """
    Inverse of the fail-safe composition test: with MC absent (prob_beating=None),
    the protective stop is NOT vetoed by any regime.

    Checks that compute_exit_confirmation with exit_confirm_ticks=adjusted_ticks
    and sufficient starting_count does not return (_, False) when the fail-safe
    should fire.  Specifically tests with starting_count already AT adjusted_ticks - 1
    (final qualifying tick).
    """
    adjusted_ticks = math_engine.apply_regime_exit_adjustment(
        regime_label=regime_label,
        base_ticks=math_engine.EXIT_CONFIRM_TICKS,
    )
    # One tick away from threshold; the next qualifying tick should fire
    starting_count = adjusted_ticks - 1

    new_count, hit = math_engine.compute_exit_confirmation(
        armed=True,
        is_triggered=False,
        current_return=-5.0,
        stop_trigger_level=-1.0,
        prob_beating=None,             # MC absent — fail-safe must not block stop
        current_below_stop_count=starting_count,
        exit_confirm_ticks=adjusted_ticks,
    )

    assert new_count == adjusted_ticks, (
        f"Expected new_count={adjusted_ticks} (base {starting_count} + 1), "
        f"got {new_count} for regime_label={regime_label!r}"
    )
    assert hit is True, (
        f"MC-absent fail-safe vetoed by regime adjustment for "
        f"regime_label={regime_label!r}: hit must be True when "
        f"new_count={new_count} >= adjusted_ticks={adjusted_ticks} and "
        f"prob_beating=None. A regime adjustment must NOT disable the "
        f"protective stop."
    )


# ---------------------------------------------------------------------------
# 8. Purity: pure function, no I/O, no side effects
# ---------------------------------------------------------------------------


def test_apply_regime_exit_adjustment_is_pure() -> None:
    """
    apply_regime_exit_adjustment must be a pure function: identical inputs
    must always yield identical outputs.  Catches an impl that reads from
    a module-level mutable counter or calls time.time().
    """
    args = {"regime_label": "trending", "base_ticks": 3}
    a = math_engine.apply_regime_exit_adjustment(**args)
    b = math_engine.apply_regime_exit_adjustment(**args)
    assert a == b, (
        f"Non-deterministic: apply_regime_exit_adjustment({args}) returned "
        f"{a} then {b} on successive calls. Function must be pure."
    )


# ---------------------------------------------------------------------------
# 9. No magic numbers in apply_regime_exit_adjustment body (AST scanner)
# ---------------------------------------------------------------------------


def test_no_magic_numbers_in_adjustment_function_body() -> None:
    """
    Project rule: 'No magic numbers in math_engine.py — every constant named
    + source comment.'

    Scans the AST of apply_regime_exit_adjustment for numeric literals.
    Domain per-regime tick values (TRENDING_EXIT_TICKS, MEAN_REVERTING_EXIT_TICKS,
    HIGH_VOL_EXIT_TICKS, REGIME_TICKS_LOWER_BOUND, REGIME_TICKS_UPPER_BOUND)
    must be referenced by Name, not inlined as bare integers.

    Structural zero, one, and negative-one are whitelisted.
    """
    src_path = pathlib.Path(math_engine.__file__)
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    target: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "apply_regime_exit_adjustment"
        ):
            target = node
            break

    assert target is not None, (
        "apply_regime_exit_adjustment not found in math_engine.py "
        "(RED state — function not yet implemented)"
    )

    STRUCTURAL_INTS = {0, 1, -1}

    offenders: list[tuple[int, int | float]] = []
    for sub in ast.walk(target):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, (int, float)):
            if isinstance(sub.value, bool):
                continue
            if sub.value in STRUCTURAL_INTS:
                continue
            offenders.append((sub.lineno, sub.value))

    assert not offenders, (
        "Unnamed magic numbers in apply_regime_exit_adjustment "
        f"(project rule: all per-regime tick values must be named constants). "
        f"Offenders (line, value): {offenders}. Reference "
        "TRENDING_EXIT_TICKS, MEAN_REVERTING_EXIT_TICKS, HIGH_VOL_EXIT_TICKS, "
        "REGIME_TICKS_LOWER_BOUND, REGIME_TICKS_UPPER_BOUND by name."
    )


# ---------------------------------------------------------------------------
# 10. Docstring on apply_regime_exit_adjustment references named sources
# ---------------------------------------------------------------------------


def test_adjustment_function_docstring_cites_kaminski_lo() -> None:
    """
    Project documentation standard: every public function gets a docstring
    (what + why).  The 'why' for regime direction must cite the theory source
    (Kaminski-Lo 2014) so the adjustment table is auditable and the user can
    translate it to a plain-English before/after.

    This is a weak test (string presence), not a semantic one — it ensures
    the implementer writes the citation rather than leaving it undocumented.
    """
    fn = getattr(math_engine, "apply_regime_exit_adjustment", None)
    assert fn is not None, (
        "apply_regime_exit_adjustment not found in math_engine "
        "(RED state — not yet implemented)"
    )
    doc = fn.__doc__ or ""
    assert "Kaminski" in doc or "kaminski" in doc.lower(), (
        "apply_regime_exit_adjustment docstring must cite the theory source "
        "'Kaminski' (Kaminski-Lo 2014) that underpins the direction of each "
        "per-regime adjustment. This makes the adjustment table auditable."
    )


# ---------------------------------------------------------------------------
# 11. compute_exit_confirmation accepts optional exit_confirm_ticks parameter
# ---------------------------------------------------------------------------


def test_exit_confirmation_accepts_exit_confirm_ticks_kwarg() -> None:
    """
    Hard constraint 3 (compose with fail-safes): the regime-adjusted tick
    count must be passed INTO compute_exit_confirmation so the execution path
    can use a regime-adjusted threshold without changing the module-level
    EXIT_CONFIRM_TICKS constant.

    compute_exit_confirmation must accept an optional 'exit_confirm_ticks'
    keyword argument that overrides EXIT_CONFIRM_TICKS for that call.
    When omitted, EXIT_CONFIRM_TICKS is the default (backwards-compatible).

    This test verifies the parameter is accepted.  The behaviour-with-override
    tests are in test_fail_safe_composes_with_regime_adjustment above.
    """
    import inspect
    sig = inspect.signature(math_engine.compute_exit_confirmation)
    assert "exit_confirm_ticks" in sig.parameters, (
        "compute_exit_confirmation must accept 'exit_confirm_ticks' as a "
        "keyword argument (the regime-adjusted tick count).  When omitted it "
        "defaults to EXIT_CONFIRM_TICKS.  Without this parameter the "
        "execution path cannot pass a regime-adjusted threshold without "
        "monkey-patching the module-level constant."
    )
    # The parameter must have a default so existing callers are unaffected
    param = sig.parameters["exit_confirm_ticks"]
    assert param.default is not inspect.Parameter.empty, (
        "'exit_confirm_ticks' must have a default value (EXIT_CONFIRM_TICKS) "
        "so existing callers that do not pass it remain unaffected. "
        "Backwards compatibility is a hard constraint."
    )


def test_exit_confirmation_exit_confirm_ticks_default_is_module_constant() -> None:
    """
    When exit_confirm_ticks is NOT passed, compute_exit_confirmation must
    behave exactly as before: threshold = EXIT_CONFIRM_TICKS.

    Verifies backwards compatibility by calling without the kwarg and
    checking that count=EXIT_CONFIRM_TICKS-1 does NOT fire but
    count=EXIT_CONFIRM_TICKS-1 DOES fire after one qualifying tick
    (i.e. count increments to EXIT_CONFIRM_TICKS on the qualifying tick
    and fires).
    """
    base = math_engine.EXIT_CONFIRM_TICKS
    # One tick below threshold: should not yet hit
    _, hit_not_yet = math_engine.compute_exit_confirmation(
        armed=True,
        is_triggered=False,
        current_return=-5.0,
        stop_trigger_level=-1.0,
        prob_beating=None,
        current_below_stop_count=base - 2,
        # exit_confirm_ticks intentionally omitted — must default to EXIT_CONFIRM_TICKS
    )
    assert hit_not_yet is False, (
        f"Backwards compat broken: count={base - 2 + 1} < EXIT_CONFIRM_TICKS={base} "
        f"should NOT fire without exit_confirm_ticks kwarg"
    )

    # At threshold: should hit
    _, hit_now = math_engine.compute_exit_confirmation(
        armed=True,
        is_triggered=False,
        current_return=-5.0,
        stop_trigger_level=-1.0,
        prob_beating=None,
        current_below_stop_count=base - 1,
        # exit_confirm_ticks intentionally omitted
    )
    assert hit_now is True, (
        f"Backwards compat broken: count={base} should equal EXIT_CONFIRM_TICKS={base} "
        f"and fire without exit_confirm_ticks kwarg"
    )


def test_exit_confirmation_exit_confirm_ticks_override_works() -> None:
    """
    When exit_confirm_ticks IS passed with a value different from EXIT_CONFIRM_TICKS,
    it must be used as the threshold.

    Example: EXIT_CONFIRM_TICKS=3, override=5.
      - starting_count=3 (override-2) -> count becomes 4 < 5 -> hit=False
      - starting_count=4 (override-1) -> count becomes 5 >= 5 -> hit=True
    """
    base = math_engine.EXIT_CONFIRM_TICKS
    override = base + 2  # e.g. 5 when base=3

    # starting_count = override-2: new_count = override-1 < override -> should NOT fire
    new_count, hit_not_yet = math_engine.compute_exit_confirmation(
        armed=True,
        is_triggered=False,
        current_return=-5.0,
        stop_trigger_level=-1.0,
        prob_beating=None,
        current_below_stop_count=override - 2,
        exit_confirm_ticks=override,
    )
    assert hit_not_yet is False, (
        f"Override threshold={override}: starting_count={override - 2} + 1 "
        f"= {new_count} < {override} -> should NOT fire yet, got hit={hit_not_yet}"
    )
    assert new_count == override - 1, (
        f"Expected new_count={override - 1}, got {new_count}"
    )

    # starting_count = override-1: new_count = override >= override -> MUST fire
    new_count_2, hit_now = math_engine.compute_exit_confirmation(
        armed=True,
        is_triggered=False,
        current_return=-5.0,
        stop_trigger_level=-1.0,
        prob_beating=None,
        current_below_stop_count=override - 1,
        exit_confirm_ticks=override,
    )
    assert new_count_2 == override, (
        f"Expected new_count={override}, got {new_count_2}"
    )
    assert hit_now is True, (
        f"Override threshold={override}: new_count={new_count_2} >= {override} "
        f"-> should fire, got hit={hit_now}"
    )
