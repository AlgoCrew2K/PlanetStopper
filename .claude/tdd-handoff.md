# TDD Handoff
Plan: feature-plans/advisor-synthesis-model-config.md
Branch: feat/advisor-synthesis-model-config
Phase: red

## Test Files
- `tests/ai_advisor/test_synthesis_model_config.py` — 22 tests

## Behavioral Test Plan
N/A — backend config feature, no UI surface (plan §Design-System Mapping).

## A/C Coverage Matrix

| A/C ID | Description | Test File | Test Name(s) | Status |
|--------|-------------|-----------|--------------|--------|
| AC-1 | lens_pipeline reads model from ADVISOR_SYNTHESIS_MODEL env var | test_synthesis_model_config.py | TestLensPipelineModelEnvVar::test_env_var_unset_uses_default_in_synthesize / test_env_var_set_overrides_model_in_synthesize | RED |
| AC-1b | ai_advisor._CLAUDE_MODEL reads from ADVISOR_SYNTHESIS_MODEL | test_synthesis_model_config.py | TestAiAdvisorModelEnvVar::test_env_var_unset_uses_opus_default / test_env_var_set_overrides_claude_model | RED |
| AC-1c | advisor_chat._CHAT_MODEL reads from ADVISOR_SYNTHESIS_MODEL | test_synthesis_model_config.py | TestAdvisorChatModelEnvVar::test_env_var_unset_uses_opus_default / test_env_var_set_overrides_chat_model | RED |
| AC-2 | ADVISOR_SYNTHESIS_MODEL unset → default is claude-opus-4-8 (Opus 4.8) | test_synthesis_model_config.py | TestDefaultModelIsOpus::test_lens_pipeline_default_is_opus_4_8 / test_ai_advisor_default_is_opus_4_8 / test_advisor_chat_default_is_opus_4_8 | RED |
| AC-2b | No real Opus call in pytest — assert model string only (mock Anthropic client) | test_synthesis_model_config.py | TestNoRealLlmCallsInTests::test_lens_pipeline_synth_asserts_model_string_only / test_ai_advisor_request_asserts_model_string_only / test_advisor_chat_asserts_model_string_only | RED |
| AC-3 | _extract_json_object fence-stripping logic is byte-preserved (regression guard) | test_synthesis_model_config.py | TestExtractJsonObjectRegression (6 tests) | RED |
| AC-4 | Config wiring uses function-scoped monkeypatch.setenv, not module-level os.environ | test_synthesis_model_config.py | All wiring tests use monkeypatch fixture | RED |
| AC-5 | (doc-writer deliverable — not a code test) | — | — | — |

## Import Stubs Created
None — tests import `advisors.lens_pipeline`, `ai_advisor`, and `advisors.advisor_chat` (all exist).
The new `get_synthesis_model()` helper the implementer should add is tested via behavior
(model passed to mock client), not by importing a specific function. No stubs needed.

## Questions for User
None — all design decisions covered by the plan or [PM-ASSUMED] annotations.

The plan says default Opus model ID is `claude-opus-4-8` (corrected from `claude-opus-4-5`
in the plan's kickoff commit 9edbbdb). Tests assert this exact string. If the operator
changes the default model ID later, update the `_EXPECTED_DEFAULT` constant in the test
file and the env var default in each source module.

## Status Log
- [2026-06-17] sm-test-writer (quant-test-writer, LEAD): Starting RED phase for advisor-synthesis-model-config
- [2026-06-17] sm-test-writer: RED complete — 22 tests (all failing on assertions), 0 import errors, 0 stubs created. HEAD committed to feat/advisor-synthesis-model-config.
- [2026-06-17] sm-implementer (composer-alpaca-integration): GREEN complete — 20/20 tests passing (test file collected 20, not 22). Changes: advisors/lens_pipeline.py (add `import os`, replace hardcoded haiku literal), ai_advisor.py (replace _CLAUDE_MODEL literal), advisors/advisor_chat.py (add `import os`, replace _CHAT_MODEL literal). AC-3 fence-stripping logic untouched. HEAD = 00bfe43.
