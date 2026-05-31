"""
RED tests — Phase 3c: safety invariants from risk-3c review (live-exit safety reviewer).

These tests cover the safety requirements from risk-3c's pre-impl review of Phase 3c
(reviewed HEAD e591421, 2026-05-31).  They complement test_regime_exit_adjustment.py
which covers the adjustment API contract.  This file focuses on SAFETY INVARIANTS
that must hold under every regime label.

Knob choice: the chosen response knob is EXIT_CONFIRM_TICKS (passed as
exit_confirm_ticks kwarg to compute_exit_confirmation).  That choice resolves
the following risk-3c requirements as vacuously satisfied (no stop-distance
arithmetic is changed): REQ-BOUND-1, REQ-BOUND-2, REQ-BOUND-3, REQ-BOUND-4,
REQ-DIR-1 (stop-distance tests not applicable).  The tests here cover the
remaining required properties.

Safety requirements covered:
  REQ-FS-2:  resolve_trigger_priority order preserved under all regime labels
  REQ-FS-3:  adjusted N-confirms >= 1 under all regime labels (ladder cannot be disabled)
  REQ-FS-4:  breakeven_locked latch preserved under all regime labels
  REQ-FS-5:  MC_SANITY_THRESHOLD direction: mean-reverting must NOT lower the MC bar
  REQ-DIR-3: None/unknown regime -> safe default (identical to no-adjustment baseline)
  REQ-DEGRADE-1: degenerate/garbage regime labels -> same as None safe default
  REQ-DEGRADE-3: exit_confirm_ticks=0 is rejected (would disable the confirmation ladder)
  REQ-BUDGET-1: the adjustment operates on exactly one named response knob
  REQ-BUDGET-2: no regime-adjustment constant appears in the Optuna search-space spec

These tests are RED by construction — apply_regime_exit_adjustment and the
exit_confirm_ticks kwarg do not exist yet.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import math_engine

_WORKTREE_ROOT = pathlib.Path(__file__).parent.parent.parent

ALL_REGIME_LABELS = ["trending", "mean-reverting", "high-vol", None]
ALL_DEGENERATE_LABELS = [None, "", "unknown", "sideways", "TRENDING", "Mean-Reverting"]


# ---------------------------------------------------------------------------
# REQ-FS-2: resolve_trigger_priority order unchanged under every regime label
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("regime_label", ALL_REGIME_LABELS)
def test_priority_ladder_order_preserved_under_all_regimes(
    regime_label: str | None,
) -> None:
    """
    REQ-FS-2: resolve_trigger_priority must return the same canonical priority
    order regardless of regime label.

    The regime adjustment touches exit_confirm_ticks only.  It MUST NOT reorder,
    disable, or short-circuit any of the four exit signals.

    Test: fire all four signals simultaneously under each regime label.
    resolve_trigger_priority takes boolean flags directly — it does not accept a
    regime label.  The contract is that the regime adjustment does not touch this
    function at all.  We assert:
      - With all four flags True: winner is always 'VWAP Breakdown' (highest priority)
      - With only Trailing Stop True: winner is always 'Trailing Stop'
      - With no flags True: winner is None

    These assertions hold with or without regime adjustment — the function is pure
    and regime-independent by contract.
    """
    # Apply regime adjustment (to confirm the function exists and doesn't raise)
    adjusted_ticks = math_engine.apply_regime_exit_adjustment(
        regime_label=regime_label,
        base_ticks=math_engine.EXIT_CONFIRM_TICKS,
    )
    assert isinstance(adjusted_ticks, int), (
        f"apply_regime_exit_adjustment must return int, regime_label={regime_label!r}"
    )

    # resolve_trigger_priority is regime-independent — but confirm it still works
    winner, co_fired = math_engine.resolve_trigger_priority(
        is_vwap_broken=True,
        is_tp_hit=True,
        is_vwap_bleed_broken=True,
        is_trailing_stop_hit=True,
    )
    assert winner == "VWAP Breakdown", (
        f"Priority ladder order violated under regime_label={regime_label!r}: "
        f"all four signals true -> winner must be 'VWAP Breakdown', got {winner!r}. "
        f"The regime adjustment must NOT alter resolve_trigger_priority."
    )
    assert "Trailing Stop" in co_fired, (
        f"Trailing Stop must be in co_fired when all signals true, "
        f"got co_fired={co_fired!r} under regime_label={regime_label!r}"
    )

    winner_trailing_only, _ = math_engine.resolve_trigger_priority(
        is_vwap_broken=False,
        is_tp_hit=False,
        is_vwap_bleed_broken=False,
        is_trailing_stop_hit=True,
    )
    assert winner_trailing_only == "Trailing Stop", (
        f"Priority ladder: only Trailing Stop true -> winner must be 'Trailing Stop', "
        f"got {winner_trailing_only!r} under regime_label={regime_label!r}"
    )

    winner_none, _ = math_engine.resolve_trigger_priority(
        is_vwap_broken=False,
        is_tp_hit=False,
        is_vwap_bleed_broken=False,
        is_trailing_stop_hit=False,
    )
    assert winner_none is None, (
        f"Priority ladder: no signals true -> winner must be None, "
        f"got {winner_none!r} under regime_label={regime_label!r}"
    )


# ---------------------------------------------------------------------------
# REQ-FS-3: adjusted exit_confirm_ticks >= 1 (ladder cannot be disabled)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("regime_label", ALL_REGIME_LABELS)
@pytest.mark.parametrize("base_ticks", [1, 2, 3, 4, 5, 8])
def test_adjusted_ticks_never_zero_or_negative(
    regime_label: str | None, base_ticks: int
) -> None:
    """
    REQ-FS-3: a regime adjustment that returns 0 or negative would disable the
    confirmation ladder entirely (the trailing stop would fire on the very FIRST
    qualifying tick, with no confirmation required).  That is an illegal state
    that bypasses the protection the ladder provides.

    Covered also by REGIME_TICKS_LOWER_BOUND >= 1 test in
    test_regime_exit_adjustment.py — this test is a direct safety assertion on
    the function output.
    """
    result = math_engine.apply_regime_exit_adjustment(
        regime_label=regime_label, base_ticks=base_ticks
    )
    assert result >= 1, (
        f"REQ-FS-3 VIOLATED: apply_regime_exit_adjustment returned {result} "
        f"(regime_label={regime_label!r}, base_ticks={base_ticks}). "
        f"A zero or negative confirmation-tick threshold disables the "
        f"confirmation ladder — the stop would fire on the first tick "
        f"without confirmation. Minimum adjusted ticks is 1."
    )


# ---------------------------------------------------------------------------
# REQ-FS-4: breakeven_locked latch preserved under all regime labels
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("regime_label", ALL_REGIME_LABELS)
def test_breakeven_locked_latch_preserved_under_all_regimes(
    regime_label: str | None,
) -> None:
    """
    REQ-FS-4: the breakeven_locked one-way latch must remain intact under all
    regime labels.  Once breakeven_locked=True, compute_breakeven_update must
    still return new_breakeven_locked=True regardless of regime.

    The regime adjustment does NOT touch compute_breakeven_update (it only adds
    exit_confirm_ticks to compute_exit_confirmation).  This test verifies that
    the latching invariant still holds after the regime adjustment is applied
    (i.e. no regime-adjustment side-effect corrupts the latch).
    """
    # Apply the regime adjustment (confirms function doesn't raise or corrupt state)
    _ = math_engine.apply_regime_exit_adjustment(
        regime_label=regime_label,
        base_ticks=math_engine.EXIT_CONFIRM_TICKS,
    )

    # Now call compute_breakeven_update with breakeven_locked=True
    # The latch must NOT be reset by anything, including a regime-adjusted cycle
    new_hold_ticks, new_breakeven_locked, stop_trigger_level = (
        math_engine.compute_breakeven_update(
            current_return=-5.0,        # below activation — would reset hold ticks
            symphony_vol=1.0,
            base_stop_level=-2.0,
            current_hold_ticks=0,
            currently_breakeven_locked=True,   # LATCHED
            is_triggered=False,
        )
    )
    assert new_breakeven_locked is True, (
        f"REQ-FS-4 VIOLATED: breakeven_locked latch was reset under "
        f"regime_label={regime_label!r}. The one-way latch must NEVER be "
        f"reset to False once set. new_breakeven_locked={new_breakeven_locked!r}."
    )
    # Also: breakeven floor must be applied (stop must be >= 0.0 when locked)
    assert stop_trigger_level >= 0.0, (
        f"REQ-FS-4 corollary: when breakeven_locked=True, stop_trigger_level "
        f"must be >= 0.0 (breakeven floor). Got {stop_trigger_level!r} under "
        f"regime_label={regime_label!r}."
    )


# ---------------------------------------------------------------------------
# REQ-FS-5: MC_SANITY_THRESHOLD direction — mean-reverting must NOT lower the bar
# ---------------------------------------------------------------------------


def test_mean_reverting_does_not_lower_mc_sanity_threshold() -> None:
    """
    REQ-FS-5: MC_SANITY_THRESHOLD=60.0 is the veto that suppresses exit when
    MC says we're still likely to beat the benchmark.  A mean-reverting regime
    calls for RESTRAINT (fewer exits).  It must NOT lower this threshold (which
    would make it EASIER to exit — wrong direction).

    Since the regime adjustment in Phase 3c adjusts exit_confirm_ticks only
    (NOT MC_SANITY_THRESHOLD), this requirement is vacuously satisfied by the
    chosen knob.  This test encodes that the MC_SANITY_THRESHOLD constant must
    not be modified or shadowed as a side-effect of the regime adjustment.

    Test: MC_SANITY_THRESHOLD must still equal its pre-Phase-3c value (60.0)
    after apply_regime_exit_adjustment is called with 'mean-reverting'.
    """
    _ = math_engine.apply_regime_exit_adjustment(
        regime_label="mean-reverting",
        base_ticks=math_engine.EXIT_CONFIRM_TICKS,
    )
    assert math_engine.MC_SANITY_THRESHOLD == 60.0, (
        f"REQ-FS-5 VIOLATED: MC_SANITY_THRESHOLD was modified by the regime "
        f"adjustment (mean-reverting call). MC_SANITY_THRESHOLD must remain "
        f"60.0. Got {math_engine.MC_SANITY_THRESHOLD!r}. The regime adjustment "
        f"must touch ONLY exit_confirm_ticks — never MC_SANITY_THRESHOLD."
    )


# ---------------------------------------------------------------------------
# REQ-DIR-3 / REQ-DEGRADE-1: degenerate inputs produce safe-default behavior
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "degenerate_label",
    ALL_DEGENERATE_LABELS,
)
def test_degenerate_regime_labels_are_safe_default(
    degenerate_label: str | None,
) -> None:
    """
    REQ-DIR-3 / REQ-DEGRADE-1: any degenerate or unrecognized label must
    produce EXACTLY the same exit behavior as the no-adjustment baseline.

    We verify that compute_exit_confirmation with exit_confirm_ticks=adjusted
    (for a degenerate label) is IDENTICAL to calling it with the module-level
    EXIT_CONFIRM_TICKS default.

    Specifically: starting_count = EXIT_CONFIRM_TICKS - 1 should fire with
    adjusted_ticks == EXIT_CONFIRM_TICKS (safe default), and should NOT fire
    with adjusted_ticks > EXIT_CONFIRM_TICKS (which would be a restraint leak
    from an unrecognized label incorrectly treated as 'mean-reverting').
    """
    base = math_engine.EXIT_CONFIRM_TICKS
    adjusted = math_engine.apply_regime_exit_adjustment(
        regime_label=degenerate_label, base_ticks=base
    )
    assert adjusted == base, (
        f"REQ-DEGRADE-1: degenerate/unknown label {degenerate_label!r} must "
        f"return base_ticks={base} (safe default), got adjusted={adjusted}. "
        f"No adjustment must be applied for unrecognized labels."
    )

    # Confirm that with adjusted==base, the confirmation fires at the expected count
    _, hit = math_engine.compute_exit_confirmation(
        armed=True,
        is_triggered=False,
        current_return=-5.0,
        stop_trigger_level=-1.0,
        prob_beating=None,
        current_below_stop_count=base - 1,
        exit_confirm_ticks=adjusted,
    )
    assert hit is True, (
        f"Safe-default exit confirmation: with degenerate label "
        f"{degenerate_label!r} -> adjusted={adjusted} == base={base}, "
        f"starting_count={base - 1} should produce hit=True on the next "
        f"qualifying tick. Got hit={hit!r}. The safe default must preserve "
        f"the original baseline firing behavior."
    )


# ---------------------------------------------------------------------------
# REQ-DEGRADE-3: exit_confirm_ticks=0 rejection
# ---------------------------------------------------------------------------


def test_exit_confirm_ticks_zero_is_disallowed() -> None:
    """
    REQ-DEGRADE-3 / REQ-FS-3: passing exit_confirm_ticks=0 to
    compute_exit_confirmation should be treated as an error.

    A zero-tick threshold would arm a stop that fires on the very first
    qualifying tick — no confirmation ladder at all.  This is an illegal
    state.  compute_exit_confirmation must reject exit_confirm_ticks <= 0
    with ValueError (same reject-don't-coerce policy as existing math layer
    functions, e.g. squeeze_multiplier <= 0).

    Note: apply_regime_exit_adjustment is bounded by REGIME_TICKS_LOWER_BOUND
    >= 1, so 0 can only arrive via a direct caller bypassing the adjustment
    function.  The rejection in compute_exit_confirmation is the last-line
    guard.
    """
    with pytest.raises(ValueError, match="exit_confirm_ticks"):
        math_engine.compute_exit_confirmation(
            armed=True,
            is_triggered=False,
            current_return=-5.0,
            stop_trigger_level=-1.0,
            prob_beating=None,
            current_below_stop_count=0,
            exit_confirm_ticks=0,
        )


# ---------------------------------------------------------------------------
# REQ-BUDGET-1: exactly one response knob (AST: only exit_confirm_ticks touched)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# risk-3c ISSUE 1a: REGIME_TICKS_UPPER_BOUND must be below session-length ceiling
# ---------------------------------------------------------------------------


def test_regime_ticks_upper_bound_is_below_session_length() -> None:
    """
    risk-3c REQ-BOUND-2 analog for N-confirms: an unbounded ceiling means a
    mean-reverting restraint adjustment could require so many ticks that the
    stop NEVER fires in a single trading session (390 minutes max).

    REGIME_TICKS_UPPER_BOUND must be a named constant AND must be well below
    any meaningful session-length ceiling.  A session has at most 390 trading
    minutes; a stop that requires more than 30 consecutive below-stop ticks
    would take at least half an hour of uninterrupted below-stop conditions to
    fire — effectively disabled.  The bound must be NAMED and <= 30.

    Source: 00-ADAPTIVE-RECOMMENDATION.md §1A — adjustments are bounded
    fixed theory-set constants; an unbounded upper end is a pathological state.
    """
    upper = math_engine.REGIME_TICKS_UPPER_BOUND
    # The constant was already tested for existence in test_regime_ticks_bounds_constants_exist.
    # This test adds the session-length safety ceiling.
    SESSION_MINUTES = 390  # NYSE regular session: 09:30-16:00 ET
    PRACTICAL_CEILING = 30  # at most 30 consecutive below-stop ticks = ~30 min of continuous drawdown required
    assert upper <= PRACTICAL_CEILING, (
        f"REGIME_TICKS_UPPER_BOUND={upper} exceeds the practical safety ceiling "
        f"of {PRACTICAL_CEILING}. A confirmation count above {PRACTICAL_CEILING} "
        f"ticks would require more than {PRACTICAL_CEILING} consecutive below-stop "
        f"minutes to fire — effectively disabling the trailing stop in most sessions. "
        f"SESSION_MINUTES={SESSION_MINUTES}. Risk-3c ISSUE 1a: N-confirms ceiling "
        f"must be meaningfully below session length."
    )
    assert upper >= math_engine.EXIT_CONFIRM_TICKS, (
        f"REGIME_TICKS_UPPER_BOUND={upper} must be >= EXIT_CONFIRM_TICKS="
        f"{math_engine.EXIT_CONFIRM_TICKS} (the upper bound must accommodate "
        f"at least the mean-reverting restraint value)"
    )


# ---------------------------------------------------------------------------
# risk-3c ISSUE 1b: trending adjusted ticks never above EXIT_CONFIRM_TICKS
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("base_ticks", [3, 4, 5, 8])
def test_trending_regime_adjusted_ticks_at_or_below_baseline(
    base_ticks: int,
) -> None:
    """
    risk-3c REQ-BOUND-4 analog for N-confirms: a 'trending' regime adjustment
    that RAISES N-confirms above the baseline is the OPPOSITE of theory-
    consistent (it makes the stop HARDER to confirm = LESS active = wrong
    direction for trending per Kaminski-Lo 2014).

    For base_ticks values >= EXIT_CONFIRM_TICKS (the normal production domain),
    apply_regime_exit_adjustment('trending', base_ticks) must return a value
    <= base_ticks.  The direction is more-active (fewer ticks needed), never
    more-restraint.

    Domain: base_ticks >= EXIT_CONFIRM_TICKS=3.  When base_ticks < TRENDING_EXIT_TICKS,
    the adjustment is a floor not a target — the safe-default lower bound applies.
    The production caller always passes EXIT_CONFIRM_TICKS (=3) as base, and
    TRENDING_EXIT_TICKS (=2) < 3, so the production case is always <= base.

    This test covers the normal production domain (base >= EXIT_CONFIRM_TICKS).
    """
    result = math_engine.apply_regime_exit_adjustment(
        regime_label="trending", base_ticks=base_ticks
    )
    assert result <= base_ticks, (
        f"risk-3c ISSUE 1b: trending adjusted_ticks={result} > base_ticks={base_ticks}. "
        f"A trending regime adjustment must NEVER raise N-confirms above the baseline "
        f"(that would make the stop LESS active = opposite of theory). "
        f"Kaminski-Lo 2014: trending -> more-active (exits faster). "
        f"adjusted_ticks must be <= base_ticks for base_ticks >= EXIT_CONFIRM_TICKS."
    )


def test_apply_regime_exit_adjustment_does_not_modify_module_constants() -> None:
    """
    REQ-BUDGET-1: the regime adjustment must move EXACTLY ONE knob.

    This test verifies that calling apply_regime_exit_adjustment does not
    mutate ANY module-level constant.  The function is pure — it must not
    reassign EXIT_CONFIRM_TICKS, MC_SANITY_THRESHOLD, MAGNITUDE_FLOOR_PCT,
    MULT_OPEN, MULT_CLOSE, or any other module attribute.

    A mutation would violate the one-knob budget AND the purity requirement.
    """
    constants_before = {
        "EXIT_CONFIRM_TICKS": math_engine.EXIT_CONFIRM_TICKS,
        "MC_SANITY_THRESHOLD": math_engine.MC_SANITY_THRESHOLD,
        "MAGNITUDE_FLOOR_PCT": math_engine.MAGNITUDE_FLOOR_PCT,
        "MULT_OPEN": math_engine.MULT_OPEN,
        "MULT_CLOSE": math_engine.MULT_CLOSE,
        "MIN_STOP_OPEN": math_engine.MIN_STOP_OPEN,
        "MIN_STOP_CLOSE": math_engine.MIN_STOP_CLOSE,
    }

    for label in ALL_REGIME_LABELS:
        math_engine.apply_regime_exit_adjustment(
            regime_label=label, base_ticks=math_engine.EXIT_CONFIRM_TICKS
        )

    for name, val_before in constants_before.items():
        val_after = getattr(math_engine, name)
        assert val_after == val_before, (
            f"REQ-BUDGET-1 VIOLATED: apply_regime_exit_adjustment mutated "
            f"module constant math_engine.{name}: was {val_before!r}, "
            f"now {val_after!r}. The function must be pure — no module state "
            f"mutations. The ONE response knob (exit_confirm_ticks) is passed "
            f"as a parameter, not encoded as a mutated constant."
        )


# ---------------------------------------------------------------------------
# REQ-BUDGET-2: regime constants NOT in Optuna spec / autotuner search space
# ---------------------------------------------------------------------------


def test_regime_adjustment_constants_not_in_optuna_spec() -> None:
    """
    REQ-BUDGET-2: the per-regime tick constants (TRENDING_EXIT_TICKS,
    MEAN_REVERTING_EXIT_TICKS, HIGH_VOL_EXIT_TICKS) must NOT appear in the
    Optuna search space.  They are THEORY-ANCHORED fixed constants; if they
    were tuned by Optuna, the ≈1-knob budget would be exceeded.

    This test scans autotuner.py for references to the new Phase 3c constant
    names in the context of suggest_int / suggest_float / suggest_categorical
    calls (the Optuna trial suggestion API).  If any new constant name is
    suggested by a trial, it is being tuned — that violates REQ-BUDGET-2.

    The scan is conservative: it checks that the constant NAME STRINGS do NOT
    appear inside a trial.suggest_* call in autotuner.py's AST.
    """
    autotuner_path = _WORKTREE_ROOT / "autotuner.py"
    assert autotuner_path.exists(), (
        f"autotuner.py not found at {autotuner_path}"
    )
    source = autotuner_path.read_text(encoding="utf-8")

    # Phase 3c constant name strings that must NOT appear in suggest_* calls
    phase3c_constant_names = {
        "TRENDING_EXIT_TICKS",
        "MEAN_REVERTING_EXIT_TICKS",
        "HIGH_VOL_EXIT_TICKS",
        "REGIME_TICKS_LOWER_BOUND",
        "REGIME_TICKS_UPPER_BOUND",
    }

    tree = ast.parse(source)
    src_lines = source.splitlines()

    # Find all suggest_* calls and check if any constant name appears in them
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match trial.suggest_int / trial.suggest_float / trial.suggest_categorical
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr.startswith("suggest_")):
            continue
        # Collect string literals in the call arguments (the parameter name arg)
        for arg in node.args + [kw.value for kw in node.keywords]:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                if arg.value in phase3c_constant_names:
                    violations.append(
                        f"line {node.lineno}: {src_lines[node.lineno - 1].strip()}"
                    )

    assert not violations, (
        f"REQ-BUDGET-2 VIOLATED: Phase 3c regime constants found in Optuna "
        f"suggest_* calls in autotuner.py. These constants must be FIXED "
        f"theory-anchored values, NOT tuned by Optuna (that would exceed the "
        f"≈1-knob budget per 00-ADAPTIVE-RECOMMENDATION.md §1A). Violations: "
        + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# One-knob audit: only exit_confirm_ticks is regime-adjusted in exec path
# ---------------------------------------------------------------------------


def test_regime_adjustment_table_docstring_cites_one_knob_budget() -> None:
    """
    REQ-BUDGET-1 documentation: apply_regime_exit_adjustment must document
    in its docstring which single knob it moves AND cite the one-knob budget
    from the research document.

    The docstring must contain both 'exit_confirm_ticks' (or 'N-confirms')
    to identify the knob, and '≈1' or '1 knob' or '1-knob' to cite the budget.
    This makes the one-knob contract explicit and auditable.
    """
    fn = getattr(math_engine, "apply_regime_exit_adjustment", None)
    assert fn is not None, (
        "apply_regime_exit_adjustment not found in math_engine (RED state)"
    )
    doc = (fn.__doc__ or "").lower()
    knob_mentioned = "exit_confirm_ticks" in doc or "n-confirm" in doc or "confirm" in doc
    budget_mentioned = "1 knob" in doc or "1-knob" in doc or "one knob" in doc or "≈1" in doc
    assert knob_mentioned, (
        "apply_regime_exit_adjustment docstring must name the single response "
        "knob it moves (e.g. 'exit_confirm_ticks' or 'N-confirms'). "
        "REQ-BUDGET-1: the one-knob contract must be explicit and auditable."
    )
    assert budget_mentioned, (
        "apply_regime_exit_adjustment docstring must cite the one-knob budget "
        "(e.g. '≈1 knob', '1-knob', 'one knob'). "
        "REQ-BUDGET-1: the budget constraint must be visible in the docstring."
    )


# ---------------------------------------------------------------------------
# risk-3c ISSUE 1b follow-up: base_ticks provenance in production wiring
# ---------------------------------------------------------------------------


def test_apply_regime_exit_adjustment_base_ticks_is_named_constant_not_state() -> None:
    """
    risk-3c ISSUE 1b clarification (from review of 396aa7d): the production
    call site MUST pass EXIT_CONFIRM_TICKS (the module-level named constant) as
    base_ticks, NOT current_below_stop_count or any other stateful counter.

    If the caller passed current_below_stop_count as base_ticks, a trending
    regime could see base_ticks < TRENDING_EXIT_TICKS on intermediate ticks
    (e.g. below_stop_count=1 when TRENDING_EXIT_TICKS=2), and the adjustment
    would return TRENDING_EXIT_TICKS=2, which is ABOVE the current stateful
    count — raising the effective threshold per-tick rather than applying a
    fixed adjustment.  That is a composition error, not what the one-knob
    design intends.

    This is an AST test on alpha_bot_execution.py's call site for
    apply_regime_exit_adjustment.  The call must pass math_engine.EXIT_CONFIRM_TICKS
    (or the module-level constant EXIT_CONFIRM_TICKS by Name reference) as
    base_ticks — never a Name referencing below_stop_count, above_tp_count, or
    any other state variable.

    The test is RED until alpha_bot_execution.py wires the regime adjustment.
    After wiring, it asserts the correct provenance in the call.
    """
    src_path = _WORKTREE_ROOT / "alpha_bot_execution.py"
    assert src_path.exists(), f"alpha_bot_execution.py not found at {src_path}"
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # Find all calls to apply_regime_exit_adjustment
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Match both math_engine.apply_regime_exit_adjustment and direct name
        if isinstance(func, ast.Attribute) and func.attr == "apply_regime_exit_adjustment":
            calls.append(node)
        elif isinstance(func, ast.Name) and func.id == "apply_regime_exit_adjustment":
            calls.append(node)

    assert calls, (
        "apply_regime_exit_adjustment is not called in alpha_bot_execution.py. "
        "The regime-conditional exit lever must be wired into the execution path "
        "(Phase 3c scope). Add the call with base_ticks=math_engine.EXIT_CONFIRM_TICKS."
    )

    # Forbidden state-variable names that must NOT be passed as base_ticks
    STATE_VARS = {
        "below_stop_count",
        "_prev_below_stop_count",
        "new_below_stop_count",
        "above_tp_count",
        "hwm_hold_ticks",
    }

    for call in calls:
        # base_ticks may be a positional arg (index 1) or keyword arg
        base_ticks_node = None
        if len(call.args) >= 2:
            base_ticks_node = call.args[1]
        for kw in call.keywords:
            if kw.arg == "base_ticks":
                base_ticks_node = kw.value
                break

        assert base_ticks_node is not None, (
            f"apply_regime_exit_adjustment call at line {call.lineno} in "
            f"alpha_bot_execution.py has no base_ticks argument. The call must "
            f"pass base_ticks=math_engine.EXIT_CONFIRM_TICKS (or EXIT_CONFIRM_TICKS "
            f"by Name) — never omit it."
        )

        # base_ticks must reference EXIT_CONFIRM_TICKS, not a state variable
        if isinstance(base_ticks_node, ast.Name):
            assert base_ticks_node.id not in STATE_VARS, (
                f"apply_regime_exit_adjustment call at line {call.lineno}: "
                f"base_ticks={base_ticks_node.id!r} is a state variable. "
                f"risk-3c ISSUE 1b: base_ticks must come from EXIT_CONFIRM_TICKS "
                f"(the named constant), not from a stateful counter. Passing a "
                f"stateful count allows the trending adjustment to compose with "
                f"itself across ticks."
            )
        elif isinstance(base_ticks_node, ast.Attribute):
            # e.g. math_engine.EXIT_CONFIRM_TICKS
            assert base_ticks_node.attr == "EXIT_CONFIRM_TICKS", (
                f"apply_regime_exit_adjustment call at line {call.lineno}: "
                f"base_ticks is an attribute access '{base_ticks_node.attr}', not "
                f"EXIT_CONFIRM_TICKS. base_ticks must be math_engine.EXIT_CONFIRM_TICKS."
            )
