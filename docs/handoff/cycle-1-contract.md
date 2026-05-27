> **HISTORICAL** — This document is a pre-Sprint-1 or Sprint-1 cycle artifact preserved for provenance. See [docs/audit/](../audit/) for current state.

---

# Cycle 1 — Foundation: Data Contract Recon

**Date:** 2026-05-18  
**Surfaces:** `templates/_chrome.html`, `static/tokens.css`, `static/tweaks.js`, `static/tweaks.css`  
**Design source:** `chrome.jsx`, `tweaks-panel.jsx`, `studioPalette()` in `studio.jsx`

---

## Fields consumed by chrome.jsx (TopBar component)

| Field (JSX path) | Source in design mock-data | Live `/api/state` status | Decision |
|---|---|---|---|
| `data.meta.account.label` | `meta.account.label = "Roth IRA"` | NOT in `/api/state` — `account_labels` is a dict keyed by account UUID | **EXTEND**: Add `meta.account` obj `{label, uuid_short}` to `/api/state` active response |
| `data.meta.account.uuid_short` | `meta.account.uuid_short = "880be47e"` | NOT in `/api/state` | **EXTEND**: derive from first account key in `accounts_map`, truncate to 8 chars |
| `data.meta.mode` | `"DRY RUN"` / `"LIVE"` | Present as `live_mode: bool` | **EXTEND**: add `meta.mode` string: `"LIVE"` if `live_mode` else `"DRY RUN"` |
| `data.meta.system_online` | `meta.system_online: bool` | NOT present explicitly | **EXTEND**: derive as `status == "active"` |
| `data.meta.next_run` | `meta.next_run = "00:00"` | Present as `next_run_seconds: int` | **EXTEND**: add `meta.next_run` as mm:ss formatted string from `next_run_seconds` |
| `data.meta.market_state` | `meta.market_state = "closed"` | Present as `market_state` top-level key | **EXTEND**: add to `meta` block |
| `data.meta.market_state_label` | `"Market closed · frozen at 16:00:01 ET"` | NOT present | **EXTEND**: human label; derive from `market_state` + `frozen_at` |
| `data.meta.clock_et` | `"06:17:07 PM ET"` | NOT present | **EXTEND**: server-side current ET clock string |
| `data.meta.tracked` | count of symphonies | Derivable from `state` object keys | **EXTEND**: add `meta.tracked` count |
| `data.meta.armed` | count armed | NOT in API currently | **EXTEND**: add `meta.armed` count (armed + tp_armed + para_armed) |
| `data.meta.triggered` | count triggered | NOT in API currently | **EXTEND**: add `meta.triggered` count |
| `data.meta.triggers_today` | `{trailing_stop: N, take_profit: N}` | NOT in API | **DROP** — triggers_today breakdown requires querying decisions table; scope separately. Note in TODO. |

## Fields consumed by chrome.jsx (PageShell navigation)

| Field | Status | Decision |
|---|---|---|
| Active route tab highlight | Derived from current URL | **EXTEND**: `_chrome.html` include receives `active_route` Jinja var from each route handler |
| Navigation items: Dashboard / Performance / Advisor / History / Settings | History + Settings routes not yet live | History + Settings routes created in cycles 5 & 6; chrome include just links to them |

## Tweaks panel (tweaks-panel.jsx → tweaks.js + tweaks.css)

All tweaks are client-side only. No backend contract needed. Persists to `localStorage` key `alphabot_tweaks`.

| Tweak key | Design default | Backend dependency |
|---|---|---|
| `theme` | `"light"` | None — CSS vars flip |
| `density` | `"balanced"` | None |
| `accent` | `"#1f7a4d"` | None |
| `typeface` | `"Manrope"` | None |
| `mathOverlays` | `true` | None |
| `numFormat` | `"full"` | None |

## tokens.css — palette CSS variables

All `--studio-*` vars are derived from `studioPalette()` in `studio.jsx`. No backend dependency. The palette function is purely client-side (theme toggle), output embedded as CSS custom properties in `tokens.css` + updated by `tweaks.js` on theme change.

---

## Gaps summary

**EXTEND** (additive additions to existing `/api/state` response):
- Add top-level `meta` object containing: `account {label, uuid_short}`, `mode`, `system_online`, `next_run` (mm:ss), `market_state`, `market_state_label`, `clock_et`, `tracked`, `armed`, `triggered`

**DROP** (engine-touching or out of scope for Cycle 1):
- `meta.triggers_today.trailing_stop` / `.take_profit` — requires decisions table query not yet in scope

**NEW-ENDPOINT**: None needed for Foundation.

---

## Acceptance criteria for Cycle 1

**AC-C1.1** — Every existing Flask route (`/`, `/performance`, `/ai-advisor`) returns HTTP 200 and the rendered HTML contains `<nav data-studio-chrome>` (or equivalent nav landmark with `data-testid="studio-nav"`).

**AC-C1.2** — `static/tokens.css` exists and contains at least the `--studio-bg`, `--studio-paper`, `--studio-ink`, `--studio-accent` CSS variables.

**AC-C1.3** — `static/tweaks.js` exists and exports (or sets) a `studioTweaks` key in `localStorage` with the 6 default keys on first load.

**AC-C1.4** — `GET /api/state` returns a JSON body containing a `meta` key with sub-keys: `mode` (string), `system_online` (bool), `tracked` (int), `armed` (int), `triggered` (int).

**AC-C1.5** — `GET /api/state` `meta.mode` is `"LIVE"` when `live_mode=true`, `"DRY RUN"` when `false`.

**AC-C1.6** — `GET /api/state` `meta.next_run` is a `"mm:ss"` formatted string when `next_run_seconds` is present.

**AC-C1.7** — Nav tabs include links to: Dashboard (`/`), Performance (`/performance`), Advisor (`/ai-advisor`), History (`/history`), Settings (`/settings`).

**AC-C1.8** — The active route tab has `aria-current="page"` (or `data-active="true"`) on the matching nav item.

**AC-C1.9** — Tweaks button is present in `_chrome.html` with `data-testid="tweaks-btn"`.

**AC-C1.10** — No hardcoded hex color values outside of `tokens.css` (tested by grepping rendered templates for inline hex outside allowed token-file).
