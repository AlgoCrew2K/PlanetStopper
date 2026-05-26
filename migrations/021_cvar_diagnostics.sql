-- Migration 021: cvar_diagnostics
-- Per-cycle CVaR diagnostic telemetry for M2 shadow evidence (decision-science Phase 1).
-- Phase-1 rows have all cvar_* columns NULL (Phase-2 forward-path compute populates them).
-- Non-blocking write path: every INSERT goes through write_telemetry_row (H4 helper),
-- which opens its own short-lived connection and swallows OperationalError in live mode.
-- Reference: shadow-logging-pattern plan §Dependencies (1); h4-telemetry-helper plan.

CREATE TABLE IF NOT EXISTS cvar_diagnostics (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id         TEXT    NOT NULL,                           -- ISO-8601 cycle timestamp; primary query key
    ts_utc           TEXT    NOT NULL DEFAULT (datetime('now')), -- write timestamp (UTC)
    symphony_id      TEXT    NOT NULL,                           -- Composer symphony identifier
    cvar_5pct        REAL    DEFAULT NULL,                       -- 5th-percentile CVaR (Phase 2)
    cvar_5pct_stderr REAL    DEFAULT NULL,                       -- CVaR bootstrap std error (Phase 2)
    cvar_n_tail      INTEGER NOT NULL DEFAULT 0,                 -- tail observation count; 0 = insufficient sentinel (F-4 ★)
    cvar_5pct_long   REAL    DEFAULT NULL,                       -- long-window 5th-pct CVaR (Phase 2 §B.6)
    cvar_n_tail_long INTEGER DEFAULT NULL                        -- long-window tail obs count (Phase 2 §B.6)
);

-- idx_cvar_diag_cycle: supports replay-determinism lookups by (cycle_id, symphony_id)
-- via read_cvar_diagnostic_for_cycle (Gate-1 parity assertion).
CREATE INDEX IF NOT EXISTS idx_cvar_diag_cycle
    ON cvar_diagnostics (cycle_id);

-- idx_cvar_diag_symphony_ts: supports dashboard reads of the latest row per symphony
-- via read_cvar_diagnostic_for_symphony (S-3 CVaR display panel, rendered each page load).
CREATE INDEX IF NOT EXISTS idx_cvar_diag_symphony_ts
    ON cvar_diagnostics (symphony_id, ts_utc DESC);
