"""Composer account-scoped symphony daily-series client (Cycle 2e, read-only).

Fetches ONE symphony's realized daily value history from Composer's
``GET /api/v0.1/portfolio/accounts/{account_id}/symphonies/{symphony_id}`` and
turns it into a date-keyed series of simple daily returns in PERCENT scale --
the unit ``analytics.compute_quantstats_metrics`` and
``advisors.correlation_diagnostic`` already consume.

Source contract
---------------
The response carries parallel arrays ``epoch_ms`` (epoch MILLISECONDS) and
``deposit_adjusted_series`` (documented as a positive value-scale series with
capital flows removed -- see the [Unverified] caveat below), alongside a raw
``series`` that jumps on deposits/withdrawals. Only ``deposit_adjusted_series``
is ever used -- the raw ``series`` would turn a cash deposit into a fake
multi-hundred-percent "return".

[Unverified] ``deposit_adjusted_series`` semantics are inferred from the Composer
docs, not from a live capture (docs/audit/COMPOSER-HISTORICAL-SERIES-2026-09-03.md).
The fixture ``tests/fixtures/composer/symphony_daily_series_v1.json`` is
``synthetic-from-docs`` and must be recaptured with ``/api-fixture`` from a
network-enabled session. Until then treat the series as a positive value series
whose ratios are returns -- nothing here claims what it means economically.

Date handling
-------------
Each ``epoch_ms`` maps to its America/New_York calendar date (NOT the UTC date: a
late-evening ET point lands on the next UTC day). Several points on one ET date
collapse to the LAST point BEFORE returns are computed (close-to-close). Every
date >= today's ET date is dropped -- a partial day is never used.

Error contract (D-1)
--------------------
``fetch_symphony_daily_returns`` NEVER raises. Every failure is returned as a
``SeriesResult`` with ``error`` set to a short string that carries only an HTTP
status code, an exception TYPE name, or a fixed phrase -- never a response body,
header value, exception message or body value. Nothing credential-shaped reaches
any log record at any level.

Retry policy
------------
Bounded exponential backoff, GET only (no write is ever issued, so no
idempotency key is needed). At most ``MAX_ATTEMPTS`` requests; a sleep happens
only BETWEEN attempts, each at most ``RETRY_AFTER_CAP_SECONDS``, so the total
retry wait is bounded by ``MAX_TOTAL_RETRY_WAIT_SECONDS``. Retried: HTTP 429/500/
502/503/504 and any transport exception. Any other non-200 status is a hard
error with no retry. A 429 integer-seconds ``Retry-After`` header is honoured up
to the cap.

Off the 1-minute execution path; advisory-only; no trade, deploy or order
primitive exists in this module.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests

from alpha_bot_execution import COMPOSER_BASE_URL, get_composer_headers

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Named constants (every value source-commented)
# ---------------------------------------------------------------------------

# Explicit per-request HTTP timeout (seconds). Never rely on requests' default
# (None = wait forever). 30 s matches app.py's _ACCOUNT_TOTALS_HTTP_TIMEOUT_S for
# Composer account-scoped portfolio reads.
REQUEST_TIMEOUT_SECONDS: float = 30.0

# TOTAL attempts including the first. Bounds the retry loop (no unbounded retry).
MAX_ATTEMPTS: int = 3

# Hard ceiling on any single inter-attempt sleep, including a server-supplied
# Retry-After value (a hostile/buggy header must not be able to stall a run).
RETRY_AFTER_CAP_SECONDS: float = 30.0

# Named maximum total retry wait for one fetch: MAX_ATTEMPTS-1 inter-attempt
# sleeps, each at most RETRY_AFTER_CAP_SECONDS.
MAX_TOTAL_RETRY_WAIT_SECONDS: float = (MAX_ATTEMPTS - 1) * RETRY_AFTER_CAP_SECONDS

# Default backoff before the first retry when no usable Retry-After exists;
# doubles each further retry (exponential), then is capped at
# RETRY_AFTER_CAP_SECONDS. 1 s matches Composer's documented 1 req/s limit.
_BACKOFF_BASE_SECONDS: float = 1.0
_BACKOFF_GROWTH_FACTOR: int = 2

# HTTP statuses that are transient and warrant a retry (same set the sibling
# composer_backtest_client retries).
_RETRYABLE_HTTP_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})
_HTTP_OK: int = 200
_HTTP_TOO_MANY_REQUESTS: int = 429
_RETRY_AFTER_HEADER: str = "Retry-After"

# epoch_ms values are MILLISECONDS. Any value below 1e11 is not a plausible
# epoch-ms timestamp: 1e11 ms is 1973-03-03, while a present-day epoch in
# SECONDS is ~1.7e9 -- so a seconds-valued array lands below this floor and is
# rejected for the whole body instead of being silently misread as 1970.
_MIN_EPOCH_MS: float = 1e11
_MS_PER_SECOND: float = 1000.0

# Returns are emitted in PERCENT scale (10% == 10.0) -- the scale
# analytics.compute_quantstats_metrics expects (it divides by 100 itself).
_PERCENT_SCALE: float = 100.0

# Composer's trading-day calendar is US-equity; every epoch maps to this zone.
_ET = ZoneInfo("America/New_York")

# Hard cap on SeriesResult.error length (D-1: short, fixed-shape strings).
_ERROR_MAX_CHARS: int = 200

# Endpoint NAME for INFO logs: a fixed template, never the account/symphony ids.
_ENDPOINT_LOG_NAME: str = "GET /portfolio/accounts/{account}/symphonies/{symphony}"


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class SeriesResult:
    """Outcome of one ``fetch_symphony_daily_returns`` call.

    returns: ISO ``YYYY-MM-DD`` America/New_York date -> simple daily return in
        PERCENT scale. Empty on failure or when fewer than two usable dates exist.
    points: ``len(returns)``.
    first_date / last_date: ``min`` / ``max`` of ``returns``, or None when empty.
    error: None on success; otherwise a short D-1-safe failure string.
    """

    returns: dict[str, float] = field(default_factory=dict)
    points: int = 0
    first_date: str | None = None
    last_date: str | None = None
    error: str | None = None


def _error_result(reason: str) -> SeriesResult:
    """A failure result: no returns, ``error`` truncated to the D-1 length cap."""
    return SeriesResult(
        returns={},
        points=0,
        first_date=None,
        last_date=None,
        error=reason[:_ERROR_MAX_CHARS],
    )


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _is_number(value: Any) -> bool:
    """True for a real int/float. ``bool`` (an int subclass) and numeric
    STRINGS are rejected -- a wrong-typed element is a malformed body."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _epoch_ms_to_et_date(epoch_ms: float) -> date:
    """Epoch milliseconds -> America/New_York calendar date."""
    return datetime.fromtimestamp(epoch_ms / _MS_PER_SECOND, tz=_ET).date()


def _parse_series_body(body: Any, today_et: date) -> SeriesResult:
    """Parse a 200 response body into a ``SeriesResult``. Never raises.

    Steps: validate shape/types -> map each epoch to its ET date -> collapse to
    ONE value per ET date (the last point wins) -> drop dates >= ``today_et`` ->
    close-to-close simple returns in percent between consecutive remaining dates.
    """
    if not isinstance(body, dict):
        return _error_result("malformed response: body is not a JSON object")
    epochs = body.get("epoch_ms")
    values = body.get("deposit_adjusted_series")
    if not isinstance(epochs, list) or not isinstance(values, list):
        return _error_result("malformed response: epoch_ms/deposit_adjusted_series missing")
    if len(epochs) != len(values):
        return _error_result("malformed response: array length mismatch")
    if not all(_is_number(v) for v in values) or not all(_is_number(e) for e in epochs):
        return _error_result("malformed response: non-numeric array element")

    try:
        points = sorted(zip(epochs, values, strict=True), key=lambda p: p[0])
        if any(not math.isfinite(e) or e < _MIN_EPOCH_MS for e, _v in points):
            return _error_result("malformed response: epoch_ms is not epoch milliseconds")
        by_date: dict[date, float] = {}
        for epoch_ms, value in points:
            by_date[_epoch_ms_to_et_date(float(epoch_ms))] = float(value)  # last point wins
    except (OverflowError, OSError, ValueError):
        return _error_result("malformed response: unconvertible epoch or value")

    dates = sorted(d for d in by_date if d < today_et)
    returns: dict[str, float] = {}
    prev_value: float | None = None
    for d in dates:
        value = by_date[d]
        if (
            prev_value is not None
            and math.isfinite(prev_value)
            and math.isfinite(value)
            and prev_value > 0.0
            and value > 0.0  # a non-positive portfolio value is not a usable mark
        ):
            ret = (value / prev_value - 1.0) * _PERCENT_SCALE
            if math.isfinite(ret):
                returns[d.isoformat()] = ret
        prev_value = value

    return SeriesResult(
        returns=returns,
        points=len(returns),
        first_date=min(returns) if returns else None,
        last_date=max(returns) if returns else None,
        error=None,
    )


# ---------------------------------------------------------------------------
# Retry helpers
# ---------------------------------------------------------------------------


def _default_backoff(attempt_index: int) -> float:
    """Exponential backoff before retry ``attempt_index`` (0-based), capped."""
    return min(
        _BACKOFF_BASE_SECONDS * (_BACKOFF_GROWTH_FACTOR**attempt_index), RETRY_AFTER_CAP_SECONDS
    )


def _retry_after_delay(response: Any, default: float) -> float:
    """Sleep for a 429: the integer-seconds ``Retry-After`` clamped into
    ``[0, RETRY_AFTER_CAP_SECONDS]`` (a negative value clamps to 0 -- never a
    negative sleep); a missing / unparseable value (e.g. an HTTP-date) falls back
    to ``default``. The header value itself is never logged."""
    try:
        raw = response.headers.get(_RETRY_AFTER_HEADER)
        if isinstance(raw, str):
            text = raw.strip()
            digits = text[1:] if text.startswith("-") else text
            if digits.isascii() and digits.isdigit():
                return float(min(max(int(text), 0), RETRY_AFTER_CAP_SECONDS))
    except (AttributeError, TypeError, ValueError):
        pass
    return default


def _fetch_with_retry(account_id: str, symphony_id: str) -> SeriesResult:
    """The bounded retry loop. Sleeps only BETWEEN attempts."""
    url = f"{COMPOSER_BASE_URL}/portfolio/accounts/{account_id}/symphonies/{symphony_id}"
    headers = get_composer_headers()

    last_error = "no attempt made"
    for attempt in range(MAX_ATTEMPTS):
        delay = _default_backoff(attempt)
        try:
            response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
        except Exception as exc:
            # Any transport failure is retried, then reported by exception TYPE only.
            last_error = f"request failed: {type(exc).__name__}"
            logger.info(
                "composer_series: %s transport error %s (attempt %d/%d)",
                _ENDPOINT_LOG_NAME,
                type(exc).__name__,
                attempt + 1,
                MAX_ATTEMPTS,
            )
        else:
            status = response.status_code
            logger.info(
                "composer_series: %s HTTP %s (attempt %d/%d)",
                _ENDPOINT_LOG_NAME,
                status,
                attempt + 1,
                MAX_ATTEMPTS,
            )
            if status == _HTTP_OK:
                try:
                    body = response.json()
                except ValueError:
                    return _error_result("malformed response: body is not valid JSON")
                return _parse_series_body(body, datetime.now(_ET).date())
            last_error = f"HTTP {status}"
            if status not in _RETRYABLE_HTTP_STATUSES:
                return _error_result(last_error)
            if status == _HTTP_TOO_MANY_REQUESTS:
                delay = _retry_after_delay(response, delay)

        if attempt < MAX_ATTEMPTS - 1:
            time.sleep(delay)

    return _error_result(last_error)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_symphony_daily_returns(account_id: str, symphony_id: str) -> SeriesResult:
    """Fetch one symphony's realized daily returns (percent scale, ET dates).

    Issues ``GET {COMPOSER_BASE_URL}/portfolio/accounts/{account_id}/symphonies/
    {symphony_id}`` with an explicit timeout and bounded retry (see module
    docstring). NEVER raises: every failure -- HTTP, transport, malformed body, or
    an unexpected internal fault -- comes back as ``SeriesResult(error=<short
    D-1-safe string>)``. The final guard below records the exception TYPE in the
    returned error and a WARNING; it does not hide the failure from the caller.
    """
    try:
        return _fetch_with_retry(account_id, symphony_id)
    except Exception as exc:
        # D-1 never-raise contract: the failure is reported (type name), not hidden.
        logger.warning("composer_series: unexpected failure: %s", type(exc).__name__)
        return _error_result(f"unexpected failure: {type(exc).__name__}")
