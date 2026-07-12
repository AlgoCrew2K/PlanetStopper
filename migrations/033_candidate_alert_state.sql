-- Migration 033: candidate_alert_state — viewed-marker for the header
-- candidate-alert indicator (feature-plans/candidate-alert.md).
--
-- The weekly suggestions job (advisors/weekly_suggestions_scheduler) persists
-- ASSET_SWAP/LOGIC_CHANGE/STRATEGY_BUILDER rows into advisor_observations.
-- This table tracks the last-viewed advisor_observations.id so the header
-- indicator can badge only NEW survivor candidates (AC-5).
--
-- Single-row table (id=1 pinned via CHECK) — same "latest wins" idiom as
-- regime_label_cache (database.py:1440-1465) but constrained to exactly one
-- row, mirroring the bot_state/execution_lock singleton pattern (database.py
-- init_db(), lines 114-120).
--
-- Additive / non-destructive / idempotent (IF NOT EXISTS). last_viewed_
-- observation_id is NOT NULL DEFAULT 0 — 0 means "nothing viewed yet" (never
-- collides with a real advisor_observations id, which is an AUTOINCREMENT
-- PK starting at 1). updated_at is NULLable (set by the accessor on write).
--
-- State DB only — zero optimization-DB placement (architecture constraint 3).

CREATE TABLE IF NOT EXISTS candidate_alert_state (
    id                          INTEGER PRIMARY KEY CHECK (id = 1),
    last_viewed_observation_id  INTEGER NOT NULL DEFAULT 0,
    updated_at                  TEXT
);
