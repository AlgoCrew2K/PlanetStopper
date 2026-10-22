"""Reply classification and block reads, against real and schema-derived replies.

Each reply is a fixture body served through the HTTP fake and parsed by the
SDK, so the helper reads the same objects production gets: the SDK builds a
``fallback`` content block as a text-typed object with a ``text`` attribute
that is ``None``, keeps ``stop_details`` as an untyped extra, and a refusal or
a truncation can arrive after partial output. Expected texts, tool inputs,
model names and categories are derived from the fixture, never typed.
"""

from __future__ import annotations

import json
import types
from unittest.mock import MagicMock

import pytest

import llm_request
from advisors.asset_swap_engine import _EMIT_SWAP_CANDIDATES_TOOL
from advisors.build_plan_generator import _EMIT_BUILD_PLANS_TOOL
from advisors.frontrunner_builder import _EMIT_OVERLAY_TOOL
from advisors.logic_change_engine import _EMIT_LOGIC_EDITS_TOOL
from model_config import Knob
from tests.llm_request.conftest import USER_MESSAGES, message_body, newest_id

TOOLS = [_EMIT_SWAP_CANDIDATES_TOOL]
REFUSALS = [
    "refusal_reasoning_extraction.json",
    "refusal_null_category.json",
    "refusal_after_partial_text.json",
    "refusal_after_partial_tool.json",
]
TRUNCATIONS = ["max_tokens.json", "max_tokens_in_thinking.json"]
NO_TOOL_REPLIES = ["text_only_with_tools.json", "text_instead_of_tool_emit_logic_edits.json"]
TOOL_REPLIES = [
    ("thinking_tool_emit_swap_candidates.json", _EMIT_SWAP_CANDIDATES_TOOL),
    ("thinking_tool_emit_logic_edits.json", _EMIT_LOGIC_EDITS_TOOL),
    ("thinking_tool_emit_build_plans.json", _EMIT_BUILD_PLANS_TOOL),
    ("thinking_tool_emit_frontrunner_overlay.json", _EMIT_OVERLAY_TOOL),
]


def reply_to(fake, name, *, tools=None, knob=Knob.SYNTHESIS):
    fake.queue_message(message_body(name))
    reply = llm_request.send(
        fake.client,
        knob,
        effort="high",
        max_tokens=1000,
        messages=list(USER_MESSAGES),
        tools=tools,
    )
    assert fake.misuse == []
    return reply


def text_blocks(body):
    return [b["text"] for b in body["content"] if b["type"] == "text"]


def tool_blocks(body):
    return [b for b in body["content"] if b["type"] == "tool_use"]


class TestSuccessfulReplies:
    def test_text_is_read_from_the_text_block_after_a_thinking_block(self, fake):
        body = message_body("thinking_text.json")

        reply = reply_to(fake, "thinking_text.json")

        assert reply.reason is None
        assert reply.text() == "".join(text_blocks(body))
        assert reply.text() != ""
        assert reply.tool_input() is None

    def test_a_structured_reply_text_is_the_json_document_after_thinking(self, fake):
        body = message_body("thinking_structured.json")

        reply = reply_to(fake, "thinking_structured.json", knob=Knob.SUGGESTION)

        assert reply.reason is None
        assert json.loads(reply.text()) == json.loads(text_blocks(body)[0])

    @pytest.mark.parametrize(("name", "tool"), TOOL_REPLIES, ids=[n for n, _ in TOOL_REPLIES])
    def test_a_tool_call_input_is_read_whether_or_not_thinking_came_first(self, fake, name, tool):
        body = message_body(name)

        reply = reply_to(fake, name, tools=[tool], knob=Knob.SUGGESTION)

        assert reply.reason is None
        assert reply.tool_input() == tool_blocks(body)[0]["input"]
        assert reply.text() == ""

    def test_without_tools_a_text_only_reply_is_a_success(self, fake):
        body = message_body("text_only_with_tools.json")

        reply = reply_to(fake, "text_only_with_tools.json")

        assert reply.reason is None
        assert reply.text() == "".join(text_blocks(body))

    def test_the_reply_model_is_the_top_level_model_of_the_response(self, fake):
        body = message_body("thinking_text.json")

        reply = reply_to(fake, "thinking_text.json")

        assert reply.model == body["model"]


class TestFallbackServedReplies:
    def test_text_comes_from_the_text_block_only_never_the_fallback_block(self, fake):
        body = message_body("fallback_served_text.json")

        reply = reply_to(fake, "fallback_served_text.json")

        assert reply.reason is None
        assert reply.text() == "".join(text_blocks(body))
        assert "None" not in reply.text()
        assert reply.text() != ""

    def test_the_tool_input_comes_from_the_tool_block_after_the_fallback_block(self, fake):
        body = message_body("fallback_served_tool.json")

        reply = reply_to(fake, "fallback_served_tool.json", tools=TOOLS, knob=Knob.SUGGESTION)

        assert reply.reason is None
        assert reply.tool_input() == tool_blocks(body)[0]["input"]
        assert reply.text() == ""

    @pytest.mark.parametrize(
        ("name", "knob", "line"),
        [
            ("fallback_served_text.json", Knob.SYNTHESIS, "opus"),
            ("fallback_served_tool.json", Knob.SUGGESTION, "fable"),
        ],
        ids=["text", "tool"],
    )
    def test_the_reply_names_the_model_that_answered_not_the_model_requested(
        self, fake, name, knob, line
    ):
        body = message_body(name)
        requested = newest_id(line)
        assert body["model"] != requested

        reply = reply_to(fake, name, tools=TOOLS if knob is Knob.SUGGESTION else None, knob=knob)

        assert fake.message_requests[0].body["model"] == requested
        assert reply.model == body["model"]


class TestRefusals:
    @pytest.mark.parametrize("name", REFUSALS)
    @pytest.mark.parametrize("with_tools", [False, True], ids=["no-tools", "tools"])
    def test_a_refusal_is_reported_and_every_partial_output_is_discarded(
        self, fake, name, with_tools
    ):
        reply = reply_to(fake, name, tools=TOOLS if with_tools else None)

        assert reply.reason == "refusal"
        assert reply.text() == ""
        assert reply.tool_input() is None
        assert len(fake.message_requests) == 1

    @pytest.mark.parametrize("name", REFUSALS)
    def test_the_reason_text_carries_the_category_unless_it_is_null(self, fake, name):
        category = message_body(name)["stop_details"]["category"]

        reply = reply_to(fake, name)

        expected = "refusal" if category is None else f"refusal: {category}"
        assert reply.reason_text() == expected

    def test_a_refusal_carries_no_category_text_when_the_category_is_null(self, fake):
        reply = reply_to(fake, "refusal_null_category.json")

        assert reply.reason_text() == "refusal"
        assert "None" not in reply.reason_text()
        assert "null" not in reply.reason_text()


class TestTruncations:
    @pytest.mark.parametrize("name", TRUNCATIONS)
    @pytest.mark.parametrize("with_tools", [False, True], ids=["no-tools", "tools"])
    def test_a_truncated_reply_is_reported_and_its_partial_content_discarded(
        self, fake, name, with_tools
    ):
        reply = reply_to(fake, name, tools=TOOLS if with_tools else None)

        assert reply.reason == "max_tokens"
        assert reply.reason_text() == "max_tokens"
        assert reply.text() == ""
        assert reply.tool_input() is None
        assert len(fake.message_requests) == 1


class TestMissingToolCall:
    @pytest.mark.parametrize("name", NO_TOOL_REPLIES)
    def test_tools_offered_and_no_tool_block_is_a_missing_tool_call(self, fake, name):
        reply = reply_to(fake, name, tools=[_EMIT_LOGIC_EDITS_TOOL], knob=Knob.SUGGESTION)

        assert reply.reason == "no_tool_call"
        assert reply.reason_text() == "no_tool_call"
        assert reply.tool_input() is None

    @pytest.mark.parametrize("name", NO_TOOL_REPLIES)
    def test_the_same_reply_without_tools_is_not_a_missing_tool_call(self, fake, name):
        reply = reply_to(fake, name, knob=Knob.SUGGESTION)

        assert reply.reason is None


class TestReplyModelFallback:
    @pytest.mark.parametrize(
        "model_attribute",
        [None, 7, "", MagicMock()],
        ids=["none", "int", "empty", "mock"],
    )
    def test_a_reply_whose_model_is_not_a_non_empty_string_is_attributed_to_the_requested_model(
        self, model_attribute
    ):
        requested = newest_id("opus")
        message = types.SimpleNamespace(
            content=[types.SimpleNamespace(type="text", text="an answer")],
            stop_reason="end_turn",
            stop_details=None,
            model=model_attribute,
        )
        client = types.SimpleNamespace(
            messages=types.SimpleNamespace(create=lambda **_: message), models=MagicMock()
        )

        reply = llm_request.send(
            client, Knob.SYNTHESIS, effort="high", max_tokens=100, messages=list(USER_MESSAGES)
        )

        assert reply.reason is None
        assert reply.model == requested
        assert reply.text() == "an answer"


class TestReplyConstructedWithAReason:
    def test_an_unavailable_reply_has_no_output_and_renders_its_bare_code(self):
        reply = llm_request.Reply(reason="model_unavailable")

        assert reply.reason == "model_unavailable"
        assert reply.reason_text() == "model_unavailable"
        assert reply.text() == ""
        assert reply.tool_input() is None
        assert reply.model == ""
