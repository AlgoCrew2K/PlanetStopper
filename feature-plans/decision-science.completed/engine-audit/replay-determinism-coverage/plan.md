# Plan — Engine-audit: replay-determinism anchor coverage (full stack)

**Feature:** A post-Phase-1 audit verifying the **replay-determinism
anchor count** is honest across the full engine stack — not just the
single Phase-1 M2 CVaR anchor. The synthesis §3.7 claims "Phase 1 = 1
anchor; Phase 2 = 5"; this audit verifies the Phase-1 claim and pre-
declares the Phase-2 anchor enumeration.

**Phase:** Engine-audit (post-Phase-1).

**Owner agent-type:** `sqlite-specialist` (the persistence side),
cross-reviewed by `quant-test-writer` (the test side) and
`risk-engine-specialist` (the math side — every anchor has an upstream
non-deterministic source).

## Source-of-truth references

- `docs/handoff/decision-science-council-synthesis.md` §3.7 — "The
  replay-determinism anchor count: Phase 1 = **1** anchor (M2's CVaR
  off the `cycle_id`-seeded kNN pool); Phase 2 = 5."
- `docs/handoff/council-converged-migration-plan.md` §8 (replay
  determinism anchors row).
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §A.8
  A3 (Gate-1 parity column-exclusion list).
- Sibling plan: `feature-plans/decision-science/phase-1/replay-
  determinism-anchor/plan.md` (the Phase-1 anchor declaration this
  audit verifies).

## Why

A replay-determinism claim is only as honest as the **count** of
non-deterministic sources it accounts for. The synthesis claims one
Phase-1 anchor; a real audit verifies that every non-deterministic
source the engine touches is either (a) seeded reproducibly from
`cycle_id` (the anchor discipline) or (b) excluded from the Gate-1
parity assertion explicitly. An unaccounted source — a wall-clock, a
global RNG, a hash-randomisation seed, an environment-variable read —
is a silent Gate-1 failure mode.

## Sub-audits

1. **Phase-1 anchor census.** Enumerate every place in `math_engine.py`,
   `alpha_bot_execution.py`, `synthetic_history.py`, and `autotuner.py`
   that consumes randomness. For each, classify:
   - `cycle_id`-seeded (anchor #1 — M2's CVaR via `derive_cycle_mc_seed`)
   - **legitimately excluded** (wall-clock for logging, `id`
     autoincrement) — must appear in `_PARITY_EXCLUDE_COLUMNS`
   - **gap** — uncategorised. A gap is a binding defect.
   Acceptance: zero gaps.
2. **Phase-2 anchor pre-declaration.** The synthesis enumerates five
   Phase-2 anchors:
   - `mc_seed` / per-cycle seed
   - `path_generator_calibrations.history_fingerprint`
   - `path_bank_manifest.tier1_seed`
   - `spec_bundles.bundle_hash` (the spec-bundle identity)
   - `shadow_decisions.hysteresis_snapshot_json` (the hysteresis state
     at decision time)
   Acceptance: each is present in the Phase-2 deferred-migration plans;
   the audit lists the plan + the column.
3. **Hash-randomisation guard.** Python's `PYTHONHASHSEED` defaults to
   randomised, which can perturb iteration order in any dict-based
   computation; if any persisted hash depends on dict iteration, the
   anchor is silently broken. Acceptance: the audit asserts every
   persisted hash (`spec_bundles.bundle_hash`, `path_bank_manifest.
   bank_sha256`, `tier1_seed`) is computed over a canonicalised byte
   stream (sorted keys, fixed encoding), not over a Python `dict`'s
   `repr`.
4. **Global-RNG ban.** No code path under
   `math_engine.py:run_monte_carlo` / Phase-2 `simulate_forward_paths`
   uses `numpy.random` global state or `random` module functions
   directly. All randomness is via `np.random.default_rng(seed)` or
   equivalent seeded generator. Acceptance: grep test rejects bare
   `np.random.choice` / `np.random.normal` / `random.choice` /
   `random.gauss` etc. — only seeded-generator method calls allowed.
5. **Wall-clock isolation.** `datetime.utcnow()` / `time.time()` calls
   are in `_PARITY_EXCLUDE_COLUMNS` for any column they populate, and
   they NEVER seed an RNG. Acceptance: grep + the audit enumerates
   every wall-clock call site.
6. **Environment-variable read ban from the determinism-critical
   path.** Reading `os.environ` mid-cycle changes behaviour based on
   non-frozen state. Acceptance: the determinism-critical surface
   (`math_engine.py:run_monte_carlo`,
   `math_engine.py:simulate_forward_paths`,
   `alpha_bot_execution.py:_replay_exit_tick`) reads no environment
   variables; `os.environ` lookups happen at module import or in
   factory functions, never per-cycle.

## Deliverables

1. **`tests/audit/test_replay_determinism_coverage.py`** — one test per
   sub-audit; CI advisory until the lane signs off.
2. **`docs/handoff/replay-determinism-anchor-census-<date>.md`** — the
   captured enumeration of every randomness-consuming site + its
   classification. The output is a table; a future PR that adds a
   randomness consumer updates the table or fails CI.
3. **A `_RANDOMNESS_CONSUMERS` constant** (or a checked-in JSON file)
   that the audit reads — the structural source of truth for "every
   randomness consumer the engine has." A new consumer added to code
   without a corresponding entry fails CI.

## Dependencies

- **Hard-depends on Phase-1 floor + the H4 helper + the replay-
  determinism-anchor plan**.
- **Soft-depends on Phase-2 deferred plans** (only their
  declarations, not their implementations).

## Golden-fixture tests required (RED before GREEN)

1. **Phase-1 anchor count == 1** — the census table has exactly one
   non-excluded randomness consumer with the `cycle_id`-seeded
   classification.
2. **Phase-2 anchor pre-declaration count == 5** — the audit confirms
   each of the five anchors named in the synthesis is plan-referenced.
3. **Hash canonicalisation** — for each persisted hash, the audit
   computes the hash twice in separate Python processes (so
   `PYTHONHASHSEED` differs) and asserts the same value.
4. **Global-RNG grep** — zero matches in determinism-critical files.
5. **Wall-clock excluded** — every wall-clock call site is in
   `_PARITY_EXCLUDE_COLUMNS` for its persisted column.
6. **Env-var read ban** — zero `os.environ` reads in the
   determinism-critical surface.
7. **Two-process hash stability** — equivalent fixture invoked from
   two `subprocess.run` calls yields the same `bundle_hash`,
   `bank_sha256`, `tier1_seed`. This is the §3 sub-audit's
   structural test.

## Definition of Done

- All seven tests pass GREEN on the post-Phase-1 codebase.
- The census artifact is committed.
- The `_RANDOMNESS_CONSUMERS` enumeration matches the test fixture.
- A future PR adding randomness fails CI until the consumer is
  classified.

## Risk callouts

- **Hash randomisation is the most easily-missed source.** A
  developer-tested `bundle_hash` looks deterministic on one
  machine; it can diverge under a fresh `PYTHONHASHSEED` if the
  hashed input is a Python dict. The §3 + §7 tests are the
  structural guard.
- **`numpy.random.choice` without an `rng=` is the second most
  common slip.** The §4 grep is intentionally strict; if a legitimate
  use case arises, the fix is to take an `rng` parameter, not to
  exempt the call.
- **The census is one-shot, not a recurring deliverable.** The
  audit's recurring obligation is the `_RANDOMNESS_CONSUMERS`
  enumeration matching the test fixture; the human-readable census
  document is regenerated on demand.
- **The Phase-2 pre-declaration is an audit obligation today**, even
  though Phase-2 may never unlock. Pre-declaring the anchors now
  catches a deferred-plan defect (e.g., a missing hysteresis snapshot
  anchor) before the Phase-2 implementation effort wastes time on a
  schema that cannot replay.

## Out of scope

- Any source of non-determinism outside the persistence + math +
  execution surfaces (e.g., Discord webhook timestamps, QuickChart
  URLs — these are reporting outputs, not engine state).
- The Phase-2 anchor implementations themselves (the deferred plans).
- The Gate-1 parity assertion itself (lives in the replay harness;
  references `_PARITY_DECISION_COLUMNS` from plan
  `phase-1/replay-determinism-anchor`).
