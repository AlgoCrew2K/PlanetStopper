# Approach: UX overhaul suite, the contracts every plan shares
Spec: feature-plans/ux-overhaul/00-overview.md
Ref: PlanetStopper@37c8e983b64c6980c28199a3f9318486b60f3864, PlanetStopper@7c0b7432587392b83465d004bfb7e8cf457af77b (branch `ux-overhaul/fixtures`, captures only), PlanetStopper@291454eb46f484c0f9048f18a92346c4e4dddc68 (branch `model-unpin/fixtures`, the model-unpin plan, approach and captures only)

## Architecture

This file holds each contract that more than one plan approach reads, once, with its owner (the plan cycle that builds it) and its consumers. A plan approach cites the contract by its SC id and never restates it; where a plan approach says something different, this file names the difference under Items Raised to the PM. Every `file:line` is at 37c8e983 unless it names a fixtures branch. The overview has no acceptance criteria, scenarios or rule matrix of its own; each plan's criteria are threaded in its own approach.

### SC-1: the `/` readiness contract (owner 05 C1)

Consumers: 05 (every capture), 01 (C7 settings modal captured on `/`), 02 (C1 S-4.18 capture and its merge gate), 04 (C5 S-2.6 and its merge gate), and the overview's Suite Gate items 3 and 4.

- `body[data-dashboard-ready="1"]` is written by `static/index.js` through `document.body.dataset.dashboardReady`, once per page load, and is never removed or reset. It is set when both hold: the hero chart has finished its first draw or `#hero-chart-empty` is shown; and every `[data-testid="card-spark"][data-sym-id]` canvas present at first paint holds a chart that has finished its first draw, or shows its `.spark-status` text.
- "Finished its first draw": Chart.js's entry animation is kept (PM ruling; the default line-growth animation of the unpinned tag at `templates/index.html:11` stays). A chart counts as drawn when the first `animation.onComplete` after its construction fires. When that first render runs no animation (for example a browser reduced-motion setting), it counts as drawn when that render ends. No chart is constructed with `animation: false` to make readiness easier.
- `body[data-refresh-count]` is absent until the first refresh settles, then reads `1`, and grows by one each time a `loadState()` call (`static/index.js:1469`) settles. A call settles when its renderers in `updateDashboard` (`static/index.js:1380-1413`) have run, or its request failed and the failure was handled, and every chart that refresh constructed or updated has drawn by the rule above. Updates use `update('none')`, which draws without animation; a canvas first seen during a refresh is constructed with its entry animation and is waited for. A failed refresh still increments the count, so a capture's wait never hangs on a refused request; the capture records the failure separately.
- No other page carries either attribute. Captures of other pages wait on the selectors each plan's checklist names, never on network idle (the 15 s and 30 s polls keep the network busy).
- Capture rules, for every plan: wait on `body[data-dashboard-ready="1"]`, never on network idle and never on a server-rendered selector such as `.sym-card`, which exists before any chart; reach refresh N by the 30 s poll or by `window.loadState()` through the browser's evaluate call (a GET), then wait on `body[data-refresh-count="N"]`; with the launcher's scheduler off there is no `cycle-complete` event; record `typeof Chart` and `Chart.version` once per page, and report an undefined `Chart` as BLOCKED, not judged. The browser loads Chart.js and the fonts from their CDNs itself; the launcher's refusal of the server's outbound network does not block them.

### SC-2: one rounding rule for text the server and the client both write (owner 05 C2)

Consumers: 05 C2 (card verdict, MC dial, fleet banner); 01 C3 (`analytics.format_dollar_saved` formats its magnitude through it, so the History page's `dollarSavedText` and the Python figure agree on every tie). Any later cycle that server-renders text a client renderer later rewrites in the same element consumes it.

- The client rule is the reference: `Number.prototype.toFixed`, which rounds the exact binary value with ties away from zero. The server writes such text only through `analytics.js_to_fixed(value, places)`, placed by 05 C2 in `analytics.py` beside `format_dollar_saved` (`analytics.py:2260-2272`), never in `app.py`, because `analytics.py` cannot import `app.py` and `format_dollar_saved` calls it: `Decimal(value).quantize(..., ROUND_HALF_UP)`, equal to `toFixed` for finite values below 1e21, negative zero included (`(-0.04).toFixed(1)` and the server both give `-0.0`).
- Never used for such text: Jinja `"%.Nf"|format`, Python `round`, f-string `:.Nf` (all round exact ties half to even: 0.25 to one place gives `0.2` against the client's `0.3`), and client `Math.round` on a value that can be negative (ties toward positive infinity: `Math.round(-2.5)` is `-2` against `(-2.5).toFixed(0)` `-3`). `Math.round` stays only where the value cannot be negative, as in 05's fleet percentage.
- One Python implementation exists, `analytics.js_to_fixed`; `app.py` imports it, and a second mirror of `toFixed` (05's approach places a private `_js_to_fixed` in `app.py`) is a defect. Search that found none at the ref: `grep -rn "ROUND_HALF_UP" --include=*.py .` outside `.claude/worktrees` (05 Architecture finding 6).
- Cross-render tests enumerate the tie family at each precision used: 0.25, 0.75, -0.25 and the near-zero pair 0.04, -0.04 at one place; 0.125 at two; 0.5 and -0.5 at zero.

### SC-3: the ET time formatter (owner 02 C2)

Consumers: 02 (Prism as-of, source publish times, lens stamp, autotune card time, Retirement tiles, pointer and earlier-run note), 03 (the Correlations as-of from the stored `run_at`, the empty-state times).

- Python `analytics.format_et_stamp(value, *, naive_is_utc=True, date_only=False) -> str` beside `analytics.format_dollar_saved` (`analytics.py:2260-2272`); Jinja filter `et_stamp` registered by `app.add_template_filter` after the Jinja settings (`app.py:80-86`); JavaScript `fmtEtStamp(value, opts)` in `static/ai_advisor.js` after `fmtSharpe` (`static/ai_advisor.js:37-40`). Search that found no existing formatter: Grep `def .*fmt.*(et|time|stamp)` and `timeZone` finds only the header clock (`static/chrome.js:357-368`).
- Output `YYYY-MM-DD HH:MM ET` in America/New_York, labelled `ET` in both summer and winter time; `date_only` gives the ET date `YYYY-MM-DD`. Minutes are truncated, never rounded; no seconds and no fractions.
- Input is an instant in the shapes the producers write: SQLite `datetime('now')` text with no zone (`created_at`, `migrations/017_advisor_observations.sql:12`; captured value `2026-10-22 18:50:25` on `ux-overhaul/fixtures@7c0b7432`, `tests/fixtures/retirement/run_summary_captured.json`), ISO with `Z` (`run_at`, `advisors/retirement_recommender.py:534`; captured `2026-10-22T18:50:08Z`), ISO with an offset, and six-digit fractions (`run_timestamp`). A zone-less value is read as UTC when `naive_is_utc` is true, the default and the rule for every stamp the app writes; it is false only for feed publish times, which then render as their date only, unconverted.
- `None`, empty and unparseable values return the missing-value character the panel already prints (`templates/ai_advisor.html:1615`); the formatter never raises and never echoes its input.
- A calendar date without a time of day (a series first or last date, a post-mortem day, a window start) is not an instant: it is printed as stored and never passed to the formatter, because reading `2026-10-21` as UTC midnight and converting it to ET prints `2026-10-20`, the one-day shift plan 03 AC-9 removes from `advisors/composer_series_client.py`. The formatter returns a date-only input unchanged (Items Raised 11).
- The JavaScript side parses with a regular expression, never `Date.parse`, and converts with `Intl.DateTimeFormat` in America/New_York, as `static/chrome.js:357-368` does. One parity test runs both sides over the same captured inputs.
- Scheduled slots run in the host's local zone (`schedule.every().day.at("03:45")`, `app.py:1440`), and the droplet's zone is UTC (`timedatectl` Etc/UTC, recorded in `tests/fixtures/retirement/PROVENANCE.md` on `ux-overhaul/fixtures@7c0b7432`). Copy that names a scheduled time states the host zone or converts it; the formatter is for stored instants, not for schedule strings.
- The dashboard's own `HH:MM ET` stamps (`app.py:2310`, `:2403`) already follow SD-2 and are unchanged (overview NB-11).

### SC-4: the exclusion-reason sentence mapping (owner 02 C3)

Consumers: 02 C3 (Retirement tab excluded list), 03 C3 (Correlations excluded list, S-5.5 compares the two per symphony).

- One callable, `_retirement_exclusion_reason_sentence(reason) -> str` in `app.py` beside `_retirement_resolve_excluded_symphonies` (`app.py:4439-4490`), reading the reason literals from `advisors/retirement_recommender.py:189-192` by a lazy import so the mapping never re-declares them. Search that found no mapping: Grep `series_too_short|no_account|fetch_error|shadow_series_unavailable` outside the recommender.
- The resolver calls it and stamps `reason_text` on every entry it already returns (`{symphony_id, name, reason, reason_text}`); both tabs render `reason_text` through autoescape and nothing else. No Jinja macro, JavaScript map or second Python mapping exists. The stored summary is never rewritten.
- The family every test enumerates: the four literals (`no_account`, `series_too_short`, `fetch_error: ` followed by its code, which the sentence keeps, `shadow_series_unavailable`); an unmapped reason, rendered as `Excluded: ` plus the raw reason, escaped; an empty reason, which is also what the resolver passes for a non-string (`app.py:4478-4485`), rendered as `Reason not recorded.`. Plan 02 AC-7 owns the sentence wording.
- 03 calls the resolver on the run its matrix renders (`_fetch_retirement_cards_run`, `app.py:4505`), which can differ from the Retirement tab's last run; the sentence for one reason is identical on both tabs either way.

### SC-5: the dashboard primitives live in `static/layout.css` (owner 02 C1)

Consumers: 02 C3 to C6 (Retirement tab), 03 C3 and C4 (`.legend-as-of`, `.section-header`), 04 C3 to C5 (every page, and deletion of every page-local copy including `templates/ai_advisor.html:595-599`).

- `.hero-section` (`templates/index.html:27-32`), `.legend-as-of` (`:174-178`), `.mini-stats`, `.mini-stat`, `.mini-stat-value`, `.mini-stat-label` (`:277-304`), `.section-header` (`:307-314`), `.sym-card` and `.sym-card:hover` (`:323-336`), `.card-name` (`:345-349`) and `.status-pill` with every modifier (`:352-383`) move byte-for-byte, in their order, to the end of `static/layout.css` (`:1-37` at the ref) and are deleted from `templates/index.html`.
- `static/layout.css` is already linked by all six templates (`templates/index.html:10`, `templates/history.html:11`, `templates/performance.html:11`, `templates/settings.html:11`, `templates/ai_advisor.html:11`, `templates/login.html:9`), so no plan adds a `<link>`; 04's "linked on History, Performance and Settings when not already" is satisfied with no edit. Login and the chrome use none of the classes, so login gains rules but no element changes.
- After 02 C1 no plan defines one of these selectors anywhere else. A page style block may add only layout properties beside a primitive; 04's `cursor` override on a non-clickable `.sym-card` is the one value override named so far.
- The `/` structure and resolved-rule comparison seam is 02 C1's `tests/ui/test_dashboard_primitives_shared.py` with its base capture under `tests/fixtures/dashboard/primitives_baseline/` (built on the CSS helpers of `tests/ui/test_page_wrapper_contract.py:138-166`); 04 C5's S-1.7 and S-2.6 reuse it against 04's own base. 04's "What the code does" item 3 ("`static/layout.css` shares only the page inset") is stale once 02 C1 lands.

### SC-6: the `#<tab>` fragment on `/ai-advisor` (owner 02 C3)

Consumers: 02 (the Overview Retirement pointer `href="#retirement"`, Run now's reload), 03 (the Correlations tab is addressed as `/ai-advisor#correlations` in captures and the PM's live check), 04 (the tab bar moves into the first `.hero-section` and keeps every `role="tab"` and `data-tab` attribute the switcher reads), the Suite Gate's live test.

- Fragment values are the rendered tabs' `data-tab` names: `overview`, `correlations`, `asset-swaps`, `logic-changes`, `chat`, `strategy-builder`, `frontrunner-builder` (`templates/ai_advisor.html:1227-1285`), and `retirement` once 02 C3 adds it.
- The switcher (`static/ai_advisor.js:646-686`, an IIFE inside the `DOMContentLoaded` handler at the ref) moves to module scope as `initTabSwitcher()` with `activateTabByName(name)` and a pure `tabNameFromHash(hash, knownNames)` that returns a name only when it equals a rendered tab's `data-tab`, else `overview`. No selector or id is built from the fragment (overview DC-7).
- A tab click writes the fragment with `history.replaceState` (no history entry, no scroll); a `hashchange` listener activates the named tab; `retirementRunNow` sets `#retirement` before its `location.reload()`. Any other reload on the page now lands on the tab the operator was on.
- The server render is unchanged: Overview is the active tab at first paint and the fragment applies at `DOMContentLoaded`. The GET sub-routes that 302 to `/ai-advisor` (for example `/ai-advisor/correlations`, `app.py:7204`) are unchanged and carry no fragment. Grep of `static/` at the ref finds no reader of `location.hash`.

### SC-7: the `browser` pytest marker (owner 01 C6)

Consumers: 01 (C6 `tests/ui/test_settings_controls_browser.py`, C8 `tests/ui/test_display_name_escaping_sweep.py`), 04 (C1 to C5 Playwright modules), and any later cycle that drives a real browser from pytest. The capturer's MCP-driven captures are not pytest and are not marked.

- Registered in `pyproject.toml` `markers` (`:90-94`) as `browser: drives a real browser through Playwright; deselected by default, run at the merge gate with -m browser`, and added to the default deselection in `addopts` (`pyproject.toml:84-89`): `-m 'not live and not slow and not perf and not browser'`. CI installs no Playwright (`.github/workflows/tests.yml:20-27`; `requirements-dev.txt` lists none), so CI never selects it (PM ruling).
- A browser module imports Playwright inside a fixture, never at module level, so CI's collection of the module succeeds without Playwright and the deselected tests never run. No `importorskip`, no `skipif`: the zero-skip mandate (`tests/meta/test_zero_skip_xfail_close.py`) holds, and a selected test with Playwright or its server missing fails with a message naming what is missing. The ref's only Playwright module (`tests/ui/test_advisor_card_overflow.py:22`, `importorskip` and a daemon `skipif`) is not the pattern.
- Engine: Firefox headless, the audit's browser (`.claude/review/37c8e983b64c/ux-audit-report.md:3`) and the ref's existing pattern; Playwright 1.59 with Firefox and Chromium is installed on the PM host.
- Server: a test that seeds its own data starts the app in process through `tests/ui/_browser_server.py` (01 C6: `werkzeug.serving.make_server("127.0.0.1", 0, app)` on the per-worker DB). A test that needs the production copy reads the launcher's base URL from the environment variable `UX_LAUNCHER_URL` and fails when it is unset; the PM sets it, with the mode SC-8 names for that module.
- Run: `python -m pytest -m browser -n 0 <module paths>` on the PM host, one pytest invocation at a time (`pyproject.toml` notes), never `-o addopts=""`, which strips the live filter; output saved under the head's review folder. A pair runs its own browser modules before its cycle-complete when its host has Playwright; the merge gate runs every browser module of the plan.
- 01 C6 also commits a meta test pinning the marker's registration, its default deselection, and that no file under `tests/` imports `playwright` at module level (Items Raised 13).

### SC-8: launcher modes for screenshot gates (provided by the PM)

Consumers: every UI head of 01 to 05, and the Suite Gate's item 3. The launcher is a session-local PM tool, never committed; this is the contract it meets. Before giving a URL to a capturer or setting `UX_LAUNCHER_URL`, the PM confirms the mode's properties below on that instance and puts the mode, URL, served SHA and snapshot time in the brief.

Mode A, real-data capture (the audit's instance, `.claude/review/37c8e983b64c/ux-audit-report.md:3`):
1. Serves the reviewed worktree's code at a named SHA on 127.0.0.1 only.
2. Reads a copy of the production state database and a copy of the post-mortem files from the same snapshot; never the production files. The snapshot time is named in every capture record.
3. Outbound network from the server process is refused (Composer, Alpaca, Anthropic, Discord and every news source), and the `.env` the served code loads (`ENV_FILE_PATH`, beside `app.py`, `app.py:69`, `:78`) holds no Composer or Anthropic key, as the audit instance had none.
4. The scheduler is off: no minute engine spawn, no nightly tick, no `cycle-complete` event.
5. Auth is bypassed, so pages render without a login.
6. Every operator-action POST is refused by the launcher before it reaches the app, so nothing in the copy changes while it serves. A test that needs a POST's answer takes it from the Flask test client; a capture that needs a click to send nothing intercepts it in the browser (04's `page.route` pattern).
7. A plan may seed the copy before launch only through a committed test-support function that writes through the production write path (03 C2's `seed_run_from_captures` in `tests/advisors/_series_captures.py`); the capture record names the function and its input.

Mode B, auth and CSRF on (for 04's login, theme and liquidation-refusal rows):
1. Mode A's items 1, 2, 4 and 7 hold.
2. The auth hook (`app.py:193`) and the CSRF hook (`app.py:489`) are enabled. A launcher-only dashboard password and secret key, read from the environment (`app.py:132-150`), are set by the PM and given to the capturer in the brief; they are never committed or reused elsewhere.
3. Reachable POSTs: `/login`, `/logout` and `/api/sell_account`; every other operator-action POST stays refused as in Mode A.
4. The `.env` beside the served `app.py` and the `.env` in the server's working directory both hold a blank `COMPOSER_KEY_ID`, a blank `COMPOSER_SECRET`, a blank `DISCORD_WEBHOOK_URL` and `LIVE_EXECUTION=False`. Both files matter: `load_dotenv(ENV_FILE_PATH)` fills the environment from the first (`app.py:78`), and `sell_account` reads the second by file, `dotenv_values(".env")` (`app.py:5468`), not the environment. A pair's worktree, which carries a copy of the real `.env` (project worktree rule), is therefore never served in Mode B as it stands.
5. Outbound network from the server process is refused.
6. Consequence: a request that passes the four confirmation checks (`app.py:5455-5466`) stops at the `Missing credentials or account ID.` 400 (`app.py:5471-5472`), before the Discord call (`app.py:5476-5488`), the ERROR log and the dry-run or liquidation branch (`app.py:5497-5528`). Live, Mode B shows the confirmation 400s the client lets through, the missing-credentials 400 and the CSRF 403 (blank page token); the 200 dry-run answer is not reachable there and is proven only by the node harness over the route's real body (04 S-9.7).
7. The PM's pre-handoff check in Mode B: an unauthenticated `GET /` redirects to `/login`, and a logged-in `POST /api/sell_account` with matching confirmation fields answers that 400.
8. Ten failed logins from one address lock it for 300 s (`_AUTH_MAX_ATTEMPTS`, `_AUTH_LOCKOUT_SECONDS`, `app.py:126-129`); live wrong-password checks stay below that count or the launcher restarts between runs.

What every launcher capture records: mode, URL, served SHA, snapshot time, browser and version, viewport, theme, and `Chart.version` where Chart.js loads. Themes are set through the stored tweak value `static/tweaks.js` reads (`static/tweaks.js:17-30`), never by editing CSS.

### SC-9 and SC-10

Branch topology and merge order are in Dispatch Ordering; fixture rules are under Per-Cycle Test Plan, PM.

## Criterion Thread

The overview has no acceptance criteria, so no row names one. Each row threads a shared contract to the requirement items it serves, the design element at the ref, the seam that pins it, and the Dispatch Ordering row that delivers it.

| AC | R items | Design element (file:line, or "new: <reason and search>") | Test seam (test file:line; fixture and provenance; harness pattern) | Cycle |
|---|---|---|---|---|
| none (SC-1 readiness) | R-4, R-7, R-66 | `static/index.js:1588-1601` (`DOMContentLoaded` load sequence), `:1380-1413` (`updateDashboard`), `:1469` (`loadState`); new attributes, search `grep -rn "dataset.ready\|data-ready" static templates` finds none | 05 C1's new `tests/dashboard/test_dashboard_charts_first_paint.py` and `test_dashboard_charts_refresh.py` on the new `tests/dashboard/_index_js_harness.py` (whole-file node run; its `Chart` stub fires `animation.onComplete` only when the test says so), extending the extractor of `tests/dashboard/test_account_basis_honesty_render.py:373-389`; live render by the capturer in Mode A | 05 |
| none (SC-2 rounding) | R-10, R-68 | new: `analytics.js_to_fixed` after `analytics.py:2272`, called by `analytics.format_dollar_saved` (`analytics.py:2260-2272`) and by `app.py`; search `grep -rn "ROUND_HALF_UP" --include=*.py .` finds no mirror of `toFixed` | 05 C2's new `tests/dashboard/test_card_verdict_dial_first_paint.py`: server text equals node `verdictText` over the SC-2 tie family, plus 01 C3's `tests/analytics/test_format_dollar_saved.py` against node `dollarSavedText` over the same ties; extractor pattern `tests/dashboard/test_account_basis_honesty_render.py:373-389`; FX-EXIT-ROW and FX-BOTSTATE from `tests/fixtures/prod_droplet/` | 05 |
| none (SC-3 ET formatter) | R-13, R-49, R-54 | new: `analytics.format_et_stamp` after `analytics.py:2272`, filter after `app.py:86`, `fmtEtStamp` after `static/ai_advisor.js:40`; search Grep `def .*fmt.*(et\|time\|stamp)` and `timeZone` finds only `static/chrome.js:357-368` | 02 C2's new `tests/analytics/test_format_et_stamp.py` on the pattern of `tests/analytics/test_format_dollar_saved.py`, Python and node parity over `tests/fixtures/ai_advisor/ux_overhaul_02/time_values.json` (each value captured from its producer's stored row) | 02 |
| none (SC-4 exclusion sentences) | R-50, R-56 | `app.py:4439-4490` (resolver stamps `reason_text`); new `_retirement_exclusion_reason_sentence` beside it; literals `advisors/retirement_recommender.py:189-192` | `tests/app/test_retirement_run_panel_render.py:323` (02 C3 S-7.1 to S-7.6; 03 C3 S-5.1 to S-5.5 on the same harness); exclusions from the real recommender over the `ux-overhaul/fixtures` captures | 02 |
| none (SC-5 primitives home) | R-5, R-46, R-60, R-61 | `templates/index.html:27-383` rules moved to the end of `static/layout.css:1-37` | 02 C1's new `tests/ui/test_dashboard_primitives_shared.py` with helpers of `tests/ui/test_page_wrapper_contract.py:138-166`; 04 C3 S-2.5 at `tests/ui/test_page_wrapper_contract.py:326`; base capture `tests/fixtures/dashboard/primitives_baseline/` produced at 02's base by the committed capture script | 02 |
| none (SC-6 tab fragment) | R-1, R-46 | `static/ai_advisor.js:646-686` (switcher hoisted), `:1210-1286` (`retirementRunNow`); tab names `templates/ai_advisor.html:1227-1285` | 02 C3's new `tests/app/test_retirement_tab_client.py` (node harness for `tabNameFromHash` over every rendered name, an unknown name, an empty fragment and a fragment holding selector characters), harness pattern `tests/dashboard/test_account_basis_honesty_render.py:373-503` | 02 |
| none (SC-7 browser marker) | R-4, R-6 | `pyproject.toml:84-94` (addopts and markers); new `tests/ui/_browser_server.py` (no in-process browser server exists at the ref; the only Playwright module, `tests/ui/test_advisor_card_overflow.py:22`, needs a running daemon) | new meta test under `tests/meta/` (01 C6): `browser` registered, deselected by default, no module-level `playwright` import under `tests/`; the default `python -m pytest --collect-only -q` on a host without Playwright collects every browser module | 01 |
| none (SC-8 launcher modes) | R-4, R-18, R-73 | new: PM-provided host tool; no launcher is tracked in the repository (the audit's launcher is an untracked file in the primary checkout) | every capture record under `.claude/review/<head-sha12>/` names mode, URL, served SHA, snapshot time, browser, viewport and theme; Mode B pre-handoff check per SC-8 item 7; 04 C2's `tests/app/test_chrome_header_honesty.py` carries the 200 dry-run body Mode B cannot show | PM |
| none (SC-9 branch topology) | R-21, R-70, R-71 | `feature-plans/ux-overhaul/00-overview.md:176-182` (Stack and Merge Rules) | at each PR: `git merge-base --is-ancestor <model-unpin merge commit> <plan tip>` exits 0, `git log --merges` shows each consumed tip entering by a merge commit, and no forced update is recorded on the branch | PM |
| none (SC-10 fixture rules) | R-9, R-77 | `tests/fixtures/prod_droplet/capture_from_droplet.py:1-30` (existing capture path); `ux-overhaul/fixtures@7c0b7432` `tests/fixtures/composer/series_captured/PROVENANCE.md` and `tests/fixtures/retirement/PROVENANCE.md`; `model-unpin/fixtures@291454eb` `tests/fixtures/anthropic/PROVENANCE.md` | every fixture directory carries a PROVENANCE.md read by qa; `plan-trace check-coverage` per cycle; a derived record's row stays held until the PM's sign-off is recorded in its PROVENANCE.md | PM |

## Execution Model

This file dispatches no team. Each plan approach sizes its own team and keeps its pairs in their own worktrees under `.claude/worktrees/`, with `.env` copied in and `git rev-parse --show-toplevel` and `git rev-parse --abbrev-ref HEAD` checked before any edit (project worktree rule). The shared contracts land with their owner cycles: 05 C1 (SC-1), 05 C2 (SC-2), 01 C6 (SC-7), 02 C1 (SC-5), 02 C2 (SC-3), 02 C3 (SC-4, SC-6). The PM provides SC-8's two launcher modes, assembles the branches of SC-9 and commits the captures of SC-10; no worker commits to a fixtures branch, runs a launcher or merges a plan tip.

Concurrency: 05's three cycles and model-unpin's cycles run at once; 01's cycles start when model-unpin is on main; 02's C1 to C3 start from the `ux-overhaul/05` tip, and its C4 and C5 once plan 01's tip is merged into `ux-overhaul/02`; 03 after 02; 04 after 03. One pytest invocation runs at a time on the PM host (`pyproject.toml` worker notes), including every `-m browser` run, and never with `-n auto` or more than 4 workers. A Mode B launcher never serves a pair's worktree as it stands, because that worktree carries the real `.env` (SC-8 Mode B item 4).

Team size: 0 = no roles (this file dispatches nothing; the plans size their own: 01 34, 02 22, 03 14, 04 21, 05 17)

## Dispatch Ordering

At suite level a row is a plan's assembly branch (or the PM's or model-unpin's work), not a cycle; each plan's own Dispatch Ordering orders its cycles inside that branch.

| Cycle | Consumes (cycle: committed artifact) | Branch base |
|---|---|---|
| MU | | main@37c8e983b64c; the model-unpin plan and approach are on `model-unpin/fixtures@291454eb`; its cycles land on main first |
| PM | | main@37c8e983b64c; `ux-overhaul/fixtures@7c0b7432` holds the PM's captures (SC-10); the launcher modes (SC-8) are a host tool, not a branch |
| 05 | | main@37c8e983b64c; C1 to C3 merge into `ux-overhaul/05`; main is merged into that tip once MU is on main |
| 01 | MU: model-unpin merged on main; 05: `analytics.js_to_fixed` (SC-2, 05 C2), read by 01 C3 | main at the model-unpin merge commit; C1 to C8 merge into `ux-data-truth/all`; when 05 is not on main as C3 starts, C3 first merges the `ux-overhaul/05` tip (or 05 C2's head) into its branch by a merge commit |
| 02 | 05: `ux-overhaul/05` tip (`templates/index.html` after 05, from which 02 C1 moves the primitives); 01: `ux-data-truth/all` tip (plan 01's card label, display names and note text that 02 C4 and C5 carry) | C1 to C3 fork from the `ux-overhaul/05` tip; plan 01's tip is then merged into `ux-overhaul/02` by a merge commit, and C4 and C5 fork after it; C6 follows its own Consumes (C3, C4, C5); every head merges into `ux-overhaul/02` |
| 03 | 02: `ux-overhaul/02` tip (SC-3, SC-4, SC-5, SC-6 and the Retirement tab); PM: `ux-overhaul/fixtures` tip (series captures and the captured run summary) | the `ux-overhaul/02` tip with `ux-overhaul/fixtures@7c0b7432` merged in by a merge commit; C1 to C4 merge into `ux-overhaul/03` |
| 04 | 03: `ux-overhaul/03` tip (every template after 01, 02, 03 and 05) | the `ux-overhaul/03` tip; C1 to C5 merge into `ux-overhaul/04-app-shell` |

Rules for every branch above:
1. Model-unpin first: it lands on main before 01 forks; 05 forks earlier. Before any plan's PR opens, model-unpin is merged into its tip, checked with `git merge-base --is-ancestor`.
2. No rebase and no force push anywhere; every consumed tip enters by a merge commit. When a lower plan changes after an upper plan forked (a review fix on 01, say), the PM merges the new lower tip into the upper branch by a merge commit.
3. One PR per plan, from its assembly branch, and every PR targets main. A cycle head is never a PR of its own.
4. Stack order (PM ruling): model-unpin, then 05, then 01 (after model-unpin), then 02 (after 05 and 01 are on main), then 03, then 04, each opened and merged after the plan before it is on main. Merge commits only, never a squash, because a squashed lower plan leaves its original commits in every branch above it and that branch then re-applies them.
5. The Symphony Tenure Review branch `feat/tenure-review-c1` touches the Retirement panel 02 moves; whichever lands second carries the merge (overview Stack and Merge Rules; plan 02 Q-1).

## Per-Cycle Detail

- 05 C1 (SC-1): `static/index.js` writes `data-dashboard-ready` and `data-refresh-count` by SC-1's rules; first production use of both attributes. Every chart keeps its entry animation.
- 05 C2 (SC-2): `analytics.js_to_fixed` in `analytics.py` beside `format_dollar_saved`, imported by `app.py` for the verdict, dial and banner text; first production use of one rounding rule on both sides.
- 01 C3 (consumes SC-2): `analytics.format_dollar_saved` formats its magnitude through `analytics.js_to_fixed`; C3 merges `ux-overhaul/05` into its base first when 05 has not reached main.
- 01 C6 (SC-7): `pyproject.toml` `markers` and `addopts` (`:84-94`), new `tests/ui/_browser_server.py`, the meta test of SC-7; first production use of the `browser` marker.
- 02 C1 (SC-5): `templates/index.html` loses the primitive rules, `static/layout.css` gains them; `tests/app/test_dashboard_m2fix.py:202` reads `layout.css`.
- 02 C2 (SC-3): `analytics.py`, `app.py` (filter), `static/ai_advisor.js` (`fmtEtStamp`).
- 02 C3 (SC-4, SC-6): `app.py` (`_retirement_exclusion_reason_sentence`, `reason_text` on the resolver's entries), `static/ai_advisor.js` (switcher at module scope, `tabNameFromHash`, `activateTabByName`, the `#retirement` fragment before Run now's reload).
- 03 C3: consumes SC-3, SC-4, SC-5, SC-6; adds no second formatter, mapping or primitive.
- 04 C1 to C5: consume SC-5 (no new link), SC-7 (marks every Playwright module `browser`), SC-8 Mode B (S-3.7, the live parts of S-6.1 to S-6.5, S-9.6) and SC-1 (every `/` capture).
- PM (SC-8, SC-9, SC-10): the two launcher modes; the branches above; the captures on `ux-overhaul/fixtures` (`tests/fixtures/composer/series_captured/` at 220d6100, `tests/fixtures/retirement/run_summary_captured.json` at 7c0b7432) and on `model-unpin/fixtures` (`tests/fixtures/anthropic/`).

## Per-Cycle Test Plan

The overview has no Rule-by-Entry-Point Matrix. The entry points through which the shared contracts are exercised are listed here and raised (Items Raised 14); each plan's own census stays in its approach.

| Entry point | Rules |
|---|---|
| GET / (`app.py:1665`) | SC-1, SC-2, SC-5 |
| GET /ai-advisor (`app.py:6619`) | SC-3, SC-4, SC-5, SC-6 |
| POST /login (`app.py:240`) | SC-8 Mode B |
| POST /api/sell_account (`app.py:5447`) | SC-8 Mode B |

### PM: fixture rules (SC-10)

1. Every fixture traces to the real producer (overview SD-5): a capture from production (the droplet, its database copy, a real API response), or the output of the real producer function over real inputs (a real writer such as `database.save_autotune_run`, the real recommender over the captures through 03's `run_over_captures`). A fixture written by hand to match the parser it tests is a gate failure.
2. One record per case the code under test distinguishes, never a dataset; PROVENANCE.md in each fixture directory names, per record, its source, the capture command or producer call with its input, the SHA, the time, and the case it covers. A record that covers no case is removed. Tests never assert a value copied from a capture; they derive it from the record or recompute it from the capture.
3. Real captures committed by the PM live on two branches and are consumed by merging the branch into a plan's base, never by copying files: `model-unpin/fixtures` (Anthropic API bodies, `tests/fixtures/anthropic/`, the model-unpin plan's) and `ux-overhaul/fixtures` (the twelve per-symphony Composer series bodies and `_index.json` under `tests/fixtures/composer/series_captured/` at 220d6100; one stored `RETIREMENT_RUN_SUMMARY` row, `tests/fixtures/retirement/run_summary_captured.json`, at 7c0b7432). Both PROVENANCE files state that nothing was edited. Existing committed captures are reused where they fit (`tests/fixtures/prod_droplet/`, captured by `tests/fixtures/prod_droplet/capture_from_droplet.py`). The production database copy behind the launcher is never committed.
4. A derived record changes only named fields of one real record and leaves every other field verbatim. Its PROVENANCE.md entry names the source record, each changed field, the reason and the scenario it serves, and records the PM's sign-off; until the sign-off is recorded the scenario's row is held, never filled with a typed value. Replacing a display name or a Composer hash by an invented value of the same length and character set is a derived edit under this rule.
5. A capture cut to the keys the code under test reads keeps every kept value verbatim and lists the kept keys in its PROVENANCE.md (05's FX-STATE-HEALTHY excerpt of a 3.9 MB `/api/state` body); a cut changes no value and is not a derived record.
6. Fixtures written through a real writer from a schema-shaped example (05's FX-FLEET-ROW through `database.write_fleet_alert`) name the writer as their runtime validator in PROVENANCE.md.

### 05 (SC-1, SC-2)

- SC-1 family, each a test: hero drawn, hero empty text, hero one point; each sparkline drawn, empty status, failed status; first draw with animation (the stub fires `onComplete`) and without (the render ends with no animation); `data-dashboard-ready` set once and never reset by a later refresh; `data-refresh-count` after a successful refresh, after a refused `/api/state`, after a refresh that constructs a newly seen canvas, and absent before the first refresh.
- SC-2 family: the ties of SC-2 at every precision 05 renders, server text against node text for the same value.

### 01 (SC-7)

- The meta test of SC-7; each browser module's fixture imports Playwright lazily; a run of `-m browser` with the in-process server for C6 and C8.

### 02 (SC-3, SC-4, SC-5, SC-6)

- SC-3 family: zone-less UTC, `Z`, offset, six-digit fraction, date-only (returned unchanged), `naive_is_utc=False` (date only), `None`, empty, unparseable; a value on each side of a summer-time change; Python and node equal for every input.
- SC-4 family as in SC-4.
- SC-5: 02 C1's base capture is produced at 02's base before any edit, and its PROVENANCE.md names that SHA.
- SC-6 family: every rendered tab name, an unknown name, an empty fragment, a fragment with selector characters, a click (no history entry), `hashchange`, Run now's reload.

### Screenshot gate protocol (every UI head of every plan)

- Viewports 1440x900 and 375x812 in light and dark (04 adds 320x640); Firefox; the launcher mode the plan names (Mode A unless a row needs Mode B).
- `/` is captured only after SC-1 readiness. Pages are compared with the audit captures under `.claude/review/37c8e983b64c/ux-audit/`; `/` is compared with the audit's dashboard captures by 05 and with the plan's own base capture by 02 and 04.
- Evidence is saved under `.claude/review/<head-sha12>/` with SC-8's capture record. The capturer never clicks a control that sends an operator-action request; in Mode A the launcher refuses it anyway.

## Per-Cycle Risk Register

| Cycle | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| 05 | Readiness waits on `animation.onComplete`, which a render that runs no animation may never fire, so a capture hangs (PM ruling keeps the entry animation; Chart.js is the unpinned CDN tag, `templates/index.html:11`) | Medium | Capture stalls, no evidence | SC-1 counts such a render drawn when it ends; the node stub covers both paths; on a timeout the capturer reports BLOCKED naming the canvas |
| 05 | A refused `/api/state` never settles a refresh, so a wait on `data-refresh-count` hangs (05 S-3.5 failure path, `static/index.js:1469`) | Medium | Capture stalls | SC-1 increments on a handled failure; 05's refresh tests include the refused request |
| 02, 04 | Their approaches wait on a different `/` signal (hero `Chart.getChart` and verdict text), which is true before the sparklines draw (the audit's blank-chart failure, 05 Architecture finding list) | High | `/` judged mid-draw | Every `/` capture uses SC-1 (Items Raised 2) |
| 05 | Server text formatted by Jinja or Python rounds exact ties half to even while the client rounds them away from zero (05 Architecture finding 6) | Medium | First-paint text changes on the first poll | SC-2: `analytics.js_to_fixed` only; tie family enumerated |
| 01 | `analytics.format_dollar_saved` (`analytics.py:2271`, an f-string `:,.2f`) rounds 0.125 to `0.12` where `toFixed(2)` gives `0.13`, so History and Discord split on ties, and moving it to `js_to_fixed` also changes Discord text (`reporting.py` calls it) | Certain | A tie renders differently on History and Discord until 01 C3 lands | SC-2: `format_dollar_saved` calls `analytics.js_to_fixed`; 01 C3's parity test enumerates the ties; the Discord change is named in 01's docs (Items Raised 9) |
| 02, 03 | A calendar date passed to the time formatter is read as UTC midnight and printed one ET day early, the defect class of G-44 (`advisors/composer_series_client.py:169-171`) | Medium | Every series date one day off | SC-3: dates never go through the formatter, and the formatter returns a date-only input unchanged (Items Raised 11) |
| 02, 03 | Copy names "03:45" as if ET while the droplet clock is UTC (`tests/fixtures/retirement/PROVENANCE.md` on `ux-overhaul/fixtures`; `app.py:1440`; `templates/ai_advisor.html:1667`) | High | A wrong time shown to the operator | SC-3's schedule rule; raised (Items Raised 12) |
| 02 | Primitive rules loaded from `static/layout.css` before the inline style block lose to an equal-specificity inline rule they beat before; login now loads them too (`templates/login.html:9`) | Low | `/` changes look (overview NB-11) | 02 C1's resolved-rule comparison and screenshots; login uses none of the classes (Grep of `templates/login.html` finds none) |
| 02 | A fragment value used to build a selector reaches the DOM from a URL (overview DC-7) | Low | Script error or wrong element | `tabNameFromHash` whitelist; SC-6 family includes selector characters |
| 01, 04 | A module-level Playwright import breaks CI collection, which has no Playwright (`.github/workflows/tests.yml:20-27`), or an `importorskip` turns into a skip against the zero-skip mandate (`tests/ui/test_advisor_card_overflow.py:22`) | High | Red CI or a hidden skip | SC-7 lazy import, no skip, the 01 C6 meta test |
| All | A run with `-o addopts=""` strips the `live` deselection and calls real APIs (`pyproject.toml:84-89`) | Low | Real-money API calls from a test | SC-7 run line uses `-m browser` only |
| 04 | In Mode B the served code loads the real `.env` beside `app.py` (`app.py:69`, `:78`) and `sell_account` reads `.env` from the working directory (`app.py:5468`); served from a pair's worktree with its copied `.env`, a confirmed request would post to Discord and log an emergency liquidation | Medium | A real Discord alert, a false incident record | SC-8 Mode B items 4, 5 and 7; network refused as a second guard; the PM's pre-handoff check |
| 04 | The login throttle locks the capturer out after ten failures (`app.py:126-129`) | Medium | Live login rows blocked for 300 s | SC-8 Mode B item 8 |
| 02 | Mode A refuses operator-action POSTs and the server's network, but 02's C3 scope asks for a live Run now on the launcher (`app.py:8394-8445`) | Certain | No live Run now evidence at C3 | Run now stays in the PM's droplet check, as 02's Merge Criteria already say (Items Raised 4) |
| All | A squash merge of a lower plan, the repository's usual style (`37c8e983` "(#145)"), leaves its commits in every upper branch | High at merge | Conflicts or duplicated changes in 02 to 04 | SC-9 rule 4: merge commits only |
| 03 | The captures carry live display names and hashes ("nothing was edited", `tests/fixtures/composer/series_captured/PROVENANCE.md` at 220d6100) while plan 03's text says they were replaced | Certain | Privacy question; tests seeded with live names | SC-10 rule 4; raised (Items Raised 8) |
| 03 | 03's approach pins `ux-overhaul/fixtures@220d6100`; the branch is at 7c0b7432, which adds the captured run summary 03's held S-8.4 row asked for | Certain | 03 forks without the record it needs | SC-9: 03's base merges the fixtures tip (Items Raised 7) |

## Cycle-Close Criteria

These hold for every cycle of every plan, in addition to its own approach's list, and never gate another cycle's start:
- The cycle's coverage record `records/coverage.json` under `.claude/review/<HEAD-sha12>/` has a test or a held row with its reason for every scenario the cycle owns, and `python3 ~/.claude/scripts/plan-trace check-coverage <record> --plan <plan> --approach <approach> --repo . --head <sha>` exits 0.
- An owner cycle of an SC contract has the tests named for it under Per-Cycle Test Plan, each family enumerated.
- No second implementation of a shared contract was added, checked on the cycle's diff: no other `toFixed` mirror in Python (`ROUND_HALF_UP`, `quantize`), no other ET formatter (`zoneinfo`, `America/New_York`, `timeZone` outside `analytics.format_et_stamp`, `fmtEtStamp`, the dashboard stamps and the chrome clock), no exclusion literal outside the recommender and `_retirement_exclusion_reason_sentence`, no primitive selector outside `static/layout.css`, no other reader of `location.hash` on `/ai-advisor`.
- Every Playwright module is marked `browser`, imports Playwright lazily, and has no skip.
- Every new fixture directory has a PROVENANCE.md meeting SC-10; no derived record without a recorded sign-off.
- Every capture behind a UI verdict carries SC-8's record, and every `/` capture was taken after SC-1 readiness.

## Merge Criteria

Per plan PR, on the exact head being merged, in addition to the plan's own list:
- The PR runs from the plan's assembly branch into main, in SC-9 rule 4's order; the head contains the model-unpin merge commit; every consumed tip entered by a merge commit; no rebase and no force push.
- `ruff format --check` and `ruff check` clean; the plan's scoped pytest with at most 4 workers and CI green on the head SHA; and, on the PM host, `python -m pytest -m browser -n 0` over every browser module of the plan, with `UX_LAUNCHER_URL` set to the mode each module needs, results saved under `.claude/review/<head-sha12>/`.
- The screenshot gate per the protocol above, in the named launcher modes, `/` only after SC-1 readiness.
- `git diff <PR base>...HEAD -- alpha_bot_execution.py math_engine.py migrations/` is empty and the five boundary files of overview NB-15 pass.
- `/review` run on the PR and its findings resolved or accepted in writing; then the PM's live functional check on the droplet against the real environment (deployed head, real daemon, real database, real API calls), because a mocked-green suite has repeatedly diverged from live behaviour here.
- The operator merges each PR in SC-9 rule 4's order, with a merge commit.

## Items Raised to the PM

1. 05's approach builds the hero and every sparkline with `animation: false` (Architecture, Client C1 hero and sparklines bullets; Risk Register C1 Chart.js row) and offers keeping the animation only as its Items Raised 2 alternative. The PM ruling and SC-1 keep the entry animation and signal readiness on `animation.onComplete`; 05's C1 must be revised to match before its RED.
2. 02's approach (Per-Cycle Test Plan C1 scope; Merge Criteria screenshot item) and 04's approach (Per-Cycle Test Plan opening paragraph: "`Chart.getChart` on the hero canvas truthy and every triggered card's verdict text present") use a different `/` readiness signal. SC-1 replaces both; the two approaches cite SC-1 instead.
3. 04's approach marks its Playwright modules `live` and follows `tests/ui/test_advisor_card_overflow.py:21-104` (`importorskip`, daemon `skipif`). Under SC-7 they are marked `browser`, import Playwright lazily, never skip, and read the launcher URL from `UX_LAUNCHER_URL`. 01's approach names Chromium (its Per-Cycle Test Plan browser line); SC-7 sets Firefox headless, the audit's engine. Settle the engine if Chromium is wanted.
4. 02's approach C3 scope asks for "live Run now outside market hours on the launcher instance". Mode A refuses operator-action POSTs and the server's network, so a run there could only fail to fetch the series; Run now stays in the PM's droplet check (already in 02's Merge Criteria).
5. 02's Merge Criteria ("Per PR (each cycle head, stacked ...)") and 03's ("Per PR (one per cycle, stacked ...)") read as one PR per cycle; the brief and SC-9 rule 3 say one PR per plan.
6. Settled by the PM: 02's C1 to C3 fork from the `ux-overhaul/05` tip and C4 and C5 fork after plan 01's tip is merged into `ux-overhaul/02`. 02's approach bases every cycle on 01's tip with 05 merged in, and must change its Dispatch Ordering. One overlap remains: 02 C2 edits the autotune card time inside `loadRecentRuns` (`static/ai_advisor.js:558`) while 01 C1 moves that function's card body into `autotuneRunCardHtml` (`static/ai_advisor.js:502-576`), so the merge of 01 into `ux-overhaul/02` conflicts there unless 02 C2 leaves `:558` to C4. The ruling does not name 02 C6, which here follows its own Consumes.
7. 03's approach pins `ux-overhaul/fixtures@220d6100` and names a machine-local database copy, launcher file and port (Execution Model; Items Raised 4). The fixtures branch is now at 7c0b7432, which adds `tests/fixtures/retirement/run_summary_captured.json` (one stored pre-plan `ok` summary, no `pairs` key), the record 03's held S-8.4 row asks for. 03's base merges the fixtures tip, and its launcher text defers to SC-8.
8. Name and hash handling differs across plans: the captures on `ux-overhaul/fixtures` carry live display names and Composer hashes unedited, while plan 03's Fixture provenance says they were replaced (03's Items Raised 2); 02 (C1 baseline) and 05 (every fixture) replace names and hashes with invented values of the real format. Under SC-10 rule 4 a replacement is a derived edit needing the PM's sign-off; settle once for the suite whether committed fixtures may carry live names and hashes.
9. Settled by the PM: SC-2's one Python implementation is `analytics.js_to_fixed`, owned by 05 C2, and `analytics.format_dollar_saved` calls it (01 C3). This changes three approaches: 05's approach places a private `_js_to_fixed` in `app.py` (Architecture, C2 helpers) and must move it to `analytics.py`; 01's approach lists no consumption of 05 and its C3 base is plain main, so C3 now merges `ux-overhaul/05` first when 05 has not reached main; and `format_dollar_saved` also feeds Discord (`reporting.py`), whose tie text changes, which 01's Docs to Update names.
10. SC-1's increment on a refused `/api/state` and its rule for a render with no animation fill cases 05's approach leaves unstated; 05 C1 carries them.
11. SC-3's date-only input (returned unchanged) is a case of 02 S-8.5's family that 02's approach does not list; 02 C2 carries it, with the rule that a calendar date is never passed to the formatter.
12. 03's Items Raised 7 is answered by `tests/fixtures/retirement/PROVENANCE.md` on `ux-overhaul/fixtures`: the droplet clock is Etc/UTC, so the 03:45 slot (`app.py:1440`) runs at 03:45 UTC (23:45 ET in summer). The never-run copy "runs nightly at 03:45" (`templates/ai_advisor.html:1667`, kept by 02 S-4.8 and cited by 03 S-8.1) must not gain an ET label; it states UTC or a converted ET time. Settle the wording with plans 02 and 03.
13. SC-7 assigns 01 C6 the marker registration, the `addopts` change and a meta test; 01's approach raised the marker but does not list that work in C6. The PM adds it to 01 C6's brief.
14. The census entry points of this file, GET / (`app.py:1665`), GET /ai-advisor (`app.py:6619`), POST /login (`app.py:240`) and POST /api/sell_account (`app.py:5447`), are not columns of a matrix, because the overview has none; each is carried by the plan approaches named in its SC.
15. The branch name `ux-overhaul/02` is introduced here; no approach named 02's branch. The overview's Stack and Merge Rules (`00-overview.md:179-181`: 02 stacks on 01, and the Suite Gate runs once on the assembled stack before the operator merges to main) predate the PM's main-targeted, merge-in-order ruling and need the same update.
16. 05's approach keeps its coverage map in `coverage.md`; the suite's Cycle-Close Criteria use `records/coverage.json` with `plan-trace check-coverage`, as 01, 02 and 03 do.
17. SC-8 Mode B cannot show the 200 dry-run answer live: with a blank `COMPOSER_KEY_ID` every confirmed request stops at the missing-credentials 400 (`app.py:5471-5472`), which is what keeps the Discord post and the liquidation branch unreachable. 04's S-9.7 stays harness-only, and the PM's droplet check provokes only a refusal (overview Suite Gate item 4).
