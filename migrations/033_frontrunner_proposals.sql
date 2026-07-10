-- Migration 033 — frontrunner_proposals: mutable approval-status lifecycle for
-- the Frontrunner Builder + the propose_strategies retrofit
-- (feature-plans/frontrunner-builder.md AC-8/9/10).
--
-- advisor_observations (migration 017) is APPEND-ONLY / IMMUTABLE — it has no
-- update accessor, by design (audit-log semantics). The frontrunner approval
-- workflow needs a MUTABLE row per candidate (pending -> approved/rejected ->
-- uploaded), so this is a SEPARATE small state table, not a repurposing of
-- advisor_observations. One append-only advisor_observations audit row is
-- still written per action (mirrors record_llm_suggestion) for traceability;
-- this table is the operational state the approval route reads/writes.
--
-- Additive / non-destructive / idempotent (IF NOT EXISTS on table and index).
-- Shared by BOTH the new frontrunner candidates AND the propose_strategies
-- retrofit (proposal_source distinguishes the two) — one approval->create
-- path, one table.

CREATE TABLE IF NOT EXISTS frontrunner_proposals (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at         TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT    NOT NULL DEFAULT (datetime('now')),
    symphony_id         TEXT    NOT NULL,  -- the incumbent Composer symphony this proposal targets
    proposal_source     TEXT    NOT NULL,  -- 'frontrunner_builder' | 'strategy_builder_retrofit'
    approval_status     TEXT    NOT NULL DEFAULT 'pending',  -- pending | approved | rejected | uploaded
    candidate_tree      TEXT    NOT NULL,  -- JSON: the full spliced candidate symphony (raw_value)
    metrics_json        TEXT    NOT NULL DEFAULT '{}',  -- JSON: incumbent-vs-candidate Calmar/CAGR/MDD/node-count deltas
    created_symphony_id TEXT,              -- Composer symphony_id once uploaded (NULL until 'uploaded')
    error_message       TEXT               -- set when a Composer create attempt fails (AC-11); never marks 'uploaded' on failure
);

CREATE INDEX IF NOT EXISTS idx_frontrunner_proposals_symphony_id
    ON frontrunner_proposals (symphony_id);

CREATE INDEX IF NOT EXISTS idx_frontrunner_proposals_approval_status
    ON frontrunner_proposals (approval_status);
