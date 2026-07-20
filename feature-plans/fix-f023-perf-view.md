# Feature: Per-Symphony Performance View — ID/Name Mismatch Fix (F-023)
Status: ready
Created: 2026-07-20

## Summary
The Performance-tab "Per-symphony" scope returns "0 observations" / all-metrics-"--" for EVERY one of the 11 symphonies, behind the SAME "Insufficient history" banner used for genuinely sparse data — a whole operator capability is silently dead AND disguised as an honest empty state. Root cause: `/api/performance/symphonies` (`app.py:3383`, `api_performance_symphonies`, docstring wrongly says "symphony_ids") returns human-readable NAMES used as BOTH the picker's label AND its value; the picked NAME is sent as `symphony_id` (`performance.js:469`) into `analytics.get_symphony_bot_and_held_daily_returns` → `SELECT ... FROM shadow_history WHERE symphony_id = ?` — but `shadow_history.symphony_id` stores ONLY hash IDs, so the WHERE matches ZERO rows every time. Data is HEALTHY (7,330 real rows exist under the sample hash); it's a pure app-layer ID/name mismatch. Fix: `/api/performance/symphonies` returns `{id, name}` pairs (hash as VALUE, name as LABEL); the frontend sends the hash as `symphony_id`; and a genuine no-data state is DISTINGUISHED from a query/unknown-id error so a future mismatch can't masquerade as an empty state. The endpoint has TWO consumers (`performance.js` + `ai_advisor.js:568`) — both must handle the new shape.

## Acceptance Criteria
- [ ] AC-1: `GET /api/performance/symphonies` returns a list of `{id, name}` objects — `id` = the hash key used in `shadow_history.symphony_id`, `name` = the display label — NOT bare name strings. The docstring's wrong "symphony_ids" claim is corrected.
- [ ] AC-2: the Performance-tab picker (`static/performance.js`, populated ~:533) uses the hash as each option's VALUE and the name as its LABEL; the picked value read at `performance.js:469` (sent as `symphony_id`) is the hash.
- [ ] AC-3: `GET /api/performance?scope=symphony&symphony_id=<a real hash>` returns NON-ZERO `observation_count` for a symphony that has shadow_history rows — the capability actually works (RED today: sending the NAME returns 0).
- [ ] AC-4: a genuine no-data symphony (real hash, <threshold rows) renders the honest "Insufficient history" empty state, BUT an unknown/mismatched `symphony_id` (no matching hash at all) is DISTINGUISHED from it — a future name/id mismatch surfaces as a distinct state, never masquerades as "Insufficient history."
- [ ] AC-5 (blast radius): the OTHER consumer `static/ai_advisor.js:568` (which also fetches `/api/performance/symphonies`) is updated for the new `{id, name}` shape with no regression to its behavior.
- [ ] AC-6 (regression guard): the Performance-tab AGGREGATE scope renders unchanged; the `analytics` query itself is byte-unchanged (it already correctly matches by hash — it simply now receives the hash instead of a name).

## Architecture
- `app.py:3383` `api_performance_symphonies()` — build `{id, name}` from the same source it currently pulls names from (the hash is available where the name is; the endpoint currently drops the ID). Fix the docstring.
- `static/performance.js` (~:469 read value, ~:533 populate options) — option value = hash, label = name.
- `static/ai_advisor.js:568` — adapt to the `{id, name}` shape (whatever it does with the list).
- `analytics.get_symphony_bot_and_held_daily_returns` / `SELECT ... WHERE symphony_id = ?` (`analytics.py:~1454`) — UNCHANGED; it correctly matches by hash. The `/api/performance?scope=symphony` route (app.py:~3298) distinguishes 0-rows-for-a-real-hash (no-data) from unknown-id (error) for AC-4.
- Provenance: the hash convention is consistent everywhere else (post-mortem `shadow_divergence.by_symphony` is hash-keyed) — only this endpoint translated to names and dropped the ID.

## Edge Cases
- Symphony with a real hash but <threshold rows → honest "Insufficient history" (unchanged).
- Unknown/mismatched `symphony_id` (not any real hash) → a DISTINCT error/invalid state, NOT "Insufficient history" (AC-4).
- Aggregate scope → unchanged.
- A symphony NAME with special chars/spaces → now moot (we send the hash, not the URL-encoded name).
- Empty symphony list → honest empty picker, no crash.

## Security Considerations
- No new external input; the `symphony_id` sent is server-provided (from the endpoint's own `{id,name}` list). Parameterized query (no injection). No secrets in the response. The endpoint stays read-only.

## Testing Strategy
- **RED (quant-test-writer):** (1) `/api/performance/symphonies` returns `{id,name}` objects, `id` being a hash (not a name) — RED today (returns names); (2) `/api/performance?scope=symphony&symphony_id=<known hash from a fixture>` returns non-zero observations, vs sending the NAME returns 0 (the mismatch, pinned); (3) unknown id → distinct-from-empty-state (AC-4); (4) aggregate scope unchanged (AC-6); (5) `node --check` on the changed JS (performance.js + ai_advisor.js) — extend the parametrized `tests/js_syntax/` module, do NOT add per-file checks. Fixtures = a shadow_history hash with known rows (schema-derived / a small seeded set), assert non-zero/shape not hardcoded metric values. App test client + analytics; `-n0`.
- **BLAST-RADIUS grep (cycle 1-2-3 lesson):** grep the whole tree for `/api/performance/symphonies` consumers (found: performance.js + ai_advisor.js) and for existing tests asserting the OLD names-list response shape — update them.
- Both ruff gates + JS syntax before GREEN. PM live E2E (render harness) will confirm the picker sends the hash + a pick yields real numbers.

## Decisions
| Decision | Rationale |
|----------|-----------|
| Return `{id, name}` (hash value, name label) | The hash is the canonical `shadow_history` key; server-side name→hash translation would be fragile and duplicate the mapping. Send the ID the query actually needs. |
| Update BOTH consumers to one response shape | performance.js + ai_advisor.js share the endpoint; a single `{id,name}` shape keeps them from diverging. |
| Distinguish no-data from query-error (AC-4) | The core defect is that a broken capability masqueraded as an honest empty state; the fix must make a real mismatch surface, not just fix today's mapping. |
| F-024 (double-"+" glyph, same file) kept OUT | It's a separate LOW cosmetic in performance.js:77/90; fold into a later cosmetic cluster, not this HIGH capability fix (scope discipline). |

## Scope Boundaries
- **IN:** `/api/performance/symphonies` → `{id,name}` (AC-1); picker hash-value (AC-2); the working per-symphony query path (AC-3); no-data-vs-error distinction (AC-4); the ai_advisor.js consumer update (AC-5).
- **OUT:** F-024 (double-+ glyph); any change to the `analytics` per-symphony query internals (it's correct); F-020 History drill-down; other findings; any engine/trade change.
