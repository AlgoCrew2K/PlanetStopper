# Feature plan — Studio design handoff

**Branch:** `feat/studio-design-handoff`
**Trigger:** User exported design canvas from claude.ai/design (handoff bundle in `.design-handoff/`). 5 screens + shared Studio chrome to ship to design parity against live Flask app.
**Mode:** Agent Teams (Quad per project CLAUDE.md). Single team, single worktree, sequential screens — shared foundation forbids parallel-worktree drift.

---

## Scope — every screen in the canvas

| # | Design source (`.design-handoff/project/`) | Flask target | Data backend |
|---|--------------------------------------------|--------------|--------------|
| 1 | `studio.jsx` + `detail-panel.jsx` + `mock-data.js` | `templates/index.html` (overwrite) | `/api/state`, `/api/chart/<sym>`, `/api/logs/<sym>` |
| 2 | `performance.jsx` | `templates/performance.html` (overwrite) | `/api/performance`, `/api/performance/symphonies` |
| 3 | `advisor.jsx` | `templates/ai_advisor.html` (overwrite) | `/ai-advisor/suggest`, `/ai-advisor/accept`, `/ai-advisor/reject`, `/api/autotune-runs` |
| 4 | `history.jsx` | NEW `templates/history.html` + NEW `/history` route | `/api/history/<days>` (extend) |
| 5 | `settings.jsx` | NEW `templates/settings.html` + NEW `/settings` route | `/api/settings` (extend for symphony overrides) |

Shared infrastructure (built once, used by all 5):
- `chrome.jsx` → `templates/_chrome.html` Jinja include (top nav + workspace switcher + tweaks button)
- `tweaks-panel.jsx` → `static/tweaks.js` + `static/tweaks.css` (theme/density/accent/typeface/math-overlays/numFormat — persists to localStorage)
- `studio.jsx` palette tokens → `static/tokens.css` (CSS variables driven by tweaks)
- Mock-data shapes in `mock-data.js` → reference for which fields each screen consumes (validate against actual `/api/state` schema before binding)

---

## Acceptance criteria (master)

**AC-S.1 — Studio chrome.** Top nav present on every screen with workspace switcher, route tabs (Dashboard / Performance / Advisor / History / Settings), tweaks button. Active route highlighted. Tweaks button opens slide-over panel.

**AC-S.2 — Tweaks panel.** Five controls render and persist to localStorage: theme (light/dark), density (compact/balanced/roomy), accent (5 swatches), typeface (Manrope/Geist/DM Sans/Plus Jakarta/IBM Plex), math overlays (toggle), number format (full/compact). Changes apply live without reload.

**AC-S.3 — Palette tokens.** All screens consume `--studio-*` CSS variables; no hardcoded hex outside `tokens.css`. Theme toggle ripples to all screens without per-screen JS.

**AC-S.4 — Dashboard parity.** Studio dashboard renders against `/api/state`:
- Hero with Guard Alpha headline (Bot − If-Held cumulative α), 60-day Bot-vs-Held chart, 3-row comparison (Today / Cumulative / Max DD) with Bot/Held bars + α delta.
- Active vs Standby card sections. Active cards: status pill, good-call/early-exit verdict for triggered, tape with TP/STOP breakline + plum badge, dual-value Bot vs Held headline, stats row, Cash Now button (top-right, 0 clicks), click-to-open detail panel.
- Standby cards: tighter grid.
- Detail slide-over (760px from right) opens on card click: sticky 4-stat row, intraday tape with live/shadow/stop/breakeven/VWAP overlays + MC right axis, today's events timeline, risk math panel, 60d Bot-vs-Held mini chart + comparison cells, all `.env`/SQLite vars with locked-from-autotuner icon + Edit, footer with id + normalized_name. ESC closes.

**AC-S.5 — Performance parity.** `/performance` renders against `/api/performance`:
- Aggregate / Per-symphony toggle, window selector 30d / 90d / 1Y / YTD / 5Y.
- Cumulative-returns chart with divergence shaded between Live (if-held) and Bot (shadow).
- 7-metric Risk table (Total Return, Annualized, Sharpe, Sortino, Max DD, Calmar, Win Rate) × (Live / Bot / Δ).
- `insufficient_history` banner when API reports it.

**AC-S.6 — Advisor parity.** `/ai-advisor` renders against advisor backend:
- Symphony picker.
- Suggestion cards (config key, current → suggested, rationale, confidence + data-sufficiency + OOS-status badges, projected metric impact, Dismiss / Apply).
- Right rail: recent autotune runs with naive Sharpe, DSR, frozen-eval verdict.

**AC-S.7 — History parity (new route).** `/history` route + template:
- 30d / 90d / 1Y / YTD / 5Y window control.
- Rollup tiles: total α, $ saved, trigger count, win rate.
- Daily α strip chart.
- By-reason breakdown (TP / Stop / VWAP Breakdown / Bleed Cut) with description + win rate + avg α/exit per reason.
- Today's exits list.
- Backed by extension of existing `/api/history/<days>` aggregating decisions table.

**AC-S.8 — Settings parity (new route).** `/settings` route + template:
- Vertical side nav (Master / Algorithm / Credentials / Symphony overrides).
- Master controls: LIVE_EXECUTION toggle with prominent danger styling when LIVE.
- Algorithm parameters: editable .env globals with bounds + descriptions from feature-plans/ai-advisor-tuning.md.
- Credentials: masked secret fields with show/hide; write-only persistence.
- Symphony overrides: split pane with lockable per-symphony parameters.

**AC-S.9 — No regressions.** Existing API contracts unchanged. Engine-path code (`alpha_bot_execution.py`, `math_engine.py`, `database.py` writes) untouched. `/api/state`, `/api/chart/<sym>`, all manual-trigger endpoints behave identically.

---

## Team composition

**Quad** (per AlphaBot CLAUDE.md):
- `quant-test-writer` — RED tests per screen (template render, route response, data binding presence)
- `implementer` — GREEN minimal HTML/CSS/JS per screen
- `flask-dashboard-specialist` — Flask routing + Jinja + template wiring (UX-side domain specialist)
- `quant-code-reviewer` — review every cycle

Single shared worktree, single branch `feat/studio-design-handoff`. Cycles handed off via SendMessage.

---

## Sequence

1. **Foundation cycle.** Build `_chrome.html`, `tokens.css`, `tweaks.js`. RED tests assert nav renders on every existing route, tweaks panel mounts, localStorage persistence works.
2. **Dashboard cycle.** Replace `templates/index.html` with Studio layout. Map mock-data.js shapes to `/api/state` actuals (read app.py L222-L688). Build detail-panel slide-over.
3. **Performance cycle.** Replace `templates/performance.html` with Studio Performance.
4. **Advisor cycle.** Replace `templates/ai_advisor.html` with Studio Advisor.
5. **History cycle.** New route + template + extend `/api/history`.
6. **Settings cycle.** New route + template + extend `/api/settings` for overrides.

Each cycle ends with quant-code-reviewer approval + cycle-complete message to PM. PM merges to main after user sign-off.

---

## Functional parity (not visual reskin)

**User clarification 2026-05-18:** delivering the designs requires *adjusting architecture and functionality*, not just restyling templates. The mock-data shapes in `.design-handoff/project/mock-data.js` describe the **target functional contract**; backends must be extended (additively) to emit those shapes.

Known architecture/functionality deltas (non-exhaustive — teams discover the rest during cycle scoping):

- **AI Advisor (advisor.jsx):** suggestion cards carry `four_gates_verdict` (allowlist / risk-direction / OOS-frozen-eval / locked-vars), `confidence`, `data_sufficiency`, `oos_status`, `projected_impact: {metric, before, after, delta}`. Current `/ai-advisor/suggest` likely doesn't return that envelope. Restructure the response (additive). Recent autotune runs rail needs `/api/autotune-runs` to return `{study_name, naive_sharpe, dsr, frozen_eval_verdict, completed_at}` per run.
- **Dashboard detail panel (detail-panel.jsx):** intraday tape needs **shadow continuation** series (the dashed "what-if-held" line that diverges after the trigger marker), breakeven-lock window, VWAP series, MC % series on the right axis. `/api/chart/<sym>` may currently only emit one line — extend to emit overlays.
- **Dashboard cards (studio.jsx):** per-symphony "good call / early exit" verdict with α delta belongs on the card. Source field: `triggered_at_return` vs `current_return` delta — present in DB but may not surface via `/api/state`. Add to the response.
- **Dashboard hero (studio.jsx):** Bot-vs-If-Held comparison rows (Today / Cumulative / Max DD) consume `portfolio_strip.{today_change, cumulative_return, max_drawdown}.{dry_run, if_held}`. Per chat transcript, app.py already emits these — just surface them. Verify before binding.
- **History (history.jsx):** new route. Backend extension: `/api/history/<days>` returns `{total_alpha, dollars_saved, trigger_count, win_rate, daily_alpha_strip[], by_reason: {tp, stop, vwap_breakdown, bleed_cut}, todays_exits[]}`. Aggregates from `decisions` + `post_mortems` tables.
- **Settings (settings.jsx):** new route. Four sections (Master / Algorithm / Credentials / Symphony overrides). Algorithm params need bounds + descriptions metadata (sourced from `feature-plans/ai-advisor-tuning.md`). Symphony overrides need per-symphony GET/POST with `locked_by_autotuner` flag per param. Existing `/api/settings` extends additively.
- **Studio chrome (chrome.jsx):** workspace switcher implies multi-account routing in the URL (`/?account=<uuid>`). Confirm current dashboard already supports account scoping; if not, extend route filter to honor `?account=`.

**Discovery is part of the cycle.** Each cycle starts with the test-writer running a `data-contract-recon` step: grep the existing API for the design's required fields, list gaps, and decide per-gap whether to (a) add the field to the existing response, (b) add a new endpoint, or (c) drop the field if it would require engine changes (engine code is off-limits — see hard rules). Decisions logged in `docs/handoff/cycle-<N>-contract.md`.

## Non-goals

- Production-quality React build (designs are HTML/CSS prototypes; we mirror the visual output in Jinja, not the React structure).
- **Engine code changes.** `alpha_bot_execution.py`, `math_engine.py`, `synthetic_history.py`, `autotuner.py`, `reporting.py`, and write paths in `database.py` are off-limits even when delivering functional parity. If a design requires data the engine doesn't produce, drop the design element or surface a TODO in the contract doc.
- **API contract breakage.** Extensions are additive. Existing field names/types/shapes must be preserved.
- **DB schema breakage.** New columns/tables are additive-first with NULLable + DEFAULT.
- Force-push or destructive history rewrites.
