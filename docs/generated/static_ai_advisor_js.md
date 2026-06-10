# static/ai_advisor.js

> Client-side logic for the AI Advisor single-page SPA: in-place tab switching, suggestion card rendering with per-symphony assessment, accept/reject lifecycle, autotune run feed, and symphony selection.

**Source:** `static/ai_advisor.js`
**Last updated:** 2026-06-10

## Overview

`ai_advisor.js` is the browser-side controller for the unified `/ai-advisor` SPA. It runs as an IIFE with `'use strict'`. All colors are CSS custom properties (`--studio-*`) resolved at runtime — no bare hex values. No Tailwind class names.

Key responsibilities:

- **In-place tab switching** (`initTabSwitcher`) — wire `[role="tab"][data-tab]` buttons to show/hide `[role="tabpanel"][data-tab]` panels without any page navigation. ARIA `aria-selected` is maintained on tab buttons. Matches the `.active-toggle` pattern used in `static/index.js`.
- **Suggestion card rendering** (`renderSuggestions`) — renders suggestion cards with confidence rings, four-gates verdict badges, projected-impact bars, OOS status, and accept/reject/chat buttons. When the suggestions list is empty, renders the per-symphony assessment block from `body.assessment` instead of a generic placeholder.
- **CSRF token** — fetches `GET /api/csrf-token` on `DOMContentLoaded`; all POST requests include `X-CSRF-Token: _csrfToken`.
- **Autotune run feed** (`loadRecentRuns`) — polls `GET /api/autotune-runs` every 15 seconds; renders run cards with decision pills, Sortino/selection t-stat, and frozen-eval verdict; renders a Chart.js sparkline of historical Sortino values.
- **Symphony selection** (`loadSymphonies`) — populates the `#symphony-id-input` select from `GET /api/performance/symphonies`; fires `getSuggestions` automatically on select change.

## API Reference

### `initTabSwitcher()` (IIFE, called on DOMContentLoaded)

Wires all `[role="tab"][data-tab]` elements to their matching `[role="tabpanel"][data-tab]` elements. Clicking a tab button calls `activateTab(btn)` which:

1. Sets `aria-selected="true"` on the clicked button and `"false"` on all others.
2. Adds/removes the `active` CSS class on tab buttons.
3. Adds `tab-panel--active` class to the matching panel; removes it from all others.

The initially-active tab is determined by the button with `aria-selected="true"` in the server-rendered HTML.

---

### `window.getSuggestions()`

Reads the selected symphony from `#symphony-id-input`, POSTs to `/ai-advisor/suggest` with CSRF token, then calls `renderSuggestions(body.suggestions, symphonyId, body)`. The full response body is passed so the assessment block is available for the empty-suggestions case.

Disables the `#get-suggestions-btn` during the fetch; re-enables only if a symphony is still selected (B-14 rule).

---

### `renderSuggestions(suggestions, symphonyId, body)`

Renders suggestion cards into `#suggestions-container`.

**Empty-suggestions path (per-symphony assessment):** When `suggestions.length === 0`, renders an assessment block from `body.assessment` (added 2026-06-10). The block shows:
- `assessment.summary` — a human-readable string explaining the tuning state (no Optuna run / all trials FDR-rejected / validated edge found).
- `assessment.baseline_decision` — the autotuner's decision for this symphony.
- `assessment.oos_alpha` and `assessment.fallback_oos_alpha` — numeric OOS values.

This differentiates the empty-state per symphony; previously all symphonies showed the same generic message.

**Populated path:** Each suggestion card includes:
- Confidence ring (SVG arc; `high` = 100% fill, `medium` = 60%, `low` = 30%)
- Four-gates verdict badges: `allowlist`, `risk_direction`, `oos_frozen_eval`, `locked_vars` (pass/fail coloring via `--studio-pos`/`--studio-neg`)
- Projected-impact bar (SVG; width proportional to `|delta| * 20`, capped at 100%)
- Current → suggested value display
- OOS status + reason
- Accept/Dismiss buttons (Accept disabled for OOS-rejected suggestions)
- "Chat about this" button — constructs a `cfgArtifact` JSON blob and calls `openChatPanel(artifact)` if defined, otherwise falls back to `/ai-advisor/chat`

---

### `window.acceptSuggestion(index, symphonyId)`

POSTs to `/ai-advisor/accept` with the suggestion at `index` from `container._suggestions`. On acceptance, replaces the card HTML with a confirmation message. On C2-gate rejection, shows an alert with the gate error.

---

### `window.rejectSuggestion(index, symphonyId)`

POSTs to `/ai-advisor/reject`. Replaces the card HTML with a "Rejected." message.

---

### `loadRecentRuns()`

Fetches `GET /api/autotune-runs`; renders run cards into `#autotune-runs-list`. Each card shows:
- Symphony name (truncated with title tooltip)
- Decision pill (Apply / Reject / Fallback / Hold / Skip / Pending; uses `DECISION_LABELS` map and `decisionPillColor`)
- Timestamp, Sortino (labelled `naive_sharpe`), and selection t-stat (Harvey & Liu haircut winner)
- Frozen-eval verdict pill

Calls `renderAutotuneSparkline(rows)` to draw a Chart.js line chart of historical Sortino values. Falls back to `--studio-swatch-1` if `--studio-accent` resolves empty (C-15 no-bare-hex rule).

Null guard: if `rows` is falsy or empty, renders a "No tuning runs recorded yet" placeholder.

---

### `loadSymphonies()`

Fetches `GET /api/performance/symphonies`, populates `#symphony-id-input` options. Preserves the previously-selected value if it is still in the returned list. On select `change`, calls `syncRunBtn()` and auto-fires `getSuggestions()` if a value is selected (C-11 wire).

## Internal Dependencies

- `GET /api/csrf-token` — CSRF token fetch
- `POST /ai-advisor/suggest` — suggestion fetch
- `POST /ai-advisor/accept` — suggestion acceptance
- `POST /ai-advisor/reject` — suggestion rejection
- `GET /api/autotune-runs` — autotune run history feed
- `GET /api/performance/symphonies` — symphony list
- `Chart.js` (global) — autotune sparkline; guarded by `typeof Chart === 'undefined'` check
- `openChatPanel` (global, optional) — chat slide-in panel; defined in the SPA template's inline script; falls back to navigation if absent
- CSS custom properties: `--studio-pos`, `--studio-neg`, `--studio-warn`, `--studio-accent`, `--studio-ink`, `--studio-ink-dim`, `--studio-surface`, `--studio-border`, `--studio-chip-bg`, `--studio-white`, `--studio-surface-raised`, `--studio-rule`, `--studio-swatch-1`
