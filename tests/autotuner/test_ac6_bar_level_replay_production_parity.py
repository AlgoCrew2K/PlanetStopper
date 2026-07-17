"""
AC-6 (the parity battery -- the charter's acceptance heart) + AC-2 (exit
reachability, regression-pinned).

WHAT THIS FILE PROVES, one layer deeper than the existing tick-level battery
--------------------------------------------------------------------------
test_c3_replay_exit_parity.py's existing battery starts from PRE-BAKED
tick dicts (return/mc_prob supplied directly by the fixture) -- it validates
the EXIT-DECISION orchestration (arm/disarm/confirm/fire) but says nothing
about whether the mc_prob value reaching that orchestration is itself
faithful (AC-1's whole subject). This file closes that gap: it starts from
RAW BAR DATA (per-tick close prices, prior-session closes, MC history) and
drives it through synthetic_history.build_replay_day -- the SAME function
AC-1 fixes -- to produce the tick series BOTH sides then consume. Exercising
AC-1, AC-3, AC-5 (and, transitively, math_engine's unmodified primitives)
together is exactly what "reachability falls out of AC-1 alone" (AC-2's
hypothesis, PM addendum @ debc9537) is there to adjudicate.

Design (kept minimal per PM ruling -- addendum 2 @ a46be889: "the new
bar-level reference harness must use the REAL math_engine decision
primitives with only the orchestration mirrored"):
  1. Two INDEPENDENT tick builders consume the SAME raw bars
     (closes/historical_data/yesterday_close/spy_today):
       - _production_ticks_from_bars: a minimal orchestration mirror that
         computes lpc per tick via the exact (c - y_close)/y_close formula
         synthetic_history.build_replay_day uses (cited inline), then calls
         the REAL math_engine.run_monte_carlo/compute_regime_match_quality/
         compute_vwap_signals/calculate_20d_vol/calculate_14d_atr_pct
         directly -- it does NOT call build_replay_day itself, so it is
         correct independent of whether AC-1 has landed. This mirrors what
         production's real per-tick value would be.
       - _replay_ticks_from_bars: calls the REAL
         synthetic_history.build_replay_day -- the ACTUAL replay path AC-1
         patches.
     IMPORTANT: an earlier draft of this file fed BOTH sides ticks built via
     build_replay_day, which made the two sides share the SAME upstream bug
     pre-AC-1 (both consuming the identical degenerate mc_prob), so their
     decisions matched TRIVIALLY regardless of whether AC-1 had landed --
     silently defeating the parity check for every price-driven-MC scenario.
     The two-independent-builders design above is required to make AC-1's
     effect observable through this file at all.
  2. _production_ticks_from_bars' output feeds
     test_c3_replay_exit_parity._production_exit_sequence (already synced to
     real production shape for AC-3/AC-4/AC-5 -- see that file) as the
     DECISION-LOGIC reference.
  3. _replay_ticks_from_bars' output feeds autotuner.replay_exit_sequence --
     the actual replay path.
  4. Compare (tick_idx, exit_reason) AND (armed, tp_armed, para_armed) --
     state parity, not just decision parity (PM: "armed/disarmed states").
     Pre-AC-1, the two tick series themselves diverge (degenerate vs real
     per-tick mc_prob), which is exactly what should make the MC-driven
     scenarios (trailing_stop, take_profit, exec_start_gate) fail RED.

AC-4 (regime-conditional ticks) is deliberately OUT of this file's direct
comparison: replay_exit_sequence's signature (ticks, params, *,
grace_minutes) has no regime_label/trailing-history input -- day-level
regime resolution lives above this single-day entry point (see
test_ac4_regime_conditional_exit_ticks.py, which tests _replay_exit_tick's
regime wiring directly and exhaustively). Every scenario here uses
regime_label=None on the production side, matching what replay_exit_sequence
implicitly does absent day-level regime wiring -- both sides stay aligned by
construction; AC-4's correctness is proven in its own file, not duplicated
here.

RED EXPECTATION (pre-fix)
-------------------------
Pre-AC-1: build_replay_day's ticks never carry the real per-tick lpc, so
mc_prob is day-constant/degenerate -- scenarios engineered to need MC
breakdown confirmation (trailing_stop_fire, take_profit_fire) either never
fire or fire on the wrong tick.
Pre-AC-3: mc_absent_fail_open_fire never arms (MA-10).
Pre-AC-5: exec_start_pre_action_gate fires before the action phase opens.
Pre state-parity extension: replay_exit_sequence's output has no
armed/tp_armed/para_armed keys at all -- every state-parity assertion fails
structurally.
"""

from __future__ import annotations

import pathlib

import pytest

import autotuner
import math_engine
import synthetic_history
from tests.autotuner.test_c3_replay_exit_parity import _production_exit_sequence

_DEFAULT_PARAMS = {
    "TRIGGER_THRESHOLD_PCT": 15.0,
    "TAKE_PROFIT_MC_PCT": 5.0,
    "VWAP_CROSS_HWM_PCT": 1.0,
    "VWAP_BLEED_MULTIPLIER": 1.5,
    "VWAP_BLEED_TICKS": 10,
    "PARABOLIC_VELOCITY_THRESHOLD": 2.0,
    "MAX_PARABOLIC_SQUEEZE": 0.5,
}

# Varied, deterministic 45-day (SPY, AAA) daily-return history -- reused
# verbatim from tests/fixtures/math_engine/ma1_build_replay_day_lpc_stamping.json
# (AC-1's empirically-validated fixture: is_unprecedented=False under
# math_engine.compute_regime_match_quality, eligible_days=26 >= MC_MIN_HISTORY_DAYS,
# and produces genuinely varying mc_prob across a range of lpc values -- an
# alternating 2-value series was tried first and rejected for tripping the
# regime-match-quality guard; see that fixture's derivation note).
_HISTORY_ROWS = [
    {"date": "2026-01-01", "SPY": 0.008699, "AAA": -0.003372},
    {"date": "2026-01-02", "SPY": -0.011317, "AAA": -0.004389},
    {"date": "2026-01-03", "SPY": -0.004296, "AAA": -0.012604},
    {"date": "2026-01-04", "SPY": -0.000152, "AAA": 0.012547},
    {"date": "2026-01-05", "SPY": -0.008223, "AAA": 0.002952},
    {"date": "2026-01-06", "SPY": 0.004126, "AAA": -0.015204},
    {"date": "2026-01-07", "SPY": -0.008957, "AAA": -0.014317},
    {"date": "2026-01-08", "SPY": -0.003915, "AAA": -0.013142},
    {"date": "2026-01-09", "SPY": -0.006295, "AAA": 0.007542},
    {"date": "2026-01-10", "SPY": 0.004041, "AAA": -0.010652},
    {"date": "2026-01-11", "SPY": -0.001306, "AAA": 0.016507},
    {"date": "2026-01-12", "SPY": -0.003925, "AAA": -0.007241},
    {"date": "2026-01-13", "SPY": -0.007962, "AAA": -0.006551},
    {"date": "2026-01-14", "SPY": -0.003467, "AAA": -0.004219},
    {"date": "2026-01-15", "SPY": -0.0043, "AAA": -0.007718},
    {"date": "2026-01-16", "SPY": -0.007974, "AAA": 0.004053},
    {"date": "2026-01-17", "SPY": -0.000363, "AAA": -0.002374},
    {"date": "2026-01-18", "SPY": 0.007428, "AAA": 0.008943},
    {"date": "2026-01-19", "SPY": -0.000522, "AAA": -0.013318},
    {"date": "2026-01-20", "SPY": 0.002671, "AAA": 0.016431},
    {"date": "2026-01-21", "SPY": -0.000467, "AAA": 0.016775},
    {"date": "2026-01-22", "SPY": 0.009194, "AAA": 0.009092},
    {"date": "2026-01-23", "SPY": 0.007846, "AAA": 0.006637},
    {"date": "2026-01-24", "SPY": -0.000806, "AAA": -0.010847},
    {"date": "2026-01-25", "SPY": -0.006622, "AAA": 0.004824},
    {"date": "2026-01-26", "SPY": -0.002043, "AAA": 0.014662},
    {"date": "2026-01-27", "SPY": -0.006684, "AAA": -0.010073},
    {"date": "2026-01-28", "SPY": 0.005139, "AAA": 0.00115},
    {"date": "2026-02-01", "SPY": -0.009975, "AAA": -0.007617},
    {"date": "2026-02-02", "SPY": 0.006892, "AAA": 0.001378},
    {"date": "2026-02-03", "SPY": -0.009569, "AAA": -0.019655},
    {"date": "2026-02-04", "SPY": -0.011437, "AAA": 0.002203},
    {"date": "2026-02-05", "SPY": 0.002328, "AAA": 0.003094},
    {"date": "2026-02-06", "SPY": 0.007038, "AAA": -0.0099},
    {"date": "2026-02-07", "SPY": 0.007598, "AAA": -0.000543},
    {"date": "2026-02-08", "SPY": -0.006786, "AAA": -0.003091},
    {"date": "2026-02-09", "SPY": -0.006137, "AAA": 0.012108},
    {"date": "2026-02-10", "SPY": 0.007932, "AAA": 0.012117},
    {"date": "2026-02-11", "SPY": 0.011648, "AAA": 0.000198},
    {"date": "2026-02-12", "SPY": -0.002905, "AAA": -0.015545},
    {"date": "2026-02-13", "SPY": -0.000372, "AAA": 0.018246},
    {"date": "2026-02-14", "SPY": -0.006657, "AAA": -0.005041},
    {"date": "2026-02-15", "SPY": -0.004881, "AAA": 0.001285},
    {"date": "2026-02-16", "SPY": 0.004643, "AAA": 0.014488},
    {"date": "2026-02-17", "SPY": -0.01139, "AAA": 0.016439},
]
_SPY_TODAY_RETURN = 0.4
_YESTERDAY_CLOSE = 100.0


def _historical_data() -> dict:
    return {
        row["date"]: {"SPY": {"daily_ret": row["SPY"]}, "AAA": {"daily_ret": row["AAA"]}}
        for row in _HISTORY_ROWS
    }


def _very_short_historical_data() -> dict:
    """Only 10 raw days -> eligible_days = 10 - 19 < 0 < MC_MIN_HISTORY_DAYS:
    run_monte_carlo returns the insufficient-history sentinel (None) for
    EVERY tick regardless of lpc -- the deliberate mechanism for an
    MC-absent scenario, distinct from (and simpler to construct than)
    triggering the regime-match-quality guard."""
    return {row["date"]: {"SPY": {"daily_ret": row["SPY"]}, "AAA": {"daily_ret": row["AAA"]}} for row in _HISTORY_ROWS[:10]}


_SYM_ID = "sym-ac6-001"
_DATE_STR = "2026-04-06"
_TICKER = "AAA"


def _replay_ticks_from_bars(*, closes: list[float], historical_data: dict) -> list[dict]:
    """Build one day's tick series via the REAL synthetic_history.build_replay_day
    -- the ACTUAL replay path AC-1 patches. This is the REPLAY side of the
    parity comparison."""
    holdings = [{"ticker": _TICKER, "allocation": 1.0}]
    timestamps = [f"{_DATE_STR}T09:{30 + i:02d}:00Z" for i in range(len(closes))]
    intraday_by_date = {_DATE_STR: {_TICKER: [{"t": ts, "c": c, "vwap": c} for ts, c in zip(timestamps, closes)]}}
    yesterday_closes = {_TICKER: _YESTERDAY_CLOSE}
    return synthetic_history.build_replay_day(
        sym_id=_SYM_ID,
        date_str=_DATE_STR,
        holdings=holdings,
        intraday_by_date=intraday_by_date,
        timestamps=timestamps,
        hist_data_up_to_yesterday=historical_data,
        yesterday_closes=yesterday_closes,
        spy_today=_SPY_TODAY_RETURN,
    )


def _production_ticks_from_bars(*, closes: list[float], historical_data: dict) -> list[dict]:
    """Independently reconstruct the per-tick production tick series from
    raw bars -- a minimal orchestration mirror, NOT a call to
    build_replay_day, so this reference is correct regardless of whether
    AC-1 has landed (see the module docstring's IMPORTANT note on why the
    two builders must be independent).

    Mirrors, citing the exact production lines:
      - lpc = (c - yesterday_close) / yesterday_close -- the same formula
        the loop in synthetic_history.build_replay_day already computes for
        its aggregate return (synthetic_history.py ~line 417).
      - the REAL math_engine.run_monte_carlo call with per-tick-stamped
        holdings, using the SAME per-(symphony, day) seed formula
        build_replay_day uses (synthetic_history.py:434:
        derive_cycle_mc_seed(f"{sym_id}_{date_str}") -- constant across
        every tick in the day, matching production's real determinism
        contract) -- mirrors the live call, alpha_bot_execution.py:1270.
      - the REAL math_engine.compute_regime_match_quality suppression,
        mirroring synthetic_history.py:447-449.
      - the REAL math_engine.compute_vwap_signals / calculate_20d_vol /
        calculate_14d_atr_pct -- unmodified by this cycle, called exactly
        as build_replay_day calls them.
    """
    holdings_base = [{"ticker": _TICKER, "allocation": 1.0}]
    vol = math_engine.calculate_20d_vol(holdings_base, historical_data)
    base_atr = math_engine.calculate_14d_atr_pct(holdings_base, historical_data)
    seed = math_engine.derive_cycle_mc_seed(f"{_SYM_ID}_{_DATE_STR}")

    ticks: list[dict] = []
    for i, c in enumerate(closes):
        lpc = (c - _YESTERDAY_CLOSE) / _YESTERDAY_CLOSE
        holdings = [{"ticker": _TICKER, "allocation": 1.0, "last_percent_change": lpc}]
        mc_prob = math_engine.run_monte_carlo(
            holdings,
            historical_data,
            _SPY_TODAY_RETURN,
            math_engine.MC_DEFAULT_SIMULATION_PATHS,
            math_engine.MC_DEFAULT_NEIGHBOR_K,
            seed=seed,
        )
        regime = math_engine.compute_regime_match_quality(historical_data, _SPY_TODAY_RETURN)
        if regime.is_unprecedented:
            mc_prob = None

        live_vwaps = {_TICKER: {"last_price": c, "vwap": c}}
        vwap_diff, valid_vwap_weight = math_engine.compute_vwap_signals(holdings, live_vwaps)

        ticks.append(
            {
                "time": f"09:{30 + i:02d}",
                "return": lpc * 100.0,
                "mc_prob": mc_prob,
                "vol": vol,
                "vwap_diff": vwap_diff,
                "base_atr_pct": base_atr,
                "valid_vwap_weight": valid_vwap_weight,
            }
        )
    return ticks


def _decisions(seq: list[dict]) -> list[tuple[int, str | None]]:
    return [(d["tick_idx"], d["exit_reason"]) for d in seq]


def _states(seq: list[dict]) -> list[tuple[bool, bool, bool]]:
    return [(d["armed"], d["tp_armed"], d["para_armed"]) for d in seq]


# ===========================================================================
# State-parity contract extension on replay_exit_sequence (new RED
# requirement -- mirrors the extension already made to
# _production_exit_sequence's output shape).
# ===========================================================================


def test_replay_exit_sequence_exposes_armed_state_per_tick() -> None:
    """AC-6 requires state parity ('armed/disarmed states'), not just
    exit-decision parity. replay_exit_sequence's output must therefore carry
    armed/tp_armed/para_armed per tick, mirroring the extension already made
    to _production_exit_sequence.

    RED (pre-fix): replay_exit_sequence's dicts only have tick_idx/exit_reason.
    """
    ticks = [
        {"time": "09:30", "return": 1.0, "mc_prob": 50.0, "vol": 0.5, "vwap_diff": 0.0, "base_atr_pct": 0.5, "valid_vwap_weight": 0.0}
    ]
    out = autotuner.replay_exit_sequence(ticks, _DEFAULT_PARAMS, grace_minutes=0)
    assert out, "replay_exit_sequence returned an empty sequence for a 1-tick input."
    missing = {"armed", "tp_armed", "para_armed"} - set(out[0].keys())
    assert not missing, (
        f"replay_exit_sequence's per-tick output is missing key(s) {sorted(missing)}. "
        "AC-6's state-parity assertions (armed/tp_armed/para_armed) cannot compare "
        "against the replay without them."
    )


# ===========================================================================
# The bar-level parity battery.
# ===========================================================================


def test_trailing_stop_reachable_and_replay_matches_production_bar_level() -> None:
    """AC-2 + AC-6: a canned day whose bar prices swing deep negative vs
    prior close (driving a real, non-degenerate mc_prob via AC-1's fix) must
    fire a Trailing Stop -- and the replay must fire it on the SAME tick as
    the production reference, with matching armed-state trace.

    RED pre-AC-1: build_replay_day's mc_prob is day-constant (degenerate),
    so this may never fire or fire on the wrong tick (AC-2's never-fires
    regression case).
    """
    # c=101.5 (lpc=+1.5%) empirically produces mc_prob=14.82, inside the
    # DEFAULT arm band [TAKE_PROFIT_MC_PCT=5.0, TRIGGER_THRESHOLD_PCT=15.0)
    # -- verified live against _HISTORY_ROWS/this seed, not hardcoded as an
    # expected decision. Deliberately NOT using a widened custom band here:
    # the DEGENERATE pre-AC-1 mc_prob (current_symphony_return always
    # excluded -> always the c=100/lpc=0.0 baseline, mc_prob≈64.98) sits
    # OUTSIDE the default [5,15) band, so pre-fix this scenario correctly
    # never arms at all (a widened band bracketing 64.98 would have let the
    # degenerate value arm too, making this scenario fail to discriminate
    # AC-1's fix -- an earlier draft made exactly that mistake).
    closes = [101.5, 80.0, 78.0, 76.0, 74.0, 72.0]  # tick0 arms; deep sustained decline confirms
    hist = _historical_data()
    production_ticks = _production_ticks_from_bars(closes=closes, historical_data=hist)
    replay_ticks = _replay_ticks_from_bars(closes=closes, historical_data=hist)

    production = _production_exit_sequence(production_ticks, _DEFAULT_PARAMS, grace_minutes=0)
    replay = autotuner.replay_exit_sequence(replay_ticks, _DEFAULT_PARAMS, grace_minutes=0)

    assert any(d["exit_reason"] is not None for d in production), (
        "Fixture self-check: this scenario must produce a production exit — "
        "revise the close-price sequence if it does not."
    )
    assert _decisions(replay) == _decisions(production), (
        f"Bar-level trailing-stop scenario diverged.\n  production: {_decisions(production)}\n"
        f"  replay:     {_decisions(replay)}"
    )
    assert _states(replay) == _states(production), (
        f"Bar-level trailing-stop scenario's armed/tp_armed/para_armed trace diverged.\n"
        f"  production: {_states(production)}\n  replay:     {_states(replay)}"
    )


def test_take_profit_reachable_and_replay_matches_production_bar_level() -> None:
    """AC-2 + AC-6: a canned day whose bar prices climb (positive lpc drives
    a LOW mc_prob = strong outperformance) confirms a Take Profit exit.
    """
    # c=102 (lpc=+2%) empirically produces mc_prob=0.0 (< TAKE_PROFIT_MC_PCT=5.0
    # default -> ARMS TP), c=101 (lpc=+1%) empirically produces mc_prob≈30.4
    # (>= 5.0, with return > 0 -> CONFIRMS; TP_CONFIRM_TICKS=2 consecutive
    # such ticks needed) -- verified live against _HISTORY_ROWS/this seed,
    # not hardcoded as an expected decision.
    closes = [100.0, 102.0, 101.0, 101.0]
    hist = _historical_data()
    production_ticks = _production_ticks_from_bars(closes=closes, historical_data=hist)
    replay_ticks = _replay_ticks_from_bars(closes=closes, historical_data=hist)

    production = _production_exit_sequence(production_ticks, _DEFAULT_PARAMS, grace_minutes=0)
    replay = autotuner.replay_exit_sequence(replay_ticks, _DEFAULT_PARAMS, grace_minutes=0)

    assert any(d["exit_reason"] is not None for d in production), (
        "Fixture self-check: this scenario must produce a production Take Profit "
        f"exit — got {_decisions(production)}. Revise the close-price sequence if "
        "it never fires (e.g. mc_prob may not be dropping low enough, or not for "
        "enough consecutive ticks to satisfy TP_CONFIRM_TICKS)."
    )
    assert _decisions(replay) == _decisions(production), (
        f"Bar-level take-profit scenario diverged.\n  production: {_decisions(production)}\n"
        f"  replay:     {_decisions(replay)}"
    )
    assert _states(replay) == _states(production)


def test_mc_absent_fail_open_reachable_and_replay_matches_production_bar_level() -> None:
    """AC-3 + AC-6: with insufficient MC history (every tick's mc_prob is the
    None sentinel, by construction of a too-short historical_data_spec), the
    fail-open arm still lets a Trailing Stop confirm via magnitude alone once
    below-stop ticks accumulate.

    RED pre-AC-3 (MA-10): the replay never arms on an MC-absent tick while
    unarmed, so this scenario stays unarmed and never fires — production
    (post-oracle-sync) does fire.

    Note: this scenario is MC-availability-driven, not lpc-magnitude-driven
    (insufficient history returns None regardless of AC-1's fix), so it is
    the one scenario in this file where the two-independent-builders design
    is not load-bearing for reaching the sentinel — but it stays on the
    independent-builder path for consistency with the rest of the file.
    """
    closes = [100.0, 80.0, 78.0, 76.0, 74.0, 72.0]
    hist = _very_short_historical_data()
    production_ticks = _production_ticks_from_bars(closes=closes, historical_data=hist)
    replay_ticks = _replay_ticks_from_bars(closes=closes, historical_data=hist)
    assert all(t["mc_prob"] is None for t in production_ticks), (
        "Fixture self-check: this scenario requires every production tick's mc_prob "
        "to be the insufficient-history sentinel (None) — revise "
        "_very_short_historical_data if any tick returns a real value."
    )

    production = _production_exit_sequence(production_ticks, _DEFAULT_PARAMS, grace_minutes=0)
    replay = autotuner.replay_exit_sequence(replay_ticks, _DEFAULT_PARAMS, grace_minutes=0)

    assert any(d["exit_reason"] is not None for d in production), (
        "Fixture self-check: the production reference (post-oracle-sync, MA-10 "
        "fail-open) must fire on this MC-absent-throughout scenario."
    )
    assert _decisions(replay) == _decisions(production), (
        f"Bar-level MC-absent fail-open scenario diverged.\n  production: {_decisions(production)}\n"
        f"  replay:     {_decisions(replay)}"
    )
    assert _states(replay) == _states(production)


def test_no_exit_day_replay_matches_production_bar_level() -> None:
    """AC-6: a calm day (small price wiggles around the prior close, MC
    never breaks down) produces no exit on either side."""
    closes = [100.0, 100.3, 99.8, 100.2, 99.9, 100.1]
    hist = _historical_data()
    production_ticks = _production_ticks_from_bars(closes=closes, historical_data=hist)
    replay_ticks = _replay_ticks_from_bars(closes=closes, historical_data=hist)

    production = _production_exit_sequence(production_ticks, _DEFAULT_PARAMS, grace_minutes=0)
    replay = autotuner.replay_exit_sequence(replay_ticks, _DEFAULT_PARAMS, grace_minutes=0)

    assert all(d["exit_reason"] is None for d in production), (
        "Fixture self-check: this calm-day scenario must produce NO production exit "
        f"— got {_decisions(production)}."
    )
    assert _decisions(replay) == _decisions(production)
    assert _states(replay) == _states(production)


def test_execution_start_time_pre_action_gate_replay_matches_production_bar_level() -> None:
    """AC-5 + AC-6: with EXECUTION_START_TIME='09:35' (droplet-real value),
    a bar sequence engineered to confirm a Trailing Stop within the first
    few ticks must not fire until the action phase opens (tick_idx >= 5) on
    EITHER side.

    RED pre-AC-5: the replay has no action-phase gate at all and fires
    before tick_idx=5; production (post-oracle-sync) does not.
    """
    # Ticks 0-4 (before the action phase opens at offset=5) are gated no
    # matter what price they carry -- padded at the prior close (c=100,
    # ret=0.0) so HWM stays flat. Tick 5 (c=101.5, mc_prob=14.82 empirically
    # -- inside the DEFAULT arm band [5,15), same value verified for the
    # trailing-stop scenario above) is the FIRST evaluated tick and arms.
    # The degenerate pre-AC-1 mc_prob (always the c=100/lpc=0.0 baseline,
    # mc_prob≈64.98) sits outside [5,15), so pre-fix this scenario correctly
    # never arms regardless of the action-phase gate -- deliberately NOT
    # using a widened band (see the trailing-stop scenario's note on why
    # that would fail to discriminate AC-1's fix). Ticks 6-8 (deep decline)
    # confirm via magnitude + MC_BREAKDOWN_THRESHOLD=60.0.
    closes = [100.0, 100.0, 100.0, 100.0, 100.0, 101.5, 80.0, 78.0, 76.0]
    hist = _historical_data()
    production_ticks = _production_ticks_from_bars(closes=closes, historical_data=hist)
    replay_ticks = _replay_ticks_from_bars(closes=closes, historical_data=hist)
    exec_start = "09:35"

    production = _production_exit_sequence(
        production_ticks, _DEFAULT_PARAMS, grace_minutes=0, execution_start_hhmm=exec_start
    )
    prod_exit = next((d for d in production if d["exit_reason"] is not None), None)
    assert prod_exit is not None, (
        "Fixture self-check: the production reference must still fire once the "
        f"action phase opens (tick_idx >= 5) — got {_decisions(production)}. Revise "
        "the close-price sequence if it does not fire at all."
    )
    assert prod_exit["tick_idx"] >= 5, (
        f"Fixture self-check: production fired at tick_idx={prod_exit['tick_idx']}, "
        "before its own action-phase gate (offset=5) — the reference itself is "
        "misconfigured."
    )

    # NOTE: autotuner.replay_exit_sequence resolves EXECUTION_START_TIME
    # internally via _replay_execution_start_time() (reads
    # alpha_bot_execution.EXECUTION_START_TIME) — monkeypatch that module
    # attribute to exercise the droplet-real value end to end, exactly as
    # test_n3_replay_grace_execution_start_time.py's convention establishes.
    import alpha_bot_execution

    original = alpha_bot_execution.EXECUTION_START_TIME
    alpha_bot_execution.EXECUTION_START_TIME = exec_start
    try:
        replay = autotuner.replay_exit_sequence(replay_ticks, _DEFAULT_PARAMS, grace_minutes=0)
    finally:
        alpha_bot_execution.EXECUTION_START_TIME = original

    assert _decisions(replay) == _decisions(production), (
        f"Bar-level EXECUTION_START_TIME action-phase-gate scenario diverged.\n"
        f"  production: {_decisions(production)}\n  replay:     {_decisions(replay)}"
    )
    assert _states(replay) == _states(production)
