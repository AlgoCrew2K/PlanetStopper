# Atlas Community-Strategies Cache: Populate + Bound Fix

Status: ready

## Summary
The `captplanet.strategies` Atlas community-strategies data is **never served from the weekly `atlas_cache`** — it hits Atlas live on every call and fails. Two root-caused bugs (both verified live on the droplet, 2026-06-21):

1. **Cache-write `TypeError` → cache never populates.** `community_strats._fetch_fn` returns raw Mongo docs; the inclusion projection `_PROJECTION = {"sid":1,"name":1,"edn_string":1,"oos_metrics":1}` **still includes `_id` (ObjectId) by default**. `atlas_cache.cached_pull` caches via `json.dumps(fetched_payload)`, which throws `TypeError` on the ObjectId → logs "write error (TypeError); returning payload without caching" → the `captplanet.strategies` cache row is **never written**. (The `universe_provider` cache row — a plain ticker-string list — serializes fine, which is why only IT caches.)
2. **Unbounded fetch → OOM.** `_fetch_fn` does `list(collection.find({}, _PROJECTION))` over **all 11,193 docs**, no `.limit()`/sort → OOM-killed on the 4 GB droplet (observed: process Killed mid-run).

Net effect: the cache stays empty for community strategies → every call re-hits Atlas (defeating the operator's 1-week-TTL bill-protection design) → and the live fetch trips the 12 s bound / OOM → `available=False`, 0 atlas-suggested candidates. Atlas IS reachable with the configured `MONGO_URI` (verified: `ATLAS_VERSION 8.3.4`, `captplanet` db, 11,193 strategies, connect+count = 9.6 s).

Advisory-only throughout. No `LIVE_EXECUTION`, no new write path beyond the existing cache table, no Composer interaction.

## Acceptance Criteria
- **AC-1 — JSON-serializable cache payload (the TypeError fix).** A community-strategies fetch result must be JSON-serializable so `atlas_cache` can write it. Fix at the source: `_PROJECTION` must exclude `_id` (`"_id": 0`). RED: a fetch whose docs carry an ObjectId `_id` (and/or a BSON datetime) must result in a written `captplanet.strategies` row in the `atlas_cache` table — NOT a swallowed "write error (TypeError)". Assert the row exists + round-trips via `json.loads`.
- **AC-2 — defense-in-depth serialization in `atlas_cache`.** `atlas_cache` write must not silently drop a payload on a stray non-JSON type — serialize BSON/`datetime`/`ObjectId`-bearing payloads robustly (e.g., `json.dumps(..., default=str)` or an explicit sanitizer). RED: a payload containing a non-JSON-native value caches successfully (no TypeError-swallow). (Keeps the universe_provider path byte-stable.)
- **AC-3 — memory-bounded fetch (the OOM fix).** `_fetch_fn` must NOT pull all 11k docs. Apply a server-side bound: a named constant cap (e.g., `_MAX_FETCH_DOCS`) via `.limit()`, and prefer the best candidates by sorting `oos_metrics.sharpe` descending at the query (docs missing sharpe handled per the existing keep-rule). RED: assert the Mongo `find` is issued with a `.limit()` (the cursor/limit is observable via the test seam) and the returned doc count ≤ the cap; the existing `limit`/`min_oos_sharpe` public params are reconciled (not double-applied incorrectly).
- **AC-4 — cache HIT serves without a live fetch.** After one successful populate, a subsequent `load_community_strategies(force_refresh=False)` within the 7-day TTL must return the cached candidates **without invoking `_fetch_fn`** (no Atlas hit). RED: patch/seam the fetch to count invocations — first call populates, second call within TTL does NOT call it and returns the same candidates.
- **AC-5 — bound accommodates a healthy bounded fetch.** With the fetch bounded (AC-3), a healthy connection completes within `_ATLAS_FETCH_TIMEOUT_S`; if the bounded fetch can legitimately exceed the current 12 s on a cold `mongodb+srv` connect, raise the constant to a justified named value (the cache means the live fetch runs ≈weekly). RED: document/justify the chosen bound; no spurious `AtlasFetchTimeout` on the bounded query.
- **AC-6 — D-1 / never-raises preserved.** All existing honest-degradation behavior (`available=False, reason=...`) on a real Atlas failure is unchanged. No exception escapes `load_community_strategies` / `cached_pull`.

## Architecture
- `advisors/community_strats.py`: `_PROJECTION` add `"_id": 0`; `_fetch_fn` add server-side sort+limit (named cap constant); reconcile with `limit`/`min_oos_sharpe`.
- `advisors/atlas_cache.py`: robust serialization in the upsert (`default=str` or sanitizer) so a stray BSON type never silently drops the payload.
- No change to the route, `propose_strategies`, `build_plan_generator.load_atlas_candidates`, or the gate. The cache key (`_COLLECTION_NAME = "captplanet.strategies"`) is unchanged.

## Edge Cases
- Docs missing `oos_metrics`/`sharpe`: kept per the existing `_oos_sharpe = -inf` rule; the server-side sort must not silently drop them below the cap in a way that violates the documented keep-behavior — verify the interaction.
- A stale cache row whose schema predates this fix: the read path already falls through on a corrupt/JSON-decode error (`cached_pull` handles `json.JSONDecodeError`).
- Empty Atlas / zero docs: honest `available=False`/empty candidates, no crash.

## Security Considerations
- `MONGO_URI` never logged (existing contract). Cache payload contains only the projected public strategy fields (`sid`/`name`/`edn_string`/`oos_metrics`) — no secrets.
- Advisory-only; off the execution path.

## Testing Strategy
- Fixture/seam-based unit tests (mock `_fetch_fn` / a fake collection returning docs with ObjectId + datetime). NO live Atlas in unit tests.
- Assertions test design contract (cache row written, fetch-not-called-on-hit, limit applied), not hard-coded producer values.
- PM-owned LIVE droplet E2E after merge: confirm a real fetch populates the `captplanet.strategies` cache row, a second call serves from cache with no Atlas hit, and `load_atlas_candidates` returns real atlas-suggested candidates.

## Scope Boundaries
- IN: `community_strats.py` projection + fetch bounding; `atlas_cache.py` serialization robustness.
- OUT: the SB route, `propose_strategies`, the FDR/PBO gate, `universe_provider` (byte-stable), any LIVE_EXECUTION path.
