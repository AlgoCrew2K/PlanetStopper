-- Migration 001: Normalize existing symphony_name keys to canonical lowercase
-- Trigger: deploy of A6.5 (commit that adds normalize_name() to get/save_symphony_strategy)
-- Risk: rows with mixed-case or whitespace-padded symphony_name become unreachable after deploy
-- Idempotent: safe to re-run; UPDATE no-ops when all rows are already normalized

UPDATE symphony_strategies
SET symphony_name = lower(trim(symphony_name))
WHERE symphony_name != lower(trim(symphony_name));
