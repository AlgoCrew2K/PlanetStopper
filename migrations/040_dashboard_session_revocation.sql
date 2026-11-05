-- Migration 040: dashboard_session_revocation -- the instant before which dashboard sessions
-- are ended, kept where a daemon restart finds it again.
--
-- A logout ends every session issued before it. The floor lives in process memory for the
-- running daemon and in this single row so a restart keeps earlier sessions refused. The
-- daemon is the only writer; the value only moves forward.
--
-- Risk: additive-only; one new table, no existing schema modified or dropped. Idempotent.
-- State DB only (architecture constraint 3).
CREATE TABLE IF NOT EXISTS dashboard_session_revocation (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    revoked_before_ns INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT
);
