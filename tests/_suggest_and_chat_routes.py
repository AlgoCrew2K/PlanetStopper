"""Replies, request readers and route drivers shared by the suggestion, chat and audit-row tests.

Every request goes through a real ``anthropic.Anthropic`` on the in-process HTTP fake
(``tests/_anthropic_http_fake.py``), so request facts are read off the outgoing HTTP request
and the SDK's own reply parsing runs. A reply is a captured body or is derived from one by a
single named edit and re-checked by the public-reference validator in
``tests/fixtures/anthropic/schema_check.py``, so a fixture and the code that reads it cannot
agree by construction. Model IDs are read from the captured Models page, never typed.

The seam both sites share is ``ai_advisor._build_client``: ``request_suggestions`` calls it as a
module global and ``advisors.advisor_chat.explain_artifact`` calls it through the module, so one
``monkeypatch.setattr`` on ``ai_advisor`` reaches both.
"""

from __future__ import annotations

import copy
import json
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from tests._anthropic_http_fake import AnthropicHttpFake
from tests.llm_request.conftest import CAPTURED, error_outcome, message_body, newest_id
from tests.llm_request.test_message_fixture_schemas import schema_check
from tests.llm_request.test_send import models_named

FALLBACK_BETA = "server-side-fallback-2026-07-01"
FORBIDDEN_PARAMETERS = frozenset({"temperature", "top_p", "top_k", "thinking", "budget_tokens"})
CAPTURED_IDS = [record["id"] for record in CAPTURED["data"]]

# One suggestion in the shape the structured-output schema asks for. The keys are checked
# against the schema's own required list in tests/ai_advisor/test_sdk_contract.py.
SUGGESTION_ITEM = {
    "config_key": "MAX_SQUEEZE_FLOOR",
    "current_value": 0.2,
    "suggested_value": 0.18,
    "rationale": "Example rationale that cites a supplied number.",
    "risk_direction": "tightens",
    "confidence": "medium",
    "data_sufficiency": "sufficient",
}

# Invented example names (a real symphony name would be copied into code and commits).
SUGGEST_NAME = "example symphony for the model unpin tests"
SUGGEST_HASH = "exampleHash0123456789"
SUGGEST_BOT_STATE = {SUGGEST_HASH: {"name": SUGGEST_NAME, "account_uuid": "acc-example"}}
SUGGEST_CONTEXT = {
    "scope": "symphony",
    "symphony_id": SUGGEST_NAME,
    "role_framing": "",
    "suggestible_surface": [],
    "locked_vars": [],
    "optuna_evidence": {"available": False},
    "volatility_regime": {"available": False},
    "data_window": {},
    "risk_invariants": [],
    "symphony_logic": None,
}


@pytest.fixture
def anthropic_fake() -> AnthropicHttpFake:
    """A fresh in-process Anthropic API serving the captured Models page."""
    return AnthropicHttpFake.with_captured_page()


def ids_newest_first(line: str, records=None) -> list[str]:
    """Active record IDs of a line, newest release first (oracle: the listing's own dates)."""
    records = CAPTURED["data"] if records is None else records
    eligible = [
        r for r in records if r.get("line") == line and r.get("lifecycle", "active") == "active"
    ]
    return [r["id"] for r in sorted(eligible, key=lambda r: r["created_at"], reverse=True)]


def captured_id_outside(*excluded: str) -> str:
    """A captured model ID that is none of ``excluded`` (a served ID an override can name)."""
    return next(i for i in CAPTURED_IDS if i not in excluded)


def validated(body: dict, where: str) -> dict:
    problems = schema_check.check_message_file(body, where)
    assert problems == [], problems
    return body


def derive_reply(base: str, edit) -> dict:
    """A captured or derived body with one edit, re-checked against the API references."""
    body = copy.deepcopy(message_body(base))
    edit(body)
    return validated(body, f"{base} (edited)")


def text_of(body: dict) -> str:
    """The text blocks of a fixture body, the oracle for an expected answer."""
    return "".join(b["text"] for b in body["content"] if b["type"] == "text")


def _replace_text(text: str):
    def edit(body):
        for block in body["content"]:
            if block["type"] == "text":
                block["text"] = text

    return edit


def structured_reply(items: list[dict], *, stop_reason: str = "end_turn") -> dict:
    """The captured thinking-then-structured reply with its JSON text block replaced by ``items``."""

    def edit(body):
        _replace_text(json.dumps({"suggestions": items}))(body)
        body["stop_reason"] = stop_reason

    return derive_reply("thinking_structured.json", edit)


def text_reply(text: str, *, base: str = "thinking_text.json") -> dict:
    """A captured thinking-then-text reply with the text block replaced."""
    return derive_reply(base, _replace_text(text))


def fallback_reply(text: str, *, declined_id: str, served_id: str) -> dict:
    """The derived fallback-served reply, naming who declined and who answered, with new text."""

    def edit(body):
        _replace_text(text)(body)
        body["model"] = served_id
        for block in body["content"]:
            if block["type"] == "fallback":
                block["from"] = {"model": declined_id}
                block["to"] = {"model": served_id}

    return derive_reply("fallback_served_text.json", edit)


def only_message_request(fake):
    assert len(fake.message_requests) == 1, models_named(fake)
    return fake.message_requests[0]


def assert_names_no_model_or_exception_text(text: str, *exception_texts: str) -> None:
    """The reason shown to the operator is a code: no model ID and no SDK or API message."""
    for model_id in CAPTURED_IDS:
        assert model_id not in text, f"{model_id!r} appears in {text!r}"
    for fragment in exception_texts:
        assert fragment not in text, f"{fragment!r} appears in {text!r}"


def use_client(monkeypatch, fake) -> None:
    """Hand the fake's real client to both sites through their shared factory seam."""
    import ai_advisor

    monkeypatch.setattr(ai_advisor, "_build_client", lambda: fake.client)
    # The developer .env sets this and the route returns canned suggestions before any call.
    monkeypatch.delenv("DEV_ADVISOR_FIXTURE", raising=False)


def post_suggest(flask_client, symphony_id: str = SUGGEST_NAME):
    """POST /ai-advisor/suggest with the real ``request_suggestions`` and the context stubbed."""
    with ExitStack() as stack:
        stack.enter_context(patch("database.load_state", return_value=SUGGEST_BOT_STATE))
        stack.enter_context(patch("database.get_latest_autotune_run", return_value=None))
        stack.enter_context(
            patch("ai_advisor.assemble_advisor_context", return_value=SUGGEST_CONTEXT)
        )
        stack.enter_context(patch("app._compute_suggestion_gates", return_value={}))
        stack.enter_context(patch("app._enrich_suggestion_impact", return_value={}))
        return flask_client.post("/ai-advisor/suggest", json={"symphony_id": symphony_id})


__all__ = [
    "CAPTURED",
    "CAPTURED_IDS",
    "FALLBACK_BETA",
    "FORBIDDEN_PARAMETERS",
    "SUGGESTION_ITEM",
    "SUGGEST_BOT_STATE",
    "SUGGEST_CONTEXT",
    "SUGGEST_HASH",
    "SUGGEST_NAME",
    "assert_names_no_model_or_exception_text",
    "captured_id_outside",
    "derive_reply",
    "error_outcome",
    "fallback_reply",
    "ids_newest_first",
    "message_body",
    "models_named",
    "newest_id",
    "only_message_request",
    "post_suggest",
    "structured_reply",
    "text_of",
    "text_reply",
    "use_client",
    "validated",
]
