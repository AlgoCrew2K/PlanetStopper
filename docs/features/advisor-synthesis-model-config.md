---
feature: advisor-synthesis-model-config
status: complete
related_files:
  - ai_advisor.py
  - advisors/advisor_chat.py
  - advisors/lens_pipeline.py
  - app.py
api_surface:
  - ai_advisor.resolve_advisor_model
  - ai_advisor.request_suggestions
  - advisors.advisor_chat.explain_artifact
  - advisors.lens_pipeline._synthesize_via_claude
tags: [advisor, llm, configuration, model-selection, env-var]
---

## Overview

Adds a single `ADVISOR_SYNTHESIS_MODEL` environment variable that controls the
LLM model ID used across all three advisor LLM call sites.  Previously each
call site either hardcoded a model literal or read a module-level constant;
this change makes model selection configurable at runtime without a daemon
restart.  The default upgrades Haiku to Opus 4.8, consistent with the project's
model routing guidelines.

## Architecture

**Call-time env read:** `os.environ.get("ADVISOR_SYNTHESIS_MODEL", "claude-opus-4-8")`
is evaluated inline at each `model=` argument, not at module import time.  This
makes tests hermetic via `monkeypatch.setenv` (no `importlib.reload` required)
and lets a running daemon pick up a config change on the next request without
a restart.

**Public accessor:** `ai_advisor.resolve_advisor_model() -> str` wraps the env
read into a named function so external callers (e.g. `app.py` audit-trail
routes) can use a stable import rather than an inline `os.environ.get` string.

**Dead constant removal:** The previous module-level constants `_CLAUDE_MODEL`
(in `ai_advisor.py`) and `_CHAT_MODEL` (in `advisors/advisor_chat.py`) were
removed at commit `46a6bc4`.  They were unused duplicates of the default value
and a maintenance trap — a future reader would not know whether the constant or
the env var was authoritative.

## API Surface

### `ai_advisor.resolve_advisor_model() -> str`
Returns the configured advisor synthesis model ID.  Reads
`ADVISOR_SYNTHESIS_MODEL` at call time; default `"claude-opus-4-8"`.  Used by
`app.py` accept/reject routes to record the env-resolved model ID in the
`llm_suggestions` audit trail.

### Three LLM call sites migrated
| File | Function | Change |
|------|----------|--------|
| `advisors/lens_pipeline.py` | `_synthesize_via_claude` | Hardcoded `"claude-haiku-4-5-20251001"` → env read |
| `advisors/advisor_chat.py` | `explain_artifact` | `_CHAT_MODEL` constant → env read |
| `ai_advisor.py` | `request_suggestions` | `_CLAUDE_MODEL` constant → env read |

### Two app.py audit-trail routes migrated
| File | Lines | Change |
|------|-------|--------|
| `app.py` | ~3748, ~3781 | `ai_advisor._CLAUDE_MODEL` → `ai_advisor.resolve_advisor_model()` |

## Testing

**Test file:** `tests/ai_advisor/test_synthesis_model_config.py` — 41 tests.

Key test classes:
- `TestLensPipelineSynthesisModelEnvVar` — env override/default/seam/zero-available
- `TestOpus48DefaultContract` — default is NOT Haiku
- `TestExtractJsonObjectPreserved` — 8 regression guards for `_extract_json_object`
- `TestAdvisorChatModelEnvVar` — chat env override/default/never-raises
- `TestRequestSuggestionsModelEnvVar` — suggestions env override/default/never-raises
- `TestNoHardcodedModelLiterals` — source-scan: no bare `model='claude-*'` kwargs
- `TestSuiteOrderingRegression` — deterministic ordering regression for both modules
- `TestDeadConstantsRemoved` — `_CLAUDE_MODEL`/`_CHAT_MODEL` absent from modules + source
- `TestNoExternalConstantReferences` — whole-codebase scan: no production file references removed attrs
- `TestAppPyAdvisorRouteModelWiring` — Flask route-level: accept/reject no AttributeError + env model recorded
- `TestLensPipelineRunPipelineModelWiring` — integration: env var reaches `messages.create` via `run_pipeline`

## Known Gaps

None.  The env var pattern is consistent across all 5 call sites (3 SDK calls
+ 2 app.py audit-trail writes).  `_extract_json_object` byte-preserved (AC-5).
D-1 and CC-2/CC-3 contracts unchanged on all modified paths.
