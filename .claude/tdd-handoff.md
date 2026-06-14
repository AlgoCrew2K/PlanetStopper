# TDD Handoff
Plan: feature-plans/atlas-cache.md
Branch: team/atlas-cache
Phase: red

## Test Files
- `tests/advisors/test_atlas_cache.py` — 18 tests

## Fixture Files
- `tests/fixtures/math/atlas_cache_ttl_boundary.json`

## Import Stubs Created
- `advisors/atlas_cache.py` — exports `init_atlas_cache`, `cached_pull` (no logic; functions raise NotImplementedError)

## A/C Coverage Matrix

| A/C ID | Description | Test File | Test Name(s) | Status |
|--------|-------------|-----------|--------------|--------|
| AC-1 | Cache HIT — fetch_fn not called | test_atlas_cache.py | `test_cache_hit_does_not_call_fetch_fn` | RED |
| AC-2 | Cache MISS (no row) — fetch_fn called once, row upserted | test_atlas_cache.py | `test_cache_miss_no_row_calls_fetch_fn_once`, `test_cache_miss_upserts_row` | RED |
| AC-2 | Cache MISS (stale row) — fetch_fn called once | test_atlas_cache.py | `test_cache_miss_stale_row_calls_fetch_fn_once` | RED |
| AC-3 | force_refresh=True calls fetch_fn even with fresh row | test_atlas_cache.py | `test_force_refresh_calls_fetch_fn_with_fresh_row` | RED |
| AC-4 | New SQLite DB at ATLAS_CACHE_DB_PATH; init idempotent + WAL | test_atlas_cache.py | `test_init_creates_db_at_env_path`, `test_init_is_idempotent`, `test_init_enables_wal` | RED |
| AC-5 | corrupt/locked read → live fetch; write fail → return value; never raises | test_atlas_cache.py | `test_corrupt_row_falls_through_to_live_fetch`, `test_write_failure_returns_fetched_payload`, `test_cached_pull_never_raises` | RED |
| AC-6 | TTL boundary: age < ttl_days fresh, >= stale; env override | test_atlas_cache.py | `test_ttl_boundary_strictly_less_than_is_fresh`, `test_ttl_boundary_exactly_equal_is_stale`, `test_ttl_env_override_respected` | RED |
| AC-7 | fetch_fn raises on MISS but stale row exists → stale returned; no row + raise → None sentinel | test_atlas_cache.py | `test_fetch_failure_on_miss_with_stale_row_returns_stale`, `test_fetch_failure_no_row_returns_none_sentinel` | RED |
| AC-8 | MONGO_URI never in DB or returns | test_atlas_cache.py | `test_mongo_uri_never_stored_in_db`, `test_mongo_uri_never_in_returned_payload` | RED |
| AC-9 | No cross-join with state/optimization DBs; no forbidden imports | test_atlas_cache.py | `test_atlas_cache_imports_no_forbidden_modules` | RED |

## Questions for User
None — all ACs are clear from the plan.

## Behavioral Test Plan
N/A — no UI surface.

## Status Log
- [2026-06-14] test-writer: Starting RED phase
