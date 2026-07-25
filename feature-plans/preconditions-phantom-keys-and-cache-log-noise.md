# Feature: Preconditions Phantom-Keys Filter + Cache-Miss Log Honesty
Status: ready
Created: 2026-07-25

## Summary
Two small hygiene fixes to existing codepaths, both found during PM live probing on 2026-07-24. (a) `GET /api/guard-alpha-preconditions` iterates the top level of `bot_state` as if every key were a symphony, so 5 non-symphony metadata keys (`date`, `last_execution_mode`, `last_market_close_snapshot`, `last_successful_cycle_at`, `post_mortem_run`) render as phantom INSUFFICIENT_DATA "symphonies" (16 rows served, 11 real). (b) `synthetic_history.get_cached_synthetic_history_only`'s 10-trading-day walk-back logs every plain missing-file probe as `WARNING "corrupt or unreadable cache file ... [Errno 2]"` — ~16 misleading warnings per call for what is an EXPECTED cache miss (the cache is written weekly, so most days the newest-date probes are simply absent). Neither fix changes any verdict math, cache semantics, or return values. No TDD Toxic Pair required (small fixes to existing codepaths — project exception class), but every behavior change is pinned by tests, and the full PR gate applies.

## Acceptance Criteria
- [ ] AC-1: `GET /api/guard-alpha-preconditions` returns ONLY real symphony entries — none of the 5 known non-symphony `bot_state` top-level keys ever appears as a symphony row, in either the response dict or any aggregate count.
- [ ] AC-2: the discrimination is STRUCTURAL (an entry qualifies as a symphony by the shape of its value in `bot_state` — recon the real droplet `bot_state` blob to pin the discriminator), NOT a hardcoded denylist of those 5 names — a future non-symphony metadata key must be excluded without a code change. A name denylist is an automatic review reject.
- [ ] AC-3: a REAL symphony with degenerate/missing sub-data still appears, with its existing honest degraded verdicts — the filter may only exclude non-symphony entries, never degrade real ones (no-self-regression).
- [ ] AC-4: a `bot_state` containing ONLY metadata keys yields the route's existing honest empty state (zero symphony rows, HTTP 200) — never an error.
- [ ] AC-5: `synthetic_history`'s cache-probe walk-back distinguishes MISSING (`FileNotFoundError`/ENOENT) from CORRUPT (file exists but unreadable/unparseable): a plain miss logs at most ONE compact line per walk-back at INFO or lower (wording says "cache miss", never "corrupt"), while genuine corruption keeps the existing per-file `WARNING "corrupt or unreadable"`.
- [ ] AC-6: zero change to cache lookup semantics, return values, walk-back depth (`AUTOTUNE_CACHE_MAX_AGE_TRADING_DAYS=10`), or any consumer behavior — (b) is logging-only; (a) is response-filtering-only. `alpha_bot_execution.py` and `math_engine.py` carry zero diff.

## Architecture
- **(a)** `app.py::guard_alpha_preconditions()` — insert one structural filter where the route iterates `state_data`/`bot_state` top-level items, before per-symphony verdict computation. The discriminator lives in one small named helper (pure, unit-testable) so the route body stays readable. `guard_preconditions.py` is untouched.
- **(b)** `synthetic_history.py::get_cached_synthetic_history_only` (and/or its shared read helper) — split the exception handling: `FileNotFoundError` is collected silently during the walk-back and summarized in one INFO line (e.g. "cache miss: no cache file within N trading days of <date>") emitted at most once per call; all other exceptions keep the existing WARNING path and wording. No new I/O, no retry-count change.
- No new routes, no schema, no JS/template changes.

## Design-System Mapping
None — no UI surface changes (JSON response contents and log lines only).

## Edge Cases
- `bot_state` empty or missing entirely → existing behavior preserved (honest empty, 200).
- All entries filtered (metadata-only blob) → AC-4 empty state.
- A symphony value that is structurally degenerate (e.g. not a dict) → excluded by the structural rule; this is correct — it was never renderable as a symphony (document in the helper's docstring).
- Walk-back where day-0 hits immediately → zero miss lines (nothing to summarize).
- Walk-back where SOME days miss and an older day hits → still at most one INFO summary; the hit proceeds normally.
- A genuinely corrupt file mid-walk-back → WARNING fires for that file; the walk-back continues past it exactly as today.

## Security Considerations
- No new inputs, params, or write paths; the route remains read-only behind the global auth hook; no new POST; no CSRF surface.
- The filter must not introduce data exposure: excluded metadata values are never echoed into the response or logs.
- Log lines carry dates/counts only — never file contents, never exception text beyond `type(exc).__name__` (D-1 convention).

## Testing Strategy
- `tests/app/test_guard_alpha_preconditions_route.py` (extend the existing route suite): phantom-key exclusion (seed a realistic `bot_state` containing real symphony sub-dicts + the 5 metadata keys → response contains exactly the real ones); metadata-only blob → honest empty; degenerate-real-symphony inclusion per AC-3; structural-not-denylist proof (a NOVEL metadata-shaped key is also excluded — the anti-rot test).
- Unit tests for the new discriminator helper (pure).
- `tests/synthetic_history/` (extend): caplog-based classification tests — all-miss walk-back yields ≤1 record, level ≤ INFO, wording contains "miss" and not "corrupt"; corrupt-file case (write a garbage file at the probed path) still yields the WARNING; mixed miss-then-hit yields the hit result unchanged.
- Non-vacuity per the house standard (observed failing, or planted-positive controls for scan-style tests).
- Full PR gate: PM suites `-n0`, live gate vs fresh droplet snapshot (re-run the K-L probe — expect symphony_count to drop 16→11 with the 5 phantoms gone and all real verdicts byte-identical), CI, `/review`, SHA-guard, deploy, teardown.

## Decisions
| Decision | Rationale |
|----------|-----------|
| Structural discriminator, not a name denylist | The 5 names are today's incidental metadata; a denylist rots the next time the engine adds a top-level key. The shape rule is the honest invariant ("a symphony entry looks like X"). |
| Cache-miss summary at INFO, once per walk-back | The miss is EXPECTED (weekly cache vs daily probes); per-file WARNINGs misreport normal operation as corruption ~16×/call. One honest summary preserves diagnosability without the noise. |
| Corruption wording/level preserved verbatim | A real corrupt file is still an anomaly worth a WARNING; only the ENOENT misclassification is being fixed. |
| No TDD Toxic Pair | Both are small fixes to existing codepaths (project CLAUDE.md exception class); behavior still test-pinned; full PR gate unchanged. |

## Scope Boundaries
- **IN**: the route's top-level iteration filter + helper; the cache-probe log classification; tests pinning both; docs/DECISIONS entries.
- **OUT**: any change to `guard_preconditions.py` verdict math or thresholds; any change to cache semantics, keys, walk-back depth, or fetch behavior; the K-L retention knob (operator decision); the per-ticker friction table (operator decision); any engine-path file.
