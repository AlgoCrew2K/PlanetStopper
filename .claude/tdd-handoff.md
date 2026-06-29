# TDD Handoff — DE-SOURCES-CAROUSEL-001 (Overview Sources Carousel)

**Phase:** red

**For:** `carousel-impl` (flask-dashboard-specialist, the implementer)
**Written by:** quant-test-writer (test-writer)
**Branch:** `feat/overview-sources-carousel`
**Worktree:** `C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/sources-carousel`
**RED test file:** `tests/ai_advisor/test_overview_sources_carousel.py` (11 tests — 2 new RED from ux-expert findings)

---

## Your job

Make the 9 failing tests pass by modifying ONLY:
- `templates/ai_advisor.html` (the sources render block ~lines 1024–1052 + the CSS block ~lines 798–854)
- Optionally `static/ai_advisor.js` IF you add arrow controls (CSS scroll-snap preferred — prefer NO JS)

Do NOT touch the test file.
Do NOT merge, push, or touch any branch other than `feat/overview-sources-carousel`.
Do NOT add new backend routes, database accessors, or Python files.

---

## What the tests require (behavior contract)

### 1. Replace `<ul class="prism-sources-list">` with a horizontal carousel container

The block rendered for `{% if _all_sources %}` (currently ~lines 1028–1052 of the template)
must become:

```html
<div data-testid="prism-sources" class="... prism-sources-carousel ...">
    <div class="prism-sources-header">Sources</div>
    <!-- one card per source -->
    ...
</div>
```

- No `<ul class="prism-sources-list">` anywhere in the rendered output.
- The carousel container must carry class `prism-sources-carousel` (the CSS check asserts it).
- `data-testid="prism-sources"` stays on the OUTER container.

### 2. `article_corpus` entries → `<a>` cards

Each entry from `_src.url` (an `article_corpus` item with a non-empty http/https URL) must render as:

```html
<a class="prism-source-card"
   href="{{ _src.url | e }}"
   target="_blank"
   rel="noopener noreferrer">
    ...title, published, lens...
</a>
```

Rules:
- `target="_blank"` — required; test asserts its presence.
- `rel="noopener noreferrer"` — required; test asserts its presence.
- The `href` must equal the URL from `article_corpus`; test checks for both distinct URLs.
- All interpolated fields use `| e` (Jinja auto-escape) — no `| safe`.

### 3. Card fields: title, published, lens

Each card must include:
- Title text (from `_src.get('title', 'Untitled')`) — escaped; test asserts the text content appears.
- Published date (from `_src.get('published', '')`) — if present; test asserts the value appears.
- Lens tag (from `_src.get('lens', '')`) — if present; test asserts the value appears.

XSS: a title of `<b>Inject</b>` must appear entity-encoded (`&lt;b&gt;Inject&lt;/b&gt;`), never as live HTML.

### 4. Plain-string `sources` entries → NON-`<a>` citation cards

When `_all_sources` contains an entry with no `url` key (only `citation` + `lens`),
it must render as a non-clickable element — e.g., a `<div>` or `<span>`.
It must NOT render as `<a href="#">` or any anchor element.
The citation text must appear in the HTML.

### 5. `javascript:` URL → non-clickable (security)

When `article_corpus` contains `url = "javascript:alert(1)"`, the template
must NOT emit `<a href="javascript:`. That URL must be treated as non-http
and fall through to the non-clickable citation card path.

Implementation hint (Jinja):
```jinja
{% if _src.url and _src.url.startswith('http') %}
    <a href="{{ _src.url | e }}" ...>...</a>
{% else %}
    <div class="prism-source-card prism-source-card--citation">...</div>
{% endif %}
```

### 6. Empty `_all_sources` → no carousel, no header

When all `per_lens_digest` entries have empty `sources: []` and no `article_corpus` key,
`_all_sources` stays empty (`[]`). The template's `{% if _all_sources %}` guard
must prevent any carousel element or "Sources" header from rendering.
`data-testid="prism-sources"` must be ABSENT from the output.

### 7. CSS: `.prism-sources-carousel` must have `overflow-x` + height cap

In the `<style>` block, the `.prism-sources-carousel` rule must contain:
- `overflow-x` (with value `auto` or `scroll`)
- A height-bounding property: either `height` or `max-height`

And `.prism-source-card` must exist in the CSS.

No raw hex color literals (`#[0-9a-fA-F]{3,8}`) anywhere in the
`.prism-sources-carousel` or `.prism-source-card` CSS rules — use `var(--...)` tokens.

### 8. `data-testid="prism-sources"` preserved

When sources are present, `data-testid="prism-sources"` must appear
exactly once in the rendered HTML. This is the regression guard.

---

## How to verify GREEN

In the worktree:
```
set ALPHABOT_TEST_MEM_CAP_GB=24
set DB_PATH=C:/Users/paulm/AppData/Local/Temp/test_carousel_state.db
python -m pytest tests/ai_advisor/test_overview_sources_carousel.py -n0 --tb=short -q
```

Target: **9 passed, 0 failed**.

Run ruff on every file you touch before committing:
```
python -m ruff format templates/ai_advisor.html  # (ruff skips non-py; just run check on any .py if you touched one)
python -m ruff check .  # only if you touched a .py file
```

Commit path-scoped (NOT `git add -A`):
```
git add templates/ai_advisor.html
# if you touched static/ai_advisor.js:
# git add static/ai_advisor.js
git commit -m "feat(overview): replace prism sources vertical list with bounded horizontal carousel (DE-SOURCES-CAROUSEL-001)"
```

Then `SendMessage` to quant-test-writer: "GREEN: 9 passed / 0 failed / 0 errors. SHA=<sha>."

---

## Test Files
- `tests/ai_advisor/test_overview_sources_carousel.py` — 9 tests (all RED)

## A/C Coverage Matrix

| A/C ID | Description | Test File | Test Name(s) | Status |
|--------|-------------|-----------|--------------|--------|
| AC-1 | Horizontal carousel container replaces vertical `<ul>` | test_overview_sources_carousel.py | test_sources_carousel_container_replaces_vertical_list | GREEN |
| AC-1 (CSS) | `.prism-sources-carousel` CSS has `overflow-x` + height cap | test_overview_sources_carousel.py | test_carousel_css_has_horizontal_scroll_and_height_cap | GREEN |
| AC-2 | Each `article_corpus` source is an `<a>` card with `target=_blank` + `rel` | test_overview_sources_carousel.py | test_each_article_corpus_source_is_an_anchor_card | GREEN |
| AC-3 | Card fields present and HTML-escaped (no `| safe`) | test_overview_sources_carousel.py | test_card_fields_present_and_html_escaped | GREEN |
| AC-5 | Plain-string citation entry is non-`<a>` | test_overview_sources_carousel.py | test_plain_citation_entry_renders_as_non_anchor_card | GREEN |
| AC-6 | Empty sources → no carousel, no header | test_overview_sources_carousel.py | test_empty_sources_renders_no_carousel_and_no_header | TEST BUG |
| AC-7 | Design tokens only; no raw hex in carousel CSS | test_overview_sources_carousel.py | test_carousel_css_uses_design_tokens_not_raw_hex | GREEN |
| AC-7 | CSS has `overflow-x` + height bounding | test_overview_sources_carousel.py | test_carousel_css_has_horizontal_scroll_and_height_cap | GREEN |
| AC-8 | `data-testid="prism-sources"` preserved | test_overview_sources_carousel.py | test_prism_sources_data_testid_preserved | GREEN |
| Security | `javascript:` URL must not produce clickable link | test_overview_sources_carousel.py | test_javascript_url_does_not_become_clickable_link | GREEN |

## Questions for User
None.

## Import Stubs Created
None — this is a template-only change. All modules (`app`, `database`) already exist.

## Status Log
- [2026-06-29] test-writer: Starting RED phase — DE-SOURCES-CAROUSEL-001
- [2026-06-29] test-writer: RED complete — 9 tests written (5 failing, 4 regression guards). Commit e2ef464
- [2026-06-29] implementer: GREEN — 8/9. 1 test bug: bare `"prism-sources-carousel"` matched CSS selector in `<style>` block. SHA=8066d67
- [2026-06-29] test-writer: FIXED test bug — changed to `'class="prism-sources-carousel"'` (element attr check). 9/9 passed. Commit bbf2e29
- [2026-06-29] test-writer: REVIEW PASSED — all 9 ACs covered. Old `.prism-sources-list`/`.prism-source-item`/`.prism-source-link` CSS fully removed. No dead code. No raw hex. Security guard (javascript: url → non-anchor) confirmed. Pair satisfied. APPROVED for ux-expert + doc-gen.
- [2026-06-29] ux-expert: 2 visual-contract gaps found from Playwright render (d3d9e5e). Finding A (CRITICAL): `.prism-source-card--citation` has no CSS rule — citation cards visually identical to anchor cards. Finding B (MEDIUM): no title ellipsis truncation CSS — long headlines wrap, breaking card height uniformity.
- [2026-06-29] test-writer: RED — 2 new tests added for ux findings A+B. 9 pass / 2 fail. Commit 9342f68. Sending carousel-impl back for fix.

---

## Test File Issues (for test-writer to fix)

### `test_empty_sources_renders_no_carousel_and_no_header` — second assertion too broad

**File:** `tests/ai_advisor/test_overview_sources_carousel.py`, line 614

**Failing assertion:**
```python
assert "prism-sources-carousel" not in html
```

**Root cause:** The assertion checks the entire rendered HTML output for the string `"prism-sources-carousel"`. However, the required CSS rule `.prism-sources-carousel { ... }` lives in the `<style>` block in `<head>`, which is always rendered regardless of whether any sources are present. So "prism-sources-carousel" always appears in the rendered HTML as part of the CSS selector — even when the carousel HTML element itself is correctly absent.

This was a valid test against the OLD implementation (which had `.prism-sources-list` in the CSS, not `.prism-sources-carousel`). With the carousel CSS added, the constraint is contradictory: AC-1 CSS + AC-7 require `.prism-sources-carousel { ... }` in the main `<style>` block (which is always rendered), while this assertion requires the string to be absent from the rendered HTML when sources are empty.

The **first assertion in the same test passes correctly:**
```python
assert 'data-testid="prism-sources"' not in html  # PASSES — guard works
```

**What correct code produces:** `"prism-sources-carousel"` appears in the CSS `<style>` block; `data-testid="prism-sources"` and `class="prism-sources-carousel"` (the HTML element class) are correctly absent.

**Suggested fix (for test-writer):**
```python
# Replace:
assert "prism-sources-carousel" not in html

# With:
assert 'class="prism-sources-carousel"' not in html
```
This checks for the HTML *element* class attribute (absent when no sources), not the CSS class name (always present in the style block).

---

## Implementation Notes

- **CSS:** Replaced `.prism-sources-list` (vertical flex-column), `.prism-source-item`, `.prism-source-link`, `.prism-source-link:hover`, and `.prism-source-meta` with `.prism-sources-carousel` (horizontal flex row, `overflow-x:auto`, `scroll-snap-type:x mandatory`, `max-height:160px`) and `.prism-source-card` (card-shaped flex column). `.prism-source-lens-tag` kept but updated with `align-self:flex-start`. No raw hex — all colors use existing `var(--)` tokens (`var(--studio-border)`, `var(--studio-ink)`, `var(--studio-ink-dim)`).

- **HTML:** The `<ul class="prism-sources-list">` + `<li>` structure is replaced with `<div class="prism-sources-carousel">` containing `<a class="prism-source-card">` (http/https URLs) or `<div class="prism-source-card prism-source-card--citation">` (plain citations and non-http URLs). The `{% if _all_sources %}` guard and `data-testid="prism-sources"` outer wrapper are preserved unchanged.

- **Security gate:** `_src.get('url', '').startswith(('http://', 'https://'))` — stricter than bare `'http'` (team-lead hardening), rejects `javascript:`, `httpfoo:`, etc. Only http/https URLs produce `<a>` elements.

- **No JS added:** Pure CSS scroll-snap implementation. `static/ai_advisor.js` not touched.
