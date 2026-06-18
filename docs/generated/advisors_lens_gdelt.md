---
feature: gdelt-news-events-sentiment-producer
status: complete
related_files:
  - advisors/lens_gdelt.py
  - ai_advisor.py
  - tests/ai_advisor/test_lens_gdelt.py
  - tests/fixtures/math/gdelt_timelinetone_response.json
  - tests/fixtures/math/gdelt_artlist_response.json
  - .claude/gdelt-contract.md
api_surface: [_fetch_gdelt_sentiment, _extract_events]
components: []
models: []
validators: []
tags: [gdelt, sentiment, news-events, tone, lens, advisory, off-execution-path]
---

# advisors/lens_gdelt

> GDELT 2.0 sentiment/news-events producer: surfaces real English-language market
> news events (title/domain/date) as the primary signal, with normalized tone
> secondary. Used by `ai_advisor._build_sentiment_section` and fed into the Market
> Prism nightly synthesis via `lens_pipeline.py`.

**Source:** `advisors/lens_gdelt.py`
**Contract reference:** `.claude/gdelt-contract.md` (pinned 2026-06-15)
**Last updated:** 2026-06-18

## Overview

`advisors/lens_gdelt.py` implements the GDELT market-sentiment/news-events lens.
It makes two HTTP GETs to the GDELT 2.0 DOC API: a `timelinetone` request for a
tone signal and an `artlist` request for source articles. Both queries include
`sourcelang:eng` to restrict results to English-language sources.

The primary output is a ranked, domain-deduplicated list of up to `_GDELT_MAX_EVENTS`
(7) English-language news events extracted from the artlist response. Tone is a
secondary signal: mean `AvgTone` across timeline data points, normalized from
GDELT's `[-100, 100]` native scale to `[-1.0, 1.0]`.

**Availability gate (events-OR-tone):** `available=True` when at least one of
`events` or `tone` is present. Both signals can succeed or fail independently.
Only HTTP-level failures on the tone endpoint short-circuit early; artlist failure
degrades to `events=[]` while preserving any valid tone. This replaced the prior
tone-only availability gate where `available=True, tone=None` was a reachable
(forbidden) state.

The module is wired into `ai_advisor._build_sentiment_section` (lazy import, CC-2
import-boundary invariant). The `"events"` key is surfaced on the success-path
payload so the Market Prism synthesizer can include real news headlines.

## Public API

### `_fetch_gdelt_sentiment(universe: list[str]) -> dict`

Fetch GDELT sentiment and news events for the configured universe.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `universe` | `list[str]` | Ticker symbols. v1 ignores this list — the query is always universe-level `stock+market+finance+sourcelang:eng`. Per-ticker signals are deferred to v2 (`per_ticker` is always `None`). |

**Returns:** A dict with the following keys:

| Key | Type | Description |
|-----|------|-------------|
| `available` | `bool` | `True` when at least one signal (events OR tone) is present. |
| `tone` | `float \| None` | Normalized mean AvgTone in `[-1.0, 1.0]`; `None` when tone extraction failed or the timeline was empty. |
| `per_ticker` | `None` | Always `None` in v1 (universe-level only). |
| `source` | `str` | Human-readable citation string; always present, even on the unavailable path. |
| `sources` | `list \| None` | Raw artlist citations (url/title/seendate/domain dicts). `None` when the whole producer is unavailable; `[]` when artlist failed or returned no articles. |
| `events` | `list` | Ranked, domain-deduplicated English-language news events (title/domain/seendate dicts), capped at `_GDELT_MAX_EVENTS`. Always present; `[]` on any unavailable path. |
| `reason` | `str \| None` | `None` on success. On failure: a named label from §4 or `type(exc).__name__` only (D-1). |

**Never raises.** All exceptions yield `available=False` with a reason.

### `_extract_events(sources_raw: list[dict]) -> list[dict]`

Extract a ranked, domain-deduplicated list of English-language events from raw
artlist article dicts. Internal helper called after a successful artlist fetch.

**Processing steps (in order):**
1. Filter: keep only articles where `language == "English"`.
2. Sort: most-recent-first by `seendate` (GDELT format `YYYYMMDDTHHmmssZ` — lexicographic sort is correct for this zero-padded format).
3. Deduplicate: keep the most-recent article per domain.
4. Cap: return at most `_GDELT_MAX_EVENTS` entries.

**Returns:** `list[dict]`, each with keys `title`, `domain`, `seendate`. Never raises.

## Constants (contract §5 — all load-bearing, never magic numbers)

| Constant | Value | Source |
|----------|-------|--------|
| `_GDELT_MAX_ATTEMPTS` | `4` | Contract §5 Amendment 1. 1 initial + 3 retries. |
| `_GDELT_BACKOFF_BASE_S` | `20.0` | Contract §5 Amendment 1. 4x margin above GDELT's 5 s/req floor. Prior value `1.0` caused a persistent-429 PC crash. |
| `_GDELT_BACKOFF_CAP_S` | `60.0` | Contract §5 Amendment 1. Caps exponential ramp: 20 s -> 40 s -> 60 s. |
| `_GDELT_TIMEOUT_S` | `15.0` | Contract §5. Explicit connect+read timeout; avoids urllib3 `None` default. |
| `_GDELT_INTER_REQUEST_S` | `6.0` | Contract §5 Amendment 1. Sleep between tone GET and artlist GET; both share GDELT's per-IP rate-limit window. |
| `_GDELT_MAX_EVENTS` | `7` | Feature plan AC-2. Caps events list for prompt-budget control (~5-8 events target). |

## Endpoint URLs

Both endpoints include `sourcelang:eng` to restrict to English-language sources
(added in the news-events upgrade; prior versions had no language filter, allowing
non-English and null-language articles to pass through).

| Endpoint | URL constant |
|----------|-------------|
| Tone signal | `_GDELT_TONE_URL` — `timelinetone` mode |
| Article citations + events | `_GDELT_ARTLIST_URL` — `artlist` mode, `maxrecords=50`, `timespan=1440` (last 24 hours); bumped 10→50 in the multi-source upgrade to feed the `news_corpus` artlist fetch |

`_GDELT_ARTLIST_URL` is consumed by `advisors/news_corpus._fetch_gdelt_artlist`
(via a CC-2 lazy import of `lens_gdelt`) — the multi-source corpus builder reuses
the pinned URL constant to ensure consistent query parameters.

## Design Invariants

**D-1 — Reason is `type(exc).__name__` only.** Never `str(exc)` or the message
body. This prevents internal host details, URLs, or credentials from leaking
through the reason field.

**Events-OR-tone availability gate.** `available=True` when `bool(events) OR tone is not None`.
The prior bug allowed `available=True, tone=None, events` absent — this is now
structurally impossible. HTTP-level tone failures (rate_limited, gdelt_fetch_failed,
exception) return early before the artlist call. Tone-extraction failures (empty
timeline, non-numeric data) set `tone=None` but continue to the artlist call — if
events are found, the result is `available=True` with `tone=None`.

**Named reason labels (§4):**

| Condition | `reason` |
|-----------|----------|
| HTTP 429 after all retries | `"rate_limited"` |
| Non-200 non-429 HTTP status | `"gdelt_fetch_failed"` |
| Empty timeline or no numeric values, and no events | `"no_tone_data"` |
| Artlist reached + articles key present, neither tone nor events available | `"no_news_events"` |
| Any caught exception | `type(exc).__name__` |

**Bounded retry.** 429 responses on the tone endpoint trigger exponential backoff
`min(BASE x 2^i, CAP)` up to `_GDELT_MAX_ATTEMPTS` total calls. No other HTTP
status triggers a retry. Artlist is never retried.

**Artlist is best-effort.** A failed artlist call does not degrade `available`
or `tone`. On artlist failure: `sources=[]`, `events=[]`. The tone signal (if
present) is returned as `available=True`.

**Inter-request spacing.** `time.sleep(_GDELT_INTER_REQUEST_S)` is called after
the tone GET (success or empty-data) and before the artlist GET. Both endpoints
share GDELT's per-IP window (1 req / 5 s); skipping this sleep would re-trip 429
on the artlist call.

**Language filter.** `_extract_events` keeps only articles where `language == "English"`.
Articles with a missing or non-English language field are dropped. The `sourcelang:eng`
query parameter is a server-side pre-filter; the client-side language check is a
defense-in-depth guard against API drift.

## Tone Normalization

```python
raw = [p["value"] for p in timeline[0]["data"] if isinstance(p.get("value"), (int, float))]
mean_tone = sum(raw) / len(raw)
tone = float(max(-1.0, min(1.0, mean_tone / 100.0)))
```

GDELT `AvgTone` is in `[-100, 100]`. Dividing by 100 maps to `[-1, 1]`.
`max(-1, min(1, ...))` clamps any extreme value. Empty timeline or no numeric
values sets `tone=None` and continues to the artlist call.

## Wiring into `ai_advisor._build_sentiment_section`

`_build_sentiment_section` uses a two-path architecture (multi-source upgrade):

- **Primary:** lazy-imports `advisors.news_corpus` (CC-2) and calls
  `news_corpus.build_news_corpus()` for the full two-facet result.
- **Fallback / test seam:** lazy-imports `advisors.lens_gdelt` (CC-2) and calls
  `lens_gdelt._fetch_gdelt_sentiment([])`. Patching `_fetch_gdelt_sentiment`
  in tests propagates into `_build_sentiment_section`, preserving the test seam.

The `events` field in the payload is mapped from corpus articles when
`news_corpus` produced a non-empty corpus (legacy shape for render compatibility,
AC-5); falls back to `gdelt_result["events"]` when the corpus is empty but GDELT
has events. The independent `_fetch_with_backoff` artlist call that existed in
the GDELT-only interim version has been removed.

## Testing

- **Test file:** `tests/ai_advisor/test_lens_gdelt.py`
- **Fixtures:** `tests/fixtures/math/gdelt_timelinetone_response.json`,
  `tests/fixtures/math/gdelt_artlist_response.json`
- **Fixture provenance:** schema-derived-with-validator from real 2026-06-14
  HTTP 200 responses. Runtime validators (`validate_timelinetone_shape`,
  `validate_artlist_shape`) guard fixtures against schema drift.
- **Mocking strategy:** All CI tests mock `requests.get` and `time.sleep`.
  No live GDELT calls in the default run. `@pytest.mark.live` tests are excluded.
- **Property tests:** `hypothesis` verifies tone in `[-1, 1]` for any
  `AvgTone` values in `[-100, 100]`, and the honest-availability invariant across
  multiple failure scenarios.
- **Test run command:**
  ```
  pytest tests/ai_advisor/test_lens_gdelt.py -p no:xdist -o "addopts=" -m "not live and not slow and not perf"
  ```

## Known Gaps / Deferred Work

- **v1 is universe-level only.** `per_ticker` is always `None`. Per-ticker tone
  signals require per-symbol queries, each consuming the rate-limit budget
  (>= 6 s/ticker). Deferred to v2.
- **No artlist retry.** Artlist failures yield `events=[]`, `sources=[]` silently.
  A future improvement could retry artlist on 429 with the same bounded-backoff logic.
- **Serial artlist + tone.** `_fetch_gdelt_tone` and `_fetch_gdelt_artlist`
  (called from `news_corpus`) both hit GDELT endpoints, each with its own
  `time.sleep` or inter-request delay. The combined delay is bounded by
  `_GDELT_INTER_REQUEST_S` + the `requests.get` timeout. No retry on artlist;
  tone retry is bounded by `_GDELT_MAX_ATTEMPTS`.
