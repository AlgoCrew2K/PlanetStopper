-- Migration 022: spec_bundles — add integer id column (additive, reversibility fix).
-- Rationale: cycle-016 created spec_bundles with bundle_hash TEXT NOT NULL PRIMARY KEY.
-- SQLite does not support ADD COLUMN ... PRIMARY KEY AUTOINCREMENT, so the integer id
-- cannot be added in-place as a PK. We add it as a nullable column and backfill from
-- SQLite's implicit rowid, which is always an integer and always non-NULL for existing rows.
-- Application layer uses id for lookup (validate_nn1_compliance, get_spec_bundle); the
-- canonical PRIMARY KEY constraint remains on bundle_hash per the additive-first rule.
-- State DB only — zero optimization-DB placement (architecture constraint 3).

ALTER TABLE spec_bundles ADD COLUMN id INTEGER;
UPDATE spec_bundles SET id = rowid WHERE id IS NULL;

-- AFTER INSERT trigger: backfill id from rowid for rows inserted after this migration runs.
-- Required because ALTER TABLE ADD COLUMN cannot specify DEFAULT (rowid) — there is no
-- compile-time expression that yields the row's own rowid. The trigger fills the gap
-- so that any INSERT (whether from insert_spec_bundle or direct SQL) always produces
-- a non-NULL id without requiring the caller to know about the backfill convention.
CREATE TRIGGER IF NOT EXISTS spec_bundles_backfill_id
AFTER INSERT ON spec_bundles
FOR EACH ROW
WHEN NEW.id IS NULL
BEGIN
    UPDATE spec_bundles SET id = NEW.rowid WHERE bundle_hash = NEW.bundle_hash;
END;
