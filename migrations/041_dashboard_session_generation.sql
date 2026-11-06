-- Migration 041: dashboard_session_revocation.generation -- the count of logouts, ordering
-- sessions against logouts without the wall clock.
--
-- A session is served only while the generation it was issued under equals the stored one, so
-- a logout ends sessions whose issue stamp lies ahead of the clock after a backward step.
--
-- Risk: additive-only; one column with a constant default on the 040 table.
-- State DB only (architecture constraint 3).
ALTER TABLE dashboard_session_revocation ADD COLUMN generation INTEGER NOT NULL DEFAULT 0;
