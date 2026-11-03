# Feature: Unpin every Claude model version
Status: ready
Story: none: operator chat request on 2026-10-22, saved verbatim in feature-plans/model-unpin.source.md
Type: fix
Suite: none
Repos: PlanetStopper
Base: main
Ref: PlanetStopper@37c8e983b64c6980c28199a3f9318486b60f3864
Sensitive: none: no trade, order, credential or settings write path changes; the AI Advisor accept route's config write and its four gates, the proposal-approve route and the retirement approve and reject routes keep their behavior (NB-22), and the change alters which model writes advisory text, which the operator still approves before anything reaches a real-money decision
Depends on: none

## Trigger
The operator found the production droplet still calling Claude Opus 4.8 and Claude Fable 5 while Claude Opus 5.5 and Claude Fable 5.1 are current, and asked that nothing pin a model version. The source, the Step-2 answers and the PM's relayed operator message and live read are saved verbatim in `feature-plans/model-unpin.source.md` and quoted in Requirements as Given (G-1 to G-32). At the ref, these production files name a versioned model ID: `model_config.py:26`, `model_config.py:34`, `ai_advisor.py:118`, `ai_advisor.py:120`, `advisors/advisor_chat.py:385`, `advisors/lens_pipeline.py:289`, `advisors/frontrunner_builder.py:7`, `advisors/frontrunner_builder.py:84`, `prism_scheduler.py:263`, the display map at `app.py:6527-6534`, and the comments at `.env.example:129-130`.

Known facts this plan is written on:
1. The Messages API accepts no generic family tag: the PM's live read on 2026-10-22 against the droplet's API key (anthropic SDK 0.85.0) got HTTP 404 for `opus`, `fable`, `sonnet`, `claude-opus`, `claude-opus-latest` and `claude-fable-latest`, while `claude-opus-5-5` and `claude-fable-5-1` resolved (G-30). The public models overview states every model ID is a pinned snapshot (https://platform.claude.com/docs/en/about-claude/models/overview).
2. The public Models API reference lists models newest first, 20 per page by default with `has_more` paging, and documents for each model `id`, `display_name`, `created_at`, `lifecycle` (`active`, `deprecated`, `retired`) and `line` (the family, such as `opus` or `fable`), and says to read `line` rather than infer the family from the ID (https://platform.claude.com/docs/en/api/models-list). The PM's live read gave the newest per family as opus `claude-opus-5-5`, fable `claude-fable-5-1`, sonnet `claude-sonnet-5-5`, haiku `claude-haiku-5-5` (G-31); it did not report whether `line` and `lifecycle` came back (Known-Unconfirmed 3).
3. The Claude Code CLI accepts the generic tag `opus` and resolves it to its own newest Opus; the six council agent files already use `model: opus` (`.claude/agents/prism-synthesizer.md:5` and the five analyst files at line 5), and every other tracked agent file names a family alias, never a version.
4. On Claude Opus 5.5 and Claude Fable 5.1, forced tool choice (`any` or `tool`) returns HTTP 400, thinking is always on and `max_tokens` covers thinking plus text, a reply can begin with thinking blocks, `temperature`, `top_p`, `top_k`, manual `budget_tokens` and `thinking` disabled are rejected, Opus 5.5 defaults to `medium` effort and Fable 5.1 to `high`, and a classifier refusal is an HTTP 200 reply with `stop_reason` `refusal` and a `stop_details` object whose `category` can be null; server-side fallback (request field `fallbacks` set to `default`, beta `server-side-fallback-2026-07-01`) retries it on another model, after which the reply's top-level `model` names the model that answered and a `fallback` content block marks the hand-over (https://platform.claude.com/docs/en/models/opus-5-5/migration-guide, https://platform.claude.com/docs/en/models/fable-5-1/overview, https://platform.claude.com/docs/en/build-with-claude/refusals-and-fallback).
5. The pinned SDK, anthropic 0.85.0 (`requirements.txt:1`, `anthropic~=0.85.0`), as read by the auditor from the installed package: its model-list type declares only `id`, `created_at`, `display_name` and `type` and keeps any other returned key as an untyped extra; `messages.create` and `messages.parse` accept `output_config` (with `effort`), `tools` with a `strict` flag and `tool_choice`; neither the standard nor the beta namespace has a `fallbacks` parameter, and `create` and `parse` on both namespaces take `extra_body` and `extra_headers`, so `fallbacks` and the beta header can go on the wire from either namespace; `client.beta.messages.parse` exists and takes `output_format` and `betas`; the reply type declares no `stop_details`; and an unknown `fallback` content block is built as a text-type object that has a `text` attribute, so code that selects blocks by `hasattr(block, "text")` (`advisors/advisor_chat.py:404-409`, `advisors/retirement_explainer.py:140-144`) would admit it.
6. None of the four tool input schemas (`advisors/asset_swap_engine.py:990`, `advisors/logic_change_engine.py:725`, `advisors/build_plan_generator.py:598`, `advisors/frontrunner_builder.py:622`) sets `additionalProperties` on any of its 13 object nodes, which strict tool use requires on every object. The build-plan and frontrunner overlay schemas also hold deliberately open objects (`rhs` at `advisors/build_plan_generator.py:681` and `advisors/frontrunner_builder.py:663`, the overlay `condition` at `advisors/frontrunner_builder.py:646-669`), whose keys the compiler reads and validates (`advisors/plan_tree_compiler.py:316-327`).
7. No `advisor_observations` row stores a model ID at the ref: the persisted `raw_response` carries `run_id` and `evidence_injected` but not `provenance` (`advisors/asset_swap_engine.py:783-799`, `advisors/strategy_builder_engine.py:735-743`). The only stored model field is `llm_suggestions.model_id` (`migrations/003_llm_suggestions.sql:45`).
8. In the daemon only the `alphabot` logger has a handler (`app.py:482-485`); advisor modules log through their own module loggers, so their records below WARNING are not emitted, while WARNING and above reach stderr and the journal through Python's last-resort handler. The Strategy Builder generator's failure reason is logged at DEBUG (`advisors/strategy_builder_engine.py:414-418`) and the frontrunner generation failure at INFO (`advisors/frontrunner_builder.py:1982-1986`).
9. The asset swap and logic change routes call their engines inline in the request (`app.py:7222`, `app.py:7577`) with a 30 s timeout per generation request (`advisors/asset_swap_engine.py:316`, `advisors/logic_change_engine.py:723`). In explicit-pair mode the asset swap route makes no model call (`advisors/asset_swap_engine.py:1449`).

## Summary
Every Claude call PlanetStopper makes runs on the newest model of its family without any versioned model ID in production or deployed source, for the operator using the AI Advisor tabs, the nightly Market Prism council, the 03:00 lens synthesis, the 03:45 retirement explainer and the weekly suggestion schedulers.
- A source scan fails the build on any versioned Claude model ID in production or deployed source (AC-1).
- Each Messages API call site resolves the newest active model of its family from the Models API at run time, re-resolving at most once a day, keeping the last good result, and re-resolving when a resolved model stops being served (AC-2).
- The nightly council launches with the generic tag `opus` (AC-3).
- A family never resolved in the process degrades honestly with a reason code, never a literal ID, and the panels say no candidate was generated rather than that the gate rejected one (AC-4).
- The two operator overrides keep working, and an override the API does not serve fails loudly instead of being replaced (AC-5).
- Model badges and copy, run provenance and the accept and reject audit rows name the model actually resolved or used (AC-6).
- The four tool-emitting generators stop forcing a tool call, send strict tool definitions and handle a reply without a tool call (AC-7).
- Classifier refusals are retried server-side on a fallback model and otherwise degrade honestly (AC-8).
- Requests and response parsing fit models that always think: explicit effort, no rejected parameters, answers read by block type, truncation reported (AC-9).

## Requirements as Given
[G-1] Also, WHY THE FUCK IS ANYTHING IN THIS FUCKING DROPLET PINNED TO A MODEL VERSION. model-unpin.source.md:2
[G-2] STILL USING OPUS 4.8, FABLE 5, FUCKING SERIOUSLY?! model-unpin.source.md:2
[G-3] AND STOP PINNING SPECIFIC FUCKING VERSIONS OF SHIT LIKE THIS model-unpin.source.md:3
[G-4] JUST DON'T PIN THE FUCKING VERSION, YOU SHOULD KNOW THAT THE GENERIC TAG ALWAYS POINTS TO THE LATEST FUCKING VERSION, WHY AM I HAVING TO TELL YOU HOW YOU YOURSELF FUCKING WORK? model-unpin.source.md:29
[G-5] IT SHOULD JUST BE FUCKING 'OPUS' model-unpin.source.md:29
[G-6] (Asked, Building 1: the single thing and its trigger) Single thing: no production code path in PlanetStopper names a dated Claude model version; every LLM call runs on the newest model of its intended family, resolved at run time. model-unpin.source.md:7
[G-7] (Building 1, continued) Trigger: the operator found the droplet still on Opus 4.8 and Fable 5 while Opus 5.5 and Fable 5.1 are current. model-unpin.source.md:7
[G-8] (Asked, Building 2: users and surfaces) Users/surfaces: the operator through every AI Advisor action that calls Claude (suggestions, chat, asset swaps, logic changes, Strategy Builder, Frontrunner Builder, retirement explainer), the nightly Market Prism council (`prism_scheduler.py` runs `claude -p --model claude-opus-4-8`, around line 263), the daemon lens-pipeline synthesis (`advisors/lens_pipeline.py` ~289), the weekly suggestion schedulers, and the model-name badges/copy in the AI Advisor UI (`app.py` `_MODEL_DISPLAY_NAMES` ~6520-6536, a hard-coded id->name map). model-unpin.source.md:8
[G-9] (Asked, Building 3: flows) Flows: no new screen. model-unpin.source.md:9
[G-10] (Building 3, continued) Each existing action behaves as today, on the newest model; each model badge shows the resolved model's display name as the Models API reports it. model-unpin.source.md:9
[G-11] (Asked, Building 4: negatives) Negatives: Models API unreachable after an earlier success -> keep using the last model resolved for that family; never resolved in this process -> the action degrades honestly (D-1: a reason code, never str(exc), never a literal fallback ID); the model refuses (stop_reason "refusal") -> server-side fallback, then an honest refusal state; under tool_choice auto the model replies without calling the tool -> bounded retry, then honest degrade naming the reason; an operator env override names a model that does not exist -> honest error naming the override; truncation (stop_reason max_tokens) keeps today's bounded-retry behavior; concurrent resolutions must not issue a storm of Models API calls. model-unpin.source.md:10
[G-12] (Asked, Building 5: data and contracts) Data/contracts: `model_config.get_advisor_suggestion_model()` (model_config.py ~22-35, default "claude-fable-5", env ADVISOR_SUGGESTION_MODEL), `ai_advisor.resolve_advisor_model()` / ADVISOR_SYNTHESIS_MODEL (ai_advisor.py ~118-120, default "claude-opus-4-8"), `advisors/advisor_chat.py` ~385 literal, `advisors/frontrunner_builder.py` ~7 and ~84, `advisors/lens_pipeline.py` ~289. model-unpin.source.md:11
[G-13] (Building 5, continued) Forced tool use `tool_choice={"type":"tool",...}` at `advisors/asset_swap_engine.py` ~1165, `advisors/build_plan_generator.py` ~1119, `advisors/frontrunner_builder.py` ~951, `advisors/logic_change_engine.py` ~884: current Fable 5.1 and Opus 5.5 return HTTP 400 for forced tool_choice "any"/"tool", so these must move to tool_choice auto with the tool marked strict and a prompt instruction to call it. model-unpin.source.md:11
[G-14] (Building 5, continued) Current API facts (Anthropic claude-api reference, cached 2026-10-06): the Models API `client.models.list()` returns `id`, `display_name`, `created_at`; there is no "latest" alias for API model IDs; on Fable 5.1 / Opus 5.5 `temperature`, `budget_tokens` and thinking "disabled" are rejected with 400; server-side refusal fallbacks are `betas=["server-side-fallback-2026-07-01"]` + `fallbacks="default"`. model-unpin.source.md:11
[G-15] (Building 5, continued) Provenance dicts that carry `generation_model` must carry the resolved ID. model-unpin.source.md:11
[G-16] (Building 5, continued) Historical DB rows keep the IDs they were written with. model-unpin.source.md:11
[G-17] (Building 5, continued) The droplet `.env` has no MODEL override lines (PM checked). model-unpin.source.md:11
[G-18] (Building 5, continued) The six `.claude/agents/prism-*.md` files already use the alias `model: opus`. model-unpin.source.md:11
[G-19] (Building 5, continued) No model literal exists in static/ or templates/ (PM grep). model-unpin.source.md:11
[G-20] (Asked, Building 6: repos) Repos: PlanetStopper only. model-unpin.source.md:12
[G-21] (Building 6, continued) Nothing else must land first. model-unpin.source.md:12
[G-22] (Asked, Building 7: sensitive) Sensitive: no. model-unpin.source.md:13
[G-23] (Building 7, continued) No trade, order or credential path changes; spend per call is equal (Fable 5.1 = Fable 5 price) or lower (Opus 5.5 below Opus 4.8). model-unpin.source.md:13
[G-24] (Asked, Not building 1: non-goals) No change to which FAMILY each call site uses (the suggestion/generation path stays on the Fable family, synthesis/chat stays on the Opus family, each site keeps its current family); only the version stops being pinned. model-unpin.source.md:15
[G-25] (Not building 1, continued) No prompt re-tuning for the newer models (a separate follow-up, tracked in the plan's follow-ups). model-unpin.source.md:15
[G-26] (Not building 1, continued) No new UI. model-unpin.source.md:15
[G-27] (Asked, Not building 2: what stays as is) Must stay exactly as is: advisory-only and no-trade boundaries (tests/security scans stay green), D-1 never-raises contracts, prompt-cache breakpoints (DE-ADVISOR-CACHE-001) still placed, truncation retry counts, every route's JSON shape except the model value it reports. model-unpin.source.md:16
[G-28] (Asked, Not building 3: deferred) Deferred: prompt-audit/effort re-tuning for the new models; the Playwright/harness unpin (separate harness work, not this repo). model-unpin.source.md:17
[G-29] (Asked, Not building 4: must not change) Must not change: `alpha_bot_execution.py`, `math_engine.py`, the database schema (no migration). model-unpin.source.md:18
[G-30] (PM live read, 2026-10-22) Live evidence for the plan (PM ran it 2026-10-22 ~19:30 UTC on the droplet's real API key, anthropic SDK 0.85.0, via client.models.retrieve): "opus", "fable", "sonnet", "claude-opus", "claude-opus-latest", "claude-fable-latest" each return HTTP 404 not_found_error ("model: opus"); "claude-opus-5-5" and "claude-fable-5-1" resolve. model-unpin.source.md:29
[G-31] (PM live read, continued) client.models.list() newest-by-created_at per family today: opus=claude-opus-5-5, fable=claude-fable-5-1, sonnet=claude-sonnet-5-5, haiku=claude-haiku-5-5. model-unpin.source.md:29
[G-32] (PM live read, continued) Record this in Known facts / the problem statement: the Messages API accepts no generic family tag, so the SDK call sites resolve the newest family member from the Models API at run time (the API-side equivalent of the generic tag), while the one Claude Code CLI path (`prism_scheduler.py` `claude -p --model ...`) uses the plain generic tag `opus`, which Claude Code resolves to its newest Opus itself model-unpin.source.md:29

## Requirement Inventory
| R-n | Requirement item | Source (G ids) | Maps to (AC ids, or NB-n) | Scope (full, or narrowed: what is left out) |
|---|---|---|---|---|
| R-1 | Nothing on the droplet names a versioned Claude model ID: production code, deployed config and scripts, and the agent files the council loads | G-1, G-6 | AC-1, AC-2, AC-3 | full |
| R-2 | Stop pinning specific versions of things like this | G-3 | AC-1, NB-4 | narrowed: covers Claude model versions only; package version pins in `requirements.txt` and the Playwright harness pin are left out (NB-4, NB-18, Q-11) |
| R-3 | Production moves off Opus 4.8 and Fable 5 to the current models | G-2, G-7 | AC-2, AC-3 | full |
| R-4 | The council uses the generic tag `opus`, which points to the latest version | G-4, G-5, G-32 | AC-3 | full |
| R-5 | Each Messages API site resolves the newest member of its family from the Models API at run time, because the API takes no generic tag | G-6, G-30, G-31, G-32 | AC-2 | full |
| R-6 | Every AI Advisor action that calls Claude is covered: suggestions, chat, asset swaps, logic changes, Strategy Builder, Frontrunner Builder, retirement explainer | G-8 | AC-2, AC-4, AC-7, AC-8, AC-9 | full |
| R-7 | The nightly Market Prism council is covered | G-8 | AC-3 | full |
| R-8 | The daemon lens-pipeline synthesis is covered | G-8 | AC-2, AC-4, AC-9 | full |
| R-9 | The weekly suggestion schedulers are covered | G-8 | AC-2, AC-4, AC-7, AC-8 | full |
| R-10 | Model badges and copy show the resolved model's display name as the Models API reports it, with no hard-coded ID to name map | G-8, G-10 | AC-6 | full |
| R-11 | No new screen; each existing action behaves as today on the newest model | G-9, G-10 | AC-2, NB-3 | full |
| R-12 | Models API unreachable after an earlier success: keep the last model resolved for that family | G-11 | AC-2 | full |
| R-13 | Never resolved in this process: the action degrades honestly with a reason code, never exception text, never a literal fallback ID | G-11 | AC-4 | full |
| R-14 | A refusal goes to server-side fallback, then to an honest refusal state | G-11, G-14 | AC-8 | full |
| R-15 | Under tool_choice auto, a reply without the tool call gets a bounded retry, then an honest degrade naming the reason | G-11, G-13 | AC-7 | full |
| R-16 | An override naming a model that does not exist gives an honest error naming the override | G-11 | AC-5 | full |
| R-17 | Truncation keeps today's bounded retry behavior | G-11, G-27 | AC-9 | full |
| R-18 | Concurrent resolutions issue no storm of Models API calls | G-11 | AC-2 | full |
| R-19 | The two accessors, the chat and lens literals and the Frontrunner Builder literal stop carrying a version | G-12 | AC-1, AC-2 | full |
| R-20 | The four forced tool calls move to tool_choice auto with the tool marked strict and a prompt instruction to call it | G-13 | AC-7 | narrowed: all four move to tool_choice auto with the call instruction, but only the asset swap and logic change tools are marked strict; the build-plan and frontrunner overlay tools stay non-strict because their `rhs` and `condition` fields are deliberately open objects (`advisors/build_plan_generator.py:681`, `advisors/frontrunner_builder.py:646-669`) that the compiler reads and validates (`advisors/plan_tree_compiler.py:316-327`) (PM ruling, D-5) |
| R-21 | No request sends temperature, budget_tokens or thinking disabled | G-14 | AC-9 | full |
| R-22 | Provenance `generation_model` carries the resolved ID | G-15 | AC-6 | full |
| R-23 | Historical DB rows keep the IDs they were written with | G-16 | AC-6, NB-15 | full |
| R-24 | With no override lines in the droplet `.env`, every family runs on its newest model | G-17 | AC-2, AC-5 | full |
| R-25 | The six council agent files keep `model: opus` | G-18 | AC-1, NB-14 | full |
| R-26 | Templates and static JS stay free of model literals | G-19 | AC-1 | full |
| R-27 | Only PlanetStopper changes and nothing must land first | G-20, G-21 | NB-23 | full |
| R-28 | No trade, order or credential path changes | G-22, G-23 | NB-8, NB-22 | full |
| R-29 | Spend per call is equal or lower | G-23 | AC-9 | narrowed: equal or lower spend per call is not guaranteed; Opus sites that ran without thinking on Opus 4.8 now think and thinking is billed as output, so the plan only requires an explicit effort per site (Known-Unconfirmed 7) |
| R-30 | Each call site keeps its current family | G-24 | AC-2, NB-1 | full |
| R-31 | No prompt re-tuning for the newer models | G-25 | NB-17 | full |
| R-32 | No new UI | G-26 | NB-3 | full |
| R-33 | Advisory-only and no-trade boundaries, D-1, cache breakpoints, truncation retry counts and route JSON shapes stay as they are | G-27 | AC-2, AC-7, AC-9, NB-8, NB-9, NB-10, NB-11, NB-12 | full |
| R-34 | Effort re-tuning and the Playwright unpin are deferred | G-28 | NB-17, NB-18 | full |
| R-35 | `alpha_bot_execution.py`, `math_engine.py` and the schema do not change | G-29 | NB-20, NB-21 | full |

## User Tasks
none: no new screen or task (G-9, G-26); existing badge spans, the Strategy Builder degraded notice, the provenance lines and the asset swap empty-state title change only their text (AC-4, AC-6).

## Earlier criteria on the same surface
Searched every `feature-plans/*.md` (including the `.completed.md` and `.obsolete.md` families), `DECISIONS.md`, `docs/generated/*.md`, `docs/features/`, `docs/audit/` and `docs/audit-inputs/` for model IDs, the two override variables, `model_config`, `generation_model`, `tool_choice`, the attribution badges and LLM import bans.

| Earlier criterion (plan and AC) | Surface | Disposition (kept, changed or contradicted) | Why |
|---|---|---|---|
| `feature-plans/advisor-remediation-r1.md` AC-16 (suggestion calls use `claude-fable-5` via an env-overridable accessor) | `model_config.py:25-38` and every suggestion site | changed | The Fable family and the override stay; the default `claude-fable-5` becomes the newest active fable-line model (AC-2, AC-5) |
| `feature-plans/advisor-remediation-r1.md` AC-16 attribution coherence ("a Fable suggestion surface must never display Opus") | Strategy Builder `mode_notice`, `app.py:8090-8094` | kept | Restored: at the ref the notice reads "Opus generation produced 0 plans" for a Fable call, contradicting the clause; S-6.13 makes it name the resolved suggestion-family model |
| `feature-plans/advisor-remediation-r1.md` AC-1 and AC-3 (attribution reads the resolved accessor, never a literal) | `templates/ai_advisor.html:1248`, `:1258`, `:1268`, `:1278`, `:2188` | kept | AC-6 keeps accessor-driven attribution and replaces the ID to name map with the Models API display name |
| `feature-plans/advisor-remediation-r1.md` AC-2 (Market Prism badge names the council model, written as claude-opus-4-8) | `templates/ai_advisor.html:1318` | changed | The badge names the resolved Opus-family model (AC-6); Q-12 asks whether it should name the model the council run recorded |
| `DECISIONS.md` DE-SYNTH-001 and `docs/features/advisor-synthesis-model-config.md` (synthesis sites default to `claude-opus-4-8`) | `ai_advisor.py:114-120`, `advisors/advisor_chat.py:385`, `advisors/lens_pipeline.py:289` | changed | Default becomes the newest active opus-line model; the override stays (AC-2, AC-5) |
| `DECISIONS.md` DE-PRISM-COUNCIL regression list and `tests/ai_advisor/test_prism_scheduling.py:1128-1145` HC-3 (council command must name `claude-opus-4-8`, never the bare alias `opus`, because an alias can change cost and behavior silently) | `prism_scheduler.py:258-268` | contradicted | G-4 and G-5 require the generic tag; AC-3 requires `--model opus`. Q-10 records the conflict |
| `feature-plans/advisor-r2-1-context-provenance.md` AC-4 (provenance `generation_model` from the accessor at call time) | Strategy Builder run JSON | changed | `generation_model` names the model whose reply the run last received, and only when no model call ran the family's resolved ID or the empty string (AC-6) |
| `feature-plans/advisor-r2-2-logic-changes.md` AC-5 and `feature-plans/advisor-r2-3-asset-swaps.md` AC-5 and AC-8 (provenance on every return path) | `app.py:7260-7265`, `app.py:7619`, `advisors/logic_change_engine.py:1043`, `:1284`, `advisors/asset_swap_engine.py:1436`, `:1600` | changed | Same change as the row above; provenance stays present on every return path |
| `feature-plans/advisor-r2-2-logic-changes.md` AC-6 and `feature-plans/advisor-r2-3-asset-swaps.md` AC-6 (LLM failure degrades to zero survivors with `NO_SURVIVORS_MESSAGE`) | `advisors/asset_swap_engine.py:93`, `advisors/logic_change_engine.py:91` and the routes' `message` field | changed | Zero survivors and the populated provenance stay; the `message` text names the reason (`model_unavailable`, `no_tool_call`, `refusal`, `max_tokens`) when generation did not run or produced nothing usable, because tool_choice auto makes a reply without a tool call a new reachable outcome (AC-4, AC-7, AC-8, AC-9) |
| `feature-plans/advisor-r2-3-asset-swaps.md` AC-12 (explicit-pair mode byte-preserved) | POST `/ai-advisor/asset-swaps/evaluate` with both tickers | kept | S-4.15 and S-6.17: explicit-pair mode makes no model call and keeps its behavior |
| `feature-plans/advisor-prompt-caching.md` AC-1 and the frontrunner site (cache breakpoints on the stable prefix) | `advisors/build_plan_generator.py:1100-1102`, `advisors/frontrunner_builder.py:940-942` | kept | S-7.11 requires the breakpoints to stay |
| `feature-plans/frontrunner-builder.md` AC-4, AC-11 and Decisions "Generation via Fable" (Fable generation, reject plus bounded retry) | `advisors/frontrunner_builder.py:84`, `:946-977` | kept | Family and the three-attempt bound stay; at the ref the loop already retries a reply without a tool call (`advisors/frontrunner_builder.py:969-971`), so only the version and the reason text change |
| `feature-plans/retirement-approval-lifecycle.md` AC-1 and Decisions "Explainer model = fable-5" | `advisors/retirement_explainer.py:122-128` | changed | The explainer stays on the Fable family; the version becomes the newest |
| `feature-plans/retirement-visibility.md` AC-9 (the Retirement Recommender imports nothing LLM-related, by an AST ban list) | `tests/app/test_retirement_producer_explainer_wiring.py:53-58` | kept | Extended: S-2.24 requires the ban to cover the new resolver as well |
| `feature-plans/market-prism-closeout-e2e-verification.completed.md` F20 (synthesis default Opus 4.8) | `advisors/lens_pipeline.py:289` | changed | Same change as DE-SYNTH-001 |
| `DECISIONS.md` DE-PRISM-SUB-AUTH-001 (council strips `ANTHROPIC_API_KEY` so it bills the subscription) | `prism_scheduler.py:271-274` | kept | S-3.2 requires it unchanged |
| `tests/security/test_dep1_requirements_pinning.py` DEP-1 (every package in `requirements.txt` carries a version specifier, a floor such as `>=` included) | `requirements.txt:1-16` | kept | Package pins are outside this plan (NB-4); Q-11 asks the operator whether G-3 reaches them |

## Acceptance Criteria (Building)

### AC-1: No versioned model ID can exist in production or deployed source
Requirement items: R-1, R-2, R-19, R-25, R-26
Rule: If a tracked file outside `tests/`, `docs/` and `feature-plans/` that is Python, under `templates/`, `static/` or `deploy/`, a PowerShell or shell script, a workflow under `.github/workflows/`, an agent file under `.claude/agents/`, or `.env.example` contains a Claude model ID made of `claude-`, a family name (opus, sonnet, haiku, fable, mythos) and a version digit, then the source-scan test shall fail and name each file and line that holds one.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-1.1 | B | the repository at the GREEN commit | the source-scan test runs | it passes; the files that hold such IDs at the ref (Trigger) hold none |
| S-1.2 | N1 | a Python module outside the exempt trees with the ID inside a string used as code | the scan runs | it fails and names that file and line |
| S-1.3 | N1 | a Python module outside the exempt trees with the ID only in a docstring or a comment | the scan runs | it fails and names that file and line |
| S-1.4 | N1 | a file under `templates/` with the ID in markup | the scan runs | it fails and names that file and line |
| S-1.5 | N1 | a file under `static/` with the ID in JavaScript | the scan runs | it fails and names that file and line |
| S-1.6 | N1 | `.env.example` with the ID in a commented example line | the scan runs | it fails and names that file and line |
| S-1.7 | N1 | one planted ID per shape: one version component (`claude-sonnet-5` shape), two components (`claude-opus-4-8` shape), a dated suffix (`claude-haiku-4-5-20251001` shape), and one per family name (opus, sonnet, haiku, fable, mythos) | the scan runs over each in turn | each one fails the scan |
| S-1.8 | N1 | the same IDs under `tests/`, `tests/fixtures/`, `docs/` (including the Python file under `docs/research/`) and `feature-plans/` | the scan runs | it passes; those trees are exempt |
| S-1.9 | N1 | the generic tag `opus` in production code, the word `Fable` in prose, and `model: opus` or `model: sonnet` in an agent file | the scan runs | it passes; none of these is a versioned ID |
| S-1.10 | N7 | any repository state | the scan runs | it only reads files; no file changes |
| S-1.11 | N1 | the ID planted, one at a time, in a `deploy/` drop-in, in `schedule_prism.ps1`, in `.github/workflows/tests.yml` and in the `model:` line of a council agent file | the scan runs | each one fails and names that file and line |

Negative coverage: N1 S-1.2, S-1.3, S-1.4, S-1.5, S-1.6, S-1.7, S-1.8, S-1.9, S-1.11 · N2 n/a: the scan refuses nothing to a user; its failure is the build gate, covered under N1 · N3 n/a: the scan computes no value · N4 n/a: the scan runs in CI and locally with no permission dimension · N5 n/a: one scan run reads a fixed file set · N6 n/a: the scan reads only the local checkout · N7 S-1.10 · N8 n/a: no edit is held open
Questions: Q-6

### AC-2: Each Messages API call runs on its family's newest model
Requirement items: R-1, R-3, R-5, R-6, R-8, R-9, R-11, R-12, R-18, R-19, R-24, R-30, R-33
Rule: When an AI Advisor action or scheduled job makes a Claude Messages API call and no override is set for its knob, the call shall name the newest active model of that site's family according to this process's last successful Models API listing, and the process shall attempt a new listing before the call whenever it has none or the last successful one is older than 24 hours.

Family per site (NB-1 keeps it): Fable family for the Run Advisor suggestions (`ai_advisor.py:1907-1908`), asset swaps (`advisors/asset_swap_engine.py:1161-1162`), logic changes (`advisors/logic_change_engine.py:880-881`), build plans (`advisors/build_plan_generator.py:1115-1116`), the frontrunner overlay (`advisors/frontrunner_builder.py:947-948`) and the retirement explainer (`advisors/retirement_explainer.py:122-123`); Opus family for chat (`advisors/advisor_chat.py:384-385`) and the lens synthesis (`advisors/lens_pipeline.py:288-289`). Family membership is the model's `line`; Q-13 states the rule when the listing carries no `line` or `lifecycle`.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-2.1 | B | the Models API lists two active fable-line models with different `created_at` | the operator clicks Run Advisor (POST `/ai-advisor/suggest`) | the Messages API request names the fable-line model with the later `created_at` |
| S-2.2 | B | the Models API lists two active opus-line models | the operator sends a chat message (POST `/ai-advisor/chat/send`) | the request names the later opus-line model |
| S-2.3 | B | as S-2.1 | the operator evaluates an asset swap in objective mode (POST `/ai-advisor/asset-swaps/evaluate` with neither ticker) | the generation request names the later fable-line model |
| S-2.4 | B | as S-2.1 | the operator evaluates a logic change (POST `/ai-advisor/logic-changes/evaluate`) | the generation request names the later fable-line model |
| S-2.5 | B | as S-2.1 | the operator runs Strategy Builder (POST `/ai-advisor/strategy-builder/run`) | the build-plan request names the later fable-line model |
| S-2.6 | B | as S-2.1 | the operator starts a Frontrunner Builder run (POST `/ai-advisor/frontrunner-builder/run`) | each overlay request names the later fable-line model |
| S-2.7 | B | as S-2.1, a retirement recommendation needing a fresh explanation | the retirement worker runs (the 03:45 tick, `app.py:1440`, or POST `/ai-advisor/retirement/run`) | the explainer request names the later fable-line model |
| S-2.8 | B | as S-2.2, at least one lens available, `DISABLE_DAEMON_LENS_PIPELINE` unset | the 03:00 lens pipeline runs (`app.py:1433`) | the synthesis request names the later opus-line model |
| S-2.9 | B | as S-2.1 | `python -m advisors.weekly_suggestions_scheduler` runs in its own process | every Strategy Builder, asset swap, logic change and frontrunner request in the run names the later fable-line model |
| S-2.10 | B | the process's last successful fable listing was at time T; a newer active fable-line model is listed afterwards | a fable call happens before T plus 24 h, then another after T plus 24 h | the first names the earlier model; the second is preceded by one new listing and names the newer model |
| S-2.11 | N1 | the newest model on the list has `line` opus and the newest fable-line model is older | a Fable-family site calls | it names the fable-line model; an Opus-family site never names a fable-line model and the reverse holds |
| S-2.12 | N1 | a listed model whose `id` text contains `fable` but whose `line` is null or another family, while other listed models carry `line` | a Fable-family site calls | that model is not chosen; family membership comes from `line` |
| S-2.13 | N1 | the fable-line model with the latest `created_at` has `lifecycle` deprecated | a Fable-family site calls | the newest active fable-line model is chosen instead |
| S-2.14 | N1 | the newest fable-line model appears only on a later page (`has_more` true on the first page) | the family is resolved | the later page is read and that model is chosen |
| S-2.15 | N1 | a fable-line model whose `created_at` is the epoch value (release date unknown) beside a dated one | the family is resolved | the dated model is chosen |
| S-2.16 | N1 | two active fable-line models with equal `created_at` | the family is resolved | the one the Models API lists first is chosen, the same one on every resolution of that list |
| S-2.17 | N5 | the fable family is unresolved in the daemon process | ten Fable-family calls start at once from request threads, the scheduler thread and the frontrunner run | one Models API listing sequence is issued and all ten calls name its result |
| S-2.18 | N6 | the family resolved earlier in this process; the Models API now times out, returns 5xx, 429, 403 or 401, or the connection fails | a call of that family happens | the call names the last resolved model; a log record at WARNING or above names the failure type only |
| S-2.19 | N6 | the 24 h re-listing is due and fails | calls continue | each names the last resolved model for the rest of the process lifetime, and the next call after a failed attempt attempts the listing again |
| S-2.20 | N7 | a call at any site | the request is sent | prompt text, system text, tool names and cache breakpoints are the same as at the ref, except the instruction to call the tool and the strict schemas of AC-7 and the request settings of AC-8 and AC-9; each site's `max_tokens` and request timeout are AC-9 request settings and may change (D-7) |
| S-2.21 | N8 | a Frontrunner Builder or weekly scheduler run in progress, holding a resolved fable model, when a re-listing in the same process picks a newer one | the run makes its later calls | every call in that run names the model it started with, so the run's provenance names one model |
| S-2.22 | N6 | a resolved, non-override model that the Messages API now answers with 404 not_found (retired, withdrawn or not served to this key) | any site of that family calls | the process discards that resolution, lists again (shared by concurrent callers), and sends the call once more on the newly resolved model if it differs; if the new listing names the same model or fails, the action degrades with `model_unavailable` as in AC-4 |
| S-2.23 | N7 | the no-trade AST scans (`tests/security/test_frontrunner_no_trade_boundary.py`, `tests/security/test_retirement_action_no_trade_boundary.py`, `tests/security/test_retirement_recommender_no_trade_boundary.py`) | a call to `composer_draft_client.save_symphony`, an order call or a `LIVE_EXECUTION` read is planted in the code that resolves models | at least one scan fails; without the plant all pass |
| S-2.25 | N1 | no listed model carries a `line` key; the list holds an ID starting with `claude-fable-` and an ID that contains `fable` but not as that prefix | a Fable-family site calls | the family is taken from the `claude-fable-` ID prefix: the prefixed model is eligible and the other is not |
| S-2.26 | N1 | listed models carry `line` but no `lifecycle` key | a Fable-family site calls | every listed fable-line model counts as active, and the one with the latest `created_at` is chosen |
| S-2.24 | N7 | the Retirement Recommender's LLM-free import ban (`tests/app/test_retirement_producer_explainer_wiring.py:53-58`, from `feature-plans/retirement-visibility.md` AC-9) | an import of the model-resolving code is planted in `advisors/retirement_recommender.py` | the ban fails; without the plant it passes |

Negative coverage: N1 S-2.11, S-2.12, S-2.13, S-2.14, S-2.15, S-2.16, S-2.25, S-2.26 · N2 covered by AC-8 · N3 covered by AC-4 · N4 n/a: resolution uses the server's own API key with no user identity, and the routes keep the global auth and CSRF hooks unchanged · N5 S-2.17 · N6 S-2.18, S-2.19, S-2.22 · N7 S-2.20, S-2.23, S-2.24 · N8 S-2.21
Questions: Q-1, Q-3, Q-13, Q-14

### AC-3: The nightly council launches with the generic tag `opus`
Requirement items: R-1, R-3, R-4, R-7
Rule: When `prism_scheduler.py` launches the Market Prism council, the `claude` command line shall pass `--model opus` and no versioned model ID.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-3.1 | B | a council run is due | the scheduler launches the council | the argument after `--model` on the launched command line is exactly `opus`, and no argument matches the AC-1 pattern |
| S-3.2 | N7 | `ANTHROPIC_API_KEY` is set in the scheduler's environment | the council subprocess starts | its environment lacks `ANTHROPIC_API_KEY` (`prism_scheduler.py:271-274`), and `--max-budget-usd 15.0` (`prism_scheduler.py:39`), `--output-format json`, `--dangerously-skip-permissions` and the prompt stay as at the ref |
| S-3.3 | N1 | `ADVISOR_SYNTHESIS_MODEL` holds a model ID | the council launches | the command line still passes `--model opus`; the API-side overrides do not reach the CLI path |
| S-3.4 | N6 | the `claude` CLI exits non-zero (for example it does not recognize the tag) | the attempt ends | the existing redacted diagnostic is printed, up to 3 attempts run (`prism_scheduler.py:33`), the scheduler exits 1 after the last failure, and the scheduler writes no MARKET_PRISM row |
| S-3.5 | N5 | a MARKET_PRISM row already exists for tonight | the scheduler starts again | the existing idempotency guard skips the run; no second council launches |
| S-3.6 | N7 | the six council agent files | the change is applied | each still reads `model: opus` at line 5 |

Negative coverage: N1 S-3.3 · N2 n/a: classifier refusals inside the CLI session are the CLI's own handling, unchanged by this plan · N3 n/a: the CLI resolves its tag itself and the scheduler computes no model value · N4 S-3.2 · N5 S-3.5 · N6 S-3.4 · N7 S-3.2, S-3.6 · N8 n/a: the scheduler holds no edit state
Questions: Q-10

### AC-4: A family never resolved degrades honestly
Requirement items: R-6, R-8, R-9, R-13
Rule: If an action needs a family's model, no override is set for its knob, and this process has never resolved that family (the listing failed, or it lists no active model of that family), then the action shall send no Messages API request and shall degrade through its existing degraded channel with the reason code `model_unavailable`, naming no model ID and no exception message.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-4.1 | N3 | fable unresolved and the listing fails | POST `/ai-advisor/suggest` | the response's error text contains `model_unavailable`; no suggestion is returned |
| S-4.2 | N3 | opus unresolved and the listing fails | POST `/ai-advisor/chat/send` | 200 with an `error` text containing `model_unavailable` |
| S-4.3 | N3 | fable unresolved and the listing fails | POST `/ai-advisor/asset-swaps/evaluate` in objective mode | 200; `survivors` 0; `message` contains `model_unavailable`; `provenance` is present with `generation_model` the empty string; no `advisor_observations` row is written |
| S-4.4 | N3 | as S-4.3 | POST `/ai-advisor/logic-changes/evaluate` | the same outcome as S-4.3 on that route |
| S-4.5 | N3 | as S-4.3 | POST `/ai-advisor/strategy-builder/run` | built-new contributes no plans, Atlas candidates are still evaluated, the response's `mode_notice` names `model_unavailable` and no model family name, and `provenance.generation_model` is the empty string |
| S-4.6 | N3 | as S-4.3 | a Frontrunner Builder run | no proposal is queued; a log record at WARNING or above names `model_unavailable` |
| S-4.7 | N3 | as S-4.3 | the retirement worker runs | each recommendation is persisted with no explanation, as at the ref when the explainer returns nothing; a log record at WARNING or above names `model_unavailable` |
| S-4.8 | N3 | opus unresolved and the listing fails | the 03:00 lens pipeline runs | one MARKET_PRISM row is written with sentiment `limited-inputs` and a rationale naming `model_unavailable`, in the shape of `advisors/lens_pipeline.py:316` |
| S-4.9 | N3 | fable unresolved and the listing fails | the weekly suggestions scheduler runs | each generator degrades as in S-4.3, S-4.4, S-4.5 and S-4.6, and the process exits normally |
| S-4.10 | N6 | the listing returns 401, 403, 429, 5xx, times out or cannot connect on first use | any site above | the same outcome as its row above; the shown reason is the code only, never the exception message |
| S-4.11 | N1 | the listing succeeds but holds no active model of the family | any site of that family | the same outcome as its row above |
| S-4.12 | N7 | any site above while unresolved | the action runs | no Messages API request is sent at all; no literal model ID is used in its place |
| S-4.13 | N5 | the family is unresolved after a failed listing | several calls of the family arrive, at once and later | concurrent calls share one listing attempt, and the next call after a failure attempts the listing again |
| S-4.14 | B | an asset swap evaluation degraded with `model_unavailable`, and the Models API has since recovered | the operator evaluates again in objective mode | the listing succeeds, the request names the newest fable-line model and the route returns the gate result as at the ref |
| S-4.15 | N7 | fable unresolved and the listing fails | POST `/ai-advisor/asset-swaps/evaluate` in explicit-pair mode (both tickers) | no model call is needed and none is made; the route returns the gate result as at the ref, with no `model_unavailable` |
| S-4.16 | N3 | an objective-mode asset swap result whose `message` carries any generation reason code (`model_unavailable`, `no_tool_call`, `refusal`, `max_tokens`, `override_model_not_found` or `override_request_rejected`) | the Asset Swaps panel renders it | the empty-state title says no swap was generated this run, not that a swap failed the gate; a zero-survivor result with no reason code keeps the title "No swap cleared the gate this run" (`static/ai_advisor_asset_swaps.js:377`) |

Negative coverage: N1 S-4.11 · N2 S-4.1, S-4.2, S-4.3, S-4.4, S-4.5, S-4.6, S-4.7, S-4.8, S-4.9 (each action refuses to run its model call) · N3 S-4.1, S-4.2, S-4.3, S-4.4, S-4.5, S-4.6, S-4.7, S-4.8, S-4.9, S-4.16 · N4 n/a: no user identity enters resolution · N5 S-4.13 · N6 S-4.10 · N7 S-4.12, S-4.15 · N8 n/a: an action holds no edit open; the page open while a family is unresolved is the AC-6 N3 and N8 case
Questions: Q-2, Q-7

### AC-5: Operator overrides still choose the model
Requirement items: R-16, R-24
Rule: When `ADVISOR_SUGGESTION_MODEL` or `ADVISOR_SYNTHESIS_MODEL` holds a non-blank value, the sites of that knob shall send that value as the model, and if the API rejects that model or the request sent to it, the action shall degrade with a message naming the variable and shall not substitute the newest model.

Knob per site: `ADVISOR_SUGGESTION_MODEL` drives the six Fable-family sites of AC-2, the Frontrunner Builder included (Q-5); `ADVISOR_SYNTHESIS_MODEL` drives chat and the lens synthesis, and not the council (S-3.3). Requests to an override model carry the same AC-8 and AC-9 settings as any other request (Q-15).

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-5.1 | N1 | `ADVISOR_SUGGESTION_MODEL` absent from the environment | a Fable-family site calls | the newest fable-line model is used (AC-2) |
| S-5.2 | N1 | `ADVISOR_SUGGESTION_MODEL` set to the empty string | a Fable-family site calls | treated as absent: the newest fable-line model is used |
| S-5.3 | N1 | `ADVISOR_SUGGESTION_MODEL` set to spaces only | a Fable-family site calls | treated as absent |
| S-5.4 | B | `ADVISOR_SUGGESTION_MODEL` set to a model ID the API serves | POST `/ai-advisor/suggest` | the request names exactly that ID |
| S-5.5 | N6 | `ADVISOR_SUGGESTION_MODEL` set to an ID the API answers with 404 not_found | POST `/ai-advisor/asset-swaps/evaluate` in objective mode | 200; `message` names `ADVISOR_SUGGESTION_MODEL` and the reason code `override_model_not_found`; no retry on the newest model; nothing is persisted |
| S-5.6 | N1 | `ADVISOR_SUGGESTION_MODEL` set to an opus-line ID | a Fable-family site calls | that ID is used verbatim; the override decides across families |
| S-5.7 | N6 | an override is set and the Models API is unreachable | a site of that knob calls | the override is used; no listing is needed for that knob |
| S-5.8 | N7 | only `ADVISOR_SYNTHESIS_MODEL` set | a Fable-family site calls | it uses the newest fable-line model; setting one knob never changes the other knob's sites |
| S-5.9 | N4 | an authenticated dashboard session | POST `/api/settings` with `ADVISOR_SUGGESTION_MODEL` in `globals` | 400, the key is rejected by `_SETTINGS_WRITE_ALLOWLIST` (`app.py:6118-6124`), and `.env` is unchanged |
| S-5.10 | B | `ADVISOR_SYNTHESIS_MODEL` set to a served ID | POST `/ai-advisor/chat/send` | the request names exactly that ID |
| S-5.11 | B | `ADVISOR_SUGGESTION_MODEL` set to a served ID | a Frontrunner Builder run | each overlay request names exactly that ID |
| S-5.12 | B | `ADVISOR_SYNTHESIS_MODEL` set to a served ID | the 03:00 lens pipeline runs | the synthesis request names exactly that ID |
| S-5.13 | B | `ADVISOR_SUGGESTION_MODEL` set to a served ID | the retirement worker runs | the explainer request names exactly that ID |
| S-5.14 | B | as S-5.13 | POST `/ai-advisor/logic-changes/evaluate` | the generation request names exactly that ID |
| S-5.15 | B | as S-5.13 | POST `/ai-advisor/strategy-builder/run` | the build-plan request names exactly that ID |
| S-5.16 | B | as S-5.13 | the weekly suggestions scheduler runs | every generation request names exactly that ID |
| S-5.17 | B | as S-5.13 | POST `/ai-advisor/asset-swaps/evaluate` in objective mode | the generation request names exactly that ID |
| S-5.18 | N6 | `ADVISOR_SYNTHESIS_MODEL` names a served model that answers 400 invalid_request to the request's effort, strict tool or fallback setting | POST `/ai-advisor/chat/send` | 200 with an `error` naming `ADVISOR_SYNTHESIS_MODEL` and the reason code `override_request_rejected`; no retry on another model |

Negative coverage: N1 S-5.1, S-5.2, S-5.3, S-5.6 · N2 S-5.5, S-5.18 · N3 covered by AC-4 · N4 S-5.9 · N5 n/a: the override is read per call with no shared state to race · N6 S-5.5, S-5.7, S-5.18 · N7 S-5.8 · N8 n/a: an environment value is not edited through the product
Questions: Q-5, Q-15

### AC-6: Badges, copy, provenance and audit rows name the model actually resolved or used
Requirement items: R-10, R-22, R-23
Rule: When the AI Advisor page renders, a run reports its model or an accept or reject decision is recorded, the page and the Strategy Builder notice shall show the Models API `display_name` of each family's resolved model (or the raw override), `provenance.generation_model` shall hold the ID from the `model` field of the last reply the run received, or, when the run made no model call, the knob's override value when one is set and otherwise the family's resolved ID or the empty string, and the audit row's `model_id` shall hold the `ADVISOR_SUGGESTION_MODEL` override value when one is set and otherwise the suggestion family's resolved ID or the empty string; text for an unresolved model reads `model unavailable`.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-6.1 | B | the daemon has resolved both families | GET `/ai-advisor` | the Asset Swaps, Logic Changes and Strategy Builder badges and the run-controls note (`templates/ai_advisor.html:1248`, `:1258`, `:1278`, `:2188`) show the fable model's `display_name`; the Chat badge and the Market Prism badge (`:1268`, `:1318`) show the opus model's `display_name` |
| S-6.2 | B | a newer model has been released and the family re-listed | GET `/ai-advisor` | the badges show the new `display_name` with no code change; no ID to name map exists in source (the map at `app.py:6527-6534` is gone) |
| S-6.3 | B | an objective-mode asset swap, a logic change or a Strategy Builder run receives a reply that produces output | the route returns | `provenance.generation_model` equals that reply's `model` field, and the result panel's model line (`static/ai_advisor_asset_swaps.js:336`, `static/ai_advisor_logic_changes.js:342`, `static/ai_advisor.js:893`) shows it |
| S-6.4 | N6 | the requested model refused and a fallback model answered (AC-8) | the run returns | `generation_model` names the fallback model from the reply's `model` field, not the requested model |
| S-6.5 | N3 | a family is unresolved | GET `/ai-advisor` | the page returns 200 and each badge of that family reads `model unavailable` |
| S-6.6 | N3 | the fable family is unresolved | an objective-mode asset swap takes an early-error path before generation (for example no Composer key) | `provenance.generation_model` is the empty string and the panel's model line reads `model unavailable` |
| S-6.7 | N6 | the Models API is slow or unreachable | GET `/ai-advisor` | the render does not wait on the Models API; badges show the last resolved name, or `model unavailable` if none |
| S-6.8 | N1 | an override names an ID the Models API list does not contain | GET `/ai-advisor` | the badge shows the raw override value |
| S-6.9 | N1 | a `display_name` containing HTML special characters | the page or panel renders it | it is escaped by template autoescape or the existing escape helpers and renders as text |
| S-6.10 | N7 | `llm_suggestions` rows written before the change | the change deploys | those rows keep the `model_id` they were written with; no backfill runs |
| S-6.11 | B | the suggestion family is resolved | POST `/ai-advisor/accept` or `/ai-advisor/reject` (`app.py:8705`, `:8738`) | the `llm_suggestions` row's `model_id` holds the resolved suggestion-family ID (at the ref it holds the synthesis-family value), and the accept route's config write and its four gates run as at the ref |
| S-6.18 | N1 | `ADVISOR_SUGGESTION_MODEL` holds a non-blank value | POST `/ai-advisor/accept` or `/ai-advisor/reject` | the row's `model_id` holds that override value verbatim, whether or not the suggestion family was ever resolved, and the accept route's config write and its four gates run as at the ref |
| S-6.12 | N6 | the Models API is unreachable and the suggestion family was never resolved | POST `/ai-advisor/accept` | the route does not wait on the Models API; the config write and gates run as at the ref and the row's `model_id` is the empty string |
| S-6.13 | B | a Strategy Builder run in which built-new produced no plans | the route returns | `mode_notice` names the resolved suggestion-family `display_name` (or `model unavailable`) and the generator's reason code, and never the word Opus for a Fable-family call (at the ref it reads "Opus generation produced 0 plans", `app.py:8091`) |
| S-6.14 | B | the daemon has just restarted, the Models API is reachable and no operator action has happened | GET `/ai-advisor` after startup completes | both families' badges show resolved display names; the page did not need a chat or generation call first |
| S-6.15 | N2 | a refusal with no fallback ends an objective-mode asset swap run | the route returns | `generation_model` names the refusing reply's `model` |
| S-6.16 | N6 | an objective-mode asset swap run ends with `no_tool_call` | the route returns | `generation_model` names the last reply's `model` |
| S-6.17 | N1 | the fable family is resolved | POST `/ai-advisor/asset-swaps/evaluate` in explicit-pair mode | no model call runs; `generation_model` holds the resolved fable-line ID, as the field held the accessor value at the ref |
| S-6.19 | N1 | `ADVISOR_SUGGESTION_MODEL` holds a non-blank value | POST `/ai-advisor/asset-swaps/evaluate` in explicit-pair mode, or an objective-mode run that ends on an early-error path before generation | no model call runs; `generation_model` holds that override value verbatim |

Negative coverage: N1 S-6.8, S-6.9, S-6.17, S-6.18, S-6.19 · N2 S-6.15 · N3 S-6.5, S-6.6 · N4 n/a: the page and the accept and reject routes keep the global auth and CSRF hooks unchanged · N5 n/a: the render and the audit write read the last resolved value and race nothing; concurrent resolution is the AC-2 N5 case · N6 S-6.4, S-6.7, S-6.12, S-6.16 · N7 S-6.10, S-6.11 · N8 S-6.5 (the page is open while the family is still unresolved)
Questions: Q-7, Q-8, Q-12

### AC-7: Tool-emitting generators do not force a tool call
Requirement items: R-6, R-9, R-15, R-20, R-33
Rule: When the asset swap, logic change, build-plan or frontrunner overlay generator calls the model, the request shall carry `tool_choice` of type `auto` and a prompt instruction to call the tool, the asset swap and logic change requests shall also carry a strict tool definition, and if a reply holds no tool call, the generator shall try again up to 3 attempts in total and then degrade with the reason code `no_tool_call`.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-7.1 | B | the model replies with the tool call | POST `/ai-advisor/asset-swaps/evaluate` in objective mode | the request's `tool_choice` type is `auto` (never `tool` or `any`) and the swap pairs are parsed as at the ref |
| S-7.2 | B | as S-7.1 | POST `/ai-advisor/logic-changes/evaluate` | as S-7.1 for logic edits |
| S-7.3 | B | as S-7.1 | POST `/ai-advisor/strategy-builder/run` | as S-7.1 for build plans |
| S-7.4 | B | as S-7.1 | a Frontrunner Builder run | as S-7.1 for overlays |
| S-7.5 | N6 | every reply is text only | POST `/ai-advisor/asset-swaps/evaluate` in objective mode | 3 requests are sent, then 200 with `survivors` 0, `message` containing `no_tool_call`, provenance present, and nothing persisted |
| S-7.6 | N6 | as S-7.5 | POST `/ai-advisor/logic-changes/evaluate` | as S-7.5 on that route |
| S-7.7 | N6 | as S-7.5 | POST `/ai-advisor/strategy-builder/run` | 3 build-plan requests, then `mode_notice` names `no_tool_call` (at the ref one reply without a tool call ends generation with `NoToolUseBlock`, `advisors/build_plan_generator.py:1143-1144`) |
| S-7.8 | N6 | as S-7.5 | a Frontrunner Builder run | 3 overlay requests as at the ref, then no proposal is queued and a log record at WARNING or above names `no_tool_call` (the reason read `NoToolUseBlock` at the ref) |
| S-7.9 | N1 | the first reply has no tool call and the second has one | any of the four generators | 2 requests; the second reply's tool input is used |
| S-7.10 | N1 | attempt 1 truncated, attempt 2 without a tool call, attempt 3 with one | build plans or overlays | 3 requests in total; the third reply is used; truncation and missing tool calls share the bound at these two sites |
| S-7.11 | N7 | the change is applied | any of the four generators sends a request | the cache breakpoints at `advisors/build_plan_generator.py:1100-1102` and `advisors/frontrunner_builder.py:940-942` are present, build plans and overlays still stop after 3 attempts, and tool input is validated as at the ref (held tickers and tradeable universe at `advisors/asset_swap_engine.py:1194-1205`, the VIX check and tree validation) |
| S-7.12 | N7 | a reply's tool input names an asset the symphony does not hold | asset swap generation | the pair is dropped as at the ref; tool input stays untrusted |
| S-7.13 | B | as S-7.1 | the weekly suggestions scheduler runs | every Strategy Builder, asset swap, logic change and frontrunner request carries `tool_choice` type `auto` |
| S-7.14 | N7 | the asset swap or logic change generator | a request is sent | its tool definition is marked strict and every object node in the tool's input schema sets `additionalProperties` to false (none does at the ref, Known fact 6), and the tool's property names and types are otherwise as at the ref |
| S-7.15 | N7 | the build-plan or frontrunner overlay generator | a request is sent | its tool definition is not marked strict and its open `rhs` and `condition` objects stay open as at the ref; the tool input is still parsed and validated as at the ref, and a reply without a tool call is still the `no_tool_call` outcome |

Negative coverage: N1 S-7.9, S-7.10 · N2 covered by AC-8 · N3 covered by AC-4 · N4 n/a: generation runs with the server's key and no user identity · N5 n/a: each call's attempts are its own, with no state shared between calls · N6 S-7.5, S-7.6, S-7.7, S-7.8 · N7 S-7.11, S-7.12, S-7.14, S-7.15 · N8 n/a: a generation call holds no edit open
Questions: Q-9

### AC-8: Refusals are retried on a fallback model, then reported
Requirement items: R-6, R-9, R-14
Rule: If the model declines a request (`stop_reason` `refusal`), then the call shall be served by Anthropic's server-side fallback where one exists for the refusal category, and otherwise the action shall degrade with the reason code `refusal` and the category when it is not null, keeping no partial output.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-8.16 | B | the primary model answers without refusing | POST `/ai-advisor/asset-swaps/evaluate` in objective mode | the request carries the server-side fallback setting, the primary reply is used as at the ref and provenance names the primary model |
| S-8.1 | N2 | the primary model refuses and a fallback model answers | POST `/ai-advisor/suggest` | suggestions from the fallback reply are returned |
| S-8.2 | N2 | as S-8.1 | POST `/ai-advisor/chat/send` | the fallback reply's text is the answer |
| S-8.3 | N2 | as S-8.1 | POST `/ai-advisor/asset-swaps/evaluate` in objective mode | candidates come from the fallback reply and provenance names the fallback model (S-6.4) |
| S-8.4 | N2 | as S-8.1 | POST `/ai-advisor/logic-changes/evaluate` | as S-8.3 on that route |
| S-8.5 | N2 | as S-8.1 | POST `/ai-advisor/strategy-builder/run` | plans come from the fallback reply and provenance names the fallback model |
| S-8.6 | N2 | as S-8.1 | a Frontrunner Builder run | the overlay comes from the fallback reply |
| S-8.7 | N2 | as S-8.1 | the retirement worker runs | the explanation is the fallback reply's text |
| S-8.8 | N2 | as S-8.1 | the 03:00 lens pipeline runs | the sentiment is parsed from the fallback reply |
| S-8.9 | N2 | a refusal whose `stop_details.category` is `reasoning_extraction`, which server-side fallback does not retry | POST `/ai-advisor/asset-swaps/evaluate` in objective mode | 200, `survivors` 0, `message` contains `refusal` and `reasoning_extraction`; nothing persisted |
| S-8.10 | N1 | a refusal whose `stop_details.category` is null | POST `/ai-advisor/chat/send` | the error text contains `refusal` with no category text |
| S-8.11 | N2 | the primary and every fallback model refuse | POST `/ai-advisor/suggest` | the error text contains `refusal` and the last refusal's category |
| S-8.12 | N2 | a refusal arrives after partial text or partial tool input | any site | the partial output is discarded; nothing from it is shown or persisted |
| S-8.13 | N7 | any of the eight sites, including each retry inside the build-plan and overlay loops | each request goes on the wire | its body carries `fallbacks` set to `default` and its headers carry the beta `server-side-fallback-2026-07-01` |
| S-8.14 | N5 | a refusal with no fallback | build-plan or overlay generation | the same request is not sent again to the same model; the generator stops with `refusal` without spending its remaining attempts |
| S-8.15 | N2 | one generator call in a weekly run is refused with no fallback | the weekly suggestions scheduler runs | that symphony or objective degrades with `refusal`, the others continue, and the process exits normally |

Negative coverage: N1 S-8.10 · N2 S-8.1, S-8.2, S-8.3, S-8.4, S-8.5, S-8.6, S-8.7, S-8.8, S-8.9, S-8.11, S-8.12, S-8.15 · N3 covered by AC-4 · N4 n/a: refusal handling has no user permission dimension · N5 S-8.14 · N6 S-8.9 (the fallback is not attempted for that category) · N7 S-8.13 · N8 n/a: no edit is held open during a call
Questions: none

### AC-9: Requests and replies fit models that always think
Requirement items: R-6, R-8, R-17, R-21, R-29, R-33
Rule: When any of the eight Messages API sites calls the model, the request shall set `effort` explicitly and send no `temperature`, `top_p`, `top_k`, `budget_tokens`, `thinking` disabled or assistant prefill, the site shall read its answer only from blocks whose type is `text` or `tool_use`, and if a reply stops at `max_tokens` at a site that has no truncation retry at the ref (suggestions, chat, explainer, lens synthesis, asset swaps, logic changes), the action shall degrade at once with the reason code `max_tokens` and show no partial answer.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-9.1 | B | the reply is a thinking block followed by a text block holding the sentiment JSON | the 03:00 lens pipeline runs | the sentiment and rationale are parsed from the text block (at the ref `advisors/lens_pipeline.py:293` reads `content[0].text`, the thinking block) |
| S-9.2 | B | a reply of thinking then text | POST `/ai-advisor/chat/send` | the answer is the text block only |
| S-9.3 | B | a reply of thinking then text | the retirement worker runs | the explanation is the text block only |
| S-9.4 | B | a structured reply preceded by thinking | POST `/ai-advisor/suggest` | suggestions are parsed |
| S-9.5 | B | a reply of thinking then the tool call | POST `/ai-advisor/asset-swaps/evaluate` in objective mode | the tool input is parsed |
| S-9.6 | B | as S-9.5 | POST `/ai-advisor/logic-changes/evaluate` | the tool input is parsed |
| S-9.7 | B | as S-9.5 | POST `/ai-advisor/strategy-builder/run` | the tool input is parsed |
| S-9.8 | B | as S-9.5 | a Frontrunner Builder run | the tool input is parsed |
| S-9.9 | N1 | a reply of a `fallback` block, thinking, then text or the tool call | chat, the explainer and each other site | the answer is read from the text or tool block only; nothing from the `fallback` block appears in the answer, although the SDK builds it as an object with a `text` attribute (Known fact 5) |
| S-9.10 | N7 | each of the eight sites | a request is sent | it carries an explicit `effort` value and none of `temperature`, `top_p`, `top_k`, `budget_tokens`, `thinking` disabled or a trailing assistant message |
| S-9.11 | N1 | a chat reply stops at `max_tokens` after partial text | POST `/ai-advisor/chat/send` | 200 with an `error` containing `max_tokens`; the partial text is not returned (at the ref `advisors/advisor_chat.py:403-419` joins whatever text blocks arrived and returns them as the answer) |
| S-9.12 | N1 | the explainer reply stops at `max_tokens` | the retirement worker runs | no explanation is stored for that recommendation; a log record at WARNING or above names `max_tokens` |
| S-9.13 | N1 | the synthesis reply stops at `max_tokens` | the 03:00 lens pipeline runs | the row is written with `limited-inputs` and a rationale naming `max_tokens` |
| S-9.14 | N1 | the suggestion reply stops at `max_tokens` | POST `/ai-advisor/suggest` | the error text names `max_tokens`; no suggestion is returned |
| S-9.15 | N1 | an asset swap or logic change reply stops at `max_tokens` | POST `/ai-advisor/asset-swaps/evaluate` in objective mode or POST `/ai-advisor/logic-changes/evaluate` | no further request is sent (these sites have no truncation retry at the ref); 200 with `survivors` 0 and `message` naming `max_tokens`; nothing persisted |
| S-9.16 | N7 | build-plan and overlay replies stop at `max_tokens` | the generator runs | it retries up to 3 attempts and ends with its reason text as at the ref (`advisors/build_plan_generator.py:1129-1134`, `advisors/frontrunner_builder.py:955-963`) |
| S-9.17 | N6 | the API answers HTTP 400 invalid_request to a request for a resolved, non-override model | any site | the site degrades through its existing D-1 path with the exception type only |
| S-9.18 | B | the real API on the droplet, one representative input per site at its chosen effort | each of the eight sites runs once | each reply has a `stop_reason` other than `max_tokens` and produces its output; verified in the PM's live merge check on the droplet, not in a development cycle (D-6) |

Negative coverage: N1 S-9.9, S-9.11, S-9.12, S-9.13, S-9.14, S-9.15 · N2 covered by AC-8 · N3 covered by AC-4 · N4 n/a: no user permission dimension · N5 n/a: each request is independent · N6 S-9.17 · N7 S-9.10, S-9.16 · N8 n/a: no edit is held open during a call
Questions: Q-4

## Rule-by-Entry-Point Matrix
Entry points: E1 POST `/ai-advisor/suggest`; E2 POST `/ai-advisor/chat/send`; E3 POST `/ai-advisor/asset-swaps/evaluate` (objective mode unless a cell says otherwise); E4 POST `/ai-advisor/logic-changes/evaluate`; E5 POST `/ai-advisor/strategy-builder/run`; E6 POST `/ai-advisor/frontrunner-builder/run`; E7 the retirement worker (03:45 tick and POST `/ai-advisor/retirement/run`); E8 the 03:00 lens pipeline; E9 `python -m advisors.weekly_suggestions_scheduler` (the weekly `python -m advisors.strategy_builder_scheduler` run is covered by the E9 weekly-run scenarios, with no column of its own, D-8); E10 `prism_scheduler.py`; E11 GET `/ai-advisor`; E12 POST `/ai-advisor/accept`; E13 POST `/ai-advisor/reject`.

| Rule | E1 | E2 | E3 | E4 | E5 | E6 | E7 | E8 | E9 | E10 | E11 | E12 | E13 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| AC-2 newest family model | S-2.1 | S-2.2 | S-2.3 | S-2.4 | S-2.5 | S-2.6 | S-2.7 | S-2.8 | S-2.9 | n/a: CLI path, covered by the AC-3 row | n/a: the page makes no model call; its badge is the AC-6 case | n/a: no model call | n/a: no model call |
| AC-3 generic tag | n/a: SDK path | n/a: SDK path | n/a: SDK path | n/a: SDK path | n/a: SDK path | n/a: SDK path | n/a: SDK path | n/a: SDK path | n/a: SDK path | S-3.1 | n/a: no launch | n/a: no launch | n/a: no launch |
| AC-4 never resolved | S-4.1 | S-4.2 | S-4.3, S-4.15, S-4.16 | S-4.4 | S-4.5 | S-4.6 | S-4.7 | S-4.8 | S-4.9 | n/a: the CLI resolves its tag and calls no Models API | n/a: the page makes no model call; its unresolved badge is the AC-6 case | n/a: no model call; its unresolved audit value is the AC-6 case | n/a: no model call; its unresolved audit value is the AC-6 case |
| AC-5 override | S-5.4 | S-5.10, S-5.18 | S-5.17, S-5.5 | S-5.14 | S-5.15 | S-5.11 | S-5.13 | S-5.12 | S-5.16 | n/a: overrides do not reach the CLI path, per the AC-3 rule | n/a: the page makes no model call; its override badge is the AC-6 case | n/a: no model call | n/a: no model call |
| AC-6 attribution | n/a: the suggest response reports no model | n/a: the chat reply reports no model | S-6.3, S-6.6, S-6.15, S-6.16, S-6.17, S-6.19 | S-6.3 | S-6.3, S-6.13 | n/a: queued proposals report no model | n/a: the explanation reports no model | n/a: the MARKET_PRISM row reports no model | n/a: weekly runs persist no model ID (Known fact 7) | n/a: the council row reports no model | S-6.1, S-6.5, S-6.7, S-6.8, S-6.14 | S-6.11, S-6.12, S-6.18 | S-6.11, S-6.18 |
| AC-7 no forced tool | n/a: structured output, no tool | n/a: plain text | S-7.1, S-7.5, S-7.14 | S-7.2, S-7.6, S-7.14 | S-7.3, S-7.7, S-7.15 | S-7.4, S-7.8, S-7.15 | n/a: plain text | n/a: plain text | S-7.13 | n/a: CLI path | n/a: no model call | n/a: no model call | n/a: no model call |
| AC-8 refusal | S-8.1, S-8.11 | S-8.2, S-8.10 | S-8.16, S-8.3, S-8.9 | S-8.4 | S-8.5 | S-8.6 | S-8.7 | S-8.8 | S-8.15 | n/a: CLI path | n/a: no model call | n/a: no model call | n/a: no model call |
| AC-9 request and reply shape | S-9.4, S-9.14 | S-9.2, S-9.11 | S-9.5, S-9.15 | S-9.6, S-9.15 | S-9.7, S-9.16 | S-9.8, S-9.16 | S-9.3, S-9.12 | S-9.1, S-9.13 | S-9.10 | n/a: CLI path | n/a: no model call | n/a: no model call | n/a: no model call |

## Not Building
Non-goals:
- NB-1: No change to which family a call site uses; the AC-2 family list is the one at the ref (R-30).
- NB-2: No new model feature beyond what the newer models require (no task budgets, no thinking display, no streaming, no Batch API).
- NB-3: No new screen, tab, button or element; only badge text, the Strategy Builder notice, the provenance line, the asset swap empty-state title and degraded message text change (R-11, R-32).
- NB-4: Package version pins in `requirements.txt` (for example `anthropic~=0.85.0` at `requirements.txt:1`) are not unpinned by this plan (excludes part of R-2; approved [PM-ASSUMED]; Q-11). DEP-1 (`tests/security/test_dep1_requirements_pinning.py:55`, `:114-117`) requires a version specifier and accepts a floor such as `>=`, so a floor-only change would satisfy both DEP-1 and G-3; raising the anthropic SDK floor to send the AC-8 settings is a Gate-2 choice the approach raises to the PM (Prerequisites).
- NB-5: removed: the Strategy Builder `mode_notice` family wording is now built by S-6.13.
- NB-6: removed: the accept and reject audit rows now record the suggestion family by S-6.11.
- NB-7: The Market Prism badge keeps reading the synthesis family's resolved model rather than a model recorded by the council run (approved [PM-ASSUMED]; Q-12).

Behavior that stays exactly as it is:
- NB-8: Advisory-only and no-trade boundaries; `tests/security/test_frontrunner_no_trade_boundary.py`, `tests/security/test_retirement_action_no_trade_boundary.py` and `tests/security/test_retirement_recommender_no_trade_boundary.py` stay green and cover the new model-resolving code (S-2.23), and no new path reaches `composer_draft_client.save_symphony`, `LIVE_EXECUTION` or an order call.
- NB-9: D-1 never-raises contracts at every site; no exception message reaches a browser-facing string (`tests/security/test_d1_error_disclosure.py` stays green).
- NB-10: Prompt-cache breakpoints (DE-ADVISOR-CACHE-001) at `advisors/build_plan_generator.py:1100-1102` and `advisors/frontrunner_builder.py:940-942`.
- NB-11: Truncation retry bounds: 3 attempts at the build-plan and overlay generators (`advisors/build_plan_generator.py:117`, `advisors/frontrunner_builder.py:94`); no truncation retry is added elsewhere.
- NB-12: Every route's JSON keys and value types; only model values, `mode_notice` text and degraded message text change.
- NB-13: The council's subscription auth, budget cap, retry, idempotency guard and F-4 row verification in `prism_scheduler.py`.
- NB-14: The six council agent files keep `model: opus` (R-25).
- NB-15: Historical rows keep their model IDs; no backfill (R-23).
- NB-16: The two override variables keep their names and stay outside `_SETTINGS_WRITE_ALLOWLIST`.

Deferred:
- NB-17: Prompt audit and effort re-tuning for the newer models (R-31, R-34); this plan sets an effort per site but runs no sweep.
- NB-18: The Playwright and harness version unpin, which lives outside this repository (R-34).
- NB-19: Keeping the droplet's Claude Code CLI current so that `opus` means the newest Opus; the CLI version is droplet operations, not repository code (Known-Unconfirmed 2).

Protected surfaces this consumes but does not change:
- NB-20: `alpha_bot_execution.py` and `math_engine.py` stay byte-unchanged (R-35).
- NB-21: The database schema; no migration (R-35). `llm_suggestions.model_id` is already `TEXT NOT NULL DEFAULT ''` (`migrations/003_llm_suggestions.sql:45`).
- NB-22: The AI Advisor accept route's config write and Gates 1 to 4, POST `/ai-advisor/proposal/approve` (the only route reaching `composer_draft_client.save_symphony`), the retirement approve and reject routes, the panic-sell route and the Managed Sleeves order path.
- NB-23: Repositories other than PlanetStopper (R-27).

## Defect Classes
| DC-n | Class (what goes wrong) | Trigger (B or N1-N8) | Sibling search (tool, query, scope) | Scenarios | Raised in review as |
|---|---|---|---|---|---|
| DC-1 | A rule enforced at one call site and missing at another: a site keeps a literal model, forced tool choice, a rejected parameter, a missing fallback setting or a positional read | N2 | grep: `messages\.(create\|parse)\|beta\.messages\|models\.(list\|retrieve)\|tool_choice\|content\[0\]\|hasattr\(block, "text"\)\|temperature\|budget_tokens\|--model` over every `*.py` outside `tests/` in PlanetStopper, including `scripts/`; stop when a widening to `anthropic` returns only judged sites | S-1.1, S-2.1, S-2.2, S-2.3, S-2.4, S-2.5, S-2.6, S-2.7, S-2.8, S-2.9, S-7.1, S-7.2, S-7.3, S-7.4, S-8.13, S-9.1, S-9.9, S-9.10 | none |
| DC-2 | An unresolved model shown or stored as a plausible value (a literal fallback ID or a stale name) | N3 | grep: `generation_model\|model_id=\|advisor_suggestion_model\|advisor_synthesis_model\|_humanize_model_name\|prov.generation_model\|mode_notice` in `app.py`, `advisors/`, `templates/`, `static/` | S-4.3, S-4.12, S-6.5, S-6.6, S-6.12 | none |
| DC-3 | A dependency failure answered with stale data where honesty is required: last-good masking a 404, or a refusal shown as an empty success | N6 | grep: every except branch around model resolution and every `stop_reason` read in `advisors/`, `ai_advisor.py`, `app.py`; read: what each returns | S-2.18, S-2.22, S-5.5, S-8.9, S-8.11 | none |
| DC-4 | A contract change not carried to every consumer: tests, fixtures, JS renders and docs still pin an ID, a display map or forced tool choice | B | grep: `claude-(opus\|sonnet\|haiku\|fable\|mythos)-[0-9]\|FABLE_MODEL\|_MODEL_DISPLAY_NAMES\|tool_choice\|NO_SURVIVORS_MESSAGE\|ADVISOR_(SUGGESTION\|SYNTHESIS)_MODEL\|mode_notice` across `tests/`, `static/`, `docs/generated/`, `docs/features/`, `README.md`, `.env.example` | S-1.1, S-6.3, S-6.13, S-7.5 | none |
| DC-5 | A narrow assertion licensing siblings: the scan covers one file type, one family or one ID shape; or `generation_model` is asserted for success only | B, N1 | read: the scan test against S-1.2 to S-1.11; read: the provenance tests against S-6.3, S-6.4, S-6.6, S-6.15, S-6.16, S-6.17 | S-1.2, S-1.7, S-1.11, S-6.15, S-6.16, S-6.17 | none |
| DC-6 | Check and write outside one lock: concurrent first uses each list the Models API | N5 | grep: every thread or executor that can reach a model call: `threading.Thread\|ThreadPoolExecutor\|schedule.every\|_FRONTRUNNER_BUILD_EXECUTOR` in `app.py` and `advisors/` | S-2.17, S-4.13 | none |
| DC-7 | Family inferred from the ID text where `line` is present, or a deprecated or retired model chosen | N1 | grep: string tests on model IDs (`startswith\|in .*id\|split("-")`) in the resolving code and its callers | S-2.11, S-2.12, S-2.13, S-2.15 | none |
| DC-8 | Partial output kept after a refusal or truncation | N1 | read: each site's reply handling after `stop_reason` is `refusal` or `max_tokens` in `advisors/` and `ai_advisor.py` | S-8.12, S-9.11, S-9.12, S-9.13, S-9.15 | none |
| DC-9 | An honesty signal computed but rendered under a wrong label: a reason code shown under a title that says the gate rejected the swap, or a Fable result labelled Opus | N3 | grep: every render of `message`, `mode_notice` and `no-survivors-title` in `static/` and `templates/` | S-4.16, S-6.13 | none |
| DC-10 | Planned example values not encoded in a test | B | read: the Worked Example against the tests that drive model resolution and the asset swap route | S-2.1, S-6.3, S-7.5 | none |

## Worked Example
The IDs below are invented for illustration; tests take theirs from captured fixtures.

The Models API returns one page: `claude-fable-8-2` (line `fable`, active, created 2027-03-01, display name "Claude Fable 8.2"), `claude-opus-9-0` (line `opus`, active, created 2027-02-10, display name "Claude Opus 9.0"), `claude-fable-8-1` (line `fable`, active, created 2026-12-01), `claude-opus-8-9` (line `opus`, deprecated, created 2026-11-01). No override is set. The operator opens the Asset Swaps tab and evaluates the objective "cut drawdown" for the symphony "Example Growth" with no tickers.
- The Asset Swaps tab badge shows "Claude Fable 8.2" followed by its existing attribution text, and the Chat tab and the Market Prism badge show "Claude Opus 9.0" (S-6.1).
- The generation request names `claude-fable-8-2` and carries `tool_choice` type `auto`, a strict tool, an explicit effort and the fallback setting (S-2.3, S-7.1, S-7.14, S-9.10, S-8.13).
- The reply, whose `model` is `claude-fable-8-2`, is a thinking block then the tool call; the route returns `provenance.generation_model` `claude-fable-8-2` and the panel line reads "Model: claude-fable-8-2" (S-9.5, S-6.3).

Main failure: the daemon restarted and the Models API answers 503 on every listing. The operator evaluates the same swap.
- No Messages API request is sent; the route returns 200 with `survivors` 0, a `message` containing `model_unavailable` and `provenance.generation_model` the empty string; the panel title says no swap was generated; the tab badge reads `model unavailable` (S-4.3, S-4.12, S-4.16, S-6.5, S-6.6).

Second failure: every reply to the generation request is text only.
- Three requests are sent, then the route returns `survivors` 0 and a `message` containing `no_tool_call`, and `generation_model` names the last reply's model (S-7.5, S-6.16).

## Fail-Closed Behaviour
| Input that now refuses | Typed refusal | Where it refuses | Scenarios |
|---|---|---|---|
| A family never resolved in the process, no override | reason code `model_unavailable` in each site's existing degraded channel | before any Messages API request, at the site's model lookup | S-4.1, S-4.2, S-4.3, S-4.4, S-4.5, S-4.6, S-4.7, S-4.8, S-4.9 |
| A resolved model the API answers with 404, when a new listing names the same model or fails | reason code `model_unavailable` | after the one re-listing and at most one repeat of the call | S-2.22 |
| An override naming a model the API does not have (404 not_found) | reason code `override_model_not_found` with the variable name | on the API's 404, with no retry on another model | S-5.5 |
| An override model that rejects the request's settings (400 invalid_request) | reason code `override_request_rejected` with the variable name | on the API's 400, with no retry on another model | S-5.18 |
| A reply with no tool call after the attempt bound | reason code `no_tool_call` | after the third attempt in each of the four generators | S-7.5, S-7.6, S-7.7, S-7.8 |
| A refusal with no fallback, or every fallback refusing | reason code `refusal` plus the category when not null | after the reply, before any parsing or persistence | S-8.9, S-8.10, S-8.11, S-8.15 |
| A reply truncated at a site without truncation retry | reason code `max_tokens` | after the reply, before the partial answer is used | S-9.11, S-9.12, S-9.13, S-9.14, S-9.15 |

Refusals the existing paths already return for the inputs this plan sends: POST `/ai-advisor/chat/send` returns 400 for a missing `message` or `artifact` and for oversize input, and 429 when rate limited (`app.py:8788-8820`); POST `/ai-advisor/retirement/run` returns 409 `market_open` or `already_running` (`app.py:8430-8434`); POST `/ai-advisor/frontrunner-builder/run` answers 200 with an error before submit when `ANTHROPIC_API_KEY` is absent (`app.py:8201-8202`); the global auth and CSRF hooks refuse unauthenticated or tokenless requests on every route. The Messages API's other statuses (401, 403, 413, 429, 500, 529, timeout) keep their existing D-1 handling, which shows the exception type only (`ai_advisor.py:1914-1930`, `advisors/advisor_chat.py:391-399`, `advisors/retirement_explainer.py:121-131`, `advisors/lens_pipeline.py:312-316`). This plan changes none of them; they stay ahead of or beside model resolution.

## Authorities Touched
| Value | Authority used | What it replaced | What proves no second source |
|---|---|---|---|
| The model ID for a Fable-family call | The Models API (`line` fable, active, latest `created_at`), or `ADVISOR_SUGGESTION_MODEL` when set | `model_config.py:26` and `advisors/frontrunner_builder.py:84` | AC-1 scan (S-1.1) and DC-1 grep |
| The model ID for an Opus-family SDK call | The Models API (`line` opus), or `ADVISOR_SYNTHESIS_MODEL` when set | `ai_advisor.py:120`, `advisors/advisor_chat.py:385`, `advisors/lens_pipeline.py:289` | AC-1 scan and DC-1 grep |
| The council model | The Claude Code CLI's own resolution of `opus` | `prism_scheduler.py:263` | S-3.1 |
| A model's display name | The Models API `display_name` | the map at `app.py:6527-6534` | S-6.2 |
| The model that produced an output | The reply's top-level `model` | the accessor value read before the call (`app.py:7261`, `app.py:7619`, `advisors/strategy_builder_engine.py:978`) | S-6.3, S-6.4, S-6.15, S-6.16 |
| The audit row's model | The suggestion family's resolved ID | `ai_advisor.resolve_advisor_model()` at `app.py:8705`, `:8738` | S-6.11 |

## Contract Changes and Consumers
| Wire value | Before | After | Backward compatible | Consumers |
|---|---|---|---|---|
| `provenance.generation_model` on the asset swap, logic change and Strategy Builder route responses | the accessor's value, always a model ID | the last reply's `model`; with no model call, the resolved ID or the empty string | yes: same key and type | `static/ai_advisor.js:893`, `static/ai_advisor_asset_swaps.js:336`, `static/ai_advisor_logic_changes.js:342` render it and must show `model unavailable` for the empty string. Not persisted (Known fact 7) |
| `message` on the asset swap and logic change routes | `NO_SURVIVORS_MESSAGE` for every empty result | the same text when the gate passed nothing, or a text naming the reason code when generation did not run or produced nothing usable | yes: same key and type | `static/ai_advisor_asset_swaps.js:377-379` (title and body; the title changes by S-4.16) and `static/ai_advisor_logic_changes.js:363-365` (neutral title "No survivors this run", body text) |
| `mode_notice` on the Strategy Builder route | "Opus generation produced 0 plans (degraded)" plus the Atlas count | the resolved suggestion-family display name (or `model unavailable`) and the reason code, plus the Atlas count | yes: same key and type | `static/ai_advisor.js` Strategy Builder render (`sb-live-mode-notice`) |
| `advisor_suggestion_model` and `advisor_synthesis_model` template values | a hard-mapped display name | the Models API `display_name`, the raw override, or `model unavailable` | yes | `templates/ai_advisor.html:1248`, `:1258`, `:1268`, `:1278`, `:1318`, `:2188` |
| `llm_suggestions.model_id` | the synthesis-family value, `claude-opus-4-8` by default | the resolved suggestion-family ID or the empty string | yes: the column defaults to the empty string | audit readers only; no render consumer |
| The council command line | `--model claude-opus-4-8` | `--model opus` | yes for the CLI | `tests/ai_advisor/test_prism_scheduling.py` HC-3 must change (Q-10) |

## Sibling Repos and Landing Order
| Repo | What it carries | Must land, then what breaks if the order is violated |
|---|---|---|
| PlanetStopper | all code, tests and docs of this plan | lands alone; nothing else must land first (G-21) |

Non-code dependency: the droplet's Claude Code CLI must be a version whose `opus` resolves to the newest Opus; owner: the PM at deploy, with the operator for any upgrade (Known-Unconfirmed 2).

## Migrations Required
none: no schema change (G-29); the only stored model field, `llm_suggestions.model_id`, already accepts the empty string (`migrations/003_llm_suggestions.sql:45`).

## Configuration and Enablement
- `ADVISOR_SUGGESTION_MODEL`: unchanged name; unset or blank now means the newest active fable-line model instead of `claude-fable-5`; it now also drives the Frontrunner Builder (Q-5).
- `ADVISOR_SYNTHESIS_MODEL`: unchanged name; unset or blank now means the newest active opus-line model instead of `claude-opus-4-8`; it does not drive the council (S-3.3).
- `.env.example:129-130`: the comment names no versioned ID.
- The 24 h re-listing interval is fixed in code, not configuration (Q-3).
- Nothing ships dark: the change is live on the daemon restart that deploys it. The PM's read of the droplet `.env` found no override lines (G-17), so production moves to the newest models on deploy.

## Breaking Changes
- Production moves from Claude Fable 5 and Claude Opus 4.8 to the newest models on deploy; output style and quality of every advisory surface may change without prompt re-tuning (NB-17).
- The Opus-family sites (chat, lens synthesis) ran without thinking on Opus 4.8 and now always think, which adds output tokens, cost and latency per call (Known-Unconfirmed 7).
- The asset swap and logic change routes run generation inside the request with a 30 s timeout per request (Known fact 9); with up to 3 attempts for a reply without a tool call (Q-9) on models that always think, the worst-case wait per click rises from about 30 s to about 90 s.
- Tests that pin the old contract must change (DC-4): `git grep -lE 'claude-fable-5|claude-opus-4-8|FABLE_MODEL|_MODEL_DISPLAY_NAMES|tool_choice' 37c8e983 -- tests/` lists 7 files, and adding the override variable names, the two accessor names, `_humanize_model_name` and the versioned-ID pattern lists 18, `tests/ai_advisor/test_prism_scheduling.py` HC-3 among them.
- The asset swap and logic change routes now say why generation produced nothing instead of always "no swap cleared the gate this run", and the Strategy Builder notice no longer says "Opus".
- Accept and reject audit rows record the suggestion family instead of the synthesis family.
- A future model release changes the production model with no deploy, by design (G-4).

## Security Considerations
- Input validation and injection: the override variables are operator-set in the droplet `.env` and are not writable through the dashboard (S-5.9). Model output stays untrusted: tool input is validated as at the ref (S-7.11, S-7.12), and strict schemas narrow it further (S-7.14).
- Authorization and access control: no route gains or loses an auth or CSRF check; the routes keep the global hooks (NB-22). The model-resolving code reaches no trade, draft-create or live-execution primitive, proven by the existing AST scans with a planted call (S-2.23), and the Retirement Recommender stays LLM-free (S-2.24).
- Data exposure: degraded messages carry reason codes and the variable name only, never exception text (S-4.10, NB-9). The council keeps stripping `ANTHROPIC_API_KEY` from its subprocess (S-3.2).
- Client-side rendering: `display_name` is text from an external API and is escaped wherever it renders (S-6.9).
- Abuse and rate limits: Models API listings are bounded to one per family per 24 h per process plus failure retries and 404 re-listings, shared across concurrent callers (S-2.17, S-2.22, S-4.13); refusals are not re-sent to the same model (S-8.14).
- Sensitive marking: none, in agreement with the header; no criterion touches security, money movement or sensitive data.

## Prerequisites
- The Models API reachable with the existing `ANTHROPIC_API_KEY`; when it is not and the process never resolved a family, actions return `model_unavailable` (AC-4).
- The droplet's Claude Code CLI recognizes `opus`; when it does not, the council attempt fails and the scheduler's bounded retry and exit 1 apply (S-3.4).
- A client able to put `fallbacks` and the `server-side-fallback-2026-07-01` beta on the wire for every site (S-8.13). The pinned anthropic 0.85.0 can: it types `effort` and strict tools, and its `create` and `parse` on either namespace carry `fallbacks` and the beta header through `extra_body` and `extra_headers` (Known fact 5). How each site sends them is a Gate-2 choice; a raised SDK floor, which DEP-1 permits (NB-4), is not required by these facts.

## Design-System Mapping
none: no new element; the existing `cap-tab-attribution` and `prism-model-badge` spans, the `no-survivors-title` element, the Strategy Builder notice and the provenance lines render different text with their existing classes.

## Definition of Done (Verification)
| AC | Scenarios | Test level | Real-entrypoint test |
|---|---|---|---|
| AC-1 | S-1.1, S-1.2, S-1.3, S-1.4, S-1.5, S-1.6, S-1.7, S-1.8, S-1.9, S-1.10, S-1.11 | unit (source scan over the real checkout plus planted temporary files) | the scan runs over the real repository tree in CI |
| AC-2 | S-2.1, S-2.2, S-2.3, S-2.4, S-2.5, S-2.6, S-2.7, S-2.8, S-2.9, S-2.10, S-2.11, S-2.12, S-2.13, S-2.14, S-2.15, S-2.16, S-2.17, S-2.18, S-2.19, S-2.20, S-2.21, S-2.22, S-2.23, S-2.24, S-2.25, S-2.26 | unit (resolution) and route integration through the Flask test client | S-2.1 to S-2.7 through the real routes and the retirement worker with the SDK client seam faked at the HTTP layer; S-2.8 through the 03:00 scheduler job; S-2.9 through `python -m advisors.weekly_suggestions_scheduler` |
| AC-3 | S-3.1, S-3.2, S-3.3, S-3.4, S-3.5, S-3.6 | unit with the subprocess captured | the scheduler's main run with the subprocess captured |
| AC-4 | S-4.1, S-4.2, S-4.3, S-4.4, S-4.5, S-4.6, S-4.7, S-4.8, S-4.9, S-4.10, S-4.11, S-4.12, S-4.13, S-4.14, S-4.15, S-4.16 | route integration and a JS render test | each route through the Flask test client with the Models API failing; S-4.16 on the rendered panel |
| AC-5 | S-5.1, S-5.2, S-5.3, S-5.4, S-5.5, S-5.6, S-5.7, S-5.8, S-5.9, S-5.10, S-5.11, S-5.12, S-5.13, S-5.14, S-5.15, S-5.16, S-5.17, S-5.18 | route integration | S-5.5, S-5.9 and S-5.18 through the real routes |
| AC-6 | S-6.1, S-6.2, S-6.3, S-6.4, S-6.5, S-6.6, S-6.7, S-6.8, S-6.9, S-6.10, S-6.11, S-6.12, S-6.13, S-6.14, S-6.15, S-6.16, S-6.17, S-6.18, S-6.19 | route integration plus a live render | GET `/ai-advisor` rendered and read; S-6.11 and S-6.12 through the real accept route; the PM's live check reads the badges on the droplet |
| AC-7 | S-7.1, S-7.2, S-7.3, S-7.4, S-7.5, S-7.6, S-7.7, S-7.8, S-7.9, S-7.10, S-7.11, S-7.12, S-7.13, S-7.14, S-7.15 | unit per generator plus route integration | S-7.5 and S-7.6 through the real routes |
| AC-8 | S-8.1, S-8.2, S-8.3, S-8.4, S-8.5, S-8.6, S-8.7, S-8.8, S-8.9, S-8.10, S-8.11, S-8.12, S-8.13, S-8.14, S-8.15, S-8.16 | unit per site plus route integration | S-8.3, S-8.9 and S-8.16 through the real asset swap route; S-8.13 asserted on the outgoing HTTP request |
| AC-9 | S-9.1, S-9.2, S-9.3, S-9.4, S-9.5, S-9.6, S-9.7, S-9.8, S-9.9, S-9.10, S-9.11, S-9.12, S-9.13, S-9.14, S-9.15, S-9.16, S-9.17, S-9.18 | unit per site, route integration and a live check | S-9.18 is the PM's live run of each site on the droplet in the live merge check, not a cycle test (D-6) |

Gates: `ruff format --check`, `ruff check`, a scoped pytest run over the touched suites (`tests/ai_advisor/`, `tests/prism_scheduler/`, `tests/security/`, `tests/app/` and each touched module's suite; never `-n auto` and never more than 4 workers), CI green on the exact PR head SHA, `/review` on the PR, then the PM's live functional check on the droplet after the review: each entry point E1 to E13 run once, the badges and the Strategy Builder notice read on the rendered page, `provenance.generation_model` read from each route response, one `llm_suggestions.model_id` read back after an accept, the council command line read from the journal, and the WARNING records of AC-4 confirmed visible in the journal.

Fixture provenance: one Models API list page is captured from the real API on the droplet, keeping one record per case it can supply (an active fable-line model, an older active fable-line model, an active opus-line model, and the `has_more` page shape) and recording whether `line` and `lifecycle` were present. Cases the live list cannot supply (deprecated newest, null `line`, epoch `created_at`, equal `created_at`, no `line` at all) are schema-derived from the public Models API reference and checked by a validator written from that reference's field list, because the SDK's model type does not declare `line` or `lifecycle` and accepts any extra key (Known fact 5). Message replies (thinking then text, thinking then tool call, text only, `max_tokens`) are captured from the real API; refusal and fallback-served replies, which cannot be triggered on demand, are schema-derived from the published refusal and fallback reference and checked by a validator written from it, never by the SDK's response types, which accept a `fallback` block as a text block. No fixture is hand-built to match the parser. Tests take model IDs from the fixture, never from a literal copied from a sample run.

## Success Measures
- In the PM's live check after deploy, every route response's `provenance.generation_model` names the newest fable-line ID the Models API lists that day, and the next `llm_suggestions` row written by an accept or reject holds the same ID (read-only query on `llm_suggestions.model_id`).
- The next nightly council and lens rows exist and are not `limited-inputs` because of `max_tokens` or `model_unavailable` (read-only query on MARKET_PRISM rows).
- The rendered AI Advisor page shows the Models API display names after a daemon restart with no operator action (PM live read).
- API spend per weekly run and per nightly council stays within the operator's existing budget (`--max-budget-usd 15.0` for the council; the spend log for the API sites); the change in Opus-site output tokens is read from the reply `usage` of the live check.

## Known-Unconfirmed and Standing Risk
1. Whether the live API accepts the structured-output request the suggestion call sends (`ai_advisor.py:1907-1913`, `parse` with `output_format`) on the newer models, since the Opus 5.5 guide lists migrating `output_format` to `output_config.format`; the SDK side is settled (Known fact 5). Settle with one live suggestion call on the droplet (S-9.18).
2. Which Opus the droplet's Claude Code CLI resolves `opus` to. Settle with a read-only `claude --version` on the droplet and the model reported in one council run's JSON output.
3. Settled: the Models API page captured for this key carries `line` and `lifecycle` on every record (`tests/fixtures/anthropic/models/page_captured.json` and `tests/fixtures/anthropic/PROVENANCE.md` on branch `model-unpin/fixtures` at 025755e0). Q-13's rule stays as the defined behavior if a later page omits them.
4. Whether today's output budgets leave room for thinking at the chosen effort: chat 1024 (`advisors/advisor_chat.py:211`), explainer 512 (`advisors/retirement_explainer.py:50`), lens synthesis 256 (`advisors/lens_pipeline.py:290`), suggestions 2048 (`ai_advisor.py:60`), asset swaps and logic changes 2048 (`advisors/asset_swap_engine.py:313`, `advisors/logic_change_engine.py:720`). S-9.18 settles it live; budget values are a Gate-2 choice.
5. Settled: `count_tokens` accepted both strict closed schemas, the asset swap tool and the logic change tool, with `additionalProperties: false` on every object node (`tests/fixtures/anthropic/PROVENANCE.md` on branch `model-unpin/fixtures` at 025755e0); the build-plan and overlay tools stay non-strict (D-5).
6. Whether `DISABLE_DAEMON_LENS_PIPELINE` is set on the droplet (`app.py:933`), which makes the lens synthesis dormant there; the criteria hold either way. Settle with a read-only grep of the droplet `.env`.
7. G-23 says spend per call is equal or lower. Prices support it (Fable 5.1 at Fable 5's price per the Fable 5.1 overview; Opus 5.5 at 4 and 20 dollars per million input and output tokens), but chat and lens synthesis ran without thinking on Opus 4.8 and now always think, and thinking is billed as output, so spend per call at those sites can rise. Settled by the `usage` read in the live check.
8. Anthropic chooses the server-side fallback model per refusal category; it can be another family or an older snapshot. Provenance names it (S-6.4); the plan does not constrain it.
9. Models API rate limits are not published per key; S-2.17, S-2.22 and S-4.13 bound the call rate instead.
10. The prism badge names the synthesis family's resolved model while the council runs whatever the CLI resolves `opus` to; they agree only while the CLI is current (Q-12, NB-7).
11. Settled: the PM confirmed that `feature-plans/model-unpin.source.md` line 29 is its own message to the spec-writer, verbatim, so G-4, G-5 and G-30 to G-32, on which AC-3 and the Q-10 supersession of HC-3 rest, are confirmed source.

## Open Questions
| Q-n | Question | Blocks (AC ids or "plan") | Proposed default | Answer |
|---|---|---|---|---|
| Q-1 | Which family does each call site use? | AC-2 | Each site keeps its current family: Fable for suggestions and generation (suggest, asset swaps, logic changes, build plans, frontrunner overlay, retirement explainer), Opus for chat, lens synthesis and the council | default approved [PM-ASSUMED] |
| Q-2 | What happens when a family was never resolved in the process? | AC-4 | Honest unavailable state with the reason code `model_unavailable`; no literal fallback ID anywhere in production code | default approved [PM-ASSUMED] |
| Q-3 | How often does a process re-resolve? | AC-2 | On first use and whenever the last successful listing is older than 24 h; the last good result is kept for the process lifetime while listings fail | default approved [PM-ASSUMED] |
| Q-4 | The newer models default to lower effort (Opus 5.5 `medium`); what effort does each site send? | AC-9 | Every site sets effort explicitly so quality does not drop silently; the value per site is chosen at Gate 2 | default approved [PM-ASSUMED] |
| Q-5 | Do the operator overrides stay, and what do they cover? | AC-5 | They stay as optional operator choice; unset or blank means newest; `ADVISOR_SUGGESTION_MODEL` also drives the Frontrunner Builder, which at the ref used its own constant (`advisors/frontrunner_builder.py:84`); neither drives the council | default approved [PM-ASSUMED] |
| Q-6 | How is a pin kept from coming back? | AC-1 | A source-scan test fails on any `claude-` family-name version-digit ID in production Python, templates, static JS, `.env.example`, `deploy/`, PowerShell and shell scripts, workflows and agent files; tests, docs, plans and fixtures exempt | default approved [PM-ASSUMED] |
| Q-7 | What text marks an unresolved model, and what does the asset swap panel title say when generation produced nothing? | AC-4, AC-6 | Badges, provenance lines and the Strategy Builder notice read `model unavailable`; ID fields hold the empty string; degraded messages carry the code `model_unavailable`; the asset swap title reads "No swap was generated this run" | default approved [PM-ASSUMED] |
| Q-8 | May a page render wait on the Models API? | AC-6 | No: the render never waits on the Models API, and after a daemon restart both badges show resolved names without any operator action | default approved [PM-ASSUMED] |
| Q-9 | What attempt bound do asset swaps and logic changes get for a reply without a tool call (they make one attempt at the ref)? | AC-7 | 3 attempts, the bound build plans and overlays already use, accepting a worst case of about 90 s per click (Breaking Changes) | default approved [PM-ASSUMED] |
| Q-10 | `DECISIONS.md` DE-PRISM-COUNCIL and the HC-3 test (`tests/ai_advisor/test_prism_scheduling.py:1128-1145`) require the pinned `claude-opus-4-8` and forbid the bare `opus` because an alias can change cost and behavior silently; G-4 and G-5 require `opus`. Which stands? | AC-3 | The operator's later instruction (G-4, G-5) supersedes HC-3; HC-3 is rewritten to assert `opus` | [PM-ASSUMED] The operator's later instruction stands: the council passes `--model opus`, and DE-PRISM-COUNCIL is superseded on this point |
| Q-11 | Does G-3 ("STOP PINNING SPECIFIC FUCKING VERSIONS OF SHIT LIKE THIS") reach package versions in `requirements.txt`? DEP-1 requires a specifier but accepts a floor such as `>=`, so floors would satisfy both | plan | No: this plan covers Claude model versions only; moving package pins to floors is a separate follow-up the operator decides | [PM-ASSUMED] Not decided in this plan: NB-4 is approved as written, package pins stay out of this plan, and the operator decides separately |
| Q-12 | Should the Market Prism badge name the model recorded by the council run instead of the synthesis family's resolved model? | AC-6 | No: the badge names the resolved opus-line model; recording the council's model is a follow-up | default approved [PM-ASSUMED] |
| Q-13 | How is family and eligibility decided if the Models API returns no `line` or no `lifecycle` to this key? | AC-2 | When no listed model carries `line`, a model belongs to a family when its ID starts with `claude-`, the family name and a hyphen; when `lifecycle` is absent, every listed model counts as active (the list omits retired models by default) | default approved [PM-ASSUMED] |
| Q-14 | What happens when a resolved model starts answering 404? | AC-2 | Discard that resolution, list again once (shared by concurrent callers), repeat the call once if the new listing names a different model, otherwise degrade with `model_unavailable` | default approved [PM-ASSUMED] |
| Q-15 | Do the effort, strict tool and fallback settings apply to an override model, which may not accept them? | AC-5 | Yes: every request carries them; an override model that rejects them degrades with `override_request_rejected` naming the variable | default approved [PM-ASSUMED] |

## Decisions
| ID | Decision | Rationale |
|---|---|---|
| D-1 | Messages API sites resolve the newest family member from the Models API at run time | The Messages API rejects every generic tag tried (G-30), so resolution is the API-side form of "the generic tag points to the latest" (G-4, G-32) |
| D-2 | The council passes the plain tag `opus` | The council runs through the Claude Code CLI on the subscription, and the CLI resolves the tag itself (G-5, G-32), so the scheduler needs no listing of its own |
| D-3 | Family membership comes from the Models API `line` field when present | The API documentation says not to infer the line from the ID (Known fact 2); Q-13 covers its absence |
| D-4 | A fallback-served reply is used and attributed to the model that answered | The reply's top-level `model` names it (Known fact 4), and provenance must name the model that produced the output (G-15) |
| D-5 | Strict tool mode applies only to the asset swap and logic change tools | PM ruling at Gate 2: strict needs closed schemas, and the build-plan and overlay tools carry deliberately open `rhs` and `condition` objects that the compiler reads and validates (Known fact 6) |
| D-6 | S-9.18 is verified in the PM's live merge check on the droplet, not in a development cycle | PM ruling at Gate 2: it needs the real API and the droplet |
| D-7 | Each site's `max_tokens` and request timeout are request settings S-2.20 allows to change | PM ruling at Gate 2: models that always think need room for thinking (Known-Unconfirmed 4) |
| D-8 | The weekly `python -m advisors.strategy_builder_scheduler` run is covered by the weekly-run scenarios (S-2.9, S-4.9, S-5.16, S-7.13, S-8.15), with no matrix column of its own | PM ruling at Gate 2: it drives the same generators as E9 |
| D-9 | Plan and Gate-2 approach (`feature-plans/approach/model-unpin.md`) approved: Q-1 to Q-9 and Q-12 to Q-15 default approved, Q-10 answered (`--model opus`, DE-PRISM-COUNCIL superseded on this point), Q-11 left to the operator with NB-4 approved as written, every other Not Building entry approved [PM-ASSUMED] | The PM approved under the project's standing autonomy directive while the operator was unavailable; the operator may override any answer |

## Docs to Update
- `docs/generated/` pages that name a versioned model or the forced tool choice: `ai_advisor.md`, `app.md`, `prism_scheduler.md`, `advisors_advisor_chat.md`, `advisors_build_plan_generator.md`, `advisors_frontrunner_builder.md`, `advisors_lens_pipeline.md`, `advisors_retirement_explainer.md`, plus the asset swap and logic change engine pages, and a new page for the model-resolving module.
- `docs/features/advisor-synthesis-model-config.md`, which documents `ADVISOR_SYNTHESIS_MODEL` with its versioned default.
- `docs/generated/INDEX.md` rows for those modules.
- `README.md` where it describes the advisor models or the override variables.
- `.env.example:129-130` (also under AC-1).
- One appended `DECISIONS.md` entry, minted at ship by the doc-writer, recording the unpin and the HC-3 supersession.
- A drafted `.claude/CLAUDE.md` Key Files update for PM approval: the `app.py`, `ai_advisor.py`, `advisors/build_plan_generator.py`, `advisors/frontrunner_*` and `prism_scheduler.py` rows name versioned models today.

## Follow-Ups
- NB-4 and Q-11: whether the operator's rule reaches package pins, which DEP-1 would allow as floors; tracked here until the operator answers Q-11.
- NB-7 and Q-12: recording the council's actual model on the MARKET_PRISM row; one backlog line.
- NB-17: prompt audit and effort sweep for the newer models; tracked here.
- NB-18: Playwright and harness unpin; harness work outside this repository.
- NB-19: a policy that keeps the droplet's Claude Code CLI current; droplet operations.
