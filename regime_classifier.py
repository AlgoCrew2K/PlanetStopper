"""
regime_classifier.py — offline diagnostic regime labeller.

Takes a daily return series and characterizes the CURRENT regime as one of a
coarse 3-state taxonomy: 'mean-reverting', 'trending', or 'high-vol'. The label
describes the realized character of the supplied window (volatility level +
lag-1 autocorrelation sign); it makes no claim about subsequent returns.

This is an OFFLINE DIAGNOSTIC. It is intentionally NOT imported by
alpha_bot_execution.py or math_engine.py and is NEVER called on the 1-minute
execution path (PHASE3_DECISIONS.md §Sub-phase 3a). It is surfaced only as an
operator/analytics diagnostic.

Method (lowest-degrees-of-freedom transparent buckets, per
PHASE3_DECISIONS.md / 00-ADAPTIVE-RECOMMENDATION.md §1A):
  - Features: realized volatility (sample std of the window) and lag-1
    autocorrelation of the return series.
  - High-vol is a separate condition that OVERRIDES the autocorrelation-based
    label: when realized vol exceeds the high-vol threshold the window is
    'high-vol' regardless of AC sign (stress dominates).
  - Otherwise the autocorrelation sign discriminates: positive lag-1 AC ->
    'trending', non-positive lag-1 AC -> 'mean-reverting'.

SINGLE-WINDOW SNAPSHOT — NO TEMPORAL PERSISTENCE (design waiver, 2026-05-31):
classify_regime is a pure function of the explicitly-supplied window. It has no
dwell-time, confirmation-bar, or hysteresis mechanism. A window-over-window
caller applying rolling classification across a time series will see label
changes whenever the features cross a threshold boundary; that is expected and
correct behaviour for a snapshot labeller. Any temporal persistence or smoothing
is the caller's responsibility. This waiver is intentional: the function is used
as a single-point offline diagnostic (not a streaming signal), so per-call
stationarity is the appropriate contract.

FIXED THRESHOLD — NOT FIT FROM HISTORY (design note, 2026-05-31):
classify_regime uses the module constant HIGH_VOL_DAILY_THRESHOLD = 0.04 as its
vol boundary. This is a theory-anchored, zero-degrees-of-freedom constant (3x
the normal ~1-1.5%/day equity vol). It does NOT use a threshold fitted from
external_data. fit_regime_classifier() is a SEPARATE, standalone operator
diagnostic that derives a data-driven quantile threshold from the real (non-
synthetic) broad daily history — its output is informational and is not wired
into classify_regime by default. This is a deliberate design decision: keeping
classify_regime parameter-free means it adds ~0 estimation DoF
(00-ADAPTIVE-RECOMMENDATION.md §1A, 'theory-fixed label (~0 added DoF)').

Graceful degradation: any input too short, non-numeric, or otherwise degenerate
returns None ('unknown' regime) rather than raising — production diagnostic
callers need no try/except wrapper.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

# ---------------------------------------------------------------------------
# Named constants (project rule: no magic numbers — every constant + source)
# ---------------------------------------------------------------------------

# Minimum number of observations required before a label is produced. A stable
# rolling autocorrelation / volatility estimate needs at least a ~20-day window;
# shorter series degrade to the 'unknown' sentinel.
# Source: 00-ADAPTIVE-RECOMMENDATION.md §1A (20-day minimum window).
MIN_LABEL_SERIES_LENGTH = 20

# Minimum number of finite observations needed for the variance / lag-1
# autocorrelation arithmetic to be defined at all (a sample std with ddof=1
# needs >= 2 points). Below this we cannot compute either feature.
MIN_FINITE_OBSERVATIONS = 2

# Lag-1 autocorrelation boundary separating trending from mean-reverting in the
# vol-normal regime. Positive AC => returns persist in direction (trending);
# non-positive AC => returns reverse (mean-reverting). The boundary is zero
# because the AC sign is the discriminator (Kaminski-Lo 2014: trend vs reversal
# are opposite signs of serial correlation).
AUTOCORR_TREND_THRESHOLD = 0.0

# High-vol (stressed) realized-volatility boundary used by classify_regime,
# expressed in DAILY return-fraction std (e.g. 0.04 = 4% daily std).
# A normal daily equity vol is ~1.0-1.5%/day; a stressed/high-vol day-window
# runs several multiples of that. 0.04 (~3x normal) cleanly separates a normal
# moderate-vol window (~1-2%/day) from a stressed window (7-11%/day, ~9% std)
# while leaving wide margin on both sides for boundary stability.
# Source: 00-ADAPTIVE-RECOMMENDATION.md §1A high-vol/stressed tercile;
# normal-vol anchor from the high_vol_stressed fixture derivation.
# UNIT: sample std of daily returns (NOT abs-return percentile). fit_regime_classifier
# computes abs-return q90 which is NOT directly comparable — a q90(|r|) = 0.04
# corresponds to std ~= 0.024 (factor ~1.645 for a normal distribution). The two
# functions are independent diagnostics; the fitted threshold from fit_regime_classifier
# is NOT wired into classify_regime and cannot be substituted without rescaling.
# See module docstring (FIXED THRESHOLD design note).
HIGH_VOL_DAILY_THRESHOLD = 0.04

# Upper quantile used by fit_regime_classifier to derive the high-vol boundary
# from a real (non-synthetic) historical daily-return distribution. The upper
# decile marks the stressed tail without being so extreme it only fires in
# crises. Source: 00-ADAPTIVE-RECOMMENDATION.md §1A upper-tercile/tail framing.
VOL_QUANTILE = 0.90

# Marker values written into source columns that flag a row as synthetic
# pre-inception history; such rows are excluded before fitting thresholds.
# Source: 00-ADAPTIVE-RECOMMENDATION.md §3 mandatory data-prep rule.
_SYNTHETIC_SOURCE_MARKER = "synthetic"

# Regime label strings (coarse 3-state taxonomy).
_LABEL_MEAN_REVERTING = "mean-reverting"
_LABEL_TRENDING = "trending"
_LABEL_HIGH_VOL = "high-vol"


def classify_regime(returns) -> str | None:
    """Characterize the current regime of a daily return series.

    Returns one of 'mean-reverting', 'trending', 'high-vol', or None when the
    input is too short or degenerate to characterize. Never raises.

    Decision order:
      1. Insufficient / degenerate input -> None.
      2. Realized vol above HIGH_VOL_DAILY_THRESHOLD (fixed constant, 0.04) ->
         'high-vol' (overrides AC). The threshold is NOT derived from fitted
         history; it is a theory-anchored zero-DoF constant. See module docstring.
      3. Otherwise: positive lag-1 AC -> 'trending'; non-positive -> 'mean-reverting'.

    The label is a retrospective characterization of the supplied window only.
    This function is a SINGLE-WINDOW SNAPSHOT with no temporal persistence:
    called repeatedly on overlapping windows, it will reflect each window's
    features independently. See module docstring for the design waiver.
    """
    finite = _coerce_finite_series(returns)
    if finite is None or len(finite) < MIN_LABEL_SERIES_LENGTH:
        return None

    vol = _realized_vol(finite)
    if vol is None:
        return None

    # High-vol is a separate, overriding condition: a stressed window is labelled
    # 'high-vol' regardless of its autocorrelation sign.
    if vol > HIGH_VOL_DAILY_THRESHOLD:
        return _LABEL_HIGH_VOL

    ac = _lag1_autocorrelation(finite)
    if ac is not None and ac > AUTOCORR_TREND_THRESHOLD:
        return _LABEL_TRENDING
    return _LABEL_MEAN_REVERTING


def fit_regime_classifier(df) -> dict:
    """Fit the high-vol threshold from real (non-synthetic) historical rows.

    Accepts a pandas DataFrame (or any iterable of row dicts) with columns
    'daily_return', 'source1', 'source2'. Rows where source1 == 'synthetic' OR
    source2 == 'synthetic' are EXCLUDED before fitting — synthetic pre-inception
    history must not contaminate the fitted distribution
    (00-ADAPTIVE-RECOMMENDATION.md §3 mandatory data-prep rule).

    Returns a dict of fitted thresholds:
      {'vol_high_threshold': <upper-quantile of |real daily returns|>,
       'autocorr_trend_threshold': AUTOCORR_TREND_THRESHOLD}

    The vol threshold is the VOL_QUANTILE quantile of the absolute real daily
    returns — a transparent, low-degrees-of-freedom bucket boundary.
    """
    real_returns: list[float] = []
    for row in _iter_rows(df):
        s1 = row.get("source1")
        s2 = row.get("source2")
        if s1 == _SYNTHETIC_SOURCE_MARKER or s2 == _SYNTHETIC_SOURCE_MARKER:
            continue
        value = row.get("daily_return")
        try:
            v = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(v):
            real_returns.append(v)

    vol_high = _quantile([abs(r) for r in real_returns], VOL_QUANTILE)

    return {
        "vol_high_threshold": vol_high,
        "autocorr_trend_threshold": AUTOCORR_TREND_THRESHOLD,
    }


# ---------------------------------------------------------------------------
# Internal feature helpers
# ---------------------------------------------------------------------------


def _coerce_finite_series(returns) -> list[float] | None:
    """Coerce an input to a list of finite floats, or None if not iterable.

    Non-finite entries (NaN/Inf) and non-numeric entries are dropped. A None or
    non-iterable input returns None. A list with fewer than MIN_FINITE_OBSERVATIONS
    usable values returns None (cannot compute features).
    """
    if returns is None or isinstance(returns, (str, bytes)):
        return None
    if not isinstance(returns, Iterable):
        return None

    finite: list[float] = []
    for v in returns:
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(fv):
            finite.append(fv)

    if len(finite) < MIN_FINITE_OBSERVATIONS:
        return None
    return finite


def _realized_vol(values: list[float]) -> float | None:
    """Sample standard deviation (ddof=1) of the series, or None if undefined.

    Returns 0.0 for a zero-variance (constant) series — a valid, non-high-vol
    result. Returns None only when there are too few points for ddof=1.
    """
    n = len(values)
    if n < MIN_FINITE_OBSERVATIONS:
        return None
    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / (n - 1)
    if variance < 0.0 or not math.isfinite(variance):
        return None
    return math.sqrt(variance)


def _lag1_autocorrelation(values: list[float]) -> float | None:
    """Lag-1 autocorrelation of the series, or None if undefined.

    Standard estimator: sum((x_t - mean)(x_{t-1} - mean)) / sum((x_t - mean)^2).
    Returns None for a zero-variance series (AC undefined when the denominator
    is zero).
    """
    n = len(values)
    if n < MIN_FINITE_OBSERVATIONS:
        return None
    mean = sum(values) / n
    denom = sum((x - mean) ** 2 for x in values)
    if denom == 0.0:
        return None
    numer = sum((values[t] - mean) * (values[t - 1] - mean) for t in range(1, n))
    return numer / denom


def _quantile(values: list[float], q: float) -> float:
    """Linear-interpolation quantile of a value list (q in [0, 1]).

    Returns 0.0 for an empty list (no fitted boundary). Matches numpy's default
    'linear' interpolation so the fitted threshold is reproducible.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    n = len(ordered)
    if n == 1:
        return ordered[0]
    pos = q * (n - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return ordered[int(pos)]
    frac = pos - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def _iter_rows(df) -> Iterable[dict]:
    """Yield row dicts from a pandas DataFrame or an iterable of dicts.

    Avoids a hard pandas dependency at import time: if the object exposes
    to_dict('records') (a DataFrame) it is used; otherwise the object is
    iterated directly as a sequence of dict-like rows.
    """
    to_dict = getattr(df, "to_dict", None)
    if callable(to_dict):
        try:
            return to_dict("records")
        except TypeError:
            pass
    return df
