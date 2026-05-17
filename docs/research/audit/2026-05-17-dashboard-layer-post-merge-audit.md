# Dashboard-Layer Post-Merge Audit — 2026-05-17

Scope: Flask + Jinja + JS surfaces touched by H1 (trigger telemetry), M1F
(shadow_divergence + Shadow Performance widget), DM (market-state banner +
/api/state extensions), V3 (fleet_correlation_alert banner + dismiss), plus
pre-existing dashboard surfaces that interact with them.

Mode: read-only research; main @ 0228a37. No code or template changes proposed
in this audit document — issues are recorded for the PM to triage.

---

## 1. H1 — Trigger telemetry surface

### 1.1 Per-symphony "Last trigger" cell
**VALIDATED.** `templates/table_partial.html:180-182` renders a "<reason> @
<HH:MM>" sub-line under the Status badge when `sym.last_trigger` is set.
Server side `app.py:351-362` attaches today's most-recent trigger row per
symphony from `database.get_triggers(since=<today>T00:00:00Z, limit=500)`.
Jinja autoescape (default on for `.html`) protects against XSS in
`triggered_reason` and `ts_et`.

### 1.2 Aggregate triggers strip
**VALIDATED.** `templates/index.html:208-213` declares the `triggers-strip`
container and `templates/index.html:1031-1051` fetches `/api/triggers` and
groups by `triggered_reason` into pill counts. Strip is `hidden` by default and
revealed only when rows exist (line 1049). Reason→class lookup
(`_TRIGGER_REASON_COLORS`, line 1024-1029) falls back to neutral slate styling
on unknown reasons.

### 1.3 GET /api/triggers query params
**VALIDATED.** `app.py:433-452` accepts `since`, `symphony_id`, `reason`, and
`limit`. `database.get_triggers` (`database.py:812-845`) applies each filter as
a parameterised SQL clause, clamps `limit` to 500 server-side, and excludes
`account_id` from the SELECT projection per fixture spec
(`tests/fixtures/telemetry/api_triggers_filter_params.json:58-61`).

### 1.4 Read-only contract
**VALIDATED.** Both `app.py:433` (`/api/triggers`) and the embedded
`get_triggers` call inside `/api/state` (line 353) are pure reads. No
`save_state`, no engine invocation.

### 1.5 90-day retention
**VALIDATED.** `app.py:191-202` registers a daily 02:00 job (`run_scheduler`,
line 206) that calls `database.prune_old_triggers(retention_days=90)`. The
prune is batched (1000-row LIMIT) per `database.py:848-877` to avoid long
write-lock contention with the engine's per-minute writes. UI relies on
`since=<today>T00:00:00Z` rather than full-table scans, so retention size has
no UI impact.

### 1.6 ISSUE (Low) — SQLite connection is not opened read-only
`database.get_connection()` (`database.py:29-30`) opens the state DB as a
read-write handle. The project CLAUDE.md and the flask-dashboard-specialist
agent both require `mode=ro` for UI routes. None of the UI routes
(`/api/triggers`, `/api/state` shadow_divergence read, `/api/logs`, etc.) use a
read-only handle. Pre-existing pattern across the file; H1 inherited it. Not a
regression introduced by H1.

---

## 2. M1F — Shadow Performance widget

### 2.1 Widget rendering + position
**VALIDATED.** `templates/index.html:215-223` declares
`shadow-performance-strip` directly after the H1 triggers strip (line 209), so
DOM order is: fleet banner → market banner → portfolio strip → triggers strip →
shadow strip. The widget has `style="max-height:48px;overflow:hidden;"`
satisfying the 48 px height constraint.

### 2.2 Sign-convention legend + null guard
**VALIDATED.** Inline legend at `templates/index.html:219-220` documents
"helped" (emerald) vs "cost" (rose). JS rendering at
`templates/index.html:961-989`: line 970 filters entries with `d.today != null`;
line 977 renders a slate-coloured em-dash pill when `v == null` (the post-
filter defensive guard is redundant but harmless).

### 2.3 "If Held Return" column header
**VALIDATED.** `templates/table_partial.html:62` sets the column header text to
"If Held Return", satisfying the M2 + M1F cross-cycle compromise.

### 2.4 /api/state.shadow_divergence key shape
**VALIDATED in live path; key-remap correct in frozen path.**
- Live path (`app.py:392-395`) calls `database.get_shadow_divergence` which
  returns `{by_symphony: {<id>: {today, cumulative}}, portfolio_today}`
  (`database.py:772-809`).
- Frozen path (`app.py:228-231`) remaps the engine-written `portfolio` key →
  `portfolio_today` so the JS contract is consistent across live/frozen.
- Waiting path (`app.py:251-255`) returns the same shape, with empty
  `by_symphony` on first-deploy failure.

### 2.5 ISSUE (Low) — `cumulative` field is a placeholder, always None
`database.get_shadow_divergence` returns `"cumulative": None` for every
symphony (`database.py:804`). No JS code reads `cumulative` today, so this is
contract-shape-only. Document or remove before any downstream consumer assumes
it carries a real number.

---

## 3. DM — Market-state banner

### 3.1 Banner position
**VALIDATED.** `templates/index.html:183-185` places `market-state-banner` in
DOM after the V3 fleet banner (line 171) and before the portfolio strip
(line 188). Initial class includes `hidden` to suppress flash-of-unstyled
state before first poll.

### 3.2 Three render states
**VALIDATED.** JS at `templates/index.html:847-871` renders:
- `open` → "Market Open" + emerald styling.
- `pre_market` → `Pre-Market — frozen at <ts>` or fallback to `notice`.
- closed/unknown → `Market Closed — frozen at <ts>` or fallback.

### 3.3 Auto-refresh via /api/state poll
**VALIDATED.** `templates/index.html:1981` calls `setInterval(fetchState,
5000)`. `fetchState` hits `/api/state` which returns the current `market_state`
and `frozen_at`. Banner is re-rendered on every poll.

### 3.4 EXECUTION_START_TIME vs market_state separation
**VALIDATED.** `market_calendar.py:1-7` explicit docstring confirms
`get_market_state` is dashboard-only and the engine gate remains
`EXECUTION_START_TIME`. `app.py:403` exposes `execution_start_time` separately
in the response, and the JS uses it only to populate the "Exec:" clock label
(`templates/index.html:908`).

### 3.5 First-deploy notice
**VALIDATED.** `app.py:260-262` injects `notice = "No closing snapshot yet —
waiting for first market close at 16:00 ET."` into the waiting response when
state is empty AND market is closed/pre. JS at `templates/index.html:835-843`
updates the banner even in the early-return waiting branch.

### 3.6 ISSUE (High) — Frozen-snapshot path serves a malformed `state` field
`app.py:237` sets `"state": snapshot.get("data_as_of")` — a date string
(`"YYYY-MM-DD"`), not the live state object. Client code at
`templates/index.html:904` does `const stateObj = data.state;` and then
`stateObj.date` (line 907) plus `Object.keys(stateObj).filter(...)` (line 910).
Passing a string here:
- `stateObj.date` resolves to `undefined`.
- `Object.keys("2026-05-16")` returns `["0","1","2",...,"9"]` — 10 fake symphony
  keys — which then each get `.armed`, `.account`, etc. accessed on a string
  character and silently no-op, but `total-symphonies` will read `10`.
The frozen path also omits `html`, so the symphony table goes blank (the
`if (data.html)` guard on line 927 skips morphdom). Result: on weekends / after
16:00 ET, the dashboard header shows "Tracked: 10 / Armed: 0 / Triggered: 0"
with an empty table. UX-impacting.
Recommended fix scope: either (a) restore `state` to the snapshot's
accounts_map / state_data subset, or (b) gate the JS table-render path on
`market_state === 'open'`.

### 3.7 ISSUE (Low) — Frozen snapshot stores all-None portfolio_strip
`alpha_bot_execution.py:736-740` writes `today_change/cumulative_return/
max_drawdown = None` into the snapshot, and `/api/state` returns the same
unchanged in the frozen path. JS formatter renders these as "---". By design
per the engine comment ("the live analytics path in app.py computes these; the
engine captures structure only") — but the frozen path never recomputes them.
Result: on closed days the portfolio strip is permanently dashes. Acceptable
if intentional; document or move analytics into the frozen branch.

---

## 4. V3 — Fleet-correlation banner

### 4.1 Banner position above DM banner
**VALIDATED.** `templates/index.html:169-178` declares
`fleet-correlation-banner` (rose-red) immediately before
`market-state-banner` (line 183). Distinguishable styling: red-900/60 bg vs
slate-700/60 (closed) or emerald-900/40 (open).

### 4.2 Auto-clear when alert becomes None
**VALIDATED.** JS at `templates/index.html:873-890` reads
`data.fleet_correlation_alert` on every poll; when falsy the banner is
re-hidden via `classList.add('hidden')`.

### 4.3 POST /api/fleet-alert/dismiss
**VALIDATED contract; ISSUE (High) on implementation.** Route at
`app.py:455-463` returns `{"status": "ok"}` with 200 and removes the
`fleet_correlation_alert` key from state. Idempotent when no alert is present.
Test coverage at `tests/dashboard/test_fleet_banner.py:209-293` exercises 200,
clear, JSON shape, idempotence.
**Problem:** the route performs a read-modify-write of the entire `bot_state`
JSON blob WITHOUT acquiring `execution_lock`. The engine subprocess writes
`bot_state` on every cycle (every minute). A dismiss POST that races the
engine's `save_state` can clobber the engine's just-written cycle output —
losing `high_water_mark` updates, newly armed states, new trigger flags, etc.
This violates the read-only-operator-surface invariant and is a latent live-
state corruption path. Severity: High because it can silently revert engine
progress.
Recommended fix: either (a) wrap dismiss in `database.acquire_lock()` /
`release_lock()`, (b) move the dismiss flag out of `bot_state` into a dedicated
single-row table that the engine never writes, or (c) atomic UPDATE that only
NULLs the JSON field rather than rewriting the whole blob.

### 4.4 High-visibility styling
**VALIDATED.** Red-900/60 background + red-500/60 border + uppercase
tracking-widest text; visually loud and matches the AC-V3.1 brief.

---

## 5. /api/state route extensions

### 5.1 Top-level fields present
**VALIDATED.** All three response branches expose `market_state`, `frozen_at`,
`shadow_divergence`, `fleet_correlation_alert`:
- Active live: `app.py:397-411`.
- Frozen snapshot: `app.py:232-242`.
- Waiting / fresh-deploy: `app.py:252-263`.

### 5.2 Branch logic
**VALIDATED.** `closed_frozen` and `pre_market` with a stored snapshot →
frozen branch (line 224-226). `open` → live branch. Empty `state_data` falls
through to waiting branch.

### 5.3 Backward compatibility
**VALIDATED.** New keys are added top-level and never replace existing keys.
JS code that does not consume them will not break (they are simply ignored
during JSON.parse). The only contract change in `state` is documented in §3.6.

---

## 6. Read-only operator-surface invariant

### 6.1 No new live-trading mutation paths
**VALIDATED.** None of H1, M1F, DM introduces a new endpoint that affects
trading state. /api/triggers and /api/state are read-only.

### 6.2 Dismiss endpoint scope
**ISSUE (High) — same as §4.3.** Although the dismiss is conceptually visual-
only, its implementation re-writes the live `bot_state` blob and races the
engine. Treats a UI-only flag as if it were live-state. Cited above.

### 6.3 XSS / Jinja autoescape
**VALIDATED.** Flask + Jinja autoescape is on by default for `.html`
templates. Reviewed data-driven fields:
- `sym.last_trigger.triggered_reason` (table_partial:181) — autoescaped.
- `data.notice`, `data.frozen_at`, `data.market_state` — written via JS
  `textContent` (lines 842, 858, 863, 868) which is XSS-safe.
- Fleet alert text — written via `textContent` (line 884), XSS-safe.
- Shadow strip pills — built with template literals into `innerHTML`
  (line 984). Symphony IDs and the formatted percentage are passed unescaped.
  IDs come from the engine (controlled) and `v.toFixed(2)` is numeric, so
  practical XSS risk is low; nominal risk if a symphony ID ever sources from
  Composer JSON without sanitisation.
- H1 trigger-strip pills — `reason.toUpperCase()` injected via `innerHTML`
  (line 1047). Same pattern; reason values are engine-controlled enums.

### 6.4 ISSUE (Med, pre-existing) — Secrets in /api/settings GET
Outside H1/M1F/DM/V3 scope but inside the dashboard-layer audit remit:
`app.py:744-746` echoes `COMPOSER_SECRET` and `ALPACA_SECRET` in plaintext to
the settings modal. Only `ANTHROPIC_API_KEY` is masked (line 752). Recommend
extending the mask to all secret fields and surfacing "set / not set" only.

---

## 7. Templates discipline

### 7.1 SQLite read-only opens
**ISSUE (Low) — repeated from §1.6.** Templates themselves do not touch SQLite
directly (good). The Flask routes that feed them open standard read-write
handles. Move to `sqlite3.connect("file:...?mode=ro", uri=True)` in dashboard
read paths.

### 7.2 Templates do not rerun the engine
**VALIDATED.** No template calls `alpha_bot_execution`, `math_engine`, or
`autotuner`. Server-side per-symphony TC/CR/MDD are computed via
`analytics.*` only (`app.py:368-379`).

### 7.3 Graceful None / missing-field render
**VALIDATED.**
- `templates/table_partial.html:153-159` formats percent fields as `---` when
  `tc_dr/tc_ih/cr_dr/cr_ih/mdd_dr/mdd_ih` are None.
- JS portfolio strip formatter `fmtPct` (`templates/index.html:993`) returns
  `'---'` on null.
- JS shadow pills (line 977) and triggers list (line 1040) handle empty
  collections by hiding the strip.

### 7.4 ISSUE (Low) — No Jinja template inheritance
Project rule: "Jinja inheritance is mandatory; templates extend a shared base
layout; never duplicate `<head>` / nav boilerplate." Today: `index.html`,
`performance.html`, and `ai_advisor.html` each carry their own `<!DOCTYPE>`,
`<head>`, Tailwind CDN, favicon, and scrollbar CSS. Drift target.

---

## 8. Performance

### 8.1 /api/state response time
**ISSUE (Low) — minor regression vector, not measured.** The live branch now
performs three additional DB reads vs. pre-H1/M1F:
1. `database.get_triggers(since=<today>T00:00:00Z, limit=500)` —
   `app.py:353`.
2. `database.get_shadow_divergence(today_str)` — `app.py:393`.
3. Three `analytics.get_symphony_*` calls per symphony (TC/CR/MDD) —
   `app.py:368-379`.
None of these touch the network. The triggers query is single-table indexed
on `ts_utc`; the shadow_divergence query is a per-symphony correlated subquery
inside a GROUP BY which is `O(symphonies × ticks_today)` — fine at current
scale (≤ 30 symphonies) but worth a synthetic-load check before fleet growth.

### 8.2 Dashboard poll cadence
**ISSUE (Med) — VIOLATES 15-second floor rule.**
`templates/index.html:1981`: `setInterval(fetchState, 5000)`.
The flask-dashboard-specialist agent's rule 5 sets the minimum poll interval
at **15 s** (engine cadence is 1 min). The dashboard currently polls 3× per
engine cycle. The 30-second `fetchTriggerStrip` interval on line 1982 is
within rules. Recommend raising `fetchState` to ≥ 15 s and adding the rule
explainer comment per the agent contract.

---

## Severity summary

| ID | Surface | Severity | Type |
|----|---------|----------|------|
| 3.6 | DM frozen path passes string in `state` field | High | Bug |
| 4.3 / 6.2 | Fleet-alert dismiss races engine save_state | High | Correctness |
| 8.2 | fetchState 5-second poll < 15 s floor | Med | Convention violation |
| 6.4 | /api/settings returns plaintext secrets | Med | Pre-existing security |
| 1.6 / 7.1 | DB connections not read-only | Low | Convention violation |
| 2.5 | shadow_divergence `cumulative` is dead | Low | Dead contract field |
| 3.7 | Frozen snapshot portfolio_strip all None | Low | Documented/by-design |
| 7.4 | No Jinja template inheritance | Low | Convention violation |

## Validations summary

H1 telemetry: 1.1–1.5 VALIDATED. M1F shadow widget: 2.1–2.4 VALIDATED.
DM market banner: 3.1–3.5 VALIDATED (3.6/3.7 have issues). V3 fleet banner:
4.1, 4.2, 4.4 VALIDATED (4.3 contract validated, implementation has High issue).
/api/state shape: 5.1–5.3 VALIDATED. Read-only invariant: 6.1, 6.3 VALIDATED.
Templates: 7.2, 7.3 VALIDATED.

## UX-impacting findings

- **3.6 (High):** Weekend / after-hours dashboard shows "Tracked: 10" with an
  empty symphony table because `state` is a date string and `data.html` is
  absent in the frozen response. Operator confusion likely.
- **3.7 (Low):** Frozen portfolio strip permanently shows "---" until
  reopened — visually indistinguishable from a broken analytics pipeline.
- **8.2 (Med):** 5-second poll cadence means every operator session opens
  ~720 /api/state calls/hour vs. ~240 if the 15 s floor were honoured. Local
  daemon so cost is low, but it tightly couples UI to per-cycle DB locks.
