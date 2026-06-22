# Plan — Migration 017_advisor_observations.sql

**Feature:** Phase-1 append-only advisor observation log; in Phase 1 holds the
**computed** Overfitting-Conscience verdict (one row per autotune run).

**Phase:** Phase 1 (HARDEN floor).

**Owner agent-type:** `sqlite-specialist`, `quant-test-writer`,
`quant-code-reviewer`.

## Source-of-truth references

- `docs/handoff/council-converged-migration-plan.md` §3.1 row 019, §6 H5
  (reversibility taxonomy — new-table migrations are abandon-by-drop only).
- `docs/handoff/decision-science-council-synthesis.md` §2.6 (the AI Advisor
  spine), §3.6 (AI Advisor — 2 of 4 roles active in Phase 1).
- Precedent: `database.py:113-149` `llm_suggestions` (the append-only,
  immutable, no-update accessor surface).
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §A.4 (M2 is
  operator instrumentation, not a Phase-2 stepping-stone — Phase-1 Advisor
  reads M2's `cvar_diagnostics` independently).

## Why

The Overfitting Conscience runs from Phase 1 — recording, every autotune run,
the computed verdict (`D_spec`, `N_effective`, the Yekutieli `c(N)` factor
used, and the touched-frozen-eval flag). The same table later holds the
**LLM-authored** roles (Specification Critic, Divergence Explainer,
Narrator); only the LLM authorship is the open Phase-1-vs-Phase-2 question
(synthesis §7 open items). The **schema is Phase 1 unconditionally** — no
schema change is needed when the LLM authorship arrives.

This is the persistence half of the AI Advisor spine.

## Numbering

Council `019_advisor_observations.sql` → codebase `017_advisor_observations.sql`
(shift +1 against existing `015_shadow_history_position_epoch.sql` — see plan
`016-spec-bundles` for the full renumbering table).

## Deliverables

1. **`migrations/017_advisor_observations.sql`** — `CREATE TABLE IF NOT
   EXISTS` for `advisor_observations`:
   - `id                  INTEGER PRIMARY KEY AUTOINCREMENT`
   - `created_at          TEXT NOT NULL DEFAULT (datetime('now'))`
   - `advisor_role        TEXT NOT NULL`  (OVERFITTING_CONSCIENCE |
     SPEC_CRITIC | DIVERGENCE_EXPLAINER | NARRATOR — discriminator across
     Phase-1/Phase-2 roles, same table)
   - `subject_type        TEXT NOT NULL`  (e.g. `autotune_run`,
     `cvar_diagnostic`, `shadow_decision`)
   - `subject_id          TEXT NOT NULL`  (the FK-ish id into the subject
     table; soft FK, not enforced)
   - `verdict             TEXT`  (the computed verdict label, e.g.
     `D_spec=1,N_effective=N_optuna,no_violations`)
   - `raw_response        TEXT NOT NULL DEFAULT '{}'`  (JSON; `{}` for a
     computed row, populated for an LLM-authored row)
   - `is_advisory_only    INTEGER NOT NULL DEFAULT 1`  (hard-wired `1`; an
     advisor never moves money — the table's structural advisory-only
     declaration)
   - `spec_bundle_id      TEXT`  (soft FK to `spec_bundles.bundle_hash`; NULL
     when not applicable)
2. **`database.py` accessor surface** — modelled on `llm_suggestions`
   (`database.py:670-715`):
   - `insert_advisor_observation(...)` — append-only.
   - `get_advisor_observations_for_subject(subject_type, subject_id)` — read.
   - `get_advisor_observations_for_role(advisor_role, limit=N)` — read.
   - **NO** `update_*` accessor. **NO** `delete_*` accessor. Immutability is
     enforced by the missing surface — the same pattern that protects
     `llm_suggestions` (council plan §3.1 row 019 cites this precedent).
3. **`_MIGRATION_FILES`** — append `"017_advisor_observations.sql"`.
4. **Fixture refresh** — seed at least two rows: one
   `OVERFITTING_CONSCIENCE` computed row (with a non-empty verdict and
   `raw_response='{}'`), one placeholder for an LLM-authored role with a
   populated `raw_response` — so test fixtures cover both row shapes.
5. **Schema-validator test** — fixture DB has the table and all columns.

## Dependencies

- **Soft-dependent on `016_spec_bundles.sql`** — `spec_bundle_id` is a soft FK
  to `spec_bundles.bundle_hash`. `016` must apply before `017` in
  `_MIGRATION_FILES`; the append-only ordering naturally gives this.

## Golden-fixture tests required (RED before GREEN)

1. **Append-only — no update accessor exists.** The implementer file
   (`database.py`) has no `update_advisor_observation` symbol; a `grep` test
   in CI is sufficient.
2. **`is_advisory_only` defaults to 1 and cannot be set to 0** at the
   accessor level — the writer ignores any caller-provided value (or rejects
   it). A test attempting to insert `is_advisory_only=0` either gets a 1
   stored or an exception; either is acceptable; "0 stored" is the failing
   case the test rejects.
3. **Computed-row shape** — `raw_response = '{}'` is a legal computed-role
   row; the read accessor parses it as an empty dict without error.
4. **LLM-authored-row shape** — a `raw_response` with a JSON payload
   round-trips through the read accessor as a dict.
5. **Role discrimination** — `get_advisor_observations_for_role` returns only
   rows of the requested role.
6. **Schema-validator test** — fixture DB carries the table.

## Definition of Done

- Migration applies cleanly; fixture DBs rebuilt.
- All six tests pass GREEN.
- `pytest tests/` full tree passes.
- The Advisor read code paths use `get_ro_connection()` (`database.py:56-60`)
  — read-only at the driver level; charter Operating Rule 3.
- The accessor surface has zero update/delete entry points.

## Risk callouts

- **A retrofitted ledger cannot reconstruct a pre-existence freeze decision**
  (council §3.1 row 020 — applies analogously here): the Overfitting
  Conscience must start counting from a clean Phase-1 freeze. Migration
  applies on a fresh DB before the first autotune run that freezes `gamma`.
- **One table for all 4 roles** — the LLM-authored roles' Phase-2 arrival
  does **not** justify a second table. `advisor_role` is the discriminator;
  `raw_response` is the JSON variant. A second table would split the
  Overfitting Conscience's reads across two surfaces.
- **`is_advisory_only` is structural belt-and-braces.** The hard-wired `1`
  declares the table's intent at the schema level so a future schema-grep
  reviewer sees the table is non-actionable; advisor read-only-ness is
  enforced primarily by the missing update accessor + the dashboard `mode=ro`
  connection, not by this column.

## Out of scope

- The LLM authorship of Specification Critic / Divergence Explainer /
  Narrator rows (Phase-2 open item per synthesis §7).
- The Advisor read-only query helper (`advisor_ro_query`) — that lives in the
  fold-role wall plan (`019_fold_role_columns`).
- M2 cvar_diagnostics persistence (separate plan `021_cvar_diagnostics`).
