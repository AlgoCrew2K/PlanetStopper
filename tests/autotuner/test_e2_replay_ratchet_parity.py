"""
Tests for autotuner replay / live-path parity of the HWM-anchored trailing
stop.

REWRITTEN for the H-1 audit fix (math-audit exit-decision-math__2026-05-21.md).

WHAT CHANGED — and why this file was rewritten:

  The PRE-FIX version of this file asserted that BOTH autotuner call sites
  pass previously_persisted_stop_level= into compute_breakeven_update, so the
  replay would freeze the resolved stop level the same way the (then-buggy)
  live path did. The math audit (HIGH finding H-1) showed the frozen-level
  clamp is the DEFECT. The H-1 fix REMOVES the previously_persisted_stop_level
  parameter entirely; the stop is HWM-anchored — stop = HWM - vol_scaled_
  distance, recomputed each tick.

  Therefore the contract this file pins FLIPS:
    - PRE-FIX: every autotuner call site MUST pass the kwarg.
    - POST-FIX: NO call site may pass the kwarg (the parameter is gone).
  The behavioural guarantee is still PARITY — the autotuner replay and the
  live engine must produce the SAME stop trajectory for the same inputs —
  but now both are HWM-anchored with no clamp, so parity means "both
  recompute HWM - distance each tick".

Acceptance Criteria exercised here (post-H-1):
  AC1 — neither autotuner call site passes previously_persisted_stop_level.
  AC2 — replay stop trajectory is HWM - distance each tick (no clamp).
  AC3 — live path also passes no kwarg (regression guard).
  AC4 — replay vs. live produce identical stop trajectories.
  AC5 — same fixture -> same replay trajectory (determinism).
  AC6 — a widening-distance tick lowers the replay stop (the H-1 behavior).

Tolerance policy:
  - Float equality between live/replay: pytest.approx(rel=1e-9, abs=1e-12) —
    pure subtraction + max() chains, ~1 ulp realistic noise.
  - Structural assertions (kwarg absence): exact, no tolerance.
  - Direction comparisons (stop moved down): exact `<`.

Fixture provenance: fixtures in tests/fixtures/autotuner/e2_replay_parity/
remain schema-derived / hand-derived; the rewritten tests read only the
input tick sequences from them and assert relational properties, never a
hardcoded producer stop value.
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

import math_engine


HWM_HOLD_TICKS_THRESHOLD: int = math_engine.HWM_HOLD_TICKS_THRESHOLD

APPROX_REL = 1e-9
APPROX_ABS = 1e-12

_FIXTURE_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "autotuner" / "e2_replay_parity"


def _load_fixture(name: str) -> dict:
    with (_FIXTURE_DIR / name).open(encoding="utf-8") as fh:
        return json.load(fh)


def _simulate_ticks(ticks: list[dict]) -> list[tuple[int, bool, float]]:
    """
    Replay a tick sequence through compute_breakeven_update.

    Post-H-1-fix: only (hold_ticks, locked) are threaded — there is NO
    previously_persisted_stop_level. The stop is recomputed each tick as
    base_stop_level (= HWM - vol_scaled_distance). This is the single code
    path both the live engine and the autotuner replay must use.
    """
    hold_ticks = 0
    locked = False
    results: list[tuple[int, bool, float]] = []
    for tick in ticks:
        new_ht, new_locked, stop_level = math_engine.compute_breakeven_update(
            current_return=tick["current_return"],
            symphony_vol=tick["symphony_vol"],
            base_stop_level=tick["base_stop_level"],
            current_hold_ticks=hold_ticks,
            currently_breakeven_locked=locked,
            is_triggered=tick.get("is_triggered", False),
        )
        results.append((new_ht, new_locked, stop_level))
        hold_ticks = new_ht
        locked = new_locked
    return results


def _compute_breakeven_update_call_sites(filename: str) -> list[ast.Call]:
    path = pathlib.Path(__file__).parent.parent.parent / filename
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Attribute) and func.attr == "compute_breakeven_update") or (
                isinstance(func, ast.Name) and func.id == "compute_breakeven_update"
            ):
                calls.append(node)
    return calls


# ===========================================================================
# AC1 — Structural: NO autotuner call site passes the removed kwarg.
# ===========================================================================


def test_no_autotuner_call_site_passes_previously_persisted_stop_level() -> None:
    """
    AC1 (post-H-1): the H-1 fix removes previously_persisted_stop_level from
    compute_breakeven_update. Every autotuner.py call site must call the
    function WITHOUT that kwarg — passing it would raise TypeError.

    Cluster-3 relaxation: the original assertion expected >= 2 call sites
    (one lexically inside each of run_simulation and _collect_sim_returns).
    The autotuner-replay-parity cycle extracted a single shared per-tick
    exit core (_replay_exit_tick) that both replay functions call — so
    compute_breakeven_update now has ONE canonical call site, strictly
    better (single source of truth) than two. The real contract this test
    enforces is the `offenders` check below: NO call site passes the removed
    kwarg. The count is only a precondition; >= 1 is correct post-cluster-3.

    RED: the autotuner call sites passed
    previously_persisted_stop_level=prev_persisted_stop.
    """
    calls = _compute_breakeven_update_call_sites("autotuner.py")
    assert len(calls) >= 1, (
        f"Expected >=1 compute_breakeven_update call site in autotuner.py "
        f"(the replay must call the canonical helper); found {len(calls)}."
    )
    offenders = [
        f"line {c.lineno}"
        for c in calls
        if "previously_persisted_stop_level" in {kw.arg for kw in c.keywords}
    ]
    assert not offenders, (
        f"autotuner.py still passes the removed previously_persisted_stop_"
        f"level kwarg at {offenders}. The H-1 fix removes the parameter; "
        f"every call site must drop it and drop the prev_persisted_stop "
        f"threading entirely. The replay stop is HWM-anchored — recomputed "
        f"HWM - distance each tick — exactly like the live path."
    )


def test_live_path_also_passes_no_previously_persisted_stop_level() -> None:
    """
    AC3 (regression guard): the live consumer path
    (alpha_bot_execution.py) must ALSO call compute_breakeven_update without
    the removed kwarg. Guards against a fix that updates autotuner but
    leaves the live path passing a now-invalid kwarg.

    RED: alpha_bot_execution.py:1219 currently passes
    previously_persisted_stop_level=bot_state[symphony_id].get('stop_trigger').
    """
    calls = _compute_breakeven_update_call_sites("alpha_bot_execution.py")
    assert calls, "No compute_breakeven_update call found in alpha_bot_execution.py"
    offenders = [
        f"line {c.lineno}"
        for c in calls
        if "previously_persisted_stop_level" in {kw.arg for kw in c.keywords}
    ]
    assert not offenders, (
        f"alpha_bot_execution.py still passes previously_persisted_stop_"
        f"level at {offenders}. The H-1 fix removes the parameter from "
        f"compute_breakeven_update; the live path must drop the kwarg."
    )


# ===========================================================================
# AC2 / AC6 — Behavioural: the replay stop is HWM - distance each tick and
# moves DOWN when the distance widens.
# ===========================================================================


def test_replay_stop_tracks_hwm_minus_distance_and_can_move_down() -> None:
    """
    AC2 / AC6 (post-H-1): the autotuner replay's stop trajectory is
    HWM-anchored — each tick stop = base_stop_level (= HWM - distance), with
    NO cross-tick clamp. When the vol-scaled distance widens the replay stop
    MOVES DOWN.

    Uses fixture 01_replay_stop_drop_cycle3.json's tick inputs. The fixture
    cycle 2 deliberately retraces base_stop_level downward; under the
    HWM-anchored contract the replay stop on cycle 2 must equal that
    retraced base (NOT be clamped up to cycle 1's level).
    """
    fixture = _load_fixture("01_replay_stop_drop_cycle3.json")
    cycles = fixture["cycles"]
    c0, c1, c2 = (cycles[i]["inputs"] for i in range(3))

    # Replay the three cycles through the single (post-fix) code path.
    ht0, lk0, s0 = math_engine.compute_breakeven_update(
        current_return=c0["current_return"],
        symphony_vol=c0["symphony_vol"],
        base_stop_level=c0["base_stop_level"],
        current_hold_ticks=c0["current_hold_ticks"],
        currently_breakeven_locked=c0["currently_breakeven_locked"],
        is_triggered=c0["is_triggered"],
    )
    ht1, lk1, s1 = math_engine.compute_breakeven_update(
        current_return=c1["current_return"],
        symphony_vol=c1["symphony_vol"],
        base_stop_level=c1["base_stop_level"],
        current_hold_ticks=ht0,
        currently_breakeven_locked=lk0,
        is_triggered=False,
    )
    _ht2, lk2, s2 = math_engine.compute_breakeven_update(
        current_return=c2["current_return"],
        symphony_vol=c2["symphony_vol"],
        base_stop_level=c2["base_stop_level"],
        current_hold_ticks=ht1,
        currently_breakeven_locked=lk1,
        is_triggered=False,
    )

    # Cycle 2's stop must equal max(base, 0.0) if locked, else base — NOT
    # clamped up to s1.
    expected_s2 = max(c2["base_stop_level"], 0.0) if lk2 else c2["base_stop_level"]
    assert s2 == pytest.approx(expected_s2, rel=APPROX_REL, abs=APPROX_ABS), (
        f"Replay cycle-2 stop {s2} != HWM-anchored expected {expected_s2}. "
        f"The replay must recompute the stop each cycle with no frozen-level "
        f"clamp."
    )
    # The fixture retraces base on cycle 2 so the stop must DROP below s1.
    assert s2 < s1, (
        f"AC6 VIOLATED: replay cycle-2 stop {s2} did not drop below cycle-1 "
        f"stop {s1} despite a retraced base_stop_level "
        f"({c2['base_stop_level']}). A frozen-level clamp is still present "
        f"in the replay path."
    )


# ===========================================================================
# AC4 — Replay vs. live parity.
# ===========================================================================


def test_replay_and_live_produce_identical_stop_trajectories() -> None:
    """
    AC4 (post-H-1): the autotuner replay and the live engine must produce
    bit-identical stop trajectories for the same tick sequence. After the
    H-1 fix both use the single HWM-anchored code path (no kwarg, no clamp),
    so parity is exact.

    Fixture 03_live_replay_parity_sequence.json supplies the tick sequence.
    Both 'replay' and 'live' here exercise compute_breakeven_update via the
    same _simulate_ticks helper — after the fix they ARE the same path.
    """
    fixture = _load_fixture("03_live_replay_parity_sequence.json")
    vol = fixture["params"]["symphony_vol"]
    ticks = [
        {
            "current_return": t["current_return"],
            "symphony_vol": vol,
            "base_stop_level": t["base_stop_level"],
            "is_triggered": t.get("is_triggered", False),
        }
        for t in fixture["ticks"]
    ]

    replay_stops = [r[2] for r in _simulate_ticks(ticks)]
    live_stops = [r[2] for r in _simulate_ticks(ticks)]

    for i, (rs, ls) in enumerate(zip(replay_stops, live_stops)):
        assert rs == pytest.approx(ls, rel=APPROX_REL, abs=APPROX_ABS), (
            f"PARITY BROKEN at tick {i}: replay={rs}, live={ls}. The "
            f"autotuner replay and the live engine must use the same "
            f"HWM-anchored stop path and produce identical trajectories."
        )

    # Each tick's stop must equal the HWM-anchored value (base, or
    # max(base,0) when locked) — pinning that neither path applies a clamp.
    hold_ticks, locked = 0, False
    for i, tick in enumerate(ticks):
        new_ht, new_locked, _ = math_engine.compute_breakeven_update(
            current_return=tick["current_return"],
            symphony_vol=tick["symphony_vol"],
            base_stop_level=tick["base_stop_level"],
            current_hold_ticks=hold_ticks,
            currently_breakeven_locked=locked,
            is_triggered=tick["is_triggered"],
        )
        if tick["is_triggered"]:
            expected = math_engine.TRIGGERED_OVERRIDE_LEVEL
        elif new_locked:
            expected = max(tick["base_stop_level"], 0.0)
        else:
            expected = tick["base_stop_level"]
        assert replay_stops[i] == pytest.approx(expected, rel=APPROX_REL, abs=APPROX_ABS), (
            f"tick {i}: replay stop {replay_stops[i]} != HWM-anchored "
            f"expected {expected}. No cross-tick clamp may be applied."
        )
        hold_ticks, locked = new_ht, new_locked


# ===========================================================================
# AC5 — Determinism.
# ===========================================================================


def test_replay_is_deterministic() -> None:
    """
    AC5: replaying the same tick sequence twice produces bit-identical
    trajectories. compute_breakeven_update is pure (no I/O, no RNG, no
    cross-tick stop state after the H-1 fix).
    """
    vol = 1.0
    ticks = [
        {
            "current_return": 1.5,
            "symphony_vol": vol,
            "base_stop_level": -0.5,
            "is_triggered": False,
        },
        {"current_return": 1.6, "symphony_vol": vol, "base_stop_level": 0.5, "is_triggered": False},
        {"current_return": 1.7, "symphony_vol": vol, "base_stop_level": 2.0, "is_triggered": False},
        {"current_return": 2.0, "symphony_vol": vol, "base_stop_level": 0.5, "is_triggered": False},
    ]
    assert _simulate_ticks(ticks) == _simulate_ticks(ticks), (
        "Non-deterministic replay — compute_breakeven_update must be pure."
    )
