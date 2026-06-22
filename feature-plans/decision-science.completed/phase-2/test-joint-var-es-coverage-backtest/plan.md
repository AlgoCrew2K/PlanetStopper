# Phase 2 — Joint VaR-ES coverage backtest design (Acerbi-Szekely / Fissler-Ziegel)

## Feature
A Phase-2-entry-gate backtest **design plan** for the joint VaR-ES
coverage test (Acerbi-Szekely / Fissler-Ziegel class) required to credit
any Phase-2 CVaR co-signal. **Scaffolded for completeness even though
structurally underpowered** at AlphaBot's ~6-37 tail-obs data scale —
per v3 §2.3 the decisive finding.

## Phase
Phase 2 entry gate.

## Owner agent-type
`quant-test-writer` (RED authoring of the test design). Implementation:
deferred to whoever lifts Phase-2 entry gates; this plan is the
verifiability spec for Phase-2's go/no-go entry decision.

## Source-of-truth references
- `docs/handoff/decision-science-council-synthesis.md` §2.3 — decisive
  finding: a joint VaR-ES coverage backtest needs ~1,000 tail-relevant
  observations; AlphaBot accrues ~6 tail days per 125-day fold, ~37 per
  3 years. **Structurally underpowered.**
- `docs/handoff/decision-science-council-synthesis.md` §5.1 precondition
  (a)-(d), §5.7 — "if the latency arithmetic does not clear or the
  buckets are too thin, Phase 2 does not proceed and the system stays
  at Finalist A permanently."
- `docs/handoff/council-attack-rubric.md` K-2 — ES not standalone-
  elicitable (Fissler-Ziegel 2016); the only honest validator is a
  joint VaR-ES coverage backtest (Acerbi-Szekely class).
- `docs/handoff/decision-science-eut-cvar-research-2026-05-22.md` §2.4
  — the joint VaR-ES backtest machinery.

## Why
If Phase 2 ever proceeds, the council brief is explicit: K-2 fails
without a joint VaR-ES coverage backtest. Even if Phase 2 ships only as
a co-signal (not a sole trigger), the structural validation question is
the same — the co-signal must demonstrate it adds information. The
honest expected outcome is **the gate FAILS** and Phase 2 does not
proceed.

This plan exists so that the gate's PRECISE shape is committed BEFORE
Phase 2 is authorized, preventing the "K-2 acceptance threshold chosen
after seeing shadow results" failure mode.

## Deliverables

### D1. Backtest design fixture
`tests/fixtures/parity/phase2_joint_var_es_backtest.spec.json` —

```jsonc
{
  "purpose": "Phase-2-entry-gate joint VaR-ES coverage backtest spec. STRUCTURALLY UNDERPOWERED at AlphaBot's data scale; documented as such per v3 §2.3.",
  "test_class": "Acerbi-Szekely (joint VaR-ES residual; Fissler-Ziegel scoring)",
  "primary_statistic": "Z2 (Acerbi-Szekely 2014) — mean of (loss_i / ES_alpha_i) * I{loss_i > VaR_alpha_i}; expected value 1.0 under correct calibration.",
  "secondary_statistic": "Fissler-Ziegel strictly-consistent joint VaR-ES scoring; difference vs benchmark scoring under bootstrap test.",
  "alpha": 0.05,
  "expected_tail_obs_count_per_3_years": "~37 (decisive finding §2.3 — ~6 per 125-day fold)",
  "power_requirement_for_credible_test": "~1000 tail-relevant observations (Yamai-Yoshiba)",
  "structural_underpowered_disposition": "EXPECTED FAIL — precondition (d) of §5.1; the gate's honest outcome is Phase 2 does not unlock. Authorized fallback per §5.1: 'OR the trigger ships diagnostic-grade-permanent (never as a calibrated budget).'",
  "acceptance_thresholds": {
    "z2_statistic_null_band": [<lower>, <upper>],
    "z2_bootstrap_p_value_min": 0.05,
    "fissler_ziegel_score_improvement_min": <bootstrap-CI lower bound>,
    "minimum_genuine_tail_obs": 100
  },
  "burnt_data_discipline": "K-3 — the data used to score this backtest cannot also be used to choose any spec facet; a separate disjoint OOS window is the score input.",
  "nn1_threshold_count_discipline": "any tunable parameter of the co-signal (hysteresis band, confirmation tick count) enters the BHY haircut's N_effective; T is the count of GENUINE INDEPENDENT TAIL OBSERVATIONS (~7-8), NEVER the simulation-path count (see Phase-2 multi-testing accounting task)."
}
```

### D2. Test file
`tests/engine/test_phase2_joint_var_es_coverage_design.py`.

### D3. Test cases (design-stage assertions on the spec, not on data yet)

**Scenario 1 — `test_phase2_backtest_spec_acknowledges_data_scale_limitation`**
- Read the spec fixture.
- Assert `expected_tail_obs_count_per_3_years` is recorded and
  `power_requirement_for_credible_test` is recorded.
- Assert `structural_underpowered_disposition` includes the explicit
  EXPECTED FAIL framing — the gate's honest outcome is documented.
- Discriminating-power: catches a Phase-2 advocate who removes the
  underpowered disposition from the spec (a structural attempt to
  flatter the gate).

**Scenario 2 — `test_phase2_backtest_uses_joint_var_es_not_es_alone`**
(Fissler-Ziegel binding).
- Assert `primary_statistic` references a joint VaR-ES test (Z2 or
  equivalent).
- Assert there is NO "ES alone" scoring statistic — ES is not
  standalone-elicitable.
- Discriminating-power: catches the K-2 failure mode of an "ES
  backtest" that scores ES against realized losses (mathematically
  meaningless per Fissler-Ziegel 2016).

**Scenario 3 — `test_phase2_acceptance_thresholds_pre_registered`**
(K-2 pre-registration).
- The spec's `acceptance_thresholds` are committed BEFORE any
  Phase-2 path simulator runs in shadow. The scenario asserts the
  spec's git commit timestamp predates the first Phase-2 shadow
  cycle's `cycle_id` timestamp (similar to Gate-2 scenario 5).
- Discriminating-power: catches post-hoc threshold tuning.

**Scenario 4 — `test_phase2_backtest_uses_disjoint_window_from_phase1_gates`**
(K-3 burnt-data discipline).
- Assert the Phase-2 backtest window does NOT overlap Gate-1 reference
  cycles, Gate-2 shadow window, or any Phase-1.5 S-1 reference window.

**Scenario 5 — `test_phase2_backtest_T_uses_distinct_tail_obs_not_path_count`**
(Phase-2 multi-testing accounting — H-2-analog).
- Assert the spec's `nn1_threshold_count_discipline` field explicitly
  states T = genuine independent tail observations, NEVER simulation
  path count. (See task #26 — Phase-2 multi-testing accounting.)
- Discriminating-power: an advocate who quietly substitutes the path
  count to gain statistical-power illusion is caught.

**Scenario 6 — `test_phase2_underpowered_outcome_authorizes_diagnostic_grade_fallback`**
(§5.1(d) fallback authorization).
- Assert the spec's `structural_underpowered_disposition` references
  the §5.1(d) fallback — "OR the trigger ships diagnostic-grade-
  permanent (never as a calibrated budget)."
- Discriminating-power: catches a Phase-2 advocate who removes the
  fallback path, leaving "ship calibrated or stop" as the only
  option (which under the data wall means "stop") — but without the
  authorized fallback recorded, future re-attempts cannot rely on
  the fallback the council pre-authorized.

### D4. Test naming
- `test_phase2_backtest_spec_acknowledges_data_scale_limitation`
- `test_phase2_backtest_uses_joint_var_es_not_es_alone`
- `test_phase2_acceptance_thresholds_pre_registered`
- `test_phase2_backtest_uses_disjoint_window_from_phase1_gates`
- `test_phase2_backtest_T_uses_distinct_tail_obs_not_path_count`
- `test_phase2_underpowered_outcome_authorizes_diagnostic_grade_fallback`

## Dependencies
- BLOCKED BY: Phase 2 authorization decision (user decision per §5.1
  preconditions).
- BLOCKED BY: Phase-2 path simulator implementation (the SUT this
  backtest scores).

## Golden-fixture tests required
- `tests/fixtures/parity/phase2_joint_var_es_backtest.spec.json`.

## Definition of Done
- [ ] Spec fixture committed.
- [ ] Test file committed at
  `tests/engine/test_phase2_joint_var_es_coverage_design.py`.
- [ ] All six scenarios assert on the SPEC (it is a design-stage
  artifact; data scoring comes only if Phase 2 unlocks).
- [ ] The expected outcome of the eventual data-stage gate is
  documented as EXPECTED FAIL — the test treats failing the gate as
  the council-pre-authorized outcome that authorizes the
  diagnostic-grade-permanent fallback.

## Risk callouts
- **This gate is expected to fail.** The decisive finding (v3 §2.3)
  is that the data wall is structural. The gate's pre-registered
  threshold for `minimum_genuine_tail_obs` (set to 100 here; the
  real Phase-2 implementer may revise upward) is **already** higher
  than the realistic 3-year tail-obs count of ~37. The honest expected
  outcome is the gate FAILS and Phase 2 stops permanently at
  Finalist A — which the council explicitly authorized as "a full
  success, not a project failure" (v3 §5.1).
- **No data scoring in Phase 1.** This plan does NOT include the
  actual statistical scoring (Z2 computation, FZ scoring, bootstrap
  p-value). Those land only if Phase 2 unlocks; this plan is the
  pre-registered design.
- **NN1 discipline carry-through.** Any tunable parameter introduced
  by the Phase-2 trigger design (e.g., hysteresis band width,
  confirmation tick count) is a P&L-touchable specification facet and
  enters the BHY haircut's N_effective. The Phase-2 multi-testing
  accounting plan (task #26) owns that discipline; this plan only
  cites it.

## Out of scope
- The actual statistical scoring against live or replay data — Phase
  2 only; this plan is design-stage.
- The Phase-2 path simulator itself — separate plan.
- Phase-2 hysteresis state machine — separate plan.
- Choice of `alpha` value — frozen by mandate at 0.05 (or whatever
  the Phase-2 design freezes).
