-- Migration 017: advisor_observations — AI Advisor audit trail
--
-- Stores every observation and verdict issued by the AI Config Advisor, plus
-- WALL_BREACH audit rows written by database._write_wall_breach_observation()
-- when advisor_ro_query detects a fold_role predicate bypass attempt.
--
-- Without this table, _write_wall_breach_observation silently fails on every
-- production deployment: the exception is swallowed at the catch block and the
-- audit record is never written.  This migration makes the wall-breach audit
-- trail durable on all production DBs.
--
-- Schema matches the INSERT statement in database._write_wall_breach_observation:
--   advisor_role    — 'WALL_BREACH' for tripwire writes; advisory role label otherwise
--   subject_type    — what the Advisor evaluated (e.g. 'fold_role_wall')
--   subject_id      — specific identifier of the evaluated subject
--   verdict         — 'BREACH' for tripwire; Advisor verdict label otherwise
--   raw_response    — JSON payload or SQL fragment (capped at 2 000 chars)
--   is_advisory_only — 1 = advisory, not binding (Advisor is read-only by construction)
--   spec_bundle_id  — optional link to spec_bundles row (Phase 2 Advisor context)
--
-- Idempotent: CREATE TABLE IF NOT EXISTS — safe on DBs that already have the table.
-- State DB only — zero optimization-DB placement (architecture constraint 3).

CREATE TABLE IF NOT EXISTS advisor_observations (
    id               INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    advisor_role     TEXT    NOT NULL,
    subject_type     TEXT    NOT NULL,
    subject_id       TEXT    NOT NULL,
    verdict          TEXT,
    raw_response     TEXT    NOT NULL DEFAULT '{}',
    is_advisory_only INTEGER NOT NULL DEFAULT 1,
    spec_bundle_id   TEXT
);
