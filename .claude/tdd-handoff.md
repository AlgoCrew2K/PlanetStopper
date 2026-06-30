# TDD Handoff — per-lens Market Prism sources carousels
Plan: feature-plans/prism-sources-per-lens-carousels.md
Branch: feat/prism-sources-per-lens-carousels
Phase: red

## Test Files
- tests/ai_advisor/test_prism_per_lens_carousels.py (10 tests)

## Behavioral Test Plan
1. GET /ai-advisor with multi-lens fixture → 3 `.prism-sources-carousel` containers, not 1 flat
2. GET /ai-advisor → shared URL appears inside BOTH the technicals AND sentiment per-lens sections
3. GET /ai-advisor → tech-only URL confined to technicals section, absent from sentiment section
4. GET /ai-advisor → `.prism-source-lens-tag` class absent from rendered HTML entirely
5. GET /ai-advisor → `data-testid="prism-sources-lens-{lens}"` present for technicals/sentiment/macro; absent for derivatives/fundamentals (no sources)
6. GET /ai-advisor → technicals testid position < sentiment testid position < macro testid position
7. GET /ai-advisor with no MARKET_PRISM row → `prism-empty-state` present, no carousel rendered
8. GET /ai-advisor → lens name appears as visible text content (between tags) in its section
9. GET /ai-advisor → macro plain-citation string rendered inside the macro per-lens section
10. GET /ai-advisor with XSS title → raw `<b>Inject</b>` absent; "Inject" text present escaped

## Implementation Notes for ph-impl (flask-dashboard-specialist)

### What GREEN must do — TEMPLATE-ONLY (AC-7 confirmed by recon at cf93826)

**Replace the flat single-carousel block** (lines 982–1083 of `templates/ai_advisor.html`)
with a per-lens loop. Specifically:

**1. Remove the `_all_sources` aggregation list** — the entire `{% set _all_sources = [] %}`
and the loop that populates it are deleted.

**2. Add a per-lens outer loop** over `_lens_names` (the canonical list at template line 1025:
`['technicals', 'sentiment', 'derivatives', 'macro', 'fundamentals']`) in that fixed order.
For each lens:
  a. Collect that lens's sources: `article_corpus` list entries + plain `sources` strings.
  b. If the lens has ZERO sources (both lists empty / absent), SKIP — no carousel, no label (AC-2).
  c. If the lens has ≥1 source, emit:
     ```html
     <div data-testid="prism-sources-lens-{lens_name}">
         <div class="prism-lens-carousel-label">{lens_name}</div>
         <div class="prism-sources-carousel">
             ... per-card loop (same logic as old flat loop, minus lens tag) ...
         </div>
     </div>
     ```

**3. Remove `.prism-source-lens-tag` spans** from BOTH card variants (AC-5) — the parent
group label already identifies the lens.

**4. Preserve all escaping** — keep `| e` on url/title/published/citation fields. No `| safe`.

**5. Outer structure preserved** — the existing `<div data-testid="prism-sources">` with
`<div class="prism-sources-header">Sources</div>` must be kept as the outer wrapper
(existing `test_overview_sources_carousel.py` pins `data-testid="prism-sources"`).
The entire per-lens loop goes inside it, gated on whether ANY lens has sources.

**6. Empty-state rule (AC-6)** — when ALL lenses have zero sources, render NO carousel
headers, NO groups, NO "Sources" heading (existing outer `{% if ... %}` guard must cover all).
The `{% if market_prism_summary %}` guard for the no-row case is unchanged.

**7. URL guard preserved** — `{% if _src.get('url') and _src.get('url').startswith(('http://', 'https://')) %}`
on anchor cards MUST stay as-is (prevents `javascript:` URLs, guards `url=None` crash).

**8. CSS rules** — `.prism-sources-carousel`, `.prism-source-card`, `.prism-source-card--citation`,
`a.prism-source-card:hover` all keep their existing rules. Remove the `.prism-source-lens-tag`
CSS rule from the `<style>` block.

### Data shape at cf93826 (confirmed by test-writer recon)
- `market_prism_summary['raw_response']['per_lens_digest']` is the per-lens dict.
- After `ai_advisor_tab()` merges the SOURCES row (app.py lines 3718–3754), each lens entry
  MAY have `article_corpus: list[{url, title, published}]` AND/OR `sources: list[str]`.
- Both fields may exist on the same lens entry; treat independently.
- JS files (`static/ai_advisor.js`, `static/index.js`) have ZERO references to sources.
  No JS changes needed.

### Structural discriminators (what tests enforce)
- `html.count('class="prism-sources-carousel"') == 3` for the 3-lens fixture
- `data-testid="prism-sources-lens-technicals"` in html; `"prism-sources-lens-derivatives"` NOT in html
- Shared URL present in BOTH technicals and sentiment sections (section-parsed)
- Tech-only URL absent from sentiment section (section-parsed)
- `prism-source-lens-tag` class absent from entire HTML
- Lens name as visible text `>[Tt]echnicals<` between tags in its section
- Macro citation string present in macro section

## A/C Coverage Matrix
| A/C | Description | Test(s) | Status |
|-----|-------------|---------|--------|
| AC-1 | One carousel per non-empty lens, each labeled | test_1_carousel_count, test_8_lens_label | RED |
| AC-2 | Empty lens renders no carousel | test_5_testid_per_lens (absent for derivatives/fundamentals) | RED |
| AC-3 | Shared URL in each of its lens carousels | test_2_shared_url_in_both_sections | RED |
| AC-4 | `.prism-sources-carousel` + `.prism-source-card--citation` preserved | test_1_carousel_count, test_9_macro_citation | RED |
| AC-5 | `.prism-source-lens-tag` removed | test_4_lens_tag_absent | RED |
| AC-6 | No-sources honest empty-state | test_7_empty_state | GUARD |
| AC-7 | Template-only (no route/data change) | Recon finding — no test needed | N/A |
| AC-8 | Canonical ordering | test_6_canonical_ordering | RED |
| Security | XSS escaping preserved through restructure | test_10_xss_escaped | GUARD |

## Import Stubs Created
None — template-only change, no new Python modules.

## Questions for User / PM
None — recon at cf93826 was conclusive. AC-7 confirmed template-only.

## Status Log
- [2026-06-30] test-writer: Starting RED phase — per-lens Market Prism sources carousels
