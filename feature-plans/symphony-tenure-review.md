# Feature: Symphony Tenure Review
Status: ready
Story: none: the request is quoted whole in Requirements as Given
Type: feat
Suite: none
Repos: AlphaBotPM
Base: main
Ref: AlphaBotPM@ed7f43ce5f9f5a6901eee3b030d3dced576df28b
Sensitive: money, security (AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-8, AC-9, AC-10)
Depends on: none

## Trigger
The operator is preparing an end-of-year analysis of the live symphonies and asked for a PlanetStopper feature that opens each symphony to a keep-or-retire analysis once it has been live a set number of trading days, built on "the correlation analysis that seemingly never got finished". The request is quoted below in Requirements as Given.

This plan reads that correlation analysis as the shipped Retirement Recommender (`advisors/retirement_recommender.py`, `advisors/correlation_diagnostic.py`, decisions `DE-RETIRE-CORE-001`, `DE-RETIRE-APPROVAL-001`, `DE-RETIRE-POLISH-001`); Q-16 asks the operator to confirm it. Its nightly 03:45 tick is registered (`app.py:1254`) and runs, but it has never produced a recommendation. The code shows why: its only history is PlanetStopper's own shadow history (`advisors/retirement_recommender.py:470-476`, `analytics.py:1882-1891`), and its structural-redundancy gate needs ceil(0.05 x aligned days) of at least 10 stressed days (`advisors/retirement_recommender.py:74`, `:83`, `:386-389`), which first holds at 181 aligned days. PM live reads (reported by the PM, not re-run by this spec): production shadow history holds 84 distinct trading days (one symphony 28), and production holds zero `RETIREMENT_RECOMMENDATION` observation rows and zero `retirement_decisions` rows. Composer's account-scoped realized daily series reach each symphony's invested-since date (`docs/audit/PERF-WINDOW-TRUTH-2026-09-03.md` evidence E-24: 538 to 539 daily points back to 2024-07-12 on the credentials already in use).

## Summary
Every live symphony gets a tenure-gated keep, watch or retire-candidate review with its evidence, which the operator reads on the AI Advisor Overview tab's Retirement Recommendations panel and in `GET /api/retirement-recommendations`, refreshed by the existing nightly retirement tick and on demand.
- Tenure in NYSE trading days and eligibility per live symphony, with no verdict below the threshold (AC-1).
- Realized Composer history as the evidence basis, with shadow-history fallback and a basis, unit, window, subject and source label on every figure (AC-2).
- The existing correlation screen and both gates rerun on that longer history, each gate result shown in full (AC-3).
- Realized performance since invested, and the existing composite over a common window (AC-4).
- Realized result against a backtest of the symphony's own current definition over the same window (AC-5).
- Marginal contribution: portfolio return and drawdown with and without the symphony (AC-6).
- Long-VIX co-firing as information only (AC-7).
- One verdict per eligible symphony, with only a retire-candidate entering the existing approve/reject lifecycle (AC-8).
- Nightly and on-demand runs, one run at a time, showing only the latest completed run (AC-9).
- No trade, Composer write or execution-engine path anywhere in the feature (AC-10).

## Requirements as Given
[G-1] Great, I'm also getting ready to do some EoY analysis on the, can you go ahead and let's get it /scaffolded and integrated into PlanetStopper?
[G-2] After a synphony has had a certain number of trading days it should be open for analysis about whether or not to keep it; the correlation analysis that seemingly never got finished should act as a great starting point for a feature like this

## Requirement Inventory
No intake answers were given; every intake item became an Open Questions row with a proposed default, and the operator approved every default; the body applies them.

| R-n | Requirement item | Source (G ids) | Maps to (AC ids, or NB-n) | Scope (full, or narrowed: what is left out) |
|---|---|---|---|---|
| R-1 | The feature supports the operator's end-of-year analysis | G-1 | AC-8, AC-9, NB-5, NB-8 | narrowed: no export file and no calendar-year window selector; the review covers each symphony's tenure window |
| R-2 | The capability is integrated into PlanetStopper itself, on its existing surfaces | G-1 | AC-1, AC-9 | full |
| R-3 | removed: "get it /scaffolded" is a process instruction that this plan itself satisfies; it describes no product behavior | G-1 | none | full |
| R-4 | A symphony becomes open for analysis only after a set number of trading days | G-2 | AC-1 | full |
| R-5 | The analysis answers whether or not to keep the symphony | G-2 | AC-3, AC-4, AC-5, AC-6, AC-7, AC-8 | full |
| R-6 | The unfinished correlation analysis is the starting point and is made to produce results | G-2 | AC-2, AC-3 | full |

## User Tasks
Workflow (Overview tab, Retirement Recommendations panel): no workflow file: this repository has no `.claude/workflows/` directory.

### T-1: Run the end-of-year review now
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Open the AI Advisor page and stay on the Overview tab | none | The Retirement Recommendations panel shows the latest completed review with its as-of trading day and run time, or the "no review yet" state | S-1.1, S-1.11 |
| 2 | Choose "Run review now" | none | The button disables and the panel says the review started | S-9.2, S-9.12 |
| 3 | Reload the page after the run finishes | none | The panel shows one row per live symphony with the new run time; rows from earlier runs are gone | S-9.2, S-9.6 |
| 4 | Choose "Run review now" while a review is still running | none | The panel says a review is already running; no second run starts | S-9.3 |

### T-2: Decide on a retire-candidate
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Read a retire-candidate row | none | The row shows verdict, tenure, every evidence item with its basis label, the explanation with the date it was written, and the Pending status with Approve and Reject | S-8.1 |
| 2 | Choose Approve retirement | none | The status reads Approved with "reload this page to view the wind-down checklist", and nothing is traded | S-8.9 |
| 3 | Reload the page | none | The row shows Approved and the manual wind-down checklist | S-8.9, S-8.11 |

### T-3: Check a symphony that is not yet eligible
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Read a young symphony's row | none | The row shows its tenure, "not yet eligible" with the trading days remaining, and no verdict or decision buttons | S-1.3 |

## Earlier criteria on the same surface
Searched `feature-plans/*.md` (including the `.completed.md` and `.obsolete.md` families and suite folders), `DECISIONS.md`, `docs/generated/*.md`, `docs/audit/`, `docs/audit-inputs/` and the guard tests for the Retirement Recommendations panel, `GET /api/retirement-recommendations`, the 03:45 tick, `RETIREMENT_RECOMMENDATION` rows, `retirement_decisions`, Composer historical series, and the engine freeze.

| Earlier criterion (plan and AC) | Surface | Disposition (kept, changed or contradicted) | Why |
|---|---|---|---|
| `feature-plans/retirement-recommender-core.md` AC-1 (0.65 screen over continuous daily returns) | recommender screen | kept | Threshold unchanged (`advisors/retirement_recommender.py:44`); only the history it reads changes (AC-3) |
| `feature-plans/retirement-recommender-core.md` AC-2 and `DE-RETIRE-CORE-001` ("Live-only, no backtest/synthetic-history sourcing. Both the screen and the composite metrics are computed over each symphony's own live `shadow_history`", `DECISIONS.md:10591`; 250-day lookback, `advisors/retirement_recommender.py:89`) | recommender basis | contradicted | Both halves: AC-2 makes Composer's realized account series the primary basis with shadow history as fallback and the tenure as the window (Q-2, Q-4); AC-5 adds a backtest and AC-8 uses its gap in the watch rule (Q-14). The backtest never enters the screen, the gates or the composite (S-5.9) |
| `feature-plans/retirement-recommender-core.md` AC-3 (composite normalized across the current fleet over the shared lookback) | recommender ranking | changed | Weights 0.40/0.20/0.15/0.15/0.10 unchanged (`advisors/retirement_recommender.py:94-98`); the population becomes eligible symphonies on the Composer basis and the window the latest threshold-many trading days (AC-4, Q-13) |
| `feature-plans/retirement-recommender-core.md` AC-4 (lower composite is the candidate) | recommender selection | kept | `advisors/retirement_recommender.py:239-291` |
| `feature-plans/retirement-recommender-core.md` AC-5, AC-6 (uncertainty and structural-redundancy gates) | recommender gates | kept | Constants unchanged (`advisors/retirement_recommender.py:51`, `:60`, `:68`, `:74`, `:83`) |
| `feature-plans/retirement-recommender-core.md` AC-7 and `feature-plans/retirement-approval-lifecycle.md` AC-8 (no-trade source scans, including "never imports `alpha_bot_execution`" for the recommender module) | safety boundary | changed | Extended to every new path; the recommender module keeps its no-engine-import rule; new review paths may use only the engine module's Composer base URL and header builder (AC-10) |
| `feature-plans/retirement-recommender-core.md` AC-8 (one advisory row per recommendation, verdict `retire_candidate`) | advisory store | kept | Retire-candidates still persist as today; the review adds append-only rows beside them (AC-9) |
| `feature-plans/retirement-recommender-core.md` AC-9 (read-only API, honest empty state) | `GET /api/retirement-recommendations` | changed | The response gains the review entries; the existing `recommendations` list keeps its fields; GET still never writes or calls Composer (AC-1, AC-2) |
| `feature-plans/retirement-recommender-core.md` AC-10 (panel lists only flagged recommendations, no controls) | Retirement Recommendations panel | changed | Already changed by `feature-plans/retirement-approval-lifecycle.md` AC-7; now one row per live symphony plus a "Run review now" control (AC-1, AC-9) |
| `feature-plans/retirement-recommender-core.md` AC-11 as corrected by `DE-RETIRE-CORE-001` Revise round 2 F1 (fail-closed when either pair member is ineligible) | recommender selection | kept | `advisors/retirement_recommender.py:277-278` |
| `DE-RETIRE-CORE-001` Revise round 2 F2 accepted limitation (an "insufficient data" versus "not redundant" distinction is a tracked follow-up, `DECISIONS.md:10697`) | recommender gates | changed | AC-3 shows "insufficient data", "undefined" and "not evaluated" distinct from "not met" |
| `DE-RETIRE-CORE-001` Revise round 2 F4 (latest-night filter by UTC date) | API and panel | changed | Two runs on one UTC date would both pass the filter (`app.py:4286-4293`; `migrations/017_advisor_observations.sql:12` stamps UTC); AC-9 requires only the latest completed run. Pinned today by `tests/app/test_retirement_recommendations_route.py:176-230` |
| `feature-plans/retirement-approval-lifecycle.md` AC-2 (explainer runs at producer time; recommender module stays LLM-free, golden-hash pinned) | producer tick | kept | On-demand runs are producer time too; the LLM stays off render and approve paths. The byte pin (`tests/app/test_retirement_producer_explainer_wiring.py:54-56`, `:80-95`) may need re-baselining if Gate 2 edits the module, a Gate-2 decision |
| `feature-plans/retirement-approval-lifecycle.md` AC-3, AC-4 (`retirement_decisions` keyed by candidate Composer hash, live-joined) | decision store | kept | `migrations/038_retirement_decisions.sql`; `app.py:4119-4148` |
| `feature-plans/retirement-approval-lifecycle.md` AC-5 (status-only approve/reject routes) | approve and reject routes | kept | `app.py:7913-7982` unchanged; Q-11 asks whether to gate them |
| `feature-plans/retirement-approval-lifecycle.md` AC-6 (deterministic checklist built at render for approved cards) | checklist | kept | `advisors/retirement_checklist.py` and `app.py:6655-6674` unchanged; after Approve the panel still says to reload (`static/ai_advisor.js:1141-1143`), S-8.9 |
| `feature-plans/retirement-approval-lifecycle.md` AC-7 (buttons only while pending; checklist on approved) | panel row | changed | Buttons appear only on a pending retire-candidate row; a prior decision stays visible whatever the current verdict (AC-8, Q-11) |
| `feature-plans/retirement-approval-polish.md` AC-4 and `feature-plans/retirement-approval-polish-2d.md` AC-2 (explanation reused when the whole rounded evidence is unchanged; "bounded, non-recurring metered spend") | producer tick | changed | With a growing tenure window the day count changes every trading day and is compared exactly (`app.py:983`, `:1016-1018`), so reuse would never fire. AC-9 keeps the spend bound by leaving window end and day counts out of the reuse comparison and dating each explanation (S-9.10, S-9.17, Q-15) |
| `feature-plans/retirement-approval-polish.md` AC-1 to AC-3, AC-5 to AC-8 and `feature-plans/retirement-approval-polish-2d.md` AC-1, AC-3 to AC-6 (display names, `decided_at`, API approval status, button robustness) | producer, API, panel | kept | Behavior unchanged |
| `feature-plans/mdd-window-truth.md` Scope Boundaries OUT ("Composer daily-series integration ... separate cycle, and any use must provenance-tag") and amended AC-2 (`invested_since` is not persisted; no live Composer call on the render path) | Composer series, render path | kept | This plan is that cycle; AC-2 labels provenance, and S-2.13 keeps Composer off the render and GET paths |
| `feature-plans/mdd-window-truth.md` AC-7 guards: engine content pins (`tests/test_mdd_window_truth_zero_diff_guard.py:68-71`) and highest migration number 38 (`:157`) | engine freeze, migrations | kept | S-10.5 relies on the engine pins; any new migration must update the migration-number guard (Migrations Required) |
| `DE-PERF-WINDOW-TRUTH-001` E-22 (a longer live series extends the live leg only) | guard-alpha comparison | kept | NB-7: no guard-alpha figure is extended before the go-live floor |

## Acceptance Criteria (Building)

### AC-1: Tenure and eligibility for every live symphony
Requirement items: R-2, R-4
Rule: When a review run completes, the Retirement Recommendations panel and `GET /api/retirement-recommendations` shall list every live symphony (a symphony present in this run's Composer symphony-statistics response for a configured account; keyed by its Composer hash, labelled by its display name) with its tenure in NYSE trading days from its current deployment's invested-since date through the run's as-of trading day (the latest NYSE trading day whose regular session had closed, in US Eastern time, when the run started), both inclusive, and its eligibility, and shall give a verdict only to a symphony whose tenure is at least the configured threshold (default 126).

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-1.1 | B | live symphony "Copper Ridge Rotation" (Composer hash Qz7Lm2Rt9Wx4Kp1Nv8Ys) with invested-since 2025-03-03, threshold 126 | the nightly review completes and the operator opens the Overview tab | its row shows the display name, tenure as a whole number of trading days at least 126, "eligible", a verdict, the as-of trading day and the run time; the API entry for that Composer hash carries the same tenure and eligibility |
| S-1.2 | N1 | a symphony whose tenure equals the threshold exactly | the review completes | the row shows "eligible" and a verdict |
| S-1.3 | N1 | a symphony whose tenure is one less than the threshold | the nightly review completes | the row and API entry show "not yet eligible: 1 trading day remaining", no verdict, no Approve or Reject; the symphony still appears as a sibling in other symphonies' redundancy evidence |
| S-1.4 | N1 | a symphony whose tenure is below the threshold | an on-demand review completes | the row and API entry show "not yet eligible" with the days remaining (threshold minus tenure) and no verdict |
| S-1.5 | N1 | the threshold setting is absent | a review completes | the threshold in effect is 126 and the panel states it |
| S-1.6 | N1 | the threshold setting is a non-numeric value | a review completes | the threshold in effect is 126 and the panel states "configured threshold invalid; 126 in use" |
| S-1.7 | N1 | the threshold setting is a decimal value | a review completes | the threshold in effect is 126 and the panel states "configured threshold invalid; 126 in use" |
| S-1.8 | N1 | the threshold setting is zero | a review completes | the threshold in effect is 126 and the panel states "configured threshold invalid; 126 in use" |
| S-1.9 | N1 | the threshold setting is negative | a review completes | the threshold in effect is 126 and the panel states "configured threshold invalid; 126 in use" |
| S-1.10 | N3 | a live symphony whose invested-since date Composer does not report | a review completes | its row shows "tenure unavailable: Composer did not report an invested-since date", no verdict; no tenure is shown as 0 |
| S-1.11 | N3 | no review run has ever completed | the operator opens the Overview tab and requests the API | the panel shows "No review yet" and the API returns an empty review list with status 200 |
| S-1.12 | N6 | the Composer symphony-statistics request fails with HTTP 4xx, 5xx or a timeout for every configured account | a run is attempted | the run does not complete and writes no review rows; the panel and API keep the previous completed run, and the panel states "latest review attempt failed: Composer statistics unavailable" with the attempt time |
| S-1.13 | N7 | a symphony renamed in Composer since the last run, same Composer hash | the next review completes | the row shows the new display name and the same tenure basis (same invested-since) |
| S-1.14 | N7 | Composer reports a later invested-since date for the same Composer hash than the previous run did | the next review completes | tenure counts from the later date |
| S-1.15 | N7 | a symphony still present in the engine's state but absent from Composer's statistics response, with an approved decision row | the next review completes | the symphony has no row in the panel or API review; its decision row is unchanged |
| S-1.16 | N4 | an unauthenticated request | it requests `GET /api/retirement-recommendations` | status 401 with the unauthenticated error body; nothing is read or written |
| S-1.17 | N4 | an unauthenticated browser | it opens the AI Advisor page | redirect to the login page; no panel content is rendered |
| S-1.18 | N6 | the statistics request succeeds for one configured account and fails for another | a run is attempted | the run does not complete and writes no review rows; the previous completed run stays shown with the failed-attempt notice |
| S-1.19 | N6 | the statistics request returns HTTP 200 with a body that cannot be read as the expected JSON | a run is attempted | the run does not complete and writes no review rows; the previous completed run stays shown with the failed-attempt notice |
| S-1.20 | N1 | the nightly tick fires at 03:45 on a Sunday | the run completes | the as-of trading day is the latest trading day before that Sunday, and tenure counts through it |
| S-1.21 | N1 | an on-demand run starts at 11:00 US Eastern on a trading day | the run completes | the as-of trading day is the previous trading day; the current day is not counted |
| S-1.22 | N1 | an on-demand run starts after the regular-session close on a trading day | the run completes | the as-of trading day is that day and it is counted |
| S-1.23 | N4 | the dashboard secret key or credential is not configured | the API and the AI Advisor page are requested | the API answers 503 with the misconfigured error body and the page redirects to login; nothing is read or written |

Negative coverage: N1 S-1.2, S-1.3, S-1.4, S-1.5, S-1.6, S-1.7, S-1.8, S-1.9, S-1.20, S-1.21, S-1.22 · N2 n/a: this rule refuses no input; an ineligible symphony is shown, not refused · N3 S-1.10, S-1.11 · N4 S-1.16, S-1.17, S-1.23 · N5 covered by AC-9 (S-9.3, S-9.4, S-9.5) · N6 S-1.12, S-1.18, S-1.19 · N7 S-1.13, S-1.14, S-1.15 · N8 n/a: the rows are read-only, with no edit held open
Questions: Q-1, Q-7, Q-12, Q-16, Q-17, Q-18

### AC-2: Realized basis and provenance on every figure
Requirement items: R-6
Rule: When a review run computes an evidence figure for a symphony, the review shall compute it from that symphony's Composer account-scoped realized daily series, deposit-adjusted, from its invested-since date, and shall label the figure on the panel and in the API with its basis, unit, window (first date, last date, trading-day count), subject and source; if that series cannot be used, then the review shall use the symphony's PlanetStopper shadow history and label the figure with that basis.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-2.1 | B | the Composer series for a symphony covers its whole tenure | a review completes | every figure for it is labelled "realized: Composer account, deposit-adjusted"; the window's first date equals its invested-since date, its last date equals the as-of trading day, and its day count equals the series points used |
| S-2.2 | N2 | Composer refuses the credentials (HTTP 401 or 403) for the series request | a review completes | the symphony's figures are labelled "PlanetStopper shadow history from the 2026-06-22 go-live floor" and the row states "Composer refused the request"; no figure is labelled Composer |
| S-2.3 | N6 | the Composer series request returns HTTP 404 | a review completes | the shadow-history fallback and its label apply, with reason "Composer has no series for this symphony" |
| S-2.4 | N6 | the Composer series request returns HTTP 429 | a review completes | the shadow-history fallback and its label apply, with reason "Composer rate limit" |
| S-2.5 | N6 | the Composer series request returns HTTP 5xx | a review completes | the shadow-history fallback and its label apply, with reason "Composer unavailable" |
| S-2.6 | N6 | the Composer series request times out or the network fails | a review completes | the shadow-history fallback and its label apply, with reason "Composer unavailable" |
| S-2.7 | N3 | the Composer series is returned empty | a review completes | the shadow-history fallback and its label apply, with reason "Composer series empty" |
| S-2.8 | N3 | the Composer series begins after the symphony's invested-since date | a review completes | figures use the available span; the window's first date is the series' first date and the row states "series begins" with that date and "tenure begins" with the invested-since date |
| S-2.9 | N3 | neither the Composer series nor shadow history exists for a symphony | a review completes | every evidence item for it reads "unavailable: no history"; no figure is shown as 0 |
| S-2.10 | N3 | one day's value in the series is absent or null | a review completes | that day is excluded, never counted as a 0% return; the window's day count excludes it |
| S-2.11 | N1 | one symphony is on the Composer basis and its pair or portfolio peer is on the shadow basis | a review completes | every pairwise or portfolio item combining them reads "unavailable: bases differ" and is not computed |
| S-2.12 | N7 | a review run has completed | the operator opens the Performance tab, the History tab and the dashboard hero | every figure there is unchanged by the run |
| S-2.13 | N7 | a completed review exists | the operator opens the panel and requests the API | no Composer request is made by either; both read the stored run |
| S-2.14 | N6 | the Composer series request returns HTTP 200 with a body that cannot be read as the expected JSON | a review completes | the shadow-history fallback and its label apply, with reason "Composer response unreadable" |

Negative coverage: N1 S-2.11 · N2 S-2.2 · N3 S-2.7, S-2.8, S-2.9, S-2.10 · N4 covered by AC-1 (S-1.16, S-1.17) · N5 covered by AC-9 (S-9.3, S-9.5) · N6 S-2.3, S-2.4, S-2.5, S-2.6, S-2.14 · N7 S-2.12, S-2.13 · N8 n/a: the basis is chosen inside a run, with no edit held open
Questions: Q-2, Q-4

### AC-3: Redundancy evidence on the review history
Requirement items: R-5, R-6
Rule: When a review run evaluates an eligible symphony, the review shall run the existing correlation screen and the uncertainty and structural-redundancy gates, thresholds unchanged, over the review history for each pair with another live symphony, and the panel and API shall show one pair (for a retire-candidate the pair that flagged it; otherwise its highest-correlation pair at or above 0.65) with the sibling's display name, the correlation, its confidence interval, the aligned-day count, the stressed-window correlation and each gate's result as "passed", "not met", "insufficient data", "undefined" or "not evaluated".

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-3.1 | B | an eligible symphony paired with a sibling over at least 181 aligned days, correlation, confidence-interval lower bound and stressed-window correlation each at least 0.65 | a review completes | both gates read "passed"; correlation, interval bounds and stressed correlation are shown to 2 decimal places with the aligned-day count |
| S-3.2 | N1 | a pair with exactly 180 aligned days whose uncertainty gate passes | a review completes | the structural-redundancy gate reads "insufficient data: needs 181 aligned days", not "not met" |
| S-3.3 | N1 | a pair with exactly 181 aligned days whose stressed-window correlation is at least 0.65 | a review completes | the structural-redundancy gate reads "passed" |
| S-3.4 | N1 | a pair with 29 aligned days | a review completes | the uncertainty gate reads "insufficient data: needs 30 aligned days" |
| S-3.5 | N1 | a pair whose correlation is exactly 0.65 | a review completes | the pair counts as a screen hit and its gates are evaluated |
| S-3.6 | N1 | no pair reaches 0.65 | a review completes | the item reads "no redundant sibling", not "unavailable" |
| S-3.7 | N3 | a pair whose correlation is undefined (a zero-variance series) | a review completes | the correlation reads "undefined", never 0, and the pair is not a screen hit |
| S-3.8 | N3 | either symphony's holdings are unavailable at run time | a review completes | holdings overlap reads "unavailable", never 0, and the gate results are unchanged by it |
| S-3.9 | N1 | a correlated sibling that is not yet eligible | a review completes | the pair is evaluated and shown on the eligible symphony's row with the reason "sibling has no composite: not yet eligible"; the pair never makes either member a retire-candidate, the eligible member is at most watch from that pair, and the sibling's own row has no verdict |
| S-3.10 | N7 | the same input series | a review completes | screen, gate and composite constants are the existing values and the gate results equal the existing recommender's on that series |
| S-3.11 | N3 | a pair with at least 181 aligned days whose stressed-window series has zero variance | a review completes | the structural-redundancy gate reads "undefined: stressed-window correlation cannot be computed", never 0 and not "insufficient data" |
| S-3.12 | N1 | a pair whose uncertainty gate is not met | a review completes | the structural-redundancy gate reads "not evaluated: uncertainty gate not met" |
| S-3.13 | B | a retire-candidate whose highest-correlation pair fails the structural gate while a lower-correlation pair passes both gates with it as the lower composite | a review completes | the row shows the lower-correlation pair that flagged it, with both gates "passed" |

Negative coverage: N1 S-3.2, S-3.3, S-3.4, S-3.5, S-3.6, S-3.9, S-3.12 · N2 n/a: no input is refused; thin data reads "insufficient data" · N3 S-3.7, S-3.8, S-3.11 · N4 covered by AC-1 (S-1.16) · N5 covered by AC-9 (S-9.3) · N6 covered by AC-2 (S-2.3 to S-2.6, S-2.14) · N7 S-3.10 · N8 n/a: computed inside a run, with no edit held open
Questions: Q-3, Q-4, Q-19

### AC-4: Realized performance since invested and the composite
Requirement items: R-5
Rule: When a review run evaluates an eligible symphony, the panel and API shall show its realized annualized return and max drawdown in percent, its Sharpe and Sortino as daily non-annualized ratios, and its Calmar ratio over its tenure window, and its composite score from 0 to 1 computed with the existing weights over the common composite window (the latest threshold-many trading days through the as-of trading day) and normalized across the eligible symphonies on the Composer basis that have a value on every day of that window; an eligible symphony with fewer days in the window is excluded from the normalization population, never computed on fewer days, and shows "insufficient coverage in the common window (N of M days)" with its verdict at most watch.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-4.1 | B | an eligible symphony with a usable realized series | a review completes | the five tenure metrics are shown with their units and the AC-2 window label; the composite is between 0 and 1 and carries the composite window label |
| S-4.2 | N3 | a symphony with fewer than 2 finite daily returns in the window | a review completes | every metric and the composite read "not computable", never 0, and it cannot be a retire-candidate |
| S-4.3 | N3 | a symphony whose Calmar is undefined (infinite) | a review completes | Calmar and the composite read "not computable"; the other metrics show values |
| S-4.4 | N1 | a symphony whose series never fell below its prior peak | a review completes | max drawdown reads "0.00%", a computed value, distinct from "not computable" |
| S-4.5 | N1 | every eligible symphony has the same value on one composite metric | a review completes | that metric contributes the neutral midpoint to each composite (`advisors/retirement_recommender.py:230-233`) |
| S-4.6 | N7 | a not-yet-eligible symphony with usable history | a review completes | it is outside the composite population, shows no composite and receives no verdict |
| S-4.7 | N1 | an eligible symphony on the shadow-history fallback basis | a review completes | its composite reads "not comparable: basis differs" and it cannot be a retire-candidate; the other symphonies' composites are computed without it; any pair it belongs to is shown with the reason "sibling has no composite: basis differs", never makes its sibling a retire-candidate, and leaves that sibling at most watch from the pair |
| S-4.8 | B | three eligible symphonies with different invested-since dates, each with a value on every day of the common window | a review completes | every composite carries the same window first and last dates and the same day count |
| S-4.9 | N3 | an eligible symphony whose Composer series starts after the common window's first day | a review completes | it is excluded from the normalization population and shows "insufficient coverage in the common window" with its day count inside the window and the window's day count; it cannot be a retire-candidate (at most watch); the other composites are computed without it |
| S-4.10 | N3 | an eligible symphony with one absent or null day inside the common window | a review completes | it is excluded from the normalization population and shows "insufficient coverage in the common window" with one day fewer than the window's day count; it cannot be a retire-candidate (at most watch) |

Negative coverage: N1 S-4.4, S-4.5, S-4.7 · N2 n/a: no input is refused · N3 S-4.2, S-4.3, S-4.9, S-4.10 · N4 covered by AC-1 (S-1.16) · N5 covered by AC-9 (S-9.3) · N6 covered by AC-2 (S-2.3 to S-2.6, S-2.14) · N7 S-4.6 · N8 n/a: computed inside a run, with no edit held open
Questions: Q-3, Q-13, Q-19, Q-20

### AC-5: Realized result against the symphony's own backtest
Requirement items: R-5
Rule: When a review run evaluates an eligible symphony, the panel and API shall show its realized total return and max drawdown in percent beside a Composer backtest of its current definition over the same first and last dates, labelled "counterfactual: Composer backtest of today's definition", and the gaps in percentage points; if the backtest cannot be obtained, then the item shall read "unavailable" with the reason and the verdict shall still be produced from the other items.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-5.1 | B | the backtest succeeds | a review completes | both legs and both gaps are shown; the backtest's first and last dates equal the realized window's |
| S-5.2 | N2 | Composer returns HTTP 413 for the backtest request | a review completes | the item reads "unavailable: definition too large for Composer backtest (HTTP 413)"; the verdict lists "backtest" as missing |
| S-5.3 | N2 | Composer returns HTTP 400 or 422 for the backtest request | a review completes | the item reads "unavailable: Composer rejected the backtest" with the status code; the verdict lists "backtest" as missing |
| S-5.4 | N6 | Composer returns HTTP 429 for the backtest request | a review completes | the item reads "unavailable: Composer rate limit"; the verdict lists "backtest" as missing |
| S-5.5 | N6 | Composer returns HTTP 5xx for the backtest request | a review completes | the item reads "unavailable: Composer unavailable"; the verdict lists "backtest" as missing |
| S-5.6 | N6 | the backtest request times out or the network fails | a review completes | the item reads "unavailable: Composer unavailable"; the verdict lists "backtest" as missing |
| S-5.7 | N6 | the symphony's current definition cannot be fetched | a review completes | the item reads "unavailable: definition unavailable"; no backtest is requested |
| S-5.8 | N3 | the backtest returns fewer than 2 points in the window | a review completes | the item reads "unavailable: backtest series too short" |
| S-5.9 | N7 | a successful backtest | a review completes | no backtest value appears in any realized figure, the composite, AC-3 or AC-6 |
| S-5.10 | N7 | any backtest request | a review completes | no symphony is created, saved, deployed or modified in Composer |
| S-5.11 | N6 | the backtest request returns HTTP 200 with a body that cannot be read as the expected JSON | a review completes | the item reads "unavailable: Composer response unreadable"; the verdict lists "backtest" as missing |
| S-5.12 | N1 | the backtest returns a series whose first or last date differs from the realized window's | a review completes | the item reads "unavailable: backtest dates do not match the realized window"; no gap is shown |

Negative coverage: N1 S-5.12 · N2 S-5.2, S-5.3 · N3 S-5.8 · N4 covered by AC-1 (S-1.16) · N5 covered by AC-9 (S-9.3) · N6 S-5.4, S-5.5, S-5.6, S-5.7, S-5.11 · N7 S-5.9, S-5.10 · N8 n/a: computed inside a run, with no edit held open
Questions: Q-3, Q-14

### AC-6: Marginal contribution to the portfolio
Requirement items: R-5
Rule: When a review run evaluates an eligible symphony, the panel and API shall show the comparison portfolio's total return and max drawdown in percent over the comparison window (the span covered by every eligible symphony) with the symphony and without it, where the comparison portfolio holds the eligible symphonies only, each not-yet-eligible live symphony is excluded from it and shown as excluded, and "without" removes the symphony's realized value and leaves every other eligible symphony's realized value unchanged with no reallocation, labelled "counterfactual: portfolio without this symphony", and the differences in percentage points.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-6.1 | B | an eligible symphony and at least one other eligible symphony on the same basis | a review completes | with and without figures and both differences are shown with the counterfactual label and the comparison window, whose first date is the latest first date among the eligible symphonies |
| S-6.2 | N1 | it is the only eligible symphony | a review completes | the item reads "not applicable: no other eligible symphonies" |
| S-6.3 | N3 | another eligible symphony lacks values on some days inside the comparison window | a review completes | the figures use only days on which every eligible symphony in the comparison portfolio has a value, and the day count shows the reduced number |
| S-6.4 | N1 | the symphony and a peer are on different bases | a review completes | the item reads "unavailable: bases differ" |
| S-6.5 | N7 | a completed review | the operator opens the dashboard hero, Performance and History tabs | no "without" figure appears there as realized portfolio performance |
| S-6.6 | N1 | a live symphony that is not yet eligible, invested more recently than every eligible symphony | a review completes | it is excluded from the comparison portfolio and listed as "excluded: not yet eligible"; the comparison window's first date is not moved later by it |

Negative coverage: N1 S-6.2, S-6.4, S-6.6 · N2 n/a: no input is refused · N3 S-6.3 · N4 covered by AC-1 (S-1.16) · N5 covered by AC-9 (S-9.3) · N6 covered by AC-2 (S-2.3 to S-2.6, S-2.14) · N7 S-6.5 · N8 n/a: computed inside a run, with no edit held open
Questions: Q-3, Q-22

### AC-7: Long-VIX co-firing, information only
Requirement items: R-5
Rule: When a review run evaluates an eligible symphony, the panel and API shall show, labelled "informational: not used in the verdict", the number of trading days in the window on which it held a long-VIX product (VIXY, VIXM, UVXY, UVIX, VXX), the number of those days on which at least one other live symphony also held one, and the portfolio's highest daily long-VIX share of total value in percent with and without it.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-7.1 | B | daily holdings show the symphony and one other held VIXY on some of the same days | a review completes | both day counts and both peak shares are shown with the informational label; the co-firing count is at most the held-days count |
| S-7.2 | N1 | the symphony never held a long-VIX product | a review completes | both day counts read 0, a computed value, and both peak shares are shown |
| S-7.3 | N1 | the symphony held only SVXY or SVIX | a review completes | both day counts read 0 (short-VIX products are not long-VIX) |
| S-7.4 | N3 | daily holdings are unavailable for the window | a review completes | the item reads "unavailable", never 0 |
| S-7.5 | N3 | daily holdings are missing for some days of the window | a review completes | counts use the days available and the row shows that day count |
| S-7.6 | N6 | the holdings request fails (HTTP 4xx, 5xx, timeout) | a review completes | the item reads "unavailable" with the reason |
| S-7.7 | N7 | two reviews whose inputs differ only in long-VIX holdings | each completes | both give the same verdict |
| S-7.8 | N6 | the holdings request returns HTTP 200 with a body that cannot be read as the expected JSON | a review completes | the item reads "unavailable: Composer response unreadable" |

Negative coverage: N1 S-7.2, S-7.3 · N2 n/a: no input is refused · N3 S-7.4, S-7.5 · N4 covered by AC-1 (S-1.16) · N5 covered by AC-9 (S-9.3) · N6 S-7.6, S-7.8 · N7 S-7.7 · N8 n/a: computed inside a run, with no edit held open
Questions: Q-10

### AC-8: One verdict per eligible symphony and the decision lifecycle
Requirement items: R-1, R-5
Rule: When a review run evaluates an eligible symphony, the review shall give exactly one verdict: retire-candidate when the existing retirement rule flags it (a pair passing both gates in which it has the lower composite); otherwise watch when its realized total return trails its backtest by more than 10 percentage points, or when removing it would raise the portfolio's total return and leave the portfolio's max drawdown no deeper; otherwise keep; a pair in which either member has no composite (not yet eligible, or on the fallback basis) never produces a retire-candidate verdict, is still shown with the reason, and leaves the eligible member at most watch from that pair; and the row shall list the evidence items that were unavailable, and only a pending retire-candidate row shall offer Approve and Reject through the existing status-only decision routes.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-8.1 | B | a symphony flagged by the existing retirement rule on the review history | a review completes | the row shows "retire-candidate", the explanation with the date it was written (or "explanation unavailable"), the Pending status and Approve and Reject; the API's existing `recommendations` list carries it with its existing fields |
| S-8.2 | B | a symphony not flagged whose realized total return trails its backtest by 12 percentage points | a review completes | the verdict is "watch" with the reason "trails its own backtest" |
| S-8.3 | B | a symphony not flagged whose removal raises portfolio total return and leaves max drawdown the same or shallower | a review completes | the verdict is "watch" with the reason "drags portfolio return and drawdown" |
| S-8.4 | B | a symphony with neither condition and no retirement flag | a review completes | the verdict is "keep" |
| S-8.5 | N1 | a symphony whose realized total return trails its backtest by exactly 10 percentage points | a review completes | the backtest condition is not met |
| S-8.6 | N1 | a symphony whose removal raises portfolio return but deepens max drawdown | a review completes | the drag condition is not met |
| S-8.7 | N3 | the backtest item is unavailable | a review completes | the verdict comes from the remaining items and the row lists "missing: backtest" |
| S-8.8 | N3 | redundancy, backtest and marginal contribution items are all unavailable | a review completes | the row shows "no verdict: no evidence available" and no Approve or Reject |
| S-8.9 | B | a pending retire-candidate row | the operator chooses Approve retirement | the status reads Approved with "reload this page to view the wind-down checklist", one decision row exists for that Composer hash, and no Composer request is made; after a reload the row shows the checklist |
| S-8.10 | N7 | keep and watch rows with no prior decision | the panel renders | they show no Approve or Reject |
| S-8.11 | N7 | a symphony with an approved decision whose latest verdict is watch | the panel renders | the row shows the Approved status and the checklist and no buttons; the decision row is unchanged |
| S-8.12 | N5 | two Approve requests for the same Composer hash arrive together | both complete | one decision row exists with status approved |
| S-8.13 | N4 | an Approve request without the CSRF token, with authentication and CSRF checks enabled | it is sent | status 403; no decision row is written or changed |
| S-8.14 | N4 | an unauthenticated Approve request sent as the panel sends it (not under `/api/`, no XHR header), with authentication checks enabled | it is sent | redirect to the login page; no decision row is written or changed |
| S-8.15 | N4 | a Reject request without the CSRF token, with authentication and CSRF checks enabled | it is sent | status 403; no decision row is written or changed |
| S-8.16 | N2 | an Approve request whose candidate id is empty or longer than 300 characters | it is sent | the existing "invalid candidate_id" refusal; nothing is written |
| S-8.17 | N6 | the decision store write fails | the operator chooses Reject | the response carries the existing failure with the exception type only; the status chip stays Pending |
| S-8.18 | N8 | a review run is in progress | the operator chooses Approve on a retire-candidate row from the previous run | the decision is written for that Composer hash and, after the run completes, the row shows Approved whatever its new verdict |
| S-8.19 | N1 | an Approve request whose body exceeds 64 KiB | it is sent | status 413; no decision row is written or changed |
| S-8.20 | N3 | an eligible symphony with the lower composite whose only pair passing both gates has a sibling with no composite (not yet eligible, or on the fallback basis) | a review completes | its verdict is not retire-candidate (at most watch, from the watch and keep conditions); the pair is shown with the reason "sibling has no composite"; no Approve or Reject is offered |

Negative coverage: N1 S-8.5, S-8.6, S-8.19 · N2 S-8.16 · N3 S-8.7, S-8.8, S-8.20 · N4 S-8.13, S-8.14, S-8.15 · N5 S-8.12 · N6 S-8.17 · N7 S-8.10, S-8.11 · N8 S-8.18
Questions: Q-5, Q-11, Q-14, Q-19, Q-20

### AC-9: Nightly and on-demand runs, latest run only
Requirement items: R-1, R-2
Rule: When the nightly 03:45 retirement tick fires or the operator chooses "Run review now" on the panel, the review shall evaluate every live symphony, keep the result as a new append-only advisory record, and the panel and API shall show only the latest completed run, one row per live symphony; if a run is already in progress, then a new run shall not start and an on-demand request shall be answered "a review is already running", with nothing written.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-9.1 | B | the daemon is running | the 03:45 tick fires and the run completes | the panel and API show one row per live symphony with that run's as-of trading day and run time |
| S-9.2 | B | a completed nightly run exists | the operator chooses "Run review now" and the run completes | the request is acknowledged "review started"; after completion the panel shows the new run time and none of the nightly run's rows |
| S-9.3 | N5 | the nightly run is in progress | the operator chooses "Run review now" | the response reads "a review is already running"; no second run starts and nothing is written |
| S-9.4 | N5 | an on-demand run is in progress | the 03:45 tick fires | the tick starts no run and writes nothing; the in-progress run's result is that night's review |
| S-9.5 | N5 | no run in progress | two "Run review now" requests arrive together | exactly one run starts; the other is answered "a review is already running" |
| S-9.6 | N5 | a nightly run and a later on-demand run complete on the same UTC date | the operator opens the panel and requests the API | each live symphony appears once, from the later run |
| S-9.7 | N4 | a "Run review now" request without the CSRF token, with authentication and CSRF checks enabled | it is sent | status 403; no run starts and nothing is written; the panel shows "Request refused: reload the page" instead of failing silently |
| S-9.8 | N4 | an unauthenticated "Run review now" request, with authentication checks enabled | it is sent | refused before any run starts (redirect to login for a page-style request, 401 for an `/api/` or XHR request); nothing is written; the panel shows "Session expired: sign in again" |
| S-9.9 | N6 | a run fails partway | the operator opens the panel | the previous completed run is still shown with the failed-attempt notice; no partial run appears; the daemon log records the failure with the exception type only |
| S-9.10 | N7 | a retire-candidate whose pair, verdict and evidence are unchanged at 2 decimal places from the last run except the window end date and day counts | the next run completes, the same day or a later day | the stored explanation is reused with its original written date and no explainer request is made |
| S-9.11 | N1 | zero live symphonies | a run completes | the panel shows "No live symphonies" and the API returns an empty review list with status 200 |
| S-9.12 | N8 | a "Run review now" request is in flight | the operator chooses the button again | the button stays disabled until the response arrives; no second request is sent |
| S-9.13 | N3 | a run is in progress | the operator opens the panel | the previous completed run is shown with "review running since" and the start time |
| S-9.14 | N7 | a run during market hours | the run is in progress | the 1-minute execution cycle starts on schedule; `alpha_bot_execution.py` and `math_engine.py` carry no diff |
| S-9.15 | N4 | the dashboard secret key or credential is not configured | "Run review now" is requested | refused before any run starts (503 with the misconfigured error body for an `/api/` or XHR request, redirect to login otherwise); nothing is written |
| S-9.16 | N1 | removed: the run action reads no request body, and Werkzeug enforces the 64 KiB cap only when a handler reads the body, so no 413 refusal exists on this path (PM ruling) | none | none |
| S-9.17 | N7 | a retire-candidate whose sibling, a gate result or any figure at 2 decimal places changed since the last run | the next run completes | a new explanation is requested and shown with the new written date |

Negative coverage: N1 S-9.11 · N2 S-9.3 · N3 S-9.13 · N4 S-9.7, S-9.8, S-9.15 · N5 S-9.3, S-9.4, S-9.5, S-9.6 · N6 S-9.9 · N7 S-9.10, S-9.14, S-9.17 · N8 S-9.12
Questions: Q-6, Q-15

### AC-10: No trade, Composer write or engine path
Requirement items: R-5
Rule: When any review path runs (the nightly tick, the on-demand run, the panel render, `GET /api/retirement-recommendations`, or the approve and reject routes), the feature shall reach no Composer write (create, save, deploy, invest, rebalance, go-to-cash, sell), no read or write of the live-execution flag, and no execution-engine function other than the Composer base URL constant and the credential header builder that the existing read-only clients already import (`advisors/composer_backtest_client.py:45`, `symphony_logic.py:19`), never `fetch_symphony_stats`, the go-to-cash or sell functions, or the panic-sell function `perform_account_liquidation`; `advisors/retirement_recommender.py` keeps its no-engine-import rule; and the existing retirement no-trade scans shall cover every new path.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-10.1 | B | the feature's code at HEAD | `tests/security/test_retirement_recommender_no_trade_boundary.py` (module scans, extended to every new review module, allowing only the two named engine imports) and `tests/security/test_retirement_action_no_trade_boundary.py` (transitive call-graph scan, extended to the tick worker, the on-demand run action, the GET route's fetch helper, the panel render's retirement block, and the approve and reject routes) run | they pass with every listed path in scope |
| S-10.2 | N7 | a Composer save call planted in a new review path | the scans run | the scans fail |
| S-10.3 | N7 | a complete run | the Composer requests it made are listed | each is a read (series, holdings, statistics, definition) or a backtest request; none is a create, save, deploy, invest, rebalance, go-to-cash or sell |
| S-10.4 | N7 | the feature is deployed | the settings write allowlist is read | it is unchanged; the tenure threshold is not dashboard-writable |
| S-10.5 | N7 | the feature's diff | `tests/test_mdd_window_truth_zero_diff_guard.py` runs | the LF-normalized content pins on `alpha_bot_execution.py` and `math_engine.py` (`:68-71`) still match |
| S-10.6 | N7 | a completed run | its stored rows are read | every row is marked advisory-only |
| S-10.7 | N7 | a call to `fetch_symphony_stats` or `perform_account_liquidation` planted in a new review path | the scans run | the scans fail |

Negative coverage: N1 n/a: the rule judges code paths, not input values · N2 n/a: nothing is refused; a write path must not exist · N3 n/a: no value is computed by this rule · N4 covered by AC-1 (S-1.16, S-1.17), AC-8 (S-8.13 to S-8.15) and AC-9 (S-9.7, S-9.8) · N5 n/a: the rule is static, so concurrency cannot change it · N6 n/a: no dependency is called by the scans · N7 S-10.2, S-10.3, S-10.4, S-10.5, S-10.6, S-10.7 · N8 n/a: no edit exists on these paths
Questions: none

## Rule-by-Entry-Point Matrix
| Rule | Nightly 03:45 tick | "Run review now" action | `GET /api/retirement-recommendations` | Overview panel render | `POST /ai-advisor/retirement/approve` | `POST /ai-advisor/retirement/reject` |
|---|---|---|---|---|---|---|
| AC-1 no verdict below the threshold | S-1.3 | S-1.4 | S-1.3 | S-1.3 | n/a: the route accepts any candidate id by an approved deviation (`app.py:7923-7929`); Q-11 | n/a: same route deviation (`app.py:7923-7929`); Q-11 |
| Fail-Closed refusal: unauthenticated request | n/a: the scheduler thread receives no request | S-9.8 | S-1.16 | S-1.17 | S-8.14 | n/a: the same global hook as approve (`app.py:194-237`) before the shared decision dispatch (`app.py:7913`); tested at S-8.14 |
| Fail-Closed refusal: authentication not configured | n/a: the scheduler thread receives no request | S-9.15 | S-1.23 | S-1.23 | n/a: the same global hook (`app.py:205-228`), tested at S-9.15 | n/a: the same global hook (`app.py:205-228`), tested at S-9.15 |
| Fail-Closed refusal: POST without the CSRF token | n/a: the scheduler thread sends no POST | S-9.7 | n/a: GET is not CSRF-checked (`app.py:490-493`) | n/a: page render is a GET | S-8.13 | S-8.15 |
| Fail-Closed refusal: POST body over 64 KiB | n/a: no request body | n/a: the run action reads no request body, and Werkzeug enforces the cap only on a body read (S-9.16 removed) | n/a: GET carries no body | n/a: page render carries no body | S-8.19 | n/a: the same app-wide cap (`app.py:452-454`), tested at S-8.19 |
| AC-2 no Composer request on read paths | n/a: the run is where Composer is read | n/a: the run is where Composer is read | S-2.13 | S-2.13 | n/a: a status-only write (`app.py:7913-7954`), scanned by AC-10 (S-10.1) | n/a: a status-only write (`app.py:7913-7954`), scanned by AC-10 (S-10.1) |
| AC-8 Approve and Reject only on a pending retire-candidate | n/a: no buttons on this path | n/a: no buttons on this path | n/a: the API offers no action | S-8.10 | n/a: route deviation (`app.py:7923-7929`); Q-11 | n/a: route deviation (`app.py:7923-7929`); Q-11 |
| AC-9 one run at a time | S-9.4 | S-9.3, S-9.5 | n/a: reads only | S-9.13 | n/a: writes a decision, starts no run | n/a: writes a decision, starts no run |
| AC-10 no trade, Composer write or engine path | S-10.1 | S-10.1 | S-10.1 | S-10.1 | S-10.1 | S-10.1 |

## Not Building
Non-goals:
- NB-1: No automatic retirement, liquidation, go-to-cash, deploy, invest, rebalance or any other Composer write; every verdict and recommendation stays advisory (approved).
- NB-4: No evaluation of undeployed candidate symphonies and no replacement-symphony generation (approved: deferred, Follow-Ups).
- NB-5: No export file (CSV or PDF); the panel and the API JSON are the outputs (excludes part of R-1; approved).
- NB-7: No guard-alpha or bot-versus-held comparison is extended before the 2026-06-22 go-live floor; the longer Composer history extends the realized leg only (`docs/audit/PERF-WINDOW-TRUTH-2026-09-03.md` E-22) (approved).
- NB-11: No dashboard control for the tenure threshold; it is set in the environment file (approved).
- NB-12: No Discord or other notification of verdicts (approved).
- NB-14: No immediate checklist on Approve; the panel keeps today's "reload this page" message (`static/ai_advisor.js:1141-1143`) (approved).

Behavior that stays exactly as it is:
- NB-2: The 1-minute execution engine (`alpha_bot_execution.py`, `math_engine.py`) and trailing-stop behavior (approved).
- NB-3: The recommender's composite weights (`advisors/retirement_recommender.py:94-98`) and gate thresholds (`:44`, `:51`, `:60`, `:68`, `:74`, `:83`); only the history and windows they run on and the tenure gating are new (approved).
- NB-6: The Performance and History tabs (approved).
- NB-9: The approve and reject routes' status-only contract and validation (`app.py:7913-7982`) (approved).

Deferred:
- NB-8: A calendar-year (or other operator-chosen) review window; the window is each symphony's tenure (excludes part of R-1; approved: Follow-Ups).
- NB-13: Persisting invested-since in the engine's state; tenure is read from Composer at run time because the engine is frozen (`feature-plans/mdd-window-truth.md` amended AC-2) (approved).

Protected surfaces consumed but not changed:
- NB-10: `advisors/retirement_explainer.py`, `advisors/retirement_checklist.py`, the `retirement_decisions` table (`migrations/038_retirement_decisions.sql`), the live-execution flag, the settings write allowlist (`app.py:5740-5746`), the panic-sell route and `perform_account_liquidation`, the proposal-approve route and the Managed Sleeves order path (approved).

## Defect Classes
| DC-n | Class (what goes wrong) | Trigger (B or N1-N8) | Sibling search (tool, query, scope) | Scenarios | Raised in review as |
|---|---|---|---|---|---|
| DC-1 | Rule enforced at one entry point, missing at another (tenure gate, authentication, one run at a time) | N2 | grep: `_retirement_recommender_tick_worker`, every new run entry point, `_fetch_retirement_recommendations`, `api_retirement_recommendations`, `ai_advisor_tab`, `_dispatch_retirement_decision` in `app.py`; AlphaBotPM | S-1.3, S-1.4, S-9.3, S-9.4, S-9.7, S-1.16 | none |
| DC-2 | Absent or not-computed value shown or stored as a default (0 for a missing tenure, metric, count or day) | N3 | grep: `else 0.0`, `or 0`, `or 0.0`, the Jinja default filter in `analytics.py`, `advisors/retirement_*.py`, `app.py` retirement helpers, `templates/ai_advisor.html` panel; AlphaBotPM | S-1.10, S-2.9, S-2.10, S-3.7, S-3.8, S-3.11, S-4.2, S-7.4 | none |
| DC-3 | Unavailable state not listed per surface (panel shows it, API omits it, or the reverse) | N3 | read: every field of a review entry in the API response against the panel row markup; AlphaBotPM | S-1.10, S-2.9, S-5.2, S-7.4, S-8.8 | none |
| DC-4 | Dependency failure swallowed or answered with stale data (Composer series, statistics, holdings, definition, backtest) | N6 | grep: `requests.get`, `requests.post`, `run_backtest`, `COMPOSER_BASE_URL` in `app.py` and `advisors/`; AlphaBotPM | S-1.12, S-1.18, S-1.19, S-2.3, S-2.4, S-2.5, S-2.6, S-2.14, S-5.4, S-5.5, S-5.6, S-5.11, S-7.6, S-7.8 | none |
| DC-5 | Check and write outside one lock (two runs both start) | N5 | grep: every start of a review run and every write of review rows; read: the guard each uses; AlphaBotPM | S-9.3, S-9.4, S-9.5 | none |
| DC-6 | Data that must stay unchanged is written on a refused path (a refused run or approve still writes) | N7 | read: every write after the refusal point in the run action and `_dispatch_retirement_decision`; grep: `insert_advisor_observation`, `upsert_retirement_decision`; AlphaBotPM | S-9.3, S-9.7, S-8.13, S-8.16, S-8.19, S-1.12 | none |
| DC-7 | Counterfactual figure presented as realized (backtest or "without" values in realized fields) | N7 | grep: every field that carries a backtest or without-symphony value and every render of it; AlphaBotPM | S-5.9, S-6.5, S-2.12 | none |
| DC-8 | Mixed bases or windows combined in one figure or one comparison | N1 | read: every pairwise, portfolio and composite computation's inputs, their basis labels and their windows; AlphaBotPM | S-2.11, S-6.4, S-6.6, S-4.7, S-4.8, S-4.9, S-4.10 | none |
| DC-9 | Narrow assertion licenses unlisted siblings (threshold values, HTTP failure statuses, unreadable 200 bodies, short-VIX tickers, gate result states) | N1 | read: every test of AC-1, AC-2, AC-3, AC-5, AC-7; list each family whole and check each member has a test; AlphaBotPM | S-1.5 to S-1.9, S-2.2 to S-2.7, S-2.14, S-3.2, S-3.11, S-3.12, S-5.2 to S-5.7, S-5.11, S-7.3 | none |
| DC-10 | Trade, Composer-write or engine path reachable from a new path | N7 | grep: `save_symphony`, `composer_draft_client`, `go-to-cash`, `LIVE_EXECUTION`, `fetch_symphony_stats`, `perform_account_liquidation`, `alpha_bot_execution` imports in `app.py`, `advisors/`, every new module; AlphaBotPM | S-10.1, S-10.2, S-10.3, S-10.7 | none |
| DC-11 | Contract change not carried to every consumer (`GET /api/retirement-recommendations`) | B | grep: `retirement-recommendations`, `_fetch_retirement_recommendations` in `app.py`, `static/`, `templates/`, `tests/`; AlphaBotPM | S-1.1, S-8.1 | none |
| DC-12 | Planned example values not encoded in a test | B | read: Worked Example against the tests for S-1.1, S-1.3, S-3.1, S-5.2; AlphaBotPM | S-1.3, S-3.1, S-5.2 | none |
| DC-13 | Refusal differs between entry points (status code, HTML against JSON, what the panel shows) | N4 | grep: every client `fetch` to a retirement route in `static/ai_advisor.js` and how it reads a non-JSON or non-2xx response; read: `app.py:194-237`, `:373-379`, `:452-454`; AlphaBotPM | S-8.14, S-9.7, S-9.8, S-9.15 | none |

## Worked Example
Review run on demand at 17:00 US Eastern on 2026-12-31 (after the close, so the as-of trading day is 2026-12-31), threshold 126, three live symphonies in one account, invented names and hashes:
- "Copper Ridge Rotation" (Qz7Lm2Rt9Wx4Kp1Nv8Ys), invested-since 2025-03-03, Composer basis.
- "Harbor Lights Momentum" (Bt3Hc8Jd5Fg2Lk9Mn4Pq), invested-since 2025-01-06, Composer basis.
- "Slate Valley Hedge" (Wr6Ty1Ui7Op3As5Df8Gh), invested-since 2026-09-01, Composer basis.

Result:
- Slate Valley Hedge: tenure 85 NYSE trading days (2026-09-01 through 2026-12-31, both inclusive; Labor Day, Thanksgiving and Christmas excluded), "not yet eligible: 41 trading days remaining", no verdict, no composite, no buttons (S-1.3, S-4.6). It still appears as a sibling in the other two rows' redundancy evidence (S-3.9).
- Copper Ridge Rotation and Harbor Lights Momentum overlap from 2025-03-03 on; suppose 460 aligned days, correlation 0.81 with a 95% interval lower bound above 0.65, and a stressed window of ceil(0.05 x 460) = 23 days with correlation 0.72. Both gates read "passed" (S-3.1). Over the common composite window (the latest 126 trading days through 2026-12-31), Copper Ridge has the lower composite (say 0.31 against 0.58), so its verdict is "retire-candidate" with Pending status, Approve and Reject, and an explanation dated 2026-12-31 (S-8.1); the API's `recommendations` list carries it.
- Harbor Lights Momentum: its backtest request returns HTTP 413. Its backtest item reads "unavailable: definition too large for Composer backtest (HTTP 413)" (S-5.2); its verdict comes from the remaining items (keep, if removing it lowers portfolio return) and the row lists "missing: backtest" (S-8.7).
- Every figure carries "realized: Composer account, deposit-adjusted" with its window; the backtest and without-symphony figures carry their counterfactual labels (S-2.1, S-5.1, S-6.1).
- The with/without comparison portfolio holds Copper Ridge and Harbor Lights only; Slate Valley is listed as "excluded: not yet eligible", and the comparison window runs from 2025-03-03 (the later of the two eligible invested-since dates) through 2026-12-31 (S-6.1, S-6.6).
- The nightly tick on 2027-01-01 (a holiday) has as-of 2026-12-31 (S-1.20); Copper Ridge's evidence is unchanged at 2 decimal places, so the 2026-12-31 explanation is reused (S-9.10).

Main failure case: the Composer series request returns HTTP 5xx for Copper Ridge Rotation during the run. Its figures fall back to "PlanetStopper shadow history from the 2026-06-22 go-live floor" (S-2.5); its composite reads "not comparable: basis differs" (S-4.7) and its pair with Harbor Lights reads "unavailable: bases differ" (S-2.11), so it cannot be a retire-candidate that night, and its row lists the missing items (S-8.7).

## Fail-Closed Behaviour
- A "Run review now" request while a run is in progress: refused with "a review is already running" before any run starts; nothing written (S-9.3, S-9.5). A tick during a run starts nothing (S-9.4).
- Composer statistics failing for any configured account (HTTP error, timeout, unreadable 200): the run does not complete and writes nothing; the previous completed run stays shown with the failed-attempt notice (S-1.12, S-1.18, S-1.19).
- An unauthenticated request: the global hook (`app.py:194-237`) answers 401 with `{"error": "unauthenticated"}` for an `/api/` path or XHR request (`app.py:183-190`, `:234-236`) and redirects to login otherwise (`app.py:237`), before the handler (S-1.16, S-1.17, S-8.14, S-9.8).
- Authentication not configured (no secret key or dashboard credential): the global hook answers 503 with `{"error": "misconfigured"}` for an `/api/` path or XHR request and redirects to login otherwise (`app.py:205-228`) (S-1.23, S-9.15).
- A POST without the CSRF token: 403 from the global hook (`app.py:373-379`), rendered as HTML, before the handler (S-8.13, S-8.15, S-9.7).
- An approve or reject POST body over 64 KiB: 413 from the app-wide body cap (`app.py:452-454`), rendered as HTML, because that handler reads the body (`app.py:7936`) (S-8.19). The run action reads no body, so the cap never fires there and no refusal is added.
- An approve or reject with an invalid candidate id: the existing `{"success": false, "error": "invalid candidate_id"}` refusal (`app.py:7936-7941`), nothing written (S-8.16).
- A decision store failure: the existing failure response with the exception type only (`app.py:7943-7952`) (S-8.17).
- A symphony below the threshold, or with no invested-since date: no verdict (S-1.3, S-1.4, S-1.10).
- Composer refusals and failures on series, definition, holdings or backtest (401, 403, 404, 400, 422, 413, 429, 5xx, timeout, unreadable 200): the affected item reads "unavailable" with the reason, or the series falls back to shadow history with its label; no figure is defaulted (S-2.2 to S-2.6, S-2.14, S-5.2 to S-5.7, S-5.11, S-7.6, S-7.8).
- Evidence on two different bases: the combining item, and the composite of a fallback-basis symphony, are not computed (S-2.11, S-4.7, S-6.4).
- An invalid threshold setting does not refuse the run: the default 126 applies and the panel says so (S-1.6 to S-1.9).

## Authorities Touched
| Value | Authority used | What it replaced | What proves no second source |
|---|---|---|---|
| Live roster and invested-since date (tenure start) | Composer's symphony-statistics response for each configured account, read by a new read-only path (not `fetch_symphony_stats`, `alpha_bot_execution.py:191-205`, and not `perform_account_liquidation`, `app.py:5022-5028`) | the engine-state roster (`advisors/retirement_recommender.py:428-435`), which never drops a symphony; invested-since is not stored today (`alpha_bot_execution.py:266-275`) | S-1.1, S-1.14, S-1.15 |
| Realized daily returns for evidence | Composer account-scoped per-symphony daily series, deposit-adjusted | shadow history's bot series (`advisors/retirement_recommender.py:470-476`), now the fallback | S-2.1, S-2.11 |
| As-of trading day and trading-day count | the NYSE calendar (`market_calendar.py:41`) | nothing | S-1.2, S-1.3, S-1.20, S-1.21, S-1.22 |
| Long-VIX product list | the VIX family (`advisors/frontrunner_detector.py:74-75`) minus the short-VIX SVXY and SVIX | nothing | S-7.3 |
| Decision status | `retirement_decisions` (`migrations/038_retirement_decisions.sql`) | unchanged | S-8.9, S-8.11 |

## Contract Changes and Consumers
- `GET /api/retirement-recommendations`: before, `{"recommendations": [...]}` holding the latest UTC night's retirement rows with request-time `approval_status`, `candidate_name`, `sibling_name` (`app.py:4302-4361`). After, the same `recommendations` list and fields, plus the latest completed run's review: the run time, the as-of trading day, the threshold in effect, any failed-attempt notice, and one entry per live symphony with tenure, eligibility, the verdict, the missing items and every evidence item with its basis, unit, window, subject and source. Backward compatible: additive. Consumers: the panel is server-rendered from the shared fetch helper, not the API; no file in `static/` reads this route; tests under `tests/app/test_retirement_*.py` read it.
- A new on-demand run action for the panel (route chosen at Gate 2), authenticated and CSRF-protected, with no request body the review depends on.
- The stored advisory record gains review rows; the existing `RETIREMENT_RECOMMENDATION` row shape is unchanged.

## Sibling Repos and Landing Order
none: AlphaBotPM is the only repository; it deploys to the droplet after merge through the project's PR workflow.

## Migrations Required
none required by the criteria: the review is kept in the existing append-only advisory store and decisions in the existing `retirement_decisions` table. If Gate 2 adds storage, it is additive only (NULLable with DEFAULT), per the project's migration standard, and the guard that pins the highest migration number at 38 (`tests/test_mdd_window_truth_zero_diff_guard.py:157`) must be updated in the same change.

## Configuration and Enablement
- New: `TENURE_REVIEW_MIN_TRADING_DAYS` in the environment file (no existing key by that name in `.env.example` or the code), a positive whole number of NYSE trading days, default 126; absent or invalid falls back to 126 (S-1.5 to S-1.9). Read at the start of each run, so a change takes effect at the next run without a daemon restart. Not in the settings write allowlist (S-10.4).
- Environments: the droplet `.env` may set it; unset means 126.
- Ships enabled: the existing 03:45 tick (`app.py:1254`) runs the review after deploy; the on-demand action is available at once. The operator enables it by merging and deploying.

## Breaking Changes
- Operator-visible: the Retirement Recommendations panel lists every live symphony instead of only flagged ones, and gains a "Run review now" button.
- Operator-visible: a retirement recommendation's basis label reads the realized Composer basis instead of "actual-traded (bot) daily returns" (`advisors/retirement_recommender.py:122`) when Composer history is used.
- Operator-visible: a symphony that has left Composer's statistics response no longer appears, even though the engine's state still holds it.
- A run now calls Composer read endpoints, the backtest endpoint and, for each new or changed retire-candidate, the explainer model; on-demand runs add that spend outside the nightly slot.

## Security Considerations
- Input validation and injection: the run action takes no input the review uses; the threshold setting is parsed as a positive whole number with a default (S-1.6 to S-1.9); decision routes keep their validation (S-8.16); the 64 KiB body cap applies to approve and reject, which read the body (S-8.19).
- Authorization and access control: every read is behind the global authentication hook and every POST behind the CSRF hook; unconfigured authentication refuses everything (S-1.16, S-1.17, S-1.23, S-8.13 to S-8.15, S-9.7, S-9.8, S-9.15). Security marking: AC-1, AC-8, AC-9.
- Data exposure: account-scoped values and holdings are shown only to the authenticated operator; failures carry the exception type only (S-9.9, S-8.17); no credential reaches a response or log.
- Client-side rendering: every new field is HTML-escaped by autoescape with no `| safe` (Design-System Mapping); the run action shows a readable message for an HTML refusal instead of failing silently (S-9.7, S-9.8).
- Abuse and rate limits: one run at a time (AC-9); Composer rate limits produce an "unavailable" item, never a retry storm (S-2.4, S-5.4); unchanged retire-candidates reuse their explanation (S-9.10).
- Money: no path moves money, writes to Composer or reaches an engine function beyond the two read-only imports (AC-10); verdicts drive real-money keep or retire decisions, so every figure carries its basis. Money marking: AC-1 to AC-6, AC-8, AC-10. The header lists the union of both categories because its form takes one criteria list.

## Prerequisites
- Composer reachable with the existing key pair and account ids; when the statistics read fails the run does not complete (S-1.12); other reads degrade per item (AC-2, AC-5, AC-7).
- The NYSE calendar the daemon already uses (`market_calendar.py:41`).
- The explainer model key already configured for the nightly tick; when absent, rows read "explanation unavailable" (`templates/ai_advisor.html:1565-1567`).

## Design-System Mapping
No formal component library; the project's convention is the `--studio-*` CSS tokens and the existing classes in `templates/ai_advisor.html`.

| Component | Element | Required primitive or token | Notes |
|---|---|---|---|
| Retirement Recommendations panel | container and header | `.matrix-card`, `.matrix-card-header` | existing (`templates/ai_advisor.html:1533-1534`) |
| Panel header | "Run review now" button, run time, as-of trading day, running notice, failed-attempt notice, refusal messages, threshold in effect | `.run-trigger-btn`; `--studio-ink-dim` text; `--studio-neg` for the failed-attempt and refusal messages | button disables while a request is in flight |
| Symphony row | display name (Composer hash as tooltip), tenure, eligibility | `.retirement-rec-card`, `.retirement-rec-pair`, `.retirement-rec-meta` | name primary, hash secondary, as today (`templates/ai_advisor.html:1540-1546`) |
| Symphony row | verdict chip (keep, watch, retire-candidate, no verdict) | new chip modifier set mirroring `.retirement-status-chip` with `--studio-pos`, `--studio-neg`, `--studio-ink-dim`, `--studio-border` | to create; no raw hex |
| Symphony row | evidence items and gate results | `.retirement-rec-gates`, `.retirement-rec-meta` | values to 2 decimal places with units |
| Symphony row | basis, window, counterfactual and informational labels | `.retirement-rec-basis` | italic dim text, one per item |
| Symphony row | unavailable reasons and missing-items line | `--studio-ink-dim` | never rendered as 0 |
| Retire-candidate row | explanation with written date, status chip, Approve and Reject, checklist | `.retirement-rec-explanation`, `.retirement-status-chip--*`, `.run-trigger-btn`, `.discuss-btn`, `.retirement-checklist*` | existing (`templates/ai_advisor.html:1565-1616`) |
| Empty states | "No review yet", "No live symphonies" | `.prism-empty` | existing pattern (`templates/ai_advisor.html:1620-1622`) |

## Definition of Done (Verification)
| AC | Scenarios | Test level | Real-entrypoint test |
|---|---|---|---|
| AC-1 | S-1.1, S-1.2, S-1.3, S-1.4, S-1.5, S-1.6, S-1.7, S-1.8, S-1.9, S-1.10, S-1.11, S-1.12, S-1.13, S-1.14, S-1.15, S-1.16, S-1.17, S-1.18, S-1.19, S-1.20, S-1.21, S-1.22, S-1.23 | unit (tenure count, as-of day, threshold parsing), route, panel render | `GET /api/retirement-recommendations` and `GET /ai-advisor` through the Flask test client |
| AC-2 | S-2.1, S-2.2, S-2.3, S-2.4, S-2.5, S-2.6, S-2.7, S-2.8, S-2.9, S-2.10, S-2.11, S-2.12, S-2.13, S-2.14 | unit with captured Composer series fixtures, route | a run started through the tick worker, then the GET route |
| AC-3 | S-3.1, S-3.2, S-3.3, S-3.4, S-3.5, S-3.6, S-3.7, S-3.8, S-3.9, S-3.10, S-3.11, S-3.12, S-3.13 | unit and golden fixture against the existing recommender | a run through the tick worker, then the GET route |
| AC-4 | S-4.1, S-4.2, S-4.3, S-4.4, S-4.5, S-4.6, S-4.7, S-4.8, S-4.9, S-4.10 | unit and golden fixture | a run through the tick worker, then the GET route |
| AC-5 | S-5.1, S-5.2, S-5.3, S-5.4, S-5.5, S-5.6, S-5.7, S-5.8, S-5.9, S-5.10, S-5.11, S-5.12 | unit with captured backtest and 413 fixtures | a run through the on-demand action, then the GET route |
| AC-6 | S-6.1, S-6.2, S-6.3, S-6.4, S-6.5, S-6.6 | unit | a run through the tick worker, then the GET route |
| AC-7 | S-7.1, S-7.2, S-7.3, S-7.4, S-7.5, S-7.6, S-7.7, S-7.8 | unit with captured holdings fixtures | a run through the tick worker, then the GET route |
| AC-8 | S-8.1, S-8.2, S-8.3, S-8.4, S-8.5, S-8.6, S-8.7, S-8.8, S-8.9, S-8.10, S-8.11, S-8.12, S-8.13, S-8.14, S-8.15, S-8.16, S-8.17, S-8.18, S-8.19, S-8.20 | unit (verdict rule), route, panel render | `POST /ai-advisor/retirement/approve` and `/reject` through the Flask test client, then `GET /ai-advisor` |
| AC-9 | S-9.1, S-9.2, S-9.3, S-9.4, S-9.5, S-9.6, S-9.7, S-9.8, S-9.9, S-9.10, S-9.11, S-9.12, S-9.13, S-9.14, S-9.15, S-9.17 | scheduler, route, concurrency | the 03:45 scheduler registration and the on-demand action through the Flask test client |
| AC-10 | S-10.1, S-10.2, S-10.3, S-10.4, S-10.5, S-10.6, S-10.7 | static AST scans, mutation check, content pins | the extended `tests/security/test_retirement_*_no_trade_boundary.py` and `tests/test_mdd_window_truth_zero_diff_guard.py` |

Gates: `ruff format --check`, `ruff check`, a scoped pytest run of the named retirement and review test files (never `-n auto`, never more than 4 workers), CI green on the exact SHA, then the PM's live functional check on the droplet after the PR's `/review`: an on-demand run against the live account, the panel rendered, and the API read, because tests-green is necessary and never sufficient.

Authentication and CSRF scenarios (S-1.16, S-1.17, S-1.23, S-8.13, S-8.14, S-8.15, S-9.7, S-9.8, S-9.15) prove nothing with the test defaults, which turn both checks off (`tests/conftest.py:280`, `:297`); their tests set `_auth_check_enabled` and `_csrf_check_enabled` back to true.

Fixture provenance: Composer symphony statistics, per-symphony daily series, daily holdings, symphony definition, backtest success and HTTP 413 responses are captured from the real Composer API by read-only requests, trimmed to one record per case the code distinguishes (one record per AC-1, AC-2, AC-5 and AC-7 case), with the account id redacted; shadow-history rows are captured from a droplet database copy. The unreadable-200 cases (S-1.19, S-2.14, S-5.11, S-7.8) use a hand-written fixture labelled synthetic, because Composer cannot be made to return a non-JSON HTTP 200 on a read-only request (operator approved). No other fixture is hand-built to match the parser.

## Success Measures
- The first nightly run after deploy produces a review row for every live symphony (read: the daemon log's retirement tick line and the API's review list count against Composer's statistics response).
- At least one symphony is on the Composer basis in that run (read: the API's basis labels).
- An on-demand run completes on the droplet without delaying any 1-minute execution cycle (read: daemon log cycle timestamps during the run).

## Known-Unconfirmed and Standing Risk
- Composer series reach and shape for every live symphony. PM live read: 538 to 539 daily points to 2024-07-12 on the oldest (E-24). Unconfirmed for all 11, and whether each starts at its invested-since date. Settle with one read-only GET of the per-symphony position series for each symphony, comparing the first date to invested-since. Surfaces as S-2.8.
- `deposit_adjusted_series` meaning: the API reference defines it by name only (`docs/audit/COMPOSER-HISTORICAL-SERIES-2026-09-03.md`, S-2); the window-truth audit calls it cash-flow-clean (E-24). Settle by comparing both arrays on a known deposit date. Wrong meaning would attribute a deposit to performance.
- Backtest date range: the existing backtest client sends no start or end date (`advisors/composer_backtest_client.py:296-304`); the PM reports a live read in which Composer's backtest honored both. Settle with one read-only backtest request carrying both dates, comparing the returned first and last days. Surfaces as S-5.1, S-5.12.
- Invested-since on redeploy or rename: unknown whether Composer moves it. Settle with a read-only statistics GET for a symphony known to have been redeployed. Surfaces as S-1.14 (Q-7).
- Composer id versus state key: the engine tolerates suffix drift between Composer ids and state keys (`alpha_bot_execution.py:211-264`), so a decision row or display name keyed by a state key may not match the Composer id the review uses. Settle by comparing state keys with the statistics response ids.
- Daily holdings range: PM live read shows HTTP 200 for the live account; the maximum date range per request and the reach to 2024 are unconfirmed (doc rates it Medium). Surfaces as S-7.5.
- Backtest size: Composer rejects request bodies over about 1 MB with HTTP 413; one live definition is about 1.06 MB with default JSON separators and 0.99 MB compact (PM live read). Which symphonies exceed is unconfirmed. Surfaces as S-5.2.
- Composer rate limits conflict between Composer sources (25 per minute against 1 per second); a full run makes on the order of 35 requests. Surfaces as S-2.4, S-5.4.
- Production facts behind the Trigger (PM live reads, not code facts). Settle with `SELECT symphony_id, COUNT(DISTINCT trading_day) FROM shadow_history GROUP BY symphony_id`, `SELECT COUNT(*) FROM advisor_observations WHERE advisor_role='RETIREMENT_RECOMMENDATION'` and `SELECT COUNT(*) FROM retirement_decisions`. No criterion depends on the counts.
- The shadow-history fallback today turns a NULL `shadow_return` or `current_return` into 0.0 (`analytics.py:1901-1902`), which S-2.10 forbids. Pre-existing; whether production rows carry NULLs is unconfirmed: `SELECT COUNT(*) FROM shadow_history WHERE shadow_return IS NULL OR current_return IS NULL`.
- The existing basis label calls shadow history's bot series "actual-traded" (`advisors/retirement_recommender.py:122`), while `DE-AUDIT-BL4-001` finding D4 records that the dashboard's "Bot" figure from the same shadow rows is a dry-run simulation trajectory (`DECISIONS.md:9715`). The fallback label in AC-2 names shadow history without claiming it is traded.
- Deliberate narrowing: with threshold 126 and the structural gate needing 181 aligned days (`advisors/retirement_recommender.py:74`, `:83`, `:386-389`), a symphony with 126 to 180 aligned days with every sibling can be eligible yet never a retire-candidate; its redundancy reads "insufficient data" (S-3.2).
- The latest-night filter keys on the UTC date of `created_at` (`app.py:4289`; `migrations/017_advisor_observations.sql:12`) while the tick fires at the droplet's local 03:45; AC-9 requires the latest run regardless of date.
- Stored rows: `advisor_observations` requires a subject type and id on every row (`migrations/017_advisor_observations.sql:14-15`); a run-level record (run time, as-of day, "No live symphonies") needs one for S-9.11 to be writable, a Gate-2 decision.
- The existing approve and reject client reads every response as JSON (`static/ai_advisor.js:1132`), so an HTML 302, 403 or 413 refusal there fails without a readable message. Pre-existing; one backlog line; the new run action must not repeat it (S-9.7, S-9.8).
- The comment beside the body-cap setting says Werkzeug "returns 413 automatically" (`app.py:450-454`), but executing behavior enforces the cap only when a handler reads the body (auditor test on Flask 3.1.0 and Werkzeug 3.1.3: a 70,000-byte POST to a handler that reads no body returned 202). Any POST handler that never reads its body has no size refusal. Out of scope to fix here; one backlog line.
- One production symphony has 28 shadow days (PM live read): a recent deployment or a key drift; unconfirmed.

## Open Questions
| Q-n | Question | Blocks (AC ids or "plan") | Proposed default | Answer |
|---|---|---|---|---|
| Q-1 | How many NYSE trading days of tenure open a symphony to review, and how is the value set? | AC-1 | 126 trading days, set as `TENURE_REVIEW_MIN_TRADING_DAYS` in the environment file and read at each run, not dashboard-writable; absent or invalid means 126 with a visible note. Note: below 181 aligned days a symphony cannot be a retire-candidate | default approved |
| Q-2 | What history is the evidence basis? This conflicts with `DE-RETIRE-CORE-001` ("Live-only ... each symphony's own live `shadow_history`", `DECISIONS.md:10591`) and `feature-plans/retirement-recommender-core.md` AC-2 | AC-2, AC-3, AC-4 | Composer's account-scoped realized daily series, deposit-adjusted, as the primary basis; shadow history as fallback; no item or composite combines two bases | default approved |
| Q-3 | Which evidence items does the review show? | AC-3, AC-4, AC-5, AC-6, AC-7 | The five items: redundancy screen and gates, realized performance and composite, realized against own backtest, marginal contribution, long-VIX co-firing (information only) | default approved |
| Q-4 | What window does each item use? This replaces the existing 250-trading-day lookback (`advisors/retirement_recommender.py:89`) | AC-2, AC-3, AC-4, AC-5, AC-6, AC-7 | Each symphony's full tenure from its current invested-since date; a pair uses its overlapping days; the composite uses the common window in Q-13 | default approved |
| Q-5 | What is the verdict scale and rule? | AC-8 | keep, watch, retire-candidate; retire-candidate is the existing rule; watch when realized total return trails the backtest by more than 10 percentage points or removal raises portfolio return without deepening its drawdown; "no verdict: no evidence available" when redundancy, backtest and marginal items are all unavailable | default approved |
| Q-6 | When does the review run? | AC-9 | The existing 03:45 nightly tick plus "Run review now"; one run at a time; a tick during an on-demand run starts nothing; only the latest completed run is shown | default approved |
| Q-7 | Does tenure reset on redeploy or rename? | AC-1 | Tenure counts from the invested-since date Composer reports for the current deployment; a rename does not reset it | default approved |
| Q-8 | Is an export file needed for the end-of-year review? | NB-5 | No; panel and API JSON only | default approved |
| Q-9 | Is the plan Sensitive, and how? | plan | money for AC-1 to AC-6, AC-8, AC-10 (the review drives real-money keep and retire decisions); security for AC-1, AC-8, AC-9 (authentication, CSRF and a new write action) | default approved |
| Q-10 | Which products count as long-VIX, and what does co-firing measure? | AC-7 | VIXY, VIXM, UVXY, UVIX, VXX (the code's VIX family without SVXY and SVIX); held days, co-held days, and peak portfolio long-VIX share with and without the symphony; information only | default approved |
| Q-11 | Should approve and reject refuse a symphony that is not a retire-candidate in the latest run, and should a prior decision stay visible when the verdict changes? The routes deliberately accept any id today (`app.py:7923-7929`) | AC-8 | Routes unchanged; a prior approved or rejected decision stays visible on the row with its checklist whatever the current verdict; buttons only on a pending retire-candidate | default approved |
| Q-12 | The source reads "EoY analysis on the," with its object missing: what is analyzed? | plan | The live symphonies in the configured Composer accounts | default approved |
| Q-13 | What window and population does the composite use? The existing recommender normalizes every roster symphony with a series over the shared lookback (`advisors/retirement_recommender.py:486-500`) | AC-4 | The latest threshold-many (default 126) trading days through the as-of day, normalized across eligible symphonies on the Composer basis only; a fallback-basis symphony gets "not comparable: basis differs" | default approved |
| Q-14 | May a backtest feed the review? This conflicts with `DE-RETIRE-CORE-001` ("no backtest/synthetic-history sourcing", `DECISIONS.md:10591`) | AC-5, AC-8 | Yes, as a labelled counterfactual item and as one input to the watch rule only; it never enters the screen, the gates, the composite or the retire-candidate rule | default approved |
| Q-15 | How is explainer spend bounded when the window grows every trading day? The reuse gate compares the day count exactly (`app.py:983`, `:1016-1018`) | AC-9 | Reuse the stored explanation when the pair, verdict and every figure at 2 decimal places are unchanged except the window end date and day counts; show each explanation with the date it was written | default approved |
| Q-16 | Which "correlation analysis that seemingly never got finished" is meant? The Retirement Recommender and the AI Advisor Correlations panel (`app.py:6296-6318`, same diagnostic over sparse trigger-day series) are both candidates | plan | The Retirement Recommender, the one that never produces output | default approved |
| Q-17 | Where does the review appear? | AC-1, AC-8, AC-9 | The existing Retirement Recommendations panel on the AI Advisor Overview tab and `GET /api/retirement-recommendations`, plus a "Run review now" action on that panel | default approved |
| Q-18 | What makes a symphony live, and what happens when Composer's statistics cannot be read? | AC-1 | Live means present in this run's Composer statistics response for a configured account; if any configured account's statistics cannot be read the run does not complete and the previous run stays shown with a failed-attempt notice | default approved |
| Q-19 | Pair with a member that has no composite | AC-3, AC-4, AC-8 | Never a retire-candidate from that pair; at most watch; shown with the reason (matches the existing selection, which returns no candidate when either composite is missing, `advisors/retirement_recommender.py:263-264`, `:277-278`) | default approved |
| Q-20 | An eligible symphony with fewer days inside the common composite window than the window holds | AC-4, AC-8 | Excluded from the composite normalization population, never computed on fewer days; shows "insufficient coverage in the common window (N of M days)"; verdict at most watch | default approved |
| Q-21 | The panel has no confirmed operator workflow file (no `.claude/workflows/` directory); non-blocking | plan | No workflow file exists; the operator confirms the panel flow (User Tasks T-1 to T-3) at approval | default approved |
| Q-22 | Window for the with/without portfolio comparison | AC-6 | The span covered by all eligible symphonies; not-yet-eligible symphonies excluded from the comparison portfolio and shown as excluded | operator approved: the span covered by all eligible symphonies; not-yet-eligible symphonies excluded from the comparison portfolio and shown as excluded |

## Decisions
| ID | Decision | Rationale |
|---|---|---|
| D-1 | No pairwise, portfolio or composite figure combines two bases | Project data-honesty rule: every figure states one basis; a mixed figure has none |
| D-2 | The panel and the GET route read the stored run and make no Composer request | `feature-plans/mdd-window-truth.md` ruled out a live Composer call on the render path; the existing GET never recomputes (`app.py:4302-4311`) |
| D-3 | Backtest and without-symphony figures are labelled counterfactual and never feed realized fields | `docs/audit/PERF-WINDOW-TRUTH-2026-09-03.md` names the backtest trap; `DE-POSTMORTEM-INTEGRITY-001` provenance discipline |
| D-4 | The as-of day is the latest closed NYSE session | A run during or before the session has no closing value for the current day, and the tick fires on non-trading days too (`app.py:1254`) |

## Docs to Update
- `docs/generated/` one file per touched module, including `docs/generated/advisors_retirement_recommender.md` and `docs/generated/app.md`, and a new file for any new module.
- `docs/generated/INDEX.md`.
- `README.md` (the review and its "Run review now" action).
- `.env.example` (`TENURE_REVIEW_MIN_TRADING_DAYS` with its default).
- An appended `DECISIONS.md` entry, minted at ship by the doc-writer.
- A drafted `.claude/CLAUDE.md` Key Files row for PM approval.

## Follow-Ups
- NB-4: evaluating undeployed candidate symphonies and generating replacements; tracked in `feature-plans/BACKLOG.md`.
- NB-8: a calendar-year or operator-chosen review window; tracked in `feature-plans/BACKLOG.md`.
- NB-13: persisting invested-since in the engine's state when the engine freeze lifts; tracked in `feature-plans/BACKLOG.md`.
- NB-14: showing the checklist immediately on Approve; tracked in `feature-plans/BACKLOG.md`.
- The shadow-history NULL-to-0.0 coercion (`analytics.py:1901-1902`): pre-existing, one backlog line in `feature-plans/BACKLOG.md`.
- The approve and reject client reading HTML refusals as JSON (`static/ai_advisor.js:1132`): pre-existing, one backlog line in `feature-plans/BACKLOG.md`.
- The body-cap comment claiming an automatic 413 (`app.py:450-454`) while the cap fires only on a body read: pre-existing, one backlog line in `feature-plans/BACKLOG.md`.
