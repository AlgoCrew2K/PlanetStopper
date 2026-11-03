# Approach: Unpin every Claude model version
Spec: feature-plans/model-unpin.md
Ref: PlanetStopper@37c8e983b64c6980c28199a3f9318486b60f3864

## Architecture

Every `file:line` below is at the Ref commit, read with `git show 37c8e983:<path>`. SDK facts are read from the installed anthropic 0.85.0 (`requirements.txt:1`, `anthropic~=0.85.0`), the version the droplet runs.

### Shape

Two shared modules carry every rule of AC-2, AC-4, AC-5, AC-8 and AC-9. The eight Messages API sites call one request helper instead of the SDK directly; the render, the audit rows and the no-call provenance read a non-blocking view of the same registry; the council changes one argument. No route gains or loses a key, no schema changes, and no file on the 1-minute engine path changes.

| Layer | File | What it owns |
|---|---|---|
| Model resolution | `model_config.py` (extended, `model_config.py:1-38`) | knobs, overrides, Models API listing, newest-per-family selection, run pin, non-blocking peek |
| Request and reply | `llm_request.py` (new, top level beside `model_config.py`) | effort, fallback setting, strict tools, `tool_choice` auto, reply classification into reason codes, 404 and 400 handling, block-type reads |
| Sites | `ai_advisor.py:1871-1973`, `advisors/advisor_chat.py:383-424`, `advisors/asset_swap_engine.py:1085-1224`, `advisors/logic_change_engine.py:825-943`, `advisors/build_plan_generator.py:1051-1192`, `advisors/frontrunner_builder.py:898-1032`, `advisors/retirement_explainer.py:101-148`, `advisors/lens_pipeline.py:248-316` | build messages and tools as at the Ref, call `llm_request.send`, map the reply onto their existing degraded channel |
| Render and audit | `app.py:7165-7174`, `app.py:8705`, `app.py:8738`, `app.py:7260-7265`, `app.py:7618-7623`, `app.py:8082-8095` | badge text, audit `model_id`, route default provenance, Strategy Builder notice |
| Council | `prism_scheduler.py:258-269` | `--model opus` |

### `model_config.py`: the single model authority (adopted, extended)

The module exists for this purpose: its docstring (`model_config.py:1-19`) makes it the zero-dependency routing module any advisor may import, and it already owns `ADVISOR_SUGGESTION_MODEL` (`model_config.py:25-38`). The synthesis knob moves here from `ai_advisor.resolve_advisor_model()` (`ai_advisor.py:114-120`), so both knobs live in one place. The module still imports no anthropic code: every function that lists models takes the caller's SDK client, which keeps it importable by anything and keeps it inside the Retirement Recommender's LLM ban, which already names `model_config` (`tests/app/test_retirement_producer_explainer_wiring.py:53-60`).

- `Knob`: `SUGGESTION` (env `ADVISOR_SUGGESTION_MODEL`, family `fable`) and `SYNTHESIS` (env `ADVISOR_SYNTHESIS_MODEL`, family `opus`). Site to knob follows the AC-5 knob table; family words are bare words, never versioned IDs.
- `ModelChoice` (frozen dataclass): `model_id` (`""` when unavailable), `display_name`, `source` (`override`, `resolved` or `unavailable`), `env_var`; property `badge_text` returns the raw override, the Models API `display_name`, or `MODEL_UNAVAILABLE_TEXT = "model unavailable"` (Q-7).
- `resolve(knob, client) -> ModelChoice`, in this order:
  1. Override: the env value after `strip()`; empty or spaces only counts as absent (S-5.1 to S-5.3). An override is returned verbatim and never listed or validated (S-5.6, S-5.7); each knob reads only its own variable (S-5.8).
  2. Run pin: inside `pinned_run()`, a family resolved earlier in the block is returned as is (S-2.21).
  3. Cache: the family's entry when the last successful listing is younger than `_RELIST_INTERVAL_SECONDS = 24 * 60 * 60` (Q-3, S-2.10).
  4. Single-flight listing under `_LISTING_LOCK` with an attempt sequence number: a caller that waited while another caller finished an attempt reuses that attempt's outcome instead of listing again (S-2.17, S-4.13). The listing is `client.models.list(limit=_MODELS_PAGE_LIMIT, timeout=_MODELS_LIST_TIMEOUT_SECONDS)` iterated across every page (the SDK's `SyncPage` follows `has_more` with `after_id`, `resources/models.py:89-140`) (S-2.14).
  5. On a failed listing: one WARNING record with `type(exc).__name__` only (S-2.18); the last good entry is kept for the process lifetime and the next call attempts again (S-2.19); a family never resolved returns `unavailable` (AC-4).
- Selection `_select_newest(records, family)`: line mode when any listed record carries a `line` key, family taken from `line` (D-3, S-2.11, S-2.12); otherwise the ID prefix `claude-<family>-` (Q-13, S-2.25). A record is eligible when its `lifecycle` key is absent or `active` (S-2.13, S-2.26). Newest by `created_at`, an epoch `created_at` ranked below every dated record (S-2.15), ties keep the first listed (S-2.16). `line` and `lifecycle` are read from the SDK object's `model_extra`, because `ModelInfo` declares only `id`, `created_at`, `display_name` and `type` (anthropic 0.85.0 `types/model_info.py`).
- `peek(knob) -> ModelChoice`: override, else the cached entry, else unavailable. It never lists and never takes `_LISTING_LOCK`, so the page render and the audit write never wait on the network (Q-8, S-6.7, S-6.12).
- `invalidate(knob, model_id, client) -> ModelChoice`: drops a resolution the Messages API answered 404, lists once (shared with concurrent callers through the same sequence number), and returns the new choice; inside a pin it replaces the pinned entry (S-2.22, Q-14).
- `pinned_run()`: a context manager over a `contextvars.ContextVar`; nested blocks reuse the outer pin.
- `_reset_registry()`: test-only reset used by `tests/conftest.py`.
- Constants with a source comment each: `_RELIST_INTERVAL_SECONDS` (Q-3), `_MODELS_PAGE_LIMIT = 100` (the SDK documents 1 to 1000, `resources/models.py:118`), `_MODELS_LIST_TIMEOUT_SECONDS = 10.0` (bounds how long the single-flight lock is held).

### `llm_request.py`: request settings and reply classification (new)

Search for an existing shared request helper: `git grep -nE "messages\.(create|parse)" 37c8e983 -- '*.py' ':!tests'` returns only the eight sites, each calling the SDK directly; `git grep -nE "fallbacks|output_config|additionalProperties|\"strict\"" 37c8e983 -- '*.py' ':!tests'` returns nothing. No helper exists, and putting AC-8 and AC-9 in each site would be the DC-1 defect the plan names, so one helper owns them.

- `send(client, knob, *, effort, max_tokens, messages, system=None, tools=None, output_schema=None, timeout=None) -> Reply`:
  - Resolves through `model_config.resolve`; an unavailable choice returns `Reply(reason="model_unavailable")` and sends nothing (S-4.12).
  - Sends `client.messages.create(model=..., max_tokens=..., messages=..., system=..., output_config={"effort": effort} plus "format" only when output_schema is given, tools=tools as the site passes them (the asset swap and logic change sites pass `strict_tool(...)`, the build-plan and overlay sites pass their Ref definitions unchanged), tool_choice={"type": "auto"} only when tools are given, extra_body={"fallbacks": "default"}, extra_headers={"anthropic-beta": FALLBACK_BETA}, timeout=...)`. It never sends `temperature`, `top_p`, `top_k`, `thinking` or a trailing assistant message (S-9.10). `messages` and `system` pass through unchanged, so cache breakpoints and prompt text stay byte-identical (S-2.20, S-7.11). The standard namespace carries both fallback values through `extra_body` and `extra_headers` (Known fact 5; `resources/messages/messages.py:972-974`), so no SDK change is needed (Q-11 keeps `requirements.txt` as is).
  - `anthropic.NotFoundError`: override knob returns `override_model_not_found` with the variable name and no retry (S-5.5); resolved knob calls `invalidate` and resends once only when the new ID differs, else returns `model_unavailable` (S-2.22).
  - `anthropic.BadRequestError`: override knob returns `override_request_rejected` with the variable name and no retry (S-5.18); resolved knob re-raises to the site's existing D-1 handler, which shows the exception type only (S-9.17). Every other exception re-raises unchanged into the existing D-1 handlers.
  - Classification: `stop_reason == "refusal"` returns `refusal` with `stop_details.category` when it is not null, and drops all content (S-8.12); `max_tokens` returns `max_tokens` and drops all content; tools given and no `tool_use` block returns `no_tool_call`.
- `Reply`: `model` is the reply's top-level `model` when it is a non-empty string, else the requested ID (legacy test doubles return non-string attributes; D-4, S-6.4); `text()` joins only blocks whose `type == "text"`; `tool_input()` returns the first `tool_use` block's input; `reason_text()` renders `refusal: <category>`, `<code>: <ENV_VAR>` or the bare code.
- Evidence for the block-type read (S-9.9): on the installed SDK, `construct_type(type_=Message, value=...)` with content `[fallback, thinking, text]` builds a `TextBlock` whose `.type` is `"fallback"` and `.text` is `None`, then a `ThinkingBlock`, then the `TextBlock`; `stop_details` survives as an extra dict (architect probe at the Ref). Selecting by `hasattr(block, "text")`, as `advisors/advisor_chat.py:404-409` and `advisors/retirement_explainer.py:135-140` do, admits the fallback block; selecting by `type == "text"` does not.
- `strict_tool(tool)`: deep copy, `"strict": True`, `"additionalProperties": False` on every node whose `type` is `object`; names, types, enums and descriptions are untouched (S-7.14; the build-plan and overlay tools are S-7.15 and plan D-5). It is applied to the two closed schemas only: `_EMIT_SWAP_CANDIDATES_TOOL` (`advisors/asset_swap_engine.py:990-1021`, object nodes at `:1003` and `:1009`) and `_EMIT_LOGIC_EDITS_TOOL` (`advisors/logic_change_engine.py:725-760`, object nodes at `:734` and `:740`). The build-plan tool (`advisors/build_plan_generator.py:598`) and the overlay tool (`advisors/frontrunner_builder.py:622`) stay non-strict, because their open `rhs` (`advisors/build_plan_generator.py:681`, `advisors/frontrunner_builder.py:663`) and the overlay `condition` (`advisors/frontrunner_builder.py:647-668`) carry fields the schema does not list and the compiler reads (`advisors/plan_tree_compiler.py:316-327`); the compiler stays their validator. The SDK's own `anthropic.transform_schema` (`lib/_parse/_transform.py:54`) is not adopted for tools, because it moves `enum` and other keys into the description and fails on the type list at `advisors/logic_change_engine.py:744`, changing property types that S-7.14 freezes.
- Constants: the six reason codes; `FALLBACK_BETA = "server-side-fallback-2026-07-01"` (Known fact 4); `NO_TOOL_CALL_ATTEMPTS = 3` (Q-9) for the two sites that had one attempt at the Ref.

### Per-site request settings

| Site | Call at Ref | Knob | effort | max_tokens (Ref to new) | timeout s (Ref to new) | Reply read | Attempts |
|---|---|---|---|---|---|---|---|
| Suggestions | `ai_advisor.py:1907` | SUGGESTION | high | 2048 (`ai_advisor.py:60`) to 16000 | 30 (`ai_advisor.py:62`) to 120 | `Reply.text()` then `ConfigSuggestionsResponse.model_validate_json` | 1 |
| Chat | `advisors/advisor_chat.py:384` | SYNTHESIS | high | 1024 (`advisors/advisor_chat.py:211`) to 8000 | 30 (`:214`) to 120 | `Reply.text()` | 1 |
| Asset swaps | `advisors/asset_swap_engine.py:1161` | SUGGESTION | high | 2048 (`:313`) to 16000 | 30 (`:316`) to 120 | `Reply.tool_input()` | 3 on `no_tool_call` |
| Logic changes | `advisors/logic_change_engine.py:880` | SUGGESTION | high | 2048 (`:720`) to 16000 | 30 (`:723`) to 120 | `Reply.tool_input()` | 3 on `no_tool_call` |
| Build plans | `advisors/build_plan_generator.py:1115` | SUGGESTION | high | 16384 (`:113`) to 21000 | none (unchanged) | `Reply.tool_input()` | 3 shared by `max_tokens` and `no_tool_call` (`:117`) |
| Overlay | `advisors/frontrunner_builder.py:947` | SUGGESTION | high | 8192 (`:90`) to 16000 | none (unchanged) | `Reply.tool_input()` | 3 shared, as at the Ref (`:94`) |
| Explainer | `advisors/retirement_explainer.py:122` | SUGGESTION | high | 512 (`:50`) to 4000 | 30 (`:54`) to 120 | `Reply.text()` | 1 |
| Lens synthesis | `advisors/lens_pipeline.py:288` | SYNTHESIS | high | 256 (`:290`) to 8000 | none (unchanged) | `Reply.text()` | 1 |

Effort (PM ruling on Q-4, value per site with the reason):
- Suggestions, asset swaps, logic changes, build plans and the overlay are generation, and the lens synthesis is synthesis: all reasoning-heavy, so `high`. For the Fable sites `high` is Fable 5.1's own default (Known fact 4), so nothing drops; for the lens synthesis it is above Opus 5.5's `medium` default.
- Chat and the explainer are conversational, and they are still `high`. The Ref sent no effort, so the Ref ran at each older model's default, and Q-4 records that the newer models default lower than those (Opus 5.5 `medium`). `medium` at chat would be the silent drop the PM ruled out. The conversational difference is carried by the smaller output budgets (8000 and 4000), not by effort. Moving either site to `medium` is a one-constant change for the NB-17 sweep.
- Council: no effort argument. It is the CLI path (`prism_scheduler.py:258-269`), the Ref passes none, S-3.2 freezes the other arguments, and AC-9 covers only the eight Messages API sites. The CLI's own default applies.

Output budgets: thinking counts against `max_tokens` on these models (Known fact 4), so the Ref budgets cannot hold a thinking reply at `high` (the lens synthesis had 256). Known-Unconfirmed 4 leaves budget values to Gate 2; the approach treats them as AC-9 request settings, which S-2.20 excepts (Items Raised 3). Sites without an explicit timeout stay at or below 21,333 tokens, because anthropic 0.85.0 raises `ValueError` for a non-streaming request whose `max_tokens` implies more than 10 minutes when no timeout is passed (`_base_client.py:726-735`, called at `resources/messages/messages.py:977-980`). Timeouts rise from 30 s to 120 s per attempt at the five sites that set one, because a thinking reply at `high` is unlikely to finish inside 30 s (PM ruling on Items Raised 2). With Q-9's three attempts, the worst-case wait per asset swap or logic change click is about 6 minutes (3 times 120 s, inline in the request thread, Known fact 9), replacing the plan's "about 90 s" under Breaking Changes; the PM accepted this.

Strict tools by site (PM ruling on Items Raised 1): asset swaps and logic changes send `strict_tool(...)`; build plans and the overlay send their Ref tool definitions unchanged. All four send `tool_choice` auto.

### Site changes (summary; per-cycle detail below)

- Suggestions move from `messages.parse` to `send(..., output_schema=anthropic.transform_schema(ConfigSuggestionsResponse))` (the public export at anthropic 0.85.0 `__init__.py:47`, the same transform `parse` applied at the Ref) and validate `Reply.text()` themselves. `parse` validates every text block inside the SDK (`lib/_parse/_response.py:16-19`, `validate_json`) and raises on a refusal's or a truncation's partial JSON before `stop_reason` can be read, which would lose the `refusal` and `max_tokens` codes (S-8.11, S-8.12, S-9.14). This also settles Known-Unconfirmed 1 on the wire: `parse` already merged `output_format` into `output_config.format` (`resources/messages/messages.py:1221-1229`), and the new path sends `output_config.format` directly.
- The four tool prompts already instruct the model to call the tool by name (`advisors/asset_swap_engine.py:1079`, `advisors/logic_change_engine.py:819`, `advisors/build_plan_generator.py:1037`, `advisors/frontrunner_builder.py:831`), so the AC-7 instruction is in place and no prompt text changes, which keeps the build-plan golden prompt and both cache breakpoints (`advisors/build_plan_generator.py:1100-1102`, `advisors/frontrunner_builder.py:940-942`) byte-identical.
- Asset swaps and logic changes keep the list-returning public generators (`advisors/asset_swap_engine.py:1085`, `advisors/logic_change_engine.py:825`, used by existing tests) as wrappers over new private functions returning `(items, Reply | None)`; the entry points read the reason and the reply model from them. Empty results whose reply carries a reason produce `message` `no swap was generated this run (<reason_text>)` or `no logic change was generated this run (<reason_text>)`; a gate with no survivors keeps `NO_SURVIVORS_MESSAGE` (`advisors/asset_swap_engine.py:93`, `advisors/logic_change_engine.py:91`).
- Strategy Builder: `GeneratorResult` (`advisors/build_plan_generator.py:135-148`) gains `model: str = ""`; the reason `NoToolUseBlock` (`:1144`) becomes `no_tool_call`; `_generate_candidate_trees` (`advisors/strategy_builder_engine.py:368`) keeps its list return as a wrapper over a new private function that also returns the reason and the reply model; `ProposalRun` (`advisors/strategy_builder_engine.py:132-165`) gains `generation_reason: str | None = None` (a dataclass field, not a route key). The route's `mode_notice` (`app.py:8090-8095`) becomes `Built-new generation (<badge_text>) produced 0 plans (<reason_text>) (degraded): this run surfaces N Atlas community candidate(s) only.` with the parenthesised reason omitted when there is none, so a Fable call is never labelled Opus (S-6.13).
- Overlay: `FABLE_MODEL` (`advisors/frontrunner_builder.py:84`) is deleted and the overlay uses the SUGGESTION knob (Q-5). A refusal ends the loop at once (S-8.14); `last_reason` holds the reason code. The generation failure log at `advisors/frontrunner_builder.py:1982-1986` moves from INFO to WARNING with the reason only, because the daemon emits module-logger records only at WARNING and above (`app.py:482-486` attaches the handler to the `alphabot` logger alone) (S-4.6, S-7.8).
- Explainer and lens synthesis log or render the reason code through their existing channels (`advisors/retirement_explainer.py:130`, `advisors/lens_pipeline.py:312-316`); the lens site reads the text block instead of `content[0]` (`advisors/lens_pipeline.py:293`, S-9.1).

### Provenance, render and audit (AC-6)

- Engines mint `generation_model` from `model_config.peek(Knob.SUGGESTION).model_id` where they read the accessor at the Ref (`advisors/asset_swap_engine.py:1436`, `:1600`; `advisors/logic_change_engine.py:1043`, `:1284`; `advisors/strategy_builder_engine.py:978`) and overwrite it with `Reply.model` once a request returned a reply. The routes' default provenance (`app.py:7260-7265`, `app.py:7618-7623`) uses the same peek. Keys and types stay as they are (NB-12).
- The page: `app.py:7173-7174` becomes `model_config.peek(Knob.SUGGESTION).badge_text` and `model_config.peek(Knob.SYNTHESIS).badge_text`; `_MODEL_DISPLAY_NAMES` and `_humanize_model_name` (`app.py:6520-6540`) are deleted. The template (`templates/ai_advisor.html:1248`, `:1258`, `:1268`, `:1278`, `:1318`, `:2188`) is unchanged and keeps its `| e` escaping (S-6.9).
- Startup warm-up (S-6.14): `_model_warmup_worker()` and `_start_model_warmup()` follow the scheduler's two-function wrapper (`app.py:912-940`, `_lens_pipeline_worker` and `_run_lens_pipeline`): a daemon thread lazy-imports `ai_advisor` and `model_config`, builds the client with `ai_advisor._build_client()` (`ai_advisor.py:1828-1849`), resolves both knobs, catches everything and logs the type only. `__main__` starts it after the scheduler thread (`app.py:9016`).
- Audit rows: `app.py:8705` and `app.py:8738` write `model_config.peek(Knob.SUGGESTION).model_id` (the override verbatim, the resolved ID, or `""`). `database.record_llm_suggestion` (`database.py:980`) is the only writer (`database.py:1003`), and the column already takes `""` (`migrations/003_llm_suggestions.sql:45`).
- JavaScript: the three provenance lines (`static/ai_advisor_asset_swaps.js:336`, `static/ai_advisor_logic_changes.js:342`, `static/ai_advisor.js:893`) render `model unavailable` for an empty `generation_model`; the Asset Swaps empty-state title (`static/ai_advisor_asset_swaps.js:377`) reads `No swap was generated this run` when `data.message` contains one of the six reason codes (Q-7, S-4.16), via a reason-code list constant in that file. The logic-changes title is already neutral (`static/ai_advisor_logic_changes.js:364`) and does not change.

### Council (AC-3)

`prism_scheduler.py:263` becomes `"opus"`. The environment strip (`:271-274`), budget, retry and idempotency stay. Per the PM ruling on Q-10, HC-3 (`tests/ai_advisor/test_prism_scheduling.py:1132-1190`) is rewritten to require the generic tag and forbid any versioned ID, and the doc-writer's `DECISIONS.md` entry records that DE-PRISM-COUNCIL is superseded on this point.

### Test infrastructure (new)

- `tests/_anthropic_http_fake.py`: a real `anthropic.Anthropic(api_key=<fake>, base_url=<test host>, http_client=httpx.Client(transport=httpx.MockTransport(handler)), max_retries=0)` that serves `/v1/models` pages (honouring `after_id`) and queued `/v1/messages` replies or error bodies, and records every request's JSON body and headers. Tests patch each site's existing client seam (`ai_advisor._build_client`, `advisors.asset_swap_engine._build_client`, `advisors.logic_change_engine._build_client`, `advisors.build_plan_generator._build_client`, `advisors.frontrunner_builder._build_client`) to return it, so S-8.13 and S-9.10 are asserted on the outgoing HTTP request (plan Definition of Done). Search: `git grep -lE "MockTransport|respx|httpx\.Client" 37c8e983 -- tests/` returns nothing.
- `tests/conftest.py`: an autouse fixture resets the registry and resolves both knobs through the real `model_config.resolve` against the captured list page served by the fake, so the 284 existing `_build_client` references in `tests/` (`git grep -c _build_client 37c8e983 -- tests/`), whose `MagicMock` clients list zero models (`iter(MagicMock())` is empty), keep sending a model; an `unresolved_models` fixture opts a test out. A live-Anthropic guard in `pytest_configure` (`tests/conftest.py:86`) sets `ANTHROPIC_BASE_URL` to an unroutable local address when the marker expression excludes `live` (`pyproject.toml:85`), mirroring the session Mongo guard (`tests/conftest.py:241-262`), so a test that forgets the seam can never list or call the real API while the opt-in live suites keep their endpoint.
- Fixtures under `tests/fixtures/anthropic/`, one record per case, with `PROVENANCE.md` naming the case each record covers, and `schema_check.py`, a validator written from the public Models API, errors and refusals-and-fallback references (never from the code under test and never from the SDK types, which accept a fallback block as a text block):

| Id | File | Provenance | Cases (one record each) |
|---|---|---|---|
| FX-M1 | `models/page_captured.json` | captured on the droplet (Items Raised 6) | newest active fable-line, older active fable-line, newest active opus-line; page envelope (`has_more`, `first_id`, `last_id`); records which raw keys came back |
| FX-M2 | `models/paged.json` | schema-derived, checked by `schema_check.py` | page one with `has_more` true, page two holding the newest fable-line model |
| FX-M3 | `models/edge_cases.json` | schema-derived, checked | deprecated newest fable-line; fable-in-ID with null `line`; epoch `created_at`; two equal `created_at` |
| FX-M4 | `models/no_line.json` | schema-derived, checked | no `line` key; `claude-fable-` prefixed ID; ID containing fable without the prefix |
| FX-M5 | `models/no_lifecycle.json` | schema-derived, checked | `line` present, no `lifecycle` key |
| FX-M6 | `models/no_fable.json` | schema-derived, checked | active opus-line only |
| FX-E | `errors/404_not_found.json`, `errors/400_invalid_request.json` (captured on the droplet); `errors/401_authentication.json`, `errors/403_permission.json`, `errors/429_rate_limit.json`, `errors/500_api_error.json`, `errors/503_unavailable.json`, `errors/529_overloaded.json` (schema-derived, checked) | as listed | one body per status |
| FX-R1 | `messages/thinking_text.json` | captured | thinking then text, opus-line |
| FX-R2 | `messages/thinking_tool_emit_swap_candidates.json`, `messages/thinking_tool_emit_logic_edits.json`, `messages/thinking_tool_emit_build_plans.json`, `messages/thinking_tool_emit_frontrunner_overlay.json` | captured, one per tool | thinking then `tool_use` under `tool_choice` auto |
| FX-R2S | `messages/thinking_structured.json` | captured | thinking then a structured-output text block for the suggestion schema |
| FX-R3 | `messages/text_only_with_tools.json` | captured | tools offered, text-only reply |
| FX-R4 | `messages/max_tokens.json` | captured | `stop_reason` `max_tokens` after partial text |
| FX-R5 | `messages/refusal_reasoning_extraction.json`, `messages/refusal_null_category.json`, `messages/refusal_after_partial_text.json`, `messages/refusal_after_partial_tool.json` | schema-derived from the refusals reference, checked | one record each, as named |
| FX-R6 | `messages/fallback_served_text.json`, `messages/fallback_served_tool.json` | schema-derived from the fallback reference, checked | `fallback` block, thinking, then text or `tool_use`, top-level `model` naming the fallback model |

Exact files and shapes for the PM's capture on `model-unpin/fixtures` (Items Raised 6). Every file is the raw JSON body the API returned (or would return), never an SDK object dump, trimmed to the records listed; `PROVENANCE.md` gives per file the date, source (captured, or schema-derived with the reference URL), the case each record covers, and any single field edited. The schema-derived files are authored by the C1 (models, errors) and C2 (messages) test-writers after the validator, not by the PM.

| File | Shape |
|---|---|
| `models/page_captured.json` (FX-M1) | `{"data": [3 ModelInfo records], "has_more": <as returned>, "first_id": ..., "last_id": ...}`; records: newest active fable-line, an older active fable-line, newest active opus-line, each with every raw key the API returned (`id`, `display_name`, `created_at`, `type`, plus `line` and `lifecycle` if present) |
| `models/paged.json` (FX-M2) | `{"pages": [page1, page2]}`, each page in FX-M1's envelope; page1 `has_more` true |
| `models/edge_cases.json` (FX-M3) | one FX-M1-envelope page holding the four case records |
| `models/no_line.json`, `models/no_lifecycle.json`, `models/no_fable.json` (FX-M4 to FX-M6) | one FX-M1-envelope page each |
| `errors/<status>_<type>.json` (FX-E) | `{"type": "error", "error": {"type": ..., "message": ...}}`; the captured 404 and 400 keep the API's own message text and `request-id` header value noted in `PROVENANCE.md` |
| `messages/*.json` (FX-R1 to FX-R6) | one full Messages response body: `id`, `type`, `role`, `model`, `content` (every block, thinking blocks with their signatures), `stop_reason`, `stop_details` when returned, `usage`; captured files also record in `PROVENANCE.md` the request body sent (model, effort, max_tokens, tools, prompt) |

Model IDs in schema-derived records reuse the real IDs captured in FX-M1, never invented ones; tests read IDs from the fixtures, never from literals.

- Node render harness (new, S-4.16): a test that runs the real `static/ai_advisor_asset_swaps.js` under `node` in a `vm` context with minimal `document`, `fetch` and element stubs, fires `DOMContentLoaded` and the evaluate click (`static/ai_advisor_asset_swaps.js:411-488`), and reads the results area's HTML. Search: the existing JS tests read the file as text because no JS runner exists (`tests/ai_advisor/test_as_live_generation_provenance_render.py:34-40`); `node` is already required by `tests/js_syntax/test_js_syntax.py:31`, whose skip rule this harness copies.

### Unchanged by design

No migration and no new table (`llm_suggestions.model_id` is `TEXT NOT NULL DEFAULT ''`, `database.py:232`). No `bot_state` writer. No change to `_SETTINGS_WRITE_ALLOWLIST` (`app.py:6118-6124`) or the auth and CSRF hooks. Advisor imports in `app.py` stay inside functions. `alpha_bot_execution.py` and `math_engine.py` carry zero diff, guarded by a new blast-radius test modeled on `tests/advisors/test_incubation_blast_radius.py:62`. `requirements.txt` is unchanged (PM ruling on Q-11). `.claude/agents/prism-*.md` keep `model: opus` at line 5.

## Criterion Thread

| AC | R items | Design element (file:line, or "new: <reason and search>") | Test seam (test file:line; fixture and provenance; harness pattern) | Cycle |
|---|---|---|---|---|
| AC-1 | R-1, R-2, R-19, R-25, R-26 | new: `tests/security/test_no_versioned_model_ids.py`; no repository scan for model IDs exists (`git grep -lE "claude-(opus\|fable\|sonnet\|haiku\|mythos)" 37c8e983 -- tests/security/` returns nothing); literals removed at `model_config.py:26`, `:34`, `ai_advisor.py:118`, `:120`, `advisors/advisor_chat.py:385`, `advisors/lens_pipeline.py:289`, `advisors/frontrunner_builder.py:7`, `:84`, `prism_scheduler.py:263`, `app.py:6527-6534`, `.env.example:129-130` | new scan over `git ls-files` of the checkout plus planted files in `tmp_path` with the same relative layout; REPO_ROOT pattern of `tests/security/test_gitignore_defense.py:37`; no fixture beyond planted strings of each ID shape | C12 |
| AC-2 | R-1, R-3, R-5, R-6, R-8, R-9, R-11, R-12, R-18, R-19, R-24, R-30, R-33 | `model_config.py:25-38` extended with `resolve`, `peek`, `invalidate`, `pinned_run`; new `llm_request.send` (search above); sites `ai_advisor.py:1907`, `advisors/advisor_chat.py:384`, `advisors/asset_swap_engine.py:1161`, `advisors/logic_change_engine.py:880`, `advisors/build_plan_generator.py:1115`, `advisors/frontrunner_builder.py:947`, `advisors/retirement_explainer.py:122`, `advisors/lens_pipeline.py:288` | new `tests/model_config/test_model_resolution.py`; FX-M1 captured, FX-M2 to FX-M6 schema-derived; `tests/_anthropic_http_fake.py`; site rows extend each site suite (C3 to C8), the weekly run extends `tests/advisors/test_weekly_suggestions_orchestrator.py:70` | C1 |
| AC-3 | R-1, R-3, R-4, R-7 | `prism_scheduler.py:263` | `tests/ai_advisor/test_prism_scheduling.py:1132` (HC-3 rewritten), `:166`, `:96`, `:650`, `:1429`; `tests/prism_scheduler/test_council_sub_auth.py:46`; `subprocess.run` captured as at `tests/ai_advisor/test_prism_scheduling.py:1148-1150` | C10 |
| AC-4 | R-6, R-8, R-9, R-13 | `model_config.resolve` returns `unavailable`; `llm_request.send` returns `model_unavailable` without a request; channels at `ai_advisor.py:1914-1928`, `advisors/advisor_chat.py:391-399`, `advisors/asset_swap_engine.py:1490-1497`, `advisors/logic_change_engine.py:1103-1110`, `app.py:8090-8095`, `advisors/frontrunner_builder.py:1982`, `advisors/retirement_explainer.py:130`, `advisors/lens_pipeline.py:312-316`, `static/ai_advisor_asset_swaps.js:377` | route rows through the Flask client (`tests/ui/conftest.py:60`) with FX-E listing failures served by the HTTP fake and the `unresolved_models` fixture; node render harness for S-4.16; per-site files in C3 to C8 | C2 |
| AC-5 | R-16, R-24 | `model_config.resolve` override branch replacing `model_config.py:38` and `ai_advisor.py:120`; `llm_request.send` 404 and 400 override handling | `tests/model_config/test_model_resolution.py` (new) for S-5.1 to S-5.3, S-5.6 to S-5.8; site override rows in each site suite; FX-E captured 404 and 400 bodies; `tests/security/test_a1_settings_allowlist.py:218` for S-5.9 | C1 |
| AC-6 | R-10, R-22, R-23 | `app.py:7173-7174`, `app.py:6520-6540` (deleted), `app.py:8705`, `app.py:8738`, `app.py:7261`, `app.py:7619`, `advisors/asset_swap_engine.py:1436`, `:1600`, `advisors/logic_change_engine.py:1043`, `:1284`, `advisors/strategy_builder_engine.py:978`, `app.py:8090-8095`, `static/ai_advisor_asset_swaps.js:336`, `static/ai_advisor_logic_changes.js:342`, `static/ai_advisor.js:893`; warm-up new beside `app.py:912-940` (no startup resolution exists: `git grep -n "models.list" 37c8e983` returns nothing) | `tests/ai_advisor/test_r1_attribution_honesty.py:202` (page), `tests/advisors/test_advisor_liveness_routes.py:239` (accept), `tests/database/test_llm_suggestions.py:275`, `tests/ui/test_asset_swap_route_reasoning_provenance.py:156`, `tests/app/test_sb_backtest_unavailable_route.py:80`; FX-M1, FX-R2, FX-R6 | C9 |
| AC-7 | R-6, R-9, R-15, R-20, R-33 | `llm_request.strict_tool` (new; `anthropic.transform_schema` rejected, see Architecture), applied to `advisors/asset_swap_engine.py:990` and `advisors/logic_change_engine.py:725` only, the build-plan and overlay tools staying non-strict per the PM ruling on S-7.14; `tool_choice` changes at `advisors/asset_swap_engine.py:1165`, `advisors/logic_change_engine.py:884`, `advisors/build_plan_generator.py:1119`, `advisors/frontrunner_builder.py:951`; retry loops at the same sites | `tests/advisors/test_asset_swap_engine_reasoned_generation.py:99`, `tests/advisors/test_logic_change_engine_reasoned_generation.py:82`, `tests/advisors/test_build_plan_generator_truncation.py:249`, `tests/advisors/test_frontrunner_builder.py:263`; FX-R2 and FX-R3 captured; cross-site file in C11 | C4 |
| AC-8 | R-6, R-9, R-14 | new: `llm_request.send` fallback body field and beta header, refusal classification, `Reply.model` (no fallback or refusal handling exists: `git grep -nE '"fallbacks"\|stop_reason.*refusal\|"refusal"' 37c8e983 -- '*.py' ':!tests' ':!docs'` returns nothing); refusal stops the loops at `advisors/build_plan_generator.py:1114` and `advisors/frontrunner_builder.py:946` | HTTP fake request recorder; FX-R5 and FX-R6 schema-derived and checked; per-site rows in C3 to C8; cross-site rows in C11 | C2 |
| AC-9 | R-6, R-8, R-17, R-21, R-29, R-33 | new: `llm_request.send` effort and forbidden parameters, `Reply.text()` and `Reply.tool_input()` block-type reads, `max_tokens` classification (no effort setting exists: `git grep -n "output_config" 37c8e983 -- '*.py' ':!tests'` returns nothing); positional and `hasattr` reads replaced at `advisors/lens_pipeline.py:293`, `advisors/advisor_chat.py:404-409`, `advisors/retirement_explainer.py:135-140`; budgets at the per-site settings table's lines | per-site suites in C3 to C8; FX-R1, FX-R2, FX-R2S, FX-R4 captured, FX-R6 schema-derived; S-9.18 on the droplet (Items Raised 4) | C2 |

## Execution Model

One team runs the whole plan, one Toxic Pair per cycle in flight, each pair in its own isolated worktree off its Branch base, never with the main checkout as its working directory, with `.env` copied in and the working directory verified before the first edit (project `.claude/CLAUDE.md` Agent Team Composition and the project's worktree rules). Each cycle is test-writer RED (driven by `/tdd` per the project's skill-driven TDD rule), implementer GREEN (`/tdd-implement`), closer REVISE, then the parallel review. Test runs are scoped to the cycle's suites with at most 4 xdist workers and never `-n auto`; the live and slow markers stay deselected.

Composition by cycle and why:
- C1 and C2 (model resolution, request helper): test-writer, implementer, `qa`, `code-reviewer` (the project's `quant-code-reviewer`), `integrations-expert`, because these heads encode the Anthropic Models and Messages API contract and its fixture-versus-live drift.
- C3, C4, C5, C6, C9 (routes, page and JavaScript): the pair plus `qa`, `code-reviewer`, `flask-dashboard-specialist`; C4, C5, C6 and C9 render UI, so `ux-expert` with `behavioral-evidence-capturer` review them.
- C7, C8 (frontrunner, explainer, lens synthesis), C10 (council), C11 (cross-site sweep and weekly run), C12 (scan and cleanup): the pair plus `qa` and `code-reviewer`.
- One doc-gen (the project's doc-writer) for the slice after every head passes: `docs/generated/` pages, `docs/features/advisor-synthesis-model-config.md`, `README.md`, the `DECISIONS.md` entry recording the unpin and the DE-PRISM-COUNCIL supersession, and the drafted `.claude/CLAUDE.md` Key Files update.

Twelve cycle rows give twelve heads, all changing code; four render UI (C4, C5, C6, C9); `flask-dashboard-specialist` reviews five heads and `integrations-expert` two. Not sensitive (plan header).

Team size: 40 = 12 test-writer + 12 implementer + 3 qa + 3 code-reviewer + 1 ux-expert + 1 behavioral-evidence-capturer + 5 flask-dashboard-specialist + 2 integrations-expert + 1 doc-gen

## Dispatch Ordering

| Cycle | Consumes (cycle: committed artifact) | Branch base |
|---|---|---|
| C1 | | `model-unpin/fixtures`: main@37c8e983 plus the PM's fixture-capture commit (Items Raised 6) |
| C2 | C1: `model_config.py` (`Knob`, `ModelChoice`, `resolve`, `invalidate`, `pinned_run`); C1: `tests/_anthropic_http_fake.py` and the `tests/conftest.py` registry seed | `model-unpin/c1` at its cycle-complete SHA |
| C3 | C2: `llm_request.py` (`send`, `Reply`, reason codes) | `model-unpin/c2` at its cycle-complete SHA |
| C4 | C2: `llm_request.py` (`send`, `Reply`, `NO_TOOL_CALL_ATTEMPTS`, `strict_tool`) | `model-unpin/c2` at its cycle-complete SHA |
| C5 | C2: `llm_request.py` (`send`, `Reply`, `NO_TOOL_CALL_ATTEMPTS`, `strict_tool`) | `model-unpin/c2` at its cycle-complete SHA |
| C6 | C2: `llm_request.py` (`send`, `Reply`) | `model-unpin/c2` at its cycle-complete SHA |
| C7 | C2: `llm_request.py` (`send`, `Reply`) | `model-unpin/c2` at its cycle-complete SHA |
| C8 | C2: `llm_request.py` (`send`, `Reply`) | `model-unpin/c2` at its cycle-complete SHA |
| C9 | C1: `model_config.py` (`peek`, `ModelChoice.badge_text`, `resolve`); C1: `tests/_anthropic_http_fake.py` | `model-unpin/c1` at its cycle-complete SHA |
| C10 | | main@37c8e983 |
| C11 | C3: `ai_advisor.py` and `advisors/advisor_chat.py` calling `send`; C4: `advisors/asset_swap_engine.py` calling `send`; C5: `advisors/logic_change_engine.py` calling `send`; C6: `advisors/build_plan_generator.py` and `advisors/strategy_builder_engine.py` calling `send`; C7: `advisors/frontrunner_builder.py` calling `send`; C8: `advisors/retirement_explainer.py` and `advisors/lens_pipeline.py` calling `send` | `model-unpin/sites`: the PM's merge of the C3 to C8 cycle-complete heads |
| C12 | C3: `ai_advisor.py`, `advisors/advisor_chat.py` and `app.py:8705`, `:8738` free of the old accessors and literals; C4: `app.py:7261` and `advisors/asset_swap_engine.py` free of `get_advisor_suggestion_model`; C5: `app.py:7619` and `advisors/logic_change_engine.py` free of it; C6: `advisors/build_plan_generator.py` and `advisors/strategy_builder_engine.py` free of it; C7: `advisors/frontrunner_builder.py` without `FABLE_MODEL`; C8: `advisors/lens_pipeline.py` and `advisors/retirement_explainer.py` free of literals and the accessor; C9: `app.py` without `_MODEL_DISPLAY_NAMES` and `resolve_advisor_model`; C10: `prism_scheduler.py` passing `opus` | `model-unpin/all`: the PM's merge of the C3 to C10 cycle-complete heads |

C1 and C10 start at once; C9 starts as soon as C1 commits; C3 to C8 start together as soon as C2 commits; C11 and C12 start together once their inputs are merged.

## Per-Cycle Detail

### C1: model resolution (AC-2, AC-4, AC-5)
- Modified: `model_config.py` (module rewritten around `Knob`, `ModelChoice`, `resolve`, `peek`, `invalidate`, `pinned_run`, `_reset_registry`; the old `get_advisor_suggestion_model` at `model_config.py:29-38` stays until C12 so unmigrated sites keep working); `tests/conftest.py` (registry seed fixture and `unresolved_models` fixture beside `_disable_auth_for_tests` at `tests/conftest.py:284`; live-Anthropic guard in `pytest_configure` at `tests/conftest.py:86`).
- Added: `tests/_anthropic_http_fake.py` (Models endpoint and request recorder; C2 adds the Messages endpoint), `tests/fixtures/anthropic/models/` (FX-M1 to FX-M6), `tests/fixtures/anthropic/errors/` (FX-E), `tests/fixtures/anthropic/schema_check.py` and `PROVENANCE.md`, `tests/model_config/__init__.py`, `tests/model_config/test_model_resolution.py`, `tests/model_config/test_fixture_schemas.py`.
- Primitives imported: `threading`, `contextvars`, `time.monotonic` (patched in tests as the clock seam), `logging`; no anthropic import.
- First production use: none in this cycle; first callers are C2 (`send`) and C9 (`peek`).

### C2: request helper (AC-2, AC-4, AC-5, AC-7, AC-8, AC-9)
- Added: `llm_request.py`; `tests/llm_request/__init__.py`, `tests/llm_request/test_send.py`, `tests/llm_request/test_strict_tool.py`, `tests/llm_request/test_reply.py`; `tests/fixtures/anthropic/messages/` (FX-R1 to FX-R6) and their checks in `schema_check.py`; `tests/advisors/test_model_unpin_blast_radius.py` (modeled on `tests/advisors/test_incubation_blast_radius.py:62`: `model_config` and `llm_request` never referenced from `alpha_bot_execution.py` or `math_engine.py`, neither module imports them).
- Modified: `tests/_anthropic_http_fake.py` (Messages endpoint); `tests/security/test_retirement_action_no_trade_boundary.py:77` (`_NEW_MODULE_REL_PATHS` gains `model_config.py` and `llm_request.py`, so Group A at `:298` scans them, S-2.23); `tests/app/test_retirement_producer_explainer_wiring.py:53-60` (`_LLM_FORBIDDEN_MODULES` gains `llm_request`; a plant test beside `:146` covers both modules, S-2.24).
- Primitives imported: `model_config` (C1), `anthropic.NotFoundError` and `anthropic.BadRequestError` (lazy, inside `send`), `copy`.
- Helper-level unit tests written here and relied on by the C11 sweep: effort and forbidden parameters on the body, `fallbacks` and the beta header, `tool_choice` auto, `strict_tool` on the 4 object nodes of the two closed schemas (`advisors/asset_swap_engine.py:1003`, `:1009`; `advisors/logic_change_engine.py:734`, `:740`) with names, types and enums unchanged, refusal and `max_tokens` content discard, block-type reads against FX-R6, `Reply.model` fallback for non-string attributes, override 404 and 400 classification, resolved 400 re-raise.
- First production use: C3 to C8.

### C3: suggestions, chat and the audit rows (AC-2, AC-4, AC-5, AC-6, AC-8, AC-9)
- Modified: `ai_advisor.py:60`, `:62` (budget and timeout), `:1871-1973` (`request_suggestions` on `send` with `output_schema`, validation of `Reply.text()`, reason text in the error string); `advisors/advisor_chat.py:211`, `:214`, `:383-424` (`explain_artifact` on `send`, `CHAT_ERROR_MSG_TEMPLATE` at `:230` carries the reason text, the `os.environ` literal at `:385` is removed); `app.py:8705`, `app.py:8738` (audit `model_id` from `peek`).
- Rewritten consumer tests (DC-4): `tests/advisors/test_r1_fable_suggestion_routing.py`, `tests/ai_advisor/test_sdk_contract.py` (pins `parse` and `parsed_output` at `:111`), `tests/ai_advisor/test_chat_engine.py`, `tests/ai_advisor/test_ai_advisor.py`, `tests/database/test_llm_suggestions.py`.
- First production use: `llm_request.send` from E1 and E2; `model_config.peek` from E12 and E13.

### C4: asset swaps (AC-2, AC-4, AC-5, AC-6, AC-7, AC-8, AC-9)
- Modified: `advisors/asset_swap_engine.py:313`, `:316`, `:1085-1224` (wrapper plus private generator with three attempts on `no_tool_call`, stop on `refusal` or `max_tokens`), `:1431-1513` and `:1591-1639` (provenance from `peek` then `Reply.model`, reason message), the `tool_choice` at `:1165`; `app.py:7260-7265` (default provenance from `peek`); `static/ai_advisor_asset_swaps.js:336`, `:377` (provenance fallback text, empty-state title by reason code).
- Added: node harness test file for S-4.16.
- Rewritten consumer tests: `tests/advisors/test_asset_swap_engine_provenance.py`, `tests/ui/test_asset_swap_route_reasoning_provenance.py`, and the engine suites whose client doubles pin `tool_choice` or reject the new keyword arguments.
- First production use: `strict_tool` and `NO_TOOL_CALL_ATTEMPTS` from E3.

### C5: logic changes (AC-2, AC-4, AC-5, AC-7, AC-8, AC-9)
- Modified: `advisors/logic_change_engine.py:720`, `:723`, `:825-943`, `:1041-1110`, `:1282-1320`, the `tool_choice` at `:884`; `app.py:7618-7623`; `static/ai_advisor_logic_changes.js:342`.
- Rewritten consumer tests: `tests/advisors/test_logic_change_engine_provenance.py`, `tests/ui/test_logic_change_route_reasoning_provenance.py`, and the engine suites with incompatible client doubles.

### C6: Strategy Builder (AC-2, AC-4, AC-5, AC-6, AC-7, AC-8, AC-9)
- Modified: `advisors/build_plan_generator.py:104-117` (budget 21000 with the SDK-guard comment), `:135-148` (`GeneratorResult.model`), `:1051-1192` (`send` in the existing loop; `no_tool_call` shares the three attempts with `max_tokens`; refusal stops; `_EMIT_BUILD_PLANS_TOOL` at `:598` passed non-strict and unchanged, `tool_choice` auto replacing `:1119`); `advisors/strategy_builder_engine.py:132-165` (`ProposalRun.generation_reason`), `:368-419` (wrapper plus private function returning reason and model, failure logged at WARNING instead of DEBUG at `:415`), `:961-1026` (provenance from `peek` then the reply model); `app.py:8082-8095` (`mode_notice`); `static/ai_advisor.js:893`.
- Rewritten consumer tests: `tests/advisors/test_strategy_builder_engine_reasoning_provenance.py`, `tests/app/test_strategy_builder_phase4.py`, the build-plan suites pinning `tool_choice` or the model value.

### C7: Frontrunner Builder overlay (AC-2, AC-4, AC-5, AC-7, AC-8, AC-9)
- Modified: `advisors/frontrunner_builder.py:7` (docstring), `:82-90` (delete `FABLE_MODEL`, budget 16000), `:898-1032` (`send` in the existing loop with the SUGGESTION knob, refusal stops, reason codes; `_EMIT_OVERLAY_TOOL` at `:622` passed non-strict and unchanged, `tool_choice` auto replacing `:951`), `:1982-1986` (WARNING).
- Rewritten consumer tests: `tests/advisors/test_frontrunner_builder.py` (pins `FABLE_MODEL`), `tests/advisors/test_prompt_caching_documented_exclusions.py`.

### C8: retirement explainer and lens synthesis (AC-2, AC-4, AC-5, AC-8, AC-9)
- Modified: `advisors/retirement_explainer.py:50`, `:54`, `:101-148` (`send`, WARNING with reason text, `None` on any reason); `advisors/lens_pipeline.py:248-316` (`send` with SYNTHESIS, `Reply.text()`, rationale `Synthesis unavailable (<reason_text>).` in the shape of `:316`).
- Rewritten consumer tests: `tests/advisors/test_retirement_explainer_generation.py`, `tests/ai_advisor/test_synthesis_model_config.py` (lens part), `tests/ai_advisor/test_cycle4_lens_pipeline.py` doubles that return `content[0].text` only.

### C9: page badges and startup resolution (AC-6)
- Modified: `app.py:6520-6540` (deleted), `app.py:7165-7174` (badge text from `peek`), new `_model_warmup_worker` and `_start_model_warmup` beside `app.py:912-940`, `app.py:9016` (start after the scheduler thread).
- Rewritten consumer tests: `tests/ai_advisor/test_r1_attribution_honesty.py` (uses `_humanize_model_name` and the accessors at `:144`, `:265`).

### C10: council generic tag (AC-3)
- Modified: `prism_scheduler.py:263`; `tests/ai_advisor/test_prism_scheduling.py:1132-1190` (HC-3 rewritten per the PM ruling on Q-10).
- No new primitive.

### C11: weekly run and cross-site sweep (AC-2, AC-4, AC-5, AC-6, AC-7, AC-8, AC-9)
- Modified: `advisors/weekly_suggestions_scheduler.py:412-450` (`run_weekly_suggestions` body inside `model_config.pinned_run()`), `advisors/strategy_builder_scheduler.py:103-207` (`run_weekly_build` likewise, so its own CLI run is pinned), `advisors/frontrunner_builder.py:1298-1332` (`run_frontrunner_build` likewise).
- Added: `tests/advisors/test_model_unpin_cross_site.py`, parametrized over the eight sites through their real entry points with the HTTP fake patched into every client seam.
- Extended: `tests/advisors/test_weekly_suggestions_orchestrator.py:70`.

### C12: source scan and cleanup (AC-1)
- Added: `tests/security/test_no_versioned_model_ids.py` (pattern: `claude-` plus one of opus, sonnet, haiku, fable, mythos plus a hyphen and a digit; file set from `git ls-files`, filtered to the AC-1 rule's trees with `tests/`, `docs/` and `feature-plans/` exempt; read-only).
- Modified: `model_config.py` (delete `_ADVISOR_SUGGESTION_MODEL_DEFAULT` at `:26` and `get_advisor_suggestion_model` at `:29-38`); `ai_advisor.py:114-120` (delete `resolve_advisor_model`); `.env.example:129-130` (comment names no versioned ID: unset means the newest opus-line model); `tests/ai_advisor/test_synthesis_model_config.py:131` (accessor tests replaced by the C1 resolution tests); any remaining test reference found by `git grep -nE "get_advisor_suggestion_model|resolve_advisor_model|FABLE_MODEL|_humanize_model_name|_MODEL_DISPLAY_NAMES" -- tests/`.

## Per-Cycle Test Plan

Write-path census: every route, job and command that reaches a matrix rule. Persisted values this plan changes are `llm_suggestions.model_id` (writers E12 and E13 only, through `database.record_llm_suggestion` at `database.py:980`, the documented only write path at `database.py:1003`), the MARKET_PRISM rationale (writer E8 at `advisors/lens_pipeline.py:421`), the retirement `explanation` (writer E7 at `app.py:1336`, persisted by `advisors/retirement_recommender.py:819`), queued frontrunner proposals (`advisors/frontrunner_builder.py:2101`, reached from E6 and E9) and the advisor observations of E3, E4, E5 and E9 (`advisors/asset_swap_engine.py:775`, `advisors/logic_change_engine.py:509`, `advisors/strategy_builder_engine.py:764`), whose content does not change. Each entry point below has round-1 RED rows in the cycle tables.

| Entry point | Rules |
|---|---|
| E1 POST `/ai-advisor/suggest` (`app.py:8559`) | AC-2, AC-4, AC-5, AC-8, AC-9 |
| E2 POST `/ai-advisor/chat/send` (`app.py:8761`) | AC-2, AC-4, AC-5, AC-8, AC-9 |
| E3 POST `/ai-advisor/asset-swaps/evaluate` (`app.py:7222`) | AC-2, AC-4, AC-5, AC-6, AC-7, AC-8, AC-9 |
| E4 POST `/ai-advisor/logic-changes/evaluate` (`app.py:7578`) | AC-2, AC-4, AC-5, AC-6, AC-7, AC-8, AC-9 |
| E5 POST `/ai-advisor/strategy-builder/run` (`app.py:7897`) | AC-2, AC-4, AC-5, AC-6, AC-7, AC-8, AC-9 |
| E6 POST `/ai-advisor/frontrunner-builder/run` (`app.py:8168`) | AC-2, AC-4, AC-5, AC-7, AC-8, AC-9 |
| E7 retirement worker (`app.py:1168`; 03:45 tick `app.py:1440`; POST `/ai-advisor/retirement/run` `app.py:8395`) | AC-2, AC-4, AC-5, AC-8, AC-9 |
| E8 03:00 lens pipeline (`app.py:912-940`, scheduled at `app.py:1433`) | AC-2, AC-4, AC-5, AC-8, AC-9 |
| E9 `python -m advisors.weekly_suggestions_scheduler` (`advisors/weekly_suggestions_scheduler.py:453`) | AC-2, AC-4, AC-5, AC-7, AC-8, AC-9 |
| E10 `prism_scheduler.py` (`prism_scheduler.py:680`) | AC-3 |
| E11 GET `/ai-advisor` (`app.py:6619`) | AC-6 |
| E12 POST `/ai-advisor/accept` (`app.py:8644`) | AC-6 |
| E13 POST `/ai-advisor/reject` (`app.py:8720`) | AC-6 |
| `python -m advisors.strategy_builder_scheduler` (`advisors/strategy_builder_scheduler.py:210`) | AC-2, AC-4, AC-5, AC-7, AC-8, AC-9 |

Scope per cycle: C1 and C2 are unit tests against the HTTP fake; C3 to C8 add route and job integration through the Flask test client (`tests/ui/conftest.py:60`) or the job function, with the HTTP fake behind each client seam; C4 adds the node render harness; C9 renders the real template; C10 captures the subprocess; C11 runs every site through its real entry point; C12 is a source scan. Screenshots are the capturer's live render of the C4, C5, C6 and C9 heads. Live-backend scope is the PM's merge check only (Merge Criteria).

### C1
| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-2.10 | AC-2 | unit | `model_config.resolve` (SUGGESTION) as reached by a Fable-family call | new: `tests/model_config/test_model_resolution.py`, no resolver exists to extend | FX-M1 captured, plus a second schema-derived page adding a newer fable-line model; patched monotonic clock |
| S-2.11 | AC-2 | unit | `model_config.resolve` for both knobs | new: `tests/model_config/test_model_resolution.py` | FX-M1 captured |
| S-2.12 | AC-2 | unit | `model_config.resolve` (SUGGESTION) | new: `tests/model_config/test_model_resolution.py` | FX-M3 null-`line` record, schema-derived and checked |
| S-2.13 | AC-2 | unit | `model_config.resolve` (SUGGESTION) | new: `tests/model_config/test_model_resolution.py` | FX-M3 deprecated-newest record, schema-derived and checked |
| S-2.14 | AC-2 | unit | `model_config.resolve` (SUGGESTION) | new: `tests/model_config/test_model_resolution.py` | FX-M2, schema-derived and checked |
| S-2.15 | AC-2 | unit | `model_config.resolve` (SUGGESTION) | new: `tests/model_config/test_model_resolution.py` | FX-M3 epoch record, schema-derived and checked |
| S-2.16 | AC-2 | unit | `model_config.resolve` (SUGGESTION), repeated | new: `tests/model_config/test_model_resolution.py` | FX-M3 equal-`created_at` pair, schema-derived and checked |
| S-2.17 | AC-2 | unit (10 threads) | `model_config.resolve` from request, scheduler and frontrunner threads | new: `tests/model_config/test_model_resolution.py` | FX-M1 captured, served after a gate event releases the listing |
| S-2.18 | AC-2 | unit | `model_config.resolve` after a prior success | new: `tests/model_config/test_model_resolution.py` | FX-M1 captured; FX-E 401, 403, 429, 5xx bodies (schema-derived and checked); `httpx.ReadTimeout` and `httpx.ConnectError` raised by the transport |
| S-2.19 | AC-2 | unit | `model_config.resolve` after 24 h | new: `tests/model_config/test_model_resolution.py` | FX-M1 captured; FX-E 500 body; patched clock |
| S-2.25 | AC-2 | unit | `model_config.resolve` (SUGGESTION) | new: `tests/model_config/test_model_resolution.py` | FX-M4, schema-derived and checked |
| S-2.26 | AC-2 | unit | `model_config.resolve` (SUGGESTION) | new: `tests/model_config/test_model_resolution.py` | FX-M5, schema-derived and checked |
| S-4.13 | AC-4 | unit (threads) | `model_config.resolve` while unresolved | new: `tests/model_config/test_model_resolution.py` | FX-E 503 body then FX-M1 captured |
| S-5.1 | AC-5 | unit | `model_config.resolve` (SUGGESTION), env absent | new: `tests/model_config/test_model_resolution.py` | FX-M1 captured |
| S-5.2 | AC-5 | unit | `model_config.resolve` (SUGGESTION), env empty | new: `tests/model_config/test_model_resolution.py` | FX-M1 captured |
| S-5.3 | AC-5 | unit | `model_config.resolve` (SUGGESTION), env spaces | new: `tests/model_config/test_model_resolution.py` | FX-M1 captured |
| S-5.6 | AC-5 | unit | `model_config.resolve` (SUGGESTION) with an opus-line override | new: `tests/model_config/test_model_resolution.py` | FX-M1 captured (override value copied from its opus-line record) |
| S-5.7 | AC-5 | unit | `model_config.resolve` with override, listing failing | new: `tests/model_config/test_model_resolution.py` | FX-E 503 body; request recorder asserts zero listings |
| S-5.8 | AC-5 | unit | `model_config.resolve` (SUGGESTION) with only `ADVISOR_SYNTHESIS_MODEL` set | new: `tests/model_config/test_model_resolution.py` | FX-M1 captured |

### C2
| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-2.22 | AC-2 | unit plus one route | `llm_request.send` (resolved knob) and E2 POST `/ai-advisor/chat/send` | new: `tests/llm_request/test_send.py`; route harness `tests/ai_advisor/test_m5_chat_hardening.py:178` | FX-E 404 captured; FX-M1 captured then a schema-derived page naming a different opus-line model; FX-R1 captured |
| S-2.23 | AC-2 | source scan with a planted call | Group A scan over `model_config.py` and `llm_request.py` | `tests/security/test_retirement_action_no_trade_boundary.py:298` | planted `save_symphony(...)`, order call and `LIVE_EXECUTION` read in a scratch copy, per the plant method at `tests/app/test_retirement_producer_explainer_wiring.py:146-153` |
| S-2.24 | AC-2 | source scan with a planted import | `advisors/retirement_recommender.py` import ban | `tests/app/test_retirement_producer_explainer_wiring.py:146` | planted `import model_config`, `import llm_request` and `from llm_request import send` as synthetic sources |

### C3
| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-2.1 | AC-2 | route integration | E1 POST `/ai-advisor/suggest` | `tests/advisors/test_r1_fable_suggestion_routing.py:71` (rewritten); route harness `tests/advisors/test_advisor_liveness_routes.py:420` | FX-M1 captured plus a schema-derived newer fable-line record; FX-R2S captured |
| S-2.2 | AC-2 | route integration | E2 POST `/ai-advisor/chat/send` | `tests/ai_advisor/test_chat_engine.py:219` | FX-M1 captured plus a schema-derived newer opus-line record; FX-R1 captured |
| S-4.1 | AC-4 | route integration | E1 POST `/ai-advisor/suggest` | `tests/advisors/test_advisor_liveness_routes.py:420` | FX-E 503 body; `unresolved_models` fixture |
| S-4.2 | AC-4 | route integration | E2 POST `/ai-advisor/chat/send` | `tests/ai_advisor/test_m5_chat_hardening.py:178` | FX-E 503 body; `unresolved_models` fixture |
| S-5.4 | AC-5 | route integration | E1 POST `/ai-advisor/suggest` | `tests/advisors/test_r1_fable_suggestion_routing.py:93` | FX-M1 captured (served ID used as the override); FX-R2S captured |
| S-5.9 | AC-5 | route integration | POST `/api/settings` | `tests/security/test_a1_settings_allowlist.py:218` | none beyond the existing `captured_set_key_calls` and `_stub_database` fixtures at `:75` |
| S-5.10 | AC-5 | route integration | E2 POST `/ai-advisor/chat/send` | `tests/ai_advisor/test_chat_engine.py:219` | FX-R1 captured |
| S-5.18 | AC-5 | route integration | E2 POST `/ai-advisor/chat/send` | `tests/ai_advisor/test_m5_chat_hardening.py:178` | FX-E 400 captured |
| S-6.10 | AC-6 | integration | E12 POST `/ai-advisor/accept` after pre-existing rows | `tests/database/test_llm_suggestions.py:275` | rows inserted through `database.record_llm_suggestion` with the Ref's default value read from the Ref file, not retyped |
| S-6.11 | AC-6 | route integration | E12 and E13 | `tests/advisors/test_advisor_liveness_routes.py:239` | FX-M1 captured |
| S-6.12 | AC-6 | route integration | E12 POST `/ai-advisor/accept` | `tests/advisors/test_advisor_liveness_routes.py:239` | `unresolved_models`; a listing transport that blocks, asserted never called |
| S-6.18 | AC-6 | route integration | E12 and E13 | `tests/advisors/test_advisor_liveness_routes.py:239` | `unresolved_models`; FX-M1 captured ID as override |
| S-8.1 | AC-8 | route integration | E1 POST `/ai-advisor/suggest` | `tests/ai_advisor/test_sdk_contract.py:111` (rewritten) | FX-R6 text variant, schema-derived and checked |
| S-8.2 | AC-8 | route integration | E2 POST `/ai-advisor/chat/send` | `tests/ai_advisor/test_chat_engine.py:219` | FX-R6 text variant |
| S-8.10 | AC-8 | route integration | E2 POST `/ai-advisor/chat/send` | `tests/ai_advisor/test_chat_engine.py:219` | FX-R5 null-category record |
| S-8.11 | AC-8 | route integration | E1 POST `/ai-advisor/suggest` | `tests/ai_advisor/test_sdk_contract.py:111` | FX-R5 category record |
| S-9.2 | AC-9 | route integration | E2 POST `/ai-advisor/chat/send` | `tests/ai_advisor/test_chat_engine.py:219` | FX-R1 captured |
| S-9.4 | AC-9 | route integration | E1 POST `/ai-advisor/suggest` | `tests/ai_advisor/test_sdk_contract.py:142` | FX-R2S captured |
| S-9.11 | AC-9 | route integration | E2 POST `/ai-advisor/chat/send` | `tests/ai_advisor/test_chat_engine.py:219` | FX-R4 captured |
| S-9.14 | AC-9 | route integration | E1 POST `/ai-advisor/suggest` | `tests/ai_advisor/test_sdk_contract.py:187` | FX-R4 captured |

### C4
| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-2.3 | AC-2 | route integration | E3 objective mode | `tests/ui/test_asset_swap_route_reasoning_provenance.py:156` | FX-M1 captured plus a schema-derived newer fable-line record; FX-R2 `emit_swap_candidates` captured; Composer seams as in that file's `_seeded_bot_state` at `:67` |
| S-4.3 | AC-4 | route integration | E3 objective mode | `tests/ui/test_asset_swap_route_reasoning_provenance.py:156` | FX-E 503 body; `unresolved_models` |
| S-4.14 | AC-4 | route integration (two requests) | E3 objective mode | `tests/ui/test_asset_swap_route_reasoning_provenance.py:156` | FX-E 503 body then FX-M1 captured; FX-R2 captured |
| S-4.15 | AC-4 | route integration | E3 explicit-pair mode | `tests/ui/test_asset_swap_route_reasoning_provenance.py:206` | FX-E 503 body; `unresolved_models`; recorder asserts zero Messages requests |
| S-4.16 | AC-4 | node render | Asset Swaps panel `renderResults` via the evaluate click | new: node harness test, no JS runner exists (`tests/ai_advisor/test_as_live_generation_provenance_render.py:34-40`); skip rule as `tests/js_syntax/test_js_syntax.py:31` | route JSON bodies produced by this cycle's route tests for each of the six reason codes and for a plain zero-survivor result (producer output, not hand-built) |
| S-5.5 | AC-5 | route integration | E3 objective mode | `tests/ui/test_asset_swap_route_reasoning_provenance.py:156` | FX-E 404 captured |
| S-5.17 | AC-5 | route integration | E3 objective mode | `tests/ui/test_asset_swap_route_reasoning_provenance.py:156` | FX-R2 `emit_swap_candidates` captured |
| S-6.4 | AC-6 | route integration | E3 objective mode | `tests/advisors/test_asset_swap_engine_provenance.py:146` (rewritten) | FX-R6 tool variant |
| S-6.6 | AC-6 | route integration plus node render | E3 objective mode, no Composer key | `tests/ui/test_asset_swap_route_reasoning_provenance.py:121`; node harness | `unresolved_models` |
| S-6.15 | AC-6 | route integration | E3 objective mode | `tests/advisors/test_asset_swap_engine_provenance.py:146` | FX-R5 category record (`reasoning_extraction`) |
| S-6.16 | AC-6 | route integration | E3 objective mode | `tests/advisors/test_asset_swap_engine_provenance.py:146` | FX-R3 captured, three times |
| S-6.17 | AC-6 | route integration | E3 explicit-pair mode | `tests/ui/test_asset_swap_route_reasoning_provenance.py:206` | FX-M1 captured |
| S-6.19 | AC-6 | route integration | E3 explicit-pair and objective early-error path | `tests/ui/test_asset_swap_route_reasoning_provenance.py:121` | FX-M1 captured ID as override |
| S-7.1 | AC-7 | route integration | E3 objective mode | `tests/advisors/test_asset_swap_engine_reasoned_generation.py:99` | FX-R2 `emit_swap_candidates` captured |
| S-7.5 | AC-7 | route integration | E3 objective mode | `tests/advisors/test_asset_swap_engine_honest_degradation.py:55` | FX-R3 captured, three times |
| S-7.12 | AC-7 | unit | asset swap generator | `tests/advisors/test_asset_swap_engine_reasoned_generation.py:99` | FX-R2 captured with its incumbent changed to a ticker absent from the test tree (the one field edited is named in `PROVENANCE.md`) |
| S-8.3 | AC-8 | route integration | E3 objective mode | `tests/advisors/test_asset_swap_engine_reasoned_generation.py:99` | FX-R6 tool variant |
| S-8.9 | AC-8 | route integration | E3 objective mode | `tests/advisors/test_asset_swap_engine_honest_degradation.py:55` | FX-R5 `reasoning_extraction` record |
| S-8.16 | AC-8 | route integration (request recorder) | E3 objective mode | `tests/ui/test_asset_swap_route_reasoning_provenance.py:156` | FX-R2 `emit_swap_candidates` captured |
| S-9.5 | AC-9 | route integration | E3 objective mode | `tests/advisors/test_asset_swap_engine_reasoned_generation.py:99` | FX-R2 captured |

### C5
| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-2.4 | AC-2 | route integration | E4 POST `/ai-advisor/logic-changes/evaluate` | `tests/ui/test_logic_change_route_reasoning_provenance.py:96` | FX-M1 captured plus a schema-derived newer fable-line record; FX-R2 `emit_logic_edits` captured |
| S-4.4 | AC-4 | route integration | E4 | `tests/ui/test_logic_change_route_reasoning_provenance.py:96` | FX-E 503 body; `unresolved_models` |
| S-5.14 | AC-5 | route integration | E4 | `tests/ui/test_logic_change_route_reasoning_provenance.py:96` | FX-R2 `emit_logic_edits` captured |
| S-7.2 | AC-7 | route integration | E4 | `tests/advisors/test_logic_change_engine_reasoned_generation.py:82` | FX-R2 `emit_logic_edits` captured |
| S-7.6 | AC-7 | route integration | E4 | `tests/advisors/test_logic_change_engine_honest_degradation.py:48` | FX-R3 captured, three times |
| S-8.4 | AC-8 | route integration | E4 | `tests/advisors/test_logic_change_engine_reasoned_generation.py:82` | FX-R6 tool variant |
| S-9.6 | AC-9 | route integration | E4 | `tests/advisors/test_logic_change_engine_reasoned_generation.py:82` | FX-R2 `emit_logic_edits` captured |

### C6
| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-2.5 | AC-2 | route integration | E5 POST `/ai-advisor/strategy-builder/run` | `tests/app/test_sb_backtest_unavailable_route.py:80` | FX-M1 captured plus a schema-derived newer fable-line record; FX-R2 `emit_build_plans` captured; Atlas loader patched and Mongo blocked by `tests/conftest.py:241` |
| S-4.5 | AC-4 | route integration | E5 | `tests/app/test_sb_backtest_unavailable_route.py:80` | FX-E 503 body; `unresolved_models`; Atlas candidates from the existing test doubles in that file |
| S-5.15 | AC-5 | route integration | E5 | `tests/app/test_sb_backtest_unavailable_route.py:80` | FX-R2 `emit_build_plans` captured |
| S-6.13 | AC-6 | route integration plus JS consumption | E5 | `tests/app/test_sb_backtest_unavailable_route.py:92`; `tests/ai_advisor/test_r1_sb_live_run_field_consumption.py:121` | FX-R3 captured three times; FX-M1 captured display name |
| S-7.3 | AC-7 | route integration | E5 | `tests/advisors/test_build_plan_generator_prompt_caching.py:151` | FX-R2 `emit_build_plans` captured |
| S-7.7 | AC-7 | route integration | E5 | `tests/advisors/test_build_plan_generator_truncation.py:266` | FX-R3 captured, three times |
| S-8.5 | AC-8 | route integration | E5 | `tests/advisors/test_strategy_builder_engine_reasoning_provenance.py:159` | FX-R6 tool variant |
| S-9.7 | AC-9 | route integration | E5 | `tests/advisors/test_build_plan_generator_truncation.py:249` | FX-R2 `emit_build_plans` captured |

### C7
| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-2.6 | AC-2 | job integration | E6 `run_frontrunner_build` as submitted by the route | `tests/app/test_frontrunner_builder_route.py:258`; generator tests `tests/advisors/test_frontrunner_builder.py:263` | FX-M1 captured plus a schema-derived newer fable-line record; FX-R2 `emit_frontrunner_overlay` captured; real tree fixtures `tests/fixtures/advisors/frontrunner/real_tree_*.json` |
| S-4.6 | AC-4 | job integration (caplog) | E6 | `tests/advisors/test_frontrunner_builder.py:263` | FX-E 503 body; `unresolved_models` |
| S-5.11 | AC-5 | job integration | E6 | `tests/advisors/test_frontrunner_builder.py:263` | FX-R2 `emit_frontrunner_overlay` captured |
| S-7.4 | AC-7 | job integration | E6 | `tests/advisors/test_frontrunner_builder_prompt_caching.py:149` | FX-R2 `emit_frontrunner_overlay` captured |
| S-7.8 | AC-7 | job integration (caplog) | E6 | `tests/advisors/test_frontrunner_builder.py:263` | FX-R3 captured, three times |
| S-8.6 | AC-8 | job integration | E6 | `tests/advisors/test_frontrunner_builder.py:263` | FX-R6 tool variant |
| S-9.8 | AC-9 | job integration | E6 | `tests/advisors/test_frontrunner_builder.py:445` | FX-R2 `emit_frontrunner_overlay` captured |

### C8
| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-2.7 | AC-2 | job integration | E7 `_retirement_recommender_tick_worker` | `tests/app/test_retirement_explainer_name_enrichment.py:78` | FX-M1 captured plus a schema-derived newer fable-line record; FX-R1-shaped text reply captured for the explainer prompt |
| S-2.8 | AC-2 | job integration | E8 `_lens_pipeline_worker` | `tests/ai_advisor/test_cycle4_lens_pipeline.py:463` | FX-M1 captured plus a schema-derived newer opus-line record; FX-R1 captured |
| S-4.7 | AC-4 | job integration (caplog) | E7 | `tests/app/test_retirement_explainer_name_enrichment.py:78` | FX-E 503 body; `unresolved_models` |
| S-4.8 | AC-4 | job integration | E8 | `tests/ai_advisor/test_cycle4_lens_pipeline.py:463` | FX-E 503 body; `unresolved_models` |
| S-5.12 | AC-5 | job integration | E8 | `tests/ai_advisor/test_synthesis_model_config.py:134` | FX-R1 captured |
| S-5.13 | AC-5 | job integration | E7 | `tests/advisors/test_retirement_explainer_generation.py:105` | FX-R1 captured |
| S-8.7 | AC-8 | job integration | E7 | `tests/advisors/test_retirement_explainer_generation.py:105` | FX-R6 text variant |
| S-8.8 | AC-8 | job integration | E8 | `tests/ai_advisor/test_cycle4_lens_pipeline.py:1371` | FX-R6 text variant |
| S-9.1 | AC-9 | job integration | E8 | `tests/ai_advisor/test_cycle4_lens_pipeline.py:1371` | FX-R1 captured with the lens synthesis prompt |
| S-9.3 | AC-9 | job integration | E7 | `tests/advisors/test_retirement_explainer_generation.py:105` | FX-R1 captured |
| S-9.12 | AC-9 | job integration (caplog) | E7 | `tests/advisors/test_retirement_explainer_generation.py:105` | FX-R4 captured |
| S-9.13 | AC-9 | job integration | E8 | `tests/ai_advisor/test_cycle4_lens_pipeline.py:463` | FX-R4 captured |

### C9
| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-6.1 | AC-6 | route integration (rendered HTML) | E11 GET `/ai-advisor` | `tests/ai_advisor/test_r1_attribution_honesty.py:202` | FX-M1 captured |
| S-6.2 | AC-6 | route integration plus source assertion | E11 after a re-listing | `tests/ai_advisor/test_r1_attribution_honesty.py:202` | FX-M1 captured then a schema-derived page with a newer fable-line record |
| S-6.5 | AC-6 | route integration | E11 | `tests/ai_advisor/test_r1_attribution_honesty.py:87` | `unresolved_models` |
| S-6.7 | AC-6 | route integration | E11 | `tests/ai_advisor/test_r1_attribution_honesty.py:87` | blocking listing transport, asserted never called |
| S-6.8 | AC-6 | route integration | E11 | `tests/ai_advisor/test_r1_attribution_honesty.py:202` | FX-M1 captured; override absent from it |
| S-6.9 | AC-6 | route integration plus node render | E11 and the Asset Swaps provenance line | `tests/ai_advisor/test_r1_attribution_honesty.py:202`; C4's node harness | FX-M1 captured record with only `display_name` changed to hold `<`, `&` and `"` (edit named in `PROVENANCE.md`) |
| S-6.14 | AC-6 | integration | `_start_model_warmup()` then E11 | `tests/ai_advisor/test_r1_attribution_honesty.py:202` | FX-M1 captured; `unresolved_models` before the warm-up |

### C10
| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-3.1 | AC-3 | unit, subprocess captured | E10 `prism_scheduler.main` | `tests/ai_advisor/test_prism_scheduling.py:1132` (HC-3 rewritten) | `subprocess.run` mock as at `:1148-1150` |
| S-3.2 | AC-3 | unit, subprocess captured | E10 | `tests/prism_scheduler/test_council_sub_auth.py:46` | env fixture in that file |
| S-3.3 | AC-3 | unit, subprocess captured | E10 with `ADVISOR_SYNTHESIS_MODEL` set | `tests/ai_advisor/test_prism_scheduling.py:166` | monkeypatched env |
| S-3.4 | AC-3 | unit | E10 with non-zero exits | `tests/ai_advisor/test_prism_scheduling.py:650` | `subprocess.run` mock returning non-zero, as in that class |
| S-3.5 | AC-3 | unit | E10 with tonight's row present | `tests/ai_advisor/test_prism_scheduling.py:96` | existing idempotency fixtures |
| S-3.6 | AC-3 | source read | `.claude/agents/prism-*.md` | `tests/ai_advisor/test_prism_scheduling.py:1429` | the six tracked agent files |

### C11
| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-2.9 | AC-2 | job integration | E9 `run_weekly_suggestions` | `tests/advisors/test_weekly_suggestions_orchestrator.py:70` | FX-M1 captured plus a schema-derived newer fable-line record; FX-R2 captured per tool |
| S-2.20 | AC-2 | integration, parametrized over the eight sites | E1 to E8 | new: `tests/advisors/test_model_unpin_cross_site.py`, no cross-site suite exists | each site's request body recorded at the Ref by running the Ref's site code against the same HTTP fake (golden captured from the Ref producer); FX-R1, FX-R2, FX-R2S captured |
| S-2.21 | AC-2 | job integration | E6 `run_frontrunner_build` and E9 `run_weekly_suggestions` | `tests/advisors/test_weekly_suggestions_orchestrator.py:70`; new cross-site file | FX-M1 captured then a schema-derived newer fable-line page served after a forced 24 h clock jump mid-run |
| S-4.9 | AC-4 | job integration | E9 | `tests/advisors/test_weekly_suggestions_orchestrator.py:128` | FX-E 503 body; `unresolved_models` |
| S-4.10 | AC-4 | integration, parametrized over sites and failure kinds | E1 to E9 | new: `tests/advisors/test_model_unpin_cross_site.py` | FX-E 401, 403, 429, 5xx bodies; transport timeout and connect errors; `unresolved_models` |
| S-4.11 | AC-4 | integration, parametrized over sites | E1 to E9 | new: `tests/advisors/test_model_unpin_cross_site.py` | FX-M6 (no fable) and an FX-M6 variant without opus records, schema-derived and checked |
| S-4.12 | AC-4 | integration, parametrized over sites | E1 to E9 | new: `tests/advisors/test_model_unpin_cross_site.py` | FX-E 503 body; recorder asserts zero Messages requests and no model literal in any argument |
| S-5.16 | AC-5 | job integration | E9 | `tests/advisors/test_weekly_suggestions_orchestrator.py:70` | FX-R2 captured per tool; override ID from FX-M1 |
| S-6.3 | AC-6 | route integration plus JS consumption | E3, E4, E5 | new: `tests/advisors/test_model_unpin_cross_site.py`; JS checks `tests/ai_advisor/test_as_live_generation_provenance_render.py:111`, `tests/ai_advisor/test_lc_live_generation_provenance_render.py:91`, `tests/ai_advisor/test_sb_live_run_provenance_render.py:79` | FX-R2 captured per tool (reply `model` read from the fixture) |
| S-7.9 | AC-7 | integration, parametrized over the four generators | E3, E4, E5, E6 | new: `tests/advisors/test_model_unpin_cross_site.py` | FX-R3 then FX-R2 captured |
| S-7.10 | AC-7 | integration | E5 and E6 | new: `tests/advisors/test_model_unpin_cross_site.py` | FX-R4, FX-R3, FX-R2 captured, in that order |
| S-7.11 | AC-7 | integration | E3, E4, E5, E6 | new: `tests/advisors/test_model_unpin_cross_site.py`; breakpoint assertions as `tests/advisors/test_build_plan_generator_prompt_caching.py:151` and `tests/advisors/test_frontrunner_builder_prompt_caching.py:149` | FX-R2 captured per tool; FX-R4 captured three times |
| S-7.13 | AC-7 | job integration | E9 | `tests/advisors/test_weekly_suggestions_orchestrator.py:70` | FX-R2 captured per tool |
| S-7.14 | AC-7 | integration (request recorder) | E3 and E4 | new: `tests/advisors/test_model_unpin_cross_site.py` | the two Ref tool definitions read from `advisors/asset_swap_engine.py:990` and `advisors/logic_change_engine.py:725` at the Ref as the producer baseline; sent bodies must equal the baseline plus `strict` and `additionalProperties: false` on every object node; FX-R2 `emit_swap_candidates` and `emit_logic_edits` captured |
| S-7.15 | AC-7 | integration (request recorder plus generator) | E5 and E6 | new: `tests/advisors/test_model_unpin_cross_site.py` | the two Ref tool definitions read from `advisors/build_plan_generator.py:598` and `advisors/frontrunner_builder.py:622` at the Ref; sent bodies must equal them exactly (no `strict`, `rhs` and `condition` open); FX-R2 `emit_build_plans` and `emit_frontrunner_overlay` captured for parsing and validation; FX-R3 captured three times for `no_tool_call` |
| S-8.12 | AC-8 | integration, parametrized over sites | E1 to E8 | new: `tests/advisors/test_model_unpin_cross_site.py` | FX-R5 partial-text and partial-tool records |
| S-8.13 | AC-8 | integration (request recorder), parametrized over sites and loop retries | E1 to E8 | new: `tests/advisors/test_model_unpin_cross_site.py` | FX-R1, FX-R2, FX-R2S, FX-R3, FX-R4 captured |
| S-8.14 | AC-8 | integration | E5 and E6 | new: `tests/advisors/test_model_unpin_cross_site.py` | FX-R5 category record |
| S-8.15 | AC-8 | job integration | E9 | `tests/advisors/test_weekly_suggestions_orchestrator.py:159` | FX-R5 category record for one call, FX-R2 captured for the rest |
| S-9.9 | AC-9 | integration, parametrized over sites | E1 to E8 | new: `tests/advisors/test_model_unpin_cross_site.py` | FX-R6 text and tool variants |
| S-9.10 | AC-9 | integration (request recorder), parametrized over sites | E1 to E8 | new: `tests/advisors/test_model_unpin_cross_site.py` | FX-R1, FX-R2, FX-R2S captured |
| S-9.15 | AC-9 | route integration | E3 and E4 | new: `tests/advisors/test_model_unpin_cross_site.py` | FX-R4 captured |
| S-9.16 | AC-9 | integration | E5 and E6 | new: `tests/advisors/test_model_unpin_cross_site.py`; `tests/advisors/test_build_plan_generator_truncation.py:266` | FX-R4 captured, three times |
| S-9.17 | AC-9 | integration, parametrized over sites | E1 to E8 | new: `tests/advisors/test_model_unpin_cross_site.py` | FX-E 400 captured |

### C12
| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-1.1 | AC-1 | source scan | repository checkout | new: `tests/security/test_no_versioned_model_ids.py`, no model-ID scan exists | the real tracked tree |
| S-1.2 | AC-1 | source scan | planted Python module | new: `tests/security/test_no_versioned_model_ids.py` | planted string in `tmp_path` with the repo's relative layout |
| S-1.3 | AC-1 | source scan | planted docstring and comment | new: `tests/security/test_no_versioned_model_ids.py` | planted in `tmp_path` |
| S-1.4 | AC-1 | source scan | planted `templates/` file | new: `tests/security/test_no_versioned_model_ids.py` | planted in `tmp_path` |
| S-1.5 | AC-1 | source scan | planted `static/` file | new: `tests/security/test_no_versioned_model_ids.py` | planted in `tmp_path` |
| S-1.6 | AC-1 | source scan | planted `.env.example` line | new: `tests/security/test_no_versioned_model_ids.py` | planted in `tmp_path` |
| S-1.7 | AC-1 | source scan, parametrized | one plant per ID shape and per family | new: `tests/security/test_no_versioned_model_ids.py` | the plan's S-1.7 shapes |
| S-1.8 | AC-1 | source scan | plants under `tests/`, `tests/fixtures/`, `docs/`, `docs/research/` Python, `feature-plans/` | new: `tests/security/test_no_versioned_model_ids.py` | planted in `tmp_path` |
| S-1.9 | AC-1 | source scan | `opus`, `Fable`, `model: opus`, `model: sonnet` | new: `tests/security/test_no_versioned_model_ids.py` | planted in `tmp_path` |
| S-1.10 | AC-1 | source scan | scan over the checkout | new: `tests/security/test_no_versioned_model_ids.py` | file mtimes and `git status --porcelain` read before and after |
| S-1.11 | AC-1 | source scan | plants in `deploy/`, `schedule_prism.ps1`, `.github/workflows/tests.yml`, an agent `model:` line | new: `tests/security/test_no_versioned_model_ids.py` | planted in `tmp_path` |

## Per-Cycle Risk Register

| Cycle | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| C1 | Existing tests patch `_build_client` with `MagicMock` clients (284 references, `git grep -c _build_client 37c8e983 -- tests/`); `iter(MagicMock())` is empty, so a cold registry lists zero models and every site degrades to `model_unavailable` | High | High | Autouse seed in `tests/conftest.py` resolves both knobs through the real `resolve` against FX-M1 before each test; `unresolved_models` opts out |
| C1 | The live API may omit `line` or `lifecycle` (Known-Unconfirmed 3); `ModelInfo` declares neither (`types/model_info.py`) | Medium | Medium | Rule implemented for both shapes (S-2.25, S-2.26); FX-M1 records which keys came back |
| C1 | The listing lock is held while a slow listing runs, blocking request threads | Medium | Low | `_MODELS_LIST_TIMEOUT_SECONDS = 10.0`; the render and audit paths use `peek`, which never takes the lock (S-6.7, S-6.12) |
| C1 | A forgotten seam lists or calls the real API from a test, a new network path this plan adds | Low | High | `ANTHROPIC_BASE_URL` guard in `pytest_configure` (`tests/conftest.py:86`), off only when `live` is selected (`pyproject.toml:85`) |
| C2 | `messages.parse` raises inside the SDK on a refusal's or truncation's partial JSON (`lib/_parse/_response.py:16-19`), hiding the reason | High | Medium | Suggestions move to `create` with `output_config.format` and validate `Reply.text()` themselves |
| C2 | The SDK raises `ValueError` for a non-streaming request above 21,333 tokens with no explicit timeout (`_base_client.py:726-735`) | Medium | High | Budgets at sites without a timeout stay at 21000, 16000 and 8000 |
| C2 | The SDK builds a `fallback` block as a `TextBlock` with a `text` attribute (architect probe), so a `hasattr` read would leak it | High | Medium | `Reply.text()` selects by `type == "text"` (S-9.9) |
| C2 | Legacy doubles return a non-string `model`, which would break `jsonify` on provenance | High | Medium | `Reply.model` falls back to the requested ID unless the reply's value is a non-empty string |
| C3, C4, C5, C8 | 30 s timeouts (`ai_advisor.py:62`, `advisors/advisor_chat.py:214`, `advisors/asset_swap_engine.py:316`, `advisors/logic_change_engine.py:723`, `advisors/retirement_explainer.py:54`) cannot hold a thinking reply at `high` | High | High | Raised to 120 s (Items Raised 2); S-9.18 measures each site live before merge |
| C4, C5 | Worst-case wait per click: 3 attempts times 120 s, about 6 minutes, inline in the request thread (Known fact 9) | Medium | Medium | Accepted by the PM (ruling on Items Raised 2); the bound is the plan's Q-9; replaces the plan's "about 90 s" under Breaking Changes |
| C4, C5 | The type list at `advisors/logic_change_engine.py:744` (`["string", "integer"]`) may be rejected in strict mode (Known-Unconfirmed 5) | Medium | Medium | The PM's token-count probe of the two strict tools (Items Raised 6) settles it before C5 RED; a rejection goes back to the PM, never a silent schema change |
| C4, C5, C6, C7 | Client doubles in existing suites with fixed `create` signatures reject `output_config`, `extra_body` or `extra_headers`, and tests pin `tool_choice` type `tool` (`git grep -l tool_choice 37c8e983 -- tests/`) | High | Medium | Each cycle's closer runs `git grep -nE "tool_choice\|messages\.create\|_build_client" tests/` for its module and rewrites stale doubles (DC-4) |
| C6, C7 | Under `tool_choice` auto the non-strict build-plan and overlay tools can return input the schema does not constrain (open `rhs` at `advisors/build_plan_generator.py:681` and `advisors/frontrunner_builder.py:663`, unlisted compound fields in the overlay `condition` at `advisors/frontrunner_builder.py:647-668`) | Medium | Medium | Unchanged from the Ref: the compiler (`advisors/plan_tree_compiler.py:316-327`) and the overlay VIX and compile checks (`advisors/frontrunner_builder.py:982-1019`) stay the validators; S-7.11 proves they still run |
| C6 | The build-plan golden prompt (`tests/advisors/test_build_plan_generator_prompt_caching.py:151`) breaks if prompt text changes | Low | Medium | No prompt text changes; the tool instruction already exists at `advisors/build_plan_generator.py:1037` |
| C7 | Generation failures logged at INFO (`advisors/frontrunner_builder.py:1982`) and DEBUG (`advisors/strategy_builder_engine.py:415`) never reach the journal (`app.py:482-486`) | High | Medium | Both move to WARNING with the reason only (S-4.6, S-7.8) |
| C3, C4, C5, C6, C9 | Parallel branches edit `app.py` at `:8705` and `:8738`, `:7260`, `:7618`, `:8090`, `:6520` and `:7173` | Medium | Low | Distinct hunks; the PM merges into `model-unpin/sites` and `model-unpin/all` in cycle order and reruns each cycle's suites on the merge |
| C10 | The droplet CLI resolves `opus` to whatever its version knows (NB-19, Known-Unconfirmed 2) | Medium | Medium | Merge check reads `claude --version` and the model in one council run's JSON |
| C11 | S-2.20 needs the Ref request bodies; hand-typed baselines would encode the author's assumption | Medium | Medium | Baselines produced by running the Ref site code at 37c8e983 against the same HTTP fake |
| C11 | A refused weekly call aborts the batch | Low | Medium | Existing per-symphony and per-engine isolation (`advisors/weekly_suggestions_scheduler.py:431-448`, `advisors/strategy_builder_scheduler.py:153-190`) stays; S-8.15 proves it |
| C12 | The scan reads untracked files (`.claude/worktrees/` copies, untracked agent files) and fails on stale duplicates | Medium | Medium | File set from `git ls-files` only; worktree copies are untracked |
| C12 | Deleting the old accessors breaks a test outside the touched suites | Medium | Medium | `git grep -nE "get_advisor_suggestion_model\|resolve_advisor_model\|FABLE_MODEL\|_humanize_model_name\|_MODEL_DISPLAY_NAMES" -- '*.py'` returns nothing before the cycle closes |

## Cycle-Close Criteria

- Every scenario the cycle owns has a test or a held row with its reason in `records/coverage.json`, and `python3 ~/.claude/scripts/plan-trace check-coverage <record> --plan feature-plans/model-unpin.md --approach feature-plans/approach/model-unpin.md` exits 0.
- The branch's own test files ran through `review-ledger run-set` and `census-run`, and `review-ledger close` exits 0 at the head, with at most 4 xdist workers and never `-n auto`.
- `ruff format --check` and `ruff check` pass at line length 100.
- Consumer sweep for the symbols the cycle changed (the grep in the C4 to C7 and C12 risk rows) returns no stale reference in `tests/`, `static/` or `docs/generated/`.
- No new response string or WARNING-or-above log carries exception text: `git diff <base>..HEAD` shows no new `str(exc)`, `%s`-formatted exception or `exc_info=True` at WARNING or above in the cycle's files; `tests/security/test_d1_error_disclosure.py` passes.
- Advisor imports in `app.py` stay inside functions; `git diff <base>..HEAD -- alpha_bot_execution.py math_engine.py migrations/ requirements.txt` is empty.
- No test reaches the real Anthropic or Mongo endpoints: every new test uses `tests/_anthropic_http_fake.py` or an existing client double, and the conftest guards are untouched.
- New fixtures carry one record per case with the case named in `PROVENANCE.md`, and `tests/model_config/test_fixture_schemas.py` passes for every schema-derived record.
- The cycle-complete message names the RED and GREEN SHAs copied from `git rev-parse` and the coverage map path.

## Merge Criteria

The stacked PRs (each cycle onto its Branch base) pass items 1 to 5 at their own head; the PR that lands on `main` is the integration head carrying C11 and C12 on `model-unpin/all`, and it passes every item.

1. CI green on the exact PR head SHA (`.github/workflows/tests.yml`: ruff and the default pytest selection).
2. The project's `-n0` gate on the touched suites (`tests/model_config/`, `tests/llm_request/`, `tests/advisors/`, `tests/ai_advisor/`, `tests/app/`, `tests/ui/`, `tests/security/`, `tests/prism_scheduler/`, `tests/database/test_llm_suggestions.py`) at the head SHA, count and SHA quoted.
3. `/review` (the built-in PR review skill) run on the PR, every finding resolved or explicitly accepted.
4. `git diff main...HEAD -- alpha_bot_execution.py math_engine.py migrations/ requirements.txt` empty; no schema migration, so no migration proof is required; `.claude/agents/prism-*.md` line 5 still reads `model: opus`.
5. The doc-gen slice is committed: the `docs/generated/` pages the plan lists plus new pages for `model_config.py` and `llm_request.py`, `docs/features/advisor-synthesis-model-config.md`, `README.md`, the `DECISIONS.md` entry recording the unpin and the DE-PRISM-COUNCIL supersession, and the drafted `.claude/CLAUDE.md` Key Files rows for PM approval.
6. The PM's live functional check on the droplet after the review (mocked-green has diverged from live behaviour in this repository): each entry point E1 to E13 run once; S-9.18 (each of the eight sites once at its chosen effort, every reply's `stop_reason` other than `max_tokens`, latency read against the 120 s timeouts); the badges and the Strategy Builder notice read on the rendered page after a daemon restart with no operator action; `provenance.generation_model` read from the E3, E4 and E5 responses; one `llm_suggestions.model_id` read back after an accept (read-only query, the persistence roundtrip); the council command line read from the journal and the model reported in the council run's JSON with `claude --version`; the AC-4 WARNING records confirmed visible in the journal; reply `usage` recorded for the chat and lens sites (Known-Unconfirmed 7).
7. Market-hours and holiday check before the deploy restart, per the project's deploy rules.

## Items Raised to the PM

Each item below was raised in the first pass and has a PM ruling; the rulings are applied throughout this document.

1. **S-7.14 versus the two DSL tool schemas.** Ruling: option (b). `strict_tool` applies to the asset swap and logic change tools only; the build-plan and overlay tools stay non-strict, validated by the compiler; the plan now carries it as S-7.14 (strict, asset swap and logic change), S-7.15 (non-strict, build plans and overlay) and D-5. Applied in Architecture, the AC-7 thread row, C2 (helper tests on the 4 object nodes of the two closed schemas), C6, C7 and the C11 S-7.14 row. No strict-schema probe runs for the two DSL tools.
2. **Timeouts.** Ruling: 120 s per attempt; Q-9's three attempts stand; the worst case of about 6 minutes per asset swap or logic change click is accepted and recorded in Architecture and the risk register, replacing the plan's "about 90 s" under Breaking Changes.
3. **Output budgets.** Ruling: the per-site `max_tokens` changes are AC-9 request settings that S-2.20 allows.
4. **S-9.18.** Ruling: carried by the PM's live merge check on the droplet (Merge Criteria item 6), not by a cycle; it appears in no cycle table.
5. **Census entry `python -m advisors.strategy_builder_scheduler` (`advisors/strategy_builder_scheduler.py:210`).** Ruling: no new matrix column; E9's weekly-run scenarios (S-2.9, S-2.21, S-4.9, S-5.16, S-7.13, S-8.15) are accepted as its coverage, and C11 pins its run.
6. **Fixture capture.** Ruling: the PM captures on the droplet and commits to `model-unpin/fixtures` off main@37c8e983 before C1 starts. Captured files and their shapes are listed under Architecture, Test infrastructure: `models/page_captured.json`, `errors/404_not_found.json`, `errors/400_invalid_request.json`, `messages/thinking_text.json`, the four `messages/thinking_tool_<tool name>.json`, `messages/thinking_structured.json`, `messages/text_only_with_tools.json`, `messages/max_tokens.json`, plus `PROVENANCE.md`. The token-count probe runs for the two strict tools only (`emit_swap_candidates`, `emit_logic_edits`, sent as `strict_tool` builds them) and settles Known-Unconfirmed 5 for the type list at `advisors/logic_change_engine.py:744`. The schema-derived files (FX-M2 to FX-M6, the other FX-E statuses, FX-R5, FX-R6) are written by the C1 and C2 test-writers.
