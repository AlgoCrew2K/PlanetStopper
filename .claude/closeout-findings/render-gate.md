# Render Gate Evidence — AC-9 + AC-13
**Auditor:** closeout-ux  
**Date:** 2026-06-17  
**Branch tip:** b1b62277ee5d8b667e76344fe80e45d7f7fdc69c (audit/ai-council-closeout-e2e)  
**Working tree:** clean (## audit/ai-council-closeout-e2e, no modifications)  
**Origin sync:** 1 ahead of origin/main (73dc603)  
**UA:** Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:151.0) Gecko/20100101 Firefox/151.0  
**Engine:** Firefox/Gecko  
**Viewport:** default desktop (~1400px wide)  
**Theme:** light (single theme — no dark-mode toggle found)  
**Live daemon:** http://localhost:8090 (pre-C1 deploy; C1 code merged @ 73dc603, deploy pending PM post-close)

---

## Noted gap: no declared canonical breakpoints

The project CLAUDE.md and the Design-System Mapping section of the closeout matrix do not
declare canonical breakpoints for this SPA. Audit conducted at default desktop viewport only.
This gap is recorded — no breakpoints invented.

---

## Pre-flight checks

- Branch tip SHA captured this turn: `b1b6227`
- UA captured this turn via `browser_evaluate(() => navigator.userAgent)`: Firefox/151.0 (Gecko)
- Design-System Mapping loaded from `feature-plans/market-prism-closeout-e2e-verification.md`:
  sentiment chip must use one of `prism-sentiment-chip--{risk-on,risk-off,neutral,limited-inputs}`,
  not hardcoded RGB values.

---

## Tab-by-tab findings

### 1. Overview tab (AC-9)

**Screenshots:** `ux-overview-desktop.png`, `ux-lens-cards-crop.png` (zoomed lens cards)  
**Console errors:** 0

**What I see on screen (ux-overview-desktop.png):**
- Dark teal header with "Planet Stopper guard" branding and nav links
- "AI Advisor" heading with subtitle
- Tab bar: all 6 tabs present, Overview active
- Green "RISK ON" chip near top of content area
- Rationale text: "Broad technical strength with 80% breadth, most sectors above SMA50, positive momentum across equities/financials, low VIX at 16.2, stable macro conditions (4.3% unemployment, 4.47% 10-year yield)..."
- Five lens cards below the rationale — at thumbnail scale appear as dense monospace blocks
- Cited sources block below with real URLs

**Sentiment chip DOM:**
- `className`: `prism-sentiment-chip prism-sentiment-chip--risk-on`  ✅ correct semantic class
- `text`: `risk-on`  ✅
- `inlineStyle`: `null`  ✅ no hardcoded inline color

**Market Prism block structure (`.prism-block`):**
- `.prism-block-header`: "Market Prism — As of 2026-06-17 09:03:09"  ✅ real nightly row
- `.prism-rationale` (p tag): real synthesis text  ✅
- `.prism-lenses`: 5 children, all `prism-lens prism-lens--available`  ✅
- Sources section: present with real external URLs  ✅

**Raw color leakage check:**
- 198 elements with inline `color`/`background` — all use `var(--studio-*)` tokens
- Only 2 non-token values: `background:transparent` on modal buttons — valid semantic values
- **Zero raw hex/RGB literals in inline styles**  ✅

---

### FINDING — RQ-1: Lens card bodies render raw JSON, not a human-readable digest

**Severity:** Render quality / content readability (not a design-token or JS error failure)  
**Screenshot evidence:** `ux-lens-cards-crop.png` (zoomed — see below)  
**Status:** CONFIRMED via DOM + template inspection + eyes-on screenshot

**What I see in ux-lens-cards-crop.png:**  
The fundamentals card body is visible at this scroll position. It shows dense, unformatted
JSON text: `"entity_name": "JPMORGAN CHASE & CO.", "cik": 19617, "key_facts": {"Revenues":
{"label": "Revenue", "value": 158104000000, "unit": "USD", "end": "2023-12-31", "filed":
"2026-02-13", "form": "10-K"}, "NetIncomeLoss": ...` — raw serialised dict, not a
formatted human digest.

**DOM evidence — all 5 lens cards, `.prism-lens-text` `<p>` content:**

| Lens | `.prism-lens-text` content |
|---|---|
| technicals | `{"ma_posture": {"XLF": {"above_sma50": true, "above_sma200": null}, ...}` — full raw JSON dict |
| sentiment | `{"article_count": 10, "tone_summary": null, "tone_score": null}` — raw JSON |
| derivatives | `{"vix_level": 16.2, "vix_term_structure": {"spot": 16.2, "term_3m": 19.36, "ratio": 0.8367..., "spread": -3.16, "regime": "contango"}, "risk_read": "neutral", "as_of_date": "2026-06-15"}` — raw JSON |
| macro | `{"series": {"DGS10": {"label": "10-Year Treasury Constant Maturity Rate", "value": "4.47", "date": "2026-06-15"}, ...}}` — raw JSON |
| fundamentals | `{"tickers": {"AAPL": {"entity_name": "Apple Inc.", "cik": 320193, "key_facts": {"Revenues": {...}, ...}}}}` — raw JSON, very long |

**Root cause — two-layer issue:**

1. **`lens_pipeline.py:166-167`** (`_build_per_lens_digest`): when a lens block has no
   `"summary"` key but has a `"payload"`, the fallback is:
   ```python
   entry["summary"] = json.dumps(block["payload"])
   ```
   This serialises the raw payload dict as a JSON string and stores it as `summary`.
   The lenses (technicals, sentiment, derivatives, macro, fundamentals) all produce
   structured payload dicts with no human-readable `"summary"` string key — so all 5
   hit the `json.dumps(payload)` fallback path.

2. **`templates/ai_advisor.html:994-995`**: the template emits whatever `_lens.get('summary')`
   contains verbatim into a `<p class="prism-lens-text">`:
   ```jinja
   {% if _lens.get('summary') %}
       <p class="prism-lens-text">{{ _lens.get('summary') | e }}</p>
   {% endif %}
   ```
   With `summary` = `json.dumps(payload)`, the result is a raw JSON blob in the paragraph.

**Correct intended behaviour:** the per-lens digest should show a human-readable summary
(e.g. "Breadth: 80%, 9/10 tickers above SMA50, momentum positive" for technicals). The
`summary` field was intended to be a prose string from the lens builder, not a JSON dump.
Either (a) the lens builders should emit a `"summary"` prose string, or (b) the template
should render structured fields from `payload` instead of falling back to `json.dumps`.

**Scope note:** this is a content-readability defect in the per-lens digest rendering. It
does not affect:
- Sentiment chip class / design-system token compliance (still PASS)
- Zero console errors (still 0)
- Zero raw color leakage (still PASS)
- Tab switching / SPA shell (still PASS)
- The synthesis rationale (still human-readable prose — the `prism-rationale` p tag is correct)
- The overall `risk-on` verdict being correctly bound and displayed

The defect means the per-lens cards are **present and structurally correct** (correct CSS
classes, available status, real data) but **not human-readable** — an operator looking at
this page sees raw JSON blobs instead of a formatted per-lens summary.

**Testable requirement for the fix cycle:**
- Assert that each `.prism-lens-text` element's text content does NOT start with `{` (i.e.
  is not a raw JSON object dump)
- Assert that each available lens card renders at minimum one human-readable label+value
  pair (not a serialised dict literal)
- The fix must not assert specific computed values — assert that `summary` is a non-empty
  string that does not parse as a JSON object

---

**AC-9 revised verdict: CONDITIONAL PASS with RQ-1 finding**
- Sentiment chip: PASS (correct semantic class, no inline style)
- Rationale prose: PASS (human-readable synthesis text)
- 5 lenses present + available status: PASS
- Cited sources present: PASS
- Per-lens card body content: FAIL — raw JSON dump, not human-readable digest (RQ-1)
- Design-system token contract: PASS
- Zero console errors: PASS

AC-9 is NOT a clean PASS on content readability. RQ-1 must be filed and adjudicated
(regression vs pre-existing) before the gate can be fully accepted.

---

### 2. Correlations tab

**Screenshot:** `ux-correlations-desktop.png` | Console errors: 0  
Full pairwise matrix, 78 pairs, real data, token-based color coding. **PASS**

### 3. Asset Swaps tab

**Screenshot:** `ux-assetswaps-desktop.png` | Console errors: 0  
TRY A SWAP form in default state, advisory banner, observations table. **PASS**

### 4. Logic Changes tab

**Screenshot:** `ux-logicchanges-desktop.png` | Console errors: 0  
TRY A LOGIC TWEAK form in default state, FDR banner, observations table. **PASS**

### 5. Chat tab

**Screenshot:** `ux-chat-desktop.png` | Console errors: 0  
Correct artifact-not-selected empty state (informative prompt, not blank). **PASS**

### 6. Strategy Builder tab

**Screenshot:** `ux-strategybuilder-desktop.png` | Console errors: 0  
PROPOSE NEW STRATEGIES form in default state; FDR-rejection result card (valid outcome). **PASS**

---

## Design-system contract assertions (AC-13)

| Assertion | Result |
|---|---|
| All 6 tabs present in tab bar | PASS |
| Tab switching in-place (URL stays `/ai-advisor`) | PASS |
| Sentiment chip class = `prism-sentiment-chip--risk-on` (semantic, not hardcoded) | PASS |
| Sentiment chip has no inline `style` attribute | PASS |
| Zero raw hex/RGB color literals in inline styles across entire page | PASS |
| All inline color styles use `var(--studio-*)` tokens | PASS |
| Zero JS console errors across all 6 tabs | PASS |
| No dark-theme bleed on light-mode card surfaces | PASS |
| No content overflow or cutoff at default desktop viewport | PASS |
| Market Prism block present with real nightly row (not empty-state) | PASS |
| All 5 lenses show `prism-lens--available` | PASS |
| Rationale text human-readable prose | PASS |
| Per-lens card body human-readable (not raw JSON) | **FAIL — RQ-1** |
| Cited sources present | PASS |
| Interactive elements in correct default state | PASS |

---

## No-declared-breakpoints gap

No canonical breakpoints declared in CLAUDE.md or the Design-System Mapping. Audit is
desktop-only. Non-blocking.

---

## Leave-state

- URL: `http://localhost:8090/ai-advisor`
- Active tab: Overview (restored)
- Viewport: default desktop
- Theme: light (default)
- Daemon: running (not started or stopped by this audit)
- No POST actions taken

---

## Overall verdict

**AC-13: PASS** — All 6 SPA tabs render live with in-place switching, zero JS errors,
zero raw-color leakage, correct design-system token usage throughout.

**AC-9: CONDITIONAL — 1 finding (RQ-1)**  
The design-system contract (chip class, tokens, zero leakage) and structural rendering
(block present, rationale prose, lenses available, sources present) all pass. The
per-lens card body content fails the human-readability check: all 5 lens cards render
raw `json.dumps(payload)` blobs instead of a formatted human digest.

**RQ-1 finding:**
- Element: `<p class="prism-lens-text">` inside each `.prism-lens` card
- Template: `templates/ai_advisor.html:995` — `{{ _lens.get('summary') | e }}`
- Root cause: `lens_pipeline.py:166-167` fallback `entry["summary"] = json.dumps(block["payload"])`
  fires for all 5 lenses (none emit a prose `"summary"` key); template emits it verbatim
- Screenshot: `ux-lens-cards-crop.png`
- Whether this is a regression (introduced by a recent change) or pre-existing is for
  closeout-synth / PM to classify — ux-expert's role is to flag it, not to classify or fix it
