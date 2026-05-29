# Plan — Shadow-logging pattern for M2 `cvar_diagnostics` per-cycle write (H-3 mitigation)

**Feature:** The execution-path I/O pattern that makes M2's per-cycle
`cvar_diagnostics` write **non-blocking and benchmarked** — the H-3
mitigation from the v3 evaluation.

**Phase:** Phase 1 (HARDEN floor — pairs with `021_cvar_diagnostics` and the
H4 telemetry helper).

**Owner agent-type:** `sqlite-specialist` (the pattern + the benchmark
fixture), `quant-test-writer` (adversarial RED on the benchmark + the
non-blocking property), `quant-code-reviewer`.

## Source-of-truth references

- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §A.3
  (Hole H-3 verbatim — "zero decision impact, **non-zero non-blocking I/O
  cost**"; binding fix (a)/(b)/(c)).
- `docs/handoff/decision-science-council-synthesis.md` §3.1 (M2 — "Zero
  [live impact]. Drives no decision."), §3.7 hazards paragraph H4.
- Project CLAUDE.md architecture constraint 1 — "Engine runs 1-minute
  cadence during market hours — no blocking I/O on the execution path."
- Precedent: `database.py:1147-1194` `record_shadow_observation` (the
  swallowed self-connection per-cycle write the council §A.3 cites as
  proof the budget is met).
- Sibling plan: `feature-plans/decision-science/phase-1/h4-telemetry-helper`
  (the helper this pattern routes through).

## Why

The v3 synthesis framed M2 as "zero impact." The v3 evaluation §A.3
sharpened that to **"zero decision impact, non-zero non-blocking I/O
cost"** — M2 writes one `cvar_diagnostics` row every cycle on a 1-minute
cadence. Two contracts must hold:

1. **Non-blocking** — the write does not delay the next execution-path
   step. The `record_shadow_observation` precedent already meets this;
   M2's write is the same object (self-opened connection, off
   `save_state`, swallowed on fail). This plan documents that the
   precedent is binding.
2. **Benchmarked** — the per-cycle write is measured against the 1-minute
   budget. v3 evaluation §A.3 fix (c): "explicit benchmark obligation —
   M2's writer benchmarked against the minute budget."

This plan is the **explicit benchmark obligation** as a TDD-cycle-sized
deliverable.

## Deliverables

1. **Benchmark fixture — `tests/perf/test_cvar_diagnostics_per_cycle_budget.py`**:
   - Builds a realistic `cvar_diagnostics` row (all eight columns
     populated, including the §B.6 second-window columns).
   - Calls `record_telemetry(table="cvar_diagnostics", columns=row,
     mode="live", cycle_id="bench-cycle-N")` in a 100-iteration loop on
     a real (non-mocked) SQLite WAL-mode DB.
   - Records per-call wall-clock time (`time.perf_counter`).
   - **Asserts** the median per-call latency `< 50 ms` AND the 99th-
     percentile `< 200 ms` on the CI runner. These thresholds match the
     existing `record_shadow_observation` precedent's measured behaviour
     (the council §A.3 implicit benchmark — the precedent is in production).
   - Marked `@pytest.mark.perf` so it can be excluded from the default
     test run (the `test_live_*` exclusion precedent in
     `.claude/CLAUDE.md`'s Known Gotchas table — opt-in via a flag, never
     blocking the default `pytest`).
2. **Non-blocking property test —
   `tests/perf/test_cvar_diagnostics_does_not_block_save_state.py`**:
   - Holds a write-lock on a separate test connection.
   - Calls the M2 writer in `mode="live"`.
   - Asserts the M2 writer returns within a generous budget (e.g., 1
     second) and does **not** raise — the live-mode swallow fires, the
     execution path is not blocked.
3. **WAL-mode confirmation test —
   `tests/database/test_journal_mode_wal_for_cvar_writes.py`**:
   - Opens the state DB; queries `PRAGMA journal_mode`; asserts `wal`.
   - This protects against an inadvertent regression that switches the
     state DB to `delete`-mode journaling — which would lock readers
     during the M2 write, violating the non-blocking contract for the
     dashboard (`get_ro_connection` reader).
4. **A documentation paragraph in the H-3 plan-of-record** — a one-
   paragraph note in this plan's body (here) that links to the
   `record_shadow_observation` precedent as the implementer's reference
   model. No code in `database.py` to add — the helper plan
   (`phase-1/h4-telemetry-helper`) supplies the writer; this plan
   supplies the benchmark and the non-blocking proof.
5. **No new migration file.** This is a measurement plan, not a schema
   plan.

## Dependencies

- **Hard-depends on `021_cvar_diagnostics.sql`** (the table).
- **Hard-depends on the H4 telemetry helper** (the writer).
- **Hard-depends on WAL mode being on** for the state DB (already
  established at `database.py:71`; the §3 test guards it).

## Golden-fixture tests required (RED before GREEN)

1. **Median latency < 50 ms** — the binding budget assertion.
2. **99th percentile latency < 200 ms** — the tail-latency guard against
   CI noise.
3. **Non-blocking under concurrent reader** — a `get_ro_connection()`
   read in a sibling thread does not block the M2 writer past the budget.
4. **Non-blocking under concurrent write-lock** — the M2 writer in
   `mode="live"` swallows the `OperationalError` from a held write-lock
   (see H4-helper plan test 1; this plan asserts the swallow's latency
   profile — the swallow returns within the budget, not after the lock
   times out at the 10-second default).
5. **WAL is on** — `PRAGMA journal_mode` returns `wal`.
6. **The benchmark is reproducible** — running the test twice yields the
   same median to within ±20%. This guards against a flaky CI runner
   masking a regression.

## Definition of Done

- All six tests pass GREEN.
- The benchmark fixture is marked `@pytest.mark.perf` and excluded from
  the default `pytest` run (so a slow CI run does not fail PR merges on
  flaky timing).
- The benchmark is wired into a dedicated CI job (or a `make perf`
  target — implementer's choice) so a regression in the per-cycle write
  latency is caught before merge.
- The implementer file references the `record_shadow_observation`
  precedent (`database.py:1147-1194`) as the model.
- The v3 wording correction lands in any downstream synthesis revision
  (out of scope for this plan to perform; flagged for the doc-writer).

## Risk callouts

- **Benchmark CI variance is real.** A median + 99th-percentile pair is
  the standard mitigation (one stable statistic + one tail-guard).
  Setting only a median threshold lets a tail spike slip through; setting
  only a 99th threshold makes the test noisy.
- **`@pytest.mark.perf` is opt-in, NOT opt-out** — the default `pytest`
  invocation must NOT include this marker, so a slow CI run does not
  block unrelated PR merges. The mark is the structural enforcement.
- **WAL mode is the load-bearing dashboard-reader assumption.** A
  regression that swaps to `delete`-mode would lock dashboard reads
  during every M2 write — the dashboard would freeze for ~50 ms per
  symphony per cycle. The §3 PRAGMA test catches this.
- **The 10-second `sqlite3.connect(timeout=...)` default at
  `database.py:53,60` is the worst-case M2 latency on a held write-lock.**
  10 seconds is **inside** the 1-minute cadence budget; the
  live-mode-swallow returns immediately on the eventual
  `OperationalError`. But: if the lock is held longer than 10s the
  swallow fires and the cycle proceeds without a `cvar_diagnostics` row.
  That is the correct fail-safe — `cvar_5pct IS NULL` for that cycle's
  dashboard read, no false data.
- **DO NOT batch M2 writes.** A "write once per 10 cycles" optimisation
  would lose the per-cycle granularity M2's evidence question needs
  (HOLD-cycle CVaR levels). Each cycle gets one write; the budget proves
  that's affordable.

## Out of scope

- The H4 helper itself (separate plan `phase-1/h4-telemetry-helper`).
- The S-3 four-part display contract on the dashboard
  (`flask-dashboard-specialist`).
- The CVaR computation (`math_engine.py`; `risk-engine-specialist`).
- Phase-2 `shadow_decisions` writes (heavier per-cycle write; will
  re-bench under the deferred plan `phase-2/025-shadow-decisions`).
