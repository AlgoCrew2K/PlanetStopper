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
   flips ``prob_beating`` from the structural 100.0 (early days in the pool) to
   the structural 0.0 (early days excluded).
2. RED MARKER: pre-fix, the early-window days ARE in the pool (prob_beating
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

import pytest

import math_engine

FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "math_engine"
    / "mc_early_window"
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
    candidates are full-window loss days, so prob_beating must be the
    crash-side structural value 0.0. If the early-window days are still in the
    pool (the pre-fix bug) prob_beating is the gain-side structural value 100.0.

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
    assert 0.0 <= result <= 100.0, f"prob_beating {result!r} escaped [0, 100]."
    assert result == excluded_prob, (
        f"run_monte_carlo returned {result!r}; expected the "
        f"early-window-excluded structural prob_beating={excluded_prob!r}. "
        f"A value of {in_pool_prob!r} means the first "
        f"{early_window_days} short-sample days are still in the kNN candidate "
        f"pool — their downward-biased volatility lets them be mis-selected as "
        f"low-vol neighbours (audit MEDIUM). AC-4: exclude them. "
        f"Derivation: {fx['derivation']}"
    )


def test_red_marker_prefix_admits_early_window_days_to_pool() -> None:
    """
    RED-VERIFICATION MARKER. Pins the PRE-FIX observable: the early-window days
    are still in the kNN candidate pool, so the proof fixture returns the
    gain-side structural value 100.0.

    This test PASSES against pre-fix code and MUST FAIL once AC-4 lands. The
    implementer deletes it in the GREEN step. Its presence proves the proof
    fixture genuinely exercises the bug.
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
    pre_fix_regime = fx["pre_fix_expected_regime"]
    pre_fix_prob = fx["regime_selection"][pre_fix_regime]["structural_prob"]
    assert result == pre_fix_prob, (
        f"RED marker no longer matches pre-fix behaviour: expected the "
        f"early-window-admitted value {pre_fix_prob!r}, got {result!r}. If AC-4 "
        f"has been implemented, DELETE this RED marker — see "
        f"test_excluding_early_window_days_flips_regime_selection."
    )


# ---------------------------------------------------------------------------
# 2. EDGE — minimally-sufficient history stays finite after the exclusion
# ---------------------------------------------------------------------------

def test_minimally_sufficient_history_stays_finite_after_exclusion() -> None:
    """
    AC-4 edge case. With MC_MIN_HISTORY_DAYS == MC_VOL_WINDOW_DAYS in the
    current constants, a history of exactly MC_MIN_HISTORY_DAYS days is
    sufficient (the MC path runs), but excluding the first
    MC_VOL_WINDOW_DAYS - 1 short-sample days shrinks the kNN candidate pool to
    a single day. run_monte_carlo must still return a finite probability in
    [0, 100] from that degenerate one-day pool — the exclusion must not crash
    or emit NaN/Inf on a minimally-sufficient history.

    Contract assertion only — no producer probability pinned.
    """
    fx = _load_fixture("02_small_sufficient_history_stays_finite.json")
    spec = fx["inputs"]["historical_data_spec"]
    history = _build_alternating_history(
        num_days=math_engine.MC_MIN_HISTORY_DAYS,
        spy_amp=spec["spy_amplitude"],
        holding_amp=spec["holding_amplitude"],
    )
    result = math_engine.run_monte_carlo(
        fx["inputs"]["holdings"],
        history,
        fx["inputs"]["spy_today_return"],
        simulation_paths=fx["inputs"]["simulation_paths"],
        neighbor_k=fx["inputs"]["neighbor_k"],
        seed=fx["inputs"]["numpy_seed"],
    )
    # A minimally-sufficient history must NOT be misclassified as insufficient:
    # it is at the MC_MIN_HISTORY_DAYS boundary, so a real probability is
    # expected. (The insufficient-history sentinel is AC-2's concern.)
    assert result is not None, (
        f"run_monte_carlo returned the insufficient sentinel for a history of "
        f"exactly MC_MIN_HISTORY_DAYS days. That history is sufficient — the "
        f"early-window exclusion must shrink the pool, not reclassify the "
        f"history as insufficient."
    )
    assert math.isfinite(result), (
        f"run_monte_carlo returned non-finite {result!r} for a "
        f"minimally-sufficient history. The early-window exclusion must keep "
        f"the result finite even when the candidate pool shrinks to one day."
    )
    assert 0.0 <= result <= 100.0, (
        f"run_monte_carlo returned {result!r} outside [0, 100] for a "
        f"minimally-sufficient history after the early-window exclusion."
    )
