# Plan — Migration 016_spec_bundles.sql (spec_bundles + spec_facets)

**Feature:** Phase-1 immutable hashed frozen-facet bundle registry — the
provenance spine the Overfitting Conscience reads.

**Phase:** Phase 1 (HARDEN floor).

**Owner agent-type:** `sqlite-specialist` (implementer), `quant-test-writer`
(adversarial RED), `quant-code-reviewer` (review).

## Source-of-truth references

- `docs/handoff/decision-science-council-synthesis.md` §2.5 (NN1 — the
  spec-freeze hard gate), §3.7 (persistence — Phase-1 footprint as a first-class
  cost), §3.7 sizing paragraph ("Phase-1 apparatus sizing — bounded team's-choice").
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §A.8 A2 — the
  binding fix to state the table count **once**: `spec_bundles` mandatory,
  `spec_facets` recommended (team may collapse into `facets_json` on
  `spec_bundles`).
- `docs/handoff/council-converged-migration-plan.md` §3.1 row 015, §6 H1/H2/H5,
  §7 fixture-update obligation.
- Codebase: `database.py:743-756` (`_MIGRATION_FILES`), `database.py:759-807`
  (`run_migrations()` — append-only contract).

## Why

The BHY haircut's Yekutieli `c(N)` corrects multiple-testing **only over the
Optuna trial search it can see**. A spec facet chosen by looking at strategy
P&L is an uncounted testing event that silently miscalibrates the FDR gate.
NN1 (§2.5) is the rule; an immutable hashed bundle with a freeze timestamp is
the structural enforcement. A source-code named constant is explicitly **not**
an acceptable substitute (council §3.7 sizing paragraph, persistence-architect
binding constraint): no row-level provenance, no `frozen_at`, no hash.

This migration provisions the registry's home in the state DB.

## Numbering — REVISED FROM COUNCIL DOC

Council plan said `015_spec_bundles.sql`. **Migration 015 is already taken** by
`015_shadow_history_position_epoch.sql` (committed; in `_MIGRATION_FILES`). All
Phase-1 / Phase-2 migrations in the council numbering shift +1:

| Council numbering | Actual codebase numbering |
|---|---|
| `015_spec_bundles.sql` | `016_spec_bundles.sql` |
| `019_advisor_observations.sql` | `017_advisor_observations.sql` |
| `020_researcher_dof_ledger.sql` | `018_researcher_dof_ledger.sql` |
| `021_fold_role.sql` | `019_fold_role_columns.sql` |
| `022_autotune_runs_eut.sql` | `020_autotune_runs_eut.sql` |
| `023_cvar_diagnostics.sql` | `021_cvar_diagnostics.sql` |

(Phase-2 deferred files renumber to `025/026/027` — see those plans.) The
**phase-1 floor count of six migrations is preserved**; only the numeric
prefix shifts to avoid the live collision.

## Deliverables

1. **`migrations/016_spec_bundles.sql`** — `CREATE TABLE IF NOT EXISTS`
   statements for:
   - `spec_bundles` — the mandatory immutable hashed bundle.
     - `bundle_hash       TEXT PRIMARY KEY`  (content-hash of `facets_json`)
     - `frozen_at         TEXT NOT NULL DEFAULT (datetime('now'))`
     - `facets_json       TEXT NOT NULL` (canonical hashable blob)
     - `horizon_bars      INTEGER`
     - `cvar_alpha        REAL`
     - `generator_family  TEXT`
   - `spec_facets` — the recommended queryable projection (one row per facet):
     - `id                INTEGER PRIMARY KEY AUTOINCREMENT`
     - `bundle_hash       TEXT NOT NULL` (soft FK to `spec_bundles.bundle_hash`)
     - `facet_name        TEXT NOT NULL`
     - `facet_value       TEXT NOT NULL`  (JSON-serialised value)
     - `freeze_discipline TEXT NOT NULL`  (THEORY | MANDATE | STYLIZED_FACT |
       CALIBRATION | BACKTEST_SELECTION — the last is the NN1-violating value
       and is the one the haircut counts into `S`)
     - `justification     TEXT`
     - `calibration_evidence TEXT`
2. **`database.py` updates:**
   - Append `"016_spec_bundles.sql"` to `_MIGRATION_FILES` (the codebase's
     append-only contract, `database.py:743`).
   - **No** `init_db()` `CREATE TABLE` mirror needed — these are new tables, not
     a column-add. The `init_db()` dual-write rule (H1) only applies to
     `ALTER TABLE ... ADD COLUMN` migrations (see plan `020_autotune_runs_eut`).
3. **Fixture refresh** — `tests/fixtures/` builder runs `run_migrations()` so
   fixture DBs carry the new tables; seed rows for at least one bundle + its
   facets so consumers have something to read.
4. **Runtime schema-validator test** — a single pytest opens each fixture DB
   and asserts the expected tables/columns exist (the guard against silent
   schema/fixture drift, charter rule).

## Dependencies

- None in front of it within Phase 1 — this is the first new Phase-1 table.
- Consumers (Phase 1):
  - Migration `018_researcher_dof_ledger.sql` (soft-FK `spec_bundle_id`).
  - Migration `020_autotune_runs_eut.sql` (`spec_bundle_id` column on
    `autotune_runs`).

## Golden-fixture tests required (RED before GREEN)

1. **Bundle immutability** — `INSERT` a row, then any subsequent `UPDATE` on
   `frozen_at`, `facets_json`, `horizon_bars`, `cvar_alpha`, or
   `generator_family` for the same `bundle_hash` is a defect; enforced at
   application code level (no SQL trigger — the codebase's `llm_suggestions`
   precedent is application-code-immutable, see `database.py:670-715`'s
   accessor surface that exposes only inserts + reads).
2. **Hash uniqueness** — second `INSERT` of the same `bundle_hash` is a
   `sqlite3.IntegrityError` (PRIMARY KEY collision); the test asserts the
   exception.
3. **Frozen-at default fires on insert** — `frozen_at` is non-null after an
   insert that omits it.
4. **`spec_facets.bundle_hash` references a real bundle** — a soft-FK test: the
   accessor that walks from a facet row back to its bundle returns a non-empty
   row (foreign-key enforcement off by default in SQLite; this is the
   application-level invariant).
5. **Fixture refresh test** — fixture DB has `spec_bundles` + `spec_facets`
   tables with all columns; at least one bundle + one facet present.

## Definition of Done

- `migrations/016_spec_bundles.sql` exists, additive-first, idempotent
  (`CREATE TABLE IF NOT EXISTS`).
- `_MIGRATION_FILES` appended in order; nothing reordered or inserted mid-list.
- Fixture DBs rebuilt; the schema-validator test passes.
- All five RED tests pass GREEN.
- `pytest tests/` full tree passes; no other suite breaks on the new tables.
- Read-only `?mode=ro` connection in dashboard / advisor code paths sees the
  new tables (no special handling needed; `mode=ro` blocks writes, not reads).

## Risk callouts

- **Sizing latitude — bounded.** The team MAY collapse `spec_facets` into a
  `facets_json` JSON column on `spec_bundles` if 3 facets does not warrant a
  child table. The team MAY NOT drop the immutable hashed bundle, the
  `frozen_at` timestamp, or the content-hash discipline (persistence-architect
  binding constraint, council §3.7 sizing). The deliverables above are the
  *recommended* shape; the *binding* shape is "an immutable persisted record
  with a freeze timestamp, content-hashed."
- **Wall-breach tripwire is a SEPARATE plan** — see `019_fold_role_columns`. A
  Phase-1 freeze without the wall in place fails the NN1 audit.
- **No optimization-DB placement.** Council §3.7: zero optimization-DB
  migrations in Phase 1. The spec registry lives in the state DB so the BHY
  haircut and Advisor reads stay single-DB (no cross-join). This was a
  re-tiering during the debate (the original placement was wrong).
- **`bundle_hash` is the content hash** of the canonical-serialised
  `facets_json`. The hashing algorithm + canonicalisation pseudocode is owned
  by the **implementer**, not this migration plan. The hash MUST be
  reproducible at replay time (a re-hash of the same facets bytes yields the
  same key); a non-reproducible hash fails Gate-1 parity.

## Out of scope

- The hashing implementation itself (lives in `database.py` / a small helper).
- The Advisor read accessor (`advisor_ro_query` — separate plan
  `019_fold_role_columns`).
- The `N_effective` arithmetic consumer (lives in `autotuner.py`; no schema
  impact, council §5).
- Phase-2 `shadow_decisions.spec_bundle_id NOT NULL` reference (deferred plan
  `025_shadow_decisions`).
