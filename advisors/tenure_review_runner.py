"""Run a symphony tenure review: Composer reads, shadow fallback, backtests, then the review.

Reads and counterfactual backtests only; writes nothing. Never raises. The first reply
answered 429 ends the run's Composer requests; every item that needed a later request reads
as rate limited.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

import analytics
import database
import market_calendar
from advisors import composer_backtest_client, composer_portfolio_client, tenure_review
from advisors.composer_backtest_client import BacktestResult
from advisors.tenure_review import HoldingsInput, ReviewResult

logger = logging.getLogger(__name__)

_ACCOUNT_ENV_KEYS = ("ACCOUNT_INDIVIDUAL", "ACCOUNT_ROTH", "ACCOUNT_TRAD")
_ACCOUNT_LABEL_SUFFIX = " account"

# Error text of a backtest that was not requested because an earlier request was answered 429;
# the review module reads the leading "HTTP 429" as the rate-limit reason.
_RATE_LIMITED_BACKTEST_ERROR = "HTTP 429 rate limit: no request made after an earlier 429"
_BACKTEST_FAILED_ERROR = "backtest failed unexpectedly"

# The review module's reason for a symphony with no holding day in the window.
_NO_HOLDINGS_REASON = "no holdings for this symphony in the window"
_SHARE_FIGURES = ("peak_share_with", "peak_share_without")


@dataclass(frozen=True)
class RunOutcome:
    """The result of one run: whether it completed, the review, or why it did not."""

    completed: bool
    result: ReviewResult | None = None
    failure_reason: str | None = None


@dataclass(frozen=True)
class _MissingAccount:
    """An account whose holdings were not read in full: its failure class and how much was read."""

    account_id: str
    failure: str
    partly_read: bool


@dataclass
class _RunState:
    """What one run learns as it goes: whether Composer has answered 429."""

    rate_limited: bool = False


def _configured_accounts(env: Mapping) -> list[tuple[str, str]]:
    """(configuration key, account id) for each configured account, in the engine's order."""
    accounts = []
    for key in _ACCOUNT_ENV_KEYS:
        value = (env.get(key) or "").strip()
        if value:
            accounts.append((key, value))
    return accounts


def configured_account_ids(env: Mapping) -> list[str]:
    """The configured Composer account ids, in the engine's account order."""
    return [account_id for _, account_id in _configured_accounts(env)]


def configured_account_labels(env: Mapping) -> dict[str, str]:
    """Account id to label for the configured accounts, each named after its configuration key."""
    return {
        account_id: key.removeprefix("ACCOUNT_").capitalize() + _ACCOUNT_LABEL_SUFFIX
        for key, account_id in _configured_accounts(env)
    }


def _shadow_returns(symphony_id: str) -> dict | None:
    series = analytics.get_symphony_bot_and_held_daily_returns(symphony_id, days=None)
    if not series:
        return None
    return dict(zip(series[0], series[1], strict=False))


def _read_roster(account_ids: Sequence[str], spacing: float):
    """Returns (roster, failure_reason); roster is a list of (account, entry)."""
    roster = []
    for account in account_ids:
        stats = composer_portfolio_client.read_symphony_statistics(account, spacing)
        if stats.failure == "unreadable":
            return None, "Composer statistics unreadable"
        if stats.failure:
            return None, "Composer statistics unavailable"
        roster.extend((account, entry) for entry in stats.symphonies)
    return roster, None


def _display_name(entry: dict) -> str:
    name = entry.get("name")
    trimmed = name.strip() if isinstance(name, str) else ""
    return trimmed or entry["id"]


def _state_entries() -> dict:
    """The engine's per-symphony state, or an empty mapping when it cannot be read."""
    try:
        state = database.load_state()
    except Exception as exc:  # noqa: BLE001 - tickers are optional evidence
        logger.warning("tenure review state read failed: %s", type(exc).__name__)
        return {}
    return state if isinstance(state, dict) else {}


def _current_tickers(state: dict, symphony_id: str) -> list[str] | None:
    entry = state.get(symphony_id)
    held = entry.get("logic_holdings") if isinstance(entry, dict) else None
    return list(held) if isinstance(held, dict) and held else None


def _read_series(
    account: str, entry: dict, state: dict, run: _RunState, spacing: float
) -> tenure_review.SymphonyInput:
    sym = tenure_review.SymphonyInput(
        symphony_id=entry["id"],
        display_name=_display_name(entry),
        invested_since=entry.get("invested_since"),
        current_tickers=_current_tickers(state, entry["id"]),
    )
    if run.rate_limited:
        sym.series_failure = "rate_limited"
    else:
        series = composer_portfolio_client.read_symphony_value_series(
            account, sym.symphony_id, spacing
        )
        if series.failure is None:
            sym.composer_values = series.values
        else:
            sym.series_failure = series.failure
            run.rate_limited = series.failure == "rate_limited"
    sym.shadow_returns = _shadow_returns(sym.symphony_id)
    return sym


def _attach_backtest(
    sym: tenure_review.SymphonyInput, window: dict, run: _RunState, spacing: float
) -> None:
    """Read the definition and run one dated backtest, isolating any failure to ``sym``."""
    if run.rate_limited:
        sym.backtest = BacktestResult(None, [], error=_RATE_LIMITED_BACKTEST_ERROR)
        return
    try:
        read = composer_portfolio_client.read_symphony_definition(sym.symphony_id, spacing)
        if read.failure == "rate_limited":
            run.rate_limited = True
            sym.backtest = BacktestResult(None, [], error=_RATE_LIMITED_BACKTEST_ERROR)
        elif read.failure or not read.definition:
            sym.definition_available = False
        else:
            try:
                sym.backtest = composer_backtest_client.run_backtest(
                    read.definition,
                    sym.symphony_id,
                    start_date=window["first"],
                    end_date=window["last"],
                    compact_body=True,
                    max_retries=1,
                    retry_on_rate_limit=False,
                    min_retry_wait_seconds=spacing,
                )
            finally:
                # The spacing holds whether or not the call returned.
                time.sleep(spacing)
            run.rate_limited = str(sym.backtest.error or "").startswith("HTTP 429")
    except Exception as exc:  # noqa: BLE001 - one symphony's failure is that symphony's item
        logger.warning("tenure review backtest step failed: %s", type(exc).__name__)
        sym.backtest = BacktestResult(None, [], error=_BACKTEST_FAILED_ERROR)


def _is_session(day: str) -> bool:
    try:
        return market_calendar.is_trading_day(date.fromisoformat(day))
    except ValueError:
        return False


def _read_holdings(
    account_ids: Sequence[str], earliest: date, as_of: date, run: _RunState, spacing: float
) -> tuple[HoldingsInput, list[_MissingAccount]]:
    """Returns (HoldingsInput, the accounts whose holdings were not read in full)."""
    days: dict = {}
    first_failure = None
    missing: list[_MissingAccount] = []
    requested_days = (as_of - earliest).days + 1
    for account in account_ids:
        if run.rate_limited:
            missing.append(_MissingAccount(account, "rate_limited", False))
            first_failure = first_failure or "rate_limited"
            continue
        read = composer_portfolio_client.read_daily_holdings(account, earliest, as_of, spacing)
        if read.failed_spans:
            failed_days = sum((s.end - s.start).days + 1 for s in read.failed_spans)
            missing.append(
                _MissingAccount(account, read.failed_spans[0].failure, failed_days < requested_days)
            )
            first_failure = first_failure or read.failed_spans[0].failure
            run.rate_limited = any(s.failure == "rate_limited" for s in read.failed_spans)
        for day, by_id in read.days.items():
            if _is_session(day):
                days.setdefault(day, {}).update(by_id)
    return HoldingsInput(days, None if days else first_failure), missing


def _class_reason(failure: str) -> str:
    """The review module's reason text for a failure class, taken from its public output."""
    return tenure_review.long_vix_cofiring(HoldingsInput({}, failure), "", [], {})["reason"]


def _missing_reason(missing: Sequence[_MissingAccount], label_of: Mapping[str, str]) -> str:
    """Each missing account's failure class text and how much of its holdings was read."""
    return "; ".join(
        f"{_class_reason(m.failure)}: holdings "
        f"{'partly read' if m.partly_read else 'not read'} for {label_of[m.account_id]}"
        for m in missing
    )


def _name_missing_accounts(
    result: ReviewResult,
    own_account: Mapping[str, str],
    missing: Sequence[_MissingAccount],
    label_of: Mapping[str, str],
) -> None:
    """Withhold the portfolio-wide shares, and explain an empty item, when an account is missing."""
    by_account = {m.account_id: m for m in missing}
    for entry in result.entries:
        item = entry.get("evidence", {}).get("long_vix")
        if not item:
            continue
        if item["status"] == "available":
            reason = _missing_reason(missing, label_of)
            for name in _SHARE_FIGURES:
                item["figures"][name] = {"value": None, "unit": "percent", "reason": reason}
        elif (
            item["reason"] == _NO_HOLDINGS_REASON
            and own_account.get(entry["symphony_id"]) in by_account
        ):
            reason = _missing_reason([by_account[own_account[entry["symphony_id"]]]], label_of)
            item["reason"] = reason
            for figure in item["figures"].values():
                figure["reason"] = reason


def run_tenure_review(
    *,
    account_ids: Sequence[str],
    threshold_raw: object,
    now_et: datetime,
    run_id: str,
    trigger: str = "on_demand",
    account_labels: Mapping[str, str] | None = None,
    read_spacing_seconds: float = composer_portfolio_client.COMPOSER_READ_SPACING_SECONDS,
) -> RunOutcome:
    """Review every live symphony of the given accounts. Never raises.

    ``account_labels`` names accounts in reasons (an unlabelled account reads ``account <n>``);
    ``read_spacing_seconds`` is the sleep after every Composer request.
    """
    try:
        if not account_ids:
            return RunOutcome(False, None, "no Composer account configured")
        threshold = tenure_review.parse_threshold(threshold_raw)
        as_of = tenure_review.as_of_trading_day(now_et)
        roster, reason = _read_roster(account_ids, read_spacing_seconds)
        if roster is None:
            return RunOutcome(False, None, reason)
        run = _RunState()
        state = _state_entries()
        symphonies = [
            _read_series(account, entry, state, run, read_spacing_seconds)
            for account, entry in roster
        ]
        firsts = []
        for sym in symphonies:
            window = tenure_review.qualifying_window(sym, as_of, threshold.value)
            if window is not None:
                firsts.append(window["first"])
                _attach_backtest(sym, window, run, read_spacing_seconds)
        holdings, missing = None, []
        if firsts:
            holdings, missing = _read_holdings(
                account_ids, date.fromisoformat(min(firsts)), as_of, run, read_spacing_seconds
            )
        inputs = tenure_review.ReviewInputs(
            run_id=run_id,
            trigger=trigger,
            started_at=now_et,
            as_of=as_of,
            threshold=threshold,
            symphonies=symphonies,
            holdings=holdings,
        )
        result = tenure_review.build_review(inputs)
        if missing and holdings.days:
            labels = account_labels or {}
            label_of = {
                account: labels.get(account) or f"account {n}"
                for n, account in enumerate(account_ids, start=1)
            }
            _name_missing_accounts(
                result, {e["id"]: account for account, e in roster}, missing, label_of
            )
        return RunOutcome(True, result, None)
    except Exception as exc:  # noqa: BLE001 - the runner never raises
        name = type(exc).__name__
        logger.warning("tenure review run failed: %s", name)
        return RunOutcome(False, None, f"tenure review failed: {name}")
