# Phase 1 — RED Test §8.3: M2 CVaR-on-known-pool + S-3 four-part display contract + H-2 stderr-on-distinct-tail-obs

## Feature
A RED golden-fixture test that **pins** the M2 5% CVaR diagnostic
(Rockafellar-Uryasev general-distribution estimator) on a frozen kNN pool,
AND asserts the **complete S-3 four-part display contract** is present on
the display surface, AND asserts the standard error (S-3 element (a)) is
computed on the **distinct genuine tail-observation count**, NOT on the
resample count (H-2 binding fix).

This is **v3 §8 test 3**.

## Phase
Phase 1 (HARDEN floor — M2).

## Owner agent-type
`quant-test-writer` (RED authoring). Implementation owners: the M2 CVaR
estimator is `risk-engine-specialist`; the display surface is
`flask-dashboard-specialist`; the per-cycle write is `sqlite-specialist` /
`persistence-architect`.

## Source-of-truth references
- `docs/handoff/decision-science-council-synthesis.md` §3.1 (M2 = single-day
  5% CVaR off the existing kNN pool, Rockafellar-Uryasev general-distribution
  estimator), §4 S-3 (four-part display contract — stderr, tail-obs count,
  "diagnostic, not a signal" label, bias warning), §8 test 3.
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md`:
  - §A.2 H-2 (BINDING) — M2's S-3 stderr must use the **distinct genuine
    tail-observation count** (~7-8), NOT the resample count (~5000). Naive
    use of 5000 understates the true estimation error by ~`√(5000/7) ≈ 27×`.
  - §B.6 — surviving residue: operator-optional second window (cvar_5pct_long,
    cvar_n_tail_long) read independently under its own full S-3 contract; NO
    cvar_divergence column allowed.
- Codebase grounding:
  - `math_engine.py:705-833` — `run_monte_carlo`: kNN pool of `neighbor_k`
    days, `simulation_paths` (default 5000) `rng.choice` draws *with
    replacement* from `nearest_day_returns` (size = `neighbor_k`).
  - `math_engine.py:828-829` — the resample is `rng.choice(...,
    size=simulation_paths)`; this is the H-2 trap.
  - `math_engine.py:695-702` — `derive_cycle_mc_seed` SHA-256-of-cycle_id
    seed pattern that M2 must inherit.

## Why
M2 is operator instrumentation, not a Phase-2 stepping-stone (H-4). Its
*only* defense against the load-bearing operator-anchoring harm is the
S-3 four-part display contract. If any of the four elements is absent, M2
is mildly harmful — the operator anchors on a reassuring-looking number
that is systematically low-biased and has no warning attached.

A test that only asserts the CVaR numeric value passes a stripped display
surface. A test that only asserts the display-surface elements passes a
fabricated CVaR value. **Both** must be in one test plan so neither can
silently regress.

H-2's discriminating-power constraint: the stderr must be checked
**numerically**, not just for "presence." A 5000-resample-count stderr and
a 7-distinct-obs stderr differ by ~27×; the fixture is constructed so the
two values are far apart and the test must land near the small-sample value.

## Deliverables

### D1. Fixture file
`tests/fixtures/math/m2_cvar_known_pool.json` — a captured-from-producer
fixture per Gate-1 D-2 provenance. The kNN pool is captured by running
`/api-fixture` against the existing live Alpaca / SPY history fetcher on
a fixed `cycle_id`. The fixture records:

```jsonc
{
  "name": "m2_cvar_known_pool",
  "purpose": "Pins the M2 5% CVaR (Rockafellar-Uryasev general-distribution estimator), asserts S-3 four-part display contract, and asserts stderr is on distinct tail-obs count not resample count.",
  "cycle_id": "<YYYYMMDD_HHMM>",
  "pool_size": <neighbor_k, e.g. 150>,
  "pool_distinct_returns": [<150 distinct daily returns from a captured kNN match>],
  "pool_tail_5pct": {
    "alpha": 0.05,
    "n_tail_distinct": <int, ~7-8 — the count of pool entries below the empirical 5% VaR threshold>,
    "tail_values": [<the n_tail_distinct values>]
  },
  "expected": {
    "var_5pct": <float, R-U VaR — the 5%-quantile of the pool>,
    "cvar_5pct": <float, R-U general-distribution CVaR — closed-form on the discrete pool, with explicit atom handling at the alpha-quantile per R&U 2002>,
    "cvar_5pct_stderr_correct": <float, stderr computed on n_tail_distinct ≈ 7>,
    "cvar_5pct_stderr_wrong_resample": <float, stderr computed on the 5000 resample count; recorded so the negative assertion is auditable>,
    "n_tail_displayed": <int, == n_tail_distinct>
  },
  "discriminating_power": "stderr_correct / stderr_wrong_resample ≈ sqrt(5000/7) ≈ 27; fixture asserts displayed stderr within a tight band around stderr_correct AND outside a wide band around stderr_wrong_resample."
}
```

**Provenance discipline (D-2 non-circular).** `pool_distinct_returns` is
**captured from the existing producer** (the live Alpaca-driven kNN match,
via `/api-fixture`). `var_5pct`, `cvar_5pct`, and `cvar_5pct_stderr_correct`
are **hand-derived from closed forms** — not from running the M2 SUT. The
R-U general-distribution CVaR has a closed form on a discrete sample with
explicit atom handling at the α-quantile; this closed form is the spec
(see eut-cvar-research §2.1 cited at A-1 of the attack rubric). The
derivation steps are recorded in a sibling `m2_cvar_known_pool.derivation.md`.

### D2. Test file
`tests/engine/test_m2_cvar_known_pool.py` (math layer test, under
`tests/engine/` to follow the existing per-layer naming convention).

### D3. Test cases

**Scenario 1 — `test_m2_cvar_matches_rockafellar_uryasev_estimator`** (A-1
positive identity).
- Load the fixture; build the kNN pool exactly as captured.
- Call the M2 estimator (`compute_cvar_5pct(pool, alpha=0.05,
  cycle_id=...)`) — the function name is set by the M2 implementer; this
  plan refers to it generically.
- Assert `result.cvar_pct == pytest.approx(expected.cvar_5pct, rel=1e-9)`.
- Assert `result.var_pct == pytest.approx(expected.var_5pct, rel=1e-9)`.
- Tolerance `1e-9`: pure double-precision arithmetic on a fixed input.
- Discriminating-power: the closed-form R-U value differs from the naive
  "mean of pool entries below VaR" by a documented amount when the
  α-quantile lands on an atom; the fixture's pool is constructed so it
  lands on an atom (one of the `pool_distinct_returns` IS the empirical
  5% quantile). A naive estimator fails this scenario.

**Scenario 2 — `test_m2_displayed_stderr_uses_distinct_tail_obs_count`** (H-2
binding — the discriminating one).
- Load fixture.
- Call the M2 estimator and read `result.stderr` from the returned
  `CVaRAssessment`.
- Assert `result.stderr == pytest.approx(expected.cvar_5pct_stderr_correct,
  rel=0.05)`. The 5% tolerance band is appropriate because the closed-form
  small-sample stderr is itself estimator-defined (sample vs population
  divisor); a 5% band admits the two acceptable conventions and rejects
  the resample-count value.
- Assert `result.stderr != pytest.approx(expected.cvar_5pct_stderr_wrong_resample,
  rel=0.5)` — the 50%-band wrong-form rejection is deliberately loose so
  even a fudged resample-count implementation fails. Discriminating-power
  is preserved because correct/wrong differ by ~27×.
- Assert `result.tail_obs_count == expected.n_tail_displayed`. The
  `tail_obs_count` field is the auditable denominator (council brief; the
  persisted `cvar_n_tail` column).

**Scenario 3 — `test_m2_display_surface_contains_full_s3_four_part_contract`**
(S-3 binding).
- Load fixture; trigger an M2 cycle write; render the diagnostic display
  surface (Flask template). The fixture provides a `cycle_id` and the
  test uses the Flask test client / a rendered-template helper to fetch
  the diagnostic surface.
- Assert **all four** elements present (this is the test that gates S-3):
  - (a) **Stderr / uncertainty band** rendered — assert the rendered HTML
    contains the numeric stderr from `result.stderr` (formatted as text)
    or a clearly-labeled uncertainty band element. The assertion is on
    **presence** AND **value match** — a placeholder "±—" string fails.
  - (b) **Tail-observation count** rendered — assert the text contains
    `n=<n_tail_distinct>` (or equivalent labeled element).
  - (c) **"Diagnostic, not a signal — do not trade on this" label**
    rendered — assert the exact-or-near-exact label text is present. The
    fixture records the canonical wording; tests assert substring match
    on the canonical wording. (Wording is design-system contract per
    `feedback_tests_assert_design_contract_not_values`; the test asserts
    the **design contract**, not a particular CSS color.)
  - (d) **Bias warning** rendered — assert the label "this CVaR estimate
    is a known-low-biased LOWER BOUND on tail severity, not a point
    estimate" (or the canonical wording recorded in the fixture) is
    present. This is the load-bearing element per H-4 — without it M2 is
    mildly harmful.
- Each of the four assertions is its **own** subtest (parametrized) so a
  reviewer can see at a glance which element is missing if a regression
  lands.
- Discriminating-power note: a display that ships with any one element
  missing must fail this scenario. A display that ships all four with
  placeholder values fails (a) and (b) via the value-match check.

**Scenario 4 — `test_m2_displays_n_tail_when_pool_is_data_starved`** (W-H1
guard — single-day tail-data-starvation is itself the warning).
- Load a second sub-fixture (or fixture variant) where the pool yields
  `n_tail_distinct = 3` (a deliberately data-starved variant captured
  from a thin-history early-life-symbol replay).
- Assert the display surface renders `n=3` AND (per H-2) the stderr is
  the small-`n` value, not the resample-count value. This proves M2's
  "you are tail-data-starved right now" warning surface (per H-4).

**Scenario 5 — `test_no_cvar_divergence_column_or_display_value`** (§B.6
surviving residue — negative identity).
- Static-analysis-style test: read the migrations directory and the
  Flask templates; assert **no** column named `cvar_divergence`,
  `regime_recency_weight`, or any signed-divergence persisted-displayable
  value exists. The whitelist of allowed second-window columns is
  exactly `{cvar_5pct_long, cvar_n_tail_long}`.
- Discriminating-power: this scenario fails the moment a developer adds
  the rejected divergence column, structurally enforcing the §B.6
  binding constraint.

### D4. Test naming
- `test_m2_cvar_matches_rockafellar_uryasev_estimator`
- `test_m2_displayed_stderr_uses_distinct_tail_obs_count`
- `test_m2_display_surface_contains_full_s3_four_part_contract`
  (parametrized over the four elements)
- `test_m2_displays_n_tail_when_pool_is_data_starved`
- `test_no_cvar_divergence_column_or_display_value`

## Dependencies
- BLOCKED BY: the M2 schema migration (`023_cvar_diagnostics.sql`) — the
  test surface needs `cvar_5pct`, `cvar_5pct_stderr`, `cvar_n_tail` columns
  (plus the operator-optional second-window columns from §B.6).
- BLOCKED BY: the M2 Flask display surface for the S-3 contract check —
  the test cannot assert HTML elements that do not yet exist.
- BLOCKED BY (soft): `/api-fixture` capture of the kNN pool.
- BLOCKS: M2 GREEN — neither the estimator nor the display ships until
  this RED test exists.

## Golden-fixture tests required
- `tests/fixtures/math/m2_cvar_known_pool.json` (D1).
- `tests/fixtures/math/m2_cvar_known_pool.derivation.md` (closed-form
  derivation of `cvar_5pct`, `var_5pct`, `cvar_5pct_stderr_correct`).
- `tests/fixtures/math/m2_cvar_data_starved_pool.json` (scenario 4
  sub-fixture).

## Definition of Done
- [ ] All three fixture artifacts committed under `tests/fixtures/math/`.
- [ ] Test file committed at `tests/engine/test_m2_cvar_known_pool.py`.
- [ ] All five scenarios RED on `main`.
- [ ] No scenario hardcodes a producer-computed numeric value — every
  numeric assertion reads from the fixture or is derived in a documented
  closed form.
- [ ] Every tolerance has a comment explaining why it is appropriate; the
  H-2 wide-band wrong-resample-rejection is documented with the
  `√(5000/7) ≈ 27` discriminating-power statement.
- [ ] Scenario 5 (no-divergence-column) is exhaustive — it greps the
  migrations directory, the Flask templates, AND `database.py` for any
  forbidden column name.
- [ ] The S-3 contract scenario is **parametrized over the four elements**
  so a reviewer sees the four sub-assertions explicitly in test output.

## Risk callouts
- **R-U atom-handling spec.** The R-U general-distribution CVaR has a
  discrete-sample edge case when the α-quantile lands on an atom (the
  contribution from the atom is weighted by the fractional part of α·N
  minus the count of strictly-below entries). The fixture must record
  the atom-handling choice and the test must match it. This is the
  precise reason A-1's "naive empirical-mean-beyond-quantile" estimator
  fails — and the test is constructed to catch that failure.
- **Display-surface stability.** Asserting on rendered HTML couples this
  test to the Flask template structure. The test asserts on **labeled
  data attributes** (`data-cvar-stderr`, `data-cvar-bias-warning`, etc.)
  rather than visual structure, per
  `feedback_tests_assert_design_contract_not_values`. The implementing
  team must add the data attributes when authoring the template.
- **Captured-pool freshness.** The kNN pool is captured at a specific
  `cycle_id`; if Alpaca's history changes (corporate actions, splits),
  the captured pool stays correct (it is captured) but is no longer
  representative of "today." This is fine — the test is a wiring pin,
  not a live data check; Gate 2 covers live-data quality.
- **Wording drift.** The canonical S-3 wording for labels (c) and (d) is
  recorded in the fixture. If a wording change is desired, the fixture
  changes AND a deliberate test-update commit lands together — the
  wording is part of the binding S-3 contract and is not an
  implementation detail.

## Out of scope
- Validating that M2 is correctly **calibrated** as a CVaR estimator on a
  larger sample (Gate-2 is the diagnostic-quality gate; this is the
  formula-pin / display-pin gate). A unit test cannot discharge
  calibration.
- The Phase-2 multi-day CVaR co-signal (separate plan).
- The signed-divergence quantity itself (rejected — see §B.6; scenario 5
  enforces the rejection structurally).
- Phase-1.5's per-cycle attribution table (separate plan, RED test §8.5).
