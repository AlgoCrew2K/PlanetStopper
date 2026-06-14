# Feature Plan — community-strats loader (Mongo → candidate trees, slice 1)

**Status:** ready
**Branch:** `pr/community-strats-loader` (worktree `.claude/pr-worktrees/community-strats`, forked from origin/main `aa2aacb`)
**Classification:** NEW CODEPATH — full TDD (RED → GREEN → review), gated-solo flow, PM-gated. Tier-3 feature delivered in bounded slices; this is slice 1 (read-only loader, no production caller).

## Summary
The operator's priority: consume the community strategy library from the `captplanet` MongoDB (`strategies` collection, ~11k docs) into the Strategy Builder. The grammar foundation (Cycles A+B) made `validate_tree` corpus-aligned and the constructors compound-aware, so real community symphonies now validate cleanly. This slice builds a **read-only loader** that pulls, parses, validates, and dedups community strategies into candidate records — the raw material the Strategy Builder will later propose from. It is NOT yet wired into `propose_strategies` (slice 2) and does NOT touch the `frontrunners` collection (slice 3).

## [PM-ASSUMED] design decisions (documented, not asked — operator may redirect before merge)
- **edn_string is JSON** (v2 grammar audit, VERIFIED-CORPUS 10,441/10,441 parse as JSON) → parse with `json.loads`, no EDN parser.
- **Dedup key = composition hash** of the validated tree (reuse `database.compute_composition_hash` if applicable to a raw_value tree, else a deterministic hash of the canonicalized tree) — falls back to `sid` if hashing is impractical. Keep the highest-OOS-quality duplicate.
- **Quality fields** surfaced from `oos_metrics` (and `oos_dd`/`oos_months` when present) for later ranking; the loader does NOT rank/gate (that's the FDR gate's job in slice 2) — it only surfaces metrics + an optional `min_oos_*` pre-filter.
- **Invalid trees** (validate_tree returns HARD errors, or edn_string missing/unparseable) are SKIPPED (counted, not raised) — honest-availability.
- **No live trade interaction, off-execution-path, advisory-only.** Mongo import is lazy (CC-2). Secrets: `MONGO_URI` read from env, NEVER logged/persisted; D-1 (`reason = type(exc).__name__` only).

## Public API
- `load_community_strategies(*, limit: int | None = None, min_oos_sharpe: float | None = None, client=None) -> dict`
  → `{available: bool, candidates: list[dict], stats: {pulled, valid, invalid, deduped}, source: str, reason?: str}`.
  Each candidate: `{sid, name, tree (validated raw_value dict), tickers (from extract_tickers), oos_metrics (dict or None), composition_hash}`.
  `client=` injectable for tests (a mock Mongo client / collection); when None, the loader lazily builds one via `_connect_mongo()`.
- `_connect_mongo()` (internal) — the verified connect recipe: `dns.resolver.default_resolver = Resolver(configure=False); .nameservers=['8.8.8.8','1.1.1.1']` (do NOT `override_system_resolver`); `MongoClient(os.environ['MONGO_URI'])`; returns `client['captplanet']['strategies']`. Never logs the URI.

## Acceptance Criteria
- **AC-1:** `load_community_strategies(client=<mock>)` pulls docs from the injected collection, parses each `edn_string` via `json.loads`, and returns candidate records with `sid`/`name`/`tree`/`tickers`/`oos_metrics`/`composition_hash`.
- **AC-2:** each returned `tree` passed `validate_tree` with zero HARD errors; a doc whose tree FAILS validate_tree (or whose `edn_string` is missing/unparseable) is SKIPPED and counted in `stats.invalid` (never raises).
- **AC-3:** dedup — two docs with the same composition (same validated tree shape) collapse to one candidate (`stats.deduped` counts removed); the retained one is the higher-OOS-quality where metrics exist.
- **AC-4:** `tickers` is populated via `symphony_schema.extract_tickers` (so it correctly includes condition-block tickers from compound/frontrunner symphonies, excluding the `"%"` placeholder).
- **AC-5:** `limit` caps the number pulled; `min_oos_sharpe` pre-filters by OOS sharpe when that metric is present (docs lacking the metric are kept unless filtered — documented).
- **AC-6 (honest-availability + D-1):** Mongo connection failure / empty collection → `{available: False, reason: type(exc).__name__ or "EmptyCollection", candidates: []}`; never raises; `reason` carries NO URI/secret/exception message.
- **AC-7 (secrets):** `MONGO_URI` is never logged or included in any returned field; `_connect_mongo` reads it from env only.
- **AC-8 (boundaries):** no Flask route, no `LIVE_EXECUTION`, no production caller (not imported by app.py/engine); pymongo + dns imports are lazy (module import works without them installed, fails only on actual connect).

## Architecture
New `advisors/community_strats.py` (pure-stdlib except lazy pymongo/dnspython inside `_connect_mongo`). Reuses `symphony_schema.validate_tree` + `extract_tickers`. No new dependency on the warehouse or state DB. Tests inject a mock collection (list of fake docs with `edn_string` built via `symphony_schema` constructors → `json.dumps`), so CI never hits live Mongo.

## Edge Cases
- `edn_string` present but `json.loads` fails → skip, count invalid.
- doc missing `edn_string` / `sid` → skip.
- `oos_metrics` absent or null → candidate kept with `oos_metrics=None` (unless `min_oos_sharpe` filters it).
- duplicate sids vs duplicate compositions (different sids, same tree) — dedup on composition, not sid.
- very large trees (corpus has 150KB+ JSON / thousands of nodes) — validate_tree is iterative (safe); `limit` bounds memory.
- `"%"` placeholder tickers must NOT appear in `tickers`.

## Security Considerations
`MONGO_URI` is a live credential (Atlas SRV with embedded user:pass) — read from env only, never logged, never in returned data, never committed. D-1 strips exception detail. Read-only queries (find), no writes to Mongo. No SSRF (host is fixed in the env URI, not user-controlled).

## Testing Strategy
RED tests inject a mock Mongo collection returning fake docs whose `edn_string` is `json.dumps` of trees built with `symphony_schema` constructors (incl. a compound/frontrunner tree to exercise condition-ticker extraction, and a deliberately-invalid tree to exercise skip-counting). Assert AC-1..AC-8. A secrets test asserts the URI never appears in returned dict/reason. No live Mongo in CI (all mocked). PM runs ONE live functional check against real Mongo before merge (separately, with .env).

## Scope Boundaries
- IN: the read-only loader + connect helper + parse/validate/dedup/metrics-surface + tests.
- OUT: wiring into `strategy_builder_engine.propose_strategies` (slice 2); the `frontrunners` collection / overlay application (slice 3); any ranking/FDR gating (the engine already owns that); any write to Mongo; any scheduled/automated pull; caching.
