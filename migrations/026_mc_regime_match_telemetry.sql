-- Migration 026: mc_regime_match_quality telemetry columns on cvar_diagnostics
-- Additive-first (NULLable + DEFAULT): two new columns record the per-cycle
-- regime-match-quality guard result alongside the existing CVaR diagnostic row.
-- Zero impact on existing rows; existing call sites continue to write NULL for
-- both columns until updated to pass the new keyword args.
-- Reference: vision-audit Critical Rec #2 / math-reaudit Surface 3;
--   compute_regime_match_quality (math_engine.py).

ALTER TABLE cvar_diagnostics
    ADD COLUMN mc_regime_match_mean_dist2 REAL DEFAULT NULL;    -- mean squared Mahalanobis-style distance (None sentinel when eligible pool insufficient)

ALTER TABLE cvar_diagnostics
    ADD COLUMN mc_regime_match_suppressed INTEGER DEFAULT NULL; -- 1 = MC veto suppressed (is_unprecedented=True); 0 = not suppressed; NULL = guard not run
