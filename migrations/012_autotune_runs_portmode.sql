-- Migration 012: add port-mode columns to autotune_runs (AC-P2.11.1, N3+N4)
-- Purpose: math_mode distinguishes per-symphony vs port-level autotuner runs;
--          account_id links a port-level run to a specific account;
--          sortino_sentinel_pct stores the port-level sentinel threshold.
-- Risk: additive-only — three NULLable columns with DEFAULT, no existing rows affected.
-- Idempotent: migration runner's schema_migrations tracker prevents re-application.
--
-- Apply via:
--   sqlite3 alphabot_state.db < migrations/012_autotune_runs_portmode.sql
--   (migration 011 must be applied first)

ALTER TABLE autotune_runs ADD COLUMN math_mode TEXT NULL DEFAULT 'per_symphony';
ALTER TABLE autotune_runs ADD COLUMN account_id TEXT NULL DEFAULT NULL;
ALTER TABLE autotune_runs ADD COLUMN sortino_sentinel_pct REAL NULL DEFAULT NULL;
