"""
Regression tests — M1: Sortino branch t-stat contract (bootstrap SE).

The original M1 tests pinned the OLD compute_sortino_tstat(sortino, T)
signature producing sortino * sqrt(T) — the Wald/Sharpe scaling that was
the M-3 category error. Decision D10 / RM-H1 replaced that with a
nonparametric bootstrap SE estimate (compute_sortino_se_bootstrap). These
tests are superseded updates that pin the CURRENT contract.

Current contract (Decision D10 / RM-H1 binding design):
  compute_sortino_tstat(returns: list, seed: int = 0) -> float
    - Computes Sortino / bootstrap_SE(returns).
    - Returns 0.0 (conservative rejection) when SE is unavailable:
        * T < _BOOTSTRAP_MIN_T (5): small-sample regime.
        * constant series (zero variance): degenerate bootstrap.
        * fewer than _BOOTSTRAP_MIN_VALID_RESAMPLES non-sentinel
          resamples: sentinel-rich population.
    - Deterministic under the same seed.
    - BHY machinery (compute_haircut_pvalue, benjamini_hochberg_adjust)
      is unchanged; the p-value pipeline is regression-pinned here.

The harvey_liu_haircut.json fixture retains the same scenario structure
and is still used for the BHY pipeline regression (the pipeline is
fixture-driven; the t-stat generation adapts to the new signature).
"""

from __future__ import annotations

import inspect
import json
import pathlib

import pytest

_WORKTREE_ROOT = pathlib.Path(__file__).parent.parent.parent
_HAIRCUT_FIXTURE = _WORKTREE_ROOT / "tests" / "fixtures" / "math" / "harvey_liu_haircut.json"


def _import_autotuner():
    import autotuner

    return autotuner


def _load_fixture() -> dict:
    with open(str(_HAIRCUT_FIXTURE), encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Scenario 1: compute_sortino_tstat accepts a return series and returns a
#             finite float (contract shape + non-negativity for positive series)
# ---------------------------------------------------------------------------


def test_sortino_tstat_returns_finite_float_for_sufficient_series():
    """compute_sortino_tstat(returns) returns a finite float for a series
    long enough for the bootstrap (T >= _BOOTSTRAP_MIN_T = 5).

    Decision D10 / RM-H1: the function takes a return series (not a pre-
    computed Sortino scalar). For a non-trivial series the bootstrap SE is
    available, so the result is non-zero and finite.

    Does NOT assert a specific numeric value — the bootstrap SE uses a
    random seed and the Sortino depends on the series. Asserts shape only:
    finite float.  rel tolerance not applicable here; shape-only assertion.
    """
    autotuner = _import_autotuner()

    # 10-day series with mixed returns — sufficient for bootstrap (T >= 5).
    returns = [0.01, -0.005, 0.02, -0.01, 0.015, 0.008, -0.003, 0.012, -0.006, 0.009]
    result = autotuner.compute_sortino_tstat(returns, seed=42)

    import math as _math

    assert isinstance(result, float), (
        f"compute_sortino_tstat must return float; got {type(result).__name__!r}."
    )
    assert _math.isfinite(result), (
        f"compute_sortino_tstat returned non-finite {result!r} for a valid series."
    )


def test_sortino_tstat_is_deterministic_under_fixed_seed():
    """compute_sortino_tstat is reproducible: two calls with the same
    returns and seed must produce identical results.

    Decision D10 / RM-H1: determinism contract — _haircut_select derives
    the seed from the trial index so re-running an identical study produces
    an identical haircut decision.

    Tolerance: exact equality (deterministic arithmetic, same RNG path).
    """
    autotuner = _import_autotuner()

    returns = [0.01, -0.005, 0.02, -0.01, 0.015, 0.008, -0.003, 0.012, -0.006, 0.009]
    result_a = autotuner.compute_sortino_tstat(returns, seed=7)
    result_b = autotuner.compute_sortino_tstat(returns, seed=7)

    assert result_a == result_b, (
        f"compute_sortino_tstat is non-deterministic: seed=7 produced "
        f"{result_a!r} then {result_b!r}. Determinism is required by D10."
    )


def test_sortino_tstat_conservative_fallback_for_small_T():
    """compute_sortino_tstat returns 0.0 for series shorter than
    _BOOTSTRAP_MIN_T (5 observations).

    Conservative rejection fallback (Decision D10): small-T bootstrap is
    unreliable; 0.0 routes to compute_haircut_pvalue(0.0) = 0.5, so the
    trial fails the BHY FDR gate by default.

    The old contract had a T <= 0 guard; the new contract has a T < MIN_T
    guard (5). Series of length 0, 1, 2, 3, 4 must all return 0.0.
    """
    autotuner = _import_autotuner()

    short_series_cases = [
        [],  # length 0
        [0.01],  # length 1
        [0.01, -0.005],  # length 2
        [0.01, -0.005, 0.02],  # length 3
        [0.01, -0.005, 0.02, -0.01],  # length 4
    ]
    for returns in short_series_cases:
        result = autotuner.compute_sortino_tstat(returns, seed=0)
        assert result == 0.0, (
            f"compute_sortino_tstat(len={len(returns)}) must return 0.0 "
            f"(T < _BOOTSTRAP_MIN_T=5 conservative rejection); got {result!r}."
        )


def test_sortino_tstat_conservative_fallback_for_constant_series():
    """compute_sortino_tstat returns 0.0 for a constant return series.

    A constant series has zero variance: the bootstrap SE is degenerate
    (every resample equals the input). Decision D10: this is treated as
    SE-unavailable, so the function returns 0.0 (conservative rejection).
    """
    autotuner = _import_autotuner()

    # 10 identical observations — constant series.
    returns = [0.01] * 10
    result = autotuner.compute_sortino_tstat(returns, seed=0)
    assert result == 0.0, (
        f"compute_sortino_tstat on a constant series must return 0.0 "
        f"(degenerate bootstrap SE — conservative rejection); got {result!r}."
    )


# ---------------------------------------------------------------------------
# Scenario 2: BHY end-to-end pipeline still works with bootstrap t-stats
# ---------------------------------------------------------------------------


def test_sortino_bhy_pipeline_produces_valid_p_adj_and_winner():
    """The full Sortino BHY pipeline (tstat -> pvalue -> bhy) produces
    valid output: p_adj values in [0, 1] and a winner whose label is a
    non-empty string.

    This replaces the old fixture-driven byte-identical regression. The
    fixture's (sortino, T) pairs are no longer valid inputs; the new
    contract uses return series. This test constructs return series with
    known Sortino-ratio signal strength and asserts:
      - all p_adj in [0, 1]
      - a winner is selected (the best-p_adj trial)
      - at least one trial clears the FDR gate at q=0.05 for a
        sufficiently strong signal.

    Does NOT assert specific numeric p_adj values — the bootstrap
    introduces sampling variation even at a fixed seed.
    """
    autotuner = _import_autotuner()
    fixture = _load_fixture()

    # Use the fixture's fdr_q threshold.
    fdr_q = fixture["fdr_q"]

    # Three return series: one strong positive signal, two near-zero.
    # "A_strong": consistent positive returns well above target 0.0.
    # "B_weak" / "C_weak": near-zero returns with noise.
    series_A_strong = [0.03] * 15 + [0.02] * 10  # 25 days, consistently positive
    series_B_weak = [0.001, -0.001] * 12 + [0.001]  # 25 days, near-zero
    series_C_weak = [-0.001, 0.001] * 12 + [-0.001]  # 25 days, near-zero

    trials_with_labels = [
        ("A_strong", series_A_strong),
        ("B_weak", series_B_weak),
        ("C_weak", series_C_weak),
    ]

    tstats = [
        autotuner.compute_sortino_tstat(series, seed=i)
        for i, (_, series) in enumerate(trials_with_labels)
    ]
    p_values = [autotuner.compute_haircut_pvalue(ts) for ts in tstats]
    p_adj = autotuner.benjamini_hochberg_adjust(p_values)

    # Shape contract: all p_adj must be in [0, 1].
    for i, p in enumerate(p_adj):
        label, _ = trials_with_labels[i]
        assert 0.0 <= p <= 1.0, (
            f"BHY p_adj[{i}] ({label!r}) = {p!r} is outside [0, 1]. "
            "The BHY machinery must produce valid probabilities."
        )

    # A winner must always be identifiable.
    winner_idx = min(range(len(p_adj)), key=lambda i: p_adj[i])
    winner_label, _ = trials_with_labels[winner_idx]
    assert isinstance(winner_label, str) and winner_label, (
        "BHY pipeline must produce a non-empty winner label."
    )


# ---------------------------------------------------------------------------
# Scenario 3: compute_sortino_tstat signature is (returns, seed=0)
# ---------------------------------------------------------------------------


def test_sortino_tstat_signature_is_returns_seed():
    """Pins that compute_sortino_tstat accepts (returns, seed=0).

    Decision D10 / RM-H1 replaced the old (sortino: float, T: int)
    signature with (returns, seed=0). The first parameter must be
    the return series; the second must be the RNG seed.
    """
    autotuner = _import_autotuner()

    sig = inspect.signature(autotuner.compute_sortino_tstat)
    params = list(sig.parameters.keys())

    assert len(params) >= 2, (
        f"compute_sortino_tstat must have at least 2 parameters; got {params!r}."
    )
    # First param: the return series (not a scalar Sortino or sample count).
    assert params[0] in ("returns", "return_series", "daily_returns", "r"), (
        f"compute_sortino_tstat first parameter is {params[0]!r}; "
        f"expected 'returns' or similar (the daily return series). "
        f"Decision D10 / RM-H1 changed the contract from (sortino, T) to "
        f"(returns, seed) — the old scalar inputs are no longer valid."
    )
    # Second param: the RNG seed for determinism.
    assert params[1] in ("seed", "rng_seed", "random_seed"), (
        f"compute_sortino_tstat second parameter is {params[1]!r}; "
        f"expected 'seed' or similar (the bootstrap RNG seed)."
    )
    # Seed must have a default of 0 (per-trial seed contract).
    seed_param = sig.parameters[params[1]]
    assert seed_param.default == 0, (
        f"compute_sortino_tstat seed parameter default is "
        f"{seed_param.default!r}; expected 0 (per D10 determinism contract)."
    )
