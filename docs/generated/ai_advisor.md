# ai_advisor

> Claude-backed config advisor: context assembly, structured-output Claude call, and safety gates (allowlist enforcement, risk-direction cross-check, OOS re-validation).

**Source:** `ai_advisor.py`
**Last updated:** 2026-05-27

## Overview

`ai_advisor.py` is the operator-assist config advisory surface. It is split into two cycles:

- **C1** — Context assembly + synchronous Claude call. `assemble_advisor_context` reads a curated 9-item allowlist of config values, never `os.environ`. `request_suggestions` calls Claude with structured output (`ConfigSuggestionsResponse`). Never raises — every failure degrades to `(None, error_message)`.

- **C2** — Safety gates. Three independent defense-in-depth layers on top of C1's context allowlist: `enforce_suggestion_allowlist`, `check_risk_direction_agreement`, `revalidate_suggestion_oos`.

Real-money-critical input governance: `assemble_advisor_context` never includes credentials, account IDs, safety flags, or methodology knobs. The config surface is an allowlist, not a denylist.

## API Reference

### Context Assembly

#### `assemble_advisor_context(scope: str, symphony_id: str | None = None) → dict`

Assembles the prompt-ready context blob carrying all 8 must-have prompt elements plus role framing:

1. Per-param definition + risk polarity
2. Valid range of every suggestible param
3. Optuna OOS-vs-train delta + `baseline_decision`
4. Current live value of each param
5. `locked_vars`
6. Volatility regime context
7. Data window + its limits
8. Risk invariants as hard constraints
9. Operator-assist role + task framing

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `scope` | `str` | `"symphony"` or `"global"` |
| `symphony_id` | `str \| None` | Required when `scope == "symphony"`; `None` for global scope |

**Returns:** Well-shaped context dict. Never raises; degrades gracefully when Optuna has not run.

**Raises:** `ValueError` when `scope == "symphony"` and `symphony_id` is `None`.

---

### Claude Client

#### `request_suggestions(context: dict) → tuple[ConfigSuggestionsResponse | None, str | None]`

Calls Claude's structured-output endpoint. Synchronous — blocks until the response returns or times out (30 seconds).

**Returns:** `(ConfigSuggestionsResponse, None)` on success; `(None, error_message)` on any failure. Never raises.

**Model:** `claude-opus-4-7`, `max_tokens=2048`.

An empty `suggestions` list is a valid non-error response ("no edit is well-supported").

---

### C2 Safety Gates

#### `enforce_suggestion_allowlist(suggestions: list[ConfigSuggestion]) → tuple[list[ConfigSuggestion], list[ConfigSuggestion]]`

Partitions suggestions into `(allowed, rejected)` by `config_key`. Any key not in the 9-item suggestible allowlist is routed to `rejected`. Defense-in-depth: even if Claude hallucinates a key or emits a credential, it can never reach a live config write.

**Returns:** `(allowed, rejected)` — order-preserving; every suggestion in exactly one partition.

#### `compute_risk_direction(config_key: str, current_value, suggested_value) → str`

Computes, code-side, whether a suggestion loosens or tightens risk. The engine never trusts Claude's self-reported `risk_direction`. Derives the direction from each param's documented risk polarity.

**Returns:** `"loosens"` | `"tightens"` | `"neutral"`.

#### `check_risk_direction_agreement(suggestion: ConfigSuggestion) → dict`

Cross-checks Claude's self-reported `risk_direction` against the engine's code-computed direction.

**Returns:** `{"agrees": bool, "code_direction": str, "claimed_direction": str}`.

#### `revalidate_suggestion_oos(symphony_id: str, config_key: str, suggested_value, current_strategy: dict) → dict`

Re-validates an accepted suggestion through the autotuner's OOS gate. Calls `run_simulation` twice (baseline + patched strategy) over the same history window. Pass rule is strict `>` — a tie does not pass.

The `autotuner` import is lazy (inside the function body) to avoid import-collision with the Anthropic SDK under pytest.

**Returns:** `{"passed": bool, "oos_alpha": float, "baseline_oos_alpha": float, "detail": str}`.

## Types

### Pydantic Schemas

#### `ConfigSuggestion`

| Field | Type | Description |
|-------|------|-------------|
| `config_key` | `str` | One of the 9 suggestible keys |
| `current_value` | `float \| int \| str` | Current live value |
| `suggested_value` | `float \| int \| str` | Claude's proposed value |
| `rationale` | `str` | Claude's reasoning, citing supplied numbers |
| `risk_direction` | `str` | `"loosens"` \| `"tightens"` \| `"neutral"` (Claude self-classifies; engine cross-checks) |
| `confidence` | `str` | Claude's confidence level |
| `data_sufficiency` | `str` | `"sufficient"` \| `"insufficient"` |
| `oos_status` | `str` | `"pending"` \| `"passed"` \| `"rejected"` |
| `oos_reason` | `str \| None` | OOS re-validation detail |
| `impact` | `dict` | `{"metric": "sharpe", "delta": 0.0}` |

#### `ConfigSuggestionsResponse`

| Field | Type | Description |
|-------|------|-------------|
| `suggestions` | `list[ConfigSuggestion]` | Zero or more suggestions. Empty list = valid abstention |

### Suggestible Config Surface

The 9-item allowlist: 7 Optuna search-space keys + `MAX_SQUEEZE_FLOOR`.

| Key | Risk Polarity |
|-----|---------------|
| `TRIGGER_THRESHOLD_PCT` | raising loosens risk |
| `TAKE_PROFIT_MC_PCT` | raising tightens risk (inverted) |
| `VWAP_CROSS_HWM_PCT` | raising loosens risk |
| `VWAP_BLEED_MULTIPLIER` | raising loosens risk |
| `VWAP_BLEED_TICKS` | raising loosens risk |
| `PARABOLIC_VELOCITY_THRESHOLD` | raising loosens risk |
| `MAX_PARABOLIC_SQUEEZE` | raising loosens risk |
| `MAX_SQUEEZE_FLOOR` | raising loosens risk |

## Internal Dependencies

- `database` — `get_latest_autotune_run`, `get_symphony_strategy`, `load_state`, `normalize_name`
- `symphony_logic` — `get_condensed_logic`
- `autotuner` — `run_simulation`, `calculate_historical_deviation` (lazy import in `revalidate_suggestion_oos`)
- `synthetic_history` — `generate_synthetic_history` (lazy import)
- `anthropic` SDK — `messages.parse` with structured output
- `pydantic` — `ConfigSuggestion`, `ConfigSuggestionsResponse`
