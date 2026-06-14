# Feature: Community-Strategies Loader (Atlas, through the weekly cache)
Status: ready
Created: 2026-06-14

## Summary
Rebuild — via a real Agent Team — the `advisors/community_strats.py` loader that reads the `captplanet` MongoDB Atlas `strategies` collection and returns validated, deduplicated community-symphony candidates for the Strategy Builder / propose_strategies suite. It was ripped (built by a standalone agent in violation of the team rule). The rebuild is NOT a straight restore: per the operator's weekly-cache directive, the Atlas read MUST go through `advisors/atlas_cache.cached_pull(...)` (weekly TTL) — the ripped version pulled Mongo directly on every call. Off-execution-path, advisory-only, never-raising. No production caller wired in THIS cycle (propose_strategies wiring is the next cycle).

## Recovered contract ground truth (from pre-rip SHA c1bf5dc, recovered read-only)
- Public API: `load_community_strategies(*, limit: int | None = None, min_oos_sharpe: float | None = None, client=None) -> dict`.
- Source: `captplanet.strategies` (MongoDB Atlas) via lazy-imported pymongo. Projection `{sid, name, edn_string, oos_metrics}` ONLY — never pull the multi-MB backtest/quantstats arrays.
- Returns dict: `{available: bool, candidates: list[dict], stats: dict, source: "captplanet", reason?: str}`. Each candidate `{sid, name, tree, tickers, oos_metrics, composition_hash}`. `stats` = `{pulled, valid, missing_edn_string, parse_failed, validate_rejected, sharpe_filtered, deduped}`.
- Validation: `symphony_schema.validate_tree(tree)` (HARD errors only); tickers via `symphony_schema.extract_tickers(tree)` (excludes `%`). Dedup by tree-structural SHA-256 hash (see AC-5 below) — retain the highest OOS Sharpe per hash.
- Sharpe filter: docs LACKING `oos_metrics['sharpe']` are KEPT (not excluded); `min_oos_sharpe` excludes only docs that HAVE a sharpe below the floor.
- Error contract D-1: `available=False` + `reason=type(exc).__name__` ONLY (no host/credential/message leak).

## The deliberate change from the ripped version (operator directive)
The pymongo fetch is wrapped in a `fetch_fn` closure and routed through `atlas_cache.cached_pull("captplanet.strategies", fetch_fn, ttl_days=<ATLAS_CACHE_TTL_DAYS default 7>)`. The raw projected docs (the cacheable payload) are what `fetch_fn` returns; validation/dedup/ticker-extraction run on the cached payload on every call (cheap, in-process) — only the network/Atlas read is cached weekly. `force_refresh` is plumbed through as an optional kwarg for an operator escape hatch.

## Acceptance Criteria
- [ ] AC-1: `load_community_strategies()` routes the Atlas read through `atlas_cache.cached_pull` — a second call within the TTL does NOT hit Mongo (assert the pymongo fetch closure call count == 0 on the second call; payload served from cache). The first call fetches once.
- [ ] AC-2: `force_refresh=True` bypasses the cache and re-fetches (closure called), even within TTL.
- [ ] AC-3: A returned candidate carries `{sid, name, tree, tickers, oos_metrics, composition_hash}`; `tree` passes `symphony_schema.validate_tree` (== []); `tickers` == `extract_tickers(tree)` (no `%`).
- [ ] AC-4: Docs whose `edn_string` is missing/unparseable/`validate_tree`-rejected are excluded and counted in `stats` (`missing_edn_string` / `parse_failed` / `validate_rejected`); the call still returns `available=True` with the valid remainder.
- [ ] AC-5: Dedup by tree-structural composition hash — two docs with structurally identical trees (same logic, same tickers, same weights) collapse to one, retaining the higher `oos_metrics['sharpe']`; `stats['deduped']` reflects the count removed. The hash is computed by a local `_composition_hash(tree)` function: strip all `id` keys recursively (`_strip_ids`) → `json.dumps(sort_keys=True, separators=(",", ":"))` → `hashlib.sha256(...).hexdigest()`. **This is NOT `database.compute_composition_hash`**, which takes a `list[str]` of symphony IDs and is used for portfolio-set identity in the mode-resolver — an entirely different function for an entirely different purpose.
- [ ] AC-6: `min_oos_sharpe=X` excludes docs with a present sharpe `< X` (counted in `stats['sharpe_filtered']`) but KEEPS docs lacking a sharpe entirely. `limit=N` caps returned candidates at N (after dedup/filter).
- [ ] AC-7 (never-raising + D-1): any failure (Mongo unavailable, cache failure, projection error) → `{available: False, reason: <ExcClassName>, source: "captplanet", candidates: [], stats: {...}}`; `reason` is ONLY `type(exc).__name__` — assert no MONGO_URI / host / message substring leaks. The function NEVER raises.
- [ ] AC-8 (secrets + isolation): `community_strats` reads `MONGO_URI` only to connect (lazy, inside `fetch_fn`); the URI is NEVER written to the cache DB, returned, or logged. No cross-join across state/optimization/atlas-cache DBs. Imports no autotuner/execution code.
- [ ] AC-9 (projection): the Mongo query uses a field projection limited to `{sid, name, edn_string, oos_metrics}` — assert the projection dict passed to pymongo excludes heavy fields (backtest/quantstats arrays).

## Architecture
New `advisors/community_strats.py` (pure stdlib + lazy pymongo + `advisors.atlas_cache` + `advisors.symphony_schema`). `load_community_strategies` builds a `fetch_fn` closure (lazy `pymongo.MongoClient(os.environ["MONGO_URI"])`, projected `find`, returns a list of plain dicts) → `atlas_cache.cached_pull("captplanet.strategies", fetch_fn, ttl_days=..., force_refresh=...)` → iterate payload: parse `edn_string`→tree (JSON, not EDN format), `validate_tree`, `extract_tickers`, local `_composition_hash` (tree-structural SHA-256, not `database.compute_composition_hash`), dedup, sharpe-filter, limit → assemble `{available, candidates, stats, source}`. Every external op wrapped; any exception → D-1 honest-empty. No Flask dependency. Mirrors the never-raising / honest-availability pattern of the lens producers and atlas_cache.

## Design-System Mapping
N/A — no UI.

## Edge Cases
- Empty collection → `available=True`, `candidates=[]`, `stats.pulled=0`.
- `edn_string` parses but `validate_tree` returns errors → `validate_rejected++`, skip.
- All docs deduped to one hash → one candidate, `deduped` = pulled-1.
- Cache MISS but Mongo down → D-1 honest-empty (atlas_cache returns its sentinel; loader maps to `available=False`).
- Doc with `oos_metrics` absent entirely → treated as no-sharpe (kept unless dedup loses it).
- `MONGO_URI` unset → KeyError caught → D-1 (`reason="KeyError"`).

## Security Considerations
`MONGO_URI` lazy-read inside `fetch_fn` only; never persisted/returned/logged (atlas_cache already strips/stores only the payload — verify the payload contains no credential). edn parsing must not eval/exec — use the project's existing safe edn parse (confirm what the ripped version used; if it used a hand parser, reuse it; NO `eval`). Bounded payload (projection caps doc size). Cache DB isolated. Off-execution-path.

## Testing Strategy
RED tests (`tests/advisors/test_community_strats.py`) with: a fake `atlas_cache.cached_pull` or an isolated temp `ATLAS_CACHE_DB_PATH` to assert cache hit/miss/force semantics (closure call counts); a spy fetch closure returning fixture Mongo docs (captured-shape projected docs — fixture provenance: schema-derived from the recovered c1bf5dc projection, validated by `symphony_schema.validate_tree`); golden trees built via `symphony_schema` constructors (no hardcoded producer metrics — assert shape/presence/dedup-count, not literal sharpe values); D-1 secret-leak walk asserting `MONGO_URI` never appears in returns/cache; never-raising on Mongo-down / cache-fail / bad-edn. No live Atlas (all mocked). `-n0` gate on `tests/advisors/` before the PM merges.

## Decisions
| Decision | Rationale |
|----------|-----------|
| Route Atlas read through atlas_cache.cached_pull (NOT direct pymongo) | Operator weekly-cache directive — protect the provider's bill; the ripped version pulled every call |
| Rebuild via a real Agent Team | Operator hard rule: teams default; new codepath |
| Keep docs lacking a sharpe (don't exclude) | Recovered c1bf5dc behavior; absence of a metric is not a failing metric |
| Reuse recovered projection {sid,name,edn_string,oos_metrics} | Avoid pulling multi-MB backtest arrays — bandwidth + provider cost |
| Tree-structural hash (local _composition_hash), not database.compute_composition_hash | database.compute_composition_hash takes list[str] symphony IDs — different function, different purpose; verified vs ripped c4d6a36 |

## Scope Boundaries
- **IN**: `advisors/community_strats.py` (`load_community_strategies` through atlas_cache) + `tests/advisors/test_community_strats.py` + doc-writer docs (docs/generated + DECISIONS.md + CLAUDE.md key-files draft).
- **OUT**: propose_strategies community-candidate wiring (NEXT cycle — depends on this); the frontrunner loader/builder; the lenses; any route/UI; any write-back to Atlas; auto-refresh cron. No change to atlas_cache.py or symphony_schema.py (consume only).
