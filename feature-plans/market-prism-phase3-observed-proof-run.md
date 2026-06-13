# Market Prism — Phase 3: Observed Proof Run

**Epic:** [Market Prism](market-prism-overview.md) · **Status:** 🔴 blocked by Phase 2.

## Goal

Before the Prism is ever trusted to run blind nightly, the PM drives the real team ONCE under
observation, on real data, and shows the operator a **real full `MARKET_PRISM` report + the
complete per-agent audit trail**. This is the operator's acceptance gate for the whole epic:
"prove it before trusting it to run blind."

## Acceptance criteria

1. The PM runs the Phase-2 orchestration end-to-end on **real data** (real APIs; spend
   authorized) under direct observation — a single `run_id`.
2. A real `MARKET_PRISM` `advisor_observations` row is written with a genuine integrated
   overnight read (NOT "Synthesis unavailable", NOT a stub) carrying the `run_id`.
3. `get_prism_audit_for_run(run_id)` returns the full deliberation trail and the PM presents it
   to the operator: each analyst's initial read, the clarifying Q&A, any debate rounds, the
   synthesis.
4. The Cycle-5 Overview tab renders the produced report correctly (live-render check by PM).
5. The PM surfaces BOTH artifacts to the operator (the report as rendered + the audit trail)
   and explicitly states this is the proof run — get operator sign-off before Phase 4.

## What the PM delivers to the operator

- The rendered Overview tab screenshot (PM reads it with its own eyes first — visual gate).
- A dump / readable rendering of `get_prism_audit_for_run(run_id)` showing the full trail.
- A short note: which lenses had real data vs `limited-inputs`, whether any debate occurred and
  why, total Opus spend for the run.

## Hard rules

- This is a PM-observed run, not an unattended one — Phase 4 (scheduling) does NOT start until
  the operator has seen these artifacts and signed off.
- If the run errors or produces a degenerate report, that is a Phase-2 defect — loop back, do
  not paper over it. (Ref: "verify a feature WORKS live, not just tests-green.")
- Visual gate is mandatory: PM must actually LOOK at the rendered report before claiming it
  renders.

## Dependencies

Phases 1 + 2 complete and merged.
