# Market Prism — Phase 2: Collaborating Analyst Team + Orchestration

**Epic:** [Market Prism](market-prism-overview.md) · **Status:** 🔴 blocked by Phase 1 (needs
the audit-log foundation to exist).

## Goal

Build the **real collaborating Claude Code Agent Team** that produces the overnight market read:
per-lens Opus analysts that pull their data, reason, freely ask each other clarifying questions,
debate only on genuine disagreement (≤3 rounds), and a synthesizer that integrates one report
and writes the `MARKET_PRISM` observation. EVERY member writes its phase outputs to
`prism_audit_log`.

## What this phase delivers

This is **orchestration + agent role contracts**, not a Toxic-Pair codepath in the usual sense.
There are two distinct buildable artifacts:

### Deliverable 1 — Analyst + synthesizer agent role definitions (`.claude/agents/`)
- One project-local agent per lens, each bound to its data source and reasoning charter:
  - `prism-technicals-analyst` — price/trend/breadth technicals
  - `prism-sentiment-analyst` — GDELT tone / news sentiment
  - `prism-derivatives-analyst` — options/vol/positioning signals
  - `prism-macro-analyst` — FRED macro series
  - `prism-fundamentals-analyst` — SEC fundamentals
  - `prism-synthesizer` (lead) — integrates, writes the `MARKET_PRISM` row
- Each agent's operating rules MUST encode: pull its lens data via the Cycle-4 producers;
  produce an `initial_read`; ask clarifying questions freely (debate-agnostic); enter debate
  ONLY on genuine disagreement, ≤3 rounds; **write every phase output to `prism_audit_log`**
  via `python -m advisors.prism_audit_write` (the Phase-1 CLI) with the run's `run_id`.
- Model: Opus 4.8.

### Deliverable 2 — Run orchestration contract
- A documented, repeatable procedure (a runbook + any thin Python glue) that, for one run:
  1. generates a `run_id`,
  2. spins up the team fresh,
  3. each analyst pulls data + posts `initial_read` (→ audit log),
  4. free clarifying Q&A,
  5. conditional debate (≤3 rounds) only where genuine disagreement exists,
  6. synthesizer integrates → writes the `MARKET_PRISM` observation row carrying `run_id`,
  7. team tears down.
- The orchestration must be **driveable by the PM directly** for the Phase-3 observed proof,
  and later shellable from a headless `claude` session for Phase 4.

## Acceptance criteria

1. A run produces exactly one `MARKET_PRISM` `advisor_observations` row carrying a `run_id`.
2. `get_prism_audit_for_run(run_id)` returns a COMPLETE trail: every analyst's `initial_read`,
   every clarification exchanged, every debate round that occurred, and the `synthesis`.
3. When analysts genuinely agree, NO debate rounds appear in the trail (protocol respected).
4. When they disagree, ≤3 debate rounds appear and the synthesis reflects the resolution.
5. Clarifications appear in the trail tagged as `clarification`, distinct from debate rounds.
6. Graceful fallback: if a lens analyst cannot get data or errors, the run still completes with
   a `verdict="limited-inputs"`-style report and the failure is recorded in the audit log
   (D-1: type-only) — never a crash, never a half-written `MARKET_PRISM` row.
7. The synthesis is a real integrated read, not a concatenation of silos.

## Team / approach

This is agent-design + orchestration work — `opus` for the agent-design (shapes the whole
nightly cycle). The PM authors/dispatches the agent role files (agent design is `opus` per
model-routing) and the thin orchestration glue via a small team (implementer + reviewer +
doc-gen). Any new Python glue is a new codepath → Toxic Pair TDD for that glue.

## Open questions (non-blocking — PM has defensible defaults)

- Exact debate-trigger heuristic (how an analyst signals "genuine disagreement"). Default:
  synthesizer detects materially divergent reads and explicitly opens a debate round; analysts
  may also flag. Refine after the Phase-3 observed run.
- Whether clarifications are point-to-point (analyst→analyst) or broadcast. Default: visible to
  the team (SendMessage), all logged.

## Dependencies

- **Phase 1** (audit-log table + CLI writer + `run_id` on `MARKET_PRISM`).
- **Epic B** (lens data producers) improves the *quality* of analyst reads but is NOT a hard
  blocker — analysts can run on the existing Cycle-4 data layer with `limited-inputs` where a
  producer is missing. Build Phase 2 against what exists; B enriches later.

## Hard rules

- Off-execution-path, advisory-only; never touches `LIVE_EXECUTION`.
- D-1 error contract throughout. No daemon-spawned team (Claude Code construct only).
- Every member writes its own audit-log entries — a member that produces output without an
  audit entry is a defect.
