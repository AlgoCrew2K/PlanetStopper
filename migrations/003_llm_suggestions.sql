-- Migration 003: Add llm_suggestions audit table
-- Trigger: deploy of Claude AI Config Advisor feature Phase 1 (task P3)
-- Risk: additive-only; no existing rows or schema modified
-- Idempotent: CREATE TABLE IF NOT EXISTS is safe to re-run
--
-- Purpose: immutable, append-only audit trail for every Claude config
-- suggestion — accepted AND rejected — capturing the complete decision chain
-- required by the prompt-methodology spec (docs/research/claude-integration/
-- prompt-methodology.md §4.2) and FINRA/NYSBA audit requirements.
--
-- All text-blob columns (prompt_inputs, raw_response, validation_results)
-- stored as TEXT; callers store JSON. Large payloads are expected.
--
-- Apply via:
--   sqlite3 alphabot_state.db < migrations/003_llm_suggestions.sql

CREATE TABLE IF NOT EXISTS llm_suggestions (
    -- Surrogate key — append-only, never reused
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Session grouping — one UUID (or similar) per operator request that
    -- produced a batch of suggestions; lets callers retrieve all suggestions
    -- from a single Claude call together
    session_id          TEXT    NOT NULL,

    -- When the suggestion record was created (UTC ISO-8601)
    created_at          TEXT    NOT NULL,

    -- Which symphony this suggestion targets
    symphony_name       TEXT    NOT NULL,

    -- Operator who initiated the request (username / email / "system")
    operator_identity   TEXT    NOT NULL DEFAULT '',

    -- -----------------------------------------------------------------------
    -- Prompt inputs — full snapshot sent to Claude (JSON blob)
    -- Includes: quant snapshot, Optuna evidence, param table + ranges,
    -- regime context — everything needed to reconstruct the decision later
    -- -----------------------------------------------------------------------
    prompt_inputs       TEXT    NOT NULL DEFAULT '{}',

    -- -----------------------------------------------------------------------
    -- Model + generation settings
    -- -----------------------------------------------------------------------
    model_id            TEXT    NOT NULL DEFAULT '',
    -- JSON object: {"temperature": 0.7, ...}
    generation_settings TEXT    NOT NULL DEFAULT '{}',

    -- -----------------------------------------------------------------------
    -- Raw Claude response (full JSON blob as returned by the API)
    -- -----------------------------------------------------------------------
    raw_response        TEXT    NOT NULL DEFAULT '{}',

    -- -----------------------------------------------------------------------
    -- Code-side validation results (JSON blob)
    -- Records which suggestions passed / were rejected / flagged and why
    -- -----------------------------------------------------------------------
    validation_results  TEXT    NOT NULL DEFAULT '{}',

    -- -----------------------------------------------------------------------
    -- Per-suggestion decision fields
    -- Each row is ONE suggestion; a multi-suggestion response produces N rows
    -- with the same session_id
    -- -----------------------------------------------------------------------

    -- Which parameter this row covers
    param_name          TEXT    NOT NULL DEFAULT '',

    -- Operator decision: 'accepted' | 'rejected' | 'pending'
    operator_decision   TEXT    NOT NULL DEFAULT 'pending',

    -- When the operator made their decision (UTC ISO-8601); NULL until decided
    decision_at         TEXT    DEFAULT NULL,

    -- Optional free-text note from the operator
    operator_note       TEXT    DEFAULT NULL,

    -- -----------------------------------------------------------------------
    -- Accepted-only fields — NULL when operator_decision != 'accepted'
    -- -----------------------------------------------------------------------

    -- Config value before the accepted change (JSON-encoded scalar or object)
    before_value        TEXT    DEFAULT NULL,

    -- Config value after the accepted change
    after_value         TEXT    DEFAULT NULL,

    -- Result of post-accept OOS re-validation (JSON blob); NULL if not run
    oos_revalidation    TEXT    DEFAULT NULL
);
