# Phase 2 — Abstain-fail-safe coverage for the forward-path simulator

## Feature
A coverage test set for **F-4** + **A-2** — every new pure function in
the Phase-2 CVaR co-signal path that can receive insufficient/degenerate
data must **abstain fail-safe** (return the `CVaRAssessment.None`
sentinel; `breach=False`), NEVER an in-band CVaR value. The protective
heuristic floor must still fire in such cases.

## Phase
Phase 2.

## Owner agent-type
`quant-test-writer` (RED authoring). Implementation: `risk-engine-specialist`
(the simulator + CVaR estimator), `quant-code-reviewer` (the
abstain-fail-safe contract).

## Source-of-truth references
- `docs/handoff/council-attack-rubric.md` F-4 (BINDING) — insufficient-
  data path returns an in-band CVaR ⇒ FAIL; protective stop must still
  function.
- `docs/handoff/council-attack-rubric.md` A-2 — non-finite propagation
  closed at the boundary; matches `_reject_non_finite` policy at
  `math_engine.py:30-54` (verbatim: a NaN must not silently
  short-circuit; an Inf must not spuriously trigger).
- `docs/handoff/decision-science-council-synthesis.md` §5.3 —
  `CVaRAssessment` frozen typed object: `cvar_pct: float|None`,
  `breach: bool`, `tail_obs_count: int`, `insufficient_reason: str`.
  `None` ⇒ out-of-band sentinel mirroring `run_monte_carlo`'s `None`;
  `breach` always `False` when `None` (fail-safe).
- Project memory:
  - `project_mc_sentinel_consumer_blast_radius` — `run_monte_carlo`'s
    `None` is consumed at 7+ sites; the Phase-2 `CVaRAssessment.None`
    inherits the same blast-radius discipline.
  - `project_mc_eligible_pool_vs_raw_day_boundary` — sufficiency is
    judged on the eligible kNN pool, not raw history.
  - `project_cluster5_d6_orphaned_red_triage` — the `_replay_exit_tick`
    abstain-fail-safe gating already handles raw `None mc_prob` on
    `mc_available`.

## Why
The Phase-2 CVaR co-signal sits next to the existing heuristic stack;
under F-4 the protective stop must still fire when CVaR is unavailable.
The contract is **fail-safe by sentinel** — never by in-band default
values that look like a CVaR. A "0.0 CVaR" value when the path bank is
empty would be a category disaster: the consumer reads "0.0 = safe" and
**suppresses** the protective stop the heuristic floor was supposed to
fire.

## Deliverables

### D1. Test file
`tests/engine/test_phase2_abstain_fail_safe.py`.

### D2. Test cases

**Scenario 1 — `test_cvar_assessment_returns_none_when_path_bank_absent`**
(M-2 Tier-2 read).
- Construct a state where the `path_bank_manifest` row is absent
  (e.g., fresh DB, first cycle after Phase-2 install).
- Call the CVaR co-signal layer.
- Assert returned `CVaRAssessment.cvar_pct is None`.
- Assert `breach is False`.
- Assert `insufficient_reason` is a non-empty descriptive string.

**Scenario 2 — `test_cvar_assessment_returns_none_when_path_bank_stale`**
- Construct a state where `path_bank_manifest.trading_day` is older
  than the current `cycle_id`'s trading day.
- Assert `cvar_pct is None`, `breach is False`,
  `insufficient_reason` mentions staleness.

**Scenario 3 — `test_cvar_assessment_returns_none_when_calibration_failed`**
- Patch the calibration step (Politis-White block-length selector or
  the path-generator MLE if any) to raise.
- Assert the layer abstains fail-safe — no in-band CVaR; no exception
  propagates to the cycle loop.

**Scenario 4 — `test_cvar_assessment_returns_none_when_regime_bucket_thin`**
(R-4 — gate-zero blockage).
- Construct a regime classifier output that maps the current cycle
  into a bucket with `< MIN_BUCKET_TAIL_OBS` (the named constant
  Phase-2 defines).
- Assert abstain fail-safe.
- Discriminating-power: catches an implementation that interpolates a
  "best-guess" CVaR from neighbouring buckets — exactly the kind of
  silent-fallback R-4 calls out.

**Scenario 5 — `test_protective_stop_fires_when_cvar_returns_none`**
(F-4 + the load-bearing safety-floor invariant).
- Construct a cycle state where:
  - the CVaR co-signal returns `None` (insufficient),
  - the ticks-below-stop condition IS triggered (the existing
    protective-stop primitive in `math_engine.py:70-74` and
    `compute_exit_confirmation` at `:739-744`).
- Call the full per-cycle decision path.
- Assert: a protective stop fires. The CVaR-`None` does NOT veto it,
  override it, or suppress it.
- Discriminating-power: this is the FAILURE MODE F-4 exists to
  prevent — and the test is the structural enforcement.

**Scenario 6 — `test_priority_resolver_treats_cvar_none_as_no_signal`**
(consumer-side contract — G-1 blast-radius discipline).
- Construct a state where every other exit flag is `False` AND the
  CVaR co-signal is `None`.
- Assert `resolve_trigger_priority(...)` returns `(None, [])` — no
  trigger fires, **no in-band CVaR value sneaks in as a signal**.
- Sub-scenario: state where Trailing Stop is `True` AND CVaR
  co-signal is `None`. Assert resolver still returns
  `("Trailing Stop", [])` — the missing co-signal does not block
  another layer.

**Scenario 7 — `test_cvar_assessment_none_breach_is_always_false`**
(invariant — None ⇒ breach=False).
- Property-based (hypothesis): generate `CVaRAssessment` instances
  with random `tail_obs_count`, `insufficient_reason`, and
  `cvar_pct ∈ {None, finite float}`. Assert:
  - `cvar_pct is None` ⇒ `breach is False` (the fail-safe invariant).
  - The reverse is not required (a finite CVaR may legitimately have
    `breach=False`).
- Discriminating-power: catches an implementation that sets `breach`
  from a default `True` when `cvar_pct is None`.

**Scenario 8 — `test_no_in_band_zero_or_sentinel_value_for_missing_cvar`**
(A-2 strict).
- AST-scan the Phase-2 module for any return statement that returns
  `0.0` or `-1.0` or a "magic" sentinel float from the CVaR layer.
- Assert no such literal exists in the abstain branch — `None` is
  the only acceptable sentinel.

### D3. Test naming
- `test_cvar_assessment_returns_none_when_path_bank_absent`
- `test_cvar_assessment_returns_none_when_path_bank_stale`
- `test_cvar_assessment_returns_none_when_calibration_failed`
- `test_cvar_assessment_returns_none_when_regime_bucket_thin`
- `test_protective_stop_fires_when_cvar_returns_none`
- `test_priority_resolver_treats_cvar_none_as_no_signal`
- `test_cvar_assessment_none_breach_is_always_false`
- `test_no_in_band_zero_or_sentinel_value_for_missing_cvar`

## Dependencies
- BLOCKED BY: Phase 2 path simulator and CVaR estimator existing as
  named modules.
- BLOCKED BY: `CVaRAssessment` dataclass / typed object existing.
- BLOCKED BY: `resolve_trigger_priority` extension to include the
  CVaR co-signal (task #8).
- BLOCKS: any Phase-2 co-signal GREEN handoff.

## Golden-fixture tests required
None for the abstain-fail-safe contract itself (state is constructed
in-test). The path-bank fixture exists in the tier1_seed plan.

## Definition of Done
- [ ] Test file committed.
- [ ] All eight scenarios RED on `main` (no Phase-2 code yet).
- [ ] Scenario 5 (the load-bearing F-4 enforcement) uses the **full**
  per-cycle decision path (including `compute_exit_confirmation`,
  `resolve_trigger_priority`), not a stub.
- [ ] Scenario 8 (AST scan) lists the whitelisted return literals
  (e.g., `0.0` in non-CVaR computations) explicitly so the scan does
  not false-positive.

## Risk callouts
- **F-4 enforcement against in-band zero.** The most dangerous failure
  mode is `cvar_pct = 0.0` when the path bank is empty. Scenario 8 is
  the AST-level guard; scenario 1 is the runtime guard.
- **Consumer-side blast radius.** G-1 reminded us 7+ consumers exist
  for `run_monte_carlo`'s `None` sentinel. Each consumer of
  `CVaRAssessment` must also handle `None` correctly; scenario 6
  enforces the resolver's contract, but other consumers (reporting,
  chart history, autotuner replay) need their own contracts.
  Out-of-scope here — separate consumer-side tests live in those
  modules' plans.
- **Property-based test seeding.** Scenario 7's hypothesis run must
  be deterministic (`derandomize=True`) per the M2 stderr property
  plan's settings.

## Out of scope
- The Phase-2 path simulator's calibration correctness — that is
  C-3 / C-4 / D-2; separate plan.
- The CVaR estimator's R-U-correctness — same as M2's; the Phase-2
  estimator inherits the M2 RED test's assertions for the algorithm.
- The hysteresis state machine — separate plan (task #7).
