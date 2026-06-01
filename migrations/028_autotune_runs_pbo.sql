-- Migration 028: pbo column on autotune_runs (Phase-3 PBO acceptance gate)
-- Additive-first: NULLable with DEFAULT NULL — existing rows get pbo=NULL, no data loss.
--
-- Only the pbo column is added here (team-lead ruling 2026-06-01): the
-- Deflated Sharpe Ratio metric was decided against — Sharpe-based, mismatched
-- to the CRRA-EU objective, and redundant with PBO + the existing BHY/n_effective
-- gate. No second column belongs in this migration.
--
-- H1 DUAL-WRITE HAZARD: pbo is mirrored inline in init_db() CREATE TABLE.
-- The duplicate-column-name swallow in run_migrations() reconciles fresh-DB and
-- upgraded-DB paths identically to migrations 020 and 023.
--
-- pbo: Probability of Backtest Overfitting from CSCV (Bailey et al. 2017).
--      In (0, 1); higher means more overfitting evidence.  NULL for legacy rows
--      and any run where PBO could not be computed (insufficient paths).

ALTER TABLE autotune_runs ADD COLUMN pbo REAL DEFAULT NULL;
