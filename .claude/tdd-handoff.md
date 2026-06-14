# TDD Handoff — community-strats wiring (slice 2)

**Branch:** `pr/community-strats-wiring`
**Worktree:** `.claude/pr-worktrees/community-wire`
**Plan:** `feature-plans/community-strats-wiring.md`
**RED commit:** `d6a8fe9`
**Phase:** green

---

## IMPLEMENTER: Read this file only — do NOT read the feature plan.

### Files to modify

1. `advisors/strategy_builder_engine.py` — add constant, add adapter function, add param, add injection
2. `advisors/community_strats.py` — add `sharpe_filtered` counter

### What to add in `advisors/strategy_builder_engine.py`

**Step 1 — new constant** (near `MAX_CANDIDATES_PER_RUN`):
```python
# Community candidate sub-budget — caps how many loader records can be
# backtested per run. templates + community <= MAX_CANDIDATES_PER_RUN.
MAX_COMMUNITY_CANDIDATES_PER_RUN: int = 15
```

**Step 2 — new function** (add after the template library, before `propose_strategies`):
```python
def community_candidate_infos(records: list[dict]) -> list[CandidateInfo]:
    """Adapt community loader records → CandidateInfo list for propose_strategies.

    Skips records missing 'sid' or 'tree'. Never raises.
    candidate_id = 'community:<sid>'; template_id = 'community'.
    """
    result: list[CandidateInfo] = []
    for rec in records:
        sid = rec.get("sid")
        tree = rec.get("tree")
        if not sid or tree is None:
            continue
        result.append(
            CandidateInfo(
                candidate_id=f"community:{sid}",
                tree=tree,
                template_id="community",
                params={"sid": sid, "name": rec.get("name", "")},
            )
        )
    return result
```

**Step 3 — add `community_candidates` param to `propose_strategies`**:
```python
def propose_strategies(
    objective: Objective,
    universe: list[str],
    screen_config: ScreenConfig,
    live_returns: list[float],
    symphony_id: str = "",
    *,
    incumbent_oos_alpha: float = 0.0,
    default_oos_alpha: float = 0.0,
    community_candidates: list[CandidateInfo] | None = None,   # ADD THIS
) -> ProposalRun:
```

**Step 4 — inject community candidates in Step 1** (after the `_generate_candidate_trees` call):
```python
# Step 1: Generate candidate trees (objective-directed, bounded)
candidate_infos = _generate_candidate_trees(objective, universe)

# Inject community candidates (AC-2 / AC-3)
if community_candidates:
    comm = list(community_candidates[:MAX_COMMUNITY_CANDIDATES_PER_RUN])
    existing_ids = {c.candidate_id for c in candidate_infos}
    for c in comm:
        if c.candidate_id not in existing_ids:
            candidate_infos.append(c)
            existing_ids.add(c.candidate_id)
    # Enforce global cap
    candidate_infos = candidate_infos[:MAX_CANDIDATES_PER_RUN]
```

No other changes to `strategy_builder_engine.py` — the existing backtest loop, FDR gate,
screens, and persist logic all operate on `candidate_infos` and will naturally handle
community candidates once injected.

### What to add in `advisors/community_strats.py`

Add `sharpe_filtered` counter to the `_EMPTY_STATS` dict and the main loop:

```python
_EMPTY_STATS = {
    "pulled": 0,
    "valid": 0,
    "missing_edn_string": 0,
    "parse_failed": 0,
    "validate_rejected": 0,
    "deduped": 0,
    "sharpe_filtered": 0,    # ADD THIS
}
```

Declare counter at the top of the doc-processing loop:
```python
sharpe_filtered = 0   # ADD
```

In the `min_oos_sharpe` filter block, add the increment:
```python
if min_oos_sharpe is not None:
    if (
        oos_metrics is not None
        and isinstance(oos_metrics, dict)
        and "sharpe" in oos_metrics
        and oos_metrics["sharpe"] < min_oos_sharpe
    ):
        sharpe_filtered += 1    # ADD THIS LINE
        continue
```

Add `"sharpe_filtered": sharpe_filtered` to the returned stats dict.

Update the comment above the return:
```python
# pulled == valid + deduped + missing_edn_string + parse_failed
#          + validate_rejected + sharpe_filtered
```

---

## Test state: 38 RED / 3 GREEN

Run to verify:
```
python -m pytest tests/advisors/test_community_strats_wiring.py -n0 --tb=no -q
```
Expected: `38 failed, 3 passed`

---

## RED tests and failure causes

### AC-1 adapter (12 RED)
All fail with `AttributeError: module has no attribute 'community_candidate_infos'`.

### AC-2 injection (5 RED)
- Param tests: `AssertionError: 'community_candidates' not in sig.parameters`
- Batch tests: `AttributeError` from missing adapter + `TypeError` from missing param

### AC-3 cap + dedup (5 RED)
- Constant tests: `AttributeError: MAX_COMMUNITY_CANDIDATES_PER_RUN does not exist`
- Truncation + dedup tests: adapter + param absent

### AC-4 failure isolation (3 RED)
Adapter absent → can't build community_infos to inject

### AC-5 provenance (2 RED)
No community ids in persist calls (injection not yet wired)

### AC-6 no regression (6 RED)
`TypeError: propose_strategies() got unexpected keyword argument 'community_candidates'`

### AC-7 sharpe_filtered (4 RED)
`AssertionError: "sharpe_filtered" not in stats` (key absent from community_strats.py)

### Already GREEN (3 tests — correct by construction)
- `test_sharpe_filtered_does_not_increment_when_doc_above_threshold` — `.get("sharpe_filtered", 0) == 0` vacuously true when key absent but doc IS above threshold
- `test_sharpe_filtered_zero_when_no_filter_set` — same
- `test_sharpe_filtered_docs_without_metrics_not_counted` — same

These 3 must stay GREEN after implementation. They test that the counter is NOT
incremented in cases where it should not be — a correct implementation satisfies them too.

---

## Scope boundary (must NOT change)

- No Flask/route changes
- No FDR gate math changes
- No screen logic changes
- No `_generate_candidate_trees` changes
- No database schema changes
- No new tests (test-writer handles next adversarial cycle)

---

## Previous slice-1 handoff (archive — DO NOT use for this cycle)

## Test Files
- `tests/advisors/test_community_strats.py` — 71 tests (17 RED / 54 already-GREEN)

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
| AC-9 | limit=N applied at query level (cursor.limit or find kwarg) | TestAC9QueryEfficiency | test_limit_applied_via_cursor_limit_or_find_kwarg | RED |
| AC-9 | limit=1 edge case applied at query level | TestAC9QueryEfficiency | test_limit_one_applied_at_query_not_sliced | RED |
| AC-9 | limit=None does not impose a cursor restriction | TestAC9QueryEfficiency | test_no_limit_does_not_call_cursor_limit | GREEN (correct; buggy impl also passes this) |
| AC-9 | find() called with a projection dict | TestAC9QueryEfficiency | test_find_called_with_projection_dict | RED |
| AC-9 | projection includes edn_string | TestAC9QueryEfficiency | test_projection_includes_edn_string | RED |
| AC-9 | projection includes sid | TestAC9QueryEfficiency | test_projection_includes_sid | RED |
| AC-9 | projection includes oos_metrics | TestAC9QueryEfficiency | test_projection_includes_oos_metrics | RED |
| AC-9 | projection excludes backtest | TestAC9QueryEfficiency | test_projection_excludes_backtest | RED |
| AC-9 | projection excludes quantstats_metrics | TestAC9QueryEfficiency | test_projection_excludes_quantstats_metrics | RED |
| AC-10 | stats has missing_edn_string key (not generic invalid) | TestAC10GranularDropAccounting | test_stats_has_missing_edn_string_key | RED |
| AC-10 | stats has parse_failed key | TestAC10GranularDropAccounting | test_stats_has_parse_failed_key | RED |
| AC-10 | stats has validate_rejected key | TestAC10GranularDropAccounting | test_stats_has_validate_rejected_key | RED |
| AC-10 | stats does NOT have old 'invalid' key | TestAC10GranularDropAccounting | test_stats_does_not_have_invalid_key | RED |
| AC-10 | absent edn_string → missing_edn_string only, not others | TestAC10GranularDropAccounting | test_absent_edn_string_increments_missing_edn_string_not_others | RED |
| AC-10 | empty edn_string → missing_edn_string | TestAC10GranularDropAccounting | test_empty_edn_string_increments_missing_edn_string | RED |
| AC-10 | non-JSON edn_string → parse_failed only, not others | TestAC10GranularDropAccounting | test_bad_json_increments_parse_failed_not_others | RED |
| AC-10 | validate_tree-failing tree → validate_rejected only, not others | TestAC10GranularDropAccounting | test_validate_rejected_tree_increments_validate_rejected_not_others | RED |
| AC-10 | pulled == valid + deduped + missing_edn_string + parse_failed + validate_rejected | TestAC10GranularDropAccounting | test_pulled_equals_sum_of_all_drop_and_success_buckets | RED |
| AC-10 | invariant holds on all-valid batch (all drop keys == 0) | TestAC10GranularDropAccounting | test_sum_invariant_holds_for_all_valid_batch | RED |

## Already-GREEN (54 tests)
7 are AC-8 static boundary assertions (text scan / signature).
47 are prior-cycle AC-1..AC-9 tests that remain GREEN after the AC-10 re-pointing.

### AC-8 boundary assertions

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

Returns: `{available: bool, candidates: list[dict], stats: {pulled, valid, deduped, missing_edn_string, parse_failed, validate_rejected}, source: str, reason?: str}`

**AC-10 contract (replaces old `invalid` key):**
- `missing_edn_string`: docs with absent or empty `edn_string` field
- `parse_failed`: docs where `json.loads(edn_string)` raises `JSONDecodeError`
- `validate_rejected`: docs where parsed tree fails `validate_tree` (has hard errors)
- Invariant: `pulled == valid + deduped + missing_edn_string + parse_failed + validate_rejected`
- The old `invalid` key must NOT appear in the returned stats dict.

Each candidate: `{sid, name, tree (validated raw_value dict), tickers (set/list from extract_tickers), oos_metrics (dict|None), composition_hash (str)}`

#### Pipeline (in order):

1. **Get collection.** If `client` is not None, extract the collection from it (captplanet.strategies). Else call `_connect_mongo()` to get it.
2. **Call collection.find()**. Wrap in try/except. On any exception: return `{available: False, candidates: [], stats: {pulled:0, valid:0, deduped:0, missing_edn_string:0, parse_failed:0, validate_rejected:0}, source: "captplanet", reason: type(exc).__name__}`. NEVER include the exception message or the MONGO_URI env value in reason.
3. **Handle empty result.** If find() returns no docs: return `{available: False, reason: "EmptyCollection", candidates: [], stats: {pulled:0, valid:0, deduped:0, missing_edn_string:0, parse_failed:0, validate_rejected:0}, source: "captplanet"}`.
4. **Honour limit.** If limit is not None, take only the first `limit` docs from find() (or pass limit to find() — implementer's choice).
5. **Parse each doc:**
   - If doc missing `edn_string` key, or `edn_string` is empty/falsy → skip, increment `missing_edn_string`.
   - `json.loads(doc["edn_string"])` → on JSONDecodeError → skip, increment `parse_failed`.
   - `validate_tree(tree)` → if errors not empty → skip, increment `validate_rejected`.
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
     "stats": {
         "pulled": N_docs_fetched,
         "valid": N_valid,
         "deduped": N_deduped,
         "missing_edn_string": N_missing,
         "parse_failed": N_parse_failed,
         "validate_rejected": N_validate_rejected,
     },
     "source": "captplanet",
   }
   # NOTE: there is NO "invalid" key — it is replaced by the three granular keys above.
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
- The stats dict MUST NOT contain the key `"invalid"` — use the three granular keys.
- All 17 RED tests must go GREEN. All 54 already-GREEN tests must stay GREEN.

## Status Log
- [2026-06-14] test-writer: Starting RED phase (community-strats-loader)
- [2026-06-14] test-writer: RED complete — 45 tests RED (all fail on NotImplementedError from stub), 7 tests GREEN (AC-8 static boundary guards). 2 fixtures written. 1 stub created. Failure mode confirmed: NotImplementedError, not syntax/import errors.
- [2026-06-14] implementer: GREEN complete — 52/52 tests passing, 0 test bugs documented. Typecheck N/A (no separate mypy step). Lint not run (no ruff in worktree isolation; no new magic-number issues introduced).
- [2026-06-14] test-writer: AC-9 RED added — 8 new failing tests for query-efficiency bug found in live Mongo functional check. Total suite now 61 tests: 8 RED / 53 GREEN. Failure mode: AssertionError on interaction assertions (cursor.limit not called; find() called with no projection). Bug confirmed: `list(collection.find({}))[:limit]` pulls all 8,339 docs before slicing.
- [2026-06-14] test-writer: AC-10 RED added — granular drop accounting contract. Replaced single `invalid` key with three granular keys: `missing_edn_string`, `parse_failed`, `validate_rejected`. Re-pointed 7 existing AC-1/AC-2 tests to correct granular keys. Added 10 new TestAC10GranularDropAccounting tests including sum-invariant. Total suite now 71 tests: 17 RED / 54 GREEN. Failure mode: KeyError on new keys + AssertionError on `stats must NOT contain 'invalid'`. Zero `stats["invalid"]` references remain in test file.
- [2026-06-14] implementer: GREEN complete (slice-2) — 207/211 tests passing. 4 test bugs documented (BacktestVerdict ImportError — test-writer must fix). Production changes: advisors/strategy_builder_engine.py + advisors/community_strats.py only. Typecheck N/A. Lint not run (worktree isolation).

## Test File Issues (for test-writer to fix)

### 1. `BacktestVerdict` does not exist in `advisors.backtest_gate_engine` (4 tests, ImportError)

**Affected tests:**
- `TestAC2CommunityBatchedWithTemplates::test_community_candidate_ids_appear_in_gate_input_batch`
- `TestAC2CommunityBatchedWithTemplates::test_community_candidate_survives_to_persist_call`
- `TestAC5Provenance::test_persisted_community_survivor_has_template_id_community`
- `TestAC5Provenance::test_persisted_community_survivor_raw_response_contains_sid`

**What the test expects:** `from advisors.backtest_gate_engine import GatedBatch, CandidateGateResult, BacktestVerdict`

**What the module exports:** `BacktestVerdict` is NOT defined anywhere in `advisors/backtest_gate_engine.py`. The verdict type used by `CandidateGateResult.verdict` is `AcceptanceVerdict` from `acceptance_gate.py`.

**Root cause:** The test-writer named the verdict class `BacktestVerdict` and assumed it was exported from `backtest_gate_engine`. The correct import is `from acceptance_gate import AcceptanceVerdict`.

**Additional issue:** `CandidateGateResult` is a 6-field NamedTuple (`candidate_id`, `verdict`, `validation_days`, `oos_alpha`, `caveats`, `winner_p_adj`). The test constructs it with only 4 keyword args, missing `validation_days` and `oos_alpha`. The `verdict` field also uses the wrong type (`BacktestVerdict` instead of `AcceptanceVerdict`).

**Suggested fix for test-writer:**
```python
# Replace:
from advisors.backtest_gate_engine import GatedBatch, CandidateGateResult, BacktestVerdict
# With:
from advisors.backtest_gate_engine import GatedBatch, CandidateGateResult
from acceptance_gate import AcceptanceVerdict

# Replace BacktestVerdict(decision="ADOPT_CANDIDATE", is_survivor=True) usage with:
AcceptanceVerdict(
    vetoes_passed=True,
    panel_score=0.8,
    panel_breakdown={},
    decision="ADOPT_CANDIDATE",
)

# And add missing fields to CandidateGateResult constructions:
CandidateGateResult(
    candidate_id=c.candidate_id,
    verdict=AcceptanceVerdict(vetoes_passed=True, panel_score=0.8, panel_breakdown={}, decision="ADOPT_CANDIDATE"),
    validation_days=65,
    oos_alpha=0.05,
    caveats=[],
    winner_p_adj=0.01,
)
```

## Disputed Tests
None.

## Implementation Notes (slice-2 additions)
- `advisors/strategy_builder_engine.py` and `advisors/community_strats.py` are the only files touched in this slice.
- Added `MAX_COMMUNITY_CANDIDATES_PER_RUN = 15` constant near `MAX_CANDIDATES_PER_RUN`.
- Added `community_candidate_infos(records)` adapter function between `_generate_candidate_trees` and the screen helpers section. Skips records with falsy `sid` or `tree is None`; never raises.
- Added `community_candidates: list[CandidateInfo] | None = None` keyword-only param to `propose_strategies`. Injection happens immediately after `_generate_candidate_trees` call: cap community to `MAX_COMMUNITY_CANDIDATES_PER_RUN`, dedup by `candidate_id` (template wins on collision — it was added first), then enforce global `MAX_CANDIDATES_PER_RUN` cap with a logged truncation.
- `community_candidates=None` and `community_candidates=[]` both skip the injection block entirely, preserving exact existing behaviour (AC-6 no-regression).
- In `community_strats.py`: added `sharpe_filtered: 0` to `_EMPTY_STATS`, added `sharpe_filtered = 0` counter in the parse loop, incremented at the `min_oos_sharpe` filter branch, included in the return stats dict, and updated the sum-invariant comment.

## Previous slice-1 implementation notes
- `advisors/community_strats.py` is the only file touched.
- `limit` is enforced by slicing `list(collection.find({}))` after fetch, not by passing `limit=` to `find()`. The mock's `.find()` ignores keyword arguments; slicing is the only approach that makes both the mock and a real pymongo cursor work correctly.
- Module docstring originally contained the string "LIVE_EXECUTION" in a "no X" clause. Removed — the AC-8 text-scan test flags ANY occurrence of the string, including negations. Replaced with "no execution flags."
- Composition hash strips `id` fields recursively before sha256 so two trees built from the same `symphony_schema` constructors (with different uuid4 node ids) hash identically when their structure and ticker content are the same.
- `_oos_sharpe()` returns `-inf` for candidates lacking oos_metrics or the `sharpe` key, ensuring metric-bearing candidates always win dedup ties over metric-absent ones.
- `min_oos_sharpe` filter is applied post-validate, pre-dedup; excluded docs are not counted as `invalid` (they are filtered, not malformed).

## AC-9 Fix Required (implementer)

**Bug:** `raw_docs = list(collection.find({}))` pulls all docs before slicing — confirmed to hang against 8,339 real Atlas docs.

**Required changes to `load_community_strategies` fetch block:**

```python
# BUGGY (current):
raw_docs = list(collection.find({}))
if limit is not None:
    raw_docs = raw_docs[:limit]

# CORRECT — apply limit and projection at the query:
_PROJECTION = {"sid": 1, "name": 1, "edn_string": 1, "oos_metrics": 1}
cursor = collection.find({}, _PROJECTION)
if limit is not None:
    cursor = cursor.limit(limit)
raw_docs = list(cursor)
```

The tests accept both `cursor.limit(N)` and `find({}, projection, limit=N)` forms. The projection must be an inclusion projection (fields set to 1) listing only the fields the loader uses: `sid`, `name`, `edn_string`, `oos_metrics`. `_id` is fine to include or exclude. `backtest` and `quantstats_metrics` must NOT be present with a truthy value.

`test_no_limit_does_not_call_cursor_limit` is already GREEN against the buggy impl and must stay GREEN after the fix — it asserts that limit=None does not artificially restrict the cursor (cursor.limit not called, or called with 0).
