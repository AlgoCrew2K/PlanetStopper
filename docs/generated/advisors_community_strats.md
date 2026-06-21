# advisors/community_strats

> Community symphonies sourced from **algo-db.com** (read via its `captplanet.strategies` MongoDB Atlas collection, weekly-cached): validates, deduplicates, and filters candidates for the Strategy Builder proposal suite.

**Source:** `advisors/community_strats.py`
**Last updated:** 2026-06-21 (DE-ATLAS-CACHE-001: _id:0 projection + _MAX_FETCH_DOCS=500 + server-side sort+limit)

## Overview

`advisors/community_strats.py` fetches community symphonies from **algo-db.com** (read via its `captplanet.strategies` MongoDB Atlas collection) and returns a well-formed result dict of validated, deduplicated community-symphony candidates.

The algo-db.com Atlas network read is routed through `advisors/atlas_cache.cached_pull` with a weekly TTL -- the collection is fetched at most once per week per the operator bill-protection directive. Validation, deduplication, and sharpe filtering run on the cached payload on every call (cheap, in-process). Only the raw projected docs are cached; the caller always receives freshly-processed results.

Off-execution-path. Advisory-only. No Flask routes, no execution flags. pymongo is lazy-imported inside the `fetch_fn` closure only; the module is importable without pymongo installed.

**Never-raising contract (D-1):** all failure modes return `available=False` with `reason=type(exc).__name__` -- never the exception message, MONGO_URI, hostname, or any credential. The function never raises.

## Public API

### `load_community_strategies(*, limit=None, min_oos_sharpe=None, client=None, force_refresh=False) -> dict`

Load and validate community strategies from the captplanet Atlas collection.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `limit` | `int or None` | Cap the number of returned candidates (applied after dedup and sharpe filtering). `None` = no cap. |
| `min_oos_sharpe` | `float or None` | Exclude candidates whose `oos_metrics['sharpe']` is below this floor. Docs that **lack** `oos_metrics` or lack the `sharpe` key are **kept** regardless. `None` = no floor applied. |
| `client` | `any` | Reserved for interface compatibility. Not used in this implementation. |
| `force_refresh` | `bool` | When `True`, bypass the atlas_cache TTL and re-fetch from Mongo unconditionally. Default `False`. |

**Returns:** `dict` -- always returned, never raises. Shape:

```python
# Success
{
    "available": True,
    "candidates": [
        {
            "sid": str,
            "name": str,
            "tree": dict,           # parsed + validated Composer decision tree
            "tickers": list,        # extract_tickers result (no '%' placeholders)
            "oos_metrics": dict,    # or None
            "composition_hash": str,  # SHA-256 hex of tree structure (see below)
        },
        ...
    ],
    "stats": {
        "pulled": int,
        "valid": int,               # final candidate count (post-dedup/filter/limit)
        "missing_edn_string": int,
        "parse_failed": int,
        "validate_rejected": int,
        "sharpe_filtered": int,
        "deduped": int,
    },
    "source": "captplanet",
}

# Failure (any exception, cache unavailable, non-list payload)
{
    "available": False,
    "reason": str,   # type(exc).__name__ only -- no secret values
    "candidates": [],
    "stats": {"pulled": 0, "valid": 0, "missing_edn_string": 0,
              "parse_failed": 0, "validate_rejected": 0,
              "sharpe_filtered": 0, "deduped": 0},
    "source": "captplanet",
}
```

**Example:**

```python
from advisors.community_strats import load_community_strategies

result = load_community_strategies(min_oos_sharpe=0.5, limit=20)
if result["available"]:
    for cand in result["candidates"]:
        print(cand["name"], cand["composition_hash"][:8])
else:
    print("unavailable:", result["reason"])
```

## Processing Pipeline

Each call executes these steps on the cached payload:

```
cached_pull("captplanet.strategies", bounded_fetch_fn)
    -> raw_docs (list of projected Mongo docs, capped at _MAX_FETCH_DOCS=500)
        -> per-doc: edn_string present? -> json.loads -> validate_tree -> extract_tickers
        -> sharpe filter (docs lacking sharpe: kept)
        -> dedup by composition_hash (retain highest OOS sharpe per hash)
        -> limit
    -> {available, candidates, stats, source}
```

### Mongo projection (`_PROJECTION`)

`_fetch_fn` applies a server-side inclusion projection to limit network transfer to the fields the loader actually reads:

```python
_PROJECTION: dict = {
    "_id": 0,        # explicit suppression -- pymongo includes _id by default even in
                     # inclusion projections; ObjectId is not JSON-serializable and would
                     # cause atlas_cache json.dumps to raise TypeError, silently dropping
                     # the cache row on every write attempt (DE-ATLAS-CACHE-001 Bug 1)
    "sid": 1,
    "name": 1,
    "edn_string": 1,
    "oos_metrics": 1,
}
```

The `backtest` and `quantstats_metrics` fields (multi-MB arrays per doc) are excluded from the projection.

### Atlas fetch (weekly cache, server-side bounded, wall-clock bounded)

The live Atlas fetch is bounded in two independent ways.

**Server-side bound -- `_MAX_FETCH_DOCS = 500`**

`_fetch_fn` applies a server-side sort + limit before the cursor is materialized:

```python
cursor = collection.find(
    {},
    _PROJECTION,
    sort=[("oos_metrics.sharpe", pymongo.DESCENDING)],
    allow_disk_use=True,
).limit(_MAX_FETCH_DOCS)
```

- `sort=[("oos_metrics.sharpe", DESCENDING)]` ensures the cap keeps the best-sharpe docs. Docs missing `oos_metrics.sharpe` sort to the bottom in MongoDB's collation and may be excluded -- this is the intended fetch policy (highest-quality candidates fetched first). The Python-side keep-rule (docs lacking sharpe are kept after fetch) applies to the fetched subset and is independent.
- `allow_disk_use=True` prevents the 32 MB in-memory sort limit from aborting the query on the ~11k-doc `captplanet.strategies` collection.
- `.limit(_MAX_FETCH_DOCS)` caps the network transfer and in-process memory. 500 covers `MAX_COMMUNITY_CANDIDATES_PER_RUN = 20` with generous headroom for validation/dedup loss. Fetching all 11k docs unbounded OOM-killed the 4 GB droplet (DE-ATLAS-CACHE-001 Bug 2).

**Wall-clock bound -- `_ATLAS_FETCH_TIMEOUT_S = 12.0 s`**

`_bounded_fetch_fn` (nested def inside `load_community_strategies`) wraps `_fetch_fn` via a `ThreadPoolExecutor(max_workers=1)`. `fut.result(timeout=_ATLAS_FETCH_TIMEOUT_S)` fires if the fetch exceeds the window; the `_timeout_fired` closure flag is set to `True` before raising `_AtlasFetchTimeout`; `shutdown(wait=False, cancel_futures=True)` releases the calling thread. `serverSelectionTimeoutMS` and `connectTimeoutMS` cannot bound a `mongodb+srv://` SRV/TXT DNS hang -- the ThreadPoolExecutor timeout is the only reliable wall-clock bound. See `DE-CS-002` in `DECISIONS.md`.

`cached_pull` routes `_bounded_fetch_fn` through the weekly cache; the second call within the TTL returns the cached payload without touching Mongo or entering the timeout wrapper.

### edn_string parse

Despite the field name, `edn_string` stores a **JSON-encoded Composer decision tree** (not EDN format). Parsing uses `json.loads`. A missing, empty, or unparseable value increments the appropriate stats counter and skips the doc; the call continues with the valid remainder.

### Validation

`symphony_schema.validate_tree(tree)` -- HARD errors only (returns `[]` for a valid tree). Any non-empty error list or any exception from `validate_tree` increments `validate_rejected` and skips the doc.

`symphony_schema.extract_tickers(tree)` -- excludes `%` placeholder. Any exception increments `validate_rejected`.

### Composition hash (tree-structure, NOT `database.compute_composition_hash`)

The dedup key is a **local tree-structural hash** computed by `_composition_hash(tree)`:

1. Strip all `id` keys recursively from the tree dict (`_strip_ids`) -- uuid4 node ids differ per construction, but identical logic trees must hash identically.
2. `json.dumps(stripped_tree, sort_keys=True, separators=(",", ":"))` -- deterministic canonical form.
3. `hashlib.sha256(...).hexdigest()` -- 64-character hex string.

**This is NOT `database.compute_composition_hash`**, which takes a `list[str]` of symphony IDs and is used for portfolio-set identity (mode-resolver use). `_composition_hash` operates on a single tree dict and is local to this module.

### Deduplication

Within each `composition_hash` group, the candidate with the highest `oos_metrics['sharpe']` is retained. Ties resolve in favor of whichever was encountered first. Docs without a sharpe value use `-inf` for comparison (a doc with any real sharpe always wins over one with a missing sharpe). `stats['deduped']` counts the number of candidates removed by dedup.

### Stats invariant

For a successful (non-exception) run:

```
pulled == (valid + missing_edn_string + parse_failed + validate_rejected
           + sharpe_filtered + deduped) + (candidates dropped by limit)
```

`valid` reflects the final candidate count after dedup and sharpe filtering but before `limit`.

## Failure Modes and D-1 Contract

| Condition | `reason` | `available` |
|-----------|----------|-------------|
| `MONGO_URI` not set | `"KeyError"` | `False` |
| pymongo connection error | `"ServerSelectionTimeoutError"` (or similar) | `False` |
| SRV/DNS or Mongo fetch hangs > `_ATLAS_FETCH_TIMEOUT_S` (12.0 s) | `"AtlasFetchTimeout"` | `False` |
| `atlas_cache.cached_pull` returns `None` (cache miss + fetch failed + no stale row, no timeout) | `"AtlasCacheUnavailable"` | `False` |
| `atlas_cache.cached_pull` returns a non-list payload (corrupt cache) | `"TypeError"` | `False` |
| Any other exception in the outer try block | `type(exc).__name__` | `False` |
| Empty collection (zero docs) | -- | `True` (candidates=[], stats.pulled=0) |

`"AtlasFetchTimeout"` is set when `_timeout_fired[0]` is `True` (the wall-clock wrapper fired). `"AtlasCacheUnavailable"` is the named sentinel for the `raw_docs is None` path when no timeout fired. See `DE-CS-002` in `DECISIONS.md`.

## DB Isolation

`community_strats.py` does not import `database`, `autotuner`, or any execution module. No cross-join with the state DB, optimization DB, or lens warehouse. The Atlas cache DB is accessed only through `atlas_cache.cached_pull` -- this module has no direct sqlite3 connection. `MONGO_URI` is read inside `fetch_fn` only and is never returned, logged, or stored by this module.

## Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `MONGO_URI` | *(required when fetching)* | MongoDB Atlas connection string. Read lazily inside `fetch_fn`; never stored or returned. |
| `ATLAS_CACHE_DB_PATH` | `alphabot_atlas_cache.db` | Cache DB path, owned by `advisors/atlas_cache.py`. Override in tests via `monkeypatch.setenv`. |
| `ATLAS_CACHE_TTL_DAYS` | `7` | Cache TTL; respected by `cached_pull` when `force_refresh=False`. |

## Internal Dependencies

- `advisors.atlas_cache` -- `cached_pull` for weekly-TTL Atlas read routing
- `advisors.symphony_schema` -- `validate_tree`, `extract_tickers`
- `concurrent.futures` -- `ThreadPoolExecutor` wall-clock timeout wrapper (`_bounded_fetch_fn`)
- `json` -- `edn_string` parsing and composition hash canonical form
- `hashlib` -- SHA-256 composition hash
- `os` -- `MONGO_URI` env read inside `fetch_fn`
- `pymongo` -- lazy-imported inside `_fetch_fn` only; not a module-level import

No imports from `database.py`, `autotuner.py`, `app.py`, or any execution module.

## Production Caller

`advisors/community_strats.py` is called indirectly via `advisors/build_plan_generator.load_atlas_candidates(objective)`, which wraps `load_community_strategies` with objective-matched ranking and the `MAX_COMMUNITY_CANDIDATES_PER_RUN` cap.

The canonical community-admission path for all production callers (Strategy Builder route and weekly scheduler) is `build_plan_generator.load_atlas_candidates(objective)`. The former `load_community_strategies + community_candidate_infos` route pattern was replaced in C5 (see `DE-SB-C5` in `DECISIONS.md`). Key invariants:

- `force_refresh=False` is enforced inside `load_atlas_candidates` -- the weekly Atlas cache TTL is the operator bill-protection directive.
- Any failure (Atlas down, `MONGO_URI` unset, cache miss) degrades to `community_candidates=[]`; the proposal run completes as built-new-only.
- Community candidates enter the single-batch FDR gate alongside built-new candidates (anti-overfit invariant).

See `DE-HF1-001` and `DE-SB-C5` in `DECISIONS.md` for the full architectural rationale.
