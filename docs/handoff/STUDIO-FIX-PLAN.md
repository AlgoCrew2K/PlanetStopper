> **HISTORICAL** — This document is a pre-Sprint-1 or Sprint-1 cycle artifact preserved for provenance. See [docs/audit/](../audit/) for current state.

---

# Studio Redesign — Consolidated Fix Plan (v2)

**Source audit:** `docs/handoff/COMPREHENSIVE-AUDIT.md` (163 raw findings, 4 lenses)
**Auditor consults applied:** D-VIS-01 retracted (no layout compression); P-DAT-01 PASS (false alarm); D-CARD-01 / D-BEH-11 already CLOSED at HEAD; D-DAT-03 confirmed MAJOR; F-COD-01 alias strategy; D-BEH-10 downgraded to minor.
**Status:** AWAITING USER APPROVAL. No impl dispatched yet.
**Goal:** Close every remaining `[ ]` finding → full visual parity + full behavioral functionality, all screens.

This plan groups findings by **root cause** so one fix closes many findings. Dependencies are explicit — earlier packages must land before later ones can pass their verifications.

## Already-closed (no work)

- **D-CARD-01 / D-BEH-11** — Chart.js sparkline `destroy()` + `_sparks` registry landed at `2c6babb`. behavior originally audited at `2de552c` which predates the fix.
- **D-VIS-01** — retracted by visual; the live layout is NOT compressed (1440px content fills 1440px viewport). The headline-size sub-issue is tracked separately as D-VIS-05 in Tier 5.
- **P-DAT-01** — false alarm. analytics.py emits `win_rate` as fraction [0,1]; JS multiplication is correct; mock-data value 56.9 is already the post-multiplication display value.

---

## Tier 0 — Foundation (unblocks ~30% of issues)

### FP-T0-01 — Alias missing CSS tokens in `tokens.css`
**Root cause:** 6 CSS variables (`--studio-border`, `--studio-surface`, `--studio-surface-raised`, `--studio-ink-muted`, `--studio-scroll-thumb`, `--studio-scroll-thumb-hover`) are referenced across Performance/Advisor/History templates but never defined. Every border/surface element on those screens silently renders as empty.

**Closes:** F-COD-01, P-COD-02, A-COD-02, H-COD-02, H-COD-03, P-COD-04, A-COD-03, H-COD-05 (8 findings)

**Files:** `static/tokens.css` only

**Approach (per code's recommendation):** Add ALIASES in both `:root` and `[data-theme="dark"]` blocks:
```css
--studio-border: var(--studio-rule);
--studio-surface: var(--studio-paper);
--studio-surface-raised: var(--studio-paper-hi);
--studio-ink-muted: var(--studio-ink-dim);
--studio-scroll-thumb: var(--studio-ink-faint);
--studio-scroll-thumb-hover: var(--studio-ink-dim);
```
Follow-up sweep (separate task, post-blocker-clear) replaces the wrong names with canonical in the four templates so the design vocabulary stays single-source.

**Verification:** `grep -c "^\s*--studio-(border|surface|ink-muted|scroll-thumb)" static/tokens.css` ≥ 6 in each theme block; Performance/Advisor/History screens render visible borders + surfaces in browser.

---

### FP-T0-02 — Confirm Google Fonts actually load (Manrope, etc.)
**Root cause:** Visual auditor reports system-font fallback on Dashboard + Advisor cards. Either the `<link href="fonts.googleapis.com/...">` is missing, malformed, or blocked.

**Closes:** D-VIS-09, A-VIS-09, F-FONT-01 (3 findings)

**Files:** `templates/_chrome.html` (font preconnect + link)

**Approach:** Verify chrome include has both `<link rel="preconnect" href="https://fonts.googleapis.com">` AND the `<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@300..800&display=swap" rel="stylesheet">` (plus the other typefaces the tweaks panel offers). If present, verify network panel shows 200 for the css2 request — daemon may be blocking outbound.

**Verification:** `curl https://fonts.googleapis.com/css2?family=Manrope...` returns 200; live page network panel confirms; computed font-family on `body` resolves to `Manrope, ...`.

---

### FP-T0-03 — Route tweaks panel hardcoded rgba through tokens
**Root cause:** `tweaks.css` hardcodes `rgba(250, 249, 247, 0.92)` and similar — breaks dark theme panel.

**Closes:** F-COD-02 (1 finding)

**Files:** `static/tweaks.css`

**Approach:** Replace hardcoded backgrounds with `var(--studio-paper)` + retained `backdrop-filter`; use `var(--studio-rule)` for hover backgrounds.

**Verification:** Toggle to dark theme in browser; tweaks panel surfaces switch to dark palette.

---

## Tier 1 — Backend data plumbing (unblocks all "0.00%" symptoms)

### FP-T1-01 — Populate `portfolio_strip` in `get_api_state_dict()` so `_build_meta()` has real numbers
**Root cause:** `get_api_state_dict()` at `app.py:386` returns no `portfolio_strip`. The Jinja-render path through `dashboard()` then calls `_build_meta(ps={})` which short-circuits every numeric field to 0.0. Meanwhile `get_state()` at `app.py:439` DOES compute portfolio_strip — but only on the `/api/state` JSON path, not the Jinja path. So initial HTML renders are all zeros until the first JS poll.

**Closes:** D-DAT-01, D-DAT-02, D-DAT-04, D-DAT-05, D-HERO-01, D-HERO-02 (6 findings)

**Files:** `app.py` — `get_api_state_dict()` body

**Approach:** Refactor so the `portfolio_strip` computation in `get_state()` is extracted to a shared helper `_compute_portfolio_strip(bot_state, account_data)` that both `get_api_state_dict()` AND `get_state()` invoke. Both code paths then emit the same shape.

**Verification:** `curl /api/state | jq .portfolio_strip.cumulative_return.dry_run` returns non-zero; dashboard route renders non-zero hero numbers in initial Jinja HTML before any JS poll.

---

### FP-T1-02 — Fix `_build_meta` hist_bot to compound, not arithmetic-sum
**Root cause:** `_build_meta()` accumulates daily increments via arithmetic sum (`running += r`). Design expects compounded cumulative returns. Confirmed by dataflow: design mock series like `[0, 4.2, 7.8, 12.4, 15.1, 16.8, 18.3]` are cumulative percentage points produced by `(prod(1+r_i) - 1) * 100`. Approximation matches at small daily returns but diverges at higher cumulative gains.

**Closes:** D-DAT-03 (1 finding)

**Files:** `app.py` — `_build_meta()` hist series construction

**Approach:**
```python
running = 1.0
for daily_r in daily_returns:
    running *= (1.0 + daily_r)
    hist_bot.append((running - 1.0) * 100.0)
```

**Verification:** With a synthetic input of `[0.10, 0.10, 0.10]` (three +10% days), the series should emit `[10.0, 21.0, 33.1]`, not the arithmetic-sum `[10.0, 20.0, 30.0]`. Hero chart Bot line shape matches design at light theme.

---

### FP-T1-03 — Echo `window_days` in `/api/performance` and `/api/history` responses
**Root cause:** Both endpoints return the data for the requested window but never echo `window_days` in the response body. JS reads `data.window_days` → undefined → window label never updates.

**Closes:** P-DAT-02, H-DAT-03 (2 findings)

**Files:** `app.py` — `api_performance()` and `get_history()` handlers

**Approach:** Append `"window_days": days` to each response dict.

**Verification:** `curl /api/performance?days=90` and `curl /api/history/90` both include `"window_days": 90`.

---

### FP-T1-04 — `win_rate` scale (CLOSED — false alarm)
Confirmed by dataflow: API emits fraction in [0,1] (`analytics.py:390`), JS `*100` is correct, mock-data 56.9 is the displayed value. No work needed. Removed from active packages.

---

### FP-T1-05 — Backend extension: emit `four_gates_verdict` + `impact.before/after` from advisor
**Root cause:** `/ai-advisor/suggest` doesn't include `four_gates_verdict` or `impact.before/after` fields. All four gate badges render "unknown"; projected-impact bar hidden by JS guard.

**Closes:** A-DAT-01, A-DAT-02, A-CHART-02, A-BADGE-01, A-VIS-04, A-VIS-05 (6 findings)

**Files:** `ai_advisor.py` (suggestion-payload builder); `app.py` (POST handler if it wraps)

**Approach:** Extend `ConfigSuggestion` (or equivalent) to compute + emit:
- `four_gates_verdict: { allowlist: bool, risk_direction: bool, oos: bool, locked_vars: bool }`
- `impact: { before: float, after: float, metric: str }`
The gates already exist in the advisor's gating code — just surface them in the response.

**Verification:** `curl POST /ai-advisor/suggest` response includes both keys with non-null values; Advisor card renders four colored gate badges + impact bar.

---

### FP-T1-06 — Backend extension: normalize `baseline_decision` enum + add `frozen_eval_verdict` string
**Root cause:** `/api/autotune-runs` emits `baseline_decision` as verbose string ("Reverted to Fallback") instead of short token; emits `frozen_eval_sharpe` as float instead of pass/fail string. Design expects short enum tokens + chip.

**Closes:** A-DAT-03, A-DAT-04, A-VIS-07 (3 findings)

**Files:** `app.py` — `api_autotune_runs()` handler; possibly `database.py` read path

**Approach:** Map verbose strings to short enum (`"Reverted to Fallback"` → `"fallback"`, `"Applied"` → `"apply"`, `"Rejected"` → `"reject"`) at API serialization. Add a derived `frozen_eval_verdict` field: `"passed"` if `frozen_eval_sharpe ≥ threshold` else `"failed"`.

**Verification:** Response shows short tokens; Advisor right rail rows render colored pill badges.

---

### FP-T1-07 — Join symphony names into `/api/history/<days>.todays_exits`
**Root cause:** `todays_exits` entries emit raw `symphony_id` (UUID). Design shows human names. No name join in `get_history_summary()`.

**Closes:** H-DAT-01 (1 finding)

**Files:** `analytics.py` — `get_history_summary()`

**Approach:** Resolve `symphony_id` → `symphony.name` via `database.load_state()` lookup or a join query; emit both `symphony_id` and `symphony_name` per entry.

**Verification:** `curl /api/history/30` shows `symphony_name` strings; History today's exits row labels render names.

---

## Tier 2 — Layout (4K ultrawide)

### FP-T2-01 — Remove `max-width` caps from `.page-wrap` on secondary routes
**Root cause:** Performance/Advisor/History all set `.page-wrap { max-width: 88-90rem; margin: 0 auto; }`. On a 3840px ultrawide, content sits in a ~1400px column with cream margins. Design has no cap.

**Closes:** P-COD-01, A-COD-01, H-COD-01, P-LAY-01, A-LAY-01, H-LAY-01 (6 findings)

**Files:** `templates/performance.html`, `templates/ai_advisor.html`, `templates/history.html`

**Approach:** Remove `max-width` (use `width: 100%`); rely on chrome padding for whitespace. If the design DOES intend a max width for *content readability* on huge screens, raise to e.g. `120rem` rather than `88rem`.

**Verification:** Resize browser to 3840×1600; content fills the viewport edge-to-edge (modulo chrome padding).

---

### FP-T2-02 — Dashboard right-panel overflow at 1440×900
**Root cause:** D-VIS-01 (layout compression) was retracted; the page does fill 1440px. The right-overflow defects D-VIS-07 (alpha delta values clipped) and D-VIS-08 (third VWAP chip truncated) remain real. Hero right-panel column overflows its container by ~28px. Likely a missing `min-width: 0` on a flex child or a fixed column width too narrow for the content.

**Closes:** D-VIS-07, D-VIS-08, D-LAY-01 (3 findings — D-LAY-01 is "wide-screen audit" which folds in here)

**Files:** `templates/index.html` hero/right-panel CSS or inline styles.

**Approach:** Audit the hero right-panel column. Either widen the column (relative units), add `overflow: visible`, or add `min-width: 0` on the flex container so children can shrink-text-wrap rather than overflow.

**Verification:** At 1440×900, all three status-strip chips visible; alpha delta values fully readable on each comparison row.

---

### FP-T2-03 — Top nav layout on 4K
**Root cause:** Top nav from `_chrome.html` may center-cluster, stretch awkwardly, or look stranded at 3840px.

**Closes:** F-LAY-01 (1 finding)

**Files:** `templates/_chrome.html`, possibly `static/tokens.css` (nav-specific)

**Approach:** Audit current nav flex/grid CSS at 3840px; ensure it spans full width with sensible left/right padding.

**Verification:** Browser at 3840×1600; nav fills full width, account chip / mode pill / force-run cluster at far right.

---

## Tier 3 — Missing JS handlers (no-ops → real interactivity)

### FP-T3-01 — Wire hero window-selector buttons to actually filter chart data
**Root cause:** `applyHeroWindow` defined in inline `<script>` scope only; buttons toggle `.active` CSS but never call it.

**Closes:** D-HERO-04, D-COD-03 (the duplicate setInterval part too), D-BEH-01 (3 findings)

**Files:** `templates/index.html` inline script + `static/index.js`

**Approach:**
- Move `applyHeroWindow(days)` from inline to `static/index.js` so the button click handler can reach it.
- Remove the duplicate `setInterval(loadState, 30000)` (keep the one in index.js DOMContentLoaded).
- Wire each `[data-testid="window-Xd"]` button's `addEventListener('click', ...)` to call `applyHeroWindow` with its window value AND toggle the `.active` class.

**Verification:** Clicking a window badge re-slices the hero chart series and the chart visibly updates.

---

### FP-T3-02 — Wire Force-run button to POST `/api/trigger`
**Root cause:** `[data-testid="force-run-btn"]` has no `onclick` and no JS listener.

**Closes:** F-COD-04, F-FORCE-01, D-BEH-02 (3 findings)

**Files:** `templates/_chrome.html` (or a new `static/chrome.js`)

**Approach:** Add `onclick="forceRun(event)"` and implement `forceRun()` that POSTs `/api/trigger`, shows a transient toast or button-state change, then calls `loadState()`. Disable button during in-flight to prevent double-fire.

**Verification:** Click Force run; network panel shows POST `/api/trigger` 200; visible confirmation.

---

### FP-T3-03 — Wire workspace switcher to actually filter by account
**Root cause:** Click calls `window.loadState && loadState()`. `loadState` lives in `index.js` scope only, never attached to `window`. Click is silent.

**Closes:** F-COD-03, F-WORKSPACE-01, D-BEH-03 (3 findings)

**Files:** `templates/_chrome.html` + `static/index.js`

**Approach:**
- Expose `loadState` on `window` (or attach a listener via `addEventListener` from a script that has scope to it).
- Add `?account=<uuid>` param when calling `/api/state`.
- Backend: ensure `get_api_state_dict()` honors an `account` query param if present.

**Verification:** Click a non-active account chip; `/api/state?account=<uuid>` fires; symphonies and totals update for that account.

---

### FP-T3-04 — Wire Cash Now button to POST `/api/sell_account` or equivalent
**Root cause:** `[data-testid="cash-now-btn"]` on active + standby cards has no `onclick`, no listener.

**Closes:** D-COD-10 (1 finding)

**Files:** `templates/index.html` (cards) + `static/index.js`

**Approach:** Add `onclick="cashNow(event, '{{ sym.id }}')"` and implement `cashNow(e, symId)` that:
1. `e.stopPropagation()` (don't open detail panel)
2. Confirm modal (`window.confirm("Sell ${symName} to cash?")` minimum)
3. POST `/api/sell_account` with `{symphony_id, account_uuid}` payload
4. Disable button on click; show in-flight state.

**Verification:** Click Cash Now → confirm dialog → click yes → network shows POST → button shows "in cash" disabled state.

---

### FP-T3-05 — Wire `openDetailPanel(idx)` to actually use idx
**Root cause:** Function ignores its parameter; detail-panel stat IDs (`#dp-cr-bot`, `#dp-cr-held`, etc.) never get per-symphony data.

**Closes:** D-COD-01, D-DET-01 (2 findings)

**Files:** `templates/index.html` (the inline scripts) + `static/index.js`

**Approach:** Capture latest `botState` in module scope. On `openDetailPanel(idx)`:
1. Look up `sym = botState[symIdsArray[idx]]`.
2. Populate `#dp-title`, `#dp-cr-bot`, `#dp-cr-held`, `#dp-mdd`, `#dp-alpha`, etc. from `sym._cr`, `sym._mdd`, `sym.guard_alpha`.
3. Fetch `/api/chart/<sym.id>` and draw intraday overlays (depends on FP-T4-04).

**Verification:** Click any active/standby card → detail panel slides in with that symphony's name + populated stats.

---

### FP-T3-06 — Add ET clock ticking interval
**Root cause:** `[data-testid="et-clock"]` shows static Jinja-rendered value; no JS tick.

**Closes:** F-CLOCK-01, D-BEH-05 (2 findings)

**Files:** `static/index.js` (or chrome.js if extracted)

**Approach:** Add `setInterval(updateClock, 1000)` that formats current time in `America/New_York` zone and writes to the testid element.

**Verification:** Watch the clock element for 5 seconds; updates each second.

---

### FP-T3-07 — Fix market dot logic inversion + ticker missing ID
**Root cause:**
- (a) JS sets class `open` (green) when `is_market_open === false`.
- (b) `#hero-ticker` element absent from template; JS writes to null.

**Closes:** D-BEH-06, D-BEH-07, F-MARKET-01 (3 findings)

**Files:** `templates/index.html` (add the ticker span); `static/index.js` (invert market-open conditional)

**Approach:** Add `<span data-testid="ticker-value" id="hero-ticker"></span>` in the appropriate hero region. Flip the dot-class boolean in JS.

**Verification:** Weekend → red dot, SPY/QQQ price visible in ticker span and updating on poll.

---

### FP-T3-08 — Add MISSING `hero-tracked` / `hero-armed` IDs to template
**Root cause:** JS targets these IDs to update mini-stat values on poll; HTML has the values but no IDs.

**Closes:** D-COD-02, D-DAT-06 (2 findings)

**Files:** `templates/index.html` — mini-stat value spans

**Approach:** Add `id="hero-tracked"` and `id="hero-armed"` (and triggered) to the relevant `.mini-stat-value` spans.

**Verification:** Mini-stats update on JS poll without page reload when bot_state changes.

---

## Tier 4 — Chart / SVG rendering

### FP-T4-01 — Replace MC dial `<div>` with real SVG arc dial
**Root cause:** `renderMcDial` writes percentage text into a plain `<div>`; design specifies an SVG arc ring with track + colored arc + centered text.

**Closes:** D-COD-08, D-MC-01, D-VIS-10, D-BEH-04 (4 findings)

**Files:** `templates/index.html` (the MC dial element), `static/index.js` (`renderMcDial`)

**Approach:** Replace the `<div data-testid="mc-dial">` with `<svg ...><circle ... track/><circle ... arc/></svg>`. JS function sets the arc's `stroke-dasharray` / `stroke-dashoffset` proportional to `mc_prob`. Matches the design's `Dial` component in `studio.jsx`.

**Verification:** Every active card shows a visible arc ring; arc length scales with mc_prob.

---

### FP-T4-02 — Make symphony card sparklines responsive (fill card width)
**Root cause:** Chart.js destroy bug already CLOSED at `2c6babb` per behavior consult. Remaining defect: `responsive: false` + hardcoded `width="120"` means sparklines don't fill the card width.

**Closes:** D-COD-09, D-VIS-11, D-CARD-03 (3 findings — D-CARD-01 and D-BEH-11 already closed)

**Files:** `static/index.js` — `renderSparkline`

**Approach:** Switch to `responsive: true` + `maintainAspectRatio: false`; ensure canvas wrapper has a defined height (e.g. `height: 32px`).

**Verification:** Sparklines fill full card width at 1440 and 3840 viewports.

---

### FP-T4-03 — Add autotune-runs sparkline canvas to Advisor + render line
**Root cause:** Canvas element `#autotune-sparkline-canvas` is absent from `ai_advisor.html`; JS `renderAutotuneSparkline` silently exits.

**Closes:** A-COD-05, A-BEH-01, A-CHART-03, A-VIS-06 (4 findings)

**Files:** `templates/ai_advisor.html` (add canvas), `static/ai_advisor.js` (verify renderAutotuneSparkline reaches it)

**Approach:** Add `<canvas data-testid="autotune-sparkline" id="autotune-sparkline-canvas">` in the right rail above the runs table. Verify render function fires after `/api/autotune-runs` fetch.

**Verification:** Right rail shows a Sharpe-trend line chart; toDataURL > 200 bytes.

---

### FP-T4-04 — Detail panel intraday chart + overlay toggles + risk math panel
**Root cause:** Detail panel `#intraday-canvas` is unstyled placeholder; no overlay toggles (Stop, Breakeven, VWAP, MC); risk math section absent.

**Closes:** D-COD-04, D-DET-02, D-DET-03, D-DET-04, D-COD-05 (5 findings)

**Files:** `templates/index.html` (detail panel HTML), `static/index.js` (intraday rendering, event toggles, log view link, go-to-cash header button)

**Approach:**
- Wire `openDetailPanel(idx)` (depends on FP-T3-05) to fetch `/api/chart/<sym.id>` and `/api/logs/<sym.id>`.
- Implement Chart.js multi-dataset render: Bot, Held-from-trigger, Stop, Breakeven window, VWAP, MC (right axis).
- Add 4 toggle buttons that show/hide each overlay dataset.
- Add risk math panel rendering MC prob, stop distance, vol, parabolic velocity, breakeven status, VWAP defense state.
- Add "View logs" + "Go to cash →" buttons in panel header.

**Verification:** Open detail panel for triggered symphony; intraday chart shows all 6 overlays; toggle buttons show/hide each.

---

### FP-T4-05 — Cumulative-returns divergence shading on Performance
**Root cause:** Design shows green tint area-fill between bot and held lines where bot > held; live chart has no shading.

**Closes:** P-CHART-02, P-VIS-06 (2 findings)

**Files:** `static/performance.js`

**Approach:** Add a third Chart.js dataset with `fill: {target: 0}` and gradient color → render area between the two existing lines as a semi-transparent green fill.

**Verification:** Performance chart shows green tinted region wherever Bot line is above Held line.

---

### FP-T4-06 — Confidence ring + projected-impact bar on Advisor cards
**Root cause:** Suggestion cards lack the SVG confidence arc + horizontal impact mini-bar.

**Closes:** A-CHART-01, A-VIS-03 (2 findings) — *also requires FP-T1-05 backend extension*

**Files:** `templates/ai_advisor.html` (card template), `static/ai_advisor.js` (renderSuggestion)

**Approach:** Replace card placeholder regions with:
- SVG arc ring (same pattern as MC dial in FP-T4-01) bound to `s.confidence`.
- Horizontal mini-bar div showing `s.impact.before` vs `s.impact.after` with delta label.

**Verification:** Each rendered suggestion card has a visible confidence ring + impact bar.

---

### FP-T4-07 — History daily alpha chart baseline + by-reason bars
**Root cause:**
- `renderDailyChart` reads `--studio-border` (undefined) for baseline color → invisible baseline line.
- By-reason cards: `renderReasonCards` early-exits on empty `by_reason` without clearing static skeleton → static placeholders remain visible.

**Closes:** H-COD-03, H-COD-04, H-CHART-01, H-CHART-02 (4 findings)

**Files:** `static/history.js`

**Approach:**
- Change `color('--studio-border')` → `color('--studio-rule')` (canonical token).
- On empty `by_reason`, replace container with empty-state HTML rather than leaving static skeleton.

**Verification:** Daily alpha chart shows visible baseline; empty by_reason renders empty-state message.

---

## Tier 5 — Structure / visual parity

### FP-T5-01 — Add Today's Exits status-strip chips
**Closes:** D-COD-06, D-VIS-08 (2 findings)
**Files:** `templates/index.html` (status strip), `static/index.js` (chip update on poll)
**Approach:** Add three chip elements for Trailing stop / Take-profit / VWAP with counts from `meta.triggers_today.*` (requires backend field — likely present after FP-T1-01).

### FP-T5-02 — Add card DualStat footer grid (Today / Cum / Max DD / Levels)
**Closes:** D-COD-07 (1 finding)
**Files:** `templates/index.html` (card body)
**Approach:** Extend card HTML to a 3-4 column footer grid binding `sym._cr.dry_run/if_held`, `sym._tc`, `sym._mdd`, with α delta in corner.

### FP-T5-03 — Heading typography systematic fix
**Root cause:** All major section headings on Performance/Advisor/History rendered at ~28px instead of design's ~48px/36px.
**Closes:** D-VIS-05, P-VIS-01, A-VIS-01, H-VIS-01 (4 findings)
**Files:** `templates/<each>.html` or shared CSS
**Approach:** Establish a `.studio-heading` class in tokens.css with the design size/weight; apply consistently.

### FP-T5-04 — Section labels ALL-CAPS → Title Case (one fix, four screens)
**Closes:** H-VIS-09 + D-VIS variants of same pattern (4+ findings cross-cutting)
**Files:** `static/tokens.css` (the `.section-label` rule)
**Approach:** Single change: `text-transform: none` (remove uppercase); adjust size/weight to design spec.

### FP-T5-05 — Replace `<select>` dropdowns with SegControl button strips
**Closes:** P-COD-05, P-VIS-02, H-VIS-02 (3 findings)
**Files:** `templates/performance.html`, `templates/history.html`, possibly shared CSS
**Approach:** Mirror the dashboard's `.window-selector` button-group pattern for scope + window controls on Performance and window on History.

### FP-T5-06 — Hero status pill opacity + card box-shadow
**Closes:** D-VIS-12, D-VIS-13 (2 minor findings)

### FP-T5-07 — Nav corrections: remove extra Tweaks/back-link, fix active underline position, "Force run now" copy
**Closes:** D-VIS-02, D-VIS-03, D-VIS-04, P-VIS-10 (4 findings)

### FP-T5-08 — Performance Risk metrics section label + delta column color/arrow + duplicate legend
**Closes:** P-VIS-05, P-VIS-07, P-VIS-08 (3 findings)

### FP-T5-09 — Performance insufficient-history banner position + token color
**Closes:** P-VIS-03 (1 finding)

### FP-T5-10 — Advisor button-label "Run Advisor" → "Apply suggestion"; table → card list
**Closes:** A-VIS-02, A-VIS-08 (2 findings)

### FP-T5-11 — History rationale lines on reason cards; wins as fraction "28/38"; "Today's exits (N)" with count
**Closes:** H-VIS-06, H-VIS-07, H-VIS-08 (3 findings)

### FP-T5-12 — History $ saved precision `.toFixed(2)`
**Closes:** H-BEH-01, H-VIS-03 (2 findings)

---

## Tier 6 — Behavior edges / error handling

### FP-T6-01 — Math overlays tweaks toggle CSS hookup
**Closes:** D-BEH-08, F-TWEAK-05 (2 findings)
**Files:** `static/tokens.css` (add `[data-math-overlays="false"] [data-testid="mc-dial"] { display: none; }` etc.)

### FP-T6-02 — Number format tweaks toggle hooked into JS formatters
**Closes:** D-BEH-09, F-TWEAK-06 (2 findings)
**Files:** `static/index.js` (read `document.documentElement.dataset.numFormat` in formatter)

### FP-T6-03 — Performance scope=symphony with no symphony → graceful error
**Closes:** P-BEH-01 (1 finding)
**Files:** `static/performance.js`
**Approach:** When scope = "symphony" and no symphony selected, show "Pick a symphony" empty state instead of firing a 400.

### FP-T6-04 — Performance annualized_return blow-up clamp
**Closes:** P-BEH-02 (1 finding)
**Files:** `static/performance.js` (or `analytics.py` for the source)
**Approach:** Clamp `annualized_return` to a sane band (e.g. `Math.abs(v) > 1000` → render em-dash) AND/OR fix the analytics annualization when observation count is too low.

### FP-T6-05 — Advisor error message visibility path
**Closes:** A-BEH-02, A-API-01 (2 findings)
**Files:** `static/ai_advisor.js`
**Approach:** `showError()` must clear `display:none` (e.g. `el.style.display = ''`) before setting textContent.

### FP-T6-06 — Performance `insufficient_history` banner trigger
**Closes:** P-INSUFF-01 (1 finding)

### FP-T6-07 — History empty-state when `total_alpha=0`
**Closes:** H-EMPTY-01 (1 finding)

---

## Tier 7 — Confirmed PASS (no work needed)

These behavior-audited items rated PASS — no fix:
- A-INTERACT-01 (symphony picker), A-INTERACT-02 (Apply), A-INTERACT-03 (Dismiss) — all fire correct fetches per behavior's audit.
- D-HERO-05 (if-held magnitude) — real data, not a bug.
- F-TWEAK-01..07 — needs spot-check but no current finding.

## Tier 8 — Cycle 6 — Settings route
Out of scope for this fix plan. Settings was halted before this audit; once Tier 0-6 close, Settings re-opens with its own brief.

---

## Dispatch order

1. **Tier 0 first** — tokens + fonts unblock the visual checks on Tier 5 visual items.
2. **Tier 1 in parallel** — backend data plumbing has no Tier 0 dependency; unblocks Tier 5 visual checks too.
3. **Tier 2** — layout removes the right-overflow + 4K compression issues.
4. **Tier 3** — JS handlers (independent, can dispatch in parallel).
5. **Tier 4** — chart rendering (some depend on Tier 1 data being right).
6. **Tier 5** — visual structure work; some depends on Tier 4 (e.g. SVG dials).
7. **Tier 6** — behavior edges & error handling, last.

**PM owns merging each tier into main only after parity (or user) verifies it in a real browser at both 1440 and 3840 widths, light + dark.**

---

## Auditor consult responses (applied in v2)

- ✅ **visual** — D-VIS-01 retracted (no compression). Headline size is its own narrow issue, already tracked via D-VIS-05.
- ✅ **dataflow** — P-DAT-01 PASS (false alarm). D-DAT-03 confirmed MAJOR with `(∏(1+r_i)-1)*100` formula.
- ✅ **behavior** — D-BEH-10 downgraded to minor (~50ms flash, not 30s). D-BEH-11 / D-CARD-01 already CLOSED at `2c6babb`.
- ✅ **code** — alias strategy in `tokens.css`; canonical-name template sweep deferred to post-blocker follow-up task.
