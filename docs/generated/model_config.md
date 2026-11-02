# model_config

> Resolves which Claude model each AI Advisor call uses: an operator override when set, otherwise the newest active model of the call's family from the Models API listing, held in memory and re-listed at most once a day.

**Source:** `model_config.py`
**Last updated:** 2026-11-02

## Overview

No production or deployed source names a versioned Claude model ID. `model_config` is the single place a model is chosen. There are two knobs, each with its own family:

| Knob | Environment override | Family chosen when unset | Used by |
|------|----------------------|--------------------------|---------|
| `Knob.SUGGESTION` | `ADVISOR_SUGGESTION_MODEL` | `fable` | config suggestions, build-plan generation, asset-swap and logic-change reasoning, frontrunner overlay generation, retirement explainer |
| `Knob.SYNTHESIS` | `ADVISOR_SYNTHESIS_MODEL` | `opus` | advisor chat explanations, the lens pipeline synthesis |

An override is used verbatim and is never replaced: if the API does not serve it, the call reports a reason code (see `llm_request`) instead of falling back to the family. Without an override, `resolve` lists models from the Models API (one request per page, page limit 1000, no SDK retries, 10 second timeout, performed while holding a lock so concurrent callers share one listing), picks the newest `created_at` record whose `line` equals the family (or, when the listing carries no `line` field at all, whose id starts with `claude-<family>-`) and whose `lifecycle` is absent or `active`, and keeps the result in memory. A successful listing is reused for 24 hours (`_RELIST_INTERVAL_SECONDS`). A failed listing keeps the previous registry and is retried by the next call.

The council that runs `prism_scheduler.py` is not resolved here: it launches `claude -p --model opus`, the Claude Code CLI's own generic tag, and the six council agent files already use `model: opus`.

The daemon resolves both knobs once at start on a daemon thread (`app._start_model_warmup`) so page badges can show display names. The accessors that used to return versioned defaults (`get_advisor_suggestion_model`, `resolve_advisor_model`) no longer exist.

## API Reference

### `Knob`
Enum whose values are the override environment variable names: `SUGGESTION = "ADVISOR_SUGGESTION_MODEL"`, `SYNTHESIS = "ADVISOR_SYNTHESIS_MODEL"`.

### `ModelChoice`
Frozen dataclass: `model_id`, `display_name`, `source` (`"override"`, `"resolved"` or `"unavailable"`), `env_var`. Property `badge_text` returns the override id, the resolved display name, or `MODEL_UNAVAILABLE_TEXT` (`"model unavailable"`).

### `resolve(knob, client) -> ModelChoice`
Never raises. Returns the override when set; else the current run's pinned choice for the family if a pinned run is active; else the registry entry, listing first when the last success is older than 24 hours. An unresolved family returns the unavailable placeholder (empty `model_id`), never a literal id.

### `peek(knob) -> ModelChoice`
The override or the cached resolution, without listing or locking. Used by page badges and by `llm_request.default_generation_model()`.

### `invalidate(knob, model_id, client) -> ModelChoice`
Called when a request got HTTP 404 for a resolved model. If the family's current entry is no longer `model_id` it returns that newer entry without listing; otherwise it drops the entry, clears the freshness stamp and lists once. Concurrent 404s share one listing.

### `pinned_run()` and `pin_run(func)`
`pinned_run` is a context manager and `pin_run` a decorator: inside one, each family keeps its first resolved model for the whole run, so a long run (a weekly scheduler job, a frontrunner build) does not change model halfway. Nested use joins the outer pin. A pinned entry replaced by a newer registry entry after a 404 is updated. Applied to `advisors.weekly_suggestions_scheduler` jobs, `advisors.strategy_builder_scheduler.run_weekly_build` and `advisors.frontrunner_builder` runs.

### `MODEL_UNAVAILABLE_TEXT`
`"model unavailable"`, the badge text when nothing resolved.

## Types
`Knob`, `ModelChoice`. Module state (`_registry`, `_last_success`, `_attempt_seq`, `_listing_lock`, `_run_pin`) is private; tests call `_reset_registry()`.

## Internal Dependencies
Standard library only. The Anthropic client is passed in by callers (`ai_advisor._build_client()`); this module never imports `anthropic`.

## Consumed By
`llm_request` (every Messages call), `ai_advisor`, `app` (warm-up, badges, accept/reject audit model id), the advisor engines and schedulers listed above.

## Unconfirmed
Which model each family resolves to on the droplet, and whether the API key can list models, cannot be confirmed from code. Settle it with a read-only call to the Models API listing from the droplet and compare with the badge text on the AI Advisor page.
