# Plan — Engine-audit: full schema audit (additive-first; two-DB boundary; param-query; WAL; RO-mode; fixture hygiene)

**Feature:** A post-Phase-1, post-changes engine-methodology audit lane
covering the persistence-layer-touching surfaces. Six sub-audits, each a
distinct verification deliverable. No code changes ship from this plan
**unless** the audits surface defects — in which case the defects get
their own TDD-cycle-sized remediation plans.

**Phase:** Engine-audit (post-Phase-1; runs in parallel to Phase 1.5).

**Owner agent-type:** `sqlite-specialist` (audit author), `quant-code-
reviewer` (review), `quant-test-writer` (RED for any uncovered defect).

## Source-of-truth references

- Project CLAUDE.md (the `sqlite-specialist` charter in this agent
  message — additive-first, parameterized queries, WAL, `?mode=ro`,
  fixture refresh).
- Project CLAUDE.md architecture constraints — 1 (no blocking I/O), 2
  (dashboard read-only), 3 (two-DB), 4 (is_live explicit), 5
  (templates open SQLite read-only).
- `docs/handoff/decision-science-council-synthesis.md` §3.7 hazards H1,
  H3, H4 — the Phase-1 implementation hazards this audit verifies are
  closed.
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §A.8
  (the three drafting-defect classes the audit watches for).
- Codebase: `database.py` (entire surface), `migrations/*.sql`, all
  consumer call sites.

## Why

The persistence-architect charter says "the two SQLite databases are
the single source of truth for AlphaBot state and history." Phase 1
adds six migrations, two helpers, and a wall. After all six ship, an
independent audit verifies the charter rules are still upheld — the
audit is the *charter's* enforcement, not Phase 1's. A drift caught
here is cheaper than a drift caught in production.

## Sub-audits — each a Definition-of-Done bullet, each verifiable

1. **Additive-first compliance.** Every migration `001`–`021` is
   verified `CREATE TABLE IF NOT EXISTS` (idempotent) or `ALTER TABLE
   ADD COLUMN` with `DEFAULT` (or a `CREATE TABLE` with `DEFAULT`
   columns). Acceptance: a grep over `migrations/*.sql` returns zero
   `DROP TABLE`, zero `DROP COLUMN`, zero `ALTER TABLE ADD COLUMN`
   without a `DEFAULT` clause on a NOT-NULL column.
2. **Two-DB boundary.** No SQL in `database.py` or
   `dashboard*.py`/`app.py`/`autotuner.py`/`alpha_bot_execution.py`
   references both `alphabot_state.db` and `optuna_studies.db` in the
   same query. Acceptance: a grep finds zero `ATTACH DATABASE`
   statements and zero string literals that name both DBs in the same
   function body.
3. **Parameterized queries.** Every SQL string in `database.py` and
   consumer modules uses `?` placeholders for any caller-supplied
   value. Acceptance: a grep rejects f-string SQL construction
   (`f"... WHERE x = {value}"` pattern); the audit enumerates each
   `cursor.execute` call and confirms placeholder usage.
4. **WAL mode confirmation.** `PRAGMA journal_mode=WAL` is asserted in
   `init_db()` (`database.py:71`) and a runtime test confirms `wal` is
   the active mode. Acceptance: the test from plan
   `phase-1/shadow-logging-pattern` §3 passes; the audit asserts no
   later code path mutates `journal_mode`.
5. **Read-only `?mode=ro` usage.** Every dashboard read handler and
   every Advisor read uses `get_ro_connection()` (URI form with
   `?mode=ro`). Acceptance: a grep finds zero direct `get_connection()`
   imports in `app.py`'s read-only routes and in the Advisor module;
   every read accessor is `get_ro_connection`-routed.
6. **Fixture-DB hygiene.** Every migration `001`–`021` is reflected in
   the fixture DBs; the schema-validator test passes (every plan's §N
   "schema-validator test" deliverable). Acceptance: running
   `run_migrations()` against a fresh fixture DB yields the same
   `schema_migrations` row count as a fixture rebuilt from scratch;
   no fixture-DB schema/code drift.

## Deliverables

1. **`tests/audit/test_full_schema_audit.py`** — one test class per
   sub-audit; the audit runs in CI on every PR that touches
   `database.py` or `migrations/`. Failures are advisory in the
   short term (the audit publishes a report); they become blocking
   after the engine-audit lane is signed off.
2. **`docs/handoff/engine-audit-results-<date>.md`** — the captured
   audit report (a one-shot output of the audit run; not a recurring
   artifact). Authored by the audit lane on first execution; updated
   on subsequent runs only if a defect surfaces.
3. **A defect-triage list** in the audit report — any sub-audit
   FAIL becomes a TDD-cycle-sized remediation plan elsewhere; the
   audit does not write the fix itself.
4. **No code changes in `database.py` or `migrations/`** from this
   plan directly. The audit reads; it does not write.

## Dependencies

- **Hard-depends on Phase-1 floor being applied** (all six
  migrations + the H4 helper + the wall).
- **Loose-couples to Phase-1.5** (M3 audit columns: this plan
  declares "the audit re-runs after M3 lands").

## Golden-fixture tests required (RED before GREEN)

The sub-audits **are** the tests. Each FAIL in §1–§6 is a binding
defect surfaced; the audit's test functions are the verifiability
spec.

1. **Additive-first grep** — `tests/audit/test_no_destructive_sql.py`.
2. **Two-DB-boundary grep** — `tests/audit/test_no_cross_db_attach.py`.
3. **Parameterized-query grep** —
   `tests/audit/test_no_f_string_sql.py`.
4. **WAL-mode runtime check** —
   `tests/audit/test_journal_mode_wal.py`.
5. **`?mode=ro` enforcement** —
   `tests/audit/test_advisor_dashboard_ro.py`.
6. **Fixture/migration parity** —
   `tests/audit/test_fixture_schema_parity.py`.

## Definition of Done

- All six audit tests exist and pass GREEN on the post-Phase-1
  codebase.
- The audit report is committed under `docs/handoff/`.
- Any defect surfaced has a corresponding TDD-cycle plan filed.
- The CI integration is wired (audit tests run on every PR touching
  `database.py` or `migrations/`).

## Risk callouts

- **Audit drift.** An audit that becomes "the team writes a test then
  routes around it" is worse than no audit. The audit tests are
  intentionally simple greps + runtime asserts; their value is
  *catching* the route-around at the next reviewer's PR diff. The
  audit's `docs/handoff/` report is the human-readable companion the
  reviewer reads, not a generated dump.
- **False positives in greps** — e.g., a legitimate `DROP TABLE` in
  a `DROP TABLE IF EXISTS` for a test fixture's teardown. The audit
  grep scopes to `migrations/*.sql` only; test code is allowed any
  SQL because tests are not production schema changes.
- **The audit is a one-shot lane**, not a recurring deliverable. The
  CI integration prevents the recurring-cost ambiguity; the
  one-shot report is the human deliverable.
- **The Phase-1 implementation hazards H1/H3/H4 (council §3.7) are
  covered by sub-audits §1, §4, §5 respectively** — the audit closes
  the explicit hazards the synthesis flagged. A FAIL on any of
  §1/§4/§5 reopens the corresponding council hazard.

## Out of scope

- Replay-determinism anchor coverage (separate audit plan,
  `engine-audit/replay-determinism-coverage`).
- Backup/restore strategy (separate audit plan,
  `engine-audit/backup-restore-strategy`).
- Migration-replay vs init_db divergence (separate audit plan,
  `engine-audit/h1-divergence-audit`).
- Any defect remediation (each FAIL gets its own TDD plan).
