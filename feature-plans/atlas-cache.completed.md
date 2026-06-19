# Feature: Weekly Atlas Read Cache (new local DB)
Status: ready
Created: 2026-06-14

## Summary
Operator directive: cache all `captplanet` MongoDB Atlas pulls weekly to protect the third-party provider's bill. This builds the shared cache layer — `advisors/atlas_cache.py` backed by a NEW dedicated local SQLite DB (`alphabot_atlas_cache.db`) — that fetches each Atlas collection at most ~once/week and serves all other reads from the local cache. Must land BEFORE any Atlas caller (the community-strats/frontrunner loaders, rebuilt in later cycles, will pull through it). Off-execution-path, advisory-only, never-raising. No production caller in THIS cycle (the loaders don't exist yet — they're separate rebuild cycles).

## Acceptance Criteria
- [ ] AC-1: `atlas_cache.cached_pull(collection_name, fetch_fn, *, ttl_days=7, force_refresh=False)` returns the cached payload when a row for `collection_name` exists and `fetched_at` is younger than `ttl_days`; `fetch_fn` is NOT called (assert call count == 0).
- [ ] AC-2: On a cache MISS (no row, or older than `ttl_days`), `cached_pull` calls `fetch_fn()` exactly once, upserts `{collection, fetched_at, payload}`, returns the fetched payload.
- [ ] AC-3: `force_refresh=True` always calls `fetch_fn()` and refreshes, even with a fresh row.
- [ ] AC-4: cache lives in a NEW SQLite DB at `ATLAS_CACHE_DB_PATH` (default `alphabot_atlas_cache.db`), separate from state/optimization/lens_warehouse DBs; `init_atlas_cache()` idempotent + WAL.
- [ ] AC-5 (never-raising): a cache READ failure (corrupt/locked/missing) falls through to call `fetch_fn()` (live pull); a cache WRITE failure returns the freshly-fetched payload; `cached_pull` NEVER raises.
- [ ] AC-6 (TTL boundary): `ttl_days` boundary is explicit — age strictly `< ttl_days` is fresh, `>= ttl_days` is stale. `ATLAS_CACHE_TTL_DAYS` env (default 7) overrides the default.
- [ ] AC-7 (stale-on-fetch-failure): if `fetch_fn()` raises on a MISS but a STALE row exists, return the stale payload rather than propagating (degrade gracefully); if no row exists and `fetch_fn` raises, re-raise is NOT allowed — return a documented empty/None sentinel (never raise).
- [ ] AC-8 (secrets): no credential (`MONGO_URI`) is ever written to the cache DB or returned; the cache stores only what `fetch_fn` returns (projected docs). `atlas_cache` imports no Mongo/credential code.
- [ ] AC-9 (isolation): the cache DB is never cross-joined with state/optimization DBs; `atlas_cache.py` imports neither.

## Architecture
New `advisors/atlas_cache.py` (pure stdlib + sqlite3), mirroring the `advisors/lens_warehouse.py` separate-DB pattern. `_atlas_cache_db()` resolves `ATLAS_CACHE_DB_PATH`; `init_atlas_cache()` creates `atlas_cache(collection TEXT PRIMARY KEY, fetched_at TEXT, payload TEXT)` + WAL (idempotent). `cached_pull(...)`: read row → if fresh `json.loads(payload)` return; else `fetch_fn()` → `json.dumps` → upsert `{collection, now_iso, payload}` → return. All sqlite/json ops wrapped so any failure falls through (read-fail→live fetch; write-fail→return fetched). Inject a clock (`now_fn`) or pre-seed `fetched_at` in tests for TTL-boundary control (`Date.now`-style calls fine in prod Python).

## Design-System Mapping
N/A — no UI.

## Edge Cases
- DB file absent first call → init creates it; first pull is a MISS.
- corrupt/non-JSON payload row → read failure → live fetch.
- `fetch_fn` returns non-JSON-serializable → write-fail path → return the value, don't raise.
- concurrent writers (daemon + manual) → WAL + upsert last-writer-wins (acceptable for weekly cache).
- TTL boundary exactness (AC-6).

## Security Considerations
`MONGO_URI` is owned by the loaders' connect code, NEVER by `atlas_cache` — verify `atlas_cache` never receives/stores/logs it. Cache stores only projected doc payloads (no secrets). `json` only — no eval/exec/pickle. No network. Cache DB isolated (no cross-join). Bounded growth (one row/collection).

## Testing Strategy
Unit (`tests/advisors/test_atlas_cache.py`): AC-1..AC-9 with an isolated temp `ATLAS_CACHE_DB_PATH` + a fake clock (or pre-seeded `fetched_at`) for fresh/stale/boundary; a spy `fetch_fn` asserting call counts (hit→0, miss→1, force→1); corrupt-row + locked-DB → fall-through never-raises; recursive secret-leak walk asserting `MONGO_URI` absent from DB + returns. No live Atlas (all mocked). `-n0` gate on `tests/advisors/` before PM merge.

## Decisions
| Decision | Rationale |
|----------|-----------|
| New dedicated SQLite DB | Operator directive "create a new db locally"; isolation; mirrors lens_warehouse |
| Weekly default TTL, env-configurable | Operator "cache weekly"; provider-bill protection |
| Stale-better-than-empty on fetch failure | Weekly cache degrades gracefully if Atlas briefly down |

## Scope Boundaries
- **IN**: `advisors/atlas_cache.py` + new `alphabot_atlas_cache.db` + `cached_pull`/`init_atlas_cache` + tests.
- **OUT**: wiring loaders through it (the loaders don't exist yet — future rebuild cycles will wire them); any write-back to Atlas; auto-refresh cron; multi-collection joins; any UI.
