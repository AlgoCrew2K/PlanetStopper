"""Retirement Explainer -- Phase 2 Cycle 2b (advisory, read-only, LLM).

Produces a concise, plain-text explanation of WHY a Cycle-2a retirement
recommendation flagged its candidate symphony: redundant with a live
sibling and the weaker performer of the pair. Explains, never decides --
this module has no write path of any kind (no advisor_observations row, no
retirement_decisions row, no bot_state write) and no trade/execution
primitive.

Pattern: mirrors advisors/advisor_chat.py's explain_artifact exactly --
the same client factory seam, the same plain-text (not tool-use)
messages.create call, the same text-block extraction, the same D-1
graceful-degradation contract. explain_recommendation is read-only and
pure with respect to its input dict (it never mutates the recommendation
passed in); stamping the result onto a recommendation's raw_response is
the PRODUCER's job (the nightly tick worker), not this function's.

Graceful degradation (D-1): explain_recommendation NEVER raises. Any
failure -- client construction, the LLM call itself, or response
extraction -- degrades to None. Only type(exc).__name__ is ever logged;
the raw exception message is never logged (it could carry a secret, a
path, or another internal detail).

No magic numbers -- all tuneable values are named module-level constants.
"""

from __future__ import annotations

import json
import logging

import ai_advisor
import model_config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model + SDK configuration
# ---------------------------------------------------------------------------

# Token budget for a single concise explanation -- smaller than the general
# advisor chat's budget (1024) since this is a fixed 2-4 sentence answer,
# not open-ended multi-turn conversation.
_EXPLAINER_MAX_TOKENS = 512

# Client-side timeout, matching this codebase's existing LLM-call convention
# for advisory (off-execution-path) calls.
_EXPLAINER_REQUEST_TIMEOUT_SECONDS = 30.0


# ---------------------------------------------------------------------------
# System prompt -- explain-only, grounded strictly in the supplied evidence
# ---------------------------------------------------------------------------

_EXPLAIN_SYSTEM_PROMPT = """\
You are an explain-only analyst for the Planet Stopper AI Advisor's
Retirement Recommender.

You are given the evidence behind ONE retirement recommendation: a flagged
pair of live symphonies where the candidate symphony is redundant with a
sibling symphony (highly correlated) and is the weaker performer of the two.

Write a concise, plain-text explanation (2-4 sentences) of WHY the
candidate is flagged as a retirement candidate: reference the correlation
strength between the two symphonies, which statistical gates it cleared,
and how its composite performance compares to the sibling's.

HARD CONSTRAINTS:
- Ground every claim strictly in the supplied evidence. Never invent a
  number, metric, or fact not present in the data.
- Do not issue a trade directive of any kind. You are explaining a
  recommendation, not acting on it or suggesting the operator act on it
  through any channel other than their own manual review.
"""


def _build_explain_messages(recommendation: dict) -> list[dict]:
    """Render the recommendation's own evidence into a messages payload.

    The recommendation dict is serialised to JSON and embedded verbatim in
    the user message so the LLM is grounded in the recommendation's actual
    fields -- never a fabricated or hardcoded evidence set. Works
    unconditionally on any dict, including one missing optional keys.
    """
    evidence_json = json.dumps(recommendation, default=str, indent=2)
    user_content = (
        "Here is the retirement-recommendation evidence:\n\n"
        f"```json\n{evidence_json}\n```\n\n"
        "In 2-4 sentences, explain why this candidate is a retirement "
        "candidate."
    )
    return [{"role": "user", "content": user_content}]


def explain_recommendation(recommendation: dict) -> str | None:
    """Return a concise plain-text explanation of a retirement recommendation.

    Args:
        recommendation: a Cycle-2a raw_response dict (candidate_id/
            sibling_id/correlation/composites/gate verdicts/basis_label,
            etc.) -- the recommendation's own evidence, and nothing else.

    Returns:
        The LLM's explanation text (stripped), or None on any failure or
        empty response -- this function never raises (D-1).
    """
    # Layer 1 -- build the client via the shared factory seam.
    try:
        client = ai_advisor._build_client()
    except Exception as exc:  # noqa: BLE001 - graceful degradation contract
        logger.warning("retirement_explainer: client construction failed: %s", type(exc).__name__)
        return None

    # Layer 2 -- call the LLM (plain text, not tool-use).
    try:
        sdk_response = client.messages.create(
            model=model_config.get_advisor_suggestion_model(),
            max_tokens=_EXPLAINER_MAX_TOKENS,
            system=_EXPLAIN_SYSTEM_PROMPT,
            messages=_build_explain_messages(recommendation),
            timeout=_EXPLAINER_REQUEST_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 - graceful degradation contract
        logger.warning("retirement_explainer: messages.create failed: %s", type(exc).__name__)
        return None

    # Layer 3 -- extract the text response (mirrors advisor_chat's pattern).
    try:
        text_blocks = [
            block.text
            for block in (getattr(sdk_response, "content", None) or [])
            if hasattr(block, "text") and block.text
        ]
        answer = "\n".join(text_blocks).strip()
    except Exception as exc:  # noqa: BLE001 - graceful degradation contract
        logger.warning("retirement_explainer: response extraction failed: %s", type(exc).__name__)
        return None

    if not answer:
        return None

    return answer
