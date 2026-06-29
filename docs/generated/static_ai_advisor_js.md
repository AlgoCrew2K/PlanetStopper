# static/ai_advisor.js

> Client-side logic for the AI Advisor single-page SPA: in-place tab switching, suggestion card rendering with per-symphony assessment and lens-cache staleness stamp (AC-3), accept/reject lifecycle, autotune run feed, symphony selection, and Strategy Builder run/chat affordances.

**Source:** `static/ai_advisor.js`
**Last updated:** 2026-06-29 (DE-ADVISOR-LATENCY AC-3: `#advisor-lens-as-of` staleness stamp populated on suggest completion; prior: spa-port cycle 2026-06-13)

## Overview

`ai_advisor.js` is the browser-side controller for the unified `/ai-advisor` SPA. It runs as an IIFE with `'use strict'`. All colors are CSS custom properties (`--studio-*`) resolved at runtime — no bare hex values. No Tailwind class names.

Key responsibilities:

- **In-place tab switching** (`initTabSwitcher`) — wire `[role="tab"][data-tab]` buttons to show/hide `[role="tabpanel"][data-tab]` panels without any page navigation. ARIA `aria-selected` is maintained on tab buttons. Matches the `.active-toggle` pattern used in `static/index.js`.
- **Suggestion card rendering** (`renderSuggestions`) — renders suggestion cards with confidence rings, four-gates verdict badges, projected-impact bars, OOS status, and accept/reject/chat buttons. When the suggestions list is empty, renders the per-symphony assessment block from `body.assessment` instead of a generic placeholder. Also updates the lens-cache staleness stamp (AC-3).
- **Lens-cache staleness stamp** — after every `/ai-advisor/suggest` response (both empty and populated paths), populates `#advisor-lens-as-of` with "Market context as of `<ts>`" (fresh) or "Market context as of `<ts>` (stale)" using `textContent` only. The element is hidden (`display:none`) until JS sets a non-empty value; cleared on cold-start (no `lens_data_as_of`). Uses `textContent` — never `innerHTML` — so no XSS risk regardless of future `lens_data_as_of` shape changes.
- **CSRF token** — fetches `GET /api/csrf-token` on `DOMContentLoaded`; all POST requests include `X-CSRF-Token: _csrfToken`.
- **Autotune run feed** (`loadRecentRuns`) — polls `GET /api/autotune-runs` every 15 seconds; renders run cards with decision pills, Sortino/selection t-stat, and frozen-eval verdict; renders a Chart.js sparkline of historical Sortino values.
- **Symphony selection** (`loadSymphonies`) — populates the `#symphony-id-input` select from `GET /api/performance/symphonies`; fires `getSuggestions` automatically on select change.
- **Strategy Builder tab** (`sbRunAnalysis`, `openChatWithArtifact`) — operator-initiated proposal run and artifact-to-chat navigation for the 6th tab panel. Moved from the deleted `templates/ai_advisor_strategy_builder.html` inline script in the spa-port cycle (2026-06-13); live inside the IIFE to share the `_csrfToken` closure, then exposed on `window`.

## API Reference

### `initTabSwitcher()` (IIFE, called on DOMContentLoaded)

Wires all `[role="tab"][data-tab]` elements to their matching `[role="tabpanel"][data-tab]` elements. Clicking a tab button calls `activateTab(btn)` which:

1. Sets `aria-selected="true"` on the clicked button and `"false"` on all others.
2. Adds/removes the `active` CSS class on tab buttons.
3. Adds `tab-panel--active` class to the matching panel; removes it from all others.

The initially-active tab is determined by the button with `aria-selected="true"` in the server-rendered HTML.

---

### `window.getSuggestions()`

Reads the selected symphony from `#symphony-id-input`, POSTs to `/ai-advisor/suggest` with CSRF token, then calls `renderSuggestions(body.suggestions, symphonyId, body)`. The full response body is passed so the assessment block and lens-cache staleness stamp are available for all paths.

Disables the `#get-suggestions-btn` during the fetch; re-enables only if a symphony is still selected (B-14 rule).

---

### `renderSuggestions(suggestions, symphonyId, body)`

Renders suggestion cards into `#suggestions-container`. Placed before the empty/populated branching logic so the lens-cache staleness stamp is always updated regardless of suggestion count.

**Lens-cache staleness stamp (AC-3 — DE-ADVISOR-LATENCY):**

```javascript
var lensAsOfEl = document.getElementById('advisor-lens-as-of');
if (lensAsOfEl) {
    var lensAsOf = body && body.lens_data_as_of;
    if (lensAsOf) {
        var staleTag = body.lens_data_stale ? ' (stale)' : '';
        lensAsOfEl.textContent = 'Market context as of ' + lensAsOf + staleTag;
        lensAsOfEl.style.display = '';
    } else {
        lensAsOfEl.textContent = '';
        lensAsOfEl.style.display = 'none';
    }
}
```

- `body.lens_data_as_of` — ISO UTC string from `ai_advisor.assemble_advisor_context`; `null` on cold-start (no cache row yet).
- `body.lens_data_stale` — boolean; `true` when the bundle age exceeds `_LENS_CACHE_MAX_AGE_HOURS=36`.
- Uses `textContent` only — never `innerHTML`. The `lens_data_as_of` value is server-generated ISO UTC but `textContent` ensures no XSS risk regardless of future content changes.
- The element (`#advisor-lens-as-of`) is initially `display:none` in the template; JS sets `style.display = ''` when a timestamp is available and reverts to `'none'` on cold-start.

**Empty-suggestions path (per-symphony assessment):** When `suggestions.length === 0`, renders an assessment block from `body.assessment` (added 2026-06-10). The block shows:
- `assessment.summary` — a human-readable string explaining the tuning state (no Optuna run / all trials FDR-rejected / validated edge found). The no-Optuna-run message was reworded in DE-ADVISOR-LATENCY AC-8 to be less alarming while retaining the same accurate semantics.
- `assessment.baseline_decision` — the autotuner's decision for this symphony.
- `assessment.oos_alpha` and `assessment.fallback_oas_alpha` — numeric OOS values.

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

---

### `window.sbRunAnalysis()` (Strategy Builder tab)

Operator-initiated proposal run for the Strategy Builder tab. Reads `#sb-objective-select`, `#sb-universe-input`, and `#sb-symphony-select` from the panel controls. Obtains the CSRF token from the cached `_csrfToken` or fetches fresh from `GET /api/csrf-token` on a miss. POSTs to `POST /ai-advisor/strategy-builder/run` with `X-CSRF-Token` header and JSON body `{ objective, universe, symphony_id }`.

On success: navigates to `/ai-advisor` (the unified SPA) so newly-persisted `STRATEGY_BUILDER` observations are rendered server-side. Navigates to `/ai-advisor`, not the old standalone `/ai-advisor/strategy-builder` URL (which 302-redirects anyway per the spa-port fold-in).

On error: shows the error class name in `#sb-run-error` inline without a page navigation.

Disables `#sb-run-btn` during the request; re-enables it in the `finally` block regardless of outcome.

*Moved from inline `<script>` in the deleted `templates/ai_advisor_strategy_builder.html`; defined inside the IIFE to share the `_csrfToken` closure; exposed as `window.sbRunAnalysis` for Jinja `onclick` handlers (spa-port cycle, 2026-06-13).*

---

### `window.openChatWithArtifact(artifactJson)` (Strategy Builder tab)

Stores a strategy-proposal artifact in `sessionStorage` under the key `sb_pending_chat_artifact` so the Chat tab can retrieve it on load. Then navigates to `/ai-advisor/chat` with `from_strategy_builder=1` and `symphony_id` query params if `#sb-symphony-select` has a value.

This is pure JS navigation — no form submission, no POST. Buttons invoking this must be `type="button"` (never `type="submit"`).

*Moved from inline `<script>` in the deleted `templates/ai_advisor_strategy_builder.html`; exposed as `window.openChatWithArtifact` for Jinja `onclick` handlers (spa-port cycle, 2026-06-13).*

## Internal Dependencies

- `GET /api/csrf-token` — CSRF token fetch
- `POST /ai-advisor/suggest` — suggestion fetch; response body includes `lens_data_as_of` (str|null) + `lens_data_stale` (bool) for AC-3 stamp
- `POST /ai-advisor/accept` — suggestion acceptance
- `POST /ai-advisor/reject` — suggestion rejection
- `POST /ai-advisor/strategy-builder/run` — strategy-builder proposal run (Strategy Builder tab)
- `GET /api/autotune-runs` — autotune run history feed
- `GET /api/performance/symphonies` — symphony list
- `Chart.js` (global) — autotune sparkline; guarded by `typeof Chart === 'undefined'` check
- `openChatPanel` (global, optional) — chat slide-in panel; defined in the SPA template's inline script; falls back to navigation if absent
- `sessionStorage` — used by `openChatWithArtifact` to pass a strategy-proposal artifact to the Chat tab across the navigation boundary
- `#advisor-lens-as-of` DOM element (from `templates/ai_advisor.html`) — AC-3 lens-cache staleness stamp; `class="prism-as-of"`, `style="display:none"` initially; JS manages `textContent` and `display`
- CSS custom properties: `--studio-pos`, `--studio-neg`, `--studio-warn`, `--studio-accent`, `--studio-ink`, `--studio-ink-dim`, `--studio-surface`, `--studio-border`, `--studio-chip-bg`, `--studio-white`, `--studio-surface-raised`, `--studio-rule`, `--studio-swatch-1`
