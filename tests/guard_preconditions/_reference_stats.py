"""TEST-ONLY reference implementations of the K&L persistence statistics.

These are an INDEPENDENT formula, not a copy of guard_preconditions.py's
implementation -- expected values in the test suite are derived from raw
fixture return series via these functions, never hardcoded and never obtained
by calling the SUT (feedback_no_hardcoded_test_values; avoiding a circular
parser+fixture co-design per feedback_verify_backend_contract_before_fixtures).

Not imported by any production module.
"""

from __future__ import annotations

# 95% two-sided normal critical value (Bartlett 1946 / Box & Jenkins 1976
# white-noise asymptotic standard error for a sample ACF, SE ~= 1/sqrt(N)).
Z_95 = 1.96


def reference_acf1(returns: list[float]) -> float:
    """Box-Jenkins lag-1 sample autocorrelation: single overall mean, sum of
    consecutive products in the numerator, sum of squared deviations over ALL
    N points in the denominator. This is the standard textbook estimator
    (statsmodels.tsa.stattools.acf(fft=False)[1]) and matches the K&L SE
    formula's assumption -- NOT numpy.corrcoef(r[1:], r[:-1]), which uses two
    different subsample means and is a different (non-equivalent for finite N)
    formula.
    """
    n = len(returns)
    mean = sum(returns) / n
    numerator = sum((returns[t] - mean) * (returns[t - 1] - mean) for t in range(1, n))
    denominator = sum((x - mean) ** 2 for x in returns)
    return numerator / denominator


def reference_acf1_pairwise(returns: list[float | None]) -> tuple[float, int]:
    """Same estimator as reference_acf1, but tolerant of None (missing-day)
    entries via PAIRWISE deletion: the numerator only sums over adjacent
    (t, t-1) pairs where BOTH values are present; a gap does NOT make the
    values on either side of it newly "adjacent" (that would be listwise
    deletion, which corrupts the lag-1 semantic). The mean and denominator use
    every present value. Returns (rho, n_obs) where n_obs = count of present
    values.
    """
    valid = [x for x in returns if x is not None]
    n = len(valid)
    mean = sum(valid) / n
    denominator = sum((x - mean) ** 2 for x in valid)
    numerator = 0.0
    for t in range(1, len(returns)):
        prev, cur = returns[t - 1], returns[t]
        if prev is not None and cur is not None:
            numerator += (cur - mean) * (prev - mean)
    return numerator / denominator, n


def reference_sharpe_daily(returns: list[float]) -> float:
    """mean / sample_std(ddof=1) -- matches the house convention already used
    at analytics.py:488 ("ddof=1 (sample)") for the same reason: an unbiased
    variance estimator over a short daily-return window."""
    n = len(returns)
    mean = sum(returns) / n
    variance = sum((x - mean) ** 2 for x in returns) / (n - 1)
    return mean / (variance**0.5)


def reference_ci95(n: int) -> float:
    return Z_95 / (n**0.5)
