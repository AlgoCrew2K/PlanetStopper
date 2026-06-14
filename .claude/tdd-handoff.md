# TDD Handoff — DE-SYMPH-001: Recursive nested condition-block validation
Plan: feature-plans/symphony-nested-validation.md
Branch: pr/symphony-nested-validation
Phase: red

## Test Files

| File | New Tests Added | Total in File |
|------|----------------|---------------|
| `tests/advisors/test_symphony_schema.py` | 24 (class TestNestedConditionValidation, section §12) | 234 |

| Fixture | Role |
|---------|------|
| `tests/fixtures/math/nested_condition_validation_basic.json` | Golden fixture with 5 hand-built malformed nested condition dicts + the v2 §7.3 valid ANY example (VERIFIED-CORPUS); used by AC-1/AC-2/AC-3/AC-4/AC-5 tests |

## Behavioral Test Plan

N/A — no UI surface. `_validate_condition_block` and `validate_tree` are pure-stdlib,
offline, read-only functions. No Flask route, no browser interaction, no e2e spec required.

## A/C Coverage Matrix

| A/C ID | Description | Test Name(s) | RED/GREEN |
|--------|-------------|--------------|-----------|
| AC-1 | Nested sub-block with unknown condition-type → HARD error naming it | `test_nested_sub_block_unknown_condition_type_produces_hard_error` | RED |
| AC-1 (precision) | Error message must name the bad token (not a different bad ct) | `test_nested_sub_block_unknown_condition_type_names_it_in_error_message` | RED |
| AC-2 | Nested binary-compound with operator='xor' → HARD error | `test_nested_compound_with_bad_operator_produces_hard_error` | RED |
| AC-2 (precision) | Error names the bad operator value 'or' | `test_nested_binary_compound_with_bad_operator_names_it_in_error` | RED |
| AC-2 (edge) | Nested compound with operator=None (key absent) → HARD error | `test_nested_compound_with_none_operator_produces_hard_error` | RED |
| AC-3a | Nested compound missing 'conditions' key → HARD error | `test_nested_compound_missing_conditions_key_produces_hard_error` | RED |
| AC-3b | Nested binary-compound missing 'tickers' key → HARD error | `test_nested_binary_compound_missing_tickers_produces_hard_error` | RED |
| AC-3 (combined) | Both malformed blocks in same compound → ≥2 errors (no early exit) | `test_nested_compound_missing_conditions_and_binary_compound_missing_tickers_both_error` | RED |
| AC-4 | Malformed at depth-2 (compound→compound→bad binary-compound) → caught | `test_malformed_block_two_levels_deep_is_caught` | RED |
| AC-4 (extra depth) | Malformed at depth-3 (compound×3→binary-compound(xnor)) → caught | `test_malformed_block_three_levels_deep_is_caught` | RED |
| AC-4 (precision) | Depth-2 error names the bad token 'nor' | `test_malformed_unknown_type_at_depth_2_names_it_in_error` | RED |
| AC-5 | Valid §7.3 corpus ANY example (fixture) → no condition-block errors | `test_valid_any_gate_from_v2_s7_3_corpus_example_passes_clean` | GREEN |
| AC-5 | Valid constructor-built compound→compound→binary-compound → no errors | `test_valid_any_gate_wrapped_in_another_compound_passes_clean` | GREEN |
| AC-5 | Valid §7.4 corpus ALL-gate → no condition errors | `test_valid_all_gate_from_v2_s7_4_passes_clean` | GREEN |
| AC-6 | 5000-deep compound → never raises (no recursion today, stays no-raise after cap) | `test_pathologically_deep_nested_compound_does_not_raise` | GREEN* |
| AC-6 (malformed) | 5000-deep + xor leaf → never raises, returns a list | `test_pathologically_deep_compound_with_malformed_leaf_either_catches_or_caps` | GREEN* |
| AC-6 (valid+deep) | 200-deep valid compound → no false positive, no raise | `test_deeply_nested_valid_compound_does_not_raise_or_falsely_error` | GREEN |
| AC-7 regression | Top-level unknown condition-type still errors | `test_top_level_unknown_condition_type_still_errors_after_recursion_added` | GREEN |
| AC-7 regression | Top-level bad operator still errors | `test_top_level_compound_bad_operator_still_errors` | GREEN |
| AC-7 regression | Top-level compound missing 'conditions' still errors | `test_top_level_compound_missing_conditions_still_errors` | GREEN |
| AC-7 regression | Top-level binary-compound missing 'tickers' still errors | `test_top_level_binary_compound_missing_tickers_still_errors` | GREEN |
| AC-7 regression | Valid flat if-child still validates clean | `test_valid_flat_if_child_still_validates_clean` | GREEN |
| AC-7 regression | Constructor-built make_if_compound tree still validates clean | `test_make_if_compound_constructor_tree_still_validates_clean` | GREEN |
| AC-7 / AC-6 | Never raises on junk conditions[] (string, None, non-dict items) | `test_validate_tree_never_raises_on_malformed_nested_condition_inputs` | GREEN |

**\* AC-6 GREEN note:** The two 5000-deep tests are GREEN today because `validate_tree` does NO recursion into condition blocks at all — so it trivially doesn't raise. They will turn RED if the implementer adds unbounded recursive traversal without a depth cap. They will return to GREEN once the implementer adds the iterative explicit-stack or depth-cap implementation. This is the correct adversarial design: the test suite enforces the never-raises contract in both the no-recursion state (currently) and the properly-bounded-recursion state (after GREEN).

## Why Each RED Test Is Red

Every RED test fails at `assert len(errors) >= 1` where `validate_tree` currently returns `[]`.
Root cause: `_validate_condition_block` (line ~326 of `advisors/symphony_schema.py`) checks the
top-level condition block fields, then returns `errs` without ever looking at
`condition.get("conditions")`. The malformed nested blocks at any depth are invisible.

Failure mode confirmed for all 11 RED tests:
```
AssertionError: AC-N: ... Got none — recursion into conditions[] is absent.
assert 0 >= 1
 +  where 0 = len([])
```

## What The Implementer Must Do (deliberately terse — see plan for full A/C)

**Target:** `advisors/symphony_schema._validate_condition_block` (~line 326)

1. After the existing top-level checks for `ct == "compound"`, when `conditions` is a list,
   iterate each sub-dict and validate it recursively (or via an explicit stack).

2. Add a depth bound. Options (implementer's choice):
   - Internal `_depth` parameter defaulting to 0; increment on each recursive call; stop and
     emit a "exceeds max condition depth (MAX_CONDITION_DEPTH)" hard error when exceeded.
   - Iterative explicit stack with a depth counter alongside each node, matching the existing
     `validate_tree` / `lint_tree` / `extract_tickers` pattern.
   - A `MAX_CONDITION_DEPTH` module-level constant (e.g. 50–200) with a comment citing the plan.

3. Non-dict items in `conditions[]` must be skipped gracefully — no raise (plan edge case:
   "conditions present but not a list, or contains non-dict items → skip those gracefully").

4. Update the `_validate_condition_block` docstring: remove "Checks the top-level condition
   block only (not sub-conditions)" and document the new recursive behavior + depth cap.

5. No constructor changes. No `extract_tickers` / `render_rules_text` changes. Scope boundary
   is purely within `_validate_condition_block`.

## Import Stubs Created

None. `advisors/symphony_schema.py` already exists with the full constructor set, the
`_validate_condition_block` function, and `validate_tree`. No new production modules required.

## Questions for User

None. All edge cases and behavior are fully specified in the plan.

## Status Log
- [2026-06-14] test-writer: Starting RED phase for DE-SYMPH-001
- [2026-06-14] test-writer: RED complete — 24 tests (11 failing RED on assertion, 13 passing GREEN). Full suite result: 11 failed / 223 passed / 0 errors on branch pr/symphony-nested-validation. 1 golden fixture created at tests/fixtures/math/nested_condition_validation_basic.json.
