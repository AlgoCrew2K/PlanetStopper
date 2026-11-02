# advisors/tenure_review

> Pure compute for the Symphony Tenure Review: for every live symphony, six labelled evidence items and one verdict (retire-candidate, watch, keep, or no verdict), with a tenure gate that withholds a verdict from young symphonies.

**Source:** `advisors/tenure_review.py`
**Last updated:** 2026-11-02

## Overview

The review answers "has this symphony earned its place" after it has been live long enough to judge. It is advisory only. This module does no network call, no database write, and has no Composer client, order, invest, deploy or liquidation path. Its callers fetch the inputs (`advisors/tenure_review_runner.py`) and persist the result (`app._tenure_review_job`).

Units: percent scale everywhere (a return of 0.5 is +0.5 percent); max drawdown is a percent number that is 0 or negative; `None` means unknown or not computable and is never replaced by 0. The one exception: metrics inside a retire-candidate recommendation keep the scale of `analytics.compute_quantstats_metrics` (fractions), so rows written by the retirement recommender and rows written from a review share one meaning per key.

Tenure is counted in NYSE trading days from the symphony's current deployment's invested-since date through the run's as-of day (`as_of_trading_day`: the latest NYSE session whose regular session has closed, read in US Eastern, so a Sunday-night or on-demand run during market hours counts through the previous session, and a run after the close counts that day). A symphony below the threshold is shown as "not yet eligible" with the days remaining and gets no verdict. A symphony with no invested-since date reads "tenure unavailable", never a tenure of 0.

## The six evidence items (`EVIDENCE_ITEM_ORDER`)

| Item | What it is | Basis label |
|------|------------|-------------|
| performance | realized total return, annualized return, max drawdown, Sharpe, Sortino, Calmar over the qualifying window | `realized: Composer account, deposit-adjusted` (`COMPOSER_BASIS_LABEL`), or `PlanetStopper shadow history from the 2026-06-22 go-live floor` (`SHADOW_BASIS_LABEL`) when Composer's series is unusable |
| composite | the retirement recommender's composite score, normalized across the eligible symphonies of one basis over a common window (`score (0 to 1, fleet-normalized)`) | same basis as performance |
| redundancy | the retirement recommender's correlation screen and both gates (uncertainty, structural) rerun on the longer history, every gate result shown (passed, not met, insufficient data, undefined, not evaluated) | pairwise return correlation across live symphonies |
| backtest | realized total return and drawdown beside a Composer backtest of the symphony's current definition over the same dates | `counterfactual: Composer backtest of today's definition`, never presented as realized |
| marginal | portfolio return and drawdown of the eligible symphonies on one basis with and without this symphony | `counterfactual: portfolio without this symphony` |
| long_vix | days this symphony held a long-volatility product (`LONG_VIX_TICKERS`: the frontrunner detector's VIX family without the inverse products SVXY and SVIX), days another symphony did too, and the peak share of the portfolio with and without it | `informational: not used in the verdict` |

Every figure carries a unit, a window, a subject and a source. An item that cannot be computed is listed with a reason (`missing_items`); `not_applicable` counts as no evidence exactly like unavailable.

## Verdict (`decide_verdict`)

1. No available redundancy, backtest or marginal item gives no verdict: `no verdict: no evidence available`, or `no verdict: the comparison has no figures yet` when only context items (performance, composite, long_vix) exist.
2. `retire-candidate` when the existing retirement rule flags the symphony: a pair that passes both gates in which it has the lower composite. A pair in which either member has no composite never flags.
3. Otherwise `watch` when its realized total return trails its backtest by more than `BACKTEST_TRAIL_WATCH_PP = 10.0` percentage points (reason `trails its own backtest`), or when removing it would raise the portfolio's total return and leave the portfolio's max drawdown no deeper (reason `drags portfolio return and drawdown`).
4. Otherwise `keep`.

A redundancy item counts as verdict evidence only when the comparison could have decided: a same-basis peer with a composite, defined correlation, and at least `STRESS_GATE_MIN_ALIGNED_DAYS` (181) aligned days, or a flag. Below that the item is listed as missing with the peer's display name and aligned days, so "no redundant sibling" never appears for a comparison that could not have flagged anything.

Only a retire-candidate enters the existing approve/reject lifecycle (`POST /ai-advisor/retirement/approve` and `/reject`, status-only writes). Keep and watch rows show no buttons. An approved decision row is keyed by the Composer hash and shown on whichever verdict the next review gives.

## API Reference

| Symbol | Description |
|--------|-------------|
| `DEFAULT_MIN_TENURE_TRADING_DAYS = 126` | default threshold, half of a 252 session year |
| `TENURE_THRESHOLD_ENV_KEY = "TENURE_REVIEW_MIN_TRADING_DAYS"` | the environment key an operator sets; read fresh from the env file by `app._tenure_review_settings`. It is not in `_SETTINGS_WRITE_ALLOWLIST`, so the dashboard cannot write it |
| `parse_threshold(raw) -> ThresholdSetting` | a string of digits with value 1 or more is used; anything else (absent aside) falls back to 126 with the note `configured threshold invalid; 126 in use` |
| `as_of_trading_day(now_et) -> date`, `count_trading_days(start, end) -> int` | the as-of session and the inclusive NYSE session count |
| `realized_window`, `performance_metrics`, `qualifying_window`, `backtest_window` | window and metric helpers |
| `evaluate_redundancy`, `backtest_comparison`, `marginal_contribution`, `long_vix_cofiring` | the evidence builders |
| `decide_verdict(evidence) -> dict` | verdict, verdict_text, verdict_reasons, missing_items |
| `build_review(ReviewInputs) -> ReviewResult` | one entry per live symphony plus a recommendation row per retire-candidate |
| `run_record(result, completed_at) -> dict` | the persisted run record (strict JSON, no non-finite numbers); the `TENURE_REVIEW_RUN` advisor row's `raw_response` |
| `TENURE_REVIEW_RUN_ROLE = "TENURE_REVIEW_RUN"` | `advisor_role` of the run record row |
| `REVIEW_RUN_STATE` | process-wide single-flight guard: `try_start(trigger)` returns an owner token or None, `finish_success`, `finish_failure(owner, reason, when)`, `release`, `snapshot()` (running, running_since, trigger, last_failed_attempt) |

## Types
`ThresholdSetting`, `SymphonyInput`, `HoldingsInput`, `ReviewInputs`, `ReviewResult` (dataclasses).

## Where it runs
- Nightly: `app._run_retirement_recommender_tick` (the 03:45 slot) first claims the run slot, then a daemon thread runs the review (`app._tenure_review_job("nightly", owner)`) and then the retirement recommender run, independently. The tick starts nothing when the newest completed nightly run is not degraded and already covers the current as-of day at the threshold now in effect (`app._nightly_review_already_done`), so a weekend or holiday tick does not repeat a review of the same session.
- On demand: `POST /ai-advisor/retirement/review/run` ("Run tenure review" on the Retirement tab). It reads no body, returns 202 `{"status": "started"}` or 409 `{"status": "already_running", "message": "a review is already running"}`. It has no market-hours gate: a run during market hours counts tenure through the previous session, and the client paces its Composer reads at 6 seconds per request (`COMPOSER_READ_SPACING_SECONDS`). The sibling recommender route `POST /ai-advisor/retirement/run` does gate on market hours.
- Read: `GET /api/retirement-recommendations` carries the review as `review_run`, `review_entries`, `review_status` and `review_read_failed` beside the existing keys; the panel renders the same data.

Recommendation rows are written before the run row, and never for a run that did not complete. Readers show only rows carrying the newest readable run's `review_run_id`. A run that does not complete (Composer statistics unavailable for any configured account, an unreadable response) writes nothing and the previous completed run stays shown with a failed-attempt notice in US Eastern.

## Internal Dependencies
`analytics` (quantstats metrics), `market_calendar`, `advisors.frontrunner_detector` (VIX family), `advisors.retirement_recommender` (composite, gates, thresholds), `advisors.correlation_diagnostic` (aligned pairs, Pearson).

## Boundary
No Composer write, no read or write of `LIVE_EXECUTION`, and no execution-engine function. Enforced by the no-trade scans in `tests/security/` (module scans extended to the new modules; the call-graph scan extended to the tick, the on-demand run action, the fetch helper and the approve and reject routes). I did not run them for this document.
