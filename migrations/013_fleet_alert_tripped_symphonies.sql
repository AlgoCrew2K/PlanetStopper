-- 013: add tripped_symphonies column to fleet_alert_state.
-- Additive only: NULLable TEXT column, no existing column modified or dropped.
-- Stores a JSON array of symphony display names that tripped the fleet alert.
-- Old rows (NULL) are safe: read_fleet_alert() defaults to [].
-- Idempotent via schema_migrations tracker.

ALTER TABLE fleet_alert_state ADD COLUMN tripped_symphonies TEXT NULL DEFAULT NULL;
