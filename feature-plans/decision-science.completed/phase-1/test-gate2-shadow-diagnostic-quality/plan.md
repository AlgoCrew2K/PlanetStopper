# Phase 1 — Gate 2: live shadow N-weeks-clean (diagnostic-quality criteria)

## Feature
The Gate-2 verifiability spec — pre-registered diagnostic-quality
acceptance criteria for M2's live shadow run. **NOT an ES-calibration
backtest** (per v3 §3.8: M2 drives no trade, so it does not need one).

## Phase
Phase 1.

## Owner agent-type
`quant-test-writer` (RED authoring of the acceptance-criteria checker).
Implementation: `flask-dashboard-specialist` (display-surface
instrumentation), `persistence-architect` (shadow log persistence).

## Source-of-truth references
- `docs/handoff/decision-science-council-synthesis.md` §3.8 Gate 2 —
  "M2's diagnostic runs in shadow permanently (it takes no action).
  Pre-registered acceptance criterion is diagnostic quality, not
  trigger behavior: zero NaN/inf, full four-part S-3 display contract
  present, reproducible under replay."
- `docs/handoff/council-attack-rubric.md` K-2, K-3, F-3.
- Codebase grounding:
  - `database.py:1147-1194` — `record_shadow_observation` sibling.

## Why
The user mandated BOTH gates clean. Gate 2's specific shape under
HARDEN is **diagnostic quality**, not trigger calibration — because M2
drives no trade, calibration failure cannot cause a wrong action. The
gate must therefore be verifiable as "the diagnostic was honest every
cycle for N weeks."

A vague "we watched it" fails K-2; a pre-registered, named-threshold
checker passes.

## Deliverables

### D1. Pre-registration fixture
`tests/fixtures/parity/gate2_pre_registration.json` — committed BEFORE
the shadow run starts:

```jsonc
{
  "purpose": "K-2 pre-registered Gate-2 acceptance criteria for HARDEN Phase-1 M2 shadow run. Committed before shadow starts; immutable.",
  "shadow_window_start": "<ISO-8601 date>",
  "shadow_window_end": "<ISO-8601 date>",
  "minimum_run_length_days": <int, pre-registered>,
  "expected_minimum_cycle_count": <int, derived from minimum_run_length and trading-day cadence>,
  "acceptance_criteria": {
    "max_nan_inf_cvar_5pct": 0,
    "max_nan_inf_cvar_5pct_stderr": 0,
    "max_nan_inf_cvar_n_tail": 0,
    "min_s3_label_coverage_pct": 100.0,
    "min_s3_bias_warning_coverage_pct": 100.0,
    "min_s3_stderr_present_coverage_pct": 100.0,
    "min_s3_n_tail_present_coverage_pct": 100.0,
    "max_replay_divergence_pct": 0.0
  },
  "gate2_disjoint_from_gate1_reference_cycle_list": true
}
```

The numeric thresholds are **all zero** for the NaN/inf and divergence
counts — M2 is a deterministic diagnostic; any non-finite value is a
load-bearing defect. The S-3 coverage thresholds are 100% — the
four-part contract is mandatory every cycle (per §3.8).

### D2. Test file
`tests/engine/test_gate2_shadow_diagnostic_quality.py`.

### D3. Test cases

**Scenario 1 — `test_shadow_log_contains_no_nan_or_inf_in_diagnostic_fields`**
- Read `cvar_diagnostics` rows over the shadow window.
- Assert no row has `cvar_5pct`, `cvar_5pct_stderr`, or `cvar_n_tail`
  that is `NaN` or `inf` — using `math.isnan` / `math.isinf` /
  numeric comparison. NULL is allowed only for the
  insufficient-history sentinel rows (and those rows must have
  `cvar_5pct IS NULL` AND `cvar_n_tail IS NULL` together — never one
  without the other).

**Scenario 2 — `test_s3_four_part_contract_present_every_shadow_cycle`**
- For every shadow cycle, fetch the rendered diagnostic surface.
- Assert all four S-3 elements present (stderr value, n_tail value,
  "diagnostic, not a signal" label, bias warning) — same parametrized
  contract as RED test §8.3 scenario 3, but over the **shadow set**,
  not a single fixture.
- Assert coverage = 100% across the shadow window.

**Scenario 3 — `test_shadow_run_meets_minimum_pre_registered_length`**
- Read the shadow log's cycle count and date range.
- Assert `cycle_count >= expected_minimum_cycle_count` from the
  pre-registration fixture.
- Discriminating-power: a developer who runs shadow for only a few
  cycles cannot trigger early-pass.

**Scenario 4 — `test_shadow_run_replay_is_bit_identical_to_live_log`**
(K-2 reproducibility — F-2 + M-2 in production).
- For a sample of `cycle_id`s from the shadow window, replay the M2
  estimator on the captured cycle inputs.
- Assert the replay's `cvar_5pct`, `cvar_5pct_stderr`, `cvar_n_tail`
  bit-equal the live shadow log.
- Discriminating-power: catches latent nondeterminism that escaped
  RED test §8.4's in-test environment but emerges in production.

**Scenario 5 — `test_pre_registration_was_committed_before_shadow_window_start`**
(K-2 + K-3 ceremony — load-bearing audit trail).
- Read the git history of `gate2_pre_registration.json`; assert the
  first-author commit timestamp is BEFORE the `shadow_window_start`.
- Discriminating-power: catches post-hoc threshold tuning — the
  load-bearing failure mode K-2 exists to prevent.

**Scenario 6 — `test_shadow_window_disjoint_from_gate1_reference_cycle_list`**
(K-3).
- Same disjointness check as Gate-1 scenario 5, from the other side.

### D4. Test naming
- `test_shadow_log_contains_no_nan_or_inf_in_diagnostic_fields`
- `test_s3_four_part_contract_present_every_shadow_cycle`
- `test_shadow_run_meets_minimum_pre_registered_length`
- `test_shadow_run_replay_is_bit_identical_to_live_log`
- `test_pre_registration_was_committed_before_shadow_window_start`
- `test_shadow_window_disjoint_from_gate1_reference_cycle_list`

## Dependencies
- BLOCKED BY: HARDEN Phase 1 GREEN.
- BLOCKED BY: shadow log persistence (the `cvar_diagnostics` table
  populated over the shadow window) — i.e., this gate runs AFTER M2
  has been live-shadow for `minimum_run_length_days`.
- BLOCKED BY: pre-registration commit (must occur BEFORE shadow run
  starts; scenario 5 enforces the ordering).
- BLOCKS: cutover to HARDEN-as-deployed.

## Golden-fixture tests required
- `tests/fixtures/parity/gate2_pre_registration.json` — committed BEFORE
  the shadow run starts.

## Definition of Done
- [ ] Pre-registration fixture committed BEFORE the shadow window
  starts; the commit message records the rationale for each threshold.
- [ ] Test file committed at
  `tests/engine/test_gate2_shadow_diagnostic_quality.py`.
- [ ] All six scenarios authored to RED on the date the
  pre-registration is committed.
- [ ] Scenarios 1, 2, 3 are RUN against the live shadow log;
  scenarios 4, 5, 6 are run against the captured shadow log and the
  pre-registration fixture.
- [ ] No threshold in the acceptance criteria is non-zero for the
  NaN/inf counts (any non-zero would silently admit a load-bearing
  defect).
- [ ] **EXPLICITLY OUT OF SCOPE in the test docstrings:** the gate
  is NOT a joint VaR-ES coverage backtest (Acerbi-Szekely / Fissler-
  Ziegel) — per §3.8, M2 does not need calibration because it drives
  no trade. The acceptance is diagnostic quality, not ES calibration.

## Risk callouts
- **Operator anchoring during shadow.** While shadow is running, the
  operator may anchor on M2's number. The S-3 bias warning element
  is the guard; scenario 2's 100%-coverage enforcement is load-bearing.
  If S-3 element (d) is missing from even one cycle, the gate FAILS.
- **K-3 burned-data discipline.** If Gate 2 FAILS, its data is BURNED
  — a fresh OOS window is required. The plan does NOT recycle the
  failed shadow window's data as input to any selection or re-tune.
  This is enforced by Gate 1 / Gate 2 / Phase-1 reference cycle list
  being disjoint (scenario 6).
- **Replay-of-live-shadow.** Scenario 4's replay requires capturing
  the exact `cycle_id`-keyed inputs at live time. This is the
  `shadow_observations` payload pattern — the live cycle's kNN-pool
  inputs must be persisted at write time so replay has the same
  pool. Persistence-architect's plan owns the schema; this test only
  asserts the contract holds.
- **Pre-registration commit must NOT be amendable.** Scenario 5
  checks the first-author commit, not the current state. If a
  later commit edits thresholds, the test still anchors on the
  original commit — but a reviewer must reject such an edit at
  PR time (the pre-registration is immutable post-commit).

## Out of scope
- The joint VaR-ES coverage backtest (Acerbi-Szekely / Fissler-Ziegel)
  — Phase 2 only, and even there, structurally underpowered. Scaffolded
  in the Phase-2 plan; NOT part of Gate 2 under HARDEN.
- Phase-2 trigger-calibration validation — separate plan.
- A live ES-coverage check on M2 — categorically out of scope under
  HARDEN per the council's explicit decision.
- Validation that M2 is "useful" — that is an operator judgment,
  not a test.
