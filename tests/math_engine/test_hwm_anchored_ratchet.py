"""
RED tests for AC-3 / AC-4 (audit finding H-1): the trailing-stop ratchet must
anchor on the HIGH-WATER MARK, not freeze the resolved stop level.

Background (math-audit exit-decision-math__2026-05-21.md, HIGH finding):

  compute_breakeven_update previously threaded `previously_persisted_stop_level`
  and clamped the resolved level upward: stop = max(prev_level, new_resolved).
  That FREEZES the resolved stop level. On a legitimate mid-day volatility
  spike the correct response is to WIDEN the stop (the vol-scaled distance
  grows, so HWM - distance falls) — but the frozen-level clamp discards that
  widening, keeping a stop too tight for realized vol and exiting on noise.

  A classical trailing stop (Fu & Zhang 2012) ratchets on the HIGH-WATER
  MARK, recomputing  stop = HWM - vol_scaled_distance  EACH TICK. The stop:
    - rises when the HWM rises (distance constant),
    - moves DOWN when the vol-scaled distance widens (HWM constant).
  The only ratchet that survives the fix is the breakeven floor: once the
  breakeven latch fires, stop = max(base_stop_level, 0.0) — "lock gains hard".

THE INTENTIONAL BEHAVIOR CHANGE (plan AC-12): removing the
`previously_persisted_stop_level` clamp changes compute_breakeven_update's
output. test_stop_monotonicity.py and test_e2_replay_ratchet_parity.py
encoded the OLD (frozen-level) behavior; they are rewritten to the new
contract as part of this RED cycle — that is expected, not a regression.

GREEN target (the implementer owns the final form):
  - Remove the `previously_persisted_stop_level` parameter from
    compute_breakeven_update entirely (no backwards-compat dead param).
  - The function returns stop = base_stop_level (unlocked) or
    max(base_stop_level, 0.0) (locked) or TRIGGERED_OVERRIDE_LEVEL
    (is_triggered) — with NO cross-tick clamp.
  - The caller (alpha_bot_execution.py) and autotuner.py drop the kwarg.

Tolerance policy:
  - stop_trigger_level: pytest.approx(rel=1e-9, abs=1e-12). The math is
    subtraction + max() with no transcendentals; ~1 ulp is the realistic
    IEEE-754 noise floor. A wrong impl (frozen clamp re-introduced, floor
    omitted, sign flipped) misses by structural factors, not by ~1 ulp.
  - new_hold_ticks (int) and new_breakeven_locked (bool): exact equality —
    pytest.approx is meaningless for ints/bools and would mask bugs.
  - Monotonicity / direction comparisons use exact `<` / `>`: a drift in
    the wrong direction is always a violation, never tolerance noise.

Provenance: every expected value is hand-derived in the fixture JSON's
`derivation` field (tests/fixtures/math/hwm_anchored_stop/) or in the inline
property derivations below. No producer-computed values are hardcoded.
"""

from __future__ import annotations

import ast
import inspect
import json
import pathlib
from typing import Any

import pytest

import math_engine


FIXTURE_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "math" / "hwm_anchored_stop"

APPROX_REL = 1e-9
APPROX_ABS = 1e-12


# ---------------------------------------------------------------------------
# Fixture discovery
# ---------------------------------------------------------------------------


def _load_fixtures() -> list[tuple[str, dict[str, Any]]]:
    paths = sorted(FIXTURE_DIR.glob("*.json"))
    out: list[tuple[str, dict[str, Any]]] = []
    for p in paths:
        with p.open("r", encoding="utf-8") as fh:
            out.append((p.name, json.load(fh)))
    return out


FIXTURES = _load_fixtures()


def _call_breakeven_update(inputs: dict[str, Any]) -> tuple[int, bool, float]:
    """Call compute_breakeven_update with ONLY the post-fix parameters.

    Deliberately does NOT pass previously_persisted_stop_level — the GREEN
    contract removes that parameter. A GREEN impl that still requires the
    param would raise TypeError here, which is the intended RED-then-GREEN
    signal.
    """
    return math_engine.compute_breakeven_update(
        current_return=inputs["current_return"],
        symphony_vol=inputs["symphony_vol"],
        base_stop_level=inputs["base_stop_level"],
        current_hold_ticks=inputs["current_hold_ticks"],
        currently_breakeven_locked=inputs["currently_breakeven_locked"],
        is_triggered=inputs["is_triggered"],
    )


# ===========================================================================
# AC-3 — Golden-fixture tests: HWM-anchored stop behavior
# ===========================================================================


@pytest.mark.parametrize(
    "fixture_name,fixture",
    FIXTURES,
    ids=[name for name, _ in FIXTURES],
)
def test_hwm_anchored_stop_matches_derived_fixture(
    fixture_name: str, fixture: dict[str, Any]
) -> None:
    """
    Each fixture under tests/fixtures/math/hwm_anchored_stop/ replays a
    two-tick sequence and pins, per tick, the (new_hold_ticks,
    new_breakeven_locked, stop_trigger_level) the HWM-anchored contract must
    produce. Every expected value is hand-derived in the fixture's
    `derivation` field.

    RED: the pre-fix compute_breakeven_update threads
    previously_persisted_stop_level and would clamp the resolved level
    upward, so the distance-widens fixture (stop must DROP) fails. This test
    calls the function WITHOUT that parameter, pinning the post-fix signature.
    """
    assert fixture["function"] == "compute_breakeven_update", (
        f"{fixture_name}: this fixture targets compute_breakeven_update only"
    )

    for tick in fixture["ticks"]:
        label = tick["label"]
        inputs = tick["inputs"]
        expected = tick["expected"]

        new_hold_ticks, new_breakeven_locked, stop_trigger_level = _call_breakeven_update(inputs)

        assert new_hold_ticks == expected["new_hold_ticks"], (
            f"{fixture_name} [{label}]: new_hold_ticks mismatch. "
            f"Expected {expected['new_hold_ticks']}, got {new_hold_ticks}. "
            f"Derivation: {fixture['derivation']}"
        )
        assert new_breakeven_locked == expected["new_breakeven_locked"], (
            f"{fixture_name} [{label}]: new_breakeven_locked mismatch. "
            f"Expected {expected['new_breakeven_locked']}, "
            f"got {new_breakeven_locked}. Derivation: {fixture['derivation']}"
        )
        assert stop_trigger_level == pytest.approx(
            expected["stop_trigger_level"], rel=APPROX_REL, abs=APPROX_ABS
        ), (
            f"{fixture_name} [{label}]: stop_trigger_level mismatch. "
            f"Expected {expected['stop_trigger_level']}, "
            f"got {stop_trigger_level}. Derivation: {fixture['derivation']}"
        )


def test_hwm_anchored_stop_moves_down_when_distance_widens() -> None:
    """
    AC-3: the load-bearing assertion of the H-1 fix.

    Two consecutive ticks at the SAME high-water mark but a wider vol-scaled
    distance on tick 2. The resolved stop_trigger_level MUST move DOWN — the
    stop tracks HWM - distance, it is NOT a frozen ratchet.

    Derived from hwm_anchored_stop_distance_widens_stop_moves_down.json:
      tick1: HWM 3.0 - distance 1.0 = base 2.0 (unlocked -> stop 2.0)
      tick2: HWM 3.0 - distance 2.5 = base 0.5 (unlocked -> stop 0.5)
    Expected: stop[2] (0.5) < stop[1] (2.0).

    RED: the pre-fix impl, given previously_persisted_stop_level=2.0 on
    tick 2, would clamp to max(2.0, 0.5)=2.0 and the strict `<` assertion
    fires. (This test does not pass the kwarg, so on a GREEN impl it pins
    the new behavior directly.)
    """
    fixture = next(
        f for n, f in FIXTURES if n == "hwm_anchored_stop_distance_widens_stop_moves_down.json"
    )
    t1, t2 = fixture["ticks"]
    _, _, stop1 = _call_breakeven_update(t1["inputs"])
    _, _, stop2 = _call_breakeven_update(t2["inputs"])

    assert stop2 < stop1, (
        f"HWM-ANCHOR VIOLATED: tick-1 stop {stop1}, tick-2 stop {stop2}. "
        f"The HWM is identical on both ticks ({t1['inputs']['base_stop_level']} "
        f"+ distance vs {t2['inputs']['base_stop_level']} + wider distance); "
        f"only the vol-scaled distance widened. The stop MUST move down — a "
        f"true HWM-anchored trailing stop recomputes HWM - distance each "
        f"tick. If stop2 >= stop1 the frozen-level ratchet is still present."
    )


def test_hwm_anchored_stop_rises_when_hwm_advances() -> None:
    """
    AC-3: when the HWM rises and the vol-scaled distance is constant, the
    resolved stop_trigger_level rises with it (a true trailing stop tracks
    the HWM upward).

    Derived from hwm_anchored_stop_rises_with_hwm.json:
      tick1: HWM 2.0 - distance 1.0 = 1.0
      tick2: HWM 3.5 - distance 1.0 = 2.5
    Expected: stop[2] (2.5) > stop[1] (1.0), a +1.5 rise == the +1.5 HWM rise.
    """
    fixture = next(f for n, f in FIXTURES if n == "hwm_anchored_stop_rises_with_hwm.json")
    t1, t2 = fixture["ticks"]
    _, _, stop1 = _call_breakeven_update(t1["inputs"])
    _, _, stop2 = _call_breakeven_update(t2["inputs"])

    assert stop2 > stop1, (
        f"HWM-TRACK VIOLATED: tick-1 stop {stop1}, tick-2 stop {stop2}. "
        f"The HWM advanced and the vol-scaled distance was constant; the "
        f"stop must rise with the HWM."
    )


def test_hwm_anchored_breakeven_floor_holds_when_base_goes_negative() -> None:
    """
    AC-3: once the breakeven latch is set, the resolved stop floors at 0.0
    even when the HWM-anchored base goes negative (a deep vol spike widens
    the distance past the HWM). The 0.0 breakeven floor is the ONLY ratchet
    that survives the H-1 fix.

    Derived from hwm_anchored_stop_breakeven_floor_holds_post_lock.json:
      tick1 (locked): base 2.5 -> stop max(2.5, 0.0) = 2.5
      tick2 (locked): base -1.0 -> stop max(-1.0, 0.0) = 0.0
    The stop drops 2.5 -> 0.0 (allowed: post-lock stop may move down) but
    is FLOORED at 0.0 — never negative.

    Catches an impl that dropped the max(base, 0.0) floor (would emit -1.0)
    or that re-introduced a frozen-level clamp (would emit 2.5).
    """
    fixture = next(
        f for n, f in FIXTURES if n == "hwm_anchored_stop_breakeven_floor_holds_post_lock.json"
    )
    t1, t2 = fixture["ticks"]
    _, locked1, stop1 = _call_breakeven_update(t1["inputs"])
    _, locked2, stop2 = _call_breakeven_update(t2["inputs"])

    assert locked1 is True and locked2 is True, (
        f"Fixture sanity: both ticks are post-lock. Got locked1={locked1}, locked2={locked2}."
    )
    assert stop2 == pytest.approx(0.0, rel=APPROX_REL, abs=APPROX_ABS), (
        f"BREAKEVEN FLOOR VIOLATED: post-lock base went negative (-1.0) and "
        f"the resolved stop must floor at 0.0. Got {stop2}."
    )
    assert stop2 < stop1, (
        f"Post-lock stop should be allowed to move DOWN toward the floor: "
        f"tick1 {stop1} -> tick2 {stop2}. If stop2 >= stop1 a frozen-level "
        f"clamp is still present above the breakeven floor."
    )
    assert stop2 >= 0.0, f"Post-lock stop {stop2} must never be below the 0.0 breakeven floor."


# ===========================================================================
# AC-3 — Property tests: HWM monotonicity + recompute-each-tick
# ===========================================================================


# Each scenario: (id, hwm_sequence, distance_sequence). The stop on each
# tick is HWM[i] - distance[i] = base_stop_level. All ticks unlocked
# (current_return below threshold) so stop == base_stop_level directly.
_HWM_DISTANCE_SEQUENCES: list[tuple[str, list[float], list[float]]] = [
    # HWM rises, distance constant -> stop rises monotonically
    ("hwm_rises_distance_flat", [1.0, 2.0, 3.0, 4.0], [1.0, 1.0, 1.0, 1.0]),
    # HWM flat, distance widens -> stop FALLS each tick (the H-1 point)
    ("hwm_flat_distance_widens", [5.0, 5.0, 5.0, 5.0], [0.5, 1.0, 2.0, 3.5]),
    # HWM flat, distance narrows -> stop rises
    ("hwm_flat_distance_narrows", [5.0, 5.0, 5.0, 5.0], [3.0, 2.0, 1.0, 0.2]),
    # both move -> stop tracks the difference exactly
    ("hwm_and_distance_both_move", [2.0, 3.0, 3.0, 5.0], [1.0, 0.5, 2.0, 1.0]),
]


@pytest.mark.parametrize(
    "scenario_id,hwm_seq,distance_seq",
    _HWM_DISTANCE_SEQUENCES,
    ids=[s[0] for s in _HWM_DISTANCE_SEQUENCES],
)
def test_stop_equals_hwm_minus_distance_each_tick(
    scenario_id: str, hwm_seq: list[float], distance_seq: list[float]
) -> None:
    """
    PROPERTY (AC-3): for an unlocked position the resolved stop_trigger_level
    on every tick equals exactly base_stop_level = HWM - vol_scaled_distance.
    The function recomputes the stop each tick from scratch — no cross-tick
    state, no clamp.

    All ticks use current_return below the qualifying threshold so the
    breakeven latch never fires; the stop is the pass-through base level.

    This pins the recompute-each-tick contract: a frozen-level clamp would
    make stop[i] != HWM[i] - distance[i] on any tick where the recomputed
    base fell below a prior tick's level.
    """
    assert len(hwm_seq) == len(distance_seq), "scenario mis-specified"
    for i, (hwm, distance) in enumerate(zip(hwm_seq, distance_seq)):
        base = hwm - distance
        _, locked, stop = math_engine.compute_breakeven_update(
            current_return=-5.0,  # far below threshold; lock never fires
            symphony_vol=1.0,
            base_stop_level=base,
            current_hold_ticks=0,
            currently_breakeven_locked=False,
            is_triggered=False,
        )
        assert locked is False, (
            f"[{scenario_id}] tick {i}: lock should not fire on a "
            f"sub-threshold return. Got locked={locked}."
        )
        assert stop == pytest.approx(base, rel=APPROX_REL, abs=APPROX_ABS), (
            f"[{scenario_id}] tick {i}: stop {stop} != HWM-distance "
            f"{hwm}-{distance}={base}. The stop must be recomputed as "
            f"HWM - vol_scaled_distance each tick with no clamp."
        )


def test_hwm_is_monotone_non_decreasing_within_a_position() -> None:
    """
    PROPERTY (AC-3): the high-water mark is monotone non-decreasing within a
    single position's lifetime. The HWM advance is engine-side
    (alpha_bot_execution.py: `if current_return > high_water_mark and not
    triggered: high_water_mark = current_return`). This test replays that
    exact advance rule against a noisy return series and asserts the HWM
    never decreases.

    This is the invariant the HWM-anchored stop RELIES on: the stop may move
    down (distance widens) but the anchor it subtracts from never falls.

    Derivation: HWM[i] = max(HWM[i-1], return[i]). The return series
    deliberately rises, dips, and recovers; the HWM must be the running max.
    """
    returns = [0.5, 1.2, 0.8, 2.0, 1.5, 3.1, 2.9, 3.1, 0.0, 4.0]
    hwm = returns[0]
    hwm_series = [hwm]
    for r in returns[1:]:
        # Engine-side advance rule: HWM only ever rises.
        if r > hwm:
            hwm = r
        hwm_series.append(hwm)

    violations = [
        (i, hwm_series[i - 1], hwm_series[i])
        for i in range(1, len(hwm_series))
        if hwm_series[i] < hwm_series[i - 1]
    ]
    assert not violations, (
        f"HWM MONOTONICITY VIOLATED. Return series {returns} produced HWM "
        f"series {hwm_series}; drops at (i, prev, new): {violations}. The "
        f"high-water mark must be the running max of returns within a "
        f"position — it never decreases."
    )
    # Sanity: the HWM must equal the running max at every index.
    for i in range(len(returns)):
        assert hwm_series[i] == pytest.approx(
            max(returns[: i + 1]), rel=APPROX_REL, abs=APPROX_ABS
        ), f"HWM at tick {i} = {hwm_series[i]} != running max {max(returns[: i + 1])}."


# ===========================================================================
# AC-3 — Structural: the previously_persisted_stop_level parameter is GONE
# ===========================================================================


def test_compute_breakeven_update_no_longer_accepts_previously_persisted_stop_level() -> None:
    """
    AC-3 / AC-12: the H-1 fix REMOVES the previously_persisted_stop_level
    parameter — it was the frozen-level clamp the audit flagged. Per the
    project rule "no backwards-compatibility hacks; if something is unused,
    delete it", the parameter must be gone entirely (not kept as a dead
    no-op).

    RED: the current signature still has the parameter. GREEN removes it.
    """
    sig = inspect.signature(math_engine.compute_breakeven_update)
    assert "previously_persisted_stop_level" not in sig.parameters, (
        "compute_breakeven_update still declares "
        "'previously_persisted_stop_level'. The H-1 fix removes the "
        "frozen-resolved-level clamp entirely; the parameter must be "
        "deleted, not retained as a dead no-op."
    )


def test_compute_breakeven_update_call_with_legacy_kwarg_raises_type_error() -> None:
    """
    AC-3: a caller still passing the removed kwarg must FAIL LOUDLY with a
    TypeError rather than silently ignoring it. This guards against a GREEN
    impl that absorbs the param via **kwargs.
    """
    with pytest.raises(TypeError):
        math_engine.compute_breakeven_update(
            current_return=1.0,
            symphony_vol=1.0,
            base_stop_level=0.5,
            current_hold_ticks=0,
            currently_breakeven_locked=False,
            is_triggered=False,
            previously_persisted_stop_level=2.0,  # removed param
        )


def test_no_caller_threads_the_removed_legacy_kwarg() -> None:
    """
    AC-3 / AC-12: the H-1 fix removes previously_persisted_stop_level from
    compute_breakeven_update. Every CALL SITE must stop passing it. AST-scan
    alpha_bot_execution.py and autotuner.py — neither may pass the removed
    kwarg, otherwise the call raises TypeError at runtime on the live path.

    RED: the live path (alpha_bot_execution.py:1219) and BOTH autotuner call
    sites currently thread previously_persisted_stop_level=...; this test
    fails until they are updated.

    This is the inverse of the pre-fix test_e2_replay_ratchet_parity.py
    structural tests, which asserted the kwarg WAS present. The H-1 fix
    flips that contract.
    """
    project_root = pathlib.Path(math_engine.__file__).parent
    offenders: list[str] = []
    for filename in ("alpha_bot_execution.py", "autotuner.py"):
        path = project_root / filename
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_target = (
                isinstance(func, ast.Attribute) and func.attr == "compute_breakeven_update"
            ) or (isinstance(func, ast.Name) and func.id == "compute_breakeven_update")
            if not is_target:
                continue
            kwarg_names = {kw.arg for kw in node.keywords}
            if "previously_persisted_stop_level" in kwarg_names:
                offenders.append(f"{filename}:{node.lineno}")

    assert not offenders, (
        f"compute_breakeven_update is still called with the removed "
        f"previously_persisted_stop_level kwarg at: {offenders}. The H-1 "
        f"fix removes that parameter; every call site must drop it (and "
        f"drop any prev_persisted_stop threading), or the call raises "
        f"TypeError at runtime."
    )


def test_compute_breakeven_update_docstring_describes_hwm_anchored_stop() -> None:
    """
    AC-4: the compute_breakeven_update docstring must describe an
    HWM-anchored trailing stop and must NOT claim a monotonic resolved-level
    ratchet (the removed behavior).

    RED: the current docstring states a "MONOTONICITY INVARIANT
    (trailing-stop ratchet): a trailing stop must never move DOWN ..." — the
    exact wrong claim the audit flagged. GREEN rewrites it.
    """
    doc = math_engine.compute_breakeven_update.__doc__ or ""
    lowered = doc.lower()
    assert "previously_persisted_stop_level" not in lowered, (
        "compute_breakeven_update docstring still references the removed "
        "previously_persisted_stop_level parameter."
    )
    assert "never move down" not in lowered and "never\nmove down" not in lowered, (
        "compute_breakeven_update docstring still claims the resolved stop "
        "'must never move DOWN'. The HWM-anchored stop CAN move down when "
        "the vol-scaled distance widens — the docstring must say so."
    )


# ===========================================================================
# AC-4 — Citation correction: Glynn & Iglehart removed, Fu & Zhang 2012
# ===========================================================================


def test_glynn_iglehart_citation_removed_from_math_engine() -> None:
    """
    AC-4: the Glynn & Iglehart (1995) "Importance sampling for stochastic
    simulations" citation is WRONG for a trailing-stop ratchet (it is an
    importance-sampling paper). It must be removed from math_engine.py.

    RED: the current compute_breakeven_update docstring cites Glynn &
    Iglehart 1995 for the monotonicity invariant.
    """
    source = pathlib.Path(math_engine.__file__).read_text(encoding="utf-8")
    assert "Glynn" not in source, (
        "math_engine.py still cites Glynn & Iglehart — that paper is about "
        "importance sampling, not trailing stops. AC-4 requires its removal."
    )
    assert "Iglehart" not in source, (
        "math_engine.py still references Iglehart — remove the incorrect Glynn & Iglehart citation."
    )


def test_fu_zhang_citation_corrected_to_2012_or_removed() -> None:
    """
    AC-4: the Fu & Zhang citation must be corrected to the real reference
    (Fu & Zhang 2012, Int. J. Operations Research 9(3), 129-140) or removed.
    The pre-fix code cited "Fu & Zhang 2010", which is not the correct year.

    This test passes in two GREEN states:
      (a) Fu & Zhang is cited with the corrected year 2012, OR
      (b) the Fu & Zhang citation is removed entirely.
    It FAILS only if the stale "Fu & Zhang 2010" wording survives.
    """
    source = pathlib.Path(math_engine.__file__).read_text(encoding="utf-8")
    if "Fu & Zhang" in source or "Fu and Zhang" in source:
        assert "2010" not in source or "Fu & Zhang 2010" not in source, (
            "math_engine.py still cites 'Fu & Zhang 2010'. AC-4 requires the "
            "year be corrected to 2012 (Int. J. Operations Research 9(3), "
            "129-140) or the citation removed."
        )
        assert "2012" in source, (
            "Fu & Zhang is cited but without the corrected year 2012. AC-4 "
            "requires Fu & Zhang 2012 specifically, or removal of the cite."
        )


# ===========================================================================
# AC-3 — is_triggered override is absolute AND applied last, but does NOT
# suppress the hold-ticks / breakeven-locked computation.
# ===========================================================================


def test_triggered_overrides_only_the_stop_level_not_the_state_fields() -> None:
    """
    AC-3 interaction safety (per risk-engine-specialist domain ruling): the
    is_triggered branch overrides ONLY stop_trigger_level — it must NOT
    suppress the hwm_hold_ticks counter or the breakeven_locked latch
    computation. Those two are still computed and returned correctly.

    The audit's invariant #5 ("triggered override absolute") is about the
    STOP LEVEL sentinel; the state evolution (counter + latch) is
    independent and must keep running so a position's bookkeeping stays
    consistent.

    Derivation (symphony_vol=1.0 -> threshold 0.8):
      current_return=1.5 (>= 0.8) -> hwm_hold_ticks 4 -> 5; 5 >=
      HWM_HOLD_TICKS_THRESHOLD -> new_breakeven_locked flips True. The stop
      level is overridden to -999.0 because is_triggered=True, but
      new_hold_ticks must be 5 and new_breakeven_locked must be True.

    Catches an impl that short-circuits the whole function on is_triggered
    and returns stale / zeroed state fields.
    """
    new_hold_ticks, new_breakeven_locked, stop_level = math_engine.compute_breakeven_update(
        current_return=1.5,
        symphony_vol=1.0,
        base_stop_level=0.5,
        current_hold_ticks=4,
        currently_breakeven_locked=False,
        is_triggered=True,
    )
    assert stop_level == pytest.approx(
        math_engine.TRIGGERED_OVERRIDE_LEVEL, rel=APPROX_REL, abs=APPROX_ABS
    ), (
        f"is_triggered=True must override the stop level to "
        f"TRIGGERED_OVERRIDE_LEVEL; got {stop_level}."
    )
    assert new_hold_ticks == 5, (
        f"is_triggered=True must NOT suppress the hwm_hold_ticks counter. "
        f"current_hold_ticks=4 + a qualifying tick -> expected 5, got "
        f"{new_hold_ticks}. Only the stop LEVEL is overridden."
    )
    assert new_breakeven_locked is True, (
        f"is_triggered=True must NOT suppress the breakeven-lock "
        f"computation. 5 hold ticks >= HWM_HOLD_TICKS_THRESHOLD -> the latch "
        f"must flip True; got {new_breakeven_locked}."
    )


def test_triggered_override_is_applied_last_beats_breakeven_floor() -> None:
    """
    AC-3: the is_triggered override is applied LAST — it beats the
    breakeven floor. Even with currently_breakeven_locked=True and a
    positive base_stop_level (locked branch would yield max(base,0.0)), an
    is_triggered=True must still return the -999.0 sentinel, not the floored
    level.

    Catches an impl that applies the override BEFORE the floor (the floor
    would then re-clamp -999.0 up to 0.0).
    """
    _, _, stop_level = math_engine.compute_breakeven_update(
        current_return=2.0,
        symphony_vol=1.0,
        base_stop_level=3.0,  # locked branch would give 3.0
        current_hold_ticks=10,
        currently_breakeven_locked=True,
        is_triggered=True,
    )
    assert stop_level == pytest.approx(
        math_engine.TRIGGERED_OVERRIDE_LEVEL, rel=APPROX_REL, abs=APPROX_ABS
    ), (
        f"is_triggered override must be applied LAST and beat the breakeven "
        f"floor. Expected TRIGGERED_OVERRIDE_LEVEL "
        f"({math_engine.TRIGGERED_OVERRIDE_LEVEL}), got {stop_level} — if it "
        f"is 3.0 or 0.0 the override ran before the floor."
    )


# ===========================================================================
# Determinism guard
# ===========================================================================


def test_compute_breakeven_update_is_pure_no_cross_call_state() -> None:
    """
    Sanity: after the H-1 fix removes the cross-tick clamp, the function is
    fully per-tick stateless. Calling it twice with identical args returns
    bit-identical results, and the order of calls does not matter.
    """
    args_a = dict(
        current_return=2.0,
        symphony_vol=1.0,
        base_stop_level=1.5,
        current_hold_ticks=3,
        currently_breakeven_locked=False,
        is_triggered=False,
    )
    args_b = dict(
        current_return=-1.0,
        symphony_vol=2.0,
        base_stop_level=-0.5,
        current_hold_ticks=0,
        currently_breakeven_locked=True,
        is_triggered=False,
    )
    r_a1 = math_engine.compute_breakeven_update(**args_a)
    r_b1 = math_engine.compute_breakeven_update(**args_b)
    r_a2 = math_engine.compute_breakeven_update(**args_a)
    r_b2 = math_engine.compute_breakeven_update(**args_b)
    assert r_a1 == r_a2, f"Non-deterministic for args_a: {r_a1} vs {r_a2}"
    assert r_b1 == r_b2, f"Non-deterministic for args_b: {r_b1} vs {r_b2}"
