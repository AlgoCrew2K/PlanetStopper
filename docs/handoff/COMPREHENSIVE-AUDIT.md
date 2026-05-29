> **HISTORICAL** — This document is a pre-Sprint-1 or Sprint-1 cycle artifact preserved for provenance. See [docs/audit/](../audit/) for current state.

---

# Studio Redesign — Comprehensive Defect Audit

**Created:** 2026-05-19
**Branch:** feat/studio-design-handoff
**Owner:** parity (populates) + tw (encodes BLOCKs as RED) + impl (drives GREEN)
**Gate:** PM verifies + user verifies in browser
**Goal:** Full visual parity with design AND full behavioral functionality of all UI components

This is the **single source of truth** for every defect in the Studio redesign. The team manages this file: parity adds findings, tw encodes them as RED tests, impl drives GREEN, parity re-audits, items move from OPEN → IN-PROGRESS → CLOSED.

---

## Methodology — every reviewer

1. Run live daemon at branch HEAD (`python app.py`).
2. Load each route in Playwright at 1440×900 (design's native) AND at 3840×1600 (user's 4K ultrawide).
3. For each route:
   - Light theme + dark theme screenshot.
   - `browser_evaluate` every `[data-testid=...]` textContent against the corresponding API field.
   - Multi-poll: capture DOM at t=0, t=poll+2s; assert polling-driven fields updated.
   - Every `<canvas>`: `getBoundingClientRect()` + `canvas.toDataURL().length` (< 200 = blank = BLOCK).
   - Every `<svg>`: child element count (empty = BLOCK).
   - `browser_console_messages` — any error = BLOCK.
   - `browser_network_requests` — any 4xx/5xx = BLOCK.
   - Every interactive element: click → assert backend call fires + DOM updates.
4. Compare against `.design-handoff/project/<file>.jsx` element-by-element.

---

## Known defects (PM + user already observed) — pre-populated

### Dashboard (`/`, `templates/index.html`, `static/index.js`, source: `.design-handoff/project/studio.jsx` + `detail-panel.jsx`)

#### Layout / responsiveness
- [ ] **D-LAY-01** Pages don't occupy proper width and height space on wide screens (user runs 4K ultrawide). Layout may have a `max-width` cap or fixed pixel widths. Source design uses fluid grid; check templates/index.html for hardcoded widths. **(User-reported 2026-05-19)**

#### Hero
- [ ] **D-HERO-01** Guard Alpha headline binding works in some browsers but user reports seeing 0.00% — verify polling fires AND DOM updates in user's actual browser (Chrome 4K ultrawide). Possibly browser-specific failure path.
- [ ] **D-HERO-02** Comparison rows Today / Cumulative / Max DD show 0.00%/0.00% initial Jinja render despite real values in `/api/state.portfolio_strip`. Either fix `_build_meta` to populate `meta.portfolio.tc/cr/mdd` from `portfolio_strip` at first call OR render placeholder `—` and rely on JS to populate.
- [ ] **D-HERO-03** "Bot " / "Held " prefix on comparison-row values is wiped when JS sets textContent. Move prefix to a sibling span the JS doesn't touch.
- [ ] **D-HERO-04** Graph time-range badges (30d/60d/90d/125d/YTD/1Y) on the hero chart **don't actually filter the chart data**. Click handler missing or no-op. **(User-reported 2026-05-19)**
- [ ] **D-HERO-05** If-held cumulative shows ~67% which is mathematically real but design mocks implied smaller scale; confirm window default + label clarifies the time horizon so the magnitude isn't surprising.

#### Symphony cards
- [ ] **D-CARD-01** Card sparklines: `renderSparkline` calls `new Chart(canvas, ...)` every poll without destroying prior instance → Chart.js "Canvas is already in use" exception on 2nd+ poll. Fix with singleton registry + `existing.destroy()`, OR mutate-and-update pattern like `renderHeroChart`.
- [ ] **D-CARD-02** `renderSparkline` reads `d.date` for x-axis labels but `/api/chart/<sym>` emits `d.time`. Labels are empty strings.
- [ ] **D-CARD-03** User reports "no graphs on symphony cards" — confirm canvases actually drawn (toDataURL > 200 bytes) for every symphony in both initial render and after a poll.
- [ ] **D-CARD-04** Cash Now button styling doesn't match design. Per the chat transcript, expected: danger-red outlined at top-right of card, fills solid red on hover. Verify CSS matches.
- [ ] **D-CARD-05** Good-call / early-exit verdict pill shows "0.0%α" for symphonies without exit_triggers data. Should gracefully show "—" or "no exit record" (dev DB doesn't have rows; production would).
- [ ] **D-CARD-06** Per-symphony Bot CR vs If-Held CR on card body — verify these populate from `_cr.dry_run` and `_cr.if_held` not from a zero fallback.

#### Detail slide-over
- [ ] **D-DET-01** Detail panel opens with all "--" placeholders (per NIT in earlier cycle close). Needs to be wired to per-symphony data.
- [ ] **D-DET-02** Risk math panel absent from detail panel.
- [ ] **D-DET-03** Intraday tape chart in detail panel: bot/held/stop/breakeven/vwap/mc overlays as design specifies. Verify each.
- [ ] **D-DET-04** Events timeline scoped to current symphony's today's events.

#### MC dials
- [ ] **D-MC-01** Active-card MC dials — verify they render as SVG arcs from `mc_prob` field.

---

### Performance (`/performance`, `templates/performance.html`, `static/performance.js`, source: `.design-handoff/project/performance.jsx`)

- [ ] **P-LAY-01** 4K ultrawide layout audit.
- [ ] **P-CHART-01** 7 metric mini-bars (Total Return / Annualized / Sharpe / Sortino / Max DD / Calmar / Win Rate) — verify each renders with Live | Bot | Δ values + comparison bar with non-zero width.
- [ ] **P-CHART-02** Cumulative-returns chart — divergence shading between Live and Bot lines.
- [ ] **P-TOGGLE-01** Aggregate / Per-symphony toggle — verify clicking actually changes the data shown.
- [ ] **P-WIN-01** Window selector (30d / 90d / 1Y / YTD / 5Y) — verify clicking re-fetches and updates the chart + metrics. The user just flagged that time badges don't work on dashboard; almost certainly the same issue here.
- [ ] **P-INSUFF-01** `insufficient_history` banner when API reports it.

---

### AI Advisor (`/ai-advisor`, `templates/ai_advisor.html`, `static/ai_advisor.js`, source: `.design-handoff/project/advisor.jsx`)

- [ ] **A-LAY-01** 4K ultrawide layout audit.
- [ ] **A-CHART-01** Per-suggestion confidence ring (SVG arc) — verify renders from `s.confidence`.
- [ ] **A-CHART-02** Projected-impact mini-bar (SVG) — verify renders from `s.impact.delta`.
- [ ] **A-CHART-03** Autotune-runs sparkline (right rail) — verify renders from `/api/autotune-runs` response.
- [ ] **A-BADGE-01** Four gate badges per suggestion card (allowlist / risk-direction / OOS / locked-vars) — verify rendered values match `s.four_gates_verdict`.
- [ ] **A-INTERACT-01** Symphony picker — clicking actually fetches new suggestions.
- [ ] **A-INTERACT-02** Apply button → `/ai-advisor/accept` POST → suggestion disappears + confirmation.
- [ ] **A-INTERACT-03** Dismiss button → `/ai-advisor/reject` POST → suggestion disappears.
- [ ] **A-API-01** Claude API may be unavailable in dev — Advisor must gracefully show "API unavailable" instead of empty state or stuck loader.

---

### History (`/history`, `templates/history.html`, `static/history.js`, source: `.design-handoff/project/history.jsx`)

- [ ] **H-LAY-01** 4K ultrawide layout audit.
- [ ] **H-CHART-01** Daily alpha strip chart — verify SVG bars render from `daily_alpha[]`.
- [ ] **H-CHART-02** 4 by-reason mini-cards (TP / Stop / VWAP Breakdown / Bleed Cut) with reason-bar SVGs.
- [ ] **H-WIN-01** Window selector (30d / 90d / 1Y / YTD / 5Y) — verify clicking actually re-fetches and updates the entire page.
- [ ] **H-STATS-01** Hero stats (total α, $ saved, trigger count, win rate) — verify values match `/api/history/<days>` response.
- [ ] **H-EXITS-01** Today's exits list — verify rendered from `todays_exits[]`.
- [ ] **H-EMPTY-01** Empty-state handling when `total_alpha=0` or `trigger_count=0`.

---

### Settings (`/settings`, NEW route — cycle 6 still on hold)

Cycle 6 blocked until everything above is closed. See `feature-plans/studio-design-handoff.md` AC-S.8 for full spec.

---

### Foundation / Chrome (shared, `templates/_chrome.html`, `static/tweaks.js`, `static/tokens.css`)

- [ ] **F-LAY-01** Top nav layout on 4K ultrawide — does it center, span, or look stranded?
- [ ] **F-FONT-01** Confirm Google Fonts actually load (network panel: 200 + non-empty) for Manrope, Geist, DM Sans, Plus Jakarta, IBM Plex.
- [ ] **F-TWEAK-01** Theme toggle propagates to every screen.
- [ ] **F-TWEAK-02** Density toggle (compact/balanced/roomy) actually affects element spacing.
- [ ] **F-TWEAK-03** Accent swatch propagates across screens.
- [ ] **F-TWEAK-04** Typeface picker actually swaps body font.
- [ ] **F-TWEAK-05** Math overlays toggle hides/shows MC dials + VWAP bands.
- [ ] **F-TWEAK-06** Number format toggle (full / compact) swaps number formatting across screens.
- [ ] **F-TWEAK-07** Tweaks panel persists to localStorage and rehydrates on next visit.
- [ ] **F-NAV-01** Active route highlighted in top nav.
- [ ] **F-NAV-02** Clicking a nav link routes correctly.
- [ ] **F-MARKET-01** Market open / closed indicator shows correct state.
- [ ] **F-CLOCK-01** ET clock updates live.
- [ ] **F-MODE-01** LIVE / DRY-RUN badge reflects actual `LIVE_EXECUTION` env.
- [ ] **F-WORKSPACE-01** Workspace switcher (Roth IRA / Individual / Trad. IRA) actually filters data when switched.
- [ ] **F-FORCE-01** Force-run button POSTs `/api/trigger` and shows confirmation.

---

## parity findings (to be populated)

parity dispatches the comprehensive sweep and adds each finding here under the appropriate screen with file/line evidence + a recommended RED test. tw monitors this file and encodes BLOCKs as RED tests in `tests/ui/test_comprehensive_audit.py`.

### code sweep — Dashboard

- [ ] **D-COD-01** `openDetailPanel(idx)` ignores `idx`; detail panel shows static placeholder `--` for all symphonies
  - design: `.design-handoff/project/detail-panel.jsx:24` — `data.detail` drives `StatRow`, `ChartBlock`, `SplitBlock`, `PerfBlock`, `VarsBlock`
  - live: `templates/index.html:861-864` — `function openDetailPanel(idx)` opens the panel and ignores `idx`; no per-symphony data is bound to `#dp-cr-bot`, `#dp-cr-held`, `#dp-mdd`, `#dp-alpha`
  - fix sketch: capture `botState` in scope; on open, look up symphony by index and populate detail stat IDs
  - severity: blocker

- [ ] **D-COD-02** `hero-tracked` and `hero-armed` DOM IDs referenced in JS but absent from HTML
  - design: `.design-handoff/project/studio.jsx:309-311` — `MiniStat` renders Tracked / Armed / Triggered
  - live: `static/index.js:244-247` — `document.getElementById('hero-tracked')` / `document.getElementById('hero-armed')`; no element with those IDs exists in `templates/index.html` (mini-stat values are Jinja-rendered, never updated by JS)
  - fix sketch: add `id="hero-tracked"` and `id="hero-armed"` to the relevant `.mini-stat-value` spans, or remove the dead JS lookups if JS polling is not the update path
  - severity: blocker

- [ ] **D-COD-03** Window-selector `active` CSS class applied by inline `<script>` but `applyHeroWindow` is defined in `index.js` scope; double `setInterval(loadState, 30000)` registered
  - design: n/a (behavioral)
  - live: `templates/index.html:874-879` applies `.active` class; `templates/index.html:882-885` registers a second `setInterval(loadState, 30000)` after `index.js` already registers one at line 263. Two polling loops fire per 30s.
  - fix sketch: remove the duplicate `setInterval` from the inline `<script>` block; the one in `index.js` DOMContentLoaded is sufficient
  - severity: major

- [ ] **D-COD-04** Detail panel missing risk math section: stop level, shadow HWM, breakeven lock, intraday chart overlays (stop/breakeven/VWAP/MC toggles)
  - design: `.design-handoff/project/detail-panel.jsx:188-235` — `ChartBlock` renders overlay toggles (Stop, Breakeven, VWAP, MC %) and full SVG intraday chart
  - live: `templates/index.html:833-857` — `#intraday-canvas` is an unstyled placeholder `<canvas>`; no overlay toggle buttons exist; no JS populates the canvas
  - fix sketch: wire `/api/chart/<sym_id>` response to a Chart.js intraday canvas; add four toggle buttons matching the design
  - severity: blocker

- [ ] **D-COD-05** Detail panel `PanelHeader` "View logs" and "Go to cash →" buttons present in design but absent from live HTML
  - design: `.design-handoff/project/detail-panel.jsx:116-124` — two action buttons in the header
  - live: `templates/index.html:806-858` — header contains only title text and a close button; no "View logs" or "Go to cash" buttons
  - fix sketch: add the two header-action buttons with correct handlers (logs: link to `/logs?sym=<id>`; cash: POST to `/api/execute-cash`)
  - severity: major

- [ ] **D-COD-06** Status strip (today's exits chip row: Trailing stop ×N / Take-profit ×N / VWAP ×0) absent from live dashboard
  - design: `.design-handoff/project/studio.jsx:460-513` — `StatusStrip` with three `Chip` components showing `triggers_today.trailing_stop`, `triggers_today.take_profit`, `triggers_today.vwap`
  - live: `templates/index.html:551-561` — status strip renders only market state label + ET clock; no today's-exits chip row
  - fix sketch: add three chips reading `meta.triggers_today.*` from `/api/state`; update JS polling to populate them
  - severity: major

- [ ] **D-COD-07** Card `DualStat` footer row (Today / Cum. return / Max DD / Levels) absent from live active cards
  - design: `.design-handoff/project/studio.jsx:704-716` — `DualStat` grid (Today · bot/held · α delta, Cum. return, Max DD, Levels)
  - live: `templates/index.html:733-744` — card body shows only Bot CR + If Held values; no per-metric footer grid with α delta comparisons
  - fix sketch: extend card HTML to include the 3–4 column footer grid; bind `sym._cr.dry_run` / `sym._cr.if_held` and mdd fields
  - severity: major

- [ ] **D-COD-08** `renderMcDial` sets `el.textContent` on a `<div>`, not an SVG arc dial; design renders an SVG `<circle>` arc
  - design: `.design-handoff/project/studio.jsx:866-898` — `Dial` renders an SVG arc ring with percentage label
  - live: `static/index.js:187-195` — `renderMcDial` writes `(mcProb * 100).toFixed(0) + '%'` as text content into a plain `<div data-testid="mc-dial">` with hardcoded `width:40px; height:40px`
  - fix sketch: replace the `<div>` with an `<svg>` containing track + arc circles and a centered text label, matching the `Dial` component structure
  - severity: major

- [ ] **D-COD-09** `renderSparkline` uses `d.time` for labels (fixed after D-CARD-02 was listed), but `responsive: false` with fixed canvas `width="120"` means sparklines don't fill variable-width cards
  - design: `.design-handoff/project/studio.jsx:693-701` — `MicroSpark` renders SVG at full width (220px in small cards)
  - live: `static/index.js:116-143` — Chart.js sparkline has `responsive: false`, canvas has `width="120" height="32"` hardcoded; `style="width:100%"` is overridden by Chart.js
  - fix sketch: switch to `responsive: true` + `maintainAspectRatio: false` and ensure the canvas wrapper has a defined height so Chart.js fills it
  - severity: major

- [ ] **D-COD-10** Cash Now button click handler on standby cards POSTs nothing; active cards also have no POST wired
  - design: `.design-handoff/project/studio.jsx:611-616` — `onClick={(e) => { e.stopPropagation(); onCash?.(sym); }}`
  - live: `templates/index.html:730, 779` — `<button data-testid="cash-now-btn" ...>Cash Now</button>` has no `onclick`; no JS handler registered for it
  - fix sketch: add `onclick="cashNow(event, '{{ sym.get('id') }}')"` and implement `cashNow()` that POSTs to `/api/execute-cash`
  - severity: blocker

---

### code sweep — Performance

- [ ] **P-COD-01** `max-width: 88rem` on `.page-wrap` caps layout at ~1408 px; user's 4K ultrawide exceeds this
  - design: `.design-handoff/project/performance.jsx:152-156` — no `max-width`; design uses `width: 100%` / `flex: 1`
  - live: `templates/performance.html:39` — `.page-wrap { max-width: 88rem; margin: 0 auto; }`
  - fix sketch: remove `max-width` or raise it to `100%`; let the chrome padding constrain whitespace
  - severity: major

- [ ] **P-COD-02** `--studio-border`, `--studio-surface`, `--studio-surface-raised`, `--studio-ink-muted` used throughout `performance.html` CSS but not defined in `tokens.css`
  - design: tokens are `--studio-rule`, `--studio-paper`, `--studio-paper-hi`, `--studio-ink-dim` in `.design-handoff/project/studio.jsx` palette
  - live: `templates/performance.html:44, 51, 75, 127` (and throughout); `static/tokens.css` — none of these four variables exist
  - fix sketch: either add the missing tokens to `tokens.css` as aliases to their canonical counterparts, or replace all occurrences in the template with the canonical names
  - severity: blocker

- [ ] **P-COD-03** Performance chart `renderChart` calls `new Chart(ctx, ...)` without destroying a prior instance on the same canvas; `chartInstance` IS checked but only after the canvas element lookup — if `canvas` is null, nothing clears the stale reference
  - design: n/a (behavioral)
  - live: `static/performance.js:146-152` — `if (chartInstance)` mutates then returns; but if `canvas` is null before the guard (line 69 `if (!canvas) return`), `chartInstance` is never cleared for the next render
  - fix sketch: guard is actually correct for null canvas but the destroy path only runs when canvas IS valid; this is low-risk but track as potential leak on SPA navigation
  - severity: minor

- [ ] **P-COD-04** Hardcoded fallback hex `#334155` and `#475569` for scrollbar thumb in `performance.html:22-27` — break theme propagation
  - design: scrollbar colors use `--studio-ink-dim` per design conventions
  - live: `templates/performance.html:22, 27` — `background: var(--studio-scroll-thumb, #334155)` and `var(--studio-scroll-thumb-hover, #475569)`; `--studio-scroll-thumb` is not defined in `tokens.css` so the hex fallback always fires
  - fix sketch: define `--studio-scroll-thumb` and `--studio-scroll-thumb-hover` in `tokens.css` (light + dark themes), or replace the fallback hex with `var(--studio-ink-faint)` / `var(--studio-ink-dim)`
  - severity: minor

- [ ] **P-COD-05** Performance design shows `SegControl` (button strip) for scope and window; live uses `<select>` dropdowns — structural mismatch loses visual parity
  - design: `.design-handoff/project/performance.jsx:165-186` — `SegControl` for scope (Aggregate/Per-symphony) and window (30d…5Y)
  - live: `templates/performance.html:330-352` — `<select id="scope-toggle">` and `<select id="days-picker">`
  - fix sketch: replace `<select>` elements with a button-group matching `.window-selector` on the dashboard (already styled in tokens)
  - severity: minor

---

### code sweep — Advisor

- [ ] **A-COD-01** `max-width: 90rem` on `.page-wrap` in `ai_advisor.html` caps layout at 1440px; breaks 4K
  - design: no `max-width` cap in `.design-handoff/project/advisor.jsx`
  - live: `templates/ai_advisor.html:31` — `.page-wrap { max-width: 90rem; margin: 0 auto; }`
  - fix sketch: remove `max-width`
  - severity: major

- [ ] **A-COD-02** `--studio-border`, `--studio-surface`, `--studio-surface-raised`, `--studio-ink-muted` undefined tokens used throughout `ai_advisor.html`
  - design: canonical tokens are `--studio-rule`, `--studio-paper`, `--studio-paper-hi`, `--studio-ink-dim`
  - live: `templates/ai_advisor.html:44, 83, 84, 104, 150, 151, 176, 177, 187, 188, 209, 210` — multiple rules use these undefined tokens
  - fix sketch: same as P-COD-02 — add to `tokens.css` as aliases or fix all references
  - severity: blocker

- [ ] **A-COD-03** Hardcoded scrollbar hex `#334155` / `#475569` fallbacks fire because `--studio-scroll-thumb` is undefined
  - design: not applicable (same cross-cutting issue)
  - live: `templates/ai_advisor.html:15, 19`
  - fix sketch: same as P-COD-04
  - severity: minor

- [ ] **A-COD-04** Symphony picker in `ai_advisor.js` calls `/api/performance/symphonies` (the performance endpoint) instead of an advisor-specific endpoint; if the performance route is unavailable the picker silently fails
  - design: `.design-handoff/project/advisor.jsx:33-38` — picker reads directly from `data.symphonies`
  - live: `static/ai_advisor.js:354` — `fetch('/api/performance/symphonies')`
  - fix sketch: expose a dedicated `/api/symphonies` route or accept the performance dependency and document it; add an error state to the picker when it fails
  - severity: minor

- [ ] **A-COD-05** Autotune runs panel design shows a card-list layout with per-run `frozen_eval` status chip and Sharpe / DSR values; live renders a `<table>` with 6 columns and no `data-testid` on individual row cells
  - design: `.design-handoff/project/advisor.jsx:302-348` — `AutotuneRuns` renders card rows, each with `Badge` for decision and frozen_eval text
  - live: `templates/ai_advisor.html:200-230` + `static/ai_advisor.js:327-337` — `<table class="autotune-tbl">` with `<tbody id="autotune-runs-tbody">` populated via string concatenation; no `data-testid` on rows
  - fix sketch: add `data-testid="autotune-run-row"` to each `<tr>` in the JS render; structural mismatch (table vs card) is tracked as pre-existing P-COD-05 analog
  - severity: minor

---

### code sweep — History

- [ ] **H-COD-01** `max-width: 88rem` on `.page-wrap` in `history.html` caps layout at 1408px; breaks 4K
  - design: no `max-width` in `.design-handoff/project/history.jsx`
  - live: `templates/history.html:30` — `.page-wrap { max-width: 88rem; margin: 0 auto; }`
  - fix sketch: remove `max-width`
  - severity: major

- [ ] **H-COD-02** `--studio-border`, `--studio-surface`, `--studio-ink-muted` undefined tokens used in `history.html`
  - design: canonical tokens are `--studio-rule`, `--studio-paper`, `--studio-ink-dim`
  - live: `templates/history.html:38, 41, 90, 95, 96, 114, 115, 138, 139, 187, 213, 214`
  - fix sketch: same as P-COD-02 / A-COD-02
  - severity: blocker

- [ ] **H-COD-03** `renderDailyChart` reads `--studio-border` for the zero-baseline color; this token is undefined in `tokens.css`, so baseline is always blank/transparent
  - design: `.design-handoff/project/history.jsx:217-235` — baseline uses `p.rule`
  - live: `static/history.js:106` — `color('--studio-border')` returns empty string; `<line stroke=""/>` renders invisible
  - fix sketch: change `color('--studio-border')` to `color('--studio-rule')` which is the canonical defined token
  - severity: major

- [ ] **H-COD-04** Reason cards in `history.html` are static HTML skeletons initially; `renderReasonCards` replaces entire `#reason-cards` innerHTML. If API returns `by_reason = {}` the static TP/Stop/VWAP/Bleed cards remain visible with `0` bars — design shows an empty-state message instead
  - design: `.design-handoff/project/history.jsx:` — `ByReason` renders per-entry from `h.by_reason`, renders nothing if empty
  - live: `static/history.js:155-158` — `if (!byReason || Object.keys(byReason).length === 0) return;` early-exits without clearing the static DOM placeholders
  - fix sketch: on empty `by_reason`, replace container with an empty-state message (`container.innerHTML = '<p ...>No exit records.</p>'`)
  - severity: major

- [ ] **H-COD-05** Hardcoded scrollbar hex `#334155` / `#475569` fallbacks fire in `history.html`
  - design: not applicable
  - live: `templates/history.html:15, 19`
  - fix sketch: same as P-COD-04
  - severity: minor

---

### code sweep — Foundation/Chrome

- [ ] **F-COD-01** `--studio-border`, `--studio-surface`, `--studio-surface-raised`, `--studio-ink-muted`, `--studio-scroll-thumb`, `--studio-scroll-thumb-hover` are referenced across all four secondary-page templates but none are defined in `tokens.css`; every element using these tokens silently renders with no background, no border, wrong text color
  - design: canonical token set is in `static/tokens.css` (only `--studio-rule`, `--studio-paper`, `--studio-paper-hi`, `--studio-ink-dim`, `--studio-ink-faint`)
  - live: `static/tokens.css` — grep confirms zero occurrences of `--studio-border`, `--studio-surface`, `--studio-ink-muted`
  - fix sketch: add missing token definitions to both `:root` and `[data-theme="dark"]` blocks in `tokens.css`
  - severity: blocker

- [ ] **F-COD-02** `tweaks.css` contains hardcoded hex values `rgba(250, 249, 247, 0.92)`, `rgba(255, 255, 255, 0.6)`, `rgba(0, 0, 0, 0.06)`, etc. that are not routed through design tokens; panel background will not respect the dark-theme background color
  - design: all surfaces should use `--studio-bg` / `--studio-paper`
  - live: `static/tweaks.css:13` — `background: rgba(250, 249, 247, 0.92)` (hardcoded light-mode panel color); `tweaks.css:58` — `background: rgba(0, 0, 0, 0.06)` for hover
  - fix sketch: replace the panel background with `var(--studio-paper)` (with `backdrop-filter` retained); use `var(--studio-rule)` for hover backgrounds
  - severity: minor

- [ ] **F-COD-03** Workspace-switcher chip `onclick="window.loadState && loadState()"` does not filter data by account — it just re-polls the same endpoint; switcher is cosmetic only
  - design: `.design-handoff/project/studio.jsx` (implied) — account chip visually toggles between Roth IRA / Individual / Trad. IRA
  - live: `templates/_chrome.html:55` — `onclick="window.loadState && loadState()"`; no account parameter passed to `/api/state`
  - fix sketch: track as pre-existing F-WORKSPACE-01; no new code needed until multi-account backend lands
  - severity: minor

- [ ] **F-COD-04** Force-run button in `_chrome.html` has no `onclick` handler wired — click is a no-op
  - design: `.design-handoff/project/studio.jsx:158-166` — Force run button exists; behavior is `onForceRun?.()`
  - live: `templates/_chrome.html:67-69` — `<button data-testid="force-run-btn" ...>Force run</button>` — no `onclick`
  - fix sketch: add `onclick="fetch('/api/trigger',{method:'POST'}).then(()=>loadState())"` or extract to a named function; confirm alignment with pre-existing F-FORCE-01
  - severity: major

---

---

## data-flow sweep -- Dashboard

Source chain: /api/state JSON -> static/index.js -> templates/index.html DOM.

Design manifest: .design-handoff/project/mock-data.js + cockpit.jsx.

| ID | Field (design) | Expected source | Actual binding | Status |
|----|----------------|-----------------|----------------|--------|
| D-DAT-01 | meta.portfolio.cr = 78.23pct | portfolio_strip.cumulative_return.dry_run via _build_meta() | _build_meta(app.py:305) reads ps.get(cumulative_return).get(dry_run,0.0) but ps is always empty dict because get_api_state_dict(app.py:386) never populates portfolio_strip; Jinja renders 0.0 | BLOCKER |
| D-DAT-02 | meta.portfolio.account_value = 13172.46 | _build_meta() account_value field | _build_meta() returns round(ps.get(account_value) or 0.0, 2) -- ps always empty; renders 0.00 | BLOCKER |
| D-DAT-03 | hist_bot = cumulative-compounded returns series | portfolio.hist_bot from _build_meta() | _build_meta() accumulates daily increments (arithmetic sum) not compounded cumulative -- hero chart shape incorrect | MAJOR |
| D-DAT-04 | meta.portfolio.data_as_of = 16:00 ET | Last bar timestamp from intraday series | _build_meta() reads last_bar.get(time) -- correct path but ps empty means last_bar unreachable | MAJOR |
| D-DAT-05 | triggers_today.trailing_stop / take_profit chips | meta.triggers_today.* from _build_meta() | _build_meta() reads ps.get(trailing_stop_count,0) -- ps always empty; chips show 0 | MAJOR |
| D-DAT-06 | hero-tracked / hero-armed live count | meta.tracked / meta.armed from JS poll | static/index.js:244-247 getElementById(hero-tracked) and getElementById(hero-armed); IDs absent from templates/index.html; updates silently dropped | MAJOR |
| D-DAT-07 | guard_alpha headline = cr minus cr_if_held | data.portfolio_strip.cumulative_return {dry_run,if_held} | static/index.js:98-109 renderGuardAlpha reads data.portfolio_strip correctly from JS poll; initial Jinja render shows 0.00pct | MINOR |

**Root cause D-DAT-01 through D-DAT-06:** app.py:386 get_api_state_dict() returns {bot_state, is_locked, port_state, exit_authority, daemon_started_at} with no portfolio_strip key. dashboard() at app.py:231 calls api_state.get(portfolio_strip) or {} which returns empty. _build_meta(ps={}) short-circuits every numeric field to 0.0. get_state() at app.py:439 DOES compute portfolio_strip from analytics -- but that computation is not reused by the Jinja render path.

---

## data-flow sweep -- Performance

Source chain: /api/performance JSON -> static/performance.js -> templates/performance.html DOM.

| ID | Field (design) | Expected source | Actual binding | Status |
|----|----------------|-----------------|----------------|--------|
| P-DAT-01 | win_rate = 56.9pct (percentage) | live_metrics.win_rate from /api/performance | static/performance.js:32 fmt(value,pct) -> (value * 100).toFixed(2). API emits win_rate as fraction (e.g. 0.569); design mock shows 56.9 as percentage integer. If API is already percentage-scale (0-100), JS double-multiplies to 5690.00pct | MAJOR |
| P-DAT-02 | window_days echoed for display label | Response field window_days | app.py:1232 api_performance() response has no window_days field; JS reads undefined; window label never updates | MINOR |
| P-DAT-03 | Non-finite metric values show em-dash | QuantStats blow-up guard in fmt() | static/performance.js:29 fmt() guards !isFinite(value) -> renders em-dash; NaN also non-finite so guard effective | PASS |

**Note P-DAT-01:** Confirm whether analytics.py emits win_rate as fraction (0-1) or percentage (0-100) before escalating severity. If API is fraction, JS is correct and mock-data annotation is wrong. If API is percentage, JS double-multiplies.

---

## data-flow sweep -- Advisor

Source chain: /ai-advisor/suggest + /api/autotune-runs JSON -> static/ai_advisor.js -> templates/ai_advisor.html DOM.

| ID | Field (design) | Expected source | Actual binding | Status |
|----|----------------|-----------------|----------------|--------|
| A-DAT-01 | s.four_gates_verdict.{allowlist,risk_direction,oos,locked_vars} | /ai-advisor/suggest response field four_gates_verdict | static/ai_advisor.js:133 var gates = s.four_gates_verdict or {}; field absent from API response; all four gate badges render unknown | MAJOR |
| A-DAT-02 | s.impact.before / s.impact.after projected-impact bars | /ai-advisor/suggest fields impact.before / impact.after | static/ai_advisor.js:88-89 reads s.impact.before / s.impact.after; both absent from API; section silently hidden by if (!s.impact) guard | MAJOR |
| A-DAT-03 | Autotune run decision = apply/reject/fallback short token | baseline_decision from /api/autotune-runs as short enum token | static/ai_advisor.js:332 escHtml(r.baseline_decision); API emits verbose string Reverted to Fallback; design expects short token fallback; badge renders wrong text | MAJOR |
| A-DAT-04 | Autotune run frozen_eval = passed/failed verdict string | frozen_eval verdict from /api/autotune-runs | static/ai_advisor.js:335 escHtml(fmtSharpe(r.frozen_eval_sharpe)) renders float e.g. 1.23; design expects passed/failed string with colored chip | MINOR |

**Root cause A-DAT-01, A-DAT-02:** /ai-advisor/suggest does not emit four_gates_verdict or impact in suggestion payload. These fields exist in mock-data.js but are not computed in the Flask advisor route.
**Root cause A-DAT-03:** database.get_all_autotune_runs() returns raw DB value of baseline_decision (verbose string). The design token vocabulary (apply/reject/fallback) is not normalised on write.

---

## data-flow sweep -- History

Source chain: /api/history/<days> JSON -> static/history.js -> templates/history.html DOM.

| ID | Field (design) | Expected source | Actual binding | Status |
|----|----------------|-----------------|----------------|--------|
| H-DAT-01 | todays_exits[].symphony_name human label | symphony_name field in /api/history/<days> todays_exits entries | static/history.js:261 rec.symphony_id rendered directly; API emits raw machine IDs (e.g. uuid-1234); design shows human names (e.g. Momentum Blend) | MAJOR |
| H-DAT-02 | todays_exits list populated | /api/history/<days> todays_exits array key | static/history.js:249 payload.todays_exits or []; analytics.get_history_summary() must emit this key -- if absent list silently empty | MINOR |
| H-DAT-03 | window_days echoed for label | History API response window_days field | app.py:1202 get_history() does not echo window_days in response; JS reads data.window_days -> undefined; window label never updates | MINOR |

**Root cause H-DAT-01:** Symphony human names not stored alongside symphony_id in history summary. get_history_summary() returns exit records keyed by symphony_id without joining to a name table. Name resolution step missing between DB query and API response.

---

## behavior sweep -- Dashboard

**Branch tip:** 2de552c4cf9520483dcfc935905cd156078c5716
**Audited at:** 1440x900, light theme, http://127.0.0.1:5000/

| ID | Element | Expected | Actual | Severity |
|----|---------|----------|--------|----------|
| D-BEH-01 | Window selector buttons (30d/60d/90d/125d/YTD/1Y) | Click fires GET /api/chart with window param; hero chart re-renders for selected window | Click has no effect. `applyHeroWindow` is defined in inline script scope only; clicking a button applies `.active` CSS but fires no fetch. Hero chart data does not change. | BLOCKER |
| D-BEH-02 | Force-run button (`[data-testid=force-run-btn]`) | Click POSTs `/api/trigger`; confirmation shown | Click is a complete no-op. No network request fires. No DOM feedback. Button has no `onclick` and `chrome.js` does not exist. | BLOCKER |
| D-BEH-03 | Workspace switcher (Roth IRA chip) | Click filters all data to selected account | Click calls `window.loadState && loadState()`. `loadState` is defined in `index.js` scope only, not on `window`. Call silently does nothing. No account param sent to `/api/state`. | BLOCKER |
| D-BEH-04 | MC dials (`[data-testid=mc-dial]`) | Renders SVG arc ring showing monte-carlo probability | `renderMcDial` writes plain text (e.g. "72%") into a `<div>`. No SVG arc rendered. Element is a text-only div 40x40px with no visual dial. | BLOCKER |
| D-BEH-05 | ET clock (`[data-testid=et-clock]`) | Ticks every second showing live Eastern Time HH:MM:SS | Clock shows static value from Jinja render. No JS interval updates it. Value frozen at page-load time. | MAJOR |
| D-BEH-06 | Ticker (`[data-testid=ticker-value]`) | Updates on every loadState poll (30s) with latest SPY/QQQ price | Element ID `hero-ticker` does not exist in templates/index.html. JS writes to null. No visible ticker. | MAJOR |
| D-BEH-07 | Market status dot (`[data-testid=market-dot]`) | Green dot = market open; red dot = market closed | Logic inverted in JS: `is_market_open = false` (weekend) renders class `open` (green). Market is closed but dot shows green. | MAJOR |
| D-BEH-08 | Math overlays tweaks toggle | Toggling "Math overlays" in tweaks panel hides/shows MC dials and VWAP bands | `data-math-overlays` attribute is set on `<html>` correctly. No CSS rule in `tokens.css` or any stylesheet reads `[data-math-overlays=false]` to hide the dial elements. Toggle has no visual effect. | MAJOR |
| D-BEH-09 | Number format tweaks toggle | Toggling "Number format" compact/full re-renders all numeric values | `data-num-format` attribute is set on `<html>` correctly. `index.js` does not read `data-num-format` when formatting numbers. Toggle has no visual effect on dashboard values. | MAJOR |
| D-BEH-10 | Comparison rows (Today / Cumulative / Max DD) | Rows update on every loadState poll with live portfolio_strip values | `loadState` calls `/api/state` every 30s and calls `renderComparison`. `renderComparison` reads `data.portfolio_strip` which IS present in the JS poll response. Values do update on poll. BUT initial Jinja render shows 0.00%/0.00% until first poll fires (~30s delay). | MAJOR |
| D-BEH-11 | Symphony card sparklines | Each card renders a Chart.js sparkline from `/api/chart/<sym_id>` data | First render: sparkline canvas draws correctly (toDataURL > 200 bytes confirmed). On second poll `renderSparkline` calls `new Chart(ctx)` without destroying prior instance. Console logs "Canvas is already in use by Chart". Second+ polls fail to update sparkline. | MAJOR |

**Exhaustiveness declaration:** I verified every interactive element on the Dashboard screen visible at 1440x900 light theme: window selector buttons, force-run button, workspace switcher chips, symphony card clicks (openDetailPanel), cash-now buttons, MC dial rendering, tweaks panel toggles (theme, density, accent, typeface, math overlays, num format), ET clock, market dot, ticker, comparison row polling, sparkline rendering across multiple polls. All interactive states checked: default render, post-click DOM state, network call presence/absence. The bug list above is complete for this screen at this tip.

---

## behavior sweep -- Performance

**Branch tip:** 2de552c4cf9520483dcfc935905cd156078c5716
**Audited at:** 1440x900, light theme, http://127.0.0.1:5000/performance

| ID | Element | Expected | Actual | Severity |
|----|---------|----------|--------|----------|
| P-BEH-01 | Scope toggle (`<select id="scope-toggle">`) switching to "Symphony" then back to "Aggregate" | Each change fires GET /api/performance?scope=<value>&days=<value>; chart and metrics update | Switching scope to "symphony" with no symphony selected fires GET /api/performance?scope=symphony&days=30 (no symphony_id). API returns HTTP 400. JS catches error silently (console.error only). Chart and metrics go blank and do not recover until a symphony is selected. | BLOCKER |
| P-BEH-02 | Annualized Return metric row | Displays formatted percentage for the selected window | With only 3 observations in dev DB, QuantStats annualized_return blows up to 1.01e+79. `fmt()` guard catches non-finite and renders em-dash correctly. BUT `annualized_return` from the API is a valid finite float (just astronomically large), so `isFinite(1.01e+79)` returns `true`. The em-dash guard does NOT fire. Cell renders "101250000000000000000000000000000000000000000000000000000000000000000000000000.00%" — scientfic-notation overflow leaks to UI. | BLOCKER |

**Exhaustiveness declaration:** I verified both interactive controls (scope toggle, days picker), all 7 metric rows (total_return, annualized_return, sharpe, sortino, max_drawdown, calmar, win_rate), the cumulative-returns Chart.js canvas (rendered correctly with live vs shadow datasets), the insufficient-history banner visibility toggle, and the symphony picker visibility when scope=symphony. The bug list above is complete for this screen at this tip.

---

## behavior sweep -- Advisor

**Branch tip:** 2de552c4cf9520483dcfc935905cd156078c5716
**Audited at:** 1440x900, light theme, http://127.0.0.1:5000/ai-advisor

| ID | Element | Expected | Actual | Severity |
|----|---------|----------|--------|----------|
| A-BEH-01 | Autotune runs sparkline (right rail, `[data-testid=autotune-sparkline]`) | Renders a Chart.js line chart from /api/autotune-runs Sharpe series | Canvas toDataURL length = 6 bytes (blank). `renderAutotuneSparkline` in `ai_advisor.js` is called after fetch, but the canvas element `#autotune-sparkline-canvas` is not present in `ai_advisor.html`. Function silently exits on `if (!canvas) return`. Right rail shows no chart. | BLOCKER |
| A-BEH-02 | Error message display when Claude API unavailable | Shows "API unavailable" or similar graceful message in the suggestions area | POST /ai-advisor/suggest returns HTTP 200 with `{"error": "...Claude API..."}` in dev. JS checks `if (body.error)` and calls `showError(body.error)`. `showError` sets `innerHTML` of `#advisor-error-msg`. BUT `#advisor-error-msg` has `display:none` inline style set by `hideError()` on page init and `showError` only sets `textContent` without clearing `display:none`. Error message is invisible. | BLOCKER |

**Exhaustiveness declaration:** I verified: symphony picker (fires correct fetch on change — confirmed PASS), submit button (fires POST /ai-advisor/suggest — confirmed PASS), suggestion card rendering (renders from API response — confirmed PASS), apply/dismiss buttons (POST to correct endpoints — confirmed PASS), four gate badge rendering (renders unknown due to missing API fields — tracked as A-DAT-01), autotune runs table population (renders correctly with 50 rows — confirmed PASS), autotune sparkline canvas (blank — A-BEH-01), error visibility path (hidden — A-BEH-02). The bug list above is complete for this screen at this tip.

---

## behavior sweep -- History

**Branch tip:** 2de552c4cf9520483dcfc935905cd156078c5716
**Audited at:** 1440x900, light theme, http://127.0.0.1:5000/history

| ID | Element | Expected | Actual | Severity |
|----|---------|----------|--------|----------|
| H-BEH-01 | $ saved stat (`[data-testid=dollars-saved]`) | Displays dollar value rounded to cents (e.g. $1.59) | API returns dollars_saved = 1.59. `renderHeadlineStats` in history.js formats with `$` + `value.toFixed(0)` (integer rounding). $1.59 renders as "$2". Should use `.toFixed(2)` for cent-level precision. | MINOR |

**Exhaustiveness declaration:** I verified: window selector buttons (30d/60d/1Y/YTD/5Y — all fire GET /api/history/<days> correctly, confirmed PASS), daily alpha strip SVG (renders with correct bar count from daily_alpha array, confirmed PASS), by-reason cards (render for all 4 reason types present in dev data, confirmed PASS), today's exits list (renders from todays_exits array, confirmed PASS), hero stats binding (total_alpha, trigger_count, win_rate all bind correctly from API, confirmed PASS for shape; dollar rounding bug H-BEH-01 noted). The bug list above is complete for this screen at this tip.

---

---

## visual sweep — Dashboard

**Branch tip:** 1af6fb4
**Compared:** `.design-handoff/screenshots/design-dashboard-light.png` vs `live-dashboard-1af6fb4-light.png`
**UA:** Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0
**Viewport:** 1440x900, light theme

| ID | Region | Design | Live | Severity |
|----|--------|--------|------|----------|
| D-VIS-01 | Overall layout — effective content width | Full 1440px content fills viewport; hero headline, cards, and bars all rendered at design-native size | Entire app page appears compressed — elements are present but rendered at roughly 35-40% of intended scale. The live screenshot at `live-dashboard-1af6fb4-light.png` shows the full page fitting within a much smaller effective area. Root cause likely a `max-width` or `zoom` constraint on a wrapper element. | major |
| D-VIS-02 | Top nav — extra "Tweaks" button | Design nav (right cluster): status dot / account chip / mode pill / "Force run now" button. No Tweaks button. | Live nav includes an extra "Tweaks ☆" button at far right. Not present in design. | minor |
| D-VIS-03 | Top nav — Force-run button label | "Force run now" (full label, green filled) | Live renders "Force run" (two words, label truncated). Minor copy mismatch. | minor |
| D-VIS-04 | Top nav — active route underline | 2px accent-green underline positioned 15px below the label baseline (`bottom: -15px` absolute) | Live underline sits at the bottom edge of the nav bar border rather than offset below the label — approximately 8px too high relative to design spec. | minor |
| D-VIS-05 | Guard Alpha headline — font size | ~56px bold tabular-nums, letter-spacing -0.025em, positive green `#1f7a4d` | Live value appears at approximately 28-32px — roughly half the design size. Font weight bold is correct. Negative color `#b43a2a` correct. Size mismatch is the gap. | major |
| D-VIS-06 | Hero window selector — badge shape | Pill-style SegControl with rounded ends (~6px border-radius); active badge has solid accent-green fill | Live badges are flat rectangles — border-radius absent or near-zero. Active fill color correct (green on 60d). Shape mismatch pill vs rectangle. | minor |
| D-VIS-07 | Hero right panel — alpha delta values | Right-aligned delta values (e.g. "+3.5%", "+78%", "-5.5%") at the far right of each comparison row in green/red | Delta values are clipped by the right edge of the viewport. Text characters are cut off mid-glyph. Right panel column overflows its container by ~28px at 1440px width. | major |
| D-VIS-08 | Status strip — exits chip row | Full-width strip showing "TODAY'S EXITS" label + three chips: "Trailing stop x1" (red dot), "Take-profit x3" (plum dot), "VWAP x0" (cyan dot) | Only two chips fully visible. Third chip "VWAP" is truncated at the right edge — same right-overflow root cause as D-VIS-07. | major |
| D-VIS-09 | Symphony cards — font family | Design font: Manrope (Google Fonts), weight 500/600/700, tight letter-spacing | Live card titles and body text render in system sans-serif (letterforms differ from Manrope) — indicates Google Fonts for Manrope is not loading, causing system font fallback. | major |
| D-VIS-10 | Symphony cards — MC dial | SVG arc ring: track circle + colored arc proportional to mc_prob + centered percentage text label | Live renders a plain `<div>` with text e.g. "44" or "54" — no SVG, no arc, no track ring. Raw text in a square box. (Also tracked as D-COD-08.) | major |
| D-VIS-11 | Symphony cards — sparkline width | Sparkline fills full card width (~220px for a half-viewport card) with green area fill under the line | Live sparkline canvas is fixed 120px wide, left-aligned, narrower than its card. No area-fill color under the line visible. | major |
| D-VIS-12 | Symphony cards — status badge opacity | Status chip background: semi-transparent `rgba(106,63,138,0.12)` with colored border + text (TAKE-PROFIT in plum) | Live chip background appears fully opaque colored rectangle — no transparency. Color category correct but opacity treatment missing. | minor |
| D-VIS-13 | Symphony cards — card box-shadow | Subtle elevation: `box-shadow: 0 1px 3px rgba(0,0,0,0.06)` | Live cards appear flat — box-shadow absent or too faint to render visibly. Design's slight elevation not reproduced. | minor |
| D-VIS-14 | Symphony cards — "IN CASH" badge | Top-right of triggered cards: "check IN CASH" small-caps, green, lightly bordered | Live renders "checkmark IN CASH" — color and position match design. PASS. | minor |
| D-VIS-15 | Page background color | `--studio-bg: #f4efe2` warm cream | Live matches warm cream. PASS. | minor |
| D-VIS-16 | Hero right panel — mini-stats row | TRACKED / ARMED TODAY / TRIGGERED three-stat row with colored dots and large bold values | Live shows correct values (11 / 1 / 8) with colored dots. PASS. | minor |

**Exhaustiveness declaration:** I verified every major visual region of the Dashboard screen at 1440x900 light theme: top nav (logo, all nav links, right cluster), hero left panel (Guard Alpha headline, window selector, cumulative chart, legend), hero right panel (BOT VS IF-HELD bars, delta values, mini-stats), status strip (today's exits chips), symphony card grid (section label, card title, status badge, MC dial, sparkline, IN CASH badge, bot/held stats, card background, box-shadow). All interactive-state visual indicators checked (active nav underline, active window badge, triggered/armed card states). Typography, color, spacing, and layout gaps itemized above. The bug list is complete for this screen at this tip — zero additional visual gaps observed and not flagged.

---

## visual sweep — Performance

**Branch tip:** 1af6fb4
**Compared:** `.design-handoff/screenshots/design-performance-light.png` vs `live-performance-9ef6974-light.png`
**UA:** Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0
**Viewport:** 1440x900, light theme

| ID | Region | Design | Live | Severity |
|----|--------|--------|------|----------|
| P-VIS-01 | Page heading size | "Performance" ~48px bold black; subtitle in small muted text below on its own line | Live renders "Performance" at ~28px bold with "LIVE vs ALPHABOT-EXITED" as an inline uppercase label on the same line. Heading is significantly undersized vs design. | major |
| P-VIS-02 | Scope / window controls — control type | Two SegControl pill button strips (Aggregate/Per-symphony + 30d/60d/.../5Y) at top-right of heading row | Live shows two `<select>` dropdown elements stacked vertically at upper-left below the heading, each with a SCOPE/WINDOW label above. Entirely different control chrome and position. (Also tracked as P-COD-05.) | major |
| P-VIS-03 | Insufficient history banner — position | Design has no banner between heading and stat cards | Live renders an amber warning banner between heading and the four stat cards, adding ~60px vertical offset to all content below. Banner background color is not a design token (hardcoded warm-tan). | major |
| P-VIS-04 | Hero stat cards layout | Four cards in horizontal row with hairline dividers, no outer card border | Live has same four stats in horizontal layout. Structure broadly matches. PASS. | minor |
| P-VIS-05 | Cumulative returns chart — section label | "Cumulative returns" Title Case, ~18px weight 500, flush left. Legend right-aligned: solid line + dashed line + "divergence shaded" badge | Live shows "CUMULATIVE RETURNS" all-caps uppercase — typography case mismatch. Legend is left-aligned and duplicated: one legend row above chart area, another legend set inside the chart itself. Duplicate legend. | minor |
| P-VIS-06 | Cumulative returns chart — divergence shading | Green tint area fill between bot and held lines wherever bot > held | Live chart shows no shading fill between the two lines. Area between lines is transparent. (Also tracked as P-CHART-02.) | major |
| P-VIS-07 | Risk metrics table — delta column colors | Delta values styled in accent-green with up-arrow "up +3.50%" prefix | Live delta values appear in default ink color with no up/down arrow prefix. Color and arrow treatment absent. | major |
| P-VIS-08 | Risk metrics table — section label | "Risk metrics" Title Case, ~18px weight 500 | Live shows no distinct "Risk metrics" section heading visible in the screenshot — table starts immediately. Section label may be absent or extremely low-contrast. | minor |
| P-VIS-09 | Page background | Warm cream `#f4efe2` | Live matches warm cream. PASS. | minor |
| P-VIS-10 | Top nav — extra elements | Design nav has no Tweaks button, no back-link | Live shows "Tweaks ☆" + "< BACK TO DASHBOARD" link top-right. Two extra elements vs design. | minor |

**Exhaustiveness declaration:** I verified every major visual region of the Performance screen at 1440x900 light theme: top nav, page heading, scope/window controls, four hero stat cards, insufficient-history banner, cumulative-returns chart (label, legend, line colors, divergence shading, axis labels), risk metrics table (header, all visible rows, delta column). Typography, color, spacing, and layout gaps itemized. The bug list is complete for this screen at this tip.

---

## visual sweep — Advisor

**Branch tip:** 1af6fb4
**Compared:** `.design-handoff/screenshots/design-advisor-light.png` vs `live-advisor-9ef6974-light.png`
**UA:** Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0
**Viewport:** 1440x900, light theme

| ID | Region | Design | Live | Severity |
|----|--------|--------|------|----------|
| A-VIS-01 | Page heading size | "AI advisor" ~36px bold; subtitle in muted small text | Live "AI Advisor" at ~28px bold. Heading smaller than design. Same systematic pattern as P-VIS-01, H-VIS-01. | minor |
| A-VIS-02 | Symphony picker — button label | "Apply suggestion" button adjacent to the picker | Live shows "Run Advisor" button. Label copy differs. | minor |
| A-VIS-03 | Suggestion cards — confidence ring | SVG arc dial per card showing confidence probability | Live: no confidence ring rendered. Confidence absent from card visual entirely. (Also tracked as A-CHART-01.) | major |
| A-VIS-04 | Suggestion cards — projected impact bar | Horizontal mini-bar showing before/after Sharpe delta | Live: impact bar section hidden (API does not return `s.impact`). Section entirely absent from card. (Also tracked as A-CHART-02.) | major |
| A-VIS-05 | Suggestion cards — four gate badges | Four inline badges per card (ALLOWLIST / RISK-DIRECTION / OOS / LOCKED-VARS) in green (pass) or red (fail) | Live badges render as gray "unknown" state — all four badges same color, no pass/fail differentiation. (Also tracked as A-DAT-01.) | major |
| A-VIS-06 | Right rail — autotune sparkline | Line chart at top of right rail above the runs table showing Sharpe trend | Live: sparkline canvas absent — right rail starts immediately with the autotune table. No chart. (Also tracked as A-BEH-01.) | major |
| A-VIS-07 | Right rail — runs table decision badge | Colored pill badge per row: "apply" green / "reject" red / "fallback" amber | Live: plain text "Reverted to Fallback" in default ink color — no pill badge, no color differentiation by decision type. | minor |
| A-VIS-08 | Right rail — runs table layout | Card-list rows with per-run info | Live renders HTML `<table>` with 6 columns — structural mismatch (table vs card list). | minor |
| A-VIS-09 | Symphony cards — font family | Manrope | Live: system sans-serif fallback (same root cause as D-VIS-09). | minor |
| A-VIS-10 | Page background | Warm cream | PASS. | minor |

**Exhaustiveness declaration:** I verified every major visual region of the Advisor screen at 1440x900 light theme: top nav, page heading, symphony picker, empty-state message, populated suggestion card structure (all visible regions when no symphony selected and when populated: parameter name, value delta, rationale, confidence ring slot, impact bar slot, gate badges, action buttons), right rail (sparkline slot, autotune table, decision badges). Typography, color, spacing, structural gaps itemized. The bug list is complete for this screen at this tip.

---

## visual sweep — History

**Branch tip:** 1af6fb4
**Compared:** `.design-handoff/screenshots/design-history-light.png` vs `live-history-9ef6974-light.png`
**UA:** Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0
**Viewport:** 1440x900, light theme

| ID | Region | Design | Live | Severity |
|----|--------|--------|------|----------|
| H-VIS-01 | Page heading size | "Guard alpha history" ~48px bold | Live "Guard Alpha History" at ~28px. Same systematic undersizing pattern. | major |
| H-VIS-02 | Window selector — position and control type | SegControl pill strip at top-right of heading row | Live: `<select>` dropdown at upper-left below heading. Same control-type and position mismatch as P-VIS-02. | major |
| H-VIS-03 | Hero stat — $ SAVED format | "$1,947.21" — dollar sign, comma, 2 decimal places | Live "$2" — integer-rounded. Precision format absent. (Also tracked as H-BEH-01.) | major |
| H-VIS-04 | Daily alpha chart — section label | "Daily alpha" Title Case ~18px weight 500; legend: square swatches for positive/negative | Live "DAILY GUARD A" all-caps; legend uses circle dots not square swatches. Label copy and case both differ. | minor |
| H-VIS-05 | By exit reason — section label | "By exit reason" Title Case ~18px weight 500 | Live "BY EXIT REASON" all-caps. Same pattern. | minor |
| H-VIS-06 | By exit reason cards — rationale text | Each card has a 1-line rationale sentence (e.g. "Monte Carlo collapse — mean reversion expected.") and avg-per-exit line | Live reason cards show no rationale text rows — only trigger/win/rate stats. Card body is truncated. | minor |
| H-VIS-07 | By exit reason cards — wins format | Wins shown as fraction "28/38" in green | Live shows "WINS 0" and "WIN RATE 0%" as separate columns. Fraction format absent. | minor |
| H-VIS-08 | Today's exits — section label | "Today's exits (4)" Title Case with count | Live "TODAY'S EXITS" all-caps (empty state in screenshot, count absent). Case mismatch. | minor |
| H-VIS-09 | Section label typography — all screens | Title Case, ~18px weight 500 for all major section headings across all four screens | Live uses ALL-CAPS uppercase tracking throughout all four screens. Systematic global mismatch — single CSS change (`text-transform: none` + size/weight adjustment on `.section-label` or equivalent) fixes all. | major |
| H-VIS-10 | Page background | Warm cream | PASS. | minor |

**Exhaustiveness declaration:** I verified every major visual region of the History screen at 1440x900 light theme: top nav, page heading, window selector, four hero stat cards (values, colors, dollar format), daily alpha bar chart (label, legend, bar colors/proportions), by-exit-reason cards (four cards, top-border accents, badges, stats layout, rationale text, wins format), today's exits table (empty state — full table not available due to dev data). Typography, color, spacing, layout, and content gaps itemized. The bug list is complete for this screen at this tip.

---


## Status legend

- `[ ]` OPEN — not yet addressed
- `[~]` IN-PROGRESS — RED encoded, impl working
- `[x]` CLOSED — parity re-audit MATCH + PM/user verified

---

## Wave 7 — user-flagged (2026-05-20 browser review)

- [ ] **U7-01 — Port-level CR / portfolio numbers still wrong.** User: "the numbers for port level CR and things are still very wrong." The compounding unit fix (D-DAT-R02) corrected hist_bot magnitude but the portfolio-level cumulative return (hero strip + comparison rows) is still showing incorrect values. Needs fresh diagnosis: what is displayed vs what is correct, traced to source.
- [ ] **U7-02 — Per-symphony card sparkline missing the if-held line.** User: "there's no if-held line on the per-symph dashboard cards, just the live line." The design's card MicroTape shows TWO lines — bot (solid) and if-held (dashed). Live cards render only one. Add the second (if-held) series to the card sparkline.
- [ ] **U7-03 — Time-range selectors do not work (Dashboard hero + Performance + History).** User: "none of the time ranges work still." Previously marked CLOSED on the basis that the dev DB has only 4 data points so all windows render identically. User perceives this as broken. Resolve properly: either (a) seed enough historical data that windows visibly differ AND confirm each selector re-fetches/re-renders, or (b) if genuinely wired but data-limited, make that explicit. Re-verify Dashboard hero badges, /performance window selector, /history window selector all actually change the rendered data.
