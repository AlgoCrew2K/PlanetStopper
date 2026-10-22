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
