# Feature: Multi-Source Market-News Lens (GDELT tone facet + ranked/topic-tagged article corpus)
Status: ready
Created: 2026-06-18 (re-scoped from GDELT-only after operator directive: multi-source, GDELT two-facet)

## Summary

The Market Prism "sentiment" lens is the softest, most subjective, and (per operator) most
crucial lens. Today it is GDELT-only: a single aggregate tone scalar, with an unfiltered query
that pulled foreign-language noise (live row 77: Chinese articles, `tone_score=null`) and threw
the actual articles away as citation URLs. This rebuilds it into a **two-facet, multi-source**
lens:

- **Facet A — GDELT aggregate TONE (independent, ALWAYS-VALID, NEVER ranked):** the existing
  `timelinetone` mean-AvgTone normalized [-100,100]→[-1,1], with the existing bounded-429
  backoff. The un-killable floor: even if every article feed fails, the lens still emits this.
- **Facet B — a single ranked, deduped, topic-tagged ARTICLE CORPUS** drawn from MANY free,
  keyless sources (GDELT `artlist` + Google News RSS + CNBC + MarketWatch + Yahoo + Fed + BLS +
  BEA + SEC). GDELT's articles are just ONE input feed here — validated/deduped/scored/tagged
  identically to the RSS feeds (no special treatment). The corpus is topic-tagged
  (macro/fundamentals/technicals/derivatives/broad-sentiment) so it can be routed as **cross-lens
  context** to the other analysts during Q&A/debate (that consumption = the council cycle, NOT
  this cycle).

Grounding (researcher, 2026-06-18, cited): `feedparser` is NOT a dep; `requests~=2.32.5` is.
`advisors/lens_gdelt.py` already fetches `artlist` per-article `{url,seendate,title,domain}` —
those objects just need to become a corpus input instead of citations.

## Acceptance Criteria

- [ ] AC-1 (multi-source fetch): the lens pulls the recommended free/keyless feed set (below) via `requests` with an **explicit descriptive `User-Agent`** and a per-feed timeout. A single feed failing (403/timeout/parse) degrades THAT feed only — never the whole lens. [LOAD-BEARING: SEC/CNBC/Fed/BLS return 403 to a default UA — UA is mandatory; for `.gov` use the descriptive contact UA `PlanetStopper/1.0 paulmgreaney@gmail.com`.]
- [ ] AC-2 (GDELT tone facet): emitted as an INDEPENDENT, always-valid, UNRANKED facet (kept exactly as `lens_gdelt.py` does timelinetone today). Present even when all article feeds fail.
- [ ] AC-3 (ranked corpus): every article feed (incl. GDELT `artlist`) is normalized to a common record `{url,title,published,domain,source_feed}`, then **cross-source deduped** (canonical-URL after stripping utm_*/fragments + resolving Google News wrapper URLs to the publisher URL; title token-set Jaccard ≥ 0.85; ≤ N-per-domain cap), **scored** `score = W_RECENCY·recency + W_RELEVANCE·relevance + W_AUTHORITY·authority` (recency = exp(-Δt_h/τ), τ named; relevance = market-keyword hits; authority = `SOURCE_AUTHORITY[publisher_domain]` table), sorted desc, top-K kept. ALL weights/τ/K/dedup-threshold are NAMED CONSTANTS with source comments (no magic numbers).
- [ ] AC-4 (topic-tagging): each corpus article is tagged with topic(s) via a keyword→topic map (pure stdlib regex/string): macro / fundamentals / technicals / derivatives / broad-sentiment. Enables Phase-B cross-lens routing.
- [ ] AC-5 (honest availability + D-1): `available=True` iff (tone facet present OR corpus non-empty); the row-77 `available=True`+all-null defect is fixed end-to-end through `_build_sentiment_section` + the `lens_pipeline` per-lens mapping. Per-feed errors logged type-only (D-1); never-raises; off-execution-path (CC-2 lazy import).
- [ ] AC-6 (consumer + persistence): `ai_advisor._build_sentiment_section` surfaces BOTH facets (tone + the ranked, topic-tagged corpus) in a structured, render-ready shape; warehouse persistence (`lens_warehouse`) preserved.
- [ ] AC-7 (ingestion): add `feedparser` to `requirements.txt`; fetch with `requests` (UA + timeout) then `feedparser.parse(resp.content)` (do NOT let feedparser fetch — it can't set UA/timeout). Bounded/never-raising per existing lens discipline.

## Architecture
- Keep GDELT tone (Facet A) in `advisors/lens_gdelt.py` (timelinetone). 
- New module (implementer's call on name, e.g. `advisors/news_corpus.py`): the multi-source corpus builder — fetch feeds (UA+timeout, per-feed isolation) → normalize → cross-source dedup → score → topic-tag → ranked top-K corpus. GDELT `artlist` is one input feed.
- `ai_advisor._build_sentiment_section` orchestrates Facet A + Facet B into the sentiment lens output.
- **Recommended feed set (~11, all keyless, pull serially w/ UA, GDELT-tone first as the floor):** GDELT timelinetone (tone facet) · GDELT artlist (corpus) · Google News RSS search `markets OR "stock market" OR "federal reserve" when:24h` (Reuters/AP proxy) · CNBC Markets + Economy (verify IDs live) · MarketWatch topstories · Yahoo Finance rssindex (best-effort) · Fed `press_all.xml` · BLS `bls_latest.rss` · BEA `rss.xml` · SEC `getcurrent&type=8-K&output=atom` (≤10 req/s) · Google News topic BUSINESS (fallback). Exact endpoints in DECISIONS / the researcher report.
- `SOURCE_AUTHORITY` (named): Fed/SEC/BLS/BEA=1.0, Reuters/AP=0.9, CNBC/MarketWatch=0.7, Yahoo/Nasdaq=0.6, unknown=0.4. Weights `W_RECENCY=0.4, W_RELEVANCE=0.35, W_AUTHORITY=0.25`; τ=24h; top-K≈25; ≤3/domain. [PM-ASSUMED tunables — named + commented.]

## Edge Cases
- Feed 403 → the UA fix prevents it; if still 403/timeout → that feed yields 0 articles, lens continues. Reuters/AP direct RSS is DEAD → only via Google News proxy. SEC 10 req/s hard cap (+~10-min IP block on excess) → bounded, spaced. GDELT 429 → existing backoff. All article feeds fail but tone OK → `available=True` (tone facet). Tone AND corpus both empty → `available=False`, named reason.

## Security Considerations
- Explicit `User-Agent` on every fetch; descriptive contact UA for `.gov`. D-1 (type-only reasons). Feed hosts are a FIXED allowlist (no user input → no SSRF). Bounded per-feed timeout + overall bound; never-raises; off-execution-path; advisory-only.

## Testing Strategy
- Fixtures **captured from REAL feed pulls** (provenance-labeled; GDELT + ≥2 RSS + 1 .gov + Google News), committed under `tests/fixtures/`. Honest fallback: schema-derived-with-runtime-validator, labeled, if a feed is unreachable at capture time.
- Assert SHAPE/STRUCTURE/PRESENCE — NEVER hardcode headline text, tone values, or specific articles. Tests: multi-feed fetch sets UA; per-feed failure isolated; GDELT tone facet independent + always-valid; cross-source dedup collapses a GDELT+GoogleNews duplicate of the same publisher URL to one (keeping higher authority); score uses named constants; topic-tagging routes by keyword; honest availability (tone-only / corpus-only / both-null) end-to-end; D-1 type-only on feed failure.

## Scope Boundaries
- IN: the two-facet lens DATA (GDELT tone facet + ranked/deduped/topic-tagged multi-source corpus) + the consumer surfacing both + warehouse persistence + `feedparser` dep.
- OUT (separate cycles): **Phase B** — the council's OTHER analysts (macro/fundamentals/technicals/derivatives) consuming the topic-tagged corpus as context during Q&A/debate (= the council 5/5 cycle). The Overview PROSE render of the news events (= RF-1 render cycle).

## Open questions [PM-ASSUMED defaults — non-blocking]
- CNBC numeric IDs / Yahoo / Nasdaq paths: live-curl-verify at wiring (RED fixture captured from a real pull). GDELT artlist `maxrecords`: bump 10→~50 for a richer corpus (one call, rate-spaced). Universe-level (no per-ticker fan-out) for v1. Google News wrapper URLs: best-effort resolve for dedup/authority, hash-fallback if not.
