# Phase 1.5 — RED Test §8.5: S-1 two-stage parity gate + per-cycle attribution table

## Feature
A RED golden-fixture test that gates M3 (Phase 1.5) — the re-derivation of
the time-squeeze decay curve (`math_engine.py:88-94`) and the VWAP
System-A HWM gate (`math_engine.py:601-606`) — via a **two-stage parity
gate** with a **committed per-cycle attribution table**.

This is **v3 §8 test 5** and the verifiability spec for binding condition
**S-1**.

## Phase
Phase 1.5 (HARDEN fast-follow — M3).

## Owner agent-type
`quant-test-writer` (RED authoring); implementation: `risk-engine-specialist`
(the two re-derived curves), `quant-test-writer` (the attribution table
helper).

## Source-of-truth references
- `docs/handoff/decision-science-council-synthesis.md` §3.1 (M3 = re-derive
  R1 time-squeeze + R2 VWAP System-A; NOT in Phase-1 floor), §4 S-1
  (BINDING — two-stage parity gate), §8 test 5.
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §A.5
  H-5 — Phase-1 floor removes R3 only; R1/R2 are M3-and-Phase-1.5 work
  contingent on S-1 shipping.
- `docs/handoff/council-attack-rubric.md` K-1 (BINDING) — a prose summary
  fails K-1; the attribution table passes it.
- Codebase grounding:
  - `math_engine.py:88-94` — time-squeeze decay curve (R1, has no
    literature provenance per code self-flag).
  - `math_engine.py:601-606` — VWAP System-A HWM gate (R2, same self-flag).
  - `tests/engine/` — the existing golden-fixture pattern M3 must extend.

## Why
M3 is a genuine **live-exit-logic change**. R1 and R2 sit on the per-cycle
decision path; changing their values changes decisions. v3 §4 S-1 is
explicit that "explained divergence" as prose fails K-1; only a
per-cycle attribution table — where every divergent cycle is individually
attributed to a specific re-derived curve value, each divergence in the
intended direction — passes the gate.

This is a verification spec for an **artifact** (the attribution table),
not for a number. The test is the gatekeeper: until the artifact exists,
M3 cannot ship.

## Deliverables

### D1. Stage 1 fixture — the pre-M3 frozen reference
`tests/fixtures/parity/m3_stage1_pre_m3_frozen_reference.parquet`
(or `.jsonl` — single-row-per-cycle, schema versioned).

Provenance: **captured-from-producer** by running the pre-M3 engine in
replay across a committed reference cycle list, recording the
decision-content columns of `decisions` table for each cycle. The
reference cycle list is itself committed at
`tests/fixtures/parity/m3_reference_cycle_list.json` and is a fixed
~125-day window with no overlap with any later Gate-2 shadow window
(K-3 — confirmation data is not recycled).

### D2. Stage 2 fixture — the attribution table schema
`tests/fixtures/parity/m3_stage2_attribution_table.schema.json` — the
JSON schema for the per-cycle attribution table the M3 implementer
**must commit alongside the M3 code**:

```jsonc
{
  "title": "m3_stage2_attribution_table",
  "type": "array",
  "items": {
    "type": "object",
    "required": ["cycle_id", "divergent_field", "pre_m3_value", "post_m3_value", "attributed_curve", "attributed_curve_input", "attributed_curve_pre_value", "attributed_curve_post_value", "intended_direction_holds", "attribution_note"],
    "properties": {
      "cycle_id": {"type": "string"},
      "divergent_field": {"type": "string", "description": "the decision-record column that diverges"},
      "pre_m3_value": {"type": ["number","string","boolean","null"]},
      "post_m3_value": {"type": ["number","string","boolean","null"]},
      "attributed_curve": {"type": "string", "enum": ["time_squeeze_decay", "vwap_system_a_hwm_gate"]},
      "attributed_curve_input": {"type": "object", "description": "the cycle-specific input the curve was evaluated at"},
      "attributed_curve_pre_value": {"type": "number"},
      "attributed_curve_post_value": {"type": "number"},
      "intended_direction_holds": {"type": "boolean", "description": "TRUE iff the divergence direction is the one the M3 re-derivation rationale predicted"},
      "attribution_note": {"type": "string"}
    }
  }
}
```

### D3. Test file
`tests/engine/test_m3_two_stage_parity_gate.py`.

### D4. Test cases

**Scenario 1 — `test_stage1_pre_m3_replay_is_bit_identical_to_frozen_reference`**
(harness proof — the load-bearing precondition of S-1).
- Run the pre-M3 engine in replay over `m3_reference_cycle_list.json`.
- Assert the produced decision records are **bit-identical** to
  `m3_stage1_pre_m3_frozen_reference` on decision-content columns
  (per H-8 A3: exclude `id`, `ts_utc`).
- Discriminating-power: this scenario MUST pass before scenario 2 runs.
  If stage 1 fails, the harness itself is broken — any "explained
  divergence" of post-M3 vs pre-M3 is then attributable to harness
  noise, not to M3, and S-1 is structurally unprovable.
- The scenario is independent of M3 itself — it is the gate's
  precondition test.

**Scenario 2 — `test_stage2_post_m3_replay_diverges_only_in_attributed_cycles`**
(S-1 the gate itself).
- Run the post-M3 engine in replay over the same cycle list.
- Compute the per-cycle diff vs `m3_stage1_pre_m3_frozen_reference`.
- Load the committed attribution table at
  `docs/handoff/m3_attribution_table.jsonl` (the M3 implementer must
  commit this — it is part of the M3 deliverable).
- Assert: for every divergent cycle in the diff, **exactly one** row
  exists in the attribution table, keyed by `cycle_id` and
  `divergent_field`.
- Assert: for every row in the attribution table, the
  `(pre_m3_value, post_m3_value)` matches the cycle's actual divergence.
- Assert: every row's `intended_direction_holds` field is `True`. A
  row with `False` is a divergence the M3 design did NOT predict — an
  unintended consequence, S-1 FAILS.
- Discriminating-power: a prose-only "explained divergence" document
  cannot satisfy this scenario — the test reads structured rows and
  enforces 1:1 mapping to actual divergent cycles.

**Scenario 3 — `test_attribution_table_validates_against_schema`** (D2
schema enforcement).
- Load `docs/handoff/m3_attribution_table.jsonl`.
- Validate every row against
  `tests/fixtures/parity/m3_stage2_attribution_table.schema.json` using
  a JSON-schema validator (jsonschema).
- Discriminating-power: a fragmented or partially-filled attribution
  table (e.g., missing `attributed_curve_post_value` on some rows) fails
  this scenario.

**Scenario 4 — `test_attribution_table_only_cites_m3_curves`** (scope
guard — S-1 attribution discipline).
- Read the attribution table; assert every `attributed_curve` value is in
  the enum `{"time_squeeze_decay", "vwap_system_a_hwm_gate"}`.
- Discriminating-power: catches a developer who "attributed" a
  divergence to a curve M3 was not supposed to touch (a sign of
  unintended scope creep).

**Scenario 5 — `test_post_m3_replay_becomes_new_committed_frozen_reference`**
(S-1 closeout — the post-M3 output replaces the pre-M3 reference).
- After scenario 2 PASS, the M3 commit must contain a NEW reference
  file `tests/fixtures/parity/m3_stage2_post_m3_frozen_reference.parquet`
  identical to the post-M3 replay output. Assert the file exists, is
  schema-conformant, and a third replay of the post-M3 engine matches
  it bit-identically.
- Discriminating-power: this is the S-1 commitment that future engine
  changes parity-check against the post-M3 baseline, not the pre-M3
  baseline. Without it, S-1 is half-done.

### D5. Test naming
- `test_stage1_pre_m3_replay_is_bit_identical_to_frozen_reference`
- `test_stage2_post_m3_replay_diverges_only_in_attributed_cycles`
- `test_attribution_table_validates_against_schema`
- `test_attribution_table_only_cites_m3_curves`
- `test_post_m3_replay_becomes_new_committed_frozen_reference`

## Dependencies
- BLOCKED BY: Phase 1 GREEN (M1 + M2 ship); the engine being parity-checked
  is post-Phase-1, pre-M3.
- BLOCKED BY: the Gate-1 parity helper (task #9) — scenarios 1 and 5
  reuse it.
- BLOCKS: M3 GREEN cycle. M3 may not ship until S-1 PASSES end to end.

## Golden-fixture tests required
- `tests/fixtures/parity/m3_reference_cycle_list.json` — the committed
  cycle list.
- `tests/fixtures/parity/m3_stage1_pre_m3_frozen_reference.parquet`.
- `tests/fixtures/parity/m3_stage2_attribution_table.schema.json`.
- `tests/fixtures/parity/m3_stage2_post_m3_frozen_reference.parquet`
  (committed by the M3 cycle as scenario 5's deliverable).
- The attribution table itself, committed at
  `docs/handoff/m3_attribution_table.jsonl` (the M3 implementer's
  deliverable).

## Definition of Done
- [ ] All five scenarios RED on `main`.
- [ ] Reference cycle list committed and immutable post-commit.
- [ ] Stage 1 pre-M3 frozen reference committed (captured BEFORE the
  M3 cycle opens).
- [ ] JSON-schema for the attribution table committed (D2 deliverable).
- [ ] The scenario 2 test uses `cycle_id`-keyed 1:1 matching against
  the attribution table — NOT a count-only "everything is attributed"
  check (a count-only check can be gamed with a single catch-all row).
- [ ] Scenario 5 PASS is the green-light for replacing the parity
  baseline; nothing else in the project may replace it.

## Risk callouts
- **Reference cycle list immutability.** If the reference cycle list is
  ever edited mid-cycle to dodge a divergence, S-1 becomes a lie. The
  cycle list lives in `tests/fixtures/parity/` and is content-hashed in
  the M3 commit message. Reviewers reject any commit that edits the
  cycle list together with the M3 code.
- **"In the intended direction" is a developer assertion.** Scenario 2
  asserts the field is `True`, but the field is implementer-set. The
  M3 design doc MUST state, per re-derived curve, what "intended
  direction" means (e.g., the new time-squeeze curve produces *earlier*
  stop tightening at higher vol). Reviewer's job: cross-check the
  design doc's stated direction against the attribution-table rows in
  spot checks. The test cannot catch a deliberately-mislabeled `True`,
  but it catches the structural failure of an unmarked or `False` row.
- **K-3 confirmation-data discipline.** The reference cycle list must
  not overlap the Gate-2 shadow window. The fixture documents the
  date range and the test asserts it via a sibling check.
- **Replay-noise sources.** If anything in the engine reads a wall clock
  or the global RNG, scenario 1 fails non-deterministically. M2's
  determinism (RED test §8.4) is the prerequisite that makes S-1
  achievable; M1 is offline-deterministic by construction.

## Out of scope
- The M3 design itself — what the new time-squeeze curve and VWAP gate
  formulas are. That is `risk-engine-specialist`'s job.
- Phase-2 parity (5 anchors — separate plan).
- Validating that the re-derived curves are *correct* in any empirical
  sense — this gate is about ATTRIBUTION discipline, not curve-validation.
  Curve validation is the M3 design doc's burden.
