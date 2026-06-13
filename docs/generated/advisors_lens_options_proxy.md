# advisors/lens_options_proxy

> Derivatives / volatility lens proxy: produces a normalized risk read from free FRED index-vol data (VIXCLS + VXVCLS) with term-structure regime classification and absolute-VIX layering.

**Source:** `advisors/lens_options_proxy.py`
**Last updated:** 2026-06-13

## Overview

`advisors/lens_options_proxy.py` fetches CBOE Volatility Index spot (VIXCLS) and 3-month (VXVCLS) series from FRED — both free, no subscription required — and produces a normalized directional risk read for use as a Market Prism lens input. The module classifies the VIX term-structure shape (contango / backwardation / flat) and layers in the absolute VIX level to emit one of three signals: `risk-off`, `risk-on`, or `neutral`.

The module is off-execution-path: it carries no Flask dependency and is never imported on the 1-minute engine loop. It has no production caller as of 2026-06-13 — it is implemented and documented but not yet wired into `advisors/lens_pipeline.py`. All data access is via bounded retry + explicit HTTP timeout; all error surfaces honor the D-1 contract (type name only, never `str(exc)`).

## Public API

### `_fetch_options_proxy() → dict`

Fetch the derivatives/volatility lens from FRED and return a normalized output dict. Never raises — all exceptions are caught; an unavailable state returns `available=False` with a `reason` key.

**Parameters:** none

**Returns:** `dict` — shape depends on `available`:

| Key | Type | Present when | Description |
|-----|------|-------------|-------------|
| `available` | `bool` | always | `True` when data was obtained successfully. |
| `source` | `str` | always | FRED citation string. |
| `reason` | `str` | `available=False` only | `type(exc).__name__` or a short label (D-1 contract). Never `str(exc)`. |
| `vix_level` | `float` | `available=True` only | Latest spot VIX (VIXCLS). |
| `vix_term_structure` | `dict` | `available=True` only | `{spot, term_3m, ratio, spread, regime}` — see Term Structure below. |
| `risk_read` | `str` | `available=True` only | One of: `"risk-off"`, `"risk-on"`, `"neutral"`. |
| `as_of_date` | `str` | `available=True` only | ISO date of the latest valid observation (VIXCLS date). |

**`vix_term_structure` sub-keys:**

| Key | Type | Description |
|-----|------|-------------|
| `spot` | `float` | VIXCLS (1-month implied vol). |
| `term_3m` | `float` | VXVCLS (3-month implied vol). |
| `ratio` | `float` | `spot / term_3m`; 0.0 when `term_3m == 0`. |
| `spread` | `float` | `spot - term_3m` (positive = backwardation). |
| `regime` | `str` | `"contango"`, `"backwardation"`, or `"flat"`. |

**Example (available):**
```python
result = _fetch_options_proxy()
# {
#   "available": True,
#   "vix_level": 17.43,
#   "vix_term_structure": {
#       "spot": 17.43, "term_3m": 19.12,
#       "ratio": 0.9116, "spread": -1.69, "regime": "contango"
#   },
#   "risk_read": "neutral",
#   "as_of_date": "2026-06-12",
#   "source": "FRED (Federal Reserve Bank of St. Louis): VIXCLS ..."
# }
```

**Example (unavailable — FRED_API_KEY missing):**
```python
result = _fetch_options_proxy()
# {"available": False, "reason": "KeyError", "source": "FRED ..."}
```

## Regime and Risk-Read Logic

### Term-Structure Regime (`_classify_regime`)

Classification is based on the ratio of spot VIX to 3-month VIX, with a flat band to avoid noise-driven flips:

| Condition | Regime |
|-----------|--------|
| `\|spot/term_3m - 1\| <= _FLAT_BAND_RATIO` (±2%) | `"flat"` |
| `spot > term_3m` (and outside flat band) | `"backwardation"` |
| `spot < term_3m` (and outside flat band) | `"contango"` |
| `term_3m <= 0` (defensive) | `"flat"` (avoids fabrication) |

**Practitioner interpretation:** contango (upward-sloping futures curve) signals a calm market; backwardation (inverted curve) signals acute stress — near-term fear exceeds forward pricing.

### Risk Read (`_derive_risk_read`)

The risk read layers absolute VIX level on top of the regime to require both structure and magnitude before emitting a strong signal:

| Condition | `risk_read` |
|-----------|-------------|
| `regime == "backwardation"` AND `vix_level >= _VIX_ELEVATED_THRESHOLD` (20.0) | `"risk-off"` |
| `regime == "contango"` AND `vix_level < _VIX_LOW_THRESHOLD` (15.0) | `"risk-on"` |
| all other combinations (including mixed signals, e.g. backwardation + low VIX) | `"neutral"` |

The neutral fallback is deliberate: mixed evidence (e.g., inverted curve but VIX below 20) is not fabricated into a strong signal.

### Named Thresholds

| Constant | Value | Source / rationale |
|----------|-------|--------------------|
| `_VIX_LOW_THRESHOLD` | `15.0` | Practitioner convention — VIX < 15 = low-fear environment |
| `_VIX_ELEVATED_THRESHOLD` | `20.0` | Practitioner convention — VIX > 20 = elevated stress |
| `_FLAT_BAND_RATIO` | `0.02` | Market convention — < 2% spot/3m deviation is effectively flat |

## Data Source

**FRED (Federal Reserve Bank of St. Louis)** — free, no subscription required.

| Series | FRED ID | Description |
|--------|---------|-------------|
| Spot VIX | `VIXCLS` | CBOE Volatility Index (1-month implied vol) |
| 3-Month VIX | `VXVCLS` | CBOE 3-Month Volatility Index |

FRED API key is read from `os.environ["FRED_API_KEY"]`. If the key is absent, `_fetch_options_proxy` returns `available=False` with `reason="KeyError"` immediately — no network request is made.

Observation endpoint: `https://api.stlouisfed.org/fred/series/observations`

FRED marks unavailable observations with value `"."` — these are skipped by `_parse_latest_observation`, which walks the response in reverse to find the latest valid non-dot entry.

## Retry Policy

Exponential backoff with a hard cap on total cumulative wait:

| Constant | Value | Role |
|----------|-------|------|
| `_OPTIONS_PROXY_MAX_ATTEMPTS` | `3` | Maximum fetch attempts per series (1 initial + 2 retries) |
| `_OPTIONS_PROXY_BACKOFF_BASE_S` | `2.0 s` | Base sleep interval |
| `_OPTIONS_PROXY_BACKOFF_CAP_S` | `16.0 s` | Per-sleep ceiling |
| `MAX_OPTIONS_RETRY_WAIT_SECONDS` | `6.0 s` | Hard ceiling on total cumulative wait (2 + 4 = 6 s for 3 attempts) |
| `_OPTIONS_PROXY_TIMEOUT_S` | `15.0 s` | Per-request HTTP timeout; never relies on urllib3 default (None) |

Retryable HTTP status codes: `{429, 500, 502, 503, 504}`. Non-retryable 4xx responses raise immediately after the first attempt.

## Design Invariants

| Code | Invariant |
|------|-----------|
| CC-2 | Never imported at module level in `alpha_bot_execution.py` — off the 1-minute engine loop. |
| CC-3 | Honest availability — `available=False` with `reason` when FRED is unreachable or returns no usable data. Values are never fabricated. |
| CC-5 | Free APIs only — VIXCLS and VXVCLS on FRED are no-cost. No paid options data feed is used. |
| D-1 | `reason` is always `type(exc).__name__` only. The exception message (`str(exc)`) is never exposed in any return value. |
| AC-12 | `put_call` ratio is omitted — no genuinely free FRED put/call series exists at the index level. |

## Known Limits

- **Regime is term-structure shape only.** The regime classification (`contango` / `backwardation` / `flat`) describes the slope of the VIX futures curve. It is not a direct measure of realized volatility, dealer positioning, or options skew. The risk read adds absolute VIX level as a second filter, but this remains an index-level proxy.
- **No production caller.** As of 2026-06-13, `_fetch_options_proxy` has no caller in `advisors/lens_pipeline.py` or any other production module. The module is fully implemented and tested in isolation but is not yet wired into the nightly pipeline. `ac-12` / not-wired status is intentional per the Prism Phase 4 derivatives lens brief.
- **put/call omitted (AC-12).** No genuinely free FRED series exists for index-level put/call ratio. Adding put/call requires a paid data source; the decision to omit it is a documented design constraint, not a gap.
- **VXVCLS as 3-month proxy.** VXVCLS (CBOE 3-Month Volatility Index) is a 93-day constant-maturity index, not a literal 3-month futures contract. The term structure read is an approximation. For live VIX futures-curve analysis a paid futures data feed would be needed.
- **FRED data lag.** FRED observations are typically published with a 1-business-day lag. The `as_of_date` field reflects the date of the latest available observation, which may be T-1.

## Internal Dependencies

- `requests` — HTTP fetches to FRED (`_fetch_fred_series`)
- `os` — `FRED_API_KEY` environment lookup
- `logging` — structured debug/info/warning log lines
- `time` — exponential backoff sleeps

No imports from `database`, `app`, `ai_advisor`, or any other project module. The lens is intentionally self-contained.
