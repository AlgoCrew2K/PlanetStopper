# Feature: News-Events Sentiment Lens (replace thin aggregate-tone with real news events)
Status: ready
Created: 2026-06-18

## Summary

The Market Prism "sentiment" lens currently consumes only a single GDELT **aggregate
tone scalar** (`timelinetone` mean AvgTone), throws the actual articles away as mere
citation URLs, and uses an unfiltered `query=stock+market+finance` that pulls
**foreign-language / irrelevant news** (observed: Chinese-language articles in the live
nightly row 77, with `tone_score=null`). The operator's intent: the council must consume
**actual market-relevant news EVENTS** (headlines/stories) — not a near-zero tone number —
to produce a human-consumable sentiment read.

This upgrades `advisors/lens_gdelt.py` (+ its consumer `ai_advisor._build_sentiment_section`
and the `lens_pipeline` mapping) so the sentiment lens surfaces a ranked set of real,
English/market-relevant **news events** (title, source domain, date) as its PRIMARY signal,
with aggregate tone retained as a SECONDARY corroborating signal, and honest availability.

## Acceptance Criteria

- [ ] AC-1: The GDELT query is filtered to **English-language, market-relevant** news (e.g. a `sourcelang:eng`-equivalent operator + a market query). A live fetch returns NO foreign-language articles. (No hardcoded headline assertions — assert the query string carries the language/relevance filter + that returned article languages, when present in the response, are English.)
- [ ] AC-2: The lens output surfaces actual **news events** — a ranked list of the top-N (N a named constant, ~5–8) relevant articles, each with `title`, `domain`, `seendate`, deduped by domain — as a first-class `events` field, NOT just citation URLs.
- [ ] AC-3: Aggregate `tone` is retained as a SECONDARY field. **Honest availability:** `available=True` iff at least one real signal is present (events OR tone); never `available=True` with all-null signals (the row-77 `tone_score=null`+`available=True` defect is fixed end-to-end through `_build_sentiment_section` and the `lens_pipeline` per-lens mapping).
- [ ] AC-4: Existing invariants preserved — bounded retry (`_GDELT_MAX_ATTEMPTS`, backoff), D-1 (`reason = type(exc).__name__` only), off-execution-path (no module-level import on the engine path), never-raises.
- [ ] AC-5: The `per_lens_digest.sentiment.summary` consumed by the Overview render carries the events in a structured, render-ready shape (so the downstream render cycle can show headlines, not raw JSON). The `prism-sentiment-analyst` reasons over the events (lens data carries them).
- [ ] AC-6: Honest degradation — GDELT 429 / empty / foreign-only result → `available=False` with a named reason (`rate_limited` / `no_news_events` / etc.), never a half-populated row.

## Architecture

- `advisors/lens_gdelt.py`: extend `_fetch_gdelt_sentiment` (or add `_extract_events`) — add the language/relevance filter to `_GDELT_*_URL`; parse `artlist` articles into a ranked, domain-deduped `events` list (title/domain/seendate); keep `timelinetone` → `tone` as secondary; recompute `available` from (events OR tone). Keep the two-GET rate-limit spacing.
- `ai_advisor._build_sentiment_section`: consume the new `events` + `tone`; emit a structured summary `{events:[...], tone, article_count, ...}`; fix any place that set `available=True` with null tone.
- `advisors/lens_pipeline.py`: ensure the sentiment per-lens mapping carries `events` into `per_lens_digest`.

## Edge Cases
- GDELT `timelinetone` empty/flaky → tone=None but events present → still `available=True` (events carry it). Both empty → `available=False`, reason `no_news_events`.
- artlist returns foreign-language despite filter → drop non-English entries; if none remain → degrade honestly.
- 429 storm → bounded retry then `rate_limited` (existing).

## Security Considerations
- D-1 everywhere (type-only reasons). No secrets (GDELT is keyless). Off-execution-path. Never-raises. No SSRF (fixed GDELT host).

## Testing Strategy
- `tests/ai_advisor/test_lens_gdelt.py` + a new events test: **fixture captured from a REAL GDELT artlist response** (provenance: captured-from-producer, committed as a fixture) — assert events are extracted (shape: title/domain/seendate present, domain-deduped, ≤N), language filter is in the query, honest availability (events-only / tone-only / both-null cases), D-1 reason on failure. NO hardcoded headline text or tone values — assert shape/presence/structure only.
- Consumer test: `_build_sentiment_section` surfaces events; `available=True` never with all-null.

## Scope Boundaries
- IN: the GDELT lens producer + its consumer/mapping so the lens DATA carries real news events.
- OUT (separate cycles): the Overview prose RENDER (RF-1), the council 5/5 coordination fix, making the council the live nightly producer. This cycle makes the sentiment lens *produce* real events; rendering them human-consumably is the next cycle.
