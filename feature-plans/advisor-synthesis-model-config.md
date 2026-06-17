# Feature: ADVISOR_SYNTHESIS_MODEL — env-configurable advisor LLM model (C1)
Status: ready
Created: 2026-06-13 (refined 2026-06-17 — call-time design + hermetic tests locked)

## Summary
The advisor LLM model is hardcoded at three call sites. Make it operator-configurable via a single `ADVISOR_SYNTHESIS_MODEL` env var read **at call time** (so a change takes effect without a daemon restart and tests are hermetic), default `claude-opus-4-8`. Advisory-only; no execution-path impact. Does NOT re-plumb the Epic-A Phase-2 `prism-synthesizer` agent (that supersedes the nightly programmatic synthesis later).

## Acceptance Criteria
- **AC-1:** All three advisor LLM call sites read `os.environ.get("ADVISOR_SYNTHESIS_MODEL", "claude-opus-4-8")` **inline at the SDK call** (call time, NOT a module-level constant read at import time):
  - `advisors/lens_pipeline._synthesize_via_claude` (`messages.create`) — was hardcoded `claude-haiku-4-5-20251001`.
  - `ai_advisor.request_suggestions` (`messages.parse`) — was module constant `_CLAUDE_MODEL = "claude-opus-4-7"`.
  - `advisors/advisor_chat.explain_artifact` (`messages.create`) — was module constant `_CHAT_MODEL = "claude-opus-4-7"`.
- **AC-2:** Env var UNSET → all three resolve to `claude-opus-4-8`. This intentionally upgrades the nightly synthesis Haiku→Opus 4.8; record DE-SYNTH-001 with the cost rationale + the Epic-A scope boundary. Confirm `claude-opus-4-8` is the current Opus ID via the `/claude-api` reference; do NOT reintroduce `claude-opus-4-7`/`claude-opus-4-5`.
- **AC-3:** Env var SET → all three pass that value to the SDK call.
- **AC-4:** The now-dead module-level `_CLAUDE_MODEL` / `_CHAT_MODEL` constants are REMOVED (no unused duplicate-of-default; matches `lens_pipeline`'s existing inline style). Any test asserting the default must assert it via the SDK call, not a module constant.
- **AC-5:** `lens_pipeline`'s `_extract_json_object` fence-stripping / balanced-brace extraction (fixed at `df2d19e`) is **byte-preserved** — only the `model=` argument changes.
- **AC-6:** NO real Anthropic/LLM call in any pytest. Tests mock the client (`ai_advisor._build_client`) and assert the model string reaching the SDK call. Use function-scoped `monkeypatch.setenv`, never module-level `os.environ` mutation.
- **AC-7:** Audit `ai_advisor`, `advisors/advisor_chat`, `advisors/lens_pipeline`, `advisors/asset_swap_engine`, `advisors/logic_change_engine` for any OTHER hardcoded model literal; convert or confirm none remain (grep evidence in the handoff).

## Architecture
- Inline `os.environ.get("ADVISOR_SYNTHESIS_MODEL", "claude-opus-4-8")` at each `messages.create`/`messages.parse` `model=` argument; remove the module-level constants. `lens_pipeline.py` is the reference (already inline).
- Data flow unchanged: `run_pipeline → _synthesize_via_claude` and the two advisor calls read the env at call time → pass `model` to the Anthropic client. Response processing (JSON extraction, fence stripping) identical.

## Edge Cases
- Invalid/empty env value → SDK 400 at call time; each site's never-raising D-1 contract degrades to an honest error (only `type(exc).__name__`). No new validation.
- **Shared-process test isolation (the critical one):** assertions MUST be hermetic + order-independent. Do NOT rely on `importlib.reload` to re-evaluate a module constant — that fails once a sibling test (e.g. `tests/ai_advisor/test_chat_engine.py`) has already imported the module (stale package attribute). Call-time reads make reload unnecessary: set the env then call, and assert the model that reaches the mocked SDK. Include a DETERMINISTIC suite-ordering regression that reproduces the failure for BOTH `ai_advisor` and `advisor_chat`, and ensure the regression restores `sys.modules` cleanly so it does not pollute downstream tests.

## Security Considerations
- Model string is operator config, not user input — no injection/secret surface. D-1 contract intact. Off-execution-path; no `LIVE_EXECUTION` interaction. No change to `ANTHROPIC_API_KEY` handling.

## Testing Strategy
- `tests/ai_advisor/test_synthesis_model_config.py`: per-site default (unset→opus-4-8) + override (set→reaches SDK), no-real-LLM-call (mocked client), AC-5 fence-stripping byte-preservation guards, and the hermetic suite-ordering regression. Run: `pytest tests/ai_advisor/test_synthesis_model_config.py -p no:xdist -o addopts= -m "not live and not slow and not perf"`.
- Cycle-complete gate (PM-run, independent): full-suite verifier vs base `348dc26` (established zero-failure; `--ignore=tests/meta/test_zero_skip_xfail_close.py`) → must be 0 fail / 0 err. Watch specifically for the new test polluting `sys.modules` and breaking downstream tests (e.g. `tests/ui/test_cycle_4_advisor.py`).

## Scope Boundaries
- **IN:** the 3 call-site conversions, dead-constant removal, DECISIONS DE-SYNTH-001, regenerated module docs, the test file.
- **OUT:** any change to the synthesis prompt / `df2d19e` JSON-extraction logic; new model-ID validation; the Epic-A Phase-2 `prism-synthesizer` path; autotuner/core-engine model changes.

**Dependencies:** none hard. Base off `origin/main` `348dc26`.
