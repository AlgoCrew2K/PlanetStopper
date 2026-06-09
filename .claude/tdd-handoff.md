# TDD Handoff
Plan: N/A — defect fix (D3 from .design-handoff/advisor-ui-diag/TAB-DEFECTS-RCA.md)
Branch: feat/advisor-tab-fixes
Phase: red

## Test Files
- `tests/ai_advisor/test_advisor_chat_handoff.py` — 30 tests total (21 failing, 5 skipped, 4 passing)

## A/C Coverage Matrix

| A/C ID | Description | Test File | Test Name(s) | Status |
|--------|-------------|-----------|--------------|--------|
| D3-S1 | asset_swaps.js contains sessionStorage.setItem | test_advisor_chat_handoff.py | TestAssetSwapsSenderHandoff::test_asset_swaps_js_contains_sessionStorage_setItem | RED |
| D3-S2 | asset_swaps.js uses key 'pendingChatArtifact' | test_advisor_chat_handoff.py | TestAssetSwapsSenderHandoff::test_asset_swaps_js_sessionStorage_uses_correct_key | RED |
| D3-S3 | asset_swaps.js setItem call is a direct string-literal call | test_advisor_chat_handoff.py | TestAssetSwapsSenderHandoff::test_asset_swaps_js_sessionStorage_setItem_with_key_in_same_expression | RED |
| D3-S4 | No bare navigation without preceding sessionStorage write | test_advisor_chat_handoff.py | TestAssetSwapsSenderHandoff::test_asset_swaps_js_no_bare_navigation_in_else_branch | RED |
| D3-S5 | Both else AND catch branches write sessionStorage (≥2 setItem calls) | test_advisor_chat_handoff.py | TestAssetSwapsSenderHandoff::test_asset_swaps_js_catch_branch_also_writes_sessionStorage | RED |
| D3-L1 | logic_changes.js contains sessionStorage.setItem | test_advisor_chat_handoff.py | TestLogicChangesSenderHandoff::test_logic_changes_js_contains_sessionStorage_setItem | RED |
| D3-L2 | logic_changes.js uses correct key | test_advisor_chat_handoff.py | TestLogicChangesSenderHandoff::test_logic_changes_js_sessionStorage_uses_correct_key | RED |
| D3-L3 | logic_changes.js setItem is a direct call | test_advisor_chat_handoff.py | TestLogicChangesSenderHandoff::test_logic_changes_js_sessionStorage_setItem_with_key_in_same_expression | RED |
| D3-L4 | logic_changes.js no bare navigation without write | test_advisor_chat_handoff.py | TestLogicChangesSenderHandoff::test_logic_changes_js_no_bare_navigation_without_sessionStorage_write | RED |
| D3-L5 | logic_changes.js both branches write (≥2 setItem calls) | test_advisor_chat_handoff.py | TestLogicChangesSenderHandoff::test_logic_changes_js_catch_branch_also_writes_sessionStorage | RED |
| D3-R1 | ai_advisor_chat.js reads sessionStorage.getItem | test_advisor_chat_handoff.py | TestChatReceiverHandoff::test_chat_js_reads_sessionStorage_getItem | RED |
| D3-R2 | chat.js reads correct key | test_advisor_chat_handoff.py | TestChatReceiverHandoff::test_chat_js_reads_correct_key | RED |
| D3-R3 | chat.js getItem call is a direct string-literal call | test_advisor_chat_handoff.py | TestChatReceiverHandoff::test_chat_js_getItem_with_correct_key_in_same_expression | RED |
| D3-R4 | chat.js calls sessionStorage.removeItem after consuming | test_advisor_chat_handoff.py | TestChatReceiverHandoff::test_chat_js_removes_item_from_sessionStorage | RED |
| D3-R5 | chat.js removeItem uses correct key | test_advisor_chat_handoff.py | TestChatReceiverHandoff::test_chat_js_removes_correct_key | RED |
| D3-R6 | sessionStorage read is inside DOMContentLoaded handler | test_advisor_chat_handoff.py | TestChatReceiverHandoff::test_chat_js_sessionStorage_read_is_in_domcontentloaded_handler | SKIPPED (getItem absent) |
| D3-R7 | openChatPanel called after getItem | test_advisor_chat_handoff.py | TestChatReceiverHandoff::test_chat_js_calls_openChatPanel_after_reading_sessionStorage | SKIPPED (getItem absent) |
| D3-R8 | removeItem follows openChatPanel in handler | test_advisor_chat_handoff.py | TestChatReceiverHandoff::test_chat_js_removeItem_follows_openChatPanel_in_domcontentloaded | SKIPPED (getItem absent) |
| D3-G1 | Receiver has null guard or try/catch near getItem | test_advisor_chat_handoff.py | TestChatReceiverRobustness::test_chat_js_has_null_guard_or_trycatch_near_getItem | SKIPPED (getItem absent) |
| D3-G2 | removeItem not called unconditionally before openChatPanel | test_advisor_chat_handoff.py | TestChatReceiverRobustness::test_chat_js_does_not_call_removeItem_unconditionally | SKIPPED (getItem absent) |
| D3-K1 | asset_swaps and chat use same key | test_advisor_chat_handoff.py | TestHandoffKeyCoherence::test_asset_swaps_and_chat_use_same_key | RED |
| D3-K2 | logic_changes and chat use same key | test_advisor_chat_handoff.py | TestHandoffKeyCoherence::test_logic_changes_and_chat_use_same_key | RED |
| D3-K3 | No alternate key spellings in sender files | test_advisor_chat_handoff.py | TestHandoffKeyCoherence::test_no_alternate_key_spellings_in_sender_files | PASSES (no typos) |
| D3-SX1 | JS syntax: asset_swaps.js passes node --check | test_advisor_chat_handoff.py | TestJsSyntaxValidity::test_asset_swaps_js_passes_node_check | PASSES (no change yet) |
| D3-SX2 | JS syntax: logic_changes.js passes node --check | test_advisor_chat_handoff.py | TestJsSyntaxValidity::test_logic_changes_js_passes_node_check | PASSES (no change yet) |
| D3-SX3 | JS syntax: ai_advisor_chat.js passes node --check | test_advisor_chat_handoff.py | TestJsSyntaxValidity::test_chat_js_passes_node_check | PASSES (no change yet) |

Note: 5 tests are SKIPPED with `pytest.skip()` (not XFAIL) because they depend on
`sessionStorage.getItem` being present in chat.js — which it isn't yet.  Once the
implementer adds the getItem call, those 5 tests will automatically ungate and run.
They will become RED at that point if the order/robustness contract is violated.

## Questions for User
None — specification is complete from the RCA.

## Import Stubs Created
None required — these are pure JS pattern-matching tests; no new Python modules are
introduced.  The test imports only stdlib (pathlib, re, subprocess) plus pytest.

## Status Log
- [2026-06-09] test-writer: Starting RED phase
- [2026-06-09] test-writer: RED complete — 30 tests (21 failing, 5 skipped, 4 passing on pre-fix
  codebase), 0 stubs created.
  Failing = the D3 sessionStorage codepath does not exist yet (correct RED).
  Skipped = order/robustness tests that gate on getItem being present (will ungate automatically).
  Passing = node --check (files are syntax-valid today) + no-typo check (both correct baselines).
