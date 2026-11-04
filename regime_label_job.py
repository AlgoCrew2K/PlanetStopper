"""Daily regime label job (STUB for RED; the implementer replaces every body).

Contract: feature-plans/regime-label-producer.md, feature-plans/approach/regime-label-producer.md.
"""

from __future__ import annotations

import logging
import time  # noqa: F401 -- tests patch regime_label_job.time.sleep
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import requests  # noqa: F401 -- tests patch requests.get

import database  # noqa: F401 -- tests patch database.save_regime_label
import market_calendar  # noqa: F401
import regime_classifier

logger = logging.getLogger("alphabot.regime_label")

REASON_INSUFFICIENT_HISTORY = "insufficient_history"
REASON_NO_HOLDINGS = "no_holdings"
REASON_PRICE_FETCH_FAILED = "price_fetch_failed"
REASON_NO_CLASSIFIER_LABEL = "no_classifier_label"
REASON_WRITE_FAILED = "write_failed"
REASON_AFTER_OPEN = "after_open"
REASON_UNEXPECTED_ERROR = "unexpected_error"

SIP_END_LAG = timedelta(minutes=20)
SIP_BATCH_SIZE = 30
MAX_ATTEMPTS = 3
REQUEST_TIMEOUT_SECONDS = 30.0
RETRY_AFTER_CAP_SECONDS = 30.0
REGIME_WINDOW_SESSIONS = regime_classifier.MIN_LABEL_SERIES_LENGTH
REGIME_WINDOW_MAX_LOOKBACK_SESSIONS = 30


@dataclass(frozen=True)
class SymphonyOutcome:
    symphony_id: str
    name: str
    label: str | None
    reason: str | None
    window_first: date | None
    window_last: date | None
    window_count: int
    detail: str | None


@dataclass(frozen=True)
class RegimeLabelRun:
    session: date | None
    outcomes: tuple = ()
    failure: str | None = None


@dataclass(frozen=True)
class SipCloses:
    closes: dict = field(default_factory=dict)  # ticker -> {date: close}
    failed: dict = field(default_factory=dict)  # ticker -> status text or exception class name


@dataclass(frozen=True)
class LabelComputation:
    label: str | None
    reason: str | None
    window_first: date | None
    window_last: date | None
    window_count: int
    series: tuple = ()


def target_session(start: datetime) -> date:
    raise NotImplementedError


def window_sessions(session: date) -> list:
    raise NotImplementedError


def fetch_sip_daily_closes(tickers, start: date, end: datetime) -> SipCloses:
    raise NotImplementedError


def compute_label(holdings, closes, session: date, newest_eligible: date) -> LabelComputation:
    raise NotImplementedError


def run_regime_label_job(load_symphonies, *, clock=None) -> RegimeLabelRun:
    raise NotImplementedError
