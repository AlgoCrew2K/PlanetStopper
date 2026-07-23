"""Guard-Alpha stop-justification preconditions (Kaminski & Lo 2014).

Per-symphony statistical read on whether observed return persistence
(lag-1 autocorrelation) clears the daily Sharpe ratio bar Kaminski & Lo's
trailing-stop analysis identifies as the expected-return condition for a
trailing stop to add value over a static hold. Advisory-only, off the
1-minute execution path (see .claude/tdd-handoff.md).
"""

from __future__ import annotations

import dataclasses
import math

# Kaminski, K.M. & Lo, A.W. (2014), "When Do Stop-Loss Rules Stop Losses?",
# Journal of Financial Markets 18, 234-254.
# DOI 10.1016/j.finmar.2013.07.001 -- cite the DOI only, never a dead mirror URL.
# PM-ASSUMED (feature plan AC-3), ~= synthetic_history._MC_WARMUP_TRADING_DAYS (39)
N_MIN_OBS: int = 40

# 95% two-sided normal critical value, used as the Bartlett (1946) / Box &
# Jenkins (1976) white-noise asymptotic standard-error multiplier for a
# sample lag-1 ACF (SE ~= 1/sqrt(N)) -- same source as
# tests/guard_preconditions/_reference_stats.py's Z_95, so the SUT and the
# test suite's independent reference formula share one documented constant.
_Z_95_TWO_SIDED: float = 1.96


@dataclasses.dataclass(frozen=True)
class PersistenceStats:
    rho: float
    # HALF-WIDTH of the 95% CI (not a tuple/interval) -- callers compute the
    # band as [rho - rho_ci, rho + rho_ci]. See classify_stop_justification's
    # boundary tests (tests/guard_preconditions/test_classify_stop_justification.py)
    # for the exact contract this shape must satisfy.
    rho_ci: float
    sharpe_daily: float
    n_obs: int
    insufficient_reason: str | None = None


def compute_persistence_stats(daily_returns: list) -> PersistenceStats:
    """Compute lag-1 return persistence (rho), its 95% CI half-width, and the
    daily (non-annualized) Sharpe ratio for one symphony's if-held daily
    return series.

    Pure function -- no I/O, no DB access, no network. Accepts a list that
    may contain None entries (missing trading days); those are dropped via
    PAIRWISE deletion for the lag-1 product (a gap does not make the values
    on either side of it newly "adjacent") and via plain omission for the
    mean/variance, matching
    tests/guard_preconditions/_reference_stats.py's reference formulas
    exactly (rel=1e-9 tolerance).

    rho: Box-Jenkins lag-1 sample autocorrelation -- single overall mean,
    sum of consecutive (present-pair) products in the numerator, sum of
    squared deviations over all present points in the denominator.
    sharpe_daily: mean / sample_std(ddof=1) -- house convention, matches
    analytics.py:488.

    n_obs below N_MIN_OBS, or a zero-variance (flat/degenerate) series,
    sets insufficient_reason (non-None) and -- for the zero-variance case
    only, where rho/sharpe_daily are mathematically undefined (0/0) -- rho
    and sharpe_daily are math.nan rather than raising ZeroDivisionError.
    Stats are still computed (not withheld) below the N_MIN_OBS floor so a
    thin-but-real sample remains inspectable, just explicitly flagged.
    """
    valid = [x for x in daily_returns if x is not None]
    n_obs = len(valid)

    insufficient_reason: str | None = None
    if n_obs < N_MIN_OBS:
        insufficient_reason = f"n_obs={n_obs} < N_MIN_OBS={N_MIN_OBS}"

    if n_obs >= 2:
        mean = sum(valid) / n_obs
        denominator = sum((x - mean) ** 2 for x in valid)
    else:
        mean = valid[0] if valid else 0.0
        denominator = 0.0

    if denominator == 0.0:
        if insufficient_reason is None:
            insufficient_reason = "zero-variance series (undefined ACF/Sharpe)"
        rho = math.nan
        sharpe_daily = math.nan
    else:
        numerator = 0.0
        for t in range(1, len(daily_returns)):
            prev, cur = daily_returns[t - 1], daily_returns[t]
            if prev is not None and cur is not None:
                numerator += (cur - mean) * (prev - mean)
        rho = numerator / denominator

        variance = denominator / (n_obs - 1)
        sharpe_daily = mean / (variance**0.5)

    rho_ci = _Z_95_TWO_SIDED / (n_obs**0.5) if n_obs > 0 else math.nan

    return PersistenceStats(
        rho=rho,
        rho_ci=rho_ci,
        sharpe_daily=sharpe_daily,
        n_obs=n_obs,
        insufficient_reason=insufficient_reason,
    )


# Absolute float-comparison tolerance for classify_stop_justification's two
# inclusive (">=") boundary checks below. A boundary that is EXACT by
# construction (e.g. rho=0.30, rho_ci=0.10, sharpe_daily=0.20, so
# rho-rho_ci-sharpe_daily is mathematically 0) can differ from 0 by a few ULPs
# of float64 subtraction error (~1e-17, e.g. 0.30-0.10 == 0.19999999999999998
# in IEEE 754 binary64) -- without tolerance, an inclusive boundary the tests
# pin as SUFFICIENT_MET/SUFFICIENT_LIKELY could wrongly fall through to the
# next class. 1e-9 is ~8 orders of magnitude above that representation noise
# floor and 2 orders of magnitude below the smallest intentional boundary
# distinction this feature's adversarial tests use (1e-7, e.g.
# TestSufficientMetBoundary::test_just_below_boundary_is_sufficient_likely).
_BOUNDARY_EPS: float = 1e-9


def classify_stop_justification(stats: PersistenceStats) -> str:
    """Classify one symphony's stop-justification verdict from its
    PersistenceStats, as an ORDERED PRIORITY CHAIN (see this feature's
    tests/guard_preconditions/test_classify_stop_justification.py module
    docstring for the full rationale, including the AC-3 ambiguous-region
    resolution):

        1. insufficient_reason is set          -> INSUFFICIENT_DATA
        2. elif sharpe_daily <= 0               -> NEGATIVE_EDGE
        3. elif rho - rho_ci >= sharpe_daily    -> SUFFICIENT_MET
        4. elif rho >= sharpe_daily             -> SUFFICIENT_LIKELY
        5. else (includes the ambiguous region) -> NOT_MET

    Order matters: SR<=0 (step 2) MUST gate before any rho-vs-SR comparison
    -- a negative SR is trivially "cleared" by any real rho, so checking
    rho first would wrongly report a stop as justified when the underlying
    edge is negative (AC-3's explicit adversarial trap).

    Takes exactly one paired PersistenceStats argument (not separate
    rho/sharpe floats) so a caller structurally cannot pass a
    wrong-frequency (e.g. annualized) Sharpe alongside a daily rho/CI --
    the unit-mismatch hole AC-2 exists to close.

    Both inclusive comparisons use _BOUNDARY_EPS (see its comment) to absorb
    float64 subtraction noise at exact-by-construction boundaries without
    blurring the adversarial tests' much larger intentional distinctions.
    """
    if stats.insufficient_reason is not None:
        return "INSUFFICIENT_DATA"
    if stats.sharpe_daily <= 0:
        return "NEGATIVE_EDGE"
    if stats.rho - stats.rho_ci - stats.sharpe_daily >= -_BOUNDARY_EPS:
        return "SUFFICIENT_MET"
    if stats.rho - stats.sharpe_daily >= -_BOUNDARY_EPS:
        return "SUFFICIENT_LIKELY"
    return "NOT_MET"


# AC-4: verdict copy. Every entry states the sufficient-not-necessary framing
# somewhere across the set (checked in combination by the test suite); none
# ever renders "stop unjustified" (NOT_MET is an expected-return statement
# only -- variance/drawdown reduction survives under IID and the CRRA
# objective legitimately values it independent of this precondition).
_VERDICT_COPY: dict[str, str] = {
    "SUFFICIENT_MET": (
        "Lag-1 return persistence, even at the low end of its 95% confidence "
        "interval, exceeds the daily Sharpe ratio -- a sufficient (not "
        "necessary) statistical condition for the trailing stop's "
        "expected-return edge, per Kaminski & Lo (2014)."
    ),
    "SUFFICIENT_LIKELY": (
        "Lag-1 return persistence meets or exceeds the daily Sharpe ratio "
        "at the point estimate, though the confidence interval overlaps it "
        "-- a sufficient (not necessary) condition for stop justification "
        "is likely but not confirmed at the 95% level."
    ),
    "NOT_MET": (
        "Observed return persistence does not clear the daily Sharpe ratio "
        "bar -- the expected-return sufficient condition for stop "
        "justification is not met. This says nothing about variance or "
        "drawdown reduction, which can survive under an IID return process."
    ),
    "NEGATIVE_EDGE": (
        "The daily Sharpe ratio is zero or negative, so no level of return "
        "persistence can establish an expected-return edge for the stop -- "
        "reassess the position's entry thesis independent of trailing-stop "
        "parameters."
    ),
    "INSUFFICIENT_DATA": (
        "Too few observations are available to estimate return persistence "
        "reliably -- collect more trading days before drawing a conclusion "
        "either way."
    ),
}


def verdict_copy(verdict: str) -> str:
    """Human-readable rationale for one of the 5 verdict classes."""
    return _VERDICT_COPY[verdict]
