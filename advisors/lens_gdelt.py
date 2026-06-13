"""GDELT tone-scoring producer for the Market Prism sentiment lens.

Fetches tone-scored data from the public GDELT 2.0 DOC API (no key required)
using mode=timelinetone, which returns a timeline of average-tone values.
Returns a normalized directional score in [-1, 1].

Why timelinetone, not artlist:
    mode=artlist returns article metadata (url, title, seendate, domain) but
    carries NO per-article AvgTone in the free-tier response.  Using artlist
    caused tone_score to be always None — the bug this module fixes.
    mode=timelinetone returns {date, value} entries where 'value' is GDELT
    AvgTone (float in [-100, 100]) for that time bucket.  This is the correct
    tone-bearing endpoint for the free public API.

Output shape (required_output_keys per gdelt_tone_producer_schema.json):
    {
        "available": bool,
        "tone":      float | None,   # GDELT AvgTone averaged and normalized to [-1, 1]
        "per_ticker": dict | None,   # always None — GDELT timeline has no ticker filter
        "source":    str,            # human-readable provenance string
    }

On any error the function returns:
    {
        "available": False,
        "tone":      None,
        "per_ticker": None,
        "source":    None,
        "reason":    <exception class name>,   # D-1: no str(exc), no host/credential leak
    }

Design rules:
    - Fixture-first: output shape is pinned in gdelt_tone_producer_schema.json.
    - Endpoint: mode=timelinetone (NOT mode=artlist — artlist has no tone).
    - Bounded retries: _GDELT_MAX_ATTEMPTS is the hard ceiling (PC-crash lesson).
    - Explicit timeout: every request carries _GDELT_TIMEOUT_S.
    - D-1 error contract: reason is the exception class name only.
    - Off-execution-path: advisory-only, no trade-execution interaction.
    - No dynamic code execution or magic numbers.
"""

from __future__ import annotations

import logging
import time

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (named, no magic numbers — coding standard)
# ---------------------------------------------------------------------------

# GDELT 2.0 DOC API — timelinetone mode with tone values, free, no key.
# mode=timelinetone returns {date, value} entries where value is GDELT AvgTone.
# Timespan=1440 means the last 24 hours.
# This endpoint DOES carry tone; artlist does NOT.
_GDELT_TIMELINETONE_URL: str = (
    "https://api.gdeltproject.org/api/v2/doc/doc"
    "?query=stock+market+finance"
    "&mode=timelinetone"
    "&format=json"
    "&timespan=1440"
)

# Explicit HTTP timeout in seconds (project rule §5 — no urllib3 default).
_GDELT_TIMEOUT_S: float = 15.0

# Maximum number of HTTP attempts (including the first).  Named constant per
# project coding standard and the PC-crash bounded-retry lesson.
_GDELT_MAX_ATTEMPTS: int = 6

# Initial backoff sleep (seconds); doubles each retry; capped at _GDELT_BACKOFF_CAP_S.
_GDELT_BACKOFF_BASE_S: float = 1.0

# Maximum total wait time budget across all retries.
# Named constant — PC-crash lesson: the original implementation lacked this cap
# and collapsed delay to 0.0, creating an infinite 429 loop that consumed ~100 GB.
# With base=1 doubling: 1+2+4 = 7 s for 3 retries — generous but finite.
_GDELT_BACKOFF_CAP_S: float = 8.0

# Provenance string for the "source" field returned on success.
_GDELT_SOURCE: str = (
    "GDELT 2.0 DOC API timelinetone — https://api.gdeltproject.org/"
)

# GDELT AvgTone is in the range [-100, 100].  We normalize to [-1, 1].
_GDELT_TONE_DIVISOR: float = 100.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalize_tone(raw_tones: list[float]) -> float | None:
    """Average a list of raw GDELT AvgTone values and normalize to [-1, 1].

    Returns None when the list is empty (no tone data — no fabrication).
    Clamps the result to [-1.0, 1.0] to guard against rare GDELT out-of-spec
    values (e.g. AvgTone slightly outside [-100, 100] due to rounding).

    Args:
        raw_tones: list of GDELT AvgTone floats (pre-normalization, in [-100, 100]).

    Returns:
        Normalized average tone in [-1.0, 1.0], or None if raw_tones is empty.
    """
    if not raw_tones:
        return None
    avg = sum(raw_tones) / len(raw_tones)
    normalized = avg / _GDELT_TONE_DIVISOR
    # Clamp to exact contract bounds — do not propagate out-of-spec GDELT data.
    return max(-1.0, min(1.0, normalized))


def _fetch_gdelt_timelinetone() -> requests.Response:
    """GET the GDELT timelinetone endpoint with bounded exponential backoff.

    Retries on 429 and transient connection/timeout errors.  Hard-bounded by
    _GDELT_MAX_ATTEMPTS and _GDELT_BACKOFF_CAP_S to prevent the infinite-loop
    that caused the PC OOM crash.

    Returns the requests.Response (caller checks status_code / raises_for_status).
    Raises the last exception when all retries are exhausted.

    Three-condition retry predicate:
        1. attempt < _GDELT_MAX_ATTEMPTS   — hard attempt ceiling
        2. delay > 0.0                     — positive sleep guard (prevents collapsed-delay spin)
        3. total_waited + delay <= budget  — time budget not exhausted
    """
    delay = _GDELT_BACKOFF_BASE_S
    total_waited = 0.0
    attempt = 0

    while True:
        attempt += 1
        can_retry = (
            attempt < _GDELT_MAX_ATTEMPTS
            and delay > 0.0
            and total_waited + delay <= _GDELT_BACKOFF_CAP_S
        )
        try:
            resp = requests.get(
                _GDELT_TIMELINETONE_URL,
                timeout=_GDELT_TIMEOUT_S,
            )
            if resp.status_code == 429 and can_retry:
                logger.info(
                    "GDELT 429 on attempt %d — backing off %.1fs",
                    attempt,
                    delay,
                )
                time.sleep(delay)
                total_waited += delay
                delay = min(delay * 2, _GDELT_BACKOFF_CAP_S - total_waited)
                continue
            # Non-429 response or retry budget exhausted — return to caller.
            return resp
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        ) as exc:
            if not can_retry:
                raise
            logger.info(
                "GDELT %s on attempt %d — backing off %.1fs",
                type(exc).__name__,
                attempt,
                delay,
            )
            time.sleep(delay)
            total_waited += delay
            delay = min(delay * 2, _GDELT_BACKOFF_CAP_S - total_waited)


# ---------------------------------------------------------------------------
# Public producer
# ---------------------------------------------------------------------------


def _fetch_gdelt_sentiment(universe: list[str]) -> dict:
    """Fetch GDELT tone data via the timelinetone endpoint and return a normalized signal.

    This is the B1 GDELT tone-scoring producer for the Market Prism sentiment
    lens.  It is off-execution-path — advisory-only, never on the trade-execution
    path.

    Uses mode=timelinetone (NOT mode=artlist).  artlist carries no per-article
    AvgTone in the free GDELT API.  timelinetone returns {date, value} entries
    where value is GDELT AvgTone in [-100, 100]; we average and normalize to
    [-1, 1].

    The ``universe`` parameter is accepted for API compatibility with the lens
    pipeline (future per-ticker filtering).  The GDELT timelinetone endpoint does
    not support per-ticker queries; per_ticker is always None.

    Returns a dict matching the shape in gdelt_tone_producer_schema.json:
        available=True:
            {available, tone (float|None), per_ticker (None), source (str)}
        available=False:
            {available, tone (None), per_ticker (None), reason (str), source (None)}

    D-1 contract: reason carries only type(exc).__name__, never str(exc).
    No fabrication: tone is None when no timeline entries carry numeric values.

    Args:
        universe: list of ticker symbols (e.g. ["SPY", "QQQ"]).  Currently
            unused by the GDELT timelinetone API but accepted for interface parity.

    Returns:
        Producer output dict with the documented shape.
    """
    try:
        resp = _fetch_gdelt_timelinetone()
        # Treat a persistent 429 (retry budget exhausted) as a fetch failure.
        if resp.status_code == 429:
            logger.info("GDELT 429 after retry exhaustion — marking unavailable")
            return {
                "available": False,
                "tone": None,
                "per_ticker": None,
                "source": None,
                "reason": "TooManyRequests",
            }
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        # D-1 / CC-10: only the class name is persisted — never str(exc).
        logger.debug("GDELT fetch or parse failed: %s", type(exc).__name__)
        return {
            "available": False,
            "tone": None,
            "per_ticker": None,
            "source": None,
            "reason": type(exc).__name__,
        }

    # Parse the REAL GDELT 2.0 timelinetone response shape (captured 2026-06-13).
    # Real shape: {query_details, timeline:[{series:'Average Tone', data:[{date,value},...]}]}
    # The old parser called entry.get('value') on SERIES objects (no 'value' key) -> None.
    # Fix: iterate series['data'] to collect actual AvgTone floats.
    timeline_series = data.get("timeline") or []

    # Empty timeline: no data = no evidence -> return available=False.
    if not timeline_series:
        logger.info("GDELT returned empty timeline -- marking unavailable")
        return {
            "available": False,
            "tone": None,
            "per_ticker": None,
            "source": _GDELT_SOURCE,
            "reason": "empty_timeline",
        }

    # Collect numeric 'value' fields from all series data arrays.
    raw_tones: list[float] = []
    for series_obj in timeline_series:
        if not isinstance(series_obj, dict):
            continue
        data_points = series_obj.get("data") or []
        for entry in data_points:
            if not isinstance(entry, dict):
                continue
            tone_val = entry.get("value")
            if isinstance(tone_val, (int, float)):
                raw_tones.append(float(tone_val))

    tone = _normalize_tone(raw_tones)
    logger.info(
        "GDELT sentiment: %d series, %d data points with tone, normalized_tone=%s",
        len(timeline_series),
        len(raw_tones),
        tone,
    )
    return {
        "available": True,
        "tone": tone,
        "per_ticker": None,
        "source": _GDELT_SOURCE,
    }
