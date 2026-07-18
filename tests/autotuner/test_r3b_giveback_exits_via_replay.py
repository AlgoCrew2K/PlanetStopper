"""
R3-b MA-4 AC-4 — THE GIVEBACK GOLDEN (behavioral, end-to-end via the real replay).

This is the crux non-vacuity proof. It drives the REAL
``autotuner._replay_exit_tick`` (production-faithful exit core — no mocking of the
math engine) over the audit's giveback scenario:

    arm at a peak (prob in [5,15)) -> prob rises past 2x-trigger (30) while the
    return is still positive (the OLD inverted disarm's exact trigger) -> the
    return gives back through 0 into a sustained loss with prob in breakdown
    (>= MC_BREAKDOWN_THRESHOLD 60).

REQUIRED behavior: the protective stop stays armed the whole way and the Trailing
Stop FIRES (resolve_trigger_priority reason == "Trailing Stop").

NON-VACUITY (demonstrably RED on base 57a8a897):
  Under the OLD inverted disarm, tick 1 (prob=35, return=+1) satisfies
  ``mc_available and prob > 2*TRIGGER_THRESHOLD_PCT and return > 0`` -> the replay
  sets state["armed"]=False mid-giveback. The subsequent loss ticks cannot re-arm
  (re-arm needs prob back in [5,15), which the giveback never revisits), so
  compute_exit_confirmation short-circuits on armed=False and the Trailing Stop
  NEVER fires. The exit reason stays None for the whole day -> this test FAILS.
  POST-FIX (r3b-tuner wires _replay_exit_tick to compute_arm_disarm_decision):
  prob=35 is deterioration (not recovery) -> stays armed -> the breakdown loss
  ticks confirm and the Trailing Stop fires.

The loss ticks are intentionally deep (-5.0) so the magnitude-breach half of
compute_exit_confirmation is unconditionally satisfied regardless of the
vol-based stop distance / time-squeeze decay — the disarm (tick 1) is the ONLY
thing gating the exit, which is exactly what this golden isolates. The giveback
still passes through the audit's -1.5 before deepening.

TOLERANCE: the exit reason is a discrete categorical string — exact equality.
"""

from __future__ import annotations

import autotuner

# VWAP + parabolic effectively disabled so the ONLY trigger that can fire is the
# Trailing Stop (VWAP never evaluates at valid_vwap_weight=0; TP never arms
# because no tick has prob < TAKE_PROFIT_MC_PCT=5.0).
_PARAMS = {
    "TRIGGER_THRESHOLD_PCT": 15.0,
    "TAKE_PROFIT_MC_PCT": 5.0,
    "VWAP_CROSS_HWM_PCT": 99.0,
    "VWAP_BLEED_MULTIPLIER": 1.5,
    "VWAP_BLEED_TICKS": 999,
    "PARABOLIC_VELOCITY_THRESHOLD": 99.0,
    "MAX_PARABOLIC_SQUEEZE": 0.5,
}


def _tick(ret: float, mc: float | None) -> dict:
    return {
        "time": "12:00",
        "return": ret,
        "mc_prob": mc,
        "vol": 0.5,
        "vwap_diff": 0.0,
        "base_atr_pct": 0.5,
        "valid_vwap_weight": 0.0,
    }


def _giveback_ticks() -> list[dict]:
    """+3% peak -> +1% (giveback through positive, OLD-disarm trigger) -> -1.5%
    (the audit value, now in breakdown) -> sustained deep loss (keeps bleeding
    because an un-exited position cannot stop itself)."""
    return [
        _tick(3.0, 10.0),  # arm at the peak (prob in [5,15))
        _tick(1.0, 35.0),  # giveback through positive: 35 > 2x trigger (30), ret > 0 -> OLD disarm
        _tick(-1.5, 70.0),  # gives back into a loss; 70 >= breakdown (60)
        _tick(-5.0, 70.0),  # sustained breakdown loss ...
        _tick(-5.0, 70.0),
        _tick(-5.0, 70.0),
        _tick(-5.0, 70.0),
        _tick(-5.0, 70.0),
    ]


def test_giveback_into_loss_fires_trailing_stop_via_replay() -> None:
    """AC-4 crux: the giveback exits with reason 'Trailing Stop'. RED on base
    (the OLD inverted disarm strips armed at the +1%/prob=35 tick, so the stop
    never fires); GREEN once the replay disarm keys on genuine recovery."""
    state = autotuner._fresh_replay_state()
    ticks = _giveback_ticks()
    n = len(ticks)

    fired = None
    armed_trace = []
    for idx, tk in enumerate(ticks):
        reason = autotuner._replay_exit_tick(state, tk, idx, n, _PARAMS, grace_minutes=0)
        armed_trace.append(state["armed"])
        if reason:
            fired = (idx, reason)
            break

    assert fired is not None, (
        "MA-4 BUG REPRODUCED: the giveback never fired the Trailing Stop. "
        f"armed trace across ticks = {armed_trace}. The OLD inverted disarm strips "
        "armed at the giveback-through-positive tick (prob=35, return=+1), so the "
        "protective stop is dark through the entire loss and the position can never "
        "exit. The fix must keep the stop armed on deterioration."
    )
    assert fired[1] == "Trailing Stop", (
        f"expected the giveback to exit via 'Trailing Stop'; got {fired[1]!r} at tick {fired[0]}."
    )


def test_giveback_keeps_stop_armed_until_it_fires_via_replay() -> None:
    """AC-4 companion: the stop stays armed through the giveback deterioration
    up to (and including) the tick it fires — it is never disarmed mid-giveback.
    RED on base: the OLD disarm flips armed=False at tick 1 (prob=35)."""
    state = autotuner._fresh_replay_state()
    ticks = _giveback_ticks()
    n = len(ticks)

    for idx, tk in enumerate(ticks):
        reason = autotuner._replay_exit_tick(state, tk, idx, n, _PARAMS, grace_minutes=0)
        if idx >= 1:
            # From the first deterioration tick onward the stop must remain armed
            # (deterioration is not recovery). The OLD code disarmed here.
            assert state["armed"] is True, (
                f"the stop was DISARMED at giveback tick {idx} "
                f"(return={tk['return']}, mc_prob={tk['mc_prob']}). MA-4: deterioration "
                "must keep the stop armed so the exit gate stays reachable."
            )
        if reason:
            break
