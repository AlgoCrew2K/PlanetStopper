# Feature: Overview Market Prism Sources — Carousel of Clickable Cards
Status: ready
Created: 2026-06-29

## Summary
The Overview tab's Market Prism "Sources" (the `article_corpus` citations shipped in DE-PRISM-SOURCES-001) currently render as a vertical `<ul class="prism-sources-list">` of `<li>` items in `templates/ai_advisor.html` (~lines 1024–1048; CSS ~798–838). With a full nightly council run producing many citations, that list is "unruly" and **expands the Overview page vertically far too much** (operator complaint, 2026-06-29). Replace the vertical list with a **bounded horizontal carousel of clickable source cards** — fixed (single-row) height so the page no longer grows with the source count; each source is its own clickable card opening the URL in a new tab. This is a presentation refactor of the existing, working sources feature (the operator likes having the sources — only the list layout is the problem). Server-rendered (Jinja + CSS); prefer CSS scroll-snap (no JS) unless arrow controls require a minimal, syntax-checked JS addition.

## Acceptance Criteria
- [ ] AC-1: The Overview Market Prism sources render as a SINGLE-ROW horizontal carousel with a **bounded fixed height** (≈ one card tall) — adding more sources scrolls horizontally and does NOT increase the carousel's vertical footprint (the page-expansion defect is gone). A test asserts the container is a horizontal scroller (e.g. `overflow-x` scroll/auto + a capped height), not a growing vertical list.
- [ ] AC-2: Each `article_corpus` source is ONE card; the **whole card is the clickable link** (`<a>`) to `_src.url`, opening in a new tab with `target="_blank"` + `rel="noopener noreferrer"`. A test asserts each card is an anchor to the source url with those attrs.
- [ ] AC-3: Each card shows the source **title** (truncated/ellipsis if long, so cards stay uniform), the **domain** (derived from the url) or source label, the **published** date, and the **lens tag** (the existing `_src` fields: `url`, `title`, `published`, `lens`).
- [ ] AC-4: Horizontal overflow is reachable — CSS `scroll-snap` + touch/trackpad swipe; if arrow/scroll-button controls are added they are minimal and the JS passes `node --check` (extend `tests/js_syntax/test_js_syntax.py`, do NOT add a new per-file node-check test). With ≤ N cards that fit, no scroll is needed (no empty controls).
- [ ] AC-5: **Plain-string citations** (the `_src.citation` entries with no url — non-`article_corpus`) render as NON-clickable cards (or text cards) within the same carousel, NOT as broken/empty links. Mixed corpus (some url cards + some citation cards) renders cleanly.
- [ ] AC-6: **Honest empty-state preserved** — when there are zero sources (`_all_sources` empty), NO carousel and NO "Sources" header render (unchanged from today); no empty/broken carousel shell.
- [ ] AC-7: Styling uses the existing design-system CSS variables/tokens already in `templates/ai_advisor.html` (the `--*` custom properties) — NO new raw hex colors. Cards reuse the existing card/surface tokens. Responsive: the carousel works at mobile width (swipe) and desktop.
- [ ] AC-8: No regression to the rest of the Overview SPA render (the per-lens digest, sentiment chip, etc. unchanged); the `data-testid="prism-sources"` hook is preserved (or updated consistently with its tests).

## Architecture
- **File: `templates/ai_advisor.html`** — primary change.
  - Render block (~1024–1048): replace `<ul class="prism-sources-list">` + `<li class="prism-source-item">` with a carousel container (e.g. `<div class="prism-sources-carousel" data-testid="prism-sources">`) holding one card per source. `article_corpus` entries → `<a class="prism-source-card" href="{{ _src.url|e }}" target="_blank" rel="noopener noreferrer">`; plain citations → `<div class="prism-source-card prism-source-card--citation">`. Keep the existing `_all_sources` aggregation logic (~954–963) unchanged.
  - CSS block (~798–838): replace/extend `.prism-sources-list`/`.prism-source-item` with `.prism-sources-carousel` (display:flex; flex-direction:row; overflow-x:auto; scroll-snap-type:x; gap; a capped height + hidden vertical overflow) and `.prism-source-card` (fixed/min width, scroll-snap-align, padding, the design tokens, title ellipsis via `text-overflow`). Reuse existing token vars; no raw colors.
- **`static/ai_advisor.js`** — ONLY if arrow controls are added (optional; CSS scroll-snap + native swipe is preferred and may need zero JS). If added: a small `initSourcesCarousel()` (scroll-by on arrow click), wired in the existing tab-init path; must pass `node --check`.
- No backend/route/data change — the `article_corpus` data + `_all_sources` aggregation are unchanged. Display-only.

## Design-System Mapping
| Element | Token / pattern | Notes |
|---------|-----------------|-------|
| Carousel container | existing surface/spacing CSS vars in ai_advisor.html | single row, capped height, `overflow-x:auto`, `scroll-snap-type:x mandatory` |
| Source card | existing card/border/`--*` color tokens (reuse `.prism-source-*` palette) | fixed/min-width, `scroll-snap-align:start`, padding via spacing tokens |
| Card title | existing text/ink token | `white-space:nowrap; overflow:hidden; text-overflow:ellipsis` (uniform cards) |
| Card meta (domain/published/lens) | existing `.prism-source-meta` / `.prism-source-lens-tag` tokens | smaller/secondary ink token |
| Link affordance | card is `<a>`; hover uses existing `.prism-source-link:hover` style | whole-card hover cue |
(No design system declared beyond the template's inline CSS vars — reuse those; introduce NO new raw hex.)

## Edge Cases
- 0 sources → no carousel, no header (AC-6).
- 1 source → single card, no horizontal scroll, no arrow controls.
- Many sources (real nightly run can be dozens) → horizontal scroll only; vertical height unchanged.
- Plain-citation-only entry (no url) → non-clickable card, not an empty `<a>` (AC-5).
- Long title / long domain → ellipsis truncation; cards stay uniform height/width.
- Missing `published` or `lens` on a source → omit that meta line gracefully (no "None"/empty tag).
- Very small viewport (mobile) → cards remain swipeable, carousel still single-row bounded.

## Security Considerations
- **`href` injection / `javascript:` protocol:** `_src.url` originates from the council's `_patch_provenance` (validated lens sources) but is still externally-derived. Render the href with `| e` AND ensure only http(s) URLs become links — if an entry's url is missing/`#`/non-http, render it as a non-clickable citation card (AC-5 path), never an `<a href="javascript:…">`. Add a test for a hostile url (e.g. `javascript:alert(1)`) → must NOT become a clickable link.
- **XSS in title/published/lens:** all already escaped with `| e` today — preserve `| e` on every interpolated field; no `| safe`.
- `target="_blank"` MUST carry `rel="noopener noreferrer"` (reverse-tabnabbing).
- No new input surface, no auth/route change (Overview is behind the existing auth gate).

## Testing Strategy
- **Template-render tests** (extend the existing Overview/ai_advisor template tests; render `ai_advisor.html` with a fabricated `per_lens_digest` carrying `article_corpus` + plain citations):
  - carousel container present with horizontal-scroll + bounded-height CSS class/markers (AC-1); NOT a `<ul>` vertical list.
  - one card per source; `article_corpus` cards are `<a href=url target=_blank rel=noopener noreferrer>` (AC-2); fields present + escaped (AC-3).
  - plain-citation entry → non-`<a>` card (AC-5); hostile `javascript:` url → non-clickable (security).
  - empty `_all_sources` → no carousel, no header (AC-6).
  - `data-testid` hooks intact (AC-8).
- **Design-system test:** assert cards/container reference the existing token classes and contain NO raw hex color literals (design-contract, not computed values).
- **JS syntax:** if `ai_advisor.js` gains carousel JS, extend `tests/js_syntax/test_js_syntax.py` (parametrized) — do NOT add a per-file node-check.
- **Behavioral / render verification (MANDATORY — UI change):** ux-expert renders `/ai-advisor` Overview via Playwright at desktop + mobile widths, captures screenshots, and confirms: single-row bounded carousel (page no longer over-expands), cards clickable, horizontal scroll works, design tokens resolve (not browser-default). **PM reads the screenshots before ship** (no "renders" == "correct"). Computed-style spot-check: container has horizontal overflow + capped height; a card is an anchor.
- No new e2e auth flow (reuse existing).

## Decisions
| Decision | Rationale |
|----------|-----------|
| Prefer CSS scroll-snap, JS only if arrows needed | Minimize JS surface; native swipe + scroll-snap handles the carousel; less to test/break |
| Whole card is the `<a>` (not a nested link) | Operator asked for "clickable carousel card"; larger hit target |
| Non-http urls render as non-clickable citation cards | Security (no `javascript:` links) + matches the existing plain-citation path |
| Bounded fixed height + horizontal scroll | The actual fix for the operator's page-expansion complaint |
| Reuse existing `.prism-source-*` tokens | No design drift; no raw colors |

## Scope Boundaries
- **IN:** `templates/ai_advisor.html` sources render block + its CSS (carousel + cards); optional minimal `static/ai_advisor.js` carousel-scroll JS; the tests above. Display-only refactor of the existing `_all_sources`/`article_corpus` rendering.
- **OUT:** the `article_corpus` data/aggregation, `_patch_provenance`, the council, any route/backend change; the advisor latency + the Optuna message (separate diagnostic/cycle); any other Overview block; non-source tabs.
