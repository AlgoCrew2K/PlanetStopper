# static/ai_advisor.js

> Client-side logic for the AI Advisor single-page SPA: in-place tab switching, suggestion card rendering with per-symphony assessment and lens-cache staleness stamp (AC-3), accept/reject lifecycle, autotune run feed, symphony selection, and Strategy Builder run/chat affordances.

**Source:** `static/ai_advisor.js`
**Last updated:** 2026-07-13 (advisor-remediation-r1 Checkpoint-3, `DE-ADVISOR-R1-001`: `sbRunAnalysis()` gains consumption of the AC-7/AC-9/AC-11/AC-12 route-JSON fields -- see below; prior: advisor-suite-fixes AC-1/AC-2: `sbRunAnalysis()` success branch renders in-place instead of navigating away; prior: DE-ADVISOR-LATENCY AC-3 `#advisor-lens-as-of` staleness stamp; prior: spa-port cycle 2026-06-13)

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

**On success (AC-1/AC-2 fix, advisor-suite-fixes.md, 2026-07-13):** renders the response IN-PLACE into `#sb-run-results` -- never navigates away, so the displayed cards are inherently scoped to the run that just completed (no re-fetch, no stale-history confusion):
- A summary line (`data-testid="sb-live-summary"`): `"Evaluated N candidate(s)"`, plus `" — threshold α=<fdr_adjusted_threshold>"` when the route returns one.
- `data.survivors` (if any): one `.proposal-card--survivor` per item (`data-testid="sb-live-survivor-cards"`), each showing `candidate_id` (HTML-escaped via `escHtml`).
- Zero survivors: an explicit honest empty state (`data-testid="sb-live-empty-state"`) — `"Evaluated N candidates — 0 passed the gate"` — never a blank div.
- `data.rejected` (if any): a `<details data-testid="sb-live-rejected-section">` collapsible, one `.proposal-card--rejected` per item.
- No sparkline — the run endpoint's response carries no equity points; only the server-rendered persisted-history cards keep the sparkline. Accepted scope gap (team-lead ruling, documented in the plan).

**Before this fix:** unconditionally navigated to `/ai-advisor` on success, discarding the response JSON entirely — the operator saw a full-page reload with no way to tell which observations (if any) belonged to the run they just triggered (AC-1: nothing rendered; AC-2: not run-identifiable). See `DECISIONS.md` `DE-ADVISOR-SUITE-FIX-001`.

**Advisor-remediation-r1 Checkpoint-3 field consumption (`DE-ADVISOR-R1-001`, 2026-07-13, commits `fa691f6a` + `f6688ed4`):** an r1-review finding — the AC-7/AC-9/AC-11/AC-12 fields the route added to its JSON response this cycle (see [app.md](app.md)'s `POST /ai-advisor/strategy-builder/run` section) were never consumed on THIS render path, even though every route-JSON RED test proved the fields reach the response — the tests were structurally blind to this render path. Closed:

- **AC-11 provenance rollup:** a new `data-testid="sb-live-provenance"` line ("Built-new: N · Atlas: N") renders whenever `built_new_count`/`atlas_count` are non-null. No prior render surface existed for these two fields anywhere in the codebase (checked Jinja + every JS file before adding).
- **AC-11 degraded-run notice:** `data.mode_notice` (server-authored prose, e.g. an "0 plans (degraded)" explanation) renders verbatim, HTML-escaped, in a new `data-testid="sb-live-mode-notice"` div — non-null-only.
- **AC-12 screens-skipped indicator:** `data.screens_skipped` renders a new `data-testid="sb-live-screens-skipped"` line, optionally appending `data.screens_skipped_reason` when present.
- **AC-11 error_category:** the error branch appends `data.error_category` in parentheses to the existing sanitized `data.error` text when non-null — never renders the literal string `"null"`/`"undefined"`.
- **AC-9 low_power:** the per-candidate `card(c, cls)` helper adds a `proposal-card--low-power` CSS modifier when `c.low_power` is true (survivor cards only — mirrors the route's own survivor-only scoping). The caveat TEXT itself is never re-derived or hardcoded in JS — it comes from `c.caveats` (the server appends `_LOW_POWER_CAVEAT` there when `low_power` fires), rendered via the existing `caveats-block`/`caveat-text` markup. The numeric `MIN_POWER_FOLD_DAYS` threshold never crosses into JS (locked AC-9 contract).
- **AC-7 rejection_reason:** a new module-level `SB_LIVE_REJECTION_COPY` map (4 entries: `pbo_veto`, `below_spy_alpha`, `oos_inferior_to_incumbent`, `fdr_not_winner`) — byte-identical wording to the persisted-history Jinja `_REJECTION_COPY` map and the Asset-Swaps/Logic-Changes JS `REJECTION_COPY` siblings, so the operator sees the same explanation regardless of which surface rejected the candidate. Rejected cards render a `data-testid="apply-guidance"` `<strong>Gate withheld:</strong>` line when `c.rejection_reason` maps to a known entry; an unmapped or `null` reason renders NOTHING — never a fabricated blanket string, matching the map's existing extensibility convention.

**Test coverage (source-consumption, not DOM/browser):** `tests/ai_advisor/test_r1_sb_live_run_field_consumption.py` reads this file as TEXT and asserts each field name is referenced as a literal token inside `sbRunAnalysis()`'s source — this stack has no JS-behavior test runner (no jsdom/Jest/Playwright-component harness; only `node --check` syntax validation exists project-wide), so a claimed DOM-behavior test would be fabricated confidence. These tests prove the field's NAME is wired into the function that reads `data.<field>`; they prove nothing about whether the resulting DOM element is visible, styled, or reachable to an operator. The PM's first-hand browser E2E is the sufficient verification for the actual rendered UI.

On error: shows the error class name in `#sb-run-error` inline without a page navigation (unchanged, now with the `error_category` extension above).

Disables `#sb-run-btn` during the request; re-enables it in the `finally` block regardless of outcome (unchanged).

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
- `POST /ai-advisor/strategy-builder/run` — strategy-builder proposal run (Strategy Builder tab); response body includes `built_new_count`/`atlas_count`/`mode_notice`/`error_category` (AC-11), `screens_skipped`/`screens_skipped_reason` (AC-12), and per-candidate `low_power` (AC-9)/`rejection_reason` (AC-7) — all consumed by `sbRunAnalysis()` (`DE-ADVISOR-R1-001` Checkpoint-3)
- `GET /api/autotune-runs` — autotune run history feed
- `GET /api/performance/symphonies` — symphony list
- `Chart.js` (global) — autotune sparkline; guarded by `typeof Chart === 'undefined'` check
- `openChatPanel` (global, optional) — chat slide-in panel; defined in the SPA template's inline script; falls back to navigation if absent
- `sessionStorage` — used by `openChatWithArtifact` to pass a strategy-proposal artifact to the Chat tab across the navigation boundary
- `#advisor-lens-as-of` DOM element (from `templates/ai_advisor.html`) — AC-3 lens-cache staleness stamp; `class="prism-as-of"`, `style="display:none"` initially; JS manages `textContent` and `display`
- CSS custom properties: `--studio-pos`, `--studio-neg`, `--studio-warn`, `--studio-accent`, `--studio-ink`, `--studio-ink-dim`, `--studio-surface`, `--studio-border`, `--studio-chip-bg`, `--studio-white`, `--studio-surface-raised`, `--studio-rule`, `--studio-swatch-1`
