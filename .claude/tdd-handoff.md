# TDD Handoff
Plan: feature-plans/symphony-grammar-foundation.md
Branch: team/grammar-foundation
Phase: green

## Test Files
- `tests/advisors/test_symphony_schema.py` — grammar-foundation tests appended (AC-1..AC-12)
- `tests/fixtures/math/compound_condition_binary_basic.json` — golden fixture for binary condition
- `tests/fixtures/math/compound_condition_binary_compound_basic.json` — golden fixture for binary-compound
- `tests/fixtures/math/compound_condition_compound_any.json` — golden fixture for compound-any
- `tests/fixtures/math/frontrunner_overlay_integration.json` — golden fixture for AC-9 integration

## Behavioral Test Plan
No UI changes — pure stdlib module. Behavioral plan: validate_tree / constructors / extract_tickers /
render_rules_text all exercised via unit tests directly against the module.

## A/C Coverage Matrix

| A/C ID | Description | Test File | Test Name(s) | Status |
|--------|-------------|-----------|--------------|--------|
| AC-1 | gte accepted; eq/neq still error | test_symphony_schema.py | TestGrammarFoundationComparatorWidening | GREEN |
| AC-2 | quarterly/yearly accepted; hourly still errors | test_symphony_schema.py | TestGrammarFoundationRebalanceWidening | GREEN |
| AC-3 | 6 new indicator fns no lint warning; rsi still warns | test_symphony_schema.py | TestGrammarFoundationIndicatorFnWidening | GREEN |
| AC-4 | make_condition_operand + make_constant_rhs constructors | test_symphony_schema.py | TestCompoundConditionOperandConstructors | GREEN |
| AC-5 | make_binary_condition → binary leaf | test_symphony_schema.py | TestMakeBinaryCondition | GREEN |
| AC-6 | make_binary_compound_condition → binary-compound | test_symphony_schema.py | TestMakeBinaryCompoundCondition | GREEN |
| AC-7 | make_compound_condition → compound | test_symphony_schema.py | TestMakeCompoundCondition | GREEN |
| AC-8 | make_if_compound → if with condition block | test_symphony_schema.py | TestMakeIfCompound | GREEN |
| AC-9 | full frontrunner overlay integration | test_symphony_schema.py | TestFrontrunnerOverlayIntegration | GREEN |
| AC-10 | validate_tree hard errors on malformed compound at any depth, bounded | test_symphony_schema.py | TestCompoundConditionValidation | GREEN |
| AC-11 | ValueError on bad operator/empty inputs; deep-copy; read-only | test_symphony_schema.py | TestCompoundConditionInvariants | GREEN |
| AC-12 | no regression on flat constructors and existing validation | test_symphony_schema.py | existing tests + TestGrammarFoundationNoRegression | GREEN |

## Conflicts with existing tests (must be updated in same commit)

The following existing tests pin the OLD OQ-2/grammar stance that AC-1 and AC-2 REVERSE.
They must be updated to reflect corpus-verified ground truth:

1. `TestAdversarialMutations::test_unknown_comparator_gte_produces_error` (line 654)
   — AC-1 says `gte` is NOW VALID (n≈39,596 in corpus). Test must be inverted.
2. `TestModuleConstants::test_known_comparators_does_not_contain_gte` (line 2362)
   — AC-1 says `gte` MUST be in KNOWN_COMPARATORS. Test must be inverted.
3. `TestPropertyStyleInvariants::test_validate_tree_does_not_mutate_input_on_invalid_tree` (line 1695)
   — Uses `"quarterly"` as invalid rebalance; AC-2 makes `quarterly` valid. Use `"hourly"` instead.
4. `TestAdversarialCasesRound2::test_make_root_with_unknown_rebalance_produces_error_when_validated` (line 2645)
   — Uses `"quarterly"` as invalid; same fix.
5. `TestAdversarialCasesRound2::test_validate_tree_errors_are_all_strings` (line 2601)
   — Uses `"quarterly"` as invalid; fix to use a truly-invalid value.
6. `TestAdversarialCasesRound2::test_valid_rebalance_values_all_accepted_without_error` (line 2546)
   — Only tests 4 values; should also test `quarterly` and `yearly` (AC-2).
7. Module-level docstring comment `"gte" must fail` (line 20)
   — Out of date; implementer should update the comment to reflect corpus ground truth.

## Questions for User
None — spec is complete from feature-plans/symphony-grammar-foundation.md.

## Import Stubs Created
None required — all new constructors live in the existing `advisors/symphony_schema.py`
module. The test file imports `advisors.symphony_schema` which already exists;
new functions are accessed via `hasattr` guards in fixture-shape tests and via
direct calls in constructor tests (which will fail with AttributeError until the
implementer adds the functions — the correct RED failure mode).

## Status Log
- [2026-06-14] test-writer: Starting RED phase for grammar-foundation AC-1..AC-12
- [2026-06-14] implementer: GREEN partial — 108/108 passing on AC-1 (gte added to KNOWN_COMPARATORS) + AC-2 (quarterly/yearly added to KNOWN_REBALANCE). AC-4..AC-12 constructor tests not yet in test file — awaiting test-writer commit of new test classes before implementing those.
- [2026-06-14] test-writer: RED commit complete. Full test run on target file: 74 failed / 136 passed. AC-1/AC-2 already GREEN (implementer partial). AC-3..AC-12 all RED (74 failures — AttributeError on missing constructors for AC-4..AC-12, assertion failure on KNOWN_INDICATOR_FNS for AC-3). 7 conflicting existing tests updated to reflect corpus ground truth. 4 golden fixtures committed. Implementer: run /tdd-implement against this handoff to bring AC-3..AC-12 GREEN.
- [2026-06-14] implementer: GREEN 209/210. AC-3..AC-12 all GREEN (102 new tests passing). BLOCKED on 1 pre-existing test conflict not listed in handoff: `TestGoldenFixtureSmall::test_small_fixture_extract_tickers_matches_reference_walk` (line 172). Notified test-writer; conflict diagnosed and resolved collaboratively (see next entry).
- [2026-06-14] implementer+test-writer: GREEN 210/210 on 7f85791. Two-part fix: (1) test-writer updated `_ref_collect_tickers` to walk condition block tickers lists (excluding '%'); (2) implementer updated `_render_compound_condition_line` for compound type to collect and list all nested binary-compound tickers in the rendered line, satisfying the render_rules_text golden fixture contract. Cycle complete.
