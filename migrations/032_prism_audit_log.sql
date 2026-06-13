-- Migration 032 — prism_audit_log: per-run deliberation trail for the Market Prism pipeline.
--
-- Prism Phase 1 (prism-audit-foundation brief):
--   Every nightly Market Prism run may produce multiple entries — one per agent_role
--   and phase — all keyed by run_id so the full deliberation is auditable.
--   The MARKET_PRISM advisor_observation row also carries run_id in raw_response
--   so the report can join to its audit entries.
--
-- Additive / non-destructive / idempotent (IF NOT EXISTS on table and index).
-- All columns are NOT NULL except the auto-populated id PK; no column is
-- NULLable because every write supplies all four caller-provided fields.
-- created_at auto-populates via DEFAULT (datetime('now')) when omitted.

CREATE TABLE IF NOT EXISTS prism_audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT    NOT NULL,
    agent_role  TEXT    NOT NULL,
    phase       TEXT    NOT NULL,
    content     TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- accelerates get_prism_audit_for_run, which filters and orders by run_id
CREATE INDEX IF NOT EXISTS idx_prism_audit_log_run_id
    ON prism_audit_log (run_id);
