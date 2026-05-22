# Phase 1 — RED Test §8.2: M1 wealth-argument derivation (W-H2 + W-H4 floor)

## Feature
A RED golden-fixture test that **pins** the derived wealth argument `W` fed
into the CRRA utility — once W-H2 has been resolved by the M1 design — AND the
`WEALTH_ARG_FLOOR` boundary behavior required by H-1 (new residual W-H4).

This is **v3 §8 test 2**.

## Phase
Phase 1 (HARDEN floor — M1).

## Owner agent-type
`quant-test-writer` (RED authoring); implementation (the wealth-argument
derivation itself + the `WEALTH_ARG_FLOOR` named constant) is owned by
`risk-engine-specialist` and is OUT OF SCOPE for this plan.

## Source-of-truth references
- `docs/handoff/decision-science-council-synthesis.md` §3.9 W-H2 (the wealth
  argument fed to CRRA is unverified — guard-alpha is a *difference*, not a
  wealth ratio), §4 (S-2 binding), §8 test 2.
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §A.1 H-1 —
  binding correctness defect: CRRA `u(W) = W^(1-γ)/(1-γ)` is **unbounded
  below** as `W → 0+` for `γ ≥ 1`; a single fold day at near-zero `W` sends
  `U_i → -∞`, poisons `mean(U)` / `sd(U)`, and the `erf` clamp at
  `autotuner.py:314` cannot rescue a NaN (it only clamps finite extremes).
  Binding fix: `WEALTH_ARG_FLOOR > 0` named module-scope constant applied
  to **input `W`**, **never to output `U`** — flooring `U` directly
  compresses the lower tail of `U`, artificially shrinks `sd(U)`, and
  inflates the t-stat (anti-conservative bias). New residual: W-H4.
- Codebase grounding:
  - `autotuner.py:94-114` — `run_simulation` and the 5 hand-tuned
    loss-aversion multipliers (residual R3 that M1 retires).
  - `math_engine.py:30-54` — `_reject_non_finite` policy: a NaN must not
    silently short-circuit; the M1 input layer must validate.

## Why
M1 feeds CRRA a wealth argument derived from guard-alpha. Guard-alpha is a
*signed daily P&L difference*; CRRA's wealth argument is structurally a
positive wealth-ratio quantity (a growth factor). Until the M1 design
**derives** that conversion explicitly, M1 has an unverified input and any
golden-fixture test of `U` is asserting against unknown ground truth.

This RED test PINS:

1. **The derivation formula** (W-H2 closure): `W = f(guard_alpha, ...)` is
   what the M1 design states, applied to a fixture input series of
   guard-alpha values that yields a closed-form-computable `W` series.
2. **The floor application** (W-H4): when the derived `W` would be `<=
   WEALTH_ARG_FLOOR`, the floor is applied to `W` **before** the CRRA
   transform — and the resulting `U` series is finite, `mean(U)` is finite,
   `sd(U)` is finite, and the downstream t-stat is finite.
3. **The floor side of the contract** (anti-anti-conservatism guard): the
   floor is **not** applied to `U`. The test detects a U-side floor by
   comparing the empirical `sd(U)` of a series containing one floor-hitting
   day against a closed-form reference — a U-side floor would compress
   the lower tail and shrink `sd(U)`; a W-side floor preserves the honest
   monotone CRRA shape of `u(WEALTH_ARG_FLOOR)`.

## Deliverables

### D1. Fixture file
`tests/fixtures/math/m1_wealth_argument_derivation.json` — a single JSON
document holding:

```jsonc
{
  "name": "m1_wealth_argument_derivation",
  "purpose": "Pins the W = f(guard_alpha, ...) derivation and the WEALTH_ARG_FLOOR boundary behavior.",
  "guard_alpha_series": [<T daily guard-alpha values, hand-picked to span the realistic range AND include at least one fold day where the derived W would fall below WEALTH_ARG_FLOOR>],
  "T": <int>,
  "gamma": <float — the frozen M1 gamma; the fixture imports this from the SUT module at fixture-load time and records the imported value here>,
  "WEALTH_ARG_FLOOR": <float — same import-time capture; the fixture does NOT hardcode it>,
  "expected": {
    "W_series_raw": [<T derived wealth-argument values BEFORE floor>],
    "W_series_floored": [<T values AFTER floor>],
    "U_series": [<T CRRA-transformed values from W_series_floored>],
    "any_floor_applied": true,
    "floor_indices": [<indices where the floor was applied>],
    "sd_U_floored_W": <closed-form sd of U from W-side floor>,
    "sd_U_naive_U_side_floor": <closed-form sd of U if the floor were wrongly applied to U; smaller than the W-side value>
  },
  "discriminating_power": "fixture includes at least one W <= WEALTH_ARG_FLOOR; W-side floor yields a larger sd(U) than a U-side floor would; the assertion sd_U_floored_W > sd_U_naive_U_side_floor + tol detects a wrong floor placement."
}
```

**Provenance discipline (D-2 non-circular).** `W_series_raw` is derived from
`guard_alpha_series` by the **fixture-author's hand calculation following the
M1 design doc** — not by calling the SUT. `W_series_floored`, `U_series`,
`sd_U_floored_W`, and `sd_U_naive_U_side_floor` are likewise hand-derived from
closed forms. The fixture is the spec; the SUT must match it.

### D2. Test file
`tests/autotuner/test_m1_wealth_argument_derivation.py`.

### D3. Test cases
**Scenario 1 — `test_wealth_argument_matches_design_formula`** (W-H2 pin).
- Load fixture.
- Derive `W` via the SUT's wealth-argument helper (the function name is
  determined at M1 implementation time and recorded in the M1 plan; this
  test plan refers to it as `derive_wealth_argument(guard_alpha_series)`).
- Assert element-wise: `result[i] == pytest.approx(W_series_raw[i],
  rel=1e-12)` for indices where the raw `W` is **above** the floor.
  Tolerance `1e-12` is appropriate because both sides are pure
  double-precision arithmetic on a fixed input.

**Scenario 2 — `test_wealth_argument_floors_input_W_at_constant`** (W-H4
positive identity).
- Load fixture.
- Derive `W_floored` via the SUT's floor-applying helper.
- Assert element-wise equality against `W_series_floored`.
- Assert: for every index in `floor_indices`, `W_floored[i] ==
  WEALTH_ARG_FLOOR` (exact equality, not approx — the floor produces an
  exact constant).

**Scenario 3 — `test_floor_is_applied_to_W_not_to_U`** (W-H4 anti-conservatism
guard — the discriminating one).
- Load fixture.
- Compute the CRRA `U` series via the SUT.
- Compute the empirical `sd(U)` (sample stdev, `ddof=1`).
- Assert `sd_U == pytest.approx(sd_U_floored_W, rel=1e-9)`.
- Assert `sd_U > sd_U_naive_U_side_floor + tol`, where `tol` is set to half
  the closed-form gap between the two reference values (recorded in the
  fixture). A U-side floor implementation would yield a smaller `sd(U)`
  and fail this assertion.
- Discriminating-power note in docstring: this is the **only** scenario
  that distinguishes a W-side floor from a U-side floor; the fixture is
  constructed so the two sd values differ by a known closed-form amount.

**Scenario 4 — `test_crra_tstat_is_finite_with_floor_hitting_day`** (W-H4
downstream propagation).
- Load fixture (same one).
- Compute the CRRA `U` series and feed it to `compute_crra_eu_tstat` (the
  function from RED test §8.1).
- Assert `math.isfinite(t)`, `math.isfinite(mean(U))`, `math.isfinite(sd(U))`.
- Assert the BHY haircut's `compute_haircut_pvalue(t)` is in the open
  interval `(_HAIRCUT_PVALUE_EPSILON, 1 - _HAIRCUT_PVALUE_EPSILON)` — i.e.
  not silently saturated. This proves the floor short-circuited the NaN
  propagation path H-1 calls out.

**Scenario 5 — `test_no_floor_applied_when_all_W_above_floor`** (negative
identity — guard against an always-on floor).
- Use a sub-fixture (or `guard_alpha_series` mode flag) where every derived
  `W` is comfortably above the floor.
- Assert `derive_floored_wealth_argument(guard_alpha_series) ==
  derive_wealth_argument(guard_alpha_series)` element-wise (no
  modification).
- Discriminating-power: a buggy "always-replace-with-floor" implementation
  fails this scenario.

### D4. Test naming
- `test_wealth_argument_matches_design_formula`
- `test_wealth_argument_floors_input_W_at_constant`
- `test_floor_is_applied_to_W_not_to_U`
- `test_crra_tstat_is_finite_with_floor_hitting_day`
- `test_no_floor_applied_when_all_W_above_floor`

## Dependencies
- BLOCKED BY: the M1 wealth-argument design (the implementer's plan that
  states `W = f(guard_alpha, ...)`) AND the `WEALTH_ARG_FLOOR` constant
  declaration. Until both land, the fixture's `W_series_raw` is unknown.
- BLOCKED BY (soft): RED test §8.1 (CRRA t-stat formula pin) — scenario 4
  depends on `compute_crra_eu_tstat` existing as a function name; can be
  authored in parallel but ordering matters for GREEN.
- BLOCKS: M1 GREEN — the function is not allowed to ship until this RED
  is in place and goes RED on `main`.

## Golden-fixture tests required
- `tests/fixtures/math/m1_wealth_argument_derivation.json` — D1.

## Definition of Done
- [ ] Fixture JSON committed; `W_series_raw`, `W_series_floored`, `U_series`,
  `sd_U_floored_W`, `sd_U_naive_U_side_floor` are all hand-derived from
  closed forms and the derivation steps are recorded in a sibling
  `m1_wealth_argument_derivation.derivation.md` file (so a reviewer can
  audit the closed-form math without running the SUT).
- [ ] Test file committed at `tests/autotuner/test_m1_wealth_argument_derivation.py`.
- [ ] All five scenarios RED on `main` (the function does not yet exist).
- [ ] `gamma` and `WEALTH_ARG_FLOOR` are imported from the SUT module at
  fixture-load time and the recorded fixture values are asserted equal to
  the imported values — preventing silent drift between fixture and SUT.
- [ ] No assertion uses a tolerance without a comment explaining why.
- [ ] Discriminating-power statement present in scenarios 1, 3, and 5.

## Risk callouts
- **W-H2 cannot be tested before it is derived.** This plan is correctly
  blocked on the M1 design doc. The plan exists now so that the moment W-H2
  closes, the RED test is one commit away.
- **`gamma` choice freeze.** If `gamma` is changed after the fixture is
  authored, the fixture's `U_series` becomes stale and the test silently
  drifts. The fixture-load-time import check is the structural guard;
  combined with persistence-architect's immutable `spec_bundles` row,
  `gamma` cannot change without producing a queryable evidence trail.
- **Floor value selection.** The numeric value of `WEALTH_ARG_FLOOR` is the
  M1 implementer's choice (constrained: `> 0`, source-commented per
  no-magic-numbers, set so the resulting CRRA `u(WEALTH_ARG_FLOOR)` is
  representable in IEEE-754 double — i.e. not so small that `W^(1-γ)`
  overflows). The test does not pick the value; it only asserts the value
  is applied consistently.

## Out of scope
- Selecting the wealth-argument derivation formula (M1 design).
- Selecting the floor numeric value (M1 design).
- The CRRA t-stat formula itself — RED test §8.1.
- Empirical validation that the chosen `gamma` matches the operator's risk
  preference (this is the Specification Critic advisor role, not a unit test).
