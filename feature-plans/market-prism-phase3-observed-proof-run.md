# Feature: Market Prism Phase 3 — Observed Proof Run
Status: ready
Created: 2026-06-13

## Summary

Before the Market Prism is ever trusted to run blind nightly, the PM drives the real Phase-2 agent team once under direct observation on real data and presents the operator with a real full `MARKET_PRISM` report and the complete per-agent audit trail. This is the operator acceptance gate for the whole epic: "prove it before trusting it to run blind." The PM surfaces both the rendered Overview tab and the raw deliberation trail, notes which lenses had real data vs `limited-inputs`, and gets explicit operator sign-off before Phase 4 (unattended scheduling) begins.

## Acceptance Criteria

- [ ] AC-1: The PM runs the Phase-2 orchestration end-to-end on real data (real APIs; Opus spend authorized) under direct observation — a single `run_id`.
- [ ] AC-2: A real `MARKET_PRISM` `advisor_observations` row is written with a genuine integrated overnight read — NOT "Synthesis unavailable", NOT a stub — carrying the `run_id`.
- [ ] AC-3: `get_prism_audit_for_run(run_id)` returns the full deliberation trail and the PM presents it to the operator: each analyst's initial read, the clarifying Q&A, any debate rounds, the synthesis.
- [ ] AC-4: The Cycle-5 Overview tab renders the produced report correctly (PM performs a live-render check, reads the screenshot with its own eyes, and confirms it renders before claiming it does).
- [ ] AC-5: The PM surfaces both artifacts to the operator (the rendered report + the audit trail) and explicitly states this is the proof run. Operator sign-off is received before Phase 4 begins.

## Architecture

This phase introduces no new code. It exercises the Phase-1 + Phase-2 deliverables on real data.

**Artifacts the PM delivers to the operator:**
- Rendered Overview tab screenshot (PM reads it with its own eyes first — visual gate per feedback: "Actually LOOK at the render before claiming it's clean")
- A dump / readable rendering of `get_prism_audit_for_run(run_id)` showing the full trail per analyst and phase
- A short note covering: which lenses had real data vs `limited-inputs`; whether any debate occurred and what triggered it; total Opus spend for the run

**Integration points exercised (read-only, no changes):**
- `advisors/lens_pipeline.py` — Cycle-4 data layer (real API calls)
- Phase-2 agent team (Opus 4.8 real calls)
- `advisors/prism_audit_write.py` + `database.insert_prism_audit_entry` / `get_prism_audit_for_run` (Phase-1)
- `templates/ai_advisor.html` Overview tab + `database.get_latest_market_prism_summary()` (Cycle-5)
- `app.py` — running daemon at :8090 (live render check)

## Design-System Mapping

N/A — backend feature, no UI surface. (All 10 are backend/infra; the Cycle-5 Market Prism Overview UI already shipped separately.)

## Edge Cases

- **Run errors or produces degenerate report:** that is a Phase-2 defect — loop back, do not paper over it. Per feedback: "verify a feature WORKS live, not just tests-green." Do not declare Phase 3 complete on a `limited-inputs` stub.
- **Overview tab renders incorrectly:** PM must actually look at the screenshot before claiming correctness. A green test suite is necessary but not sufficient for visual correctness.
- **Lenses return `limited-inputs`:** acceptable if some lenses are genuinely unavailable; degenerate synthesis is not acceptable. Distinguish clearly in the operator note.
- **Operator unavailable for sign-off:** Phase 4 dispatch is blocked until sign-off is received. PM does not unilaterally declare the run sufficient.
- **Run produces multiple `MARKET_PRISM` rows (e.g. a retry):** confirm exactly one row per `run_id`; duplicate rows are a Phase-2 defect to fix before declaring proof run complete.

## Security Considerations

- **Real API keys in use:** Opus 4.8 spend is real; Anthropic API key must be present and valid. Lens API calls (GDELT, FRED, SEC) are real. No keys are logged or echoed to the audit trail.
- **Data exposure:** the proof run produces real market analysis stored in the local state DB and rendered in the dashboard (local :8090 only). No external egress beyond the existing Cycle-4 pattern.
- **D-1 contract:** any errors during the proof run surface as `type(exc).__name__` only — no raw exception strings in the audit log, the Overview tab, or any Discord notification.
- **Authz / advisory-only:** the proof run does not touch `LIVE_EXECUTION`. The rendered Overview tab is advisory.
- **Prompt injection:** lens data from real APIs enters analyst prompts. The Phase-2 agent files treat all external data as untrusted text. No mitigation is added in Phase 3 beyond what Phase 2 established.

## Testing Strategy

No new automated tests in this phase — it is an observed operational run, not a codepath. The acceptance bar is the PM's direct observation and the operator's sign-off.

**Live functional verification protocol:**
1. Verify Phase-1 and Phase-2 deliverables are merged and clean on main (PM checks `git log` HEAD SHA).
2. Confirm the :8090 daemon is running and the Overview tab loads without error.
3. Run the Phase-2 orchestration with a fresh `run_id` using real APIs.
4. Query `get_prism_audit_for_run(run_id)` and inspect the full trail.
5. Open the Overview tab screenshot with the Read tool; describe what is rendered before asserting correctness.
6. Present both artifacts to the operator with the operator note.

## Decisions

| Decision | Rationale |
|----------|-----------|
| Phase 3 is a PM-observed run, not a team dispatch | The whole point is human oversight before blind automation; an unobserved "proof" is not a proof |
| Visual gate is mandatory (PM reads screenshot) | Per feedback: "Actually LOOK at the render" — a poll-test + 0 console errors is necessary but not sufficient |
| Phase 4 is hard-blocked on operator sign-off | The operator must see the real artifacts; PM cannot unilaterally clear this gate |
| Run errors loop back to Phase 2, not papered over | Per feedback: "verify a feature WORKS live" — a degenerate report is a Phase-2 defect, not a Phase-3 pass |

## Scope Boundaries

- **IN**: PM execution of the Phase-2 orchestration on real data; operator presentation of report + audit trail; operator sign-off gate; visual render check; operator note (lens coverage, debate summary, Opus spend)
- **OUT**: new code changes; automated tests; Phase-4 scheduling (blocked until this phase clears); changes to the Overview tab template; any reprocessing of historical runs

**Dependencies:** Phases 1 + 2 complete and merged. The :8090 daemon must be running for the live-render check. Operator must be available for sign-off.
