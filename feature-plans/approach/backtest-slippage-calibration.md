# Approach: Backtest slippage default calibrated to live Composer results
Spec: feature-plans/backtest-slippage-calibration.md
Ref: AlphaBotPM@c3e12882141ae601f900fdc13e0c342462a739bb

## Architecture

Every file:line below is at AlphaBotPM@c3e12882, read in the worktree at `feat/backtest-slippage-calibration`@2490fda6. That head adds only the plan, its source and its audit to the ref (`git diff --stat c3e12882 2490fda6` lists three files under `feature-plans/`), so every production and test line cited here is the ref's line.

### Shape

Two production files change, both by text beside a constant: the client default value with its evidence statement, and the statement above the exit friction constant. No function body, signature, route, schema, scheduler slot or engine file changes. Two generated documentation pages change with them. Everything else in the slice is test work: the client request tests, the eleven entry points of the plan's matrix run against one Composer capture, the friction pin rewrite, and the content pins re-taken over exactly these edits.

### Production edit 1: `advisors/composer_backtest_client.py` (AC-1, AC-2, AC-3, AC-5)

At the ref the default is `_DEFAULT_SLIPPAGE_PERCENT: float = 0.005` (`advisors/composer_backtest_client.py:80`) under the comment "Default backtest parameters, match the Composer UI defaults" (`:78`), bound as the default of `run_backtest` when the function is defined (`:277`) and sent unchanged in the body (`:339`). `submit_backtest` is the same function object (`:454`).

The edit replaces `:78` and `:80` and inserts the evidence statement directly above `:80`, leaving `:79` (`_DEFAULT_CAPITAL`) and `:81-82` byte-identical. The text, each line under the 100 column limit and free of em dashes, en dashes and spaced hyphens:

```python
# Default backtest parameters. Capital, the fee flags, the broker and the version match the
# Composer UI defaults; the slippage default below is calibrated to live Composer results.
_DEFAULT_CAPITAL: float = 10_000.0
# Slippage per trade, a fraction of each traded dollar: 0.001 is 0.1%. Calibrated 2026-11-16
# against live Composer results: for 8 live symphonies, each current tree was backtested
# (Composer backtest v2, broker alpaca) at slippage 0, 0.001 and 0.005 and compared day by day
# with Composer's realized deposit-adjusted daily returns over the 87 trading days since each
# tree's last edit (46 for one symphony), 2026-07-15 to 2026-11-13. Daily correlation, live
# with backtest: 0.976 to 0.996. Simple mean of the eight per-symphony gaps (live minus
# backtest) at 0, 0.001 and 0.005: -6.45, +0.875 and +29.9 bps a day. Per symphony the gap
# runs from -11.4 to +9.3 bps a day at 0.001 and from +15.3 to +45.9 bps a day at 0.005, so
# 0.005 understated live results on these daily-rebalancing trees. 0.001 is the measured
# setting closest to live, slightly conservative on the mean.
_DEFAULT_SLIPPAGE_PERCENT: float = 0.001
```

The means are the plan's computed rows (plan Calibration evidence table: -6.45, +0.875, +29.9), which S-5.5 requires exactly; they read as "about -6.5, +0.9 and +30" in S-5.1's words. The PM's stated +2.1 appears nowhere. The per-symphony range at 0.005 (15.3 to 45.9, the table's last column) is the measured form of the understatement R-3 records. No line holds both "0.005" and "default", which is the testable form of S-5.3, and the `:78` statement no longer covers slippage (S-5.4).

The `run_backtest` docstring's unit line (`:304`, "Execution slippage fraction (0.005 = 0.5 %).") becomes "Execution slippage fraction (0.001 = 0.1 %)." with `:303` unchanged. Nothing else in the file moves: the body build (`:333-355`), retries (`:362-449`), timeout (`:70`) and parsing (`:140-243`) stay the ref's text, which the slice scope pin proves (Test infrastructure).

Explicit values (AC-2) need no code: the parameter is placed in the body as given (`:339`), the compact form serializes with `allow_nan=False` and returns the existing error result (`:349-353`). The client validates nothing (NB-5).

### Production edit 2: `autotuner.py` (AC-4)

The statement above `SIM_EXIT_FRICTION_PCT` (`autotuner.py:1433-1447`) derives the value from the client default (`:1438-1441`). It is replaced; the assignment (`:1448`, `SIM_EXIT_FRICTION_PCT: float = 0.5`) is unchanged. `autotuner.py` imports nothing from the client (no `composer_backtest_client` import in the file; the only mentions are this comment), so the value already stands alone and only the statement and its pin change. The replacement, 12 lines so the citation stays inside the 15 line window the existing check reads (`tests/autotuner/test_exit_friction_constant.py:143`):

```python
# Simulated-exit friction: a replay-only cost term subtracted at every triggered-exit
# accounting site below (_collect_sim_returns, _collect_sim_returns_dated, run_simulation),
# distinct from the deviation-dict `penalty` term. It models the cost of a Planet Stopper
# full-position exit in the replay and closes the gross-of-cost gap identified in
# docs/research/methodology-validation-2026-07.md. Value: 0.5 PERCENTAGE POINTS, in this
# file's percent-point convention (RETURN_PCT_TO_FRACTION, 100.0); a decimal-fraction value
# would apply 100x too little friction at these percent-point-scaled subtraction sites. It is
# a different cost from the per-trade slippage Composer backtests assume for daily
# rebalancing and does not follow the backtest client's default. Deliberately kept out of
# math_engine.py (the live engine's decision-math module) and out of
# OPTUNA_SEARCH_SPACE_KEYS / the per-trial params dict / os.environ: a fixed
# replay-environment parameter, never tunable by the optimizer.
SIM_EXIT_FRICTION_PCT: float = 0.5
```

It carries none of the four tokens the S-4.5 check refuses (`_DEFAULT_SLIPPAGE_PERCENT`, `composer_backtest_client`, "derived from", "0.005") and both phrases S-4.8 requires ("methodology-validation", "full-position exit"). The three subtraction sites (`autotuner.py:1530`, `:1866`, `:2142`), the page copy reader (`app.py:2740-2744`) and the guard-alpha and turnover routes (`app.py:6806`, `:6811`, `:6902`, `:8363`) read the unchanged constant and carry no diff.

### Documentation edits (AC-4, AC-5)

Made by the implementer in GREEN, because S-4.5 and S-5.2 test them; the slice-tail doc-gen keeps them and adds the log entry.
- `docs/generated/advisors_composer_backtest_client.md:23`: the signature's `slippage_percent=0.005` becomes `slippage_percent=0.001`.
- `:34`: the parameter row reads "Execution slippage fraction (`0.001` = 0.1%)."
- `:97`: the constants row reads `` | `_DEFAULT_SLIPPAGE_PERCENT` | `0.001` | Default execution slippage fraction, calibrated to live Composer results (see Slippage calibration). | ``.
- A new `### Slippage calibration` subsection after the constants table (after `:99`) carries the same summary as the code statement, in the same numbers and with no file path. No line of the page holds both "0.005" and "default".
- `docs/generated/autotuner.md:72`: the "Units derivation (Finding E)" paragraph becomes "**Units (Finding E):** 0.5 percentage points in this file's percent-point convention (`RETURN_PCT_TO_FRACTION`, `100.0`); a decimal-fraction value would apply 100x too little friction at the percent-point-scaled subtraction sites. The value models the cost of a full-position exit in the replay (`docs/research/methodology-validation-2026-07.md`), a different cost from the per-trade slippage Composer backtests assume, and does not follow the backtest client's default."
- `docs/generated/autotuner.md:452`: the constants row reads "Replay-only cost of a full-position exit, PERCENTAGE POINTS per round-trip exit (`DE-EXIT-FRICTION-REALIZED-001`, 2026-07-24); see `docs/research/methodology-validation-2026-07.md`. NOT in the Optuna search space; see the Exit-Friction Constant section above."

`docs/generated/INDEX.md`, the `DECISIONS.md` entry (citing DE-EXIT-FRICTION-REALIZED-001 at `DECISIONS.md:9223`, DE-SB-CULL-001 at `:2343`, DE-MATH-R2-001 at `:6636`), the drafted `.claude/CLAUDE.md` Key Files rows and the NB-16 backlog line are the doc-gen's at the slice tail (plan Docs to Update).

### Why every surface moves with one constant (AC-3)

A repository search (`git grep -n -E "run_backtest|submit_backtest|backtest_fn|slippage" -- '*.py' ':!tests'`) finds sixteen production call sites, none passing `slippage_percent`. Each reaches the one default bound at `advisors/composer_backtest_client.py:277`: the engines import the function object at module scope (`advisors/strategy_builder_engine.py:34`, `advisors/asset_swap_engine.py:73`, `advisors/logic_change_engine.py:74`, `advisors/incubation.py:36`), the Frontrunner Builder imports it lazily (`advisors/frontrunner_builder.py:2191`), the tenure runner calls it as a module attribute (`advisors/tenure_review_runner.py:179`), and the compiler receives it as `backtest_fn` (`advisors/strategy_builder_engine.py:442`, called at `advisors/plan_tree_compiler.py:517`). The SPY baselines go through the same function (`advisors/strategy_builder_engine.py:1068`, `advisors/asset_swap_engine.py:824`, `advisors/logic_change_engine.py:555`, `advisors/incubation.py:280`). `advisors/backtest_gate_engine.py` sends no request. No caller changes, so DC-1's sibling search is a no-diff check, and the gates (BHY FDR, PBO veto, SPY rule, Calmar acceptance) carry no diff.

The consequence for tests: a test that patches `run_backtest` on an engine module (`tests/_asset_swap_llm_support.py:141`, `tests/advisors/logic_change_http_support.py:208`, `tests/advisors/test_incubation_tick.py:110`) or on the client module (`tests/advisors/test_frontrunner_gate_wiring.py:274`) never sees the default. Every AC-3 test keeps the real `run_backtest` and captures at the `requests` boundary.

### Test infrastructure

Adopted, after a search of `tests/` for request capture (`requests.post`, `composer_backtest_client.requests`, `ComposerRouter`, `body_json`):
- `tests/advisors/_composer_http_router.py:295` `ComposerRouter`: replaces every `requests` verb, `Session.request` and `time.sleep` (`:411-440`), records each request (`of_kind("backtest")`, `:333`), decodes either body form (`Request.body_json`, `:266`), answers the backtest POST from the captured tenure record by default (`:317`) and takes a scripted handler (`backtest_handler(request, index)`, `set_backtest`, `:350`). It is the capture for every AC-3 world. A request to a non-Composer URL is recorded as a violation and answered 404, so a hidden live call fails the test.
- `tests/ai_advisor/test_advisor_backtest_client.py:603` `_post_capturing` and `:75` `_make_mock_response`: the client unit tests' own capture, kept for AC-1 and AC-2.
- `tests/ai_advisor/test_advisor_backtest_client.py:1153` `_comment_lines_above`: reads the statement above a constant (S-5.1, S-5.3, S-5.4).
- `tests/test_hygiene_protected_surfaces.py`: `Selector`, `_union` (`:121`), `cut_text` (`:145`), `scope_digest` (`:160`), `_read` (`:202`), imported, not copied, as `tests/test_preconditions_replay_protected_scope.py:33-41` does; and `_module_assignments_with_comments` from `tests/test_preconditions_replay_protected_scope.py:60`.
- Entry-point harnesses: `tests/_sb_generation_harness.py:244` `install_chain_seams`; `tests/_asset_swap_llm_support.py:128` `post_evaluate`; `tests/advisors/logic_change_http_support.py:202-245`; `tests/advisors/test_frontrunner_gate_wiring.py:73`, `:196`, `:213`; `tests/advisors/test_incubation_tick.py:40`, `:53`; `tests/app/_tenure_review_job_builders.py:474`, `:516`, `:716` with the drivers of `tests/app/test_tenure_review_end_to_end.py:156-174`.

Two keyword additions to shared harnesses, each defaulting to today's behaviour:
- `install_chain_seams(monkeypatch, fake, atlas=None, *, composer_router=None)` (`tests/_sb_generation_harness.py:244`): when a router is given, the ExitStack does not patch `requests.post` (`:261-263`), so the router's `monkeypatch` patch is the only one and nothing restores a MagicMock into `requests.post` at teardown.
- `post_evaluate(..., real_backtest=False)` (`tests/_asset_swap_llm_support.py:128`): when true, the ExitStack skips the `run_backtest` patch (`:140-142`).
The logic change harness needs none: after `install_route_world` the test rebinds `lce.run_backtest` to `advisors.composer_backtest_client.run_backtest` with `monkeypatch`.

New, each justified by an absence the search found:
- `tests/advisors/_slippage_surface_worlds.py`: no helper runs every matrix entry point against one capture. It holds `CALIBRATED_SLIPPAGE = 0.001` (the plan's AC-1 value, written once; never read from `_DEFAULT_SLIPPAGE_PERCENT`, which would pass vacuously while the constant stays 0.005), `block_atlas(monkeypatch)` patching `advisors.community_strats.load_community_strategies` and `advisors.frontrunner_signals.load_frontrunner_signals` as `tests/advisors/test_frontrunner_gate_wiring.py:102-122` does (a live Atlas read is a paid read), and one runner per matrix column returning `SurfaceRun(router, backtests, outcome)`. Each runner installs a `ComposerRouter` (the tenure runners take it from `jb.install_world`), applies `block_atlas`, drives its entry point as named in the census, and returns the router's backtest requests. `outcome` projects the entry point's gate results to stable fields (verdicts, rejection reasons, persisted statuses), with run ids and timestamps dropped.
- `tests/advisors/test_backtest_slippage_surfaces.py`: the AC-3 entry point tests. Each asserts first that its world sent at least one backtest, then that every body's `slippage_percent` equals `CALIBRATED_SLIPPAGE`, then that `router.violations` is empty.
- `tests/fixtures/backtest_slippage_calibration/capture_request_sequences.py`, `request_sequences.json` and `PROVENANCE.md`: the S-3.14 goldens. The script runs every runner and records, per entry point, each backtest request's URL, body form (`json` or `data`) and body with `slippage_percent` removed and every `"id"` value inside `symphony.raw_value` replaced by its order of first appearance in that sequence (the constructors mint uuid4 ids), plus `outcome`. It refuses to run unless `git diff --quiet c3e12882141ae601f900fdc13e0c342462a739bb -- . ':(exclude)tests' ':(exclude)feature-plans'` succeeds, the guard of `tests/fixtures/preconditions_replay/capture_goldens.py:15-16`, so the goldens are the ref's behaviour.
- `tests/test_backtest_slippage_calibration_protected_scope.py`: the slice scope pin, the precedent of `tests/test_preconditions_replay_protected_scope.py`. Selectors: for the client, `_module_assignments_with_comments("_DEFAULT_CAPITAL", "_DEFAULT_SLIPPAGE_PERCENT")` united with a local `_function_docstring("run_backtest")` (the docstring `Expr` of the module-level function); for `autotuner.py`, `_module_assignments_with_comments("SIM_EXIT_FRICTION_PCT")`. Each scope digest is computed from `git show c3e12882:<path>`, LF-normalized and cut, and equals the file at RED. Because the cuts take whole statements, three exact pins close the gap: `ast.unparse` of the `_DEFAULT_CAPITAL` statement is `_DEFAULT_CAPITAL: float = 10000.0`, of the `SIM_EXIT_FRICTION_PCT` statement `SIM_EXIT_FRICTION_PCT: float = 0.5`, and the `run_backtest` docstring with the one line after `slippage_percent:` removed hashes to the ref's digest of the same removal.

### Content pins and their re-take order (AC-6)

The four pins on the two files at the ref: the client full-file digest (`tests/ai_advisor/test_advisor_backtest_client.py:1274-1276`, asserted at `:1360-1366`); the `autotuner.py` full-file digest (`tests/test_regime_label_protected_files.py:40`); the `autotuner.py` scope digest under the hygiene cut (`tests/test_hygiene_protected_surfaces.py:197`, cut `:178-181`, which does not reach the statement above `SIM_EXIT_FRICTION_PCT`); and the `autotuner.py` scope digest under the preconditions cut (`tests/test_preconditions_replay_protected_scope.py:116`). All four move with the listed edits. A search of `tests/` for `sha256` or `hexdigest` beside either file name finds no fifth; `tests/test_mdd_window_truth_zero_diff_guard.py:68` pins only the engine files.

Order, the precedent of commits f57d2e0d (GREEN) then 60bd4848 (re-take): the slice scope pin is written at RED and passes at RED; GREEN makes it pass with the new text in place (it fails only if a byte moves outside the listed edits); then the closer re-takes the four digests in a test commit, each with the comment form already used at `tests/test_regime_label_protected_files.py:39`: "re-taken at <GREEN sha> for backtest-slippage-calibration (calibrated backtest slippage default); tests/test_backtest_slippage_calibration_protected_scope.py proves the change stays inside its listed edits." The fixture digests beside the client pin (`:1278-1283`) and every other entry of the three pin dictionaries keep their ref values, which the slice pin asserts (S-6.3, S-6.4). The implementer's handoff names the four pin tests as expected to fail at GREEN and closed by the closer.

### Unchanged by construction

`alpha_bot_execution.py` and `math_engine.py` (NB-14, pinned at `tests/test_mdd_window_truth_zero_diff_guard.py:68`); `app.py`; every caller and gate module named above; `advisors/tenure_review.py` with its threshold (`:49`) and rule (`:1187-1196`); `database.py` with the incubation write (`:5180`); migrations, templates and static files; the captured fixtures (NB-10); the research and audit records (NB-11).

## Criterion Thread

| AC | R items | Design element (file:line, or "new: <reason and search>") | Test seam (test file:line; fixture and provenance; harness pattern) | Cycle |
|---|---|---|---|---|
| AC-1 | R-1, R-2, R-4, R-6, R-9, R-11, R-16, R-22, R-24, R-28 | advisors/composer_backtest_client.py:80 (value 0.001), bound as the default at :277, placed in the body at :339 | tests/ai_advisor/test_advisor_backtest_client.py:416 (new class after `TestRequestContract`); `response_body` (:107) from tests/fixtures/composer/backtest_inline_v1.json, captured-from-producer; `_post_capturing` (:603) with `patch("requests.post")`; engine files by tests/test_mdd_window_truth_zero_diff_guard.py:68 | C1 |
| AC-2 | R-15, R-23 | advisors/composer_backtest_client.py:277 and :339 (the parameter sent as given), :349-353 (compact refusal), unchanged | tests/ai_advisor/test_advisor_backtest_client.py:619 (new class beside `TestDatedCompactRequest`); `minimal_raw_value` (:122), `response_body` (:107), captured; `_post_capturing` (:603), retries with `patch("time.sleep")` as at :496 | C1 |
| AC-3 | R-1, R-5, R-7, R-10, R-20, R-25, R-27, R-28 | advisors/composer_backtest_client.py:277, reached unchanged by the sixteen call sites listed under Architecture (advisors/strategy_builder_engine.py:442, :1068, :1082; advisors/asset_swap_engine.py:824, :924, :931, :1516; advisors/logic_change_engine.py:555, :648, :656, :967; advisors/frontrunner_builder.py:2197, :2201; advisors/incubation.py:280, :304; advisors/tenure_review_runner.py:179) | new: tests/advisors/test_backtest_slippage_surfaces.py with tests/advisors/_slippage_surface_worlds.py (no existing helper drives every matrix entry point against one capture; search under Test infrastructure); `ComposerRouter` (tests/advisors/_composer_http_router.py:295) serving the captured tenure backtest record; S-3.14 goldens captured at RED; S-3.15 in tests/advisors/test_tenure_review_verdict.py:121; S-3.16 in tests/advisors/test_incubation_tick.py:123 | C1 |
| AC-4 | R-9, R-13, R-14, R-17, R-28 | autotuner.py:1433-1447 (statement replaced), :1448 unchanged; docs/generated/autotuner.md:72, :452 | tests/autotuner/test_exit_friction_constant.py:76 and :120 (rewritten), :163, :173, :211 (kept); tests/ui/test_page_copy_performance_history.py:272 (new unpatched case); tests/autotuner/test_exit_friction_three_site_application.py:136; tests/app/test_guard_alpha_summary_friction_aware.py:161; tests/app/test_exit_turnover_route.py:71; reference text from `git show c3e12882` | C1 |
| AC-5 | R-3, R-4, R-8, R-12 | advisors/composer_backtest_client.py:78-80 (statement and evidence), :304 (docstring unit line); docs/generated/advisors_composer_backtest_client.md:23, :34, :97 and a new subsection after :99 | tests/ai_advisor/test_advisor_backtest_client.py:1162 (new class beside `TestStatedWaitBound`, module-text pattern :1185-1197, `_comment_lines_above` :1153); fixture: the plan's Calibration evidence table (PM live measurement 2026-11-16, read-only, recorded in the plan) | C1 |
| AC-6 | R-14, R-16 | tests/ai_advisor/test_advisor_backtest_client.py:1274-1276; tests/test_regime_label_protected_files.py:40; tests/test_hygiene_protected_surfaces.py:197; tests/test_preconditions_replay_protected_scope.py:116 (re-taken after GREEN) | new: tests/test_backtest_slippage_calibration_protected_scope.py (the slice scope pin; precedent tests/test_preconditions_replay_protected_scope.py, whose cut helpers it imports); reference texts from `git show c3e12882:<path>`, LF-normalized | C1 |

## Execution Model

One cycle, C1. The client default and the friction pin cannot land on separate heads: at the ref the friction check computes its expected value from the client default (`tests/autotuner/test_exit_friction_constant.py:96`), so a head that moves the default to 0.001 and leaves that check fails it (0.001 times 100 is 0.1, not 0.5). AC-6's scope pin and S-6.1 also need both files' edits on one head. The production diff is two statements and five documentation lines; the work is the tests.

- C1 (AC-1 to AC-6), one round, two RED batches:
  - RED batch A, `test-writer`, the closer: AC-1, AC-2, AC-4, AC-5, AC-6. Files: tests/ai_advisor/test_advisor_backtest_client.py, tests/autotuner/test_exit_friction_constant.py, tests/ui/test_page_copy_performance_history.py, and the new tests/test_backtest_slippage_calibration_protected_scope.py. After GREEN it re-takes the four pins (Architecture, Content pins), runs sufficiency, merges batch B's coverage rows into the cycle's record and sends the one cycle-complete.
  - RED batch B, `test-writer`: AC-3. Files: the new tests/advisors/_slippage_surface_worlds.py and tests/advisors/test_backtest_slippage_surfaces.py, the goldens folder, the two harness keywords, and the new tests in tests/advisors/test_incubation_tick.py. It captures the S-3.14 goldens on its RED commit, before GREEN, and hands its batch coverage record to the closer.
  - Two authors because AC-3's eleven worlds are most of the test work and touch no file batch A touches; the split halves the RED wall-clock without a shared file.
  - GREEN: `implementer`, reading both RED handoffs, which carry the exact statement texts and documentation lines above.
  - No `quant-test-writer`: no formula, threshold or math-layer computation changes; `autotuner.py` changes only a comment.
- Review, dispatched by the PM at cycle-complete against one head, in parallel:
  - `qa`, and one `qa` dispatch in hidden mode because the plan is Sensitive (money, D-3).
  - `code-reviewer`, because the diff changes code files.
  - `quant-code-reviewer`, because the default is the cost basis of every advisor gate (BHY FDR, PBO, the SPY rule, the Calmar acceptance) and of the tenure watch verdict, and the evidence statement carries statistics that must equal the plan's table.
  - `composer-alpaca-integration`, the domain specialist for the Composer client, because the diff changes a field of the Composer request body.
  - No `api-expert` or `pipelines-expert`: no route, job, slot or pipeline code changes; only tests drive them.
  - No `ux-expert` or capturer: no template, client script or style changes (NB-3).
  - No `*-integration-auditor`: one repository and one production surface.
  - One `doc-gen` at the slice tail, after every reviewer passes the head.
- Isolation follows the project's environment rules: the C1 worktree is its own checkout off the branch base below, never the primary checkout; kickoffs mandate `cd` plus `git rev-parse --show-toplevel --abbrev-ref HEAD` and `git rev-parse HEAD`, and copy `.env`. Both RED authors work in the C1 worktree and stage only their own paths by name (`git add <paths>`, `git commit -- <paths>`), never `git add -A` and never `git stash`; a commit refused on `index.lock` is retried. Pytest runs named files only, `-n0` or at most 4 workers, never `-n auto`, never `-o addopts=""`.

Team size: 9 = 2 test-writer + 1 implementer + 1 qa + 1 qa (hidden mode) + 1 code-reviewer + 1 quant-code-reviewer + 1 composer-alpaca-integration + 1 doc-gen

## Dispatch Ordering

| Cycle | Consumes (cycle: committed artifact) | Branch base |
|---|---|---|
| C1 | none (starts at once) | feat/backtest-slippage-calibration@2490fda6 |

## Per-Cycle Detail

### C1: AC-1, AC-2, AC-3, AC-4, AC-5, AC-6

Production files modified:
- advisors/composer_backtest_client.py: `:78` replaced, evidence statement inserted above `:80`, `:80` value 0.001, `:304` unit line.
- autotuner.py: `:1433-1447` replaced; `:1448` unchanged.

Documentation modified: docs/generated/advisors_composer_backtest_client.md `:23`, `:34`, `:97`, new subsection after `:99`; docs/generated/autotuner.md `:72`, `:452`.

Test files modified:
- tests/ai_advisor/test_advisor_backtest_client.py: new classes for AC-1 (after `:491`), AC-2 (after `:781`) and AC-5 (after `:1228`); the client digest at `:1274-1276` re-taken after GREEN.
- tests/autotuner/test_exit_friction_constant.py: module docstring `:1-18` and the two provenance tests `:76-117`, `:120-155` rewritten; a self-source check and the generated-page check added.
- tests/ui/test_page_copy_performance_history.py: one unpatched trading-cost render case beside `:272`.
- tests/advisors/test_incubation_tick.py: one new test beside `:123`.
- tests/_sb_generation_harness.py `:244` and tests/_asset_swap_llm_support.py `:128`: one keyword each, default unchanged.
- tests/test_regime_label_protected_files.py `:40`, tests/test_hygiene_protected_surfaces.py `:197`, tests/test_preconditions_replay_protected_scope.py `:116`: digests re-taken after GREEN with the comment named above.

Files added: tests/advisors/_slippage_surface_worlds.py; tests/advisors/test_backtest_slippage_surfaces.py; tests/test_backtest_slippage_calibration_protected_scope.py; tests/fixtures/backtest_slippage_calibration/capture_request_sequences.py, request_sequences.json, PROVENANCE.md.

Primitives imported: `ComposerRouter`, `Reply`, `error_reply` (tests/advisors/_composer_http_router.py:295, :193, :208); the cut helpers (tests/test_hygiene_protected_surfaces.py:121-202); `_module_assignments_with_comments` (tests/test_preconditions_replay_protected_scope.py:60); `_post_capturing`, `_make_mock_response`, `_comment_lines_above`, `_LF_DIGESTS`, `_lf_sha256` (tests/ai_advisor/test_advisor_backtest_client.py:603, :75, :1153, :1274, :1287); `_PINNED_HASHES`, `_PINNED_ENGINE_WITHOUT_REGIME_READ`, `_sha256_bytes` (tests/test_regime_label_protected_files.py:36, :50, :55); the entry-point harnesses listed under Test infrastructure.

First production uses: none. No production primitive is added.

## Per-Cycle Test Plan

C1 scope: unit tests on the client with `requests.post` captured; entry-point tests through the Flask test client and through each job's entry function with Composer captured by `ComposerRouter`; source and document text checks; content pins. No live backend: the PM's live check is under Merge Criteria.

Write-path census. The changed value is the body every backtest request carries; the entry points that send it are the matrix's eleven columns, and a search of every route, scheduler slot and `__main__` module finds no other:

| Entry point | Rules |
|---|---|
| Strategy Builder route | AC-3: `POST /ai-advisor/strategy-builder/run` (app.py:11921) to `propose_strategies`, which backtests through the compiler (advisors/strategy_builder_engine.py:442, advisors/plan_tree_compiler.py:517), the SPY baseline (:1068) and each candidate (:1082) |
| Strategy Builder weekly | AC-3: `run_weekly_build` (advisors/strategy_builder_scheduler.py:106, its `propose_strategies` call :166), run by `run_weekly_suggestions` (advisors/weekly_suggestions_scheduler.py:427) and by `python -m advisors.strategy_builder_scheduler` (:220) |
| Asset Swaps route | AC-3: `POST /ai-advisor/asset-swaps/evaluate` (app.py:11247): SPY (advisors/asset_swap_engine.py:824), baseline (:924), variant (:931) |
| Asset Swaps weekly | AC-3: `run_weekly_asset_swap_suggestions` (advisors/weekly_suggestions_scheduler.py:326, `suggest_swaps` call :398): baseline returns (advisors/asset_swap_engine.py:1516), SPY, baseline and variant |
| Logic Changes route | AC-3: `POST /ai-advisor/logic-changes/evaluate` (app.py:11602): SPY (advisors/logic_change_engine.py:555), baseline (:648), variant (:656) |
| Logic Changes weekly | AC-3: `run_weekly_logic_change_suggestions` (advisors/weekly_suggestions_scheduler.py:122, `suggest_logic_changes` call :153): baseline returns (advisors/logic_change_engine.py:967), SPY, baseline and variant |
| Frontrunner route | AC-3: `POST /ai-advisor/frontrunner-builder/run` (app.py:12205), submitted to `_FRONTRUNNER_BUILD_EXECUTOR` (app.py:765, submit :12279), `run_frontrunner_build` (advisors/frontrunner_builder.py:1300): incumbent (:2197) and candidate (:2201) |
| Frontrunner weekly | AC-3: `run_weekly_build` calling `run_frontrunner_build` (advisors/strategy_builder_scheduler.py:210) |
| Incubation tick | AC-3: the 03:30 slot (app.py:3096) to `_run_incubation_tick` (app.py:2124) and `run_incubation_tick` (advisors/incubation.py:238): SPY (:280) and each candidate (:304) |
| Tenure review route | AC-3: `POST /ai-advisor/retirement/review/run` (app.py:12573) on a thread to `_tenure_review_job` (app.py:2812), `run_tenure_review` (advisors/tenure_review_runner.py:298), the dated compact backtest (:179-188) |
| Tenure review tick | AC-3: the 03:45 slot (app.py:3101) to `_run_retirement_recommender_tick` (app.py:2972), stage 1 `_tenure_review_job("nightly", owner)` (app.py:2967) |

`POST /ai-advisor/retirement/run` (app.py:12517) runs the recommender, which reads series by GET and sends no backtest. The plan's AC-2 matrix row is n/a at every column (no production caller passes slippage), so AC-2 is tested at the client alone.

Fixture keys:
- FX-C: the client unit capture. `response_body` (tests/ai_advisor/test_advisor_backtest_client.py:107) from tests/fixtures/composer/backtest_inline_v1.json, captured-from-producer and pinned at `:1278`; `minimal_raw_value` (:122), a test-stated tree whose content no assertion reads; `_post_capturing` (:603). The expected slippage is the plan's literal 0.001 (`CALIBRATED_SLIPPAGE`), never read from the module.
- FX-C3: tests/fixtures/composer/backtest_response_v1.json, captured-from-producer, pinned at `:1281`.
- FX-R: `ComposerRouter` with its default backtest reply, the captured record tests/fixtures/composer/tenure-review/case5_backtest_dated_compact_nOyb55RMGVCKPiYXv7TI.json (provenance in that folder's README.md). Every assertion reads the request the client built, never the reply.
- FX-W: the world of the named runner in tests/advisors/_slippage_surface_worlds.py, each built from an existing harness:
  - Strategy Builder route: `install_chain_seams(..., composer_router=router)` (tests/_sb_generation_harness.py:244) with `fake_with_captured_page()` serving `tool_reply_with_plans(conforming_diversify_plans())` (two plans, two candidates; captured model replies under tests/fixtures/anthropic), `build_reasoning_context` patched and `symphony_id` in the body as at tests/app/test_sb_route_reasoning_provenance.py:189-218, the `client` fixture of tests/app/test_sb_backtest_unavailable_route.py:54.
  - Strategy Builder weekly: the same seams, `_already_ran_this_week` patched False (tests/advisors/test_strategy_builder_c5_scheduler_dualmode.py:66), one queued reply per objective, `run_frontrunner_build` patched to a no-op.
  - Asset Swaps route: `post_evaluate(client, fake, explicit_pair_body(), real_backtest=True)` (tests/_asset_swap_llm_support.py:108, :128) with `held_tree()` (:62, the captured score fixture with one ticker renamed, labelled there).
  - Asset Swaps weekly: `run_weekly_asset_swap_suggestions` with one live symphony in `database.load_state`, the score fetch returning `held_tree()`, the asset swap model seam on a fake serving `swap_tool_reply()` (:45, captured), the lens cache as at tests/advisors/test_weekly_asset_swap_suggestions_loop.py:843-909.
  - Logic Changes route: `install_route_world` (tests/advisors/logic_change_http_support.py:214) with `new_fake()` serving `reply_with_tool_edit(real_edit())` (:181, :159, :115), then `lce.run_backtest` rebound to the real function, `post_evaluate()` (:230).
  - Logic Changes weekly: `install_engine_world` (:202) with the same reply and rebinding, `run_weekly_logic_change_suggestions` with one live symphony and the score fetch returning `load_tree()` (:97).
  - Frontrunner route: the route with `symphony_ids` (tests/app/test_frontrunner_builder_route.py:459), `ANTHROPIC_API_KEY` set (:89), `app._FRONTRUNNER_BUILD_EXECUTOR` replaced by a stand-in whose `submit` runs the callable inline, `symphony_logic.fetch_symphony_score` returning the constructor-built incumbent of tests/advisors/test_frontrunner_gate_wiring.py:196 (synthetic, built by `symphony_schema`), `_build_client` returning `_mocked_fable_overlay_client()` (:213).
  - Frontrunner weekly: `run_weekly_build` with `_already_ran_this_week` False, `propose_strategies` stubbed as at tests/advisors/test_strategy_builder_c5_scheduler_dualmode.py:72, `database.load_state` naming the one incumbent, the same frontrunner seams.
  - Incubation tick: the scratch database of tests/advisors/test_incubation_tick.py:40, one candidate admitted by `_admit` (:53, a test-stated tree), `run_incubation_tick()`.
  - Tenure review route and tick: `jb.block_live_calls`, `jb.use_scratch_db`, `jb.isolate_env`, `jb.freeze_app_clock` (a Tuesday 03:45 ET, market closed) and the explainer stand-in as the `world` fixture builds them (tests/app/test_tenure_review_end_to_end.py:156-161), `jb.install_world` (tests/app/_tenure_review_job_builders.py:716) serving the captured tenure records, `run_on_demand` (:170) or `run_tick` (:164), `jb.assert_no_unserved_requests` at teardown (:516).
- FX-G: tests/fixtures/backtest_slippage_calibration/request_sequences.json, captured from the FX-W runners on batch B's RED commit by the guarded script; `PROVENANCE.md` records that SHA and the empty `git diff` output. Compared as parsed JSON, so line endings cannot differ.
- FX-E: the plan's Calibration evidence table (the eight per-symphony rows and the computed means), the PM's read-only live measurement of 2026-11-16 recorded in the plan; the raw files are not tracked.
- FX-REF: reference texts from `git show c3e12882141ae601f900fdc13e0c342462a739bb:<path>`, LF-normalized, digests typed into the test with that provenance in a comment.

Harness rules for every new test:
- An AC-3 test asserts the count of backtest requests is at least one before it asserts any value, so a world that sends nothing cannot pass.
- No test patches `_DEFAULT_SLIPPAGE_PERCENT` or reads it to compute an expected value.
- Every world calls `block_atlas` and installs exactly one `requests` replacement (the router).
- Retry scenarios run under the router's or the unit test's `time.sleep` replacement, never a real wait.
- Goldens are compared as parsed JSON with the masking the capture applied; nothing pops `sys.modules`.

### C1
| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-1.1 | AC-1 | unit, request captured | `run_backtest` (advisors/composer_backtest_client.py:270) | tests/ai_advisor/test_advisor_backtest_client.py:416 (new class after `TestRequestContract`, pattern :454-469) | FX-C |
| S-1.2 | AC-1 | unit, request captured | `submit_backtest` (advisors/composer_backtest_client.py:454) | tests/ai_advisor/test_advisor_backtest_client.py:416 (same class; also asserts `submit_backtest is run_backtest`) | FX-C |
| S-1.3 | AC-1 | unit, compact text decoded | `run_backtest` in the tenure call form (advisors/tenure_review_runner.py:179-188) | tests/ai_advisor/test_advisor_backtest_client.py:416 (same class, compact pattern :675-700) | FX-C with `start_date`, `end_date`, `compact_body=True` |
| S-1.4 | AC-1 | unit, full body, URL, headers and timeout | `run_backtest` (advisors/composer_backtest_client.py:270) | tests/ai_advisor/test_advisor_backtest_client.py:416 (same class; key set exact, headers as at :471-491, timeout equal to `_BACKTEST_REQUEST_TIMEOUT`, which the slice scope pin holds at the ref's text) | FX-C |
| S-1.5 | AC-1 | unit, scripted 429 then 200 | `run_backtest` (advisors/composer_backtest_client.py:270) | tests/ai_advisor/test_advisor_backtest_client.py:416 (same class, pattern :496-515 with `patch("time.sleep")`) | FX-C |
| S-1.6 | AC-1 | unit, 503 on every attempt, default retries | `run_backtest` (advisors/composer_backtest_client.py:270) | tests/ai_advisor/test_advisor_backtest_client.py:416 (same class, pattern :517-538, default `max_retries`) | FX-C with `_make_mock_response(503)` (:75) |
| S-1.7 | AC-1 | unit, 422 not retried | `run_backtest` (advisors/composer_backtest_client.py:270) | tests/ai_advisor/test_advisor_backtest_client.py:416 (same class, pattern :757-781) | FX-C with `_make_mock_response(422)` |
| S-1.8 | AC-1 | unit, parse equality | `run_backtest` (advisors/composer_backtest_client.py:270) | tests/ai_advisor/test_advisor_backtest_client.py:416 (same class: fields equal `_parse_response` (:222) applied to a copy of the same body; the scope pin holds `_parse_response` and `_extract_returns` at the ref's text) | FX-C3 |
| S-1.9 | AC-1 | content pin, existing | the engine files | tests/test_mdd_window_truth_zero_diff_guard.py:68 (existing, unchanged) | FX-REF (the guard's own pinned digests) |
| S-2.1 | AC-2 | unit, request captured | `run_backtest(tree, slippage_percent=0.005)` | tests/ai_advisor/test_advisor_backtest_client.py:619 (new class after `TestDatedCompactRequest`) | FX-C |
| S-2.2 | AC-2 | unit, JSON body | `run_backtest(tree, slippage_percent=0.0)` | tests/ai_advisor/test_advisor_backtest_client.py:619 (same class; asserts `== 0.0` and `!= 0.001`) | FX-C |
| S-2.3 | AC-2 | unit, compact text decoded | `run_backtest(..., slippage_percent=0.0, compact_body=True)` | tests/ai_advisor/test_advisor_backtest_client.py:619 (same class, compact pattern :675-700) | FX-C |
| S-2.4 | AC-2 | unit, request captured | `run_backtest(tree, slippage_percent=0.001)` | tests/ai_advisor/test_advisor_backtest_client.py:619 (same class) | FX-C |
| S-2.5 | AC-2 | unit, request captured | `run_backtest(tree, slippage_percent=0.0025)` | tests/ai_advisor/test_advisor_backtest_client.py:619 (same class) | FX-C |
| S-2.6 | AC-2 | unit, JSON body | `run_backtest(tree, slippage_percent=None)` | tests/ai_advisor/test_advisor_backtest_client.py:619 (same class; the captured `json` kwarg holds `None`) | FX-C |
| S-2.7 | AC-2 | unit, request captured | `run_backtest(tree, slippage_percent=-0.001)` | tests/ai_advisor/test_advisor_backtest_client.py:619 (same class) | FX-C |
| S-2.8 | AC-2 | unit, no request sent | `run_backtest(..., slippage_percent=nan, compact_body=True)` | tests/ai_advisor/test_advisor_backtest_client.py:716 (pattern of the capital NaN case; error equals "request body not serializable: ValueError") | FX-C |
| S-2.9 | AC-2 | unit, parametrized NaN, +inf, -inf, JSON body | `run_backtest(tree, slippage_percent=<value>)` | tests/ai_advisor/test_advisor_backtest_client.py:619 (same class; each captured value is the given one by `math.isnan` or equality, never 0.001) | FX-C |
| S-2.10 | AC-2 | unit, scripted 429 then 200 | `run_backtest(tree, slippage_percent=0.0)` | tests/ai_advisor/test_advisor_backtest_client.py:619 (same class, pattern :496-515) | FX-C |
| S-3.1 | AC-3 | route integration, bodies captured | Strategy Builder route | new: tests/advisors/test_backtest_slippage_surfaces.py (no test reads the backtest body through this route; harness tests/_sb_generation_harness.py:244) | FX-W Strategy Builder route, FX-R |
| S-3.2 | AC-3 | job integration | Strategy Builder weekly | new: tests/advisors/test_backtest_slippage_surfaces.py (pattern tests/advisors/test_strategy_builder_c5_scheduler_dualmode.py:99) | FX-W Strategy Builder weekly, FX-R |
| S-3.3 | AC-3 | route integration | Asset Swaps route | new: tests/advisors/test_backtest_slippage_surfaces.py (harness tests/_asset_swap_llm_support.py:128 with `real_backtest=True`) | FX-W Asset Swaps route, FX-R |
| S-3.4 | AC-3 | job integration | Asset Swaps weekly | new: tests/advisors/test_backtest_slippage_surfaces.py (pattern tests/advisors/test_weekly_asset_swap_suggestions_loop.py:843 without its `run_backtest` patch at :941) | FX-W Asset Swaps weekly, FX-R |
| S-3.5 | AC-3 | route integration | Logic Changes route | new: tests/advisors/test_backtest_slippage_surfaces.py (harness tests/advisors/logic_change_http_support.py:214, :230) | FX-W Logic Changes route, FX-R |
| S-3.6 | AC-3 | job integration | Logic Changes weekly | new: tests/advisors/test_backtest_slippage_surfaces.py (pattern tests/advisors/test_weekly_logic_change_suggestions_loop.py:97 with the real `suggest_logic_changes`) | FX-W Logic Changes weekly, FX-R |
| S-3.7 | AC-3 | route integration, inline executor | Frontrunner route | new: tests/advisors/test_backtest_slippage_surfaces.py (patterns tests/app/test_frontrunner_builder_route.py:459 and tests/advisors/test_frontrunner_gate_wiring.py:263) | FX-W Frontrunner route, FX-R |
| S-3.8 | AC-3 | job integration | Frontrunner weekly | new: tests/advisors/test_backtest_slippage_surfaces.py (pattern tests/advisors/test_strategy_builder_c5_scheduler_dualmode.py:72) | FX-W Frontrunner weekly, FX-R |
| S-3.9 | AC-3 | job integration | Incubation tick | new: tests/advisors/test_backtest_slippage_surfaces.py (world of tests/advisors/test_incubation_tick.py:40, :53 without the `run_backtest` patch at :110) | FX-W Incubation tick, FX-R |
| S-3.10 | AC-3 | route integration, compact body decoded | Tenure review route | new: tests/advisors/test_backtest_slippage_surfaces.py (pattern tests/app/test_tenure_review_end_to_end.py:585-605, `router.of_kind("backtest")[0].body_json()`) | FX-W Tenure review route |
| S-3.11 | AC-3 | job integration | Tenure review tick | new: tests/advisors/test_backtest_slippage_surfaces.py (pattern tests/app/test_tenure_review_end_to_end.py:784-797 with `run_tick`) | FX-W Tenure review tick |
| S-3.12 | AC-3 | route integration, scripted 400 then 200 | Strategy Builder route | new: tests/advisors/test_backtest_slippage_surfaces.py (router handler answers the first backtest 400 naming an in-tree ticker, the envelope form of tests/advisors/test_plan_tree_compiler_repair.py:254; asserts a retry body without that ticker carries 0.001) | FX-W Strategy Builder route, FX-R with a scripted `Reply(status=400, body=<text>)`, test-stated text as in the precedent |
| S-3.13 | AC-3 | route integration, SPY 503 on every attempt | Strategy Builder route | new: tests/advisors/test_backtest_slippage_surfaces.py (handler answers `error_reply(503)` (tests/advisors/_composer_http_router.py:208) when the tree's only asset is SPY; asserts every body 0.001, each candidate's `rejection_reason` is `below_spy_alpha` as read at tests/ai_advisor/test_ac7_route_json_rejection_reason.py:177) | FX-W Strategy Builder route, FX-R |
| S-3.14 | AC-3 | golden comparison, every runner | all eleven census entry points | new: tests/advisors/test_backtest_slippage_surfaces.py (masked sequence and outcome equal FX-G; capture guard precedent tests/fixtures/preconditions_replay/capture_goldens.py:15-16) | FX-W all runners, FX-G |
| S-3.15 | AC-3 | unit, existing | `tr.decide_verdict` (advisors/tenure_review.py:1187-1196) | tests/advisors/test_tenure_review_verdict.py:121 (existing parametrized `exactly_10_points` and `12_points` cases) and :147 (existing constant check), unchanged | test-stated evidence items of that file (:100-106) |
| S-3.16 | AC-3 | job integration, rows read back | Incubation tick | tests/advisors/test_incubation_tick.py:123 (new test beside the rerun test: rows appended by `append_incubation_day` before the tick for dates inside FX-R's reply, with values that differ from it, keep their values; dates not recorded are added; the admission `backtest_mdd_pct` is unchanged) | FX-W Incubation tick, FX-R |
| S-3.17 | AC-3 | structural scan plus a run on the tenure world | Tenure review tick | new: tests/advisors/test_backtest_slippage_surfaces.py (scan of every tracked production `*.py` from `git ls-files`, as at tests/ai_advisor/test_advisor_backtest_client.py:1328-1356, finds no `UPDATE` or `DELETE` on `advisor_observations`; then rows written by a first tick keep their ids and content after a second, pattern tests/app/test_tenure_review_end_to_end.py:784) | FX-W Tenure review tick |
| S-4.1 | AC-4 | unit, value read | `autotuner.SIM_EXIT_FRICTION_PCT` and the client default | new: tests/test_backtest_slippage_calibration_protected_scope.py (the slice's cross-file module; keeps tests/autotuner/test_exit_friction_constant.py free of any client reference for S-4.4) | the plan's AC-4 values 0.5 and 0.001 |
| S-4.2 | AC-4 | page render, unpatched constant | the Performance page copy (app.py:2740-2744) and the dashboard | tests/ui/test_page_copy_performance_history.py:272 (new case without the `friction` fixture (:233), reading F24 at 0.5; the dashboard's D25 at 0.5 is asserted by the existing tests/ui/test_page_copy_dashboard.py:445-447, named in the same coverage row) | the page copy record of that file (`friction_text`, :223) |
| S-4.3 | AC-4 | unit, existing | the three replay sites (autotuner.py:1530, :1866, :2142) | tests/autotuner/test_exit_friction_three_site_application.py:136 (existing; with :191 and :271 each subtracts the real constant, which S-4.1 holds at 0.5) | the existing tick fixtures of that file |
| S-4.4 | AC-4 | unit plus source read | tests/autotuner/test_exit_friction_constant.py | tests/autotuner/test_exit_friction_constant.py:76 (rewritten: expected value is the plan's 0.5; the units guard compares with the fraction form `0.5 / RETURN_PCT_TO_FRACTION`; a self-source check finds no `composer_backtest_client` import and no `_DEFAULT_SLIPPAGE_PERCENT` read in the module) | the plan's AC-4 value |
| S-4.5 | AC-4 | source and document text | autotuner.py:1433-1447, docs/generated/autotuner.md:72, :452 | tests/autotuner/test_exit_friction_constant.py:120 (new test beside it: the window, the `### Exit-Friction Constant` section up to the next `#### ` heading after the constant's own, and the constants row starting `` | `SIM_EXIT_FRICTION_PCT` `` each cite `methodology-validation-2026-07.md`, name the full-position exit, and hold none of `_DEFAULT_SLIPPAGE_PERCENT`, `composer_backtest_client`, "derived from", "0.005") | FX-REF for the section boundaries |
| S-4.6 | AC-4 | unit, existing | `OPTUNA_SEARCH_SPACE_KEYS`, autotuner.py source, math_engine.py source | tests/autotuner/test_exit_friction_constant.py:163 (existing; with :173 and :211, unchanged) | none beyond the source files |
| S-4.7 | AC-4 | route, existing goldens | `GET /api/guard-alpha-summary`, `GET /api/exit-turnover` | tests/app/test_guard_alpha_summary_friction_aware.py:161 (existing golden; with tests/app/test_exit_turnover_route.py:71, both reading the real constant) | the existing golden post-mortem fixture and seeded rows of those files |
| S-4.8 | AC-4 | source text | autotuner.py:1433-1447 | tests/autotuner/test_exit_friction_constant.py:120 (rewritten: requires "methodology-validation" and "full-position exit" in the window; no clause requires the client or its default) | FX-REF |
| S-5.1 | AC-5 | source text | advisors/composer_backtest_client.py:80 and the statement above it | tests/ai_advisor/test_advisor_backtest_client.py:1162 (new class after `TestStatedWaitBound`, `_comment_lines_above` :1153, pattern :1185-1197) | FX-E |
| S-5.2 | AC-5 | document text | docs/generated/advisors_composer_backtest_client.md:23, :34, :97 and the new subsection | tests/ai_advisor/test_advisor_backtest_client.py:1162 (same class) | FX-E |
| S-5.3 | AC-5 | source and document text | the client module and its page | tests/ai_advisor/test_advisor_backtest_client.py:1162 (same class: no line holds both "0.005" and "default", case-insensitive; the docstring unit line reads `(0.001 = 0.1 %)`) | none beyond the two files |
| S-5.4 | AC-5 | source text | advisors/composer_backtest_client.py:78 | tests/ai_advisor/test_advisor_backtest_client.py:1162 (same class: the statement above `_DEFAULT_CAPITAL` that names the Composer UI defaults also states the slippage default is calibrated, and the ref sentence "match the Composer UI defaults." is absent) | FX-REF |
| S-5.5 | AC-5 | source and document text | the S-5.1 and S-5.2 statements | tests/ai_advisor/test_advisor_backtest_client.py:1162 (same class: no drive letter, `/Users/`, `/home/`, `/tmp`, `scratchpad` or `.claude/`; each figure equals FX-E, the means computed from its eight rows) | FX-E |
| S-6.1 | AC-6 | content pin | both files and the four pins | new: tests/test_backtest_slippage_calibration_protected_scope.py (scope digests against FX-REF, the three exact statement pins, and a source check that each of the four re-taken entries carries the comment naming this plan; precedent tests/test_preconditions_replay_protected_scope.py:121-160) | FX-REF |
| S-6.2 | AC-6 | planted text, no file written | both files | new: tests/test_backtest_slippage_calibration_protected_scope.py (one byte planted outside the cuts moves the slice digest and each of the four pins' digests, computed on the planted text with each pin's own function; precedent :163-213) | FX-REF |
| S-6.3 | AC-6 | dictionary read | tests/ai_advisor/test_advisor_backtest_client.py:1278-1283 | new: tests/test_backtest_slippage_calibration_protected_scope.py (the two fixture entries of `_LF_DIGESTS` equal their ref values) | FX-REF (`git show c3e12882:tests/ai_advisor/test_advisor_backtest_client.py`) |
| S-6.4 | AC-6 | dictionary read | tests/test_regime_label_protected_files.py:36-52, tests/test_hygiene_protected_surfaces.py:193-199, tests/test_preconditions_replay_protected_scope.py:115-118 | new: tests/test_backtest_slippage_calibration_protected_scope.py (every entry other than `autotuner.py`, and `_PINNED_ENGINE_WITHOUT_REGIME_READ`, equals its ref value) | FX-REF |
| S-6.5 | AC-6 | the pin modules, existing | tests/test_preconditions_replay_protected_scope.py:123 | tests/test_preconditions_replay_protected_scope.py:123 (the coverage row names this test and lists tests/test_regime_label_protected_files.py, tests/test_hygiene_protected_surfaces.py, tests/test_mdd_window_truth_zero_diff_guard.py, tests/ai_advisor/test_advisor_backtest_client.py and the slice pin, with the closer's `-n0` run log path) | the pins' own digests |

## Per-Cycle Risk Register

| Cycle | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| C1 | An AC-3 test patches `run_backtest` and passes without the default ever being sent | Medium | High | Four harnesses do this today (tests/_asset_swap_llm_support.py:141, tests/advisors/logic_change_http_support.py:208, tests/advisors/test_incubation_tick.py:110, tests/advisors/test_frontrunner_gate_wiring.py:274). Every world captures at `requests` through `ComposerRouter` and asserts at least one backtest request before any value |
| C1 | The expected value is read from the constant, so the tests pass with 0.005 still in place | Medium | High | `CALIBRATED_SLIPPAGE = 0.001` is the plan's literal; no test reads or patches `_DEFAULT_SLIPPAGE_PERCENT`. The default is bound at definition (advisors/composer_backtest_client.py:277), so patching the constant would not move the default anyway |
| C1 | Two `requests.post` replacements stack and a MagicMock is restored into `requests` at teardown, leaking into later tests | Medium | Medium | `install_chain_seams` patches `requests.post` with `patch.object` (tests/_sb_generation_harness.py:261-263) while the router uses `monkeypatch` (tests/advisors/_composer_http_router.py:424-433). The new `composer_router` keyword removes the first patch whenever a router is used |
| C1 | A world reaches a paid Atlas read | Low | High | `load_frontrunner_signals` and `load_community_strategies` reach Atlas on a cache miss and the cache has no pytest sentinel (tests/advisors/test_frontrunner_gate_wiring.py:87-100). `block_atlas` runs in every world; the Strategy Builder harness already patches `load_atlas_candidates` (tests/_sb_generation_harness.py:259) |
| C1 | A re-take covers more than the listed edits, or another file's digest is re-taken by mistake | Low | High | The slice scope pin passes before any re-take and cuts only the three statements and one docstring; S-6.3 and S-6.4 assert every other pinned entry equals its ref value; the closer re-takes after GREEN in its own test commit (precedent 60bd4848) |
| C1 | Digests differ between Windows and CI line endings | Medium | Medium | Every digest is LF-normalized as the pins already do (tests/ai_advisor/test_advisor_backtest_client.py:1287-1291, tests/test_regime_label_protected_files.py:55); goldens are compared as parsed JSON |
| C1 | The new friction statement pushes the citation outside the window the check reads, or the generated page reintroduces the derivation at the slice tail | Low | Medium | The statement is 12 lines against the 15 line window (tests/autotuner/test_exit_friction_constant.py:143); S-4.5 and S-5.3 read the generated pages, so a doc-gen regression fails the suite |
| C1 | The goldens capture the head instead of the ref | Low | High | The capture script refuses unless every path outside `tests/` and `feature-plans/` equals c3e12882 (guard precedent tests/fixtures/preconditions_replay/capture_goldens.py:15-16); it runs on batch B's RED commit and `PROVENANCE.md` records that SHA |
| C1 | uuid4 ids minted by the tree constructors make the golden sequences differ run to run | High | Medium | `symphony_schema` constructors emit fresh ids (`.claude/CLAUDE.md` row for advisors/symphony_schema.py); the capture and the test replace every `raw_value` id by its order of first appearance |
| C1 | Two RED authors in one worktree commit over each other | Medium | Medium | Disjoint file sets (Execution Model); explicit-path staging and `git commit -- <paths>`, never `git add -A` or `git stash`; an `index.lock` refusal is retried |
| C1 | The frontrunner build runs on the real executor thread and the test reads the router before the build finishes | Medium | Medium | The executor (app.py:765) is replaced by an inline stand-in, so the build finishes inside the request (the real executor path is already covered at tests/app/test_frontrunner_builder_route.py:271) |
| C1 | Composer rejects or misreads 0.001 in production | Low | High | The PM's 2026-11-16 measurement sent 0.001 and got results (plan Contract Changes); the Merge Criteria live check re-confirms through the client before push |
| C1 | Tenure verdicts change at the first nightly run after deploy (plan Q-1: two measured symphonies would read watch) | Medium | Medium | Expected by the plan (Breaking Changes); the PM reads the latest `TENURE_REVIEW_RUN` row after the first 03:45 run (Merge Criteria) |

## Cycle-Close Criteria

- Every scenario of C1 has a row in `records/coverage.json` under `.claude/review/<HEAD-sha12>/`, `tested` with its test file and name (the existing tests named in the table count as tests); batch B's rows are merged into the closer's record. `python3 ~/.claude/scripts/plan-trace check-coverage <record> --plan feature-plans/backtest-slippage-calibration.md --approach feature-plans/approach/backtest-slippage-calibration.md --repo . --head <HEAD>` runs clean.
- RED was committed by both batches before GREEN, and each RED test was seen failing for the stated reason (the body carries 0.005; the statement still derives; the generated pages still give 0.005). Tests of today's behaviour stayed green at RED: AC-2's explicit values, S-1.9, S-3.15, S-3.17's scan, S-4.3, S-4.6, S-4.7, S-6.3, S-6.4 and the slice scope digests.
- The S-3.14 goldens were captured on batch B's RED commit; `PROVENANCE.md` holds the SHA and the empty guard output; no golden changes after GREEN.
- The four pins were re-taken after GREEN in a test commit, each with the comment naming this plan, and the slice scope pin passed on the GREEN commit before the re-take.
- These files pass under `-n0`, each named on its own run: the six files changed or added under tests/ that hold tests, plus tests/test_regime_label_protected_files.py, tests/test_hygiene_protected_surfaces.py, tests/test_preconditions_replay_protected_scope.py, tests/test_mdd_window_truth_zero_diff_guard.py, tests/advisors/test_tenure_review_verdict.py, tests/autotuner/test_exit_friction_three_site_application.py, tests/autotuner/test_exit_friction_blast_radius.py, tests/app/test_guard_alpha_summary_friction_aware.py, tests/app/test_exit_turnover_route.py, tests/ui/test_page_copy_dashboard.py, tests/app/test_page_copy_values.py, every file that imports the two edited harnesses (`git grep -l "install_chain_seams\|_asset_swap_llm_support" -- tests`), and the three no-trade boundary files under tests/security/ named in plan NB-15.
- `ruff format --check` and `ruff check` are clean (line length 100).
- Diff scope: `git diff --stat c3e12882 -- . ':(exclude)tests' ':(exclude)feature-plans'` lists exactly advisors/composer_backtest_client.py, autotuner.py, docs/generated/advisors_composer_backtest_client.md and docs/generated/autotuner.md; `git diff c3e12882 -- advisors/composer_backtest_client.py autotuner.py` shows only the listed hunks, pasted into the coverage map.
- The sufficiency sweep runs the plan's DC-1 to DC-7 sibling searches and records each result in the coverage map.

## Merge Criteria

Delivery: no PR (project mandate). The PM merges `feat/backtest-slippage-calibration` into `main` with a merge commit and pushes it to `origin/main` as a fast-forward, never a force push, only after every check below passes on that exact head.

The head being merged must pass:
- The verdicts of `qa`, the hidden-mode `qa`, `code-reviewer`, `quant-code-reviewer` and `composer-alpaca-integration` on the head read PASS, and the slice-tail doc-gen record is present.
- CI green on the exact SHA: ruff format, ruff check and the full pytest under CI's own worker count.
- No migration and no configuration key: `migrations/`, `database._MIGRATION_FILES` and `_SETTINGS_WRITE_ALLOWLIST` are unchanged (NB-4, NB-9).
- The PM's live functional check against the real Composer API, read-only: one live symphony's current tree backtested through `run_backtest` with no slippage argument and again with `slippage_percent=0.005`. The first answers HTTP 200, its request body carries 0.001 (logged or captured at the client), and its `costs` slippage figure is lower than the second's (plan Definition of Done).
- The deploy gate: out of market hours after a closed-market check that includes the NYSE holiday calendar; the restart ends no dashboard session (no logout generation bump, `SECRET_KEY` and the session lifetime unchanged).
- After the deploy, the PM runs the two read-only queries of the plan's Known-Unconfirmed list on the production state database (the latest `TENURE_REVIEW_RUN` row after the first 03:45 run, and the count of `INCUBATING` candidates) and records the results against Q-1 and Q-3.
- At ship, doc-gen appends the `DECISIONS.md` entry, the `docs/generated/INDEX.md` log entry, the drafted `.claude/CLAUDE.md` Key Files rows for PM approval, and one `BACKLOG.md` line for NB-16.

## Items Raised to the PM

1. Project gotcha "invoke `composer-api-researcher` before any client change" (`.claude/CLAUDE.md` Known Gotchas). This slice changes only the default value of an existing field, which the PM's 2026-11-16 measurement already sent to Composer, and the Merge Criteria live check re-confirms it. Recommended default: treat those two as satisfying the rule. The alternative is a read-only `composer-api-researcher` dispatch to confirm the unit of `slippage_percent` in Composer's documentation, which the plan lists as Known-Unconfirmed. The PM decides before C1 starts.
2. Reach of AC-4's rule ("no check or provenance statement in the repository"). The design changes autotuner.py's statement, the friction test module and docs/generated/autotuner.md (S-4.5, S-4.8); the `.claude/CLAUDE.md` `autotuner.py` row goes to the doc-gen's draft for PM approval (plan Docs to Update). Three dated records still state the derivation and are left as they are: `DECISIONS.md:9239` (append-only; the new entry supersedes it), `feature-plans/exit-friction-realized-savings.md:10` and `:22` (a shipped plan), and `docs/research/methodology-validation-2026-07.md:103-105` (NB-11). Recommended default: confirm that reading; no scenario tests those files.
3. Noted for confirmation, no decision required:
   - The evidence statement gives the means exactly as the plan's computed rows (-6.45, +0.875, +29.9), which S-5.5 requires, rather than S-5.1's rounded "about" form.
   - The generated-page edits are made in GREEN by the implementer because S-4.5 and S-5.2 test them; the doc-gen keeps them.
   - S-4.1 lives in the slice scope pin module so the friction test module carries no client reference (S-4.4).
