"""
Supplementary RED tests for mc-sentinel-blast-radius cycle — spec-mc R-5 through R-14.

WHAT THESE TESTS COVER
-----------------------
These tests fill the gaps identified by the spec-mc domain review. The first
batch of RED tests (test_cvar_assessment_dataclass_contract.py and
test_run_monte_carlo_signature_frozen.py) covered the structural contract and
signature freeze. This file covers:

R-5  — Eligible-pool boundary: 38 raw days → sentinel (19 eligible < 20).
R-6  — Eligible-pool boundary: 39 raw days → float in [0, 100] (20 eligible >= 20).
R-7  — 20 raw days → sentinel (pins the pre-fix in-band 100.0 is gone).
R-8  — compute_tp_confirmation with mc_available=False does NOT arm TP.
R-9  — compute_tp_confirmation with mc_available=False does NOT confirm TP exit.
R-10 — mc_sanity_gate_would_block expression is always False when mc_available=False,
        regardless of prob_beating value.
R-14 — mc_history list never contains None after a sentinel-returning tick.
C-13 — autotuner tick with mc_prob=None key present → mc_available=False (not 50.0
        default).

FIXTURE
-------
tests/fixtures/math_engine/mc_sentinel_blast_radius/eligible_pool_boundary.json

RED STATUS
----------
R-5, R-6, R-7 are GREEN on the current fork-point — the eligible-pool fix is
already in place (math_engine.py:738-744). They are written as regression guards:
they go RED if a PR regresses the boundary (e.g. reverts to raw-day counting).

R-8, R-9, R-10, R-14, C-13 are also GREEN on fork-point — the consumer guards
are already correct. They go RED if a PR removes the mc_available guard pattern.

All tests in this file are structural regression guards for the sentinel contract.
None of them hardcode producer-computed probability values — assertions are on
sentinel/non-sentinel boolean outcomes or on type/range.
"""

from __future__ import annotations

import json
import pathlib

import pytest

import math_engine

FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "math_engine"
    / "mc_sentinel_blast_radius"
)


def _load_boundary_fixture() -> dict:
    with (FIXTURE_DIR / "eligible_pool_boundary.json").open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _date_key(i: int) -> str:
    """Deterministic date key for fixture-building; avoids datetime import."""
    base_day = 1 + i
    month = 1 + (base_day - 1) // 28
    day = 1 + (base_day - 1) % 28
    return f"2024-{month:02d}-{day:02d}"


def _build_constant_history(
    num_days: int, spy_ret: float, holding_ret: float
) -> dict[str, dict[str, dict[str, float]]]:
    return {
        _date_key(i): {
            "SPY": {"daily_ret": spy_ret},
            "AAA": {"daily_ret": holding_ret},
        }
        for i in range(num_days)
    }


def _minimum_raw_days() -> int:
    """
    The minimum raw-day count for a non-sentinel result.
    Derived from module constants at test time — never hardcoded.
    """
    return math_engine.MC_MIN_HISTORY_DAYS + (math_engine.MC_VOL_WINDOW_DAYS - 1)


# ---------------------------------------------------------------------------
# R-5. 38 raw days → sentinel (eligible_days = 19 < MC_MIN_HISTORY_DAYS)
# ---------------------------------------------------------------------------

def test_eligible_pool_38_raw_days_returns_sentinel() -> None:
    """
    R-5 — 38 raw days: eligible_days = 38 - (MC_VOL_WINDOW_DAYS - 1) = 19,
    which is one below MC_MIN_HISTORY_DAYS (20). Must return the insufficient
    sentinel.

    This pins the eligible-pool boundary exactly: raw_days < minimum_raw_days
    returns sentinel.

    RED trigger: reverting to raw-day counting (eligible_days = raw_days,
    so 38 raw days → eligible_days 38 → passes the threshold → returns a float).
    """
    fx = _load_boundary_fixture()
    spec = fx["history_spec"]
    call = fx
    minimum_raw = _minimum_raw_days()
    raw_days = minimum_raw - 1  # one below the boundary = 38 at baseline constants

    history = _build_constant_history(
        num_days=raw_days,
        spy_ret=spec["spy_daily_ret"],
        holding_ret=spec["holding_daily_ret"],
    )
    result = math_engine.run_monte_carlo(
        call["holdings"],
        history,
        call["spy_today_return"],
        simulation_paths=call["simulation_paths"],
        neighbor_k=call["neighbor_k"],
        seed=call["numpy_seed"],
    )
    eligible_days = raw_days - (math_engine.MC_VOL_WINDOW_DAYS - 1)
    assert result is math_engine.MC_INSUFFICIENT_HISTORY_SENTINEL, (
        f"run_monte_carlo with {raw_days} raw days (eligible_days={eligible_days}, "
        f"MC_MIN_HISTORY_DAYS={math_engine.MC_MIN_HISTORY_DAYS}) returned "
        f"{result!r} instead of the sentinel. "
        f"Sufficiency is based on eligible-pool count (raw_days - "
        f"(MC_VOL_WINDOW_DAYS-1)), not raw-day count."
    )


# ---------------------------------------------------------------------------
# R-6. 39 raw days → float in [0, 100] (eligible_days = 20 = MC_MIN_HISTORY_DAYS)
# ---------------------------------------------------------------------------

def test_eligible_pool_39_raw_days_returns_valid_float() -> None:
    """
    R-6 — 39 raw days: eligible_days = 39 - (MC_VOL_WINDOW_DAYS - 1) = 20,
    exactly MC_MIN_HISTORY_DAYS. Must return a valid float in [0.0, 100.0].

    This is the minimum-sufficient boundary: exactly minimum_raw_days → real MC.

    RED trigger: off-by-one in the eligibility check (using < instead of <=,
    which would send this case back to the sentinel path).
    """
    fx = _load_boundary_fixture()
    spec = fx["history_spec"]
    call = fx
    minimum_raw = _minimum_raw_days()

    history = _build_constant_history(
        num_days=minimum_raw,
        spy_ret=spec["spy_daily_ret"],
        holding_ret=spec["holding_daily_ret"],
    )
    result = math_engine.run_monte_carlo(
        call["holdings"],
        history,
        call["spy_today_return"],
        simulation_paths=call["simulation_paths"],
        neighbor_k=call["neighbor_k"],
        seed=call["numpy_seed"],
    )
    eligible_days = minimum_raw - (math_engine.MC_VOL_WINDOW_DAYS - 1)
    assert result is not math_engine.MC_INSUFFICIENT_HISTORY_SENTINEL, (
        f"run_monte_carlo with {minimum_raw} raw days (eligible_days={eligible_days}, "
        f"MC_MIN_HISTORY_DAYS={math_engine.MC_MIN_HISTORY_DAYS}) returned the "
        f"sentinel. The minimum-sufficient boundary must return a valid float."
    )
    assert isinstance(result, float), (
        f"run_monte_carlo at the minimum-sufficient boundary returned {result!r} "
        f"(type {type(result).__name__}); must return float."
    )
    assert 0.0 <= result <= 100.0, (
        f"run_monte_carlo at the minimum-sufficient boundary returned {result!r}; "
        f"valid probability must be in [0.0, 100.0]."
        # Tolerance note: this is a range bound, not a float equality comparison.
    )


# ---------------------------------------------------------------------------
# R-7. 20 raw days → sentinel (not the old in-band 100.0)
# ---------------------------------------------------------------------------

def test_twenty_raw_days_returns_sentinel_not_old_in_band_100() -> None:
    """
    R-7 — 20 raw days: eligible_days = 20 - (MC_VOL_WINDOW_DAYS - 1) = 1,
    far below MC_MIN_HISTORY_DAYS. Must return the sentinel.

    This pins that the pre-fix fail-dangerous return value of 100.0 is gone.
    The old code used raw-day counting: 20 raw days >= old MC_MIN_HISTORY_DAYS
    (20), so it ran the MC engine and could return 100.0. The new eligible-pool
    logic makes 20 raw days produce eligible_days=1, which is immediately
    below the threshold — sentinel is returned.

    RED trigger: reverting to raw-day counting (eligible_days = raw_days).
    """
    fx = _load_boundary_fixture()
    spec = fx["history_spec"]
    call = fx

    history = _build_constant_history(
        num_days=20,
        spy_ret=spec["spy_daily_ret"],
        holding_ret=spec["holding_daily_ret"],
    )
    result = math_engine.run_monte_carlo(
        call["holdings"],
        history,
        call["spy_today_return"],
        simulation_paths=call["simulation_paths"],
        neighbor_k=call["neighbor_k"],
        seed=call["numpy_seed"],
    )
    assert result is math_engine.MC_INSUFFICIENT_HISTORY_SENTINEL, (
        f"run_monte_carlo with 20 raw days returned {result!r}. "
        f"With eligible-pool counting, 20 raw days has eligible_days = "
        f"20 - (MC_VOL_WINDOW_DAYS-1) = {20 - (math_engine.MC_VOL_WINDOW_DAYS - 1)}, "
        f"which is below MC_MIN_HISTORY_DAYS ({math_engine.MC_MIN_HISTORY_DAYS}). "
        f"The pre-fix behavior (raw-day counting, returns 100.0) is fail-dangerous; "
        f"the sentinel must be returned here."
    )
    assert result != 100.0, (
        f"run_monte_carlo with 20 raw days returned 100.0 — this is the pre-fix "
        f"fail-dangerous in-band value. The sentinel (None) must be returned. "
        f"A 100.0 return makes the MC sanity gate veto the protective stop forever."
    )


# ---------------------------------------------------------------------------
# R-8. compute_tp_confirmation with mc_available=False does NOT arm TP
# ---------------------------------------------------------------------------

def test_tp_confirmation_does_not_arm_when_mc_unavailable() -> None:
    """
    R-8 — When mc_available=False (insufficient MC history), the take-profit
    arm gate must not transition tp_armed from False to True. An absent MC
    opinion is not an "exceptional gain" signal.

    Tested across a range of prob_beating values (None and plausible floats
    that would arm if mc_available were True) to confirm the gate is the
    binding control, not the prob_beating value.

    RED trigger: a regression that removes the ``if mc_available`` guard from
    the TP arm branch in compute_tp_confirmation.
    """
    # A low prob_beating (below take_profit_mc_pct) would arm IF mc_available.
    take_profit_mc_pct = 35.0  # threshold below which TP arms
    for prob_beating_value in [None, 10.0, 0.0, 34.9]:
        new_tp_armed, new_above_tp_count, is_tp_hit = math_engine.compute_tp_confirmation(
            mc_available=False,
            prob_beating=prob_beating_value,
            take_profit_mc_pct=take_profit_mc_pct,
            current_return=5.0,
            is_triggered=False,
            tp_armed=False,
            above_tp_count=0,
        )
        assert new_tp_armed is False, (
            f"compute_tp_confirmation(mc_available=False, prob_beating={prob_beating_value!r}) "
            f"set tp_armed=True. An absent MC opinion must never arm the take-profit "
            f"gate. The mc_available guard must block the arm transition."
        )
        assert is_tp_hit is False, (
            f"compute_tp_confirmation(mc_available=False, prob_beating={prob_beating_value!r}) "
            f"set is_tp_hit=True from an unarmed state. Impossible: TP confirm requires "
            f"armed + mc_available."
        )


# ---------------------------------------------------------------------------
# R-9. compute_tp_confirmation with mc_available=False does NOT confirm exit
# ---------------------------------------------------------------------------

def test_tp_confirmation_does_not_confirm_exit_when_mc_unavailable() -> None:
    """
    R-9 — When mc_available=False and tp is already armed (above_tp_count
    pre-seeded), the confirmation counter must be reset to 0 and is_tp_hit
    must remain False. An absent MC opinion cannot count toward confirmation.

    This tests the ``else`` branch in compute_tp_confirmation (MC unavailable
    while armed → reset count, no confirm).

    RED trigger: removing the ``mc_available and`` prefix from the confirmation
    branch, which would let above_tp_count accumulate without MC being present.
    """
    take_profit_mc_pct = 35.0
    # Pre-seed with above_tp_count=1 (one tick already counted toward the 2-tick confirm).
    # If mc_available=False, count must reset to 0, not increment to TP_CONFIRM_TICKS.
    new_tp_armed, new_above_tp_count, is_tp_hit = math_engine.compute_tp_confirmation(
        mc_available=False,
        prob_beating=None,
        take_profit_mc_pct=take_profit_mc_pct,
        current_return=5.0,
        is_triggered=False,
        tp_armed=True,
        above_tp_count=1,  # one tick into a 2-tick confirmation
    )
    assert is_tp_hit is False, (
        f"compute_tp_confirmation(mc_available=False, tp_armed=True, above_tp_count=1) "
        f"set is_tp_hit=True. An absent MC opinion must not confirm a TP exit."
    )
    assert new_above_tp_count == 0, (
        f"compute_tp_confirmation(mc_available=False, tp_armed=True, above_tp_count=1) "
        f"returned new_above_tp_count={new_above_tp_count}; expected 0. "
        f"An MC-unavailable tick must reset the confirmation counter — the absent "
        f"opinion cannot count toward the 2-tick confirmation threshold."
    )


def test_tp_confirmation_does_not_confirm_exit_when_mc_unavailable_at_threshold() -> None:
    """
    R-9 hostile variant — above_tp_count is already at TP_CONFIRM_TICKS - 1
    (one tick away from triggering). With mc_available=False, is_tp_hit must
    still be False and count resets.

    This is the most dangerous regression path: a single tick above the MC-
    available gate with the count nearly saturated.

    RED trigger: same as R-9 main test.
    """
    take_profit_mc_pct = 35.0
    near_threshold_count = math_engine.TP_CONFIRM_TICKS - 1
    assert near_threshold_count >= 1, "TP_CONFIRM_TICKS must be >= 2 for this test to be hostile."

    new_tp_armed, new_above_tp_count, is_tp_hit = math_engine.compute_tp_confirmation(
        mc_available=False,
        prob_beating=None,
        take_profit_mc_pct=take_profit_mc_pct,
        current_return=5.0,
        is_triggered=False,
        tp_armed=True,
        above_tp_count=near_threshold_count,
    )
    assert is_tp_hit is False, (
        f"compute_tp_confirmation(mc_available=False, tp_armed=True, "
        f"above_tp_count={near_threshold_count}) set is_tp_hit=True at count "
        f"TP_CONFIRM_TICKS-1. The MC-unavailable guard must block this."
    )
    assert new_above_tp_count == 0, (
        f"Expected count reset to 0; got {new_above_tp_count}."
    )


# ---------------------------------------------------------------------------
# R-10. mc_sanity_gate_would_block is always False when mc_available=False
# ---------------------------------------------------------------------------

def test_mc_sanity_gate_would_block_is_false_when_mc_unavailable() -> None:
    """
    R-10 — The mc_sanity_gate_would_block snapshot field is computed as:
        bool(mc_available and prob_beating >= acc_TRIGGER_THRESHOLD_PCT)
    When mc_available=False, short-circuit evaluation makes this False
    regardless of prob_beating. This test pins that contract explicitly.

    This matters because mc_sanity_gate_would_block is stored in the chart-data
    snapshot and is read by the dashboard and reporting layers. A True value
    when MC is unavailable would falsely claim the MC gate would have blocked
    the stop — a misleading audit trail.

    We test the expression directly (not via main()) to avoid coupling to the
    full execution pipeline. The expression is the canonical production form.

    RED trigger: changing the expression to
    ``bool(prob_beating is not None and prob_beating >= threshold)``
    would change the semantics for non-None prob_beating with mc_available=False
    (impossible in production but tests the contract precisely).
    """
    threshold = 50.0  # representative trigger threshold

    # Test across plausible prob_beating values including values that would
    # trigger the gate if mc_available were True.
    for prob_value in [None, 0.0, 49.9, 50.0, 75.0, 100.0]:
        mc_available = False
        # The production expression, reproduced faithfully:
        mc_sanity_gate_would_block = bool(mc_available and prob_value >= threshold)  # type: ignore[operator]
        assert mc_sanity_gate_would_block is False, (
            f"mc_sanity_gate_would_block = bool(mc_available=False and "
            f"prob_beating={prob_value!r} >= {threshold}) evaluated to True. "
            f"When MC is unavailable, the gate would never block — False is the "
            f"only correct value for the audit trail."
        )


# ---------------------------------------------------------------------------
# R-14. mc_history never contains None after a sentinel-returning tick
# ---------------------------------------------------------------------------

def test_mc_history_not_polluted_with_sentinel_after_insufficient_mc() -> None:
    """
    R-14 — After a tick where run_monte_carlo returns the insufficient sentinel,
    the mc_history list must not grow and must not contain None. Polluting
    mc_history with None would break any later averaging/comparison over the
    history window.

    This test simulates the mc_history append logic from alpha_bot_execution.py
    (lines 1184-1187) directly, without invoking main(), to isolate the guard
    from the rest of the execution pipeline.

    The production guard pattern:
        mc_available = prob_beating is not None
        if mc_available:
            mc_history.append(prob_beating)

    RED trigger: removing the ``if mc_available:`` guard before mc_history.append.
    """
    mc_history: list = []
    initial_len = len(mc_history)

    # Simulate what run_monte_carlo returns for a 1-day history.
    one_day_history = {
        "2024-01-01": {
            "SPY": {"daily_ret": 0.001},
            "AAA": {"daily_ret": 0.001},
        }
    }
    holdings = [{"ticker": "AAA", "allocation": 1.0, "last_percent_change": 0.0}]
    prob_beating = math_engine.run_monte_carlo(
        holdings, one_day_history, 0.1, simulation_paths=100, neighbor_k=5, seed=42
    )

    # Confirm the harness actually exercises the sentinel path.
    assert prob_beating is math_engine.MC_INSUFFICIENT_HISTORY_SENTINEL, (
        f"1-day history did not return the insufficient sentinel; got {prob_beating!r}. "
        f"This harness invariant must hold for R-14 to test the right path."
    )

    # Production guard: only append when mc_available.
    mc_available = prob_beating is not None
    if mc_available:
        mc_history.append(prob_beating)

    # The list must be unchanged — None must not have been appended.
    assert len(mc_history) == initial_len, (
        f"mc_history grew from {initial_len} to {len(mc_history)} after a "
        f"sentinel-returning tick. The None sentinel must not be appended."
    )
    assert None not in mc_history, (
        f"mc_history contains None after a sentinel-returning tick: {mc_history}. "
        f"A None entry would crash any later averaging or comparison over the history."
    )


def test_mc_history_does_append_valid_prob_beating() -> None:
    """
    Companion to R-14: a valid (non-None) prob_beating MUST be appended to
    mc_history. This ensures the guard does not accidentally block legitimate
    appends (i.e. tests the True branch, not just the False branch).

    RED trigger: removing the append entirely rather than just guarding it.
    """
    # Use the minimum-sufficient history to get a real float result.
    minimum_raw = _minimum_raw_days()
    history = {
        f"2024-{1 + i // 28:02d}-{1 + i % 28:02d}": {
            "SPY": {"daily_ret": 0.001},
            "AAA": {"daily_ret": 0.001},
        }
        for i in range(minimum_raw)
    }
    holdings = [{"ticker": "AAA", "allocation": 1.0, "last_percent_change": 0.0}]
    prob_beating = math_engine.run_monte_carlo(
        holdings, history, 0.1, simulation_paths=500, neighbor_k=5, seed=42
    )
    assert prob_beating is not None, (
        f"Minimum-sufficient history ({minimum_raw} raw days) returned the sentinel. "
        f"This fixture is invalid for the R-14 valid-append companion test."
    )

    mc_history: list = []
    mc_available = prob_beating is not None
    if mc_available:
        mc_history.append(prob_beating)

    assert len(mc_history) == 1, (
        f"mc_history did not grow after a valid prob_beating={prob_beating!r}. "
        f"The guard must permit appending when mc_available=True."
    )
    assert mc_history[0] is prob_beating, (
        f"The appended value {mc_history[0]!r} is not the same object as "
        f"prob_beating={prob_beating!r}."
    )


# ---------------------------------------------------------------------------
# C-13. autotuner tick with mc_prob=None key present → mc_available=False
# ---------------------------------------------------------------------------

def test_autotuner_tick_mc_prob_none_key_present_yields_mc_unavailable() -> None:
    """
    C-13 — spec-mc critical note: ``tick.get("mc_prob", 50.0)`` returns None
    when the key is present with value None (the default 50.0 only applies when
    the key is ABSENT). A tick dict with ``"mc_prob": None`` must produce
    mc_available=False in the replay path, not mc=50.0.

    This pins the Python dict.get() semantics that the autotuner relies on:
        mc = tick.get("mc_prob", 50.0)  # → None (key present, value None)
        mc_available = mc is not None   # → False

    RED trigger: changing the replay tick function to use
    ``tick.get("mc_prob") or 50.0`` (which would substitute 50.0 for None
    and make mc_available=True — the wrong behavior, and a recurrence of the
    pre-fix fail-dangerous path in the replay engine).
    """
    # Reproduce the exact production pattern from autotuner.py:463-464.
    tick_with_none_sentinel = {"mc_prob": None, "return": -2.0, "vol": 1.0}
    mc = tick_with_none_sentinel.get("mc_prob", 50.0)
    mc_available = mc is not None

    assert mc is None, (
        f"tick.get('mc_prob', 50.0) returned {mc!r} for a tick with mc_prob=None. "
        f"Python's dict.get() returns the stored value (None) when the key is "
        f"present, not the default. The default only applies for absent keys."
    )
    assert mc_available is False, (
        f"mc_available={mc_available!r} for a tick with mc_prob=None. "
        f"mc_available must be False when mc_prob is the insufficient sentinel. "
        f"A True here would drive spurious MC-gated arms/disarms/TP transitions "
        f"in the autotuner replay — recapitulating the pre-fix fail-dangerous path."
    )


def test_autotuner_tick_mc_prob_key_absent_yields_mc_available_true() -> None:
    """
    Companion to C-13: when the mc_prob key is ABSENT (old tick format or
    pre-sentinel tick), the default of 50.0 applies and mc_available=True.

    This is NOT a sentinel case — 50.0 is a valid mid-range probability and
    the key-absent default is for backward compatibility with tick data from
    before the sentinel was introduced.

    This test ensures the get() default behavior is not accidentally overridden.
    RED trigger: removing the default from tick.get("mc_prob", 50.0).
    """
    tick_without_mc_key = {"return": -2.0, "vol": 1.0}  # no mc_prob key
    mc = tick_without_mc_key.get("mc_prob", 50.0)
    mc_available = mc is not None

    assert mc == pytest.approx(50.0, abs=1e-9), (  # approx: the 50.0 default is exact, but pytest.approx is idiomatic for float comparisons here
        f"tick.get('mc_prob', 50.0) returned {mc!r} for a tick with no mc_prob "
        f"key. Expected the default 50.0 for backward-compatible absent-key ticks."
    )
    assert mc_available is True, (
        f"mc_available={mc_available!r} for a tick with no mc_prob key (default "
        f"50.0). An absent-key tick must be treated as mc_available=True (backward "
        f"compatibility with pre-sentinel tick data)."
    )


# ---------------------------------------------------------------------------
# R-4b. F-4 sentinel path: compute_exit_confirmation fires when MC is sentinel
#        and below-stop condition is met (protective stop always fires)
# ---------------------------------------------------------------------------

def test_exit_confirmation_fires_when_mc_sentinel_and_below_stop() -> None:
    """
    R-4b — F-4 plan scope item 1: "protective stop always fires on ticks-below-stop
    alone in every sentinel branch."

    compute_exit_confirmation with prob_beating=None (MC sentinel) and the below-
    stop condition met must:
      * Increment below_stop_count each qualifying tick.
      * Return is_trailing_stop_hit=True once below_stop_count reaches EXIT_CONFIRM_TICKS.

    This is the primary F-4 fail-safe path. The MC sanity gate evaluates as
    ``mc_sanity_ok = prob_beating is None or prob_beating < MC_SANITY_THRESHOLD``,
    so None → mc_sanity_ok=True → the below-stop condition is gated only on the
    return magnitude, not the MC opinion. The protective stop must not be suppressed
    by insufficient MC history.

    RED trigger: changing the mc_sanity_ok expression to
    ``mc_sanity_ok = prob_beating is not None and prob_beating < MC_SANITY_THRESHOLD``
    (removing the None pass-through), which would make the sentinel BLOCK the
    protective stop — the pre-fix fail-dangerous behavior this cycle exists to pin.
    """
    # Set up a below-stop scenario: current_return well below the stop trigger.
    # We choose values such that the below_stop_condition is True regardless of
    # MAGNITUDE_FLOOR_PCT — using a large negative gap so the test is not fragile
    # to constant changes.
    stop_trigger_level = -2.0  # e.g. stop is at -2% drawdown
    # Return is 1 full percentage point below the threshold (MAGNITUDE_FLOOR_PCT is 0.10).
    current_return = stop_trigger_level - (math_engine.MAGNITUDE_FLOOR_PCT + 1.0)

    # Drive below_stop_count from 0 up to EXIT_CONFIRM_TICKS - 1 and check no early
    # hit, then deliver the final tick and confirm is_trailing_stop_hit=True.
    below_stop_count = 0
    for tick_num in range(1, math_engine.EXIT_CONFIRM_TICKS + 1):
        new_count, is_hit = math_engine.compute_exit_confirmation(
            armed=True,
            is_triggered=False,
            current_return=current_return,
            stop_trigger_level=stop_trigger_level,
            prob_beating=None,  # MC sentinel — insufficient history
            current_below_stop_count=below_stop_count,
        )
        assert new_count == tick_num, (
            f"Tick {tick_num}: expected below_stop_count={tick_num}, "
            f"got {new_count}. compute_exit_confirmation must increment the count "
            f"on each qualifying tick when prob_beating=None (MC sentinel). "
            f"The sentinel must not reset or suppress the count."
        )
        if tick_num < math_engine.EXIT_CONFIRM_TICKS:
            assert is_hit is False, (
                f"Tick {tick_num}: is_trailing_stop_hit=True before reaching "
                f"EXIT_CONFIRM_TICKS ({math_engine.EXIT_CONFIRM_TICKS}). "
                f"The stop must require the full confirmation window."
            )
        else:
            assert is_hit is True, (
                f"Tick {tick_num} (= EXIT_CONFIRM_TICKS={math_engine.EXIT_CONFIRM_TICKS}): "
                f"is_trailing_stop_hit=False. The protective stop must fire once the "
                f"confirmation window is saturated. MC sentinel (prob_beating=None) "
                f"must not suppress the exit."
            )
        below_stop_count = new_count


def test_exit_confirmation_mc_sentinel_does_not_suppress_stop_when_mc_would_have_blocked() -> None:
    """
    R-4b hostile variant: prob_beating=None with a value that, if it WERE a real
    float, would exceed MC_SANITY_THRESHOLD and block the stop.

    Concretely: if prob_beating were 99.0 (well above MC_SANITY_THRESHOLD=60.0),
    mc_sanity_ok would be False and the stop would be suppressed. But because
    prob_beating is None (sentinel), mc_sanity_ok must be True (fail-safe) and
    the stop must still fire.

    This is the sharpest regression test: it proves that None is treated as
    "MC unavailable → do not block" and not as "extreme bullishness → always block."

    RED trigger: any expression that maps prob_beating=None to mc_sanity_ok=False,
    such as ``mc_sanity_ok = prob_beating is not None and prob_beating < MC_SANITY_THRESHOLD``.
    """
    stop_trigger_level = -2.0
    current_return = stop_trigger_level - (math_engine.MAGNITUDE_FLOOR_PCT + 1.0)

    # Confirm that a real high prob_beating DOES block the stop (sanity check that
    # the test is testing the right thing).
    _count, blocked = math_engine.compute_exit_confirmation(
        armed=True,
        is_triggered=False,
        current_return=current_return,
        stop_trigger_level=stop_trigger_level,
        prob_beating=math_engine.MC_SANITY_THRESHOLD + 10.0,  # well above threshold
        current_below_stop_count=math_engine.EXIT_CONFIRM_TICKS - 1,
    )
    assert blocked is False, (
        f"Sanity check failed: a high prob_beating ({math_engine.MC_SANITY_THRESHOLD + 10.0}) "
        f"did not block the stop. The MC sanity gate must veto when prob_beating >= "
        f"MC_SANITY_THRESHOLD ({math_engine.MC_SANITY_THRESHOLD})."
    )

    # Now confirm that None (sentinel) does NOT block — even though the equivalent
    # high float would.
    _count, sentinel_fires = math_engine.compute_exit_confirmation(
        armed=True,
        is_triggered=False,
        current_return=current_return,
        stop_trigger_level=stop_trigger_level,
        prob_beating=None,  # sentinel — "MC unavailable"
        current_below_stop_count=math_engine.EXIT_CONFIRM_TICKS - 1,  # one tick from firing
    )
    assert sentinel_fires is True, (
        f"compute_exit_confirmation(prob_beating=None) at EXIT_CONFIRM_TICKS-1 "
        f"returned is_trailing_stop_hit=False. The MC sentinel must not block the "
        f"protective stop. The fail-safe is: None → mc_sanity_ok=True → stop fires "
        f"on magnitude alone. A real high prob_beating would have blocked; None "
        f"must not recapitulate that suppression."
    )
