# Phase 2 — CVaR co-signal hysteresis state machine

## Feature
A state-machine test set for **C-1** — the Phase-2 CVaR co-signal is
gated by a two-level hysteresis band + multi-tick confirmation, a
sibling of `compute_exit_confirmation` with its `EXIT_CONFIRM_TICKS = 3`
counter. The co-signal enters `resolve_trigger_priority` as one
additional boolean — never solely fires an exit.

## Phase
Phase 2.

## Owner agent-type
`quant-test-writer` (RED authoring). Implementation:
`risk-engine-specialist`.

## Source-of-truth references
- `docs/handoff/council-attack-rubric.md` C-1 (★ BINDING) — explicit
  hysteresis / multi-tick confirmation; structurally analogous to
  `EXIT_CONFIRM_TICKS = 3` and `VWAP_BREAK_CONFIRM_TICKS = 3`. A
  single-evaluation hard CVaR trigger converts tail-estimation noise
  directly into spurious exits.
- `docs/handoff/decision-science-council-synthesis.md` §5.3 —
  "two-level hysteresis band + multi-tick confirmation state machine,
  a sibling of `compute_exit_confirmation`. Abstains fail-safe when
  the ensemble is unavailable. Operates as a co-signal."
- `docs/handoff/decision-science-council-synthesis.md` §5.2 —
  Phase 2 endpoint = CVaR co-signal: input that can narrow, veto, or
  confirm an exit another layer already supports, but never solely
  fires one.
- Codebase grounding:
  - `math_engine.py:669-692` — `resolve_trigger_priority` and
    `_TRIGGER_PRIORITY_ORDER` (the resolver the co-signal extends).
  - The existing `compute_exit_confirmation` (search for
    `EXIT_CONFIRM_TICKS`) pattern.

## Why
A bare CVaR breach evaluated once per minute converts every noisy tail
estimate into an exit (the failure mode C-1 exists to prevent). The
council brief is explicit: hysteresis is mandatory. The co-signal also
cannot solely fire an exit — §5.2's "co-signal, never trigger" framing.

## Deliverables

### D1. Test file
`tests/engine/test_phase2_cvar_cosignal_hysteresis.py`.

### D2. Fixture file
`tests/fixtures/math/phase2_cvar_hysteresis_thresholds.json` — recording
the design's frozen thresholds (named constants in source):

```jsonc
{
  "CVAR_COSIGNAL_ARM_THRESHOLD": <float>,       // upper band — co-signal arms above this
  "CVAR_COSIGNAL_DISARM_THRESHOLD": <float>,    // lower band — co-signal disarms below this (DISARM < ARM)
  "CVAR_COSIGNAL_CONFIRM_TICKS": <int>,         // multi-tick confirmation (sibling of EXIT_CONFIRM_TICKS=3)
  "discriminating_power": "DISARM strictly less than ARM (hysteresis); CONFIRM_TICKS >= 2 (multi-tick)."
}
```

### D3. Test cases

**Scenario 1 — `test_cosignal_does_not_fire_on_single_tick_above_arm`**
(C-1 the load-bearing scenario).
- State machine starts disarmed; CVaR estimate is above ARM on tick 1.
- Assert the co-signal output is `False` on tick 1 (CONFIRM_TICKS not
  yet reached).
- Discriminating-power: this is exactly the single-tick-trigger
  failure C-1 calls out.

**Scenario 2 — `test_cosignal_fires_after_confirm_ticks_consecutive_above_arm`**
- CVaR is above ARM on `CONFIRM_TICKS` consecutive ticks.
- Assert the co-signal becomes `True` on the `CONFIRM_TICKS`-th tick.
- Sub-scenario: CVaR drops below ARM on tick 2; assert the counter
  resets and the co-signal stays `False` on tick 3, even if tick 3
  is back above ARM.

**Scenario 3 — `test_cosignal_disarms_only_when_cvar_falls_below_disarm`**
(hysteresis — the load-bearing one).
- Co-signal is armed (True). CVaR falls below ARM but stays above
  DISARM.
- Assert the co-signal stays `True` (hysteresis: must cross the
  lower band to disarm).
- Sub-scenario: CVaR falls below DISARM. Assert co-signal becomes
  `False`.
- Discriminating-power: catches an implementation that uses a single
  threshold (no hysteresis) — the most common C-1 violation.

**Scenario 4 — `test_arm_threshold_strictly_greater_than_disarm_threshold`**
(static invariant — the hysteresis precondition).
- Read the source constants `CVAR_COSIGNAL_ARM_THRESHOLD` and
  `CVAR_COSIGNAL_DISARM_THRESHOLD`.
- Assert `ARM > DISARM`. Without this, the hysteresis collapses into
  a single threshold (no hysteresis at all).
- Discriminating-power: catches a developer who tunes the two
  thresholds to the same value (a degenerate state machine).

**Scenario 5 — `test_cosignal_never_solely_fires_an_exit`** (§5.2
co-signal-not-trigger — the load-bearing semantic guard).
- Construct a state where the CVaR co-signal is `True` and ALL OTHER
  exit flags (Trailing Stop, Take-Profit, VWAP Breakdown, VWAP Bleed
  Cut) are `False`.
- Call `resolve_trigger_priority(...)`.
- Assert the resolver returns `(None, [])` OR an empty/no-exit
  decision — the CVaR co-signal alone does NOT fire any exit.
- Sub-scenario: state where CVaR co-signal is `True` AND Trailing Stop
  is `True`. Assert the resolver returns `("Trailing Stop", ...)` —
  the co-signal does not block another layer; it narrows/confirms but
  is not the sole driver.
- Discriminating-power: catches an implementation that puts the
  CVaR co-signal into `_TRIGGER_PRIORITY_ORDER` as a peer of
  Trailing Stop — which would let it solely fire.

**Scenario 6 — `test_cosignal_arming_is_input_to_resolver_not_independent_trigger`**
(architectural — extends scenario 5).
- AST-scan: assert no call to `submit_order` / `place_order` /
  `cancel_order` / `liquidate` in the CVaR co-signal module that does
  not first go through `resolve_trigger_priority`.

**Scenario 7 — `test_state_machine_replay_deterministic_on_seeded_input_stream`**
(F-2).
- Given a fixed sequence of CVaR estimates, run the state machine
  twice; assert the output streams are bit-identical.

**Scenario 8 — `test_state_machine_abstains_fail_safe_when_cvar_is_none`**
(F-4 + the abstain-fail-safe plan crossover).
- State machine is currently armed. An incoming `CVaRAssessment.None`
  arrives.
- Assert the state machine does NOT count this as a tick (neither up
  nor down). The arming state is preserved; the counter does NOT
  increment (a `None` is "no information," not "below threshold").
- Sub-scenario: state machine is disarmed and a `None` arrives;
  assert it stays disarmed and the counter stays 0.
- Discriminating-power: catches an implementation that treats `None`
  as "below threshold" (and thus rapidly disarms) OR as "above
  threshold" (and thus spuriously arms).

### D4. Test naming
- `test_cosignal_does_not_fire_on_single_tick_above_arm`
- `test_cosignal_fires_after_confirm_ticks_consecutive_above_arm`
- `test_cosignal_disarms_only_when_cvar_falls_below_disarm`
- `test_arm_threshold_strictly_greater_than_disarm_threshold`
- `test_cosignal_never_solely_fires_an_exit`
- `test_cosignal_arming_is_input_to_resolver_not_independent_trigger`
- `test_state_machine_replay_deterministic_on_seeded_input_stream`
- `test_state_machine_abstains_fail_safe_when_cvar_is_none`

## Dependencies
- BLOCKED BY: Phase-2 CVaR estimator (provides the input stream).
- BLOCKED BY: priority-resolver extension (task #8) — scenario 5/6
  depend on the resolver knowing about the co-signal.
- BLOCKS: any Phase-2 trigger path GREEN handoff.

## Golden-fixture tests required
- `tests/fixtures/math/phase2_cvar_hysteresis_thresholds.json`.

## Definition of Done
- [ ] Test file committed.
- [ ] All eight scenarios RED on `main`.
- [ ] The CONFIRM_TICKS constant is `>= 2` (a multi-tick discipline);
  scenario 4 enforces ARM > DISARM as a static invariant.
- [ ] Scenario 5 (co-signal-not-trigger) is the load-bearing semantic
  guard for §5.2.
- [ ] Scenario 8 (None-abstain) is consistent with the abstain-fail-safe
  plan (task #65 / Phase-2 plan).

## Risk callouts
- **Threshold values are design-set, not test-set.** The ARM/DISARM
  numeric values come from the Phase-2 design (frozen by ES-coverage
  calibration on the return series, NOT by P&L — NN1). The test only
  asserts the structure (ARM > DISARM, CONFIRM_TICKS >= 2); the
  specific values are spec-bundle facets and are content-hashed.
- **Resolver integration coupling.** Scenario 5/6 couples this test
  to the resolver-extension plan (task #8). Order of GREEN: resolver
  extension lands first; this hysteresis state machine lands second.
- **`None`-handling subtlety.** Scenario 8's treatment of `None`
  ("no information, preserve state") is a deliberate design choice.
  Alternative implementations (treat `None` as "below threshold")
  are testable but failsafe-questionable. The plan freezes the
  "preserve state on `None`" semantics; if the design changes, the
  fixture and test update together.

## Out of scope
- The CVaR estimator itself (separate plan — task #6).
- The path simulator (separate plan).
- The Phase-2 joint VaR-ES coverage backtest (separate plan — task #64).
- Operator-display surfaces for the co-signal — separate UI plan.
