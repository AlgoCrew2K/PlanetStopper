# M5 Chat Hardening — Feature Plan

**Branch:** `cycle/m5-chat-hardening`  
**Baseline:** `819ec34` (0-failure tree)

## Problem

The AI Advisor M5 Chat feature (`POST /ai-advisor/chat/send`) is currently broken and
has two HIGH security findings:

1. **AC-1 (BROKEN — 403s):** The dashboard JS (`static/ai_advisor_chat.js`) POSTs to
   `/ai-advisor/chat/send` without fetching `GET /api/csrf-token` first or attaching the
   `X-CSRF-Token` header. The `_csrf_before_request` hook (`app.py:138`) rejects every POST
   without the header with a 403. The chat feature is completely non-functional.

2. **AC-2 (HIGH — cost-DoS):** The chat route (`app.py:2942–2983`) has no request-body size
   cap, no input-length cap on `message` or `artifact`, and no per-client rate limit. An
   unbounded or unauthenticated flood can reach the paid Anthropic LLM API.

3. **AC-3 (HIGH — artifact scoping):** The `artifact` dict supplied by the client is passed
   directly to `explain_artifact()` with no server-side validation, field allowlisting, or
   size bounds. An attacker can inject arbitrarily large or unexpected fields that reach the
   Anthropic prompt verbatim.

## Acceptance Criteria

### AC-1 — JS CSRF Token Flow
- The `sendChatMessage` function in `ai_advisor_chat.js` MUST fetch `GET /api/csrf-token`
  and attach the returned token as `X-CSRF-Token` on the POST.
- A chat POST WITH the correct CSRF token flow returns 200.
- A chat POST WITHOUT a CSRF token (bare fetch, no header) returns 403.
- CONSERVATIVE: fetch the token once on `sendChatMessage` call (or cache it on module
  init). Do not redesign the token lifecycle.

### AC-2 — Cost-DoS Guards on `POST /ai-advisor/chat/send`
Route must enforce, in order, before reaching `explain_artifact`:
- **(a) Body size cap:** request body > `CHAT_MAX_REQUEST_BODY_BYTES` → 413 or 400
- **(b) Message length cap:** `message` field > `CHAT_MAX_MESSAGE_CHARS` → 400 JSON error
- **(c) Artifact size cap:** `artifact` JSON-serialised size > `CHAT_MAX_ARTIFACT_BYTES` → 400
- **(d) Rate limit:** > `CHAT_RATE_LIMIT_MAX_REQUESTS` requests per
  `CHAT_RATE_LIMIT_WINDOW_SECONDS` from the same client IP → 429 JSON error
- All limits are NAMED CONSTANTS only — no magic numbers.
- The paid Anthropic call is unreachable when any guard fires.

### AC-3 — Server-Side Artifact Scoping
`POST /ai-advisor/chat/send` must validate the `artifact` field server-side before it
reaches the Anthropic prompt:
- An allowlist of permitted top-level field names (covering the known M1–M4 artifact types).
- Unknown/disallowed fields are stripped (not rejected) so a future extension doesn't
  break the UI.
- Each field value is coerced to a scalar type (str/int/float/bool/None) or a bounded list
  — no nested dicts deeper than `CHAT_ARTIFACT_MAX_DEPTH` levels.
- Total validated artifact JSON size ≤ `CHAT_MAX_ARTIFACT_BYTES`.
- An artifact that reduces to empty after stripping is passed as-is (graceful — the chat
  still works, just without grounding).
- Implementation in `advisors/advisor_chat.py` as `validate_artifact(artifact: dict) -> dict`
  (a pure function; easy to unit-test independently).

### AC-4 — Boundary Preserved
- No new write/trade/config-mutation path reachable from chat.
- Output stays display-only + HTML-escaped in the JS.
- Existing `tests/ai_advisor/test_chat_engine.py` tests still pass GREEN.

### AC-5 — No Regressions
- Full-tree pytest (live tests excluded; `--deselect tests/meta/test_zero_skip_xfail_close.py::test_full_suite_reports_zero_skips_and_zero_xfails`)
  passes with 0 NEW failures vs `819ec34` baseline.

## Scope

**IN:**
- `static/ai_advisor_chat.js` — add CSRF token fetch + header injection
- `app.py` chat route — body-size cap, message/artifact length guards, rate limiter
- `advisors/advisor_chat.py` — add `validate_artifact()` pure function
- `tests/ai_advisor/test_m5_chat_hardening.py` — new RED tests (this cycle)
- `feature-plans/m5-chat-hardening.md` — this document

**OUT (flag as follow-up):**
- Grounding redesign to DB-by-id lookup (larger architecture change — defer)
- Full dashboard authentication (localhost operator surface — bigger product decision)
- Rate-limiting the whole dashboard (only the paid-LLM chat route is in scope)

## Implementation Notes

### AC-1 CSRF
The cleanest approach is to cache the CSRF token once at module-init time in `ai_advisor_chat.js`.
On DOMContentLoaded, fetch `GET /api/csrf-token` and store in a module-scoped `_csrfToken`
variable. `sendChatMessage` attaches `{ 'X-CSRF-Token': _csrfToken }` to the fetch headers.
If the token fetch fails, `_csrfToken` stays null and the POST 403s naturally.

### AC-2 Rate Limiter
A simple in-memory `{ip: deque([timestamps])}` dict (no pip dependency) is sufficient for
the localhost operator surface. The rate limiter is a `_CHAT_RATE_LIMITER` dict at module
level in `app.py` (or a small helper class). Uses `request.remote_addr` as the key.

### AC-3 Artifact Allowlist
Permitted top-level field names come from the known artifact types produced by M1–M4:
`artifact_type`, `artifact_id`, `symphony_id`, `gate_verdict`, `verdict`, `reason`,
`n_candidates`, `objective_type`, `incumbent_asset`, `candidate_asset`,
`correlation_value`, `obs_count`, `symphony_a`, `symphony_b`, `fold_count`,
`fdr_method`, `candidates`, `overfitting_score`, `regime_context`.

`validate_artifact` strips unknown keys, truncates long strings, and limits depth.
It never raises — returns a best-effort scoped dict.
