-- Migration 011: add port-mode columns to exit_triggers (AC-P2.10.1, AC-P2.10.2)
-- Purpose: math_mode records whether trigger was per-symphony or port-level;
--          port_trigger_id links all per-symphony exits fired by one port-level trigger event.
-- Risk: additive-only — two NULLable columns + one partial index added.
-- Idempotent: ALTER TABLE ADD COLUMN raises error if column exists on some SQLite versions;
--             the migration runner's schema_migrations tracker prevents re-application.
--
-- Apply via:
--   sqlite3 alphabot_state.db < migrations/011_exit_triggers_port.sql
--   (migration 010 must be applied first)

ALTER TABLE exit_triggers ADD COLUMN math_mode TEXT NULL DEFAULT NULL;
ALTER TABLE exit_triggers ADD COLUMN port_trigger_id TEXT NULL DEFAULT NULL;

-- accelerates forensic reconstruction of all symphony exits linked to one port trigger
CREATE INDEX IF NOT EXISTS idx_exit_triggers_port_trigger_id
    ON exit_triggers (port_trigger_id)
    WHERE port_trigger_id IS NOT NULL;
