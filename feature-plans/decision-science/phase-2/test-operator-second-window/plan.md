# Phase 2 — Operator-optional second-window M2 enrichment

## Feature
A structural-invariant test set for the **only surviving residue** of
the CVaR-divergence idea (per v3 evaluation §B.6). Asserts:
1. The operator-optional second-window columns `cvar_5pct_long` and
   `cvar_n_tail_long` may exist (additive NULLable);
2. **NO `cvar_divergence`, `regime_recency_weight`, or any
   signed-divergence persisted-displayable column** exists in schema
   or in templates — the binding constraint of §B.6.

## Phase
Phase 2 (residue from the rejected divergence idea — Phase-1.5 schema-
adjacent but materially Phase-2-scope per §B.6's "operator-optional"
nature).

> Note on phasing: §B.6 places these columns inside migration `023`
> (Phase 1). If the implementing team adopts the second-window residue
> in Phase 1, this plan moves to `phase-1/`. Pending that decision,
> the plan lives in `phase-2/` to avoid pre-committing.

## Owner agent-type
`quant-test-writer` (RED authoring). Implementation:
`persistence-architect` (schema), `flask-dashboard-specialist`
(display surface), `risk-engine-specialist` (the second-window M2 call).

## Source-of-truth references
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §B.6
  — "no new architecture, no new validation, no new finalist, and no
  signed-divergence quantity surfaced as a single derived value...
  cvar_5pct_long and cvar_n_tail_long as additive NULLable columns...
  It must NOT gain a cvar_divergence or regime_recency_weight
  persisted, displayable column — those manufacture the detector
  affordance the REJECT removes."
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md`
  §B.1–§B.5 — the divergence REJECT and the three converging routes.

## Why
The divergence idea was REJECTED but the user correctly identified one
real weakness — M2 is a single static CVaR with no temporal reference
frame. The operator-optional second window addresses it without
manufacturing the detector affordance the REJECT exists to remove. The
test enforces this structurally so a future developer cannot
re-introduce the detector by accident.

## Deliverables

### D1. Test file
`tests/database/test_phase2_operator_second_window.py`.

### D2. Test cases

**Scenario 1 — `test_cvar_5pct_long_column_is_additive_nullable`**
- Read the migration that adds the second-window columns.
- Assert `cvar_5pct_long` is `REAL DEFAULT NULL` (or equivalent), per
  E-1.
- Assert `cvar_n_tail_long` is `INTEGER DEFAULT NULL`.
- Discriminating-power: a `NOT NULL` constraint on either column
  would force fabricated values when the long pool is insufficient,
  exactly the failure §B.6 calls out.

**Scenario 2 — `test_each_window_displayed_independently_under_s3_contract`**
(the binding S-3 read-independently constraint).
- Render the diagnostic display surface for a cycle where both
  windows are populated.
- Assert two **independent** S-3 contract groups are rendered: one
  for the short window (cvar_5pct, cvar_5pct_stderr, cvar_n_tail,
  bias-warning) and one for the long window (cvar_5pct_long,
  cvar_5pct_stderr_long, cvar_n_tail_long, bias-warning).
- Assert NO derived value (e.g., the signed difference) appears on
  the surface.

**Scenario 3 — `test_no_cvar_divergence_or_recency_weight_column_in_schema`**
(load-bearing — §B.6 binding).
- Read `database.py` and every migration file under
  `database/migrations/`; assert no `CREATE TABLE` or `ALTER TABLE`
  introduces a column named:
  - `cvar_divergence`
  - `cvar_5pct_diff`
  - `regime_recency_weight`
  - `divergence_signal`
  - or any case-insensitive variant matching `cvar.*diverg|recency`.
- Discriminating-power: catches the structural attempt to re-introduce
  the detector affordance.

**Scenario 4 — `test_no_cvar_divergence_value_in_dashboard_templates`**
- AST-scan / grep across `dashboard/templates/` (or the rendered HTML
  test client).
- Assert no template renders a derived value labeled `divergence`,
  `delta`, `gap`, `spread` between the two CVaRs.
- Allowed: two separate values displayed side-by-side, each in its
  own S-3 group.

**Scenario 5 — `test_long_window_writes_null_when_insufficient`**
(F-4 / sentinel discipline carry-through).
- Construct a state where the long window's 3-year pool has < the
  long-window sufficiency threshold (an early-life symbol, or a
  thin-history regime).
- Compute the M2 row.
- Assert `cvar_5pct_long IS NULL` AND `cvar_n_tail_long IS NULL`.
- Discriminating-power: catches a fabricated zero-or-default value.

**Scenario 6 — `test_long_window_does_not_add_a_second_replay_anchor`**
(§3.7 + §B.6 — Phase 1 = 1 anchor invariant carry-through).
- The plan's claim per §B.6: "the longer window is a second statistic
  off the same `cycle_id`-seeded resample discipline — so v3's
  'Phase 1 = 1 anchor' claim is unchanged."
- Assert the seed derivation for the long window equals the seed
  derivation for the short window (same `cycle_id`-keyed recipe;
  no additional independent seed). The two windows share the same
  resample discipline.

**Scenario 7 — `test_long_window_per_cycle_write_is_a_single_insert_not_a_second_insert`**
(§B.6 — execution-path cost zero on top of M2).
- Patch the SQLite connection's `execute` to count `INSERT` calls
  in a cycle.
- Run a full M2 cycle with the long window enabled.
- Assert the M2 write is exactly one INSERT per cycle (a wider row,
  not a second row), per §B.6: "the per-cycle write is still one
  INSERT of one (wider) row into one table."

### D3. Test naming
- `test_cvar_5pct_long_column_is_additive_nullable`
- `test_each_window_displayed_independently_under_s3_contract`
- `test_no_cvar_divergence_or_recency_weight_column_in_schema`
- `test_no_cvar_divergence_value_in_dashboard_templates`
- `test_long_window_writes_null_when_insufficient`
- `test_long_window_does_not_add_a_second_replay_anchor`
- `test_long_window_per_cycle_write_is_a_single_insert_not_a_second_insert`

## Dependencies
- BLOCKED BY: the second-window column adoption decision (the
  implementing team's choice per §3.7 team's-choice latitude).
- BLOCKED BY: M2 estimator + long-window pool fetch (the long
  window reads the same rolling Alpaca pool the kNN MC already uses).

## Golden-fixture tests required
No JSON fixtures — invariants are read from schema/templates/code.

## Definition of Done
- [ ] Test file committed.
- [ ] All seven scenarios RED on `main` (if second window adopted)
  or skipped with explicit `pytest.mark.skip("second-window not
  adopted")` (if not adopted).
- [ ] Scenarios 3 and 4 are the load-bearing structural enforcement of
  §B.6's binding constraint — they catch any future attempt to
  re-introduce the rejected detector affordance.
- [ ] Scenario 7 (single-INSERT) enforces the H-3 framing carry-over.

## Risk callouts
- **Phase placement.** This plan lives in `phase-2/` pending the
  implementer's adoption decision. If adopted in Phase 1 (per §B.6's
  "may gain"), the plan moves to `phase-1/` and rejoins the M2 RED
  set. The substance is unchanged.
- **Future-proofing.** Scenarios 3 and 4 are forward-looking: they
  catch even a developer who renames `cvar_divergence` to something
  semantically equivalent (`cvar_delta`, `cvar_gap`). The regex
  pattern catches common synonyms; the reviewer must reject any
  new derived-value column at PR review even if the test misses it.
- **Operator interpretation.** Even with two windows displayed
  side-by-side under independent S-3 contracts, an operator may
  mentally compute the divergence. §B.6 accepts this: "If a
  divergence delta is ever computed it belongs in an offline
  analysis notebook, never in a state-DB column the dashboard can
  render." The discipline is on persistence + display, not on
  operator cognition.

## Out of scope
- The divergence quantity itself as a notebook artifact — §B.6
  explicitly allows offline notebook computation; tests do not police
  notebooks.
- A live ES-coverage test on the long window — categorically out of
  scope per the council's K-2 framing.
- Any signal use of the two windows — they are diagnostic-only.
