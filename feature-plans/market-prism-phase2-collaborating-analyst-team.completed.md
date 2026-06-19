# Feature: Market Prism Phase 2 — Collaborating Analyst Team + Orchestration
Status: ready
Created: 2026-06-13

## Summary

Builds the real collaborating Claude Code Agent Team that produces the overnight market read: per-lens Opus 4.8 analysts that pull their lens data, produce an initial read, freely exchange clarifying questions, and conditionally debate (up to 3 rounds, only on genuine disagreement). A synthesizer lead integrates the views into a single `MARKET_PRISM` observation. Every member writes its phase outputs to `prism_audit_log` via the Phase-1 CLI writer, creating a fully auditable deliberation trail keyed to a `run_id`. This phase delivers two buildable artifacts: (1) project-local agent role files for each analyst and the synthesizer, and (2) a documented, PM-driveable run orchestration contract.

## Acceptance Criteria

- [ ] AC-1: A single run produces exactly one `MARKET_PRISM` `advisor_observations` row carrying a `run_id` that links to the audit trail.
- [ ] AC-2: `get_prism_audit_for_run(run_id)` returns a complete deliberation trail: every analyst's `initial_read`, every clarification exchanged (`phase=clarification`), every debate round that occurred (`phase=debate_round_1..3`), and the `synthesis`. No phase is missing for any member that participated.
- [ ] AC-3: When analysts genuinely agree, NO debate rounds appear in the trail (protocol is respected — debate does not fire without genuine disagreement).
- [ ] AC-4: When analysts disagree, at most 3 debate rounds appear in the trail and the synthesis reflects the resolution.
- [ ] AC-5: Clarifications are tagged as `phase=clarification` and are distinct from debate rounds in the audit trail.
- [ ] AC-6: Graceful fallback — if a lens analyst cannot get data or errors, the run still completes with a `verdict="limited-inputs"`-style report; the failure is recorded in the audit log (D-1: `type(exc).__name__` only); the `MARKET_PRISM` row is never half-written.
- [ ] AC-7: The synthesis is a real integrated read reflecting cross-lens reasoning — not a concatenation of analyst silos.
- [ ] AC-8: Any new Python orchestration glue (if introduced) is covered by Toxic Pair TDD tests (new codepath rule).

## Architecture

**Deliverable 1 — Agent role files (`.claude/agents/`):**
- `prism-technicals-analyst.md` — price/trend/breadth technicals; pulls from Cycle-4 data layer
- `prism-sentiment-analyst.md` — GDELT tone / news sentiment; pulls from Cycle-4 data layer
- `prism-derivatives-analyst.md` — options/vol/positioning signals; pulls from Cycle-4 data layer
- `prism-macro-analyst.md` — FRED macro series; pulls from Cycle-4 data layer
- `prism-fundamentals-analyst.md` — SEC fundamentals; pulls from Cycle-4 data layer
- `prism-synthesizer.md` (lead) — integrates clarified/debated views; writes the `MARKET_PRISM` observation row carrying `run_id`

Each agent's operating rules encode: pull its lens data via `advisors/lens_pipeline.py` Cycle-4 producers; produce `initial_read`; ask clarifying questions freely (debate-agnostic); enter debate ONLY on genuine disagreement, ≤3 rounds; write every phase output to `prism_audit_log` via `python -m advisors.prism_audit_write --run-id <run_id> --role <role> --phase <phase>` (piping output to STDIN). Model: Opus 4.8 for all members.

**Deliverable 2 — Run orchestration contract:**
A documented runbook (+ thin Python glue if needed) that for one run:
1. Generates a `run_id` (UUID4 or ISO-ms timestamp)
2. Spins up the team fresh (new short-lived session per run — avoids marathon-session compaction/husk fragility)
3. Each analyst pulls its data + posts `initial_read` (→ audit log)
4. Free clarifying Q&A among analysts (→ audit log, `phase=clarification`)
5. Conditional debate (≤3 rounds, only where genuine disagreement exists; → audit log, `phase=debate_round_N`)
6. Synthesizer integrates → writes the `MARKET_PRISM` observation row carrying `run_id`
7. Team tears down cleanly

The orchestration is PM-driveable for the Phase-3 observed proof run and later shellable from a headless `claude` session for Phase 4.

**Integration points:**
- `advisors/lens_pipeline.py` — data layer providing the lens data that analysts pull; unchanged
- `advisors/prism_audit_write.py` (Phase-1 CLI) — the write path for all audit entries
- `database.py` `insert_prism_audit_entry` / `get_prism_audit_for_run` (Phase-1 accessors)
- `templates/ai_advisor.html` / `database.get_latest_market_prism_summary()` (Cycle-5 Overview tab) — reads the `MARKET_PRISM` row this phase writes; no changes to the template in this phase

## Design-System Mapping

N/A — backend feature, no UI surface. (All 10 are backend/infra; the Cycle-5 Market Prism Overview UI already shipped separately.)

## Edge Cases

- **Lens data unavailable:** a lens analyst must not crash when its Cycle-4 producer returns an empty/unavailable signal. It records the unavailability in its `initial_read` audit entry (`type(exc).__name__` only for errors) and passes a `limited-inputs` flag to the synthesizer.
- **All lenses unavailable:** synthesizer still produces a `MARKET_PRISM` row with `verdict="limited-inputs"` and the Overview tab degrades informatively.
- **No genuine disagreement:** the protocol explicitly skips debate. The synthesizer must not open debate rounds when analysts converge — the audit trail must not contain spurious `debate_round_*` entries.
- **Debate exceeds 3 rounds:** the protocol hard-caps at 3. The synthesizer closes debate and synthesizes on available information even if disagreement persists.
- **Half-written MARKET_PRISM row:** must not exist. The synthesizer writes the observation row atomically only after integration is complete.
- **Team member crash mid-run:** the orchestration runbook must handle a member that fails to respond; the run completes with `limited-inputs` for that lens, never a hung team.
- **SendMessage inbox lag:** per feedback (verify-git-over-lagging-teammate-relays), verify repo state with git before acting on teammate messages. The runbook documents this constraint.
- **Debate trigger ambiguity:** the synthesizer detects materially divergent reads and explicitly opens a debate round; analysts may also flag disagreement. [PM-ASSUMED] Exact heuristic refined after Phase-3 observed run.
- **Clarification routing:** clarifications are broadcast via SendMessage (visible to the team, all logged) rather than point-to-point. [PM-ASSUMED] Refine after Phase-3 if needed.

## Security Considerations

- **Prompt injection into LLM analysts:** analysts receive lens data (from `advisors/lens_pipeline.py`) and peer messages (via SendMessage). Lens data originates from external APIs (GDELT, FRED, SEC); any attacker-controlled content in that data could influence analyst prompts. [PM-ASSUMED] Lens producers already validate/sanitize data shapes; Phase-2 analysts treat lens data as untrusted input text, not executable code.
- **Data exposure:** `MARKET_PRISM` content is advisory and non-sensitive market analysis. Analyst outputs are stored in `prism_audit_log` (local state DB only). D-1 contract applies: no `str(exc)` echoed to routes, Discord, or UI.
- **Authz / advisory-only:** off-execution-path; never touches `LIVE_EXECUTION`. The orchestration writes to `advisor_observations` and `prism_audit_log` only. No Flask route in this phase.
- **API key handling:** analysts use the project `ANTHROPIC_API_KEY` (Opus 4.8). The key must be present in the team worktree `.env` (per feedback: worktree kickoffs must copy `.env`). No key is echoed to logs or stored in the audit log.
- **Abuse / rate-limiting:** Opus 4.8 multi-agent spend is operator-accepted. Bounded debate (≤3 rounds) and bounded clarification protocol prevent runaway spend. [PM-ASSUMED] Per-run spend is recorded in the audit log or a run-summary entry.
- **Input validation:** no new Flask routes; no user-supplied input enters this phase. Lens data is pre-validated by Cycle-4 producers.

## Testing Strategy

**Scope:** agent role files and the orchestration runbook are not Python codepaths — they are not directly pytest-testable. Any new Python orchestration glue IS a new codepath and requires Toxic Pair TDD tests.

**If Python glue is introduced (e.g. `advisors/prism_orchestrator.py`):**
- `tests/ai_advisor/test_prism_orchestrator.py` — `run_id` generation produces a unique non-empty string per call; the orchestrator calls `insert_prism_audit_entry` via the CLI writer (subprocess) in the correct order; graceful fallback path (simulated lens failure) produces a `limited-inputs` verdict without raising; tests mock external API calls and `subprocess.run` for `prism_audit_write` — no real Opus calls in unit tests.

**Fixture provenance:** mocked analyst responses are schema-derived (assert structure/phase tags), not captured from live Opus runs. Tests never assert specific market text — assert presence/shape of audit entries.

**Run protocol:** `DB_PATH` set via `tests/conftest.py`; targeted run: `pytest tests/ai_advisor -n0 -o addopts= -p no:xdist`. Lens data mocked via fixtures. No live Opus calls in unit tests. Live functional verification is Phase 3 (PM-observed run on real data with real Opus spend).

## Decisions

| Decision | Rationale |
|----------|-----------|
| Per-lens agent files in `.claude/agents/` | Real project-local specialists (not general-purpose); PM dispatches the right model (Opus 4.8) per model-routing rules; agent files encode timeless operating rules |
| Model: Opus 4.8 for all members | Operator directive 2026-06-13: "use opus agents for this work"; Haiku in Cycle 4 was a test placeholder; daily multi-agent Opus spend is accepted |
| Clarifications broadcast (all team members see them) | Full deliberation transparency; all clarifications logged to `prism_audit_log` for auditability; point-to-point would create invisible sub-deliberations |
| Debate trigger: synthesizer detects divergence, analysts may also flag | [PM-ASSUMED] Simplest reliable heuristic; refine after Phase-3 |
| Fresh short-lived team per run | Avoids marathon-session compaction/husk fragility; clean state each nightly run |
| Any new Python glue uses Toxic Pair TDD | All new codepaths in this project require it; orchestration glue is a new codepath |

## Scope Boundaries

- **IN**: project-local agent role files for 5 analysts + 1 synthesizer; run orchestration runbook; any Python orchestration glue (if minimal); Toxic Pair TDD tests for any new Python glue; doc-gen updates to `docs/generated/` + `DECISIONS.md` + `CLAUDE.md` key-files
- **OUT**: Phase-1 DB foundation (prerequisite); Phase-3 observed proof run; Phase-4 unattended scheduling; Epic B lens data enrichment (analysts run on existing Cycle-4 data layer; Epic B raises quality, not a Phase-2 blocker); changes to the Cycle-5 Overview tab template; any re-ranking/scoring of analysts (Prism produces a market read, not candidate rankings)

**Dependencies:** Phase 1 complete and merged (audit-log table + CLI writer + `run_id` on `MARKET_PRISM`). Epic B (lens data producers) improves analyst read quality but is NOT a hard blocker — analysts run `limited-inputs` where a producer is missing.

**Team note:** agent-design work is `opus` per model-routing (shapes the whole nightly cycle). PM authors/dispatches the agent role files. Any new Python glue is built by a small TDD team: implementer + quant-code-reviewer + doc-gen. Hard rules: off-execution-path, advisory-only; never touches `LIVE_EXECUTION`; D-1 error contract throughout; no daemon-spawned team (Claude Code construct only); every member writes its own audit-log entries — a member that produces output without an audit entry is a defect.
