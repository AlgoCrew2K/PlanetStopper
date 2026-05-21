"""
AC-1 (math-audit CRITICAL C-1) — RED tests for kNN feature-vector standardization
in ``math_engine.run_monte_carlo``.

THE DEFECT
----------
``run_monte_carlo`` selects a regime-matched neighbourhood with a 2-D kNN over
(SPY daily return, SPY 20-day rolling volatility) and an UNSCALED Euclidean
distance::

    distances = np.sqrt((spy_returns - spy_today_ret_dec)**2
                        + (spy_vols - today_vol)**2)

SPY returns have an empirical std ~0.012; their rolling 20-day vol has an
empirical std ~0.0023. Mixed into one unscaled distance the return term
dominates the vol term ~33:1 (audit measurement). The regime match is
effectively 1-D on SPY return — a calm day and a crash day with the same SPY
return are treated as the same regime, and the Monte Carlo bootstrap is drawn
from the wrong volatility regime. ``prob_beating`` then feeds every live
trailing-stop / arm / take-profit gate.

THE FIX (AC-1)
--------------
Standardize BOTH features with window-fitted parameters (z-score, or min-max)
BEFORE the Euclidean distance, and transform today's query point with the SAME
parameters. Neither feature may dominate the distance by a unit artifact.

WHAT THESE TESTS ASSERT
-----------------------
1. The audit's prescribed proof: a history with a calm regime whose SPY returns
   hug today's return and a crash regime that genuinely matches today's
   volatility. A correct (standardized) kNN must select the CRASH regime — the
   one whose volatility matches today — not the return-only calm match.
2. Property: the volatility feature is load-bearing. Two histories that differ
   ONLY in the second block's volatility must produce a different regime
   selection (hence a different ``prob_beating``). An unscaled feature vector
   ignores the vol difference.
3. Edge case: a zero-variance feature must not make standardization divide by
   zero — the output stays finite and bounded.

RED-VERIFICATION
----------------
``test_*_red_marker_documents_prefix_behavior`` pins the PRE-FIX observable
(unscaled kNN returns the structural value 100.0 for the proof fixture). It
passes today and MUST flip once the standardization fix lands; the implementer
deletes/inverts it as the GREEN step. The behavioural tests
(``test_high_vol_query_selects_crash_regime_neighbor`` etc.) are RED against
pre-fix code and GREEN after the fix.

PA-18 / fixture-provenance
--------------------------
No producer-computed probability is hardcoded. The proof fixture's two possible
outcomes (0.0 and 100.0) are STRUCTURAL boundary values — the current symphony
return sits entirely below / entirely above a one-sided bootstrap distribution,
so searchsorted yields 0 or ``paths`` deterministically. They are sentinels of
*which regime was selected*, not Monte Carlo draws. Histories are schema-derived
from ``run_monte_carlo``'s documented input contract.
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
    / "mc_knn_standardization"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_fixture(filename: str) -> dict[str, Any]:
    with (FIXTURE_DIR / filename).open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _date_key(i: int) -> str:
    """Synthetic ISO date string; lexicographic ordering is all run_monte_carlo needs."""
    base_day = 1 + i
    month = 1 + (base_day - 1) // 28
    day = 1 + (base_day - 1) % 28
    return f"2024-{month:02d}-{day:02d}"


def _build_two_regime_history(
    num_days: int,
    first_block_days: int,
    today_return_decimal: float,
    first_dispersion: float,
    second_dispersion: float,
    first_holding_ret: float,
    second_holding_ret: float,
) -> dict[str, dict[str, dict[str, float]]]:
    """
    Two-regime history. The first ``first_block_days`` days form block 1 (SPY
    returns dispersed by ``first_dispersion`` around ``today_return_decimal``,
    holding ticker AAA at ``first_holding_ret``); the remainder form block 2
    (dispersed by ``second_dispersion``, holding at ``second_holding_ret``).

    Both blocks' SPY returns are centred on the SAME value so the return feature
    alone cannot distinguish them — only the volatility (dispersion) feature can.
    """
    history: dict[str, dict[str, dict[str, float]]] = {}
    for i in range(num_days):
        if i < first_block_days:
            disp, holding_ret = first_dispersion, first_holding_ret
        else:
            disp, holding_ret = second_dispersion, second_holding_ret
        spy_ret = today_return_decimal + (disp if i % 2 == 0 else -disp)
        history[_date_key(i)] = {
            "SPY": {"daily_ret": spy_ret},
            "AAA": {"daily_ret": holding_ret},
        }
    return history


def _build_constant_history(
    num_days: int, spy_ret: float, holding_ret: float
) -> dict[str, dict[str, dict[str, float]]]:
    """Every day has the identical SPY/holding return — a zero-variance feature."""
    return {
        _date_key(i): {
            "SPY": {"daily_ret": spy_ret},
            "AAA": {"daily_ret": holding_ret},
        }
        for i in range(num_days)
    }


# ---------------------------------------------------------------------------
# 1. THE PROOF — high-vol query must select the high-vol (crash) neighbour
# ---------------------------------------------------------------------------

def test_high_vol_query_selects_crash_regime_neighbor() -> None:
    """
    AC-1 proof (audit-prescribed). Today's SPY return is identical to the calm
    regime's return cluster, but today's realized volatility matches the crash
    regime. A correct, standardized kNN must select the CRASH regime — the one
    whose volatility matches today.

    Observable: the holding gains in the calm regime and loses in the crash
    regime, so the bootstrap distribution is one-sided. Selecting the crash
    regime drives current_symphony_return (0.0) above an all-negative bootstrap
    -> prob_beating collapses to the STRUCTURAL value 0.0. Selecting the calm
    regime (the pre-fix bug) drives it below an all-positive bootstrap ->
    prob_beating is the STRUCTURAL value 100.0.

    This test is RED against unscaled (pre-fix) code (which returns 100.0) and
    GREEN once the kNN feature vector is standardized.

    Tolerance: exact equality. 0.0 is a deterministic searchsorted boundary
    result (every bootstrapped path strictly below current_symphony_return),
    NOT a pseudo-random Monte Carlo draw — no pytest.approx is appropriate.
    """
    fx = _load_fixture("01_equal_return_opposite_vol_regime.json")
    spec = fx["inputs"]["historical_data_spec"]
    history = _build_two_regime_history(
        num_days=spec["num_days"],
        first_block_days=spec["calm_block_days"],
        today_return_decimal=spec["today_return_decimal"],
        first_dispersion=spec["calm_spy_dispersion"],
        second_dispersion=spec["crash_spy_dispersion"],
        first_holding_ret=spec["calm_holding_daily_ret"],
        second_holding_ret=spec["crash_holding_daily_ret"],
    )
    result = math_engine.run_monte_carlo(
        fx["inputs"]["holdings"],
        history,
        fx["inputs"]["spy_today_return"],
        simulation_paths=fx["inputs"]["simulation_paths"],
        neighbor_k=fx["inputs"]["neighbor_k"],
        seed=fx["inputs"]["numpy_seed"],
    )
    crash_prob = fx["regime_selection"]["crash"]["structural_prob"]
    calm_prob = fx["regime_selection"]["calm"]["structural_prob"]

    assert math.isfinite(result), f"run_monte_carlo returned non-finite {result!r}."
    assert 0.0 <= result <= 100.0, f"prob_beating {result!r} escaped [0, 100]."
    assert result == crash_prob, (
        f"run_monte_carlo selected the wrong volatility regime. "
        f"Expected the CRASH-regime structural prob_beating={crash_prob!r} "
        f"(today's volatility matches the crash block), got {result!r}. "
        f"A value of {calm_prob!r} means the kNN selected the calm regime on the "
        f"SPY-return feature alone — the unstandardized-feature bug (audit C-1). "
        f"Derivation: {fx['derivation']}"
    )


def test_high_vol_query_does_not_select_calm_regime_neighbor() -> None:
    """
    Companion negative assertion for the proof: the result must NOT be the
    calm-regime structural value. Spelled out separately so a regression to the
    return-only match produces an unambiguous failure message.
    """
    fx = _load_fixture("01_equal_return_opposite_vol_regime.json")
    spec = fx["inputs"]["historical_data_spec"]
    history = _build_two_regime_history(
        num_days=spec["num_days"],
        first_block_days=spec["calm_block_days"],
        today_return_decimal=spec["today_return_decimal"],
        first_dispersion=spec["calm_spy_dispersion"],
        second_dispersion=spec["crash_spy_dispersion"],
        first_holding_ret=spec["calm_holding_daily_ret"],
        second_holding_ret=spec["crash_holding_daily_ret"],
    )
    result = math_engine.run_monte_carlo(
        fx["inputs"]["holdings"],
        history,
        fx["inputs"]["spy_today_return"],
        simulation_paths=fx["inputs"]["simulation_paths"],
        neighbor_k=fx["inputs"]["neighbor_k"],
        seed=fx["inputs"]["numpy_seed"],
    )
    calm_prob = fx["regime_selection"]["calm"]["structural_prob"]
    assert result != calm_prob, (
        f"run_monte_carlo returned the calm-regime structural prob_beating "
        f"({calm_prob!r}): the kNN matched on SPY return only and ignored the "
        f"volatility regime. The standardized feature vector (AC-1) must let "
        f"today's high volatility steer the match to the crash regime."
    )


def test_unscaled_knn_red_marker_documents_prefix_behavior() -> None:
    """
    RED-VERIFICATION MARKER. Pins the PRE-FIX observable: the unscaled kNN
    returns the calm-regime structural value 100.0 for the proof fixture
    (vol feature dominated ~33:1 by the return feature).

    This test PASSES against pre-fix code and MUST FAIL once AC-1 lands. The
    implementer removes or inverts it in the GREEN step. Its presence proves
    the proof fixture genuinely exercises the bug — a fixture that returned the
    correct value even pre-fix would be worthless.
    """
    fx = _load_fixture("01_equal_return_opposite_vol_regime.json")
    spec = fx["inputs"]["historical_data_spec"]
    history = _build_two_regime_history(
        num_days=spec["num_days"],
        first_block_days=spec["calm_block_days"],
        today_return_decimal=spec["today_return_decimal"],
        first_dispersion=spec["calm_spy_dispersion"],
        second_dispersion=spec["crash_spy_dispersion"],
        first_holding_ret=spec["calm_holding_daily_ret"],
        second_holding_ret=spec["crash_holding_daily_ret"],
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
        f"RED marker no longer matches pre-fix behaviour: expected the unscaled "
        f"kNN to return {pre_fix_prob!r}, got {result!r}. If AC-1 has been "
        f"implemented, DELETE this RED marker (its job is done — see "
        f"test_high_vol_query_selects_crash_regime_neighbor)."
    )


# ---------------------------------------------------------------------------
# 2. PROPERTY — the volatility feature is load-bearing (neither feature dominates)
# ---------------------------------------------------------------------------

def test_vol_feature_is_load_bearing_changes_regime_selection() -> None:
    """
    AC-1 property: 'neither feature dominates the distance.' Operationalized as
    'the volatility feature is not silently dominated' — two histories identical
    in every SPY return but differing only in the second block's volatility must
    select different regimes (hence different prob_beating).

    Pre-fix the unscaled distance ignores the vol difference and both histories
    return the same prob_beating. Post-fix the standardized vol feature is
    load-bearing: the high-tail history's high today_vol selects the loss-making
    crash regime while the low-tail history stays in the gain-making calm regime,
    so the two prob_beating values MUST differ.

    PA-18: no expected literal — the assertion is the inequality between the
    two histories' outputs.
    """
    fx = _load_fixture("02_vol_feature_load_bearing.json")
    shared = fx["inputs"]["shared"]

    def _run(second_dispersion: float) -> float:
        history = _build_two_regime_history(
            num_days=shared["num_days"],
            first_block_days=shared["first_block_days"],
            today_return_decimal=shared["today_return_decimal"],
            first_dispersion=shared["calm_spy_dispersion"],
            second_dispersion=second_dispersion,
            first_holding_ret=shared["first_block_holding_daily_ret"],
            second_holding_ret=shared["second_block_holding_daily_ret"],
        )
        return float(
            math_engine.run_monte_carlo(
                shared["holdings"],
                history,
                shared["spy_today_return"],
                simulation_paths=shared["simulation_paths"],
                neighbor_k=shared["neighbor_k"],
                seed=shared["numpy_seed"],
            )
        )

    prob_low_tail = _run(fx["inputs"]["history_low_tail"]["second_block_spy_dispersion"])
    prob_high_tail = _run(fx["inputs"]["history_high_tail"]["second_block_spy_dispersion"])

    for label, prob in [("low-tail", prob_low_tail), ("high-tail", prob_high_tail)]:
        assert math.isfinite(prob), f"{label}: run_monte_carlo returned non-finite {prob!r}."
        assert 0.0 <= prob <= 100.0, f"{label}: prob_beating {prob!r} escaped [0, 100]."

    assert prob_low_tail != prob_high_tail, (
        f"prob_beating is identical ({prob_low_tail!r}) for two histories that "
        f"differ ONLY in the second block's volatility. The kNN volatility "
        f"feature is being dominated by the return feature — the unstandardized "
        f"feature-vector bug (audit C-1). After standardization, today's "
        f"volatility (set by the history tail) must steer the regime match and "
        f"the two histories must produce different prob_beating."
    )


# ---------------------------------------------------------------------------
# 3. EDGE CASE — zero-variance feature must not break standardization
# ---------------------------------------------------------------------------

def test_zero_variance_feature_does_not_produce_nan_or_inf() -> None:
    """
    AC-1 edge case: a feature with zero variance over the window. Every SPY day
    has the identical return, so both the return feature and the rolling-vol
    feature have zero spread. A z-score standardization divides by the feature
    std; with zero variance that is 0/0. The fix must guard this (a finite
    fallback) and never emit NaN/Inf or raise.

    Contract assertion only — no producer probability is pinned.
    """
    fx = _load_fixture("03_zero_variance_feature.json")
    spec = fx["inputs"]["historical_data_spec"]
    history = _build_constant_history(
        num_days=spec["num_days"],
        spy_ret=spec["spy_daily_ret"],
        holding_ret=spec["holding_daily_ret"],
    )
    result = math_engine.run_monte_carlo(
        fx["inputs"]["holdings"],
        history,
        fx["inputs"]["spy_today_return"],
        simulation_paths=fx["inputs"]["simulation_paths"],
        neighbor_k=fx["inputs"]["neighbor_k"],
        seed=fx["inputs"]["numpy_seed"],
    )
    assert math.isfinite(result), (
        f"run_monte_carlo returned non-finite {result!r} for a zero-variance "
        f"feature. The standardization fix must guard the divide-by-zero in the "
        f"z-score (feature std == 0) with a finite fallback."
    )
    assert 0.0 <= result <= 100.0, (
        f"run_monte_carlo returned {result!r} outside [0, 100] for a "
        f"zero-variance feature."
    )
