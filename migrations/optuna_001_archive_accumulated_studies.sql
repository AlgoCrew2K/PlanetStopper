-- Migration optuna_001: Archive accumulated studies in optuna_studies.db
-- Target DB: optuna_studies.db (Optuna's INTERNAL DB) — NOT alphabot_state.db
-- Trigger: O3 study-name convention fix — autotuner.py now uses
--   study_name=f"{timestamp}__{normalized_name}" with load_if_exists=False,
--   so existing studies with the old bare normalized_name scheme are orphaned.
--   This migration archives them non-destructively by renaming with LEGACY__ prefix.
--
-- Non-destructive: ONLY renames; never deletes rows or trial data.
-- Idempotent: rows already prefixed with LEGACY__ are not double-prefixed
--   (the WHERE clause guards against LEGACY__LEGACY__ accumulation).
--   Rows already in new timestamp__symphony format are also left untouched
--   (they contain the literal '__' substring, detected via INSTR).
--
-- Note: SQLite LIKE uses '_' as a single-char wildcard, so '%__%' would
--   match almost all strings.  INSTR is used instead to detect the literal
--   double-underscore separator '__' reliably.
--
-- Apply via:
--   sqlite3 optuna_studies.db < migrations/optuna_001_archive_accumulated_studies.sql
--
-- Verify after apply:
--   SELECT study_name FROM studies;
--   -- All rows should start with LEGACY__ or the new timestamp__ format.
--
-- Context: see docs/research/dashboard/optuna-tuning-audit.md finding #7
--   and feature-plans/engine-correctness-remediation.md AC-O3.2.

UPDATE studies
SET    study_name = 'LEGACY__' || study_name
WHERE  study_name NOT LIKE 'LEGACY__%'
  AND  INSTR(study_name, '__') = 0;
