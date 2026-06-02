-- Migration 016 — shadow_history (symphony_id, ts_utc) composite index.
--
-- AC-10 / shadow-trajectory warm-cache perf watch (math-audit-2):
--   The trajectory query performs ``ORDER BY ts_utc DESC LIMIT 1`` before
--   the cache check. The existing idx_shadow_history_sym_day keys on
--   (symphony_id, trading_day) and cannot serve the ts_utc sort — a 245x
--   to 9700x warm-cache regression on the dashboard. This composite index
--   on (symphony_id, ts_utc) lets SQLite serve the LIMIT-1 sort directly.
-- Additive / non-destructive / idempotent (IF NOT EXISTS).
CREATE INDEX IF NOT EXISTS idx_shadow_history_sym_ts
    ON shadow_history (symphony_id, ts_utc);
