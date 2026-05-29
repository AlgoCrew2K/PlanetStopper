-- Migration 020: EUT audit columns on autotune_runs
-- Adds nine additive columns persisting per-run EUT (expected-utility theory) machinery
-- state so the Overfitting Conscience and Specification Critic can read it, and so
-- optuna-compare can diff an EUT study against its paired heuristic study by name.
--
-- H1 DUAL-WRITE HAZARD: these same nine columns are mirrored in the init_db()
-- CREATE TABLE autotune_runs statement (database.py:99-113).  The duplicate-column-name
-- swallow in run_migrations() (database.py:921-932) reconciles the overlap safely:
-- a fresh DB already has the columns inline; this ALTER raises "duplicate column name";
-- the swallow marks the migration applied.  An upgraded DB lacks the columns and
-- this ALTER succeeds normally.  Both paths converge on the same schema.
--
-- Reference: decision-science-council-synthesis.md §3.5, §3.7 H1;
--            council-converged-migration-plan.md §3.1 row 022, §5, §6 H1.
--
-- All nine columns DEFAULT NULL:
--   Phase-1 honest shape: heuristic-path rows have all nine columns NULL.
--   cvar_feasible and lambda_budget are Phase-2 columns persisted Phase-1-NULL
--   (additive-first discipline per council H5 reversibility taxonomy).
--
-- Apply via:
--   sqlite3 alphabot_state.db < migrations/020_autotune_runs_eut.sql
--   (migrations 004 through 019 must be applied first)

-- spec_bundle_id: soft FK to spec_bundles.bundle_hash — the frozen facet bundle
--                 active during this autotune run.
ALTER TABLE autotune_runs ADD COLUMN spec_bundle_id TEXT DEFAULT NULL;

-- d_spec: bundle-distinct count of BACKTEST_SELECTION rows in researcher_dof_ledger
--         for this run's bundle (council §5 definition).
ALTER TABLE autotune_runs ADD COLUMN d_spec INTEGER DEFAULT NULL;

-- n_effective: additive haircut count N_optuna + S (council §2.2; S=0 in Phase 1
--              → n_effective == selection_tstat's trial count).
ALTER TABLE autotune_runs ADD COLUMN n_effective INTEGER DEFAULT NULL;

-- ce_metric: certainty-equivalent in return units — the display-time inverse of
--            mean(U(W)) for the CRRA objective.  Finite only when WEALTH_ARG_FLOOR
--            was applied (W-H4) to every trial's wealth argument.
ALTER TABLE autotune_runs ADD COLUMN ce_metric REAL DEFAULT NULL;

-- cvar_feasible: Phase-2 surface — NULL in Phase 1.  Will hold 1/0 when the
--                CVaR feasibility gate is implemented (council §3.4).
ALTER TABLE autotune_runs ADD COLUMN cvar_feasible INTEGER DEFAULT NULL;

-- gamma: the frozen CRRA risk-aversion parameter used for this run.
--        The WEALTH_ARG_FLOOR constant is NOT persisted here — it lives as a
--        named module-scope constant in math_engine.py / autotuner.py.
ALTER TABLE autotune_runs ADD COLUMN gamma REAL DEFAULT NULL;

-- lambda_budget: Phase-2 only — NULL in Phase 1.  Council §3.6 "HARDEN has no
--                lambda"; persisted as a forward-compatible NULL slot.
ALTER TABLE autotune_runs ADD COLUMN lambda_budget REAL DEFAULT NULL;

-- overfitting_verdict: human-readable Overfitting Conscience summary
--                      e.g. "D_spec=1,N_effective=N_optuna,no_violations".
ALTER TABLE autotune_runs ADD COLUMN overfitting_verdict TEXT DEFAULT NULL;

-- paired_heuristic_study_name: the Optuna study name of the paired heuristic run
--                               so optuna-compare can diff EUT vs heuristic
--                               by a single-DB name lookup.
ALTER TABLE autotune_runs ADD COLUMN paired_heuristic_study_name TEXT DEFAULT NULL;
