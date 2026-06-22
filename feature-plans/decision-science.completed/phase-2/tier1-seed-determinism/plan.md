# Feature: tier1_seed — Deterministic Seeding for Phase-2 Pre-Open Path Bank

**Phase / Lane:** Phase 2 — Finalist B, **evidence-gated**. Scaffold now; ships only if the four Phase-2 preconditions pass.
**Owner agent-type:** `risk-engine-specialist` (implementer) + `quant-test-writer` (RED) + `sqlite-specialist` (manifest persistence) + `quant-code-reviewer` (review). Quad for this surface (the persistence surface adds the DB specialist).

## Source-of-truth references

- `docs/handoff/decision-science-council-synthesis.md` §5.3 (*"The Tier-1 bootstrap is seeded deterministically: `tier1_seed = SHA-256(symphony_id ‖ trading_day ‖ spec_bundle_hash)`, persisted in the manifest — **load-bearing for Gate 1 (without it, replay parity cannot pass).**"*), §3.7 (replay-determinism anchor count: Phase 1 = 1; Phase 2 = 5; **`tier1_seed` is one of the 5 Phase-2 anchors**).
- `docs/handoff/council-converged-migration-plan.md` §3.2 row 017 (`017_path_generator.sql` includes `path_bank_manifest` with `tier1_seed` as a load-bearing column), §6 hazard **H4** (the telemetry write helper takes a `live | replay` mode — replay mode raises on write failure; a replay that cannot persist its decision record must fail loud).
- `docs/handoff/council-attack-rubric.md` Family **D** ★ (D-3 **deterministic stochastic output** — the new generator is seeded the way `run_monte_carlo` is — *isolated `np.random.default_rng`, **never the numpy global RNG***), Family **F** ★ (F-2 ★ bit-identical replay under fixed seed; F-4 ★ insufficient-data sentinel mirror), Family **M** ★ (M-2 ★ **persisted derived artifacts are replay-deterministic from a recorded seed** — "an unpersisted / wall-clock seed makes Gate 1 structurally unachievable"; "a Tier-2 read of an absent/stale bank manifest must abstain fail-safe").
- Code anchors: `math_engine.py:80-86` (the `MC_SEED_MODULUS = 2**64` constant and its birthday-bound rationale comment — the precedent `tier1_seed` mirrors); `math_engine.py:695-702` (`derive_cycle_mc_seed` — SHA-256 of cycle_id into 64-bit space; the structural template for `derive_tier1_seed`); `synthetic_history.py` (file-cache precedent for hashed artifacts).

## Why (problem statement)

The Phase-2 forward-path simulator (`simulate_forward_paths`) is stochastic — block bootstrap draws random block lengths from a geometric distribution, then resamples returns by block. **Gate 1 (backtest-replay parity) requires bit-identical replay** under a fixed seed (F-2 ★). Without a deterministically-derived, persisted seed, the path bank produced by Tier 1 today cannot be regenerated tomorrow — replay parity fails by construction. **M-2 ★ is explicit:** *"an unpersisted / wall-clock seed makes Gate 1 (K-1) structurally unachievable."*

The seed must also be:

- **Reproducible across daemon restarts** — the daemon spawns subprocesses at `:00`; a seed derived from process state is non-reproducible.
- **Distinct per (symphony, trading day, spec bundle)** — two symphonies on the same day must produce distinct path banks; the same symphony on two different days must produce distinct banks; a spec-bundle freeze change must produce a distinct bank (NN1 enforcement — a quietly-changed spec must not silently reuse a stale bank).
- **Auditable** — every bank manifest row records the seed; a reviewer can re-derive the seed from `(symphony_id, trading_day, spec_bundle_hash)` and verify.

The recipe from synthesis §5.3 is binding: `tier1_seed = SHA-256(symphony_id ‖ trading_day ‖ spec_bundle_hash)` — the same construction as `derive_cycle_mc_seed`, structurally one layer up (per-(symphony, day, bundle) instead of per-cycle).

## Deliverables

### Code

#### Seed derivation

- **`math_engine.py`** — new pure function:
  ```python
  def derive_tier1_seed(symphony_id: str, trading_day: str, spec_bundle_hash: str) -> int:
      """Deterministic Tier-1 seed for the Phase-2 forward-path simulator.
  
      Same (symphony_id, trading_day, spec_bundle_hash) always produces the same
      seed (SHA-256 digest reduced into the 64-bit MC_SEED_MODULUS space). Safe
      across daemon restarts and across subprocess :00 spawns. This is one of the
      5 Phase-2 replay-determinism anchors (council synthesis §3.7).
  
      The three input components are joined with a "‖" separator (U+2016 DOUBLE
      VERTICAL LINE) to prevent ambiguity between concatenations like
      ("ab"+"c") vs ("a"+"bc"). The separator is a named module constant.
      """
      blob = f"{symphony_id}{_TIER1_SEED_SEPARATOR}{trading_day}{_TIER1_SEED_SEPARATOR}{spec_bundle_hash}"
      return int(hashlib.sha256(blob.encode()).hexdigest(), 16) % MC_SEED_MODULUS
  ```
- Module-scope named constants (no-magic-numbers):
  - `_TIER1_SEED_SEPARATOR = "‖"` (the literal "‖" character). Source comment: separator prevents ambiguity between concatenated string components; matches the synthesis §5.3 verbatim.
- The function reuses `MC_SEED_MODULUS = 2**64` (already named at `math_engine.py:86`) — preserves the existing 64-bit seed-space rationale.

#### RNG isolation

- Wherever `tier1_seed` is consumed (in `simulate_forward_paths` and its calibration helpers), the RNG is `np.random.default_rng(tier1_seed)` — **never** the numpy global RNG (D-3 ★ kill).
- A regression test scans the Phase-2 code paths and asserts NO `np.random.seed(...)` or `numpy.random.choice(...)` (module-level) call appears — only `default_rng(tier1_seed).choice(...)` or equivalent.

#### Manifest persistence

- **`path_bank_manifest`** state-DB table (from migration `017_path_generator.sql`, deferred — sibling Phase-2 plan):
  - `regime_fingerprint` (the regime key + classifier feature snapshot hash).
  - `bank_file_path` (the `.npy` file location).
  - `bank_sha256` (content hash of the `.npy` file — verified on Tier-2 read).
  - `tier1_seed` (the integer seed; **this column is the load-bearing audit anchor**).
  - `symphony_id`, `trading_day`, `spec_bundle_id` (the three inputs to `derive_tier1_seed` — for audit reconstruction).
  - `built_at_utc`, `superseded_at_utc`.
- The manifest write is routed through the **H4 telemetry helper** in **`live` mode** during Tier-1 batch execution, and in **`replay` mode** during replay runs. A replay-mode write failure RAISES (a replay that cannot persist its decision record is broken and must fail loud — H4 binding).

#### Tier-2 read — fail-safe abstention (M-2 ★)

- The per-cycle path-bank read:
  1. Looks up the manifest row for the current `(symphony_id, trading_day, spec_bundle_hash)`.
  2. If no row found → **abstain fail-safe**: `simulate_forward_paths` is treated as unavailable; the CVaR co-signal is `cvar_assessment_available=False`; the cosignal state machine returns `(0, 0, False, True)` (inactive); the protective stop still fires on its own.
  3. If row found but `bank_sha256` mismatches the `.npy` file content → **abstain fail-safe** AND log an integrity error.
  4. If row found AND hash matches → load the `.npy` paths array and proceed.
- A test forces each of (no manifest row) and (hash mismatch) and asserts the cosignal becomes inactive without disabling the safety floor.

#### Replay verification

- On replay, the harness re-derives `tier1_seed` from `(symphony_id, trading_day, spec_bundle_hash)` and verifies it matches the manifest row's recorded value. A mismatch fails Gate 1 loud — the harness halts and reports.
- The replay also regenerates the `.npy` bank from the seed and verifies the SHA-256 matches. A regeneration mismatch is a Gate-1 failure (M-2 ★).

### Tests (RED before GREEN)

| Test | What must exist before GREEN |
|---|---|
| **Determinism (F-2 ★)** | `derive_tier1_seed("symphA", "2026-05-22", "hashX")` returns the same integer on every call, every process, every daemon restart. |
| **Distinctness** | Distinct symphonies on the same day produce distinct seeds; same symphony on different days produce distinct seeds; same symphony, same day, different spec-bundle hashes produce distinct seeds. (3 sub-cases.) |
| **Component-separator ambiguity prevention** | `derive_tier1_seed("ab", "c", "x")` ≠ `derive_tier1_seed("a", "bc", "x")` — the `_TIER1_SEED_SEPARATOR` test. |
| **Output domain** | Returned value is in `[0, 2**64)`. |
| **D-3 ★ RNG isolation** | A scan over Phase-2 code asserts no `np.random.seed(...)` or `numpy.random` module-level call; all randomness goes through `np.random.default_rng(tier1_seed)`. |
| **M-2 ★ manifest persistence** | A Tier-1 batch run writes a manifest row with the correct `tier1_seed`, `bank_sha256`, and audit columns. Recomputing `derive_tier1_seed` from the audit columns matches the recorded seed. |
| **M-2 ★ hash mismatch → abstain fail-safe** | Corrupt the `.npy` file; Tier-2 read detects the SHA mismatch; cosignal becomes inactive; protective stop still fires (F-4 ★ mirror). |
| **M-2 ★ no manifest row → abstain fail-safe** | Delete the manifest row; Tier-2 read returns no bank; cosignal becomes inactive. |
| **M-2 ★ wall-clock seed prohibited** | A regression test asserts `derive_tier1_seed` does NOT call `time.time()`, `datetime.now()`, `random.random()`, or any non-deterministic source. The seed is a pure function of its three string inputs. |
| **H4 live | replay write helper** | A live-mode manifest write that fails (e.g. disk full) is swallowed (continues the cycle); a replay-mode write failure raises. |
| **Replay regeneration parity (Gate 1)** | Given a recorded manifest row, the replay re-derives the seed, re-runs `simulate_forward_paths`, and produces a `.npy` with the same `bank_sha256`. A mismatch fails the test. |
| **NaN/Inf closure (A-2 ★)** | The function accepts only strings; passing a non-string raises TypeError at entry; the strings themselves are not validated for content (UTF-8 encoding is the implicit contract). |
| **No global state** | A property-based test (Hypothesis): 1000 random `(symphony_id, trading_day, spec_bundle_hash)` triples; each derived twice; assert bit-identical. |

### Documentation

- Module-level docstring on `derive_tier1_seed` quoting synthesis §5.3 verbatim: *"`tier1_seed = SHA-256(symphony_id ‖ trading_day ‖ spec_bundle_hash)`, persisted in the manifest — load-bearing for Gate 1."*
- `_TIER1_SEED_SEPARATOR` source comment explaining the ambiguity-prevention rationale.
- `path_bank_manifest.tier1_seed` schema documentation in `017_path_generator.sql` comments.
- The PR commit message states: *"`tier1_seed` derived deterministically per synthesis §5.3; persisted in `path_bank_manifest`; verified on every replay; M-2 ★ binding."*

## Dependencies

- **Blocks:** `phase-2/simulate-forward-paths/plan.md` (consumes `tier1_seed`).
- **Blocks:** `phase-2/cvar-cosignal-hysteresis-trigger/plan.md` (consumes the `cvar_assessment_available` boolean produced by Tier-2 read, which depends on this seed pipeline).
- **Blocked by:** Phase-1 `spec_bundles` migration (15) — `spec_bundle_hash` is a column from that table.
- **Blocked by:** the deferred `017_path_generator.sql` migration (which is itself blocked by the Phase-2 entry gate).
- **Blocked by:** the four Phase-2 preconditions (synthesis §5.1).
- **Soft dependency:** the H4 telemetry helper (live|replay mode) from the Phase-1 persistence work.

## Definition of Done

- All RED tests above land first; GREEN: every test passes.
- Full-tree pytest with HEAD SHA + count + zero errors.
- `derive_tier1_seed` is a pure function — no I/O, no global state, no wall-clock.
- `_TIER1_SEED_SEPARATOR` named module constant with source comment.
- The function reuses `MC_SEED_MODULUS` (already named at `math_engine.py:86`).
- D-3 ★ RNG isolation scan passes — NO global numpy RNG calls in Phase-2 code.
- M-2 ★ persistence verified — manifest row recorded with `tier1_seed`, `bank_sha256`, audit columns; replay regenerates bit-identical bank.
- M-2 ★ abstain-fail-safe verified — missing manifest row OR hash mismatch → cosignal inactive; protective stop unaffected.
- H4 telemetry helper routing verified — live swallows, replay raises.
- A replay-regeneration parity test runs end-to-end: capture a real Tier-1 batch run, store the manifest, regenerate the bank from the recorded seed, assert bit-identical.
- The four Phase-2 preconditions have been authorized as PASS in writing.

## Risk callouts / hazards

- **M-2 ★ (LOAD-BEARING for Gate 1).** Without persisted seed + content hash, replay parity is structurally unachievable. The single most important hazard in this plan.
- **F-2 ★ (replay determinism).** `derive_tier1_seed` is pure; same inputs → same output, every process, every machine.
- **D-3 ★ (no global RNG).** The RNG-isolation scan test enforces it. A future contributor introducing `np.random.choice(...)` (module-level) breaks Gate 1 silently — the scan catches it.
- **F-4 ★ + M-2 ★ (abstain fail-safe).** Missing manifest OR hash mismatch → cosignal inactive; **protective stop unaffected**. The new core NEVER disables the safety floor.
- **H4 (live | replay write semantics).** Live swallows (telemetry failure must not fail a live cycle); replay raises (a replay that cannot persist is broken). The two modes are structural, not optional.
- **NN1 (★ load-bearing).** `spec_bundle_hash` is a component of the seed — a quietly-changed spec produces a quietly-changed seed produces a quietly-distinct path bank. The audit columns (`symphony_id`, `trading_day`, `spec_bundle_id`) make the change visible to a reviewer.
- **Separator ambiguity.** `"‖"` (the literal "‖") is a non-alphanumeric Unicode character extremely unlikely to appear in a `symphony_id` or `trading_day` string. The test asserts the ambiguity-prevention property; a future contributor changing the separator to `"_"` would break the test if any input contained a `"_"`.
- **64-bit seed space.** Reuses `MC_SEED_MODULUS = 2**64`; the birthday-bound rationale at `math_engine.py:80-86` applies (negligible collision rate).
- **Two-DB boundary (E-2 ★).** `path_bank_manifest` lives in the state DB. The `.npy` file cache is on-disk separately.
- **`is_live` explicit (F-1 ★).** Pure function; no path to a broker call.
- **Anchor count.** Per synthesis §3.7: Phase 1 = 1 anchor; Phase 2 = 5 anchors. `tier1_seed` is one of the 5. The other 4 — seed derivation for the calibration step, history fingerprint, spec bundle hash, hysteresis-state snapshot — are sibling concerns (not all in this plan, but all governed by the M-2 ★ principle: persisted and replay-deterministic).

## Out of scope

- A wall-clock-based seed. Forbidden by M-2 ★.
- A process-state-based seed (PID, thread ID). Forbidden by M-2 ★.
- Reseeding numpy global RNG. Forbidden by D-3 ★.
- A bank that is NOT content-hashed. Forbidden by M-2 ★.
- A `NOT NULL` constraint without the immutable-hashed-record discipline. The manifest table is a fresh CREATE with `NOT NULL` on `tier1_seed` and `bank_sha256` (safe because the table is fresh; H2 migration-plan rationale).
- Cross-DB joins. State-DB only.
- The `simulate_forward_paths` function itself. Sibling plan.
- The CVaR estimator. Sibling plan (M2 Phase-1 + Phase-2 cosignal extension).
- The cosignal state machine. Sibling plan.
- The `.npy` file format choice / compression. A small implementation detail; the test surface only requires bit-identical content and a SHA-256 hash.
- LLM-authored advisor commentary on seed-derivation choices. NN1 binding: the seed recipe is mandate-frozen.
