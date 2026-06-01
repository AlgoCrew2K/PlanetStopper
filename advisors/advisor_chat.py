"""Explain-only chat backend for the AI Advisor (M5).

This module implements the chat capability (AC-4.*) for the AI Advisor.  It
explains a SPECIFIC surfaced advisor artifact — a recommendation, gate verdict,
correlation diagnostic, or swap/logic-change result — in plain language.

HARD BOUNDARY (AC-4.1 — non-negotiable):
    Chat MUST NOT issue trade directives.
    Chat MUST NOT propose, apply, or accept config / logic / asset changes.
    Chat MUST NOT generate new unvalidated recommendations.
    Chat has NO write path and NO path to accept/apply routes.
    The boundary is enforced at two layers:
      1. The system prompt instructs Claude to explain-only.
      2. This module has no import of, and no call into, any write path, trade
         path, or config-mutation surface.  Imports are read-only.

Grounding (AC-4.2):
    The caller supplies a specific ``artifact`` dict produced by M1–M4 (a
    gate verdict, correlation diagnostic, swap result, logic-change result, or
    advisor observation row).  The prompt is constructed around that artifact.
    Chat explains existing data; it does not generate new analysis.

Graceful degradation (AC-4.3):
    ``explain_artifact`` NEVER raises.  No LLM key / any LLM error →
    ``ChatResponse(answer=None, error=CHAT_UNAVAILABLE_MSG)``.
    The contract mirrors ``ai_advisor.request_suggestions``.

Claude client:
    Reuses ``ai_advisor._build_client()`` — the single client factory seam in
    this codebase.  Tests patch ``ai_advisor._build_client``.

No magic numbers — all tuneable values are named module-level constants.

Architecture constraints (project CLAUDE.md):
  - MUST NOT be imported by ``alpha_bot_execution.py`` (AC-X2).
  - No trade / Composer write endpoints touched (AC-X1).
  - No DB write path touched here — DB writes happen in the caller (Flask route)
    if an observation needs to be recorded (AC-X3); this module is pure I/O.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional

import ai_advisor

# ---------------------------------------------------------------------------
# AC-3 — Artifact scoping constants
# ---------------------------------------------------------------------------

# Allowlist of top-level field names permitted in a client-supplied artifact.
# Derived from the known M1–M4 artifact schemas:
#   M1 (diagnostic):  artifact_type, artifact_id, symphony_id, diagnostic_type,
#                     regime_context, correlation_score, pair_results, summary,
#                     timestamp, run_id, asset_a, asset_b, p_value, fdr_threshold
#   M2 (gate):        gate_verdict, score, threshold, bhy_adjusted, n_candidates,
#                     n_survivors, fold_count, haircut_rate
#   M3 (swap):        incumbent, candidate, swap_type, reason, approved, vetoed,
#                     veto_reason, objective, context
#   M4 (logic):       logic_change_type, description, before_value, after_value,
#                     impact_estimate, approval_status
#   Shared advisor observation fields: advisor_role, observation_id, created_at,
#                                      subject_type, subject_id, raw_response,
#                                      verdict, weight, symbol
CHAT_ARTIFACT_ALLOWED_FIELDS: frozenset = frozenset({
    # Identity — present on every artifact type
    "artifact_type",
    "artifact_id",
    "symphony_id",
    # M1 — diagnostic
    "diagnostic_type",
    "regime_context",
    "correlation_score",
    "pair_results",
    "summary",
    "timestamp",
    "run_id",
    "asset_a",
    "asset_b",
    "p_value",
    "fdr_threshold",
    # M2 — gate verdict
    "gate_verdict",
    "score",
    "threshold",
    "bhy_adjusted",
    "n_candidates",
    "n_survivors",
    "fold_count",
    "haircut_rate",
    # M3 — swap proposal
    "incumbent",
    "candidate",
    "swap_type",
    "reason",
    "approved",
    "vetoed",
    "veto_reason",
    "objective",
    "context",
    # M4 — logic change
    "logic_change_type",
    "description",
    "before_value",
    "after_value",
    "impact_estimate",
    "approval_status",
    # Shared advisor observation fields
    "advisor_role",
    "observation_id",
    "created_at",
    "subject_type",
    "subject_id",
    "raw_response",
    "verdict",
    "weight",
    "symbol",
})

# Maximum nesting depth for artifact field values.  We strip rather than
# recurse into nested structures — the outer allowlist is the boundary.
# Depth-1 means we scope the top level only; nested dicts are passed through
# as-is (string-bounded by CHAT_ARTIFACT_MAX_FIELD_VALUE_CHARS on serialisation).
CHAT_ARTIFACT_MAX_DEPTH: int = 2

# Maximum characters for a single string field value.  Prevents a client from
# stuffing the LLM prompt via a known allowed field (e.g. "reason": "A"*100000).
# 500 chars is sufficient for any real M1–M4 diagnostic string.
CHAT_ARTIFACT_MAX_FIELD_VALUE_CHARS: int = 500


def validate_artifact(artifact: dict) -> dict:
    """Scope a client-supplied artifact to the known M1–M4 field allowlist.

    Strips any top-level field not in CHAT_ARTIFACT_ALLOWED_FIELDS and
    truncates string values to CHAT_ARTIFACT_MAX_FIELD_VALUE_CHARS.  Returns
    a new dict; never mutates the input.  Never raises — returns {} on
    empty or all-unknown input.

    Args:
        artifact: raw dict from the client POST body.

    Returns:
        Scoped dict containing only allowed fields with bounded string values.
    """
    if not isinstance(artifact, dict):
        return {}

    scoped: dict = {}
    for key, value in artifact.items():
        if key not in CHAT_ARTIFACT_ALLOWED_FIELDS:
            # Unknown field — strip it (prompt injection defence, AC-3).
            continue
        if isinstance(value, str) and len(value) > CHAT_ARTIFACT_MAX_FIELD_VALUE_CHARS:
            # Truncate oversized string values to prevent prompt-stuffing.
            value = value[:CHAT_ARTIFACT_MAX_FIELD_VALUE_CHARS]
        scoped[key] = value

    return scoped

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model + SDK configuration
# ---------------------------------------------------------------------------

# Re-use the same model as the config advisor for consistency.
# claude-opus-4-7 (analytical reasoning over quant data, claude-api-mechanics.md §2).
_CHAT_MODEL = "claude-opus-4-7"

# Token budget for chat responses — longer than config suggestions because we
# are explaining multi-paragraph reasoning, not producing a structured list.
_CHAT_MAX_TOKENS = 1024

# Client-side timeout matching the config advisor (ai_advisor._REQUEST_TIMEOUT_SECONDS).
_CHAT_REQUEST_TIMEOUT_SECONDS = 30.0

# ---------------------------------------------------------------------------
# Response type
# ---------------------------------------------------------------------------

# Generic "chat unavailable" prefix — operator-facing, never-crashing.
# The specific error detail follows a colon in the actual error string.
CHAT_UNAVAILABLE_PREFIX = "chat unavailable"

# Full message returned when no API key is configured (AC-4.3 / AC-X4).
CHAT_UNAVAILABLE_MSG = (
    "chat unavailable: no LLM API key is configured "
    "— set ANTHROPIC_API_KEY to enable chat"
)

# Message returned on any LLM or parsing error (AC-4.3).
CHAT_ERROR_MSG_TEMPLATE = "chat unavailable: {reason}"


@dataclass
class ChatResponse:
    """Result of a single chat turn.

    Exactly one of ``answer`` and ``error`` is non-None in normal operation:
      - ``(answer=<str>, error=None)`` — successful explanation.
      - ``(answer=None, error=<str>)`` — any failure; the caller shows ``error``
        to the operator.

    An answer is always an explanation of the supplied artifact — never a trade
    directive, never a new recommendation (AC-4.1).
    """

    answer: Optional[str]
    error: Optional[str]


# ---------------------------------------------------------------------------
# System prompt — the explain-only boundary baked in
# ---------------------------------------------------------------------------

# The system prompt is the first layer of the AC-4.1 boundary.  Claude is
# instructed to EXPLAIN the artifact and NOTHING ELSE.  Every trade-directive
# and config-mutation pattern is explicitly prohibited.
#
# This text is a module-level constant so it appears in code review, diffs,
# and tests — not buried in a runtime string-builder.
_EXPLAIN_ONLY_SYSTEM_PROMPT = """\
You are an explain-only analyst for the Planet Stopper AI Advisor.

YOUR ROLE:
You EXPLAIN a specific advisor artifact (a gate verdict, correlation diagnostic,
asset-swap result, logic-change result, or advisor observation) that the operator
is looking at.  You help the operator understand WHY the gate accepted or rejected
a candidate, WHAT the correlation picture means, WHAT the regime context implies,
and what the numbers mean in plain language.

HARD CONSTRAINTS — you MUST NOT violate any of these:
1. DO NOT issue trade directives (do not buy, sell, or hold any instrument).
2. DO NOT propose, suggest, or accept any config change, logic change, or asset swap.
3. DO NOT generate new recommendations or new analysis beyond what is in the supplied artifact.
4. DO NOT suggest applying, accepting, or rejecting any advisor recommendation.
5. DO NOT reference live portfolio positions, account balances, or brokerage actions.
6. DO NOT produce new statistical analysis, backtests, or gate evaluations.

WHAT YOU SHOULD DO:
- Explain the artifact plainly and accurately.
- Clarify what the gate verdict means (ADOPT_CANDIDATE / KEEP_INCUMBENT / REJECT_VETO_FAILED).
- Explain the BHY/Yekutieli FDR correction in operator-friendly terms if asked.
- Describe what the correlation numbers or regime context means conceptually.
- Point out the caveats already included in the artifact (e.g. overfitting caveats).
- Answer follow-up questions about the artifact's data, methods, or limitations.

If you cannot answer a question solely by explaining the supplied artifact, say so
plainly and do NOT fabricate analysis.  An honest "I cannot determine that from the
supplied data" is always the correct answer when the artifact doesn't contain the
information needed.
"""


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def _build_chat_messages(question: str, artifact: dict) -> list[dict]:
    """Render the operator question + grounding artifact into a messages payload.

    The artifact is serialised to JSON and embedded in the user message so
    Claude has a concrete data anchor to explain.  The system prompt (above)
    enforces the explain-only boundary.

    Args:
        question: the operator's natural-language question.
        artifact: the specific M1–M4 artifact to explain — a plain dict
                  (gate verdict, PairResult, swap result, observation row,
                  or similar).  Must be JSON-serialisable.

    Returns:
        A ``messages`` list ready to pass to ``client.messages.create``.
    """
    artifact_json = json.dumps(artifact, default=str, indent=2)
    user_content = (
        "Here is the advisor artifact I am asking about:\n\n"
        f"```json\n{artifact_json}\n```\n\n"
        f"My question: {question}"
    )
    return [{"role": "user", "content": user_content}]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def explain_artifact(
    question: str,
    artifact: dict,
) -> ChatResponse:
    """Explain a specific advisor artifact in response to an operator question.

    EXPLAIN-ONLY: this function has no write path.  It takes a question and
    an M1–M4 artifact dict, builds a grounded prompt, calls Claude (reusing
    ``ai_advisor._build_client()``), and returns a plain-text explanation.

    Graceful degradation (AC-4.3): NEVER raises.  Any failure — missing API
    key, network error, SDK error, empty response — degrades to
    ``ChatResponse(answer=None, error=<description>)`` for the UI to show.

    Args:
        question: the operator's natural-language question about the artifact.
        artifact: a specific M1–M4 advisor artifact as a plain dict.  Examples:
                  - a ``PairResult``-like dict from correlation_diagnostic
                  - a ``CandidateGateResult``-like dict from backtest_gate_engine
                  - a swap or logic-change run-result dict
                  - an ``advisor_observations`` DB row dict
                  Must be JSON-serialisable; non-serialisable values are
                  coerced to strings via ``json.dumps(default=str)``.

    Returns:
        ``ChatResponse(answer=<explanation>, error=None)`` on success.
        ``ChatResponse(answer=None, error=<description>)`` on any failure.
    """
    # Layer 1 — build the client via the shared factory seam.
    # Tests patch ``ai_advisor._build_client``; we never fork the client.
    try:
        client = ai_advisor._build_client()
    except Exception as exc:  # noqa: BLE001 - graceful degradation contract
        logger.warning("advisor_chat: client construction failed: %s", exc)
        return ChatResponse(answer=None, error=CHAT_UNAVAILABLE_MSG)

    # Layer 2 — call the LLM.
    # We use client.messages.create (plain text response) rather than
    # client.messages.parse (structured output) because chat answers are
    # free-form explanations, not typed schemas.
    try:
        sdk_response = client.messages.create(
            model=_CHAT_MODEL,
            max_tokens=_CHAT_MAX_TOKENS,
            system=_EXPLAIN_ONLY_SYSTEM_PROMPT,
            messages=_build_chat_messages(question, artifact),
            timeout=_CHAT_REQUEST_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 - graceful degradation contract
        reason = f"{type(exc).__name__}: {exc}"
        logger.warning("advisor_chat: messages.create failed: %s", reason)
        return ChatResponse(
            answer=None,
            error=CHAT_ERROR_MSG_TEMPLATE.format(reason=f"LLM request failed ({type(exc).__name__})"),
        )

    # Layer 3 — extract the text response.
    # anthropic SDK: sdk_response.content is a list of content blocks;
    # each text block has a .text attribute.  We join all text blocks.
    try:
        text_blocks = [
            block.text
            for block in (getattr(sdk_response, "content", None) or [])
            if hasattr(block, "text") and block.text
        ]
        answer = "\n".join(text_blocks).strip()
    except Exception as exc:  # noqa: BLE001 - graceful degradation contract
        reason = f"{type(exc).__name__}: {exc}"
        logger.warning("advisor_chat: response extraction failed: %s", reason)
        return ChatResponse(
            answer=None,
            error=CHAT_ERROR_MSG_TEMPLATE.format(reason="could not extract response text"),
        )

    if not answer:
        logger.warning("advisor_chat: SDK returned an empty text response")
        return ChatResponse(
            answer=None,
            error=CHAT_ERROR_MSG_TEMPLATE.format(reason="LLM returned an empty response"),
        )

    return ChatResponse(answer=answer, error=None)
