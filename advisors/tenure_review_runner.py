"""Run a symphony tenure review: Composer reads, shadow fallback, backtests, then the review.

Reads and counterfactual backtests only; writes nothing. Never raises.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import analytics
import symphony_logic
from advisors import composer_backtest_client, composer_portfolio_client, tenure_review
from advisors.tenure_review import ReviewResult

logger = logging.getLogger(__name__)

_ACCOUNT_ENV_KEYS = ("ACCOUNT_INDIVIDUAL", "ACCOUNT_ROTH", "ACCOUNT_TRAD")


@dataclass(frozen=True)
class RunOutcome:
    completed: bool
    result: ReviewResult | None = None
    failure_reason: str | None = None


def configured_account_ids(env) -> list[str]:
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


def _read_series(account, entry) -> tenure_review.SymphonyInput:
    sym = tenure_review.SymphonyInput(
        symphony_id=entry["id"],
        display_name=entry.get("name"),
        invested_since=entry.get("invested_since"),
    )
    series = composer_portfolio_client.read_symphony_value_series(account, sym.symphony_id)
    if series.failure is None:
        sym.composer_values = series.values
    else:
        sym.series_failure = series.failure
        sym.shadow_returns = _shadow_returns(sym.symphony_id)
    return sym


def _qualifying_window(sym, as_of: date, threshold: int):
    prep = tenure_review._prepare(sym, as_of, threshold)
    if not prep.eligible:
        return None
    first, last = prep.window["first"], prep.window["last"]
    return (first, last) if first and last else None


def _attach_backtest(sym, window) -> None:
    definition = symphony_logic.fetch_symphony_score(sym.symphony_id)
    if not definition:
        sym.definition_available = False
        return
    sym.backtest = composer_backtest_client.run_backtest(
        definition,
        sym.symphony_id,
        start_date=window[0],
        end_date=window[1],
        compact_body=True,
        max_retries=1,
    )


def _read_holdings(account_ids, earliest: date, as_of: date) -> tenure_review.HoldingsInput:
    days: dict = {}
    first_failure = None
    for account in account_ids:
        read = composer_portfolio_client.read_daily_holdings(account, earliest, as_of)
        if read.failed_spans and first_failure is None:
            first_failure = read.failed_spans[0].failure
        for day, by_id in read.days.items():
            days.setdefault(day, {}).update(by_id)
    return tenure_review.HoldingsInput(days, None if days else first_failure)


def run_tenure_review(*, account_ids, threshold_raw, now_et, run_id, trigger="on_demand"):
    try:
        if not account_ids:
            return RunOutcome(False, None, "no Composer account configured")
        threshold = tenure_review.parse_threshold(threshold_raw)
        as_of = tenure_review.as_of_trading_day(now_et)
        roster, reason = _read_roster(account_ids)
        if roster is None:
            return RunOutcome(False, None, reason)
        symphonies = [_read_series(account, entry) for account, entry in roster]
        firsts = []
        for sym in symphonies:
            window = _qualifying_window(sym, as_of, threshold.value)
            if window is not None:
                firsts.append(window[0])
                _attach_backtest(sym, window)
        holdings = None
        if firsts:
            holdings = _read_holdings(account_ids, date.fromisoformat(min(firsts)), as_of)
        inputs = tenure_review.ReviewInputs(
            run_id=run_id,
            trigger=trigger,
            started_at=now_et,
            as_of=as_of,
            threshold=threshold,
            symphonies=symphonies,
            holdings=holdings,
        )
        return RunOutcome(True, tenure_review.build_review(inputs), None)
    except Exception as exc:  # noqa: BLE001 - the runner never raises
        name = type(exc).__name__
        logger.warning("tenure review run failed: %s", name)
        return RunOutcome(False, None, f"tenure review failed: {name}")
