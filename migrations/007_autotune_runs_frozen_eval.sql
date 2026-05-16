-- O6: Frozen evaluation window — additive schema extension.
-- Adds per-run validation and frozen-eval Sortino metrics to autotune_runs
-- (alphabot_state.db). Both columns are NULLable with DEFAULT NULL so
-- pre-existing rows are preserved. Idempotent: re-applying raises
-- "duplicate column name" which callers should swallow.
--
-- Reference: López de Prado 2018, Advances in Financial Machine Learning, Ch. 7.4.

ALTER TABLE autotune_runs ADD COLUMN validation_sharpe  REAL DEFAULT NULL;
ALTER TABLE autotune_runs ADD COLUMN frozen_eval_sharpe REAL DEFAULT NULL;
