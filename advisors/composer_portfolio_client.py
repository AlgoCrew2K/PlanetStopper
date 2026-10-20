"""Import stub: the implementer replaces this file with the read-only Composer portfolio client."""

from dataclasses import dataclass, field
from datetime import date

# Trivially wrong placeholders: the implementer sets the real values.
COMPOSER_READ_TIMEOUT_SECONDS = 0
COMPOSER_READ_SPACING_SECONDS = 0.0
HOLDINGS_REQUEST_SPAN_DAYS = 0


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


def read_symphony_statistics(account_id):
    raise NotImplementedError("read_symphony_statistics")


def read_symphony_value_series(account_id, symphony_id):
    raise NotImplementedError("read_symphony_value_series")


def read_daily_holdings(account_id, start, end):
    raise NotImplementedError("read_daily_holdings")
