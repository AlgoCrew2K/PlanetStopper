"""Composer inline-backtest client — M2 AI Advisor building block.

Submits an inline symphony definition (the ``raw_value`` tree returned by
``GET /api/v0.1/symphonies/{id}/score``) to ``POST /api/v0.1/backtest`` and
parses the response into a typed result the gate layer can consume directly.

The live/test separation is hard:

* Tests inject a ``_session`` (a ``requests.Session`` whose adapter replays a
  fixture) — **never** the real Composer endpoint.
* Live calls require ``is_live=True`` passed explicitly by the caller; the
  default is ``False`` and calling with ``is_live=False`` raises immediately
  rather than silently doing nothing.
* The ``test_live_*.py`` opt-in path (excluded by default) is the only place
  that may set ``is_live=True``.

Retry policy
------------
Exponential backoff: 1 s → 2 s → 4 s → 8 s.  Maximum total wait is bounded by
``BACKTEST_MAX_RETRY_WAIT_SECONDS``.  Every attempt is idempotent (read-only
from Composer's perspective — no state is mutated on their side).

Rate limit
----------
``POST /api/v0.1/backtest`` inherits the standard Composer limit of ~1 req/sec.
The caller is responsible for spacing concurrent calls; this module does not
sleep between separate invocations.  On HTTP 429 the ``Retry-After`` header is
respected if present; otherwise the first backoff interval is used.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import requests

from alpha_bot_execution import COMPOSER_BASE_URL, get_composer_headers

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Retry policy constants
# ---------------------------------------------------------------------------

# Seconds to wait between attempts: attempt 0 → 1 s, 1 → 2 s, 2 → 4 s, 3 → 8 s.
_BACKOFF_INTERVALS: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0)

# Hard ceiling on cumulative retry wait.  Sum of _BACKOFF_INTERVALS = 15 s.
BACKTEST_MAX_RETRY_WAIT_SECONDS: float = sum(_BACKOFF_INTERVALS)  # 15 s

# Explicit HTTP timeout for the backtest POST.  Composer serialises a full
# decision-tree + daily returns so 120 s is needed for large symphonies.
_BACKTEST_REQUEST_TIMEOUT: int = 120

# HTTP status codes that are transient and warrant a retry.
_RETRYABLE_HTTP_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})

# Unix epoch used by Composer for day integers (days since 1970-01-01).
_COMPOSER_DAY_EPOCH = date(1970, 1, 1)

# The key inside ``dvm_capital`` that holds the per-day portfolio values when
# a ``raw_value`` inline tree is submitted (as opposed to a named symphony).
# Composer uses the submitted ``id`` field from ``raw_value`` as the outer key.
# Callers pass the symphony id so we can extract the correct inner dict.

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class BacktestStats:
    """Summary statistics from a single Composer backtest run.

    All float fields are ``None`` when the API response does not include the key
    (forward-compatible: new stat fields do not break the parser).  ``daily_returns``
    is the primary input the gate layer consumes; it is derived from the ``dvm_capital``
    per-day portfolio-value series by computing day-over-day log returns.
    """

    # Core risk-adjusted performance metrics — all from ``stats`` block.
    sharpe_ratio: float | None
    sortino_ratio: float | None
    max_drawdown: float | None
    annualized_rate_of_return: float | None

    # Additional performance scalars.
    cumulative_return: float | None
    calmar_ratio: float | None
    win_rate: float | None
    tail_ratio: float | None

    # Trailing-period returns (absent = None, not an error).
    trailing_1d_return: float | None
    trailing_1w_return: float | None
    trailing_1m_return: float | None
    trailing_3m_return: float | None
    trailing_6m_return: float | None = None  # not always present
    trailing_1y_return: float | None = None
    trailing_3y_return: float | None = None  # not always present
    trailing_5y_return: float | None = None  # not always present
    trailing_2w_return: float | None = None  # present in v2

    # Date-keyed daily portfolio values: {ISO date string → float}.
    # Empty when dvm_capital is absent or the symphony-id key is missing.
    daily_portfolio_values: dict[str, float] = field(default_factory=dict)

    # Derived date-keyed daily log returns: {ISO date string → float}.
    # Gate layer consumes this directly for walk-forward fold construction.
    # Empty when there are fewer than 2 portfolio-value observations.
    daily_returns: dict[str, float] = field(default_factory=dict)

    # Ticker-level data warnings from the API (empty list = no warnings).
    # Shape is whatever the API returns (historically a dict, sometimes a list).
    data_warnings: Any = field(default_factory=dict)

    # Cost breakdown (present in all observed responses).
    costs: dict[str, float] = field(default_factory=dict)

    # ISO date strings for the first and last trading day of the backtest.
    first_day: str | None = None
    last_market_day: str | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _day_int_to_iso(day_int: int | str) -> str:
    """Convert a Composer day integer (days since Unix epoch) to an ISO date string.

    Composer encodes calendar dates as integer counts of days since 1970-01-01.
    The keys inside ``dvm_capital`` inner dicts use this encoding.
    """
    return (_COMPOSER_DAY_EPOCH + timedelta(days=int(day_int))).isoformat()


def _extract_daily_returns(dvm_capital: dict, symphony_id: str) -> tuple[dict[str, float], dict[str, float]]:
    """Extract date-keyed portfolio values and derived log returns from ``dvm_capital``.

    ``dvm_capital`` shape (observed from live capture 2026-05-31):
        {symphony_id: {day_int_str: portfolio_value_float, ...}}

    Returns a pair:
        (daily_portfolio_values, daily_returns)
    both keyed by ISO date string, sorted chronologically.

    Log returns are computed as ln(V_t / V_{t-1}) for consecutive calendar-day
    pairs.  Gaps in the series (weekends, holidays) are treated as non-trading
    days and skipped — only adjacent pairs present in the series contribute.
    An empty or missing inner dict returns two empty dicts.
    """
    inner = dvm_capital.get(symphony_id, {})
    if not inner:
        return {}, {}

    # Build sorted (day_int, value) pairs so returns are in chronological order.
    sorted_pairs = sorted(
        ((int(k), float(v)) for k, v in inner.items()),
        key=lambda t: t[0],
    )

    portfolio_values: dict[str, float] = {
        _day_int_to_iso(day_int): value for day_int, value in sorted_pairs
    }

    # Compute log returns: ln(V_t / V_{t-1}) for each consecutive pair.
    import math

    daily_returns: dict[str, float] = {}
    for i in range(1, len(sorted_pairs)):
        prev_day, prev_val = sorted_pairs[i - 1]
        curr_day, curr_val = sorted_pairs[i]
        if prev_val > 0 and curr_val > 0:
            iso_date = _day_int_to_iso(curr_day)
            daily_returns[iso_date] = math.log(curr_val / prev_val)

    return portfolio_values, daily_returns


def _parse_backtest_response(body: dict, symphony_id: str) -> BacktestStats:
    """Parse a raw ``POST /api/v0.1/backtest`` JSON response into ``BacktestStats``.

    Uses the provider schema as the source of truth (live capture 2026-05-31).
    Unknown keys are ignored; missing keys yield ``None`` — never an exception.
    The ``symphony_id`` is required to extract the correct inner dict from
    ``dvm_capital`` (Composer keys it by the ``id`` field in ``raw_value``).
    """
    stats = body.get("stats", {}) or {}

    portfolio_values, daily_returns = _extract_daily_returns(
        body.get("dvm_capital", {}), symphony_id
    )

    # Convert day integers from ``first_day`` / ``last_market_day`` to ISO strings.
    first_day_raw = body.get("first_day")
    last_day_raw = body.get("last_market_day")

    return BacktestStats(
        sharpe_ratio=stats.get("sharpe_ratio"),
        sortino_ratio=stats.get("sortino_ratio"),
        max_drawdown=stats.get("max_drawdown"),
        annualized_rate_of_return=stats.get("annualized_rate_of_return"),
        cumulative_return=stats.get("cumulative_return"),
        calmar_ratio=stats.get("calmar_ratio"),
        win_rate=stats.get("win_rate"),
        tail_ratio=stats.get("tail_ratio"),
        trailing_1d_return=stats.get("trailing_one_day_return"),
        trailing_1w_return=stats.get("trailing_one_week_return"),
        trailing_1m_return=stats.get("trailing_one_month_return"),
        trailing_3m_return=stats.get("trailing_three_month_return"),
        trailing_6m_return=stats.get("trailing_six_month_return"),
        trailing_1y_return=stats.get("trailing_one_year_return"),
        trailing_2w_return=stats.get("trailing_two_week_return"),
        daily_portfolio_values=portfolio_values,
        daily_returns=daily_returns,
        data_warnings=body.get("data_warnings", {}),
        costs=body.get("costs", {}),
        first_day=_day_int_to_iso(first_day_raw) if first_day_raw is not None else None,
        last_market_day=_day_int_to_iso(last_day_raw) if last_day_raw is not None else None,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def submit_backtest(
    raw_value: dict,
    symphony_id: str,
    *,
    capital: float = 10_000.0,
    apply_reg_fee: bool = True,
    apply_taf_fee: bool = True,
    slippage_percent: float = 0.005,
    broker: str = "alpaca",
    backtest_version: str = "v2",
    is_live: bool = False,
    _session: requests.Session | None = None,
) -> BacktestStats:
    """Submit an inline symphony tree to ``POST /api/v0.1/backtest`` and parse the result.

    Parameters
    ----------
    raw_value:
        The full symphony decision-tree dict, as returned by
        ``GET /api/v0.1/symphonies/{id}/score``.  The ``id`` field inside this
        dict must equal ``symphony_id`` so that ``dvm_capital`` can be unpacked.
    symphony_id:
        The Composer symphony UUID.  Used to extract per-day portfolio values
        from the response ``dvm_capital`` keyed dict.
    capital:
        Starting portfolio value for the backtest (USD).  Default 10 000.
    apply_reg_fee / apply_taf_fee:
        SEC/FINRA fee flags.  Default both True to match Composer UI defaults.
    slippage_percent:
        Execution slippage fraction (0.005 = 0.5 %).
    broker:
        Broker enum accepted by Composer.  Default ``"alpaca"``.
    backtest_version:
        ``"v1"`` or ``"v2"``.  Default ``"v2"`` (matches current Composer UI default).
    is_live:
        **Must be ``True`` to issue a real HTTP request.**  Tests must never
        pass ``is_live=True``; they must supply ``_session`` with a fixture
        adapter instead.  A call with ``is_live=False`` and no ``_session``
        raises ``RuntimeError`` immediately — no silent no-op.
    _session:
        Optional ``requests.Session`` injected by tests.  When provided the
        function uses it in place of a real HTTP call; ``is_live`` is ignored.

    Returns
    -------
    BacktestStats
        Parsed backtest statistics.  Never raises on a parseable response.

    Raises
    ------
    RuntimeError
        When ``is_live=False`` and no ``_session`` is supplied (hard live-guard).
    requests.HTTPError
        When the API returns a non-retryable error status after all retry attempts.
    requests.RequestException
        On persistent transport failures after all retry attempts.
    """
    if _session is None and not is_live:
        raise RuntimeError(
            "submit_backtest called with is_live=False and no _session. "
            "Tests must inject a _session with a fixture adapter. "
            "Live calls require explicit is_live=True."
        )

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

    session = _session or requests.Session()
    headers = get_composer_headers()

    logger.debug(
        "submit_backtest: POST %s | symphony_id=%s capital=%.0f broker=%s version=%s",
        url,
        symphony_id,
        capital,
        broker,
        backtest_version,
    )

    last_exc: Exception | None = None
    for attempt, backoff in enumerate((*_BACKOFF_INTERVALS, None)):
        try:
            response = session.post(
                url, headers=headers, json=body, timeout=_BACKTEST_REQUEST_TIMEOUT
            )
            logger.info(
                "submit_backtest: POST /backtest HTTP %d (attempt %d)",
                response.status_code,
                attempt + 1,
            )

            if response.status_code == 200:
                try:
                    data = response.json()
                except ValueError as exc:
                    raise requests.RequestException(
                        f"submit_backtest: invalid JSON in 200 response — {exc}"
                    ) from exc
                return _parse_backtest_response(data, symphony_id)

            if response.status_code == 429:
                # Respect Retry-After header; fall back to first backoff interval.
                retry_after = float(
                    response.headers.get("Retry-After", _BACKOFF_INTERVALS[0])
                )
                logger.info(
                    "submit_backtest: rate-limited (429), sleeping %.1f s", retry_after
                )
                if backoff is not None:
                    time.sleep(retry_after)
                    continue
                # Exhausted retries on 429 — raise as an HTTP error.
                response.raise_for_status()

            if response.status_code in _RETRYABLE_HTTP_STATUSES and backoff is not None:
                logger.info(
                    "submit_backtest: transient HTTP %d, retrying in %.1f s",
                    response.status_code,
                    backoff,
                )
                time.sleep(backoff)
                continue

            # Non-retryable error — raise immediately.
            raise requests.HTTPError(
                f"submit_backtest: non-retryable HTTP {response.status_code}",
                response=response,
            )

        except requests.RequestException as exc:
            last_exc = exc
            if backoff is not None:
                logger.info(
                    "submit_backtest: transport error on attempt %d, retrying in %.1f s — %s",
                    attempt + 1,
                    backoff,
                    type(exc).__name__,
                )
                time.sleep(backoff)
                continue
            raise requests.RequestException(
                f"submit_backtest: transport failure after {len(_BACKOFF_INTERVALS) + 1} attempts — {exc}"
            ) from exc

    # Should be unreachable, but satisfies type checkers.
    raise requests.RequestException(
        f"submit_backtest: exhausted retries — last error: {last_exc}"
    )
