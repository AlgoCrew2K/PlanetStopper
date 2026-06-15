# TDD Handoff — derivatives-section-wiring
Plan: feature-plans/derivatives-section-wiring.md
Branch: feat/derivatives-section-wiring
Phase: green
HEAD SHA: 12218de

## Test Files
- tests/test_derivatives_section.py (root-level; 9 tests, 8 RED — prior test-writer)
- tests/ai_advisor/test_derivatives_section.py (32 tests, 23 RED — comprehensive coverage)

## RED Run Summary
Both test files together: 31 failing tests, 10 passing.

Canonical RED test run (tests/test_derivatives_section.py):
  8 failed, 1 passed — against the stub in ai_advisor.py ~lines 544-556
  which unconditionally returns available=False without calling the proxy.

Extended RED test run (tests/ai_advisor/test_derivatives_section.py):
  23 failed, 9 passed

All failures are against the stub. Tests that pass on the stub:
- Shape checks (lens=derivatives, available=False on failure path) — correct stub behavior
- CC-2 module-load check (stub does not import lens_options_proxy at module level)
- Stub marker test (intentional pass, flips RED after implementation until guard added)

## GREEN Run Summary (implementer)
Combined run (both test files): 39 passed, 2 failed

Root file only: 9/9 passed (including invariant guard test_lazy_import_not_at_module_load)

The 2 failures are test-writer issues (documented below), NOT implementation bugs:
- test_lazy_import_not_at_module_load: cross-file sys.modules pollution
- TestDerivativesSectionStubMarker::test_stub_returns_available_false_with_stub_reason: intentional stub-marker (documented as "flips after implementation")

## Call-site check (ai_advisor.py ~line 1208)
RESOLVED — assemble_advisor_context now wraps _build_derivatives_section() in a
try/except block (lines ~1210-1219) before the context dict is built. The AST
structural test test_derivatives_call_site_is_wrapped_in_try_except now passes.

## What implementer did

### 1. Replaced _build_derivatives_section body (ai_advisor.py)
Replaced the cycle-1 stub with the lazy-import + proxy call pattern:
- Lazy `from advisors import lens_options_proxy as _proxy_mod` inside function body (CC-2)
- Calls `_proxy_mod._fetch_options_proxy()` and propagates result verbatim
- Failure path: returns `{lens, available=False, reason=proxy_reason, payload=None, sources=[]}`
- Success path: calls `build_citation({title, url, published=as_of_date, lens})`, returns
  `{lens, available=True, payload={vix_level, vix_term_structure, risk_read, as_of_date}, sources}`
- Empty as_of_date -> build_citation returns None -> sources=[] but available=True maintained

### 2. Added try/except guard in assemble_advisor_context
Wrapped the `_build_derivatives_section()` call in try/except before the context dict
literal. On exception: degrades to `{lens, available=False, reason=type(exc).__name__,
payload=None, sources=[]}`. Context dict references `_derivatives_block` variable.

### 3. No other files changed
- advisors/lens_options_proxy.py: already on branch, not touched
- CLAUDE.md key-files: doc-writer's job

## AC Coverage
- AC-1 (CC-2 lazy import): test_lazy_import_not_at_module_load (root), test_lens_options_proxy_not_in_sys_modules_after_ai_advisor_import (ai_advisor/), test_lazy_import_fires_inside_function_not_before_via_ast (ai_advisor/)
- AC-2 (success shape + payload keys): test_success_path_shape, test_success_path_payload_keys, test_success_path_* (ai_advisor/)
- AC-2 (one source): test_success_path_one_source, test_success_path_sources_has_exactly_one_entry
- AC-3 (citation args): test_success_path_citation_args, TestDerivativesSectionBuildCitationArgs/*
- AC-4 (failure shape + proxy called): test_failure_path_shape
- AC-4 (reason propagated): test_failure_path_reason_propagated, test_failure_path_reason_is_propagated_from_proxy
- AC-5 (D-1 no str(exc) in section): test_failure_path_does_not_construct_reason_from_exception
- AC-6 / AC-9e (KeyError propagated): test_missing_fred_key_propagated, TestDerivativesSectionMissingFredKey/*
- AC-9 (call-site guard): test_derivatives_call_site_is_wrapped_in_try_except (AST)
- edge (empty as_of_date): test_empty_as_of_date_yields_no_sources_but_still_available, test_empty_as_of_date_yields_empty_sources_but_available_true
- edge (missing vix_term_structure): test_missing_vix_term_structure_in_proxy_result_does_not_raise

## Test File Issues (for test-writer to fix)

### 1. tests/test_derivatives_section.py::test_lazy_import_not_at_module_load
- **What the test expects:** `"advisors.lens_options_proxy" not in sys.modules` after importing `ai_advisor`
- **What correct code produces:** In isolation (root file only), the test PASSES (9/9). In a combined run with the extended file, `tests/ai_advisor/test_derivatives_section.py` has `import advisors.lens_options_proxy` at module level (line 41), polluting sys.modules before this test runs.
- **Root cause:** Root file's test does not save/restore sys.modules; the companion test in the extended file (`test_lens_options_proxy_not_in_sys_modules_after_ai_advisor_import`) correctly uses a save/restore pattern. Cross-file import pollution, not an implementation bug.
- **Suggested fix:** Add sys.modules save/restore to the root test, or add a fixture to isolate it.

### 2. tests/ai_advisor/test_derivatives_section.py::TestDerivativesSectionStubMarker::test_stub_returns_available_false_with_stub_reason
- **What the test expects:** `_build_derivatives_section()` returns `available=False` with "cycle-2b"/"not connected" in reason
- **What correct code produces:** After implementation, calling without patching the proxy invokes the real FRED proxy which may succeed (returning available=True) or fail with a real proxy reason (not "cycle-2b deliverable")
- **Root cause:** Intentional. The test-writer explicitly documented: "PASSES on the stub. FAILS after implementation. This test will be deleted or updated after the RED/GREEN cycle."
- **Suggested fix:** Delete this stub-marker test (it served its purpose) or update it to assert post-implementation behavior.

## Status Log
- [2026-06-15] implementer: GREEN complete — 39/41 tests passing in combined run; 9/9 in root file isolation (including CC-2 invariant guard). 2 test-file issues documented. Typecheck N/A (Python). Lint deferred to finalize.
