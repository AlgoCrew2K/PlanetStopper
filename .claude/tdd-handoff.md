# TDD Handoff
Plan: feature-plans/community-strats-loader.md
Branch: pr/community-strats-loader
Phase: red

## Test Files
- `tests/advisors/test_community_strats.py` — 52 tests (45 RED, 7 already-GREEN)

## Behavioral Test Plan
N/A — no UI surface. This is a read-only data loader, off-execution-path, advisory-only.
No e2e spec required (no Flask route, no browser interaction).

## A/C Coverage Matrix

| A/C ID | Description | Test Class | Test Name(s) | Status |
|--------|-------------|------------|--------------|--------|
| AC-1 | loader returns dict with required top-level keys | TestAC1BasicHappyPath | test_load_returns_dict_with_available_key, test_load_returns_candidates_list, test_load_returns_stats_dict_with_required_keys, test_load_returns_source_string | RED |
| AC-1 | single valid doc → one candidate | TestAC1BasicHappyPath | test_single_valid_doc_produces_one_candidate | RED |
| AC-1 | candidate has all required keys | TestAC1BasicHappyPath | test_candidate_has_all_required_keys | RED |
| AC-1 | sid/name pass through from doc | TestAC1BasicHappyPath | test_candidate_sid_matches_doc, test_candidate_name_matches_doc | RED |
| AC-1 | tree is a dict | TestAC1BasicHappyPath | test_candidate_tree_is_a_dict | RED |
| AC-1 | returned tree passes validate_tree | TestAC1BasicHappyPath | test_candidate_tree_passes_validate_tree | RED |
| AC-1 | tickers is a set or list | TestAC1BasicHappyPath | test_candidate_tickers_is_a_set_or_list | RED |
| AC-1 | composition_hash is a non-empty string | TestAC1BasicHappyPath | test_candidate_composition_hash_is_non_empty_string | RED |
| AC-1 | oos_metrics present when doc provides it | TestAC1BasicHappyPath | test_candidate_oos_metrics_matches_doc_value | RED |
| AC-1 | stats.pulled >= 1 | TestAC1BasicHappyPath | test_stats_pulled_count_is_positive_integer | RED |
| AC-1 | stats counts are non-negative | TestAC1BasicHappyPath | test_stats_valid_counts_non_negative | RED |
| AC-2 | non-JSON edn_string is skipped, counted invalid | TestAC2InvalidDocsSkipped | test_non_json_edn_string_is_skipped | RED |
| AC-2 | missing edn_string field is skipped | TestAC2InvalidDocsSkipped | test_missing_edn_string_field_is_skipped | RED |
| AC-2 | tree failing validate_tree is skipped | TestAC2InvalidDocsSkipped | test_tree_that_fails_validate_tree_is_skipped | RED |
| AC-2 | loader never raises on all-invalid batch | TestAC2InvalidDocsSkipped | test_loader_never_raises_on_all_invalid_docs | RED |
| AC-2 | mixed batch: only valid docs in candidates | TestAC2InvalidDocsSkipped | test_valid_and_invalid_mix_only_valid_in_candidates | RED |
| AC-3 | identical composition collapses to one candidate | TestAC3Deduplication | test_two_identical_composition_docs_collapse_to_one | RED |
| AC-3 | dedup retains higher-OOS-sharpe copy | TestAC3Deduplication | test_dedup_retains_higher_oos_sharpe_copy | RED |
| AC-3 | different compositions are NOT deduped | TestAC3Deduplication | test_different_composition_docs_are_not_deduped | RED |
| AC-3 | same-tree docs share composition_hash | TestAC3Deduplication | test_dedup_composition_hash_is_identical_for_same_tree | RED |
| AC-4 | simple-tree tickers match extract_tickers | TestAC4TickerExtraction | test_simple_tree_tickers_match_extract_tickers | RED |
| AC-4 | frontrunner watched tickers present (condition.tickers[]) | TestAC4TickerExtraction | test_frontrunner_tree_watched_tickers_present | RED |
| AC-4 | frontrunner basket tickers present (asset nodes) | TestAC4TickerExtraction | test_frontrunner_tree_basket_tickers_present | RED |
| AC-4 | '%' placeholder absent from returned tickers | TestAC4TickerExtraction | test_frontrunner_tree_percent_placeholder_absent | RED |
| AC-4 | tickers non-empty for tree with assets | TestAC4TickerExtraction | test_tickers_is_non_empty_for_tree_with_assets | RED |
| AC-5 | limit caps candidate count | TestAC5Filtering | test_limit_caps_candidate_count | RED |
| AC-5 | limit=None returns all valid | TestAC5Filtering | test_limit_none_returns_all_valid | RED |
| AC-5 | min_oos_sharpe excludes docs below threshold | TestAC5Filtering | test_min_oos_sharpe_excludes_docs_below_threshold | RED |
| AC-5 | docs lacking oos metric are KEPT under min_oos_sharpe | TestAC5Filtering | test_min_oos_sharpe_keeps_docs_with_no_metric | RED |
| AC-5 | limit=0 returns empty candidates | TestAC5Filtering | test_limit_zero_returns_empty_candidates | RED |
| AC-6 | .find() raises → available=False | TestAC6HonestAvailability | test_find_raises_returns_available_false | RED |
| AC-6 | reason is bare type name only | TestAC6HonestAvailability | test_find_raises_reason_is_bare_type_name | RED |
| AC-6 | D-1: exception message not in reason | TestAC6HonestAvailability | test_find_raises_reason_does_not_contain_exception_message | RED |
| AC-6 | candidates=[] on connection failure | TestAC6HonestAvailability | test_find_raises_candidates_is_empty_list | RED |
| AC-6 | empty collection → available=False | TestAC6HonestAvailability | test_empty_collection_returns_available_false | RED |
| AC-6 | empty collection reason is static string | TestAC6HonestAvailability | test_empty_collection_reason_is_static_string | RED |
| AC-6 | loader never raises on any .find() exception | TestAC6HonestAvailability | test_loader_never_raises_on_find_exception | RED |
| AC-6+AC-7 | URI absent from available=False result reason | TestAC6HonestAvailability | test_available_false_result_has_no_reason_containing_uri | RED |
| AC-7 | MONGO_URI absent from successful result | TestAC7SecretsNeverLeaked | test_mongo_uri_absent_from_successful_result | RED |
| AC-7 | MONGO_URI absent from error result | TestAC7SecretsNeverLeaked | test_mongo_uri_absent_from_error_result | RED |
| AC-7 | MONGO_URI absent from empty-collection result | TestAC7SecretsNeverLeaked | test_mongo_uri_absent_from_empty_collection_result | RED |
| AC-8 | module importable without pymongo | TestAC8BoundaryAssertions | test_module_importable_without_pymongo | GREEN (stub satisfies) |
| AC-8 | no Flask route decorator in source | TestAC8BoundaryAssertions | test_module_has_no_flask_route_decorator | GREEN (stub satisfies) |
| AC-8 | no LIVE_EXECUTION reference in source | TestAC8BoundaryAssertions | test_module_has_no_live_execution_reference | GREEN (stub satisfies) |
| AC-8 | app.py does not import community_strats | TestAC8BoundaryAssertions | test_app_py_does_not_import_community_strats | GREEN (stub satisfies) |
| AC-8 | load_community_strategies is callable | TestAC8BoundaryAssertions | test_load_community_strategies_is_the_public_entrypoint | GREEN (stub satisfies) |
| AC-8 | all params are keyword-only | TestAC8BoundaryAssertions | test_load_community_strategies_accepts_keyword_only_params | GREEN (stub satisfies) |
| AC-8 | _connect_mongo is defined and callable | TestAC8BoundaryAssertions | test_connect_mongo_is_internal | GREEN (stub satisfies) |

## Already-GREEN (7 tests — all AC-8 boundary assertions)

These 7 tests pass against the stub because they assert STATIC properties of the source file
and import signature — not runtime behavior:
- No Flask routes in source (text scan)
- No LIVE_EXECUTION in source (text scan)
- app.py does not import community_strats (text scan)
- Module importable without pymongo (stub has no top-level import of pymongo)
- load_community_strategies is callable (stub defines it)
- All params are keyword-only (stub signature is correct)
- _connect_mongo is defined (stub defines it)

All 7 must STAY GREEN after implementation. They will correctly FAIL if the implementer
violates AC-8 boundaries (adds a Flask route, adds LIVE_EXECUTION, eager-imports pymongo).

## Import Stubs Created

`advisors/community_strats.py` — minimal stub with:
- `load_community_strategies(*, limit=None, min_oos_sharpe=None, client=None)` — raises NotImplementedError
- `_connect_mongo()` — raises NotImplementedError
- No logic, no imports of pymongo/dns, correct keyword-only signature
- ~12 lines total

## Fixtures Created

- `tests/fixtures/math/community_strats_loader_basic.json` — documents the input doc shapes
  and expected output contract. Not used directly by tests (tests construct docs inline via
  symphony_schema constructors); serves as a persisted spec for the math layer.
- `tests/fixtures/math/community_strats_loader_frontrunner.json` — documents the frontrunner
  tree fixture: watched_tickers, basket_tickers, percent_placeholder. Tests load this fixture
  and use the values to BUILD the tree via symphony_schema constructors, then assert behavior.
  No producer-computed values hardcoded.

## Questions for User
None. The A/C is fully specified in the feature plan. The one design decision that affects
test behavior is the dedup quality metric: the plan says "Keep the highest-OOS-quality where
metrics exist" and the test uses `sharpe` as the quality metric (consistent with AC-5 which
also uses sharpe for min_oos_sharpe). If the implementer uses a different quality field,
the dedup test will surface it correctly.

## What the Implementer Must Do (blind to the plan — read handoff only)

Implement `advisors/community_strats.py`. Replace the stub. The public surface is:

### `load_community_strategies(*, limit=None, min_oos_sharpe=None, client=None) -> dict`

Returns: `{available: bool, candidates: list[dict], stats: {pulled, valid, invalid, deduped}, source: str, reason?: str}`

Each candidate: `{sid, name, tree (validated raw_value dict), tickers (set/list from extract_tickers), oos_metrics (dict|None), composition_hash (str)}`

#### Pipeline (in order):

1. **Get collection.** If `client` is not None, extract the collection from it (captplanet.strategies). Else call `_connect_mongo()` to get it.
2. **Call collection.find()**. Wrap in try/except. On any exception: return `{available: False, candidates: [], stats: {pulled:0, valid:0, invalid:0, deduped:0}, source: "captplanet", reason: type(exc).__name__}`. NEVER include the exception message or the MONGO_URI env value in reason.
3. **Handle empty result.** If find() returns no docs: return `{available: False, reason: "EmptyCollection", candidates: [], stats: {pulled:0, valid:0, invalid:0, deduped:0}, source: "captplanet"}`.
4. **Honour limit.** If limit is not None, take only the first `limit` docs from find() (or pass limit to find() — implementer's choice).
5. **Parse each doc:**
   - If doc missing `edn_string` key or `sid` key → skip, increment invalid.
   - `json.loads(doc["edn_string"])` → on JSONDecodeError → skip, increment invalid.
   - `validate_tree(tree)` → if errors not empty → skip, increment invalid.
   - Extract tickers via `symphony_schema.extract_tickers(tree)`.
   - Collect `oos_metrics = doc.get("oos_metrics")` (may be None).
   - Apply `min_oos_sharpe` filter: if oos_metrics has a "sharpe" key AND sharpe < min_oos_sharpe → skip (NOT counted as invalid — counted as filtered). Docs with no oos_metrics or no "sharpe" key are KEPT.
   - Compute `composition_hash`: a deterministic hash of the validated tree (e.g. sha256 of json.dumps(tree, sort_keys=True, separators=(',',':'))). Do NOT use database.compute_composition_hash (it takes a list of sid strings, not a tree).
6. **Dedup.** Group candidates by composition_hash. When multiple docs share a hash, retain the one with the highest oos_metrics["sharpe"] (or any if none have sharpe). Count each removed duplicate in stats.deduped.
7. **Return.**
   ```python
   {
     "available": True,
     "candidates": [list of candidate dicts],
     "stats": {"pulled": N_docs_fetched, "valid": N_valid, "invalid": N_invalid, "deduped": N_deduped},
     "source": "captplanet",
   }
   ```

### `_connect_mongo() -> collection`

1. Lazy-import: `from dns.resolver import Resolver` and `import pymongo` INSIDE this function (NOT at module top).
2. Override DNS: `resolver = Resolver(configure=False); resolver.nameservers = ["8.8.8.8", "1.1.1.1"]`; assign to `dns.resolver.default_resolver` (do NOT call `override_system_resolver`).
3. `client = pymongo.MongoClient(os.environ["MONGO_URI"])`.
4. Return `client["captplanet"]["strategies"]`.
5. NEVER log the URI. NEVER return the URI.

### Hard rules for implementation:
- No `@app.route` anywhere in the file.
- No `LIVE_EXECUTION` anywhere in the file.
- No top-level `import pymongo` or `import dns` — lazy only inside `_connect_mongo`.
- D-1: `reason` field is always `type(exc).__name__` — never `str(exc)`, never `repr(exc)`.
- All 45 RED tests must go GREEN. All 7 already-GREEN tests must stay GREEN.

## Status Log
- [2026-06-14] test-writer: Starting RED phase (community-strats-loader)
- [2026-06-14] test-writer: RED complete — 45 tests RED (all fail on NotImplementedError from stub), 7 tests GREEN (AC-8 static boundary guards). 2 fixtures written. 1 stub created. Failure mode confirmed: NotImplementedError, not syntax/import errors.
