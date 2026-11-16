# Feature: Backtest slippage default calibrated to live Composer results
Status: ready
Story: none: operator request in chat on 2026-11-16, quoted whole in backtest-slippage-calibration.source.md
Type: fix
Suite: none
Repos: AlphaBotPM
Base: main
Ref: AlphaBotPM@c3e12882141ae601f900fdc13e0c342462a739bb
Sensitive: money (AC-1, AC-3)
Depends on: none

## Trigger
The operator objected that problems found in the app were being reported and not fixed (backtest-slippage-calibration.source.md:4). The problem in question is the PM's finding that every advisor backtest assumes 0.5% slippage per trade, which on daily-rebalancing trees understates live results by about 0.2% to 0.5% a day (backtest-slippage-calibration.source.md:7). The source and the PM's intake answers are saved verbatim in backtest-slippage-calibration.source.md beside this plan.

## Summary
A Composer backtest that the app runs without an explicit slippage argument assumes 0.1% slippage per trade, the setting a 2026-11-16 measurement found closest to live Composer results, so every advisor surface that compares backtests (Strategy Builder with its SPY baseline, Asset Swaps, Logic Changes, Frontrunner Builder, the incubation tick and the tenure review) compares on one basis that tracks realized results: candidates, backtested incumbents and SPY baselines all carry the same default (Strategy Builder's incumbent bar is the fixed value 0.0, advisors/strategy_builder_engine.py:899, so there only its candidates and SPY baseline move).
- The backtest client sends `slippage_percent` 0.001 when the caller gives none, with every other request field unchanged (AC-1).
- A caller that passes `slippage_percent` explicitly, 0.0 included, still sends exactly that value (AC-2).
- Every advisor surface's backtest requests, SPY baselines included, carry the same default (AC-3).
- The autotuner's simulated exit cost stays 0.5 percentage points and is no longer stated or checked as derived from the backtest default (AC-4).
- The calibration evidence sits beside the default where a reader of the constant finds it (AC-5).
- The content pins on the two edited files move only over the listed edits (AC-6).

## Requirements as Given
[G-1] The fact that you're reporting "problems found in the app" and not fixing them is also suspect.. backtest-slippage-calibration.source.md:4
[G-2] The advisor's backtests assume 0.5% slippage per trade. backtest-slippage-calibration.source.md:7
[G-3] On these daily-rebalancing trees that understates live results by 0.2–0.5% a day. backtest-slippage-calibration.source.md:7
[G-4] At 0.1% the backtests match live within about 0.02% a day. backtest-slippage-calibration.source.md:7
[G-5] Every advisor backtest gate therefore leans against high-turnover strategies, and against beating SPY. backtest-slippage-calibration.source.md:7
[G-6] (Answer 1) What this makes true: a Composer backtest the app runs without an explicit slippage argument assumes the per-trade slippage that live Composer fills show, so every advisor gate that compares backtests (Strategy Builder candidates and its SPY baseline, Asset Swaps, Logic Changes, Frontrunner Builder, the incubation tick, backtest_gate_engine) compares on a basis that tracks realized results. backtest-slippage-calibration.source.md:10
[G-7] (Answer 2) Evidence (PM measurement 2026-11-16, read-only, inline these numbers in the plan; the raw files are not tracked): for 8 live symphonies, the current Composer tree was backtested (Composer v2, broker alpaca) at slippage_percent 0, 0.001 and 0.005 and compared day by day with Composer's realized deposit-adjusted daily returns over the 87 trading days since each tree's last edit (2026-07-15..2026-11-13). backtest-slippage-calibration.source.md:11
[G-8] (Answer 2) Daily correlation live vs backtest 0.976–0.996 for all 8. backtest-slippage-calibration.source.md:11
[G-9] (Answer 2) Mean daily gap (live minus backtest), bps/day, at 0 / 0.001 / 0.005: HuntedCascades 1.6/7.8/32.2; FeaverdAlloc 0.1/9.3/45.9; ErasedHistory -11.1/-2.7/30.5; GoldenAgeCopy -1.1/4.2/25.2; Reasonabilists(46 days) -0.9/7.1/38.8; LQD+EYEG -18.1/-11.4/15.3; CorpChaos -3.4/3.2/29.3; Paragons -18.7/-10.5/22.2. backtest-slippage-calibration.source.md:11
[G-10] (Answer 2) Cross-symphony mean: about -6.5 / +2.1 / +30 bps/day. backtest-slippage-calibration.source.md:11
[G-11] (Answer 2) Compounded over the 87 days, e.g. FeaverdAlloc live +57.2% vs backtest +54.8% / +43.1% / +4.4%. backtest-slippage-calibration.source.md:11
[G-12] (Answer 2) Conclusion used: 0.001 is the setting closest to live while staying slightly conservative. backtest-slippage-calibration.source.md:11
[G-13] (Answer 3) Current code (verify at the ref): `advisors/composer_backtest_client.py` `_DEFAULT_SLIPPAGE_PERCENT: float = 0.005` (comment says it matches Composer UI defaults) used as `run_backtest`'s default; `autotuner.py` `SIM_EXIT_FRICTION_PCT: float = 0.5` whose comment derives it from that constant, and `tests/autotuner/test_exit_friction_constant.py` pins that derivation. backtest-slippage-calibration.source.md:12
[G-14] (Answer 3) Find every caller of run_backtest/submit_backtest and whether it passes slippage explicitly. backtest-slippage-calibration.source.md:12
[G-15] (Answer 4) [PM-ASSUMED] New default value: 0.001 (0.1% per trade), with the calibration evidence above recorded where a reader of the constant finds it. backtest-slippage-calibration.source.md:13
[G-16] (Answer 5) [PM-ASSUMED] SIM_EXIT_FRICTION_PCT keeps its value 0.5 percentage points: it models the cost of a Planet Stopper full-position exit in the autotuner replay, a different cost from Composer's daily-rebalance slippage, and changing it would change the optimizer objective without evidence. backtest-slippage-calibration.source.md:14
[G-17] (Answer 5) It must no longer be derived from the backtest default (so the backtest default can change without moving it), and its pin test changes accordingly. backtest-slippage-calibration.source.md:14
[G-18] (Answer 6) Must not change: any caller that passes slippage explicitly; the engine (alpha_bot_execution.py, math_engine.py); SIM_EXIT_FRICTION_PCT's value; the dashboard's printed friction copy (it reads SIM_EXIT_FRICTION_PCT); no live trade path exists here. backtest-slippage-calibration.source.md:15
[G-19] (Answer 7) Not building: per-symphony or per-turnover calibration; re-running or rewriting past advisor observations already stored; any UI change; changing Composer request fields other than the slippage default. backtest-slippage-calibration.source.md:16
[G-20] (Answer 8) Negative cases to cover: a caller passing slippage_percent explicitly (including 0.0) still sends exactly that value; the request body field name and units sent to Composer are unchanged except the default value; SPY baseline backtests use the same default as candidates. backtest-slippage-calibration.source.md:17
[G-21] (Answer 9) Data/contracts: none (no schema, no route). backtest-slippage-calibration.source.md:18
[G-22] (Answer 9) Sensitive: no (advisory backtests only; no order path) — confirm or correct with evidence. backtest-slippage-calibration.source.md:18
[G-23] (Answer 10) Earlier decisions on the same surface to find and cite: DE-EXIT-FRICTION-REALIZED-001 (SIM_EXIT_FRICTION_PCT), DE-MATH-R2-001 (_extract_returns), DE-SB-CULL-001 (SPY baseline). backtest-slippage-calibration.source.md:19

## Requirement Inventory
| R-n | Requirement item | Source (G ids) | Maps to (AC ids, or NB-n) | Scope (full, or narrowed: what is left out) |
|---|---|---|---|---|
| R-1 | The reported problem is fixed in the app, not only reported | G-1 | AC-1, AC-3 | narrowed: only the backtest slippage problem of G-2 to G-5; other reported app problems are not in this plan |
| R-2 | The advisor's backtests no longer assume 0.5% slippage per trade by default | G-2, G-13 | AC-1 | full |
| R-3 | The understatement of live results on daily-rebalancing trees is recorded as the reason | G-3 | AC-5 | full |
| R-4 | At 0.1% the backtests track live closely, and that finding is the chosen basis | G-4, G-12 | AC-1, AC-5 | narrowed: "within about 0.02% a day" holds for the cross-symphony mean only (computed from the rows: +0.9 bps a day, not the stated +2.1); per symphony the gap at 0.001 ranges from -11.4 to +9.3 bps a day (Known-Unconfirmed, Q-5) |
| R-5 | The advisor backtest gates stop leaning against high-turnover strategies and against beating SPY | G-5 | AC-3 | full |
| R-6 | A backtest run without an explicit slippage argument assumes the live-calibrated per-trade slippage | G-6 | AC-1 | full |
| R-7 | Every advisor gate that compares backtests (Strategy Builder candidates and SPY baseline, Asset Swaps, Logic Changes, Frontrunner Builder, incubation tick, backtest_gate_engine) compares on that basis | G-6 | AC-3 | full |
| R-8 | The calibration evidence (sample, method, window, settings, gaps, correlation, compounded example, conclusion) is inlined in the plan | G-7, G-8, G-9, G-10, G-11 | AC-5 | narrowed: the per-symphony rows and the compounded example are in this plan's Calibration evidence table; the code comment carries the summary listed in S-5.1 |
| R-9 | Current code is verified at the ref: the client default, the friction constant and its pin test | G-13 | AC-1, AC-4 | full |
| R-10 | Every caller of run_backtest or submit_backtest is found, with whether it passes slippage explicitly | G-14 | AC-3 | full |
| R-11 | The new default value is 0.001 (0.1% per trade) | G-15 | AC-1 | full |
| R-12 | The calibration evidence is recorded where a reader of the constant finds it | G-15 | AC-5 | full |
| R-13 | SIM_EXIT_FRICTION_PCT keeps its value of 0.5 percentage points | G-16, G-18 | AC-4 | full |
| R-14 | SIM_EXIT_FRICTION_PCT is no longer derived from the backtest default, and its pin test changes accordingly | G-17 | AC-4, AC-6 | full |
| R-15 | A caller that passes slippage explicitly is unchanged | G-18 | AC-2 | full |
| R-16 | The engine (alpha_bot_execution.py, math_engine.py) is unchanged | G-18 | AC-1, AC-6, NB-14 | full |
| R-17 | The dashboard's printed friction copy is unchanged | G-18 | AC-4 | full |
| R-18 | No live trade path is added or reached | G-18 | NB-15 | full |
| R-19 | No per-symphony or per-turnover calibration | G-19 | NB-1 | full |
| R-20 | Past advisor observations already stored are not re-run or rewritten | G-19 | AC-3, NB-2 | full |
| R-21 | No UI change | G-19 | NB-3 | full |
| R-22 | No Composer request field other than the slippage default changes | G-19, G-20 | AC-1 | full |
| R-23 | An explicit slippage_percent, 0.0 included, is sent exactly | G-20 | AC-2 | full |
| R-24 | The request body's field name and units are unchanged | G-20 | AC-1 | full |
| R-25 | SPY baseline backtests use the same default as candidates | G-20 | AC-3 | full |
| R-26 | No schema and no route change | G-21 | NB-4 | full |
| R-27 | The Sensitive marking is confirmed or corrected with evidence | G-22 | AC-3 | full |
| R-28 | DE-EXIT-FRICTION-REALIZED-001, DE-MATH-R2-001 and DE-SB-CULL-001 are found and cited | G-23 | AC-1, AC-3, AC-4 | full |

## User Tasks
none: no screen, template or client script changes (NB-3).

## Earlier criteria on the same surface
Searched every `feature-plans/*.md` (including the `.completed.md` and `.obsolete.md` families and the suite folders), `DECISIONS.md`, `docs/generated/*.md`, `docs/audit/` and `docs/audit-inputs/` for `slippage`, `_DEFAULT_SLIPPAGE_PERCENT`, `SIM_EXIT_FRICTION_PCT`, `run_backtest`, the backtest leg of the tenure review, and criteria that hold `advisors/composer_backtest_client.py` or `autotuner.py` byte-unchanged.

| Earlier criterion (plan and AC) | Surface | Disposition (kept, changed or contradicted) | Why |
|---|---|---|---|
| feature-plans/exit-friction-realized-savings.md AC-1 and DE-EXIT-FRICTION-REALIZED-001 (DECISIONS.md:9223, :9239) | SIM_EXIT_FRICTION_PCT (autotuner.py:1448) | changed | the value 0.5 percentage points is kept; its stated derivation as the backtest default times 100 no longer holds once the default is 0.001 (AC-4) |
| feature-plans/exit-friction-realized-savings.md decision row on "one friction worldview" (line 58) | SIM_EXIT_FRICTION_PCT rationale | contradicted | the rationale tied the replay exit cost to the backtest default for consistency; this plan separates them because the two model different costs (G-16) |
| feature-plans/exit-friction-realized-savings.md AC-2 (three replay sites) and AC-3 (not tunable) | autotuner.py:1530, :1866, :2142 | kept | S-4.3 and S-4.6 hold both unchanged |
| feature-plans/strategy-builder-composer-grammar.completed.md request schema (line 354, "default 0.005") | backtest request body | changed | the default becomes 0.001; field name and units are unchanged (AC-1) |
| feature-plans/composer-backtest-schema-drift.completed.md (lines 61, 66, 180) | backtest request body required fields | kept | `slippage_percent` stays a required top-level field holding a fraction (S-1.4) |
| feature-plans/ai-advisor-composer-api-research.completed.md (lines 85, 309, "0.01 = 1%") | unit of `slippage_percent` | kept | the unit stays a fraction of each traded dollar; 0.001 means 0.1% |
| DE-SB-CULL-001 (DECISIONS.md:2343) and its addenda A and B (:2392, :2425) | Strategy Builder SPY baseline and the +inf unavailable sentinel | kept | the SPY baseline is still sent through the same client, now on the same default as the candidates; the withhold on an unavailable SPY series is unchanged (S-3.13) |
| DE-MATH-R2-001 (DECISIONS.md:6636) | `_extract_returns` simple returns (composer_backtest_client.py:140-202) | kept | response parsing is unchanged (S-1.8) |
| feature-plans/strategy-incubation-gate.md AC-3 and line 24 ("fee/slippage params as shipped") | incubation tick backtests | kept | forward segments stay net of cost, now at the new default; rows recorded before the change keep their stored values (S-3.16, Q-3) |
| feature-plans/hygiene-waves-3-4.md AC-2 S-2.3 (the client and its two captured fixtures byte-unchanged) and AC-10 (each content pin moves only over that plan's listed edits) | advisors/composer_backtest_client.py, autotuner.py and their pins | changed | the client digest and the `autotuner.py` full-file and scope digests are re-taken over this plan's listed edits only; the two fixtures keep their digests (AC-6) |
| feature-plans/regime-label-producer.md AC-6 S-6.4 (`autotuner.py` among the files byte-unchanged), pinned by tests/test_regime_label_protected_files.py | autotuner.py | changed | its full-file digest is re-taken over the statement above `SIM_EXIT_FRICTION_PCT` only; the other files that test pins keep their digests (S-6.4) |
| feature-plans/preconditions-replay-load-once.md AC-8 (protected files unchanged outside its listed edits) and its scope pin tests/test_preconditions_replay_protected_scope.py | autotuner.py | changed | the scope digest moves only by this plan's listed edit; S-6.1 proves no other byte moved (Q-6) |
| feature-plans/symphony-tenure-review.md AC-5 (S-5.1) and AC-8 (S-8.2, S-8.5) | tenure review backtest item and the watch rule | kept | the comparison and the 10 percentage point watch threshold are unchanged (S-3.15); the backtest leg's cost basis moves to the new default, so verdicts can differ for the same realized data (Q-1, Q-2) |

## Acceptance Criteria (Building)
### AC-1: The backtest client sends 0.001 when no slippage is given
Requirement items: R-1, R-2, R-4, R-6, R-9, R-11, R-16, R-22, R-24, R-28
Rule: When a caller of `run_backtest` or its alias `submit_backtest` (advisors/composer_backtest_client.py:270, :454) passes no `slippage_percent`, the backtest client shall send Composer's `POST /backtest` a body whose `slippage_percent` is 0.001, a fraction meaning 0.1% of each traded dollar, with every other field as at the ref.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-1.1 | B | a valid symphony tree; Composer answers HTTP 200 | `run_backtest(tree)` is called with no slippage argument | the one POST body carries `slippage_percent` equal to 0.001 |
| S-1.2 | B | the same tree | `submit_backtest(tree)` is called with no slippage argument | the POST body carries `slippage_percent` 0.001 |
| S-1.3 | N1 | the same tree with `start_date`, `end_date` and `compact_body=True`, the tenure review's call form (advisors/tenure_review_runner.py:179-188) | `run_backtest` is called with no slippage argument | the compact JSON text decodes to `slippage_percent` 0.001 and the given `start_date` and `end_date` |
| S-1.4 | N7 | the same tree, no slippage argument, no dates | `run_backtest` is called | the body's keys are exactly `symphony`, `capital`, `apply_reg_fee`, `apply_taf_fee`, `slippage_percent`, `broker`, `backtest_version`; `capital` 10000.0, both fee flags true, `broker` "alpaca", `backtest_version` "v2", `symphony.raw_value` equal to the tree; the URL, the Composer auth headers and the 120 second timeout equal the ref's (composer_backtest_client.py:333-348, :372-374) |
| S-1.5 | N6 | Composer answers HTTP 429 and then HTTP 200 | `run_backtest` is called with no slippage argument | both POST bodies carry 0.001; the result parses the 200 body |
| S-1.6 | N6 | Composer answers HTTP 503 to every attempt | `run_backtest` is called with no slippage argument and the default retries | every attempt's body carries 0.001; the result has `stats` None and an error naming HTTP 503, as at the ref |
| S-1.7 | N2 | Composer answers HTTP 422 to a body carrying the default | `run_backtest` is called with no slippage argument | exactly one POST is sent; the result's error starts "HTTP 422", as at the ref (composer_backtest_client.py:421-423); no request is re-sent with another slippage value |
| S-1.8 | N7 | the captured 200 response in tests/fixtures/composer/backtest_response_v1.json | it is parsed through `run_backtest` with no slippage argument | `stats`, `daily_returns` (simple returns, DE-MATH-R2-001), `daily_portfolio_values`, `first_day`, `last_market_day` and `costs` equal what the client at the ref produces from the same response |
| S-1.9 | N7 | the change is complete | alpha_bot_execution.py and math_engine.py are compared with the ref | both files are byte-identical to the ref |

Negative coverage: N1 S-1.3 · N2 S-1.7 · N3 n/a: the default is a fixed module constant, never computed or read from a source that can be unavailable · N4 n/a: the client evaluates no permission and its auth headers are unchanged (S-1.4); no route is added or changed · N5 n/a: the client keeps no state between calls, and the default is fixed when `run_backtest` is defined at import (composer_backtest_client.py:277), so two concurrent calls send the same value · N6 S-1.5, S-1.6 · N7 S-1.4, S-1.8, S-1.9 · N8 n/a: a backtest is one request and holds no edit open
Questions: Q-5, Q-6

### AC-2: An explicit slippage value is sent exactly as given
Requirement items: R-15, R-23
Rule: If a caller passes `slippage_percent` explicitly, then the backtest client shall send exactly that value and never substitute the default.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-2.1 | B | a valid tree; Composer answers HTTP 200 | `run_backtest(tree, slippage_percent=0.005)` is called | the body carries 0.005 |
| S-2.2 | N1 | the same tree | `run_backtest` is called with `slippage_percent=0.0` (JSON body form) | the body carries 0.0, not 0.001 |
| S-2.3 | N1 | the same tree | `run_backtest` is called with `slippage_percent=0.0` and `compact_body=True` | the compact text decodes to 0.0 |
| S-2.4 | N1 | the same tree | `run_backtest` is called with `slippage_percent=0.001`, equal to the default | the body carries 0.001 |
| S-2.5 | N1 | the same tree | `run_backtest` is called with `slippage_percent=0.0025` | the body carries 0.0025 |
| S-2.6 | N1 | the same tree | `run_backtest` is called with `slippage_percent=None` (JSON body form) | the body carries null, as at the ref; 0.001 is not substituted |
| S-2.7 | N1 | the same tree | `run_backtest` is called with `slippage_percent=-0.001` | the body carries -0.001, as at the ref (the client validates no slippage value, NB-5) |
| S-2.8 | N1 | the same tree | `run_backtest` is called with `slippage_percent` NaN and `compact_body=True` | no request is sent; the error reads "request body not serializable: ValueError", as at the ref (composer_backtest_client.py:349-353) |
| S-2.9 | N1 | the same tree | `run_backtest` is called with `slippage_percent` NaN, then positive infinity, then negative infinity, each in the JSON body form | no request carries 0.001 in place of the given value |
| S-2.10 | N6 | Composer answers HTTP 429 and then HTTP 200 | `run_backtest` is called with `slippage_percent=0.0` | both POST bodies carry 0.0 |

Negative coverage: N1 S-2.2, S-2.3, S-2.4, S-2.5, S-2.6, S-2.7, S-2.8, S-2.9 · N2 n/a: the client adds no refusal of an explicit value; Composer's refusals reach the caller as in S-1.7 · N3 n/a: an explicit value is passed in, never computed · N4 n/a: no permission is evaluated · N5 n/a: no state is kept between calls · N6 S-2.10 · N7 S-2.1 · N8 n/a: one request, no edit held open
Questions: none

### AC-3: Every advisor surface backtests on the one default
Requirement items: R-1, R-5, R-7, R-10, R-20, R-25, R-27, R-28
Rule: When any advisor surface runs Composer backtests, every backtest request it sends, including each SPY baseline, incumbent, baseline, variant and candidate request in the same run, shall carry `slippage_percent` 0.001.

At the ref no production caller passes `slippage_percent` (repository search of every `run_backtest`, `submit_backtest` and `backtest_fn` use outside tests); the call sites are advisors/strategy_builder_engine.py:442 (compiler `backtest_fn`, called at advisors/plan_tree_compiler.py:517), :1068 (SPY), :1082 (candidates); advisors/asset_swap_engine.py:824 (SPY), :924, :931, :1516; advisors/logic_change_engine.py:555 (SPY), :648, :656, :967; advisors/frontrunner_builder.py:2197, :2201; advisors/incubation.py:280 (SPY), :304; advisors/tenure_review_runner.py:179-188. `advisors/backtest_gate_engine.py` sends no request; it gates the results these callers pass it.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-3.1 | B | Composer and the generation model are stubbed at their seams; a symphony-scoped Strategy Builder run yields two compiled candidates | `POST /ai-advisor/strategy-builder/run` (app.py:11921) is called | every backtest body the run sends (the SPY baseline and each candidate) carries 0.001 |
| S-3.2 | B | the same stubs | the weekly Strategy Builder run (advisors/strategy_builder_scheduler.py:106, :166) runs | every backtest body it sends carries 0.001 |
| S-3.3 | B | an explicit swap pair for a live symphony | `POST /ai-advisor/asset-swaps/evaluate` (app.py:11247) is called | the SPY, baseline and variant bodies carry 0.001 |
| S-3.4 | B | the weekly suggestions run with one symphony | the weekly asset swap suggestions run (advisors/weekly_suggestions_scheduler.py:398) runs | the baseline returns body (advisors/asset_swap_engine.py:1516), the SPY body and every baseline and variant body carry 0.001 |
| S-3.5 | B | a reasoned logic change for a live symphony | `POST /ai-advisor/logic-changes/evaluate` (app.py:11602) is called | the SPY, baseline and variant bodies carry 0.001 |
| S-3.6 | B | the weekly suggestions run with one symphony | the weekly logic change suggestions run (advisors/weekly_suggestions_scheduler.py:153) runs | the baseline returns body (advisors/logic_change_engine.py:967), the SPY body and every baseline and variant body carry 0.001 |
| S-3.7 | B | one live symphony with a detected cascade and one generated overlay | `POST /ai-advisor/frontrunner-builder/run` (app.py:12205) dispatches the build | the incumbent and candidate bodies carry 0.001 |
| S-3.8 | B | the same | the weekly Frontrunner Builder run (advisors/strategy_builder_scheduler.py:210) runs | the incumbent and candidate bodies carry 0.001 |
| S-3.9 | B | one INCUBATING candidate | the 03:30 incubation tick runs | the SPY body and the candidate body carry 0.001 |
| S-3.10 | B | one eligible symphony; the market is closed | `POST /ai-advisor/retirement/review/run` (app.py:12573) runs the tenure review | the backtest body (compact form, with start and end dates) carries 0.001 |
| S-3.11 | B | the same | the 03:45 tick runs the tenure review (app.py:2846) | the backtest body carries 0.001 |
| S-3.12 | N1 | a Strategy Builder plan whose first compiled tree Composer refuses as untradeable (HTTP 400) | the compiler prunes the ticker and retries (advisors/plan_tree_compiler.py:517) | the retry's body also carries 0.001 |
| S-3.13 | N6 | the SPY baseline backtest fails with HTTP 503 after its retries | a Strategy Builder run completes | each candidate body still carries 0.001; candidates are withheld with `below_spy_alpha` as at the ref (DE-SB-CULL-001 addendum B); no request is re-sent with another slippage value |
| S-3.14 | N7 | the same inputs and stubbed responses at the ref and after the change | any surface of S-3.1 to S-3.11 runs | the number, order and URLs of Composer requests and every body field other than `slippage_percent` are equal; the gate rules (BHY FDR, PBO veto above 0.5, the SPY baseline rule, the Calmar acceptance) are unchanged |
| S-3.15 | N7 | an eligible symphony not flagged by the redundancy rule whose backtest total return exceeds its realized total return by 12 percentage points, and a second by exactly 10 | a tenure review completes | the first reads watch with "trails its own backtest"; the second does not meet the backtest condition; the 10 point threshold (advisors/tenure_review.py:49) and the rule (:1187-1196) are unchanged |
| S-3.16 | N7 | `incubation_daily` rows written before the change for an INCUBATING candidate | the incubation tick runs after the change and Composer returns the full history | the earlier rows keep their stored values (INSERT OR IGNORE, database.py:5180); only trading days not yet recorded are added; the candidate's admission `backtest_mdd_pct` is unchanged |
| S-3.17 | N7 | `advisor_observations` rows written before the change | any surface of S-3.1 to S-3.11 runs after it | no earlier row is updated or deleted; the run only appends its own rows |

Negative coverage: N1 S-3.12 · N2 n/a: no surface adds a refusal; a Composer refusal is handled in the one client every surface calls, tested as S-1.7 under AC-1 · N3 n/a: no new computed or unavailable value is introduced; each surface's existing handling of an empty or failed backtest is unchanged (S-3.14) · N4 n/a: no route is added and every route's auth and CSRF hooks are unchanged · N5 n/a: the default is fixed when `run_backtest` is defined at import (composer_backtest_client.py:277), so concurrent runs in one process send the same value; every scenario asserts the sent body, never a patched constant · N6 S-3.13 · N7 S-3.14, S-3.15, S-3.16, S-3.17 · N8 n/a: no surface holds an edit open while it backtests
Questions: Q-1, Q-2, Q-3

### AC-4: The simulated exit cost stays 0.5 percentage points and stands on its own basis
Requirement items: R-9, R-13, R-14, R-17, R-28
Rule: When the backtest slippage default is 0.001, `autotuner.SIM_EXIT_FRICTION_PCT` shall stay 0.5 percentage points, every surface that prints or subtracts it shall use 0.5, and no check or provenance statement in the repository shall derive it from the backtest default.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-4.1 | B | the change is complete | `autotuner.SIM_EXIT_FRICTION_PCT` and the backtest default are read | they are 0.5 and 0.001 |
| S-4.2 | N7 | the dashboard and Performance page copy that names the trading cost (app.py:2741-2744) | the pages render | the printed cost is 0.5, as at the ref |
| S-4.3 | N7 | a replay with a triggered exit | each of the three replay accounting sites runs (autotuner.py:1530, :1866, :2142) | each subtracts 0.5, as at the ref |
| S-4.4 | N7 | the checks that pin `SIM_EXIT_FRICTION_PCT` (tests/autotuner/test_exit_friction_constant.py, the units check at :76-117) | their source is read and they run | they pass with the value 0.5 percentage points, and none reads `composer_backtest_client`'s default to compute the expected value |
| S-4.5 | N7 | the statement beside `SIM_EXIT_FRICTION_PCT` (autotuner.py:1433-1448) and its entries in docs/generated/autotuner.md (lines 72 and 452) | they are read | each says the value models the cost of a full-position exit in the replay and cites docs/research/methodology-validation-2026-07.md; none says it is derived from, or equals, the backtest slippage default times 100 |
| S-4.6 | N7 | the non-tunability checks of DE-EXIT-FRICTION-REALIZED-001 | they run | `SIM_EXIT_FRICTION_PCT` is absent from `OPTUNA_SEARCH_SPACE_KEYS`, never read from trial params or the environment, and absent from math_engine.py |
| S-4.7 | N7 | the same post-mortem and exit-turnover inputs at the ref and after the change | `GET /api/guard-alpha-summary` and `GET /api/exit-turnover` are called | the net-of-friction fields and `est_annual_friction_drag_pct` are equal |
| S-4.8 | N7 | the check on the statement beside `SIM_EXIT_FRICTION_PCT` (tests/autotuner/test_exit_friction_constant.py:120-155, which at the ref requires the statement to cite the Composer backtest client as its provenance, :146-150) | it runs after the change | it requires the statement to cite docs/research/methodology-validation-2026-07.md and to name the full-position exit cost in the replay as its basis; no check requires the statement to cite the backtest client or its default as provenance |

Negative coverage: N1 n/a: the constant takes no input · N2 n/a: nothing is refused · N3 n/a: a fixed constant is never unavailable; the page copy's existing unreadable-value case is unchanged · N4 n/a: no permission is involved · N5 n/a: a constant read has no concurrency · N6 n/a: the constant reads no dependency · N7 S-4.2, S-4.3, S-4.4, S-4.5, S-4.6, S-4.7, S-4.8 · N8 n/a: nothing is edited
Questions: none

### AC-5: The calibration evidence sits beside the default
Requirement items: R-3, R-4, R-8, R-12
Rule: When a reader opens the definition of the backtest slippage default or its generated documentation page, they shall find the value 0.001, its unit (a fraction of each traded dollar) and the calibration evidence behind it, and no statement there shall give 0.005 as the default or call the slippage default Composer's UI default.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-5.1 | B | the statement at the default's definition (advisors/composer_backtest_client.py, the constant at :80) | it is read | it names: 8 live symphonies; Composer backtest v2, broker alpaca; realized deposit-adjusted daily returns as the comparison; 87 trading days since each tree's last edit (46 for one symphony), 2026-07-15 to 2026-11-13; the settings 0, 0.001 and 0.005; the simple mean of the eight per-symphony gaps (live minus backtest) of about -6.5, +0.9 and +30 bps a day; daily correlation 0.976 to 0.996; the per-symphony gap at 0.001 of -11.4 to +9.3 bps a day; the measurement date 2026-11-16; and the conclusion that 0.001 is closest to live |
| S-5.2 | B | docs/generated/advisors_composer_backtest_client.md (signature line 23, parameter table line 34, constants table line 97) | it is read | each gives 0.001 and the page carries the evidence summary of S-5.1 |
| S-5.3 | N7 | the client module and its generated page | they are searched for the default | no line gives 0.005 as the default; the `run_backtest` docstring's unit example reads 0.001 as 0.1% |
| S-5.4 | N7 | the statement that the default parameters match the Composer UI defaults (composer_backtest_client.py:78) | it is read | it no longer covers the slippage default |
| S-5.5 | N1 | the evidence statements of S-5.1 and S-5.2 | they are searched | they carry no machine-local or untracked file path, and each number equals this plan's Calibration evidence table, the means taken from its computed rows |

Negative coverage: N1 S-5.5 · N2 n/a: documentation refuses nothing · N3 n/a: every figure is a recorded measurement, none computed at runtime · N4 n/a: no permission is involved · N5 n/a: static text · N6 n/a: static text reads no dependency · N7 S-5.3, S-5.4 · N8 n/a: nothing is edited at runtime
Questions: Q-5

### AC-6: Each content pin on the two edited files moves only over this plan's listed edits
Requirement items: R-14, R-16
Rule: When the change is complete, `advisors/composer_backtest_client.py` and `autotuner.py` shall differ from the ref by exactly the listed edits below, and each content pin on those files shall be re-taken over those edits only, while every other file those pins hold keeps its ref digest.

Listed edits: `advisors/composer_backtest_client.py`: the slippage default (:80) with the statement beside it (:78), and the `slippage_percent` unit line of the `run_backtest` docstring (:303-304). `autotuner.py`: the statement above `SIM_EXIT_FRICTION_PCT` (:1433-1447); the assignment (:1448) is unchanged. The content pins on these files at the ref: the full-file digest of the client (tests/ai_advisor/test_advisor_backtest_client.py:1274-1276, asserted at :1362-1366), the full-file digest of `autotuner.py` (tests/test_regime_label_protected_files.py:40), and the two scope digests of `autotuner.py` (tests/test_hygiene_protected_surfaces.py:197, asserted at :210-213, and tests/test_preconditions_replay_protected_scope.py), two of which say to stop and ask the PM before a re-take (Q-6).

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-6.1 | B | the change is complete | each of the two files is compared with the ref, hunk by hunk | its hunks are exactly its listed edits, and each of the four pins holds the new digest with a comment naming this plan |
| S-6.2 | N1 | one byte changed outside the listed edits in each of the two files | the pin tests run | each pin on that file fails |
| S-6.3 | N7 | the captured fixtures pinned beside the client (tests/fixtures/composer/backtest_inline_v1.json, backtest_response_v1.json) | the client pin test runs | their digests are the ref's, not re-taken |
| S-6.4 | N7 | the other files the same pin tests hold (`math_engine.py`, `regime_classifier.py`, `synthetic_history.py`, `migrations/027_regime_label_cache.sql`, `alpha_bot_execution.py`) | the pin tests run | their digests and scope digests are the ref's, not re-taken |
| S-6.5 | N7 | the change is complete | the repository-root pin tests (tests/test_regime_label_protected_files.py, tests/test_hygiene_protected_surfaces.py, tests/test_preconditions_replay_protected_scope.py, tests/test_mdd_window_truth_zero_diff_guard.py) and tests/ai_advisor/test_advisor_backtest_client.py run | all pass |

Negative coverage: N1 S-6.2 · N2 n/a: a pin refuses nothing at runtime; it fails a test · N3 n/a: digests are recorded values, never unavailable · N4 n/a: no permission is involved · N5 n/a: static file content · N6 n/a: the pins read only repository files · N7 S-6.3, S-6.4, S-6.5 · N8 n/a: nothing is edited at runtime
Questions: Q-6

## Rule-by-Entry-Point Matrix
| Rule | Strategy Builder route | Strategy Builder weekly | Asset Swaps route | Asset Swaps weekly | Logic Changes route | Logic Changes weekly | Frontrunner route | Frontrunner weekly | Incubation tick | Tenure review route | Tenure review tick |
|---|---|---|---|---|---|---|---|---|---|---|---|
| AC-3: no slippage given, 0.001 sent | S-3.1, S-3.12 | S-3.2 | S-3.3 | S-3.4 | S-3.5 | S-3.6 | S-3.7 | S-3.8 | S-3.9 | S-3.10 | S-3.11 |
| AC-3: SPY baseline on the candidates' default | S-3.1, S-3.13 | S-3.2 | S-3.3 | S-3.4 | S-3.5 | S-3.6 | n/a: the Frontrunner Builder runs no SPY baseline | n/a: the Frontrunner Builder runs no SPY baseline | S-3.9 | n/a: the tenure review compares with realized returns, not SPY | n/a: the tenure review compares with realized returns, not SPY |
| AC-2: explicit value sent exactly | n/a: passes no slippage at the ref | n/a: passes no slippage at the ref | n/a: passes no slippage at the ref | n/a: passes no slippage at the ref | n/a: passes no slippage at the ref | n/a: passes no slippage at the ref | n/a: passes no slippage at the ref | n/a: passes no slippage at the ref | n/a: passes no slippage at the ref | n/a: passes no slippage at the ref | n/a: passes no slippage at the ref |

## Not Building
Non-goals:
- NB-1: Per-symphony or per-turnover slippage calibration (excludes R-19 as given).
- NB-2: Re-running or rewriting stored advisor observations, `incubation_daily` rows, incubation admission baselines or tenure review records written before the change (excludes R-20 as given).
- NB-3: Any UI change; no page states the backtest slippage assumption today and none will (excludes R-21 as given).
- NB-4: Any schema, migration or route change (R-26 as given).
- NB-5: Validation of an explicit `slippage_percent` (negative, NaN or infinite values keep their ref behavior; approved).
- NB-6: A field recording the slippage used on stored observations or in API responses (approved).
- NB-7: Re-tuning the tenure review's 10 percentage point watch threshold for the new basis (approved; Q-2).
- NB-8: Special handling for candidates already incubating when the default changes (approved; Q-3).
- NB-9: An operator setting or environment key for the slippage default; it is not added to `_SETTINGS_WRITE_ALLOWLIST` (approved).
- NB-10: Editing the recorded request bodies inside captured Composer fixtures (tests/fixtures/composer/backtest_inline_v1.json, backtest_inline_transit_v1.json, backtest_response_v1.json, and the tenure review family under tests/fixtures/composer/tenure-review/: case4_definition_sizes.json, case5_backtest_dated_compact_nOyb55RMGVCKPiYXv7TI.json, case6_backtest_413_too_large.json, case7c_backtest_malformed_structure_400.json, case7c_backtest_unknown_ticker_500.json and README.md); they record what was sent at capture time (approved).
- NB-11: Editing dated research and audit records that state the old default (docs/research/methodology-validation-2026-07.md line 105, docs/audit/COMPOSER-HISTORICAL-SERIES-2026-09-03.md line 159) (approved).

Behavior that stays exactly as it is:
- NB-12: Every other request field, the retry policy, the timeout and response parsing of the backtest client (AC-1).
- NB-13: `SIM_EXIT_FRICTION_PCT`'s value, its three replay subtraction sites and every display that reads it (AC-4).

Deferred:
- NB-16: Repeating the calibration measurement on a later window and adjusting the default from that evidence (approved; Follow-Ups).

Protected surfaces this consumes but does not change:
- NB-14: The execution engine, alpha_bot_execution.py and math_engine.py (S-1.9).
- NB-15: The no-trade boundary. The only route that reaches Composer symphony creation stays the proposal-approve route (`POST /ai-advisor/proposal/approve`), which creates an undeployed symphony; the panic-sell route stays the only dashboard trade button; the Managed Sleeves order path and its arming ladder are untouched; effective live trading stays `LIVE_EXECUTION` AND the symphony's `live_mode`; `LIVE_EXECUTION` and credentials stay outside `_SETTINGS_WRITE_ALLOWLIST`. The existing boundary tests tests/security/test_frontrunner_no_trade_boundary.py, tests/security/test_retirement_action_no_trade_boundary.py and tests/security/test_retirement_recommender_no_trade_boundary.py pass unchanged, and tests/autotuner/test_exit_friction_blast_radius.py keeps `SIM_EXIT_FRICTION_PCT` out of the engine files.

## Defect Classes
| DC-n | Class (what goes wrong) | Trigger (B or N1-N8) | Sibling search (tool, query, scope) | Scenarios | Raised in review as |
|---|---|---|---|---|---|
| DC-1 | Rule enforced at one entry point, missing at another: one advisor surface sends a different slippage from its siblings or from its own SPY baseline | B | grep: `run_backtest`, `submit_backtest`, `backtest_fn`, `slippage` in every tracked `*.py` outside tests (advisors/, app.py, scripts/, root modules); AlphaBotPM | S-3.1 to S-3.13 | none |
| DC-2 | Boundary or edge state mishandled: an explicit 0.0 or None is replaced by the default | N1 | grep: `slippage_percent or`, `if not slippage`, `slippage_percent=` in advisors/ and app.py; AlphaBotPM | S-2.2, S-2.3, S-2.6 | none |
| DC-3 | Contract change not carried to every consumer: a check, comment or doc still states 0.005 as the default or derives the exit cost from it | B | grep: `0.005`, `_DEFAULT_SLIPPAGE_PERCENT`, `0.5 %`, `RETURN_PCT_TO_FRACTION` near `SIM_EXIT_FRICTION_PCT` in `*.py`, docs/generated/, .claude/CLAUDE.md, and `*.json` and `*.md` under tests/fixtures/; AlphaBotPM | S-4.4, S-4.5, S-5.2, S-5.3, S-5.4 | none |
| DC-4 | Data that must stay unchanged is written: another request field, the exit cost or a stored row changes | N7 | read: the request body build (composer_backtest_client.py:333-355); grep: writes to `advisor_observations` and `incubation_daily`, assignments of `SIM_EXIT_FRICTION_PCT`; AlphaBotPM | S-1.4, S-3.14, S-3.16, S-3.17, S-4.1, S-4.7, S-6.2, S-6.3, S-6.4 | none |
| DC-5 | Narrow assertion licenses unlisted siblings: only the JSON body form or only `run_backtest` is tested | B, N1 | read: every test of the client body against both body forms and both names | S-1.2, S-1.3, S-2.3, S-2.8, S-2.9 | none |
| DC-6 | Dependency failure answered with another basis: a refused or retried request falls back to a different slippage value | N6 | grep: retry and error paths in composer_backtest_client.py and every caller's error branch; AlphaBotPM | S-1.5, S-1.6, S-1.7, S-2.10, S-3.13 | none |
| DC-7 | Planned example values not encoded in a test | B | read: this plan's Worked Example against the tests of S-1.1, S-2.2, S-3.1, S-3.13 | S-1.1, S-2.2, S-3.1, S-3.13 | none |

## Worked Example
Input: a Strategy Builder run for a live symphony named Willow Creek Rotation compiles one candidate, Harbor Lights Momentum, a daily-rebalancing tree. The run sends two backtest requests. The SPY baseline request body reads `{"symphony": {"raw_value": ...}, "capital": 10000.0, "apply_reg_fee": true, "apply_taf_fee": true, "slippage_percent": 0.001, "broker": "alpaca", "backtest_version": "v2"}` and the candidate request body is identical except for `raw_value` (S-3.1). At the ref both carried 0.005. A test that calls `run_backtest(tree, slippage_percent=0.0)` sees a body carrying 0.0 (S-2.2).

Main failure case: the SPY baseline request for the same run gets HTTP 503 on every attempt. Every attempt's body carries 0.001 (S-1.6); the candidate request still carries 0.001; the candidate is withheld with `below_spy_alpha` exactly as at the ref, and no request is re-sent with 0.005 (S-3.13).

## Fail-Closed Behaviour
none: no input now refuses instead of defaulting. A Composer refusal of a body carrying the default reaches the caller as the existing error result (S-1.7), and the client's existing refusal of an unserializable compact body is unchanged (S-2.8).

## Authorities Touched
| Value | Authority used | What it replaced | What proves no second source |
|---|---|---|---|
| Backtest slippage default | `_DEFAULT_SLIPPAGE_PERCENT` (advisors/composer_backtest_client.py:80), the default of `run_backtest` (:277) | the value 0.005 | the caller census in AC-3 finds no production caller that passes `slippage_percent`; DC-1's search |
| Simulated exit cost | `SIM_EXIT_FRICTION_PCT` (autotuner.py:1448) | its stated derivation from the backtest default | tests/autotuner/test_exit_friction_blast_radius.py and S-4.4 |

## Contract Changes and Consumers
The request body sent to Composer's `POST /backtest` keeps its shape; only the default value of `slippage_percent` changes from 0.005 to 0.001, still a fraction of each traded dollar. It is backward compatible: Composer accepted 0.001 in the PM's 2026-11-16 measurement (Known-Unconfirmed). The consumers are Composer, and inside the app the callers listed under AC-3, which see the new default from the first request after deploy. No route, wire field or stored record changes shape.

## Sibling Repos and Landing Order
none: the change is in AlphaBotPM alone.

## Migrations Required
none: no schema or stored record changes.

## Configuration and Enablement
none: the default is a code constant; no configuration key is added (NB-9) and nothing ships dark.

## Breaking Changes
For the same inputs, advisor outcomes can differ from the ref because candidates, incumbents and SPY baselines are backtested at 0.1% instead of 0.5% per trade: high-turnover candidates score higher relative to SPY, and more of them can pass the gates. The tenure review's backtest leg rises toward realized results, so a symphony whose realized return trails a 0.1% backtest by more than 10 percentage points now reads watch where at the ref it read keep (Q-1). Candidates incubating at deploy mix rows recorded at 0.5% with rows recorded at 0.1% (Q-3). Stored observations keep the basis they were written on, so observations written before and after the deploy are not comparable on cost (NB-2, NB-6).

## Security Considerations
- Input validation and injection: `slippage_percent` is never taken from a request or a form; no route accepts it, and no caller passes it (AC-3). Explicit values from code keep their ref behavior (AC-2, NB-5).
- Authorization and access control: no route is added; every advisor route keeps its auth and CSRF hooks (AC-3 N4 reason).
- Data exposure: the request body carries the same fields to the same endpoint with the same headers (S-1.4).
- Client-side rendering: no template or script changes (NB-3).
- Abuse and rate limits: the number of Composer requests per run is unchanged (S-3.14), so the incubation bill bound (one request per incubating candidate plus one SPY request per tick) and every rate-limit spacing hold.
- Money: corrected from the intake's "no" (D-3). No order path is reached (NB-15), but the backtests this changes drive real-money keep, retire and adopt decisions: the tenure review's keep or watch verdict reads the backtest leg (advisors/tenure_review.py:1187-1196), and the advisor gates decide which candidates the operator is shown to adopt. Covered by AC-1 and AC-3.

## Prerequisites
none: the Composer backtest endpoint and its credentials are already in use; no new runtime, service or provider.

## Design-System Mapping
none: no UI change (NB-3).

## Definition of Done (Verification)
| AC | Scenarios | Test level | Real-entrypoint test |
|---|---|---|---|
| AC-1 | S-1.1, S-1.2, S-1.3, S-1.4, S-1.5, S-1.6, S-1.7, S-1.8, S-1.9 | unit on the client with `requests.post` captured; S-1.8 with the captured response fixture; S-1.9 a diff against the ref | `run_backtest` and `submit_backtest` called as production callers call them |
| AC-2 | S-2.1, S-2.2, S-2.3, S-2.4, S-2.5, S-2.6, S-2.7, S-2.8, S-2.9, S-2.10 | unit on the client with `requests.post` captured | `run_backtest` called with each explicit value in both body forms |
| AC-3 | S-3.1, S-3.2, S-3.3, S-3.4, S-3.5, S-3.6, S-3.7, S-3.8, S-3.9, S-3.10, S-3.11, S-3.12, S-3.13, S-3.14, S-3.15, S-3.16, S-3.17 | route and job tests with Composer captured at `requests.post` and the generation model stubbed at its seam | each route through the Flask test client (S-3.1, S-3.3, S-3.5, S-3.7, S-3.10) and each scheduled job through its tick or weekly entry function (S-3.2, S-3.4, S-3.6, S-3.8, S-3.9, S-3.11) |
| AC-4 | S-4.1, S-4.2, S-4.3, S-4.4, S-4.5, S-4.6, S-4.7, S-4.8 | unit on autotuner and the page copy; route tests for S-4.7 | the dashboard and Performance renders and `GET /api/guard-alpha-summary`, `GET /api/exit-turnover` through the Flask test client |
| AC-5 | S-5.1, S-5.2, S-5.3, S-5.4, S-5.5 | source and document text checks | not applicable: documentation, read as a reader opens it |
| AC-6 | S-6.1, S-6.2, S-6.3, S-6.4, S-6.5 | content-pin tests and a hunk comparison with the ref | the repository-root pin tests and the client pin test as CI runs them |

Gates: `ruff format --check`, `ruff check`, a scoped pytest run over the touched suites (tests/ai_advisor/, tests/autotuner/, tests/app/, tests/ui/ page copy tests, tests/security/, the incubation and tenure review suites, and the repository-root pin tests tests/test_regime_label_protected_files.py, tests/test_hygiene_protected_surfaces.py, tests/test_preconditions_replay_protected_scope.py and tests/test_mdd_window_truth_zero_diff_guard.py) with at most 4 workers and never `-n auto`, CI green on the exact SHA, and the PM's live functional check after the PR review: one read-only backtest of a live symphony's tree through the client with no slippage argument, confirming HTTP 200, a request body carrying 0.001, and a `costs` slippage figure lower than the same tree's backtest at 0.005.

Fixture provenance: response fixtures are the existing captures in tests/fixtures/composer/ (marked `captured-from-producer`), used unchanged because response parsing is unchanged; request bodies are asserted from what the client builds, never from a hand-built fixture. No new fixture is required. The tenure review capture case5 (recorded at 0.005) is replayed through `run_backtest` with no slippage argument (tests/ai_advisor/test_advisor_backtest_client.py:731-743); after the change that test sends 0.001 against a response captured at 0.005, which stays valid because the test checks parsing only.

## Success Measures
- The tenure review's `total_return_gap` (realized minus backtest) across reviewed symphonies centres near zero instead of sitting well above it; read from the latest `TENURE_REVIEW_RUN` advisor observation after the first review run following deploy.
- A repeat of the calibration measurement on a later window keeps the cross-symphony mean gap at 0.001 within a few bps a day (NB-16).

## Known-Unconfirmed and Standing Risk
Calibration evidence (PM live read, 2026-11-16, read-only; the raw files are not tracked). Method: for 8 live symphonies, the current Composer tree was backtested (Composer v2, broker alpaca) at `slippage_percent` 0, 0.001 and 0.005 and compared day by day with Composer's realized deposit-adjusted daily returns over the 87 trading days since each tree's last edit (2026-07-15 to 2026-11-13). Daily correlation live with backtest was 0.976 to 0.996 for all 8.

| Symphony | Days | Gap at 0 (bps/day) | Gap at 0.001 (bps/day) | Gap at 0.005 (bps/day) |
|---|---|---|---|---|
| HuntedCascades | 87 | 1.6 | 7.8 | 32.2 |
| FeaverdAlloc | 87 | 0.1 | 9.3 | 45.9 |
| ErasedHistory | 87 | -11.1 | -2.7 | 30.5 |
| GoldenAgeCopy | 87 | -1.1 | 4.2 | 25.2 |
| Reasonabilists | 46 | -0.9 | 7.1 | 38.8 |
| LQD+EYEG | 87 | -18.1 | -11.4 | 15.3 |
| CorpChaos | 87 | -3.4 | 3.2 | 29.3 |
| Paragons | 87 | -18.7 | -10.5 | 22.2 |
| Cross-symphony mean, simple, computed from the rows above | | -6.45 | +0.875 | +29.9 |
| Cross-symphony mean, weighted by days | | | +0.49 | |
| Cross-symphony mean as the PM stated it (G-10) | | about -6.5 | about +2.1 | about +30 |

Gap is live minus backtest. The PM's stated mean at 0.001 (+2.1) does not follow from the per-symphony rows by a simple or a day-weighted mean; the plan and the code use the computed +0.9, and the conclusion that the mean at 0.001 is slightly conservative (live above backtest) still holds. Compounded over the 87 days, FeaverdAlloc earned +57.2% live against a backtest of +54.8%, +43.1% and +4.4% at the three settings.

Standing risks and unconfirmed claims:
- "Slightly conservative" holds for the cross-symphony mean only. At 0.001 three of the eight backtests exceed live (ErasedHistory, LQD+EYEG, Paragons). By linear interpolation between each symphony's gaps at 0.001 and 0.005 (an inference, not a measurement), every measured backtest stays at or below live from about 0.0027 upward, where the cross-symphony mean gap is about +13 bps a day (Q-5).
- LQD+EYEG and Paragons trail even a zero-slippage backtest by about 18 bps a day, so part of the live shortfall is a cost slippage does not model (fees, fills or data); no single default fits both groups (NB-1).
- The sample is 8 live symphonies on daily-rebalancing trees. Candidates with lower turnover pay proportionally less slippage at any setting, so the default is not tuned to them; this is not measured.
- The unit of `slippage_percent` is a fraction (0.01 means 1%) per the Composer API research (feature-plans/ai-advisor-composer-api-research.completed.md lines 85 and 309); the measured gap moving about 36 bps a day between 0 and 0.005 on daily-rebalancing trees agrees with a fraction and not with a percent. Composer's own documentation of the field is not re-read here.
- Composer's UI default slippage is not confirmed; the client's comment calling its defaults the UI defaults (composer_backtest_client.py:78) is unverified, and this plan stops claiming it for slippage (S-5.4).
- Composer accepted 0.001 in the PM's measurement; the live check in the Definition of Done re-confirms it through the client.
- Tenure review effect: at 0.001 the measured gaps of LQD+EYEG (-11.4 bps a day) and Paragons (-10.5 bps a day) would sum past 10 percentage points within a 126-session window if they persisted (simple sum, not compounded), so those two would read watch; at 0.005 no measured symphony trails its backtest. Which symphonies are tenure-eligible today is not known from code; settle with the read-only query `SELECT raw_response FROM advisor_observations WHERE advisor_role = 'TENURE_REVIEW_RUN' ORDER BY id DESC LIMIT 1` on the production state database.
- Incubation mix: how many candidates are incubating at deploy is not known from code; settle with `SELECT COUNT(*) FROM strategy_incubation WHERE status = 'INCUBATING'`. Each one's forward series mixes 0.5% and 0.1% days, and its MDD breach test compares with an admission baseline taken at 0.5% (Q-3).

## Open Questions
| Q-n | Question | Blocks (AC ids or "plan") | Proposed default | Answer |
|---|---|---|---|---|
| Q-1 | Does the tenure review's backtest leg move to the new default with the advisor gates? It passes no slippage today, so it moves unless excluded, and its watch verdicts can change (two of the eight measured symphonies would read watch). | AC-3 | yes: it is the one consumer that compares a backtest with realized returns, the comparison the calibration measures | [PM-ASSUMED] yes: the tenure review is a consumer and takes the same default |
| Q-2 | Does the tenure review's 10 percentage point watch threshold stay 10 on the new basis? | AC-3 | yes, unchanged; re-tuning it is NB-7 | [PM-ASSUMED] yes: the 10 percentage point threshold stays |
| Q-3 | Do candidates already incubating at deploy get special handling for their mixed basis and their admission baseline taken at 0.5%? | AC-3 | no; their rows stay as written (S-3.16) and the mix is recorded as a standing risk (NB-8) | [PM-ASSUMED] no special handling; the mixed-basis period is an accepted limitation |
| Q-4 | removed: settled by D-3; the project's Sensitive definition covers anything that drives a real-money keep, retire or trade decision, and the tenure watch verdict reads the backtest leg (advisors/tenure_review.py:1187-1196) | none | none | [PM-ASSUMED] Sensitive: money, accepted (D-3) |
| Q-5 | The default 0.001 is conservative on the cross-symphony mean but not for three of the eight symphonies; about 0.0027 would be conservative for all eight at a mean gap of about +13 bps a day (interpolated). Which value? | AC-1, AC-5 | 0.001, the intake's value | [PM-ASSUMED] 0.001: the goal is the setting closest to live, not a one-sided bound |
| Q-6 | Two of the content pins on `autotuner.py` say to stop and ask the PM before a re-take. Does approving this plan authorize re-taking the four pins of AC-6 over the listed edits? | AC-6 | yes, over the listed edits only, each re-take proven by S-6.1 | [PM-ASSUMED] yes: approval authorizes re-taking the four content pins over the listed edits only |

## Decisions
| ID | Decision | Rationale |
|---|---|---|
| D-1 | The backtest default becomes 0.001 [PM-ASSUMED] | closest of the three measured settings to live results, with the cross-symphony mean (+0.9 bps a day computed from the rows) slightly conservative (Calibration evidence) |
| D-2 | `SIM_EXIT_FRICTION_PCT` stays 0.5 percentage points and stops being derived from the backtest default [PM-ASSUMED] | it models a full-position exit in the replay, a different cost from daily-rebalance slippage; changing it would move the optimizer objective without evidence (G-16) |
| D-3 | Sensitive is marked money, correcting the intake | the tenure review verdict and the advisor adopt gates read these backtests and drive real-money keep, retire and adopt decisions (advisors/tenure_review.py:1187-1196) |
| D-4 | Dated records that state the old default or the old derivation stay unchanged: DECISIONS.md:9239, feature-plans/exit-friction-realized-savings.md:10 and :22, docs/research/methodology-validation-2026-07.md line 105, docs/audit/COMPOSER-HISTORICAL-SERIES-2026-09-03.md line 159 [PM-ASSUMED] | they record what was true when written; the new basis is recorded by this plan's DECISIONS.md entry at ship (NB-11) |

## Docs to Update
- docs/generated/advisors_composer_backtest_client.md: the default in the signature, parameter and constants entries, with the evidence summary (S-5.2).
- docs/generated/autotuner.md: the `SIM_EXIT_FRICTION_PCT` entries at lines 72 and 452 lose the derivation from the backtest default (S-4.5).
- docs/generated/INDEX.md: the log entry for both pages.
- README.md: only if a statement there names the backtest slippage default (none found at the ref).
- DECISIONS.md: an appended entry minted at ship by the doc-writer, citing DE-EXIT-FRICTION-REALIZED-001, DE-SB-CULL-001 and DE-MATH-R2-001.
- .claude/CLAUDE.md Key Files rows, drafted for PM approval: the `autotuner.py` row's "derived from composer_backtest_client._DEFAULT_SLIPPAGE_PERCENT * RETURN_PCT_TO_FRACTION" wording, and the `advisors/composer_backtest_client.py` row gains the calibrated default.

## Follow-Ups
- NB-16: repeat the calibration measurement on a later window; tracked as one backlog line in BACKLOG.md at ship.
- NB-6: a slippage provenance field on stored observations; one backlog line if the operator wants it.
- NB-7 and NB-8: tracked under Q-2 and Q-3 until answered.
