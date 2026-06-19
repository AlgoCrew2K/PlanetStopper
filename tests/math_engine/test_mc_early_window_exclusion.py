"""
AC-4 (math-audit MEDIUM) — RED tests for early-window exclusion from the kNN
candidate pool in ``math_engine.run_monte_carlo``.

THE DEFECT
----------
``run_monte_carlo`` builds the SPY rolling-volatility feature day by day::

    for i in range(len(spy_returns)):
        start_idx = max(0, i - (MC_VOL_WINDOW_DAYS - 1))
        spy_vols[i] = np.std(spy_returns[start_idx : i + 1]) if i > 0 else 0.0

For the first ``MC_VOL_WINDOW_DAYS - 1`` days the slice has FEWER than
``MC_VOL_WINDOW_DAYS`` observations — a short sample — and ``spy_vols[0]`` is
hard-set to ``0.0``. ``np.std`` (ddof=0) is a downward-biased estimator of the
true sigma, and the bias grows sharply as the sample shrinks (n=2 -> ~20% low,
n=5 -> ~6% low, vs n=20 -> ~1.3% low). ``today_vol`` always uses a full
20-observation window, so the bias is one-sided: early-window days look
artificially calmer than reality and can be mis-selected as low-volatility
regime neighbours.

THE FIX (AC-4)
--------------
Exclude the first ``MC_VOL_WINDOW_DAYS - 1`` days from the kNN candidate pool
(drop them, or mark their ``spy_vols`` NaN and exclude NaN-vol days from the
distance).

WHAT THESE TESTS ASSERT
-----------------------
1. THE PROOF: a history whose early-window days uniquely match today's query
   and carry an opposite-sign holding return. Excluding the early-window days
   flips ``prob_underperforming`` from the structural 100.0 (early days in the pool) to
   the structural 0.0 (early days excluded).
2. RED MARKER: pre-fix, the early-window days ARE in the pool (prob_underperforming
   100.0).
3. EDGE: a minimally-sufficient history (exactly ``MC_MIN_HISTORY_DAYS`` days)
   still returns a finite, bounded probability after the exclusion shrinks the
   pool to a single day.

RED-VERIFICATION
----------------
``test_red_marker_*`` passes pre-fix and must fail once the exclusion lands.
``test_excluding_early_window_days_flips_regime_selection`` is RED against
pre-fix code.

PA-18 / fixture-provenance
--------------------------
No producer-computed probability is hardcoded. The proof's two outcomes (0.0,
100.0) are STRUCTURAL boundary values (one-sided bootstrap) — sentinels of
which candidate pool was used, not Monte Carlo draws. The early-window day
count is read from ``math_engine.MC_VOL_WINDOW_DAYS`` at test time. Histories
are schema-derived from ``run_monte_carlo``'s documented input contract.
"""

from __future__ import annotations

import json
import math
import pathlib
from typing import Any

import math_engine

FIXTURE_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "math_engine" / "mc_early_window"


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


def _build_early_window_bias_history(
    num_days: int,
    early_window_days: int,
    today_return_decimal: float,
    early_dispersion: float,
    full_dispersion: float,
    early_holding_ret: float,
    full_holding_ret: float,
) -> dict[str, dict[str, dict[str, float]]]:
    """
    History whose first ``early_window_days`` days (the short-sample
    early-window region) have SPY returns tightly hugging ``today_return_decimal``
    (dispersion ``early_dispersion``) and a holding return of ``early_holding_ret``;
    the remaining full-window days have SPY returns dispersed farther
    (``full_dispersion``) and a holding return of ``full_holding_ret``.
    """
    history: dict[str, dict[str, dict[str, float]]] = {}
    for i in range(num_days):
        if i < early_window_days:
            disp, holding_ret = early_dispersion, early_holding_ret
        else:
            disp, holding_ret = full_dispersion, full_holding_ret
        spy_ret = today_return_decimal + (disp if i % 2 == 0 else -disp)
        history[_date_key(i)] = {
            "SPY": {"daily_ret": spy_ret},
            "AAA": {"daily_ret": holding_ret},
        }
    return history


def _build_alternating_history(
    num_days: int, spy_amp: float, holding_amp: float
) -> dict[str, dict[str, dict[str, float]]]:
    history: dict[str, dict[str, dict[str, float]]] = {}
    for i in range(num_days):
        sign = 1 if i % 2 == 0 else -1
        history[_date_key(i)] = {
            "SPY": {"daily_ret": sign * spy_amp},
            "AAA": {"daily_ret": sign * holding_amp},
        }
    return history


# ---------------------------------------------------------------------------
# 1. THE PROOF — excluding the early-window days flips the regime selection
# ---------------------------------------------------------------------------


def test_excluding_early_window_days_flips_regime_selection() -> None:
    """
    AC-4 proof. The early-window days (the first MC_VOL_WINDOW_DAYS - 1 days,
    whose rolling vol is computed on a short, downward-biased sample) uniquely
    match today's query and carry a GAINING holding return; every full-window
    day carries a LOSING holding return.

    With the early-window days excluded from the kNN candidate pool, the only
    candidates are full-window loss days, so prob_underperforming must be the
    crash-side structural value 0.0. If the early-window days are still in the
    pool (the pre-fix bug) prob_underperforming is the gain-side structural value 100.0.

    This test is RED against pre-fix code (returns 100.0) and GREEN once the
    first MC_VOL_WINDOW_DAYS - 1 days are dropped from the candidate pool.

    Tolerance: exact equality. 0.0 is a deterministic searchsorted boundary
    (every bootstrapped path above current_symphony_return), not a random draw.
    """
    fx = _load_fixture("01_early_window_excluded_from_knn_pool.json")
    spec = fx["inputs"]["historical_data_spec"]
    early_window_days = math_engine.MC_VOL_WINDOW_DAYS - 1
    history = _build_early_window_bias_history(
        num_days=spec["num_days"],
        early_window_days=early_window_days,
        today_return_decimal=spec["today_return_decimal"],
        early_dispersion=spec["early_window_spy_dispersion"],
        full_dispersion=spec["full_window_spy_dispersion"],
        early_holding_ret=spec["early_window_holding_daily_ret"],
        full_holding_ret=spec["full_window_holding_daily_ret"],
    )
    result = math_engine.run_monte_carlo(
        fx["inputs"]["holdings"],
        history,
        fx["inputs"]["spy_today_return"],
        simulation_paths=fx["inputs"]["simulation_paths"],
        neighbor_k=fx["inputs"]["neighbor_k"],
        seed=fx["inputs"]["numpy_seed"],
    )
    excluded_prob = fx["regime_selection"]["early_window_excluded"]["structural_prob"]
    in_pool_prob = fx["regime_selection"]["early_window_in_pool"]["structural_prob"]

    assert math.isfinite(result), f"run_monte_carlo returned non-finite {result!r}."
    assert 0.0 <= result <= 100.0, f"prob_underperforming {result!r} escaped [0, 100]."
    assert result == excluded_prob, (
        f"run_monte_carlo returned {result!r}; expected the "
        f"early-window-excluded structural prob_underperforming={excluded_prob!r}. "
        f"A value of {in_pool_prob!r} means the first "
        f"{early_window_days} short-sample days are still in the kNN candidate "
        f"pool — their downward-biased volatility lets them be mis-selected as "
        f"low-vol neighbours (audit MEDIUM). AC-4: exclude them. "
        f"Derivation: {fx['derivation']}"
    )


# RED-VERIFICATION NOTE: a marker test (test_red_marker_prefix_admits_early_
# window_days_to_pool) lived here during the RED phase. It pinned the pre-fix
# observable (the early-window days are still in the kNN candidate pool, so the
# proof fixture returns the gain-side structural value 100.0) to prove the
# fixture genuinely exercised the bug. It was RED-verified against pre-fix code
# and removed once AC-4 landed. The behavioural proof is
# test_excluding_early_window_days_flips_regime_selection.


# ---------------------------------------------------------------------------
# 2. ELIGIBLE-POOL SUFFICIENCY (team-lead Ruling 2 — Option ii)
# ---------------------------------------------------------------------------
#
# Ruling 2: the insufficient-history determination is made on the ELIGIBLE
# pool, AFTER the early-window exclusion. The MC_MIN_HISTORY_DAYS guard counts
# ELIGIBLE days, not raw days — equivalently, raw history must be
#   >= MC_MIN_HISTORY_DAYS + (MC_VOL_WINDOW_DAYS - 1)
# for the MC path to run. A raw history that is raw-sufficient but
# eligible-insufficient (eligible_days < MC_MIN_HISTORY_DAYS) collapses into
# the SAME insufficient path as a too-short history and returns the SAME
# out-of-band sentinel (None) — never a degenerate 1-neighbour kNN.
#
# All boundary day-counts below are DERIVED from math_engine.MC_MIN_HISTORY_DAYS
# and MC_VOL_WINDOW_DAYS — never hardcoded (per risk-engine-specialist's
# refinement).


def _required_raw_days() -> int:
    """Minimum raw history days for a non-degenerate eligible pool (Ruling 2)."""
    return math_engine.MC_MIN_HISTORY_DAYS + (math_engine.MC_VOL_WINDOW_DAYS - 1)


def test_eligible_insufficient_history_returns_the_insufficient_sentinel() -> None:
    """
    AC-4 / Ruling 2. A raw history that is RAW-sufficient (>= MC_MIN_HISTORY_DAYS
    days) but ELIGIBLE-insufficient (after excluding the first
    MC_VOL_WINDOW_DAYS - 1 early-window days, fewer than MC_MIN_HISTORY_DAYS
    eligible days remain) must be treated as insufficient: run_monte_carlo
    returns the out-of-band insufficient sentinel, NOT a degenerate kNN
    probability.

    Day count tested: required_raw - 1 (one day below the eligible-sufficient
    boundary), derived from the named constants.

    RED against pre-Ruling-2 code (the guard counts raw days, so this history
    runs a degenerate-pool MC and returns an in-band value).
    """
    raw_days = _required_raw_days() - 1
    assert raw_days >= math_engine.MC_MIN_HISTORY_DAYS, (
        "Test invariant: the eligible-insufficient case must still be "
        "RAW-sufficient (>= MC_MIN_HISTORY_DAYS) — otherwise it would exercise "
        "the plain too-short guard, not the eligible-pool guard."
    )
    history = _build_alternating_history(num_days=raw_days, spy_amp=0.010, holding_amp=0.005)
    result = math_engine.run_monte_carlo(
        [{"ticker": "AAA", "allocation": 1.0, "last_percent_change": 0.0}],
        history,
        0.2,
        simulation_paths=2000,
        neighbor_k=8,
        seed=42,
    )
    assert result is math_engine.MC_INSUFFICIENT_HISTORY_SENTINEL, (
        f"run_monte_carlo returned {result!r} for a history of {raw_days} raw "
        f"days. After excluding the first MC_VOL_WINDOW_DAYS-1 "
        f"({math_engine.MC_VOL_WINDOW_DAYS - 1}) early-window days only "
        f"{raw_days - (math_engine.MC_VOL_WINDOW_DAYS - 1)} eligible days "
        f"remain — below MC_MIN_HISTORY_DAYS ({math_engine.MC_MIN_HISTORY_DAYS}). "
        f"Ruling 2: the sufficiency guard counts ELIGIBLE days; this history is "
        f"insufficient and must return the out-of-band sentinel, never a "
        f"degenerate kNN probability."
    )


def test_eligible_insufficient_returns_same_sentinel_as_raw_insufficient() -> None:
    """
    AC-4 / Ruling 2 (risk-engine-specialist refinement). An eligible-insufficient
    history must return the IDENTICAL sentinel object as a raw-too-short
    history — one insufficient sentinel, one downstream fail-safe path. A
    separate "eligible-insufficient" sentinel would re-open the consumer-gate
    problem (every is-None branch would need a parallel branch).
    """
    raw_insufficient = _build_alternating_history(
        num_days=math_engine.MC_MIN_HISTORY_DAYS - 1, spy_amp=0.010, holding_amp=0.005
    )
    eligible_insufficient = _build_alternating_history(
        num_days=_required_raw_days() - 1, spy_amp=0.010, holding_amp=0.005
    )
    holdings = [{"ticker": "AAA", "allocation": 1.0, "last_percent_change": 0.0}]

    raw_result = math_engine.run_monte_carlo(
        holdings, raw_insufficient, 0.2, simulation_paths=2000, neighbor_k=8, seed=42
    )
    eligible_result = math_engine.run_monte_carlo(
        holdings, eligible_insufficient, 0.2, simulation_paths=2000, neighbor_k=8, seed=42
    )
    assert raw_result is math_engine.MC_INSUFFICIENT_HISTORY_SENTINEL, (
        f"Raw-too-short history did not return the insufficient sentinel: {raw_result!r}."
    )
    assert eligible_result is raw_result, (
        f"The eligible-insufficient history returned {eligible_result!r} but "
        f"the raw-too-short history returned {raw_result!r}. Ruling 2: both "
        f"must return the SAME insufficient sentinel object — a distinct "
        f"second sentinel would re-open the consumer-gate is-None problem."
    )


def test_eligible_sufficient_history_runs_real_mc_not_sentinel() -> None:
    """
    AC-4 / Ruling 2 boundary companion. A history of exactly
    MC_MIN_HISTORY_DAYS + (MC_VOL_WINDOW_DAYS - 1) raw days yields exactly
    MC_MIN_HISTORY_DAYS eligible days after the early-window exclusion — the
    inclusive eligible-sufficient boundary. run_monte_carlo must run the real
    MC path and return a finite probability in [0, 100], NOT the sentinel.

    Together with test_eligible_insufficient_history_returns_the_insufficient_
    sentinel this pins the exact eligible-pool boundary so the fix does not
    silently move it.
    """
    history = _build_alternating_history(
        num_days=_required_raw_days(), spy_amp=0.010, holding_amp=0.005
    )
    result = math_engine.run_monte_carlo(
        [{"ticker": "AAA", "allocation": 1.0, "last_percent_change": 0.0}],
        history,
        0.2,
        simulation_paths=2000,
        neighbor_k=8,
        seed=42,
    )
    assert result is not None, (
        f"run_monte_carlo returned the insufficient sentinel for a history of "
        f"{_required_raw_days()} raw days — exactly MC_MIN_HISTORY_DAYS eligible "
        f"days after the early-window exclusion. That is the inclusive "
        f"eligible-sufficient boundary; the real MC path must run."
    )
    assert math.isfinite(result), (
        f"run_monte_carlo returned non-finite {result!r} at the eligible-sufficient boundary."
    )
    assert 0.0 <= result <= 100.0, (
        f"run_monte_carlo returned {result!r} outside [0, 100] at the eligible-sufficient boundary."
    )
