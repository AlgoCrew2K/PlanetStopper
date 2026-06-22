# Plan — Migration 025_shadow_decisions.sql (DEFERRED — Phase 2)

**Feature:** Phase-2 deferred — paired legacy+new decision per cycle. The
runtime-state heavy table that makes the CVaR co-signal's effect
auditable cycle-by-cycle. **Ships only if the Phase-2 entry gates pass.**

**Phase:** Phase 2 (Finalist B; evidence-gated).

**Owner agent-type:** `sqlite-specialist`, `quant-test-writer`,
`quant-code-reviewer`. Writer: `alpha_bot_execution.py`; reader:
dashboard + Advisor.

## Source-of-truth references

- `docs/handoff/decision-science-council-synthesis.md` §5 (Finalist B
  design), §5.4 (Phase-2 persistence — `016_shadow_decisions.sql`
  council-numbered), §5.7 (Phase-2 entry gates — gate fails → stop).
- `docs/handoff/council-converged-migration-plan.md` §3.2 row 016, §4
  (entry gates), §6 H4 (telemetry helper), H6 (legacy retention), H7
  (`run_monte_carlo` blast radius).
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §B
  (REJECT of the CVaR-divergence trigger — confirms the shadow
  decision's CVaR fields stay co-signal-shaped, not detector-shaped).

## Why

A live CVaR co-signal that ever fires must be **paired** every cycle
against the legacy heuristic's decision, so the operator can read,
cycle by cycle:

- did the new engine agree with the legacy engine?
- when they disagreed, did the divergence carry better outcomes?

That is the **only** valid evidence base for the eventual legacy
retirement decision (council H6 — human-operator-authorized only).
Without per-cycle pairing the operator has nothing to sign off on.

## Numbering — REVISED FROM COUNCIL DOC

Council `016_shadow_decisions.sql` → codebase `025_shadow_decisions.sql`
(Phase-1 migrations shifted from `015-023` to `016-021` because `015` is
taken; Phase-2 migrations follow at `025-027` leaving a numeric gap for
any Phase-1.5 or other interim migrations — see the persistence-
architect's full renumbering table in plan `016-spec-bundles`).

## Deliverables

1. **`migrations/025_shadow_decisions.sql`** — `CREATE TABLE IF NOT
   EXISTS shadow_decisions` with ~25 columns. Skeleton:
   - `id                       INTEGER PRIMARY KEY AUTOINCREMENT`
   - `created_at               TEXT NOT NULL DEFAULT (datetime('now'))`
   - `cycle_id                 TEXT NOT NULL`
   - `symphony_id              TEXT NOT NULL`
   - `account_id               TEXT`  (multi-account discriminator;
     `port_state` precedent)
   - `legacy_action            TEXT NOT NULL`  (HOLD | EXIT)
   - `legacy_reason            TEXT`
   - `shadow_action            TEXT NOT NULL`  (HOLD | EXIT)
   - `shadow_reason            TEXT`
   - `decisions_agree          INTEGER NOT NULL`  (denormalised +
     indexed; council §3.2 row 016)
   - `cvar_estimate            REAL`
   - `cvar_std_error           REAL`
   - `cvar_breach              INTEGER`
   - `cvar_n_tail              INTEGER`
   - `eu_hold                  REAL`  (CRRA-EU of holding through the
     next cycle — co-signal input)
   - `eu_exit                  REAL`
   - `eu_margin                REAL`
   - `spec_bundle_id           TEXT NOT NULL`  (a shadow decision
     without provenance is unreplayable — council §5.4 binding)
   - `mc_seed                  TEXT NOT NULL`  (the SHA-256-derived
     seed used; council §5.4 binding)
   - `generator_calib_id       TEXT`  (FK to `026_path_generator`
     calibration row in force)
   - `hysteresis_snapshot_json TEXT`  (the hysteresis state at decision
     time — load-bearing replay anchor; council §3.7 anchor count = 5)
   - `path_bank_manifest_id    TEXT`  (FK to `026_path_generator`
     manifest row in force)
2. **Indexes** (in the migration, each commented):
   - `CREATE INDEX IF NOT EXISTS idx_shadow_dec_cycle ON
     shadow_decisions(cycle_id);`  -- accelerates per-cycle replay
     parity assertion
   - `CREATE INDEX IF NOT EXISTS idx_shadow_dec_agree ON
     shadow_decisions(decisions_agree, created_at);`  -- accelerates
     the dashboard divergence-rate rollup
   - `CREATE INDEX IF NOT EXISTS idx_shadow_dec_symphony_ts ON
     shadow_decisions(symphony_id, created_at DESC);`  -- accelerates
     the per-symphony latest-decision read
3. **`_MIGRATION_FILES`** — append `"025_shadow_decisions.sql"`.
4. **No `init_db()` mirror.** New table; H1 zero-exposure (council §6
  H2).
5. **Fixture refresh** — seed rows that cover:
   - `decisions_agree=1` (the common case).
   - `decisions_agree=0` with `legacy_action=HOLD, shadow_action=EXIT`
     (shadow more cautious).
   - `decisions_agree=0` with `legacy_action=EXIT, shadow_action=HOLD`
     (shadow more permissive — the dangerous direction the rubric
     specifically watches; J-3 inverted-shadow window).
   - At least one row with all five replay anchors populated
     (`spec_bundle_id`, `mc_seed`, `generator_calib_id`,
     `hysteresis_snapshot_json`, `path_bank_manifest_id` — the council
     §3.7 "Phase 2 = 5 anchors" count).

## Dependencies

- **Hard-depends on:** all Phase-1 migrations + `026_path_generator.sql`
  (generator/manifest FKs) + `027_decision_core_state.sql`
  (hysteresis state source).
- **Phase-2 entry gates must pass** (council §5.7) before this
  migration ships.

## Golden-fixture tests required (RED before GREEN)

1. **`spec_bundle_id NOT NULL` enforced at write time** — an attempted
   insert with `spec_bundle_id IS NULL` raises `sqlite3.IntegrityError`.
   This is **safe additive** because the table is brand-new
   (council §5.4 explicit: "a NOT NULL on a brand-new CREATE TABLE (no
   pre-existing rows) is fully additive-compliant").
2. **`mc_seed NOT NULL` enforced at write time** — same shape.
3. **All five replay anchors present** in the fixture's anchor-complete
   row.
4. **`decisions_agree` denormalisation is consistent** — a write where
   `legacy_action != shadow_action` and `decisions_agree=1` is a defect;
   the writer (in `alpha_bot_execution.py`) computes
   `decisions_agree` from the two action columns at write time, never
   from a caller-supplied value. A property test asserts
   `decisions_agree == (legacy_action == shadow_action)` for every
   fixture row.
5. **Index usage** — `EXPLAIN QUERY PLAN` for the divergence-rate
   rollup uses `idx_shadow_dec_agree`.
6. **H4 telemetry helper routing** — the writer uses
   `record_telemetry(table="shadow_decisions", ...,
   mode="live"|"replay", cycle_id=...)` (plan
   `phase-1/h4-telemetry-helper`). A lint grep enforces.
7. **Schema-validator test** — fixture DB has the table + all three
   indexes + all ~25 columns.

## Definition of Done

- Migration applies cleanly; fixture DBs rebuilt; all four shape
  cases present.
- All seven tests pass GREEN.
- `pytest tests/` full tree passes.
- Writer routes through the H4 helper; dashboard reads via
  `get_ro_connection()`.
- The five Phase-2 replay anchors are all queryable on a fixture row.

## Risk callouts

- **`spec_bundle_id` and `mc_seed` are `NOT NULL`** — council §5.4
  binding. A shadow decision without provenance is unreplayable; an
  unreplayable shadow row is worse than no row at all (it manufactures
  the appearance of a verified-by-replay decision). The `NOT NULL`
  constraint is the structural enforcement; the writer fails loud at
  insert if either is missing. This is **safe additive** on a brand-new
  table.
- **`shadow_inputs` (a separate optional table)** — the council §3.2
  row 016 notes "`shadow_inputs` is needed only for a non-pre-sim
  generator." Under the recommended pre-sim-bank architecture (Tier-1
  pre-open batch), inputs are reproducible from `(cycle_id,
  symphony_id, spec_bundle_id, generator_calib_id, mc_seed)` — no
  separate inputs table needed. This plan does **not** ship
  `shadow_inputs`; if the Phase-2 generator-family decision shifts to a
  deferred-compute (Shape-B) generator, a sibling migration adds it.
- **`hysteresis_snapshot_json` is JSON.** A JSON column is the
  codebase's existing precedent for transient state snapshots (`bot_state`,
  `chart_history` data columns); the snapshot's structure is owned by
  plan `027-decision-core-state`.
- **The legacy + shadow pairing is the J-3 inverted-shadow window's
  evidence.** A `decisions_agree=0, legacy_action=EXIT,
  shadow_action=HOLD` row is the dangerous direction the operator must
  read before signing the legacy drop (council H6). The dashboard must
  surface this row class prominently — the
  `flask-dashboard-specialist` plan owns the display.
- **`run_monte_carlo` STAYS FROZEN.** Council §6 H7 binding: this
  table's `cvar_estimate` column reads from the **net-new**
  `simulate_forward_paths` (plan `phase-2/026-path-generator`), not
  from a mutated `run_monte_carlo`. The 7+ legacy consumers of
  `run_monte_carlo` keep reading the legacy scalar unchanged. M2's
  `cvar_5pct` in `cvar_diagnostics` (Phase 1) is a separate column on
  a separate table from the Phase-2 `cvar_estimate` in
  `shadow_decisions` — they coexist; they do not share a writer.

## Out of scope

- The CVaR co-signal logic (`alpha_bot_execution.py` writer; risk-
  engine-specialist).
- The Phase-2 forward-path generator itself (`math_engine.py` /
  `simulate_forward_paths`; plan `phase-2/026-path-generator`).
- The hysteresis state machine (plan `phase-2/027-decision-core-state`).
- The legacy-drop ceremony (plan `phase-2/027-decision-core-state`
  retention section + H6 human-authorized release).
- All Phase-1 schema (separate plans).
