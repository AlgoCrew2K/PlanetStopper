-- Migration 004: Add schema_migrations tracker table
-- Trigger: H1 Trigger Attribution Telemetry (task h1-trigger-telemetry)
-- Risk: additive-only; new table, no existing schema modified
-- Idempotent: CREATE TABLE IF NOT EXISTS is safe to re-run
--
-- Purpose: records which numbered migrations have been applied to this DB,
-- so run_migrations() can skip already-applied migrations on restart.
-- Migration name is the PK; INSERT OR IGNORE is the idempotent apply pattern.
--
-- Apply via:
--   sqlite3 alphabot_state.db < migrations/004_schema_migrations_tracker.sql

CREATE TABLE IF NOT EXISTS schema_migrations (
    migration_name TEXT PRIMARY KEY,
    applied_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
