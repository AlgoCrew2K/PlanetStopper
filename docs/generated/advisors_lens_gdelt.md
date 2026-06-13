# advisors/lens_gdelt

> B1 GDELT tone-scoring producer for the Market Prism sentiment lens: fetches public GDELT 2.0 DOC API artlist, normalizes AvgTone to `[-1, 1]`, returns a structured `{available, tone, per_ticker, source}` dict. Bounded retry with explicit timeout. D-1 error contract throughout.

**Source:** `advisors/lens_gdelt.py`
**Last updated:** 2026-06-13

## Overview

`advisors/lens_gdelt.py` provides the `_fetch_gdelt_sentiment(universe)` producer function consumed by `ai_advisor._build_sentiment_section`. It is the Cycle B1 implementation of the sentiment lens data layer.

**Key properties:**
- **No API key required.** GDELT is a public, free-tier API.
- **Off-execution-path.** Never imported by `alpha_bot_execution.py`; lazily imported inside `ai_advisor._build_sentiment_section` (CC-2 import-boundary invariant).
- **Bounded retry.** Three-condition retry predicate (`attempt < _GDELT_MAX_ATTEMPTS AND delay > 0.0 AND total_waited + delay <= _GDELT_BACKOFF_CAP_S`) prevents the unbounded-loop pattern that caused the PC OOM crash.
- **Explicit timeout.** Every `requests.get` call carries `timeout=_GDELT_TIMEOUT_S` (no urllib3 default).
- **D-1 error contract.** The `reason` field in any `available=False` return carries only `type(exc).__name__`, never `str(exc)` (which may contain hostnames or partial credentials).
- **No tone fabrication.** When GDELT returns zero articles or all articles lack a `tone` field, the producer returns `available=False` rather than a fabricated neutral value.

**Integration point:** `ai_advisor._build_sentiment_section` lazy-imports this module and calls `_fetch_gdelt_sentiment(universe=[])` to populate `payload["tone_score"]` in the sentiment lens block. The lazy import keeps `lens_gdelt` off the module-level import graph.

**Fixture provenance:** Output shape is pinned in `tests/fixtures/math/gdelt_tone_producer_schema.json` (schema-derived, Cycle B1 RED — written before any implementation).

## Constants

| Constant | Value | Purpose |
|----------|-------|---------|
| `_GDELT_ARTLIST_URL` | `https://api.gdeltproject.org/api/v2/doc/doc?...` | Full GDELT artlist endpoint URL (mode=artlist, format=json, timespan=1440 = last 24 h, maxrecords=10) |
| `_GDELT_TIMEOUT_S` | `15.0` | HTTP request timeout in seconds (custom instructions §5 — no urllib3 default) |
| `_GDELT_MAX_ATTEMPTS` | `6` | Hard ceiling on HTTP attempts, including the first. PC-crash regression guard. |
| `_GDELT_BACKOFF_BASE_S` | `1.0` | Initial sleep before first retry (seconds) |
| `_GDELT_BACKOFF_CAP_S` | `8.0` | Maximum total seconds spent sleeping across all retries |
| `_GDELT_TONE_DIVISOR` | `100.0` | Normalization divisor: GDELT AvgTone `[-100, 100]` → `[-1, 1]` |
| `_GDELT_SOURCE` | `"GDELT 2.0 DOC API artlist/tone — https://api.gdeltproject.org/"` | Provenance string in `source` field of successful results |

## Public API

### `_fetch_gdelt_sentiment(universe: list[str]) → dict`

Fetch GDELT tone data for the portfolio universe and return a normalized directional signal.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `universe` | `list[str]` | Ticker symbols (e.g. `["SPY", "QQQ"]`). Accepted for interface parity with the lens pipeline; currently unused by the GDELT artlist API, which does not support per-ticker filtering. |

**Returns:**

On success (`available=True`):
```python
{
    "available": True,
    "tone":      float | None,  # mean AvgTone normalized to [-1, 1]; None if no articles carry a tone field
    "per_ticker": None,         # always None — GDELT artlist has no per-ticker filter
    "source":    str,           # _GDELT_SOURCE provenance string
}
```

On any failure (`available=False`):
```python
{
    "available":  False,
    "tone":       None,
    "per_ticker": None,
    "source":     None,         # or _GDELT_SOURCE when fetch succeeded but articles empty
    "reason":     str,          # type(exc).__name__ only (D-1) or "TooManyRequests" / "no_articles"
}
```

**Never raises.** All exceptions are caught and converted to `available=False` returns.

**Degradation paths:**

| Condition | Result |
|-----------|--------|
| Network timeout | `available=False, reason="Timeout"` |
| Connection error | `available=False, reason="ConnectionError"` |
| HTTP 4xx/5xx | `available=False, reason="HTTPError"` |
| Persistent 429 (retry budget exhausted) | `available=False, reason="TooManyRequests"` |
| Malformed JSON response | `available=False, reason="ValueError"` (or similar) |
| Empty article list | `available=False, reason="no_articles"` |
| Articles present but no `tone` field | `available=True, tone=None` |

## Internal API

### `_fetch_gdelt_artlist() → requests.Response`

GET the GDELT artlist endpoint with bounded exponential backoff. Returns the `requests.Response`; callers check `status_code` and call `raise_for_status()` themselves.

**Retry predicate (three conditions, all must hold):**
1. `attempt < _GDELT_MAX_ATTEMPTS` — hard attempt ceiling
2. `delay > 0.0` — positive sleep guard (prevents zero-delay spin)
3. `total_waited + delay <= _GDELT_BACKOFF_CAP_S` — time budget not exhausted

Retries on HTTP 429 and `ConnectionError` / `Timeout`. Any other exception propagates immediately after the budget is spent.

### `_normalize_tone(raw_tones: list[float]) → float | None`

Average a list of raw GDELT AvgTone values and normalize to `[-1, 1]`.

- Returns `None` when `raw_tones` is empty (no tone data — no fabrication).
- Clamps to `[-1.0, 1.0]` to guard against rare out-of-spec GDELT values.
- Normalization: `mean(raw_tones) / 100.0`.

## Error Contract (D-1)

All `except` blocks in this module use only `type(exc).__name__` in the `reason` field. `str(exc)` is never surfaced: it may contain hostnames, partial URLs, or request parameters from the GDELT call.

The literal strings `"TooManyRequests"` and `"no_articles"` are used for the 429-exhaustion and empty-articles cases respectively (not exception-derived, but safe: they contain no credential or host information).

## Test Coverage

**Test file:** `tests/ai_advisor/test_lens_gdelt.py` (42 tests)

| Class | Tests | Coverage |
|-------|-------|---------|
| `TestFetchGdeltSentimentExists` | 3 | Module importable, function callable, `universe` parameter present |
| `TestFetchReturnsValidShape` | 7 | Required keys, bool available, float-or-None tone, normalized bounds, non-empty source, per_ticker type, never raises on success |
| `TestUnavailableOnNetworkError` | 6 | ConnectionError, Timeout, HTTPError, tone=None on error, D-1 reason, never raises |
| `TestUnavailableOn429AfterMaxRetries` | 3 | available=False, tone=None, call count bounded (< 20) |
| `TestEmptyResponseReturnsUnavailable` | 3 | Empty articles → no fabricated tone, tone≠0.0, no-tone-field articles handled |
| `TestGoldenFixtureSchemaContract` | 6 | Schema fixture exists, required fields, invariants, artlist fixture exists, output satisfies schema keys, tone bounds invariant |
| `TestBoundedRetries` | 3 | `_GDELT_MAX_ATTEMPTS` constant exists and is in `[1, 20]`, single-429-then-200 retries correctly |
| `TestLensPipelineIntegration` | 5 | `run_pipeline(dry_run=True)` returns required keys, sentiment block wired when GDELT available, sentiment unavailable when GDELT fails, pipeline never raises, `_build_sentiment_section` calls `_fetch_gdelt_sentiment` |
| `TestSecurity` | 6 | No `eval()`/`exec()`, no hardcoded API key, D-1 on JSON decode error, no LIVE_EXECUTION reference, no Flask route, D-1 on HTTP error |

**Fixture:** `tests/fixtures/math/gdelt_tone_producer_schema.json` — schema-derived provenance (Cycle B1 RED, written before implementation).
