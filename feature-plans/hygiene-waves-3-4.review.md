# Approval packet: Cleanup Waves 3 and 4, review leftovers and repository hygiene

## What is being built

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

## Acceptance criteria and scenarios

### AC-1: The dead functions and orphan fixtures are gone, and nothing reachable is removed
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

Negative coverage: N1 S-1.8 | N2 n/a: a deletion refuses no request | N3 n/a: no value is computed or shown by a removed function on any live path | N4 n/a: no route, hook or permission changes (S-10.4) | N5 n/a: the removed functions hold no state a repeated action could reach | N6 n/a: no live path loses or gains a dependency | N7 S-1.2, S-1.3, S-1.4, S-1.5, S-1.6, S-1.7, S-1.9, S-1.10 | N8 n/a: no edit is held open

### AC-2: The unused root Composer backtest module is gone and the canonical client keeps its tests
Rule: When the GREEN tree is built, `composer_backtest.py` shall not exist, and the three `TestDvmCapitalKeyMismatchFallback` tests shall run against `advisors.composer_backtest_client._extract_returns` from a test file that does not import `composer_backtest`.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-2.1 | B | the GREEN tree | `import composer_backtest` runs in a fresh interpreter at the repository root | it raises `ModuleNotFoundError`; the `pyproject.toml:69` per-file ignore and the `README.md:104` row are gone |
| S-2.2 | N7 | the three tests of `tests/ai_advisor/test_composer_backtest_client.py:760-797` (a single series is used despite a key mismatch; an exact match is preferred among several; several series with no match return empty) | the GREEN suite runs | the same three cases pass against the canonical `_extract_returns` (`advisors/composer_backtest_client.py:140`) and each fails when qa breaks that function's key matching |
| S-2.3 | N7 | the GREEN tree | the canonical client and its fixtures are compared with the ref | `advisors/composer_backtest_client.py`, `tests/fixtures/composer/backtest_inline_v1.json` and `tests/fixtures/composer/backtest_response_v1.json` are byte-unchanged, and the live-client suites that read them pass |
| S-2.4 | N7 | the GREEN tree | `tests/ai_advisor/test_backtest_fold_transform.py:996-1020` runs | it passes: the gate engine imports neither backtest module |
| S-2.5 | N1 | the classes `TestModuleExists`, `TestLiveGuard`, `TestRequestBodyConstruction`, `TestResponseParsing`, `TestErrorHandling` and `TestArchitectureSeparation` (`tests/ai_advisor/test_composer_backtest_client.py:165-759`) | the GREEN tree is built | they are deleted, none moved: the contracts only the root module had (an `is_live` guard raising `RuntimeError`, raising on 429 and 500, log returns at `composer_backtest.py:197`) go with it, and the behavior the canonical client shares (the `/backtest` URL, the tree under `symphony.raw_value`, the Composer auth headers, ISO-date keys, a retry that succeeds after 429; `advisors/composer_backtest_client.py:333-347`) is already pinned by `tests/ai_advisor/test_advisor_backtest_client.py` (`TestSuccessfulParsing` `:321`, `TestRequestContract` `:418`, `TestRetryPolicy` `:495`), which stays green |

Negative coverage: N1 S-2.5 | N2 n/a: no request is refused | N3 n/a: no value is computed or shown | N4 n/a: no permission path | N5 n/a: no state | N6 n/a: the canonical client's Composer handling is unchanged (S-2.3) | N7 S-2.2, S-2.3, S-2.4 | N8 n/a: no edit is held open

### AC-3: The z-score closures and the boolean environment-flag parse each have one definition, with every result unchanged
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

Negative coverage: N1 S-3.2, S-3.4, S-3.5, S-3.6, S-3.7, S-3.8, S-3.9, S-3.10, S-3.13, S-3.14 | N2 S-3.12, S-3.14 | N3 S-3.4, S-3.5, S-3.6, S-3.7, S-3.8, S-3.9, S-3.13, S-3.14 (the absent key) | N4 n/a: no authorization path changes; the auth and CSRF hooks are checked in S-10.4 | N5 n/a: each site parses one value per call and holds no state | N6 n/a: neither definition calls a dependency | N7 S-3.1, S-3.3, S-3.11 | N8 n/a: no edit is held open

### AC-4: The JSON-column row parsers and the latest-shadow-row SQL each have one definition, with each reader's contract unchanged
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

Negative coverage: N1 S-4.2, S-4.7 | N2 n/a: no request is refused | N3 S-4.7 (no row) | N4 S-4.5 (the analytics reader cannot write) | N5 S-4.5 (a second read in one request) | N6 S-4.6 | N7 S-4.3, S-4.5, S-4.8 | N8 n/a: no edit is held open

### AC-5: The engine's dip line names the confirmation count in force
Rule: When an armed, untriggered symphony's below-stop count goes from 0 to 1 in an engine cycle, the engine shall print the dip line naming the confirmation tick count passed to `compute_exit_confirmation` for that symphony on that tick.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-5.1 | B | symphony "Alpha Grove" armed and untriggered, with a `regime_label_cache` row for the session labelled `mean-reverting` | a cycle takes its below-stop count from 0 to 1 | the console line reads `Alpha Grove dipped below stop. Awaiting 5-tick confirmation...` after the unchanged prefix |
| S-5.2 | N3 | the label family: `trending`, `mean-reverting`, `high-vol`, no row, a row dated another session, an unrecognized label string | the same transition happens | the line names 2, 5, 3, 3, 3 and 3 ticks respectively, each equal to `_regime_exit_ticks` on that tick |
| S-5.3 | N7 | any S-5.2 case | the tick runs | the printed count and the `exit_confirm_ticks` argument of `compute_exit_confirmation` come from the same value on that tick |
| S-5.4 | N7 | a recorded sequence of engine cycles with dips, recoveries, triggers and take-profit transitions | it runs at GREEN and at the ref | every `bot_state` transition, exit decision and outgoing order is identical; the recovery line and the take-profit lines (`alpha_bot_execution.py:1550-1553`, `:1591-1597`) are unchanged |
| S-5.5 | N7 | the GREEN tree | the dip line is compared with the ref's | only the count differs: the prefix, the name truncated to 35 characters and `dipped below stop.` are unchanged, and no event, log or database write is added |
| S-5.6 | N5 | a symphony already at count 1 or more | further below-stop ticks run | no further dip line prints, as at the ref |

Negative coverage: N1 S-5.2 (the label family) | N2 n/a: nothing is refused | N3 S-5.2 | N4 n/a: console output only | N5 S-5.6 | N6 n/a: a failed label read returns no label and is the S-5.2 no-row case | N7 S-5.3, S-5.4, S-5.5 | N8 n/a: no edit is held open

### AC-6: No tracked file holds the real Composer account id
Rule: When the GREEN tree is built, no tracked file, read plain or gzip-decompressed, shall contain the real Composer account id or its 8-character prefix, each place that held it shall hold `ACCOUNT_ID_REDACTED`, and every digest or byte count recorded for a changed file shall match the file.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-6.1 | B | the real id read from the PM's local `.env` and never printed | the PM's scan of every tracked file at the PR head runs, decompressing `*.gz` | it reports 0 files holding the id and 0 holding its 8-character prefix, printing counts only |
| S-6.2 | B | the GREEN tree | a CI test scans every tracked fixture field and key that holds an account id (`account`, `account_id`, `accounts_map` keys, account path segments in recorded URLs) | each holds `ACCOUNT_ID_REDACTED` or an invented id recorded as invented in a `PROVENANCE.md` |
| S-6.3 | N7 | every suite that reads a changed fixture | the GREEN suite runs | it passes, because inputs and goldens carry the same placeholder (`tests/app/test_api_state_without_table.py`, the `tests/fixtures/refresh_timing/` suites, the `tests/fixtures/prod_droplet/` consumers) |
| S-6.4 | N7 | the GREEN tree | `tests/fixtures/refresh_timing/test_record_hashes.py` runs and each `PROVENANCE.md` row naming a changed file is read | the sidecar digests and byte counts match, and each row states the new sha256 and names the replacement as a changed field |
| S-6.5 | N1 | the 5 gzip records in `tests/fixtures/refresh_timing/01-c1/` | each is decompressed | the JSON equals the ref's decompressed JSON with the id replaced and nothing else changed |
| S-6.6 | N7 | every changed text file except the five re-produced goldens of S-6.10 | its ref content with the id (or prefix) replaced by the placeholder is compared with its GREEN content | they are byte-equal, except that in a file written with sorted keys the redacted key sits where sorting places it; no other value or order changed |
| S-6.10 | N7 | the nine `tests/fixtures/state_poll_no_table/` files where the id is an `accounts_map` key serialized with `sort_keys=True` beside a second account key (the derived records `snapshot_two_accounts.json`, `snapshot_missing_defaults.json`, `snapshot_null_defaults.json`, `snapshot_unnamed_entry.json`, and the goldens `golden_ref_closed_frozen_two_accounts.json`, `golden_ref_closed_guard_alpha_fail.json`, `golden_ref_closed_missing_defaults.json`, `golden_ref_closed_null_defaults.json`, `golden_ref_closed_unnamed_entry.json`) | the id is replaced and each file is written key-sorted as before | each of the four derived records differs from the ref only by the id value and the key-order move sorting forces for the redacted key, and each of the five goldens differs from the ref only by those and by float values within `math.isclose(rel_tol=1e-12, abs_tol=0)` of the old golden's; each file stays key-sorted, so `test_the_derived_records_are_what_the_derivation_writes` (`tests/app/test_api_state_without_table.py:378-382`) passes over the redacted sources; its `PROVENANCE.md` rows state the new digests (Q-5); the five goldens are re-produced by `tests/app/produce_state_poll_golden_ref.py` over the redacted records and that output becomes the golden, as the folder's provenance says, with every non-float value exactly equal to the old golden's and every float within `math.isclose(rel_tol=1e-12, abs_tol=0)` of it, and the record states the largest difference from the old golden; a larger difference is a defect sent back to the round, not a new golden |
| S-6.7 | N7 | the branch's commit messages, the PR body, test output, review records and logs written by the work | the S-6.1 scan runs over them | none holds the id |
| S-6.8 | N1 | a test that compared against the real id (`tests/advisors/test_composer_series_client.py:1202`, `tests/ui/test_static_js_page_harness.py:579`, `tests/ui/test_cycle_1_foundation.py:500`) | the GREEN suite runs | it compares against the placeholder, or against an invented UUID v4 where its assertion needs a UUID-shaped value (Q-7), and passes |
| S-6.9 | N7 | the branch | it is compared with main | it is a fast-forward of main; no commit is rewritten and nothing is force-pushed |

Negative coverage: N1 S-6.5, S-6.8 | N2 n/a: nothing is refused | N3 n/a: no value is computed or shown | N4 n/a: no permission path | N5 n/a: a one-time replacement | N6 n/a: no dependency is called | N7 S-6.3, S-6.4, S-6.6, S-6.7, S-6.9, S-6.10 | N8 n/a: no edit is held open

### AC-7: Three test verdicts stop depending on the machine
Rule: When `test_a_run_hung_in_the_session_is_ended_after_600_seconds_and_the_next_minute_spawns`, `test_composer_held_for_the_whole_run_does_not_delay_either_engine_spawn` or the heading-margin baseline producer runs, its verdict shall depend only on the code under test, never on machine load or on a developer `.env`.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-7.1 | B | the GREEN daemon | the hung-run test runs once normally and once with every logical core kept busy by other processes | it passes both times and still asserts: no signal inside 600 s, 9 skipped minutes and 1 run inside the bound, terminate between 600 and 661 virtual seconds, no kill, the terminated record's cycle id, a `/health` reason, at least one WARNING, the next run started by 10:11:59 ET, and no skipped record after the ended run |
| S-7.2 | N7 | each planted fault: the 600 s bound not enforced, no run spawned after the end, one minute skipped after the end | the hung-run test runs with and without load | it fails in every case, within its own time limit |
| S-7.3 | B | Composer `total-stats` held | the Composer-held test runs two scheduler passes, once normally and once under S-7.1's load | it passes both times, proving both passes finished while Composer was still held, two engine spawns and one Composer call, with no assertion on elapsed seconds |
| S-7.4 | N6 | a planted fault that runs the account refresh on the scheduler thread | the Composer-held test runs | it fails within its own time limit and does not hang the suite |
| S-7.5 | N5 | the GREEN tree | each of the two tests runs 20 times in a row under load | all 40 runs pass |
| S-7.6 | B | a checkout with a `.env` holding credential values and one with no `.env` | `tests/fixtures/dashboard/heading_margin_baseline/produce_heading_margin_baseline.py` runs in each, and `test_heading_margin_shared_browser.py` runs in each | the producer writes identical records in both, and the test gives the same verdict in both (QA-8) |

Negative coverage: N1 n/a: no input value is judged | N2 n/a: nothing is refused | N3 n/a: no value is computed or shown | N4 n/a: no permission path | N5 S-7.5 | N6 S-7.4 | N7 S-7.2 | N8 n/a: no edit is held open

### AC-8: The regime label job fails only the affected batch on an unusable page token
Rule: If a SIP daily-bars page carries a non-empty `next_page_token` that cannot be checked or used, then the regime label job shall fail only that batch with a reason naming the exception type, and shall fetch and label every other batch as with good pages.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-8.1 | B | two batches; batch 1's first page carries a non-empty list as `next_page_token`; batch 2's pages are good | the job runs | batch 1's tickers fail with reason `TypeError`; batch 2 is fetched; each symphony whose holdings are all in batch 2 is labelled; one line per symphony and one summary line are logged |
| S-8.2 | N1 | the token family: a non-empty list, a non-empty dict, a non-empty set, an empty list, an empty dict, an empty set, an integer, a token seen before on the same batch, None, an empty string, and no token key | page 1 of a batch carries it | the non-empty list, dict and set fail the batch with `TypeError`; the empty containers, None, the empty string and the absent key end the batch at `if not token` (`regime_label_job.py:227`), as at the ref; the integer is sent as `page_token` as at the ref; a repeat fails with `RepeatedPageToken` |
| S-8.3 | N7 | the GREEN tree | a run with only good pages and a run with a transport failure on page 2 | the per-symphony lines, the summary line, the reasons and the written labels equal the ref's |
| S-8.4 | N7 | the GREEN tree | `typing.get_type_hints(regime_label_job._log_summary)` is read and the summary is logged for an empty run and for a run of outcomes | the `outcomes` annotation names a type that supports `len()` and repeated iteration, and both summary lines equal the ref's (CR-5) |
| S-8.5 | N3 | a symphony whose batch failed | the engine reads its label next session | no row is dated that session for it and the engine applies the default count, as at the ref |

Negative coverage: N1 S-8.2 | N2 n/a: nothing is refused | N3 S-8.5 | N4 n/a: off-hours job with no caller | N5 S-8.2 (a repeated token) | N6 S-8.1, S-8.3 | N7 S-8.3, S-8.4 | N8 n/a: no edit is held open

### AC-9: Comments, docstrings, annotations, whitespace and configuration lines state what holds at the ref
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

Negative coverage: N1 n/a: no input is judged | N2 n/a: nothing is refused | N3 n/a: no value is computed or shown | N4 n/a: no permission path | N5 n/a: no state | N6 n/a: no dependency | N7 S-9.4 | N8 n/a: no edit is held open

### AC-10: Protected surfaces stay unchanged and each content pin moves only over the listed edits
Rule: When the GREEN tree is built, every route response, engine exit decision, autotuner output, scheduler registration and database schema shall equal the ref's, and each content pin on `alpha_bot_execution.py`, `math_engine.py`, `autotuner.py` and `regime_classifier.py` shall be re-taken only when that file differs from the ref by exactly the edits listed here.

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

Negative coverage: N1 S-10.2 | N2 n/a: no refusal changes; the panic-sell refusals are S-3.12 | N3 n/a: no value is computed or shown | N4 S-10.4 | N5 n/a: no state | N6 n/a: no dependency | N7 S-10.1, S-10.3, S-10.5, S-10.6, S-10.7, S-10.8, S-10.9, S-10.10 | N8 n/a: no edit is held open

## Rule-by-entry-point matrix

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

## Narrowed mappings

R-2: The code does what it was designed to do; maps to AC-5, AC-8; left out: applied to the dip line and the regime job's batch isolation; the M3 bundle's designed production registration is reported to the PM, not built (G-20, NB-7)
R-17: The row-parse helpers share one definition; maps to AC-4; left out: two parsers remain after R-12 removes the third
R-19: The two latest-shadow-row readers share one definition, keeping analytics' read-only connection; maps to AC-4; left out: they share the SQL statement; each keeps its own connection, memo, error and cutoff behavior (Known fact 8)
R-34: The cleanup slice's leftover review rows are fixed where they are code or test hygiene; maps to AC-7, AC-9, NB-14; left out: INT-2 and QA-1 to QA-7 need no work (D-11)
R-36: The regime slice's leftover review rows are fixed where they are code or test hygiene; maps to AC-8, NB-14; left out: QA-AC-1 to QA-AC-7 need no work (D-11)
R-39: Tests that compared against the real id compare against the placeholder; maps to AC-6; left out: a test whose assertion needs a UUID-shaped value uses an invented UUID v4 instead (Q-7)
R-50: Fixtures change only the account-id value; maps to AC-6; left out: the orphan fixtures of R-24 are deleted, and the heading-margin baseline is re-captured if the QA-8 fix changes it (Q-11)

## Open questions

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

Sensitive: security, money, sensitive-data (AC-3, AC-4, AC-5, AC-6, AC-10). qa writes hidden scenarios for the named criteria after approval; they are not shown to the builders.
