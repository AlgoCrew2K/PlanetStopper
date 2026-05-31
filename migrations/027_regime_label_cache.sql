-- Migration 027: regime_label_cache (Phase 3c — regime-conditional exit lever)
-- Additive-first: a NEW standalone table, no change to existing tables.
-- Stores the most-recent offline regime label per symphony so the 1-minute
-- execution path reads a CACHED label (database.get_cached_regime_label)
-- instead of running regime_classifier.classify_regime() on the hot path
-- (PHASE3_DECISIONS.md §Sub-phase 3a: classifier is offline; no blocking I/O
-- on the execution path).
--
-- "Latest wins" per symphony: symphony_id is the PRIMARY KEY, so the offline
-- daily job uses INSERT ... ON CONFLICT(symphony_id) DO UPDATE to overwrite the
-- prior row. as_of_date (ISO YYYY-MM-DD) is the label's effective date, used by
-- the staleness_cutoff_days check in get_cached_regime_label.
CREATE TABLE IF NOT EXISTS regime_label_cache (
    symphony_id TEXT PRIMARY KEY,                       -- one current label per symphony
    label       TEXT NOT NULL,                          -- 'trending' | 'mean-reverting' | 'high-vol'
    as_of_date  TEXT NOT NULL,                          -- ISO date (YYYY-MM-DD) the label was computed for
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')) -- audit: when the row was last written
);
