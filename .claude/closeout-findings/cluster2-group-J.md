# Cluster 2 — Group J: Chat Tab M5 (F31)
Auditor: closeout-audit-suite
Date: 2026-06-17
Evidence standard: file:line + runnable result per finding

---

## F31 — Chat explain-only boundary

**PASS**

### CHAT_ARTIFACT_ALLOWED_FIELDS

Static cite `advisors/advisor_chat.py:75-153`: `CHAT_ARTIFACT_ALLOWED_FIELDS` is a `frozenset` of 47 fields spanning M1–M4, M6, shared advisor observation fields, and Cycle-1 multi-lens additions. The set covers all documented artifact types.

### validate_artifact (defense-in-depth at route AND at explain_artifact entry)

- Route `app.py:3883`: `scoped_artifact = validate_artifact(artifact)` — strips unknown fields BEFORE passing to `explain_artifact`.
- `advisors/advisor_chat.py:370`: `artifact = validate_artifact(artifact)` — `explain_artifact` ALSO re-validates at its own entry point (defense-in-depth; adversarial cycle 2 BUG 1 fix: prevents a 100k-char field slipping through a future non-route caller).
- `validate_artifact` at `:168-201`: strips unknown fields + truncates string values to `CHAT_ARTIFACT_MAX_FIELD_VALUE_CHARS=500` chars. Never raises; returns `{}` on all-unknown input.

### explain_artifact — no write path, no suggest/backtest boundary

Static cite: `advisors/advisor_chat.py:334-419`:
- Module imports: `import ai_advisor` (for `_build_client`) only. NO import of `asset_swap_engine`, `logic_change_engine`, `strategy_builder_engine`, `database`, or any write-path module.
- **Runnable result**: `grep advisors/advisor_chat.py` for `suggest_swaps|run_backtest|save_state|insert_advisor_observation|revalidate_suggestion` = **0 lines**. Confirmed via Bash probe — no write path or trade path reachable from `explain_artifact`.

### System prompt (explain-only baked in)

`advisors/advisor_chat.py:261-296`: `_EXPLAIN_ONLY_SYSTEM_PROMPT` is a module-level constant prohibiting all 6 categories of violations (trade directives, config changes, new recommendations, apply/accept/reject, portfolio positions, new statistical analysis). Any violation would require Claude to defy a hard system-prompt constraint.

### Route wiring

- `app.py:3803`: `POST /ai-advisor/chat/send` → `ai_advisor_chat_send()`
- `app.py:3888`: `result = explain_artifact(question=message, artifact=scoped_artifact)` — the ONLY engine call in the route.
- `app.py:3804-3819`: Route docstring explicitly states "MUST NOT call insert_advisor_observation, save_state, or any mutation", "MUST NOT call the OOS re-validation gate, suggest_swaps, or run_backtest". These constraints are architectural, not just a comment.

### D-1 error contract

- `advisors/advisor_chat.py:376-378`: client construction failure → `ChatResponse(answer=None, error=CHAT_UNAVAILABLE_MSG)` — no bare exception leaked.
- `:392-400`: `messages.create` failure → `ChatResponse(answer=None, error=CHAT_ERROR_MSG_TEMPLATE.format(reason=f"LLM request failed ({type(exc).__name__})"))` — `type(exc).__name__` only.
- Route `app.py:3894`: `return jsonify({"error": result.error or "chat unavailable"})` — human-readable msg, never `str(exc)`.

### Per-client rate limiting

- `app.py:3855-3878`: sliding-window rate limiter (`CHAT_RATE_LIMIT_MAX_REQUESTS` per `CHAT_RATE_LIMIT_WINDOW_SECONDS` per IP). Memory-bounded by `CHAT_RATE_LIMITER_MAX_TRACKED_IPS`.

---

## Summary — Group J

| Feature | Status | Confidence |
|---------|--------|------------|
| F31 Chat explain-only boundary | PASS | HIGH |
| CHAT_ARTIFACT_ALLOWED_FIELDS coverage | PASS | HIGH |
| validate_artifact re-validation at explain_artifact | PASS | HIGH |
| No write path / no trade path in explain_artifact | PASS | HIGH (grep confirmed 0 hits) |
| D-1 error contract | PASS | HIGH |
| Route docstring hard constraints | PASS | HIGH |

No open questions or assumptions for Group J. The explain-only boundary is structurally enforced at two independent layers (route-level `validate_artifact` + `explain_artifact`-level re-validation + import isolation + system prompt).
