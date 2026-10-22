# Approval packet: Dashboard charts, verdicts and banners hold through every refresh

## What is being built

The operator sees the dashboard's hero chart, card sparklines, verdict lines and MC dials drawn with true values from first paint and through every refresh, in the selected window and the current theme, with live banners and a detail panel titled by the symphony's name.
- The hero chart and sparklines draw without waiting for `/api/state` (AC-1).
- First-paint verdicts and dials match what a refresh shows (AC-2).
- A refresh never empties, rebuilds or re-animates a drawn chart (AC-3).
- After a refresh the hero shows the selected calendar window (AC-4).
- A theme or accent change recolours the charts without a reload (AC-5).
- Picking an account reloads the dashboard for that account (AC-6).
- The fleet-correlation banner shows when an alert is active, and Dismiss is recorded (AC-7).
- The detail panel title is the display name (AC-8).

## Acceptance criteria and scenarios

### AC-1: The hero chart and sparklines draw without waiting for the state poll
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

Negative coverage: N1 S-1.6 | N2 n/a: nothing on first paint is refused | N3 S-1.3, S-1.4 | N4 n/a: `/` and `/api/chart` are behind the global auth hook, unchanged | N5 S-1.8 | N6 S-1.2, S-1.5 | N7 S-1.7 | N8 n/a: first paint holds no edit state

### AC-2: First-paint verdicts and dials match what a refresh shows
Rule: When the server renders a triggered symphony's card, its verdict line shall read the same text a refresh writes for the symphony's guard alpha, and its MC dial shall read the dash with an empty arc, never "0%", whenever the symphony is triggered or its MC reading is unknown.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-2.1 | B | a triggered symphony whose guard alpha is negative | the operator opens `/` | the server-rendered verdict reads "Early exit | gave up", the magnitude to one decimal, "%\u03b1" and "at exit"; after the first poll the text is identical |
| S-2.2 | B | a triggered symphony whose guard alpha is positive | the operator opens `/` | the verdict reads "Good call | saved +", the value to one decimal, "%\u03b1" and "at exit" on both paths |
| S-2.3 | N1 | a triggered symphony whose guard alpha is exactly 0 | the operator opens `/` | both paths read "Early exit | gave up 0.0%\u03b1 at exit" |
| S-2.4 | B | a triggered symphony | the operator opens `/` | the dial reads the dash with an empty arc, and stays so after a poll |
| S-2.5 | N3 | a triggered symphony with no guard-alpha row | the page loads and polls | the verdict reads the dash on both paths |
| S-2.6 | N1 | an untriggered symphony whose MC reading is 0 | the page polls | the dial reads "0%" |
| S-2.7 | N3 | an untriggered symphony before its first MC reading is known | the operator opens `/` | the dial reads the dash, not "0%" |
| S-2.8 | N6 | `/api/state` fails | the page loads | the server-rendered verdict stays; it is not replaced by a dash |
| S-2.9 | N7 | an untriggered symphony | the page renders and polls | its verdict line stays hidden, as at the ref |
| S-2.10 | N5 | the page is open | three polls land | each triggered card's verdict text stays the same and never flashes the dash |

Negative coverage: N1 S-2.3, S-2.6 | N2 n/a: display only | N3 S-2.5, S-2.7 | N4 n/a: `/` is behind the global auth hook, unchanged | N5 S-2.10 | N6 S-2.8 | N7 S-2.9 | N8 n/a: the card holds no edit state

### AC-3: A refresh never empties, rebuilds or re-animates a drawn chart
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

Negative coverage: N1 S-3.6 | N2 n/a: a refresh refuses nothing | N3 S-3.1, S-3.3 | N4 n/a: the routes are behind the global auth hook, unchanged | N5 S-3.4 | N6 S-3.5 | N7 S-3.7 | N8 S-3.8

### AC-4: After a refresh the hero shows the selected calendar window
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

Negative coverage: N1 S-4.3, S-4.4 | N2 n/a: a window click refuses nothing | N3 n/a: an empty series is AC-3's case (S-3.1) | N4 n/a: the routes are behind the global auth hook, unchanged | N5 S-4.6, S-4.8 | N6 S-4.5 | N7 S-4.7 | N8 n/a: a window click holds no edit state

### AC-5: A theme or accent change recolours the charts
Rule: When the operator changes the theme or the accent in the tweaks panel on `/`, the hero chart, every sparkline, the exit markers and the MC dial arcs shall redraw in the colours their tokens then hold (`--studio-accent`, `--studio-ink-dim` and the exit-reason and dial tokens), without a reload.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-5.1 | B | the light theme | the operator switches to dark | each chart's line colours equal the computed `--studio-accent` and `--studio-ink-dim` values read after the switch |
| S-5.2 | B | any theme | the operator picks another accent swatch | the bot lines take the new accent value |
| S-5.6 | B | a triggered card with an exit marker and a dial arc | the operator switches to dark | the exit marker and the dial arc take the dark theme's token values |
| S-5.3 | N7 | any change | the charts redraw | their points and labels are unchanged |
| S-5.4 | N5 | the operator toggles light, dark, light | the last change lands | the colours equal the light theme's tokens |
| S-5.5 | N8 | the detail panel's intraday chart is open | the operator switches theme | the detail chart also takes the new colours |

Negative coverage: N1 n/a: the theme control offers fixed values only | N2 n/a: nothing is refused | N3 n/a: the tokens are always defined by `static/tokens.css` | N4 n/a: client-only | N5 S-5.4 | N6 n/a: no request | N7 S-5.3 | N8 S-5.5

### AC-6: Picking an account reloads the dashboard's cards for that account
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

Negative coverage: N1 S-6.6 | N2 n/a: the filter refuses nothing | N3 S-6.3, S-6.8 | N4 n/a: `/api/state` is behind the global auth hook, unchanged | N5 S-6.2 | N6 S-6.4 | N7 S-6.5 | N8 n/a: picking an account holds no edit state

### AC-7: The fleet-correlation banner shows when an alert is active, and Dismiss is recorded
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

Negative coverage: N1 S-7.6 | N2 S-7.5 | N3 S-7.3 | N4 n/a: the route is behind the global auth hook, unchanged | N5 S-7.8 | N6 n/a: a failed poll leaves the banner as it was, the same path as S-3.5 | N7 S-7.7 | N8 n/a: Dismiss is a single click with no pending edit

### AC-8: The detail panel title is the display name
Rule: When the operator opens a symphony's detail panel on `/`, its title shall read the symphony's display name as its card shows it, never the lowercase internal key.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-8.1 | B | a symphony named "Copper Ridge Rotation" | the operator clicks its card | the panel title reads "Copper Ridge Rotation" |
| S-8.2 | N3 | a state entry with no `name` | the panel opens | the title reads "Symphony Detail" |
| S-8.3 | N1 | a name containing an HTML bold tag | the panel opens | the tag shows literally |
| S-8.4 | N7 | any symphony | the panel opens | the detail chart, risk math and variables render as at the ref |

Negative coverage: N1 S-8.3 | N2 n/a: display only | N3 S-8.2 | N4 n/a: client-only over authenticated data | N5 n/a: opening the panel twice re-renders the same title | N6 n/a: the title reads the already-loaded state | N7 S-8.4 | N8 n/a: no edit state

## Rule-by-entry-point matrix

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

## Narrowed mappings

R-5: Every page and tab looks right; maps to AC-1, AC-2, AC-5; left out: this plan's part is the dashboard's charts, verdicts, dials and banners
R-6: Every control on every page functions; maps to AC-6, AC-7; left out: this plan's part is the workspace switcher's refresh on `/` and the fleet banner's Dismiss; Force run's honesty is plan 04's
R-7: The dashboard `/` is the reference standard and keeps its look; maps to AC-1, AC-2, AC-3, AC-8, NB-1; left out: `/` changes only its charts' loading and refresh, its first-paint verdict and dial, its account refresh, its detail-panel title, and the fleet-correlation banner, which earlier plans require and the operator has never seen because it never showed
R-10: Every page and tab renders honestly; maps to AC-2, AC-7, AC-8; left out: the dashboard only
R-11: The overhaul covers every page, the modal, the Advisor tabs and the header; maps to AC-1, AC-6; left out: the dashboard `/` only
R-15: Every page renders in the dark theme; maps to AC-5; left out: this plan's part is chart colours following a theme change; login and the token decisions are plan 04's and Claude Design's
R-39: Internal lowercase keys are replaced by display names; maps to AC-8; left out: the dashboard detail-panel title only

## User tasks

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

## Open questions

| Q-n | Question | Blocks (AC ids or "plan") | Proposed default | Answer |
|---|---|---|---|---|
| Q-1 | What does the hero show when the server render holds no history: the text "No portfolio history to chart yet", or the blank canvas as at the ref? | AC-1 | The text | default approved by the PM, [PM-ASSUMED], operator may override |
| Q-2 | At the ref, `/api/state?account=` computes the hero portfolio-wide and the strip from the filtered symphonies mixed with whole-account totals. After an account pick, should the cards alone follow the account, or should the hero and strip be computed per account too (new per-account totals)? | AC-6 | Cards only; the hero and strip cover all accounts under an "All accounts" caption | default approved by the PM, [PM-ASSUMED], operator may override |
| Q-3 | The fleet-correlation and market-state banners have never shown on `/`. Show the fleet banner when an alert is active, and keep the market-state banner hidden because the header already shows the engine status? | AC-7 | Yes to both | default approved by the PM, [PM-ASSUMED], operator may override |
