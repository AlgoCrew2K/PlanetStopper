-- Migration 038: retirement_decisions — mutable operator approve/reject
-- status for Retirement Recommender candidates (Phase 2, Cycle 2b:
-- feature-plans/retirement-approval-lifecycle.md; contract frozen in
-- .claude/tdd-handoff.md — implementers read the handoff, not the plan).
--
-- Cycle-2a's Retirement Recommender persists advisory recommendation rows
-- append-only into advisor_observations (RETIREMENT_RECOMMENDATION role) —
-- that history is immutable by design, same as every other advisor role.
-- This table is deliberately the OPPOSITE shape: ONE mutable row per
-- candidate_id, live-joined onto the (possibly re-run, possibly re-flagged)
-- recommendation at render time. candidate_id is the stable natural key (a
-- Composer symphony hash) — a persistently-flagged candidate recurs across
-- nights with the same candidate_id, giving "decide once, persists" (a
-- pairing can rotate a different sibling in on a later night; the decision
-- is about the CANDIDATE, not the pair, so sibling_id is informational only
-- and NOT part of the key — see database.py's upsert_retirement_decision).
--
-- Risk: additive-only; one new table, no existing schema modified or
-- dropped. Idempotent: CREATE TABLE IF NOT EXISTS is safe to re-run.
--
-- No `PRAGMA foreign_keys=ON` anywhere in this codebase (verified against
-- database.py and all prior migrations) — sibling_id is a SOFT reference
-- only (documented here, never DB-enforced), same convention as migrations
-- 035/036/037.
--
-- candidate_id's UNIQUE constraint is the sole index this table needs —
-- SQLite auto-creates an index backing a UNIQUE column, so no additional
-- `CREATE INDEX` is added here (a second explicit index on the same column
-- would be pure write-cost for zero read benefit).
--
-- State DB only — zero optimization-DB placement (architecture constraint 3).
--
-- Apply via:
--   sqlite3 alphabot_state.db < migrations/038_retirement_decisions.sql

CREATE TABLE IF NOT EXISTS retirement_decisions (
    id               INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    candidate_id     TEXT    NOT NULL UNIQUE,              -- Composer symphony hash; the
                                                             -- stable natural join key (see
                                                             -- retirement_recommender's
                                                             -- raw_response.candidate_id)
    sibling_id       TEXT,                                  -- informational only, NOT part of
                                                             -- the key — a candidate's sibling
                                                             -- can vary night to night; no
                                                             -- production writer this cycle
                                                             -- (the approve/reject routes pass
                                                             -- only candidate_id) — reserved for
                                                             -- future use, see
                                                             -- upsert_retirement_decision's
                                                             -- COALESCE-preserve contract
    approval_status  TEXT    NOT NULL DEFAULT 'pending',     -- 'pending' | 'approved' |
                                                             -- 'rejected' — validated in Python
                                                             -- (app-level, not a SQL CHECK
                                                             -- constraint), same convention as
                                                             -- strategy_incubation.status
    decided_at       TEXT,                                  -- NULL while pending; stamped
                                                             -- datetime('now') whenever the
                                                             -- WRITTEN approval_status is
                                                             -- 'approved' or 'rejected'
    updated_at       TEXT    NOT NULL DEFAULT (datetime('now'))  -- stamped datetime('now') on
                                                             -- every write, including a
                                                             -- re-decision
);
