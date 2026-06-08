"""
AC-2 (math-audit HIGH) — RED tests for the insufficient-MC-history fail-safe.

THE DEFECT
----------
When MC history is below ``MC_MIN_HISTORY_DAYS``, ``run_monte_carlo`` returns
the IN-BAND value ``MC_INSUFFICIENT_HISTORY_PROB = 100.0``. A comment claims
this "skips MC exit gate." It does the opposite. ``compute_exit_confirmation``
requires ``prob_underperforming < MC_BREAKDOWN_THRESHOLD`` (60.0) for the trailing-stop
exit to confirm. ``100.0 >= 60.0`` makes ``below_stop_condition`` permanently
False — the protective trailing-stop exit can NEVER confirm while MC history is
insufficient. Insufficient MC data DISABLES the stop-loss. For a risk engine
that is fail-DANGEROUS.

THE FIX (AC-2, team-lead decision D2 — "bypass the MC gate, fail-safe")
----------------------------------------------------------------------
* ``run_monte_carlo`` signals insufficient history via a DISTINCT out-of-band
  sentinel (``None``), never the in-band ``100.0``.
* ``compute_exit_confirmation`` treats the insufficient sentinel as "MC
  confirmation unavailable" — it does NOT veto the trailing-stop exit. The
  protective stop fires on the ticks-below-stop condition alone.
* The misleading "skips MC exit gate" comment is corrected to match behaviour.

WHAT THESE TESTS ASSERT
-----------------------
1. ``run_monte_carlo`` returns a distinct out-of-band sentinel (not a value in
   [0, 100]) for insufficient history.
2. The insufficient sentinel is distinguishable from the in-band ``100.0`` —
   the two must not be the same object/value.
3. THE FAIL-SAFE PROOF: insufficient MC history + position below the stop for
   ``EXIT_CONFIRM_TICKS`` consecutive ticks -> the trailing-stop exit STILL
   fires. Insufficient data never disables the protective stop.
4. The ``MC_MIN_HISTORY_DAYS`` boundary is inclusive: exactly that many days
   runs the real MC path; one fewer returns the sentinel.
5. The corrected ``run_monte_carlo`` / ``MC_INSUFFICIENT_HISTORY_PROB`` comment
   must no longer assert "skips MC exit gate" (the false claim the audit
   flagged).

NOTE TO TEAM
------------
``tests/math_engine/test_mc_fallbacks.py`` (Test 1) and
``test_mc_gating_magic_numbers.py`` pin the OLD in-band ``100.0`` return for
insufficient history. Those are characterization pins of the now-incorrect
fail-dangerous behaviour; the implementer must update them to the new
out-of-band-sentinel contract as part of the GREEN step (in scope per AC-5).

PA-18 / fixture-provenance
--------------------------
No producer-computed probability is hardcoded. The fail-safe proof asserts a
BOOLEAN exit outcome. ``MAGNITUDE_FLOOR_PCT``, ``EXIT_CONFIRM_TICKS``,
``MC_MIN_HISTORY_DAYS`` are read from ``math_engine`` at test time. Histories
are schema-derived from ``run_monte_carlo``'s documented input contract.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

import math_engine

FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "math_engine"
    / "mc_insufficient_failsafe"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_fixture(filename: str) -> dict[str, Any]:
    with (FIXTURE_DIR / filename).open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _date_key(i: int) -> str:
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


def _is_insufficient_sentinel(value: Any) -> bool:
    """
    True when ``value`` is the distinct out-of-band insufficient-history
    sentinel. The AC-2 contract is "a DISTINCT out-of-band sentinel (None)";
    this helper accepts None and rejects any in-band probability so the tests
    do not over-constrain the exact sentinel representation while still
    enforcing 'out of band'.
    """
    if value is None:
        return True
    # Reject any finite number in the valid probability band — that is not
    # out-of-band. (A NaN would also be out-of-band but is disallowed
    # elsewhere as a non-finite output; None is the contract.)
    return False


# ---------------------------------------------------------------------------
# 1. run_monte_carlo signals insufficient history out-of-band
# ---------------------------------------------------------------------------

def test_insufficient_history_returns_out_of_band_sentinel() -> None:
    """
    AC-2: with history below MC_MIN_HISTORY_DAYS, run_monte_carlo must return a
    DISTINCT out-of-band sentinel (None) — never an in-band probability in
    [0, 100], and specifically never the misleading 100.0.

    RED against pre-fix code (which returns the in-band 100.0).
    """
    fx = _load_fixture("01_insufficient_history_below_stop.json")
    spec = fx["run_monte_carlo_inputs"]["historical_data_spec"]
    assert spec["num_days"] < math_engine.MC_MIN_HISTORY_DAYS, (
        "Fixture invariant broken: the insufficient-history fixture must have "
        "fewer than MC_MIN_HISTORY_DAYS days."
    )
    history = _build_constant_history(
        num_days=spec["num_days"],
        spy_ret=spec["spy_daily_ret"],
        holding_ret=spec["holding_daily_ret"],
    )
    result = math_engine.run_monte_carlo(
        fx["run_monte_carlo_inputs"]["holdings"],
        history,
        fx["run_monte_carlo_inputs"]["spy_today_return"],
        simulation_paths=fx["run_monte_carlo_inputs"]["simulation_paths"],
        neighbor_k=fx["run_monte_carlo_inputs"]["neighbor_k"],
        seed=fx["run_monte_carlo_inputs"]["numpy_seed"],
    )
    assert _is_insufficient_sentinel(result), (
        f"run_monte_carlo returned {result!r} for insufficient MC history. "
        f"AC-2 requires a DISTINCT out-of-band sentinel (None), not an in-band "
        f"probability. The pre-fix in-band 100.0 is exactly the fail-dangerous "
        f"value the audit flagged — it makes the MC sanity gate veto the "
        f"protective stop forever."
    )


def test_insufficient_sentinel_is_distinct_from_in_band_full_confidence() -> None:
    """
    The insufficient sentinel must be DISTINGUISHABLE from an in-band
    prob_underperforming of 100.0. A genuine MC run that legitimately produces 100.0
    ("every bootstrapped path was below the current return") must not be
    confused with "MC could not run at all." The two carry opposite meanings
    for the exit gate and must be different values.

    RED against pre-fix code, where insufficient history IS 100.0.
    """
    fx = _load_fixture("01_insufficient_history_below_stop.json")
    spec = fx["run_monte_carlo_inputs"]["historical_data_spec"]
    history = _build_constant_history(
        num_days=spec["num_days"],
        spy_ret=spec["spy_daily_ret"],
        holding_ret=spec["holding_daily_ret"],
    )
    insufficient_result = math_engine.run_monte_carlo(
        fx["run_monte_carlo_inputs"]["holdings"],
        history,
        fx["run_monte_carlo_inputs"]["spy_today_return"],
        simulation_paths=fx["run_monte_carlo_inputs"]["simulation_paths"],
        neighbor_k=fx["run_monte_carlo_inputs"]["neighbor_k"],
        seed=fx["run_monte_carlo_inputs"]["numpy_seed"],
    )
    assert insufficient_result != 100.0, (
        f"The insufficient-history sentinel ({insufficient_result!r}) equals the "
        f"in-band 100.0. AC-2: insufficient history and 'MC says 100% confident' "
        f"must be DISTINCT — they drive opposite exit-gate behaviour."
    )


# ---------------------------------------------------------------------------
# 2. THE FAIL-SAFE PROOF — insufficient MC must not disable the protective stop
# ---------------------------------------------------------------------------

def test_protective_stop_still_fires_when_mc_history_insufficient() -> None:
    """
    AC-2 FAIL-SAFE PROOF (the safety-critical assertion of this cycle).

    The position is armed, not triggered, and deeply below its protective
    trailing stop. MC history is insufficient — no MC second opinion exists.
    Driven for EXIT_CONFIRM_TICKS consecutive ticks, the trailing-stop exit
    MUST confirm. Insufficient MC data must NEVER disable the protective stop.

    Pre-fix, run_monte_carlo returns 100.0, the MC sanity gate
    (prob_underperforming < MC_BREAKDOWN_THRESHOLD) is False, below_stop_condition is
    permanently False, the count resets every tick, is_trailing_stop_hit never
    becomes True — the stop is silently disabled. This test is RED against that.

    Post-fix, run_monte_carlo returns the insufficient sentinel,
    compute_exit_confirmation treats it as "no MC veto", and the magnitude
    condition alone drives the count to EXIT_CONFIRM_TICKS -> the stop fires.

    Tolerance: none — the assertion is a boolean exit outcome.
    """
    fx = _load_fixture("01_insufficient_history_below_stop.json")
    rmc = fx["run_monte_carlo_inputs"]
    spec = rmc["historical_data_spec"]
    scenario = fx["exit_confirmation_scenario"]

    # Step 1: the engine computes prob_underperforming from insufficient history.
    history = _build_constant_history(
        num_days=spec["num_days"],
        spy_ret=spec["spy_daily_ret"],
        holding_ret=spec["holding_daily_ret"],
    )
    prob_underperforming = math_engine.run_monte_carlo(
        rmc["holdings"],
        history,
        rmc["spy_today_return"],
        simulation_paths=rmc["simulation_paths"],
        neighbor_k=rmc["neighbor_k"],
        seed=rmc["numpy_seed"],
    )

    # Sanity: the magnitude condition is genuinely satisfied for this scenario,
    # so that a non-firing stop can only be the MC gate vetoing — never a
    # too-shallow drawdown. MAGNITUDE_FLOOR_PCT is read from the module.
    magnitude_satisfied = scenario["current_return"] <= (
        scenario["stop_trigger_level"] - math_engine.MAGNITUDE_FLOOR_PCT
    )
    assert magnitude_satisfied, (
        "Fixture scenario invariant broken: current_return must be below "
        "stop_trigger_level - MAGNITUDE_FLOOR_PCT so the magnitude gate passes."
    )

    # Step 2: drive compute_exit_confirmation for EXIT_CONFIRM_TICKS ticks with
    # the insufficient prob_underperforming. The protective stop must confirm.
    count = scenario["starting_below_stop_count"]
    hit = False
    for tick in range(math_engine.EXIT_CONFIRM_TICKS):
        count, hit = math_engine.compute_exit_confirmation(
            armed=scenario["armed"],
            is_triggered=scenario["is_triggered"],
            current_return=scenario["current_return"],
            stop_trigger_level=scenario["stop_trigger_level"],
            prob_underperforming=prob_underperforming,
            current_below_stop_count=count,
        )

    assert hit is True, (
        f"The protective trailing stop did NOT fire after "
        f"{math_engine.EXIT_CONFIRM_TICKS} consecutive ticks below the stop "
        f"while MC history was insufficient (prob_underperforming={prob_underperforming!r}). "
        f"Insufficient MC data must NEVER disable the protective stop — the "
        f"exit must confirm on the ticks-below-stop condition alone. This is "
        f"the audit's fail-dangerous HIGH finding."
    )
    assert count >= math_engine.EXIT_CONFIRM_TICKS, (
        f"below_stop_count reached {count} after {math_engine.EXIT_CONFIRM_TICKS} "
        f"ticks; with the MC gate not vetoing it must reach at least "
        f"EXIT_CONFIRM_TICKS. A reset means the insufficient sentinel was still "
        f"treated as an MC veto."
    )


def test_insufficient_mc_does_not_veto_exit_on_a_single_qualifying_tick() -> None:
    """
    Granular companion to the fail-safe proof: a SINGLE qualifying tick (deeply
    below the stop) with an insufficient prob_underperforming must INCREMENT the
    below-stop count, not reset it to zero. A reset is the fingerprint of the
    MC gate vetoing.

    RED against pre-fix code (100.0 -> MC gate False -> reset to 0).
    """
    fx = _load_fixture("01_insufficient_history_below_stop.json")
    rmc = fx["run_monte_carlo_inputs"]
    spec = rmc["historical_data_spec"]
    scenario = fx["exit_confirmation_scenario"]

    history = _build_constant_history(
        num_days=spec["num_days"],
        spy_ret=spec["spy_daily_ret"],
        holding_ret=spec["holding_daily_ret"],
    )
    prob_underperforming = math_engine.run_monte_carlo(
        rmc["holdings"],
        history,
        rmc["spy_today_return"],
        simulation_paths=rmc["simulation_paths"],
        neighbor_k=rmc["neighbor_k"],
        seed=rmc["numpy_seed"],
    )

    new_count, hit = math_engine.compute_exit_confirmation(
        armed=scenario["armed"],
        is_triggered=scenario["is_triggered"],
        current_return=scenario["current_return"],
        stop_trigger_level=scenario["stop_trigger_level"],
        prob_underperforming=prob_underperforming,
        current_below_stop_count=0,
    )
    assert new_count == 1, (
        f"A single qualifying tick (deeply below stop) with an insufficient "
        f"prob_underperforming ({prob_underperforming!r}) produced below_stop_count={new_count}, "
        f"expected 1. A value of 0 means the insufficient sentinel was treated "
        f"as an MC veto and the count was reset — fail-dangerous."
    )


# ---------------------------------------------------------------------------
# 3. MC sufficiency boundary
# ---------------------------------------------------------------------------
#
# The sufficiency boundary is the ELIGIBLE-day boundary, not the raw-day
# boundary. Team-lead Ruling 2 (Option ii) unified AC-2 and AC-4: the
# MC_MIN_HISTORY_DAYS guard counts days remaining AFTER the early-window
# exclusion (raw history must be >= MC_MIN_HISTORY_DAYS + MC_VOL_WINDOW_DAYS-1).
# An eligible pool below MC_MIN_HISTORY_DAYS IS "insufficient" and returns the
# same out-of-band sentinel as a raw-too-short history.
#
# The exact eligible-pool boundary (eligible-insufficient -> sentinel;
# eligible-sufficient -> real MC; both insufficient cases return the SAME
# sentinel object) is pinned by:
#   tests/math_engine/test_mc_early_window_exclusion.py
#     - test_eligible_insufficient_history_returns_the_insufficient_sentinel
#     - test_eligible_insufficient_returns_same_sentinel_as_raw_insufficient
#     - test_eligible_sufficient_history_runs_real_mc_not_sentinel
#
# A raw-day-boundary test (a history of exactly MC_MIN_HISTORY_DAYS RAW days
# expected to run the real MC) lived here during the RED phase. Ruling 2
# superseded the raw-day boundary — at 20 raw days only one day survives the
# early-window exclusion, so that history is now correctly insufficient. The
# raw-day boundary tests were removed; the eligible-pool boundary above is the
# canonical sufficiency pin.


# ---------------------------------------------------------------------------
# 4. The misleading comment must be corrected
# ---------------------------------------------------------------------------

def test_insufficient_history_comment_no_longer_claims_skips_mc_exit_gate() -> None:
    """
    AC-2: the audit flagged the MC_INSUFFICIENT_HISTORY_PROB / run_monte_carlo
    comment for asserting the OPPOSITE of runtime behaviour — it claims the
    insufficient path "skips MC exit gate" when in fact the in-band 100.0
    PERMANENTLY VETOES the exit. After the fix the comment must no longer make
    that false claim.

    This is a documentation-correctness assertion (the comment is safety
    documentation for the next reader of a stop-loss code path). It scans the
    math_engine source for the misleading phrase near the insufficient-history
    handling.

    The pre-fix comment (math_engine.py ~93) reads:
        "Sentinel probability when MC history insufficient — emits 100% to
         skip MC exit gate"
    The phrase "skip MC exit gate" is the false claim — the in-band 100.0 does
    not skip the gate, it permanently VETOES the exit. The test searches for
    that phrase (case-insensitive) and must be RED until the comment is fixed.
    """
    source = pathlib.Path(math_engine.__file__).read_text(encoding="utf-8")
    lowered = source.lower()
    assert "skip mc exit gate" not in lowered, (
        "math_engine source still contains the comment phrase 'skip MC exit "
        "gate' for the insufficient-history path. The audit flagged this as the "
        "opposite of runtime behaviour: the in-band 100.0 does not skip the MC "
        "gate, it permanently vetoes the exit. AC-2 requires the comment be "
        "corrected to describe the real fail-safe behaviour (the insufficient "
        "sentinel makes the MC gate UNAVAILABLE — the protective stop still "
        "fires on the ticks-below-stop condition alone)."
    )
