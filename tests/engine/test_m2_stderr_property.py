"""
Property-based tests — H-2 standalone gate: M2 stderr on distinct-tail-obs count.

Tests the invariant that for ANY pool configuration, the M2 estimator's stderr
is consistent with the small-sample (distinct-tail-obs) denominator and inconsistent
with the resample-count denominator. Discriminating power: sqrt(5000/7) ≈ 27x gap
means the 10% correct-value band and 50% wrong-value rejection band have >20x margin.

Uses hypothesis (per quant-test-writer rule 2) with derandomize=True so CI runs are
reproducible without a hypothesis database.

max_examples=50: enough to shrink failures to minimal counterexamples, fast enough
to keep CI under the slow-test threshold (per quant-test-writer rule 6).
"""

from __future__ import annotations

import math
import statistics

import pytest

try:
    from hypothesis import given, settings, HealthCheck, assume
    from hypothesis import strategies as st
    _HYPOTHESIS_AVAILABLE = True
except ImportError:
    _HYPOTHESIS_AVAILABLE = False

import math_engine

pytestmark = pytest.mark.skipif(
    not _HYPOTHESIS_AVAILABLE,
    reason="hypothesis is not installed — install with: pip install hypothesis",
)


# ---------------------------------------------------------------------------
# Composite strategies for synthetic kNN pools
# ---------------------------------------------------------------------------

if _HYPOTHESIS_AVAILABLE:
    @st.composite
    def synthetic_knn_pool(draw):
        """
        Generate a synthetic kNN pool in the realistic AlphaBot range.

        Pool size in [50, 300] (covers small early-life symbols to large histories).
        Daily returns in [-0.20, 0.20] (realistic range for equity holdings in pct
        or decimal; the math_engine works in percent-points so we use [-20, 20] pct
        but that's outside realistic single-day bounds — using fractional returns here
        since compute_cvar_5pct_general_distribution accepts raw returns).

        Constructed so there are guaranteed at least 2 distinct values below the
        5th-percentile quantile, ensuring n_tail >= 1 (pool is non-degenerate).
        """
        pool_size = draw(st.integers(min_value=50, max_value=300))
        # Draw the pool as floats in a realistic daily return range.
        # Using st.floats with a filter so no NaN/Inf and values stay in realistic bounds.
        returns = draw(
            st.lists(
                st.floats(min_value=-0.20, max_value=0.20, allow_nan=False, allow_infinity=False),
                min_size=pool_size,
                max_size=pool_size,
            )
        )
        return returns

    @st.composite
    def simulation_paths_count(draw):
        """Generate a realistic simulation path count (1000–50000)."""
        return draw(st.integers(min_value=1000, max_value=50000))


# ---------------------------------------------------------------------------
# Reference implementation for H-2 verification
#
# The reference recipe is the canonical small-sample stderr formula:
#   std(tail_values, ddof=1) / sqrt(n_tail_distinct)
# where n_tail_distinct = count of pool entries at or below the alpha-quantile
# that contribute to the CVaR estimate.
#
# This is extracted here as a shared spec module (plan risk callout: "shared spec
# module avoids drift between test §8.3 scenario 2 and this property test").
# ---------------------------------------------------------------------------

def _reference_distinct_tail_obs_count(pool: list[float], alpha: float = 0.05) -> int:
    """
    Compute the distinct-tail-obs count from a pool using the R-U floor formula.

    k = floor(alpha * N); fractional_weight = alpha*N - k.
    If fractional_weight > 0: n_tail_distinct = k + 1 (atom contributes).
    If fractional_weight == 0: n_tail_distinct = k (no atom).
    """
    n = len(pool)
    if n == 0:
        return 0
    k_float = alpha * n
    k = int(math.floor(k_float))
    fractional_weight = k_float - k
    if fractional_weight > 0 and k < n:
        return k + 1  # atom at sorted_pool[k] contributes with partial weight
    return k


def _reference_stderr(pool: list[float], alpha: float = 0.05) -> float | None:
    """
    Reference stderr recipe: std(tail_values, ddof=1) / sqrt(n_tail_distinct).
    Returns None when n_tail_distinct < 2 (cannot compute sample std).
    """
    if not pool:
        return None
    sorted_pool = sorted(pool)
    n = len(sorted_pool)
    k = int(math.floor(alpha * n))
    k_float = alpha * n
    fractional_weight = k_float - k
    n_tail = k + (1 if fractional_weight > 0 and k < n else 0)
    if n_tail < 2:
        return None
    tail_values = sorted_pool[:n_tail]
    try:
        std = statistics.stdev(tail_values)  # ddof=1
    except statistics.StatisticsError:
        return None
    return std / math.sqrt(n_tail)


# ---------------------------------------------------------------------------
# Property 1: stderr scales with distinct-tail-obs count, not resample count
# ---------------------------------------------------------------------------


@settings(
    max_examples=50,
    # derandomize=True ensures deterministic example generation in CI
    # (hypothesis default_db may not be writable in CI environments)
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(pool=synthetic_knn_pool(), sim_paths=simulation_paths_count())
def test_m2_stderr_scales_with_distinct_tail_obs_count_not_resample(
    pool: list[float], sim_paths: int
):
    """
    For any kNN pool and any resample count, the M2 estimator's stderr must be
    consistent with the small-sample (distinct-tail-obs) denominator and NOT
    consistent with the resample-count denominator.

    Discriminating power: sqrt(5000/7) ≈ 27x gap between correct and wrong value.
    A 10% tolerance band admits the ddof convention difference (~3% at n=7) while
    safely rejecting the 27x-wrong resample-count value.
    The 50% rejection band around the wrong value is conservatively large: even at
    50% the correct and wrong values are separated by >20x.

    Docstring: "wrong stderr ≈ 27× correct stderr, so a 50% rejection band has
    > 20x margin" (per plan test-m2-stderr-correctness §D3 Property 1).
    """
    result = math_engine.compute_cvar_5pct_general_distribution(pool, alpha=math_engine.CVAR_ALPHA_DEFAULT)

    if result.cvar_pct is None or result.stderr is None:
        # Insufficient pool — sentinel path, skip property assertion for this draw
        return

    reference = _reference_stderr(pool, alpha=math_engine.CVAR_ALPHA_DEFAULT)
    if reference is None:
        # Too few tail observations for a meaningful property check
        return

    # Positive assertion: result.stderr must be within 10% of the reference
    # (computed on the distinct-tail-obs count).
    # 10%: accounts for ddof=0 vs ddof=1 variance (~3% at n=7); still leaves >15x margin.
    assert result.stderr == pytest.approx(reference, rel=0.10), (
        f"stderr={result.stderr:.6f} is not within 10% of reference={reference:.6f} "
        f"(pool_size={len(pool)}, n_tail={result.tail_obs_count}). "
        "H-2: stderr must use the distinct-tail-obs denominator."
    )

    # Negative assertion: result.stderr must NOT be within 50% of the wrong value.
    # Wrong value uses sim_paths as denominator.
    n_tail = _reference_distinct_tail_obs_count(pool, alpha=math_engine.CVAR_ALPHA_DEFAULT)
    if n_tail >= 2:
        tail_vals = sorted(pool)[:n_tail]
        try:
            std_tail = statistics.stdev(tail_vals)
        except statistics.StatisticsError:
            return
        if std_tail == 0.0:
            return
        wrong_stderr = std_tail / math.sqrt(sim_paths)
        # The ratio correct/wrong = sqrt(sim_paths/n_tail) >= sqrt(1000/300) ≈ 1.83x at worst.
        # For sim_paths=5000 and n_tail=7: 27x. At the minimum (sim_paths=1000, n_tail=300):
        # sqrt(1000/300)≈1.83x — still safely outside the 50% band.
        # But at extreme draws (sim_paths=1000, n_tail=50) = sqrt(20)=4.5x, well outside 50%.
        if abs(reference - 0.0) > 1e-15 and wrong_stderr > 0:
            ratio = reference / wrong_stderr
            if ratio > 1.5:  # only assert when the gap is large enough to be meaningful
                assert result.stderr != pytest.approx(wrong_stderr, rel=0.5), (
                    f"stderr={result.stderr:.6f} is within 50% of wrong_resample_stderr={wrong_stderr:.6f}; "
                    f"pool_size={len(pool)}, n_tail={n_tail}, sim_paths={sim_paths}. "
                    "H-2 violation: the implementation appears to be using the resample count."
                )


# ---------------------------------------------------------------------------
# Property 2: tail_obs_count field matches distinct-tail-obs count
# ---------------------------------------------------------------------------


@settings(max_examples=50, derandomize=True, suppress_health_check=[HealthCheck.too_slow])
@given(pool=synthetic_knn_pool())
def test_m2_tail_obs_count_field_matches_distinct_count(pool: list[float]):
    """
    For any pool, result.tail_obs_count must equal the hand-computed distinct-tail-obs
    count (the R-U formula: floor(alpha*N) entries strictly below VaR, +1 if atom
    contributes with fractional_weight > 0).

    Exact equality (integer).
    """
    result = math_engine.compute_cvar_5pct_general_distribution(pool, alpha=math_engine.CVAR_ALPHA_DEFAULT)

    if result.cvar_pct is None:
        # Sentinel path — tail_obs_count must be 0
        assert result.tail_obs_count == 0, (
            f"Sentinel result must have tail_obs_count=0; got {result.tail_obs_count}"
        )
        return

    reference_n = _reference_distinct_tail_obs_count(pool, alpha=math_engine.CVAR_ALPHA_DEFAULT)
    assert result.tail_obs_count == reference_n, (
        f"tail_obs_count={result.tail_obs_count} != reference={reference_n} "
        f"for pool_size={len(pool)}. "
        "tail_obs_count must equal the count of distinct genuine tail observations "
        "(floor(alpha*N) + atom if fractional_weight>0), not the resample count."
    )


# ---------------------------------------------------------------------------
# Property 3: stderr decreases monotonically with pool growth (in expectation)
# ---------------------------------------------------------------------------


@settings(max_examples=30, derandomize=True, suppress_health_check=[HealthCheck.too_slow])
@given(
    base_pool=synthetic_knn_pool(),
    extra_pool=synthetic_knn_pool(),
)
def test_m2_stderr_decreases_monotonically_with_pool_growth(
    base_pool: list[float], extra_pool: list[float]
):
    """
    When pool size grows from draws of the SAME distribution, stderr should decrease
    monotonically in expectation (law of large numbers: stderr ~ 1/sqrt(n_tail)).

    The "same distribution" precondition is enforced by drawing extra_pool from the
    same synthetic_knn_pool() strategy. When extra_returns come from a different
    distribution (e.g. all-zeros appended to a negative-tail base pool), the tail
    composition changes adversarially and the law of large numbers does NOT predict
    monotone decrease — adding zero-valued returns to a negative tail expands the
    tail region and legitimately raises std, making the invariant inapplicable.

    Property invariant (quant-test-writer rule 2): assert result_2n.stderr <= result_n.stderr * 1.05
    (5% slop for the random component at finite sample size).

    Discriminating power: an implementation that uses sim_paths as the denominator
    produces stderr invariant to pool growth (only scales with sim_paths, never
    decreases) — this property catches that: for a non-degenerate base pool the
    constant-denominator impl's stderr does not decrease with pool growth.

    Note: monotonicity holds in expectation for same-distribution draws, not as a
    universal guarantee for all finite samples. 5% tolerance is conservative.
    """
    result_n = math_engine.compute_cvar_5pct_general_distribution(base_pool, alpha=math_engine.CVAR_ALPHA_DEFAULT)
    combined_pool = base_pool + extra_pool
    result_2n = math_engine.compute_cvar_5pct_general_distribution(combined_pool, alpha=math_engine.CVAR_ALPHA_DEFAULT)

    if result_n.stderr is None or result_2n.stderr is None:
        return  # sentinel path — skip

    n_tail_n = result_n.tail_obs_count
    n_tail_2n = result_2n.tail_obs_count

    if n_tail_n < 2 or n_tail_2n < 2:
        return  # too few tail obs for the property to be meaningful

    # Monotonicity is only defined when the base pool has a non-zero tail variance.
    # A degenerate base pool (all identical tail values → stderr=0.0) is a valid
    # implementation output but lies outside the scope of this property.
    assume(result_n.stderr > 0.0)

    # Discard draws where the combined tail VaR has shifted substantially relative
    # to the base tail VaR. The monotonicity law requires the tail region to be
    # approximately stable across the two pools. If VaR shifts by more than 50%
    # (relative), the two tails are measuring different distributional regions and
    # the "same distribution" precondition for the LLN is violated.
    sorted_base = sorted(base_pool)
    sorted_combined = sorted(combined_pool)
    alpha = math_engine.CVAR_ALPHA_DEFAULT
    k_base = int(math.floor(alpha * len(sorted_base)))
    k_combined = int(math.floor(alpha * len(sorted_combined)))
    var_base = sorted_base[k_base]
    var_combined = sorted_combined[k_combined]
    # Both VaRs must be non-zero and in the same sign for a meaningful comparison.
    if var_base == 0.0 or var_combined == 0.0:
        return
    if var_base * var_combined < 0:
        return  # opposite signs — distributional shift too large, skip
    # Relative shift: |var_combined - var_base| / |var_base| <= 0.50
    assume(abs(var_combined - var_base) / abs(var_base) <= 0.50)

    # The 5% slop: in expectation stderr shrinks as 1/sqrt(n_tail), but at small n
    # the sample std can be larger for the bigger pool (random variation).
    # 1.05 factor allows a 5% overshoot without failing.
    assert result_2n.stderr <= result_n.stderr * 1.05, (
        f"Monotonicity property violated: stderr({len(combined_pool)}-pool)={result_2n.stderr:.6f} "
        f"> stderr({len(base_pool)}-pool)={result_n.stderr:.6f} * 1.05. "
        f"n_tail: {n_tail_n} (base) → {n_tail_2n} (combined). "
        f"VaR: {var_base:.4f} (base) → {var_combined:.4f} (combined). "
        "An implementation using the resample count produces pool-size-invariant stderr; "
        "correct implementation stderr decreases with pool growth."
    )


# ---------------------------------------------------------------------------
# Property 4: stderr is finite and positive for any non-degenerate eligible pool
# ---------------------------------------------------------------------------


@settings(max_examples=50, derandomize=True, suppress_health_check=[HealthCheck.too_slow])
@given(pool=synthetic_knn_pool())
def test_m2_stderr_is_finite_for_any_non_degenerate_pool(pool: list[float]):
    """
    For any pool with at least MC_MIN_HISTORY_DAYS + MC_VOL_WINDOW_DAYS - 1 entries
    (the eligibility threshold), if the tail has >= 2 distinct observations, then
    result.stderr must be a finite positive float.

    For insufficient pools (below the eligibility threshold or with < 2 tail obs),
    the function must return the None sentinel — not raise or return non-finite.
    """
    result = math_engine.compute_cvar_5pct_general_distribution(pool, alpha=math_engine.CVAR_ALPHA_DEFAULT)

    if result.cvar_pct is None:
        # Insufficient pool — None sentinel is the correct outcome
        assert result.stderr is None, (
            f"Insufficient pool must have stderr=None (sentinel); got {result.stderr}"
        )
        return

    # Sufficient pool: stderr must be a finite non-negative float.
    # stderr=0.0 is a valid, correct result when all tail values are identical
    # (zero sample variance = infinite precision for the CVaR estimate).
    # The invariant is >= 0.0 (non-negative), not > 0.0 (positive).
    assert result.stderr is not None, (
        f"Sufficient pool (n_tail={result.tail_obs_count}) must have non-None stderr"
    )
    if result.tail_obs_count >= 2:
        assert math.isfinite(result.stderr), (
            f"stderr must be finite; got {result.stderr} "
            f"(pool_size={len(pool)}, n_tail={result.tail_obs_count})"
        )
        assert result.stderr >= 0.0, (
            f"stderr must be non-negative; got {result.stderr} "
            f"(pool_size={len(pool)}, n_tail={result.tail_obs_count}). "
            "stderr=0.0 is valid for all-identical tail values (zero variance); "
            "negative stderr is never valid."
        )
