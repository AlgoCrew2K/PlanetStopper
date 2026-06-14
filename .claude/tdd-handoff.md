# TDD Handoff — community-strats-loader
Plan: feature-plans/community-strats-loader.md
Branch: team/community-strats
Phase: red

## Test Files
- tests/advisors/test_community_strats.py

## Fixture Files
None — fixtures are built inline via symphony_schema constructors in test helpers.

## A/C Coverage Matrix
| A/C ID | Description | Test File | Test Name(s) | Status |
|--------|-------------|-----------|--------------|--------|
| AC-1 | Atlas read routes through cached_pull; second call within TTL does NOT call fetch_fn | test_community_strats.py | test_cache_routing_first_call_invokes_fetch_fn_once, test_cache_routing_second_call_within_ttl_skips_fetch | RED |
| AC-2 | force_refresh=True bypasses cache, calls fetch_fn again even when fresh | test_community_strats.py | test_force_refresh_calls_fetch_fn_despite_fresh_cache | RED |
| AC-3 | Returned candidate has {sid,name,tree,tickers,oos_metrics,composition_hash}; tree passes validate_tree==[]; tickers==extract_tickers(tree) | test_community_strats.py | test_candidate_shape_has_required_keys, test_candidate_tree_passes_validate_tree, test_candidate_tickers_match_extract_tickers | RED |
| AC-4 | Bad edn / unparseable / validate_tree-rejected counted in stats; valid remainder returned | test_community_strats.py | test_missing_edn_string_counted_in_stats, test_unparseable_edn_counted_in_stats, test_validate_rejected_counted_in_stats, test_mixed_valid_and_invalid_returns_valid_remainder | RED |
| AC-5 | Dedup by composition hash — two same-hash docs collapse to one retaining higher sharpe | test_community_strats.py | test_dedup_same_hash_keeps_higher_sharpe, test_dedup_count_reflected_in_stats | RED |
| AC-6 | min_oos_sharpe excludes present-sharpe-below-floor; keeps docs lacking sharpe; limit caps | test_community_strats.py | test_min_oos_sharpe_excludes_below_floor, test_min_oos_sharpe_keeps_missing_sharpe_docs, test_limit_caps_returned_candidates | RED |
| AC-7 | Never-raising + D-1: any failure → available=False, reason=type(exc).__name__ ONLY | test_community_strats.py | test_mongo_down_returns_available_false, test_available_false_has_required_keys, test_d1_reason_is_exception_class_name_only, test_d1_no_mongo_uri_substring_in_return, test_d1_no_host_substring_in_return, test_function_never_raises | RED |
| AC-8 | MONGO_URI never written to cache DB or returned; no cross-DB imports | test_community_strats.py | test_mongo_uri_not_stored_in_cache_db, test_mongo_uri_not_in_return_value_recursive, test_no_autotuner_or_execution_import | RED |
| AC-9 | Mongo projection excludes heavy fields (backtest/quantstats arrays) | test_community_strats.py | test_projection_excludes_heavy_fields | RED |

## Import Stubs Created
- advisors/community_strats.py — exports `load_community_strategies(*, limit=None, min_oos_sharpe=None, client=None, force_refresh=False) -> dict`; returns always-false honest-empty; NO logic

## Questions for User
- The feature plan references "edn_string" parsed from Mongo docs but does not specify the wire format. Tests treat edn_string as a JSON-encoded tree dict (json.loads). If an EDN library is intended, the implementer must document this; the test contract (parse_failed on bad input, validate_tree on result) is format-agnostic.

## Behavioral Test Plan
N/A — no UI surface. All tests are unit tests.

## Status Log
- [2026-06-14] test-writer: Starting RED phase for community-strats-loader (AC-1..AC-9)
- [2026-06-14] test-writer: RED complete — 32 tests total (24 failing on stub, 7 green security/invariant guards, 1 skipped pending pymongo call). Stub created at advisors/community_strats.py. Committed at c1bca14 on team/community-strats.

## Implementation Notes for Implementer
- edn_string wire format: implement as json.loads (safest, no eval). parse_failed on any ValueError/JSONDecodeError.
- atlas_cache.cached_pull collection name: "captplanet.strategies" (matches AC-1/AC-2 spy tests).
- The 7 security invariant tests (PASSED) are not to be broken — they assert things that must hold on both stub and real implementation.
- The 1 skipped projection test activates once find() is called with a projection arg.
- Trees must be deserialised from edn_string then passed through validate_tree ([] = keep; errors = validate_rejected++).
- Dedup: use database.compute_composition_hash on tickers list from extract_tickers(tree). Keep doc with higher oos_metrics.get('sharpe', -inf) when hashes collide.
- Missing sharpe: treat as -inf for dedup comparison only; keep doc regardless of min_oos_sharpe floor.
