-- Migration 025: advisor_observations.symphony_id (S3-AUDIT-004 fix)
-- Denormalized symphony_id so /api/advisor-observations?symphony_id=<id>
-- can filter in a single SELECT instead of the 3x subject_type fan-out.
-- Additive-first: NULLable, DEFAULT NULL — existing rows unaffected.
ALTER TABLE advisor_observations ADD COLUMN symphony_id TEXT DEFAULT NULL;
