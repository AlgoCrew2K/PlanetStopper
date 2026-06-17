# advisors/lens\_options\_proxy

> Derivatives/volatility lens producer — fetches current FRED VIXCLS and VXVCLS and returns a freshness-guarded directional risk read.

**Source:** `advisors/lens_options_proxy.py`
**Last updated:** 2026-06-16

## Overview

Produces a normalized directional risk read from two free FRED series: VIXCLS (spot 1-month implied volatility) and VXVCLS (3-month implied volatility). Both series are available on the no-cost FRED tier; no subscription is required. The producer is off-execution-path and advisory-only — it is called by `advisors/lens_pipeline.py` in the 03:00 off-hours nightly run and is never imported at `alpha_bot_execution.py` module level (CC-2 boundary).

**Honest-availability covers staleness, not only fetch failure.** Prior to the freshness-guard fix (2026-06-16), the lens served a ~6-year-stale VIX value as `available=True` because the hardcoded `observation_start="2020-01-01"` with ascending sort caused `_parse_latest_observation` to select the oldest 100 observations rather than the most-recent ones. The fix has two parts: (1) the FRED request now uses a rolling recent window (`_today() - _OPTIONS_PROXY_LOOKBACK_DAYS`) so the response always contains the most-recently published observations; (2) a freshness guard (`_OPTIONS_PROXY_MAX_STALENESS_DAYS`) rejects observations older than 10 calendar days as `available=False` even when the HTTP fetch succeeded.

## API Reference

### `_fetch_options_proxy() → dict`

Public entry point. Fetches VIXCLS and VXVCLS from FRED, applies freshness guard, computes term-structure metrics, and returns a normalized risk read. **Never raises** — all exceptions are caught and returned as `available=False` with `reason=type(exc).__name__` (D-1 contract).

**Parameters:** none

**Returns:** `dict` with the following keys:

| Key | Type | Present when | Description |
|-----|------|-------------|-------------|
| `available` | `bool` | always | `True` when data is fresh and valid. |
| `vix_level` | `float` | `available=True` only | Latest spot VIX (VIXCLS). |
| `vix_term_structure` | `dict` | `available=True` only | `{spot, term_3m, ratio, spread, regime}` — see below. |
| `risk_read` | `str` | `available=True` only | One of: `"risk-off"`, `"risk-on"`, `"neutral"`. |
| `as_of_date` | `str` | `available=True` only | ISO date of the selected VIXCLS observation. Always equals the real FRED observation date; never fabricated. |
| `source` | `str` | always | Full FRED citation string. |
| `reason` | `str` | `available=False` only | Unavailability label (D-1: `type(exc).__name__` for exceptions; named labels for non-exception paths — see below). |

**`vix_term_structure` sub-keys:**

| Key | Type | Description |
|-----|------|-------------|
| `spot` | `float` | VIXCLS (1-month spot VIX). |
| `term_3m` | `float` | VXVCLS (3-month VIX). |
| `ratio` | `float` | `spot / term_3m`; 0.0 if `term_3m <= 0`. |
| `spread` | `float` | `spot - term_3m`. |
| `regime` | `str` | One of `"contango"`, `"backwardation"`, `"flat"` (see `_classify_regime`). |

**`reason` values when `available=False`:**

| Value | Meaning |
|-------|---------|
| `"KeyError"` | `FRED_API_KEY` not set in environment. |
| `"ValueError"` | FRED returned no valid observations, a malformed date, or an unexpected response shape. |
| `"stale_data"` | The latest FRED observation is older than `_OPTIONS_PROXY_MAX_STALENESS_DAYS` calendar days. This is a data-quality decision, not an exception; the label is a named sentinel, not `type(exc).__name__`. |
| `type(exc).__name__` | Any `requests.RequestException`, `requests.HTTPError`, `RuntimeError`, or other caught exception after retry exhaustion (D-1). |

**Example:**

```python
from advisors.lens_options_proxy import _fetch_options_proxy

result = _fetch_options_proxy()
if result["available"]:
    print(result["vix_level"], result["risk_read"], result["as_of_date"])
else:
    print("unavailable:", result["reason"])
```

---

### `_fetch_fred_series(series_id, api_key) → dict`

Fetches one FRED series with exponential-backoff bounded retry. Returns the raw parsed JSON dict (keys include `"observations"`). Raises on all failures after retry exhaustion — callers catch exceptions.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `series_id` | `str` | FRED series identifier (e.g. `"VIXCLS"`). |
| `api_key` | `str` | FRED API key from `os.environ` — never hardcoded. |

**The request always uses a rolling recent window:** `observation_start` is computed as `_today() - timedelta(days=_OPTIONS_PROXY_LOOKBACK_DAYS)` (default: 90 calendar days). This ensures the response contains the genuinely most-recently published observations, not a fixed historical window.

---

### `_parse_latest_observation(data) → tuple[float, str] | None`

Extracts `(value, date_str)` for the latest non-`"."` observation from a FRED response dict. Returns `None` when no valid observation exists. The observations list is expected in ascending date order (matching the `sort_order="asc"` request parameter); the helper walks the list in reverse to find the latest valid entry.

---

### `_classify_regime(spot, term_3m) → str`

Classifies the VIX term-structure regime from spot (`VIXCLS`) and 3-month (`VXVCLS`) values.

Returns one of `"contango"` (spot < term\_3m), `"backwardation"` (spot > term\_3m), or `"flat"` (ratio within `_FLAT_BAND_RATIO` of 1.0). If `term_3m <= 0`, returns `"flat"` (defensive, no fabrication).

Source: market convention — VIX term-structure regime is a recognized risk indicator; backwardation signals acute stress.

---

### `_derive_risk_read(regime, vix_level) → str`

Produces a normalized directional risk read from regime and VIX level.

| Condition | Returns |
|-----------|---------|
| `regime == "backwardation"` AND `vix_level >= 20.0` | `"risk-off"` |
| `regime == "contango"` AND `vix_level < 15.0` | `"risk-on"` |
| anything else | `"neutral"` |

Mixed signals (e.g. backwardation + low VIX) always resolve to `"neutral"` — no fabricated strong signal from ambiguous evidence.

---

### `_today() → datetime.date`

Returns today's date. This is an injectable test seam — monkeypatch it in tests to evaluate freshness guard logic deterministically without wall-clock coupling.

## Types

### Module-Level Constants

| Constant | Type | Value | Purpose |
|----------|------|-------|---------|
| `_OPTIONS_PROXY_MAX_STALENESS_DAYS` | `int` | `10` | **Freshness guard threshold.** Latest FRED observation must be within this many calendar days of `_today()`; older observations yield `available=False, reason="stale_data"`. Source: longest normal market closure (long-weekend + adjacent holiday ≈ 4 calendar days) with headroom. [PM-ASSUMED] |
| `_OPTIONS_PROXY_LOOKBACK_DAYS` | `int` | `90` | Rolling window width for `observation_start` query parameter. Wide enough to always contain several trading-day observations across holidays; keeps response small. [PM-ASSUMED] |
| `_OPTIONS_PROXY_MAX_ATTEMPTS` | `int` | `3` | Maximum fetch attempts per FRED series (1 initial + 2 retries). |
| `_OPTIONS_PROXY_BACKOFF_BASE_S` | `float` | `2.0` | Exponential-backoff base (seconds). |
| `_OPTIONS_PROXY_BACKOFF_CAP_S` | `float` | `16.0` | Per-sleep maximum (seconds). |
| `MAX_OPTIONS_RETRY_WAIT_SECONDS` | `float` | `6.0` | Hard ceiling on cumulative retry wait (`base + 2*base`). |
| `_OPTIONS_PROXY_TIMEOUT_S` | `float` | `15.0` | Per-request HTTP timeout (seconds). |
| `_VIX_LOW_THRESHOLD` | `float` | `15.0` | VIX level below which `"risk-on"` fires. Source: practitioner convention (VIX < 15 = low fear). |
| `_VIX_ELEVATED_THRESHOLD` | `float` | `20.0` | VIX level at or above which `"risk-off"` fires. Source: practitioner convention (VIX > 20 = elevated stress). |
| `_FLAT_BAND_RATIO` | `float` | `0.02` | Term-structure flat band (±2% of spot/term ratio). Source: market convention. |
| `_FRED_NA_VALUE` | `str` | `"."` | FRED "not available" marker — observations with this value are skipped. |
| `_RETRYABLE_HTTP_STATUSES` | `frozenset[int]` | `{429,500,502,503,504}` | HTTP status codes that trigger a retry. |

## Design Invariants

| Code | Invariant |
|------|-----------|
| CC-2 | Never imported at module level in `alpha_bot_execution.py`. |
| CC-3 | Honest availability — `available=False` with `reason` when FRED is down, returns no usable data, **or returns stale data** (see staleness contract below). Values are never fabricated. |
| CC-5 | Free APIs only — VIXCLS and VXVCLS on FRED are no-cost. |
| D-1 | `reason` is `type(exc).__name__` only for caught exceptions — never `str(exc)`, never the raw exception message. Named sentinel labels (`"stale_data"`, `"KeyError"`, etc.) are used for non-exception unavailability paths. |
| AC-12 | `put_call` is omitted — no genuinely free FRED put/call series exists at the index level. |

### Staleness Contract (core of the 2026-06-16 fix)

`available=True` means the VIX level and term-structure values are **both** (a) successfully fetched from FRED **and** (b) from an observation within `_OPTIONS_PROXY_MAX_STALENESS_DAYS` calendar days of the run date. `available=True` with a stale-as-of-2020 observation is **not possible** after this fix. The freshness guard fires immediately after `_parse_latest_observation` returns and before any regime/risk computation, so no fabricated signals reach the lens pipeline.

## Internal Dependencies

- `requests` — HTTP client for FRED API calls.
- `datetime`, `time` — date arithmetic for rolling window + backoff sleeps.
- `os` — `os.environ.get("FRED_API_KEY")` — FRED API key; never hardcoded.
- `logging` — structured log lines for warning (staleness, missing key) and info (successful fetch summary). No FRED response body echoed in log output.

**Called by:**
- `advisors/lens_pipeline.py` — 03:00 off-hours nightly pipeline; the primary production caller.
- `ai_advisor._build_derivatives_section()` — on-demand assembly for the AI Advisor suggest route.
