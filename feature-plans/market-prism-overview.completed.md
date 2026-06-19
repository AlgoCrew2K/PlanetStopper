# Market Prism — Epic Overview (real collaborating agent team)

**Status:** 🟡 in progress (Phase 1 team spawned). **Exclusive focus** once Phase 1 work
actively begins: nothing else is worked on until the operator sees a real full report + logs.

## What it is

The Market Prism is the always-on, off-hours "overall market sentiment / overnight market
read" that the AI Advisor surfaces. It is **advise-only** and **never intraday** — a daily
off-hours read. The Cycle-5 Overview tab already renders the latest `MARKET_PRISM`
`advisor_observations` row (landed `d636ce3`); this epic replaces *how that row is produced*.

## Why a real agent team (not silos + a synthesizer)

Independent per-lens passes funnelled into one synthesizer just relocate ALL cross-lens
integration into the synthesizer — that is still siloed analysis. Genuine integration (macro
reframing technicals, sentiment tempering fundamentals, derivatives confirming/contradicting a
read) requires the analysts to actually **collaborate**: ask each other questions and, when
they genuinely disagree, debate. The PM could not honestly claim API-orchestration equals a
real Claude Code Agent Team on free-form emergent Q&A — so it is a real team.

## Locked design (operator-confirmed 2026-06-13)

- **Team per nightly run:** one ANALYST agent per lens — **technicals, sentiment, derivatives,
  macro, fundamentals** — each pulls its lens data (from the Cycle-4 data layer / Epic B
  producers) and reasons about its own domain. Plus a **SYNTHESIZER lead** that integrates the
  clarified/debated views into the overnight report and writes the `MARKET_PRISM` observation.
- **Model:** analysts + synthesizer run on **Opus 4.8**. (Haiku in Cycle 4 was a test
  placeholder — "use opus agents for this work.") Daily Opus multi-agent spend is accepted.
- **Collaboration protocol:**
  1. **Clarifying questions flow FREELY** between agents at any time and are
     **debate-round-agnostic** — clarifications do NOT count as debate.
  2. **Debate happens ONLY if there is a genuine reason** (real disagreement on the read).
     When it does, it is bounded to **UP TO 3 ROUNDS**. No conflict → no debate → straight to
     synthesis.
  3. The synthesizer integrates the clarified/debated views into one report.
- **No ranked verdict for candidates** (that is the Strategy/Swap surfaces' job, not Prism).
  The Prism produces a market *read*; it never suggests defunding ("that's my job" — operator).
- **Per-agent DB audit log (MANDATORY):** EVERY member (each analyst + synthesizer) writes its
  OWN output to `prism_audit_log`, keyed to the run, capturing each phase (initial read /
  clarifying Q&A / each debate round / synthesis). For each nightly report we can audit
  EXACTLY why it was built that way — the full deliberation trail. The `MARKET_PRISM`
  observation row links to its `run_id`.
- **Execution model (hard constraint):** an Agent Team is a Claude Code construct — the Flask
  daemon CANNOT spawn one. So the Prism is NOT a daemon Python job. It is a **scheduled Claude
  Code session** that each off-hours run spins up the Prism team FRESH, runs it to completion
  (clarify → conditional debate → synthesize → write the `MARKET_PRISM` row), and exits. A
  fresh short-lived team per run avoids marathon-session compaction/husk fragility.
- **Prove it observed first:** build it, then run it ONCE under PM observation on real data →
  produce a REAL full report + the per-agent audit logs, show the operator, BEFORE wiring any
  unattended/blind schedule (with a graceful fallback when a run errors).

## Phase sequencing (each phase is its own feature file)

| Phase | Deliverable | File | Gate to advance |
|-------|-------------|------|-----------------|
| 1 | Audit-log DB foundation (migration 032, accessors, agent-callable CLI writer, `run_id` on `MARKET_PRISM`) | [phase1](market-prism-phase1-audit-log-foundation.md) | `-n0` green, PM-merged |
| 2 | Collaborating analyst team + orchestration (the real team, protocol, each writes audit log) | [phase2](market-prism-phase2-collaborating-analyst-team.md) | observed dry/real run produces a report + complete audit trail |
| 3 | Observed proof run — real report + per-agent logs shown to operator | [phase3](market-prism-phase3-observed-proof-run.md) | operator sees the artifacts |
| 4 | Unattended scheduling (daemon→headless `claude` or cron) + graceful fallback | [phase4](market-prism-phase4-unattended-scheduling.md) | runs blind nightly with fallback |

## Cross-cutting hard rules

- The Cycle-4 lens fetchers (GDELT/FRED/SEC in `advisors/lens_pipeline.py`) **stay** as the
  data the analysts pull — they are the data layer, not the analysts. Epic B enriches them.
- Off-execution-path, advisory-only, never touches `LIVE_EXECUTION`.
- D-1 contract everywhere: error paths surface `type(exc).__name__` only.
- Graceful fallback: a failed run must not crash the daemon and must leave a clear
  `verdict="limited-inputs"` (or similar) state, never a half-written report.

## Related memory

`project-market-prism-agent-team-design`, `feedback-nightly-and-n0-are-hard-premerge-gates`,
`project-vision-portfolio-command-center`.
