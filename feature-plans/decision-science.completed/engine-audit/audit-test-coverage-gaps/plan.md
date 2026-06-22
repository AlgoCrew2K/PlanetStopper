# Engine audit — Test coverage gap audit (golden fixtures + provenance)

## Feature
A standalone audit workstream that enumerates every public function in
`math_engine.py`, every integration path in `alpha_bot_execution.py`,
and every fixture under `tests/fixtures/`. Produces:
1. A fixture-gap inventory keyed by function — what has a golden
   fixture, what does not.
2. A fixture-provenance audit per Gate-1 D-2 rule — captured-from-
   producer / schema-derived with runtime validator / producer-owner
   sign-off / **CIRCULAR** (parser+fixture co-design, an automatic
   gate-1 fail).
3. A prioritized upgrade list — which fixtures must be re-captured or
   re-derived before HARDEN Phase 1 ships.

## Phase
Engine audit (post-changes / pre-deploy). Cross-cutting.

## Owner agent-type
`quant-test-writer` (audit authoring). Implementation of any
remediation fixtures is owned by the relevant specialist.

## Source-of-truth references
- `.claude/CLAUDE.md` — "Every change to math layers requires a
  golden-fixture test."
- Project memory `feedback_verify_backend_contract_before_fixtures` —
  parser+fixture co-design is circular, automatic Gate-1 fail.
- Project memory `feedback_no_hardcoded_test_values` — assertions
  touching producer-computed values derive from fixture or assert
  shape/format.
- `docs/handoff/council-attack-rubric.md` D-1 (★), D-2 (★).
- Project memory `project_verification_audit_pattern` — established
  pre-deploy 6-parallel-surface verification pattern.

## Why
Every project memory entry on test discipline points the same
direction: fixtures are the engine's regression spec; their provenance
is load-bearing; circular fixtures are a Gate-1 fail. AlphaBot has had
~40+ math/engine commits since the last comprehensive coverage audit
(per recent commit log). The decision-science work is about to add
multiple new math functions; before it lands, the existing coverage
floor must be known.

This is NOT a test-writing cycle — it is an audit that produces a
remediation backlog. The remediation cycles are spun off as separate
plans.

## Deliverables

### D1. Inventory deliverable
`feature-plans/decision-science/engine-audit/audit-test-coverage-gaps/output/fixture_inventory.json`
— produced by the audit, NOT pre-committed. Schema:

```jsonc
{
  "audit_date": "<ISO-8601>",
  "audit_head_sha": "<git SHA>",
  "math_engine_functions": [
    {
      "name": "run_monte_carlo",
      "file": "math_engine.py",
      "line": 705,
      "fixtures": [
        {"path": "tests/fixtures/math/<name>.json", "provenance": "captured-from-producer | schema-derived | producer-sign-off | CIRCULAR | NONE", "covers_branches": ["happy_path", "insufficient_history", "zero_std", ...], "missing_branches": [...]}
      ],
      "fixture_gap_severity": "BLOCKER | HIGH | MEDIUM | LOW | NONE"
    },
    ...
  ],
  "alpha_bot_execution_integration_paths": [
    {"description": "decision-loop with MC", "fixture": "...", "provenance": "...", "fixture_gap_severity": "..."}
  ],
  "summary": {
    "total_functions": <int>,
    "covered_functions": <int>,
    "uncovered_functions": <int>,
    "circular_fixtures": <int>,
    "circular_fixture_paths": [...]
  }
}
```

### D2. Audit script
`tools/audit/coverage_gap_audit.py` — a deterministic, RE-RUNNABLE
audit script. Reads the codebase, traces fixture references, classifies
provenance from fixture metadata (each fixture's
`provenance` field — adopt the convention NOW so audits are repeatable),
and produces the inventory.

Discipline:
- The script does NOT modify code or fixtures.
- The script's output is committed at audit-run time (a snapshot, not
  a watchdog).
- The script's runtime is bounded — it does NOT run pytest; it parses
  source and fixture metadata.

### D3. Remediation plan
A flat list (markdown) of the gaps, sorted by severity:
`feature-plans/decision-science/engine-audit/audit-test-coverage-gaps/output/remediation_backlog.md`.

Each entry: function name, fixture gap, severity, recommended action,
owning specialist.

## Test cases (the audit's self-tests)

This audit IS testable. The audit script itself is the SUT; the
tests assert the audit is honest.

**Scenario 1 — `test_audit_identifies_known_uncovered_function`**
- Construct a synthetic codebase with one math function and zero
  fixtures.
- Run the audit script.
- Assert the inventory reports the function as uncovered with
  severity BLOCKER (a math function with no fixture is a Gate-D-1
  blocker).

**Scenario 2 — `test_audit_classifies_known_captured_fixture_as_captured`**
- Construct a fixture with `"provenance": "captured-from-producer"`
  and a function referencing it.
- Run audit; assert classification.

**Scenario 3 — `test_audit_flags_known_circular_fixture`**
- Construct a fixture whose `provenance` field is `"CIRCULAR"` or
  missing entirely (per the convention, missing ⇒ unknown ⇒ flag for
  manual review).
- Run audit; assert it is flagged in `circular_fixture_paths`.

**Scenario 4 — `test_audit_runtime_under_30_seconds`**
- Run the audit on the real codebase.
- Assert it completes in under 30 seconds (the audit must be cheap
  enough to run before every dispatch — it is a coordination tool,
  not a heavy CI job).

**Scenario 5 — `test_audit_output_is_deterministic`**
- Run the audit twice on a frozen tree.
- Assert byte-identical output (no wall-clock, no global RNG, no
  unordered iteration).

## Dependencies
- BLOCKED BY (soft): adopting the fixture `provenance` metadata
  convention. Existing fixtures may need a one-time pass to add the
  field; that one-time pass is itself a remediation item but is
  scoped narrowly (add metadata, do not re-author fixtures).
- BLOCKS: HARDEN Phase-1 GREEN handoff. The audit must run pre-merge
  to confirm Phase 1 does not regress coverage.

## Golden-fixture tests required
- Synthetic-codebase fixtures for the audit's self-tests
  (`tests/fixtures/audit/synthetic_*/`).

## Definition of Done
- [ ] Audit script committed at `tools/audit/coverage_gap_audit.py`.
- [ ] Audit self-tests committed at
  `tests/tools/test_coverage_gap_audit.py`.
- [ ] All five self-test scenarios PASS (the audit itself is a tested
  SUT).
- [ ] First audit run committed under
  `feature-plans/decision-science/engine-audit/audit-test-coverage-gaps/output/`.
- [ ] Remediation backlog produced and sorted by severity.

## Risk callouts
- **`provenance` metadata adoption.** Existing fixtures don't have it;
  retro-adding it is a separate one-time pass owned by the relevant
  specialist (`quant-test-writer` + `risk-engine-specialist` for math
  fixtures, etc.). The audit treats "missing provenance field" as
  "unknown — flag for manual classification" — NOT as automatic
  CIRCULAR — so the audit does not produce false BLOCKERs in the
  transition window.
- **Audit-vs-pytest drift.** The audit reads source statically; pytest
  runs the tests. A test that *references* a fixture but *doesn't
  load* it is reported as covered by the audit but is actually dead.
  Mitigation: the audit can be extended later with a coverage-tool
  cross-reference, but that adds runtime; for the initial pass,
  static reference is acceptable.
- **Severity calibration.** The audit's severity rubric (BLOCKER /
  HIGH / MEDIUM / LOW) is set in the audit script as named constants
  and documented in the script's docstring. Reviewer's responsibility
  to confirm a math-engine function with no fixture is always
  BLOCKER per D-1.

## Out of scope
- Authoring the remediation fixtures themselves — those are separate
  TDD cycles, one per remediation item.
- Property-based test coverage audit — separate plan (next).
- Live-vs-replay determinism audit — separate plan (next).
- Re-classifying existing fixtures retroactively — out of scope; the
  audit reports what it finds.
