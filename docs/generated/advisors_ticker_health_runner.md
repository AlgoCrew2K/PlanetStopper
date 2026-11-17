# advisors/ticker_health_runner

> Runs one dead-ticker check: reads the live symphony rosters and trees from Composer, asks Alpaca about each distinct ticker, stores one run row and posts newly found dead pairs to Discord once.

**Source:** `advisors/ticker_health_runner.py`
**Last updated:** 2026-11-17

## Overview

One run reads each configured account's symphony roster, each symphony's current tree (Composer `/score`, GET only, paced by `advisors.composer_portfolio_client`), then one Alpaca asset record per distinct plain ticker (`GET {ALPACA_TRADING_BASE_URL}/v2/assets/{ticker}`, the paper trading host, no redirects). Judgement and text live in [advisors/ticker_health](advisors_ticker_health.md). The module is advisory: no order, no Composer write, no credential or response body in a log line or stored row. `app.py` is its only caller (see the dead-ticker wiring section of [app.md](app.md)).

Stored rows (`advisor_observations`, both roles excluded from `_ADVISOR_ROLES`):

- `TICKER_HEALTH_RUN`: one row per run, `subject_id` the run id, `verdict` the status, `raw_response` the record (`schema` 1, `run_id`, `trigger`, `started_at`, `ended_at`, `status`, `completed`, `reason`, `error_type`, and for a completed run `warnings`, `not_checked`, `counts`). Statuses: `completed`, `unavailable`, `skipped`, `failed`. `error_type` is an exception type name only. `counts.dead_pairs` is the number of warnings stored, which includes pairs carried from an earlier run for symphonies or tickers this run did not judge; it is not the number this run judged dead.
- `TICKER_HEALTH_ANNOUNCEMENT`: one row per Discord message posted, listing the `[symphony_id, ticker]` pairs it named. Announcements are read from the previous completed run only, keyed by Composer hash id and ticker, so a rename never re-announces.

No migration: both roles use the existing append-only `advisor_observations` table.

## Gates as built

- **Mode:** the check does not read `LIVE_EXECUTION` or any symphony's `live_mode`; it runs the same in dry-run and live mode and cannot place or change an order.
- **Market (Composer):** `_Run.gate()` runs before each roster and each definition read. If `market_calendar.get_market_state` reads `open`, reads stop; if no Composer request had begun, the run is stored `skipped` ("market open"), otherwise each symphony not yet read is not checked ("market opened").
- **Another Composer job:** the gate also waits while another in-process Composer reader runs (the predicate `app._ticker_health_other_reader_running`), polling every `GATE_POLL_SECONDS` up to `GATE_WAIT_LIMIT_SECONDS` (2700 s), then stops ("another Composer job was running"). A predicate that raises stops the gate at once with its own cause ("could not tell whether another Composer job was running"), logged as the exception type only.
- **Rate limit (Composer):** a 429 ends the run's Composer requests; each symphony not yet read is not checked ("Composer rate limit").
- **Market (Alpaca):** `_read_asset` checks the market before every attempt, so a run begun near the open stops asking Alpaca at 09:30 ET and the tickers not yet asked read not checked ("market opened").
- **Alpaca bounds:** `ALPACA_SPACING_SECONDS = 0.5` after every attempt, `ALPACA_ATTEMPTS = 2` per ticker on a 429, 5xx or transport error, retry wait from a clamped `Retry-After` (default 2 s, cap 30 s), `ALPACA_RETRY_BUDGET_SECONDS = 120` of total 429 wait per run, and a stop after `ALPACA_FAILURE_STREAK_LIMIT = 5` consecutive failing tickers or any 401 or 403. Tickers after a stop read not checked with the stop's reason.

## API Reference

### `run_ticker_health_check(*, account_ids, account_labels, alpaca_key, alpaca_secret, webhook_url, trigger, other_reader_running, clock=None) → RunOutcome`
One check. `RunOutcome(run_id, status, completed)`. Never raises an `Exception` (an unexpected fault is a stored `failed` run); a `BaseException` leaves no run row. With no account configured, or a missing Alpaca key or secret, the run ends `unavailable` before any request. A run that read no roster ends `unavailable` with the one shared failure reason (or "no account's symphony list could be read"). An account with no usable label is shown as `account {position}`, never the Composer account id. A symphony whose tree names a non-equity asset class is reported not checked. If the run row cannot be stored the outcome is `failed` and nothing is posted. After a completed run with a webhook, the newly found, not-yet-announced pairs are posted one message at a time (`DISCORD_SPACING_SECONDS = 1.5` apart, `allowed_mentions` empty, no redirects); the first failed post stops posting and never fails the stored run. One end line logs the status and the Discord posted and failed counts.

### `read_latest_runs(*, scan_limit=RUN_SCAN_LIMIT) → LatestRuns`
`LatestRuns(latest, latest_completed)`: the newest run record and the newest completed one (scanning up to `RUN_SCAN_LIMIT = 365` newest rows when the newest did not complete). Raises on a database error; callers degrade. Stored warnings missing a required field are skipped as unreadable when a run starts, with the row id logged and no data.

## Types
- `RunOutcome(run_id, status, completed)` and `LatestRuns(latest, latest_completed)`, named tuples.

## Internal Dependencies
- `advisors.ticker_health` (verdicts, carry-forward, text), `advisors.composer_portfolio_client` (`read_symphony_statistics`, `read_symphony_definition`, `is_plain_id`)
- `advisors.universe_provider` (`ALPACA_TRADING_BASE_URL` only; its weekly tradeable-set cache is not read or refreshed)
- `database` (`insert_advisor_observation`, `get_advisor_observations_for_role`), `market_calendar`, `timing_log`, `requests`

## Unconfirmed
- Whether Alpaca's rate limit is shared between the paper trading host (asset reads here) and the data host (the engine's bar reads) is unverified.
- The real count of distinct tickers across the live trees is owed from the first production run (`counts.tickers_asked`); the read-only query is `SELECT json_extract(raw_response, '$.counts.tickers_asked') FROM advisor_observations WHERE advisor_role='TICKER_HEALTH_RUN' ORDER BY id DESC LIMIT 1`.
