"""
AC-7 (inert-dims verification) — a bounded, deterministic smoke proving the
three previously-inert Optuna search-space dims (TAKE_PROFIT_MC_PCT,
PARABOLIC_VELOCITY_THRESHOLD, MAX_PARABOLIC_SQUEEZE) now MOVE the
autotuner's objective.

Audit context (MA-1's downstream consequence): with day-constant/degenerate
mc_prob (pre-AC-1), these three dims could never change which tick an exit
fires on, or whether one fires at all -- Optuna spent trials tuning noise.
This file demonstrates the OPPOSITE post-fix: each dim, swept in isolation
(every other input held byte-identical), changes autotuner.run_simulation's
returned objective.

Two layers (a single-layer draft of this file was found, empirically, to be
insufficient -- see the note at the end of this docstring):

  Section 0+1 (wiring-sensitivity, hand-specified ticks): does NOT exercise
  AC-1's lpc-to-mc_prob mapping -- it tests the EXIT-DECISION layer's
  parameter sensitivity, a property of math_engine's primitives
  (compute_tp_confirmation, compute_para_arm_decision,
  compute_active_trailing_stop) independent of how mc_prob was derived.
  Scenarios use hand-specified tick dicts (mc_prob supplied directly),
  matching the SAME convention the pre-existing
  tests/fixtures/autotuner/replay_parity/*.json fixtures use. Sensitivity of
  each underlying math_engine primitive is verified directly (Section 0)
  before being exercised through run_simulation (Section 1). These pass
  GREEN today (the wiring was never broken by MA-1) and stay GREEN post-fix
  -- they are regression guards against a FUTURE refactor that drops a
  swept-param lookup, not a RED signal for this cycle. See "WHY THIS COULD
  STILL BE RED POST-AC-1" below.

  Section 3 (the ACTUAL walk-forward smoke the plan's AC-7 text calls for --
  "a bounded walk-forward smoke ... demonstrates the three previously-inert
  dims ... now MOVE the objective"): drives REAL bar-derived multi-day
  history through synthetic_history.build_replay_day (the function AC-1
  patches) into autotuner.run_simulation, sweeping TAKE_PROFIT_MC_PCT (the
  cleanest of the 3 dims to construct end-to-end without the empirical
  fixture-tuning risk AC-6 required for the other two -- PARABOLIC_VELOCITY_
  THRESHOLD/MAX_PARABOLIC_SQUEEZE's wiring-level sensitivity is covered
  above and their bar-derived form is left for a follow-up if the team
  wants full parity of proof-depth across all 3 dims). VERIFIED LIVE,
  pre-fix: 3 days of bar-derived history give per-day CONSTANT mc_prob
  (~64-66, never varying tick-to-tick within a day, confirming MA-1's
  "day-constant" finding directly) that NEVER crosses the
  TAKE_PROFIT_MC_PCT=5.0-vs-10.0 sweep boundary -- both give objective=-0.0,
  genuinely IDENTICAL. This is the audit's actual claim, reproduced.

WHY THIS COULD STILL BE RED POST-AC-1 (Section 0+1, the wiring guards):
run_simulation calls _replay_exit_tick via the SAME per-tick core the parity
battery pins -- if _replay_exit_tick does not thread a swept param through
to the right math_engine call (e.g. a future refactor drops the
p.get("PARABOLIC_VELOCITY_THRESHOLD", ...) lookup or hardcodes it), this
file catches that even though AC-1/AC-3/AC-4/AC-5 are otherwise fully fixed
-- a distinct regression class from the ones those ACs target.
"""

from __future__ import annotations

import math_engine
import pytest

import autotuner
import synthetic_history

_BASE_PARAMS = {
    "TRIGGER_THRESHOLD_PCT": 15.0,
    "TAKE_PROFIT_MC_PCT": 5.0,
    "VWAP_CROSS_HWM_PCT": 99.0,
    "VWAP_BLEED_MULTIPLIER": 1.5,
    "VWAP_BLEED_TICKS": 999,
    "PARABOLIC_VELOCITY_THRESHOLD": 99.0,
    "MAX_PARABOLIC_SQUEEZE": 0.5,
}
_SYM_ID = "sym-ac7-001"
_DATE = "2026-04-06"


def _tick(*, ret: float, mc: float | None, vol: float = 1.0) -> dict:
    return {
        "time": "tick",
        "return": ret,
        "mc_prob": mc,
        "vol": vol,
        "vwap_diff": 0.0,
        "base_atr_pct": 0.5,
        "valid_vwap_weight": 0.0,
    }


def _objective(ticks: list[dict], params: dict) -> float:
    history_data = {_SYM_ID: {_DATE: ticks}}
    return autotuner.run_simulation(params, history_data, [_SYM_ID], _DATE, {})


# ===========================================================================
# Section 0 — underlying math_engine primitive sensitivity (isolates "is the
# primitive itself sensitive" from "does the wiring reach it").
# ===========================================================================


def test_take_profit_confirmation_primitive_is_sensitive_to_threshold() -> None:
    armed_low, _, _ = math_engine.compute_tp_confirmation(
        mc_available=True, prob_underperforming=8.0, take_profit_mc_pct=5.0,
        current_return=1.0, is_triggered=False, tp_armed=False, above_tp_count=0,
    )
    armed_high, _, _ = math_engine.compute_tp_confirmation(
        mc_available=True, prob_underperforming=8.0, take_profit_mc_pct=10.0,
        current_return=1.0, is_triggered=False, tp_armed=False, above_tp_count=0,
    )
    assert armed_low != armed_high, (
        "compute_tp_confirmation must be sensitive to take_profit_mc_pct at "
        "prob_underperforming=8.0 (below 10.0, at-or-above 5.0) -- fixture "
        "self-check for the run_simulation-level test below."
    )


def test_para_arm_decision_primitive_is_sensitive_to_threshold() -> None:
    _, arm_low = math_engine.compute_para_arm_decision(
        current_return=3.0, prev_return=0.0, para_threshold=0.5, currently_armed=False
    )
    _, arm_high = math_engine.compute_para_arm_decision(
        current_return=3.0, prev_return=0.0, para_threshold=5.0, currently_armed=False
    )
    assert arm_low != arm_high, (
        "compute_para_arm_decision must be sensitive to para_threshold at "
        "velocity=3.0 -- fixture self-check for the run_simulation-level test below."
    )


def test_active_trailing_stop_primitive_is_sensitive_to_squeeze_cap() -> None:
    stop_tight = math_engine.compute_active_trailing_stop(1.0, 1.0, 0.5, True, False, 0.1)
    stop_wide = math_engine.compute_active_trailing_stop(1.0, 1.0, 0.5, True, False, 0.8)
    assert stop_tight != stop_wide, (
        "compute_active_trailing_stop must be sensitive to MAX_PARABOLIC_SQUEEZE "
        "when para_armed=True -- fixture self-check for the run_simulation-level "
        "test below."
    )


# ===========================================================================
# Section 1 — the objective itself varies (wiring-level, via run_simulation).
# ===========================================================================


def test_take_profit_mc_pct_varies_the_objective() -> None:
    """RED pre-fix (or on a wiring regression): TAKE_PROFIT_MC_PCT reaching
    zero downstream effect on the objective means Optuna cannot tune it --
    objective-inert noise applied to live money.

    ticks: mc=8.0 (below 10.0, not below 5.0) then two ticks at mc=12.0,
    ret=+1.0 each (>= 10.0, confirms TP after TP_CONFIRM_TICKS=2 consecutive
    ticks, when armed). At TAKE_PROFIT_MC_PCT=10.0 this arms-then-confirms a
    Take-Profit exit; at 5.0 it never arms (8.0 is not < 5.0) and the day
    runs to EOD with no exit -- a different guard_alpha contribution either
    way (an exit changes total_guard_alpha's accounting entirely relative to
    no exit at all).
    """
    ticks = [_tick(ret=1.0, mc=8.0), _tick(ret=1.0, mc=12.0), _tick(ret=1.0, mc=12.0)]

    obj_low = _objective(ticks, {**_BASE_PARAMS, "TAKE_PROFIT_MC_PCT": 5.0})
    obj_high = _objective(ticks, {**_BASE_PARAMS, "TAKE_PROFIT_MC_PCT": 10.0})

    assert obj_low != obj_high, (
        f"run_simulation's objective did not vary with TAKE_PROFIT_MC_PCT "
        f"(5.0 -> {obj_low!r}, 10.0 -> {obj_high!r}) despite the underlying "
        "compute_tp_confirmation primitive being demonstrably sensitive to it "
        "(see test_take_profit_confirmation_primitive_is_sensitive_to_threshold). "
        "The parameter is not reaching the objective -- objective-inert."
    )


def _velocity_squeeze_ticks() -> list[dict]:
    """Shared 5-tick sequence for the velocity/squeeze scenarios below.

    tick0 arms the trailing stop (mc=10.0, in the default band [5,15));
    tick1 is a velocity spike (ret 0.0 -> 8.0) with mc STILL in-band (10.0,
    not >2*TRIGGER_THRESHOLD_PCT) so it cannot accidentally trigger the
    disarm condition (mc > 2*trigger_threshold and ret > 0) -- an earlier
    draft used mc=70.0 on this tick and disarmed immediately, silently
    zeroing both sweep values' objectives (both -0.0), a false-negative
    discovered only by tracing _replay_exit_tick tick-by-tick. ticks 2-4
    are a modest pullback (7.7/7.6/7.5, still positive vs HWM=8.0) with
    mc=70.0 (clears MC_BREAKDOWN_THRESHOLD=60 for the trailing-stop confirm
    gate); TRIGGER_THRESHOLD_PCT=40.0 (vs the 15.0 default) keeps
    2*trigger_threshold=80.0 > mc=70.0, so this positive-return pullback
    with mc=70.0 does NOT trigger disarm either -- verified live via the
    same tick-by-tick trace before locking this fixture in.
    """
    return [
        _tick(ret=0.0, mc=10.0),
        _tick(ret=8.0, mc=10.0),
        _tick(ret=7.7, mc=70.0),
        _tick(ret=7.6, mc=70.0),
        _tick(ret=7.5, mc=70.0),
    ]


_VELOCITY_SQUEEZE_PARAMS = {**_BASE_PARAMS, "TRIGGER_THRESHOLD_PCT": 40.0}


def test_parabolic_velocity_threshold_varies_the_objective() -> None:
    """A velocity spike (ret jumps 0.0 -> 8.0) then a modest pullback. A LOW
    PARABOLIC_VELOCITY_THRESHOLD arms the parabolic squeeze on the spike; a
    HIGH one does not -- changing active_stop_dist (via the para_armed
    multiplier in compute_active_trailing_stop) for the subsequent pullback
    ticks and therefore whether a trailing stop confirms. MAX_PARABOLIC_SQUEEZE
    is fixed tight (0.1) so the tight-squeeze effect is visible ONLY when
    para-armed -- isolating this dim from AC-7's squeeze test below.

    Verified live (tick-by-tick trace, not hardcoded): threshold=0.5 arms
    para at tick1 and confirms Trailing Stop at tick4; threshold=20.0 never
    arms para and never confirms (below_stop_count stays 0 throughout).
    """
    ticks = _velocity_squeeze_ticks()

    obj_low = _objective(
        ticks, {**_VELOCITY_SQUEEZE_PARAMS, "PARABOLIC_VELOCITY_THRESHOLD": 0.5, "MAX_PARABOLIC_SQUEEZE": 0.1}
    )
    obj_high = _objective(
        ticks, {**_VELOCITY_SQUEEZE_PARAMS, "PARABOLIC_VELOCITY_THRESHOLD": 20.0, "MAX_PARABOLIC_SQUEEZE": 0.1}
    )

    assert obj_low != obj_high, (
        f"run_simulation's objective did not vary with PARABOLIC_VELOCITY_THRESHOLD "
        f"(0.5 -> {obj_low!r}, 20.0 -> {obj_high!r}) despite the underlying "
        "compute_para_arm_decision primitive being demonstrably sensitive to it "
        "(see test_para_arm_decision_primitive_is_sensitive_to_threshold). "
        "The parameter is not reaching the objective -- objective-inert."
    )


def test_max_parabolic_squeeze_varies_the_objective() -> None:
    """Once parabolic-armed (PARABOLIC_VELOCITY_THRESHOLD fixed low so both
    sweep values arm identically on the tick1 spike), MAX_PARABOLIC_SQUEEZE
    multiplies active_stop_dist while armed (verified in Section 0) -- a
    TIGHT squeeze (0.1) confirms a trailing-stop breach on the modest
    pullback that a WIDE squeeze (1.5) does not.

    Verified live (tick-by-tick trace, not hardcoded): squeeze=0.1 confirms
    Trailing Stop at tick4 (below_stop_count reaches EXIT_CONFIRM_TICKS=3);
    squeeze=1.5 never confirms (below_stop_count stays 0 throughout -- the
    wider stop is never breached by the modest pullback).
    """
    ticks = _velocity_squeeze_ticks()

    obj_tight = _objective(
        ticks, {**_VELOCITY_SQUEEZE_PARAMS, "PARABOLIC_VELOCITY_THRESHOLD": 0.5, "MAX_PARABOLIC_SQUEEZE": 0.1}
    )
    obj_wide = _objective(
        ticks, {**_VELOCITY_SQUEEZE_PARAMS, "PARABOLIC_VELOCITY_THRESHOLD": 0.5, "MAX_PARABOLIC_SQUEEZE": 1.5}
    )

    assert obj_tight != obj_wide, (
        f"run_simulation's objective did not vary with MAX_PARABOLIC_SQUEEZE "
        f"(0.1 -> {obj_tight!r}, 1.5 -> {obj_wide!r}) despite the underlying "
        "compute_active_trailing_stop primitive being demonstrably sensitive to "
        "it (see test_active_trailing_stop_primitive_is_sensitive_to_squeeze_cap). "
        "The parameter is not reaching the objective -- objective-inert."
    )


# ===========================================================================
# Section 2 — guard against the "N results hides a dead factor" failure
# mode: a sweep across MORE than 2 values must show genuine variance, not
# just a coincidental 2-point difference.
# ===========================================================================


def test_take_profit_mc_pct_three_point_sweep_shows_non_degenerate_variance() -> None:
    """A 2-point difference could be a coincidence (e.g. floating-point noise
    on an otherwise-flat objective). A 3-point sweep across the same
    qualitative regions (below-arm, arms-only, arms-and-confirms) must show
    at least 2 DISTINCT objective values -- not all 3 collapsing to one.
    """
    ticks = [_tick(ret=1.0, mc=8.0), _tick(ret=1.0, mc=12.0), _tick(ret=1.0, mc=12.0)]
    values = {
        threshold: _objective(ticks, {**_BASE_PARAMS, "TAKE_PROFIT_MC_PCT": threshold})
        for threshold in (5.0, 9.0, 13.0)
    }
    assert len(set(values.values())) > 1, (
        f"All 3 TAKE_PROFIT_MC_PCT sweep values produced the IDENTICAL objective "
        f"({values}) -- a 3-point sweep collapsing to one value is exactly the "
        "'N results hides a dead factor' failure mode: a passing 2-point diff "
        "test could still hide a factor that is only accidentally sensitive at "
        "those two specific values."
    )


# ===========================================================================
# Section 3 — the ACTUAL bounded walk-forward smoke: real bar-derived
# multi-day history through synthetic_history.build_replay_day (the
# function AC-1 patches) into autotuner.run_simulation. This is the genuine
# RED signal for this cycle (Section 0+1 above are wiring regression guards
# that pass both pre- and post-fix).
# ===========================================================================

# Reused verbatim from tests/fixtures/math_engine/ma1_build_replay_day_lpc_stamping.json
# / test_ac6_bar_level_replay_production_parity.py's _historical_data (AC-1's
# empirically-validated fixture: is_unprecedented=False, eligible_days=26 >=
# MC_MIN_HISTORY_DAYS, mc_prob genuinely varies with lpc).
_WF_HISTORY_ROWS = [
    ("2026-01-01", 0.008699, -0.003372), ("2026-01-02", -0.011317, -0.004389),
    ("2026-01-03", -0.004296, -0.012604), ("2026-01-04", -0.000152, 0.012547),
    ("2026-01-05", -0.008223, 0.002952), ("2026-01-06", 0.004126, -0.015204),
    ("2026-01-07", -0.008957, -0.014317), ("2026-01-08", -0.003915, -0.013142),
    ("2026-01-09", -0.006295, 0.007542), ("2026-01-10", 0.004041, -0.010652),
    ("2026-01-11", -0.001306, 0.016507), ("2026-01-12", -0.003925, -0.007241),
    ("2026-01-13", -0.007962, -0.006551), ("2026-01-14", -0.003467, -0.004219),
    ("2026-01-15", -0.0043, -0.007718), ("2026-01-16", -0.007974, 0.004053),
    ("2026-01-17", -0.000363, -0.002374), ("2026-01-18", 0.007428, 0.008943),
    ("2026-01-19", -0.000522, -0.013318), ("2026-01-20", 0.002671, 0.016431),
    ("2026-01-21", -0.000467, 0.016775), ("2026-01-22", 0.009194, 0.009092),
    ("2026-01-23", 0.007846, 0.006637), ("2026-01-24", -0.000806, -0.010847),
    ("2026-01-25", -0.006622, 0.004824), ("2026-01-26", -0.002043, 0.014662),
    ("2026-01-27", -0.006684, -0.010073), ("2026-01-28", 0.005139, 0.00115),
    ("2026-02-01", -0.009975, -0.007617), ("2026-02-02", 0.006892, 0.001378),
    ("2026-02-03", -0.009569, -0.019655), ("2026-02-04", -0.011437, 0.002203),
    ("2026-02-05", 0.002328, 0.003094), ("2026-02-06", 0.007038, -0.0099),
    ("2026-02-07", 0.007598, -0.000543), ("2026-02-08", -0.006786, -0.003091),
    ("2026-02-09", -0.006137, 0.012108), ("2026-02-10", 0.007932, 0.012117),
    ("2026-02-11", 0.011648, 0.000198), ("2026-02-12", -0.002905, -0.015545),
    ("2026-02-13", -0.000372, 0.018246), ("2026-02-14", -0.006657, -0.005041),
    ("2026-02-15", -0.004881, 0.001285), ("2026-02-16", 0.004643, 0.014488),
    ("2026-02-17", -0.01139, 0.016439),
]


def _wf_historical_data() -> dict:
    return {d: {"SPY": {"daily_ret": s}, "AAA": {"daily_ret": a}} for d, s, a in _WF_HISTORY_ROWS}


def _build_wf_history_data(sym_id: str, day_closes: dict[str, list[float]]) -> dict:
    """Build multi-day history_data via the REAL synthetic_history.build_replay_day
    -- reused across every day for the same symphony, exactly as
    generate_synthetic_history's process_day does."""
    hist = _wf_historical_data()
    out: dict[str, dict] = {sym_id: {}}
    for date_str, closes in day_closes.items():
        holdings = [{"ticker": "AAA", "allocation": 1.0}]
        timestamps = [f"{date_str}T09:{30 + i:02d}:00Z" for i in range(len(closes))]
        intraday_by_date = {date_str: {"AAA": [{"t": t, "c": c, "vwap": c} for t, c in zip(timestamps, closes)]}}
        yesterday_closes = {"AAA": 100.0}
        out[sym_id][date_str] = synthetic_history.build_replay_day(
            sym_id=sym_id,
            date_str=date_str,
            holdings=holdings,
            intraday_by_date=intraday_by_date,
            timestamps=timestamps,
            hist_data_up_to_yesterday=hist,
            yesterday_closes=yesterday_closes,
            spy_today=0.4,
        )
    return out


def test_take_profit_mc_pct_varies_the_objective_over_real_bar_derived_walk_forward() -> None:
    """The genuine walk-forward-level RED signal: 3 days of bar-derived
    history (each day a deep decline from a c=101.5 open, matching the
    AC-6-validated arm-band price) for one symphony, through the REAL
    synthetic_history.build_replay_day -> autotuner.run_simulation pipeline.

    RED pre-AC-1 (verified live): every day's mc_prob is CONSTANT across all
    its ticks (~64-66 across the 3 days, never varying tick-to-tick within a
    day -- reproducing MA-1's "day-constant mc_prob" finding directly) and
    never crosses the TAKE_PROFIT_MC_PCT=5.0-vs-10.0 sweep boundary --
    both sweep values give the IDENTICAL objective (-0.0, no exit fires
    either way). This is the audit's literal "objective-inert" claim.
    """
    sym_id = "sym-ac7-wf-001"
    day_closes = {
        "2026-04-06": [101.5, 80.0, 78.0, 76.0, 74.0],
        "2026-04-07": [101.5, 80.0, 78.0, 76.0, 74.0],
        "2026-04-08": [101.5, 80.0, 78.0, 76.0, 74.0],
    }
    history_data = _build_wf_history_data(sym_id, day_closes)

    values = {}
    for tp_pct in (5.0, 10.0):
        params = {**_BASE_PARAMS, "TAKE_PROFIT_MC_PCT": tp_pct}
        values[tp_pct] = autotuner.run_simulation(params, history_data, [sym_id], "2026-04-08", {})

    assert values[5.0] != values[10.0], (
        f"run_simulation's objective did not vary with TAKE_PROFIT_MC_PCT over a "
        f"REAL bar-derived 3-day walk-forward smoke (5.0 -> {values[5.0]!r}, "
        f"10.0 -> {values[10.0]!r}). This is the audit's literal claim: "
        "TAKE_PROFIT_MC_PCT is objective-inert noise applied to live money "
        "because mc_prob never carries a real per-tick signal for it to act on."
    )
