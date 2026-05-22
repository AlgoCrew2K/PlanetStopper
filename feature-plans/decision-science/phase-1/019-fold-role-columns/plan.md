# Plan — Migration 019_fold_role_columns.sql (structural frozen-eval wall)

**Feature:** Phase-1 structural frozen-eval wall — `fold_role` ALTER + the
`COALESCE(fold_role,'') != 'frozen_eval'` accessor + the wall-breach
tripwire.

**Phase:** Phase 1 (HARDEN floor — Phase 1 unconditionally, council §3.7:
"the moment gamma is frozen, the structural proof the freeze was clean must
already exist; a wall retrofitted in Phase 2 cannot certify a Phase-1
freeze").

**Owner agent-type:** `sqlite-specialist`, `quant-test-writer`,
`quant-code-reviewer`.

## Source-of-truth references

- `docs/handoff/decision-science-council-synthesis.md` §3.7 — `021` "(the
  frozen-eval wall) **is Phase 1**"; H3 (`COALESCE` filter, not bare `!=`).
- `docs/handoff/council-converged-migration-plan.md` §3.1 row 021, §6 H3 (the
  SQL-NULL trap), §6 H5 (ALTER reversibility taxonomy).
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §A.8 A1 —
  council canonical name is `021_fold_role.sql`; this plan adopts the
  council's intent (the column + the wall) with the codebase renumbering to
  `019_fold_role_columns.sql`.
- Attack rubric: M-1 (the structural Advisor wall) and J-3 (legacy-engine
  retention).

## Why

The AI Advisor (Phase 1: Overfitting Conscience + Specification Critic) must
be **structurally walled off** from the frozen-eval fold so its reads can
never accidentally peek at the held-out evaluation data. NN1 violation by
inadvertent Advisor query is exactly the failure mode the wall prevents.

The wall has three parts, all in this plan:

1. **The column** — `fold_role` on the fold-partitioned replay table(s).
2. **The accessor filter** — `WHERE COALESCE(fold_role,'') != 'frozen_eval'`.
   The bare `WHERE fold_role != 'frozen_eval'` evaluates NULL to NULL/falsy
   and **silently hides** legitimate train/validation rows. (H3.)
3. **The wall-breach tripwire** — a runtime check that fires when an
   Advisor-tagged query touches a `frozen_eval` row.

## Numbering

Council `021_fold_role.sql` → codebase `019_fold_role_columns.sql` (shift +1;
plural "columns" because the underlying fold-partitioned table(s) plural may
mean the ALTER fires on more than one table — the implementer must enumerate
candidate tables at implementation time. The current candidate is the
backtest-replay rows; if the implementer determines a single-table scope,
the file can be renamed `019_fold_role_column.sql` at implementation, but
the migration-list entry stays as planned).

## Deliverables

1. **`migrations/019_fold_role_columns.sql`** — an `ALTER TABLE` per
   fold-partitioned table:
   - `ALTER TABLE <table> ADD COLUMN fold_role TEXT DEFAULT NULL`
   - Idempotent under `run_migrations`'s "duplicate column name" swallow
     (`database.py:794-803`).
2. **`database.py`** — the accessor surface:
   - `advisor_ro_query(sql, params=())` — opens a `get_ro_connection()` and
     **rewrites or validates** that any `fold_role`-touching predicate is
     `COALESCE(fold_role,'') != 'frozen_eval'`. A bare `fold_role !=` in the
     caller's SQL is rejected at the helper before execution (a defensive
     check; the helper is the only entry the Advisor uses). The helper is
     the **only** read entry from Advisor code paths — calling
     `get_connection()` / `get_ro_connection()` directly from advisor code
     is a lint-banned pattern (a `grep` test in CI enforces).
   - The helper is documented as "the Advisor's only door to the state DB."
3. **The wall-breach tripwire** — a small instrumentation in
   `advisor_ro_query`:
   - If a row returned by the query has `fold_role = 'frozen_eval'`, the
     helper **raises** and writes a `WALL_BREACH` row to
     `advisor_observations` (plan `017_advisor_observations`) with the
     offending SQL fragment. This is the structural enforcement: the
     `COALESCE` filter is the prevention; the tripwire is the audit.
4. **`_MIGRATION_FILES`** — append `"019_fold_role_columns.sql"`.
5. **`init_db()` dual-write — NOT REQUIRED FOR THIS MIGRATION.** `fold_role`
   is added by ALTER **only**; the `init_db()` `CREATE TABLE` statements for
   the fold-partitioned table(s) do **not** add the column on a fresh DB.
   That looks like the H1 hazard but **is not**: a fresh DB has no
   pre-existing rows so all rows enter with `fold_role` set by the engine
   writer at insert time (the writer is the one place that assigns the
   role); upgraded DBs get the column via this migration and legacy rows
   read NULL → `COALESCE` makes them invisible to the Advisor wall, which is
   the safe default. **Important contrast with `020_autotune_runs_eut.sql`,
   which DOES need the H1 dual-write** — see that plan.
6. **Fixture refresh** — fold-partitioned fixture rows gain a `fold_role`
   value across `train` / `validation` / `frozen_eval`; the schema-validator
   test asserts column presence; at least one `frozen_eval` row exists so
   the tripwire test has a target.

## Dependencies

- **Hard-depends on `017_advisor_observations.sql`** — the tripwire writes to
  it.
- **Consumers in Phase 1:** any Advisor read code path; the Overfitting
  Conscience's auditable queries; the Specification Critic's facet reads.

## Golden-fixture tests required (RED before GREEN)

1. **`COALESCE` filter is correct on NULL** — a fixture row with
   `fold_role IS NULL`: the `COALESCE(fold_role,'') != 'frozen_eval'`
   accessor includes it (the train/validation legacy row visible to the
   Advisor); the **bare** `fold_role != 'frozen_eval'` excludes it (the bug
   the wall prevents). The test runs both filters explicitly and asserts the
   different counts. (H3.)
2. **`frozen_eval` rows are blocked by the accessor** — fixture row with
   `fold_role = 'frozen_eval'`: not in the accessor's returned set.
3. **Wall-breach tripwire fires** — an instrumented test that bypasses the
   filter (constructs raw SQL with a deliberate `OR 1=1` predicate so a
   `frozen_eval` row reaches the result set): `advisor_ro_query` raises and
   writes a `WALL_BREACH` row to `advisor_observations`. The fixture asserts
   the row was written before the raise.
4. **Lint test — only `advisor_ro_query` is used from advisor code paths.**
   A grep across the Advisor module rejects direct `get_connection` /
   `get_ro_connection` imports. This is the structural enforcement that the
   wall has no side door.
5. **Bare `fold_role !=` in the caller's SQL is rejected** — the helper
   validates the predicate before execution; a bare `!=` filter raises
   `ValueError` at the helper call site, not at SQL execution time.
6. **`run_migrations` swallow-on-duplicate** — applies clean on a fresh DB
   whose `init_db()` `CREATE TABLE` *would* include `fold_role` (if a
   future implementer added it); the duplicate-column swallow records the
   migration applied. The test is non-binding for this plan (no current
   dual-write) but documents the safe-on-future-dual-write property.
7. **Schema-validator test** — fixture DB has `fold_role` on the
   fold-partitioned table(s); at least one row of each role
   (`train`/`validation`/`frozen_eval`/`NULL`) present.

## Definition of Done

- Migration applies cleanly; fixture DBs rebuilt with all four
  `fold_role` values represented (including NULL for the legacy-row case).
- All seven tests pass GREEN.
- `pytest tests/` full tree passes.
- The Advisor module has zero non-`advisor_ro_query` connection imports
  (lint).
- The wall-breach tripwire writes to `advisor_observations` before raising.

## Risk callouts

- **H3 — the SQL-NULL trap is the single most important defect to prevent.**
  A bare `WHERE fold_role != 'frozen_eval'` silently passes NULL→falsy and
  hides train/validation rows (council §3.7 H3, §6 H3). The `COALESCE`
  filter is binding; the lint check + the predicate-rejection test are
  belt-and-braces.
- **Reversibility — ALTER is abandon-in-place only** (council §6 H5). Once
  `fold_role` ships, the column is permanent. Reversal means reverting the
  accessor + the writer, **not** dropping the column. The charter
  anti-pattern "never drop a column in the migration that added it"
  governs.
- **The wall-breach tripwire must not blow up production.** It writes the
  observation row **first**, then raises. If the write itself fails (rare —
  full-disk, locked DB), the helper still raises but does so without the
  audit row; the H4 telemetry helper plan governs the live/replay mode for
  the write inside the tripwire (plan `phase-1/h4-telemetry-helper`).
- **`fold_role` values are an enum at application level, NOT enforced by
  CHECK.** The codebase precedent is application-level enum (no CHECK
  constraints elsewhere in `init_db()`); a CHECK would block additive
  schema evolution (e.g. adding a `holdout` role later).

## Out of scope

- The decision about which writer assigns `fold_role` at insert time (lives
  in `autotuner.py` / the replay harness; risk-engine-specialist domain).
- The `gamma` freeze writer itself (lives in `autotuner.py`; tuning-
  architect domain).
- The Phase-2 deeper Advisor walls (the LLM-authored roles' read scopes —
  same `advisor_ro_query` helper covers them; no new schema).
