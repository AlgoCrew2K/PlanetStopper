# Feature: Dashboard refresh: every element of `/` follows every refresh, in every market state, and recovers by itself
Status: planned
Story: none: operator chat request on 2026-10-22, saved verbatim in feature-plans/refresh-timing/00-overview.source.md
Type: fix
Suite: refresh-timing 03
Repos: PlanetStopper
Base: the assembled `ux-overhaul/05` tip (C1 61707cd6, C2 d10040f7, C3 67cf9ca3 with its round 2 GREEN c20265db)
Ref: PlanetStopper@37c8e983b64c6980c28199a3f9318486b60f3864
Sensitive: money (AC-7)
Depends on: ux-overhaul plan 05 assembled on `ux-overhaul/05`; plan 02 merged into this plan's tip before the AC-2, AC-3 and AC-4 cycles (the event's `changed` flag, `engine_phase` and the label rules); plan 01 merged into this plan's tip before the AC-5, AC-6 and AC-9 cycles (the frozen `symphonies` list, `guard_alpha`, `mdd_lifetime` and the dated stamp); the model-unpin plan through ux-overhaul 05's own tip. Overlaps: AC-10 adds a pre-paint script to the head of every page template, and AC-4 edits `static/chrome.js` (the label precedence, the NEXT countdown and the Stale check); ux-overhaul 04 rewrites both, and plan 04's tip merges this plan and resolves them

## Trigger
The operator wrote "I expect any and all timing issues to be resolved as well, I'm tired of the wonky updates and / or no updates to data on the poll" (G-1, saved in `feature-plans/refresh-timing/00-overview.source.md`). The refresh-timing audit at the ref (a review artifact outside the tracked tree, dated 2026-10-22; client behaviour from the code and a node harness running the real `static/index.js` and `static/chrome.js` against payloads captured from a copy of the production database) assigned this plan the dashboard's client findings. Each was re-read at the ref for this spec; ux-overhaul plan 05 (`feature-plans/ux-overhaul/05-dashboard-charts.md` on branch `ux-overhaul/fixtures`) changes the hero chart, sparklines, first-paint verdicts and dials, banners, account refresh and detail title before this plan, and none of its criteria touches the findings below:

1. TA-2 and TA-22: every poll calls `renderGuardAlpha(data)` (`static/index.js:1390`), which writes `portfolio_strip.guard_alpha`, always computed for the 30d default (`app.py:1980`, `:2366-2376`), under the window label the picker wrote (`static/index.js:1633-1634`); its null fallback requests `/api/strip/30d` (`static/index.js:175`). The picker wraps the strip's `vol_bot` and `vol_held` as `meta.portfolio` (`static/index.js:1497-1503`) while each poll writes the lifetime `vol_bot` and a `vol_held` that is always null (`app.py:2254-2260`), and the row needs both (`static/index.js:1083-1085`). Measured: with 1Y picked the headline reads +4.40%, then +0.68% under "1Y" after the next poll; the vol row flips between "Bot 19.1% / Held 21.6%" and dashes.
2. TA-10: `loadState` renders any JSON body with no `ok` check (`static/index.js:1469-1480`); a 500, 401 or 503 JSON body sets the counts to 0 (`static/index.js:1415-1422`), the header to "Closed" (`static/chrome.js:214-222`) and leaves the old as-of; a 401 never redirects.
3. TA-4: the event stream's `onerror` does nothing (`static/index.js:1601`); a reconnect answered with a non-200 status (Caddy's 502 while the daemon restarts) closes an EventSource for good under the HTML specification, and the $-saved panel refreshes only at load, on a picker click and on the event (`static/index.js:1600`, `:1604`, `:1662`). Production had 4 restarts on 2026-10-22.
4. TA-19 and TA-32: no `visibilitychange`, `pageshow`, `online` or `focus` handler exists in `static/` or `templates/`; `lastSuccessfulPollAt` is written (`static/index.js:1454`, `:1473`) and never read; the NEXT countdown is set from a `next_run_seconds` taken early in the request (`app.py:3161-3164`) with no allowance for the response's age, clamps at 0 (`static/chrome.js:276-292`) and changes from "NEXT 00:42" (`templates/_chrome.html:57`) to "NEXT: 0:48" on the first poll.
5. TA-3 and TA-9 (client halves): `updateVerdicts` writes the dash when `guard_alpha` is null (`static/index.js:379-382`), which the frozen payload never carries; `updateCards` returns when the payload has no `symphonies` (`static/index.js:1177-1178`) while `updateSectionMeta` counts from `bot_state` (`static/index.js:1310-1321`). Measured after the close: 6 of 6 triggered cards lose their verdict; badges read 6 and 6 while the cards sit 11 and 1.
6. TA-21, TA-26 and TA-36: the mode pill and the hero legend are server-rendered only (`templates/_chrome.html:93-95`, `templates/index.html:861`); the comparison bars get `winner` and `loser` only at render (`templates/index.html:956-1030`) while the poll writes widths only (`static/index.js:1033-1041`); the per-card lifetime Max DD, the "insufficient history" labels, the edit-vars count and the Sleeves and CVaR panels are server-rendered only (`app.py:1464`, `:1808`, `templates/index.html:1186-1190`, `:1690-1765`).
7. TA-27: Cash Now's `disabled` is set at render (`templates/index.html:1216`) and `updateCards` never touches it.
8. TA-24 and TA-25: `openDetailPanel` reads `_botState` once (`static/index.js:431-460`) and labels the live return "real-time"; `renderIntradayChart` returns on empty data before destroying the previous chart (`static/index.js:585-591`), and `fetchDetailChart` has no sequence check (`static/index.js:570-583`).
9. TA-29: `/api/chart` keys the held overlay to the host-local date (`app.py:4852`) on the UTC host; `chart_history` resets only at the day's first in-gate cycle (`alpha_bot_execution.py:748-750`); `renderMcDial` takes the tape's last non-null reading (`static/index.js:897-905`) after `updateCards` wrote the state's (`static/index.js:1190`).
10. TA-41: the stored theme is applied at DOMContentLoaded (`static/tweaks.js:114-118`), with no pre-paint script and no `storage` listener.

Found during this spec's investigation: Cash Now posts `{symphony_id}` to `/api/sell_account` (`static/index.js:409-413`), which refuses any request without `confirm_account_id` with 400 "confirm_account_id is required" (`app.py:5455-5456`), and the button resets with no message (`static/index.js:418-421`); so Cash Now cannot succeed at the ref. Its request may not change in this suite (overview NB-6); plan 04 shows the refusal (its AC-2) and the request defect is a follow-up (NB-6 below).

## Summary
The operator keeps `/` open through a whole trading day and every number, label, card, panel and status on it follows the newest data, keeps the window and symphony he picked, never draws an error as data, never goes quiet after a restart or a sleep, and says how old its data is.
- The headline and Ann. Vol keep the picked window (AC-1).
- An error, refused or timed-out poll keeps the last values with a cue; a 401 goes to login (AC-2).
- The event stream re-opens after it closes, and the $-saved panel refreshes without it (AC-3).
- An age-based stale cue, a catch-up on return, wake and network return, and a true countdown (AC-4).
- After the close and before the open, cards, verdicts and badges match the poll (AC-5).
- Render-only elements follow the poll: mode pill, hero legend, bar classes, card labels, Sleeves and CVaR panels (AC-6).
- Cash Now is disabled on a card that exits (AC-7).
- The detail panel follows polls and shows only its own symphony (AC-8).
- Sparklines and dials show only the current session's readings (AC-9).
- The stored theme applies before first paint and across tabs (AC-10).

## Requirements as Given
[G-1] I expect any and all timing issues to be resolved as well, I'm tired of the wonky updates and / or no updates to data on the poll. 00-overview.source.md:6
[G-12] PM Step-2 answers ([PM-ASSUMED]; the operator is not available for questions; project directive: record assumptions, do not stop) 00-overview.source.md:18
[G-13] (Building 1) Every number, chart, label and status on every page shows the newest data the engine has produced. 00-overview.source.md:21
[G-14] (Building 1) It updates without a reload, never reverts or flickers when no new data arrived, and never shows stale or wrong-day data without a dated, honest cue. 00-overview.source.md:21
[G-15] (Building 1) It recovers by itself after a daemon restart, laptop sleep, tab return or network loss. 00-overview.source.md:21
[G-16] (Building 2) Users: the operator, through `/`, `/history`, `/performance`, `/ai-advisor`, `/settings` and the shared header. 00-overview.source.md:22
[G-19] (Building 3) Flows: keep `/` open through a whole trading day: pre-market, the 09:30 open, intraday, the 15:53-15:59 blackout, the 16:00 close, the evening and overnight. 00-overview.source.md:23
[G-20] (Building 3) Along the way: pick a hero window, pick and clear an account, open a detail panel, use Cash Now, Force run and Emergency Liquidate. 00-overview.source.md:24
[G-21] (Building 3) Keep tabs open across a deploy or restart and a laptop sleep; switch tabs and come back; use the other pages. 00-overview.source.md:25
[G-22] (Building 3) Expected: values change within one cycle of new data; the operator's selections persist through polls; every freshness stamp carries a date and its true age; after a restart, live updates, actions and the header recover without a reload. 00-overview.source.md:26
[G-23] (Building 3) Use the workflow files under `.claude/workflows/` if any have a dated Confirmed line; otherwise write "no workflow file" with the reason. 00-overview.source.md:27
[G-24] (Building 4) An `/api/state` 401/403/500/503/timeout or an error-shaped body is never drawn as data. 00-overview.source.md:29
[G-25] (Building 4) The last good values stay, with a visible stale/connection cue and retry; a 401 goes to login. 00-overview.source.md:29
[G-29] (Building 4) After a restart, the event stream reconnects and the CSRF token refreshes. 00-overview.source.md:33
[G-33] (Building 4) Overlapping requests: the newest issued request wins everywhere, not only `loadState`. 00-overview.source.md:35
[G-34] (Building 4) Two tabs are independent. 00-overview.source.md:35
[G-38] (Building 5) The error body shape is distinguishable from data. 00-overview.source.md:39
[G-51] (Not building 1) Server-sent events stay; they are fixed, not replaced. 00-overview.source.md:51
[G-53] (Not building 2) trade, liquidation and order request payloads; 00-overview.source.md:54
[G-59] (Not building 3) Items the audit marks COVERED stay with their plans (ux-overhaul 05, C3 round 2, 04 AC-8/AC-9, 03 AC-2). 00-overview.source.md:60
[G-65] 03-dashboard-refresh (`/`). TA-2, TA-4, TA-10, TA-19, TA-21, TA-22, TA-24, TA-25, TA-26, TA-27, TA-29, TA-32, TA-36, TA-41, and the client halves of TA-3 and TA-9. 00-overview.source.md:66
[G-66] This plan supersedes ux-overhaul plan 05's S-4.7 (the headline revert) and NB-4 (the event stream kept as is); record both under Earlier criteria with the reason. 00-overview.source.md:66

[G-87] (PM amendment) PLAN 03: TA-36 criterion, new scenario: Sleeves rows added or removed while `/` is open appear or disappear on the next poll, without a reload. 00-overview.source.md:91
[G-88] (PM amendment) PLAN 03: data-refresh-count increments for the newest request's handled outcome, whether a rendered body or a handled failure. A superseded request never counts. 00-overview.source.md:92
[G-89] (PM amendment) PLAN 03: NEW gap, found by the ux05 C1 closer: when the poll series has no row inside the picked window, the client currently returns before drawing or captioning. The new window's label then sits over the previous window's series with no cue. This happens when a refused /api/hero-chart meets a stale poll series, or on 1 January for YTD. Default: keep the old series with a visible cue saying the picked window has no data yet. Add it as a scenario under the window criterion. 00-overview.source.md:93

[G-93] (PM final amendment) "Degraded" header state: Overview SD-13 gains a state, "Degraded", at rank 3. The full order is: Connection Lost > Not updating > Degraded > Closed > Rebalance pause > Opening > Stale > Live. Only `/health`'s `degraded_reasons` changes the label. Add a scenario to plan 03 AC-4 (on `/`) and to plan 04 AC-1 (the four other pages): when `degraded_reasons` is non-empty, the header reads "Degraded", with a tooltip naming the reasons. 00-overview.source.md:99

## Requirement Inventory
These are the overview's R ids. Overview items R-83, R-85 and R-87 (the Base, Depends on and overlap notes) and R-88 (the Earlier criteria section) are met by those parts of this plan.

| R-n | Requirement item | Source (G ids) | Maps to (AC ids, or NB-n) | Scope (full, or narrowed: what is left out) |
|---|---|---|---|---|
| R-1 | Every timing issue the refresh-timing audit found is resolved | G-1 | AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-7, AC-8, AC-9, AC-10 | narrowed: this plan's part is the findings G-65 assigns it |
| R-2 | No shown value changes, reverts or flickers when no new data arrived | G-1, G-14 | AC-1, AC-5, AC-6 | narrowed: the client writers on `/` |
| R-3 | Every datum the poll carries reaches the screen without a reload | G-1, G-14 | AC-5, AC-6, AC-8 | narrowed: `/` |
| R-4 | Every number, chart, label and status on every page shows the newest data the engine has produced | G-13 | AC-5, AC-6, AC-8, AC-9 | narrowed: `/` |
| R-5 | Stale or wrong-day data is shown only with a dated, honest cue | G-14, G-22 | AC-4, AC-9 | narrowed: the client's age cue and the sparkline's session date; the server's stamps are plan 01's |
| R-6 | Every page recovers by itself after a daemon restart, a laptop sleep, a tab return or a network loss | G-15 | AC-3, AC-4 | narrowed: `/` |
| R-7 | The suite covers `/` and the GET routes it polls | G-16 | AC-1, AC-9 | narrowed: `/`, `/api/strip/{window}`, `/api/guard-alpha-summary` and `/api/chart/{symphony_id}` |
| R-9 | A whole trading day on `/` shows correct, current values | G-19 | AC-4, AC-5, AC-9 | narrowed: the client side |
| R-10 | The operator's selections persist through every refresh, and Cash Now keeps working through the day | G-20, G-22 | AC-1, AC-7, AC-8 | narrowed: the hero window, the open detail panel and Cash Now's enabled state; the account pick is ux-overhaul 05 AC-6, and Force run and Emergency Liquidate are ux-overhaul 04's and plan 04's |
| R-11 | Tabs kept open across a deploy, a restart or a sleep, switched away and back, stay current | G-21 | AC-3, AC-4 | narrowed: `/` |
| R-12 | Values change within one engine cycle of new data | G-22 | AC-1, AC-3 | narrowed: `/` |
| R-13 | Every freshness stamp carries a date and its true age | G-22 | AC-4 | narrowed: the age cue on `/`; the date is plan 01's |
| R-14 | After a restart, live updates, actions and the header recover without a reload | G-22, G-29 | AC-3 | narrowed: the event stream on `/`; the token is plan 04's |
| R-15 | Each plan with a screen names its workflow file or states "no workflow file" with the reason | G-23 | AC-1 | full |
| R-16 | An error, refused, unavailable or timed-out poll response is never drawn as data; the last good values stay with a cue and a retry; a 401 goes to login | G-24, G-25 | AC-2 | narrowed: `/`'s `/api/state` poll; the other pages are plan 04's |
| R-20 | After a restart the event stream reconnects, and the $-saved panel refreshes even while no stream is open | G-29, G-51, G-65 | AC-3 | full |
| R-23 | The newest issued request wins on every page, not only `loadState`, and two tabs are independent | G-33, G-34 | AC-1, AC-3, AC-8, AC-9 | narrowed: `/`; `loadState` itself is ux-overhaul 05 C3 round 2's (NB-3) |
| R-27 | An error body is distinguishable from data | G-38 | AC-2 | narrowed: `/api/state` on `/` |
| R-37 | Server-sent events stay, fixed and not replaced | G-51 | AC-3 | narrowed: the client's half |
| R-39 | Trade, liquidation and order request payloads stay exactly as they are | G-53 | NB-2 | full |
| R-45 | Findings the audit marks covered stay with their plans | G-59 | NB-3 | full |
| R-61 | The guard-alpha headline keeps the window the operator picked through every refresh | G-65 | AC-1 | full |
| R-62 | `/` shows a stale cue by data age, catches up at once on tab return, wake and network return, and its "Stale" check does not trust the browser clock | G-65, G-15 | AC-4 | full |
| R-63 | The LIVE / DRY RUN mode pill and the hero legend follow the live flag without a reload | G-65 | AC-6 | narrowed: on `/`; the other four pages' pill is plan 04 AC-1 (overview SD-9) |
| R-64 | The Ann. Vol row keeps the picked window through every refresh | G-65 | AC-1 | full |
| R-65 | The open detail panel's numbers follow every refresh | G-65 | AC-8 | full |
| R-66 | The detail panel shows only the symphony it is open on | G-65 | AC-8 | full |
| R-67 | The comparison bars' winner and loser follow every refresh | G-65 | AC-6 | full |
| R-68 | Cash Now is disabled on a card that exits while the page is open | G-65 | AC-7 | full |
| R-69 | Sparklines and MC dials show only the current session's readings | G-65 | AC-9 | full |
| R-70 | The NEXT countdown is true and keeps one format | G-65 | AC-4 | full |
| R-71 | The Sleeves panel, the CVaR panel and the per-card labels follow every refresh | G-65, G-87 | AC-6 | full |
| R-72 | The stored theme applies before first paint and across open tabs | G-65 | AC-10 | full |
| R-73 | A triggered card keeps its verdict after the close | G-65 | AC-5 | full |
| R-74 | After the close and pre-market the cards and section badges match the poll | G-65 | AC-5 | full |
| R-75 | This plan supersedes ux-overhaul plan 05's S-4.7 and NB-4 and records both, with the reason, under Earlier criteria | G-66 | AC-1, AC-3 | full |
| R-89 | Open questions are answered by recorded defaults, never by stopping | G-12 | AC-1, AC-2, AC-4, AC-8, AC-9 | full |
| R-90 | A refresh with unchanged data sends no per-symphony `/api/chart` requests | G-1 | AC-9 | full |
| R-94 | The dashboard's refresh counter counts each newest request's handled outcome, rendered or failed, and never a superseded one | G-88 | AC-2 | full |
| R-95 | When the picked window has no data, the hero keeps its previous series with a visible no-data-yet cue | G-89 | AC-1 | full |
| R-97 | The header reads "Degraded", with a tooltip that describes each reason in a fixed plain sentence (never the raw key), whenever `/health`'s `degraded_reasons` is non-empty, ranked under "Connection Lost" and "Not updating" | G-93 | AC-4 | full |

## User Tasks
Workflow (Dashboard `/`): no workflow file: this repository has no `.claude/workflows/` directory at the ref.

### T-1: Keep `/` open through a trading day
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Open `/` at 08:00 ET | the prior session's snapshot | Cards, badges and rows agree; the as-of names the prior session's date | S-5.4, S-5.3 |
| 2 | Pick 1Y | none | The headline, its label and the Ann. Vol row read 1Y and keep it through every poll and event | S-1.1, S-1.2, S-1.5 |
| 3 | Watch a symphony exit at 11:00 | a trigger | Its card turns triggered and its Cash Now button disables | S-7.1 |
| 4 | Stay past 16:00 | the close | Each triggered card keeps its verdict; badges equal the cards | S-5.1, S-5.3 |

### T-2: Survive a restart, a sleep and a tab switch
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Leave `/` open while the daemon restarts | a 502 window | The event stream re-opens; the $-saved panel keeps refreshing | S-3.1, S-3.3 |
| 2 | Switch tabs for 10 minutes and come back | none | One refresh fires at once; the stale cue shows while data is old | S-4.2, S-4.1 |
| 3 | Sleep the laptop for 5 minutes | none | On wake one refresh fires at once | S-4.3 |

### T-3: Read a symphony's detail
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Open a card's detail panel and leave it open | a moving symphony | Its numbers update on each poll | S-8.1 |
| 2 | Open another card quickly | two symphonies | Only the second symphony's chart, logs and profile show | S-8.3, S-8.4 |

## Earlier criteria on the same surface
Searched every `feature-plans/*.md` (with the `.completed.md` and `.obsolete.md` families and the suite folders; the UX overhaul suite read from branch `ux-overhaul/fixtures` at 340b9d03), `DECISIONS.md` and `docs/generated/static_index_js.md` for `renderGuardAlpha`, `fetchWindowedStrip`, `EventSource`, `cycle-complete`, `loadState`, `updateCards`, `updateVerdicts`, `openDetailPanel`, `renderMcDial`, `cash-now`, `mode-pill`, `winner`, `tweakchange` and `data-theme`.

| Earlier criterion (plan and AC) | Surface | Disposition (kept, changed or contradicted) | Why |
|---|---|---|---|
| ux-overhaul plan 05 S-4.7 (any window: a poll leaves the windowed headline, the vs-rows and the $-saved panel requesting and rendering as at the ref) | Headline, vs-rows, $-saved | contradicted | As at the ref the poll writes the 30d value under the picked label (TA-2) and the vol row flips (TA-22); AC-1 keeps the picked window, and AC-3 changes when the panel refreshes (G-66) |
| ux-overhaul plan 05 NB-4 (the 30 s poll, the event stream, the picker's requests, the $-saved panel and the account-basis chip stay exactly as at the ref) | Poll, stream, panel | contradicted | The stream dies for good after a restart and the panel refreshes only through it (TA-4); AC-3 re-arms the stream and refreshes the panel by poll while it is down; the 30 s interval and the chip stay (G-66) |
| ux-overhaul plan 05 AC-3 (a refresh never empties, rebuilds or re-animates a drawn chart; S-3.5 keeps points on a failed poll; S-3.8 keeps the detail chart and its toggles) | Hero, sparklines, detail chart | kept | AC-2 keeps every other value on a failed poll as S-3.5 keeps the chart's; AC-8 updates the panel's numbers without rebuilding its chart (S-8.5) |
| ux-overhaul plan 05 AC-6 and PM-7 (picking an account reloads that account's cards; after hours the client hides other accounts' cards) | Account filter | kept | S-5.6 |
| ux-overhaul plan 05 S-1.2 (when `/api/state` fails the existing "connection lost" freshness text shows) | `#hero-data-as-of` | changed | The cue keeps "connection lost" and adds the last data stamp (S-2.1) |
| ux-overhaul plan 05 AC-2 (first-paint verdicts and dials) and S-2.10 (three polls never flash the dash) | Card verdict, dial | kept | AC-5 makes S-2.10 hold after the close; plan 01 AC-9 owns the label |
| ux-overhaul plan 05 C2's `writeVerdict` (a missing `guard_alpha` writes nothing, keeping the server-rendered text) | Card verdict | changed | AC-5 shows the dash for a missing, null or out-of-bound `guard_alpha` (S-5.10, S-5.9), as ux-overhaul 05 S-2.5 already asks of both paths; two C2 tests that pin "write nothing" are rewritten with the reason |
| ux-overhaul plan 05 AC-5 (charts recolour on `tweakchange`) | Theme | kept | AC-10 adds the pre-paint theme and the cross-tab change, which reaches AC-5's recolour through the same event |
| ux-overhaul plan 05 NB-1 (no change to `/` beyond its eight criteria) | `/` | changed | This plan's criteria are the next change set, by the PM's split (G-65) |
| ux-overhaul plan 05 PM-3 (a poll adds at most one `/api/hero-chart` request, only when the series changed) | Request count | kept | AC-1 follows the same rule for `/api/strip/{window}` (S-1.11, S-1.12) |
| ux-overhaul 05 C3 round 2 (the newest-request guard on every `loadState` caller, GREEN c20265db; the audit's TA-11) | `loadState` | kept | NB-3; this plan applies the same rule to its other requests (SD-6) |
| ux-overhaul plan 03 (branch `ux-overhaul/fixtures`) AC-2 (the Correlations as-of names the last trading date and the run time) | Correlations as-of | kept | Not on `/`; named because the audit counts it covered (TA-52) |
| `feature-plans/dashboard-realtime-push.md` AC-3 and AC-5, and DE-SSE-PUSH-001 ("SSE has browser-native reconnect"; "SSE failure silently degrades to poll") | Event stream | contradicted | A non-200 reconnect closes the stream for good, and the panel never degrades to the poll; AC-3 re-arms and polls the panel |
| `feature-plans/guard-alpha-saved-coherence.md` AC-2 (the picker re-windows the $-saved panel in lockstep) and DE-GAS-COHERENCE-001 | $-saved panel | kept | S-1.9, S-3.3 use the picked window |
| `feature-plans/math-r0.md` AC-5 (one calendar window per picker click) | Hero, strip | kept | AC-1 keeps the calendar tokens |
| `feature-plans/audit-bl4-account-basis-honesty-render.md` AC-1 to AC-3 (the basis chip and `#account-basis-as-of` on every poll) | Basis chip | kept | AC-2 keeps the chip's last values on a failed poll |
| `feature-plans/mdd-window-truth.md` (no Max DD winner class while history is insufficient; DE-PERF-WINDOW-TRUTH-001) | Comparison bars | kept | AC-6 applies the server render's rule per row, including no class on Max DD with insufficient history (S-6.4) |
| `feature-plans/math-f7.md` AC-2 (the dial reads an explicit dash for triggered symphonies) | MC dial | kept | S-9.5 |
| `feature-plans/managed-sleeves.md` (the Sleeves panel on `/`; arming reloads the page) | Sleeves panel | changed | The panel's counts now follow each poll (AC-6); arming still reloads, unchanged |
| ux-overhaul plan 04 AC-6 (login loads the app font and the stored theme) | Theme | kept | AC-10's pre-paint theme covers the pages that load `static/tweaks.js`; login keeps plan 04's way |
| ux-overhaul plan 04 AC-4 S-4.6 (the mode pill reads LIVE or DRY RUN as at the ref) and NB-5 (the pill's source is `_build_meta`) | Mode pill | changed | On `/` the pill now refreshes on each poll (S-6.1); its source stays `meta.mode` from `_build_meta`, and its look and text stay |
| ux-overhaul plan 05 NB-5 (every value `/api/chart` computes stays) | `/api/chart` | changed | AC-9 keys the held overlay to the ET date and adds the tape's session date |
| `DECISIONS.md` DE-CLOSED-BOUNCE-001 client leg (`renderGuardAlpha` falls back to `/api/strip/30d` when the poll's guard alpha is null; `updateComparisonRows` gates the vs-rows on `data_as_of`) | Headline, vs-rows | changed | The fallback requests the picked window, not 30d (S-1.3); the `data_as_of` gate stays, so no windowed strip ever writes the Cumulative row |

## Acceptance Criteria (Building)
### AC-1: The headline and Ann. Vol keep the picked window
Requirement items: R-1, R-2, R-7, R-10, R-12, R-15, R-23, R-61, R-64, R-75, R-89, R-95
Rule: When any refresh lands while a hero window is selected (30d when none was picked), the hero shall keep its previous series with a visible no-data-yet cue whenever that window has no data, and the guard-alpha headline, its window label and the Ann. Vol row shall show that window's values from `/api/strip/{window}`, never the poll's default-window values, and a response for an earlier selection that lands after a later one shall be discarded.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-1.1 | B | 1Y is picked and the headline shows `/api/strip/1y`'s guard alpha G1 | a 30 s poll carrying `portfolio_strip.guard_alpha` G30, different from G1, lands | `#guard-alpha-headline` reads G1 formatted as at the ref, or the fresh 1Y value; `#guard-alpha-window-label` reads "1Y" |
| S-1.2 | B | as S-1.1 | a cycle event's refresh lands | as S-1.1 |
| S-1.3 | N3 | 1Y is picked | a poll lands whose `portfolio_strip.guard_alpha` is null | the request sent is `/api/strip/1y`, never `/api/strip/30d`; the headline keeps its 1Y value until it answers |
| S-1.4 | N7 | no window has been picked (30d default) | the page renders and three polls land with no new data | the headline reads the same value on the render and after each poll |
| S-1.5 | B | 1Y is picked and the vol row shows `/api/strip/1y`'s `vol_bot` and `vol_held` | a poll lands | the row still shows the 1Y values |
| S-1.6 | N3 | 30d is picked and `/api/strip/30d` carries `vol_bot` null | the click and three polls land | the row reads the dashes each time, never a lifetime value under 30d |
| S-1.7 | N5 | the operator clicks 1Y then 30d before either strip response lands; 1Y's lands last | both land | the headline, label and vol row show 30d |
| S-1.8 | N6 | 1Y is picked | the refresh's `/api/strip/1y` request fails | the headline, label and vol row keep their last 1Y values |
| S-1.9 | N7 | any window | a refresh lands | the hero chart (ux-overhaul 05 AC-4), the Today, Cumulative and Max DD rows and the $-saved panel's window are as before this plan |
| S-1.10 | N1 | All Time is picked | a poll lands | the headline reads `/api/strip/all`'s value |
| S-1.11 | B | 1Y is picked | a refresh lands whose data differs from the last one (a new data stamp) | exactly one `/api/strip/1y` request is sent |
| S-1.12 | N5 | 1Y is picked | a refresh lands whose data stamp equals the last one's | no `/api/strip/1y` request is sent |
| S-1.13 | B | no window was picked | the page renders and the first poll lands | the vol row shows `/api/strip/30d`'s `vol_bot` and `vol_held`, or the dashes when either is null, never the poll's lifetime `vol_bot` beside a null `vol_held` |
| S-1.14 | N3 | the poll's series has no row inside the picked window (a refused `/api/hero-chart` meeting a stale poll series, or YTD on 1 January) | the window is picked or a refresh lands | the hero keeps its previous series, the label reads the picked window, and a visible cue under it reads "No data yet for YTD" (the picked window's label); the cue clears when data for the window arrives |

Negative coverage: N1 S-1.10 · N2 n/a: the strip route refuses only unknown tokens, which the picker never sends · N3 S-1.3, S-1.6, S-1.14 · N4 n/a: the route is behind the global auth hook, unchanged · N5 S-1.7, S-1.12 · N6 S-1.8 · N7 S-1.4, S-1.9 · N8 n/a: a window pick holds no edit state
Questions: Q-5

### AC-2: An error, refused or timed-out poll keeps the last values with a cue; a 401 goes to login
Requirement items: R-1, R-16, R-27, R-89, R-94
Rule: If an `/api/state` response is not a 2xx JSON data body (a 401, 403, 500 or 503, an HTML body, a JSON body without the data fields, a network failure or no answer within 20 s), then `/` shall keep every value it shows, show the connection-lost cue with the last data stamp, retry at the next poll, and on a 401 navigate to `/login`; and the page's refresh counter (`body[data-refresh-count]`) shall count each newest request's handled outcome, a rendered body or a handled failure, and never a superseded request.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-2.1 | N6 | a healthy poll was rendered | a poll answers 500 `{"status": "error", "message": "An internal error occurred"}` | the tracked, armed and triggered counts, both section badges, the hero points, the vol row and every card keep their values; the header's engine label reads "Connection Lost" (overview SD-13) and `#hero-data-as-of` reads "connection lost · data as of" followed by the last good stamp |
| S-2.2 | N4 | a healthy poll was rendered | a poll answers 401 `{"error": "unauthenticated"}` | the page navigates to `/login` |
| S-2.3 | N6 | as S-2.1 | a poll answers 503 `{"error": "misconfigured"}` | as S-2.1 |
| S-2.4 | N2 | as S-2.1 | a poll answers 403 with an HTML body | as S-2.1 |
| S-2.5 | N6 | as S-2.1 | the request fails at the network | as S-2.1 |
| S-2.6 | N6 | as S-2.1 | no answer arrives within 20 s | the request is abandoned and the page shows the S-2.1 state; a late answer to it is discarded |
| S-2.7 | N1 | as S-2.1 | a 200 answers with a body that is not JSON | as S-2.1 |
| S-2.8 | B | the page shows the S-2.1 state | the next poll answers with data | every value updates and the cue clears |
| S-2.9 | N3 | a fresh deploy with no state | a poll answers the waiting body with its `notice` | it renders as at the ref; it is data, not an error |
| S-2.10 | N5 | a newer poll succeeded | an older poll's error lands after it | the error is ignored and no cue shows |
| S-2.11 | B | the counter reads N | the newest poll answers with data and renders | the counter reads N+1 |
| S-2.12 | N6 | the counter reads N | the newest poll fails and the failure is handled (the S-2.1 state) | the counter reads N+1 |
| S-2.13 | N5 | the counter reads N and a newer request has been issued | the older request's body or failure lands | the counter still reads N |
| S-2.14 | N6 | the page is open | the operator clicks Force run now and its state refresh answers 500 | the page shows the S-2.1 state |
| S-2.15 | N6 | the server render has drawn the page | the first page-load state request fails | the server-rendered values stay with the S-2.1 cue |

Negative coverage: N1 S-2.7 · N2 S-2.4 · N3 S-2.9 · N4 S-2.2 · N5 S-2.10, S-2.13 · N6 S-2.1, S-2.3, S-2.5, S-2.6, S-2.12, S-2.14, S-2.15 · N7 n/a: the healthy path is unchanged and covered by S-2.8 · N8 n/a: no edit state
Questions: Q-1

### AC-3: The event stream re-opens after it closes, and the $-saved panel refreshes without it
Requirement items: R-1, R-6, R-11, R-12, R-14, R-20, R-23, R-37, R-75
Rule: When the event stream closes or reports an error, `/` shall open a new stream within 30 s and keep retrying, at most 60 s apart, while it fails, with at most one stream open; while no stream is open each 30 s poll shall also refresh the $-saved panel for the picked window; and while a stream is open the panel shall refresh on each event whose `changed` is true.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-3.1 | B | the page's stream is open | a reconnect is answered 502, so the stream is closed | within 30 s the page opens a new `EventSource` to `/api/events` |
| S-3.2 | N6 | every reconnect fails for 5 minutes | the attempts run | the gap between attempts grows and never exceeds 60 s, and at most one stream is ever open |
| S-3.3 | B | no stream is open | a 30 s poll lands | one `GET /api/guard-alpha-summary?window=` with the picked window is sent |
| S-3.4 | B | a stream is open | an event with `changed` true arrives, then one with `changed` false | the panel refreshes once, for the first |
| S-3.5 | N4 | the reconnect is answered 401 | the stream closes | the page navigates to `/login` |
| S-3.6 | B | the daemon restarts with the page open | the new daemon serves | events resume within 90 s of it serving, and the header and values recover with no reload |
| S-3.7 | N5 | two tabs on `/` | one tab's stream closes | only that tab re-opens a stream; the other's is untouched |
| S-3.8 | N6 | a $-saved request fails | the failure lands | the panel keeps its last values |
| S-3.9 | N5 | the operator picks 30d while a 1Y $-saved request is in flight | both land, 1Y last | the panel shows 30d |
| S-3.10 | N7 | the stream is open and healthy | an hour passes | the page opens no second stream, and the 30 s poll interval stays |

Negative coverage: N1 n/a: the stream takes no input · N2 S-3.5 · N3 n/a: an empty panel response is the existing honest empty state · N4 S-3.5 · N5 S-3.7, S-3.9 · N6 S-3.2, S-3.8 · N7 S-3.10 · N8 n/a: no edit state
Questions: none

### AC-4: An age-based stale cue, a catch-up refresh on return, and a true countdown
Requirement items: R-1, R-5, R-6, R-9, R-11, R-13, R-62, R-70, R-89, R-97
Rule: When more than 90 s have passed since `/` last received data, it shall show the not-updating cue; when the tab becomes visible, the page is restored from the back-forward cache or the browser reports it is online again, it shall send exactly one `/api/state` request at once; the NEXT countdown shall show the true seconds to the next `:00` within 1 s, in the server render's format; the header's "Stale" check shall judge the last cycle's age as the server reckons it, never by the browser clock; and the header label shall follow overview SD-13's precedence.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-4.1 | B | the last data arrived 95 s ago | the cue check runs | the header label reads "Not updating" with the tooltip "No data received for 1 min."; `#hero-data-as-of` keeps its stamp and adds " · not updated for 1 min" |
| S-4.2 | B | the tab was hidden for 10 minutes | it becomes visible | exactly one `/api/state` request is sent at once, and the stream is re-opened if it closed (AC-3) |
| S-4.3 | B | the laptop slept for 5 minutes | it wakes and the browser fires `online` or the tab's visibility returns | exactly one request at once |
| S-4.4 | B | the page is restored from the back-forward cache | `pageshow` fires with `persisted` | exactly one request at once |
| S-4.5 | N5 | `visibilitychange` and `online` fire within 1 s | both fire | one request is sent |
| S-4.6 | N7 | a visible tab with data 20 s old | 30 s pass | requests are only the poll's and the events', as before |
| S-4.7 | B | `/api/state` took 4 s | the countdown resyncs | it equals the seconds to the next `:00` within 1 s |
| S-4.8 | B | the server render shows "NEXT 00:42" | the first poll resyncs | the text keeps that form, for example "NEXT 00:38", never "NEXT: 0:38" |
| S-4.9 | N1 | the countdown reaches `:00` | the second passes | it continues from the next minute's count and never sits at "NEXT 00:00" for more than 2 s |
| S-4.10 | N1 | the browser clock is 3 minutes fast | the cue check and the countdown run | the cue appears only when no data arrived for 90 s by the page's own receipt times, and the countdown stays true |
| S-4.11 | N7 | the market is closed | the header renders | the countdown hides as at the ref |
| S-4.12 | N1 | the browser clock is 3 minutes fast and the last cycle completed 30 s ago by the server's clock | a poll lands | the header reads "Live", never "Stale" |
| S-4.13 | N6 | the last data arrived 95 s ago and the current poll fails | the failure lands | the header reads "Connection Lost" (overview SD-13) |
| S-4.14 | B | 15:56 ET in the blackout, and no data for 95 s | the cue check runs | the header reads "Not updating", which outranks "Rebalance pause" (overview SD-13) |
| S-4.15 | N6 | the market is open, data is fresh, and `/api/state`'s `degraded_reasons` reads ["scheduler_stalled"] | a poll lands | the header reads "Degraded" and its tooltip reads "Scheduler stalled: no engine spawn for 2 minutes or more." (the suite approach RC-1's fixed sentence), never the raw key; an unknown reason reads "The daemon reports a degraded state."; with `degraded_reasons` empty the label follows the lower ranks of overview SD-13 |

Negative coverage: N1 S-4.9, S-4.10, S-4.12 · N2 n/a: nothing is refused · N3 n/a: the cue shows exactly when no data arrived · N4 n/a: client-only over authenticated data · N5 S-4.5 · N6 S-4.13, S-4.15 · N7 S-4.6, S-4.11 · N8 n/a: no edit state
Questions: Q-2

### AC-5: After the close and before the open, cards, verdicts and badges match the poll
Requirement items: R-1, R-2, R-3, R-4, R-9, R-73, R-74
Rule: When a poll lands in `closed_frozen` or `pre_market`, `/` shall update every card's status pill, values, section and verdict from the response's `symphonies` list and `guard_alpha` (plan 01 AC-6), and each section badge shall equal the number of cards in its section; a `guard_alpha` that is absent, null, not finite, or 1e21 or more in magnitude (the server render's bound) shall show the dash.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-5.1 | B | the page is open across 16:00 with 6 triggered cards | the first frozen poll lands | each triggered card's verdict reads the verdict for its frozen `guard_alpha`, never the dash |
| S-5.2 | B | the page is loaded after the close | three polls land | each triggered card's verdict text is identical on the render and after each poll |
| S-5.3 | B | the page is loaded after the close | the first poll lands | the Active and Standby badges equal the numbers of cards in those sections |
| S-5.4 | B | 08:00 ET | the first poll lands | each card's status pill and Today, Cumulative and Max DD values equal the poll's `symphonies` entry |
| S-5.5 | N3 | a triggered entry with no `guard_alpha` | a poll lands | its verdict reads the dash |
| S-5.6 | N7 | an account is picked after hours | polls land | other accounts' cards stay hidden (ux-overhaul 05 S-6.8) |
| S-5.7 | N5 | a card's status changes on a poll | it lands | the card moves into its new section at the place the server render's sort order gives it |
| S-5.8 | N6 | a frozen poll fails | it lands | the cards keep their values (AC-2) |
| S-5.9 | N1 | a triggered entry whose `guard_alpha` is 1e21, -1e21 or 1e30 | a poll lands | its verdict reads the dash, as the server render does for the same value |
| S-5.10 | N3 | a triggered entry whose `guard_alpha` key is absent, or null | a poll lands | its verdict reads the dash (this reverses ux-overhaul 05 C2's write-nothing, see Earlier criteria) |

Negative coverage: N1 S-5.9 · N2 n/a: display only · N3 S-5.5, S-5.10 · N4 n/a: `/` is behind the global auth hook, unchanged · N5 S-5.7 · N6 S-5.8 · N7 S-5.6 · N8 n/a: no edit state
Questions: none

### AC-6: Elements fixed at render follow the poll
Requirement items: R-1, R-2, R-3, R-4, R-63, R-67, R-71
Rule: When a poll lands, `/` shall update the mode pill and the hero legend's live or dry-run word from `meta.mode`, each comparison row's winner and loser classes by the rule the server render applies to that row, each card's lifetime Max DD, the insufficient-history labels and the edit-vars count, and the Sleeves and CVaR panels, from data the poll fetches.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-6.1 | B | two tabs on `/` showing DRY RUN | `LIVE_EXECUTION` becomes true and a poll lands in the other tab | that tab's mode pill reads LIVE with its live state and the legend reads "Planet Stopper (live)" |
| S-6.2 | B | the Today row rendered with Bot ahead | a poll makes Held ahead | `winner` is on the held bar and label and `loser` on the bot bar |
| S-6.3 | B | the Ann. Vol row | a poll makes bot's vol lower than held's | bot's bar carries `winner`, as the render's inverted rule gives |
| S-6.4 | N3 | the Max DD row with insufficient history | a poll lands | neither bar carries `winner` or `loser` |
| S-6.5 | N1 | bot equals held on the Today row | a poll lands | the classes equal the server render's for equal values (`templates/index.html:923`) |
| S-6.6 | B | a sleeve rule fires while `/` is open | the next poll lands | that rule's "today" fire count has increased |
| S-6.7 | B | the CVaR diagnostic changes in a cycle | the next poll lands | the CVaR panel shows the new values |
| S-6.8 | B | a card's lifetime Max DD changes | the next poll lands | `[data-field="mdd-lifetime"]` shows it |
| S-6.9 | N6 | the sleeves read fails | a poll lands | the panel keeps its last values |
| S-6.10 | N7 | the Sleeves panel's arm and disarm buttons | the operator uses them | they behave and reload as at the ref |
| S-6.11 | N5 | the Sleeves and CVaR data for two polls land out of order | both land | the older is discarded (overview SD-6) |
| S-6.12 | B | a sleeve rule is added and another removed while `/` is open | the next poll lands | the added rule's row appears and the removed rule's row disappears, without a reload |

Negative coverage: N1 S-6.5 · N2 n/a: display only · N3 S-6.4 · N4 n/a: client-only over authenticated data · N5 S-6.11 · N6 S-6.9 · N7 S-6.10 · N8 n/a: no edit state
Questions: none

### AC-7: Cash Now is disabled on a card that exits
Requirement items: R-1, R-10, R-68
Rule: When a poll reports a symphony triggered, its Cash Now button shall be disabled, and when a poll reports it untriggered, enabled, except while a Cash Now request for it is in flight, and except after one has succeeded in this page load, when the button stays disabled reading "In cash" until a reload; the request itself stays as at the ref.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-7.1 | B | an untriggered card with Cash Now enabled | a poll reports it triggered | its Cash Now button is disabled |
| S-7.2 | B | a triggered card | the next session's first poll reports it untriggered | its Cash Now button is enabled and reads "Cash Now" |
| S-7.3 | N8 | a Cash Now request for the card is in flight | a poll lands | the button stays disabled and reads "Selling…" until the request answers |
| S-7.4 | N7 | any click | the request is sent | its URL, method, headers and body equal the ref's (`static/index.js:409-413`) |
| S-7.5 | N5 | the operator double-clicks | both clicks land | one request is sent, as at the ref |
| S-7.6 | N5 | a Cash Now request for the card succeeded and the button reads "In cash" | a poll reports the symphony untriggered | the button stays disabled and reads "In cash", so no second sell is one click away |

Negative coverage: N1 n/a: no input · N2 n/a: refusal display is plan 04 AC-2 · N3 n/a: a card always has a triggered state · N4 n/a: the route's auth and CSRF hooks are unchanged · N5 S-7.5, S-7.6 · N6 n/a: a failed poll leaves the button as it was (AC-2) · N7 S-7.4 · N8 S-7.3
Questions: none

### AC-8: The detail panel follows polls and shows only its own symphony
Requirement items: R-1, R-3, R-4, R-10, R-23, R-65, R-66, R-89
Rule: While the detail panel is open on symphony S, each poll shall rewrite its live return, stop, peak, alpha and risk-math figures from S's newest state, and the panel shall show only S's chart, logs, variables and risk profile, discarding any response for another symphony or older than the newest request for S.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-8.1 | B | the panel is open on an untriggered symphony showing -0.70% | a poll carries `current_return` 5.55 | `#dp-live-ret` shows +5.55% within that poll |
| S-8.2 | B | the panel is open | a poll changes the stop and the MC probability | the risk-math rows show the new values |
| S-8.3 | N3 | the panel was open on A | the operator opens B, whose `/api/chart` returns no points | the panel shows B's empty state, never A's chart or exit marker |
| S-8.4 | N5 | the operator opens A then B | A's responses land after B's | the panel shows B's chart, logs, variables and profile |
| S-8.5 | N8 | the panel's chart overlays are toggled | a poll lands | the chart is not rebuilt and the toggles stay (ux-overhaul 05 S-3.8) |
| S-8.6 | N3 | the panel is open on S | a poll no longer carries S (the account pick changed) | the panel keeps its last figures and its sub-label reads "not in the current view" |
| S-8.7 | N3 | the operator opens a card before the first poll has landed | the first poll lands | the panel fills from it |
| S-8.8 | N7 | any open | the panel opens | the title is the display name (ux-overhaul 05 AC-8) |
| S-8.9 | N6 | a poll fails | it lands | the panel keeps its figures |

Negative coverage: N1 n/a: the panel takes a symphony id from a card click only · N2 n/a: display only · N3 S-8.3, S-8.6, S-8.7 · N4 n/a: client-only over authenticated data · N5 S-8.4 · N6 S-8.9 · N7 S-8.8 · N8 S-8.5
Questions: Q-3

### AC-9: Sparklines and dials show only the current session's readings
Requirement items: R-1, R-4, R-5, R-7, R-9, R-23, R-69, R-89, R-90
Rule: When `/` draws a card's sparkline or MC dial, `/api/chart/{symphony_id}` shall key its held overlay to the ET trading date, the sparkline shall carry its session's date whenever that session is not today's, and the dial shall show the poll's current `mc_prob`, or the dash when it is null, never an older tape reading; a refresh whose data stamp is unchanged shall send no `/api/chart` request.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-9.1 | B | 20:30 EDT on the UTC host, a symphony with shadow rows for today's ET date | `GET /api/chart/{symphony_id}` | its points carry `held` values |
| S-9.2 | B | 01:00 ET, a tape from the previous session | the sparkline draws | it carries the caption with that session's date, for example "2026-10-21 session" |
| S-9.3 | N3 | an untriggered symphony whose state `mc_prob` is null | the poll and the chart fetch land | the dial reads the dash after both |
| S-9.4 | B | state `mc_prob` 63 and the tape's last non-null reading 58 | both land | the dial reads 63% |
| S-9.5 | N7 | a triggered symphony | the dial renders | it reads the dash (`feature-plans/math-f7.md` AC-2) |
| S-9.6 | N5 | chart responses for two polls land out of order | both land | the older is discarded |
| S-9.7 | N3 | `/api/chart` returns no points | the card renders | "No intraday data yet" as in ux-overhaul 05 S-1.4 |
| S-9.8 | N1 | 09:31 ET, the day's first cycle has written the new tape | the poll lands | the sparkline shows today's tape with no caption |
| S-9.9 | B | 12 cards, and a refresh whose data stamp is new | it lands | one `/api/chart` request per card is sent, and none for a non-symphony key |
| S-9.10 | N5 | a refresh whose data stamp equals the last one's, including the first poll after page load, whose stamp equals the one the page loaded with | it lands | no `/api/chart` request is sent, so the load's chart requests are not repeated |

Negative coverage: N1 S-9.8 · N2 n/a: the chart route refuses nothing · N3 S-9.3, S-9.7 · N4 n/a: the route is behind the global auth hook, unchanged · N5 S-9.6, S-9.10 · N6 n/a: a failed chart fetch is ux-overhaul 05 S-1.5 · N7 S-9.5 · N8 n/a: no edit state
Questions: Q-4

### AC-10: The stored theme applies before first paint and across tabs
Requirement items: R-1, R-72
Rule: When a page that loads `static/tweaks.js` loads with a stored theme, the root element shall carry that theme before the body's first paint, and when the theme changes in one tab, every other open tab of the app shall apply it without a reload.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-10.1 | B | a stored dark theme | `/` loads | the root element has `data-theme` "dark" before the body's first paint |
| S-10.2 | B | as S-10.1 | `/history`, `/performance`, `/ai-advisor` and `/settings` load | each root has `data-theme` "dark" before first paint |
| S-10.3 | B | two open tabs | the theme changes to dark in one | the other applies dark without a reload, and its charts recolour (ux-overhaul 05 AC-5) |
| S-10.4 | N3 | no stored theme | a page loads | the default theme applies, as at the ref |
| S-10.5 | N1 | a stored value that is not valid JSON | a page loads | the default theme applies and no error stops the page |
| S-10.6 | N7 | the number-format setting changes in one tab | the other tab's next poll lands | its numbers use the new format |

Negative coverage: N1 S-10.5 · N2 n/a: nothing is refused · N3 S-10.4 · N4 n/a: client-only · N5 n/a: the last change wins, as ux-overhaul 05 S-5.4 pins · N6 n/a: no request · N7 S-10.6 · N8 n/a: the tweaks panel holds no unsaved state
Questions: none

## Rule-by-Entry-Point Matrix
| Rule | 30 s poll | Cycle event | Window picker click | Account pick | Tab return or `online` | Stream re-open |
|---|---|---|---|---|---|---|
| AC-1 picked window kept | S-1.1, S-1.5 | S-1.2 | S-1.7 | n/a: the pick refreshes cards only (ux-overhaul 05 AC-6), and its poll is the 30 s poll's case | n/a: the catch-up request is a poll, covered by S-1.1 | n/a: re-opening fetches no strip |
| AC-2 errors never drawn | S-2.1 | n/a: the event carries no data; its refetch is a poll | n/a: the click fetches no `/api/state` | n/a: its request is a poll | n/a: its request is a poll | n/a: the stream carries no state; its 401 is AC-3's S-3.5 |
| AC-3 panel refresh | S-3.3 | S-3.4 | S-3.9 | n/a: the panel is portfolio-wide | n/a: the catch-up is a poll, covered by S-3.3 | S-3.1 |
| AC-5 frozen cards match | S-5.1 | S-5.1 | n/a: the click changes no card | S-5.6 | n/a: a poll | n/a: no card data |
| AC-8 panel follows polls | S-8.1 | S-8.1 | n/a: the click changes no panel | S-8.6 | n/a: a poll | n/a: no panel data |

| Rule | First page-load state request | Force run button | Detail panel open |
|---|---|---|---|
| AC-1 picked window kept | S-1.4, S-1.13 | n/a: its refresh is a poll, covered by S-1.1 | n/a: opening the panel changes no window |
| AC-2 errors never drawn | S-2.15 | S-2.14 | n/a: the panel reads `/api/chart`, `/api/logs` and `/api/settings`, not `/api/state`; its failures are AC-8's S-8.9 |
| AC-5 frozen cards match | S-5.2 | n/a: its refresh is a poll, covered by S-5.1 | n/a: opening the panel changes no card |
| AC-8 panel follows polls | S-8.7 | n/a: its refresh is a poll, covered by S-8.1 | S-8.3, S-8.4 |

## Not Building
Non-goals:
- NB-1: A new visual design for any cue; every cue uses existing text, the header label and the `.legend-as-of` style (overview NB-2).
- NB-4: Changing the 30 s poll interval (approved by the PM, [PM-ASSUMED]).
- NB-6: Fixing Cash Now's request so it can succeed: it posts `{symphony_id}` to a route that requires `confirm_account_id` (`static/index.js:409-413`, `app.py:5455-5456`); the request may not change in this suite (overview NB-6), so it is a sensitive follow-up plan; plan 04 AC-2 shows the refusal (approved by the PM, [PM-ASSUMED]).

Behaviour that stays exactly as it is:
- NB-2: Every trade and liquidation request, Cash Now's included (overview NB-6).
- NB-5: The hero chart's window, the sparklines' in-place updates, the first-paint verdicts and dials, the banners, the account filter and the detail title, as ux-overhaul 05 delivers them.

Deferred:
- NB-3: The newest-request guard on `loadState` (TA-11) stays ux-overhaul 05 C3 round 2's; the other covered findings stay with their plans (overview NB-12).

Protected surfaces consumed, not changed:
- NB-7: `POST /api/sell_account`, the Managed Sleeves arming routes and `LIVE_EXECUTION` (overview NB-14).

## Defect Classes
| DC-n | Class (what goes wrong) | Trigger (B or N1-N8) | Sibling search (tool, query, scope) | Scenarios | Raised in review as |
|---|---|---|---|---|---|
| DC-1 | One element written by two refresh paths on different windows or bases (overview DC-1) | N5 | grep each id written by `renderGuardAlpha`, `updateComparisonRows`, `renderMcDial` and `updateCards` in `static/index.js` for a second writer | S-1.1, S-1.5, S-9.4 | - |
| DC-2 | A response drawn without a status or shape check (overview DC-4) | N6 | grep `return r.json()` in `static/index.js` and `templates/index.html` | S-2.1, S-2.4, S-2.7 | - |
| DC-3 | A response applied without a newest-issued guard (overview DC-5) | N5 | grep `fetch(` in `static/index.js`, `static/chrome.js` and `templates/index.html` | S-1.7, S-3.9, S-8.4, S-9.6 | - |
| DC-4 | A server-rendered element no poll updates (overview DC-6) | B | read: every `data-testid` and `data-field` in `templates/index.html` and `templates/_chrome.html` against the writers in `static/index.js` and `static/chrome.js` | S-6.1, S-6.2, S-6.8, S-7.1 | - |
| DC-5 | A refresh path that never re-arms after the server goes away | N6 | grep `EventSource`, `onerror`, `setInterval` and `visibilitychange` in `static/` | S-3.1, S-4.2 | - |
| DC-6 | A host-local date where the ET trading date is meant (overview DC-3) | N1 | grep `datetime.now()` and `.strftime("%Y-%m-%d")` in the `/api/chart` route and its helpers in `app.py` | S-9.1 | - |
| DC-8 | A new read path that can reach an order path (the Sleeves panel builder reads `sleeves.ledger`, the package holding the Managed Sleeves order path, `app.py:1475`) | N7 | extend the transitive call-graph walk of `tests/security/test_retirement_action_no_trade_boundary.py` (its Group E pattern) to the AC-6 read, checking for `sell_account`, the sleeves order functions, `composer_draft_client`, `save_symphony` and `LIVE_EXECUTION` writes; prove it non-vacuous by injecting a call to one of them into the read and seeing the scan fail | S-6.6, S-6.7 | - |
| DC-7 | A client timer or age check that trusts the browser clock against a server time | N1 | grep `Date.now()` and `new Date(` beside server time fields in `static/chrome.js` and `static/index.js` | S-4.10 | - |

## Worked Example
At 10:15 ET the operator picks 1Y: the headline reads +4.40% under "1Y" and the vol row reads "Bot 19.1% / Held 21.6% / α +2.5pp". The 10:15:30 poll carries the 30d guard alpha +0.68% and an unchanged data stamp: the headline, label and vol row keep their 1Y values and no strip request is sent; the 10:16 cycle event reports `changed` true, so one `/api/strip/1y` request refreshes the headline (S-1.1, S-1.5, S-1.12, S-1.11). At 10:20 the daemon restarts: the stream's reconnect gets a 502 and closes; within 30 s the page opens a new stream, each poll in between also refreshes the $-saved panel, and events resume once the daemon serves (S-3.1, S-3.3, S-3.6).

Failure case: the session lapses and the 10:30 poll answers 401 `{"error": "unauthenticated"}`. The page navigates to `/login` instead of drawing zeros and "Closed" (S-2.2).

## Contract Changes and Consumers
- `/api/chart/{symphony_id}`: the held overlay keys to the ET trading date (`app.py:4852`), and the response adds `et_date` (today's ET date) beside the tape's session date, so the client can caption a previous session's tape. Additive; consumers: `static/index.js` `loadCharts` and `fetchDetailChart`.
- `/api/state` adds `dashboard_panels` (the Sleeves rows, the CVaR diagnostic and the locked-variables count, from the data `dashboard()` already builds at `app.py:1464` and `:1808`), and every `symphonies` entry adds `mdd_lifetime`, plan 01's frozen builder included. Additive; consumer: `static/index.js`.
- `/api/state` adds `degraded_reasons`, the list plan 02's `_health_status()` gives `/health` (empty when healthy). Additive; consumer: the header label in `static/chrome.js` (S-4.15).
- `/` reads the event's data (plan 02 AC-1) and plan 01's frozen `symphonies` and `guard_alpha`.

## Security Considerations
- Input validation and injection: new text (captions, cues, labels) renders through `textContent`; ids from a payload never build a selector by string concatenation (overview DC-7 of the UX suite).
- Authorization and access control: a 401 now leads to `/login` (S-2.2, S-3.5); no route's guard changes.
- Data exposure: nothing new leaves the server beyond what `dashboard()` already renders for the same session.
- Client-side rendering: no `innerHTML` with response data is added.
- No-trade boundary: AC-6's Sleeves and CVaR read is read-only; DC-8's transitive scan proves it reaches no order, liquidation or symphony-creation call.
- Abuse or rate limits: the re-opened stream backs off to 60 s (S-3.2); a catch-up is one request (S-4.5); a strip request is sent only when data changed (S-1.12).
- Money (Sensitive): AC-7 changes Cash Now's enabled state only; its request is byte-identical (S-7.4), it never re-sends, and an in-flight request keeps the button disabled (S-7.3).

## Design-System Mapping
| Component | Element | Required primitive or token | Notes |
|---|---|---|---|
| Hero legend | not-updating suffix and connection-lost stamp | existing `.legend-as-of` | text only |
| Hero chart | "No data yet for" plus the picked window's label (S-1.14) | the existing `.legend-as-of` style, beside `#hero-chart-empty` | text only |
| Header | "Not updating" label | the existing `#engine-status-label` with `--studio-warn`, as "Stale" uses | text and tooltip |
| Card sparkline | session date caption | `--studio-ink-dim` text in the existing 64 px slot | as ux-overhaul 05's empty-state text |
| Comparison bars | `winner` and `loser` | the existing `.vs-bar-fill` and `.vs-bar-label` classes | classes only |
| Detail panel | "not in the current view" sub-label | the existing `#dp-live-ret-sub` style | text only |

## Definition of Done (Verification)
| AC | Scenarios | Test level | Real-entrypoint test |
|---|---|---|---|
| AC-1 | S-1.1, S-1.2, S-1.3, S-1.4, S-1.5, S-1.6, S-1.7, S-1.8, S-1.9, S-1.10, S-1.11, S-1.12, S-1.13, S-1.14 | the refresh path under node with captured `/api/state` and `/api/strip` payloads | live `/` with 1Y picked across three refreshes |
| AC-2 | S-2.1, S-2.2, S-2.3, S-2.4, S-2.5, S-2.6, S-2.7, S-2.8, S-2.9, S-2.10, S-2.11, S-2.12, S-2.13, S-2.14, S-2.15 | `loadState` under node with each error body | live `/` with the daemon stopped for one poll |
| AC-3 | S-3.1, S-3.2, S-3.3, S-3.4, S-3.5, S-3.6, S-3.7, S-3.8, S-3.9, S-3.10 | the stream handler under node with a recording `EventSource` stub | a browser on the droplet across a daemon restart, DevTools open (`browser` marker) |
| AC-4 | S-4.1, S-4.2, S-4.3, S-4.4, S-4.5, S-4.6, S-4.7, S-4.8, S-4.9, S-4.10, S-4.11, S-4.12, S-4.13, S-4.14, S-4.15 | handlers under node with a fake clock | a browser tab hidden for 10 minutes on the droplet |
| AC-5 | S-5.1, S-5.2, S-5.3, S-5.4, S-5.5, S-5.6, S-5.7, S-5.8, S-5.9, S-5.10 | `updateCards`, `updateVerdicts` and `updateSectionMeta` under node with captured frozen payloads | live `/` after 16:00 |
| AC-6 | S-6.1, S-6.2, S-6.3, S-6.4, S-6.5, S-6.6, S-6.7, S-6.8, S-6.9, S-6.10, S-6.11, S-6.12 | renderers under node; the panel data source with the Flask test client | live `/` in two tabs with a settings change |
| AC-7 | S-7.1, S-7.2, S-7.3, S-7.4, S-7.5, S-7.6 | `updateCards` and `cashNow` under node | live `/` when a symphony exits |
| AC-8 | S-8.1, S-8.2, S-8.3, S-8.4, S-8.5, S-8.6, S-8.7, S-8.8, S-8.9 | `openDetailPanel` and its fetches under node | live `/` with the panel open across two cycles |
| AC-9 | S-9.1, S-9.2, S-9.3, S-9.4, S-9.5, S-9.6, S-9.7, S-9.8, S-9.9, S-9.10 | `/api/chart` route test with a frozen UTC clock; `renderMcDial` under node | `/api/chart` on the droplet at 20:30 EDT |
| AC-10 | S-10.1, S-10.2, S-10.3, S-10.4, S-10.5, S-10.6 | a browser test of each page's head order (`browser` marker); the storage handler under node | two browser tabs on the droplet |

Gates: `ruff format --check`; `ruff check`; a scoped pytest run of `tests/dashboard`, `tests/app`, `tests/realtime_push`, `tests/ui`, `tests/js_syntax` and `tests/security`, at most 4 workers, never `-n auto`; DC-8's extended no-trade scan, mutation-tested once; CI green on the exact SHA; `/review`; then the PM's live functional check on the droplet of T-1 to T-3, because tests-green is necessary and never sufficient.
The no-trade boundary scan of DC-8 covers the AC-6 read path and is mutation-tested once. A node harness or a recording stub counts calls only; a claim that an element is visibly drawn or a theme visibly applied is made on a live render.
Fixture provenance (SD-7): `/api/state` (open and frozen), `/api/strip`, `/api/guard-alpha-summary` and `/api/chart` payloads are captured from the production state database copy the audit used, one per case; the error bodies are the route's own literal responses (`app.py:217`, `:236`, `:3513`); a case no capture holds is derived from a captured record with one field changed, signed off by the PM.
Screenshots: `/` at 1440x900 and 375x812, light and dark, after three refreshes, with the cues of S-2.1 and S-4.1 shown once each.

## Known-Unconfirmed and Standing Risk
- Whether a production restart closes the event stream for good is unreproduced: it needs Caddy and a restart. Read-only check: restart the daemon with `/` open and watch DevTools for a second `/api/events` request.
- Background-tab timer throttling differs by browser; AC-4's catch-up does not depend on it.
- The node harness stubs the DOM, `Chart` and `EventSource`; every AC's live check is the proof.
- The droplet's `/api/state` time is unmeasured (6.11 s open on the audit's copy, 2 vCPU shared with the engine on the droplet); if it exceeds Q-1's 20 s, every poll would count as failed. Plan 05 S-6.1 measures it before this plan's live check.

## Open Questions
| Q-n | Question | Blocks (AC ids or "plan") | Proposed default | Answer |
|---|---|---|---|---|
| Q-1 | How long may `/api/state` take before the page counts it as failed? | AC-2 | 20 s, over three times the slowest measured response (6.11 s) | default approved by the PM, [PM-ASSUMED] |
| Q-2 | What do the not-updating cues read? | AC-4 | The header label "Not updating" with the tooltip "No data received for N min."; the as-of adds " · not updated for N min", N in whole minutes | default approved by the PM, [PM-ASSUMED] |
| Q-3 | What does the detail panel do when its symphony leaves the current view? | AC-8 | It keeps its last figures and its sub-label reads "not in the current view" | default approved by the PM, [PM-ASSUMED] |
| Q-4 | What does a card's sparkline show between midnight and the day's first cycle? | AC-9 | The previous session's tape with a caption naming its date | default approved by the PM, [PM-ASSUMED] |
| Q-5 | No `.claude/workflows/` file exists for `/` (plans 01, 02 and 03). Accept "no workflow file" and the User Tasks written here? | AC-1 | Yes, until the operator confirms a workflow file | default approved by the PM, [PM-ASSUMED] |

## Decisions
| ID | Decision | Rationale |
|---|---|---|
| DEC-1 | A refresh's data is "changed" when its data stamp (plan 01 AC-4) differs from the last one's | One rule decides the strip refresh here and mirrors ux-overhaul 05 PM-3 for the hero |
| DEC-2 | The connection-lost cue keeps the last data stamp | "connection lost" alone hides how old the shown values are |
| PM-1 | Q-2's copy uses " · not updated for N min", with the plan's middle dot | PM amendment from the Gate-2 approach, [PM-ASSUMED] |
| PM-2 | The refresh counter counts the newest request's handled outcome, rendered or failed, never a superseded one (S-2.11 to S-2.13) | PM amendment, [PM-ASSUMED] |
| PM-3 | A picked window with no data keeps the previous series with a no-data-yet cue (S-1.14) | PM amendment from the ux-overhaul 05 C1 closer's finding, [PM-ASSUMED] |
| PM-4 | The duplicate `/api/chart` request at load folds into AC-9 (S-9.10), and the client verdict uses the server's 1e21 bound (S-5.9) | PM amendment, as the architect folded them, [PM-ASSUMED] |
| PM-5 | The contract adds `mdd_lifetime`, `dashboard_panels` and `et_date`, all additive; the matrix adds the first page-load request, Force run and the detail panel | PM amendment from the Gate-2 approach, [PM-ASSUMED] |
| PM-6 | The header label gains "Degraded" at rank 3 of overview SD-13, set only by `degraded_reasons` (S-4.15); `/api/state` carries `degraded_reasons` from plan 02's `_health_status()` | PM amendment from `feature-plans/refresh-timing/approach/00-suite.md`, [PM-ASSUMED] |
| PM-7 | The mode pill and hero legend are written by `updateModeIndicators(meta)` in `static/chrome.js`, which `static/index.js` calls on `/`, so the page has one writer (AC-6) | PM amendment from `feature-plans/refresh-timing/approach/00-suite.md`, [PM-ASSUMED] |
| PM-8 | The S-1.14 cue uses the existing `.legend-as-of` style beside `#hero-chart-empty` | PM amendment, [PM-ASSUMED] |
| PM-9 | The "Degraded" tooltip gives RC-1's fixed plain sentence for each reason and never shows the raw key (`scheduler_stalled`, `engine_overrun` and the rest) (S-4.15) | PM final ruling, `feature-plans/refresh-timing/approach/00-suite.md` RC-1, [PM-ASSUMED] |

## Docs to Update
- `docs/generated/static_index_js.md`, `docs/generated/app.md` (`/api/chart`), `docs/generated/INDEX.md`.
- A `DECISIONS.md` entry appended at ship by the doc writer.
- A drafted `.claude/CLAUDE.md` Key Files row update for `static/index.js`, for PM approval.

## Follow-Ups
- NB-6: Cash Now's request cannot pass the route's confirmation checks; a sensitive plan of its own.
