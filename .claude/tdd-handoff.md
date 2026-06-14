# TDD Handoff
Plan: feature-plans/symphony-validator-grammar.md
Branch: pr/symphony-validator-grammar
Phase: red

## Test Files
- tests/advisors/test_symphony_schema.py — new tests appended (classes TestGrammarV2Alignment,
  TestGrammarV2RegressionGuard, TestGrammarV2TypeGuard). Total file: 139 tests (122 passing,
  17 failing = the RED set).

## Behavioral Test Plan
N/A — pure unit tests, no UI/e2e surface.
advisors/symphony_schema.py is a stdlib-only pure-function module with no I/O, no Flask
dependency, no DB access, and no network calls. No e2e spec required.

## A/C Coverage Matrix

| A/C ID | Description | Test Class | Test Name(s) | Status |
|--------|-------------|------------|--------------|--------|
| AC-1 | validate_tree: no hard error for comparator "gte" | TestGrammarV2Alignment | test_comparator_gte_does_not_produce_hard_error | RED |
| AC-1 | validate_tree: "lte" still accepted after widening | TestGrammarV2Alignment | test_comparator_lte_still_does_not_produce_hard_error | GREEN (already passes) |
| AC-2 | validate_tree: no hard error for rebalance "quarterly" | TestGrammarV2Alignment | test_rebalance_quarterly_does_not_produce_hard_error | RED |
| AC-2 | validate_tree: no hard error for rebalance "yearly" | TestGrammarV2Alignment | test_rebalance_yearly_does_not_produce_hard_error | RED |
| AC-2 | Both quarterly and yearly clean in same run | TestGrammarV2Alignment | test_rebalance_quarterly_and_yearly_both_validate_clean_in_same_run | RED |
| AC-3 | lint_tree: no warning for exponential-moving-average-price (lhs-fn) | TestGrammarV2Alignment | test_exponential_moving_average_price_does_not_produce_lint_warning | RED |
| AC-3 | lint_tree: no warning for standard-deviation-price (lhs-fn) | TestGrammarV2Alignment | test_standard_deviation_price_as_lhs_fn_does_not_produce_lint_warning | RED |
| AC-3 | lint_tree: no warning for percentage-price-oscillator (lhs-fn) | TestGrammarV2Alignment | test_percentage_price_oscillator_does_not_produce_lint_warning | RED |
| AC-3 | lint_tree: no warning for percentage-price-oscillator-signal (lhs-fn) | TestGrammarV2Alignment | test_percentage_price_oscillator_signal_does_not_produce_lint_warning | RED |
| AC-3 | lint_tree: no warning for upper-bollinger (lhs-fn) | TestGrammarV2Alignment | test_upper_bollinger_does_not_produce_lint_warning | RED |
| AC-3 | lint_tree: no warning for lower-bollinger (lhs-fn) | TestGrammarV2Alignment | test_lower_bollinger_does_not_produce_lint_warning | RED |
| AC-3 | lint_tree: no warning for ema-price as sort-by-fn | TestGrammarV2Alignment | test_exponential_moving_average_price_as_sort_by_fn_does_not_warn | RED |
| AC-3 | lint_tree: no warning for std-dev-price as sort-by-fn | TestGrammarV2Alignment | test_standard_deviation_price_as_sort_by_fn_does_not_warn | RED |
| AC-3 | All 6 new fns produce no warnings in batch | TestGrammarV2Alignment | test_all_six_new_indicator_fns_produce_no_lint_warnings_in_batch | RED |
| AC-4 | "eq" comparator still errors after widening | TestGrammarV2RegressionGuard | test_unknown_comparator_eq_still_produces_hard_error_after_widening | GREEN |
| AC-4 | "neq" comparator still errors after widening | TestGrammarV2RegressionGuard | test_unknown_comparator_neq_still_produces_hard_error_after_widening | GREEN |
| AC-4 | Symbol ">" comparator still errors after widening | TestGrammarV2RegressionGuard | test_unknown_comparator_symbol_form_still_produces_error_after_widening | GREEN |
| AC-4 | "hourly" rebalance still errors after widening | TestGrammarV2RegressionGuard | test_unknown_rebalance_hourly_still_produces_hard_error | GREEN |
| AC-4 | "biweekly" rebalance still errors after widening | TestGrammarV2RegressionGuard | test_unknown_rebalance_biweekly_still_produces_hard_error | GREEN |
| AC-4 | "rsi" abbreviation still warns after widening | TestGrammarV2RegressionGuard | test_rsi_abbreviation_still_produces_lint_warning_after_widening | GREEN |
| AC-4 | Made-up fn still warns after widening | TestGrammarV2RegressionGuard | test_made_up_indicator_fn_still_produces_lint_warning_after_widening | GREEN |
| AC-4 | Original 7 v1 fns produce no lint warnings after widening | TestGrammarV2RegressionGuard | test_existing_v1_indicator_fns_produce_no_lint_warnings_after_widening | GREEN |
| AC-4 | Original 4 v1 rebalance values accepted after widening | TestGrammarV2RegressionGuard | test_existing_v1_rebalance_values_still_accepted_after_widening | GREEN |
| AC-5 | All three constants are frozenset | TestGrammarV2TypeGuard | test_all_three_vocabulary_constants_are_frozensets | GREEN |
| AC-5 | KNOWN_COMPARATORS is frozenset + immutable | TestGrammarV2TypeGuard | test_known_comparators_is_frozenset_and_not_mutable | GREEN |
| AC-5 | KNOWN_REBALANCE is frozenset + immutable | TestGrammarV2TypeGuard | test_known_rebalance_is_frozenset_and_not_mutable | GREEN |
| AC-5 | KNOWN_INDICATOR_FNS is frozenset + immutable | TestGrammarV2TypeGuard | test_known_indicator_fns_is_frozenset_and_not_mutable | GREEN |
| AC-5+AC-1 | KNOWN_COMPARATORS contains "gte" (canary) | TestGrammarV2TypeGuard | test_known_comparators_after_widening_still_contains_gte_as_member | RED |
| AC-5+AC-2 | KNOWN_REBALANCE contains "quarterly" (canary) | TestGrammarV2TypeGuard | test_known_rebalance_after_widening_contains_quarterly | RED |
| AC-5+AC-2 | KNOWN_REBALANCE contains "yearly" (canary) | TestGrammarV2TypeGuard | test_known_rebalance_after_widening_contains_yearly | RED |
| AC-5+AC-3 | KNOWN_INDICATOR_FNS contains all 6 new tokens (canary) | TestGrammarV2TypeGuard | test_known_indicator_fns_after_widening_contains_all_six_new_tokens | RED |

## Pre-existing Tests — Already Re-pointed (DONE — implementer leave alone)

The two conflicting pre-existing tests have been re-pointed by the test-writer. The implementer
must NOT touch them — they are now correct regression guards.

1. **TestAdversarialMutations::test_unknown_comparator_eq_produces_hard_error**
   - Was: `test_unknown_comparator_gte_produces_error` using `comparator="gte"`
   - Now: renamed + re-pointed to `comparator="eq"` (v2 §8: 0 corpus occurrences, permanent hard error)
   - Status: PASSES now AND will pass after GREEN (eq is never added to KNOWN_COMPARATORS)

2. **TestAdversarialCasesRound2::test_make_root_with_unknown_rebalance_produces_error_when_validated**
   - Was: using `rebalance="quarterly"` (was unknown in v1, now accepted in v2/AC-2)
   - Now: re-pointed to `rebalance="hourly"` (v2 §6 full census: 0 occurrences, permanent hard error)
   - Status: PASSES now AND will pass after GREEN (hourly is never added to KNOWN_REBALANCE)

## Questions for User
None — specification is complete from the v2 grammar doc and feature plan.

## Import Stubs Created
None required. All tests exercise the existing advisors/symphony_schema module.
No new modules are introduced by this cycle.

## RED Test Count: 17 (all fail on assertions, zero fail on import/syntax errors)

### RED tests (must turn GREEN after implementation):
1. TestGrammarV2Alignment::test_comparator_gte_does_not_produce_hard_error
2. TestGrammarV2Alignment::test_rebalance_quarterly_does_not_produce_hard_error
3. TestGrammarV2Alignment::test_rebalance_yearly_does_not_produce_hard_error
4. TestGrammarV2Alignment::test_rebalance_quarterly_and_yearly_both_validate_clean_in_same_run
5. TestGrammarV2Alignment::test_exponential_moving_average_price_does_not_produce_lint_warning
6. TestGrammarV2Alignment::test_standard_deviation_price_as_lhs_fn_does_not_produce_lint_warning
7. TestGrammarV2Alignment::test_percentage_price_oscillator_does_not_produce_lint_warning
8. TestGrammarV2Alignment::test_percentage_price_oscillator_signal_does_not_produce_lint_warning
9. TestGrammarV2Alignment::test_upper_bollinger_does_not_produce_lint_warning
10. TestGrammarV2Alignment::test_lower_bollinger_does_not_produce_lint_warning
11. TestGrammarV2Alignment::test_exponential_moving_average_price_as_sort_by_fn_does_not_warn
12. TestGrammarV2Alignment::test_standard_deviation_price_as_sort_by_fn_does_not_warn
13. TestGrammarV2Alignment::test_all_six_new_indicator_fns_produce_no_lint_warnings_in_batch
14. TestGrammarV2TypeGuard::test_known_comparators_after_widening_still_contains_gte_as_member
15. TestGrammarV2TypeGuard::test_known_rebalance_after_widening_contains_quarterly
16. TestGrammarV2TypeGuard::test_known_rebalance_after_widening_contains_yearly
17. TestGrammarV2TypeGuard::test_known_indicator_fns_after_widening_contains_all_six_new_tokens

### GREEN tests (already passing, must stay green):
All 122 pre-existing tests + new AC-4 regression guards + AC-5 type guards for current behavior.

## What the Implementer Must Do (deliberately blind to plan — read handoff only)

The implementation is three one-line frozenset literal edits in advisors/symphony_schema.py:

1. Add "gte" to KNOWN_COMPARATORS (~line 80).
2. Add "quarterly" and "yearly" to KNOWN_REBALANCE (~line 84).
3. Add "exponential-moving-average-price", "standard-deviation-price",
   "percentage-price-oscillator", "percentage-price-oscillator-signal",
   "upper-bollinger", "lower-bollinger" to KNOWN_INDICATOR_FNS (~line 65).
4. Update the two conflicting pre-existing tests described in "Conflicting Pre-existing Tests"
   above (changing their example values to genuinely-unknown tokens).
5. Update source-comment provenance above the three constants (AC-5 from the plan).

No logic changes. No new functions. No changes to validate_tree or lint_tree behavior.
The frozensets are already iterated in membership checks — adding tokens is the only change.

## Status Log
- [2026-06-14] test-writer: Starting RED phase
- [2026-06-14] test-writer: RED complete — 17 tests failing (all on assertions, zero
  import/syntax errors), 122 passing. Test file:
  tests/advisors/test_symphony_schema.py. 0 stubs created.
  Commit SHA: f06500c.
- [2026-06-14] test-writer: Re-pointed two stale pre-existing tests (test_unknown_comparator_eq_produces_hard_error
  from gte→eq; test_make_root_with_unknown_rebalance from quarterly→hourly). Both pass now and
  will pass after GREEN. 17 RED / 122 GREEN count unchanged.
