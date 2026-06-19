"""
Fixture-derivation script for AC-1 / RM-H1 bootstrap Sortino SE test.

PURPOSE: produce the numeric oracle values the test
tests/autotuner/test_h1_bootstrap_sortino_se.py pastes as literals. This
script implements the Efron 1979 standard nonparametric bootstrap from
scratch — it never imports autotuner or any production module. The test
must NOT call the production function to compute its own oracle (per
memory feedback_no_hardcoded_test_values).

USAGE:
    python scripts/derive_h1_bootstrap_fixture.py

The script prints the SE / Sortino / t-stat for the basic and
all-negative fixtures defined in the test, plus the H0-calibration
KS-distance for the slow property test. Paste the printed numerics into
the test's expected-value constants.

Reference: Efron, B. (1979). "Bootstrap Methods: Another Look at the
Jackknife." Annals of Statistics 7(1), 1-26.

Provenance verification: risk-engine-specialist requested this script
be readable independently before the AC-1 RED commit lands.
"""

from __future__ import annotations

import math
import statistics

import numpy as np

# Match autotuner.SORTINO_TARGET_RETURN — copied as a literal, not imported.
_REFERENCE_SORTINO_TARGET = 0.0
# Risk-engine's prescribed bootstrap count for the haircut p-value
# calibration use case (Efron & Tibshirani 1986 + Davidson & MacKinnon
# 2000).
_BOOTSTRAP_RESAMPLES = 2000
# Sortino & van der Meer 1991 zero-downside sentinel. Matches the value
# autotuner.compute_sortino_ratio returns when downside deviation is 0.0.
_SORTINO_SENTINEL = 1e6


def reference_sortino(returns: list[float]) -> float:
    """Sortino from first principles. NEVER imports autotuner."""
    if not returns:
        return 0.0
    n = len(returns)
    mean_r = sum(returns) / n
    sum_sq_downside = sum(min(r - _REFERENCE_SORTINO_TARGET, 0.0) ** 2 for r in returns)
    mean_sq_downside = sum_sq_downside / n
    downside_deviation = math.sqrt(mean_sq_downside)
    if downside_deviation == 0.0:
        return _SORTINO_SENTINEL
    return mean_r / downside_deviation


def reference_bootstrap_se(
    returns: list[float],
    n_resamples: int,
    seed: int,
    min_valid_resamples: int = 100,
) -> float | None:
    """Standard nonparametric bootstrap SE (Efron 1979) with risk-engine
    spec's None-fallback semantics.

    Uses numpy.random.default_rng to match risk-engine's prescribed RNG
    family, so production and reference produce identical sequences for
    identical seeds.
    """
    T = len(returns)
    if T < 5:
        return None
    # zero-variance series: bootstrap SE is degenerate (every resample
    # equals the original)
    if all(r == returns[0] for r in returns):
        return None
    rng = np.random.default_rng(seed)
    sortinos: list[float] = []
    for _ in range(n_resamples):
        idx = rng.integers(0, T, size=T)
        resample = [returns[int(i)] for i in idx]
        s = reference_sortino(resample)
        # Sentinel-rich-resamples guard: a resample landing in the
        # all-non-negative region returns the +1e6 sentinel, which is
        # not a real Sortino sample. Filter for SE computation.
        if s != _SORTINO_SENTINEL:
            sortinos.append(s)
    if len(sortinos) < min_valid_resamples:
        return None
    # ddof=1 sample stdev — Efron 1979 convention for bootstrap SE.
    return float(np.std(sortinos, ddof=1))


# ---------------------------------------------------------------------------
# Fixture series — copied verbatim into the test as constants.
# ---------------------------------------------------------------------------

# Basic mixed-sign fixture (T=12). Hand-curated for the numeric pin.
RETURN_SERIES_BASIC: tuple[float, ...] = (
    0.012,
    -0.005,
    0.008,
    0.003,
    -0.011,
    0.006,
    0.001,
    0.015,
    -0.002,
    0.009,
    0.004,
    -0.007,
)

# Strongly-positive Sortino fixture (T=20): the sign/direction test
# requires Sortino > 0 with statistical significance.
RETURN_SERIES_POSITIVE_SORTINO: tuple[float, ...] = (
    0.020,
    0.015,
    0.018,
    -0.005,
    0.022,
    0.012,
    0.019,
    -0.003,
    0.025,
    0.014,
    0.017,
    0.011,
    -0.006,
    0.020,
    0.013,
    0.016,
    0.024,
    0.018,
    0.012,
    0.021,
)

# All-negative Sortino fixture (T=15): pin t < 0, p > 0.5.
RETURN_SERIES_NEGATIVE_SORTINO: tuple[float, ...] = (
    -0.010,
    -0.015,
    -0.008,
    -0.012,
    -0.005,
    -0.020,
    -0.011,
    -0.007,
    -0.014,
    -0.009,
    -0.013,
    -0.006,
    -0.018,
    -0.016,
    -0.010,
)

# Near-zero Sortino fixture (T=20): symmetric around 0, pin t ≈ 0,
# p ≈ 0.5.
RETURN_SERIES_NULL_SORTINO: tuple[float, ...] = (
    0.010,
    -0.010,
    0.008,
    -0.008,
    0.012,
    -0.012,
    0.005,
    -0.005,
    0.015,
    -0.015,
    0.007,
    -0.007,
    0.011,
    -0.011,
    0.009,
    -0.009,
    0.013,
    -0.013,
    0.006,
    -0.006,
)

# Sentinel-rich fixture (T=30): 28 zeros + 2 small positives so many
# resamples land entirely at/above target and return the +1e6 sentinel.
RETURN_SERIES_SENTINEL_RICH: tuple[float, ...] = (
    *([0.0] * 28),
    0.001,
    0.002,
)


# ---------------------------------------------------------------------------
# Compute and print.
# ---------------------------------------------------------------------------


def _phi_one_sided_p(t: float) -> float:
    """One-sided p-value 1 - Φ(t). Matches autotuner.compute_haircut_pvalue's
    pre-clamp value."""
    phi = 0.5 * (1.0 + math.erf(t / math.sqrt(2.0)))
    return 1.0 - phi


def main() -> None:
    seed = 20260522

    print("=" * 72)
    print("AC-1 / RM-H1 — fixture derivation")
    print(f"_BOOTSTRAP_RESAMPLES = {_BOOTSTRAP_RESAMPLES}; seed = {seed}")
    print("RNG = numpy.random.default_rng (matches risk-engine design).")
    print("=" * 72)

    for label, series in (
        ("BASIC", RETURN_SERIES_BASIC),
        ("POSITIVE_SORTINO", RETURN_SERIES_POSITIVE_SORTINO),
        ("NEGATIVE_SORTINO", RETURN_SERIES_NEGATIVE_SORTINO),
        ("NULL_SORTINO", RETURN_SERIES_NULL_SORTINO),
        ("SENTINEL_RICH", RETURN_SERIES_SENTINEL_RICH),
    ):
        sortino = reference_sortino(list(series))
        se = reference_bootstrap_se(list(series), _BOOTSTRAP_RESAMPLES, seed)
        if se is None or se == 0.0:
            tstat = 0.0
        else:
            tstat = sortino / se
        p_raw = _phi_one_sided_p(tstat) if math.isfinite(tstat) else float("nan")
        # Wald comparison for the mutation-guard separation pin.
        wald = sortino * math.sqrt(len(series)) if sortino != _SORTINO_SENTINEL else float("nan")

        print(f"\n[{label}]  T = {len(series)}")
        print(f"  Sortino           = {sortino!r}")
        print(f"  SE_bootstrap      = {se!r}")
        print(f"  t = Sortino/SE    = {tstat!r}")
        print(f"  one-sided p (raw) = {p_raw!r}")
        print(f"  Wald (= S*sqrt T) = {wald!r}")

    # H0 calibration property test — produce 200 H0 series (zero-mean,
    # finite-variance), compute the bootstrap p-value distribution, and
    # report the KS distance to uniform on [0,1].
    print()
    print("=" * 72)
    print("H0-calibration property (slow test target)")
    print("200 zero-mean Gaussian series, T=60 each; B=2000 per series.")
    print("=" * 72)
    h0_rng = np.random.default_rng(99)
    p_values: list[float] = []
    for k in range(200):
        s = list(h0_rng.normal(loc=0.0, scale=0.01, size=60))
        sortino = reference_sortino(s)
        se = reference_bootstrap_se(s, _BOOTSTRAP_RESAMPLES, seed + k)
        if se is None or se == 0.0:
            continue
        tstat = sortino / se
        p = _phi_one_sided_p(tstat)
        # Clamp into [eps, 1-eps] for stability (matches the production
        # compute_haircut_pvalue behaviour at any reasonable epsilon).
        eps = 1e-12
        p = min(max(p, eps), 1.0 - eps)
        p_values.append(p)

    # Empirical KS distance to Uniform[0,1].
    sorted_p = sorted(p_values)
    n = len(sorted_p)
    ks = max(max(abs((i + 1) / n - p), abs(i / n - p)) for i, p in enumerate(sorted_p))
    print(f"  n (non-degenerate)  = {n}")
    print(f"  empirical mean p    = {statistics.mean(sorted_p):.4f}")
    print(f"  empirical median p  = {statistics.median(sorted_p):.4f}")
    print(f"  KS distance to U[0,1] = {ks:.4f}")
    print("  (test target: < 0.10 under H0)")


if __name__ == "__main__":
    main()
