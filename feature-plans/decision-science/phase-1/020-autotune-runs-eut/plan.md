# Plan — Migration 020_autotune_runs_eut.sql (additive EUT audit columns; H1 dual-write)

**Feature:** Phase-1 additive EUT audit columns on `autotune_runs`. **H1
hazard: the columns MUST be dual-written to both the migration ALTER
statements AND the `init_db()` `CREATE TABLE autotune_runs` statement in
`database.py`** — or fresh and upgraded DBs diverge.

**Phase:** Phase 1 (HARDEN floor — supports both M1 offline audit and the
`N_effective` haircut consumer).

**Owner agent-type:** `sqlite-specialist`, `quant-test-writer`,
`quant-code-reviewer`. Consumer code: `autotuner.py`
(`risk-engine-specialist`).

## Source-of-truth references

- `docs/handoff/decision-science-council-synthesis.md` §3.5 (Optuna/autotuner
  integration — preserved), §3.7 (`022_autotune_runs_eut.sql` row), §3.7
  H1 (dual-write the columns to BOTH the migration AND the `init_db()`
  CREATE TABLE statement).
- `docs/handoff/council-converged-migration-plan.md` §3.1 row 022, §5 (the
  `N_effective` haircut arithmetic consumer; `d_spec` and `n_effective` are
  columns on this table), §6 H1 (the Q7 hazard — the duplicate-column-name
  swallow at `database.py:794-803` makes the dual-write the **safe** pattern,
  not a defect).
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §A.0 (H1
  CRRA-unbounded — `WEALTH_ARG_FLOOR` is the W-H4 fix; the floor value
  itself does not persist here, but its application is the per-trial
  precondition that lets `ce_metric` be finite).
- Codebase: `database.py:96-111` (existing `CREATE TABLE autotune_runs`),
  `database.py:743-756` (`_MIGRATION_FILES`).

## Why

The autotuner gains a CRRA-EU objective (M1) in Phase 1. Eight new audit
columns persist the EUT machinery's per-run state so the Overfitting
Conscience and the Specification Critic can read it; a ninth column links
the EUT study to its paired heuristic study so `optuna-compare` can diff
them by name.

This is the **single H1-class migration in Phase 1.** The hazard is
catastrophic-but-easy-to-fix: forget the `init_db()` mirror → fresh DBs
silently lack the columns → the EUT writer fails silently on insert →
production runs without an EUT audit trail. The mitigation is one extra
edit. This plan **calls H1 out as a named implementation step**, not a
footnote.

## Numbering

Council `022_autotune_runs_eut.sql` → codebase `020_autotune_runs_eut.sql`
(shift +1; plan `016-spec-bundles` carries the full table).

## Deliverables

1. **`migrations/020_autotune_runs_eut.sql`** — nine `ALTER TABLE autotune_runs
   ADD COLUMN` statements:
   - `spec_bundle_id              TEXT    DEFAULT NULL`  (soft FK to
     `spec_bundles.bundle_hash`)
   - `d_spec                      INTEGER DEFAULT NULL`  (the
     bundle-distinct count of `BACKTEST_SELECTION` rows in
     `researcher_dof_ledger`, as defined in council §5)
   - `n_effective                 INTEGER DEFAULT NULL`  (the additive
     haircut count `N_optuna + S`)
   - `ce_metric                   REAL    DEFAULT NULL`  (certainty-
     equivalent in return units — the display-time inverse of `mean(U)`)
   - `cvar_feasible               INTEGER DEFAULT NULL`  (Phase-2 surface,
     persisted in Phase 1 as `NULL`; safe forward-compatible additive)
   - `gamma                       REAL    DEFAULT NULL`  (the frozen risk-
     aversion parameter)
   - `lambda_budget               REAL    DEFAULT NULL`  (Phase-2 only —
     persisted `NULL` in Phase 1; council §3.6 notes "HARDEN has no lambda")
   - `overfitting_verdict         TEXT    DEFAULT NULL`  (e.g.
     `D_spec=1,N_effective=N_optuna,no_violations`)
   - `paired_heuristic_study_name TEXT    DEFAULT NULL`  (lets
     `optuna-compare` diff an EUT study against its paired heuristic study
     by name — single-DB-per-read)
2. **`database.py` — `init_db()` MIRROR (H1 — BINDING).** The `CREATE TABLE
   autotune_runs` block at `database.py:96-111` is extended to include all
   nine columns inline. The migration's ALTER is the upgrade path; the
   `init_db()` mirror is the fresh-DB path. The
   `run_migrations()` duplicate-column-name swallow
   (`database.py:794-803`) reconciles the overlap — a fresh DB has the
   columns inline; the migration's `ALTER` raises `duplicate column name`;
   the swallow records the migration applied. **This is the only safe
   pattern**; omitting `init_db()` fails fresh DBs, omitting the migration
   fails upgraded DBs.
3. **`_MIGRATION_FILES`** — append `"020_autotune_runs_eut.sql"`.
4. **Fixture refresh** — at least one fixture row carries:
   - a non-null `spec_bundle_id` referencing a `016_spec_bundles` seed
   - `d_spec = 1`, `n_effective` equal to the row's pre-existing
     `selection_tstat`-counted `N_optuna` (so the property "S=0 →
     n_effective == N_optuna" holds on the seed)
   - non-null `gamma`, `ce_metric`, `overfitting_verdict`
   - **plus** at least one legacy-shape fixture row where all nine new
     columns are NULL (the historical-row backwards-compatibility
     assertion).
5. **Schema-validator test** — fixture DB has all nine new columns on
   `autotune_runs`; the legacy-row case is preserved; the EUT-row case is
   populated.

## Dependencies

- **Hard-depends on `016_spec_bundles.sql`** — `spec_bundle_id` soft FK.
- **Hard-depends on `018_researcher_dof_ledger.sql`** — `d_spec` and
  `n_effective` are derived from its rows.
- **Consumer in Phase 1:** `autotuner.py` writes these columns at the end of
  each autotune run (the EUT path; the heuristic path leaves them NULL —
  legitimate historical-row shape).

## Golden-fixture tests required (RED before GREEN)

1. **Fresh-DB has all nine columns inline** — open a freshly-initialised DB
   via `init_db()` only (no `run_migrations()`); assert the nine columns
   exist on `autotune_runs`. (H1 fresh-DB leg.)
2. **Upgraded-DB has all nine columns via migration** — start with a
   pre-migration `autotune_runs` shape (the `:96-111` shape minus the nine);
   run `run_migrations()`; assert the nine columns now exist. (H1
   upgraded-DB leg.)
3. **Dual-write produces no schema delta** — apply the migration to a
   fresh DB whose `init_db()` already added the columns; assert no error
   surfaces above the duplicate-column swallow, and `schema_migrations`
   records `020_autotune_runs_eut.sql` as applied.
4. **Historical-row NULL backwards-compatibility** — a fixture row with all
   nine new columns NULL reads cleanly through any existing autotune-runs
   read accessor without raising.
5. **`S=0` property** — for an EUT-shape fixture row where
   `researcher_dof_ledger` has no `BACKTEST_SELECTION` rows referencing the
   bundle: the row's `n_effective == N_optuna`. (Council §2.2 property 1
   byte-identity.)
6. **`cvar_feasible` / `lambda_budget` Phase-1 NULL** — the EUT-shape
   fixture row has these as NULL (Phase-1 honest shape; council §3.4 "no
   path generator, no CVaR trigger" → no feasibility, no budget).
7. **`paired_heuristic_study_name` round-trip** — a row with
   `paired_heuristic_study_name='my_heuristic_study'` is queryable;
   `optuna-compare` can find the paired study by exact name match.
8. **Schema-validator test** — fixture DB has all nine new columns; the
   legacy-row + EUT-row shapes both present.

## Definition of Done

- Migration applies cleanly on both fresh and upgraded DBs.
- `init_db()` mirror present (the binding H1 dual-write).
- Fixture DBs rebuilt; both row shapes present.
- All eight tests pass GREEN.
- `pytest tests/` full tree passes.
- `_MIGRATION_FILES` appended in order; nothing reordered.

## Risk callouts

- **H1 — DUAL-WRITE IS BINDING.** This is the single most important
  defect-prevention obligation in Phase 1. The implementer:
  1. writes the migration ALTERs;
  2. **in the same PR** edits `database.py:96-111`'s `CREATE TABLE
     autotune_runs` to add all nine columns inline;
  3. runs the §1 + §2 RED tests (fresh-DB + upgraded-DB legs) before
     handoff.
  Skipping step (2) is the §A.8 A1-class silent failure: fresh DBs lack
  columns, the EUT writer fails silently, production runs without an audit
  trail. The duplicate-column swallow at `database.py:794-803` is the
  reconciler; it does not paper over a missing mirror.
- **`lambda_budget` and `cvar_feasible` are Phase-2 columns persisted
  Phase-1-NULL.** This is the additive-first discipline (council H5
  reversibility taxonomy): cheaper to ship them NULL now than to add a
  later ALTER that re-tests the H1 dual-write hazard.
- **`gamma` is a single REAL.** The W-H4 `WEALTH_ARG_FLOOR` is **not** a
  column — it lives in `math_engine.py` / `autotuner.py` as a named
  module-scope constant (project no-magic-numbers rule). What persists here
  is the gamma value frozen for the run, not the floor used to evaluate
  CRRA at it.
- **`n_effective` and `d_spec` are honest-Phase-1 either equal-to-or-
  derivable-from existing columns.** Council §5 explicit: the schema impact
  of the multiplicative→additive correction is **zero**; only the consumer
  arithmetic changed. Persisting both columns is for audit transparency.

## Out of scope

- The CRRA-EU `compute_crra_eu_tstat` implementation (lives in
  `autotuner.py`; tuning-architect domain).
- The CRRA `u(W)` flooring logic + `WEALTH_ARG_FLOOR` (lives in
  `math_engine.py`; risk-engine-specialist + tuning-architect; W-H4
  binding fix).
- The `optuna-compare` skill changes (orthogonal; consumes the new
  `paired_heuristic_study_name` column read-only).
- Phase-2 `lambda` Optuna-search ban — council §5.7 "lambda frozen by
  mandate, NOT Optuna-searched" governs at the autotuner level; this plan
  just provides the NULL persistence slot.
