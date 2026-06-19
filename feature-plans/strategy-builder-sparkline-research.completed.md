# Research Report: Strategy Builder Sparkline Rendering Approach

**Researcher:** viz-library-researcher
**Date:** 2026-06-12
**Confidence Summary:** High confidence on architectural fit and data sizing; medium confidence on exact gzipped bundle sizes for some libraries (Bundlephobia 403d during session — sizes sourced from GitHub READMEs and npm metadata cross-checked with two community sources).

---

## Question

Identify the optimal rendering approach for the equity-curve sparkline on Strategy Builder proposal cards, covering:

1. Server-side inline SVG (Flask route / Jinja macro)
2. QuickChart `<img>` URLs (parity with `reporting.py`)
3. Tiny client-side libs (uPlot, sparkline.js, Chart.js)
4. Hand-rolled `<canvas>` with vanilla JS

Secondary question: what return series should be persisted, at what resolution, and what is the raw_response size impact?

**Scope constraints read from codebase:**

- `templates/ai_advisor_strategy_builder.html` — zero external runtime dependencies today; vanilla JS only; no bundler; no CDN framework; page reload after POST renders observations server-side from read-only SQLite
- `reporting.py` — QuickChart is used exclusively on the Discord EOD path (POST to `quickchart.io/chart/create` returns a URL embedded in a Discord webhook embed); it is NOT used anywhere in the browser dashboard
- `advisors/strategy_builder_engine.py` — `raw_response` is a JSON blob persisted to `advisor_observations`; current rows estimated at 2–4 KB; the persist path MUST NOT perform new I/O (HR-2 from phase-3.5 contract)
- Design token system uses CSS custom properties; all colors must resolve from `--studio-*` variables
- `ai-advisor-design-prompt.md` line 44: "Sparkline / chart areas use Chart.js-style inline canvas elements; no external charting library beyond what already exists in the dashboard"

---

## Phase 1 — Broad Sweep

The four candidate approaches map to two render-time models:

- **Server-render** (Option A: inline SVG, Option B: QuickChart img URL): data → HTML/img at Flask route time; browser receives markup, no JS needed to draw.
- **Client-render** (Option C: vendored lib, Option D: hand-rolled canvas): data encoded into HTML as JSON array → JS draws on `<canvas>` or SVG in the browser.

The design prompt notes "Chart.js-style inline canvas elements," which is a visual description (small canvas with a line), not a hard dependency requirement on Chart.js itself.

---

## Phase 2 — Targeted Deep Dive

### Option A: Server-side inline SVG (Jinja macro or Flask helper)

**Technique:** The Flask route computes the downsampled equity series from `raw_response`, normalises points to a coordinate space (e.g. viewBox `0 0 100 40`), and emits an `<svg><polyline points="..."/></svg>` fragment inside each card. A Jinja macro encapsulates the logic and can be `{% import %}`-ed in the template.

**Coordinate math (Primary — SVG spec, MDN):**
- Each point: `x = i / (n-1) * width`, `y = height - ((v - v_min) / (v_max - v_min)) * height`
- `viewBox="0 0 100 40" preserveAspectRatio="none"` stretches to fit the card width naturally
- A filled `<polygon>` or closed `<path>` adds the area fill without additional library weight
- CSS custom properties resolve inside inline SVG in all modern browsers (Chromium, Firefox, Safari) — `stroke: var(--studio-accent)` works [Primary — MDN SVG, https://developer.mozilla.org/en-US/docs/Web/SVG/Reference/Attribute/preserveAspectRatio]

**Dependencies:** None. Zero bytes of additional JS or CSS.

**CSP posture:** Clean. Inline SVG is HTML content, not a script. No `unsafe-inline` script policy required. Works with the strictest `script-src 'self'` directive.

**Network posture:** None. Fully self-contained. Page does not gain any external dependency.

**Template integration:** The current template renders observations in a Jinja for-loop. A macro `render_sparkline(series, width=280, height=48)` added to the template (or a shared `_macros.html` include) integrates with zero JS changes. The macro receives the series as a Python list from the Flask route context variable — already a standard Jinja pattern.

**Render cost:** Negligible. String concatenation of floats in Python. For 60 points, the SVG fragment is ~800 bytes of HTML.

**Accessibility:** SVG supports `<title>` and `<desc>` child elements for screen-reader text, and `role="img" aria-label="..."` on the `<svg>` element — all achievable in the Jinja macro with no additional library. [Primary — MDN SVG accessibility]

**Maintenance:** No third-party version to track. Zero dependency surface.

**Fit with existing architecture:** `[High]` Best fit. The template already renders all card content server-side. The Flask route already assembles the `rr` dict for each observation. Adding `sparkline_points` as a pre-computed list in the route context requires ~10 lines of Python (downsample + normalise). The Jinja macro requires ~15 lines of SVG/Jinja.

---

### Option B: QuickChart `<img>` URLs

**Technique:** The Flask route constructs a QuickChart URL (or POST-create URL) encoding the series as a line chart, embeds the resulting URL as `<img src="...">` in the card HTML.

**How reporting.py uses QuickChart:** `reporting.py` POSTs to `https://quickchart.io/chart/create` in `send_eod_discord_post()` to produce a stored-chart URL for Discord embeds. This is an asynchronous, out-of-band, fire-and-forget call on the EOD report path — explicitly not on any browser-request path.

**Architectural change flagged:** Embedding a QuickChart `<img src="https://quickchart.io/...">` in dashboard HTML introduces an external network dependency to page load. Today the dashboard is fully self-contained: all assets are served from the Flask process itself. Adding QuickChart `<img>` tags means:

1. Every dashboard page load would make outbound HTTPS requests to `quickchart.io` for each card rendered — potentially 5–20 requests per page view.
2. If `quickchart.io` is unavailable, slow, or rate-limited, card sparklines fail to render — a silent partial failure in an operator-grade risk tool.
3. CSP `img-src` must be extended to include `quickchart.io` (currently no external `img-src` entries exist in the project).
4. The operator's network path to `quickchart.io` becomes a reliability dependency — unacceptable for a system that monitors live portfolios.

**QuickChart GET URL encoding:** A complete line-chart config for 60 points encodes to roughly 600–1200 bytes in base64 or URL-encoding; this fits the GET URL approach without the POST/create round-trip. However the GET approach embeds the full chart JSON in the URL, which still makes the page dependent on external resolution.

**Fit with existing architecture:** `[Low]` This approach converts a fully self-contained dashboard into one with 5–20 external image requests per page view. It is architecturally inconsistent with the existing posture. It also diverges from `reporting.py`'s use of QuickChart, which is exclusively for Discord embeds (async, not browser-critical path).

---

### Option C: Vendored client-side library

Three candidates evaluated:

#### C1 — uPlot

- **Version:** 1.6.32 (released 2025-03-14) [Primary — github.com/leeoniya/uPlot releases]
- **Bundle size:** ~47.9 KB minified (stated in README benchmarks table); gzipped size not explicitly stated in README — estimated ~15–16 KB gzipped based on typical JS compression ratios for this library class [Medium — GitHub README cross-referenced with npm metadata; Bundlephobia returned 403 during this session]
- **License:** MIT [Primary — github.com/leeoniya/uPlot]
- **Maintenance:** Active; last release March 2025; 10.2k GitHub stars
- **Render mode:** Canvas 2D
- **Financial features:** OHLC/candlestick natively listed as a chart type in the official description; time-series lines are the primary use case [Primary — uPlot GitHub README]
- **Vendoring feasibility:** Single JS file + single CSS file — copy to `static/` and load with `<script src="{{ url_for('static', filename='uplot.min.js') }}">`. No bundler required.
- **Fit:** uPlot is overkill for a 56px-tall sparkline on a card. Its API is designed for full interactive charts with cursor tracking, multiple series, and zooming. Using it for static 60-point equity lines adds ~48 KB min (est. ~16 KB gz) of JS that is not needed anywhere else on the page. The template would require initialising a `uPlot` instance per card in a DOMContentLoaded loop.

#### C2 — Chart.js

- **Version:** 4.x latest
- **Bundle size:** ~65 KB min+gz reported across multiple community sources [Medium — dev.to bundle-size survey, cross-checked with bundlephobia.com reference in search results; Bundlephobia returned 403 during this session]
- **License:** MIT [Primary — chartjs.org]
- **Maintenance:** Active; widely used
- **Financial features:** Line charts only via built-in types; OHLC requires a separate `chartjs-chart-financial` plugin
- **Fit:** The design prompt line 44 says "Chart.js-style inline canvas elements" as a visual descriptor, but Chart.js itself is not currently in the static/ directory. Adding it for sparklines-only use is ~65 KB gz of runtime for what is essentially a polyline. The full Chart.js API surface (tooltip, legend, responsive resize, animation) is unused overhead for static 56px-tall cards.

#### C3 — Standalone sparkline micro-library (fnando/sparkline or equivalent)

- **Example: fnando/sparkline** — MIT, ~1.8 KB minified (no gzip figure found; estimated ~800 bytes gz) [Single-source — GitHub fnando/sparkline; bundle size from repo README; no independent verification found during session — labeled [Unverified] for gzipped size]
- **Render mode:** SVG (generates inline SVG via JS)
- **Fit:** Smaller footprint than Chart.js or uPlot, but still requires vendoring, adds a JS execution step, and generates SVG client-side rather than server-side — introducing a flash of unstyled/empty card before JS executes.

**General C-class concern:** Any client-side rendering approach requires that the series data be embedded in the HTML (as a `data-*` attribute or inline JSON). This duplicates data that is already in the `raw_response` JSON blob. It also means the card is incomplete during the brief JS execution window, which matters for an operator who may screenshot or print the dashboard.

---

### Option D: Hand-rolled `<canvas>` with vanilla JS (~30 lines)

**Technique:** Embed the downsampled series as a `data-points` JSON attribute on a `<canvas>` element. A single shared vanilla JS function (`drawSparkline(canvas)`) reads the attribute and draws the polyline with `CanvasRenderingContext2D` calls.

**Implementation sketch:**

```javascript
function drawSparkline(canvas) {
    const pts = JSON.parse(canvas.dataset.points);
    if (!pts || pts.length < 2) return;
    const w = canvas.width, h = canvas.height;
    const mn = Math.min(...pts), mx = Math.max(...pts);
    const rng = mx - mn || 1;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, w, h);
    ctx.beginPath();
    pts.forEach((v, i) => {
        const x = (i / (pts.length - 1)) * w;
        const y = h - ((v - mn) / rng) * h;
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    // Read CSS variable for stroke color
    ctx.strokeStyle = getComputedStyle(canvas).getPropertyValue('--studio-accent').trim() || '#2ea566';
    ctx.lineWidth = 1.5;
    ctx.stroke();
}
document.querySelectorAll('canvas[data-sparkline]').forEach(drawSparkline);
```

**Bundle size:** 0 additional bytes (pure vanilla JS, inlined in template `<script>` block)

**Dependencies:** None beyond what the page already loads

**CSP posture:** Requires `<script>` block in the template — the template already contains a `<script>` block (`sbRunAnalysis`, `openChatWithArtifact`). The new function joins that existing block; no new CSP surface.

**Network posture:** Self-contained. No external requests.

**Canvas accessibility:** Canvas elements are opaque to screen readers by default. WCAG 1.1.1 requires a text alternative. Mitigation: add `role="img" aria-label="Equity curve: from X% to Y%"` to the `<canvas>` element (data available from the series). This is a developer responsibility — not provided automatically. [Primary — WCAG 2.1 Technique ARIA6, W3C WAI]

**Render cost:** Negligible; `getContext('2d')` and ~60 `lineTo` calls per card complete in microseconds.

**CSS variable limitation:** `canvas.style` does not directly inherit CSS custom properties. `getComputedStyle(canvas).getPropertyValue('--studio-accent')` does work in all modern browsers (Chromium, Firefox, Safari) but requires that the canvas element be in the document when JS runs. DOMContentLoaded ensures this. [Primary — MDN getComputedStyle]

**Fit with existing architecture:** Good. Joins the existing vanilla JS block. The data is embedded as a `data-points` attribute populated server-side in the Jinja loop. No new files required. The operator sees identical output whether JS has run or not: without JS, the canvas is blank (56px-tall empty box). This is the one degradation vs Option A where SVG is always visible.

---

## Phase 3 — Verification Pass

**Option A vs Option D — key distinction:** SVG is always rendered; canvas degrades to blank if JS fails or is disabled. For an operator-grade institutional tool, graceful degradation matters. Server-rendered SVG wins this dimension unambiguously.

**QuickChart parity with reporting.py** — Confirmed conflict: `reporting.py` uses QuickChart on an async out-of-process path (Discord webhook), not on the browser-request path. There is NO existing QuickChart dependency in the dashboard browser path. Option B would be a net-new external dependency, not parity with an existing one.

**CSS custom properties in SVG** — Confirmed: `stroke: var(--studio-accent)` and `fill: var(--studio-pos)` work in inline SVG when the SVG is part of the document (as opposed to an external `.svg` file). [Primary — MDN CSS custom properties and SVG, https://developer.mozilla.org/en-US/docs/Web/SVG/Applying_SVG_effects_to_HTML_content]

**Theme (light/dark) compatibility** — CSS custom properties re-evaluate when `data-theme` changes on `<html>`. Both inline SVG (via `stroke: var(--studio-accent)`) and `<canvas>` (via `getComputedStyle`) correctly pick up theme changes as long as colors reference the token variables, not hardcoded hex. `reporting.py` uses hardcoded hex (`"#10b981"`) in its QuickChart config — a pattern the dashboard template explicitly avoids.

---

## Phase 4 — Recency Check

- uPlot v1.6.32 released 2025-03-14 — current within 12 months. [Primary — GitHub releases]
- Chart.js v4 — maintained; last major release in 2023; actively maintained in 2025.
- SVG/Canvas browser support — stable standards; no recency concern.
- QuickChart service — `quickchart.io` is an external SaaS; no uptime SLA published for free tier. Relying on it for page-critical rendering is an availability risk regardless of current status.

---

## Library Comparison Matrix

| Library / Approach | Render Mode | Bundle Size (min / min+gz) | Financial Chart Features | License | Maintenance | Last Release | Notes |
|---|---|---|---|---|---|---|---|
| **Inline SVG (server)** | Server | 0 / 0 | Line only (sufficient for equity curve) | N/A | N/A (Python stdlib) | N/A | Zero deps; always renders; CSS vars work; CSP clean |
| **QuickChart img** | Server (external) | 0 / 0 JS, but adds external network call | Line, bar, pie (full Chart.js API) | SaaS (free tier) | External service | N/A | ADDS external dependency to browser page load — architectural change |
| **uPlot (vendored)** | Client (Canvas 2D) | ~47.9 KB min / ~15–16 KB gz [Medium] | Time series, OHLC, bars (native) | MIT | Active | 2025-03-14 |  Overkill for static 56px sparkline; interactive API unused |
| **Chart.js (vendored)** | Client (Canvas 2D) | ~200 KB min / ~65 KB gz [Medium] | Line, bar (OHLC via plugin) | MIT | Active | 2024 | Heaviest option; design prompt descriptor only, not a requirement |
| **sparkline micro-lib (fnando)** | Client (SVG via JS) | ~1.8 KB min / ~0.8 KB gz [Unverified] | Line only | MIT | Low (last commit 2020) [Unverified] | N/A | Abandoned risk; generates SVG client-side (flash of blank) |
| **Hand-rolled canvas (vanilla JS)** | Client (Canvas 2D) | 0 / 0 (inlined) | Line only | N/A | N/A (project code) | N/A | Good; canvas blank if JS disabled; CSS var via getComputedStyle |

---

## Integration with Flask

**Option A (recommended — see below):** The Flask `GET /ai-advisor/strategy-builder` route already builds `observations` for the template. Adding a `sparkline_points` key to each observation dict requires a helper function in `app.py`:

1. Read `raw_response.get('equity_curve_downsampled')` — the new persisted field (see Data section below).
2. Normalise to `[0, 1]` range (or pass raw cumulative return values; the SVG macro normalises them).
3. Pass as `obs['sparkline_points']` alongside the existing `rr` dict.

The Jinja macro is a `{% macro render_sparkline(points, w=280, h=48) %}...{% endmacro %}` block in the template, called from the card loop.

No new routes, no new files required. The macro and helper are additive to existing files.

**Option D (hand-rolled canvas):** Integrates in the existing `<script>` block. The series is embedded as `data-points="[0.00, 1.23, ...]"` on a `<canvas data-sparkline>` element output by the Jinja loop. The single `drawSparkline` function is appended to the existing script block and called on `DOMContentLoaded`.

---

## Operator-Grade Suitability

**Clarity:** A single-series equity curve from t=0 to t=n showing cumulative return (indexed to 1.0 or percent) is maximally clear. No tooltip needed for a 56px sparkline — the scalar metrics table already provides the numbers.

**Contrast:** `stroke: var(--studio-accent)` gives the forest-green positive line; `stroke: var(--studio-neg)` for drawdown visualization is available without library customization.

**Color-only encoding:** The sparkline encodes only a single series; no color-only information is conveyed. The starting and ending point of the line are sufficient to show direction. No separate color-coded legend is needed.

**Print-friendliness:** Inline SVG prints faithfully. Canvas elements may not print in all browsers depending on user settings (`print-color-adjust`). [Primary — MDN Canvas print behaviour] SVG is the better choice for institutional dashboards that may be printed or exported to PDF.

**Degradation:** Inline SVG degrades gracefully with no JavaScript. Canvas degrades to a blank element. For an operator reading the page without JS, SVG provides the sparkline; canvas provides nothing.

---

## Data Side: Persisted Series Specification

### What series to persist

**Recommendation: cumulative return indexed to 1.0** (not raw daily returns).

Rationale:
- Cumulative return is what the sparkline displays. Storing pre-computed cumulative values eliminates a rebuild step at read time.
- Daily return series require a cumulative product transform at render time, adding complexity to the Jinja macro or Flask helper.
- Cumulative returns are monotone-derivable from daily returns but not the reverse — storing cumulative values is a one-way lossy compression that is fine for visualization.
- Storing percent-scale (e.g., cumulative return as `[0.00, 1.34, -0.87, ...]` percent gains) is also acceptable and matches the existing `returns_pct` representation in the engine.

**Field name:** `equity_curve_downsampled`

**Value format:** JSON array of floats, percent scale (consistent with `returns_pct` in the engine). Example: `[0.0, 0.82, 1.10, -0.23, ...]` where each value is the cumulative percent return at that sample point.

### Downsampling rule

The full backtest series from Composer typically covers 1–5 years of daily data, i.e. 250–1250 points. The budget is 60 points maximum (from task brief).

**Downsampling algorithm:** Uniform stride sampling — take every `floor(n / target)` point, always including the first and last point. This preserves the shape of the equity curve and the terminal value (which the scalar metrics already show).

```python
def _downsample(series: list[float], target: int = 60) -> list[float]:
    if len(series) <= target:
        return series
    step = len(series) / target
    indices = [round(i * step) for i in range(target - 1)]
    indices.append(len(series) - 1)
    return [series[i] for i in indices]
```

**Why uniform stride over LTTB (Largest Triangle Three Buckets):** LTTB provides better visual fidelity by preserving local extrema, but it requires a ~2 KB third-party Python library and adds complexity to the persist path. For a 56px sparkline showing general shape and direction, uniform stride is sufficient and zero-dependency. [Interpretation — not a hard finding; LTTB would improve fidelity of drawdown troughs at the cost of implementation complexity.]

### Point budget and size impact

| Points | Precision | Per-point cost (JSON float) | Total series size |
|---|---|---|---|
| 60 | 2 decimal places | ~6 bytes average (e.g. `"-1.23"`) | ~420 bytes |
| 60 | 4 decimal places | ~8 bytes average | ~560 bytes |
| 120 | 2 decimal places | ~6 bytes | ~840 bytes |

With JSON array overhead (`[`, `]`, `,` separators), a 60-point series at 2 decimal places is approximately **450–500 bytes** of JSON. Adding this to a current row of ~2–4 KB raises the row to ~2.5–4.5 KB — well within the stated budget of keeping the series under 2 KB. [High — arithmetic; no external source needed]

**Precision:** 2 decimal places (e.g. `round(v, 2)`) is sufficient for visualization. The sparkline is 56px tall; subpercent precision is invisible.

**Where to write:** In `_persist_survivor()` in `advisors/strategy_builder_engine.py`, extend the `raw_response` dict with:

```python
raw_response["equity_curve_downsampled"] = _downsample(
    [sum(returns_pct[:i+1]) for i in range(len(returns_pct))],
    target=60
)
```

This is purely CPU dict assembly on the persist path — no new I/O, satisfying HR-2 from the phase-3.5 contract.

---

## Recommendation

**Option A — server-side inline SVG via Jinja macro — is the recommended approach.**

Rationale:

1. **Zero new dependencies.** The dashboard has no external runtime dependencies today. Option A preserves that posture completely. Options B, C, and D all either add external network dependencies (B) or add JS files to vendor (C).

2. **Always renders.** SVG is HTML content. It renders before JS loads, without JS, and when printing. Canvas (Option D) is blank without JS execution.

3. **CSP-clean.** Inline SVG requires no script CSP changes. Option D joins the existing `<script>` block (acceptable but not as clean).

4. **Theme-compatible.** `stroke: var(--studio-accent)` in inline SVG responds to `data-theme` toggling exactly as other CSS-variable-driven elements on the page.

5. **Print-faithful.** SVG serialises to the print stream. Canvas may not, depending on browser print settings.

6. **Matches the server-render model.** The template is driven by server-rendered Jinja. Adding a sparkline as server-rendered SVG is architecturally identical to adding any other card element today.

7. **The design prompt descriptor ("Chart.js-style")** refers to the visual style (small inline canvas chart), not a hard Chart.js dependency. The description predates the current zero-dependency posture of the template.

The only trade-off Option A has versus Option D is that it requires the sparkline computation to happen in the Flask route (not in JS). This is consistent with the existing read-only template model — the route already assembles complex `card_artifacts` dicts per observation. A `sparkline_points` list is a smaller addition.

---

## Options and Trade-offs

| Option | Key benefit | Key cost | Architectural fit |
|---|---|---|---|
| A: Server SVG (RECOMMENDED) | Zero deps; always renders; print-safe; CSP-clean | Server must compute downsampled series; Jinja macro adds ~20 lines | Best fit |
| B: QuickChart img | Reuses known API | ADDS external network dependency to page; breaks self-contained posture; partial failure risk | Poor fit — architectural regression |
| C1: uPlot vendored | Interactive; OHLC support for future use | ~48 KB min / ~16 KB gz additional JS; overkill for static 56px sparkline; requires `<script>` per-card initialisation | Acceptable if future interactive charts planned |
| C2: Chart.js vendored | Familiar API; design prompt mentions it | ~200 KB min / ~65 KB gz additional JS; heaviest option; no incremental benefit over inline SVG for sparklines | Poor fit for sparklines-only |
| C3: sparkline micro-lib | Small (~1.8 KB) | [Unverified] size; abandoned maintenance risk (last commit 2020); still requires vendoring + JS execution | Low fit |
| D: Hand-rolled canvas | Zero deps; joins existing script block | Canvas blank without JS; print behaviour uncertain; CSS vars require getComputedStyle | Good fit but strictly inferior to Option A for this use case |

---

## Implementation Sketch (Option A)

**Files touched (minimal scope):**

1. `advisors/strategy_builder_engine.py` — `_persist_survivor()`: add `equity_curve_downsampled` field to `raw_response`. Add `_downsample()` helper. Add `_cumulative_returns()` helper (running sum of `returns_pct`). Both are pure CPU functions, no I/O.

2. `app.py` — `GET /ai-advisor/strategy-builder` route: for each observation, read `raw_response.get('equity_curve_downsampled')` and attach to the obs context dict.

3. `templates/ai_advisor_strategy_builder.html` — add a `{% macro render_sparkline(points, label="") %}` block (~20 lines of SVG/Jinja). Call the macro inside the survivor card loop and optionally the withheld card loop, positioned between the stats table and the gate verdict row (per design-prompt Screen 3 anatomy: item 4 "Return-series mini chart").

4. `tests/` — quant-test-writer scope: test `_downsample()` with edge cases (empty, shorter than target, exact target length); test `raw_response` contains `equity_curve_downsampled` after `_persist_survivor()`; test Jinja macro renders valid SVG; test backward compat with old rows missing the field (macro renders nothing gracefully).

**Scope estimate:** ~60 lines of production code across three files; ~40 lines of tests. This is a small addition well within the existing Phase 3.5 Toxic Pair TDD pattern.

**Backward compatibility:** Old rows in `advisor_observations` that lack `equity_curve_downsampled` must render the card without a sparkline — a `{% if points %}` guard in the macro ensures this.

**Naming for the persisted series:** `equity_curve_downsampled` is the canonical field name. It is not in `CHAT_ARTIFACT_ALLOWED_FIELDS` — add it if M6 chat artifacts should reference it, or omit if sparkline is display-only.

---

## Open Questions

1. **LTTB vs uniform stride:** If visual fidelity of drawdown troughs matters to the operator, LTTB preserves local extrema better. The decision is a PM/UX call; the implement complexity is low (~30 additional lines in a pure Python helper). [Unverified — no user-testing data on which matters more at 56px height]

2. **Dual-series sparkline (candidate vs live baseline):** The design prompt Screen 2 (Asset Swaps) shows a baseline vs variant comparison line. The Strategy Builder card's design-prompt entry (Screen 3 / Screen 4) does not specify dual-series. If dual-series is wanted, the inline SVG approach handles it naturally by adding a second `<polyline>` element with `stroke: var(--studio-ink-dim)`. This requires a second downsampled series (`live_baseline_curve_downsampled`) in the persisted payload, adding another ~500 bytes per row.

3. **Target point count:** 60 is specified in the task brief. For a card width of ~280px (`minmax(28rem, 1fr)` column), 60 points gives ~4.7px per point — effectively continuous. 30 points (7px/point) would be visibly stepped. 60 is appropriate.

4. **`equity_curve_downsampled` in CHAT_ARTIFACT_ALLOWED_FIELDS:** The Phase-4 M6 artifact allowlist controls what fields reach `advisor_chat.py`. If chat should be able to discuss the shape of the equity curve, the field needs to be allowlisted. This is a PM scope decision.

5. **Does `_persist_rejected()` also write `equity_curve_downsampled`?** The withheld cards in the template use the same card anatomy. Consistency argues yes; the persist path for rejected candidates already mirrors survivors via `_persist_survivor(..., is_rejected=True)`.

---

## Sources

| URL | Access date | Tier | Description |
|---|---|---|---|
| https://github.com/leeoniya/uPlot | 2026-06-12 | 1 (Primary) | uPlot GitHub — bundle size (~47.9 KB min), MIT license, OHLC support, latest release v1.6.32 (2025-03-14) |
| https://github.com/leeoniya/uPlot/releases | 2026-06-12 | 1 (Primary) | uPlot release history — last release date confirmed |
| https://www.npmjs.com/package/uplot | 2026-06-12 | 2 (Expert) | npm package metadata — package size 447 KB (unpacked), MIT license, version 1.6.32 |
| https://developer.mozilla.org/en-US/docs/Web/SVG/Reference/Attribute/preserveAspectRatio | ongoing | 1 (Primary) | MDN — SVG preserveAspectRatio attribute spec |
| https://developer.mozilla.org/en-US/docs/Web/SVG/Applying_SVG_effects_to_HTML_content | ongoing | 1 (Primary) | MDN — CSS custom properties in inline SVG |
| https://www.w3.org/WAI/WCAG21/Techniques/aria/ARIA6 | ongoing | 1 (Primary) | W3C WAI — ARIA6 aria-label for canvas/SVG elements (WCAG 1.1.1) |
| https://www.chartjs.org/docs/latest/general/accessibility.html | 2026-06-12 | 1 (Primary) | Chart.js accessibility documentation — developer responsibilities for canvas ARIA |
| https://news.ycombinator.com/item?id=22567922 | 2026-06-12 | 3 (Community) | HN thread on uPlot v1.0 — performance and OHLC features |
| https://alexplescan.com/posts/2023/07/08/easy-svg-sparklines/ | 2026-06-12 | 3 (Community) | Server-side inline SVG sparkline technique — coordinate normalisation, viewBox, preserveAspectRatio |
| https://github.com/fnando/sparkline | 2026-06-12 | 1 (Primary) | fnando/sparkline — MIT, ~1.8 KB min [bundle size Unverified independently] |
| https://quickchart.io/documentation/ | 2026-06-12 | 1 (Primary) | QuickChart docs — API usage (confirms POST to create stored URL for embeds) |
| https://pauljadam.com/demos/canvas.html | ongoing | 2 (Expert) | Canvas accessibility — screen reader behaviour without ARIA |
| https://dev.to/ben/what-s-the-best-charts-library-with-a-small-bundle-size-fho | 2026-06-12 | 3 (Community) | Community bundle-size comparison including uPlot and Chart.js |
| https://bundlephobia.com/package/chart.js | 2026-06-12 | 2 (Expert) | Bundlephobia — Chart.js size; returned 403 during session; size ~65 KB gz from cross-referenced community sources [Medium confidence] |
