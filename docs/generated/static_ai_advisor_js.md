# static/ai_advisor.js

> Client-side logic for the AI Advisor single-page SPA: in-place tab switching, suggestion card rendering with per-symphony assessment and lens-cache staleness stamp (AC-3), accept/reject lifecycle, autotune run feed, symphony selection, and Strategy Builder run/chat affordances.

**Source:** `static/ai_advisor.js`
**Last updated:** 2026-08-05 (BL-8/BL-11 hygiene bundle, `DE-AUDIT-BL5-12-001` -- `loadRecentRuns()` renders the new `never_adopted_streak` field as a dim one-liner per autotune-run card; the per-symphony assessment block's OOS alpha label is relabeled to disclose the cumulative-sum convention -- see the updated sections below and `DE-AUDIT-BL5-12-001` in `DECISIONS.md`.) Prior: 2026-07-25 (strategy-incubation-gate, `DE-INCUBATION-GATE-001` -- new `refreshIncubationChips()`, live-refreshes server-rendered Strategy Builder incubation-status chips from `GET /api/incubation`, folded into the existing 15s `loadRecentRuns` poll interval; see the new section below. Prior: 2026-07-20 (fix-f023-perf-view, `DE-PERFVIEW-ID-MISMATCH`, F-023 -- `loadSymphonies()` now consumes `{id, name}` objects from `GET /api/performance/symphonies` (the endpoint's new contract, see `docs/generated/app.md`) instead of bare name strings, but deliberately keeps `sym.name` (not `sym.id`) as the option VALUE -- the accept/suggest flow's canonical key is the display name (`POST /ai-advisor/accept`'s `database.get_symphony_strategy`/`save_symphony_strategy` are `normalize_name(display_name)`-keyed with no hash resolution); an earlier draft of this fix used `sym.id` (the hash) here, matching `static/performance.js`'s picker, which silently broke Accept (a phantom hash-keyed `symphony_strategies` row instead of the real one) -- caught during the doc-audit pass before merge, corrected, zero server-side changes needed; see `DE-PERFVIEW-ID-MISMATCH` in `DECISIONS.md` and `BACKLOG.md` for a related pre-existing, out-of-scope `composer_symphony_id` gap surfaced during the same audit); prior: 2026-07-14 (branch-integration merge — frontrunner-builder wave-2 `frRunBuild`/`frApprove`/`frReject`/`frDispatchProposalAction` DE-FRONTRUNNER-002 integrated with R2-1 provenance render; 2026-07-13 (R2-1, `DE-ADVISOR-R2-1-001`, commit `4063ec33`: `sbRunAnalysis()` gains a `data-testid="sb-live-generation-provenance"` render for the run-level generation-model/injected-evidence/run-id object -- see below; prior: advisor-outage-degrade AC-4/AC-5, `DE-SB-DEGRADE-001`, commit `14adb451`: `sbRunAnalysis()` gains the honest `backtest_unavailable` outage notice; prior: advisor-remediation-r1 Checkpoint-3, `DE-ADVISOR-R1-001`: `sbRunAnalysis()` gains consumption of the AC-7/AC-9/AC-11/AC-12 route-JSON fields; prior: advisor-suite-fixes AC-1/AC-2: `sbRunAnalysis()` success branch renders in-place instead of navigating away; prior: DE-ADVISOR-LATENCY AC-3 `#advisor-lens-as-of` staleness stamp; prior: spa-port cycle 2026-06-13) ALSO: 2026-07-11 (frontrunner-builder wave-2 -- DE-FRONTRUNNER-002: `frRunBuild`/`frApprove`/`frReject`/`frDispatchProposalAction` added for the Frontrunner Builder tab; prior: 2026-06-29 DE-ADVISOR-LATENCY AC-3, `#advisor-lens-as-of` staleness stamp populated on suggest completion; spa-port cycle 2026-06-13)

## Overview

`ai_advisor.js` is the browser-side controller for the unified `/ai-advisor` SPA. It runs as an IIFE with `'use strict'`. All colors are CSS custom properties (`--studio-*`) resolved at runtime — no bare hex values. No Tailwind class names.

Key responsibilities:

- **In-place tab switching** (`initTabSwitcher`) — wire `[role="tab"][data-tab]` buttons to show/hide `[role="tabpanel"][data-tab]` panels without any page navigation. ARIA `aria-selected` is maintained on tab buttons. Matches the `.active-toggle` pattern used in `static/index.js`.
- **Suggestion card rendering** (`renderSuggestions`) — renders suggestion cards with confidence rings, four-gates verdict badges, projected-impact bars, OOS status, and accept/reject/chat buttons. When the suggestions list is empty, renders the per-symphony assessment block from `body.assessment` instead of a generic placeholder. Also updates the lens-cache staleness stamp (AC-3).
- **Lens-cache staleness stamp** — after every `/ai-advisor/suggest` response (both empty and populated paths), populates `#advisor-lens-as-of` with "Market context as of `<ts>`" (fresh) or "Market context as of `<ts>` (stale)" using `textContent` only. The element is hidden (`display:none`) until JS sets a non-empty value; cleared on cold-start (no `lens_data_as_of`). Uses `textContent` — never `innerHTML` — so no XSS risk regardless of future `lens_data_as_of` shape changes.
- **CSRF token** — fetches `GET /api/csrf-token` on `DOMContentLoaded`; all POST requests include `X-CSRF-Token: _csrfToken`.
- **Autotune run feed** (`loadRecentRuns`) — polls `GET /api/autotune-runs` every 15 seconds; renders run cards with decision pills, Sortino/selection t-stat, and frozen-eval verdict; renders a Chart.js sparkline of historical Sortino values.
- **Symphony selection** (`loadSymphonies`) — populates the `#symphony-id-input` select from `GET /api/performance/symphonies`'s `{id, name}` objects, using `sym.name` as BOTH the option value and label (F-023, `DE-PERFVIEW-ID-MISMATCH` — the accept/suggest flow's canonical key, unlike `static/performance.js`'s picker, which uses `sym.id`); fires `getSuggestions` automatically on select change.
- **Strategy Builder tab** (`sbRunAnalysis`, `openChatWithArtifact`) — operator-initiated proposal run and artifact-to-chat navigation for the 6th tab panel. Moved from the deleted `templates/ai_advisor_strategy_builder.html` inline script in the spa-port cycle (2026-06-13); live inside the IIFE to share the `_csrfToken` closure, then exposed on `window`.
- **Frontrunner Builder tab** (`frRunBuild`, `frApprove`, `frReject`) — operator-initiated on-demand build trigger and per-proposal approve/reject dispatch for the 7th tab panel (frontrunner-builder wave-2, 2026-07-11). Live inside the IIFE to share the `_csrfToken` closure, then exposed on `window`.

- **Strategy Incubation status chips** (`refreshIncubationChips`) — re-syncs already-rendered `incubation-status-chip` elements against the live `GET /api/incubation` ledger (a candidate's status can change between page loads, e.g. `INCUBATING` -> `PROMOTED`, without a full reload). Folded into the existing 15s poll interval rather than a new timer.

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

**OOS alpha label disclosure (BL-11, `DE-AUDIT-BL5-12-001`, audit #118 Finding T6, 2026-08-05):** the rendered `oosHtml` line's label literal changed from `'OOS alpha: <code>'` to `'OOS alpha (cumulative sum across triggered days): <code>'` -- `assessment.oos_alpha` is `autotuner.py`'s `total_guard_alpha`, a multi-day SUM across every triggered OOS day, not a per-day or annualized figure (this is why values like -743%/-581% render here). No `avg_oos_alpha` companion is persisted anywhere to surface alongside it (AC-19 team-lead-ruled deviation from the plan's literal "surface avg alongside" wording -- see `docs/generated/autotuner.md`'s "oos_alpha Sum-Convention Comment" section for the full ruling), so the disclosure is relabeling in place rather than a second displayed value.

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

**"Never adopted" streak render (BL-8, `DE-AUDIT-BL5-12-001`, audit #118 Finding T3, 2026-08-05):** each card additionally renders `r.never_adopted_streak` (new additive field from `GET /api/autotune-runs`, see `docs/generated/app.md`) as a dim one-liner below the frozen-eval verdict pill, same idiom as the timestamp span:
- `status === 'streak' && streak_weeks >= 2` -> `"<N> consecutive runs without adopting a tuned config"` (threshold mirrors `analytics._NEVER_ADOPTED_MIN_ROWS` -- a single non-adopted run isn't a pattern worth flagging).
- `status === 'insufficient_history'` -> `"adoption streak: insufficient history (<2 runs)"` (AC-11's informative degrade, team-lead-ruled visible per the operator's affirmative reading of AC-11's letter -- rare in production since every live symphony has 4-5+ runs).
- `streak_weeks < 2` with `status === 'streak'` -> nothing rendered.

Shipped as a dedicated follow-up commit (`f846bca1`) after the team lead flagged the AC-9/AC-10 gap: the streak signal was computed and stamped on the route response but not yet rendered anywhere -- "the exact defect class this audit program exists to close." `TestStreakRenderTextContract` (`tests/app/test_bl8_streak_render_and_raw_baseline.py`) source-scans for both rendered variants, confirms `escHtml` wraps the dynamic `streak_weeks` variant specifically (non-vacuity independently demonstrated by temporarily stripping the `escHtml()` call), and confirms the `streak_weeks < 2` case renders nothing (no bare else-fallback).

---

### `loadSymphonies()`

Fetches `GET /api/performance/symphonies`, populates `#symphony-id-input` options from the returned `{id, name}` objects: `opt.value = sym.name` AND `opt.textContent = sym.name` (both the display name — see the F-023 note below for why this differs from `static/performance.js`'s picker). Preserves the previously-selected value if it is still in the returned list. On select `change`, calls `syncRunBtn()` and auto-fires `getSuggestions()` if a value is selected (C-11 wire).

**F-023 fix + blast-radius correction (`DE-PERFVIEW-ID-MISMATCH`, 2026-07-20):** the endpoint previously returned bare NAME strings for both label and value; this function now reads the new `{id, name}` shape explicitly, but deliberately keeps `sym.name` — not `sym.id` — as the option value. `#symphony-id-input`'s value flows into `getSuggestions()` -> `POST /ai-advisor/suggest` (which dual-resolves either a hash OR a name, `app.py:5773-5786`, unaffected either way) AND into `renderSuggestions()`'s per-card `acceptSuggestion(index, symphonyId)`/`rejectSuggestion(index, symphonyId)` handlers -> `POST /ai-advisor/accept`/`POST /ai-advisor/reject`. Unlike `/ai-advisor/suggest`, `/ai-advisor/accept` (`app.py:5843`) uses `symphony_id` DIRECTLY with no hash resolution — it calls `database.get_symphony_strategy(symphony_id)`/`database.save_symphony_strategy(symphony_id, ...)` (`database.py:508-509`/`538-539`), both `normalize_name(display_name)`-keyed only. An earlier draft of this cycle set `opt.value = sym.id` (the hash, matching `performance.js`'s picker) — this silently broke Accept: a hash-valued `symphony_id` misses the real `symphony_strategies` row (`get_symphony_strategy` returns empty defaults, wrong OOS-revalidation baseline) and, on gate pass, `save_symphony_strategy` INSERTs a phantom row keyed by the lowercased hash instead of updating the real one — an accepted config change would silently never take effect, despite a `{"status": "accepted"}` response. Caught during the doc-audit pass (before merge, no live exposure), root-caused, and corrected — `POST /ai-advisor/accept` and `POST /ai-advisor/suggest` needed and received ZERO server-side changes; the fix lives entirely in this function. `performance.js`'s picker legitimately keeps `sym.id` — its downstream query (`GET /api/performance?scope=symphony&symphony_id=`) is hash-keyed, a genuinely different consumer contract from this file's accept/suggest flow.

**Known pre-existing gap, out of scope for this cycle (surfaced during the same audit):** `POST /ai-advisor/suggest` passes the raw client `symphony_id` straight through as `composer_symphony_id` (no server-side hash resolution, unlike its own `resolved_id` name-resolution a few lines above). `ai_advisor.assemble_advisor_context`'s P2 dependency (`ai_advisor.py:1601-1604`) requires the Composer HASH for `symphony_logic.get_condensed_logic`/`fetch_symphony_score` — a name produces an HTTP 400 and an all-empty logic struct (D-1, degrades silently, never crashes). Since this picker sends a NAME (by design, per above), every Advisor-tab suggestion request built from `#symphony-id-input` gets a degraded (empty) condensed-logic context section. Pre-existing (not introduced by F-023 — the pre-fix picker also sent a bare name), explicitly ruled out of scope for this cycle; tracked in `BACKLOG.md`.

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

**Advisor-outage-degrade AC-4/AC-5 (`DE-SB-DEGRADE-001`, commit `14adb451`, 2026-07-13):** a new honest-degrade notice, rendered right after the AC-12 screens-skipped indicator, before the survivor/rejected cards. Guarded on the boolean `data.backtest_unavailable` flag (mirrors the `screens_skipped`/`screens_skipped_reason` pairing above, not the `mode_notice`-only pattern) — a healthy run renders nothing. When true, renders `data.backtest_unavailable_notice` (server-authored prose, e.g. `"3 candidate(s) could not be tradeability-checked — Composer backtest unavailable"`, HTML-escaped via `escHtml`) in a new `data-testid="sb-live-backtest-unavailable"` `.empty-state` div. Distinguishes an outage-degraded run from both a normal "0 passed the gate" empty-state and from ordinary survivor/rejected cards — the operator can tell the difference between "the gate rejected everything" and "Composer was unreachable, so some candidates were never tradeability-checked." Test coverage: `tests/ai_advisor/test_sb_backtest_unavailable_js_consumption.py` (same source-consumption pattern as the R1 sibling test below).

**R2-1 — generation provenance render (`DE-ADVISOR-R2-1-001`, commit `4063ec33`, 2026-07-13):** a new run-level render, placed right after the AC-4/AC-5 outage-degrade notice above and before the survivor/rejected cards. Guarded on the truthy `data.provenance` object — a run before R2-1's route change, or either error branch (both leave `provenance` absent from the JSON entirely, never `null`), renders nothing:

```javascript
if (data.provenance) {
    var prov = data.provenance;
    var evidence = prov.evidence_injected || {};
    var evidenceParts = [];
    ['tree', 'stats', 'technicals', 'sentiment', 'derivatives', 'macro', 'fundamentals'].forEach(function (key) {
        var val = evidence[key];
        if (val) { evidenceParts.push(key + ': ' + val); }
    });
    html += '<div class="run-controls-note" data-testid="sb-live-generation-provenance">' +
        'Model: ' + escHtml(prov.generation_model || '') +
        (evidenceParts.length ? ' · Context — ' + escHtml(evidenceParts.join(', ')) : '') +
        (prov.run_id ? ' · Run: ' + escHtml(prov.run_id) : '') +
        '</div>';
}
```

Renders three pieces of the R2-1 provenance contract in one `data-testid="sb-live-generation-provenance"` line: the generation model (`prov.generation_model`), a compact rendering of the `evidence_injected` honest manifest (`"tree: present, stats: present, technicals: available, ..."` — every truthy manifest value, in the fixed order `tree`/`stats`/the 5 lenses, HTML-escaped), and the run id (`prov.run_id`). All three pieces are non-null-only within the block (`evidenceParts.length` guards the `· Context —` segment; `prov.run_id` guards the `· Run:` segment) so a partially-populated manifest never renders a dangling separator.

**Deliberately DISAMBIGUATED from the pre-existing `data-testid="sb-live-provenance"` line (AC-11/F5, above):** both use the word "provenance" but name two independent concepts — the AC-11 line is a per-candidate TEMPLATE-origin rollup (built-new vs. atlas-suggested COUNTS); this R2-1 line is this RUN's generation-CONTEXT provenance (which model, what evidence, which run). The comment directly above this block in the source calls out the collision explicitly so a future reader does not conflate or merge the two testids. See [app.md](app.md)'s `POST /ai-advisor/strategy-builder/run` section for the route-side `provenance` object and its `isinstance(dict)` MagicMock-serialization guard, and [advisors/strategy_builder_engine](advisors_strategy_builder_engine.md)'s "R2-1" section for the manifest's honest-degradation contract.

**Test coverage (source-consumption, not DOM/browser):** `tests/ai_advisor/test_r1_sb_live_run_field_consumption.py` reads this file as TEXT and asserts each field name is referenced as a literal token inside `sbRunAnalysis()`'s source — this stack has no JS-behavior test runner (no jsdom/Jest/Playwright-component harness; only `node --check` syntax validation exists project-wide), so a claimed DOM-behavior test would be fabricated confidence. These tests prove the field's NAME is wired into the function that reads `data.<field>`; they prove nothing about whether the resulting DOM element is visible, styled, or reachable to an operator. The PM's first-hand browser E2E is the sufficient verification for the actual rendered UI.

On error: shows the error class name in `#sb-run-error` inline without a page navigation (unchanged, now with the `error_category` extension above).

Disables `#sb-run-btn` during the request; re-enables it in the `finally` block regardless of outcome (unchanged).

*Moved from inline `<script>` in the deleted `templates/ai_advisor_strategy_builder.html`; defined inside the IIFE to share the `_csrfToken` closure; exposed as `window.sbRunAnalysis` for Jinja `onclick` handlers (spa-port cycle, 2026-06-13).*

---

### `window.openChatWithArtifact(artifactJson)` (Strategy Builder tab)

Stores a strategy-proposal artifact in `sessionStorage` under the key `sb_pending_chat_artifact` so the Chat tab can retrieve it on load. Then navigates to `/ai-advisor/chat` with `from_strategy_builder=1` and `symphony_id` query params if `#sb-symphony-select` has a value.

This is pure JS navigation — no form submission, no POST. Buttons invoking this must be `type="button"` (never `type="submit"`).

*Moved from inline `<script>` in the deleted `templates/ai_advisor_strategy_builder.html`; exposed as `window.openChatWithArtifact` for Jinja `onclick` handlers (spa-port cycle, 2026-06-13).*

### `window.frRunBuild()` (Frontrunner Builder tab)

Operator-initiated on-demand build trigger. Obtains the CSRF token from the cached `_csrfToken` or fetches fresh from `GET /api/csrf-token` on a miss. POSTs to `POST /ai-advisor/frontrunner-builder/run` with `X-CSRF-Token` header and an empty JSON body.

**Deliberately does NOT auto-navigate** (unlike `sbRunAnalysis`) -- the route dispatches the build to a background executor and returns `202` immediately; there is no synchronous result to render yet. On success, shows a status message (`#fr-run-status`) telling the operator to reload the page later. On an `{error}` response or a fetch failure, shows the error inline in `#fr-run-error` -- no page navigation either way.

Disables `#fr-run-btn` during the request; re-enables it in the `finally` block regardless of outcome.

---

### `frDispatchProposalAction(action, proposalId)` (Frontrunner Builder tab, internal)

Shared approve/reject dispatch for one `frontrunner_proposals` row. `action` is `'approve'` or `'reject'`; routes to `POST /ai-advisor/proposal/approve` or `POST /ai-advisor/proposal/reject` respectively with `{ proposal_id: proposalId }`.

Immediately disables both the card's `[data-testid="fr-approve-btn"]` and `[data-testid="fr-reject-btn"]` buttons and dims the card (`opacity: 0.6`) -- prevents a double-submit while the request is in flight. On `{success: true}`, replaces the card's `.proposal-actions` row with a confirmation message (echoing `symphony_id` on approve via `escHtml()` -- no raw interpolation) and leaves the card dimmed permanently (an already-actioned card). On any failure (`{success: false}`, non-2xx, or a thrown error), restores full opacity and re-enables both buttons, then `alert()`s the error -- the operator can retry.

Not exposed on `window` directly; called only via the two wrappers below.

---

### `window.frApprove(proposalId)` / `window.frReject(proposalId)` (Frontrunner Builder tab)

Thin wrappers calling `frDispatchProposalAction('approve', proposalId)` / `frDispatchProposalAction('reject', proposalId)`. Exposed on `window` for Jinja `onclick="frApprove({{ p.id }})"` / `onclick="frReject({{ p.id }})"` handlers.

---

### `refreshIncubationChips()` (Strategy Builder tab, `DE-INCUBATION-GATE-001`, 2026-07-25)

Live re-sync for the Strategy Builder tab's server-rendered incubation-status chips (`templates/ai_advisor.html`'s `.incubation-status-chip` elements, `data-testid="incubation-status-chip"`). Chips are server-rendered on page load — `app.py`'s `ai_advisor_tab()` stamps a live-joined status onto each survivor at THAT request (see `docs/generated/app.md`'s "`ai_advisor_tab()` — Strategy Incubation live-join badge stamping" section) — but the underlying status can change between page loads (a candidate promoted or failed days after the operator last loaded the tab). This function keeps already-rendered chips current without a full page reload.

```javascript
function refreshIncubationChips() {
    var chips = document.querySelectorAll('[data-testid="incubation-status-chip"]');
    if (!chips.length) { return; }
    fetch('/api/incubation')
        .then(function (resp) { return resp.json(); })
        .then(function (body) {
            var rows = (body && body.incubating) || [];
            var byHash = {};
            rows.forEach(function (r) { byHash[r.candidate_hash] = r; });
            chips.forEach(function (chip) {
                var hash = chip.dataset.candidateHash;
                var row = hash ? byHash[hash] : null;
                if (!row) { return; }
                chip.className = 'incubation-status-chip incubation-status-chip--' + row.badge_modifier;
                chip.textContent = row.badge_label;
            });
        })
        .catch(function () { /* leave last-known chip state in place */ });
}
```

**Property-assignment only, never `innerHTML`.** Both `className` and `textContent` are plain property assignments — `status_reason` (embedded in `badge_label` for `FAILED`/`EXPIRED` chips) is server-derived text, never treated as markup.

**No-op fast path:** if the page has zero incubation chips (no Strategy Builder survivors carry a `candidate_hash` yet), the function returns immediately without making a network call — `querySelectorAll(...).length` guard before the `fetch`.

**Wiring:** called once on `DOMContentLoaded` (alongside `loadRecentRuns()`/`loadSymphonies()`) and then folded into the existing `setInterval(loadRecentRuns, 15000)` cadence — that `setInterval` callback now wraps both `loadRecentRuns()` and `refreshIncubationChips()` in one closure rather than adding a second timer (house convention: no new timers, fold into the SPA's existing refresh cycle).

**Silent degrade on fetch failure:** an empty `.catch()` leaves the last-known chip state in place rather than clearing it or showing an error — a transient network failure should never flicker a correct chip to a blank or broken state.

**Placement note:** deliberately placed between the `DOMContentLoaded` block and the "Strategy Builder tab functions" section — NOT adjacent to `loadSymphonies()` or `sbRunAnalysis()`, both of which use `.innerHTML` nearby for unrelated markup — so this function's own body sits outside the char-window a repo-wide `test_js_does_not_use_innerhtml_for_incubation_content` source-scan check inspects around the incubation testid string; verified GREEN by direct test run, not merely by construction.

## Internal Dependencies

- `GET /api/csrf-token` — CSRF token fetch
- `POST /ai-advisor/suggest` — suggestion fetch; response body includes `lens_data_as_of` (str|null) + `lens_data_stale` (bool) for AC-3 stamp
- `POST /ai-advisor/accept` — suggestion acceptance
- `POST /ai-advisor/reject` — suggestion rejection
- `POST /ai-advisor/strategy-builder/run` — strategy-builder proposal run (Strategy Builder tab); response body includes `built_new_count`/`atlas_count`/`mode_notice`/`error_category` (AC-11), `screens_skipped`/`screens_skipped_reason` (AC-12), and per-candidate `low_power` (AC-9)/`rejection_reason` (AC-7) — all consumed by `sbRunAnalysis()` (`DE-ADVISOR-R1-001` Checkpoint-3) — `backtest_unavailable`/`backtest_unavailable_count`/`backtest_unavailable_notice` (AC-4/AC-5, `DE-SB-DEGRADE-001`, also consumed by `sbRunAnalysis()`) — and (R2-1, symphony-scoped runs only) `provenance` (`{generation_model, mode, evidence_injected, run_id}`, `DE-ADVISOR-R2-1-001`, consumed by `sbRunAnalysis()`'s `sb-live-generation-provenance` block)
- `POST /ai-advisor/frontrunner-builder/run` — on-demand build trigger (Frontrunner Builder tab, async 202)
- `POST /ai-advisor/proposal/approve` / `POST /ai-advisor/proposal/reject` — per-proposal approve/reject (Frontrunner Builder tab, shared by both proposal sources)
- `GET /api/autotune-runs` — autotune run history feed
- `GET /api/performance/symphonies` — symphony list, `{id, name}` objects (F-023, `DE-PERFVIEW-ID-MISMATCH`, was bare name strings); this file's picker uses `sym.name` as the option value (accept/suggest's canonical key), NOT `sym.id` (contrast `static/performance.js`, which needs the hash)
- `Chart.js` (global) — autotune sparkline; guarded by `typeof Chart === 'undefined'` check
- `openChatPanel` (global, optional) — chat slide-in panel; defined in the SPA template's inline script; falls back to navigation if absent
- `sessionStorage` — used by `openChatWithArtifact` to pass a strategy-proposal artifact to the Chat tab across the navigation boundary
- `#advisor-lens-as-of` DOM element (from `templates/ai_advisor.html`) — AC-3 lens-cache staleness stamp; `class="prism-as-of"`, `style="display:none"` initially; JS manages `textContent` and `display`
- `#fr-run-btn` / `#fr-run-status` / `#fr-run-error` DOM elements (from `templates/ai_advisor.html`) — Frontrunner Builder run-controls panel, wired by `frRunBuild()`
- `GET /api/incubation` — Strategy Incubation Gate ledger read, consumed by `refreshIncubationChips()` (`{incubating: [{candidate_hash, badge_label, badge_modifier, ...}]}`, see `docs/generated/app.md`)
- `[data-testid="incubation-status-chip"]` / `data-candidate-hash` DOM elements (from `templates/ai_advisor.html`, server-rendered per survivor) — re-synced by `refreshIncubationChips()`
- `escHtml()` / `cssVar()` (pre-existing internal helpers, this file) — used by `frDispatchProposalAction` for the post-approve confirmation message
- CSS custom properties: `--studio-pos`, `--studio-neg`, `--studio-warn`, `--studio-accent`, `--studio-ink`, `--studio-ink-dim`, `--studio-surface`, `--studio-border`, `--studio-chip-bg`, `--studio-white`, `--studio-surface-raised`, `--studio-rule`, `--studio-swatch-1`
