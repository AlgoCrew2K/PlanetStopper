# Feature: Retire the dead state-poll table render and define the shared heading margin rule once
Status: ready
Story: none: a follow-up from the PM's 2026-11-03 operator report, saved verbatim in feature-plans/dead-table-partial-and-header-css.source.md
Type: chore
Suite: none
Repos: AlphaBotPM
Base: main
Ref: AlphaBotPM@758f7f9551ff3a15a292debb7bfd4f00b3ecf1e7
Sensitive: none: no auth, CSRF, settings allowlist, credential or money path changes, and alpha_bot_execution.py and math_engine.py carry zero diff
Depends on: none

## Trigger
The operator's standing hygiene directive and two follow-ups in the PM's 2026-11-03 operator report, saved verbatim in `feature-plans/dead-table-partial-and-header-css.source.md`. The investigation confirms both at the ref:

1. `GET /api/state` renders `templates/table_partial.html` (201 lines) into an `"html"` key on two paths: the close-snapshot branch (`app.py:5600-5610`, served at `app.py:5640`) and the live branch (`app.py:5891-5901`, served at `app.py:5938`). The only client of the route, `static/index.js` (`loadState`, `static/index.js:2788-2810`), never reads `html`; its body check `isStateBody` reads only `meta` and `market_state` (`static/index.js:2757-2761`). `DECISIONS.md:7683-7700` (DE-MATH-F7-001 backlog item 1) already records the template as an orphaned render path awaiting a delete or rewire decision, and item 2 (`DECISIONS.md:7701-7704`) records a dead `openChartModal` reference inside it.
2. The rule `.hero-section > .section-header:first-child { margin-top: 0; }` is written four times, in the inline style blocks of `templates/ai_advisor.html:50`, `templates/history.html:40`, `templates/performance.html:44` and `templates/settings.html:33`. All six pages link `static/layout.css` (`templates/ai_advisor.html:11`, `templates/history.html:11`, `templates/performance.html:11`, `templates/settings.html:11`, `templates/index.html:10`, `templates/login.html:9`), which defines `.section-header` once at `static/layout.css:150-155`.

## Summary
The state poll stops computing and sending an HTML table no client reads, the dead template goes, and the one heading rule four pages repeat is defined once in the shared stylesheet, with every page and every other response value exactly as it is at the ref.
- `GET /api/state` carries no `html` key in any branch (AC-1).
- Every other key and value of the live branch (AC-2) and of the close-snapshot branch (AC-3) is unchanged.
- The `sortCol` and `sortDir` query parameters, which only ordered the table, are no longer read (AC-4).
- The dashboard page shows and requests exactly what it does at the ref (AC-5).
- `templates/table_partial.html` and every test that pins it or the `html` key are gone or rewritten (AC-6).
- The heading margin rule lives once in `static/layout.css` and computed styles on all six pages are unchanged (AC-7).

## Requirements as Given
[G-1] Part 1, operator directive (standing, verbatim): "the hygiene work also isn't trivial. dead-table-partial-and-header-css.source.md:1
[G-2] if this code is spaghetti like having multiple codepaths to the same outcomes or variables/classes/functions that do the same thing elsewhere or anything like that, or we have old files lying around like screenshots or feature-plans then we need to clean up after ourselves." dead-table-partial-and-header-css.source.md:1
[G-3] Part 2, the follow-ups section of the PM's 2026-11-03 operator report (verbatim): dead-table-partial-and-header-css.source.md:2
[G-4] "Follow-ups (not blocking) dead-table-partial-and-header-css.source.md:3
[G-5] `templates/table_partial.html` is dead. dead-table-partial-and-header-css.source.md:4
[G-6] It is still rendered into every `/api/state` response that nothing reads, and removing it would shrink the payload; this is an `app.py` change. dead-table-partial-and-header-css.source.md:4
[G-7] The shared header styles (`.section-header`, `.mini-stat`) are refined per page in three places; they could be folded into `layout.css`." dead-table-partial-and-header-css.source.md:5
[G-8] (Asked, Building 1: what does this make true?) "What this makes true: GET /api/state stops rendering and sending an HTML table that no client reads, and a heading rule repeated on four pages is defined once in static/layout.css. Trigger: the two follow-ups above, under the operator's directive that dead code and duplication are defects." [PM-ASSUMED]
[G-9] (Asked, Building 2: who, through which surface?) "Who and through which surface: the dashboard's browser client (static/index.js) polls GET /api/state; the pages are the dashboard (templates/index.html), AI Advisor (templates/ai_advisor.html), History (templates/history.html), Performance (templates/performance.html), Settings (templates/settings.html) and Login (templates/login.html)." [PM-ASSUMED]
[G-10] (Asked, Building 3: screens and flows?) "Screens and flows: no user flow changes. Every page must look and behave exactly as it does at 758f7f95." [PM-ASSUMED]
[G-11] (Asked, Building 4: negatives?) "Negatives: /api/state must keep every other key and value in every market state it serves (open with cycles, closed_frozen, pre_market, a stalled open with no cycle since the open); a render failure of the table no longer exists to fall back from; repeated polls and the SSE-triggered refresh behave as before; nothing about auth, CSRF or the two guarded write paths changes; no figure, label or computed style changes." [PM-ASSUMED]
[G-12] (Asked, Building 5: data and contracts?) "Data and contracts: the GET /api/state JSON response (both the live branch and the closed/frozen branch in app.py) loses its "html" key. Any request parameter that exists only to feed that table (for example sort_col/sort_dir, if nothing else reads them) is in scope to retire; verify by reading every reader. No schema, migration or config key changes." [PM-ASSUMED]
[G-13] (Asked, Building 6: repos?) "Repos: AlphaBotPM only." [PM-ASSUMED]
[G-14] (Asked, Building 7: sensitive?) "Sensitive: no. No security, auth, money computation or sensitive data changes; the engine (alpha_bot_execution.py, math_engine.py) has zero diff." [PM-ASSUMED]
[G-15] (Asked, Not building 1: non-goals?) "No other /api/state slimming; no change to the dashboard's own page-scoped rules (.page-dashboard .section-header, .page-dashboard .mini-stat*, which templates/index.html's head comment calls this page's own) unless one is a true duplicate of a layout.css rule with identical computed effect; no change to the operator's open design decisions D1 (0.7 opacity on OOS-rejected suggestion cards) and "at close" per dashboard card; .design-handoff/ (the Claude Design source copy) is not touched." [PM-ASSUMED]
[G-16] (Asked, Not building 2: what must stay exactly as is?) "Must stay exactly as is: every figure, label, computed style and network request on every page at 1440 and 375 px, light and dark; the dashboard server render; the engine." [PM-ASSUMED]
[G-17] (Asked, Not building 3: deferred?) "Deferred: any further dead code found during the investigation is listed in the plan as a follow-up, not fixed here." [PM-ASSUMED]
[G-18] (Asked, Not building 4: consumed, not changed?) "Consumed, not changed: static/index.js's handling of the /api/state response; static/layout.css's existing rules other than the one added." [PM-ASSUMED]

## Requirement Inventory
| R-n | Requirement item | Source (G ids) | Maps to (AC ids, or NB-n) | Scope (full, or narrowed: what is left out) |
|---|---|---|---|---|
| R-1 | Hygiene work gets the same rigour as feature work | G-1 | AC-2, AC-3, AC-5, AC-7 | full |
| R-2 | No two codepaths produce the same outcome, and nothing does what something elsewhere already does | G-2 | AC-4, AC-6, AC-7, NB-13, NB-14 | narrowed: covers the table render with its two copies of the sort logic and the four copies of the heading rule; other duplication found is deferred (NB-13, NB-14) |
| R-3 | Old files lying around (screenshots, stale plans) are cleaned up | G-2 | AC-6, NB-2 | narrowed: covers the dead template and the test fixtures only it used; untracked files, screenshots and stale plans elsewhere are NB-2 |
| R-4 | The follow-ups are not blocking work | G-3, G-4 | NB-5 | full |
| R-5 | `templates/table_partial.html` is removed as dead | G-5 | AC-6 | full |
| R-6 | `/api/state` stops rendering the table into its responses | G-6, G-8, G-12 | AC-1 | full |
| R-7 | The payload shrinks | G-6 | AC-1 | full |
| R-8 | The change is in `app.py` | G-6 | AC-1, AC-4 | full |
| R-9 | The per-page header refinements are folded into `layout.css` | G-7, G-8 | AC-7, NB-3, NB-4 | narrowed: only the one rule repeated on four pages moves; the `.mini-stat` and dashboard-scoped refinements stay (Q-5, Q-6) |
| R-10 | The dashboard client and all six pages are the surfaces | G-9 | AC-5, AC-7 | full |
| R-11 | No user flow changes, and every page looks and behaves as at the ref | G-10, G-16 | AC-5, AC-7, NB-8 | full |
| R-12 | Every other key and value is kept in every market state | G-11 | AC-2, AC-3 | full |
| R-13 | No fallback for a table render failure remains | G-11 | AC-1 | full |
| R-14 | Repeated polls and the SSE-triggered refresh behave as before | G-11 | AC-1, AC-5 | full |
| R-15 | Auth, CSRF and the two guarded write paths are unchanged | G-11, G-14 | AC-1, NB-11 | full |
| R-16 | A parameter that only fed the table is retired, after reading every reader | G-12 | AC-4 | full |
| R-17 | No schema, migration or config key change | G-12 | NB-12 | full |
| R-18 | AlphaBotPM is the only repo | G-13 | NB-7 | full |
| R-19 | Nothing sensitive changes and the engine has zero diff | G-14 | NB-10, NB-11 | full |
| R-20 | No other `/api/state` slimming | G-15 | NB-1 | full |
| R-21 | Dashboard page-scoped rules stay unless one is a true duplicate | G-15 | NB-3 | full |
| R-22 | Open design decisions D1 and "at close" are untouched | G-15 | NB-5 | full |
| R-23 | `.design-handoff/` is untouched | G-15 | NB-6 | full |
| R-24 | Further dead code found is listed as a follow-up | G-17 | NB-13, NB-14, NB-15, NB-16, NB-17 | full |
| R-25 | `static/index.js`'s handling of the response and `layout.css`'s existing rules are consumed, not changed | G-18 | NB-19, NB-20 | full |

## User Tasks
none: no screen, flow or control changes; AC-5 and AC-7 prove each page renders as it does at the ref.

## Earlier criteria on the same surface
Searched every `feature-plans/*.md` (including `.completed.md`, `.obsolete.md` and the suite folders), `DECISIONS.md`, `docs/generated/` and `docs/audit/` for `table_partial`, the `html` key, `sortCol`, `section-header` and `layout.css`.

| Earlier criterion (plan and AC) | Surface | Disposition (kept, changed or contradicted) | Why |
|---|---|---|---|
| `feature-plans/refresh-timing/01-server-state-truth.md` NB-2 and Q-4 (keep the unread `/api/state` fields `html`, `accounts_map`, `shadow_divergence`, `port_state`, `exit_authority`, `execution_start_time`) | `GET /api/state` | contradicted | for `html` only: the operator's later directive and follow-up name the table as dead; the other five keys stay (NB-1); Q-7 asks the operator to confirm |
| `tests/dashboard/test_frozen_html_rendering.py` AC-H1, AC-H2, AC-H4, AC-H5, AC-H6, AC-H7, AC-H8 (frozen and live bodies carry `html`; sort parameters honoured) | `GET /api/state` | contradicted | the key and the sort are retired (AC-1, AC-4) |
| `tests/dashboard/test_frozen_html_rendering.py` AC-H3 (analytics read the snapshot's trading day) | `GET /api/state` close-snapshot branch | kept | it covers a live behaviour (S-6.4) |
| `feature-plans/m1f-real-shadow-equity-series.completed.md` AC-M1F.4.1 (table header "Held Return") | table column | contradicted | the table has no reader (`DECISIONS.md:7683-7700`) |
| `feature-plans/math-f7.md` AC-3 (MC Prob tooltip truth) | table header tooltip | contradicted | the orphaned table copy of the tooltip goes with the template |
| `feature-plans/math-f7.md` AC-3 (MC Prob tooltip truth) | MC dial, detail Risk Math | kept | those live surfaces are not touched |
| `feature-plans/vwap-remediation.completed.md` Workstream 1 (`last_trigger` per symphony in `/api/state`) | `GET /api/state` | kept | the intake keeps every other key and value (S-2.6) |
| `feature-plans/vwap-remediation.completed.md` Workstream 1 (a Last Trigger table column) | table column | contradicted | the table has no reader |
| `feature-plans/engine-correctness-remediation.completed.md` H1 (the table part of its UI) | table | contradicted | the table has no reader; its `templates/index.html` part is untouched |
| `tests/dashboard/test_cards_live_updates.py` AC-CL.6 (`static/index.js` never puts `data.html` into the DOM) | `static/index.js` | kept | Q-1 |
| `feature-plans/post-audit-hardening.completed.md` AC-9 (INV-COV-4: every consumer of the stop level handles a decrease; its table pin is `tests/reporting/test_inv_cov_4_stop_consumers_handle_decrease.py:189-250`) | table stop cell | contradicted | the table consumer goes; the other consumers AC-9 covers are untouched |
| `tests/dashboard/test_frozen_html_rendering.py` AC-H9 (a snapshot missing `shadow_hwm` renders without error) | close-snapshot render | contradicted | the render goes; the defaulting it relied on stays (S-3.2) |
| `tests/ui/test_dash_advisor_fixes.py` AC-3b (the table MDD column is not multiplied by 100) | table column | contradicted | the table has no reader |
| `tests/dashboard/test_r15_shadow_names.py` section 6 (table rows carry `data-symphony-id`) | table rows | contradicted | the table has no reader; the dashboard cards' own identifiers are untouched |
| `tests/ui/test_persymph_settings_modal.py` F7 AC-1 for table rows (each row carries the settings gear) | table rows | contradicted | the table has no reader; the card gear (`templates/index.html:1711-1714`) stays |
| `tests/app/test_dashboard_m2.py` M2 per-symphony TC, CR, MDD columns and clearer headers | table columns | contradicted | the table has no reader; the cards carry the same figures in `symphonies` (AC-2) |
| `feature-plans/ux-overhaul/00-overview.md` SD-6 (the dashboard primitives move into `static/layout.css` with identical values; plan 04 deletes every page-local copy) | `static/layout.css` | changed | the shared sheet gains one rule beyond the moved set and every moved rule stays as it is; the four page-local copies are the kind SD-6 removes; its byte-for-byte pin is Q-4 |
| `feature-plans/ux-overhaul/04-app-shell.md` AC-2 (pages use the dashboard primitives with no page-local look values) | four page templates | kept | the moved rule sets layout only and keeps its value |

## Acceptance Criteria (Building)

### AC-1: The state poll carries no table markup in any branch
Requirement items: R-6, R-7, R-8, R-13, R-14, R-15
Rule: When `GET /api/state` answers, the route shall send a body with no `html` key, in every branch and market state it serves.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-1.1 | B | market open, a cycle committed today, two accounts with three symphonies | an authenticated client requests `GET /api/state` | 200 with `status` "active" and no `html` key |
| S-1.2 | B | market `closed_frozen`, a close snapshot stored for the latest session | the client requests the route | 200 close-snapshot body with no `html` key |
| S-1.3 | B | market `pre_market`, a close snapshot stored | the client requests the route | 200 close-snapshot body with no `html` key |
| S-1.4 | N3 | market open, no cycle committed since today's open, a close snapshot stored (the stalled open, `app.py:4164-4168`) | the client requests the route | 200 close-snapshot body with no `html` key |
| S-1.5 | N3 | market `closed_frozen`, a newer session closed and its snapshot not stored yet (the held view, `app.py:4172-4177`) | the client requests the route | 200 close-snapshot body with no `html` key |
| S-1.6 | N3 | market `closed_frozen`, no snapshot stored, live state present (served by the live branch, `app.py:4170-4171`) | the client requests the route | 200 live-branch body with no `html` key |
| S-1.7 | N3 | no bot state at all | the client requests the route | the waiting body with `status` "waiting", no `html` key, every other key as at the ref |
| S-1.8 | N1 | as S-1.1 | the client requests `GET /api/state?account=ACCT-ALPHA` | 200 with no `html` key |
| S-1.9 | N6 | the state read raises inside the route | the client requests the route | 500 with `{"status": "error", "message": "An internal error occurred"}`, as at the ref (`app.py:5956-5959`) |
| S-1.10 | N4 | no authenticated session | an XHR requests the route | 401 with `{"error": "unauthenticated"}` (`app.py:313-315`); no state is read or sent |
| S-1.11 | N5 | as S-1.2, with the request clock fixed at one instant for both requests | two requests arrive one after the other with nothing committed between | both bodies lack `html` and are equal to each other |
| S-1.12 | N2 | no secret key configured | an XHR requests the route | 503 with `{"error": "misconfigured"}` (`app.py:290-296`), as at the ref |
| S-1.13 | N2 | a secret key configured but no dashboard credential (neither `DASHBOARD_PASSWORD` nor `DASHBOARD_PASSWORD_HASH`) | an XHR requests the route | 503 with `{"error": "misconfigured"}` (`app.py:298-306`), as at the ref |

Negative coverage: N1 S-1.8 · N2 S-1.12, S-1.13 · N3 S-1.4, S-1.5, S-1.6, S-1.7 · N4 S-1.10 · N5 S-1.11 · N6 S-1.9 · N7 covered by AC-2 and AC-3 · N8 n/a: the route is a read and holds no edit open; the page side is AC-5
Questions: Q-7

### AC-2: Every other value of the live branch is unchanged
Requirement items: R-1, R-12
Rule: When `GET /api/state` serves the live branch (market open with a committed cycle, or closed with no snapshot), the route shall send every key and value that 758f7f95 sends for the same stored state, query and clock, with only `html` absent.

Every comparison with the ref body in AC-2 and AC-3 is made with the request clock fixed at the instant the ref body was captured, as the golden producer does (`tests/fixtures/refresh_timing/01-c2/produce_golden_ref.py:12`).

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-2.1 | B | market open, accounts ACCT-ALPHA and ACCT-BIRCH, symphonies Maple Rotation, Birch Momentum and Cedar Hedge | the client requests the route | the body equals the ref body for the same inputs minus `html`: every key, value and order in `state`, `bot_state`, `symphonies`, `meta`, `portfolio_strip`, `dashboard_panels`, `shadow_divergence`, `fleet_correlation_alert` and the scalar keys |
| S-2.2 | B | as S-2.1 | the client requests the route | each symphony entry in `state` and `bot_state` carries `id` equal to its key (the Composer symphony hash) and `normalized_name` equal to the normalized display name `database.normalize_name` gives for its `name`, as at the ref (`app.py:5729-5733`) |
| S-2.3 | N1 | the state holds a dict entry with no `name` (for example `last_market_close_snapshot`) | the client requests the route | that entry gains no `id` or `normalized_name` and every other value is as at the ref |
| S-2.4 | N1 | as S-2.1 | the client requests `?account=ACCT-ALPHA` | `state`, `bot_state` and `symphonies` are narrowed to ACCT-ALPHA exactly as at the ref, and `portfolio_strip` and `meta` come from the whole state as at the ref (`app.py:5700-5707`, `:5921-5927`) |
| S-2.5 | N1 | as S-2.1 | the client requests `?account=ACCT-NONE` (no symphony in it) | the body equals the ref body for the same request minus `html` |
| S-2.6 | N6 | the trigger read (`database.get_triggers`) raises | the client requests the route | every symphony's `last_trigger` is null, as at the ref (`app.py:5842-5853`) |
| S-2.7 | N6 | one per-symphony analytics read raises KeyError, TypeError or ValueError | the client requests the route | that symphony's `_tc`, `_cr` and `_mdd` are each `{"if_held": null, "dry_run": null}` and its card figures are null, as at the ref (`app.py:5855-5877`) |
| S-2.8 | N3 | one symphony has no `prev_return`, a second has no `current_return`, a third has both | the client requests the route | the first two carry `para_velocity` null and the third the difference in percent points, as at the ref (`app.py:5878-5886`) |
| S-2.9 | N6 | the dashboard panels builder raises | the client requests the route | `dashboard_panels` is the "could not read" payload, as at the ref (`app.py:5482-5489`) |
| S-2.10 | N5 | as S-2.1, with the request clock fixed at one instant for both requests | two requests arrive with no cycle between | both bodies are equal, including the clock-derived `next_run_seconds`, `meta.next_run` and `meta.clock_et` (`app.py:3653`, `:3728`, `:3735`) |
| S-2.11 | N3 | market `closed_frozen`, no snapshot stored, the S-2.1 state (the live branch of S-1.6) | the client requests the route | the body equals the ref body for the same inputs minus `html` |

Negative coverage: N1 S-2.3, S-2.4, S-2.5 · N2 n/a: the live branch refuses nothing beyond AC-1's 401 and 503 · N3 S-2.8, S-2.11 · N4 covered by AC-1 S-1.10 · N5 S-2.10 · N6 S-2.6, S-2.7, S-2.9 · N7 S-2.1, S-2.2 · N8 n/a: the route holds no edit open
Questions: none

### AC-3: Every other value of the close-snapshot branch is unchanged
Requirement items: R-1, R-12
Rule: When `GET /api/state` serves a close snapshot (closed, pre-market, held or stalled open), the route shall send every key and value that 758f7f95 sends for the same stored state and clock, including each snapshot entry's defaulted fields and each account list's order, with only `html` absent.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-3.1 | B | market `closed_frozen`, a snapshot with two accounts and four symphonies | the client requests the route | the body equals the ref body minus `html`: `state`, `bot_state`, `accounts_map`, `symphonies`, `shadow_divergence`, `frozen_at`, `trading_day`, `snapshot_is_previous_session`, `data_as_of`, `portfolio_strip`, `meta` and the shared keys |
| S-3.2 | N1 | a snapshot entry lacks some of the 13 fields `app.py:4441-4455` defaults (`mc_prob`, `shadow_hwm`, `stop_trigger`, `current_return`, `current_value`, `breakeven_locked`, `armed`, `tp_armed`, `para_armed`, `triggered`, `above_tp_count`, `below_stop_count`, `last_trigger`) | the client requests the route | the entry in `state`, `bot_state` and `accounts_map` carries each missing field at the default the ref serves, and its card in `symphonies` reads those defaults as at the ref |
| S-3.3 | N1 | a snapshot entry carries one of those fields as null | the client requests the route | the field stays null in every place S-3.2 names |
| S-3.4 | N1 | a snapshot entry carries one of those fields with a value | the client requests the route | the value is unchanged in every place S-3.2 names |
| S-3.5 | B | ACCT-ALPHA's entries are stored in the order Birch Momentum, Ash Ladder | the client requests the route | `accounts_map["ACCT-ALPHA"]` lists Ash Ladder then Birch Momentum: ascending by lower-cased name, as at the ref (`app.py:5574-5578`) |
| S-3.6 | N1 | one entry in an account list has no `name` | the client requests the route | it is placed by its lower-cased `id` (the Composer symphony hash) among the names, as at the ref |
| S-3.7 | N5 | the snapshot was first served by a poll, with no `GET /` since it was held, and the request clock fixed at one instant for every request | the client requests the route, then loads `GET /`, then requests the route again | both poll bodies are equal and equal the ref body minus `html`, including S-3.2's defaults and S-3.5's order |
| S-3.8 | N3 | the held view of S-1.5 | the client requests the route | the body equals the ref body minus `html` |
| S-3.9 | N3 | a symphony in the snapshot is no longer held | the client requests the route | it is left out of `state`, `accounts_map` and `symphonies` exactly as at the ref |
| S-3.10 | N3 | market `pre_market` with a stored snapshot (S-1.3) | the client requests the route | the body equals the ref body minus `html` |
| S-3.11 | N3 | the stalled open of S-1.4 | the client requests the route | the body equals the ref body minus `html` |
| S-3.12 | N6 | the guard-alpha read for triggered symphonies raises | the client requests the route | no entry gains `guard_alpha` and every other value is as at the ref (`app.py:3109-3137`) |
| S-3.13 | N1 | the snapshot carries no `accounts_map` | the client requests the route | `accounts_map` is null as at the ref (`app.py:5633`) |

Negative coverage: N1 S-3.2, S-3.3, S-3.4, S-3.6, S-3.13 · N2 n/a: the branch refuses nothing beyond AC-1's 401 and 503 · N3 S-3.8, S-3.9, S-3.10, S-3.11 · N4 covered by AC-1 S-1.10 · N5 S-3.7 · N6 S-3.12 · N7 S-3.1, S-3.5 · N8 n/a: the route holds no edit open
Questions: none

### AC-4: The table's sort parameters are no longer read
Requirement items: R-2, R-8, R-16
Rule: If `GET /api/state` is requested with a `sortCol` or `sortDir` query parameter, then the route shall answer exactly as it answers the same request without them.

Every comparison in this table is between two requests made with the request clock fixed at one instant, so the clock-derived `next_run_seconds`, `meta.next_run` and `meta.clock_et` (`app.py:3653`, `:3728`, `:3735`) are equal by construction.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-4.1 | B | the snapshot of S-3.5 | the client requests `?sortCol=status&sortDir=desc` | the body equals the body for the request without the parameters; `accounts_map["ACCT-ALPHA"]` is in name order |
| S-4.2 | B | the live state of S-2.1 | the client requests `?sortCol=mc_prob&sortDir=desc` | the body equals the body without the parameters |
| S-4.3 | N1 | as S-4.1 | the client requests `?sortCol=unknown_column` | the body equals the body without the parameter |
| S-4.4 | N1 | as S-4.1 | the client requests `?sortCol=&sortDir=` | the body equals the body without the parameters |
| S-4.5 | N1 | as S-4.1 | the client requests `?sortDir=desc` alone (at the ref this reversed the name order) | the body equals the body without the parameter |
| S-4.6 | N1 | as S-2.4 | the client requests `?account=ACCT-ALPHA&sortCol=status` | the body equals the `?account=ACCT-ALPHA` body |
| S-4.7 | N2 | as S-4.1 | the client sends any sort parameter value | the answer is 200, never a 400 |
| S-4.8 | N3 | no bot state | the client requests `?sortCol=status` | the waiting body, equal to the body without the parameter |

Negative coverage: N1 S-4.3, S-4.4, S-4.5, S-4.6 · N2 S-4.7 · N3 S-4.8 · N4 n/a: the parameters do not reach the auth hook, which runs first (AC-1 S-1.10) · N5 n/a: the route is a stateless read, so a repeated parameter has no effect to accumulate · N6 n/a: the parameters read no dependency · N7 S-4.1, S-4.2 · N8 n/a: the route holds no edit open
Questions: Q-2

### AC-5: The dashboard page shows and requests what it does at the ref
Requirement items: R-1, R-10, R-11, R-14
Rule: When the dashboard page applies a `GET /api/state` body (first poll, the 30 s fallback poll, or a refresh on an SSE `cycle-complete` event), the page shall show the same figures, labels, cards, panels and detail panel, and send the same requests, as 758f7f95 does for the same stored state and clock.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-5.1 | B | market open, the S-2.1 state | the dashboard loads and its first poll applies, at 1440 px, light theme | every figure, label, card and panel text equals the ref page's |
| S-5.2 | B | market `closed_frozen`, the S-3.1 snapshot | the dashboard loads and polls | every figure, label, card and panel text equals the ref page's |
| S-5.3 | N5 | as S-5.1 | the SSE stream (`static/index.js:2858-2862`) sends `cycle-complete` | the page makes one `GET /api/state` request with the same URL as at the ref (no `sortCol`), and updates as at the ref |
| S-5.4 | N8 | the detail panel is open on Maple Rotation | a poll lands | the footer reads `id: ` then the Composer symphony hash ` · normalized: ` then the normalized display name, and Risk Math shows the same parabolic velocity text, as at the ref (`static/index.js:1691-1695`, `:1443`) |
| S-5.5 | N6 | the route answers 500 | the poll runs | the page keeps every value and shows the "connection lost" cue, as at the ref (`static/index.js:2763-2770`) |
| S-5.6 | N4 | the session has expired | the poll runs | the poll answers 401 and the page goes to the login page, as at the ref |
| S-5.7 | N1 | as S-5.2 | the dashboard is viewed at 375 px in the dark theme | the page equals the ref page at the same width and theme |
| S-5.8 | N3 | no bot state | the dashboard loads | the page shows the same initializing state as at the ref |
| S-5.9 | N7 | as S-5.1 | a poll runs | the page's network requests per poll match the ref in URL and count |

Negative coverage: N1 S-5.7 · N2 n/a: the page refuses no input on this path · N3 S-5.8 · N4 S-5.6 · N5 S-5.3 · N6 S-5.5 · N7 S-5.9 · N8 S-5.4
Questions: none

### AC-6: The dead template and every pin on it are gone
Requirement items: R-2, R-3, R-5
Rule: When this change lands, `templates/table_partial.html` shall not exist, no file among `app.py`, `static/` and `templates/` shall render, load or name it, and no test shall render it, read its source or require an `html` key from `GET /api/state`.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-6.1 | B | the change's tree | the repository is searched for `table_partial` in `app.py`, `static/` and `templates/` | the template file does not exist and the search returns nothing |
| S-6.2 | B | the change's tree | the test suite runs | a route test asserts that each body of S-1.1, S-1.2, S-1.3, S-1.4, S-1.5, S-1.6, S-1.7 and S-1.8 has no `html` key, in place of every presence pin and every reader of the key in the Test census (Contract Changes and Consumers) |
| S-6.3 | N7 | the change's tree | the test suite runs | the pins that `static/index.js` never puts `data.html` into the DOM (`tests/dashboard/test_cards_live_updates.py:326-391`, `tests/dashboard/test_card_consistency_liveness.py:733-745`, `tests/dashboard/test_cards_live_refresh.py:405-419`, `tests/dashboard/test_symph_autoupdate.py:227-247`) still run and pass |
| S-6.4 | N7 | a test that pins the template or the `html` key and also asserts a live behaviour (for example `tests/dashboard/test_frozen_html_rendering.py:197-275`, analytics reading the snapshot's trading day) | the test is retired or rewritten | the live assertion remains in the suite and passes |
| S-6.5 | N1 | the change's tree | the repository is searched for `table_partial` in `tests/` | the only hits are in docstrings that record a past cycle's diff or describe history (`tests/test_scope_guard_f7.py:8`, `:58`; `tests/execution/test_cycleat_fix.py:21`) or in the S-6.3 pins' descriptions; no test renders the template, reads its file, or reads the template context from a patched `render_template` |
| S-6.6 | N1 | `tests/fixtures/dashboard/frozen_html/frozen_html_missing_shadow_hwm.json` and `frozen_html_with_symphonies.json` | the suite is searched for each fixture's readers | a fixture with no remaining reader is removed; a fixture a kept test reads stays |
| S-6.7 | N1 | `static/settings-modal.js:4-5` names a table row as an opener | the change lands | the header comment names only what opens the modal at the change (the card gear on the dashboard) |
| S-6.8 | N7 | `.design-handoff/project/templates/index.html`, the existing entries of `DECISIONS.md`, `docs/research/`, `docs/audit/` and every `.completed.md` or `.obsolete.md` plan | the change lands | none of them changes; `DECISIONS.md` gains only the one appended entry Docs to Update names |

Negative coverage: N1 S-6.5, S-6.6, S-6.7 · N2 n/a: a repository state refuses nothing · N3 n/a: no value is computed · N4 n/a: no access path is involved · N5 n/a: the removal happens once · N6 n/a: no dependency is called · N7 S-6.3, S-6.4, S-6.8 · N8 n/a: no edit is held open
Questions: Q-1

### AC-7: The heading margin rule is defined once in the shared stylesheet
Requirement items: R-1, R-2, R-9, R-10, R-11
Rule: When any page that links `static/layout.css` renders, the zero top margin of a `.section-header` that is the first child of a `.hero-section` shall come from one rule in `static/layout.css`, no template's inline style shall define it, and every element's computed style on all six pages shall equal the ref's.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-7.1 | B | the change's tree | `static/layout.css` and the inline style blocks of the six templates are scanned | `static/layout.css` holds exactly one `.hero-section > .section-header:first-child` rule with `margin-top: 0`; no template holds one |
| S-7.2 | B | History, 1440 px, light | the page renders | the "By exit reason" heading (`templates/history.html:447-448`) has a computed top margin of 0 px, as at the ref |
| S-7.3 | B | Performance, 1440 px, light | the page renders | the "Risk metrics", "Stop-justification preconditions" and "Exit turnover" headings (`templates/performance.html:594-595`, `:652-653`, `:697-698`) have a computed top margin of 0 px, as at the ref |
| S-7.4 | B | AI Advisor, 1440 px, light | each tab is shown | every first-child panel heading (for example `templates/ai_advisor.html:1538-1539`, `:1670-1671`, `:1941-1942`, `:2427-2428`) has a computed top margin of 0 px, as at the ref |
| S-7.5 | B | Settings, 1440 px, light | the page renders | each section title that is a panel's first child (for example `templates/settings.html:999-1000`) has a computed top margin of 0 px, as at the ref |
| S-7.6 | N7 | the dashboard and the login page | each renders at 1440 and 375 px, light and dark | every element's computed style equals the ref's; no element there is a `.section-header` first child of a `.hero-section` (`templates/index.html:1252-1253`, `:1612-1613`; `templates/login.html` has neither class; `static/index.js` writes neither) |
| S-7.7 | N1 | History and Performance | a `.section-header` that is not a panel's first child (inside `.hp-head`, `templates/history.html:425-427`, `templates/performance.html:573-576`) renders | its computed margins equal the ref's |
| S-7.8 | N1 | each of the four pages | rendered at 375 px and in the dark theme | every computed style equals the ref's |
| S-7.9 | N8 | removed: the script-written headings (`static/ai_advisor_asset_swaps.js:366`, `:390`; `static/ai_advisor_logic_changes.js:368`, `:379`) sit on AI Advisor, where the rule applied at the ref with the same specificity and no competing rule; seeding a run result needs a live model call, and S-7.4 covers the page's headings | n/a | n/a |
| S-7.10 | N7 | the change's tree | `tests/ui/test_dashboard_primitives_shared.py` runs | every primitive rule `static/layout.css` held at the ref is unchanged in declarations and order; the one moved rule is the only addition (Q-4) |

Negative coverage: N1 S-7.7, S-7.8 · N2 n/a: a stylesheet refuses nothing · N3 n/a: no value is computed or awaited; hidden tabs are covered by S-7.4 when shown · N4 n/a: every page that links the sheet already reaches it; login is covered by S-7.6 · N5 n/a: a static stylesheet has no repeated action · N6 n/a: when `static/layout.css` fails to load, every shared primitive (`static/layout.css:97-155`) is already lost at the ref, and the moved rule joins that set (Known-Unconfirmed) · N7 S-7.6, S-7.10 · N8 n/a: no edit is held open on these pages; the run-result headings are AI Advisor's, covered by S-7.4 (S-7.9 removed)
Questions: Q-4, Q-5, Q-6

## Rule-by-Entry-Point Matrix
| Rule | Live: open with a cycle | Live: closed, no snapshot | Snapshot: `closed_frozen` | Snapshot: `pre_market` | Snapshot: stalled open | Snapshot: held view | Waiting: no state | Error: 500 | Unauthenticated: 401 | Misconfigured: 503 |
|---|---|---|---|---|---|---|---|---|---|---|
| AC-1 no `html` key | S-1.1 | S-1.6 | S-1.2 | S-1.3 | S-1.4 | S-1.5 | S-1.7 | S-1.9 | S-1.10 | S-1.12, S-1.13 |
| AC-2 every other live-branch value unchanged | S-2.1 | S-2.11 | n/a: the close-snapshot branch serves this state (AC-3) | n/a: the close-snapshot branch serves this state (AC-3) | n/a: the close-snapshot branch serves this state (AC-3) | n/a: the close-snapshot branch serves this state (AC-3) | n/a: the waiting branch serves this state (AC-1 S-1.7) | n/a: the error body carries no state values | n/a: the auth hook answers before the route runs | n/a: the auth hook answers before the route runs |
| AC-3 every other close-snapshot value unchanged | n/a: the live branch serves this state (AC-2) | n/a: the live branch serves this state (AC-2) | S-3.1 | S-3.10 | S-3.11 | S-3.8 | n/a: the waiting branch serves this state (AC-1 S-1.7) | n/a: the error body carries no state values | n/a: the auth hook answers before the route runs | n/a: the auth hook answers before the route runs |
| AC-4 sort parameters not read | S-4.2 | S-4.2 | S-4.1 | S-4.1 | S-4.1 | S-4.1 | S-4.8 | n/a: the error body carries no symphony data to order | n/a: the auth hook answers before the route reads a parameter | n/a: the auth hook answers before the route reads a parameter |

| Rule | Dashboard | AI Advisor | History | Performance | Settings | Login |
|---|---|---|---|---|---|---|
| AC-7 heading margin from `static/layout.css`, computed style unchanged | S-7.6 | S-7.4 | S-7.2, S-7.7 | S-7.3, S-7.7 | S-7.5 | S-7.6 |

## Not Building
Non-goals:
- NB-1: No other `/api/state` slimming; `accounts_map`, `shadow_divergence`, `port_state`, `exit_authority`, `execution_start_time` and every other key stay (excludes nothing; keeps `feature-plans/refresh-timing/01-server-state-truth.md` NB-2 for those keys).
- NB-2: Cleaning untracked files, screenshots or stale plans outside this change (excludes part of R-3; approved: their own cleanup cycle, tracked separately).
- NB-3: No change to the dashboard's page-scoped rules (`templates/index.html:37-55`, `:438-478`); none is a whole-rule duplicate of a `static/layout.css` rule (Q-5).
- NB-4: No fold of the `.mini-stat` refinements; the dashboard's are page-scoped (`templates/index.html:438-478`) and the Advisor's tiles use its own `adv-fig` classes (`templates/ai_advisor.html:2028-2032`); neither is a cross-page copy (excludes part of R-9; approved; Q-6).
- NB-5: No change to the open design decisions D1 (0.7 opacity on OOS-rejected suggestion cards) and "at close" per dashboard card, and no urgency beyond the operator's delivery rules (Q-3).
- NB-6: `.design-handoff/` is not touched, including its copy of the table template.
- NB-7: No repository other than AlphaBotPM changes.

Stays exactly as it is:
- NB-8: Every figure, label, computed style and network request on every page at 1440 and 375 px, light and dark.
- NB-9: The dashboard server render (`GET /`, `app.py:3249-3265` and `_render_dashboard`), including its own call that fills a snapshot's missing fields (`app.py:3285-3288`).
- NB-10: `alpha_bot_execution.py` and `math_engine.py` carry zero diff.
- NB-11: The auth hook, CSRF, the two guarded write paths, `_SETTINGS_WRITE_ALLOWLIST` and the `LIVE_EXECUTION` reads in the route.
- NB-12: No schema, migration or configuration key change.

Deferred (follow-ups found during the investigation, not fixed here):
- NB-13: The scrollbar rules repeated in four inline style blocks (`templates/ai_advisor.html:15-24`, `templates/history.html:14-23`, `templates/performance.html:14-31`, `templates/settings.html:14-17`).
- NB-14: Single declarations inside the dashboard's page-scoped rules that repeat the `static/layout.css` value (for example `border-radius: 14px` at `templates/index.html:38` and `static/layout.css:99`; `gap: 24px` at `templates/index.html:438` and `static/layout.css:113`).
- NB-15: The stale comment at `static/settings.js:259`, which says Sync and Flush fetches `/api/state` while the code fetches `/api/armed-symphonies` (`static/settings.js:416`).
- NB-16: The close-snapshot defaults fill a missing `current_return` and a missing `shadow_hwm` with 0.0 in the poll body (`app.py:4443`, `:4445`). Those entries are the objects the body's `bot_state` serves (`app.py:4710-4718`), and the dashboard detail panel prints from them (`static/index.js:1216-1225`), so an absent return reads +0.00% and an absent peak 0.00% where the panel would print a dash. The defaults' own comment (`app.py:4439-4440`) gives the template as their reason; once it is gone, this fabricated zero is their only effect left on the poll. Kept here (PD-1, the intake keeps every value); deferred as its own fix.
- NB-17: The project agent file `.claude/agents/flask-dashboard-specialist.md:3`, `:15` names the template as owned; its update is drafted for the PM (Docs to Update).

Consumed, not changed:
- NB-18: The `GET /api/events` SSE route (`app.py:5224`).
- NB-19: `static/index.js`'s handling of the `/api/state` body.
- NB-20: `static/layout.css`'s existing rules other than the one added.
- NB-21: The captured baselines `tests/fixtures/dashboard/primitives_baseline/baseline.json` and `tests/fixtures/dashboard/shell_baseline/baseline.json` (Q-4).

## Defect Classes
| DC-n | Class (what goes wrong) | Trigger (B or N1-N8) | Sibling search (tool, query, scope) | Scenarios | Raised in review as |
|---|---|---|---|---|---|
| DC-1 | Contract change not carried to every consumer: a reader of `html` or a sender of `sortCol` is missed | B | grep: `"html"`, `'html'`, `data.html`, `sortCol`, `sortDir`, `sort_col`, `sort_dir`, `table_partial` in `app.py`, `static/`, `templates/`, `tests/`, `scripts/`; grep: every patched `render_template` in `tests/` whose captured context is read for `accounts_map`, `account_labels`, `sort_col`, `sort_dir` or `data_as_of` (for example `tests/app/test_dashboard_m2.py:1033-1043`); AlphaBotPM only; stop when a widening returns only page-HTML test worlds (`world["html"]`) or stubs that ignore their arguments, already judged unrelated | S-1.1, S-1.2, S-1.3, S-1.4, S-1.5, S-1.6, S-1.7, S-1.8, S-6.1, S-6.2, S-6.5 | none |
| DC-2 | Data that must stay unchanged is changed: a side effect that fed the template also fed the body and goes with it | N7 | read: every statement in `get_state` (`app.py:5428-5967`) that writes to `state_data`, `snapshot`, `_snap_accounts_map` or an entry dict (stamping, `setdefault`, in-place `sort`) | S-2.2, S-3.2, S-3.5, S-3.7 | none |
| DC-3 | Absent or not-computed value served as a default, or a default dropped so absent replaces null | N3 | grep: `_FROZEN_SYM_DEFAULTS`, `_normalize_frozen_symphonies`, `setdefault` in `app.py` | S-3.2, S-3.3, S-3.4 | none |
| DC-4 | Rule enforced at one entry point, missing at another: `html` dropped from one branch and left in another | N2 | grep: `jsonify(` inside `get_state`, every return path | S-1.1, S-1.2, S-1.3, S-1.4, S-1.5, S-1.6, S-1.7, S-1.8, S-1.9, S-1.10, S-1.11, S-1.12, S-1.13 | none |
| DC-5 | Narrow assertion licenses unlisted siblings: one branch or one defaulted field tested | B, N1 | read: the tests replacing the presence pins; check each branch of the matrix and each of the 13 defaulted fields has a row | S-1.1, S-1.2, S-1.3, S-1.4, S-1.5, S-1.6, S-1.7, S-3.2 | none |
| DC-6 | A moved style rule reaches a page or element it did not reach before, or loses a cascade tie it used to win | N7 | grep: `hero-section`, `section-header` in `templates/` and `static/*.js`; read: each page's inline rules on `.section-header` with three or more classes or pseudo-classes, before and after the old position | S-7.6, S-7.7 | none |
| DC-7 | Dependency failure swallowed or answered differently once the table's fallback is gone | N6 | read: every `try` in `get_state` and what each `except` serves | S-1.9, S-2.6, S-2.7, S-2.9, S-3.12 | none |
| DC-8 | Planned example values not encoded in a test | B | read: the Worked Example against the AC-1, AC-3 and AC-4 tests | S-3.5, S-4.1 | none |

## Worked Example
Request: `GET /api/state` from an authenticated dashboard after the close (market `closed_frozen`), with a stored snapshot whose ACCT-ALPHA list holds Birch Momentum (hash `Zq4mT8vLr2Xw9KpB1nYd`) then Ash Ladder (hash `Hc7pV3sRn6Wd2LmQ8tFa`), where Ash Ladder's entry has no `shadow_hwm`.

At the ref the body carries `status`, `market_state`, `frozen_at`, `trading_day`, `snapshot_is_previous_session`, `data_as_of`, `state`, `bot_state`, `live_mode`, `portfolio_strip`, `shadow_divergence`, `accounts_map`, `symphonies`, `fleet_correlation_alert`, `html`, `meta`, `port_state`, `exit_authority`, `daemon_started_at`, `engine_phase`, `degraded_reasons` and `dashboard_panels`. After the change the body carries the same keys except `html`, with equal values: `accounts_map["ACCT-ALPHA"]` lists Ash Ladder before Birch Momentum (name order), and Ash Ladder's entry carries `shadow_hwm` 0.0 in `state`, `bot_state` and `accounts_map`, as at the ref (S-1.2, S-3.1, S-3.2, S-3.5).

Failure case: the same request with `?sortCol=status&sortDir=desc`. At the ref the ACCT-ALPHA list followed the status rank, descending. After the change the body equals the body without the parameters, Ash Ladder first (S-4.1). Unauthenticated, the same request answers 401 with `{"error": "unauthenticated"}` and no state (S-1.10).

## Fail-Closed Behaviour
none: no input now refuses. The sort parameters are ignored rather than refused (AC-4, S-4.7), and the route's existing refusals, 401 unauthenticated (`app.py:313-315`, S-1.10) and 503 misconfigured for a missing secret key (`app.py:290-296`, S-1.12) or a missing dashboard credential (`app.py:298-306`, S-1.13), are unchanged.

## Authorities Touched
| Value | Authority used | What it replaced | What proves no second source |
|---|---|---|---|
| Zero top margin of a `.section-header` that is a `.hero-section`'s first child | one rule in `static/layout.css` | four inline copies (`templates/ai_advisor.html:50`, `templates/history.html:40`, `templates/performance.html:44`, `templates/settings.html:33`) | S-7.1 |
| Order of each account list in the close-snapshot body | the route's default name order, as at the ref (`app.py:5574-5578`) | the same order or a `sortCol`/`sortDir` order | S-3.5, S-4.1 |

## Contract Changes and Consumers
| Wire shape | Before (758f7f95) | After | Backward compatible | Consumers (AlphaBotPM) and what each sees |
|---|---|---|---|---|
| `GET /api/state` body | carries `html`, a server-rendered table string, on the live and close-snapshot branches | no `html` key on any branch; every other key and value unchanged | yes for every in-repo reader | `static/index.js` never reads `html` (`static/index.js:2757-2761`, `:2788-2810`), so it sees no difference; tests that pin the key change with this work (AC-6) |
| `GET /api/state` query | `sortCol`, `sortDir` order the table and, on the close-snapshot branch, the `accounts_map` lists (`app.py:5513-5578`, `:5747-5806`) | ignored | yes for every in-repo sender: `static/index.js` sends only `?account=` (`static/index.js:2790-2792`) | tests that send them: `tests/app/test_app_routes.py:107-163`, `tests/app/test_dashboard_m2.py:645-720`, `tests/dashboard/test_frozen_html_rendering.py:281-332` |

Test census (AlphaBotPM `tests/`, at the ref), each changed with this work under Q-1:

| Test | What it pins | Kind |
|---|---|---|
| `tests/app/test_f011_state_field_availability.py:218` | the open body carries `html` | presence pin |
| `tests/dashboard/test_cards_live_refresh.py:494-503` | `html` among the keys that must stay | presence pin |
| `tests/dashboard/test_frozen_html_rendering.py:88-151`, `:151-192`, `:281-332`, `:335-392`, `:394-428`, `:430-474` | AC-H1, AC-H2, AC-H4 to AC-H9: `html` present and its rows; sort honoured; a missing `shadow_hwm` renders | presence pin and render |
| `tests/dashboard/test_frozen_html_rendering.py:197-275` | AC-H3: analytics read the snapshot's trading day | live, kept (S-6.4) |
| `tests/app/test_dashboard_m2.py:312-393`, `:453-579`, `:580-644`, `:789-812` | per-symphony TC, CR, MDD columns, headers, the shadow peak cell and the freshness text, read from `html` | reader of the key |
| `tests/app/test_dashboard_m2.py:394-452` | the per-symphony analytics helpers are called once per symphony | live, kept (S-6.4) |
| `tests/app/test_dashboard_m2.py:645-720`, `:1033-1043` | the table context read from a patched `render_template` | render-context reader |
| `tests/app/test_app_routes.py:107-163` | the status sort | sort sender |
| `tests/app/test_dashboard_m2fix.py:32`, `:382-580` | the template source and its TC, CR, MDD cells | template source |
| `tests/app/test_dashboard_vertical_fill.py:27`, `:157-167` | no inline max-height in the template | template source |
| `tests/dashboard/test_f7_ac3_tooltip_first_sentence.py:43-61` | the MC Prob tooltip text in the template | template source |
| `tests/dashboard/test_r15_shadow_names.py:46`, `:497-545` | table rows carry `data-symphony-id` | template source |
| `tests/shadow/test_shadow_history.py:1118-1134` | the "Held Return" header | template source |
| `tests/reporting/test_inv_cov_4_stop_consumers_handle_decrease.py:189-250` | the template renders a decreased stop | template render |
| `tests/ui/test_dash_advisor_fixes.py:315-431` | the table MDD column is not multiplied by 100 | template render |
| `tests/ui/test_persymph_settings_modal.py:1511-1589` | table rows carry the settings gear | template render |
| `tests/fixtures/dashboard/frozen_html/frozen_html_missing_shadow_hwm.json`, `frozen_html_with_symphonies.json` | inputs to the frozen render tests | fixture (S-6.6) |
| `tests/dashboard/test_cards_live_updates.py:326-391`, `tests/dashboard/test_card_consistency_liveness.py:733-745`, `tests/dashboard/test_cards_live_refresh.py:405-419`, `tests/dashboard/test_symph_autoupdate.py:227-247` | `static/index.js` never puts `data.html` into the DOM | kept (S-6.3) |

## Sibling Repos and Landing Order
none: AlphaBotPM is the only repository this changes (NB-7).

## Migrations Required
none: no schema or data change (NB-12).

## Configuration and Enablement
none: no configuration key is added or changed and nothing ships dark (NB-12).

## Breaking Changes
- `GET /api/state` no longer sends `html`. No in-repo reader exists; an out-of-repo reader is unconfirmed (Known-Unconfirmed).
- `GET /api/state` no longer orders the close-snapshot `accounts_map` by `sortCol`/`sortDir`. No in-repo sender exists; an out-of-repo sender is unconfirmed (Known-Unconfirmed).

## Security Considerations
- Input validation and injection: `sortCol` and `sortDir` are no longer read (AC-4); no new input is read.
- Authorization and access control: the global auth hook and its 401 and 503 answers are unchanged (AC-1, S-1.10, S-1.12, S-1.13).
- Data exposure: the body loses a rendered table that repeated symphony names and account labels already in `state` (AC-1); nothing new is exposed.
- Client-side rendering: no script writes a server-rendered HTML string; the pin that `static/index.js` never puts `data.html` into the DOM stays (S-6.3).
- Abuse or rate limits: n/a; the route's cost per poll falls by the table render.

## Prerequisites
none: no new runtime, service or provider; the route and the pages need what they need at the ref.

## Design-System Mapping
none: no element is added, removed or restyled; the one moved rule keeps its selector and its declaration, and AC-7's scenarios prove computed styles unchanged.

## Definition of Done (Verification)
| AC | Scenarios | Test level | Real-entrypoint test |
|---|---|---|---|
| AC-1 | S-1.1, S-1.2, S-1.3, S-1.4, S-1.5, S-1.6, S-1.7, S-1.8, S-1.9, S-1.10, S-1.11, S-1.12, S-1.13 | route | `GET /api/state` through the Flask test client in each seeded world (`tests/app/_refresh_timing_state_world.py:47`, branches open, `closed_frozen`, `pre_market`, extended to the stalled open, the held view, closed with no snapshot and no state) |
| AC-2 | S-2.1, S-2.2, S-2.3, S-2.4, S-2.5, S-2.6, S-2.7, S-2.8, S-2.9, S-2.10, S-2.11 | route | `GET /api/state` against the same seeded world, compared with the ref body |
| AC-3 | S-3.1, S-3.2, S-3.3, S-3.4, S-3.5, S-3.6, S-3.7, S-3.8, S-3.9, S-3.10, S-3.11, S-3.12, S-3.13 | route | `GET /api/state` and `GET /` in the closed seeded world, compared with the ref body |
| AC-4 | S-4.1, S-4.2, S-4.3, S-4.4, S-4.5, S-4.6, S-4.7, S-4.8 | route | `GET /api/state` with and without the parameters |
| AC-5 | S-5.1, S-5.2, S-5.3, S-5.4, S-5.5, S-5.6, S-5.7, S-5.8, S-5.9 | browser | a committed test drives the dashboard in a browser over the S-2.1 and S-3.1 worlds and compares its rendered text, the detail-panel footer and the requests per poll with the committed ref capture (Fixtures); the existing `static/index.js` harness (`tests/dashboard/_index_js_harness.py`) still passes; the behavioral-evidence-capturer adds screenshots |
| AC-6 | S-6.1, S-6.2, S-6.3, S-6.4, S-6.5, S-6.6, S-6.7, S-6.8 | repository scan and route | a tree scan test plus the AC-1 route tests |
| AC-7 | S-7.1, S-7.2, S-7.3, S-7.4, S-7.5, S-7.6, S-7.7, S-7.8, S-7.10 | stylesheet scan and browser | a committed test compares the computed style of every `.section-header` element and every direct child of a `.hero-section` on all six pages, at 1440 and 375 px, light and dark, with the committed ref capture (Fixtures) |

Gates:
- `ruff format --check` and `ruff check`.
- A scoped pytest run over the touched and consuming suites (`tests/app/`, `tests/dashboard/`, `tests/ui/`, `tests/reporting/`, `tests/shadow/`, `tests/execution/test_cycleat_fix.py`, `tests/test_scope_guard_f7.py`), never `-n auto` and never more than 4 workers.
- CI green on the exact SHA that lands on main: the reviewed merge commit is pushed to `assembly/dead-table-partial`, and main is fast-forwarded to that SHA only after its CI is green (Q-3).
- The PM's live functional check after the review: closed-market and open-market `/api/state` bodies from the running daemon, and the six pages viewed in a browser (Q-3).

Fixtures:
- The ref bodies AC-2, AC-3 and AC-4 compare against are captured from the real route at 758f7f95 in the committed seeded worlds, by a committed producer in the same form as `tests/fixtures/refresh_timing/01-c2/produce_golden_ref.py`; none is hand-built.
- Snapshot and state inputs come from the existing captured records (`tests/fixtures/refresh_timing/01-c2/snapshot_two_symphonies.json`, `live_state_two_cycles.json`, `tests/fixtures/prod_droplet/bot_state_positions.json`); a case none of them holds (a missing default field, an unnamed entry) is one record derived from a captured one, with its provenance naming the case.
- The primitives baselines are not edited (NB-21).
- The computed-style capture for AC-7 and the page capture for AC-5 are produced at 758f7f95 by a committed browser producer in the form of `tests/ui/_shell_capture.py` and `tests/dashboard/_first_paint_dom.py` (`getComputedStyle` over the real routes, with the seeded worlds), committed with a provenance note naming the ref, the pages, the widths 1440 and 375 px and the light and dark themes. The AC-7 capture holds every `.section-header` element and every direct child of a `.hero-section` on each page, the only elements the moved selector can match or whose margin its move can change.

Screens: each of the six pages compared with the ref at 1440 and 375 px, light and dark, by the behavioral-evidence-capturer.

## Success Measures
- Size of the uncompressed JSON body of `GET /api/state` on the production daemon, market `closed_frozen`, for one stored snapshot: at the ref the PM's live read measured 177,727 bytes, of which the `html` string was 75,052 characters (42 percent), at 2026-11-03T21:46:44Z. After deploy the uncompressed body for a snapshot of the same symphonies is smaller by about the `html` string's length plus its key; the compressed wire size falls by the compressed share of that string (`deploy/caddy/Caddyfile:24-26`). Read by the PM's closed-market check after deploy, uncompressed and with `Accept-Encoding: gzip`.

## Known-Unconfirmed and Standing Risk
- An out-of-repo reader of `html` or sender of `sortCol`/`sortDir` (an operator script or monitor) cannot be ruled out from the code. Read-only check on the daemon host: search the request timing lines (`timing_log.py` writes one line per HTTP request) for `/api/state?` with `sortCol` or `sortDir`, over the retained log window.
- The size figures in Success Measures are one live sample from the brief. The tracked proxy config compresses JSON (`deploy/caddy/Caddyfile:24-26`, `encode gzip zstd` for `application/json`), so the wire saving is the compressed size of the removed string, smaller than its 42 percent share of the uncompressed body. Whether the droplet's `/etc/caddy/Caddyfile` equals the tracked file is not confirmable from the code; read-only check on the host: `cmp /etc/caddy/Caddyfile deploy/caddy/Caddyfile`.
- The close-snapshot view is held per snapshot and shared between the poll and `GET /` (`app.py:4339-4345`, `:4706-4732`), so the fields the poll fills and the order it sets persist on the held object; S-3.7 guards that the poll's body does not depend on whether a page render ran first.
- The AC-7 capture holds the elements the moved selector can reach, not every element on a page; the screenshots compare the rest.
- When `static/layout.css` fails to load, the moved rule is lost with every other shared primitive; at the ref the inline copies would still have applied. Accepted, not tested (AC-7 N6).
- Delivery and production deploy timing depend on Q-3.

## Open Questions
| Q-n | Question | Blocks (AC ids or "plan") | Proposed default | Answer |
|---|---|---|---|---|
| Q-1 | For each test that references the template or the `html` key, is it retired or rewritten? | AC-6 | a test whose only subject is the template or the `html` key is deleted; a test that also covers something live keeps its live assertions and drops the table part; the pins that `static/index.js` never puts `data.html` into the DOM are kept, and a route test that no branch sends `html` replaces the presence pins | default approved [PM-ASSUMED] (PM, session 8055360f, 2026-11-03T23:58Z) |
| Q-2 | Are `sortCol` and `sortDir` retired? The code shows no client sends them (`static/index.js:2790-2792`), but on the close-snapshot branch they also ordered the response's `accounts_map` (`app.py:5539-5578`, `:5633`), which no client reads | AC-4 | retire them; the close-snapshot `accounts_map` keeps the default name order (AC-3) and ignores the parameters | default approved [PM-ASSUMED] (PM, session 8055360f, 2026-11-03T23:58Z) |
| Q-3 | How is the change delivered? `.claude/CLAUDE.md` "Merge & PR Workflow (HARD REQUIREMENT)" requires a PR, `/review` and a live check before merge, while the operator's standing rule relayed by the PM is no PRs | plan | the operator's standing rule: no PR; the PM merges to main and pushes after a behavioral check against the real app; one review at the end; the droplet is updated only after the Wednesday 2026-11-04 close, with a closed-market check and a next-morning open proof | default approved [PM-ASSUMED] (PM, session 8055360f, 2026-11-03T23:58Z): the operator's standing no-PR rule supersedes the `.claude/CLAUDE.md` Merge & PR Workflow for this change; the PM pushes the reviewed merge commit to the branch `assembly/dead-table-partial` so GitHub CI runs, then fast-forwards main to that same SHA only after CI is green |
| Q-4 | `tests/ui/test_dashboard_primitives_shared.py:237-262` pins `static/layout.css`'s primitive rules byte for byte to a captured baseline, which a new rule breaks. How is the pin kept? | AC-7 | the captured baseline files are not edited; the tests state the one added rule as the only allowed difference, and every rule the baseline holds stays byte for byte | default approved [PM-ASSUMED] (PM, session 8055360f, 2026-11-03T23:58Z) |
| Q-5 | Does "a true duplicate of a layout.css rule" in the intake mean a whole rule or a single repeated declaration? | AC-7 | a whole rule; no dashboard page-scoped rule is one, so none changes; repeated single declarations are a follow-up (NB-14) | default approved [PM-ASSUMED] (PM, session 8055360f, 2026-11-03T23:58Z) |
| Q-6 | The source says `.section-header` and `.mini-stat` are refined per page in three places. The code shows the one cross-page copy is the margin rule on four pages; the `.mini-stat` refinements are dashboard-scoped or use the Advisor's own classes. Is the margin rule the whole fold? | AC-7 | yes; `.mini-stat` is unchanged (NB-4) | default approved [PM-ASSUMED] (PM, session 8055360f, 2026-11-03T23:58Z) |
| Q-7 | `feature-plans/refresh-timing/01-server-state-truth.md` NB-2 and Q-4 kept `html` as part of the route's documented contract. Does this plan supersede that for `html`? | AC-1 | yes, for `html` only; the other unread keys stay (NB-1) | default approved [PM-ASSUMED] (PM, session 8055360f, 2026-11-03T23:58Z) |

## Decisions
| ID | Decision | Rationale |
|---|---|---|
| PD-1 | The close-snapshot fields the template needed filled (`app.py:4441-4455`) stay filled in the poll body | they reach `state`, `bot_state`, `accounts_map` and the cards, and the intake keeps every value (AC-3) |
| PD-2 | Each symphony entry in the live body keeps `id` and `normalized_name` | the detail panel reads both (`static/index.js:1668`, `:1694`, `:1751`) and only this table-building loop writes them on the poll (`app.py:5729-5733`) |
| PD-3 | Removing the template closes DE-MATH-F7-001 backlog items 1 and 2 (`DECISIONS.md:7683-7704`) | the orphaned surface and its dead `openChartModal` button go together |

## Docs to Update
- `docs/generated/app.md`: the `/api/state` section, for the removed `html` key and the retired sort parameters.
- `docs/generated/static_index_js.md:206` and `docs/generated/alpha_bot_execution.md:133`, `:143`: the paragraphs that describe the table template.
- `docs/generated/INDEX.md`: the entries for the pages above.
- `DECISIONS.md`: one appended entry, minted at ship by the doc writer, recording the removal, the closure of DE-MATH-F7-001 backlog items 1 and 2, and the supersession of `feature-plans/refresh-timing/01-server-state-truth.md` NB-2 for `html`.
- `.claude/CLAUDE.md`: a drafted Key Files note for `app.py` (the state poll carries no rendered table) and `static/layout.css`, for PM approval.
- `.claude/agents/flask-dashboard-specialist.md:3`, `:15`: drafted for PM approval (NB-17).

## Follow-Ups
- NB-13 (scrollbar rules on four pages), NB-14 (repeated single declarations in the dashboard's scoped rules), NB-15 (stale `static/settings.js:259` comment), NB-16 (close-snapshot `current_return` and `shadow_hwm` filled with 0.0 and printed by the detail panel): each tracked as one backlog line by the PM at ship, per the intake's deferral rule.
- NB-2 (untracked files and stale plans outside this change): tracked by the PM as its own cleanup cycle.
- Raised by the Gate-2 architect, not a criterion: in both branches of `GET /api/state` the `live_mode` value in the body literal (`app.py:5630`, `:5935`) is overwritten by `**_additive` (`:5648`, `:5953`), so `_frozen_live_mode` and the live branch's own `live_mode` read are dead code; deferred to the dead-code cycle.
