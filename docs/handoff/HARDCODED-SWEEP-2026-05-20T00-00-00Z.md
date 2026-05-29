> **HISTORICAL** — This document is a pre-Sprint-1 or Sprint-1 cycle artifact preserved for provenance. See [docs/audit/](../audit/) for current state.

---

# Exhaustive Hardcoded-Value Sweep

## Metadata
- Auditor: code-auditor
- Run date: 2026-05-20T00:00:00Z
- Repo: AlphaBotPM — branch `feat/studio-design-handoff`
- Commit SHA: `46c9f161a185726bff0b9b97ed4ede6ce11f4377`
- Scope: `templates/index.html`, `templates/_chrome.html`, `templates/performance.html`, `templates/ai_advisor.html`, `templates/history.html`, `templates/settings.html`, `static/index.js`, `static/chrome.js`, `static/performance.js`, `static/ai_advisor.js`, `static/history.js`, `static/settings.js`, `static/tweaks.js`, `static/tokens.css`, `static/tweaks.css`

Severity key: **BLOCKER** = shows wrong live data to the user; **MINOR** = cosmetic, no live data shown incorrectly; **INFO** = acceptable pattern (not an issue).

---

## Category 1 — Hardcoded Text That Should Be Data-Bound

| ID | File:Line | Hardcoded Value | Should Bind To | Severity |
|---|---|---|---|---|
| HC-TXT-01 | `templates/_chrome.html:51` | `ONLINE · NEXT {{ meta.next_run }}` — the word `ONLINE` is a literal inside the Jinja condition; `CLOSED` and `OFFLINE` branches are also literals | All three strings should come from `meta.market_status_label` or a dedicated `meta.connection_label` field so a server-driven change (e.g. pre-market, extended hours) propagates without a template edit | MINOR |
| HC-TXT-02 | `templates/_chrome.html:66` | Fallback `DRY RUN` in `{% else %}DRY RUN{% endif %}` | When `meta` is undefined (offline), falls back to a hardcoded mode string. Acceptable as an offline default, but should use `meta.mode_label` when `meta` is defined and `meta.mode` is absent. Currently `meta.mode` being `None` still renders `DRY RUN`. | MINOR |
| HC-TXT-03 | `templates/index.html:687` | `AlphaBot (dry-run)` chart legend label | Should derive from `meta.mode` — when live it should read `AlphaBot (live)`. The legend string is a hardcoded mix of product name + mode. | BLOCKER |
| HC-TXT-04 | `templates/index.html:7` | `<title>AlphaBot Dashboard</title>` — no mode or account context in the page title | Should include mode pill text, e.g. `AlphaBot · DRY RUN` | MINOR |
| HC-TXT-05 | `templates/performance.html:7` | `<title>Performance — AlphaBot Dashboard v3</title>` | "v3" is a hardcoded version string. Remove or pull from a server-side `app_version` var. | MINOR |
| HC-TXT-06 | `templates/ai_advisor.html:7` | `<title>AI Config Advisor — AlphaBot Dashboard v3</title>` | Same "v3" issue | MINOR |
| HC-TXT-07 | `templates/history.html:7` | `<title>Guard Alpha History — AlphaBot Dashboard v3</title>` | Same "v3" issue | MINOR |
| HC-TXT-08 | `templates/settings.html:7` | `<title>Settings — AlphaBot Dashboard v3</title>` | Same "v3" issue | MINOR |
| HC-TXT-09 | `static/index.js:204` | `'Good call · saved +'` and `static/index.js:206` `'Early exit · gave up '` | Verdict string literals in JS. Template already has Jinja equivalents (`Good call · saved` / `Early exit · gave up`) — dual maintenance risk. JS overrides on poll; strings must match exactly. Consider extracting to a shared constant or eliminating the Jinja version. | MINOR |
| HC-TXT-10 | `static/index.js:226` | `'Selling…'` button state text; `static/index.js:234` `'In cash'`; `static/index.js:238` `'Cash Now'` | Acceptable UI feedback strings — but `'In cash'` is final state shown permanently until page reload. User may not realise this is done. Non-data-binding issue, flagged for review. | MINOR |
| HC-TXT-11 | `templates/index.html:963` | Static initial text `Symphony Detail` inside `<span id="detail-panel-title">` | Replaced on open by `openDetailPanel`, but visible for a brief flash if panel ever opens before JS hydration. Low risk but not truly static default. | MINOR |

---

## Category 2 — Hardcoded Numbers That Should Be Live

| ID | File:Line | Hardcoded Value | Should Bind To | Severity |
|---|---|---|---|---|
| HC-NUM-01 | `static/index.js:415–416` | MC dial color thresholds `mcProb < 15` and `mcProb > 80` | These are algorithm design constants. They should either be exported from the backend as `meta.mc_warn_threshold` / `meta.mc_dim_threshold`, or centralised in a named constant file. Currently spread across template (SVG) and JS. | MINOR |
| HC-NUM-02 | `static/index.js:397` | `MC_CIRCUMFERENCE = 94.25` (2π×15) | Correct and named — acceptable as a named geometric constant. Radius `15` matches SVG `r="15"` in `index.html:854`. **INFO — not an issue.** | INFO |
| HC-NUM-03 | `static/index.js:534` | `setInterval(loadState, 30000)` — 30 s poll interval | Could drift from server expectation. Acceptable as a client-only config constant, but should be named, e.g. `var POLL_MS = 30000`. Currently a magic number inline. | MINOR |
| HC-NUM-04 | `static/performance.js:402` | `setInterval(refresh, 60000)` — 60 s refresh | Same pattern — unnamed inline interval. | MINOR |
| HC-NUM-05 | `static/ai_advisor.js:465` | `setInterval(loadRecentRuns, 15000)` — 15 s autotune poll | Same pattern. | MINOR |
| HC-NUM-06 | `static/history.js:337` | `setInterval(_historyPoll, 30000)` — 30 s history poll | Same pattern. | MINOR |
| HC-NUM-07 | `static/performance.js:45` | `Math.abs(value) > 1000` — outlier suppression threshold for metrics table formatting | This is an algorithm constant for display formatting. Should be named `MAX_DISPLAY_PCT`. | MINOR |
| HC-NUM-08 | `static/index.js:541–542` | Window map values `252` (1Y) and `1260` (5Y) — hardcoded trading-day counts | The mapping of label → day count is client-side only. If the backend changes what "1Y" means, these diverge silently. Low risk for now. | MINOR |
| HC-NUM-09 | `templates/performance.html:389` | `{{ min_history_days }}` — already data-bound, this is correctly injected by Flask. **INFO — not an issue.** | | INFO |

---

## Category 3 — Hardcoded Colors

| ID | File:Line | Hardcoded Value | Should Bind To | Severity |
|---|---|---|---|---|
| HC-COL-01 | `templates/settings.html:241` | `background: #fff` (`.toggle-knob`) | `var(--studio-white)` — already defined in `tokens.css:54`. | MINOR |
| HC-COL-02 | `templates/index.html:322` | `box-shadow: 0 1px 3px rgba(0,0,0,0.06)` (`.sym-card`) | Shadow-only; acceptable as a shadow value that doesn't track theme. But for dark mode correctness the shadow alpha should be lower on dark. `var(--studio-rule)` used for card hover already — consider consistency. | MINOR |
| HC-COL-03 | `templates/index.html:427` | `background: rgba(0,0,0,0.35)` (`.detail-panel-scrim`) | Not tokenised. In dark mode a dark scrim over a dark background is invisible. Should be `var(--studio-scrim, rgba(0,0,0,0.35))` with a dark-mode override, or replace with a themed scrim token. | MINOR |
| HC-COL-04 | `templates/_chrome.html:80` | `box-shadow: 0 2px 8px rgba(0,0,0,0.12)` (tweaks floating button) | Shadow-only; same minor dark-mode concern as HC-COL-02. | MINOR |
| HC-COL-05 | `templates/settings.html:242` | `box-shadow: 0 1px 2px rgba(0,0,0,0.18)` (`.toggle-knob`) | Shadow-only; acceptable but inconsistent with token system. | MINOR |
| HC-COL-06 | `static/tweaks.css:18` | `box-shadow: 0 1px 0 rgba(255,255,255,0.5) inset, 0 12px 40px rgba(0,0,0,0.18)` | Shadow-only fallbacks. The inset white highlight is inappropriate in dark mode (double bright). | MINOR |
| HC-COL-07 | `static/tweaks.css:154` | `box-shadow: 0 1px 2px rgba(0,0,0,0.25)` (toggle knob) | Shadow-only; acceptable. | MINOR |
| HC-COL-08 | `static/tweaks.css:34,47,88,101,170,171,182` | Multiple `rgba(...)` fallbacks inside `var(--studio-*, rgba(...))` | These are fallback values within `var()` calls where the primary token is now always defined. The fallbacks are dead code but harmless. | INFO |
| HC-COL-09 | `static/tweaks.js:11` | `|| '#1f7a4d'` (accent fallback) | Defensive fallback only; `getComputedStyle` will return empty string if CSS not yet loaded. Acceptable. **INFO.** | INFO |
| HC-COL-10 | `static/performance.js:25` | `return 'rgba(31,122,77,' + alpha + ')'` (hexToRgba fallback) | Fallback when hex parse fails — only reached if `--studio-pos` returns an unparseable value. Acceptable defensive fallback. **INFO.** | INFO |
| HC-COL-11 | `static/ai_advisor.js:331–332` | `|| '#6366f1'` (accent fallback) and `|| '#334155'` (border fallback) | CSS var fallbacks; same pattern as HC-COL-09. Acceptable. **INFO.** | INFO |
| HC-COL-12 | `templates/performance.html:22,27` / `templates/history.html:15,19` / `templates/ai_advisor.html:15,19` | `var(--studio-scroll-thumb, #334155)` / `var(--studio-scroll-thumb-hover, #475569)` scrollbar fallbacks | `--studio-scroll-thumb` is now defined via F-COD-01 alias; fallbacks are dead code. No immediate risk. | INFO |

---

## Category 4 — Hardcoded Placeholder / Sample Data

| ID | File:Line | Hardcoded Value | Should Bind To | Severity |
|---|---|---|---|---|
| HC-PLC-01 | `templates/index.html:1025` | `<div class="event-item">No events yet today.</div>` — static skeleton in HTML | Replaced by `renderDetailLogs` on panel open, but visible between panel-open and API response. `renderDetailLogs` in `static/index.js:382` also re-renders this exact string after the API returns empty. Both are intentional empty-state messages, but the one in the HTML template is never actually seen as a "loading" state — it persists until the fetch completes. Should render a loading indicator first, then replace. | MINOR |
| HC-PLC-02 | `templates/index.html:1033` | `<div class="var-row"><span class="var-key">No vars loaded</span></div>` | Same pattern as HC-PLC-01 — static skeleton, replaced on API response. | MINOR |
| HC-PLC-03 | `templates/index.html:976–989` | All six `<div class="detail-stat-value" id="dp-*">--</div>` initial `--` values | Correct empty-state defaults; replaced by `openDetailPanel`. **INFO.** | INFO |
| HC-PLC-04 | `templates/performance.html:446–465` | Metrics table skeleton rows with hardcoded `--` in every cell | Replaced by `renderMetrics` on page load. **INFO — correct loading pattern.** | INFO |
| HC-PLC-05 | `templates/performance.html:369` | `<option value="" disabled selected>Loading...</option>` symphony picker | Replaced by `loadSymphonies` on DOMContentLoaded. **INFO.** | INFO |
| HC-PLC-06 | `templates/ai_advisor.html:303` | `Select a symphony and click Run Advisor to get suggestions.` — static placeholder text | This is a correct empty-state instruction, not a data placeholder. **INFO.** | INFO |
| HC-PLC-07 | `static/index.js:258` | Fallback string `'Symphony Detail'` when `sym.normalized_name` and `sym.name` are both absent | Acceptable last-resort fallback. **INFO.** | INFO |

---

## Category 5 — Hardcoded States / Flags

| ID | File:Line | Hardcoded Value | Should Bind To | Severity |
|---|---|---|---|---|
| HC-STT-01 | `templates/settings.html:602` | `data-live="false"` on `#live-card` (initial render) | Correct SSR default — `renderMaster()` in `settings.js:84` replaces this immediately on load. **INFO.** | INFO |
| HC-STT-02 | `templates/settings.html:607` | `data-live="false"` and text `DRY RUN` on `#live-pill` (initial render) | Same as HC-STT-01 — overwritten by `renderMaster`. **INFO.** | INFO |
| HC-STT-03 | `templates/settings.html:620` | `aria-checked="false"` and `data-on="0"` on `#live-toggle` (initial render) | Same as HC-STT-01. **INFO.** | INFO |
| HC-STT-04 | `templates/_chrome.html:107` | `aria-checked="true"` on swatch-1; `aria-checked="false"` on swatches 2–5 | Baked initial state. `studioTweaks` restores from `localStorage` on DOMContentLoaded via `applyAll`, but `aria-checked` attributes are NOT updated by `applyAll` or `tweaks.js`. If the user saved swatch-3 as accent, swatch-1 still renders with `aria-checked="true"`. **This is a stale aria-state bug.** | BLOCKER |
| HC-STT-05 | `templates/_chrome.html:137` | `data-on="1"` and `aria-checked="true"` on the Math Overlays toggle | `tweaks.js:applyAll` sets `data-math-overlays` on `<html>` but does NOT update the toggle's `data-on` or `aria-checked`. If user saved `mathOverlays: false`, the toggle renders "on" but the overlays are hidden. Visual state lies about the persisted setting. | BLOCKER |
| HC-STT-06 | `templates/_chrome.html:101` | `<option value="balanced" selected>Balanced</option>` — density default baked in HTML | `applyAll` in `tweaks.js` sets `data-density` on `<html>` but does NOT update the `<select>` `selected` attribute. If user saved "compact", the select still shows "Balanced". | BLOCKER |
| HC-STT-07 | `templates/_chrome.html:143` | `<option value="full" selected>Full</option>` — number format default baked in HTML | Same pattern as HC-STT-06. If user saved "compact", select shows "Full". | BLOCKER |
| HC-STT-08 | `templates/index.html:669` | `class="active"` on `data-window="30d"` button (30d hard-selected as default) | The JS poll loop on DOMContentLoaded calls `loadState()` which calls `renderHeroChart` with the full history — the window selector `active` class is a visual default only and JS never re-syncs it to the actual rendered window. On first load the chart shows all data while "30d" appears active. | BLOCKER |
| HC-STT-09 | `templates/performance.html:362` | `class="active" aria-pressed="true"` on Aggregate scope button | Correct initial state — `performance.js` honours this on init. **INFO.** | INFO |
| HC-STT-10 | `templates/performance.html:376` | `class="active" aria-pressed="true"` on 60d window button | Correct initial state — `performance.js` default window is 60 days. **INFO.** | INFO |
| HC-STT-11 | `templates/history.html:266` | `class="active"` on 90d window button | Correct initial state — `history.js` `currentWindow = '90'`. **INFO.** | INFO |

---

## Category 6 — Hardcoded Config

| ID | File:Line | Hardcoded Value | Should Bind To | Severity |
|---|---|---|---|---|
| HC-CFG-01 | `static/chrome.js:45` | `timeZone: 'America/New_York'` | This is the correct timezone for US market hours and is a legitimate constant. **INFO.** | INFO |
| HC-CFG-02 | `static/chrome.js:12,20` | `setTimeout(..., 2000)` — force-run button reset delay | Named config candidate. Acceptable as a UI feedback constant. | MINOR |
| HC-CFG-03 | `static/settings.js:71` | `setTimeout(() => load(false), 400)` — startup race retry delay | Internal implementation detail. Acceptable. | INFO |
| HC-CFG-04 | `static/settings.js:92` | `|| '09:30'` — execution start time fallback | Fallback for when `EXECUTION_START_TIME` is absent from the API response. Should match the actual server default (`database.DEFAULT_STRATEGY` or env default). If the server default changes, this diverges silently. **BLOCKER** if they get out of sync and the user sees a stale value in the UI. | BLOCKER |
| HC-CFG-05 | `static/index.js:534` | `30000` (30 s dashboard poll interval) | Unnamed magic number inline in DOMContentLoaded. Name it: `var POLL_INTERVAL_MS = 30000`. | MINOR |
| HC-CFG-06 | `static/performance.js:402` | `60000` (60 s performance poll) | Same — unnamed magic number. | MINOR |
| HC-CFG-07 | `static/ai_advisor.js:465` | `15000` (15 s autotune poll) | Same. | MINOR |
| HC-CFG-08 | `static/history.js:337` | `30000` (30 s history poll) | Same. | MINOR |

---

## BLOCKER Summary (items that show wrong data or wrong state to the user)

| ID | File:Line | Issue |
|---|---|---|
| HC-TXT-03 | `templates/index.html:687` | Legend says "AlphaBot (dry-run)" even in LIVE mode |
| HC-STT-04 | `templates/_chrome.html:107–119` | Swatch `aria-checked` not updated on page load from saved accent — screen readers and tests see wrong active swatch |
| HC-STT-05 | `templates/_chrome.html:137` | Math overlays toggle `data-on`/`aria-checked` not restored from localStorage — toggle lies about its persisted state |
| HC-STT-06 | `templates/_chrome.html:101` | Density `<select>` not populated from localStorage — shows "Balanced" regardless of saved setting |
| HC-STT-07 | `templates/_chrome.html:143` | Number format `<select>` not populated from localStorage — shows "Full" regardless of saved setting |
| HC-STT-08 | `templates/index.html:669` | "30d" window button marked active on load but chart renders full history — visual state contradicts data |
| HC-CFG-04 | `static/settings.js:92` | Execution start time fallback `'09:30'` may silently diverge from server default |

---

## MINOR / INFO Summary

- **MINOR** findings (cosmetic or naming): HC-TXT-01/02/04–11, HC-NUM-01/03–08, HC-COL-01–07, HC-PLC-01/02, HC-CFG-02/05–08
- **INFO** (acceptable patterns): HC-NUM-02/09, HC-COL-08–12, HC-PLC-03–07, HC-STT-01–03/09–11, HC-CFG-01/03

**Total findings:** 41
**BLOCKER:** 7
**MINOR:** 22
**INFO (not actionable):** 12
