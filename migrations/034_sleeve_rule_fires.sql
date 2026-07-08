-- Migration 034: Managed Sleeves P2 -- sleeve_rule_fires (rule engine fire log).
-- Trigger: feature-plans/managed-sleeves.md P2 (rule engine core); this table was
-- deliberately DEFERRED from migration 033 (P1) per DECISIONS.md DE-SLEEVES-P1-001
-- addendum -- the row shape (sensed snapshot, episode semantics) is designed
-- against the real P2 runner instead of guessed at P1 time. Shape reconciled
-- 2026-07-08 across s2-db/s2-rules-impl/s2-test-writer (SendMessage thread) --
-- s2-rules-impl signed off on the clamp/episode/outcome-json design below;
-- s2-test-writer's independent proposal contributed rule_class + created_at.
-- Risk: additive-only; one new table, no existing schema modified or dropped.
-- Idempotent: CREATE TABLE/INDEX IF NOT EXISTS are safe to re-run.
--
-- No `PRAGMA foreign_keys=ON` anywhere in this codebase (verified against
-- database.py and all prior migrations) -- every cross-table reference below
-- is a SOFT FK (documented in comments, not DB-enforced), matching migration
-- 033_sleeves.sql's precedent.
--
-- Table role:
--   sleeve_rule_fires - one row per rule-engine tick evaluation that fired
--                       (i.e. `when`/`if` matched and `then` was attempted).
--                       SHADOW rules record the fire + the order the
--                       envelope-clamped sizing WOULD have produced and
--                       execute nothing (AC-6); PAPER/LIVE rules additionally
--                       carry the real order_id once one is placed. The
--                       engine is a fresh subprocess per minute (no in-memory
--                       state across ticks -- see migration 033's
--                       sleeve_runtime header), so this table is also the
--                       durable source for per-rule/per-sleeve attribution
--                       (AC-16, AC-19) and an independent (ground-truth)
--                       fire count for the dashboard, alongside limits.py's
--                       own pacing bookkeeping in sleeve_runtime (AC-5).
--
-- Apply via:
--   sqlite3 alphabot_state.db < migrations/034_sleeve_rule_fires.sql

CREATE TABLE IF NOT EXISTS sleeve_rule_fires (
    id                    INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    rule_id               INTEGER NOT NULL,              -- soft FK to sleeve_rules.id
    sleeve_id             INTEGER NOT NULL,              -- soft FK to sleeves.id (denormalized off
                                                           -- rule_id, mirrors sleeve_orders' sleeve_id
                                                           -- + rule_id pairing -- avoids a join for
                                                           -- per-sleeve panel queries)
    fired_at              TEXT    NOT NULL DEFAULT (datetime('now')),  -- tick logical timestamp; caller
                                                           -- may pass the tick's own timestamp
                                                           -- explicitly, falls back to insert-time
    action                TEXT    NOT NULL,               -- the rule's `then` action attempted, e.g.
                                                           -- 'buy'/'sell'/'scale'/'set_stop'/
                                                           -- 'go_to_cash'/'notify'
    rule_class            TEXT    NOT NULL,               -- 'DEFENSIVE'|'ENTRY' -- re-derived from the
                                                           -- rule's action set AT FIRE TIME (AC-20);
                                                           -- an immutable per-fire audit snapshot, never
                                                           -- re-trusted as the rule's CURRENT class
    mode_at_fire          TEXT    NOT NULL,               -- 'SHADOW'|'PAPER'|'LIVE' -- the rule's mode
                                                           -- AT FIRE TIME, snapshotted so a later mode
                                                           -- change never rewrites history
    sensed_snapshot_json  TEXT    NOT NULL DEFAULT '{}',  -- senses/condition values read this tick
                                                           -- (symphony/portfolio state, indicators,
                                                           -- time-of-day, cached FRED series) -- market/
                                                           -- portfolio values ONLY, never env/credentials
    outcome_json          TEXT    NOT NULL DEFAULT '{}',  -- what happened: would-have-ordered sizing
                                                           -- (SHADOW), broker result, rejection/skip
                                                           -- reason -- one flexible envelope so a new
                                                           -- action's outcome shape never needs a migration
    clamped               INTEGER NOT NULL DEFAULT 0,     -- 0/1 -- whether envelope.clamp() reduced
                                                           -- the attempted sizing (AC-2)
    clamp_reason          TEXT    DEFAULT NULL,           -- populated only when clamped = 1
    episode_id            TEXT    DEFAULT NULL,           -- groups fires belonging to one latch
                                                           -- episode (AC-10 flat-latch, AC-11
                                                           -- churn-brake round-trip grouping); minted
                                                           -- and formatted by the runner, NULL for
                                                           -- fires outside any episode
    order_id              INTEGER DEFAULT NULL,           -- soft FK to sleeve_orders.id -- the INTERNAL
                                                           -- id, matching sleeve_fills.order_id's
                                                           -- established convention (NOT client_order_id
                                                           -- or alpaca_order_id); NULL for SHADOW fires
                                                           -- and for fires whose action placed no order
                                                           -- (notify, skip, envelope refusal)
    created_at            TEXT    NOT NULL DEFAULT (datetime('now'))  -- row-insert time, distinct from
                                                           -- fired_at (sleeve_fills.filled_at/created_at
                                                           -- precedent, migration 033)
);

-- accelerates get_sleeve_rule_fires() + per-day fire counts (AC-5/AC-16)
CREATE INDEX IF NOT EXISTS idx_sleeve_rule_fires_rule_id ON sleeve_rule_fires (rule_id, fired_at);
-- accelerates per-sleeve fire history (dashboard Sleeves panel attribution, AC-16)
CREATE INDEX IF NOT EXISTS idx_sleeve_rule_fires_sleeve_id ON sleeve_rule_fires (sleeve_id);
-- accelerates order->fire attribution trace (AC-19: every trade traces to a named
-- rule + fire row + order + fill records)
CREATE INDEX IF NOT EXISTS idx_sleeve_rule_fires_order_id
    ON sleeve_rule_fires (order_id) WHERE order_id IS NOT NULL;
-- accelerates episode-latch lookups (AC-10: "while its condition holds" needs the
-- most recent fire in an episode)
CREATE INDEX IF NOT EXISTS idx_sleeve_rule_fires_episode_id
    ON sleeve_rule_fires (episode_id) WHERE episode_id IS NOT NULL;
