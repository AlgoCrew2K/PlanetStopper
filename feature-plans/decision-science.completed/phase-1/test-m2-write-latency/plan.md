# Phase 1 — H-3: M2 per-cycle write latency benchmark

## Feature
A benchmark test that asserts the M2 `cvar_diagnostics` INSERT, routed
through persistence-architect's `live|replay`-mode telemetry helper,
fits within the 1-minute cycle budget at the realistic live-symphony
count — and that the path matches the established `record_shadow_observation`
sibling pattern.

## Phase
Phase 1.

## Owner agent-type
`quant-test-writer` (benchmark RED authoring). Implementation owners:
`persistence-architect` (the H4 helper), `sqlite-specialist` (the
migration), `risk-engine-specialist` (the M2 call site).

## Source-of-truth references
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §A.3
  H-3 (BINDING) — M2's per-cycle write is "zero decision impact,
  non-zero non-blocking I/O cost." Route through the H4 helper (live
  swallows on failure, replay raises). Benchmark obligation.
- `docs/handoff/decision-science-council-synthesis.md` §3.7 — the
  H4 helper is part of persistence-architect's converged migration plan.
- Project `.claude/CLAUDE.md` — architecture constraint 1: no blocking
  I/O on the per-cycle execution path. 1-minute cadence.
- Codebase grounding:
  - `database.py:1147-1194` — `record_shadow_observation`: the
    established per-cycle telemetry sibling, with a self-opened
    connection, swallowed exception in live mode, off the
    `save_state` transaction.

## Why
H-3 reframes M2's previously-"zero impact" framing as "zero **decision**
impact, non-zero non-blocking I/O cost." The reframing is binding;
without the benchmark obligation, the wording fix is hollow. A latent
M2 write that crowds out the 1-minute cycle budget at portfolio scale
silently breaks architecture constraint 1.

## Deliverables

### D1. Benchmark test file
`tests/engine/test_m2_write_latency_benchmark.py`.

### D2. Test cases

**Scenario 1 — `test_m2_write_uses_h4_telemetry_helper`** (routing pin).
- Patch the H4 helper (`persistence.telemetry.record_telemetry` or
  the name set by the persistence-architect plan); call the M2 cycle.
- Assert the helper was called exactly once per cycle with `mode='live'`
  (live cycles) or `mode='replay'` (replay cycles) and the
  `cvar_diagnostics` payload as positional args.
- Discriminating-power: a direct `INSERT INTO cvar_diagnostics` call
  bypassing the helper fails this assertion. A double-write (helper
  + direct INSERT) fails the exactly-once count.

**Scenario 2 — `test_m2_live_mode_swallows_write_failure_does_not_fail_cycle`**
(H4 contract — live mode).
- Patch the underlying SQLite connection to raise `OperationalError`
  on INSERT.
- Run a live-mode M2 cycle (via the helper).
- Assert the cycle returned normally (no exception propagated).
- Assert an error was logged (the helper logs the swallowed exception).
- Discriminating-power: catches a developer who routes through
  the helper but uses `mode='replay'` (which raises) on the live path.

**Scenario 3 — `test_m2_replay_mode_raises_write_failure`** (H4 contract
— replay mode).
- Patch the SQLite connection to raise.
- Run a replay-mode M2 cycle.
- Assert the exception propagates (does NOT swallow). Replay must fail
  loudly so Gate-1 parity does not silently mask a telemetry-write bug.
- Discriminating-power: catches a developer who copies the live-mode
  swallow into replay, which would hide a real schema mismatch.

**Scenario 4 — `test_m2_write_p99_latency_under_budget_at_realistic_portfolio_size`**
(the benchmark itself).
- Compute the per-symphony per-cycle write budget. The 1-minute cadence
  is 60 s; the M2 write must consume a bounded fraction of that even at
  the realistic portfolio size. The plan **does not hardcode the
  fraction or the symphony count** — both come from a fixture
  (`tests/fixtures/benchmark/m2_write_budget.json`) recording:
  - `realistic_symphony_count`: derived from the production state-DB
    fixture (a `/db-inspect` capture of `bot_state.symphonies`).
  - `cycle_budget_ms`: from project config / constant (the 1-minute
    cadence minus the established headroom budget for the rest of
    the cycle, sourced from a calibration captured in a separate
    benchmark exercise — NOT invented by the test).
  - `p99_latency_ms_target`: derived as `cycle_budget_ms /
    realistic_symphony_count` — the worst-case per-symphony share.
- Run the M2 write N times (N = 1000 or so) on a real in-memory SQLite
  connection (via the same migration sequence the production DB uses).
- Compute the p99 latency from the timings.
- Assert `p99_latency_ms < fixture.p99_latency_ms_target`.
- Discriminating-power: a developer who lands an unindexed WHERE
  clause or an N+1 query on the M2 write fails this scenario.

**Scenario 5 — `test_m2_write_does_not_run_inside_save_state_transaction`**
(architecture constraint 1 — the load-bearing one).
- Use an instrumented SQLite connection wrapper that records every
  `BEGIN` / `COMMIT` and the call-stack at each.
- Run a full simulated cycle including `save_state` and the M2 write.
- Assert the M2 INSERT is **outside** the `save_state` transaction —
  i.e., its `BEGIN`/`COMMIT` pair does not nest inside `save_state`'s.
- Discriminating-power: catches the most dangerous failure mode — an
  M2 write that becomes part of `save_state`'s transaction, where a
  telemetry-write failure would fail the cycle's state save (the
  exact regression H-3's H4 helper exists to prevent).

### D3. Test naming
- `test_m2_write_uses_h4_telemetry_helper`
- `test_m2_live_mode_swallows_write_failure_does_not_fail_cycle`
- `test_m2_replay_mode_raises_write_failure`
- `test_m2_write_p99_latency_under_budget_at_realistic_portfolio_size`
- `test_m2_write_does_not_run_inside_save_state_transaction`

## Dependencies
- BLOCKED BY: the H4 telemetry helper (persistence-architect's plan).
- BLOCKED BY: migration `023_cvar_diagnostics.sql`.
- BLOCKED BY: the M2 estimator call site.

## Golden-fixture tests required
- `tests/fixtures/benchmark/m2_write_budget.json` — recording
  `realistic_symphony_count`, `cycle_budget_ms`,
  `p99_latency_ms_target`. The values are sourced from production /
  the established benchmark exercise, NOT invented.

## Definition of Done
- [ ] Test file committed.
- [ ] Five scenarios RED on `main`.
- [ ] The benchmark fixture is captured-from-producer (production
  symphony count via `/db-inspect`; cycle budget from project config).
- [ ] Scenario 4 uses an in-memory SQLite to avoid file-system flake;
  the test does NOT touch the production DB.
- [ ] Scenario 5's transaction-nesting check uses an `sqlite3`
  connection-factory wrapper (deterministic), not a process-wide
  monitor.
- [ ] The benchmark is marked `@pytest.mark.slow` if it exceeds 5
  seconds — per quant-test-writer rule 6. It is NOT marked
  `@pytest.mark.live` because it touches no API.

## Risk callouts
- **Benchmark flake on shared CI.** The p99 latency is sensitive to
  CI noise. The fixture target must include CI-noise headroom; the
  test re-runs N times and reports p99 (not a single mean). Alternative:
  budget asserts `mean + 3 * std < target`, which is more robust to
  occasional CI hiccups.
- **In-memory vs disk SQLite.** In-memory is faster than disk; a
  benchmark passing in-memory does not guarantee disk. Scenario 4 should
  use **both** modes — in-memory for the discriminating-power check,
  disk (a tmp_path DB with WAL mode matching production) for the
  realistic measure. The disk run is the load-bearing measure.
- **Cycle budget figure.** `cycle_budget_ms` is conservative — it
  includes only the per-cycle work the engine does AFTER M1/M2 lands.
  The exact figure is settable by the persistence-architect or
  risk-engine-specialist; this plan only asserts the fixture-loaded
  value is honored.

## Out of scope
- M2 numerical correctness (Test §8.3, §8.4).
- The H4 helper's own unit tests — those live with the helper.
- Phase-2 path-bank write latency (Phase-2 plan).
- A live production benchmark — that is a Gate-2 measurement, not a
  unit test.
