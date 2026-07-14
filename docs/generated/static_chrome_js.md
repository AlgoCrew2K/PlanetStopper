# static/chrome.js

> Client-side controller for the shared header chrome (`templates/_chrome.html`) — the only JS asset loaded on all 4 dashboard screens (index, AI Advisor, History, Performance).

**Source:** `static/chrome.js`
**Last updated:** 2026-07-12 (candidate-alert cycle: new candidate-alert header badge — `fetchCandidateAlert()` + `markCandidateAlertViewed()` — see `feature-plans/candidate-alert.md`. First generated-docs page for this file.)

## Overview

`static/chrome.js` runs on every page that includes `templates/_chrome.html` (`index.html`, `ai_advisor.html`, `history.html`, `performance.html`). Because it is the one JS asset common to all 4 screens, any indicator that must be "always visible regardless of the screen I'm on" is wired here rather than in `static/index.js` (which loads on the dashboard root only). Responsibilities:

- **CSRF token bootstrap** — fetches `/api/csrf-token` once on `DOMContentLoaded`, stores it in module-scope `_chromeCsrfToken` for every CSRF-protected `fetch()` call in this file.
- **Force-run button** — POSTs `/api/trigger`, shows a transient "Running…"/"Triggered!"/"Error" label, and calls `window.loadState()` (when present — `index.html` only) to refresh immediately.
- **Workspace switcher** — populates the account dropdown from `/api/accounts`, exposes the active account as `window.activeAccount` for `index.js`'s `loadState()` to read.
- **Emergency Liquidate modal** — confirmation-phrase-gated (`LIQUIDATE`) panic modal; POSTs `/api/sell_account`. Escape key closes it.
- **Engine status (Live / Stale / Closed)** — single three-state indicator, updated only on poll (no per-second counters that can look frozen).
- **NEXT run countdown** — ticks down every second locally, resynced from `data.next_run_seconds` on each `/api/state` poll.
- **Candidate-alert header badge (new this cycle)** — see below.
- **ET clock** — `America/New_York` wall-clock display, ticks every second.

## API Reference

### CSRF bootstrap

```js
var _chromeCsrfToken = null;
```
Fetched once via `GET /api/csrf-token` on `DOMContentLoaded`. Every subsequent CSRF-protected POST in this file (`forceRun`, `submitPanicLiquidation`, `markCandidateAlertViewed`) sends it as the `X-CSRF-Token` header, falling back to `''` if the fetch hasn't resolved yet (that request would 403; there is no retry queue).

### `forceRun(e)`
Handler for `[data-testid="force-run-btn"]`. Disables the button, POSTs `/api/trigger`, shows a 2 s transient status label, then calls `window.loadState()` if defined.

### Workspace switcher
`window.activeAccount`, `switchAccount(uuid)`, `toggleAccountDropdown(e)`, `closeAccountDropdown()`, `escHtml(str)`. Populates `#ws-dropdown` from `GET /api/accounts` on first open; closes on an outside click.

### Emergency Liquidate modal
`openPanicModal()`, `closePanicModal()`, `submitPanicLiquidation()`. The confirm button stays `disabled` until the phrase input exactly equals `LIQUIDATE`. `submitPanicLiquidation()` POSTs `/api/sell_account` with `{account_id, confirm_account_id, confirm_phrase}`.

### `updateEngineStatus(data)` / `updateChromeTicker(data)`
`updateEngineStatus` sets `#engine-status-dot` / `#engine-status-label` to one of Live (green) / Stale (amber, >2 min since `last_successful_cycle_at`) / Closed (dim). `updateChromeTicker` is a compatibility alias that also calls `resyncNextCountdown(data)` — kept so existing `index.js` callers of `updateChromeTicker` continue to work unchanged.

### NEXT countdown
`resyncNextCountdown(data)` reads `data.next_run_seconds` (top-level integer; a `typeof` check, not a truthiness check, since `0` is a valid value) or falls back to parsing `meta.next_run` (`"MM:SS"`). `_tickNextCountdown()` decrements the cached value every second via `setInterval`; hidden when the market is closed.

### Candidate alert (header badge for new weekly-suggestion survivors)

**New this cycle** — `feature-plans/candidate-alert.md` AC-1..AC-6. Lives in `chrome.js`, not `index.js`, specifically because this is the one JS asset shared by all 4 screens (loaded via `_chrome.html`'s `<script src="/static/chrome.js">` on every page) — placing it in `index.js` would only surface the badge on the dashboard root.

#### `fetchCandidateAlert()`
Fetches `GET /api/candidate-alert`. On a non-OK response or any network error, does nothing — the badge/tooltip stay at their last-known state (AC-6, honest degradation; no error UI). On success:

| Element | Behavior |
|---------|----------|
| `#candidate-alert-badge` | `data.new_valid_count > 0` → `textContent` = the count, `display: inline-block`. Otherwise → cleared and `display: none` (badge hidden at zero, per AC-2). |
| `#candidate-alert-indicator` (`title` attribute) | `data.last_run` present → `"Weekly run <ran_at>: <evaluated> evaluated, <survivors> passed the gate"`. Absent (`null`) → `"No weekly run yet"` (AC-3 honest empty state). |

Exposed as `window.fetchCandidateAlert` (test seam / manual re-trigger).

**Poll cadence:** `CANDIDATE_ALERT_POLL_INTERVAL_MS = 30000` — matches `index.js`'s `POLL_INTERVAL_MS`, comfortably above the dashboard's 15 s minimum refresh floor. Fires once on `DOMContentLoaded` and then every 30 s via `setInterval`.

#### `markCandidateAlertViewed()`
POSTs `/api/candidate-alert/mark-viewed` with the `X-CSRF-Token` header and `keepalive: true` — the `keepalive` flag lets the request complete in the background even though the browser is about to navigate away (the click also triggers the indicator's native `<a href="{{ url_for('ai_advisor_tab') }}">` navigation). Best-effort: any failure is silently swallowed — a failed mark-viewed call must never block or interfere with the navigation itself (AC-4 works with JS disabled at all, since the link is a real server-rendered `<a href>`). Exposed as `window.markCandidateAlertViewed`.

#### Wiring (`DOMContentLoaded`)
```js
document.addEventListener('DOMContentLoaded', function () {
  fetchCandidateAlert();
  setInterval(fetchCandidateAlert, CANDIDATE_ALERT_POLL_INTERVAL_MS);
  var indicator = document.getElementById('candidate-alert-indicator');
  if (indicator) indicator.addEventListener('click', markCandidateAlertViewed);
});
```

### `updateClock()`
Renders `[data-testid="et-clock"]` as `HH:MM:SS` in `America/New_York`. Ticks every second; silently no-ops in browsers lacking `timeZone` support.

## Constants

| Constant | Value | Description |
|----------|-------|--------------|
| `CANDIDATE_ALERT_POLL_INTERVAL_MS` | `30000` | Poll interval (ms) for `fetchCandidateAlert()`. |

## Internal Dependencies

- `GET /api/csrf-token` — CSRF token bootstrap
- `POST /api/trigger` — force-run button
- `GET /api/accounts` — workspace switcher + panic-modal account select
- `POST /api/sell_account` — emergency liquidate
- `GET /api/candidate-alert` / `POST /api/candidate-alert/mark-viewed` — candidate-alert header badge (see [app](app.md))
- `window.loadState` — optional cross-file call into `static/index.js` (present on `index.html` only; guarded with `typeof === 'function'`)

## Consumed By

- `templates/_chrome.html` — the shared header partial that renders `#candidate-alert-indicator`/`#candidate-alert-badge`, `#engine-status`, `#next-run-countdown`, `#ws-wrap`, `#panic-modal`, and includes `<script src="/static/chrome.js">`. Included by all 4 screens (`index.html`, `ai_advisor.html`, `history.html`, `performance.html`).
