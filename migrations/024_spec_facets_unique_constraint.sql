-- Migration 024: spec_facets — enforce UNIQUE(bundle_hash, facet_name).
--
-- Additive-first approach (project CLAUDE.md mandate: never destructive in one step).
-- No table rebuild, no DROP, no RENAME — only a targeted DELETE + CREATE UNIQUE INDEX.
--
-- Rationale: without this constraint, concurrent calls to
-- get_or_create_phase1_theory_bundle_id() can both read zero facets and each
-- insert all three, producing duplicate facet rows.  INSERT OR IGNORE at the
-- application layer (insert_spec_bundle_facet, added in the same cycle) is the
-- companion change; this index makes idempotency durable at the schema level
-- regardless of caller discipline.
--
-- Deduplication step: remove duplicate (bundle_hash, facet_name) rows, keeping
-- the lowest-id row per pair ("first write wins").  This DELETE is a no-op on
-- any clean DB (the application layer never produced duplicates before migration
-- 024 ran), but it must precede CREATE UNIQUE INDEX to avoid a constraint
-- violation on DBs where concurrent inserts previously occurred.
--
-- State DB only — zero optimization-DB placement (architecture constraint 3).

DELETE FROM spec_facets
WHERE id NOT IN (
    SELECT MIN(id)
    FROM spec_facets
    GROUP BY bundle_hash, facet_name
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_spec_facets_bundle_facet
    ON spec_facets (bundle_hash, facet_name);
