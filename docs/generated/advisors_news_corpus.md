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

- **Facet A — GDELT tone** (from `lens_gdelt._fetch_gdelt_sentiment`): a single
  `float | None` in `[-1, 1]`, normalized from GDELT `AvgTone`. `build_news_corpus`
  calls `lens_gdelt._fetch_gdelt_sentiment([])` once and extracts `result["tone"]`.

- **Facet B — ranked corpus** (`_fetch_all_feeds` → dedup → score → top-K): up
  to `TOP_K=25` articles from GDELT artlist + 8 RSS/Atom feeds, after cross-source
  deduplication, composite scoring, and topic tagging. GDELT artlist articles are
  obtained from `result["sources"]` (the same single `_fetch_gdelt_sentiment` call
  used for Facet A) via `_normalize_gdelt_articles()`. `_fetch_all_feeds()` is
  RSS-only — no direct GDELT HTTP calls in `news_corpus`.

**Single GDELT call invariant:** `build_news_corpus()` makes exactly ONE
`lens_gdelt._fetch_gdelt_sentiment([])` call per invocation, producing <=2 spaced
GDELT GETs (timelinetone + artlist, already spaced by `_GDELT_INTER_REQUEST_S=6.0s`
inside `lens_gdelt`). This satisfies the <=2-GETs-per-run rate-limit contract.

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

GDELT artlist articles arrive via `lens_gdelt._fetch_gdelt_sentiment` (one call,
normalized by `_normalize_gdelt_articles`). All other sources are RSS/Atom parsed
via `feedparser`.

| Feed name | URL | UA |
|-----------|-----|----|
| `gdelt_artlist` | Via `lens_gdelt._fetch_gdelt_sentiment` (maxrecords=50, sourcelang:eng) | Handled by `lens_gdelt` |
| `google_news_business` | `https://news.google.com/rss/headlines/section/topic/BUSINESS` | `_UA_STD` — domain resolved via `entry.source.href` (publisher URL); falls back to link domain for feeds without a source href |
| `cnbc_markets` | `https://www.cnbc.com/id/10000664/device/rss/rss.html` | `_UA_STD` |
| `marketwatch_top` | `https://feeds.marketwatch.com/marketwatch/topstories/` | `_UA_STD` |
| `yahoo_finance` | `https://finance.yahoo.com/news/rssindex` | `_UA_STD` |
| `fed_press` | `https://www.federalreserve.gov/feeds/press_all.xml` | `_UA_GOV` |
| `bls_latest` | `https://www.bls.gov/feed/bls_latest.rss` | `_UA_GOV` |
| `bea_rss` | `https://apps.bea.gov/rss/rss.xml` | `_UA_GOV` |
| `sec_8k` | `https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&output=atom&count=10` | `_UA_GOV` |

`.gov` feeds (SEC, Fed, BLS, BEA) require a descriptive contact UA; they return
403 on bare `requests` default UA. `_UA_GOV = "PlanetStopper/1.0 paulmgreaney@gmail.com"`.
`_UA_STD = "PlanetStopper/1.0 (market-news lens; paulmgreaney@gmail.com)"`.

## Scoring Constants (all named, no magic numbers)

All weights and thresholds are sourced from `feature-plans/news-sources-reference.md`.

| Constant | Value | Purpose |
|----------|-------|---------|
| `W_RECENCY` | `0.40` | Weight for recency component; `exp(-delta_hours / TAU_HOURS)` |
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
Recency defaults to `0.5` on unparseable dates. Timestamps computed with
`datetime.datetime.now(datetime.timezone.utc)` (Python 3.12+ compatible).

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
| `_normalize_gdelt_articles(sources_raw)` | Pure normalizer: converts GDELT `sources` field records `{url, seendate, title, domain}` to the common article shape; `source_feed="gdelt_artlist"` |
| `_fetch_rss_feed(name, url, ua)` | Fetch + normalize one RSS/Atom feed via `feedparser`; per-feed isolation. Published date sourced from `entry.published_parsed` (feedparser struct_time) via `calendar.timegm` + tz-aware `datetime.fromtimestamp` → ISO `%Y-%m-%dT%H:%M:%S` string, making recency decay functional across all feeds; falls back to raw `published`/`updated` string when `published_parsed` is absent. Domain resolved via `entry.source.href` when present (Google News wrapper→publisher); falls through to `_extract_domain(link)` for other feeds. |
| `_fetch_all_feeds()` | Orchestrates all RSS/Atom feeds (RSS-only; no direct GDELT GETs here) |

## Design Invariants

**D-1.** Every error path returns `type(exc).__name__` only — never `str(exc)`.

**Single GDELT call.** `build_news_corpus` calls `lens_gdelt._fetch_gdelt_sentiment([])` once
inline. Both tone (Facet A) and artlist articles (Facet B input) come from that one call.
No direct GDELT HTTP requests originate in `news_corpus` itself.

**Per-feed isolation.** One RSS feed failing (403, timeout, parse error) degrades
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

`_build_sentiment_section` calls `news_corpus.build_news_corpus()` as its sole
entry point. The direct `lens_gdelt._fetch_gdelt_sentiment` call that existed
in the cycle-1 interim has been removed from the section — GDELT data reaches
the section only through `build_news_corpus`.

The GDELT test seam is preserved: `build_news_corpus` delegates to
`lens_gdelt._fetch_gdelt_sentiment` internally, so patching `_fetch_gdelt_sentiment`
propagates into `_build_sentiment_section`.

On the success path, the payload carries:
- `tone_score`: from `corpus_result["tone"]`.
- `corpus`: the ranked article list (up to `TOP_K`).
- `events`: mapped from corpus articles to legacy shape `{title, domain, seendate}`
  for render compatibility (AC-5).
- `article_count`: `len(corpus)`.

`sources[]` is populated by calling `build_citation({title, url, published, lens})`
per corpus article; `None` returns are filtered.

**Warehouse persistence (DW-1):** `_build_sentiment_section` calls
`lens_warehouse.persist_lens_snapshot` on both paths (CC-2 lazy import, wrapped
in `try/except` — warehouse errors never surface to callers):
- **Unavailable path:** `{lens="sentiment", source="news_corpus", available=False, raw_payload={"reason": reason}}`
- **Success path:** `{lens="sentiment", source="news_corpus", available=True, raw_payload={"tone_score": tone_score, "corpus_size": len(corpus)}}`

## Testing

- **Test files:** `tests/ai_advisor/test_news_corpus.py`,
  `tests/ai_advisor/test_lens_gdelt.py` (updated for maxrecords=50)
- **Fixtures:** `tests/fixtures/ai_advisor/` — `gdelt_artlist_maxrecords50.json`,
  `google_news_business.xml`, `fed_press.xml`, `bea_rss.xml`, `sec_8k_atom.xml`,
  `news_corpus_feeds_provenance.json`
- **Mocking strategy:** All CI tests mock `requests.get` and `feedparser.parse`.
  No live network calls in the default run.
- **Single-GDELT-path tests (cycle-2):** Assert `_fetch_gdelt_sentiment` is called
  at most once per `_build_sentiment_section` call; assert `_fetch_gdelt_sentiment`
  is NOT called directly from `_build_sentiment_section` when `news_corpus` is
  available; `test_build_sentiment_section_total_gdelt_gets_at_most_two` asserts
  total GDELT GETs <=2 through the real production path; tombstone test
  `test_fetch_gdelt_tone_is_removed_dead_code` asserts `_fetch_gdelt_tone` no
  longer exists in the module.
- **Warehouse persistence tests (cycle-2):** Assert `persist_lens_snapshot` is called
  with `lens="sentiment"` on both the success and unavailable paths; assert payload
  shape carries `tone` and `corpus_size` keys (no hardcoded values).
- **Total suite:** GREEN at fdf33df (AC-3 recency + Google News domain fix; 177 passed / 0 failed).
