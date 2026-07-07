-- Migration 033: Managed Sleeves P1 -- sleeve infrastructure + order layer tables.
-- Trigger: feature-plans/managed-sleeves.md P1 (sleeve infra + order layer).
-- Risk: additive-only; five new tables, no existing schema modified or dropped.
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS are safe to re-run.
--
-- No `PRAGMA foreign_keys=ON` anywhere in this codebase (verified against
-- database.py and all prior migrations) -- every cross-table reference below
-- is a SOFT FK (documented in comments, not DB-enforced), matching the
-- spec_facets.bundle_hash precedent (migration 016_spec_bundles.sql).
--
-- Table roles:
--   sleeves         - one row per managed sleeve (a bounded slice of our own
--                     Alpaca account governed by operator-authored rules).
--   sleeve_rules    - schema-ready now for the P2 rule engine (when/if/then/
--                     limits JSON doc); not written or read by any P1 code path.
--   sleeve_orders   - one row per Alpaca order a sleeve has submitted.
--   sleeve_fills    - one row per fill/partial-fill against a sleeve_orders row.
--   sleeve_runtime  - per-rule key/value runtime state (pacing/latch/bench).
--                     The engine is a fresh subprocess per minute
--                     (alpha_bot_execution.main(), app.py:572-587,697), so ALL
--                     state that must survive a tick boundary lives here --
--                     never in memory. Schema-ready now; consumed by P2.
--
-- Apply via:
--   sqlite3 alphabot_state.db < migrations/033_sleeves.sql

CREATE TABLE IF NOT EXISTS sleeves (
    id            INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    capital_usd   REAL    NOT NULL,
    status        TEXT    NOT NULL DEFAULT 'SHADOW',
    envelope_json TEXT    NOT NULL DEFAULT '{}',
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sleeves_name ON sleeves (name);

-- P2 rule engine schema. Created now (schema-ready per the plan's build-phase
-- note) so P1's sleeve_id FK target exists; json_doc/mode/enabled are not
-- populated or read by any P1 code path.
CREATE TABLE IF NOT EXISTS sleeve_rules (
    id         INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    sleeve_id  INTEGER NOT NULL,             -- soft FK to sleeves.id
    name       TEXT    NOT NULL,
    json_doc   TEXT    NOT NULL DEFAULT '{}',
    mode       TEXT    NOT NULL DEFAULT 'SHADOW',
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- accelerates rule lookups for one sleeve (panel render, P2 runner tick fan-out)
CREATE INDEX IF NOT EXISTS idx_sleeve_rules_sleeve_id ON sleeve_rules (sleeve_id);

CREATE TABLE IF NOT EXISTS sleeve_orders (
    id              INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    alpaca_order_id TEXT    NOT NULL,        -- broker-assigned order id
    sleeve_id       INTEGER NOT NULL,        -- soft FK to sleeves.id
    rule_id         INTEGER DEFAULT NULL,    -- soft FK to sleeve_rules.id; nullable (P1 has no rules yet)
    symbol          TEXT    NOT NULL,
    side            TEXT    NOT NULL,        -- 'buy' | 'sell'
    qty             REAL    NOT NULL,        -- fractional shares supported
    order_class     TEXT    NOT NULL DEFAULT 'simple', -- 'simple' | 'bracket' | 'trailing_stop' | ...
    status          TEXT    NOT NULL DEFAULT 'new',     -- mirrors Alpaca's order-status enum verbatim
    submitted_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    raw_json        TEXT    NOT NULL DEFAULT '{}',      -- full broker submit/poll response, for audit
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sleeve_orders_alpaca_order_id ON sleeve_orders (alpaca_order_id);
-- accelerates per-sleeve order history + reconciliation account/order-truth checks
CREATE INDEX IF NOT EXISTS idx_sleeve_orders_sleeve_id ON sleeve_orders (sleeve_id);
-- accelerates per-rule attribution lookups (P2); partial index since rule_id is nullable in P1
CREATE INDEX IF NOT EXISTS idx_sleeve_orders_rule_id ON sleeve_orders (rule_id) WHERE rule_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS sleeve_fills (
    id         INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    order_id   INTEGER NOT NULL,   -- soft FK to sleeve_orders.id (the internal id, not alpaca_order_id)
    fill_price REAL    NOT NULL,
    filled_qty REAL    NOT NULL,
    filled_at  TEXT    NOT NULL,   -- broker-reported fill timestamp
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))  -- row-insert time, distinct from filled_at
);

-- accelerates per-order fill history (ledger conservation checks, P&L attribution)
CREATE INDEX IF NOT EXISTS idx_sleeve_fills_order_id ON sleeve_fills (order_id);

-- Pure key/value runtime store -- no surrogate id; (rule_id, key) is the
-- natural composite PK, upserted via INSERT OR REPLACE (port_state precedent,
-- migration 010_port_state.sql).
CREATE TABLE IF NOT EXISTS sleeve_runtime (
    rule_id    INTEGER NOT NULL,   -- soft FK to sleeve_rules.id
    key        TEXT    NOT NULL,
    value      TEXT    NOT NULL,
    updated_at TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (rule_id, key)
);
