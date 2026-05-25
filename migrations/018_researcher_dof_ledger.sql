-- Migration 018: researcher_dof_ledger
-- Append-only degrees-of-freedom ledger for the NN1 multiple-testing haircut.
-- Records every facet evaluated on a strategy P&L / strategy-return basis so
-- the honest N_effective = N_optuna + S accumulator can be computed at autotune time.
--
-- In the honest Phase-1 case S = 0 (all facets frozen by THEORY/MANDATE/
-- STYLIZED_FACT/CALIBRATION) and the haircut is byte-identical to today's.
-- The ledger is a tripwire, not a routine penalty — it bites only when someone
-- P&L-tours a facet (evidence_source = 'BACKTEST_SELECTION').
--
-- S accumulator: SUM(n_configs_searched) WHERE evidence_source = 'BACKTEST_SELECTION'
-- This is the sub-sweep sum (Defect 2 binding), NOT COUNT(DISTINCT spec_bundle_id).
-- A single P&L-toured spec that received its own sub-sweep contributes its full
-- sub-sweep count to S (council §2.2 / v3-evaluation §A.0 Defect 2).
--
-- touched_frozen_eval is the wall-breach tripwire column; the tripwire logic
-- itself lives in plan 019_fold_role_columns — this migration only provisions it.
--
-- State DB only — zero optimization-DB placement (architecture constraint 3).
-- Immutable: only INSERT accessors are exposed at the application layer
-- (llm_suggestions + advisor_observations precedent).

CREATE TABLE IF NOT EXISTS researcher_dof_ledger (
    id                  INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    facet_name          TEXT    NOT NULL,
    facet_category      TEXT    NOT NULL,   -- 'specification' | 'parameter'
    decision_type       TEXT    NOT NULL,   -- 'FIXED' | 'SEARCHED' | 'REVISED' | 'OOS_PEEK'
    evidence_source     TEXT    NOT NULL,   -- 'THEORY' | 'MANDATE' | 'STYLIZED_FACT' |
                                            -- 'CALIBRATION' | 'BACKTEST_SELECTION' | 'OOS'
    n_configs_searched  INTEGER NOT NULL DEFAULT 1,
    touched_frozen_eval INTEGER NOT NULL DEFAULT 0,  -- boolean; 1 = wall-breach tripwire fired
    spec_bundle_id      TEXT    DEFAULT NULL,         -- soft FK to spec_bundles.bundle_hash
    justification       TEXT    DEFAULT NULL
);

-- Phase-1 theory-frozen seed rows (synthesis §3.3 / plan §Deliverables #3).
-- All three have evidence_source != 'BACKTEST_SELECTION' so S = 0 in the honest
-- Phase-1 case (council §2.2 property 1: N_effective = N_optuna, byte-identical
-- to today's BHY haircut).  INSERT OR IGNORE is idempotent on re-run.
INSERT OR IGNORE INTO researcher_dof_ledger
    (facet_name, facet_category, decision_type, evidence_source,
     n_configs_searched, touched_frozen_eval, justification)
VALUES
    ('gamma',          'parameter',     'FIXED', 'THEORY',      1, 0,
     'CRRA risk-aversion parameter. Frozen by utility theory; not P&L-selected.'),
    ('utility_family', 'specification', 'FIXED', 'MANDATE',     1, 0,
     'CRRA utility family mandated by council decision; not selected by backtest comparison.'),
    ('wealth_argument','specification', 'FIXED', 'CALIBRATION', 1, 0,
     'Wealth argument fixed by W-H2 calibration derivation (terminal-wealth formulation).');
