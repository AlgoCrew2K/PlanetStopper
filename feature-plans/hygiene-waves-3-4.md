# Feature: Cleanup Waves 3 and 4, review leftovers and repository hygiene
Status: gate-1
Story: none: the PM's hygiene scope of 2026-11-03, saved verbatim in feature-plans/hygiene-waves-3-4.source.md, with the reachability evidence in docs/audit/HYGIENE-REACHABILITY-2026-11-03.md
Type: chore
Suite: none
Repos: AlphaBotPM
Base: main
Ref: AlphaBotPM@7adc2c56953a3873c013425ebb06033375b62c53
Sensitive: security, money, sensitive-data (AC-3, AC-4, AC-5, AC-6, AC-10)
Depends on: none

## Trigger
The operator's standing hygiene directive and the PM's scope for the unfinished June cleanup program (Waves 3 and 4), the leftover rows of the two latest review rounds and three repository hygiene items, saved verbatim in `feature-plans/hygiene-waves-3-4.source.md` and quoted in Requirements as Given (G-1 to G-46); the PM's intake answers are G-47 to G-62. The reachability re-check the scope relies on was run at 758f7f95 and is tracked as `docs/audit/HYGIENE-REACHABILITY-2026-11-03.md`; every line it cites was re-found at the ref of this plan (7adc2c56), and every `path:line` below is at 7adc2c56.

Known facts at the ref:
1. The eleven DEAD items still exist with no production caller: `autotuner.build_symphony_study_name` (`autotuner.py:623`), `autotuner._build_optuna_sampler_from_env` (`autotuner.py:284-295`), the local `_n_jobs_raw` (`autotuner.py:2750`, assigned and never read; `autotuner.py:2751` is the value used), `database.record_autotune_run` (`database.py:1076`; the live writer is `database.save_autotune_run`, called at `autotuner.py:3227`), `analytics.compute_v1_bootstrap_state` (`analytics.py:2745-2757`), `database.get_eod_shadow_row` (`database.py:3864-3870`), `database.compute_shadow_hwm` (`database.py:3873-3891`, X-1; only `tests/shadow/test_shadow_history.py:1105` calls it), `database.get_advisor_observations_for_subject` (`database.py:1396-1418`), `math_engine.filter_sortino_sentinels` (`math_engine.py:18-30`), `database.get_suggestions_for_symphony` and `get_suggestions_for_session` (`database.py:1261-1296`) with their only helper `_parse_llm_suggestion_row` (`database.py:1214`) and column list `_LLM_SUGGESTION_COLUMNS` (`database.py:1240`), the root module `composer_backtest.py` (399 lines, imported only by `tests/ai_advisor/test_composer_backtest_client.py:173-747`), and `database.get_or_create_phase15_m3_bundle_id` (`database.py:2319-2394`) with its constants and cache (`database.py:2265-2316`).
2. The only module-level `getattr` dispatch on `database`, `analytics`, `autotuner` or `math_engine` in production code is `ai_advisor.py:1429-1430` (`DEFAULT_STRATEGY`, `DEFAULT_LOCKED_VARS`) and `app.py:3536` (`get_portfolio_bot_and_held_daily_returns`); none names an item of this plan. Test-side string dispatch exists: `tests/autotuner/test_optuna_sampler_pin_and_n_jobs.py:364,409` and `tests/math_engine/test_m3_spec_bundle_provenance.py:78` call `getattr` on a module with an item's name.
3. The `llm_suggestions` table is written live by `database.record_llm_suggestion` (`database.py:1152`) from `/ai-advisor/accept` (`app.py:12406`, write at `app.py:12462`) and `/ai-advisor/reject` (`app.py:12482`, write at `app.py:12495`); no code reads it except the two dead readers.
4. HYG-014 no longer exists: no `_frozen_status_rank`, `_frozen_exit_ret`, `get_status_rank`, `get_exit_ret` or `sortCol` read is left in tracked `*.py`, `*.js` or `*.html` outside `.design-handoff/` and the absence tests in `tests/app/test_api_state_without_table.py`.
5. The three `_z` closures are byte-identical: `math_engine.py:1356` in `run_monte_carlo` (`math_engine.py:1280`), `math_engine.py:1792` in `compute_portfolio_cvar` (`math_engine.py:1698`), `math_engine.py:2011` in `compute_regime_match_quality` (`math_engine.py:1922`). The engine reaches them every cycle (`alpha_bot_execution.py:1354`, `:1372`, `:1742`, `:1757`) and the replay through `synthetic_history.py:448,467`.
6. The boolean environment parse `.lower() in ("true", "1", "yes")` appears at ten production sites, four of them with the tuple wrapped over several lines (which a single-line grep misses): `alpha_bot_execution.py:75` (module-level `LIVE_EXECUTION`, from `os.getenv` with default `"False"`), `app.py:3639` (`_build_meta`), `app.py:5642` (`get_state`, `GET /api/state`, route at `app.py:5452`), `app.py:8826` (`sell_account`, `POST /api/sell_account`, route at `app.py:8804`), `app.py:9625` (`get_symphony_settings`, `GET /api/symphony-settings/{symphony_name}`, route at `app.py:9614`) `sleeves/tick_orchestrator.py:187-189` (`_compute_live_gates`, `SLEEVE_LIVE_EXECUTION`), and the multi-line four: `app.py:175` (`SESSION_COOKIE_SECURE`, from `os.environ` with default `""`), `app.py:5465` (`_additive_live_mode`, the `live_mode` field of `GET /api/state` at `app.py:5474`), `app.py:5546` (`_frozen_live_mode`, the `GET /api/state` frozen branch) and `app.py:9096` (`POST /api/sleeves/{sleeve_id}/arm-live`, route at `app.py:9033`, `SLEEVE_LIVE_EXECUTION` guarded by `or "False"`, refusing with 400 "SLEEVE_LIVE_EXECUTION is not enabled" at `app.py:9101-9102`). All ten accept exactly `true`, `1` and `yes` after lower-casing. The six `app.py` sites that read a `dotenv_values` mapping without a guard (`:3639`, `:5465`, `:5546`, `:5642`, `:8826`, `:9625`) raise `AttributeError` when a `.env` line holds the key with no `=` (the mapping holds `None`); `:9096` turns `None` into `"False"`.
7. The three row parsers differ: `_parse_llm_suggestion_row` (`database.py:1214-1237`) and `_parse_frontrunner_proposal_row` (`database.py:4969-4983`) raise on unreadable JSON, while `_parse_advisor_observation_row` (`database.py:1315-1338`) turns unreadable `raw_response` into `{}` and adds `raw_response_unreadable: True`.
8. The two latest-shadow-row readers share one SQL core and differ in contract: `analytics._load_latest_shadow_row_for_analytics` (`analytics.py:678-719`) opens `file:...?mode=ro` or reuses a passed connection, saves and restores `row_factory`, memoizes through `_read_once`, returns a copy and calls `note_build_failure()` on error; `database.load_latest_shadow_row` (`database.py:3665-3697`) opens a read-write connection, takes an optional `et_cutoff` (`substr(ts_et, 12, 8) <= ?`) and logs one ERROR on failure. The database reader is on the engine's EOD path (`alpha_bot_execution.py:1068`) and the post-mortem stages (`reporting.py:73`, `reporting.py:180`).
9. No two of the eight finite-float helpers have identical semantics (`database.py:25`, `analytics.py:515`, `app.py:7738`, `app.py:6229`, `app.py:6678`, `advisors/tenure_review.py:509`, `advisors/frontrunner_acceptance.py:234`, `regime_classifier.py:190`; D-8).
10. The engine prints `Awaiting 3-tick confirmation...` (`alpha_bot_execution.py:1547-1549`) when the below-stop count goes from 0 to 1, while the count in force is `_regime_exit_ticks` (`alpha_bot_execution.py:1526-1531`), which `math_engine.apply_regime_exit_adjustment` sets to 2, 5 or 3 by label and to `EXIT_CONFIRM_TICKS` (3, `math_engine.py:610`) for no label (`math_engine.py:733-744`, `math_engine.py:779-785`).
11. Three content pins cover the engine-side files: `tests/test_mdd_window_truth_zero_diff_guard.py:72-73` (`alpha_bot_execution.py`, `math_engine.py`), `tests/engine/test_close_snapshot_builder_scope.py:28` (`alpha_bot_execution.py` minus the close-snapshot builder) and `tests/test_regime_label_protected_files.py:35-49` (`math_engine.py`, `regime_classifier.py`, `autotuner.py`, `synthetic_history.py`, `migrations/027_regime_label_cache.sql`, and `alpha_bot_execution.py` minus the regime read); the last says to stop and ask the PM before re-taking any digest (`tests/test_regime_label_protected_files.py:19-23`; Q-1).
12. The real Composer account id is in 71 tracked files at the ref: 66 plain-text files and 5 gzip records in `tests/fixtures/refresh_timing/01-c1/` that a plain grep does not see; 7 further tracked files carry only its first 8 characters (Q-6). Three capture sidecars and the `PROVENANCE.md` tables of `tests/fixtures/refresh_timing/` and `tests/fixtures/state_poll_no_table/` record sha256 digests and byte counts of changed files, and `tests/fixtures/refresh_timing/test_record_hashes.py:24-33` checks the sidecars. (Counted by a script that read the id from a fixture and printed only counts; the id is not written anywhere in this plan.)

## Summary
The code no longer carries the dead functions, duplicated helpers and stale text the June cleanup audit (Waves 3 and 4) and the two latest review rounds found, no tracked file holds the real Composer account id, two wall-clock tests stop depending on machine load, and the engine's dip line names the confirmation count in force, for the developers and agents who work in the code and the operator who reads the engine console, with every route response, engine decision, autotuner output and schema unchanged.
- The dead functions, orphan fixtures and names they leave unread are removed, each proven unreachable by call path at the ref (AC-1).
- The unused root `composer_backtest.py` is removed after the canonical client's three tests move out of its test file (AC-2).
- The z-score closures and the boolean environment-flag parse (all ten sites) each have one definition, with every caller's result unchanged for every input (AC-3).
- The JSON-column row parsers and the latest-shadow-row SQL each have one definition, with each reader's contract unchanged (AC-4).
- The engine's dip line states the confirmation tick count in force for that symphony on that tick (AC-5).
- The real Composer account id is replaced by `ACCOUNT_ID_REDACTED` in every tracked file, with every recorded digest re-stated (AC-6).
- The two named tests and the heading-margin baseline producer give the same verdict under load and with or without a local `.env` (AC-7).
- The regime label job fails only the affected batch on an unusable page token, and its summary helper's annotation matches its body (AC-8).
- Stale comments, docstrings, annotations and whitespace are corrected, the specialist agent file stops naming a deleted template, and `.claude/archive/` is ignored (AC-9).
- Protected surfaces stay byte-identical or behavior-identical, and each content pin is re-taken only over the listed edits (AC-10).

## Requirements as Given
[G-1] 1. "clean up after ourselves" (standing operator hygiene directive; cleanup, dead code and hygiene are first-class work on this project). hygiene-waves-3-4.source.md:4
[G-2] 2. "it should do what it was designed to do" hygiene-waves-3-4.source.md:5
[G-3] 3. "There should be no goddamn decisions for me" hygiene-waves-3-4.source.md:6
[G-4] PM scope (the PM is the requester for everything below; every call is the PM's, recorded [PM-ASSUMED]) hygiene-waves-3-4.source.md:8
[G-5] A. June cleanup program, Waves 3 and 4 (never finished) hygiene-waves-3-4.source.md:10
[G-6] A re-check of reachability at 758f7f95 (2026-11-03, integration-auditor) found these items. hygiene-waves-3-4.source.md:11
[G-7] Line numbers there are at 758f7f95 and must be re-found at 7adc2c56. hygiene-waves-3-4.source.md:11
[G-8] DEAD, delete: HYG-011 autotuner.build_symphony_study_name; HYG-012a autotuner._build_optuna_sampler_from_env; HYG-012b autotuner local _n_jobs_raw; HYG-008 database.record_autotune_run (live writer is save_autotune_run; test mocks move to it); HYG-013 analytics.compute_v1_bootstrap_state; HYG-016 database.get_eod_shadow_row; HYG-010 database.get_advisor_observations_for_subject (8 test files use it, 2 as a test-side reader); HYG-001 math_engine.filter_sortino_sentinels (live one is autotuner.filter_sortino_sentinels); HYG-009 database.get_suggestions_for_symphony and get_suggestions_for_session (llm_suggestions is written live, never read in code); HYG-018 root composer_backtest.py (399 lines, tests-only importers); HYG-005 database.get_or_create_phase15_m3_bundle_id with its _M3_R1_*/_M3_R2_* constants. hygiene-waves-3-4.source.md:12
[G-9] DUPLICATE, extract one shared helper: HYG-003 three byte-identical _z closures in math_engine (run_monte_carlo, compute_portfolio_cvar, and the third site); HYG-014 _frozen_status_rank/_frozen_exit_ret vs get_status_rank/get_exit_ret in app.py (may have changed or gone with the /api/state table removal; re-verify); HYG-004 the three row-parse helpers in database.py; HYG-007 the inline .lower() in ("true","1","yes") env parse (6 sites incl. alpha_bot_execution.py); HYG-015 analytics._load_latest_shadow_row_for_analytics vs database.load_latest_shadow_row (respect analytics' read-only connection constraint); HYG-006 finite-float helpers, only where semantics are identical. hygiene-waves-3-4.source.md:13
[G-10] Extras found while tracing: X-1 database.compute_shadow_hwm (no production caller); X-3 autotuner._collect_sim_returns(return_dates=True) branch; X-4 regime_classifier.fit_regime_classifier (tests only); X-7 orphan fixtures tests/fixtures/autotuner/sortino_sentinel_trim/; X-9/X-10 extra sortino-sentinel shapes (read and rule); X-11 regime_classifier.py:9-12 docstring claims offline-only use, contradicted by the autotuner replay and now by regime_label_job.py. hygiene-waves-3-4.source.md:14
[G-11] Full evidence: .claude/review/hyg-recheck-20261103/reachability.md in the primary checkout (copy its findings into the plan as needed; that file is untracked). hygiene-waves-3-4.source.md:15
[G-12] PM rulings on Wave 3/4: hygiene-waves-3-4.source.md:17
[G-13] Every deletion is proven unreachable by call path at 7adc2c56 (not grep), including getattr/string dispatch and mock.patch targets (tests that patch a deleted name move to the live name or are deleted with it). hygiene-waves-3-4.source.md:18
[G-14] HYG-002 (_collect_sim_returns variants) is an intentional, documented duplicate: keep; the replay-fidelity cycle owns the replay. hygiene-waves-3-4.source.md:19
[G-15] HYG-020 regime_classifier is LIVE (autotuner replay and regime_label_job): keep; fix only the docstring drift (X-11). hygiene-waves-3-4.source.md:20
[G-16] HYG-017 (relative .env reads in get_state and sell_account) moves to the controls slice with Cash Now, because it touches POST /api/sell_account. hygiene-waves-3-4.source.md:21
[G-17] HYG-018: before deleting composer_backtest.py, any behavior its tests cover that the canonical advisors/composer_backtest_client.py also has gets its test moved there (e.g. TestDvmCapitalKeyMismatchFallback, if the canonical client has that behavior); behavior that exists only in the dead module is dead and goes with it. hygiene-waves-3-4.source.md:22
[G-18] HYG-005: read the M3 decision record in DECISIONS.md to its end. hygiene-waves-3-4.source.md:23
[G-19] If the phase-1.5 helper was a finished provenance artifact, delete it. hygiene-waves-3-4.source.md:23
[G-20] If it was meant to be registered in production and never was, do not build it here: report it to the PM as a missing wire. hygiene-waves-3-4.source.md:23
[G-21] HYG-009: the llm_suggestions table and its live writer stay; only the two unused readers go. hygiene-waves-3-4.source.md:24
[G-22] The llm_suggestions ad hoc SQL question is closed: code reachability governs. hygiene-waves-3-4.source.md:25
[G-23] alpha_bot_execution.py:1548 prints "Awaiting 3-tick confirmation..." when a symphony dips below its stop. hygiene-waves-3-4.source.md:28
[G-24] Since the regime label job, the confirm count is 2, 5 or 3 per symphony (math_engine.apply_regime_exit_adjustment). hygiene-waves-3-4.source.md:28
[G-25] The text must state the actual count in force for that symphony on that tick. hygiene-waves-3-4.source.md:28
[G-26] Console text only; no decision change. hygiene-waves-3-4.source.md:28
[G-63] C. Two wall-clock flaky tests (pre-existing; failed only under CI load) hygiene-waves-3-4.source.md:30
[G-27] tests/app/test_engine_run_bound.py::test_a_run_hung_in_the_session_is_ended_after_600_seconds_and_the_next_minute_spawns (failed once on CI: "minutes were skipped after the hung run was ended") hygiene-waves-3-4.source.md:31
[G-28] tests/app/test_scheduler_survival.py::TestSlotsThatRaise::test_composer_held_for_the_whole_run_does_not_delay_either_engine_spawn hygiene-waves-3-4.source.md:32
[G-29] Both use real wall-clock deadlines. hygiene-waves-3-4.source.md:33
[G-30] They must keep proving the same behavior and stop depending on machine load. hygiene-waves-3-4.source.md:33
[G-64] D. Leftover low-severity review rows (verify each still applies at 7adc2c56) hygiene-waves-3-4.source.md:35
[G-31] Cleanup slice review (folder .claude/worktrees/dtp/.claude/review/f3a699965cca/): code-reviewer CR-1 (joined comment line, double blank lines in templates/ai_advisor.html), CR-2 (unannotated helpers in tests/app/_state_poll_worlds.py); the false sort comment near app.py ~:5509; stale comments near app.py ~:5681 and ~:4439-4440; qa QA-8 (a test producer that depends on a local .env); a settings-modal comment; integration-auditor INT-1/INT-2, qa QA-1..QA-7, flask-dashboard-specialist FLASK-1 where they are code or test hygiene. hygiene-waves-3-4.source.md:36
[G-32] ux-expert UX-1..UX-5 are OUT (visible UI goes to the UI slice). hygiene-waves-3-4.source.md:36
[G-33] Regime slice review (folder .claude/worktrees/regime/.claude/review/d615e0941f25/): pipelines-expert PIPE-3 (repeated-token check in regime_label_job._fetch_batch sits outside the parse try; an unhashable token fails all batches), code-reviewer CR-5 (_log_summary annotates outcomes as Iterable but calls len() and iterates twice), and qa QA-AC-1..7 where they are code or test hygiene. hygiene-waves-3-4.source.md:37
[G-34] S-7 (security audit, MEDIUM): the real Composer account id appears in tracked files (43 files at 38b0cbe8; tests/fixtures/refresh_timing/*, tests/fixtures/prod_droplet/*, docs/handoff). hygiene-waves-3-4.source.md:40
[G-35] Replace it everywhere in the tracked tree with the existing placeholder ACCOUNT_ID_REDACTED (precedent: tests/fixtures/composer/tenure-review/README.md). hygiene-waves-3-4.source.md:40
[G-36] No git history rewrite (the id is not a credential; a rewrite needs a force-push of a public repository). hygiene-waves-3-4.source.md:40
[G-37] Tests that compared against the real id compare against the placeholder. hygiene-waves-3-4.source.md:40
[G-38] Never print the real id in any artifact. hygiene-waves-3-4.source.md:40
[G-39] .claude/archive/ is not gitignored: add it. hygiene-waves-3-4.source.md:41
[G-40] .claude/agents/flask-dashboard-specialist.md still names templates/table_partial.html (deleted): remove the mention. hygiene-waves-3-4.source.md:42
[G-65] Out of scope (other slices, already queued) hygiene-waves-3-4.source.md:44
[G-41] Security slice: S-9 (/health detail), S-10, S-11 (logout), S-17 (explainer prompt boundary). hygiene-waves-3-4.source.md:45
[G-42] Controls slice: Cash Now, tenure on-demand market gate, HYG-017. hygiene-waves-3-4.source.md:46
[G-43] Engine slice: VWAP_BLEED_MULTIPLIER/VWAP_BLEED_TICKS read from settings. hygiene-waves-3-4.source.md:47
[G-44] UI slice: D1 fade, P-1 axis contrast, closed-market detail footer showing a Composer hash, ux-expert rows UX-1..UX-5. hygiene-waves-3-4.source.md:48
[G-45] Replay-fidelity math cycle: IEX vs SIP in the replay. hygiene-waves-3-4.source.md:49
[G-46] Worktree removal (PM operation, not code). hygiene-waves-3-4.source.md:50
[G-47] (Intake, Building 1) Makes true: the code carries none of the dead functions, duplicated helpers or stale comments the June cleanup audit (Waves 3 and 4) and the two latest review rounds found; no tracked file carries the real Composer account id; two wall-clock-flaky tests become load-independent; the engine's "dipped below stop" console line states the confirm-tick count actually in force.
[G-48] (Intake, Building 1) Trigger: standing operator hygiene directive; the 2026-11-03 recheck found 11 DEAD and 7 DUPLICATE items still present.
[G-49] (Intake, Building 2) Users: developers and agents working in the code; the operator reads the engine console log.
[G-50] (Intake, Building 2) Surfaces: app.py, database.py, analytics.py, autotuner.py, math_engine.py, alpha_bot_execution.py, regime_label_job.py, regime_classifier.py, root composer_backtest.py, tests/, tests/fixtures/, docs/handoff, .gitignore, .claude/agents/flask-dashboard-specialist.md.
[G-51] (Intake, Building 3) Screens: no screen changes visibly.
[G-52] (Intake, Building 3) Every screen reads `no workflow file: no visible change in this plan` (no `.claude/workflows/` files exist).
[G-53] (Intake, Building 4) Negatives: every route response, engine exit decision, autotuner output and DB schema stays identical; a deletion of a reachable name must be impossible to land (call-path proof, including getattr/string dispatch and mock.patch targets); a shared helper must behave exactly like each site it replaces (characterization first); the engine sha256 pins (tests/test_mdd_window_truth_zero_diff_guard.py, tests/engine/test_close_snapshot_builder_scope.py) are re-pinned only when the engine diff is exactly the listed edits; the account id never appears in any artifact, log or message.
[G-54] (Intake, Building 5) Data/contracts: no schema or migration change; the llm_suggestions table and its live writer stay; no route or wire shape changes; fixtures change only the account-id value to ACCOUNT_ID_REDACTED.
[G-55] (Intake, Building 6) Repos: AlphaBotPM only.
[G-56] (Intake, Building 7) Sensitive: YES (edits the engine file alpha_bot_execution.py and math_engine.py; S-7 handles account-identifying data).
[G-57] (Intake, Building 7) qa writes hidden scenarios after approval.
[G-58] (Intake, Not building 1) No visible UI change; no security fixes S-9/S-10/S-11/S-17; no Cash Now, tenure gate or HYG-017 (controls slice); no VWAP_BLEED change (engine slice); no git history rewrite for S-7; no change to the replay (HYG-002 stays, replay-fidelity cycle owns it); no removal of llm_suggestions or regime_classifier.
[G-59] (Intake, Not building 2) Behavior that stays exactly as is: all of item 4 above.
[G-60] (Intake, Not building 3) Deferred (tracked in [the PM's work ledger, a harness file named in the brief]): security, controls, engine VWAP, UI and replay-fidelity slices; worktree removal is a PM operation.
[G-61] (Intake, Not building 4) Consumed, not changed: Composer and Alpaca client wire behavior; advisors/composer_backtest_client.py behavior.
[G-62] (Intake, PM brief) The PM rulings in the source file (HYG-002 keep, HYG-020 keep + docstring, HYG-017 out, HYG-018 move-then-delete, HYG-005 conditional, HYG-009 readers only) are decisions, not open questions: record them as [PM-ASSUMED] with their reasons.

## Requirement Inventory
| R-n | Requirement item | Source (G ids) | Maps to (AC ids, or NB-n) | Scope (full, or narrowed: what is left out) |
|---|---|---|---|---|
| R-1 | Cleanup of dead code, duplicates and stale text is done as first-class work | G-1, G-47, G-48 | AC-1, AC-3, AC-4, AC-9 | full |
| R-2 | The code does what it was designed to do | G-2 | AC-5, AC-8 | narrowed: applied to the dip line and the regime job's batch isolation; the M3 bundle's designed production registration is reported to the PM, not built (G-20, NB-7) |
| R-3 | No decision is left to the operator | G-3, G-4 | NB-1 | full |
| R-4 | Every Wave 3 and 4 item is re-verified at 7adc2c56 from the tracked evidence | G-5, G-6, G-7, G-11 | AC-1, AC-3, AC-4 | full |
| R-5 | `autotuner.build_symphony_study_name` is deleted | G-8 | AC-1 | full |
| R-6 | `autotuner._build_optuna_sampler_from_env` and the local `_n_jobs_raw` are deleted | G-8 | AC-1 | full |
| R-7 | `database.record_autotune_run` is deleted and its test mocks move to the live writer | G-8 | AC-1 | full |
| R-8 | `analytics.compute_v1_bootstrap_state` is deleted | G-8 | AC-1 | full |
| R-9 | `database.get_eod_shadow_row` is deleted | G-8 | AC-1 | full |
| R-10 | `database.get_advisor_observations_for_subject` is deleted, and the tests that read through it keep their coverage | G-8 | AC-1 | full |
| R-11 | `math_engine.filter_sortino_sentinels` is deleted; the autotuner one stays | G-8 | AC-1 | full |
| R-12 | The two `llm_suggestions` readers are deleted; the table and its live writer stay | G-8, G-21, G-22, G-54 | AC-1, AC-10 | full |
| R-13 | Root `composer_backtest.py` is deleted after the canonical client's tests move out of its test file | G-8, G-17 | AC-2 | full |
| R-14 | HYG-005 follows the PM's conditional ruling from the M3 decision record | G-8, G-18, G-19, G-20 | NB-7 | full |
| R-15 | The three `_z` closures share one definition | G-9 | AC-3 | full |
| R-16 | HYG-014 is re-verified and de-duplicated if present | G-9 | NB-8 | full |
| R-17 | The row-parse helpers share one definition | G-9 | AC-4 | narrowed: two parsers remain after R-12 removes the third |
| R-18 | The six boolean environment parses named by HYG-007 share one definition | G-9 | AC-3 | full |
| R-19 | The two latest-shadow-row readers share one definition, keeping analytics' read-only connection | G-9 | AC-4 | narrowed: they share the SQL statement; each keeps its own connection, memo, error and cutoff behavior (Known fact 8) |
| R-20 | Finite-float helpers are consolidated only where semantics are identical | G-9 | NB-9 | full |
| R-21 | `database.compute_shadow_hwm` is deleted | G-10 | AC-1 | full |
| R-22 | The dead `return_dates=True` branch of `_collect_sim_returns` | G-10 | NB-10 | full |
| R-23 | `regime_classifier.fit_regime_classifier` | G-10 | NB-11 | full |
| R-24 | The orphan fixtures under `tests/fixtures/autotuner/sortino_sentinel_trim/` are deleted | G-10 | AC-1 | full |
| R-25 | The extra sortino-sentinel shapes X-9 and X-10 are read and ruled | G-10 | NB-12 | full |
| R-26 | The `regime_classifier.py` docstring states its live callers; nothing else in the module changes | G-10, G-15 | AC-9, AC-10 | full |
| R-27 | Each deletion is proven unreachable by call path, including getattr and string dispatch and mock.patch targets, and tests that patch a deleted name move to the live name or go with it | G-13, G-53 | AC-1, AC-2 | full |
| R-28 | HYG-002 stays and the replay does not change | G-14, G-58 | AC-10, NB-18 | full |
| R-29 | HYG-017 is not in this plan | G-16, G-42 | NB-4 | full |
| R-30 | The dip line states the confirmation count in force for that symphony on that tick | G-23, G-24, G-25, G-47 | AC-5 | full |
| R-31 | The dip-line change is console text only, with no decision change | G-26 | AC-5, AC-10 | full |
| R-32 | The hung-run test keeps proving the same behavior without depending on machine load | G-27, G-29, G-30, G-47, G-63 | AC-7 | full |
| R-33 | The Composer-held scheduler test keeps proving the same behavior without depending on machine load | G-28, G-29, G-30, G-47, G-63 | AC-7 | full |
| R-34 | The cleanup slice's leftover review rows are fixed where they are code or test hygiene | G-31, G-64 | AC-7, AC-9, NB-14 | narrowed: INT-2 and QA-1 to QA-7 need no work (D-11) |
| R-35 | ux-expert UX-1 to UX-5 are out | G-32, G-44 | NB-2 | full |
| R-36 | The regime slice's leftover review rows are fixed where they are code or test hygiene | G-33, G-64 | AC-8, NB-14 | narrowed: QA-AC-1 to QA-AC-7 need no work (D-11) |
| R-37 | The real Composer account id is replaced by `ACCOUNT_ID_REDACTED` everywhere in the tracked tree | G-34, G-35, G-47, G-54 | AC-6 | full |
| R-38 | No git history rewrite | G-36, G-58 | AC-6, NB-6 | full |
| R-39 | Tests that compared against the real id compare against the placeholder | G-37 | AC-6 | narrowed: a test whose assertion needs a UUID-shaped value uses an invented UUID v4 instead (Q-7) |
| R-40 | The real id never appears in any artifact, log or message | G-38, G-53 | AC-6 | full |
| R-41 | `.claude/archive/` is gitignored | G-39 | AC-9 | full |
| R-42 | The specialist agent file stops naming `templates/table_partial.html` | G-40 | AC-9 | full |
| R-43 | The security, controls, engine, UI and replay-fidelity slices and worktree removal stay out | G-41, G-42, G-43, G-44, G-45, G-46, G-58, G-65 | NB-3, NB-4, NB-5, NB-21 | full |
| R-44 | The work stays in AlphaBotPM on the surfaces the items name | G-49, G-50, G-55 | AC-10, NB-26 | full |
| R-45 | No screen changes visibly | G-51, G-52, G-58 | AC-9, NB-2 | full |
| R-46 | Every route response, engine exit decision, autotuner output and database schema stays identical | G-53, G-59 | AC-10, NB-16 | full |
| R-47 | A shared helper behaves exactly like each site it replaces, proven by characterization first | G-53 | AC-3, AC-4 | full |
| R-48 | Engine content pins are re-taken only when the diff is exactly the listed edits | G-53 | AC-10 | full |
| R-49 | No schema, migration, route or wire-shape change | G-54 | AC-10 | full |
| R-50 | Fixtures change only the account-id value | G-54 | AC-6 | narrowed: the orphan fixtures of R-24 are deleted, and the heading-margin baseline is re-captured if the QA-8 fix changes it (Q-11) |
| R-51 | The plan is sensitive and qa writes hidden scenarios after approval | G-56, G-57 | AC-3, AC-5, AC-6, AC-10 | full |
| R-52 | The deferred slices are tracked outside this plan | G-60 | NB-21 | full |
| R-53 | Composer and Alpaca client wire behavior and `advisors/composer_backtest_client.py` are consumed, not changed | G-61 | AC-2, NB-23 | full |
| R-54 | The PM's rulings are recorded as [PM-ASSUMED] decisions with their reasons | G-12, G-62 | NB-1 | full |
| R-55 | Each leftover review row is re-verified at 7adc2c56 before it is built | G-64 | AC-7, AC-8, AC-9 | full |
| R-56 | The ten boolean environment parses share one definition | G-9, G-53 | AC-3 | full |

## User Tasks
none: no screen changes visibly (G-51); every screen reads `no workflow file: no visible change in this plan` (G-52), and Design-System Mapping lists no screen.

## Earlier criteria on the same surface
Searched every `feature-plans/*.md` including the `.completed.md` and `.obsolete.md` families and the suite folders, `DECISIONS.md`, `docs/generated/*.md`, `docs/audit/` and `docs/audit-inputs/` for each removed or consolidated name, the dip-line text, the two test names, the three pin files, `ACCOUNT_ID_REDACTED`, `llm_suggestions`, `record_autotune_run` and the M3 facet names.

| Earlier criterion (plan and AC) | Surface | Disposition (kept, changed or contradicted) | Why |
|---|---|---|---|
| `feature-plans/regime-label-producer.md` NB-7 (the dip line "Awaiting 3-tick confirmation" is not changed by that plan) | `alpha_bot_execution.py:1547-1549` | changed | That plan deferred the text as a backlog line; this plan states the count in force (AC-5) |
| `feature-plans/regime-label-producer.md` AC-6 S-6.4 (math_engine, regime_classifier, autotuner byte-unchanged; engine unchanged outside the regime read), pinned by `tests/test_regime_label_protected_files.py` | the four files | changed | The pins are re-taken over this plan's listed, behavior-identical edits only (AC-10, Q-1) |
| `feature-plans/regime-label-producer.md` AC-5 and AC-7 (one symphony's failure never stops the others; one line per symphony and one summary) | `regime_label_job.py` | kept | AC-8 extends the isolation to an unusable page token and keeps the line formats |
| `feature-plans/mdd-window-truth.md` AC-7 (engine and math_engine zero diff, guarded by `tests/test_mdd_window_truth_zero_diff_guard.py`) | `alpha_bot_execution.py`, `math_engine.py` | changed | Re-pinned over the listed edits only; the `LIVE_EXECUTION` reference check stays (S-10.6) |
| `feature-plans/refresh-timing/01-server-state-truth.md` (close-snapshot builder scope, pinned by `tests/engine/test_close_snapshot_builder_scope.py:28`) | `alpha_bot_execution.py` | changed | The pin is re-taken over the listed engine edits; the builder block is untouched |
| `feature-plans/ux-overhaul/07-final-gate-fixes.md` AC-3 (a hung run is ended after 600 s and the next minute spawns) | the engine run bound | kept | The test proves the same assertions without depending on load (S-7.1, S-7.2) |
| `feature-plans/refresh-timing/02-scheduler-events-health.md` AC-3 S-3.2 (with Composer held, each `:00` engine is spawned within 2 s) | the scheduler loop | kept | The 2 s bound is restated as the two loop passes that take 2 s in production (one `time.sleep(1)` per pass), proven by ordering, not elapsed time (S-7.3) |
| `feature-plans/dead-table-partial-and-header-css.md` AC-1 to AC-7 | `GET /api/state`, the page templates | kept | Only comments and whitespace that slice left are corrected (AC-9); responses and computed styles stay (S-9.4, S-10.9) |
| `feature-plans/audit-bl5-12-hygiene-cleanup.md` AC-15 (each never-wired `autotune_runs` column carries a non-implementation note) | `database.py:211-222` | kept | The note keeps its signal words and stops naming the deleted writer as existing (S-1.7) |
| `feature-plans/decision-science.completed/phase-1/017-advisor-observations/plan.md` (`get_advisor_observations_for_subject` as the subject read accessor) | `database.py:1396` | contradicted | The accessor has no production consumer at the ref and is deleted (AC-1); the routes already use other readers |
| `feature-plans/decision-science.completed/phase-1.5/m3-redrive-provenance-gaps/plan.md` deliverables and done criteria (M3 facets persisted in `spec_bundles`, `:79`, `:109`) | `database.py:2265-2394` | kept | The helper stays and the missing production registration is reported to the PM (NB-7, D-5) |
| `feature-plans/held-basis-convergence.md` AC-9 (tests mocking `_load_latest_shadow_row_for_analytics` are discovered and still intercept) | `analytics.py:678` | kept | The function keeps its name and signature, so every patch still intercepts (S-4.5) |
| `feature-plans/symphony-tenure-review.md` fixture provenance (account id redacted in captured Composer fixtures) | `tests/fixtures/composer/tenure-review/` | kept | Extended to every tracked file (AC-6) |

## Acceptance Criteria (Building)

### AC-1: The dead functions and orphan fixtures are gone, and nothing reachable is removed
Requirement items: R-1, R-4, R-5, R-6, R-7, R-8, R-9, R-10, R-11, R-12, R-21, R-24, R-27
Rule: When the GREEN tree is built, the repository shall define none of `autotuner.build_symphony_study_name`, `autotuner._build_optuna_sampler_from_env`, the local `_n_jobs_raw`, `database.record_autotune_run`, `analytics.compute_v1_bootstrap_state`, `database.get_eod_shadow_row`, `database.get_advisor_observations_for_subject`, `math_engine.filter_sortino_sentinels`, `database.get_suggestions_for_symphony`, `database.get_suggestions_for_session`, `database.compute_shadow_hwm`, and any name that only these used, nor hold any file under `tests/fixtures/autotuner/sortino_sentinel_trim/` or `tests/fixtures/shadow/v1_bootstrap_gate.json`, while every name a production path reaches at the ref still exists with its behavior.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-1.1 | B | the GREEN tree | an AST scan of every tracked `.py` file runs | no listed name is defined or referenced anywhere outside an absence assertion; `git ls-files tests/fixtures/autotuner/sortino_sentinel_trim tests/fixtures/shadow/v1_bootstrap_gate.json` is empty, and the two `shadow_hwm_day1` keys (`tests/fixtures/shadow/analytics_shadow_history.json:91`, `:99`), read only by the deleted `compute_shadow_hwm` test, are gone |
| S-1.2 | N7 | the ref 7adc2c56 | the call-path proof for each listed name is recorded in the coverage record | each name has no production caller, is the target of no `getattr` or `importlib` dispatch (the only production module-level dispatch sites are `ai_advisor.py:1429-1430` and `app.py:3536`), no route or scheduler registration, and no reference in a tracked `.js`, `.html`, `.json`, `.toml`, `.ps1`, `.sh`, `.service` or `.timer` file |
| S-1.3 | N7 | the GREEN tree | the live siblings are imported and called | `autotuner.filter_sortino_sentinels`, `math_engine._SORTINO_SENTINEL` (1e6), `database.save_autotune_run`, `database.load_latest_shadow_row`, `database.record_llm_suggestion`, `database.insert_advisor_observation`, `analytics._V1_BOOTSTRAP_MIN_DAYS` and `analytics._WINDOWED_VOL_MIN_DAYS` exist with the same values and outputs as at the ref |
| S-1.4 | N7 | a test at the ref that read rows back through a deleted reader to check a writer (`tests/database/test_017_advisor_observations.py:428-880`, `tests/advisors/test_incubation_admission.py:293,465`, `tests/advisors/test_retirement_recommender_persistence.py:193,209`, `tests/database/test_audit_fix_migration_025_symphony_id.py:226`, `tests/perf/test_overfitting_conscience_write_latency.py:195`, `tests/database/test_llm_suggestions.py`) | the GREEN suite runs | a test asserts the same column values for the same writes, read without any deleted function; the read-only-connection and no-optimization-DB properties those files pinned on the deleted reader are pinned on the readers that remain where those properties apply to them |
| S-1.5 | N7 | the patch targets and mock attributes that name a deleted function at the ref (`tests/ai_advisor/test_advisor_observations_ui.py:134-633`, `tests/app/test_audit_fix_advisor_route_symphony_filter.py:146`, `tests/autotuner/test_m1_crra_eu_objective.py:899`, `tests/autotuner/test_pbo_units_decimal_contract.py:241`, `tests/app/test_dashboard_advisor_render_is_read_only.py:262`, `tests/app/test_dashboard_routes_never_call_engine_mutators.py:8,69,371`) | the GREEN suite is collected and run | each targets a live name or is deleted with its test; collection raises no `AttributeError` and no test patches a name the module no longer has |
| S-1.6 | N7 | `OPTUNA_SAMPLER_SEED` set to `1234567`, unset, empty, whitespace only, and `abc` | `run_autotuner` creates its study (`autotuner.py:2746-2747`, `autotuner.py:2767`) | the `TPESampler` passed to `optuna.create_study` has seed 1234567, None, None and None, and `abc` gives the same outcome as at the ref; the seed pins of `tests/autotuner/test_optuna_sampler_pin_and_n_jobs.py:340-415` observe this live site, not a helper |
| S-1.7 | N7 | the GREEN tree | `tests/database/test_bl10_dead_autotune_columns_documented.py` runs | it passes, and the comment at `database.py:211-222` no longer describes `record_autotune_run` as an existing function |
| S-1.8 | N1 | the GREEN tree | the names the deletions leave unread are listed, and the seed read is inspected | `_parse_llm_suggestion_row` and `_LLM_SUGGESTION_COLUMNS` are gone; `_OPTUNA_SAMPLER_SEED_ENV` (`autotuner.py:245`) stays and is the name `run_autotuner` reads the seed through (the literal at `autotuner.py:2746` gives way to it, with the seed parse at `:2747` unchanged), so `test_autotuner_exposes_named_env_var_constants` (`tests/autotuner/test_optuna_sampler_pin_and_n_jobs.py:136-166`) holds for the reason it was written; `_OPTUNA_N_JOBS_ENV` (`:246`) stays, read by the resolver (`autotuner.py:310`); the comment at `autotuner.py:2742-2745` that says literals appear at the call site is gone |
| S-1.9 | N7 | the GREEN tree, with `_n_jobs_raw` deleted, and `OPTUNA_N_JOBS` set to `3`, unset, empty, and `abc` | `run_autotuner` runs its study | the `n_jobs` passed to `study.optimize` (`autotuner.py:2770`) equals the ref's in each case; `test_run_autotuner_n_jobs_sourced_from_env_constant` (`tests/autotuner/test_optuna_sampler_pin_and_n_jobs.py:301-315`), which at the ref passes only through the `"OPTUNA_N_JOBS"` literal on the deleted line, is replaced by a check that `run_autotuner` takes its `n_jobs` from `_resolve_optuna_n_jobs_from_env`, and the existing pass-through tests keep proving the environment value reaches `study.optimize` |
| S-1.10 | N7 | the tests at the ref that test a deleted function's own behavior (`tests/shadow/test_shadow_history.py:1105` for `compute_shadow_hwm`, `:1451` for `compute_v1_bootstrap_state`) and the one that tests a deleted wrapper (`:1262`, `get_eod_shadow_row`) | the GREEN suite runs | the first two are deleted with their functions; the third asserts the same EOD divergence through the live callee `database.load_latest_shadow_row` and keeps reading `eod_day1_divergence` |

Negative coverage: N1 S-1.8 · N2 n/a: a deletion refuses no request · N3 n/a: no value is computed or shown by a removed function on any live path · N4 n/a: no route, hook or permission changes (S-10.4) · N5 n/a: the removed functions hold no state a repeated action could reach · N6 n/a: no live path loses or gains a dependency · N7 S-1.2, S-1.3, S-1.4, S-1.5, S-1.6, S-1.7, S-1.9, S-1.10 · N8 n/a: no edit is held open
Questions: none

### AC-2: The unused root Composer backtest module is gone and the canonical client keeps its tests
Requirement items: R-13, R-27, R-53
Rule: When the GREEN tree is built, `composer_backtest.py` shall not exist, and the three `TestDvmCapitalKeyMismatchFallback` tests shall run against `advisors.composer_backtest_client._extract_returns` from a test file that does not import `composer_backtest`.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-2.1 | B | the GREEN tree | `import composer_backtest` runs in a fresh interpreter at the repository root | it raises `ModuleNotFoundError`; the `pyproject.toml:69` per-file ignore and the `README.md:104` row are gone |
| S-2.2 | N7 | the three tests of `tests/ai_advisor/test_composer_backtest_client.py:760-797` (a single series is used despite a key mismatch; an exact match is preferred among several; several series with no match return empty) | the GREEN suite runs | the same three cases pass against the canonical `_extract_returns` (`advisors/composer_backtest_client.py:140`) and each fails when qa breaks that function's key matching |
| S-2.3 | N7 | the GREEN tree | the canonical client and its fixtures are compared with the ref | `advisors/composer_backtest_client.py`, `tests/fixtures/composer/backtest_inline_v1.json` and `tests/fixtures/composer/backtest_response_v1.json` are byte-unchanged, and the live-client suites that read them pass |
| S-2.4 | N7 | the GREEN tree | `tests/ai_advisor/test_backtest_fold_transform.py:996-1020` runs | it passes: the gate engine imports neither backtest module |
| S-2.5 | N1 | the classes `TestModuleExists`, `TestLiveGuard`, `TestRequestBodyConstruction`, `TestResponseParsing`, `TestErrorHandling` and `TestArchitectureSeparation` (`tests/ai_advisor/test_composer_backtest_client.py:165-759`) | the GREEN tree is built | they are deleted, none moved: the contracts only the root module had (an `is_live` guard raising `RuntimeError`, raising on 429 and 500, log returns at `composer_backtest.py:197`) go with it, and the behavior the canonical client shares (the `/backtest` URL, the tree under `symphony.raw_value`, the Composer auth headers, ISO-date keys, a retry that succeeds after 429; `advisors/composer_backtest_client.py:333-347`) is already pinned by `tests/ai_advisor/test_advisor_backtest_client.py` (`TestSuccessfulParsing` `:321`, `TestRequestContract` `:418`, `TestRetryPolicy` `:495`), which stays green |

Negative coverage: N1 S-2.5 · N2 n/a: no request is refused · N3 n/a: no value is computed or shown · N4 n/a: no permission path · N5 n/a: no state · N6 n/a: the canonical client's Composer handling is unchanged (S-2.3) · N7 S-2.2, S-2.3, S-2.4 · N8 n/a: no edit is held open
Questions: none

### AC-3: The z-score closures and the boolean environment-flag parse each have one definition, with every result unchanged
Requirement items: R-1, R-4, R-15, R-18, R-47, R-51, R-56
Rule: When the engine, the replay, the app's configuration, a dashboard route or the sleeve tick computes a z-score or reads a true or false environment flag at any of the ten sites of Known fact 6, it shall use one shared definition whose result for every input equals what the replaced site produced at the ref, each site keeping its own source, default and `None` guard.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-3.1 | B | the golden inputs of the existing Monte Carlo, CVaR and regime-match suites | `run_monte_carlo`, `compute_portfolio_cvar` and `compute_regime_match_quality` run at GREEN | each output is bitwise equal to the same function's output at the ref on the same input |
| S-3.2 | N1 | the z-score family: std 0.0, std -0.0, std NaN, std positive with values holding NaN, +inf and -inf, an empty array, and a 2-D array | the shared z-score runs | each result equals the ref closure's result elementwise, dtype and shape included (zeros of the input's shape as float for std 0.0 and -0.0) |
| S-3.3 | N7 | the GREEN tree | an AST scan of `math_engine.py` runs | there is exactly one z-score definition, the three functions call it, and no nested function named `_z` remains |
| S-3.4 | B | `LIVE_EXECUTION` in the process environment set to each of the true family (`true`, `True`, `TRUE`, `tRuE`, `1`, `yes`, `Yes`, `YES`), each of the false family (`false`, `False`, `0`, `no`, an empty string, ` true`, `true `, `on`, `y`, `t`, `2`), or absent | `alpha_bot_execution` is imported in a fresh interpreter | `alpha_bot_execution.LIVE_EXECUTION` equals the ref's value for the same environment in every case |
| S-3.5 | B | a `.env` with `LIVE_EXECUTION` set to each value of S-3.4's families, or absent | `GET /` and `GET /api/state` build their page meta (`_build_meta`, `app.py:3639`) | the meta's live-mode output equals the ref's in every case |
| S-3.6 | B | the same `.env` cases, with the market open and with a close snapshot served | `GET /api/state` runs its live branch (`app.py:5642`), its additive fields (`app.py:5465`, the `live_mode` field at `app.py:5474`) and its frozen branch (`app.py:5546`) | the response body equals the ref's in every case |
| S-3.7 | B | the same `.env` cases and a confirmed request | `POST /api/sell_account` runs (`app.py:8826`) | the response and the set of outgoing HTTP requests equal the ref's in every case; URLs, headers and payloads are unchanged |
| S-3.8 | B | the same `.env` cases | `GET /api/symphony-settings/{symphony_name}` runs (`app.py:9625`) | the response body equals the ref's in every case |
| S-3.9 | B | `SLEEVE_LIVE_EXECUTION` set to each value of S-3.4's families, or absent, and a sleeve row with status `LIVE` and with status `PAPER` | `_compute_live_gates` runs (`sleeves/tick_orchestrator.py:187-189`) | the returned pair equals the ref's in every case |
| S-3.10 | N1 | a `.env` line `LIVE_EXECUTION` with no `=` (the mapping holds `None`) | each entry point that reads it unguarded runs (`app.py:3639`, `:5465`, `:5546`, `:5642`, `:8826`, `:9625`) | each answers with the same status and body, or raises the same exception type, as at the ref (Q-9) |
| S-3.13 | B | `SESSION_COOKIE_SECURE` in the process environment set to each value of S-3.4's families, or absent | `app` is imported in a fresh interpreter (`app.py:175`) | `app.config["SESSION_COOKIE_SECURE"]` equals the ref's value in every case, the absent case reading `""` and giving False |
| S-3.14 | B | an armable sleeve, a request that passes every earlier check, and `SLEEVE_LIVE_EXECUTION` in the `.env` set to each value of S-3.4's families, absent, or present with no `=` | `POST /api/sleeves/{sleeve_id}/arm-live` runs (`app.py:9096`) | the response and the sleeve's stored status equal the ref's in every case: the false family, absent and no-value cases answer 400 "SLEEVE_LIVE_EXECUTION is not enabled" and write no status; the true family writes `LIVE` |
| S-3.11 | N7 | the GREEN tree | the no-trade boundary scans and the settings allowlist tests run (`tests/security/test_model_warmup_no_trade_boundary.py`, `tests/security/test_retirement_recommender_no_trade_boundary.py`, `tests/security/test_retirement_action_no_trade_boundary.py`, `tests/security/test_frontrunner_no_trade_boundary.py`, `tests/security/test_a1_settings_allowlist.py`) | all pass, each still fails on its planted forbidden call, and `LIVE_EXECUTION` stays outside `_SETTINGS_WRITE_ALLOWLIST` |
| S-3.12 | N2 | a `POST /api/sell_account` whose `confirm_phrase` is not exactly `LIQUIDATE`, and one with missing credentials | the route runs | it refuses with the same status and body as at the ref and sends no outgoing request; the refusals that precede the edited line are listed in Fail-Closed Behaviour as unreachable by this edit |

Negative coverage: N1 S-3.2, S-3.4, S-3.5, S-3.6, S-3.7, S-3.8, S-3.9, S-3.10, S-3.13, S-3.14 · N2 S-3.12, S-3.14 · N3 S-3.4, S-3.5, S-3.6, S-3.7, S-3.8, S-3.9, S-3.13, S-3.14 (the absent key) · N4 n/a: no authorization path changes; the auth and CSRF hooks are checked in S-10.4 · N5 n/a: each site parses one value per call and holds no state · N6 n/a: neither definition calls a dependency · N7 S-3.1, S-3.3, S-3.11 · N8 n/a: no edit is held open
Questions: Q-8, Q-9, Q-12

### AC-4: The JSON-column row parsers and the latest-shadow-row SQL each have one definition, with each reader's contract unchanged
Requirement items: R-1, R-4, R-17, R-19, R-47
Rule: When `database.py` turns an `advisor_observations` or `frontrunner_proposals` row into a dict, or either latest-shadow-row reader selects a symphony's latest `shadow_history` row for a day, it shall use one shared definition, and each accessor and reader shall return, raise and log exactly what it did at the ref.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-4.1 | B | rows written into a temporary state database by the real writers `insert_advisor_observation` and `insert_frontrunner_proposal`, one per case the parsers distinguish, plus the raw stored values of S-4.2 inserted directly | every accessor that reads `advisor_observations` or `frontrunner_proposals` runs | each returns dicts equal to the ref's for the same rows |
| S-4.2 | N1 | a JSON column holding, as TEXT, a JSON object, a JSON array, a JSON string, a JSON number and unreadable text; SQL NULL; an INTEGER; a REAL; a BLOB holding UTF-8 JSON; and a BLOB that is not JSON | each parser runs | each result equals the ref's: the observation parser turns unreadable text, the INTEGER, the REAL and the non-JSON BLOB into `{}` plus `raw_response_unreadable: True`, parses the JSON BLOB, and adds no such key to a readable row; the frontrunner parser raises the same exception type as at the ref for each case the ref raises on |
| S-4.3 | N7 | the GREEN tree | an AST scan of `database.py` runs | there is exactly one JSON-column parse definition and both remaining parsers use it; exactly one statement text selects the latest `shadow_history` row for a symphony and day, used by both readers |
| S-4.4 | B | `shadow_history` rows built from `tests/fixtures/prod_droplet/shadow_last_rows.json`, `shadow_postclose_rows.json` and `shadow_snapshot_rows.json` | `analytics._load_latest_shadow_row_for_analytics` and `database.load_latest_shadow_row` run, the latter with and without `et_cutoff` | each returns the dict the ref returns |
| S-4.5 | N7 | the analytics reader | it runs with no connection, with a shared connection, twice in one request, and with a query that raises | it opens a read-only connection when none is passed (a write through it fails), opens none when one is passed (`tests/app/test_api_state_connection_count.py` passes), memoizes per request (`tests/app/test_api_state_one_cycle.py` passes), restores `row_factory` after a raise, returns a copy, keeps its name and signature so every existing patch intercepts, and calls `note_build_failure()` and returns None on error |
| S-4.6 | N6 | a missing database file, a locked database and a database without `shadow_history` | each reader runs | the analytics reader returns None and records one build failure; the database reader returns None and logs one ERROR naming `load_latest_shadow_row`, as at the ref |
| S-4.7 | N1 | no row for the day; a row at exactly the cutoff time; a row one second after it | the database reader runs with that `et_cutoff` | None; the row qualifies; the row does not qualify (`<=`, as at `database.py:3681-3682`) |
| S-4.8 | N7 | the GREEN tree | the engine EOD loop (`alpha_bot_execution.py:1068`) and the post-mortem stages (`reporting.py:73`, `reporting.py:180`) run on the S-4.4 rows | they read the same rows as at the ref; `tests/reporting/test_stage2_realized_basis.py`, `tests/telemetry/test_trigger_id_lineage.py` and `tests/dashboard/test_market_mode.py` pass |

Negative coverage: N1 S-4.2, S-4.7 · N2 n/a: no request is refused · N3 S-4.7 (no row) · N4 S-4.5 (the analytics reader cannot write) · N5 S-4.5 (a second read in one request) · N6 S-4.6 · N7 S-4.3, S-4.5, S-4.8 · N8 n/a: no edit is held open
Questions: none

### AC-5: The engine's dip line names the confirmation count in force
Requirement items: R-2, R-30, R-31, R-51
Rule: When an armed, untriggered symphony's below-stop count goes from 0 to 1 in an engine cycle, the engine shall print the dip line naming the confirmation tick count passed to `compute_exit_confirmation` for that symphony on that tick.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-5.1 | B | symphony "Alpha Grove" armed and untriggered, with a `regime_label_cache` row for the session labelled `mean-reverting` | a cycle takes its below-stop count from 0 to 1 | the console line reads `Alpha Grove dipped below stop. Awaiting 5-tick confirmation...` after the unchanged prefix |
| S-5.2 | N3 | the label family: `trending`, `mean-reverting`, `high-vol`, no row, a row dated another session, an unrecognized label string | the same transition happens | the line names 2, 5, 3, 3, 3 and 3 ticks respectively, each equal to `_regime_exit_ticks` on that tick |
| S-5.3 | N7 | any S-5.2 case | the tick runs | the printed count and the `exit_confirm_ticks` argument of `compute_exit_confirmation` come from the same value on that tick |
| S-5.4 | N7 | a recorded sequence of engine cycles with dips, recoveries, triggers and take-profit transitions | it runs at GREEN and at the ref | every `bot_state` transition, exit decision and outgoing order is identical; the recovery line and the take-profit lines (`alpha_bot_execution.py:1550-1553`, `:1591-1597`) are unchanged |
| S-5.5 | N7 | the GREEN tree | the dip line is compared with the ref's | only the count differs: the prefix, the name truncated to 35 characters and `dipped below stop.` are unchanged, and no event, log or database write is added |
| S-5.6 | N5 | a symphony already at count 1 or more | further below-stop ticks run | no further dip line prints, as at the ref |

Negative coverage: N1 S-5.2 (the label family) · N2 n/a: nothing is refused · N3 S-5.2 · N4 n/a: console output only · N5 S-5.6 · N6 n/a: a failed label read returns no label and is the S-5.2 no-row case · N7 S-5.3, S-5.4, S-5.5 · N8 n/a: no edit is held open
Questions: none

### AC-6: No tracked file holds the real Composer account id
Requirement items: R-37, R-38, R-39, R-40, R-50, R-51
Rule: When the GREEN tree is built, no tracked file, read plain or gzip-decompressed, shall contain the real Composer account id or its 8-character prefix, each place that held it shall hold `ACCOUNT_ID_REDACTED`, and every digest or byte count recorded for a changed file shall match the file.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-6.1 | B | the real id read from the PM's local `.env` and never printed | the PM's scan of every tracked file at the PR head runs, decompressing `*.gz` | it reports 0 files holding the id and 0 holding its 8-character prefix, printing counts only |
| S-6.2 | B | the GREEN tree | a CI test scans every tracked fixture field and key that holds an account id (`account`, `account_id`, `accounts_map` keys, account path segments in recorded URLs) | each holds `ACCOUNT_ID_REDACTED` or an invented id recorded as invented in a `PROVENANCE.md` |
| S-6.3 | N7 | every suite that reads a changed fixture | the GREEN suite runs | it passes, because inputs and goldens carry the same placeholder (`tests/app/test_api_state_without_table.py`, the `tests/fixtures/refresh_timing/` suites, the `tests/fixtures/prod_droplet/` consumers) |
| S-6.4 | N7 | the GREEN tree | `tests/fixtures/refresh_timing/test_record_hashes.py` runs and each `PROVENANCE.md` row naming a changed file is read | the sidecar digests and byte counts match, and each row states the new sha256 and names the replacement as a changed field |
| S-6.5 | N1 | the 5 gzip records in `tests/fixtures/refresh_timing/01-c1/` | each is decompressed | the JSON equals the ref's decompressed JSON with the id replaced and nothing else changed |
| S-6.6 | N7 | every changed text file | its ref content with the id (or prefix) replaced by the placeholder is compared with its GREEN content | they are byte-equal, except that in a file written with sorted keys the redacted key sits where sorting places it; no other value or order changed |
| S-6.10 | N7 | the nine `tests/fixtures/state_poll_no_table/` files where the id is an `accounts_map` key serialized with `sort_keys=True` beside a second account key (the derived records `snapshot_two_accounts.json`, `snapshot_missing_defaults.json`, `snapshot_null_defaults.json`, `snapshot_unnamed_entry.json`, and the goldens `golden_ref_closed_frozen_two_accounts.json`, `golden_ref_closed_guard_alpha_fail.json`, `golden_ref_closed_missing_defaults.json`, `golden_ref_closed_null_defaults.json`, `golden_ref_closed_unnamed_entry.json`) | the id is replaced and each file is written key-sorted as before | each file differs from the ref only by the id value and the key-order move sorting forces for the redacted key; it stays key-sorted, so `test_the_derived_records_are_what_the_derivation_writes` (`tests/app/test_api_state_without_table.py:378-382`) passes over the redacted sources; its `PROVENANCE.md` rows state the new digests (Q-5); the five goldens are re-produced by `tests/app/produce_state_poll_golden_ref.py` over the redacted records and that output becomes the golden, as the folder's provenance says, with every non-float value exactly equal to the old golden's and every float within `math.isclose(rel_tol=1e-12, abs_tol=0)` of it, and the record states the largest difference from the old golden; a larger difference is a defect sent back to the round, not a new golden |
| S-6.7 | N7 | the branch's commit messages, the PR body, test output, review records and logs written by the work | the S-6.1 scan runs over them | none holds the id |
| S-6.8 | N1 | a test that compared against the real id (`tests/advisors/test_composer_series_client.py:1202`, `tests/ui/test_static_js_page_harness.py:579`, `tests/ui/test_cycle_1_foundation.py:500`) | the GREEN suite runs | it compares against the placeholder, or against an invented UUID v4 where its assertion needs a UUID-shaped value (Q-7), and passes |
| S-6.9 | N7 | the branch | it is compared with main | it is a fast-forward of main; no commit is rewritten and nothing is force-pushed |

Negative coverage: N1 S-6.5, S-6.8 · N2 n/a: nothing is refused · N3 n/a: no value is computed or shown · N4 n/a: no permission path · N5 n/a: a one-time replacement · N6 n/a: no dependency is called · N7 S-6.3, S-6.4, S-6.6, S-6.7, S-6.9, S-6.10 · N8 n/a: no edit is held open
Questions: Q-5, Q-6, Q-7, Q-11

### AC-7: Three test verdicts stop depending on the machine
Requirement items: R-32, R-33, R-34, R-55
Rule: When `test_a_run_hung_in_the_session_is_ended_after_600_seconds_and_the_next_minute_spawns`, `test_composer_held_for_the_whole_run_does_not_delay_either_engine_spawn` or the heading-margin baseline producer runs, its verdict shall depend only on the code under test, never on machine load or on a developer `.env`.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-7.1 | B | the GREEN daemon | the hung-run test runs once normally and once with every logical core kept busy by other processes | it passes both times and still asserts: no signal inside 600 s, 9 skipped minutes and 1 run inside the bound, terminate between 600 and 661 virtual seconds, no kill, the terminated record's cycle id, a `/health` reason, at least one WARNING, the next run started by 10:11:59 ET, and no skipped record after the ended run |
| S-7.2 | N7 | each planted fault: the 600 s bound not enforced, no run spawned after the end, one minute skipped after the end | the hung-run test runs with and without load | it fails in every case, within its own time limit |
| S-7.3 | B | Composer `total-stats` held | the Composer-held test runs two scheduler passes, once normally and once under S-7.1's load | it passes both times, proving both passes finished while Composer was still held, two engine spawns and one Composer call, with no assertion on elapsed seconds |
| S-7.4 | N6 | a planted fault that runs the account refresh on the scheduler thread | the Composer-held test runs | it fails within its own time limit and does not hang the suite |
| S-7.5 | N5 | the GREEN tree | each of the two tests runs 20 times in a row under load | all 40 runs pass |
| S-7.6 | B | a checkout with a `.env` holding credential values and one with no `.env` | `tests/fixtures/dashboard/heading_margin_baseline/produce_heading_margin_baseline.py` runs in each, and `test_heading_margin_shared_browser.py` runs in each | the producer writes identical records in both, and the test gives the same verdict in both (QA-8) |

Negative coverage: N1 n/a: no input value is judged · N2 n/a: nothing is refused · N3 n/a: no value is computed or shown · N4 n/a: no permission path · N5 S-7.5 · N6 S-7.4 · N7 S-7.2 · N8 n/a: no edit is held open
Questions: Q-11

### AC-8: The regime label job fails only the affected batch on an unusable page token
Requirement items: R-2, R-36, R-55
Rule: If a SIP daily-bars page carries a non-empty `next_page_token` that cannot be checked or used, then the regime label job shall fail only that batch with a reason naming the exception type, and shall fetch and label every other batch as with good pages.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-8.1 | B | two batches; batch 1's first page carries a non-empty list as `next_page_token`; batch 2's pages are good | the job runs | batch 1's tickers fail with reason `TypeError`; batch 2 is fetched; each symphony whose holdings are all in batch 2 is labelled; one line per symphony and one summary line are logged |
| S-8.2 | N1 | the token family: a non-empty list, a non-empty dict, a non-empty set, an empty list, an empty dict, an empty set, an integer, a token seen before on the same batch, None, an empty string, and no token key | page 1 of a batch carries it | the non-empty list, dict and set fail the batch with `TypeError`; the empty containers, None, the empty string and the absent key end the batch at `if not token` (`regime_label_job.py:227`), as at the ref; the integer is sent as `page_token` as at the ref; a repeat fails with `RepeatedPageToken` |
| S-8.3 | N7 | the GREEN tree | a run with only good pages and a run with a transport failure on page 2 | the per-symphony lines, the summary line, the reasons and the written labels equal the ref's |
| S-8.4 | N7 | the GREEN tree | `typing.get_type_hints(regime_label_job._log_summary)` is read and the summary is logged for an empty run and for a run of outcomes | the `outcomes` annotation names a type that supports `len()` and repeated iteration, and both summary lines equal the ref's (CR-5) |
| S-8.5 | N3 | a symphony whose batch failed | the engine reads its label next session | no row is dated that session for it and the engine applies the default count, as at the ref |

Negative coverage: N1 S-8.2 · N2 n/a: nothing is refused · N3 S-8.5 · N4 n/a: off-hours job with no caller · N5 S-8.2 (a repeated token) · N6 S-8.1, S-8.3 · N7 S-8.3, S-8.4 · N8 n/a: no edit is held open
Questions: none

### AC-9: Comments, docstrings, annotations, whitespace and configuration lines state what holds at the ref
Requirement items: R-1, R-26, R-34, R-41, R-42, R-45, R-55
Rule: When the GREEN tree is built, each listed comment, docstring, annotation, blank-line run, agent-file line and ignore rule shall state or do only what holds at the ref's code, and no rendered page, response or log line shall change because of it.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-9.1 | B | `regime_classifier.py` | its module docstring is read | it names its code callers (the autotuner replay, `autotuner.py:1657`, and the nightly label job, `regime_label_job.py:285`), keeps that `alpha_bot_execution.py` and `math_engine.py` never import it, no longer says it is surfaced only as an operator or analytics diagnostic (`regime_classifier.py:2`, `:9-12`), and says `fit_regime_classifier` has no code caller (Q-3) |
| S-9.2 | B | `app.py:5533-5535` | the comment over the close-snapshot sort is read | it states the true reason the sort is done in place, without claiming the held view is shared with `GET /` or that a copy would change the page (INT-1; the hold hands out deep copies, `app.py:4285-4296`) |
| S-9.3 | B | `app.py:5705` and `app.py:4463-4464` | the comments are read | neither names a template reason that does not exist at the ref (`GET /api/state` renders no template); each says where its values are read (`app.py:3312`, `app.py:5542` for the frozen defaults) |
| S-9.4 | N7 | `GET /`, `/history`, `/performance`, `/settings` and `/ai-advisor` | they render at GREEN | the computed styles equal the ref's (the heading-margin capture passes), and each served page differs from the ref only by removed whitespace inside a style block |
| S-9.5 | B | `static/settings-modal.js:4` and the style blocks of `templates/ai_advisor.html`, `templates/history.html`, `templates/performance.html` and `templates/settings.html` | the files are read | the header comment has no line over 100 characters and no doubled space; no style block holds two consecutive blank lines; each comment sits directly above the rule it describes (CR-1, FLASK-1) |
| S-9.6 | B | `tests/app/_state_poll_worlds.py` | its signatures are read | the 19 world builders annotate `monkeypatch` and `tmp_path`, and `_raiser`, `boom` and `StateWorld.get` have return annotations (CR-2) |
| S-9.7 | B | `.claude/agents/flask-dashboard-specialist.md` | it is read | no line names `templates/table_partial.html`; the description still names `app.py` routes, `templates/index.html` and `static/` |
| S-9.8 | B | the GREEN tree | `git check-ignore -v .claude/archive/any-file` runs | the file is ignored, and every ignore rule present at the ref is still present |

Negative coverage: N1 n/a: no input is judged · N2 n/a: nothing is refused · N3 n/a: no value is computed or shown · N4 n/a: no permission path · N5 n/a: no state · N6 n/a: no dependency · N7 S-9.4 · N8 n/a: no edit is held open
Questions: Q-3

### AC-10: Protected surfaces stay unchanged and each content pin moves only over the listed edits
Requirement items: R-12, R-26, R-28, R-31, R-44, R-46, R-48, R-49, R-51
Rule: When the GREEN tree is built, every route response, engine exit decision, autotuner output, scheduler registration and database schema shall equal the ref's, and each content pin on `alpha_bot_execution.py`, `math_engine.py`, `autotuner.py` and `regime_classifier.py` shall be re-taken only when that file differs from the ref by exactly the edits listed here.

Listed edits: `alpha_bot_execution.py`: the `LIVE_EXECUTION` assignment (`:75`), the dip print (`:1547-1549`) and one import for the shared flag parse. `app.py`, `database.py`, `analytics.py`, `regime_label_job.py` and `sleeves/tick_orchestrator.py` carry no content pin; their edits are bounded by the criteria that name them. `math_engine.py`: the removal of `filter_sortino_sentinels` (`:18-30`) and the three `_z` closures (`:1356-1359`, `:1792-1795`, `:2011-2014`) replaced by one module-level definition and its three calls. `autotuner.py`: the removals of `:284-295`, `:623` with its body, `:2750` and the comment at `:2742-2745`, and the seed read at `:2746` taking `_OPTUNA_SAMPLER_SEED_ENV` in place of its literal. `regime_classifier.py`: the module docstring only.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-10.1 | B | the GREEN tree | each of the four files is compared with the ref, hunk by hunk | its hunks are exactly its listed edits, and the pins at `tests/test_mdd_window_truth_zero_diff_guard.py:72-73`, `tests/engine/test_close_snapshot_builder_scope.py:28` and `tests/test_regime_label_protected_files.py:35-49` hold the GREEN digests with a comment naming this plan (Q-1) |
| S-10.2 | N1 | one byte changed outside the listed edits in each pinned file | the pin tests run | each fails |
| S-10.3 | N7 | `synthetic_history.py` and `migrations/027_regime_label_cache.sql` | the regime pin test runs | their digests are the ref's, not re-taken |
| S-10.4 | N4 | the Flask app and `run_scheduler` | the URL map and the registrations are listed | every rule, method and endpoint, `_AUTH_EXEMPT_ENDPOINTS`, `_SETTINGS_WRITE_ALLOWLIST`, the auth and CSRF hooks, and every slot time and callable equal the ref's |
| S-10.5 | N7 | a fresh database | the migrations run | the migration files equal the ref's, and every `sqlite_master` entry's SQL equals the ref's |
| S-10.6 | N7 | the GREEN tree | the engine suites (`tests/execution/`, `tests/engine/`, `tests/math_engine/`) and the `LIVE_EXECUTION` reference check (`tests/test_mdd_window_truth_zero_diff_guard.py:118-131`) run | all pass |
| S-10.7 | N7 | the GREEN tree | `_collect_sim_returns`, `_collect_sim_returns_dated` and `run_simulation` are compared by AST with the ref, and the replay parity suites run (`tests/autotuner/test_ac6_bar_level_replay_production_parity.py`, `tests/autotuner/test_c3_replay_exit_parity.py`) | the three functions are identical and the suites pass |
| S-10.8 | N7 | the GREEN tree | `regime_classifier.py` is compared by AST with the ref, its module docstring excluded | identical |
| S-10.9 | N7 | the GREEN tree | the route golden suites run (`tests/app/test_api_state_without_table.py` and the `GET /` render suites) and `/ai-advisor/accept` and `/ai-advisor/reject` write an `llm_suggestions` row | every response equals its golden, and each written row equals the ref's |
| S-10.10 | N7 | the GREEN tree | `database.get_or_create_phase15_m3_bundle_id`, its constants and its cache are compared with the ref | byte-identical (NB-7) |

Negative coverage: N1 S-10.2 · N2 n/a: no refusal changes; the panic-sell refusals are S-3.12 · N3 n/a: no value is computed or shown · N4 S-10.4 · N5 n/a: no state · N6 n/a: no dependency · N7 S-10.1, S-10.3, S-10.5, S-10.6, S-10.7, S-10.8, S-10.9, S-10.10 · N8 n/a: no edit is held open
Questions: Q-1

## Rule-by-Entry-Point Matrix
| Rule | Engine import (`alpha_bot_execution.py:75`) | App import, session cookie flag (`app.py:175`) | Page meta for `GET /`, `GET /api/state` and the other pages (`app.py:3639`) | `GET /api/state` additive, live and frozen (`app.py:5465`, `:5642`, `:5546`) | `POST /api/sell_account` (`app.py:8826`) | `POST /api/sleeves/{sleeve_id}/arm-live` (`app.py:9096`) | `GET /api/symphony-settings/{symphony_name}` (`app.py:9625`) | Sleeve tick (`sleeves/tick_orchestrator.py:187-189`) |
|---|---|---|---|---|---|---|---|---|
| AC-3 flag parse, true, false and absent families | S-3.4 | S-3.13 | S-3.5 | S-3.6 | S-3.7 | S-3.14 | S-3.8 | S-3.9 |
| AC-3 flag parse, key present with no value | n/a: `os.getenv` never returns None for a set variable | n/a: `os.environ.get` never returns None for a set variable | S-3.10 | S-3.10 | S-3.10 | S-3.14 (the `or "False"` guard) | S-3.10 | n/a: `os.getenv` never returns None for a set variable |

## Not Building
Non-goals:
- NB-1: No question is put to the operator; every open item carries the PM's default and the PM decides it, and the PM's rulings are recorded as [PM-ASSUMED] decisions (R-3, R-54).
- NB-2: No visible UI change; ux-expert UX-1 to UX-5 and the other UI slice items stay in that slice (R-35, R-45).
- NB-3: No security fix S-9, S-10, S-11 or S-17 (R-43).
- NB-4: No Cash Now, no tenure on-demand market gate and no HYG-017; the relative `.env` reads at `app.py:5641` and `app.py:8825` stay (R-29, R-43).
- NB-5: No change to `VWAP_BLEED_MULTIPLIER` or `VWAP_BLEED_TICKS` (R-43).
- NB-6: No git history rewrite and no force push for S-7 (R-38).
- NB-7: `get_or_create_phase15_m3_bundle_id`, `_M3_R1_*`, `_M3_R2_*` and `_phase15_m3_bundle_id_cache` stay unchanged: the M3 decision record requires the facets to be persisted in `spec_bundles` and no production path calls the helper, so this is a missing wire, confirmed by the PM's droplet read (0 rows for both facets) and tracked by the PM as its own cycle, not built here (R-14; D-5; Q-4).
- NB-8: HYG-014 needs no change: both sort-key pairs are gone at the ref (R-16; Known fact 4).
- NB-9: No finite-float helper is extracted: no two of the eight share semantics (R-20; D-8).
- NB-10: The dead `return_dates=True` branch of `_collect_sim_returns` (`autotuner.py:1467-1553`) stays, because it lives in the replay (excludes R-22; approved [PM-ASSUMED]: Q-2).
- NB-11: `regime_classifier.fit_regime_classifier` stays (excludes R-23; approved [PM-ASSUMED]: Q-3).
- NB-12: The sentinel filter in `compute_sortino_se_bootstrap` (`autotuner.py:890-896`, X-9) and the constant copy in `scripts/derive_h1_bootstrap_fixture.py:41` (X-10) stay (R-25; D-9).
- NB-13: The identical scalar lines `today_ret_z = ...` (`math_engine.py:1363`, `:1799`, `:2018`) are not consolidated; HYG-003 names the `_z` closures only.
- NB-14: INT-2, QA-1 to QA-7 and QA-AC-1 to QA-AC-7 build nothing (narrows R-34 and R-36; approved [PM-ASSUMED]: Q-10).
- NB-15: The `AttributeError` on a `.env` line `LIVE_EXECUTION` with no `=` stays as at the ref; its fix belongs to the controls slice (Q-9).

Behavior that stays exactly as it is:
- NB-16: Every route response, engine exit decision, autotuner output and database schema (R-46; S-10.4 to S-10.9).
- NB-17: The `llm_suggestions` table, `database.record_llm_suggestion` and the two routes that write it (R-12; S-10.9).
- NB-18: The replay: `_collect_sim_returns`, `_collect_sim_returns_dated` and `run_simulation` are identical (R-28; S-10.7).
- NB-19: `regime_classifier.py` outside its module docstring (R-26; S-10.8).
- NB-20: `LIVE_EXECUTION` and per-symphony `live_mode` gating; `is_live` is never a default; neither `LIVE_EXECUTION` nor any credential is settings-writable; the panic-sell route and the Managed Sleeves order path send what they sent at the ref (S-3.7, S-3.9, S-3.11).

Deferred:
- NB-21: The security, controls, engine VWAP, UI and replay-fidelity slices, and worktree removal by the PM (R-43, R-52).
- NB-22: Other tests that assert on elapsed wall-clock time, found by the DC-9 search; this plan fixes the two named tests only.

Protected surfaces this consumes but does not change:
- NB-23: Composer and Alpaca client wire behavior; `advisors/composer_backtest_client.py` is byte-unchanged (R-53; S-2.3).
- NB-24: `synthetic_history.py` and `migrations/027_regime_label_cache.sql` (S-10.3).
- NB-25: The existing boundary tests stay green: `tests/security/test_model_warmup_no_trade_boundary.py`, `tests/security/test_retirement_recommender_no_trade_boundary.py`, `tests/security/test_retirement_action_no_trade_boundary.py`, `tests/security/test_frontrunner_no_trade_boundary.py`, `tests/security/test_a1_settings_allowlist.py` and `tests/test_mdd_window_truth_zero_diff_guard.py:118-131` (S-3.11, S-10.6).
- NB-26: No repository other than AlphaBotPM changes (R-44).
- NB-27: Earlier plans and approach documents that quote fixture digests are not rewritten; only the `PROVENANCE.md` beside each changed fixture is.

## Defect Classes
| DC-n | Class (what goes wrong) | Trigger (B or N1-N8) | Sibling search (tool, query, scope) | Scenarios | Raised in review as |
|---|---|---|---|---|---|
| DC-1 | A deleted name is still reached through string dispatch, `getattr`, `importlib`, a scheduler registration or a `mock.patch` target | N7 | grep: each removed name with `-w` over every tracked file except `docs/` and `doc-archive/`; grep: `getattr\((database\|analytics\|autotuner\|math_engine\|regime_classifier)` and `importlib.import_module` over production `*.py`; grep: `patch(` and `patch.object(` targets over `tests/`; grep by value: each removed name's string literals and constant values (for example `"OPTUNA_N_JOBS"`, `"OPTUNA_SAMPLER_SEED"`) over `tests/`, to find pins that match by value or by `dir()` rather than by name | S-1.1, S-1.2, S-1.5, S-1.8, S-1.9, S-1.10, S-2.1 | - |
| DC-2 | A narrow characterization licenses unlisted siblings of an input family | N1 | read: every input the replaced site accepts (env spellings, std values, JSON column kinds, token types, label values) against the scenario families | S-3.2, S-3.4, S-3.10, S-4.2, S-5.2, S-8.2 | - |
| DC-3 | A shared definition used at one entry point and a different result at another | N2 | grep: `git grep -nE '\.lower\(\)( \|\.strip\(\))* *in *[\(\{\[]' -- '*.py'` (matches the multi-line form too) and `os.getenv(` or `os.environ.get(` with a boolean default over every tracked `*.py`; one matrix column per hit | S-3.4, S-3.5, S-3.6, S-3.7, S-3.8, S-3.9, S-3.10, S-3.13, S-3.14 | - |
| DC-4 | Data or decisions that must stay unchanged change on a behavior-identical edit | N7 | read: every write, order and decision on the engine cycle path and the autotuner path after each edit; diff: `git diff 7adc2c56 -- alpha_bot_execution.py math_engine.py autotuner.py regime_classifier.py` | S-3.1, S-3.7, S-5.4, S-10.1, S-10.6, S-10.7 | - |
| DC-5 | A changed fixture or removed reader not carried to every consumer (goldens, sidecars, PROVENANCE tables, writer tests) | B | grep: each changed fixture file name over `tests/`; read: each `*.capture.json` and `PROVENANCE.md` under `tests/fixtures/`; grep: tests that call a removed reader | S-1.4, S-6.3, S-6.4 | - |
| DC-6 | A dependency failure escapes its isolation boundary or is answered with stale data | N6 | read: every raise after the `try` in `regime_label_job._fetch_batch` and `fetch_sip_daily_closes`; read: the error paths of both shadow-row readers | S-4.6, S-8.1, S-8.3 | - |
| DC-7 | Boundary or edge state mishandled | N1 | read: comparisons on `std`, `et_cutoff` and the token check in the touched functions, and in their tests | S-3.2, S-4.7, S-8.2 | - |
| DC-8 | A sensitive value survives in a compressed, derived or written artifact | N7 | script: scan every tracked file plain and gzip-decompressed, commit messages and review records for the id and its 8-character prefix, printing counts only | S-6.1, S-6.5, S-6.7 | - |
| DC-9 | A test verdict depends on machine load or a developer `.env` | N5 | grep: `perf_counter()`, `time.monotonic()` and `time.time()` followed by `assert` on an elapsed value over `tests/`; grep: `os.environ.pop` after `import app` over `tests/` | S-7.1, S-7.3, S-7.5, S-7.6 | - |
| DC-10 | A comment or docstring states a reason that does not hold in the code it describes | B | grep: `template` in comments inside `get_state`; read: every docstring that names callers of a touched module | S-9.1, S-9.2, S-9.3 | - |

## Worked Example
Engine console, mean-reverting session (S-5.1, S-5.3): symphony "Alpha Grove" is armed and untriggered, its `regime_label_cache` row for today reads `mean-reverting`, so `_regime_exit_ticks` is 5. At 10:41 ET its return falls below `stop_trigger_level - MAGNITUDE_FLOOR_PCT` with the Monte Carlo gate allowing the exit; `compute_exit_confirmation` returns count 1 and no hit. The console line after the unchanged warning prefix reads `Alpha Grove dipped below stop. Awaiting 5-tick confirmation...` where the ref printed `Awaiting 3-tick confirmation...`. The count reaches 5 at the fifth consecutive qualifying tick and the stop fires then, exactly as at the ref.

Failure case, regime job (S-8.1): the 04:30 run fetches two SIP batches. Batch 1's first page answers 200 with bars and the non-empty list `"next_page_token": ["a1"]`. At the ref the membership check raised `TypeError` outside the batch's parse guard, the run-level handler marked every ticker of every batch as failed, and no symphony was labelled. At GREEN only batch 1's tickers fail with reason `TypeError`; batch 2's symphonies are labelled; the summary line counts both outcomes.

## Fail-Closed Behaviour
- An unusable non-empty `next_page_token` (list, dict, set) fails its batch with reason `TypeError` at the token check in `regime_label_job._fetch_batch` (`regime_label_job.py:224-231`); the job writes no label for that batch's symphonies and the engine applies the default count (S-8.1, S-8.2, S-8.5). The existing refusals stay as they are: `RepeatedPageToken` (`regime_label_job.py:229-230`), `PageLimitExceeded` (`regime_label_job.py:233`) and the transport failures of `_get_page` (S-8.3).
- `POST /api/sell_account` keeps its refusals: a `confirm_phrase` other than `LIQUIDATE` answers 400 (`app.py:8820-8823`), and missing credentials (`app.py:8828-8829`) answer as at the ref (S-3.12). Unreachable by this plan's edit, because each refuses before the edited line `app.py:8826`: the auth hook's 302, 401 or 503 (`app.py:273`), the CSRF hook's 403 (`app.py:648`), the body-size 413 (`app.py:591`), the framework's refusal of a non-JSON or malformed body at `request.json` (`app.py:8806`), 400 "confirm_account_id is required" (`app.py:8812-8813`), 400 "confirm_account_id does not match account_id" (`app.py:8814-8817`) and 400 "confirm_phrase is required" (`app.py:8818-8819`).
- `POST /api/sleeves/{sleeve_id}/arm-live` keeps its refusals: 400 "SLEEVE_LIVE_EXECUTION is not enabled" (`app.py:9101-9102`) with no status written (S-3.14). Unreachable by this edit, because each refuses before `app.py:9096`: the auth, CSRF and body-size refusals above, 404 for an unknown sleeve (`app.py:9042-9043`), the confirm-id and confirm-phrase 400s (`app.py:9050-9065`) the framework's refusal of a non-JSON or malformed body at `request.json` (`app.py:9045`) and the missing-live-keys refusal (`app.py:9087-9095`).

## Authorities Touched
| Value | Authority used | What it replaced | What proves no second source |
|---|---|---|---|
| A z-score of an array | one module-level definition in `math_engine.py` | three nested `_z` closures (`math_engine.py:1356`, `:1792`, `:2011`) | S-3.3 |
| A true or false environment flag | one shared parse definition | ten inline `.lower() in ("true", "1", "yes")` expressions (Known fact 6) | S-3.3 extended by the DC-3 grep; the matrix rows |
| A JSON column of an advisor or frontrunner row | one shared parse definition in `database.py` | `_parse_advisor_observation_row` and `_parse_frontrunner_proposal_row` bodies | S-4.3 |
| The latest `shadow_history` row for a symphony and day | one statement text | the SQL at `analytics.py:703-705` and `database.py:3685-3688` | S-4.3 |

## Contract Changes and Consumers
none: no route, wire shape, table or fixture contract changes (G-54); fixtures change only the account-id value (AC-6), and removed functions have no production consumer (S-1.2).

## Sibling Repos and Landing Order
none: AlphaBotPM only (G-55). The controls slice also edits `app.py:5641-5642` and `app.py:8825-8826` (HYG-017); whichever lands second rebases (Q-8).

## Migrations Required
none: no schema or migration change (G-54; S-10.5).

## Configuration and Enablement
none: no configuration key is added or changed; `.gitignore` gains one ignore rule (S-9.8).

## Breaking Changes
none for any tracked consumer. An untracked operator script that imports `composer_backtest` or a removed function fails at import; no such script is visible in the repository (Known-Unconfirmed 2).

## Security Considerations
- Data exposure (sensitive-data, AC-6): the real Composer account id leaves the tracked tree and must not enter any artifact of this work (S-6.1, S-6.7); history is not rewritten (S-6.9), so the id remains in earlier commits by the PM's ruling (G-36).
- Input validation (AC-8): a malformed SIP page token is isolated to its batch (S-8.1, S-8.2).
- Authorization and access control (security, AC-3): unchanged; the URL map, auth and CSRF hooks and the settings allowlist equal the ref's (S-10.4, S-3.11), and the auth cookie's `SESSION_COOKIE_SECURE` flag resolves as at the ref for every spelling (S-3.13).
- Live-trade gates (money, AC-3, AC-10): `LIVE_EXECUTION` and `SLEEVE_LIVE_EXECUTION` resolve exactly as at the ref at all nine sites that read them, and the session cookie flag at `app.py:175` likewise (S-3.4 to S-3.10, S-3.13, S-3.14), and the panic-sell route sends the same requests (S-3.7).
- Client-side rendering: unchanged (S-9.4). Abuse and rate limits: the regime job's paging bounds are unchanged (S-8.3).

## Prerequisites
The shared browser set up by `scripts/browser-sign-in` for the heading-margin capture (S-7.6, S-9.4); without it that capture cannot run and the capturer stops at its first browser call. The PM's local `.env` holds the real account id for the S-6.1 scan.

## Design-System Mapping
none: no visible element changes; the template edits remove whitespace inside style blocks only (S-9.4, S-9.5).

## Definition of Done (Verification)
| AC | Scenarios | Test level | Real-entrypoint test |
|---|---|---|---|
| AC-1 | S-1.1, S-1.2, S-1.3, S-1.4, S-1.5, S-1.6, S-1.7, S-1.8, S-1.9, S-1.10 | AST and text scans, unit, suite collection | `run_autotuner` creating its study (S-1.6); the writer tests through the real database accessors (S-1.4) |
| AC-2 | S-2.1, S-2.2, S-2.3, S-2.4, S-2.5 | import check, unit | a fresh interpreter importing the module (S-2.1) |
| AC-3 | S-3.1, S-3.2, S-3.3, S-3.4, S-3.5, S-3.6, S-3.7, S-3.8, S-3.9, S-3.10, S-3.11, S-3.12, S-3.13, S-3.14 | characterization against the ref's code, route tests through the Flask test client, subprocess import | the eight entry points of the matrix, each through its real route, import or tick function |
| AC-4 | S-4.1, S-4.2, S-4.3, S-4.4, S-4.5, S-4.6, S-4.7, S-4.8 | characterization against the ref, temporary state database | `GET /api/state` through the test client (S-4.5) and the engine EOD loop (S-4.8) |
| AC-5 | S-5.1, S-5.2, S-5.3, S-5.4, S-5.5, S-5.6 | engine integration with a temporary state database, Composer and Alpaca faked at the HTTP layer | real engine cycles through `alpha_bot_execution.main`, capturing stdout |
| AC-6 | S-6.1, S-6.2, S-6.3, S-6.4, S-6.5, S-6.6, S-6.7, S-6.8, S-6.9, S-6.10 | repository scans, fixture integrity tests | the PM's scan at the PR head (S-6.1) and the fixture consumers' suites (S-6.3) |
| AC-7 | S-7.1, S-7.2, S-7.3, S-7.4, S-7.5, S-7.6 | the named tests under imposed load, the producer in two checkouts | the real scheduler loop and run slot of the named tests; the producer script |
| AC-8 | S-8.1, S-8.2, S-8.3, S-8.4, S-8.5 | unit with the Alpaca HTTP layer faked per page | `run_regime_label_job` invoked as the 04:30 slot invokes it |
| AC-9 | S-9.1, S-9.2, S-9.3, S-9.4, S-9.5, S-9.6, S-9.7, S-9.8 | text checks, signature checks, browser capture, `git check-ignore` | the five pages rendered in the shared browser (S-9.4) |
| AC-10 | S-10.1, S-10.2, S-10.3, S-10.4, S-10.5, S-10.6, S-10.7, S-10.8, S-10.9, S-10.10 | content pins, AST comparison, migration run, route goldens | the Flask URL map and `run_scheduler` registrations (S-10.4); the route goldens (S-10.9) |

Gates: `ruff format --check`, `ruff check`, a scoped pytest run (never `-n auto` and never more than 4 workers) over `tests/execution/`, `tests/engine/`, `tests/math_engine/`, `tests/autotuner/`, `tests/database/`, `tests/analytics/`, `tests/shadow/`, `tests/reporting/`, `tests/telemetry/`, `tests/security/`, `tests/ai_advisor/`, `tests/advisors/`, `tests/app/`, `tests/dashboard/`, `tests/ui/`, `tests/fixtures/`, `tests/test_mdd_window_truth_zero_diff_guard.py`, `tests/test_regime_label_protected_files.py` and the new tests; CI green on the exact PR head SHA; `/review` on the change; then the PM's live functional check on the droplet after the review: the daemon restarted on the merged SHA, `GET /`, `GET /api/state` and `GET /api/symphony-settings/{symphony_name}` answer as before, the engine console shows a dip line naming the count of that symphony's label when a dip occurs, the next 04:30 regime run logs its summary line, and the S-6.1 scan reports 0 files. Tests-green is necessary and never sufficient.

Fixture provenance: no new captured fixture is needed except one page record for S-8.1, derived from the captured Alpaca bars page the regime slice committed by changing only `next_page_token`, recorded in that folder's `PROVENANCE.md` as derived. The redacted fixtures are the existing captured and PM-signed records with the id value replaced, their `PROVENANCE.md` rows re-stated (S-6.4). Characterization expectations come from running the ref's own code (`git show 7adc2c56:PATH` loaded in the test, or a capture recorded from it) on the same input, never from hand-typed literals. The z-score and Monte Carlo inputs are the existing golden fixtures under `tests/fixtures/math/`. The invented UUID v4 values of S-6.8 are recorded as invented in the relevant `PROVENANCE.md`.

## Success Measures
none: a hygiene change has no in-use measure beyond the Definition of Done; its effect is read in the gates and the PM's live check.

## Known-Unconfirmed and Standing Risk
1. removed: settled. The PM's read-only droplet query (`SELECT bundle_hash, facet_name FROM spec_facets WHERE facet_name IN ('time_squeeze_decay_curve_v2','vwap_system_a_hwm_gate_v2');`) found the tables present and 0 rows for both facets (Q-4).
2. An untracked operator script or REPL habit that imports a removed name cannot be seen; it would fail at import after merge.
3. QA-8's evidence (four Advisor cases of `test_heading_margin_shared_browser.py` fail in a checkout without `.env`) is the qa reviewer's and was not re-run for this spec; whether the committed baseline must be re-captured depends on it (Q-11).
4. The single CI failure of the hung-run test is not reproduced here; the inferred cause is that the post-end ticks at `tests/app/test_engine_run_bound.py:228-238` advance the virtual clock without waiting for the engine thread, which a loaded machine exposes.
5. The account-id counts (71 files with the id, 7 with its prefix) are at the ref; a fixture merged to main before this branch lands can add more, so S-6.1 runs at the PR head.
6. A test that compares served template bytes would see the S-9.5 whitespace removal; none was found by grep, which is not proof.
7. The controls slice edits the lines next to two HYG-007 sites; the second slice to land rebases (Q-8).
8. The ten-site count of Known fact 6 comes from a multi-line-aware grep; a site that builds the tuple elsewhere or uses a set would still escape it, so the DC-3 search is re-run at the PR head.
9. removed: settled by the PM's R-5 ruling. Redacting the id moves an account within the sorted `accounts_map`, which can reorder the summation in `_frozen_symphony_inputs` (`app.py:4430`). GREEN re-produces the five goldens with `tests/app/produce_state_poll_golden_ref.py` over the redacted records, and that output becomes the golden; non-float values must equal the old golden's exactly, floats must be within `math.isclose(rel_tol=1e-12, abs_tol=0)`, the record states the largest difference, and a larger difference is a defect sent back to the round (S-6.10, D-15).

## Open Questions
| Q-n | Question | Blocks (AC ids or "plan") | Proposed default | Answer |
|---|---|---|---|---|
| Q-1 | `tests/test_regime_label_protected_files.py` pins `math_engine.py`, `regime_classifier.py`, `autotuner.py` and the engine minus the regime read, and says to stop and ask the PM before re-taking a digest; this plan's edits fail it. May it be re-taken? | AC-10 | Yes, under the same rule as the two pins the intake names: only when each file's diff is exactly the listed edits (S-10.1), with its docstring naming this plan; `synthetic_history.py` and migration 027 stay pinned (S-10.3) | Default ruled by the PM. [PM-ASSUMED] |
| Q-2 | X-3, the dead `return_dates=True` branch, lives in `_collect_sim_returns`, the replay; the intake says no change to the replay. Is it removed here? | AC-1, AC-10 | No: it stays (NB-10) and goes to the replay-fidelity cycle as a Follow-Up | Default ruled by the PM. [PM-ASSUMED] |
| Q-3 | X-4, `fit_regime_classifier`, is a documented standalone operator diagnostic (`regime_classifier.py:37-40`) with no code caller, and the HYG-020 ruling limits `regime_classifier.py` to its docstring. Is it removed? | AC-9, AC-10 | No: it stays (NB-11), and the docstring states that no code calls it (S-9.1) | Default ruled by the PM. [PM-ASSUMED] |
| Q-4 | HYG-005: the M3 decision record is the completed plan `feature-plans/decision-science.completed/phase-1.5/m3-redrive-provenance-gaps/plan.md` (no M3 entry exists in `DECISIONS.md`); it requires the facets persisted in `spec_bundles` (`:79`, `:109`) and nothing in production calls the helper. Missing wire or finished artifact? | plan | Missing wire under the PM's ruling: the helper stays unchanged and the PM receives the report (NB-7, D-5) | Default ruled by the PM: the PM's read-only droplet query found `spec_bundles` and `spec_facets` present and 0 rows for the two M3 facets, so the wire is missing; the PM tracks it as its own cycle (Follow-Ups). [PM-ASSUMED] |
| Q-5 | The id is in 71 tracked files, 5 of them gzip records, and digests of changed files are recorded in sidecars and `PROVENANCE.md` tables; `DECISIONS.md:11117` holds it in an append-only log. Is every file, the gzip records and `DECISIONS.md` included? | AC-6 | Yes: every file, the gzip records decompressed and re-compressed, every recorded digest re-stated, and `DECISIONS.md` redacted in place because a redaction is not a decision change | Default ruled by the PM. [PM-ASSUMED] |
| Q-6 | Seven tracked files hold only the id's first 8 characters (`.design-handoff/project/mock-data.js`, `docs/handoff/V3-AUDIT.md`, two `docs/handoff/VERIFY-behavioral-*` files, `docs/handoff/cycle-1-contract.md`, `docs/research/dashboard/composer-per-symphony-stats.md`, `tests/ui/test_cycle_1_foundation.py:500`). Are they included? | AC-6 | Yes: the fragment is replaced too, so no tracked file holds any part of the id | Default ruled by the PM. [PM-ASSUMED] |
| Q-7 | Some tests may need a UUID-shaped account value for their assertion to mean anything. Placeholder there too? | AC-6 | The placeholder everywhere, except where the test-writer shows an assertion depends on the UUID shape; there an invented UUID v4, recorded as invented, as `tests/fixtures/state_poll_no_table/PROVENANCE.md:13` already does | Default ruled by the PM. [PM-ASSUMED] |
| Q-8 | Two HYG-007 sites (`app.py:5642`, `app.py:8826`) are the lines after the HYG-017 reads moved to the controls slice because they touch `POST /api/sell_account`. Are they in this plan? | AC-3 | Yes: all six sites (extended to ten by Q-12), behind the sell_account characterization (S-3.7, S-3.12); the slice that lands second rebases | Default ruled by the PM. [PM-ASSUMED] |
| Q-9 | A `.env` line `LIVE_EXECUTION` with no `=` makes the six unguarded `app.py` sites (`:3639`, `:5465`, `:5546`, `:5642`, `:8826`, `:9625`) raise `AttributeError`. Keep it? | AC-3 | Keep it exactly (the intake requires each site's behavior unchanged, S-3.10); fixing it is a Follow-Up | Default ruled by the PM; the fix belongs to the controls slice, which owns the same route with Cash Now and HYG-017. [PM-ASSUMED] |
| Q-10 | QA-1 to QA-7 and QA-AC-1 to QA-AC-7 are SATISFIED acceptance rows with no defect, and INT-2 is the unrouted `.design-handoff` prototype whose auditor wrote "None needed". Anything to build? | AC-9 | No (NB-14, D-11) | Default ruled by the PM. [PM-ASSUMED] |
| Q-11 | The intake says fixtures change only the account-id value, while X-7 deletes orphan fixtures and the QA-8 fix may change the committed heading-margin baseline. Which wins? | AC-6, AC-7 | Both listed items stand: X-7's orphan fixtures are deleted, and the baseline is re-captured with no credentials only if the GREEN producer's records differ from the committed ones; no other fixture value changes | Default ruled by the PM: the account-id-only line limits value edits to live fixtures; it never meant to keep fixtures nothing reads, or a baseline whose producer this plan fixes. [PM-ASSUMED] |
| Q-12 | The parse appears at ten production sites, not the six HYG-007 named: `app.py:175` (session cookie flag), `app.py:5465`, `app.py:5546` (`GET /api/state`) and `app.py:9096` (sleeve arm-live) wrap the tuple over several lines. Are the four in this plan? | AC-3 | Yes: all ten share the one definition, each keeping its own source, default and `None` guard, behind the characterizations S-3.6, S-3.13 and S-3.14 and the matrix | Default ruled by the PM: the shared definition takes the raw value, and each site keeps its own read, default and missing-value handling, so every site behaves as at the ref, including the raise on a bare key (Q-9); the live-mode resolution of both trade routes and the cookie flag are characterized before the edit. [PM-ASSUMED] |

## Decisions
| ID | Decision | Rationale |
|---|---|---|
| D-1 | [PM-ASSUMED, PM ruling] HYG-002 stays; the replay is not changed | The duplicate is intentional and documented: `_collect_sim_returns_dated` is deliberately not a wrapper so that mocks of `_collect_sim_returns` do not intercept it (`autotuner.py:1672-1675`); the replay-fidelity cycle owns the replay |
| D-2 | [PM-ASSUMED, PM ruling] HYG-020: `regime_classifier` stays; only its docstring changes | `classify_regime` is live in the replay (`autotuner.py:1657`) and the nightly label job (`regime_label_job.py:285`) |
| D-3 | [PM-ASSUMED, PM ruling] HYG-017 moves to the controls slice | It touches `POST /api/sell_account` (`app.py:8825`) |
| D-4 | [PM-ASSUMED, PM ruling] HYG-018: move, then delete | `TestDvmCapitalKeyMismatchFallback` imports the canonical `_extract_returns` (`tests/ai_advisor/test_composer_backtest_client.py:770`); the other classes pin either contracts only the dead module had or behavior `tests/ai_advisor/test_advisor_backtest_client.py` already pins for the canonical client (S-2.5) |
| D-5 | [PM-ASSUMED, PM ruling applied] HYG-005 is a missing wire, reported and not built | The M3 plan's deliverables and done criteria require a persisted `spec_bundles` row for the M3 facets (`feature-plans/decision-science.completed/phase-1.5/m3-redrive-provenance-gaps/plan.md:79`, `:109`); `get_or_create_phase15_m3_bundle_id` (`database.py:2319`) has no production caller, while the phase-1 bundle is registered by the engine (`alpha_bot_execution.py:1189`) and `app.py:8241` |
| D-6 | [PM-ASSUMED, PM ruling] HYG-009: only the two readers go; the ad hoc SQL question is closed by code reachability | The table is written live (Known fact 3) |
| D-7 | HYG-014 needs no work | Both pairs were removed with the `/api/state` table (Known fact 4) |
| D-8 | HYG-006 extracts nothing | Each pair differs: `database._finite_or_none` passes finite non-floats through; `analytics._is_finite_number` accepts numeric strings and bools while `app._is_finite_number` rejects both; the two `_json_safe_float` copies differ on non-numeric input (`app.py:6239` raises, `app.py:6679` passes through); `advisors/tenure_review.py:509` rejects bools; `advisors/frontrunner_acceptance.py:234` keeps NaN and infinity; `regime_classifier.py:190` works on a series |
| D-9 | X-9 and X-10 stay | X-9 filters bootstrap floats against the canonical `math_engine._SORTINO_SENTINEL` (`autotuner.py:890-896`), the one float-list filter left once HYG-001 goes; X-10 is an oracle script that by design never imports production code (`scripts/derive_h1_bootstrap_fixture.py:1-9`, `:39-41`) |
| D-10 | A name that only a removed function used goes with it | The intake's "the code carries none of the dead functions" would otherwise leave dead helpers behind (`_parse_llm_suggestion_row`, `_LLM_SUGGESTION_COLUMNS`); a name an existing test pins by value stays and is made live instead (`_OPTUNA_SAMPLER_SEED_ENV`, D-13) |
| D-11 | Review rows with no defect build nothing | QA-1 to QA-7 and QA-AC-1 to QA-AC-7 report SATISFIED criteria; INT-2 concerns the unrouted `.design-handoff` prototype, and its auditor recorded no fix needed |
| D-13 | [PM-ASSUMED, PM ruling] The seed constant becomes live and the `n_jobs` source-literal pin is replaced | `_n_jobs_raw` exists only to satisfy a source-literal test while the value used comes from `_resolve_optuna_n_jobs_from_env()` (`autotuner.py:2751`); reading the seed through `_OPTUNA_SAMPLER_SEED_ENV` keeps the named-constant test true for the reason it was written |
| D-14 | [PM-ASSUMED, PM ruling] A key-sorted fixture changes only by the id value and the key-order move sorting forces | The derived-record byte check (`tests/app/test_api_state_without_table.py:378-382`) compares canonical sorted bytes, so a sorted file must stay sorted |
| D-12 | The tests that test a deleted function's own behavior are deleted with it, and fixtures and fixture keys nothing reads afterward are deleted with them | The PM's Q-11 ruling: the account-id-only line never meant to keep fixtures nothing reads (`tests/fixtures/shadow/v1_bootstrap_gate.json`, read only at `tests/shadow/test_shadow_history.py:1453`; the `shadow_hwm_day1` keys, read only at `:1110-1112`) |
| D-15 | [PM-ASSUMED, PM ruling R-5] GREEN re-produces the five redacted goldens with the folder's producer and that output becomes the golden; non-float values equal the old golden's exactly, floats are within `math.isclose(rel_tol=1e-12, abs_tol=0)`, the largest difference is recorded, and a larger difference is a defect sent back to the round | The only expected difference is the summation order the key-order move forces; the PM gates the branch, so no separate notice precedes the commit |

## Docs to Update
- `docs/generated/autotuner.md` (the `build_symphony_study_name` and `_build_optuna_sampler_from_env` sections at `:394` and `:403`), `docs/generated/database.md` (the `get_advisor_observations_for_subject` and `get_suggestions_for_symphony` sections at `:196` and `:337`, the `record_autotune_run` note at `:159`), `docs/generated/math_engine.md` (`filter_sortino_sentinels` at `:30`), `docs/generated/regime_label_job.md` (the page-token isolation), and one page per other touched module.
- `docs/generated/INDEX.md` (the `composer_backtest` row at `:74`, which names an importer that does not exist).
- `README.md` (the `composer_backtest.py` row at `:104`).
- An appended `DECISIONS.md` entry, minted at ship by the doc-writer.
- A drafted `.claude/CLAUDE.md` Key Files change for PM approval: the `database.py` row's accessor list and the `regime_label_job.py` row.

## Follow-Ups
- X-3, the dead `return_dates` branch of `_collect_sim_returns`: the replay-fidelity cycle (NB-10).
- HYG-005, the M3 bundle never registered in production (0 facet rows on the droplet): the PM tracks it as its own cycle (NB-7, Q-4).
- The `AttributeError` on a `.env` key with no value at six `app.py` sites (`:3639`, `:5465`, `:5546`, `:5642`, `:8826`, `:9625`): the controls slice (NB-15, Q-9).
- The two same-named `_is_finite_number` helpers with different semantics (D-8).
- Other wall-clock-dependent tests found by the DC-9 search (NB-22).
- The security, controls, engine VWAP, UI and replay-fidelity slices and worktree removal: the PM's work ledger (NB-21).
