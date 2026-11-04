"""Daily regime label job: one regime label per live symphony, written to regime_label_cache.

Runs from the 04:30 scheduler slot, never on the 1-minute engine path. Each label is computed from
SIP daily closes of the symphony's stored holdings and dated the session whose open follows the run.
"""

from __future__ import annotations

import logging
import math
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from zoneinfo import ZoneInfo

import requests

import database
import market_calendar
import regime_classifier
import synthetic_history
import timing_log

logger = logging.getLogger("alphabot.regime_label")

REASON_INSUFFICIENT_HISTORY = "insufficient_history"
REASON_NO_HOLDINGS = "no_holdings"
REASON_PRICE_FETCH_FAILED = "price_fetch_failed"
REASON_NO_CLASSIFIER_LABEL = "no_classifier_label"
REASON_WRITE_FAILED = "write_failed"
REASON_AFTER_OPEN = "after_open"
REASON_UNEXPECTED_ERROR = "unexpected_error"

# The SIP feed refuses a request whose end is inside the last 15 minutes; 20 leaves margin.
SIP_END_LAG = timedelta(minutes=20)
SIP_BATCH_SIZE = 30
MAX_ATTEMPTS = 3
REQUEST_TIMEOUT_SECONDS = 30.0
RETRY_AFTER_CAP_SECONDS = 30.0
REGIME_WINDOW_SESSIONS = regime_classifier.MIN_LABEL_SERIES_LENGTH
REGIME_WINDOW_MAX_LOOKBACK_SESSIONS = 30

_ET = ZoneInfo("America/New_York")
_BARS_PATH = "/v2/stocks/bars"
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
_BACKOFF_START_SECONDS = 1.0
_PAGE_PAUSE_SECONDS = 0.25
_BAR_LIMIT = 10000  # Alpaca's maximum bars per page
_FOLLOWING_SESSION_SEARCH_DAYS = 15
_RFC3339_UTC = "%Y-%m-%dT%H:%M:%SZ"
_REASONS = (
    REASON_INSUFFICIENT_HISTORY,
    REASON_NO_HOLDINGS,
    REASON_PRICE_FETCH_FAILED,
    REASON_NO_CLASSIFIER_LABEL,
    REASON_WRITE_FAILED,
    REASON_AFTER_OPEN,
    REASON_UNEXPECTED_ERROR,
)


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


class _BatchFailed(Exception):
    def __init__(self, text: str):
        super().__init__(text)
        self.text = text


def _open_of(session: date) -> datetime:
    return datetime.combine(session, market_calendar.REAL_MARKET_OPEN_TIME, _ET)


def _in_session(start: datetime) -> bool:
    day = start.astimezone(_ET).date()
    if not market_calendar.is_trading_day(day):
        return False
    close = datetime.combine(day, market_calendar.session_close(day), _ET)
    return _open_of(day) <= start < close


def target_session(start: datetime) -> date | None:
    """First NYSE trading day whose 09:30 ET open is strictly after `start`; None when `start`
    lies inside a trading session (a run then labels nothing)."""
    if _in_session(start):
        return None
    day = start.astimezone(_ET).date()
    for _ in range(_FOLLOWING_SESSION_SEARCH_DAYS):
        if market_calendar.is_trading_day(day) and _open_of(day) > start:
            return day
        day += timedelta(days=1)
    raise ValueError("no trading day found after the start instant")


def window_sessions(session: date) -> list:
    """The 30 NYSE sessions strictly before `session` plus the one before them, oldest first."""
    found: list = []
    day = session - timedelta(days=1)
    while len(found) < REGIME_WINDOW_MAX_LOOKBACK_SESSIONS + 1:
        if market_calendar.is_trading_day(day):
            found.append(day)
        day -= timedelta(days=1)
    return found[::-1]


def _retry_delay(resp, backoff: float) -> float:
    header = resp.headers.get("Retry-After") if resp is not None else None
    try:
        return min(max(float(int(str(header).strip())), 0.0), RETRY_AFTER_CAP_SECONDS)
    except ValueError:
        return min(backoff, RETRY_AFTER_CAP_SECONDS)


def _get_page(url: str, params: dict):
    """One bars page with bounded retries: (response, None) on a 200, else (None, failure text)."""
    backoff = _BACKOFF_START_SECONDS
    failure = None
    for attempt in range(MAX_ATTEMPTS):
        began = timing_log.clock()
        resp = None
        try:
            resp = requests.get(
                url,
                headers=synthetic_history.get_alpaca_headers(),
                params=params,
                timeout=REQUEST_TIMEOUT_SECONDS,
                allow_redirects=False,
            )
            elapsed = int((timing_log.clock() - began) * 1000)
            timing_log.emit("alpaca", "GET", _BARS_PATH, status=resp.status_code, ms=elapsed)
            if resp.status_code == 200:
                return resp, None
            failure = str(resp.status_code)
            if resp.status_code not in _RETRYABLE_STATUSES:
                return None, failure
        except requests.RequestException as exc:
            elapsed = int((timing_log.clock() - began) * 1000)
            timing_log.emit("alpaca", "GET", _BARS_PATH, error=type(exc).__name__, ms=elapsed)
            failure = type(exc).__name__
        if attempt < MAX_ATTEMPTS - 1:
            time.sleep(_retry_delay(resp, backoff))
            backoff *= 2
    return None, failure


def _fetch_batch(tickers: list, start: date, end: datetime) -> dict:
    """{ticker: {ET bar date: close}} for one batch; raises _BatchFailed with the failure text."""
    url = f"{synthetic_history.ALPACA_BASE_URL}/stocks/bars"
    start_utc = datetime.combine(start, dtime(0, 0), _ET).astimezone(UTC)
    params = {
        "symbols": ",".join(tickers),
        "timeframe": "1Day",
        "adjustment": "split",
        "feed": "sip",
        "limit": _BAR_LIMIT,
        "start": start_utc.strftime(_RFC3339_UTC),
        "end": end.astimezone(UTC).strftime(_RFC3339_UTC),
    }
    closes: dict = {}
    first = True
    while True:
        if not first:
            time.sleep(_PAGE_PAUSE_SECONDS)
        first = False
        resp, failure = _get_page(url, params)
        if resp is None:
            raise _BatchFailed(failure)
        try:
            body = resp.json()
            for ticker, bars in (body.get("bars") or {}).items():
                table = closes.setdefault(ticker, {})
                for bar in bars or []:
                    stamp = datetime.fromisoformat(bar["t"].replace("Z", "+00:00"))
                    table[stamp.astimezone(_ET).date()] = bar["c"]
            token = body.get("next_page_token")
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise _BatchFailed(type(exc).__name__) from exc
        if not token:
            return closes
        params = {**params, "page_token": token}


def fetch_sip_daily_closes(tickers, start: date, end: datetime) -> SipCloses:
    """Split-adjusted SIP daily closes, batched; a failed batch fails every ticker in it."""
    unique = sorted(set(tickers))
    closes: dict = {}
    failed: dict = {}
    for i in range(0, len(unique), SIP_BATCH_SIZE):
        batch = unique[i : i + SIP_BATCH_SIZE]
        try:
            closes.update(_fetch_batch(batch, start, end))
        except _BatchFailed as exc:
            failed.update(dict.fromkeys(batch, exc.text))
    return SipCloses(closes=closes, failed=failed)


def _valid_close(value) -> bool:
    return isinstance(value, int | float) and math.isfinite(value) and value > 0


def _session_return(holdings, closes, current: date, previous: date) -> float:
    total = 0.0
    for holding in holdings:
        table = closes[holding["ticker"]]
        now, before = table[current], table[previous]
        if not (_valid_close(now) and _valid_close(before)):
            return math.nan
        total += holding["allocation"] * (now / before - 1.0)
    return total


def compute_label(holdings, closes, session: date, newest_eligible: date) -> LabelComputation:
    """Label from the 20 most recent computable sessions among the 30 before `session`."""
    sessions = window_sessions(session)
    computable = []
    for previous, current in zip(sessions, sessions[1:], strict=False):
        if current > newest_eligible:
            continue
        if all(
            current in closes.get(h["ticker"], {}) and previous in closes.get(h["ticker"], {})
            for h in holdings
        ):
            computable.append((current, _session_return(holdings, closes, current, previous)))
    recent = computable[-REGIME_WINDOW_SESSIONS:]
    if len(recent) < REGIME_WINDOW_SESSIONS:
        return LabelComputation(None, REASON_INSUFFICIENT_HISTORY, None, None, 0)
    series = tuple(value for _, value in recent)
    label = regime_classifier.classify_regime(list(series))
    return LabelComputation(
        label,
        None if label is not None else REASON_NO_CLASSIFIER_LABEL,
        recent[0][0],
        recent[-1][0],
        len(recent),
        series,
    )


def _holdings_of(entry) -> list:
    """Validated holdings [{ticker, allocation}] of a stored entry; raises on a malformed one."""
    raw = entry.get("current_holdings")
    if not raw:
        return []
    return [{"ticker": str(h["ticker"]), "allocation": float(h["allocation"])} for h in raw]


def _outcome(sid, name, reason=None, detail=None, label=None, comp=None) -> SymphonyOutcome:
    return SymphonyOutcome(
        sid,
        name,
        label,
        reason,
        comp.window_first if comp else None,
        comp.window_last if comp else None,
        comp.window_count if comp else 0,
        detail,
    )


def _log_symphony(session: date, o: SymphonyOutcome) -> None:
    # repr() escapes any newline in a display name, so a name cannot forge a log line.
    line = f"regime_label symphony={o.symphony_id} name={o.name!r} session={session.isoformat()}"
    if o.reason is None:
        line += (
            f" label={o.label} window={o.window_first.isoformat()}..{o.window_last.isoformat()}"
            f" sessions={o.window_count}"
        )
    else:
        line += f" reason={o.reason}"
        if o.detail:
            line += f" {'status' if str(o.detail).isdigit() else 'error'}={o.detail}"
    logger.info("%s", line)


def _log_summary(session: date, outcomes, failure: str | None) -> None:
    counts = Counter(o.reason for o in outcomes if o.reason)
    line = (
        f"regime_label summary session={session.isoformat()} seen={len(outcomes)}"
        f" labelled={sum(1 for o in outcomes if o.reason is None)}"
    )
    for reason in _REASONS:
        if counts[reason]:
            line += f" {reason}={counts[reason]}"
    if failure:
        line += f" failed={failure}"
    logger.info("%s", line)


def _label_and_store(sid, name, holdings, sip, session, newest_eligible, clock):
    failed = sorted(h["ticker"] for h in holdings if h["ticker"] in sip.failed)
    if failed:
        return _outcome(sid, name, REASON_PRICE_FETCH_FAILED, sip.failed[failed[0]])
    comp = compute_label(holdings, sip.closes, session, newest_eligible)
    if comp.label is None:
        return _outcome(sid, name, comp.reason, comp=comp)
    if clock().astimezone(_ET) >= _open_of(session):
        return _outcome(sid, name, REASON_AFTER_OPEN, comp=comp)
    try:
        database.save_regime_label(sid, comp.label, session.isoformat())
    except Exception as exc:
        return _outcome(sid, name, REASON_WRITE_FAILED, type(exc).__name__, comp=comp)
    return _outcome(sid, name, label=comp.label, comp=comp)


def run_regime_label_job(load_symphonies, *, clock=None) -> RegimeLabelRun:
    """Label every stored symphony for the session whose open follows this run.

    One symphony's failure never stops the others; logs one line per symphony and one summary.
    """
    clock = clock or (lambda: datetime.now(UTC))
    started = clock()
    session = target_session(started)
    if session is None:
        logger.info("regime_label skipped reason=session_in_progress")
        return RegimeLabelRun(None)
    try:
        entries = load_symphonies()
    except Exception as exc:
        failure = type(exc).__name__
        _log_summary(session, (), failure)
        return RegimeLabelRun(session, (), failure)

    prepared: dict = {}
    outcomes: dict = {}
    for sid, entry in entries.items():
        name = entry.get("name") if isinstance(entry, dict) else None
        try:
            holdings = _holdings_of(entry)
        except Exception as exc:
            outcomes[sid] = _outcome(sid, name, REASON_UNEXPECTED_ERROR, type(exc).__name__)
            continue
        if holdings:
            prepared[sid] = (name, holdings)
        else:
            outcomes[sid] = _outcome(sid, name, REASON_NO_HOLDINGS)

    end = started - SIP_END_LAG
    newest_eligible = market_calendar.most_recent_completed_session(end) or date.min
    tickers = {h["ticker"] for _, holdings in prepared.values() for h in holdings}
    sip = SipCloses()
    if tickers:
        try:
            sip = fetch_sip_daily_closes(tickers, window_sessions(session)[0], end)
        except Exception as exc:
            sip = SipCloses(failed=dict.fromkeys(tickers, type(exc).__name__))

    for sid, (name, holdings) in prepared.items():
        try:
            outcomes[sid] = _label_and_store(
                sid, name, holdings, sip, session, newest_eligible, clock
            )
        except Exception as exc:
            outcomes[sid] = _outcome(sid, name, REASON_UNEXPECTED_ERROR, type(exc).__name__)

    ordered = tuple(outcomes[sid] for sid in entries)
    for outcome in ordered:
        _log_symphony(session, outcome)
    _log_summary(session, ordered, None)
    return RegimeLabelRun(session, ordered)
