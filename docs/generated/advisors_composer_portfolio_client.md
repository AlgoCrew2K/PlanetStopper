# advisors/composer_portfolio_client

> Read-only Composer portfolio client for the symphony tenure review: four GET reads, each one request with no retry and no redirect following, paced by a fixed sleep, never raising.

**Source:** `advisors/composer_portfolio_client.py`
**Last updated:** 2026-11-02

## Overview

Every function returns a small result object holding either data or a failure class string; none raises. A failure class is a short word (for example `rate_limited`, `unreadable`), never a response body or exception text. Logs carry the endpoint kind, HTTP status and exception type name only. Account and symphony ids must be plain URL path segments (`[A-Za-z0-9_-]+`); anything else is refused as unreadable without a request, so an id cannot reroute the request.

## API Reference

| Function | Endpoint | Returns |
|----------|----------|---------|
| `read_symphony_statistics(account_id, spacing_seconds=None)` | `GET /portfolio/accounts/{account}/symphony-stats-meta` | `StatisticsRead(failure, symphonies)`: the symphonies an account lists (id, display name, invested-since where reported) |
| `read_symphony_value_series(account_id, symphony_id, spacing_seconds=None)` | `GET /portfolio/accounts/{account}/symphonies/{symphony}` | `SeriesRead(failure, values)`: deposit-adjusted value by UTC date |
| `read_symphony_definition(symphony_id, spacing_seconds=None)` | `GET /symphonies/{symphony}/score` | `DefinitionRead(failure, body)`: the current decision tree, status kept |
| `read_daily_holdings(account_id, start, end, spacing_seconds=None)` | account daily holdings, in spans of at most `HOLDINGS_REQUEST_SPAN_DAYS = 30` days | `HoldingsRead(days, failed_spans)`: a rate-limited span ends the reads and it and every later span are listed as `FailedSpan` |

`COMPOSER_READ_TIMEOUT_SECONDS = 15` bounds each request. `COMPOSER_READ_SPACING_SECONDS = 6.0` is the sleep after every request whether or not it was answered; `is_valid_spacing_seconds` validates an override.

## Date key spaces
Series dates are UTC calendar dates from Composer's epoch milliseconds (`_utc_date`): Composer stamps each point at 00:00 UTC of its own calendar date, so the UTC date is the date it means. `advisors/composer_series_client.py` labels its returns the same way. These are Composer's dates, not New York trading-day keys in the shadow history; the tenure review keeps the two bases apart (`COMPOSER_BASIS_LABEL` against `SHADOW_BASIS_LABEL`).

## Failure classes
`refused` (401, 403), `not_found` (404), `rate_limited` (429), `unavailable` (5xx), `rejected` (any other status, a redirect included), `unreadable` (a body that cannot be read, or an id refused before the request).

## Internal Dependencies
`alpha_bot_execution` (`COMPOSER_BASE_URL`, `get_composer_headers` only, the same two names every read-only Composer client imports), `requests`.

## Boundary
GET only. No create, save, deploy, invest, rebalance, go-to-cash or sell request exists in this module.
