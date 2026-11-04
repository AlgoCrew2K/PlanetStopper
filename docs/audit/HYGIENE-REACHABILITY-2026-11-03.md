# Cleanup Waves 3 and 4: Reachability Re-check at 758f7f95

## Metadata
- Auditor: integration-auditor (read-only reachability audit, standalone, no cycle)
- Run date: 2026-11-03
- Repo: `C:/Users/paulm/Documents/Projects/POC/AlphaBotPM`, single repo, no sibling repos
- `git rev-parse HEAD`: `758f7f9551ff3a15a292debb7bfd4f00b3ecf1e7`
- `git status -sb`: `## main...origin/main` (tracked tree equals HEAD; the only modified tracked file is `.claude/pr125-review-findings.json`, outside scope)
- Local `origin/main`: `758f7f9551ff3a15a292debb7bfd4f00b3ecf1e7`, merge base the same, ahead/behind `0 0`. No fetch was run (the brief forbids git mutations), so origin freshness is as of the last fetch.
- Method: `git grep` at `758f7f95` over `*.py` (and a second pass over every non-`.py`, non-`.md` tracked file for string dispatch), excluding `.claude/`, `.claude/worktrees/`, `.claude/audit-worktrees/`, `.design-handoff/`; each hit followed to its enclosing function and entry point. `getattr` dispatch on `database`/`analytics`/`autotuner`/`math_engine` was enumerated (only `ai_advisor.py:1429-1430` and `app.py:3512`, none touching an item here). `mock.patch` targets are listed as test coupling, never as callers.
- Line numbers in `.claude/wave3-reachability.md` (HEAD `52ef5cc`) are historical; every line below was re-found at `758f7f95`.
- Hash pins that matter for several items: `tests/test_mdd_window_truth_zero_diff_guard.py:72-73` pins sha256 of `alpha_bot_execution.py` (`a512d874...`) and `math_engine.py` (`772886ea...`); `tests/engine/test_close_snapshot_builder_scope.py:28` pins `alpha_bot_execution.py` minus the builder block. Any edit to either file, including deleting dead code, fails those pins until they are re-pinned.

## Summary table

| Item | Symbol | Verdict | Production callers | Coupled test files | Engine-adjacent |
|------|--------|---------|--------------------|--------------------|-----------------|
| HYG-011 | `autotuner.build_symphony_study_name` (`autotuner.py:623`) | DEAD | none; study name built inline at `autotuner.py:2763` | none | no (autotuner module, not the replay) |
| HYG-012a | `autotuner._build_optuna_sampler_from_env` (`autotuner.py:284`) | DEAD | none; sampler built inline at `autotuner.py:2746-2747,2767` | `tests/autotuner/test_optuna_sampler_pin_and_n_jobs.py:364,409` (existence pin, fails on delete) | no |
| HYG-012b | local `_n_jobs_raw` (`autotuner.py:2750`) | DEAD | assigned, never read | none | no |
| HYG-008 | `database.record_autotune_run` (`database.py:1076`) | DEAD | none; live writer is `save_autotune_run` (`autotuner.py:3227`) | `tests/app/test_dashboard_advisor_render_is_read_only.py:262`, `tests/app/test_dashboard_routes_never_call_engine_mutators.py:8,69,371`, `tests/autotuner/test_m1_crra_eu_objective.py:899`, `tests/autotuner/test_pbo_units_decimal_contract.py:241`, `tests/database/test_bl10_dead_autotune_columns_documented.py:20,30` (docstring) | no |
| HYG-013 | `analytics.compute_v1_bootstrap_state` (`analytics.py:2745`) | DEAD | none | `tests/shadow/test_shadow_history.py:1477-1482` | no |
| HYG-016 | `database.get_eod_shadow_row` (`database.py:3854`) | DEAD | none (one-line wrapper over `load_latest_shadow_row`) | `tests/shadow/test_shadow_history.py:1288-1293` | wrapper no; its callee yes |
| HYG-010 | `database.get_advisor_observations_for_subject` (`database.py:1396`) | DEAD | none | 8 files (see section) incl. 2 that use it as a test-side reader | no |
| HYG-001 | `math_engine.filter_sortino_sentinels` (`math_engine.py:18`) | DEAD | none; live one is `autotuner.filter_sortino_sentinels` (`autotuner.py:1780`), a different function | none call it; `math_engine.py` hash pin | yes (file is hash-pinned engine math) |
| HYG-003 | three `_z` closures (`math_engine.py:1356,1792,2011`) | DUPLICATE (byte-identical x3) | `run_monte_carlo`, `compute_portfolio_cvar`, `compute_regime_match_quality` on the 1-minute engine and the replay | hash pin + `tests/math_engine/test_perf001_rolling_vol_vectorization.py:346` + MC/CVaR/regime suites | YES (1-minute path) |
| HYG-014 | `_frozen_status_rank`/`_frozen_exit_ret` (`app.py:5518,5531`) vs `get_status_rank`/`get_exit_ret` (`app.py:5752,5765`) | DUPLICATE (behaviorally identical x2) | both inside `get_state()` (`GET /api/state`, `app.py:5428-5430`) | `tests/app/test_app_routes.py`, `tests/app/test_dashboard_m2.py`, `tests/dashboard/test_frozen_html_rendering.py` | no |
| HYG-004 | `_parse_llm_suggestion_row` / `_parse_advisor_observation_row` / `_parse_frontrunner_proposal_row` (`database.py:1214,1315,4959`) | DUPLICATE (near, NOT identical) | observation and frontrunner parsers live; llm parser only via HYG-009's dead readers | `tests/app/test_strategy_builder_phase4.py`, `tests/advisors/test_frontrunner_asset_class.py` | no |
| HYG-007 | inline `.lower() in ("true", "1", "yes")` env parse | DUPLICATE (identical expression x6) | `alpha_bot_execution.py:75`, `app.py:3615,5710,8989,9788`, `sleeves/tick_orchestrator.py:189` | hash pins + `tests/security/test_a1_settings_allowlist.py`, `tests/app/test_sleeves_arming_ceremony.py`, `tests/app/test_sleeves_panel_render.py` | YES (`LIVE_EXECUTION` gate in the engine; sleeve order host) |
| HYG-017 | `dotenv_values(".env")` (`app.py:5709`, `app.py:8988`) | LIVE | `get_state()` live branch and `sell_account()` (`POST /api/sell_account`) | `tests/app/conftest.py:212-219,450-457` (redirect bypassed by these 2 sites), sell_account suites (see section) | trade-adjacent (panic-sell route), not engine |
| HYG-009 | `database.get_suggestions_for_symphony`/`_session` (`database.py:1261,1280`) | DEAD | none; `llm_suggestions` is written live, never read in code | `tests/database/test_llm_suggestions.py` | no |
| HYG-015 | `analytics._load_latest_shadow_row_for_analytics` (`analytics.py:678`) vs `database.load_latest_shadow_row` (`database.py:3655`) | DUPLICATE (same SQL core, NOT behaviorally identical) | analytics copy: `get_symphony_today_change` (`analytics.py:593`); database copy: engine `alpha_bot_execution.py:1068`, `reporting.py:73,180` | `tests/analytics/test_r14_shadow_read_contract.py`, `tests/app/test_api_state_connection_count.py`, `tests/app/test_api_state_one_cycle.py`, `tests/dashboard/test_market_mode.py`, `tests/reporting/test_stage2_realized_basis.py`, `tests/telemetry/test_trigger_id_lineage.py` | YES (database copy is on the engine EOD path) |
| HYG-018 | root `composer_backtest.py` (399 lines) | DEAD | none (only tests import it) | `tests/ai_advisor/test_composer_backtest_client.py` (and `pyproject.toml:69` per-file ignore) | no |
| HYG-005 | `get_or_create_phase1_theory_bundle_id` (`database.py:2188`) vs `get_or_create_phase15_m3_bundle_id` (`database.py:2309`) | DEAD (the phase-1.5 copy), so de-duplication is moot | phase-1: `alpha_bot_execution.py:1189`, `app.py:8404`; phase-1.5: none | phase-1.5: `tests/math_engine/test_m3_spec_bundle_provenance.py`; phase-1: 5 files | phase-1 yes (engine weekly autotune branch); phase-1.5 no |
| HYG-002 | `_collect_sim_returns` (`autotuner.py:1466`), `_collect_sim_returns_dated` (`:1663`), `run_simulation` (`:1928`) | DUPLICATE (intentional, documented mock boundary holds) + a DEAD `return_dates` flag | all three live in `run_autotuner` | 21 / 11 / 41 test files respectively | YES (autotuner replay) |
| HYG-020 | `regime_classifier.classify_regime` (`regime_classifier.py:110`) | LIVE (not dormant) | `autotuner._replay_resolve_regime_exit_ticks` (`autotuner.py:1657`) from all three replay loops | 7 files (see section) | YES (replay); the live engine reads a cache nothing writes |
| HYG-006 | finite-float helpers (8 sites) | DUPLICATE (near; the "3rd site" condition is met; semantics diverge) | see section | see section | partial (`database._finite_or_none` on engine `save_state`) |

Verdict counts (20 rows): DEAD 11, DUPLICATE 7, LIVE 2, GONE 0. No item was found already removed.

---

## Wave 3: dead-code deletes

### HYG-011 `build_symphony_study_name`
- Exists: `autotuner.py:623` `def build_symphony_study_name(timestamp: str, symphony_id: str) -> str:`
- Only `.py` hit is the definition. Production study name is built inline: `autotuner.py:2763` `study_name=f"{study_timestamp}__{normalized_name}",`. Calibration sweep builds its own: `autotuner.py:3440` `study_name = f"{study_timestamp}__{sym_id}__calsweep"`.
- Verdict: DEAD. Tests: none. Engine adjacency: no (autotuner study setup, not the replay).

### HYG-012a `_build_optuna_sampler_from_env`
- Exists: `autotuner.py:284`; body `autotuner.py:293-295` `raw = os.environ.get(_OPTUNA_SAMPLER_SEED_ENV)` / `return optuna.samplers.TPESampler(seed=seed)`.
- Production builds the identical value inline: `autotuner.py:2746-2747` `_seed_raw = os.environ.get("OPTUNA_SAMPLER_SEED")` / `_sampler_seed = int(_seed_raw) if _seed_raw is not None and _seed_raw.strip() else None`, used at `autotuner.py:2767`. Same parse logic, so the helper and the inline site are behaviorally identical; only the inline site runs.
- Verdict: DEAD (and a DUPLICATE of the inline site).
- Test coupling: `tests/autotuner/test_optuna_sampler_pin_and_n_jobs.py:364` `helper = getattr(autotuner_module, "_build_optuna_sampler_from_env", None)` and `:409`; deleting the helper fails this pin. The pin asserts on the helper, not on the inline code that actually runs, so it currently tests dead code. A cleanup either wires `run_autotuner` to call the helper (making the pin meaningful) or retargets the test; that choice belongs to the cycle, not this audit.

### HYG-012b `_n_jobs_raw`
- Exists: `autotuner.py:2750` `_n_jobs_raw = os.environ.get("OPTUNA_N_JOBS")`; the next line `autotuner.py:2751` `_n_jobs = _resolve_optuna_n_jobs_from_env()` is the value used.
- Only occurrence repo-wide. Ruff does not flag it because `pyproject.toml` per-file ignores include `F841` for `autotuner.py` (comment: "SIM*/F841 stylistic/intentional").
- Verdict: DEAD. Tests: none.

### HYG-008 `record_autotune_run`
- Exists: `database.py:1076` `def record_autotune_run(`. Live writer: `autotuner.py:3227` `_inserted_id = database.save_autotune_run(`.
- No production caller. `database.py:215-221` comment already documents it: "database.record_autotune_run inserts both, but that function has zero callers repo-wide".
- Verdict: DEAD.
- Test coupling: mock attributes only (`tests/autotuner/test_m1_crra_eu_objective.py:899` `mock_db.record_autotune_run.return_value = None`; `tests/autotuner/test_pbo_units_decimal_contract.py:241`; `tests/app/test_dashboard_advisor_render_is_read_only.py:262` `assert_not_called()`; `tests/app/test_dashboard_routes_never_call_engine_mutators.py:69` denylist entry, unioned with discovered names at `:118`, so deletion leaves a harmless stale entry). `tests/database/test_bl10_dead_autotune_columns_documented.py` is text-based over the comment region; deleting the function makes the `database.py:215-221` comment stale and it must be reworded in the same change while keeping the BL-10 signal words.

### HYG-013 `compute_v1_bootstrap_state`
- Exists: `analytics.py:2745` `def compute_v1_bootstrap_state(sample_size: int, divergence_detected: bool) -> str:`.
- No production caller. Its constant `_V1_BOOTSTRAP_MIN_DAYS` (`analytics.py:2104`) stays live via `_WINDOWED_VOL_MIN_DAYS = _V1_BOOTSTRAP_MIN_DAYS` (`analytics.py:2111`), so only the function goes.
- Verdict: DEAD. Tests: `tests/shadow/test_shadow_history.py:1477` `from analytics import compute_v1_bootstrap_state`.

### HYG-016 `get_eod_shadow_row`
- Exists: `database.py:3854`; body `database.py:3860` `return load_latest_shadow_row(symphony_id, trading_day)`.
- No production caller. Callee `load_latest_shadow_row` is live on the engine (`alpha_bot_execution.py:1068`) and in `reporting.py:73,180`; deleting the wrapper does not touch the callee.
- Verdict: DEAD. Tests: `tests/shadow/test_shadow_history.py:1288` `eod_row = database.get_eod_shadow_row(`.

### HYG-010 `get_advisor_observations_for_subject`
- Exists: `database.py:1396` `def get_advisor_observations_for_subject(`.
- No production caller and no string dispatch. Route tests already assert the route does NOT call it (`tests/ai_advisor/test_advisor_observations_ui.py:517` "Route must NOT call the legacy get_advisor_observations_for_subject"; `tests/ai_advisor/test_logic_l_2_api_docstring.py:92`).
- Verdict: DEAD.
- Test coupling: direct callers that use it as a test-side reader and would need another reader: `tests/advisors/test_incubation_admission.py:293,465`, `tests/advisors/test_retirement_recommender_persistence.py:193,209`, `tests/database/test_017_advisor_observations.py:428-586`. Patch targets (patching a deleted attribute raises `AttributeError`): `tests/ai_advisor/test_advisor_observations_ui.py:134,393,407,431,450,507,581,633`, `tests/app/test_audit_fix_advisor_route_symphony_filter.py:146` (`patch.object(db_module, ...)`). Absence pins that keep passing: `tests/ai_advisor/test_logic_l_2_api_docstring.py:92,116`. Docstring only: `tests/app/test_retirement_recommendations_route.py:22`.

### HYG-001 `math_engine.filter_sortino_sentinels`
- Exists: `math_engine.py:18` `def filter_sortino_sentinels(sortino_values: list[float]) -> list[float]:`; body `math_engine.py:30` `return [v for v in sortino_values if v != _SORTINO_SENTINEL]`.
- No caller anywhere (no `math_engine.filter_sortino_sentinels`, no `from math_engine import ... filter_sortino_sentinels`). All production calls (`autotuner.py:1859,2804,3545`) resolve to `autotuner.filter_sortino_sentinels` (`autotuner.py:1780`), which takes trial objects and also drops `value is None`, per-split sentinels and partial means. They are not behavioral duplicates; the math_engine copy is an older, narrower float-list filter.
- Verdict: DEAD. Keep `math_engine._SORTINO_SENTINEL` (`math_engine.py:15`), read by `autotuner.py:895,1759,1777`.
- Test coupling: no test calls it. Tests that monkeypatch `autotuner.filter_sortino_sentinels` (`tests/test_calibration_sweep_pbo_computation.py:102,148`) are on the live copy. Engine adjacency: yes by file; deletion changes `math_engine.py`, so the sha256 pin at `tests/test_mdd_window_truth_zero_diff_guard.py:73` must be re-pinned in the same change.

## Wave 3: de-duplication

Note: the June names `_zscore`, `_sym_status_rank`, `_sym_exit_ret`, `_parse_row` and `_parse_bool_env` never existed as strings in any `.py` file in git history (`git log -S` over `--all` returns nothing for them). They were descriptive labels. The real symbols at the ref are below.

### HYG-003 z-score closures in math_engine
- Three byte-identical nested closures, each `def _z(values, mean, std): if std == 0.0: return np.zeros_like(values, dtype=float); return (values - mean) / std`:
  - `math_engine.py:1356` inside `run_monte_carlo` (`math_engine.py:1280`)
  - `math_engine.py:1792` inside `compute_portfolio_cvar` (`math_engine.py:1698`)
  - `math_engine.py:2011` inside `compute_regime_match_quality` (`math_engine.py:1922`)
- The two following scalar lines are also identical at each site (`math_engine.py:1363-1364`, `1799-1800`, `2018-2019`): `today_ret_z = 0.0 if ret_std == 0.0 else (spy_today_ret_dec - ret_mean) / ret_std`.
- Callers: `alpha_bot_execution.py:1354` `math_engine.run_monte_carlo(`, `alpha_bot_execution.py:1372` `compute_regime_match_quality(`, `alpha_bot_execution.py:1740,1755` `compute_portfolio_cvar(`; replay `synthetic_history.py:448,467`; probe `scripts/mc_band_edge_stability_probe.py:223`.
- Verdict: DUPLICATE (identical x3, all live).
- Test coupling: sha256 pin `tests/test_mdd_window_truth_zero_diff_guard.py:73`; a local copy of `_z` in `tests/math_engine/test_perf001_rolling_vol_vectorization.py:346`; CVaR and regime suites (`tests/engine/test_m2_cvar_known_pool.py`, `tests/execution/test_cvar_wireup_*.py`, `tests/math_engine/test_cvar_5pct_replay_determinism.py`, `tests/math_engine/test_mc_regime_match_quality.py`, `tests/autotuner/test_ac6_bar_level_replay_production_parity.py`); MC golden and determinism suites (`tests/math_engine/test_mc_rng_seeding.py`, `test_mc_knn_exit_gate_properties.py`, `test_run_monte_carlo_signature_frozen.py`, `test_mc_sentinel_blast_radius_coverage.py`, `tests/synthetic_history/test_mc_deterministic_seed.py`, `tests/database/test_replay_determinism_anchor.py` and others; 44 test files reference `run_monte_carlo`).
- Engine adjacency: YES, on the 1-minute path. A golden-fixture characterization over all three functions is needed before extraction, and the hash pin must be re-pinned.

### HYG-014 symphony table sort keys in `/api/state`
- Frozen branch: `app.py:5518` `def _frozen_status_rank(s: dict) -> int:` and `app.py:5531` `def _frozen_exit_ret(s: dict) -> float:`.
- Live branch: `app.py:5752` `def get_status_rank(s):` and `app.py:5765` `def get_exit_ret(s):`.
- Both pairs sit inside `get_state()` (`app.py:5430`, route `@app.route("/api/state")` at `app.py:5428`). Rank tables are identical (VWAP Breakdown 5, triggered 4, para_armed 3, tp_armed 2, armed 1, else 0) and the exit-return logic is identical (`triggered_at_return`, else `current_return or -999.0`). The surrounding `sortCol` dispatch blocks (`mc_prob`, `stop_level`, `shadow_hwm`, `shadow` lambdas, `app.py:5540-5575` and `app.py:5774-5800`) are duplicated too.
- Verdict: DUPLICATE (behaviorally identical x2), server-reachable.
- Related: `_sym_status` (`app.py:5360`) returns a status string, not a rank, so it is not part of this duplicate.
- Reachability caveat (see extra X-6): no shipped client sends `sortCol` or consumes the rendered `html` key, so these sort branches only run with the default `name`. UNVERIFIED whether any external client uses them.
- Test coupling: `tests/app/test_app_routes.py`, `tests/app/test_dashboard_m2.py`, `tests/dashboard/test_frozen_html_rendering.py` (pass `sortCol`). Engine adjacency: no.

### HYG-004 row-parse helpers in database.py
- `database.py:1214` `_parse_llm_suggestion_row`: seven JSON columns, `json.loads` raises on bad JSON.
- `database.py:1315` `_parse_advisor_observation_row`: `raw_response` only; bad JSON yields `{}` plus `raw_response_unreadable = True` (`database.py:1332-1335` `except (TypeError, ValueError): result[col] = {}`).
- `database.py:4959` `_parse_frontrunner_proposal_row`: `candidate_tree`, `metrics_json`; raises on bad JSON.
- Verdict: DUPLICATE, near only. One generic `parse_json_columns(row, columns, json_columns)` would fit two, but the observation parser's fault tolerance is a deliberate contract, so a shared helper needs an explicit tolerant mode.
- Reachability: `_parse_llm_suggestion_row` is only used by the HYG-009 accessors (`database.py:1277,1296`), so if HYG-009 goes it goes too, along with `_LLM_SUGGESTION_COLUMNS`. Observation parser has 6 in-module call sites; frontrunner parser 3.
- Test coupling: `tests/app/test_strategy_builder_phase4.py` (observation parser), `tests/advisors/test_frontrunner_asset_class.py` (frontrunner parser). Engine adjacency: no.

### HYG-007 boolean env parsing
- Six copies of the identical expression `.lower() in ("true", "1", "yes")` with default `"False"`:
  - `alpha_bot_execution.py:75` `LIVE_EXECUTION = os.getenv("LIVE_EXECUTION", "False").lower() in ("true", "1", "yes")`
  - `app.py:3615` (`_build_meta`), `app.py:5710` (`get_state`), `app.py:8989` (`sell_account`), `app.py:9788` `global_live = env_vars.get("LIVE_EXECUTION", "False").lower() in ("true", "1", "yes")`
  - `sleeves/tick_orchestrator.py:187-189` `os.getenv("SLEEVE_LIVE_EXECUTION", "False").lower() in ("true", "1", "yes")`
- No helper named like a bool parser exists anywhere (`git grep` for `def _?(env_bool|bool_env|parse_bool|env_flag|truthy|as_bool|to_bool)` is empty).
- Verdict: DUPLICATE (identical x6), all live.
- Test coupling: `tests/security/test_a1_settings_allowlist.py`, `tests/app/test_sleeves_arming_ceremony.py`, `tests/app/test_sleeves_panel_render.py`; hash pins `tests/test_mdd_window_truth_zero_diff_guard.py:72` and `tests/engine/test_close_snapshot_builder_scope.py:28` on `alpha_bot_execution.py`.
- Engine adjacency: YES. These are the live-trade gates (`LIVE_EXECUTION` in the engine and the panic-sell route, `SLEEVE_LIVE_EXECUTION` on the sleeve order host). Consolidation needs a characterization test over every accepted and rejected spelling at each site; the engine copy is a module-level import-time read and changing it re-pins two hash tests.

### HYG-017 relative `.env` reads
- June listed `app.py:1712,2633`. At the ref, 14 `dotenv_values` sites use `ENV_FILE_PATH` (`app.py:85` absolute path) and two still read relative to the working directory:
  - `app.py:5709` `env_vars = dotenv_values(".env")` in `get_state()` live branch (`GET /api/state`)
  - `app.py:8988` `env_vars = dotenv_values(".env")` in `sell_account()` (`POST /api/sell_account`, `app.py:8967`), feeding `live_mode` at `app.py:8989` and the credential check at `app.py:8991`.
- Verdict: LIVE (latent defect, still present). Production is protected only by `WorkingDirectory=/opt/planetstopper` (`docs/DEPLOYMENT.md:177`).
- Test coupling: `tests/app/conftest.py:212-219` and `:450-457` redirect `app_module.ENV_FILE_PATH` to an empty file to keep tests off the developer `.env`; these two sites bypass that redirect, so under pytest run from the repo root they read the real developer `.env` unless the test also patches `app.dotenv_values`. Tests patching `app.dotenv_values`: `tests/app/test_app_routes.py:136,181,197,230`, `tests/app/_chrome_route_answers.py:111,124,145`, `tests/app/_refresh_timing_state_world.py:382` (which also patches `app._dotenv_module.dotenv_values` at `:383`). sell_account suites: `tests/app/test_sell_account_panic_confirm.py`, `tests/app/test_f003_liquidation_confirmation.py`, `tests/app/test_dashboard_no_order_path.py`, `tests/app/test_sleeves_arming_ceremony.py`, `tests/app/test_sleeves_disarm_and_envelope.py`.
- Fix shape constraint: use `dotenv_values(ENV_FILE_PATH)` (the module-level name), not `_dotenv_module.dotenv_values`, or the existing `app.dotenv_values` patches stop intercepting.
- Engine adjacency: not the engine; the panic-sell route is the dashboard's one trade button, so a characterization test on `sell_account` live-mode resolution is needed first.

## Wave 4

### HYG-009 `get_suggestions_for_symphony` / `get_suggestions_for_session` (BLOCKING question settled)
- Exist: `database.py:1261` and `database.py:1280`; each `SELECT ... FROM llm_suggestions` (`database.py:1272,1291`) through `_parse_llm_suggestion_row`.
- Production callers: none in `*.py`, `*.js`, `*.html`, `*.sql`, scripts or unit files.
- Is `llm_suggestions` read anywhere live? No. The only SQL reads of the table in tracked code are those two accessors. `advisor_ro_query` (`database.py:2827`) is the one generic SQL reader; its four callers query `cvar_diagnostics` (`advisors/divergence_explainer.py:189-192`), `spec_facets`/`spec_bundles` (`autotuner.py:2386`), `researcher_dof_ledger` (`autotuner.py:3207`) and `autotune_runs` (`autotuner.py:3266`). No `sqlite_master` enumeration exists in production code.
- The table is written live: `database.record_llm_suggestion` (`database.py:1152`) from `ai_advisor_accept()` (`app.py:12625`, route `/ai-advisor/accept`) and `ai_advisor_reject()` (`app.py:12658`, route `/ai-advisor/reject`). It stays an append-only, write-only audit trail; only the two readers are dead.
- Verdict: DEAD (readers). Deleting them also orphans `_parse_llm_suggestion_row` (`database.py:1214`) and `_LLM_SUGGESTION_COLUMNS`.
- Test coupling: `tests/database/test_llm_suggestions.py`. Other files mention the table name for the writer (listed by `git grep -l llm_suggestions tests/`), not the readers.
- Residual (non-blocking): operator reads of the table through the `/db-inspect` skill or the sqlite CLI are not code paths and cannot be seen here.

### HYG-015 latest shadow row readers
- `analytics.py:678` `_load_latest_shadow_row_for_analytics(symphony_id, trading_day, db_file, conn=None)`: opens `file:{db_file}?mode=ro` (`analytics.py:695`) or reuses a shared connection, saves and restores `row_factory`, memoizes through `_read_once` (`analytics.py:712`), returns a copy, calls `note_build_failure()` on error.
- `database.py:3655` `load_latest_shadow_row(symphony_id, trading_day, et_cutoff=None)`: read-write `sqlite3.connect(_db_file(), timeout=10.0)` (`database.py:3667`), optional `et_cutoff` clause, logs on error.
- Same core SQL when `et_cutoff is None`: `SELECT * FROM shadow_history WHERE symphony_id = ? AND trading_day = ? ORDER BY ts_utc DESC LIMIT 1`.
- Callers: analytics copy from `get_symphony_today_change` (`analytics.py:593`), which backs `/api/state`, the dashboard SSR cards and `/api/strip`. Database copy from the engine EOD loop (`alpha_bot_execution.py:1068` `last_row = database.load_latest_shadow_row(s_id, current_date_str)`), Stage 1 and Stage 2 post-mortem (`reporting.py:73,180`), and `get_eod_shadow_row` (dead, HYG-016).
- Verdict: DUPLICATE, not behaviorally identical. The analytics copy carries three contracts the database copy lacks: read-only URI (architecture constraint 5), shared-connection reuse (`DE-OPS-CLUSTER-001` F-1, pinned by `tests/app/test_api_state_connection_count.py`), and per-request memoization (`tests/app/test_api_state_one_cycle.py`). Consolidation means the database accessor would gain `conn=` and RO mode, or the analytics wrapper keeps its own connection logic and calls a shared SQL builder.
- Test coupling: analytics copy `tests/analytics/test_r14_shadow_read_contract.py`, `tests/app/test_api_state_connection_count.py`, `tests/app/test_api_state_one_cycle.py`; database copy `tests/dashboard/test_market_mode.py`, `tests/reporting/test_stage2_realized_basis.py`, `tests/telemetry/test_trigger_id_lineage.py`.
- Engine adjacency: YES for the database copy (engine EOD path, and the Stage 1 `et_cutoff` basis).

### HYG-018 root `composer_backtest.py` vs `advisors/composer_backtest_client.py` (open question settled)
- Both tracked. Root: `composer_backtest.py` (399 lines) with `submit_backtest` (`:250`), `BacktestStats` (`:76`), `_extract_daily_returns` (`:143`). Canonical: `advisors/composer_backtest_client.py` (454 lines) with `run_backtest` (`:270`), `BacktestResult` (`:91`), `_extract_returns` (`:140`).
- Production importers of the root module: none. Only `tests/ai_advisor/test_composer_backtest_client.py` imports it (`:173` `importlib.import_module("composer_backtest")`, `:179`, `:189`, ...). `tests/ai_advisor/test_backtest_fold_transform.py:1009,1017` only asserts the gate engine does NOT import it.
- Verdict: DEAD.
- Does its test cover behavior absent from the canonical client? Settled, in two parts:
  1. Classes `TestModuleExists`, `TestLiveGuard`, `TestRequestBodyConstruction`, `TestResponseParsing`, `TestErrorHandling`, `TestArchitectureSeparation` (`:165-760`) test contracts of the root module only, which the canonical client does not share and must not inherit: `is_live`/`_session` guard raising `RuntimeError` (`composer_backtest.py:260-261`), raise-on-429/500 (`:574`, `:608`), and log returns (`composer_backtest.py:197` `math.log(curr_val / prev_val)`, whereas the canonical client emits simple returns at `advisors/composer_backtest_client.py:200` `(curr_val / prev_val) - 1.0` per `DE-MATH-R2-001`). Deleting these loses nothing about live behavior.
  2. Class `TestDvmCapitalKeyMismatchFallback` (`tests/ai_advisor/test_composer_backtest_client.py:760-800`) tests the CANONICAL client: `:770` `from advisors.composer_backtest_client import _extract_returns`. Its three tests (single series used despite key mismatch, exact match preferred among several, several series with no match returns empty) exist nowhere else; the other `_extract_returns` tests (`tests/analytics/test_ac5_log_return_compounding_boundary.py`, `tests/advisors/test_ac5_fold_transform_genuine_compounding.py`) cover return semantics only. These three tests must be moved, not deleted, before the file goes.
- Other coupling: `pyproject.toml:69` `"composer_backtest.py" = ["E501"]` per-file ignore. Fixtures `tests/fixtures/composer/backtest_inline_v1.json` / `backtest_response_v1.json` are shared with live-client tests (`tests/ai_advisor/test_advisor_backtest_client.py`, `tests/ai_advisor/test_asset_swap_engine.py` and others), so they stay.
- Engine adjacency: no.

### HYG-005 get-or-create bundle helpers
- `database.py:2188` `get_or_create_phase1_theory_bundle_id()`: live, called at `alpha_bot_execution.py:1189` `spec_bundle_id=database.get_or_create_phase1_theory_bundle_id(),` (engine weekly autotune branch, Friday or forced) and `app.py:8404`.
- `database.py:2309` `get_or_create_phase15_m3_bundle_id()`: zero production callers; its constants `_M3_R1_*`/`_M3_R2_*` (`database.py:2265-2306`) and cache `_phase15_m3_bundle_id_cache` (`database.py:2258`) are referenced only inside `database.py` and by `tests/math_engine/test_m3_spec_bundle_provenance.py`.
- Verdict: DEAD for the phase-1.5 copy. The pattern duplicate (cache check, canonicalize, insert, reselect id, backfill facets) disappears if the dead copy is deleted, so de-duplication has nothing left to share unless the M3 bundle is meant to be wired. Whether the M3 provenance bundle was intended to be registered in production is a QUESTION for the plan owner (`DE-` record for M3), not a code fact.
- Test coupling: phase-1 `tests/autotuner/test_nn1_spec_freeze_discipline.py`, `tests/autotuner/test_objective_selection_rule.py`, `tests/database/test_get_spec_facets_ro_connection.py`, `tests/database/test_phase1_theory_bundle_accessor.py`, `tests/math_engine/test_m3_spec_bundle_provenance.py`; phase-1.5 only the last.
- Engine adjacency: phase-1 yes (engine weekly branch); phase-1.5 no.

### HYG-002 `_collect_sim_returns` variants
- `autotuner.py:1466` `_collect_sim_returns(..., *, return_dates=False)`, `autotuner.py:1663` `_collect_sim_returns_dated(..., *, score_dates=None)`, `autotuner.py:1928` `run_simulation(...)`.
- Documented mock boundary verified: `autotuner.py:1672-1675` "This function is intentionally NOT a wrapper around _collect_sim_returns ... so that test mocks of `autotuner._collect_sim_returns` (which return flat floats) do not intercept the dated-variant call". Shared per-tick logic is `_replay_exit_tick`/`_fresh_replay_state`, and all three call `_replay_resolve_regime_exit_ticks` (`autotuner.py:1520,1723,1993`), so the replay semantics are shared.
- Callers: `_collect_sim_returns` at `autotuner.py:2107,2993,3012,3415,3452,3582,3588`; `_collect_sim_returns_dated` at `autotuner.py:2682`; `run_simulation` at `autotuner.py:2940,2972,2982`, `ai_advisor.py:2274,2277`, `scripts/objective_variance_probe.py:601`.
- Verdict: DUPLICATE, intentional and structurally constrained by 21 test files that mock `autotuner._collect_sim_returns`. Merging the loops would break that boundary.
- Dead sub-finding: the `return_dates=True` branch (`autotuner.py:1550-1553` `if return_dates: daily_returns.append((date, guard_alpha))`) has no caller anywhere; the dated variant superseded it. `tests/autotuner/test_pbo_cscv_date_returns_persist.py:117` only checks `"return_dates" in src` OR the dated function exists, so removing the flag keeps that test green through its second branch.
- Engine adjacency: YES (autotuner replay; `tests/autotuner/test_ac6_bar_level_replay_production_parity.py`, `tests/autotuner/test_c3_replay_exit_parity.py` are the parity guards).

### HYG-020 `regime_classifier` (open question settled)
- `classify_regime` (`regime_classifier.py:110`) is LIVE, not dormant: `autotuner.py:18` `import regime_classifier`; `autotuner.py:1657` `label = regime_classifier.classify_regime(trailing_returns)` in `_replay_resolve_regime_exit_ticks` (`autotuner.py:1630`), called from all three replay loops (`autotuner.py:1520,1723,1993`), reached from `run_autotuner`, reached from the engine weekly branch (`alpha_bot_execution.py:1184`). The module docstring (`regime_classifier.py:9-12` "This is an OFFLINE DIAGNOSTIC ... surfaced only as an operator/analytics diagnostic") is stale on this point.
- The module does not read `external_data/`. It only mentions it (`regime_classifier.py:38`). `fit_regime_classifier(df)` (`regime_classifier.py:147`) takes a dataframe argument and has no production caller (tests only: `tests/analytics/test_regime_classifier.py`). No tracked `.py` reads `external_data`, and the directory is untracked (`git ls-files external_data` is empty; `git status` shows `?? external_data/`).
- Does an operator-run offline job call `classify_regime`? No tracked job does. The writer that such a job would use, `database.save_regime_label` (`database.py:1717`, docstring "Called by the OFFLINE daily job"), has zero production callers (tests only: `tests/app/test_guard_alpha_summary_route.py`, `tests/execution/test_regime_cache_isolation.py`, `tests/test_calibration_sweep_advisory_invariant.py`).
- Consequence (engine adjacent): the live engine reads that never-written cache every tick, `alpha_bot_execution.py:1527` `regime_label=database.get_cached_regime_label(symphony_id),`, and `apply_regime_exit_adjustment` (`math_engine.py:747`) returns `base_ticks` unchanged for `None`. So unless `regime_label_cache` (migration `027`) holds rows written outside the code, production always runs unadjusted exit-confirm ticks while the autotuner replay applies regime-conditional ticks. That is a production/replay parity gap on the exit path. Settling read-only check on the droplet: `SELECT COUNT(*), MAX(as_of_date), MAX(updated_at) FROM regime_label_cache;` against the production state DB. This is a QUESTION, not a confirmed defect, until that count is read.
- Verdicts: `classify_regime` LIVE; `fit_regime_classifier` DEAD (unverified extra X-4); `save_regime_label` DEAD as a writer (X-5).
- Test coupling for `classify_regime`: `tests/analytics/test_regime_classifier.py`, `tests/autotuner/test_ac1_regime_lookback_chronology_purity.py`, `tests/autotuner/test_ac4_r2_residual_tripwire.py`, `tests/autotuner/test_ac4_regime_conditional_exit_ticks.py`, `tests/autotuner/test_ac4_undated_path_regime_faithful.py`, `tests/execution/test_regime_cache_isolation.py`, `tests/math_engine/test_regime_exit_adjustment.py`.

### HYG-006 finite-float utilities
- The "skip unless a 3rd site appears" condition is met; there are at least eight helpers with overlapping intent and divergent semantics:
  - `database.py:25` `_finite_or_none(x)`: passes non-floats through `math.isfinite`, `None` on error; used by `_sanitize_state_for_json` (`database.py:356`), which `save_state` calls (`database.py:399`), which the engine calls (`alpha_bot_execution.py:530,676`).
  - `analytics.py:515` `_is_finite_number(value) -> bool`: `math.isfinite(float(value))`, so it accepts numeric strings and bools.
  - `app.py:7901` `_is_finite_number(value) -> bool`: same NAME as analytics, but rejects bools and strings (`isinstance(value, int) or (isinstance(value, float) and math.isfinite(value))`).
  - `app.py:6392` nested `_json_safe_float` in `guard_alpha_preconditions()` (`GET /api/guard-alpha-preconditions`): `None`/NaN/Inf to `None`, raises on non-numeric.
  - `app.py:6841` nested `_json_safe_float` in `api_incubation()` (`GET /api/incubation`): only floats checked, others passed through.
  - `advisors/tenure_review.py:509` `_finite(value)`: rejects bools, coerces, finite or `None`.
  - `advisors/frontrunner_acceptance.py:234` `_safe_float(value)`: coerces, does NOT reject NaN/Inf.
  - `regime_classifier.py:190` `_coerce_finite_series(returns)`: series form.
- Verdict: DUPLICATE (near, not identical). The two `_is_finite_number` and two `_json_safe_float` copies share names while behaving differently, which is the higher-risk part. Engine adjacency: partial (`database._finite_or_none` sits on the engine `save_state` path). Test coupling: not enumerated per helper (the brief scoped this item as conditional); UNVERIFIED.

## Open Questions (batched)
- [QUESTION-01] Non-blocking for deletion, blocking for engine trust: does `regime_label_cache` hold any production rows? Settle with the read-only query in HYG-020. If empty, the live exit-confirm regime lever has never fired while the replay assumes it does.
- [QUESTION-02] Non-blocking: was `get_or_create_phase15_m3_bundle_id` meant to be registered in production (M3 provenance), or is it a finished provenance artifact only? Settle from the M3 decision record in `DECISIONS.md` read to its end.
- [QUESTION-03] Non-blocking: does any client outside this repo call `GET /api/state` with `sortCol` or read its `html` key? Settle from production access logs (`timing_log` request lines) for `sortCol=`.
- [ASSUMPTION-01] Non-blocking: `origin/main` was not fetched (no git mutation allowed); freshness is as of the last fetch.
- [ASSUMPTION-02] Non-blocking: operator use of `llm_suggestions` through ad hoc SQL is outside code reachability.

## Unverified extras (tripped over while tracing; one line each)
- X-1 `database.compute_shadow_hwm` (`database.py:3863`): no production caller; only `tests/shadow/test_shadow_history.py`. Likely DEAD.
- X-2 `get_or_create_phase15_m3_bundle_id` and `_M3_R1_*`/`_M3_R2_*` constants (`database.py:2258-2309`): DEAD (traced under HYG-005).
- X-3 `autotuner._collect_sim_returns(return_dates=True)` branch (`autotuner.py:1550`): DEAD (traced under HYG-002).
- X-4 `regime_classifier.fit_regime_classifier` (`regime_classifier.py:147`): tests only; likely DEAD.
- X-5 `database.save_regime_label` (`database.py:1717`): no production writer while `alpha_bot_execution.py:1527` reads its cache every tick.
- X-6 `/api/state` `html` key (`app.py:5640,5938`) rendering `templates/table_partial.html`, whose headers call `setSort(...)` (`templates/table_partial.html:43`): `setSort` is defined nowhere in `static/` or `templates/`, and no script reads `.html` from the `/api/state` answer; likely an unconsumed render on every poll plus dead sort branches.
- X-7 `tests/fixtures/autotuner/sortino_sentinel_trim/` (e.g. `03_all_sentinels.json`): no `.py` file references the directory; likely orphan fixtures for the dead `math_engine.filter_sortino_sentinels`.
- X-8 `app.py:32` `import dotenv as _dotenv_module` and `app.py:36` `from dotenv import dotenv_values`: two call forms of one function, so tests must patch both (`tests/app/_refresh_timing_state_world.py:382-383`).
- X-9 `autotuner.py:895` inline `if s != math_engine._SORTINO_SENTINEL:` in `compute_sortino_se_bootstrap` (`autotuner.py:839`): a third sentinel-filter shape; reachability of the host function not traced.
- X-10 `scripts/derive_h1_bootstrap_fixture.py:41` `_SORTINO_SENTINEL = 1e6`: a separate copy of the engine constant in an offline script.
- X-11 `regime_classifier.py:9-12` docstring claims offline-only use, contradicted by `autotuner.py:1657` (doc drift).

## Evidence appendix
- File citations are inline above, each at `758f7f95`.
- Key commands: `git grep -n -E "<symbol>" 758f7f95 -- '*.py' ':!.claude' ':!.design-handoff'`; non-Python pass `git grep ... -- ':!*.py' ':!*.md' ':!*.json' ':!.claude' ':!.design-handoff'` (only hit: `pyproject.toml:69` for `composer_backtest.py`); history check `git log -S <name> --all -- '*.py'`.
- No screenshots, network logs or browser use: this is a source reachability audit.
