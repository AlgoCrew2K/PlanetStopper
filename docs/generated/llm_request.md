# llm_request

> The one request helper for every Anthropic Messages call that names a resolved model: it resolves the model, sends one request, recovers from a retired model, and returns a classified `Reply` with a reason code instead of raising.

**Source:** `llm_request.py`
**Last updated:** 2026-11-02

## Overview

Every advisor call site (`ai_advisor.request_suggestions`, `advisors.advisor_chat.explain_artifact`, `advisors.build_plan_generator.generate_build_plans`, `advisors.asset_swap_engine.generate_reasoned_swap_candidates`, `advisors.logic_change_engine.generate_reasoned_logic_candidates`, `advisors.frontrunner_builder.generate_candidate_overlay`, `advisors.lens_pipeline` synthesis, `advisors.retirement_explainer`) sends through `send`. The helper exists because the models in use always think, reject forced tool choice, and can answer a classifier refusal as an HTTP 200 reply, so the old pattern (forced `tool_choice`, read the first block, parse in the SDK) no longer fits.

What `send` builds for each request: `output_config = {"effort": <caller's effort>}` (every site passes `"high"`), plus `{"format": {"type": "json_schema", ...}}` when the caller gives `output_schema`; `tool_choice = {"type": "auto"}` whenever tools are passed (never `any` or `tool`); `extra_body = {"fallbacks": "default"}` and the beta header `anthropic-beta: server-side-fallback-2026-07-01`, so a classifier refusal is retried on a server-side fallback model; no `temperature`, `top_p`, `top_k` or manual thinking budget. `max_tokens` covers thinking plus text and is set per call site.

## API Reference

### `send(client, knob, *, effort, max_tokens, messages, system=None, tools=None, output_schema=None, timeout=None) -> Reply`
Resolves `knob` through `model_config.resolve`, sends one `client.messages.create`, and classifies the reply. Imports `anthropic` lazily.
- Empty resolution: returns `Reply(REASON_MODEL_UNAVAILABLE)` without sending.
- HTTP 404 on a resolved (non-override) model: calls `model_config.invalidate`, and if a different model results, sends once more on it; a second 404, or no newer model, gives `REASON_MODEL_UNAVAILABLE`.
- HTTP 404 on an override: `REASON_OVERRIDE_MODEL_NOT_FOUND`, naming the environment variable, never replaced by the family model.
- HTTP 400 on an override: `REASON_OVERRIDE_REQUEST_REJECTED`; on a resolved model the `BadRequestError` is re-raised to the caller, which handles it under its own never-raises contract.
- Reply classification (`_classify`): `stop_reason == "refusal"` gives `REASON_REFUSAL` (with the null-safe `stop_details.category` when present); `max_tokens` gives `REASON_MAX_TOKENS`; tools passed and no `tool_use` block gives `REASON_NO_TOOL_CALL`; otherwise success. `Reply.model` is the model the API reports in the response (the serving model, which can differ from the requested one after a fallback), else the requested id.

### `Reply`
`reason` (None on success), `model`, `text()` (joins `text` blocks only, so thinking blocks are skipped), `tool_input()` (the first `tool_use` block's input), `reason_text()` (`"refusal: <category>"`, `"override_model_not_found: <ENV_VAR>"`, or the bare code).

### Reason codes
`REASON_MODEL_UNAVAILABLE = "model_unavailable"`, `REASON_OVERRIDE_MODEL_NOT_FOUND`, `REASON_OVERRIDE_REQUEST_REJECTED`, `REASON_REFUSAL`, `REASON_MAX_TOKENS`, `REASON_NO_TOOL_CALL`.

### `NO_TOOL_CALL_ATTEMPTS = 3`
Attempts a call site may make when a reply carries no tool call. The tool-emitting generators loop on it (asset swaps, logic changes, build plans, frontrunner overlay); any other reason ends the loop.

### `strict_tool(tool) -> dict`
Deep copy of a tool definition with `strict: true` and `additionalProperties: false` on every `type: "object"` node.

### `default_generation_model() -> str`
The suggestion knob's model id from `model_config.peek`, without listing. Seeds `provenance["generation_model"]` in the engines and the accept/reject audit row.

### `adopt_reply(provenance, replies)`
Writes the serving model of the last reply into `provenance["generation_model"]` (cleared to `""` when that reply had no model, since none served the run) and returns that reply. With no replies collected, the minted model stays.

### `empty_run_message(reply, noun, default) -> str`
`"no <noun> was generated this run (<reason text>)"` when the reply has a reason, else `default`. Panels therefore say that nothing was generated, not that the gate rejected a candidate.

### `reason_in_message(message) -> str | None`
Reads the reason code back out of a message produced by `empty_run_message` (used by `weekly_suggestions_scheduler` to log the cause).

## Internal Dependencies
`model_config` (resolution), `anthropic` (lazy, inside `send`).

## Data honesty
A model id shown to an operator is either an override, a display name from the Models API, or the serving model the API reported on that run. Where no model served a run the id is empty, not a guess.
