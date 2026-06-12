# Strategy Builder — Phase 4 Contract: Chat Anchoring for Strategy Proposals

**Status:** BINDING contract for the Phase-4 Toxic Pair TDD team. Where this doc
conflicts with the living doc (`strategy-builder.md`), this doc wins for Phase 4.
Phase-3 surface (`templates/ai_advisor_strategy_builder.html`, Strategy Builder
routes in `app.py`) and Phase-2 engine are FROZEN except where this contract
explicitly grants edits.

## 1. Objective

Let the operator anchor the existing AI Advisor chat (`advisors/advisor_chat.py`,
`POST /ai-advisor/chat/send`) on a Strategy Builder proposal card: a "Discuss
this proposal" affordance on each survivor/rejected card opens the chat panel
pre-anchored to that proposal's artifact, so questions like "why did this
survive the gate?" are answered grounded in THAT candidate's data.

## 2. Scope of change (exhaustive — anything else is blast-radius violation)

| Surface | Granted edit |
|---------|--------------|
| `advisors/advisor_chat.py` | ADD an M6 `strategy_proposal` artifact type: extend `CHAT_ARTIFACT_ALLOWED_FIELDS` (additive ONLY — never remove/rename existing fields) with: `template_id`, `template_params`, `tickers`, `rules_text`, `cagr`, `sharpe`, `calmar`, `max_drawdown`, `correlation_vs_live`, `blended_drawdown`, `fdr_adjusted_threshold`, `screen_verdict`, `rejected_reason`. Existing M2 fields (`gate_verdict`, `score`, `threshold`, `n_candidates`, `n_survivors`) are REUSED, not duplicated. Update the system prompt's artifact-type description so the LLM knows what a strategy proposal is. |
| `app.py` | `GET /ai-advisor/strategy-builder` route MAY be extended to serialize per-card artifact dicts into the template context (built from the stored observation row). NO new routes. NO changes to `/ai-advisor/chat/send`. |
| `templates/ai_advisor_strategy_builder.html` | ADD the "Discuss this proposal" affordance per card + JS that opens the existing chat panel with the artifact. The affordance is a NAVIGATION/UI control — it must not be styled as, or confusable with, a trade action. |
| `tests/**` | test-writer owned. |
| `feature-plans/**` | doc-writer owned. |

## 3. Hard requirements

- **HR-1 (allowlist boundary):** artifact construction happens SERVER-SIDE from
  the stored observation row. The client may send the artifact dict back to
  `/ai-advisor/chat/send` (existing pattern), and `validate_artifact` remains
  the trust boundary: unknown fields stripped, `CHAT_ARTIFACT_MAX_FIELD_VALUE_CHARS`
  (500) and `CHAT_ARTIFACT_MAX_DEPTH` (2) caps unchanged. `rules_text` longer
  than the cap is TRUNCATED server-side at render, never exempted from the cap.
- **HR-2 (explain-only):** no write path anywhere in the cycle. Chat cannot
  mutate observations, settings, or live state. `explain_artifact` never-raises
  contract preserved.
- **HR-3 (prompt-injection posture):** ticker strings and template params are
  engine-generated, but `raw_response`-derived fields are treated as untrusted;
  the existing per-field char cap is the defense — no new free-text fields
  beyond the enumerated list.
- **HR-4 (off execution path):** lazy imports preserved (AC-X2). No blocking
  I/O added to the 1-minute scheduler path.
- **HR-5 (CSRF):** `/ai-advisor/chat/send` CSRF posture unchanged (no edits to
  that route at all).
- **HR-6 (Phase-3 invariants):** the no-action-affordance test from Phase 3
  must still pass — the Discuss control is exempted ONLY if it is a chat-open
  control with no form/POST semantics of its own. test-writer must extend the
  affordance test to assert the Discuss control does not submit to any
  non-chat endpoint.

## 4. Acceptance criteria

- AC-1: `validate_artifact` accepts a well-formed M6 artifact and strips
  unknown fields; rejects (strips to identity) a malformed one; never raises.
- AC-2: Each rendered survivor AND rejected card carries a Discuss affordance
  wired to a server-built M6 artifact for that observation.
- AC-3: `explain_artifact` with an M6 artifact produces a grounded prompt
  containing template_id, gate verdict, and FDR threshold (fixture-asserted,
  LLM client mocked).
- AC-4: Full default suite zero failures (baseline: 5,933 passed / 6 skipped —
  any regression is cycle-caused by definition).
- AC-5: ruff clean on all touched files.

## 5. Team

Standard Toxic Pair TDD: test-writer (quant-test-writer) ⇄ implementer,
quant-code-reviewer + flask-dashboard-specialist reviewers, doc-writer.
Minimum 2 adversarial cycles. doc-writer drafts the CLAUDE.md advisors/ row
delta into the exit report for PM approval.

[PM-ASSUMED] Artifact field list in §2 is the PM's reading of the Phase-2/3
persisted observation shape; if the implementer finds the stored row lacks a
listed field, the field is DROPPED from the artifact (never synthesized) and
the deviation ledgered in the exit report.
