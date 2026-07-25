-- Migration 037: Strategy Incubation Gate -- strategy_incubation + incubation_daily.
-- Trigger: feature-plans/strategy-incubation-gate.md (contract frozen in
-- .claude/tdd-handoff.md -- implementers read the handoff, not the plan). Strategy
-- Builder survivors (built-new + atlas-suggested candidates that pass the FDR/PBO/SPY
-- gates) no longer surface as recommendations immediately -- each survivor enters this
-- forward-incubation ledger and is tracked on real forward market data (paper, zero
-- capital, zero execution) for a minimum window; only candidates meeting forward
-- criteria are PROMOTED to recommendation status. Advisory-only end to end; zero
-- live-execution surface; zero change to the 1-minute engine decision path.
--
-- Risk: additive-only; two new tables, no existing schema modified or dropped.
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS are safe to re-run.
--
-- No `PRAGMA foreign_keys=ON` anywhere in this codebase (verified against database.py
-- and all prior migrations) -- every cross-table reference below is a SOFT FK
-- (documented in comments, not DB-enforced), matching migration 035/036's precedent.
--
-- Table role:
--   strategy_incubation - one row per admitted candidate; candidate_hash is the
--                          UNIQUE tree-structural identity (see database.py's
--                          "candidate_hash convention" note next to the accessors
--                          that use it). status drives the ledger lifecycle
--                          (INCUBATING -> PROMOTED, or INCUBATING -> FAILED/EXPIRED
--                          with a 90-day refractory window before re-admission is
--                          possible -- enforced by register_incubation_candidate()).
--   incubation_daily    - one row per candidate per observed forward trading day;
--                          UNIQUE(candidate_hash, trading_day) makes a tick re-run
--                          or a same-day double-fire structurally idempotent
--                          (append_incubation_day() relies on INSERT OR IGNORE
--                          against this constraint).
--
-- Apply via:
--   sqlite3 alphabot_state.db < migrations/037_strategy_incubation.sql

CREATE TABLE IF NOT EXISTS strategy_incubation (
    id                 INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    candidate_hash     TEXT    NOT NULL UNIQUE,             -- tree-structural SHA-256,
                                                              -- MUST match
                                                              -- community_strats._composition_hash
                                                              -- exactly (see database.py accessor
                                                              -- comments) -- NOT
                                                              -- compute_composition_hash (that hashes
                                                              -- list[str] symphony IDs, unrelated)
    tree_json          TEXT    NOT NULL,                    -- the compiled Composer raw_value tree,
                                                              -- JSON-serialized (server-side only --
                                                              -- never exposed via the API route)
    objective           TEXT    NOT NULL,                    -- matches
                                                              -- strategy_builder_engine.Objective enum
                                                              -- value
    provenance          TEXT    NOT NULL,                    -- 'built-new' | 'atlas-suggested' (never
                                                              -- 'T1'-'T7', never 'community' -- same
                                                              -- provenance-tag rule as the rest of
                                                              -- Strategy Builder)
    admitted_at         TEXT    NOT NULL DEFAULT (datetime('now')),  -- incubation clock anchor; never
                                                              -- reset while status is INCUBATING or
                                                              -- PROMOTED; reset on a refractory
                                                              -- reentry (a genuinely fresh attempt)
    backtest_mdd_pct    REAL,                                -- gate-time backtest max drawdown,
                                                              -- stored as a POSITIVE PERCENTAGE
                                                              -- MAGNITUDE (e.g. 8.0 = an 8% drawdown)
                                                              -- -- caller converts from quantstats'
                                                              -- negative-fraction convention before
                                                              -- insert; see database.py accessor
                                                              -- comments
    status               TEXT    NOT NULL DEFAULT 'INCUBATING',  -- 'INCUBATING' | 'PROMOTED' |
                                                              -- 'FAILED' | 'EXPIRED'
    status_reason        TEXT,                               -- static token only, NEVER str(exc)
                                                              -- (C5 sanitized-error precedent); NULL
                                                              -- while INCUBATING or PROMOTED
    status_changed_at    TEXT,                                -- timestamp of the last status
                                                              -- transition (set by
                                                              -- set_incubation_status()); the
                                                              -- refractory-window anchor; NULL until
                                                              -- the first transition away from
                                                              -- INCUBATING
    promoted_at          TEXT                                 -- set only on the transition to
                                                              -- PROMOTED
);

-- accelerates get_incubating()'s hot filter (WHERE status = 'INCUBATING')
CREATE INDEX IF NOT EXISTS idx_strategy_incubation_status ON strategy_incubation (status);

CREATE TABLE IF NOT EXISTS incubation_daily (
    id                   INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    candidate_hash       TEXT    NOT NULL,                   -- soft FK to
                                                              -- strategy_incubation.candidate_hash
    trading_day          TEXT    NOT NULL,                   -- ISO date string, a genuine NYSE
                                                              -- trading day (market_calendar
                                                              -- .is_trading_day), never a weekday
                                                              -- proxy
    forward_return_pct   REAL    NOT NULL,                   -- candidate's simple daily return for
                                                              -- that day, PCT scale (x100 of the raw
                                                              -- fraction composer_backtest_client
                                                              -- returns)
    spy_return_pct       REAL,                                -- shared SPY benchmark return for that
                                                              -- same day, same pct scale; NULLable
                                                              -- when the shared SPY call failed that
                                                              -- tick -- candidate rows are still
                                                              -- recorded, promotion evaluation defers
    UNIQUE(candidate_hash, trading_day)                       -- idempotency guard: a tick re-run or
                                                              -- a same-day double-fire never
                                                              -- double-inserts (append_incubation_day
                                                              -- relies on this via INSERT OR IGNORE)
);
