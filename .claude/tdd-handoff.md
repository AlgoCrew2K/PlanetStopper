# TDD Handoff
Plan: feature-plans/advisor-synthesis-model-config.md
Branch: feat/advisor-synthesis-model-config
Phase: green

## Test Files
- `tests/ai_advisor/test_synthesis_model_config.py` — 30 tests collected
  (14 failing RED on correct assertions, 16 passing as regression guards)

## Behavioral Test Plan
N/A — backend producer, no UI surface (plan §Design-System Mapping states "N/A").

## A/C Coverage Matrix

| A/C ID | Description | Test File | Test Name(s) | Status |
|--------|-------------|-----------|--------------|--------|
| AC-1 | lens_pipeline._synthesize_via_claude reads model from ADVISOR_SYNTHESIS_MODEL | test_synthesis_model_config.py | TestLensPipelineSynthesisModelEnvVar::test_env_var_set_overrides_synthesis_model | RED |
| AC-2 | Default=Opus 4.8 when env unset; env override honored; no real API in pytest | test_synthesis_model_config.py | TestLensPipelineSynthesisModelEnvVar::test_env_var_unset_uses_opus_default; TestOpus48DefaultContract::test_default_model_is_opus_48_not_haiku | RED |
| AC-2 (regression) | _build_client seam intact; zero-available skips LLM | test_synthesis_model_config.py | test_no_real_claude_call_escapes_in_pytest; test_zero_available_lenses_skips_claude_call | GREEN (guards) |
| AC-3 | _extract_json_object preserved byte-for-exact | test_synthesis_model_config.py | TestExtractJsonObjectPreserved::* (8 tests) | GREEN (regression guards) |
| AC-4 | advisor_chat.explain_artifact reads model from ADVISOR_SYNTHESIS_MODEL | test_synthesis_model_config.py | TestAdvisorChatModelEnvVar::test_env_var_set_overrides_chat_model; test_env_var_unset_uses_opus_default_in_chat | RED |
| AC-4 (regression) | chat never raises graceful degradation survives refactor | test_synthesis_model_config.py | TestAdvisorChatModelEnvVar::test_chat_never_raises | GREEN (guard) |
| AC-5 | request_suggestions reads model from ADVISOR_SYNTHESIS_MODEL | test_synthesis_model_config.py | TestRequestSuggestionsModelEnvVar::test_env_var_set_overrides_suggestions_model; test_env_var_unset_uses_opus_default_in_suggestions | RED |
| AC-5 (regression) | request_suggestions never raises survives refactor | test_synthesis_model_config.py | TestRequestSuggestionsModelEnvVar::test_request_suggestions_never_raises_after_refactor | GREEN (guard) |
| AC-6 | No hardcoded model literal at LLM call sites; env var in all 3 files | test_synthesis_model_config.py | TestNoHardcodedModelLiterals::test_lens_pipeline_has_no_hardcoded_model_kwarg; test_env_var_name_appears_in_all_three_files | RED |
| AC-6 (regression) | advisor_chat and ai_advisor already use variable refs not literals | test_synthesis_model_config.py | test_advisor_chat_has_no_hardcoded_model_kwarg; test_ai_advisor_has_no_hardcoded_model_kwarg | GREEN (guards) |
| AC-7 | Suite ordering / stale-package-attribute regression (both modules) | test_synthesis_model_config.py | TestSuiteOrderingRegression::* (4 tests) | RED |
| AC-7 (regression) | sys.modules not polluted across tests | test_synthesis_model_config.py | TestSuiteOrderingRegression::test_sys_modules_not_polluted_by_lens_pipeline_test | GREEN (guard) |
| Integration | run_pipeline env var wires end-to-end | test_synthesis_model_config.py | TestLensPipelineRunPipelineModelWiring::test_run_pipeline_with_available_lens_uses_env_model | RED |
| Integration (regression) | dry_run skips LLM call survives refactor | test_synthesis_model_config.py | TestLensPipelineRunPipelineModelWiring::test_run_pipeline_dry_run_never_calls_llm | GREEN (guard) |

## Implementation Contract (implementer reads THIS, not the plan)

**What to change (exactly 3 call sites):**

1. `advisors/lens_pipeline.py` — `_synthesize_via_claude()` function, line ~284:
   BEFORE (hardcoded literal): `model="claude-haiku-4-5-20251001",  # lightest model for synthesis`
   AFTER (env-var backed, read at call time): `model=os.environ.get("ADVISOR_SYNTHESIS_MODEL", "claude-opus-4-8"),`
   Note: `import os` must be added at the top of the file (currently absent).

2. `ai_advisor.py` — `request_suggestions()` function, line ~1632:
   BEFORE: `model=_CLAUDE_MODEL,`
   AFTER: `model=os.environ.get("ADVISOR_SYNTHESIS_MODEL", "claude-opus-4-8"),`
   Note: `os` is already imported in `ai_advisor.py`. Module-level constant
   `_CLAUDE_MODEL = "claude-opus-4-7"` MAY remain (tests assert hasattr).

3. `advisors/advisor_chat.py` — `explain_artifact()` function, line ~389:
   BEFORE: `model=_CHAT_MODEL,`
   AFTER: `model=os.environ.get("ADVISOR_SYNTHESIS_MODEL", "claude-opus-4-8"),`
   Note: `import os` must be added if not present. `_CHAT_MODEL = "claude-opus-4-7"`
   MAY remain as a comment anchor.

**What NOT to change:**
- `_extract_json_object` in `advisors/lens_pipeline.py` — preserved byte-for-exact (AC-3)
- `_build_client()` in `ai_advisor.py` — mock seam must stay as-is
- Any DB, persistence, scheduling, or response processing code
- `_CODE_FENCE_OPEN_RE`, `_CODE_FENCE_CLOSE_RE` regex patterns

**The mock seam contract:**
All 3 paths route through `ai_advisor._build_client()`. Tests patch this.
Do NOT add separate client construction paths.

**Failing test count:** 14 RED on correct assertions. 16 PASS as regression guards.
All 14 failures on AssertionError (not ImportError or SyntaxError).

## Import Stubs Created
None — all target modules exist. No new modules introduced by this feature.

## Questions for User / PM
None. All design decisions covered by the feature plan or [PM-ASSUMED].

## Test File Issues (for test-writer to fix)
None.

## Test File Issues (for test-writer to fix)
None.

## Implementation Notes
- `advisors/lens_pipeline.py`: added `import os` (previously absent); replaced hardcoded `"claude-haiku-4-5-20251001"` literal with `os.environ.get("ADVISOR_SYNTHESIS_MODEL", "claude-opus-4-8")` at `_synthesize_via_claude` call site (line 284). `_extract_json_object`, `_build_client`, and all regex patterns unchanged.
- `ai_advisor.py`: `os` already imported; replaced `_CLAUDE_MODEL` reference with `os.environ.get("ADVISOR_SYNTHESIS_MODEL", "claude-opus-4-8")` at `request_suggestions` call site (line 1632). Module-level `_CLAUDE_MODEL = "claude-opus-4-7"` constant preserved (tests assert `hasattr`).
- `advisors/advisor_chat.py`: added `import os`; replaced `_CHAT_MODEL` reference with `os.environ.get("ADVISOR_SYNTHESIS_MODEL", "claude-opus-4-8")` at `explain_artifact` call site (line 389). `_CHAT_MODEL = "claude-opus-4-7"` constant preserved as comment anchor.
- All 3 paths still route through `ai_advisor._build_client()` mock seam — no new client construction paths introduced.

## Disputed Tests
None.

## Status Log
- [2026-06-17] c1-test-writer: Starting RED phase for advisor-synthesis-model-config
- [2026-06-17] c1-test-writer: RED complete — 30 tests: 14 failing RED (correct assertion failures on current code), 16 passing regression guards. 0 import/syntax errors. 0 stubs. HEAD committed to feat/advisor-synthesis-model-config.
- [2026-06-17] c1-implementer: GREEN complete — 30/30 tests passing. Sibling pollution check: 1146 passed, 10 skipped, 0 failures (tests/ai_advisor/). 0 test bugs. 3 production files changed (import os added to 2 files, 3 model literal/constant refs replaced). Typecheck N/A (Python). Lint pending (see /lint).
- [2026-06-17] c1-test-writer: RED — AC-4 dead-constant removal (5 new tests, 5b104e4). 5 FAILED / 29 PASSED.
- [2026-06-17] c1-implementer: GREEN — deleted _CLAUDE_MODEL + comment from ai_advisor.py; deleted _CHAT_MODEL + 2 comment lines from advisors/advisor_chat.py. 34/34 passed. 0 test bugs.
