# Approval packet: Symphony Tenure Review

## What is being built

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

## Acceptance criteria and scenarios

### AC-1: Tenure and eligibility for every live symphony
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

Negative coverage: N1 S-1.2, S-1.3, S-1.4, S-1.5, S-1.6, S-1.7, S-1.8, S-1.9, S-1.20, S-1.21, S-1.22 | N2 n/a: this rule refuses no input; an ineligible symphony is shown, not refused | N3 S-1.10, S-1.11 | N4 S-1.16, S-1.17, S-1.23 | N5 covered by AC-9 (S-9.3, S-9.4, S-9.5) | N6 S-1.12, S-1.18, S-1.19 | N7 S-1.13, S-1.14, S-1.15 | N8 n/a: the rows are read-only, with no edit held open

### AC-2: Realized basis and provenance on every figure
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

Negative coverage: N1 S-2.11 | N2 S-2.2 | N3 S-2.7, S-2.8, S-2.9, S-2.10 | N4 covered by AC-1 (S-1.16, S-1.17) | N5 covered by AC-9 (S-9.3, S-9.5) | N6 S-2.3, S-2.4, S-2.5, S-2.6, S-2.14 | N7 S-2.12, S-2.13 | N8 n/a: the basis is chosen inside a run, with no edit held open

### AC-3: Redundancy evidence on the review history
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

Negative coverage: N1 S-3.2, S-3.3, S-3.4, S-3.5, S-3.6, S-3.9, S-3.12 | N2 n/a: no input is refused; thin data reads "insufficient data" | N3 S-3.7, S-3.8, S-3.11 | N4 covered by AC-1 (S-1.16) | N5 covered by AC-9 (S-9.3) | N6 covered by AC-2 (S-2.3 to S-2.6, S-2.14) | N7 S-3.10 | N8 n/a: computed inside a run, with no edit held open

### AC-4: Realized performance since invested and the composite
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

Negative coverage: N1 S-4.4, S-4.5, S-4.7 | N2 n/a: no input is refused | N3 S-4.2, S-4.3, S-4.9, S-4.10 | N4 covered by AC-1 (S-1.16) | N5 covered by AC-9 (S-9.3) | N6 covered by AC-2 (S-2.3 to S-2.6, S-2.14) | N7 S-4.6 | N8 n/a: computed inside a run, with no edit held open

### AC-5: Realized result against the symphony's own backtest
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

Negative coverage: N1 S-5.12 | N2 S-5.2, S-5.3 | N3 S-5.8 | N4 covered by AC-1 (S-1.16) | N5 covered by AC-9 (S-9.3) | N6 S-5.4, S-5.5, S-5.6, S-5.7, S-5.11 | N7 S-5.9, S-5.10 | N8 n/a: computed inside a run, with no edit held open

### AC-6: Marginal contribution to the portfolio
Rule: When a review run evaluates an eligible symphony, the panel and API shall show the comparison portfolio's total return and max drawdown in percent over the comparison window (the span covered by every eligible symphony) with the symphony and without it, where the comparison portfolio holds the eligible symphonies only, each not-yet-eligible live symphony is excluded from it and shown as excluded, and "without" removes the symphony's realized value and leaves every other eligible symphony's realized value unchanged with no reallocation, labelled "counterfactual: portfolio without this symphony", and the differences in percentage points.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-6.1 | B | an eligible symphony and at least one other eligible symphony on the same basis | a review completes | with and without figures and both differences are shown with the counterfactual label and the comparison window, whose first date is the latest first date among the eligible symphonies |
| S-6.2 | N1 | it is the only eligible symphony | a review completes | the item reads "not applicable: no other eligible symphonies" |
| S-6.3 | N3 | another eligible symphony lacks values on some days inside the comparison window | a review completes | the figures use only days on which every eligible symphony in the comparison portfolio has a value, and the day count shows the reduced number |
| S-6.4 | N1 | the symphony and a peer are on different bases | a review completes | the item reads "unavailable: bases differ" |
| S-6.5 | N7 | a completed review | the operator opens the dashboard hero, Performance and History tabs | no "without" figure appears there as realized portfolio performance |
| S-6.6 | N1 | a live symphony that is not yet eligible, invested more recently than every eligible symphony | a review completes | it is excluded from the comparison portfolio and listed as "excluded: not yet eligible"; the comparison window's first date is not moved later by it |

Negative coverage: N1 S-6.2, S-6.4, S-6.6 | N2 n/a: no input is refused | N3 S-6.3 | N4 covered by AC-1 (S-1.16) | N5 covered by AC-9 (S-9.3) | N6 covered by AC-2 (S-2.3 to S-2.6, S-2.14) | N7 S-6.5 | N8 n/a: computed inside a run, with no edit held open

### AC-7: Long-VIX co-firing, information only
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

Negative coverage: N1 S-7.2, S-7.3 | N2 n/a: no input is refused | N3 S-7.4, S-7.5 | N4 covered by AC-1 (S-1.16) | N5 covered by AC-9 (S-9.3) | N6 S-7.6, S-7.8 | N7 S-7.7 | N8 n/a: computed inside a run, with no edit held open

### AC-8: One verdict per eligible symphony and the decision lifecycle
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

Negative coverage: N1 S-8.5, S-8.6, S-8.19 | N2 S-8.16 | N3 S-8.7, S-8.8, S-8.20 | N4 S-8.13, S-8.14, S-8.15 | N5 S-8.12 | N6 S-8.17 | N7 S-8.10, S-8.11 | N8 S-8.18

### AC-9: Nightly and on-demand runs, latest run only
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

Negative coverage: N1 S-9.11 | N2 S-9.3 | N3 S-9.13 | N4 S-9.7, S-9.8, S-9.15 | N5 S-9.3, S-9.4, S-9.5, S-9.6 | N6 S-9.9 | N7 S-9.10, S-9.14, S-9.17 | N8 S-9.12

### AC-10: No trade, Composer write or engine path
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

Negative coverage: N1 n/a: the rule judges code paths, not input values | N2 n/a: nothing is refused; a write path must not exist | N3 n/a: no value is computed by this rule | N4 covered by AC-1 (S-1.16, S-1.17), AC-8 (S-8.13 to S-8.15) and AC-9 (S-9.7, S-9.8) | N5 n/a: the rule is static, so concurrency cannot change it | N6 n/a: no dependency is called by the scans | N7 S-10.2, S-10.3, S-10.4, S-10.5, S-10.6, S-10.7 | N8 n/a: no edit exists on these paths

## Rule-by-entry-point matrix

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

## Narrowed mappings

R-1: The feature supports the operator's end-of-year analysis; maps to AC-8, AC-9, NB-5, NB-8; left out: no export file and no calendar-year window selector; the review covers each symphony's tenure window

## User tasks

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

## Open questions

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

Sensitive: money, security (AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-8, AC-9, AC-10). qa writes hidden scenarios for the named criteria after approval; they are not shown to the builders.
