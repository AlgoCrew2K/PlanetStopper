---
feature: gdelt-tone-sentiment-producer
status: complete
related_files:
  - advisors/lens_gdelt.py
  - tests/ai_advisor/test_lens_gdelt.py
  - tests/fixtures/math/gdelt_timelinetone_response.json
  - tests/fixtures/math/gdelt_artlist_response.json
  - .claude/gdelt-contract.md
api_surface: [_fetch_gdelt_sentiment]
components: []
models: []
validators: []
tags: [gdelt, sentiment, tone, lens, advisory, off-execution-path]
---

# advisors/lens_gdelt

> GDELT 2.0 tone/sentiment producer: fetches normalized market-sentiment tone
> from the free GDELT DOC API (no key required) and returns structured citations
> from the artlist endpoint. Used by `lens_pipeline.py` as an input to the
> Market Prism synthesis.

**Source:** `advisors/lens_gdelt.py`
**Contract reference:** `.claude/gdelt-contract.md` (pinned 2026-06-15)
**Last updated:** 2026-06-15

## Overview

`advisors/lens_gdelt.py` implements the GDELT market-sentiment lens. It makes
two HTTP GETs to the GDELT 2.0 DOC API: a `timelinetone` request for the tone
signal and an `artlist` request for source citations. The tone signal is the mean
`AvgTone` across timeline data points, normalized from GDELT's `[-100, 100]`
native scale to `[-1.0, 1.0]`.

The module is off-execution-path and has no production caller yet -- it is
scaffolded infrastructure (analogous to `advisors/lens_warehouse.py`). The
intended wiring is a lazy import inside `ai_advisor._build_sentiment_section`
(CC-2 import-boundary invariant), deferred to a follow-on cycle; the current
`_build_sentiment_section` still uses an older inline GDELT artlist fetch.

**Prior bug fixed in this cycle (gdelt-diagnosis.md §1):** The original
implementation read `entry.get("value")` from the series-wrapper object
`{series, data}`. That object has no `"value"` key at that level — the real
tone values are nested inside `timeline[0]["data"][k]["value"]`. The result was
always `tone=None` with `available=True`, which is the forbidden state under the
honest-availability contract (§4).

## Public API

### `_fetch_gdelt_sentiment(universe: list[str]) → dict`

Fetch GDELT tone/sentiment for the configured universe.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `universe` | `list[str]` | Ticker symbols. v1 ignores this list — the query is always universe-level `stock+market+finance`. Per-ticker signals are deferred to v2 (`per_ticker` is always `None`). |

**Returns:** A dict with the following keys:

| Key | Type | Description |
|-----|------|-------------|
| `available` | `bool` | `True` when tone was successfully extracted. Hard invariant: `tone is None ⟹ available is False`. |
| `tone` | `float \| None` | Normalized mean AvgTone in `[-1.0, 1.0]`; `None` when unavailable. |
| `per_ticker` | `None` | Always `None` in v1 (universe-level only). |
| `source` | `str` | Human-readable citation string; always present, even on the unavailable path. |
| `sources` | `list \| None` | Artlist citations. `None` when the whole producer is unavailable. `[]` when tone succeeded but artlist failed or returned no articles. |
| `reason` | `str \| None` | `None` on success. On failure: a named label from §4 or `type(exc).__name__` only (D-1). |

**Never raises.** All exceptions yield `available=False` with a reason.

## Constants (contract §5 — all load-bearing, never magic numbers)

| Constant | Value | Source |
|----------|-------|--------|
| `_GDELT_MAX_ATTEMPTS` | `4` | Contract §5 Amendment 1. 1 initial + 3 retries. |
| `_GDELT_BACKOFF_BASE_S` | `20.0` | Contract §5 Amendment 1. 4× margin above GDELT's 5 s/req floor. Prior value `1.0` caused a persistent-429 PC crash. |
| `_GDELT_BACKOFF_CAP_S` | `60.0` | Contract §5 Amendment 1. Caps exponential ramp: 20 s → 40 s → 60 s. |
| `_GDELT_TIMEOUT_S` | `15.0` | Contract §5. Explicit connect+read timeout; avoids urllib3 `None` default. |
| `_GDELT_INTER_REQUEST_S` | `6.0` | Contract §5 Amendment 1. Sleep between tone GET and artlist GET; both share GDELT's per-IP rate-limit window. |

## Design Invariants

**D-1 — Reason is `type(exc).__name__` only.** Never `str(exc)` or the message
body. This prevents internal host details, URLs, or credentials from leaking
through the reason field.

**Honest availability.** `available=True` implies `tone` is a `float` in
`[-1.0, 1.0]` and `reason` is `None`. `available=False` implies `tone` is `None`
and `reason` is one of the named labels (§4) or an exception class name.

**Named reason labels (§4):**

| Condition | `reason` |
|-----------|----------|
| HTTP 429 after all retries | `"rate_limited"` |
| Non-200 non-429 HTTP status | `"gdelt_fetch_failed"` |
| Empty timeline or no numeric values | `"no_tone_data"` |
| Any caught exception | `type(exc).__name__` |

**Bounded retry.** 429 responses trigger exponential backoff
`min(BASE × 2ⁱ, CAP)` up to `_GDELT_MAX_ATTEMPTS` total calls. No other HTTP
status triggers a retry. A 200 with empty data is definitively `no_tone_data`
and is not retried.

**Artlist is best-effort.** A failed artlist call does not degrade
`available` or `tone`. On artlist failure: `sources=[]`, `available=True`.

**Inter-request spacing.** `time.sleep(_GDELT_INTER_REQUEST_S)` is called
after successful tone extraction and before the artlist GET. Both endpoints
share GDELT's per-IP window (1 req / 5 s); skipping this sleep would re-trip
429 on the artlist call.

## Tone Normalization

```
raw = [p["value"] for p in timeline[0]["data"] if isinstance(p.get("value"), (int, float))]
mean_tone = sum(raw) / len(raw)
tone = float(max(-1.0, min(1.0, mean_tone / 100.0)))
```

GDELT `AvgTone` is in `[-100, 100]`. Dividing by 100 maps to `[-1, 1]`.
`max(-1, min(1, ...))` clamps any extreme value.

## Testing

- **Test file:** `tests/ai_advisor/test_lens_gdelt.py` (47 tests, 2 live-excluded)
- **Fixtures:** `tests/fixtures/math/gdelt_timelinetone_response.json`,
  `tests/fixtures/math/gdelt_artlist_response.json`
- **Fixture provenance:** schema-derived-with-validator from real 2026-06-14
  HTTP 200 captured in `gdelt-diagnosis.md`. Runtime validators
  (`validate_timelinetone_shape`, `validate_artlist_shape`) guard the fixtures
  against schema drift.
- **Mocking strategy:** All CI tests mock `requests.get` and `time.sleep`.
  No live GDELT calls in the default run. `@pytest.mark.live` tests are excluded.
- **Property tests:** `hypothesis` verifies tone ∈ `[-1, 1]` for any
  `AvgTone` values in `[-100, 100]`, and the honest-availability invariant across
  multiple failure scenarios.
- **Test run command:**
  ```
  pytest tests/ai_advisor/test_lens_gdelt.py -p no:xdist -o "addopts=" -m "not live and not slow and not perf"
  ```

## Known Gaps / Deferred Work

- **v1 is universe-level only.** `per_ticker` is always `None`. Per-ticker tone
  signals require per-symbol queries, each consuming the rate-limit budget
  (≥ 6 s/ticker). Deferred to v2.
- **No artlist retry.** Artlist failures yield `sources=[]` silently. A future
  improvement could retry artlist on 429 with the same bounded-backoff logic.
- **No production caller yet.** `_fetch_gdelt_sentiment` is not imported by any
  production module on this branch. The wiring into `ai_advisor._build_sentiment_section`
  is deferred. Do not reference `advisors/lens_gdelt.py` from production code until
  the caller is wired in a follow-on cycle.
