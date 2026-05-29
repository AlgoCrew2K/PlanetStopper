# Phase 1 — Gate 1: backtest-replay parity test (K-1)

## Feature
The Gate-1 verifiability spec — a named, committed bit-identical
backtest-replay parity test asserting the full Guard-Alpha decision
record matches a frozen reference, with the column-exclusion list per
H-8 A3 fixture-recorded (not in-test).

## Phase
Phase 1.

## Owner agent-type
`quant-test-writer` (RED authoring). Implementation: the parity helper
is `persistence-architect`'s + `risk-engine-specialist`'s joint
deliverable; the test wires it up.

## Source-of-truth references
- `docs/handoff/decision-science-council-synthesis.md` §3.8 Gate 1 —
  "the strongest possible version: M2 changes no decision → a replay is
  bit-identical to the reference Guard-Alpha sequence by construction;
  M1 is offline and deterministic."
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §A.8
  A3 (BINDING) — the Gate-1 column-exclusion list must explicitly name
  `id` (autoincrement) and `ts_utc` (wall-clock); any decision-content
  column changes must be in BOTH live AND replay schemas (E-3).
- `docs/handoff/council-attack-rubric.md` K-1, F-2, M-2.
- Project memory:
  - `feedback_no_hardcoded_test_values` — assertions touching
    producer-computed values must derive from the fixture.
  - `feedback_full_suite_means_genuine_full_tree` — a parity claim must
    be a whole-window-replay, never a scoped subset.

## Why
Gate 1 is one of the **two** user-mandated validation gates ("BOTH gates
clean"). It is the verifiable spec for HARDEN's correctness: under
HARDEN, M2 changes no decision (it is a diagnostic), so the new engine's
decision sequence is **by construction** bit-identical to the reference.
If Gate 1 fails, either (a) M2 leaked into a decision (a load-bearing
regression — fail loud), or (b) the test harness drifted (Stage 1 of
S-1 also catches this).

A prose summary ("backtest looked fine") fails K-1 outright; the test
must be the committed artifact.

## Deliverables

### D1. Reference cycle list fixture
`tests/fixtures/parity/gate1_reference_cycle_list.json` — a committed
list of `cycle_id` values spanning a ~125-day window. Same provenance
discipline as the S-1 reference cycle list (no overlap with Gate-2
shadow window — K-3).

### D2. Frozen reference fixture
`tests/fixtures/parity/gate1_pre_hardening_frozen_reference.parquet` —
the **pre-HARDEN** decision records (i.e., the current `main` engine's
decisions) captured by running the engine in replay over the reference
cycle list. This is captured BEFORE HARDEN Phase-1 lands.

### D3. Column policy fixture
`tests/fixtures/parity/gate1_column_policy.json` —

```jsonc
{
  "purpose": "H-8 A3 binding — explicit column-exclusion list for Gate-1 parity.",
  "decision_content_columns": [
    "cycle_id", "symphony_id", "exit_reason",
    "trailing_stop_arm_state", "tp_arm_state", "vwap_break_state",
    "vwap_bleed_state", "mc_prob", "is_protective_exit", ...
  ],
  "explicitly_excluded_columns": ["id", "ts_utc"],
  "post_phase1_added_columns": [
    "cvar_5pct", "cvar_5pct_stderr", "cvar_n_tail",
    "cvar_5pct_long", "cvar_n_tail_long"
  ],
  "post_phase1_added_columns_disposition": "DIAGNOSTIC-ONLY — present in post-Phase-1 schema BUT not parity-compared against the pre-HARDEN frozen reference (the pre-HARDEN reference does not contain them); parity check is a strict-subset on decision_content_columns."
}
```

The `decision_content_columns` list IS the binding policy artifact.
Adding a column to it AFTER the reference is captured re-opens Gate 1.

### D4. Test file
`tests/engine/test_gate1_backtest_replay_parity.py`.

### D5. Test cases

**Scenario 1 — `test_post_hardening_replay_is_bit_identical_on_decision_content_columns`**
(the headline Gate-1 assertion).
- Load the reference cycle list AND the pre-HARDEN frozen reference.
- Run the post-HARDEN-Phase-1 engine in replay over the cycle list.
- For each cycle, assert the produced decision record matches the
  frozen reference on **every column in `decision_content_columns`**.
- The assertion uses a parity helper that:
  - selects only `decision_content_columns` from both rows;
  - explicitly drops `explicitly_excluded_columns` if present;
  - compares element-wise; for floats uses `==` (bit-identical — not
    `approx`; floats produced by deterministic input must be bit-equal,
    and `approx` would silently admit a numerical drift the gate is
    meant to catch).
- Discriminating-power: any new decision-content column that diverges
  by even one ULP fails this scenario.

**Scenario 2 — `test_post_phase1_only_adds_diagnostic_columns_not_decision_content_columns`**
(E-3 + the strict-subset discipline).
- Inspect the post-Phase-1 `decisions` schema.
- Inspect the policy fixture's `post_phase1_added_columns`.
- Assert the schema's new columns are a subset of
  `post_phase1_added_columns` — i.e., HARDEN Phase 1 added **only**
  M2 diagnostic columns to the decision-adjacent surface, never a
  decision-content column.
- Discriminating-power: catches a developer who quietly adds a
  decision-content column to live without updating the column policy
  (E-3 silent-drift failure mode).

**Scenario 3 — `test_parity_helper_rejects_id_and_ts_utc_differences`**
(H-8 A3 binding — load-bearing).
- Build two synthetic decision rows that differ ONLY in `id` and
  `ts_utc`.
- Call the parity helper; assert it returns PASS.
- Mutate the helper to remove `id` from the exclude list; assert it
  now returns FAIL.
- Discriminating-power: directly enforces the H-8 A3 binding fix.

**Scenario 4 — `test_replay_is_cross_process_bit_identical`** (M-2 /
F-2 — the replay-determinism load-bearing scenario).
- Launch the replay in two separate processes; compare their output
  decision records.
- Assert bit-identical on `decision_content_columns`.
- Discriminating-power: same rationale as RED test §8.4 scenario 2 —
  in-process tests miss per-process hidden state.

**Scenario 5 — `test_reference_cycle_list_does_not_overlap_gate2_shadow_window`**
(K-3 confirmation-data discipline).
- Load both the Gate-1 reference cycle list AND the Gate-2 shadow
  window definition (committed in a sibling fixture per the Gate-2 plan).
- Assert the two date ranges are disjoint.
- Discriminating-power: catches inadvertent overlap that would
  re-credit data the gate already consumed.

### D6. Test naming
- `test_post_hardening_replay_is_bit_identical_on_decision_content_columns`
- `test_post_phase1_only_adds_diagnostic_columns_not_decision_content_columns`
- `test_parity_helper_rejects_id_and_ts_utc_differences`
- `test_replay_is_cross_process_bit_identical`
- `test_reference_cycle_list_does_not_overlap_gate2_shadow_window`

## Dependencies
- BLOCKED BY: HARDEN Phase 1 GREEN (M1 + M2 ship).
- BLOCKED BY: capture of the pre-HARDEN frozen reference (must happen
  BEFORE Phase 1 lands on the engine).
- BLOCKS: cycle-complete handoff for HARDEN Phase 1 (this gate must
  PASS).

## Golden-fixture tests required
- `tests/fixtures/parity/gate1_reference_cycle_list.json`.
- `tests/fixtures/parity/gate1_pre_hardening_frozen_reference.parquet`.
- `tests/fixtures/parity/gate1_column_policy.json`.

## Definition of Done
- [ ] All five scenarios RED on `main` (the post-HARDEN engine does
  not yet exist there).
- [ ] Frozen reference captured BEFORE M1 or M2 lands; the
  capture commit is named and content-hashed in the parity-fixture
  commit message.
- [ ] Column policy fixture committed; the
  `decision_content_columns` list is the binding artifact and changes
  to it require a re-capture of the frozen reference.
- [ ] Scenario 1 uses **exact** float equality (no `approx`) — by
  construction, M1 and M2 produce deterministic floats.
- [ ] Scenario 4 cross-process check is permitted to be
  `@pytest.mark.slow` if it exceeds 5 s; otherwise default-suite.
- [ ] Scenario 5 (K-3 disjoint windows) is the structural enforcement
  of confirmation-data hygiene.

## Risk callouts
- **Capture timing.** The pre-HARDEN reference MUST be captured before
  HARDEN code lands. If it is captured from a post-HARDEN engine
  accidentally, the gate becomes trivially-passing (a tautology). The
  capture commit MUST occur on a SHA pre-dating the first HARDEN
  Phase-1 code commit; the parity fixture's commit message records
  the source SHA.
- **`approx` vs `==` for floats.** Per the M1 design, the CRRA-EU
  objective produces deterministic floats given a fixed input. M2's
  CVaR is also deterministic. There is no legitimate float-noise
  source. `approx` would mask exactly the regressions the gate is
  meant to catch.
- **Schema migration ordering.** The post-HARDEN engine's `decisions`
  table has new diagnostic columns. The parity helper filters to the
  policy's `decision_content_columns`; the pre-HARDEN reference does
  not have the new columns, so the helper does a strict-subset compare,
  ignoring columns the reference lacks.
- **Test data volume.** A ~125-day cycle list of `cycle_id` values at
  1-minute cadence is ~32500 cycles. Loading the parquet reference
  must be memory-efficient; the test reads in batches if needed.

## Out of scope
- Live execution / production deployment — Gate 2 covers it.
- Phase-2 parity (5 anchors) — separate Phase-2 plan.
- Curve-correctness validation of M3 — that is Phase-1.5 S-1 (RED
  test §8.5).
- Empirical validation that the engine's decisions are *good* — a
  parity gate is a regression gate, not an out-performance gate.
