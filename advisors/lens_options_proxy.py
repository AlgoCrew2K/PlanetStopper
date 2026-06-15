"""Derivatives / volatility lens proxy — FREE index-level producer.

Produces a normalized directional risk read from FRED VIXCLS (spot VIX) and
VXVCLS (3-month VIX) — both free, no subscription required.  FRED_API_KEY is
read from os.environ (never hardcoded).

Public entry point
------------------
``_fetch_options_proxy() -> dict``

Returns a dict with keys:
    available (bool)      — True when data was obtained successfully.
    vix_level (float)     — Latest spot VIX (VIXCLS), available=True only.
    vix_term_structure (dict) — {spot, term_3m, ratio, spread, regime}
                               available=True only.
    risk_read (str)       — One of: "risk-off", "risk-on", "neutral".
                            available=True only.
    as_of_date (str)      — ISO date of the latest valid observation.
                            available=True only.
    source (str)          — Citation string; always present.
    reason (str)          — type(exc).__name__ or short label;
                            available=False only (D-1).

Design invariants
-----------------
CC-2   Never imported at module level in alpha_bot_execution.py.
CC-3   Honest availability — available=False with reason when FRED is down
       or returns no usable data.  Values are never fabricated.
CC-5   Free APIs only — VIXCLS and VXVCLS on FRED are no-cost.
D-1    reason is type(exc).__name__ only — never str(exc) and never the raw
       exception message.
AC-12  put_call is omitted — no genuinely free FRED put/call series exists
       at the index level.

Retry policy
------------
Exponential backoff: base_s → 2*base_s → 4*base_s … up to _MAX_ATTEMPTS.
MAX_OPTIONS_RETRY_WAIT_SECONDS is the hard ceiling (named constant).

HTTP timeout
------------
_OPTIONS_PROXY_TIMEOUT_S — explicit per-request timeout for all requests.get
calls.  Never relies on the urllib3 default (which is None).

Reference: project spec, Prism Phase 4 derivatives lens brief.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FRED endpoint constants
# ---------------------------------------------------------------------------

_FRED_BASE_URL: str = "https://api.stlouisfed.org/fred/series/observations"

# FRED series IDs (free, no subscription required)
_SERIES_VIXCLS: str = "VIXCLS"  # CBOE Volatility Index: spot (1-month implied)
_SERIES_VXVCLS: str = "VXVCLS"  # CBOE 3-Month Volatility Index

# FRED "not available" marker — observations with this value must be skipped.
_FRED_NA_VALUE: str = "."

# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------

# Exponential backoff base (seconds) and maximum attempts.
_OPTIONS_PROXY_BACKOFF_BASE_S: float = 2.0

# Maximum individual sleep cap (seconds) to prevent runaway waits.
_OPTIONS_PROXY_BACKOFF_CAP_S: float = 16.0

# Maximum number of attempts per FRED series fetch (1 initial + retries).
_OPTIONS_PROXY_MAX_ATTEMPTS: int = 3

# Hard ceiling on cumulative retry wait — sum of all backoff intervals.
# base + 2*base = 2 + 4 = 6 s (for 3 attempts: 1 fetch + 2 sleeps)
MAX_OPTIONS_RETRY_WAIT_SECONDS: float = (
    _OPTIONS_PROXY_BACKOFF_BASE_S + _OPTIONS_PROXY_BACKOFF_BASE_S * 2
)  # 6.0 s

# HTTP status codes that warrant a retry (transient errors).
_RETRYABLE_HTTP_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})

# ---------------------------------------------------------------------------
# HTTP timeout
# ---------------------------------------------------------------------------

# Explicit per-request timeout for every requests.get call (seconds).
# FRED is a well-maintained government data service; 15 s is generous.
_OPTIONS_PROXY_TIMEOUT_S: float = 15.0

# ---------------------------------------------------------------------------
# Regime + risk_read thresholds
# ---------------------------------------------------------------------------

# VIX level thresholds (named; no magic numbers).
# Source: practitioner convention — VIX <15 = low-fear; >20 = elevated stress.
_VIX_LOW_THRESHOLD: float = 15.0
_VIX_ELEVATED_THRESHOLD: float = 20.0

# Term-structure flat band (±percentage of spot): ratio within this of 1.0 → flat.
# Source: market convention — <2% difference between spot/3m is effectively flat.
_FLAT_BAND_RATIO: float = 0.02

# ---------------------------------------------------------------------------
# Source citation
# ---------------------------------------------------------------------------

_SOURCE_CITATION: str = (
    "FRED (Federal Reserve Bank of St. Louis): VIXCLS (CBOE Volatility Index) "
    "and VXVCLS (CBOE 3-Month Volatility Index). "
    "https://fred.stlouisfed.org/series/VIXCLS — free, no subscription required."
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _fetch_fred_series(series_id: str, api_key: str) -> dict[str, Any]:
    """Fetch the latest observations for a FRED series with bounded retry.

    Implements exponential backoff with a hard cap on total wait time.
    Returns the parsed JSON dict on success.  Raises on all failures after
    exhausting retries — callers handle exceptions.

    Parameters
    ----------
    series_id:
        FRED series identifier (e.g. "VIXCLS").
    api_key:
        FRED API key from os.environ — never hardcoded.

    Raises
    ------
    requests.RequestException
        On transport/timeout errors after all retry attempts.
    requests.HTTPError
        On non-2xx non-retryable HTTP responses.
    ValueError
        On unexpected response shape (missing "observations" key).
    """
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "asc",
        "limit": 100,  # only the most recent batch needed
        "observation_start": "2020-01-01",  # sufficient historical window
    }

    last_exc: Exception | None = None
    for attempt in range(_OPTIONS_PROXY_MAX_ATTEMPTS):
        try:
            resp = requests.get(
                _FRED_BASE_URL,
                params=params,
                timeout=_OPTIONS_PROXY_TIMEOUT_S,
            )
            if resp.status_code in _RETRYABLE_HTTP_STATUSES:
                if attempt < _OPTIONS_PROXY_MAX_ATTEMPTS - 1:
                    sleep_s = min(
                        _OPTIONS_PROXY_BACKOFF_BASE_S * (2**attempt),
                        _OPTIONS_PROXY_BACKOFF_CAP_S,
                    )
                    logger.debug(
                        "FRED %s returned %d; retrying in %.1fs (attempt %d/%d)",
                        series_id,
                        resp.status_code,
                        sleep_s,
                        attempt + 1,
                        _OPTIONS_PROXY_MAX_ATTEMPTS,
                    )
                    time.sleep(sleep_s)
                    continue
                # Final attempt also retryable — raise
                resp.raise_for_status()

            resp.raise_for_status()
            data = resp.json()
            if "observations" not in data:
                raise ValueError(f"FRED response missing 'observations' key for {series_id}")
            return data

        except (requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
            if attempt < _OPTIONS_PROXY_MAX_ATTEMPTS - 1:
                sleep_s = min(
                    _OPTIONS_PROXY_BACKOFF_BASE_S * (2**attempt),
                    _OPTIONS_PROXY_BACKOFF_CAP_S,
                )
                logger.debug(
                    "FRED %s transport error %s; retrying in %.1fs (attempt %d/%d)",
                    series_id,
                    type(exc).__name__,
                    sleep_s,
                    attempt + 1,
                    _OPTIONS_PROXY_MAX_ATTEMPTS,
                )
                time.sleep(sleep_s)
                continue
            raise

    # Should not reach here; raise the last exception
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"Exhausted retries for FRED {series_id}")  # pragma: no cover


def _parse_latest_observation(data: dict[str, Any]) -> tuple[float, str] | None:
    """Extract (value, date) of the latest non-'.' observation.

    FRED marks unavailable observations with value='.'; these are skipped.
    Returns None when all observations are missing or the list is empty.
    """
    observations: list[dict] = data.get("observations", [])
    # Observations are already sorted ascending (asc sort_order parameter).
    # Walk from the end to find the latest valid one.
    for obs in reversed(observations):
        value_str = obs.get("value", ".")
        date_str = obs.get("date", "")
        if value_str == _FRED_NA_VALUE or not value_str:
            continue
        try:
            return float(value_str), date_str
        except ValueError:
            continue
    return None


def _classify_regime(spot: float, term_3m: float) -> str:
    """Classify the VIX term structure regime.

    Returns one of: ``contango``, ``backwardation``, ``flat``.

    contango     = spot < term_3m (futures curve upward sloping; calm market).
    backwardation = spot > term_3m (futures curve inverted; stress market).
    flat         = |spot/term_3m - 1| < _FLAT_BAND_RATIO.

    Source: market convention — VIX term structure is a recognised risk-regime
    indicator; backwardation signals acute stress (spot fear > forward pricing).
    """
    if term_3m <= 0:
        # Defensive: can't compute ratio — call it flat to avoid fabrication.
        return "flat"
    ratio = spot / term_3m
    deviation = abs(ratio - 1.0)
    if deviation <= _FLAT_BAND_RATIO:
        return "flat"
    if spot > term_3m:
        return "backwardation"
    return "contango"


def _derive_risk_read(regime: str, vix_level: float) -> str:
    """Produce a normalized directional risk read.

    Risk-off: backwardation + elevated VIX (>= _VIX_ELEVATED_THRESHOLD).
    Risk-on:  contango + low VIX (< _VIX_LOW_THRESHOLD).
    Neutral:  all other combinations (e.g., backwardation + low VIX is unusual
              but we do not fabricate a strong signal from mixed evidence).

    Returns one of: ``risk-off``, ``risk-on``, ``neutral``.
    """
    if regime == "backwardation" and vix_level >= _VIX_ELEVATED_THRESHOLD:
        return "risk-off"
    if regime == "contango" and vix_level < _VIX_LOW_THRESHOLD:
        return "risk-on"
    return "neutral"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _fetch_options_proxy() -> dict[str, Any]:
    """Fetch the derivatives/volatility lens from FRED (free tier).

    Fetches VIXCLS (spot VIX) and VXVCLS (3-month VIX) from FRED.
    Computes term structure metrics and a normalized risk read.

    Returns a dict with keys described in the module docstring.  Never raises.
    All exceptions are caught; unavailability returns available=False + reason.

    D-1 contract: reason is always type(exc).__name__ only — the exception
    message is never exposed.
    """
    # --- Guard: require FRED_API_KEY from environment ---
    api_key = os.environ.get("FRED_API_KEY", "")
    if not api_key:
        logger.warning("FRED_API_KEY not set; derivatives lens unavailable.")
        return {
            "available": False,
            "reason": "KeyError",  # treat missing key like KeyError (D-1: type name)
            "source": _SOURCE_CITATION,
        }

    try:
        # Fetch VIXCLS (spot VIX)
        vix_data = _fetch_fred_series(_SERIES_VIXCLS, api_key)
        spot_result = _parse_latest_observation(vix_data)
        if spot_result is None:
            return {
                "available": False,
                "reason": "ValueError",  # no valid observations = data-shape issue
                "source": _SOURCE_CITATION,
            }
        spot_vix, as_of_date = spot_result

        # Fetch VXVCLS (3-month VIX)
        vxv_data = _fetch_fred_series(_SERIES_VXVCLS, api_key)
        vxv_result = _parse_latest_observation(vxv_data)
        if vxv_result is None:
            return {
                "available": False,
                "reason": "ValueError",
                "source": _SOURCE_CITATION,
            }
        term_3m, _vxv_date = vxv_result

        # --- Compute term structure metrics ---
        ratio = spot_vix / term_3m if term_3m > 0 else 0.0
        spread = spot_vix - term_3m
        regime = _classify_regime(spot_vix, term_3m)
        risk_read = _derive_risk_read(regime, spot_vix)

        logger.info(
            "FRED derivatives lens: VIX=%.2f VXVCLS=%.2f regime=%s risk_read=%s as_of=%s",
            spot_vix,
            term_3m,
            regime,
            risk_read,
            as_of_date,
        )

        return {
            "available": True,
            "vix_level": float(spot_vix),
            "vix_term_structure": {
                "spot": float(spot_vix),
                "term_3m": float(term_3m),
                "ratio": float(ratio),
                "spread": float(spread),
                "regime": regime,
            },
            "risk_read": risk_read,
            "as_of_date": as_of_date,
            "source": _SOURCE_CITATION,
        }

    except Exception as exc:
        # D-1: only type(exc).__name__ in the returned reason; never str(exc).
        exc_type = type(exc).__name__
        logger.warning("Derivatives lens unavailable: %s (series=VIXCLS/VXVCLS)", exc_type)
        return {
            "available": False,
            "reason": exc_type,
            "source": _SOURCE_CITATION,
        }
