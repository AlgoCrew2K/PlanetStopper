# Plan — Phase-1.5 schema deltas for M3 / S-1

**Feature:** The persistence-architect schema decisions for the Phase-1.5
M3 re-derivation and the S-1 two-stage parity gate. Two decisions, both
load-bearing: (1) the S-1 per-cycle attribution table is a **fixture
artifact, not a SQL table**; (2) M3 ships with **zero new schema** —
re-derivation does not need new audit columns on `autotune_runs` because
the curve change is captured by the spec-bundle + DoF-ledger spine already
in Phase 1.

**Phase:** Phase 1.5 (fast-follow on Phase 1; ships with M3 under S-1).

**Owner agent-type:** `sqlite-specialist`, `quant-code-reviewer`,
cross-reviewed by `quant-test-writer` (since the test fixtures are part
of the deliverable).

## Source-of-truth references

- `docs/handoff/decision-science-council-synthesis.md` §3.1 (M3 in
  Phase 1.5), §4 S-1 binding condition (the attribution table is "a
  **committed per-cycle attribution table**, each divergence in the
  intended direction" — committed artifact, not runtime state).
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §A.5
  (H-5 — the Phase-1 floor removes R3 only; R1/R2 removal needs M3
  under S-1).
- Sibling plan: `feature-plans/decision-science/phase-1.5/
  s1-two-stage-parity-gate/plan.md` (drafted by quant-test-writer;
  this plan resolves the persistence question that plan defers to
  persistence-architect).
- Sibling plan: `feature-plans/decision-science/phase-1.5/
  m3-redrive-provenance-gaps/plan.md`.
- Codebase: `database.py:96-111` (`autotune_runs` `CREATE TABLE`);
  `tests/fixtures/` (the codebase's existing fixture pattern).

## Why — decision (1): attribution table as fixture, not DB table

The S-1 attribution table is by design a **committed checked-in
artifact** that a reviewer reads in a PR diff. Persisting it as a
SQLite table would:

- Make the attribution invisible to PR review (a SQLite blob diff is
  unreadable).
- Require a new migration + a new fixture for a one-shot M3
  delivery.
- Couple the attribution to the runtime DB — but the attribution is
  authored from the **M3 design doc**, not from runtime data; storing
  authored content in a runtime DB confuses the provenance source.
- Re-expose the K-1 "explained divergence as prose" failure if the
  attribution row format ever drifted out of sync with the analysis
  scripts.

The correct home is `tests/fixtures/s1-stage1-reference/...` (the
pre-M3 frozen reference) and `tests/fixtures/s1-stage2-attribution.csv`
(the per-cycle attribution). Both are reviewed-in-diff text artifacts.
The S-1 plan §10 says exactly this; this plan **ratifies that decision
from the persistence lens** so a future reviewer does not propose a
DB-table re-home.

## Why — decision (2): M3 needs no new audit columns on `autotune_runs`

M3 re-derives two curves (time-squeeze decay; VWAP System-A HWM gate).
The natural question is "where does the audit of the curve change
land?" Three candidates:

1. **A new `autotune_runs` column** (e.g., `m3_curve_revision_id`)
   recording which curve revision an autotune run used. This is the
   H1-class dual-write hazard repeated, for negligible audit value.
2. **A new `researcher_dof_ledger` row** (Phase-1 table — plan
   `018-researcher-dof-ledger`) recording the curve re-derivation as a
   `STYLIZED_FACT` or `CALIBRATION` evidence-source row with
   `decision_type='REVISED'`. **No schema change** — the ledger schema
   already supports this; the new row is data, not schema.
3. **A new `spec_facets` row** (Phase-1 table — plan
   `016-spec-bundles`) recording the curve choice as a frozen facet
   under the bundle in force. **No schema change** — the facets table
   already supports this.

**The binding answer is (2)+(3): no new schema; new rows in
`researcher_dof_ledger` and `spec_facets`.** M3's audit trail rides the
spine the Phase-1 floor built precisely so a re-derivation gets the
defensibility upgrade without an ALTER. Candidate (1) is **explicitly
rejected** as an H1-class temptation.

## Deliverables

1. **A short documentation block in `migrations/README.md`** stating:
   - Phase 1.5 ships **zero new migrations**.
   - M3's audit trail lives in new rows in `researcher_dof_ledger` and
     `spec_facets`, not in a new schema object.
   - The S-1 attribution table is `tests/fixtures/s1-stage2-attribution.csv`,
     not a DB table.
2. **A `tests/fixtures/s1-stage1-reference/` directory provisioning
   note** in the S-1 sibling plan's tree — the persistence-architect
   does not author the reference contents (that is quant-test-writer's
   harness output captured), but does declare the path as the canonical
   home for the parity baseline.
3. **A `tests/fixtures/s1-stage2-attribution.csv` schema note** — the
   columns the sibling S-1 plan enumerates:
   `cycle_id, symphony_id, field_changed, old_value, new_value,
   attributed_curve, intended_direction, direction_check_passed`. The
   persistence-architect's contribution is asserting that **this CSV
   shape is the canonical attribution shape** so a future reviewer
   reads one shape across all M3 revisions; the CSV reader/writer in
   tests must use a header-driven parser (not positional) to keep the
   shape forward-compatible.
4. **Two new Phase-1.5 ledger rows (data, not schema)** — captured by
   the M3 implementer in the same PR that ships M3:
   - `researcher_dof_ledger` rows for the two re-derived curves, each
     `decision_type='REVISED'`, `evidence_source='STYLIZED_FACT'` (or
     `'CALIBRATION'` if the re-derivation is calibration-driven), and
     `n_configs_searched=1` (the re-derivation is a single chosen
     curve, not a P&L tour).
   - `spec_facets` rows under the bundle in force, recording the
     re-derived curve as a frozen facet.
5. **A fixture refresh** for any Phase-1 fixture DB that needs to
   reflect the M3-era rows (the existing `018-researcher-dof-ledger`
   and `016-spec-bundles` fixtures grow rows; no schema change).

## Dependencies

- **Hard-depends on Phase-1 floor (all six migrations) being in
  place** — M3 cannot ship before the spine that records it.
- **Hard-depends on the S-1 sibling plan** (the attribution table's
  CSV shape).
- **Hard-depends on the M3 sibling plan** (the curve re-derivations
  themselves).

## Golden-fixture tests required (RED before GREEN)

1. **No new migration file in 1.5** — a CI check counts the
   `_MIGRATION_FILES` list before and after the Phase-1.5 PR; the
   count is unchanged. (Structural guard against the H1-class
   temptation.)
2. **`researcher_dof_ledger` has two new rows after M3** — the
   fixture refresh test asserts row count grows by exactly two, one
   per re-derived curve, with `decision_type='REVISED'`.
3. **`spec_facets` has two new rows after M3** — the fixture refresh
   test asserts the two new facet rows are present and linked to the
   bundle in force.
4. **CSV schema is header-driven** — the test that reads
   `s1-stage2-attribution.csv` uses `csv.DictReader`, not positional
   indexing; a column-reorder in the CSV does not break the harness.
5. **Attribution CSV columns are the canonical eight** — a schema
   test reads the CSV header and asserts the column set equals
   `{cycle_id, symphony_id, field_changed, old_value, new_value,
   attributed_curve, intended_direction, direction_check_passed}`.

## Definition of Done

- The `migrations/README.md` note is in place.
- The S-1 attribution CSV column set is documented in this plan and
  in the sibling S-1 plan.
- All five tests pass GREEN.
- No new `.sql` migration files added under `migrations/` for Phase
  1.5.
- The M3 PR carries the two `researcher_dof_ledger` rows + the two
  `spec_facets` rows as data deltas in the fixture refresh.

## Risk callouts

- **The biggest risk is a "let's just add an audit column" PR.** This
  plan exists primarily to prevent that PR. The §1 CI count check is
  the structural enforcement.
- **The attribution CSV is hand-curated content** — its
  `intended_direction` column is authored from the M3 design doc,
  **not** machine-derived from the post-M3 output. This is the K-1
  guard that prevents "test the implementation against itself"
  circularity. Test §4 + the sibling plan's RED test enforce.
- **`STYLIZED_FACT` vs `CALIBRATION` for the curve re-derivation
  evidence-source.** Either is acceptable; the M3 implementer chooses
  based on the curve's re-derivation discipline. Both are
  **not-`BACKTEST_SELECTION`** (which would feed `S` and bite the
  haircut). A re-derivation that quietly tours curve options on P&L
  is an NN1 violation and must be tagged `BACKTEST_SELECTION`; the
  audit trail makes the violation visible.
- **No fixture-DB schema change means no `init_db()` dual-write
  hazard for Phase 1.5.** This is the entire point of routing M3's
  audit through Phase-1's existing spine.

## Out of scope

- The M3 curve re-derivations themselves (sibling plan
  `m3-redrive-provenance-gaps`).
- The S-1 parity harness (sibling plan `s1-two-stage-parity-gate`).
- The pre-M3 reference fixture's content (captured from production
  pre-M3 code; `quant-test-writer` domain).
- Phase-2 schema deltas (separate plans).
