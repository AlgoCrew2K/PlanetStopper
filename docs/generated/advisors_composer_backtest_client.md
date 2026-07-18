# advisors/composer_backtest_client

> M2 AI Advisor building block: submits an inline symphony definition to Composer's `POST /api/v0.1/backtest` and parses the response into a typed `BacktestResult` the gate layer (`advisors/backtest_gate_engine.py`) consumes directly.

**Source:** `advisors/composer_backtest_client.py`
**Last updated:** 2026-07-17 (math-r2, `DE-MATH-R2-001` AC-5 — `_extract_returns` emits simple returns instead of log returns at the producer boundary)

*This is this module's first `docs/generated/` entry — it had no prior doc-gen coverage. Full-module coverage reflects that this cycle's diff (`e57c2970`) touched the module for the first time under doc-gen; created now rather than left undocumented indefinitely.*

## Overview

`composer_backtest_client.py` is the sole client for Composer's inline-backtest endpoint. It:

1. Builds a `POST /api/v0.1/backtest` request body from a `raw_value` symphony tree (the same shape `GET /api/v0.1/symphonies/{id}/score` returns) plus cost/broker parameters.
2. Submits with bounded exponential-backoff retry (1s → 2s → 4s → 8s, `max_retries` default 4, `BACKTEST_MAX_RETRY_WAIT_SECONDS`-bounded cumulative wait); 429 responses honor `Retry-After`; a fixed set of transient HTTP statuses (`429, 500, 502, 503, 504`) retry, everything else fails immediately.
3. Parses a successful response's `dvm_capital` (per-day portfolio values) into date-keyed portfolio values and daily returns via `_extract_returns`.
4. Never raises on API or transport errors — every failure mode (bad JSON, non-retryable HTTP status, timeout, transport exception, retries exhausted) returns a `BacktestResult` with `stats=None` and a populated `error` string, so one candidate's failure never aborts a batch.

Off-execution-path: consumed by the Strategy Builder / advisor proposal pipeline (`advisors/backtest_gate_engine.py`, `advisors/strategy_builder_engine.py`), never by `alpha_bot_execution.py`.

## Public API

### `run_backtest(raw_value, symphony_id="", *, capital=10_000.0, apply_reg_fee=True, apply_taf_fee=True, slippage_percent=0.005, broker="alpaca", backtest_version="v2", max_retries=4) -> BacktestResult`

Submit an inline symphony tree and parse the result. Never raises.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `raw_value` | `dict` | Full symphony decision-tree dict (from `GET /api/v0.1/symphonies/{id}/score`). Its own `id` field is used as `symphony_id` when the caller does not supply one — matching the key Composer uses in `dvm_capital`. |
| `symphony_id` | `str` | Composer symphony UUID for unpacking `dvm_capital`. Defaults to `raw_value.get("id", "")`. |
| `capital` | `float` | Starting portfolio value (USD). Default 10,000. |
| `apply_reg_fee` / `apply_taf_fee` | `bool` | SEC/FINRA fee flags. Default both `True`, matching the Composer UI. |
| `slippage_percent` | `float` | Execution slippage fraction (`0.005` = 0.5%). |
| `broker` | `str` | Broker enum Composer accepts. Default `"alpaca"`. |
| `backtest_version` | `str` | `"v1"` or `"v2"`. Default `"v2"`. |
| `max_retries` | `int` | Max retry attempts after transient failures. `0` = first attempt only. Bounded, never unbounded. |

**Returns:** `BacktestResult` — `error=None`/`stats` populated on success; `error` non-empty/`stats=None` on any failure.

`submit_backtest` is an alias for `run_backtest` (same function object) — some callers prefer that name.

## Types

### `BacktestResult` (dataclass)

| Field | Type | Description |
|-------|------|-------------|
| `stats` | `dict[str, Any] \| None` | The `stats` block from the Composer response on success; `None` on failure. |
| `data_warnings` | `list[Any]` | Ticker-level history warnings, always a list (never `None`) regardless of the raw API's shape — normalized via `_coerce_data_warnings_to_list`. |
| `daily_returns` | `dict[str, float]` | Date-keyed (ISO string) **simple** returns derived from `dvm_capital` portfolio values. Empty on failure or when `dvm_capital` is absent. **Changed 2026-07-17 (`DE-MATH-R2-001` AC-5):** was log returns before this cycle — see `_extract_returns` below. |
| `daily_portfolio_values` | `dict[str, float]` | Date-keyed (ISO string) portfolio values. Empty on failure. |
| `first_day` / `last_market_day` | `str \| None` | ISO date strings for the backtest's first/last trading day. |
| `costs` | `dict[str, float]` | Cost breakdown. Empty dict on failure. |
| `error` | `str \| None` | `None` on success; non-empty failure-reason string on any error path. |

**Docstring/comment audit finding (filed to r2-analytics, not self-edited):** the class docstring (`advisors/composer_backtest_client.py:87-88`, "``daily_returns`` is a date-keyed dict of **log returns** derived from the per-day portfolio values") and the `daily_returns` field's inline comment (`:101`, "Date-keyed (ISO string) **log returns** derived from dvm_capital portfolio values") both still say "log returns." The AC-5 commit (`e57c2970`) rewrote `_extract_returns`'s own docstring to describe the new simple-return contract correctly but did not touch these two other, now-stale mentions of "log returns" in the same file. Two-line fix, same pattern as the `_extract_returns` docstring rewrite.

## Internal Helpers

### `_extract_returns(dvm_capital: dict, symphony_id: str) -> tuple[dict[str, float], dict[str, float]]`

Extracts date-keyed portfolio values and returns from `dvm_capital` (shape: `{symphony_id: {day_int_str: portfolio_value_float}}`). Returns `(daily_portfolio_values, daily_returns)`, both keyed by ISO date string, sorted chronologically.

**Simple-return fix (`DE-MATH-R2-001` AC-5, `advisors/composer_backtest_client.py:190`):** `daily_returns[date] = (curr_val / prev_val) - 1.0` — genuine simple (arithmetic) returns. **Prior to this cycle:** `math.log(curr_val / prev_val)` — log returns. Every downstream consumer of this producer assumes simple-return compounding: `analytics.compute_quantstats_metrics`'s `(1+r).prod()-1` and quantstats' own cagr/calmar internals, and `math_engine.compute_crra_eu_objective`'s `W = 1 + r` wealth-ratio math via the PBO/CRRA-EU gate path (`backtest_gate_engine.py:686`'s `RETURN_PCT_TO_FRACTION` boundary) — feeding log returns through simple-return math was a category error (log returns require `exp(sum(r))-1` compounding, not `prod(1+r)-1`), understating compounded return by an amount that grows with volatility. This single producer-side fix corrects both consumers at their one shared root — see `docs/generated/autotuner.md`'s "Adoption Cascade" note and `docs/generated/advisors_backtest_gate_engine.md`'s `_fold_transform_single` entry for the downstream consequences; `DECISIONS.md` `DE-MATH-R2-001` "Decision: AC-5" for the full three-step boundary-ruling history (client-side conditional → consumer-side kwarg → this producer-side final).

**Single-series fallback (pre-existing, unchanged this cycle):** for inline synthetic-tree backtests, the response key Composer uses for `dvm_capital` is not attested to always match the requested `symphony_id`. When exactly one series comes back under a non-matching key, it is used regardless (logged as a warning) rather than returning empty — a 2026-06-12 live-daemon E2E finding that an exact-match-only lookup silently degraded the whole proposal pipeline (empty returns → every candidate withheld with `None` metrics) whenever the key didn't match.

Gaps in the series (weekends, holidays) are non-trading days and are skipped, not zero-filled. Empty or missing inner dict → two empty dicts. Non-finite guard: `prev_val > 0 and curr_val > 0` — a non-positive portfolio value never contributes a return entry.

### `_day_int_to_iso(day_int: int | str) -> str`

Converts a Composer day integer (days since `1970-01-01`, the Unix epoch) to an ISO date string.

### `_coerce_data_warnings_to_list(raw: Any) -> list[Any]`

Normalizes `data_warnings` to a list regardless of the live API's shape — it has returned both `{}` and `[]` for "no warnings." Empty dict → empty list; list → unchanged; non-empty dict → flattened to its values; anything else → wrapped in a single-element list (or `[]` for `None`).

### `_parse_response(body: dict, symphony_id: str) -> BacktestResult`

Parses a raw 200 response body into `BacktestResult`. Missing keys yield safe defaults, never an exception.

### `_error_result(reason: str) -> BacktestResult`

Constructs a failure `BacktestResult` — `stats=None`, `data_warnings=[]`, `error=reason`.

## Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `_BACKOFF_INTERVALS` | `(1.0, 2.0, 4.0, 8.0)` | Retry backoff schedule, indexed by attempt number. |
| `BACKTEST_MAX_RETRY_WAIT_SECONDS` | `15.0` | Sum of `_BACKOFF_INTERVALS` — hard ceiling on cumulative retry wait. |
| `_BACKTEST_REQUEST_TIMEOUT` | `120` | Per-request HTTP timeout (seconds). Composer serializes a full decision tree plus per-day portfolio values for large symphonies, so a generous timeout is required. |
| `_RETRYABLE_HTTP_STATUSES` | `{429, 500, 502, 503, 504}` | Transient statuses that warrant a retry. |
| `_COMPOSER_DAY_EPOCH` | `date(1970, 1, 1)` | Unix epoch, used to convert Composer day integers to ISO dates. |
| `_DEFAULT_CAPITAL` | `10_000.0` | Default starting portfolio value, matching the Composer UI. |
| `_DEFAULT_SLIPPAGE_PERCENT` | `0.005` | Default execution slippage fraction. |
| `_DEFAULT_BROKER` | `"alpaca"` | Default broker enum. |
| `_DEFAULT_BACKTEST_VERSION` | `"v2"` | Default backtest version. |

## Error Handling Contract (AC-X5)

This module never raises on API or transport errors — every failure mode returns a `BacktestResult` with `stats=None` and `error=<reason string>`, so a single candidate's failure never aborts the batch. Covers: non-200 HTTP status (after retries exhausted or on a non-retryable status), invalid JSON in a 200 response, request timeout (not retried — the server may still be processing), and any other `requests.RequestException` (retried per the backoff schedule, then returns an error result).

## Test / Live Separation

Tests patch `requests.post` directly to inject fixture responses — this module does not use a `requests.Session` internally, so the standard `patch("requests.post", ...)` intercept works without additional wiring. No top-level network calls are made at import time.

## Rate Limit

`POST /api/v0.1/backtest` inherits the standard Composer 1 req/sec limit. This module does not sleep between separate `run_backtest` calls — callers (e.g. `advisors/backtest_gate_engine.py`'s batch evaluation) are responsible for spacing concurrent candidate submissions.

## Internal Dependencies

- `alpha_bot_execution` — `COMPOSER_BASE_URL`, `get_composer_headers` (the ONLY import from an execution-path module; used purely for the Composer API base URL and auth headers, not for any execution-path call)
- `requests` — third-party HTTP client
- `dataclasses`, `datetime`, `logging`, `time`, `typing` — stdlib

## Consumers

- `advisors/backtest_gate_engine.py` — `_fold_transform_single` consumes `BacktestResult.daily_returns` (see `docs/generated/advisors_backtest_gate_engine.md`)
- `advisors/strategy_builder_engine.py`, `advisors/frontrunner_builder.py`, `advisors/asset_swap_engine.py`, `advisors/logic_change_engine.py` — each multiplies `daily_returns` by 100 into percent-scale `BacktestCandidate.dated_returns` (unaffected by the AC-5 return-convention change — a scale multiplier is convention-agnostic)
- `analytics.compute_quantstats_metrics` (via the advisor call chain) — the original M1 finding's consumer; needed **zero code changes** for AC-5, since the fix is entirely at this producer
