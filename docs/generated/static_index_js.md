# static/index.js

> Client-side dashboard controller: state polling, event-driven updates via SSE, guard-alpha panel, window-picker, visible staleness cue, and honest post-trigger MC rendering.

**Source:** `static/index.js`
**Last updated:** 2026-08-05 (BL-3, HEAD `fdcb07e1`, `DE-AUDIT-BL3-001` -- `fetchGuardAlphaSummary()` gains two additive net-of-friction render lines (snapshot + realized), gated on the SAME empty-state conditions as their gross siblings after a review-found gap was fixed; closes audit #118 Finding M1 -- see the updated section below.) Prior: 2026-07-29 (guard-alpha-saved-coherence, `DE-GAS-COHERENCE-001` -- `fetchGuardAlphaSummary()` gains a window-token parameter (joining `/api/guard-alpha-summary?window=<token>` in lockstep with the existing hero window-picker), both dollar headlines (snapshot + realized) drop their naked `-$` sign prefix in favor of a dedicated sign-conditional verb element (`#dollar-saved-verb` / `#dollar-saved-realized-verb`), and `_heroWindow`'s initial value changes from the bare number `30` to the string `'30d'` to match the shape the picker's click handler always assigns; see the updated section below. That same change exposed a pre-existing `applyHeroWindow()` type mismatch (numeric `dates.slice(-days)` fed a string token), caught by gas-test's own sufficiency review before merge and fixed in the same cycle -- see the updated `applyHeroWindow()` section below, not a deferred item.) Prior: 2026-07-24 (exit-friction-realized-savings, `DE-EXIT-FRICTION-REALIZED-001` -- `fetchGuardAlphaSummary()` gains an additive realized-basis (marks) headline render; see the updated section below.) Prior: 2026-07-21 (fix-display-cluster, `DE-DISPLAY-TRUTH-001`) — F-011 (`updateSectionMeta` field fix), F-014 (`updateComparisonRows` lifetime-source fix), F-016 locus 1 (`updateComparisonRows` null-vs-zero honest empty-state), F-025 (`renderHeroChart` y-axis visible); see the new sections below. Prior: 2026-07-18 (Math Remediation F7, `DE-MATH-F7-001`) — MC dial + detail-view chart-fallback exited-state render honesty (AC-2); see the F7 section below. Prior: 2026-06-23 (feat/dashboard-realtime-push: EventSource SSE wiring + showConnectionLost staleness cue)

## Overview

`static/index.js` is the browser-side controller for the operator dashboard (`/`). It runs as an IIFE. Key responsibilities:

- **State polling** — `loadState()` fetches `/api/state` and calls `updateDashboard(data)` on success. Fires once on `DOMContentLoaded` and then on a 30 s `setInterval` as the resilience fallback.
- **Event-driven updates (primary path)** — subscribes to `/api/events` via the `EventSource` API. On a `cycle-complete` event, calls `loadState()` immediately so the dashboard reflects the engine's just-completed cycle without waiting for the next 30 s poll tick.
- **Staleness cue** — `showConnectionLost()` flips the engine badge and `data-as-of` element to a visible error state when `loadState()` fails, so the operator knows displayed numbers are frozen rather than silently stale.
- **Guard-alpha dollar-saved panel** — `fetchGuardAlphaSummary(windowToken)` calls `/api/guard-alpha-summary` (optionally windowed, `DE-GAS-COHERENCE-001`) once on page load and on every window-picker click, populating the `#dollar-saved-headline` / `#dollar-saved-verb` / `#guard-event-count` / `#dollar-saved-basis-label` elements (plus the realized-basis siblings).
- **Window picker** — `fetchWindowedStrip(token)` calls `/api/strip/<token>` when the operator clicks a time-window button; re-windows the hero headline and comparison rows without a full page reload. As of `DE-GAS-COHERENCE-001`, the SAME click also calls `fetchGuardAlphaSummary(token)` so the $-saved panel re-windows in lockstep.
- **MC dial + detail render honesty** — `renderMcDial()` and the detail-view Risk Math panel actively render an explicit exited/"—" state for a triggered symphony instead of scanning history for (or freezing on) a stale pre-trigger reading (Math Remediation F7, `DE-MATH-F7-001`).
- **Section-count badges** — `updateSectionMeta()` reads `data.state`/`data.bot_state` (present on every `/api/state` branch) rather than the sometimes-absent `data.symphonies`, so the active/standby counts don't collapse to 0 on the closed/frozen branch (F-011).
- **Comparison rows** — `updateComparisonRows()` re-renders the Today/Cumulative/MDD rows on every poll; the Cumulative row sources the lifetime `cumulative_return` only (F-014) and preserves a genuine `null` through to the honest empty-state instead of coercing it to a false zero (F-016 locus 1).
- **Hero chart axis + label** — `renderHeroChart()`'s Chart.js config keeps the y-axis visible so the chart's day-by-day cumulative series can't be visually mistaken for the adjacent lifetime scalar headline (F-025).

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

### `updateSectionMeta(data)`
Computes the active/standby section-count badges from the live symphony set.

**F-011 fix (`DE-DISPLAY-TRUTH-001`, 2026-07-21):** previously read `Array.isArray(data.symphonies) ? data.symphonies : []` — `data.symphonies` genuinely does not exist on the closed/frozen `/api/state` branch (`app.py` emits `state`/`bot_state` only there), so the badges deterministically read 0 (empty array) whenever the market was closed. Fixed to read `data.bot_state || data.state` — present on BOTH branches — filtered to symphony-shaped entries (`typeof v === 'object' && 'name' in v`), matching the same filter `app.py` itself applies (`isinstance(v, dict) and "name" in v`) since `state`/`bot_state` also carries flat non-symphony metadata keys (e.g. `last_successful_cycle_at`) at the top level that a naive `Object.values()` would miscount as a phantom standby symphony.

### `updateComparisonRows(data)`
Renders the Today / Cumulative · lifetime / Max DD comparison rows (bot vs. held bars, per-row alpha) on every poll.

**F-014 fix (`DE-DISPLAY-TRUTH-001`, 2026-07-21):** the Cumulative row's `values:` previously preferred `ps.windowed_cumulative_return || ps.cumulative_return`. The row's own SSR label reads "Cumulative · lifetime" (`templates/index.html:920`) and is correctly sourced server-side — but this JS re-poll clobbered it with a windowed value every 30 s. Fixed to source `ps.cumulative_return` only, dropping the windowed preference entirely. (Numerically invisible while the dataset is < 30 days old — the fix is pinned on the SOURCE EXPRESSION, not a numeric-equality test.)

**F-016 locus 1 fix (`DE-DISPLAY-TRUTH-001`, 2026-07-21):** the bot/held extraction previously did `sentinelToNull(...) || 0`, discarding a genuine `null` before `fmtPct`'s own honest `'--'` branch could ever run. Fixed by dropping the `|| 0` coercion. Because `setPosNeg`'s `value >= 0` check evaluates `true` for `null` in JS (null coerces to `0` in a numeric comparison), every downstream `setPosNeg` call and the delta-alpha (`α`) calculation are now explicitly null-guarded too — a `null` bot/held value skips color-classification and renders `α --` rather than being silently treated as positive/green. See `DE-DISPLAY-TRUTH-001` in `DECISIONS.md` for the companion 3rd F-016 locus (`app.py`'s `_tc_cr_mdd_floats`, the opposite failure direction) this cycle also fixed.

### `renderHeroChart()` — Chart.js config (hero chart)
Builds the hero day-by-day cumulative chart (Chart.js line chart, bot vs. if-held datasets).

**F-025 fix (`DE-DISPLAY-TRUTH-001`, 2026-07-21):** the `scales` config previously hid both axes (`{ x: { display: false }, y: { display: false } }`). The y-axis (magnitude) is now visible (`y: { display: true }`) so the chart's day-by-day cumulative series (~-3% at audit time) can't be visually conflated with the adjacent VW-lifetime scalar headline (~+34%, opposite sign, ~12x magnitude) — a naive glance at the hidden-axis version could read as "the tool contradicts itself." The x-axis stays hidden (PM ruling: the y-axis is the requirement since magnitude conflation was the defect; the `hero-data-as-of` legend span already covers time). `templates/index.html`'s chart-legend area also gains a "Day-by-day cumulative · shadow-history basis" label span distinguishing this chart's basis from the "Cumulative · lifetime" comparison row a few lines below. The plotted series (`applyHeroWindow`'s dataset assignments) is byte-unchanged — presentation-only, regression-pinned.

**Sufficiency-review fix, `applyHeroWindow()` token parsing (`DE-GAS-COHERENCE-001` Revise, 2026-07-29):** this cycle's own AC-11 change to `_heroWindow`'s initial value (bare number `30` -> string token `'30d'`) exposed a pre-existing type mismatch: `applyHeroWindow(days)`'s non-`'ytd'` branch did `dates.slice(-days)`, which requires a NUMERIC `days` -- but `_heroWindow` (and every value the window-picker click handler assigns to it, `:1513`) has always been a STRING token (`'30d'`/`'60d'`/`'90d'`/`'125d'`/`'1y'`/`'all'`). `dates.slice(-'30d')` coerced to `dates.slice(NaN)`, which `Array.prototype.slice` treats as `slice(0)` -- the FULL array, not the intended window. Since `applyHeroWindow(_heroWindow)` fires on every chart render, including the very first (`:129`), this cycle's own `_heroWindow` fix would have regressed the hero chart's default page-load view from a 30-day window to the entire lifetime history -- caught by gas-test's own sufficiency review (RED at `622da235`) BEFORE merge, never shipped as a live regression. **Fixed (`67b66b7e`):** the non-`'ytd'` branch now parses the `'<N>d'` token shape via `parseInt` (also transparently preserving bare-number compatibility -- `parseInt(30, 10)` still yields `30`), special-cases `'1y'` to `365` (the SAME app-wide `analytics._WINDOW_TRAILING_DAYS['1y']` day-count this cycle's server-side AC-2 fix already established -- so the hero CHART and the windowed $-saved/strip VALUES now agree on what `'1y'` means, not just the two server-side routes agreeing with each other), and `'all'` is now an explicit lifetime branch (previously an accidental byproduct of the same NaN-slice bug, made deliberate so it survives the token-parsing fix). `'ytd'` handling is unchanged.

### SSE subscription (DOMContentLoaded block)
```js
if (typeof EventSource !== 'undefined') {
    var _es = new EventSource('/api/events');
    _es.addEventListener('cycle-complete', function () { loadState(); fetchGuardAlphaSummary(_heroWindow); });
    _es.onerror = function () { /* silent — poll fallback handles reconnect */ };
}
```

Registered alongside the `setInterval` in the `DOMContentLoaded` callback. Behavior:
- **Primary update path:** `cycle-complete` fires within ~1 s of engine subprocess exit; `loadState()` fetches fresh `/api/state` before the next 30 s poll would fire. As of `DE-GAS-COHERENCE-001`, the same event also re-fetches the $-saved panel at the currently-active window token (was a bare `fetchGuardAlphaSummary()` call, always all-time).
- **Auth failure:** if `/api/events` returns 401, `EventSource.onerror` fires silently; the 30 s poll continues as the sole update path.
- **Connection drop / daemon restart:** `EventSource` retries automatically (browser built-in retry with exponential backoff). During the reconnect window the 30 s poll keeps state live.
- **Unsupported browser:** the `typeof EventSource !== 'undefined'` guard skips SSE entirely; the existing poll is the only update path. No visual breakage.

### `fetchGuardAlphaSummary(windowToken)`
Fetches `GET /api/guard-alpha-summary` (optionally `?window=<windowToken>`, `DE-GAS-COHERENCE-001`, AC-11) once on `DOMContentLoaded`, on every `cycle-complete` SSE event, and on every window-picker click. Populates:

| Element | Field |
|---------|-------|
| `#dollar-saved-headline` | `'$' + Math.abs(data.cumulative_saved_dollars).toFixed(2)` (ABS magnitude, no sign character) or `'No guard events yet'`, colored `--studio-pos`/`--studio-neg` by sign |
| `#dollar-saved-verb` | `'saved'` (sign `>= 0`) or `'lost'` (sign `< 0`) -- a dedicated element, independent of the static "across"/"exits" caption text around it |
| `#guard-event-count` | `data.guard_event_count` |
| `#dollar-saved-basis-label` | `data.basis_label` |

Non-200 responses are silently ignored (advisory-only display). Does NOT clobber `#guard-alpha-headline`, which is owned by the windowed strip path.

**Sign coherence fix (`DE-GAS-COHERENCE-001`, 2026-07-29):** previously formatted the headline as `(saved < 0 ? '-$' : '$') + Math.abs(saved).toFixed(2)`, paired with a STATIC caption span that always read " saved across " regardless of sign -- a losing window rendered `"-$50.00 saved across 3 exits"`, a naked minus under an unconditional "saved" claim. Fixed: the headline VALUE is now always the ABS magnitude with no sign character at all (`'$' + Math.abs(saved).toFixed(2)`); the new `#dollar-saved-verb` span (added in `templates/index.html`, replacing part of the old static caption) is set to `'saved'`/`'lost'` by `fetchGuardAlphaSummary` itself, driven by the SAME sign that already colors the headline. The realized-basis sibling below gets the identical treatment via its own `#dollar-saved-realized-verb` element.

**Windowing wiring (`DE-GAS-COHERENCE-001`, AC-11):** `fetchGuardAlphaSummary` gained its `windowToken` parameter, included in the fetch URL as `window=<token>` when truthy. The existing hero window-picker click handler (`var windowTokenMap = {...}`, which already re-fetches `/api/hero-chart` + the windowed strip on click) now ALSO calls `fetchGuardAlphaSummary(token)` on the same click -- previously the $-saved panel always showed the all-time sum regardless of the selected window. `_heroWindow`'s initial value changed from the bare number `30` to the string `'30d'` (`:7`) to match the string shape the click handler always assigns and the actively-highlighted 30d button, so the very first `fetchGuardAlphaSummary(_heroWindow)` call (on `DOMContentLoaded`, before any click) is windowed the same way subsequent calls are.

**Realized-basis (marks) headline render (exit-friction-realized-savings, `DE-EXIT-FRICTION-REALIZED-001`, 2026-07-24; sign-coherence-updated `DE-GAS-COHERENCE-001`, 2026-07-29):** an additive sibling render, independent of the snapshot-basis branch above, driven by `data.realized_coverage` (`{with_data, total}`) and `data.saved_dollars_realized`:

| Condition | `#dollar-saved-realized-headline` | `#dollar-saved-realized-verb` | `#dollar-saved-realized-coverage` |
|-----------|--------------------------------------|--------------------------------------|--------------------------------------|
| `coverage.with_data === 0` | `'no realized data yet'` (AC-7 honesty requirement -- a bare `$0.00` would misrepresent "no data" as "measured zero") | (unset) | `'0 of ' + coverage.total` |
| `coverage.with_data > 0` | `'$' + Math.abs(realizedSaved).toFixed(2)` (no sign character), colored `--studio-pos`/`--studio-neg` by sign | `'saved'`/`'lost'` by sign | `coverage.with_data + ' of ' + coverage.total` |

The "marks basis" qualifier text itself lives in `templates/index.html` as a STATIC caption (never JS-injected) so it can never be silently dropped by a JS bug -- see `docs/generated/app.md`'s `GET /api/guard-alpha-summary` section for the route-side `saved_dollars_realized`/`realized_coverage` field semantics and the RULING A sourcing rule.

**JS-side dollar-formatting convention (`DE-GAS-COHERENCE-001`):** this file implements its own PER-FILE-LOCAL abs+no-sign+word logic inline in `fetchGuardAlphaSummary` (rather than importing a shared JS module -- this repo has no bundler, and its established JS-behavior-test idiom inspects each function's own source text for the literal tokens). `static/history.js` implements the IDENTICAL contract independently for its own dollar surfaces; DRY across the two files is enforced by parallel body-extraction tests, not a shared import. The shared Python-side equivalent, `analytics.format_dollar_saved`, is documented in `docs/generated/analytics.md`.

**Net-of-friction lines (`DE-AUDIT-BL3-001`, BL-3, 2026-08-05):** an additive third and fourth line, driven by the two new `GET /api/guard-alpha-summary` response fields `cumulative_saved_dollars_net_of_friction` / `saved_dollars_realized_net_of_friction`. Own dedicated DOM elements (`#dollar-saved-net-of-friction-headline`/`-verb`, `#dollar-saved-realized-net-of-friction-headline`/`-verb`), never overwriting the gross `headlineEl`/`realizedHeadlineEl` variables above. Reuses the IDENTICAL `DE-GAS-COHERENCE-001` display contract (ABS magnitude, no naked sign character, sign-conditional `'saved'`/`'lost'` word + `--studio-pos`/`--studio-neg` color) -- each net line's sign is independent of its gross sibling's own sign, which is the entire point of the fix (a real window can be gross-positive/net-negative once the optimizer's own `SIM_EXIT_FRICTION_PCT=0.5` friction floor is subtracted).

**Empty-state gating (review-found gap, fixed at commit `40b130e3`):** the initial GREEN commit (`ed4eebee`) rendered both net-of-friction blocks unconditionally -- so a `guard_event_count===0` / `realized_coverage.with_data===0` empty state could show a spurious `"$0.00"` on the net line right beside the gross line's honest `"No guard events yet"` / `"no realized data yet"`. `bl3review` caught this; `bl3test` pinned it RED via a real Node execution harness (source extracted by brace-counting, run against a synthetic DOM + stubbed `fetch()`, asserted on actual `textContent` -- a source-text-only assertion can't distinguish "genuinely gated on `guard_event_count`/`with_data`" from "the guard string happens to appear nearby"). Fixed by folding BOTH net-of-friction render blocks into the SAME `if`/`else` empty-state structure the gross lines already use -- the snapshot net line inside the `data.guard_event_count === 0` branch, the realized net line inside the `coverage.with_data === 0` branch. A genuine non-empty zero result (guard events exist, net-of-friction happens to compute to exactly `0.0`) still renders `"$0.00"` honestly, since gating is now on the SAME condition as the gross line, never on the net figure's own numeric value (two positive-control tests pin this, guarding against overcorrection).

The "gross of trading costs" caveat qualifying the gross headline(s) is a STATIC caption in `templates/index.html` (never JS-injected) -- mirrors the existing static "(marks basis)" caption pattern. See `docs/generated/app.md`'s `GET /api/guard-alpha-summary` section for the route-side friction-subtraction formula and `DE-AUDIT-BL3-001` in `DECISIONS.md` for the full record.

### `fetchWindowedStrip(token)`
Fetches `GET /api/strip/<token>` for a time-window button click. Re-windows the hero guard-alpha headline and the comparison rows (Bot / Held / Delta) by wrapping the strip dict as a pseudo-poll payload and calling `renderGuardAlpha` and `updateComparisonRows`. Errors are logged to console and silently swallowed (the dashboard retains its prior state on strip failure). As of `DE-GAS-COHERENCE-001`, the window-picker click handler calls this AND `fetchGuardAlphaSummary(token)` on the same click (see above) -- this function itself carries zero diff.

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
|----------|-------|--------------|
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
- `GET /api/guard-alpha-summary` — dollar-saved panel data (both snapshot and, as of exit-friction-realized-savings, the additive realized/marks-basis figure); windowed as of `DE-GAS-COHERENCE-001`
- `GET /api/strip/<token>` — windowed strip for the time-window picker
- `GET /api/hero-chart/<token>` — hero chart re-windowing (separate fetch on picker click)
- `GET /api/chart/<sym.id>` — per-symphony chart history, feeding both `renderSparkline` and `renderMcDial`; F7 threads `sym.triggered` onto this payload before the `renderMcDial` call (see above)

## Related Surfaces (not this file)

- **`templates/table_partial.html`** — main-table MC Prob column header tooltip corrected this cycle (`DE-MATH-F7-001` AC-3: "Monte Carlo probability this symphony underperforms its own regime-matched historical baseline", replacing the inaccurate "beats SPY" claim) and the cell body (`:98`) already rendered `"---"` for a `None` `mc_prob` before this cycle — no code change needed there. No dedicated `docs/generated` page exists for this template (matches the existing no-template-page precedent, e.g. `static/ai_advisor_logic_changes.js`); its render contract is documented here and in `INDEX.md`'s module-index prose instead of a standalone file. **CONFIRMED orphaned, not live (f7-dash finding, independently confirmed by f7-review's own call-path falsification):** this template has no live DOM consumer since the card-SPA redesign removed the `morphdom` injector that used to inject its output — a discrepancy with the audit's own premise, which counted the main table as live. F7's tooltip fix there is template-correct regardless but may not currently reach the operator; see `DE-MATH-F7-001`'s backlog items (delete-vs-rewire decision + the unrelated pre-existing dead `openChartModal()` reference at `:170`, both out of F7 scope).
