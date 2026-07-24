# static/performance.js

> Client-side logic for the read-only Performance tab: fetches aggregate/per-symphony return series + quantstats metrics, renders a cumulative-return Chart.js curve and a 7-metric comparison table, and drives the scope/window/symphony picker controls.

**Source:** `static/performance.js`
**Last updated:** 2026-07-23 (guard-alpha-preconditions, `DE-GUARD-ALPHA-PRECONDITIONS-001` -- new Guard-Alpha Stop-Justification Preconditions panel: `fetchGuardAlphaPreconditions()` + `renderGuardAlphaPreconditions()` render `GET /api/guard-alpha-preconditions`'s per-symphony verdict table; see the new section below and `docs/generated/app.md`). Prior: 2026-07-20 (fix-f023-perf-view, `DE-PERFVIEW-ID-MISMATCH`, F-023 -- first doc-gen entry for this file. `loadSymphonies()` and `renderBanner()` updated for the `GET /api/performance/symphonies` `{id,name}` contract fix -- see `docs/generated/app.md`'s `GET /api/performance/symphonies` section for the route-side root cause and fix.)

## Overview

`performance.js` is the browser-side controller for `templates/performance.html` (the `/performance` route). It runs as an IIFE with `'use strict'`. All colors are CSS custom properties (`--studio-*`) resolved at runtime -- no bare hex values, except a documented fallback tuple inside `hexToRgba()`.

Key responsibilities:

- **Data fetch + orchestration** (`refresh()`) -- reads the current scope/window/symphony picker state, fetches `GET /api/performance`, and fans the response out to the chart, metrics table, headline stats, observation-count caption, and insufficient-history banner renderers.
- **Symphony picker population** (`loadSymphonies()`) -- fetches `GET /api/performance/symphonies` and populates `#symphony-picker` with one `<option>` per live symphony.
- **Cumulative-return chart** (`renderChart()`) -- Chart.js line chart with two series (live if-held vs. Planet Stopper-exited), a custom endpoint-label plugin, and no built-in legend (endpoint labels substitute for it).
- **7-metric quantstats table** (`renderMetrics()`) -- Sharpe/Sortino/Max Drawdown/Calmar/Volatility/Total Return/CAGR/Win Rate rows plus two Tier-2 SPY-benchmark placeholder rows (`upside_capture`/`downside_capture`, always rendered as "not yet available" -- no data feed exists yet).
- **Headline stats strip** (`renderHeadlineStats()`) -- guard-alpha delta (total-return spread), Sharpe delta, Sortino delta, max-drawdown reduction.
- **Insufficient-history / unrecognized-id banner** (`renderBanner()`) -- shared banner element now branches on two independent conditions (AC-4, see below).
- **Pick-a-symphony empty state** (`showPickSymphonyState()`/`hidePickSymphonyState()`) -- when `scope=symphony` and no symphony is selected, hides the chart/metrics/headline blocks and shows a dedicated placeholder instead of firing a doomed fetch.
- Auto-refresh floor is 60s, wired in the `DOMContentLoaded` handler (`setInterval(refresh, 60000)`) -- the aggregate series derives from `shadow_history`'s daily portfolio returns, which move at most once per engine cycle; polling faster than that only burns CPU re-parsing near-identical JSON, and stays well above the 15s live-cycle floor other tabs use.

## API Reference

### `refresh()`

Reads `currentParams()` (scope/days/symphony_id), and if `scope === 'symphony'` with no symphony selected, calls `showPickSymphonyState()` and returns without fetching. Otherwise calls `hidePickSymphonyState()` and fetches `GET /api/performance?<qs>`, then on success calls, in order: `renderObsCount`, `renderHeadlineStats`, `renderBanner`, `renderChart`, `renderMetrics`. A non-OK HTTP status or a fetch failure logs to `console.error` and leaves the previous render in place (no partial/broken re-render).

---

### `currentParams()`

Reads the active `scope-toggle` and `days-picker` segmented-control values plus `#symphony-picker`'s selected value, and builds the `GET /api/performance` query string. `symphony_id` is appended (URL-encoded) only when `scope === 'symphony'` and a value is selected.

`resolveDays(raw)` passes through every numeric window token unchanged; the literal string `'ytd'` is also passed through unchanged (AC-5/MAPERF-03 cross-plan correction -- the server resolves `'ytd'` to a Jan-1 calendar cutoff via `analytics._window_cutoff_date`, the same helper the dashboard hero-chart/strip picker uses for the same token; the six numeric buttons stay deliberate TRADING-day counts).

---

### `renderChart(payload)`

Compounds `payload.live_returns` (if-held) and `payload.shadow_returns` (Planet Stopper-exited) via `cumulative()` (percentage-point daily returns -> a cumulative percentage curve) and renders/updates a Chart.js line chart on `#returns-chart`. Registers `endpointLabelPlugin` once (a custom `afterDraw` plugin drawing a filled pill (bot) and hollow rect (live) value callout at the right edge of each series, substituting for a legend). Series values are percentage points (e.g. `-0.56` = `-0.56%`), consistent with the `/api/performance` route's documented field semantics.

---

### `renderMetrics(payload)`

Renders the `#metrics-tbody` 7-metric-plus-2-placeholder table from `payload.live_metrics`/`payload.shadow_metrics` (quantstats dicts). Derives two Tier-1 fields client-side before rendering (`ux-design-deliverable.md` §Change 2 sign conventions):

- `max_drawdown_delta = abs(live.max_drawdown) - abs(shadow.max_drawdown)` -- positive means the bot had a shallower drawdown.
- `volatility_delta = live.volatility - shadow.volatility` -- positive means the bot was calmer.

Per-row delta coloring honors an `invert` flag (`METRIC_LABELS` tuple, AC-7/MAPERF-05) for lower-is-better metrics (volatility) -- the displayed value and arrow direction are unchanged by `invert`, only the color decision flips (mirrors `static/index.js`'s Risk Profile panel `invertDelta`). `TIER2_PLACEHOLDER_KEYS` (`upside_capture`/`downside_capture`) always render via `placeholderRow()` -- an explicit "not yet available" dash, never a fabricated number, since no SPY/TQQQ benchmark data feed exists yet.

---

### `renderBanner(payload)`

Controls the shared `#insufficient-banner` element's visibility and content.

**AC-4 unrecognized-symphony-id distinction (F-023, `DE-PERFVIEW-ID-MISMATCH`, 2026-07-20):** the banner's original Jinja-rendered "Insufficient history" markup is captured once into the module-level `_defaultBannerHtml` on first call. Each render then branches on `payload.symphony_id_recognized === false` (see `docs/generated/app.md`'s `GET /api/performance`'s `symphony_id_recognized` field):

```javascript
var unrecognized = payload.symphony_id_recognized === false;
banner.innerHTML = unrecognized
    ? '<strong>Strategy not recognized.</strong> This symphony ID isn\'t known — the picker may be showing a stale value.'
    : _defaultBannerHtml;
banner.style.display = (unrecognized || payload.insufficient_history) ? '' : 'none';
```

`symphony_id_recognized` is only present on `scope=symphony` responses (never on `scope=aggregate`), so `unrecognized` is always `false` there and the banner falls back to the original insufficient-history behavior unchanged. This closes the F-023 defect where a broken symphony_id and a genuinely sparse-data symphony rendered the identical banner, hiding a dead capability behind an honest-looking empty state.

---

### `loadSymphonies()`

Fetches `GET /api/performance/symphonies`, populates `#symphony-picker` with one `<option>` per entry (first entry pre-selected). An empty list renders a single disabled "No symphonies in history yet" placeholder option -- never a crash or a silently-empty select.

**F-023 fix (`DE-PERFVIEW-ID-MISMATCH`, 2026-07-20):** each option's `value` is now `sym.id` (the bot_state hash `shadow_history.symphony_id` actually stores) and its `textContent` is `sym.name` (the display label) -- previously both were set to the same bare NAME string returned by the pre-fix endpoint, so the value sent onward as `symphony_id` in `currentParams()` never matched any `shadow_history` row. This is the root cause the whole F-023 cycle fixes; see `docs/generated/app.md`'s `GET /api/performance/symphonies` section for the endpoint-side root cause.

---

### `showPickSymphonyState()` / `hidePickSymphonyState()`

Toggle a dedicated `#pick-symphony-state` placeholder (lazily created on first call) against the chart/metrics/headline-strip blocks (`[data-testid="perf-chart-block"]`/`[data-testid="metrics-table"]`/`[data-testid="headline-strip"]`). Entered when `scope === 'symphony'` and no symphony is selected -- prevents `refresh()` from firing a `symphony_id`-less fetch that would 400.

---

### `wireUI()`

Wires the `scope-toggle` and `days-picker` segmented controls (`wireSegControl`) and the `#symphony-picker` `change` event, each triggering `refresh()`. Toggling scope away from `'symphony'` also calls `hidePickSymphonyState()` so a stale placeholder never lingers after switching to the aggregate view. Calls `syncSymphonyVisibility()` once up front to hide/show the symphony-picker wrapper based on the initial scope.

---

### `cumulative(returns)` / `fmt(value, kind)` / `fmtDelta(live, shadow, kind)` (internal helpers)

- `cumulative()` compounds an array of percentage-point daily returns into a cumulative percentage curve (`acc *= 1 + r/100` per step).
- `fmt()` formats a raw metric value per its `kind` tag (`pct_frac`/`frac`/`pp`/`pct`/plain number); returns `'—'` for `null`/`undefined`/non-finite/`|value| > 1000` (defensive against a corrupt or NaN producer value reaching the DOM).
- `fmtDelta()` formats `shadow - live` with a directional arrow (`↑`/`↓`), same `'—'` guards as `fmt()`.

---

### Guard-Alpha Stop-Justification Preconditions Panel (guard-alpha-preconditions, `DE-GUARD-ALPHA-PRECONDITIONS-001`, 2026-07-23)

Independent panel, independent fetch/render cycle, and independent DOM subtree from the rest of this file -- reads `GET /api/guard-alpha-preconditions` (see `docs/generated/app.md`), a THEORETICAL PRECONDITION read distinct from the `renderHeadlineStats()` guard-alpha $-saved delta above.

#### `fetchGuardAlphaPreconditions()`

Fetches `/api/guard-alpha-preconditions`, calls `renderGuardAlphaPreconditions(data)` on success. **401-guarded**: `if (!response.ok) return;` short-circuits before `.json()` -- an unauthenticated response never reaches the renderer. Failures are silent (`.catch()` no-op) -- the panel keeps its last-known state rather than clearing on a transient error, same convention as `fetchGuardAlphaSummary()` in `static/index.js`. Called once on `DOMContentLoaded` and folded into the existing 60s `refresh()` poll (no new `setInterval` timer).

#### `renderGuardAlphaPreconditions(data)`

Clears `#guard-alpha-preconditions-tbody` and rebuilds it from `data.symphonies` (a `{symphony_id: {replay, shadow}}` map). **Panel-level empty state (AC-7):** when there are zero symphonies, or none has at least one sample that cleared `INSUFFICIENT_DATA` (`_preconditionEntryIsUsable`), shows `#guard-alpha-preconditions-empty-state` and renders no rows -- never an empty table with no explanation. Per symphony: the "replay" sample renders via `preconditionUnavailableRowEl()` (a friendlier "replay sample unavailable — populates after the next autotune run" message, `PRECOND_REPLAY_UNAVAILABLE_MESSAGE`) specifically when it is a cold-cache degrade (`verdict === 'INSUFFICIENT_DATA' && n_obs === 0`) rather than the generic verdict-chip row -- AC-8's cold-cache case gets its own honest copy instead of looking like a thin-but-real sample. The "shadow" sample always renders via the standard `preconditionRowEl()`.

#### `preconditionRowEl(symphonyId, sampleLabel, row)` / `preconditionUnavailableRowEl(symphonyId, sampleLabel)` / `preconditionChipEl(verdict)` / `preconditionNumCell(value, digits, prefix)` (internal helpers)

Build one `<tr>`/cell/chip via **DOM APIs only** (`createElement`/`textContent`) -- never `innerHTML` with interpolated symphony names or API response strings (XSS hygiene: symphony identifiers are external-origin, from Composer). `preconditionChipEl` maps a verdict string to its `.precond-verdict-chip--<modifier>` CSS class via the `PRECOND_VERDICT_CHIP_CLASS` table (falls back to the insufficient-data modifier for an unrecognized verdict), mirroring `ai_advisor.js`'s sentiment-chip BEM pattern. `preconditionNumCell` renders `'--'` for `null`/`undefined`, else `Number(value).toFixed(digits)` with an optional prefix (e.g. `'±'` for the CI column).

## Types

- **`METRIC_LABELS`** -- array of `[key, label, kind, isPrimary, invert?]` tuples driving `renderMetrics()`'s row order and formatting; order defines rendering order, with risk-adjusted metrics leading per `ux-design-deliverable.md` §2.1 (capital preservation ranks above return).
- **`TIER2_PLACEHOLDER_KEYS`** -- `['upside_capture', 'downside_capture']`, the two benchmark metrics with no live data feed.

## Internal Dependencies

- `GET /api/performance` -- primary data fetch; response fields consumed: `dates`, `live_returns`, `shadow_returns`, `live_metrics`, `shadow_metrics`, `observation_count`, `window_days`, `insufficient_history`, and (scope=symphony only) `symphony_id_recognized` (AC-4, `DE-PERFVIEW-ID-MISMATCH`)
- `GET /api/performance/symphonies` -- symphony picker population; `{id, name}` objects (F-023, `DE-PERFVIEW-ID-MISMATCH`, was bare name strings) -- see `docs/generated/app.md`
- `GET /api/guard-alpha-preconditions` -- Stop-Justification Preconditions panel fetch; response fields consumed: `symphonies.<id>.replay`/`.shadow` (each `{rho, rho_ci, sharpe_daily, n_obs, verdict, sample_source}`) (`DE-GUARD-ALPHA-PRECONDITIONS-001`) -- see `docs/generated/app.md`
- `Chart.js` (global) -- cumulative-return line chart
- CSS custom properties: `--studio-accent`, `--studio-ink-dim`, `--studio-pos`, `--studio-neg`, `--studio-paper`, `--studio-ink-faint`, `--studio-rule`
- DOM elements (from `templates/performance.html`): `#returns-chart`, `#metrics-tbody`, `#insufficient-banner`, `#symphony-picker`, `#symphony-picker-wrapper`, `#scope-toggle`, `#days-picker`, `#obs-caption`, `#guard-alpha-value`, `#sharpe-delta-value`, `#sortino-delta-value`, `#mdd-reduction-value`, `[data-testid="perf-chart-block"]`, `[data-testid="metrics-table"]`, `[data-testid="headline-strip"]`, `#guard-alpha-preconditions-tbody`, `#guard-alpha-preconditions-empty-state`, `[data-testid="guard-alpha-preconditions-panel"]`
