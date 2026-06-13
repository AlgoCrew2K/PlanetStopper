# Feature: Market Prism Phase 1 — Audit-Log DB Foundation
Status: ready
Created: 2026-06-13

## Summary

Lays the SQLite audit-log foundation that the Phase-2 collaborating analyst team requires to persist a full deliberation trail. Every analyst and the synthesizer writes its own output per phase, keyed to the nightly run via a `run_id`, so each `MARKET_PRISM` report is fully auditable. Adds migration 032, two public accessors in `database.py`, a `run_id` field on `MARKET_PRISM` observations, and an agent-callable CLI writer so team members can write audit entries from Bash. Backend only; no UI in this phase.

## Acceptance Criteria

- [ ] AC-1: Migration 032 (additive, NULLable + DEFAULT, never destructive) creates table `prism_audit_log` in the state DB with columns: `id INTEGER PK autoincrement`, `run_id TEXT NOT NULL`, `agent_role TEXT NOT NULL`, `phase TEXT NOT NULL`, `content TEXT NOT NULL`, `created_at TEXT NOT NULL DEFAULT (datetime('now'))`. An index exists on `run_id`. Migration is wired into `database._MIGRATION_FILES` after 031 and applies cleanly on a fresh DB; the migration runner is idempotent (`CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`).
- [ ] AC-2: `database.insert_prism_audit_entry(run_id, agent_role, phase, content) -> int` inserts a row using parameterized queries and returns the new row `id`.
- [ ] AC-3: `database.get_prism_audit_for_run(run_id) -> list[dict]` returns all entries for a run ordered by `id` as typed dicts; returns `[]` when no entries exist for the given `run_id`.
- [ ] AC-4: The `MARKET_PRISM` `advisor_observations` row written by `advisors/lens_pipeline.py` carries a `run_id` field in `raw_response`. Existing rows without `run_id` continue to read correctly (backward-compatible). The `run_id` value matches the audit trail entries for the same run.
- [ ] AC-5: `python -m advisors.prism_audit_write --run-id <id> --role <role> --phase <phase>` reads `content` from STDIN (not a CLI arg), calls `insert_prism_audit_entry`, and prints the new row id to stdout. Never raises uncaught — errors surface as `type(exc).__name__` on stderr with non-zero exit (D-1 contract).
- [ ] AC-6: Tests use parameterized queries only, respect the pytest DB sentinel (`DB_PATH` set via `tests/conftest.py`), and make no cross-DB joins. Tests assert shape/contract (insert+read round-trip, ordering by id, `run_id` grouping) — no hardcoded content values.

## Architecture

**Files changed:**
- `database.py` — add migration 032 wire-up in `_MIGRATION_FILES` (after 031); add `insert_prism_audit_entry` and `get_prism_audit_for_run` accessors following the pattern of `insert_advisor_observation` / `get_advisor_observations_for_symphony`
- `migrations/032_prism_audit_log.sql` — new migration file; additive table + index; never destructive
- `advisors/lens_pipeline.py` — add `run_id` to the `raw_response` dict written on every non-dry_run `MARKET_PRISM` observation (alongside the existing `run_ts` field)
- `advisors/prism_audit_write.py` — new CLI entry-point module (`python -m advisors.prism_audit_write`); reads STDIN, delegates to `insert_prism_audit_entry`, prints row id; uses `if __name__ == "__main__"` guard

**Data flow:**
1. Phase-2 orchestration generates a `run_id` (UUID4 or ISO-ms timestamp string) at run start.
2. Each analyst calls `python -m advisors.prism_audit_write --run-id <run_id> --role <role> --phase <phase>` piping its output to STDIN after each phase.
3. The synthesizer writes with `phase=synthesis`.
4. `lens_pipeline.py` writes the `MARKET_PRISM` `advisor_observations` row with `run_id` in `raw_response`.
5. `get_prism_audit_for_run(run_id)` returns the complete ordered trail for any given run.

**Integration points:** `database._MIGRATION_FILES` ordering (021-before-020 intentional — new 032 appends after 031 without disturbing that ordering). `advisor_observations` table is unchanged; the existing `get_latest_market_prism_summary()` accessor remains intact.

## Design-System Mapping

N/A — backend feature, no UI surface. (All 10 are backend/infra; the Cycle-5 Market Prism Overview UI already shipped separately.)

## Edge Cases

- **Long agent content:** `content` is TEXT (SQLite has no length cap); CLI writer reads STDIN to avoid OS arg-length limits on large multi-paragraph deliberations.
- **Missing or garbled CLI args:** `prism_audit_write` handles missing `--run-id`, `--role`, or `--phase` gracefully — `type(exc).__name__` on stderr, non-zero exit, no traceback. Empty STDIN is valid (stores empty string).
- **Backward compatibility:** existing `MARKET_PRISM` rows that pre-date this feature have no `run_id` in `raw_response`. `get_latest_market_prism_summary()` does not read `run_id`, so existing readers are unaffected.
- **Migration idempotency:** `CREATE TABLE IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS` prevents errors on re-application.
- **Concurrent writes:** SQLite WAL mode serializes writes; the CLI writer is short-lived per invocation — no persistent connection contention.
- **`run_id` collision:** two runs sharing a `run_id` would merge their audit entries under `get_prism_audit_for_run`. [PM-ASSUMED] `run_id` generation is Phase-2 responsibility; Phase 1 stores whatever it receives.

## Security Considerations

- **Input validation / SQL injection:** all DB writes use parameterized queries (`?` placeholders). STDIN content is stored verbatim and never eval'd or templated — parameterization prevents injection.
- **Prompt injection into LLM analysts:** Phase 1 writes content, does not re-feed it into prompts. Phase-2 is responsible for sanitizing audit content before passing back to Claude. Out of scope here.
- **Data exposure:** audit content is stored in the local state DB only. No Discord, no UI route, no external egress in Phase 1. CLI writer never echoes `str(exc)` — D-1: type name only on stderr.
- **Authz / advisory-only:** no Flask route added. `LIVE_EXECUTION` is not read or written. CLI writer is callable only by agent team members with shell access to the worktree.
- **API key handling:** no external API calls in Phase 1. `prism_audit_write` calls only `database.insert_prism_audit_entry`.
- **Abuse / rate-limiting:** N/A — no network calls, no retries. CLI writer is invoked per-phase by bounded agent protocol.

## Testing Strategy

**New test files:**
- `tests/database/test_prism_audit_log.py` — migration applies on fresh DB; insert + read round-trip; ordering by id; `run_id` grouping; `[]` returned for unknown `run_id`; multiline content round-trips without truncation (assert `len(content) == len(original)`, not specific string)
- `tests/ai_advisor/test_prism_audit_write.py` — valid subprocess invocation with piped STDIN inserts a row and prints a numeric id; missing required arg exits non-zero with `type(exc).__name__` on stderr; empty STDIN inserts a row with `content = ""`

**Fixture provenance:** tests construct minimal valid inputs (not captured from a producer); `run_id` values are arbitrary strings chosen by the test. No hardcoded DB content values — assert presence/shape/types.

**Run protocol:** set `DB_PATH` to a temp path before pytest (via `tests/conftest.py` `pytest_configure`); targeted run: `pytest tests/database tests/ai_advisor -n0 -o addopts= -p no:xdist`. One pytest at a time. All tests rely on the `_isolate_db` autouse fixture. No live functional verification needed (pure backend; live proof is Phase 3).

## Decisions

| Decision | Rationale |
|----------|-----------|
| Migration 032 uses `CREATE TABLE IF NOT EXISTS` | Idempotency — migration runner re-applying on a partially-upgraded DB must not error |
| CLI writer reads content from STDIN, not a CLI arg | Agent outputs can be multi-KB multiline text; OS arg-length limits would truncate; STDIN is the safe choice |
| `run_id` is TEXT (not INTEGER FK) | Decoupled from `advisor_observations` PK; allows UUID4 or ISO-timestamp strings; joins are by value matching |
| No new Flask route in Phase 1 | Audit data consumed by team protocol (Phase 2) and PM inspection (Phase 3); a route is a Phase-3 concern if ever needed |
| D-1: CLI writer outputs `type(exc).__name__` on stderr, non-zero exit | Consistent with project-wide D-1 error contract; agent callers inspect exit code |

## Scope Boundaries

- **IN**: migration 032 `prism_audit_log` table + index; `insert_prism_audit_entry` + `get_prism_audit_for_run` in `database.py`; `advisors/prism_audit_write.py` CLI module; `run_id` field on `MARKET_PRISM` `raw_response`; tests for all of the above; doc-gen updates to `docs/generated/database.md` + `DECISIONS.md` + `CLAUDE.md` key-files row for `database.py` (noting migration 032 + `prism_audit_log`)
- **OUT**: Phase-2 agent role definitions and orchestration; Phase-3 observed proof run; any Flask UI route for audit data; schema changes post-Phase-1 (new columns go in a later migration); changes to `advisor_observations` table

**Team note:** `prism-audit-foundation` — quant-test-writer (lead) + sqlite-specialist (implementer) + quant-code-reviewer + doc-gen (mandatory). Shared worktree on branch `cycle/prism-audit-foundation`. Hard rules: NEVER edit AlphaBotPM root files / `alphabot_state.db` / the :8090 daemon; NEVER merge to main; commit ONLY on the cycle branch; set `DB_PATH=temp` before pytest; targeted `-n0`; trailer `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. Pre-merge bar: PM independent `-n0` gate (0 new failures vs fork-point). Note: main advanced to `d636ce3` after branch forked from `df2d19e`; migration 032 does not collide with Cycle-5 additions — PM verifies clean merge.

**Reference:** `database.py` migration pattern (`_MIGRATION_FILES`, 001–031; 021-before-020 intentional); `insert_advisor_observation` / `get_advisor_observations_for_symphony` as accessor patterns; `advisors/lens_pipeline.py` (`MARKET_PRISM` `raw_response` shape). Next migration number = 032.
