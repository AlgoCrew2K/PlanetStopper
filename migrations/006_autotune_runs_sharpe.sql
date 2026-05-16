-- Migration 006: Add deflated_sharpe and naive_sharpe columns to autotune_runs
-- Trigger: O2 Deflated-Sharpe correction at trial selection (task o2-deflated-sharpe)
-- Risk: additive-only; two NULLable REAL columns with DEFAULT NULL; no existing schema modified
-- Idempotent: ALTER TABLE ADD COLUMN is safe if the column does not already exist;
--             wrapped in the schema_migrations tracker so run_migrations() skips on re-run
--
-- Target table: autotune_runs in alphabot_state.db (BC-5 — NOT llm_suggestions;
-- deflated Sharpe is a per-run Optuna selection metric, not an LLM suggestion attribute)
--
-- deflated_sharpe: DSR value for the AI-branch best trial (Bailey & López de Prado 2014,
--                  Eq. 9). NULL when the AI branch was not used (fallback / default cascade).
-- naive_sharpe:    Raw best trial Sortino/Sharpe before DSR correction. NULL for non-AI rows.
--
-- Existing rows are preserved; new columns default to NULL per additive-first migration pattern
-- (project CLAUDE.md: additive-first, NULLable + DEFAULT, never destructive in one step).
--
-- Apply via:
--   sqlite3 alphabot_state.db < migrations/006_autotune_runs_sharpe.sql
--   (migrations 004 and 005 must be applied first)

ALTER TABLE autotune_runs ADD COLUMN deflated_sharpe REAL DEFAULT NULL;
ALTER TABLE autotune_runs ADD COLUMN naive_sharpe    REAL DEFAULT NULL;
