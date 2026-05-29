# Phase 1 — Additive `N_effective = N_optuna + S` Accounting

**Feature:** Install the **additive** multiple-testing accounting consumer
in `autotuner.py`. Replaces the retracted multiplicative
`N_optuna × D_spec` form. NN1-honest case: `S = 0`, `N_effective =
N_optuna`, the haircut is **byte-identical to today's**. Tripwire-only;
the haircut fires harder ONLY when someone P&L-toured a facet.

**Phase:** Phase 1 (HARDEN floor — overfitting-accounting spine)

**Owner agent-type:** `optuna-specialist` (drives), `quant-test-writer`
(adversarial RED on the S-bumping edge cases).

## Source-of-truth references

- `docs/handoff/decision-science-council-synthesis.md` §2.2 (settled —
  "additive `N_effective = N_optuna + S`"; multiplicative retracted;
  three properties — NN1-honest no-op, conservative upper bound,
  tripwire), §2.5 (NN1 — accounting is the structural enforcement).
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §A.0
  bullet 3 (additive is "deliberately conservative — errs safe"), §B.3
  route 2 ("Defect 2 — a P&L-toured spec that received its own sub-sweep
  contributes its **full sub-sweep count** to `S`").
- `docs/handoff/council-converged-migration-plan.md` §5 (`N_effective`
  haircut consumer, zero schema impact beyond migration 022).
- `autotuner.py:319-356` — `benjamini_hochberg_adjust`: where `N` flows
  in (currently `n = len(p_values)`).
- `autotuner.py:1041-1047` — the AI-branch BHY application.

## Why

The autotuner's BHY haircut today calibrates over `N = n_optuna`
(`len(p_values)`). That is correct **only** if `n_optuna` is the entire
multiple-testing budget that was actually consumed. The moment a
researcher tours K specifications cheaply and full-sweeps the winner,
the honest test count is `N_optuna + (K - 1)` — the additional `K - 1`
specs were tested-and-rejected, and the Yekutieli `c(N)` factor must
correct over the **honest** N.

The retracted multiplicative form `N_optuna × D_spec` punished trials
that were **never run** — wrong direction. The additive form is exact for
the NN1-honest case (S=0) and a **conservative upper bound** for the
NN1-violation case (it can reject a genuine signal, never pass a
spurious one — synthesis §2.2 property 2).

The accounting is a **tripwire**, not a routine penalty. Under NN1
(every facet frozen by theory/mandate/calibration, NEVER by P&L) →
`S = 0` → `N_effective = N_optuna` → haircut **byte-identical to
today's**. The accounting bites only when NN1 is violated — which is the
**point**: it makes NN1 enforced by the math, not by the design doc
alone.

## Deliverables

### D1 — `compute_n_effective(n_optuna: int, ledger_query: Callable) → int`

A NEW function in `autotuner.py` next to the BHY block:

```python
def compute_n_effective(n_optuna: int, ledger_query) -> int:
    """Return the honest multiple-testing count for the BHY haircut.

    N_effective = N_optuna + S, where S is the sum of n_configs_searched
    over `researcher_dof_ledger` rows whose `evidence_source` is
    `BACKTEST_SELECTION` AND that are NOT the winner of the Optuna sweep
    actually run (council synthesis §2.2; v3-and-divergence-evaluation
    §B.3 route 2 — a P&L-toured spec that received its own sub-sweep
    contributes its **full sub-sweep count** to S).

    NN1-honest case: every facet is theory/mandate/calibration-frozen so
    no row has evidence_source='BACKTEST_SELECTION' → S = 0 →
    N_effective = N_optuna and the haircut is byte-identical to today's.

    The accounting is a conservative upper bound (errs safe — rejects a
    genuine signal, never passes a spurious one) and a tripwire that
    enforces NN1 structurally.

    `ledger_query` is a callable returning the list of relevant ledger
    rows; injected for testability and to keep `compute_n_effective`
    pure with respect to its DB read.
    """
```

Constraints:
- `n_optuna` is the trial count of the current Optuna sweep — not the
  configured `n_trials` (a trial can be pruned / fail).
- `S` is computed by SUMMING `n_configs_searched` over qualifying ledger
  rows (NOT counting them — Defect 2: a P&L-toured spec with a sub-sweep
  contributes its full grid size).
- `ledger_query` is a tightly-typed dependency-injection seam — in
  production it wraps `database.get_researcher_dof_ledger_for_run(...)`;
  in tests it is a deterministic fixture.
- Returns `int >= n_optuna`. A negative or `S` from a malformed ledger
  row raises loud (a malformed ledger is a correctness defect, not
  swallow-and-continue).
- Pure: no side effects.

### D2 — `_haircut_select` accepts an explicit `n_effective` parameter

`_haircut_select` (`autotuner.py:698-732`) gains a new parameter
`n_effective: int`. Internally, `benjamini_hochberg_adjust` is invoked
with the **N-effective-many** p-values — see D3.

Default value: `n_effective = len(p_values)` — preserves backward
compatibility for any test fixture that does not supply an effective N.

### D3 — `benjamini_hochberg_adjust` N-source

The internal `n = len(p_values)` in `benjamini_hochberg_adjust`
(`autotuner.py:341`) **remains unchanged** — the function continues to
adjust over the p-values it actually sees. The N-redirection happens
ABOVE the call: `_haircut_select` pads the p-value list with synthetic
"tested-and-rejected" placeholder p-values for the `S` untested
configurations.

Two concrete shapes — the implementing team picks one:

**Shape A (recommended).** `_haircut_select` passes
`benjamini_hochberg_adjust(p_values + [1.0] * S)` — the `S` placeholders
are at-the-cap p-values (the BHY-honest representation of
"tested-and-rejected at no significance"). The Yekutieli `c(N)` factor
in `benjamini_hochberg_adjust` then computes over `N = n_optuna + S`
exactly because `len(p_values + [1.0] * S) == n_optuna + S`. The
running-min step-up correctly inflates every adjusted p-value through a
larger `c(N)`. The winner-selection (argmin over the **original**
n_optuna p-values, dropping the padded indices) returns the original
trial. **Zero change to `benjamini_hochberg_adjust`** — preserved
byte-identical per the BHY preservation plan.

**Shape B (alternative).** `benjamini_hochberg_adjust` accepts an
optional `n_effective` parameter and substitutes it into the `c(N)`
calculation, while `n = len(p_values)` continues to drive the rank
iteration. This is slightly more efficient (no padding allocation) but
**requires modifying** `benjamini_hochberg_adjust` — which the BHY
preservation plan forbids.

**The plan picks Shape A.** It satisfies the BHY preservation contract.

### D4 — Ledger query helper (`database.py`)

A NEW read-only helper (single-DB, state-DB) returning the rows that feed
`S`:

```python
def get_researcher_dof_ledger_for_run(run_timestamp: str) -> list[Row]:
    """Return researcher_dof_ledger rows whose evidence_source is
    BACKTEST_SELECTION for the active autotune run window.

    Filters out the winning spec_bundle_id (it is already counted in
    n_optuna). Returns 0 rows in the NN1-honest case → S = 0.
    """
```

Constraints:
- Single-DB (state DB) read; consistent with the converged-migration
  plan's two-DB rule.
- Excludes the winning `spec_bundle_id` — the winner is the spec
  selected by the Optuna sweep actually run; it is already counted in
  `n_optuna`. Counting it again would double-count.
- `WHERE COALESCE(touched_frozen_eval, 0) = 0` — frozen-eval-tainted
  rows are NOT counted here; they are a separate, harder violation that
  raises a `OOS_PEEK` alarm in the Overfitting Conscience advisor (not
  in this plan's scope, but flag-out cleanly).

### D5 — Persistence write-back

`autotune_runs` row (migration 022) gains write of:
- `d_spec`: `COUNT(DISTINCT spec_bundle_id)` over qualifying rows — the
  distinct-P&L-toured-bundle count (NOT the sum; sum is `S`).
- `n_effective`: `n_optuna + S`.
- `overfitting_verdict`: human-readable summary — e.g.
  `"NN1_HONEST n_optuna=500 d_spec=0 n_effective=500"` or
  `"NN1_VIOLATION_TRIPWIRE n_optuna=500 d_spec=3 n_effective=523"`.

`d_spec` and `n_effective` are **NOT** the same number; the schema design
deliberately stores both (council-converged migration plan §5).

### D6 — Inline NN1 disclosure comment

A NEW comment block near `compute_n_effective` stating verbatim:

```
NN1 (synthesis hard gate — council §2.5): the generator family and the
horizon convention may NEVER be frozen by P&L / backtest selection.
This function ENFORCES NN1 STRUCTURALLY: a P&L-frozen facet appears as
a researcher_dof_ledger row with evidence_source='BACKTEST_SELECTION',
which bumps S, which inflates N_effective, which inflates the Yekutieli
c(N) factor, which inflates every adjusted p-value, which raises the
FDR-gate bar - making the haircut harder to clear. NN1-honest case
(S=0) → byte-identical to today's haircut.
```

## Dependencies

- **Blocks:** Phase 1 — overfitting-accounting spine completion.
- **Blocked by:** persistence-architect's migrations 015
  (`spec_bundles`/`spec_facets`), 020 (`researcher_dof_ledger`), and 022
  (`autotune_runs` EUT columns including `d_spec` and `n_effective`).
- **Soft-coupled to:** the BHY haircut preservation plan (this plan adds
  the N-padding above `benjamini_hochberg_adjust`; preservation plan
  forbids modifying the function itself — Shape A satisfies both).
- **Soft-coupled to:** the NN1 spec-freeze discipline plan (this plan is
  the structural enforcement; that plan is the rule and the team-process
  discipline).

## Golden-fixture tests required

### T1 — NN1-honest no-op (the critical one)

Fixture: empty `researcher_dof_ledger` (no `BACKTEST_SELECTION` rows for
the run window). Assert:
- `compute_n_effective(500, empty_ledger_query) == 500`;
- `_haircut_select(trials, ..., n_effective=500)` returns the **same**
  winner, **same** `p_adj`, **same** `t_stat` as
  `_haircut_select(trials, ..., n_effective=len(trials))` — byte-identical;
- `autotune_runs` row writes `d_spec=0`, `n_effective=500`,
  `overfitting_verdict` starts with `"NN1_HONEST"`.

This is the **defining test** of the accounting: today's behaviour is
preserved exactly in the NN1-honest case. If T1 fails, ship-blocking.

### T2 — Single P&L-toured facet (tripwire fires)

Fixture: one `researcher_dof_ledger` row with
`evidence_source='BACKTEST_SELECTION'`, `n_configs_searched = 3`.
Assert:
- `compute_n_effective(500, ledger_with_one_row) == 503`;
- the adjusted p-values are strictly larger than the NN1-honest case (the
  inflated `c(N)` raises every adjusted p);
- the winner of the BHY selection may change (or `None` if the inflated
  c(N) pushes everything past `HARVEY_LIU_FDR_Q`) — the test asserts
  whatever the deterministic outcome is on a frozen fixture, pinning
  it.

### T3 — Defect-2 sub-sweep counting

Fixture: a single ledger row with `n_configs_searched = 7` (a P&L-toured
spec that received its own 7-config sub-sweep). Assert:
- `compute_n_effective(500, ledger) == 507`, NOT `501` (counting the
  bundle once rather than its full sub-sweep would be the v3-and-
  divergence-evaluation §B.3 Defect-2 understatement).
- `d_spec = 1`, `n_effective = 507` — both written.

### T4 — Frozen-eval-tainted row exclusion

Fixture: a ledger row with `touched_frozen_eval = 1`. Assert it is
**excluded** from the `S` sum (it is the separate, harder
Overfitting-Conscience alarm). T4 documents the boundary: this plan's
accounting does not double-fire with the OOS_PEEK alarm.

### T5 — Winner-bundle self-exclusion

Fixture: a ledger row whose `spec_bundle_id` matches the winning
`autotune_runs.spec_bundle_id`. Assert it is excluded from `S` (already
counted in `n_optuna` via the sweep that selected it). Catches a
double-count.

### T6 — Shape-A byte-identical preservation

Assert `benjamini_hochberg_adjust(p_values + [1.0] * 0)` returns the
exact same vector as `benjamini_hochberg_adjust(p_values)` — empty
padding is a no-op. This is the BHY-preservation contract under the
plan's chosen Shape.

### T7 — Conservative-upper-bound property

Property-based: for any random N=20 p-vector and any `S in [0, 5]`,
assert that `benjamini_hochberg_adjust(p + [1.0]*S)` produces adjusted
p-values element-wise `>=` those of `benjamini_hochberg_adjust(p)`.
Confirms the "errs safe" property.

## Definition of Done

1. T1-T7 RED on a clean implementer commit, GREEN after.
2. `pytest tests/autotuner/` PASSES — full suite; existing BHY tests
   pass unchanged.
3. `compute_n_effective` lives in `autotuner.py` next to the haircut
   block, with the NN1 disclosure comment block.
4. `_haircut_select` gains exactly one new parameter (`n_effective`);
   default preserves backward-compat.
5. `benjamini_hochberg_adjust` is **diff-empty** in the M1 commit (the
   BHY preservation contract).
6. `autotune_runs` row writes `d_spec`, `n_effective`,
   `overfitting_verdict` with the documented semantics.
7. Commit message: `feat(autotuner): study_name=<TS>__<symphony>,
   N_effective=N_optuna+S additive accounting installed (council §2.2);
   Shape-A p-value padding; benjamini_hochberg_adjust diff-empty;
   n_trials=500; objective=CRRA-EU mean(U)`.

## Risk callouts

- **The "this is just len(p_values)" simplification.** A maintainer
  reasonably argues: in NN1-honest steady-state, `n_effective ==
  len(p_values)` and the parameter is dead. The temptation is to delete
  the parameter and the padding shape. T1 (no-op equality) AND T2
  (tripwire fires) together kill that simplification — the parameter is
  byte-identical-no-op in the happy path AND load-bearing in the unhappy
  path.
- **Counting vs summing.** The Defect-2 case (T3) catches the most
  likely arithmetic error: counting ledger rows (`d_spec`) where the
  council requires summing `n_configs_searched`. Both are persisted
  precisely to disambiguate.
- **Frozen-eval double-fire.** A frozen-eval-tainted ledger row is a
  worse violation than a P&L-toured spec. The plan excludes it from `S`
  (T4) precisely because the Overfitting Conscience advisor surfaces it
  separately. If the plan included it, an operator might "fix" the
  OOS_PEEK alarm by hiding the row and unknowingly reduce `n_effective`.
- **Ledger-query timing.** If `compute_n_effective` runs BEFORE the
  current autotune run's ledger rows are written, those rows are
  excluded from the count and `S` understates. The plan's seam:
  `compute_n_effective` runs **after** the Optuna sweep completes AND
  after the current-run ledger rows are flushed; the query filters by
  `run_timestamp <= current_run_timestamp`. T5's winner-self-exclusion
  is the regression guard.
- **Schema drift.** `n_configs_searched` is a column on
  `researcher_dof_ledger` (migration 020). If the column is renamed or
  retypes, the `compute_n_effective` query silently breaks. The fixture
  validator in the council-converged migration plan §7 is the guard;
  this plan's tests use the schema-derived fixture, never hand-author.
- **`c(N)` cost.** For NN1-honest steady-state, `c(N) = c(500) ≈ 6.79`.
  Padding `S = 1000` would inflate to `c(1500) ≈ 7.84`. Conservative
  scaling is logarithmic — `S = 23` adds ~3% to `c(N)`, ~3% to every
  adjusted p, mild on a clear winner, ship-blocking on a borderline. The
  council documented this as **the point** (synthesis §2.2 property 2).
- **Two-DB cleanliness.** `researcher_dof_ledger` is state-DB. The query
  helper is single-DB. The autotuner reads it via the same accessor
  pattern as `get_symphony_strategy`. Never cross-join from
  `optuna_studies.db`.

## Out of scope

- The NN1 rule itself — owned by the NN1 spec-freeze discipline plan.
- The `researcher_dof_ledger` and `spec_bundles` schemas — owned by
  persistence-architect's migrations 015 / 019 / 020 / 022.
- The Overfitting Conscience advisor — `advisor_observations` row
  generation; owned separately (Phase-1 advisor scope; this plan emits
  the verdict string but does not run the LLM-authored advisor).
- The OOS_PEEK frozen-eval alarm — distinct alarm path, NOT this
  accounting.
- The multiplicative form — explicitly retracted; do NOT re-implement
  even "behind a flag for comparison."
