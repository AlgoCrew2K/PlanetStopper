# RF-1 — Overview Market Prism: prose render (no raw JSON)

Status: ready
Owner: PM-authored (autonomous E2E-verification program, item #3)
Branch: feat/rf1-prose-render (off origin/main ab03263)

## Summary
The AI Advisor Overview's always-on **Market Prism** block renders **raw JSON** for the
per-lens digests whenever the producer is the nightly `lens_pipeline` (the current live
producer — row 77). Verified live: `per_lens_digest[lens]["summary"]` for lens_pipeline rows
holds the raw structured payload (e.g. technicals `{"ma_posture": {...}, "breadth": 0.7,
"momentum": {...}}`, fundamentals a multi-ticker 10-K facts dump), and the template renders
that string verbatim in `.prism-lens-text`. By contrast the **council** producer (item #4,
proven in live run 637c719f) writes clean **prose** summaries that already render correctly.

Fix: a **producer-agnostic, render-layer prose-preparation guard**. Council prose passes
through unchanged; lens_pipeline's structured JSON is humanized to readable text; null /
unavailable lenses get an honest empty-state; the operator-facing Overview never shows raw
JSON. The nightly data-production path is NOT modified (low risk; the council supersedes
lens_pipeline in item #4, and this guard stays correct + defensive for both producers).

## Acceptance Criteria
- **AC-1 (prose passthrough):** For a MARKET_PRISM row whose `per_lens_digest[lens]["summary"]`
  is already prose (a non-JSON string, e.g. the council's "BULLISH. Breadth 0.70 (7/10 above
  50d SMA)…"), the rendered Overview shows that prose substantively unchanged.
- **AC-2 (JSON → readable, all 5 lenses):** For a `summary` that is a structured JSON string
  (lens_pipeline shape), the rendered Overview shows readable human text with **no raw JSON
  syntax visible** (no `{`, `}`, `"key":` markers) for each of technicals, sentiment,
  derivatives, macro, fundamentals.
- **AC-3 (honest empty-state):** When a lens is unavailable or its summary is null/empty
  (e.g. sentiment `tone_summary: null`), the Overview shows an honest message
  (e.g. "limited inputs — tone unavailable"), never "null", "{}", "None", or raw JSON.
- **AC-4 (fundamentals concision):** The fundamentals digest (worst offender: multi-ticker
  10-K facts) renders as concise readable text (coverage count + brief highlight), not the
  full nested JSON dump.
- **AC-5 (obs-raw-preview):** The separate symphony-level `obs-raw-preview` element on the
  Overview (currently `{"backtest_selection_count": 0, …}`) no longer shows raw JSON —
  humanized or removed from the operator-facing view.
- **AC-6 (no regression):** Sentiment chip color still matches verdict (#46); overall
  `sentiment_rationale` prose, the "As of" datetime, and the cited-sources list still render
  correctly; zero new JS console errors.
- **AC-7 (defensive, never-raise):** The render guard never raises on malformed/unexpected
  digest shapes (missing keys, non-dict entries, non-JSON non-prose strings) — it degrades to
  an honest readable fallback (D-1 spirit). The daemon execution path / nightly producer is
  NOT modified.

## Architecture
- New pure render-prep helper (proposed `advisors/prism_render.py` or a function in
  `ai_advisor.py`): `humanize_lens_summary(lens_name: str, lens_entry: dict | None) -> str`
  (+ a helper preparing the whole Market Prism block for the template). Pure, no I/O,
  never-raising.
- **Detection** (prose vs structured): attempt `json.loads(summary)`; if it yields a
  dict/list → structured (humanize); otherwise → prose (passthrough). Do NOT use a naive
  leading-`{` check (prose can contain braces).
- **Humanization** of structured digests: readable text via lens-aware key extraction
  (technicals: breadth + momentum highlights; derivatives: VIX + term-structure regime;
  macro: key series values; fundamentals: coverage count + a highlight; sentiment: article
  count + tone-or-unavailable) OR a clean generic key-value collapse. The exact prose quality
  is gated by the **ux-expert** against AC-2/AC-4 ("readable, no raw JSON").
- Template (`templates/ai_advisor.html`): render the humanized text in `.prism-lens-text`
  instead of the raw summary. Keep Jinja autoescaping (no `|safe` on humanized text).
- `obs-raw-preview`: humanize or hide via the render-prep/template.

## Edge Cases
- summary None / "" / "null" / "None" → empty-state (AC-3).
- prose containing a brace or `$416B` → JSON-parse detection avoids misclassification (AC-1).
- council prose with numbers/symbols → passthrough.
- lens dict missing "summary" key, or `per_lens_digest` missing/empty → honest "no lens data".
- extremely long fundamentals JSON → concise summary, never full dump (AC-4).
- a `summary` that parses as a bare JSON scalar (e.g. `"16.41"`) → treat as prose/text, not an object.

## Security Considerations
- Render-layer only; no new external input; no execution-path or credential change.
- XSS: humanized text rendered through existing Jinja autoescaping; never `|safe` raw content.
- D-1 spirit: helper never raises; no exception/secret leakage into the rendered page.

## Testing Strategy
- **Unit (quant-test-writer, RED-first):** humanizer over real-captured fixtures — prose
  passthrough (council row prose), JSON→readable for each of the 5 lens_pipeline shapes
  (captured from live row 77), null/empty→empty-state, malformed→fallback, and a never-raises
  sweep over junk inputs. **Fixture provenance = captured-from-producer** (live lens_pipeline
  row 77 JSON + a council row's prose) — recorded in the fixture files.
- **Route/render test:** GET /ai-advisor renders the Market Prism block with a fixture row;
  assert NO raw-JSON markers (`{"`, `":`) in the per-lens text; assert council prose passes
  through; assert empty-state for a null-summary lens. Mock DB only; render the real template.
- **Visual gate (ux-expert, mandatory):** run a worktree daemon on port 8091 with the fixed
  template + a seeded lens_pipeline-style MARKET_PRISM row → render the Overview → READ the
  screenshot → confirm no raw JSON + readable per-lens prose + working parts intact (chip,
  rationale, sources, datetime). Then render a council-style row to confirm prose passthrough.

## Scope Boundaries
- **IN:** render-layer humanization of `per_lens_digest` for the Overview Market Prism block;
  `obs-raw-preview` raw-JSON fix; empty-state for null/unavailable; defensive never-raise.
- **OUT:** changing `lens_pipeline` data production (council supersedes it — item #4); council
  orchestration; non-English GDELT source quality (sentiment/news-lens data concern); the
  `run_ts`-holds-UUID issue (only surfaces under the council producer — item #4);
  cited-sources timestamp prettification (optional; fold in only if trivial, not required).
