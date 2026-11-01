-- Migration 039: close_account_totals — the account totals latched for a close snapshot, stored
-- where a daemon restart finds them again.
--
-- Today, Cumulative and the account value of a closed market rest on Composer's account totals
-- (portfolio value, simple return, today's change, max drawdown). The first successful read taken
-- at or after the close snapshot's capture moment is latched for that snapshot, and every figure
-- shown until the next session is computed from it, so no later refresh moves a number while the
-- market is closed. The latch used to live only in process memory: a restart (every deploy and
-- every crash recovery happens outside market hours) emptied it, the figures fell to the
-- value-weighted floor, and the first refresh after the restart latched whatever Composer
-- answered then (Composer's totals drift after the close).
--
-- One row per close snapshot, named by the snapshot's own (trading_day, captured_at_et). The
-- latch is taken once: the writer uses INSERT OR IGNORE, so a row is never replaced. bot_state is
-- not used for this: the engine rewrites it from its own copy on every cycle and drops any key it
-- does not know. The daemon is the only writer; the dashboard routes only read.
--
-- Risk: additive-only; one new table, no existing schema modified or dropped. Idempotent:
-- CREATE TABLE IF NOT EXISTS is safe to re-run. as_of is NULLable text; latched_at defaults to
-- the insert time. State DB only (architecture constraint 3).
--
-- Apply via:
--   sqlite3 alphabot_state.db < migrations/039_close_account_totals.sql

CREATE TABLE IF NOT EXISTS close_account_totals (
    trading_day     TEXT NOT NULL,                       -- the close snapshot's session day
    captured_at_et  TEXT NOT NULL,                       -- the snapshot's capture time ("16:00:42 ET")
    totals_json     TEXT NOT NULL,                       -- the account totals dict, as latched
    as_of           TEXT,                                -- the ET stamp of the Composer read
    latched_at      TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (trading_day, captured_at_et)
);
