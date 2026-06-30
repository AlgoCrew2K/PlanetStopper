# Feature Plan: Per-Lens Market Prism Sources Carousels

Status: ready

## Summary
On the AI Advisor Overview tab, the Market Prism "Sources" section currently renders ONE
horizontal carousel (`.prism-sources-carousel` of `.prism-source-card` items — DE-SOURCES-CAROUSEL-001
/ #85) that FLATTENS every prism lens's sources into a single strip, with a per-card lens tag
(`.prism-source-lens-tag`). The operator finds the single strip requires too much horizontal
scrolling. Replace it with ONE carousel PER prism lens (Technicals, Sentiment, Derivatives, Macro,
Fundamentals) — each a short, lens-labeled strip containing only that lens's sources. A source
(article URL or plain citation) cited by multiple lenses appears in each of those lenses' carousels
(natural duplication — `per_lens_digest` is keyed by lens with no cross-lens dedup). This is a
template-side change; the data already supports it.

## Acceptance Criteria
- AC-1: Under the "Sources" header, the Overview tab renders one carousel per prism lens that has
  >=1 source (an `article_corpus` entry OR a plain `sources` citation), each labeled with the lens's
  display name.
- AC-2: A lens with zero sources renders NO carousel (no empty strip, no bare label).
- AC-3: A source cited by N lenses appears once in each of those N lens carousels (multi-lens
  duplication preserved; no cross-lens dedup introduced).
- AC-4: Each lens carousel preserves the #85 visual contract — `.prism-source-card` styling, bounded
  horizontal scroll, the `.prism-source-card--citation` variant for plain citations, and clickable
  `<a>` cards for `article_corpus` entries (url/title/published).
- AC-5: The per-card lens tag (`.prism-source-lens-tag`) is removed (redundant inside a lens-labeled
  carousel).
- AC-6: When NO lens has any source (no nightly MARKET_PRISM/SOURCES row, or all lenses empty), the
  Sources section shows the EXISTING honest empty-state — NOT five empty carousels.
- AC-7: Template-only is the expected path. Any change to app.py route logic / the data layer / the
  lens pipeline is OUT unless the team CONFIRMS at cf93826 that per-article multi-lens attribution is
  not already present in the template context — in which case the smallest possible route-side
  exposure is allowed, justified inline.
- AC-8: Lens display ordering is stable + deterministic (technicals, sentiment, derivatives, macro,
  fundamentals).

## Architecture
- Surface: `templates/ai_advisor.html` — the Market Prism Sources section. At cf93826: CSS
  `.prism-sources-header` / `.prism-sources-carousel` / `.prism-source-card` /
  `.prism-source-card--citation` (~lines 799-850) + the render block that builds the flat
  `_all_sources` list and emits one carousel. CONFIRM exact lines at cf93826 (the prior recon read a
  pre-#85 checkout; markup classes there were `.prism-source-item`/`.prism-sources-list`, which are
  STALE — the real classes are `.prism-sources-carousel`/`.prism-source-card`).
- Data: `market_prism_summary['raw_response']['per_lens_digest']` is a dict keyed by lens; each lens
  entry carries `sources: list[str]` (plain citations) and/or `article_corpus: list[{url,title,
  published}]`. Today the template flattens all lenses into one list tagging each item with its
  `lens`; instead iterate per lens and emit a carousel each. Multi-lens duplication is automatic
  because the same source living under two lens keys is rendered under both.
- No JS carousel logic exists (CSS `overflow-x`); per-lens = repeat the carousel container per lens.
  Confirm `static/ai_advisor.js` / `static/index.js` don't touch it at cf93826.
- FIRST TEAM STEP: confirm the cf93826 data shape — which lenses actually carry `article_corpus`
  vs only `sources` citations, and that lens attribution is in the template context (the DE-PRISM-
  SOURCES-001 `_patch_provenance` rebuilds per-lens sources for macro/fundamentals/derivatives too,
  so at cf93826 articles likely span more lenses than the stale recon's "sentiment only").

## Edge Cases
- Lens present but `available: false` / empty sources -> no carousel (AC-2).
- Same URL under 2+ lenses -> appears in each lens carousel (AC-3); no cross-lens dedup.
- Preserve existing WITHIN-lens behavior (fundamentals dedups by URL within itself — keep).
- No MARKET_PRISM row at all -> existing honest empty-state (AC-6), not empty carousels.
- A lens with citations but no `article_corpus` -> its carousel shows citation-variant cards.
- Long per-lens lists -> bounded horizontal scroll (existing #85 behavior).

## Security Considerations
- All `url`/`title`/`published` + citation strings are already escaped (`| e`) — PRESERVE escaping,
  no `| safe`. External article links keep their current rel/target. No new data sources, no new
  user input, read-only render path. No SSRF/injection surface added.

## Testing Strategy
- Design-contract / structural tests (NOT computed producer values — project standard
  `feedback_tests_assert_design_contract_not_values`): render the Overview tab via the Flask
  test-client (module-global `import app; app._auth_check_enabled=False`, NOT `app.app`) seeded with
  a MARKET_PRISM (+ SOURCES) row whose `per_lens_digest` has KNOWN per-lens sources INCLUDING one URL
  shared across two lenses. Assert: one carousel container per non-empty lens; the shared URL renders
  under BOTH lenses; an empty lens renders no carousel; the per-card lens tag is absent; honest
  empty-state when no row exists; output stays escaped (no raw HTML). Fixture = captured/representative
  `per_lens_digest` shape, not hand-invented values.
- Keep/extend existing prism-sources render tests; assert structure + grouping + escaping, never
  specific article titles or computed values.
- If static JS changes, it is covered by the consolidated `tests/js_syntax/test_js_syntax.py`
  (`node --check`) — do not add per-file node-check methods.

## Scope Boundaries
- IN: the Overview-tab Market Prism Sources section rendering (template + CSS; minimal JS only if
  strictly needed); removing the per-card lens tag; per-lens grouping + duplication; empty-state.
- OUT: the nightly MARKET_PRISM/SOURCES producers, the lens pipeline, the data schema, other Overview
  blocks, other tabs, new endpoints. NO backend change unless AC-7's confirmation forces a minimal,
  justified one.
