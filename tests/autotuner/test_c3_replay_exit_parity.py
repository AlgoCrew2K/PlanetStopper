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


@pytest.fixture(autouse=True)
def _pin_execution_start_time_to_session_open(monkeypatch):
    """Pin alpha_bot_execution.EXECUTION_START_TIME = "09:30" for every
    parity test in this file.

    Cluster 7 / AC-5 fixed the replay's grace gate to read
    EXECUTION_START_TIME from env (the bug: replay hardcoded a "09:30"
    anchor while production correctly anchored at EXECUTION_START_TIME).
    The parity fixtures in tests/fixtures/autotuner/replay_parity/ were
    authored under the legacy semantic "tick_idx 0 == 09:30 ET session
    open" and the in-file `_production_exit_sequence` reference helper
    hardcodes `session_open_hhmm="09:30"`. With AC-5 landed, if the
    operator's env carries EXECUTION_START_TIME="10:31" (the
    default-changed-by-operator case), the replay's grace window shifts
    to tick_idx 60..74 while the production reference stays at 0..14
    — the parity test then sees a real divergence on the grace-sensitive
    fixture (`parity_vwap_grace_no_phantom.json`).

    Pinning EXECUTION_START_TIME to "09:30" here makes the parity check
    hermetic against the operator's env, matching the fixtures' baked-in
    tick-anchoring AND the in-file production reference. The
    non-default-EXECUTION_START_TIME behavior is exercised independently
    by tests/autotuner/test_n3_replay_grace_execution_start_time.py.
    """
    monkeypatch.setattr("alpha_bot_execution.EXECUTION_START_TIME", "09:30", raising=False)


# ---------------------------------------------------------------------------
# Constants sourced from the producers (never re-typed as bare literals).
# ---------------------------------------------------------------------------

_FIXTURE_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "autotuner" / "replay_parity"

# Production-side names re-exported so the harness reads them, not literals.
_MC_SANITY_THRESHOLD = math_engine.MC_BREAKDOWN_THRESHOLD  # 60.0
_EXIT_CONFIRM_TICKS = math_engine.EXIT_CONFIRM_TICKS  # 3
_MAGNITUDE_FLOOR_PCT = math_engine.MAGNITUDE_FLOOR_PCT  # 0.10
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
    regime_label: str | None = None,
    execution_start_hhmm: str | None = None,
) -> list[dict]:
    """Drive a tick sequence through the production exit path.

    Returns one dict per tick: {tick_idx, exit_reason or None}. exit_reason
    is the resolve_trigger_priority string on the tick the position exits;
    None on every non-exit tick. After an exit fires the loop stops (the
    production engine commits the exit and freezes the symphony for the day).

    ORACLE SYNC (math-r1 AC-3/AC-4/AC-5, PM addendum 2 @ a46be889, r1-tuner's
    cross-cutting finding): this reference predates the math-r1 audit and
    carried the SAME 3 gaps the pre-fix replay has — no MA-10 fail-open arm,
    no regime-conditional exit_confirm_ticks, no EXECUTION_START_TIME
    action-phase gate. All three are added here, additively, sourced from the
    exact production lines cited below. The pre-existing VWAP-grace / TP-
    re-arm / trailing-stop-primitive behavior this oracle already modeled
    correctly is untouched.

    New optional parameters (both default to the exact prior behavior, so
    every existing caller is unaffected):
      regime_label: fed into math_engine.apply_regime_exit_adjustment to
        resolve exit_confirm_ticks, mirroring alpha_bot_execution.py:
        1436-1448. None (the default) resolves to base_ticks unchanged —
        identical to compute_exit_confirmation's own implicit default that
        every pre-sync caller relied on.
      execution_start_hhmm: the action-phase gate anchor (see below). None
        (the default) falls back to session_open_hhmm's value, preserving
        this file's existing convention exactly (all current fixtures/tests
        pin EXECUTION_START_TIME == session open == "09:30").

    TICK-0-ANCHOR BUG FIX (found while adding the action-phase gate): the
    prior implementation overwrote base_open's hour/minute FROM
    session_open_hhmm, conflating "tick_idx 0's wall clock" (production:
    ALWAYS 09:30 — the data phase runs from session open regardless of
    EXECUTION_START_TIME, alpha_bot_execution.py:876-885/681) with
    "EXECUTION_START_TIME" (an independently configurable value). This never
    manifested as a wrong RESULT because every existing caller only ever
    passed session_open_hhmm="09:30" (both values coincided), but it was
    latently wrong. base_open is now hardcoded to 09:30 unconditionally;
    session_open_hhmm/execution_start_hhmm are consulted ONLY for the
    grace-window and action-phase-gate comparisons, matching production.
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

    effective_exec_start = execution_start_hhmm if execution_start_hhmm is not None else session_open_hhmm

    # AC-4/F5: regime-conditional exit_confirm_ticks, resolved ONCE (mirrors
    # production reading the offline-computed cached label once per cycle;
    # the label does not change intra-day/intra-tick).
    # alpha_bot_execution.py:1436-1448.
    _regime_exit_ticks = math_engine.apply_regime_exit_adjustment(
        regime_label=regime_label, base_ticks=math_engine.EXIT_CONFIRM_TICKS
    )

    # AC-5/F6: the action phase (para-arm, MC arm/disarm, trailing-stop
    # confirm, TP confirm, VWAP checks, exit firing) does not run at all
    # before EXECUTION_START_TIME (alpha_bot_execution.py:951-953, the hard
    # `if current_time < market_open and not force_run: return` gate). Only
    # the DATA phase (HWM/shadow_hwm tracking, :876-885) runs unconditionally
    # from the true 09:30 session open. tick_idx 0 == 09:30 (N-3 convention).
    _h, _m = effective_exec_start.split(":")
    _action_phase_start_offset = (int(_h) - 9) * 60 + (int(_m) - 30)

    # Production minute timeline: tick_idx 0 == the TRUE session open (09:30),
    # unconditionally — never parameterized (see TICK-0-ANCHOR BUG FIX above).
    base_open = datetime(2026, 4, 6, 9, 30, tzinfo=_ET)

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

        # DATA PHASE (alpha_bot_execution.py:876-885): HWM/safe_hwm tracking
        # runs unconditionally, even before EXECUTION_START_TIME.
        if ret > hwm:
            hwm = ret
        safe_hwm = max(hwm, ret)

        if tick_idx < _action_phase_start_offset:
            # ACTION PHASE NOT YET OPEN (alpha_bot_execution.py:951-953):
            # production returns before evaluating para-arm, MC arm/disarm,
            # trailing-stop confirm, TP confirm, VWAP, or firing. armed,
            # below_stop_count, tp_armed, above_tp_count, para_armed,
            # prev_return, breakeven_locked, hwm_hold_ticks, vwap_ticks,
            # vwap_bleed_ticks all stay exactly as they were.
            out.append({"tick_idx": tick_idx, "exit_reason": None})
            continue

        para_threshold = params.get("PARABOLIC_VELOCITY_THRESHOLD", 2.0)
        effective_prev = ret if prev_return is None else prev_return
        _velocity, should_arm_para = math_engine.compute_para_arm_decision(
            current_return=ret,
            prev_return=effective_prev,
            para_threshold=para_threshold,
            currently_armed=para_armed,
        )
        prev_return = ret
        if should_arm_para:
            para_armed = True

        # MC arm / disarm (alpha_bot_execution.py:1302-1347). MA-10 fail-open:
        # an ABSENT MC opinion (mc_available=False) ARMS — never silently
        # leaves the protective stop dark. should_arm is reset every tick
        # (matches production's should_arm = False at the top of this block).
        should_arm = False
        if mc_available and take_profit_mc <= mc < trigger_threshold:
            should_arm = True
        elif not mc_available:
            should_arm = True  # MA-10 fail-open (:1324-1326)

        if should_arm and not armed:
            armed = True
        elif armed:
            if mc_available and mc > (trigger_threshold * 2) and ret > 0.0:
                armed = False
                below_stop_count = 0

        time_ratio = tick_idx / max(1, len(ticks) - 1)
        dynamic_multiplier, dynamic_min_stop = math_engine.compute_time_squeeze_decay(time_ratio)
        active_stop_dist = math_engine.compute_active_trailing_stop(
            vol,
            dynamic_multiplier,
            dynamic_min_stop,
            para_armed,
            breakeven_locked,
            params.get("MAX_PARABOLIC_SQUEEZE", 0.50),
        )
        base_stop = safe_hwm - active_stop_dist
        hwm_hold_ticks, breakeven_locked, stop_level = math_engine.compute_breakeven_update(
            ret,
            vol,
            base_stop,
            hwm_hold_ticks,
            breakeven_locked,
            False,
        )

        # Check 1: Trailing Stop — the real production primitive. AC-4/F5:
        # exit_confirm_ticks is now the regime-resolved value, never the
        # implicit module default.
        below_stop_count, is_trailing_hit = math_engine.compute_exit_confirmation(
            armed=armed,
            is_triggered=False,
            current_return=ret,
            stop_trigger_level=stop_level,
            prob_underperforming=mc,
            current_below_stop_count=below_stop_count,
            exit_confirm_ticks=_regime_exit_ticks,
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
            prob_underperforming=mc,
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
        if math_engine.is_in_open_window_grace(current_et, effective_exec_start, grace_minutes):
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
    return autotuner.replay_exit_sequence(ticks, params, grace_minutes=grace_minutes)


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

    production = _production_exit_sequence(ticks, params, grace_minutes=grace_minutes)
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
