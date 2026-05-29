# Phase 1 — M-1: Structural frozen-eval-wall breach tripwire

## Feature
A structural-invariant test set for **M-1** — the AI Advisor's frozen-eval
wall is a queryable structural invariant, not a convention. Asserts the
data-access layer is **structurally incapable** of reading frozen-eval data
via the single read helper, AND a wall-breach query is detectable as a
tripwire.

## Phase
Phase 1.

## Owner agent-type
`quant-test-writer` (RED authoring). Implementation owners:
`persistence-architect` (the COALESCE accessor `advisor_ro_query`,
migration `021_fold_role.sql`), `risk-engine-specialist` (call-site
discipline).

## Source-of-truth references
- `docs/handoff/decision-science-council-synthesis.md` §3.7 H3 — the
  frozen-eval wall filter must be `COALESCE(fold_role,'') !=
  'frozen_eval'`. A bare `!=` **silently hides train/validation rows**
  by failing on NULL.
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §A.8
  A1 — canonical migration name is `021_fold_role.sql` (NOT
  `021_fold_role_columns.sql` as v3 §3.7 mis-quoted).
- `docs/handoff/council-attack-rubric.md` M-1 (★, BINDING) — Advisor's
  data-access layer structurally incapable of reading frozen-eval data;
  wall breach a queryable tripwire.
- `docs/handoff/council-attack-rubric.md` I-2 (★) — Advisor walled off
  from frozen-eval fold during selection.
- Codebase grounding:
  - `database.py` — existing migration discipline and the
    `_MIGRATION_FILES` ordered append list.

## Why
A convention-only wall fails M-1. If any developer code path can bypass
the COALESCE filter, the Overfitting Conscience can read frozen-eval
data and the entire selection-isolation claim is hollow. The wall is a
structural invariant; it is enforced by:

1. A **single** read helper `advisor_ro_query` (or the agreed name)
   that all advisor reads must go through — and is the **only** function
   that filters by fold_role.
2. A **COALESCE-protected** WHERE clause that handles NULL `fold_role`
   safely.
3. A **tripwire query** that detects any DoF-ledger row touching
   frozen-eval data after a spec_bundle's `frozen_at` timestamp.

## Deliverables

### D1. Test file
`tests/database/test_frozen_eval_wall.py`.

### D2. Fixture file
`tests/fixtures/database/frozen_eval_wall_seed.sql` — a SQL seed
producing rows with all three `fold_role` values present:
`'train'`, `'validation'`, `'frozen_eval'`, AND a row with `fold_role
IS NULL` (an untagged row — the precise H3 failure mode).

### D3. Test cases

**Scenario 1 — `test_advisor_ro_query_excludes_frozen_eval_rows`** (the
positive identity).
- Seed the test DB from `frozen_eval_wall_seed.sql`.
- Call `advisor_ro_query("SELECT * FROM <ledger>")` (or the agreed
  helper interface).
- Assert no returned row has `fold_role == 'frozen_eval'`.

**Scenario 2 — `test_advisor_ro_query_excludes_null_fold_role_via_coalesce`**
(the H3 discriminating-power scenario — the load-bearing one).
- Seed includes a row with `fold_role IS NULL`.
- Call `advisor_ro_query("SELECT * FROM <ledger>")`.
- Assert the NULL-tagged row is **also** excluded — because a NULL
  `fold_role` is operationally indistinguishable from
  `fold_role='frozen_eval'` for safety purposes (a forgotten tag must
  fail-safe to "frozen_eval until proven train/validation").
- Discriminating-power: a bare `fold_role != 'frozen_eval'` SQL
  predicate **passes** NULL rows through (SQL three-valued logic: NULL
  != 'frozen_eval' yields NULL, which is filtered out by WHERE — wait,
  actually filtered out → not passed through... but the v3 §3.7 H3
  notes a bare `!=` silently HIDES train/validation rows that have NULL
  by accident). The COALESCE form converts NULL to '' and the
  comparison '' != 'frozen_eval' evaluates to TRUE → the row is
  RETURNED (which is the wrong default — a row with no fold_role label
  must NOT be served to the Advisor). The test asserts the **safe**
  semantics: untagged rows are excluded. Implementation note: the
  exact COALESCE predicate the persistence-architect plan adopts is
  `COALESCE(fold_role,'NULL_SENTINEL') NOT IN ('frozen_eval',
  'NULL_SENTINEL')` OR equivalent — the test asserts the EFFECT
  ("untagged rows are excluded"), not the exact SQL string, so
  implementation has freedom on the predicate form.

**Scenario 3 — `test_advisor_ro_query_includes_train_and_validation_rows`**
(negative identity — guard against over-filtering).
- Seed includes train and validation rows with non-NULL fold_role.
- Call `advisor_ro_query(...)`.
- Assert the train and validation rows ARE returned.
- Discriminating-power: a buggy "exclude everything if any row has
  frozen_eval" predicate fails this scenario.

**Scenario 4 — `test_only_advisor_ro_query_is_an_advisor_read_path`**
(architectural-invariant — the load-bearing one).
- AST-scan the entire `ai_advisor/` module tree.
- Assert every SELECT-style SQLite call inside Advisor code routes
  through `advisor_ro_query` (or the named helper). No raw
  `cursor.execute("SELECT ...")` in Advisor code.
- Implementation: the scan uses Python AST; it finds `Call` nodes
  whose attribute chain includes `.execute` on names that look like
  cursors. Whitelist: known internal cursor uses inside the helper
  itself.
- Discriminating-power: catches a developer who adds a new Advisor
  routine that opens its own cursor — bypassing the wall.

**Scenario 5 — `test_wall_breach_tripwire_query_detects_post_freeze_frozen_eval_touch`**
(M-1 tripwire — the queryable invariant).
- Seed: a `spec_bundles` row with `frozen_at = T0`, AND a
  `researcher_dof_ledger` row with `ts > T0` AND a `fold_role` link
  resolving to `'frozen_eval'`.
- Call the tripwire query (the documented one — its SQL lives in
  `docs/handoff/wall_breach_tripwire.sql` per persistence-architect's
  plan, or as a function in `database.py`).
- Assert the query returns exactly **one** row corresponding to the
  seeded breach.
- Mutation: in a second sub-scenario, remove the breach row from the
  seed; assert the query returns zero rows.
- Discriminating-power: catches a tripwire whose JOIN is broken
  (always-zero, false-clean) or whose filter is inverted (always-fires,
  noise).

**Scenario 6 — `test_canonical_migration_name_is_021_fold_role_sql`**
(H-8 A1 — drafting defect guard).
- Read `database.py`'s `_MIGRATION_FILES` list.
- Assert it contains `'021_fold_role.sql'` (the canonical name per
  council-converged-migration-plan).
- Assert it does NOT contain `'021_fold_role_columns.sql'` (the
  v3-draft-mis-quoted name that would cause silent migration-skip per
  the `FileNotFoundError`-caught-and-swallowed path at
  `database.py:780-781`).
- Discriminating-power: A1 specifically calls this out as a BLOCKING
  defect because a name mismatch is silently skipped, never recorded
  in `schema_migrations`, and retried forever every startup.

### D4. Test naming
- `test_advisor_ro_query_excludes_frozen_eval_rows`
- `test_advisor_ro_query_excludes_null_fold_role_via_coalesce`
- `test_advisor_ro_query_includes_train_and_validation_rows`
- `test_only_advisor_ro_query_is_an_advisor_read_path`
- `test_wall_breach_tripwire_query_detects_post_freeze_frozen_eval_touch`
- `test_canonical_migration_name_is_021_fold_role_sql`

## Dependencies
- BLOCKED BY: migration `021_fold_role.sql` (persistence-architect's
  Phase-1 deliverable).
- BLOCKED BY: the `advisor_ro_query` helper (persistence-architect).
- BLOCKED BY: the documented tripwire query (persistence-architect).

## Golden-fixture tests required
- `tests/fixtures/database/frozen_eval_wall_seed.sql`.

## Definition of Done
- [ ] Test file committed at `tests/database/test_frozen_eval_wall.py`.
- [ ] Seed SQL committed.
- [ ] All six scenarios RED on `main`.
- [ ] Scenario 2 (NULL handling) explicitly documents the H3
  failure-mode and the COALESCE / safe-default fix.
- [ ] Scenario 4 (AST scan) lists the whitelist of allowed direct-cursor
  call sites — and any new whitelist entry requires a code-review note
  citing M-1.
- [ ] Scenario 6 (migration filename) is a permanent fixture against
  the A1 silent-skip failure mode.

## Risk callouts
- **AST-scan false positives.** Scenario 4 scans the entire
  `ai_advisor/` tree. Test-side code, mocks, and the helper itself
  legitimately use `cursor.execute`. The whitelist is therefore
  load-bearing and must be reviewed every time it changes. A reviewer
  who silently adds the whitelist entry without a code-review note
  has nullified M-1.
- **Tripwire query performance.** The tripwire query runs over
  potentially-large `researcher_dof_ledger`; persistence-architect's
  plan must index `(spec_bundle_id, fold_role, ts)`. The test
  asserts behavior, not perf; perf is the persistence-architect's
  concern.
- **Migration ordering.** `021_fold_role.sql` adds the `fold_role`
  column. Tests that pre-date `021` see the OLD schema; the test
  fixture's seed SQL must run AFTER migration `021` is applied — the
  test harness handles this by running ALL migrations before seed.

## Out of scope
- The Advisor's own logic / output — I-1, I-3 (separate plans).
- The Advisor's read paths into Composer/Alpaca APIs — I-3 logging
  redaction is a separate plan.
- Phase-2 path-bank wall enforcement — same M-1 discipline, separate
  Phase-2 plan.
