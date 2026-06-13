# Market Prism — Phase 1: Audit-Log DB Foundation

**Epic:** [Market Prism](market-prism-overview.md) · **Status:** 🟡 team spawned
(`prism-audit-foundation`, branch `cycle/prism-audit-foundation` off `df2d19e`), no commits yet.

## Goal

Lay the DB foundation so the Phase-2 agent team can persist a FULL deliberation trail: every
analyst + the synthesizer writes its own output, per phase, keyed to the nightly run — so each
`MARKET_PRISM` report is fully auditable ("exactly why it was built that way"). Backend only;
no UI in this phase.

## Acceptance criteria

1. **Migration 032** (additive, NULLable + DEFAULT, never destructive; wired into
   `database._MIGRATION_FILES` after 031) — new STATE-DB table `prism_audit_log`:
   - `id` INTEGER PK autoincrement
   - `run_id` TEXT NOT NULL — links all entries of one nightly run + the `MARKET_PRISM` report
   - `agent_role` TEXT NOT NULL — e.g. `technicals_analyst`, `sentiment_analyst`,
     `derivatives_analyst`, `macro_analyst`, `fundamentals_analyst`, `synthesizer`
   - `phase` TEXT NOT NULL — e.g. `initial_read`, `clarification`, `debate_round_1..3`,
     `synthesis`
   - `content` TEXT NOT NULL — the agent's verbatim output for that phase
   - `created_at` TEXT NOT NULL DEFAULT (datetime)
   - index on `run_id`
2. **Public accessors in `database.py`:**
   - `insert_prism_audit_entry(run_id, agent_role, phase, content) -> int` (returns row id;
     parameterized)
   - `get_prism_audit_for_run(run_id) -> list[dict]` (all entries for a run, ordered by id;
     typed dicts)
3. **`MARKET_PRISM` report links to its run:** the `MARKET_PRISM` `advisor_observation` carries
   `run_id` in `raw_response` (Cycle 4 writes `run_ts`; add/confirm a `run_id` field) so the
   report joins to its audit entries. Backward-compatible (existing rows without `run_id` still
   read fine).
4. **Agent-callable writer** so a Claude Code agent can write an audit entry from Bash:
   `python -m advisors.prism_audit_write --run-id <id> --role <r> --phase <p>` that reads
   `content` from **STDIN** (not an arg — content can be long/multiline; avoid arg-length
   limits) and calls `insert_prism_audit_entry`. Prints the new row id. Never raises uncaught
   (D-1: errors → type-only on stderr, non-zero exit).
5. Contracts: parameterized queries only; respects the pytest DB sentinel (tests set `DB_PATH`
   via conftest); no cross-DB joins.

## Team composition

`prism-audit-foundation` — quant-test-writer (lead) + sqlite-specialist (implementer) +
quant-code-reviewer + **doc-gen** (mandatory). Shared worktree, one branch, SendMessage
handoffs, Toxic Pair cycling.

## RED tests first

- migration 032 applies cleanly on a fresh DB + is idempotent in the migration runner
- insert+read round-trip; ordering by id; `run_id` grouping
- the CLI writer reads stdin, inserts, prints id, handles a missing/garbled arg gracefully
- `MARKET_PRISM` `raw_response` carries `run_id`
- no hardcoded values — assert shape/contract

## doc-gen (mandatory)

Document the new table + accessors + writer in `docs/generated/database.md` + `DECISIONS.md` +
the `database.py` Key-Files row in `.claude/CLAUDE.md` (note migration 032 + `prism_audit_log`).
Commit before cycle-complete.

## Hard rules

- Work EXCLUSIVELY in `alphabot-prism-foundation` (branch `cycle/prism-audit-foundation`).
  NEVER edit AlphaBotPM root files / `alphabot_state.db` / the :8090 daemon. NEVER merge to
  main — no override available to the team; commit ONLY on the cycle branch.
- TEST DISCIPLINE: `$env:DB_PATH=temp` before pytest; targeted (`tests/database`,
  `tests/ai_advisor`); `-n0` (`-o addopts= -p no:xdist`); ONE pytest at a time.
- Trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

## Pre-merge bar (PM)

PM independent `-n0` gate (`tests/database` + `tests/ai_advisor`, 0 new failures vs `df2d19e`).
Then PM merges via the private token. No live-render needed (backend only; live proof comes in
Phase 3). NOTE: rebase/re-fork consideration — main advanced to `d636ce3` (Cycle 5 surface)
after this branch forked from `df2d19e`; both are additive and migration 032 does not collide,
but PM verifies clean merge into `d636ce3`.

## Reference

`database.py` migration pattern (`_MIGRATION_FILES`, 001–031; 021-before-020 intentional),
`insert_advisor_observation` / `get_advisor_observations_for_role` as accessor patterns,
`advisors/lens_pipeline.py` (`MARKET_PRISM` `raw_response` shape). Next migration number = 032.
