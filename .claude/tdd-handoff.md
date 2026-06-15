# TDD Handoff — propose-strategies-community-wiring
Plan: feature-plans/propose-strategies-community-wiring.md
Branch: team/propose-strategies-wiring
Phase: red

## Test Files
- tests/advisors/test_community_strats_wiring.py — 39 tests (38 failing RED, 1 GREEN invariant guard)

## Fixture Files
None — trees built inline via symphony_schema constructors; community_result dicts built by _make_community_result() helper in the test file.

## A/C Coverage Matrix
| A/C ID | Description | Test File | Test Name(s) | Status |
|--------|-------------|-----------|--------------|--------|
| AC-1 | adapter maps community docs → CandidateInfo; candidate_id=sid, template_id="community", tree carried, params has sid+name+composition_hash; metrics={}; backtest_error=None | test_community_strats_wiring.py | test_community_candidate_infos_exists_on_module, test_adapter_returns_list, test_adapter_candidate_id_is_sid, test_adapter_template_id_is_community, test_adapter_tree_carried_through, test_adapter_params_contains_sid, test_adapter_params_contains_name, test_adapter_params_contains_composition_hash, test_adapter_metrics_empty_dict_pre_backtest, test_adapter_backtest_error_is_none_pre_backtest, test_adapter_count_matches_candidates_list | RED |
| AC-1 (empty) | available=False → []; empty candidates list → [] | test_community_strats_wiring.py | test_adapter_available_false_returns_empty_list, test_adapter_empty_candidates_list_returns_empty_list | RED |
| AC-2 | evaluate_candidate_batch receives template + community candidates together (FULL batch); called exactly once | test_community_strats_wiring.py | test_gate_input_includes_community_candidate_ids, test_gate_input_includes_template_candidates_when_community_present, test_gate_is_called_exactly_once | RED |
| AC-3 | MAX_COMMUNITY_CANDIDATES_PER_RUN exists as named int constant; adapter caps at max_candidates; propose_strategies enforces the cap | test_community_strats_wiring.py | test_max_community_candidates_per_run_exists, test_max_community_candidates_per_run_is_positive_int, test_adapter_caps_at_max_candidates, test_adapter_cap_is_deterministic_takes_first, test_adapter_max_candidates_uses_module_constant_as_default, test_community_candidates_exceeding_cap_are_truncated_in_batch | RED |
| AC-4 | backtest exception → backtest_error set; failed candidate excluded from gate; other candidates unaffected; all-fail → run completes | test_community_strats_wiring.py | test_community_backtest_error_sets_backtest_error_field, test_community_backtest_error_candidate_excluded_from_gate, test_one_bad_community_candidate_does_not_block_template_candidates, test_all_community_backtest_failures_run_completes | RED |
| AC-5 | persisted observation for surviving community candidate has template_id="community" + sid in params + subject_id=sid | test_community_strats_wiring.py | test_persisted_observation_has_template_id_community, test_persisted_observation_raw_response_contains_sid, test_persisted_subject_id_is_sid | RED |
| AC-6 | community_candidates=None and =[] → no regression; gate batch size identical; community_candidates is keyword-only | test_community_strats_wiring.py | test_propose_strategies_accepts_community_candidates_none, test_propose_strategies_accepts_community_candidates_empty_list, test_gate_batch_size_same_for_none_and_empty_list, test_community_candidates_kwarg_is_keyword_only | RED |
| AC-7 | adapter never raises on malformed input; propose_strategies never raises on community failures; returns ProposalRun on catastrophic failure; no LIVE_EXECUTION import; advisory-only | test_community_strats_wiring.py | test_community_candidate_infos_never_raises_on_malformed_result, test_propose_strategies_never_raises_on_community_exception, test_propose_strategies_returns_proposal_run_on_community_failures, test_strategy_builder_engine_does_not_import_live_execution_at_module_level (GREEN invariant), test_community_candidate_infos_does_not_call_live_execution, test_propose_strategies_with_community_candidates_returns_proposal_run_type | RED (38 failing) + 1 GREEN invariant |

## Import Stubs Created
None needed — `advisors/strategy_builder_engine.py` already exists. The new symbols (`community_candidate_infos`, `MAX_COMMUNITY_CANDIDATES_PER_RUN`, `community_candidates` kwarg on `propose_strategies`) are simply absent, causing AttributeError / TypeError failures — the correct RED signal.

## Questions for User
None. The AC matrix is fully specified; the adapter contract is recovered from pre-rip c1bf5dc.

## RED Run Result
- 38 failed on missing symbols (AttributeError on `community_candidate_infos` / `MAX_COMMUNITY_CANDIDATES_PER_RUN`; TypeError on unknown `community_candidates` kwarg in `propose_strategies`)
- 1 passed: `test_strategy_builder_engine_does_not_import_live_execution_at_module_level` — architectural invariant guard; correctly GREEN before and after implementation (module must never export LIVE_EXECUTION)
- 0 skipped
- No syntax errors, no import errors, no test infrastructure failures

## Status Log
- [2026-06-14] test-writer: Starting RED phase for propose-strategies-community-wiring (AC-1..AC-7)
- [2026-06-14] test-writer: RED complete — 39 tests (38 failing on missing symbols, 1 GREEN architectural invariant guard). No stubs needed. Committed RED on team/propose-strategies-wiring.

## Implementation Notes for Implementer
### What to add to advisors/strategy_builder_engine.py

1. `MAX_COMMUNITY_CANDIDATES_PER_RUN: int = <N>` at module level (alongside `MAX_CANDIDATES_PER_RUN`). Choose a reasonable positive integer cap.

2. `community_candidate_infos(community_result, *, max_candidates) -> list[CandidateInfo]` function:
   - Returns `[]` when `community_result.get("available") is False` or `candidates` is empty/None
   - Maps each candidate dict `{sid, name, tree, tickers, oos_metrics, composition_hash}` to:
     `CandidateInfo(candidate_id=sid, tree=tree, template_id="community", params={"sid": sid, "name": name, "composition_hash": composition_hash}, metrics={}, backtest_error=None, data_warnings=[])`
   - Truncates to `max_candidates` (deterministic: first N items)
   - Never raises — wrap in try/except, return [] on any failure

3. `community_candidates: list[CandidateInfo] | None = None` as a keyword-only parameter on `propose_strategies` (after the `*` — it's already keyword-only for `incumbent_oos_alpha`/`default_oos_alpha`):
   - After `candidate_infos = _generate_candidate_trees(objective, universe)`, extend with the (capped) community candidates: `if community_candidates: candidate_infos.extend(community_candidates[:MAX_COMMUNITY_CANDIDATES_PER_RUN])`
   - The existing backtest loop, gate call, and persistence path are UNCHANGED — community candidates flow through the same code
   - Per-candidate try/except already exists in the backtest loop — community candidates get the same failure isolation automatically

### Key invariants the tests enforce (implementer must NOT break)
- `evaluate_candidate_batch` called exactly once per run
- Gate input = FULL combined batch (template + community); screens never applied to gate input
- `template_id="community"` must propagate from CandidateInfo into raw_response on persist
- `params["sid"]` must equal the community candidate's `candidate_id`
- `community_candidates=None` and `community_candidates=[]` must be identical in behavior
- `community_candidates` must be keyword-only (no positional use)
