-- Seed fixture for frozen-eval wall tests.
-- Run AFTER all migrations have been applied (including 021_fold_role.sql).
--
-- Produces rows in three distinct tables:
--   1. autotune_runs         — fold_role corpus for advisor_ro_query wall tests
--   2. spec_bundles          — one bundle with frozen_at set, for tripwire tests
--   3. researcher_dof_ledger — rows spanning pre-freeze and post-freeze timestamps
--
-- Four fold_role values present in autotune_runs:
--   'train'       — must be returned by advisor_ro_query
--   'validation'  — must be returned by advisor_ro_query
--   'frozen_eval' — must be blocked by advisor_ro_query
--   NULL          — untagged (legacy) row; safe default is excluded (fail-safe)
--
-- Tripwire scenario: the researcher_dof_ledger row with created_at > spec_bundles.frozen_at
-- AND fold_role = 'frozen_eval' must be detected by the wall-breach tripwire query.
-- The honest-workflow row (created_at < frozen_at) must NOT be detected.

-- 1. autotune_runs seed -------------------------------------------------------
-- Idempotent guard: the autouse conftest does not pre-insert these specific IDs.
INSERT OR IGNORE INTO autotune_runs (run_timestamp, symphony_id, fold_role)
VALUES
    ('2026-01-01T09:30:00', 'seed-train-wall',       'train'),
    ('2026-01-01T09:31:00', 'seed-validation-wall',  'validation'),
    ('2026-01-01T09:32:00', 'seed-frozen-wall',      'frozen_eval'),
    ('2026-01-01T09:33:00', 'seed-null-wall',        NULL);

-- 2. spec_bundles seed --------------------------------------------------------
-- bundle_hash is a fixed test value; frozen_at is set to a known timestamp T0.
-- T0 = '2026-01-15T12:00:00' — the freeze point for tripwire scenario.
INSERT OR IGNORE INTO spec_bundles
    (bundle_hash, frozen_at, facets_json, horizon_bars, cvar_alpha, generator_family)
VALUES
    (
        'deadbeef0000000000000000000000000000000000000000000000000000abcd',
        '2026-01-15T12:00:00',
        '{"generator_family":"crra-seed","horizon_bars":63}',
        63,
        0.05,
        'crra-seed'
    );

-- 3. researcher_dof_ledger seed -----------------------------------------------
-- Row A: honest workflow — created_at BEFORE frozen_at.  Must NOT fire tripwire.
-- Row B: wall breach — created_at AFTER frozen_at, fold_role resolves to 'frozen_eval'.
--        Must be detected by the tripwire query.
--
-- researcher_dof_ledger does not exist until migration 021 is applied.
-- If the migration has not run, these INSERTs will fail and the seed is
-- partially complete — the test harness will surface the missing table error.
INSERT OR IGNORE INTO researcher_dof_ledger
    (spec_bundle_id, fold_role, created_at, observation_type, payload_json)
VALUES
    (
        -- Row A: pre-freeze — honest use; spec_bundle_id references the bundle above.
        (SELECT id FROM spec_bundles
         WHERE bundle_hash = 'deadbeef0000000000000000000000000000000000000000000000000000abcd'),
        'train',
        '2026-01-10T08:00:00',   -- before frozen_at 2026-01-15
        'TRAIN_READ',
        '{}'
    ),
    (
        -- Row B: post-freeze breach — fold_role 'frozen_eval' after frozen_at.
        (SELECT id FROM spec_bundles
         WHERE bundle_hash = 'deadbeef0000000000000000000000000000000000000000000000000000abcd'),
        'frozen_eval',
        '2026-01-20T09:00:00',   -- after frozen_at 2026-01-15
        'FROZEN_EVAL_READ',
        '{}'
    );
