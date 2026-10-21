"""Run a symphony tenure review: Composer reads, shadow fallback, backtests, then the review.

Reads and counterfactual backtests only; writes nothing. Never raises. The first reply
answered 429 ends the run's Composer requests; every item that needed a later request reads
as rate limited.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date

import analytics
import database
import market_calendar
from advisors import composer_backtest_client, composer_portfolio_client, tenure_review
from advisors.composer_backtest_client import BacktestResult
from advisors.tenure_review import ReviewResult

logger = logging.getLogger(__name__)

_ACCOUNT_ENV_KEYS = ("ACCOUNT_INDIVIDUAL", "ACCOUNT_ROTH", "ACCOUNT_TRAD")

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


@dataclass
class _RunState:
    """What one run learns as it goes: whether Composer has answered 429."""

    rate_limited: bool = False


def configured_account_ids(env) -> list[str]:
    """The configured Composer account ids, in the engine's account order."""
    ids = []
    for key in _ACCOUNT_ENV_KEYS:
        value = (env.get(key) or "").strip()
        if value:
            ids.append(value)
    return ids


def _shadow_returns(symphony_id: str):
    series = analytics.get_symphony_bot_and_held_daily_returns(symphony_id, days=None)
    if not series:
        return None
    return dict(zip(series[0], series[1], strict=False))


def _read_roster(account_ids):
    """Returns (roster, failure_reason); roster is a list of (account, entry)."""
    roster = []
    for account in account_ids:
        stats = composer_portfolio_client.read_symphony_statistics(account)
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


def _read_series(account, entry, state: dict, run: _RunState) -> tenure_review.SymphonyInput:
    sym = tenure_review.SymphonyInput(
        symphony_id=entry["id"],
        display_name=_display_name(entry),
        invested_since=entry.get("invested_since"),
        current_tickers=_current_tickers(state, entry["id"]),
    )
    if run.rate_limited:
        sym.series_failure = "rate_limited"
    else:
        series = composer_portfolio_client.read_symphony_value_series(account, sym.symphony_id)
        if series.failure is None:
            sym.composer_values = series.values
        else:
            sym.series_failure = series.failure
            run.rate_limited = series.failure == "rate_limited"
    sym.shadow_returns = _shadow_returns(sym.symphony_id)
    return sym


def _attach_backtest(sym, window: dict, run: _RunState) -> None:
    """Read the definition and run one dated backtest, isolating any failure to ``sym``."""
    if run.rate_limited:
        sym.backtest = BacktestResult(None, [], error=_RATE_LIMITED_BACKTEST_ERROR)
        return
    try:
        read = composer_portfolio_client.read_symphony_definition(sym.symphony_id)
        if read.failure == "rate_limited":
            run.rate_limited = True
            sym.backtest = BacktestResult(None, [], error=_RATE_LIMITED_BACKTEST_ERROR)
        elif read.failure or not read.definition:
            sym.definition_available = False
        else:
            sym.backtest = composer_backtest_client.run_backtest(
                read.definition,
                sym.symphony_id,
                start_date=window["first"],
                end_date=window["last"],
                compact_body=True,
                max_retries=1,
                retry_on_rate_limit=False,
                min_retry_wait_seconds=composer_portfolio_client.COMPOSER_READ_SPACING_SECONDS,
            )
            run.rate_limited = str(sym.backtest.error or "").startswith("HTTP 429")
            time.sleep(composer_portfolio_client.COMPOSER_READ_SPACING_SECONDS)
    except Exception as exc:  # noqa: BLE001 - one symphony's failure is that symphony's item
        logger.warning("tenure review backtest step failed: %s", type(exc).__name__)
        sym.backtest = BacktestResult(None, [], error=_BACKTEST_FAILED_ERROR)


def _is_session(day: str) -> bool:
    try:
        return market_calendar.is_trading_day(date.fromisoformat(day))
    except ValueError:
        return False


def _read_holdings(account_ids, earliest: date, as_of: date, run: _RunState):
    """Returns (HoldingsInput, accounts whose holdings were not read in full)."""
    days: dict = {}
    first_failure = None
    missing: list[str] = []
    for account in account_ids:
        if run.rate_limited:
            missing.append(account)
            first_failure = first_failure or "rate_limited"
            continue
        read = composer_portfolio_client.read_daily_holdings(account, earliest, as_of)
        if read.failed_spans:
            missing.append(account)
            first_failure = first_failure or read.failed_spans[0].failure
            run.rate_limited = any(s.failure == "rate_limited" for s in read.failed_spans)
        for day, by_id in read.days.items():
            if _is_session(day):
                days.setdefault(day, {}).update(by_id)
    return tenure_review.HoldingsInput(days, None if days else first_failure), missing


def _name_missing_accounts(result: ReviewResult, own_account: dict, missing: list[str]) -> None:
    """Withhold the portfolio-wide shares, and explain an empty item, when an account is missing."""
    named = ", ".join(missing)
    for entry in result.entries:
        item = entry.get("evidence", {}).get("long_vix")
        if not item:
            continue
        if item["status"] == "available":
            reason = f"holdings not read for account {named}"
            for name in _SHARE_FIGURES:
                item["figures"][name] = {"value": None, "unit": "percent", "reason": reason}
        elif (
            item["reason"] == _NO_HOLDINGS_REASON
            and own_account.get(entry["symphony_id"]) in missing
        ):
            reason = f"holdings not read for account {own_account[entry['symphony_id']]}"
            item["reason"] = reason
            for figure in item["figures"].values():
                figure["reason"] = reason


def run_tenure_review(*, account_ids, threshold_raw, now_et, run_id, trigger="on_demand"):
    """Review every live symphony of the given accounts. Never raises."""
    try:
        if not account_ids:
            return RunOutcome(False, None, "no Composer account configured")
        threshold = tenure_review.parse_threshold(threshold_raw)
        as_of = tenure_review.as_of_trading_day(now_et)
        roster, reason = _read_roster(account_ids)
        if roster is None:
            return RunOutcome(False, None, reason)
        run = _RunState()
        state = _state_entries()
        symphonies = [_read_series(account, entry, state, run) for account, entry in roster]
        firsts = []
        for sym in symphonies:
            window = tenure_review.qualifying_window(sym, as_of, threshold.value)
            if window is not None:
                firsts.append(window["first"])
                _attach_backtest(sym, window, run)
        holdings, missing = None, []
        if firsts:
            holdings, missing = _read_holdings(
                account_ids, date.fromisoformat(min(firsts)), as_of, run
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
            _name_missing_accounts(result, {e["id"]: account for account, e in roster}, missing)
        return RunOutcome(True, result, None)
    except Exception as exc:  # noqa: BLE001 - the runner never raises
        name = type(exc).__name__
        logger.warning("tenure review run failed: %s", name)
        return RunOutcome(False, None, f"tenure review failed: {name}")
