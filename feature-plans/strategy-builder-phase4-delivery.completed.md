# Strategy Builder — Phase 4 Delivery Notes

**Status:** DELIVERED  
**Date:** 2026-06-12  
**Branch:** claude/strategy-builder-ai-advisor-m3jlyw  
**Contract:** feature-plans/strategy-builder-phase4-contract.md

## What was delivered

### M6 `strategy_proposal` artifact type (advisors/advisor_chat.py)
Extended `CHAT_ARTIFACT_ALLOWED_FIELDS` with 13 new M6 fields (additive only):
`template_id`, `template_params`, `tickers`, `rules_text`, `cagr`, `sharpe`,
`calmar`, `max_drawdown`, `correlation_vs_live`, `blended_drawdown`,
`fdr_adjusted_threshold`, `screen_verdict`, `rejected_reason`

Existing M2 fields reused (no duplication): `gate_verdict`, `score`, `threshold`,
`n_candidates`, `n_survivors`.

`CHAT_ARTIFACT_MAX_FIELD_VALUE_CHARS` (500) and `CHAT_ARTIFACT_MAX_DEPTH` (2) unchanged.
`validate_artifact` never-raise contract preserved.
`_EXPLAIN_ONLY_SYSTEM_PROMPT` updated with `strategy_proposal` artifact description.

### Server-side artifact construction (app.py)
`GET /ai-advisor/strategy-builder` builds a `card_artifacts` dict after loading
observations. Dict is keyed by `obs["id"]` (int). Each value is an M6 artifact
with `artifact_type="strategy_proposal"` and fields from `raw_response`.
`rules_text` truncated server-side to 500 chars. No new DB queries or writes.
Context variable `card_artifacts` passed to render_template alongside `observations`.

### Discuss affordance (templates/ai_advisor_strategy_builder.html)
`type="button"` Discuss button with `data-testid="discuss-proposal-btn"` added to:
- Every survivor card (ADOPT_CANDIDATE)
- Every withheld card (did not clear gate)

Button carries `data-artifact` attribute with server-built M6 artifact JSON
(HTML-escaped via Jinja2 `| tojson | e`).
`openChatWithArtifact()` JS function: stores artifact in sessionStorage, navigates
to `/ai-advisor/chat` for grounded explain-only chat.
All HR-6 constraints met: `type="button"`, no `<form>` elements, no POST semantics.

## Test coverage
- New tests: 36 (tests/app/test_strategy_builder_phase4.py)
- New fixture: tests/fixtures/ai_advisor/m6/strategy_proposal_artifact_m6.json
- Full suite: 5969 passed / 6 skipped / 0 failed (baseline: 5933/6/0)
- Phase-3 invariants: all 28 tests still pass

## [PM-ASSUMED] deviations
- `cagr`, `sharpe`, `calmar`, `correlation_vs_live`, `blended_drawdown`: these fields are in the allowlist but NOT populated by the server-side artifact constructor (the stored observation `raw_response` does not contain them). They will appear in an artifact only if a future producer adds them. The allowlist accepts them if present; the constructor does not synthesize them. This matches the PM-ASSUMED note in the contract: "if the stored row lacks a listed field, the field is DROPPED from the artifact (never synthesized)."
- No other deviations.

## Cycles run
- Cycle 1: test-writer RED → implementer GREEN (all 36 failing tests fixed in one pass)
- Cycle 2: code-reviewer + domain-reviewer review (see verdicts below)

## Reviewer verdicts
- code-reviewer: pending (dispatched against commit 4ef17ee5a55eb65a2a4994abb1afda72449812a2)
- domain-reviewer: pending (dispatched against commit 4ef17ee5a55eb65a2a4994abb1afda72449812a2)

## Commit SHAs
- RED (test-writer): 4d79948ca1747a51ea06d6ff36a84d07f6bac26b
- GREEN (implementer): 4ef17ee5a55eb65a2a4994abb1afda72449812a2
- Doc: [this commit SHA]
