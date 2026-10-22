# Feature: Dashboard charts, verdicts and banners hold through every refresh
Status: planned
Story: none: operator chat request on 2026-10-22, saved verbatim in feature-plans/ux-overhaul/00-overview.source.md
Type: fix
Suite: ux-overhaul 05
Repos: PlanetStopper
Base: main
Ref: PlanetStopper@37c8e983b64c6980c28199a3f9318486b60f3864
Sensitive: none: the dashboard's charts, verdict line, MC dial, banners and detail-panel title are display; the fleet-alert dismiss writes only the alert's dismissed time, and no trade, liquidation, settings or credential path changes
Depends on: the model-unpin plan (branch `model-unpin/fixtures`) merged into this plan's tip before its PR, because both edit `app.py`; this plan's three cycles assemble on `ux-overhaul/05`, plan 02's base

## Trigger
The operator named the dashboard `/` the only page that looks right (G-5), and the app-wide UX audit at the ref saw its hero chart and every card sparkline blank in later captures (audit D1, decision D-5). The PM ruled that this plan follows the read-only diagnosis run for it (G-21 to G-23, G-28); that report exists (a review artifact outside the tracked tree, dated 2026-10-22, measured on a snapshot copy of the production database at the ref), and this plan carries its findings and numbers here.

Diagnosis findings, each confirmed against the code at the ref:
1. Root cause of the audit symptom: first paint waits on a serial chain. The server render of `/` (`app.py:1665-1675`) took 3.48 s, then `loadState()` runs only at DOMContentLoaded (`static/index.js:1590-1593`) and `GET /api/state` took 6.11 s for a 3,879,395 byte body; only then does `updateDashboard` draw the hero (`static/index.js:1389`, `:84-145`) and start one `GET /api/chart/` request per symphony (12) for the sparklines (`static/index.js:349-364`, `:1395`). A simulated double navigation put the first JavaScript-drawn frame about 12 s after navigation. The audit's blank captures were frames taken before that, which is why a later probe found 30 hero points and 311 sparkline points.
2. The server-rendered placeholders look final: `dashboard()` never injects `guard_alpha` (its only caller is `get_state()`, `app.py:3407-3421`), so every triggered card renders a dash verdict (`templates/index.html:1245-1257`, 11 of 12 cards in the audit copy) until a poll lands, and the MC dial is hard-coded "0%" with an empty arc (`templates/index.html:1258-1265`). The client then writes a verdict without the server's "at exit" suffix (`static/index.js:380-389`).
3. One degraded poll empties a healthy hero chart: `renderHeroChart` overwrites the stored series and re-slices even when the poll's `hist_*` lists are empty (`static/index.js:84-100`; `_build_meta` defaults them to empty lists, `app.py:1937-1938`).
4. Every poll and every cycle event destroys and rebuilds all 12 sparklines and replays Chart.js's default animation (`static/index.js:283`, `:330`).
5. A poll re-slices the hero by row count (`dates.slice(-n)`, `static/index.js:75-76`, 1Y as 365 rows) while a picker click loads the server's calendar series from `/api/hero-chart/` with the window token (`static/index.js:1638-1650`), so after any poll the hero can switch series under the same 1Y label.
6. Chart colours are read once at creation (`static/index.js:112`, `:122`, `:288`, `:298`), and `static/tweaks.js:44` only emits `tweakchange`, so a theme or accent change leaves the hero's colours stale until reload.
7. `window.loadState` is read by `static/chrome.js:25` and `:63` but never assigned (`loadState` is private to the `static/index.js` IIFE, `:1469`), so the workspace switcher never refreshes the dashboard; `loadState` also never sends the `account` filter `/api/state` accepts (`app.py:3146-3153`).
8. `updateBanners(data)` (`templates/index.html:1782-1823`) is defined and never called. Both banners are `hidden` in the server render (`templates/index.html:766`, `:800`) and `updateBanners` is their only un-hider, so neither banner has ever appeared on `/`: the fleet-correlation alert the engine raises (`app.py:3401-3405`, dismissed alerts already excluded) never reaches the operator, although earlier plans require it as a high-visibility banner, and the fleet banner's Dismiss button cannot be reached. Called as written, `updateBanners` would also show the market-state banner on every poll in both branches (`templates/index.html:1813-1822`), a permanent strip duplicating the header's engine status.

Found during this spec's investigation: the fleet banner's Dismiss button posts `/api/fleet-alert/dismiss` without the CSRF header every other dashboard POST sends (`templates/index.html:773`), so the global CSRF hook refuses it (`app.py:490-493`) while the button hides the banner anyway, and the dismissal is never recorded (`app.py:4913-4928`).

## Summary
The operator sees the dashboard's hero chart, card sparklines, verdict lines and MC dials drawn with true values from first paint and through every refresh, in the selected window and the current theme, with live banners and a detail panel titled by the symphony's name.
- The hero chart and sparklines draw without waiting for `/api/state` (AC-1).
- First-paint verdicts and dials match what a refresh shows (AC-2).
- A refresh never empties, rebuilds or re-animates a drawn chart (AC-3).
- After a refresh the hero shows the selected calendar window (AC-4).
- A theme or accent change recolours the charts without a reload (AC-5).
- Picking an account reloads the dashboard for that account (AC-6).
- The fleet-correlation banner shows when an alert is active, and Dismiss is recorded (AC-7).
- The detail panel title is the display name (AC-8).

## Requirements as Given
[G-3] holy fuck this looks like shit, you've done literally 0 UX screenshot checking. 00-overview.source.md:8
[G-4] Your whole job for the rest of the day is an overhaul of the entire application, ensuring it fucking looks right on top of functions properly. 00-overview.source.md:8
[G-5] The only piece of this application that even looks slightly good is the fucking dashboard. 00-overview.source.md:8
[G-7] PM Step-2 answers ([PM-ASSUMED]; the operator is not available for questions; project directive: record assumptions, do not stop) 00-overview.source.md:11
[G-8] Building: (1) every page and tab of the dashboard app renders correctly, honestly and consistently with the main dashboard `/`, which is the operator's reference standard, and every control on it works. 00-overview.source.md:13
[G-9] (2) Users: the operator, through `/`, `/history`, `/performance`, `/settings` (+ the per-symphony settings modal), `/login`, `/ai-advisor` (tabs Overview, Correlations, Asset Swaps, Logic Changes, Chat, Strategy Builder, Frontrunner Builder) and the shared header/nav. 00-overview.source.md:14
[G-10] (3) Flows: as the audit records per surface; the workflow files under `.claude/workflows/` if any exist. 00-overview.source.md:15
[G-11] (4) Negatives: per the audit (empty states, unverifiable states, unavailable data, narrow widths, dark theme). 00-overview.source.md:16
[G-21] 05-dashboard-charts: the hero chart and card sparklines blank after the client refresh (audit D-5). 00-overview.source.md:28
[G-22] A separate read-only diagnosis is running now; its report will be at .claude/review/37c8e983b64c/dashboard-charts-diagnosis.md. 00-overview.source.md:28
[G-23] Write plan 05 from it if it exists when you reach 05; otherwise write the criteria on the observable behavior and list the cause under Known-Unconfirmed. 00-overview.source.md:28
[G-24] PM rulings on the audit's decisions (record as decisions in the overview; each a default the operator may override) 00-overview.source.md:30
[G-28] D-5 per diagnosis; 00-overview.source.md:35
[G-32] D-9 dashboard conventions everywhere: Max DD as a positive magnitude labelled Max DD, 1Y = 365 calendar days everywhere, 30d default window on every page; 00-overview.source.md:39

## Requirement Inventory
These are the overview's R ids. Overview items R-21, R-70 and R-71 (plan numbering, the Base chain, landing after model-unpin) are met by this plan's Suite, Base and Depends on headers.

| R-n | Requirement item | Source (G ids) | Maps to (AC ids, or NB-n) | Scope (full, or narrowed: what is left out) |
|---|---|---|---|---|
| R-4 | Every screen this plan changes is checked by rendered screenshots before it ships | G-3 | AC-1, AC-2, AC-3, AC-4, AC-5, AC-7, AC-8 | full |
| R-5 | Every page and tab looks right | G-4, G-8 | AC-1, AC-2, AC-5 | narrowed: this plan's part is the dashboard's charts, verdicts, dials and banners |
| R-6 | Every control on every page functions | G-4, G-8 | AC-6, AC-7 | narrowed: this plan's part is the workspace switcher's refresh on `/` and the fleet banner's Dismiss; Force run's honesty is plan 04's |
| R-7 | The dashboard `/` is the reference standard and keeps its look | G-5, G-8 | AC-1, AC-2, AC-3, AC-8, NB-1 | narrowed: `/` changes only its charts' loading and refresh, its first-paint verdict and dial, its account refresh, its detail-panel title, and the fleet-correlation banner, which earlier plans require and the operator has never seen because it never showed |
| R-9 | Open questions are answered by recorded defaults, never by stopping | G-7 | AC-1, AC-6 | full |
| R-10 | Every page and tab renders honestly | G-8 | AC-2, AC-7, AC-8 | narrowed: the dashboard only |
| R-11 | The overhaul covers every page, the modal, the Advisor tabs and the header | G-9 | AC-1, AC-6 | narrowed: the dashboard `/` only |
| R-12 | Flows follow the audit per surface, and workflow files where they exist | G-10 | AC-1, AC-4 | full |
| R-13 | Empty, unverifiable and unavailable states are honest on every surface this plan touches | G-11 | AC-1, AC-2, AC-3 | full |
| R-15 | Every page renders in the dark theme | G-11 | AC-5 | narrowed: this plan's part is chart colours following a theme change; login and the token decisions are plan 04's and Claude Design's |
| R-39 | Internal lowercase keys are replaced by display names | G-10 | AC-8 | narrowed: the dashboard detail-panel title only |
| R-66 | The hero chart and card sparklines stay drawn after the client refresh | G-21, G-28 | AC-1, AC-3 | full |
| R-67 | Plan 05 is written from the diagnosis report when it exists | G-22, G-23 | AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-7 | full |
| R-68 | The card verdict line and MC dial agree between first paint and refresh | G-10 | AC-2 | full |
| R-69 | The PM's rulings are recorded as decisions, each a default the operator may override | G-24 | AC-4 | full |
| R-72 | The dashboard hero chart covers the selected calendar window after every refresh | G-32 | AC-4 | full |

## User Tasks
Workflow (Dashboard `/`): no workflow file: this repository has no `.claude/workflows/` directory at the ref.

### T-1: Open the dashboard and read the charts
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Open `/` | a portfolio with history and triggered symphonies | The hero chart and each card's sparkline draw without waiting for the state poll; triggered cards show their verdict and a dash dial | S-1.1, S-2.1, S-2.4 |
| 2 | Leave the page open through several polls and a cycle event | none | Charts stay drawn without flicker; values update in place | S-3.2, S-3.4 |

### T-2: Change the window and the theme
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Click 1Y | none | The hero shows the last 365 calendar days and keeps them through the next poll | S-4.1 |
| 2 | Switch the theme to dark in the tweaks panel | none | The charts redraw in the dark theme's colours | S-5.1 |

### T-3: Switch account, handle the fleet alert, open a symphony
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Pick an account in the workspace switcher | two accounts | The dashboard reloads with that account's symphonies | S-6.1 |
| 2 | Read and dismiss the fleet banner | an active alert | The banner shows on load with the alert; Dismiss hides it and it stays hidden after a reload | S-7.1, S-7.4 |
| 3 | Click a card to open its detail panel | a live symphony | The panel title is the card's display name | S-8.1 |

## Earlier criteria on the same surface
| Earlier criterion (plan and AC) | Surface | Disposition (kept, changed or contradicted) | Why |
|---|---|---|---|
| `feature-plans/dashboard-realtime-push.md` AC-3 (the client updates on a cycle-complete event) and AC-5 (the poll is the fallback when the event stream drops) | Dashboard refresh | kept | Both refresh paths stay; AC-3 here changes how a refresh updates charts, not when |
| `feature-plans/math-r0.md` AC-5 (one calendar window per picker click; the hero's trading-day slicing converts from the calendar cutoff) | Hero chart window | changed | Shipped for the picker click only; a poll still re-slices by row count (diagnosis finding 5); AC-4 extends it to every refresh |
| `feature-plans/fix-display-cluster.md` AC-5 (hero y-axis visible, series unchanged) | Hero chart | kept | The axis stays; the series is the selected window's |
| `feature-plans/math-f7.md` AC-2 (the MC dial renders an explicit exited dash for triggered symphonies, never 0) | MC dial | kept | Shipped for the client path; AC-2 here applies it to the server render, which still hard-codes "0%" |
| `feature-plans/engine-correctness-remediation.completed.md` AC-V3.1 (a fleet correlation alert surfaces a high-visibility dashboard banner) | Fleet banner | kept | It never showed at the ref (Trigger item 8); AC-7 makes it show |
| `feature-plans/vwap-remediation.completed.md` AC-4.1 (the banner copy "Fleet-correlated trigger event detected: ... review before acting") | Fleet banner copy | changed | The code at the ref writes a later copy ("Concentrated risk", the tripped and active counts, the reason and the trip time, `templates/index.html:1789-1798`); AC-7 keeps the copy at the ref and does not restore AC-4.1's |
| `feature-plans/dm-dashboard-market-mode.completed.md` (market-state display on the dashboard) | Market-state banner | kept | The market state stays in the header's engine status; the market-state banner stays hidden (NB-8) |
| `feature-plans/guard-alpha-saved-coherence.md` (the hero window picker drives the dollar-saved panel) | Dashboard window picker | kept | AC-4 does not change the picker's requests |

## Acceptance Criteria (Building)
### AC-1: The hero chart and sparklines draw without waiting for the state poll
Requirement items: R-4, R-5, R-7, R-9, R-11, R-12, R-13, R-66, R-67
Rule: When the operator opens `/`, the dashboard shall draw the hero chart from the series the server render already holds and start each card's sparkline request at load, without waiting for the `/api/state` response.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-1.1 | B | a portfolio history whose embedded 30d series (the default window) holds N daily points, and `/api/state` held unanswered | the operator opens `/` | the hero chart holds N points in each of its two lines and every card canvas with intraday data holds a chart, while `/api/state` is still pending |
| S-1.2 | N6 | `/api/state` fails | the operator opens `/` | the hero and sparklines are drawn as in S-1.1 and the existing "connection lost" freshness text shows |
| S-1.3 | N3 | the server render holds no portfolio history | the operator opens `/` | the hero area shows the text "No portfolio history to chart yet" instead of a blank canvas |
| S-1.4 | N3 | one symphony's `/api/chart` returns no data | the page loads | that card's sparkline area reads "No intraday data yet"; other cards draw |
| S-1.5 | N6 | one symphony's `/api/chart` request fails | the page loads | that card's sparkline area reads "Chart unavailable"; other cards draw |
| S-1.6 | N1 | a portfolio history of exactly one point | the operator opens `/` | the hero chart draws that point without an error |
| S-1.7 | N7 | the page has loaded | 30 s pass and a cycle-complete event arrives | the poll and the event each request `/api/state` once, as at the ref |
| S-1.8 | N5 | the operator navigates to `/` twice in quick succession | the second load completes | its hero and sparklines draw as in S-1.1 |
| S-1.9 | B | the page has loaded | each chart is constructed | each plays Chart.js's entry animation as at the ref, and the page signals readiness only after every chart's entry animation has completed |

Negative coverage: N1 S-1.6 · N2 n/a: nothing on first paint is refused · N3 S-1.3, S-1.4 · N4 n/a: `/` and `/api/chart` are behind the global auth hook, unchanged · N5 S-1.8 · N6 S-1.2, S-1.5 · N7 S-1.7 · N8 n/a: first paint holds no edit state
Questions: Q-1

### AC-2: First-paint verdicts and dials match what a refresh shows
Requirement items: R-4, R-5, R-7, R-10, R-13, R-67, R-68
Rule: When the server renders a triggered symphony's card, its verdict line shall read the same text a refresh writes for the symphony's guard alpha, and its MC dial shall read the dash with an empty arc, never "0%", whenever the symphony is triggered or its MC reading is unknown.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-2.1 | B | a triggered symphony whose guard alpha is negative | the operator opens `/` | the server-rendered verdict reads "Early exit · gave up", the magnitude to one decimal, "%α" and "at exit"; after the first poll the text is identical |
| S-2.2 | B | a triggered symphony whose guard alpha is positive | the operator opens `/` | the verdict reads "Good call · saved +", the value to one decimal, "%α" and "at exit" on both paths |
| S-2.3 | N1 | a triggered symphony whose guard alpha is exactly 0 | the operator opens `/` | both paths read "Early exit · gave up 0.0%α at exit" |
| S-2.4 | B | a triggered symphony | the operator opens `/` | the dial reads the dash with an empty arc, and stays so after a poll |
| S-2.5 | N3 | a triggered symphony with no guard-alpha row | the page loads and polls | the verdict reads the dash on both paths |
| S-2.6 | N1 | an untriggered symphony whose MC reading is 0 | the page polls | the dial reads "0%" |
| S-2.7 | N3 | an untriggered symphony before its first MC reading is known | the operator opens `/` | the dial reads the dash, not "0%" |
| S-2.8 | N6 | `/api/state` fails | the page loads | the server-rendered verdict stays; it is not replaced by a dash |
| S-2.9 | N7 | an untriggered symphony | the page renders and polls | its verdict line stays hidden, as at the ref |
| S-2.10 | N5 | the page is open | three polls land | each triggered card's verdict text stays the same and never flashes the dash |

Negative coverage: N1 S-2.3, S-2.6 · N2 n/a: display only · N3 S-2.5, S-2.7 · N4 n/a: `/` is behind the global auth hook, unchanged · N5 S-2.10 · N6 S-2.8 · N7 S-2.9 · N8 n/a: the card holds no edit state
Questions: none

### AC-3: A refresh never empties, rebuilds or re-animates a drawn chart
Requirement items: R-4, R-7, R-13, R-66, R-67
Rule: If a refresh carries no hero series or new sparkline data for a drawn chart, then the dashboard shall keep that chart's points, and every refresh shall update an existing chart in place, never destroying and rebuilding a chart that holds data or replaying its entry animation.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-3.1 | N3 | the hero chart holds N points | a poll arrives whose `hist_dates`, `hist_bot` and `hist_held` are empty lists | the hero still holds the same N points |
| S-3.2 | B | every sparkline is drawn | a poll arrives | each canvas keeps the same chart instance, its data equals the new `/api/chart` data, and no chart is destroyed |
| S-3.3 | N3 | the hero chart holds N points | a poll arrives with the `hist_*` keys absent, and another with them null | the hero keeps its points in both cases |
| S-3.4 | N5 | the page is open | ten polls and two cycle events arrive | each canvas has had exactly one chart constructed |
| S-3.5 | N6 | `/api/state` fails on a poll | the failure lands | every chart keeps its points |
| S-3.6 | N1 | the hero holds N points | a poll carries N+1 points | the hero shows N+1 points |
| S-3.7 | N7 | a triggered card's sparkline with an exit marker | a poll arrives | the exit marker is still drawn at the exit time |
| S-3.8 | N8 | the operator has the detail panel open with its intraday chart | a poll arrives | the detail chart is not rebuilt and keeps its overlays' toggle state |

Negative coverage: N1 S-3.6 · N2 n/a: a refresh refuses nothing · N3 S-3.1, S-3.3 · N4 n/a: the routes are behind the global auth hook, unchanged · N5 S-3.4 · N6 S-3.5 · N7 S-3.7 · N8 S-3.8
Questions: none

### AC-4: After a refresh the hero shows the selected calendar window
Requirement items: R-4, R-12, R-67, R-69, R-72
Rule: When a refresh lands while a window is selected, the hero chart shall show the series `/api/hero-chart` returns for that window's token (the calendar window: that many calendar days back from today inclusive, YTD from 1 January, 1Y as 365 days), never a re-slice of the poll payload by row count.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-4.1 | B | 1Y is selected and drawn from `/api/hero-chart/1y` | a poll arrives | the hero's first date and point count equal the 1Y response's |
| S-4.2 | B | the page has just loaded with 30d selected | the first poll arrives | the hero's first date equals `/api/hero-chart/30d`'s first date |
| S-4.3 | N1 | All Time is selected | a poll arrives | the hero holds the full series |
| S-4.4 | N1 | today is 2 January and YTD is selected | a poll arrives | the hero holds only points dated 2 January or later |
| S-4.5 | N6 | `/api/hero-chart/1y` fails after a 1Y click | the next poll arrives | the hero shows the 1Y calendar window computed from the poll's dated series, and the window label still reads 1Y |
| S-4.6 | N5 | the operator clicks 1Y then 30d before either response arrives | both responses and a poll land | the hero shows the 30d window |
| S-4.7 | N7 | any window | a poll arrives | the windowed headline, the vs-rows and the dollar-saved panel request and render as at the ref |
| S-4.8 | N5 | a window is selected | a poll arrives whose portfolio series is unchanged, then one whose series has a new day | the first poll sends no `/api/hero-chart` request and the second sends exactly one |

Negative coverage: N1 S-4.3, S-4.4 · N2 n/a: a window click refuses nothing · N3 n/a: an empty series is AC-3's case (S-3.1) · N4 n/a: the routes are behind the global auth hook, unchanged · N5 S-4.6, S-4.8 · N6 S-4.5 · N7 S-4.7 · N8 n/a: a window click holds no edit state
Questions: none

### AC-5: A theme or accent change recolours the charts
Requirement items: R-4, R-5, R-15, R-67
Rule: When the operator changes the theme or the accent in the tweaks panel on `/`, the hero chart, every sparkline, the exit markers and the MC dial arcs shall redraw in the colours their tokens then hold (`--studio-accent`, `--studio-ink-dim` and the exit-reason and dial tokens), without a reload.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-5.1 | B | the light theme | the operator switches to dark | each chart's line colours equal the computed `--studio-accent` and `--studio-ink-dim` values read after the switch |
| S-5.2 | B | any theme | the operator picks another accent swatch | the bot lines take the new accent value |
| S-5.6 | B | a triggered card with an exit marker and a dial arc | the operator switches to dark | the exit marker and the dial arc take the dark theme's token values |
| S-5.3 | N7 | any change | the charts redraw | their points and labels are unchanged |
| S-5.4 | N5 | the operator toggles light, dark, light | the last change lands | the colours equal the light theme's tokens |
| S-5.5 | N8 | the detail panel's intraday chart is open | the operator switches theme | the detail chart also takes the new colours |

Negative coverage: N1 n/a: the theme control offers fixed values only · N2 n/a: nothing is refused · N3 n/a: the tokens are always defined by `static/tokens.css` · N4 n/a: client-only · N5 S-5.4 · N6 n/a: no request · N7 S-5.3 · N8 S-5.5
Questions: none

### AC-6: Picking an account reloads the dashboard's cards for that account
Requirement items: R-6, R-9, R-11, R-67
Rule: When the operator picks an account in the workspace switcher on `/`, the dashboard shall reload its state through `/api/state`'s `account` parameter and show only that account's symphony cards, every later poll shall keep that filter, and the hero chart and portfolio strip shall keep covering all accounts under an "All accounts" caption, never mixing the filtered symphonies with whole-account totals.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-6.1 | B | two accounts, each with symphonies | the operator picks the second account | one `/api/state?account=` request for that account's id follows, and the cards shown are that account's |
| S-6.2 | N5 | an account is picked | the next poll and cycle event land | both send the same `account` filter |
| S-6.3 | N3 | an account with no symphonies | the operator picks it | no cards show and the section counts read zero, with no new copy |
| S-6.4 | N6 | `/api/accounts` fails | the operator opens the switcher | the existing "Failed to load accounts" text shows and the state is unchanged |
| S-6.5 | N7 | no account has been picked | the page polls | the request carries no `account` parameter, and the strip and hero equal the ref's |
| S-6.6 | N1 | an account id containing characters that need URL encoding | the operator picks it | the parameter is URL-encoded |
| S-6.7 | B | an account is picked | the strip and hero render | each reads the same values as with no account picked and carries the caption "All accounts" |
| S-6.8 | N3 | after market hours, when `/api/state` serves its frozen snapshot branch, which does not apply the `account` filter | an account is picked | the client hides the other accounts' cards; the server's frozen branch is unchanged |

Negative coverage: N1 S-6.6 · N2 n/a: the filter refuses nothing · N3 S-6.3, S-6.8 · N4 n/a: `/api/state` is behind the global auth hook, unchanged · N5 S-6.2 · N6 S-6.4 · N7 S-6.5 · N8 n/a: picking an account holds no edit state
Questions: Q-2

### AC-7: The fleet-correlation banner shows when an alert is active, and Dismiss is recorded
Requirement items: R-4, R-6, R-10, R-67
Rule: When the server render or a refresh carries an undismissed fleet-correlation alert, the dashboard shall show the fleet banner with that alert's text and hide it when a refresh carries none, the market-state banner shall stay hidden, and when the operator clicks Dismiss, the dismissal shall be sent with the CSRF token and the banner shall stay hidden after a reload.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-7.1 | B | an undismissed fleet alert at render | the operator opens `/` | the fleet banner shows at first paint with the "Concentrated risk" copy, the tripped and active counts, the reason and the trip time |
| S-7.2 | B | no alert at load | a poll carries an alert object | the fleet banner shows |
| S-7.3 | N3 | the fleet banner is shown | a poll carries no alert | the banner hides |
| S-7.4 | B | an active alert | the operator clicks Dismiss | one `POST /api/fleet-alert/dismiss` carries `X-CSRF-Token`, the response is 200, and after a reload the banner stays hidden |
| S-7.5 | N2 | the CSRF token is unavailable | the operator clicks Dismiss | the request is refused with 403 and the banner stays shown with a short notice that the dismissal was not saved |
| S-7.6 | N1 | an alert in the legacy string form | a poll carries it | the banner shows the string |
| S-7.7 | N7 | any market state | the page renders and polls | the market-state banner stays hidden and the header's engine status is unchanged; the More Info modal and its content are unchanged |
| S-7.8 | N5 | the operator clicks Dismiss twice | both responses land | the banner is hidden and the first dismissal time is kept; the second click does not overwrite it |

Negative coverage: N1 S-7.6 · N2 S-7.5 · N3 S-7.3 · N4 n/a: the route is behind the global auth hook, unchanged · N5 S-7.8 · N6 n/a: a failed poll leaves the banner as it was, the same path as S-3.5 · N7 S-7.7 · N8 n/a: Dismiss is a single click with no pending edit
Questions: Q-3

### AC-8: The detail panel title is the display name
Requirement items: R-4, R-7, R-10, R-39
Rule: When the operator opens a symphony's detail panel on `/`, its title shall read the symphony's display name as its card shows it, never the lowercase internal key.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-8.1 | B | a symphony named "Copper Ridge Rotation" | the operator clicks its card | the panel title reads "Copper Ridge Rotation" |
| S-8.2 | N3 | a state entry with no `name` | the panel opens | the title reads "Symphony Detail" |
| S-8.3 | N1 | a name containing an HTML bold tag | the panel opens | the tag shows literally |
| S-8.4 | N7 | any symphony | the panel opens | the detail chart, risk math and variables render as at the ref |

Negative coverage: N1 S-8.3 · N2 n/a: display only · N3 S-8.2 · N4 n/a: client-only over authenticated data · N5 n/a: opening the panel twice re-renders the same title · N6 n/a: the title reads the already-loaded state · N7 S-8.4 · N8 n/a: no edit state
Questions: none

## Rule-by-Entry-Point Matrix
| Rule | 30 s poll | Cycle-complete event | Window picker click | Account pick |
|---|---|---|---|---|
| AC-3 keep drawn charts, update in place | S-3.2, S-3.4 | S-3.4 | n/a: a click updates the hero in place already (`static/index.js:1640-1646`) and sparklines are not involved | S-3.4 |
| AC-4 selected calendar window | S-4.1 | S-4.1 | S-4.6 | n/a: the account filter does not change the selected window |
| AC-6 account filter kept | S-6.2 | S-6.2 | n/a: the picker does not call `/api/state` | S-6.1 |

## Not Building
Non-goals:
- NB-1: Any change to the dashboard's layout, primitives, numbers or copy beyond the eight criteria; it stays the reference the other pages conform to (overview NB-11).
- NB-2: Pinning Chart.js to a version or vendoring it into `static/`: the diagnosis recommends pinning, and the operator's message says to stop pinning versions (G-6 in the overview); the script tag stays as at the ref (`templates/index.html:11`) (approved by the PM, [PM-ASSUMED], operator may override).
- NB-3: Trimming the `/api/state` payload (3,879,395 bytes, about 2 MB of it a duplicated close snapshot); it needs a consumer search first (diagnosis section 6.6) (approved by the PM, [PM-ASSUMED], operator may override).

Behaviour that stays exactly as it is:
- NB-4: The 30 s poll interval, the event stream, the window picker's requests, the dollar-saved panel and the account-basis chip.
- NB-5: Every value `/api/state`, `/api/chart` and `/api/hero-chart` compute.

Deferred:
- NB-6: Measuring the droplet's first-paint timings and the server cost of `/api/state` per open tab (Known-Unconfirmed).

Protected surfaces consumed, not changed:
- NB-7: `alpha_bot_execution.py`, `math_engine.py`, `POST /api/sell_account` behind Emergency Liquidate, and `POST /api/trigger`.
- NB-8: Showing the market-state banner; it stays hidden as it is at the ref, because the header's engine status already shows the market state (approved by the PM, [PM-ASSUMED], operator may override).

## Defect Classes
| DC-n | Class (what goes wrong) | Trigger (B or N1-N8) | Sibling search (tool, query, scope) | Scenarios | Raised in review as |
|---|---|---|---|---|---|
| DC-1 | An absent or not-loaded value rendered as a final one (dash verdict, "0%" dial, blank canvas) | N3 | grep `&mdash;\|>0%<\|if (!.*length) return` in `templates/index.html` and `static/index.js`, PlanetStopper | S-1.3, S-1.4, S-2.5, S-2.7 | - |
| DC-2 | A refresh writer that drops or replaces good data with an empty payload | N3 | grep every assignment to `_heroFull`, `_cumChart.data`, `_sparks` and every `.destroy()` in `static/index.js`, PlanetStopper | S-3.1, S-3.2, S-3.3 | - |
| DC-3 | The same label showing different windows on different paths | B | grep `slice(-\|applyHeroWindow\|hero-chart/` in `static/index.js`, PlanetStopper | S-4.1, S-4.2 | - |
| DC-4 | A function read through `window` that is never assigned | B | grep `window\.[a-zA-Z]* ===\? 'function'` and each matching assignment in `static/`, PlanetStopper | S-6.1 | - |
| DC-5 | A POST sent without the CSRF header | N2 | grep `method: *'POST'\|method:'POST'` in `static/` and `templates/`, and check each sends `X-CSRF-Token`, PlanetStopper | S-7.4, S-7.5 | - |
| DC-6 | The server render and the client render of one value disagree | B | read: each element `updateDashboard` writes and its server-rendered counterpart in `templates/index.html` | S-2.1, S-2.4, S-7.1 | - |

## Worked Example
A portfolio with 87 daily points, of which the embedded 30d series holds 21, and 12 symphonies, 11 triggered: opening `/` draws the hero's two 21-point lines with their entry animation and starts 12 `/api/chart` requests at load; while `/api/state` is still pending, a triggered card reads "Early exit · gave up 0.1%α at exit" with a dash dial (S-1.1, S-2.1, S-2.4). With 1Y selected, a poll arriving with empty `hist_*` lists leaves the hero on the 1Y calendar series it already holds (S-3.1, S-4.1).

Failure case: `/api/state` never answers. The hero and sparklines are still drawn, the server-rendered verdicts stay, and the freshness text reads "connection lost" (S-1.2, S-2.8).

## Fail-Closed Behaviour
`POST /api/fleet-alert/dismiss` is refused by the global CSRF hook without a valid token (`app.py:490-493`); after this plan the button sends the token and, when refused, keeps the banner shown with a notice instead of hiding it (S-7.5). No other refusal is added or changed.

## Contract Changes and Consumers
- The server render of `/` additionally carries the hero series in a form the page script reads at load, and each triggered card's guard alpha (diagnosis section 6.1 and 6.2). Consumer: `static/index.js`. Additive.
- `/api/state?account=`: before, the filter narrowed the state used for both the cards and the portfolio strip, while the strip's account totals stayed whole-account (`app.py:3146-3153`, `:3351`, `:2099`); after, the filter narrows the symphony cards only and the strip is computed for all accounts. No client sent the parameter at the ref, so no consumer sees a change until this plan's account pick.

## Security Considerations
- Input validation and injection: the embedded hero series is data only, never executable script; display names render through `textContent` (S-8.3).
- Authorization and access control: no route changes guard; the dismiss request now passes the CSRF hook instead of being refused (S-7.4, S-7.5).
- Data exposure: the series embedded in `/` is already in `/api/state` for the same authenticated session.
- Client-side rendering: no `innerHTML` with unescaped data is added.
- Abuse or rate limits: sparklines are requested once at load instead of after the state poll; a poll adds at most one `/api/hero-chart` request, and only when the series changed (S-4.8), and in-place updates stop the per-poll rebuild of 12 charts.

## Design-System Mapping
| Component | Element | Required primitive or token | Notes |
|---|---|---|---|
| Hero chart | lines | `--studio-accent`, `--studio-ink-dim` read at draw and on `tweakchange` | no new token |
| Hero chart | empty state text | `.legend-as-of` style text | "No portfolio history to chart yet" |
| Card sparkline | empty and failed text | `--studio-ink-dim` text in the existing 64 px slot | |
| MC dial | dash state | existing `.mc-text` and `.mc-arc` | empty arc |
| Fleet banner | not-saved notice | existing banner text style | |

## Definition of Done (Verification)
| AC | Scenarios | Test level | Real-entrypoint test |
|---|---|---|---|
| AC-1 | S-1.1, S-1.2, S-1.3, S-1.4, S-1.5, S-1.6, S-1.7, S-1.8, S-1.9 | extracted functions under node with a recording `Chart` stub; server-render assertion with the Flask test client | live render of `/` in a browser with `/api/state` delayed |
| AC-2 | S-2.1, S-2.2, S-2.3, S-2.4, S-2.5, S-2.6, S-2.7, S-2.8, S-2.9, S-2.10 | server-render assertion of `GET /`; `updateVerdicts` and `renderMcDial` under node | `GET /` then one `/api/state` poll in a browser |
| AC-3 | S-3.1, S-3.2, S-3.3, S-3.4, S-3.5, S-3.6, S-3.7, S-3.8 | `renderHeroChart` and `renderSparkline` under node with a recording `Chart` stub (constructs, destroys, updates counted) | live `/` across ten polls |
| AC-4 | S-4.1, S-4.2, S-4.3, S-4.4, S-4.5, S-4.6, S-4.7, S-4.8 | refresh path under node with captured `/api/hero-chart` payloads | live `/` with 1Y selected across a poll |
| AC-5 | S-5.1, S-5.2, S-5.3, S-5.4, S-5.5, S-5.6 | `tweakchange` handler under node | live `/` with the tweaks panel |
| AC-6 | S-6.1, S-6.2, S-6.3, S-6.4, S-6.5, S-6.6, S-6.7, S-6.8 | `loadState` and `switchAccount` under node; route test of `/api/state?account=` for the strip | live `/` with two accounts |
| AC-7 | S-7.1, S-7.2, S-7.3, S-7.4, S-7.5, S-7.6, S-7.7, S-7.8 | server-render assertion of the banner with an alert row; banner update and dismiss under node; route test of the dismiss POST with and without the token | live `/` with an alert row |
| AC-8 | S-8.1, S-8.2, S-8.3, S-8.4 | `openDetailPanel` under node | live `/` |

Gates: `ruff format --check`; `ruff check`; a scoped pytest run of `tests/dashboard`, `tests/app`, `tests/realtime_push`, `tests/ui` and `tests/js_syntax` with at most 4 workers, never `-n auto`; CI green on the exact SHA; after `/review`, the PM's live check on the droplet of T-1 to T-3 across at least three refreshes.
The recording `Chart` stub counts constructor, `destroy` and `update` calls only; the assertion that a chart is visibly drawn is made on a live render, never on the stub.
Fixture provenance: `/api/state`, `/api/chart` and `/api/hero-chart` payloads are captured from the production database copy the audit used, one per case (healthy, empty `hist_*`, a triggered card, an untriggered card), with names and hashes replaced by invented ones of the same format.
Screenshots: `/` at 1440x900 and 375x812, light and dark, captured only after a readiness signal (every chart constructed), at load and after three refreshes, compared with the audit's reference captures.

## Known-Unconfirmed and Standing Risk
- The droplet's first-paint timings are unmeasured; the diagnosis measured 3.48 s for `/` and 6.11 s for `/api/state` on a snapshot copy. Read-only check, with the dashboard credential: `curl -s -o /dev/null -w '%{time_total}\n'` against the droplet's `/` and `/api/state`.
- The daemon's Flask process also hosts the scheduler thread; a 6 s CPU-bound `/api/state` per open tab every 30 s is unmeasured against the engine's timing (the engine runs as a spawned subprocess, so no effect is claimed).
- The audit's capture harness waited on server-rendered selectors; its next run needs a readiness signal (every chart constructed) or its screenshots will again show the pre-population frame.

## Open Questions
| Q-n | Question | Blocks (AC ids or "plan") | Proposed default | Answer |
|---|---|---|---|---|
| Q-1 | What does the hero show when the server render holds no history: the text "No portfolio history to chart yet", or the blank canvas as at the ref? | AC-1 | The text | default approved by the PM, [PM-ASSUMED], operator may override |
| Q-2 | At the ref, `/api/state?account=` computes the hero portfolio-wide and the strip from the filtered symphonies mixed with whole-account totals. After an account pick, should the cards alone follow the account, or should the hero and strip be computed per account too (new per-account totals)? | AC-6 | Cards only; the hero and strip cover all accounts under an "All accounts" caption | default approved by the PM, [PM-ASSUMED], operator may override |
| Q-3 | The fleet-correlation and market-state banners have never shown on `/`. Show the fleet banner when an alert is active, and keep the market-state banner hidden because the header already shows the engine status? | AC-7 | Yes to both | default approved by the PM, [PM-ASSUMED], operator may override |

## Decisions
| ID | Decision | Rationale |
|---|---|---|
| DEC-1 | Both render paths use the server's verdict text, including its "at exit" suffix | The suffix explains the figure is a value at exit, not an ongoing gap (`templates/index.html:1240-1244`) |
| DEC-2 | Chart.js stays unpinned | Operator's message (overview G-6) |
| PM-1 | N in S-1.1 and the Worked Example is the point count of the embedded 30d series | PM ruling on the Gate-2 approach |
| PM-2 | Chart.js's entry animation is kept on first draw, so the reference page's feel does not change, and readiness is signalled when it completes; refreshes still update in place (AC-3) | PM ruling on the Gate-2 approach |
| PM-3 | Requests per poll grow by at most one `/api/hero-chart` request, only when the series changed | PM ruling on the Gate-2 approach |
| PM-4 | An account with no symphonies shows no cards and zero counts, with no new copy | PM ruling on the Gate-2 approach |
| PM-5 | The dismiss route keeps the first dismissal time | PM ruling on the Gate-2 approach |
| PM-6 | A theme change also recolours exit markers and MC dial arcs | PM ruling on the Gate-2 approach |
| PM-7 | After hours the client hides other accounts' cards; the server's frozen branch is unchanged | PM ruling on the Gate-2 approach |
| PM-8 | Plan 05 approved at Step 7 under the project's standing autonomy directive (operator unavailable), with its Gate-2 approach `feature-plans/ux-overhaul/approach/05-dashboard-charts.md`; Q-1 to Q-3 take their defaults and every Not Building entry is approved | [PM-ASSUMED]; the operator may override |

## Docs to Update
- `docs/generated/static_index_js.md` and `docs/generated/app.md` (the `dashboard()` render), `docs/generated/INDEX.md`.
- A `DECISIONS.md` entry appended at ship by the doc writer.
- A drafted `.claude/CLAUDE.md` Key Files row update for `static/index.js` and `templates/index.html`, for PM approval.

## Follow-Ups
- Payload trimming of `/api/state` (NB-3): its own plan after a consumer search.
- Droplet timing measurement (NB-6): the PM's live check.
