# Approval packet: Pages and header: every page refreshes honestly, and every control survives a restart without ever re-sending

## What is being built

The operator keeps every page open through restarts and the trading day, and each page's header follows the engine, each page shows its data's age and never draws an error as data, and each POST control works after a restart without ever sending anything he did not click again.
- The header on the four other pages follows the engine, the countdown and the live flag (AC-1).
- Every POST control refreshes its token after a restart, never re-sends, and says when an action was not sent (AC-2).
- `/history` keeps a drilled day, uses the ET date and shows its data age (AC-3).
- `/performance` renders each panel independently and shows its data age (AC-4).
- `/ai-advisor` never shows an error as an empty list and signals a Frontrunner build's end (AC-5).
- `/settings` shows a load failure, and its flush confirm reads a small response (AC-6).
- The settings modal never saves one symphony with another's data (AC-7).

## Acceptance criteria and scenarios

### AC-1: The header on the four other pages follows the engine, the countdown and the live flag
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

Negative coverage: N1 n/a: the header takes no input | N2 n/a: the read refuses nothing but a lapsed session (S-1.7) | N3 n/a: every field has a value or the connection-lost state | N4 S-1.7 | N5 S-1.9 | N6 S-1.5, S-1.6, S-1.11 | N7 S-1.8 | N8 n/a: no edit state

### AC-2: Every POST control refreshes its token after a restart, never re-sends, and says when an action was not sent
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

Negative coverage: N1 n/a: no control's input changes | N2 S-2.2, S-2.3, S-2.5, S-2.6, S-2.7, S-2.8, S-2.9, S-2.15, S-2.16, S-2.17, S-2.18, S-2.19, S-2.20 | N3 n/a: a missing token is the stale-token case | N4 S-2.9 | N5 S-2.10 | N6 S-2.12, S-2.22 | N7 S-2.11 | N8 S-2.13

### AC-3: `/history` keeps a drilled day, uses the ET date and shows its data age
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

Negative coverage: N1 S-3.3 | N2 n/a: the route refuses only non-integer days, which the page never sends | N3 n/a: an empty window is the existing honest empty state | N4 S-3.4 | N5 S-3.7 | N6 S-3.5 | N7 S-3.8 | N8 n/a: a drilled day is a view, not an edit

### AC-4: `/performance` renders each panel independently and shows its data age
Rule: When `/performance` refreshes, a failure in one panel's rendering or request shall leave every other panel and that panel's last good rows in place, a 401 shall go to `/login`, and the page shall show the date and time of its data.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-4.1 | N6 | `Chart` is undefined because the script failed to load | the page refreshes | the metrics table, headline stats and banner render |
| S-4.2 | N6 | one symphony's `/api/exit-turnover` request fails | the panel refreshes | the other rows render and that row keeps its last values |
| S-4.3 | N6 | every exit-turnover request fails | the panel refreshes | the panel keeps its last rows with a connection-lost cue |
| S-4.4 | N4 | the session lapsed | a refresh answers 401 | the page navigates to `/login` |
| S-4.5 | B | any load | the page renders | it shows "data as of" with the date and time of the newest shadow observation the response used (overview SD-1) |
| S-4.6 | N7 | the 60 s refresh | it runs | the same requests are sent as at the ref |

Negative coverage: N1 n/a: the page's inputs are the window and symphony pickers, unchanged | N2 n/a: the routes refuse only invalid windows, which the page never sends | N3 n/a: an empty window is the existing honest empty state | N4 S-4.4 | N5 n/a: one refresh at a time per 60 s tick, as at the ref | N6 S-4.1, S-4.2, S-4.3 | N7 S-4.6 | N8 n/a: no edit state

### AC-5: `/ai-advisor` never shows an error as an empty list, and signals a Frontrunner build's end
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

Negative coverage: N1 n/a: no input | N2 n/a: the build route's refusals (missing key) show as at the ref | N3 S-5.6, S-5.8 | N4 S-5.2 | N5 S-5.5 | N6 S-5.1, S-5.4, S-5.9 | N7 S-5.7, S-5.10 | N8 n/a: a running build is S-5.5

### AC-6: `/settings` shows a load failure, and its flush confirm reads a small response
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

Negative coverage: N1 n/a: no new input | N2 n/a: nothing new is refused | N3 n/a: no armed symphonies is the existing empty list | N4 S-6.4 | N5 n/a: one load at a time | N6 S-6.1, S-6.7 | N7 S-6.5 | N8 S-6.6

### AC-7: The settings modal never saves one symphony with another's data
Rule: If a response for a symphony other than the one the settings modal is open on lands, then the modal shall discard it, and Save shall post only the open symphony's own loaded values and the operator's edits, with the go-live confirmation whenever the live flag turns on.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-7.1 | N5 | the modal is opened for A and then for B within the response time | A's response lands last | the modal shows B's settings, and Save posts B's own values |
| S-7.2 | N8 | the modal for B has unsaved edits | a dashboard poll lands | the modal's symphony, values and edits are unchanged |
| S-7.3 | B | B is in dry run | the operator turns B live and saves | the go-live confirmation shows before the POST |
| S-7.4 | N7 | any save | it is sent | `POST /api/symphony-settings/{symphony_name}` carries the same fields as at the ref |
| S-7.5 | N6 | B's load fails | the modal renders | "Failed to load settings. Please try again." and Save is not offered |
| S-7.6 | N5 | the modal is closed while a response is in flight | the response lands | nothing renders and nothing is saved |

Negative coverage: N1 n/a: the modal's inputs are unchanged | N2 n/a: the route's refusals show as at the ref | N3 n/a: a missing setting is the route's default, unchanged | N4 n/a: the route's auth and CSRF hooks are unchanged; a stale token is AC-2 | N5 S-7.1, S-7.6 | N6 S-7.5 | N7 S-7.4 | N8 S-7.2

## Rule-by-entry-point matrix

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

## Narrowed mappings

R-1: Every timing issue the refresh-timing audit found is resolved; maps to AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-7; left out: this plan's part is the findings G-67 assigns it
R-3: Every datum the poll carries reaches the screen without a reload; maps to AC-1, AC-3, AC-4; left out: the four other pages and the header
R-4: Every number, chart, label and status on every page shows the newest data the engine has produced; maps to AC-1, AC-3; left out: the header and `/history`
R-6: Every page recovers by itself after a daemon restart, a laptop sleep, a tab return or a network loss; maps to AC-1, AC-2; left out: the header and the POST controls
R-10: The operator's selections persist, and Cash Now, Force run and Emergency Liquidate keep working through the day; maps to AC-2, AC-3; left out: the drilled day, and the three controls' behaviour after a restart; their requests, phrase gate and refusal messages stay ux-overhaul 04's
R-11: Tabs kept open across a deploy, a restart or a sleep, and the other pages, stay current; maps to AC-1, AC-2; left out: the header and the controls on every page
R-13: Every freshness stamp carries a date and its true age; maps to AC-3, AC-4; left out: `/history` and `/performance`; `/ai-advisor`'s stamps are ux-overhaul 02 AC-8's
R-14: After a restart, live updates, actions and the header recover without a reload; maps to AC-1, AC-2; left out: the header and the actions
R-16: An error, refused, unavailable or timed-out poll response is never drawn as data; the last good values stay with a cue and a retry; a 401 goes to login; maps to AC-3, AC-4, AC-5, AC-6; left out: the other pages' polls; `/` is plan 03's
R-23: The newest issued request wins on every page, and two tabs are independent; maps to AC-3, AC-7; left out: `/history` and the settings modal
R-27: An error body is distinguishable from data; maps to AC-3, AC-5; left out: `/history` and `/ai-advisor`
R-63: The LIVE / DRY RUN mode pill follows the live flag without a reload; maps to AC-1; left out: the four other pages (overview SD-9); `/` is plan 03 AC-6
R-78: `/performance` renders each panel independently, keeps the last good rows on a failed request and shows its data age; maps to AC-4; left out: the request count per minute stays (NB-5)
R-79: `/ai-advisor` never shows an error as an empty list and signals a Frontrunner build's completion; maps to AC-5; left out: no new "loaded at" stamps on the server-rendered panels (NB-6)

## User tasks

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

## Open questions

| Q-n | Question | Blocks (AC ids or "plan") | Proposed default | Answer |
|---|---|---|---|---|
| Q-1 | What does the header on the four other pages read its values from? | AC-1 | A light status read (`/health`'s liveness fields from plan 02 plus the market state, engine phase, next-run seconds, mode and workspace label), polled every 30 s and on tab return; the route is a Gate-2 choice | default approved by the PM, [PM-ASSUMED] |
| Q-2 | When does a page fetch a new CSRF token? | AC-2 | After any 403 on a POST and whenever a poll shows `daemon_started_at` changed; never on a timer; never by re-sending | default approved by the PM, [PM-ASSUMED] |
| Q-3 | How does `/ai-advisor` learn a Frontrunner build finished? | AC-5 | Each on-demand build writes one append-only `FRONTRUNNER_BUILD_SUMMARY` row in `advisor_observations`, as `RETIREMENT_RUN_SUMMARY` does, and the tab polls for it every 15 s for at most 30 minutes | approved by the PM with the summary row (PM amendment), [PM-ASSUMED] |
| Q-4 | No `.claude/workflows/` file exists for `/history`, `/performance`, `/ai-advisor`, `/settings` or the shared header. Accept "no workflow file" and the User Tasks written here? | AC-1 | Yes, until the operator confirms a workflow file | default approved by the PM, [PM-ASSUMED] |

Sensitive: security, money (AC-2, AC-7). qa writes hidden scenarios for the named criteria after approval; they are not shown to the builders.
