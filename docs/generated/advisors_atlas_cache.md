# advisors/atlas_cache

> Weekly read-through cache for captplanet MongoDB Atlas pulls: one dedicated SQLite DB, TTL-gated upsert, never-raising, secrets-isolated.

**Source:** `advisors/atlas_cache.py`
**Last updated:** 2026-06-21 (DE-ATLAS-CACHE-001: json.dumps default=str defense-in-depth; production callers now live)

## Overview

`advisors/atlas_cache.py` is a pure-stdlib (sqlite3 + json + os) caching layer that protects the captplanet MongoDB Atlas provider from excess reads. All callers (community-strats loader, universe-provider, and any future Atlas collection reader) go through `cached_pull` rather than hitting Atlas directly.

The cache owns a **dedicated SQLite DB** (`alphabot_atlas_cache.db`, path from env `ATLAS_CACHE_DB_PATH`) -- separate from the state DB (`alphabot_state.db`), the Optuna optimization DB, and the lens warehouse (`alphabot_warehouse.db`). It is never cross-joined with any of them.

**Never-raising contract:** `cached_pull` absorbs every internal exception (DB read failure, DB write failure, `fetch_fn` raise, JSON parse error) and always returns a value. The degradation order is: cached payload -> stale payload (on fetch failure) -> `None` sentinel.

**Secrets isolation:** `atlas_cache.py` has no knowledge of `MONGO_URI` or any credential. The caller's `fetch_fn` is responsible for connecting to Atlas and projecting clean docs. The cache stores only what `fetch_fn` returns.

**Robust serialization (DE-ATLAS-CACHE-001):** The upsert uses `json.dumps(fetched_payload, default=str)` to convert any stray BSON type (ObjectId, datetime, Decimal128) to its `str()` representation rather than raising `TypeError`. This is defense-in-depth -- the canonical fix for ObjectId is `"_id": 0` in the caller's projection, but `default=str` ensures a stray non-JSON-native type from any caller never silently drops a cache row. The serialized form of JSON-native types (list, dict, str, int, float, bool, None) is byte-identical to plain `json.dumps` -- existing callers (universe_provider returns `list[str]`) are unaffected.

## Public API

### `init_atlas_cache() -> None`

Create the cache schema idempotently in the DB at `ATLAS_CACHE_DB_PATH`.

Safe to call multiple times (`CREATE TABLE IF NOT EXISTS`). Enables WAL journal mode for concurrent daemon + manual access.

**Schema created:**

```sql
CREATE TABLE IF NOT EXISTS atlas_cache (
    collection  TEXT PRIMARY KEY,
    fetched_at  TEXT NOT NULL,
    payload     TEXT NOT NULL
)
```

One row per collection name. `collection` is the PRIMARY KEY -- upsert (`INSERT OR REPLACE`) keeps exactly one row per collection at all times.

---

### `cached_pull(collection_name, fetch_fn, *, ttl_days=_ENV_DEFAULT, force_refresh=False) -> object or None`

Return the cached payload for `collection_name` if fresh; otherwise call `fetch_fn()`, upsert the result, and return it.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `collection_name` | `str` | Atlas collection identifier. Used as the cache row key. |
| `fetch_fn` | `Callable[[], object]` | Zero-argument callable that fetches live data from Atlas. Called at most once per cache miss. The return value is serialized via `json.dumps(..., default=str)` -- stray BSON types are str()-converted (see Overview). |
| `ttl_days` | `int` | Cache TTL in days. Default `7` (or `ATLAS_CACHE_TTL_DAYS` env var). Age strictly `< ttl_days` is fresh (HIT); age `>= ttl_days` is stale (MISS). |
| `force_refresh` | `bool` | When `True`, bypasses freshness check and calls `fetch_fn()` unconditionally. Default `False`. |

**Returns:** The cached (or freshly fetched) payload as a deserialized Python object, or `None` if both the cache and `fetch_fn` are unavailable and no stale row exists.

**Never raises.** All internal failures are absorbed. See Degradation Paths below.

**TTL boundary (AC-6):** The boundary is strict -- `age < ttl_days` is fresh; `age >= ttl_days` is stale. Controlled via `ATLAS_CACHE_TTL_DAYS` env var (default `7`).

**Example:**

```python
from advisors.atlas_cache import init_atlas_cache, cached_pull

init_atlas_cache()

def _fetch_community_strats():
    # caller owns the Mongo connection and projection; returns projected docs only
    # "_id": 0 in the projection prevents ObjectId serialization issues (DE-ATLAS-CACHE-001)
    client = MongoClient(os.environ["MONGO_URI"])
    return list(client["db"]["community_strats"].find({}, {"_id": 0}))

docs = cached_pull("community_strats", _fetch_community_strats, ttl_days=7)
# docs is None only if both DB and Atlas are unreachable
```

## Degradation Paths (AC-5, AC-7)

`cached_pull` never raises. The failure cascade is:

| Condition | Outcome |
|-----------|---------|
| Fresh row in DB | Return cached payload; `fetch_fn` not called. |
| Stale/missing row, `fetch_fn` succeeds | Call `fetch_fn` once, upsert row (via `json.dumps(..., default=str)`), return fetched payload. |
| DB read failure (corrupt, locked) | Fall through to live fetch as if MISS. |
| `fetch_fn` raises, stale row exists | Return stale payload (degrade gracefully). |
| `fetch_fn` raises, no row exists | Return `None` (documented sentinel). |
| DB write failure after successful fetch | Return fetched payload (write failure is silent). |
| `force_refresh=True` + write failure | Return fetched payload. |

## Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `ATLAS_CACHE_DB_PATH` | `alphabot_atlas_cache.db` | Path to the dedicated cache SQLite DB. Override in tests via `monkeypatch.setenv`. |
| `ATLAS_CACHE_TTL_DAYS` | `7` | Default TTL in days when `cached_pull` is called without an explicit `ttl_days` kwarg. |

## Design Invariants

| Code | Invariant |
|------|-----------|
| AC-4 | Dedicated SQLite DB -- never the state DB or optimization DB. `atlas_cache.py` imports neither `database` nor `autotuner`. |
| AC-5 | `cached_pull` never raises. All exception paths return a value or `None`. |
| AC-7 | Stale payload preferred over `None` when `fetch_fn` fails and a stale row exists. |
| AC-8 | `MONGO_URI` never read, stored, or returned by this module. Callers own credential access. |
| AC-9 | No imports of `database`, `autotuner`, `pymongo`, `motor`, or any Mongo client. Pure stdlib. |
| DE-ATLAS-CACHE-001 | `json.dumps(..., default=str)` in the upsert path -- stray BSON types (ObjectId, datetime, Decimal128) are str()-converted rather than raising TypeError. JSON-native types are byte-stable. |
| Off-execution-path | Never imported on the 1-minute engine loop. Advisory/data-access only. |
| WAL mode | Concurrent daemon + manual reads are safe. Single-writer upsert is last-writer-wins (acceptable for weekly cache). |
| One row per collection | `collection TEXT PRIMARY KEY` + `INSERT OR REPLACE` -- bounded storage, no unbounded growth. |

## Internal Dependencies

- `sqlite3` -- cache DB reads and writes
- `json` -- payload serialization (`json.dumps(..., default=str)`) / deserialization
- `os` -- `ATLAS_CACHE_DB_PATH` and `ATLAS_CACHE_TTL_DAYS` env reads
- `datetime` -- `fetched_at` ISO timestamp generation and TTL age computation

No imports from `database.py`, `autotuner.py`, or any Mongo/network client. No Flask dependency.

## Production Callers

Two modules call `cached_pull` in production:

- **`advisors/community_strats.py`** -- routes the `captplanet.strategies` Atlas collection through the weekly cache. `_PROJECTION` includes `"_id": 0` to suppress the BSON ObjectId field (DE-ATLAS-CACHE-001). Fetch is also bounded server-side (`_MAX_FETCH_DOCS=500`, sorted by `oos_metrics.sharpe DESC`) and wall-clock-bounded (`_ATLAS_FETCH_TIMEOUT_S=12.0 s`).
- **`advisors/universe_provider.py`** -- routes the Alpaca tradeable-universe list through the weekly cache. Payload is `list[str]` (JSON-native); the `default=str` addition is a no-op for this caller.

Both callers set `force_refresh=False` (operator bill-protection directive). Tests override `ATLAS_CACHE_DB_PATH` to an isolated temp path via the `pytest_configure` hook in `tests/conftest.py`.
