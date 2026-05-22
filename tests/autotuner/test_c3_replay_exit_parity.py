"""
Cluster 3 — Autotuner replay / production EXIT-DECISION parity.

This is the CENTERPIECE guard of the cluster (plan AC-6). The autotuner's
walk-forward simulation (`autotuner.run_simulation` / `_collect_sim_returns`)
is the objective function every deployed parameter set is selected against.
If the replay's exit logic diverges from the production exit path, the
autotuner optimizes a different system than the one trading real money.

WHAT THIS FILE PROVES
---------------------
Drive ONE tick sequence through BOTH:
  * a faithful production exit-path harness (`_production_exit_sequence`), and
  * the autotuner replay's own per-tick loop (exercised in-process), via the
    real `autotuner` module functions.
Assert the exit-decision sequence — WHICH trigger fired, on WHICH tick —
is bit-identical.

Both sides MUST call the real `math_engine` primitives
(`compute_exit_confirmation`, `compute_vwap_breakdown_update`,
`is_in_open_window_grace`/its tick-index equivalent, the take-profit
confirmation). Nothing in `math_engine` is mocked here — mocking the math
engine would defeat the entire purpose of a parity guard.

RED EXPECTATION (pre-fix)
-------------------------
Against pre-fix `autotuner.py` this file FAILS because:
  * AC-1: the replay open-codes the trailing-stop exit
    (`ret <= (stop_level - 0.10) and mc < 60.0 ... below_stop_count >= 3`)
    instead of calling `math_engine.compute_exit_confirmation`.
  * AC-2: the replay never suppresses VWAP signals in the open-window grace
    period, so it fires phantom early-session VWAP exits production would
    not take.
  * AC-3: the replay's take-profit re-arm keeps `above_tp_count` across a
    sub-threshold MC dip (the inner reset is unreachable), so it can confirm
    a TP exit on two NON-consecutive above-threshold ticks.

Tolerance policy
----------------
  * Exit-decision identity (which trigger / which tick): EXACT — these are
    discrete categorical outcomes, no float tolerance applies.
  * Any numeric comparison of returns/levels uses pytest.approx with an
    explicit absolute tolerance and a comment; the parity assertions
    themselves are discrete and need none.

Fixture provenance
------------------
Fixtures under tests/fixtures/autotuner/replay_parity/ are hand-derived
INPUT tick sequences (schema-derived: the replay tick schema is
`{time, return, mc_prob, vol, vwap_diff, base_atr_pct, valid_vwap_weight}`,
read directly from synthetic_history.py). The tests read ONLY the inputs and
assert relational identity between the two code paths — no producer-computed
exit value is hardcoded.
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import autotuner
import math_engine


# ---------------------------------------------------------------------------
# Constants sourced from the producers (never re-typed as bare literals).
# ---------------------------------------------------------------------------

_FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "autotuner"
    / "replay_parity"
)

# Production-side names re-exported so the harness reads them, not literals.
_MC_SANITY_THRESHOLD = math_engine.MC_SANITY_THRESHOLD          # 60.0
_EXIT_CONFIRM_TICKS = math_engine.EXIT_CONFIRM_TICKS            # 3
_MAGNITUDE_FLOOR_PCT = math_engine.MAGNITUDE_FLOOR_PCT          # 0.10
_VWAP_BREAK_CONFIRM_TICKS = math_engine.VWAP_BREAK_CONFIRM_TICKS  # 3

_ET = ZoneInfo("America/New_York")


def _load_fixture(name: str) -> dict:
    with (_FIXTURE_DIR / name).open(encoding="utf-8") as fh:
        return json.load(fh)


# ===========================================================================
# Production-side exit-decision harness.
#
# This re-creates the production per-tick exit decision (alpha_bot_execution.py
# lines ~1129-1430) by calling the SAME math_engine primitives the engine
# calls. It is the reference the replay must match. It is intentionally a
# focused re-expression of the engine loop — NOT a mock — and every exit
# primitive it invokes is the real math_engine function.
# ===========================================================================


def _production_exit_sequence(
    ticks: list[dict],
    params: dict,
    *,
    session_open_hhmm: str = "09:30",
    grace_minutes: int,
) -> list[dict]:
    """Drive a tick sequence through the production exit path.

    Returns one dict per tick: {tick_idx, exit_reason or None}. exit_reason
    is the resolve_trigger_priority string on the tick the position exits;
    None on every non-exit tick. After an exit fires the loop stops (the
    production engine commits the exit and freezes the symphony for the day).
    """
    hwm = -999.0
    armed = False
    tp_armed = False
    vwap_ticks = 0
    vwap_bleed_ticks = 0
    para_armed = False
    breakeven_locked = False
    prev_return = None
    hwm_hold_ticks = 0
    below_stop_count = 0
    above_tp_count = 0

    take_profit_mc = params["TAKE_PROFIT_MC_PCT"]
    trigger_threshold = params["TRIGGER_THRESHOLD_PCT"]

    # Production minute timeline: tick_idx 0 == session open.
    base_open = datetime(2026, 4, 6, 9, 30, tzinfo=_ET)
    h, m = map(int, session_open_hhmm.split(":"))
    base_open = base_open.replace(hour=h, minute=m)

    out: list[dict] = []
    for tick_idx, tick in enumerate(ticks):
        ret = tick.get("return", 0.0)
        # mc_prob may be the None sentinel (MC unavailable / insufficient
        # history) — production's run_monte_carlo None contract. mc_available
        # gates every MC-driven branch exactly as production does.
        mc = tick.get("mc_prob", 50.0)
        mc_available = mc is not None
        vol = tick.get("vol", 1.0)
        vwap_diff = tick.get("vwap_diff", 0.0)
        valid_vwap_weight = tick.get("valid_vwap_weight", 1.0)

        if ret > hwm:
            hwm = ret
        safe_hwm = max(hwm, ret)

        para_threshold = params.get("PARABOLIC_VELOCITY_THRESHOLD", 2.0)
        effective_prev = ret if prev_return is None else prev_return
        _velocity, should_arm = math_engine.compute_para_arm_decision(
            current_return=ret,
            prev_return=effective_prev,
            para_threshold=para_threshold,
            currently_armed=para_armed,
        )
        prev_return = ret
        if should_arm:
            para_armed = True

        # MC arm / disarm — gated on mc_available (alpha_bot_execution.py:
        # 1140-1163). An absent MC opinion drives no arm and no disarm.
        if mc_available and take_profit_mc <= mc < trigger_threshold:
            if not armed:
                armed = True
        elif armed:
            if mc_available and mc > (trigger_threshold * 2) and ret > 0.0:
                armed = False
                below_stop_count = 0

        time_ratio = tick_idx / max(1, len(ticks) - 1)
        dynamic_multiplier, dynamic_min_stop = math_engine.compute_time_squeeze_decay(
            time_ratio
        )
        active_stop_dist = math_engine.compute_active_trailing_stop(
            vol, dynamic_multiplier, dynamic_min_stop,
            para_armed, breakeven_locked, params.get("MAX_PARABOLIC_SQUEEZE", 0.50),
        )
        base_stop = safe_hwm - active_stop_dist
        hwm_hold_ticks, breakeven_locked, stop_level = math_engine.compute_breakeven_update(
            ret, vol, base_stop, hwm_hold_ticks, breakeven_locked, False,
        )

        # Check 1: Trailing Stop — the real production primitive.
        below_stop_count, is_trailing_hit = math_engine.compute_exit_confirmation(
            armed=armed,
            is_triggered=False,
            current_return=ret,
            stop_trigger_level=stop_level,
            prob_beating=mc,
            current_below_stop_count=below_stop_count,
        )

        # Check 2: Take Profit — the REAL production primitive.
        # Production (alpha_bot_execution.py) was refactored this cluster
        # (D-C3a) to call math_engine.compute_tp_confirmation; so this
        # harness calls the SAME function, exactly as Check 1 calls the real
        # compute_exit_confirmation and Check 3 the real
        # compute_vwap_breakdown_update. The parity test must exercise the
        # production code path, never a hand-written copy of it.
        tp_armed, above_tp_count, is_tp_hit = math_engine.compute_tp_confirmation(
            mc_available=mc_available,
            prob_beating=mc,
            take_profit_mc_pct=take_profit_mc,
            current_return=ret,
            is_triggered=False,
            tp_armed=tp_armed,
            above_tp_count=above_tp_count,
        )

        # Check 3: VWAP Breakdown — real primitive + grace suppression.
        vwap_bleed_arm_pct = math_engine.compute_vwap_bleed_arm_threshold(
            vol, params.get("VWAP_BLEED_MULTIPLIER", 1.5)
        )
        vwap_ticks, vwap_bleed_ticks, is_vwap_broken, is_vwap_bleed_broken = (
            math_engine.compute_vwap_breakdown_update(
                is_triggered=False,
                valid_vwap_weight=valid_vwap_weight,
                weighted_vwap_diff=vwap_diff,
                safe_hwm=safe_hwm,
                current_return=ret,
                vwap_cross_hwm_pct=params.get("VWAP_CROSS_HWM_PCT", 1.0),
                vwap_bleed_arm_pct=vwap_bleed_arm_pct,
                vwap_bleed_ticks_threshold=params.get("VWAP_BLEED_TICKS", 10),
                current_vwap_ticks=vwap_ticks,
                current_vwap_bleed_ticks=vwap_bleed_ticks,
            )
        )
        current_et = base_open + timedelta(minutes=tick_idx)
        if math_engine.is_in_open_window_grace(
            current_et, session_open_hhmm, grace_minutes
        ):
            is_vwap_broken = False
            is_vwap_bleed_broken = False

        if is_trailing_hit or is_tp_hit or is_vwap_broken or is_vwap_bleed_broken:
            reason, _ = math_engine.resolve_trigger_priority(
                is_vwap_broken=is_vwap_broken,
                is_tp_hit=is_tp_hit,
                is_vwap_bleed_broken=is_vwap_bleed_broken,
                is_trailing_stop_hit=is_trailing_hit,
            )
            out.append({"tick_idx": tick_idx, "exit_reason": reason})
            return out

        out.append({"tick_idx": tick_idx, "exit_reason": None})

    return out


# ---------------------------------------------------------------------------
# Replay-side exit-decision extraction.
#
# AC-6 requires asserting the exit decision PER TICK. run_simulation collapses
# the day to a scalar guard-alpha; it does NOT return the tick-by-tick exit
# trace, so it cannot be parity-checked tick-for-tick directly. The
# implementer must expose a pure helper `autotuner.replay_exit_sequence` that
# runs the replay's per-tick exit loop and returns the exit-decision sequence
# — calling the SAME math_engine primitives run_simulation uses, so the
# helper IS the replay path, not a re-implementation. This test file asserts
# that helper exists and that its decisions match production tick-for-tick.
# ---------------------------------------------------------------------------


def test_autotuner_exposes_replay_exit_sequence_helper() -> None:
    """AC-6: a parity test needs the replay's per-tick exit decision.

    The implementer must expose `autotuner.replay_exit_sequence(ticks,
    params, *, grace_minutes)` — a pure helper that runs the replay's
    per-tick exit loop and returns one {tick_idx, exit_reason} dict per
    executed tick (stopping after the first exit). The helper must call the
    SAME math_engine primitives run_simulation uses; it exists so the exit
    decision is observable for parity testing.

    RED: no such helper exists on pre-fix autotuner.
    """
    assert hasattr(autotuner, "replay_exit_sequence"), (
        "autotuner must expose a pure `replay_exit_sequence(ticks, params, "
        "*, grace_minutes)` helper returning the replay's per-tick exit "
        "decision sequence. AC-6's bit-identical parity test cannot compare "
        "exit decisions tick-by-tick without it."
    )


def _replay_seq(ticks: list[dict], params: dict, grace_minutes: int) -> list[dict]:
    """Call the implementer-exposed replay helper (skips cleanly pre-fix)."""
    if not hasattr(autotuner, "replay_exit_sequence"):
        pytest.fail(
            "autotuner.replay_exit_sequence missing — see "
            "test_autotuner_exposes_replay_exit_sequence_helper."
        )
    return autotuner.replay_exit_sequence(
        ticks, params, grace_minutes=grace_minutes
    )


def _default_params() -> dict:
    """Autotuner default param set (the run_simulation .get() defaults)."""
    return {
        "TRIGGER_THRESHOLD_PCT": 15.0,
        "TAKE_PROFIT_MC_PCT": 5.0,
        "VWAP_CROSS_HWM_PCT": 1.0,
        "VWAP_BLEED_MULTIPLIER": 1.5,
        "VWAP_BLEED_TICKS": 10,
        "PARABOLIC_VELOCITY_THRESHOLD": 2.0,
        "MAX_PARABOLIC_SQUEEZE": 0.5,
    }


# ===========================================================================
# AC-6 — the bit-identical exit-decision parity tests.
# ===========================================================================


@pytest.mark.parametrize(
    "fixture_name",
    [
        "parity_trailing_stop_exit.json",
        "parity_vwap_grace_no_phantom.json",
        "parity_tp_rearm_dip.json",
        "parity_no_exit_session.json",
        "parity_mc_sanity_veto.json",
    ],
)
def test_replay_exit_decision_matches_production(fixture_name: str) -> None:
    """AC-6: the replay's exit decision sequence is bit-identical to the
    production exit path's, tick-for-tick, for every parity fixture.

    Each fixture supplies an input tick sequence + a param set + a
    grace_minutes value. Both the production harness and the replay helper
    run that sequence; the (tick_idx, exit_reason) sequences must be equal.

    RED: pre-fix the replay open-codes the exit (AC-1), omits the VWAP
    grace gate (AC-2) and mishandles TP re-arm (AC-3); at least one fixture
    diverges on which trigger fires or on which tick.
    """
    fx = _load_fixture(fixture_name)
    ticks = fx["ticks"]
    params = fx.get("params") or _default_params()
    grace_minutes = fx["grace_minutes"]

    production = _production_exit_sequence(
        ticks, params, grace_minutes=grace_minutes
    )
    replay = _replay_seq(ticks, params, grace_minutes)

    prod_decisions = [(d["tick_idx"], d["exit_reason"]) for d in production]
    replay_decisions = [(d["tick_idx"], d["exit_reason"]) for d in replay]

    assert replay_decisions == prod_decisions, (
        f"[{fixture_name}] exit-decision parity broken.\n"
        f"  production: {prod_decisions}\n"
        f"  replay:     {replay_decisions}\n"
        f"The autotuner replay must reproduce the production exit path "
        f"tick-for-tick — same trigger, same tick. A divergence here means "
        f"the autotuner optimizes a different exit rule than the live engine."
    )


def test_replay_exit_and_production_exit_fire_on_same_tick() -> None:
    """AC-6: when an exit fires, both paths fire it on the SAME tick index.

    Isolates the 'which tick' half of the parity contract on the
    trailing-stop fixture. An off-by-one in confirm-counting (e.g. the
    replay's open-coded counter desyncing from compute_exit_confirmation)
    surfaces here even if the trigger STRING happens to match.
    """
    fx = _load_fixture("parity_trailing_stop_exit.json")
    ticks = fx["ticks"]
    params = fx.get("params") or _default_params()
    grace = fx["grace_minutes"]

    production = _production_exit_sequence(ticks, params, grace_minutes=grace)
    replay = _replay_seq(ticks, params, grace)

    prod_exit = next((d for d in production if d["exit_reason"]), None)
    replay_exit = next((d for d in replay if d["exit_reason"]), None)

    assert prod_exit is not None, (
        "Fixture parity_trailing_stop_exit.json must produce a production "
        "exit — otherwise it cannot exercise tick-index parity."
    )
    assert replay_exit is not None, (
        "Replay produced no exit where production exited at tick "
        f"{prod_exit['tick_idx']} ({prod_exit['exit_reason']})."
    )
    assert replay_exit["tick_idx"] == prod_exit["tick_idx"], (
        f"Exit-tick parity broken: production exited on tick "
        f"{prod_exit['tick_idx']}, replay on tick {replay_exit['tick_idx']}."
    )
    assert replay_exit["exit_reason"] == prod_exit["exit_reason"], (
        f"Exit-reason parity broken: production={prod_exit['exit_reason']}, "
        f"replay={replay_exit['exit_reason']}."
    )
