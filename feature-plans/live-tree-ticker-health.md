# Feature: Flag dead tickers in live symphony trees
Status: draft
Story: none: an operator remark of 2026-11-16 on a PM report, saved verbatim with the PM's intake answers in feature-plans/live-tree-ticker-health.source.md
Type: feat
Suite: none
Repos: AlphaBotPM
Base: main
Ref: AlphaBotPM@c3e12882141ae601f900fdc13e0c342462a739bb
Sensitive: security (AC-9)
Depends on: none

## Trigger
The operator's remark of 2026-11-16 on the PM's report that nothing in the app flags a dead ticker in a live symphony tree, with the PM's intake answers; all saved verbatim in `feature-plans/live-tree-ticker-health.source.md`. The investigation confirms the gap at the ref:

1. No code path judges whether a ticker in a live tree can still trade. The only Alpaca asset reader is `advisors/universe_provider.py`, which requests active assets only (`advisors/universe_provider.py:103`), keeps those on five exchanges (`:62`, `:78-85`) and caches the set for 7 days (`:67-68`); its consumers are the Strategy Builder paths (`DECISIONS.md:1807`, DE-SB-UNIV-001) and the Asset Swaps generator (`advisors/asset_swap_engine.py:74`).
2. `advisors/symphony_schema.extract_tickers` (`advisors/symphony_schema.py:585-613`) collects asset-node tickers and condition-block operands (`:616-645`) but never the legacy if-child operands `lhs-val` and `rhs-val`. Measured on the 11 trimmed real trees in `tests/fixtures/advisors/frontrunner/` and on the complete captured definition `tests/fixtures/composer/tenure-review/case4_definition_nOyb55RMGVCKPiYXv7TI.json`: every tree has tickers that appear only as such operands (4 to 113 per tree), so a dead ticker read only by a condition is invisible to it.
3. The nightly tenure review reads each eligible symphony's definition and backtests it (`advisors/tenure_review_runner.py:162-197`); a refused backtest reads "Composer rejected the backtest (HTTP 422)" (`advisors/tenure_review.py:804`) and names no ticker.

## Summary
When a ticker that a live symphony's current Composer tree buys or reads in a condition can no longer trade, the operator sees it on the dashboard page and gets one Discord message within one trading day, naming the symphony, the ticker, how many asset nodes and conditions use it and whether the symphony holds it now, so the tree can be fixed in Composer.
- A nightly off-hours check judges every ticker each live tree names as an asset or as any condition operand (AC-1), against Alpaca's asset status read that night (AC-2).
- The dashboard page `/` shows one warning line per symphony and dead ticker (AC-3), and a muted notice whenever the check could not judge something, never an all-clear (AC-4).
- One Discord message per newly found symphony and ticker pair, never repeated until it clears (AC-5); a fixed ticker's warning clears (AC-6).
- The check makes no Composer request while the market is open or while another in-process off-hours Composer reader runs, never runs twice at once, and leaves no partial result (AC-7).
- The check runs every calendar night and catches up a missed night at the next daemon start outside market hours (AC-10).
- Terms used throughout: a run **ends** when it stops for any reason (finished, skipped, unavailable, failed or stopped by the daemon); a run **completes** when it ends after reading at least one account's roster and obtaining Alpaca's answer for at least one ticker, and a completed run may still leave symphonies or tickers not checked; a symphony is **judged** by a run that read its tree, and a ticker is **judged** by a run that read Alpaca's answer for it. Checked times and staleness count completed runs only (D-6).
- The engine, Composer write paths, the tenure review, `/health`, the header and the Strategy Builder's tradeable set stay as at the ref (AC-8); credentials and names stay out of logs, markup and mentions (AC-9).

## Requirements as Given
[G-1] OPERATOR SOURCE (verbatim, 2026-11-16): "The fact that you're reporting "problems found in the app" and not fixing them is also suspect.." live-tree-ticker-health.source.md:1
[G-2] The problem it refers to, as the PM reported it (verbatim): "Nothing in the app flags a dead ticker in a live tree, as with SHNY." live-tree-ticker-health.source.md:2
[G-3] and, from the same report: "SHNY is dead. live-tree-ticker-health.source.md:2
[G-4] Alpaca lists SHNY (MicroSectors Gold 3x ETN) as inactive and not tradable, and its last daily bar was Nov 11. live-tree-ticker-health.source.md:2
[G-5] Composer refuses to backtest any tree that references it. live-tree-ticker-health.source.md:2
[G-6] Planet LQD and Trolling each have 25 nodes that buy SHNY. live-tree-ticker-health.source.md:2
[G-7] Each also has a condition comparing SHNY's 118-day and 114-day moving-average return." live-tree-ticker-health.source.md:2
[G-8] (Intake answer 1) "What this makes true: when any ticker a live symphony's current Composer tree references — as a buyable asset or as an operand of a condition — is no longer tradable or has no price data, the operator learns it within one trading day, naming the symphony, the ticker, how many nodes use it and whether it is held now, so the operator can fix the tree in Composer before the symphony fails to rebalance." live-tree-ticker-health.source.md:5
[G-9] (Intake answer 2) "Evidence (PM, 2026-11-16, read-only): Alpaca `GET /v2/assets/SHNY` → status inactive, tradable false; last SIP daily bar 2026-11-11. Composer POST /backtest of both trees → HTTP 422 `no-series-data-available`, ticker `EQUITIES::SHNY//USD`. Trees `8FAXAnQmYi1INDubazeC` and `n2ooAZTvBRN6ZzpMmWmU` (live roster ids) each: 25 asset nodes ticker SHNY plus one if-child with lhs-val and rhs-val SHNY (moving-average-return 118 vs 114). Neither held SHNY on 2026-11-16. The nightly tenure review already lists "backtest" as missing for those two symphonies without saying why." live-tree-ticker-health.source.md:6
[G-10] (Intake answer 3) "Who/surface: the operator, on the dashboard (header status line and/or /health degraded reasons — find what surfaces exist: GET /api/header-status, /health degraded_reasons) and via the existing Discord webhook path in reporting.py. Off-hours only for Composer reads (Composer's rate limit is shared with the minute engine; see the retirement Run-now market gate DE-RETIRE-VISIBILITY-001 AC-7b). Existing pieces to find and cite, not reinvent: `advisors/composer_portfolio_client.read_symphony_definition` (/score), the live roster source (symphony-stats-meta), `advisors/symphony_schema.extract_tickers` (known backlog: misses legacy rhs-val operand tickers — the plan must require condition operands to be covered), `advisors/universe_provider` (Alpaca assets, weekly-cached 7-day TTL — a weekly cache cannot meet "within one trading day"), the 03:45 nightly tick pattern in app.py run_scheduler." live-tree-ticker-health.source.md:7
[G-11] (Intake answer 4) "For each flow, negatives to specify: Alpaca unreachable or erroring → the check reports itself unavailable, never "all clear" and never a false dead-ticker alarm; Composer /score fails for one symphony → that symphony is reported unchecked, others still checked; a symphony with no dead tickers → nothing alarming shown; the same dead ticker on later nights → no repeated Discord alert (one per symphony+ticker until it clears); the ticker becomes tradable again or is removed from the tree → the warning clears; market open → no Composer reads; a run already in progress → no second concurrent run; restart mid-run → no partial alarm state." live-tree-ticker-health.source.md:8
[G-12] (Intake answer 5) "Data/contracts: whatever persisted record is needed to make alerts once-only and to show the last result (architect decides; additive migration only if needed). No change to the engine (alpha_bot_execution.py, math_engine.py)." live-tree-ticker-health.source.md:9
[G-13] (Intake answer 6) "Not building: any automatic edit of a live Composer symphony (the app has no write path to live symphonies by design; the operator fixes trees in Composer); suggesting a replacement ticker; intraday checks; checking non-live candidate trees (Strategy Builder / frontrunner proposals); price-staleness detection for tickers that still trade." live-tree-ticker-health.source.md:10
[G-14] (Intake answer 7) "Must not change: the engine's minute cycle; the existing tenure review output; Composer write paths (none may be added)." live-tree-ticker-health.source.md:11
[G-15] (Intake answer 8) "Sensitive: no order path; mark not sensitive unless your investigation finds otherwise." live-tree-ticker-health.source.md:12
[G-16] (Intake answer 9) "Earlier criteria on the same surfaces to find: DE-RETIRE-VISIBILITY-001 (nightly tick + market gate), the /health degraded reasons and header-status contracts (refresh-timing / ux-overhaul docs), DE-HYGIENE / universe_provider decisions." live-tree-ticker-health.source.md:13

## Requirement Inventory

| R-n | Requirement item | Source (G ids) | Maps to (AC ids, or NB-n) | Scope (full, or narrowed: what is left out) |
|---|---|---|---|---|
| R-1 | The app itself reports the dead-ticker problem the PM found by hand | G-1, G-2 | AC-3, AC-5 | narrowed: the app reports the problem; fixing the tree stays the operator's action in Composer (NB-1) |
| R-2 | A dead ticker in a live symphony's tree is flagged | G-2, G-3 | AC-1, AC-2, AC-3 | full |
| R-3 | A ticker Alpaca lists as inactive or not tradable is judged dead | G-4, G-8 | AC-2 | full |
| R-4 | A ticker whose last daily bar is days old is judged dead | G-4 | AC-2 | narrowed: judged by Alpaca's asset status and listing, not by the date of its last bar (Q-1, NB-5) |
| R-5 | A ticker Composer refuses to backtest is recognised as dead | G-5, G-9 | NB-6 | full |
| R-6 | A dead ticker bought by asset nodes is flagged with the number of asset nodes that buy it | G-6, G-8 | AC-1, AC-3, AC-5 | full |
| R-7 | A dead ticker read by a condition is flagged, including a condition comparing two of its own indicators | G-7, G-10 | AC-1, AC-3 | full |
| R-8 | Every ticker the current tree references as a buyable asset or as a condition operand is covered | G-8 | AC-1 | full |
| R-9 | A ticker with no price data is flagged | G-8 | AC-2 | narrowed: counts only when Alpaca does not list the ticker; a listed, tradable ticker without recent bars is excluded (Q-1, NB-5) |
| R-10 | The operator learns of a dead ticker within one trading day | G-8 | AC-10 | narrowed: holds whenever the daemon runs outside market hours between a session's close and the next open; a night with no such window is reported by the stale notice (S-4.10) |
| R-11 | The report names the symphony | G-8 | AC-3, AC-5 | full |
| R-12 | The report names the ticker | G-8 | AC-3, AC-5 | full |
| R-13 | The report says how many nodes use the ticker | G-8 | AC-3, AC-5 | full |
| R-14 | The report says whether the symphony holds the ticker now | G-8, G-9 | AC-3, AC-5 | full |
| R-15 | The operator fixes the tree in Composer; the app does not | G-8, G-13 | NB-1 | full |
| R-16 | The two live symphonies that buy SHNY in 25 asset nodes and read it in one condition are flagged, as not held | G-9 | AC-3 | full |
| R-17 | The tenure review names "backtest" as missing without saying why | G-9 | NB-13 | full |
| R-18 | The operator sees the warning on the dashboard | G-10 | AC-3, AC-4 | full |
| R-19 | The header status line or `/health` degraded reasons carry the warning | G-10 | AC-8, NB-9 | narrowed: the warning is on the dashboard page; the header engine label and `/health` stay engine-health only (Q-3) |
| R-20 | The operator is told through the existing Discord webhook | G-10 | AC-5 | full |
| R-21 | Composer reads happen off-hours only, as the retirement Run-now market gate does | G-10, G-16 | AC-7, AC-10 | full |
| R-22 | Live symphonies are those of the symphony-stats-meta roster of the configured accounts, and their trees come from the score endpoint | G-10 | AC-1, AC-3 | narrowed: which existing functions are reused is the Gate-2 approach's choice |
| R-23 | Legacy condition operands (`lhs-val`, `rhs-val`) are covered | G-10 | AC-1 | full |
| R-24 | The tradability read is fresher than a weekly cache | G-10 | AC-2 | full |
| R-25 | Alpaca unreachable or erroring: the check reports itself unavailable, never all clear and never a false alarm | G-11 | AC-2, AC-4 | full |
| R-26 | Composer score read fails for one symphony: that symphony is unchecked and the others are still checked | G-11 | AC-4 | full |
| R-27 | A symphony with no dead ticker shows nothing alarming | G-11 | AC-3 | full |
| R-28 | The same dead ticker on later nights sends no repeated Discord alert, one per symphony and ticker until it clears | G-11 | AC-5 | full |
| R-29 | The warning clears when the ticker trades again or leaves the tree | G-11 | AC-6 | full |
| R-30 | No Composer read while the market is open | G-11 | AC-7 | full |
| R-31 | No second concurrent run | G-11 | AC-7 | full |
| R-32 | A restart mid-run leaves no partial alarm state | G-11 | AC-7 | full |
| R-33 | A stored record makes alerts once-only and shows the last result | G-12 | AC-3, AC-5 | full |
| R-34 | Any migration is additive | G-12 | AC-8 | full |
| R-35 | The engine files do not change | G-12, G-14 | AC-8 | full |
| R-36 | No replacement ticker is suggested | G-13 | NB-2 | full |
| R-37 | No intraday check | G-13 | NB-3 | full |
| R-38 | Non-live candidate trees are not checked | G-13 | NB-4 | full |
| R-39 | No price-staleness detection for tickers that still trade | G-13 | NB-5 | full |
| R-40 | The engine's minute cycle does not change | G-14 | AC-7, AC-8 | full |
| R-41 | The tenure review output does not change | G-14 | AC-8 | full |
| R-42 | No Composer write path is added | G-14 | AC-8 | full |
| R-43 | Not sensitive unless the investigation finds otherwise | G-15 | AC-9 | narrowed: marked security for the new use of two credentials (AC-9); no money marking |
| R-44 | Earlier criteria on the nightly tick, market gate, `/health`, header and universe provider are found and reconciled | G-16 | AC-7, AC-8, AC-10 | full |

## User Tasks
Workflow (dashboard): no workflow file: the repository has no `.claude/workflows/` directory at the ref, so no confirmed workflow exists for the dashboard page.

### T-1: Learn that a live symphony references a dead ticker and confirm the fix

| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Reads the Discord channel the morning after a ticker dies | none | one message names the symphony, its Composer hash id, the ticker, the reason, the asset node and condition counts and the held state | S-5.1 |
| 2 | Opens the dashboard page `/` | none | a warning above the hero section shows one line for that symphony and ticker, with the check's completion time | S-3.1 |
| 3 | Edits the tree in Composer to drop the ticker (outside the app) | the symphony's tree in Composer | nothing changes in the app until the next check | S-3.9 |
| 4 | Opens `/` after the next nightly check | none | the line for that symphony and ticker is gone; no Discord message is sent for the clearing | S-6.2 |

### T-2: See that the check could not judge everything

| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Opens `/` after a night on which Alpaca could not be read | none | a muted notice says the ticker check was unavailable and why, with the last completed check's time; earlier warnings still show with their own time | S-4.7, S-4.6 |
| 2 | Opens `/` after a night on which one symphony's tree could not be read | none | the notice names that symphony as not checked with the reason; warnings for the others reflect that night | S-4.1 |

## Earlier criteria on the same surface

| Earlier criterion (plan and AC) | Surface | Disposition (kept, changed or contradicted) | Why |
|---|---|---|---|
| `feature-plans/refresh-timing/02-scheduler-events-health.md` AC-4 | `GET /health` status and `degraded_reasons` | kept | Its reasons are engine and scheduler health only (`app.py:1688-1712`); a dead ticker adds no reason (S-8.6, Q-3) |
| `feature-plans/refresh-timing/02-scheduler-events-health.md` AC-3 (S-3.4, every slot at its time) | `run_scheduler` slots | kept | Every slot at the ref keeps its time (`app.py:3090-3107`); S-7.9 |
| `feature-plans/refresh-timing/00-overview.md:208` R-97 (header reads "Degraded" only from `/health` reasons) and `feature-plans/refresh-timing/04-pages-and-header.md` AC-1 | Header engine label on the four other pages, `GET /api/header-status` | kept | The header label precedence (`static/chrome.js:351-375`) is unchanged; no warning turns it "Degraded" (S-8.6) |
| `feature-plans/refresh-timing/03-dashboard-refresh.md` AC-4 (the header on `/`: stale cue, catch-up refresh, countdown; carries R-97) | Header engine label and countdown on `/` | kept | The warning is a separate page element; the label, its precedence and the countdown on `/` are unchanged (S-3.11, S-8.6) |
| `feature-plans/retirement-visibility.md` AC-7b (Run now refused while the market is open, Composer rate limit shared with the engine) | Composer reads by off-hours jobs | kept | Applied to the check: it makes no Composer request while `market_calendar.get_market_state` reads "open" (`market_calendar.py:132`); S-7.2, S-7.3 |
| `feature-plans/retirement-visibility.md` AC-7 (single-flight lock shared by the 03:45 tick and Run now) | Retirement run lock | kept | The check neither takes nor changes that lock; its own single-flight rule is S-7.4 |
| `feature-plans/symphony-tenure-review.md` AC-9 (nightly 03:45 review, latest run only) | Tenure review run and panel | kept | The review's record and panel are unchanged (S-8.5); the check does not overlap its Composer reads (S-7.5) |
| `feature-plans/symphony-tenure-review.md` AC-5 S-5.3 (refused backtest reads "Composer rejected the backtest" with the status) | Tenure review backtest item | kept | The item still names no ticker (NB-13); the dead ticker is reported by this feature's own surfaces |
| `feature-plans/symphony-tenure-review.md` AC-10 (no trade, Composer write or engine path) | No-trade boundary | kept | Extended: the same boundary and scans cover the check's new paths (S-8.1, S-8.2, S-8.4) |
| `DECISIONS.md:11638` DE-SECURITY-CONTROLS-001 (tenure review run refused while the market is open) | Off-hours Composer reads | kept | Same rule as AC-7b, applied to the check (S-7.2) |
| `feature-plans/page-explainers-and-density.md` AC-5 S-5.3 and `feature-plans/aligned-tables-and-quiet-copy.md` AC-3 S-3.12 (existing banners byte-identical) | Dashboard banners | kept | The fleet and market-state banners stay byte-identical (S-3.11); the new warning is a separate element |
| `feature-plans/aligned-tables-and-quiet-copy.md` AC-3 (a fact appears once per screen) | Dashboard copy | kept | The warning states each fact once; the checked time appears once per warning (S-3.1) |
| `DECISIONS.md:1807` DE-SB-UNIV-001 (weekly-cached tradeable set for the Strategy Builder; also read by Asset Swaps, `advisors/asset_swap_engine.py:74`) | `advisors/universe_provider.py` | kept | The check does not read, refresh or replace that cache (S-2.13) |
| `DECISIONS.md:2266` DE-SB-BINARY-ENCODING (extract_tickers reads binary operands) | `advisors/symphony_schema.extract_tickers` and its callers | kept | Its results for its existing callers are unchanged (S-1.11); the check covers the legacy operands itself (S-1.2, S-1.3) |
| `DECISIONS.md:9404` DE-HYGIENE-R1-001 and `DECISIONS.md:11674` DE-HYGIENE-W34-001 | none of this plan's surfaces | kept | Searched for universe provider, tradability and ticker extraction rulings; neither touches them |

## Acceptance Criteria (Building)

### AC-1: Every ticker the current tree names is judged
Requirement items: R-2, R-6, R-7, R-8, R-22, R-23
Rule: When the nightly check reads a live symphony's current tree from Composer's score endpoint, the check shall judge every distinct ticker that the tree names as an asset node's ticker or as an operand of any condition (an if-child's `lhs-val`, its `rhs-val` when the right side is not a fixed value, a binary condition's operand tickers, and each ticker of a binary-compound list, at any depth of compound nesting), and shall judge no other string.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-1.1 | B | a live tree in which ticker T appears only as the ticker of asset nodes, and Alpaca reports T inactive | the nightly check completes | the dashboard warning line for that symphony and T reads "Used by n asset nodes and 0 conditions.", with n the count in the tree |
| S-1.2 | B | a live tree in which T appears only as an if-child's `lhs-val`, with no asset node, and Alpaca reports T inactive | the nightly check completes | the warning line for T reads "Used by 0 asset nodes and 1 condition." |
| S-1.3 | N1 | T appears only as an if-child's `rhs-val` and `rhs-fixed-value?` is false | the nightly check completes | T is judged; when Alpaca reports it inactive the warning line reads "Used by 0 asset nodes and 1 condition." |
| S-1.4 | N1 | an if-child with `rhs-fixed-value?` true and a numeric `rhs-val` | the nightly check completes | the numeric value is never judged, sent to Alpaca or named |
| S-1.5 | N1 | a binary-compound condition whose operand ticker is the placeholder `%` and whose ticker list holds A and B | the nightly check completes | A and B are judged; `%` is never judged, sent to Alpaca or named |
| S-1.6 | N1 | a compound if-child whose `lhs-val` holds a comma-joined label of A, B and C beside a condition block naming A, B and C | the nightly check completes | A, B and C are each judged once; the joined string is never judged, sent to Alpaca or named |
| S-1.7 | N1 | T named only inside an inner condition of a compound condition nested in another compound condition | the nightly check completes | T is judged |
| S-1.8 | N1 | T bought by 25 asset nodes and read by one if-child that compares two of T's own indicators (T on both sides) | the nightly check completes and Alpaca reports T inactive | one warning line for that symphony and T, reading "Used by 25 asset nodes and 1 condition." (a condition naming T on both sides counts once); T is judged once in the run |
| S-1.9 | N1 | a live tree with no asset node and no condition | the nightly check completes | the symphony is counted as checked; no warning line and no notice for it |
| S-1.10 | N1 | a live tree whose `asset_class` is present and is not `EQUITIES` | the nightly check completes | none of its tickers is sent to Alpaca; the notice names the symphony as not checked with the reason "asset class not covered by the check" |
| S-1.11 | N7 | the trees the asset swap engine, the community loader, the plan compiler and the weekly suggestions scheduler read (callers at `advisors/asset_swap_engine.py:878`, `:1167`, `advisors/community_strats.py:349`, `advisors/plan_tree_compiler.py:549`, `advisors/weekly_suggestions_scheduler.py:378`) | each reads the tickers of a tree containing legacy operands | each receives the same ticker set as at the ref |

Negative coverage: N1 S-1.3, S-1.4, S-1.5, S-1.6, S-1.7, S-1.8, S-1.9, S-1.10 · N2 n/a: the check refuses no tree; a tree it cannot cover is reported not checked (S-1.10) · N3 covered by AC-4 (S-4.1 to S-4.5) · N4 n/a: a nightly job with no caller to authorize · N5 covered by AC-7 (S-7.4) · N6 covered by AC-4 (S-4.1 to S-4.5) · N7 S-1.11 · N8 n/a: each tree is read once per run and no edit is held open
Questions: none

### AC-2: The verdict comes from Alpaca's status read that night
Requirement items: R-2, R-3, R-4, R-9, R-24, R-25
Rule: When the nightly check judges a ticker, the check shall read that ticker's asset record from Alpaca during that run and report the ticker dead when Alpaca reports it inactive, not tradable, or not listed; if Alpaca's answer for a ticker cannot be obtained or read, then the check shall report that ticker not checked, as AC-4 shows it, never healthy and never dead.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-2.1 | B | Alpaca reports T with status inactive and tradable false | the nightly check completes | T is dead with the reason "inactive and not tradable at Alpaca" |
| S-2.2 | N1 | Alpaca reports T with status active and tradable false | the nightly check completes | T is dead with the reason "not tradable at Alpaca" |
| S-2.3 | N1 | Alpaca reports T with status inactive and tradable true | the nightly check completes | T is dead with the reason "inactive at Alpaca" |
| S-2.4 | N1 | Alpaca answers not found for T | the nightly check completes | T is dead with the reason "not listed at Alpaca" (Q-2) |
| S-2.5 | B | Alpaca reports T with status active and tradable true | the nightly check completes | T is healthy; no warning line names T |
| S-2.6 | N1 | Alpaca reports T active and tradable on an exchange outside NASDAQ, NYSE, ARCA, BATS and AMEX | the nightly check completes | T is healthy; the exchange plays no part in the verdict |
| S-2.7 | N1 | Alpaca reports T active and tradable, and T has no daily bar for the last 5 sessions | the nightly check completes | T is healthy; no warning names T (Q-1, NB-5) |
| S-2.8 | B | Alpaca's status for T changed to inactive after the previous run, and the Strategy Builder's weekly tradeable set, cached 3 days earlier, still lists T | the nightly check completes | T is dead on the dashboard and in Discord after this run |
| S-2.9 | N6 | every Alpaca request in the run times out or fails to connect | the run ends | no ticker is dead or healthy from this run; the run reads "unavailable: Alpaca unreachable" (S-4.7); no Discord message; earlier warnings stay as in S-4.6 |
| S-2.10 | N4 | Alpaca answers 401 or 403 | the run ends | the run reads "unavailable: Alpaca refused the credentials"; no ticker is dead or healthy from this run |
| S-2.11 | N6 | Alpaca answers 429 or 5xx for some tickers and 200 for the rest | the nightly check completes | the answered tickers are judged; each unanswered ticker is not checked and the notice counts them per symphony with the reason "Alpaca rate limit" or "Alpaca unavailable" (S-4.8) |
| S-2.12 | N3 | Alpaca answers 200 for T with a body lacking `status` or `tradable` | the nightly check completes | T is not checked with the reason "Alpaca response unreadable" |
| S-2.13 | N7 | the tradeable-set cache row read by the Strategy Builder and Asset Swaps, as it stood before the run | the nightly check completes | the row's content and timestamp are unchanged; the check neither reads nor refreshes it |
| S-2.14 | N3 | the Alpaca key or secret is absent from the configuration | the run ends | no Alpaca request is sent; the run reads "unavailable: Alpaca credentials not configured" |
| S-2.15 | N5 | T named by two live symphonies | the nightly check completes | T is judged once in the run; both symphonies show their own line for T when T is dead |
| S-2.16 | N6 | Alpaca answers T with a status other than 200, 401, 403, 404, 429 or 5xx (another 4xx, or a 3xx) | the nightly check completes | T is not checked with the reason "Alpaca rejected the request"; it is never read as "not listed" or dead |

Negative coverage: N1 S-2.2, S-2.3, S-2.4, S-2.6, S-2.7 · N2 n/a: nothing is refused; an unreadable answer leaves the ticker not checked (S-2.12) · N3 S-2.12, S-2.14 · N4 S-2.10 · N5 S-2.15 · N6 S-2.9, S-2.11, S-2.16 · N7 S-2.13 · N8 n/a: each ticker is read once per run and no edit is held open
Questions: Q-1, Q-2

### AC-3: The dashboard names each dead ticker in each live symphony
Requirement items: R-1, R-2, R-6, R-7, R-11, R-12, R-13, R-14, R-16, R-18, R-22, R-27, R-33
Rule: When the latest completed check found a dead ticker in a live symphony, the dashboard page `/` shall show above the hero section a warning whose lead sentence reads "These live symphonies reference tickers that can no longer be traded; fix each tree in Composer." followed by "Checked YYYY-MM-DD HH:MM ET" and one line per symphony and dead ticker naming the symphony's display name, the ticker, the reason, the number of asset nodes that buy it, the number of conditions that read it, and whether the symphony holds it now.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-3.1 | B | the latest completed check found T dead in symphony X, bought by n asset nodes and read by m conditions, and Composer's roster holdings for X do not include T | the operator opens `/` | the warning shows the lead sentence, the check's completion time in the `YYYY-MM-DD HH:MM ET` form, and the line "X: T is {reason}. Used by n asset nodes and m conditions. Not held now.", with n and m taken from the tree and the reason from AC-2 |
| S-3.2 | N1 | the roster holdings for X include T | the operator opens `/` | the line ends "Held now." |
| S-3.3 | N3 | the roster entry for X has no holdings field, or holdings that cannot be read | the operator opens `/` | the line ends "Holding unknown."; it never reads "Not held now." |
| S-3.4 | N1 | counts of 1 asset node and 0 conditions, and of 0 asset nodes and 1 condition | the operator opens `/` | the lines read "Used by 1 asset node and 0 conditions." and "Used by 0 asset nodes and 1 condition." |
| S-3.5 | N1 | dead tickers B and A in symphony Y and C in symphony X | the operator opens `/` | three lines in display-name order, then ticker order: X C, Y A, Y B |
| S-3.6 | N1 | X's roster name is empty or blank | the operator opens `/` | the line names X by its Composer hash id |
| S-3.7 | N1 | X listed by two configured accounts, holding T in one of them | the operator opens `/` | one line per dead ticker for X, not one per account; it ends "Held now." |
| S-3.8 | B | the latest check completed within 36 hours, judged every symphony and every ticker, and found no dead ticker | the operator opens `/` | no warning and no notice of this feature appear on the page |
| S-3.9 | N7 | any stored result | the operator opens `/` | the render makes no Composer and no Alpaca request; it shows the stored result only |
| S-3.10 | N4 | no session, with the authentication checks enabled | `GET /` is sent | redirect to the login page; the response carries no check result |
| S-3.11 | N7 | a stored result with dead tickers, and a fleet alert | the operator opens `/` | the fleet banner and the market-state banner are byte-identical to the ref (`templates/index.html:1283-1316`); the header engine label reads as it would with no dead ticker |
| S-3.12 | N1 | five warning lines and a 375 px wide viewport | the operator opens `/` | every line wraps inside the page width; the page has no horizontal scroll |
| S-3.13 | N8 | a check run is in progress and an earlier run completed | the operator opens `/` | the earlier completed run's result is shown; nothing from the run in progress appears |

Negative coverage: N1 S-3.2, S-3.4, S-3.5, S-3.6, S-3.7, S-3.12 · N2 n/a: the page refuses nothing beyond the existing auth gate (S-3.10) · N3 S-3.3 · N4 S-3.10 · N5 covered by AC-7 (S-7.4) · N6 covered by AC-4 · N7 S-3.9, S-3.11 · N8 S-3.13
Questions: Q-3, Q-8

### AC-4: The dashboard says when the check could not judge, never all clear
Requirement items: R-18, R-25, R-26
Rule: If the latest run ended without completing, left any live symphony or ticker not checked, or no run has completed within the last 36 hours, then the dashboard page `/` shall show a muted notice naming what was not checked and why, and shall keep showing each earlier warning for a symphony or ticker the latest run did not judge, followed by "(from the check at YYYY-MM-DD HH:MM ET)".

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-4.1 | B | Composer's score read for symphony Y answers 5xx or times out, and every other symphony's tree is read | the run completes and the operator opens `/` | the notice reads "Not checked: Y (Composer unavailable)."; every other symphony is judged and its warnings reflect this run |
| S-4.2 | N6 | the score read for Y answers 404 | the run completes | the notice reads "Not checked: Y (definition not found)." |
| S-4.3 | N6 | the score read for Y answers 401 or 403 | the run completes | the notice reads "Not checked: Y (Composer refused the request)." |
| S-4.4 | N6 | the score read for Y answers 200 with a body that is not a tree | the run completes | the notice reads "Not checked: Y (definition unreadable)." |
| S-4.5 | N6 | the roster read for one configured account answers 5xx or times out, and the other accounts' rosters are read | the run completes | the notice reads "Not checked: symphonies of the {account label} account (Composer unavailable)."; the other accounts' symphonies are judged |
| S-4.6 | N3 | Y had a warning for T from the previous run, and the latest run did not judge Y | the operator opens `/` | the line for Y and T still shows, followed by "(from the check at" and the previous run's time; no Discord message is sent about it |
| S-4.7 | N6 | the latest run ended without completing because it read no roster or no Alpaca answer | the operator opens `/` | the notice reads "Ticker check unavailable: {reason}. Last completed check YYYY-MM-DD HH:MM ET." (or "No completed check yet." in place of the last sentence when none exists); earlier warnings show as in S-4.6 |
| S-4.8 | N3 | Alpaca left k tickers of X unanswered (S-2.11) | the operator opens `/` | the notice reads "k tickers in X could not be checked (Alpaca rate limit)." with k the count from that run |
| S-4.9 | N3 | no check run has ever completed | the operator opens `/` | the notice reads "No ticker check has run yet." |
| S-4.10 | N3 | the newest completed check finished more than 36 hours before the page is rendered | the operator opens `/` | the notice reads "No ticker check has completed since YYYY-MM-DD HH:MM ET." (Q-4); the stored warnings still show |
| S-4.11 | N3 | no Composer account is configured | the run ends and the operator opens `/` | no request is sent; the notice reads "Ticker check unavailable: no Composer account configured." |
| S-4.12 | N1 | the configured accounts' rosters are read and list no symphony | the run completes and the operator opens `/` | no warning and no notice of this feature appear |
| S-4.13 | N7 | a Composer error answer whose body carries text | the run completes | the notice shows only the fixed reason; no response body, header or exception text appears on the page |
| S-4.14 | N1 | runs completed on Friday, Saturday and Sunday nights (AC-10) | the operator opens `/` on Sunday or on a market holiday | no stale notice appears |
| S-4.15 | N6 | the score read for Y answers a status the read client classes as rejected (a 3xx, which is not followed, a 400, a 422 or another 4xx) | the run completes | the notice reads "Not checked: Y (Composer rejected the request)." |
| S-4.16 | N1 | a roster entry whose symphony id is not a plain id of letters, digits, "_" and "-" | the run completes | no score request is sent for it; the notice reads "Not checked: Y (symphony id not usable)." |
| S-4.17 | N4 | the roster read for one account answers 401 or 403 | the run completes | the notice reads "Not checked: symphonies of the {account label} account (Composer refused the request)." |
| S-4.18 | N6 | the roster read for one account answers 404 | the run completes | the notice reads "Not checked: symphonies of the {account label} account (account not found)." |
| S-4.19 | N6 | the roster read for one account answers a rejected status (a 3xx, a 400, a 422 or another 4xx) | the run completes | the notice reads "Not checked: symphonies of the {account label} account (Composer rejected the request)." |
| S-4.20 | N6 | the roster read for one account answers 200 with a body that is not a roster, or the account id is not a plain id | the run completes | the notice reads "Not checked: symphonies of the {account label} account (Composer response unreadable)."; no request is sent for a non-plain account id |
| S-4.21 | N6 | the roster read for one account answers 429 | the run continues | S-7.6 applies: no further Composer request in the run, so the remaining accounts and symphonies are not checked with the reason "Composer rate limit" |

Negative coverage: N1 S-4.12, S-4.14, S-4.16 · N2 n/a: nothing is refused; an unreadable source leaves its items not checked · N3 S-4.6, S-4.8, S-4.9, S-4.10, S-4.11 · N4 S-4.17 · N5 covered by AC-7 (S-7.4) · N6 S-4.2, S-4.3, S-4.4, S-4.5, S-4.7, S-4.15, S-4.18, S-4.19, S-4.20, S-4.21 · N7 S-4.13 · N8 covered by AC-3 (S-3.13)
Questions: Q-4, Q-8

### AC-5: One Discord message per newly found dead ticker
Requirement items: R-1, R-6, R-11, R-12, R-13, R-14, R-20, R-28, R-33
Rule: When a completed check finds a dead ticker in a live symphony and that symphony and ticker pair has not been announced since it last cleared, the check shall post through the configured Discord webhook a message naming each such pair with the facts of its AC-3 dashboard line plus the symphony's Composer hash id, shall not post that pair again until it has cleared, and shall post nothing about an item AC-4 reports not checked.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-5.1 | B | a new dead pair (X, T) | the run completes | one Discord message names X's display name, X's Composer hash id, T, the reason, the counts and the held state, in the AC-3 wording |
| S-5.2 | N5 | (X, T) was announced on an earlier night and is still dead | the next run completes | no Discord message names (X, T) |
| S-5.3 | N5 | two new pairs (X, T) and (Y, T) in one run | the run completes | both pairs are named in that run's message; each is announced once |
| S-5.4 | N5 | (X, T) announced earlier, and a new dead ticker U appears in X | the run completes | the message names (X, U) only |
| S-5.5 | N1 | (X, T) cleared on one night (AC-6) and is dead again on a later night | the later run completes | (X, T) is announced again |
| S-5.6 | N6 | the webhook answers non-2xx or times out | the run completes | (X, T) is not recorded as announced; the next completed run posts it; the log line names the status or exception type only |
| S-5.7 | N3 | no Discord webhook URL is configured | the run completes | no post is attempted; the dashboard shows the warning; (X, T) is not recorded as announced, so the first run after a webhook is configured posts it |
| S-5.8 | N3 | a run that judged X and found a new pair (X, T) but left Y not checked | the run completes | the message names (X, T) and says nothing about Y (Q-5) |
| S-5.9 | N5 | Discord accepted the message for (X, T) and the daemon stopped before the announcement was stored | the next run completes | (X, T) is posted once more; an announcement is never lost (Q-6) |
| S-5.10 | N1 | X renamed in Composer after (X, T) was announced | the next run completes | no message for (X, T); the pair is keyed by X's Composer hash id |
| S-5.11 | N1 | 40 new pairs in one run | the run completes | every pair is named, across as many messages as Discord's size limits require |
| S-5.12 | N7 | the exit alert (`reporting.py:779-875`) and the end-of-day post (`reporting.py:370`) | their senders run | their payloads are as at the ref |

Negative coverage: N1 S-5.5, S-5.10, S-5.11 · N2 n/a: nothing is refused; a failed post is retried on the next run (S-5.6) · N3 S-5.7, S-5.8 · N4 n/a: the webhook is the only credential and its absence is S-5.7 · N5 S-5.2, S-5.3, S-5.4, S-5.9 · N6 S-5.6 · N7 S-5.12 · N8 covered by AC-7 (S-7.7)
Questions: Q-5, Q-6

### AC-6: A warning clears when the ticker is fixed
Requirement items: R-29
Rule: When a completed check judges a symphony and finds that a ticker it warned about is now healthy or no longer named by the tree, the dashboard shall stop showing that warning and the pair shall become eligible for a new announcement.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-6.1 | B | a warning for (X, T), and Alpaca now reports T active and tradable | the next run completes and the operator opens `/` | no line for (X, T) |
| S-6.2 | B | a warning for (X, T), and the operator has removed every use of T from X's tree | the next run completes and the operator opens `/` | no line for (X, T) |
| S-6.3 | N6 | a warning for (X, T), and the next run could not read X's tree | the run completes | the line for (X, T) stays, with the earlier run's time (S-4.6) |
| S-6.4 | N6 | a warning for (X, T), and the next run could not read T's Alpaca answer | the run completes | the line for (X, T) stays, with the earlier run's time |
| S-6.5 | N1 | a warning for (X, T), and X no longer appears on any configured account's roster | the next run completes, every roster read | no line for X |
| S-6.6 | N1 | T dead in X and Y, and T removed from X's tree only | the next run completes | the line for (Y, T) stays and the line for (X, T) goes |
| S-6.7 | N7 | (X, T) clears | the run completes | no Discord message is sent for the clearing (NB-7) |

Negative coverage: N1 S-6.5, S-6.6 · N2 n/a: clearing refuses nothing · N3 covered by AC-4 (S-4.6) · N4 covered by AC-3 (S-3.10) · N5 covered by AC-5 (S-5.5) · N6 S-6.3, S-6.4 · N7 S-6.7 · N8 covered by AC-3 (S-3.13)
Questions: none

### AC-7: The check reads Composer only off-hours and alone, and leaves no partial result
Requirement items: R-21, R-30, R-31, R-32, R-40, R-44
Rule: If the market is open, a check run is already in progress, or an in-process off-hours Composer reader (the 03:30 incubation tick, the tenure review or the retirement run) is making Composer requests, then the check shall make no Composer request, and a run that ends early shall leave the previous completed result in place and post nothing from its unfinished part.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-7.1 | B | removed: moved to S-10.1 under AC-10, which owns the schedule | none | none |
| S-7.12 | B | no market session is open, no other check run is in progress and no in-process off-hours Composer reader is running | a run starts | the run makes its Composer reads and completes |
| S-7.2 | N2 | `market_calendar.get_market_state` reads "open" when a run would start | the run would start | no Composer request is made; the run is stored as "skipped: market open"; the previous result stays shown |
| S-7.3 | N2 | the market opens while a run is in progress | the run continues | no Composer request after the open; each symphony not yet read is not checked with the reason "market opened" |
| S-7.4 | N5 | a run is in progress | a second start of the check is triggered | no second run starts and the second start writes nothing |
| S-7.5 | N5 | the 03:30 incubation tick, the tenure review or the retirement run is making Composer requests | the check is due | the check makes no Composer request until that run has ended (Q-7) |
| S-7.6 | N6 | Composer answers 429 to any request of the check | the run continues | no further Composer request in that run; each symphony not yet read is not checked with the reason "Composer rate limit" |
| S-7.7 | N8 | the daemon stops while a run is in progress | the daemon restarts and the operator opens `/` | `/` shows the previous completed result and no line from the stopped run; no Discord message was sent for the stopped run's findings; the catch-up rule of AC-10 (S-10.4) decides the next run |
| S-7.8 | N7 | market hours | each minute passes | the engine is spawned at each `:00` as at the ref; the check makes no request |
| S-7.9 | N7 | the scheduler's slots at the ref (02:00, 03:00, 03:30, 03:45, 04:15, 04:30, `app.py:3090-3107`) | a day passes | each runs at its time as at the ref |
| S-7.10 | N6 | the check raises an unexpected error | the run ends | the scheduler keeps running; the run is stored as failed with the exception type name only; `/` shows the previous result and the notice "Ticker check failed on YYYY-MM-DD HH:MM ET." |
| S-7.11 | N1 | a run reading 11 trees | the run completes | the check made no more than 10 Composer requests in any 60 seconds (the per-run budget at `advisors/composer_portfolio_client.py:27-34`) |

Negative coverage: N1 S-7.11 · N2 S-7.2, S-7.3 · N3 covered by AC-4 (S-4.9, S-4.10) · N4 n/a: a scheduled job with no caller · N5 S-7.4, S-7.5 · N6 S-7.6, S-7.10 · N7 S-7.8, S-7.9 · N8 S-7.7
Questions: Q-7

### AC-8: Protected surfaces stay as they are
Requirement items: R-19, R-34, R-35, R-40, R-41, R-42, R-44
Rule: When the check is added, the engine files, the Composer write paths, the live-execution flag, the tenure review output, the `/health` and `/api/header-status` bodies, the settings write allowlist and the Strategy Builder's tradeable set shall stay as at the ref, and the no-trade scans shall cover every new path.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-8.1 | B | the feature's code at HEAD | the no-trade scans, extended to the check's module and its scheduler entry (precedents `tests/security/test_regime_label_job_no_trade_boundary.py` and `tests/security/test_retirement_action_no_trade_boundary.py`), run | they pass with every new path in scope |
| S-8.2 | N7 | a Composer save, deploy, go-to-cash or `perform_account_liquidation` call planted in a new path | the scans run | the scans fail |
| S-8.3 | N7 | the feature's diff | `tests/test_mdd_window_truth_zero_diff_guard.py` runs | the pins on `alpha_bot_execution.py` and `math_engine.py` (`tests/test_mdd_window_truth_zero_diff_guard.py:74-75`) still match |
| S-8.4 | N7 | a complete run | its requests are listed | every Composer request is a GET of an account's symphony-stats-meta or a symphony's score; every Alpaca request is a read-only GET of asset records; there is no backtest, order, account, position or Composer write request |
| S-8.5 | N7 | the same inputs | a tenure review run completes with and without the feature | the stored review records are equal |
| S-8.6 | N7 | a stored result with dead tickers | `GET /health` (on-box and remote) and `GET /api/header-status` are requested | `status` and `degraded_reasons` are as they would be with no dead ticker (Q-3) |
| S-8.7 | N7 | the feature is deployed | `_SETTINGS_WRITE_ALLOWLIST` is read | it is unchanged |
| S-8.8 | N7 | every new path | the scans run | no new path reads or writes the live-execution flag or a symphony's live mode |
| S-8.9 | N7 | any migration the feature adds | the migrations run on a copy of a database at migration 041 | the existing tables and columns are unchanged; the migration only adds |

Negative coverage: N1 n/a: the rule judges code paths and stored schema, not input values · N2 n/a: nothing is refused; a write path must not exist · N3 n/a: no value is computed by this rule · N4 covered by AC-3 (S-3.10) · N5 n/a: the rule is static · N6 n/a: the scans call no dependency · N7 S-8.2, S-8.3, S-8.4, S-8.5, S-8.6, S-8.7, S-8.8, S-8.9 · N8 n/a: no edit exists on these paths
Questions: Q-3

### AC-9: Credentials and names stay out of logs, markup and mentions
Requirement items: R-43
Rule: When the check runs, renders or posts, no Alpaca key, Composer key or Discord webhook URL shall appear in a log line, the page or a Discord message, and a symphony name or ticker shall appear only as text, never as markup, a Discord mention or a change to any request's target.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-9.1 | B | a run in which the Discord post fails and an Alpaca read fails | the run completes | its log lines carry status codes, fixed reasons and exception type names only; none of the Alpaca key, the Alpaca secret, the Composer key or the webhook URL appears |
| S-9.2 | N1 | a symphony display name containing an HTML image tag with an `onerror` attribute | the operator opens `/` | the name shows as literal text; no element is created from it |
| S-9.3 | N1 | a symphony display name containing `@everyone` or a Discord user mention | the message is posted | the message pings no one; the name reads literally |
| S-9.4 | N1 | a tree ticker containing a slash, a space or `..` | the nightly check completes | no Alpaca request target is formed from it; the notice names that ticker in its symphony as not checked with the reason "ticker form not checked" |
| S-9.5 | N7 | an Alpaca error answer whose body carries text | the run completes | neither the page, the Discord message nor the log carries that body |

Negative coverage: N1 S-9.2, S-9.3, S-9.4 · N2 n/a: nothing is refused beyond S-9.4's not-checked ticker · N3 n/a: the rule judges what is written, not a computed value · N4 covered by AC-3 (S-3.10) · N5 n/a: repetition does not change what is written · N6 S-9.1 · N7 S-9.5 · N8 n/a: no edit exists on these paths
Questions: none

### AC-10: The check runs every night and catches up a missed night
Requirement items: R-10, R-21, R-44
Rule: When the nightly start time arrives, or the daemon starts outside market hours and no run has completed since the most recent session close, the check shall start one run, so that whenever the daemon runs outside market hours between a session's close and the next open, a run completes in that interval.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-10.1 | B | Alpaca marks T inactive during session D, T is in a live tree, and the daemon runs from D's close to D+1's open | session D+1 opens | the warning for T is on `/` and its Discord message has been posted |
| S-10.2 | N1 | a Saturday, a Sunday or an NYSE holiday | the nightly start time arrives | a run starts and completes as on a trading day |
| S-10.3 | N6 | the daemon was down at the nightly start time and starts at 06:00 ET on a trading day, with no run completed since the previous close | the daemon starts | one catch-up run starts and completes before the 09:30 open |
| S-10.4 | N8 | the daemon stopped mid-run (S-7.7) and restarts outside market hours | the daemon starts | one catch-up run starts, because no run completed since the most recent close |
| S-10.5 | N5 | a run has completed since the most recent session close | the daemon restarts outside market hours | no catch-up run starts |
| S-10.6 | N2 | the daemon starts during market hours with no run completed since the previous close | the daemon starts | no run starts and no Composer request is made; the next run is the nightly one; the stale notice applies once 36 hours pass (S-4.10) |
| S-10.7 | N5 | a catch-up run is in progress when the nightly start time arrives | the nightly start time arrives | no second run starts (S-7.4) |
| S-10.8 | N1 | the host clock in UTC, with Eastern time in its standard and its daylight offset | the nightly start time arrives in each offset | the start falls after that day's session close (13:00 ET on an early-close day) and the run completes before the next open |

Negative coverage: N1 S-10.2, S-10.8 · N2 S-10.6 · N3 covered by AC-4 (S-4.9, S-4.10) · N4 n/a: a scheduled job with no caller · N5 S-10.5, S-10.7 · N6 S-10.3 · N7 covered by AC-7 (S-7.9) · N8 S-10.4
Questions: Q-4

## Rule-by-Entry-Point Matrix

| Rule | Scheduled nightly start | Catch-up start at daemon start | Dashboard page `/` | Discord message | Daemon log |
|---|---|---|---|---|---|
| AC-3 and AC-5 facts named for a dead pair | n/a: the run stores, it does not show | n/a: the run stores, it does not show | S-3.1 | S-5.1 | n/a: the log names no pair beyond counts |
| AC-4 a run that did not complete, or a partial one, never reads all clear | S-2.9 | S-2.9 | S-4.7 | S-5.8 | n/a: the log is not where the operator reads verdicts |
| AC-6 a warning clears only on a judged fix | S-6.3 | S-6.3 | S-6.1 | S-6.7 | n/a: no log line is required for a clearing |
| AC-7 no Composer request while the market is open | S-7.2 | S-7.3 | n/a: the page reads only the stored result and makes no request | n/a: posting makes no Composer request | n/a: the log makes no request |
| AC-7 no second concurrent run | S-7.4 | S-7.4 | n/a: the page starts no run | n/a: posting starts no run | n/a: the log starts no run |
| AC-9 names and credentials only as plain text | S-9.4 | S-9.4 | S-9.2 | S-9.3 | S-9.1 |
| AC-10 a missed night is caught up outside market hours | S-10.2 | S-10.3 | n/a: the page starts no run | n/a: posting starts no run | n/a: the log starts no run |

## Not Building
Non-goals:
- NB-1: Any automatic edit of a live Composer symphony; the operator fixes the tree in Composer (excludes R-15; proposed).
- NB-2: Suggesting a replacement ticker (excludes R-36; proposed).
- NB-3: Intraday checks (excludes R-37; proposed).
- NB-4: Checking non-live candidate trees: Strategy Builder survivors and frontrunner proposals (excludes R-38; proposed).
- NB-5: Price-staleness detection for a ticker Alpaca lists as active and tradable, including "no daily bar for several sessions" (excludes R-39, narrows R-4 and R-9; proposed, Q-1).
- NB-6: Running a Composer backtest to confirm that a ticker is dead (excludes R-5; proposed: Alpaca's asset status is the judge, and a backtest per tree is the heavy request the tenure review already spends).
- NB-7: A Discord message when a warning clears (proposed).
- NB-8: A Discord message about an unavailable or partial check (proposed, Q-5).
- NB-9: A header indicator on the other pages, a header "Degraded" state or a `/health` degraded reason for dead tickers (narrows R-19; proposed, Q-3).
- NB-10: A JSON route for the check's result, and updating the warning on the dashboard poll; a reload shows a new result (proposed).
- NB-11: An on-demand "run the check now" control (proposed).
- NB-12: Dismissing a warning without fixing the tree (proposed).

Behavior that stays exactly as it is:
- NB-13: The tenure review's evidence, verdicts and stored records, including the backtest item that names no ticker (excludes R-17; proposed).
- NB-14: The ticker sets that `extract_tickers` returns to its existing callers (S-1.11).
- NB-15: The weekly-cached tradeable set the Strategy Builder and Asset Swaps read, and its refresh (S-2.13).
- NB-16: The fleet and market-state banners, the header engine label and the `/health` and `/api/header-status` bodies (S-3.11, S-8.6).
- NB-17: The engine's minute cycle, `alpha_bot_execution.py` and `math_engine.py` (S-7.8, S-8.3).

Deferred:
- NB-18: Covering legacy `lhs-val` and `rhs-val` operands in `extract_tickers` for its other callers (asset swaps, community loader, plan compiler, weekly suggestions) (proposed: one backlog line, Follow-Ups).
- NB-19: Checking trees whose asset class is not `EQUITIES` (S-1.10; proposed).
- NB-20: Reporting a failed Composer rebalance from the roster's own rebalance fields (proposed).

Protected surfaces consumed, not changed:
- NB-21: The read-only Composer client (`advisors/composer_portfolio_client.py:151-223`) as the tenure review uses it, the configured account keys (`advisors/tenure_review_runner.py:26-31`), `market_calendar.get_market_state` (`market_calendar.py:132`), `analytics.format_et_stamp` (`analytics.py:2524`) and the `DISCORD_WEBHOOK_URL` configuration key.
- NB-22: The no-trade boundary: the proposal-approve route is the only path to Composer symphony creation, the panic-sell route the only dashboard trade button, Managed Sleeves its own armed order path, and live trading needs both `LIVE_EXECUTION` and the symphony's `live_mode`; the check adds no path near any of them (S-8.1, S-8.2, S-8.8).

## Defect Classes

| DC-n | Class (what goes wrong) | Trigger (B or N1-N8) | Sibling search (tool, query, scope) | Scenarios | Raised in review as |
|---|---|---|---|---|---|
| DC-1 | A ticker family member is skipped: an operand form is collected in one walker and missed in another | N1 | grep: `lhs-val`, `rhs-val`, `rhs-fixed-value?`, `"tickers"`, `"ticker"` and `"%"` in `advisors/` and the check's module; read: each operand form against the check's walker | S-1.2, S-1.3, S-1.4, S-1.5, S-1.6, S-1.7 | - |
| DC-2 | Absent or not-computed value shown as a default: an unread holding shows "Not held", an unread ticker shows healthy | N3 | grep: every read of the roster's holdings and every verdict default in the check's module and the dashboard render | S-3.3, S-2.12, S-6.4 | - |
| DC-3 | Dependency failure answered with stale data or success: a failed Alpaca or Composer read clears a warning or reads all clear | N6 | grep: every call to Alpaca, Composer and the webhook from the check's module and its error handling | S-2.9, S-4.1, S-4.7, S-6.3, S-6.4 | - |
| DC-4 | Unavailable state not listed per surface: the page and Discord treat an unavailable run differently | N3 | read: each surface in the Rule-by-Entry-Point Matrix row for AC-4 | S-4.7, S-5.8, S-7.10 | - |
| DC-5 | Check and write outside one lock: two starts both run, or a once-only announcement is written twice | N5 | grep: every start of the check and every write of the announced pairs; read: the lock or key each uses | S-7.4, S-5.2, S-5.9 | - |
| DC-6 | Rule enforced at one entry point, missing at another: the market gate, the single-flight rule or the overlap rule guards the nightly start and not the catch-up start | N2 | grep: every scheduler registration, every daemon-start hook and every call into the check's run function | S-7.2, S-7.3, S-7.4, S-7.5, S-10.6, S-10.7 | - |
| DC-7 | Data that must stay unchanged is written on a no-op path: a skipped or stopped run replaces the stored result or posts | N7 | read: every write after the market gate and after a stop; grep: writes to the stored result | S-7.2, S-7.7 | - |
| DC-8 | A shared helper changed for a new caller alters its old callers | N7 | grep: callers of `extract_tickers`, the Composer read client and `universe_provider` | S-1.11, S-2.13, S-8.5 | - |
| DC-9 | Narrow assertion licenses unlisted siblings: one Alpaca status case tested, the others not | B, N1 | read: tests of the verdict; list status, tradable and not-found cases whole | S-2.1, S-2.2, S-2.3, S-2.4, S-2.5 | - |
| DC-11 | A scheduled slot missed while the daemon was down is never re-run, so a night is lost silently | N6 | read: how the scheduler treats a slot whose time passed while the daemon was down; grep: every daemon-start path | S-10.3, S-10.4, S-4.10 | - |
| DC-10 | Planned example values not encoded in a test | B | read: the Worked Example against the tests that use its inputs | S-3.1, S-5.1 | - |

## Worked Example
A stored result from a completed run at 2026-11-17 04:12 UTC (shown as 2026-11-16 23:12 ET). Symphony "Example Copper Rotation", Composer hash id `AbCdEf0123456789XyZa`, buys ticker QXGD in 25 asset nodes and reads it in one if-child that compares QXGD's 118-day and 114-day moving-average return; Alpaca answers QXGD with status inactive and tradable false; the roster holdings for the symphony do not include QXGD. Every other ticker of every live tree is active and tradable.

The operator opens `/` and sees above the hero section (S-1.8, S-2.1, S-3.1):

"These live symphonies reference tickers that can no longer be traded; fix each tree in Composer. Checked 2026-11-16 23:12 ET."
"Example Copper Rotation: QXGD is inactive and not tradable at Alpaca. Used by 25 asset nodes and 1 condition. Not held now."

The run posted one Discord message naming Example Copper Rotation, `AbCdEf0123456789XyZa`, QXGD, the same reason, counts and held state (S-5.1). The following night QXGD is still dead: the line stays with the new checked time and no message is posted (S-5.2).

Failure case: on the following night every Alpaca request times out. The page shows the muted notice "Ticker check unavailable: Alpaca unreachable. Last completed check 2026-11-16 23:12 ET." and keeps the QXGD line followed by "(from the check at 2026-11-16 23:12 ET)"; no Discord message is posted and no warning clears (S-2.9, S-4.6, S-4.7, S-6.4).

## Fail-Closed Behaviour
Every input the check cannot judge leaves its item not checked, never healthy:
- Alpaca unreachable, refused, rate limited, unavailable, rejected or unreadable: the ticker or the run is not checked with a fixed reason (S-2.9, S-2.10, S-2.11, S-2.12, S-2.14, S-2.16); only a not-found answer reads "not listed" (S-2.4); earlier warnings stay (S-4.6, S-6.4).
- Composer roster or score read refused, not found, rate limited, unavailable, rejected or unreadable, or an id that is not a plain id: the account or symphony is not checked with one fixed reason per class (score: S-4.1, S-4.2, S-4.3, S-4.4, S-4.15, S-4.16, S-7.6; roster: S-4.5, S-4.17, S-4.18, S-4.19, S-4.20, S-4.21); earlier warnings stay (S-6.3).
- A tree of another asset class, or a ticker that is not a plain symbol: not checked (S-1.10, S-9.4).
- The market open: no Composer request; the run is stored as skipped (S-7.2, S-7.3).
- A second start while a run is in progress: refused, nothing written (S-7.4, S-10.7).
- A daemon start during market hours: no catch-up run (S-10.6).
The existing read client classes failures as refused, not found, rate limited, unavailable, rejected and unreadable (`advisors/composer_portfolio_client.py:86-96`, `:114-148`); each maps to one fixed reason above.

## Authorities Touched

| Value | Authority used | What it replaced | What proves no second source |
|---|---|---|---|
| Live symphonies | The symphony-stats-meta roster of the configured accounts (`advisors/composer_portfolio_client.py:151-172`, `advisors/tenure_review_runner.py:26-31`) | nothing | S-4.5, S-6.5 |
| A ticker's tradability | Alpaca's asset record read during the run | nothing; the weekly tradeable set is not used | S-2.8, S-2.13 |
| Held now | The roster entry's `holdings` for the symphony, the same field the engine copies into its state (`alpha_bot_execution.py:920-927`) | nothing | S-3.2, S-3.3 |
| Market open | `market_calendar.get_market_state` (`market_calendar.py:132`) | nothing | S-7.2 |
| Shown time | `analytics.format_et_stamp` (`analytics.py:2524`) | nothing | S-3.1 |

## Contract Changes and Consumers
none: no route, JSON body or wire field changes; the dashboard page gains a server-rendered element and Discord gains a new message, neither consumed by another program.

## Sibling Repos and Landing Order
none: one repository.

## Migrations Required
The Gate-2 approach decides whether the stored result and the announced pairs need a table (G-12). If one is added it is additive only, numbered after `migrations/041_dashboard_session_generation.sql`, and changes no existing table or column (S-8.9); forward-only is acceptable because nothing reads the new table before this feature.

## Configuration and Enablement
No new configuration key. The check uses the existing Composer account keys, `ALPACA_KEY` and `ALPACA_SECRET`, and the optional `DISCORD_WEBHOOK_URL`. It ships enabled; without a webhook it shows warnings on the dashboard only (S-5.7).

## Breaking Changes
none: no consumer-facing route, body, configuration or stored data changes meaning.

## Security Considerations
- Input validation and injection: tree tickers go into an Alpaca request path only as plain symbols (S-9.4); symphony names render as text (S-9.2).
- Authorization: the warning renders behind the existing dashboard auth gate (S-3.10); the check adds no route and no POST, so CSRF does not apply.
- Data exposure: no credential, response body or exception message reaches a log, the page or Discord (S-4.13, S-9.1, S-9.5).
- Client-side rendering: server-rendered text through the template's autoescape; no client script builds markup from the result (S-9.2).
- Abuse and rate limits: Composer requests stay off-hours, paced and stopped on the first 429 (S-7.2, S-7.6, S-7.11, S-10.6); Alpaca failures leave items not checked (S-2.11, S-2.16); Discord mentions are disabled (S-9.3).
These match the Sensitive header: security for the new use of the Alpaca keys and the webhook URL (AC-9).

## Prerequisites
Composer credentials and at least one configured account (otherwise S-4.11); Alpaca paper keys (otherwise S-2.14); Alpaca's paper trading host reachable (otherwise S-2.9); a Discord webhook URL for messages (otherwise S-5.7).

## Design-System Mapping
No design system is declared; the dashboard's own styling conventions in `templates/index.html` apply.

| Component | Element | Required primitive or token | Notes |
|---|---|---|---|
| Dead-ticker warning | container above the hero section | the `.fleet-banner` pattern: `--studio-warn-tint` background, `--studio-warn-border` border, `--studio-ink` text (`templates/index.html:794-806`) | a new element beside the fleet banner; the fleet banner itself is unchanged |
| Dead-ticker warning | lead sentence and checked time | `--studio-ink`, 13 px, weight 500 as the fleet banner | the time in the `YYYY-MM-DD HH:MM ET` form |
| Dead-ticker warning | one line per symphony and ticker | `--studio-ink`, `--studio-sans` (the dashboard declares no monospace token) | wraps at 375 px (S-3.12) |
| Earlier-run suffix | "(from the check at ...)" | `--studio-ink-dim` | S-4.6 |
| Check notice | muted container | the `.market-banner` pattern: `--studio-chip-bg` background, `--studio-rule` border, `--studio-ink-dim` text (`templates/index.html:822-833`) | left-aligned text; no new primitive |
| Glossary | a line for "dead ticker" in the dashboard's closed Glossary | the existing `glossary(host)` macro (`templates/_glossary.html`) | only when the page names a term the Glossary lacks |

## Definition of Done (Verification)

| AC | Scenarios | Test level | Real-entrypoint test |
|---|---|---|---|
| AC-1 | S-1.1, S-1.2, S-1.3, S-1.4, S-1.5, S-1.6, S-1.7, S-1.8, S-1.9, S-1.10, S-1.11 | unit over captured trees, plus the scheduled entry | the check run started through its scheduler entry with Composer and Alpaca at the HTTP seam |
| AC-2 | S-2.1, S-2.2, S-2.3, S-2.4, S-2.5, S-2.6, S-2.7, S-2.8, S-2.9, S-2.10, S-2.11, S-2.12, S-2.13, S-2.14, S-2.15, S-2.16 | unit over captured Alpaca answers, plus the scheduled entry | as AC-1 |
| AC-3 | S-3.1, S-3.2, S-3.3, S-3.4, S-3.5, S-3.6, S-3.7, S-3.8, S-3.9, S-3.10, S-3.11, S-3.12, S-3.13 | route test of `GET /` | `GET /` through the Flask test client with auth on for S-3.10 |
| AC-4 | S-4.1, S-4.2, S-4.3, S-4.4, S-4.5, S-4.6, S-4.7, S-4.8, S-4.9, S-4.10, S-4.11, S-4.12, S-4.13, S-4.14, S-4.15, S-4.16, S-4.17, S-4.18, S-4.19, S-4.20, S-4.21 | route test of `GET /` over stored results from real runs | as AC-3 |
| AC-5 | S-5.1, S-5.2, S-5.3, S-5.4, S-5.5, S-5.6, S-5.7, S-5.8, S-5.9, S-5.10, S-5.11, S-5.12 | unit with the webhook at the HTTP seam, plus the scheduled entry | as AC-1, asserting the posted payloads |
| AC-6 | S-6.1, S-6.2, S-6.3, S-6.4, S-6.5, S-6.6, S-6.7 | two consecutive runs, then `GET /` | as AC-1 then AC-3 |
| AC-7 | S-7.2, S-7.3, S-7.4, S-7.5, S-7.6, S-7.7, S-7.8, S-7.9, S-7.10, S-7.11, S-7.12 | scheduler and run tests with a fixed Eastern clock | the scheduler registration and the run entry |
| AC-8 | S-8.1, S-8.2, S-8.3, S-8.4, S-8.5, S-8.6, S-8.7, S-8.8, S-8.9 | source scans, the engine pin test, the migration test | the extended no-trade scans |
| AC-9 | S-9.1, S-9.2, S-9.3, S-9.4, S-9.5 | log capture, route render, payload assertion | as AC-1 and AC-3 |
| AC-10 | S-10.1, S-10.2, S-10.3, S-10.4, S-10.5, S-10.6, S-10.7, S-10.8 | scheduler and daemon-start tests with a fixed Eastern clock | the scheduler registration and the daemon-start path |

Gates: `ruff format --check`, `ruff check`, a scoped pytest run of the touched test folders (never `-n auto` and never more than 4 workers), CI green on the exact SHA, and the PM's live functional check after the PR review: on the droplet, after one nightly run, `/` shows a line for each live symphony whose tree names a ticker Alpaca reports inactive or not tradable, each line checked by hand against Alpaca's asset record, and one Discord message for each.

Fixture provenance: Composer score bodies captured from the producer (the complete capture `tests/fixtures/composer/tenure-review/case4_definition_nOyb55RMGVCKPiYXv7TI.json` covers legacy operands, comma-joined labels and binary-compound lists; one fresh capture of a live tree naming an inactive ticker in asset nodes and a condition); a roster capture that keeps the `holdings` field: `tests/fixtures/composer/symphony_stats_meta.json` (11 symphonies, `holdings` as a list of ticker, allocation, price, amount and value) may be adopted once its provenance is recorded, since commit 6432c6ff added it with no provenance note; otherwise one fresh roster capture (the documented `case1_statistics_roth.json` drops `holdings` by its own trimming record); Alpaca asset answers captured from the paper host, one record each for inactive and not tradable, active and tradable, and not found, plus active and not tradable when one exists; the Discord request shape derived from Discord's published webhook schema, never from a real webhook URL. One record per case the code distinguishes; no fixture is written to match the parser. The trimmed trees in `tests/fixtures/advisors/frontrunner/` replace core tickers with placeholders, so they test operand coverage only, never a verdict.

## Success Measures
- The first nightly run after deploy shows the dead-ticker lines for every live symphony that names an inactive or not-tradable ticker, read on the droplet's `/`.
- No line over the first 5 nightly runs names a ticker that Alpaca reports active and tradable, checked against Alpaca's asset record.
- One Discord message per pair over those runs, read in the channel.

## Known-Unconfirmed and Standing Risk
- Distinct judged tickers: at least 303 in the repository's captures (the 11 trimmed real trees plus the complete case4 capture, placeholders excluded, legacy operands included), and the roster capture `tests/fixtures/composer/symphony_stats_meta.json` lists 338 distinct symbols in its per-symphony `tickers` field. The live total is higher than the trimmed count; how it sits under Alpaca's request limit (200 a minute in Alpaca's public documentation) depends on the read shape the approach picks. Read-only query for the live total: pull each live symphony's score body and count the distinct judged tickers.
- The roster's per-symphony `tickers` field lists symbols in Composer's `EQUITIES::SYMBOL//USD` form; whether it lists every condition operand of the tree is unconfirmed, so it is not the authority for AC-1 (the score tree is).
- Whether Composer writes any ticker in a form Alpaca writes differently (share classes) is unconfirmed; the complete capture and the trimmed trees hold only plain uppercase symbols of 1 to 6 letters. Read-only query: list the judged tickers that contain a character other than a letter. S-9.4 reports such a ticker as not checked rather than dead.
- A ticker Composer supports and Alpaca does not list would read dead under Q-2's default. Read-only query: compare the judged tickers against Alpaca's full asset list once.
- The roster entry's `holdings` shape is taken from the engine's reader (`alpha_bot_execution.py:920-927`) and from `tests/fixtures/composer/symphony_stats_meta.json`, whose provenance is unrecorded (Definition of Done).
- The PM's live read of 2026-11-16 counted 25 SHNY asset nodes and one SHNY condition in each of the two trees; the July trimmed captures of the same ids show 25 and 1, and 50 and 2. Trees change, so tests derive counts from their fixtures and never hardcode them.
- Composer readers the check cannot wait for: an operator-started tenure review or retirement Run now during the check's run, the weekly suggestions job (a separate process on a systemd timer, Mondays 04:00 ET, `docs/DEPLOYMENT.md:363`) and the account refresh every minute at all hours (`app.py:3085-3089`). The client comment budgets 25 a minute for its portfolio endpoints (`advisors/composer_portfolio_client.py:27-34`); whether `/score`, `/backtest` and the portfolio reads share one limit is not documented. The first 429 stops the check's requests (S-7.6). Q-7.
- A daily slot whose time passes while the daemon is down is not re-run by the scheduler (`app.py:3090-3107` registers daily slots only); AC-10's catch-up start covers this check only.
- The tenure review runner reads a `logic_holdings` state key (`advisors/tenure_review_runner.py:130-133`) that no module in the repository writes; the engine writes `current_holdings` (`alpha_bot_execution.py:920-927`). Outside this plan's scope; reported to the PM as a sibling finding (Follow-Ups).
- Alpaca's paper host is assumed to serve the same asset status as the live host for this purpose (`DECISIONS.md:1807` records that the keys are paper keys).
- The scheduler runs on host time, `Etc/UTC` on the droplet; any start time the approach picks must fall after the close in both Eastern standard and daylight time and finish before the next open.

## Open Questions

| Q-n | Question | Blocks (AC ids or "plan") | Proposed default | Answer |
|---|---|---|---|---|
| Q-1 | What counts as "no price data" for a ticker Alpaca lists as active and tradable? | AC-2 | Nothing: that is price staleness, which intake answer 6 excludes; "no price data" is covered by "not listed at Alpaca" | |
| Q-2 | Is a ticker Alpaca does not list at all reported as dead? | AC-2, AC-3 | Yes, with its own reason "not listed at Alpaca", so the operator can tell it from an inactive ticker | |
| Q-3 | Should a dead ticker also turn the header engine label "Degraded" or add a `/health` degraded reason? | AC-3, AC-8 | No: both report the engine's health, a degraded reason outranks Live and Closed in the header (`static/chrome.js:373-375`), and an unfixed tree would hide the engine's real state for days; the dashboard warning and Discord carry it | |
| Q-4 | After how long without a completed run does the dashboard say the check is stale? | AC-4, AC-10 | 36 hours; the check runs every calendar night (S-10.2), so weekends and holidays never read stale and one missed night does not either | |
| Q-5 | Should an unavailable or partial check post to Discord? | AC-5 | No; the dashboard notice states it, and the stale notice appears after 36 hours | |
| Q-6 | If the daemon stops after Discord accepted a message and before it was recorded, is one repeat acceptable? | AC-5 | Yes: a repeated message is preferred over a lost one | |
| Q-7 | Which Composer readers does the check wait for? | AC-7 | The in-process off-hours readers it can see (the 03:30 incubation tick, the tenure review and the retirement run); an operator-started Run now, the out-of-process weekly suggestions job and the minute account refresh are neither blocked nor waited for, and the check stops on the first 429 and reports the rest not checked | |
| Q-8 | Do T-1 and T-2 describe how you use the dashboard for this? No confirmed dashboard workflow file exists | AC-3, AC-4 | Yes, as written | |

## Decisions

| ID | Decision | Rationale |
|---|---|---|
| D-1 | The warning surfaces are the dashboard page `/` and a Discord message | The header label and `/health` are engine health (Q-3); the dashboard already carries banners (`templates/index.html:1283-1316`) |
| D-2 | "Held now" comes from the roster read's `holdings`, not the engine's stored holdings | The engine freezes its copy while a symphony is triggered (`alpha_bot_execution.py:920`); the roster is read in the same run |
| D-3 | Node use is reported as two counts, asset nodes and conditions | A condition-only ticker has no asset node; one count would hide it |
| D-4 | An announcement is keyed by the Composer hash id and the ticker | A rename must not re-announce (S-5.10); hash and display name do not join |
| D-5 | Sensitive is marked security for AC-9 | The project's marking covers credentials, and the check is a new sender of two |
| D-6 | A run "ends" for any stop and "completes" only after reading a roster and at least one Alpaca answer; checked times and staleness count completed runs | One word for two states let a night where everything failed reset the stale clock |
| D-7 | The check runs every calendar night and catches up at a daemon start outside market hours | The scheduler never re-runs a missed slot, and a per-session cadence would read stale every weekend |

## Docs to Update
- `docs/generated/`: one file per touched module, including the check's module and `app.py`'s scheduler and dashboard sections.
- `docs/generated/INDEX.md`.
- `README.md`: the nightly dead-ticker check and its Discord message.
- `DECISIONS.md`: one appended entry, its id minted at ship by the doc writer.
- `.claude/CLAUDE.md`: a drafted Key Files row for the check's module, for PM approval.

## Follow-Ups
- NB-18: one backlog line in `feature-plans/BACKLOG.md` for legacy operand coverage in `extract_tickers` for its other callers.
- NB-19: one backlog line for non-equity asset classes.
- NB-20: one backlog line for failed-rebalance reporting.
- The `logic_holdings` key with no writer (Known-Unconfirmed): reported to the PM in the completion summary, for a backlog line.
