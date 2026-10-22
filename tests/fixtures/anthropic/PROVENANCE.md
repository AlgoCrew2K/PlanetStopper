# Anthropic API fixtures: provenance

Captured 2026-10-22 on the production droplet with the app's own API key, anthropic SDK 0.85.0.
Every file under `models/`, `errors/` and `messages/` listed here is the raw HTTP response body exactly
as the API returned it; no field was edited. Error files wrap the body as `{"status": <code>, "body": <body>}`.
Newest family members at capture time (Models API, newest `created_at` per `line`): opus `claude-opus-5-5`, fable `claude-fable-5-1`.

Request shape for every captured reply (mirrors the model-unpin approach): `tool_choice` `auto`,
`fallbacks: "default"` in the body with the `anthropic-beta: server-side-fallback-2026-07-01` header,
`output_config.effort` `low` (chosen only to keep capture spend small; reply shape does not depend on effort).
Strict tools (`emit_swap_candidates`, `emit_logic_edits`) were sent with `strict: true` and
`additionalProperties: false` on every object node; `count_tokens` accepted both strict schemas
(707 and 692 input tokens). `emit_build_plans` and `emit_frontrunner_overlay` were sent unchanged (non-strict).

| File | Case |
|---|---|
| `models/page_captured.json` | GET /v1/models?limit=1000 raw page |
| `errors/404_not_found.json` | messages.create with the generic tag 'opus' -> HTTP 404 |
| `errors/400_invalid_request.json` | temperature sent to the newest opus-line model -> HTTP 400 |
| `messages/thinking_text.json` | opus-line, plain text / stop_reason=end_turn blocks=thinking,text |
| `messages/thinking_tool_emit_swap_candidates.json` | fable-line, tool_choice auto, strict=True / stop_reason=tool_use blocks=tool_use |
| `messages/thinking_tool_emit_logic_edits.json` | fable-line, tool_choice auto, strict=True / stop_reason=end_turn blocks=thinking,text |
| `messages/thinking_tool_emit_build_plans.json` | fable-line, tool_choice auto, strict=False / stop_reason=tool_use blocks=tool_use |
| `messages/thinking_tool_emit_frontrunner_overlay.json` | fable-line, tool_choice auto, strict=False / stop_reason=tool_use blocks=tool_use |
| `messages/thinking_structured.json` | fable-line structured output, ConfigSuggestionsResponse schema / stop_reason=end_turn blocks=thinking,text |
| `messages/text_only_with_tools.json` | tools offered, text-only reply requested / stop_reason=end_turn blocks=text |
| `messages/max_tokens.json` | opus-line, max_tokens 300 on a long answer / stop_reason=max_tokens blocks=thinking |
| `messages/text_instead_of_tool_emit_logic_edits.json` | renamed from the first pass: strict logic tool, auto choice, prompt lacked candidate parameters; model replied in text asking for them (a real no_tool_call) |
| `messages/thinking_tool_emit_logic_edits.json` | fable-line, strict logic tool, auto choice, prompt with candidate parameters / stop_reason=tool_use blocks=tool_use |
| `messages/max_tokens_in_thinking.json` | renamed from the first pass: max_tokens 300 exhausted inside the thinking block (no text block) |
| `messages/max_tokens.json` | opus-line, max_tokens 1200 on a long essay, effort low / stop_reason=max_tokens blocks=thinking,text |

Notes for test authors:
- `models/page_captured.json` holds the full page (14 records, `has_more` false); each record carries
  `line`, `lifecycle`, `deprecated_at`, `retires_at`, `capabilities`, `max_input_tokens`, `max_tokens`.
- `messages/text_instead_of_tool_emit_logic_edits.json` is a real reply without a tool call under `tool_choice` auto:
  the prompt lacked the candidate parameter list the tool contract requires, and the model asked for it.
- `messages/max_tokens_in_thinking.json` stopped inside the thinking block (no text block);
  `messages/max_tokens.json` stopped after partial text.
- Some tool replies carry no thinking block: at effort `low` the model may skip thinking. Do not assume a thinking block precedes `tool_use`.
- Captured with these exact prompts (see `.claude/review` capture scripts in the PM session for the full text):
  swap: replace TLT with IEF for diversify; logic: lengthen an RSI window from 10 to 14 given two candidate parameters;
  build plans: one equal-weight SPY/TLT plan; overlay: RSI(SPY,10) > 79 rotates into VIXY; structured: empty suggestions list.

## Schema-derived model-list and error fixtures (authored 2026-10-22 by the model-resolution test author)

Not captured: each is derived from the captured `models/page_captured.json` records (same keys, same real ids and
dates) and checked by `schema_check.py`, a validator written from the public Models API list reference and the Errors
reference (not from the code under test, not from the SDK types). `capabilities` and the token limits are omitted from
derived records because nothing reads them. Each file passes `schema_check.validate_directory()`; the test file
`tests/model_config/test_fixture_schemas.py` runs it and proves the validator rejects planted violations.

| File | Case (one record per case the resolver distinguishes) |
|---|---|
| `models/paged.json` | two pages, `has_more` true on page one; the newest fable-line record only on page two |
| `models/relisted_newer_fable.json` | the same real ids with `claude-fable-5` dated after `claude-fable-5-1`: a re-listing whose newest fable-line model differs |
| `models/relisted_newer_opus.json` | likewise for the opus line (`claude-opus-5` dated after `claude-opus-5-5`) |
| `models/edge_cases.json` | one page per case under `cases`, with `expect` naming the record id each scenario requires: `deprecated_newest`, `retired_newest` (newest fable-line record not active), `null_line_fable_id` (newest record has fable in its id and a null `line`), `other_line_fable_id` (fable in its id, `line` opus), `epoch_created_at` (epoch release date, listed first), `equal_created_at` (two fable-line records, equal dates) |
| `models/no_line.json` | no record carries `line`; `legacy-claude-fable-5-1` is a derived label (the real id behind a prefix), the only id in any derived file that is not a captured model id, because the case needs an id containing `fable` without the `claude-fable-` prefix |
| `models/no_lifecycle.json` | `line` present on every record, no `lifecycle`, `deprecated_at` or `retires_at` key |
| `models/no_fable.json` | opus-line records only |
| `errors/401_authentication.json`, `403_permission.json`, `429_rate_limit.json`, `500_api_error.json`, `529_overloaded.json` | one body per status, wrapped as `{"status", "body"}` like the captured error files; `request_id` values are in the real `req_` format; message text follows the Errors reference |
| `errors/503_unavailable.json` | 503 is not a documented Messages API status; a gateway answering it carries the generic server-side `api_error` type |

Deviation from the approach's fixture table: `edge_cases.json` holds one page per case (under `cases`) instead of one page
with four records, because every case reuses the same two real fable-line ids in different roles and ids are unique
within a page.

## Schema-derived message fixtures (authored 2026-10-22 by the request-helper test author)

Refusals and fallback-served replies cannot be triggered on demand, so these six files are derived, not captured.
Each is built from the captured replies (same real model ids, `usage` shape, thinking block with its signature, text
block and tool_use block, copied verbatim) and wrapped in the shapes the Claude API reference gives for them, as
relayed to the author by the PM from the Claude API reference (current docs; the stop-reasons and server-side
fallback pages named in the model-unpin plan, Known fact 4):

- Refusal: HTTP 200, `stop_reason: "refusal"`, `stop_details` an object with `type` `"refusal"`, `category` (an open set,
  or null) and `explanation` (string or null); `stop_details` is null for every other `stop_reason`.
- Fallback: a content block `{"type": "fallback", "from": {"model": <declining model>}, "to": {"model": <serving model>}}`
  placed before the serving model's own blocks; the top-level `model` names the serving model; `usage.iterations`
  gains an entry of type `fallback_message` for the serving model's iteration.

`schema_check.check_message_file` encodes those rules and `schema_check.validate_messages_directory()` runs it over
every file under `messages/` (the captured replies pass it too). `tests/llm_request/test_message_fixture_schemas.py`
proves the validator rejects planted violations.

| File | Case (one record per case the request helper distinguishes) |
|---|---|
| `messages/refusal_reasoning_extraction.json` | fable-line refusal, empty content, `category` `reasoning_extraction` (the category server-side fallback does not retry) |
| `messages/refusal_null_category.json` | fable-line refusal, empty content, `category` null |
| `messages/refusal_after_partial_text.json` | opus-line refusal after a thinking block and the first words of a text block |
| `messages/refusal_after_partial_tool.json` | fable-line refusal after a `tool_use` block for `emit_swap_candidates` whose input is a complete candidate list |
| `messages/fallback_served_text.json` | fallback block, thinking, then text; top-level `model` is the fable-line id, so an opus-line request is answered by a different model |
| `messages/fallback_served_tool.json` | fallback block, thinking, then `tool_use`; top-level `model` is the opus-line id, so a fable-line request is answered by a different model |

The `explanation` of every derived refusal is null because nothing under test reads it. Derived reply ids are
`msg_`/`toolu_` strings of the real format and length class, not captured ids.
