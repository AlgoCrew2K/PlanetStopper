"""
RED tests — H-3: fail-open trailing-stop arming when MC is absent.

Audit finding H-3 root cause
==============================
The only armed=True path requires mc_available=True:

    alpha_bot_execution.py:1292  mc_available = prob_underperforming is not None
    alpha_bot_execution.py:1294  if mc_available and acc_TAKE_PROFIT_MC_PCT <= prob_underperforming < acc_TRIGGER_THRESHOLD_PCT:
    alpha_bot_execution.py:1295      should_arm = True

When MC history is permanently insufficient (prob_underperforming is None every cycle),
mc_available is always False, should_arm stays False, bot_state["armed"] stays
False, and compute_exit_confirmation is called with armed=False — its early-return
guard fires and the protective stop NEVER arms.

The code comment at alpha_bot_execution.py:1284-1289 explicitly says
"The protective stop still fires on its ticks-below-stop condition alone" — a
documented contract that the pre-fix code violates.

Required behavior after the fix (FAIL-OPEN)
============================================
- When MC is absent (prob_underperforming is None), the trailing stop MUST arm.
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
POST R3-b (MA-4): the arm/disarm decision was EXTRACTED into the pure seam
``math_engine.compute_arm_disarm_decision`` (called by both production and the
autotuner replay). The inline arming block this file used to inspect via AST no
longer exists — so Section 1 now asserts the SAME fail-open / arm-band /
disarm-requires-mc / triggered-guard contracts BEHAVIORALLY against that seam
(a strict upgrade over AST-structure matching), plus a light guard that
production DELEGATES to the seam (the fail-open arm is preserved, not dropped).
This file tests it via:

1. Behavioral assertions against math_engine.compute_arm_disarm_decision: the
   fail-open arm on MC-absent, the MC-present arm band, the triggered guard, and
   the disarm-requires-mc-available contract (Section 1).
2. Integration-style: exercises compute_exit_confirmation directly with armed=True
   and prob_underperforming=None (verifying the math layer is already correct and fires).
3. Golden-fixture parametrised tests: sequence assertions for the ticks-below-stop
   confirmation ladder with MC absent.
4. Regression parametrised tests: MC-present behaviour unchanged.
5. Property tests: monotonicity, idempotence, scope guard.

Scope guard
===========
This file covers the ARM/DISARM SEAM (math_engine.compute_arm_disarm_decision,
fail-open + disarm-requires-mc facets) and the exit-confirmation layer
(math_engine.compute_exit_confirmation) with prob_underperforming=None. It does
NOT cover: the recovery-disarm hysteresis ladder (see
tests/math_engine/test_r3b_disarm_recovery_hysteresis.py), TP arming, parabolic
arming, breakeven logic, or any other trigger type.
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
_FIXTURE_DIR = _WORKTREE_ROOT / "tests" / "fixtures" / "math_engine" / "arming_gate_failopen"

# ---------------------------------------------------------------------------
# Fixture loading helpers
# ---------------------------------------------------------------------------

EXIT_CONFIRM_TICKS = 3  # from math_engine; asserted by constant-existence test
MAGNITUDE_FLOOR_PCT = 0.10


def _load_fixture(name: str) -> dict:
    path = _FIXTURE_DIR / name
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


# ===========================================================================
# SECTION 1: Behavioral contracts at the extracted seam
# (post R3-b: the arm/disarm decision moved into
#  math_engine.compute_arm_disarm_decision; these were AST-structure checks on the
#  old inline arming block and are now behavioral against the seam — a strict upgrade.)
# ===========================================================================


def test_seam_fail_opens_arm_on_mc_absent() -> None:
    """MA-10 fail-open, now at the seam: an MC-absent reading (prob=None) while
    UNARMED must ARM the protective stop — an absent second opinion must never
    leave the stop dark. (Was an AST assertion on the inline `not mc_available ->
    should_arm=True` branch; the arm decision moved to the seam.)

    RED on base: math_engine.compute_arm_disarm_decision does not exist yet.
    """
    new_armed, _ = math_engine.compute_arm_disarm_decision(
        prob_underperforming=None,
        is_triggered=False,
        armed=False,
        disarm_confirm_count=0,
        take_profit_mc_pct=5.0,
        trigger_threshold_pct=15.0,
    )
    assert new_armed is True, (
        "MA-10 fail-open lost in the extraction: an absent MC opinion (prob=None) "
        "must arm the protective stop while unarmed."
    )


def test_seam_does_not_arm_triggered_position() -> None:
    """The not-triggered guard, now at the seam: an in-band reading must NOT arm a
    TRIGGERED position (production guarded the whole arm/disarm block on
    not-triggered). Was an AST assertion on the inline `not triggered` guard."""
    new_armed, _ = math_engine.compute_arm_disarm_decision(
        prob_underperforming=10.0,
        is_triggered=True,
        armed=False,
        disarm_confirm_count=0,
        take_profit_mc_pct=5.0,
        trigger_threshold_pct=15.0,
    )
    assert new_armed is False, (
        "The not-triggered guard was lost: an already-triggered position must never "
        "be (re-)armed by the seam."
    )


def test_seam_arms_on_mc_present_in_band() -> None:
    """The MC-present arm band, now at the seam: prob in
    [TAKE_PROFIT_MC_PCT, TRIGGER_THRESHOLD_PCT) must arm — the original MC-present
    arming path preserved (the extraction must EXTEND, not drop it)."""
    new_armed, _ = math_engine.compute_arm_disarm_decision(
        prob_underperforming=10.0,
        is_triggered=False,
        armed=False,
        disarm_confirm_count=0,
        take_profit_mc_pct=5.0,
        trigger_threshold_pct=15.0,
    )
    assert new_armed is True, (
        "The MC-present arm band [TAKE_PROFIT_MC_PCT, TRIGGER_THRESHOLD_PCT) must "
        "still arm the stop."
    )


def test_seam_disarm_requires_mc_available() -> None:
    """The disarm-requires-mc contract, now at the seam: an MC-absent tick
    (prob=None) while ARMED must NOT disarm — an absent opinion cannot manufacture
    a recovery signal (the disarm requires an available reading confirming genuine
    recovery, prob < TAKE_PROFIT_MC_PCT). Was an AST assertion that the inline
    disarm required mc_available.

    A regression that disarmed on MC-absent would silently drop the protective stop
    on a data gap — the exact fail-dangerous path this test forbids.
    """
    new_armed, _ = math_engine.compute_arm_disarm_decision(
        prob_underperforming=None,
        is_triggered=False,
        armed=True,
        disarm_confirm_count=0,
        take_profit_mc_pct=5.0,
        trigger_threshold_pct=15.0,
    )
    assert new_armed is True, (
        "MC-absent must NOT disarm an armed stop — the disarm requires an available "
        "MC reading confirming genuine recovery (prob < TAKE_PROFIT_MC_PCT)."
    )


def test_production_arming_delegates_to_shared_seam() -> None:
    """The arm/disarm decision now lives in the extracted seam; production MUST
    delegate to it (so the fail-open arm is preserved, not silently re-inlined /
    dropped). RED on base: alpha_bot_execution.py does not yet call the seam."""
    src = _EXEC_FILE.read_text(encoding="utf-8")
    assert "compute_arm_disarm_decision" in src, (
        "alpha_bot_execution.py no longer routes arming through "
        "math_engine.compute_arm_disarm_decision — the fail-open arm and the "
        "recovery-disarm must be preserved via the shared seam, not re-inlined."
    )


# ===========================================================================
# SECTION 2: compute_exit_confirmation with armed=True, prob_underperforming=None
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
    Golden-fixture: with armed=True and prob_underperforming=None, the 3-tick
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
            prob_underperforming=inputs["prob_underperforming"],  # None
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
    Golden-fixture: armed=True, prob_underperforming=None, return ABOVE stop level.
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
        prob_underperforming=inputs["prob_underperforming"],  # None
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
        (
            "09_mc_absent_mc_sanity_irrelevant_magnitude_gates.json",
            0,
        ),  # at stop, not below threshold
        (
            "09_mc_absent_mc_sanity_irrelevant_magnitude_gates.json",
            1,
        ),  # exactly at threshold boundary
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
        prob_underperforming=inputs["prob_underperforming"],  # None
        current_below_stop_count=inputs["current_below_stop_count"],
    )
    assert new_count == expected["new_below_stop_count"], (
        f"Tick {tick_idx} ({step.get('description', '')}): "
        f"new_below_stop_count expected {expected['new_below_stop_count']}, got {new_count}. "
        f"Derivation: {step['derivation']}"
    )
    assert hit is False, f"Tick {tick_idx}: hit must be False at boundary counts. Got {hit}."


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
            prob_underperforming=inputs["prob_underperforming"],
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
    "prob_underperforming,current_below_stop_count,expected_count,expected_hit",
    [
        # H1 corrected gate: prob_underperforming is the fraction of analog days
        # that beat us (HIGH = badly underperforming). The breakdown gate fires the
        # stop when prob_underperforming >= MC_BREAKDOWN_THRESHOLD (60) and vetoes
        # below it. LOW underperformance (< 60) -> "normal noise, don't capitulate"
        # -> count resets, no hit.
        (10.0, 0, 0, False),
        (10.0, 1, 0, False),
        (10.0, 2, 0, False),
        (59.9, 2, 0, False),  # just under MC_BREAKDOWN_THRESHOLD -> veto -> reset
        # HIGH underperformance (>= 60.0) -> confirmed breakdown -> stop fires.
        (60.0, 2, 3, True),  # at threshold (>= is inclusive) -> count increments, hits at 3
        (75.0, 2, 3, True),
        (99.9, 2, 3, True),
    ],
)
def test_exit_confirmation_mc_present_corrected_gate_direction(
    prob_underperforming: float,
    current_below_stop_count: int,
    expected_count: int,
    expected_hit: bool,
) -> None:
    """
    Corrected-gate regression: when prob_underperforming is a real float (MC
    present), compute_exit_confirmation fires the protective stop iff
    prob_underperforming >= MC_BREAKDOWN_THRESHOLD (confirmed breakdown) and
    vetoes below it. This is the H1-corrected direction — the pre-H1 gate read
    the metric inverted (suppressed the stop exactly when underperformance was
    worst).

    Uses inputs where magnitude definitely passes: current_return=-2.0,
    stop_trigger_level=-1.0 -> threshold=-1.10 (well satisfied by -2.0), so the
    only variable is the MC breakdown gate.
    """
    new_count, hit = math_engine.compute_exit_confirmation(
        armed=True,
        is_triggered=False,
        current_return=-2.0,
        stop_trigger_level=-1.0,
        prob_underperforming=prob_underperforming,
        current_below_stop_count=current_below_stop_count,
    )
    assert new_count == expected_count, (
        f"MC-present corrected-gate: prob_underperforming={prob_underperforming}, "
        f"starting_count={current_below_stop_count}. Expected count={expected_count}, "
        f"got {new_count}."
    )
    assert hit is expected_hit, (
        f"MC-present corrected-gate: prob_underperforming={prob_underperforming}, "
        f"starting_count={current_below_stop_count}. Expected hit={expected_hit}, "
        f"got {hit}."
    )


# ===========================================================================
# SECTION 5: Property — armed=False guard still holds with prob_underperforming=None
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
    Property: even with prob_underperforming=None (MC absent), the not-armed guard
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
        prob_underperforming=None,  # MC absent
        current_below_stop_count=current_below_stop_count,
    )
    assert new_count == current_below_stop_count, (
        f"Guard broken: armed=False + MC absent must preserve count. "
        f"Expected {current_below_stop_count}, got {new_count}."
    )
    assert hit is False, f"Guard broken: armed=False + MC absent must return hit=False. Got {hit}."


# ===========================================================================
# SECTION 6: Property — MC absent, non-fire path (return stays above stop)
# Count monotonically does NOT increase when magnitude not met
# ===========================================================================


@pytest.mark.parametrize(
    "current_return,stop_trigger_level",
    [
        (0.0, -1.0),  # well above stop
        (-0.5, -1.0),  # above threshold -1.10
        (-1.09, -1.0),  # just above threshold boundary (strict: -1.09 > -1.10)
        (5.0, 0.0),  # positive return
    ],
)
def test_exit_confirmation_mc_absent_no_magnitude_no_increment(
    current_return: float,
    stop_trigger_level: float,
) -> None:
    """
    Property: when armed=True, MC absent (prob_underperforming=None), but return is
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
        prob_underperforming=None,
        current_below_stop_count=2,  # non-zero starting count
    )
    assert new_count == 0, (
        f"MC absent, return not below stop: count must reset to 0. "
        f"current_return={current_return}, stop={stop_trigger_level}, "
        f"threshold={stop_trigger_level - MAGNITUDE_FLOOR_PCT:.4f}. Got count={new_count}."
    )
    assert hit is False, f"MC absent, return not below stop: must not fire. Got hit={hit}."


# ===========================================================================
# SECTION 7: Scope guard — fix must NOT touch exit confirm beyond prob_underperforming=None
# ===========================================================================


def test_exit_confirmation_stays_free_of_arming_identifiers() -> None:
    """
    Scope guard (updated for R3-b): the arm/disarm DECISION now lives in the
    dedicated pure seam math_engine.compute_arm_disarm_decision — but
    math_engine.compute_exit_confirmation must REMAIN free of arming logic
    (should_arm / arm_reason / mc_available identifiers, or any `armed` assignment).
    The two concerns stay separate: compute_exit_confirmation is the pure
    exit-confirmation ladder; the arm/disarm gate is its own seam.

    compute_exit_confirmation accepts prob_underperforming=None and treats None as
    MC-breakdown-gate-pass (fail-safe, H-1 cycle) — unchanged by R3-b.

    Catches: a fix that leaks arm/disarm logic into compute_exit_confirmation,
    which would change the math layer's behaviour for NOT-armed calls (wrong lane).
    """
    src_path = pathlib.Path(math_engine.__file__)
    source = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # Find compute_exit_confirmation
    target: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "compute_exit_confirmation":
            target = node
            break
    assert target is not None, "compute_exit_confirmation not found in math_engine.py"

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
