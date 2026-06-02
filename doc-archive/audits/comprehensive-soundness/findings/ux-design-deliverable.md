<!-- ARCHIVED from audit/comprehensive-soundness @ 848b492, original date 2026-05-30. UX design deliverable for risk-adjusted metrics dashboard (Tier 1/Tier 2 split). Implemented in Phase 2/2b per memory/project_adaptive_exit_direction.md. -->
# UX Design Deliverable — Risk-Adjusted Metrics as First-Class Data
**Branch tip:** 8586ab2  
**Authored by:** ux-designer (audit-soundness team, task #7)  
**Date:** 2026-05-30

---

## 1. Current Dashboard Readiness Assessment

### 1.1 What Exists Today

#### Performance page (`templates/performance.html` + `static/performance.js`)

The Performance tab is the primary home for risk metrics. It already surfaces:

| Metric | Location | Source |
|---|---|---|
| Cumulative return chart (bot vs if-held) | Chart block | `shadow_returns` / `live_returns` from `/api/performance` |
| Annualized return (CAGR) | Risk Metrics table | `analytics.compute_quantstats_metrics` → `annualized_return` |
| Sharpe ratio | Risk Metrics table (primary row, bold) | same, `annualize=False`, rf=0 |
| Sortino ratio | Risk Metrics table | same, `annualize=False` |
| Max drawdown | Risk Metrics table (primary row, bold) | same, quantstats `max_drawdown` (≤0 convention) |
| Calmar ratio | Risk Metrics table | same, CAGR / |MDD| |
| Win rate | Risk Metrics table | same, fraction positive observations |
| Guard Alpha headline | 4-column headline strip | shadow total_return − live total_return |
| Bot total return | 4-column headline strip | shadow total_return |
| If-held total return | 4-column headline strip | live total_return |
| Observation count | 4-column headline strip | len(dates) |
| Scope toggle (Aggregate / Per-symphony) | Header controls | — |
| Window toggle (30d–5Y) | Header controls | — |

#### Dashboard (`templates/index.html`) — hero section

The hero already surfaces portfolio-level:

| Metric | Location |
|---|---|
| Cumulative bot vs if-held chart | Hero left column |
| Bot vs If-Held return bars (today, cumulative, max DD) with alpha delta | `vs-rows` in hero right column |
| Max drawdown bot and if-held with alpha | `vs-row` in hero right column |
| Per-symphony card footer: today, cumulative, max DD — bot vs held with alpha | `card-footer-grid` per `.sym-card` |
| MC probability dial | Per-symphony card |
| Guard alpha total | Hero headline |

### 1.2 What Is Missing

#### Immediately computable from existing return series (Tier 1 — no new data plumbing)

These can be derived entirely from the daily `live_returns` and `shadow_returns` arrays already returned by `/api/performance`. They only require calling additional `quantstats` functions or simple arithmetic on the existing series:

| Missing metric | Formula / source | Notes |
|---|---|---|
| Annualized volatility (bot) | `std(daily_returns) * sqrt(252)` | quantstats has `qs.stats.volatility()` |
| Annualized volatility (if-held) | same on live series | — |
| Volatility reduction | bot_vol − held_vol | Negative = bot is calmer, desirable |
| Downside deviation | `std(returns[returns<0]) * sqrt(252)` | Input to Sortino; already implicit |
| Max drawdown reduction (%) | held_mdd − bot_mdd | Already displayed as `mdd_alpha` in hero/cards but NOT in the Performance table |
| Sharpe delta | bot_sharpe − held_sharpe | Already computed in Performance table Delta column but labelled ambiguously |
| Sortino delta | bot_sortino − held_sortino | Same |

#### Requires new external data feed (Tier 2 — new plumbing needed)

These require benchmark daily return series (SPY, TQQQ) to be fetched, cached, and aligned with the strategy's return dates. No such data pipeline exists today:

| Missing metric | What it needs | Blocker |
|---|---|---|
| Upside capture % vs SPY | SPY daily returns aligned to same date range | Alpaca API can supply this; needs new DB table or in-memory join |
| Downside capture % vs SPY | same | same |
| Beta vs SPY | same | same |
| Sharpe vs SPY benchmark (information ratio) | same | same |
| Upside / downside capture vs TQQQ | TQQQ daily returns | same; TQQQ is available via Alpaca |
| B&H SPY return comparison | SPY returns over same window | same |

**Read-only constraint confirmed:** `templates/*.html` open SQLite read-only; UI never reruns the engine. Any new benchmark data must arrive via the existing `/api/performance` JSON response shape. The API route is already the correct extension point — new fields on the existing JSON response.

### 1.3 Where Risk-Adjusted Metrics Would Live

| Surface | Scope | Appropriate placement |
|---|---|---|
| Performance page | Aggregate and per-symphony | Expand existing Risk Metrics table with new rows; add a second "Volatility" headline stat replacing "Observations" (low-value for expert) |
| Performance page | Per-symphony only | Add a "vs Baseline" comparison section below the metrics table when scope = symphony and baseline data is available |
| Dashboard hero | Portfolio level | Add annualized volatility reduction to the existing vs-rows block as a 4th row |
| Symphony cards (index.html) | Per-symphony | Not recommended — cards are already dense; annualized vol can be deferred to the detail panel |
| Detail panel (index.html) | Per-symphony | Add a "Risk profile" section within the existing slide-over panel with vol, Sharpe, Sortino bot vs held |

---

## 2. Design Direction

### 2.1 Hierarchy Principle

Planet Stopper's north star is capital preservation, not return maximization (see vision doc §2). The design hierarchy must reflect this: **risk-adjusted metrics rank above raw return metrics**. The current table buries Sharpe below total return. The redesign inverts this.

Proposed primary metrics (bold rows, rendered first):
1. **Sharpe** — canonical risk-adjusted return per unit of total volatility
2. **Sortino** — downside-penalized; more relevant for this system (it only manages downside)
3. **Max drawdown reduction** — direct evidence of the system's stated purpose
4. **Guard Alpha** — headline value-add, already present

Secondary metrics (normal weight, rendered after):
- Total return (bot vs held)
- Annualized return (CAGR)
- Calmar
- Annualized volatility (bot vs held)
- Win rate

### 2.2 Layout Changes

#### Performance page — Headline Strip

Current 4-column strip: `Guard Alpha | Bot Return | If-Held Return | Observations`

Proposed: `Guard Alpha | Sharpe Delta | MDD Reduction | Sortino Delta`

Rationale: The bot/held raw returns are already visible in the chart endpoint labels and in the metrics table. The 4-stat strip should lead with risk-adjusted evidence. "Observations" count is low-value operator data; move it to a small caption below the chart or as secondary metadata in the header subtitle.

#### Performance page — Risk Metrics Table

Current column headers: `Metric | Live · if held | Bot · Planet Stopper-exited | Delta`

These headers stay. Changes:
1. Reorder rows to lead with risk-adjusted: Sharpe (primary), Sortino, Max Drawdown (primary), Calmar, then returns.
2. Add two new rows in Tier 1 (immediately computable): **Annualized Volatility** and **Volatility Reduction**.
3. Add a row for **Max Drawdown Reduction (%)** explicitly — this is `mdd_alpha` already on dashboard cards but absent from the table.
4. For Tier 2 (requires plumbing): add grayed-out placeholder rows with `— data not available` and a tooltip "Requires benchmark data feed" — this communicates the roadmap intent without false data.

Table row order (proposed):
```
[primary] Sharpe
          Sortino
[primary] Max drawdown
          Max drawdown reduction (% improvement)    ← new Tier 1
          Calmar
          Annualized volatility                     ← new Tier 1
          Volatility reduction (% improvement)      ← new Tier 1
          Total return
          Annualized return (CAGR)
          Win rate
          ─────────────────────────────────────────
          Upside capture vs SPY [grayed placeholder]  ← Tier 2 roadmap
          Downside capture vs SPY [grayed placeholder] ← Tier 2 roadmap
```

#### Performance page — "vs Baseline" Section (Per-Symphony scope, Tier 2)

When scope = `symphony` and benchmark data is available, add a dedicated collapsible section below the metrics table:

```
▾  vs Benchmarks                         [30d ▾]
   METRIC              vs SPY    vs TQQQ
   Upside capture %    --        --
   Downside capture %  --        --
   Beta                --        --
```

When benchmark data is NOT available, show the section header with a single prose note:
> "Benchmark comparisons (SPY, TQQQ) require a market data feed for those symbols. Not yet configured."

This makes the design forward-compatible without shipping broken UI.

#### Dashboard Hero — vs-Rows Block

Current 3 rows: `Today | Cumulative | Max DD`

Add a 4th row: **Volatility** (annualized bot vol vs annualized held vol, alpha = held_vol − bot_vol). This row uses the same winner-encoded bar pattern already in the hero. The alpha badge is green when bot is calmer (lower vol = favorable).

#### Symphony Cards — No Change

Cards are already at the density limit. Max drawdown and cumulative alpha are visible in the footer grid. Risk metrics beyond MDD belong in the detail panel, not the card.

#### Detail Panel — "Risk Profile" Section

Add a new section after the existing "Math layers" readout, before "Vars":

```
RISK PROFILE                    ← .section-label
Bot    If Held   Improvement
Sharpe    1.12    0.94    +0.18
Sortino   1.68    1.42    +0.26
Max DD   -2.1%   -3.8%   +1.7pp
Ann. Vol  8.4%   11.2%   −2.8pp
```

Rendered using the existing `.math-row` / `.math-row-value` / `.math-row-hint` pattern — no new CSS classes needed.

### 2.3 "vs Baseline" Comparison Legibility

For a non-expert operator, the key legibility principle is: **one direction is always "good."** For this system:
- Higher Sharpe/Sortino = good
- Lower |max drawdown| = good (Planet Stopper's raison d'être)
- Lower volatility = good (the system is explicitly risk-averse)
- Higher upside capture AND lower downside capture = good

Implementation rule: every comparison cell shows a colored delta badge using the existing `--studio-pos` / `--studio-neg` token palette — green when bot beats held on that metric, red when it doesn't. This is already the pattern in the `vs-rows` hero block. The delta column in the metrics table already does this. The design is consistent — extend the pattern, don't create a new one.

For baseline comparisons (Tier 2), the framing should be:
- Upside capture % > 100% means bot captured MORE of the upside than the benchmark — label "BEATS" in `--studio-pos`
- Downside capture % < 100% means bot suffered LESS drawdown than benchmark — label "PROTECTS" in `--studio-pos`

Both need inline tooltips for a non-expert: `ⓘ "% of the market's up days captured by this strategy"`

### 2.4 Data Availability States

The existing `insufficient-history` warning banner pattern (amber, dismissible) should be extended:
- **< 30 observations:** existing banner — "Insufficient history. N days available; stable metrics need 30+."
- **0 observations:** gray-out the entire metrics table with a centered "No post-mortem data yet" message.
- **Tier 2 data absent:** grayed-out rows (opacity: 0.4) with `—` values and a tooltip. Never show broken/missing data as `--` without explanation.

---

## 3. Ready-to-Use Prompt for Claude Design (frontend-design)

---

**PROMPT FOR frontend-design:**

You are updating the Planet Stopper AlphaBot dashboard to surface risk-adjusted metrics as first-class data on the Performance page and Dashboard hero. This is a read-only operator surface — templates open SQLite read-only, UI never reruns the engine, all data flows through existing API JSON. Do NOT add new backend API calls beyond what already exists.

**Design system:** All styling must use `--studio-*` CSS custom properties from `static/tokens.css`. No bare hex values anywhere outside `:root` in `tokens.css`. Light and dark themes are defined there. Use existing component patterns: `.headline-stat`, `.vs-row`, `.metrics-tbl`, `.math-row`, `.cfg-cell`, `.cfg-dual-row`, `.section-label`.

**Canonical breakpoints for testing:** 1440px (primary), 768px (tablet), 375px (mobile). The `.page-wrap` container and fluid grids handle responsiveness — you must not break them.

**Files to change:**
- `templates/performance.html` — layout, metric rows, headline strip
- `static/performance.js` — metric rendering logic, METRIC_LABELS array, renderMetrics(), renderHeadlineStats()
- `templates/index.html` — hero vs-rows block (add volatility row), detail panel risk-profile section
- DO NOT change `static/tokens.css`, `static/layout.css`, `static/tweaks.css`, `static/tweaks.js`, `templates/_chrome.html`

---

### Change 1: Performance Page — Headline Strip

**File:** `templates/performance.html`, section `data-testid="headline-strip"`

Replace the current 4-column strip:
```
Guard Alpha | Bot Total Return | If-Held Total Return | Observations
```
With:
```
Guard Alpha | Sharpe Delta | MDD Reduction | Sortino Delta
```

- "Guard Alpha" — unchanged: cumulative guard α, colored `--studio-pos`/`--studio-neg` by sign.
- "Sharpe Delta" — label `SHARPE IMPROVEMENT`, value = bot_sharpe − held_sharpe, formatted to 2 decimal places, colored by sign.
- "MDD Reduction" — label `MAX DD REDUCTION`, value = |held_mdd| − |bot_mdd| expressed as percentage points (e.g. "+1.70pp"), colored `--studio-pos` if positive (bot has less drawdown = good).
- "Sortino Delta" — label `SORTINO IMPROVEMENT`, value = bot_sortino − held_sortino, formatted to 2 decimal places, colored by sign.

Observation count moves to a small `data-testid="obs-caption"` `<p>` inside `.perf-subtitle`, e.g. "N observations · 60d window".

**JS:** In `renderHeadlineStats(payload)`:
- Add computation of sharpeDelta, mddReduction, sortinoDelta from `payload.shadow_metrics` and `payload.live_metrics`.
- MDD values are fractional and ≤0; compute reduction as `live.max_drawdown − shadow.max_drawdown` (a positive number when bot has a less-negative MDD).
- Update the four stat `<div>` elements with `data-testid` attributes `guard-alpha-stat`, `sharpe-delta-stat`, `mdd-reduction-stat`, `sortino-delta-stat`.

---

### Change 2: Performance Page — Risk Metrics Table

**File:** `static/performance.js`, `METRIC_LABELS` array and `renderMetrics()` function.

**New METRIC_LABELS** (order defines rendering order):

```javascript
var METRIC_LABELS = [
    // --- risk-adjusted: primary (bold) ---
    ['sharpe',               'Sharpe',                     'num',      true ],
    ['sortino',              'Sortino',                    'num',      false],
    ['max_drawdown',         'Max drawdown',               'pct_frac', true ],
    ['max_drawdown_delta',   'Max DD reduction',           'pp',       false],  // derived
    ['calmar',               'Calmar',                     'num',      false],
    // --- volatility ---
    ['volatility',           'Annualized volatility',      'pct_frac', false],  // new Tier 1
    ['volatility_delta',     'Volatility reduction',       'pp',       false],  // new Tier 1, derived
    // --- return ---
    ['total_return',         'Total return',               'pct_frac', true ],
    ['annualized_return',    'Annualized return (CAGR)',   'pct_frac', false],
    ['win_rate',             'Win rate',                   'frac',     false],
    // --- roadmap placeholders (Tier 2, grayed out) ---
    ['upside_capture',       'Upside capture vs SPY',      'pct',      false],  // placeholder
    ['downside_capture',     'Downside capture vs SPY',    'pct',      false],  // placeholder
];
```

The 4th element in each tuple is `isPrimary` (boolean, replacing the current `PRIMARY_METRICS` lookup).

**Derived fields to compute in `renderMetrics()` before rendering:**

```javascript
// max_drawdown_delta: held_mdd − bot_mdd (positive = bot has less drawdown)
var heldMdd = (live.max_drawdown || 0);    // ≤ 0 fraction
var botMdd  = (shadow.max_drawdown || 0);  // ≤ 0 fraction
shadow['max_drawdown_delta'] = (heldMdd - botMdd);  // ≥ 0 when bot better
live['max_drawdown_delta']   = 0;                    // baseline is zero

// volatility: std(daily_returns_fraction) * sqrt(252)
// The API already returns live_returns and shadow_returns as pct-point arrays.
// Add 'volatility' to the API response OR compute client-side:
// volatility = stddev(returns_array / 100) * sqrt(252)
// For now compute from the series passed to renderChart.
// (see Note A below on where to add this computation)

// volatility_delta: held_vol − bot_vol (positive = bot calmer, favorable)
```

**Note A — Volatility computation:** Volatility requires the full return series, not just the metrics dict. Two options:
1. **Backend (preferred):** Add `volatility` key to `analytics.compute_quantstats_metrics()` using `qs.stats.volatility(series)`. This keeps all metric math in one place and is consistent with the other quantstats calls. The `/api/performance` response already returns `live_metrics` and `shadow_metrics` dicts — add `volatility` there.
2. **Client-side fallback:** Compute stddev from `payload.live_returns` and `payload.shadow_returns` in `performance.js`. Acceptable if backend change is deferred.

**Preferred option: backend.** Add to `analytics.py` in `compute_quantstats_metrics()`:
```python
metrics["volatility"] = _safe(lambda: qs_stats.volatility(series))
```
This requires no schema change, no new API endpoint, and no new data fetch.

**Placeholder row rendering:** For `upside_capture` and `downside_capture`, when the value is `null`/`undefined`, render the row with:
- Both value columns: `<span class="metric-unavail" title="Requires SPY/TQQQ benchmark data feed">—</span>` styled with `color: var(--studio-ink-faint); opacity: 0.55;`
- Delta column: empty
- Row `data-unavail="true"` attribute for test assertions

---

### Change 3: Performance Page — Table Column Headers and Section Title

**File:** `templates/performance.html`

Change section title from "Risk Metrics" to "Risk Metrics" — unchanged, appropriate.

Change column headers to make the comparison framing explicit for non-experts:
```html
<th>Metric</th>
<th style="text-align:right">If-held baseline</th>
<th style="text-align:right">Bot (Planet Stopper)</th>
<th style="text-align:right">Delta (bot − baseline)</th>
```

Add a one-line prose caption below the section heading:
```html
<p class="metrics-caption">
  Positive deltas on risk metrics (Sharpe, Sortino, drawdown reduction) mean the bot
  improved risk-adjusted outcomes vs holding through.
</p>
```
Style `.metrics-caption` with `font-size: 0.75rem; color: var(--studio-ink-dim); margin-bottom: 0.75rem;`.

---

### Change 4: Dashboard Hero — Add Volatility Row to vs-Rows Block

**File:** `templates/index.html`, inside `<div id="portfolio-strip" class="vs-rows">`

After the existing Max DD row, add a 4th row:

```html
<div data-testid="vs-row" class="vs-row">
  <div class="vs-row-top">
    <span class="vs-row-label">Ann. Vol</span>
    <span data-testid="comp-vol-bot-text" class="vs-val">Bot --</span>
    <span data-testid="comp-vol-held-text" class="vs-val">Held --</span>
    <span class="vs-delta" data-testid="comp-vol-delta">α --</span>
  </div>
  <div class="vs-bars">
    <div class="vs-bar-row">
      <span class="vs-bar-label" id="vol-bot-winner-label">Bot</span>
      <div data-testid="vs-bar" class="vs-bar-track">
        <div data-testid="comp-bar-vol-bot" class="vs-bar-fill" style="width:0%"></div>
      </div>
    </div>
    <div class="vs-bar-row">
      <span class="vs-bar-label" id="vol-held-winner-label">Held</span>
      <div data-testid="vs-bar" class="vs-bar-track">
        <div data-testid="comp-bar-vol-held" class="vs-bar-fill" style="width:0%"></div>
      </div>
    </div>
  </div>
</div>
```

**Important:** For volatility, the bot WINS when its volatility is LOWER than held (opposite sign convention from return rows). The winner bar logic must be reversed:
```
vol_bot_wins = vol_bot <= vol_held   # lower vol = better for this system
```
The alpha delta label: `α −1.5pp` colored `--studio-pos` when bot is calmer (negative delta = good here — clarify with tooltip or label "Bot calmer by 1.5pp").

**However,** the hero section reads from Jinja template variables populated by the Flask route, not from JS. The portfolio-level volatility values (`vol_bot`, `vol_held`) must be added to the `meta.portfolio` dict in the Flask `index()` route. This requires the analytics layer to compute portfolio-level annualized volatility from the shadow_history series (same data the hero chart uses). Specifically: the `_analytics_strip` dict that populates `meta.portfolio.cr` / `mdd` should also include `vol` keys.

If this backend change is deferred, the volatility row can be stub-rendered with `--` values and updated by a JS poll of `/api/performance?scope=aggregate` (the performance page already does this). The JS for the dashboard hero can read the volatility from that response and update the DOM.

---

### Change 5: Detail Panel — "Risk Profile" Section

**File:** `templates/index.html`, inside the `<div class="detail-body">` element in the `#detail-panel`

After the existing math-layer section and before the vars section, add:

```html
<div style="padding: 18px 24px 0;">
  <div class="section-label" style="font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:var(--studio-ink-faint);margin-bottom:10px;">
    Risk Profile
  </div>
  <div id="detail-risk-profile" data-testid="detail-risk-profile">
    <!-- Populated by JS in openDetailPanel() -->
  </div>
</div>
```

In the JS `openDetailPanel()` function, after populating existing detail sections, add:

```javascript
// Risk profile: Sharpe, Sortino, MDD, Annualized Vol — bot vs held
// Fetched from /api/performance?scope=symphony&symphony_id=<id>&days=60
fetch('/api/performance?scope=symphony&symphony_id=' + encodeURIComponent(symId) + '&days=60')
  .then(function(r){ return r.json(); })
  .then(function(p){
    var bot  = p.shadow_metrics || {};
    var held = p.live_metrics   || {};
    var rows = [
      ['Sharpe',          bot.sharpe,      held.sharpe,      'num',      false],
      ['Sortino',         bot.sortino,     held.sortino,     'num',      false],
      ['Max drawdown',    bot.max_drawdown,held.max_drawdown,'pct_frac', true ],
      ['Ann. volatility', bot.volatility,  held.volatility,  'pct_frac', false],
    ];
    var html = rows.map(function(r){
      var label=r[0], bv=r[1], hv=r[2], kind=r[3], invertDelta=r[4];
      var delta = (bv !== null && hv !== null) ? bv - hv : null;
      var deltaGood = invertDelta ? delta <= 0 : delta >= 0;
      var dcol = delta === null ? 'inherit' : deltaGood ? 'var(--studio-pos)' : 'var(--studio-neg)';
      return '<div class="math-row">'
        + '<div style="display:flex;justify-content:space-between;align-items:baseline;">'
        + '<span class="math-row-label">'+label+'</span>'
        + '<span class="math-row-value" style="color:'+dcol+';">'
        + (delta !== null ? (delta >= 0 ? '+' : '') + (kind==='num' ? delta.toFixed(2) : (delta*100).toFixed(1)+'pp') : '—')
        + '</span></div>'
        + '<div style="display:flex;gap:1rem;font-size:10px;color:var(--studio-ink-faint);">'
        + '<span>Bot: '+(bv!==null ? (kind==='num'?bv.toFixed(2):(bv*100).toFixed(1)+'%') : '—')+'</span>'
        + '<span>Held: '+(hv!==null ? (kind==='num'?hv.toFixed(2):(hv*100).toFixed(1)+'%') : '—')+'</span>'
        + '</div></div>';
    }).join('');
    var el = document.getElementById('detail-risk-profile');
    if (el) el.innerHTML = html;
  });
```

---

### Summary of Backend Changes Required (small, all in existing files)

| Change | File | Type | Tier |
|---|---|---|---|
| Add `volatility` to `compute_quantstats_metrics()` | `analytics.py` | 1-line quantstats call | 1 — immediately computable |
| Add `vol_bot` / `vol_held` to `meta.portfolio` dict | `app.py` index route | derive from existing shadow_history series | 1 — immediately computable |
| Add `upside_capture` / `downside_capture` to metrics dict | `analytics.py` | requires SPY return fetch + alignment | 2 — needs benchmark data feed |

For Tier 2: when benchmark data is added, it should flow through the same `/api/performance` response shape by adding new keys to `live_metrics` and `shadow_metrics`. The table placeholder rows are already designed to accept them — no template change needed when the data arrives, only the JS `METRIC_LABELS` array toggle from `placeholder` to active.

---

### Design Token and Component Constraints to Honor

All changes must:
1. Use only `--studio-*` custom properties — no bare hex values in any selector or rule block outside `tokens.css`.
2. Light and dark themes must work — do not hard-code colors that only work in light mode.
3. Use existing component classes (`headline-stat`, `vs-row`, `math-row`, `metrics-tbl`, `seg-control`) — do not invent new component names for patterns already defined.
4. `data-testid` attributes are required on every new interactive element and on every data-bearing cell — tests select by them.
5. Do not touch `static/tokens.css`, `static/layout.css`, `static/tweaks.css`, `static/tweaks.js`, `templates/_chrome.html`.
6. Responsive: the `headline-strip` is already a 4-column grid with `minmax(0, 1fr)` — the new stat columns will fill it without change. The vs-rows block in the hero is flex column — the new row appends without layout change.
7. The density system (`--studio-space-*` tokens, `[data-density="compact|roomy"]`) is global — do not hard-code spacing values that override density tokens.

---

## 4. Coordination Note for empirical-auditor

I sent an alignment request to empirical-auditor before finalizing this document. If empirical-auditor confirms any additional computable metrics (e.g., a Tier 2 benchmark series actually already exists in the codebase under a name I missed), this design accommodates them: the metrics table's METRIC_LABELS array is designed as an ordered list that can be extended. Benchmark placeholder rows switch from gray to live by removing the `data-unavail` attribute — no structural redesign needed.

---

**End of UX Design Deliverable**
