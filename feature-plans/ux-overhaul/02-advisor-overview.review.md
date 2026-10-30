# Approval packet: AI Advisor Overview order and bounds, and a Retirement tab

## What is being built

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

## Acceptance criteria and scenarios

### AC-1: Observations sit in the Overview tab as one status line per producer
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

Negative coverage: N1 S-1.7, S-1.10 | N2 n/a: display only | N3 S-1.4, S-1.5 | N4 n/a: the page is behind the global auth hook, unchanged | N5 S-1.8 | N6 S-1.9 | N7 S-1.6 | N8 n/a: the expander is not an edit

### AC-2: The autotune list shows the latest run per symphony
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

Negative coverage: N1 S-2.2, S-2.4 | N2 n/a: display only | N3 S-2.3 | N4 n/a: the route is behind the global auth hook, unchanged | N5 S-2.5 | N6 S-2.7 | N7 S-2.6 | N8 n/a: no edit state

### AC-3: The autotune sparkline draws on the Advisor page
Rule: When `/ai-advisor` loads, the page shall load the same Chart.js script the dashboard loads, so the autotune sparkline draws from the runs' best objective scores (`naive_sharpe`), and if Chart.js cannot load, the sparkline canvas shall not show.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-3.1 | B | runs with best objective scores | the Overview loads | the canvas holds a chart whose point count equals the number of runs with a value |
| S-3.2 | N3 | runs whose best objective scores are all null | the Overview loads | the canvas does not show |
| S-3.3 | N6 | the Chart.js script fails to load | the Overview loads | the canvas does not show and the run cards still render |
| S-3.4 | N7 | the page | it loads | the script tag's source equals `templates/index.html:11`'s |
| S-3.5 | N5 | the 15 s poll re-renders | the sparkline redraws | one chart exists on the canvas |
| S-3.6 | N1 | exactly one run with a value | the Overview loads | the chart draws that point |

Negative coverage: N1 S-3.6 | N2 n/a: nothing is refused | N3 S-3.2 | N4 n/a: page behind the global auth hook, unchanged | N5 S-3.5 | N6 S-3.3 | N7 S-3.4 | N8 n/a: no edit state

### AC-4: Retirement is its own tab, built from the dashboard's primitives
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

Negative coverage: N1 S-4.14 | N2 S-4.4 | N3 S-4.8, S-4.9, S-4.10, S-4.11, S-4.12, S-4.13 | N4 n/a: every retirement route is behind the global auth and CSRF hooks, unchanged | N5 S-4.5 | N6 S-4.17 | N7 S-4.15, S-4.18, S-4.19 | N8 S-4.16

### AC-5: The Overview carries a one-line Retirement pointer
Rule: When the Overview renders, it shall show one Retirement line in the `.mini-stat` form stating the last run's time in ET and its recommendation count, or that no run has completed, and selecting it shall open the Retirement tab.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-5.1 | B | a successful run with 2 pending recommendations | the Overview renders | the line reads the run time in ET and "2 recommendations, 2 pending" |
| S-5.2 | B | the line | the operator selects it | the Retirement tab opens |
| S-5.3 | N3 | no run has completed | the Overview renders | the line reads "Retirement: not run yet" |
| S-5.4 | N3 | the latest run failed | the Overview renders | the line names the failure state and the time |
| S-5.5 | N7 | any state | the Overview renders | the pointer issues no request beyond those the page makes at the ref |

Negative coverage: N1 n/a: the pointer shows counts the summary already validated | N2 n/a: display only | N3 S-5.3, S-5.4 | N4 n/a: page behind the global auth hook, unchanged | N5 n/a: selecting the pointer twice opens the same tab | N6 n/a: it reads the same summary as AC-4, whose failure path is S-4.17 | N7 S-5.5 | N8 n/a: no edit state

### AC-6: The Overview's first screen shows actionable items first
Rule: When the Overview renders, its sections shall appear in this order: the Retirement pointer, the advisor controls with the suggestions and autotune panel, the Market Prism block, then the observations status lines.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-6.1 | B | a full data set | the Overview renders at 1440x900 | the Retirement pointer and the advisor controls are inside the first 900 px |
| S-6.2 | B | the same | the Overview renders | the Market Prism block follows the advisor grid and precedes the observations |
| S-6.3 | N3 | no Market Prism row | the Overview renders | the Prism empty state renders in the same position |
| S-6.4 | N7 | any data | the Overview renders | the Prism block's lenses, sources and verification render as at the ref apart from plan 01's pill and this plan's times |

Negative coverage: N1 n/a: order does not depend on input values | N2 n/a: display only | N3 S-6.3 | N4 n/a: page behind the global auth hook, unchanged | N5 n/a: the order is server-rendered once per load | N6 n/a: each section keeps its own failure state, covered by S-1.9, S-2.7 | N7 S-6.4 | N8 n/a: no edit state

### AC-7: Exclusion reasons read as sentences
Rule: When the Retirement tab lists a symphony excluded from a run, it shall show the symphony's display name and a sentence for its reason rendered through one callable that plan 03's excluded list also renders through: `no_account` as "No Composer account is recorded for this symphony.", `series_too_short` as "Its Composer series has fewer than 2 daily returns.", `fetch_error: ` followed by a code as "Its Composer series could not be fetched (" the code ").", and `shadow_series_unavailable` as "No shadow history is available for it."

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-7.1 | B | an exclusion with reason `series_too_short` | the tab renders | the entry reads the display name and "Its Composer series has fewer than 2 daily returns." |
| S-7.2 | N1 | reasons `no_account`, `fetch_error: HTTP 429` and `shadow_series_unavailable` | the tab renders | each reads its sentence; the fetch error names HTTP 429 |
| S-7.3 | N3 | a reason not in the mapping | the tab renders | the entry reads "Excluded: " followed by the raw reason |
| S-7.4 | N3 | an empty reason | the tab renders | the entry reads the display name and "Reason not recorded." |
| S-7.5 | N1 | a symphony no longer in the live state | the tab renders | the entry reads the Composer hash with its sentence |
| S-7.6 | N7 | any exclusion | the tab renders | the stored summary row is unchanged |

Negative coverage: N1 S-7.2, S-7.5 | N2 n/a: display only | N3 S-7.3, S-7.4 | N4 n/a: page behind the global auth hook, unchanged | N5 n/a: server-rendered once per load | N6 n/a: the list reads the already-fetched summary | N7 S-7.6 | N8 n/a: no edit state

### AC-8: Overview times name their zone, pre-run chips are gone, the label is linked
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

Negative coverage: N1 S-8.8 | N2 n/a: display only | N3 S-8.4, S-8.5 | N4 n/a: page behind the global auth hook, unchanged | N5 S-8.10 | N6 n/a: a failed suggestion run leaves the chips hidden, the same state as S-8.6 | N7 S-8.9 | N8 n/a: no edit state

### AC-9: Chat's example prompts name only flows the page can start
Rule: When the Chat tab renders its example questions, each shall name an item that carries a Discuss entry point on the page, and no example shall name correlations while no correlation element has one.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-9.1 | B | the page at this plan | the Chat tab renders | no example mentions "correlation"; every example names a swap, a logic change or a Strategy Builder proposal |
| S-9.2 | N7 | the Chat panel | it opens from a Discuss button | its explain-only notice and behaviour are unchanged |
| S-9.3 | N3 | Chat unavailable (no key) | the Chat tab renders | the examples still render and the panel's unavailable state is unchanged |

Negative coverage: N1 n/a: static copy | N2 n/a: copy only | N3 S-9.3 | N4 n/a: page behind the global auth hook, unchanged | N5 n/a: static copy | N6 n/a: no request | N7 S-9.2 | N8 n/a: no edit state

## Rule-by-entry-point matrix

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

## Narrowed mappings

R-5: Every page and tab looks right; maps to AC-1, AC-2, AC-4, AC-6; left out: this plan's part is the Overview and Retirement tabs; page-wide primitives are plan 04's, the Overview information architecture is Claude Design B3
R-6: Every control on every page functions; maps to AC-3, AC-4; left out: this plan's part is the autotune sparkline, the Retirement tab's Run now, Approve and Reject, and the tab staying selected through a reload
R-10: Every page and tab renders honestly; maps to AC-1, AC-7, AC-8, AC-9; left out: this plan's surfaces only
R-11: The overhaul covers every page, the modal, the Advisor tabs and the header; maps to AC-1, AC-4, AC-9; left out: the Advisor Overview, the new Retirement tab and the Chat tab
R-45: The Overview puts actionable items before reference material; maps to AC-6; left out: the Overview information architecture design (B3) and the Prism five-lens layout are left out
R-50: Exclusion reasons render as sentences; maps to AC-7; left out: the Retirement tab's part; the Correlations matrix's part is plan 03's

## User tasks

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

## Open questions

| Q-n | Question | Blocks (AC ids or "plan") | Proposed default | Answer |
|---|---|---|---|---|
| Q-1 | The Symphony Tenure Review plan (status ready, branch `feat/tenure-review-c1`) places its review on the Overview's Retirement panel. Which lands first, and does that plan follow the panel to the Retirement tab? | AC-4 | This suite lands first, and the tenure review renders on the Retirement tab | PM: no ordering dependency on the tenure review (another session's unmerged work); this plan keeps every existing test id of the Retirement section so the tenure review's selectors still resolve, and whichever lands second resolves the overlap in its own merge |
| Q-2 | Where does the Retirement tab sit in the tab bar? | AC-4 | Directly after Correlations | default approved by the PM, [PM-ASSUMED], operator may override |
| Q-3 | Is the Overview order (Retirement pointer, advisor controls and autotune, Market Prism, observations) right until Claude Design B3? | AC-6 | Yes | default approved by the PM, [PM-ASSUMED], operator may override |
