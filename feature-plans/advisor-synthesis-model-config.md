# Feature: Advisor Synthesis Model — Configurable (Opus Prod / Cheap CI)
Status: ready
Created: 2026-06-13

## Summary

The advisor synthesis path currently hardcodes a model (`claude-haiku-4-5-20251001` in `advisors/lens_pipeline._synthesize_via_claude`, a Cycle-4 placeholder). Production analysis should run on Opus 4.8; CI/tests should not burn Opus tokens. This feature makes the model configurable via an environment variable with a sensible default so prod uses Opus and test/CI uses a cheap/mocked model. Scoped to advisor LLM calls that remain after Epic A (real agent team) lands — specifically: the Cycle-4 `_synthesize_via_claude` path and any advisor chat/swap/logic explanation calls that should be model-tiered. Does NOT re-plumb the Market Prism synthesizer agent (that becomes a standalone Opus 4.8 agent in Phase 2).

## Acceptance Criteria

- [ ] AC-1: The synthesis model (and any other advisor LLM call that is model-tiered) reads its model identifier from a single config source — an environment variable (e.g. `ADVISOR_SYNTHESIS_MODEL`) with a documented sensible default — not a hardcoded literal anywhere in the production path.
- [ ] AC-2: When `ADVISOR_SYNTHESIS_MODEL` is not set, the default resolves to Opus 4.8 in production. When set to a test/CI value, the code uses that model. No production path makes a real Opus call during a pytest run.
- [ ] AC-3: The JSON-extraction / fence-stripping logic fixed at `df2d19e` is preserved exactly — no behavior change to the response processing path.
- [ ] AC-4: Tests assert the config wiring (which model is selected under which env state), not a specific network response. No real LLM calls in unit tests.
- [ ] AC-5: The `ADVISOR_SYNTHESIS_MODEL` env var is documented in the project's configuration reference (doc-gen updates `docs/generated/` and `DECISIONS.md`).

## Architecture

**Files changed:**
- `advisors/lens_pipeline.py` — in `_synthesize_via_claude()`, replace the hardcoded model literal with `os.environ.get("ADVISOR_SYNTHESIS_MODEL", "claude-opus-4-5")` (exact Opus model ID to be confirmed; [PM-ASSUMED] follows the project's Anthropic client pattern)
- Any other advisor modules that hardcode a model literal in an LLM call — audit `ai_advisor.py`, `advisors/advisor_chat.py`, `advisors/asset_swap_engine.py`, `advisors/logic_change_engine.py` for hardcoded model strings; apply the same pattern
- `tests/conftest.py` or individual test modules — set `ADVISOR_SYNTHESIS_MODEL` to a test stub/cheap model before any test that triggers a synthesis call; ensure no Opus calls escape into CI

**Data flow (unchanged):** `run_pipeline()` → `_synthesize_via_claude(lens_outputs)` → reads env var → passes model to Anthropic client. Response processing (JSON extraction, fence stripping) is identical.

**Scope boundary with Epic A:** once Epic A Phase 2 lands, the Prism's overnight synthesis is done by the `prism-synthesizer` agent (Opus 4.8 directly) — the `_synthesize_via_claude` path is superseded for the nightly Prism read. This feature applies to the remaining advisor LLM calls (chat, swap/logic explanations, any non-Prism synthesis that stays on the programmatic path).

## Design-System Mapping

N/A — backend feature, no UI surface. (All 10 are backend/infra; the Cycle-5 Market Prism Overview UI already shipped separately.)

## Edge Cases

- **Empty or invalid `ADVISOR_SYNTHESIS_MODEL` env var:** if set to an empty string or an unrecognized model ID, the Anthropic client raises an error at call time (not at import time). D-1: `type(exc).__name__` on error, no model string leaked to the caller.
- **CI without the env var set:** the default (Opus 4.8) would fire real API calls in CI — tests MUST set the env var or mock the Anthropic client before any synthesis call.
- **Multiple advisor modules with hardcoded models:** the audit must cover all modules in `advisors/`; missing one leaves a hardcoded literal in the codebase. The implementer confirms the full list before the GREEN handoff.
- **Model ID drift (Anthropic deprecates Opus 4.5 or renames it):** the env var default is a single-point update. Document the exact model ID string and which Anthropic API version it targets.
- **Test isolation:** if `ADVISOR_SYNTHESIS_MODEL` is set globally in the test process, it affects all tests in that run. Use function-scoped `monkeypatch.setenv` in tests, not module-level `os.environ` mutation.

## Security Considerations

- **API key handling:** no change to `ANTHROPIC_API_KEY` handling. The model change does not affect key security.
- **Data exposure:** the model identifier is a config string, not sensitive data. It appears in the env but is not logged to the DB, UI, or Discord. D-1 contract unchanged.
- **Authz / advisory-only:** off-execution-path; no change to `LIVE_EXECUTION` interaction.
- **Input validation:** the env var is a model identifier string passed to the Anthropic client. The client validates the model; no additional validation needed in the producer.
- **No secret leakage:** the model name is not a secret; it may be logged for observability (e.g. in the audit log `source` field) without a security concern.

## Testing Strategy

**Approach:** this is either a one-line literal→env-var swap on an existing path (no new codepath, existing tests guard behavior) or a new codepath if the config loading adds meaningful logic. Confirm which at dispatch.

**If a pure literal→env swap (no new logic):**
- Existing tests in `tests/ai_advisor/` must pass without real Opus calls (add `monkeypatch.setenv("ADVISOR_SYNTHESIS_MODEL", "mock-model")` + mock Anthropic client where needed)
- `test_synthesis_model_config.py` — `os.environ` unset → default is the Opus model ID string; `ADVISOR_SYNTHESIS_MODEL=X` → model passed to client is `X`; assert no real HTTP call is made in tests (mock the Anthropic client)

**If new config-loading logic (new codepath → Toxic Pair TDD):**
- `tests/ai_advisor/test_synthesis_model_config.py` — same assertions as above, plus edge cases (empty string, unset)

**Fixture provenance:** no external API fixtures needed — tests mock the Anthropic client (no network). Assert model string, not response content.

**Run protocol:** `DB_PATH` set via `tests/conftest.py`; targeted: `pytest tests/ai_advisor -n0 -o addopts= -p no:xdist`. No real Opus/Anthropic calls in CI.

## Decisions

| Decision | Rationale |
|----------|-----------|
| Single env var `ADVISOR_SYNTHESIS_MODEL` with Opus 4.8 default | One change-point; consistent with the project's env-var config pattern; tests can override without code changes |
| Scope to remaining advisor LLM calls (not the Prism synthesizer) | Once Epic A lands, the Prism synthesizer becomes a standalone Opus 4.8 agent — this feature should not re-plumb that path |
| Preserve `df2d19e` JSON-extraction fix exactly | The fence-stripping logic was a deliberate fix; any response-processing change is out of scope |
| Confirm which modules have hardcoded literals at dispatch | An audit is needed before claiming the fix is complete; missing one leaves a hardcoded literal |

## Scope Boundaries

- **IN**: replace hardcoded model literal(s) in `advisors/lens_pipeline._synthesize_via_claude` and any other advisor LLM calls with an env-var-backed config; tests asserting config wiring; doc-gen updating the configuration reference
- **OUT**: Market Prism synthesizer agent (Phase 2 concern); changes to `df2d19e` JSON-extraction logic; changes to `ANTHROPIC_API_KEY` handling; model changes for the autotuner or core engine (out of scope for this feature)

**Dependencies:** none hard. Schedule around Epic A's exclusive-focus window (can be worked in parallel on a separate branch).
