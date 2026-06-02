-- Migration 030: per-symphony live_mode column + config_audit_log table.
--
-- Context: today live/dry-run is a single global flag (LIVE_EXECUTION).  This
-- migration introduces a per-symphony master-switch:
--   is_live = LIVE_EXECUTION AND symphony_live_mode
--
-- live_mode is a SEPARATE column on symphony_strategies — deliberately NOT inside
-- the parameters JSON blob — so the autotuner (which tunes parameters) can never
-- touch it.  It is an operator-only config field gated behind set_symphony_live_mode.
--
-- Additive-first: live_mode is NULLable with DEFAULT 0 so every pre-existing
-- symphony_strategies row reads as dry-run (arch rule 4: is_live explicit, never
-- by omission).  Live DBs gain the column without touching existing rows.
--
-- config_audit_log: new table recording every set_symphony_live_mode call.
-- Append-only — no update/delete accessor exists (same pattern as llm_suggestions).
--
-- H1 DUAL-WRITE: live_mode is mirrored inline in init_db() CREATE TABLE
-- symphony_strategies.  The duplicate-column-name swallow in run_migrations()
-- (database.py:1196-1207) reconciles fresh-DB and upgraded-DB paths identically
-- to migrations 020, 023, 028, and 029.  Reordering or removing this migration
-- from _MIGRATION_FILES would corrupt live DBs — do not do so.
--
-- config_audit_log is also mirrored in init_db() via CREATE TABLE IF NOT EXISTS.

ALTER TABLE symphony_strategies ADD COLUMN live_mode INTEGER DEFAULT 0;

CREATE TABLE IF NOT EXISTS config_audit_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    symphony     TEXT    NOT NULL,
    field        TEXT    NOT NULL,
    before_value TEXT,
    after_value  TEXT    NOT NULL,
    operator     TEXT    NOT NULL,
    ts_utc       TEXT    NOT NULL
);
