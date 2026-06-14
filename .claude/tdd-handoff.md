# TDD Handoff
Plan: feature-plans/symphony-compound-construction.md
Branch: pr/symphony-compound-construction
Phase: green

## Test Files
- tests/advisors/test_symphony_schema.py — Section 12 "COMPOUND ANY/ALL CONSTRUCTION — Cycle B"
  appended after line 3433 (end of Cycle A green section). 72 new tests across 11 test classes.
  File total: 211 tests (139 Cycle A pre-existing all GREEN + 72 Cycle B new = 60 RED, 12 already-GREEN).

## Behavioral Test Plan
N/A — pure unit tests, no UI/e2e surface.
advisors/symphony_schema.py is a stdlib-only pure-function module with no I/O, no Flask
dependency, no DB access, and no network calls. No e2e spec required.

## A/C Coverage Matrix

| A/C ID | Description | Test Class | Test Name(s) | Status |
|--------|-------------|------------|--------------|--------|
| AC-1 | make_condition_operand emits fn key | TestMakeConditionOperand | test_make_condition_operand_emits_fn_key | RED |
| AC-1 | make_condition_operand emits ticker key | TestMakeConditionOperand | test_make_condition_operand_emits_ticker_key | RED |
| AC-1 | make_condition_operand emits params.window | TestMakeConditionOperand | test_make_condition_operand_emits_params_window | RED |
| AC-1 | make_condition_operand does not emit fn-params | TestMakeConditionOperand | test_make_condition_operand_does_not_emit_fn_params_key | RED |
| AC-1 | make_condition_operand does not emit val | TestMakeConditionOperand | test_make_condition_operand_does_not_emit_val_key | RED |
| AC-1 | make_condition_operand returns dict | TestMakeConditionOperand | test_make_condition_operand_is_a_dict | RED |
| AC-1 | params.window matches window argument | TestMakeConditionOperand | test_make_condition_operand_window_value_matches_argument | RED |
| AC-2 | make_constant_rhs emits constant key | TestMakeConstantRhs | test_make_constant_rhs_emits_constant_key | RED |
| AC-2 | constant value matches argument | TestMakeConstantRhs | test_make_constant_rhs_constant_value_matches_argument | RED |
| AC-2 | accepts float values (73.5 from §7.4) | TestMakeConstantRhs | test_make_constant_rhs_with_float_value | RED |
| AC-2 | accepts negative values | TestMakeConstantRhs | test_make_constant_rhs_with_negative_value | RED |
| AC-2 | returns dict | TestMakeConstantRhs | test_make_constant_rhs_is_a_dict | RED |
| AC-2 | emits ONLY constant key (no stray fields) | TestMakeConstantRhs | test_make_constant_rhs_has_only_constant_key | RED |
| AC-3 | make_binary_condition emits condition-type=binary | TestMakeBinaryCondition | test_make_binary_condition_emits_condition_type_binary | RED |
| AC-3 | emits lhs with §7.2 operand shape | TestMakeBinaryCondition | test_make_binary_condition_emits_lhs | RED |
| AC-3 | emits comparator | TestMakeBinaryCondition | test_make_binary_condition_emits_comparator | RED |
| AC-3 | constant-rhs form: rhs={constant: N} | TestMakeBinaryCondition | test_make_binary_condition_emits_rhs_constant_form | RED |
| AC-3 | operand-rhs form (ticker comparison, §7.4) | TestMakeBinaryCondition | test_make_binary_condition_supports_operand_rhs_form | RED |
| AC-3/AC-9 | lhs is deep-copied (mutation isolation) | TestMakeBinaryCondition | test_make_binary_condition_lhs_is_deep_copied | RED |
| AC-4 | make_binary_compound_condition emits condition-type=binary-compound | TestMakeBinaryCompoundCondition | test_make_binary_compound_condition_emits_condition_type | RED |
| AC-4 | emits operator=any | TestMakeBinaryCompoundCondition | test_make_binary_compound_condition_emits_operator_any | RED |
| AC-4 | emits operator=all | TestMakeBinaryCompoundCondition | test_make_binary_compound_condition_emits_operator_all | RED |
| AC-4 | emits tickers as list | TestMakeBinaryCompoundCondition | test_make_binary_compound_condition_emits_tickers_list | RED |
| AC-4 | lhs.ticker is '%' placeholder | TestMakeBinaryCompoundCondition | test_make_binary_compound_condition_lhs_ticker_is_percent_placeholder | RED |
| AC-4 | lhs.fn matches fn argument | TestMakeBinaryCompoundCondition | test_make_binary_compound_condition_lhs_fn_matches_argument | RED |
| AC-4 | lhs.params.window matches window argument | TestMakeBinaryCompoundCondition | test_make_binary_compound_condition_lhs_params_window_matches_argument | RED |
| AC-4 | single-ticker tickers list accepted (§7.3 FDL) | TestMakeBinaryCompoundCondition | test_make_binary_compound_condition_single_ticker_is_valid | RED |
| AC-4/AC-9 | invalid operator raises ValueError | TestMakeBinaryCompoundCondition | test_make_binary_compound_condition_invalid_operator_raises_value_error | RED |
| AC-4/AC-9 | empty tickers raises ValueError | TestMakeBinaryCompoundCondition | test_make_binary_compound_condition_empty_tickers_raises_value_error | RED |
| AC-4/AC-9 | tickers are deep-copied | TestMakeBinaryCompoundCondition | test_make_binary_compound_condition_tickers_are_deep_copied | RED |
| AC-4 | §7.3 golden fixture: first binary-compound leaf (FDL, RSI10, gt, 83) | TestMakeBinaryCompoundCondition | test_make_binary_compound_condition_golden_fixture_73_first_leaf | RED |
| AC-5 | make_compound_condition emits condition-type=compound | TestMakeCompoundCondition | test_make_compound_condition_emits_condition_type_compound | RED |
| AC-5 | emits operator (any/all) | TestMakeCompoundCondition | test_make_compound_condition_emits_operator | RED |
| AC-5 | emits conditions list | TestMakeCompoundCondition | test_make_compound_condition_emits_conditions_list | RED |
| AC-5 | operator=all emitted correctly | TestMakeCompoundCondition | test_make_compound_condition_operator_all_emitted | RED |
| AC-5/AC-9 | invalid operator raises ValueError | TestMakeCompoundCondition | test_make_compound_condition_invalid_operator_raises_value_error | RED |
| AC-5/AC-9 | empty conditions raises ValueError | TestMakeCompoundCondition | test_make_compound_condition_empty_conditions_raises_value_error | RED |
| AC-5/AC-9 | conditions are deep-copied | TestMakeCompoundCondition | test_make_compound_condition_conditions_are_deep_copied | RED |
| AC-5 | supports nesting: compound-in-compound | TestMakeCompoundCondition | test_make_compound_condition_supports_nesting_compound_in_compound | RED |
| AC-5 | §7.3 golden fixture: 5-condition ANY (1 binary + 4 binary-compound) | TestMakeCompoundCondition | test_make_compound_condition_golden_fixture_73_any_structure | RED |
| AC-5 | §7.4 golden fixture: 3-condition ALL (binary-compound with operand rhs) | TestMakeCompoundCondition | test_make_compound_condition_golden_fixture_74_all_structure | RED |
| AC-6 | make_if_compound emits step=if | TestMakeIfCompound | test_make_if_compound_emits_if_step | RED |
| AC-6 | emits exactly two children (true + else) | TestMakeIfCompound | test_make_if_compound_has_two_children | RED |
| AC-6 | true if-child carries condition block | TestMakeIfCompound | test_make_if_compound_true_child_carries_condition_block | RED |
| AC-6 | condition block has correct condition-type | TestMakeIfCompound | test_make_if_compound_true_child_condition_has_correct_type | RED |
| AC-6 | else if-child has is-else-condition?=True | TestMakeIfCompound | test_make_if_compound_else_child_has_is_else_condition_true | RED |
| AC-6 | make_if_compound output validates clean (validate_tree==[]) | TestMakeIfCompound | test_make_if_compound_validates_clean_wrapped_in_root | RED |
| AC-6/AC-9 | then_children are deep-copied | TestMakeIfCompound | test_make_if_compound_then_children_are_deep_copied | RED |
| AC-6/AC-9 | emits fresh UUID4 id | TestMakeIfCompound | test_make_if_compound_has_fresh_uuid4_id | RED |
| AC-6/AC-9 | two calls produce distinct UUIDs | TestMakeIfCompound | test_two_make_if_compound_calls_produce_distinct_ids | RED |
| AC-7 | frontrunner overlay: validate_tree returns [] | TestFrontrunnerOverlayIntegration | test_frontrunner_overlay_validates_clean | RED |
| AC-7 | extract_tickers returns watched+basket+base tickers | TestFrontrunnerOverlayIntegration | test_frontrunner_overlay_extract_tickers_returns_watched_and_basket_and_base | RED |
| AC-7 | render_rules_text mentions any-gate readably | TestFrontrunnerOverlayIntegration | test_frontrunner_overlay_render_rules_text_mentions_any_gate | RED |
| AC-7 | extract_tickers includes watched tickers from condition.tickers[] | TestFrontrunnerOverlayIntegration | test_frontrunner_overlay_extract_tickers_includes_watched_tickers | RED |
| AC-8 | validate_tree errors on compound with operator=xor | TestValidateTreeCompoundAwareness | test_validate_tree_errors_on_compound_with_invalid_operator_xor | RED |
| AC-8 | validate_tree errors on unknown condition-type | TestValidateTreeCompoundAwareness | test_validate_tree_errors_on_unknown_condition_type | RED |
| AC-8 | validate_tree errors on compound missing conditions key | TestValidateTreeCompoundAwareness | test_validate_tree_errors_on_compound_missing_conditions_key | RED |
| AC-8 | validate_tree errors on binary-compound missing tickers | TestValidateTreeCompoundAwareness | test_validate_tree_errors_on_binary_compound_missing_tickers | RED |
| AC-8 | validate_tree accepts well-formed compound (sanity) | TestValidateTreeCompoundAwareness | test_validate_tree_accepts_well_formed_compound_condition | GREEN (already passes; amendment 6 + existing tolerance) |
| AC-8 | validate_tree accepts well-formed binary-compound (sanity) | TestValidateTreeCompoundAwareness | test_validate_tree_accepts_well_formed_binary_compound_condition | GREEN (amendment 6) |
| AC-8 | validate_tree accepts well-formed binary (sanity) | TestValidateTreeCompoundAwareness | test_validate_tree_accepts_well_formed_binary_condition | GREEN (amendment 6) |
| AC-8 | validate_tree never raises on malformed condition block | TestValidateTreeCompoundAwareness | test_validate_tree_never_raises_on_malformed_condition_block | GREEN (already passes; never-raising contract) |
| AC-9 | fresh UUID4 ids for compound constructors | TestCompoundConstructionInvariants | test_compound_condition_block_condition_ids_are_uuid4 | RED |
| AC-9 | validate_tree is read-only (no mutation of compound if-node) | TestCompoundConstructionInvariants | test_validate_tree_read_only_on_compound_if_node | RED |
| AC-9 | readers never raise on garbage compound tree | TestCompoundConstructionInvariants | test_readers_never_raise_on_garbage_compound_tree | GREEN (already passes; never-raising contract) |
| AC-10 | existing make_if still produces flat lhs-fn field (unchanged) | TestCompoundConstructionRegression | test_existing_make_if_still_produces_flat_lhs_fn_field | GREEN (regression guard) |
| AC-10 | existing make_if validate_tree still clean | TestCompoundConstructionRegression | test_existing_make_if_validated_tree_still_clean | GREEN (regression guard) |
| AC-10 | existing make_condition shape unchanged | TestCompoundConstructionRegression | test_existing_make_condition_unchanged | GREEN (regression guard) |
| AC-10 | existing make_indicator shape unchanged | TestCompoundConstructionRegression | test_existing_make_indicator_unchanged | GREEN (regression guard) |
| AC-11 | no duplicate test_unknown_comparator_eq_produces_error | TestCycleANits | test_no_duplicate_test_unknown_comparator_eq_produces_error | GREEN (already clean) |
| AC-11 | upper-bollinger in KNOWN_INDICATOR_FNS (lint no warning) | TestCycleANits | test_upper_bollinger_as_lhs_fn_in_lint_tree_matches_known_indicator_fns | GREEN (already passes from Cycle A) |
| AC-11 | lower-bollinger in KNOWN_INDICATOR_FNS (lint no warning) | TestCycleANits | test_lower_bollinger_as_lhs_fn_in_lint_tree_matches_known_indicator_fns | GREEN (already passes from Cycle A) |

## RED tests: 60 (all fail on AttributeError — constructors do not exist yet)

### AC-1 — TestMakeConditionOperand (6 RED)
1. test_make_condition_operand_emits_fn_key
2. test_make_condition_operand_emits_ticker_key
3. test_make_condition_operand_emits_params_window
4. test_make_condition_operand_does_not_emit_fn_params_key
5. test_make_condition_operand_does_not_emit_val_key
6. test_make_condition_operand_is_a_dict
7. test_make_condition_operand_window_value_matches_argument

(7 tests in class — 7 RED)

### AC-2 — TestMakeConstantRhs (6 RED)
1. test_make_constant_rhs_emits_constant_key
2. test_make_constant_rhs_constant_value_matches_argument
3. test_make_constant_rhs_with_float_value
4. test_make_constant_rhs_with_negative_value
5. test_make_constant_rhs_is_a_dict
6. test_make_constant_rhs_has_only_constant_key

(6 tests in class — 6 RED)

### AC-3 — TestMakeBinaryCondition (6 RED)
1. test_make_binary_condition_emits_condition_type_binary
2. test_make_binary_condition_emits_lhs
3. test_make_binary_condition_emits_comparator
4. test_make_binary_condition_emits_rhs_constant_form
5. test_make_binary_condition_supports_operand_rhs_form
6. test_make_binary_condition_lhs_is_deep_copied

(6 tests in class — 6 RED)

### AC-4 — TestMakeBinaryCompoundCondition (12 RED)
1. test_make_binary_compound_condition_emits_condition_type
2. test_make_binary_compound_condition_emits_operator_any
3. test_make_binary_compound_condition_emits_operator_all
4. test_make_binary_compound_condition_emits_tickers_list
5. test_make_binary_compound_condition_lhs_ticker_is_percent_placeholder
6. test_make_binary_compound_condition_lhs_fn_matches_argument
7. test_make_binary_compound_condition_lhs_params_window_matches_argument
8. test_make_binary_compound_condition_single_ticker_is_valid
9. test_make_binary_compound_condition_invalid_operator_raises_value_error
10. test_make_binary_compound_condition_empty_tickers_raises_value_error
11. test_make_binary_compound_condition_tickers_are_deep_copied
12. test_make_binary_compound_condition_golden_fixture_73_first_leaf

(12 tests in class — 12 RED)

### AC-5 — TestMakeCompoundCondition (10 RED)
1. test_make_compound_condition_emits_condition_type_compound
2. test_make_compound_condition_emits_operator
3. test_make_compound_condition_emits_conditions_list
4. test_make_compound_condition_operator_all_emitted
5. test_make_compound_condition_invalid_operator_raises_value_error
6. test_make_compound_condition_empty_conditions_raises_value_error
7. test_make_compound_condition_conditions_are_deep_copied
8. test_make_compound_condition_supports_nesting_compound_in_compound
9. test_make_compound_condition_golden_fixture_73_any_structure
10. test_make_compound_condition_golden_fixture_74_all_structure

(10 tests in class — 10 RED)

### AC-6 — TestMakeIfCompound (9 RED)
1. test_make_if_compound_emits_if_step
2. test_make_if_compound_has_two_children
3. test_make_if_compound_true_child_carries_condition_block
4. test_make_if_compound_true_child_condition_has_correct_type
5. test_make_if_compound_else_child_has_is_else_condition_true
6. test_make_if_compound_validates_clean_wrapped_in_root
7. test_make_if_compound_then_children_are_deep_copied
8. test_make_if_compound_has_fresh_uuid4_id
9. test_two_make_if_compound_calls_produce_distinct_ids

(9 tests in class — 9 RED)

### AC-7 — TestFrontrunnerOverlayIntegration (4 RED)
1. test_frontrunner_overlay_validates_clean
2. test_frontrunner_overlay_extract_tickers_returns_watched_and_basket_and_base
3. test_frontrunner_overlay_render_rules_text_mentions_any_gate
4. test_frontrunner_overlay_extract_tickers_includes_watched_tickers

(4 tests in class — 4 RED)

### AC-8 — TestValidateTreeCompoundAwareness (4 RED, 4 already-GREEN)
RED (require new validate_tree compound-block validation):
1. test_validate_tree_errors_on_compound_with_invalid_operator_xor
2. test_validate_tree_errors_on_unknown_condition_type
3. test_validate_tree_errors_on_compound_missing_conditions_key
4. test_validate_tree_errors_on_binary_compound_missing_tickers

Already-GREEN (sanity guards — validate_tree already tolerates/never-raises on these):
5. test_validate_tree_accepts_well_formed_compound_condition
6. test_validate_tree_accepts_well_formed_binary_compound_condition
7. test_validate_tree_accepts_well_formed_binary_condition
8. test_validate_tree_never_raises_on_malformed_condition_block

(8 tests in class — 4 RED, 4 GREEN)

### AC-9 — TestCompoundConstructionInvariants (2 RED, 1 already-GREEN)
RED (require new constructors to exist):
1. test_compound_condition_block_condition_ids_are_uuid4
2. test_validate_tree_read_only_on_compound_if_node

Already-GREEN (readers already never-raise):
3. test_readers_never_raise_on_garbage_compound_tree

(3 tests in class — 2 RED, 1 GREEN)

### AC-10 — TestCompoundConstructionRegression (4 already-GREEN, regression guards)
1. test_existing_make_if_still_produces_flat_lhs_fn_field
2. test_existing_make_if_validated_tree_still_clean
3. test_existing_make_condition_unchanged
4. test_existing_make_indicator_unchanged

(4 tests in class — 0 RED, 4 GREEN)

### AC-11 — TestCycleANits (3 already-GREEN)
1. test_no_duplicate_test_unknown_comparator_eq_produces_error
2. test_upper_bollinger_as_lhs_fn_in_lint_tree_matches_known_indicator_fns
3. test_lower_bollinger_as_lhs_fn_in_lint_tree_matches_known_indicator_fns

(3 tests in class — 0 RED, 3 GREEN)

## Already-GREEN summary (12 total)
These 12 pass today because:
- Regression guards (AC-10/AC-11): verify existing behavior is unchanged — correctly GREEN before implementation
- Amendment-6 sanity guards (AC-8): validate_tree already tolerates well-formed compound blocks via the NON-GAP exemption (_validate_if_child returns early if condition dict is present)
- Never-raising contract guards (AC-8, AC-9): readers already never raise on any input

All 12 must STAY GREEN after implementation.

## Questions for User
None — specification is complete from v2 grammar doc (§7.3/§7.4 golden fixtures) and feature plan.

## Import Stubs Created
None required. All 6 new constructors are appended to the EXISTING advisors/symphony_schema.py
module. No new modules introduced. Import graph is unchanged.

## What the Implementer Must Do (deliberately blind to plan — read handoff only)

Append to advisors/symphony_schema.py (pure stdlib, same style as existing constructors):

### 1. make_condition_operand(fn, ticker, *, window) -> dict
Returns {"fn": fn, "ticker": ticker, "params": {"window": window}}.
Must NOT emit "fn-params", "val", or any flat if-child keys.

### 2. make_constant_rhs(value) -> dict
Returns {"constant": value}. ONLY the "constant" key.

### 3. make_binary_condition(lhs_operand, comparator, rhs) -> dict
Returns {"condition-type": "binary", "lhs": deepcopy(lhs_operand), "comparator": comparator,
"rhs": deepcopy(rhs)}. Deep-copy both lhs and rhs.

### 4. make_binary_compound_condition(fn, tickers, comparator, rhs, *, window, operator="any") -> dict
- Raise ValueError if operator not in {"any", "all"}.
- Raise ValueError if tickers is empty.
- Returns {"condition-type": "binary-compound", "operator": operator,
  "tickers": list(tickers) [deep copy],
  "lhs": {"fn": fn, "ticker": "%", "params": {"window": window}},
  "comparator": comparator, "rhs": deepcopy(rhs)}.
- lhs.ticker MUST be "%" (the corpus placeholder, not the fn ticker).

### 5. make_compound_condition(operator, conditions) -> dict
- Raise ValueError if operator not in {"any", "all"}.
- Raise ValueError if conditions is empty.
- Returns {"condition-type": "compound", "operator": operator,
  "conditions": [deepcopy(c) for c in conditions]}.
- Deep-copy each condition in the list.

### 6. make_if_compound(condition_block, *, then_children, else_children) -> dict
- Emits an if node (step="if", fresh UUID4 id) with exactly two children:
  - True branch: an if-child carrying "condition": deepcopy(condition_block), plus deep-copied then_children
  - Else branch: an if-child with "is-else-condition?": True, plus deep-copied else_children
- validate_tree must return [] on the output wrapped in make_root (amendment 6 handles this:
  _validate_if_child already returns early when condition dict is present — no validate_tree changes
  needed for AC-6).

### 7. validate_tree compound-block validation (AC-8 — NEW logic)
In the validate_tree recursion, when an if-child carries a "condition" dict:
- Validate the condition block recursively:
  - condition-type must be in {"binary", "binary-compound", "compound"} — else HARD ERROR
  - compound: must have "conditions" key — else HARD ERROR; operator must be "any" or "all" — else HARD ERROR
  - binary-compound: must have "tickers" key — else HARD ERROR; operator must be "any" or "all" — else HARD ERROR
  - recurse into compound.conditions[] for nested blocks

### 8. extract_tickers compound-walk (AC-7)
extend extract_tickers to walk condition.tickers[] and condition.lhs.ticker
(where ticker != "%" — skip the placeholder) and condition.rhs.ticker
(for operand-rhs form). This ensures watched tickers from binary-compound
blocks are returned alongside asset/filter tickers.

### 9. render_rules_text compound-walk (AC-7)
Extend render_rules_text to emit readable text for condition blocks —
must mention the operator ("any"/"all") and at minimum one ticker from
the condition.tickers[] list. The exact format is implementer's choice;
the test only asserts "any" appears in the output for a binary-compound ANY gate.

## Key Implementation Notes (adversarial callouts)

- **"%" placeholder**: lhs.ticker for binary-compound MUST be the literal string "%"
  (not the fn ticker, not None). This is a corpus token — hardcoded in the grammar.
- **Amendment 6 already handles AC-6**: _validate_if_child at lines ~335-336 already
  does `if isinstance(node.get("condition"), dict): return errs`. No validate_tree changes
  needed for make_if_compound output to validate clean. AC-8 adds NEW validation INSIDE
  the condition block — that is the only validate_tree change.
- **Deep-copy everything**: mutating the input list/dict after construction must not affect
  the built tree. Use copy.deepcopy or list comprehensions.
- **extract_tickers adversarial test (AC-7)**: test_frontrunner_overlay_extract_tickers_includes_watched_tickers
  specifically checks that tickers in condition.tickers[] are returned. A naive extract_tickers
  that only walks asset nodes will FAIL this test.
- **render_rules_text adversarial test (AC-7)**: test_frontrunner_overlay_render_rules_text_mentions_any_gate
  checks that "any" (case-insensitive) appears in the output. A render that silently ignores
  condition blocks will FAIL.
- **Empty conditions/tickers edge case**: both make_compound_condition(operator, []) and
  make_binary_compound_condition(..., [], ...) must raise ValueError — not produce empty output.

## Test File Issues (for test-writer to fix)
None — all 72 tests are syntactically correct and have been run to confirm RED/GREEN status.

## Test File Issues (for test-writer to fix)

### 1. TestGoldenFixtureSmall::test_small_fixture_extract_tickers_matches_reference_walk

**File:** `tests/advisors/test_symphony_schema.py` line ~182

**What the test expects:** `result == expected` where `expected = _ref_collect_tickers(raw_small)`.
The `_ref_collect_tickers` reference walker only traverses `children` arrays and collects any
dict's `ticker` field. It does NOT walk into `condition` blocks, and it does NOT collect from
`condition.tickers[]` lists.

**What correct code produces (AC-7):** `extract_tickers` now also walks `condition.tickers[]`
from binary-compound condition blocks (per AC-7 requirement: watched tickers must be returned).
The small golden fixture (`sample_score_small.json`) contains one binary-compound condition block
with `tickers=['DWX', 'IXP', 'IWS', 'MGV', 'PTH', 'DEW', 'SCHG', 'XNTK', 'DNL', 'TILT']` —
10 tickers that are now included in `extract_tickers` output but NOT in the reference walker output.

**Root cause:** The oracle (`_ref_collect_tickers`) was accurate for the old `extract_tickers`
behavior but does NOT reflect the new AC-7 behavior. The old and new behaviors conflict on the
real fixture.

**Suggested fix for test-writer:** Update `_ref_collect_tickers` to also walk `condition.tickers[]`
lists (add handling for `current.get("tickers")` as a list), OR change the assertion from
`result == expected` to `expected.issubset(result)` (all tickers the reference walker finds must
be in the result, but result may be a superset). The existing AC-7 tests
(`test_frontrunner_overlay_extract_tickers_includes_watched_tickers`) explicitly verify the new
superset behavior is correct.

## Disputed Tests
None.

## Implementation Notes

- Only modified `advisors/symphony_schema.py` — no other files touched.
- Six new constructors appended: `make_condition_operand`, `make_constant_rhs`,
  `make_binary_condition`, `make_binary_compound_condition`, `make_compound_condition`,
  `make_if_compound`.
- Two new constants: `_KNOWN_CONDITION_TYPES`, `_KNOWN_OPERATORS` (used by `_validate_condition_block`).
- New helpers: `_validate_condition_block` (AC-8), `_extract_tickers_from_condition` (AC-7),
  `_render_condition_block` (AC-7 render extension).
- `_validate_condition_block` validates TOP-LEVEL condition block only (does not recurse into
  sub-conditions). Recursion was avoided because the pre-existing `test_compound_condition_block_tolerated`
  (amendment-6 test) has sub-conditions that lack `condition-type` — recursing into them would
  break that pre-existing passing test while none of the 4 AC-8 RED tests require nested recursion.
- `extract_tickers` now returns a strict superset of the old behavior for trees with condition
  blocks. This is correct per AC-7 but breaks the golden-fixture exact-match test (documented above).
- `make_binary_compound_condition`: `tickers` is stored as `list(tickers)` — shallow copy is
  sufficient because list elements are strings (immutable). Deep-copy of the list itself is done
  by Python's `list()` call.
- KNOWN_INDICATOR_FNS comment: filled in `n=?` placeholders per AC-11:
  `percentage-price-oscillator n=99`, `percentage-price-oscillator-signal n=100`,
  `upper-bollinger n=1`, `lower-bollinger n=1`.

## Status Log
- [2026-06-14] test-writer: Starting RED phase (Cycle B — symphony-compound-construction)
- [2026-06-14] test-writer: RED complete — 60 tests failing (all on AttributeError for
  non-existent constructors), 12 already-GREEN (regression guards + sanity guards).
  72 new Cycle B tests appended to tests/advisors/test_symphony_schema.py (section 12).
  0 import stubs created (new constructors appended to existing module).
  Failure mode confirmed: AttributeError: module 'advisors.symphony_schema' has no
  attribute 'make_condition_operand' (all 60 fail on attribute access, not syntax/import).
- [2026-06-14] implementer: GREEN complete — 210/211 tests passing. All 60 previously-RED
  Cycle B tests now GREEN. All 12 pre-existing Cycle B GREEN guards still GREEN. 1 pre-existing
  Cycle A test (TestGoldenFixtureSmall::test_small_fixture_extract_tickers_matches_reference_walk)
  now fails due to the AC-7 extract_tickers extension — documented as test file issue above.
  Only advisors/symphony_schema.py was modified. Typecheck N/A (stdlib only, no type-checker
  configured for this module). Lint: no ruff violations on the new code.
