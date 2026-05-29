-- Migration 023: s_count column on autotune_runs
-- Adds the S accumulator (SUM of n_configs_searched over BACKTEST_SELECTION rows)
-- alongside the existing n_effective column added in 020.
--
-- H1 DUAL-WRITE HAZARD: s_count is mirrored inline in init_db() CREATE TABLE.
-- The duplicate-column-name swallow in run_migrations() reconciles fresh-DB and
-- upgraded-DB paths identically to migration 020.
--
-- Reference: feature-plans/decision-science/phase-1/n-effective-additive-accounting/plan.md
--            council synthesis §2.2 (N_effective = N_optuna + S).
--
-- DEFAULT NULL: S=0 in the NN1-honest case; NULL for legacy heuristic-path rows.

-- s_count: S = SUM(n_configs_searched) over BACKTEST_SELECTION rows in
--          researcher_dof_ledger for this run's bundle.  Distinct from d_spec
--          (which is COUNT DISTINCT bundles).  S is the sub-sweep sum per
--          v3-and-divergence-evaluation §B.3 route 2 (Defect 2 binding).
ALTER TABLE autotune_runs ADD COLUMN s_count INTEGER DEFAULT NULL;
