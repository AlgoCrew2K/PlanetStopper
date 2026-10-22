# Feature: AI Advisor Overview order and bounds, and a Retirement tab
Status: planned
Story: none: operator chat request on 2026-10-22, saved verbatim in feature-plans/ux-overhaul/00-overview.source.md
Type: fix
Suite: ux-overhaul 02
Repos: PlanetStopper
Base: main; the PR targets main once plans 05 and 01 are on main (no separate base branch for this plan); cycles C1 to C3 branch from `ux-overhaul/05`, cycles C4 and C5 branch after plan 01 is merged into this plan's branch, and C6 follows
Ref: PlanetStopper@37c8e983b64c6980c28199a3f9318486b60f3864
Sensitive: none: the Overview and Retirement surfaces are advisory displays; the retirement approve, reject and run routes and the no-trade boundary keep their behaviour (NB-6), and nothing moves money or arms live trading
Depends on: plan 01, merged into this plan's branch before cycles C4 and C5, which edit the same code (`loadRecentRuns` and the observations rows); plan 05 on `ux-overhaul/05`, this plan's base, because this plan moves the dashboard's primitives out of `templates/index.html`, which plan 05 also edits; the model-unpin plan (branch `model-unpin/fixtures`) merged into this plan's tip before its PR

## Trigger
The operator could not find the retirement analysis on the droplet's AI Advisor page (G-1). The app-wide UX audit rendered at the ref measured why: on the Overview tab the Market Prism block runs from y 299 to 1,379 px, the autotune list is 5,284 px tall with 50 cards, the Retirement Recommendations panel starts at y 6,832 (7.6 screens down at 1440x900), and a 4,734 px "Advisor Observations" table of 50 identical CLEAR rows hangs below every tab, outside the page layout (audit O4, A1, A2, UX-1). The PM assigned the Overview's order and bounds, a Retirement tab built from existing primitives and loading Chart.js on the Advisor page to this plan (G-18), and ruled D-3 and D-12 (G-26, G-35). The source is saved in `feature-plans/ux-overhaul/00-overview.source.md`.

What the code does at the ref:
1. The observations section opens after the page wrapper closes (`templates/ai_advisor.html:2727`, `:2901-2964`), outside every tab panel, so it renders full width at x 0 under all seven tabs (UX-1). The route collects up to 50 rows per role in `_ADVISOR_ROLES` order, Overfitting Conscience first, then cuts the combined list to 50 (`app.py:6640-6643`, `:6660`, `app.py:8859-8870`), so when Overfitting Conscience has 50 or more rows every other producer is dropped before the template sees it; the table prints one row per observation (`templates/ai_advisor.html:2930-2961`). Nothing guards the query: a database error in it fails the whole page with 500 (`app.py:6640-6643`, `database.py:1249-1257`).
2. The autotune panel lists every row `/api/autotune-runs` returns, up to 50 (`app.py:8979`, `static/ai_advisor.js:520-575`), one card per run.
3. The autotune sparkline returns early when `Chart` is undefined (`static/ai_advisor.js:425`), and the Advisor page loads no Chart.js (`templates/ai_advisor.html:2968-2971`), while `templates/index.html:11` and `templates/performance.html:617` do; a 60 px empty canvas sits under the panel header (`templates/ai_advisor.html:1562`) (UX-13, O1).
4. The Retirement Recommendations panel is the last section of the Overview tab panel (`templates/ai_advisor.html:1578-1793`); its last-run block is nine lines of 12 px text, Run now uses the ghost `.discuss-btn` (`templates/ai_advisor.html:1670-1675`), exclusion reasons print raw values such as `series_too_short` (`templates/ai_advisor.html:1661`, UX-20), and run times print as UTC (`templates/ai_advisor.html:1592-1596`) (O7).
5. Approving a retirement says "reload this page to view the wind-down checklist" (`static/ai_advisor.js:1142`), and Run now reloads the page when the run finishes (`static/ai_advisor.js:1274`); a reload always opens the Overview tab (`static/ai_advisor.js:646-685`).
6. The Prism block shows "As of" followed by the raw `created_at` (`templates/ai_advisor.html:1320`), source cards print the raw `published` value (`templates/ai_advisor.html:1403`), autotune cards print `run_timestamp` raw (`static/ai_advisor.js:561`), and observation rows print `created_at` raw (`templates/ai_advisor.html:2939`); the audit saw microseconds and no zone (UX-5). `advisor_observations.created_at` is SQLite's `datetime('now')`, UTC written without a zone marker (`migrations/017_advisor_observations.sql:12`), and autotune `run_timestamp` is UTC ISO with a `Z` (`autotuner.py:2562`).
7. The summary strip shows "0 suggestions", "0 OOS passed" and "0 OOS rejected" before any run (`templates/ai_advisor.html:1531-1538`, UX-21), and the Symphony label's `for` names `symphony-id-select` while the select's id is `symphony-id-input` (`templates/ai_advisor.html:1518-1519`, UX-2).
8. The Chat tab suggests "What does this correlation mean for my portfolio?" (`templates/ai_advisor.html:2080`), but no correlation cell has a Discuss entry point (CH2).

## Summary
The operator finds the retirement analysis one click away on its own AI Advisor tab, and an Overview that opens on actionable items with bounded lists, a drawn autotune sparkline, readable times and one status line per guardrail producer.
- Observations live inside the Overview tab as one status line per producer, with history behind an expander (AC-1).
- The autotune list shows the latest run per symphony, at most 12 cards (AC-2).
- The Advisor page loads Chart.js so the autotune sparkline draws (AC-3).
- Retirement is its own tab built from the dashboard's stat, stamp, card and pill primitives, keeping every behaviour it has (AC-4).
- The Overview carries a one-line Retirement pointer (AC-5).
- The Overview's first screen shows the pointer and the advisor controls before the Prism block (AC-6).
- Exclusion reasons read as sentences (AC-7).
- Overview times name their zone, pre-run chips are gone, and the Symphony label is linked (AC-8).
- Chat's example prompts name only flows the page can start (AC-9).

## Requirements as Given
[G-1] and yet, here i am, not seeing any fucking retirement or correlation shit on the droplet page I'm viewing. 00-overview.source.md:7
[G-3] holy fuck this looks like shit, you've done literally 0 UX screenshot checking. 00-overview.source.md:8
[G-4] Your whole job for the rest of the day is an overhaul of the entire application, ensuring it fucking looks right on top of functions properly. 00-overview.source.md:8
[G-7] PM Step-2 answers ([PM-ASSUMED]; the operator is not available for questions; project directive: record assumptions, do not stop) 00-overview.source.md:11
[G-8] Building: (1) every page and tab of the dashboard app renders correctly, honestly and consistently with the main dashboard `/`, which is the operator's reference standard, and every control on it works. 00-overview.source.md:13
[G-9] (2) Users: the operator, through `/`, `/history`, `/performance`, `/settings` (+ the per-symphony settings modal), `/login`, `/ai-advisor` (tabs Overview, Correlations, Asset Swaps, Logic Changes, Chat, Strategy Builder, Frontrunner Builder) and the shared header/nav. 00-overview.source.md:14
[G-10] (3) Flows: as the audit records per surface; the workflow files under `.claude/workflows/` if any exist. 00-overview.source.md:15
[G-11] (4) Negatives: per the audit (empty states, unverifiable states, unavailable data, narrow widths, dark theme). 00-overview.source.md:16
[G-18] 02-advisor-overview: Overview order and bounds (autotune list of 50 identical cards, observations table outside the layout under every tab), Retirement as its own tab (interim, from existing primitives `.mini-stats`, `.legend-as-of`, `.sym-card`, `.status-pill`), Chart.js loaded on the Advisor page. 00-overview.source.md:25
[G-24] PM rulings on the audit's decisions (record as decisions in the overview; each a default the operator may override) 00-overview.source.md:30
[G-26] D-3 Retirement its own tab now, combined Correlations+Retirement view after Claude Design B2; 00-overview.source.md:33
[G-35] D-12 observations collapse to one status line per producer with history behind an expander. 00-overview.source.md:42

## Requirement Inventory
These are the overview's R ids. Overview items R-21, R-70 and R-71 (plan numbering, the Base chain, landing after model-unpin) are met by this plan's Suite, Base and Depends on headers. R-47 (the combined view after B2) is the overview's NB-5.

| R-n | Requirement item | Source (G ids) | Maps to (AC ids, or NB-n) | Scope (full, or narrowed: what is left out) |
|---|---|---|---|---|
| R-1 | The operator sees the retirement analysis on the AI Advisor page on the droplet without scrolling past unrelated content | G-1, G-18, G-26 | AC-4, AC-5, AC-6 | full |
| R-4 | Every screen this plan changes is checked by rendered screenshots before it ships | G-3 | AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-8 | full |
| R-5 | Every page and tab looks right | G-4, G-8 | AC-1, AC-2, AC-4, AC-6 | narrowed: this plan's part is the Overview and Retirement tabs; page-wide primitives are plan 04's, the Overview information architecture is Claude Design B3 |
| R-6 | Every control on every page functions | G-4, G-8 | AC-3, AC-4 | narrowed: this plan's part is the autotune sparkline, the Retirement tab's Run now, Approve and Reject, and the tab staying selected through a reload |
| R-9 | Open questions are answered by recorded defaults, never by stopping | G-7 | AC-4, AC-6 | full |
| R-10 | Every page and tab renders honestly | G-8 | AC-1, AC-7, AC-8, AC-9 | narrowed: this plan's surfaces only |
| R-11 | The overhaul covers every page, the modal, the Advisor tabs and the header | G-9 | AC-1, AC-4, AC-9 | narrowed: the Advisor Overview, the new Retirement tab and the Chat tab |
| R-12 | Flows follow the audit per surface, and workflow files where they exist | G-10 | AC-4, AC-5 | full |
| R-13 | Empty, unverifiable and unavailable states are honest on every surface this plan touches | G-11 | AC-1, AC-2, AC-4, AC-8 | full |
| R-42 | The observations table sits inside the page layout and only on the Overview tab | G-18 | AC-1 | full |
| R-43 | Observations collapse to one status line per producer with history behind an expander | G-35 | AC-1 | full |
| R-44 | The autotune list is bounded | G-18 | AC-2 | full |
| R-45 | The Overview puts actionable items before reference material | G-18 | AC-6 | narrowed: the Overview information architecture design (B3) and the Prism five-lens layout are left out |
| R-46 | Retirement is its own Advisor tab, built from existing dashboard primitives | G-18, G-26 | AC-4 | full |
| R-48 | The Advisor page loads Chart.js so its autotune sparkline draws | G-18 | AC-3 | full |
| R-49 | Overview timestamps name their timezone without fractional seconds, pre-run zero chips are gone, and the symphony label is linked to its control | G-10, G-11 | AC-8 | full |
| R-50 | Exclusion reasons render as sentences | G-10 | AC-7 | narrowed: the Retirement tab's part; the Correlations matrix's part is plan 03's |
| R-51 | Chat example prompts name only flows the page can start | G-10 | AC-9 | full |
| R-69 | The PM's rulings are recorded as decisions, each a default the operator may override | G-24 | AC-1, AC-4 | full |

## User Tasks
Workflow (AI Advisor Overview and Retirement tabs): no workflow file: this repository has no `.claude/workflows/` directory at the ref.

### T-1: Find the retirement analysis
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Open `/ai-advisor` | a completed retirement run | The first screen shows the Retirement pointer and a Retirement tab in the tab bar | S-5.1, S-6.1 |
| 2 | Click the Retirement tab | none | The last run's summary tiles, its time in ET, Run now, and any recommendation cards show | S-4.1 |

### T-2: Run the retirement analysis now
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Click Run now outside market hours | none | The status slot reports the run; when it finishes the page reloads on the Retirement tab | S-4.3 |
| 2 | Click Run now during market hours | none | The status slot says Run now is closed during market hours; nothing runs | S-4.4 |

### T-3: Decide a recommendation
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Click Approve retirement on a pending card | none | The card's status pill reads Approved; after a reload the Retirement tab shows the wind-down checklist | S-4.6 |

### T-4: Review the guardrail producers and autotune runs
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Scroll the Overview | stored observations and autotune runs | One status line per producer; the latest run per symphony; a drawn sparkline | S-1.1, S-2.1, S-3.1 |
| 2 | Open a producer's history expander | none | That producer's observations list, newest first | S-1.3 |

Workflow (AI Advisor Chat tab): no workflow file: this repository has no `.claude/workflows/` directory at the ref.

### T-5: Learn what Chat can explain
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Open the Chat tab | none | Each example question names a card that has a Discuss button | S-9.1 |

## Earlier criteria on the same surface
| Earlier criterion (plan and AC) | Surface | Disposition (kept, changed or contradicted) | Why |
|---|---|---|---|
| `feature-plans/retirement-visibility.md` AC-6 (the Overview-tab Retirement panel's always-present last-run block: run time, basis, symphonies analysed, depth, pairs screened at the threshold, blocked counts per gate with the stress minimum, recommendations, exclusions, and distinct never-run, `series_unavailable`, `insufficient_symphonies` and `internal_error` states) | Retirement panel | changed | The block moves to the Retirement tab and renders as stat tiles with an ET stamp; the threshold, the four per-gate counts, the stress minimum and every state's copy all stay (S-4.1, S-4.8 to S-4.11, S-4.14) |
| `feature-plans/retirement-visibility.md` AC-7 and AC-7b (Run now: 409 while the market is open, 409 while a run is in flight, else 202, poll then reload) | Run now | kept | The requests and refusals are unchanged; the reload now returns to the Retirement tab (S-4.3) |
| `feature-plans/retirement-visibility.md` AC-5b (a note when the cards come from an earlier successful run) | Retirement cards | kept | The note moves with the cards (S-4.12) |
| `feature-plans/retirement-approval-lifecycle.md` (approve and reject buttons on pending cards; checklist on approved cards; status chip) | Retirement cards | changed | The chip becomes the dashboard's status pill; the buttons, routes and checklist are unchanged (S-4.6, S-4.7) |
| `feature-plans/retirement-recommender-core.md` (the panel on the Overview tab) | Retirement panel placement | changed | D-3 moves it to its own tab |
| The Symphony Tenure Review plan (status ready, committed on branch `feat/tenure-review-c1`, not on main at the ref), its S-1.1 and T-1 (the review reads on the Overview tab's Retirement Recommendations panel) | Retirement panel placement | contradicted | That approved plan places its review on the Overview panel this plan moves; Q-1 asks which lands first |
| `feature-plans/advisor-remediation-r1.md` AC-14 (the guardrail note names each producer's real scope) | Observations note | kept | The note stays above the status lines (S-1.6) |
| `feature-plans/audit-bl5-12-hygiene-cleanup.md` BL-8 (the adoption-streak line on autotune cards) | Autotune card | kept | Cards keep the line (S-2.6) |
| `feature-plans/market-prism-overview.completed.md` (the always-on Market Prism block on the Overview) | Prism block | changed | The block stays on the Overview, below the actionable sections (AC-6) |
| `feature-plans/advisor-latency-cache-serve.md` AC-3 (the "market context as of" stamp) | Lens stamp | changed | The stamp follows the shared time format (S-8.3) |

## Acceptance Criteria (Building)
### AC-1: Observations sit in the Overview tab as one status line per producer
Requirement items: R-4, R-5, R-10, R-11, R-13, R-42, R-43, R-69
Rule: When `/ai-advisor` renders, the advisor observations shall render inside the page wrapper and inside the Overview tab panel only, as one status line for every producer that has observations, whatever another producer's volume (its label, its latest verdict, the latest observation's time and its total observation count), with that producer's newest rows, up to 50, behind an expander that is closed on load.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-1.1 | B | 60 stored observations from Overfitting Conscience and 3 from Spec Critic | the Overview renders | two status lines show, each with its label, latest verdict, time and total count (60 and 3); no observation row is visible |
| S-1.10 | N1 | the same data | the operator opens the Overfitting Conscience expander | it lists the 50 newest rows and states that it shows 50 of 60 |
| S-1.2 | B | the same data | the operator opens any other tab | no observation status line or row is in view |
| S-1.3 | B | the same data | the operator opens the Overfitting Conscience expander | its rows list, newest first, each with the symphony's display name, verdict and summary |
| S-1.4 | N3 | no stored observations | the Overview renders | the existing "No observations from advisors at this time." text shows inside the Overview panel |
| S-1.5 | N3 | a producer role in the list with no rows | the Overview renders | no status line shows for it |
| S-1.6 | N7 | any data | the Overview renders | the guardrail scope note renders above the status lines with the text plan 01 gives it |
| S-1.7 | N1 | a producer whose latest row has an empty verdict | the Overview renders | its status line reads "verdict not recorded" |
| S-1.8 | N5 | an expander is open | the operator switches tabs and back | the expander keeps its state |
| S-1.9 | N6 | the observations query raises a database error | the page renders | the page returns 200; the Overview reads "Observations could not be loaded.", not the empty text, and every other section and tab renders |

Negative coverage: N1 S-1.7, S-1.10 · N2 n/a: display only · N3 S-1.4, S-1.5 · N4 n/a: the page is behind the global auth hook, unchanged · N5 S-1.8 · N6 S-1.9 · N7 S-1.6 · N8 n/a: the expander is not an edit
Questions: none

### AC-2: The autotune list shows the latest run per symphony
Requirement items: R-4, R-5, R-13, R-44
Rule: When the Overview loads its autotune runs, the panel shall show one card for each symphony's most recent run, at most 12 cards ordered newest first, and list every older run behind an expander that is closed on load and whose label states that the list covers the route's 50 most recent runs.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-2.1 | B | 50 runs across 11 symphonies | the Overview loads | 11 cards show, one per symphony, each its newest run; an expander labelled with the count of older runs (39) and that the list covers the 50 most recent runs is closed |
| S-2.2 | N1 | runs for 15 symphonies | the Overview loads | 12 cards show, the 12 most recent; the other symphonies' latest runs are inside the expander |
| S-2.3 | N3 | no runs | the Overview loads | the existing "No tuning runs recorded yet." text shows and no expander |
| S-2.4 | N1 | exactly one run per symphony | the Overview loads | no expander shows |
| S-2.5 | N5 | the panel has rendered | the 15 s poll re-renders twice | the card count and the expander's state are unchanged |
| S-2.6 | N7 | any run | its card renders | it carries the decision pill, the plan 01 labels and the adoption-streak line, as for any card |
| S-2.7 | N6 | `/api/autotune-runs` fails | the panel loads | the existing failure text shows |

Negative coverage: N1 S-2.2, S-2.4 · N2 n/a: display only · N3 S-2.3 · N4 n/a: the route is behind the global auth hook, unchanged · N5 S-2.5 · N6 S-2.7 · N7 S-2.6 · N8 n/a: no edit state
Questions: none

### AC-3: The autotune sparkline draws on the Advisor page
Requirement items: R-4, R-6, R-48
Rule: When `/ai-advisor` loads, the page shall load the same Chart.js script the dashboard loads, so the autotune sparkline draws from the runs' best objective scores (`naive_sharpe`), and if Chart.js cannot load, the sparkline canvas shall not show.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-3.1 | B | runs with best objective scores | the Overview loads | the canvas holds a chart whose point count equals the number of runs with a value |
| S-3.2 | N3 | runs whose best objective scores are all null | the Overview loads | the canvas does not show |
| S-3.3 | N6 | the Chart.js script fails to load | the Overview loads | the canvas does not show and the run cards still render |
| S-3.4 | N7 | the page | it loads | the script tag's source equals `templates/index.html:11`'s |
| S-3.5 | N5 | the 15 s poll re-renders | the sparkline redraws | one chart exists on the canvas |
| S-3.6 | N1 | exactly one run with a value | the Overview loads | the chart draws that point |

Negative coverage: N1 S-3.6 · N2 n/a: nothing is refused · N3 S-3.2 · N4 n/a: page behind the global auth hook, unchanged · N5 S-3.5 · N6 S-3.3 · N7 S-3.4 · N8 n/a: no edit state
Questions: none

### AC-4: Retirement is its own tab, built from the dashboard's primitives
Requirement items: R-1, R-4, R-5, R-6, R-9, R-11, R-12, R-13, R-46, R-69
Rule: When `/ai-advisor` renders, the tab bar shall carry a Retirement tab whose panel shows the last run as stat tiles in the dashboard's `.mini-stats` form, its run time as a `.legend-as-of` stamp in ET, Run now as a secondary button with the market-hours hint, and each recommendation as a `.sym-card` with a `.status-pill`, keeping every state, request and refusal the Overview panel has at the ref, and the tab shall stay selected through a page reload by the URL fragment naming it (`#retirement`).

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-4.1 | B | a successful run summary (11 of 12 analysed, 29 to 250 days of history, 10 of 55 pairs at or above the 0.65 screen, blocked 6 by uncertainty, 4 by a too-thin stress window, 0 by stress below threshold, 0 with no candidate, 0 recommendations) | the operator opens the Retirement tab | tiles read Analysed 11 of 12, History 29 to 250 days, Pairs screened at 0.65 or above 10 of 55, Blocked 10 with the four per-gate counts listed under it, Recommendations 0, each read from the summary; the stamp reads "Last run" with the date and the time in ET |
| S-4.2 | B | the same summary | the operator reads the basis | the basis line reads the summary's `basis_label` verbatim |
| S-4.3 | B | the market is closed | the operator clicks Run now and the run finishes | the 202 path, the 5 s poll and the reload behave as at the ref, and the reload opens on the Retirement tab |
| S-4.4 | N2 | the market is open | the operator clicks Run now | the 409 `market_open` reason shows in the status slot, nothing polls, nothing runs |
| S-4.5 | N5 | a run is in flight | the operator clicks Run now | the 409 `already_running` path waits for that run, as at the ref |
| S-4.6 | B | a pending recommendation card | the operator clicks Approve retirement | the same request as at the ref is sent; the pill reads Approved; after a reload the Retirement tab is selected and the card shows the wind-down checklist |
| S-4.7 | B | a pending card | the operator clicks Reject | the same request as at the ref; the pill reads Rejected |
| S-4.8 | N3 | no run has ever completed | the tab renders | it shows the never-run copy (`templates/ai_advisor.html:1667`) with the schedule named as 03:45 UTC, and no tiles |
| S-4.9 | N3 | the latest run's status is `series_unavailable` | the tab renders | its existing honest copy shows with the analysed count |
| S-4.10 | N3 | the latest run's status is `insufficient_symphonies` | the tab renders | its existing copy shows |
| S-4.11 | N3 | the latest run's status is `internal_error` | the tab renders | its existing copy shows with the error type |
| S-4.12 | N3 | the latest run failed and the cards come from an earlier successful run | the tab renders | the earlier-run note shows with that run's date |
| S-4.13 | N3 | a summary tile's value is missing, or one of the four per-gate counts is missing | the tab renders | that tile reads the dash, never 0; the Blocked total reads the dash when any per-gate count is missing |
| S-4.14 | N1 | `blocked_stress_too_thin` above 0 | the tab renders | the Blocked tile's detail states the stress-gate minimum read from the summary |
| S-4.15 | N7 | any state | the tab renders | `POST /ai-advisor/retirement/run`, `/approve` and `/reject` and `GET /api/retirement-recommendations` are called with the same requests as at the ref, and the no-trade boundary tests pass with the transitive scan extended to every retirement helper this plan adds |
| S-4.16 | N8 | a Run-now poll is in progress | the operator switches to another tab and back | the status slot still reports the run and the reload still lands on the Retirement tab |
| S-4.17 | N6 | `GET /api/retirement-recommendations` fails during a Run-now poll | the poll times out | the existing "still going, reload later" text shows |
| S-4.19 | N7 | the Retirement section's `data-testid` values at the ref (`retirement-recommendations-panel`, `retirement-last-run` and its line ids, `retirement-run-now-btn`, `retirement-run-now-status`, `retirement-run-now-hint`, `retirement-cards-from-earlier-run`, `retirement-recommendation-card`, `retirement-rec-correlation`, `retirement-rec-explanation`, `retirement-rec-status`, `retirement-approve-btn`, `retirement-reject-btn`, `retirement-checklist`, `retirement-recommendations-empty`) | the Retirement tab renders each state | every one of those ids is still present on the element that carries the same content |
| S-4.18 | N7 | this plan has moved the dashboard primitives (`.hero-section`, `.sym-card`, `.card-name`, `.section-header`, `.mini-stats` and its parts, `.status-pill`, `.legend-as-of`) out of `templates/index.html` into the shared definition | the operator opens `/` | `/` renders unchanged against this plan's base: the server-rendered `/` markup has the same element structure and classes, and every CSS rule that resolves on those primitives' selectors on `/` has the same property values as at the base; the screenshot gate shows no difference at 1440 light, 1440 dark and 375 light |

Negative coverage: N1 S-4.14 · N2 S-4.4 · N3 S-4.8, S-4.9, S-4.10, S-4.11, S-4.12, S-4.13 · N4 n/a: every retirement route is behind the global auth and CSRF hooks, unchanged · N5 S-4.5 · N6 S-4.17 · N7 S-4.15, S-4.18, S-4.19 · N8 S-4.16
Questions: Q-1, Q-2

### AC-5: The Overview carries a one-line Retirement pointer
Requirement items: R-1, R-4, R-12
Rule: When the Overview renders, it shall show one Retirement line in the `.mini-stat` form stating the last run's time in ET and its recommendation count, or that no run has completed, and selecting it shall open the Retirement tab.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-5.1 | B | a successful run with 2 pending recommendations | the Overview renders | the line reads the run time in ET and "2 recommendations, 2 pending" |
| S-5.2 | B | the line | the operator selects it | the Retirement tab opens |
| S-5.3 | N3 | no run has completed | the Overview renders | the line reads "Retirement: not run yet" |
| S-5.4 | N3 | the latest run failed | the Overview renders | the line names the failure state and the time |
| S-5.5 | N7 | any state | the Overview renders | the pointer issues no request beyond those the page makes at the ref |

Negative coverage: N1 n/a: the pointer shows counts the summary already validated · N2 n/a: display only · N3 S-5.3, S-5.4 · N4 n/a: page behind the global auth hook, unchanged · N5 n/a: selecting the pointer twice opens the same tab · N6 n/a: it reads the same summary as AC-4, whose failure path is S-4.17 · N7 S-5.5 · N8 n/a: no edit state
Questions: none

### AC-6: The Overview's first screen shows actionable items first
Requirement items: R-1, R-4, R-5, R-9, R-45
Rule: When the Overview renders, its sections shall appear in this order: the Retirement pointer, the advisor controls with the suggestions and autotune panel, the Market Prism block, then the observations status lines.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-6.1 | B | a full data set | the Overview renders at 1440x900 | the Retirement pointer and the advisor controls are inside the first 900 px |
| S-6.2 | B | the same | the Overview renders | the Market Prism block follows the advisor grid and precedes the observations |
| S-6.3 | N3 | no Market Prism row | the Overview renders | the Prism empty state renders in the same position |
| S-6.4 | N7 | any data | the Overview renders | the Prism block's lenses, sources and verification render as at the ref apart from plan 01's pill and this plan's times |

Negative coverage: N1 n/a: order does not depend on input values · N2 n/a: display only · N3 S-6.3 · N4 n/a: page behind the global auth hook, unchanged · N5 n/a: the order is server-rendered once per load · N6 n/a: each section keeps its own failure state, covered by S-1.9, S-2.7 · N7 S-6.4 · N8 n/a: no edit state
Questions: Q-3

### AC-7: Exclusion reasons read as sentences
Requirement items: R-10, R-50
Rule: When the Retirement tab lists a symphony excluded from a run, it shall show the symphony's display name and a sentence for its reason rendered through one callable that plan 03's excluded list also renders through: `no_account` as "No Composer account is recorded for this symphony.", `series_too_short` as "Its Composer series has fewer than 2 daily returns.", `fetch_error: ` followed by a code as "Its Composer series could not be fetched (" the code ").", and `shadow_series_unavailable` as "No shadow history is available for it."

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-7.1 | B | an exclusion with reason `series_too_short` | the tab renders | the entry reads the display name and "Its Composer series has fewer than 2 daily returns." |
| S-7.2 | N1 | reasons `no_account`, `fetch_error: HTTP 429` and `shadow_series_unavailable` | the tab renders | each reads its sentence; the fetch error names HTTP 429 |
| S-7.3 | N3 | a reason not in the mapping | the tab renders | the entry reads "Excluded: " followed by the raw reason |
| S-7.4 | N3 | an empty reason | the tab renders | the entry reads the display name and "Reason not recorded." |
| S-7.5 | N1 | a symphony no longer in the live state | the tab renders | the entry reads the Composer hash with its sentence |
| S-7.6 | N7 | any exclusion | the tab renders | the stored summary row is unchanged |

Negative coverage: N1 S-7.2, S-7.5 · N2 n/a: display only · N3 S-7.3, S-7.4 · N4 n/a: page behind the global auth hook, unchanged · N5 n/a: server-rendered once per load · N6 n/a: the list reads the already-fetched summary · N7 S-7.6 · N8 n/a: no edit state
Questions: none

### AC-8: Overview times name their zone, pre-run chips are gone, the label is linked
Requirement items: R-4, R-10, R-13, R-49
Rule: When the Overview renders a time (the Prism as-of, source-card publish times, autotune run times, observation times and the lens-cache stamp), it shall show it in ET without fractional seconds in the dashboard's form, reading the zone-less `advisor_observations.created_at` stamps as UTC, the summary chips shall not show until a suggestion run has returned, and the Symphony label shall be linked to its select.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-8.1 | B | a Prism row whose `created_at` is "2026-10-22 11:10:06" (UTC, no zone marker) | the Overview renders | the stamp reads "As of 2026-10-22 07:10 ET" |
| S-8.2 | B | an autotune run at 2026-10-17T01:08:12.048917Z | the card renders | it reads "2026-10-16 21:08 ET" |
| S-8.3 | B | a suggest response with `lens_data_as_of` | the stamp shows | it reads "market context as of" with the date and time in ET |
| S-8.4 | N3 | a source card whose feed-supplied publish time has no zone | the card renders | it shows the date only, never a time without a zone |
| S-8.5 | N3 | a missing or unparseable time | it renders | the dash shows, never the raw string |
| S-8.6 | B | no suggestion run this page load | the Overview renders | no "0 suggestions", "0 OOS passed" or "0 OOS rejected" chip is visible |
| S-8.7 | B | a suggestion run returns 3 suggestions, 1 passed | the response lands | the chips show 3, 1 and 2 |
| S-8.8 | N1 | the rendered page | its labels are checked | every `label` `for` on `/ai-advisor` names an id present on the page |
| S-8.9 | N7 | the times | they render | each stored value is unchanged; only its display changes |
| S-8.10 | N5 | two suggestion runs | both return | the chips show the second run's counts |

Negative coverage: N1 S-8.8 · N2 n/a: display only · N3 S-8.4, S-8.5 · N4 n/a: page behind the global auth hook, unchanged · N5 S-8.10 · N6 n/a: a failed suggestion run leaves the chips hidden, the same state as S-8.6 · N7 S-8.9 · N8 n/a: no edit state
Questions: none

### AC-9: Chat's example prompts name only flows the page can start
Requirement items: R-10, R-11, R-51
Rule: When the Chat tab renders its example questions, each shall name an item that carries a Discuss entry point on the page, and no example shall name correlations while no correlation element has one.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-9.1 | B | the page at this plan | the Chat tab renders | no example mentions "correlation"; every example names a swap, a logic change or a Strategy Builder proposal |
| S-9.2 | N7 | the Chat panel | it opens from a Discuss button | its explain-only notice and behaviour are unchanged |
| S-9.3 | N3 | Chat unavailable (no key) | the Chat tab renders | the examples still render and the panel's unavailable state is unchanged |

Negative coverage: N1 n/a: static copy · N2 n/a: copy only · N3 S-9.3 · N4 n/a: page behind the global auth hook, unchanged · N5 n/a: static copy · N6 n/a: no request · N7 S-9.2 · N8 n/a: no edit state
Questions: none

## Rule-by-Entry-Point Matrix
| Rule | Run now | Approve retirement | Reject | Nightly 03:45 UTC tick |
|---|---|---|---|---|
| AC-4 same requests and refusals as at the ref | S-4.3, S-4.4, S-4.5 | S-4.6 | S-4.7 | n/a: the tick is not a UI entry point and this plan does not change it |
| AC-4 Retirement tab stays selected after the reload | S-4.3 | S-4.6 | n/a: Reject does not reload | n/a: no page involved |

## Not Building
Non-goals:
- NB-1: The combined Correlations and Retirement view (overview NB-5, Claude Design B2).
- NB-2: Redesigning the Market Prism block's five lens columns or its sources carousel (overview NB-8, Claude Design B3).
- NB-3: A Discuss entry point on correlation cells (approved by the PM, [PM-ASSUMED], operator may override).

Behaviour that stays exactly as it is:
- NB-4: The retirement recommender, its run summary, its nightly tick, the explainer and the checklist builder; every retirement route's request, response and refusal.
- NB-5: The observation roles, the feature-off filter and the preview text; only the collection changes, so every producer keeps its line and its true count (AC-1).

Deferred:
- NB-7: The Advisor tab bar's uneven heights and hover-only dots (overview NB-13).

Protected surfaces consumed, not changed:
- NB-6: `POST /ai-advisor/retirement/run`, `/approve`, `/reject` and `GET /api/retirement-recommendations` stay unchanged. The no-trade boundary test `tests/security/test_retirement_action_no_trade_boundary.py` is extended so its transitive scan covers every retirement helper this plan adds (PM ruling: safety coverage over a frozen test file); `tests/security/test_retirement_recommender_no_trade_boundary.py` stays unchanged.

## Defect Classes
| DC-n | Class (what goes wrong) | Trigger (B or N1-N8) | Sibling search (tool, query, scope) | Scenarios | Raised in review as |
|---|---|---|---|---|---|
| DC-1 | A section rendered outside the tab panels or page wrapper | B | grep `</div>{# /page-wrap #}\|<section` and each section's ancestor in `templates/ai_advisor.html`, PlanetStopper | S-1.1, S-1.2 | - |
| DC-2 | An absent value shown as a zero or a raw string | N3 | grep `or 0\|'0 \|\| ''` and each time and count rendered in `templates/ai_advisor.html` and `static/ai_advisor.js`, PlanetStopper | S-4.13, S-8.5, S-8.6 | - |
| DC-3 | Tab or panel state lost on reload or re-render | N8 | grep `location.reload\|activateTab\|aria-selected` in `static/ai_advisor.js`, PlanetStopper | S-4.3, S-4.6, S-4.16 | - |
| DC-4 | A moved element whose script or test still targets the old place or class | B | grep `retirement-status-chip\|retirement-recommendations-panel\|tab-panel-overview\|advisor-observations-section` in `templates/`, `static/` and `tests/`, PlanetStopper | S-4.6, S-4.15 | - |
| DC-5 | An unbounded list rendered whole | B | grep `rows.map\|{% for .* in observations` in `static/ai_advisor.js` and `templates/ai_advisor.html`, PlanetStopper | S-2.1, S-2.2, S-1.1 | - |
| DC-6 | An unformatted time | B | grep `created_at\|run_timestamp\|published\|run_at\|as_of` in `templates/ai_advisor.html` and `static/ai_advisor.js`, PlanetStopper | S-8.1, S-8.2, S-8.3 | - |

## Worked Example
A droplet run at 2026-10-22T18:50:00Z analysed 11 of 12 symphonies with 29 to 250 days of realized history, screened 10 of 55 pairs, blocked all 10 and recommended none. The Overview's first line reads "Retirement: last run 2026-10-22 14:50 ET, 0 recommendations"; selecting it opens the Retirement tab, whose tiles read 11 of 12, 29 to 250 days, 10 of 55, 10 and 0, with "Last run 2026-10-22 14:50 ET" and the basis "realized daily returns (Composer, deposit-adjusted)"; the excluded symphony "Quiet Meadow Bonds" reads "Its Composer series has fewer than 2 daily returns." (S-5.1, S-4.1, S-7.1).

Failure case: at 10:15 ET on a trading day the operator clicks Run now; the status slot shows the market-open reason, nothing polls, and the tiles still show the last run (S-4.4).

## Fail-Closed Behaviour
Refusals on the write paths this plan's controls reach, each unchanged:
- `POST /ai-advisor/retirement/run`: 409 `{"status": "market_open"}` while the market is open (`app.py:8431-8432`, S-4.4); 409 `{"status": "already_running"}` while the lock is held (`app.py:8434-8435`, S-4.5); 500 when the run thread cannot start (`ai_advisor_retirement_run`, S-4.15).
- `POST /ai-advisor/retirement/approve` and `/reject`: 200 with `"error": "invalid candidate_id"` for a missing or over-long id, and 200 with the exception's type name when the write fails (`app.py:8346-8360`, S-4.15).
- Every POST above without a valid CSRF token: 403 HTML from the global hook (`app.py:379`); the page shows "Could not start the run." for Run now (`static/ai_advisor.js:1259-1262`).

## Contract Changes and Consumers
none: no route payload changes; the Retirement tab reads the same context keys (`retirement_last_run`, `retirement_cards_run`, `retirement_recommendations`, `retirement_last_run_excluded`) the panel reads at the ref; the observations context changes from a capped row list to per-producer lines with total counts, read only by this template.

## Security Considerations
- Input validation and injection: display names, reasons and summaries render through autoescaping or `textContent`; no `| safe` is added (the retirement panel's existing pin stays).
- Authorization and access control: no route changes; every retirement POST stays behind the auth and CSRF hooks.
- Data exposure: the Composer hash moves to `title` attributes only.
- Client-side rendering: the tab-selection fragment is read against the known tab names only; an unknown fragment opens the Overview.
- Abuse or rate limits: Run now's market gate and single-flight lock are unchanged.

## Design-System Mapping
| Component | Element | Required primitive or token | Notes |
|---|---|---|---|
| Retirement tab | run-summary tiles | `.mini-stats`, `.mini-stat`, `.mini-stat-value`, `.mini-stat-label` (`templates/index.html:277-305`) | defined only on the dashboard at the ref; this plan moves the dashboard primitives into `static/layout.css` with identical values, and plan 04 applies them to the other pages (overview SD-6) |
| Retirement tab | last-run stamp | `.legend-as-of` (`templates/index.html:174`) | time in ET |
| Retirement tab | recommendation card | `.sym-card`, `.card-name` (`templates/index.html:323-350`) | names as plan 01 sets |
| Retirement tab | approval state | `.status-pill` with existing modifiers (`templates/index.html:352`) | pending, approved, rejected map to existing modifiers; no new token |
| Retirement tab | Run now | the page's existing primary-or-secondary button class `.run-btn` (`templates/ai_advisor.html:1523`) | replaces the ghost `.discuss-btn` |
| Overview | Retirement pointer | `.mini-stat` | one tile |
| Overview | producer status line | `.section-header` label plus body text in `--studio-ink-dim` | expander uses `details` |

## Definition of Done (Verification)
| AC | Scenarios | Test level | Real-entrypoint test |
|---|---|---|---|
| AC-1 | S-1.1, S-1.2, S-1.3, S-1.4, S-1.5, S-1.6, S-1.7, S-1.8, S-1.9, S-1.10 | template render with captured observation rows; route test with a failing accessor | `GET /ai-advisor`, asserting each status line is a descendant of `.page-wrap` and the Overview tab panel |
| AC-2 | S-2.1, S-2.2, S-2.3, S-2.4, S-2.5, S-2.6, S-2.7 | `loadRecentRuns` renderer under node with captured rows | live render of `/ai-advisor` |
| AC-3 | S-3.1, S-3.2, S-3.3, S-3.4, S-3.5, S-3.6 | template render; sparkline under node | live render in a browser with and without the CDN reachable |
| AC-4 | S-4.1, S-4.2, S-4.3, S-4.4, S-4.5, S-4.6, S-4.7, S-4.8, S-4.9, S-4.10, S-4.11, S-4.12, S-4.13, S-4.14, S-4.15, S-4.16, S-4.17, S-4.18, S-4.19 | template render with captured summary rows per status; `retirementRunNow` and `retDispatchDecision` under node; for S-4.18, a comparison seam over the server-rendered `GET /` HTML (element structure and classes) plus the CSS rules that resolve on the extracted primitives' selectors, taken at this plan's base and at its head | live render of `/ai-advisor`, Run now clicked outside market hours and during them; `GET /` at base and head for S-4.18 |
| AC-5 | S-5.1, S-5.2, S-5.3, S-5.4, S-5.5 | template render | `GET /ai-advisor` |
| AC-6 | S-6.1, S-6.2, S-6.3, S-6.4 | template render order | live render at 1440x900 |
| AC-7 | S-7.1, S-7.2, S-7.3, S-7.4, S-7.5, S-7.6 | the shared reason mapping as a unit; template render | `GET /ai-advisor` |
| AC-8 | S-8.1, S-8.2, S-8.3, S-8.4, S-8.5, S-8.6, S-8.7, S-8.8, S-8.9, S-8.10 | time formatter as a unit in both Python and JavaScript; label scan of the rendered page | `GET /ai-advisor` |
| AC-9 | S-9.1, S-9.2, S-9.3 | template render | `GET /ai-advisor` |

Gates: `ruff format --check`; `ruff check`; a scoped pytest run of `tests/ai_advisor`, `tests/app`, `tests/security`, `tests/ui` and `tests/js_syntax` with at most 4 workers, never `-n auto`; CI green on the exact SHA; after `/review`, the PM's live check on the droplet of T-1 to T-5, Run now outside market hours.
Fixture provenance: retirement summary rows for each status (`ok`, `series_unavailable`, `insufficient_symphonies`, `internal_error`), a recommendation row per approval state, observation rows for two producers and autotune rows are captured from the production database copy the audit used, one record per case, with real names and Composer ids kept and no account ids (overview SD-11). A record the committed captures lack is added by the PM as a real capture on `ux-overhaul/fixtures` or produced by the real producer functions; the S-1.7 record (a latest row with an empty verdict) is a PM-signed derived record (overview SD-9).
Screenshots: the Overview and Retirement tabs at 1440x900 and 375x812, light and dark, compared with the audit's captures of the same surfaces (A-OV-1 to A-OV-5 series) and with the dashboard reference. The dashboard `/` at 1440 light, 1440 dark and 375 light, captured by the UX gate at this plan's head and compared with a capture of this plan's own base, never with the audit's captures; both captures are taken only after the suite's SC-1 readiness signal, `body[data-dashboard-ready="1"]`, because the audit's dashboard captures include frames taken before the charts populated (S-4.18).

## Known-Unconfirmed and Standing Risk
- Whether the droplet runs the ref (Cycle 2e, PR #145, merged 2026-10-22): the operator's message was sent the same day; if the droplet runs an earlier build, the Retirement panel there has no last-run block at all. Read-only check: `git rev-parse HEAD` in the droplet's application directory.
- Moving `retirement-recommendations-panel` and `advisor-observations-section` breaks tests that locate them under the Overview tab (3 and 2 test files at the ref reference these ids); DC-4 carries the search.
- The approved Symphony Tenure Review plan (branch `feat/tenure-review-c1`) adds tenure verdicts to the same panel; there is no ordering dependency, and whichever lands second resolves the overlap in its own merge (Q-1).

## Open Questions
| Q-n | Question | Blocks (AC ids or "plan") | Proposed default | Answer |
|---|---|---|---|---|
| Q-1 | The Symphony Tenure Review plan (status ready, branch `feat/tenure-review-c1`) places its review on the Overview's Retirement panel. Which lands first, and does that plan follow the panel to the Retirement tab? | AC-4 | This suite lands first, and the tenure review renders on the Retirement tab | PM: no ordering dependency on the tenure review (another session's unmerged work); this plan keeps every existing test id of the Retirement section so the tenure review's selectors still resolve, and whichever lands second resolves the overlap in its own merge |
| Q-2 | Where does the Retirement tab sit in the tab bar? | AC-4 | Directly after Correlations | default approved by the PM, [PM-ASSUMED], operator may override |
| Q-3 | Is the Overview order (Retirement pointer, advisor controls and autotune, Market Prism, observations) right until Claude Design B3? | AC-6 | Yes | default approved by the PM, [PM-ASSUMED], operator may override |

## Decisions
| ID | Decision | Rationale |
|---|---|---|
| DEC-1 | Retirement times display in ET, like the dashboard's stamps | SD-2; the panel printed UTC while every dashboard stamp reads ET |
| DEC-2 | The tab selection survives a reload through the URL fragment, and an unknown fragment opens the Overview | Run now and Approve both end in a reload |
| PM-1 | The exclusion-reason sentences are one callable, rendered through by this plan's Retirement tab and plan 03's excluded list | PM ruling on the Gate-2 approaches |
| PM-2 | The ET time formatter of AC-8 is the one plan 03 uses for its as-of | PM ruling on the Gate-2 approaches |
| PM-3 | The dashboard primitives live in `static/layout.css` | PM ruling on the Gate-2 approaches |
| PM-4 | The URL fragment naming the tab keeps it across reloads | PM ruling on the Gate-2 approaches |
| PM-5 | The autotune expander's label states the route's 50-run cap | PM ruling on the Gate-2 approaches |
| PM-8 | Plan 02's PR targets main once plans 05 and 01 are on main; screenshots of `/` wait for SC-1 (`body[data-dashboard-ready="1"]`); Q-1 is answered with no ordering dependency and every Retirement test id kept; missing fixtures are real captures or real producer output, S-1.7's record PM-signed | PM rulings on the suite approach |
| PM-7 | Cycles C1 to C3 branch from `ux-overhaul/05`; C4 and C5 branch after plan 01 is merged into this plan's branch; C6 follows; one PR, merge commits only | Suite approach ruling: C4 and C5 edit the same code as plan 01 |
| PM-6 | The retirement no-trade transitive scan is extended to this plan's new retirement helpers (NB-6) | PM ruling: safety coverage beats freezing the test file |
| PM-A | Plan 02 approved at Step 7 under the project's standing autonomy directive (operator unavailable), with its Gate-2 approach `feature-plans/ux-overhaul/approach/02-advisor-overview.md` and the suite approach `feature-plans/ux-overhaul/approach/00-suite.md`; every open question takes its default or its recorded ruling, and every Not Building entry is approved | [PM-ASSUMED]; the operator may override |

## Docs to Update
- `docs/generated/static_ai_advisor_js.md`, `docs/generated/app.md` (the Advisor page sections), `docs/generated/INDEX.md`.
- A `DECISIONS.md` entry appended at ship by the doc writer.
- A drafted `.claude/CLAUDE.md` Key Files row update for `templates/ai_advisor.html` and `static/ai_advisor.js`, for PM approval.

## Follow-Ups
- The combined view (NB-1): Claude Design B2, overview NB-5.
- The Prism layout (NB-2): Claude Design B3, overview NB-8.
- A Discuss entry point on correlation cells (NB-3): tracked with plan 03's follow-ups.
