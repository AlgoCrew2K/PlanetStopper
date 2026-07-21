# Feature: Ops/Observability Cluster + Residuals (F-1, F-005, F-020, F-010, F-030, F-003-residual, suggest-hash)
Status: ready
Created: 2026-07-21

## Summary
The FINAL confidence-program cycle: the ops/observability findings plus two tracked residuals. Audit source: `docs/audit/confidence-program/INSTITUTIONAL-READINESS-REPORT.md` @ branch `audit/confidence-program` (readable at `.claude/worktrees/confidence-program/...`) — the test-writer MUST quote each finding's register row in the RED plan for the three findings whose specs live mainly in the report (F-020, F-010, F-030). NO engine/trade-behavior change anywhere; F-010 is the only item allowed to TOUCH an engine-adjacent file and ONLY with a provably logging-only diff.

- **F-1 (efficiency, PM-REPRODUCED 157 SQLite connects / 1.4s per `/api/state` poll):** the dashboard route's per-symphony enrichment opens a fresh SQLite connection at ~8 connect-sites + 3 per-symphony metric functions inside the loop. Fix: ONE shared read-only connection per request (or equivalent batching) threaded through the enrichment path — display path only; the engine's own DB access untouched. Success = connection count per poll drops from ~157 to a small constant (test pins the mechanism, a counting seam; no wall-clock assertions — flaky).
- **F-005 (LOW):** `_AUTH_EXEMPT_ENDPOINTS` exempts `/health` but NO such route exists (CLAUDE.md's claim is currently false; no liveness probe possible). Fix: minimal unauthenticated `GET /health` returning `{status, daemon_started_at, last_successful_cycle_at}` — read-only, no secrets, no DB write.
- **F-020 (LOW, quote the register row):** no exit drill-down — the History surface gives the operator no way to inspect an individual exit's details. Fix per the report's remediation line — MINIMAL: an expandable per-exit detail row (or equivalent) sourcing EXISTING fields from the already-loaded history payload; no new analytics, no schema change.
- **F-010 (INFO→observability, quote the register row):** ~958 Composer read-timeout full tracebacks/month drown the journal. Fix: compact single-line structured log (counter/context) for the known read-timeout case, full traceback preserved for UNKNOWN exceptions. If the locus is an engine file: the diff must be provably logging-only (reviewer gates it line-by-line); retry/timeout VALUES unchanged.
- **F-030 (LOW, quote the register row):** advisory-DB writes lack a reconstructable audit trail. Fix per the report's remediation — MINIMAL: ensure each advisory write path records who/what/when sufficient to reconstruct (prefer EXISTING tables/patterns, e.g. the existing audit/observation conventions; schema change only if additive+NULLable per house rules, and only if genuinely unavoidable).
- **F-003 residual (MUST-CLOSE-BEFORE-LIVE):** in `perform_account_liquidation` (app.py, panic-stop path) the per-symphony `name = sym.get(...)` + `sell_url` setup sits OUTSIDE the per-symphony try — a non-dict `sym` still aborts the whole queue via the outer except, defeating #105's isolation. Fix: move the extraction INSIDE the per-symphony try; a malformed entry yields a per-symphony FAILED outcome, queue continues. HARD GUARD: no trade-behavior change (same symphonies/endpoint/payload/live_mode gating).
- **suggest-hash (BACKLOG item, pre-existing):** `POST /ai-advisor/suggest` (app.py:5796 area) never hash-resolves `composer_symphony_id` — fix to the 4-site both-sides `normalize_name` convention (app.py:4660/5011/5313/5784) consistent with the F-013 cycle's accept-path work; advisory-only.

## Acceptance Criteria
- [ ] AC-1 (F-1): per-`/api/state`-request SQLite connection count is a small constant (pinned via a counting seam/monkeypatched connect), NOT O(symphonies); response payload byte-equivalent for identical state (golden comparison); engine DB access untouched.
- [ ] AC-2 (F-005): `GET /health` returns 200 + honest fields unauthenticated; NOT in `_SETTINGS_WRITE_ALLOWLIST` scope; no secrets; CLAUDE.md's existing exempt-list claim becomes true (no CLAUDE.md edit needed unless wording requires — draft to PM if so).
- [ ] AC-3 (F-020): an individual exit's details are inspectable from History using existing payload fields; graceful when fields are missing; no new analytics computation; no schema change.
- [ ] AC-4 (F-010): the known Composer read-timeout logs ONE compact structured line (with a counter or equivalent aggregation context); unknown exceptions keep full tracebacks; retry/timeout constants byte-unchanged; if an engine file is touched, the diff is logging-only (reviewer-gated).
- [ ] AC-5 (F-030): every advisory-DB write path leaves a reconstructable trail (what/when/trigger); prefer existing tables/conventions; any schema change is additive+NULLable+DEFAULT.
- [ ] AC-6 (F-003 residual): a non-dict/malformed symphony entry in the panic-stop queue produces a per-symphony FAILED outcome and the queue CONTINUES (route-level test); zero change to the sell request construction for valid entries.
- [ ] AC-7 (suggest-hash): a hash-valued `composer_symphony_id` on `/ai-advisor/suggest` resolves per the both-sides convention; name-valued still works; mixed-case pinned (mirror the F-013 rider-test pattern).
- [ ] AC-8 (blast radius): `alpha_bot_execution.py`/`math_engine.py`/`autotuner.py` untouched EXCEPT an F-010 logging-only diff if its locus demands it; no `_SETTINGS_WRITE_ALLOWLIST` change; no LIVE_EXECUTION interaction; full changed-surface suites green `-n0`.

## Architecture
- F-1: app.py dashboard/state enrichment path + the analytics per-symphony metric fns' connection handling (pass a connection/seam; do NOT restructure the engine's `database.py` access patterns).
- F-005: app.py route + `_AUTH_EXEMPT_ENDPOINTS` already lists it — route only.
- F-020: templates/history.html + static JS + (if needed) the existing `GET /api/history` payload — existing fields only.
- F-010: the Composer read-timeout log site (locate precisely; quote file:line in the RED plan).
- F-030: the advisory write helpers (database.py insert_advisor_observation / related) — trail via existing conventions.
- F-003 residual + suggest-hash: app.py, surgical.
- PM gate: E2E = /health probe + /api/state connection-count seam check + a History drill-down render + panic-stop malformed-entry mock + suggest-hash route test; droplet deploy + restart + journal check.

## Edge Cases
- F-1: concurrent requests must not share the read-only connection unsafely (per-request scope, not module-global).
- F-005: daemon-degraded state still returns honestly (no fabricated "ok").
- F-020: exits missing optional fields render gracefully (em-dash convention).
- F-010: the compact line must not swallow DIFFERENT Composer errors (timeout-only match).
- F-030: trail writes must never raise into the advisory path (D-1 preserved).
- F-003: valid entries byte-identical request construction (pin it).

## Security Considerations
/health leaks no secrets/config; audit trail stores no credentials; all new routes read-only; suggest stays advisory + CSRF-protected as-is.

## Testing Strategy
RED per finding as specified in AC-1..AC-7 (route-level where routes are involved; counting-seam for F-1; register rows quoted for F-020/F-010/F-030 in the RED plan so the contract is audit-anchored). Standing rules: `-n0` only; never `-o addopts=""`; precise-regex JS pins; LF blobs; both ruff gates; old-behavior tests rewritten-not-deleted with why-comments; blast-radius grep for every changed field/consumer.

## Decisions
| Decision | Rationale |
|----------|-----------|
| One final cycle for all 7 | All are small, share surfaces (mostly app.py), and close the program; splitting would add 2+ gate round-trips for no risk reduction. |
| F-1 pins the MECHANISM not wall-clock | Timing assertions flake; connection-count via seam is deterministic. |
| F-010 logging-only, values unchanged | Observability fix; retry/timeout tuning would be a behavior change needing its own cycle. |
| F-020/F-030 minimal per report remediation | LOW findings; the bar is "close the register honestly," not build features. |

## Scope Boundaries
- IN: the 7 items above + tests + docs.
- OUT: F-024 glyph (stays deferred cosmetic — record as ACCEPTED-COSMETIC in the close-out); any engine/trade change; retry/timeout value tuning; new analytics; non-additive schema changes; performance work beyond F-1's connection sharing.
