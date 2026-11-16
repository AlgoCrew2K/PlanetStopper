# Approval packet: Backtest slippage default calibrated to live Composer results

## What is being built

A Composer backtest that the app runs without an explicit slippage argument assumes 0.1% slippage per trade, the setting a 2026-11-16 measurement found closest to live Composer results, so every advisor surface that compares backtests (Strategy Builder with its SPY baseline, Asset Swaps, Logic Changes, Frontrunner Builder, the incubation tick and the tenure review) compares on one basis that tracks realized results: candidates, backtested incumbents and SPY baselines all carry the same default (Strategy Builder's incumbent bar is the fixed value 0.0, advisors/strategy_builder_engine.py:899, so there only its candidates and SPY baseline move).
- The backtest client sends `slippage_percent` 0.001 when the caller gives none, with every other request field unchanged (AC-1).
- A caller that passes `slippage_percent` explicitly, 0.0 included, still sends exactly that value (AC-2).
- Every advisor surface's backtest requests, SPY baselines included, carry the same default (AC-3).
- The autotuner's simulated exit cost stays 0.5 percentage points and is no longer stated or checked as derived from the backtest default (AC-4).
- The calibration evidence sits beside the default where a reader of the constant finds it (AC-5).
- The content pins on the two edited files move only over the listed edits (AC-6).

## Acceptance criteria and scenarios

### AC-1: The backtest client sends 0.001 when no slippage is given
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

Negative coverage: N1 S-1.3 | N2 S-1.7 | N3 n/a: the default is a fixed module constant, never computed or read from a source that can be unavailable | N4 n/a: the client evaluates no permission and its auth headers are unchanged (S-1.4); no route is added or changed | N5 n/a: the client keeps no state between calls, and the default is fixed when `run_backtest` is defined at import (composer_backtest_client.py:277), so two concurrent calls send the same value | N6 S-1.5, S-1.6 | N7 S-1.4, S-1.8, S-1.9 | N8 n/a: a backtest is one request and holds no edit open

### AC-2: An explicit slippage value is sent exactly as given
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

Negative coverage: N1 S-2.2, S-2.3, S-2.4, S-2.5, S-2.6, S-2.7, S-2.8, S-2.9 | N2 n/a: the client adds no refusal of an explicit value; Composer's refusals reach the caller as in S-1.7 | N3 n/a: an explicit value is passed in, never computed | N4 n/a: no permission is evaluated | N5 n/a: no state is kept between calls | N6 S-2.10 | N7 S-2.1 | N8 n/a: one request, no edit held open

### AC-3: Every advisor surface backtests on the one default
Rule: When any advisor surface runs Composer backtests, every backtest request it sends, including each SPY baseline, incumbent, baseline, variant and candidate request in the same run, shall carry `slippage_percent` 0.001.

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

Negative coverage: N1 S-3.12 | N2 n/a: no surface adds a refusal; a Composer refusal is handled in the one client every surface calls, tested as S-1.7 under AC-1 | N3 n/a: no new computed or unavailable value is introduced; each surface's existing handling of an empty or failed backtest is unchanged (S-3.14) | N4 n/a: no route is added and every route's auth and CSRF hooks are unchanged | N5 n/a: the default is fixed when `run_backtest` is defined at import (composer_backtest_client.py:277), so concurrent runs in one process send the same value; every scenario asserts the sent body, never a patched constant | N6 S-3.13 | N7 S-3.14, S-3.15, S-3.16, S-3.17 | N8 n/a: no surface holds an edit open while it backtests

### AC-4: The simulated exit cost stays 0.5 percentage points and stands on its own basis
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

Negative coverage: N1 n/a: the constant takes no input | N2 n/a: nothing is refused | N3 n/a: a fixed constant is never unavailable; the page copy's existing unreadable-value case is unchanged | N4 n/a: no permission is involved | N5 n/a: a constant read has no concurrency | N6 n/a: the constant reads no dependency | N7 S-4.2, S-4.3, S-4.4, S-4.5, S-4.6, S-4.7, S-4.8 | N8 n/a: nothing is edited

### AC-5: The calibration evidence sits beside the default
Rule: When a reader opens the definition of the backtest slippage default or its generated documentation page, they shall find the value 0.001, its unit (a fraction of each traded dollar) and the calibration evidence behind it, and no statement there shall give 0.005 as the default or call the slippage default Composer's UI default.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-5.1 | B | the statement at the default's definition (advisors/composer_backtest_client.py, the constant at :80) | it is read | it names: 8 live symphonies; Composer backtest v2, broker alpaca; realized deposit-adjusted daily returns as the comparison; 87 trading days since each tree's last edit (46 for one symphony), 2026-07-15 to 2026-11-13; the settings 0, 0.001 and 0.005; the simple mean of the eight per-symphony gaps (live minus backtest) of about -6.5, +0.9 and +30 bps a day; daily correlation 0.976 to 0.996; the per-symphony gap at 0.001 of -11.4 to +9.3 bps a day; the measurement date 2026-11-16; and the conclusion that 0.001 is closest to live |
| S-5.2 | B | docs/generated/advisors_composer_backtest_client.md (signature line 23, parameter table line 34, constants table line 97) | it is read | each gives 0.001 and the page carries the evidence summary of S-5.1 |
| S-5.3 | N7 | the client module and its generated page | they are searched for the default | no line gives 0.005 as the default; the `run_backtest` docstring's unit example reads 0.001 as 0.1% |
| S-5.4 | N7 | the statement that the default parameters match the Composer UI defaults (composer_backtest_client.py:78) | it is read | it no longer covers the slippage default |
| S-5.5 | N1 | the evidence statements of S-5.1 and S-5.2 | they are searched | they carry no machine-local or untracked file path, and each number equals this plan's Calibration evidence table, the means taken from its computed rows |

Negative coverage: N1 S-5.5 | N2 n/a: documentation refuses nothing | N3 n/a: every figure is a recorded measurement, none computed at runtime | N4 n/a: no permission is involved | N5 n/a: static text | N6 n/a: static text reads no dependency | N7 S-5.3, S-5.4 | N8 n/a: nothing is edited at runtime

### AC-6: Each content pin on the two edited files moves only over this plan's listed edits
Rule: When the change is complete, `advisors/composer_backtest_client.py` and `autotuner.py` shall differ from the ref by exactly the listed edits below, and each content pin on those files shall be re-taken over those edits only, while every other file those pins hold keeps its ref digest.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-6.1 | B | the change is complete | each of the two files is compared with the ref, hunk by hunk | its hunks are exactly its listed edits, and each of the four pins holds the new digest with a comment naming this plan |
| S-6.2 | N1 | one byte changed outside the listed edits in each of the two files | the pin tests run | each pin on that file fails |
| S-6.3 | N7 | the captured fixtures pinned beside the client (tests/fixtures/composer/backtest_inline_v1.json, backtest_response_v1.json) | the client pin test runs | their digests are the ref's, not re-taken |
| S-6.4 | N7 | the other files the same pin tests hold (`math_engine.py`, `regime_classifier.py`, `synthetic_history.py`, `migrations/027_regime_label_cache.sql`, `alpha_bot_execution.py`) | the pin tests run | their digests and scope digests are the ref's, not re-taken |
| S-6.5 | N7 | the change is complete | the repository-root pin tests (tests/test_regime_label_protected_files.py, tests/test_hygiene_protected_surfaces.py, tests/test_preconditions_replay_protected_scope.py, tests/test_mdd_window_truth_zero_diff_guard.py) and tests/ai_advisor/test_advisor_backtest_client.py run | all pass |

Negative coverage: N1 S-6.2 | N2 n/a: a pin refuses nothing at runtime; it fails a test | N3 n/a: digests are recorded values, never unavailable | N4 n/a: no permission is involved | N5 n/a: static file content | N6 n/a: the pins read only repository files | N7 S-6.3, S-6.4, S-6.5 | N8 n/a: nothing is edited at runtime

## Rule-by-entry-point matrix

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

## Narrowed mappings

R-1: The reported problem is fixed in the app, not only reported; maps to AC-1, AC-3; left out: only the backtest slippage problem of G-2 to G-5; other reported app problems are not in this plan
R-4: At 0.1% the backtests track live closely, and that finding is the chosen basis; maps to AC-1, AC-5; left out: "within about 0.02% a day" holds for the cross-symphony mean only (computed from the rows: +0.9 bps a day, not the stated +2.1); per symphony the gap at 0.001 ranges from -11.4 to +9.3 bps a day (Known-Unconfirmed, Q-5)
R-8: The calibration evidence (sample, method, window, settings, gaps, correlation, compounded example, conclusion) is inlined in the plan; maps to AC-5; left out: the per-symphony rows and the compounded example are in this plan's Calibration evidence table; the code comment carries the summary listed in S-5.1

## Open questions

| Q-n | Question | Blocks (AC ids or "plan") | Proposed default | Answer |
|---|---|---|---|---|
| Q-1 | Does the tenure review's backtest leg move to the new default with the advisor gates? It passes no slippage today, so it moves unless excluded, and its watch verdicts can change (two of the eight measured symphonies would read watch). | AC-3 | yes: it is the one consumer that compares a backtest with realized returns, the comparison the calibration measures | [PM-ASSUMED] yes: the tenure review is a consumer and takes the same default |
| Q-2 | Does the tenure review's 10 percentage point watch threshold stay 10 on the new basis? | AC-3 | yes, unchanged; re-tuning it is NB-7 | [PM-ASSUMED] yes: the 10 percentage point threshold stays |
| Q-3 | Do candidates already incubating at deploy get special handling for their mixed basis and their admission baseline taken at 0.5%? | AC-3 | no; their rows stay as written (S-3.16) and the mix is recorded as a standing risk (NB-8) | [PM-ASSUMED] no special handling; the mixed-basis period is an accepted limitation |
| Q-4 | removed: settled by D-3; the project's Sensitive definition covers anything that drives a real-money keep, retire or trade decision, and the tenure watch verdict reads the backtest leg (advisors/tenure_review.py:1187-1196) | none | none | [PM-ASSUMED] Sensitive: money, accepted (D-3) |
| Q-5 | The default 0.001 is conservative on the cross-symphony mean but not for three of the eight symphonies; about 0.0027 would be conservative for all eight at a mean gap of about +13 bps a day (interpolated). Which value? | AC-1, AC-5 | 0.001, the intake's value | [PM-ASSUMED] 0.001: the goal is the setting closest to live, not a one-sided bound |
| Q-6 | Two of the content pins on `autotuner.py` say to stop and ask the PM before a re-take. Does approving this plan authorize re-taking the four pins of AC-6 over the listed edits? | AC-6 | yes, over the listed edits only, each re-take proven by S-6.1 | [PM-ASSUMED] yes: approval authorizes re-taking the four content pins over the listed edits only |

Sensitive: money (AC-1, AC-3). qa writes hidden scenarios for the named criteria after approval; they are not shown to the builders.
