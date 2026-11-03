# Approval packet: Retire the dead state-poll table render and define the shared heading margin rule once

## What is being built

The state poll stops computing and sending an HTML table no client reads, the dead template goes, and the one heading rule four pages repeat is defined once in the shared stylesheet, with every page and every other response value exactly as it is at the ref.
- `GET /api/state` carries no `html` key in any branch (AC-1).
- Every other key and value of the live branch (AC-2) and of the close-snapshot branch (AC-3) is unchanged.
- The `sortCol` and `sortDir` query parameters, which only ordered the table, are no longer read (AC-4).
- The dashboard page shows and requests exactly what it does at the ref (AC-5).
- `templates/table_partial.html` and every test that pins it or the `html` key are gone or rewritten (AC-6).
- The heading margin rule lives once in `static/layout.css` and computed styles on all six pages are unchanged (AC-7).

## Acceptance criteria and scenarios

### AC-1: The state poll carries no table markup in any branch
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

Negative coverage: N1 S-1.8 | N2 S-1.12, S-1.13 | N3 S-1.4, S-1.5, S-1.6, S-1.7 | N4 S-1.10 | N5 S-1.11 | N6 S-1.9 | N7 covered by AC-2 and AC-3 | N8 n/a: the route is a read and holds no edit open; the page side is AC-5

### AC-2: Every other value of the live branch is unchanged
Rule: When `GET /api/state` serves the live branch (market open with a committed cycle, or closed with no snapshot), the route shall send every key and value that 758f7f95 sends for the same stored state, query and clock, with only `html` absent.

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

Negative coverage: N1 S-2.3, S-2.4, S-2.5 | N2 n/a: the live branch refuses nothing beyond AC-1's 401 and 503 | N3 S-2.8, S-2.11 | N4 covered by AC-1 S-1.10 | N5 S-2.10 | N6 S-2.6, S-2.7, S-2.9 | N7 S-2.1, S-2.2 | N8 n/a: the route holds no edit open

### AC-3: Every other value of the close-snapshot branch is unchanged
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

Negative coverage: N1 S-3.2, S-3.3, S-3.4, S-3.6, S-3.13 | N2 n/a: the branch refuses nothing beyond AC-1's 401 and 503 | N3 S-3.8, S-3.9, S-3.10, S-3.11 | N4 covered by AC-1 S-1.10 | N5 S-3.7 | N6 S-3.12 | N7 S-3.1, S-3.5 | N8 n/a: the route holds no edit open

### AC-4: The table's sort parameters are no longer read
Rule: If `GET /api/state` is requested with a `sortCol` or `sortDir` query parameter, then the route shall answer exactly as it answers the same request without them.

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

Negative coverage: N1 S-4.3, S-4.4, S-4.5, S-4.6 | N2 S-4.7 | N3 S-4.8 | N4 n/a: the parameters do not reach the auth hook, which runs first (AC-1 S-1.10) | N5 n/a: the route is a stateless read, so a repeated parameter has no effect to accumulate | N6 n/a: the parameters read no dependency | N7 S-4.1, S-4.2 | N8 n/a: the route holds no edit open

### AC-5: The dashboard page shows and requests what it does at the ref
Rule: When the dashboard page applies a `GET /api/state` body (first poll, the 30 s fallback poll, or a refresh on an SSE `cycle-complete` event), the page shall show the same figures, labels, cards, panels and detail panel, and send the same requests, as 758f7f95 does for the same stored state and clock.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-5.1 | B | market open, the S-2.1 state | the dashboard loads and its first poll applies, at 1440 px, light theme | every figure, label, card and panel text equals the ref page's |
| S-5.2 | B | market `closed_frozen`, the S-3.1 snapshot | the dashboard loads and polls | every figure, label, card and panel text equals the ref page's |
| S-5.3 | N5 | as S-5.1 | the SSE stream (`static/index.js:2858-2862`) sends `cycle-complete` | the page makes one `GET /api/state` request with the same URL as at the ref (no `sortCol`), and updates as at the ref |
| S-5.4 | N8 | the detail panel is open on Maple Rotation | a poll lands | the footer reads `id: ` then the Composer symphony hash ` | normalized: ` then the normalized display name, and Risk Math shows the same parabolic velocity text, as at the ref (`static/index.js:1691-1695`, `:1443`) |
| S-5.5 | N6 | the route answers 500 | the poll runs | the page keeps every value and shows the "connection lost" cue, as at the ref (`static/index.js:2763-2770`) |
| S-5.6 | N4 | the session has expired | the poll runs | the poll answers 401 and the page goes to the login page, as at the ref |
| S-5.7 | N1 | as S-5.2 | the dashboard is viewed at 375 px in the dark theme | the page equals the ref page at the same width and theme |
| S-5.8 | N3 | no bot state | the dashboard loads | the page shows the same initializing state as at the ref |
| S-5.9 | N7 | as S-5.1 | a poll runs | the page's network requests per poll match the ref in URL and count |

Negative coverage: N1 S-5.7 | N2 n/a: the page refuses no input on this path | N3 S-5.8 | N4 S-5.6 | N5 S-5.3 | N6 S-5.5 | N7 S-5.9 | N8 S-5.4

### AC-6: The dead template and every pin on it are gone
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

Negative coverage: N1 S-6.5, S-6.6, S-6.7 | N2 n/a: a repository state refuses nothing | N3 n/a: no value is computed | N4 n/a: no access path is involved | N5 n/a: the removal happens once | N6 n/a: no dependency is called | N7 S-6.3, S-6.4, S-6.8 | N8 n/a: no edit is held open

### AC-7: The heading margin rule is defined once in the shared stylesheet
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

Negative coverage: N1 S-7.7, S-7.8 | N2 n/a: a stylesheet refuses nothing | N3 n/a: no value is computed or awaited; hidden tabs are covered by S-7.4 when shown | N4 n/a: every page that links the sheet already reaches it; login is covered by S-7.6 | N5 n/a: a static stylesheet has no repeated action | N6 n/a: when `static/layout.css` fails to load, every shared primitive (`static/layout.css:97-155`) is already lost at the ref, and the moved rule joins that set (Known-Unconfirmed) | N7 S-7.6, S-7.10 | N8 n/a: no edit is held open on these pages; the run-result headings are AI Advisor's, covered by S-7.4 (S-7.9 removed)

## Rule-by-entry-point matrix

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

## Narrowed mappings

R-2: No two codepaths produce the same outcome, and nothing does what something elsewhere already does; maps to AC-4, AC-6, AC-7, NB-13, NB-14; left out: covers the table render with its two copies of the sort logic and the four copies of the heading rule; other duplication found is deferred (NB-13, NB-14)
R-3: Old files lying around (screenshots, stale plans) are cleaned up; maps to AC-6, NB-2; left out: covers the dead template and the test fixtures only it used; untracked files, screenshots and stale plans elsewhere are NB-2
R-9: The per-page header refinements are folded into `layout.css`; maps to AC-7, NB-3, NB-4; left out: only the one rule repeated on four pages moves; the `.mini-stat` and dashboard-scoped refinements stay (Q-5, Q-6)

## Open questions

| Q-n | Question | Blocks (AC ids or "plan") | Proposed default | Answer |
|---|---|---|---|---|
| Q-1 | For each test that references the template or the `html` key, is it retired or rewritten? | AC-6 | a test whose only subject is the template or the `html` key is deleted; a test that also covers something live keeps its live assertions and drops the table part; the pins that `static/index.js` never puts `data.html` into the DOM are kept, and a route test that no branch sends `html` replaces the presence pins | default approved [PM-ASSUMED] (PM, session 8055360f, 2026-11-03T23:58Z) |
| Q-2 | Are `sortCol` and `sortDir` retired? The code shows no client sends them (`static/index.js:2790-2792`), but on the close-snapshot branch they also ordered the response's `accounts_map` (`app.py:5539-5578`, `:5633`), which no client reads | AC-4 | retire them; the close-snapshot `accounts_map` keeps the default name order (AC-3) and ignores the parameters | default approved [PM-ASSUMED] (PM, session 8055360f, 2026-11-03T23:58Z) |
| Q-3 | How is the change delivered? `.claude/CLAUDE.md` "Merge & PR Workflow (HARD REQUIREMENT)" requires a PR, `/review` and a live check before merge, while the operator's standing rule relayed by the PM is no PRs | plan | the operator's standing rule: no PR; the PM merges to main and pushes after a behavioral check against the real app; one review at the end; the droplet is updated only after the Wednesday 2026-11-04 close, with a closed-market check and a next-morning open proof | default approved [PM-ASSUMED] (PM, session 8055360f, 2026-11-03T23:58Z): the operator's standing no-PR rule supersedes the `.claude/CLAUDE.md` Merge & PR Workflow for this change; the PM pushes the reviewed merge commit to the branch `assembly/dead-table-partial` so GitHub CI runs, then fast-forwards main to that same SHA only after CI is green |
| Q-4 | `tests/ui/test_dashboard_primitives_shared.py:237-262` pins `static/layout.css`'s primitive rules byte for byte to a captured baseline, which a new rule breaks. How is the pin kept? | AC-7 | the captured baseline files are not edited; the tests state the one added rule as the only allowed difference, and every rule the baseline holds stays byte for byte | default approved [PM-ASSUMED] (PM, session 8055360f, 2026-11-03T23:58Z) |
| Q-5 | Does "a true duplicate of a layout.css rule" in the intake mean a whole rule or a single repeated declaration? | AC-7 | a whole rule; no dashboard page-scoped rule is one, so none changes; repeated single declarations are a follow-up (NB-14) | default approved [PM-ASSUMED] (PM, session 8055360f, 2026-11-03T23:58Z) |
| Q-6 | The source says `.section-header` and `.mini-stat` are refined per page in three places. The code shows the one cross-page copy is the margin rule on four pages; the `.mini-stat` refinements are dashboard-scoped or use the Advisor's own classes. Is the margin rule the whole fold? | AC-7 | yes; `.mini-stat` is unchanged (NB-4) | default approved [PM-ASSUMED] (PM, session 8055360f, 2026-11-03T23:58Z) |
| Q-7 | `feature-plans/refresh-timing/01-server-state-truth.md` NB-2 and Q-4 kept `html` as part of the route's documented contract. Does this plan supersede that for `html`? | AC-1 | yes, for `html` only; the other unread keys stay (NB-1) | default approved [PM-ASSUMED] (PM, session 8055360f, 2026-11-03T23:58Z) |
