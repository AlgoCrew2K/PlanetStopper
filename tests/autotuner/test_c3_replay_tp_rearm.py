"""
Cluster 3 AC-3 — the autotuner replay's take-profit confirm counter must
reset on an MC-UNAVAILABLE tick mid-position, matching production.

AUDIT-FINDING-9 CORRECTION
--------------------------
The math audit (finding 9) described this as "missing reset on a sub-threshold
dip" — production resets above_tp_count when MC dips back below
TAKE_PROFIT_MC_PCT, the replay does not. A verified tick-by-tick trace of BOTH
TP machines against the live code disproves that:

  sub-threshold dip, mc=[2,8,3,8] all MC-available:
    production: above_tp_count = 0,1,1,2 -> TP exit on tick 3
    replay:     above_tp_count = 0,1,1,2 -> TP exit on tick 3   (IDENTICAL)

Production's `if mc_available and prob_beating < TAKE_PROFIT_MC_PCT` branch
does `if not tp_armed: ...` — when already tp_armed it does nothing; the
counter is NOT reset. Same as the replay. There is no sub-threshold-dip
divergence.

The REAL divergence is the MC-UNAVAILABLE case. Production's
`elif tp_armed: ... else: above_tp_count = 0` (alpha_bot_execution.py:1298-
1301) is reached ONLY when MC is unavailable (None) while tp_armed — and it
RESETS the counter:

  mc=[2,8,None,8], tick 2 MC-unavailable:
    production: above_tp_count = 0,1,0,1 -> NO TP exit (None resets it)
    replay:     0,1,2,3 -> PHANTOM TP exit (synthetic_history substitutes
                22.5 >= TAKE_PROFIT_MC_PCT, so the counter increments)

So finding 9's defect is real but mislabeled — it is an insufficient-MC
divergence, coupled to AC-5 (the MC_INSUFFICIENT_REPLAY_VALUE=22.5 substitute).
This file tests the ACTUAL behaviour: an MC-unavailable tick mid-position must
reset the TP confirm counter, exactly as production does.

Plan D-C3a still prefers extracting a shared
`math_engine.compute_tp_confirmation` for single-source-of-truth on the
confirm-count literal (2); a dedicated test asserts it is wired into both
consumers IF the team takes that route. The AC-6 parity test guards the
behaviour either way.

RED EXPECTATION: against pre-fix code
`test_replay_tp_counter_resets_on_mc_unavailable_tick` fails — the replay
fires a phantom TP exit across an MC-unavailable tick.
"""

from __future__ import annotations

import json
import pathlib

import pytest

import autotuner


_FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "autotuner"
    / "replay_parity"
)


def _load_fixture(name: str) -> dict:
    with (_FIXTURE_DIR / name).open(encoding="utf-8") as fh:
        return json.load(fh)


def _default_params() -> dict:
    return {
        "TRIGGER_THRESHOLD_PCT": 15.0,
        "TAKE_PROFIT_MC_PCT": 5.0,
        "VWAP_CROSS_HWM_PCT": 99.0,   # VWAP never arms — isolate the TP path
        "VWAP_BLEED_MULTIPLIER": 1.5,
        "VWAP_BLEED_TICKS": 999,
        "PARABOLIC_VELOCITY_THRESHOLD": 99.0,
        "MAX_PARABOLIC_SQUEEZE": 0.5,
    }


def _replay_seq(ticks: list[dict], params: dict, grace_minutes: int = 0) -> list[dict]:
    if not hasattr(autotuner, "replay_exit_sequence"):
        pytest.fail(
            "autotuner.replay_exit_sequence missing — required for AC-3/AC-6 "
            "exit-decision parity tests."
        )
    return autotuner.replay_exit_sequence(
        ticks, params, grace_minutes=grace_minutes
    )


# ===========================================================================
# AC-3 — behavioural: the TP counter resets on an MC-unavailable tick.
# ===========================================================================


def test_replay_tp_counter_resets_on_mc_unavailable_tick() -> None:
    """AC-3 (corrected): a TP exit must NOT confirm on two above-threshold
    ticks separated by an MC-UNAVAILABLE tick.

    Fixture parity_tp_rearm_dip.json, TAKE_PROFIT_MC_PCT=5:
      tick 0: mc 2     -> tp_armed, above_tp_count = 0
      tick 1: mc 8     -> above_tp_count = 1
      tick 2: mc null  -> MC unavailable -> above_tp_count MUST reset to 0
      tick 3: mc 8     -> above_tp_count = 1  (NOT 2 — the None tick reset it)
    Production produces NO TP exit. Pre-fix the replay substitutes 22.5 for
    the null tick, increments the counter, and fires a phantom TP exit.

    RED: pre-fix replay fires "Take-Profit".
    """
    fx = _load_fixture("parity_tp_rearm_dip.json")
    ticks = fx["ticks"]
    params = fx.get("params") or _default_params()

    seq = _replay_seq(ticks, params)
    tp_exits = [
        (d["tick_idx"], d["exit_reason"])
        for d in seq
        if d["exit_reason"] and "Take-Profit" in d["exit_reason"]
    ]
    assert not tp_exits, (
        f"Replay confirmed a Take-Profit exit {tp_exits} despite an "
        f"intervening MC-UNAVAILABLE tick. Production resets above_tp_count "
        f"to 0 when MC is absent (its `elif tp_armed: ... else` branch); the "
        f"replay must do the same — an absent MC opinion cannot count toward "
        f"a TP confirmation. The pre-fix replay substitutes 22.5 for the "
        f"None tick and wrongly increments the counter."
    )


def test_replay_tp_exit_still_fires_on_consecutive_above_threshold() -> None:
    """AC-3 (the reset must not break a LEGITIMATE TP exit): two CONSECUTIVE
    above-threshold ticks (MC available) while tp_armed, with return > 0,
    must still confirm a TP exit.

    Guards against an over-broad fix that never lets a TP exit fire.

    TAKE_PROFIT_MC_PCT=5:
      tick 0: mc 2 -> tp_armed
      tick 1: mc 8 (ret>0) -> above_tp_count = 1
      tick 2: mc 8 (ret>0) -> above_tp_count = 2 -> TP exit confirms
    """
    params = _default_params()
    ticks = [
        {"time": "09:30", "return": 3.0, "mc_prob": 2.0, "vol": 0.5,
         "vwap_diff": 0.0, "base_atr_pct": 0.5, "valid_vwap_weight": 0.0},
        {"time": "09:31", "return": 3.0, "mc_prob": 8.0, "vol": 0.5,
         "vwap_diff": 0.0, "base_atr_pct": 0.5, "valid_vwap_weight": 0.0},
        {"time": "09:32", "return": 3.0, "mc_prob": 8.0, "vol": 0.5,
         "vwap_diff": 0.0, "base_atr_pct": 0.5, "valid_vwap_weight": 0.0},
    ]
    seq = _replay_seq(ticks, params)
    exits = [d for d in seq if d["exit_reason"]]
    assert exits, (
        "Two consecutive above-threshold ticks (MC available) while tp_armed "
        "(return > 0) must confirm a Take-Profit exit. The AC-3 reset fix "
        "must not break a legitimate consecutive-tick TP confirmation."
    )
    assert exits[0]["exit_reason"] == "Take-Profit", (
        f"Expected a Take-Profit exit; got {exits[0]['exit_reason']!r}."
    )
    assert exits[0]["tick_idx"] == 2, (
        f"TP exit must confirm on tick 2 (2nd consecutive above-threshold "
        f"tick); fired on tick {exits[0]['tick_idx']}."
    )


def test_replay_tp_sub_threshold_dip_matches_production_no_special_reset() -> None:
    """AC-3 (anti-regression / audit correction pin): a sub-threshold MC DIP
    (MC available, mc back below TAKE_PROFIT_MC_PCT) is NOT a divergence —
    production does not reset above_tp_count there, and neither must the
    replay. mc=[2,8,3,8] all available -> both fire a TP exit on tick 3.

    This pins the corrected understanding of audit finding 9: a fix that
    'resets on a sub-threshold dip' would itself be a NEW divergence from
    production. The replay must match production: counter survives a
    sub-threshold dip while MC is available.
    """
    params = _default_params()
    ticks = [
        {"time": "09:30", "return": 3.0, "mc_prob": 2.0, "vol": 0.5,
         "vwap_diff": 0.0, "base_atr_pct": 0.5, "valid_vwap_weight": 0.0},
        {"time": "09:31", "return": 3.0, "mc_prob": 8.0, "vol": 0.5,
         "vwap_diff": 0.0, "base_atr_pct": 0.5, "valid_vwap_weight": 0.0},
        {"time": "09:32", "return": 3.0, "mc_prob": 3.0, "vol": 0.5,
         "vwap_diff": 0.0, "base_atr_pct": 0.5, "valid_vwap_weight": 0.0},
        {"time": "09:33", "return": 3.0, "mc_prob": 8.0, "vol": 0.5,
         "vwap_diff": 0.0, "base_atr_pct": 0.5, "valid_vwap_weight": 0.0},
    ]
    seq = _replay_seq(ticks, params)
    tp_exits = [
        d for d in seq if d["exit_reason"] and "Take-Profit" in d["exit_reason"]
    ]
    assert tp_exits, (
        "A sub-threshold MC dip while MC is AVAILABLE must NOT reset the TP "
        "counter — production keeps it, so mc=[2,8,3,8] confirms a TP exit "
        "on tick 3. The replay must match production: the AC-3 fix targets "
        "only the MC-UNAVAILABLE case, not an available-MC sub-threshold dip."
    )
    assert tp_exits[0]["tick_idx"] == 3, (
        f"TP exit must confirm on tick 3 (matching production's counter "
        f"sequence 0,1,1,2); fired on tick {tp_exits[0]['tick_idx']}."
    )


def test_replay_tp_disarm_on_above_threshold_with_nonpositive_return() -> None:
    """AC-3: production disarms TP (tp_armed=False, above_tp_count=0) when the
    2nd above-threshold tick has return <= 0. The replay must do the same.

    tick 0: mc 2          -> tp_armed
    tick 1: mc 8, ret 3   -> above_tp_count = 1
    tick 2: mc 8, ret -1  -> above_tp_count would reach 2 but ret <= 0:
                             TP disarms; NO exit fires
    """
    params = _default_params()
    ticks = [
        {"time": "09:30", "return": 3.0, "mc_prob": 2.0, "vol": 0.5,
         "vwap_diff": 0.0, "base_atr_pct": 0.5, "valid_vwap_weight": 0.0},
        {"time": "09:31", "return": 3.0, "mc_prob": 8.0, "vol": 0.5,
         "vwap_diff": 0.0, "base_atr_pct": 0.5, "valid_vwap_weight": 0.0},
        {"time": "09:32", "return": -1.0, "mc_prob": 8.0, "vol": 0.5,
         "vwap_diff": 0.0, "base_atr_pct": 0.5, "valid_vwap_weight": 0.0},
    ]
    seq = _replay_seq(ticks, params)
    tp_exits = [
        d for d in seq if d["exit_reason"] and "Take-Profit" in d["exit_reason"]
    ]
    assert not tp_exits, (
        "A 2nd above-threshold tick with return <= 0 must DISARM take-profit "
        "(no exit), matching production's TP-DISARMED branch — not confirm "
        f"a TP exit. Got {[(d['tick_idx'], d['exit_reason']) for d in tp_exits]}."
    )


# ===========================================================================
# AC-3 — D-C3a: IF the team extracts the shared compute_tp_confirmation,
# assert it is wired into BOTH production and the replay.
#
# Skips if the team chooses fix-in-place (the plan permits either) — it does
# not fail the fix-in-place route. When the shared function exists it enforces
# that both consumers actually call it (no orphan helper).
# ===========================================================================


def test_shared_tp_confirmation_if_extracted_is_called_by_both_consumers() -> None:
    """AC-3 / D-C3a: if math_engine.compute_tp_confirmation is extracted, it
    must be CALLED by both alpha_bot_execution.py (production) and
    autotuner.py (replay) — a shared function nothing calls is dead code
    advertising a contract.

    Skips cleanly if the team chooses the fix-in-place route (no shared
    function) — the plan permits that since the AC-6 parity test guards the
    behaviour either way.
    """
    import math_engine

    if not hasattr(math_engine, "compute_tp_confirmation"):
        pytest.skip(
            "compute_tp_confirmation not extracted — team chose fix-in-place "
            "(permitted by plan D-C3a). AC-6 parity test guards the behaviour."
        )

    import ast as _ast

    def _calls_compute_tp(module_path: pathlib.Path) -> bool:
        tree = _ast.parse(module_path.read_text(encoding="utf-8"))
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Call):
                f = node.func
                if (isinstance(f, _ast.Attribute) and f.attr == "compute_tp_confirmation") \
                        or (isinstance(f, _ast.Name) and f.id == "compute_tp_confirmation"):
                    return True
        return False

    autotuner_path = pathlib.Path(autotuner.__file__)
    repo_root = autotuner_path.parent
    production_path = repo_root / "alpha_bot_execution.py"

    assert _calls_compute_tp(autotuner_path), (
        "compute_tp_confirmation exists but autotuner.py never calls it — "
        "the replay must use the shared TP function."
    )
    assert _calls_compute_tp(production_path), (
        "compute_tp_confirmation exists but alpha_bot_execution.py never "
        "calls it — production must use the shared TP function too, else "
        "extraction created a divergence surface instead of removing one."
    )
