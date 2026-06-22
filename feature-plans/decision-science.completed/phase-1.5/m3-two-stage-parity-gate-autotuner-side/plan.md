# Phase 1.5 — Two-Stage Parity Gate (S-1): Autotuner-Side Support for M3

**Feature:** Provide autotuner-side support for the M3 two-stage parity
gate (binding S-1). The autotuner replay harness must support (a) Stage
1 — pre-M3 engine replays bit-identical to the current frozen reference,
and (b) Stage 2 — post-M3 engine replay enumerates **every divergent
cycle** in a committed per-cycle attribution table. **Strict scope:** only
the autotuner-replay coupling — NOT the math_engine curves themselves.

**Phase:** Phase 1.5 (M3 — fast-follow after Phase 1; binding S-1)

**Owner agent-type:** `optuna-specialist` (drives the replay-harness
side), `risk-engine-specialist` (owns M3's math_engine re-derivation;
out of this plan's scope), `quant-test-writer` (RED on the
attribution-table format).

## Source-of-truth references

- `docs/handoff/decision-science-council-synthesis.md` §3.1 (M3 is
  Phase 1.5, not Phase-1 floor — re-derives the two layers the code
  self-flags: time-squeeze decay `math_engine.py:88-94`, VWAP System-A
  HWM gate `math_engine.py:601-606`), §4 S-1 (binding two-stage parity
  gate; "Explained divergence" as prose fails Gate-1 K-1; a per-cycle
  attribution table passes it).
- `docs/handoff/decision-science-v3-and-divergence-evaluation.md` §A.5
  (H-5 — the Phase-1 floor removes R3 only via M1; R1/R2 removal is
  Phase-1.5 recommendation contingent on M3 shipping under S-1).
- `autotuner.py:437-642` — the replay machinery (`_replay_exit_tick`,
  `_fresh_replay_state`, `replay_exit_sequence`, `_collect_sim_returns`):
  the single per-tick exit core M3 will exercise.
- `autotuner.py:645-695` — `_collect_sim_returns`: produces the per-day
  guard-alpha series the attribution table cross-references against
  cycle-level divergences.
- `tests/autotuner/test_c3_replay_exit_parity.py` (referenced at
  autotuner.py:444) — the existing bit-identical parity test the M3
  Stage-1 reuses as its precedent.

## Why

M3 re-derives two ad-hoc heuristic curves that the code itself flags as
having no literature provenance. A re-derivation is a **live-exit-logic
change** carrying a real test burden. The council's S-1 condition
(binding) makes that burden concrete: a prose summary fails the Gate-1
review (K-1). What passes is **a committed per-cycle attribution table**
naming every divergent cycle and the specific re-derived curve value
that caused the divergence, each divergence in the intended direction.

The autotuner is where this attribution must be assembled because the
autotuner replay is where the cycle-by-cycle trace exists. The
production engine (`alpha_bot_execution.py`) does not produce a replay
trace; the autotuner does. M3's attribution table is therefore an
**autotuner-replay artifact**.

This plan is **strict-scope**: it covers the autotuner-side support
ONLY — the harness, the attribution-table format, the parity-gate
discipline. The `math_engine` curve re-derivations themselves are
owned by the risk-engine-specialist (separate plan in the
risk-architect's domain).

## Deliverables

### D1 — `run_attribution_replay(history_data, params, reference_engine_mode, current_engine_mode)`

A NEW autotuner-side function in a new module `autotuner_parity.py` (a
sibling to `autotuner.py` — keeping the parity machinery testable in
isolation):

```python
def run_attribution_replay(
    history_data: dict,
    params: dict,
    reference_engine_mode: str,  # "pre_m3" | "post_m3"
    current_engine_mode:   str,  # "pre_m3" | "post_m3"
) -> list[CycleAttribution]:
    """Re-run the replay under TWO engine modes and emit the per-cycle
    attribution rows for every cycle where they diverge.

    Stage 1 (binding S-1, sub-stage 1): reference=pre_m3, current=pre_m3.
        EXPECTED: zero divergent cycles. If non-zero, the harness itself
        is broken and no Stage-2 attribution is meaningful.
    Stage 2 (binding S-1, sub-stage 2): reference=pre_m3, current=post_m3.
        EXPECTED: every divergent cycle has an attribution row naming the
        specific re-derived curve value that caused the divergence AND
        the direction of the divergence matches the intended direction
        of the M3 re-derivation (no "unexpectedly more aggressive exit"
        cycles).

    Returns a list of CycleAttribution dicts with: symphony_id, date,
    tick_idx, reference_exit_reason, current_exit_reason,
    reference_curve_value, current_curve_value, curve_name (one of
    "time_squeeze_decay" | "vwap_system_a_hwm" |
    "BOTH" | "OTHER"), direction ("more_protective" |
    "less_protective" | "unexpected"), magnitude (a numeric delta).
    """
```

Constraints:
- Pure with respect to its inputs; deterministic.
- Does NOT modify the persistence layer — purely a replay harness.
- The `reference_engine_mode` flag toggles between the canonical pre-M3
  curves and the new post-M3 curves. This requires `math_engine.py` to
  expose `compute_time_squeeze_decay_v1` (pre-M3) and
  `compute_time_squeeze_decay_v2` (post-M3) — that's the
  risk-engine-specialist's job; this plan asserts the surface needs to
  exist.
- **The harness does NOT silently pick the "current" curve as the
  reference.** A bug in M3's risk-engine plan that swaps the two would
  trivially make every Stage-2 run "pass" because reference and current
  would be identical.

### D2 — `CycleAttribution` typed shape

A `dataclass` (or `TypedDict`) in `autotuner_parity.py`:

```python
@dataclass(frozen=True)
class CycleAttribution:
    symphony_id: str
    date: str
    tick_idx: int
    reference_exit_reason: str | None
    current_exit_reason:   str | None
    reference_curve_value: float
    current_curve_value:   float
    curve_name: str   # "time_squeeze_decay" | "vwap_system_a_hwm" | "BOTH" | "OTHER"
    direction: str    # "more_protective" | "less_protective" | "unexpected"
    magnitude: float
```

Frozen — immutable once constructed; matches the committed-table
expectation.

### D3 — Attribution-table committed format

The Stage-2 output is committed to a NEW directory:
`feature-plans/decision-science/phase-1.5/m3-attribution-tables/`. Each
M3 replay run produces one file:
`<run_timestamp>__m3_stage2_attribution.csv` (or JSON — team's choice;
the format must be human-readable and diffable in a PR review).

The committed file is the **artifact the Phase-1.5 cycle delivers**.
Without it, S-1 is not satisfied (council §4 S-1 — "Explained divergence
as prose fails Gate-1; a per-cycle attribution table passes it").

### D4 — Stage-1 (pre-M3 self-parity) test

A NEW test asserting that under `reference=pre_m3, current=pre_m3`, the
attribution list is **empty** for the full 125-day replay fixture. This
is the harness self-test — if it fails, the M3 attribution cannot be
trusted in Stage 2.

### D5 — Stage-2 attribution direction guard

A NEW test asserting that **every** Stage-2 attribution row has
`direction in {"more_protective", "less_protective"}` — NOT
`"unexpected"`. `"unexpected"` is a tripwire value the harness emits
when the post-M3 curve's effect direction does NOT match the M3
re-derivation's intended direction. A non-empty `"unexpected"` row set
**fails Phase 1.5** — that is the whole point of the attribution.

### D6 — Post-M3 frozen-reference cutover discipline

After Stage 2 passes (zero `"unexpected"` rows; every divergence
attributed), the post-M3 replay output **becomes the new committed
frozen reference**. Per S-1: "the post-M3 output then becomes the new
committed frozen reference."

The cutover discipline:
- the old frozen-reference fixture (used by Phase-1 Gate-1 parity) is
  PRESERVED — committed to
  `tests/fixtures/frozen_reference/pre_m3/` — never deleted.
- the new frozen-reference fixture is committed to
  `tests/fixtures/frozen_reference/post_m3/` and the Gate-1 parity test
  switches to read from there.
- the switch is a single commit, surface-clear at PR review.
- a `tests/fixtures/frozen_reference/CURRENT.md` file is updated to
  point at `post_m3`. A future M4-class re-derivation would extend
  this pattern.

### D7 — Integration-test stage gates

In `tests/autotuner/test_m3_two_stage_parity.py`:
- `test_stage1_harness_self_parity()` — D4.
- `test_stage2_every_divergent_cycle_attributed()` — for every divergent
  cycle in the Stage-2 output, assert a CycleAttribution row exists.
- `test_stage2_no_unexpected_direction()` — D5.
- `test_stage2_committed_table_present()` — assert the committed CSV
  file exists in the m3-attribution-tables directory and matches the
  in-memory list.

## Dependencies

- **Blocked by:** Phase 1 — M1 CRRA-EU objective plan (M1 ships first
  per H-5 framing).
- **Blocks:** Phase 2 entry — M3 must ship cleanly under S-1 before
  Phase 2 enters any evidence-gated unlock.
- **Coupled to (sibling plan):** the risk-engine-specialist's M3 plan
  re-deriving `compute_time_squeeze_decay` and the VWAP System-A HWM
  gate. This plan ASSUMES that plan exposes both `_v1` and `_v2`
  variants for the harness's reference / current toggling.

## Golden-fixture tests required

(All RED-first.)

### T1 — Stage-1 self-parity (D4)

Fixture: the 125-day frozen replay reference (current Phase-1 reference).
Assert `run_attribution_replay(..., reference="pre_m3", current="pre_m3")`
returns `[]` (zero divergent cycles).

### T2 — Stage-2 divergent-cycle enumeration

Fixture: the same 125-day replay reference. Run
`run_attribution_replay(..., reference="pre_m3", current="post_m3")`.
Assert:
- the returned list is non-empty (M3 changes behaviour — that's the
  point);
- every divergent cycle in the replay corresponds to a returned
  CycleAttribution row (no missing rows);
- every CycleAttribution row corresponds to a genuinely divergent cycle
  in the replay (no phantom rows).

### T3 — Direction guard (D5)

Same fixture. Assert no row has `direction == "unexpected"`.

### T4 — Curve-name classification

Assert every row's `curve_name` is in
`{"time_squeeze_decay", "vwap_system_a_hwm", "BOTH", "OTHER"}` AND
`"OTHER"` does NOT appear (an "OTHER" row would mean the divergence is
NOT attributable to either re-derived curve — a harness defect or an
unintended side-effect change in M3).

### T5 — Committed-table determinism

Run the same Stage-2 replay twice (same seed, same inputs). Assert the
committed CSV is byte-identical between runs.

### T6 — Bit-identical post-M3 frozen-reference

After Stage-2 passes, assert the new `post_m3` frozen reference, when
replayed under `reference="post_m3", current="post_m3"`, returns `[]`
— closes the cutover discipline (D6) and confirms the new reference
is self-consistent.

### T7 — Negative pin: silent reference swap

Adversarial: temporarily rename `compute_time_squeeze_decay_v1` to
`compute_time_squeeze_decay_v2`'s implementation (a misconfiguration
where reference and current accidentally collapse). Assert the
HARNESS DETECTS this — either by emitting "unexpected" direction rows
(Stage 2 with reference=current returns the empty list, which would
make Stage 2 falsely pass; the test asserts the harness has a
secondary check that the reference and current modes produce
DIFFERENT results on at least one fixture day).

## Definition of Done

1. T1-T7 RED on a clean implementer commit, GREEN after.
2. `pytest tests/autotuner/test_m3_two_stage_parity.py` PASSES.
3. `pytest tests/autotuner/test_c3_replay_exit_parity.py` STILL PASSES
   — the pre-existing parity test is unmodified.
4. `feature-plans/decision-science/phase-1.5/m3-attribution-tables/<run_timestamp>__m3_stage2_attribution.csv`
   is committed with the per-cycle attribution rows.
5. `tests/fixtures/frozen_reference/post_m3/` is committed; `CURRENT.md`
   updated.
6. M3 risk-engine plan's `_v1` / `_v2` curve surface is in place
   (verified by import success in `autotuner_parity.py`).
7. Commit message: `feat(autotuner): study_name=<TS>__<symphony>,
   M3 two-stage parity gate (S-1) — autotuner_parity harness, committed
   attribution table, post_m3 frozen reference cutover; n_trials=500;
   objective=CRRA-EU mean(U)`.

## Risk callouts

- **The "prose summary is good enough" temptation.** A maintainer might
  argue "we've explained M3 in the design doc; a per-cycle table is
  overkill." S-1 binding makes this not negotiable. The test surface
  enforces what the design doc alone cannot.
- **`"unexpected"` direction rows.** The single most likely failure
  mode: M3's re-derivation introduces a side-effect not anticipated by
  the curve's intended direction (e.g. the new VWAP gate fires
  EARLIER on a class of low-volume days, producing more protective
  cycles in the time bucket the time-squeeze curve was supposed to
  loosen). A non-zero `"unexpected"` set fails Phase 1.5 — the M3
  re-derivation is sent back for revision. This is the **point** of
  the attribution machinery.
- **Frozen-reference fork.** Once `post_m3` becomes the new reference,
  any further re-derivation (a hypothetical M4) must repeat the S-1
  dance. The committed `CURRENT.md` and the fork-not-overwrite
  fixture directory pattern (D6) keep this clean.
- **Harness drift.** If `_replay_exit_tick` itself changes between
  Phase 1 and Phase 1.5 (e.g. a tick-state refactor), the
  pre-M3-vs-post-M3 attribution mixes harness-induced and curve-induced
  divergences. T1 (Stage-1 self-parity) is the guard — if Stage 1 is
  non-empty, the harness has drifted and Stage 2 attribution is not
  trustworthy. **Stage 2 MUST NOT run if Stage 1 is non-empty.**
- **Two-DB cleanliness.** The attribution harness is read-only on the
  state DB (history) and produces a CSV artifact, not a DB write. No
  optimization-DB touch. No cross-join.
- **N_effective interaction (out of scope but flagged).** M3 does NOT
  P&L-tour any spec facet — the re-derivation is by
  literature/calibration (`STYLIZED_FACT` or `CALIBRATION` discipline
  per NN1). It contributes `0` to `S` in the additive accounting. Any
  M3 plan that proposes A/B testing the two curves on validation P&L
  would violate NN1 — coordinate with the risk-engine-specialist plan
  to confirm this is excluded.
- **Live-engine impact.** M3 IS a live-exit-logic change (unlike M1
  which is offline). The autotuner-side harness validates that the
  Phase-1.5 cycle's replay matches the post-M3 production engine's
  behaviour — but live-engine deployment still needs the operator
  sign-off described in the council H6 retention rule.

## Out of scope

- The M3 math_engine curve re-derivations themselves — owned by the
  risk-engine-specialist plan.
- M2 / CVaR diagnostic — owned by the risk-architect lens.
- Phase-2 path-generator changes — Phase 2 only.
- Modifications to `_replay_exit_tick` or `replay_exit_sequence`
  (`autotuner.py:437-642`) — explicit preservation; the harness wraps
  the existing per-tick core and does not modify it.
- Persistence changes — Phase 1.5 introduces NO new migrations; M3 is
  a code-only change in math_engine + the autotuner harness.
- The Gate-2 live-shadow N-weeks-clean validation — that is a
  cycle-runtime artifact, not an autotuner-replay artifact.
