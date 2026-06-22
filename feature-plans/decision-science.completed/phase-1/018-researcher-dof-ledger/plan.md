# Plan — Migration 018_researcher_dof_ledger.sql

**Feature:** Phase-1 append-only researcher degrees-of-freedom ledger — the
auditable input that feeds the additive `N_effective = N_optuna + S` haircut.

**Phase:** Phase 1 (HARDEN floor).

**Owner agent-type:** `sqlite-specialist`, `quant-test-writer`,
`quant-code-reviewer`. Consumer code lives in `autotuner.py`
(`risk-engine-specialist`).

## Source-of-truth references

- `docs/handoff/decision-science-council-synthesis.md` §2.2 (overfitting-
  accounting correction — additive `N_effective`), §2.5 (NN1 verbatim + the
  tripwire framing), §3.6 (Overfitting Conscience active in Phase 1).
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §A.0 Defect 2
  ("a P&L-toured spec that received its own sub-sweep contributes its **full
  sub-sweep count** to `S`") — schema must support this.
- `docs/handoff/council-converged-migration-plan.md` §3.1 row 020, §5 (consumer
  arithmetic, zero schema impact of multiplicative→additive correction).
- Codebase: `autotuner.py:266-271` (the H-6 category-error comment),
  `autotuner.py:344-345` (Yekutieli `c(N) = Σ 1/j`).

## Why

The honest multiple-testing count for the BHY haircut is additive:
`N_effective = N_optuna + S`, where `S` counts the configurations evaluated on
a strategy P&L / strategy-return basis **beyond** the single Optuna sweep
actually run. In the honest NN1-compliant case `S = 0` and the haircut is
**byte-identical to today's** (council §2.2 property 1). The ledger is a
**tripwire**, not a routine penalty — it bites only when someone P&L-toured a
facet (council §2.5).

A retrofitted ledger cannot reconstruct a pre-existence freeze decision; the
Overfitting Conscience must count from a clean Phase-1 start (council §3.1
row 020). This is binding.

## Numbering

Council `020_researcher_dof_ledger.sql` → codebase
`018_researcher_dof_ledger.sql` (shift +1; see plan `016-spec-bundles` for
table).

## Deliverables

1. **`migrations/018_researcher_dof_ledger.sql`** — `CREATE TABLE IF NOT
   EXISTS researcher_dof_ledger`:
   - `id                  INTEGER PRIMARY KEY AUTOINCREMENT`
   - `created_at          TEXT NOT NULL DEFAULT (datetime('now'))`
   - `facet_name          TEXT NOT NULL`
   - `facet_category      TEXT NOT NULL`  (`specification` | `parameter`)
   - `decision_type       TEXT NOT NULL`  (`FIXED` | `SEARCHED` | `REVISED` |
     `OOS_PEEK`)
   - `evidence_source     TEXT NOT NULL`  (`THEORY` | `MANDATE` |
     `STYLIZED_FACT` | `CALIBRATION` | `BACKTEST_SELECTION` | `OOS`)
   - `n_configs_searched  INTEGER NOT NULL DEFAULT 1`
   - `touched_frozen_eval INTEGER NOT NULL DEFAULT 0`  (boolean — the
     wall-breach tripwire records here)
   - `spec_bundle_id      TEXT`  (soft FK to `spec_bundles.bundle_hash`)
   - `justification       TEXT`
2. **`_MIGRATION_FILES`** — append `"018_researcher_dof_ledger.sql"` after
   `017_advisor_observations.sql`.
3. **Fixture refresh** — seed rows for the three Phase-1 theory-frozen facets
   identified in the synthesis §3.3:
   - `gamma` — `THEORY` / `FIXED` / `n_configs_searched=1`
   - `utility_family` — `THEORY` (or `MANDATE`) / `FIXED` /
     `n_configs_searched=1`
   - `wealth_argument` — `CALIBRATION` (W-H2 derivation) / `FIXED` /
     `n_configs_searched=1`
   All three carry `evidence_source ≠ BACKTEST_SELECTION` (so `S = 0`).
4. **Append-only accessor surface** (modelled on `llm_suggestions`):
   - `insert_dof_ledger_row(...)` — append-only.
   - `get_dof_ledger_for_bundle(spec_bundle_id)` — read.
   - `count_dof_backtest_selections(spec_bundle_id)` — the `S` accumulator;
     returns `SUM(n_configs_searched) WHERE evidence_source =
     'BACKTEST_SELECTION'` (Defect 2: sub-sweep count, not bundle-distinct).
5. **Schema-validator test** — fixture DB has the table and all columns; the
   three seed rows are present.

## Dependencies

- **Soft-depends on `016_spec_bundles.sql`** — `spec_bundle_id` FK.
- **Consumer in Phase 1:** `autotuner.py` reads
  `count_dof_backtest_selections()` to compute `S` and writes
  `N_effective = N_optuna + S` into `autotune_runs.n_effective` (column added
  by migration `020_autotune_runs_eut.sql`).

## Golden-fixture tests required (RED before GREEN)

1. **The honest Phase-1 case yields `S = 0`** — fixture seeded with three
   `THEORY/MANDATE/CALIBRATION` rows; `count_dof_backtest_selections` returns
   `0`. Consumer-side test: `N_effective == N_optuna` byte-identical to
   today's haircut (the council's property-1 byte-identity assertion).
2. **A single P&L-toured row contributes its `n_configs_searched` to `S`** —
   insert a row with `evidence_source='BACKTEST_SELECTION'` and
   `n_configs_searched=4`; `S = 4`. (Defect 2 sub-sweep contribution.)
3. **`touched_frozen_eval=1` records the wall breach** — a row with
   `touched_frozen_eval=1` is queryable; the wall-breach tripwire reads this
   to fire (the actual tripwire logic lives in plan
   `019_fold_role_columns.sql`).
4. **Append-only — no update accessor** (grep test, as for
   `017_advisor_observations`).
5. **Schema-validator test** — fixture DB carries the table + the three seed
   rows.
6. **Conservative-upper-bound property** — `S` from
   `n_configs_searched`-sums is `>= S` from a bundle-distinct count when any
   bundle has `n_configs_searched > 1`. A documentation fixture asserts this
   so a future reviewer cannot quietly switch the accumulator to a
   bundle-distinct `COUNT(DISTINCT)`.

## Definition of Done

- Migration applies cleanly; fixture DBs rebuilt with the three Phase-1
  theory-frozen seed rows.
- All six tests pass GREEN.
- `pytest tests/` full tree passes.
- Read accessor uses `get_ro_connection()` where called from the Advisor or
  dashboard surface.
- Append-only invariant enforced by missing update/delete accessors.
- The `count_dof_backtest_selections` accessor returns the **`SUM` of
  `n_configs_searched`** (sub-sweep contribution), **not**
  `COUNT(DISTINCT spec_bundle_id)` — explicit RED test in §2 enforces this.

## Risk callouts

- **`evidence_source` is the NN1 enforcement column.** A facet frozen by
  `THEORY`, `MANDATE`, `STYLIZED_FACT`, `CALIBRATION` does not feed `S`. A
  facet frozen by `BACKTEST_SELECTION` does. This is the structural mapping
  of the rule in §2.5; mis-labelling here breaks the haircut's honesty.
- **`OOS_PEEK` is a separate badge** — a row with `decision_type='OOS_PEEK'`
  is a methodology defect; the Advisor flags it and the operator must
  re-freeze. The schema records the event; it does not block the write.
- **Bundle-distinct vs sum-of-sub-sweep is the council's biggest accounting
  judgement.** The §2.2 framing says "additive — sum over P&L-toured
  configurations." The v3 evaluation §A.0 Defect 2 explicitly states a
  sub-sweep contributes its **full sub-sweep count**, not a single `+1`. The
  schema supports either reading (one row with `n_configs_searched=K`
  collapses into either by the accumulator choice); the **binding consumer
  reading is the sub-sweep `SUM`** (deliberately conservative; can reject a
  genuine signal, never pass a spurious one — council §2.2 property 2).
- **`touched_frozen_eval` is the column the wall-breach tripwire writes to.**
  The wall and its tripwire logic are in plan `019_fold_role_columns`; this
  migration just provisions the column.

## Out of scope

- The `autotuner.py` consumer code that computes `N_effective` and writes it
  to `autotune_runs.n_effective` — separate plan (tuning-architect's domain;
  schema impact is the `n_effective` column on `autotune_runs`, plan
  `020_autotune_runs_eut`).
- The wall-breach tripwire itself (plan `019_fold_role_columns`).
- The `c(N) = Σ 1/j` Yekutieli arithmetic (unchanged at the autotuner level;
  council §2.2 "BHY step-up + Yekutieli c(N) machinery 100% preserved").
