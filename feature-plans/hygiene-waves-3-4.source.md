# Source: hygiene-waves-3-4

## Operator's words (verbatim)
1. "clean up after ourselves" (standing operator hygiene directive; cleanup, dead code and hygiene are first-class work on this project).
2. "it should do what it was designed to do"
3. "There should be no goddamn decisions for me"

## PM scope (the PM is the requester for everything below; every call is the PM's, recorded [PM-ASSUMED])

### A. June cleanup program, Waves 3 and 4 (never finished)
A re-check of reachability at 758f7f95 (2026-11-03, integration-auditor) found these items. Line numbers there are at 758f7f95 and must be re-found at 7adc2c56.
- DEAD, delete: HYG-011 autotuner.build_symphony_study_name; HYG-012a autotuner._build_optuna_sampler_from_env; HYG-012b autotuner local _n_jobs_raw; HYG-008 database.record_autotune_run (live writer is save_autotune_run; test mocks move to it); HYG-013 analytics.compute_v1_bootstrap_state; HYG-016 database.get_eod_shadow_row; HYG-010 database.get_advisor_observations_for_subject (8 test files use it, 2 as a test-side reader); HYG-001 math_engine.filter_sortino_sentinels (live one is autotuner.filter_sortino_sentinels); HYG-009 database.get_suggestions_for_symphony and get_suggestions_for_session (llm_suggestions is written live, never read in code); HYG-018 root composer_backtest.py (399 lines, tests-only importers); HYG-005 database.get_or_create_phase15_m3_bundle_id with its _M3_R1_*/_M3_R2_* constants.
- DUPLICATE, extract one shared helper: HYG-003 three byte-identical _z closures in math_engine (run_monte_carlo, compute_portfolio_cvar, and the third site); HYG-014 _frozen_status_rank/_frozen_exit_ret vs get_status_rank/get_exit_ret in app.py (may have changed or gone with the /api/state table removal; re-verify); HYG-004 the three row-parse helpers in database.py; HYG-007 the inline .lower() in ("true","1","yes") env parse (6 sites incl. alpha_bot_execution.py); HYG-015 analytics._load_latest_shadow_row_for_analytics vs database.load_latest_shadow_row (respect analytics' read-only connection constraint); HYG-006 finite-float helpers, only where semantics are identical.
- Extras found while tracing: X-1 database.compute_shadow_hwm (no production caller); X-3 autotuner._collect_sim_returns(return_dates=True) branch; X-4 regime_classifier.fit_regime_classifier (tests only); X-7 orphan fixtures tests/fixtures/autotuner/sortino_sentinel_trim/; X-9/X-10 extra sortino-sentinel shapes (read and rule); X-11 regime_classifier.py:9-12 docstring claims offline-only use, contradicted by the autotuner replay and now by regime_label_job.py.
- Full evidence: .claude/review/hyg-recheck-20261103/reachability.md in the primary checkout (copy its findings into the plan as needed; that file is untracked).

PM rulings on Wave 3/4:
- Every deletion is proven unreachable by call path at 7adc2c56 (not grep), including getattr/string dispatch and mock.patch targets (tests that patch a deleted name move to the live name or are deleted with it).
- HYG-002 (_collect_sim_returns variants) is an intentional, documented duplicate: keep; the replay-fidelity cycle owns the replay.
- HYG-020 regime_classifier is LIVE (autotuner replay and regime_label_job): keep; fix only the docstring drift (X-11).
- HYG-017 (relative .env reads in get_state and sell_account) moves to the controls slice with Cash Now, because it touches POST /api/sell_account.
- HYG-018: before deleting composer_backtest.py, any behavior its tests cover that the canonical advisors/composer_backtest_client.py also has gets its test moved there (e.g. TestDvmCapitalKeyMismatchFallback, if the canonical client has that behavior); behavior that exists only in the dead module is dead and goes with it.
- HYG-005: read the M3 decision record in DECISIONS.md to its end. If the phase-1.5 helper was a finished provenance artifact, delete it. If it was meant to be registered in production and never was, do not build it here: report it to the PM as a missing wire.
- HYG-009: the llm_suggestions table and its live writer stay; only the two unused readers go.
- The llm_suggestions ad hoc SQL question is closed: code reachability governs.

### B. Engine console text
- alpha_bot_execution.py:1548 prints "Awaiting 3-tick confirmation..." when a symphony dips below its stop. Since the regime label job, the confirm count is 2, 5 or 3 per symphony (math_engine.apply_regime_exit_adjustment). The text must state the actual count in force for that symphony on that tick. Console text only; no decision change.

### C. Two wall-clock flaky tests (pre-existing; failed only under CI load)
- tests/app/test_engine_run_bound.py::test_a_run_hung_in_the_session_is_ended_after_600_seconds_and_the_next_minute_spawns (failed once on CI: "minutes were skipped after the hung run was ended")
- tests/app/test_scheduler_survival.py::TestSlotsThatRaise::test_composer_held_for_the_whole_run_does_not_delay_either_engine_spawn
Both use real wall-clock deadlines. They must keep proving the same behavior and stop depending on machine load.

### D. Leftover low-severity review rows (verify each still applies at 7adc2c56)
- Cleanup slice review (folder .claude/worktrees/dtp/.claude/review/f3a699965cca/): code-reviewer CR-1 (joined comment line, double blank lines in templates/ai_advisor.html), CR-2 (unannotated helpers in tests/app/_state_poll_worlds.py); the false sort comment near app.py ~:5509; stale comments near app.py ~:5681 and ~:4439-4440; qa QA-8 (a test producer that depends on a local .env); a settings-modal comment; integration-auditor INT-1/INT-2, qa QA-1..QA-7, flask-dashboard-specialist FLASK-1 where they are code or test hygiene. ux-expert UX-1..UX-5 are OUT (visible UI goes to the UI slice).
- Regime slice review (folder .claude/worktrees/regime/.claude/review/d615e0941f25/): pipelines-expert PIPE-3 (repeated-token check in regime_label_job._fetch_batch sits outside the parse try; an unhashable token fails all batches), code-reviewer CR-5 (_log_summary annotates outcomes as Iterable but calls len() and iterates twice), and qa QA-AC-1..7 where they are code or test hygiene.

### E. Repository hygiene
- S-7 (security audit, MEDIUM): the real Composer account id appears in tracked files (43 files at 38b0cbe8; tests/fixtures/refresh_timing/*, tests/fixtures/prod_droplet/*, docs/handoff). Replace it everywhere in the tracked tree with the existing placeholder ACCOUNT_ID_REDACTED (precedent: tests/fixtures/composer/tenure-review/README.md). No git history rewrite (the id is not a credential; a rewrite needs a force-push of a public repository). Tests that compared against the real id compare against the placeholder. Never print the real id in any artifact.
- .claude/archive/ is not gitignored: add it.
- .claude/agents/flask-dashboard-specialist.md still names templates/table_partial.html (deleted): remove the mention.

### Out of scope (other slices, already queued)
- Security slice: S-9 (/health detail), S-10, S-11 (logout), S-17 (explainer prompt boundary).
- Controls slice: Cash Now, tenure on-demand market gate, HYG-017.
- Engine slice: VWAP_BLEED_MULTIPLIER/VWAP_BLEED_TICKS read from settings.
- UI slice: D1 fade, P-1 axis contrast, closed-market detail footer showing a Composer hash, ux-expert rows UX-1..UX-5.
- Replay-fidelity math cycle: IEX vs SIP in the replay.
- Worktree removal (PM operation, not code).
