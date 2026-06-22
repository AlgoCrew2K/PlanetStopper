# Phase 1 — RED Test §8.1: CRRA t-stat formula pin (S-2)

## Feature
A RED golden-fixture test that **pins** the formula of the new
`compute_crra_eu_tstat(U: Sequence[float])` per-trial significance statistic
for the CRRA-EU autotuner objective, replacing `compute_sortino_tstat` for
that objective.

This is **v3 §8 test 1** and the verifiability spec for binding condition **S-2**.

## Phase
Phase 1 (HARDEN floor — M1).

## Owner agent-type
`quant-test-writer` (RED authoring), with `quant-code-reviewer` reviewing.
Implementation (GREEN) is owned by `risk-engine-specialist` and is OUT OF SCOPE
for this plan.

## Source-of-truth references
- `docs/handoff/decision-science-council-synthesis.md` §2.1 (CRRA-EU objective),
  §4 (S-2 binding condition), §8 test 1.
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §A.1 (H-1
  WEALTH_ARG_FLOOR, new residual W-H4), §A.6 (H-6 inherited serial-correlation
  exposure, new residual W-H5), §A.7 (H-7 — language must say "pins," not
  "validates").
- Codebase grounding:
  - `autotuner.py:289-301` — `compute_sortino_tstat` returning `sortino * sqrt(T)`
    (the *ratio-multiplied-by-√T* form valid for a Sortino, NOT a CRRA mean).
  - `autotuner.py:266-271` — code comment naming the H-6 *category error* the
    Sortino t-stat already corrected once; reusing it for a CRRA mean would
    repeat that category error.
  - `autotuner.py:304-316` — `compute_haircut_pvalue` clamp at `1e-12` — the
    clamp catches a finite-extreme `t`; it cannot rescue a NaN. The fixture
    must therefore prove `t` is finite by construction.
- Constants: `WEALTH_ARG_FLOOR` (named module-scope constant, source-commented
  per project no-magic-numbers rule, applied to the **input wealth argument
  `W`**, never to the output utility `U`). The constant lives under the M1
  implementer's plan; this test plan only asserts its **effect**.

## Why
The CRRA-EU autotuner objective is `mean(u(g_i))` — a *mean*, not a *ratio*.
Its genuine one-sample significance statistic is

    t = mean(U) / (sd(U) / √T)

NOT `effect_size · √T`. Silently reusing `compute_sortino_tstat` would re-commit
the exact H-6 category error the existing code comment already calls out. The
BHY haircut machinery (`benjamini_hochberg_adjust`, `compute_haircut_pvalue`,
Yekutieli c(N) at `autotuner.py:344-345`) is 100% preserved; only the per-trial
statistic changes. A test that only checks a single numeric value cannot
discriminate the right formula from the wrong one — both Sortino-form and
CRRA-form can be made to match a single point. The test must therefore make
the **discriminating power** explicit by asserting **both** of:

1. `t == mean(U) / (sd(U) / √T)` (positive identity),
2. `t != effect_size · √T` (negative identity — wrong-form rejection).

The fixture uses a series for which the two formulae **diverge by construction**
— `sd(U) ≠ 1` — so a Sortino-form implementation cannot accidentally pass.

## Deliverables

### D1. Fixture file
`tests/fixtures/math/crra_tstat_formula_pin.json` — a single JSON document
holding:

```jsonc
{
  "name": "crra_tstat_formula_pin",
  "purpose": "Pins compute_crra_eu_tstat to the one-sample t-stat formula and rejects the H-6 sortino-form category error.",
  "U_series": [<T floats, hand-picked so sd(U) is NOT 1.0; see below>],
  "T": <int>,
  "expected": {
    "mean_U": <float>,
    "sd_U": <float, must NOT equal 1.0 or any value that collapses the two formulae>,
    "t_correct": <float = mean_U / (sd_U / sqrt(T))>,
    "t_sortino_form": <float = mean_U * sqrt(T) — included so the negative assertion is computable independent of the SUT>
  },
  "discriminating_power": "sd(U) chosen so t_correct and t_sortino_form differ by > 5% — a Sortino-form SUT cannot accidentally pass."
}
```

The `U_series` is **synthesized in the fixture from a closed-form recipe** — NOT
captured from the SUT — so provenance is non-circular (D-2). A reference
recipe (recommended): a deterministic arithmetic progression with a closed-form
mean and sample standard deviation, e.g. `U = [a + b*i for i in range(T)]`,
`mean = a + b*(T-1)/2`, `var = b**2 * (T**2 - 1) / 12`. Choose `a, b, T` so
`sd(U) != 1.0`.

### D2. Test file
`tests/autotuner/test_crra_eu_tstat_formula_pin.py` (path under existing
`tests/autotuner/` tree; this is an autotuner-side statistic).

### D3. Test cases
The file holds **four** named scenarios. Each scenario reads the fixture and
asserts independently — no shared mutable state.

**Scenario 1 — `test_crra_eu_tstat_matches_one_sample_t_formula`.**
- Load `U_series`, `T`, `expected` from fixture.
- Call `compute_crra_eu_tstat(U_series)`.
- Assert `result == pytest.approx(expected["t_correct"], rel=1e-9)`.
  Tolerance `1e-9` is appropriate because both sides are pure double-precision
  arithmetic of a fixed input — the only allowable drift is last-bit floating
  rounding; a tolerance larger than `1e-9` would silently admit a wrong
  algebraic rearrangement.

**Scenario 2 — `test_crra_eu_tstat_rejects_sortino_form_category_error` (H-6
guard).**
- Load the same fixture.
- Call `compute_crra_eu_tstat(U_series)`.
- Assert the returned `t` is **NOT** `pytest.approx(expected["t_sortino_form"],
  rel=1e-3)`. The 1e-3 wrong-form tolerance is *deliberately loose* — any
  implementation that "near-matches" Sortino-form, even with a constant fudge
  factor, fails this assertion.
- Discriminating-power note in docstring: this assertion is meaningful **only
  because** the fixture is constructed so `sd(U) ≠ 1.0`. The fixture's
  `discriminating_power` field exists so a future reviewer auditing this test
  can confirm the gap is preserved.

**Scenario 3 — `test_crra_eu_tstat_finite_at_wealth_argument_floor` (W-H4
boundary).**
- Use a second fixture sub-document (or a sibling fixture file
  `crra_tstat_near_floor_wealth.json`) where `U_series` is the CRRA transform
  of a wealth-argument series containing one entry **at exactly**
  `WEALTH_ARG_FLOOR`. The fixture pre-computes `U` from `W` using the same
  `WEALTH_ARG_FLOOR` constant the SUT will import (the SUT does not
  re-derive it).
- Assert:
  - `math.isfinite(result)` is True;
  - `math.isfinite(statistics.stdev(U_series))` is True;
  - the floor was applied to `W`, **not** to `U` — verified by checking that
    the smallest entry in `U_series` equals `crra(WEALTH_ARG_FLOOR, gamma)`
    and is the floor-induced minimum, with `sd(U_series)` matching the
    fixture's `expected.sd_U_near_floor` (which a U-side floor would have
    artificially shrunk).
- Discriminating-power note: a U-side floor would compress `sd(U)` and
  inflate `t`; this scenario is the anti-conservative-bias guard called out
  by H-1.

**Scenario 4 — `test_crra_eu_tstat_returns_zero_on_empty_series` (parity
with `compute_sortino_tstat` empty-input convention at `autotuner.py:299-300`).**
- Call `compute_crra_eu_tstat([])`.
- Assert `result == 0.0`. The fixture documents this is the agreed
  empty-input convention; departing from it would silently change autotuner
  early-iteration behavior.

### D4. Test naming
Per quant-test-writer rule 5 — names describe the scenario, not the function.
The four names above all do this.

## Dependencies
- BLOCKED BY: the M1 wealth-argument derivation (W-H2; team task #2). Until
  that derivation lands, `WEALTH_ARG_FLOOR`'s numeric value is unknown — but
  the formula-pin scenarios 1, 2, and 4 do **not** depend on
  `WEALTH_ARG_FLOOR` and can ship first. Scenario 3 (W-H4 boundary) is the
  only scenario blocked on W-H2.
- BLOCKS: the GREEN implementation of `compute_crra_eu_tstat` and the
  M1 cycle's haircut-preservation test (task #11).

## Golden-fixture tests required
- `tests/fixtures/math/crra_tstat_formula_pin.json` — D1.
- `tests/fixtures/math/crra_tstat_near_floor_wealth.json` — sub-fixture for D3
  scenario 3 (may be embedded in D1 if cleaner).

## Definition of Done
- [ ] Fixture JSON committed under `tests/fixtures/math/`.
- [ ] Test file committed at `tests/autotuner/test_crra_eu_tstat_formula_pin.py`.
- [ ] All four scenarios RED on `main` (the function does not yet exist).
- [ ] No scenario asserts a hardcoded producer value — every numeric assertion
  is derived from the fixture or from a closed-form recipe documented in the
  fixture's `purpose` / docstring.
- [ ] No scenario uses a tolerance without a comment explaining why that
  tolerance is appropriate.
- [ ] No scenario mocks `compute_crra_eu_tstat`'s math (the math is the SUT);
  no scenario shares state across tests via module-level mutables.
- [ ] The test docstrings explicitly state the discriminating-power claim
  for scenarios 1, 2, and 3.
- [ ] Language audit per H-7 — every docstring and comment that refers to the
  test's purpose uses the verb **"pins"** (e.g., "pins the S-2 formula"),
  never "validates" or "verifies." A unit test cannot discharge a methodology
  claim; W-H5 (the inherited serial-correlation exposure) is left explicitly
  out of scope by this test and is owned by the M1 design doc.

## Risk callouts
- **W-H5 inheritance (H-6 disclosure).** This test does **not** address the
  serial-correlation anti-conservatism in `√T` under dependent observations —
  the same exposure carried by the incumbent `compute_sortino_tstat`. The test
  is a wiring pin, not a methodology validator. Closure of W-H5 is a
  separately-documented future workstream (HAC / Newey-West / `T_eff`); at
  HARDEN Phase-1 scale the lag-1 autocorrelation `ρ` is itself unestimable on
  the ~5-day frozen-eval fold.
- **Sub-fixture `gamma` value.** Scenario 3 requires a `gamma` value to compute
  the reference `U_series` from `W`. The fixture must record the `gamma` value
  used and the SUT under test must import the **same** module-scope `gamma`
  constant — otherwise the fixture and SUT can drift silently. A defensive
  check at fixture load time asserts the SUT's imported `gamma` matches the
  fixture's recorded `gamma`.
- **`U_series` length `T` choice.** If `T < 2`, `sd(U)` is undefined; the
  fixture chooses `T >= 5` so `sd(U)` is well-defined and the
  `Bessel-corrected` sample-stdev is not pathological. The fixture's
  `expected.sd_U` is computed with the **sample** stdev (`ddof=1`) — the SUT
  must use the same. This is an explicit choice the SUT-implementer must match.

## Out of scope
- Validating that `√T` is the correct denominator under the data's dependence
  structure. That is W-H5 and is **not** what this test does.
- The choice of `gamma` (M1 design — separate plan, team task #2).
- The choice of utility *family* (CRRA vs CARA vs skew-aware) — a frozen spec
  facet, not a tested value.
- The wealth-argument derivation itself (W-H2 — RED test §8.2, separate plan).
- Integration of `compute_crra_eu_tstat` into the BHY pipeline — task #11.
