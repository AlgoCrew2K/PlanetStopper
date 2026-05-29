# Plan — Engine-audit: migration-replay vs init_db direct-create divergence (H1-class hazard sweep)

**Feature:** A full-stack audit of the **H1 dual-write hazard** — the
class of defect where a column or table is added by both `init_db()`
(fresh-DB path) and a migration (upgrade-DB path), and the two
definitions silently drift. The synthesis §3.7 + the council H1 named
`autotune_runs` (Phase 1's `020`-migration) as the one H1-class
migration; this audit verifies the rest of the codebase has no other
latent H1 surface.

**Phase:** Engine-audit (post-Phase-1; this is the most consequential
audit because an H1 defect is silent in test, catastrophic in
production).

**Owner agent-type:** `sqlite-specialist` (audit author),
cross-reviewed by `quant-code-reviewer`.

## Source-of-truth references

- `docs/handoff/decision-science-council-synthesis.md` §3.7 (H1
  hazard — "the columns must be dual-written to BOTH the migration
  ALTER statements AND the `init_db()` CREATE TABLE autotune_runs
  statement in `database.py`").
- `docs/handoff/council-converged-migration-plan.md` §6 H1 (the Q7
  hazard analysis — the `database.py:794-803` duplicate-column-name
  swallow is what makes the dual-write **safe** when done, and silent
  when forgotten).
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §A.8
  A1 (the migration-name silent-skip mode that compounds H1).
- Codebase: `database.py:63-148` (`init_db()` `CREATE TABLE`s),
  `database.py:759-807` (`run_migrations()`), `migrations/*.sql`.

## Why

The H1 hazard is the single most dangerous persistence-layer defect
class for this codebase because:

1. **It is silent in test.** A fresh fixture DB built via `init_db()`
   has the columns inline; a fixture DB rebuilt via
   `run_migrations()` has the columns via ALTERs; both DBs LOOK
   identical at the SQL level. The defect only surfaces when one path
   is taken in dev and the other in production.
2. **It compounds with the migration-name silent-skip** (§A.8 A1).
   `run_migrations` (`database.py:780-806`) catches generic
   exceptions on apply, including `FileNotFoundError` for a
   mistyped filename — and records the migration as **not applied**.
   The next startup re-attempts and fails again. The operator
   discovers it only when a downstream query hits a missing column.
3. **The duplicate-column-name swallow is the reconciler, not the
   detector.** `database.py:794-803` catches "duplicate column
   name" and records the migration applied. That makes a
   correctly-done dual-write idempotent on a fresh DB. It also
   silently absorbs a wrong-table column-add ("duplicate" because
   `init_db()` had it on a different table) — which is the H1 trap.

This audit makes every H1 surface explicit so a code reviewer reads
one table.

## Sub-audits

1. **Enumerate every `CREATE TABLE` in `init_db()`.** From
   `database.py:63-148`: `bot_state`, `execution_lock`,
   `chart_history`, `chart_archive`, `symphony_strategies`,
   `autotune_runs`, `llm_suggestions`, plus any others added between
   Phase 1's first commit and this audit. Acceptance: a captured
   enumeration with line numbers.
2. **Enumerate every `CREATE TABLE` and `ALTER TABLE ADD COLUMN`
   in `migrations/*.sql`.** For each, classify the target table
   against §1:
   - **Dual-defined** — the table appears in both `init_db()` and
     in a migration. This is the H1 surface.
   - **Migration-only** — the table appears only in a migration
     (new table; H1 zero exposure).
   - **`init_db()`-only** — legacy/pre-migration; the migration
     framework was introduced later (`004_schema_migrations_tracker`
     onward). H1 zero exposure.
3. **For each dual-defined table — verify every migration's
   columns are mirrored in `init_db()`.** For each `ALTER TABLE
   ADD COLUMN` migration on a dual-defined table, the column must
   appear in the `init_db()` `CREATE TABLE` for that table.
   Acceptance: zero columns in the migration that are absent from
   `init_db()`; zero columns in `init_db()` that are absent from the
   migration (the latter is a different drift — a column added
   inline in `init_db()` without a migration leaves upgraded DBs
   without the column).
4. **Migration filename → `_MIGRATION_FILES` entry exact match.**
   For every `migrations/*.sql` file, an exact-string match exists
   in `_MIGRATION_FILES` (`database.py:743-756`). For every entry in
   `_MIGRATION_FILES`, the file exists on disk. Acceptance: bijection.
   §A.8 A1 binding — a mismatch is a `FileNotFoundError` caught by
   the generic except, silently retried forever.
5. **`run_migrations` failure-mode hardening — proposed.** The
   current `run_migrations` (`database.py:780-806`) catches
   `Exception` broadly and logs an error. A migration-name typo
   becomes a silent forever-retry. **Proposed remediation** (out of
   scope for this audit; in scope as a derived defect):
   `run_migrations` distinguishes
   `FileNotFoundError` (raise hard — the operator MUST see) from
   "duplicate column name" (the existing safe swallow) from other
   `sqlite3.Error` (log + raise; the operator MUST see). The audit
   surfaces the proposal; the remediation is a separate TDD plan.
6. **Phase-1 `020_autotune_runs_eut.sql` is the named H1 surface.**
   Verify post-Phase-1 that `database.py:96-111` lists all nine new
   EUT columns inline AND the migration ALTERs all nine. Acceptance:
   exact-match column list, both sides.
7. **No `init_db()` mirror for new-table migrations.** Verify
   `016_spec_bundles.sql`, `017_advisor_observations.sql`,
   `018_researcher_dof_ledger.sql`, `021_cvar_diagnostics.sql` are
   each `CREATE TABLE IF NOT EXISTS` only — no `init_db()` mirror
   (adding one would create a maintenance surface for no safety
   gain; council §6 H2). Acceptance: zero `init_db()` mirrors for
   these four tables.

## Deliverables

1. **`docs/handoff/h1-divergence-audit-<date>.md`** — the captured
   table:
   | Table | `init_db()` ref | Migration refs | Classification | H1
   exposure |
   The classification column is `dual-defined` / `migration-only` /
   `init_db-only`; the H1 exposure column is `audited-clean` /
   `audited-defect` / `n/a`.
2. **`tests/audit/test_h1_dual_write.py`** — programmatic version of
   the §3 sub-audit. For each dual-defined table, build a fresh
   `init_db()` DB and a migration-only DB; assert the `PRAGMA
   table_info` of both tables match column-for-column.
3. **`tests/audit/test_migration_files_bijection.py`** — the §4
   sub-audit as a test.
4. **A surfaced remediation plan** if §5 finds the broad-catch
   defect — the audit does not implement the fix; a separate TDD
   plan does.

## Dependencies

- **Hard-depends on Phase-1 floor being applied** (including the
  `020_autotune_runs_eut.sql` migration with its `init_db()` mirror).
- **Independent of Phase 1.5 + Phase 2** (those phases' plans
  inherit this audit's guidance).

## Golden-fixture tests required (RED before GREEN)

1. **Dual-defined-table column parity** — for `autotune_runs`
   (the only Phase-1 dual-defined table), `PRAGMA table_info` from
   `init_db()`-only and from `run_migrations()`-only match
   exactly. (§3 + §6.)
2. **Migration-file bijection** — every entry in `_MIGRATION_FILES`
   has a matching `migrations/*.sql` file; every file has a
   matching entry. (§4.)
3. **New-table migrations have no `init_db()` mirror** — for
   `016`/`017`/`018`/`021`, grep `database.py` for the table name
   inside `init_db()`; zero matches. (§7.)
4. **Phase-1 EUT columns mirrored** — the §6 sub-audit's
   column-by-column assertion.
5. **No silently-failing migration on a typo'd filename** — RED
   test: add a typo'd entry to `_MIGRATION_FILES`; assert
   `run_migrations()` raises a clear error, not a silent
   retry-forever. (§5 — currently expected to fail; the test
   documents the defect until the remediation plan lands.)

## Definition of Done

- All four §1-§4 tests pass GREEN on the post-Phase-1 codebase.
- §5 test exists and either passes (remediation shipped) or fails
  with a clear pointer to the remediation plan — the failure is the
  visible obligation.
- The audit document is committed.
- Every dual-defined table is `audited-clean` in the captured
  table.

## Risk callouts

- **The §5 broad-catch defect is real and pre-Phase-1.** It is not
  introduced by the Phase-1 floor; it has been present since
  `004_schema_migrations_tracker.sql`. The audit surfaces it; the
  remediation is a separate TDD plan. **Until the remediation
  lands, every PR that adds a migration MUST manually verify the
  filename matches `_MIGRATION_FILES`** — the §4 test catches the
  bijection break, but only if CI is wired.
- **Phase-1's `020_autotune_runs_eut.sql` is the ONE H1 surface
  the council named.** If the audit finds a second H1 surface
  (e.g., a future PR adds a column inline to `init_db()`'s
  `chart_history` without a matching migration), it is a new
  defect; the §3 sub-audit's column-by-column assertion catches
  it.
- **`init_db()` is run on every daemon startup.** The fresh-DB
  semantics are not "only on first install" — `CREATE TABLE IF NOT
  EXISTS` is idempotent; the duplicate-column swallow makes the
  ALTERs idempotent. The fragility is in the dual-write
  *correctness*, not the idempotence.
- **The §3 fixture-comparison test is the only way to catch a
  silent drift.** A unit test against one fixture cannot find
  what only differs between the two paths; the test must build
  both and diff.

## Out of scope

- The §5 remediation (separate TDD plan; filed if §5 fails).
- Optimization-DB schema audit (orthogonal; the optimization DB has
  no `init_db()` direct-create path — Optuna manages its own
  schema; H1 zero exposure by construction).
- Phase-1.5 / Phase-2 migrations (this audit re-runs after they
  land; the rules carry forward).
- The Phase-1.5 / Phase-2 plans themselves (separate plans;
  this audit's role is verification, not authorship).
