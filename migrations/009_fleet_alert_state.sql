-- R1: fleet_alert_state dedicated single-row table.
-- Purpose: Moves fleet_correlation_alert OUT of bot_state JSON blob into a proper
--          typed table, eliminating the dashboard-engine race window (R1 AC-1).
--          Dashboard dismiss writes ONLY to this table; engine writes ONLY to this
--          table. bot_state is never touched by the dismiss path.
-- Risk: additive-only — new table only, no existing table modified or dropped.
-- Idempotent: CREATE TABLE IF NOT EXISTS is safe to re-run.
--
-- Apply via:
--   sqlite3 alphabot_state.db < migrations/009_fleet_alert_state.sql
--   (migration 008 must be applied first to record this migration's name)

CREATE TABLE IF NOT EXISTS fleet_alert_state (
    id               INTEGER PRIMARY KEY CHECK(id = 1),
    tripped_at_et    TEXT NOT NULL,
    triggered_reason TEXT NOT NULL,
    tripped_count    INTEGER NOT NULL,
    active_count     INTEGER NOT NULL,
    dismissed_at_et  TEXT NULL DEFAULT NULL
);
