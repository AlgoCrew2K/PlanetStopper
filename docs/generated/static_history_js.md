# static/history.js

> Client-side logic for the History tab: fetches `/api/history/<days>` and renders the hero stat strip, the daily-alpha SVG bar chart (with per-day click-to-drilldown), the by-reason breakdown cards, and the recent-triggers table.

**Source:** `static/history.js`
**Last updated:** 2026-07-21 (fix-ops-cluster, `DE-OPS-CLUSTER-001` F-020 -- first doc-gen entry for this file. `renderDailyChart()` gains per-bar `data-date` hooks + click-to-drilldown wiring; new `renderDayDrilldown()`; `loadHistory()` retains the fetched payload in `lastPayload` so drill-down never re-fetches.)

## Overview

`history.js` is the browser-side controller for `templates/history.html` (the `/history` route). It runs as an IIFE with `'use strict'`. All colors are CSS custom properties (`--studio-*`) resolved at runtime via `getComputedStyle` -- no bare hex values, no Tailwind class names.

Key responsibilities:

- **Data fetch + orchestration** (`window.loadHistory(windowVal)`) -- resolves the selected window token to a day count, fetches `GET /api/history/<days>`, and fans the response out to the hero, daily chart, reason cards, and triggers-table renderers.
- **Hero stat strip** (`renderHero()`) -- total alpha, total $ saved, trigger count, win rate; toggles an empty-state block when `trigger_count === 0`.
- **Daily alpha bar chart** (`renderDailyChart()`) -- a hand-built SVG bar chart (no charting library), one bar per trading day, positive bars above and negative below a baseline. Each bar is both hoverable (`<title>` tooltip) and clickable (F-020 drill-down, see below).
- **By-reason breakdown cards** (`renderReasonCards()`) -- canonicalizes the API's free-form exit-reason strings onto the 4 design-spec canonical reasons (`CANONICAL_REASONS`) via `REASON_CANONICAL_MAP`, then renders one card per reason with cumulative alpha, $ saved, trigger/win counts, and a win-rate bar.
- **Recent triggers table** (`renderTriggers()`) -- renders `payload.todays_exits` as table rows; reused (F-020) by the day drill-down to render an arbitrary past day's exits into the same table.
- **Per-day drill-down** (`renderDayDrilldown()`, F-020) -- see the dedicated section below.
- Auto-refresh: `DOMContentLoaded` calls `loadHistory(currentWindow)` once, then polls every 30 s (`setInterval`).

## API Reference

### `window.loadHistory(windowVal)`

Resolves `windowVal` to a day count via `windowDays()` (numeric tokens pass through; `'ytd'` computes days-since-Jan-1), fetches `GET /api/history/<days>`, and on success calls, in order: `renderHero`, `renderDailyChart`, `renderReasonCards`, `renderTriggers`. A fetch failure logs to `console.error` and leaves the previous render in place.

**F-020 (`DE-OPS-CLUSTER-001`, 2026-07-21):** the fetched `payload` is now additionally stored in the module-level `lastPayload` variable before the render fan-out, so `renderDayDrilldown()` can read `payload.daily_exits` without a second network round-trip.

---

### `renderDailyChart(dailyAlpha, dailyDates)`

Builds an SVG bar chart (`viewBox="0 0 1000 100"`) from the `dailyAlpha` array -- bar height is proportional to `|value| / max(|values|)`, positive values drawn above the midline in `--studio-pos`, negative below in `--studio-neg`.

**F-020 signature change (`DE-OPS-CLUSTER-001`, 2026-07-21):** gained a second parameter, `dailyDates` (parallel index-for-index to `dailyAlpha`, sourced from the new `payload.daily_dates` field -- see `docs/generated/analytics.md`'s `get_history_summary` section). Each `<rect>` now carries a `data-date` attribute and an inline `<title>` tooltip when a date is available for that index, plus `style="cursor:pointer;"` signaling it's clickable. After `svg.innerHTML` is set, every `rect[data-date]` gets a real `click` listener wired via `addEventListener` (not an inline `onclick` attribute -- avoids HTML-attribute quoting hazards around the escaped date string) that calls `renderDayDrilldown(rect.getAttribute('data-date'))`. Re-rendering replaces `svg.innerHTML` wholesale on every call, which discards the old `<rect>` elements (and their listeners) along with it -- there is no double-binding across repeated renders/polls.

---

### `renderDayDrilldown(dateStr)` (F-020, `DE-OPS-CLUSTER-001`, 2026-07-21)

Looks up `lastPayload.daily_exits[dateStr]` (absent/empty when the day had zero triggers -- degrades to an empty array, never throws) and re-renders the existing `#triggers-tbody` surface via `renderTriggers()` -- a deliberate reuse of the live "Today's exits" table rather than a parallel render target. Each looked-up exit entry is remapped to the shape `renderTriggers()` expects (`ts` sourced from `time_triggered` with `ts`/absent fallbacks, `symphony_name` falling back to `symphony_id`). After `renderTriggers()` runs (which sets its own "Today's exits (N)" heading first), `renderDayDrilldown` overwrites `#todays-exits-heading` with the selected day's own label (`"<dateStr> exits (N)"`), so the table reads as showing that day's exits, not today's.

**Design choice:** no separate "back to today" affordance exists yet -- the next poll (30 s) or window-picker click re-runs `loadHistory()`, which re-renders `renderTriggers(payload)` with the live `todays_exits` array and restores the original heading. This was a deliberate minimal-scope choice (`feature-plans/fix-ops-cluster.md`'s F-020 remediation line: "MINIMAL: an expandable per-exit detail row... no new analytics, no schema change") -- not a gap.

---

### `renderHero(payload)`

Toggles an empty-state block (`#history-empty-state`) against the hero/daily-strip/by-reason sections when `payload.trigger_count === 0`. Otherwise renders `total_alpha` (2dp %, color-coded), `total_saved` ($, locale-formatted), `trigger_count`, and `win_rate` (1dp %, color-coded at the 50% threshold).

---

### `renderReasonCards(byReason)`

Canonicalizes `byReason` (a `{raw_reason_key: {alpha, count, wins, dollars}}` map, keys in whatever casing/punctuation the producer emitted) onto the 4 design-spec canonical reasons via `canonicalizeByReason()` — `REASON_CANONICAL_MAP` maps every known raw variant (underscore/hyphen/space, multiple synonyms per canonical reason, e.g. `stop_loss`/`parabolic_stop` both map to `'Trailing Stop'`) onto one of `CANONICAL_REASONS`. Canonical reasons with zero triggers are dropped from the render (no empty cards). Each card shows cumulative alpha, $ saved, trigger/win counts, a win-rate mini-bar (`data-testid="reason-bar"`), and the reason's static one-line description (`REASON_DESCRIPTIONS`).

---

### `renderTriggers(payload)`

Renders `payload.todays_exits` (an array of `{ts, symphony_id, symphony_name, reason, detail}` entries) into `#triggers-tbody`, resolving each row's REASON-cell color via the same `REASON_CANONICAL_MAP` lookup `renderReasonCards` uses. Sets `#todays-exits-heading` to `"Today's exits (N)"` first — `renderDayDrilldown()` (F-020) overwrites this heading immediately after calling this function with a past day's data, per the reuse pattern documented above. An empty array renders a single "No exit records for this window." row.

---

### `windowDays(val)` / `canonicalizeByReason(byReason)` / `reasonStripColor(reason)` / `reasonBadge(reason)` (internal helpers)

- `windowDays()` -- numeric window tokens pass through as-is; `'ytd'` computes `Math.ceil((now - startOfYear) / 86400000)`.
- `canonicalizeByReason()` -- the V-28 canonicalization pass described under `renderReasonCards()` above.
- `reasonStripColor()` / `reasonBadge()` -- lookup tables (`REASON_STRIP_COLOR`/`REASON_BADGE`) with a generic fallback (`--studio-accent` / first-4-chars-uppercased) for an unrecognized reason string, so a producer adding a 5th reason degrades gracefully rather than breaking the render.

## Types

- **`CANONICAL_REASONS`** -- `['Take-Profit', 'Trailing Stop', 'VWAP Breakdown', 'VWAP Bleed Cut']`, the 4 exit reasons the design spec (`history.jsx REASON_META`) defines (V-28).
- **`REASON_CANONICAL_MAP`** -- raw-string-variant → canonical-reason lookup table (underscore/hyphen/space variants, multiple synonyms).
- **`REASON_DESCRIPTIONS`** / **`REASON_STRIP_COLOR`** / **`REASON_BADGE`** -- per-canonical-reason static description text, CSS-var color token, and short badge label.

## Internal Dependencies

- `GET /api/history/<days>` -- primary data fetch; response fields consumed: `total_alpha`, `total_saved`, `trigger_count`, `win_rate`, `by_reason`, `daily_alpha`, `daily_dates` (F-020, new), `daily_exits` (F-020, new), `todays_exits` -- see `docs/generated/analytics.md`'s `get_history_summary` section.
- CSS custom properties: `--studio-pos`, `--studio-neg`, `--studio-rule`, `--studio-ink-dim`, `--studio-ink`, `--studio-surface`, `--studio-border`, `--studio-plum`, `--studio-cyan`, `--studio-warn`, `--studio-accent`
- DOM elements (from `templates/history.html`): `#history-empty-state`, `[data-testid="history-hero"]`, `[data-testid="daily-strip"]`, `[data-testid="by-reason-section"]`, `#val-total-alpha`, `#val-total-saved`, `#val-trigger-count`, `#val-win-rate`, `#daily-chart-svg`, `#reason-cards`, `#triggers-tbody`, `#todays-exits-heading`, `#window-picker`
