# advisors/retirement_explainer

> LLM explainer that turns a Cycle-2a Retirement Recommender evidence dict into a concise, plain-text explanation of *why* a candidate symphony is flagged for retirement. Explains, never decides -- read-only, no DB write, no trade/execution primitive, and structurally kept out of the approve/reject/checklist action path (operator ruling, Gate-2b).

**Source:** `advisors/retirement_explainer.py`
**Last updated:** 2026-08-26 (new module, Phase 2 Cycle 2b, `DE-RETIRE-APPROVAL-001`)

## Overview

`advisors/retirement_explainer.py` is the one LLM-touching module of the Retirement Approval Lifecycle feature. It has exactly one public function, `explain_recommendation(recommendation: dict) -> str | None`, which takes a Cycle-2a `raw_response` evidence dict (`candidate_id`/`sibling_id`/`correlation`/`ci_lower`/`ci_upper`/`candidate_composite`/`sibling_composite`/gate verdicts/`basis_label` -- see [advisors/retirement_recommender](advisors_retirement_recommender.md)'s "`raw_response` shape" section for the authoritative schema) and returns a 2-4 sentence plain-text explanation grounded strictly in that evidence, or `None` on any failure.

**Pattern: mirrors `advisors/advisor_chat.py`'s `explain_artifact` exactly** -- the same client factory seam (`ai_advisor._build_client()`), the same plain-text (not tool-use) `client.messages.create(...)` call shape, the same response text-block extraction, the same D-1 graceful-degradation contract. This is a deliberate reuse of an already-established pattern in this codebase, not a new one.

**Who calls it, and when.** The sole production caller is `app.py`'s `_retirement_recommender_tick_worker()` -- the same 03:45 off-hours scheduler tick that already calls `build_recommendations()`/`persist_recommendations()` (see [app](app.md)'s producer section). The explainer runs AFTER `build_recommendations()` and BEFORE `persist_recommendations()`, once per recommendation, stamping the result onto `rec["explanation"]` before the batch is persisted. This keeps the LLM entirely off:
- **The render path** -- `ai_advisor_tab()` only ever reads the already-persisted `explanation` key; it never calls this module.
- **The approve/reject/checklist action path** -- neither `_dispatch_retirement_decision` (the shared body of the two new approve/reject routes) nor `advisors/retirement_checklist.py` imports or calls this module. This is structurally proven, not merely asserted -- see `tests/security/test_retirement_action_no_trade_boundary.py`'s Group C (a static AST transitive call-graph walk over both new route handlers) and Group B (`retirement_checklist.py`'s own AST is scanned for any reference to the LLM seam).
- **`advisors/retirement_recommender.py` itself** -- that module is byte-unchanged this cycle (pinned by a golden-hash test); the explainer call lives entirely in the `app.py` producer orchestration, never inside `build_recommendations()`.

`explain_recommendation` is pure with respect to its input -- it never mutates the `recommendation` dict passed in. Stamping the result onto a recommendation's `raw_response` is the caller's (the tick worker's) job, not this function's.

## `explain_recommendation(recommendation: dict) -> str | None`

Three layers, each independently wrapped in its own `try`/`except Exception`, so a failure at any one layer degrades to `None` without ever raising out of the function (D-1):

1. **Client construction** -- `client = ai_advisor._build_client()`. A construction failure (e.g. missing API key) logs `retirement_explainer: client construction failed: <type(exc).__name__>` at WARNING and returns `None`.
2. **The LLM call** -- `client.messages.create(model=..., max_tokens=_EXPLAINER_MAX_TOKENS, system=_EXPLAIN_SYSTEM_PROMPT, messages=_build_explain_messages(recommendation), timeout=_EXPLAINER_REQUEST_TIMEOUT_SECONDS)`. Plain text, NOT tool-use (no `tools=` kwarg, no structured-output schema). Any exception (timeout, API error, rate limit) logs `retirement_explainer: messages.create failed: <type(exc).__name__>` and returns `None`.
3. **Response extraction** -- collects every `block.text` from `sdk_response.content` (guarded with `getattr`/`hasattr` so a malformed SDK response object degrades gracefully rather than raising an `AttributeError`), joins with newlines, strips. An extraction failure logs `retirement_explainer: response extraction failed: <type(exc).__name__>` and returns `None`. An empty/whitespace-only result (including the case where the SDK returns zero text blocks) also returns `None` -- an empty string is never returned as if it were a real explanation.

**Never logs `str(exc)`.** Every one of the three `except` clauses logs `type(exc).__name__` only -- the raw exception message is never logged, since it could carry a secret, a file path, or another internal detail not meant for the log stream. This is the same D-1 contract every other advisors module in this codebase follows.

### `_build_explain_messages(recommendation: dict) -> list[dict]`

Private helper. Serializes the whole `recommendation` dict to JSON (`json.dumps(recommendation, default=str, indent=2)`) and embeds it verbatim in a single user message, followed by a fixed instruction ("In 2-4 sentences, explain why this candidate is a retirement candidate."). Grounds the LLM strictly in the recommendation's own real fields -- never a fabricated or hardcoded evidence set -- and works unconditionally on any dict shape, including one missing optional keys (the `default=str` fallback means a non-JSON-native value, e.g. a `datetime`, never raises `TypeError` mid-serialization).

## Prompt shape

`_EXPLAIN_SYSTEM_PROMPT` sets the model up as an "explain-only analyst" and gives two hard constraints, verbatim in the system prompt:
- Ground every claim strictly in the supplied evidence -- never invent a number, metric, or fact not present in the data.
- Do not issue a trade directive of any kind -- explain the recommendation, never act on it or suggest the operator act on it through any channel other than their own manual review.

The user message is `_build_explain_messages`'s JSON-embedded evidence block plus the 2-4-sentence instruction (see above).

## Named constants -- real shipped values

| Constant | Value | Meaning |
|----------|-------|---------|
| `_EXPLAINER_MAX_TOKENS` | `512` | Token budget for one concise explanation -- smaller than `advisor_chat`'s general 1024-token chat budget, since this is a fixed 2-4-sentence answer, not open-ended multi-turn conversation. |
| `_EXPLAINER_REQUEST_TIMEOUT_SECONDS` | `30.0` | Client-side request timeout, matching this codebase's existing convention for advisory (off-execution-path) LLM calls. |

## Model

`model_config.get_advisor_suggestion_model()` -- the lightweight config-suggestion model knob (env var `ADVISOR_SUGGESTION_MODEL`, default `claude-fable-5`), NOT the heavier opus synthesis knob the Market Prism council uses. A one-off 2-4-sentence explanation over a small, well-scoped evidence dict does not need the heavier model.

## No DB write, no trade path

This module contains zero references to `database`, `sqlite3`, `insert_advisor_observation`, `retirement_decisions`, `is_advisory_only`, `alpha_bot_execution`, `composer_draft_client`, `invest_in_symphony`, or `LIVE_EXECUTION` anywhere in its source -- structurally enforced by `tests/security/test_retirement_action_no_trade_boundary.py`'s Group A (parametrized source-scan, shared across both new modules) and Group C (the transitive AST call-graph proof that neither new Flask route can reach this module's `explain_recommendation` entrypoint). It reads its input dict and returns a string or `None`; it writes nothing, anywhere, ever.

## Internal Dependencies

- `ai_advisor` -- `_build_client()` (the single sanctioned Anthropic client factory seam in this codebase; see [ai_advisor](ai_advisor.md) if present, or `app.py`'s AI Advisor section for the seam's broader usage pattern).
- `model_config` -- `get_advisor_suggestion_model()` (fable-5 default; see the `ADVISOR_SUGGESTION_MODEL` env var).
- No import of `database`, `alpha_bot_execution`, `math_engine`, or `composer_draft_client` anywhere in this module.

**Caller:** `app.py`'s `_retirement_recommender_tick_worker()` -- see [app](app.md)'s "`_run_retirement_recommender_tick()` / `_retirement_recommender_tick_worker()`" section for the full producer-orchestration wiring (per-rec try/except, defense-in-depth against a D-1 contract violation, never blocks persistence of the rest of the batch on one recommendation's explainer failure).

See `DE-RETIRE-APPROVAL-001` in `DECISIONS.md` for the full Gate-2b design record.
