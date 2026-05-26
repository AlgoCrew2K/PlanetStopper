-- Migration 024: spec_facets — add UNIQUE(bundle_hash, facet_name) constraint.
-- Rationale: without this constraint, concurrent calls to
-- get_or_create_phase1_theory_bundle_id() can both read zero facets and each
-- insert all three, producing duplicate facet rows.  INSERT OR IGNORE at the
-- application layer (insert_spec_bundle_facet) is the companion change;
-- the schema constraint makes idempotency durable regardless of caller discipline.
--
-- SQLite does not support ALTER TABLE ADD CONSTRAINT, so we rebuild the table.
-- Existing duplicate rows (if any) are deduplicated by taking the lowest-id row
-- per (bundle_hash, facet_name) pair — consistent with "first write wins."
--
-- State DB only — zero optimization-DB placement (architecture constraint 3).

-- Step 1: rebuild spec_facets with the UNIQUE constraint in place.
-- PRAGMA foreign_keys is OFF by default in SQLite; no FK enforcement needed.
CREATE TABLE spec_facets_new (
    id                   INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    bundle_hash          TEXT NOT NULL,
    facet_name           TEXT NOT NULL,
    facet_value          TEXT NOT NULL,
    freeze_discipline    TEXT NOT NULL,
    justification        TEXT,
    calibration_evidence TEXT,
    UNIQUE (bundle_hash, facet_name)
);

-- Step 2: copy rows, keeping only the first (lowest id) for each pair.
INSERT INTO spec_facets_new
    (id, bundle_hash, facet_name, facet_value, freeze_discipline,
     justification, calibration_evidence)
SELECT MIN(id), bundle_hash, facet_name, facet_value, freeze_discipline,
       justification, calibration_evidence
FROM spec_facets
GROUP BY bundle_hash, facet_name;

-- Step 3: replace the old table.
DROP TABLE spec_facets;
ALTER TABLE spec_facets_new RENAME TO spec_facets;
