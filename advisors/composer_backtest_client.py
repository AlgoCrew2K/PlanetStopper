"""Composer inline-backtest client — M2 AI Advisor building block.

Submits an inline symphony definition (the ``raw_value`` tree returned by
``GET /api/v0.1/symphonies/{id}/score``) to ``POST /api/v0.1/backtest`` and
returns a typed ``BacktestResult`` the gate layer can consume directly.

Error handling contract (AC-X5)
--------------------------------
This module never raises on API or transport errors — every failure mode
returns a ``BacktestResult`` with ``stats=None`` and ``error=<reason string>``.
A single candidate's failure must not abort the batch.

Live / test separation
-----------------------
Tests patch ``requests.post`` to inject fixture responses.  This module does
not use a Session internally so the standard ``patch("requests.post", ...)``
intercept works without additional wiring.

No top-level network calls are made at import time.

Retry policy
------------
Exponential backoff: 1 s → 2 s → 4 s → 8 s.  ``max_retries`` is a per-call
parameter (default 4).  The maximum cumulative wait is bounded by
``BACKTEST_MAX_RETRY_WAIT_SECONDS`` for a caller with the default ``min_retry_wait_seconds``
only; a larger floor can outrun it.  429 responses respect the ``Retry-After``
header when present, clamped to 60 s; otherwise the first backoff interval is used.
A caller may raise every wait to a floor with ``min_retry_wait_seconds``.

Rate limit
----------
``POST /api/v0.1/backtest`` inherits the standard Composer 1 req/sec limit.
Callers are responsible for spacing concurrent candidate batches; this module
does not sleep between separate ``run_backtest`` calls.
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import requests

from alpha_bot_execution import COMPOSER_BASE_URL, get_composer_headers

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------

# Backoff intervals (seconds) indexed by attempt number (0-based).
_BACKOFF_INTERVALS: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0)

# Longest wait taken on a server's Retry-After say-so, seconds: the length of Composer's
# per-minute rate-limit window, so a mistaken or hostile header cannot stall a caller for hours.
_MAX_RETRY_AFTER_SECONDS: float = 60.0

# Ceiling on the cumulative retry wait of a default caller, seconds: the longest case is every
# one of the four default retries answered 429 with a Retry-After at the clamp.
BACKTEST_MAX_RETRY_WAIT_SECONDS: float = len(_BACKOFF_INTERVALS) * _MAX_RETRY_AFTER_SECONDS

# Explicit per-request HTTP timeout.  Composer serialises a full decision-tree
# plus per-day portfolio values so 120 s is required for large symphonies.
_BACKTEST_REQUEST_TIMEOUT: int = 120

# HTTP status codes that are transient and warrant a retry.
_RETRYABLE_HTTP_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})

# Unix epoch used by Composer for day integers (days since 1970-01-01).
_COMPOSER_DAY_EPOCH = date(1970, 1, 1)

# Default backtest parameters — match the Composer UI defaults.
_DEFAULT_CAPITAL: float = 10_000.0
_DEFAULT_SLIPPAGE_PERCENT: float = 0.005
_DEFAULT_BROKER: str = "alpaca"
_DEFAULT_BACKTEST_VERSION: str = "v2"


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class BacktestResult:
    """Structured result from a single ``POST /api/v0.1/backtest`` call.

    On success: ``stats`` is the ``stats`` dict from the response,
    ``data_warnings`` is a list of any ticker-level history warnings,
    ``daily_returns`` is a date-keyed dict of simple (arithmetic) returns
    derived from the per-day portfolio values, and ``error`` is ``None``.

    On failure: ``stats`` is ``None``, ``error`` is a non-empty string
    describing the failure reason, and all other fields are empty defaults.
    """

    # On success: the ``stats`` block from the Composer response.  On failure: None.
    stats: dict[str, Any] | None

    # Ticker-level data warnings from the API (empty list = no warnings or no data).
    # Always a list (never None) regardless of the raw response shape.
    data_warnings: list[Any]

    # Date-keyed (ISO string) simple (arithmetic) returns derived from dvm_capital
    # portfolio values (AC-5 / DE-MATH-R2-001: simple, not log — see _extract_returns).
    # Gate layer consumes this for walk-forward fold construction.
    # Empty dict on failure or when dvm_capital is absent.
    daily_returns: dict[str, float] = field(default_factory=dict)

    # Date-keyed portfolio values: {ISO date → float}.  Empty on failure.
    daily_portfolio_values: dict[str, float] = field(default_factory=dict)

    # ISO date strings for the first and last trading day of the backtest.
    first_day: str | None = None
    last_market_day: str | None = None

    # Cost breakdown.  Empty dict on failure.
    costs: dict[str, float] = field(default_factory=dict)

    # None on success; non-empty string describing the failure on any error.
    error: str | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _day_int_to_iso(day_int: int | str) -> str:
    """Convert a Composer day integer (days since Unix epoch) to an ISO date string."""
    return (_COMPOSER_DAY_EPOCH + timedelta(days=int(day_int))).isoformat()


def _extract_returns(
    dvm_capital: dict[str, Any], symphony_id: str
) -> tuple[dict[str, float], dict[str, float]]:
    """Extract date-keyed portfolio values and simple returns from ``dvm_capital``.

    ``dvm_capital`` shape (live capture 2026-05-31):
        {symphony_id: {day_int_str: portfolio_value_float, ...}}

    Returns a pair:
        (daily_portfolio_values, daily_returns)
    both keyed by ISO date string, sorted chronologically.

    Simple (arithmetic) returns are computed as V_t / V_{t-1} - 1 for
    consecutive pairs — NOT log returns. AC-5 / DE-MATH-R2-001 (ma-stats M1):
    every downstream consumer (analytics.compute_quantstats_metrics's
    (1+r).prod()-1 compounding and quantstats' own cagr/calmar internals;
    math_engine.compute_crra_eu_objective's W = 1 + r wealth-ratio math via
    the PBO/CRRA-EU gate path) assumes simple-return compounding — feeding
    log returns through simple-return math is a category error (log returns
    require exp(sum(r))-1, not prod(1+r)-1), understating compounded return
    by an amount that grows with volatility. Emitting the simple return
    directly here fixes both consumers at their single shared producer.
    Gaps in the series (weekends, holidays) are non-trading days and skipped.
    Empty or missing inner dict → two empty dicts.
    """
    inner = dvm_capital.get(symphony_id, {})
    if (not isinstance(inner, dict) or not inner) and len(dvm_capital) == 1:
        # Inline synthetic-tree backtests: the response key Composer uses for
        # dvm_capital is unattested (the only captured fixture is a real-symphony
        # backtest where request id == response key). When exactly one series
        # came back, it is structurally the answer to the one tree we posted —
        # use it regardless of its key, and log the mismatch for fixture capture.
        # Live-daemon E2E 2026-06-12: the exact-match-only lookup made the whole
        # proposal pipeline degrade SILENTLY (empty returns → all candidates
        # withheld with None metrics) whenever the key didn't match.
        ((only_key, inner),) = dvm_capital.items()
        logger.warning(
            "_extract_returns: dvm_capital key %r != requested symphony_id %r; "
            "using the single returned series (capture a fixture via /api-fixture "
            "to attest inline-backtest key semantics)",
            only_key,
            symphony_id,
        )
    if not isinstance(inner, dict) or not inner:
        return {}, {}

    sorted_pairs = sorted(
        ((int(k), float(v)) for k, v in inner.items()),
        key=lambda t: t[0],
    )

    portfolio_values: dict[str, float] = {
        _day_int_to_iso(day_int): value for day_int, value in sorted_pairs
    }

    daily_returns: dict[str, float] = {}
    for i in range(1, len(sorted_pairs)):
        _, prev_val = sorted_pairs[i - 1]
        curr_day, curr_val = sorted_pairs[i]
        if prev_val > 0 and curr_val > 0:
            daily_returns[_day_int_to_iso(curr_day)] = (curr_val / prev_val) - 1.0

    return portfolio_values, daily_returns


def _coerce_data_warnings_to_list(raw: Any) -> list[Any]:
    """Normalise the ``data_warnings`` field to a list regardless of API shape.

    The live API has returned both an empty dict ``{}`` and a list ``[]``.
    Tests assert ``isinstance(result.data_warnings, list)`` so we normalise here.
    An empty dict → empty list.  A list → unchanged.  Anything else → wrapped in list.
    """
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict) and not raw:
        return []
    if isinstance(raw, dict):
        # Non-empty dict of warnings — flatten to values list.
        return list(raw.values())
    return [raw] if raw is not None else []


def _parse_response(body: dict, symphony_id: str) -> BacktestResult:
    """Parse a raw 200 response body into ``BacktestResult``.

    Missing keys yield safe defaults — never an exception.
    ``symphony_id`` is required to extract per-day values from ``dvm_capital``.
    """
    portfolio_values, daily_returns = _extract_returns(body.get("dvm_capital", {}), symphony_id)

    first_day_raw = body.get("first_day")
    last_day_raw = body.get("last_market_day")

    return BacktestResult(
        stats=body.get("stats"),
        data_warnings=_coerce_data_warnings_to_list(body.get("data_warnings")),
        daily_returns=daily_returns,
        daily_portfolio_values=portfolio_values,
        first_day=_day_int_to_iso(first_day_raw) if first_day_raw is not None else None,
        last_market_day=_day_int_to_iso(last_day_raw) if last_day_raw is not None else None,
        costs=body.get("costs") or {},
        error=None,
    )


def _retry_after_seconds(header: str | None, fallback: float) -> float:
    """The wait a Retry-After header asks for, in seconds, within 0..the clamp.

    Only a plain number of seconds is honoured; an HTTP-date, words, an empty value or
    nan fall back to ``fallback``. Negative values wait 0, large or infinite ones the clamp.
    """
    try:
        seconds = float(header)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback
    if math.isnan(seconds):
        return fallback
    return min(max(seconds, 0.0), _MAX_RETRY_AFTER_SECONDS)


def _error_result(reason: str) -> BacktestResult:
    """Construct a failure ``BacktestResult`` with the given reason string."""
    return BacktestResult(stats=None, data_warnings=[], error=reason)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_backtest(
    raw_value: dict,
    symphony_id: str = "",
    *,
    capital: float = _DEFAULT_CAPITAL,
    apply_reg_fee: bool = True,
    apply_taf_fee: bool = True,
    slippage_percent: float = _DEFAULT_SLIPPAGE_PERCENT,
    broker: str = _DEFAULT_BROKER,
    backtest_version: str = _DEFAULT_BACKTEST_VERSION,
    max_retries: int = 4,
    start_date: str | None = None,
    end_date: str | None = None,
    compact_body: bool = False,
    retry_on_rate_limit: bool = True,
    min_retry_wait_seconds: float = 0.0,
) -> BacktestResult:
    """Submit an inline symphony tree to ``POST /api/v0.1/backtest`` and parse the result.

    Parameters
    ----------
    raw_value:
        The full symphony decision-tree dict, as returned by
        ``GET /api/v0.1/symphonies/{id}/score``.  The ``id`` field inside this
        dict is used as ``symphony_id`` when the caller does not supply one
        explicitly — this matches the key Composer uses in ``dvm_capital``.
    symphony_id:
        Composer symphony UUID for unpacking ``dvm_capital``.  Defaults to
        ``raw_value.get("id", "")`` when not supplied.
    capital:
        Starting portfolio value (USD).  Default 10 000.
    apply_reg_fee / apply_taf_fee:
        SEC/FINRA fee flags.  Default both True to match Composer UI defaults.
    slippage_percent:
        Execution slippage fraction (0.005 = 0.5 %).
    broker:
        Broker enum accepted by Composer.  Default ``"alpaca"``.
    backtest_version:
        ``"v1"`` or ``"v2"``.  Default ``"v2"``.
    max_retries:
        Maximum number of retry attempts after transient failures.  A value of
        0 means no retries (first attempt only).  Bounded retry loops only —
        never unbounded.
    start_date / end_date:
        ISO dates added to the request body only when given.
    compact_body:
        Send the body as compact ASCII JSON text (``data=``) instead of
        ``json=``; a large tree otherwise exceeds the request size limit (413).
    retry_on_rate_limit:
        When False a 429 returns its error result at once instead of waiting and retrying.
    min_retry_wait_seconds:
        Floor for every wait before a retry, seconds. Default 0 (no floor).

    Returns
    -------
    BacktestResult
        On success: ``error=None``, ``stats`` populated.
        On failure: ``error`` is a non-empty string, ``stats=None``.
        Never raises on API or transport errors.
    """
    # Fall back to the id embedded in raw_value when the caller does not supply one.
    sid = symphony_id or (raw_value.get("id", "") if isinstance(raw_value, dict) else "")

    url = f"{COMPOSER_BASE_URL}/backtest"
    body = {
        "symphony": {"raw_value": raw_value},
        "capital": capital,
        "apply_reg_fee": apply_reg_fee,
        "apply_taf_fee": apply_taf_fee,
        "slippage_percent": slippage_percent,
        "broker": broker,
        "backtest_version": backtest_version,
    }
    if start_date is not None:
        body["start_date"] = start_date
    if end_date is not None:
        body["end_date"] = end_date
    headers = get_composer_headers()
    post_kwargs: dict[str, Any] = {"json": body, "allow_redirects": False}
    if compact_body:
        try:
            payload = json.dumps(body, separators=(",", ":"), allow_nan=False)
        except (ValueError, TypeError) as exc:
            return _error_result(f"request body not serializable: {type(exc).__name__}")
        post_kwargs = {"data": payload, "allow_redirects": False}
        headers = {**headers, "Content-Type": "application/json"}

    logger.debug(
        "run_backtest: POST %s | symphony_id=%s capital=%.0f broker=%s version=%s",
        url,
        sid,
        capital,
        broker,
        backtest_version,
    )

    # Build backoff schedule clamped to max_retries.
    backoff_schedule = _BACKOFF_INTERVALS[:max_retries]

    last_reason: str = "unknown error"
    for attempt in range(max_retries + 1):
        try:
            response = requests.post(
                url, headers=headers, timeout=_BACKTEST_REQUEST_TIMEOUT, **post_kwargs
            )
            logger.info(
                "run_backtest: POST /backtest HTTP %d (attempt %d/%d)",
                response.status_code,
                attempt + 1,
                max_retries + 1,
            )

            if response.status_code == 200:
                try:
                    data = response.json()
                except ValueError as exc:
                    return _error_result(f"invalid JSON in 200 response: {exc}")
                return _parse_response(data, sid)

            if response.status_code == 429:
                retry_after = _retry_after_seconds(
                    response.headers.get("Retry-After"), _BACKOFF_INTERVALS[0]
                )
                last_reason = f"HTTP 429 rate limit exceeded; Retry-After={retry_after:.0f}s"
                if retry_on_rate_limit and attempt < max_retries:
                    retry_after = max(retry_after, min_retry_wait_seconds)
                    logger.info(
                        "run_backtest: rate-limited (429), sleeping %.1f s (attempt %d)",
                        retry_after,
                        attempt + 1,
                    )
                    time.sleep(retry_after)
                    continue
                return _error_result(last_reason)

            if response.status_code in _RETRYABLE_HTTP_STATUSES and attempt < max_retries:
                delay = max(
                    backoff_schedule[attempt]
                    if attempt < len(backoff_schedule)
                    else _BACKOFF_INTERVALS[-1],
                    min_retry_wait_seconds,
                )
                last_reason = f"HTTP {response.status_code} (transient)"
                logger.info(
                    "run_backtest: transient HTTP %d, retrying in %.1f s (attempt %d)",
                    response.status_code,
                    delay,
                    attempt + 1,
                )
                time.sleep(delay)
                continue

            # Non-retryable or retries exhausted.
            last_reason = f"HTTP {response.status_code}: {response.text[:200]}"
            return _error_result(last_reason)

        except requests.Timeout as exc:
            last_reason = f"request timed out after {_BACKTEST_REQUEST_TIMEOUT}s: {exc}"
            logger.info("run_backtest: timeout on attempt %d — %s", attempt + 1, type(exc).__name__)
            # Timeout is not worth retrying (the server may still be processing);
            # return an error result immediately.
            return _error_result(last_reason)

        except requests.RequestException as exc:
            last_reason = f"transport error: {type(exc).__name__}: {exc}"
            logger.info(
                "run_backtest: transport error on attempt %d — %s",
                attempt + 1,
                type(exc).__name__,
            )
            if attempt < max_retries:
                delay = (
                    backoff_schedule[attempt]
                    if attempt < len(backoff_schedule)
                    else _BACKOFF_INTERVALS[-1]
                )
                time.sleep(max(delay, min_retry_wait_seconds))
                continue
            return _error_result(last_reason)

    return _error_result(last_reason)


# Alias for callers who prefer the submit_backtest name.
submit_backtest = run_backtest
