"""
Cycle C of task #24 — BEHAVIORAL regression pins for the calibration fix.

Context (chain to pin):
  - Cycle A (merge 8c063de): synthetic_history.py now emits
    ``valid_vwap_weight`` (= local ``valid_alloc``) per tick + bumps the
    on-disk cache key to v2.
  - Cycle B (merge 0cb7651): autotuner.py swapped its inline VWAP state
    machine for a call to math_engine.compute_vwap_breakdown_update,
    threading ``tick.get("valid_vwap_weight", 1.0)`` into the kwarg.
  - The fix intentionally changes sim behavior on the low-coverage path:
    when ``valid_vwap_weight <= VWAP_WEIGHT_THRESHOLD`` (0.5), VWAP signals
    are now SUPPRESSED in sim to match live.

Cycle B's tests are STRUCTURAL only (AST scan of autotuner.py). This file
adds the BEHAVIORAL pin for the calibration outcome by:

  1. Exercising math_engine.compute_vwap_breakdown_update directly with
     low-coverage inputs that the autotuner now passes through, and
     asserting the (0, 0, False, False) gate-fail tuple — proving the
     calibration semantics live in the canonical function (the same
     function Cycle B wired the autotuner into).

  2. Documenting the SEMANTIC ASYMMETRY between
     ``synthetic_history.py`` (increments ``valid_alloc`` on bar-existence
     only) and ``math_engine.compute_vwap_signals`` (increments
     ``valid_vwap_weight`` on bar-existence AND ``v > 0``) — as a STATIC
     AST regression catch. If a future contributor "fixes" one side without
     the other, the asymmetry would vanish silently and the assumption
     "for IEX-sourced bars, c > 0 implies v > 0" would become load-bearing
     elsewhere with no record. The test pins the gap and explains its
     rationale.

  3. Pinning the ``tick.get("valid_vwap_weight", 1.0)`` fallback in
     autotuner.py via static AST — guarantees that a stale v1 cache that
     slips through the version marker still produces sensible behavior
     (the default of 1.0 makes the gate PASS, which preserves pre-fix
     behavior for stale caches; a default of 0.0 would silently suppress
     all VWAP signals on stale caches).

HARD RULES followed:
  - Pure pytest (stdlib + math_engine import only).
  - ADDITIVE-only: no existing tests modified.
  - No live API, no autotuner execution.
  - No production-file edits.
  - Behavioral pins assert exact tuple equality (no floats in the function
    output, so pytest.approx is not used and is not appropriate here).

Semantic-asymmetry pin approach: STATIC (AST). Reasoning: an
"unreachable-claim" assertion would require live IEX data to verify, which
is forbidden in the default pytest run. The static pin documents the gap
in the test record and fires loudly if either side is "harmonized" without
the matching change on the other.

Calibration-fix behavioral pin approach: APPROACH A — direct call to
math_engine.compute_vwap_breakdown_update with the same low-coverage
inputs that Cycle B's structural tests prove autotuner now threads through.
Combined with Cycle B's structural pin (autotuner CALLS this function with
valid_vwap_weight from the tick), the behavioral chain is complete.
Approach B (running autotuner.run_autotuner end-to-end with a monkey-
patched canonical function) would require Optuna + parallelization setup
costs disproportionate to the marginal coverage gain.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import math_engine

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SYNTH_PATH = _REPO_ROOT / "synthetic_history.py"
_AUTOTUNER_PATH = _REPO_ROOT / "autotuner.py"
_MATH_ENGINE_PATH = _REPO_ROOT / "math_engine.py"


# ---------------------------------------------------------------------------
# Section 1 — BEHAVIORAL: calibration-fix outcome on low-coverage inputs
# ---------------------------------------------------------------------------
#
# These are the inputs Cycle B routes through math_engine.compute_vwap_-
# breakdown_update from inside autotuner.run_simulation. Each parametrization
# represents a low-coverage tick scenario (valid_vwap_weight at or below the
# 0.5 threshold) where the canonical gate MUST fire and suppress signals.
#
# We deliberately use inputs that — but for the calibration gate — WOULD
# trip both System A break and System B break. The fact that they cannot
# fire because the gate vetoes is the calibration outcome we are pinning.
# A naive impl that dropped the gate (e.g. by removing the `> 0.5` check
# under the misapprehension that "live coverage is always full") would let
# both inner systems fire and break this test.


@pytest.mark.parametrize(
    "valid_vwap_weight,scenario_description",
    [
        # 0.3: well below threshold. Typical mid-day partial-fill scenario
        # where only ~30% of the portfolio's holdings have valid intraday
        # bars (e.g. thinly-traded names with no v>0 bar yet). The user's
        # cycle prompt names this specific value.
        (0.3, "30 percent coverage — well below 0.5 threshold"),
        # 0.5: EXACTLY at threshold. The gate uses strict `>`, so 0.5
        # exclusive boundary. Catches an impl that "helpfully" used `>=`.
        (0.5, "50 percent coverage — exactly at threshold (strict greater-than)"),
        # 0.499: just below. Catches an impl that floor-rounds or compares
        # against `int(0.5)`.
        (0.499, "49.9 percent coverage — just below threshold"),
        # 0.0: full discard. No coverage at all.
        (0.0, "0 percent coverage — zero valid bars"),
        # 0.1: extreme low. Single-name partial fill.
        (0.1, "10 percent coverage — single-name partial fill"),
    ],
)
def test_low_coverage_tick_suppresses_vwap_signals_in_canonical_function(
    valid_vwap_weight: float,
    scenario_description: str,
) -> None:
    """
    BEHAVIORAL PIN for the calibration fix from Cycles A + B.

    When the autotuner threads a low-coverage tick into
    math_engine.compute_vwap_breakdown_update, the canonical function MUST
    return (0, 0, False, False) — both counters reset, both broken flags
    False — regardless of how violent the inner-system signals would be.

    Inputs deliberately chosen so that, but for the gate:
      - vwap_diff = -0.001 (just barely below zero; minimum to satisfy the
        diff < 0 half of the gate IF coverage were high)
      - safe_hwm = 10.0, current_return = -50.0, vwap_cross = 1.0 -> System
        A condition (hwm >= cross AND ret < hwm) is FAR met; would
        increment new_a from 2 -> 3 and fire is_vwap_broken=True
      - bleed_arm = -1.0, current_return = -50.0, threshold = 3 -> System B
        condition (ret <= arm) is FAR met; would increment new_b from 2 ->
        3 and fire is_vwap_bleed_broken=True
      - prev counters at 2 (one increment away from the break threshold)
        to maximize the cost of a false positive

    A regression that removed the gate (e.g. by always passing
    valid_vwap_weight=1.0 because "live is always full coverage") would
    return (3, 3, True, True) and double-break this test.

    If Cycle B is reverted (autotuner stops calling this function), Cycle
    B's structural tests fail. This test stays GREEN against the canonical
    function because the function itself is unchanged. The chain is:
    Cycle-B-structural (function called with right args) AND this-test
    (function does the right thing on those args).
    """
    result = math_engine.compute_vwap_breakdown_update(
        is_triggered=False,
        valid_vwap_weight=valid_vwap_weight,
        weighted_vwap_diff=-0.001,
        safe_hwm=10.0,
        current_return=-50.0,
        vwap_cross_hwm_pct=1.0,
        vwap_bleed_arm_pct=-1.0,
        vwap_bleed_ticks_threshold=3,
        current_vwap_ticks=2,
        current_vwap_bleed_ticks=2,
    )
    assert result == (0, 0, False, False), (
        f"CALIBRATION REGRESSION ({scenario_description}): "
        f"valid_vwap_weight={valid_vwap_weight} must SUPPRESS VWAP signals "
        f"(return (0, 0, False, False)). Got {result}. "
        "The gate `valid_vwap_weight > VWAP_WEIGHT_THRESHOLD AND "
        "weighted_vwap_diff < 0` must veto inner-system firing on "
        "low-coverage ticks — this is the live-sim parity fix from Cycle A "
        "+ Cycle B of task #24."
    )


def test_low_coverage_exact_boundary_value_resets_high_prev_counters() -> None:
    """
    BEHAVIORAL PIN, sharper variant: even when prev counters are HIGH
    (already incremented many ticks ago on a high-coverage stretch), a
    single low-coverage tick MUST wipe them to 0. The autotuner's prior
    inline implementation had this behavior; the canonical function
    preserves it; this test pins the preservation post-Cycle-B.

    Inputs: high prev counters (10 each), low coverage (0.3), but inner-
    system conditions also met. Expected: full wipe to 0.
    """
    new_a, new_b, broken, bleed = math_engine.compute_vwap_breakdown_update(
        is_triggered=False,
        valid_vwap_weight=0.3,
        weighted_vwap_diff=-2.0,
        safe_hwm=5.0,
        current_return=-3.0,
        vwap_cross_hwm_pct=2.0,
        vwap_bleed_arm_pct=-1.0,
        vwap_bleed_ticks_threshold=3,
        current_vwap_ticks=10,
        current_vwap_bleed_ticks=10,
    )
    assert (new_a, new_b, broken, bleed) == (0, 0, False, False), (
        "CALIBRATION REGRESSION: high prev counters (10, 10) must be wiped "
        f"to (0, 0) by a low-coverage tick. Got ({new_a}, {new_b}, "
        f"{broken}, {bleed}). Partial decrement is forbidden by the "
        "producer's `else: bot_state[...] = 0` reset semantics."
    )


def test_high_coverage_tick_allows_canonical_break_through() -> None:
    """
    DUAL-CONTROL companion to the low-coverage suppression pin: a tick
    with valid_vwap_weight ABOVE the threshold (0.9) must NOT be gated —
    the canonical function must let System A increment normally. This
    catches an over-aggressive impl that gated EVERY tick (the opposite
    over-correction).

    Inputs: high coverage (0.9), diff just below zero, A-condition met,
    prev_a=2 -> new_a=3 -> is_vwap_broken=True. B is set to fail (ret > arm)
    so we isolate the A signal.
    """
    new_a, _, broken, _ = math_engine.compute_vwap_breakdown_update(
        is_triggered=False,
        valid_vwap_weight=0.9,
        weighted_vwap_diff=-0.001,
        safe_hwm=4.0,
        current_return=1.0,
        vwap_cross_hwm_pct=2.0,
        vwap_bleed_arm_pct=-10.0,  # ret 1.0 > -10.0, B fails
        vwap_bleed_ticks_threshold=3,
        current_vwap_ticks=2,
        current_vwap_bleed_ticks=0,
    )
    assert new_a == 3, (
        f"Over-gating regression: high coverage (0.9) tick must allow "
        f"System A increment from 2 -> 3, got {new_a}. The calibration "
        "fix MUST NOT suppress signals when coverage is above threshold."
    )
    assert broken is True, (
        "Over-gating regression: high coverage tick at new_a=3 must "
        f"flip is_vwap_broken=True, got {broken}."
    )


# ---------------------------------------------------------------------------
# Section 2 — STATIC PIN: producer-vs-canonical semantic asymmetry
# ---------------------------------------------------------------------------
#
# The asymmetry (surfaced by the Cycle A reviewer):
#
#   synthetic_history.py (cycle A):
#       for each holding's bar:
#           if v > 0:
#               weighted_vwap_diff += alloc * ((c - v) / v)
#           valid_alloc += alloc                   # <- counted on bar-existence ALONE
#
#   math_engine.compute_vwap_signals (live path):
#       for each holding in live_vwaps:
#           if v > 0:
#               weighted_vwap_diff += allocation * ((p - v) / v)
#               valid_vwap_weight += allocation   # <- counted ONLY when v > 0
#
# In sim, ``valid_alloc`` overcounts vs. live by holdings whose bar exists
# but has v == 0. For IEX-sourced bars (cycle 25 feed pinning), a bar with
# ``c > 0`` essentially always also has ``v > 0``, so the practical-impact
# difference is microscopic. But "essentially always" is not "always" —
# the asymmetry is real in the source code and would matter on any feed
# that emits ``c > 0, v == 0`` bars (some non-IEX SIP corner cases).
#
# We CANNOT prove the asymmetry is empirically unreachable on IEX without
# live data (forbidden in default pytest). Instead, we PIN THE GAP via
# static AST and document the rationale in the test docstring. If a future
# contributor "fixes" the producer to mirror the canonical function (or
# vice versa) WITHOUT acknowledging the asymmetry, this test fires and
# forces the contributor to confront the decision in the test record.


def _parse_synth() -> ast.Module:
    return ast.parse(_SYNTH_PATH.read_text(encoding="utf-8"), filename=str(_SYNTH_PATH))


def _parse_math_engine() -> ast.Module:
    return ast.parse(_MATH_ENGINE_PATH.read_text(encoding="utf-8"), filename=str(_MATH_ENGINE_PATH))


def _find_function(tree: ast.Module, name: str) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _augassign_lineno_of_name_inside_node(
    node: ast.AST, target_name: str
) -> int | None:
    """Find the first AugAssign on ``target_name`` inside ``node``."""
    for sub in ast.walk(node):
        if (
            isinstance(sub, ast.AugAssign)
            and isinstance(sub.target, ast.Name)
            and sub.target.id == target_name
        ):
            return sub.lineno
    return None


def _enclosing_if_test_at_or_above(
    node: ast.AST, target_lineno: int, target_name: str
) -> ast.If | None:
    """
    Walk ``node`` looking for ``If`` statements whose body contains an
    AugAssign on ``target_name`` at exactly ``target_lineno``. Returns the
    nearest enclosing ``If`` (the gate around the AugAssign), or None if
    the AugAssign is not gated.
    """
    candidates: list[ast.If] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.If):
            for inner in ast.walk(sub):
                if (
                    isinstance(inner, ast.AugAssign)
                    and isinstance(inner.target, ast.Name)
                    and inner.target.id == target_name
                    and inner.lineno == target_lineno
                ):
                    candidates.append(sub)
                    break
    if not candidates:
        return None
    # The "innermost" enclosing If is the one whose own lineno is largest
    # but still <= target_lineno.
    eligible = [c for c in candidates if c.lineno <= target_lineno]
    if not eligible:
        return None
    eligible.sort(key=lambda n: n.lineno)
    return eligible[-1]


def _if_test_compares_v_gt_zero(if_node: ast.If) -> bool:
    """Return True iff the If's test is `v > 0` (or `v > 0.0`)."""
    test = if_node.test
    if not isinstance(test, ast.Compare):
        return False
    if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Gt):
        return False
    if not (isinstance(test.left, ast.Name) and test.left.id == "v"):
        return False
    if len(test.comparators) != 1:
        return False
    rhs = test.comparators[0]
    if not isinstance(rhs, ast.Constant):
        return False
    return rhs.value == 0 or rhs.value == 0.0


def test_synthetic_history_valid_alloc_accumulator_is_NOT_gated_by_v_gt_zero() -> None:
    """
    SEMANTIC-ASYMMETRY PIN, producer side.

    synthetic_history.process_day() contains an inner loop over each
    holding's bar. The ``valid_alloc += alloc`` line MUST be OUTSIDE the
    ``if v > 0:`` guard — i.e. accumulated on bar-existence alone. This is
    Cycle-A behavior verbatim and is intentional (the producer's coverage
    accumulator counts bars-with-data, not bars-with-positive-vwap).

    REGRESSION CATCH: if a future contributor moves this line INSIDE the
    ``if v > 0:`` guard ("harmonizing" with math_engine.compute_vwap_-
    signals), the asymmetry vanishes silently — sim becomes slightly more
    conservative on the rare bar where c > 0 but v == 0, which on IEX is
    near-empty practically but DOES change calibration values.
    Contributors who want to harmonize MUST do so explicitly with a paired
    change and a test update; this test forces them to confront the choice.

    Static check: locate the AugAssign on ``valid_alloc`` and assert NO
    enclosing ``if v > 0:`` guard sits between it and the for-loop body.
    """
    tree = _parse_synth()
    # process_day is defined inside generate_synthetic_history; use ast.walk
    process_day = _find_function(tree, "process_day")
    assert process_day is not None, (
        "synthetic_history.process_day not found — the cycle-A producer "
        "function appears to have been removed or renamed. This test "
        "cannot verify the asymmetry."
    )

    aug_lineno = _augassign_lineno_of_name_inside_node(process_day, "valid_alloc")
    assert aug_lineno is not None, (
        "synthetic_history.process_day no longer contains an AugAssign on "
        "`valid_alloc`. The Cycle-A producer accumulator appears to have "
        "been removed or renamed. If the rename was intentional, update "
        "this test to target the new name and re-verify the asymmetry."
    )

    enclosing_if = _enclosing_if_test_at_or_above(process_day, aug_lineno, "valid_alloc")
    if enclosing_if is not None and _if_test_compares_v_gt_zero(enclosing_if):
        pytest.fail(
            "SEMANTIC-ASYMMETRY REGRESSION (producer side): "
            f"synthetic_history.process_day's `valid_alloc += alloc` at line "
            f"{aug_lineno} is now gated by `if v > 0:` at line "
            f"{enclosing_if.lineno}. This 'harmonizes' the producer with "
            "math_engine.compute_vwap_signals (which DOES gate on v > 0). "
            "If intentional, the change must be paired with a Cycle-A "
            "fixture regen + a quant-risk-researcher sign-off because it "
            "shifts sim calibration on bars where c > 0 but v == 0 (rare "
            "on IEX, non-rare on some SIP feeds). Cycle A documented the "
            "asymmetry as intentional and this test pins it."
        )


def test_math_engine_compute_vwap_signals_accumulator_IS_gated_by_v_gt_zero() -> None:
    """
    SEMANTIC-ASYMMETRY PIN, canonical side.

    math_engine.compute_vwap_signals — the live path — gates BOTH
    ``weighted_vwap_diff`` and ``valid_vwap_weight`` accumulators inside
    ``if v > 0:``. This is correct for live: a degenerate VWAP (v <= 0)
    cannot produce a valid (p - v) / v signal, so neither half of the
    weighted signal nor its weight should count that bar.

    REGRESSION CATCH: if a future contributor removes the ``if v > 0:``
    guard from around ``valid_vwap_weight += allocation`` ("harmonizing"
    with the producer), live behavior shifts in the same direction — and
    this test fires.

    Static check: locate the AugAssign on ``valid_vwap_weight`` inside
    compute_vwap_signals; assert it IS enclosed in an ``if v > 0:``.
    """
    tree = _parse_math_engine()
    fn = _find_function(tree, "compute_vwap_signals")
    assert fn is not None, (
        "math_engine.compute_vwap_signals not found. The canonical live "
        "VWAP signal function appears to have been removed or renamed; "
        "the asymmetry pin cannot proceed."
    )

    aug_lineno = _augassign_lineno_of_name_inside_node(fn, "valid_vwap_weight")
    assert aug_lineno is not None, (
        "math_engine.compute_vwap_signals no longer contains an AugAssign "
        "on `valid_vwap_weight`. The canonical accumulator appears to "
        "have been removed or renamed."
    )

    enclosing_if = _enclosing_if_test_at_or_above(fn, aug_lineno, "valid_vwap_weight")
    assert enclosing_if is not None and _if_test_compares_v_gt_zero(enclosing_if), (
        "SEMANTIC-ASYMMETRY REGRESSION (canonical side): "
        f"math_engine.compute_vwap_signals' `valid_vwap_weight += "
        f"allocation` at line {aug_lineno} is no longer gated by "
        "`if v > 0:`. The live path MUST gate the weight accumulator the "
        "same way it gates the diff accumulator — otherwise a degenerate "
        "(v <= 0) bar would contribute weight without contributing a "
        "diff, producing an under-weighted weighted_vwap_diff that "
        "appears to have full coverage."
    )


def test_producer_and_canonical_asymmetry_is_recorded_in_test_docstring() -> None:
    """
    META-PIN: this test exists purely to make sure the asymmetry rationale
    is preserved in the test file's module docstring. A reviewer searching
    `git log -p -- tests/synthetic_history/test_calibration_alignment.py`
    for the phrase 'semantic asymmetry' will find this file.

    The other two static-AST tests pin BEHAVIOR; this one pins the
    DOCUMENTATION pointer — a regression where someone deletes the
    asymmetry-explaining docstring (e.g. via an automated cleanup pass)
    would leave the two AST pins looking arbitrary. Keeping the rationale
    co-located with the pins is part of the regression budget.
    """
    import tests.synthetic_history.test_calibration_alignment as self_mod

    doc = (self_mod.__doc__ or "")
    assert "asymmetry" in doc.lower(), (
        "This test file's module docstring no longer mentions the "
        "producer-vs-canonical 'asymmetry'. The behavioral context for "
        "the two AST pins above lives in the docstring; removing it "
        "leaves the pins looking arbitrary. Restore the explanation."
    )
    assert "valid_alloc" in doc and "valid_vwap_weight" in doc, (
        "Module docstring no longer names both accumulators "
        "(producer `valid_alloc` and canonical `valid_vwap_weight`). "
        "These names are load-bearing for a contributor diagnosing why "
        "the AST pins fired."
    )


# ---------------------------------------------------------------------------
# Section 3 — STATIC PIN: autotuner's tick.get fallback default is 1.0
# ---------------------------------------------------------------------------
#
# Cycle A bumped the cache key to v2 so stale v1 caches cannot be loaded
# under the new schema, but the autotuner still defends against the case
# where a v1 cache somehow slips through (e.g. concurrent write race, or
# a contributor editing the cache filename without bumping the marker).
# The defense is the ``.get(..., 1.0)`` default at autotuner.py:224.
#
# The default of 1.0 makes the gate PASS (1.0 > 0.5) and therefore
# REPRODUCES pre-fix behavior on stale caches. A default of 0.0 would
# silently suppress ALL VWAP signals on stale caches — silently degrading
# the sim without an error. A default of 0.5 would also fail (strict `>`),
# producing the same silent suppression. The test pins the value of the
# default by name + literal.


def _find_compute_vwap_breakdown_calls(tree: ast.Module) -> list[ast.Call]:
    out: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "compute_vwap_breakdown_update"
                and isinstance(func.value, ast.Name)
                and func.value.id == "math_engine"
            ) or isinstance(func, ast.Name) and func.id == "compute_vwap_breakdown_update":
                out.append(node)
    return out


def test_autotuner_valid_vwap_weight_tick_get_default_is_one_point_zero() -> None:
    """
    STALE-CACHE COMPATIBILITY PIN.

    autotuner.py reads ``valid_vwap_weight`` from each tick. If a stale v1
    cache slips through (no ``valid_vwap_weight`` key), the ``.get(..., X)``
    default MUST be 1.0 — not 0.0, not 0.5, not any other value that would
    silently fail the gate ``valid_vwap_weight > 0.5``.

    Rationale for 1.0:
      - 1.0 > 0.5 -> gate PASSES on stale tick -> pre-fix behavior
        reproduced (sim runs as it did before Cycle A). This is the SAFE
        degradation: the user gets the pre-fix calibration on stale
        caches, with no silent suppression of signals.
      - 0.0 (or anything <= 0.5) -> gate FAILS on every stale tick ->
        ALL VWAP signals suppressed -> sim silently de-tunes itself ->
        UNSAFE degradation.

    Static check: find every ``tick.get("valid_vwap_weight", DEFAULT)``
    in autotuner.py and assert DEFAULT == 1.0. Also accept
    ``tick["valid_vwap_weight"]`` (subscript with no default), since that
    would raise KeyError on stale ticks — explicit failure, which is
    SAFER than silent suppression. We do NOT accept subscript-without-
    default as the primary form, but we don't fire if both forms coexist.
    """
    src = _AUTOTUNER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(_AUTOTUNER_PATH))

    # Walk every .get(...) call whose first arg is the literal
    # "valid_vwap_weight". For each, assert the default (second arg) is
    # exactly the float literal 1.0 (also accepting int 1 -- they coerce
    # identically in the float comparison `> 0.5`).
    found_get_calls: list[tuple[int, ast.AST | None]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "get"):
            continue
        if not node.args:
            continue
        first_arg = node.args[0]
        if not (
            isinstance(first_arg, ast.Constant)
            and first_arg.value == "valid_vwap_weight"
        ):
            continue
        # Capture the default arg if present, else None
        default_arg = node.args[1] if len(node.args) >= 2 else None
        found_get_calls.append((node.lineno, default_arg))

    assert found_get_calls, (
        "autotuner.py contains no `tick.get(\"valid_vwap_weight\", ...)` "
        "call. The stale-cache fallback (Cycle A reviewer requirement) "
        "appears to be missing. Without it, a stale v1 cache that slips "
        "through the version marker raises KeyError at runtime. Add a "
        "`.get(\"valid_vwap_weight\", 1.0)` read."
    )

    offenders: list[tuple[int, str]] = []
    for lineno, default in found_get_calls:
        if default is None:
            offenders.append(
                (lineno, "no default — KeyError on stale ticks")
            )
            continue
        if not isinstance(default, ast.Constant):
            offenders.append(
                (
                    lineno,
                    f"default is non-literal expression "
                    f"({ast.dump(default)}); cannot verify safety",
                )
            )
            continue
        if isinstance(default.value, bool):
            # bool is a subclass of int; True == 1 but the semantic intent
            # is wrong.
            offenders.append(
                (lineno, f"default is bool {default.value!r} (semantic miss)")
            )
            continue
        if default.value != 1.0:
            offenders.append(
                (
                    lineno,
                    f"default is {default.value!r}, must be 1.0 to keep "
                    "the stale-cache gate PASSING (any value <= 0.5 "
                    "silently suppresses ALL VWAP signals on stale ticks)",
                )
            )

    assert not offenders, (
        "STALE-CACHE FALLBACK REGRESSION in autotuner.py — "
        "`tick.get(\"valid_vwap_weight\", DEFAULT)` default(s) wrong:\n"
        + "\n".join(f"  - line {ln}: {msg}" for ln, msg in offenders)
        + "\nThe default MUST be 1.0 so a stale v1 cache (no key) "
        "produces the same gate-passes-by-default behavior as pre-Cycle-A "
        "sim. Any default <= 0.5 silently suppresses VWAP signals."
    )


def test_autotuner_canonical_call_threads_through_valid_vwap_weight_from_local() -> None:
    """
    COMPANION PIN to the .get() default test: confirm the value read by
    .get() is actually what flows into the canonical function call. If a
    future refactor accidentally decoupled the two (e.g. reading the tick
    field into a local but then passing a hardcoded 1.0 to the canonical
    function), the stale-cache safety would be cosmetic.

    Static check: at least one call to math_engine.compute_vwap_-
    breakdown_update must have a kwarg ``valid_vwap_weight`` whose value
    is either:
      (a) directly ``tick.get("valid_vwap_weight", 1.0)`` / ``tick[...]``, OR
      (b) a Name bound earlier in the function from a tick read of
          ``valid_vwap_weight``.

    Cycle B already pins this (test_canonical_call_passes_valid_vwap_-
    weight_from_tick). We re-pin from this file's perspective as a
    behavioral-companion documentation pointer — if Cycle B's tests are
    deleted or moved, this one keeps the contract pinned in the
    calibration test file.
    """
    src = _AUTOTUNER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(_AUTOTUNER_PATH))
    calls = _find_compute_vwap_breakdown_calls(tree)
    assert calls, (
        "autotuner.py contains no call to "
        "math_engine.compute_vwap_breakdown_update. Cycle B's canonical "
        "swap appears to have been reverted; the calibration fix is "
        "broken at the call site."
    )

    # Collect aliases: any local Name bound to a tick read of
    # "valid_vwap_weight".
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        v = node.value
        is_tick_read = False
        if (
            isinstance(v, ast.Call)
            and isinstance(v.func, ast.Attribute)
            and v.func.attr == "get"
            and v.args
            and isinstance(v.args[0], ast.Constant)
            and v.args[0].value == "valid_vwap_weight"
        ) or (
            isinstance(v, ast.Subscript)
            and isinstance(v.slice, ast.Constant)
            and v.slice.value == "valid_vwap_weight"
        ):
            is_tick_read = True
        if not is_tick_read:
            continue
        for tgt in node.targets:
            if isinstance(tgt, ast.Name):
                aliases.add(tgt.id)

    threaded = False
    for call in calls:
        for kw in call.keywords:
            if kw.arg != "valid_vwap_weight":
                continue
            val = kw.value
            # Direct tick read at call site
            if (
                isinstance(val, ast.Call)
                and isinstance(val.func, ast.Attribute)
                and val.func.attr == "get"
                and val.args
                and isinstance(val.args[0], ast.Constant)
                and val.args[0].value == "valid_vwap_weight"
            ):
                threaded = True
                break
            if (
                isinstance(val, ast.Subscript)
                and isinstance(val.slice, ast.Constant)
                and val.slice.value == "valid_vwap_weight"
            ):
                threaded = True
                break
            # Aliased local
            if isinstance(val, ast.Name) and val.id in aliases:
                threaded = True
                break
        if threaded:
            break

    assert threaded, (
        "CALIBRATION CHAIN REGRESSION: autotuner.py calls "
        "math_engine.compute_vwap_breakdown_update, but the "
        "`valid_vwap_weight` kwarg is NOT threaded from a tick read "
        "(neither direct nor via an alias). A hardcoded value would "
        "make the stale-cache `.get(..., 1.0)` defense cosmetic. The "
        "kwarg MUST receive its value from `tick.get(\"valid_vwap_weight\", "
        "1.0)` (directly or via a one-line local alias)."
    )
