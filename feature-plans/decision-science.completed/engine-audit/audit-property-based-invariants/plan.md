# Engine audit — Property-based invariant testing audit

## Feature
A standalone audit + remediation plan for **property-based invariant
test coverage** across the math engine. Identifies which math-engine
invariants are amenable to hypothesis-style tests; reports which are
already covered (per quant-test-writer rule 2) and which are not;
prioritizes a remediation backlog.

## Phase
Engine audit (cross-cutting).

## Owner agent-type
`quant-test-writer`.

## Source-of-truth references
- Project agent contract (this file) rule 2 — "Write property-based
  tests (via `hypothesis` if available) for invariants: monotonicity
  of stops vs time, non-negativity of volatility, bounded probability
  outputs in [0, 1]."
- `docs/handoff/council-attack-rubric.md` A-2 (★) — non-finite
  propagation closed at every new pure-function boundary.
- Codebase grounding:
  - `math_engine.py:30-54` — `_reject_non_finite` /
    `_reject_non_finite_in_records` (NaN/Inf entry validation).
  - `math_engine.py:783-786` — `_z` zero-std guard (0/0 → 0).
  - `math_engine.py:88-94` — time-squeeze decay (monotonicity claim).
  - `math_engine.py:601-606` — VWAP System-A HWM gate.

## Why
Golden fixtures pin specific input → output pairs. Property tests pin
**invariants over the input space**. A bug that passes the captured
pool but fails on a synthetic adversarial pool slips a fixture-only
suite. Hypothesis is the standard tool; the audit identifies where it
is and isn't being used.

## Deliverables

### D1. Invariant catalog
`feature-plans/decision-science/engine-audit/audit-property-based-invariants/output/invariant_catalog.md`
— a per-function catalog of invariants:

| Function | Invariant | Currently tested? | Priority |
|---|---|---|---|
| `run_monte_carlo` | output ∈ [0, 100] when finite, else `None` | YES/NO | ... |
| `run_monte_carlo` | identical seed ⇒ identical output | YES (RED test §8.4 covers M2's; this row is run_monte_carlo's own) | ... |
| `compute_volatility` (or wherever volatility is computed) | output >= 0 | YES/NO | ... |
| `compute_parabolic_ratchet_stop` (or named) | stop monotonically non-decreasing as gain increases (ratchet) | YES/NO | ... |
| `compute_time_squeeze_stop` | stop monotonically tighter as time-since-entry grows (squeeze) | YES/NO | ... |
| `compute_exit_confirmation` | confirmed = False on first tick above threshold | YES/NO | ... |
| `_reject_non_finite` | NaN/Inf input ⇒ raises (never silent) | YES/NO | ... |
| `resolve_trigger_priority` | output's first element is always the highest-priority fired flag | YES/NO | ... |
| `derive_cycle_mc_seed` | same cycle_id ⇒ same seed (purity) | YES/NO | ... |
| `benjamini_hochberg_adjust` | output is non-decreasing in raw-p rank | YES/NO | ... |
| `benjamini_hochberg_adjust` | output is in [0, 1] | YES/NO | ... |
| `compute_haircut_pvalue` | output is in [_HAIRCUT_PVALUE_EPSILON, 1 - _HAIRCUT_PVALUE_EPSILON] | YES/NO | ... |
| `compute_sortino_tstat` (and new `compute_crra_eu_tstat`) | output is finite for any finite input | YES/NO | ... |
| Phase-2 `CVaRAssessment` | cvar_pct is None ⇒ breach is False (fail-safe) | YES/NO | ... |
| ... | ... | ... | ... |

### D2. Audit script
`tools/audit/property_test_coverage_audit.py` — searches the
`tests/` tree for `hypothesis.given` decorators and for `@hypothesis`
imports; per-function counts the property-test coverage.

### D3. Remediation plan
`feature-plans/decision-science/engine-audit/audit-property-based-invariants/output/remediation_backlog.md`
— prioritized property-test backlog. Each entry: function, invariant,
suggested strategy, owning specialist.

## Test cases (the audit's self-tests)

**Scenario 1 — `test_audit_catalogs_known_property_test`**
- Construct a synthetic test file with `@given(...)` and a synthetic
  invariant claim.
- Run audit; assert the catalog reports the function as covered.

**Scenario 2 — `test_audit_misses_no_function_with_hypothesis_decorator`**
- Construct two test files; one uses `from hypothesis import given`,
  one uses `import hypothesis as hyp` (rename).
- Audit must catch both — AST-based, not regex.

**Scenario 3 — `test_audit_output_lists_invariants_per_function`**
- The audit's output schema includes per-function invariant rows.
- Schema validation: every row has `function`, `invariant`,
  `currently_tested`, `priority`.

## Property-test additions (the remediation cycle's deliverables — listed here so the audit's downstream work is sized)

The plans below are per-cycle remediation items. They are NOT this
audit's deliverables — they are downstream cycles the audit's
remediation_backlog will reference. Listed here for sizing:

- `tests/engine/test_time_squeeze_monotonicity_property.py` — assert
  `stop_at(t1) <= stop_at(t2)` for `t1 < t2` over a hypothesis
  strategy of `(entry_price, entry_time, current_time)` tuples.
- `tests/engine/test_parabolic_ratchet_monotonicity_property.py` —
  assert ratchet stop is non-decreasing as gain increases.
- `tests/engine/test_volatility_non_negativity_property.py` — assert
  volatility ≥ 0 for any input returns series.
- `tests/engine/test_monte_carlo_probability_bound_property.py` —
  assert `0.0 <= mc_prob <= 100.0` when finite; `is None` when
  insufficient.
- `tests/engine/test_resolve_trigger_priority_correctness_property.py`
  — assert returned (primary, others) matches the deterministic
  priority order over any combination of input flags.
- `tests/engine/test_benjamini_hochberg_monotonicity_property.py` —
  assert adjusted p-values are non-decreasing in raw-p rank.
- `tests/engine/test_non_finite_input_rejection_property.py` — assert
  every public math function raises on NaN/Inf input (per A-2).

## Dependencies
- BLOCKED BY: `hypothesis` being installed (test-only dep).
- BLOCKED BY (soft): the coverage-gap audit (task #69) — invariant
  catalog is informed by the gap inventory.

## Golden-fixture tests required
None for the audit itself. The downstream property-test cycles do not
need JSON fixtures (strategies generate inputs).

## Definition of Done
- [ ] Invariant catalog committed.
- [ ] Audit script committed.
- [ ] Audit self-tests PASS.
- [ ] Remediation backlog ordered by priority.

## Risk callouts
- **Hypothesis non-determinism.** Per the M2 stderr property plan,
  hypothesis runs must be `derandomize=True` for CI stability. This
  applies to every downstream property-test cycle from the
  remediation backlog.
- **Strategy choice influences coverage.** A poorly-chosen strategy
  (too narrow, biased toward easy cases) yields false-PASS property
  tests. The audit identifies the strategy used (via AST inspection
  of the `@given` arguments) but cannot itself score the strategy's
  fitness — that is the reviewer's job at each downstream cycle.

## Out of scope
- Writing the property tests themselves — each is its own TDD cycle.
- Coverage-gap audit — separate plan (task #69).
- Live-vs-replay determinism audit — separate plan (next).
