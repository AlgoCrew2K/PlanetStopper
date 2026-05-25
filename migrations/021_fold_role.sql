-- Migration 021: researcher_dof_ledger — Degrees-of-Freedom ledger for AI Advisor
--
-- 1. Adds an integer surrogate-key id column to spec_bundles (table-swap pattern,
--    safe on fresh test DBs and on production DBs with existing rows — data is
--    preserved via INSERT … SELECT).  The id column is required by
--    researcher_dof_ledger.spec_bundle_id and the wall-breach tripwire JOIN.
--
-- 2. Creates researcher_dof_ledger — records every observation the Advisor makes,
--    including which fold partition was touched and when.  The wall-breach tripwire
--    query (database.query_wall_breach_tripwire) JOINs this table against spec_bundles
--    to detect post-freeze frozen_eval touches.
--
-- Idempotent (via CREATE TABLE IF NOT EXISTS for the ledger; the spec_bundles
-- swap is guarded by the existing duplicate-column swallow in run_migrations).
-- State DB only — zero optimization-DB placement (architecture constraint 3).

-- ---------------------------------------------------------------------------
-- Step 1: add integer id to spec_bundles via table-swap
-- ---------------------------------------------------------------------------
-- Create the replacement table with id INTEGER PRIMARY KEY AUTOINCREMENT.
-- bundle_hash becomes a UNIQUE NOT NULL secondary key.
CREATE TABLE IF NOT EXISTS spec_bundles_v021 (
    id               INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    bundle_hash      TEXT    NOT NULL UNIQUE,                       -- SHA-256 hex; unique immutable identifier
    frozen_at        TEXT    NOT NULL DEFAULT (datetime('now')),    -- freeze timestamp; set once on INSERT
    facets_json      TEXT    NOT NULL,                              -- canonical-serialised JSON blob
    horizon_bars     INTEGER,                                       -- optional: horizon convention (bars)
    cvar_alpha       REAL,                                          -- optional: CVaR alpha level
    generator_family TEXT                                           -- optional: generator family identifier
);

-- Copy all existing spec_bundles rows; id is auto-assigned by AUTOINCREMENT.
INSERT OR IGNORE INTO spec_bundles_v021
    (bundle_hash, frozen_at, facets_json, horizon_bars, cvar_alpha, generator_family)
SELECT bundle_hash, frozen_at, facets_json, horizon_bars, cvar_alpha, generator_family
FROM spec_bundles;

-- Drop the old table and rename the new one into place.
DROP TABLE spec_bundles;
ALTER TABLE spec_bundles_v021 RENAME TO spec_bundles;

-- ---------------------------------------------------------------------------
-- Step 2: create researcher_dof_ledger
-- ---------------------------------------------------------------------------
-- Records every observation the Advisor makes against the state DB, including
-- which fold partition was touched (fold_role) and when.
--
-- fold_role values mirror the autotune_runs partition labels:
--   'train' | 'validation' | 'frozen_eval' | NULL (untagged)
-- created_at is compared against spec_bundles.frozen_at in the tripwire query.
-- spec_bundle_id is a soft FK to spec_bundles.id (the integer id added above).
CREATE TABLE IF NOT EXISTS researcher_dof_ledger (
    id               INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    spec_bundle_id   INTEGER,                                      -- soft FK to spec_bundles.id
    fold_role        TEXT    DEFAULT NULL,                         -- 'train'|'validation'|'frozen_eval'|NULL
    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),   -- ISO-8601 UTC
    observation_type TEXT    NOT NULL,                             -- audit label
    payload_json     TEXT    NOT NULL DEFAULT '{}'                 -- arbitrary JSON
);

-- Index to make the tripwire JOIN efficient: spec_bundle_id + fold_role + created_at.
CREATE INDEX IF NOT EXISTS idx_rdl_bundle_role_ts
    ON researcher_dof_ledger (spec_bundle_id, fold_role, created_at);
