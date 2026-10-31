# Feature: Pages and header: every page refreshes honestly, and every control survives a restart without ever re-sending
Status: planned
Story: none: operator chat request on 2026-10-22, saved verbatim in feature-plans/refresh-timing/00-overview.source.md
Type: fix
Suite: refresh-timing 04
Repos: PlanetStopper
Base: the ux-overhaul 04 tip (the app shell, which stacks on ux-overhaul 03, 02 and 05)
Ref: PlanetStopper@37c8e983b64c6980c28199a3f9318486b60f3864
Sensitive: security, money (AC-2, AC-7)
Depends on: ux-overhaul plan 04 (branch `ux-overhaul/fixtures` holds its plan; it rewrites every page header and `static/chrome.js`); plan 02 merged into this plan's tip before the AC-1 cycle (the header's scheduled-pause labels); plan 03 merged into this plan's tip before the AC-2 cycle, because both edit `static/index.js` (Cash Now); the model-unpin plan through ux-overhaul 04's own tip

## Trigger
The operator wrote "I expect any and all timing issues to be resolved as well, I'm tired of the wonky updates and / or no updates to data on the poll" (G-1, saved in `feature-plans/refresh-timing/00-overview.source.md`). The refresh-timing audit at the ref (a review artifact outside the tracked tree, dated 2026-10-22) assigned this plan the findings on the pages other than `/` and on every page's POST controls. Each was re-read at the ref for this spec:

1. TA-5: the CSRF token is minted once per process at import (`app.py:339`) and checked with `abort(403)` (`app.py:373-379`), which answers HTML because `app.py` has no error handler; eight scripts fetch it once at load (`static/chrome.js:5-10`, `static/index.js:15-20`, `static/settings-modal.js:27-32`, `static/settings.js:32-37`, `static/ai_advisor.js:13-18`, `static/ai_advisor_asset_swaps.js:17-22`, `static/ai_advisor_logic_changes.js:19-24`, `static/ai_advisor_chat.js:264-272`) and none re-fetches. After each of the 4 restarts on 2026-10-22, every open tab's POSTs are refused: Emergency Liquidate's modal stays open with no message (`static/chrome.js:189-196`), Cash Now resets with no message (`static/index.js:414-421`), and Settings says "Save failed: network error". A login form left open across a restart gets Werkzeug's raw 403 page (`app.py:247`).
2. TA-20: `/history`, `/performance`, `/settings` and `/ai-advisor` render the header with `_build_meta({}, ...)` (`app.py:5155`, `:5173`, `:5983`, `:7179`), whose `next_run_seconds` defaults to 0 (`app.py:1837`, `:1883`); `updateEngineStatus` and `resyncNextCountdown` run only from `static/index.js:1401`; so those pages read "Live" or "Closed" as rendered, never turn "Closed" at 16:00 or "Stale", and show "NEXT 00:00" that never counts (`static/chrome.js:287-292`). The mode pill is render-only on every page (`templates/_chrome.html:93-95`).
3. TA-28: the `/history` 30 s poll calls `renderTriggers(payload)` (`static/history.js:398-432`), erasing a drilled day within 30 s; `analytics.get_history_summary` picks today's file with the host date (`analytics.py:2372`), so on the UTC host "Today's exits" switch basis from 20:00 EDT; there is no `ok` check (`static/history.js:401-403`), so a 401 body wipes the page into "No exit records", and the page shows no data age.
4. TA-34: `/performance` renders the chart and the metrics in one `.then`, so a chart exception skips the metrics (`static/performance.js:565-570`); one failed exit-turnover request drops that row and any failure empties the panel (`static/performance.js:880-897`); the page has no data age.
5. TA-35: `/ai-advisor`'s 15 s poll rebuilds the runs list (`static/ai_advisor.js:620-623`), and a non-array error body reads "No tuning runs recorded yet." (`static/ai_advisor.js:512-517`); the Frontrunner Run build button re-enables in `finally` right after the 202 and tells the operator to reload (`static/ai_advisor.js:990-1011`).
6. TA-37: `/settings` retries its load once after 400 ms and then shows nothing (`static/settings.js:66-83`); the flush confirm fetches all of `/api/state` (5,666,593 B after the close, 3.3 to 3.8 s) to read the armed symphonies (`static/settings.js:308-326`).
7. TA-38: `openSymphonySettings` applies whichever response lands last with no check against `_currentSymphonyId` (`static/settings-modal.js:66-86`), and Save posts `live_mode` and `locked_vars` to `_currentSymphonyId`, so an A-LIVE state can be posted to B without the go-live confirmation.

## Summary
The operator keeps every page open through restarts and the trading day, and each page's header follows the engine, each page shows its data's age and never draws an error as data, and each POST control works after a restart without ever sending anything he did not click again.
- The header on the four other pages follows the engine, the countdown and the live flag (AC-1).
- Every POST control refreshes its token after a restart, never re-sends, and says when an action was not sent (AC-2).
- `/history` keeps a drilled day, uses the ET date and shows its data age (AC-3).
- `/performance` renders each panel independently and shows its data age (AC-4).
- `/ai-advisor` never shows an error as an empty list and signals a Frontrunner build's end (AC-5).
- `/settings` shows a load failure, and its flush confirm reads a small response (AC-6).
- The settings modal never saves one symphony with another's data (AC-7).

## Requirements as Given
[G-1] I expect any and all timing issues to be resolved as well, I'm tired of the wonky updates and / or no updates to data on the poll. 00-overview.source.md:6
[G-12] PM Step-2 answers ([PM-ASSUMED]; the operator is not available for questions; project directive: record assumptions, do not stop) 00-overview.source.md:18
[G-13] (Building 1) Every number, chart, label and status on every page shows the newest data the engine has produced. 00-overview.source.md:21
[G-14] (Building 1) It updates without a reload, never reverts or flickers when no new data arrived, and never shows stale or wrong-day data without a dated, honest cue. 00-overview.source.md:21
[G-15] (Building 1) It recovers by itself after a daemon restart, laptop sleep, tab return or network loss. 00-overview.source.md:21
[G-16] (Building 2) Users: the operator, through `/`, `/history`, `/performance`, `/ai-advisor`, `/settings` and the shared header. 00-overview.source.md:22
[G-20] (Building 3) Along the way: pick a hero window, pick and clear an account, open a detail panel, use Cash Now, Force run and Emergency Liquidate. 00-overview.source.md:24
[G-21] (Building 3) Keep tabs open across a deploy or restart and a laptop sleep; switch tabs and come back; use the other pages. 00-overview.source.md:25
[G-22] (Building 3) Expected: values change within one cycle of new data; the operator's selections persist through polls; every freshness stamp carries a date and its true age; after a restart, live updates, actions and the header recover without a reload. 00-overview.source.md:26
[G-23] (Building 3) Use the workflow files under `.claude/workflows/` if any have a dated Confirmed line; otherwise write "no workflow file" with the reason. 00-overview.source.md:27
[G-24] (Building 4) An `/api/state` 401/403/500/503/timeout or an error-shaped body is never drawn as data. 00-overview.source.md:29
[G-25] (Building 4) The last good values stay, with a visible stale/connection cue and retry; a 401 goes to login. 00-overview.source.md:29
[G-29] (Building 4) After a restart, the event stream reconnects and the CSRF token refreshes. 00-overview.source.md:33
[G-30] (Building 4) SAFETY: no POST is ever re-sent automatically after a token refresh. 00-overview.source.md:34
[G-31] (Building 4) That covers Emergency Liquidate, Cash Now, settings saves and approvals. 00-overview.source.md:34
[G-32] (Building 4) The page tells the operator the action was not sent and needs a new click. 00-overview.source.md:34
[G-33] (Building 4) Overlapping requests: the newest issued request wins everywhere, not only `loadState`. 00-overview.source.md:35
[G-34] (Building 4) Two tabs are independent. 00-overview.source.md:35
[G-35] (Building 4) A poll arriving while the settings modal has unsaved edits never changes which symphony the modal saves (TA-38). 00-overview.source.md:36
[G-38] (Building 5) The error body shape is distinguishable from data. 00-overview.source.md:39
[G-48] (Building 7) Plan 04 touches CSRF and token handling, the controls Emergency Liquidate, Cash Now and the settings modal, and the live flag (TA-38). 00-overview.source.md:48
[G-53] (Not building 2) trade, liquidation and order request payloads; 00-overview.source.md:54
[G-54] (Not building 2) `LIVE_EXECUTION`; 00-overview.source.md:55
[G-59] (Not building 3) Items the audit marks COVERED stay with their plans (ux-overhaul 05, C3 round 2, 04 AC-8/AC-9, 03 AC-2). 00-overview.source.md:60
[G-67] 04-pages-and-header. TA-5 (CSRF refresh on every page; never an automatic re-send), TA-20, TA-28, TA-34, TA-35, TA-37, TA-38 (Sensitive). 00-overview.source.md:67

[G-90] (PM amendment) PLAN 04: The Frontrunner on-demand build writes one append-only FRONTRUNNER_BUILD_SUMMARY advisor_observations row per run, following the RETIREMENT_RUN_SUMMARY precedent. This uses the existing table and needs no schema change. Narrow overview NB-3 to allow it. 00-overview.source.md:94
[G-91] (PM amendment) PLAN 04: New scenario: POST /api/fleet-alert/dismiss waits on its write with a bounded timeout and answers 503 on failure. It must never answer ok before the write lands. 00-overview.source.md:95

[G-93] (PM final amendment) "Degraded" header state: Overview SD-13 gains a state, "Degraded", at rank 3. The full order is: Connection Lost > Not updating > Degraded > Closed > Rebalance pause > Opening > Stale > Live. Only `/health`'s `degraded_reasons` changes the label. Add a scenario to plan 03 AC-4 (on `/`) and to plan 04 AC-1 (the four other pages): when `degraded_reasons` is non-empty, the header reads "Degraded", with a tooltip naming the reasons. 00-overview.source.md:99
[G-94] (PM final amendment) Plan 04, new scenario: When `POST /api/settings/flush-resync` fails, its JSON response must carry the exception type name only. Place it under the settings-flush criterion (TA-37 area) and cite the project rule: no exception text in responses. 00-overview.source.md:100

## Requirement Inventory
These are the overview's R ids. Overview items R-83, R-86 and R-87 (the Base, Depends on and overlap notes) and R-88 (the Earlier criteria section) are met by those parts of this plan.

| R-n | Requirement item | Source (G ids) | Maps to (AC ids, or NB-n) | Scope (full, or narrowed: what is left out) |
|---|---|---|---|---|
| R-1 | Every timing issue the refresh-timing audit found is resolved | G-1 | AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-7 | narrowed: this plan's part is the findings G-67 assigns it |
| R-3 | Every datum the poll carries reaches the screen without a reload | G-1, G-14 | AC-1, AC-3, AC-4 | narrowed: the four other pages and the header |
| R-4 | Every number, chart, label and status on every page shows the newest data the engine has produced | G-13 | AC-1, AC-3 | narrowed: the header and `/history` |
| R-6 | Every page recovers by itself after a daemon restart, a laptop sleep, a tab return or a network loss | G-15 | AC-1, AC-2 | narrowed: the header and the POST controls |
| R-7 | The suite covers `/history`, `/performance`, `/ai-advisor`, `/settings` and the shared header | G-16 | AC-1, AC-3, AC-4, AC-5, AC-6 | full |
| R-10 | The operator's selections persist, and Cash Now, Force run and Emergency Liquidate keep working through the day | G-20, G-22 | AC-2, AC-3 | narrowed: the drilled day, and the three controls' behaviour after a restart; their requests, phrase gate and refusal messages stay ux-overhaul 04's |
| R-11 | Tabs kept open across a deploy, a restart or a sleep, and the other pages, stay current | G-21 | AC-1, AC-2 | narrowed: the header and the controls on every page |
| R-13 | Every freshness stamp carries a date and its true age | G-22 | AC-3, AC-4 | narrowed: `/history` and `/performance`; `/ai-advisor`'s stamps are ux-overhaul 02 AC-8's |
| R-14 | After a restart, live updates, actions and the header recover without a reload | G-22, G-29 | AC-1, AC-2 | narrowed: the header and the actions |
| R-15 | Each plan with a screen names its workflow file or states "no workflow file" with the reason | G-23 | AC-1 | full |
| R-16 | An error, refused, unavailable or timed-out poll response is never drawn as data; the last good values stay with a cue and a retry; a 401 goes to login | G-24, G-25 | AC-3, AC-4, AC-5, AC-6 | narrowed: the other pages' polls; `/` is plan 03's |
| R-21 | After a restart every open page's CSRF token refreshes, so its controls work without a reload | G-29, G-67 | AC-2 | full |
| R-22 | No POST is ever re-sent automatically after a token refresh, and the page says the action was not sent and needs a new click | G-30, G-31, G-32 | AC-2 | full |
| R-23 | The newest issued request wins on every page, and two tabs are independent | G-33, G-34 | AC-3, AC-7 | narrowed: `/history` and the settings modal |
| R-24 | Neither a poll nor a late response while the settings modal is open changes which symphony the modal shows or saves | G-35, G-67 | AC-7 | full |
| R-27 | An error body is distinguishable from data | G-38 | AC-3, AC-5 | narrowed: `/history` and `/ai-advisor` |
| R-34 | Plan 04 is marked Sensitive for CSRF and tokens, Emergency Liquidate, Cash Now, the settings modal and the live flag | G-48 | AC-2, AC-7 | full |
| R-39 | Trade, liquidation and order request payloads stay exactly as they are | G-53 | NB-2 | full |
| R-40 | `LIVE_EXECUTION` stays exactly as it is | G-54 | NB-2 | full |
| R-45 | Findings the audit marks covered stay with their plans | G-59 | NB-3 | full |
| R-63 | The LIVE / DRY RUN mode pill follows the live flag without a reload | G-67 | AC-1 | narrowed: the four other pages (overview SD-9); `/` is plan 03 AC-6 |
| R-76 | The header on `/history`, `/performance`, `/ai-advisor` and `/settings` refreshes | G-67 | AC-1 | full |
| R-77 | `/history` keeps a drilled day through the poll, keeps "Today's exits" on the ET date, never draws an error as data and shows its data age | G-67 | AC-3 | full |
| R-78 | `/performance` renders each panel independently, keeps the last good rows on a failed request and shows its data age | G-67 | AC-4 | narrowed: the request count per minute stays (NB-5) |
| R-79 | `/ai-advisor` never shows an error as an empty list and signals a Frontrunner build's completion | G-67, G-90 | AC-5 | narrowed: no new "loaded at" stamps on the server-rendered panels (NB-6) |
| R-80 | `/settings` shows a load failure and its flush confirm reads a small response | G-67 | AC-6 | full |
| R-89 | Open questions are answered by recorded defaults, never by stopping | G-12 | AC-1, AC-2, AC-5 | full |
| R-93 | `POST /api/fleet-alert/dismiss` answers only after its write lands, within a bounded wait, and answers 503 when it fails | G-91 | AC-2 | full |
| R-97 | The header reads "Degraded", with a tooltip that describes each reason in a fixed plain sentence (never the raw key), whenever `/health`'s `degraded_reasons` is non-empty, ranked under "Connection Lost" and "Not updating" | G-93 | AC-1 | full |
| R-98 | A failed `POST /api/settings/flush-resync` answers with the exception type name only, never exception text | G-94 | AC-6 | full |

## User Tasks
Workflow (every page): no workflow file: this repository has no `.claude/workflows/` directory at the ref.

### T-1: Use controls after a deploy
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Keep `/settings` open while the daemon restarts, then click Save | a pending override | The save is accepted with no reload | S-2.1 |
| 2 | On `/`, open Emergency Liquidate after a restart, pick the account and type LIQUIDATE, then Confirm | a stale token | Nothing is sent twice; if the first request is refused, the modal says it was not sent and the next Confirm click sends one request | S-2.3, S-2.4 |

### T-2: Read the other pages through the day
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Keep `/history` open past 16:00 ET | the close | The header turns "Closed" within 60 s | S-1.1 |
| 2 | Click a past day's bar on `/history` | none | The drill-down stays through the next polls | S-3.1 |
| 3 | Open `/performance` with Chart.js blocked | none | The metrics table renders | S-4.1 |

### T-3: Edit a symphony's settings
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Open symphony A's settings, close, open B quickly | A's response lands last | The modal shows B; Save posts B's own values | S-7.1 |

## Earlier criteria on the same surface
Searched every `feature-plans/*.md` (with the `.completed.md` and `.obsolete.md` families and the suite folders; the UX overhaul suite read from branch `ux-overhaul/fixtures` at 340b9d03), `DECISIONS.md` and `docs/generated/` for `csrf`, `_CSRF_TOKEN`, `submitPanicLiquidation`, `cashNow`, `forceRun`, `updateEngineStatus`, `mode-pill`, `renderDayDrilldown`, `loadRecentRuns`, `frRunBuild`, `flush-resync` and `openSymphonySettings`.

| Earlier criterion (plan and AC) | Surface | Disposition (kept, changed or contradicted) | Why |
|---|---|---|---|
| ux-overhaul plan 04 AC-8 (Force run shows the route's answer; S-8.2: a 403 shows "Error") | Force run | changed | The answer stays; a 403 now also refreshes the token and the button says the run was not requested (S-2.6) |
| ux-overhaul plan 04 AC-9 (Emergency Liquidate shows the server's real answer; S-9.6 a 403 says the session check refused it and nothing was liquidated; S-9.8 request byte-identical; S-9.10 a re-confirm sends one new request) | Emergency Liquidate | changed | Every message and the request stay; a 403 now refreshes the token so the next Confirm can pass, and nothing is ever re-sent without that click (S-2.3, S-2.4) |
| ux-overhaul plan 04 AC-4 S-4.6 (the mode pill's server render as at the ref) and NB-5 (the pill's source `_build_meta`) | Mode pill | changed | The pill keeps its source and look and now refreshes on the four other pages (S-1.4) |
| ux-overhaul plan 04 AC-1 (every page header conforms to the dashboard's) and NB-3 (the workspace switcher filters only `/`) | Header | kept | AC-1 refreshes the header's values, not its layout |
| ux-overhaul plan 05 AC-7 S-7.5 (a Dismiss refused for a missing token keeps the banner with a notice) | Fleet banner Dismiss | changed | A stale token is refreshed and the notice says to click again (S-2.8) |
| The fleet-dismiss route's design (`app.py:4915-4928`: the write is submitted to `_DISMISS_EXECUTOR` and the route answers ok at once) and ux-overhaul plan 05 PM-5 (the first dismissal time is kept) | Fleet banner Dismiss | changed | PM-5 is kept; the route now waits a bounded time for its write and answers 503 on failure, never ok first (S-2.21, S-2.22) |
| ux-overhaul plan 01 AC-6 (the History drill-down names each symphony in time order; S-6.6 `todays_exits` as at the ref; S-6.7 another click replaces the day) | History drill-down | changed | S-6.7 is kept; the poll no longer erases a drilled day (S-3.1), and after 20:00 EDT `todays_exits` follows the ET date, not the host's (S-3.3) |
| ux-overhaul plan 01 AC-8 (Settings controls; S-8.6 a refused save shows the failure and keeps the pending reset; S-8.10 a failed load uses the existing handling) | `/settings` | changed | S-8.6 is kept and gains the token refresh and the not-sent text (S-2.1, S-2.2); S-8.10's silent failure becomes a visible load error (S-6.1) |
| ux-overhaul plan 02 S-2.5 and S-3.5 (the 15 s poll keeps the card count, the expander and one chart), S-2.7 (a failed runs request shows the existing failure text) and S-4.17 (Run now's timeout text) | `/ai-advisor` | kept | AC-5 adds the error-body case to S-2.7's rule |
| ux-overhaul plan 02 AC-8 (Advisor timestamps name their zone) | `/ai-advisor` stamps | kept | NB-6 |
| `feature-plans/fix-ops-cluster.md` AC-3 (F-020: an exit's detail inspectable from the loaded History payload) | History drill-down | kept | AC-3 keeps the payload-only drill-down |
| `feature-plans/frontrunner-builder.md` (async 202 dispatch; the UI says reload afterwards; DE-FRONTRUNNER-002) | Frontrunner Run build | changed | The page shows the build's end itself (S-5.3) |
| `feature-plans/dashboard-auth.completed.md` and DE-AUTH-001 (one process-lifetime CSRF token; the login form posts it) | CSRF | kept | The token stays per process; pages refresh their copy (AC-2) |
| `feature-plans/m5-chat-hardening.completed.md` (the chat's CSRF fix) and `feature-plans/candidate-alert.md` (mark-viewed is CSRF-protected) | Chat, header badge | kept | Their POSTs gain the token refresh |
| `feature-plans/guard-alpha-saved-coherence.md` AC-6 (History 1Y and 5Y send 365 and 1825) | History window | kept | AC-3 changes no window value |
| model-unpin plan (branch `model-unpin/fixtures`) AC-6 (the Advisor badges name the resolved model) and its C9 badge re-listing | `/ai-advisor` | kept | AC-5 changes no badge |

## Acceptance Criteria (Building)
### AC-1: The header on the four other pages follows the engine, the countdown and the live flag
Requirement items: R-1, R-3, R-4, R-6, R-7, R-11, R-14, R-15, R-63, R-76, R-89, R-97
Rule: While `/history`, `/performance`, `/ai-advisor` or `/settings` is open, the header shall refresh its engine status, NEXT countdown, workspace label and mode pill at least every 30 s, by the same rules `/` uses (plan 02 AC-6 included), from a status read that answers in under 1 s with under 10 KB, with "Not updating" judged by the age of that read's last success and every label in overview SD-13's precedence.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-1.1 | B | `/history` open at 15:59:30 ET on a trading day | 16:00 passes | the header reads "Closed" within 60 s |
| S-1.2 | B | `/performance` open during market hours | the seconds pass | the NEXT countdown decreases each second, equals the seconds to the next `:00` within 1 s and never reads "00:00" for more than 2 s |
| S-1.3 | B | `/ai-advisor` open at 09:25 ET | 09:30 passes and the first cycle completes | the header turns "Opening", then "Live" |
| S-1.4 | B | two tabs, `/settings` and `/history`, showing DRY RUN | `LIVE_EXECUTION` becomes true | within one refresh the `/history` tab's mode pill reads LIVE |
| S-1.5 | N6 | `/history` open and the engine's last cycle 3 minutes old at 11:00 | the header refreshes | it reads "Stale" with its tooltip |
| S-1.6 | N6 | the status read fails | the header refreshes | it reads "Connection Lost" and keeps the countdown hidden |
| S-1.7 | N4 | the session lapsed | the status read answers 401 | the page navigates to `/login` |
| S-1.8 | N7 | the header's layout | the page renders | it is ux-overhaul 04's; only the values refresh |
| S-1.9 | N5 | the status read is slow and two are in flight | both land, older last | the header shows the newer |
| S-1.10 | B | 15:56 ET | `/settings` refreshes its header | it reads "Rebalance pause", never "Stale" (plan 02 AC-6) |
| S-1.11 | N6 | `/history` open, its status read carrying `degraded_reasons` ["engine_overrun"] | the header refreshes | it reads "Degraded" and its tooltip reads "An engine run has lasted over 3 minutes; minutes are being skipped." (the suite approach RC-1's fixed sentence, overview SD-13), never the raw key |

Negative coverage: N1 n/a: the header takes no input · N2 n/a: the read refuses nothing but a lapsed session (S-1.7) · N3 n/a: every field has a value or the connection-lost state · N4 S-1.7 · N5 S-1.9 · N6 S-1.5, S-1.6, S-1.11 · N7 S-1.8 · N8 n/a: no edit state
Questions: Q-1, Q-4

### AC-2: Every POST control refreshes its token after a restart, never re-sends, and says when an action was not sent
Requirement items: R-1, R-6, R-10, R-11, R-14, R-21, R-22, R-34, R-89, R-93
Rule: When a page's POST is refused for its CSRF token (403), or a poll shows the daemon has restarted (`daemon_started_at` changed), the page shall fetch a new token and use it for the next click, and shall never re-send any POST without a new operator click; a refused action shall say that it was not sent and needs a new click, and any other refusal shall show the server's own answer; and `POST /api/fleet-alert/dismiss` shall answer only after its write has landed, waiting a bounded time, and answer 503 when the write fails or the wait runs out.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-2.1 | B | `/settings` loaded before a daemon restart, with a pending override | after the restart the operator clicks Save | `POST /api/settings` is accepted (200) with no reload, carrying a token minted by the new process |
| S-2.2 | N2 | `/settings` holds a stale token and has not seen the restart | the operator clicks Save and the POST is refused 403 | no second POST is sent; the page reads "Not saved: the session check refused it after a restart. Click Save again."; the pending edits stay; the next Save click sends one POST with the new token |
| S-2.3 | N2 | `/` loaded before a restart; Emergency Liquidate confirmed with an account and LIQUIDATE | the POST is refused 403 | exactly one POST was sent; the alert says the session check refused the request, nothing was liquidated and Confirm must be clicked again; no POST follows until the next Confirm click |
| S-2.4 | B | after S-2.3 | the operator reopens Emergency Liquidate and confirms again | exactly one `POST /api/sell_account` is sent with a valid token and a body byte-identical to the ref's for the same inputs |
| S-2.5 | N2 | a Cash Now click on a card | `POST /api/sell_account` answers 400 "confirm_account_id is required" | the card shows "Not sold: confirm_account_id is required" and the button re-enables; no POST is re-sent |
| S-2.6 | N2 | a stale token | the operator clicks Force run now and it is refused 403 | the button reads "Not sent: click again" for a few seconds; no POST is re-sent |
| S-2.7 | N2 | a stale token on `/ai-advisor` | an approve, reject or run click is refused 403 | that card or form says the action was not sent and needs a new click; no POST is re-sent |
| S-2.8 | N2 | a stale token on `/` with the fleet banner shown | Dismiss is refused 403 | the banner stays with "Not dismissed: click Dismiss again"; no POST is re-sent |
| S-2.9 | N2 | a login form rendered before a restart | the operator submits it | the login page renders again with a fresh token and the line "This form expired after a restart; sign in again", never Werkzeug's raw 403 page; nothing is submitted for the operator |
| S-2.10 | N5 | two tabs loaded before a restart | one tab's POST is refused and refreshes its token | the other tab's state and token are untouched until its own refusal or poll |
| S-2.11 | N7 | every POST control | its request is sent | its URL, method, headers other than the token value, and body equal the ref's for the same inputs |
| S-2.12 | N6 | the token fetch itself fails | a refused click | the page says the action was not sent and that the connection failed; no POST is re-sent |
| S-2.13 | N8 | a settings save is in flight when the poll shows a restart | the refresh fetches a new token | the in-flight save is not repeated; its own answer is shown |
| S-2.14 | B | a poll on any page shows `daemon_started_at` changed | the poll lands | the page fetches a new token before the operator's next click |
| S-2.15 | N2 | a stale token on `/` | the operator's Managed Sleeves arm-to-paper, disarm or delete click is refused 403 (`static/index.js:1679`, `:1703`, `:1721`) | the panel says the action was not sent and needs a new click; the page does not reload and no POST is re-sent |
| S-2.16 | N2 | a stale token on `/settings` | the flush-resync Confirm is refused 403 (`static/settings.js:297`) | the confirm panel says the flush was not sent and needs a new click; no POST is re-sent |
| S-2.17 | N2 | a stale token on any page | the candidate-alert mark-viewed POST is refused 403 (`static/chrome.js:341-342`) | the badge keeps its count; the next open of the alert sends one POST with the new token; no POST is re-sent |
| S-2.18 | N2 | a stale token on `/ai-advisor` | a chat send is refused 403 (`static/ai_advisor_chat.js:173`) | the chat shows the message as not sent with a Send-again button and keeps the typed text; no POST is re-sent |
| S-2.19 | N2 | a stale token, the settings modal open with `live_mode` turned on and the go-live confirmation accepted | Save is refused 403 (`static/settings-modal.js:501`) | the modal says the save was not sent and needs a new click, keeps the edits, and the next Save asks the go-live confirmation again; no POST is re-sent |
| S-2.20 | N2 | a stale token on `/ai-advisor` | an Accept of a suggestion (`POST /ai-advisor/accept`, which writes strategy parameters) is refused 403 | the card says the accept was not sent and needs a new click; no POST is re-sent |
| S-2.21 | B | an active fleet alert | the operator clicks Dismiss and the write lands | the route answers 200 only after the dismissed time is stored |
| S-2.22 | N6 | the dismiss write fails or exceeds the bounded wait | the operator clicks Dismiss | the route answers 503, never ok; the banner stays with its not-dismissed notice (ux-overhaul 05 S-7.5's notice) |

Negative coverage: N1 n/a: no control's input changes · N2 S-2.2, S-2.3, S-2.5, S-2.6, S-2.7, S-2.8, S-2.9, S-2.15, S-2.16, S-2.17, S-2.18, S-2.19, S-2.20 · N3 n/a: a missing token is the stale-token case · N4 S-2.9 · N5 S-2.10 · N6 S-2.12, S-2.22 · N7 S-2.11 · N8 S-2.13
Questions: Q-2

### AC-3: `/history` keeps a drilled day, uses the ET date and shows its data age
Requirement items: R-1, R-3, R-4, R-7, R-10, R-13, R-16, R-23, R-27, R-77
Rule: While a day is drilled into on `/history`, each poll shall leave the table and heading on that day; "Today's exits" shall follow the ET trading date; a response that is not data shall leave the page as it was with a cue (a 401 goes to `/login`); and the page shall show the date and time of its data.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-3.1 | B | the operator clicked a past day's bar | the 30 s poll lands | the table and heading still show that day |
| S-3.2 | B | a day is drilled | the operator clicks "Today's exits" or picks a window | the table returns to today's exits |
| S-3.3 | N1 | 20:30 EDT on the UTC host | `/api/history/{days}` is served | "Today's exits" and their Detail values equal those served at 19:30 EDT |
| S-3.4 | N4 | the session lapsed | a poll answers 401 | the page navigates to `/login` |
| S-3.5 | N6 | a poll answers 500 or an HTML body | it lands | the hero, bars, reason cards and table keep their values, and the page shows a connection-lost cue with its last data stamp |
| S-3.6 | B | any load | the page renders | it shows "data as of" with the date and time of the newest post-mortem or exit it shows (overview SD-1) |
| S-3.7 | N5 | a window click while a poll is in flight | both land, the poll last | the page shows the clicked window |
| S-3.8 | N7 | the drill-down's content | a day is drilled | it lists exits as ux-overhaul 01 AC-6 does |

Negative coverage: N1 S-3.3 · N2 n/a: the route refuses only non-integer days, which the page never sends · N3 n/a: an empty window is the existing honest empty state · N4 S-3.4 · N5 S-3.7 · N6 S-3.5 · N7 S-3.8 · N8 n/a: a drilled day is a view, not an edit
Questions: none

### AC-4: `/performance` renders each panel independently and shows its data age
Requirement items: R-1, R-3, R-7, R-13, R-16, R-78
Rule: When `/performance` refreshes, a failure in one panel's rendering or request shall leave every other panel and that panel's last good rows in place, a 401 shall go to `/login`, and the page shall show the date and time of its data.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-4.1 | N6 | `Chart` is undefined because the script failed to load | the page refreshes | the metrics table, headline stats and banner render |
| S-4.2 | N6 | one symphony's `/api/exit-turnover` request fails | the panel refreshes | the other rows render and that row keeps its last values |
| S-4.3 | N6 | every exit-turnover request fails | the panel refreshes | the panel keeps its last rows with a connection-lost cue |
| S-4.4 | N4 | the session lapsed | a refresh answers 401 | the page navigates to `/login` |
| S-4.5 | B | any load | the page renders | it shows "data as of" with the date and time of the newest shadow observation the response used (overview SD-1) |
| S-4.6 | N7 | the 60 s refresh | it runs | the same requests are sent as at the ref |

Negative coverage: N1 n/a: the page's inputs are the window and symphony pickers, unchanged · N2 n/a: the routes refuse only invalid windows, which the page never sends · N3 n/a: an empty window is the existing honest empty state · N4 S-4.4 · N5 n/a: one refresh at a time per 60 s tick, as at the ref · N6 S-4.1, S-4.2, S-4.3 · N7 S-4.6 · N8 n/a: no edit state
Questions: none

### AC-5: `/ai-advisor` never shows an error as an empty list, and signals a Frontrunner build's end
Requirement items: R-1, R-7, R-16, R-27, R-79, R-89
Rule: If `/api/autotune-runs` answers anything but a list, then the runs list shall keep its cards with a cue; each Frontrunner on-demand build shall write one append-only `FRONTRUNNER_BUILD_SUMMARY` row in `advisor_observations` (its outcome: completed with a proposal count, or failed with an error code); and when a build is started, the Run build button shall stay disabled until the page shows that build's outcome or 30 minutes pass.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-5.1 | N6 | the runs list shows N cards | `/api/autotune-runs` answers a JSON error object | the list keeps its N cards and shows "Could not refresh runs" |
| S-5.2 | N4 | the session lapsed | the 15 s poll answers 401 | the page navigates to `/login` |
| S-5.3 | B | a build was started (202) | it completes and writes its `FRONTRUNNER_BUILD_SUMMARY` row with N proposals | within 60 s the tab shows "Build finished: N new proposals" and re-enables Run build, with no reload |
| S-5.4 | N6 | the build ends with an error code | its summary row records the failure | the tab shows the failure with its code and re-enables Run build |
| S-5.5 | N5 | a build is running | the operator clicks Run build again | the button is disabled, so no second build starts from this tab |
| S-5.6 | N3 | no outcome after 30 minutes | 30 minutes pass | the button re-enables and the tab reads "Build still running or lost; reload to check" |
| S-5.7 | N7 | the runs list with a healthy response | the poll lands | it renders as ux-overhaul 02 AC-2 delivers it |
| S-5.8 | N3 | a build completes with no proposals | its summary row records 0 | the tab shows "Build finished: 0 new proposals": a completed build, not a failure |
| S-5.9 | N6 | the daemon restarts during a build, so no summary row is written | 30 minutes pass | the tab shows S-5.6's text and re-enables Run build |
| S-5.10 | N7 | the summary row | it is written | it goes to the existing `advisor_observations` table, append-only, with no schema change, as `RETIREMENT_RUN_SUMMARY` does |

Negative coverage: N1 n/a: no input · N2 n/a: the build route's refusals (missing key) show as at the ref · N3 S-5.6, S-5.8 · N4 S-5.2 · N5 S-5.5 · N6 S-5.1, S-5.4, S-5.9 · N7 S-5.7, S-5.10 · N8 n/a: a running build is S-5.5
Questions: Q-3

### AC-6: `/settings` shows a load failure, and its flush confirm reads a small response
Requirement items: R-1, R-7, R-16, R-80, R-98
Rule: If `/settings` cannot load its settings after its retry, then it shall show a visible load error with a retry button; and when the flush confirm opens, it shall read the armed symphonies from a response under 100 KB; and a failed flush-resync shall answer with the exception type name only, never exception text (the project rule that no exception text reaches a response).

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-6.1 | N6 | `/api/settings` fails twice | the page loads | a visible error reads "Settings could not be loaded" with a Retry button, and no control is bound to empty data |
| S-6.2 | B | as S-6.1 | the operator clicks Retry and the load succeeds | the page renders as at the ref |
| S-6.3 | B | live-mode symphonies are armed | the operator opens the flush confirm | the request it sends returns under 100 KB and the panel lists the LIVE state's armed symphonies, the ones the flush resets, each labelled as live |
| S-6.4 | N4 | the session lapsed | the load answers 401 | the page navigates to `/login` |
| S-6.5 | N7 | the flush itself | the operator confirms | `POST /api/settings/flush-resync` is sent as at the ref |
| S-6.6 | N8 | edits are pending | a background reload of settings would run | the pending edits stay (no reload discards typed values) |
| S-6.7 | N6 | the flush-resync's Composer resync raises a `ReadTimeout` whose message carries the URL | `POST /api/settings/flush-resync` answers | its `errors` list holds "resync: ReadTimeout", never the exception text (at the ref `app.py:6408` puts `f"resync: {exc}"` into `errors`, which `app.py:6430` returns) |

Negative coverage: N1 n/a: no new input · N2 n/a: nothing new is refused · N3 n/a: no armed symphonies is the existing empty list · N4 S-6.4 · N5 n/a: one load at a time · N6 S-6.1, S-6.7 · N7 S-6.5 · N8 S-6.6
Questions: none

### AC-7: The settings modal never saves one symphony with another's data
Requirement items: R-1, R-23, R-24, R-34
Rule: If a response for a symphony other than the one the settings modal is open on lands, then the modal shall discard it, and Save shall post only the open symphony's own loaded values and the operator's edits, with the go-live confirmation whenever the live flag turns on.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-7.1 | N5 | the modal is opened for A and then for B within the response time | A's response lands last | the modal shows B's settings, and Save posts B's own values |
| S-7.2 | N8 | the modal for B has unsaved edits | a dashboard poll lands | the modal's symphony, values and edits are unchanged |
| S-7.3 | B | B is in dry run | the operator turns B live and saves | the go-live confirmation shows before the POST |
| S-7.4 | N7 | any save | it is sent | `POST /api/symphony-settings/{symphony_name}` carries the same fields as at the ref |
| S-7.5 | N6 | B's load fails | the modal renders | "Failed to load settings. Please try again." and Save is not offered |
| S-7.6 | N5 | the modal is closed while a response is in flight | the response lands | nothing renders and nothing is saved |

Negative coverage: N1 n/a: the modal's inputs are unchanged · N2 n/a: the route's refusals show as at the ref · N3 n/a: a missing setting is the route's default, unchanged · N4 n/a: the route's auth and CSRF hooks are unchanged; a stale token is AC-2 · N5 S-7.1, S-7.6 · N6 S-7.5 · N7 S-7.4 · N8 S-7.2
Questions: none

## Rule-by-Entry-Point Matrix
| Rule | `/settings` Save | Per-symphony settings Save | Emergency Liquidate | Cash Now | Force run now | `/ai-advisor` approve, reject and run | Fleet Dismiss | Login form |
|---|---|---|---|---|---|---|---|---|
| AC-2 refresh the token, never re-send, say not sent | S-2.1, S-2.2 | S-2.13, S-2.19 | S-2.3, S-2.4 | S-2.5 | S-2.6 | S-2.7 | S-2.8 | S-2.9 |
| AC-7 save only the open symphony | n/a: the page edits globals and per-symphony rows by key, with no modal | S-7.1, S-7.2 | n/a: not a settings write | n/a: not a settings write | n/a: not a settings write | n/a: not a settings write | n/a: not a settings write | n/a: not a settings write |

| Rule | Managed Sleeves arm, disarm and delete | Flush-resync Confirm | Candidate-alert mark-viewed | Chat send | `POST /ai-advisor/accept` |
|---|---|---|---|---|---|
| AC-2 refresh the token, never re-send, say not sent | S-2.15 | S-2.16 | S-2.17 | S-2.18 | S-2.20 |
| AC-7 save only the open symphony | n/a: not a settings write | n/a: resets the global flush state, no modal | n/a: not a settings write | n/a: not a settings write | n/a: writes the suggestion's strategy row by its own symphony key, with no modal |

| Rule | `/history` | `/performance` | `/ai-advisor` | `/settings` |
|---|---|---|---|---|
| AC-1 header follows the engine | S-1.1 | S-1.2 | S-1.3 | S-1.10 |
| An error body is never drawn as data; a 401 goes to login | S-3.4, S-3.5 | S-4.3, S-4.4 | S-5.1, S-5.2 | S-6.1, S-6.4 |

## Not Building
Non-goals:
- NB-1: Rotating the CSRF token per session or per request; it stays one per process (DE-AUTH-001) and pages refresh their copy (approved by the PM, [PM-ASSUMED]).
- NB-4: Fixing Cash Now's request so it can pass the route's confirmation checks; plan 03 NB-6 records it as a sensitive follow-up (approved by the PM, [PM-ASSUMED]).
- NB-5: Reducing `/performance`'s 15 requests a minute (narrows R-78; approved by the PM, [PM-ASSUMED]).
- NB-6: "Loaded at" stamps on `/ai-advisor`'s server-rendered panels; their own run times are ux-overhaul 02 AC-8's (narrows R-79; approved by the PM, [PM-ASSUMED]).

Behaviour that stays exactly as it is:
- NB-2: Every POST's URL, method, body and confirmation steps; `LIVE_EXECUTION` and the settings allowlist (overview NB-6, NB-7).

Deferred:
- NB-3: The covered findings stay with their plans: Force run's answer is ux-overhaul 04 AC-8 (TA-51) (overview NB-12).

Protected surfaces consumed, not changed:
- NB-7: `POST /api/sell_account`, `POST /ai-advisor/proposal/approve`, the retirement routes and the no-trade boundary tests (overview NB-14).

## Defect Classes
| DC-n | Class (what goes wrong) | Trigger (B or N1-N8) | Sibling search (tool, query, scope) | Scenarios | Raised in review as |
|---|---|---|---|---|---|
| DC-1 | A POST that can be sent twice or re-sent without a click (overview DC-7) | N5 | grep `method: 'POST'`, `method:'POST'` and `method: "POST"` in `static/` and `templates/`; each must have no retry path after a token refresh | S-2.2, S-2.3, S-2.6, S-2.13 | - |
| DC-2 | A script that reads the CSRF token once and never again | N2 | grep `/api/csrf-token` in `static/` and `templates/` (eight scripts at the ref) | S-2.1, S-2.14 | - |
| DC-3 | A refusal shown as a generic error or not at all | N2 | grep `.catch(` and `r.ok` beside each POST in `static/` | S-2.5, S-2.7 | - |
| DC-4 | A response drawn without a status or shape check (overview DC-4) | N6 | grep `return resp.json()` and `return r.json()` in `static/history.js`, `static/performance.js`, `static/ai_advisor.js` and `static/settings.js` | S-3.5, S-5.1 | - |
| DC-5 | A modal applying a response for a target it no longer shows | N5 | grep `fetch(` in `static/settings-modal.js` and every modal opener in `static/` | S-7.1, S-7.6 | - |
| DC-6 | A header value fixed at render on a page with no refresh (overview DC-6) | B | read: each value in `templates/_chrome.html` against the writers in `static/chrome.js`, on every page | S-1.1, S-1.4 | - |
| DC-8 | A new read path that can reach an order or symphony-creation path | N7 | extend the transitive call-graph walk of `tests/security/test_retirement_action_no_trade_boundary.py` (its Group E pattern) to the AC-1 status read, the AC-5 build-outcome read and the AC-6 armed-symphony read, checking for `sell_account`, the sleeves order functions, `composer_draft_client`, `save_symphony` and `LIVE_EXECUTION` writes; prove it non-vacuous by injecting a call to one of them into each read and seeing the scan fail | S-1.1, S-5.3, S-6.3 | - |
| DC-7 | A host-local date where the ET trading date is meant (overview DC-3) | N1 | grep `_date.today()` and `date.today()` in `analytics.py` and the `/api/history` route in `app.py` | S-3.3 | - |

## Worked Example
The daemon restarts at 11:02 ET while `/settings` is open with a pending override. At 11:02:30 the header's status read shows a new `daemon_started_at`, so the page fetches a new token; at 11:03 the operator clicks Save and `POST /api/settings` is accepted (200) with no reload (S-2.14, S-2.1). On `/`, which had not polled yet, Emergency Liquidate is confirmed with an account and LIQUIDATE: the one POST is refused 403, the alert says the session check refused it and nothing was liquidated, and no request follows until the operator confirms again, which sends exactly one POST with the new token (S-2.3, S-2.4).

Failure case: the modal for "Copper Ridge Rotation" is opened, closed, and "Granite Bay Momentum" is opened within 80 ms; the first response lands last. The modal shows "Granite Bay Momentum" and its own values, and Save posts them to it alone (S-7.1).

## Fail-Closed Behaviour
- A POST refused for its token (403) is never repeated by the page; the operator's next click sends one new request with the refreshed token (S-2.2 to S-2.8, S-2.15 to S-2.19). This is enforced at each control listed in the matrix.
- A login form submitted with a token from an earlier process is refused as at the ref; the page then shows a fresh form instead of the raw 403 (S-2.9). The refusal itself is the existing CSRF check (`app.py:373-379`).
- `POST /api/sell_account`'s existing 400 refusals (`app.py:5455-5472`) are shown with their messages for Cash Now (S-2.5), as ux-overhaul 04 AC-9 already does for Emergency Liquidate.
- A settings-modal response for another symphony is discarded (S-7.1).

## Contract Changes and Consumers
- `/api/history/{days}` and `/api/performance` each add a `data_as_of` field: the ET date and time of the newest post-mortem entry or exit (history) and of the newest shadow observation (performance) the response uses, in overview SD-1's form, or `--`. At the ref both return dates only (`app.py:5063`). Additive; consumers `static/history.js` and `static/performance.js` (S-3.6, S-4.5).
- The header's status read: the four other pages read the engine status, countdown, workspace label and mode pill at least every 30 s from a read under 10 KB (Q-1 picks it). Additive.
- The flush confirm's armed-symphony read returns under 100 KB (S-6.3). Additive.
- Each Frontrunner on-demand build writes one append-only `FRONTRUNNER_BUILD_SUMMARY` row in the existing `advisor_observations` table, and the page reads the newest one (S-5.3). Additive; no schema change (overview NB-3, narrowed).
- `POST /api/fleet-alert/dismiss` answers after its write lands, or 503 (S-2.21, S-2.22). The success body is unchanged.
- The login page re-renders with a fresh token on a CSRF refusal of its form (S-2.9). The route's refusal status is unchanged.

## Security Considerations
- Input validation and injection: no new input; the not-sent texts are fixed strings; server refusal messages render through `textContent`.
- Authorization and access control: the CSRF check, the auth gate, the login throttle and `_AUTH_EXEMPT_ENDPOINTS` are unchanged; a page only re-reads the token the same session could already read from `GET /api/csrf-token` (security, AC-2).
- Data exposure: the header's status read carries what `_build_meta` already renders; nothing new leaves the server.
- Client-side rendering: no `innerHTML` with response data is added.
- Abuse or rate limits: a token is re-fetched only after a 403 or a detected restart (S-2.14); a refused action is never retried by the page, so a refusal cannot loop (DC-1).
- Money (Sensitive): Emergency Liquidate and Cash Now send exactly the operator's clicks, never a page-made repeat (S-2.3, S-2.4, S-2.5, S-2.11); the settings modal can no longer apply one symphony's live flag to another or skip the go-live confirmation (S-7.1, S-7.3).

## Design-System Mapping
| Component | Element | Required primitive or token | Notes |
|---|---|---|---|
| Header | engine status, countdown, mode pill | the existing ux-overhaul 04 header primitives | values only |
| Every POST control | not-sent text | each control's existing message area (alert, button text, card notice) | text only |
| `/history`, `/performance` | data as-of stamp | the existing `.legend-as-of` | dated (overview SD-1) |
| `/settings` | load error and Retry | the existing page alert and button styles | |
| Login | expired-form line | the existing login error text style | |

## Definition of Done (Verification)
| AC | Scenarios | Test level | Real-entrypoint test |
|---|---|---|---|
| AC-1 | S-1.1, S-1.2, S-1.3, S-1.4, S-1.5, S-1.6, S-1.7, S-1.8, S-1.9, S-1.10, S-1.11 | `static/chrome.js` under node with a fake clock; the status read with the Flask test client | each page's header on the droplet at 15:59 to 16:01 and 09:29 to 09:31 |
| AC-2 | S-2.1, S-2.2, S-2.3, S-2.4, S-2.5, S-2.6, S-2.7, S-2.8, S-2.9, S-2.10, S-2.11, S-2.12, S-2.13, S-2.14, S-2.15, S-2.16, S-2.17, S-2.18, S-2.19, S-2.20, S-2.21, S-2.22 | each control under node with a request recorder that fails if a POST is sent without a click; the CSRF hook and the login re-render with the Flask test client, the token rotated between page load and click, each such test setting `app._csrf_check_enabled` (and, for S-2.9, `app._auth_check_enabled`) back to True, because `tests/conftest.py:280` and `:297` turn both off for every test | the PM restarts the droplet daemon with every page open and clicks Save on `/settings`; Emergency Liquidate is provoked with a blank token on a dry-run instance, as ux-overhaul 04 PM-4 does |
| AC-3 | S-3.1, S-3.2, S-3.3, S-3.4, S-3.5, S-3.6, S-3.7, S-3.8 | `static/history.js` under node; `/api/history` with a frozen UTC clock | `/history` on the droplet at 20:30 EDT |
| AC-4 | S-4.1, S-4.2, S-4.3, S-4.4, S-4.5, S-4.6 | `static/performance.js` under node with `Chart` undefined and failing requests | `/performance` on the droplet with the CDN blocked in DevTools |
| AC-5 | S-5.1, S-5.2, S-5.3, S-5.4, S-5.5, S-5.6, S-5.7, S-5.8, S-5.9, S-5.10 | `static/ai_advisor.js` under node with captured route bodies | one Frontrunner build run on the droplet (the PM's call, with its LLM spend) |
| AC-6 | S-6.1, S-6.2, S-6.3, S-6.4, S-6.5, S-6.6, S-6.7 | `static/settings.js` under node; the armed read with the Flask test client | `/settings` on the droplet |
| AC-7 | S-7.1, S-7.2, S-7.3, S-7.4, S-7.5, S-7.6 | `static/settings-modal.js` under node with reordered responses | the modal on the droplet, opened on two symphonies in quick succession |

Gates: `ruff format --check`; `ruff check`; a scoped pytest run of `tests/app`, `tests/dashboard`, `tests/security`, `tests/ui`, `tests/ai_advisor` and `tests/js_syntax`, at most 4 workers, never `-n auto`; the NB-7 boundary tests; DC-8's extended no-trade scan, mutation-tested once; CI green on the exact SHA; `/review` with the integration auditor, and with the security auditor on the AC-2 and AC-7 cycles; then the PM's live functional check on the droplet.
Fixture provenance (SD-7): route bodies (`/api/history`, `/api/performance`, `/api/exit-turnover`, `/api/autotune-runs`, `/api/symphony-settings/{symphony_name}`) are captured from the production state database copy the audit used, one per case; refusal bodies are the routes' own literal responses at the cited lines; the 403 HTML is Werkzeug's real page captured from the test client.

## Known-Unconfirmed and Standing Risk
- How often a session lapses in production (a 401) is unmeasured; the redirect is a guard, not a frequent path.
- Whether a Frontrunner build's outcome is recorded on every failure path is read from the code, not run; S-5.6's 30-minute bound covers a lost outcome.
- ux-overhaul 04 rewrites the header markup; this plan's header ids are those ux-overhaul 04 leaves, checked when this plan's base is cut.

## Open Questions
| Q-n | Question | Blocks (AC ids or "plan") | Proposed default | Answer |
|---|---|---|---|---|
| Q-1 | What does the header on the four other pages read its values from? | AC-1 | A light status read (`/health`'s liveness fields from plan 02 plus the market state, engine phase, next-run seconds, mode and workspace label), polled every 30 s and on tab return; the route is a Gate-2 choice | default approved by the PM, [PM-ASSUMED] |
| Q-2 | When does a page fetch a new CSRF token? | AC-2 | After any 403 on a POST and whenever a poll shows `daemon_started_at` changed; never on a timer; never by re-sending | default approved by the PM, [PM-ASSUMED] |
| Q-3 | How does `/ai-advisor` learn a Frontrunner build finished? | AC-5 | Each on-demand build writes one append-only `FRONTRUNNER_BUILD_SUMMARY` row in `advisor_observations`, as `RETIREMENT_RUN_SUMMARY` does, and the tab polls for it every 15 s for at most 30 minutes | approved by the PM with the summary row (PM amendment), [PM-ASSUMED] |
| Q-4 | No `.claude/workflows/` file exists for `/history`, `/performance`, `/ai-advisor`, `/settings` or the shared header. Accept "no workflow file" and the User Tasks written here? | AC-1 | Yes, until the operator confirms a workflow file | default approved by the PM, [PM-ASSUMED] |

## Decisions
| ID | Decision | Rationale |
|---|---|---|
| DEC-1 | The not-sent texts name the action and say "click again"; they never say "retrying" | G-32; the page never retries (G-30) |
| DEC-2 | Cash Now's existing 400 refusals are shown with the server's message | The same honesty ux-overhaul 04 AC-9 gives Emergency Liquidate; the request stays byte-identical (NB-2) |
| PM-1 | The Frontrunner on-demand build writes one append-only `FRONTRUNNER_BUILD_SUMMARY` row per run in `advisor_observations`; a failed build ended with an error code; no proposals is a completed build; a build lost to a restart shows S-5.6's text after 30 minutes | PM amendment from the Gate-2 approach, [PM-ASSUMED] |
| PM-2 | The flush confirm lists the LIVE state's armed symphonies, labelled live (S-6.3) | PM amendment, [PM-ASSUMED] |
| PM-3 | `POST /ai-advisor/accept` joins the AC-2 matrix (S-2.20) | PM amendment, [PM-ASSUMED] |
| PM-4 | `POST /api/fleet-alert/dismiss` waits on its write with a bounded timeout and answers 503 on failure (S-2.21, S-2.22) | PM amendment, [PM-ASSUMED] |
| PM-5 | Reviewers: the integration auditor, and the security auditor on the AC-2 and AC-7 cycles | PM amendment, [PM-ASSUMED] |
| PM-6 | The header on the four other pages reads "Degraded" when its status read's `degraded_reasons` is non-empty (S-1.11), at rank 3 of overview SD-13 | PM amendment from `feature-plans/refresh-timing/approach/00-suite.md`, [PM-ASSUMED] |
| PM-7 | The header poller calls `updateModeIndicators(meta)` in `static/chrome.js` for the mode pill, the same writer `/` uses | PM amendment from `feature-plans/refresh-timing/approach/00-suite.md`, [PM-ASSUMED] |
| PM-8 | A failed flush-resync answers with the exception type name only (S-6.7) | PM ruling from plan 05's approach review, [PM-ASSUMED] |
| PM-9 | The "Degraded" tooltip gives RC-1's fixed plain sentence for each reason and never shows the raw key (S-1.11) | PM final ruling, `feature-plans/refresh-timing/approach/00-suite.md` RC-1, [PM-ASSUMED] |

## Docs to Update
- `docs/generated/static_chrome_js.md` if present, `docs/generated/static_history_js.md`, `docs/generated/app.md` (the login re-render, the status read, the armed read), `docs/generated/INDEX.md`.
- A `DECISIONS.md` entry appended at ship by the doc writer.
- A drafted `.claude/CLAUDE.md` Key Files row update for `static/chrome.js`, `static/history.js`, `static/settings.js` and `static/settings-modal.js`, for PM approval.

## Follow-Ups
- NB-4: Cash Now's request (plan 03 NB-6).
- NB-5: `/performance`'s request count, if the droplet's request log (plan 05) shows it matters.
