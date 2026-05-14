-- Migration 002: Add autotune_runs table for per-run Optuna validation metrics
-- Trigger: P1 prerequisite for Claude AI Config Advisor feature
--   run_autotuner() computes oos_alpha, train_alpha, baseline_decision,
--   fallback_oos_alpha, default_oos_alpha on every run but never persisted them.
--   This table makes them durable so the Claude context-assembly can read them.
-- Target DB: alphabot_state.db (state DB; NOT optuna_studies.db)
-- Risk: additive-only; all metric columns are NULLable with DEFAULT NULL so
--   pre-existing rows (none — this is a new table) are unaffected.
--   Safe to apply before or after daemon restart.
-- Idempotent: CREATE TABLE IF NOT EXISTS — safe to re-run.

CREATE TABLE IF NOT EXISTS autotune_runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_timestamp     TEXT    NOT NULL,
    symphony_id       TEXT    NOT NULL,
    oos_alpha         REAL    DEFAULT NULL,
    train_alpha       REAL    DEFAULT NULL,
    baseline_decision TEXT    DEFAULT NULL,
    fallback_oos_alpha REAL   DEFAULT NULL,
    default_oos_alpha REAL    DEFAULT NULL
);
