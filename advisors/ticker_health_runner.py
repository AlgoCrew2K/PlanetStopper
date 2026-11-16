"""Dead-ticker check, runner: reads live trees from Composer, asks Alpaca about each ticker.

One run reads the symphony roster of each configured account, each symphony's current tree
(Composer, read-only, paced), then one Alpaca asset record per distinct ticker, stores a single
run row and posts the newly dead pairs to Discord once. Advisory only: no order, no Composer
write, no credential or response body in a log line or a stored row.
"""

import logging
import time
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import NamedTuple
from zoneinfo import ZoneInfo

import requests

import database
import market_calendar
import timing_log
from advisors import composer_portfolio_client as composer
from advisors import ticker_health as th
from advisors.universe_provider import ALPACA_TRADING_BASE_URL

logger = logging.getLogger("alphabot.ticker_health")

SCHEMA_VERSION = 1
RUN_SUBJECT_TYPE = "ticker_health_run"
ANNOUNCEMENT_SUBJECT_TYPE = "ticker_health_announcement"
ANNOUNCED_VERDICT = "announced"

# Seconds an Alpaca or Discord request may take before it is given up.
REQUEST_TIMEOUT_SECONDS = 10
# Pause after every Alpaca attempt, seconds: well under Alpaca's request limit for one run.
ALPACA_SPACING_SECONDS = 0.5
# Attempts per ticker on a 429, a 5xx or a transport error (one retry).
ALPACA_ATTEMPTS = 2
# Wait before the retry when Alpaca names no usable Retry-After, and the longest wait honoured.
ALPACA_RETRY_DEFAULT_SECONDS = 2
ALPACA_RETRY_CAP_SECONDS = 30
# Consecutive tickers ending unreachable, unavailable or rate limited after which the rest are
# not asked.
ALPACA_FAILURE_STREAK_LIMIT = 5
# Most seconds one run spends waiting out Alpaca rate limits (429 retries, whose wait Alpaca
# chooses). Without it a run whose every ticker is rate limited waits the retry cap per ticker
# for the whole list, long past the night window; once spent, the tickers not yet asked read as
# rate limited. The short fixed wait after a transport error or a 5xx is not counted.
ALPACA_RETRY_BUDGET_SECONDS = 120
# Composer gate: poll interval and the longest wait for another Composer job, seconds.
GATE_POLL_SECONDS = 15
GATE_WAIT_LIMIT_SECONDS = 2700
# Pause between two Discord messages of one run, seconds.
DISCORD_SPACING_SECONDS = 1.5
# Newest announcement rows read at the start of a run.
ANNOUNCEMENT_READ_LIMIT = 200
# Newest run rows scanned for a completed run when the newest run did not complete.
RUN_SCAN_LIMIT = 365

_ET = ZoneInfo("America/New_York")
_HTTP_UNAUTHORIZED, _HTTP_FORBIDDEN, _HTTP_RATE_LIMIT = 401, 403, 429
_HTTP_OK, _HTTP_SERVER_ERROR = 200, 500
_MS_PER_S = 1000

_ACCOUNT_REASONS = {
    "unavailable": "Composer unavailable",
    "refused": "Composer refused the request",
    "not_found": "account not found",
    "rejected": "Composer rejected the request",
    "unreadable": "Composer response unreadable",
    "rate_limited": "Composer rate limit",
}
_SYMPHONY_REASONS = {
    **_ACCOUNT_REASONS,
    "not_found": "definition not found",
    "unreadable": "definition unreadable",
}
_TICKER_FORM_REASON = "ticker form not checked"
_NOT_COVERED_REASON = "asset class not covered by the check"
_ID_NOT_USABLE_REASON = "symphony id not usable"
_GENERIC_ROSTER_REASON = "no account's symphony list could be read"


class RunOutcome(NamedTuple):
    run_id: str
    status: str
    completed: bool


class LatestRuns(NamedTuple):
    latest: dict | None
    latest_completed: dict | None


@dataclass(frozen=True)
class _Stop:
    """Why Composer reads ended: the reason per unread symphony, and the reason if none began."""

    symphony_reason: str
    skip_reason: str


_STOP_MARKET = _Stop("market opened", "market open")
_STOP_OTHER_JOB = _Stop(
    "another Composer job was running", "another Composer job was still running"
)
_STOP_RATE_LIMIT = _Stop("Composer rate limit", "Composer rate limit")
_STOP_CANNOT_TELL = _Stop(
    "could not tell whether another Composer job was running",
    "could not tell whether another Composer job was running",
)


class _SkipRun(Exception):  # noqa: N818 - control flow inside this module only
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class _Symphony:
    symphony_id: str
    name: str
    holdings_fields: list = field(default_factory=list)


@dataclass(frozen=True)
class _Ended:
    status: str
    reason: str | None = None
    error_type: str | None = None
    body: dict = field(default_factory=dict)


class _Run:
    def __init__(self, trigger: str, other_reader_running: Callable[[], bool], clock):
        self.run_id = uuid.uuid4().hex
        self.trigger = trigger
        self.other_reader_running = other_reader_running
        self.clock = clock
        self.started = clock()
        self.ended: datetime | None = None
        self.composer_requests = 0
        self.alpaca_requests = 0
        self.alpaca_waited = 0
        self.stop: _Stop | None = None

    def ended_at(self) -> str:
        if self.ended is None:
            self.ended = self.clock()
        return self.ended.isoformat()

    def market_is_open(self) -> bool:
        return market_calendar.get_market_state(self.clock().astimezone(_ET)) == "open"

    def _other_reader_running(self) -> bool:
        """Whether another Composer job runs; a check that cannot answer stops the reads
        (waiting out the whole period for a job that may not exist would be a guess)."""
        try:
            return self.other_reader_running()
        except Exception as exc:  # noqa: BLE001 - the cause is stored, never the message
            logger.warning("wait check failed: %s", type(exc).__name__)
            self.stop = _STOP_CANNOT_TELL
            return True

    def gate(self) -> _Stop | None:
        """The stop that applies before the next Composer request, waiting for another job."""
        while self.stop is None:
            if self.market_is_open():
                self.stop = _STOP_MARKET
            elif not self._other_reader_running():
                return None
            elif self.stop is not None:  # the wait check could not answer: no poll, no overwrite
                break
            elif (self.clock() - self.started).total_seconds() >= GATE_WAIT_LIMIT_SECONDS:
                self.stop = _STOP_OTHER_JOB
            else:
                time.sleep(GATE_POLL_SECONDS)
        return self.stop


# ---------------------------------------------------------------------------
# Stored runs
# ---------------------------------------------------------------------------


def _record_of(row: dict) -> dict | None:
    record = row.get("raw_response")
    return record if isinstance(record, dict) else None


def read_latest_runs(*, scan_limit: int = RUN_SCAN_LIMIT) -> LatestRuns:
    """The newest run record and the newest completed one; raises on a database error."""
    newest = database.get_advisor_observations_for_role(th.RUN_ROLE, 1)
    latest = _record_of(newest[0]) if newest else None
    if latest is None or latest.get("completed") is True:
        return LatestRuns(latest, latest)
    scanned = database.get_advisor_observations_for_role(th.RUN_ROLE, scan_limit)
    records = (_record_of(row) for row in scanned)
    return LatestRuns(latest, next((r for r in records if r and r.get("completed") is True), None))


def _announced_pairs(previous_run_id: str | None) -> set[tuple[str, str]]:
    """Pairs announced by the previous completed run; older announcements never count."""
    rows = database.get_advisor_observations_for_role(th.ANNOUNCEMENT_ROLE, ANNOUNCEMENT_READ_LIMIT)
    pairs = set()
    for row in rows:
        record = _record_of(row) or {}
        if record.get("run_id") == previous_run_id:
            pairs.update((p[0], p[1]) for p in record.get("pairs") or [])
    return pairs


def _no_answer_reason(verdicts: dict) -> str:
    """Why Alpaca never answered: a refusal first, else the first failure in ask order."""
    reasons = [v.reason for v in verdicts.values()]
    refused = th.classify_asset_answer(401, None).reason
    unreachable = th.classify_asset_failure().reason
    if refused in reasons:
        return refused
    return unreachable if all(r == unreachable for r in reasons) else reasons[0]


# ---------------------------------------------------------------------------
# Composer reads
# ---------------------------------------------------------------------------


def _read_rosters(run: _Run, account_ids, labels) -> tuple[dict, list, int]:
    symphonies: dict[str, _Symphony] = {}
    failures: list[dict] = []
    rosters_read = 0
    for position, account in enumerate(account_ids, start=1):
        label = labels.get(account)
        if not _has_text(label):
            label = f"account {position}"  # never the Composer id, which must not be shown
        stop = run.gate()
        if stop and run.composer_requests == 0 and stop is not _STOP_RATE_LIMIT:
            raise _SkipRun(stop.skip_reason)
        if stop:
            failures.append({"label": label, "reason": stop.symphony_reason})
            continue
        if composer.is_plain_id(account):
            run.composer_requests += 1
        read = composer.read_symphony_statistics(account, None)
        if read.failure:
            if read.failure == "rate_limited":
                run.stop = _STOP_RATE_LIMIT
            reason = _ACCOUNT_REASONS.get(read.failure, _ACCOUNT_REASONS["rejected"])
            failures.append({"label": label, "reason": reason})
            continue
        rosters_read += 1
        for entry in read.symphonies:
            known = symphonies.setdefault(
                entry["id"], _Symphony(entry["id"], th.display_name(entry))
            )
            known.holdings_fields.append(entry.get("holdings"))
    return symphonies, failures, rosters_read


def _read_definition(run: _Run, symphony: _Symphony) -> tuple[dict | None, str | None]:
    """The tree of one symphony, or the reason it was not read."""
    if not composer.is_plain_id(symphony.symphony_id):
        return None, _ID_NOT_USABLE_REASON
    stop = run.gate()
    if stop:
        return None, stop.symphony_reason
    run.composer_requests += 1
    read = composer.read_symphony_definition(symphony.symphony_id, None)
    if read.failure:
        if read.failure == "rate_limited":
            run.stop = _STOP_RATE_LIMIT
        return None, _SYMPHONY_REASONS.get(read.failure, _SYMPHONY_REASONS["rejected"])
    return read.definition, None


# ---------------------------------------------------------------------------
# Alpaca reads
# ---------------------------------------------------------------------------


def _retry_wait(retry_after) -> int:
    try:
        wait = int(retry_after)
    except (TypeError, ValueError):
        return ALPACA_RETRY_DEFAULT_SECONDS
    return max(0, min(ALPACA_RETRY_CAP_SECONDS, wait))


def _alpaca_attempt(run: _Run, ticker: str, headers: dict):
    """One asset GET: (status or None on a transport error, parsed body, Retry-After)."""
    run.alpaca_requests += 1
    began = timing_log.clock()
    status = None
    try:
        response = requests.get(
            f"{ALPACA_TRADING_BASE_URL}/v2/assets/{ticker}",
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
        status = response.status_code
    except Exception as exc:  # noqa: BLE001 - a transport error is a not-checked ticker
        logger.warning("alpaca asset read failed: %s", type(exc).__name__)
        return None, None, None
    finally:
        elapsed_ms = int((timing_log.clock() - began) * _MS_PER_S)
        timing_log.emit("alpaca", "GET", "/v2/assets", status=status or "error", ms=elapsed_ms)
        time.sleep(ALPACA_SPACING_SECONDS)
    if status != _HTTP_OK:
        logger.warning("alpaca asset read: HTTP %d", status)
        return status, None, response.headers.get("Retry-After")
    try:
        return status, response.json(), None
    except ValueError:
        return status, None, None


def _read_asset(run: _Run, ticker: str, headers: dict) -> tuple[th.TickerVerdict, int | None, bool]:
    """The verdict, the last HTTP status, and whether the run must stop asking (the market
    opened, or the retry-wait budget is spent)."""
    for attempt in range(1, ALPACA_ATTEMPTS + 1):
        if run.market_is_open():
            return th.TickerVerdict("not_checked", _STOP_MARKET.symphony_reason), None, True
        status, body, retry_after = _alpaca_attempt(run, ticker, headers)
        verdict = (
            th.classify_asset_failure()
            if status is None
            else th.classify_asset_answer(status, body)
        )
        retryable = status is None or status == _HTTP_RATE_LIMIT or status >= _HTTP_SERVER_ERROR
        if not retryable or attempt == ALPACA_ATTEMPTS:
            return verdict, status, False
        wait = _retry_wait(retry_after)
        if status == _HTTP_RATE_LIMIT:
            if run.alpaca_waited + wait > ALPACA_RETRY_BUDGET_SECONDS:
                return verdict, status, True
            run.alpaca_waited += wait
        time.sleep(wait)
    raise AssertionError("unreachable")  # pragma: no cover


def _ask_alpaca(run: _Run, tickers: list[str], key: str, secret: str) -> dict:
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    verdicts: dict[str, th.TickerVerdict] = {}
    stop_reason = None
    streak = 0
    for ticker in tickers:
        if stop_reason:
            verdicts[ticker] = th.TickerVerdict("not_checked", stop_reason)
            continue
        verdict, status, halt = _read_asset(run, ticker, headers)
        verdicts[ticker] = verdict
        if halt or status in (_HTTP_UNAUTHORIZED, _HTTP_FORBIDDEN):
            stop_reason = verdict.reason
        elif status is None or status == _HTTP_RATE_LIMIT or status >= _HTTP_SERVER_ERROR:
            streak += 1
            if streak >= ALPACA_FAILURE_STREAK_LIMIT:
                stop_reason = verdict.reason
        else:
            streak = 0
    return verdicts


# ---------------------------------------------------------------------------
# One run
# ---------------------------------------------------------------------------


def _has_text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _shared_roster_reason(failures: list[dict]) -> str:
    reasons = {f["reason"] for f in failures}
    return reasons.pop() if len(reasons) == 1 else _GENERIC_ROSTER_REASON


def _symphony_entry(symphony: _Symphony, reason: str) -> dict:
    return {
        "symphony_id": symphony.symphony_id,
        "display_name": symphony.name,
        "reason": reason,
    }


def _ticker_entries(symphony: _Symphony, uses: dict, verdicts: dict) -> list[dict]:
    named = [
        {**_symphony_entry(symphony, _TICKER_FORM_REASON), "ticker": ticker}
        for ticker in sorted(uses)
        if not th.is_plain_ticker(ticker)
    ]
    unanswered = Counter(
        verdicts[ticker].reason
        for ticker in sorted(uses)
        if th.is_plain_ticker(ticker) and verdicts[ticker].state == "not_checked"
    )
    counted = [{**_symphony_entry(symphony, r), "count": n} for r, n in unanswered.items()]
    return named + counted


def _read_trees(run: _Run, symphonies: dict) -> tuple[dict, list[dict]]:
    """Judged symphonies (id to tree uses) and the not-checked entries, in roster order."""
    judged: dict[str, dict] = {}
    not_checked: list[dict] = []
    for symphony in symphonies.values():
        tree, reason = _read_definition(run, symphony)
        if reason is None and not th.asset_class_covered(tree):
            reason = _NOT_COVERED_REASON
        if reason is not None:
            not_checked.append(_symphony_entry(symphony, reason))
            continue
        judged[symphony.symphony_id] = {
            "display_name": symphony.name,
            "holdings_fields": symphony.holdings_fields,
            "uses": th.tree_ticker_uses(tree),
        }
    return judged, not_checked


_WARNING_FIELDS = frozenset(
    {
        "symphony_id",
        "display_name",
        "ticker",
        "reason",
        "asset_nodes",
        "conditions",
        "held",
        "found_at",
        "announced",
    }
)


def _stored_row_id(run_id) -> int | None:
    rows = database.get_advisor_observations_for_role(th.RUN_ROLE, RUN_SCAN_LIMIT)
    return next((r.get("id") for r in rows if r.get("subject_id") == run_id), None)


def _previous_warnings(latest_completed: dict | None, announced: set) -> list[dict]:
    stored = (latest_completed or {}).get("warnings") or []
    warnings = [dict(w) for w in stored if isinstance(w, dict) and w.keys() >= _WARNING_FIELDS]
    if len(warnings) != len(stored):
        # Only the row id is named: the unreadable data itself is never logged.
        logger.warning(
            "stored ticker check warning skipped as unreadable: row %s",
            _stored_row_id((latest_completed or {}).get("run_id")),
        )
    for warning in warnings:
        if (warning["symphony_id"], warning["ticker"]) in announced:
            warning["announced"] = True
    return warnings


def _evaluate(run: _Run, account_ids, labels, key, secret) -> _Ended:
    latest_completed = read_latest_runs().latest_completed
    announced = _announced_pairs((latest_completed or {}).get("run_id"))
    if not account_ids:
        return _Ended("unavailable", reason="no Composer account configured")
    if not (_has_text(key) and _has_text(secret)):
        return _Ended("unavailable", reason="Alpaca credentials not configured")
    symphonies, failures, rosters_read = _read_rosters(run, account_ids, labels)
    if not rosters_read:
        return _Ended("unavailable", reason=_shared_roster_reason(failures))
    judged, unread = _read_trees(run, symphonies)
    tickers = sorted(
        {t for entry in judged.values() for t in entry["uses"] if th.is_plain_ticker(t)}
    )
    verdicts = _ask_alpaca(run, tickers, key, secret) if tickers else {}
    answered = sum(v.state != "not_checked" for v in verdicts.values())
    if not th.is_completed(
        rosters_read=rosters_read,
        symphonies_listed=len(symphonies),
        symphonies_reached=len(judged) + len(unread),
        tickers_asked=len(tickers),
        tickers_answered=answered,
    ):
        return _Ended("unavailable", reason=_no_answer_reason(verdicts))
    for entry in judged.values():
        entry["verdicts"] = {t: verdicts[t] for t in entry["uses"] if t in verdicts}
    warnings = th.carry_warnings(
        previous_warnings=_previous_warnings(latest_completed, announced),
        judged=judged,
        unjudged_symphonies={e["symphony_id"] for e in unread},
        listed_ids=set(symphonies),
        every_roster_read=not failures,
        ended_at=run.ended_at(),
    )
    not_checked = {
        "accounts": failures,
        "symphonies": unread,
        "tickers": [
            e
            for sid, entry in judged.items()
            for e in _ticker_entries(symphonies[sid], entry["uses"], entry["verdicts"])
        ],
    }
    counts = {
        "accounts_read": rosters_read,
        "symphonies_listed": len(symphonies),
        "symphonies_judged": len(judged),
        "tickers_asked": len(tickers),
        "tickers_answered": answered,
        "dead_pairs": len(warnings),
        "composer_requests": run.composer_requests,
        "alpaca_requests": run.alpaca_requests,
    }
    return _Ended(
        "completed", body={"warnings": warnings, "not_checked": not_checked, "counts": counts}
    )


def _run_record(run: _Run, ended: _Ended) -> dict:
    return {
        "schema": SCHEMA_VERSION,
        "run_id": run.run_id,
        "trigger": run.trigger,
        "started_at": run.started.isoformat(),
        "ended_at": run.ended_at(),
        "status": ended.status,
        "completed": ended.status == "completed",
        "reason": ended.reason,
        "error_type": ended.error_type,
        **ended.body,
    }


# ---------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------


def _store_announcement(run_id: str, pairs: list[dict]) -> None:
    database.insert_advisor_observation(
        advisor_role=th.ANNOUNCEMENT_ROLE,
        subject_type=ANNOUNCEMENT_SUBJECT_TYPE,
        subject_id=run_id,
        verdict=ANNOUNCED_VERDICT,
        raw_response={"run_id": run_id, "pairs": [[p["symphony_id"], p["ticker"]] for p in pairs]},
    )


def _post_new_pairs(run_id: str, record: dict, webhook_url: str) -> tuple[int, int]:
    """Post each pair found dead in this run and not announced before; stop at the first
    failure. Returns the messages posted and the messages that failed."""
    pairs = sorted(
        (
            w
            for w in record["warnings"]
            if w["found_at"] == record["ended_at"] and not w["announced"]
        ),
        key=lambda w: (w["display_name"].casefold(), w["symphony_id"], w["ticker"]),
    )
    taken = 0
    posted = 0
    for index, message in enumerate(th.discord_messages(pairs)):
        if index:
            time.sleep(DISCORD_SPACING_SECONDS)
        # One line per pair follows the lead, so the line count says which pairs this message holds.
        in_message = pairs[taken : taken + message.count("\n")]
        taken += len(in_message)
        try:
            response = requests.post(
                webhook_url,
                json={"content": message, "allowed_mentions": {"parse": []}},
                timeout=REQUEST_TIMEOUT_SECONDS,
                allow_redirects=False,
            )
            if not 200 <= response.status_code < 300:
                logger.warning("discord post failed: HTTP %d", response.status_code)
                return posted, 1
            _store_announcement(run_id, in_message)
            posted += 1
        except Exception as exc:  # noqa: BLE001 - posting never fails the stored run
            logger.warning("discord post or announcement failed: %s", type(exc).__name__)
            return posted, 1
    return posted, 0


def run_ticker_health_check(
    *,
    account_ids,
    account_labels,
    alpaca_key,
    alpaca_secret,
    webhook_url,
    trigger,
    other_reader_running,
    clock=None,
) -> RunOutcome:
    """One check: never raises an ``Exception``; a ``BaseException`` leaves no run row."""
    run = _Run(trigger, other_reader_running, clock or (lambda: datetime.now(UTC)))
    logger.info("ticker check started: trigger=%s run_id=%s", trigger, run.run_id)
    try:
        ended = _evaluate(run, account_ids, account_labels, alpaca_key, alpaca_secret)
    except _SkipRun as skip:
        ended = _Ended("skipped", reason=skip.reason)
    except Exception as exc:  # noqa: BLE001 - an unexpected fault is a stored failed run
        logger.warning("ticker check failed: %s", type(exc).__name__)
        ended = _Ended("failed", error_type=type(exc).__name__)
    record = _run_record(run, ended)
    try:
        database.insert_advisor_observation(
            advisor_role=th.RUN_ROLE,
            subject_type=RUN_SUBJECT_TYPE,
            subject_id=run.run_id,
            verdict=ended.status,
            raw_response=record,
        )
    except Exception as exc:  # noqa: BLE001 - the outcome is still reported to the caller
        logger.warning("ticker check run row not stored: %s", type(exc).__name__)
        return RunOutcome(run.run_id, "failed", False)
    posted, failed = 0, 0
    if record["completed"] and webhook_url:
        posted, failed = _post_new_pairs(run.run_id, record, webhook_url)
    logger.info("ticker check ended: status=%s posted=%d failed=%d", ended.status, posted, failed)
    return RunOutcome(run.run_id, ended.status, record["completed"])
