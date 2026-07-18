# static/index.js

> Client-side dashboard controller: state polling, event-driven updates via SSE, guard-alpha panel, window-picker, visible staleness cue, and honest post-trigger MC rendering.

**Source:** `static/index.js`
**Last updated:** 2026-07-18 (Math Remediation F7, `DE-MATH-F7-001`) — MC dial + detail-view chart-fallback exited-state render honesty (AC-2); see the new section below. Prior: 2026-06-23 (feat/dashboard-realtime-push: EventSource SSE wiring + showConnectionLost staleness cue)

## Overview

`static/index.js` is the browser-side controller for the operator dashboard (`/`). It runs as an IIFE. Key responsibilities:

- **State polling** — `loadState()` fetches `/api/state` and calls `updateDashboard(data)` on success. Fires once on `DOMContentLoaded` and then on a 30 s `setInterval` as the resilience fallback.
- **Event-driven updates (primary path)** — subscribes to `/api/events` via the `EventSource` API. On a `cycle-complete` event, calls `loadState()` immediately so the dashboard reflects the engine's just-completed cycle without waiting for the next 30 s poll tick.
- **Staleness cue** — `showConnectionLost()` flips the engine badge and `data-as-of` element to a visible error state when `loadState()` fails, so the operator knows displayed numbers are frozen rather than silently stale.
- **Guard-alpha dollar-saved panel** — `fetchGuardAlphaSummary()` calls `/api/guard-alpha-summary` once on page load and populates the `#dollar-saved-headline` / `#guard-event-count` / `#dollar-saved-basis-label` elements.
- **Window picker** — `fetchWindowedStrip(token)` calls `/api/strip/<token>` when the operator clicks a time-window button; re-windows the hero headline and comparison rows without a full page reload.
- **MC dial + detail render honesty** — `renderMcDial()` and the detail-view Risk Math panel actively render an explicit exited/"—" state for a triggered symphony instead of scanning history for (or freezing on) a stale pre-trigger reading (Math Remediation F7, `DE-MATH-F7-001`).

## API Reference

### `loadState()`
Fetches `GET /api/state` and calls `updateDashboard(data)` on success. On any fetch or JSON-parse error, calls `showConnectionLost()` and logs to console.

Called on:
- `DOMContentLoaded` (initial load)
- Every `POLL_INTERVAL_MS` (30 000 ms) via `setInterval` (resilience fallback)
- Every `cycle-complete` SSE event (primary path)

### `showConnectionLost()`
Visible staleness cue (AC-8). Targets three real DOM element IDs (from `_chrome.html:51-53` and `index.html:846`):
- `#engine-status-dot` — background color set to `'var(--studio-neg, #e33)'` (red dot).
- `#engine-status-label` — text set to `'Connection Lost'`.
- `#hero-data-as-of` — text set to `'connection lost'`.

Called by `loadState()` on fetch failure. Does NOT suppress or replace the poll retry — `setInterval` continues, so the badge self-heals on the next successful poll.

**Prior defect (fixed this cycle):** The original implementation targeted `#engine-status-badge` and `[data-testid="data-as-of"]`/`.data-as-of` — selectors that do not exist in the production template. `getElementById` returned `null` for all three; the staleness cue was silently a no-op: the operator saw no visual indication of a dropped connection.

### SSE subscription (DOMContentLoaded block)
```js
if (typeof EventSource !== 'undefined') {
    var _es = new EventSource('/api/events');
    _es.addEventListener('cycle-complete', function () { loadState(); });
    _es.onerror = function () { /* silent — poll fallback handles reconnect */ };
}
```

Registered alongside the `setInterval` in the `DOMContentLoaded` callback. Behavior:
- **Primary update path:** `cycle-complete` fires within ~1 s of engine subprocess exit; `loadState()` fetches fresh `/api/state` before the next 30 s poll would fire.
- **Auth failure:** if `/api/events` returns 401, `EventSource.onerror` fires silently; the 30 s poll continues as the sole update path.
- **Connection drop / daemon restart:** `EventSource` retries automatically (browser built-in retry with exponential backoff). During the reconnect window the 30 s poll keeps state live.
- **Unsupported browser:** the `typeof EventSource !== 'undefined'` guard skips SSE entirely; the existing poll is the only update path. No visual breakage.

### `fetchGuardAlphaSummary()`
Fetches `GET /api/guard-alpha-summary` once on `DOMContentLoaded`. Populates:

| Element | Field |
|---------|-------|
| `#dollar-saved-headline` | `data.cumulative_saved_dollars` (formatted `$X.XX`) or `'No guard events yet'` |
| `#guard-event-count` | `data.guard_event_count` |
| `#dollar-saved-basis-label` | `data.basis_label` |

Non-200 responses are silently ignored (advisory-only display). Does NOT clobber `#guard-alpha-headline`, which is owned by the windowed strip path.

### `fetchWindowedStrip(token)`
Fetches `GET /api/strip/<token>` for a time-window button click. Re-windows the hero guard-alpha headline and the comparison rows (Bot / Held / Delta) by wrapping the strip dict as a pseudo-poll payload and calling `renderGuardAlpha` and `updateComparisonRows`. Errors are logged to console and silently swallowed (the dashboard retains its prior state on strip failure).

### `renderMcDial(symId, chartData) → void`
Renders the SVG MC dial (arc + percentage text) for every `[data-testid="mc-dial"][data-sym-id="<symId>"]` element on the page (`:852`).

**Exited/triggered branch (`:859`, Math Remediation F7, `DE-MATH-F7-001` AC-2):** when `chartData.triggered` is truthy, actively renders the honest exited state — arc `stroke-dashoffset` set to full circumference (`MC_CIRCUMFERENCE`) in a faint color, text set to `'—'` — and returns immediately, before the non-triggered branch's history scan runs. This is deliberate, not a fallback: a null-scan cannot distinguish "no data yet" from "exited", and would otherwise resurrect the last real pre-trigger `mc_prob` as if it were current.

**Non-triggered branch (unchanged logic, now only reached when `!chartData.triggered`):** scans `chartData.data` for the last non-null `mc_prob` (0-100 scale), falling back to top-level fields, and colors the arc by band (`< 15` warn, `> 80` dim, else accent).

**Two call sites, both now pass `triggered` explicitly:**
- The per-symphony chart fetch handler (`:334`) threads `data.triggered = sym.triggered` onto the fetched chart payload before calling `renderMcDial(sym.id, data)`.
- The poll-path `updateDashboard` loop (`:1075`) calls `renderMcDial(id, { triggered: sym.triggered, mc_prob: sym.mc_prob, data: [] })` **unconditionally on every poll** — the pre-F7 code only called this when `sym.mc_prob != null`, which froze the dial at its last-drawn state for an exited symphony (whose `mc_prob` is now honestly `null`) instead of updating it to the exited state.

### Detail-view Risk Math panel (chart-fallback + neutral reset, `DE-MATH-F7-001` AC-2)
Inside the detail-view chart data handler:
- **`mcProb` resolution (`:674`):** short-circuits to `null` when `sym.triggered` is truthy, skipping the backward null-scan over `data` entirely — same rationale as `renderMcDial`'s exited branch (a null-scan on mixed pre/post-trigger history would resurrect a stale reading).
- **`#dp-rm-mc-bar` neutral reset (`:711`):** when the resolved `mcProb` is `null` (Not-triggered-with-no-data OR triggered), the bar width is explicitly set to `'0%'` and its color to `cs('--studio-ink-faint')` — added so the bar never keeps showing a stale width/color from a previous render for a symphony that no longer has a real reading.

## Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `POLL_INTERVAL_MS` | `30000` | Interval for the resilience-fallback state poll (ms). SSE is the primary path; this floor ensures liveness on SSE failure. |
| `MC_CIRCUMFERENCE` | `94.25` (`2 * π * 15`) | SVG arc circumference for the MC dial; used both for the normal probability-scaled arc and the F7 exited-state full-faint-arc render. |

## Update Paths

Two update paths coexist. Both call `loadState()` → `updateDashboard(data)`:

| Path | Trigger | Latency |
|------|---------|---------|
| SSE (primary) | `cycle-complete` event from `/api/events` | ~1 s after engine cycle exit |
| Poll (fallback) | `setInterval` every 30 s | up to 30 s lag |

The SSE path is always attempted first. The poll continues running as an unconditional safety net — it is NOT disabled when SSE is active. On auth failure, SSE silently degrades to poll-only with no visible disruption.

## Internal Dependencies

- `GET /api/state` — primary state source for `loadState()`
- `GET /api/events` — SSE stream for cycle-complete notifications
- `GET /api/guard-alpha-summary` — dollar-saved panel data
- `GET /api/strip/<token>` — windowed strip for the time-window picker
- `GET /api/hero-chart/<token>` — hero chart re-windowing (separate fetch on picker click)
- `GET /api/chart/<sym.id>` — per-symphony chart history, feeding both `renderSparkline` and `renderMcDial`; F7 threads `sym.triggered` onto this payload before the `renderMcDial` call (see above)

## Related Surfaces (not this file)

- **`templates/table_partial.html`** — main-table MC Prob column header tooltip corrected this cycle (`DE-MATH-F7-001` AC-3: "Monte Carlo probability this symphony underperforms its own regime-matched historical baseline", replacing the inaccurate "beats SPY" claim) and the cell body (`:98`) already rendered `"---"` for a `None` `mc_prob` before this cycle — no code change needed there. No dedicated `docs/generated` page exists for this template (matches the existing no-template-page precedent, e.g. `static/ai_advisor_logic_changes.js`); its render contract is documented here and in `INDEX.md`'s module-index prose instead of a standalone file. **CONFIRMED orphaned, not live (f7-dash finding, independently confirmed by f7-review's own call-path falsification):** this template has no live DOM consumer since the card-SPA redesign removed the `morphdom` injector that used to inject its output — a discrepancy with the audit's own premise, which counted the main table as live. F7's tooltip fix there is template-correct regardless but may not currently reach the operator; see `DE-MATH-F7-001`'s backlog items (delete-vs-rewire decision + the unrelated pre-existing dead `openChartModal()` reference at `:170`, both out of F7 scope).
