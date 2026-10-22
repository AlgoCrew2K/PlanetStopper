"""Shared pieces for tests of an LLM site driven through a real client over the HTTP fake.

A site under test builds its client through ``ai_advisor._build_client``; ``served_by``
hands it the fake's real ``anthropic.Anthropic`` so request facts are read off the
outgoing HTTP request and the SDK parses the reply. Model IDs are derived from the
captured Models page, never typed. ``reply_with_text`` builds the one derived reply
these sites need: a captured reply whose first text block carries the answer shape a
site's own prompt asks for (every other block, field and usage figure stays as captured).
"""

from __future__ import annotations

import copy
from unittest.mock import patch

from tests.llm_request.conftest import CAPTURED, message_body, newest_id

FORBIDDEN_PARAMETERS = frozenset({"temperature", "top_p", "top_k", "thinking", "budget_tokens"})
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# The SDK refuses a non-streaming request whose budget implies more than ten minutes when no
# timeout is passed (anthropic 0.85.0 _base_client): 60 * 60 * max_tokens / 128000 > 600.
SDK_NO_TIMEOUT_MAX_TOKENS = 21333

__all__ = [
    "CAPTURED",
    "FALLBACK_BETA",
    "FORBIDDEN_PARAMETERS",
    "SDK_NO_TIMEOUT_MAX_TOKENS",
    "message_body",
    "newest_id",
    "older_id",
    "refusal_reason_text",
    "reply_with_text",
    "served_by",
    "text_of",
]


def served_by(fake):
    """Patch the sites' shared client seam so they use the fake's real client."""
    return patch("ai_advisor._build_client", return_value=fake.client)


def older_id(line: str) -> str:
    """A served, active model of the line that is not its newest (for an override)."""
    eligible = sorted(
        (
            r
            for r in CAPTURED["data"]
            if r.get("line") == line and r.get("lifecycle", "active") == "active"
        ),
        key=lambda r: r["created_at"],
        reverse=True,
    )
    assert len(eligible) >= 2, f"the captured page needs two active {line}-line models"
    return eligible[1]["id"]


def text_of(body: dict) -> str:
    """The text of the reply body's text blocks, in order."""
    return "".join(b["text"] for b in body["content"] if b["type"] == "text")


def reply_with_text(name: str, text: str) -> dict:
    """The fixture body ``name`` with its (single) text block's text replaced by ``text``."""
    body = copy.deepcopy(message_body(name))
    text_blocks = [b for b in body["content"] if b["type"] == "text"]
    assert len(text_blocks) == 1, f"{name} must carry exactly one text block"
    text_blocks[0]["text"] = text
    return body


def refusal_reason_text(name: str) -> str:
    """The reason text a refusal fixture is reported as: the category when it has one."""
    category = (message_body(name).get("stop_details") or {}).get("category")
    return f"refusal: {category}" if category else "refusal"
