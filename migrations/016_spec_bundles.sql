-- Migration 016: spec_bundles + spec_facets
-- Provenance spine for the Overfitting Conscience (NN1 spec-freeze enforcement).
-- An immutable hashed bundle registry: once a row is inserted its content is frozen;
-- no UPDATE path is exposed at the application layer (llm_suggestions precedent).
-- State DB only — zero optimization-DB placement (council §3.7 / architecture constraint 3).

CREATE TABLE IF NOT EXISTS spec_bundles (
    id                INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, -- surrogate integer PK (validate_nn1_compliance consumer)
    bundle_hash       TEXT NOT NULL UNIQUE,                    -- SHA-256 hex of canonical facets_json
    frozen_at         TEXT NOT NULL DEFAULT (datetime('now')), -- freeze timestamp; set once on INSERT
    facets_json       TEXT NOT NULL,                           -- canonical-serialised JSON blob
    horizon_bars      INTEGER,                                  -- optional: horizon convention (bars)
    cvar_alpha        REAL,                                     -- optional: CVaR alpha level
    generator_family  TEXT                                      -- optional: generator family identifier
);

CREATE TABLE IF NOT EXISTS spec_facets (
    id                   INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    bundle_hash          TEXT NOT NULL,    -- soft FK to spec_bundles.bundle_hash
    facet_name           TEXT NOT NULL,
    facet_value          TEXT NOT NULL,    -- JSON-serialised value
    freeze_discipline    TEXT NOT NULL,    -- THEORY|MANDATE|STYLIZED_FACT|CALIBRATION|BACKTEST_SELECTION
    justification        TEXT,
    calibration_evidence TEXT
);
