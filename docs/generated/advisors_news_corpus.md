---
feature: multi-source-news-corpus
status: complete
related_files:
  - advisors/news_corpus.py
  - advisors/lens_gdelt.py
  - ai_advisor.py
  - requirements.txt
  - feature-plans/lens-news-events-upgrade.md
  - feature-plans/news-sources-reference.md
api_surface: [build_news_corpus]
components: []
models: []
validators: []
tags: [news-corpus, sentiment, rss, gdelt, feedparser, lens, advisory, off-execution-path]
---

# advisors/news_corpus

> Multi-source market-news corpus builder: produces a two-facet result combining
> GDELT aggregate tone (Facet A) with a ranked, cross-source-deduplicated,
> topic-tagged article corpus drawn from GDELT artlist + 8 RSS/Atom feeds
> (Facet B). Primary consumer: `ai_advisor._build_sentiment_section`.

**Source:** `advisors/news_corpus.py`
**Contract reference:** `feature-plans/lens-news-events-upgrade.md` AC-1..AC-7;
`feature-plans/news-sources-reference.md` (scoring weights + authority table)
**Last updated:** 2026-06-18

## Overview

`advisors/news_corpus.py` is the sentiment lens producer for the Market Prism
nightly pipeline. It makes no assumptions about which feeds are available — each
feed failure degrades that source only; the result is available as long as either
the GDELT tone or at least one article was fetched.

**Two-facet design:**

- **Facet A — GDELT tone** (`_fetch_gdelt_tone`): a single `float | None` in
  `[-1, 1]`, normalized from GDELT `AvgTone`. Independent of the corpus fetch.
  Uses `lens_gdelt._GDELT_TONE_URL` via a lazy import (CC-2), adding an explicit
  `User-Agent` header (`_UA_STD`).

- **Facet B — ranked corpus** (`_fetch_all_feeds` → dedup → score → top-K): up
  to `TOP_K=25` articles from GDELT artlist + 8 RSS/Atom feeds, after cross-source
  deduplication, composite scoring, and topic tagging.

**Dependency:** `feedparser>=6.0` (added to `requirements.txt`).

## Public API

### `build_news_corpus() -> dict`

Fetch multi-source news corpus and GDELT tone. Never raises.

**Parameters:** None.

**Returns:**

| Key | Type | Description |
|-----|------|-------------|
| `available` | `bool` | `True` iff `tone is not None OR bool(corpus)`. |
| `tone` | `float \| None` | GDELT aggregate AvgTone normalized to `[-1, 1]`; `None` on any tone-fetch failure. |
| `corpus` | `list[dict]` | Up to `TOP_K` ranked, deduped, topic-tagged article dicts; `[]` when no articles fetched. |
| `reason` | `str \| None` | `"no_news_events"` when `available=False`; `None` on success. |

Each article dict in `corpus` has keys: `url`, `title`, `published`, `domain`,
`source_feed`, `topics` (list of topic labels), `score` (composite float).

**Never raises.** All exceptions are caught per-feed; top-level exceptions in
`build_news_corpus` itself are also caught by the outer defense-in-depth
`try/except` in `ai_advisor._build_sentiment_section`.

## Feed Sources

GDELT artlist is fetched via JSON (not RSS); all other sources are RSS/Atom parsed
via `feedparser`.

| Feed name | URL | UA |
|-----------|-----|----|
| `gdelt_artlist` | `lens_gdelt._GDELT_ARTLIST_URL` (maxrecords=50) | `_UA_STD` |
| `google_news_business` | `https://news.google.com/rss/headlines/section/topic/BUSINESS` | `_UA_STD` |
| `cnbc_markets` | `https://www.cnbc.com/id/10000664/device/rss/rss.html` | `_UA_STD` |
| `marketwatch_top` | `https://feeds.marketwatch.com/marketwatch/topstories/` | `_UA_STD` |
| `yahoo_finance` | `https://finance.yahoo.com/news/rssindex` | `_UA_STD` |
| `fed_press` | `https://www.federalreserve.gov/feeds/press_all.xml` | `_UA_GOV` |
| `bls_latest` | `https://www.bls.gov/feed/bls_latest.rss` | `_UA_GOV` |
| `bea_rss` | `https://apps.bea.gov/rss/xml` | `_UA_GOV` |
| `sec_8k` | `https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&output=atom&count=10` | `_UA_GOV` |

`.gov` feeds (SEC, Fed, BLS, BEA) require a descriptive contact UA; they return
403 on bare `requests` default UA. `_UA_GOV = "PlanetStopper/1.0 paulmgreaney@gmail.com"`.
`_UA_STD = "PlanetStopper/1.0 (market-news lens; paulmgreaney@gmail.com)"`.

## Scoring Constants (all named, no magic numbers)

All weights and thresholds are sourced from `feature-plans/news-sources-reference.md`.

| Constant | Value | Purpose |
|----------|-------|---------|
| `W_RECENCY` | `0.40` | Weight for recency component; `exp(-Δt_hours / TAU_HOURS)` |
| `W_RELEVANCE` | `0.35` | Weight for relevance component; `min(1.0, keyword_hits / 3)` |
| `W_AUTHORITY` | `0.25` | Weight for domain authority component |
| `TAU_HOURS` | `24.0` | Recency decay half-life in hours |
| `TOP_K` | `25` | Max corpus size after scoring (AC-3) |
| `DEDUP_JACCARD_THRESHOLD` | `0.85` | Title token-set Jaccard threshold for same-story dedup (AC-3) |
| `_PER_DOMAIN_CAP` | `3` | Max articles per domain in final corpus (AC-3) |

Weights sum to 1.0: `W_RECENCY + W_RELEVANCE + W_AUTHORITY = 1.0`.

## Authority Table (`SOURCE_AUTHORITY`)

Domain authority scores used in the composite ranking. Unknown domains default
to `0.4`.

| Domain | Authority |
|--------|-----------|
| `federalreserve.gov`, `sec.gov`, `bls.gov`, `bea.gov`, `apps.bea.gov` | 1.0 |
| `reuters.com`, `apnews.com` | 0.9 |
| `cnbc.com`, `marketwatch.com` | 0.7 |
| `yahoo.com`, `finance.yahoo.com`, `nasdaq.com` | 0.6 |
| Unknown | 0.4 |

## Topic Tagging

`_tag_topics(title)` assigns one or more topic labels to each article based on
title keyword matching (pure stdlib, lowercase match). Multi-label is allowed.
Defaults to `["broad-sentiment"]` when no keywords match.

| Topic | Example keywords |
|-------|-----------------|
| `macro` | federal reserve, fed, fomc, rate, inflation, cpi, ppi, gdp, jobs, payroll, treasury, yield, recession |
| `fundamentals` | earnings, revenue, guidance, eps, dividend, buyback, 8-k, 10-k, 10-q |
| `technicals` | rally, selloff, breakout, support, resistance, moving average, volume, breadth |
| `derivatives` | options, vix, futures, gamma, open interest, expiry, hedge |

## Deduplication Pipeline (`_dedup`)

Three-step cross-source dedup applied before scoring:

1. **URL dedup** — canonical URL (strip query string + fragment via `_canonical_url`);
   highest-authority article wins per canonical URL.
2. **Title Jaccard dedup** — token-set Jaccard >= `DEDUP_JACCARD_THRESHOLD` (0.85)
   identifies same-story duplicates; highest-authority article is kept.
3. **Per-domain cap** — at most `_PER_DOMAIN_CAP` (3) articles per domain in the
   final output.

## Composite Scoring

```python
score = W_RECENCY * exp(-delta_hours / TAU_HOURS)
      + W_RELEVANCE * min(1.0, keyword_hits / 3)
      + W_AUTHORITY * SOURCE_AUTHORITY.get(domain, 0.4)
```

Articles are sorted descending by score; top `TOP_K` are kept as the corpus.
Recency defaults to `0.5` on unparseable dates.

## Internal Helpers

| Helper | Purpose |
|--------|---------|
| `_authority(domain)` | Lookup in `SOURCE_AUTHORITY`; default `0.4` |
| `_recency(published_str, now)` | Exponential decay; default `0.5` on parse failure |
| `_relevance(title)` | Keyword hit count / 3, capped at 1.0 |
| `_score_article(article, now)` | Composite of the three above with named weights |
| `_tag_topics(title)` | Multi-label topic assignment; defaults to `["broad-sentiment"]` |
| `_extract_domain(url)` | `urllib.parse` netloc, strips `www.` |
| `_canonical_url(url)` | Strip query + fragment for dedup comparison |
| `_jaccard(title_a, title_b)` | Token-set Jaccard similarity |
| `_dedup(articles)` | Three-step cross-source dedup pipeline |
| `_fetch_gdelt_tone()` | Facet A — GDELT tone scalar; lazy-imports `lens_gdelt` (CC-2) |
| `_fetch_gdelt_artlist()` | Fetch + normalize GDELT artlist JSON; lazy-imports `lens_gdelt` (CC-2) |
| `_fetch_rss_feed(name, url, ua)` | Fetch + normalize one RSS/Atom feed via `feedparser`; per-feed isolation |
| `_fetch_all_feeds()` | Orchestrates artlist + all RSS feeds; per-feed isolation |

## Design Invariants

**D-1.** Every error path returns `type(exc).__name__` only — never `str(exc)`.

**Per-feed isolation.** One feed failing (403, timeout, parse error) degrades
that feed only. `_fetch_all_feeds` always returns a list; absent feeds contribute
`[]` silently.

**Never-raises.** `build_news_corpus()` catches all exceptions; callers never
see a throw. The `_build_sentiment_section` caller adds a second defense-in-depth
`try/except` around the import + call.

**Off-execution-path (CC-2).** `news_corpus` is never imported at module level
in `alpha_bot_execution.py`. `_build_sentiment_section` lazy-imports it with
`from advisors import news_corpus`.

**No magic numbers.** Every weight, threshold, and cap is a named module-level
constant with an inline source comment.

## Wiring into `ai_advisor._build_sentiment_section`

`_build_sentiment_section` uses a two-path architecture:

1. **Primary:** `news_corpus.build_news_corpus()` — produces `tone` + `corpus`.
2. **Fallback:** `lens_gdelt._fetch_gdelt_sentiment([])` — GDELT-only path
   preserved as a test seam and fallback. Patching
   `lens_gdelt._fetch_gdelt_sentiment` in tests propagates into the section.

`available=True` if either source produced data. The payload carries:
- `tone_score`: from `corpus_result` when corpus is available, else from
  `gdelt_result`.
- `corpus`: the ranked article list from `news_corpus` (empty list when corpus
  unavailable).
- `events`: mapped from corpus articles (legacy shape `{title, domain, seendate}`)
  when corpus is non-empty; falls back to `gdelt_result["events"]` when corpus
  is empty but GDELT has events (AC-5 render compatibility).

## Testing

- **Test files:** `tests/ai_advisor/test_news_corpus.py` (new),
  `tests/ai_advisor/test_lens_gdelt.py` (updated for maxrecords=50)
- **Fixtures:** `tests/fixtures/ai_advisor/` — `gdelt_artlist_maxrecords50.json`,
  `google_news_business.xml`, `fed_press.xml`, `bea_rss.xml`, `sec_8k_atom.xml`,
  `news_corpus_feeds_provenance.json`
- **Mocking strategy:** All CI tests mock `requests.get` and `feedparser.parse`.
  No live network calls in the default run.
- **Per-feed isolation tests:** each feed failure path asserts the corpus is not
  empty when other feeds succeed.
- **Total suite:** 107 passed / 0 failed at GREEN commit b93b724.

## Known Gaps / Deferred Work

- **Serial fetching.** `_fetch_all_feeds` fetches all 9 sources serially. A
  future improvement could parallelize with `ThreadPoolExecutor`, subject to
  rate-limit considerations on `.gov` feeds.
- **No warehouse persistence.** `news_corpus` does not call `lens_warehouse`
  directly. Persistence of the corpus to the nightly warehouse is deferred.
- **GDELT artlist UA.** The artlist fetch uses `_UA_STD`. GDELT does not require
  a `.gov`-style contact UA, but if GDELT begins 403-ing on standard UAs,
  switching to `_UA_GOV` is the fix.
