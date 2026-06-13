# advisors/lens_gdelt

> B1 GDELT tone-scoring producer for the Market Prism sentiment lens: fetches public GDELT 2.0 DOC API `mode=timelinetone`, normalizes AvgTone to `[-1, 1]`, returns a structured `{available, tone, per_ticker, source}` dict. Bounded retry with explicit timeout. D-1 error contract throughout.

**Source:** `advisors/lens_gdelt.py`
**Last updated:** 2026-06-13

## Overview

`advisors/lens_gdelt.py` provides the `_fetch_gdelt_sentiment(universe)` producer function for the Market Prism sentiment lens. It is the Cycle B1 implementation of the GDELT sentiment data layer.

**Why `mode=timelinetone`, not `mode=artlist`:**
The original implementation used `mode=artlist`, which returns article metadata (url, title, seendate, domain) but carries **no per-article AvgTone** in the free-tier GDELT response. That caused `tone_score` to be `None` on every production call — the bug this module fixes. `mode=timelinetone` returns a timeline of `{date, value}` entries where `value` is GDELT AvgTone (a float in `[-100, 100]`) for each 15-minute bucket. This is the correct tone-bearing endpoint.

**Parsing — series nesting (critical):**
The real GDELT 2.0 timelinetone response has a two-level nested shape:
```
{
  "timeline": [
    {
      "series": "Average Tone",
      "data": [{"date": "...", "value": <float>}, ...]
    }
  ]
}
```
The producer iterates `timeline[*].data[*].value` to collect AvgTone floats. The old implementation iterated `timeline[*].value` — attempting `.get("value")` on series objects that have no `value` key — which silently produced all-`None` tone reads. The fixture `gdelt_timelinetone_api_shape.json` (captured live 2026-06-13) documents this shape authoritatively.

**Key properties:**
- **No API key required.** GDELT is a public, free-tier API.
- **Off-execution-path.** Never imported by `alpha_bot_execution.py`; this module is advisory-only (CC-2 import-boundary invariant). See Wiring Status below for current integration state.
- **Bounded retry.** Three-condition retry predicate (`attempt < _GDELT_MAX_ATTEMPTS AND delay > 0.0 AND total_waited + delay <= _GDELT_BACKOFF_CAP_S`) prevents the unbounded-loop pattern that caused the PC OOM crash.
- **Explicit timeout.** Every `requests.get` call carries `timeout=_GDELT_TIMEOUT_S` (no urllib3 default).
- **D-1 error contract.** The `reason` field in any `available=False` return carries only `type(exc).__name__`, never `str(exc)` (which may contain hostnames or partial credentials).
- **No tone fabrication.** When GDELT returns an empty timeline or all data points lack a numeric `value` field, the producer returns `available=False` (with `reason="empty_timeline"`) or `available=True, tone=None` — never a fabricated neutral value.

**Wiring status:** `_fetch_gdelt_sentiment` is **not yet wired** into `advisors/lens_pipeline.py` or `ai_advisor._build_sentiment_section`. The `ai_advisor._build_sentiment_section` still uses the old artlist path. Wiring `lens_gdelt` into the live pipeline is a deliberate follow-up PR.

**Fixture provenance:** Output shape is pinned in `tests/fixtures/math/gdelt_tone_producer_schema.json`. The API response shape is captured live in `tests/fixtures/math/gdelt_timelinetone_api_shape.json` — provenance: `"captured-from-producer — live GET 2026-06-13"` (not schema-derived; a real GDELT API call).

## Constants

| Constant | Value | Purpose |
|----------|-------|---------|
| `_GDELT_TIMELINETONE_URL` | `https://api.gdeltproject.org/api/v2/doc/doc?query=stock+market+finance&mode=timelinetone&format=json&timespan=1440` | Full GDELT timelinetone endpoint URL (last 24 h, no API key) |
| `_GDELT_TIMEOUT_S` | `15.0` | HTTP request timeout in seconds (project rule §5 — no urllib3 default) |
| `_GDELT_MAX_ATTEMPTS` | `6` | Hard ceiling on HTTP attempts, including the first. PC-crash regression guard. |
| `_GDELT_BACKOFF_BASE_S` | `1.0` | Initial sleep before first retry (seconds); doubles each retry |
| `_GDELT_BACKOFF_CAP_S` | `8.0` | Maximum total seconds spent sleeping across all retries |
| `_GDELT_TONE_DIVISOR` | `100.0` | Normalization divisor: GDELT AvgTone `[-100, 100]` → `[-1, 1]` |
| `_GDELT_SOURCE` | `"GDELT 2.0 DOC API timelinetone — https://api.gdeltproject.org/"` | Provenance string in the `source` field of successful results |

## Public API

### `_fetch_gdelt_sentiment(universe: list[str]) → dict`

Fetch GDELT tone data via the `mode=timelinetone` endpoint and return a normalized directional signal.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `universe` | `list[str]` | Ticker symbols (e.g. `["SPY", "QQQ"]`). Accepted for interface parity with the lens pipeline; currently unused by the GDELT timelinetone API, which does not support per-ticker filtering. |

**Returns:**

On success (`available=True`):
```python
{
    "available": True,
    "tone":      float | None,  # mean AvgTone normalized to [-1, 1]; None if no data points carry a numeric value
    "per_ticker": None,         # always None — GDELT timelinetone has no per-ticker filter
    "source":    str,           # _GDELT_SOURCE provenance string
}
```

On any failure (`available=False`):
```python
{
    "available":  False,
    "tone":       None,
    "per_ticker": None,
    "source":     None,         # or _GDELT_SOURCE when fetch succeeded but timeline was empty
    "reason":     str,          # type(exc).__name__ only (D-1), or "TooManyRequests" / "empty_timeline"
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
| Empty timeline (`timeline == []`) | `available=False, reason="empty_timeline", source=_GDELT_SOURCE` |
| Timeline present but all data points lack numeric `value` | `available=True, tone=None` |

## Internal API

### `_fetch_gdelt_timelinetone() → requests.Response`

GET the GDELT `mode=timelinetone` endpoint with bounded exponential backoff. Returns the `requests.Response`; caller checks `status_code` and calls `raise_for_status()`.

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

The literal strings `"TooManyRequests"` and `"empty_timeline"` are used for the 429-exhaustion and empty-timeline cases respectively (not exception-derived, but safe: they contain no credential or host information). The string `"no_articles"` does **not** appear in this module.

## Test Coverage

**Test file:** `tests/ai_advisor/test_lens_gdelt.py` (50 tests)

| Class | Tests | Coverage |
|-------|-------|---------|
| `TestFetchGdeltSentimentExists` | 3 | Module importable, function callable, `universe` parameter present |
| `TestEndpointUsesTimelinetone` | 3 | URL constant contains `timelinetone` not `artlist`; static source scan; response parsing reads `timeline` key not `articles` |
| `TestFetchReturnsValidShape` | 7 | Required keys, bool `available`, float-or-None `tone`, normalized bounds, non-empty `source`, `per_ticker` type, never raises on success |
| `TestGoldenFixtureSchemaContract` | 9 | Schema fixture exists + has required fields + has invariants + asserts `endpoint_uses_timelinetone`; timelinetone shape fixture exists + has `timeline` array + entries have `value` field; producer output satisfies schema keys; tone bounds invariant |
| `TestUnavailableOnNetworkError` | 6 | `ConnectionError`, `Timeout`, `HTTPError`, `tone=None` on error, D-1 reason, never raises |
| `TestUnavailableOn429AfterMaxRetries` | 4 | `available=False`, `tone=None`, call count bounded (< 20), single-429-then-200 retries correctly |
| `TestBoundedRetries` | 4 | `_GDELT_MAX_ATTEMPTS` exists and is in `[1, 20]`; backoff cap constant exists; timeout constant exists |
| `TestEmptyTimelineReturnsUnavailable` | 4 | Empty timeline → no fabricated tone, `tone ≠ 0.0`, no-value-field entries handled gracefully, missing `timeline` key handled gracefully |
| `TestToneIsActuallyScored` | 4 | `tone` is positive float from positive value; negative float from negative value; correctly normalized from single entry; multiple entries produce scalar |
| `TestSecurity` | 6 | No `eval()`/`exec()`, no hardcoded API key, D-1 on JSON decode error, no `LIVE_EXECUTION` reference, no Flask route, D-1 on HTTP error |

**Fixtures:**
- `tests/fixtures/math/gdelt_tone_producer_schema.json` — producer output schema (pinned contract).
- `tests/fixtures/math/gdelt_timelinetone_api_shape.json` — live-captured GDELT API response shape. Provenance: `"captured-from-producer — live GET 2026-06-13"`. Documents the real nested structure (`timeline[*].data[*].value`) and explains why the old flat-parse assumption yielded `tone=None`.
