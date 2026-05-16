-- M1F: Shadow-equity history table.
-- BC-1: migration 008 — next after 007 in _MIGRATION_FILES.
-- BC-2: trigger_id is advisory soft reference only — NO FK clause (SQLite FK enforcement off codebase-wide).
-- BC-6: idx_shadow_history_ts_utc required for prune DELETE at ~1.1M-row scale.
-- PA-M1F-10: current_return + shadow_return are NOT NULL with no DEFAULT — write must supply both.
-- AC-M1F.6.5: math_mode discriminator for V1 post-selection validation queries.

CREATE TABLE IF NOT EXISTS shadow_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc          TEXT    NOT NULL,
    ts_et           TEXT    NOT NULL,
    trading_day     TEXT    NOT NULL,
    symphony_id     TEXT    NOT NULL,
    account_id      TEXT,
    cycle_id        TEXT,
    current_return  REAL    NOT NULL,
    shadow_return   REAL    NOT NULL,
    is_post_trigger INTEGER NOT NULL DEFAULT 0,
    trigger_id      INTEGER,
    math_mode       TEXT    NOT NULL DEFAULT 'per_symphony'
);

CREATE INDEX IF NOT EXISTS idx_shadow_history_sym_day ON shadow_history (symphony_id, trading_day, ts_utc);
CREATE INDEX IF NOT EXISTS idx_shadow_history_day     ON shadow_history (trading_day, ts_utc);
CREATE INDEX IF NOT EXISTS idx_shadow_history_ts_utc  ON shadow_history (ts_utc);
