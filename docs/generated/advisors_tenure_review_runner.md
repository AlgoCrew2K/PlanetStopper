# advisors/tenure_review_runner

> Runs one symphony tenure review: reads Composer for the roster, value series, definitions and holdings (with a shadow-history fallback), requests the backtests, then hands the inputs to `advisors.tenure_review.build_review`.

**Source:** `advisors/tenure_review_runner.py`
**Last updated:** 2026-11-02

## Overview

The runner is the only code that fetches inputs for the tenure review. It reads and runs counterfactual backtests only, writes nothing, and never raises. `app._tenure_review_job` calls it, then persists what it returns. The first reply answered HTTP 429 ends the run's Composer requests; every item that needed a later request reads as rate limited, so a single rate limit degrades the run instead of failing it.

A run completes when the roster could be read for every configured account. If the roster is unavailable for any configured account, or a response cannot be read, the run does not complete (`RunOutcome.completed` False with a `failure_reason` such as `Composer statistics unavailable`) and nothing is persisted.

## API Reference

### `run_tenure_review(*, account_ids, threshold_raw, now_et, run_id, trigger="on_demand", account_labels=None, read_spacing_seconds=COMPOSER_READ_SPACING_SECONDS) -> RunOutcome`
Reviews every live symphony of the given accounts. Returns `RunOutcome(completed, result, failure_reason, degraded)`. `degraded` is True when a rate limit was met or any series, definition, backtest or holdings read failed. With no account configured it returns `RunOutcome(False, None, "no Composer account configured")`.

### `configured_account_ids(env) -> list[str]`, `configured_account_labels(env) -> dict[str, str]`
Reads `ACCOUNT_INDIVIDUAL`, `ACCOUNT_ROTH` and `ACCOUNT_TRAD` from the mapping the caller passes (`app._tenure_review_settings`, which reads the env file first and `os.environ` second). Labels are `Individual`, `Roth IRA`, `Trad. IRA`.

## Types
`RunOutcome` (frozen dataclass). Private: `_MissingAccount`, `_RunState`.

## Internal Dependencies
`advisors.composer_portfolio_client` (reads), `advisors.composer_backtest_client` (backtests), `advisors.tenure_review` (compute), `analytics`, `database` (shadow-history fallback), `market_calendar`.

## Request budget
Composer's endpoints used here allow 25 requests a minute; the engine and the dashboard already use 13 to 15 of that. The client sleeps `COMPOSER_READ_SPACING_SECONDS = 6.0` after every request, answered or not, which adds at most 10 requests a minute. Reads are single attempts with no retry and no redirect following. Holdings are read in spans of at most `HOLDINGS_REQUEST_SPAN_DAYS = 30` calendar days.

## Unconfirmed
The holdings and symphony-statistics endpoints were verified against captured requests only; their live behaviour and the 25 requests a minute limit cannot be confirmed from code. Settle it with the droplet journal: the daemon's timing lines (`planetstopper.timing`) record each Composer call's latency and status.
