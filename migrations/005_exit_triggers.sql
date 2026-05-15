-- Migration 005: Add exit_triggers telemetry table
-- Trigger: H1 Trigger Attribution Telemetry (task h1-trigger-telemetry)
-- Risk: additive-only; new table + indexes, no existing schema modified
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS is safe to re-run
--
-- Purpose: one row per trigger fire; records the reason, gate state, and
-- timestamps so post-hoc analysis can attribute what caused each exit.
-- Non-blocking write path: database.record_exit_trigger() opens its own
-- connection and wraps in try/except — a write failure never fails the cycle.
--
-- Retention: rows older than TRIGGER_TELEMETRY_RETENTION_DAYS (default 90)
-- are pruned by a daily scheduled task via database.prune_old_triggers().
-- Pruning uses batched DELETE with LIMIT to avoid long write locks.
--
-- Apply via:
--   sqlite3 alphabot_state.db < migrations/005_exit_triggers.sql
--   (migration 004 must be applied first to record this migration's name)

CREATE TABLE IF NOT EXISTS exit_triggers (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc           TEXT    NOT NULL,
    ts_et            TEXT    NOT NULL,
    symphony_id      TEXT    NOT NULL,
    account_id       TEXT,                 -- stored for internal audit; excluded from /api/triggers
    triggered_reason TEXT    NOT NULL,
    at_return        REAL,
    gate_state_json  TEXT,                 -- JSON blob: hwm, vwap_ticks, bleed_ticks, mc_prob, symphony_vol
    cycle_id         TEXT
);

-- Primary query pattern: most-recent triggers across all symphonies
CREATE INDEX IF NOT EXISTS idx_exit_triggers_ts
    ON exit_triggers (ts_utc DESC);

-- Per-symphony query pattern: most-recent trigger for a given symphony
CREATE INDEX IF NOT EXISTS idx_exit_triggers_symphony_ts
    ON exit_triggers (symphony_id, ts_utc DESC);
