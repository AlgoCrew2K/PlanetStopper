# advisors/composer_series_client

> Read-only Composer client that fetches one symphony's realized daily value history from the account-scoped portfolio endpoint and returns it as ET-dated simple daily returns in **percent** scale. Its only consumer is the Retirement Recommender's `source="composer"` path. It never raises, sends GET requests only, and has no trade, deploy or draft primitive.

**Source:** `advisors/composer_series_client.py`
**Last updated:** 2026-10-22 (new module, Retirement Recommender Cycle 2e, `DE-RETIRE-VISIBILITY-001` AC-1/AC-10)

## Overview

The module exists for one reason. Before this cycle, `shadow_history` was the recommender's only series source, and it begins at the **2026-06-22 production go-live floor**. That gave roughly 87 trading days on 2026-10-22. The recommender's structural-redundancy gate needs **at least 181 aligned days** (`STRESS_GATE_MIN_ALIGNED_DAYS`), so no pair could ever reach a recommendation until about March 2027. See [advisors/retirement_recommender](advisors_retirement_recommender.md).

Composer keeps a much longer per-symphony history behind credentials Planet Stopper already uses. The 2026-09-03 audit live-probed 538 daily points reaching back to 2024-07-12, with zero truncation (`docs/audit/PERF-WINDOW-TRUTH-2026-09-03.md` E-24).

The client does five things:

1. Issues one `GET {COMPOSER_BASE_URL}/portfolio/accounts/{account_id}/symphonies/{symphony_id}` with `get_composer_headers()`, an explicit timeout, and a bounded retry.
2. Reads the parallel arrays `epoch_ms` and `deposit_adjusted_series`. It **never** reads the raw `series`, which jumps on every deposit or withdrawal; one cash deposit would otherwise become a fake multi-hundred-percent "return".
3. Maps each epoch to its **America/New_York** calendar date. When several points land on one ET date, the last one wins. Every date at or after today's ET date is dropped, so the series never contains a partial day.
4. Emits close-to-close simple returns `(v_t / v_{t-1} - 1) * 100` between consecutive remaining dates. This is the percent scale `analytics.compute_quantstats_metrics` expects; it divides by 100 itself.
5. Never raises. Every failure comes back as `SeriesResult(returns={}, error=<short reason>)`.

It runs off the 1-minute execution path. The recommender imports it lazily (CC-2) and only on the Composer source path, so the default `source="shadow"` never loads it.

## Basis caveat: `deposit_adjusted_series` is `[Unverified]`

Composer documents `deposit_adjusted_series` by name only. The reading this module relies on — a positive value-scale series with capital flows removed, so the ratio of consecutive points is a return — is an **inference**. It has never been checked against a live capture or against a known invest/withdraw date (`docs/audit/COMPOSER-HISTORICAL-SERIES-2026-09-03.md`, its "What exactly is `deposit_adjusted_series`?" open question and its `[Unverified]` row). The module docstring repeats this caveat, and the code claims nothing about what the series means economically. The recommender surfaces the basis to the operator verbatim as `"realized daily returns (Composer, deposit-adjusted)"`.

**No live Composer call was possible while this module was built or verified.** The cloud build container's network policy blocks `api.composer.trade` (verified 2026-10-22). Every test runs against a patched `requests.get` plus the synthetic fixture below. A real recapture is a tracked follow-up (see "Fixture").

## Public API

### `fetch_symphony_daily_returns(account_id: str, symphony_id: str) -> SeriesResult`

Fetches one symphony's realized daily returns. The caller passes the Composer **account id** (the recommender reads it from `bot_state[symphony_id]["account"]`) and the Composer symphony **hash**, never the display name (Architecture Constraint 6). The function never raises. The outer `try` catches any unexpected internal fault, logs `type(exc).__name__` at WARNING, and returns `error="unexpected failure: <TypeName>"`. The failure is reported, not hidden.

## Types

### `SeriesResult` (dataclass)

| Field | Type | Meaning |
|-------|------|---------|
| `returns` | `dict[str, float]` | ISO `YYYY-MM-DD` America/New_York date → simple daily return in **percent** (10% == `10.0`). Empty on failure, or when fewer than two usable dates exist. |
| `points` | `int` | `len(returns)`. |
| `first_date` / `last_date` | `str \| None` | `min` / `max` of `returns`; `None` when empty. |
| `error` | `str \| None` | `None` on success; otherwise a short D-1-safe reason, truncated to `_ERROR_MAX_CHARS`. |

A successful fetch can legitimately return `returns={}` with `error=None`, for example a symphony with a single dated point. The recommender handles that through its own `< 2 returns` exclusion (`series_too_short`). It does not treat it as an error.

## Named constants

| Constant | Value | Why |
|----------|-------|-----|
| `REQUEST_TIMEOUT_SECONDS` | `30.0` | Explicit per-request timeout; never `requests`' default of waiting forever. Matches `app.py`'s `_ACCOUNT_TOTALS_HTTP_TIMEOUT_S` for Composer account-scoped reads. |
| `MAX_ATTEMPTS` | `3` | Total attempts, including the first. Bounds the retry loop. |
| `RETRY_AFTER_CAP_SECONDS` | `30.0` | Ceiling on any single inter-attempt sleep, including a server-supplied `Retry-After`, so a hostile or buggy header cannot stall a run. |
| `MAX_TOTAL_RETRY_WAIT_SECONDS` | `60.0` | Derived as `(MAX_ATTEMPTS - 1) * RETRY_AFTER_CAP_SECONDS`. The named worst-case total retry wait for one fetch. |
| `_BACKOFF_BASE_SECONDS` / `_BACKOFF_GROWTH_FACTOR` | `1.0` / `2` | Default backoff `min(1.0 * 2**n, RETRY_AFTER_CAP_SECONDS)` before retry `n` (0-based). The 1 s base matches Composer's documented 1 req/s limit. |
| `_RETRYABLE_HTTP_STATUSES` | `{429, 500, 502, 503, 504}` | The same transient set the sibling `composer_backtest_client` retries. |
| `_MIN_EPOCH_MS` | `1e11` | Epoch-ms sanity floor. 1e11 ms is 1973-03-03, while a present-day epoch in **seconds** is about 1.7e9. A seconds-valued array therefore falls below the floor and the **whole body** is rejected, instead of being misread as 1970. |
| `_PERCENT_SCALE` | `100.0` | Percent output scale. |
| `_ET` | `ZoneInfo("America/New_York")` | Composer's trading-day calendar is US-equity. |
| `_ERROR_MAX_CHARS` | `200` | D-1 length cap on `SeriesResult.error`. |
| `_ENDPOINT_LOG_NAME` | `"GET /portfolio/accounts/{account}/symphonies/{symphony}"` | A fixed template for INFO logs; the real account and symphony ids never reach a log line. |

## Parsing — `_parse_series_body(body, today_et) -> SeriesResult`

The steps run in this order, and the function never raises:

1. **Shape and type validation.** The body must be a dict; `epoch_ms` and `deposit_adjusted_series` must both be lists of equal length; every element must be a real `int`/`float`. `bool` (an `int` subclass) and numeric strings are rejected. Any violation returns `error="malformed response: ..."`.
2. **Epoch check.** Points are sorted by epoch. Any non-finite epoch, or any epoch below `_MIN_EPOCH_MS`, rejects the whole body. An `OverflowError`, `OSError` or `ValueError` while converting an epoch also returns a malformed-response error.
3. **ET-date collapse.** Each epoch maps to its America/New_York date, **not** the UTC date; a late-evening ET point lands on the next UTC day. Within one ET date the last point wins, which keeps returns close-to-close.
4. **Today-drop.** Every date `>= today_et` is discarded. A midday "Run now" therefore never uses a partial day.
5. **Returns.** For consecutive remaining dates, the return is `(value / prev_value - 1.0) * 100`. It is emitted only when **both** endpoints are finite and strictly positive, and the result is itself finite. A non-positive or non-finite value on **either** side skips that return; this is a PM ruling, pinned with a hypothesis invariant in `tests/advisors/test_composer_series_client.py`. A symphony that was emptied and re-funded therefore never yields a fabricated -100% day or a division by zero. `prev_value` still advances past a skipped point, so the next return is measured from the latest mark, never stitched across the gap.

## Retry and error contract

`_fetch_with_retry` makes at most `MAX_ATTEMPTS` GET requests, sleeping only **between** attempts:

- **HTTP 200:** parse the JSON body. A body that is not valid JSON returns a malformed-response error with no retry.
- **HTTP 429/500/502/503/504:** retried. On 429, an integer-seconds `Retry-After` is honoured and clamped into `[0, RETRY_AFTER_CAP_SECONDS]`. A negative value clamps to `0`, never a negative sleep (`b255e95`). A missing or unparseable value, such as an HTTP-date, falls back to the exponential default. The header value itself is never logged.
- **Any other status:** a hard error, `error="HTTP <status>"`, with no retry.
- **Any transport exception:** retried, then reported as `error="request failed: <TypeName>"`.
- **Retries exhausted:** returns the last recorded reason.

**D-1.** An `error` string carries only an HTTP status code, an exception **type** name, or a fixed phrase. It never carries a response body, a header value, an exception message, or a body value. The INFO lines log the endpoint *template*, the status or exception type, and the attempt number, and nothing else. Nothing credential-shaped reaches a log record at any level.

**GET only.** No write is ever issued, so no idempotency key is needed.

## No-trade boundary (AC-8)

`tests/security/test_retirement_action_no_trade_boundary.py` **Group G** source-scans this module's *executable* text (docstrings and comments blanked) and fails on any of the following:

- **Forbidden fragments:** `/deploy/`, `/trading/`, `go-to-cash`, `execute_sell_to_cash`, `LIVE_EXECUTION`, `composer_draft_client`, `save_symphony`, `invest_in_symphony`, `/invest`, `/sell`, `/liquidate`.
- **`alpha_bot_execution` imports:** a whole-module import of `alpha_bot_execution`, or any import from it other than the precedent-allowed pair `COMPOSER_BASE_URL` / `get_composer_headers`. That is the same pair `advisors/composer_backtest_client.py` imports.
- **Other HTTP stacks:** any of `httpx`, `urllib`, `urllib3`, `aiohttp`, `http`, `socket`, `websocket`.
- **Non-GET `requests` use:** any `requests` attribute outside `get` and the exception types. Aliased `requests`, `from requests import post/Session` and `requests.*` submodule imports are all caught.
- **Mutating verbs:** any `post`/`put`/`patch`/`delete`/`request`/`send` call on any receiver.

The scan is a pure function over source text. `TestSeriesClientScanIsNonVacuous` feeds it synthetic mutated sources, appending the violation rather than prepending it, and asserts that the specific rule fires. This proves each rule can fail.

## Fixture

`tests/fixtures/composer/symphony_daily_series_v1.json` (AC-10) has the documented response shape: `epoch_ms`, `series` and `deposit_adjusted_series`, 21 points each. The raw `series` jumps about +48.7% at point 9 to simulate a deposit and about -12.2% at point 16 to simulate a withdrawal, while `deposit_adjusted_series` moves no more than about 2% on any day. Re-derived by this doc-writer from the fixture values. The fixture carries `"_fixture_provenance": "synthetic-from-docs"` and a `_note` stating that it is **not** a live capture and must be recaptured with `/api-fixture` from a network-enabled session. **Tracked follow-up:** recapture a real response and check the `deposit_adjusted_series` reading against a known deposit or withdrawal date.

## Internal Dependencies

- `alpha_bot_execution`: `COMPOSER_BASE_URL` and `get_composer_headers` only. This is a constants-and-headers import with the same precedent as `composer_backtest_client`; no execution function is called.
- `requests`: `requests.get` only.
- Standard library: `zoneinfo`, `datetime`, `math`, `time`, `logging`, `dataclasses`.

**Callers:** `advisors.retirement_recommender._collect_composer_series`, reached only through `build_recommendations_with_summary(source="composer")`, is the sole caller. In production, `app.py`'s `_retirement_recommender_tick_worker` calls it through the 03:45 tick and `POST /ai-advisor/retirement/run`.

See `DE-RETIRE-VISIBILITY-001` in `DECISIONS.md`.
