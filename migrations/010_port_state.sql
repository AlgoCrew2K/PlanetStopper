-- Migration 010: port_state typed table for port-level math mode (AC-P2.5.1)
-- Purpose: dedicated single-row-per-account table replacing per-account JSON blobs in bot_state.
--          Mirrors fleet_alert_state (009) pattern: one row per account_id PK.
-- Risk: additive-only — new table, no existing table modified or dropped.
-- Idempotent: CREATE TABLE IF NOT EXISTS is safe to re-run.
--
-- Apply via:
--   sqlite3 alphabot_state.db < migrations/010_port_state.sql
--   (migration 009 must be applied first)

CREATE TABLE IF NOT EXISTS port_state (
    account_id                TEXT    PRIMARY KEY,
    composition_hash          TEXT    DEFAULT NULL,
    high_water_mark           REAL    DEFAULT NULL,
    safe_hwm                  REAL    DEFAULT NULL,
    shadow_hwm                REAL    DEFAULT NULL,
    vwap_ticks_json           TEXT    DEFAULT NULL,
    vwap_bleed_ticks_json     TEXT    DEFAULT NULL,
    mc_history_json           TEXT    DEFAULT NULL,
    mc_prob                   REAL    DEFAULT NULL,
    armed                     INTEGER DEFAULT NULL,
    para_armed                INTEGER DEFAULT NULL,
    port_breakeven_active     INTEGER DEFAULT NULL,
    triggered                 INTEGER DEFAULT NULL,
    triggered_reason          TEXT    DEFAULT NULL,
    prev_return               REAL    DEFAULT NULL,
    current_return            REAL    DEFAULT NULL,
    last_target_reduction_json TEXT   DEFAULT NULL,
    last_selected_symphony_id TEXT    DEFAULT NULL,
    stop_trigger              REAL    DEFAULT NULL,
    updated_at                TEXT    DEFAULT NULL
);
