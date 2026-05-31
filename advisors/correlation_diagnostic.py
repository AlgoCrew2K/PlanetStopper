"""De-correlation diagnostic — M1 AI Advisor producer.

Pure measurement: computes pairwise Pearson return-correlation across the
current set of symphonies from an already-extracted per-symphony return-series
dict.  No external API call, no gate, no DB write (AC-1.4 / AC-X1).
Runs entirely off the 1-minute live execution path (AC-X2).

Entry point:
    compute_pairwise_correlations(return_series) -> list[PairResult]

Input contract:
    return_series: dict[str, list[float] | dict[str, float]]
        Keys are symphony identifiers.
        Values are either:
          - list[float]  — positional daily returns; overlap computed by index
          - dict[str, float]  — date-keyed daily returns; overlap computed by
                                shared date keys (required for correct non-overlap
                                handling across non-overlapping windows)

Output contract:
    List of PairResult, one per unique unordered pair (exactly C(n,2) entries).
    sym_a <= sym_b lexically for every pair (canonical key for callers).
    correlation is None when n_obs < 2 or denominator is zero (zero-variance).
    thin_data is True when n_obs < THIN_DATA_THRESHOLD.

All returned scalars are plain Python primitives (float / int / bool) — no
numpy scalars — so the results serialise to JSON without a custom encoder.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Minimum observations for a correlation estimate to be considered
# interpretable.  Matches analytics._V1_BOOTSTRAP_MIN_DAYS — the
# Bailey/de-Prado 2014 interpretability floor used elsewhere in the codebase.
THIN_DATA_THRESHOLD: int = 30

# Minimum overlapping observations required to compute Pearson r at all.
# At n < 2 the sample covariance is undefined (ddof=1 → division by zero).
_MIN_OBS_FOR_PEARSON: int = 2


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class PairResult:
    """Pairwise correlation result for one (sym_a, sym_b) pair.

    sym_a <= sym_b lexically so callers can use the pair as a canonical key
    without worrying about order.
    """

    sym_a: str
    sym_b: str
    n_obs: int                        # overlapping finite observations used; Python int
    correlation: float | None         # Pearson r ∈ [-1, 1]; None when undefined
    thin_data: bool                   # True when n_obs < THIN_DATA_THRESHOLD; Python bool
    window: tuple[str, str] | None    # (first_date, last_date) of overlap for date-keyed
                                      # input; None for positional list input (AC-1.2)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_aligned_pairs(
    series_a: list[float] | dict[str, float],
    series_b: list[float] | dict[str, float],
) -> tuple[list[float], list[float], tuple[str, str] | None]:
    """Return parallel lists of finite values shared by both series, plus the
    date window of the overlap.

    List input:  aligned by position (index i of a with index i of b).
                 window is None — positional indices are not real dates.
    Dict input:  aligned by shared date keys (intersection of key sets).
                 window is (first_date, last_date) of the finite overlap,
                 or None when no finite overlap exists.
    Mixed input (one list, one dict): convert the list to a positional dict
                 keyed "0", "1", ... so the dict path handles intersection
                 uniformly; window is None (indices, not dates).

    Only pairs where BOTH values are finite (non-NaN, non-Inf) are retained.
    Returns (vals_a, vals_b, window).
    """
    # Track whether both inputs are genuine date-keyed dicts (not positional lists).
    both_date_keyed = isinstance(series_a, dict) and isinstance(series_b, dict)

    # Normalise both to dicts so a single code path handles alignment.
    def _to_dict(s: list[float] | dict[str, float]) -> dict[str, float]:
        if isinstance(s, dict):
            return s
        return {str(i): v for i, v in enumerate(s)}

    da = _to_dict(series_a)
    db = _to_dict(series_b)

    shared_keys = sorted(set(da.keys()) & set(db.keys()))

    vals_a: list[float] = []
    vals_b: list[float] = []
    finite_keys: list[str] = []
    for k in shared_keys:
        va, vb = da[k], db[k]
        try:
            fa, fb = float(va), float(vb)
        except (TypeError, ValueError):
            continue
        if math.isfinite(fa) and math.isfinite(fb):
            vals_a.append(fa)
            vals_b.append(fb)
            finite_keys.append(k)

    # Window: the span of the finite overlap, but only when input was date-keyed.
    window: tuple[str, str] | None = None
    if both_date_keyed and finite_keys:
        window = (finite_keys[0], finite_keys[-1])

    return vals_a, vals_b, window


def _pearson_r(xs: list[float], ys: list[float]) -> float | None:
    """Pearson correlation coefficient (sample, ddof=1 implicit via the
    algebraic form below — ddof cancels in the ratio so it is irrelevant).

    Returns None when:
      - fewer than _MIN_OBS_FOR_PEARSON observations
      - either series has zero variance (denominator is zero)

    The algebraic form used is:

        r = Σ(xi - x̄)(yi - ȳ) / sqrt(Σ(xi-x̄)² · Σ(yi-ȳ)²)

    which is equivalent to the standard Pearson r and avoids accumulating
    sums-of-squares in a single pass (two-pass for numerical stability).

    All arithmetic is plain Python float — no numpy — so the return value
    is always a Python float (or None), never a numpy scalar.
    """
    n = len(xs)
    if n < _MIN_OBS_FOR_PEARSON:
        return None

    mean_x = sum(xs) / n
    mean_y = sum(ys) / n

    cov_num = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(xs, ys))
    var_x = sum((xi - mean_x) ** 2 for xi in xs)
    var_y = sum((yi - mean_y) ** 2 for yi in ys)

    denom = math.sqrt(var_x * var_y)

    if denom == 0.0 or not math.isfinite(denom):
        # At least one series has zero variance — r is undefined.
        return None

    r = cov_num / denom

    # Clamp to [-1, 1] to absorb IEEE-754 floating-point rounding that can
    # produce values like 1.0000000000000002 for algebraically-exact series.
    # This is not a lossy clamp for well-behaved data; it is a guard against
    # sub-ULP overflow in the normalisation step.
    r = max(-1.0, min(1.0, r))

    if not math.isfinite(r):
        return None

    # Guarantee return type is a plain Python float (not a numpy scalar or int).
    return float(r)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_pairwise_correlations(
    return_series: dict[str, list[float] | dict[str, float]],
) -> list[PairResult]:
    """Compute pairwise Pearson return-correlation for all symphony pairs.

    Returns an empty list when fewer than 2 symphonies are present (no pairs
    possible).  Never raises on empty/degenerate input (AC-1.3).

    Parameters
    ----------
    return_series:
        dict mapping symphony identifier to its return series.  Each value is
        either a list[float] (positional) or a dict[str, float] (date-keyed).
        NaN / Inf values are filtered before computing r; n_obs reflects the
        filtered overlap count.

    Returns
    -------
    list[PairResult]
        Exactly C(n,2) entries, one per unique unordered pair.  sym_a <= sym_b
        lexically.  correlation is None when n_obs < 2 or denominator is zero.
        thin_data is True when n_obs < THIN_DATA_THRESHOLD.
    """
    names = sorted(return_series.keys())  # sort for deterministic lexical order
    if len(names) < 2:
        return []

    results: list[PairResult] = []

    for name_a, name_b in itertools.combinations(names, 2):
        # Lexical ordering is guaranteed by itertools.combinations on a sorted
        # list, but enforce it explicitly as a contract invariant.
        sym_a, sym_b = (name_a, name_b) if name_a <= name_b else (name_b, name_a)

        vals_a, vals_b, window = _extract_aligned_pairs(
            return_series[sym_a], return_series[sym_b]
        )

        n_obs: int = len(vals_a)  # explicit Python int
        corr = _pearson_r(vals_a, vals_b)  # float | None
        thin = bool(n_obs < THIN_DATA_THRESHOLD)  # explicit Python bool

        results.append(
            PairResult(
                sym_a=sym_a,
                sym_b=sym_b,
                n_obs=n_obs,
                correlation=corr,
                thin_data=thin,
                window=window,
            )
        )

    return results
