# Engine audit — Live-vs-replay determinism stack audit

## Feature
A cross-cutting audit identifying every site in `math_engine.py`,
`alpha_bot_execution.py`, `autotuner.py`, and the daemon scheduler
(`app.py`) that could introduce nondeterminism into the per-cycle
decision record — and confirms each site is either deterministic by
construction or seeded/sorted/timestamped via `cycle_id`.

Produces a per-site disposition table that becomes a regression
spec — any future change that introduces a new nondeterminism source
must update the table or fail the audit.

## Phase
Engine audit (cross-cutting, pre-Gate-1).

## Owner agent-type
`quant-test-writer` (audit authoring). Implementation of any
remediation is owned by the relevant specialist.

## Source-of-truth references
- `docs/handoff/council-attack-rubric.md` F-2 (★) — bit-identical
  under fixed seed; isolated RNG; no global state; no wall-clock
  inside the math.
- `docs/handoff/council-attack-rubric.md` M-2 (★) — persisted derived
  artifacts replay-deterministic from a recorded seed.
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md`
  §A.8 H-8 A3 — Gate-1 parity column policy.
- Codebase grounding:
  - `math_engine.py:695-702` — `derive_cycle_mc_seed` SHA-256 seed.
  - `math_engine.py:828` — `np.random.default_rng(seed)` isolated.
  - `math_engine.py:30-54` — `_reject_non_finite` policy.

## Why
Gate 1 is "bit-identical decision record on replay." If a single site
reads a wall-clock, uses the global RNG, iterates an unordered
collection in a numerics-affecting way, or depends on thread
scheduling, Gate 1 silently fails for non-obvious cycles. The
council brief is explicit (F-2, M-2). This audit produces the
**per-site evidence** that Gate 1's promise holds.

## Deliverables

### D1. Site disposition table
`feature-plans/decision-science/engine-audit/audit-live-vs-replay-determinism/output/determinism_sites.md`

| File:line | Site | Nondeterminism source | Disposition | Notes |
|---|---|---|---|---|
| `math_engine.py:828` | MC resample | `default_rng(seed)` | DETERMINISTIC — seed derived via `derive_cycle_mc_seed` | OK |
| `math_engine.py:702` | seed derivation | SHA-256 of `cycle_id` | DETERMINISTIC by construction | OK |
| `alpha_bot_execution.py:?` | cycle timestamp | `datetime.utcnow()` | DETERMINISTIC under replay IFF cycle_id supplies the timestamp | VERIFY |
| `autotuner.py:?` | Optuna sampler | TPE state | DETERMINISTIC under fixed `sampler_seed`; UNDETERMINED across versions | DOCUMENT |
| `database.py:?` | autoincrement `id` | SQLite default | NON-DETERMINISTIC — EXCLUDED from Gate-1 parity (per H-8 A3) | OK |
| `database.py:?` | `ts_utc` | SQLite `CURRENT_TIMESTAMP` | NON-DETERMINISTIC — EXCLUDED from Gate-1 parity (per H-8 A3) | OK |
| `reporting.py:?` | Discord webhook timestamp | `time.time()` | NON-DETERMINISTIC — but outside decision record | OK (out of scope) |
| ... | ... | ... | ... | ... |

Each row records the site, the source of nondeterminism, and one of
five disposition statuses: DETERMINISTIC, SEEDED, EXCLUDED, OK
(out-of-scope), or VERIFY (audit flag — must be reviewed and resolved
before Gate 1).

### D2. Audit script
`tools/audit/determinism_site_audit.py` — AST-based scanner that
finds every call to:
- `time.time`, `time.monotonic`, `datetime.now`, `datetime.utcnow`
- `random.*` (without explicit seed)
- `np.random.*` (the module-level functions, not isolated
  `default_rng` calls — the test asserts `default_rng` IS used)
- `os.urandom`, `secrets.*`
- `uuid.uuid1` (time-based), `uuid.uuid4` (random)
- iteration over `set`, `dict` (pre-3.7), or `frozenset` in
  numerics-affecting positions
- threading / multiprocessing primitives in the decision path

For each, classifies via the disposition table.

### D3. Determinism regression test
`tests/engine/test_no_nondeterminism_in_decision_path.py` — runs the
audit script and asserts every site in the decision path
(`math_engine.py` + `alpha_bot_execution.py`'s `run_cycle`-equivalent +
`autotuner.py`'s replay path) is on the disposition table with a
non-VERIFY status. A site with VERIFY blocks Gate 1.

## Test cases

**Scenario 1 — `test_audit_finds_global_np_random_use`**
- Construct a synthetic module that calls `np.random.choice(...)`
  (the module-level, not isolated).
- Run audit; assert it flags the site.

**Scenario 2 — `test_audit_does_not_flag_isolated_default_rng`**
- Construct a synthetic module that calls
  `rng = np.random.default_rng(seed); rng.choice(...)`.
- Run audit; assert it does NOT flag — this is the safe pattern.

**Scenario 3 — `test_audit_flags_wallclock_in_decision_path`**
- Construct a synthetic decision function that calls `time.time()`.
- Run audit; assert flag.

**Scenario 4 — `test_audit_excludes_reporting_and_logging_paths`**
- Construct a synthetic reporting function calling `time.time()`.
- Run audit; assert NOT flagged — out-of-scope for Gate 1 (which only
  cares about the decision record).
- The audit's scope is declared in the script (a list of
  decision-path modules); other modules are out-of-scope by design.

**Scenario 5 — `test_audit_disposition_table_covers_every_flagged_site`**
- Run audit on the real codebase.
- Assert every flagged site has a row in the disposition table with
  status != VERIFY.
- Discriminating-power: a new flagged site without a disposition row
  fails this scenario — the developer must either eliminate the
  nondeterminism or add a disposition entry explaining why it's safe.

**Scenario 6 — `test_audit_output_is_deterministic`**
- Run audit twice on a frozen tree.
- Assert byte-identical output.

**Scenario 7 — `test_decision_path_uses_seeded_rng_only`** (positive
identity — the load-bearing one).
- Audit asserts every RNG use in `math_engine.py`'s decision path is
  via an isolated `default_rng(seed)` call, with the seed derived
  from `cycle_id`.
- Discriminating-power: catches any new RNG-using function that
  forgets to seed.

## Dependencies
- BLOCKED BY (soft): the coverage-gap audit (task #69) and the
  property-test audit (task #70) — this audit benefits from their
  inventories but doesn't strictly depend on them.
- BLOCKS: Gate-1 backtest-replay parity (task #9). Gate 1 cannot
  pass without this audit's site table being complete and all
  statuses != VERIFY.

## Golden-fixture tests required
- Synthetic-module fixtures for the audit's self-tests
  (`tests/fixtures/audit/synthetic_determinism_modules/`).

## Definition of Done
- [ ] Disposition table committed.
- [ ] Audit script committed.
- [ ] All seven self-test scenarios PASS.
- [ ] Regression test
  `tests/engine/test_no_nondeterminism_in_decision_path.py`
  committed; PASSES on `main` (every flagged site has a non-VERIFY
  disposition).
- [ ] The disposition table is a living document — any new commit
  touching the decision path is required to update the table or
  justify why no update is needed.

## Risk callouts
- **Replay vs live divergence in supplied inputs.** Even if every
  decision-path site is deterministic, replay can drift from live if
  the input data (kNN pool, MC history) is reconstructed differently.
  This audit covers the **decision code**; the **input pipeline**
  determinism is a separate concern (`synthetic_history.py` is the
  load-bearing module — but it is covered by other tests, not by
  this audit).
- **Optuna sampler state.** Optuna's TPE sampler is deterministic
  under a fixed seed but is NOT guaranteed bit-identical across
  Optuna versions. The disposition table records this and flags the
  Optuna version as a frozen spec facet (a content-hashed dependency
  in the spec_bundle).
- **Thread scheduling.** The daemon has multi-threaded components
  (Flask routes, the scheduler). The decision path itself must be
  single-threaded; the audit asserts no `threading.Thread` /
  `multiprocessing.Process` instantiation in the decision-path
  modules.

## Out of scope
- The input data pipeline's determinism (`synthetic_history.py`) —
  separate concern.
- Reporting / Discord / dashboard nondeterminism — out of Gate-1
  scope.
- Optuna sampler internals — frozen by version pin, not tested here.
- Live-vs-replay parity for the broker layer — `is_live` discipline,
  separate plan.
