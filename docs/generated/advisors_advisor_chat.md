# advisors/advisor_chat

> Explain-only chat backend for the AI Advisor (M5): scopes client artifacts to a known allowlist, calls Claude to explain a specific surfaced artifact in plain language, and enforces a hard boundary against any trade, write, or config-mutation path.

**Source:** `advisors/advisor_chat.py`
**Last updated:** 2026-06-10

## Overview

`advisor_chat.py` implements the Chat tab of the AI Advisor SPA. It explains a specific surfaced artifact — a gate verdict, correlation diagnostic, swap result, logic-change result, or advisor observation — in plain language. It never generates new analysis, proposes changes, or writes to any trade or config surface.

The module is structured around two hard boundaries:

1. **Explain-only (AC-4.1):** Chat MUST NOT issue trade directives, propose/apply/accept changes, or generate new unvalidated recommendations. Enforced at two layers: (a) the system prompt instructs Claude to explain-only; (b) this module imports no write path, trade path, or config-mutation surface.

2. **Artifact scoping (AC-3):** The client POST body `artifact` field is validated through `validate_artifact`, which strips any field not in `CHAT_ARTIFACT_ALLOWED_FIELDS` and truncates oversized string values. Unknown fields are silently dropped (prompt injection defense).

**Architecture constraint:** `advisor_chat` MUST NOT be imported by `alpha_bot_execution.py` (AC-X2). The import boundary is enforced by a test in the cycle-1 test suite.

## API Reference

### `explain_artifact(artifact: dict, question: str | None = None) → ChatResponse`

Explains a specific surfaced advisor artifact. The `artifact` is first scoped through `validate_artifact`; the scoped dict and optional `question` are embedded in a system-prompted Claude call.

Never raises — all failure paths return `ChatResponse(answer=None, error=<message>)`. With no `ANTHROPIC_API_KEY`, returns `CHAT_UNAVAILABLE_MSG` immediately without attempting an API call.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `artifact` | `dict` | Client-supplied artifact dict (M1–M4 or advisor observation). Unknown fields are stripped by `validate_artifact`. |
| `question` | `str \| None` | Optional user question about the artifact; embedded in the prompt if provided. |

**Returns:** `ChatResponse` — `answer` is a plain-language explanation string on success, `None` on failure; `error` is `None` on success, an operator-safe error string on failure.

**Model:** `_CHAT_MODEL` — env-configurable via `ADVISOR_LLM_MODEL` (default `claude-opus-4-8`). `max_tokens=1024`, 30-second timeout.

---

### `validate_artifact(artifact: dict) → dict`

Scopes a client-supplied artifact to the known M1–M4 field allowlist. Strips unknown top-level fields and truncates string values to `CHAT_ARTIFACT_MAX_FIELD_VALUE_CHARS` (500 chars). Returns a new dict; never mutates the input. Never raises — returns `{}` on empty or all-unknown input.

**Parameters:**
| Name | Type | Description |
|------|------|-------------|
| `artifact` | `dict` | Raw dict from the client POST body. |

**Returns:** Scoped dict containing only allowed fields with bounded string values.

---

## Constants

### `CHAT_ARTIFACT_ALLOWED_FIELDS: frozenset`

The allowlist of top-level field names permitted in a client-supplied artifact. Derived from the known M1–M4 artifact schemas. Fields not in this set are stripped by `validate_artifact`.

**Grouped by artifact type:**

| Group | Fields |
|-------|--------|
| Identity (all types) | `artifact_type`, `artifact_id`, `symphony_id` |
| M1 — diagnostic | `diagnostic_type`, `regime_context`, `correlation_score`, `pair_results`, `summary`, `timestamp`, `run_id`, `asset_a`, `asset_b`, `p_value`, `fdr_threshold` |
| M2 — gate verdict | `gate_verdict`, `score`, `threshold`, `bhy_adjusted`, `n_candidates`, `n_survivors`, `fold_count`, `haircut_rate` |
| M3 — swap proposal | `incumbent`, `candidate`, `swap_type`, `reason`, `approved`, `vetoed`, `veto_reason`, `objective`, `context` |
| M4 — logic change | `logic_change_type`, `description`, `before_value`, `after_value`, `impact_estimate`, `approval_status` |
| Shared observation | `advisor_role`, `observation_id`, `created_at`, `subject_type`, `subject_id`, `raw_response`, `verdict`, `weight`, `symbol` |
| Cycle-1 ADD_CANDIDATE | `candidate_symphony`, `lens_evidence`, `apply_guidance` |
| Cycle-1 citations | `sources`, `title`, `url`, `published`, `lens` |

**Cycle-1 additions (2026-06-10):** Nine fields added — three for the `ADD_CANDIDATE` advisory role (`candidate_symphony`, `lens_evidence`, `apply_guidance`) and six for the citation convention (`sources`, `title`, `url`, `published`, `lens`). The `sources` field is a list of citation dicts; the other five are the individual sub-fields of each citation. All survive `validate_artifact` — the depth-2 boundary passes list-of-dicts through unchanged (depth scoping applies only to top-level string truncation). See [DE-ML-001 in DECISIONS.md](../../DECISIONS.md).

### `CHAT_ARTIFACT_MAX_DEPTH: int = 2`

Maximum nesting depth for artifact field values. Nested dicts are passed through as-is (string-bounded by `CHAT_ARTIFACT_MAX_FIELD_VALUE_CHARS` on serialisation). The outer allowlist is the security boundary.

### `CHAT_ARTIFACT_MAX_FIELD_VALUE_CHARS: int = 500`

Maximum characters for a single string field value. Prevents prompt-stuffing via a known allowed field. Lists and dicts are not truncated at this layer.

### `CHAT_UNAVAILABLE_MSG: str`

Full message returned when no `ANTHROPIC_API_KEY` is configured:
`"chat unavailable: no LLM API key is configured — set ANTHROPIC_API_KEY to enable chat"`.

### `CHAT_UNAVAILABLE_PREFIX: str`

Prefix for all chat-unavailable responses: `"chat unavailable"`. Stable for frontend detection.

---

## Types

### `ChatResponse`

```python
@dataclass
class ChatResponse:
    answer: str | None   # plain-language explanation; None on error
    error: str | None    # operator-safe error message; None on success
```

---

## Internal Dependencies

- `ai_advisor` — `_build_client()` (single client factory; tests patch here)
- `anthropic` SDK — `messages.create` (non-structured; plain-text response)
- No write path, trade path, or config-mutation surface imported
