# Phase 1 — H-2 standalone gate: M2 stderr-on-distinct-tail-obs correctness

## Feature
A standalone gate test for the H-2 binding fix — the M2 diagnostic's
standard error (S-3 element (a)) is computed on the **distinct genuine
tail-observation count**, NOT on the resample count. Property-based
extension of §8 test 3 scenario 2 — exercises the assertion against many
pool configurations, not just the one captured pool.

## Phase
Phase 1.

## Owner agent-type
`quant-test-writer` (RED authoring). Implementation: `risk-engine-specialist`.

## Source-of-truth references
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §A.2
  H-2 — naive use of 5000 resample count understates true estimation
  error by `√(5000/7) ≈ 27×`; converts S-3's honesty mechanism into a
  false-precision generator (exactly the comfort element (d) exists to
  prevent).
- `docs/handoff/decision-science-council-synthesis.md` §3.1 S-3 element
  (a), §4.
- `docs/handoff/decision-science-eut-cvar-research-2026-05-22.md` §2.3
  — drawing 50,000 times from a 150-neighbour pool does not add tail
  information beyond the ~7-8 genuine sub-5% neighbour-days.

## Why
RED test §8.3 scenario 2 pins the H-2 fix against **one** captured pool.
H-2 is a discipline that must hold for **every** pool the M2 estimator
sees. A property-based test extends the guarantee from a single-fixture
pin to a class of inputs:

> For any kNN pool with `pool_size ∈ [50, 300]` and any `simulation_paths
> ∈ [1000, 50000]`, the displayed stderr is consistent with the
> small-sample stderr (denominator ≈ `n_tail_distinct`) and inconsistent
> with the resample-count stderr (denominator ≈ `simulation_paths`).

This is a `hypothesis` test (per quant-test-writer rule 2) that draws
synthetic pools and asserts the H-2 contract. Hypothesis is preferred
because it shrinks failing cases to minimal counter-examples and
exercises corner cases the captured-fixture cannot reach (extreme
right-skewed pools, pools with many ties on the α-quantile, very small
distinct-tail counts).

## Deliverables

### D1. Test file
`tests/engine/test_m2_stderr_property.py`.

### D2. Hypothesis strategies
```python
@st.composite
def synthetic_kNN_pool(draw):
    pool_size = draw(st.integers(min_value=50, max_value=300))
    # daily returns in a realistic range (decimals), drawn from a
    # heavy-tailed Student-t-like distribution constructed from
    # hypothesis primitives — NOT from numpy.random (deterministic
    # given the hypothesis seed)
    ...

@st.composite
def simulation_paths(draw):
    return draw(st.integers(min_value=1000, max_value=50000))
```

The strategies produce pools and resample counts; the test calls the M2
estimator with `seed = derive_cycle_mc_seed("<fixed_cycle_id>")` so the
M2-side randomness is deterministic. Hypothesis-side randomness drives
**input** variation; numpy-side randomness inside the SUT is seeded.

### D3. Test cases

**Property 1 — `test_m2_stderr_scales_with_distinct_tail_obs_count_not_resample`**.
- Given a pool and a resample count, compute the M2 estimator.
- Compute the reference small-sample stderr from a closed-form recipe
  using the distinct sub-5% tail-observation count (`n_tail`) — e.g.,
  `stderr ≈ std(tail_values) / sqrt(n_tail)` (or the R-U-specific
  recipe documented in the M2 design).
- Assert `result.stderr == pytest.approx(reference_stderr, rel=0.10)` —
  10% tolerance band because hypothesis-drawn pools have small-`n`
  variance in the closed-form recipe; a band tighter than 10% would
  flake at extreme draws. The discriminating-power claim still holds:
  the wrong (resample-count) value differs by ~`√(5000/7) ≈ 27×`, far
  outside a 10% band.
- Negative identity: `result.stderr != pytest.approx(std(tail_values)
  / sqrt(simulation_paths), rel=0.5)` — wide-band rejection of the
  resample-count form (50%-band still safely separates the two values
  given the 27× gap).

**Property 2 — `test_m2_tail_obs_count_field_matches_distinct_count`**.
- Given a pool, compute the M2 estimator.
- Compute the distinct sub-5% count by hand from the pool.
- Assert `result.tail_obs_count == hand_computed_distinct_count`. Exact
  equality (it's an integer).

**Property 3 — `test_m2_stderr_decreases_monotonically_with_pool_growth`**.
- For a fixed underlying distribution, draw a pool of size `n` and a
  pool of size `2n`. Assert `result_2n.stderr <= result_n.stderr * 1.05`
  (allowing 5% slop for the random component; in expectation the stderr
  shrinks as √n). Property invariant — quant-test-writer rule 2
  monotonicity invariant.
- Discriminating-power: an implementation that uses the resample count
  produces a stderr that is invariant to pool growth (it scales with
  `simulation_paths` only) — fails this property.

**Property 4 — `test_m2_stderr_is_finite_for_any_non_degenerate_pool`**.
- For any pool with `pool_size >= MC_MIN_HISTORY_DAYS + (MC_VOL_WINDOW_DAYS
  - 1)` (the eligibility threshold at `math_engine.py:731-738`), assert
  `math.isfinite(result.stderr) AND result.stderr > 0.0`.
- For pools below the threshold, assert M2 returns the `None` /
  insufficient-history sentinel (F-4) — not an in-band stderr.

### D4. Test naming
- `test_m2_stderr_scales_with_distinct_tail_obs_count_not_resample`
- `test_m2_tail_obs_count_field_matches_distinct_count`
- `test_m2_stderr_decreases_monotonically_with_pool_growth`
- `test_m2_stderr_is_finite_for_any_non_degenerate_pool`

## Dependencies
- BLOCKED BY: the M2 estimator (RED test §8.3) — this property test
  exercises the same SUT.
- BLOCKED BY (soft): `hypothesis` being installed; per quant-test-writer
  rule 2 it is the property-based test library, and is a test-only
  dependency.

## Golden-fixture tests required
None — this is a property-based test; the strategies generate inputs.
No JSON fixtures.

## Definition of Done
- [ ] Test file committed at `tests/engine/test_m2_stderr_property.py`.
- [ ] All four properties RED on `main`.
- [ ] Hypothesis strategies seed-deterministic (hypothesis settings:
  `derandomize=True` OR `database=None` + a fixed seed in `conftest.py`),
  so a CI re-run reproduces the same draws.
- [ ] Every tolerance has a comment explaining why; the 10% band
  recipe-noise rationale is explicit.
- [ ] The discriminating-power statement "wrong stderr ≈ 27× correct
  stderr, so a 50% rejection band has > 20× margin" is in the test
  docstring.
- [ ] Hypothesis settings set `max_examples` to a reasonable count
  (e.g., 50–100) — not 1000, which would make the default suite slow
  per quant-test-writer rule 6 (slow tests are `@pytest.mark.live`).

## Risk callouts
- **Hypothesis non-determinism.** Hypothesis's `database` records failing
  examples for replay. In CI the database may not be writable;
  `derandomize=True` is the safer setting for reproducibility. The
  failing-case shrinking is preserved — only the example exploration
  becomes deterministic.
- **Tolerance flake.** 10% tolerance on a small-sample stderr can flake
  at the most extreme hypothesis draws. Test marks itself
  `@pytest.mark.flaky(reruns=2)` only if measured flake rate > 0.1%
  over a CI baseline; otherwise no flaky-rerun.
- **Closed-form recipe vs estimator recipe.** The reference stderr
  recipe lives in the M2 design doc. If the design states a different
  recipe (e.g., bias-corrected for the R-U atom adjustment), the test
  imports it from a shared spec module — not duplicated in the test.
  This avoids drift between Test §8.3 scenario 2 (single-fixture pin)
  and this property test.

## Out of scope
- Validation of the R-U estimator formula itself — Test §8.3 scenario
  1 covers it.
- The S-3 display surface (bias warning, labels) — Test §8.3 scenarios
  3-4 cover it.
- Property tests for the M2 second-window residue (Phase-2 plan).
