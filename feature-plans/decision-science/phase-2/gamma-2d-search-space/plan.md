# Phase 2 — `gamma` Integration into the 2D Search Space

**Feature:** When Phase 2 unlocks, transition `gamma` from a Phase-1
theory-frozen spec facet to a Phase-2 Optuna-searched parameter. The
search space becomes 7-D — the existing 6 + `gamma` — but `lambda`
remains frozen-by-mandate. `gamma` now shapes the CVaR-budget as a
risk-aversion parameter (council §5.3: "EUT+CVaR ships as
CVaR-with-risk-aversion-shaping"). W-H4 wealth-argument floor preserved.

**Phase:** Phase 2 (HONEST RESHAPE — evidence-gated)

**Owner agent-type:** `optuna-specialist` (drives), `quant-test-writer`
(adversarial RED), `risk-engine-specialist` (reviews the gamma-shaping
of the CVaR-budget — out-of-scope coupling here, but flagged).

## Source-of-truth references

- `docs/handoff/decision-science-council-synthesis.md` §3.5 (Phase-1
  search space is 6-D; gamma frozen, not added), §5.3 ("Expected
  utility: EUT enters as the `gamma` risk-aversion *shaping* of the
  CVaR budget — there is no separate `E[U(exit)]` vs `E[U(hold)]`
  crossover layer (a soft objective arbitrated by a boolean resolver
  is a category mismatch). This is an honest narrowing of the
  4-primitive pitch"), §5.6 R-5 ("'EUT+CVaR' becomes
  'CVaR-with-risk-aversion-shaping' in every buildable candidate"),
  §6.4 (the user reframing accepted).
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §A.1
  H-1 (CRRA is unbounded below for `gamma >= 1`; `WEALTH_ARG_FLOOR`
  must hold under a searched gamma too).
- The Phase-1 NN1 spec-freeze plan: gamma was THEORY-frozen in Phase 1
  precisely because the Phase-1 objective had no degree of freedom for
  it; Phase 2 introduces the CVaR-budget surface that genuinely couples
  to gamma.

## Why

In Phase 1, `gamma` was THEORY-frozen because the objective
(`mean(U)` over CRRA-transformed guard-alpha) had no second
risk-control surface for gamma to interact with. A theory-chosen
single value sufficed.

In Phase 2 (if unlocked), the CVaR co-signal introduces a tail-budget
surface. The honest narrative (council §5.3) is: there is **no live
`E[U(exit)]` vs `E[U(hold)]` crossover** — the boolean-resolver
composition is a category mismatch with a soft EU objective. Instead,
`gamma` *shapes* the CVaR-budget hysteresis: a higher gamma →
tighter co-signal threshold; a lower gamma → looser. This is the
honest narrowing of "EUT+CVaR" to "CVaR-with-risk-aversion-shaping."

Under this shaping, `gamma` becomes a genuine **trial parameter** —
the offline objective `mean(U)` AND the live co-signal threshold both
depend on it, so there is a legitimate optimization surface. Optuna
searches gamma; lambda does NOT search (separate plan).

This plan installs the 2D search-space transition. Search-space sizing
matters for `c(N)`: the Yekutieli c(N) factor scales with the number
of searched parameters' effective contribution to multiple testing.

## Deliverables

### D1 — Phase-2 spec_bundle reclassification of gamma

When (if) Phase 2 unlocks, a NEW `spec_bundles` row is frozen with:
- `gamma` removed from the THEORY-frozen facet set
- `gamma` registered as a **SEARCHED** parameter in
  `researcher_dof_ledger` with `decision_type='SEARCHED'`,
  `evidence_source='THEORY'` (the *bounds* are theory-frozen; the
  *value* is Optuna-searched within those bounds)
- `gamma_min` and `gamma_max` registered as the bound-defining facets
  (`freeze_discipline='THEORY'`, theory-justified ranges)

The bound-defining facets are themselves NN1-honest because the
*bounds* are theory-frozen (e.g. `gamma in [1.0, 5.0]` chosen a-priori,
NOT by tuning the range to maximise the validation Sharpe).

### D2 — `gamma` added to `OPTUNA_SEARCH_SPACE_KEYS`

`OPTUNA_SEARCH_SPACE_KEYS` (`autotuner.py:52-56`) gains `"gamma"` in
Phase 2:

```python
OPTUNA_SEARCH_SPACE_KEYS = frozenset({
    "TAKE_PROFIT_MC_PCT", "VWAP_CROSS_HWM_PCT",
    "VWAP_BLEED_MULTIPLIER", "VWAP_BLEED_TICKS",
    "PARABOLIC_VELOCITY_THRESHOLD", "MAX_PARABOLIC_SQUEEZE",
    "gamma",  # Phase 2 — gamma searches as CVaR-budget shape (council §5.3)
})
```

The Phase-1 NN1 disclosure block's forbidden-from-search-space list is
**updated** to remove `gamma`. The block becomes:

```
NN1 (council §2.5): the following facets MUST NEVER appear in
OPTUNA_SEARCH_SPACE_KEYS — frozen OUTSIDE the search space:
  - utility_family        (THEORY)
  - wealth_argument       (THEORY)
  - generator_family      (STYLIZED_FACT)
  - horizon_convention    (CADENCE)
  - lambda (CVaR budget)  (MANDATE)
  - regime_bucket_thresh  (CALIBRATION)
gamma was THEORY-frozen in Phase 1; Phase 2 promotes it to a SEARCHED
parameter with theory-frozen bounds (council §5.3 — gamma shapes the
CVaR budget). See Phase-2 plan gamma-2d-search-space.
```

`validate_search_space_nn1` updates its `forbidden_in_search_space`
set accordingly — drops `gamma`.

### D3 — `gamma` bounds: theory-justified, named

New named constants:

```python
# CRRA risk-aversion bounds — theory-frozen (NN1-honest).
# gamma in [1.0, 5.0]:
#   - 1.0 lower bound: log utility; the boundary case (council §3.3).
#     Below 1.0, CRRA becomes risk-seeking — not a defensible
#     risk-control parameterization.
#   - 5.0 upper bound: extreme risk-aversion. Above 5.0, CRRA
#     under-weights ALL non-tail outcomes so heavily that the
#     objective decouples from realised guard-alpha — empirically
#     and theoretically unstable.
# The BOUNDS are theory-frozen (NN1-honest); the VALUE within is
# Optuna-searched in Phase 2.
_SS_GAMMA_MIN = 1.0
_SS_GAMMA_MAX = 5.0
```

The bounds value (1.0, 5.0) is the team's-choice within the
risk-engine-specialist's theory range — explicit; documented at the
constant.

### D4 — `objective(trial)` gains a `suggest_float("gamma", ...)`

`objective(trial)` (`autotuner.py:980-998`) gains a 7th
`trial.suggest_float("gamma", _SS_GAMMA_MIN, _SS_GAMMA_MAX)` call.
Behavior:
- the suggested gamma is captured at the top of the trial body;
- threaded through to `run_simulation_crra_eu(..., gamma=trial_gamma)`;
- threaded through to `compute_crra_eu_tstat(..., gamma=trial_gamma)`
  (via the `tstat_fn` closure that `_haircut_select` receives);
- threaded through to the CVaR co-signal threshold computation
  (Phase-2 risk-engine code, out-of-scope here);
- recorded as a `trial.set_user_attr("gamma", trial_gamma)` for the
  `autotune_runs` write-back to read.

### D5 — `autotune_runs.gamma` write-back updates

`autotune_runs.gamma` (migration 022) in Phase 2 carries the WINNER's
gamma — the trial-attr value of the BHY-winning trial. Same COPY
discipline as the lambda-budget plan (D4 there).

### D6 — W-H4 wealth floor preservation under searched gamma

Per H-1, `WEALTH_ARG_FLOOR` must hold under every trial's gamma —
including `gamma = _SS_GAMMA_MAX = 5.0`. The risk-engine-specialist's
floor sizing (Phase-1 M1 plan D3) must be **re-verified** at the upper
gamma bound: for `gamma = 5.0`, `u(W) = W^(-4) / (-4)`, which goes to
`-inf` as `W → 0+` and grows in magnitude FAR faster than at gamma=1.
The Phase-1 floor that was safe at the single theory-chosen gamma must
remain safe across the searched range.

**Re-verification step:** Phase-2 cycle's risk-engine-specialist
re-derives the floor under the worst-case bound and either:
- confirms the Phase-1 floor remains safe, or
- raises the floor (a `spec_bundles` `WEALTH_ARG_FLOOR` facet update —
  new bundle, new hash, new frozen_at).

This plan ASSERTS the re-verification surface: the
`compute_crra_eu_tstat` plan T2 (near-floor finite-t) gains a Phase-2
sub-case at `gamma = _SS_GAMMA_MAX`.

### D7 — `c(N)` consequence under 7-D search

The Yekutieli c(N) factor remains computed over the trial count
(currently 500). Adding gamma to the search space does NOT directly
change `c(N)` — `c(N)` is over `N = n_trials`, not over the number of
searched parameters. The change is **indirect**: gamma's inclusion
broadens the explored region, which CAN make individual trial
significance more variable (each trial samples a different gamma) —
but the haircut math is unchanged.

This plan **does not modify** `benjamini_hochberg_adjust` or
`compute_haircut_pvalue`. The BHY preservation discipline
(Phase-1 plan) carries through.

## Dependencies

- **Blocked by:** Phase 1 — M1 CRRA-EU objective plan (gamma's
  Phase-1 frozen-facet seed exists).
- **Blocked by:** Phase 1 — NN1 spec-freeze plan (the disclosure block
  and `validate_search_space_nn1`).
- **Blocked by:** Phase 1 — spec-bundles-integration plan (the
  `get_active_spec_bundle` accessor).
- **Blocked by:** Phase 2 unlock — all four §5.1 preconditions.
- **Coupled to:** Phase 2 lambda-frozen-by-mandate plan (must ship in
  the same Phase-2 cycle — gamma searches AND lambda does not, both
  contracts).
- **Coupled to:** Phase 2 multi-testing-accounting plan (T is genuine
  tail-obs count, not path count — the CVaR-derived haircut score's
  T-source).
- **Coupled to:** Phase 2 fold-structure plan (NN2 narrowed —
  60/20/20 preserved for the CRRA-mean objective; rolling purged
  k-fold only if a CVaR-VALUED objective is introduced, which this
  plan does NOT introduce).

## Golden-fixture tests required

### T1 — `gamma` IN search-space (positive pin, Phase 2 only)

Assert `"gamma"` IN `OPTUNA_SEARCH_SPACE_KEYS` after the Phase-2
cutover. Catches a Phase-2 cycle that ships the migration but forgets
the search-space update.

### T2 — `validate_search_space_nn1` does NOT reject `gamma` in Phase 2

Assert `validate_search_space_nn1()` does NOT raise under the Phase-2
config (gamma in search-space; gamma's forbidden-listing removed from
the validator's set). Catches a stale validator that breaks Phase 2.

### T3 — `gamma` bounds are theory-frozen facets

Fixture: Phase-2 active `spec_bundles` row. Assert there exist
`gamma_min` and `gamma_max` facets with
`freeze_discipline='THEORY'`, AND a `gamma` `researcher_dof_ledger` row
with `decision_type='SEARCHED', evidence_source='THEORY'`. Catches a
Phase-2 cutover that forgets to register the SEARCHED-with-theory-
bounds shape.

### T4 — Trial suggests gamma within bounds

Property-based: run 100 trials of `objective(trial)`; assert every
`trial.user_attrs["gamma"]` falls within `[_SS_GAMMA_MIN,
_SS_GAMMA_MAX]`. Catches a future bound-narrowing PR.

### T5 — Trial-attr gamma threads through to t-stat

Fixture: a fake trial with `user_attrs["gamma"] = 2.5`. Assert
`_haircut_select`'s t-stat function consumes `2.5` (not the active
bundle's Phase-1 gamma). Catches a future drift where the trial's
gamma is suggested but the t-stat still uses the bundle's
theory-frozen value.

### T6 — W-H4 floor finite-t at gamma_max

Fixture: a guard-alpha series with one near-floor day; gamma = 5.0
(`_SS_GAMMA_MAX`). Assert `compute_crra_eu_tstat(series, gamma=5.0)`
returns a FINITE value, `sd(U)` finite, no NaN propagation. Extends
the Phase-1 T2 of the `compute_crra_eu_tstat` plan.

### T7 — `autotune_runs.gamma` copies winner gamma

Fixture: a small Phase-2 trial set with one winning trial whose
`user_attrs["gamma"] = 3.1`. Assert `autotune_runs.gamma == 3.1` AND
matches the winner's trial-attr (not the bundle's theory-frozen value,
which would be a Phase-1-stale leak).

### T8 — Phase-1 ↔ Phase-2 cross-test

Phase-1 trial set: gamma frozen at 2.0 (the Phase-1 theory choice);
search space 6-D. Phase-2 trial set: gamma searches in [1.0, 5.0];
search space 7-D. Assert the haircut machinery (`_haircut_select`,
`benjamini_hochberg_adjust`) produces consistent winner rankings on a
fixture where both runs would naturally pick the same parameter set —
catches drift in the BHY logic between phases.

## Definition of Done

1. T1-T8 RED on a clean implementer commit, GREEN after.
2. `pytest tests/autotuner/` PASSES — full suite. Includes the Phase-1
   tests with the gamma-frozen behaviour AND the Phase-2 tests with
   the gamma-searched behaviour (gated by a Phase-2-enabled fixture
   flag).
3. `OPTUNA_SEARCH_SPACE_KEYS` contains `"gamma"` in Phase 2.
4. `_SS_GAMMA_MIN` and `_SS_GAMMA_MAX` are named module-scope
   constants with the source-comment documenting the theory bounds.
5. `objective(trial)` calls `trial.suggest_float("gamma", ...)`.
6. The Phase-2 `spec_bundles` registration:
   - drops the THEORY-frozen `gamma` facet,
   - adds `gamma_min` / `gamma_max` THEORY facets,
   - adds `researcher_dof_ledger` row `gamma`
     `decision_type='SEARCHED'`.
7. `autotune_runs.gamma` writes the WINNER's trial-attr gamma.
8. W-H4 re-verification step (D6) completed — either floor unchanged
   with a documented why, or floor raised with a new spec_bundle.
9. Commit message: `feat(autotuner): study_name=<TS>__<symphony>,
   gamma promoted to SEARCHED (council §5.3); 7-D search space;
   _SS_GAMMA_MIN/MAX theory-frozen; lambda still MANDATE-frozen;
   n_trials=500; objective=CRRA-EU-with-CVaR-shaping mean(U)`.

## Risk callouts

- **Bound tuning is NN1 violation.** A maintainer reasonably argues
  "let's widen `_SS_GAMMA_MAX` to 10.0 — we got better validation
  Sharpe at 7." That is bound-selection by P&L — an NN1 violation
  (P&L-selected spec facet). The bounds are theory-frozen; a widening
  requires a new theory justification AND a new `spec_bundles` row.
  Surface the discipline in the constant's source comment AND in PR
  review.
- **W-H4 at the upper bound.** The Phase-1 floor was sized for the
  single theory gamma. At gamma_max=5.0, CRRA's negative tail grows
  much faster — `W^(-4)/(-4)` versus `W^0/0 = log(W)`. T6 catches a
  near-floor case that is finite at gamma=1 but produces a non-finite
  t-stat at gamma=5. The re-verification step (D6) is required, not
  optional.
- **Trial-attr gamma vs bundle gamma confusion.** Phase 2 has TWO
  gamma values present during a run: the bundle's `gamma_min`/`gamma_max`
  (theory bounds) and the trial's suggested `gamma` (the value used).
  T5 catches a future PR that mixes them up. Threading discipline:
  bundle bounds drive `suggest_float`; trial value drives objective
  and t-stat; winner trial value drives `autotune_runs.gamma`.
- **c(N) cost.** Adding gamma broadens trial diversity; significance
  could weaken at the margin (each trial samples a slightly different
  objective surface). The haircut is unchanged but the EFFECTIVE
  power may drop. This is a documented risk of the Phase-2 reshape,
  not a defect in the haircut.
- **CVaR-budget shaping coupling.** This plan installs the
  search-space side; the actual gamma-shaping of the CVaR-budget
  hysteresis is owned by the Phase-2 risk-architect plan. The
  coupling test (T8) confirms the autotuner-side wiring is correct;
  end-to-end shaping correctness is a separate validation surface.
- **Lambda must NOT bleed in.** The Phase-2 lambda plan ships in the
  same cycle. A common bug shape: a maintainer "completes" the 2D
  search space by adding both gamma AND lambda. T1 here positively
  pins gamma; the lambda plan's T1 negatively pins lambda. Both
  required to catch the bug.
- **Phase 1 backward-compat.** The Phase-1 tests must continue to
  pass under their fixture (gamma frozen). The fixture flag pattern
  in T8 makes this surface explicit. A Phase-2 PR that retro-breaks
  Phase 1 behaviour would fail the Phase-1 tests.

## Out of scope

- The CVaR-budget hysteresis state machine and gamma-shaping math —
  owned by the risk-architect lens (Phase-2 risk-architect plan).
- The horizon-convention choice — escalated to user per §6.2.
- The Phase-2 path-generator — owned by the risk-architect lens.
- `lambda` searching — explicitly forbidden; see the lambda-frozen
  plan.
- Phase-2 fold-structure changes — NN2 narrowed; owned by the
  fold-structure plan in this folder.
- The CVaR-derived haircut t-stat T-source — owned by the
  multi-testing-accounting plan in this folder.
- Wealth-argument re-derivation under Phase 2 — W-H2 is solved in
  Phase 1; this plan inherits the solution and only re-verifies
  W-H4 at the upper gamma bound.
