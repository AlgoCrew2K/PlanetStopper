# Phase 1 — RED Test §8.4: M2 one-anchor replay determinism

## Feature
A RED golden-fixture test that asserts the M2 CVaR diagnostic produces
**bit-identical** output across two replays of the same `cycle_id`, with
the diagnostic seed derived from `cycle_id` via the same SHA-256 discipline
as `derive_cycle_mc_seed`.

This is **v3 §8 test 4** and the single replay-determinism anchor under
HARDEN Phase 1 (the council brief — Phase 1 = 1 anchor; Phase 2 = 5).

## Phase
Phase 1 (HARDEN floor — M2).

## Owner agent-type
`quant-test-writer` (RED authoring); implementation: `risk-engine-specialist`
+ `persistence-architect`.

## Source-of-truth references
- `docs/handoff/decision-science-council-synthesis.md` §3.7 (Phase 1 = 1
  replay-determinism anchor — the M2 CVaR off the `cycle_id`-seeded kNN
  pool), §8 test 4.
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §A.8 H-8
  A3 (BINDING) — Gate-1 parity column-exclusion list must explicitly
  exclude `id` (autoincrement) and `ts_utc` (wall-clock).
- `docs/handoff/council-attack-rubric.md` F-2, K-1, M-2.
- Codebase grounding:
  - `math_engine.py:695-702` — `derive_cycle_mc_seed`: SHA-256 of
    `cycle_id` reduced into `MC_SEED_MODULUS`. The M2 estimator MUST
    inherit this discipline (its own seed-derivation function may exist,
    but the cycle-id-keyed property is identical).
  - `math_engine.py:828` — `np.random.default_rng(seed)` isolated; never
    the numpy global RNG. Same discipline binds M2.

## Why
Gate 1 (backtest-replay parity) requires bit-identical decisions across
replays. M2 changes no decision — so M2's contribution to Gate 1 is its
own bit-identical reproducibility on the same `cycle_id`. If M2 ever
re-seeded from a wall-clock, or used the global RNG, or read an unordered
dict whose iteration order varies, Gate 1 is structurally unachievable
(M-2 binding) and the entire HARDEN claim collapses.

This is the *one* anchor in Phase 1. It must be airtight.

## Deliverables

### D1. Fixture file
`tests/fixtures/math/m2_replay_determinism.json` —

```jsonc
{
  "name": "m2_replay_determinism",
  "purpose": "Pins M2 bit-identical reproducibility across replays of the same cycle_id.",
  "cycle_id": "<YYYYMMDD_HHMM, fixed>",
  "captured_kNN_pool": [<the same captured pool as in test 3 — the M2 input is identical>],
  "expected": {
    "cvar_5pct": <float, bit-identical reference value>,
    "cvar_5pct_stderr": <float>,
    "cvar_n_tail": <int>,
    "cvar_5pct_long": <float | null, second-window per §B.6 if adopted>,
    "cvar_n_tail_long": <int | null>
  },
  "parity_excluded_columns": ["id", "ts_utc"],
  "parity_included_columns": [
    "cycle_id",
    "cvar_5pct",
    "cvar_5pct_stderr",
    "cvar_n_tail",
    "cvar_5pct_long",
    "cvar_n_tail_long"
  ]
}
```

The included/excluded column lists are **the fixture-level statement of
the H-8 A3 binding fix** — the lists live in the fixture, not in a
test-internal Python literal, so a reviewer can audit the column policy
without reading test code.

### D2. Test file
`tests/engine/test_m2_replay_determinism.py`.

### D3. Test cases

**Scenario 1 — `test_m2_cycle_seeded_run_is_bit_identical_across_two_invocations`**
(F-2 in-process determinism).
- Load fixture; build the kNN pool.
- Call the M2 estimator twice in the same process with the same `cycle_id`.
- Assert the two `CVaRAssessment` objects are byte-equal on every field
  in `parity_included_columns`. The `id` and `ts_utc` fields are
  **explicitly excluded** from the comparison; the assertion helper
  uses the fixture's `parity_included_columns` list and rejects any
  field not on it (catch-all to flag unintentional new fields).
- Discriminating-power: a buggy implementation that reads from a wall
  clock or the global RNG fails this scenario on a fast machine; a
  buggy implementation that adds a non-deterministic field (a
  timestamp-derived nonce) is caught by the catch-all.

**Scenario 2 — `test_m2_cycle_seeded_run_is_bit_identical_across_processes`**
(F-2 cross-process determinism — Gate 1's actual shape).
- Use `subprocess` to run a small "compute M2 for this cycle_id" Python
  script in **two separate Python processes**. Compare results.
- Assert byte equality on `parity_included_columns`.
- Discriminating-power: an in-process test would silently accept code
  that depends on a per-process random initialization (e.g., a
  numpy global RNG reseeded once per import). A cross-process test
  catches that.

**Scenario 3 — `test_m2_seed_derived_from_cycle_id_via_sha256_modulus`**
(M-2 — the seed itself is the recorded artifact).
- Load fixture's `cycle_id`.
- Read the seed the M2 estimator used (the implementation must expose
  it — e.g., as a field on `CVaRAssessment` or via a logged audit row).
- Assert the seed equals `int(hashlib.sha256(cycle_id.encode()).hexdigest(),
  16) % MC_SEED_MODULUS` — the exact `derive_cycle_mc_seed` recipe at
  `math_engine.py:695-702`. (M2 may have its own seed-derivation function
  with a different `_M2_SEED_MODULUS`; in that case the assertion is
  against M2's documented recipe, with the fixture recording which
  recipe applies. Either way, the recipe is pure, cycle-id-keyed, and
  asserted byte-for-byte.)
- Discriminating-power: a wall-clock or process-id-derived seed fails.

**Scenario 4 — `test_m2_parity_check_excludes_id_and_ts_utc`** (H-8 A3
binding — the negative identity).
- Build two `cvar_diagnostics` rows from the M2 estimator run twice.
  These rows have **different** `id` (autoincrement) and **different**
  `ts_utc` (wall-clock).
- Call the parity-check helper (the Gate-1 parity assertion helper used
  in the broader parity test plan, task #9).
- Assert the parity helper returns PASS — i.e., it correctly **excludes**
  `id` and `ts_utc` from the comparison.
- Mutation-style sub-assertion: temporarily strip `id` from the
  excluded list, re-run the parity helper, assert it now returns FAIL.
  This proves the exclusion is load-bearing, not coincidental.

**Scenario 5 — `test_m2_no_global_rng_or_wallclock_in_hot_path`**
(static-analysis guard — F-2 + M-2).
- Read the M2 implementation module source.
- Assert no occurrence of `np.random.seed`, `np.random.choice` (without
  an explicit `default_rng`), `time.time()`, `datetime.now()`, or
  `datetime.utcnow()` in the M2 estimator function's source range.
- This is an `ast`-based check (parse the module, find the function
  node, inspect call expressions), not a regex grep — so renamed
  imports are still caught.
- Discriminating-power: a developer who later writes
  `from time import time` and uses it in the hot path fails this
  scenario.

### D4. Test naming
- `test_m2_cycle_seeded_run_is_bit_identical_across_two_invocations`
- `test_m2_cycle_seeded_run_is_bit_identical_across_processes`
- `test_m2_seed_derived_from_cycle_id_via_sha256_modulus`
- `test_m2_parity_check_excludes_id_and_ts_utc`
- `test_m2_no_global_rng_or_wallclock_in_hot_path`

## Dependencies
- BLOCKED BY: M2 estimator existing (the function may be RED itself —
  this test still goes RED on `main` because the function does not exist
  there yet).
- BLOCKED BY: the Gate-1 parity helper (task #9 plan) — scenario 4
  depends on calling it. The parity helper can be authored against this
  test's expected interface; the two are co-designed.
- BLOCKS: Gate-1 backtest-replay parity (task #9) — without M2
  determinism, Gate 1 cannot pass.

## Golden-fixture tests required
- `tests/fixtures/math/m2_replay_determinism.json` (D1).

## Definition of Done
- [ ] Fixture committed.
- [ ] Test file committed at `tests/engine/test_m2_replay_determinism.py`.
- [ ] All five scenarios RED on `main`.
- [ ] No assertion compares wall-clock-dependent fields.
- [ ] Scenario 2 (cross-process) is the load-bearing scenario; if
  scenario 1 passes but scenario 2 fails, the implementation has hidden
  per-process state and must be reworked before GREEN.
- [ ] Scenario 5 (static-analysis) uses AST inspection, not regex.

## Risk callouts
- **Cross-process test cost.** Scenario 2 launches subprocesses; on
  Windows the launch is non-trivial (~200ms). The test marks itself as
  `slow` only if it exceeds 5 seconds total; otherwise it runs in the
  default suite. It is **never** marked `@pytest.mark.live` — it touches
  no API.
- **`MC_SEED_MODULUS` reuse.** If M2 uses the same modulus as
  `run_monte_carlo`, the two seeds collide on the same `cycle_id`. This
  is **fine** — they are isolated `default_rng` instances, so identical
  seeds across two independent estimators produce no correlation issue.
  But the fixture must record which modulus M2 uses, and scenario 3
  asserts the documented recipe.
- **Hidden state via memoization.** If the M2 estimator caches a
  per-`cycle_id` result on first call, scenario 1 (in-process repeat)
  trivially passes. Scenario 2 (cross-process) is the guard against
  this false PASS — a memoization cache does not survive a process boundary.
- **Adopted second-window columns.** If §B.6 second-window columns
  ship, scenarios 1, 2, and 4 must include them in
  `parity_included_columns`. The fixture's list is the single source of
  truth; updates land in one place.

## Out of scope
- Replay parity for the full decision record — this test only covers M2.
  The full Gate-1 parity is task #9 (separate plan).
- Replay parity for Phase 2 path-bank artifacts (5-anchor Phase-2
  determinism — separate plan, task #66 tier1_seed).
- Live-vs-replay safety (`is_live` flag) — F-1, covered by other plans.
