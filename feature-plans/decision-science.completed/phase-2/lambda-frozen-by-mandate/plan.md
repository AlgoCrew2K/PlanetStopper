# Phase 2 — `lambda` Frozen by Mandate (NOT Optuna-searched)

**Feature:** Encode the binding discipline that `lambda` (the CVaR-budget
parameter) is frozen by **mandate**, NOT searched by Optuna. A searched
`lambda` against a regime-drifting effective alpha is optimization
against a non-stationary objective, which the BHY haircut cannot
correct. The system is therefore honestly **ONE tuned parameter — gamma —
not two**.

**Phase:** Phase 2 (HONEST RESHAPE — evidence-gated; ships only if four
preconditions all pass per council §5.1)

**Owner agent-type:** `optuna-specialist` (drives), `quant-test-writer`
(adversarial RED on the "let me just add lambda to the search space" PR
shape).

## Source-of-truth references

- `docs/handoff/decision-science-council-synthesis.md` §5.3 ("lambda
  frozen by mandate, NOT Optuna-searched — a searched lambda compared
  against a regime-drifting effective alpha is optimization against a
  non-stationary objective, which the BHY haircut cannot correct. **The
  system is therefore honestly ONE tuned parameter — gamma — not two**"),
  §6.2 (the horizon trilemma — effective alpha ≠ literal 5% under
  iterated CVaR; the "regime-drifting effective alpha" is concrete),
  §6.4 ("EUT+CVaR" ships as "CVaR-with-risk-aversion-shaping").
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §A.0
  bullets 1 + 4 (the validation wall holds; NN1 is the precondition
  that keeps the haircut TRUE).
- The Phase-1 NN1 plan: `lambda` is **already pre-named** in the NN1
  disclosure block as `MANDATE`-frozen.

## Why

Phase 2 (if it ever unlocks per the four §5.1 preconditions) introduces
a CVaR co-signal. A naive reading of the design might suggest:
"`lambda` is a budget level; let's tune it on the validation fold to
find the best level." That reading is wrong, for two compounding
reasons:

1. **Non-stationary objective.** A CVaR estimate's *effective alpha*
   drifts with the regime (council §2.4 / §6.2 — the horizon trilemma
   has no clean answer; iterated CVaR's effective alpha is **not** the
   literal nominal "5%"). Tuning `lambda` against a moving alpha is
   optimization against a non-stationary objective. The BHY haircut
   corrects multiple-testing over a stationary signal; it cannot fix
   non-stationarity.
2. **NN1 violation.** Searching `lambda` would make it a P&L-selected
   spec facet — the exact form NN1 forbids. The Yekutieli c(N) factor
   would see only the trial-sweep, not the lambda-tour, and the
   haircut would silently understate its effective N.

The honest position: **`lambda` is set by mandate** — operator decision,
documented justification, immutable until a mandate change. The system
has **one** Optuna-tuned objective parameter (`gamma`) regardless of how
many primitives the pitch named. Section §5.3 of the council synthesis
states this verbatim.

## Deliverables

### D1 — `lambda` registered as MANDATE-frozen facet

When (if) Phase 2 unlocks, a NEW `spec_bundles` row is frozen that adds
a `lambda` facet to the four Phase-1 facets, with:
- `facet_name = "lambda"`
- `facet_value = <mandated scalar — e.g. 0.05 for a 5% CVaR budget>`
- `freeze_discipline = "MANDATE"`
- `evidence_source = "MANDATE"`
- `justification` = a mandated rationale (e.g. "operator policy:
  Basel-FRTB-style 5% tail budget").

Single source of truth: the `spec_bundles` registry, never a Python
constant. The autotuner reads it through the same `get_active_spec_bundle`
accessor as `gamma` (Phase-1 spec-bundles-integration plan).

### D2 — `OPTUNA_SEARCH_SPACE_KEYS` contains NO `lambda`

`OPTUNA_SEARCH_SPACE_KEYS` (`autotuner.py:52-56`) does NOT contain
`"lambda"` in Phase 2. The Phase-1 NN1 disclosure block already
pre-names `lambda` in the forbidden set (`validate_search_space_nn1`).
This plan ratifies it: Phase 2 ships with `lambda` in the
forbidden-from-search-space set and **OPTUNA_SEARCH_SPACE_KEYS
unchanged with respect to lambda**.

### D3 — `lambda` consumer signature

Wherever Phase 2 code uses `lambda` (the CVaR co-signal threshold, the
budget reference for `gamma`-shaping per §5.3, the hysteresis band's
asymmetry), the value is **read from the active spec_bundle** ONCE per
`run_autotuner` invocation and threaded through. NEVER hard-coded
inline; NEVER read from a module-level constant; NEVER passed as a
trial parameter.

A NEW helper `get_mandated_lambda(active_bundle: dict) → float` returns
the value from the bundle's `spec_facets` projection. Raises if absent
(a Phase-2 cutover where lambda is absent is unrecoverable — fail
loud).

### D4 — `autotune_runs.lambda_budget` write-back

Per migration 022, `autotune_runs` already includes a `lambda_budget`
column. Phase 2 writes the COPIED value of the mandated lambda into
that column. The COPY discipline (Phase-1 spec-bundles-integration
plan D4) carries through.

### D5 — Mandate-change cutover discipline

When the operator changes the mandated `lambda`, the discipline is:
1. A NEW `spec_bundles` row is frozen with the new `lambda` value AND
   a fresh `bundle_hash` AND a fresh `frozen_at`.
2. The old `spec_bundles` row is PRESERVED (immutability).
3. The active bundle becomes the new one by the most-recent-`frozen_at`
   rule (Phase-1 spec-bundles-integration plan, D1 T2).
4. The change is a documented mandate event — surfaced in the audit
   trail via the new bundle's `justification` field.

The mandate-change is therefore a one-shot persistence event, not a
search.

### D6 — Inline NN1 disclosure update

The Phase-1 NN1 disclosure block (NN1 plan D4) already pre-names
`lambda` as `MANDATE`-frozen. This plan does NOT modify that block —
the discipline was established in Phase 1. The plan only ratifies the
block's correctness when Phase 2 unlocks.

A NEW comment block alongside any Phase-2 `lambda`-consuming code:

```
NN1 (council §5.3): lambda is mandate-frozen, NEVER Optuna-searched.
A searched lambda against a regime-drifting effective alpha
(council §2.4 / §6.2) is optimization against a non-stationary
objective — the BHY haircut cannot correct it. The system is honestly
ONE tuned parameter — gamma — not two. A future PR adding "lambda" to
OPTUNA_SEARCH_SPACE_KEYS will be rejected by validate_search_space_nn1
at runtime (autotuner.py); this comment is the design-intent record.
```

## Dependencies

- **Blocked by:** Phase 1 — NN1 spec-freeze plan (the enum constants
  and `validate_search_space_nn1` runtime check).
- **Blocked by:** Phase 1 — spec-bundles-integration plan (the
  `get_active_spec_bundle` accessor and the COPY-on-write discipline).
- **Blocked by:** Phase 2 unlock — all four §5.1 preconditions PASS
  before this plan is implemented (M2-evidence, gate-zero tail-data
  audit, latency + bucket arithmetic, powered validation design exists).
- **Soft-coupled to:** Phase 2 gamma-integration plan (the 2D
  search-space surface). gamma searches; lambda does NOT. Both plans
  must ship in the same Phase-2 cycle.
- **Soft-coupled to:** Phase 2 multi-testing-accounting plan (the
  CVaR-derived haircut's T must use the genuine tail-obs count, never
  the simulation path count).

## Golden-fixture tests required

### T1 — `lambda` NOT in search-space (static pin)

Assert `"lambda"` NOT in `OPTUNA_SEARCH_SPACE_KEYS`. (Already in the
NN1 plan's T6 forbidden-list; T1 here re-asserts under the Phase-2
scope and would be a tripwire against a Phase-2 PR that "temporarily
adds lambda for an A/B."

### T2 — `validate_search_space_nn1` rejects `lambda`

Monkeypatch `OPTUNA_SEARCH_SPACE_KEYS` to include `"lambda"`. Assert
`validate_search_space_nn1()` raises with a message mentioning
`lambda` (parallels the NN1 plan's T4). Catches the runtime form.

### T3 — `lambda` MANDATE-discipline in active bundle

Fixture: a Phase-2 `spec_bundles` row with a `lambda` facet whose
`freeze_discipline='MANDATE'`. Assert `validate_nn1_compliance` returns
`(True, [])` — MANDATE is in `NN1_HONEST_DISCIPLINES`. Sanity: catches
a future enum drift that drops MANDATE from the honest set.

### T4 — `lambda` BACKTEST_SELECTION discipline trips violation

Fixture: same as T3 but `freeze_discipline='BACKTEST_SELECTION'`.
Assert `validate_nn1_compliance` returns `(False, [...])` with a
violation row naming `lambda`. The structural enforcement (additive
`N_effective`) then fires regardless of this validation; the
validation surfaces the violation **before** the haircut alone makes
the call.

### T5 — `get_mandated_lambda` fail-loud on missing

Fixture: a `spec_bundles` row WITHOUT a `lambda` facet. Assert
`get_mandated_lambda(active_bundle)` raises a `RuntimeError` (NEVER
returns a default; NEVER silently uses 0.05).

### T6 — `lambda_budget` COPY write-back

Fixture: an `autotune_runs` row written under Phase 2 with
`mandated_lambda = 0.05`. Assert `autotune_runs.lambda_budget == 0.05`,
written by COPY (verified by mutating the spec_bundles row AFTER the
write and asserting `lambda_budget` is unchanged — confirms the value
was snapshotted, not joined at read-time).

### T7 — Mandate-change cutover

Fixture: bundle A with `lambda=0.05`, frozen 1 day ago; bundle B with
`lambda=0.04`, frozen 1 hour ago. Assert `get_active_spec_bundle()`
returns bundle B AND `get_mandated_lambda(active)` returns `0.04`.
Confirms the most-recent-`frozen_at` resolution discipline (Phase-1
spec-bundles-integration plan D1 T2) carries through to lambda.

## Definition of Done

1. T1-T7 RED on a clean implementer commit, GREEN after.
2. `pytest tests/autotuner/` PASSES — full suite, including the
   Phase-1 NN1 tests and the spec-bundles-integration tests.
3. `OPTUNA_SEARCH_SPACE_KEYS` contains NO `"lambda"`.
4. `get_mandated_lambda` lives in `autotuner.py` next to
   `get_active_spec_bundle`-consuming code.
5. The Phase-2 `spec_bundles` registration step seeds a `lambda` facet
   with `freeze_discipline='MANDATE'`, `evidence_source='MANDATE'`,
   `justification` populated.
6. `autotune_runs.lambda_budget` COPIES the mandated value.
7. Commit message: `feat(autotuner): study_name=<TS>__<symphony>,
   lambda mandate-frozen (council §5.3); search space unchanged;
   n_trials=500; objective=CRRA-EU-with-CVaR-shaping mean(U)`.

## Risk callouts

- **"Let me A/B lambda" PR.** The single most likely Phase-2 PR
  shape: a maintainer adds `lambda` to `OPTUNA_SEARCH_SPACE_KEYS` to
  "see if a different budget level works better on the validation
  fold." T1 (static pin) catches the edit; T2
  (`validate_search_space_nn1`) catches the runtime form. **Both
  must ship.**
- **`get_mandated_lambda` defaulting.** A maintainer "for tests" adds a
  `default=0.05` argument. T5's fail-loud catches it. The Phase-1
  defaulting risk is already addressed; this plan extends the
  discipline to `lambda`.
- **Mandate as a euphemism.** A maintainer might mark a P&L-selected
  lambda as `MANDATE` to slip it past the NN1 validation. This is
  social, not technical — but the `justification` field is the
  PR-review surface: a justification like "best validation Sharpe at
  this level" is a self-confessed NN1 violation. PR reviewers must
  scrutinize the justification text.
- **Non-stationary objective subtlety.** The "regime-drifting effective
  alpha" framing is council §6.2's open trade-off. Even after Phase 2
  unlocks, the horizon-convention choice (Family B vs Family C) is the
  operator's call, and the effective alpha is a property of that
  choice. `lambda`'s mandate-freeze pre-supposes the operator picks
  ONE horizon convention and sticks with it; flipping conventions
  mid-deployment is a separate, harder change that requires a fresh
  spec_bundle anyway.
- **One-parameter honesty.** The §5.3 framing — "the system is
  honestly ONE tuned parameter, not two" — is a USER-FACING
  honest-claim about complexity. If this plan ships and a future
  surface (dashboard, docs) advertises "2-parameter EUT+CVaR," that
  marketing is inaccurate. The autotuner does not enforce
  documentation discipline, but the `lambda_budget` column's
  populated-but-not-searched semantics is the technical anchor for
  the honest claim.
- **Phase-2 unlock contingency.** This plan is implemented ONLY if
  Phase 2 unlocks (all four §5.1 preconditions PASS). If Phase 2 does
  not unlock, the plan stays in the feature-plans tree as the
  authoritative record of "what we would have built, why we did not."

## Out of scope

- The Phase-2 CVaR co-signal computation itself — owned by the
  risk-architect lens.
- The horizon-convention choice (Family B vs Family C) — owned by the
  risk-architect lens, escalated to user per §6.2.
- The path-generator design — owned by the risk-architect lens.
- gamma-integration into the 2D search space — separate Phase-2 plan
  (gamma searches; lambda does not).
- The CVaR-derived haircut t-stat's T-source discipline — owned by the
  Phase-2 multi-testing-accounting plan.
- Mandate-change governance / operator workflow — out of scope for the
  autotuner; persisted as a `justification` text but the workflow lives
  outside.
