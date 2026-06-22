# Phase 2 — tier1_seed deterministic seeding for the path bank

## Feature
A determinism test set for **M-2** — Phase-2's persisted path bank is
re-generatable bit-identically from a recorded seed
`tier1_seed = SHA-256(symphony_id ‖ trading_day ‖ spec_bundle_hash)`,
persisted in `path_bank_manifest`. Without this, Gate-1 replay parity is
structurally unachievable.

## Phase
Phase 2.

## Owner agent-type
`quant-test-writer` (RED authoring). Implementation:
`risk-engine-specialist` (the seed-derivation function),
`persistence-architect` (the manifest schema).

## Source-of-truth references
- `docs/handoff/decision-science-council-synthesis.md` §5.3 —
  `tier1_seed = SHA-256(symphony_id ‖ trading_day ‖ spec_bundle_hash)`,
  persisted in the manifest — load-bearing for Gate 1.
- `docs/handoff/council-attack-rubric.md` M-2 (★ BINDING) —
  persisted derived artifacts are replay-deterministic from a recorded
  seed; unpersisted/wall-clock seed makes K-1 structurally
  unachievable.
- `docs/handoff/council-attack-rubric.md` F-2 — bit-identical replay.
- `docs/handoff/decision-science-council-synthesis.md` §3.7 — Phase 2
  = 5 replay-determinism anchors (vs Phase 1 = 1). tier1_seed is the
  primary anchor for the path bank.
- Codebase grounding:
  - `math_engine.py:695-702` — `derive_cycle_mc_seed`: the sibling
    SHA-256 reduction Phase-2's tier1_seed mirrors.

## Why
Phase-2 adds a pre-simulated path bank — a derived artifact consumed
by the decision core. Without a recorded seed, two replays produce
different banks ⇒ different CVaR values ⇒ different decisions ⇒ Gate 1
FAILS. M-2 makes this structural; this test makes M-2 verifiable.

## Deliverables

### D1. Test file
`tests/engine/test_phase2_tier1_seed_determinism.py`.

### D2. Test cases

**Scenario 1 — `test_tier1_seed_derived_from_documented_recipe`** (M-2
formula pin).
- Construct inputs: `symphony_id = "SYMA"`, `trading_day = "2025-05-22"`,
  `spec_bundle_hash = "<hex digest>"`.
- Call the seed-derivation function.
- Assert the result equals
  `int(hashlib.sha256(("SYMA||2025-05-22||<hex>").encode()).hexdigest(),
  16) % SEED_MODULUS` — the documented recipe.
- The exact concatenation discipline (separator, byte order) is
  recorded in the fixture; the test asserts the **documented** recipe,
  not its own re-derivation.
- Discriminating-power: catches a developer who swaps the
  separator or omits one input.

**Scenario 2 — `test_same_inputs_same_seed`** (invariance).
- Call the function twice with the same inputs.
- Assert equal output.
- Trivial but load-bearing — guards against an implementation that
  uses a wall-clock or process-id.

**Scenario 3 — `test_different_inputs_different_seed`** (separation).
- Call with three input triples differing in one component each
  (different `symphony_id`, different `trading_day`, different
  `spec_bundle_hash`).
- Assert all three seeds are distinct.
- Discriminating-power: catches a buggy reduction that collapses one
  input dimension.

**Scenario 4 — `test_path_bank_regenerated_from_seed_is_bit_identical`**
(M-2 the load-bearing artifact-determinism scenario).
- Generate the path bank with `tier1_seed = S`.
- Persist the manifest (`tier1_seed = S`, manifest_hash = H1).
- In a separate process, read the manifest, re-run the path-bank
  generation with seed `S`, and compute its hash H2.
- Assert H1 == H2 (bit-identical).
- Discriminating-power: catches non-deterministic ordering (e.g.,
  `dict` iteration in 3.6-) or any global-RNG-driven side path.

**Scenario 5 — `test_manifest_records_seed_and_input_components`**
(M-2 audit trail).
- Read the `path_bank_manifest` row after generation.
- Assert it records `tier1_seed`, `symphony_id`, `trading_day`,
  `spec_bundle_hash`, and `manifest_hash`.
- Assert the seed equals the recipe applied to the recorded
  components.
- Discriminating-power: catches a manifest that stores the seed but
  not the inputs (or vice versa), making the audit-trail incomplete.

**Scenario 6 — `test_path_bank_loaded_for_replay_matches_manifest_hash`**
(F-2 replay parity).
- Load the manifest; compute the hash of the loaded path bank.
- Assert it equals the manifest's recorded `manifest_hash`.
- Discriminating-power: catches a stale or corrupted bank file — a
  silent data drift that would falsely PASS Gate 1.

**Scenario 7 — `test_tier1_seed_never_uses_wallclock`** (static AST).
- AST-scan the seed-derivation function module.
- Assert no use of `time.time`, `datetime.now`, `datetime.utcnow`,
  `os.urandom`, `random.random` (without explicit seed), or
  `np.random.seed` in the seed-derivation function body.

### D3. Test naming
- `test_tier1_seed_derived_from_documented_recipe`
- `test_same_inputs_same_seed`
- `test_different_inputs_different_seed`
- `test_path_bank_regenerated_from_seed_is_bit_identical`
- `test_manifest_records_seed_and_input_components`
- `test_path_bank_loaded_for_replay_matches_manifest_hash`
- `test_tier1_seed_never_uses_wallclock`

## Dependencies
- BLOCKED BY: Phase-2 path simulator existing.
- BLOCKED BY: migration `017_path_generator.sql` defining
  `path_bank_manifest`.
- BLOCKS: Phase-2 Gate-1 parity check (Gate 1 cannot pass without
  this determinism).

## Golden-fixture tests required
- `tests/fixtures/seed/tier1_seed_recipe.json` — recording the seed
  recipe (separator string, modulus, hash function) so the test
  asserts the documented recipe.

## Definition of Done
- [ ] Test file committed.
- [ ] All seven scenarios RED on `main`.
- [ ] Scenario 4 (cross-process bit-identical) is the load-bearing
  scenario; if it passes only in-process, the implementation has
  hidden state.
- [ ] Manifest schema asserted to include every input component.

## Risk callouts
- **`spec_bundle_hash` immutability.** Per persistence-architect, the
  spec bundle is content-hashed. If the hash drifts, tier1_seed drifts
  silently. Persistence-architect's plan must enforce content-hash
  immutability; this test only verifies the contract is honored at
  manifest-write time.
- **SEED_MODULUS choice.** A modulus smaller than `2^32` could collide
  across the (symphony, day) space. The fixture records the modulus;
  Phase-2 design picks a `2^63`-class modulus (matching
  `MC_SEED_MODULUS`'s scale at `math_engine.py:695-702`).
- **Cross-process file system.** Scenario 4 / 6 reads a file in a
  second process; on Windows the temp-file lifecycle is fragile. The
  test uses `tmp_path` (pytest's per-test directory) and explicit
  `flush()` before the second process reads.

## Out of scope
- The path simulator's calibration step's determinism — covered
  separately (the Politis-White selector is itself deterministic
  given the return series, so this test does not need to assert it).
- The CVaR-from-path-bank computation's determinism — falls out of
  the path bank being deterministic.
- Phase 1 determinism — RED test §8.4.
