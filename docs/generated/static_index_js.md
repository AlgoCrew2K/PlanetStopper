# static/index.js

> Client-side dashboard controller: state polling, event-driven updates via SSE, guard-alpha panel, window-picker, and visible staleness cue.

**Source:** `static/index.js`
**Last updated:** 2026-06-23 (feat/dashboard-realtime-push: EventSource SSE wiring + showConnectionLost staleness cue)

## Overview

`static/index.js` is the browser-side controller for the operator dashboard (`/`). It runs as an IIFE. Key responsibilities:

- **State polling** — `loadState()` fetches `/api/state` and calls `updateDashboard(data)` on success. Fires once on `DOMContentLoaded` and then on a 30 s `setInterval` as the resilience fallback.
- **Event-driven updates (primary path)** — subscribes to `/api/events` via the `EventSource` API. On a `cycle-complete` event, calls `loadState()` immediately so the dashboard reflects the engine's just-completed cycle without waiting for the next 30 s poll tick.
- **Staleness cue** — `showConnectionLost()` flips the engine badge and `data-as-of` element to a visible error state when `loadState()` fails, so the operator knows displayed numbers are frozen rather than silently stale.
- **Guard-alpha dollar-saved panel** — `fetchGuardAlphaSummary()` calls `/api/guard-alpha-summary` once on page load and populates the `#dollar-saved-headline` / `#guard-event-count` / `#dollar-saved-basis-label` elements.
- **Window picker** — `fetchWindowedStrip(token)` calls `/api/strip/<token>` when the operator clicks a time-window button; re-windows the hero headline and comparison rows without a full page reload.

## API Reference

### `loadState()`
Fetches `GET /api/state` and calls `updateDashboard(data)` on success. On any fetch or JSON-parse error, calls `showConnectionLost()` and logs to console.

Called on:
- `DOMContentLoaded` (initial load)
- Every `POLL_INTERVAL_MS` (30 000 ms) via `setInterval` (resilience fallback)
- Every `cycle-complete` SSE event (primary path)

### `showConnectionLost()`
Visible staleness cue (AC-8). Flips two DOM elements:
- `#engine-status-badge` — text set to `'Connection Lost'`; CSS class `live` or `stale` replaced with `stale`.
- `[data-testid="data-as-of"]` or `.data-as-of` — text set to `'connection lost'`.

Called by `loadState()` on fetch failure. Does NOT suppress or replace the poll retry — `setInterval` continues, so the badge self-heals on the next successful poll.

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

## Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `POLL_INTERVAL_MS` | `30000` | Interval for the resilience-fallback state poll (ms). SSE is the primary path; this floor ensures liveness on SSE failure. |

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
