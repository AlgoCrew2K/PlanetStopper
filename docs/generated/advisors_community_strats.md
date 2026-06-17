# advisors/community_strats

> Weekly-cached captplanet Atlas loader: validates, deduplicates, and filters community strategy documents for the Strategy Builder proposal suite.

**Source:** `advisors/community_strats.py`
**Last updated:** 2026-06-17

## Overview

`advisors/community_strats.py` fetches strategy documents from the captplanet MongoDB Atlas `strategies` collection and returns a well-formed result dict of validated, deduplicated community-symphony candidates.

The Atlas network read is routed through `advisors/atlas_cache.cached_pull` with a weekly TTL — the collection is fetched at most once per week per the operator's bill-protection directive. Validation, deduplication, and sharpe filtering run on the cached payload on every call (cheap, in-process). Only the raw projected docs are cached; the caller always receives freshly-processed results.

Off-execution-path. Advisory-only. No Flask routes, no execution flags. pymongo is lazy-imported inside the `fetch_fn` closure only; the module is importable without pymongo installed.

**Never-raising contract (D-1):** all failure modes return `available=False` with `reason=type(exc).__name__` — never the exception message, MONGO_URI, hostname, or any credential. The function never raises.

## Public API

### `load_community_strategies(*, limit=None, min_oos_sharpe=None, client=None, force_refresh=False) -> dict`

Load and validate community strategies from the captplanet Atlas collection.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `limit` | `int \| None` | Cap the number of returned candidates (applied after dedup and sharpe filtering). `None` = no cap. |
| `min_oos_sharpe` | `float \| None` | Exclude candidates whose `oos_metrics['sharpe']` is below this floor. Docs that **lack** `oos_metrics` or lack the `sharpe` key are **kept** regardless. `None` = no floor applied. |
| `client` | `any` | Reserved for interface compatibility. Not used in this implementation. |
| `force_refresh` | `bool` | When `True`, bypass the atlas_cache TTL and re-fetch from Mongo unconditionally. Default `False`. |

**Returns:** `dict` — always returned, never raises. Shape:

```python
# Success
{
    "available": True,
    "candidates": [
        {
            "sid": str,
            "name": str,
            "tree": dict,           # parsed + validated Composer decision tree
            "tickers": list[str],   # extract_tickers result (no '%' placeholders)
            "oos_metrics": dict | None,
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
    "reason": str,   # type(exc).__name__ only — no secret values
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
cached_pull("captplanet.strategies", fetch_fn)
    -> raw_docs (list of projected Mongo docs)
        -> per-doc: edn_string present? → json.loads → validate_tree → extract_tickers
        -> sharpe filter (docs lacking sharpe: kept)
        -> dedup by composition_hash (retain highest OOS sharpe per hash)
        -> limit
    -> {available, candidates, stats, source}
```

### Atlas fetch (weekly cache)

`fetch_fn` lazy-imports `pymongo`, connects to `os.environ["MONGO_URI"]`, and fetches from `captplanet.strategies` using the projection `{sid, name, edn_string, oos_metrics}` only — explicitly excluding multi-MB `backtest` and `quantstats_metrics` arrays. `cached_pull` routes this through the weekly cache; the second call within the TTL returns the cached payload without touching Mongo.

### edn_string parse

Despite the field name, `edn_string` stores a **JSON-encoded Composer decision tree** (not EDN format). Parsing uses `json.loads`. A missing, empty, or unparseable value increments the appropriate stats counter and skips the doc; the call continues with the valid remainder.

### Validation

`symphony_schema.validate_tree(tree)` — HARD errors only (returns `[]` for a valid tree). Any non-empty error list or any exception from `validate_tree` increments `validate_rejected` and skips the doc.

`symphony_schema.extract_tickers(tree)` — excludes `%` placeholder. Any exception increments `validate_rejected`.

### Composition hash (tree-structure, NOT `database.compute_composition_hash`)

The dedup key is a **local tree-structural hash** computed by `_composition_hash(tree)`:

1. Strip all `id` keys recursively from the tree dict (`_strip_ids`) — uuid4 node ids differ per construction, but identical logic trees must hash identically.
2. `json.dumps(stripped_tree, sort_keys=True, separators=(",", ":"))` — deterministic canonical form.
3. `hashlib.sha256(...).hexdigest()` — 64-character hex string.

**This is NOT `database.compute_composition_hash`.** That function takes a `list[str]` of symphony IDs and is used for portfolio-set identity (mode-resolver use). `_composition_hash` operates on a single tree dict and is local to this module.

### Deduplication

Within each `composition_hash` group, the candidate with the highest `oos_metrics['sharpe']` is retained. Ties resolve in favor of whichever was encountered first. Docs without a sharpe value use `-inf` for comparison (a doc with any real sharpe always wins over one with a missing sharpe). `stats['deduped']` counts the number of candidates removed by dedup.

### Stats invariant

For a successful (non-exception) run, the 7 stats keys account for all docs:

```
pulled == (valid + missing_edn_string + parse_failed + validate_rejected
           + sharpe_filtered + deduped) + (candidates dropped by limit)
```

`valid` reflects the final candidate count after dedup and sharpe filtering but before `limit`. When `limit` truncates, `valid` still equals the number returned (it is set from `len(final_candidates)` after limit is applied).

## Failure Modes and D-1 Contract

| Condition | `reason` | `available` |
|-----------|----------|-------------|
| `MONGO_URI` not set | `"KeyError"` | `False` |
| pymongo connection error | `"ServerSelectionTimeoutError"` (or similar) | `False` |
| `atlas_cache.cached_pull` returns `None` (cache miss + fetch failed + no stale row) | `"AtlasCacheUnavailable"` | `False` |
| `atlas_cache.cached_pull` returns a non-list payload (corrupt cache) | `"TypeError"` | `False` |
| Any other exception in the outer try block | `type(exc).__name__` | `False` |
| Empty collection (zero docs) | — | `True` (candidates=[], stats.pulled=0) |

The `"AtlasCacheUnavailable"` reason is a named sentinel (not `type(exc).__name__`) for the `raw_docs is None` branch. This string predates the HF-1 route wiring (2026-06-17); no change was made to the sentinel value.

## DB Isolation

`community_strats.py` does not import `database`, `autotuner`, or any execution module. No cross-join with the state DB, optimization DB, or lens warehouse. The Atlas cache DB (`alphabot_atlas_cache.db`) is accessed only through `atlas_cache.cached_pull` — this module has no direct sqlite3 connection. `MONGO_URI` is read inside `fetch_fn` only and is never returned, logged, or stored by this module.

## Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `MONGO_URI` | *(required when fetching)* | MongoDB Atlas connection string. Read lazily inside `fetch_fn`; never stored or returned. |
| `ATLAS_CACHE_DB_PATH` | `alphabot_atlas_cache.db` | Cache DB path, owned by `advisors/atlas_cache.py`. Override in tests via `monkeypatch.setenv`. |
| `ATLAS_CACHE_TTL_DAYS` | `7` | Cache TTL; respected by `cached_pull` when `force_refresh=False`. |

## Internal Dependencies

- `advisors.atlas_cache` — `cached_pull` for weekly-TTL Atlas read routing
- `advisors.symphony_schema` — `validate_tree`, `extract_tickers`
- `json` — `edn_string` parsing and composition hash canonical form
- `hashlib` — SHA-256 composition hash
- `os` — `MONGO_URI` env read inside `fetch_fn`
- `pymongo` — lazy-imported inside `fetch_fn` only; not a module-level import

No imports from `database.py`, `autotuner.py`, `app.py`, or any execution module.

## Production Caller

`advisors/community_strats.py` is called from production by the Strategy Builder route handler `ai_advisor_strategy_builder_run()` in `app.py` (handler defined at `app.py:3395`).

The route lazily imports `load_community_strategies` at `app.py:3421` (inside the handler body — never at module level, preserving the CC-2 import boundary). The community load is **best-effort**:

```python
# app.py:3440-3448
community_candidates: list = []
try:
    _community = load_community_strategies(force_refresh=False)
    community_candidates = community_candidate_infos(
        _community, max_candidates=MAX_COMMUNITY_CANDIDATES_PER_RUN
    )
except Exception as exc:
    _daemon_log.warning("community-strats load skipped: %s", type(exc).__name__)
    community_candidates = []
```

Key invariants of the production call:
- `force_refresh=False` is mandatory — the weekly Atlas cache TTL is the operator bill-protection directive. A per-request forced pull is never acceptable.
- Any exception (Atlas down, `MONGO_URI` unset, cache miss, adapter failure) logs only the exception class name and degrades to `community_candidates=[]`. The proposal run completes as template-only.
- `community_candidates=` is forwarded to `propose_strategies` at `app.py:3457`. When non-empty, community candidates enter the single-batch FDR gate alongside template candidates (anti-overfit invariant — DE-PSW-001). When empty or `None`, `propose_strategies` behaves identically to the pre-wiring template-only path.

See `DE-HF1-001` in `DECISIONS.md` for the full architectural rationale.
