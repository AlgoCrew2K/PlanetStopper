"""Read-only Composer portfolio client for the symphony tenure review.

Three GET reads (symphony statistics, one symphony's value series, daily holdings).
Every read is one request with no retry, paced by one sleep after each HTTP reply, and
never raises: a failure is a class string on the result. Logs carry the exception type
name only.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import requests

from alpha_bot_execution import COMPOSER_BASE_URL, get_composer_headers

logger = logging.getLogger(__name__)

# Per-request HTTP timeout, seconds: a bounded wait so a hung Composer read cannot stall a run.
COMPOSER_READ_TIMEOUT_SECONDS = 15
# Pause after every HTTP reply, seconds: keeps the read rate under Composer's documented
# 1 request per second limit with margin.
COMPOSER_READ_SPACING_SECONDS = 2.5
# Longest date range one holdings request may cover, calendar days: the span of the captured
# holdings request the endpoint was verified with.
HOLDINGS_REQUEST_SPAN_DAYS = 30

_MS_PER_SECOND = 1000


@dataclass(frozen=True)
class StatisticsRead:
    failure: str | None
    symphonies: list = field(default_factory=list)


@dataclass(frozen=True)
class SeriesRead:
    failure: str | None
    values: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FailedSpan:
    start: date
    end: date
    failure: str


@dataclass(frozen=True)
class HoldingsRead:
    days: dict = field(default_factory=dict)
    failed_spans: list = field(default_factory=list)


def _status_class(status: int) -> str:
    if status in (401, 403):
        return "refused"
    if status == 404:
        return "not_found"
    if status == 429:
        return "rate_limited"
    if status >= 500:
        return "unavailable"
    return "rejected"


def _get(path: str, params: dict | None = None):
    """One GET. Returns (failure, parsed JSON); sleeps once after any HTTP reply."""
    url = f"{COMPOSER_BASE_URL}{path}"
    try:
        kwargs = {"headers": get_composer_headers(), "timeout": COMPOSER_READ_TIMEOUT_SECONDS}
        if params is not None:
            kwargs["params"] = params
        response = requests.get(url, **kwargs)
    except Exception as exc:  # noqa: BLE001 - the module never raises
        logger.warning("composer read failed: %s", type(exc).__name__)
        return "unavailable", None
    time.sleep(COMPOSER_READ_SPACING_SECONDS)
    if response.status_code != 200:
        return _status_class(response.status_code), None
    try:
        return None, response.json()
    except ValueError:
        return "unreadable", None


def read_symphony_statistics(account_id) -> StatisticsRead:
    failure, body = _get(f"/portfolio/accounts/{account_id}/symphony-stats-meta")
    if failure:
        return StatisticsRead(failure, [])
    symphonies = body.get("symphonies") if isinstance(body, dict) else None
    if not isinstance(symphonies, list) or not all(
        isinstance(s, dict) and isinstance(s.get("id"), str) and s["id"].strip() for s in symphonies
    ):
        return StatisticsRead("unreadable", [])
    return StatisticsRead(None, symphonies)


def _utc_date(epoch_ms) -> str:
    return datetime.fromtimestamp(epoch_ms / _MS_PER_SECOND, tz=UTC).date().isoformat()


def read_symphony_value_series(account_id, symphony_id) -> SeriesRead:
    failure, body = _get(f"/portfolio/accounts/{account_id}/symphonies/{symphony_id}")
    if failure:
        return SeriesRead(failure, {})
    epochs = body.get("epoch_ms") if isinstance(body, dict) else None
    levels = body.get("deposit_adjusted_series") if isinstance(body, dict) else None
    if not isinstance(epochs, list) or not isinstance(levels, list) or len(epochs) != len(levels):
        return SeriesRead("unreadable", {})
    if not epochs:
        return SeriesRead("empty", {})
    try:
        return SeriesRead(None, {_utc_date(e): lv for e, lv in zip(epochs, levels, strict=True)})
    except (TypeError, ValueError, OverflowError, OSError):
        return SeriesRead("unreadable", {})


def _spans(start: date, end: date) -> list[tuple[date, date]]:
    spans = []
    cursor = start
    while cursor <= end:
        span_end = min(cursor + timedelta(days=HOLDINGS_REQUEST_SPAN_DAYS - 1), end)
        spans.append((cursor, span_end))
        cursor = span_end + timedelta(days=1)
    return spans


def _parse_holdings(body) -> dict | None:
    by_symphony = body.get("by_symphony") if isinstance(body, dict) else None
    if not isinstance(by_symphony, dict):
        return None
    days: dict = {}
    for symphony_id, per_day in by_symphony.items():
        if not isinstance(per_day, dict):
            return None
        for day, rows in per_day.items():
            if not isinstance(rows, list):
                return None
            tickers = {}
            for row in rows:
                if not isinstance(row, dict) or "symbol" not in row or "value" not in row:
                    return None
                tickers[row["symbol"]] = row["value"]
            days.setdefault(day, {})[symphony_id] = tickers
    return days


def read_daily_holdings(account_id, start, end) -> HoldingsRead:
    days: dict = {}
    failed: list[FailedSpan] = []
    for span_start, span_end in _spans(start, end):
        params = {
            "start_date": span_start.isoformat(),
            "end_date": span_end.isoformat(),
            "include_aggregate_account_holdings": "false",
        }
        failure, body = _get(
            f"/portfolio/accounts/{account_id}/symphony-historical-holdings", params
        )
        parsed = None
        if failure is None:
            parsed = _parse_holdings(body)
            if parsed is None:
                failure = "unreadable"
        if failure:
            failed.append(FailedSpan(span_start, span_end, failure))
            continue
        for day, by_id in parsed.items():
            days.setdefault(day, {}).update(by_id)
    return HoldingsRead(days, failed)
