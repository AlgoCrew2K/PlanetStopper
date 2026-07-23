# advisors/community_strats

> Community symphonies sourced from **algo-db.com** (read via its `captplanet.strategies` MongoDB Atlas collection, weekly-cached): validates, deduplicates, and filters candidates for the Strategy Builder proposal suite.

**Source:** `advisors/community_strats.py`
**Last updated:** 2026-07-11 (DE-ATLAS-SLOW-QUERY-001 + DE-ATLAS-SHARPE-FIELD-001 + DE-ATLAS-DEEP-TREE-001: the fetch is now a three-step, client-ranked query with NO server-side sort at all — `captplanet.strategies` has no usable index besides `_id`, so any Mongo-side sort is an unindexed COLLSCAN; ranking moved into Python. Also fixes a pre-existing field-name bug: the real OOS-sharpe field is `oos_metrics['Sharpe']` (capital S, string-valued), not `'sharpe'`, which was present on 0/11,227 live docs. `_MAX_FETCH_DOCS` tightened 500→100→50 for live-Atlas timeout headroom. The per-doc composition-hash step is now exception-contained — a pathologically deep tree drops only that one doc instead of aborting the whole batch.)

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
| `min_oos_sharpe` | `float or None` | Exclude candidates whose `oos_metrics['Sharpe']` (capital S) is below this floor. Docs that **lack** `oos_metrics`, lack the `Sharpe` key, or whose `Sharpe` value is unparseable (non-numeric, percent-formatted, NaN, or Infinity) are **kept** regardless -- the floor only ever excludes a genuinely-parsed low value. `None` = no floor applied. |
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
            "oos_metrics": dict,    # or None -- raw passthrough of the doc's oos_metrics
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
    -> raw_docs (list of full-projection Mongo docs, capped at _MAX_FETCH_DOCS=50)
        -> per-doc: edn_string present? -> json.loads -> validate_tree -> extract_tickers
        -> sharpe filter via _parse_sharpe (oos_metrics['Sharpe']; unparseable/missing: kept)
        -> dedup by composition_hash (retain highest OOS sharpe per hash)
        -> limit
    -> {available, candidates, stats, source}
```

### Mongo projection (`_PROJECTION`)

`_PROJECTION` is the inclusion projection applied to the **step-3 targeted full-document fetch** (see "Atlas fetch" below) -- it limits network transfer to the fields the loader actually reads for the returned candidates. It is *not* used for the step-1 lightweight selection query, which uses its own inline `{"_id": 1, "oos_metrics.Sharpe": 1}` projection.

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

### Atlas fetch (weekly cache, three-step client-ranked, wall-clock bounded)

**No server-side sort of any kind (DE-ATLAS-SLOW-QUERY-001, amended).** `captplanet.strategies` has no usable index besides `_id`. An earlier version of this fix (superseded within the same cycle) put the `oos_metrics.sharpe` sort on a lightweight, `edn_string`-free selection query on the theory that sorting small documents would be cheap even though the field is unindexed. A live-Atlas gate run proved this wrong: **any** server-side `sort=` against this collection is an unindexed COLLSCAN across the full ~11,227-doc corpus regardless of projection size, and it still exceeded `_ATLAS_FETCH_TIMEOUT_S` (observed 45.03 s, 0 candidates returned). The fetch was restructured again to remove server-side sorting entirely:

```python
# Step 1 -- lightweight, UNSORTED, uncapped selection: pull only {_id, oos_metrics.Sharpe}
# for the whole collection. No sort=, no .limit() -- no Mongo-side sort runs at all.
selection_cursor = collection.find({}, {"_id": 1, "oos_metrics.Sharpe": 1})
selection_docs = list(selection_cursor)

# Step 2 -- client-side rank + bound: parse each doc's Sharpe defensively (via
# _oos_sharpe/_parse_sharpe -- missing/unparseable -> -inf, sorts to the bottom,
# never raises), sort descending in Python, slice to the top _MAX_FETCH_DOCS ids.
selection_docs.sort(key=_oos_sharpe, reverse=True)
top_ids = [doc["_id"] for doc in selection_docs[:_MAX_FETCH_DOCS]]

# Step 3 -- targeted full-document fetch by the ids selected in step 2, an indexed
# _id lookup ({"_id": {"$in": top_ids}}), so it needs no server-side sort.
full_cursor = collection.find({"_id": {"$in": top_ids}}, _PROJECTION)
```

- **Step 1** transfers only two small fields per document across all ~11,227 docs -- cheap even unindexed, and the ranking work (the expensive part) moves to Python where it is a single in-memory sort of a small list.
- **Step 2** ranking uses `_oos_sharpe`, which now reads the corrected `oos_metrics['Sharpe']` field (see "Sharpe field correction" below) via the shared `_parse_sharpe` helper. Docs with a missing or unparseable Sharpe sort to `-inf` -- last, never excluded, never a crash.
- **Step 3** is an indexed `_id` lookup (`$in`), so it needs no sort and completes quickly regardless of collection size.

**`_MAX_FETCH_DOCS` -- tightened 500 → 100 → 50 (live-timing-driven, not a correctness bound).**

`edn_string` averages ~153 KB/doc live. Even with the sort eliminated, an indexed `_id` fetch of too many full documents dominates the 45 s timeout budget on its own, independent of any sort/index concern:

- `500` (original, pre-cycle): OOM/latency risk on the 4 GB droplet; superseded.
- `100` (first amendment): passed the live-Atlas gate at 33.95 s but left only ~11 s of headroom under the 45 s bound on the PM's test machine -- judged insufficient margin for the droplet's slower 2 vCPU single-thread parse.
- `50` (current): roughly halves fetch+parse cost with zero functional impact on candidate quality; still 2.5x headroom over `MAX_COMMUNITY_CANDIDATES_PER_RUN=20` after validation/dedup loss.

The public `limit` parameter (post-fetch, post-dedup, caller-level) is a separate, independent control -- not the same as `_MAX_FETCH_DOCS`.

**Wall-clock bound -- `_ATLAS_FETCH_TIMEOUT_S = 45.0 s`**

`_bounded_fetch_fn` (nested def inside `load_community_strategies`) wraps `_fetch_fn` via a `ThreadPoolExecutor(max_workers=1)`. `fut.result(timeout=_ATLAS_FETCH_TIMEOUT_S)` fires if the fetch exceeds the window; the `_timeout_fired` closure flag is set to `True` before raising `_AtlasFetchTimeout`; `shutdown(wait=False, cancel_futures=True)` releases the calling thread. `serverSelectionTimeoutMS` and `connectTimeoutMS` cannot bound a `mongodb+srv://` SRV/TXT DNS hang -- the ThreadPoolExecutor timeout is the only reliable wall-clock bound. See `DE-CS-002` in `DECISIONS.md`. (Note: `45.0` is the live value as of this cycle -- it was raised from an earlier `12.0` in a prior merged commit, independent of this fix.)

`cached_pull` routes `_bounded_fetch_fn` through the weekly cache; the second call within the TTL returns the cached payload without touching Mongo or entering the timeout wrapper.

### Sharpe field correction (DE-ATLAS-SHARPE-FIELD-001)

A pre-existing, cycle-independent correctness bug was found via direct Mongo reads while diagnosing the live-Atlas gate failure above: all three Sharpe-consumption sites in this module (client-side selection ranking, the dedup tie-break, and the `min_oos_sharpe` filter) read `oos_metrics['sharpe']` (lowercase) -- a key present on **0 of 11,227** live `captplanet.strategies` docs. The real field is `oos_metrics['Sharpe']` (capital S), **string-valued**, present on **10,067 of 11,227** docs.

`_parse_sharpe(oos_metrics) -> float | None` is the single shared parse contract now used by every consumption site:

```python
def _parse_sharpe(oos_metrics: Any) -> float | None:
    if not isinstance(oos_metrics, dict):
        return None
    raw = oos_metrics.get("Sharpe")
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if math.isnan(value) or math.isinf(value):
        return None
    return value
```

Defensive rules -- never raises, all resolve to `None` (treated as "absent"):

- Missing `oos_metrics` or missing `Sharpe` key.
- Non-numeric string (e.g. `"N/A"`).
- Percent-formatted string (e.g. `"12.3%"` -- `float()` rejects the `%` suffix).
- `"nan"` / `"inf"` -- Python's bare `float()` *accepts* these as valid floats, but neither is a valid Sharpe ratio, so both are explicitly rejected via `math.isnan`/`math.isinf`.

`_oos_sharpe(doc) -> float` wraps `_parse_sharpe(doc.get("oos_metrics"))`, returning `float("-inf")` when parsing yields `None`. `-inf` guarantees a missing/unparseable Sharpe always loses dedup ties and sorts to the bottom of the client-side selection ranking -- the same "absent, never excluded, never a crash" contract as before the fix, just now pointed at a field that actually has data.

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

### Deep-tree exception containment (DE-ATLAS-DEEP-TREE-001)

`_strip_ids` (the first step of `_composition_hash`) is recursive -- unlike `symphony_schema`'s deliberately iterative traversal -- and can raise `RecursionError` on a pathologically deep (but structurally valid) tree at roughly 500 nesting levels. The other three per-doc steps in the parse loop (`json.loads`, `validate_tree`, `extract_tickers`) each already had their own `try`/`except`; the composition-hash call site did not, so one pathological doc could propagate an uncaught exception out of `load_community_strategies`, violating the D-1 never-raising contract and losing the *entire* batch rather than just the one bad doc.

Fixed by wrapping the composition-hash call in its own `try`/`except`, matching the containment pattern already given to the three steps above -- any exception there (`RecursionError`, `MemoryError`, or otherwise) now increments `parse_failed` and drops only that one doc; the loop continues with the remainder of the batch. `_strip_ids` itself is intentionally left recursive (a minimal, scoped fix) -- a rare deep doc dropping is acceptable latent-risk containment, not a live data-loss concern.

### Deduplication

Within each `composition_hash` group, the candidate with the highest `_oos_sharpe(doc)` (i.e. the correctly-parsed `oos_metrics['Sharpe']`, see above) is retained. Ties resolve in favor of whichever was encountered first. Docs without a parseable sharpe value use `-inf` for comparison (a doc with any real, parseable sharpe always wins over one with a missing/unparseable sharpe). `stats['deduped']` counts the number of candidates removed by dedup.

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
| SRV/DNS or Mongo fetch hangs > `_ATLAS_FETCH_TIMEOUT_S` (45.0 s) | `"AtlasFetchTimeout"` | `False` |
| `atlas_cache.cached_pull` returns `None` (cache miss + fetch failed + no stale row, no timeout) | `"AtlasCacheUnavailable"` | `False` |
| `atlas_cache.cached_pull` returns a non-list payload (corrupt cache) | `"TypeError"` | `False` |
| Any other exception in the outer try block | `type(exc).__name__` | `False` |
| Empty collection (zero docs) | -- | `True` (candidates=[], stats.pulled=0) |
| A doc's `oos_metrics['Sharpe']` is missing/malformed | -- (not a failure) | `True` -- doc is kept, sorts/ties to `-inf` (see "Sharpe field correction") |

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
- `math` -- `isnan`/`isinf` rejection in `_parse_sharpe` (DE-ATLAS-SHARPE-FIELD-001)
- `os` -- `MONGO_URI` env read inside `fetch_fn`
- `pymongo` -- lazy-imported inside `_fetch_fn` only; not a module-level import

No imports from `database.py`, `autotuner.py`, `app.py`, or any execution module.

## Production Caller

`advisors/community_strats.py` is called indirectly via `advisors/build_plan_generator.load_atlas_candidates(objective)`, which wraps `load_community_strategies` with objective-matched ranking and the `MAX_COMMUNITY_CANDIDATES_PER_RUN` cap.

The canonical community-admission path for all production callers (Strategy Builder route and weekly scheduler) is `build_plan_generator.load_atlas_candidates(objective)`. The former `load_community_strategies + community_candidate_infos` route pattern was replaced in C5 (see `DE-SB-C5` in `DECISIONS.md`). Key invariants:

- `force_refresh=False` is enforced inside `load_atlas_candidates` -- the weekly Atlas cache TTL is the operator bill-protection directive.
- Any failure (Atlas down, `MONGO_URI` unset, cache miss) degrades to `community_candidates=[]`; the proposal run completes as built-new-only.
- Community candidates enter the single-batch FDR gate alongside built-new candidates (anti-overfit invariant).

See `DE-HF1-001`, `DE-SB-C5`, `DE-ATLAS-SLOW-QUERY-001`, and `DE-ATLAS-SHARPE-FIELD-001` in `DECISIONS.md` for the full architectural rationale.
