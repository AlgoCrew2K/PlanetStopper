"""Read-only Composer portfolio client for the symphony tenure review.

Four GET reads (symphony statistics, one symphony's value series, one symphony's
definition, daily holdings). Every read is one request with no retry and no redirect
following, paced by one sleep after each request whether or not it was answered, and never
raises: a failure is a class string on the result. Logs carry the endpoint kind, the HTTP
status and exception type names only.
"""

from __future__ import annotations

import logging
import math
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import requests

from alpha_bot_execution import COMPOSER_BASE_URL, get_composer_headers

logger = logging.getLogger(__name__)

# Per-request HTTP timeout, seconds: a bounded wait so a hung Composer read cannot stall a run.
COMPOSER_READ_TIMEOUT_SECONDS = 15
# Pause after every request, seconds: a run makes at most 10 requests a minute, well under the
# 25 requests per minute Composer documents for these endpoints, so the engine and the dashboard,
# which keep reading Composer while a run is in progress, still have the remaining headroom.
COMPOSER_READ_SPACING_SECONDS = 6.0
# Longest date range one holdings request may cover, calendar days: the span of the captured
# holdings request the endpoint was verified with.
HOLDINGS_REQUEST_SPAN_DAYS = 30

_MS_PER_SECOND = 1000

# A symphony or account id is one plain URL path segment; anything else could reroute the request.
_PLAIN_ID = re.compile(r"[A-Za-z0-9_-]+")


@dataclass(frozen=True)
class StatisticsRead:
    """The symphony roster of one account, or the failure class that prevented reading it."""

    failure: str | None
    symphonies: list = field(default_factory=list)


@dataclass(frozen=True)
class SeriesRead:
    """A symphony's deposit-adjusted values by UTC date, or a failure class."""

    failure: str | None
    values: dict = field(default_factory=dict)


@dataclass(frozen=True)
class DefinitionRead:
    """A symphony's decision-tree definition, or a failure class."""

    failure: str | None
    definition: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FailedSpan:
    """A holdings date span that could not be read, with its failure class."""

    start: date
    end: date
    failure: str


@dataclass(frozen=True)
class HoldingsRead:
    """Holdings by day and symphony for the spans that were read, plus the failed spans."""

    days: dict = field(default_factory=dict)
    failed_spans: list = field(default_factory=list)


def _status_class(status: int) -> str:
    """The failure class of a non-200 status; a redirect or any other odd status is rejected."""
    if status in (401, 403):
        return "refused"
    if status == 404:
        return "not_found"
    if status == 429:
        return "rate_limited"
    if status >= 500:
        return "unavailable"
    return "rejected"


def is_valid_spacing_seconds(value: object) -> bool:
    """Whether ``value`` is a finite number of zero or more (a boolean is not a number here)."""
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _is_plain_id(identifier: object) -> bool:
    """Whether the id is a non-empty string of letters, digits, "_" and "-" only."""
    return isinstance(identifier, str) and _PLAIN_ID.fullmatch(identifier) is not None


def _get(
    kind: str,
    path: str,
    params: dict | None = None,
    spacing_seconds: float | None = None,
) -> tuple[str | None, object]:
    """One GET without redirects. Returns (failure, parsed JSON); sleeps once after any request."""
    spacing = (
        spacing_seconds
        if is_valid_spacing_seconds(spacing_seconds)
        else (COMPOSER_READ_SPACING_SECONDS)
    )
    url = f"{COMPOSER_BASE_URL}{path}"
    try:
        kwargs = {
            "headers": get_composer_headers(),
            "timeout": COMPOSER_READ_TIMEOUT_SECONDS,
            "allow_redirects": False,
        }
        if params is not None:
            kwargs["params"] = params
        response = requests.get(url, **kwargs)
    except Exception as exc:  # noqa: BLE001 - the module never raises
        logger.warning("composer %s read failed: %s", kind, type(exc).__name__)
        return "unavailable", None
    finally:
        # The spacing holds whether or not the request was answered.
        time.sleep(spacing)
    logger.info("composer %s read: HTTP %d", kind, response.status_code)
    if response.status_code != 200:
        return _status_class(response.status_code), None
    try:
        return None, response.json()
    except ValueError:
        return "unreadable", None


def read_symphony_statistics(
    account_id: str, spacing_seconds: float | None = None
) -> StatisticsRead:
    """The symphonies an account lists, from its symphony-stats-meta endpoint.

    An account id that is not a plain id segment is refused as unreadable without a request.
    """
    if not _is_plain_id(account_id):
        return StatisticsRead("unreadable", [])
    failure, body = _get(
        "statistics",
        f"/portfolio/accounts/{account_id}/symphony-stats-meta",
        spacing_seconds=spacing_seconds,
    )
    if failure:
        return StatisticsRead(failure, [])
    symphonies = body.get("symphonies") if isinstance(body, dict) else None
    if not isinstance(symphonies, list) or not all(
        isinstance(s, dict) and isinstance(s.get("id"), str) and s["id"].strip() for s in symphonies
    ):
        return StatisticsRead("unreadable", [])
    return StatisticsRead(None, symphonies)


def _utc_date(epoch_ms: float) -> str:
    return datetime.fromtimestamp(epoch_ms / _MS_PER_SECOND, tz=UTC).date().isoformat()


def read_symphony_value_series(
    account_id: str, symphony_id: str, spacing_seconds: float | None = None
) -> SeriesRead:
    """One symphony's deposit-adjusted value by UTC date, from the account's portfolio data.

    An id that is not a plain id segment is refused as unreadable without a request.
    """
    if not _is_plain_id(account_id) or not _is_plain_id(symphony_id):
        return SeriesRead("unreadable", {})
    failure, body = _get(
        "series",
        f"/portfolio/accounts/{account_id}/symphonies/{symphony_id}",
        spacing_seconds=spacing_seconds,
    )
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


def read_symphony_definition(
    symphony_id: str, spacing_seconds: float | None = None
) -> DefinitionRead:
    """One symphony's current decision tree, from its score endpoint, keeping the status.

    An id that is not a plain id segment is refused as unreadable without a request.
    """
    if not _is_plain_id(symphony_id):
        return DefinitionRead("unreadable", {})
    failure, body = _get(
        "definition", f"/symphonies/{symphony_id}/score", spacing_seconds=spacing_seconds
    )
    if failure:
        return DefinitionRead(failure, {})
    if not isinstance(body, dict):
        return DefinitionRead("unreadable", {})
    return DefinitionRead(None, body)


def _spans(start: date, end: date) -> list[tuple[date, date]]:
    spans = []
    cursor = start
    while cursor <= end:
        span_end = min(cursor + timedelta(days=HOLDINGS_REQUEST_SPAN_DAYS - 1), end)
        spans.append((cursor, span_end))
        cursor = span_end + timedelta(days=1)
    return spans


def _parse_holdings(body: object) -> dict | None:
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
                if (
                    not isinstance(row, dict)
                    or not isinstance(row.get("symbol"), str)
                    or "value" not in row
                ):
                    return None
                tickers[row["symbol"]] = row["value"]
            days.setdefault(day, {})[symphony_id] = tickers
    return days


def read_daily_holdings(
    account_id: str, start: date, end: date, spacing_seconds: float | None = None
) -> HoldingsRead:
    """Daily holdings of an account from ``start`` to ``end`` in client-sized date spans.

    A rate-limited span ends the reads: it and every later span are listed as failed spans.
    An account id that is not a plain id segment fails every span as unreadable, with no request.
    """
    days: dict = {}
    failed: list[FailedSpan] = []
    spans = _spans(start, end)
    if not _is_plain_id(account_id):
        return HoldingsRead({}, [FailedSpan(s, e, "unreadable") for s, e in spans])
    for index, (span_start, span_end) in enumerate(spans):
        params = {
            "start_date": span_start.isoformat(),
            "end_date": span_end.isoformat(),
            "include_aggregate_account_holdings": "false",
        }
        failure, body = _get(
            "holdings",
            f"/portfolio/accounts/{account_id}/symphony-historical-holdings",
            params,
            spacing_seconds,
        )
        parsed = None
        if failure is None:
            parsed = _parse_holdings(body)
            if parsed is None:
                failure = "unreadable"
        if failure == "rate_limited":
            failed.extend(FailedSpan(s, e, failure) for s, e in spans[index:])
            break
        if failure:
            failed.append(FailedSpan(span_start, span_end, failure))
            continue
        for day, by_id in parsed.items():
            days.setdefault(day, {}).update(by_id)
    return HoldingsRead(days, failed)
