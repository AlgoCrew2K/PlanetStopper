-- Migration 017: advisor_observations
-- Append-only log for all AI Advisor roles (Phase 1: Overfitting Conscience computed rows;
-- Phase 2: SPEC_CRITIC, DIVERGENCE_EXPLAINER, NARRATOR LLM-authored rows).
-- advisor_role is the discriminator; raw_response is the JSON variant field.
-- is_advisory_only=1 is hard-wired at both schema level (DEFAULT) and accessor level:
-- the table is structurally non-actionable — it never moves money.
-- Soft FK to spec_bundles.bundle_hash via spec_bundle_id (not enforced by SQLite).
-- State DB only — zero optimization-DB placement (architecture constraint 3).

CREATE TABLE IF NOT EXISTS advisor_observations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    advisor_role      TEXT    NOT NULL,  -- OVERFITTING_CONSCIENCE | SPEC_CRITIC | DIVERGENCE_EXPLAINER | NARRATOR
    subject_type      TEXT    NOT NULL,  -- e.g. autotune_run, cvar_diagnostic, shadow_decision
    subject_id        TEXT    NOT NULL,  -- soft FK into the subject table
    verdict           TEXT,              -- computed verdict label; NULL for LLM-only rows if not applicable
    raw_response      TEXT    NOT NULL DEFAULT '{}',  -- JSON; '{}' for computed rows, populated for LLM rows
    is_advisory_only  INTEGER NOT NULL DEFAULT 1,     -- always 1; structural declaration that Advisor never acts
    spec_bundle_id    TEXT                             -- soft FK to spec_bundles.bundle_hash; NULL when not applicable
);
